from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        pass

    def register(cls):
        return cls

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module

    spec = importlib.util.spec_from_file_location(
        "private_chat_assistant_plugin_under_test",
        ROOT / "private_chat_assistant" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin_module = _load_plugin_module()


class FakeClient:
    def __init__(self) -> None:
        self.me = types.SimpleNamespace(
            id=2, first_name="李四", last_name="", username="lisi"
        )
        self.entities = {
            1682400007: types.SimpleNamespace(
                id=1682400007,
                first_name="张三",
                last_name="",
                username="zhangsan",
            )
        }

    async def get_me(self):
        return self.me

    async def get_entity(self, user_id: int):
        return self.entities[user_id]


class FakeStorage:
    available = True

    def __init__(self, values):
        self.values = values
        self.deleted = []

    async def get_all(self):
        return dict(self.values)

    async def delete(self, key: str):
        self.deleted.append(key)


def make_context():
    return types.SimpleNamespace(
        account_id=2,
        account_config={},
        client=FakeClient(),
        config={
            "bot_token": "999:test-token",
            "recipient_chat_id": 42,
            "aggregation_seconds": 60,
        },
        scheduler=None,
        storage=None,
        http=None,
        log=None,
    )


def message_payload(sender_id: int, text: str = "测试"):
    return {
        "source": {"channel": "userbot", "type": "message"},
        "chat": {"id": sender_id, "type": "private"},
        "sender": {"user_id": sender_id},
        "message": {"chat_id": sender_id, "text": text},
    }


class PrivateChatAssistantIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_monitored_account_and_sender_identity(self):
        plugin = plugin_module.PrivateChatAssistantPlugin()
        ctx = make_context()

        await plugin._resolve_monitored_account(ctx)
        sender_id, display_name, username = await plugin._resolve_sender_info(
            ctx, message_payload(1682400007)
        )

        self.assertEqual(plugin._monitored_account_id, 2)
        self.assertEqual(plugin._monitored_account_name, "李四（@lisi）")
        self.assertEqual((sender_id, display_name, username), (1682400007, "张三", "zhangsan"))

    async def test_ignores_message_sent_by_monitored_account_itself(self):
        plugin = plugin_module.PrivateChatAssistantPlugin()
        ctx = make_context()
        await plugin._resolve_monitored_account(ctx)

        result = await plugin.on_event(ctx, message_payload(2, "/start"))

        self.assertIsNone(result)
        self.assertEqual(plugin._memory_pending, {})

    async def test_discards_restored_self_message_from_old_version(self):
        plugin = plugin_module.PrivateChatAssistantPlugin()
        ctx = make_context()
        ctx.storage = FakeStorage(
            {
                "pending:2": {
                    "sender_id": 2,
                    "messages": ["/start"],
                    "fire_at": "2026-08-15T00:00:00+00:00",
                }
            }
        )

        await plugin.on_startup(ctx)

        self.assertEqual(plugin._memory_pending, {})
        self.assertEqual(ctx.storage.deleted, ["pending:2"])

    async def test_renders_visible_html_and_escapes_message_content(self):
        text = plugin_module._format_notification(
            {
                "monitored_account_name": "李四（@lisi）",
                "display_name": "张三",
                "username": "zhangsan",
                "messages": ["https://test", "<测试>&内容"],
            }
        )

        self.assertIn("🔔 <b>私聊消息提醒</b>", text)
        self.assertIn("<b>李四（@lisi）</b>", text)
        self.assertIn("<b>张三（@zhangsan）</b>", text)
        self.assertIn("<blockquote>1. https://test", text)
        self.assertIn("2. &lt;测试&gt;&amp;内容</blockquote>", text)
        self.assertIn("<i>请及时查看。</i>", text)


if __name__ == "__main__":
    unittest.main()
