from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch
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
        def __init__(self, account_id=1, feature_key="ten_half", log=None, config=None, client=None, redis=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}
            self.client = client
            self.redis = redis

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


def payment_payload(
    *,
    payer_id: int = 111,
    payer_name: str = "玩家A",
    amount: int = 100,
    notice_message_id: int = 701,
    reply_message_id: int = 700,
) -> dict:
    return {
        "event": {"type": "payment_confirmed", "chat_id": -100123},
        "source": {"type": "payment_confirmed", "chat_id": -100123, "message_id": notice_message_id},
        "actor": {"user_id": 456, "display_name": "通知Bot"},
        "reply_to": {"message_id": reply_message_id, "user_id": payer_id, "display_name": payer_name},
        "payer_user_id": payer_id,
        "payer_name": payer_name,
        "amount": amount,
    }


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return types.SimpleNamespace(id=len(self.sent))


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)
        return True


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
                self.assertIn("当前牌桌 ID", start_actions[0]["text"])
                self.assertIn("save_message_id_key", start_actions[0])

                join_actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual(len(join_actions), 1)
                self.assertIn("加入牌局成功", join_actions[0]["text"])
                self.assertIn("牌桌 ID", join_actions[0]["text"])
                self.assertNotIn("十点半开局", join_actions[0]["text"])
                self.assertEqual(
                    join_actions[0]["replace_saved_message_id_key"],
                    plugin_module._join_notice_key(1, -100123),
                )

                game = plugin._games[-100123]
                self.assertEqual(game.player_message_ids[111], 700)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_join_edits_saved_main_and_deletes_previous_join_notice(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "start_ten_half", keyword_payload())
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                first = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["type"] for a in first], ["send_message", "edit_message"])
                self.assertEqual(first[1]["message_id"], 900)
                self.assertIn("玩家A", first[1]["text"])

                redis.store[plugin_module._join_notice_key(1, -100123)] = "910"
                second = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(
                        payer_id=222,
                        payer_name="玩家B",
                        notice_message_id=711,
                        reply_message_id=710,
                    ),
                )
                self.assertEqual([a["type"] for a in second], ["send_message", "delete_message", "edit_message"])
                self.assertEqual(second[1]["message_id"], 910)
                self.assertEqual(second[2]["message_id"], 900)
                self.assertIn("玩家A、玩家B", second[0]["text"])
                self.assertIn("玩家A、玩家B", second[2]["text"])
                self.assertEqual(plugin._games[-100123].phase, "ask_dealer")
                self.assertIn("reply_markup", second[2])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_interaction_dealer_timeout_does_not_auto_pick_bot_dealer(self) -> None:
        # This test uses a local coroutine patch to avoid waiting 30 seconds.
        async def fast_sleep(_seconds):
            return None

        async def scenario_fast() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="ask_dealer",
                via_interaction=True,
                started_at=123.0,
            )
            game.ask_dealer_uid = 111
            game.ask_dealer_name = "玩家A"
            plugin._games[-100123] = game

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._dealer_question_timeout(-100123, 123.0, ctx)

            self.assertEqual(game.phase, "ask_dealer")
            self.assertEqual(game.dealer_id, 0)
            self.assertEqual(game.dealer_cards, [])
            self.assertIn("选庄等待已结束", game.status_note)

        asyncio.run(scenario_fast())

    def test_bot_dealer_stand_advances_to_player_turn(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, via_interaction=True)
            game.lobby_players = [(111, "玩家A"), (222, "玩家B")]
            game.main_message_id = 900
            deck = [
                plugin_module.Card("♠️", "4"),
                plugin_module.Card("♥️", "3"),
                plugin_module.Card("♦️", "2"),
                plugin_module.Card("♣️", "A"),
            ]

            with patch.object(plugin_module, "create_deck", return_value=list(deck)):
                actions = await plugin._ix_begin(-100123, game, 0, "🤖 庄家", ctx)

            self.assertEqual(game.phase, "playing")
            self.assertFalse(game.finished)
            self.assertEqual(game.current_player_idx, 0)
            self.assertEqual(len(game.dealer_cards), 2)
            self.assertEqual([len(p.cards) for p in game.players], [1, 1])
            self.assertNotIn("end_session", [action["type"] for action in actions])
            self.assertEqual(actions[-1]["type"], "edit_message")
            self.assertIn("轮到 玩家A 行动", actions[-1]["text"])
            self.assertIn("reply_markup", actions[-1])

        asyncio.run(scenario())

    def test_wrong_player_callback_returns_answer_callback(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.players = [
                plugin_module.PlayerHand(user_id=111, name="玩家A", cards=[plugin_module.Card("♠️", "5")]),
                plugin_module.PlayerHand(user_id=222, name="玩家B", cards=[plugin_module.Card("♥️", "6")]),
            ]
            game.current_player_idx = 0
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-1",
                        "callback_data": "th:hit:111",
                    },
                    "actor": {"user_id": 222, "display_name": "玩家B"},
                },
            )
            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-1",
                "text": "还没轮到你。",
                "show_alert": False,
            }])

        asyncio.run(scenario())

    def test_interaction_begin_deals_one_card_to_each_player_and_dealer_two(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, via_interaction=True)
            game.lobby_players = [(111, "庄家候选"), (222, "玩家B"), (333, "玩家C")]
            game.main_message_id = 900

            actions = await plugin._ix_begin(-100123, game, 111, "庄家候选", ctx)
            self.assertEqual(game.phase, "dealer_turn")
            self.assertEqual(len(game.dealer_cards), 2)
            self.assertEqual([len(p.cards) for p in game.players], [1, 1])
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertIn("1张", actions[0]["text"])
            self.assertIn("庄家先行动", actions[0]["text"])

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
