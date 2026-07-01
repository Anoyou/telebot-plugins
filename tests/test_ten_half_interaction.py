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
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id=1, feature_key="ten_half", log=None, config=None, client=None, redis=None, messages=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}
            self.client = client
            self.redis = redis
            self.messages = messages

    def register(cls):
        return cls

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        name = getattr(entity, "first_name", None) or getattr(entity, "username", None)
        if name:
            return str(name)
        return str(fallback_id) if fallback_id not in (None, "") else default

    def current_command_prefix(*, fallback=None):
        return "。"

    command_module.current_command_prefix = current_command_prefix
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules["app.worker.command"] = command_module
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


class FakeCommandClient(FakeClient):
    async def get_me(self):
        return types.SimpleNamespace(id=999, username="owner")


class FakeCommandEvent:
    chat_id = -100123
    id = 600
    sender_id = 999

    def __init__(self) -> None:
        self.replies: list[dict] = []

    async def reply(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})
        return types.SimpleNamespace(id=700)


class FakeMessages:
    def __init__(self) -> None:
        self.applied: list[dict] = []

    async def apply(self, actions, *, entry_key=None):
        self.applied.append({"entry_key": entry_key, "actions": list(actions)})


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)
        return True


class TenHalfInteractionTest(unittest.TestCase):
    def test_userbot_command_missing_bet_uses_live_command_prefix(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            event = FakeCommandEvent()

            await plugin._cmd(FakeCommandClient(), event, [], 1, PluginContext())

            self.assertEqual(len(event.replies), 1)
            self.assertIn("例如：。10d 100", event.replies[0]["text"])
            self.assertNotIn(",10d", event.replies[0]["text"])

        asyncio.run(scenario())

    def test_userbot_command_config_strips_live_prefix_for_registration(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"command": "。10d"})
            await plugin.on_startup(ctx)
            try:
                self.assertIn("10d", plugin.commands)
                self.assertNotIn("。10d", plugin.commands)
                event = FakeCommandEvent()
                await plugin.commands["10d"](FakeCommandClient(), event, [], 1, ctx)
                self.assertIn("例如：。10d 100", event.replies[0]["text"])
                self.assertNotIn("。。10d", event.replies[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_join_existing_keyword_lobby_does_not_duplicate_lobby_message(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                start_actions = await plugin.on_interaction(ctx, "start_ten_half", keyword_payload())
                self.assertEqual(len(start_actions), 1)
                self.assertIn("十点半开局", start_actions[0]["text"])
                self.assertIn("当前牌桌 ID", start_actions[0]["text"])
                self.assertIn("save_message_id_key", start_actions[0])
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                join_actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["type"] for a in join_actions], ["send_message", "delete_message"])
                self.assertIn("加入牌局成功", join_actions[0]["text"])
                self.assertIn("牌桌 ID", join_actions[0]["text"])
                self.assertNotIn("十点半开局", join_actions[0]["text"])
                self.assertIn("👥 当前玩家 (1/5):\n• 玩家A", join_actions[0]["text"])
                self.assertEqual(
                    join_actions[0]["save_message_id_key"],
                    plugin_module._join_notice_key(1, -100123),
                )
                self.assertEqual(join_actions[1]["message_id"], 900)

                game = plugin._games[-100123]
                self.assertEqual(game.player_message_ids[111], 700)
                self.assertTrue(game.opening_message_deleted)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_join_deletes_opening_then_previous_join_notice(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "start_ten_half", keyword_payload())
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                first = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["type"] for a in first], ["send_message", "delete_message"])
                self.assertEqual(first[1]["message_id"], 900)

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
                self.assertEqual([a["type"] for a in second], ["send_message", "delete_message"])
                self.assertEqual(second[1]["message_id"], 910)
                self.assertIn("👥 当前玩家 (2/5):\n• 玩家A\n• 玩家B", second[0]["text"])
                self.assertIn("开始倒计时 15 秒", second[0]["text"])
                self.assertIn("如果没人加入则庄家可以选择直接开局", second[0]["text"])
                self.assertEqual(plugin._games[-100123].phase, "lobby")
                self.assertTrue(plugin._games[-100123].dealer_locked)
                self.assertEqual(plugin._games[-100123].dealer_id, 111)
                self.assertEqual(plugin._games[-100123].ask_dealer_uid, 0)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_join_notice_uses_latest_saved_message_after_stale_previous_game_key(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            messages = FakeMessages()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis, messages=messages)
            join_key = plugin_module._join_notice_key(1, -100123)
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "start_ten_half", keyword_payload())
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
                redis.store[join_key] = "800"

                first = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["message_id"] for a in first if a["type"] == "delete_message"], [800, 900])
                game = plugin._games[-100123]
                self.assertIsNone(game.join_notice_msg_id)

                redis.store[join_key] = "910"
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
                self.assertEqual([a["message_id"] for a in second if a["type"] == "delete_message"], [910])
                self.assertIsNone(game.join_notice_msg_id)

                redis.store[join_key] = "920"
                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._idle_start_prompt_task(-100123, game.started_at, game.lobby_version, ctx)

                prompt_actions = messages.applied[-1]["actions"]
                self.assertEqual(prompt_actions[0]["type"], "edit_message")
                self.assertEqual(prompt_actions[0]["message_id"], 920)
                self.assertIn("th:start_now:111", str(prompt_actions[0]["reply_markup"]))
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

    def test_interaction_idle_prompt_emits_background_start_buttons(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario_fast() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(messages=messages)
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                max_players=5,
                phase="lobby",
                via_interaction=True,
                dealer_id=111,
                dealer_name="玩家A",
                dealer_locked=True,
                started_at=123.0,
                join_notice_msg_id=910,
            )
            game.lobby_players = [(111, "玩家A"), (222, "玩家B")]
            plugin._games[-100123] = game

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._idle_start_prompt_task(-100123, 123.0, 0, ctx)

            self.assertTrue(game.awaiting_start_confirmation)
            self.assertEqual(len(messages.applied), 1)
            self.assertEqual(messages.applied[0]["entry_key"], "start_ten_half")
            actions = messages.applied[0]["actions"]
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertEqual(actions[0]["chat_id"], -100123)
            self.assertEqual(actions[0]["message_id"], 910)
            self.assertIn("th:start_now:111", str(actions[0]["reply_markup"]))
            self.assertIn("15 秒内没有新玩家加入", actions[0]["text"])

        asyncio.run(scenario_fast())

    def test_interaction_lobby_timeout_auto_begins_when_min_players_and_dealer_locked(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario_fast() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(messages=messages)
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                max_players=5,
                phase="lobby",
                via_interaction=True,
                dealer_id=111,
                dealer_name="玩家A",
                dealer_locked=True,
                started_at=123.0,
                main_message_id=900,
            )
            game.lobby_players = [(111, "玩家A"), (222, "玩家B")]
            plugin._games[-100123] = game

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._lobby_timeout_task(-100123, 123.0, ctx)

            self.assertEqual(game.phase, "playing")
            self.assertEqual(len(game.players), 1)
            self.assertEqual(len(messages.applied), 1)
            actions = messages.applied[0]["actions"]
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertEqual(actions[0]["chat_id"], -100123)
            self.assertIn("轮到 玩家B 行动", actions[0]["text"])
            self.assertIn("th:hit:222", str(actions[0]["reply_markup"]))

        asyncio.run(scenario_fast())

    def test_lobby_timeout_locks_first_player_as_dealer_without_prompt(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario_fast() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            logs: list[str] = []

            async def log(_level, message):
                logs.append(message)

            ctx = PluginContext(messages=messages, log=log)
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                max_players=5,
                phase="lobby",
                via_interaction=True,
                dealer_locked=False,
                ask_dealer_uid=111,
                ask_dealer_name="玩家A",
                started_at=123.0,
                main_message_id=900,
            )
            game.lobby_players = [(111, "玩家A"), (222, "玩家B"), (333, "玩家C")]
            plugin._games[-100123] = game

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._lobby_timeout_task(-100123, 123.0, ctx)

            self.assertEqual(game.phase, "playing")
            self.assertTrue(game.dealer_locked)
            self.assertEqual(game.dealer_id, 111)
            self.assertEqual(game.ask_dealer_uid, 0)
            self.assertEqual(len(messages.applied), 1)
            self.assertTrue(any("lobby_timeout_begin" in item for item in logs))

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
                "text": "点点点！啥你都点！",
                "show_alert": True,
            }])

        asyncio.run(scenario())

    def test_stale_turn_button_returns_expired_without_action(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.action_version = 2
            game.players = [
                plugin_module.PlayerHand(user_id=111, name="玩家A", cards=[plugin_module.Card("♠️", "5")]),
            ]
            game.current_player_idx = 0
            game.deck = [plugin_module.Card("♥️", "A")]
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-stale",
                        "callback_data": "th:hit:111:1",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-stale",
                "text": "按钮已过期，请看最新牌桌。",
                "show_alert": False,
            }])
            self.assertEqual(len(game.players[0].cards), 1)
            self.assertEqual(game.phase, "playing")

        asyncio.run(scenario())

    def test_stale_dealer_choice_button_is_rejected(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="ask_dealer", via_interaction=True)
            game.main_message_id = 900
            game.lobby_players = [(111, "玩家A"), (222, "玩家B")]
            game.ask_dealer_uid = 111
            game.ask_dealer_name = "玩家A"
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-dealer",
                        "callback_data": "th:dealer_yes:111",
                    },
                    "actor": {"user_id": 222, "display_name": "玩家B"},
                },
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-dealer",
                "text": "当前不需要选庄，首位加入玩家自动当庄。",
                "show_alert": True,
            }])

        asyncio.run(scenario())

    def test_keyword_lobby_uses_module_max_players_and_first_player_dealer(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload["module_config"] = {"max_players": 3}
                await plugin.on_interaction(ctx, "start_ten_half", payload)
                game = plugin._games[-100123]
                self.assertEqual(game.max_players, 3)

                await plugin.on_interaction(ctx, "start_ten_half", payment_payload(payer_id=111, payer_name="玩家A"))
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(payer_id=222, payer_name="玩家B", notice_message_id=711, reply_message_id=710),
                )
                self.assertEqual(game.phase, "lobby")
                self.assertEqual(game.ask_dealer_uid, 0)
                self.assertEqual(game.dealer_id, 111)
                self.assertTrue(game.dealer_locked)

                third = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(payer_id=333, payer_name="玩家C", notice_message_id=721, reply_message_id=720),
                )
                self.assertEqual(len(game.lobby_players), 3)
                self.assertEqual(game.phase, "playing")
                self.assertEqual([p.user_id for p in game.players], [222, 333])
                self.assertFalse(game.finished)
                self.assertTrue(any("轮到 玩家B 行动" in action.get("text", "") for action in third))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_lobby_prefers_module_config_bet_over_framework_fallback_prize(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload["bet"] = 100
                payload["prize"] = 123
                payload["module_config"] = {"bet": 1000}
                actions = await plugin.on_interaction(ctx, "start_ten_half", payload)

                game = plugin._games[-100123]
                self.assertEqual(game.bet, 1000)
                self.assertIn("底注: <b>1000</b>", actions[0]["text"])

                wrong = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(amount=100),
                )
                self.assertIn("入场金额需为 1000", wrong[0]["text"])

                joined = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(amount=1000),
                )
                self.assertTrue(any("加入牌局成功" in action.get("text", "") for action in joined))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_lobby_accepts_explicit_module_prize(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload["bet"] = 100
                payload["module_prize"] = 1000
                actions = await plugin.on_interaction(ctx, "start_ten_half", payload)

                game = plugin._games[-100123]
                self.assertEqual(game.bet, 1000)
                self.assertIn("底注: <b>1000</b>", actions[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_lobby_ignores_bare_framework_fallback_prize(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload.pop("bet", None)
                payload["prize"] = 123
                actions = await plugin.on_interaction(ctx, "start_ten_half", payload)

                self.assertNotIn(-100123, plugin._games)
                self.assertIn("请指定下注金额", actions[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_command_starts_interaction_lobby_with_userbot_dealer(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, messages=messages, redis=redis)
            redis.store[plugin_module._main_msg_key(1, -100123)] = "599"
            client = FakeCommandClient()
            event = FakeCommandEvent()
            await plugin.on_startup(ctx)
            try:
                await plugin._cmd(client, event, ["100"], 1, ctx)

                game = plugin._games[-100123]
                self.assertTrue(game.via_interaction)
                self.assertTrue(game.dealer_locked)
                self.assertEqual(game.dealer_id, 999)
                self.assertEqual(game.dealer_name, "owner")
                self.assertEqual(game.lobby_players, [(999, "owner")])
                self.assertNotIn(999, game.player_message_ids)
                self.assertEqual(game.host_user_id, 999)
                self.assertEqual(game.max_players, 2)
                self.assertEqual(event.replies, [])
                self.assertEqual(len(messages.applied), 1)
                self.assertEqual(messages.applied[0]["entry_key"], "start_ten_half")
                session_action = messages.applied[0]["actions"][0]
                self.assertEqual(session_action["type"], "start_session")
                self.assertEqual(session_action["entry_key"], "start_ten_half")
                self.assertEqual(session_action["started_by_user_id"], 999)
                self.assertEqual(session_action["paid_user_ids"], [999])
                self.assertEqual(session_action["participant_user_ids"], [999])
                action = messages.applied[0]["actions"][1]
                self.assertEqual(action["type"], "send_message")
                self.assertEqual(action["send_via"], "interaction_bot")
                self.assertEqual(action["reply_to_message_id"], 600)
                self.assertEqual(action["replace_saved_message_id_key"], plugin_module._main_msg_key(1, -100123))
                self.assertIn("十点半开局", action["text"])
                self.assertIn("请转账 <b>100</b> 给 <b>@owner</b>", action["text"])
                self.assertNotIn("本群 userbot", action["text"])
                self.assertIn("👥 已加入 (1/2): owner", action["text"])
                self.assertIn("🎰 庄家: <b>owner</b>", action["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_start_force_sends_new_lobby_message_when_old_main_exists(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, redis=redis)
            redis.store[plugin_module._main_msg_key(1, -100123)] = "599"
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(ctx, "start_ten_half", keyword_payload())

                self.assertEqual(len(actions), 1)
                action = actions[0]
                self.assertEqual(action["type"], "send_message")
                self.assertEqual(action["reply_to_message_id"], 600)
                self.assertEqual(action["save_message_id_key"], plugin_module._main_msg_key(1, -100123))
                self.assertEqual(action["replace_saved_message_id_key"], plugin_module._main_msg_key(1, -100123))
                self.assertIn("十点半开局", action["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_lobby_timeout_with_only_command_dealer_updates_message_and_ends_session(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, messages=messages, redis=redis)
            redis.store[plugin_module._main_msg_key(1, -100123)] = "599"
            await plugin.on_startup(ctx)
            try:
                with patch("asyncio.sleep", fast_sleep):
                    await plugin._cmd(FakeCommandClient(), FakeCommandEvent(), ["100"], 1, ctx)
                    tasks = list(plugin._tasks)
                    if tasks:
                        await asyncio.gather(*tasks)

                self.assertNotIn(-100123, plugin._games)
                self.assertGreaterEqual(len(messages.applied), 2)
                timeout_actions = messages.applied[-1]["actions"]
                self.assertEqual([a["type"] for a in timeout_actions], ["edit_message", "end_session"])
                self.assertEqual(timeout_actions[0]["message_id"], 599)
                self.assertIn("参与人数不足 2 人，牌局已取消", timeout_actions[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_command_game_begins_with_command_dealer_buttons(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, messages=FakeMessages())
            await plugin.on_startup(ctx)
            deck = [
                plugin_module.Card("♣️", "3"),
                plugin_module.Card("♦️", "2"),
                plugin_module.Card("♥️", "8"),
                plugin_module.Card("♠️", "9"),
            ]
            try:
                await plugin._cmd(FakeCommandClient(), FakeCommandEvent(), ["100"], 1, ctx)
                game = plugin._games[-100123]
                game.main_message_id = 900

                with patch.object(plugin_module, "create_deck", return_value=list(deck)):
                    actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload(payer_id=111, payer_name="玩家A"))

                self.assertEqual(game.dealer_id, 999)
                self.assertEqual(game.phase, "playing")
                self.assertEqual([p.user_id for p in game.players], [111])
                self.assertTrue(any("th:hit:111" in str(action.get("reply_markup")) for action in actions))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_command_button_flow_settles_and_rewards(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, messages=FakeMessages())
            await plugin.on_startup(ctx)
            deck = [
                plugin_module.Card("♣️", "3"),
                plugin_module.Card("♦️", "4"),
                plugin_module.Card("♥️", "3"),
                plugin_module.Card("♠️", "9"),
            ]
            try:
                await plugin._cmd(FakeCommandClient(), FakeCommandEvent(), ["100"], 1, ctx)
                game = plugin._games[-100123]
                game.main_message_id = 900

                with patch.object(plugin_module, "create_deck", return_value=list(deck)):
                    await plugin.on_interaction(ctx, "start_ten_half", payment_payload(payer_id=111, payer_name="玩家A"))

                player_actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    {
                        "source": {
                            "type": "callback_query",
                            "chat_id": -100123,
                            "message_id": 900,
                            "callback_query_id": "cb-player-stand",
                            "callback_data": "th:stand:111",
                        },
                        "actor": {"user_id": 111, "display_name": "玩家A"},
                    },
                )
                self.assertEqual(game.phase, "dealer_turn")
                self.assertTrue(any("th:stand:999" in str(action.get("reply_markup")) for action in player_actions))

                final_actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    {
                        "source": {
                            "type": "callback_query",
                            "chat_id": -100123,
                            "message_id": 900,
                            "callback_query_id": "cb-dealer-stand",
                            "callback_data": "th:stand:999",
                        },
                        "actor": {"user_id": 999, "display_name": "owner"},
                    },
                )

                self.assertTrue(any(action.get("type") == "send_message" and "十点半结算" in action.get("text", "") for action in final_actions))
                rewards = [action for action in final_actions if action.get("send_via") == "userbot_reply"]
                self.assertEqual([action["text"] for action in rewards], ["+180"])
                self.assertEqual({action["reply_to_message_id"] for action in rewards}, {700})
                self.assertEqual(final_actions[-1]["type"], "end_session")
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_interaction_message_text_no_longer_advances_turn_actions(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="dealer_turn",
                via_interaction=True,
                dealer_id=999,
                dealer_name="owner",
            )
            game.dealer_cards = [plugin_module.Card("♠️", "3"), plugin_module.Card("♥️", "4")]
            game.deck = [plugin_module.Card("♦️", "A")]
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "event": {"type": "message", "chat_id": -100123, "message_id": 801, "text": "要牌"},
                    "source": {"type": "message", "chat_id": -100123, "message_id": 801},
                    "message": {"chat_id": -100123, "message_id": 801, "text": "要牌"},
                    "actor": {"user_id": 999, "display_name": "owner"},
                },
            )

            self.assertEqual(actions, [])
            self.assertEqual(len(game.dealer_cards), 2)
            self.assertEqual(game.phase, "dealer_turn")

        asyncio.run(scenario())

    def test_interaction_begin_deals_one_card_to_each_player_and_dealer_two(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, via_interaction=True)
            game.lobby_players = [(111, "庄家候选"), (222, "玩家B"), (333, "玩家C")]
            game.main_message_id = 900

            actions = await plugin._ix_begin(-100123, game, 111, "庄家候选", ctx)
            self.assertEqual(game.phase, "playing")
            self.assertEqual(len(game.dealer_cards), 2)
            self.assertEqual([len(p.cards) for p in game.players], [1, 1])
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertIn("玩家先行动", actions[0]["text"])
            self.assertIn("轮到 玩家B 行动", actions[0]["text"])
            self.assertIn("👉 <b>玩家B</b>", actions[0]["text"])

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

    def test_interaction_settlement_rewards_player_dealer_when_all_players_lose(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            game.dealer_id = 111
            game.dealer_name = "玩家A"
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "A")]
            player_b = plugin_module.PlayerHand(user_id=222, name="玩家B")
            player_b.cards = [plugin_module.Card("♠️", "8"), plugin_module.Card("♥️", "A")]
            player_c = plugin_module.PlayerHand(user_id=333, name="玩家C")
            player_c.cards = [plugin_module.Card("♠️", "7"), plugin_module.Card("♥️", "A")]
            game.players = [player_b, player_c]
            game.player_message_ids = {111: 700, 222: 710, 333: 720}

            actions = await plugin._ix_settle(-100123, game, PluginContext())
            rewards = [action for action in actions if action.get("send_via") == "userbot_reply"]

            self.assertEqual([action["text"] for action in rewards], ["+270"])
            self.assertEqual(rewards[0]["reply_to_message_id"], 700)
            self.assertTrue(any("庄家 <b>玩家A</b> 🎉是赢家 获得 <b>270</b>" in action.get("text", "") for action in actions))
            self.assertTrue(any("玩家B</b>: 2张 · 9点 → ❌ 输 100" in action.get("text", "") for action in actions))

        asyncio.run(scenario())

    def test_interaction_settlement_skips_userbot_dealer_reward_without_payment_message(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            game.dealer_id = 999
            game.dealer_name = "owner"
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "A")]
            player = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player.cards = [plugin_module.Card("♠️", "8"), plugin_module.Card("♥️", "A")]
            game.players = [player]
            game.player_message_ids = {111: 700}

            actions = await plugin._ix_settle(-100123, game, PluginContext())
            rewards = [action for action in actions if action.get("send_via") == "userbot_reply"]

            self.assertEqual(rewards, [])
            self.assertTrue(any("庄家 <b>owner</b> 🎉是赢家 获得 <b>180</b>" in action.get("text", "") for action in actions))

        asyncio.run(scenario())

    def test_interaction_settlement_marks_busted_player_without_duplicate_loss_text(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=1000)
            game.dealer_id = 111
            game.dealer_name = "庄家A"
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "A")]
            player = plugin_module.PlayerHand(user_id=222, name="玩家B")
            player.cards = [
                plugin_module.Card("♠️", "9"),
                plugin_module.Card("♥️", "8"),
                plugin_module.Card("♦️", "K"),
            ]
            player.busted = True
            game.players = [player]
            game.player_message_ids = {111: 700, 222: 710}

            actions = await plugin._ix_settle(-100123, game, PluginContext())
            text = next(action["text"] for action in actions if "十点半结算" in action.get("text", ""))

            self.assertIn("玩家B</b>: 3张 · 17.5点 → ❌ 爆牌！输 1000", text)
            self.assertNotIn("损失 1000", text)
            self.assertIn("庄家 <b>庄家A</b> 🎉是赢家 获得 <b>1800</b>", text)

        asyncio.run(scenario())

    def test_settlement_cleanup_deletes_only_bot_and_userbot_messages(self) -> None:
        async def fast_sleep(seconds):
            self.assertEqual(seconds, 15)

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            messages = FakeMessages()
            ctx = PluginContext(redis=redis, messages=messages)
            reward_key = plugin_module._reward_msg_key(1, -100123, "GAME01", 111)
            redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
            redis.store[plugin_module._join_notice_key(1, -100123)] = "910"
            settlement_key = plugin_module._settlement_msg_key(1, -100123, "GAME01")
            redis.store[settlement_key] = "930"
            redis.store[reward_key] = "920"

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._cleanup_game_messages_task(ctx, -100123, None, None, {899}, settlement_key, [reward_key], 15)

            actions = messages.applied[0]["actions"]
            self.assertEqual(
                actions,
                [
                    {"type": "delete_message", "message_id": 899, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 900, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 910, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 930, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 920, "send_via": "userbot_reply", "chat_id": -100123},
                ],
            )
            self.assertNotIn(700, {action["message_id"] for action in actions})
            self.assertNotIn(710, {action["message_id"] for action in actions})

        asyncio.run(scenario())

    def test_multiple_five_small_players_all_receive_double_reward(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            player_a = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player_b = plugin_module.PlayerHand(user_id=222, name="玩家B")
            five_small = [
                plugin_module.Card("♠️", "A"),
                plugin_module.Card("♥️", "A"),
                plugin_module.Card("♦️", "A"),
                plugin_module.Card("♣️", "A"),
                plugin_module.Card("♠️", "A"),
            ]
            player_a.cards = list(five_small)
            player_b.cards = list(five_small)
            game.players = [player_a, player_b]
            game.dealer_cards = [plugin_module.Card("♦️", "4"), plugin_module.Card("♣️", "5")]
            game.player_message_ids = {111: 700, 222: 710}

            actions = await plugin._ix_settle(-100123, game, PluginContext())
            rewards = [action for action in actions if action.get("send_via") == "userbot_reply"]

            self.assertEqual([action["text"] for action in rewards], ["+360", "+360"])
            self.assertEqual({action["reply_to_message_id"] for action in rewards}, {700, 710})

        asyncio.run(scenario())

    def test_hit_resets_player_turn_timeout_version(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(messages=messages)
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="playing",
                via_interaction=True,
                started_at=123.0,
                turn_timeout=8,
                dealer_id=222,
                dealer_name="庄家",
                main_message_id=900,
            )
            player = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player.cards = [plugin_module.Card("♠️", "A")]
            game.players = [player]
            game.dealer_cards = [plugin_module.Card("♦️", "4"), plugin_module.Card("♣️", "5")]
            game.deck = [plugin_module.Card("♥️", "A")]
            plugin._games[-100123] = game

            await plugin._ix_advance(-100123, game, ctx)
            first_version = game.turn_timeout_version
            await plugin._ix_hit(-100123, game, ctx)
            second_version = game.turn_timeout_version

            self.assertEqual(first_version + 1, second_version)
            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._turn_timeout_task(-100123, 0, 123.0, ctx, first_version)
            self.assertFalse(player.stood)
            self.assertEqual(messages.applied, [])

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._turn_timeout_task(-100123, 0, 123.0, ctx, second_version)
            self.assertTrue(player.stood)
            self.assertEqual(game.phase, "dealer_turn")
            self.assertEqual(messages.applied[0]["actions"][0]["type"], "edit_message")
            self.assertIn("轮到庄家", messages.applied[0]["actions"][0]["text"])

        asyncio.run(scenario())

    def test_callback_after_timeout_auto_stand_settles_instead_of_empty_ack(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="playing",
                via_interaction=True,
            )
            game.main_message_id = 900
            game.status_note = "玩家A 超时，自动停牌。"
            player = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player.cards = [plugin_module.Card("♠️", "9"), plugin_module.Card("♥️", "A")]
            player.stood = True
            game.players = [player]
            game.dealer_id = 222
            game.dealer_name = "庄家"
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "10")]
            game.current_player_idx = 1
            game.player_message_ids[111] = 700
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-timeout",
                        "callback_data": "th:stand:111",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertGreater(len(actions), 1)
            self.assertEqual(actions[0]["type"], "answer_callback")
            self.assertIn("庄家回合", actions[0]["text"])
            self.assertEqual(actions[1]["type"], "edit_message")
            self.assertEqual(actions[1]["message_id"], 900)
            self.assertIn("轮到庄家", actions[1]["text"])
            self.assertIn("th:stand:222", str(actions[1]["reply_markup"]))
            self.assertIn(-100123, plugin._games)

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
