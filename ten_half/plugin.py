"""十点半纸牌游戏插件。

经典十点半纸牌游戏：支持多人对战、加倍、五小等规则。
A=1, 2-9=面值, 10/J/Q/K=0.5点。目标 10.5 点。
五小(5张不爆)自动赢，天生十点半(前两张=10.5)双倍赔付。
"""

from __future__ import annotations

import asyncio
import random
import secrets
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
REDIS_MAIN_MSG_KEY_PREFIX = "ten_half:main:"
REDIS_JOIN_NOTICE_KEY_PREFIX = "ten_half:join_notice:"


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
            return "暗牌"
        return self.rank


def create_deck() -> list[Card]:
    """创建并洗牌一副 52 张牌。"""
    deck = [Card(s, r) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _fv(v: float) -> str:
    """格式化点数：整数不带小数点。"""
    return str(int(v)) if v == int(v) else str(v)


def _main_msg_key(account_id: int, chat_id: int) -> str:
    return f"{REDIS_MAIN_MSG_KEY_PREFIX}{account_id}:{chat_id}"


def _join_notice_key(account_id: int, chat_id: int) -> str:
    return f"{REDIS_JOIN_NOTICE_KEY_PREFIX}{account_id}:{chat_id}"


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
    game_id: str = field(default_factory=lambda: secrets.token_hex(3).upper())
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
    main_message_id: int | None = None
    join_notice_msg_id: int | None = None
    payment_receiver_name: str = ""
    status_note: str = ""
    dealer_timeout_started: bool = False
    player_message_ids: dict[int, int] = field(default_factory=dict)

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


def _pm(p: dict[str, Any]) -> dict[str, Any]:
    m = p.get("message")
    return m if isinstance(m, dict) else {}


def _ps(p: dict[str, Any]) -> dict[str, Any]:
    s = p.get("source")
    return s if isinstance(s, dict) else {}


def _pc(p: dict[str, Any]) -> dict[str, Any]:
    c = p.get("chat")
    return c if isinstance(c, dict) else {}


def _pa(p: dict[str, Any]) -> dict[str, Any]:
    a = p.get("actor")
    return a if isinstance(a, dict) else {}


def _pp(p: dict[str, Any]) -> dict[str, Any]:
    player = p.get("player")
    return player if isinstance(player, dict) else {}


def _pay(p: dict[str, Any]) -> dict[str, Any]:
    payment = p.get("payment")
    return payment if isinstance(payment, dict) else {}


def _reply_to(p: dict[str, Any]) -> dict[str, Any]:
    rt = p.get("reply_to")
    return rt if isinstance(rt, dict) else {}


def _pint(v: Any, d: int, *, minimum: int = 0) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return d
    return n if n >= minimum else d


def _ie_type(p: dict[str, Any]) -> str:
    e, t, s = _pe(p), p.get("trigger") or {}, _ps(p)
    source_type = str(s.get("type") or "").strip()
    if source_type in {
        "keyword",
        "payment_confirmed",
        "callback_query",
        "session_close",
        "command",
        "inline_query",
        "chosen_inline_result",
    }:
        return source_type
    return str(
        e.get("type") or t.get("event") or t.get("type")
        or s.get("event_type") or p.get("event_type") or source_type or ""
    ).strip()


def _ie_chat(p: dict[str, Any]) -> int:
    e, s, m, c = _pe(p), _ps(p), _pm(p), _pc(p)
    sess = p.get("session") if isinstance(p.get("session"), dict) else {}
    return _pint(
        p.get("chat_id") or e.get("chat_id")
        or m.get("chat_id") or s.get("chat_id") or c.get("id") or sess.get("chat_id"),
        0, minimum=-10 ** 20,
    )


def _ie_message_mid(p: dict[str, Any]) -> int | None:
    e, s, m = _pe(p), _ps(p), _pm(p)
    v = _pint(
        m.get("message_id") or p.get("message_id") or p.get("source_message_id")
        or e.get("message_id") or s.get("message_id"),
        0,
    )
    return v or None


def _ie_mid(p: dict[str, Any]) -> int | None:
    rt, m, pay = _reply_to(p), _pm(p), _pay(p)
    v = _pint(
        pay.get("reply_to_message_id")
        or rt.get("message_id")
        or m.get("reply_to_message_id")
        or p.get("reply_to_message_id"),
        0,
    )
    return v or _ie_message_mid(p)


def _ie_text(p: dict[str, Any]) -> str:
    e, s, m = _pe(p), _ps(p), _pm(p)
    return str(
        m.get("text") or p.get("message_text") or p.get("text")
        or e.get("text") or s.get("text") or ""
    ).strip()


def _ie_actor(p: dict[str, Any]) -> tuple[int, str]:
    a, e, player = _pa(p), _pe(p), _pp(p)
    rid = (
        a.get("user_id") or a.get("id") or player.get("user_id")
        or p.get("sender_user_id") or e.get("user_id")
    )
    rname = (
        a.get("display_name") or a.get("name")
        or player.get("display_name") or player.get("name")
        or p.get("sender_name") or e.get("display_name") or "玩家"
    )
    return _pint(rid, 0, minimum=0), str(rname).strip() or "玩家"


def _ie_callback_id(p: dict[str, Any]) -> str | None:
    e, s = _pe(p), _ps(p)
    raw = (
        p.get("callback_query_id")
        or s.get("callback_query_id")
        or e.get("callback_query_id")
    )
    text = str(raw or "").strip()
    return text or None


def _ie_callback_data(p: dict[str, Any]) -> str:
    """Extract callback_data from a callback_query payload."""
    e, s = _pe(p), _ps(p)
    return str(
        p.get("callback_data")
        or s.get("callback_data")
        or e.get("callback_data")
        or e.get("data")
        or ""
    ).strip()


def _ie_payment_amount(p: dict[str, Any]) -> int:
    """Extract payment amount from a payment_confirmed payload."""
    e = _pe(p)
    payment = _pay(p)
    data = e.get("data") if isinstance(e.get("data"), dict) else {}
    return _pint(
        payment.get("amount") or p.get("amount") or data.get("amount")
        or p.get("payment_amount") or e.get("amount"),
        0, minimum=1,
    )


def _ie_payer(p: dict[str, Any]) -> tuple[int, str]:
    """Extract payer identity from a payment_confirmed payload."""
    e = _pe(p)
    player = _pp(p)
    payment = _pay(p)
    payer = payment.get("payer") if isinstance(payment.get("payer"), dict) else {}
    data = e.get("data") if isinstance(e.get("data"), dict) else {}
    raw_id = (
        player.get("user_id") or payment.get("payer_user_id") or payer.get("user_id")
        or p.get("payer_user_id") or data.get("payer_user_id")
        or p.get("sender_user_id") or e.get("payer_user_id")
    )
    raw_name = (
        player.get("display_name") or payment.get("payer_display_name")
        or payment.get("payer_name") or payer.get("display_name") or payer.get("name")
        or p.get("payer_name") or data.get("payer_name")
        or p.get("sender_name") or e.get("payer_name") or "玩家"
    )
    return _pint(raw_id, 0, minimum=0), str(raw_name).strip() or "玩家"


def _ie_payment_status(p: dict[str, Any]) -> str:
    payment = _pay(p)
    return str(payment.get("status") or p.get("payment_status") or "confirmed").strip()


def _ie_payment_receiver(p: dict[str, Any]) -> str:
    payment = _pay(p)
    receiver = payment.get("receiver") if isinstance(payment.get("receiver"), dict) else {}
    module_config = p.get("module_config") if isinstance(p.get("module_config"), dict) else {}
    return str(
        receiver.get("username")
        or receiver.get("display_name")
        or receiver.get("name")
        or payment.get("receiver_username")
        or payment.get("receiver_display_name")
        or payment.get("receiver_name")
        or p.get("receiver_username")
        or p.get("receiver_name")
        or module_config.get("receiver_username")
        or module_config.get("receiver_name")
        or p.get("payout_account_label")
        or ""
    ).strip()


# ─────────────────────────────────────────────────────
# Inline keyboard builders
# ─────────────────────────────────────────────────────
def _kb_join(bet: int) -> dict[str, Any] | None:
    """Lobby join button. Only for free games (bet == 0).

    Paid games use transfer-to-bot flow; no inline button.
    """
    if bet > 0:
        return None
    return {
        "inline_keyboard": [[
            {"text": "🎮 加入游戏", "callback_data": "th:join:0"},
        ]]
    }


def _kb_dealer(uid: int) -> dict[str, Any]:
    """Dealer question buttons."""
    return {
        "inline_keyboard": [[
            {"text": "👑 我要当庄", "callback_data": f"th:dealer_yes:{uid}"},
            {"text": "🤖 机器人当庄", "callback_data": "th:dealer_no:0"},
        ]]
    }


def _kb_turn(uid: int, *, can_double: bool = True) -> dict[str, Any]:
    """Player turn action buttons."""
    row: list[dict[str, str]] = [
        {"text": "🃏 要牌", "callback_data": f"th:hit:{uid}"},
        {"text": "🛑 停牌", "callback_data": f"th:stand:{uid}"},
    ]
    if can_double:
        row.append({"text": "💰 加倍", "callback_data": f"th:double:{uid}"})
    return {"inline_keyboard": [row]}


def _html(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _points(cards: list[Card]) -> float:
    return sum(c.value for c in cards)


def _cards_brief(cards: list[Card]) -> str:
    if not cards:
        return "未要牌"
    return f"{len(cards)}张 · {_fv(_points(cards))}点"


def _player_status(p: PlayerHand, current_uid: int | None = None) -> str:
    tags: list[str] = []
    if p.user_id == current_uid:
        tags.append("行动中")
    if p.doubled:
        tags.append("已加倍")
    if p.is_natural:
        tags.append("十点半")
    elif p.is_five_small:
        tags.append("五小")
    elif p.busted:
        tags.append("爆牌")
    elif p.stood:
        tags.append("停牌")
    return " · ".join(tags)


def _dealer_public_brief(g: TenHalfGame, *, reveal: bool = False) -> str:
    count = len(g.dealer_cards)
    if count <= 0:
        return "未发牌"
    if reveal or g.phase == "finished":
        tags = [f"{count}张", f"{_fv(g.dealer_val())}点"]
        if g.dealer_busted():
            tags.append("爆牌")
        elif g.dealer_five_small():
            tags.append("五小")
        elif g.dealer_natural():
            tags.append("十点半")
        elif g.phase == "playing":
            tags.append("已停牌")
        return " · ".join(tags)
    visible_cards = g.dealer_cards[1:] if len(g.dealer_cards) > 1 else []
    visible = _fv(_points(visible_cards)) if visible_cards else "0"
    hidden = max(0, count - len(visible_cards))
    return f"{count}张（明牌 {visible}点，暗牌 {hidden}张）"


def _dealer_private_brief(g: TenHalfGame) -> str:
    if not g.dealer_cards:
        return "庄家还没有牌。"
    cards = "、".join(c.display() for c in g.dealer_cards)
    return f"庄家手牌：{cards}；共 {len(g.dealer_cards)} 张，{_fv(g.dealer_val())}点。"


def _send_action(
    text: str,
    *,
    reply_to_message_id: int | None = None,
    reply_markup: dict[str, Any] | None = None,
    save_message_id_key: str | None = None,
    replace_saved_message_id_key: str | None = None,
    send_via: str = "interaction_bot",
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "send_message",
        "text": text,
        "parse_mode": "html",
        "send_via": send_via,
    }
    if reply_to_message_id:
        action["reply_to_message_id"] = reply_to_message_id
    if reply_markup is not None:
        action["reply_markup"] = reply_markup
    if save_message_id_key:
        action["save_message_id_key"] = save_message_id_key
    if replace_saved_message_id_key:
        action["replace_saved_message_id_key"] = replace_saved_message_id_key
    return action


def _edit_action(
    message_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "edit_message",
        "message_id": message_id,
        "text": text,
        "parse_mode": "html",
        "send_via": "interaction_bot",
    }
    if reply_markup is not None:
        action["reply_markup"] = reply_markup
    return action


def _delete_action(message_id: int) -> dict[str, Any]:
    return {
        "type": "delete_message",
        "message_id": message_id,
        "send_via": "interaction_bot",
    }


def _answer_action(payload: dict[str, Any], text: str, *, show_alert: bool = False) -> dict[str, Any]:
    return {
        "type": "answer_callback",
        "callback_query_id": _ie_callback_id(payload),
        "text": text,
        "show_alert": show_alert,
    }


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

    @staticmethod
    def _remember_player_message(g: TenHalfGame, uid: int, mid: int | None) -> None:
        if uid and mid:
            g.player_message_ids[int(uid)] = int(mid)

    @staticmethod
    def _player_reply_message(g: TenHalfGame, uid: int) -> int | None:
        return g.player_message_ids.get(int(uid))

    @staticmethod
    async def _read_saved_message_id(ctx: PluginContext, key: str) -> int | None:
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return None
        try:
            raw = await redis.get(key)
        except Exception:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return _pint(raw, 0) or None

    def _receiver_label(
        self,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
        g: TenHalfGame | None = None,
    ) -> str:
        payload = payload or {}
        module_config = payload.get("module_config") if isinstance(payload.get("module_config"), dict) else {}
        cfg = ctx.config or {}
        label = (
            (g.payment_receiver_name if g else "")
            or _ie_payment_receiver(payload)
            or str(module_config.get("receiver_username") or module_config.get("receiver_name") or "").strip()
            or str(cfg.get("receiver_username") or cfg.get("receiver_name") or "").strip()
            or str(payload.get("payout_account_label") or "").strip()
        )
        return label or "本群 userbot"

    async def _main_action(
        self,
        ctx: PluginContext,
        g: TenHalfGame,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
        send_if_missing: bool = True,
    ) -> dict[str, Any] | None:
        key = _main_msg_key(ctx.account_id, g.chat_id)
        mid = g.main_message_id or await self._read_saved_message_id(ctx, key)
        if mid:
            g.main_message_id = mid
            return _edit_action(mid, text, reply_markup=reply_markup)
        if not send_if_missing:
            return None
        return _send_action(
            text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
            save_message_id_key=key,
        )

    async def _join_notice_actions(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        g: TenHalfGame,
        *,
        payer_name: str,
        amount: int,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        key = _join_notice_key(ctx.account_id, g.chat_id)
        previous_mid = g.join_notice_msg_id or await self._read_saved_message_id(ctx, key)
        actions.append(
            _send_action(
                self._build_join_notice_text(g, payer_name=payer_name, amount=amount),
                reply_to_message_id=_ie_mid(payload),
                save_message_id_key=key,
                replace_saved_message_id_key=key if not previous_mid else None,
            )
        )
        if previous_mid:
            g.join_notice_msg_id = previous_mid
            actions.append(_delete_action(previous_mid))
        return actions

    def _build_lobby_text(self, g: TenHalfGame, receiver_label: str) -> str:
        lines = [
            "🃏 <b>十点半开局！</b>",
            f"💰 底注: <b>{g.bet}</b>",
            "",
        ]
        if g.bet > 0:
            lines.append(
                f"📢 请转账 <b>{g.bet}</b> 给 <b>{_html(receiver_label)}</b> 即可参与本桌牌局～"
            )
        else:
            lines.append("📢 点击按钮或发送「加入」即可参与本桌牌局～")
        lines.append(
            f"⏰ 等待玩家加入中... ({self._lobby_timeout}秒)，当前牌桌 ID 为 <code>{g.game_id}</code>"
        )
        if g.lobby_players:
            players = "、".join(_html(name) for _, name in g.lobby_players)
            lines.extend([
                "",
                f"👥 已加入 ({len(g.lobby_players)}/{self._max_players}): {players}",
            ])
        return "\n".join(lines)

    def _build_join_notice_text(self, g: TenHalfGame, *, payer_name: str, amount: int) -> str:
        players = "、".join(_html(name) for _, name in g.lobby_players) or "暂无"
        return "\n".join([
            f"✅ <b>{_html(payer_name)}</b> 加入牌局成功",
            f"🆔 牌桌 ID: <code>{g.game_id}</code>",
            f"💰 入场金额: {amount}",
            f"👥 当前玩家 ({len(g.lobby_players)}/{self._max_players}): {players}",
        ])

    def _build_ask_dealer_text(self, g: TenHalfGame) -> str:
        players = "、".join(_html(name) for _, name in g.lobby_players)
        return "\n".join([
            f"🃏 <b>十点半 · 牌桌 <code>{g.game_id}</code></b>",
            f"💰 底注: <b>{g.bet}</b>",
            f"👥 参与玩家: {players}",
            "",
            f"❓ <b>{_html(g.ask_dealer_name)}</b>，你要当庄家吗？",
        ])

    def _build_ix_state_text(self, g: TenHalfGame, *, reveal_dealer: bool = False) -> str:
        phase_text = {
            "dealer_turn": "庄家行动",
            "playing": "玩家行动",
            "finished": "已结算",
        }.get(g.phase, "进行中")
        lines = [
            f"🃏 <b>十点半 · 牌桌 <code>{g.game_id}</code></b>",
            f"💰 底注: <b>{g.bet}</b> · {phase_text}",
            "",
            f"🎰 庄家 <b>{_html(g.dealer_name)}</b>: {_dealer_public_brief(g, reveal=reveal_dealer)}",
        ]
        if g.phase == "dealer_turn" and not g.dealer_is_bot and not g.finished:
            lines.append("👉 庄家请先要牌或停牌。")
        if g.players:
            lines.extend(["", "👥 玩家"])
            current_uid = None
            if g.phase == "playing" and 0 <= g.current_player_idx < len(g.players):
                current_uid = g.players[g.current_player_idx].user_id
            for p in g.players:
                status = _player_status(p, current_uid=current_uid)
                suffix = f" · {status}" if status else ""
                lines.append(f"• <b>{_html(p.name)}</b>: {_cards_brief(p.cards)}{suffix}")
        if g.status_note:
            lines.extend(["", _html(g.status_note)])
        return "\n".join(lines)

    async def _enter_ask_dealer(
        self,
        ctx: PluginContext,
        cid: int,
        g: TenHalfGame,
    ) -> list[dict[str, Any]]:
        first_id, first_name = g.lobby_players[0]
        g.phase = "ask_dealer"
        g.ask_dealer_uid = first_id
        g.ask_dealer_name = first_name
        g.status_note = ""
        if ctx.log:
            await ctx.log(
                "info",
                f"[ten_half] ask_dealer: uid={first_id}, name={first_name}, "
                f"players={[n for _, n in g.lobby_players]}, chat_id={cid}",
            )
        if not g.via_interaction and not g.dealer_timeout_started:
            g.dealer_timeout_started = True
            self._track(asyncio.create_task(
                self._dealer_question_timeout(cid, g.started_at, ctx),
            ))
        return [
            await self._main_action(
                ctx,
                g,
                self._build_ask_dealer_text(g),
                reply_markup=_kb_dealer(first_id),
            )
        ]

    async def _send(self, ctx: PluginContext, cid: int, text: str, *,
                    parse_mode: str = "html", reply_to: int | None = None) -> None:
        """安全发送消息（用于异步任务/命令流）。"""
        if not ctx.client:
            return
        try:
            await ctx.client.send_message(
                cid, text, parse_mode=parse_mode,
                **({"reply_to": reply_to} if reply_to else {}),
            )
        except Exception:
            pass

    async def _send_ix_actions(self, ctx: PluginContext, cid: int, actions: list[dict[str, Any]]) -> None:
        """Send interaction actions from background timeout tasks."""
        if not ctx.client:
            return
        for action in actions:
            if not isinstance(action, dict) or action.get("type") not in {"send_message", "edit_message"}:
                continue
            text = str(action.get("text") or "").strip()
            if not text:
                continue
            kwargs: dict[str, Any] = {}
            if action.get("parse_mode"):
                kwargs["parse_mode"] = action.get("parse_mode")
            if action.get("reply_markup"):
                kwargs["reply_markup"] = action.get("reply_markup")
            reply_to = action.get("reply_to_message_id")
            if reply_to:
                kwargs["reply_to"] = reply_to
            try:
                if action.get("type") == "edit_message" and action.get("message_id"):
                    await ctx.client.edit_message(cid, int(action["message_id"]), text, **kwargs)
                else:
                    await ctx.client.send_message(cid, text, **kwargs)
            except Exception:
                try:
                    await ctx.client.send_message(cid, text, **kwargs)
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

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] game_start: chat_id={cid}, bet={bet}, "
                f"lobby_timeout={self._lobby_timeout}, via_interaction=False")

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
                if g.via_interaction:
                    g.status_note = "没人加入，牌局已取消。"
                else:
                    await self._send(ctx, cid, "⏰ 没人加入，十点半游戏取消。")
                return
            if g.via_interaction:
                # Interaction-visible messages must be returned as standard
                # actions from on_interaction. A background task only mutates
                # state here; otherwise it would send via the userbot client.
                g.status_note = "大厅等待已结束，请等待下一次交互刷新牌桌。"
            else:
                await self._ask_dealer(cid, g, ctx)

    async def _ask_dealer(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """向第一个加入的玩家询问是否当庄家 (userbot 流)。"""
        first_id, first_name = g.lobby_players[0]
        g.phase = "ask_dealer"
        g.ask_dealer_uid = first_id
        g.ask_dealer_name = first_name

        plist = "、".join(n for _, n in g.lobby_players)
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] ask_dealer: uid={first_id}, name={first_name}, "
                f"players={plist}, chat_id={cid}")
        await self._send(
            ctx, cid,
            f"👥 参与玩家: {plist}\n\n"
            f"❓ <b>{first_name}</b>，你要当庄家吗？\n"
            f"回复 <b>「是」</b> 当庄家 或 <b>「否」</b> 让机器人当庄家",
        )
        self._track(asyncio.create_task(
            self._dealer_question_timeout(cid, g.started_at, ctx),
        ))

    async def _ask_dealer_ix(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """向第一个加入的玩家询问是否当庄家 (interaction 流, 通过 ctx.client 发送)。"""
        actions = await self._enter_ask_dealer(ctx, cid, g)
        await self._send_ix_actions(ctx, cid, actions)

    async def _dealer_question_timeout(self, cid: int, sa: float, ctx: PluginContext) -> None:
        await asyncio.sleep(30)
        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.phase != "ask_dealer" or g.finished or g.started_at != sa:
                return
            # 超时默认机器人当庄
            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] dealer_question_timeout: defaulting to bot dealer, "
                    f"chat_id={cid}")
            if g.via_interaction:
                # Do not auto-pick a bot dealer from a background task: it
                # cannot return Bot API actions, so the follow-up buttons would
                # be sent by the userbot and become non-interactive.
                g.status_note = "选庄等待已结束，请重新开桌或让候选玩家点击选庄按钮。"
            else:
                await self._begin_game(cid, g, dealer_id=0, dealer_name="🤖 庄家", ctx=ctx)

    # ═══════════════════════════════════════════════════
    # 开局发牌 (userbot 流)
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

        if ctx.log:
            player_names = [p.name for p in g.players]
            await ctx.log("info",
                f"[ten_half] game_begin: dealer={dealer_name} (uid={dealer_id}), "
                f"players={player_names}, bet={g.bet}, chat_id={cid}")
            for p in g.players:
                await ctx.log("info",
                    f"[ten_half] card_dealt: uid={p.user_id}, name={p.name}, "
                    f"cards={p.hand_str()}, natural={p.is_natural}, chat_id={cid}")

        # 展示初始状态
        await self._send(ctx, cid, self._build_state_text(g))

        # 庄家天生十点半 → 直接结算
        if g.dealer_natural():
            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] dealer_natural: dealer={g.dealer_name}, chat_id={cid}")
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
    # 回合推进 (userbot 流)
    # ═══════════════════════════════════════════════════
    async def _advance_turn(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """跳过已完成的玩家，找到下一个可行动的玩家或进入庄家回合。"""
        if g.phase != "playing":
            return

        g.current_turn = g.current_player_idx

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
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] turn_start: uid={p.user_id}, name={p.name}, "
                f"value={_fv(p.value)}, cards={len(p.cards)}, chat_id={cid}")
        await self._send(
            ctx, cid,
            f"⏳ 轮到 <b>{p.name}</b> 行动\n"
            f"指令: 要牌 / 停牌 / 加倍",
        )
        self._track_task(asyncio.create_task(
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

            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] turn_timeout: uid={p.user_id}, name={p.name}, "
                    f"auto_stand=True, chat_id={cid}")

            if g.via_interaction:
                g.status_note = f"{p.name} 超时，自动停牌。"
                g.current_player_idx += 1
            else:
                await self._send(ctx, cid, f"⏰ {p.name} 超时，自动停牌。")
                g.current_player_idx += 1
                await self._advance_turn(cid, g, ctx)

    async def _ix_advance_and_send(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """Advance turn in interaction flow and send messages via ctx.client.

        Used by timeout tasks where on_interaction already returned.
        """
        if g.phase != "playing":
            return

        g.current_turn = g.current_player_idx

        while g.current_player_idx < len(g.players):
            p = g.players[g.current_player_idx]
            if p.is_done:
                g.current_player_idx += 1
                continue
            if p.is_natural:
                p.stood = True
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] natural_detected: uid={p.user_id}, name={p.name}, "
                        f"value={_fv(p.value)}, chat_id={cid}")
                try:
                    if ctx.client:
                        await ctx.client.send_message(
                            cid, f"✨ <b>{p.name}</b> 天生十点半！自动停牌。", parse_mode="html",
                        )
                except Exception:
                    pass
                g.current_player_idx += 1
                continue
            if p.is_five_small:
                p.stood = True
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] five_small_detected: uid={p.user_id}, name={p.name}, "
                        f"cards={len(p.cards)}, value={_fv(p.value)}, chat_id={cid}")
                try:
                    if ctx.client:
                        await ctx.client.send_message(
                            cid, f"🌟 <b>{p.name}</b> 五小！自动停牌。", parse_mode="html",
                        )
                except Exception:
                    pass
                g.current_player_idx += 1
                continue
            if p.value > 10.5 + 1e-9:
                p.busted = True
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] bust_detected: uid={p.user_id}, name={p.name}, "
                        f"value={_fv(p.value)}, chat_id={cid}")
                g.current_player_idx += 1
                continue
            break

        if g.current_player_idx >= len(g.players):
            await self._dealer_play_ix(cid, g, ctx)
            return

        p = g.players[g.current_player_idx]
        can_double = len(p.cards) == 2
        markup = _kb_turn(p.user_id, can_double=can_double)
        self._track_task(asyncio.create_task(
            self._turn_timeout_task(cid, g.current_player_idx, g.started_at, ctx),
        ))
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] turn_start: uid={p.user_id}, name={p.name}, "
                f"value={_fv(p.value)}, cards={len(p.cards)}, chat_id={cid}")
        try:
            if ctx.client:
                await ctx.client.send_message(
                    cid,
                    f"⏳ 轮到 <b>{p.name}</b> 行动",
                    parse_mode="html",
                    reply_markup=markup,
                )
        except Exception:
            pass

    async def _dealer_play_ix(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        """Dealer turn + settle in interaction flow, sent via ctx.client."""
        g.phase = "dealer_turn"
        all_bust = all(p.busted for p in g.players)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] dealer_turn: dealer={g.dealer_name}, "
                f"all_bust={all_bust}, chat_id={cid}")

        msgs: list[str] = []
        msgs.append(
            f"🎰 <b>{g.dealer_name}</b> 亮牌！\n"
            f"手牌: {g.dealer_hand_str(reveal=True)}"
        )

        if all_bust:
            msgs.append(f"💀 所有玩家都爆牌，{g.dealer_name} 自动获胜！")
        else:
            while g.dealer_val() <= 5.0 + 1e-9:
                if not g.deck:
                    g.deck = create_deck()
                card = g.deck.pop()
                g.dealer_cards.append(card)
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] dealer_draw: card={card.display()}, "
                        f"dealer_value={_fv(g.dealer_val())}, busted={g.dealer_busted()}, "
                        f"chat_id={cid}")
                msgs.append(
                    f"  🎰 {g.dealer_name} 要牌 {card.display()}\n"
                    f"  手牌: {g.dealer_hand_str(reveal=True)}"
                )
                if g.dealer_busted():
                    msgs.append(f"  💥 {g.dealer_name} 爆牌！")
                    break
            else:
                if not g.dealer_busted():
                    msgs.append(f"  ✅ {g.dealer_name} 停牌 ({_fv(g.dealer_val())}点)")

        # Build settlement text
        g.phase = "finished"
        g.finished = True
        dv = g.dealer_val()
        db = g.dealer_busted()
        dn = g.dealer_natural()
        dfs = g.dealer_five_small()

        # 总底注池
        total_pot = sum(g.bet * (2 if p.doubled else 1) for p in g.players)
        lines = ["🏆 <b>结算</b>\n"]
        lines.append(f"💰 总底注池: {total_pot}\n")
        lines.append(f"庄家 {g.dealer_name}: {g.dealer_hand_str(reveal=True)}\n")
        for p in g.players:
            eb = g.bet * (2 if p.doubled else 1)
            outcome = self._compare(p, dv, db, dn, dfs)
            mult = 2.0 if outcome == "win_nat" else 1.5 if outcome == "win_5s" else 1.0 if outcome == "win" else 0.0
            reward = int(total_pot * mult * 0.9) if mult > 0 else 0
            display = self._outcome_str(outcome, eb)
            if reward > 0:
                display += f" → 获得 {reward}"
            lines.append(f"👤 {p.name}: {p.hand_str()} → {display}")
            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] settlement: uid={p.user_id}, name={p.name}, "
                    f"outcome={outcome}, multiplier={mult}, reward={reward}, "
                    f"bet={eb}, total_pot={total_pot}, chat_id={cid}")
        msgs.append("\n".join(lines))

        # Send all messages via ctx.client
        try:
            if ctx.client:
                for msg in msgs:
                    await ctx.client.send_message(cid, msg, parse_mode="html")
        except Exception:
            pass

        # Send individual reward messages via userbot fallback. Background timeout
        # tasks cannot return actions to TelePilot's delivery executor.
        for p in g.players:
            outcome = self._compare(p, dv, db, dn, dfs)
            if outcome.startswith("win"):
                mult = 2.0 if outcome == "win_nat" else 1.5 if outcome == "win_5s" else 1.0
                reward = int(total_pot * mult * 0.9)
                reply_to = self._player_reply_message(g, p.user_id)
                try:
                    if ctx.client:
                        await ctx.client.send_message(
                            cid,
                            f"+{reward}",
                            **({"reply_to": reply_to} if reply_to else {}),
                        )
                except Exception:
                    pass
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] reward_sent: uid={p.user_id}, name={p.name}, "
                        f"amount={reward}, chat_id={cid}, background=True")
        self._games.pop(cid, None)

    # ═══════════════════════════════════════════════════
    # 玩家动作 (userbot 流)
    # ═══════════════════════════════════════════════════
    async def _act_hit(self, cid: int, g: TenHalfGame, pi: int, ctx: PluginContext) -> None:
        p = g.players[pi]
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=hit, card={card.display()}, new_value={_fv(p.value)}, "
                f"busted={p.value > 10.5 + 1e-9}, chat_id={cid}")

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
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=stand, value={_fv(p.value)}, chat_id={cid}")
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

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=double, card={card.display()}, new_value={_fv(p.value)}, "
                f"bet_doubled={g.bet * 2}, busted={p.value > 10.5 + 1e-9}, chat_id={cid}")

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
    # 庄家回合 (userbot 流)
    # ═══════════════════════════════════════════════════
    async def _dealer_play(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        g.phase = "dealer_turn"
        all_bust = all(p.busted for p in g.players)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] dealer_turn: dealer={g.dealer_name}, "
                f"all_bust={all_bust}, chat_id={cid}")

        await self._send(
            ctx, cid,
            f"🎰 <b>{g.dealer_name}</b> 亮牌！\n"
            f"手牌: {g.dealer_hand_str(reveal=True)}")

        if all_bust:
            await self._send(ctx, cid, f"💀 所有玩家都爆牌，{g.dealer_name} 自动获胜！")
        else:
            while g.dealer_val() <= 5.0 + 1e-9:
                if not g.deck:
                    g.deck = create_deck()
                card = g.deck.pop()
                g.dealer_cards.append(card)
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] dealer_draw: card={card.display()}, "
                        f"dealer_value={_fv(g.dealer_val())}, busted={g.dealer_busted()}, "
                        f"chat_id={cid}")
                await self._send(
                    ctx, cid,
                    f"  🎰 {g.dealer_name} 要牌 {card.display()}\n"
                    f"  手牌: {g.dealer_hand_str(reveal=True)}")
                if g.dealer_busted():
                    await self._send(ctx, cid, f"  💥 {g.dealer_name} 爆牌！")
                    break
            else:
                if not g.dealer_busted():
                    await self._send(
                        ctx, cid,
                        f"  ✅ {g.dealer_name} 停牌 ({_fv(g.dealer_val())}点)")

        await self._settle(cid, g, ctx)

    # ═══════════════════════════════════════════════════
    # 结算 (userbot 流)
    # ═══════════════════════════════════════════════════
    async def _settle(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        g.phase = "finished"
        g.finished = True

        dv = g.dealer_val()
        db = g.dealer_busted()
        dn = g.dealer_natural()
        dfs = g.dealer_five_small()

        # 总底注池
        total_pot = sum(g.bet * (2 if p.doubled else 1) for p in g.players)
        lines = ["🏆 <b>结算</b>\n"]
        lines.append(f"💰 总底注池: {total_pot}\n")
        lines.append(f"庄家 {g.dealer_name}: {g.dealer_hand_str(reveal=True)}\n")

        for p in g.players:
            eb = g.bet * (2 if p.doubled else 1)
            outcome = self._compare(p, dv, db, dn, dfs)
            mult = 2.0 if outcome == "win_nat" else 1.5 if outcome == "win_5s" else 1.0 if outcome == "win" else 0.0
            reward = int(total_pot * mult * 0.9) if mult > 0 else 0
            display = self._outcome_str(outcome, eb)
            if reward > 0:
                display += f" → 获得 {reward}"
            lines.append(f"👤 {p.name}: {p.hand_str()} → {display}")
            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] settlement: uid={p.user_id}, name={p.name}, "
                    f"outcome={outcome}, multiplier={mult}, reward={reward}, "
                    f"bet={eb}, total_pot={total_pot}, chat_id={cid}")

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
    # on_message (userbot 流)
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
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_joined: uid={uid}, name={name}, "
                f"via=keyword, chat_id={cid}, count={cnt}/{self._max_players}")
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

        if etype == "payment_confirmed":
            return await self._ix_payment_join(ctx, payload, cid)
        if etype == "keyword":
            return await self._ix_start(ctx, payload, cid)
        if etype == "callback_query":
            return await self._ix_callback(ctx, payload, cid)
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
                _send_action(
                    f"请指定下注金额。例：{{prefix}}{self._command} 100",
                    reply_to_message_id=_ie_mid(payload),
                ),
                {"type": "end_session"},
            ]

        async with self._lock(cid):
            if cid in self._games and not self._games[cid].finished:
                return [
                    _send_action(
                        "⚠️ 当前已有进行中的十点半游戏。",
                        reply_to_message_id=_ie_mid(payload),
                    )
                ]
            g = TenHalfGame(
                chat_id=cid, bet=bet,
                phase="lobby", started_at=time.monotonic(),
                via_interaction=True,
            )
            g.payment_receiver_name = self._receiver_label(ctx, payload, g)
            self._games[cid] = g

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] game_start: chat_id={cid}, bet={bet}, "
                f"lobby_timeout={self._lobby_timeout}, via_interaction=True")

        self._track(asyncio.create_task(
            self._lobby_timeout_task(cid, g.started_at, ctx),
        ))

        reply_markup = _kb_join(bet)
        return [
            await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, g.payment_receiver_name),
                reply_markup=reply_markup,
                reply_to_message_id=_ie_mid(payload),
            )
        ]


    # ── 交互：转账加入 ────────────────────────────────
    async def _ix_payment_join(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        """payment_confirmed: 玩家转账给管理员(userbot)。

        有活跃大厅 → 加入；没有大厅则提示先开桌。
        """
        payer_id, payer_name = _ie_payer(payload)
        amount = _ie_payment_amount(payload)
        mid = _ie_mid(payload)
        payment_status = _ie_payment_status(payload)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] payment_confirmed: payer={payer_id} ({payer_name}), "
                f"amount={amount}, status={payment_status}, chat_id={cid}")

        if payment_status and payment_status != "confirmed":
            return [
                _send_action(
                    "⚠️ 这笔转账尚未确认到账，暂不能加入牌局。",
                    reply_to_message_id=mid,
                )
            ]
        if not payer_id:
            return [
                _send_action(
                    "⚠️ 未能识别付款人，请按付款确认按钮绑定身份后再加入。",
                    reply_to_message_id=mid,
                )
            ]

        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.finished:
                return [
                    _send_action(
                        f"⚠️ 暂无等待中的十点半牌局，请先发送 {self._command} 下注金额 开桌。",
                        reply_to_message_id=mid,
                    ),
                    {"type": "no_session"},
                ]
            if not g.via_interaction:
                return [
                    _send_action(
                        "⚠️ 当前牌局不是交互 Bot 开局，请按原牌局提示加入。",
                        reply_to_message_id=mid,
                    )
                ]
            if not g.payment_receiver_name:
                g.payment_receiver_name = self._receiver_label(ctx, payload, g)

            if g.phase != "lobby":
                return [
                    _send_action(
                        "⚠️ 本桌牌局已经开始，不能继续加入。",
                        reply_to_message_id=mid,
                    )
                ]

            if amount != g.bet:
                return [
                    _send_action(
                        f"⚠️ 入场金额需为 {g.bet}，你转了 {amount}，本次未加入。",
                        reply_to_message_id=mid,
                    )
                ]

            if len(g.lobby_players) >= self._max_players:
                return [
                    _send_action("⚠️ 人数已满。", reply_to_message_id=mid)
                ]

            for uid, _ in g.lobby_players:
                if uid == payer_id:
                    return [
                        _send_action("⚠️ 你已经加入了。", reply_to_message_id=mid)
                    ]

            g.lobby_players.append((payer_id, payer_name))
            self._remember_player_message(g, payer_id, mid)
            cnt = len(g.lobby_players)

            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] player_joined: uid={payer_id}, name={payer_name}, "
                    f"via=payment, amount={amount}, count={cnt}/{self._max_players}, chat_id={cid}")

            actions = await self._join_notice_actions(
                ctx,
                payload,
                g,
                payer_name=payer_name,
                amount=amount,
            )

            if cnt >= 2:
                actions.extend(await self._enter_ask_dealer(ctx, cid, g))
                return actions

            main_update = await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, self._receiver_label(ctx, payload, g)),
                reply_markup=_kb_join(g.bet),
                send_if_missing=False,
            )
            if main_update is not None:
                actions.append(main_update)
            return actions

    # ── 交互：callback_query 处理 ────────────────────
    async def _ix_callback(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        """Handle callback_query events from inline keyboard buttons.

        Callback data format: th:<action>:<id>
        Actions: join, dealer_yes, dealer_no, hit, stand, double
        """
        callback_data = _ie_callback_data(payload)
        if not callback_data:
            return []

        parts = callback_data.split(":")
        if len(parts) != 3 or parts[0] != "th":
            return []

        action = parts[1]
        try:
            cb_id = int(parts[2])
        except (ValueError, TypeError):
            return []

        aid, aname = _ie_actor(payload)
        mid = _ie_mid(payload)

        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.finished:
                return [{"type": "no_session"}]
            callback_message_id = _ie_message_mid(payload)
            if callback_message_id:
                g.main_message_id = callback_message_id

            # ── join ──
            if action == "join":
                result = await self._ix_join(ctx, payload, g, aid, aname)
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] player_joined: uid={aid}, name={aname}, "
                        f"via=button, chat_id={cid}, count={len(g.lobby_players)}/{self._max_players}")
                return result

            # ── dealer_yes / dealer_no ──
            if action in ("dealer_yes", "dealer_no"):
                return await self._ix_dealer_choice(g, action, aid, aname, ctx, payload)

            # ── hit / stand / double ──
            if action in ("hit", "stand", "double"):
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] player_action_input: uid={aid}, name={aname}, "
                        f"action={action} (callback), chat_id={cid}")
                return await self._ix_player_action(g, action, aid, mid, ctx, payload)

        return []

    async def _ix_join(
        self, ctx: PluginContext, payload: dict[str, Any],
        g: TenHalfGame, aid: int, aname: str,
    ) -> list[dict[str, Any]]:
        """Handle join button press."""
        mid = _ie_mid(payload)
        is_callback = bool(_ie_callback_id(payload))

        def hint(text: str) -> dict[str, Any]:
            if is_callback:
                return _answer_action(payload, text)
            return _send_action(f"⚠️ {text}", reply_to_message_id=mid)

        if g.phase != "lobby":
            return [hint("游戏不在大厅阶段。")]

        for uid, _ in g.lobby_players:
            if uid == aid:
                return [hint("你已经加入了。")]
        if len(g.lobby_players) >= self._max_players:
            return [hint("人数已满。")]

        g.lobby_players.append((aid, aname))
        self._remember_player_message(g, aid, mid)
        cnt = len(g.lobby_players)
        if is_callback:
            result: list[dict[str, Any]] = [_answer_action(payload, f"加入成功，牌桌 {g.game_id}")]
        else:
            result = [
                _send_action(
                    f"✅ <b>{_html(aname)}</b> 加入牌局成功\n🆔 牌桌 ID: <code>{g.game_id}</code>",
                    reply_to_message_id=mid,
                )
            ]

        if cnt >= 2:
            result.extend(await self._enter_ask_dealer(ctx, g.chat_id, g))
        else:
            result.append(
                await self._main_action(
                    ctx,
                    g,
                    self._build_lobby_text(g, self._receiver_label(ctx, payload, g)),
                    reply_markup=_kb_join(g.bet),
                )
            )
        return result

    async def _ix_dealer_choice(
        self, g: TenHalfGame, action: str, aid: int, aname: str,
        ctx: PluginContext, payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Handle dealer_yes / dealer_no button press."""
        if g.phase != "ask_dealer":
            return [_answer_action(payload, "当前不在选庄阶段。")]

        if aid != g.ask_dealer_uid:
            return [_answer_action(payload, "点点点！啥你都点！问你了吗！")]

        if ctx.log:
            choice = "dealer_yes" if action == "dealer_yes" else "dealer_no"
            await ctx.log("info",
                f"[ten_half] dealer_choice: uid={aid}, name={aname}, "
                f"choice={choice}, chat_id={g.chat_id}")

        if action == "dealer_yes":
            return await self._ix_begin(g.chat_id, g, aid, aname, ctx, payload=payload)
        else:  # dealer_no
            actions = [_answer_action(payload, "本局由机器人当庄。", show_alert=False)]
            actions.extend(await self._ix_begin(g.chat_id, g, 0, "🤖 庄家", ctx))
            return actions

    async def _ix_player_action(
        self, g: TenHalfGame, action: str, aid: int, mid: int | None,
        ctx: PluginContext, payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Handle hit/stand/double button press."""
        if g.phase == "dealer_turn":
            if g.dealer_is_bot:
                return [_answer_action(payload or {}, "机器人庄家正在行动。")] if payload else []
            if aid != g.dealer_id:
                return [_answer_action(payload or {}, "还没轮到你。")] if payload else []
            if action == "hit":
                return await self._ix_dealer_hit(g.chat_id, g, ctx, payload)
            if action == "stand":
                return await self._ix_dealer_stand(g.chat_id, g, ctx, payload)
            return [_answer_action(payload or {}, "庄家不能加倍。")] if payload else []

        if g.phase != "playing":
            if payload:
                return [_answer_action(payload, "游戏不在进行中。")]
            return [_send_action("⚠️ 游戏不在进行中。", reply_to_message_id=mid)]
        if g.current_player_idx >= len(g.players):
            return []

        cur = g.players[g.current_player_idx]
        if aid != cur.user_id:
            if payload:
                return [_answer_action(payload, "还没轮到你。")]
            return []

        if action == "hit":
            return await self._ix_hit(g.chat_id, g, ctx, payload)
        elif action == "stand":
            return await self._ix_stand(g.chat_id, g, ctx, payload)
        elif action == "double":
            return await self._ix_double(g.chat_id, g, ctx, payload)
        return []

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
                if text in ("加入", "join"):
                    if g.bet > 0:
                        # Paid game: hint to pay instead
                        return [
                            _send_action(
                                f"请先转账 <b>{g.bet}</b> 给 <b>{_html(self._receiver_label(ctx, payload, g))}</b> 加入牌局。",
                                reply_to_message_id=mid,
                            )
                        ]
                    # Free game: join directly
                    result = await self._ix_join(ctx, payload, g, aid, aname)
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] player_joined: uid={aid}, name={aname}, "
                            f"via=keyword, chat_id={cid}")
                    return result
                return []

            # ── 选庄 (text fallback alongside buttons) ──
            if g.phase == "ask_dealer":
                if aid != g.ask_dealer_uid:
                    return []
                if text in ("是", "yes", "对", "好"):
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] dealer_choice: uid={aid}, name={aname}, "
                            f"choice=dealer_yes, chat_id={cid}")
                    return await self._ix_begin(cid, g, aid, aname, ctx)
                if text in ("否", "no", "不"):
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] dealer_choice: uid={aid}, name={aname}, "
                            f"choice=dealer_no, chat_id={cid}")
                    return await self._ix_begin(cid, g, 0, "🤖 庄家", ctx)
                return []

            if g.phase == "dealer_turn":
                if aid != g.dealer_id:
                    return []
                if text in ("要牌", "hit", "拿牌"):
                    return await self._ix_dealer_hit(cid, g, ctx)
                if text in ("停牌", "stand", "停"):
                    return await self._ix_dealer_stand(cid, g, ctx)
                return []

            # ── 游戏中 (text fallback alongside buttons) ──
            if g.phase == "playing":
                if g.current_player_idx >= len(g.players):
                    return []
                cur = g.players[g.current_player_idx]

                if aid != cur.user_id:
                    if text in ("手牌", "牌"):
                        for p in g.players:
                            if p.user_id == aid:
                                return [_send_action(f"🃏 {p.name}: {_cards_brief(p.cards)}", reply_to_message_id=mid)]
                    return []

                if text in ("要牌", "hit", "拿牌"):
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] player_action_input: uid={aid}, name={aname}, "
                            f"action=hit (text), chat_id={cid}")
                    return await self._ix_hit(cid, g, ctx)
                if text in ("停牌", "stand", "停"):
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] player_action_input: uid={aid}, name={aname}, "
                            f"action=stand (text), chat_id={cid}")
                    return await self._ix_stand(cid, g, ctx)
                if text in ("加倍", "double"):
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] player_action_input: uid={aid}, name={aname}, "
                            f"action=double (text), chat_id={cid}")
                    return await self._ix_double(cid, g, ctx)
                if text in ("手牌", "牌"):
                    return [_send_action(f"🃏 你的手牌: {_cards_brief(cur.cards)}", reply_to_message_id=mid)]
                return []

        return []

    # ── 交互：开局发牌 ──────────────────────────────
    async def _ix_begin(
        self, cid: int, g: TenHalfGame,
        dealer_id: int, dealer_name: str, ctx: PluginContext,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        g.dealer_id = dealer_id
        g.dealer_name = dealer_name
        g.deck = create_deck()
        g.dealer_cards.clear()
        g.players.clear()
        g.current_player_idx = 0
        g.current_turn = 0
        g.status_note = ""

        for uid, name in g.lobby_players:
            if uid != dealer_id:
                g.players.append(PlayerHand(user_id=uid, name=name))

        if not g.players:
            g.finished = True
            self._games.pop(cid, None)
            return [
                await self._main_action(ctx, g, "⚠️ 没有其他玩家，游戏取消。"),
                {"type": "end_session"},
            ]

        for p in g.players:
            p.cards.append(g.deck.pop())
        g.dealer_cards.append(g.deck.pop())
        g.dealer_cards.append(g.deck.pop())

        g.phase = "dealer_turn"
        g.turn_order = [p.user_id for p in g.players]

        if ctx.log:
            player_names = [p.name for p in g.players]
            await ctx.log("info",
                f"[ten_half] game_begin: dealer={dealer_name} (uid={dealer_id}), "
                f"players={player_names}, bet={g.bet}, chat_id={cid}")

        if g.dealer_is_bot:
            return await self._ix_bot_dealer_play(cid, g, ctx)

        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, _dealer_private_brief(g), show_alert=True))
        g.status_note = f"{g.dealer_name} 当庄，玩家起手 1 张明牌，庄家先行动。"
        actions.append(
            await self._main_action(
                ctx,
                g,
                self._build_ix_state_text(g),
                reply_markup=_kb_turn(g.dealer_id, can_double=False),
            )
        )
        return actions

    async def _ix_bot_dealer_play(
        self, cid: int, g: TenHalfGame, ctx: PluginContext,
    ) -> list[dict[str, Any]]:
        g.phase = "dealer_turn"
        g.status_note = "机器人庄家先行动。"
        while g.dealer_val() <= 5.0 + 1e-9:
            if not g.deck:
                g.deck = create_deck()
            card = g.deck.pop()
            g.dealer_cards.append(card)
            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] dealer_draw: card={card.display()}, "
                    f"dealer_value={_fv(g.dealer_val())}, busted={g.dealer_busted()}, "
                    f"chat_id={cid}")
            if g.dealer_busted():
                break

        if g.dealer_busted():
            g.status_note = f"{g.dealer_name} 爆牌，本局直接结算。"
            return await self._ix_settle(cid, g, ctx)

        g.status_note = f"{g.dealer_name} 已停牌，共 {len(g.dealer_cards)} 张。"
        actions = [
            await self._main_action(
                ctx,
                g,
                self._build_ix_state_text(g),
            )
        ]
        g.phase = "playing"
        g.current_player_idx = 0
        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    async def _ix_dealer_hit(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        g.dealer_cards.append(card)
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] dealer_action: action=hit, card={card.display()}, "
                f"value={_fv(g.dealer_val())}, busted={g.dealer_busted()}, chat_id={cid}")
        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, _dealer_private_brief(g), show_alert=True))
        if g.dealer_busted():
            g.status_note = f"{g.dealer_name} 要牌后爆牌，本局直接结算。"
            actions.extend(await self._ix_settle(cid, g, ctx))
            return actions
        g.status_note = f"{g.dealer_name} 已要牌，当前 {len(g.dealer_cards)} 张。"
        actions.append(
            await self._main_action(
                ctx,
                g,
                self._build_ix_state_text(g),
                reply_markup=_kb_turn(g.dealer_id, can_double=False),
            )
        )
        return actions

    async def _ix_dealer_stand(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] dealer_action: action=stand, value={_fv(g.dealer_val())}, "
                f"cards={len(g.dealer_cards)}, chat_id={cid}")
        g.status_note = f"{g.dealer_name} 停牌，共 {len(g.dealer_cards)} 张。"
        g.phase = "playing"
        g.current_player_idx = 0
        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, _dealer_private_brief(g), show_alert=True))
        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：回合推进 ──────────────────────────────
    async def _ix_advance(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> list[dict[str, Any]]:
        if g.phase != "playing":
            return []

        g.current_turn = g.current_player_idx
        actions: list[dict[str, Any]] = []

        while g.current_player_idx < len(g.players):
            p = g.players[g.current_player_idx]
            if p.is_done:
                g.current_player_idx += 1
                continue
            if p.is_natural:
                p.stood = True
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] natural_detected: uid={p.user_id}, name={p.name}, "
                        f"value={_fv(p.value)}, chat_id={cid}")
                g.status_note = f"{p.name} 十点半，自动停牌。"
                g.current_player_idx += 1
                continue
            if p.is_five_small:
                p.stood = True
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] five_small_detected: uid={p.user_id}, name={p.name}, "
                        f"cards={len(p.cards)}, value={_fv(p.value)}, chat_id={cid}")
                g.status_note = f"{p.name} 五小，自动停牌。"
                g.current_player_idx += 1
                continue
            if p.value > 10.5 + 1e-9:
                p.busted = True
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] bust_detected: uid={p.user_id}, name={p.name}, "
                        f"value={_fv(p.value)}, chat_id={cid}")
                g.current_player_idx += 1
                continue
            break

        if g.current_player_idx >= len(g.players):
            actions.extend(await self._ix_settle(cid, g, ctx))
            return actions

        p = g.players[g.current_player_idx]
        can_double = len(p.cards) == 2
        self._track_task(asyncio.create_task(
            self._turn_timeout_task(cid, g.current_player_idx, g.started_at, ctx),
        ))
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] turn_start: uid={p.user_id}, name={p.name}, "
                f"value={_fv(p.value)}, cards={len(p.cards)}, chat_id={cid}")
        g.status_note = f"轮到 {p.name} 行动。"
        actions.append(
            await self._main_action(
                ctx,
                g,
                self._build_ix_state_text(g),
                reply_markup=_kb_turn(p.user_id, can_double=can_double),
            )
        )
        return actions

    # ── 交互：要牌 ──────────────────────────────────
    async def _ix_hit(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        pi = g.current_player_idx
        p = g.players[pi]
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=hit, card={card.display()}, new_value={_fv(p.value)}, "
                f"busted={p.value > 10.5 + 1e-9}, chat_id={cid}")

        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, f"要到 {card.display()}，当前 {_fv(p.value)}点。"))
        if p.value > 10.5 + 1e-9:
            p.busted = True
            g.status_note = f"{p.name} 要牌后爆牌，自动结束本回合。"
        elif p.is_five_small:
            p.stood = True
            g.status_note = f"{p.name} 五小，自动停牌。"
        elif p.is_natural:
            p.stood = True
            g.status_note = f"{p.name} 十点半，自动停牌。"
        else:
            g.status_note = f"{p.name} 已要牌，当前 {_cards_brief(p.cards)}。"

        if p.is_done:
            g.current_player_idx += 1
            actions.extend(await self._ix_advance(cid, g, ctx))
        else:
            actions.append(
                await self._main_action(
                    ctx,
                    g,
                    self._build_ix_state_text(g),
                    reply_markup=_kb_turn(p.user_id, can_double=len(p.cards) == 2),
                )
            )
        return actions

    # ── 交互：停牌 ──────────────────────────────────
    async def _ix_stand(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        p = g.players[g.current_player_idx]
        p.stood = True
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=stand, value={_fv(p.value)}, chat_id={cid}")
        g.status_note = f"{p.name} 停牌，{_cards_brief(p.cards)}。"
        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, f"已停牌，{_cards_brief(p.cards)}。"))
        g.current_player_idx += 1
        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：加倍 ──────────────────────────────────
    async def _ix_double(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        p = g.players[g.current_player_idx]
        if len(p.cards) != 2:
            if payload is not None:
                return [_answer_action(payload, "加倍只能在前两张牌时使用。")]
            return [_send_action("⚠️ 加倍只能在前两张牌时使用。")]

        p.doubled = True
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=double, card={card.display()}, new_value={_fv(p.value)}, "
                f"bet_doubled={g.bet * 2}, busted={p.value > 10.5 + 1e-9}, chat_id={cid}")

        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, f"加倍要到 {card.display()}，当前 {_fv(p.value)}点。"))
        if p.value > 10.5 + 1e-9:
            p.busted = True
            g.status_note = f"{p.name} 加倍后爆牌，下注按 {g.bet * 2} 计算。"
        else:
            p.stood = True
            g.status_note = f"{p.name} 加倍后停牌，下注按 {g.bet * 2} 计算。"

        g.current_player_idx += 1
        actions.extend(await self._ix_advance(cid, g, ctx))
        return actions

    # ── 交互：庄家回合 ──────────────────────────────
    async def _ix_dealer_play(self, cid: int, g: TenHalfGame, ctx: PluginContext | None = None) -> list[dict[str, Any]]:
        g.phase = "dealer_turn"
        all_bust = all(p.busted for p in g.players)

        if ctx and ctx.log:
            await ctx.log("info",
                f"[ten_half] dealer_turn: dealer={g.dealer_name}, "
                f"all_bust={all_bust}, chat_id={cid}")

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
                if ctx and ctx.log:
                    await ctx.log("info",
                        f"[ten_half] dealer_draw: card={card.display()}, "
                        f"dealer_value={_fv(g.dealer_val())}, busted={g.dealer_busted()}, "
                        f"chat_id={cid}")
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

        actions.extend(await self._ix_settle(cid, g, ctx))
        return actions

    # ── 交互：结算 ──────────────────────────────────
    async def _ix_settle(self, cid: int, g: TenHalfGame, ctx: PluginContext | None = None) -> list[dict[str, Any]]:
        """结算：赢家获得总底注池 × 倍数 × 0.9（平台抽水10%），由 userbot 发放。"""
        g.phase = "finished"
        g.finished = True
        dv = g.dealer_val()
        db = g.dealer_busted()
        dn = g.dealer_natural()
        dfs = g.dealer_five_small()

        # 总底注池 = 所有玩家的有效下注（含加倍）
        total_pot = sum(g.bet * (2 if p.doubled else 1) for p in g.players)

        # ── 结算明细 ──
        lines = [
            f"🏆 <b>十点半结算 · 牌桌 <code>{g.game_id}</code></b>",
            f"💰 总底注池: <b>{total_pot}</b>",
            f"🎰 庄家 <b>{_html(g.dealer_name)}</b>: {_dealer_public_brief(g, reveal=True)}",
            "",
            "👥 玩家",
        ]

        player_results: list[dict[str, Any]] = []
        winners: list[dict[str, Any]] = []
        losers: list[dict[str, Any]] = []

        for p in g.players:
            eb = g.bet * (2 if p.doubled else 1)
            outcome = self._compare(p, dv, db, dn, dfs)

            # 倍数
            multiplier = (
                2.0 if outcome == "win_nat"
                else 1.5 if outcome == "win_5s"
                else 1.0 if outcome == "win"
                else 0.0
            )

            # 赢家获得 = 总底注池 × 倍数 × 0.9（抽水10%）
            reward = int(total_pot * multiplier * 0.9) if multiplier > 0 else 0
            loss = eb if outcome == "lose" else 0

            # 显示文案
            outcome_display = self._outcome_str(outcome, eb)
            if reward > 0:
                outcome_display += f" → 获得 <b>{reward}</b>"
            elif loss > 0:
                outcome_display += f" → 损失 {loss}"

            lines.append(f"• <b>{_html(p.name)}</b>: {_cards_brief(p.cards)} → {outcome_display}")

            pr = {
                "user_id": p.user_id,
                "name": p.name,
                "outcome": outcome,
                "multiplier": multiplier,
                "reward": reward,
                "loss": loss,
                "bet": eb,
            }
            player_results.append(pr)
            if reward > 0:
                winners.append(pr)
            elif loss > 0:
                losers.append(pr)

            if ctx and ctx.log:
                await ctx.log("info",
                    f"[ten_half] settlement: uid={p.user_id}, name={p.name}, "
                    f"outcome={outcome}, multiplier={multiplier}, reward={reward}, "
                    f"loss={loss}, bet={eb}, total_pot={total_pot}, chat_id={cid}")

        actions: list[dict[str, Any]] = []

        # ── 结算公告（走 interaction_bot，编辑主消息） ──
        if ctx is not None:
            actions.append(await self._main_action(ctx, g, "\n".join(lines)))
        else:
            actions.append(_send_action("\n".join(lines)))

        # ── 向每位赢家发放奖励（走 userbot_reply，参照 dice_grid_hunt） ──
        for w in winners:
            actions.append({
                "type": "send_message",
                "text": f"+{w['reward']}",
                "reply_to_message_id": self._player_reply_message(g, int(w["user_id"])),
                "send_via": "userbot_reply",
            })
            if ctx and ctx.log:
                await ctx.log("info",
                    f"[ten_half] reward_sent: uid={w['user_id']}, name={w['name']}, "
                    f"amount={w['reward']}, chat_id={cid}")

        # ── 平台结算元数据（参照 dice_grid_hunt / guess_number） ──
        if winners:
            primary = max(winners, key=lambda r: r["reward"])
            actions.append({
                "type": "result",
                "success": True,
                "result": {
                    "status": "finished",
                    "dealer_name": g.dealer_name,
                    "dealer_value": dv,
                    "total_pot": total_pot,
                    "winner_user_id": primary["user_id"],
                    "winner_name": primary["name"],
                    "winner_count": len(winners),
                    "players": player_results,
                    "payout_mode": "auto",
                },
                "settlement": {
                    "mode": "announce_only",
                    "amount": primary["reward"],
                    "winner_user_id": primary["user_id"],
                    "winner_name": primary["name"],
                    "payout_account_label": "管理员",
                    "status": "announced",
                },
            })
        else:
            # 所有玩家都输了（庄家通吃）
            actions.append({
                "type": "result",
                "success": True,
                "result": {
                    "status": "dealer_wins",
                    "dealer_name": g.dealer_name,
                    "dealer_value": dv,
                    "total_pot": total_pot,
                    "players": player_results,
                },
            })

        actions.append({"type": "end_session"})
        self._games.pop(cid, None)
        return actions

PLUGIN_CLASS = TenHalfPlugin

__all__ = ["TenHalfPlugin", "PLUGIN_CLASS"]
