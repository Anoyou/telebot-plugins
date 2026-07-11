from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _install_framework_stubs() -> type:
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")
    events_module = types.ModuleType("app.worker.plugins.events")
    telethon_module = types.ModuleType("telethon")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id=1, feature_key="", log=None, config=None, redis=None, messages=None, client=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}
            self.redis = redis
            self.messages = messages
            self.client = client

    def register(cls):
        return cls

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        name = getattr(entity, "first_name", None) or getattr(entity, "username", None)
        if name:
            return str(name)
        return str(fallback_id) if fallback_id not in (None, "") else default

    def current_command_prefix(*, fallback=None):
        return fallback or ","

    def event_from_interaction_payload(payload):
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        event_type = str(event.get("type") or payload.get("event_type") or source.get("type") or "")
        chat_id = message.get("chat_id") or payload.get("chat_id") or source.get("chat_id") or event.get("chat_id")
        message_id = message.get("message_id") or payload.get("message_id") or source.get("message_id") or event.get("message_id")
        text = message.get("text") or payload.get("message_text") or payload.get("text") or source.get("text") or event.get("text") or ""
        actor_ns = types.SimpleNamespace(
            user_id=actor.get("user_id") or payload.get("sender_user_id"),
            display_name=actor.get("display_name") or payload.get("sender_name") or "玩家",
        )
        return types.SimpleNamespace(
            type=event_type,
            message=types.SimpleNamespace(chat_id=chat_id, message_id=message_id, text=text),
            sender=actor_ns,
            actor=actor_ns,
            callback=types.SimpleNamespace(
                id=source.get("callback_query_id") or event.get("callback_query_id") or payload.get("callback_query_id"),
                data=source.get("callback_data") or event.get("callback_data") or payload.get("callback_data") or "",
            ),
        )

    command_module.current_command_prefix = current_command_prefix
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name
    events_module.event_from_interaction_payload = event_from_interaction_payload
    telethon_module.events = types.SimpleNamespace(NewMessage=types.SimpleNamespace(Event=object))

    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules["app.worker.command"] = command_module
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module
    sys.modules["app.worker.plugins.events"] = events_module
    sys.modules.setdefault("telethon", telethon_module)
    return PluginContext


PluginContext = _install_framework_stubs()


def _load_plugin(module_name: str, plugin_dir: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / plugin_dir / "plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


math10_module = _load_plugin("math10_plugin_under_test", "math10")
game24_module = _load_plugin("game24_plugin_under_test", "game24")


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, *keys: str):
        removed = 0
        for key in keys:
            removed += 1 if self.store.pop(key, None) is not None else 0
        return removed


class FakeMessages:
    def __init__(self) -> None:
        self.applied: list[dict[str, object]] = []
        self.saved: dict[str, int] = {}

    async def send(self, **kwargs):  # noqa: ANN003
        self.applied.append({"send": dict(kwargs)})

    async def edit(self, **kwargs):  # noqa: ANN003
        self.applied.append({"edit": dict(kwargs)})

    async def apply(self, actions, *, entry_key=None):  # noqa: ANN001
        self.applied.append({"actions": list(actions), "entry_key": entry_key})

    async def read_saved_message_id(self, key: str) -> int | None:
        return self.saved.get(key)


def start_payload(*, prize: int = 666) -> dict:
    return {
        "event": {"type": "keyword", "chat_id": -100123, "message_id": 10},
        "source": {"type": "message", "chat_id": -100123, "message_id": 10},
        "actor": {"user_id": 999, "display_name": "管理员"},
        "chat_id": -100123,
        "message_id": 10,
        "prize": prize,
        "valid_seconds": 900,
    }


def answer_payload(*, text: str, user_id: int = 111, message_id: int = 20, name: str = "玩家A") -> dict:
    return {
        "event": {"type": "message", "chat_id": -100123, "message_id": message_id, "text": text},
        "source": {"type": "message", "chat_id": -100123, "message_id": message_id, "text": text},
        "actor": {"user_id": user_id, "display_name": name},
        "sender_user_id": user_id,
        "sender_name": name,
        "chat_id": -100123,
        "message_id": message_id,
        "message_text": text,
    }


class InteractionPayoutContractTests(unittest.TestCase):
    def test_math10_answer_reads_saved_message_id_from_messages_facade(self) -> None:
        async def scenario() -> None:
            plugin = math10_module.Math10Plugin()
            messages = FakeMessages()
            ctx = PluginContext(feature_key="math10", redis=FakeRedis(), messages=messages)
            with patch.object(math10_module, "_new_math_question", return_value=("1 + 1", 2)):
                await plugin.on_interaction(ctx, "start_math_game", start_payload(prize=666))
            state = await plugin._load_state(ctx, ctx.account_id, -100123)
            self.assertIsNotNone(state)
            key = math10_module._question_message_key(state)
            messages.saved[key] = 777

            await plugin.on_interaction(
                ctx,
                "start_math_game",
                answer_payload(text="2", user_id=111, message_id=22),
            )

            edit = next(call["edit"] for call in messages.applied if "edit" in call)
            self.assertEqual(edit["message_id"], 777)

        asyncio.run(scenario())

    def test_start_messages_include_plugin_versions(self) -> None:
        async def scenario() -> None:
            math_plugin = math10_module.Math10Plugin()
            math_ctx = PluginContext(feature_key="math10", redis=FakeRedis())
            with patch.object(math10_module, "_new_math_question", return_value=("1 + 1", 2)):
                math_actions = await math_plugin.on_interaction(math_ctx, "start_math_game", start_payload(prize=666))

            game_plugin = game24_module.Game24Plugin()
            game_ctx = PluginContext(feature_key="game24", redis=FakeRedis())
            with patch.object(game24_module, "generate_24_puzzle", return_value=[1, 2, 3, 4]):
                game_actions = await game_plugin.on_interaction(game_ctx, "start_paid_game", start_payload(prize=777))

            self.assertIn("v1.0.10", math_actions[0]["text"])
            self.assertIn("v1.1.10", game_actions[0]["text"])

        asyncio.run(scenario())

    def test_math10_answer_payout_carries_prize_and_user_fallback(self) -> None:
        async def scenario() -> None:
            plugin = math10_module.Math10Plugin()
            ctx = PluginContext(feature_key="math10", redis=FakeRedis())
            with patch.object(math10_module, "_new_math_question", return_value=("1 + 1", 2)):
                await plugin.on_interaction(ctx, "start_math_game", start_payload(prize=666))
            actions = await plugin.on_interaction(
                ctx,
                "start_math_game",
                answer_payload(text="2", user_id=111, message_id=22, name="你心里已经有答案了"),
            )
            message = next(action for action in actions if action.get("type") == "send_message")
            payout = next(action for action in actions if action.get("type") == "payout")

            self.assertIn("恭喜 你心里已经有答案了 答对！", message["text"])
            self.assertNotIn("你心里已经有答案了（你心里已经有答案了）", message["text"])
            self.assertEqual(message["text"].count("奖金：666"), 1)
            self.assertEqual(payout["chat_id"], -100123)
            self.assertEqual(payout["amount"], 666)
            self.assertEqual(payout["reply_to_message_id"], 22)
            self.assertEqual(payout["reply_to_user_id"], 111)
            self.assertEqual(payout["reply_to_search_limit"], 50)

        asyncio.run(scenario())

    def test_game24_answer_payout_carries_user_fallback(self) -> None:
        async def scenario() -> None:
            plugin = game24_module.Game24Plugin()
            ctx = PluginContext(feature_key="game24", redis=FakeRedis())
            with patch.object(game24_module, "generate_24_puzzle", return_value=[1, 2, 3, 4]):
                await plugin.on_interaction(ctx, "start_paid_game", start_payload(prize=777))
            actions = await plugin.on_interaction(
                ctx,
                "start_paid_game",
                answer_payload(text="(1+2+3)*4", user_id=222, message_id=33),
            )
            message = next(action for action in actions if action.get("type") == "send_message")
            payout = next(action for action in actions if action.get("type") == "payout")

            self.assertEqual(message["text"].count("奖金：777"), 1)
            self.assertEqual(payout["chat_id"], -100123)
            self.assertEqual(payout["amount"], 777)
            self.assertEqual(payout["reply_to_message_id"], 33)
            self.assertEqual(payout["reply_to_user_id"], 222)
            self.assertEqual(payout["reply_to_search_limit"], 50)

        asyncio.run(scenario())

    def test_game24_userbot_command_reward_uses_standard_payout_action(self) -> None:
        async def scenario() -> None:
            plugin = game24_module.Game24Plugin()
            messages = FakeMessages()
            ctx = PluginContext(feature_key="game24", messages=messages)
            game = game24_module.GameState(chat_id=-100123, trigger_msg_id=10, numbers=[1, 2, 3, 4], prize=888)
            incoming = game24_module.IncomingMessage(
                chat_id=-100123,
                message_id=44,
                sender_id=333,
                sender_name="玩家C",
                text="(1+2+3)*4",
                outgoing=False,
            )

            sent = await plugin._send_prize_reply(ctx, object(), game, incoming)

            self.assertTrue(sent)
            self.assertEqual(messages.applied[0]["entry_key"], "admin_command")
            payout = messages.applied[0]["actions"][0]
            self.assertEqual(payout["type"], "payout")
            self.assertEqual(payout["chat_id"], -100123)
            self.assertEqual(payout["amount"], 888)
            self.assertEqual(payout["reply_to_message_id"], 44)
            self.assertEqual(payout["reply_to_user_id"], 333)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
