"""interaction-ai-chat 0.4.7：模型调用失败时的「🔄 重试」「🔀 换模型重试」按钮与回复“重试”重放。

根因回归：0.4.6 及以前失败的那一轮不写入短期记忆，群里回复 Bot 消息时还会清空引用内容，
用户对失败消息回复“重试”时模型只看到“重试”二字，不知道要重试什么。
"""

from __future__ import annotations

import asyncio
import json
import types
import unittest

import test_interaction_ai_chat as base

PLUGIN = base.PLUGIN
RTY = __import__(f"{base.PACKAGE.__name__}.retry", fromlist=["retry"])
run = base.run

CHAT = -100777
ASKER = 601
OTHER = 602
ADMIN = 700
QUESTION = "北京今天天气怎么样"


class ScriptedAI:
    """按顺序返回结果；Exception 则抛出。可选延迟（并发锁用例）。"""

    def __init__(self, *results, delay: float = 0.0, providers=None) -> None:
        self.results = list(results)
        self.calls: list[dict] = []
        self.delay = delay
        self.providers = providers

    async def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.results.pop(0) if self.results else "默认回答"
        if isinstance(item, Exception):
            raise item
        return types.SimpleNamespace(text=item)

    async def list_providers(self):
        return list(self.providers or [])


class Identities:
    def __init__(self, admins=()) -> None:
        self.admins = set(admins)
        self.calls: list[tuple] = []

    async def resolve(self, *, chat_id, user_id, **_kwargs):
        self.calls.append((chat_id, user_id))
        return types.SimpleNamespace(user_id=user_id, is_admin=user_id in self.admins, resolved=True)


def provider(pid, name, default, models):
    return types.SimpleNamespace(id=pid, name=name, default_model=default, modality="text", has_api_key=True, models=models)


def make_ctx(ai, **config):
    ctx = base.Ctx({"bot_username": "helper_bot", "rich_text": False, **config}, ai=ai)
    ctx.messages = base.LiveMessages()
    return ctx


def ask(text=QUESTION, *, user_id=ASKER, reply_text=None, reply_message_id=None, chat_id=CHAT, chat_type="supergroup", mention=True, **extra):
    payload = base.message_payload(f"@helper_bot {text}" if mention else text, chat_id=chat_id, chat_type=chat_type, user_id=user_id)
    if reply_text is not None or reply_message_id is not None:
        payload["reply_to"] = {"message_id": reply_message_id or 50, "username": "someone", "text": reply_text}
    payload.update(extra)
    return payload


def reply_to_bot(text, message_id, *, user_id=ASKER, **extra):
    payload = base.message_payload(text, chat_id=CHAT, user_id=user_id)
    payload["reply_to"] = {"message_id": message_id, "username": "helper_bot", "text": "AI 暂时不可用"}
    payload.update(extra)
    return payload


def callback(data, message_id, *, user_id=ASKER, chat_id=CHAT, cq="cq-1", **extra):
    payload = {
        "source": {
            "type": "callback_query",
            "channel": "interaction_bot",
            "callback_query_id": cq,
            "callback_data": data,
            "chat_id": chat_id,
            "message_id": message_id,
        },
        "event_type": "callback_query",
        "message": {"chat_id": chat_id, "message_id": message_id, "text": "AI 暂时不可用"},
        "chat": {"id": chat_id, "type": "supergroup"},
        "sender": {"user_id": user_id},
    }
    payload.update(extra)
    return payload


def markup_of(actions):
    for action in actions:
        if action.get("reply_markup"):
            return action["reply_markup"]
    return None


def buttons(markup):
    return [button for row in (markup or {}).get("inline_keyboard", []) for button in row]


def answers(ctx):
    return [action for action in ctx.messages.applied if action.get("type") == "answer_callback"]


def memory(ctx):
    for key, value in ctx.storage.data.items():
        if key.startswith("mem:"):
            return value["turns"]
    return []


def fail_first(ctx, plugin, payload=None):
    """首次提问失败，返回 (动作, 失败消息 id, token)。"""

    actions = run(plugin.on_event(ctx, payload or ask(reply_text="被引用的上下文")))
    markup = markup_of(actions)
    token = buttons(markup)[0]["callback_data"].split(":")[2] if markup else None
    edit = next((a for a in actions if a.get("type") == "edit_message"), {})
    return actions, edit.get("message_id"), token


class CallbackFormatTest(unittest.TestCase):
    def test_callback_data_roundtrip_and_length(self) -> None:
        token = RTY.new_token()
        for data in (
            RTY.callback_data("r", token),
            RTY.callback_data("m", token),
            RTY.callback_data("p", token, 12),
            RTY.callback_data("s", token, 47),
            RTY.callback_data("c", token),
        ):
            self.assertLessEqual(len(data.encode()), 64)
            self.assertIsNotNone(RTY.parse_callback(data))
        self.assertIsNone(RTY.parse_callback("other:r:abc"))
        self.assertIsNone(RTY.parse_callback(f"iac:s:{token}"))
        self.assertIsNone(RTY.parse_callback("iac:r:../../x"))

    def test_retry_words(self) -> None:
        for word in ("重试", " 再试一次！", "Retry", "重新回答。", "try again"):
            self.assertTrue(RTY.is_retry_text(word), word)
        for word in ("重试一下这个问题：北京天气", "怎么重试", "", "天气"):
            self.assertFalse(RTY.is_retry_text(word), word)


class FailureButtonsTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_call_failure_attaches_two_buttons_and_saves_full_request(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 502 bad gateway"))
        ctx = make_ctx(ai, telepilot_provider="3", telepilot_model="glm-x")
        plugin = PLUGIN.InteractionAIChatPlugin()
        actions, message_id, token = fail_first(ctx, plugin)

        markup = markup_of(actions)
        texts = [button["text"] for button in buttons(markup)]
        self.assertEqual(texts, ["🔄 重试", "🔀 换模型重试"])
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in buttons(markup)))
        self.assertEqual(message_id, 901)  # 编辑的是 🤔 占位
        record = ctx.storage.data[f"retry:{token}"]
        self.assertEqual(record["text"], QUESTION)
        self.assertEqual(record["reply_text"], "被引用的上下文")
        self.assertEqual(record["user_id"], ASKER)
        self.assertEqual(record["chat_id"], CHAT)
        self.assertEqual(record["message_id"], 88)
        self.assertEqual((record["provider"], record["model"]), ("3", "glm-x"))
        self.assertEqual(record["failure_message_id"], 901)
        self.assertEqual(record["history"], [])
        self.assertEqual(ctx.storage.ttls[f"retry:{token}"], 30 * 60)

    def test_failed_turn_is_written_to_memory_with_failure_mark(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "第二个问题的回答")
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        fail_first(ctx, plugin)
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", PLUGIN.FAILED_TURN_MARK]])
        # 根因回归：之后的提问能看到失败的那一问。
        run(plugin.on_event(ctx, ask("那明天呢")))
        self.assertIn(QUESTION, ai.calls[1]["user"])

    def test_timeout_empty_and_no_answer_are_retryable(self) -> None:
        for error in (asyncio.TimeoutError(), type("AIResponseTruncated", (RuntimeError,), {})("no final")):
            ai = ScriptedAI(error, error)
            ctx = make_ctx(ai)
            actions, _, token = fail_first(ctx, PLUGIN.InteractionAIChatPlugin())
            self.assertIsNotNone(markup_of(actions), type(error).__name__)
        ai = ScriptedAI("")
        ctx = make_ctx(ai)
        actions, _, _ = fail_first(ctx, PLUGIN.InteractionAIChatPlugin())
        self.assertIsNotNone(markup_of(actions))

    def test_moderation_rejection_uses_own_template_and_keeps_buttons(self) -> None:
        # 0.4.8：审核拒答用独立模板（不再与“模型调用失败”共用），仍附按钮（换模型往往能过）。
        ai = ScriptedAI(RuntimeError("Error code: 400 data_inspection_failed: Input data may contain inappropriate content"))
        ctx = make_ctx(ai, error_ai_template="模型调用失败。请联系管理员。")
        actions, _, token = fail_first(ctx, PLUGIN.InteractionAIChatPlugin())
        edit = next(a for a in actions if a.get("type") == "edit_message")
        self.assertEqual(edit["text"], "内容未通过模型审核，换个问法试试。")
        self.assertEqual([b["text"] for b in buttons(markup_of(actions))], ["🔄 重试", "🔀 换模型重试"])
        self.assertEqual(ctx.storage.data[f"retry:{token}"]["error_key"], "error_moderation_template")

    def test_limit_and_guard_have_no_buttons(self) -> None:
        ctx = make_ctx(ScriptedAI("x"), per_user_daily_limit=1)
        plugin = PLUGIN.InteractionAIChatPlugin()
        run(plugin.on_event(ctx, ask()))
        actions = run(plugin.on_event(ctx, ask()))
        self.assertIsNone(markup_of(actions))
        ctx = make_ctx(ScriptedAI(",re"))
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, ask()))
        self.assertIsNone(markup_of(actions))

    def test_switch_off_hides_buttons_but_text_retry_still_works(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "成功回答")
        ctx = make_ctx(ai, retry_buttons_enabled=False)
        plugin = PLUGIN.InteractionAIChatPlugin()
        actions, message_id, _ = fail_first(ctx, plugin)
        self.assertIsNone(markup_of(actions))
        actions = run(plugin.on_event(ctx, reply_to_bot("重试", message_id)))
        self.assertEqual(len(ai.calls), 2)
        self.assertIn(QUESTION, ai.calls[1]["user"])
        self.assertTrue(any("成功回答" in str(a.get("text")) for a in actions))

    def test_guest_failure_has_no_buttons(self) -> None:
        ctx = make_ctx(ScriptedAI(RuntimeError("upstream 500")))
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("你好")))
        self.assertIsNone(markup_of(actions))
        self.assertFalse(any(a.get("reply_markup") for a in ctx.messages.applied))

    def test_no_placeholder_sends_failure_live_with_buttons(self) -> None:
        ctx = make_ctx(ScriptedAI(RuntimeError("upstream 500")), thinking_placeholder=False)
        plugin = PLUGIN.InteractionAIChatPlugin()
        actions = run(plugin.on_event(ctx, ask()))
        self.assertEqual(actions, [])
        sent = [a for a in ctx.messages.applied if a.get("type") == "send_message"]
        self.assertEqual(len(sent), 1)
        self.assertEqual([b["text"] for b in buttons(sent[0]["reply_markup"])], ["🔄 重试", "🔀 换模型重试"])
        token = buttons(sent[0]["reply_markup"])[0]["callback_data"].split(":")[2]
        self.assertEqual(ctx.storage.data[f"retry:{token}"]["failure_message_id"], 901)

    def test_foreign_callback_is_ignored(self) -> None:
        ctx = make_ctx(ScriptedAI())
        self.assertEqual(run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, callback("guess:status", 1))), [])
        self.assertEqual(ctx.messages.applied, [])


class ButtonRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_requester_retry_replays_original_request_in_place(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "晴，25 度")
        ctx = make_ctx(ai, telepilot_provider="3", telepilot_model="glm-x")
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)

        actions = run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id)))

        self.assertEqual(len(ai.calls), 2)
        # 原请求重放：同样的提示词（原问题 + 引用 + 历史快照），原模型；没有把“重试”当问题。
        self.assertEqual(ai.calls[1]["user"], ai.calls[0]["user"])
        self.assertIn(QUESTION, ai.calls[1]["user"])
        self.assertIn("被引用的上下文", ai.calls[1]["user"])
        self.assertEqual((ai.calls[1]["provider"], ai.calls[1]["model"]), ("3", "glm-x"))
        self.assertEqual(answers(ctx)[0]["text"], "开始重试")
        running = [a for a in ctx.messages.applied if a.get("type") == "edit_message" and "正在重试" in a.get("text", "")]
        self.assertEqual(running[0]["message_id"], message_id)
        final = [a for a in actions if a.get("type") == "edit_message"]
        self.assertEqual(final[0]["message_id"], message_id)
        self.assertIn("晴，25 度", final[0]["text"])
        self.assertNotIn("reply_markup", final[0])
        # 记忆：问题只有一份，失败标记被替换为成功回答。
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", "晴，25 度"]])
        self.assertNotIn(f"retry:{token}", ctx.storage.data)

    def test_retry_uses_history_snapshot_and_keeps_memory_unique(self) -> None:
        ai = ScriptedAI("第一问回答", RuntimeError("upstream 500"), "第二问回答")
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        run(plugin.on_event(ctx, ask("第一问")))
        _, message_id, token = fail_first(ctx, plugin, ask("第二问"))
        run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id)))
        self.assertEqual(ai.calls[2]["user"], ai.calls[1]["user"])
        self.assertEqual(
            memory(ctx),
            [["u", "第一问"], ["a", "第一问回答"], ["u", "第二问"], ["a", "第二问回答"]],
        )

    def test_other_user_cannot_retry(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"))
        ctx = make_ctx(ai)
        ctx.identities = Identities()
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id, user_id=OTHER)))
        self.assertEqual(actions, [])
        self.assertEqual(len(ai.calls), 1)
        self.assertEqual(answers(ctx)[-1]["text"], "只有提问者或管理员可以重试。")
        self.assertTrue(answers(ctx)[-1]["show_alert"])

    def test_admins_can_retry(self) -> None:
        cases = [
            ("group_admin", {}, {"identities": Identities(admins={ADMIN})}, {}),
            ("telepilot_admin", {}, {}, {"admin_user_ids": [ADMIN]}),
            ("config_admin", {"retry_admin_user_ids": str(ADMIN)}, {}, {}),
        ]
        for name, config, attrs, extra in cases:
            ai = ScriptedAI(RuntimeError("upstream 500"), "管理员重试成功")
            ctx = make_ctx(ai, **config)
            for key, value in attrs.items():
                setattr(ctx, key, value)
            plugin = PLUGIN.InteractionAIChatPlugin()
            _, message_id, token = fail_first(ctx, plugin)
            actions = run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id, user_id=ADMIN, **extra)))
            self.assertEqual(len(ai.calls), 2, name)
            self.assertTrue(any("管理员重试成功" in str(a.get("text")) for a in actions), name)

    def test_retry_failure_shows_buttons_again(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), RuntimeError("upstream 503"), "第三次成功")
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id)))
        markup = markup_of(actions)
        self.assertEqual([b["callback_data"] for b in buttons(markup)], [f"iac:r:{token}", f"iac:m:{token}"])
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", PLUGIN.FAILED_TURN_MARK]])
        run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id, cq="cq-2")))
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", "第三次成功"]])

    def test_expired_token(self) -> None:
        ctx = make_ctx(ScriptedAI(RuntimeError("upstream 500")))
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        ctx.storage.data.pop(f"retry:{token}")  # TTL 到期
        actions = run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id)))
        self.assertEqual(actions, [])
        self.assertEqual(answers(ctx)[-1]["text"], "这条重试已过期，请重新提问。")
        run(plugin.on_event(ctx, callback("iac:r:0123456789ab", message_id)))
        self.assertEqual(answers(ctx)[-1]["text"], "这条重试已过期，请重新提问。")

    def test_concurrent_clicks_run_only_one_retry(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "慢回答", "不该出现", delay=0.0)
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        ai.delay = 0.2

        async def both():
            return await asyncio.gather(
                plugin.on_event(ctx, callback(f"iac:r:{token}", message_id, cq="cq-a")),
                plugin.on_event(ctx, callback(f"iac:r:{token}", message_id, cq="cq-b")),
            )

        first, second = run(both())
        self.assertEqual(len(ai.calls), 2)
        texts = [a.get("text") for a in answers(ctx)]
        self.assertIn("正在重试，请稍候…", texts)
        self.assertIn("开始重试", texts)
        self.assertNotIn(f"retry:lock:{token}", ctx.storage.data)


class SwitchModelTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    PROVIDERS = [
        provider(
            3,
            "智谱",
            "glm-x",
            [
                {"id": "glm-x", "label": "GLM X", "enabled": True},
                {"id": "glm-y", "label": "GLM Y", "enabled": True},
                {"id": "glm-off", "label": "GLM Off", "enabled": False},
            ],
        ),
        provider(5, "OpenAI", "gpt-a", [{"id": f"gpt-{i}", "enabled": True} for i in range(7)]),
    ]

    def _failed(self, **config):
        ai = ScriptedAI(RuntimeError("upstream 500"), "换模型后成功", providers=self.PROVIDERS)
        ctx = make_ctx(ai, telepilot_provider="3", telepilot_model="glm-x", **config)
        ctx.identities = Identities(admins={ADMIN})
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        return ai, ctx, plugin, message_id, token

    def test_requester_who_is_not_admin_cannot_switch(self) -> None:
        ai, ctx, plugin, message_id, token = self._failed()
        actions = run(plugin.on_event(ctx, callback(f"iac:m:{token}", message_id, user_id=ASKER)))
        self.assertEqual(actions, [])
        self.assertEqual(answers(ctx)[-1]["text"], "只有管理员可以换模型重试。")

    def test_menu_lists_enabled_models_except_failed_with_pages_and_cancel(self) -> None:
        ai, ctx, plugin, message_id, token = self._failed()
        actions = run(plugin.on_event(ctx, callback(f"iac:m:{token}", message_id, user_id=ADMIN)))
        markup = markup_of(actions)
        labels = [b["text"] for b in buttons(markup)]
        self.assertNotIn("智谱 · GLM X", labels)  # 刚失败的模型
        self.assertNotIn("智谱 · GLM Off", labels)  # 未启用
        self.assertEqual(labels[0], "智谱 · GLM Y")
        self.assertEqual(len([b for b in buttons(markup) if b["callback_data"].startswith("iac:s:")]), 6)
        self.assertIn("下一页 ▶", labels)
        self.assertEqual(labels[-1], "✖ 取消")
        self.assertIn("选择本次重试使用的模型", actions[-1]["text"])
        # 翻页
        nxt = next(b for b in buttons(markup) if b["text"] == "下一页 ▶")
        actions = run(plugin.on_event(ctx, callback(nxt["callback_data"], message_id, user_id=ADMIN)))
        labels = [b["text"] for b in buttons(markup_of(actions))]
        self.assertIn("◀ 上一页", labels)
        self.assertEqual(len([b for b in buttons(markup_of(actions)) if b["callback_data"].startswith("iac:s:")]), 2)
        # 取消回到两个按钮
        actions = run(plugin.on_event(ctx, callback(f"iac:c:{token}", message_id, user_id=ADMIN)))
        self.assertEqual([b["text"] for b in buttons(markup_of(actions))], ["🔄 重试", "🔀 换模型重试"])
        self.assertEqual(len(ai.calls), 1)

    def test_select_model_retries_once_without_changing_config(self) -> None:
        ai, ctx, plugin, message_id, token = self._failed()
        actions = run(plugin.on_event(ctx, callback(f"iac:m:{token}", message_id, user_id=ADMIN)))
        first = next(b for b in buttons(markup_of(actions)) if b["callback_data"].startswith("iac:s:"))
        actions = run(plugin.on_event(ctx, callback(first["callback_data"], message_id, user_id=ADMIN)))
        self.assertEqual((ai.calls[1]["provider"], ai.calls[1]["model"]), ("3", "glm-y"))
        self.assertEqual(ai.calls[1]["user"], ai.calls[0]["user"])
        self.assertTrue(any("换模型后成功" in str(a.get("text")) for a in actions))
        running = [a for a in ctx.messages.applied if "正在用" in str(a.get("text"))]
        self.assertIn("GLM Y", running[0]["text"])
        self.assertEqual(ctx.config["telepilot_model"], "glm-x")
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", "换模型后成功"]])

    def test_switch_failure_then_plain_retry_uses_original_model(self) -> None:
        ai = ScriptedAI(RuntimeError("e1"), RuntimeError("e2"), "ok", providers=self.PROVIDERS)
        ctx = make_ctx(ai, telepilot_provider="3", telepilot_model="glm-x", retry_admin_user_ids=str(ADMIN))
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, callback(f"iac:m:{token}", message_id, user_id=ADMIN)))
        first = next(b for b in buttons(markup_of(actions)) if b["callback_data"].startswith("iac:s:"))
        actions = run(plugin.on_event(ctx, callback(first["callback_data"], message_id, user_id=ADMIN)))
        self.assertIsNotNone(markup_of(actions))
        run(plugin.on_event(ctx, callback(f"iac:r:{token}", message_id, user_id=ASKER)))
        self.assertEqual(ai.calls[2]["model"], "glm-x")

    def test_legacy_host_without_enabled_flag_lists_default_models(self) -> None:
        legacy = [
            provider(3, "智谱", "glm-x", [{"id": "glm-x"}, {"id": "glm-y"}]),
            provider(5, "OpenAI", "gpt-a", [{"id": "gpt-a", "label": "GPT A"}, {"id": "gpt-b"}]),
        ]
        models, exact = RTY.model_candidates(legacy, exclude_provider="3", exclude_model="glm-x")
        self.assertFalse(exact)
        self.assertEqual([(m["p"], m["m"]) for m in models], [("5", "gpt-a")])

    def test_no_candidates(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), providers=[provider(3, "智谱", "glm-x", [{"id": "glm-x", "enabled": True}])])
        ctx = make_ctx(ai, telepilot_provider="3", telepilot_model="glm-x", retry_admin_user_ids=str(ADMIN))
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        run(plugin.on_event(ctx, callback(f"iac:m:{token}", message_id, user_id=ADMIN)))
        self.assertEqual(answers(ctx)[-1]["text"], "没有其它可用的模型可以切换。")


class TextRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_reply_retry_to_failure_message_replays_original_question(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "晴天")
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, reply_to_bot("重试", message_id)))
        self.assertEqual(len(ai.calls), 2)
        self.assertEqual(ai.calls[1]["user"], ai.calls[0]["user"])
        self.assertNotIn("【用户现在的问题】\n重试", ai.calls[1]["user"])
        self.assertTrue(any("晴天" in str(a.get("text")) for a in actions))
        # 旧失败消息去掉按钮
        old = [a for a in actions if a.get("message_id") == message_id]
        self.assertTrue(old and "reply_markup" not in old[-1] and "已重新回答" in old[-1]["text"])
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", "晴天"]])
        self.assertNotIn(f"retry:{token}", ctx.storage.data)

    def test_private_chat_retry_without_reply_uses_last_failure(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "私聊成功")
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        run(plugin.on_event(ctx, ask("私聊问题", chat_id=ASKER, chat_type="private", mention=False)))
        actions = run(plugin.on_event(ctx, ask("再试一次", chat_id=ASKER, chat_type="private", mention=False)))
        self.assertIn("私聊问题", ai.calls[1]["user"])
        self.assertTrue(any("私聊成功" in str(a.get("text")) for a in actions))
        self.assertEqual(memory(ctx), [["u", "私聊问题"], ["a", "私聊成功"]])

    def test_retry_without_failure_gives_hint_and_no_ai_call(self) -> None:
        ai = ScriptedAI("不该调用")
        ctx = make_ctx(ai)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, reply_to_bot("重试", 12345)))
        self.assertEqual(ai.calls, [])
        self.assertIn("没有找到可以重试的失败提问", json.dumps(actions, ensure_ascii=False))

    def test_other_user_reply_retry_denied_admin_allowed(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "管理员回复重试成功")
        ctx = make_ctx(ai)
        ctx.identities = Identities(admins={ADMIN})
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, _ = fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, reply_to_bot("重试", message_id, user_id=OTHER)))
        self.assertEqual(len(ai.calls), 1)
        self.assertIn("只有提问者或管理员可以重试", json.dumps(actions, ensure_ascii=False))
        actions = run(plugin.on_event(ctx, reply_to_bot("重试", message_id, user_id=ADMIN)))
        self.assertEqual(len(ai.calls), 2)
        self.assertIn(QUESTION, ai.calls[1]["user"])
        self.assertTrue(any("管理员回复重试成功" in str(a.get("text")) for a in actions))
        # 记忆写回提问者自己的会话记忆
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", "管理员回复重试成功"]])

    def test_guest_text_retry_replays_last_failure(self) -> None:
        ai = ScriptedAI(RuntimeError("upstream 500"), "访客重试成功")
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        run(plugin.on_event(ctx, base.guest_payload("访客的问题", query_id="g1")))
        actions = run(plugin.on_event(ctx, base.guest_payload("重试", query_id="g2")))
        self.assertIn("访客的问题", ai.calls[1]["user"])
        self.assertEqual(ai.calls[1]["user"], ai.calls[0]["user"])
        applied = json.dumps(ctx.messages.applied + actions, ensure_ascii=False)
        self.assertIn("访客重试成功", applied)

    def test_text_retry_failure_moves_buttons_to_new_message(self) -> None:
        ai = ScriptedAI(RuntimeError("e1"), RuntimeError("e2"))
        ctx = make_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, message_id, token = fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, reply_to_bot("重试", message_id)))
        markup = markup_of(actions)
        self.assertEqual(buttons(markup)[0]["callback_data"], f"iac:r:{token}")
        record = ctx.storage.data[f"retry:{token}"]
        self.assertNotEqual(record["failure_message_id"], message_id)
        self.assertEqual(memory(ctx), [["u", QUESTION], ["a", PLUGIN.FAILED_TURN_MARK]])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
