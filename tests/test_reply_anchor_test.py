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
    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_types = types.ModuleType("telethon.tl.types")

    class Plugin:
        pass

    class PluginContext:
        pass

    class Manifest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class PeerUser:
        def __init__(self, user_id):
            self.user_id = user_id

    class PeerChannel:
        def __init__(self, channel_id):
            self.channel_id = channel_id

    def register(cls):
        return cls

    def sanitize_public_display_name(value, *, fallback="匿名用户", **kwargs):
        return str(value or "").replace(" ", "") or fallback

    async def resolve_public_sender_identity(ctx, *, chat_id, user_id, **kwargs):
        return types.SimpleNamespace(
            display_name=kwargs.get("fallback_display_name") or "人",
            is_anonymous_admin=False,
            is_admin=user_id == 456,
            tag="管理员标签" if user_id == 456 else None,
            resolved=True,
        )

    def event_from_interaction_payload(payload):
        return types.SimpleNamespace(
            type=payload.get("event_type", "command"),
            message=types.SimpleNamespace(
                chat_id=payload["message"]["chat_id"],
                message_id=payload["message"]["message_id"],
                text=payload["message"].get("text", ""),
                reply_to_message_id=payload["message"].get("reply_to_message_id"),
            ),
        )

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = register
    base.resolve_public_sender_identity = resolve_public_sender_identity
    base.sanitize_public_display_name = sanitize_public_display_name
    events.event_from_interaction_payload = event_from_interaction_payload
    manifest.Manifest = Manifest
    telethon_types.PeerUser = PeerUser
    telethon_types.PeerChannel = PeerChannel
    sys.modules["app.worker.plugins.base"] = base
    sys.modules["app.worker.plugins.events"] = events
    sys.modules["app.worker.plugins.manifest"] = manifest
    sys.modules["telethon"] = telethon
    sys.modules["telethon.tl"] = telethon_tl
    sys.modules["telethon.tl.types"] = telethon_types
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
        self.assertEqual(manifest_module.PLUGIN_VERSION, "0.2.0")
        self.assertEqual(manifest_module.MANIFEST.min_telepilot_version, "0.70.10")
        self.assertTrue(all(entry["session_scope"] == "none" for entry in raw["interaction_entries"]))
        self.assertTrue(
            all("end_session" in entry["result_contract"]["actions"] for entry in raw["interaction_entries"])
        )

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
            self.assertEqual(actions[-1], {"type": "end_session"})

        asyncio.run(run_case())

    def test_name_command_renders_configured_template(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(
                config={
                    "name_result_template": (
                        "姓名={tg_name}｜用户名={tg_username}｜ID={tg_id}｜"
                        "管理员={is_admin}｜标签={tag}"
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
                "姓名=未获取｜用户名=未获取｜ID=123｜管理员=否｜标签=无",
            )

        asyncio.run(run_case())

    def test_invalid_name_template_falls_back_to_default(self) -> None:
        identity = types.SimpleNamespace(
            display_name="人",
            is_anonymous_admin=False,
            is_admin=False,
            tag=None,
            resolved=True,
        )
        ctx = types.SimpleNamespace(config={"name_result_template": "{display_name"})
        profile = plugin_module._TargetPublicProfile(user_id=123)

        rendered = plugin_module._identity_result_text(ctx, identity, profile)

        self.assertIn("TG 姓名：未获取", rendered)
        self.assertIn("TG ID：123", rendered)

    def test_saved_legacy_default_template_migrates_to_public_info_default(self) -> None:
        identity = types.SimpleNamespace(
            display_name="人",
            is_anonymous_admin=False,
            is_admin=False,
            tag=None,
            resolved=True,
        )
        profile = plugin_module._TargetPublicProfile(
            user_id=123,
            name="公开姓名",
            username="public_user",
            from_message=True,
        )
        ctx = types.SimpleNamespace(
            config={"name_result_template": plugin_module.LEGACY_NAME_RESULT_TEMPLATE}
        )

        rendered = plugin_module._identity_result_text(ctx, identity, profile)

        self.assertTrue(rendered.startswith("用户公开信息："))
        self.assertIn("TG 姓名：公开姓名", rendered)
        self.assertIn("TG 用户名：@public_user", rendered)

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
            self.assertEqual([action["type"] for action in actions], ["send_message", "result", "end_session"])
            self.assertIn("TG 姓名：未获取", actions[0]["text"])
            self.assertIn("TG 用户名：未获取", actions[0]["text"])
            self.assertIn("TG ID：123", actions[0]["text"])
            self.assertIn("在本群是否管理员：否", actions[0]["text"])
            self.assertNotIn("payout", [action["type"] for action in actions])
            self.assertEqual(actions[1]["result"]["target_display_name"], "人")

        asyncio.run(run_case())

    def test_name_command_uses_replied_peer_user_without_user_id(self) -> None:
        async def run_case() -> None:
            class Client:
                async def get_messages(self, chat_id, *, ids):
                    self.lookup = (chat_id, ids)
                    return types.SimpleNamespace(
                        from_id=sys.modules["telethon.tl.types"].PeerUser(456),
                        sender=types.SimpleNamespace(
                            id=456,
                            first_name="真实",
                            last_name="公开姓名",
                            username="public_user",
                        ),
                        from_rank="普通成员小尾巴",
                    )

            client = Client()
            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(config={}, client=client)
            actions = await plugin.on_interaction(
                ctx,
                plugin_module.NAME_ENTRY_KEY,
                {
                    "message": {
                        "chat_id": -1001,
                        "message_id": 91,
                        "reply_to_message_id": 77,
                        "text": "name",
                    },
                    "trigger": {"args": []},
                },
            )

            assert actions is not None
            self.assertEqual(client.lookup, (-1001, 77))
            self.assertEqual(actions[1]["result"]["target_user_id"], 456)
            self.assertEqual(actions[1]["result"]["target_display_name"], "真实公开姓名")
            self.assertNotEqual(actions[1]["result"]["target_display_name"], "public_user")
            self.assertNotEqual(actions[1]["result"]["target_display_name"], "456")
            self.assertEqual(actions[1]["result"]["target_source"], "reply_message")
            self.assertEqual(
                actions[0]["text"],
                "用户公开信息：\n"
                "TG 姓名：真实公开姓名\n"
                "TG 用户名：@public_user\n"
                "TG ID：456\n"
                "在本群是否管理员：是\n"
                "在本群的小尾巴：管理员标签",
            )

        asyncio.run(run_case())

    def test_name_template_uses_regular_member_custom_tag(self) -> None:
        identity = types.SimpleNamespace(
            user_id=789,
            display_name="普通成员",
            is_anonymous_admin=False,
            is_admin=False,
            tag=None,
            resolved=True,
        )
        profile = plugin_module._TargetPublicProfile(
            user_id=789,
            name="普通成员",
            username="member_user",
            tag="成员小尾巴",
            from_message=True,
        )

        values = plugin_module._identity_result_values(identity, profile)

        self.assertEqual(values["is_admin"], "否")
        self.assertEqual(values["tag"], "成员小尾巴")

    def test_name_template_hides_anonymous_admin_private_fields(self) -> None:
        identity = types.SimpleNamespace(
            user_id=999,
            display_name="匿名值班",
            is_anonymous_admin=True,
            is_admin=True,
            tag="匿名值班",
            resolved=True,
        )
        profile = plugin_module._TargetPublicProfile(
            user_id=999,
            name="不应公开的姓名",
            username="private_admin",
            tag="不应公开的标签",
            from_message=True,
        )

        values = plugin_module._identity_result_values(identity, profile)

        self.assertEqual(values["tg_name"], "不可公开")
        self.assertEqual(values["tg_username"], "不可公开")
        self.assertEqual(values["tg_id"], "不可公开")
        self.assertEqual(values["is_admin"], "是")
        self.assertEqual(values["tag"], "匿名值班")

    def test_name_command_rejects_replied_channel_identity(self) -> None:
        async def run_case() -> None:
            class Client:
                async def get_messages(self, chat_id, *, ids):
                    return types.SimpleNamespace(
                        from_id=sys.modules["telethon.tl.types"].PeerChannel(999)
                    )

            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(config={}, client=Client())
            actions = await plugin.on_interaction(
                ctx,
                plugin_module.NAME_ENTRY_KEY,
                {
                    "message": {
                        "chat_id": -1001,
                        "message_id": 92,
                        "reply_to_message_id": 78,
                        "text": "name",
                    },
                    "trigger": {"args": []},
                },
            )

            assert actions is not None
            self.assertEqual([action["type"] for action in actions], ["send_message", "end_session"])
            self.assertIn("回复目标用户的消息", actions[0]["text"])

        asyncio.run(run_case())

    def test_stale_session_message_closes_without_feedback(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.ReplyAnchorTestPlugin()
            ctx = types.SimpleNamespace(config={})

            actions = await plugin.on_interaction(
                ctx,
                plugin_module.NAME_ENTRY_KEY,
                {
                    "event_type": "message",
                    "message": {"chat_id": -1001, "message_id": 93, "text": "普通群消息"},
                    "trigger": {},
                },
            )

            self.assertEqual(actions, [{"type": "end_session"}])

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
