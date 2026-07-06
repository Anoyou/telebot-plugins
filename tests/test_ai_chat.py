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
    def __init__(self, text: str = "模型回答") -> None:
        self.calls: list[dict] = []
        self.text = text

    async def complete(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, **kwargs}
        )
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


if __name__ == "__main__":
    unittest.main()
