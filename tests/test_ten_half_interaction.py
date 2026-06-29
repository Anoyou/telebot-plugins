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
        def __init__(self, account_id=1, feature_key="ten_half", log=None, config=None, client=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}
            self.client = client

    def register(cls):
        return cls

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        name = getattr(entity, "first_name", None) or getattr(entity, "username", None)
        if name:
            return str(name)
        return str(fallback_id) if fallback_id not in (None, "") else default

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module

    spec = importlib.util.spec_from_file_location(
        "ten_half_plugin_under_test",
        ROOT / "ten_half" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


def keyword_payload() -> dict:
    return {
        "event": {"type": "keyword", "chat_id": -100123, "message_id": 600},
        "source": {"type": "message", "chat_id": -100123, "message_id": 600},
        "actor": {"user_id": 999, "display_name": "管理员"},
        "bet": 100,
    }


def payment_payload(*, payer_id: int = 111, payer_name: str = "玩家A") -> dict:
    return {
        "event": {"type": "payment_confirmed", "chat_id": -100123},
        "source": {"type": "payment_confirmed", "chat_id": -100123, "message_id": 701},
        "actor": {"user_id": 456, "display_name": "通知Bot"},
        "reply_to": {"message_id": 700, "user_id": payer_id, "display_name": payer_name},
        "payer_user_id": payer_id,
        "payer_name": payer_name,
        "amount": 100,
    }


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return types.SimpleNamespace(id=len(self.sent))


class TenHalfInteractionTest(unittest.TestCase):
    def test_payment_join_existing_keyword_lobby_does_not_duplicate_lobby_message(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                start_actions = await plugin.on_interaction(ctx, "start_ten_half", keyword_payload())
                self.assertEqual(len(start_actions), 1)
                self.assertIn("十点半开局", start_actions[0]["text"])

                join_actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual(len(join_actions), 1)
                self.assertIn("加入成功", join_actions[0]["text"])
                self.assertNotIn("十点半开局", join_actions[0]["text"])

                game = plugin._games[-100123]
                self.assertEqual(game.player_message_ids[111], 700)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_interaction_settlement_rewards_reply_with_transfer_amount(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            player = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player.cards = [plugin_module.Card("♠️", "9"), plugin_module.Card("♥️", "A")]
            game.players = [player]
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "10")]
            game.player_message_ids[111] = 700

            actions = await plugin._ix_settle(-100123, game, PluginContext())
            reward = next(action for action in actions if action.get("send_via") == "userbot_reply")
            self.assertEqual(reward["text"], "+90")
            self.assertEqual(reward["reply_to_message_id"], 700)
            self.assertEqual(actions[-1]["type"], "end_session")

        asyncio.run(scenario())

    def test_background_dealer_play_sends_reward_without_actions_name_error(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            client = FakeClient()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            player = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player.cards = [plugin_module.Card("♠️", "9"), plugin_module.Card("♥️", "A")]
            game.players = [player]
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "10")]
            game.player_message_ids[111] = 700
            plugin._games[-100123] = game

            await plugin._dealer_play_ix(-100123, game, PluginContext(client=client))

            reward_messages = [item for item in client.sent if item["text"] == "+90"]
            self.assertEqual(len(reward_messages), 1)
            self.assertEqual(reward_messages[0]["reply_to"], 700)
            self.assertNotIn(-100123, plugin._games)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
