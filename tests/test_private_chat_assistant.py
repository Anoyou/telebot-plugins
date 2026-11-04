from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from copy import copy
from pathlib import Path
from unittest.mock import AsyncMock


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
                bot=None,
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
        self.values.pop(key, None)

    async def get(self, key: str, default=None):
        return self.values.get(key, default)

    async def set(self, key: str, value, **kwargs):
        self.values[key] = value


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def register(self, job_id, schedule, callback, **kwargs):
        self.jobs[job_id] = (schedule, callback)

    def unregister_all(self):
        self.jobs.clear()


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
        host=types.SimpleNamespace(read=AsyncMock(return_value={"chat_id": 1682400007, "read_inbox_max_id": 0})),
    )


def message_payload(sender_id: int, text: str = "测试", message_id: int = 10):
    return {
        "source": {"channel": "userbot", "type": "message"},
        "chat": {"id": sender_id, "type": "private"},
        "sender": {"user_id": sender_id},
        "message": {"chat_id": sender_id, "text": text, "message_id": message_id},
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


class PrivateChatAssistantBotTests(unittest.IsolatedAsyncioTestCase):
    def make_case(self, *, enabled=True, is_bot=True):
        plugin = plugin_module.PrivateChatAssistantPlugin()
        ctx = make_context()
        ctx.config["skip_bot_messages"] = enabled
        ctx.scheduler = FakeScheduler()
        ctx.log = AsyncMock()
        ctx.client.entities[1682400007].bot = is_bot
        plugin._send_notification = AsyncMock(return_value=True)
        return plugin, ctx

    async def test_enabled_skips_bot_before_queuing(self):
        plugin, ctx = self.make_case()
        await plugin.on_event(ctx, message_payload(1682400007))
        self.assertEqual(plugin._memory_pending, {})
        self.assertEqual(ctx.scheduler.jobs, {})
        ctx.host.read.assert_not_awaited()
        self.assertIn("私聊对象为 Bot", ctx.log.await_args.args[1])

    async def test_disabled_and_default_preserve_bot_notifications(self):
        for default in (True, False):
            plugin, ctx = self.make_case(enabled=False)
            if default:
                ctx.config.pop("skip_bot_messages")
            await plugin.on_event(ctx, message_payload(1682400007))
            await plugin._flush_sender(ctx, 1682400007)
            plugin._send_notification.assert_awaited_once()

    async def test_normal_users_are_not_filtered_even_with_bot_like_username(self):
        for is_bot in (None, False):
            plugin, ctx = self.make_case(is_bot=is_bot)
            ctx.client.entities[1682400007].username = "human_bot"
            await plugin.on_event(ctx, message_payload(1682400007))
            await plugin._flush_sender(ctx, 1682400007)
            plugin._send_notification.assert_awaited_once()

    async def test_enabling_filter_cancels_already_queued_bot_notification(self):
        plugin, ctx = self.make_case(enabled=False)
        await plugin.on_event(ctx, message_payload(1682400007))
        ctx.config["skip_bot_messages"] = True
        await plugin._flush_sender(ctx, 1682400007)
        self.assertEqual(plugin._memory_pending, {})
        plugin._send_notification.assert_not_awaited()
        ctx.host.read.assert_not_awaited()

    async def test_enabling_filter_updates_captured_event_context_for_queued_bot(self):
        plugin, ctx = self.make_case(enabled=False)
        await plugin.on_startup(ctx)
        event_ctx = copy(ctx)
        await plugin.on_event(event_ctx, message_payload(1682400007))
        # 宿主给事件复制 ctx，并在配置热更新时替换常驻 ctx.config。
        ctx.config = {**ctx.config, "skip_bot_messages": True}
        callback = ctx.scheduler.jobs["private_digest_1682400007"][1]
        await callback(None)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(plugin._memory_pending, {})

    async def test_enabling_filter_during_read_query_blocks_pending_bot(self):
        plugin, ctx = self.make_case(enabled=False)
        await plugin.on_startup(ctx)
        await plugin.on_event(copy(ctx), message_payload(1682400007))
        async def enable_during_query(*args, **kwargs):
            ctx.config = {**ctx.config, "skip_bot_messages": True}
            return {"chat_id": 1682400007, "read_inbox_max_id": 0}
        ctx.host.read.side_effect = enable_during_query
        await plugin._flush_sender(copy(ctx), 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(plugin._memory_pending, {})

    async def test_enabling_filter_during_storage_delete_blocks_pending_bot(self):
        plugin, ctx = self.make_case(enabled=False)
        ctx.storage = FakeStorage({})
        await plugin.on_startup(ctx)
        await plugin.on_event(copy(ctx), message_payload(1682400007))
        original_delete = ctx.storage.delete
        async def enable_during_delete(key):
            ctx.config = {**ctx.config, "skip_bot_messages": True}
            await original_delete(key)
        ctx.storage.delete = enable_during_delete
        await plugin._flush_sender(copy(ctx), 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(ctx.storage.values, {})

    async def test_restored_bot_pending_is_not_notified(self):
        plugin, ctx = self.make_case()
        ctx.storage = FakeStorage({"pending:1682400007": {
            "sender_id": 1682400007, "messages": [{"id": 10, "text": "Bot 消息"}]}})
        await plugin.on_startup(ctx)
        await plugin._flush_sender(ctx, 1682400007)
        self.assertEqual(ctx.storage.values, {})
        plugin._send_notification.assert_not_awaited()

    async def test_failed_identity_lookup_preserves_notification_and_can_recover(self):
        plugin, ctx = self.make_case()
        original = ctx.client.get_entity
        ctx.client.get_entity = AsyncMock(side_effect=RuntimeError("offline"))
        await plugin.on_event(ctx, message_payload(1682400007))
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_awaited_once()
        self.assertTrue(any("无法确认" in call.args[1] for call in ctx.log.await_args_list))
        ctx.client.get_entity = original
        await plugin.on_event(ctx, message_payload(1682400007, message_id=11))
        self.assertEqual(plugin._memory_pending, {})

    async def test_bot_identity_lookup_is_cached(self):
        plugin, ctx = self.make_case()
        ctx.client.get_entity = AsyncMock(wraps=ctx.client.get_entity)
        await plugin.on_event(ctx, message_payload(1682400007))
        await plugin.on_event(ctx, message_payload(1682400007, message_id=11))
        ctx.client.get_entity.assert_awaited_once_with(1682400007)


class PrivateChatAssistantUnreadTests(unittest.IsolatedAsyncioTestCase):
    def make_case(self):
        plugin = plugin_module.PrivateChatAssistantPlugin()
        ctx = make_context()
        ctx.scheduler = FakeScheduler()
        ctx.log = AsyncMock()
        plugin._send_notification = AsyncMock(return_value=True)
        return plugin, ctx

    async def test_all_read_cancels_notification(self):
        plugin, ctx = self.make_case()
        await plugin.on_event(ctx, message_payload(1682400007))
        ctx.host.read.return_value["read_inbox_max_id"] = 10
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(plugin._memory_pending, {})
        ctx.host.read.assert_awaited_once_with("chat.read_state@1", chat_id=1682400007)
        self.assertTrue(any("已读，跳过" in call.args[1] for call in ctx.log.await_args_list))

    async def test_partially_read_sends_only_remaining_messages(self):
        plugin, ctx = self.make_case()
        await plugin.on_event(ctx, message_payload(1682400007, "已读内容", 10))
        await plugin.on_event(ctx, message_payload(1682400007, "未读 <内容>", 11))
        ctx.host.read.return_value["read_inbox_max_id"] = 10
        await plugin._flush_sender(ctx, 1682400007)
        text = plugin._send_notification.await_args.args[1]
        self.assertNotIn("已读内容", text)
        self.assertIn("未读 &lt;内容&gt;", text)
        self.assertNotIn("'id'", text)

    async def test_query_failure_keeps_pending_and_retries_without_sending(self):
        plugin, ctx = self.make_case()
        await plugin.on_event(ctx, message_payload(1682400007))
        ctx.host.read.side_effect = RuntimeError("offline")
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(plugin._memory_pending[1682400007]["retry_count"], 1)
        self.assertEqual(plugin._memory_pending[1682400007]["messages"][0]["id"], 10)
        ctx.host.read.side_effect = None
        ctx.host.read.return_value["read_inbox_max_id"] = 10
        await plugin._flush_sender(ctx, 1682400007)
        self.assertEqual(plugin._memory_pending, {})
        plugin._send_notification.assert_not_awaited()

    async def test_send_failure_rechecks_reads_before_retry(self):
        plugin, ctx = self.make_case()
        plugin._send_notification.return_value = False
        await plugin.on_event(ctx, message_payload(1682400007))
        await plugin._flush_sender(ctx, 1682400007)
        ctx.host.read.return_value["read_inbox_max_id"] = 10
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_awaited_once()
        self.assertEqual(ctx.host.read.await_count, 2)
        self.assertEqual(plugin._memory_pending, {})

    async def test_disabled_filter_preserves_old_behavior(self):
        plugin, ctx = self.make_case()
        ctx.config["skip_read_messages"] = False
        await plugin.on_event(ctx, message_payload(1682400007))
        ctx.host.read.return_value["read_inbox_max_id"] = 10
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_awaited_once()
        ctx.host.read.assert_not_awaited()

    async def test_restored_messages_are_filtered_after_restart(self):
        plugin, ctx = self.make_case()
        ctx.storage = FakeStorage({})
        await plugin.on_event(ctx, message_payload(1682400007, "持久化内容", 10))
        await plugin.on_shutdown(ctx)
        restored = plugin_module.PrivateChatAssistantPlugin()
        restored._send_notification = AsyncMock(return_value=True)
        ctx.host.read.return_value["read_inbox_max_id"] = 10
        await restored.on_startup(ctx)
        await restored._flush_sender(ctx, 1682400007)
        restored._send_notification.assert_not_awaited()
        self.assertEqual(ctx.storage.values, {})

    async def test_legacy_messages_without_ids_are_not_forwarded_as_unread(self):
        plugin, ctx = self.make_case()
        plugin._memory_pending[1682400007] = {"sender_id": 1682400007, "messages": ["旧内容"]}
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_not_awaited()
        ctx.host.read.assert_not_awaited()
        self.assertEqual(plugin._memory_pending, {})

    async def test_read_queries_stop_after_retry_limit(self):
        plugin, ctx = self.make_case()
        await plugin.on_event(ctx, message_payload(1682400007))
        ctx.host.read.side_effect = RuntimeError("offline")
        for _ in range(plugin_module.MAX_SEND_RETRIES + 1):
            await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(plugin._memory_pending, {})
        self.assertIn("停止自动重试", ctx.log.await_args.args[1])

    async def test_invalid_read_state_cannot_send(self):
        for state in ({}, {"chat_id": 99, "read_inbox_max_id": 0},
                      {"chat_id": 1682400007, "read_inbox_max_id": -1},
                      {"chat_id": 1682400007, "read_inbox_max_id": True}):
            plugin, ctx = self.make_case()
            await plugin.on_event(ctx, message_payload(1682400007))
            ctx.host.read.return_value = state
            await plugin._flush_sender(ctx, 1682400007)
            plugin._send_notification.assert_not_awaited()
            self.assertIn(1682400007, plugin._memory_pending)

    async def test_shutdown_during_read_query_preserves_pending_without_send(self):
        plugin, ctx = self.make_case()
        await plugin.on_event(ctx, message_payload(1682400007))
        async def stop_during_query(*args, **kwargs):
            await plugin.on_shutdown(ctx)
            return {"chat_id": 1682400007, "read_inbox_max_id": 0}
        ctx.host.read.side_effect = stop_during_query
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertIn(1682400007, plugin._memory_pending)

    async def test_shutdown_during_storage_delete_preserves_pending_without_send(self):
        plugin, ctx = self.make_case()
        ctx.storage = FakeStorage({})
        await plugin.on_event(ctx, message_payload(1682400007))
        original_delete = ctx.storage.delete
        async def stop_during_delete(key):
            await plugin.on_shutdown(ctx)
            await original_delete(key)
        ctx.storage.delete = stop_during_delete
        await plugin._flush_sender(ctx, 1682400007)
        plugin._send_notification.assert_not_awaited()
        self.assertEqual(ctx.storage.values["pending:1682400007"]["messages"][0]["id"], 10)


if __name__ == "__main__":
    unittest.main()
