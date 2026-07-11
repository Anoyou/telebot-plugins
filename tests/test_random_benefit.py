from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, *, config=None, account_id: int = 1, redis=None) -> None:
            self.account_id = account_id
            self.feature_key = "random_benefit"
            self.config = config or {}
            self.redis = redis
            self.log = None

    def register(cls):
        return cls

    def current_command_prefix(*, fallback=","):
        return fallback

    command_module.current_command_prefix = current_command_prefix
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register

    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules["app.worker.command"] = command_module
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module

    spec = importlib.util.spec_from_file_location(
        "random_benefit_plugin_under_test",
        ROOT / "random_benefit" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


def _message_payload(*, text: str = "今天真不错", chat_id: int = -1001, message_id: int = 55):
    return {
        "type": "message",
        "message": {"text": text, "chat_id": chat_id, "message_id": message_id},
        "sender": {"user_id": 42, "display_name": "小明"},
    }


def _command_payload(text: str, *, chat_id: int = -1001, message_id: int = 9):
    return {
        "type": "command",
        "message": {"text": text, "chat_id": chat_id, "message_id": message_id},
        "trigger": {"command": "福利", "args": text.split()[1:]},
    }


class RandomBenefitPluginTest(unittest.TestCase):
    def _ctx(self, **overrides):
        config = {
            "command": "福利",
            "allowed_chat_ids": [-1001],
            "reply_template": "送给 {sender}: +1-6666",
            "trigger_probability": 1,
            "default_enabled": True,
        }
        config.update(overrides)
        return PluginContext(config=config)

    def test_config_schema_declares_allowed_peer_selector_and_preview(self) -> None:
        manifest = json.loads((ROOT / "random_benefit" / "plugin.json").read_text())
        properties = manifest["config_schema"]["properties"]

        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(properties["allowed_chat_ids"]["x-ui-widget"], "allowed-peer-multi-select")
        self.assertEqual(properties["allowed_chat_ids"]["items"]["type"], "integer")
        self.assertTrue(properties["template_preview"]["readOnly"])
        self.assertIn("x-usage-guide", manifest["config_schema"])

    def test_message_randomly_replies_to_allowed_active_chat(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx()
            with patch.object(plugin_module.random, "random", return_value=0):
                actions = await plugin.on_event(ctx, _message_payload())

            self.assertEqual(actions, [
                {
                    "type": "send_message",
                    "text": "送给 小明: +1-6666",
                    "parse_mode": "plain",
                    "chat_id": -1001,
                    "reply_to_message_id": 55,
                }
            ])

        asyncio.run(run_case())

    def test_off_command_pauses_current_chat(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx()

            off_actions = await plugin.on_event(ctx, _command_payload(",福利 off"))
            self.assertEqual(off_actions[0]["text"], "随机福利已暂停。")

            with patch.object(plugin_module.random, "random", return_value=0):
                actions = await plugin.on_event(ctx, _message_payload())
            self.assertEqual(actions, [])

            on_actions = await plugin.on_event(ctx, _command_payload(",福利 on"))
            self.assertEqual(on_actions[0]["text"], "随机福利已开启。")

            with patch.object(plugin_module.random, "random", return_value=0):
                actions = await plugin.on_event(ctx, _message_payload())
            self.assertEqual(len(actions), 1)

        asyncio.run(run_case())

    def test_unselected_chat_is_ignored(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx()
            with patch.object(plugin_module.random, "random", return_value=0):
                actions = await plugin.on_event(ctx, _message_payload(chat_id=-2002))
            self.assertEqual(actions, [])

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
