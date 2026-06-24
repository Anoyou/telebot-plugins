"""21点（Blackjack）远程插件。

群内庄家模式：发牌 → 玩家要牌/停牌/加倍 → 庄家自动补牌 → 比大小。
每个群同时只能有一局进行中。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

try:
    from app.worker.plugins.base import public_entity_display_name
except ImportError:  # pragma: no cover - older TelePilot compatibility
    def public_entity_display_name(entity: Any, *, fallback_id: int | str | None = None, default: str = "玩家") -> str:
        if entity is not None:
            username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
            if username:
                return username
            entity_id = getattr(entity, "id", None)
            if not bool(getattr(entity, "contact", False)):
                name = " ".join(
                    part
                    for part in (
                        str(getattr(entity, "first_name", "") or "").strip(),
                        str(getattr(entity, "last_name", "") or "").strip(),
                    )
                    if part
                )
                if name:
                    return name
            if entity_id not in (None, ""):
                return str(entity_id)
        return str(fallback_id) if fallback_id not in (None, "") else default

# ─────────────────────────────────────────────────────
# 牌组
# ─────────────────────────────────────────────────────
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _card_str(rank: str, suit: str) -> str:
    return f"{suit}{rank}"


def _deal_card() -> tuple[str, str]:
    return random.choice(RANKS), random.choice(SUITS)


def _hand_value(cards: list[tuple[str, str]]) -> tuple[int, bool]:
    """返回 (点数, 是否软手)。软手 = 有 Ace 按 11 算且没爆。"""
    total = 0
    aces = 0
    for rank, _ in cards:
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    soft = False
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    if aces > 0 and total <= 21:
        soft = True
    return total, soft


def _format_hand(cards: list[tuple[str, str]], hide_first: bool = False) -> str:
    if hide_first:
        return f"🂠 {''.join(_card_str(r, s) for r, s in cards[1:])}"
    return " ".join(_card_str(r, s) for r, s in cards)


def _format_result(player_cards, dealer_cards, result: str, bet: int) -> str:
    p_val, _ = _hand_value(player_cards)
    d_val, _ = _hand_value(dealer_cards)
    lines = [
        f"<b>🃏 21点 结算</b>",
        f"",
        f"庄家：{_format_hand(dealer_cards)}（{d_val}点）",
        f"你的牌：{_format_hand(player_cards)}（{p_val}点）",
        f"",
    ]
    if result == "win":
        lines.append(f"🎉 你赢了！+{bet} 筹码")
    elif result == "lose":
        lines.append(f"😢 你输了 -{bet} 筹码")
    elif result == "push":
        lines.append(f"🤝 平局，筹码退回")
    elif result == "blackjack":
        lines.append(f"💰 Blackjack！+{int(bet * 1.5)} 筹码")
    elif result == "bust":
        lines.append(f"💥 爆了！-{bet} 筹码")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────
# 游戏状态
# ─────────────────────────────────────────────────────
@dataclass
class GameState:
    player_cards: list[tuple[str, str]] = field(default_factory=list)
    dealer_cards: list[tuple[str, str]] = field(default_factory=list)
    bet: int = 10
    player_id: int = 0
    player_name: str = ""
    message_id: int | None = None
    started_at: float = 0.0
    doubled: bool = False
    finished: bool = False


def _payload_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    return event if isinstance(event, dict) else {}


def _payload_source(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source")
    return source if isinstance(source, dict) else {}


def _payload_actor(payload: dict[str, Any]) -> dict[str, Any]:
    actor = payload.get("actor")
    return actor if isinstance(actor, dict) else {}


def _payload_reply_to(payload: dict[str, Any]) -> dict[str, Any]:
    reply_to = payload.get("reply_to")
    return reply_to if isinstance(reply_to, dict) else {}


def _positive_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _interaction_event_type(payload: dict[str, Any]) -> str:
    event = _payload_event(payload)
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    source = _payload_source(payload)
    return str(event.get("type") or trigger.get("event") or trigger.get("type") or source.get("event_type") or payload.get("event_type") or "").strip()


def _interaction_chat_id(payload: dict[str, Any]) -> int:
    event = _payload_event(payload)
    source = _payload_source(payload)
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    return _positive_int(payload.get("chat_id") or event.get("chat_id") or source.get("chat_id") or session.get("chat_id"), 0, minimum=-10**20)


def _interaction_message_id(payload: dict[str, Any]) -> int | None:
    event = _payload_event(payload)
    source = _payload_source(payload)
    reply_to = _payload_reply_to(payload)
    value = _positive_int(payload.get("message_id") or payload.get("source_message_id") or reply_to.get("message_id") or event.get("message_id") or source.get("message_id"), 0)
    return value or None


def _interaction_message_text(payload: dict[str, Any]) -> str:
    event = _payload_event(payload)
    source = _payload_source(payload)
    return str(payload.get("message_text") or payload.get("text") or event.get("text") or source.get("text") or "").strip()


def _interaction_amount(payload: dict[str, Any]) -> int:
    event = _payload_event(payload)
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return _positive_int(payload.get("amount") or data.get("amount"), 0, minimum=1)


def _interaction_actor(payload: dict[str, Any]) -> tuple[int, str]:
    actor = _payload_actor(payload)
    event = _payload_event(payload)
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw_id = actor.get("user_id") or actor.get("id") or payload.get("sender_user_id") or payload.get("payer_user_id") or event.get("user_id") or data.get("payer_user_id")
    raw_name = actor.get("display_name") or actor.get("name") or payload.get("sender_name") or payload.get("payer_name") or event.get("display_name") or data.get("payer_name") or "玩家"
    return _positive_int(raw_id, 0, minimum=0), str(raw_name).strip() or "玩家"


# ─────────────────────────────────────────────────────
# 插件
# ─────────────────────────────────────────────────────
@register
class BlackjackPlugin(Plugin):
    key = "blackjack"
    display_name = "21点"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "bj"
        self._timeout = 120
        self._games: dict[int, GameState] = {}  # chat_id -> game
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def _track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "bj")
        self._timeout = cfg.get("timeout", 120)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[blackjack] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._games.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[blackjack] 已停止")

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_blackjack":
            return None
        event_type = _interaction_event_type(payload)
        chat_id = _interaction_chat_id(payload)
        if not chat_id:
            return [{"type": "send_message", "text": "❌ 21 点需要在群聊里使用。"}]
        if event_type in {"payment_confirmed", "keyword"}:
            return await self._interaction_start(ctx, payload, chat_id)
        if event_type == "message":
            return await self._interaction_action(payload, chat_id)
        if event_type == "session_close":
            async with self._get_lock(chat_id):
                self._games.pop(chat_id, None)
            return [{"type": "end_session"}]
        return []

    async def _interaction_start(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        player_id, player_name = _interaction_actor(payload)
        bet = _positive_int(payload.get("prize") or payload.get("bet") or _interaction_amount(payload), 10, minimum=1)
        timeout = _positive_int(payload.get("timeout") or payload.get("valid_seconds"), self._timeout, minimum=10)
        async with self._get_lock(chat_id):
            if chat_id in self._games and not self._games[chat_id].finished:
                return [{"type": "send_message", "text": "🃏 当前聊天已有进行中的 21 点牌局。", "reply_to_message_id": _interaction_message_id(payload)}]
            player_cards = [_deal_card(), _deal_card()]
            dealer_cards = [_deal_card(), _deal_card()]
            gs = GameState(
                player_cards=player_cards,
                dealer_cards=dealer_cards,
                bet=bet,
                player_id=player_id,
                player_name=player_name,
                started_at=time.monotonic(),
            )
            p_val, _ = _hand_value(player_cards)
            if p_val == 21:
                gs.finished = True
                return self._interaction_settle_actions(gs, "blackjack", _interaction_message_id(payload))
            self._games[chat_id] = gs
        self._track_task(asyncio.create_task(self._auto_timeout(chat_id, ctx, gs.started_at, timeout)))
        p_val, _ = _hand_value(player_cards)
        return [
            {
                "type": "send_message",
                "text": (
                    f"<b>🃏 21点</b> · {player_name} 下注 {bet} 筹码\n\n"
                    f"庄家：{_format_hand(dealer_cards, hide_first=True)}\n"
                    f"你的牌：{_format_hand(player_cards)}（{p_val}点）\n\n"
                    "直接发送：要牌 / 停牌 / 加倍"
                ),
                "parse_mode": "html",
                "reply_to_message_id": _interaction_message_id(payload),
            }
        ]

    async def _interaction_action(self, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        action_text = _interaction_message_text(payload).lower()
        action = ""
        if action_text in {"h", "hit", "要牌"}:
            action = "hit"
        elif action_text in {"s", "stand", "停牌"}:
            action = "stand"
        elif action_text in {"d", "double", "加倍"}:
            action = "double"
        if not action:
            return []
        actor_id, _ = _interaction_actor(payload)
        async with self._get_lock(chat_id):
            gs = self._games.get(chat_id)
            if not gs or gs.finished:
                return [{"type": "no_session"}]
            if actor_id != gs.player_id:
                return [{"type": "send_message", "text": "这不是你的牌局哦。", "reply_to_message_id": _interaction_message_id(payload)}]
            if action == "hit":
                gs.player_cards.append(_deal_card())
                p_val, _ = _hand_value(gs.player_cards)
                if p_val > 21:
                    gs.finished = True
                    self._games.pop(chat_id, None)
                    return self._interaction_settle_actions(gs, "bust", _interaction_message_id(payload))
                if p_val == 21:
                    self._dealer_finish(gs)
                    self._games.pop(chat_id, None)
                    return self._interaction_settle_actions(gs, self._result_for_finished(gs), _interaction_message_id(payload))
                return [
                    {
                        "type": "send_message",
                        "text": f"你的牌：{_format_hand(gs.player_cards)}（{p_val}点）\n继续发送：要牌 / 停牌",
                        "parse_mode": "html",
                        "reply_to_message_id": _interaction_message_id(payload),
                    }
                ]
            if action == "double":
                if len(gs.player_cards) != 2:
                    return [{"type": "send_message", "text": "只能在前两张牌时加倍。", "reply_to_message_id": _interaction_message_id(payload)}]
                gs.bet *= 2
                gs.doubled = True
                gs.player_cards.append(_deal_card())
                p_val, _ = _hand_value(gs.player_cards)
                if p_val > 21:
                    gs.finished = True
                    self._games.pop(chat_id, None)
                    return self._interaction_settle_actions(gs, "bust", _interaction_message_id(payload))
            self._dealer_finish(gs)
            result = self._result_for_finished(gs)
            self._games.pop(chat_id, None)
            return self._interaction_settle_actions(gs, result, _interaction_message_id(payload))

    def _dealer_finish(self, gs: GameState) -> None:
        while True:
            d_val, d_soft = _hand_value(gs.dealer_cards)
            if d_val < 17 or (d_val == 17 and d_soft):
                gs.dealer_cards.append(_deal_card())
            else:
                break
        gs.finished = True

    def _result_for_finished(self, gs: GameState) -> str:
        p_val, _ = _hand_value(gs.player_cards)
        d_val, _ = _hand_value(gs.dealer_cards)
        if p_val > 21:
            return "bust"
        if d_val > 21 or p_val > d_val:
            if p_val == 21 and len(gs.player_cards) == 2:
                return "blackjack"
            return "win"
        if p_val < d_val:
            return "lose"
        return "push"

    def _interaction_settle_actions(self, gs: GameState, result: str, reply_to: int | None) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = [
            {"type": "send_message", "text": _format_result(gs.player_cards, gs.dealer_cards, result, gs.bet), "parse_mode": "html", "reply_to_message_id": reply_to}
        ]
        if result in {"win", "blackjack"}:
            amount = int(gs.bet * 1.5) if result == "blackjack" else gs.bet
            actions.append({"type": "send_message", "text": f"+{amount}", "reply_to_message_id": reply_to, "send_via": "userbot_reply"})
            actions.append(
                {
                    "type": "result",
                    "success": True,
                    "result": {"winner_user_id": gs.player_id, "winner_name": gs.player_name, "amount": amount, "result": result},
                    "settlement": {"mode": "announce_only", "winner_user_id": gs.player_id, "winner_name": gs.player_name, "amount": amount, "amount_field": "prize"},
                }
            )
        actions.append({"type": "end_session"})
        return actions

    # ── 命令入口 ─────────────────────────────────────
    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        arg_str = " ".join(args).strip()

        # 子命令：要牌/停牌/加倍
        if arg_str in ("h", "hit", "要牌"):
            return await self._player_action(chat_id, "hit", event, ctx)
        if arg_str in ("s", "stand", "停牌"):
            return await self._player_action(chat_id, "stand", event, ctx)
        if arg_str in ("d", "double", "加倍"):
            return await self._player_action(chat_id, "double", event, ctx)

        lock = self._get_lock(chat_id)
        async with lock:
            if chat_id in self._games and not self._games[chat_id].finished:
                gs = self._games[chat_id]
                p_val, _ = _hand_value(gs.player_cards)
                d_val, _ = _hand_value(gs.dealer_cards)
                await event.reply(
                    f"🃏 已有进行中的牌局！\n"
                    f"你的牌：{_format_hand(gs.player_cards)}（{p_val}点）\n"
                    f"庄家：{_format_hand(gs.dealer_cards, hide_first=True)}\n\n"
                    f"操作：,{self._command} 要牌 / ,{self._command} 停牌 / ,{self._command} 加倍",
                    parse_mode="html",
                )
                return

            # 解析下注
            bet = 10
            if arg_str:
                try:
                    bet = max(1, min(1000, int(arg_str)))
                except ValueError:
                    bet = 10

            # 发牌
            player_cards = [_deal_card(), _deal_card()]
            dealer_cards = [_deal_card(), _deal_card()]
            sender = await event.get_sender()
            player_name = public_entity_display_name(sender, default="玩家")

            gs = GameState(
                player_cards=player_cards,
                dealer_cards=dealer_cards,
                bet=bet,
                player_id=int(getattr(sender, "id", 0) or 0),
                player_name=player_name,
                started_at=time.monotonic(),
            )

            # 检查 Blackjack
            p_val, _ = _hand_value(player_cards)
            if p_val == 21:
                gs.finished = True
                result = "blackjack"
                reply = _format_result(player_cards, dealer_cards, result, bet)
                await event.reply(reply, parse_mode="html")
                self._games.pop(chat_id, None)
                return

            self._games[chat_id] = gs
            p_val, _ = _hand_value(player_cards)
            msg = await event.reply(
                f"<b>🃏 21点</b> · 下注 {bet} 筹码\n\n"
                f"庄家：{_format_hand(dealer_cards, hide_first=True)}\n"
                f"你的牌：{_format_hand(player_cards)}（{p_val}点）\n\n"
                f"操作：\n"
                f"  ,{self._command} 要牌 — 再拿一张\n"
                f"  ,{self._command} 停牌 — 不要了\n"
                f"  ,{self._command} 加倍 — 翻倍下注，只拿一张",
                parse_mode="html",
            )
            gs.message_id = msg.id if msg else None

            # 超时自动停牌
            self._track_task(asyncio.create_task(self._auto_timeout(chat_id, ctx, gs.started_at)))

    # ── 玩家操作 ─────────────────────────────────────
    async def _player_action(self, chat_id: int, action: str, event: Any, ctx: PluginContext) -> None:
        lock = self._get_lock(chat_id)
        async with lock:
            gs = self._games.get(chat_id)
            if not gs or gs.finished:
                await event.reply("没有进行中的牌局。输入指令开一局~", parse_mode="html")
                return

            sender = await event.get_sender()
            sender_id = int(getattr(sender, "id", 0) or 0)
            if sender_id != gs.player_id:
                await event.reply("这不是你的牌局哦~", parse_mode="html")
                return

            if action == "hit":
                gs.player_cards.append(_deal_card())
                p_val, _ = _hand_value(gs.player_cards)
                if p_val > 21:
                    # 爆了
                    gs.finished = True
                    reply = _format_result(gs.player_cards, gs.dealer_cards, "bust", gs.bet)
                    await event.reply(reply, parse_mode="html")
                    self._games.pop(chat_id, None)
                    return
                elif p_val == 21:
                    # 自动停牌
                    return await self._dealer_turn(chat_id, event, ctx)
                else:
                    await event.reply(
                        f"你的牌：{_format_hand(gs.player_cards)}（{p_val}点）\n\n"
                        f"继续：,{self._command} 要牌 / ,{self._command} 停牌",
                        parse_mode="html",
                    )

            elif action == "stand":
                return await self._dealer_turn(chat_id, event, ctx)

            elif action == "double":
                if len(gs.player_cards) != 2:
                    await event.reply("只能在前两张牌时加倍~", parse_mode="html")
                    return
                gs.bet *= 2
                gs.doubled = True
                gs.player_cards.append(_deal_card())
                p_val, _ = _hand_value(gs.player_cards)
                if p_val > 21:
                    gs.finished = True
                    reply = _format_result(gs.player_cards, gs.dealer_cards, "bust", gs.bet)
                    await event.reply(reply, parse_mode="html")
                    self._games.pop(chat_id, None)
                    return
                # 加倍后强制停牌
                return await self._dealer_turn(chat_id, event, ctx)

    # ── 庄家回合 ─────────────────────────────────────
    async def _dealer_turn(self, chat_id: int, event: Any, ctx: PluginContext) -> None:
        gs = self._games.get(chat_id)
        if not gs:
            return

        # 庄家补牌到 17
        while True:
            d_val, d_soft = _hand_value(gs.dealer_cards)
            if d_val < 17 or (d_val == 17 and d_soft):
                gs.dealer_cards.append(_deal_card())
            else:
                break

        p_val, _ = _hand_value(gs.player_cards)
        d_val, _ = _hand_value(gs.dealer_cards)

        if d_val > 21:
            result = "win"
        elif p_val > d_val:
            result = "win"
        elif p_val < d_val:
            result = "lose"
        else:
            result = "push"

        # Blackjack 额外奖励
        if result == "win" and p_val == 21 and len(gs.player_cards) == 2:
            result = "blackjack"

        gs.finished = True
        reply = _format_result(gs.player_cards, gs.dealer_cards, result, gs.bet)
        await event.reply(reply, parse_mode="html")
        self._games.pop(chat_id, None)

    # ── 超时 ─────────────────────────────────────────
    async def _auto_timeout(self, chat_id: int, ctx: PluginContext, started_at: float, timeout: int | None = None) -> None:
        await asyncio.sleep(timeout or self._timeout)
        async with self._get_lock(chat_id):
            gs = self._games.get(chat_id)
            if not gs or gs.finished or gs.started_at != started_at:
                return
            gs.finished = True
            self._games.pop(chat_id, None)
        if ctx.log:
            await ctx.log("info", f"[blackjack] chat {chat_id} 牌局超时自动结束")

    # ── 消息钩子（留空，只用命令）──────────────────
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        pass


PLUGIN_CLASS = BlackjackPlugin

__all__ = ["BlackjackPlugin", "PLUGIN_CLASS"]
