from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "interaction-ai-chat"


def _install_stubs() -> None:
    base_module = types.ModuleType("app.worker.plugins.base")
    manifest_module = types.ModuleType("app.worker.plugins.manifest")
    command_module = types.ModuleType("app.worker.command")

    class Plugin:
        pass

    class PluginContext:
        pass

    class Manifest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def register(cls):
        return cls

    def current_command_prefix(*, fallback=None):
        return "。"

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    manifest_module.Manifest = Manifest
    command_module.current_command_prefix = current_command_prefix
    command_module._BUILTIN_ALIAS_TO_PRIMARY = {"help": "help", "fd": "fd"}
    command_module._PLUGIN_COMMANDS = {"10d": object()}
    command_module._BUILTIN = {"help": object()}

    sys.modules["app"] = types.ModuleType("app")
    sys.modules["app.worker"] = types.ModuleType("app.worker")
    sys.modules["app.worker.plugins"] = types.ModuleType("app.worker.plugins")
    sys.modules["app.worker.plugins.base"] = base_module
    sys.modules["app.worker.plugins.manifest"] = manifest_module
    sys.modules["app.worker.command"] = command_module


def _load_package():
    _install_stubs()
    name = "_telebot_installed_plugin_interaction-ai-chat"
    for key in list(sys.modules):
        if key == name or key.startswith(f"{name}."):
            sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, sys.modules[f"{name}.plugin"]


PACKAGE, PLUGIN = _load_package()


class FakeStorage:
    """模拟 ctx.storage（PluginStorage）：JSON 值 + 可选 TTL。"""

    available = True

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.ttls: dict[str, float] = {}

    async def get(self, key, default=None):
        return json.loads(json.dumps(self.data[key])) if key in self.data else default

    async def set(self, key, value, *, ttl=None):
        self.data[key] = json.loads(json.dumps(value, ensure_ascii=False))
        if ttl is not None:
            self.ttls[key] = ttl
        return True

    async def delete(self, *keys):
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    async def incr(self, key, amount=1, *, ttl=None):
        self.data[key] = int(self.data.get(key, 0)) + int(amount)
        if ttl is not None:
            self.ttls[key] = ttl
        return self.data[key]


class FakeAI:
    def __init__(self, text="模型回答", exc: Exception | None = None, delay: float = 0.0) -> None:
        self.calls: list[dict] = []
        self.text = text
        self.exc = exc
        self.delay = delay

    async def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(text=self.text)


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def send(self, **kwargs):  # pragma: no cover - 插件不应直接调用
        self.calls.append(("send", kwargs))

    async def answer_guest_query(self, **kwargs):  # pragma: no cover
        self.calls.append(("answer_guest_query", kwargs))


class Ctx:
    def __init__(self, config=None, ai=None, storage=None) -> None:
        self.account_id = 1
        self.feature_key = "interaction-ai-chat"
        self.config = dict(config or {})
        self.ai = ai if ai is not None else FakeAI()
        self.storage = storage if storage is not None else FakeStorage()
        self.messages = FakeMessages()
        self.logs: list[tuple] = []

    async def log(self, level, message, **detail):
        self.logs.append((level, message, detail))


def guest_payload(text, *, user_id=501, chat_id=-100555, query_id="guest-1", reply_text=None):
    payload = {
        "source": {"type": "guest_message", "channel": "interaction_bot", "guest_query_id": query_id},
        "event_type": "guest_message",
        "message": {"chat_id": chat_id, "message_id": 77, "text": text},
        "chat": {"id": chat_id, "type": "supergroup", "title": "外部群"},
        "sender": {"user_id": user_id, "display_name": "访客"},
        "guest": {
            "query_id": query_id,
            "chat_id": chat_id,
            "chat_type": "supergroup",
            "caller_user_id": user_id,
            "raw_text": f"@helper_bot {text}",
            "reply_once": True,
            "reply_via": "answer_guest_query",
        },
        "reply_to": None,
    }
    if reply_text is not None:
        payload["reply_to"] = {"message_id": 70, "text": reply_text, "username": "someone"}
    return payload


def message_payload(text, *, chat_id=-100777, chat_type="supergroup", user_id=601, reply_username=None, reply_text=None):
    payload = {
        "source": {"type": "message", "channel": "interaction_bot"},
        "event_type": "message",
        "message": {"chat_id": chat_id, "message_id": 88, "text": text},
        "chat": {"id": chat_id, "type": chat_type},
        "sender": {"user_id": user_id},
        "reply_to": None,
    }
    if reply_username is not None or reply_text is not None:
        payload["reply_to"] = {"message_id": 80, "username": reply_username, "text": reply_text}
    return payload


def run(coro):
    return asyncio.run(coro)


class _StubbedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # 其它测试文件会替换 app.* 桩模块；每个用例前重新安装本文件的桩。
        _install_stubs()


class ManifestTest(_StubbedTestCase):
    def test_static_and_runtime_manifest_match(self) -> None:
        raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        manifest = PACKAGE.MANIFEST
        self.assertEqual(raw["name"], manifest.key)
        self.assertEqual(raw["name"], PLUGIN.InteractionAIChatPlugin.key)
        self.assertEqual(raw["version"], manifest.version)
        self.assertEqual(raw["display_name"], "AI 答疑（交互 Bot）")
        self.assertEqual(raw["config_schema"], manifest.config_schema)
        self.assertEqual(raw["config_actions"], manifest.config_actions)
        self.assertEqual(raw["event_subscriptions"], manifest.event_subscriptions)
        self.assertEqual(raw["permissions"], manifest.permissions)
        self.assertIn("ai_text", raw["permissions"])
        self.assertIs(PACKAGE.PLUGIN_CLASS, PLUGIN.InteractionAIChatPlugin)

    def test_subscriptions_cover_guest_and_bot_messages(self) -> None:
        subs = PACKAGE.MANIFEST.event_subscriptions
        guest = next(item for item in subs if "guest_message" in item["events"])
        self.assertEqual(guest["source"], ["interaction_bot"])
        self.assertEqual(guest["scope"], "guest_all")
        message = next(item for item in subs if "message" in item["events"])
        self.assertEqual(message["source"], ["interaction_bot"])
        self.assertEqual(message["scope"], "all_allowed_chats")

    def test_schema_documents_botfather_setup_and_required_fields(self) -> None:
        schema = PACKAGE.MANIFEST.config_schema
        usage = schema["properties"]["usage_preview"]
        self.assertTrue(usage["readOnly"])
        self.assertIn("Guest Chat Mode", usage["default"])
        self.assertIn("Inline Mode", usage["default"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]) - {"usage_preview"})
        self.assertLessEqual(schema["properties"]["timeout_seconds"]["maximum"], 13)
        self.assertEqual(schema["properties"]["max_history_rounds"]["default"], 6)
        self.assertEqual(schema["properties"]["max_history_rounds"]["maximum"], 20)
        self.assertEqual(schema["properties"]["per_user_daily_limit"]["default"], 20)


class GuestReplyTest(_StubbedTestCase):
    def test_guest_message_answers_once_with_answer_guest_query(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        actions = run(plugin.on_event(ctx, guest_payload("什么是量子纠缠？")))
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["type"], "answer_guest_query")
        self.assertEqual(action["guest_query_id"], "guest-1")
        self.assertEqual(action["text"], "模型回答")
        self.assertEqual(action["parse_mode"], "plain")
        self.assertEqual(ctx.messages.calls, [])
        self.assertEqual(len(ctx.ai.calls), 1)
        call = ctx.ai.calls[0]
        self.assertIn("什么是量子纠缠？", call["user"])
        self.assertIn("机器人", call["system"])
        self.assertIn("不执行", call["system"])
        self.assertNotIn("provider", call)
        self.assertNotIn("provider_tag", call)
        self.assertEqual(call["timeout_seconds"], 12)
        # 不把用户 ID 写进提示词
        self.assertNotIn("501", call["user"] + call["system"])

    def test_fixed_provider_and_model_are_forwarded(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"telepilot_provider": "7", "telepilot_model": "gpt-x", "timeout_seconds": 99})
        run(plugin.on_event(ctx, guest_payload("你好")))
        call = ctx.ai.calls[0]
        self.assertEqual(call["provider"], "7")
        self.assertEqual(call["model"], "gpt-x")
        self.assertEqual(call["timeout_seconds"], 13)

    def test_guest_disabled_returns_nothing(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"enable_guest": False})
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("你好"))), [])
        self.assertEqual(ctx.ai.calls, [])

    def test_empty_question_returns_help_without_ai(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        actions = run(plugin.on_event(ctx, guest_payload("")))
        self.assertEqual(actions[0]["text"], PLUGIN.HELP_REPLY)
        self.assertEqual(ctx.ai.calls, [])

    def test_reply_to_text_is_added_as_context(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        run(plugin.on_event(ctx, guest_payload("这句话什么意思？", reply_text="塞翁失马焉知非福")))
        user_prompt = ctx.ai.calls[0]["user"]
        self.assertIn("塞翁失马焉知非福", user_prompt)
        self.assertIn("用户回复的消息", user_prompt)

    def test_ai_failure_gives_friendly_error_and_refunds_quota(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(ai=FakeAI(exc=RuntimeError("401 unauthorized: Bearer sk-secret")))
        actions = run(plugin.on_event(ctx, guest_payload("你好")))
        self.assertEqual(actions[0]["text"], PLUGIN.AI_UNAVAILABLE_REPLY)
        self.assertNotIn("Traceback", actions[0]["text"])
        quota_values = [v for k, v in ctx.storage.data.items() if k.startswith("quota:")]
        self.assertTrue(quota_values)
        self.assertTrue(all(value == 0 for value in quota_values))
        self.assertFalse(any("sk-secret" in str(item) for item in ctx.logs))

    def test_ai_timeout_gives_timeout_reply(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"timeout_seconds": 3}, ai=FakeAI(delay=10))
        original = PLUGIN.asyncio.wait_for

        async def fast_wait_for(awaitable, timeout):
            return await original(awaitable, timeout=0.05)

        PLUGIN.asyncio.wait_for = fast_wait_for
        try:
            actions = run(plugin.on_event(ctx, guest_payload("你好")))
        finally:
            PLUGIN.asyncio.wait_for = original
        self.assertEqual(actions[0]["text"], PLUGIN.AI_TIMEOUT_REPLY)

    def test_unexpected_error_still_replies_once(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()

        class BrokenStorage(FakeStorage):
            async def incr(self, key, amount=1, *, ttl=None):
                raise RuntimeError("redis down")

            async def get(self, key, default=None):
                raise RuntimeError("redis down")

        ctx = Ctx(storage=BrokenStorage())
        actions = run(plugin.on_event(ctx, guest_payload("你好")))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "answer_guest_query")

    def test_long_output_is_truncated_within_telegram_limit(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"max_output_chars": 300}, ai=FakeAI(text="长" * 5000))
        text = run(plugin.on_event(ctx, guest_payload("写长文")))[0]["text"]
        self.assertLessEqual(len(text), 300)
        self.assertTrue(text.endswith("[内容过长，已截断]"))

    def test_thinking_tags_are_removed(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(ai=FakeAI(text="<think>内部推理</think>最终答案"))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "最终答案")

    def test_slash_command_is_left_to_other_features(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("/rules"))), [])
        self.assertEqual(ctx.ai.calls, [])


class MemoryTest(_StubbedTestCase):
    def test_memory_is_written_and_used_in_next_turn(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        ctx.ai.text = "第一轮回答"
        run(plugin.on_event(ctx, guest_payload("第一问")))
        ctx.ai.text = "第二轮回答"
        run(plugin.on_event(ctx, guest_payload("第二问")))
        second_prompt = ctx.ai.calls[1]["user"]
        self.assertIn("第一问", second_prompt)
        self.assertIn("第一轮回答", second_prompt)
        mem_keys = [k for k in ctx.storage.data if k.startswith("mem:")]
        self.assertEqual(len(mem_keys), 1)
        # 存储键不含明文会话/用户 ID
        self.assertNotIn("501", mem_keys[0])
        self.assertNotIn("100555", mem_keys[0])
        self.assertEqual(ctx.storage.ttls[mem_keys[0]], 30 * 60)
        record = ctx.storage.data[mem_keys[0]]
        self.assertEqual(len(record["turns"]), 4)

    def test_memory_is_isolated_per_chat_and_user(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        run(plugin.on_event(ctx, guest_payload("甲的问题", user_id=1)))
        run(plugin.on_event(ctx, guest_payload("乙的问题", user_id=2)))
        run(plugin.on_event(ctx, guest_payload("甲在别的群", user_id=1, chat_id=-100999)))
        self.assertNotIn("甲的问题", ctx.ai.calls[1]["user"])
        self.assertNotIn("甲的问题", ctx.ai.calls[2]["user"])

    def test_memory_keeps_only_configured_rounds(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"max_history_rounds": 2})
        for idx in range(5):
            run(plugin.on_event(ctx, guest_payload(f"问题{idx}")))
        record = next(v for k, v in ctx.storage.data.items() if k.startswith("mem:"))
        self.assertEqual(len(record["turns"]), 4)
        self.assertEqual(record["turns"][0], ["u", "问题3"])

    def test_zero_rounds_disables_memory(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"max_history_rounds": 0})
        run(plugin.on_event(ctx, guest_payload("第一问")))
        run(plugin.on_event(ctx, guest_payload("第二问")))
        self.assertFalse(any(k.startswith("mem:") for k in ctx.storage.data))
        self.assertNotIn("第一问", ctx.ai.calls[1]["user"])

    def test_expired_memory_is_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"memory_ttl_minutes": 30})
        run(plugin.on_event(ctx, guest_payload("旧问题")))
        key = next(k for k in ctx.storage.data if k.startswith("mem:"))
        ctx.storage.data[key]["updated"] = time.time() - 31 * 60
        run(plugin.on_event(ctx, guest_payload("新问题")))
        self.assertNotIn("旧问题", ctx.ai.calls[1]["user"])

    def test_reset_clears_memory_without_calling_ai(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        run(plugin.on_event(ctx, guest_payload("记住我喜欢猫")))
        actions = run(plugin.on_event(ctx, guest_payload("重置")))
        self.assertEqual(actions[0]["text"], PLUGIN.RESET_REPLY)
        self.assertEqual(len(ctx.ai.calls), 1)
        self.assertFalse(any(k.startswith("mem:") for k in ctx.storage.data))
        actions = run(plugin.on_event(ctx, guest_payload("reset")))
        self.assertEqual(actions[0]["text"], PLUGIN.RESET_REPLY)
        run(plugin.on_event(ctx, guest_payload("我喜欢什么？")))
        self.assertNotIn("猫", ctx.ai.calls[-1]["user"])

    def test_local_fallback_when_storage_unavailable(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        storage = FakeStorage()
        storage.available = False
        ctx = Ctx(storage=storage)
        run(plugin.on_event(ctx, guest_payload("第一问")))
        run(plugin.on_event(ctx, guest_payload("第二问")))
        self.assertIn("第一问", ctx.ai.calls[1]["user"])
        self.assertEqual(storage.data, {})


class LimitTest(_StubbedTestCase):
    def test_per_user_daily_limit(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"per_user_daily_limit": 2})
        for _ in range(2):
            self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "模型回答")
        text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
        self.assertIn("每日 2 次", text)
        self.assertEqual(len(ctx.ai.calls), 2)
        # 另一个用户不受影响
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问", user_id=9)))[0]["text"], "模型回答")

    def test_exempt_user_skips_per_user_limit(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"per_user_daily_limit": 1, "exempt_user_ids": "501"})
        for _ in range(3):
            self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "模型回答")

    def test_global_daily_limit(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"global_daily_limit": 2, "per_user_daily_limit": 0})
        run(plugin.on_event(ctx, guest_payload("问", user_id=1)))
        run(plugin.on_event(ctx, guest_payload("问", user_id=2)))
        text = run(plugin.on_event(ctx, guest_payload("问", user_id=3)))[0]["text"]
        self.assertEqual(text, PLUGIN.GLOBAL_LIMIT_REPLY)
        self.assertEqual(len(ctx.ai.calls), 2)
        # 超限的那次已回滚，不会把用户计数一起吃掉
        self.assertEqual(ctx.storage.data[f"quota:global:{PLUGIN._today()}"], 2)

    def test_global_limit_rolls_back_user_counter(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"global_daily_limit": 1, "per_user_daily_limit": 5})
        run(plugin.on_event(ctx, guest_payload("问", user_id=1)))
        run(plugin.on_event(ctx, guest_payload("问", user_id=2)))
        user_counts = [v for k, v in ctx.storage.data.items() if k.startswith("quota:user:")]
        self.assertEqual(sorted(user_counts), [0, 1])

    def test_input_length_limit(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"max_input_chars": 20})
        text = run(plugin.on_event(ctx, guest_payload("长" * 21)))[0]["text"]
        self.assertIn("上限 20 字", text)
        self.assertEqual(ctx.ai.calls, [])
        self.assertFalse(any(k.startswith("quota:") for k in ctx.storage.data))

    def test_quota_keys_have_ttl_and_no_plain_ids(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        run(plugin.on_event(ctx, guest_payload("问")))
        for key in ctx.storage.data:
            if key.startswith("quota:"):
                self.assertNotIn("501", key)
                self.assertEqual(ctx.storage.ttls[key], PLUGIN.QUOTA_KEY_TTL_SECONDS)


class AccessTest(_StubbedTestCase):
    def test_whitelist_mode_denies_unknown_user(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"access_mode": "仅白名单用户", "allowed_user_ids": "1, 2"})
        actions = run(plugin.on_event(ctx, guest_payload("问", user_id=501)))
        self.assertEqual(actions[0]["text"], "这个 Bot 的 AI 答疑暂未对你开放。")
        self.assertEqual(ctx.ai.calls, [])
        actions = run(plugin.on_event(ctx, guest_payload("问", user_id=2)))
        self.assertEqual(actions[0]["text"], "模型回答")

    def test_whitelist_mode_with_empty_deny_reply_is_silent(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"access_mode": "whitelist", "allowed_user_ids": "1", "deny_reply": ""})
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问"))), [])

    def test_chat_blacklist_and_whitelist(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"chat_blacklist": "-100555"})
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问"))), [])
        ctx = Ctx(config={"chat_whitelist": "-100123"})
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问"))), [])
        ctx = Ctx(config={"chat_whitelist": "-100555\n-100123"})
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "模型回答")


class GuardTest(_StubbedTestCase):
    def test_slash_command_output_is_blocked(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(ai=FakeAI(text="好的\n/ban @someone"))
        text = run(plugin.on_event(ctx, guest_payload("帮我封人")))[0]["text"]
        self.assertEqual(text, PLUGIN.COMMAND_REFUSAL)
        self.assertTrue(any("拦截" in item[1] for item in ctx.logs))

    def test_telepilot_prefix_and_bare_command_output_is_blocked(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        for output in ("。hb 100", "fd 1", "10d", "help"):
            ctx = Ctx(ai=FakeAI(text=output))
            text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
            self.assertEqual(text, PLUGIN.COMMAND_REFUSAL, output)

    def test_payment_like_output_gets_penalty_reply(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        for output in ("+100", "+ 50", "+100-200", "+1至9"):
            ctx = Ctx(ai=FakeAI(text=output))
            self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "-66666", output)

    def test_payment_guard_reply_cannot_itself_be_plus(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"payment_guard_reply": "+999"}, ai=FakeAI(text="+100"))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "-66666")
        ctx = Ctx(config={"payment_guard_reply": ""}, ai=FakeAI(text="+100"))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], PLUGIN.COMMAND_REFUSAL)

    def test_normal_text_with_numbers_is_not_blocked(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(ai=FakeAI(text="答案是 +100 摄氏度左右，水在 100 度沸腾。"))
        text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
        self.assertIn("100 度", text)

    def test_guard_can_be_disabled(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"protect_command_outputs": False}, ai=FakeAI(text="/start"))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "/start")

    def test_blocked_output_is_not_stored_as_memory(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(ai=FakeAI(text="+100"))
        run(plugin.on_event(ctx, guest_payload("问")))
        record = next(v for k, v in ctx.storage.data.items() if k.startswith("mem:"))
        self.assertNotIn(["a", "+100"], record["turns"])


class BotMessageTest(_StubbedTestCase):
    def test_private_chat_replies_without_mention(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        actions = run(plugin.on_event(ctx, message_payload("你好", chat_id=601, chat_type="private")))
        self.assertEqual(actions, [{"type": "send_message", "chat_id": 601, "text": "模型回答", "parse_mode": "plain"}])

    def test_private_chat_can_be_disabled(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"enable_private": False})
        self.assertEqual(run(plugin.on_event(ctx, message_payload("你好", chat_id=601, chat_type="private"))), [])

    def test_private_start_command_is_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        self.assertEqual(run(plugin.on_event(ctx, message_payload("/start", chat_id=601, chat_type="private"))), [])
        self.assertEqual(ctx.ai.calls, [])

    def test_group_without_bot_username_is_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        self.assertEqual(run(plugin.on_event(ctx, message_payload("@helper_bot 你好"))), [])
        self.assertEqual(ctx.ai.calls, [])

    def test_group_mention_triggers_reply_to_message(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"bot_username": "@Helper_Bot"})
        actions = run(plugin.on_event(ctx, message_payload("@helper_bot 今天星期几")))
        self.assertEqual(actions[0]["type"], "send_message")
        self.assertEqual(actions[0]["reply_to_message_id"], 88)
        self.assertEqual(actions[0]["chat_id"], -100777)
        self.assertNotIn("@helper_bot", ctx.ai.calls[0]["user"])
        self.assertIn("今天星期几", ctx.ai.calls[0]["user"])

    def test_group_plain_message_and_other_bot_mention_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"bot_username": "helper_bot"})
        self.assertEqual(run(plugin.on_event(ctx, message_payload("大家好"))), [])
        self.assertEqual(run(plugin.on_event(ctx, message_payload("@helper_bot2 你好"))), [])
        self.assertEqual(run(plugin.on_event(ctx, message_payload("回复别人", reply_username="other_bot", reply_text="x"))), [])
        self.assertEqual(ctx.ai.calls, [])

    def test_group_reply_to_bot_continues_with_memory(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"bot_username": "helper_bot"})
        ctx.ai.text = "第一轮"
        run(plugin.on_event(ctx, message_payload("@helper_bot 第一问")))
        ctx.ai.text = "第二轮"
        actions = run(
            plugin.on_event(ctx, message_payload("那第二呢", reply_username="helper_bot", reply_text="第一轮"))
        )
        self.assertEqual(actions[0]["text"], "第二轮")
        prompt = ctx.ai.calls[1]["user"]
        self.assertIn("第一问", prompt)
        self.assertNotIn("用户回复的消息", prompt)

    def test_group_disabled_and_edited_message_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"bot_username": "helper_bot", "enable_group": False})
        self.assertEqual(run(plugin.on_event(ctx, message_payload("@helper_bot 你好"))), [])
        ctx = Ctx(config={"bot_username": "helper_bot"})
        payload = message_payload("@helper_bot 你好")
        payload["message"]["edited"] = True
        self.assertEqual(run(plugin.on_event(ctx, payload)), [])

    def test_unrelated_event_types_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        payload = message_payload("你好", chat_id=601, chat_type="private")
        payload["event_type"] = "callback_query"
        payload["source"]["type"] = "callback_query"
        self.assertEqual(run(plugin.on_event(ctx, payload)), [])


class ConfigActionTest(_StubbedTestCase):
    def test_test_model_action_reports_success(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        result = run(plugin.on_config_action(ctx, "test_model", {"config": {}, "input": {"test_message": "你是谁"}}))
        self.assertTrue(result["result"]["ok"])
        self.assertIn("模型回答", result["message"])
        self.assertFalse(any(k.startswith("quota:") for k in ctx.storage.data))

    def test_test_model_action_reports_failure(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(ai=FakeAI(exc=RuntimeError("429 too many requests")))
        result = run(plugin.on_config_action(ctx, "test_model", {"config": {}, "input": {}}))
        self.assertFalse(result["result"]["ok"])
        self.assertIn("频繁", result["message"])

    def test_unknown_action_returns_none(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        self.assertIsNone(run(plugin.on_config_action(Ctx(), "other", {})))

    def test_missing_ctx_ai_gives_hint(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        ctx.ai = None
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(actions[0]["text"], PLUGIN.AI_UNAVAILABLE_REPLY)
        self.assertTrue(any("ai_text" in item[1] for item in ctx.logs))


if __name__ == "__main__":
    unittest.main()
