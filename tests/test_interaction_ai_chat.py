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
        # 联网搜索默认开启；多数用例用不支持 web_search 的旧版 FakeAI，这里默认关掉，专门的用例再显式开启。
        self.config = {"enable_web_search": False, **dict(config or {})}
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
        self.assertEqual(
            set(schema["required"]),
            {key for key in schema["properties"] if not key.startswith("pt_")}
            - {"usage_preview", "template_placeholders", "deny_reply"},
        )
        # PT 站点数据源字段全部可选，不进 required。
        self.assertFalse([key for key in schema["required"] if key.startswith("pt_")])
        self.assertNotIn("timeout_seconds", schema["properties"])  # 已合并进 handler_timeout_seconds
        handler = schema["properties"]["handler_timeout_seconds"]
        self.assertEqual((handler["default"], handler["minimum"], handler["maximum"]), (45, 10, 85))
        self.assertTrue(schema["properties"]["enable_web_search"]["default"])
        self.assertIn("/setprivacy", schema["properties"]["trigger_keywords"]["description"])
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
        self.assertEqual(action["parse_mode"], "html")
        self.assertEqual(ctx.messages.calls, [])
        self.assertEqual(len(ctx.ai.calls), 1)
        call = ctx.ai.calls[0]
        self.assertIn("什么是量子纠缠？", call["user"])
        self.assertIn("机器人", call["system"])
        self.assertIn("不执行", call["system"])
        self.assertNotIn("provider", call)
        self.assertNotIn("provider_tag", call)
        self.assertEqual(call["timeout_seconds"], 45)
        # 不把用户 ID 写进提示词
        self.assertNotIn("501", call["user"] + call["system"])

    def test_fixed_provider_and_model_are_forwarded(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"telepilot_provider": "7", "telepilot_model": "gpt-x", "handler_timeout_seconds": 999})
        run(plugin.on_event(ctx, guest_payload("你好")))
        call = ctx.ai.calls[0]
        self.assertEqual(call["provider"], "7")
        self.assertEqual(call["model"], "gpt-x")
        self.assertEqual(call["timeout_seconds"], 85)

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
        ctx = Ctx(config={"handler_timeout_seconds": 10}, ai=FakeAI(delay=30))
        original = PLUGIN.asyncio.wait_for

        async def fast_wait_for(awaitable, timeout):
            return await original(awaitable, timeout=0.05)

        PLUGIN.asyncio.wait_for = fast_wait_for
        try:
            actions = run(plugin.on_event(ctx, guest_payload("你好")))
        finally:
            PLUGIN.asyncio.wait_for = original
        self.assertEqual(actions[0]["text"], "AI 思考超时了（10 秒），换个简短点的问题再试试？")

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
        self.assertEqual(text, PLUGIN.GLOBAL_LIMIT_REPLY.format(limit=2))
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
        records = [v for k, v in ctx.storage.data.items() if k.startswith("mem:")]
        self.assertFalse(any(["a", "+100"] in record["turns"] for record in records))


class BotMessageTest(_StubbedTestCase):
    def test_private_chat_replies_without_mention(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        actions = run(plugin.on_event(ctx, message_payload("你好", chat_id=601, chat_type="private")))
        self.assertEqual(
            actions,
            [{"type": "send_message", "chat_id": 601, "text": "模型回答", "parse_mode": "html", "send_via": ["interaction_bot"]}],
        )

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


# ---------------------------------------------------------------------------
# 0.2.0：占位消息、富文本、模板、联网、识图
# ---------------------------------------------------------------------------

TH = sys.modules[f"{PACKAGE.__name__}.telegram_html"]


class LiveMessages:
    """模拟交互入口内的 ctx.messages：apply 实时执行并记录 message_id / inline_message_id。"""

    def __init__(self, *, fail: bool = False, inline: bool = True) -> None:
        self.applied: list[dict] = []
        self.saved: dict[str, object] = {}
        self.fail = fail
        self._next_id = 900
        if not inline:
            self.read_saved_inline_message_id = None  # type: ignore[assignment]

    async def apply(self, actions, *, entry_key=None):
        if self.fail:
            raise RuntimeError("bot api down")
        for action in actions:
            self.applied.append(dict(action))
            key = action.get("save_message_id_key")
            if not key:
                continue
            if action["type"] == "answer_guest_query":
                self.saved[key] = "inline-abc"
            else:
                self._next_id += 1
                self.saved[key] = self._next_id
        return {"ok": True}

    async def read_saved_message_id(self, key):
        value = self.saved.get(key)
        return value if isinstance(value, int) else None

    async def read_saved_inline_message_id(self, key):
        value = self.saved.get(key)
        return value if isinstance(value, str) else None


class CapAI(FakeAI):
    """带 images / web_search 参数的新版 ctx.ai。"""

    def __init__(self, *args, vision=True, unsupported=(), sources=None, model="gpt-x", provider_name="OpenAI", **kwargs):
        super().__init__(*args, **kwargs)
        self.vision_enabled = vision
        self.unsupported = set(unsupported)
        self.sources = sources or []
        self.model = model
        self.provider_name = provider_name
        self.fetched: list[str] = []

    async def fetch_bot_image(self, file_id, *, max_bytes=0):
        self.fetched.append(file_id)
        if file_id == "broken":
            raise RuntimeError("download failed")
        return b"\xff\xd8jpeg"

    async def complete(self, system, user, *, images=None, web_search=False, web_search_context_size=None, **kwargs):
        self.calls.append({"system": system, "user": user, "images": images, "web_search": web_search, **kwargs})
        if web_search and "web_search" in self.unsupported:
            raise type("AICapabilityError", (RuntimeError,), {})("所有 provider 均不兼容: provider 不支持原生联网搜索")
        if images and "images" in self.unsupported:
            raise type("AICapabilityError", (RuntimeError,), {})("所有 provider 均不兼容: provider 不支持图片输入")
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(text=self.text, sources=self.sources, model=self.model, provider_name=self.provider_name)


def live_ctx(config=None, ai=None, messages=None):
    ctx = Ctx(config=config, ai=ai)
    ctx.messages = messages if messages is not None else LiveMessages()
    return ctx


class RichTextTest(_StubbedTestCase):
    def test_markdown_converts_to_telegram_html_and_escapes_raw_html(self) -> None:
        out = TH.markdown_to_telegram_html(
            "**粗** *斜* `a<b>` ~~删~~ ||剧透||\n<script>x</script> & y\n> 引用\n- 项\n[链](https://e.com/?a=1&b=2) [坏](javascript:alert)\n"
            "```py\nprint('<b>')\n```"
        )
        self.assertIn("<b>粗</b>", out)
        self.assertIn("<i>斜</i>", out)
        self.assertIn("<code>a&lt;b&gt;</code>", out)
        self.assertIn("<s>删</s>", out)
        self.assertIn("<tg-spoiler>剧透</tg-spoiler>", out)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; y", out)
        self.assertIn("<blockquote>引用</blockquote>", out)
        self.assertIn("• 项", out)
        self.assertIn('<a href="https://e.com/?a=1&amp;b=2">链</a>', out)
        self.assertNotIn("javascript", out.replace("[坏](javascript:alert)", ""))
        self.assertIn('<pre><code class="language-py">print(\'&lt;b&gt;\')</code></pre>', out)

    def test_snake_case_and_math_are_not_italicized(self) -> None:
        out = TH.markdown_to_telegram_html("snake_case_name 与 2*3*4")
        self.assertEqual(out, "snake_case_name 与 2*3*4")

    def test_split_html_keeps_tags_balanced(self) -> None:
        text = "<b>" + "字" * 5000 + "</b>\n<pre><code>" + "line\n" * 900 + "</code></pre>"
        parts = TH.split_html(text, 4000)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(TH.visible_length(part), 4000)
            self.assertEqual(part.count("<b>"), part.count("</b>"))
            self.assertEqual(part.count("<pre>"), part.count("</pre>"))
        self.assertEqual("".join(TH.html_to_plain(p) for p in parts).count("字"), 5000)

    def test_split_never_cuts_inside_entities(self) -> None:
        parts = TH.split_html("&amp;" * 5000, 4000)
        for part in parts:
            self.assertNotRegex(part, r"&[a-z]*$")
            self.assertNotRegex(part, r"^[a-z]*;")

    def test_sanitize_template_keeps_supported_tags_only(self) -> None:
        out = TH.sanitize_template_html('<b>好</b><foo>x</foo> & <a href="javascript:1">坏</a> <strong>强 <a href="https://a.b">l</a>')
        self.assertEqual(
            out,
            '<b>好</b>&lt;foo&gt;x&lt;/foo&gt; &amp; &lt;a href="javascript:1"&gt;坏&lt;/a&gt; <b>强 <a href="https://a.b">l</a></b>',
        )

    def test_long_answer_is_split_into_multiple_messages(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"max_output_chars": 9000}, ai=CapAI(text="段落。" * 3000))
        actions = run(plugin.on_event(ctx, message_payload("写长文", chat_id=601, chat_type="private")))
        self.assertEqual(actions[0]["type"], "edit_message")
        self.assertTrue(all(a["type"] == "send_message" for a in actions[1:]))
        self.assertGreaterEqual(len(actions), 2)
        for action in actions:
            self.assertLessEqual(TH.visible_length(action["text"]), 4096)

    def test_guest_long_answer_is_truncated_to_one_message(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"max_output_chars": 9000}, ai=CapAI(text="段落。" * 3000))
        actions = run(plugin.on_event(ctx, guest_payload("写长文")))
        self.assertEqual(len(actions), 1)
        self.assertIn("已截断", actions[0]["text"])
        self.assertLessEqual(TH.visible_length(actions[0]["text"]), 4096)

    def test_rich_text_can_be_disabled(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"rich_text": False}, ai=FakeAI(text="**粗** <b>"))
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(actions[0]["parse_mode"], "plain")
        self.assertEqual(actions[0]["text"], "**粗** <b>")
        self.assertIn("纯文本", ctx.ai.calls[0]["system"])


class PlaceholderTest(_StubbedTestCase):
    def test_message_placeholder_is_sent_then_edited(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"bot_username": "helper_bot"}, ai=CapAI(text="**答案**"))
        actions = run(plugin.on_event(ctx, message_payload("@helper_bot 问题")))
        placeholder = ctx.messages.applied[0]
        self.assertEqual(placeholder["type"], "send_message")
        self.assertEqual(placeholder["text"], "🤔")
        self.assertEqual(placeholder["reply_to_message_id"], 88)
        self.assertEqual(placeholder["send_via"], ["interaction_bot"])
        self.assertEqual(
            actions,
            [
                {
                    "type": "edit_message",
                    "chat_id": -100777,
                    "message_id": 901,
                    "text": "<b>答案</b>",
                    "parse_mode": "html",
                    "send_via": ["interaction_bot"],
                    "send_on_edit_failure": True,
                    "reply_to_message_id": 88,
                }
            ],
        )

    def test_guest_placeholder_answers_then_edits_inline_message(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(ai=CapAI(text="答"))
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(ctx.messages.applied[0]["type"], "answer_guest_query")
        self.assertEqual(ctx.messages.applied[0]["guest_query_id"], "guest-1")
        self.assertEqual(ctx.messages.applied[0]["text"], "🤔")
        self.assertEqual(
            actions,
            [{"type": "edit_message", "inline_message_id": "inline-abc", "text": "答", "parse_mode": "html", "send_via": ["interaction_bot"]}],
        )

    def test_guest_without_inline_edit_support_answers_once(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(ai=CapAI(text="答"), messages=LiveMessages(inline=False))
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(ctx.messages.applied, [])
        self.assertEqual(actions[0]["type"], "answer_guest_query")

    def test_errors_edit_the_placeholder(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(ai=CapAI(exc=RuntimeError("401 unauthorized")))
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions[0]["type"], "edit_message")
        self.assertEqual(actions[0]["message_id"], 901)
        self.assertEqual(actions[0]["text"], "AI 暂时不可用，请稍后再试。")

    def test_empty_error_template_deletes_placeholder(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"error_ai_template": ""}, ai=CapAI(exc=RuntimeError("boom")))
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions, [{"type": "delete_message", "chat_id": 601, "message_id": 901, "send_via": ["interaction_bot"]}])

    def test_guest_error_with_empty_template_still_edits_inline_placeholder(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"error_ai_template": ""}, ai=CapAI(exc=RuntimeError("boom")))
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(actions[0]["inline_message_id"], "inline-abc")
        self.assertEqual(actions[0]["text"], "AI 暂时不可用，请稍后再试。")

    def test_unexpected_exception_after_placeholder_does_not_leave_thinking(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(ai=CapAI(text="答"))

        async def boom(*args, **kwargs):
            raise ValueError("bug")

        plugin._load_history = boom  # type: ignore[method-assign]
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        # 占位已发出后流程异常：编辑占位为错误提示，不留孤零零的 🤔。
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "edit_message")
        self.assertEqual(actions[0]["message_id"], 901)
        self.assertIn("AI 暂时不可用", actions[0]["text"])

    def test_placeholder_failure_falls_back_to_single_send(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(ai=CapAI(text="答"), messages=LiveMessages(fail=True))
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions[0]["type"], "send_message")
        self.assertTrue(any("占位" in item[1] for item in ctx.logs))

    def test_placeholder_can_be_disabled_and_customized(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"thinking_placeholder": False}, ai=CapAI(text="答"))
        run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(ctx.messages.applied, [])
        ctx = live_ctx(config={"thinking_template": "<b>{user}</b> 稍等…"}, ai=CapAI(text="答"))
        payload = message_payload("问", chat_id=601, chat_type="private")
        payload["sender"]["display_name"] = "张<三>"
        run(plugin.on_event(ctx, payload))
        self.assertEqual(ctx.messages.applied[0]["text"], "<b>张&lt;三&gt;</b> 稍等…")

    def test_help_reset_and_limits_do_not_send_placeholder(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = live_ctx(config={"per_user_daily_limit": 1})
        run(plugin.on_event(ctx, message_payload("", chat_id=601, chat_type="private")))
        run(plugin.on_event(ctx, message_payload("重置", chat_id=601, chat_type="private")))
        self.assertEqual(ctx.messages.applied, [])
        run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(len(ctx.messages.applied), 1)
        actions = run(plugin.on_event(ctx, message_payload("再问", chat_id=601, chat_type="private")))
        self.assertEqual(len(ctx.messages.applied), 1)
        self.assertEqual(actions[0]["type"], "send_message")
        self.assertIn("每日 1 次", actions[0]["text"])


class TemplateTest(_StubbedTestCase):
    def test_render_template_is_safe(self) -> None:
        out = PLUGIN.render_template("<b>{answer}</b> {unknown} {model} <x> & {", {"answer": "<i>a</i>", "model": "m"})
        self.assertEqual(out, "<b><i>a</i></b> {unknown} m &lt;x&gt; &amp; {")

    def test_answer_template_with_sources_and_model(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="答", sources=[{"url": "https://a.com/x?y=1&z=2", "title": "A<站>"}, {"url": "javascript:1", "title": "坏"}])
        ctx = Ctx(config={"answer_template": "{answer}{sources}\n<i>by {model} @ {provider}</i>"}, ai=ai)
        text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
        self.assertIn('1. <a href="https://a.com/x?y=1&amp;z=2">A&lt;站&gt;</a>', text)
        self.assertNotIn("javascript", text)
        self.assertTrue(text.endswith("<i>by gpt-x @ OpenAI</i>"))

    def test_answer_template_without_answer_placeholder_appends_answer(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"answer_template": "前言"}, ai=CapAI(text="正文"))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "前言\n\n正文")

    def test_every_error_template_is_configurable(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        cases = [
            ({"error_ai_template": "失败:{reason}"}, CapAI(exc=RuntimeError("429 too many")), "问", "失败:请求过于频繁"),
            ({"error_empty_template": "空"}, CapAI(text="  "), "问", "空"),
            ({"input_too_long_template": "太长>{limit}", "max_input_chars": 20}, CapAI(), "长" * 30, "太长&gt;20"),
            ({"guarded_template": "<b>拦截</b>"}, CapAI(text="/ban x"), "问", "<b>拦截</b>"),
            ({"deny_template": "无权 {user}", "access_mode": "仅白名单用户"}, CapAI(), "问", "无权 访客"),
            ({"help_template": "帮助"}, CapAI(), "", "帮助"),
            ({"reset_template": "已重置"}, CapAI(), "重置", "已重置"),
        ]
        for config, ai, question, expected in cases:
            ctx = Ctx(config=config, ai=ai)
            actions = run(plugin.on_event(ctx, guest_payload(question)))
            self.assertEqual(actions[0]["text"], expected, config)

    def test_limit_templates(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"per_user_daily_limit": 1, "limit_user_template": "用户上限 {limit}"})
        run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "用户上限 1")
        ctx = Ctx(config={"global_daily_limit": 1, "per_user_daily_limit": 0, "limit_global_template": "全局上限 {limit}"})
        run(plugin.on_event(ctx, guest_payload("问", user_id=1)))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问", user_id=2)))[0]["text"], "全局上限 1")

    def test_timeout_template(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"handler_timeout_seconds": 10, "error_timeout_template": "超时 {timeout}s"}, ai=FakeAI(delay=30))
        original = PLUGIN.asyncio.wait_for

        async def fast_wait_for(awaitable, timeout):
            return await original(awaitable, timeout=0.05)

        PLUGIN.asyncio.wait_for = fast_wait_for
        try:
            actions = run(plugin.on_event(ctx, guest_payload("你好")))
        finally:
            PLUGIN.asyncio.wait_for = original
        self.assertEqual(actions[0]["text"], "超时 10s")

    def test_legacy_deny_reply_is_still_honored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"access_mode": "whitelist", "deny_reply": "旧提示"})
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "旧提示")

    def test_manifest_lists_all_templates_with_defaults(self) -> None:
        props = PACKAGE.MANIFEST.config_schema["properties"]
        defaults = sys.modules[f"{PACKAGE.__name__}.manifest"].DEFAULT_TEMPLATES
        for key, default in defaults.items():
            self.assertTrue(key.endswith("_template"), key)
            self.assertEqual(props[key]["default"], default)
            self.assertEqual(props[key]["x-ui-widget"], "textarea")
            self.assertIn("可用", props[key]["description"])
        self.assertEqual(props["thinking_template"]["default"], "🤔")
        self.assertTrue(props["template_placeholders"]["readOnly"])
        for name in ("{answer}", "{sources}", "{model}", "{provider}", "{notices}", "{user}", "{limit}", "{timeout}", "{reason}"):
            self.assertIn(name, props["template_placeholders"]["default"])
        orders = [props[key]["x-ui-order"] for key in defaults]
        self.assertEqual(orders, sorted(orders))
        self.assertGreater(min(orders), props["blocked_bare_outputs"]["x-ui-order"])


class CapabilityTest(_StubbedTestCase):
    def test_web_search_is_requested_and_sources_are_attached(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="今天新闻", sources=[{"url": "https://news.example/1", "title": "新闻"}])
        ctx = Ctx(config={"enable_web_search": True}, ai=ai)
        text = run(plugin.on_event(ctx, guest_payload("今天有什么新闻")))[0]["text"]
        self.assertTrue(ai.calls[0]["web_search"])
        self.assertIn("<b>来源</b>", text)

    def test_web_search_unsupported_degrades_with_notice(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="旧答案", unsupported={"web_search"})
        ctx = Ctx(config={"enable_web_search": True}, ai=ai)
        text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
        self.assertEqual(len(ai.calls), 2)
        self.assertFalse(ai.calls[1]["web_search"])
        self.assertTrue(text.startswith("<i>当前模型不支持联网搜索"))
        self.assertTrue(text.endswith("旧答案"))

    def test_old_platform_without_web_search_param_degrades(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"enable_web_search": True, "search_unavailable_template": ""}, ai=FakeAI(text="答"))
        self.assertEqual(run(plugin.on_event(ctx, guest_payload("问")))[0]["text"], "答")
        self.assertNotIn("web_search", ctx.ai.calls[0])

    def test_image_message_is_sent_to_model(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="这是一只猫")
        ctx = Ctx(ai=ai)
        payload = message_payload("", chat_id=601, chat_type="private")
        payload["message"]["media"] = {"type": "photo", "file_id": "photo-1", "file_size": 1000}
        actions = run(plugin.on_event(ctx, payload))
        self.assertEqual(ai.fetched, ["photo-1"])
        self.assertEqual(ai.calls[0]["images"], [b"\xff\xd8jpeg"])
        self.assertIn("图片", ai.calls[0]["user"])
        self.assertEqual(actions[0]["text"], "这是一只猫")

    def test_replied_image_is_used_in_group(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="图里是表格")
        ctx = Ctx(ai=ai)
        payload = message_payload("@helper_bot 这是什么", reply_username="someone", reply_text="")
        payload["bot"] = {"id": 999, "username": "helper_bot"}
        payload["reply_to"]["media"] = {"type": "document", "mime_type": "image/png", "file_id": "doc-1"}
        run(plugin.on_event(ctx, payload))
        self.assertEqual(ai.fetched, ["doc-1"])

    def test_vision_unsupported_model_degrades_to_text(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="只看文字的回答", unsupported={"images"})
        ctx = Ctx(ai=ai)
        payload = message_payload("图里写了什么", chat_id=601, chat_type="private")
        payload["message"]["media"] = {"type": "photo", "file_id": "photo-1"}
        text = run(plugin.on_event(ctx, payload))[0]["text"]
        self.assertEqual(len(ai.calls), 2)
        self.assertIsNone(ai.calls[1]["images"])
        self.assertNotIn("用户附带了图片", ai.calls[1]["user"])
        self.assertIn("暂不支持识图", text)

    def test_image_only_without_vision_replies_template(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="x", vision=False)
        ctx = Ctx(ai=ai)
        payload = message_payload("", chat_id=601, chat_type="private")
        payload["message"]["media"] = {"type": "photo", "file_id": "photo-1"}
        actions = run(plugin.on_event(ctx, payload))
        self.assertEqual(ai.calls, [])
        self.assertIn("暂不支持识图", actions[0]["text"])
        self.assertFalse(any(v for k, v in ctx.storage.data.items() if k.startswith("quota:")))

    def test_vision_can_be_disabled(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="x")
        ctx = Ctx(config={"enable_vision": False}, ai=ai)
        payload = message_payload("看图", chat_id=601, chat_type="private")
        payload["message"]["media"] = {"type": "photo", "file_id": "photo-1"}
        run(plugin.on_event(ctx, payload))
        self.assertEqual(ai.fetched, [])
        self.assertIsNone(ai.calls[0]["images"])

    def test_non_image_media_is_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="x")
        ctx = Ctx(ai=ai)
        payload = message_payload("看", chat_id=601, chat_type="private")
        payload["message"]["media"] = {"type": "document", "mime_type": "application/pdf", "file_id": "pdf-1"}
        run(plugin.on_event(ctx, payload))
        self.assertEqual(ai.fetched, [])


class TriggerDiagnosticsTest(_StubbedTestCase):
    def test_platform_bot_identity_enables_group_mentions_without_config(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        payload = message_payload("@Helper_Bot 你好")
        payload["bot"] = {"id": 999, "username": "helper_bot"}
        actions = run(plugin.on_event(ctx, payload))
        self.assertEqual(actions[0]["type"], "send_message")
        self.assertNotIn("@Helper_Bot", ctx.ai.calls[0]["user"])

    def test_reply_to_bot_detected_by_bot_id(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        payload = message_payload("继续")
        payload["bot"] = {"id": 999, "username": "helper_bot"}
        payload["reply_to"] = {"message_id": 80, "user_id": 999, "text": "上一轮"}
        self.assertEqual(run(plugin.on_event(ctx, payload))[0]["type"], "send_message")

    def test_unidentifiable_group_mention_is_logged(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        self.assertEqual(run(plugin.on_event(ctx, message_payload("@helper_bot 你好"))), [])
        self.assertTrue(any("Bot 身份" in item[1] for item in ctx.logs))

    def test_skips_are_logged_with_reason(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        cases = [
            ({"chat_blacklist": "-100555"}, "黑名单"),
            ({"chat_whitelist": "1"}, "不在白名单"),
            ({"access_mode": "whitelist", "deny_template": ""}, "白名单用户"),
            ({"enable_guest": False}, "访客模式触发已关闭"),
        ]
        for config, needle in cases:
            ctx = Ctx(config=config)
            self.assertEqual(run(plugin.on_event(ctx, guest_payload("问"))), [], config)
            self.assertTrue(any(needle in item[1] for item in ctx.logs), (config, ctx.logs))

    def test_success_is_logged(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        run(plugin.on_event(ctx, guest_payload("问")))
        self.assertTrue(any(item[1] == "AI 答疑已回答" for item in ctx.logs))

    def test_strip_thinking_only_affects_content(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"strip_thinking": False}, ai=FakeAI(text="<think>想</think>答"))
        text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
        # 关闭后思考内容按原文（已转义）显示，不会被当作 HTML 标签。
        self.assertEqual(text, "&lt;think&gt;想&lt;/think&gt;答")

    def test_markdown_wrapped_command_is_still_blocked(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        for output in ("**/ban x**", "- /kick y", "> +100", "`。hb 1`"):
            ctx = Ctx(ai=FakeAI(text=output))
            text = run(plugin.on_event(ctx, guest_payload("问")))[0]["text"]
            self.assertIn(text, (PLUGIN.COMMAND_REFUSAL, "-66666"), output)

    def test_model_select_only_model_and_provider_are_forwarded(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"telepilot_provider": "3", "telepilot_model": "deepseek-chat"})
        run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(ctx.ai.calls[0]["provider"], "3")
        self.assertEqual(ctx.ai.calls[0]["model"], "deepseek-chat")
        ctx = Ctx(config={"telepilot_provider": "", "telepilot_model": "orphan"})
        run(plugin.on_event(ctx, guest_payload("问")))
        self.assertNotIn("model", ctx.ai.calls[0])


if __name__ == "__main__":
    unittest.main()


class KeywordTriggerTest(_StubbedTestCase):
    """群里以/含关键词的消息不必 @Bot 也触发；私聊与访客不受影响。"""

    def _ask(self, text, config=None, **payload_kwargs):
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"trigger_keywords": "AI, 问问\n小助手", **(config or {})})
        actions = run(plugin.on_event(ctx, message_payload(text, **payload_kwargs)))
        return ctx, actions

    def test_keyword_list_parsing(self) -> None:
        self.assertEqual(PLUGIN._parse_keywords("AI, 问问\n小助手，ai"), ["AI", "问问", "小助手"])
        self.assertEqual(PLUGIN._parse_keywords(["AI", " ", "问问"]), ["AI", "问问"])
        self.assertEqual(PLUGIN._parse_keywords(""), [])

    def test_prefix_keyword_triggers_and_strips_keyword(self) -> None:
        ctx, actions = self._ask("AI，今天几号？")
        self.assertEqual(len(ctx.ai.calls), 1)
        self.assertIn("今天几号？", ctx.ai.calls[0]["user"])
        self.assertNotIn("AI，今天几号", ctx.ai.calls[0]["user"])
        self.assertEqual(actions[0]["reply_to_message_id"], 88)

    def test_keyword_can_be_kept_in_question(self) -> None:
        ctx, _ = self._ask("问问 天气怎么样", config={"strip_keyword": False})
        self.assertIn("问问 天气怎么样", ctx.ai.calls[0]["user"])

    def test_prefix_mode_ignores_keyword_in_middle(self) -> None:
        ctx, actions = self._ask("我想问问天气")
        self.assertEqual((actions, ctx.ai.calls), ([], []))

    def test_contains_mode_matches_anywhere(self) -> None:
        ctx, _ = self._ask("我想问问天气怎么样", config={"keyword_match_mode": "包含"})
        self.assertEqual(len(ctx.ai.calls), 1)
        self.assertIn("我想 天气怎么样", ctx.ai.calls[0]["user"])

    def test_keyword_without_question_is_ignored(self) -> None:
        for text in ("AI", "AI？", "小助手 ！"):
            ctx, actions = self._ask(text)
            self.assertEqual((actions, ctx.ai.calls), ([], []), text)

    def test_ascii_keyword_needs_word_boundary(self) -> None:
        for text in ("AIM 是什么", "aim high"):
            ctx, _ = self._ask(text)
            self.assertEqual(ctx.ai.calls, [], text)
        ctx, _ = self._ask("ai 是什么")  # 大小写不敏感
        self.assertEqual(len(ctx.ai.calls), 1)

    def test_commands_and_bot_senders_are_ignored(self) -> None:
        ctx, _ = self._ask("/AI 今天几号")
        self.assertEqual(ctx.ai.calls, [])
        plugin = PLUGIN.InteractionAIChatPlugin()
        for sender in ({"user_id": 999, "is_bot": True}, {"user_id": 700, "username": "other_bot"}, {"user_id": 4242}):
            ctx = Ctx(config={"trigger_keywords": "AI"})
            payload = message_payload("AI 今天几号")
            payload["sender"] = sender
            payload["bot"] = {"id": 4242, "username": "helper_bot"}
            self.assertEqual(run(plugin.on_event(ctx, payload)), [], sender)
            self.assertEqual(ctx.ai.calls, [], sender)

    def test_keyword_trigger_still_respects_chat_whitelist_and_limits(self) -> None:
        ctx, actions = self._ask("AI 你好", config={"chat_whitelist": "-100999"})
        self.assertEqual((actions, ctx.ai.calls), ([], []))
        ctx, _ = self._ask("AI 你好", config={"per_user_daily_limit": 0, "global_daily_limit": 0})
        self.assertEqual(len(ctx.ai.calls), 1)  # 0 = 不限制

    def test_keywords_empty_means_disabled(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx()
        self.assertEqual(run(plugin.on_event(ctx, message_payload("AI 今天几号"))), [])
        self.assertEqual(ctx.ai.calls, [])

    def test_private_chat_unaffected(self) -> None:
        ctx, _ = self._ask("AI 今天几号", chat_id=601, chat_type="private")
        self.assertIn("AI 今天几号", ctx.ai.calls[0]["user"])  # 私聊原样提问，不去关键词

    def test_privacy_mode_warning_is_logged_once(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"trigger_keywords": "AI"})
        for _ in range(2):
            payload = message_payload("随便聊聊")
            payload["bot"] = {"id": 4242, "username": "helper_bot", "can_read_all_group_messages": False}
            run(plugin.on_event(ctx, payload))
        warnings = [log for log in ctx.logs if log[0] == "warning" and "隐私模式" in log[1]]
        self.assertEqual(len(warnings), 1)
        self.assertIn("/setprivacy", warnings[0][1])
        # 没填关键词、或可读全部消息时不警告
        for config, flag in (({}, False), ({"trigger_keywords": "AI"}, True)):
            plugin = PLUGIN.InteractionAIChatPlugin()
            ctx = Ctx(config=config)
            payload = message_payload("随便聊聊")
            payload["bot"] = {"id": 4242, "username": "helper_bot", "can_read_all_group_messages": flag}
            run(plugin.on_event(ctx, payload))
            self.assertFalse([log for log in ctx.logs if "隐私模式" in log[1]])


class TimeAndSearchPromptTest(_StubbedTestCase):
    def test_system_prompt_contains_current_date_with_weekday(self) -> None:
        from datetime import datetime, timedelta, timezone

        fixed = datetime(2026, 10, 25, 23, 6, tzinfo=timezone(timedelta(hours=8)))
        prompt = PLUGIN.build_system_prompt({"rich_text": True}, now=fixed)
        self.assertIn("2026-10-25", prompt)
        self.assertIn("星期", prompt)
        self.assertIn("服务器时区", prompt)

    def test_web_search_on_by_default_with_guidance_not_forced(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="答")
        ctx = Ctx(config={}, ai=ai)
        del ctx.config["enable_web_search"]  # 使用插件默认值
        run(plugin.on_event(ctx, guest_payload("今天上海天气？")))
        call = ai.calls[0]
        self.assertTrue(call["web_search"])
        self.assertIn("web_search", call["system"])
        self.assertIn("闲聊", call["system"])
        self.assertNotIn("tool_choice", call)  # 由模型自行决定（auto）
        self.assertIn("当前时间", call["system"])

    def test_guidance_only_when_search_enabled(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="答")
        run(plugin.on_event(Ctx(config={"enable_web_search": False}, ai=ai), guest_payload("你好")))
        self.assertNotIn("【联网搜索】", ai.calls[0]["system"])

    def test_subscriptions_declare_platform_timeout(self) -> None:
        for sub in PACKAGE.MANIFEST.event_subscriptions:
            self.assertEqual(sub["timeout_seconds"], 90)
        self.assertLess(PLUGIN.MAX_HANDLER_TIMEOUT_SECONDS, PLUGIN.PLATFORM_TIMEOUT_SECONDS)

    def test_legacy_timeout_seconds_is_ignored(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = Ctx(config={"timeout_seconds": 12})
        run(plugin.on_event(ctx, guest_payload("你好")))
        self.assertEqual(ctx.ai.calls[0]["timeout_seconds"], 45)
