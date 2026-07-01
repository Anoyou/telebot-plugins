"""十点半纸牌游戏插件。

经典十点半纸牌游戏：支持多人对战、加倍、五小等规则。
A=1, 2-9=面值, 10/J/Q/K=0.5点。目标 10.5 点。
五小(5张不爆)和天生十点半(前两张=10.5)双倍赔付。
"""

from __future__ import annotations

import asyncio
import random
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from app.worker.command import current_command_prefix
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


def _receiver_label_from_entity(entity: Any, *, fallback: str = "") -> str:
    username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    label = public_entity_display_name(entity, fallback_id="", default="")
    return label or fallback


# ─────────────────────────────────────────────────────
# 牌组
# ─────────────────────────────────────────────────────
SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
REDIS_MAIN_MSG_KEY_PREFIX = "ten_half:main:"
REDIS_JOIN_NOTICE_KEY_PREFIX = "ten_half:join_notice:"
REDIS_SETTLEMENT_MSG_KEY_PREFIX = "ten_half:settlement:"
REDIS_REWARD_MSG_KEY_PREFIX = "ten_half:reward:"


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


def _settlement_msg_key(account_id: int, chat_id: int, game_id: str) -> str:
    return f"{REDIS_SETTLEMENT_MSG_KEY_PREFIX}{account_id}:{chat_id}:{game_id}"


def _reward_msg_key(account_id: int, chat_id: int, game_id: str, user_id: int) -> str:
    return f"{REDIS_REWARD_MSG_KEY_PREFIX}{account_id}:{chat_id}:{game_id}:{user_id}"


def _normalize_command_name(raw: Any) -> str:
    text = str(raw or "").strip()
    prefixes = [
        current_command_prefix(fallback=","),
        ",",
        ".",
        "。",
        "/",
        "!",
    ]
    changed = True
    while changed and text:
        changed = False
        for prefix in prefixes:
            if prefix and text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
                break
    return text or "10d"


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
        return (
            self.busted
            or self.stood
            or self.is_natural
            or self.is_five_small
            or self.value > 10.5 + 1e-9
        )

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
    max_players: int = 5
    turn_timeout: int = 8
    lobby_timeout: int = 60
    idle_start_seconds: int = 15
    game_id: str = field(default_factory=lambda: secrets.token_hex(3).upper())
    # lobby -> playing -> dealer_turn -> finished
    phase: str = "lobby"
    dealer_id: int = 0          # 0 = bot 庄家
    dealer_name: str = "🤖 庄家"
    dealer_locked: bool = False
    dealer_stood: bool = False
    host_user_id: int = 0
    host_name: str = ""
    dealer_cards: list[Card] = field(default_factory=list)
    deck: list[Card] = field(default_factory=list)
    players: list[PlayerHand] = field(default_factory=list)
    lobby_players: list[tuple[int, str]] = field(default_factory=list)
    turn_order: list[int] = field(default_factory=list)
    started_at: float = 0.0
    via_interaction: bool = False
    finished: bool = False
    lobby_msg_id: int | None = None
    main_message_id: int | None = None
    join_notice_msg_id: int | None = None
    known_interaction_message_ids: set[int] = field(default_factory=set)
    opening_message_deleted: bool = False
    game_message_started: bool = False
    payment_receiver_name: str = ""
    status_note: str = ""
    awaiting_start_confirmation: bool = False
    lobby_version: int = 0
    action_version: int = 0
    action_versions: dict[int, int] = field(default_factory=dict)
    timeout_versions: dict[int, int] = field(default_factory=dict)
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

    def dealer_done(self) -> bool:
        return (
            self.dealer_busted()
            or self.dealer_stood
            or self.dealer_natural()
            or self.dealer_five_small()
        )

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
    rt = _reply_to(p)
    actor = _pa(p)
    player = _pp(p)
    payment = _pay(p)
    payer = payment.get("payer") if isinstance(payment.get("payer"), dict) else {}
    data = e.get("data") if isinstance(e.get("data"), dict) else {}
    is_payment = _ie_type(p) == "payment_confirmed"
    if is_payment:
        raw_id = (
            payment.get("payer_user_id") or payer.get("user_id")
            or p.get("payer_user_id") or data.get("payer_user_id")
            or rt.get("user_id") or player.get("user_id") or actor.get("user_id")
            or e.get("payer_user_id") or p.get("sender_user_id")
        )
        raw_name = (
            payment.get("payer_display_name") or payment.get("payer_name")
            or payer.get("display_name") or payer.get("name")
            or p.get("payer_name") or data.get("payer_name")
            or rt.get("display_name") or player.get("display_name") or actor.get("display_name")
            or player.get("name") or actor.get("name")
            or e.get("payer_name") or p.get("sender_name") or "玩家"
        )
        return _pint(raw_id, 0, minimum=0), str(raw_name).strip() or "玩家"
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


def _module_config(p: dict[str, Any]) -> dict[str, Any]:
    cfg = p.get("module_config")
    return dict(cfg) if isinstance(cfg, dict) else {}


def _trigger_payload(p: dict[str, Any]) -> dict[str, Any]:
    trigger = p.get("trigger")
    if not isinstance(trigger, dict):
        return {}
    payload = trigger.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _config_int(
    ctx: PluginContext | None,
    payload: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    sources = [payload, _module_config(payload), _trigger_payload(payload)]
    if ctx is not None and isinstance(ctx.config, dict):
        sources.append(ctx.config)
    for source in sources:
        value = source.get(key) if isinstance(source, dict) else None
        parsed = _pint(value, 0, minimum=minimum)
        if parsed > 0:
            return max(minimum, min(maximum, parsed))
    return max(minimum, min(maximum, int(default)))


def _interaction_bet_from_payload(payload: dict[str, Any]) -> int:
    module_config = _module_config(payload)
    trigger_payload = _trigger_payload(payload)

    def first_positive(source: dict[str, Any], keys: tuple[str, ...]) -> int:
        for key in keys:
            parsed = _pint(source.get(key), 0, minimum=1)
            if parsed > 0:
                return parsed
        return 0

    rule_keys = ("module_prize", "math_prize")
    amount_keys = (
        "entry_fee",
        "entry_amount",
        "threshold_amount",
        "payment_threshold",
        "default_bet",
        "bet_amount",
        "stake",
        "bet",
        "amount",
        "prize",
    )
    payload_amount_keys = tuple(key for key in amount_keys if key != "prize")

    for source, keys in (
        (payload, rule_keys),
        (module_config, amount_keys),
        (trigger_payload, amount_keys),
        (payload, payload_amount_keys),
    ):
        parsed = first_positive(source, keys)
        if parsed > 0:
            return parsed
    return 0


def _start_keyword_label(payload: dict[str, Any], fallback: str) -> str:
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    trigger_payload = _trigger_payload(payload)
    module_config = _module_config(payload)
    event_type = str(payload.get("event_type") or trigger.get("type") or "").strip()
    for values in (trigger.get("start_keywords"), module_config.get("start_keywords")):
        if isinstance(values, list):
            for value in values:
                text = str(value or "").strip()
                if text:
                    return text
    values = [
        trigger.get("keyword"),
        trigger_payload.get("keyword"),
        payload.get("keyword"),
        module_config.get("keyword"),
        module_config.get("start_keyword"),
    ]
    if event_type != "payment_confirmed":
        values.extend([
            trigger.get("text"),
            trigger_payload.get("text"),
            payload.get("message_text"),
        ])
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return str(fallback or "").strip() or "玩法启动关键词"


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


def _kb_start_decision(uid: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "▶️ 直接开局", "callback_data": f"th:start_now:{uid}"},
            {"text": "⏳ 继续等待", "callback_data": f"th:wait_more:{uid}"},
        ]]
    }


def _short_button_name(name: str, *, limit: int = 8) -> str:
    text = str(name or "玩家").strip() or "玩家"
    return text if len(text) <= limit else text[:limit] + "…"


def _target_action_version(g: TenHalfGame, uid: int) -> int:
    uid = int(uid)
    if uid not in g.action_versions:
        g.action_versions[uid] = max(1, int(g.action_version or 0))
    return g.action_versions[uid]


def _bump_target_action_version(g: TenHalfGame, uid: int) -> None:
    uid = int(uid)
    current = _target_action_version(g, uid)
    g.action_versions[uid] = current + 1
    g.action_version = max(g.action_version + 1, g.action_versions[uid])


def _kb_action_row(uid: int, name: str, *, can_double: bool, action_version: int, dealer: bool = False) -> list[dict[str, str]]:
    suffix = f":{action_version}"
    who = _short_button_name(name)
    row = [
        {"text": f"👀 {who}", "callback_data": f"th:view:{uid}{suffix}"},
        {"text": f"🃏 {who}", "callback_data": f"th:hit:{uid}{suffix}"},
        {"text": f"🛑 {who}", "callback_data": f"th:stand:{uid}{suffix}"},
    ]
    if can_double and not dealer:
        row.append({"text": f"💰 {who}", "callback_data": f"th:double:{uid}{suffix}"})
    return row


def _kb_parallel_actions(g: TenHalfGame) -> dict[str, Any] | None:
    rows: list[list[dict[str, str]]] = []
    for p in g.players:
        if p.is_done:
            continue
        rows.append(
            _kb_action_row(
                p.user_id,
                p.name,
                can_double=len(p.cards) == 2,
                action_version=_target_action_version(g, p.user_id),
            )
        )
    if g.dealer_id > 0 and not g.dealer_done():
        rows.append(
            _kb_action_row(
                g.dealer_id,
                f"庄 {g.dealer_name}",
                can_double=False,
                action_version=_target_action_version(g, g.dealer_id),
                dealer=True,
            )
        )
    return {"inline_keyboard": rows} if rows else None


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
    else:
        tags.append("等待操作")
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
        elif g.dealer_stood:
            tags.append("已停牌")
        elif g.phase == "playing" and g.dealer_id > 0:
            tags.append("等待操作")
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


def _delete_action(
    message_id: int,
    *,
    chat_id: int | None = None,
    send_via: str = "interaction_bot",
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "delete_message",
        "message_id": message_id,
        "send_via": send_via,
    }
    if chat_id is not None:
        action["chat_id"] = chat_id
    return action


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
        self._turn_timeout = 8
        self._lobby_timeout = 60
        self._settlement_cleanup_delay = 15
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
    def _remember_interaction_message(g: TenHalfGame, mid: int | None) -> None:
        if mid:
            g.known_interaction_message_ids.add(int(mid))

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
        force_send: bool = False,
    ) -> dict[str, Any] | None:
        key = _main_msg_key(ctx.account_id, g.chat_id)
        if (
            not force_send
            and not g.main_message_id
            and g.opening_message_deleted
            and g.phase in {"playing", "dealer_turn"}
            and not g.game_message_started
        ):
            force_send = True
        mid = None if force_send else (g.main_message_id or await self._read_saved_message_id(ctx, key))
        if mid:
            g.main_message_id = mid
            self._remember_interaction_message(g, mid)
            if g.phase in {"playing", "dealer_turn"}:
                g.game_message_started = True
            action = _edit_action(mid, text, reply_markup=reply_markup)
        elif not send_if_missing:
            return None
        else:
            action = _send_action(
                text,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
                save_message_id_key=key,
                replace_saved_message_id_key=key if force_send else None,
            )
            if g.phase in {"playing", "dealer_turn"}:
                g.game_message_started = True
        action.setdefault("chat_id", g.chat_id)
        return action

    async def _delete_current_join_notice_actions(
        self,
        ctx: PluginContext,
        g: TenHalfGame,
    ) -> list[dict[str, Any]]:
        key = _join_notice_key(ctx.account_id, g.chat_id)
        saved_mid = await self._read_saved_message_id(ctx, key)
        mid = saved_mid or g.join_notice_msg_id
        if not mid:
            return []
        g.join_notice_msg_id = mid
        self._remember_interaction_message(g, mid)
        return [_delete_action(mid)]

    async def _join_notice_update_action(
        self,
        ctx: PluginContext,
        g: TenHalfGame,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = _join_notice_key(ctx.account_id, g.chat_id)
        saved_mid = await self._read_saved_message_id(ctx, key)
        mid = saved_mid or g.join_notice_msg_id
        if not mid:
            return None
        g.join_notice_msg_id = mid
        self._remember_interaction_message(g, mid)
        action = _edit_action(mid, text, reply_markup=reply_markup)
        action.setdefault("chat_id", g.chat_id)
        return action

    async def _emit_background_actions(
        self,
        ctx: PluginContext,
        actions: list[dict[str, Any] | None],
    ) -> bool:
        clean = [action for action in actions if isinstance(action, dict)]
        if not clean:
            return False
        messages = getattr(ctx, "messages", None)
        apply = getattr(messages, "apply", None)
        if not callable(apply):
            if ctx.log:
                await ctx.log("warn", "[ten_half] background_actions_unavailable: ctx.messages.apply missing")
            return False
        try:
            await apply(clean, entry_key="start_ten_half")
            return True
        except Exception as exc:  # noqa: BLE001
            if ctx.log:
                await ctx.log("warn", f"[ten_half] background_actions_failed: {exc}")
            return False

    async def _emit_background_actions_batched(
        self,
        ctx: PluginContext,
        actions: list[dict[str, Any]],
        *,
        batch_size: int = 10,
    ) -> bool:
        delivered = False
        for index in range(0, len(actions), batch_size):
            delivered = await self._emit_background_actions(ctx, actions[index:index + batch_size]) or delivered
        return delivered

    def _schedule_settlement_cleanup(
        self,
        ctx: PluginContext,
        g: TenHalfGame,
        reward_message_keys: list[str],
        settlement_message_key: str | None = None,
    ) -> None:
        self._track_task(asyncio.create_task(
            self._cleanup_game_messages_task(
                ctx,
                g.chat_id,
                g.main_message_id,
                g.join_notice_msg_id,
                set(g.known_interaction_message_ids),
                settlement_message_key,
                list(reward_message_keys),
                self._settlement_cleanup_delay,
            )
        ))

    async def _cleanup_game_messages_task(
        self,
        ctx: PluginContext,
        cid: int,
        main_message_id: int | None,
        join_notice_msg_id: int | None,
        known_interaction_message_ids: set[int] | None,
        settlement_message_key: str | None,
        reward_message_keys: list[str],
        delay_seconds: int,
    ) -> None:
        await asyncio.sleep(max(0, int(delay_seconds)))
        interaction_message_ids: set[int] = set(known_interaction_message_ids or set())
        userbot_message_ids: set[int] = set()

        if main_message_id:
            interaction_message_ids.add(main_message_id)
        saved_main_mid = await self._read_saved_message_id(ctx, _main_msg_key(ctx.account_id, cid))
        if saved_main_mid:
            interaction_message_ids.add(saved_main_mid)
        if join_notice_msg_id:
            interaction_message_ids.add(join_notice_msg_id)
        saved_join_mid = await self._read_saved_message_id(ctx, _join_notice_key(ctx.account_id, cid))
        if saved_join_mid:
            interaction_message_ids.add(saved_join_mid)
        if settlement_message_key:
            settlement_mid = await self._read_saved_message_id(ctx, settlement_message_key)
            if settlement_mid:
                interaction_message_ids.add(settlement_mid)
        for key in reward_message_keys:
            reward_mid = await self._read_saved_message_id(ctx, key)
            if reward_mid:
                userbot_message_ids.add(reward_mid)

        actions = [
            _delete_action(mid, chat_id=cid, send_via="interaction_bot")
            for mid in sorted(interaction_message_ids)
        ]
        actions.extend(
            _delete_action(mid, chat_id=cid, send_via="userbot_reply")
            for mid in sorted(userbot_message_ids)
        )
        if not actions:
            if ctx.log:
                await ctx.log("info", f"[ten_half] settlement_cleanup_skip: no_messages, chat_id={cid}")
            return
        delivered = await self._emit_background_actions_batched(ctx, actions)
        if ctx.log:
            level = "info" if delivered else "warn"
            await ctx.log(
                level,
                f"[ten_half] settlement_cleanup: messages={len(actions)}, "
                f"delay={delay_seconds}, delivered={delivered}, chat_id={cid}",
            )

    @staticmethod
    def _start_controller_uid(g: TenHalfGame) -> int:
        if g.dealer_id > 0:
            return g.dealer_id
        return g.host_user_id or (g.lobby_players[0][0] if g.lobby_players else 0)

    def _touch_lobby(self, g: TenHalfGame) -> None:
        g.lobby_version += 1
        g.awaiting_start_confirmation = False

    def _lock_dealer(self, g: TenHalfGame, uid: int, name: str, status_note: str) -> None:
        if g.dealer_locked:
            return
        g.dealer_id = uid
        g.dealer_name = name
        g.dealer_locked = True
        g.status_note = status_note

    def _lock_first_dealer(self, g: TenHalfGame, uid: int, name: str) -> None:
        self._lock_dealer(
            g,
            uid,
            name,
            f"{name} 作为首位加入玩家，已自动成为本局庄家。",
        )

    def _lock_command_dealer(self, g: TenHalfGame, uid: int, name: str) -> None:
        self._lock_dealer(
            g,
            uid,
            name,
            f"{name} 作为开桌者，已直接成为本局庄家。",
        )

    @staticmethod
    def _normalize_player_state(p: PlayerHand) -> None:
        if p.value > 10.5 + 1e-9:
            p.busted = True
        elif p.is_natural or p.is_five_small:
            p.stood = True

    @staticmethod
    def _normalize_dealer_state(g: TenHalfGame) -> None:
        if g.dealer_natural() or g.dealer_five_small():
            g.dealer_stood = True

    def _normalize_parallel_state(self, g: TenHalfGame) -> None:
        for p in g.players:
            self._normalize_player_state(p)
        self._normalize_dealer_state(g)

    @staticmethod
    def _find_player(g: TenHalfGame, uid: int) -> PlayerHand | None:
        for p in g.players:
            if p.user_id == uid:
                return p
        return None

    @staticmethod
    def _all_players_done(g: TenHalfGame) -> bool:
        return all(p.is_done for p in g.players)

    def _parallel_round_done(self, g: TenHalfGame) -> bool:
        if not self._all_players_done(g):
            return False
        return g.dealer_is_bot or g.dealer_done()

    def _active_target_ids(self, g: TenHalfGame) -> list[int]:
        ids = [p.user_id for p in g.players if not p.is_done]
        if g.dealer_id > 0 and not g.dealer_done():
            ids.append(g.dealer_id)
        return ids

    def _schedule_target_timeout(self, cid: int, g: TenHalfGame, uid: int, ctx: PluginContext) -> None:
        uid = int(uid)
        g.timeout_versions[uid] = int(g.timeout_versions.get(uid, 0)) + 1
        self._track_task(asyncio.create_task(
            self._target_timeout_task(cid, uid, g.started_at, g.timeout_versions[uid], ctx),
        ))

    def _schedule_all_active_timeouts(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        for uid in self._active_target_ids(g):
            self._schedule_target_timeout(cid, g, uid, ctx)

    async def _target_timeout_task(
        self,
        cid: int,
        uid: int,
        sa: float,
        version: int,
        ctx: PluginContext,
    ) -> None:
        g0 = self._games.get(cid)
        await asyncio.sleep(g0.turn_timeout if g0 and g0.started_at == sa else self._turn_timeout)
        actions: list[dict[str, Any] | None] = []
        async with self._lock(cid):
            g = self._games.get(cid)
            if (
                not g
                or g.phase != "playing"
                or g.finished
                or g.started_at != sa
                or int(g.timeout_versions.get(uid, 0)) != version
            ):
                return
            if uid == g.dealer_id and g.dealer_id > 0:
                if g.dealer_done():
                    return
                g.dealer_stood = True
                _bump_target_action_version(g, uid)
                g.status_note = f"{g.dealer_name} 超时，自动停牌。"
                if ctx.log:
                    await ctx.log(
                        "info",
                        f"[ten_half] target_timeout: uid={uid}, name={g.dealer_name}, "
                        f"role=dealer, auto_stand=True, chat_id={cid}",
                    )
            else:
                p = self._find_player(g, uid)
                if p is None or p.is_done:
                    return
                p.stood = True
                _bump_target_action_version(g, uid)
                g.status_note = f"{p.name} 超时，自动停牌。"
                if ctx.log:
                    await ctx.log(
                        "info",
                        f"[ten_half] target_timeout: uid={uid}, name={p.name}, "
                        f"role=player, auto_stand=True, chat_id={cid}",
                    )
            actions.extend(await self._ix_refresh_or_settle(cid, g, ctx))
        if actions:
            delivered = await self._emit_background_actions(ctx, actions)
            if ctx.log and not delivered:
                await ctx.log("warn", f"[ten_half] target_timeout_actions_not_delivered: chat_id={cid}")

    def _schedule_idle_start_prompt(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        if (
            g.phase != "lobby"
            or g.finished
            or not g.via_interaction
            or not g.dealer_locked
            or len(g.lobby_players) < 2
            or len(g.lobby_players) >= g.max_players
        ):
            return
        version = g.lobby_version
        self._track(asyncio.create_task(
            self._idle_start_prompt_task(cid, g.started_at, version, ctx),
        ))

    async def _idle_start_prompt_task(
        self,
        cid: int,
        sa: float,
        version: int,
        ctx: PluginContext,
    ) -> None:
        g0 = self._games.get(cid)
        delay = g0.idle_start_seconds if g0 and g0.started_at == sa else 15
        await asyncio.sleep(delay)
        async with self._lock(cid):
            g = self._games.get(cid)
            if (
                not g
                or g.phase != "lobby"
                or g.finished
                or g.started_at != sa
                or g.lobby_version != version
                or not g.dealer_locked
                or len(g.lobby_players) < 2
                or len(g.lobby_players) >= g.max_players
            ):
                return
            g.awaiting_start_confirmation = True
            controller_uid = self._start_controller_uid(g)
            g.status_note = f"{g.idle_start_seconds} 秒内没有新玩家加入，{g.dealer_name} 可以选择直接开局或继续等待。"
            if ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] idle_start_prompt: controller={controller_uid}, "
                    f"players={len(g.lobby_players)}/{g.max_players}, chat_id={cid}",
                )
            action = await self._join_notice_update_action(
                ctx,
                g,
                self._build_join_notice_text(
                    g,
                    payer_name=g.lobby_players[-1][1] if g.lobby_players else g.dealer_name,
                    amount=g.bet,
                ),
                reply_markup=_kb_start_decision(controller_uid),
            )
        delivered = await self._emit_background_actions(ctx, [action])
        if ctx.log and not delivered:
            await ctx.log("warn", f"[ten_half] idle_start_prompt_not_delivered: chat_id={cid}")

    def _game_limits_from_payload(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, int]:
        return {
            "max_players": _config_int(ctx, payload, "max_players", self._max_players, minimum=2, maximum=10),
            "turn_timeout": _config_int(ctx, payload, "timeout", self._turn_timeout, minimum=5, maximum=120),
            "lobby_timeout": _config_int(ctx, payload, "lobby_timeout", self._lobby_timeout, minimum=10, maximum=300),
        }

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
        join_key = _join_notice_key(ctx.account_id, g.chat_id)
        main_key = _main_msg_key(ctx.account_id, g.chat_id)
        saved_join_mid = await self._read_saved_message_id(ctx, join_key)
        previous_mid = saved_join_mid or g.join_notice_msg_id
        opening_mid = None
        if not g.opening_message_deleted:
            opening_mid = g.main_message_id or await self._read_saved_message_id(ctx, main_key)
        actions.append(
            _send_action(
                self._build_join_notice_text(g, payer_name=payer_name, amount=amount),
                reply_to_message_id=_ie_mid(payload),
                save_message_id_key=join_key,
            )
        )
        g.join_notice_msg_id = None
        if previous_mid:
            self._remember_interaction_message(g, previous_mid)
            actions.append(_delete_action(previous_mid))
        if opening_mid and opening_mid != previous_mid:
            self._remember_interaction_message(g, opening_mid)
            g.main_message_id = None
            g.opening_message_deleted = True
            actions.append(_delete_action(opening_mid))
        elif opening_mid:
            g.opening_message_deleted = True
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
            f"⏰ 等待玩家加入中... ({g.lobby_timeout}秒)，当前牌桌 ID 为 <code>{g.game_id}</code>"
        )
        if g.lobby_players:
            players = "、".join(_html(name) for _, name in g.lobby_players)
            lines.extend([
                "",
                f"👥 已加入 ({len(g.lobby_players)}/{g.max_players}): {players}",
            ])
        if g.dealer_locked:
            lines.extend(["", f"🎰 庄家: <b>{_html(g.dealer_name)}</b>"])
            if 2 <= len(g.lobby_players) < g.max_players:
                lines.append(
                    f"🕒 {g.idle_start_seconds} 秒无人加入可由庄家提前开局，"
                    f"{g.lobby_timeout} 秒后自动开局。"
                )
        if g.finished and g.status_note:
            lines.extend(["", _html(g.status_note)])
        return "\n".join(lines)

    def _build_join_notice_text(self, g: TenHalfGame, *, payer_name: str, amount: int) -> str:
        players = [f"• {_html(name)}" for _, name in g.lobby_players] or ["• 暂无"]
        lines = [
            f"✅ <b>{_html(payer_name)}</b> 加入牌局成功",
            f"🆔 牌桌 ID: <code>{g.game_id}</code>",
            f"💰 入场金额: {amount}",
            f"👥 当前玩家 ({len(g.lobby_players)}/{g.max_players}):",
            *players,
        ]
        if g.dealer_locked and 2 <= len(g.lobby_players) < g.max_players:
            lines.extend([
                "",
                f"⏳ 开始倒计时 {g.idle_start_seconds} 秒，如果没人加入则庄家可以选择直接开局。",
            ])
        if g.awaiting_start_confirmation and g.status_note:
            lines.extend(["", _html(g.status_note)])
        return "\n".join(lines)

    def _build_ix_state_text(self, g: TenHalfGame, *, reveal_dealer: bool = False) -> str:
        phase_text = {
            "dealer_turn": "庄家行动",
            "playing": "自由行动",
            "finished": "已结算",
        }.get(g.phase, "进行中")
        lines = [
            f"🃏 <b>十点半 · 牌桌 <code>{g.game_id}</code></b>",
            f"💰 底注: <b>{g.bet}</b> · {phase_text}",
            "",
            f"🎰 庄家 <b>{_html(g.dealer_name)}</b>: {_dealer_public_brief(g, reveal=reveal_dealer)}",
        ]
        if g.phase == "playing" and not g.finished:
            active_names = [p.name for p in g.players if not p.is_done]
            if g.dealer_id > 0 and not g.dealer_done():
                active_names.append(g.dealer_name)
            if active_names:
                lines.append("⚡ 所有人可同时操作自己的按钮；全部停牌/爆牌后统一结算。")
                lines.append("⏳ 等待：" + "、".join(_html(name) for name in active_names))
        if g.phase == "dealer_turn" and not g.dealer_is_bot and not g.finished:
            lines.append("👉 所有玩家已行动，庄家请要牌或停牌。")
        if g.players:
            lines.extend(["", "👥 玩家"])
            for p in g.players:
                status = _player_status(p)
                suffix = f" · {status}" if status else ""
                marker = "👉" if not p.is_done else "•"
                lines.append(f"{marker} <b>{_html(p.name)}</b>: {_cards_brief(p.cards)}{suffix}")
        if g.status_note:
            lines.extend(["", _html(g.status_note)])
        return "\n".join(lines)

    # ── 生命周期 ─────────────────────────────────────
    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = _normalize_command_name(cfg.get("command", "10d"))
        self._turn_timeout = _pint(cfg.get("timeout"), 8, minimum=5)
        self._lobby_timeout = _pint(cfg.get("lobby_timeout"), 60, minimum=10)
        self._max_players = _pint(cfg.get("max_players"), 5, minimum=2)
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
    # 命令入口（userbot 开桌，后续仍走交互 Bot 按钮）
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
                prefix = current_command_prefix(fallback=",")
                await event.reply(
                    f"请指定下注金额，例如：{prefix}{self._command} 100",
                    parse_mode="html",
                )
                return

            host_id = int(getattr(event, "sender_id", 0) or 0)
            host_name = "管理员"
            receiver_name = ""
            try:
                me = await client.get_me()
                host_id = int(getattr(me, "id", host_id) or host_id)
                host_name = public_entity_display_name(me, fallback_id=host_id, default="管理员")
                receiver_name = _receiver_label_from_entity(me, fallback=host_name)
            except Exception:
                pass

            g = TenHalfGame(
                chat_id=cid,
                bet=bet,
                max_players=max(2, self._max_players),
                turn_timeout=self._turn_timeout,
                lobby_timeout=self._lobby_timeout,
                phase="lobby", started_at=time.monotonic(),
                via_interaction=True,
                host_user_id=host_id,
                host_name=host_name,
            )
            g.payment_receiver_name = receiver_name or host_name or self._receiver_label(ctx, None, g)
            if host_id:
                g.lobby_players.append((host_id, host_name))
                self._lock_command_dealer(g, host_id, host_name)
            else:
                g.status_note = "首位成功加入的玩家将自动成为本局庄家。"
            self._games[cid] = g

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] game_start: chat_id={cid}, bet={bet}, "
                f"lobby_timeout={g.lobby_timeout}, via_interaction=True, "
                f"dealer={'command_host' if g.dealer_locked else 'first_player'}, "
                f"players={len(g.lobby_players)}/{g.max_players}")

        action = await self._main_action(
            ctx,
            g,
            self._build_lobby_text(g, g.payment_receiver_name or g.dealer_name),
            reply_to_message_id=int(getattr(event, "id", 0) or 0) or None,
            force_send=True,
        )
        session_action = {
            "type": "start_session",
            "chat_id": cid,
            "entry_key": "start_ten_half",
            "event_type": "command",
            "started_by_user_id": host_id,
            "started_by_message_id": int(getattr(event, "id", 0) or 0) or None,
            "participant_policy": "paid_pool",
        }
        if host_id:
            session_action["paid_user_ids"] = [host_id]
            session_action["participant_user_ids"] = [host_id]
        await self._emit_background_actions(ctx, [
            session_action,
            action,
        ])
        self._track(asyncio.create_task(
            self._lobby_timeout_task(cid, g.started_at, ctx),
        ))

    # ═══════════════════════════════════════════════════
    # 大厅
    # ═══════════════════════════════════════════════════
    async def _lobby_timeout_task(self, cid: int, sa: float, ctx: PluginContext) -> None:
        g0 = self._games.get(cid)
        await asyncio.sleep(g0.lobby_timeout if g0 and g0.started_at == sa else self._lobby_timeout)
        actions: list[dict[str, Any] | None] = []
        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.phase != "lobby" or g.finished or g.started_at != sa:
                return
            if not g.lobby_players:
                g.finished = True
                self._games.pop(cid, None)
                g.status_note = "没人加入，牌局已取消。"
                actions.append(await self._main_action(
                    ctx,
                    g,
                    self._build_lobby_text(g, self._receiver_label(ctx, None, g)),
                    reply_markup=None,
                ))
                actions.append({"type": "end_session"})
                if ctx.log:
                    await ctx.log("info", f"[ten_half] lobby_timeout_cancel: players=0, chat_id={cid}")
            else:
                if len(g.lobby_players) < 2:
                    g.finished = True
                    self._games.pop(cid, None)
                    g.status_note = "大厅等待已结束，参与人数不足 2 人，牌局已取消。"
                    actions.append(await self._main_action(
                        ctx,
                        g,
                        self._build_lobby_text(g, self._receiver_label(ctx, None, g)),
                        reply_markup=None,
                    ))
                    actions.append({"type": "end_session"})
                    if ctx.log:
                        await ctx.log("info", f"[ten_half] lobby_timeout_cancel: players={len(g.lobby_players)}, chat_id={cid}")
                elif g.dealer_locked:
                    if ctx.log:
                        await ctx.log(
                            "info",
                            f"[ten_half] lobby_timeout_begin: dealer={g.dealer_name}, "
                            f"players={len(g.lobby_players)}/{g.max_players}, chat_id={cid}",
                        )
                    actions.extend(await self._ix_begin(cid, g, g.dealer_id, g.dealer_name, ctx))
                else:
                    first_id, first_name = g.lobby_players[0]
                    self._lock_first_dealer(g, first_id, first_name)
                    if ctx.log:
                        await ctx.log(
                            "info",
                            f"[ten_half] lobby_timeout_begin: dealer={g.dealer_name}, "
                            f"players={len(g.lobby_players)}/{g.max_players}, chat_id={cid}",
                        )
                    actions.extend(await self._ix_begin(cid, g, g.dealer_id, g.dealer_name, ctx))
        if actions:
            delivered = await self._emit_background_actions(ctx, actions)
            if ctx.log and not delivered:
                await ctx.log("warn", f"[ten_half] lobby_timeout_actions_not_delivered: chat_id={cid}")

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

        if p.busted or p.value > 10.5 + 1e-9:
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
    def _settlement_outcome_text(
        p: PlayerHand,
        outcome: str,
        bet: int,
        reward: int,
        loss: int,
        *,
        html_mode: bool = False,
    ) -> str:
        if p.busted or p.value > 10.5 + 1e-9:
            return f"❌ 爆牌！输 {loss or bet}"
        if reward > 0:
            amount = f"<b>{reward}</b>" if html_mode else str(reward)
            prefix = ""
            if outcome == "win_nat":
                prefix = "✨ 天生十点半！"
            elif outcome == "win_5s":
                prefix = "🌟 五小！"
            return f"{prefix}🎉是赢家 获得 {amount}"
        if outcome == "push":
            return "🤝 平局 0"
        return f"❌ 输 {loss or bet}"

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
        limits = self._game_limits_from_payload(ctx, payload)
        bet = _interaction_bet_from_payload(payload)



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
            host_id, host_name = _ie_actor(payload)
            g = TenHalfGame(
                chat_id=cid, bet=bet,
                max_players=limits["max_players"],
                turn_timeout=limits["turn_timeout"],
                lobby_timeout=limits["lobby_timeout"],
                phase="lobby", started_at=time.monotonic(),
                via_interaction=True,
                host_user_id=host_id,
                host_name=host_name,
            )
            g.payment_receiver_name = self._receiver_label(ctx, payload, g)
            self._games[cid] = g

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] game_start: chat_id={cid}, bet={bet}, "
                f"max_players={g.max_players}, lobby_timeout={g.lobby_timeout}, via_interaction=True")

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
                force_send=True,
            )
        ]


    # ── 交互：转账加入 ────────────────────────────────
    async def _ix_payment_join(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        """payment_confirmed: 玩家转账给管理员(userbot)。

        付款订阅是群级别的，必须先用插件内活跃牌桌和底注做过滤；
        不属于本桌的转账静默跳过，避免干扰同群其它玩法或普通转账。
        """
        payer_id, payer_name = _ie_payer(payload)
        amount = _ie_payment_amount(payload)
        mid = _ie_mid(payload)
        payment_status = _ie_payment_status(payload)

        async def _skip(reason: str, game: TenHalfGame | None = None) -> list[dict[str, Any]]:
            if ctx.log:
                await ctx.log(
                    "debug",
                    "[ten_half] payment_skip: "
                    f"reason={reason}, payer={payer_id} ({payer_name}), amount={amount}, "
                    f"bet={getattr(game, 'bet', None)}, phase={getattr(game, 'phase', None)}, "
                    f"via_interaction={getattr(game, 'via_interaction', None)}, "
                    f"players={len(getattr(game, 'lobby_players', []) or [])}, chat_id={cid}",
                )
            return [{"type": "no_session"}]

        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.finished:
                return await _skip("no_lobby", g)
            if not g.via_interaction:
                return await _skip("not_interaction_lobby", g)
            if not g.payment_receiver_name:
                g.payment_receiver_name = self._receiver_label(ctx, payload, g)

            if g.phase != "lobby":
                return await _skip("phase_not_lobby", g)

            if amount != g.bet:
                return await _skip("amount_mismatch", g)

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

            if len(g.lobby_players) >= g.max_players:
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
            if len(g.lobby_players) == 1:
                self._lock_first_dealer(g, payer_id, payer_name)
            self._touch_lobby(g)
            cnt = len(g.lobby_players)

            if ctx.log:
                await ctx.log("info",
                    f"[ten_half] player_joined: uid={payer_id}, name={payer_name}, "
                    f"via=payment, amount={amount}, count={cnt}/{g.max_players}, chat_id={cid}")

            actions = await self._join_notice_actions(
                ctx,
                payload,
                g,
                payer_name=payer_name,
                amount=amount,
            )

            if cnt >= g.max_players and g.dealer_locked:
                actions.extend(await self._ix_begin(cid, g, g.dealer_id, g.dealer_name, ctx))
                return actions

            if g.dealer_locked:
                self._schedule_idle_start_prompt(cid, g, ctx)
            return actions

    # ── 交互：callback_query 处理 ────────────────────
    async def _ix_callback(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        """Handle callback_query events from inline keyboard buttons.

        Callback data format: th:<action>:<id>
        Actions: join, hit, stand, double; dealer_yes/dealer_no are stale-button compatibility only.
        """
        callback_data = _ie_callback_data(payload)
        if not callback_data:
            return []

        parts = callback_data.split(":")
        if len(parts) not in (3, 4) or parts[0] != "th":
            return []

        action = parts[1]
        try:
            cb_id = int(parts[2])
        except (ValueError, TypeError):
            return []
        cb_version: int | None = None
        if len(parts) == 4:
            try:
                cb_version = int(parts[3])
            except (ValueError, TypeError):
                return []

        aid, aname = _ie_actor(payload)
        mid = _ie_mid(payload)

        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.finished:
                return [{"type": "no_session"}]
            callback_message_id = _ie_message_mid(payload)
            if callback_message_id and action in ("hit", "stand", "double"):
                g.main_message_id = callback_message_id
                self._remember_interaction_message(g, callback_message_id)

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

            # ── start_now / wait_more ──
            if action in ("start_now", "wait_more"):
                return await self._ix_start_decision(g, action, aid, ctx, payload)

            # ── view / hit / stand / double ──
            if action in ("view", "hit", "stand", "double"):
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] player_action_input: uid={aid}, name={aname}, "
                        f"action={action} (callback), chat_id={cid}")
                return await self._ix_player_action(g, action, aid, mid, ctx, payload, cb_version=cb_version)

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
        if len(g.lobby_players) >= g.max_players:
            return [hint("人数已满。")]

        g.lobby_players.append((aid, aname))
        self._remember_player_message(g, aid, mid)
        if len(g.lobby_players) == 1:
            self._lock_first_dealer(g, aid, aname)
        self._touch_lobby(g)
        cnt = len(g.lobby_players)
        if is_callback:
            result: list[dict[str, Any]] = [_answer_action(payload, f"加入成功，牌桌 {g.game_id}")]
        else:
            result = [
                _send_action(
                    self._build_join_notice_text(g, payer_name=aname, amount=g.bet),
                    reply_to_message_id=mid,
                )
            ]

        if cnt >= g.max_players and g.dealer_locked:
            result.extend(await self._ix_begin(g.chat_id, g, g.dealer_id, g.dealer_name, ctx, payload=payload))
        else:
            if g.dealer_locked:
                self._schedule_idle_start_prompt(g.chat_id, g, ctx)
            result.append(
                await self._main_action(
                    ctx,
                    g,
                    self._build_lobby_text(g, self._receiver_label(ctx, payload, g)),
                    reply_markup=_kb_join(g.bet),
                )
            )
        return result

    async def _ix_start_decision(
        self,
        g: TenHalfGame,
        action: str,
        aid: int,
        ctx: PluginContext,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        controller_uid = self._start_controller_uid(g)
        if aid != controller_uid:
            return [_answer_action(payload, "点点点！啥你都点！", show_alert=True)]
        if g.phase != "lobby" or not g.dealer_locked:
            return [_answer_action(payload, "当前不能直接开局。", show_alert=True)]
        if len(g.lobby_players) < 2:
            return [_answer_action(payload, "至少需要 2 名玩家。", show_alert=True)]

        if action == "start_now":
            g.awaiting_start_confirmation = False
            return await self._ix_begin(g.chat_id, g, g.dealer_id, g.dealer_name, ctx, payload=payload)

        self._touch_lobby(g)
        g.status_note = f"{g.dealer_name} 选择继续等待后续玩家加入。"
        self._schedule_idle_start_prompt(g.chat_id, g, ctx)
        return [
            _answer_action(payload, "继续等待后续玩家加入。"),
            await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, self._receiver_label(ctx, payload, g)),
                reply_markup=_kb_join(g.bet),
            ),
        ]

    async def _ix_dealer_choice(
        self, g: TenHalfGame, action: str, aid: int, aname: str,
        ctx: PluginContext, payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Reject stale dealer-choice buttons from older lobby messages."""
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] stale_dealer_choice: uid={aid}, name={aname}, "
                f"action={action}, chat_id={g.chat_id}")
        return [_answer_action(payload, "当前不需要选庄，首位加入玩家自动当庄。", show_alert=True)]

    async def _ix_player_action(
        self, g: TenHalfGame, action: str, aid: int, mid: int | None,
        ctx: PluginContext, payload: dict[str, Any] | None = None,
        cb_version: int | None = None,
    ) -> list[dict[str, Any]]:
        """Handle hit/stand/double button press."""
        callback_data = _ie_callback_data(payload or {})
        parts = callback_data.split(":") if callback_data else []
        target_uid = aid
        if len(parts) >= 3:
            try:
                target_uid = int(parts[2])
            except (ValueError, TypeError):
                target_uid = aid

        current_version = _target_action_version(g, target_uid)
        if cb_version is not None and cb_version != current_version:
            if ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] stale_action_button: uid={aid}, target={target_uid}, action={action}, "
                    f"button_version={cb_version}, current_version={current_version}, "
                    f"phase={g.phase}, chat_id={g.chat_id}",
                )
            return [_answer_action(payload or {}, "按钮已过期，请看最新牌桌。", show_alert=False)] if payload else []

        if aid != target_uid:
            return [_answer_action(payload or {}, "点点点！啥你都点！", show_alert=True)] if payload else []

        if g.phase != "playing":
            if payload:
                return [_answer_action(payload, "游戏不在进行中。")]
            return [_send_action("⚠️ 游戏不在进行中。", reply_to_message_id=mid)]

        if target_uid == g.dealer_id and g.dealer_id > 0:
            if action == "view":
                return [_answer_action(payload or {}, _dealer_private_brief(g), show_alert=True)] if payload else []
            if g.dealer_done():
                return [_answer_action(payload or {}, "庄家本轮已结束。")] if payload else []
            if action == "hit":
                return await self._ix_dealer_hit(g.chat_id, g, ctx, payload)
            if action == "stand":
                return await self._ix_dealer_stand(g.chat_id, g, ctx, payload)
            return [_answer_action(payload or {}, "庄家不能加倍。")] if payload else []

        cur = self._find_player(g, target_uid)
        if cur is None:
            return [_answer_action(payload or {}, "你不在本轮付费玩家列表中。", show_alert=True)] if payload else []
        if cur.is_done:
            return [_answer_action(payload or {}, "你本轮已经结束。")] if payload else []
        if action == "view":
            return [_answer_action(payload or {}, f"你的手牌：{cur.hand_str()}", show_alert=True)] if payload else []
        if action == "hit":
            return await self._ix_hit(g.chat_id, g, ctx, payload, player=cur)
        elif action == "stand":
            return await self._ix_stand(g.chat_id, g, ctx, payload, player=cur)
        elif action == "double":
            return await self._ix_double(g.chat_id, g, ctx, payload, player=cur)
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

            # 正式牌局只接受按钮 callback；消息事件只用于大厅加入提示。
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
        g.dealer_stood = False
        g.action_versions.clear()
        g.timeout_versions.clear()
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

        g.phase = "playing"
        g.turn_order = [p.user_id for p in g.players]
        self._normalize_parallel_state(g)

        if ctx.log:
            player_names = [p.name for p in g.players]
            await ctx.log("info",
                f"[ten_half] game_begin: dealer={dealer_name} (uid={dealer_id}), "
                f"players={player_names}, bet={g.bet}, chat_id={cid}")

        actions: list[dict[str, Any]] = []
        actions.extend(await self._delete_current_join_notice_actions(ctx, g))
        if payload is not None:
            actions.append(_answer_action(payload, _dealer_private_brief(g), show_alert=True))
        g.status_note = f"{g.dealer_name} 当庄，玩家起手 1 张明牌。所有人可同时操作自己的按钮。"
        actions.extend(await self._ix_refresh_or_settle(cid, g, ctx, schedule_all=True))
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
        _bump_target_action_version(g, g.dealer_id)
        if payload is not None:
            actions.append(_answer_action(payload, _dealer_private_brief(g), show_alert=True))
        if g.dealer_busted():
            g.status_note = f"{g.dealer_name} 要牌后爆牌。"
        if g.dealer_five_small():
            g.dealer_stood = True
            g.status_note = f"{g.dealer_name} 五小，自动停牌。"
        if not g.dealer_busted() and not g.dealer_five_small():
            g.status_note = f"{g.dealer_name} 已要牌，当前 {len(g.dealer_cards)} 张。"
        actions.extend(await self._ix_refresh_or_settle(cid, g, ctx, reschedule_uid=g.dealer_id))
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
        g.dealer_stood = True
        _bump_target_action_version(g, g.dealer_id)
        g.status_note = f"{g.dealer_name} 停牌，共 {len(g.dealer_cards)} 张。"
        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, _dealer_private_brief(g), show_alert=True))
        actions.extend(await self._ix_refresh_or_settle(cid, g, ctx))
        return actions

    async def _ix_refresh_or_settle(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        *,
        schedule_all: bool = False,
        reschedule_uid: int | None = None,
    ) -> list[dict[str, Any]]:
        if g.phase != "playing":
            return []

        self._normalize_parallel_state(g)

        if self._all_players_done(g):
            if g.dealer_is_bot:
                return await self._ix_dealer_play(cid, g, ctx)
            if g.dealer_done():
                return await self._ix_settle(cid, g, ctx)

        if schedule_all:
            self._schedule_all_active_timeouts(cid, g, ctx)
        elif reschedule_uid is not None and int(reschedule_uid) in self._active_target_ids(g):
            self._schedule_target_timeout(cid, g, int(reschedule_uid), ctx)

        active_names = [p.name for p in g.players if not p.is_done]
        if g.dealer_id > 0 and not g.dealer_done():
            active_names.append(g.dealer_name)
        if active_names and not g.status_note:
            g.status_note = "等待操作：" + "、".join(active_names)

        action = await self._main_action(
            ctx,
            g,
            self._build_ix_state_text(g),
            reply_markup=_kb_parallel_actions(g),
        )
        return [action] if action else []

    # ── 交互：要牌 ──────────────────────────────────
    async def _ix_hit(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
        *,
        player: PlayerHand | None = None,
    ) -> list[dict[str, Any]]:
        if player is None:
            return []
        p = player
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)
        _bump_target_action_version(g, p.user_id)

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

        actions.extend(
            await self._ix_refresh_or_settle(
                cid,
                g,
                ctx,
                reschedule_uid=None if p.is_done else p.user_id,
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
        *,
        player: PlayerHand | None = None,
    ) -> list[dict[str, Any]]:
        if player is None:
            return []
        p = player
        p.stood = True
        _bump_target_action_version(g, p.user_id)
        if ctx.log:
            await ctx.log("info",
                f"[ten_half] player_action: uid={p.user_id}, name={p.name}, "
                f"action=stand, value={_fv(p.value)}, chat_id={cid}")
        g.status_note = f"{p.name} 停牌，{_cards_brief(p.cards)}。"
        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, f"已停牌，{_cards_brief(p.cards)}。"))
        actions.extend(await self._ix_refresh_or_settle(cid, g, ctx))
        return actions

    # ── 交互：加倍 ──────────────────────────────────
    async def _ix_double(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        payload: dict[str, Any] | None = None,
        *,
        player: PlayerHand | None = None,
    ) -> list[dict[str, Any]]:
        if player is None:
            return []
        p = player
        if len(p.cards) != 2:
            if payload is not None:
                return [_answer_action(payload, "加倍只能在前两张牌时使用。")]
            return [_send_action("⚠️ 加倍只能在前两张牌时使用。")]

        p.doubled = True
        if not g.deck:
            g.deck = create_deck()
        card = g.deck.pop()
        p.cards.append(card)
        _bump_target_action_version(g, p.user_id)

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

        actions.extend(await self._ix_refresh_or_settle(cid, g, ctx))
        return actions

    # ── 交互：庄家回合 ──────────────────────────────
    async def _ix_dealer_play(self, cid: int, g: TenHalfGame, ctx: PluginContext | None = None) -> list[dict[str, Any]]:
        g.phase = "dealer_turn"
        all_bust = all(p.busted for p in g.players)

        if ctx and ctx.log:
            await ctx.log("info",
                f"[ten_half] dealer_turn: dealer={g.dealer_name}, "
                f"all_bust={all_bust}, chat_id={cid}")

        if all_bust:
            g.status_note = f"所有玩家都爆牌，{g.dealer_name} 自动获胜。"
        else:
            draw_notes: list[str] = []
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
                draw_notes.append(card.display())
                if g.dealer_busted():
                    break
            if g.dealer_busted():
                suffix = f"，要牌 {'、'.join(draw_notes)}" if draw_notes else ""
                g.status_note = f"{g.dealer_name}{suffix} 后爆牌。"
            else:
                suffix = f"，要牌 {'、'.join(draw_notes)}" if draw_notes else ""
                g.status_note = f"{g.dealer_name}{suffix} 后停牌（{_fv(g.dealer_val())}点）。"

        actions: list[dict[str, Any]] = []
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

        # 总底注池 = 庄家基础入场金额 + 所有闲家有效下注（含加倍）
        dealer_bet = g.bet if g.dealer_id else 0
        total_pot = dealer_bet + sum(g.bet * (2 if p.doubled else 1) for p in g.players)

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
                else 2.0 if outcome == "win_5s"
                else 1.0 if outcome == "win"
                else 0.0
            )

            # 赢家获得 = 总底注池 × 倍数 × 0.9（抽水10%）
            reward = int(total_pot * multiplier * 0.9) if multiplier > 0 else 0
            loss = eb if outcome == "lose" else 0

            # 显示文案
            outcome_display = self._settlement_outcome_text(
                p,
                outcome,
                eb,
                reward,
                loss,
                html_mode=True,
            )

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

        dealer_reward = (
            int(total_pot * 0.9)
            if not winners and g.players and len(losers) == len(g.players) and g.dealer_id
            else 0
        )
        if dealer_reward > 0:
            dealer_result = {
                "user_id": g.dealer_id,
                "name": g.dealer_name,
                "outcome": "dealer_win",
                "multiplier": 1.0,
                "reward": dealer_reward,
                "loss": 0,
                "bet": dealer_bet,
            }
            winners.append(dealer_result)
            player_results.append(dealer_result)
            lines.extend([
                "",
                f"🎰 庄家 <b>{_html(g.dealer_name)}</b> 🎉是赢家 获得 <b>{dealer_reward}</b>",
            ])
            if ctx and ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] dealer_reward: uid={g.dealer_id}, name={g.dealer_name}, "
                    f"amount={dealer_reward}, bet={dealer_bet}, total_pot={total_pot}, chat_id={cid}",
                )

        actions: list[dict[str, Any]] = []

        settlement_message_key = (
            _settlement_msg_key(ctx.account_id, cid, g.game_id)
            if ctx is not None
            else None
        )

        # ── 结算公告（走 interaction_bot，新发结算消息） ──
        if ctx is not None:
            actions.append(_send_action(
                "\n".join(lines),
                save_message_id_key=settlement_message_key,
            ))
        else:
            actions.append(_send_action("\n".join(lines)))

        # ── 向每位赢家发放奖励（走 userbot_reply，参照 dice_grid_hunt） ──
        reward_message_keys: list[str] = []
        for w in winners:
            reply_to = self._player_reply_message(g, int(w["user_id"]))
            if not reply_to:
                if ctx and ctx.log:
                    await ctx.log("info",
                        f"[ten_half] reward_skipped_no_payment_message: "
                        f"uid={w['user_id']}, name={w['name']}, amount={w['reward']}, chat_id={cid}")
                continue
            reward_key = _reward_msg_key(ctx.account_id, cid, g.game_id, int(w["user_id"])) if ctx else ""
            if reward_key:
                reward_message_keys.append(reward_key)
            actions.append({
                "type": "send_message",
                "text": f"+{w['reward']}",
                "reply_to_message_id": reply_to,
                "send_via": "userbot_reply",
                **({"save_message_id_key": reward_key} if reward_key else {}),
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
        if ctx is not None and getattr(ctx, "messages", None) is not None:
            self._schedule_settlement_cleanup(ctx, g, reward_message_keys, settlement_message_key)
        self._games.pop(cid, None)
        return actions

PLUGIN_CLASS = TenHalfPlugin

__all__ = ["TenHalfPlugin", "PLUGIN_CLASS"]
