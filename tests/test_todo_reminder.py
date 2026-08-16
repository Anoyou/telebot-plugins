from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _install_stubs() -> None:
    if "app.worker.plugins.base" in sys.modules and "telethon.tl.types" in sys.modules:
        return
    app = sys.modules.setdefault("app", types.ModuleType("app"))
    worker = sys.modules.setdefault("app.worker", types.ModuleType("app.worker"))
    plugins = sys.modules.setdefault("app.worker.plugins", types.ModuleType("app.worker.plugins"))
    base = types.ModuleType("app.worker.plugins.base")
    manifest = types.ModuleType("app.worker.plugins.manifest")
    telethon = sys.modules.setdefault("telethon", types.ModuleType("telethon"))
    telethon_tl = sys.modules.setdefault("telethon.tl", types.ModuleType("telethon.tl"))
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

    def register(cls):
        return cls

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = register
    manifest.Manifest = Manifest
    telethon_types.PeerUser = PeerUser
    sys.modules["app.worker.plugins.base"] = base
    sys.modules["app.worker.plugins.manifest"] = manifest
    sys.modules["telethon.tl.types"] = telethon_types
    app.worker = worker
    worker.plugins = plugins
    telethon.tl = telethon_tl
    telethon_tl.types = telethon_types


_install_stubs()
package_module = importlib.import_module("todo_reminder")
plugin_module = importlib.import_module("todo_reminder.plugin")
manifest_module = importlib.import_module("todo_reminder.manifest")


class _Storage:
    available = True

    def __init__(self, initial=None):
        self.values = dict(initial or {})

    async def get_all(self):
        return dict(self.values)

    async def set(self, key, value):
        self.values[key] = dict(value)
        return True

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)


class _Scheduler:
    def __init__(self):
        self.jobs = {}
        self.unregistered = []

    def register(self, job_id, schedule, callback, *, replace=True):
        self.jobs[job_id] = (schedule, callback)

    def unregister(self, job_id):
        self.unregistered.append(job_id)
        return self.jobs.pop(job_id, None) is not None

    def unregister_all(self):
        count = len(self.jobs)
        self.jobs.clear()
        return count


class _Messages:
    def __init__(self):
        self.actions = []
        self.saved = {}
        self.deleted_saved = []

    async def apply(self, actions, *, entry_key=None):
        self.actions.extend(actions)

    async def send(self, **kwargs):
        self.actions.append({"type": "send_message", **kwargs})

    async def read_saved_message_id(self, key):
        return self.saved.get(key)

    async def delete_saved_message_id(self, key):
        self.deleted_saved.append(key)
        self.saved.pop(key, None)
        return True


class _FailingMessages(_Messages):
    async def apply(self, actions, *, entry_key=None):
        raise RuntimeError("temporary send failure")


def _ctx(*, storage=None, scheduler=None, messages=None, config=None):
    class Client:
        async def get_me(self):
            return types.SimpleNamespace(id=999, username="owner")

    return types.SimpleNamespace(
        config=config or {},
        storage=storage or _Storage(),
        scheduler=scheduler or _Scheduler(),
        messages=messages or _Messages(),
        client=Client(),
        log=None,
    )


class TodoReminderTest(unittest.TestCase):
    def test_static_manifest_matches_python_manifest(self) -> None:
        raw = json.loads((Path(__file__).resolve().parents[1] / "todo_reminder" / "plugin.json").read_text(encoding="utf-8"))
        manifest = manifest_module.MANIFEST
        self.assertEqual(raw["name"], manifest.key)
        self.assertEqual(raw["version"], manifest.version)
        self.assertEqual(raw["category"], manifest.category)
        self.assertEqual(raw["usage"], manifest.usage)
        self.assertEqual(raw["permissions"], manifest.permissions)
        self.assertEqual(raw["event_subscriptions"], manifest.event_subscriptions)
        self.assertEqual(raw["requires_platform_capabilities"], manifest.requires_platform_capabilities)
        self.assertEqual(raw["capabilities"], manifest.capabilities)
        self.assertEqual(raw["config_schema"], manifest.config_schema)
        self.assertEqual(raw["interaction_send_via"], manifest.interaction_send_via)
        self.assertIs(package_module.PLUGIN_CLASS, plugin_module.TodoReminderPlugin)
        self.assertIs(package_module.MANIFEST, manifest)

    def test_natural_language_time_examples(self) -> None:
        now = datetime(2026, 8, 16, 4, 0, tzinfo=timezone.utc)  # 上海 12:00
        cases = {
            "5分钟后提醒我喝水": (datetime(2026, 8, 16, 4, 5, tzinfo=timezone.utc), "提醒我喝水"),
            "五分钟后提醒他喝水": (datetime(2026, 8, 16, 4, 5, tzinfo=timezone.utc), "提醒他喝水"),
            "半小时后提醒我休息": (datetime(2026, 8, 16, 4, 30, tzinfo=timezone.utc), "提醒我休息"),
            "明天上午九点提醒我开会": (datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc), "提醒我开会"),
            "后天下午三点半提醒他提交": (datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc), "提醒他提交"),
            "今晚八点提醒我吃药": (datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc), "提醒我吃药"),
            "2026-08-17 14:30 提醒我开会": (datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc), "提醒我开会"),
            "8月17日 14点提醒我开会": (datetime(2026, 8, 17, 6, 0, tzinfo=timezone.utc), "提醒我开会"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(plugin_module.parse_natural_time(text, now=now), expected)
        self.assertEqual(
            plugin_module.parse_natural_time("8月15日 14点提醒我开会", now=now)[0],
            datetime(2027, 8, 15, 6, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            plugin_module.parse_natural_time("2026-08-15 14:00 提醒我开会", now=now)[0],
            datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc),
        )

    def test_reminder_request_extracts_todo_and_target_hint(self) -> None:
        now = datetime(2026, 8, 16, tzinfo=timezone.utc)
        self.assertEqual(plugin_module.parse_reminder_request("五分钟后提醒我喝水", now=now)[1:], ("喝水", True))
        self.assertEqual(plugin_module.parse_reminder_request("五分钟后提醒他喝水", now=now)[1:], ("喝水", False))

    def test_other_target_uses_userbot_reply_and_self_uses_interaction_bot(self) -> None:
        async def run_case():
            plugin = plugin_module.TodoReminderPlugin()
            messages = _Messages()
            ctx = _ctx(messages=messages)
            other = {
                "id": "other001", "chat_id": -1001, "target_user_id": 123, "target_username": "alice",
                "target_display_name": "Alice", "target_is_self": False, "todo": "喝水",
                "reply_to_message_id": 88, "repeat_interval_minutes": 5, "reminder_count": 0,
                "reminder_message_keys": [], "fire_at": datetime.now(timezone.utc).isoformat(),
            }
            other["todo"] = "喝水 <现在> & 记得"
            plugin._tasks["other001"] = other
            await plugin._fire_task(ctx, "other001")
            self.assertEqual(messages.actions[-1]["send_via"], "userbot_reply")
            self.assertEqual(messages.actions[-1]["reply_to_message_id"], 88)
            self.assertEqual(messages.actions[-1]["reply_to_user_id"], 123)
            self.assertEqual(messages.actions[-1]["parse_mode"], "html")
            self.assertIn('tg://user?id=123', messages.actions[-1]["text"])
            self.assertIn(">@alice</a>", messages.actions[-1]["text"])
            self.assertIn("喝水 &lt;现在&gt; &amp; 记得", messages.actions[-1]["text"])
            self.assertNotIn("喝水 <现在> & 记得", messages.actions[-1]["text"])

            own = dict(other, id="self0001", target_user_id=999, target_username="owner", target_is_self=True, reply_to_message_id=None)
            plugin._tasks["self0001"] = own
            await plugin._fire_task(ctx, "self0001")
            self.assertEqual(messages.actions[-1]["send_via"], "interaction_bot")
            self.assertEqual(messages.actions[-1]["parse_mode"], "html")
            self.assertIn("tg://user?id=999", messages.actions[-1]["text"])

        asyncio.run(run_case())

    def test_other_target_requires_reply_even_without_spacing(self) -> None:
        async def run_case():
            plugin = plugin_module.TodoReminderPlugin()
            plugin._me_id = 999
            messages = _Messages()
            ctx = _ctx(messages=messages)
            await plugin._handle_command_text(
                ctx,
                -1001,
                88,
                "五分钟后提醒他喝水",
                types.SimpleNamespace(reply_to_msg_id=None, message=None),
            )
            self.assertFalse(plugin._tasks)
            self.assertIn("需要回复目标用户", messages.actions[-1]["text"])

        asyncio.run(run_case())

    def test_undo_command_cancels_by_id_without_todo_prefix(self) -> None:
        async def run_case():
            storage = _Storage()
            scheduler = _Scheduler()
            messages = _Messages()
            ctx = _ctx(storage=storage, scheduler=scheduler, messages=messages)
            plugin = plugin_module.TodoReminderPlugin()
            task = {"id": "undo1234", "chat_id": -1001, "target_user_id": 123, "reminder_message_keys": []}
            plugin._tasks[task["id"]] = task
            storage.values["task:undo1234"] = task
            scheduler.jobs["todo_reminder_undo1234"] = ({}, None)
            event = types.SimpleNamespace(chat_id=-1001, id=99, raw_text=",undo undo1234")
            await plugin.on_startup(ctx)
            self.assertIn("undo", plugin.commands)
            await plugin.commands["undo"](None, event, ["undo1234"], 1, ctx)
            self.assertNotIn("undo1234", plugin._tasks)
            self.assertNotIn("task:undo1234", storage.values)
            self.assertIn("todo_reminder_undo1234", scheduler.unregistered)
            self.assertEqual(messages.actions[-1]["text"], "已取消提醒。")

        asyncio.run(run_case())

    def test_send_failure_keeps_next_repeat_schedule(self) -> None:
        async def run_case():
            scheduler = _Scheduler()
            storage = _Storage()
            ctx = _ctx(storage=storage, scheduler=scheduler, messages=_FailingMessages())
            plugin = plugin_module.TodoReminderPlugin()
            task = {
                "id": "retry001",
                "chat_id": -1001,
                "target_user_id": 123,
                "target_username": "alice",
                "target_display_name": "Alice",
                "target_is_self": False,
                "todo": "喝水",
                "reply_to_message_id": 88,
                "repeat_interval_minutes": 5,
                "reminder_count": 0,
                "reminder_message_keys": [],
                "fire_at": datetime.now(timezone.utc).isoformat(),
            }
            plugin._tasks["retry001"] = task
            with self.assertRaisesRegex(RuntimeError, "temporary send failure"):
                await plugin._fire_task(ctx, "retry001")
            self.assertIn("todo_reminder_retry001", scheduler.jobs)
            self.assertIn("task:retry001", storage.values)

        asyncio.run(run_case())

    def test_completed_reply_deletes_state_and_schedule(self) -> None:
        async def run_case():
            storage = _Storage()
            scheduler = _Scheduler()
            messages = _Messages()
            messages.saved["reminder:abc12345:1"] = 501
            ctx = _ctx(storage=storage, scheduler=scheduler, messages=messages)
            plugin = plugin_module.TodoReminderPlugin()
            task = {
                "id": "abc12345", "chat_id": -1001, "target_user_id": 123,
                "reminder_message_keys": ["reminder:abc12345:1"], "todo": "喝水",
            }
            plugin._tasks["abc12345"] = task
            storage.values["task:abc12345"] = task
            scheduler.jobs["todo_reminder_abc12345"] = ({}, None)
            await plugin.on_event(ctx, {
                "source": {"type": "message", "channel": "userbot"},
                "message": {"chat_id": -1001, "message_id": 502, "reply_to_message_id": 501, "text": "已完成"},
                "sender": {"user_id": 123},
            })
            self.assertNotIn("abc12345", plugin._tasks)
            self.assertNotIn("task:abc12345", storage.values)
            self.assertIn("todo_reminder_abc12345", scheduler.unregistered)

        asyncio.run(run_case())
        message_subscription = next(
            item for item in manifest_module.MANIFEST.event_subscriptions if "message" in item["events"]
        )
        sources = message_subscription["source"]
        self.assertIn("userbot", sources)
        self.assertIn("interaction_bot", sources)

    def test_startup_restores_persisted_task(self) -> None:
        async def run_case():
            future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            task = {"id": "restore1", "fire_at": future, "chat_id": -1001, "target_user_id": 123}
            storage = _Storage({"task:restore1": task})
            scheduler = _Scheduler()
            plugin = plugin_module.TodoReminderPlugin()
            await plugin.on_startup(_ctx(storage=storage, scheduler=scheduler))
            self.assertIn("restore1", plugin._tasks)
            self.assertIn("todo_reminder_restore1", scheduler.jobs)

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
