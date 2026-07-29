from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginContext:
    def __init__(self, *, config=None, log=None):
        self.config = config or {}
        self.log = log


class FakeEvent:
    chat_id = -100123

    def __init__(self) -> None:
        self.replies: list[dict] = []

    async def reply(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})
        return types.SimpleNamespace(id=len(self.replies))


def _install_app_stubs() -> None:
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    def register(cls):
        return cls

    def current_command_prefix(*, fallback=None):
        return "。"

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        return str(fallback_id) if fallback_id not in (None, "") else default

    command_module.current_command_prefix = current_command_prefix
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name
    sys.modules["app"] = app_module
    sys.modules["app.worker"] = worker_module
    sys.modules["app.worker.command"] = command_module
    sys.modules["app.worker.plugins"] = plugins_module
    sys.modules["app.worker.plugins.base"] = base_module


def _load_plugin(plugin_key: str):
    _install_app_stubs()
    module_name = f"{plugin_key}_prefix_under_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / plugin_key / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class CommandPrefixExamplesTest(unittest.TestCase):
    def test_missing_prize_prompts_use_live_command_prefix(self) -> None:
        cases = [
            ("guess_number", "GuessNumberPlugin", "guess"),
            ("idiom_chain", "IdiomChainPlugin", "cy"),
            ("poetry_blank", "PoetryBlankPlugin", "poetry"),
        ]

        async def scenario() -> None:
            for plugin_key, class_name, command in cases:
                module = _load_plugin(plugin_key)
                plugin = getattr(module, class_name)()
                event = FakeEvent()
                await plugin.on_startup(PluginContext(config={"command": command}))

                await plugin._cmd_handler(None, event, [], 1, PluginContext())

                self.assertEqual(len(event.replies), 1, plugin_key)
                self.assertIn(f"例如：。{command} 100", event.replies[0]["text"])
                self.assertNotIn(f",{command}", event.replies[0]["text"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
