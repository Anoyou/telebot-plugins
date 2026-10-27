"""interaction-ai-chat 0.4.8：

1. 根因回归——“非访客群 + 模型不可用”失败消息必须带「🔄 重试」「🔀 换模型重试」：
   连接失败、404 模型不存在、401 鉴权失败、模型未启用、平台回退到其它 Provider 后被审核拒答、
   答疑流程里 AI 调用之外的意外异常（0.4.7 走 on_event 通用兜底，回复 error_ai_template 却没有重试记录）、
   失败轮次写记忆失败、实时发送失败提示被拒/异常。
2. 审核拒答使用独立模板 error_moderation_template。
3. 访客模式（TelePilot 0.106.0-beta.17+，ctx.messages.guest_reply_markup）失败回复也挂按钮：
   token 记录保存 inline_message_id，按钮回调（只有 inline_message_id + 点击者）按 inline_message_id 原地编辑；
   权限：「重试」= 提问者本人或管理员；「换模型重试」= 仅管理员（提问者本人非管理员也不行），第三人两个都不行。
"""

from __future__ import annotations

import unittest

import test_interaction_ai_chat as base
import test_interaction_ai_chat_retry as R

PLUGIN = base.PLUGIN
run = base.run
ASKER = R.ASKER
OTHER = R.OTHER
ADMIN = R.ADMIN
GUEST_CHAT = -100555
CUSTOM_ERROR = "模型调用失败。请联系管理员。"


class GuestLive(base.LiveMessages):
    """TelePilot 0.106.0-beta.17+ 的交互入口 ctx.messages：访客回复支持 callback 按钮。"""

    guest_reply_markup = True
    live_inline_edit = True

    async def apply(self, actions, *, entry_key=None):
        outcome = await super().apply(actions, entry_key=entry_key)
        if any(a.get("type") == "answer_guest_query" for a in actions):
            outcome = {**outcome, "inline_message_id": "inline-abc"}
        return outcome


class FacadeError(RuntimeError):
    """模拟平台 ctx.ai 的 AIUnavailableError（类名不同，消息与平台一致）。"""


FacadeError.__name__ = "AIUnavailableError"


def group_ctx(ai, **config):
    ctx = R.make_ctx(ai, error_ai_template=CUSTOM_ERROR, telepilot_provider="9", telepilot_model="bad-model", **config)
    return ctx


def failure_edit(actions):
    return next((a for a in actions if a.get("type") == "edit_message"), None)


def guest_ctx(ai, **config):
    ctx = base.Ctx({"bot_username": "helper_bot", "rich_text": False, "error_ai_template": CUSTOM_ERROR, **config}, ai=ai)
    ctx.messages = GuestLive()
    return ctx


def guest_ask(text="访客的问题", *, user_id=ASKER, query_id="g1"):
    return base.guest_payload(text, user_id=user_id, chat_id=GUEST_CHAT, query_id=query_id)


def inline_callback(data, *, user_id=ASKER, inline_id="inline-abc", cq="cq-g", **extra):
    """平台 0.106.0-beta.17 投递的 inline callback：没有 chat/message，只有 inline_message_id 与点击者。"""

    payload = {
        "source": {
            "type": "callback_query",
            "channel": "interaction_bot",
            "callback_query_id": cq,
            "callback_data": data,
            "chat_id": None,
            "message_id": None,
            "inline_message_id": inline_id,
        },
        "event_type": "callback_query",
        "message": {"chat_id": None, "message_id": None, "text": ""},
        "chat": {"id": None, "type": None},
        "sender": {"user_id": user_id},
        "chat_id": None,
        "inline_message_id": inline_id,
        "callback_data": data,
        "callback_query_id": cq,
        "inline_callback": {
            "inline_message_id": inline_id,
            "chat_instance": "ci-1",
            "data": data,
            "clicker_user_id": user_id,
            "origin": {"guest_query_id": "g1", "chat_id": GUEST_CHAT, "chat_type": "supergroup", "caller_user_id": ASKER},
        },
        "owner_user_ids": [],
        "admin_user_ids": [],
    }
    payload.update(extra)
    return payload


def guest_fail_first(ctx, plugin, payload=None):
    actions = run(plugin.on_event(ctx, payload or guest_ask()))
    markup = R.markup_of(actions) or R.markup_of(ctx.messages.applied)
    token = R.buttons(markup)[0]["callback_data"].split(":")[2] if markup else None
    return actions, token


class ModelUnavailableButtonsTest(unittest.TestCase):
    """用户实测场景：测试群（Bot 是普通成员）里固定一个不通的模型，失败消息必须带两个按钮。"""

    def setUp(self) -> None:
        base._install_stubs()

    def assert_buttons(self, actions, ctx, text=CUSTOM_ERROR):
        edit = failure_edit(actions)
        self.assertIsNotNone(edit, actions)
        self.assertEqual(edit["text"], text)
        self.assertTrue(edit.get("send_on_edit_failure"))
        self.assertEqual([b["text"] for b in R.buttons(edit.get("reply_markup"))], ["🔄 重试", "🔀 换模型重试"])
        token = R.buttons(edit["reply_markup"])[0]["callback_data"].split(":")[2]
        record = ctx.storage.data[f"retry:{token}"]
        self.assertEqual(record["failure_message_id"], edit["message_id"])
        return token

    def test_unreachable_model_errors_all_have_buttons(self) -> None:
        errors = [
            FacadeError("Provider「坏模型」内模型均失败。最后错误: LLMError: Connection error: [Errno 111] Connection refused"),
            FacadeError(
                "OpenAI 接口返回 404: {\"error\":{\"message\":\"The model `bad-model` does not exist or you do not have access to it.\","
                "\"code\":\"model_not_found\"}}  ↳ [model_missing] 模型不存在或当前 Key 无权限；核对模型 ID。"
            ),
            FacadeError("OpenAI 接口返回 401: Incorrect API key provided  ↳ [auth_failed] 鉴权失败：请检查 API Key 是否有效、是否有该模型权限。"),
            FacadeError("模型 bad-model 未在 provider 坏模型 中启用"),
            FacadeError("Model names are case-sensitive; model `Bad-Model` not found"),
        ]
        for exc in errors:
            ai = R.ScriptedAI(exc, exc, exc, exc)
            ctx = group_ctx(ai)
            actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, R.reply_to_bot("@helper_bot 今年分最高的是啥", 555)))
            self.assert_buttons(actions, ctx)

    def test_fallback_provider_moderation_gets_own_template_and_buttons(self) -> None:
        # 固定模型不通时平台回退到其它 Provider，最后错误是回退那家的审核拒答。
        exc = FacadeError("Provider「通义」内模型均失败。最后错误: LLMCallFailed: 400 data_inspection_failed: Input data may contain inappropriate content.")
        ctx = group_ctx(R.ScriptedAI(exc, exc))
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, R.ask()))
        self.assert_buttons(actions, ctx, text="内容未通过模型审核，换个问法试试。")

    def test_custom_moderation_template_is_separate_from_error_ai(self) -> None:
        exc = RuntimeError("content_filter: blocked by safety system")
        ctx = group_ctx(R.ScriptedAI(exc), error_moderation_template="审核没过")
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, R.ask()))
        self.assertEqual(failure_edit(actions)["text"], "审核没过")

    def test_unexpected_exception_outside_ai_call_still_has_buttons(self) -> None:
        # 0.4.7 根因：AI 调用 try 之外的异常冒到 on_event 通用兜底，回复 error_ai_template 但没有重试记录。
        ctx = group_ctx(R.ScriptedAI("不会被调用"))
        plugin = PLUGIN.InteractionAIChatPlugin()

        async def boom(*_args, **_kwargs):
            raise KeyError("wiki")

        plugin._wiki_context = boom  # type: ignore[method-assign]
        actions = run(plugin.on_event(ctx, R.ask()))
        token = self.assert_buttons(actions, ctx)
        # 重试按原请求重放（这次 Wiki 正常）。
        plugin2 = PLUGIN.InteractionAIChatPlugin()
        ctx.ai = R.ScriptedAI("重试成功")
        edit = failure_edit(actions)
        result = run(plugin2.on_event(ctx, R.callback(f"iac:r:{token}", edit["message_id"])))
        self.assertTrue(any("重试成功" in str(a.get("text")) for a in result))

    def test_memory_write_failure_does_not_drop_buttons(self) -> None:
        ctx = group_ctx(R.ScriptedAI(RuntimeError("Connection error.")))
        plugin = PLUGIN.InteractionAIChatPlugin()

        async def broken_record(*_args, **_kwargs):
            raise RuntimeError("storage down")

        plugin._record_turn = broken_record  # type: ignore[method-assign]
        actions = run(plugin.on_event(ctx, R.ask()))
        self.assert_buttons(actions, ctx)

    def test_live_failure_send_rejected_falls_back_to_returned_actions_with_buttons(self) -> None:
        class Rejecting(base.LiveMessages):
            async def apply(self, actions, *, entry_key=None):
                if any(a.get("reply_markup") for a in actions):
                    return {"ok": False, "failed": 1}
                return await super().apply(actions, entry_key=entry_key)

        ctx = group_ctx(R.ScriptedAI(RuntimeError("Connection error.")), thinking_placeholder=False)
        ctx.messages = Rejecting()
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, R.ask()))
        sent = [a for a in actions if a.get("type") == "send_message"]
        self.assertEqual(len(sent), 1)
        self.assertNotIn("save_message_id_key", sent[0])
        self.assertEqual([b["text"] for b in R.buttons(sent[0]["reply_markup"])], ["🔄 重试", "🔀 换模型重试"])


class GuestFailureButtonsTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_old_platform_guest_failure_has_no_buttons(self) -> None:
        ctx = R.make_ctx(R.ScriptedAI(RuntimeError("upstream 500")))
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, guest_ask()))
        self.assertIsNone(R.markup_of(actions))
        self.assertFalse(any(a.get("reply_markup") for a in ctx.messages.applied))

    def test_guest_placeholder_failure_edits_inline_with_buttons(self) -> None:
        ctx = guest_ctx(R.ScriptedAI(RuntimeError("upstream 500")))
        actions, token = guest_fail_first(ctx, PLUGIN.InteractionAIChatPlugin())
        edit = failure_edit(actions)
        self.assertEqual(edit["inline_message_id"], "inline-abc")
        self.assertNotIn("chat_id", edit)
        self.assertEqual(edit["text"], CUSTOM_ERROR)
        self.assertEqual([b["text"] for b in R.buttons(edit["reply_markup"])], ["🔄 重试", "🔀 换模型重试"])
        record = ctx.storage.data[f"retry:{token}"]
        self.assertEqual(record["failure_inline_message_id"], "inline-abc")
        self.assertEqual(record["kind"], "guest")
        self.assertEqual(record["chat_id"], GUEST_CHAT)

    def test_guest_without_placeholder_answers_once_with_buttons(self) -> None:
        ctx = guest_ctx(R.ScriptedAI(RuntimeError("upstream 500")), thinking_placeholder=False)
        actions, token = guest_fail_first(ctx, PLUGIN.InteractionAIChatPlugin())
        self.assertEqual(actions, [])
        answered = [a for a in ctx.messages.applied if a.get("type") == "answer_guest_query"]
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0]["send_via"], ["interaction_bot"])
        self.assertEqual([b["text"] for b in R.buttons(answered[0]["reply_markup"])], ["🔄 重试", "🔀 换模型重试"])
        self.assertEqual(ctx.storage.data[f"retry:{token}"]["failure_inline_message_id"], "inline-abc")


class GuestCallbackTest(unittest.TestCase):
    PROVIDERS = (
        R.provider(3, "智谱", "glm-x", [{"id": "glm-x", "enabled": True}, {"id": "glm-y", "label": "GLM Y", "enabled": True}]),
    )

    def setUp(self) -> None:
        base._install_stubs()

    def _failed(self, *results, **config):
        ai = R.ScriptedAI(RuntimeError("upstream 500"), *results, providers=self.PROVIDERS)
        ctx = guest_ctx(ai, telepilot_provider="3", telepilot_model="glm-x", **config)
        ctx.identities = R.Identities()
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, token = guest_fail_first(ctx, plugin)
        ctx.messages.applied.clear()
        return ai, ctx, plugin, token

    def test_requester_retry_edits_inline_message(self) -> None:
        ai, ctx, plugin, token = self._failed("访客重试成功")
        actions = run(plugin.on_event(ctx, inline_callback(f"iac:r:{token}")))
        self.assertEqual(len(ai.calls), 2)
        self.assertEqual(ai.calls[1]["user"], ai.calls[0]["user"])
        self.assertEqual(R.answers(ctx)[0]["text"], "开始重试")
        running = [a for a in ctx.messages.applied if a.get("type") == "edit_message" and "正在重试" in a.get("text", "")]
        self.assertEqual(running[0]["inline_message_id"], "inline-abc")
        final = failure_edit(actions)
        self.assertEqual(final["inline_message_id"], "inline-abc")
        self.assertIn("访客重试成功", final["text"])
        self.assertNotIn("reply_markup", final)
        self.assertNotIn(f"retry:{token}", ctx.storage.data)

    def test_guest_retry_failure_shows_buttons_again_on_same_inline_message(self) -> None:
        _ai, ctx, plugin, token = self._failed(RuntimeError("upstream 503"))
        actions = run(plugin.on_event(ctx, inline_callback(f"iac:r:{token}")))
        final = failure_edit(actions)
        self.assertEqual(final["inline_message_id"], "inline-abc")
        self.assertEqual(R.buttons(final["reply_markup"])[0]["callback_data"], f"iac:r:{token}")

    def test_requester_cannot_switch_model(self) -> None:
        ai, ctx, plugin, token = self._failed()
        for data in (f"iac:m:{token}", f"iac:p:{token}:1", f"iac:s:{token}:0", f"iac:c:{token}"):
            actions = run(plugin.on_event(ctx, inline_callback(data, user_id=ASKER)))
            self.assertEqual(actions, [], data)
            self.assertEqual(R.answers(ctx)[-1]["text"], "只有管理员可以换模型重试。")
            self.assertTrue(R.answers(ctx)[-1]["show_alert"])
        self.assertEqual(len(ai.calls), 1)
        self.assertFalse(any(a.get("type") == "edit_message" for a in ctx.messages.applied))

    def test_third_party_denied_for_both_buttons(self) -> None:
        ai, ctx, plugin, token = self._failed()
        retry = run(plugin.on_event(ctx, inline_callback(f"iac:r:{token}", user_id=OTHER)))
        self.assertEqual(retry, [])
        self.assertEqual(R.answers(ctx)[-1]["text"], "只有提问者或管理员可以重试。")
        switch = run(plugin.on_event(ctx, inline_callback(f"iac:m:{token}", user_id=OTHER)))
        self.assertEqual(switch, [])
        self.assertEqual(R.answers(ctx)[-1]["text"], "只有管理员可以换模型重试。")
        self.assertEqual(len(ai.calls), 1)

    def test_clicker_identity_ignores_original_guest_caller(self) -> None:
        # 即使 payload 里带着当初 @Bot 的访客（guest.caller_user_id = 提问者），点击者仍按 sender 判定。
        ai, ctx, plugin, token = self._failed()
        payload = inline_callback(f"iac:r:{token}", user_id=OTHER, guest={"caller_user_id": ASKER, "chat_id": GUEST_CHAT})
        self.assertEqual(run(plugin.on_event(ctx, payload)), [])
        self.assertEqual(R.answers(ctx)[-1]["text"], "只有提问者或管理员可以重试。")
        self.assertEqual(len(ai.calls), 1)

    def test_admins_can_switch_and_retry(self) -> None:
        cases = [
            ("telepilot_admin", {}, {"admin_user_ids": [ADMIN]}),
            ("owner", {}, {"owner_user_ids": [ADMIN]}),
            ("config_admin", {"retry_admin_user_ids": str(ADMIN)}, {}),
        ]
        for name, config, extra in cases:
            ai, ctx, plugin, token = self._failed("换模型后成功", **config)
            menu = run(plugin.on_event(ctx, inline_callback(f"iac:m:{token}", user_id=ADMIN, **extra)))
            edit = failure_edit(menu)
            self.assertEqual(edit["inline_message_id"], "inline-abc", name)
            labels = [b["text"] for b in R.buttons(edit["reply_markup"])]
            self.assertIn("智谱 · GLM Y", labels, name)
            selected = run(plugin.on_event(ctx, inline_callback(f"iac:s:{token}:0", user_id=ADMIN, **extra)))
            self.assertEqual((ai.calls[1]["provider"], ai.calls[1]["model"]), ("3", "glm-y"), name)
            self.assertTrue(any("换模型后成功" in str(a.get("text")) for a in selected), name)

    def test_admin_can_plain_retry_someone_elses_guest_failure(self) -> None:
        _ai, ctx, plugin, token = self._failed("管理员重试成功")
        actions = run(plugin.on_event(ctx, inline_callback(f"iac:r:{token}", user_id=ADMIN, admin_user_ids=[ADMIN])))
        self.assertTrue(any("管理员重试成功" in str(a.get("text")) for a in actions))

    def test_group_admin_uses_recorded_guest_chat_and_failure_means_not_admin(self) -> None:
        _ai, ctx, plugin, token = self._failed("群管理员重试成功")
        ctx.identities = R.Identities(admins={ADMIN})
        actions = run(plugin.on_event(ctx, inline_callback(f"iac:m:{token}", user_id=ADMIN)))
        self.assertIsNotNone(failure_edit(actions))
        self.assertIn((GUEST_CHAT, ADMIN), ctx.identities.calls)

        class Broken:
            async def resolve(self, **_kwargs):
                raise RuntimeError("bot is not a member of the chat")

        ctx.identities = Broken()
        denied = run(plugin.on_event(ctx, inline_callback(f"iac:m:{token}", user_id=ADMIN)))
        self.assertEqual(denied, [])
        self.assertEqual(R.answers(ctx)[-1]["text"], "只有管理员可以换模型重试。")

    def test_wrong_inline_message_or_chat_record_is_expired(self) -> None:
        _ai, ctx, plugin, token = self._failed()
        actions = run(plugin.on_event(ctx, inline_callback(f"iac:r:{token}", inline_id="inline-other")))
        self.assertEqual(actions, [])
        self.assertEqual(R.answers(ctx)[-1]["text"], "这条重试已过期，请重新提问。")

        group = R.make_ctx(R.ScriptedAI(RuntimeError("upstream 500")))
        group_plugin = PLUGIN.InteractionAIChatPlugin()
        _, _, group_token = R.fail_first(group, group_plugin)
        denied = run(group_plugin.on_event(group, inline_callback(f"iac:r:{group_token}")))
        self.assertEqual(denied, [])
        self.assertEqual(R.answers(group)[-1]["text"], "这条重试已过期，请重新提问。")

    def test_concurrent_guest_clicks_run_once(self) -> None:
        ai = R.ScriptedAI(RuntimeError("upstream 500"), "成功", delay=0.0)
        ctx = guest_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        _, token = guest_fail_first(ctx, plugin)
        plugin._retry_running.add(token)
        actions = run(plugin.on_event(ctx, inline_callback(f"iac:r:{token}")))
        self.assertEqual(actions, [])
        self.assertEqual(R.answers(ctx)[-1]["text"], "正在重试，请稍候…")
        self.assertEqual(len(ai.calls), 1)

    def test_guest_text_retry_clears_old_inline_buttons(self) -> None:
        ai = R.ScriptedAI(RuntimeError("upstream 500"), "文本重试成功")
        ctx = guest_ctx(ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        guest_fail_first(ctx, plugin)
        actions = run(plugin.on_event(ctx, guest_ask("重试", query_id="g2")))
        cleared = [a for a in actions if a.get("inline_message_id") == "inline-abc" and "已重新回答" in a.get("text", "")]
        self.assertEqual(len(cleared), 1)
        self.assertNotIn("reply_markup", cleared[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

