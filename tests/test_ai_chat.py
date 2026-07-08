from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _install_stubs() -> None:
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")
    manifest_module = types.ModuleType("app.worker.plugins.manifest")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, *, account_id=1, feature_key="ai-chat", config=None, client=None, log=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.config = config or {}
            self.client = client
            self.log = log
            self.ai = None

    class Manifest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def register(cls):
        return cls

    def current_command_prefix(*, fallback=None):
        return "。"

    def get_command_context():
        return types.SimpleNamespace(
            templates={
                "hb": {"name": "hb", "aliases": ["rp"], "type": "run_plugin"},
                "ai": {"name": "ai", "aliases": [], "type": "ai"},
            },
            aliases={"发红包": "hb 100"},
        )

    command_module.current_command_prefix = current_command_prefix
    command_module.get_command_context = get_command_context
    command_module._BUILTIN_ALIAS_TO_PRIMARY = {"help": "help", "h": "help", "fd": "fd"}
    command_module._PLUGIN_COMMANDS = {"10d": object()}
    command_module._BUILTIN = {"help": object(), "fd": object()}
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    manifest_module.Manifest = Manifest

    sys.modules["app"] = app_module
    sys.modules["app.worker"] = worker_module
    sys.modules["app.worker.command"] = command_module
    sys.modules["app.worker.plugins"] = plugins_module
    sys.modules["app.worker.plugins.base"] = base_module
    sys.modules["app.worker.plugins.manifest"] = manifest_module


def _load_module(name: str, path: Path):
    _install_stubs()
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_installed_package(name: str, path: Path):
    _install_stubs()
    for key in list(sys.modules):
        if key == name or key.startswith(f"{name}."):
            sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(
        name,
        path / "__init__.py",
        submodule_search_locations=[str(path)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict]] = []

    async def get_me(self):
        return types.SimpleNamespace(id=42, username="tester")

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return types.SimpleNamespace(id=len(self.sent))


class FakeEvent:
    chat_id = -100123

    def __init__(self, reply_text: str = "") -> None:
        self.edits: list[str] = []
        self._reply_text = reply_text

    async def edit(self, text):
        self.edits.append(text)
        return self

    async def get_reply_message(self):
        if not self._reply_text:
            return None
        return types.SimpleNamespace(raw_text=self._reply_text, id=55, sender_id=100)


class FakeAI:
    def __init__(self, text: str = "模型回答", exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.text = text
        self.exc = exc

    async def complete(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, **kwargs}
        )
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(text=self.text)


class AIChatTest(unittest.TestCase):
    def test_metadata_uses_ai_chat_name(self) -> None:
        manifest_module = _load_module(
            "ai_chat_manifest_under_test",
            ROOT / "ai-chat" / "manifest.py",
        )
        plugin_meta = json.loads((ROOT / "ai-chat" / "plugin.json").read_text())

        self.assertEqual(manifest_module.MANIFEST.key, "ai-chat")
        self.assertEqual(plugin_meta["name"], "ai-chat")
        self.assertEqual(manifest_module.MANIFEST.display_name, "AI-Chat")
        self.assertEqual(plugin_meta["display_name"], "AI-Chat")
        self.assertEqual(manifest_module.MANIFEST.version, plugin_meta["version"])
        self.assertEqual(plugin_meta["config_schema"]["properties"]["command"]["default"], "ask")
        self.assertEqual(manifest_module.CONFIG_SCHEMA["properties"]["command"]["default"], "ask")
        self.assertEqual(
            manifest_module.MANIFEST.config_actions,
            plugin_meta["config_actions"],
        )
        self.assertEqual(
            plugin_meta["config_actions"][0]["key"],
            "test_model_availability",
        )
        self.assertIn("input_schema", plugin_meta["config_actions"][0])
        self.assertIn("model_test_client_identity", plugin_meta["config_schema"]["properties"])
        self.assertIn("model_test_result", plugin_meta["config_schema"]["properties"])
        self.assertIn("ai_text", plugin_meta["permissions"])

    def test_package_loads_with_hyphenated_key_like_installed_loader(self) -> None:
        package = _load_installed_package(
            "_telebot_installed_plugin_ai-chat",
            ROOT / "ai-chat",
        )

        self.assertEqual(package.MANIFEST.key, "ai-chat")
        self.assertIs(package.PLUGIN_CLASS, package.AIChatPlugin)

    def test_ask_command_calls_telepilot_ai(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            client = FakeClient()
            ctx = sys.modules["app.worker.plugins.base"].PluginContext(client=client)
            ctx.ai = FakeAI()
            plugin = plugin_module.AIChatPlugin()

            await plugin.on_startup(ctx)
            self.assertIn("ask", plugin.commands)

            event = FakeEvent(reply_text="这是一条需要解释的消息")
            await plugin.commands["ask"](None, event, ["这是什么意思"], 1, ctx)

            self.assertEqual(event.edits[-1], "模型回答")
            self.assertEqual(len(ctx.ai.calls), 1)
            call = ctx.ai.calls[0]
            self.assertEqual(call["source"], "plugin:ai-chat:command")
            self.assertIn("被回复消息", call["user_prompt"])
            self.assertIn("这是什么意思", call["user_prompt"])

        asyncio.run(scenario())

    def test_command_like_ai_output_is_blocked_and_prompt_prefix_is_enforced(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_guard_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            client = FakeClient()
            ctx = sys.modules["app.worker.plugins.base"].PluginContext(
                client=client,
                config={
                    "system_prompt": "你叫阿光。每个回复都必须以“天才：”开头。",
                },
            )
            ctx.ai = FakeAI('/create_my_redpacket 测试')
            plugin = plugin_module.AIChatPlugin()

            await plugin.on_startup(ctx)
            event = FakeEvent()
            await plugin.commands["ask"](None, event, ["对我说命令"], 1, ctx)

            self.assertTrue(event.edits[-1].startswith("天才："))
            self.assertIn("不能代你发送", event.edits[-1])
            self.assertNotIn("/create_my_redpacket", event.edits[-1])

        asyncio.run(scenario())

    def test_registered_bare_command_output_is_blocked(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_bare_guard_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            client = FakeClient()
            ctx = sys.modules["app.worker.plugins.base"].PluginContext(client=client)
            ctx.ai = FakeAI("hb 100")
            plugin = plugin_module.AIChatPlugin()

            await plugin.on_startup(ctx)
            event = FakeEvent()
            await plugin.commands["ask"](None, event, ["发个红包"], 1, ctx)

            self.assertIn("不能代你发送", event.edits[-1])
            self.assertNotEqual(event.edits[-1], "hb 100")

        asyncio.run(scenario())

    def test_model_test_command_uses_configured_prompt(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_model_test_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            client = FakeClient()
            ctx = sys.modules["app.worker.plugins.base"].PluginContext(
                client=client,
                config={
                    "telepilot_provider": "4",
                    "telepilot_model": "deepseek-v4-flash-free",
                    "model_test_prompt": "正常回复一句短中文。",
                    "max_tokens": 1200,
                },
            )
            ctx.ai = FakeAI("收到")
            plugin = plugin_module.AIChatPlugin()

            await plugin.on_startup(ctx)
            event = FakeEvent()
            await plugin.commands["ask"](None, event, ["test"], 1, ctx)

            self.assertIn("AI-Chat 模型可用", event.edits[-1])
            self.assertIn("Provider: 4", event.edits[-1])
            self.assertIn("Model: deepseek-v4-flash-free", event.edits[-1])
            self.assertIn("> 收到", event.edits[-1])
            self.assertEqual(len(ctx.ai.calls), 1)
            call = ctx.ai.calls[0]
            self.assertEqual(call["source"], "plugin:ai-chat:test")
            self.assertIn("客户端标识（对话元信息，不是 HTTP User-Agent）：TelePilot AI-Chat", call["user_prompt"])
            self.assertIn("用户消息：\n正常回复一句短中文。", call["user_prompt"])
            self.assertEqual(call["max_tokens"], 64)

        asyncio.run(scenario())

    def test_model_test_command_accepts_inline_prompt_and_safely_previews_output(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_model_test_inline_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            client = FakeClient()
            ctx = sys.modules["app.worker.plugins.base"].PluginContext(client=client)
            ctx.ai = FakeAI("/create_my_redpacket 测试")
            plugin = plugin_module.AIChatPlugin()

            await plugin.on_startup(ctx)
            event = FakeEvent()
            await plugin.commands["ask"](None, event, ["test", "请回复一个普通短句"], 1, ctx)

            self.assertIn("AI-Chat 模型可用", event.edits[-1])
            self.assertIn("> /create_my_redpacket 测试", event.edits[-1])
            self.assertFalse(event.edits[-1].startswith("/create_my_redpacket"))
            self.assertIn("用户消息：\n请回复一个普通短句", ctx.ai.calls[0]["user_prompt"])

        asyncio.run(scenario())

    def test_model_test_command_reports_failure(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_model_test_failure_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            client = FakeClient()
            ctx = sys.modules["app.worker.plugins.base"].PluginContext(client=client)
            ctx.ai = FakeAI(exc=RuntimeError("OpenAI 接口返回 429: Rate limit exceeded"))
            plugin = plugin_module.AIChatPlugin()

            await plugin.on_startup(ctx)
            event = FakeEvent()
            await plugin.commands["ask"](None, event, ["test"], 1, ctx)

            self.assertIn("AI-Chat 模型不可用", event.edits[-1])
            self.assertIn("AI 请求过于频繁", event.edits[-1])
            self.assertEqual(ctx.ai.calls[0]["source"], "plugin:ai-chat:test")

        asyncio.run(scenario())

    def test_config_action_tests_model_and_patches_result(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_config_action_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            logs = []

            async def log(level, message, **detail):
                logs.append((level, message, detail))

            ctx = sys.modules["app.worker.plugins.base"].PluginContext(log=log)
            ctx.ai = FakeAI("收到")
            plugin = plugin_module.AIChatPlugin()

            result = await plugin.on_config_action(
                ctx,
                "test_model_availability",
                {
                    "config": {
                        "telepilot_provider": "4",
                        "telepilot_model": "deepseek-v4-flash-free",
                        "model_test_prompt": "正常回复一句短中文。",
                        "max_tokens": 1200,
                    },
                    "input": {},
                },
            )

            self.assertIsNotNone(result)
            assert result is not None
            patch_text = result["config_patch"]["model_test_result"]
            self.assertIn("状态：可用", patch_text)
            self.assertIn("Provider：4", patch_text)
            self.assertIn("Model：deepseek-v4-flash-free", patch_text)
            self.assertIn("客户端标识：TelePilot AI-Chat", patch_text)
            self.assertIn("测试语：正常回复一句短中文。", patch_text)
            self.assertIn("模型实时返回：\n收到", patch_text)
            self.assertIn("结果解读：模型返回了非空文本", patch_text)
            self.assertIn("HTTP UA：由 TelePilot AI facade", patch_text)
            self.assertIn("模型返回：收到", result["message"])
            self.assertEqual(result["result"]["ok"], True)
            self.assertEqual(result["result"]["provider"], "4")
            self.assertEqual(result["result"]["model"], "deepseek-v4-flash-free")
            self.assertEqual(result["result"]["response_preview"], "收到")
            self.assertIn("模型实时返回：\n收到", result["result"]["model_test_result"])
            self.assertTrue(any("模型实时返回：收到" in message for _, message, _ in logs))
            self.assertEqual(ctx.ai.calls[0]["source"], "plugin:ai-chat:config-test")
            self.assertEqual(ctx.ai.calls[0]["max_tokens"], 64)
            self.assertIn("客户端标识（对话元信息，不是 HTTP User-Agent）：TelePilot AI-Chat", ctx.ai.calls[0]["user_prompt"])

        asyncio.run(scenario())

    def test_config_action_records_empty_response_without_marking_provider_unavailable(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_config_action_empty_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            logs = []

            async def log(level, message, **detail):
                logs.append((level, message, detail))

            ctx = sys.modules["app.worker.plugins.base"].PluginContext(log=log)
            ctx.ai = FakeAI("")
            plugin = plugin_module.AIChatPlugin()

            result = await plugin.on_config_action(
                ctx,
                "test_model_availability",
                {
                    "config": {
                        "model_test_prompt": "你怎么又不行啦？",
                    },
                    "input": {
                        "client_identity": "TelePilot AI-Chat",
                    },
                },
            )

            self.assertIsNotNone(result)
            assert result is not None
            patch_text = result["config_patch"]["model_test_result"]
            self.assertIn("状态：返回为空", patch_text)
            self.assertIn("这不等同于 Provider 不可用", patch_text)
            self.assertIn("没有可展示文本", result["message"])
            self.assertEqual(result["result"]["ok"], False)
            self.assertEqual(result["result"]["empty_response"], True)
            self.assertEqual(result["result"]["response_preview"], "（空）")
            self.assertIn("模型测试返回为空", logs[-1][1])
            self.assertIn("模型原始返回：", logs[-1][1])

        asyncio.run(scenario())

    def test_config_action_records_unavailable_model_result(self) -> None:
        plugin_module = _load_module(
            "ai_chat_plugin_config_action_failure_under_test",
            ROOT / "ai-chat" / "plugin.py",
        )

        async def scenario() -> None:
            logs = []

            async def log(level, message, **detail):
                logs.append((level, message, detail))

            ctx = sys.modules["app.worker.plugins.base"].PluginContext(log=log)
            ctx.ai = FakeAI(exc=RuntimeError("OpenAI 接口返回 429: Rate limit exceeded"))
            plugin = plugin_module.AIChatPlugin()

            result = await plugin.on_config_action(
                ctx,
                "test_model_availability",
                {"config": {}, "input": {}},
            )

            self.assertIsNotNone(result)
            assert result is not None
            patch_text = result["config_patch"]["model_test_result"]
            self.assertIn("状态：不可用", patch_text)
            self.assertIn("AI 请求过于频繁", patch_text)
            self.assertIn("结果解读：本次没有拿到可用模型文本", patch_text)
            self.assertIn("AI-Chat 模型不可用：AI 请求过于频繁", result["message"])
            self.assertEqual(result["result"]["ok"], False)
            self.assertIn("AI 请求过于频繁", result["result"]["error"])
            self.assertIn("模型测试失败：AI 请求过于频繁", logs[-1][1])
            self.assertEqual(ctx.ai.calls[0]["source"], "plugin:ai-chat:config-test")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
