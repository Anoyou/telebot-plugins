"""十点半纸牌游戏插件。

经典十点半纸牌游戏：支持多人对战、加倍、五小等规则。
A=1, 2-9=面值, 10/J/Q/K=0.5点。目标 10.5 点。
五小(5张不爆)自动赢，天生十点半(前两张=10.5)双倍赔付。
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
    def public_entity_display_name(
        entity: Any,
        *,
        fallback_id: int | str | None = None,
        default: str = "玩家",
    ) -> str:
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
SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


@dataclass
class Card:
    suit: str
    rank: str

    @property
    def value(self) -> float:
        if self.rank == "A":
            return 1.0
        if self.rank in ("10", "J", "Q", "K"):
            return 0.5
        return float(self.rank)

    def display(self, hidden: bool = False) -> str:
        if hidden:
            return "🂠(?点)"
        v = self.value
        v_str = str(int(v)) if v == int(v) else str(v)
        return f"{self.suit}{self.rank}({v_str}点)"


def create_deck() -> list[Card]:
    """创建并洗牌一副 52 张牌。"""
    deck = [Card(s, r) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _fv(v: float) -> str:
    """格式化点数：整数不带小数点。"""
    return str(int(v)) if v == int(v) else str(v)


# ─────────────────────────────────────────────────────
# 玩家手牌
# ─────────────────────────────────────────────────────
@dataclass
class PlayerHand:
    user_id: int
    name: str
    cards: list[Card] = field(default_factory=list)
    stood: bool = False
    busted: bool = False
    doubled: bool = False
    is_winner: bool = False
    payout: int = 0

    @property
    def value(self) -> float:
        return sum(c.value for c in self.cards)

    @property
    def is_natural(self) -> bool:
        """前两张恰好 10.5 点 → 天生十点半。"""
        return len(self.cards) == 2 and abs(self.value - 10.5) < 1e-9

    @property
    def is_five_small(self) -> bool:
        """5 张牌且不爆 → 五小。"""
        return len(self.cards) >= 5 and self.value <= 10.5 + 1e-9

    @property
    def is_done(self) -> bool:
        return self.busted or self.stood

    def hand_str(self) -> str:
        parts = " ".join(c.display() for c in self.cards)
        return f"{parts} = {_fv(self.value)}点"


# ─────────────────────────────────────────────────────
# 游戏状态
# ─────────────────────────────────────────────────────
@dataclass
class TenHalfGame:
    chat_id: int
    bet: int
    # lobby → ask_dealer → playing → dealer_turn → finished
    phase: str = "lobby"
    dealer_id: int = 0          # 0 = bot 庄家
    dealer_name: str = "🤖 庄家"
    dealer_cards: list[Card] = field(default_factory=list)
    deck: list[Card] = field(default_factory=list)
    players: list[PlayerHand] = field(default_factory=list)
    lobby_players: list[tuple[int, str]] = field(default_factory=list)
    turn_order: list[int] = field(default_factory=list)
    current_turn: int = 0
    current_player_idx: int = 0
    ask_dealer_uid: int = 0
    ask_dealer_name: str = ""
    started_at: float = 0.0
    via_interaction: bool = False
    finished: bool = False
    lobby_msg_id: int | None = None

    # ── 庄家辅助 ─────────────────────────────────────
    @property
    def dealer_is_bot(self) -> bool:
        return self.dealer_id == 0

    def dealer_val(self) -> float:
        return sum(c.value for c in self.dealer_cards)

    def dealer_natural(self) -> bool:
        return len(self.dealer_cards) == 2 and abs(self.dealer_val() - 10.5) < 1e-9

    def dealer_five_small(self) -> bool:
        return len(self.dealer_cards) >= 5 and self.dealer_val() <= 10.5 + 1e-9

    def dealer_busted(self) -> bool:
        return self.dealer_val() > 10.5 + 1e-9

    def dealer_hand_str(self, reveal: bool = False) -> str:
        if not self.dealer_cards:
            return "无"
        show = reveal or self.phase in ("dealer_turn", "finished")
        if show:
            parts = [c.display() for c in self.dealer_cards]
            total = self.dealer_val()
        else:
            # 第一张暗牌
            parts = [self.dealer_cards[0].display(hidden=True)]
            parts.extend(c.display() for c in self.dealer_cards[1:])
            total = sum(c.value for c in self.dealer_cards[1:])
        return " ".join(parts) + f" = {_fv(total)}点"


# ─────────────────────────────────────────────────────
# Payload helpers (交互 bot 协议)
# ─────────────────────────────────────────────────────
def _pe(p: dict[str, Any]) -> dict[str, Any]:
    e = p.get("event")
    return e if isinstance(e, dict) else {}


def _ps(p: dict[str, Any]) -> dict[str, Any]:
    s = p.get("source")
    return s if isinstance(s, dict) else {}


def _pa(p: dict[str, Any]) -> dict[str, Any]:
    a = p.get("actor")
    return a if isinstance(a, dict) else {}


def _pint(v: Any, d: int, *, minimum: int = 0) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return d
    return n if n >= minimum else d


def _ie_type(p: dict[str, Any]) -> str:
    e, t, s = _pe(p), p.get("trigger") or {}, _ps(p)
    return str(
        e.get("type") or t.get("event") or t.get("type")
        or s.get("event_type") or p.get("event_type") or ""
    ).strip()


def _ie_chat(p: dict[str, Any]) -> int:
    e, s = _pe(p), _ps(p)
    sess = p.get("session") if isinstance(p.get("session"), dict) else {}
    return _pint(
        p.get("chat_id") or e.get("chat_id")
        or s.get("chat_id") or sess.get("chat_id"),
        0, minimum=-10 ** 20,
    )


def _ie_mid(p: dict[str, Any]) -> int | None:
    e, s = _pe(p), _ps(p)
    rt = p.get("reply_to") if isinstance(p.get("reply_to"), dict) else {}
    v = _pint(
        p.get("message_id") or p.get("source_message_id")
        or rt.get("message_id") or e.get("message_id") or s.get("message_id"),
        0,
    )
    return v or None


def _ie_text(p: dict[str, Any]) -> str:
    e, s = _pe(p), _ps(p)
    return str(
        p.get("message_text") or p.get("text")
        or e.get("text") or s.get("text") or ""
    ).strip()


def _ie_actor(p: dict[str, Any]) -> tuple[int, str]:
    a, e = _pa(p), _pe(p)
    rid = (
        a.get("user_id") or a.get("id")
        or p.get("sender_user_id") or e.get("user_id")
    )
    rname = (
        a.get("display_name") or a.get("name")
        or p.get("sender_name") or e.get("display_name") or "玩家"
    )
    return _pint(rid, 0, minimum=0), str(rname).strip() or "玩家"


# ─────────────────────────────────────────────────────
# 插件
# ─────────────────────────────────────────────────────
@register
class TenHalfPlugin(Plugin):
    key = "ten_half"
    display_name = "十点半"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command", "timeout", "lobby_timeout", "max_players"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "10d"
        self._turn_timeout = 30
        self._lobby_timeout = 60
        self._max_players = 5
        self._games: dict[int, TenHalfGame] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    # ── 工具方法 ─────────────────────────────────────
    def _lock(self, cid: int) -> asyncio.Lock:
        if cid not in self._locks:
            self._locks[cid] = asyncio.Lock()
        return self._locks[cid]

    def _track(self, t: asyncio.Task) -> None:
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _track_task(self, t: asyncio.Task) -> None:
        """Alias for _track — tracks asyncio.Task for cleanup."""
        self._track(t)

    async def _send(self, ctx: PluginContext, cid: int, text: str, *,
                    parse_mode: str = "html", reply_to: int | None = None) -> None:
        """安全发送消息（用于异步任务）。"""
        if not ctx.client:
            return
        try:
            await ctx.client.send_message(
                cid, text, parse_mode=parse_mode,
                **({"reply_to": reply_to} if reply_to else {}),
            )
        except Exception:
            pass

    # ── 生命周期 ─────────────────────────────────────
    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "10d")
        self._turn_timeout = cfg.get("timeout", 30)
        self._lobby_timeout = cfg.get("lobby_timeout", 60)
        self._max_players = cfg.get("max_players", 5)
        self.commands = {self._command: self._cmd, "十点半": self._cmd}
        if ctx.log:
            await ctx.log("info", f"[ten_half] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._games.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[ten_half] 已停止")

    # ═══════════════════════════════════════════════════
    # 命令入口 (userbot 流)
    # ═══════════════════════════════════════════════════
    async def _cmd(
        self, client: Any, event: Any, args: list[str],
        account_id: int, ctx: PluginContext,
    ) -> None:
        cid = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not cid:
            return

        lock = self._lock(cid)
        async with lock:
            g = self._games.get(cid)
            if g and not g.finished:
                await event.reply("⚠️ 当前已有进行中的十点半游戏。", parse_mode="html")
                return

            bet = 0
            if args:
                try:
                    bet = max(0, min(1_000_000, int(args[0])))
                except ValueError:
                    pass
            if bet <= 0:
                await event.reply(
                    f"请指定下注金额，例如：,{self._command} 100",
                    parse_mode="html",
                )
                return

            g = TenHalfGame(
                chat_id=cid, bet=bet,
                phase="lobby", started_at=time.monotonic(),
            )
            self._games[cid] = g

        msg = await event.reply(
            f"🃏 <b>十点半开局！</b>\n💰 底注: {bet}\n\n"
            f"📢 输入 <b>「加入」</b> 参加游戏\n"
            f"⏰ 等待玩家加入中... ({self._lobby_timeout}秒)",
            parse_mode="html",
        )
        g.lobby_msg_id = int(getattr(msg, "id", 0) or 0) or None
        self._track(asyncio.create_task(
            self._lobby_timeout_task(cid, g.started_at, ctx),
        ))

    # ═══════════════════════════════════════════════════
    # 大厅 / 选庄
    # ═══════════════════════════════════════════════════
    async def _lobby_timeout_task(self, cid: int, sa: float, ctx: PluginContext) -> None:
        await asyncio.sleep(self._lobby_timeout)
        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.phase != "lobby" or g.finished or g.started_at != sa:
                return
            if not g.lobby_players:
                g.finished = True
                self._games.pop(cid, None)
                await self._send(ctx, cid, "⏰ 没人加入，十点半游戏取消。")
                return
            await self._ask_dealer(cid, g, ctx)

    async def _ask_dealer(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """向第一个加入的玩家询问是否当庄家。"""
        first_id, first_name = g.lobby_players[0]
        g.phase = "ask_dealer"
        g.ask_dealer_uid = first_id
        g.ask_dealer_name = first_name

        plist = "、".join(n for _, n in g.lobby_players)
        await self._send(
            ctx, cid,
            f"👥 参与玩家: {plist}\n\n"
            f"❓ <b>{first_name}</b>，你要当庄家吗？\n"
            f"回复 <b>「是」</b> 当庄家 或 <b>「否」</b> 让机器人当庄家",
        )
        self._track(asyncio.create_task(
            self._dealer_question_timeout(cid, g.started_at, ctx),
        ))

    async def _dealer_question_timeout(self, cid: int, sa: float, ctx: PluginContext) -> None:
        await asyncio.sleep(30)
        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.phase != "ask_dealer" or g.finished or g.started_at != sa:
                return
            # 超时默认机器人当庄
            await self._begin_game(cid, g, dealer_id=0, dealer_name="🤖 庄家", ctx=ctx)

    # ═══════════════════════════════════════════════════
    # 开局发牌
    # ═══════════════════════════════════════════════════
    async def _begin_game(
        self, cid: int, g: TenHalfGame,
        *, dealer_id: int, dealer_name: str, ctx: PluginContext,
    ) -> None:
        g.dealer_id = dealer_id
        g.dealer_name = dealer_name
        g.deck = create_deck()

        # 构建玩家列表（庄家除外）
        for uid, name in g.lobby_players:
            if uid != dealer_id:
                g.players.append(PlayerHand(user_id=uid, name=name))

        if not g.players:
            g.finished = True
            self._games.pop(cid, None)
            await self._send(ctx, cid, "⚠️ 没有其他玩家，游戏取消。")
            return

        # 发牌：每人 2 张，庄家 2 张（一暗一明）
        for p in g.players:
            p.cards.append(g.deck.pop())
            p.cards.append(g.deck.pop())
        g.dealer_cards.append(g.deck.pop())
        g.dealer_cards.append(g.deck.pop())

        g.phase = "playing"
        g.current_player_idx = 0

        # Build turn_order from player IDs; detect natural 10.5 winners
        g.turn_order = [p.user_id for p in g.players]
        for p in g.players:
            if p.is_natural:
                p.is_winner = True
                p.payout = g.bet * 2  # Double payout for natural 10.5

        g.current_turn = 0

        # 展示初始状态
        await self._send(ctx, cid, self._build_state_text(g))

        # 庄家天生十点半 → 直接结算
        if g.dealer_natural():
            await self._send(ctx, cid, f"✨ <b>{g.dealer_name}</b> 天生十点半！")
            await self._dealer_play(cid, g, ctx)
            return

        # 推进到第一个可行动的玩家
        await self._advance_turn(cid, g, ctx)

    def _build_state_text(self, g: TenHalfGame, *, reveal_dealer: bool = False) -> str:
        lines = [f"🃏 <b>十点半 · 底注 {g.bet}</b>\n"]
        lines.append(f"👤 <b>{g.dealer_name}</b> (庄)\n  手牌: {g.dealer_hand_str(reveal=reveal_dealer)}\n")
        for p in g.players:
            tag = ""
            if p.is_natural:
                tag = " ✨天生十点半！"
            elif p.is_five_small:
                tag = " 🌟五小！"
            elif p.busted:
                tag = " 💥爆牌"
            elif p.stood:
                tag = " ✋停牌"
            lines.append(f"👤 <b>{p.name}</b>{tag}\n  手牌: {p.hand_str()}\n")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════
    # 回合推进
    # ═══════════════════════════════════════════════════
    async def _advance_turn(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """跳过已完成的玩家，找到下一个可行动的玩家或进入庄家回合。"""
        if g.phase != "playing":
            return

        while g.current_player_idx < len(g.players):
            p = g.players[g.current_player_idx]
            if p.is_done:
                g.current_player_idx += 1
                continue
            # 天生十点半 → 自动停牌
            if p.is_natural:
                p.stood = True
                await self._send(ctx, cid, f"✨ <b>{p.name}</b> 天生十点半！自动停牌。")
                g.current_player_idx += 1
                continue
            # 五小 → 自动停牌
            if p.is_five_small:
                p.stood = True
                await self._send(ctx, cid, f"🌟 <b>{p.name}</b> 五小！自动停牌。")
                g.current_player_idx += 1
                continue
            # 爆牌
            if p.value > 10.5 + 1e-9:
                p.busted = True
                g.current_player_idx += 1
                continue
            break

        if g.current_player_idx >= len(g.players):
            # 所有玩家行动完毕 → 庄家回合
            await self._dealer_play(cid, g, ctx)
            return

        p = g.players[g.current_player_idx]
        await self._send(
            ctx, cid,
            f"⏳ 轮到 <b>{p.name}</b> 行动\n"
            f"指令: 要牌 / 停牌 / 加倍",
        )
        self._track(asyncio.create_task(
            self._turn_timeout_task(cid, g.current_player_idx, g.started_at, ctx),
        ))

    async def _turn_timeout_task(self, cid: int, pi: int, sa: float, ctx: PluginContext) -> None:
        await asyncio.sleep(self._turn_timeout)
        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.phase != "playing" or g.finished or g.started_at != sa:
                return
            if g.current_player_idx != pi:
                return
            p = g.players[pi]
            if p.is_done:
                return
            p.stood = True
            await self._send(ctx, cid, f"⏰ {p.name} 超时，自动停牌。")
            g.current_player_idx += 1
            await self._advance_turn(cid, g, ctx)

    # ═══════════════════════════════════════════════════
    # 玩家动作（命令流，直接发送消息）
    # ═══════════════════════════════════════════════════
    async def _act_hit(self, cid: int, g: TenHalfGame, pi: int, ctx: PluginContext) -> None:
        p = g.players[pi]
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        if p.value > 10.5 + 1e-9:
            p.busted = True
            await self._send(
                ctx, cid,
                f"💥 <b>{p.name}</b> 要牌 {card.display()} → 爆牌！({_fv(p.value)}点)\n"
                f"手牌: {p.hand_str()}")
        elif p.is_five_small:
            p.stood = True
            await self._send(
                ctx, cid,
                f"🌟 <b>{p.name}</b> 要牌 {card.display()} → <b>五小！</b>\n"
                f"手牌: {p.hand_str()}")
        else:
            await self._send(
                ctx, cid,
                f"✅ <b>{p.name}</b> 要牌 {card.display()}\n"
                f"手牌: {p.hand_str()}")

        if p.is_done:
            g.current_player_idx += 1
            await self._advance_turn(cid, g, ctx)

    async def _act_stand(self, cid: int, g: TenHalfGame, pi: int, ctx: PluginContext) -> None:
        p = g.players[pi]
        p.stood = True
        await self._send(
            ctx, cid,
            f"✅ <b>{p.name}</b> 停牌 ({_fv(p.value)}点)")
        g.current_player_idx += 1
        await self._advance_turn(cid, g, ctx)

    async def _act_double(self, cid: int, g: TenHalfGame, pi: int, ctx: PluginContext) -> None:
        p = g.players[pi]
        if len(p.cards) != 2:
            await self._send(ctx, cid, "⚠️ 加倍只能在前两张牌时使用。")
            return

        p.doubled = True
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        if p.value > 10.5 + 1e-9:
            p.busted = True
            await self._send(
                ctx, cid,
                f"💥 <b>{p.name}</b> 加倍要牌 {card.display()} → 爆牌！({_fv(p.value)}点)\n"
                f"下注翻倍: {g.bet * 2}")
        else:
            p.stood = True
            await self._send(
                ctx, cid,
                f"💰 <b>{p.name}</b> 加倍！要牌 {card.display()}\n"
                f"手牌: {p.hand_str()}\n"
                f"下注翻倍: {g.bet * 2}")

        g.current_player_idx += 1
        await self._advance_turn(cid, g, ctx)

    # ═══════════════════════════════════════════════════
    # 庄家回合
    # ═══════════════════════════════════════════════════
    async def _dealer_play(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        g.phase = "dealer_turn"
        all_bust = all(p.busted for p in g.players)

        await self._send(
            ctx, cid,
            f"🎰 <b>{g.dealer_name}</b> 亮牌！\n"
            f"手牌: {g.dealer_hand_str(reveal=True)}")

        if all_bust:
            await self._send(ctx, cid, f"💀 所有玩家都爆牌，{g.dealer_name} 自动获胜！")
        else:
            # 庄家自动要牌/停牌规则：≤5 要牌，≥5.5 停牌
            drew = False
            while g.dealer_val() <= 5.0 + 1e-9:
                if not g.deck:
                    g.deck = create_deck()
                card = g.deck.pop()
                g.dealer_cards.append(card)
                drew = True
                await self._send(
                    ctx, cid,
                    f"  🎰 {g.dealer_name} 要牌 {card.display()}\n"
                    f"  手牌: {g.dealer_hand_str(reveal=True)}")
                if g.dealer_busted():
                    await self._send(ctx, cid, f"  💥 {g.dealer_name} 爆牌！")
                    break
            else:
                # 循环正常结束（非 break）→ 庄家停牌
                if not g.dealer_busted():
                    await self._send(
                        ctx, cid,
                        f"  ✅ {g.dealer_name} 停牌 ({_fv(g.dealer_val())}点)")

        await self._settle(cid, g, ctx)

    # ═══════════════════════════════════════════════════
    # 结算
    # ═══════════════════════════════════════════════════
    async def _settle(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        g.phase = "finished"
        g.finished = True

        dv = g.dealer_val()
        db = g.dealer_busted()
        dn = g.dealer_natural()
        dfs = g.dealer_five_small()

        lines = ["🏆 <b>结算</b>\n"]
        lines.append(f"庄家 {g.dealer_name}: {g.dealer_hand_str(reveal=True)}\n")

        for p in g.players:
            eb = g.bet * (2 if p.doubled else 1)
            outcome = self._compare(p, dv, db, dn, dfs)
            lines.append(f"👤 {p.name}: {p.hand_str()} → {self._outcome_str(outcome, eb)}")

        await self._send(ctx, cid, "\n".join(lines))
        self._games.pop(cid, None)

    @staticmethod
    def _compare(
        p: PlayerHand,
        dealer_val: float,
        dealer_busted: bool,
        dealer_natural: bool,
        dealer_five_small: bool,
    ) -> str:
        """比较玩家与庄家，返回结果标识。

        返回值: win_nat | win_5s | win | push | lose
        """
        pn = p.is_natural
        pfs = p.is_five_small

        if p.busted:
            return "lose"

        if dealer_busted:
            # 庄家爆牌：没爆的玩家赢
            if pn:
                return "win_nat"
            if pfs:
                return "win_5s"
            return "win"

        # ── 天生十点半优先级最高 ──
        if pn and dealer_natural:
            return "push"
        if pn:
            return "win_nat"
        if dealer_natural:
            return "lose"

        # ── 五小次之 ──
        if pfs and dealer_five_small:
            return "push"
        if pfs:
            return "win_5s"
        if dealer_five_small:
            return "lose"

        # ── 普通比较 ──
        if p.value > dealer_val:
            return "win"
        if p.value < dealer_val:
            return "lose"
        return "push"

    @staticmethod
    def _outcome_str(outcome: str, bet: int) -> str:
        if outcome == "win_nat":
            return f"✨ 天生十点半！+{bet * 2}"
        if outcome == "win_5s":
            return f"🌟 五小！+{int(bet * 1.5)}"
        if outcome == "win":
            return f"✅ 赢 +{bet}"
        if outcome == "push":
            return "🤝 平局 0"
        return f"❌ 输 -{bet}"

    # ═══════════════════════════════════════════════════
    # on_message（命令流）
    # ═══════════════════════════════════════════════════
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        if not text or text.startswith(",") or text.startswith("/"):
            return

        cid = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not cid:
            return

        g = self._games.get(cid)
        if not g or g.finished or g.via_interaction:
            return

        lock = self._lock(cid)
        async with lock:
            if g.finished:
                return

            sender = await event.get_sender()
            uid = int(getattr(sender, "id", 0) or 0)
            name = public_entity_display_name(sender, default="玩家")

            # ── 大厅阶段 ──
            if g.phase == "lobby":
                if text in ("加入", "join"):
                    await self._cmd_join(cid, g, uid, name, ctx, event)
                return

            # ── 选庄阶段 ──
            if g.phase == "ask_dealer":
                if uid != g.ask_dealer_uid:
                    return
                if text in ("是", "yes", "对", "好"):
                    await self._begin_game(cid, g, dealer_id=uid, dealer_name=name, ctx=ctx)
                elif text in ("否", "no", "不"):
                    await self._begin_game(cid, g, dealer_id=0, dealer_name="🤖 庄家", ctx=ctx)
                return

            # ── 游戏阶段 ──
            if g.phase == "playing":
                if g.current_player_idx >= len(g.players):
                    return
                cur = g.players[g.current_player_idx]

                if uid == cur.user_id:
                    if text in ("要牌", "hit", "拿牌"):
                        await self._act_hit(cid, g, g.current_player_idx, ctx)
                    elif text in ("停牌", "stand", "停"):
                        await self._act_stand(cid, g, g.current_player_idx, ctx)
                    elif text in ("加倍", "double"):
                        await self._act_double(cid, g, g.current_player_idx, ctx)
                    elif text in ("手牌", "牌"):
                        await event.reply(
                            f"🃏 你的手牌:\n{cur.hand_str()}",
                            parse_mode="html",
                        )
                else:
                    # 任何玩家可以查看自己手牌
                    if text in ("手牌", "牌"):
                        for p in g.players:
                            if p.user_id == uid:
                                await event.reply(
                                    f"🃏 {p.name} 的手牌:\n{p.hand_str()}",
                                    parse_mode="html",
                                )
                                break

    async def _cmd_join(
        self, cid: int, g: TenHalfGame,
        uid: int, name: str, ctx: PluginContext, event: Any,
    ) -> None:
        for existing_uid, _ in g.lobby_players:
            if existing_uid == uid:
                await event.reply("⚠️ 你已经加入了。", parse_mode="html")
                return
        if len(g.lobby_players) >= self._max_players:
            await event.reply("⚠️ 人数已满。", parse_mode="html")
            return

        g.lobby_players.append((uid, name))
        cnt = len(g.lobby_players)
        await event.reply(
            f"✅ {name} 加入成功！({cnt}/{self._max_players})",
            parse_mode="html",
        )

        if cnt >= self._max_players:
            await self._ask_dealer(cid, g, ctx)

    # ═══════════════════════════════════════════════════
    # on_interaction（交互 bot 流）
    # ═══════════════════════════════════════════════════
    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_ten_half":
            return None

        etype = _ie_type(payload)
        cid = _ie_chat(payload)
        if not cid:
            return [{"type": "send_message", "text": "❌ 十点半需要在群聊里使用。"}]

        if etype in ("payment_confirmed", "keyword"):
            return await self._ix_start(ctx, payload, cid)
        if etype == "message":
            return await self._ix_message(ctx, payload, cid)
        if etype == "session_close":
            async with self._lock(cid):
                self._games.pop(cid, None)
            return [{"type": "end_session"}]
        return []

    # ── 交互：开局 ──────────────────────────────────
    async def _ix_start(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        bet = _pint(
            payload.get("bet") or payload.get("amount") or payload.get("prize"),
            0, minimum=1,
        )
        if bet <= 0:
            return [
                {
                    "type": "send_message",
                    "text": f"请指定下注金额。例：{{prefix}}{self._command} 100",
                    "reply_to_message_id": _ie_mid(payload),
                },
                {"type": "end_session"},
            ]

        async with self._lock(cid):
            if cid in self._games and not self._games[cid].finished:
                return [{
                    "type": "send_message",
                    "text": "⚠️ 当前已有进行中的十点半游戏。",
                    "reply_to_message_id": _ie_mid(payload),
                }]
            g = TenHalfGame(
                chat_id=cid, bet=bet,
                phase="lobby", started_at=time.monotonic(),
                via_interaction=True,
            )
            self._games[cid] = g

        self._track(asyncio.create_task(
            self._lobby_timeout_task(cid, g.started_at, ctx),
        ))

        return [{
            "type": "send_message",
            "text": (
                f"🃏 <b>十点半开局！</b>\n💰 底注: {bet}\n\n"
                f"📢 输入 <b>「加入」</b> 参加游戏\n"
                f"⏰ 等待玩家加入中... ({self._lobby_timeout}秒)"
            ),
            "parse_mode": "html",
            "reply_to_message_id": _ie_mid(payload),
        }]

    # ── 交互：消息处理 ──────────────────────────────
    async def _ix_message(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        text = _ie_text(payload)
        if not text:
            return []
        mid = _ie_mid(payload)

        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.finished:
                return [{"type": "no_session"}]

            aid, aname = _ie_actor(payload)

            # ── 大厅 ──
            if g.phase == "lobby":
                if text not in ("加入", "join"):
                    return []
                for uid, _ in g.lobby_players:
                    if uid == aid:
                        return [{"type": "send_message", "text": "⚠️ 你已经加入了。", "reply_to_message_id": mid}]
                if len(g.lobby_players) >= self._max_players:
                    return [{"type": "send_message", "text": "⚠️ 人数已满。", "reply_to_message_id": mid}]

                g.lobby_players.append((aid, aname))
                cnt = len(g.lobby_players)
                result: list[dict[str, Any]] = [{
                    "type": "send_message",
                    "text": f"✅ {aname} 加入成功！({cnt}/{self._max_players})",
                    "reply_to_message_id": mid,
                }]

                if cnt >= self._max_players:
                    # 满员 → 直接进入选庄
                    first_id, first_name = g.lobby_players[0]
                    g.phase = "ask_dealer"
                    g.ask_dealer_uid = first_id
                    g.ask_dealer_name = first_name
                    plist = "、".join(n for _, n in g.lobby_players)
                    result.append({
                        "type": "send_message",
                        "text": (
                            f"👥 参与玩家: {plist}\n\n"
                            f"❓ <b>{first_name}</b>，你要当庄家吗？\n"
                            f"回复 <b>「是」</b> 当庄家 或 <b>「否」</b> 让机器人当庄家"
                        ),
                        "parse_mode": "html",
                    })
                    self._track(asyncio.create_task(
                        self._dealer_question_timeout(cid, g.started_at, ctx),
                    ))
                return result

            # ── 选庄 ──
            if g.phase == "ask_dealer":
                if aid != g.ask_dealer_uid:
                    return []
                if text in ("是", "yes", "对", "好"):
                    return await self._ix_begin(cid, g, aid, aname, ctx)
                if text in ("否", "no", "不"):
                    return await self._ix_begin(cid, g, 0, "🤖 庄家", ctx)
                return []

            # ── 游戏中 ──
            if g.phase == "playing":
                if g.current_player_idx >= len(g.players):
                    return []
                cur = g.players[g.current_player_idx]

                if aid != cur.user_id:
                    if text in ("手牌", "牌"):
                        for p in g.players:
                            if p.user_id == aid:
                                return [{"type": "send_message", "text": f"🃏 {p.name} 的手牌:\n{p.hand_str()}", "parse_mode": "html", "reply_to_message_id": mid}]
                    return []

                if text in ("要牌", "hit", "拿牌"):
                    return await self._ix_hit(cid, g, ctx)
                if text in ("停牌", "stand", "停"):
                    return await self._ix_stand(cid, g, ctx)
                if text in ("加倍", "double"):
                    return await self._ix_double(cid, g, ctx)
                if text in ("手牌", "牌"):
                    return [{"type": "send_message", "text": f"🃏 你的手牌:\n{cur.hand_str()}", "parse_mode": "html", "reply_to_message_id": mid}]
                return []

        return []

    # ── 交互：开局发牌 ──────────────────────────────
    async def _ix_begin(
        self, cid: int, g: TenHalfGame,
        dealer_id: int, dealer_name: str, ctx: PluginContext,
    ) -> list[dict[str, Any]]:
        g.dealer_id = dealer_id
        g.dealer_name = dealer_name
        g.deck = create_deck()

        for uid, name in g.lobby_players:
            if uid != dealer_id:
                g.players.append(PlayerHand(user_id=uid, name=name))

        if not g.players:
            g.finished = True
            self._games.pop(cid, None)
            return [
                {"type": "send_message", "text": "⚠️ 没有其他玩家，游戏取消。"},
                {"type": "end_session"},
            ]

        for p in g.players:
            p.cards.append(g.deck.pop())
            p.cards.append(g.deck.pop())
        g.dealer_cards.append(g.deck.pop())
        g.dealer_cards.append(g.deck.pop())

        g.phase = "playing"
        g.current_player_idx = 0

        # Build turn_order from player IDs; detect natural 10.5 winners
        g.turn_order = [p.user_id for p in g.players]
        for p in g.players:
            if p.is_natural:
                p.is_winner = True
                p.payout = g.bet * 2  # Double payout for natural 10.5

        g.current_turn = 0

        actions: list[dict[str, Any]] = [
            {"type": "send_message", "text": self._build_state_text(g), "parse_mode": "html"},
        ]

        # 庄家天生十点半 → 直接结算
        if g.dealer_natural():
            actions.append({
                "type": "send_message",
                "text": f"✨ <b>{g.dealer_name}</b> 天生十点半！",
                "parse_mode": "html",
            })
            actions.extend(self._ix_dealer_play(cid, g))
            return actions

        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：回合推进 ──────────────────────────────
    async def _ix_advance(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> list[dict[str, Any]]:
        if g.phase != "playing":
            return []

        actions: list[dict[str, Any]] = []

        while g.current_player_idx < len(g.players):
            p = g.players[g.current_player_idx]
            if p.is_done:
                g.current_player_idx += 1
                continue
            if p.is_natural:
                p.stood = True
                actions.append({"type": "send_message", "text": f"✨ <b>{p.name}</b> 天生十点半！自动停牌。", "parse_mode": "html"})
                g.current_player_idx += 1
                continue
            if p.is_five_small:
                p.stood = True
                actions.append({"type": "send_message", "text": f"🌟 <b>{p.name}</b> 五小！自动停牌。", "parse_mode": "html"})
                g.current_player_idx += 1
                continue
            if p.value > 10.5 + 1e-9:
                p.busted = True
                g.current_player_idx += 1
                continue
            break

        if g.current_player_idx >= len(g.players):
            actions.extend(self._ix_dealer_play(cid, g))
            return actions

        p = g.players[g.current_player_idx]
        self._track(asyncio.create_task(
            self._turn_timeout_task(cid, g.current_player_idx, g.started_at, ctx),
        ))
        actions.append({
            "type": "send_message",
            "text": f"⏳ 轮到 <b>{p.name}</b> 行动\n指令: 要牌 / 停牌 / 加倍",
            "parse_mode": "html",
        })
        return actions

    # ── 交互：要牌 ──────────────────────────────────
    async def _ix_hit(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> list[dict[str, Any]]:
        pi = g.current_player_idx
        p = g.players[pi]
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        actions: list[dict[str, Any]] = []
        if p.value > 10.5 + 1e-9:
            p.busted = True
            actions.append({
                "type": "send_message",
                "text": f"💥 <b>{p.name}</b> 要牌 {card.display()} → 爆牌！({_fv(p.value)}点)\n手牌: {p.hand_str()}",
                "parse_mode": "html",
            })
        elif p.is_five_small:
            p.stood = True
            actions.append({
                "type": "send_message",
                "text": f"🌟 <b>{p.name}</b> 要牌 {card.display()} → <b>五小！</b>\n手牌: {p.hand_str()}",
                "parse_mode": "html",
            })
        else:
            actions.append({
                "type": "send_message",
                "text": f"✅ <b>{p.name}</b> 要牌 {card.display()}\n手牌: {p.hand_str()}",
                "parse_mode": "html",
            })

        if p.is_done:
            g.current_player_idx += 1
            actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：停牌 ──────────────────────────────────
    async def _ix_stand(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> list[dict[str, Any]]:
        p = g.players[g.current_player_idx]
        p.stood = True
        actions: list[dict[str, Any]] = [{
            "type": "send_message",
            "text": f"✅ <b>{p.name}</b> 停牌 ({_fv(p.value)}点)",
            "parse_mode": "html",
        }]
        g.current_player_idx += 1
        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：加倍 ──────────────────────────────────
    async def _ix_double(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> list[dict[str, Any]]:
        p = g.players[g.current_player_idx]
        if len(p.cards) != 2:
            return [{"type": "send_message", "text": "⚠️ 加倍只能在前两张牌时使用。", "parse_mode": "html"}]

        p.doubled = True
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        actions: list[dict[str, Any]] = []
        if p.value > 10.5 + 1e-9:
            p.busted = True
            actions.append({
                "type": "send_message",
                "text": f"💥 <b>{p.name}</b> 加倍要牌 {card.display()} → 爆牌！({_fv(p.value)}点)\n下注翻倍: {g.bet * 2}",
                "parse_mode": "html",
            })
        else:
            p.stood = True
            actions.append({
                "type": "send_message",
                "text": f"💰 <b>{p.name}</b> 加倍！要牌 {card.display()}\n手牌: {p.hand_str()}\n下注翻倍: {g.bet * 2}",
                "parse_mode": "html",
            })

        g.current_player_idx += 1
        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：庄家回合 ──────────────────────────────
    def _ix_dealer_play(self, cid: int, g: TenHalfGame) -> list[dict[str, Any]]:
        g.phase = "dealer_turn"
        all_bust = all(p.busted for p in g.players)

        actions: list[dict[str, Any]] = [{
            "type": "send_message",
            "text": f"🎰 <b>{g.dealer_name}</b> 亮牌！\n手牌: {g.dealer_hand_str(reveal=True)}",
            "parse_mode": "html",
        }]

        if all_bust:
            actions.append({
                "type": "send_message",
                "text": f"💀 所有玩家都爆牌，{g.dealer_name} 自动获胜！",
                "parse_mode": "html",
            })
        else:
            while g.dealer_val() <= 5.0 + 1e-9:
                if not g.deck:
                    g.deck = create_deck()
                card = g.deck.pop()
                g.dealer_cards.append(card)
                actions.append({
                    "type": "send_message",
                    "text": f"  🎰 {g.dealer_name} 要牌 {card.display()}\n  手牌: {g.dealer_hand_str(reveal=True)}",
                    "parse_mode": "html",
                })
                if g.dealer_busted():
                    actions.append({
                        "type": "send_message",
                        "text": f"  💥 {g.dealer_name} 爆牌！",
                        "parse_mode": "html",
                    })
                    break
            else:
                if not g.dealer_busted():
                    actions.append({
                        "type": "send_message",
                        "text": f"  ✅ {g.dealer_name} 停牌 ({_fv(g.dealer_val())}点)",
                        "parse_mode": "html",
                    })

        actions.extend(self._ix_settle(cid, g))
        return actions

    # ── 交互：结算 ──────────────────────────────────
    def _ix_settle(self, cid: int, g: TenHalfGame) -> list[dict[str, Any]]:
        g.phase = "finished"
        g.finished = True

        dv = g.dealer_val()
        db = g.dealer_busted()
        dn = g.dealer_natural()
        dfs = g.dealer_five_small()

        lines = ["🏆 <b>结算</b>\n"]
        lines.append(f"庄家 {g.dealer_name}: {g.dealer_hand_str(reveal=True)}\n")

        player_results: list[dict[str, Any]] = []
        for p in g.players:
            eb = g.bet * (2 if p.doubled else 1)
            outcome = self._compare(p, dv, db, dn, dfs)
            lines.append(f"👤 {p.name}: {p.hand_str()} → {self._outcome_str(outcome, eb)}")
            if outcome.startswith("win"):
                amount = eb * 2 if outcome == "win_nat" else int(eb * 1.5) if outcome == "win_5s" else eb
            elif outcome == "push":
                amount = 0
            else:
                amount = -eb
            player_results.append({
                "user_id": p.user_id,
                "name": p.name,
                "outcome": outcome,
                "amount": amount,
            })

        self._games.pop(cid, None)
        return [
            {"type": "send_message", "text": "\n".join(lines), "parse_mode": "html"},
            {
                "type": "result",
                "success": True,
                "result": {
                    "dealer_name": g.dealer_name,
                    "dealer_value": dv,
                    "players": player_results,
                },
            },
            {"type": "end_session"},
        ]


PLUGIN_CLASS = TenHalfPlugin

__all__ = ["TenHalfPlugin", "PLUGIN_CLASS"]
