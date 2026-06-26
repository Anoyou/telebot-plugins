from __future__ import annotations

import asyncio
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
        def __init__(self, account_id=1, feature_key="blackjack", log=None, config=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}

    def register(cls):
        return cls

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        return str(fallback_id) if fallback_id not in (None, "") else default

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules.setdefault("app.worker.plugins.base", base_module)

    spec = importlib.util.spec_from_file_location(
        "blackjack_plugin_under_test",
        ROOT / "blackjack" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


class CardDealer:
    def __init__(self, cards: list[tuple[str, str]]) -> None:
        self._cards = list(cards)

    def __call__(self) -> tuple[str, str]:
        if not self._cards:
            raise AssertionError("deterministic card sequence exhausted")
        return self._cards.pop(0)


def payment_payload() -> dict:
    return {
        "event": {
            "type": "payment_confirmed",
            "chat_id": -100123,
            "message_id": 10,
            "user_id": 456,
            "display_name": "通知Bot",
        },
        "source": {"type": "payment_confirmed", "chat_id": -100123, "message_id": 10},
        "actor": {"user_id": 456, "display_name": "通知Bot"},
        "payer_user_id": 111,
        "payer_name": "玩家A",
        "sender_user_id": 456,
        "sender_name": "通知Bot",
        "chat_id": -100123,
        "message_id": 10,
        "prize": 10,
        "valid_seconds": 120,
    }


def callback_payload(callback_data: str, *, user_id: int = 111) -> dict:
    return {
        "event": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": 11,
            "user_id": user_id,
            "display_name": f"玩家{user_id}",
            "callback_query_id": "cb1",
            "callback_data": callback_data,
        },
        "source": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": 11,
            "callback_query_id": "cb1",
            "callback_data": callback_data,
        },
        "actor": {"user_id": user_id, "display_name": f"玩家{user_id}"},
        "callback_query_id": "cb1",
        "callback_data": callback_data,
        "sender_user_id": user_id,
        "sender_name": f"玩家{user_id}",
        "chat_id": -100123,
        "message_id": 11,
    }


class BlackjackInteractionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_deal_card = plugin_module._deal_card

    def tearDown(self) -> None:
        plugin_module._deal_card = self._original_deal_card

    def test_payment_confirmed_start_uses_payer_identity_for_buttons(self) -> None:
        plugin_module._deal_card = CardDealer([("5", "♠"), ("6", "♣"), ("9", "♦"), ("7", "♥"), ("2", "♠")])

        async def scenario() -> None:
            plugin = plugin_module.BlackjackPlugin()
            ctx = PluginContext(account_id=1, feature_key="blackjack", log=None)
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(ctx, "start_blackjack", payment_payload())

                self.assertIsNotNone(actions)
                self.assertEqual(actions[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "bj:hit:111")
                self.assertIn("玩家A 下注 10 筹码", actions[0]["text"])
                self.assertNotIn("通知Bot 下注", actions[0]["text"])

                callback_actions = await plugin.on_interaction(ctx, "start_blackjack", callback_payload("bj:hit:111"))
                self.assertTrue(callback_actions)
                self.assertEqual(callback_actions[0]["type"], "send_message")
                self.assertIn("继续操作", callback_actions[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_blackjack_callback_still_rejects_non_owner(self) -> None:
        plugin_module._deal_card = CardDealer([("5", "♠"), ("6", "♣"), ("9", "♦"), ("7", "♥")])

        async def scenario() -> None:
            plugin = plugin_module.BlackjackPlugin()
            ctx = PluginContext(account_id=1, feature_key="blackjack", log=None)
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "start_blackjack", payment_payload())

                callback_actions = await plugin.on_interaction(
                    ctx,
                    "start_blackjack",
                    callback_payload("bj:hit:111", user_id=222),
                )
                self.assertEqual(callback_actions, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
