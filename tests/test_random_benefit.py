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
        def __init__(self, *, config=None, account_id: int = 1, redis=None, client=None, messages=None) -> None:
            self.account_id = account_id
            self.feature_key = "random_benefit"
            self.config = config or {}
            self.redis = redis
            self.client = client
            self.messages = messages
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


def _message_payload(
    *,
    text: str = "今天真不错",
    chat_id: int = -1001,
    message_id: int = 55,
    sender_id: int = 42,
    sender_is_bot: bool = False,
    sender_username: str = "",
    outgoing: bool = False,
    userbot_user_id: int | None = None,
):
    payload = {
        "type": "message",
        "message": {"text": text, "chat_id": chat_id, "message_id": message_id, "outgoing": outgoing},
        "sender": {
            "user_id": sender_id,
            "display_name": "小明",
            "username": sender_username,
            "is_bot": sender_is_bot,
        },
    }
    if userbot_user_id is not None:
        payload["userbot_user_id"] = userbot_user_id
    return payload


def _command_payload(text: str, *, chat_id: int = -1001, message_id: int = 9):
    command = text.strip().split(maxsplit=1)[0].lstrip(",/!！，")
    return {
        "type": "command",
        "message": {"text": text, "chat_id": chat_id, "message_id": message_id},
        "trigger": {"command": command, "args": text.split()[1:]},
    }


class FakeDirectEvent:
    def __init__(
        self,
        *,
        text: str = "今天真不错",
        chat_id: int = -1001,
        message_id: int = 66,
        sender_id: int = 42,
        outgoing: bool = False,
        bot: bool = False,
    ) -> None:
        self.raw_text = text
        self.text = text
        self.chat_id = chat_id
        self.id = message_id
        self.sender_id = sender_id
        self.outgoing = outgoing
        self.replies: list[str] = []
        self.sender = types.SimpleNamespace(first_name="小明", username="", bot=bot, is_bot=bot)

    async def get_sender(self):
        return self.sender

    async def reply(self, text: str, **kwargs):
        self.replies.append(text)
        return types.SimpleNamespace(text=text, kwargs=kwargs)


class FakeCommandEvent(FakeDirectEvent):
    pass


class FakeMessages:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return kwargs


class FakeClient:
    def __init__(self, *, me_id: int, entities: dict[int, object] | None = None) -> None:
        self.me_id = me_id
        self.entities = entities or {}
        self.get_entity_calls: list[int] = []

    async def get_me(self):
        return types.SimpleNamespace(id=self.me_id)

    async def get_entity(self, sender_id: int):
        self.get_entity_calls.append(sender_id)
        return self.entities[sender_id]


class RandomBenefitPluginTest(unittest.TestCase):
    def _ctx(self, *, client=None, messages=None, **overrides):
        config = {
            "start_command": "福利开启",
            "stop_command": "福利暂停",
            "status_command": "福利状态",
            "allowed_chat_ids": [-1001],
            "reply_template": "送给 {sender}: +1-6666",
            "trigger_probability": "1",
            "default_enabled": True,
        }
        config.update(overrides)
        return PluginContext(config=config, client=client, messages=messages or FakeMessages())

    def test_config_schema_declares_allowed_peer_selector_and_preview(self) -> None:
        manifest = json.loads((ROOT / "random_benefit" / "plugin.json").read_text())
        properties = manifest["config_schema"]["properties"]

        self.assertEqual(manifest["version"], "1.5.4")
        self.assertEqual(properties["allowed_chat_ids"]["x-ui-widget"], "allowed-peer-multi-select")
        self.assertEqual(properties["allowed_chat_ids"]["items"]["type"], "integer")
        self.assertEqual(properties["start_command"]["default"], "福利开启")
        self.assertEqual(properties["stop_command"]["default"], "福利暂停")
        self.assertEqual(properties["status_command"]["default"], "福利状态")
        self.assertTrue(properties["template_preview"]["readOnly"])
        self.assertEqual(properties["trigger_probability"]["type"], ["string", "number"])
        self.assertEqual(properties["trigger_probability"]["default"], "0.05")
        self.assertEqual(properties["trigger_probability"]["minimum"], 0)
        self.assertEqual(properties["trigger_probability"]["maximum"], 1)
        self.assertEqual(properties["chat_cooldown_seconds"]["default"], 30)
        self.assertEqual(properties["user_cooldown_seconds"]["default"], 120)
        self.assertEqual(
            [item["scope"] for item in manifest["event_subscriptions"] if "command" in item["events"]],
            ["owner_only"],
        )
        self.assertIn("x-usage-guide", manifest["config_schema"])
        passthrough = manifest["capabilities"]["telegram_direct_passthrough"]
        self.assertTrue(passthrough["enabled"])
        self.assertEqual(passthrough["sources"], ["userbot"])
        self.assertEqual(passthrough["directions"], ["incoming"])

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

            off_actions = await plugin.on_event(ctx, _command_payload(",福利暂停"))
            self.assertEqual(off_actions[0]["text"], "随机福利已暂停。")

            with patch.object(plugin_module.random, "random", return_value=0):
                actions = await plugin.on_event(ctx, _message_payload())
            self.assertEqual(actions, [])

            on_actions = await plugin.on_event(ctx, _command_payload(",福利开启"))
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

    def test_event_bus_ignores_self_and_bot_messages(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(chat_cooldown_seconds=0, user_cooldown_seconds=0)

            with patch.object(plugin_module.random, "random", return_value=0):
                self_actions = await plugin.on_event(
                    ctx,
                    _message_payload(sender_id=42, userbot_user_id=42, message_id=301),
                )
                bot_actions = await plugin.on_event(
                    ctx,
                    _message_payload(sender_id=77, sender_is_bot=True, message_id=302),
                )
                account_bot_payload = _message_payload(sender_id=88, message_id=303)
                account_bot_payload["source_actor"] = {"user_id": 88, "type": "account_bot"}
                account_bot_actions = await plugin.on_event(ctx, account_bot_payload)

            self.assertEqual(self_actions, [])
            self.assertEqual(bot_actions, [])
            self.assertEqual(account_bot_actions, [])

        asyncio.run(run_case())

    def test_event_bus_resolves_bot_when_envelope_omits_bot_flag(self) -> None:
        async def run_case() -> None:
            client = FakeClient(
                me_id=42,
                entities={77: types.SimpleNamespace(id=77, username="benefit_helper_bot", bot=True)},
            )
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(client=client, chat_cooldown_seconds=0, user_cooldown_seconds=0)

            with patch.object(plugin_module.random, "random", return_value=0):
                first_actions = await plugin.on_event(
                    ctx,
                    _message_payload(
                        sender_id=77,
                        sender_username="benefit_helper_bot",
                        message_id=304,
                    ),
                )
                second_actions = await plugin.on_event(
                    ctx,
                    _message_payload(
                        sender_id=77,
                        sender_username="benefit_helper_bot",
                        message_id=305,
                    ),
                )

            self.assertEqual(first_actions, [])
            self.assertEqual(second_actions, [])
            self.assertEqual(client.get_entity_calls, [77])

        asyncio.run(run_case())

    def test_event_bus_uses_bot_username_when_entity_lookup_is_unavailable(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(chat_cooldown_seconds=0, user_cooldown_seconds=0)

            with patch.object(plugin_module.random, "random", return_value=0):
                actions = await plugin.on_event(
                    ctx,
                    _message_payload(
                        sender_id=78,
                        sender_username="fallback_helper_bot",
                        message_id=306,
                    ),
                )

            self.assertEqual(actions, [])

        asyncio.run(run_case())

    def test_blocked_bot_message_does_not_consume_reply_cooldown(self) -> None:
        async def run_case() -> None:
            client = FakeClient(
                me_id=999,
                entities={77: types.SimpleNamespace(id=77, username="benefit_helper_bot", bot=True)},
            )
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(client=client, chat_cooldown_seconds=30, user_cooldown_seconds=120)

            with patch.object(plugin_module.random, "random", return_value=0):
                bot_actions = await plugin.on_event(
                    ctx,
                    _message_payload(
                        sender_id=77,
                        sender_username="benefit_helper_bot",
                        message_id=307,
                    ),
                )
                human_actions = await plugin.on_event(
                    ctx,
                    _message_payload(sender_id=79, message_id=308),
                )

            self.assertEqual(bot_actions, [])
            self.assertEqual(len(human_actions), 1)

        asyncio.run(run_case())

    def test_reply_cooldown_blocks_repeated_trigger(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(chat_cooldown_seconds=30, user_cooldown_seconds=120)

            with patch.object(plugin_module.random, "random", return_value=0):
                first = await plugin.on_event(ctx, _message_payload(message_id=101))
                second = await plugin.on_event(ctx, _message_payload(message_id=102))

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])

        asyncio.run(run_case())

    def test_decimal_probability_string_is_preserved_at_runtime(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(trigger_probability="0.09", chat_cooldown_seconds=0, user_cooldown_seconds=0)

            with patch.object(plugin_module.random, "random", return_value=0.08):
                hit = await plugin.on_event(ctx, _message_payload(message_id=201))
            with patch.object(plugin_module.random, "random", return_value=0.10):
                miss = await plugin.on_event(ctx, _message_payload(message_id=202))

            self.assertEqual(len(hit), 1)
            self.assertEqual(miss, [])

        asyncio.run(run_case())

    def test_direct_message_replies_through_live_event(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx()
            event = FakeDirectEvent()

            with patch.object(plugin_module.random, "random", return_value=0):
                await plugin.on_direct_message(ctx, event)

            self.assertEqual(event.replies, [])
            self.assertEqual(
                ctx.messages.sent,
                [{"chat_id": -1001, "text": "送给 小明: +1-6666", "reply_to_message_id": 66}],
            )

        asyncio.run(run_case())

    def test_direct_message_ignores_self_and_bot_messages(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx(client=FakeClient(me_id=42), chat_cooldown_seconds=0, user_cooldown_seconds=0)

            with patch.object(plugin_module.random, "random", return_value=0):
                self_event = FakeDirectEvent(sender_id=42)
                await plugin.on_direct_message(ctx, self_event)

                bot_event = FakeDirectEvent(sender_id=77, bot=True)
                await plugin.on_direct_message(ctx, bot_event)

            self.assertEqual(self_event.replies, [])
            self.assertEqual(bot_event.replies, [])

        asyncio.run(run_case())

    def test_direct_message_ignores_outgoing_commands(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx()
            event = FakeDirectEvent(text=",福利 off", outgoing=True)

            with patch.object(plugin_module.random, "random", return_value=0):
                await plugin.on_direct_message(ctx, event)

            self.assertEqual(event.replies, [])

        asyncio.run(run_case())

    def test_legacy_command_accepts_extra_dispatch_arguments(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.RandomBenefitPlugin()
            ctx = self._ctx()
            event = FakeCommandEvent(text=",福利暂停")

            await plugin._legacy_command(ctx, event, "arg1", "arg2", "arg3")

            self.assertEqual([item["text"] for item in ctx.messages.sent], ["随机福利已暂停。"])

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
