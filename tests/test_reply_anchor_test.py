from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import unittest
from pathlib import Path


def _install_telepilot_stubs() -> None:
    app = sys.modules.setdefault("app", types.ModuleType("app"))
    worker = sys.modules.setdefault("app.worker", types.ModuleType("app.worker"))
    plugins = sys.modules.setdefault("app.worker.plugins", types.ModuleType("app.worker.plugins"))
    base = types.ModuleType("app.worker.plugins.base")
    events = types.ModuleType("app.worker.plugins.events")
    manifest = types.ModuleType("app.worker.plugins.manifest")

    class Plugin:
        pass

    class PluginContext:
        pass

    class Manifest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def register(cls):
        return cls

    async def resolve_public_sender_identity(ctx, *, chat_id, user_id, **kwargs):
        return types.SimpleNamespace(
            display_name="人",
            is_anonymous_admin=False,
            tag=None,
            resolved=True,
        )

    def event_from_interaction_payload(payload):
        return types.SimpleNamespace(
            type="command",
            message=types.SimpleNamespace(
                chat_id=payload["message"]["chat_id"],
                message_id=payload["message"]["message_id"],
                text=payload["message"].get("text", ""),
            ),
        )

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = register
    base.resolve_public_sender_identity = resolve_public_sender_identity
    events.event_from_interaction_payload = event_from_interaction_payload
    manifest.Manifest = Manifest
    sys.modules["app.worker.plugins.base"] = base
    sys.modules["app.worker.plugins.events"] = events
    sys.modules["app.worker.plugins.manifest"] = manifest
    app.worker = worker
    worker.plugins = plugins


_install_telepilot_stubs()
plugin_module = importlib.import_module("reply_anchor_test.plugin")
manifest_module = importlib.import_module("reply_anchor_test.manifest")


class ReplyAnchorTestPluginTest(unittest.TestCase):
    def test_static_manifest_matches_python_manifest(self) -> None:
        raw = json.loads(
            (Path(__file__).resolve().parents[1] / "reply_anchor_test" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(raw["version"], manifest_module.PLUGIN_VERSION)
        self.assertEqual(raw["min_telepilot_version"], manifest_module.MANIFEST.min_telepilot_version)
        self.assertEqual(raw["usage"], manifest_module.USAGE)
        self.assertEqual(raw["config_schema"], manifest_module.CONFIG_SCHEMA)
        self.assertEqual(raw["event_subscriptions"], manifest_module.EVENT_SUBSCRIPTIONS)
        self.assertEqual(raw["interaction_entries"], manifest_module.INTERACTION_ENTRIES)
        self.assertEqual(manifest_module.PLUGIN_VERSION, "0.1.3")
        self.assertEqual(manifest_module.MANIFEST.min_telepilot_version, "0.70.9")

    def test_payout_contains_platform_sanitized_public_name(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(config={})
            actions = await plugin.on_interaction(
                ctx,
                plugin_module.ENTRY_KEY,
                {
                    "message": {"chat_id": -1001, "message_id": 88, "text": "send 123 66"},
                    "trigger": {"args": ["123", "66"]},
                },
            )

            self.assertIsNotNone(actions)
            assert actions is not None
            self.assertEqual(actions[0]["reply_to_display_name"], "人")
            self.assertEqual(actions[1]["result"]["target_display_name"], "人")

        asyncio.run(run_case())

    def test_name_command_renders_configured_template(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(
                config={
                    "name_result_template": (
                        "姓名={display_name}｜身份={identity_status}｜"
                        "标签={tag}｜状态={resolved_status}"
                    )
                }
            )
            actions = await plugin.on_interaction(
                ctx,
                plugin_module.NAME_ENTRY_KEY,
                {
                    "message": {"chat_id": -1001, "message_id": 90, "text": "name 123"},
                    "trigger": {"args": ["123"]},
                },
            )

            assert actions is not None
            self.assertEqual(
                actions[0]["text"],
                "姓名=人｜身份=非匿名公开身份｜标签=无｜状态=已确认",
            )

        asyncio.run(run_case())

    def test_invalid_name_template_falls_back_to_default(self) -> None:
        identity = types.SimpleNamespace(
            display_name="人",
            is_anonymous_admin=False,
            tag=None,
            resolved=True,
        )
        ctx = types.SimpleNamespace(config={"name_result_template": "{display_name"})

        rendered = plugin_module._identity_result_text(ctx, identity)

        self.assertIn("公开姓名：人", rendered)
        self.assertIn("解析状态：已确认", rendered)

    def test_name_command_returns_public_identity_without_payout(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(config={})
            actions = await plugin.on_interaction(
                ctx,
                plugin_module.NAME_ENTRY_KEY,
                {
                    "message": {"chat_id": -1001, "message_id": 89, "text": "name 123"},
                    "trigger": {"args": ["123"]},
                },
            )

            self.assertIsNotNone(actions)
            assert actions is not None
            self.assertEqual([action["type"] for action in actions], ["send_message", "result"])
            self.assertIn("公开姓名：人", actions[0]["text"])
            self.assertIn("身份状态：非匿名公开身份", actions[0]["text"])
            self.assertIn("解析状态：已确认", actions[0]["text"])
            self.assertNotIn("payout", [action["type"] for action in actions])
            self.assertEqual(actions[1]["result"]["target_display_name"], "人")

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
