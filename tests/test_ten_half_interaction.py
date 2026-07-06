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


def message_payload(
    *,
    user_id: int = 111,
    name: str = "玩家A",
    text: str = "加入",
    message_id: int = 801,
    owner_user_ids: list[int] | None = None,
) -> dict:
    payload = {
        "event": {"type": "message", "chat_id": -100123, "message_id": message_id, "text": text},
        "source": {"type": "message", "chat_id": -100123, "message_id": message_id, "text": text},
        "message": {"chat_id": -100123, "message_id": message_id, "text": text},
        "actor": {"user_id": user_id, "display_name": name},
        "sender_user_id": user_id,
        "sender_name": name,
        "message_text": text,
    }
    if owner_user_ids is not None:
        payload["owner_user_ids"] = list(owner_user_ids)
        payload["admin_user_ids"] = list(owner_user_ids)
        payload["userbot_user_id"] = owner_user_ids[0] if owner_user_ids else None
    return payload


def userbot_plus_payload(
    *,
    user_id: int = 111,
    name: str = "玩家A",
    text: str = "+100",
    message_id: int = 811,
    reply_message_id: int = 600,
) -> dict:
    return {
        "event": {"type": "message", "chat_id": -100123, "message_id": message_id, "text": text},
        "source": {"type": "message", "channel": "userbot", "chat_id": -100123, "message_id": message_id},
        "message": {
            "chat_id": -100123,
            "message_id": message_id,
            "text": text,
            "reply_to_message_id": reply_message_id,
        },
        "reply_to": {"message_id": reply_message_id},
        "actor": {"user_id": user_id, "display_name": name},
        "sender_user_id": user_id,
        "sender_name": name,
        "message_text": text,
    }


def userbot_entry_payload(
    *,
    user_id: int = 999,
    name: str = "owner",
    text: str = "入局",
    message_id: int = 811,
) -> dict:
    return {
        "event": {"type": "message", "chat_id": -100123, "message_id": message_id, "text": text},
        "source": {"type": "message", "channel": "userbot", "chat_id": -100123, "message_id": message_id, "text": text},
        "message": {"chat_id": -100123, "message_id": message_id, "text": text},
        "actor": {"user_id": user_id, "display_name": name},
        "sender_user_id": user_id,
        "sender_name": name,
        "message_text": text,
        "owner_user_ids": [user_id],
        "admin_user_ids": [user_id],
        "userbot_user_id": user_id,
    }


def callback_payload(
    *,
    user_id: int = 111,
    name: str = "玩家A",
    callback_data: str = "th:join:0",
    callback_query_id: str = "cb-join",
    message_id: int = 900,
    owner_user_ids: list[int] | None = None,
) -> dict:
    payload = {
        "source": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": message_id,
            "callback_query_id": callback_query_id,
            "callback_data": callback_data,
        },
        "actor": {"user_id": user_id, "display_name": name},
    }
    if owner_user_ids is not None:
        payload["owner_user_ids"] = list(owner_user_ids)
        payload["admin_user_ids"] = list(owner_user_ids)
        payload["userbot_user_id"] = owner_user_ids[0] if owner_user_ids else None
    return payload


def stake_payload(
    *,
    amount: int = 100,
    user_id: int = 999,
    name: str = "管理员",
    callback_query_id: str = "cb-stake",
    message_id: int = 601,
) -> dict:
    return callback_payload(
        user_id=user_id,
        name=name,
        callback_data=f"th:stake:{amount}",
        callback_query_id=callback_query_id,
        message_id=message_id,
    )


async def start_lobby(
    plugin,
    ctx,
    *,
    amount: int = 100,
    payload: dict | None = None,
    user_id: int = 999,
    name: str = "管理员",
) -> list[dict]:
    start_payload = dict(payload or keyword_payload())
    start_payload.setdefault("stake_options", [amount])
    await plugin.on_interaction(ctx, "start_ten_half", start_payload)
    return await plugin.on_interaction(
        ctx,
        "start_ten_half",
        stake_payload(amount=amount, user_id=user_id, name=name),
    )


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

    async def delete(self, key):
        self.store.pop(key, None)
        return True


class TenHalfInteractionTest(unittest.TestCase):
    def test_card_points_follow_single_card_rules(self) -> None:
        self.assertEqual(plugin_module.Card("♠️", "A").value, 1)
        self.assertEqual(plugin_module.Card("♠️", "10").value, 10)
        self.assertEqual(plugin_module.Card("♠️", "J").value, 0.5)

    def test_compare_keeps_natural_ten_half(self) -> None:
        player = plugin_module.PlayerHand(
            user_id=111,
            name="玩家A",
            cards=[plugin_module.Card("♠️", "10"), plugin_module.Card("♥️", "J")],
        )

        self.assertTrue(player.is_natural)
        self.assertEqual(
            plugin_module.TenHalfPlugin._compare(
                player,
                dealer_val=10.0,
                dealer_busted=False,
                dealer_natural=False,
                dealer_five_small=False,
            ),
            "win_nat",
        )

    def test_compare_five_small_smaller_points_wins(self) -> None:
        player = plugin_module.PlayerHand(
            user_id=111,
            name="玩家A",
            cards=[
                plugin_module.Card("♠️", "A"),
                plugin_module.Card("♥️", "A"),
                plugin_module.Card("♦️", "A"),
                plugin_module.Card("♣️", "A"),
                plugin_module.Card("♠️", "A"),
            ],
        )

        self.assertEqual(
            plugin_module.TenHalfPlugin._compare(
                player,
                dealer_val=6.0,
                dealer_busted=False,
                dealer_natural=False,
                dealer_five_small=True,
            ),
            "win_5s",
        )

    def test_compare_same_points_dealer_wins(self) -> None:
        player = plugin_module.PlayerHand(
            user_id=111,
            name="玩家A",
            cards=[plugin_module.Card("♠️", "9"), plugin_module.Card("♥️", "A")],
        )

        self.assertEqual(
            plugin_module.TenHalfPlugin._compare(
                player,
                dealer_val=10.0,
                dealer_busted=False,
                dealer_natural=False,
                dealer_five_small=False,
            ),
            "lose",
        )

    def test_dealer_public_brief_hides_hidden_card_bust_before_settlement(self) -> None:
        game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
        game.dealer_cards = [
            plugin_module.Card("♠️", "9"),
            plugin_module.Card("♥️", "2"),
        ]

        self.assertTrue(game.dealer_busted())
        self.assertEqual(game.dealer_val(), 11)
        self.assertEqual(
            plugin_module._dealer_public_brief(game),
            "2张（明牌 2点，暗牌 1张）",
        )

    def test_dealer_public_brief_freezes_visible_points_before_visible_bust(self) -> None:
        game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
        game.dealer_cards = [
            plugin_module.Card("♠️", "A"),
            plugin_module.Card("♥️", "9"),
            plugin_module.Card("♦️", "2"),
        ]

        self.assertTrue(game.dealer_busted())
        self.assertEqual(game.dealer_val(), 12)
        self.assertEqual(
            plugin_module._dealer_public_brief(game),
            "3张（明牌 9点，暗牌 1张）",
        )

    def test_dealer_hit_bust_does_not_leak_bust_state_before_settlement(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="playing",
                via_interaction=True,
                dealer_id=333,
                dealer_name="庄家",
                main_message_id=900,
            )
            game.dealer_cards = [plugin_module.Card("♠️", "9")]
            game.deck = [plugin_module.Card("♥️", "2")]
            game.players = [
                plugin_module.PlayerHand(
                    user_id=111,
                    name="玩家A",
                    cards=[plugin_module.Card("♣️", "5")],
                )
            ]

            actions = await plugin._ix_dealer_hit(-100123, game, ctx)

            self.assertTrue(game.dealer_busted())
            self.assertEqual(game.dealer_val(), 11)
            self.assertEqual(game.status_note, "庄家 已要牌，当前 2张，明牌 2点。")
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertIn("庄家 <b>庄家</b>: 2张（明牌 2点，暗牌 1张）", actions[0]["text"])
            self.assertNotIn("庄家 要牌后爆牌", actions[0]["text"])
            self.assertNotIn("11点", actions[0]["text"])

        asyncio.run(scenario())

    def test_userbot_command_only_returns_migration_hint(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            event = FakeCommandEvent()

            await plugin._cmd(FakeCommandClient(), event, [], 1, PluginContext())

            self.assertEqual(len(event.replies), 1)
            self.assertIn("只通过交互 Bot 关键词/规则开局", event.replies[0]["text"])
            self.assertIn("发送「入局」直接加入", event.replies[0]["text"])
            self.assertEqual(plugin._games, {})

        asyncio.run(scenario())

    def test_userbot_command_is_not_registered_on_startup(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"command": "。10d"})
            await plugin.on_startup(ctx)
            try:
                self.assertEqual(plugin.commands, {})
                self.assertNotIn("。10d", plugin.commands)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_runtime_config_defaults_and_overrides_message_timers(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            default_ctx = PluginContext()
            await plugin.on_startup(default_ctx)
            try:
                self.assertEqual(plugin._turn_timeout, 45)
                self.assertEqual(plugin._settlement_cleanup_delay, 60)
            finally:
                await plugin.on_shutdown(default_ctx)

            plugin = plugin_module.TenHalfPlugin()
            custom_ctx = PluginContext(config={"timeout": 12, "settlement_cleanup_delay": 90})
            await plugin.on_startup(custom_ctx)
            try:
                self.assertEqual(plugin._turn_timeout, 12)
                self.assertEqual(plugin._settlement_cleanup_delay, 90)
            finally:
                await plugin.on_shutdown(custom_ctx)

        asyncio.run(scenario())

    def test_saved_message_id_reads_platform_namespaced_key(self) -> None:
        async def scenario() -> None:
            redis = FakeRedis()
            ctx = PluginContext(account_id=1, redis=redis)
            key = plugin_module._main_msg_key(1, -100123)
            redis.store[f"tp:msgid:1:{key}"] = "900"

            plugin = plugin_module.TenHalfPlugin()
            self.assertEqual(await plugin._read_saved_message_id(ctx, key), 900)

        asyncio.run(scenario())

    def test_payment_join_existing_keyword_lobby_does_not_duplicate_lobby_message(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                start_actions = await start_lobby(plugin, ctx)
                start_message = next(action for action in start_actions if action["type"] == "send_message")
                self.assertIn("十点半开局", start_message["text"])
                self.assertIn("当前牌桌 ID", start_message["text"])
                self.assertIn("save_message_id_key", start_message)
                self.assertIn("th:rules:0", str(start_message["reply_markup"]))
                self.assertNotIn("th:join:0", str(start_message["reply_markup"]))
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                join_actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["type"] for a in join_actions], ["send_message", "start_session"])
                join_message = next(action for action in join_actions if action["type"] == "send_message")
                self.assertIn("加入牌局成功", join_message["text"])
                self.assertIn("牌桌 ID", join_message["text"])
                self.assertNotIn("十点半开局", join_message["text"])
                self.assertIn("👥 当前玩家 (1/5):\n• 玩家A", join_message["text"])
                self.assertEqual(
                    join_message["save_message_id_key"],
                    plugin_module._join_notice_key(1, -100123),
                )
                session_action = next(action for action in join_actions if action["type"] == "start_session")
                self.assertEqual(session_action["paid_user_ids"], [111])
                self.assertEqual(session_action["participant_user_ids"], [111])
                self.assertIn("ten_half_lobby", session_action["data"])

                game = plugin._games[-100123]
                self.assertEqual(game.player_message_ids[111], 700)
                self.assertFalse(game.opening_message_deleted)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_join_keeps_opening_and_deletes_previous_join_notice(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                first = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["type"] for a in first], ["send_message", "start_session"])
                self.assertFalse(plugin._games[-100123].opening_message_deleted)

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
                self.assertEqual([a["type"] for a in second], ["send_message", "delete_message", "start_session"])
                delete_action = next(action for action in second if action["type"] == "delete_message")
                send_action = next(action for action in second if action["type"] == "send_message")
                session_action = next(action for action in second if action["type"] == "start_session")
                self.assertEqual(delete_action["message_id"], 910)
                self.assertIn("👥 当前玩家 (2/5):\n• 玩家A\n• 玩家B", send_action["text"])
                self.assertIn("开始倒计时 15 秒", send_action["text"])
                self.assertIn("如果没人加入则庄家可以选择直接开局", send_action["text"])
                self.assertEqual(session_action["paid_user_ids"], [111, 222])
                self.assertEqual(plugin._games[-100123].phase, "lobby")
                self.assertTrue(plugin._games[-100123].dealer_locked)
                self.assertEqual(plugin._games[-100123].dealer_id, 111)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_join_notice_auto_deletes_after_ten_seconds(self) -> None:
        async def fast_sleep(seconds):
            self.assertEqual(seconds, plugin_module.JOIN_NOTICE_AUTO_DELETE_DELAY_SECONDS)

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            messages = FakeMessages()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis, messages=messages)
            join_key = plugin_module._join_notice_key(1, -100123)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
                await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                redis.store[join_key] = "910"
                game = plugin._games[-100123]
                notice_version = game.join_notice_version

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._join_notice_cleanup_task(
                        -100123,
                        game.started_at,
                        notice_version,
                        ctx,
                        plugin_module.JOIN_NOTICE_AUTO_DELETE_DELAY_SECONDS,
                    )

                self.assertEqual(
                    messages.applied[0]["actions"],
                    [{"type": "delete_message", "message_id": 910, "send_via": "interaction_bot", "chat_id": -100123}],
                )
                self.assertNotIn(join_key, redis.store)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_join_notice_cleanup_does_not_delete_newer_notice(self) -> None:
        async def fast_sleep(seconds):
            self.assertEqual(seconds, plugin_module.JOIN_NOTICE_AUTO_DELETE_DELAY_SECONDS)

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            messages = FakeMessages()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis, messages=messages)
            join_key = plugin_module._join_notice_key(1, -100123)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
                await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                game = plugin._games[-100123]
                stale_version = game.join_notice_version
                redis.store[join_key] = "910"

                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(
                        payer_id=222,
                        payer_name="玩家B",
                        notice_message_id=711,
                        reply_message_id=710,
                    ),
                )
                redis.store[join_key] = "920"

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._join_notice_cleanup_task(
                        -100123,
                        game.started_at,
                        stale_version,
                        ctx,
                        plugin_module.JOIN_NOTICE_AUTO_DELETE_DELAY_SECONDS,
                    )

                self.assertEqual(messages.applied, [])
                self.assertEqual(redis.store[join_key], "920")
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_join_restores_keyword_lobby_from_persisted_state(self) -> None:
        async def scenario() -> None:
            redis = FakeRedis()
            starter = plugin_module.TenHalfPlugin()
            starter_ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await starter.on_startup(starter_ctx)
            try:
                await start_lobby(starter, starter_ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
            finally:
                await starter.on_shutdown(starter_ctx)

            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())

                self.assertEqual([action["type"] for action in actions], ["send_message", "start_session"])
                send_action = next(action for action in actions if action["type"] == "send_message")
                session_action = next(action for action in actions if action["type"] == "start_session")
                self.assertIn("加入牌局成功", send_action["text"])
                self.assertEqual(session_action["paid_user_ids"], [111])
                self.assertEqual(session_action["participant_user_ids"], [111])
                self.assertIn("ten_half_lobby", session_action["data"])

                game = plugin._games[-100123]
                self.assertEqual(game.bet, 100)
                self.assertEqual(game.lobby_players, [(111, "玩家A")])
                self.assertEqual(game.dealer_id, 111)
                self.assertEqual(game.player_message_ids[111], 700)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_regular_message_join_is_ignored(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    message_payload(user_id=111, name="玩家A", text="加入"),
                )

                self.assertEqual(actions, [])
                self.assertEqual(plugin._games[-100123].lobby_players, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_userbot_plus_amount_message_no_longer_joins_lobby(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    userbot_plus_payload(),
                )

                self.assertEqual(actions, [])
                self.assertEqual(plugin._games[-100123].lobby_players, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_plus_amount_from_interaction_bot_echo_is_ignored(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    message_payload(user_id=111, name="玩家A", text="+100"),
                )

                self.assertEqual(actions, [])
                self.assertEqual(plugin._games[-100123].lobby_players, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_userbot_plus_mismatched_amount_does_not_join(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    userbot_plus_payload(text="+99"),
                )

                self.assertEqual(actions, [])
                self.assertEqual(plugin._games[-100123].lobby_players, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_regular_button_join_still_requires_transfer(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-normal-join"),
                )

                self.assertEqual(actions, [{
                    "type": "answer_callback",
                    "callback_query_id": "cb-normal-join",
                    "text": "请转账底注加入；管理员可发送「入局」直接入桌。",
                    "show_alert": True,
                }])
                self.assertEqual(plugin._games[-100123].lobby_players, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_userbot_message_join_is_transfer_exempt_and_syncs_session(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    message_payload(user_id=999, name="owner", text="入局", owner_user_ids=[999]),
                )

                game = plugin._games[-100123]
                self.assertEqual(game.lobby_players, [(999, "owner")])
                self.assertTrue(game.dealer_locked)
                self.assertEqual(game.dealer_id, 999)
                self.assertTrue(any("入场金额: 免转账" in action.get("text", "") for action in actions))
                session_action = next(action for action in actions if action.get("type") == "start_session")
                self.assertEqual(session_action["paid_user_ids"], [999])
                self.assertEqual(session_action["participant_user_ids"], [999])
                self.assertEqual(session_action["started_by_user_id"], 999)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_lobby_userbot_button_join_still_requires_transfer(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(
                        user_id=999,
                        name="owner",
                        callback_query_id="cb-userbot-join",
                        owner_user_ids=[999],
                    ),
                )

                game = plugin._games[-100123]
                self.assertEqual(game.lobby_players, [])
                self.assertEqual(actions, [{
                    "type": "answer_callback",
                    "callback_query_id": "cb-userbot-join",
                    "text": "请转账底注加入；管理员可发送「入局」直接入桌。",
                    "show_alert": True,
                }])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_mode_command_toggles_join_mode(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(redis=redis)
            await plugin.on_startup(ctx)
            try:
                first = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    userbot_entry_payload(text="10d 模式"),
                )
                self.assertEqual(plugin._join_mode, plugin_module.JOIN_MODE_SILENT_DEBIT)
                self.assertEqual(redis.store[plugin_module._join_mode_key(1)], plugin_module.JOIN_MODE_SILENT_DEBIT)
                self.assertEqual(first[0]["send_via"], "userbot_reply")
                self.assertIn("无感模式", first[0]["text"])
                self.assertIn("save_message_id_key", first[0])

                second = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    userbot_entry_payload(text="10d模式 转账"),
                )
                self.assertEqual(plugin._join_mode, plugin_module.JOIN_MODE_TRANSFER)
                self.assertIn("转账模式", second[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_lobby_start_warns_button_will_debit(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                actions = await start_lobby(plugin, ctx)
                start_message = next(action for action in actions if action["type"] == "send_message")

                self.assertIn("入局模式: <b>无感模式</b>", start_message["text"])
                self.assertIn("【扣款 100 并加入】按钮完成扣款即可加入本桌", start_message["text"])
                self.assertIn("⚠️会被自动扣款哦", start_message["text"])
                self.assertNotIn("<code>-100</code>", start_message["text"])
                self.assertNotIn("请转账 <b>100</b>", start_message["text"])
                self.assertIn("我同意被 扣款 100 后加入牌局", str(start_message["reply_markup"]))
                self.assertIn("th:rules:0", str(start_message["reply_markup"]))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_free_lobby_text_does_not_offer_text_join(self) -> None:
        plugin = plugin_module.TenHalfPlugin()
        game = plugin_module.TenHalfGame(chat_id=-100123, bet=0, phase="lobby", via_interaction=True)

        text = plugin._build_lobby_text(game, "本群 userbot")

        self.assertIn("点击下方按钮即可参与本桌牌局", text)
        self.assertNotIn("发送「加入」", text)

    def test_normal_user_mode_command_does_not_toggle_join_mode(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(redis=redis)
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    message_payload(user_id=111, name="玩家A", text="10d 模式"),
                )

                self.assertEqual(actions, [{"type": "no_session"}])
                self.assertEqual(plugin._join_mode, plugin_module.JOIN_MODE_TRANSFER)
                self.assertNotIn(plugin_module._join_mode_key(1), redis.store)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_button_requests_userbot_debit_without_joining(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )

                game = plugin._games[-100123]
                self.assertEqual(game.join_mode, plugin_module.JOIN_MODE_SILENT_DEBIT)
                self.assertEqual(game.lobby_players, [])
                self.assertNotIn(111, game.player_message_ids)
                self.assertEqual(game.pending_debits[111]["amount"], 100)
                self.assertEqual(game.pending_debits[111]["name"], "玩家A")

                self.assertEqual(len(actions), 1)
                debit = actions[0]
                self.assertEqual(debit["type"], "send_message")
                self.assertEqual(debit["send_via"], "userbot_reply")
                self.assertEqual(debit["text"], "-100")
                self.assertEqual(debit["reply_to_user_id"], 111)
                self.assertEqual(debit["reply_anchor_missing_text"], "无法扣款，加入失败。")
                self.assertTrue(debit["suppress_reply_anchor_missing_notice"])
                self.assertIn("save_message_id_key", debit)
                self.assertEqual(debit["failure_callback"]["text"], "无法扣款，加入失败。")
                self.assertEqual(debit["failure_callback"]["error_code"], "reply_anchor_missing")
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_duplicate_join_click_does_not_charge_twice(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                first = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )
                second = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join-2", message_id=900),
                )

                game = plugin._games[-100123]
                self.assertEqual(list(game.pending_debits), [111])
                self.assertEqual(first[0]["type"], "send_message")
                self.assertEqual(second, [{
                    "type": "answer_callback",
                    "callback_query_id": "cb-silent-join-2",
                    "text": "扣款处理中，请稍等。",
                    "show_alert": True,
                }])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_expired_pending_can_retry_join_click(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )
                game = plugin._games[-100123]
                game.pending_debits[111]["requested_at"] = plugin_module.time.time() - plugin_module.PENDING_DEBIT_TTL_SECONDS - 1

                retry = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join-retry", message_id=900),
                )

                self.assertEqual(retry[0]["type"], "send_message")
                self.assertEqual(retry[0]["text"], "-100")
                self.assertEqual(game.pending_debits[111]["amount"], 100)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_payment_notice_joins_after_debit_confirmed(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )
                debit_notice = payment_payload()
                debit_notice["payment"] = {"direction": "debit"}
                actions = await plugin.on_interaction(ctx, "start_ten_half", debit_notice)

                game = plugin._games[-100123]
                self.assertEqual(game.lobby_players, [(111, "玩家A")])
                self.assertNotIn(111, game.player_message_ids)
                self.assertNotIn(111, game.pending_debits)

                join_notice = next(action for action in actions if action.get("type") == "send_message" and action.get("send_via") == "interaction_bot")
                self.assertIn("入场金额: 自动扣款 100", join_notice["text"])
                session_action = next(action for action in actions if action.get("type") == "start_session")
                self.assertEqual(session_action["paid_user_ids"], [111])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_anonymous_notice_uses_single_pending_player(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=5843467471, name="ㅤㅤ", callback_query_id="cb-silent-blank", message_id=900),
                )
                debit_notice = payment_payload(payer_id=1682400007, payer_name="匿名用户")
                debit_notice["payment"] = {
                    "direction": "debit",
                    "payer_user_id": 1682400007,
                    "payer_name": "匿名用户",
                }

                actions = await plugin.on_interaction(ctx, "start_ten_half", debit_notice)

                game = plugin._games[-100123]
                self.assertEqual(game.lobby_players, [(5843467471, "ㅤㅤ")])
                self.assertEqual(game.paid_stakes, {5843467471: 100})
                self.assertNotIn(5843467471, game.pending_debits)
                session_action = next(action for action in actions if action.get("type") == "start_session")
                self.assertEqual(session_action["paid_user_ids"], [5843467471])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_notice_corrects_wrong_platform_payer_from_pending(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )
                debit_notice = payment_payload(payer_id=999, payer_name="玩家A")
                debit_notice["payment"] = {"direction": "debit", "payer_user_id": 999, "payer_name": "玩家A"}

                actions = await plugin.on_interaction(ctx, "start_ten_half", debit_notice)

                game = plugin._games[-100123]
                self.assertEqual(game.lobby_players, [(111, "玩家A")])
                self.assertEqual(game.paid_stakes, {111: 100})
                self.assertNotIn(999, game.paid_stakes)
                session_action = next(action for action in actions if action.get("type") == "start_session")
                self.assertEqual(session_action["paid_user_ids"], [111])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_lobby_refresh_keeps_join_button_after_first_player(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60}, messages=messages)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )
                debit_notice = payment_payload()
                debit_notice["payment"] = {"direction": "debit"}
                await plugin.on_interaction(ctx, "start_ten_half", debit_notice)
                game = plugin._games[-100123]
                version = game.lobby_version

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._lobby_main_refresh_task(-100123, game.started_at, version, ctx, 0)

                self.assertTrue(messages.applied)
                action = messages.applied[-1]["actions"][0]
                self.assertEqual(action["type"], "edit_message")
                self.assertEqual(action["message_id"], 900)
                self.assertIn("我同意被 扣款 100 后加入牌局", str(action["reply_markup"]))
                self.assertIn("th:join:0", str(action["reply_markup"]))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_silent_debit_lobby_ignores_payment_confirmed_notice(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 5, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())

                self.assertEqual(actions, [{"type": "no_session"}])
                self.assertEqual(plugin._games[-100123].lobby_players, [])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_join_background_refresh_uses_latest_lobby_version(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            messages = FakeMessages()
            ctx = PluginContext(config={"max_players": 5, "lobby_timeout": 60}, redis=redis, messages=messages)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(
                        payer_id=222,
                        payer_name="玩家B",
                        notice_message_id=711,
                        reply_message_id=710,
                    ),
                )
                game = plugin._games[-100123]
                latest_version = game.lobby_version

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._lobby_main_refresh_task(-100123, game.started_at, latest_version - 1, ctx, 0.75)
                self.assertEqual(messages.applied, [])

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._lobby_main_refresh_task(-100123, game.started_at, latest_version, ctx, 0.75)

                actions = messages.applied[-1]["actions"]
                self.assertEqual(actions[0]["type"], "edit_message")
                self.assertEqual(actions[0]["message_id"], 900)
                self.assertIn("👥 已加入 (2/5): 玩家A、玩家B", actions[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_display_messages_trim_long_player_names_without_changing_metadata(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            dealer_name = "庄家一二三四五六七八九十十一"
            player_name = "玩家一二三四五六七八九十十一"
            payer_name = "付款人一二三四五六七八九十十一"
            dealer_display = plugin_module._display_name(dealer_name)
            player_display = plugin_module._display_name(player_name)
            payer_display = plugin_module._display_name(payer_name)

            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                dealer_id=111,
                dealer_name=dealer_name,
                dealer_locked=True,
            )
            game.lobby_players = [(111, dealer_name), (222, player_name)]

            lobby_text = plugin._build_lobby_text(game, "本群 userbot")
            join_text = plugin._build_join_notice_text(game, payer_name=payer_name, amount=100)
            self.assertIn(dealer_display, lobby_text)
            self.assertIn(player_display, lobby_text)
            self.assertIn(payer_display, join_text)
            self.assertNotIn(dealer_name, lobby_text)
            self.assertNotIn(player_name, lobby_text)
            self.assertNotIn(payer_name, join_text)

            game.players = [plugin_module.PlayerHand(user_id=222, name=player_name)]
            game.players[0].cards = [plugin_module.Card("♠️", "9"), plugin_module.Card("♥️", "A")]
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "10")]
            game.player_message_ids[222] = 700
            actions = await plugin._ix_settle(-100123, game, ctx)
            settlement_text = next(action["text"] for action in actions if "十点半结算" in action.get("text", ""))
            result_action = next(action for action in actions if action.get("type") == "result")

            self.assertIn(dealer_display, settlement_text)
            self.assertIn(player_display, settlement_text)
            self.assertNotIn(dealer_name, settlement_text)
            self.assertNotIn(player_name, settlement_text)
            self.assertEqual(result_action["result"]["players"][0]["name"], player_name)

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
                await start_lobby(plugin, ctx)
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
                redis.store[join_key] = "800"

                first = await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                self.assertEqual([a["message_id"] for a in first if a["type"] == "delete_message"], [800])
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

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._idle_start_prompt_task(-100123, game.started_at, game.lobby_version, ctx)

                prompt_actions = messages.applied[-1]["actions"]
                self.assertEqual(prompt_actions[0]["type"], "edit_message")
                self.assertEqual(prompt_actions[0]["message_id"], 900)
                self.assertIn("th:start_now:111", str(prompt_actions[0]["reply_markup"]))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

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
                main_message_id=900,
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
            self.assertEqual(actions[0]["message_id"], 900)
            self.assertIn("th:start_now:111", str(actions[0]["reply_markup"]))
            self.assertIn("15 秒内没有新玩家加入", actions[0]["text"])

        asyncio.run(scenario_fast())

    def test_non_dealer_start_decision_only_acknowledges_callback(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
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
            game.awaiting_start_confirmation = True
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                callback_payload(
                    user_id=222,
                    name="玩家B",
                    callback_data="th:start_now:111",
                    callback_query_id="cb-start-wrong-user",
                    message_id=900,
                ),
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-start-wrong-user",
                "text": "只有庄家可以决定是否开局。",
                "show_alert": True,
            }])
            self.assertEqual(game.phase, "lobby")
            self.assertEqual(game.main_message_id, 900)

        asyncio.run(scenario())

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
            deck = [
                plugin_module.Card("♣️", "4"),
                plugin_module.Card("♦️", "5"),
                plugin_module.Card("♠️", "6"),
            ]

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep), patch.object(plugin_module, "create_deck", return_value=list(deck)):
                await plugin._lobby_timeout_task(-100123, 123.0, ctx)

            self.assertEqual(game.phase, "playing")
            self.assertEqual(len(game.players), 1)
            self.assertEqual(len(messages.applied), 1)
            actions = messages.applied[0]["actions"]
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertEqual(actions[0]["chat_id"], -100123)
            self.assertIn("所有人共用下方按钮", actions[0]["text"])
            self.assertIn("等待：玩家B、玩家A", actions[0]["text"])
            self.assertIn("th:hit:0", str(actions[0]["reply_markup"]))
            self.assertNotIn("th:hit:222", str(actions[0]["reply_markup"]))
            self.assertNotIn("th:hit:111", str(actions[0]["reply_markup"]))

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
            self.assertEqual(len(game.dealer_cards), 1)
            self.assertEqual([len(p.cards) for p in game.players], [1, 1])
            self.assertNotIn("end_session", [action["type"] for action in actions])
            self.assertEqual(actions[-1]["type"], "edit_message")
            self.assertIn("所有人共用下方按钮", actions[-1]["text"])
            self.assertIn("等待：玩家A、玩家B", actions[-1]["text"])
            self.assertIn("reply_markup", actions[-1])
            self.assertIn("th:hit:0", str(actions[-1]["reply_markup"]))
            self.assertNotIn("th:hit:111", str(actions[-1]["reply_markup"]))
            self.assertNotIn("th:hit:222", str(actions[-1]["reply_markup"]))

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
                "text": "这不是你的操作按钮。",
                "show_alert": True,
            }])

        asyncio.run(scenario())

    def test_rules_button_only_answers_callback(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="lobby",
                via_interaction=True,
                join_mode=plugin_module.JOIN_MODE_TRANSFER,
            )
            game.main_message_id = 900
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-rules",
                        "callback_data": "th:rules:0",
                    },
                    "actor": {"user_id": 222, "display_name": "玩家B"},
                },
            )

            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["type"], "answer_callback")
            self.assertEqual(actions[0]["callback_query_id"], "cb-rules")
            self.assertTrue(actions[0]["show_alert"])
            self.assertIn("2-10按牌面", actions[0]["text"])
            self.assertIn("天生十点半", actions[0]["text"])
            self.assertEqual(game.lobby_players, [])
            self.assertEqual(game.phase, "lobby")

        asyncio.run(scenario())

    def test_parallel_player_can_act_without_waiting_for_turn_order(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.dealer_id = 333
            game.dealer_name = "庄家"
            game.dealer_stood = True
            game.dealer_cards = [plugin_module.Card("♣️", "4"), plugin_module.Card("♦️", "5")]
            game.players = [
                plugin_module.PlayerHand(user_id=111, name="玩家A", cards=[plugin_module.Card("♠️", "5")]),
                plugin_module.PlayerHand(user_id=222, name="玩家B", cards=[plugin_module.Card("♥️", "6")]),
            ]
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-b-stand",
                        "callback_data": "th:stand:0",
                    },
                    "actor": {"user_id": 222, "display_name": "玩家B"},
                },
            )

            self.assertTrue(game.players[1].stood)
            self.assertFalse(game.players[0].stood)
            self.assertEqual(game.phase, "playing")
            self.assertEqual(actions[0]["type"], "answer_callback")
            self.assertIn("已停牌", actions[0]["text"])
            self.assertEqual(actions[1]["type"], "edit_message")
            self.assertIn("等待：玩家A", actions[1]["text"])
            self.assertIn("th:hit:0", str(actions[1]["reply_markup"]))
            self.assertNotIn("th:hit:222", str(actions[1]["reply_markup"]))

        asyncio.run(scenario())

    def test_player_can_double_after_second_card_in_silent_debit_mode(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="playing",
                via_interaction=True,
                join_mode=plugin_module.JOIN_MODE_SILENT_DEBIT,
            )
            game.main_message_id = 900
            game.dealer_id = 333
            game.dealer_name = "庄家"
            game.dealer_cards = [plugin_module.Card("♣️", "4")]
            player = plugin_module.PlayerHand(
                user_id=111,
                name="玩家A",
                cards=[plugin_module.Card("♠️", "5"), plugin_module.Card("♦️", "4")],
            )
            game.players = [player]
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
                        "callback_query_id": "cb-double",
                        "callback_data": "th:double:0",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertTrue(player.doubled)
            self.assertTrue(player.stood)
            self.assertEqual(player.stake, 200)
            self.assertEqual(game.paid_stakes[111], 200)
            self.assertEqual([card.rank for card in player.cards], ["5", "4", "A"])
            self.assertEqual(actions[0]["type"], "answer_callback")
            self.assertIn("加倍扣款 100，要到 A", actions[0]["text"])
            self.assertEqual(actions[1]["type"], "send_message")
            self.assertEqual(actions[1]["send_via"], plugin_module.USERBOT_SEND_VIA)
            self.assertEqual(actions[1]["text"], "-100")
            self.assertEqual(actions[1]["reply_to_user_id"], 111)
            self.assertEqual(actions[2]["type"], "edit_message")
            self.assertIn("已加倍", actions[2]["text"])

        asyncio.run(scenario())

    def test_transfer_mode_double_does_not_auto_debit_or_change_state(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(
                chat_id=-100123,
                bet=100,
                phase="playing",
                via_interaction=True,
                join_mode=plugin_module.JOIN_MODE_TRANSFER,
            )
            game.main_message_id = 900
            game.dealer_id = 333
            game.dealer_name = "庄家"
            game.dealer_cards = [plugin_module.Card("♣️", "4")]
            player = plugin_module.PlayerHand(
                user_id=111,
                name="玩家A",
                cards=[plugin_module.Card("♠️", "5"), plugin_module.Card("♦️", "4")],
                stake=100,
            )
            game.players = [player]
            game.deck = [plugin_module.Card("♥️", "A")]
            game.paid_stakes[111] = 100
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-double-transfer",
                        "callback_data": "th:double:0",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-double-transfer",
                "text": "转账模式不会自动扣款，本局暂不支持按钮加倍；可切换无感模式后再开局。",
                "show_alert": True,
            }])
            self.assertFalse(player.doubled)
            self.assertFalse(player.stood)
            self.assertEqual(player.stake, 100)
            self.assertEqual(game.paid_stakes[111], 100)
            self.assertEqual([card.rank for card in player.cards], ["5", "4"])
            self.assertEqual([card.rank for card in game.deck], ["A"])

        asyncio.run(scenario())

    def test_player_cannot_double_before_second_card(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.dealer_id = 333
            game.dealer_name = "庄家"
            game.dealer_cards = [plugin_module.Card("♣️", "4")]
            player = plugin_module.PlayerHand(
                user_id=111,
                name="玩家A",
                cards=[plugin_module.Card("♠️", "5")],
            )
            game.players = [player]
            game.deck = [plugin_module.Card("♦️", "A")]
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-double-late",
                        "callback_data": "th:double:0",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertFalse(player.doubled)
            self.assertEqual(len(player.cards), 1)
            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-double-late",
                "text": "加倍只能在已有 2 张牌时使用。",
                "show_alert": False,
            }])

        asyncio.run(scenario())

    def test_parallel_dealer_can_stand_before_players_and_waits_for_all(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.dealer_id = 333
            game.dealer_name = "庄家"
            game.dealer_cards = [plugin_module.Card("♣️", "4"), plugin_module.Card("♦️", "5")]
            game.players = [
                plugin_module.PlayerHand(user_id=111, name="玩家A", cards=[plugin_module.Card("♠️", "5")]),
            ]
            plugin._games[-100123] = game

            dealer_actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-dealer-stand",
                        "callback_data": "th:stand:0",
                    },
                    "actor": {"user_id": 333, "display_name": "庄家"},
                },
            )

            self.assertTrue(game.dealer_stood)
            self.assertEqual(game.phase, "playing")
            self.assertIn(-100123, plugin._games)
            self.assertEqual(dealer_actions[0]["type"], "answer_callback")
            self.assertEqual(dealer_actions[1]["type"], "edit_message")
            self.assertIn("等待：玩家A", dealer_actions[1]["text"])
            self.assertIn("th:stand:0", str(dealer_actions[1]["reply_markup"]))
            self.assertNotIn("th:stand:333", str(dealer_actions[1]["reply_markup"]))

            player_actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-player-stand",
                        "callback_data": "th:stand:0",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertTrue(any(action.get("type") == "send_message" and "十点半结算" in action.get("text", "") for action in player_actions))
            self.assertEqual(player_actions[-1]["type"], "end_session")
            self.assertNotIn(-100123, plugin._games)

        asyncio.run(scenario())

    def test_unpaid_user_cannot_operate_unified_button(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.dealer_id = 333
            game.dealer_name = "庄家"
            game.dealer_stood = True
            game.players = [
                plugin_module.PlayerHand(user_id=111, name="玩家A", cards=[plugin_module.Card("♠️", "5")]),
            ]
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-fake",
                        "callback_data": "th:hit:0",
                    },
                    "actor": {"user_id": 444, "display_name": "路人"},
                },
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-fake",
                "text": "你不在本轮付费玩家列表中。",
                "show_alert": True,
            }])
            self.assertEqual(len(game.players[0].cards), 1)
            self.assertEqual(game.phase, "playing")

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

    def test_done_player_can_still_view_own_hand_with_unified_button(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="playing", via_interaction=True)
            game.main_message_id = 900
            game.players = [
                plugin_module.PlayerHand(
                    user_id=111,
                    name="玩家A",
                    cards=[plugin_module.Card("♠️", "5"), plugin_module.Card("♥️", "4")],
                    stood=True,
                ),
                plugin_module.PlayerHand(user_id=222, name="玩家B", cards=[plugin_module.Card("♦️", "3")]),
            ]
            plugin._games[-100123] = game

            actions = await plugin.on_interaction(
                ctx,
                "start_ten_half",
                {
                    "source": {
                        "type": "callback_query",
                        "chat_id": -100123,
                        "message_id": 900,
                        "callback_query_id": "cb-view",
                        "callback_data": "th:view:0",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-view",
                "text": "你的手牌：5 4 = 9点",
                "show_alert": True,
            }])

        asyncio.run(scenario())

    def test_stale_dealer_choice_button_is_rejected(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, phase="ask_dealer", via_interaction=True)
            game.main_message_id = 900
            game.lobby_players = [(111, "玩家A"), (222, "玩家B")]
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
                await start_lobby(plugin, ctx, payload=payload)
                game = plugin._games[-100123]
                self.assertEqual(game.max_players, 3)

                await plugin.on_interaction(ctx, "start_ten_half", payment_payload(payer_id=111, payer_name="玩家A"))
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(payer_id=222, payer_name="玩家B", notice_message_id=711, reply_message_id=710),
                )
                self.assertEqual(game.phase, "lobby")
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
                self.assertTrue(any("所有人共用下方按钮" in action.get("text", "") for action in third))
                self.assertTrue(any("th:hit:0" in str(action.get("reply_markup")) for action in third))
                self.assertFalse(any("th:hit:222" in str(action.get("reply_markup")) for action in third))
                self.assertFalse(any("th:hit:333" in str(action.get("reply_markup")) for action in third))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_start_uses_configured_stake_options_before_lobby(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload["bet"] = 100
                payload["prize"] = 123
                payload["module_config"] = {"stake_options": [1000, 2000]}
                actions = await plugin.on_interaction(ctx, "start_ten_half", payload)

                game = plugin._games[-100123]
                self.assertEqual(game.phase, "select_bet")
                self.assertEqual(game.bet, 0)
                self.assertEqual(game.stake_options, [1000, 2000])
                select_message = next(action for action in actions if action["type"] == "send_message")
                self.assertIn("请选择要开局的底注额度", select_message["text"])
                callbacks = [
                    button["callback_data"]
                    for row in select_message["reply_markup"]["inline_keyboard"]
                    for button in row
                ]
                self.assertEqual(callbacks, ["th:stake:1000", "th:stake:2000"])

                rejected = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    stake_payload(amount=1000, user_id=111, name="玩家A"),
                )
                self.assertEqual(rejected, [{
                    "type": "answer_callback",
                    "callback_query_id": "cb-stake",
                    "text": "只有发起开局的人可以选择本局底注。",
                    "show_alert": True,
                }])
                self.assertEqual(game.phase, "select_bet")

                lobby_actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    stake_payload(amount=1000),
                )
                self.assertEqual(game.phase, "lobby")
                self.assertEqual(game.bet, 1000)
                start_message = next(action for action in lobby_actions if action["type"] == "send_message")
                self.assertIn("底注: <b>1000</b>", start_message["text"])

                wrong = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(amount=100),
                )
                self.assertEqual(wrong, [{"type": "no_session"}])

                joined = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(amount=1000),
                )
                self.assertTrue(any("加入牌局成功" in action.get("text", "") for action in joined))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_lobby_accepts_userbot_entry_and_matching_payment(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                await start_lobby(plugin, ctx, payload=payload, amount=7895)

                self.assertIn(-100123, plugin._games)
                self.assertEqual(plugin._games[-100123].bet, 7895)

                wrong = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(amount=1000),
                )
                self.assertEqual(wrong, [{"type": "no_session"}])

                owner_joined = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    userbot_entry_payload(),
                )
                self.assertTrue(any("加入牌局成功" in action.get("text", "") for action in owner_joined))

                joined = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    payment_payload(amount=7895),
                )
                self.assertTrue(any("加入牌局成功" in action.get("text", "") for action in joined))
                game = plugin._games[-100123]
                self.assertEqual([uid for uid, _name in game.lobby_players], [999, 111])
                self.assertEqual(game.dealer_id, 999)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_notice_uses_replied_user_instead_of_notice_bot_sender(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx, amount=1000)

                first_payload = payment_payload(amount=1000, payer_id=111, payer_name="玩家A")
                first_payload.pop("payer_user_id", None)
                first_payload["sender_user_id"] = 8980553289
                first_payload["sender_name"] = "转账通知Abot"
                first_payload["actor"] = {"user_id": 8980553289, "display_name": "转账通知Abot"}
                first_payload["player"] = {"user_id": 8980553289, "display_name": "转账通知Abot"}

                first = await plugin.on_interaction(ctx, "start_ten_half", first_payload)
                self.assertTrue(any("加入牌局成功" in action.get("text", "") for action in first))

                second_payload = payment_payload(
                    amount=1000,
                    payer_id=222,
                    payer_name="玩家B",
                    notice_message_id=711,
                    reply_message_id=710,
                )
                second_payload.pop("payer_user_id", None)
                second_payload["sender_user_id"] = 8980553289
                second_payload["sender_name"] = "转账通知Abot"
                second_payload["actor"] = {"user_id": 8980553289, "display_name": "转账通知Abot"}
                second_payload["player"] = {"user_id": 8980553289, "display_name": "转账通知Abot"}

                second = await plugin.on_interaction(ctx, "start_ten_half", second_payload)
                self.assertFalse(any("你已经加入了" in action.get("text", "") for action in second))
                game = plugin._games[-100123]
                self.assertEqual([uid for uid, _name in game.lobby_players], [111, 222])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_start_ignores_legacy_module_prize_until_stake_selected(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60, "stake_options": [2000]})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload["bet"] = 100
                payload["module_prize"] = 1000
                actions = await plugin.on_interaction(ctx, "start_ten_half", payload)

                game = plugin._games[-100123]
                self.assertEqual(game.phase, "select_bet")
                self.assertEqual(game.bet, 0)
                self.assertEqual(game.stake_options, [2000])
                select_message = next(action for action in actions if action["type"] == "send_message")
                self.assertIn("th:stake:2000", str(select_message["reply_markup"]))
                self.assertNotIn("底注: <b>1000</b>", select_message["text"])

                lobby_actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    stake_payload(amount=2000),
                )
                self.assertEqual(game.phase, "lobby")
                self.assertEqual(game.bet, 2000)
                start_message = next(action for action in lobby_actions if action["type"] == "send_message")
                self.assertIn("底注: <b>2000</b>", start_message["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_start_without_bet_shows_default_stake_options(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext(config={"max_players": 3, "lobby_timeout": 60})
            await plugin.on_startup(ctx)
            try:
                payload = keyword_payload()
                payload.pop("bet", None)
                payload["prize"] = 123
                actions = await plugin.on_interaction(ctx, "start_ten_half", payload)

                game = plugin._games[-100123]
                self.assertEqual(game.phase, "select_bet")
                self.assertEqual(game.bet, 0)
                self.assertEqual(game.stake_options, [1000, 10000, 50000, 100000])
                select_message = next(action for action in actions if action["type"] == "send_message")
                self.assertIn("请选择要开局的底注额度", select_message["text"])
                self.assertIn("th:stake:1000", str(select_message["reply_markup"]))
                self.assertIn("th:stake:10000", str(select_message["reply_markup"]))
                self.assertIn("th:stake:50000", str(select_message["reply_markup"]))
                self.assertIn("th:stake:100000", str(select_message["reply_markup"]))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_entry_joins_keyword_lobby_as_dealer(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, redis=redis)
            await plugin.on_startup(ctx)
            try:
                start_actions = await start_lobby(plugin, ctx)
                start_message = next(action for action in start_actions if action["type"] == "send_message")
                redis.store[plugin_module._main_msg_key(1, -100123)] = "900"

                actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    userbot_entry_payload(),
                )

                game = plugin._games[-100123]
                self.assertTrue(game.via_interaction)
                self.assertTrue(game.dealer_locked)
                self.assertEqual(game.dealer_id, 999)
                self.assertEqual(game.dealer_name, "owner")
                self.assertEqual(game.lobby_players, [(999, "owner")])
                self.assertEqual(game.player_message_ids[999], 811)
                self.assertEqual(game.host_user_id, 999)
                self.assertEqual(game.max_players, 2)
                self.assertEqual(start_message["send_via"], "interaction_bot")
                session_action = next(action for action in actions if action.get("type") == "start_session")
                self.assertEqual(session_action["type"], "start_session")
                self.assertEqual(session_action["entry_key"], "start_ten_half")
                self.assertEqual(session_action["started_by_user_id"], 999)
                self.assertEqual(session_action["paid_user_ids"], [999])
                self.assertEqual(session_action["participant_user_ids"], [999])
                join_message = next(action for action in actions if action.get("type") == "send_message")
                self.assertEqual(join_message["send_via"], "interaction_bot")
                self.assertEqual(join_message["reply_to_message_id"], 811)
                self.assertIn("加入牌局成功", join_message["text"])
                self.assertIn("入场金额: 免转账", join_message["text"])
                refresh = next(action for action in actions if action.get("type") == "edit_message")
                self.assertEqual(refresh["send_via"], "interaction_bot")
                self.assertEqual(refresh["message_id"], 900)
                self.assertIn("👥 已加入 (1/2): owner", refresh["text"])
                self.assertIn("🎰 庄家: <b>owner</b>", refresh["text"])
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
                actions = await start_lobby(plugin, ctx)

                self.assertEqual(
                    [action["type"] for action in actions],
                    ["answer_callback", "edit_message", "start_session", "send_message"],
                )
                action = next(action for action in actions if action["type"] == "send_message")
                self.assertEqual(action["type"], "send_message")
                self.assertEqual(action["reply_to_message_id"], 601)
                self.assertEqual(action["save_message_id_key"], plugin_module._main_msg_key(1, -100123))
                self.assertEqual(action["replace_saved_message_id_key"], plugin_module._main_msg_key(1, -100123))
                self.assertIn("十点半开局", action["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_lobby_timeout_with_only_userbot_entry_updates_message_and_ends_session(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, messages=messages)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(ctx, "start_ten_half", userbot_entry_payload())
                game = plugin._games[-100123]
                game.main_message_id = 599

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._lobby_timeout_task(-100123, game.started_at, ctx)

                self.assertNotIn(-100123, plugin._games)
                self.assertEqual(len(messages.applied), 1)
                timeout_actions = messages.applied[-1]["actions"]
                self.assertEqual([a["type"] for a in timeout_actions], ["edit_message", "end_session"])
                self.assertEqual(timeout_actions[0]["message_id"], 599)
                self.assertEqual(timeout_actions[0]["send_via"], "interaction_bot")
                self.assertIn("参与人数不足 2 人，牌局已取消", timeout_actions[0]["text"])
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_lobby_timeout_with_single_paid_player_refunds_entry_fee(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(config={"max_players": 2, "lobby_timeout": 60}, messages=messages)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(ctx, "start_ten_half", payment_payload())
                game = plugin._games[-100123]
                game.main_message_id = 599

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._lobby_timeout_task(-100123, game.started_at, ctx)

                self.assertNotIn(-100123, plugin._games)
                self.assertEqual(len(messages.applied), 1)
                timeout_actions = messages.applied[-1]["actions"]
                self.assertEqual([a["type"] for a in timeout_actions], ["edit_message", "payout", "end_session"])
                self.assertIn("参与人数不足 2 人，牌局已取消；已退还 玩家A 的入局费 100", timeout_actions[0]["text"])
                refund = timeout_actions[1]
                self.assertEqual(refund["chat_id"], -100123)
                self.assertEqual(refund["amount"], 100)
                self.assertEqual(refund["text"], "+100")
                self.assertEqual(refund["reply_to_user_id"], 111)
                self.assertEqual(refund["reply_to_message_id"], 700)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_lobby_timeout_silent_debit_refund_uses_recent_user_message_lookup(self) -> None:
        async def fast_sleep(_seconds):
            return None

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            messages = FakeMessages()
            ctx = PluginContext(config={"join_mode": "silent_debit", "max_players": 2, "lobby_timeout": 60}, messages=messages)
            await plugin.on_startup(ctx)
            try:
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    callback_payload(user_id=111, name="玩家A", callback_query_id="cb-silent-join", message_id=900),
                )
                debit_notice = payment_payload(reply_message_id=999)
                debit_notice["payment"] = {"direction": "debit"}
                await plugin.on_interaction(ctx, "start_ten_half", debit_notice)
                game = plugin._games[-100123]
                game.main_message_id = 599

                for task in list(plugin._tasks):
                    task.cancel()
                if plugin._tasks:
                    await asyncio.gather(*plugin._tasks, return_exceptions=True)
                plugin._tasks.clear()

                with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                    await plugin._lobby_timeout_task(-100123, game.started_at, ctx)

                timeout_actions = messages.applied[-1]["actions"]
                refund = next(action for action in timeout_actions if action["type"] == "payout")
                self.assertEqual(refund["reply_to_user_id"], 111)
                self.assertNotIn("reply_to_message_id", refund)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_entry_game_begins_with_owner_dealer_buttons(self) -> None:
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
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(ctx, "start_ten_half", userbot_entry_payload())
                game = plugin._games[-100123]
                game.main_message_id = 900

                with patch.object(plugin_module, "create_deck", return_value=list(deck)):
                    actions = await plugin.on_interaction(ctx, "start_ten_half", payment_payload(payer_id=111, payer_name="玩家A"))

                self.assertEqual(game.dealer_id, 999)
                self.assertEqual(game.phase, "playing")
                self.assertEqual([p.user_id for p in game.players], [111])
                self.assertTrue(any("th:hit:0" in str(action.get("reply_markup")) for action in actions))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_userbot_entry_button_flow_settles_and_rewards(self) -> None:
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
                await start_lobby(plugin, ctx)
                await plugin.on_interaction(ctx, "start_ten_half", userbot_entry_payload())
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
                            "callback_data": "th:stand:0",
                        },
                        "actor": {"user_id": 111, "display_name": "玩家A"},
                    },
                )
                self.assertEqual(game.phase, "playing")
                self.assertTrue(any("th:stand:0" in str(action.get("reply_markup")) for action in player_actions))

                final_actions = await plugin.on_interaction(
                    ctx,
                    "start_ten_half",
                    {
                        "source": {
                            "type": "callback_query",
                            "chat_id": -100123,
                            "message_id": 900,
                            "callback_query_id": "cb-dealer-stand",
                            "callback_data": "th:stand:0",
                        },
                        "actor": {"user_id": 999, "display_name": "owner"},
                    },
                )

                self.assertTrue(any(action.get("type") == "send_message" and "十点半结算" in action.get("text", "") for action in final_actions))
                rewards = [action for action in final_actions if action.get("type") == "payout"]
                self.assertEqual([action["text"] for action in rewards], ["+180"])
                self.assertEqual([action["amount"] for action in rewards], [180])
                self.assertEqual({action["reply_to_message_id"] for action in rewards}, {700})
                self.assertEqual({action["reply_to_user_id"] for action in rewards}, {111})
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

    def test_interaction_begin_deals_one_card_to_each_player_and_dealer(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            ctx = PluginContext()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100, via_interaction=True)
            game.lobby_players = [(111, "庄家候选"), (222, "玩家B"), (333, "玩家C")]
            game.main_message_id = 900
            deck = [
                plugin_module.Card("♣️", "4"),
                plugin_module.Card("♦️", "5"),
                plugin_module.Card("♥️", "6"),
                plugin_module.Card("♠️", "5"),
            ]

            with patch.object(plugin_module, "create_deck", return_value=list(deck)):
                actions = await plugin._ix_begin(-100123, game, 111, "庄家候选", ctx)
            self.assertEqual(game.phase, "playing")
            self.assertEqual(len(game.dealer_cards), 1)
            self.assertEqual([len(p.cards) for p in game.players], [1, 1])
            self.assertEqual(actions[0]["type"], "edit_message")
            self.assertIn("所有人共用下方按钮", actions[0]["text"])
            self.assertIn("等待：玩家B、玩家C、庄家候选", actions[0]["text"])
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
            reward = next(action for action in actions if action.get("type") == "payout")
            self.assertEqual(reward["text"], "+180")
            self.assertEqual(reward["amount"], 180)
            self.assertEqual(reward["reply_to_message_id"], 700)
            self.assertEqual(reward["reply_to_user_id"], 111)
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
            rewards = [action for action in actions if action.get("type") == "payout"]
            settlement_text = next(action["text"] for action in actions if "十点半结算" in action.get("text", ""))

            self.assertEqual([action["text"] for action in rewards], ["+270"])
            self.assertEqual([action["amount"] for action in rewards], [270])
            self.assertEqual(rewards[0]["reply_to_message_id"], 700)
            self.assertEqual(rewards[0]["reply_to_user_id"], 111)
            self.assertIn("总入池金额: <b>300</b>", settlement_text)
            self.assertIn("庄家 <b>玩家A</b> 🎉是赢家 获得 <b>270</b>", settlement_text)
            self.assertIn("玩家B</b>: 2张 · 9点 → ❌ 输 100", settlement_text)

        asyncio.run(scenario())

    def test_doubled_flag_without_extra_paid_stake_does_not_inflate_pot(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            game.game_id = "84F383"
            game.dealer_id = 111
            game.dealer_name = "庄家"
            game.paid_stakes = {111: 100, 222: 100}
            game.dealer_cards = [
                plugin_module.Card("♠️", "A"),
                plugin_module.Card("♥️", "2"),
                plugin_module.Card("♦️", "2"),
                plugin_module.Card("♣️", "2"),
                plugin_module.Card("♠️", "2"),
            ]
            player = plugin_module.PlayerHand(user_id=222, name="玩家", stake=100, doubled=True)
            player.cards = [plugin_module.Card("♣️", "A"), plugin_module.Card("♦️", "A")]
            game.players = [player]
            game.player_message_ids = {111: 700, 222: 710}

            actions = await plugin._ix_settle(-100123, game, PluginContext())
            settlement = next(action for action in actions if action.get("type") == "send_message")
            reward = next(action for action in actions if action.get("type") == "payout")

            self.assertIn("总入池金额: <b>200</b>", settlement["text"])
            self.assertIn("玩家</b>: 2张 · 2点 → ❌ 输 100", settlement["text"])
            self.assertIn("庄家 <b>庄家</b> 🎉是赢家 获得 <b>180</b>", settlement["text"])
            self.assertEqual(reward["amount"], 180)
            self.assertEqual(reward["reply_to_user_id"], 111)

        asyncio.run(scenario())

    def test_interaction_settlement_rewards_dealer_by_user_id_without_payment_message(self) -> None:
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
            rewards = [action for action in actions if action.get("type") == "payout"]

            self.assertEqual([action["text"] for action in rewards], ["+180"])
            self.assertEqual([action["amount"] for action in rewards], [180])
            self.assertNotIn("reply_to_message_id", rewards[0])
            self.assertEqual(rewards[0]["reply_to_user_id"], 999)
            self.assertEqual(rewards[0]["reply_to_search_limit"], 50)
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
            self.assertEqual(seconds, 60)

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
                await plugin._cleanup_game_messages_task(ctx, -100123, 900, 910, {899}, settlement_key, [reward_key], 60)

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

    def test_settlement_cleanup_does_not_delete_next_lobby_saved_messages(self) -> None:
        async def fast_sleep(seconds):
            self.assertEqual(seconds, 60)

        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            redis = FakeRedis()
            messages = FakeMessages()
            ctx = PluginContext(redis=redis, messages=messages)
            old_settlement_key = plugin_module._settlement_msg_key(1, -100123, "OLD01")
            old_reward_key = plugin_module._reward_msg_key(1, -100123, "OLD01", 111)
            redis.store[plugin_module._main_msg_key(1, -100123)] = "900"
            redis.store[plugin_module._join_notice_key(1, -100123)] = "910"
            redis.store[old_settlement_key] = "830"
            redis.store[old_reward_key] = "820"

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._cleanup_game_messages_task(
                    ctx,
                    -100123,
                    800,
                    810,
                    {799},
                    old_settlement_key,
                    [old_reward_key],
                    60,
                )

            actions = messages.applied[0]["actions"]
            self.assertEqual(
                actions,
                [
                    {"type": "delete_message", "message_id": 799, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 800, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 810, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 830, "send_via": "interaction_bot", "chat_id": -100123},
                    {"type": "delete_message", "message_id": 820, "send_via": "userbot_reply", "chat_id": -100123},
                ],
            )
            self.assertNotIn(900, {action["message_id"] for action in actions})
            self.assertNotIn(910, {action["message_id"] for action in actions})

        asyncio.run(scenario())

    def test_multiple_five_small_players_all_receive_triple_reward(self) -> None:
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
            rewards = [action for action in actions if action.get("type") == "payout"]

            self.assertEqual([action["text"] for action in rewards], ["+360", "+360"])
            self.assertEqual([action["amount"] for action in rewards], [360, 360])
            self.assertEqual({action["reply_to_message_id"] for action in rewards}, {700, 710})
            self.assertEqual({action["reply_to_user_id"] for action in rewards}, {111, 222})

        asyncio.run(scenario())

    def test_hit_resets_player_target_timeout_version(self) -> None:
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

            await plugin._ix_refresh_or_settle(-100123, game, ctx, schedule_all=True)
            first_version = game.timeout_versions[111]
            await plugin._ix_hit(-100123, game, ctx, player=player)
            second_version = game.timeout_versions[111]

            self.assertEqual(first_version + 1, second_version)
            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._target_timeout_task(-100123, 111, 123.0, first_version, ctx)
            self.assertFalse(player.stood)
            self.assertEqual(messages.applied, [])

            with patch.object(plugin_module.asyncio, "sleep", new=fast_sleep):
                await plugin._target_timeout_task(-100123, 111, 123.0, second_version, ctx)
            self.assertTrue(player.stood)
            self.assertEqual(game.phase, "playing")
            self.assertEqual(messages.applied[0]["actions"][0]["type"], "edit_message")
            self.assertIn("等待：庄家", messages.applied[0]["actions"][0]["text"])
            self.assertIn("th:stand:0", str(messages.applied[0]["actions"][0]["reply_markup"]))

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
                        "callback_data": "th:stand:0",
                    },
                    "actor": {"user_id": 111, "display_name": "玩家A"},
                },
            )

            self.assertEqual(actions, [{
                "type": "answer_callback",
                "callback_query_id": "cb-timeout",
                "text": "你本轮已经结束。",
                "show_alert": False,
            }])
            self.assertIn(-100123, plugin._games)

        asyncio.run(scenario())

    def test_bot_dealer_play_returns_reward_actions(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.TenHalfPlugin()
            game = plugin_module.TenHalfGame(chat_id=-100123, bet=100)
            player = plugin_module.PlayerHand(user_id=111, name="玩家A")
            player.cards = [plugin_module.Card("♠️", "9"), plugin_module.Card("♥️", "A")]
            game.players = [player]
            game.dealer_cards = [plugin_module.Card("♦️", "9"), plugin_module.Card("♣️", "10")]
            game.player_message_ids[111] = 700
            plugin._games[-100123] = game

            actions = await plugin._ix_dealer_play(-100123, game, PluginContext())

            rewards = [action for action in actions if action.get("type") == "payout"]
            self.assertEqual([action["text"] for action in rewards], ["+180"])
            self.assertEqual([action["amount"] for action in rewards], [180])
            self.assertEqual(rewards[0]["reply_to_message_id"], 700)
            self.assertEqual(rewards[0]["reply_to_user_id"], 111)
            self.assertNotIn(-100123, plugin._games)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
