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

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "bj")
        self._timeout = cfg.get("timeout", 120)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[blackjack] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._games.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[blackjack] 已停止")

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
            player_name = getattr(sender, "first_name", "") or "玩家"

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
            asyncio.create_task(self._auto_timeout(chat_id, ctx))

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
    async def _auto_timeout(self, chat_id: int, ctx: PluginContext) -> None:
        await asyncio.sleep(self._timeout)
        gs = self._games.get(chat_id)
        if gs and not gs.finished:
            gs.finished = True
            self._games.pop(chat_id, None)
            if ctx.log:
                await ctx.log("info", f"[blackjack] chat {chat_id} 牌局超时自动结束")

    # ── 消息钩子（留空，只用命令）──────────────────
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        pass


PLUGIN_CLASS = BlackjackPlugin

__all__ = ["BlackjackPlugin", "PLUGIN_CLASS"]
