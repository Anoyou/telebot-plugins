"""十点半纸牌游戏插件。

经典十点半纸牌游戏：支持多人对战、加倍、五小等规则。
A=1, 2-10=面值, J/Q/K=0.5点。目标 10.5 点。
五小(5张不爆)最高；同点庄家胜。
"""

from __future__ import annotations

import asyncio
import json
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


try:  # 最新开发指南：优先读标准事件信封
    from app.worker.plugins.events import event_from_interaction_payload
except ImportError:  # pragma: no cover - older TelePilot compatibility
    event_from_interaction_payload = None  # type: ignore[assignment]


def _tp_event(payload: dict[str, Any]) -> Any:
    """标准事件信封主路径；取不到时（旧 runtime/测试桩）返回 None 回退旧平铺 helper。"""
    if event_from_interaction_payload is None or not isinstance(payload, dict):
        return None
    try:
        return event_from_interaction_payload(payload)
    except Exception:  # pragma: no cover - 信封解析异常时安全回退旧路径
        return None


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
# 相对 key：account 由 PluginRedisFacade 命名空间承载。
REDIS_MAIN_MSG_KEY_PREFIX = "main:"
REDIS_JOIN_NOTICE_KEY_PREFIX = "join_notice:"
REDIS_SETTLEMENT_MSG_KEY_PREFIX = "settlement:"
REDIS_REWARD_MSG_KEY_PREFIX = "reward:"
REDIS_LOBBY_STATE_KEY_PREFIX = "lobby_state:"
REDIS_TRANSIENT_USERBOT_MSG_KEY_PREFIX = "transient_userbot:"
INTERACTION_SEND_VIA = "interaction_bot"
USERBOT_SEND_VIA = "userbot_reply"
PLUGIN_VERSION = "0.4.19"
JOIN_NOTICE_AUTO_DELETE_DELAY_SECONDS = 10
TRANSIENT_USERBOT_DELETE_DELAY_SECONDS = 5
JOIN_MODE_TRANSFER = "transfer"
JOIN_MODE_SILENT_DEBIT = "silent_debit"
DEFAULT_STAKE_OPTIONS = (1000, 10000, 50000, 100000)
PENDING_DEBIT_TTL_SECONDS = 45
PENDING_DEBIT_RETRY_SECONDS = 5
ACTION_DEBOUNCE_SECONDS = 1.2
JOIN_DEBIT_ANCHOR_MISSING_TEXT = "无法扣款，加入失败。可手动发言一次后再次尝试扣款加入。"


@dataclass
class Card:
    suit: str
    rank: str

    @property
    def value(self) -> float:
        if self.rank == "A":
            return 1.0
        if self.rank in ("J", "Q", "K"):
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
    del account_id
    return f"{REDIS_MAIN_MSG_KEY_PREFIX}{chat_id}"


def _join_notice_key(account_id: int, chat_id: int) -> str:
    del account_id
    return f"{REDIS_JOIN_NOTICE_KEY_PREFIX}{chat_id}"


def _settlement_msg_key(account_id: int, chat_id: int, game_id: str) -> str:
    del account_id
    return f"{REDIS_SETTLEMENT_MSG_KEY_PREFIX}{chat_id}:{game_id}"


def _reward_msg_key(account_id: int, chat_id: int, game_id: str, user_id: int) -> str:
    del account_id
    return f"{REDIS_REWARD_MSG_KEY_PREFIX}{chat_id}:{game_id}:{user_id}"


def _transient_userbot_msg_key(account_id: int, chat_id: int, label: str) -> str:
    del account_id
    safe_label = "".join(ch for ch in str(label or "msg") if ch.isalnum() or ch in "_-")[:32] or "msg"
    return f"{REDIS_TRANSIENT_USERBOT_MSG_KEY_PREFIX}{chat_id}:{safe_label}:{secrets.token_hex(3)}"


def _lobby_state_key(account_id: int, chat_id: int) -> str:
    del account_id
    return f"{REDIS_LOBBY_STATE_KEY_PREFIX}{chat_id}"


def _join_mode_key(account_id: int) -> str:
    del account_id
    return "join_mode"


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


def _inline_payment_amount(text: str) -> int:
    cleaned = str(text or "").strip()
    if not cleaned.startswith("+"):
        return 0
    amount = cleaned[1:].strip()
    return int(amount) if amount.isdigit() else 0


def _identity_token(raw: Any) -> str:
    return "".join(str(raw or "").strip().casefold().split())


def _is_anonymous_payer_name(raw: Any) -> bool:
    return str(raw or "").strip() in {"匿名用户", "未知用户", "用户"}


def _normalize_join_mode(raw: Any, default: str = JOIN_MODE_TRANSFER) -> str:
    text = str(raw or "").strip().lower()
    if text in {"silent_debit", "silent", "debit", "auto_debit", "无感", "扣款", "无感模式"}:
        return JOIN_MODE_SILENT_DEBIT
    if text in {"transfer", "payment", "pay", "转账", "转账模式"}:
        return JOIN_MODE_TRANSFER
    return default


def _join_mode_label(mode: str) -> str:
    return "无感模式" if mode == JOIN_MODE_SILENT_DEBIT else "转账模式"


def _join_mode_from_command_text(text: str, current: str) -> str | None:
    cleaned = str(text or "").strip().lower()
    compact = "".join(cleaned.split())
    if compact not in {"10d模式", "十点半模式"} and not compact.startswith(("10d模式", "十点半模式")):
        return None
    tail = compact.removeprefix("10d模式").removeprefix("十点半模式")
    if not tail:
        return JOIN_MODE_SILENT_DEBIT if current == JOIN_MODE_TRANSFER else JOIN_MODE_TRANSFER
    if tail in {"转账", "付款", "转账模式", "付款模式"}:
        return JOIN_MODE_TRANSFER
    if tail in {"无感", "扣款", "无感模式", "扣款模式"}:
        return JOIN_MODE_SILENT_DEBIT
    return None


def _is_userbot_message(payload: dict[str, Any]) -> bool:
    source = _ps(payload)
    return str(source.get("channel") or "").strip() == "userbot"


def _is_userbot_self(payload: dict[str, Any], user_id: int) -> bool:
    return _is_userbot_message(payload) or _is_payload_userbot_actor(payload, user_id)


def _is_debit_payment_notice(payload: dict[str, Any]) -> bool:
    text = _ie_text(payload)
    if "扣减" in text:
        return True
    event = _pe(payload)
    payment = _pay(payload)
    for key in ("direction", "kind", "mode", "payment_type", "template", "action"):
        value = str(payment.get(key) or event.get(key) or payload.get(key) or "").strip().lower()
        if value in {"debit", "deduct", "charge", "扣减", "扣款"}:
            return True
    return False


# ─────────────────────────────────────────────────────
# 玩家手牌
# ─────────────────────────────────────────────────────
@dataclass
class PlayerHand:
    user_id: int
    name: str
    cards: list[Card] = field(default_factory=list)
    stake: int = 0
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
        """开局一张牌版不再设置“天生十点半”特殊牌型。"""
        return False

    @property
    def is_five_small(self) -> bool:
        """5 张牌且不爆 → 五小。"""
        return len(self.cards) >= 5 and self.value <= 10.5 + 1e-9

    @property
    def is_done(self) -> bool:
        return (
            self.busted
            or self.stood
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
    turn_timeout: int = 45
    lobby_timeout: int = 60
    settlement_cleanup_delay: int = 60
    service_fee_percent: int = 10
    idle_start_seconds: int = 15
    game_id: str = field(default_factory=lambda: secrets.token_hex(3).upper())
    # select_bet -> lobby -> playing -> dealer_turn -> finished
    phase: str = "lobby"
    join_mode: str = JOIN_MODE_TRANSFER
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
    join_notice_version: int = 0
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
    paid_stakes: dict[int, int] = field(default_factory=dict)
    pending_debits: dict[int, dict[str, Any]] = field(default_factory=dict)
    stake_options: list[int] = field(default_factory=list)
    recent_action_clicks: dict[str, float] = field(default_factory=dict)

    # ── 庄家辅助 ─────────────────────────────────────
    @property
    def dealer_is_bot(self) -> bool:
        return self.dealer_id == 0

    def dealer_val(self) -> float:
        return sum(c.value for c in self.dealer_cards)

    def dealer_natural(self) -> bool:
        return False

    def dealer_five_small(self) -> bool:
        return len(self.dealer_cards) >= 5 and self.dealer_val() <= 10.5 + 1e-9

    def dealer_busted(self) -> bool:
        return self.dealer_val() > 10.5 + 1e-9

    def dealer_done(self) -> bool:
        return (
            self.dealer_busted()
            or self.dealer_stood
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


def _user_id_set(value: Any) -> set[int]:
    ids: set[int] = set()
    if isinstance(value, dict):
        for key in ("user_id", "id", "tg_user_id", "owner_user_id", "account_owner_user_id", "userbot_user_id"):
            parsed = _pint(value.get(key), 0, minimum=1)
            if parsed:
                ids.add(parsed)
        for key in ("owner_user_ids", "admin_user_ids", "userbot_user_ids"):
            ids.update(_user_id_set(value.get(key)))
        return ids
    if isinstance(value, (list, tuple, set)):
        for item in value:
            ids.update(_user_id_set(item))
        return ids
    parsed = _pint(value, 0, minimum=1)
    if parsed:
        ids.add(parsed)
    return ids


def _payload_userbot_user_ids(payload: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for key in ("owner_user_ids", "admin_user_ids", "userbot_user_ids"):
        ids.update(_user_id_set(payload.get(key)))
    for key in ("userbot_user_id", "owner_user_id", "account_owner_user_id", "tg_user_id"):
        ids.update(_user_id_set(payload.get(key)))
    for envelope_key in ("account", "source", "sender"):
        envelope = payload.get(envelope_key)
        if isinstance(envelope, dict):
            ids.update(_user_id_set({
                "owner_user_ids": envelope.get("owner_user_ids"),
                "admin_user_ids": envelope.get("admin_user_ids"),
                "userbot_user_id": envelope.get("userbot_user_id"),
                "owner_user_id": envelope.get("owner_user_id"),
                "account_owner_user_id": envelope.get("account_owner_user_id"),
                "tg_user_id": envelope.get("tg_user_id"),
            }))
    return ids


def _is_payload_userbot_actor(payload: dict[str, Any], user_id: int) -> bool:
    return bool(user_id and user_id in _payload_userbot_user_ids(payload))


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


def _format_stake_label(amount: int) -> str:
    amount = int(amount)
    if amount % 10000 == 0:
        return f"{amount // 10000}万"
    if amount % 1000 == 0:
        return f"{amount // 1000}千"
    return str(amount)


def _stake_options_from_payload(ctx: PluginContext | None, payload: dict[str, Any]) -> list[int]:
    sources: list[Any] = [
        payload.get("stake_options"),
        _module_config(payload).get("stake_options"),
        _module_config(payload).get("bet_options"),
        _trigger_payload(payload).get("stake_options"),
        _trigger_payload(payload).get("bet_options"),
    ]
    if ctx is not None and isinstance(ctx.config, dict):
        sources.extend([
            ctx.config.get("stake_options"),
            ctx.config.get("bet_options"),
        ])

    for raw in sources:
        values: list[Any]
        if isinstance(raw, str):
            values = [item.strip() for item in raw.replace("，", ",").split(",")]
        elif isinstance(raw, (list, tuple)):
            values = list(raw)
        else:
            values = []
        parsed: list[int] = []
        seen: set[int] = set()
        for value in values:
            amount = _pint(value, 0, minimum=1)
            if amount <= 0 or amount in seen:
                continue
            parsed.append(amount)
            seen.add(amount)
            if len(parsed) >= 8:
                break
        if parsed:
            return parsed
    return list(DEFAULT_STAKE_OPTIONS)


def _stake_selection_text(g: TenHalfGame) -> str:
    host = _display_name(g.host_name) if g.host_name else "发起人"
    return "\n".join([
        "🃏 <b>十点半开局</b>",
        "",
        "请选择要开局的底注额度：",
        f"只有 <b>{_html_name(host)}</b> 可以选择本局底注。",
    ])


def _kb_stake_options(options: list[int]) -> dict[str, Any]:
    buttons = [
        {"text": _format_stake_label(amount), "callback_data": f"th:stake:{amount}"}
        for amount in options
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return {"inline_keyboard": rows}


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
def _rules_button() -> dict[str, str]:
    return {"text": "📜 规则", "callback_data": "th:rules:0"}


def _rules_text(g: TenHalfGame | None = None) -> str:
    mode_note = "无感模式加倍会再扣底注；转账模式不按钮加倍。"
    service_fee_percent = 10
    if g is not None and g.join_mode == JOIN_MODE_SILENT_DEBIT:
        mode_note = f"加倍会再扣 {g.bet}，补1张即停。"
    if g is not None:
        service_fee_percent = max(0, min(100, int(g.service_fee_percent)))
    return "\n".join([
        "规则：A=1，2-10按牌面，J/Q/K=0.5。",
        "目标≤10.5且越大越好；开局每人1张，庄家首牌暗牌。",
        "五小最大；同点庄家胜，双爆闲家输。",
        f"已有2张后可加倍，{mode_note}",
        "庄家一对一制；闲家赢牌的倍率奖金由庄家出。",
        f"刷屏费收赢家 {service_fee_percent}%，从赢家倍率奖金里扣。",
    ])


def _kb_join(bet: int, join_mode: str = JOIN_MODE_TRANSFER) -> dict[str, Any] | None:
    """Lobby join button.

    Paid transfer mode uses transfer-to-userbot flow; silent-debit mode uses
    the button and asks userbot to debit the clicked player.
    """
    rows: list[list[dict[str, str]]] = []
    if not (bet > 0 and join_mode != JOIN_MODE_SILENT_DEBIT):
        label = f"🙋🏻‍♂️ 我同意被 扣款 {bet} 后加入牌局" if bet > 0 else "🎮 加入游戏"
        rows.append([{"text": label, "callback_data": "th:join:0"}])
    rows.append([_rules_button()])
    return {
        "inline_keyboard": rows,
    }


def _kb_start_decision(uid: int, bet: int = 0, join_mode: str = JOIN_MODE_TRANSFER) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = [
        [
            {"text": "▶️ 直接开局", "callback_data": f"th:start_now:{uid}"},
            {"text": "⏳ 继续等待", "callback_data": f"th:wait_more:{uid}"},
        ]
    ]
    if not (bet > 0 and join_mode != JOIN_MODE_SILENT_DEBIT):
        label = f"🙋🏻‍♂️ 我同意被 扣款 {bet} 后加入牌局" if bet > 0 else "🎮 加入游戏"
        rows.append([{"text": label, "callback_data": "th:join:0"}])
    rows.append([_rules_button()])
    return {"inline_keyboard": rows}


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


def _board_action_version(g: TenHalfGame) -> int:
    return max(1, int(g.action_version or 0))


def _kb_unified_action_row(g: TenHalfGame) -> list[dict[str, str]]:
    version = _board_action_version(g)
    return [
        {"text": "👀 看我的牌", "callback_data": "th:view:0"},
        {"text": "🃏 要牌", "callback_data": f"th:hit:0:{version}"},
        {"text": "🛑 停牌", "callback_data": f"th:stand:0:{version}"},
        {"text": "💰 加倍", "callback_data": f"th:double:0:{version}"},
    ]


def _kb_parallel_actions(g: TenHalfGame) -> dict[str, Any] | None:
    return {"inline_keyboard": [_kb_unified_action_row(g), [_rules_button()]]} if _active_action_targets(g) else None


def _active_action_targets(g: TenHalfGame) -> list[int]:
    ids = [p.user_id for p in g.players if not p.is_done]
    if g.dealer_id > 0 and not g.dealer_done():
        ids.append(g.dealer_id)
    return ids


def _consume_action_click(g: TenHalfGame, uid: int, action: str, *, now: float | None = None) -> bool:
    if action not in {"hit", "stand", "double"}:
        return True
    current = time.monotonic() if now is None else float(now)
    stale_before = current - max(ACTION_DEBOUNCE_SECONDS * 4, 5.0)
    for key, ts in list(g.recent_action_clicks.items()):
        if ts < stale_before:
            g.recent_action_clicks.pop(key, None)
    key = f"{int(uid)}:{action}"
    last = float(g.recent_action_clicks.get(key) or 0.0)
    if last and current - last < ACTION_DEBOUNCE_SECONDS:
        return False
    g.recent_action_clicks[key] = current
    return True


def _html(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _display_name(name: Any, *, limit: int = 10) -> str:
    text = str(name or "").strip() or "玩家"
    return text[:limit]


def _html_name(name: Any) -> str:
    return _html(_display_name(name))


def _points(cards: list[Card]) -> float:
    return sum(c.value for c in cards)


def _cards_brief(cards: list[Card]) -> str:
    if not cards:
        return "未要牌"
    return f"{len(cards)}张 · {_fv(_points(cards))}点"


def _dealer_visible_cards(g: TenHalfGame) -> list[Card]:
    return g.dealer_cards[1:] if len(g.dealer_cards) > 1 else []


def _dealer_safe_visible_points(g: TenHalfGame) -> float:
    total = 0.0
    for card in _dealer_visible_cards(g):
        next_total = total + card.value
        if next_total > 10.5 + 1e-9:
            break
        total = next_total
    return total


def _dealer_public_action_points(g: TenHalfGame) -> str:
    return _fv(_dealer_safe_visible_points(g)) if _dealer_visible_cards(g) else "0"


def _player_status(p: PlayerHand, current_uid: int | None = None) -> str:
    tags: list[str] = []
    if p.user_id == current_uid:
        tags.append("行动中")
    if p.doubled:
        tags.append("已加倍")
    if p.is_five_small:
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
        elif g.dealer_stood:
            tags.append("已停牌")
        elif g.phase == "playing" and g.dealer_id > 0:
            tags.append("等待操作")
        return " · ".join(tags)
    visible_cards = _dealer_visible_cards(g)
    visible = _dealer_public_action_points(g)
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
    send_via: str | None = INTERACTION_SEND_VIA,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "send_message",
        "text": text,
        "parse_mode": "html",
    }
    if send_via is not None:
        action["send_via"] = send_via
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
    send_via: str | None = INTERACTION_SEND_VIA,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "edit_message",
        "message_id": message_id,
        "text": text,
        "parse_mode": "html",
    }
    if send_via is not None:
        action["send_via"] = send_via
    if reply_markup is not None:
        action["reply_markup"] = reply_markup
    return action


def _delete_action(
    message_id: int,
    *,
    chat_id: int | None = None,
    send_via: str | None = INTERACTION_SEND_VIA,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "delete_message",
        "message_id": message_id,
    }
    if send_via is not None:
        action["send_via"] = send_via
    if chat_id is not None:
        action["chat_id"] = chat_id
    return action


def _debit_action(
    g: TenHalfGame,
    user_id: int,
    *,
    amount: int | None = None,
    reply_to_message_id: int | None = None,
    reply_anchor_missing_text: str = "无法扣款，加入失败。",
    suppress_reply_anchor_missing_notice: bool = True,
) -> dict[str, Any]:
    debit_amount = int(amount if amount is not None else g.bet)
    action: dict[str, Any] = {
        "type": "send_message",
        "send_via": USERBOT_SEND_VIA,
        "chat_id": g.chat_id,
        "text": f"-{debit_amount}",
        "parse_mode": "plain",
        "reply_to_user_id": int(user_id),
        "reply_to_search_limit": 50,
        "reply_anchor_missing_text": reply_anchor_missing_text,
        "suppress_reply_anchor_missing_notice": suppress_reply_anchor_missing_notice,
    }
    if reply_to_message_id:
        action["reply_to_message_id"] = int(reply_to_message_id)
    return action


def _answer_action(payload: dict[str, Any], text: str, *, show_alert: bool = False) -> dict[str, Any]:
    return {
        "type": "answer_callback",
        "callback_query_id": _ie_callback_id(payload),
        "text": text,
        "show_alert": show_alert,
    }


def _session_sync_action(g: TenHalfGame, payload: dict[str, Any]) -> dict[str, Any]:
    participant_ids = sorted({int(uid) for uid, _name in g.lobby_players if int(uid or 0) > 0})
    return {
        "type": "start_session",
        "chat_id": g.chat_id,
        "entry_key": "start_ten_half",
        "event_type": _ie_type(payload) or "message",
        "started_by_user_id": g.host_user_id or g.dealer_id or (participant_ids[0] if participant_ids else None),
        "started_by_message_id": _ie_mid(payload),
        "participant_policy": "paid_pool",
        "paid_user_ids": participant_ids,
        "participant_user_ids": participant_ids,
        "data": {"ten_half_lobby": _lobby_snapshot(g)},
    }


def _lobby_snapshot(g: TenHalfGame) -> dict[str, Any]:
    return {
        "schema": 1,
        "plugin_version": PLUGIN_VERSION,
        "chat_id": g.chat_id,
        "bet": g.bet,
        "max_players": g.max_players,
        "turn_timeout": g.turn_timeout,
        "lobby_timeout": g.lobby_timeout,
        "settlement_cleanup_delay": g.settlement_cleanup_delay,
        "service_fee_percent": g.service_fee_percent,
        "idle_start_seconds": g.idle_start_seconds,
        "game_id": g.game_id,
        "phase": g.phase,
        "join_mode": g.join_mode,
        "dealer_id": g.dealer_id,
        "dealer_name": g.dealer_name,
        "dealer_locked": g.dealer_locked,
        "host_user_id": g.host_user_id,
        "host_name": g.host_name,
        "lobby_players": [{"user_id": uid, "name": name} for uid, name in g.lobby_players],
        "started_wall_time": time.time() - max(0.0, time.monotonic() - float(g.started_at or time.monotonic())),
        "via_interaction": g.via_interaction,
        "main_message_id": g.main_message_id,
        "join_notice_msg_id": g.join_notice_msg_id,
        "join_notice_version": g.join_notice_version,
        "payment_receiver_name": g.payment_receiver_name,
        "status_note": g.status_note,
        "awaiting_start_confirmation": g.awaiting_start_confirmation,
        "lobby_version": g.lobby_version,
        "player_message_ids": {str(uid): mid for uid, mid in g.player_message_ids.items()},
        "paid_stakes": {str(uid): int(amount) for uid, amount in g.paid_stakes.items()},
        "pending_debits": {str(uid): dict(item) for uid, item in g.pending_debits.items()},
        "stake_options": [int(amount) for amount in g.stake_options],
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
    command_config_keys = {
        "timeout",
        "lobby_timeout",
        "max_players",
        "settlement_cleanup_delay",
        "service_fee_percent",
        "join_mode",
        "stake_options",
    }

    def __init__(self) -> None:
        super().__init__()
        self._command = "10d"
        self._turn_timeout = 45
        self._lobby_timeout = 60
        self._settlement_cleanup_delay = 60
        self._service_fee_percent = 10
        self._max_players = 5
        self._join_mode = JOIN_MODE_TRANSFER
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

    def _lobby_cancel_refunds(self, g: TenHalfGame) -> list[tuple[int, str, int, int | None]]:
        refunds: list[tuple[int, str, int, int | None]] = []
        for uid, name in g.lobby_players:
            amount = int(g.paid_stakes.get(int(uid)) or 0)
            if amount <= 0:
                continue
            refunds.append((int(uid), str(name), amount, self._player_reply_message(g, int(uid))))
        return refunds

    @staticmethod
    def _lobby_refund_message_keys(
        ctx: PluginContext | None,
        g: TenHalfGame,
        refunds: list[tuple[int, str, int, int | None]],
    ) -> list[str]:
        if ctx is None:
            return []
        return [
            _reward_msg_key(ctx.account_id, g.chat_id, f"{g.game_id}:refund", uid)
            for uid, _name, _amount, _reply_to in refunds
        ]

    @staticmethod
    def _lobby_refund_actions(
        g: TenHalfGame,
        refunds: list[tuple[int, str, int, int | None]],
        *,
        reward_message_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        keys = list(reward_message_keys or [])
        for index, (uid, _name, amount, reply_to) in enumerate(refunds):
            actions.append({
                "type": "payout",
                "chat_id": g.chat_id,
                "amount": amount,
                "text": f"+{amount}",
                "parse_mode": "plain",
                "reply_to_user_id": uid,
                "reply_to_search_limit": 50,
                **({"reply_to_message_id": reply_to} if reply_to else {}),
                **({"save_message_id_key": keys[index]} if index < len(keys) and keys[index] else {}),
            })
        return actions

    @staticmethod
    def _lobby_refund_note(refunds: list[tuple[int, str, int, int | None]]) -> str:
        if not refunds:
            return ""
        if len(refunds) == 1:
            _uid, name, amount, _reply_to = refunds[0]
            return f"；已退还 {_display_name(name)} 的入局费 {amount}"
        total = sum(amount for _uid, _name, amount, _reply_to in refunds)
        return f"；已退还 {len(refunds)} 位玩家的入局费共 {total}"

    @staticmethod
    async def _read_saved_message_id(ctx: PluginContext, key: str) -> int | None:
        messages = getattr(ctx, "messages", None)
        reader = getattr(messages, "read_saved_message_id", None)
        if callable(reader):
            try:
                message_id = await reader(key)
                if message_id is not None:
                    return _pint(message_id, 0) or None
            except Exception:
                pass
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return None
        keys = [key, f"tp:msgid:{ctx.account_id}:{key}"]
        for read_key in dict.fromkeys(keys):
            try:
                raw = await redis.get(read_key)
            except Exception:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            parsed = _pint(raw, 0) or None
            if parsed:
                return parsed
        return None

    @staticmethod
    async def _delete_saved_message_id(ctx: PluginContext, key: str) -> None:
        messages = getattr(ctx, "messages", None)
        delete_saved = getattr(messages, "delete_saved_message_id", None)
        if callable(delete_saved):
            try:
                await delete_saved(key)
            except Exception:
                pass
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return
        delete = getattr(redis, "delete", None)
        if not callable(delete):
            return
        keys = [key, f"tp:msgid:{ctx.account_id}:{key}"]
        for item in dict.fromkeys(keys):
            try:
                await delete(item)
            except Exception:
                continue

    async def _load_join_mode(self, ctx: PluginContext) -> str:
        cfg_mode = _normalize_join_mode((ctx.config or {}).get("join_mode"), "")
        if cfg_mode:
            self._join_mode = cfg_mode
        redis = getattr(ctx, "redis", None)
        if redis is not None:
            try:
                raw = await redis.get(_join_mode_key(ctx.account_id))
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                if raw:
                    self._join_mode = _normalize_join_mode(raw, self._join_mode)
            except Exception:
                if ctx.log:
                    await ctx.log("debug", "[ten_half] load_join_mode_failed")
        return self._join_mode

    async def _save_join_mode(self, ctx: PluginContext, mode: str) -> None:
        self._join_mode = _normalize_join_mode(mode, JOIN_MODE_TRANSFER)
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return
        try:
            await redis.set(_join_mode_key(ctx.account_id), self._join_mode)
        except Exception:
            if ctx.log:
                await ctx.log("debug", "[ten_half] save_join_mode_failed")

    async def _save_lobby_state(self, ctx: PluginContext, g: TenHalfGame) -> None:
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return
        key = _lobby_state_key(ctx.account_id, g.chat_id)
        ttl = max(60, int(g.lobby_timeout or self._lobby_timeout or 60) + 180)
        try:
            await redis.set(key, json.dumps(_lobby_snapshot(g), ensure_ascii=False), ex=ttl)
        except Exception:
            if ctx.log:
                await ctx.log("debug", f"[ten_half] save_lobby_state_failed: chat_id={g.chat_id}")

    async def _delete_lobby_state(self, ctx: PluginContext, g: TenHalfGame) -> None:
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return
        key = _lobby_state_key(ctx.account_id, g.chat_id)
        try:
            delete = getattr(redis, "delete", None)
            if callable(delete):
                await delete(key)
        except Exception:
            if ctx.log:
                await ctx.log("debug", f"[ten_half] delete_lobby_state_failed: chat_id={g.chat_id}")

    async def _restore_lobby_state(self, ctx: PluginContext, payload: dict[str, Any], cid: int) -> TenHalfGame | None:
        snapshot = self._lobby_snapshot_from_payload(payload)
        if snapshot is None:
            snapshot = await self._read_lobby_snapshot(ctx, cid)
        if snapshot is None:
            return None
        return self._game_from_lobby_snapshot(snapshot, cid)

    async def _read_lobby_snapshot(self, ctx: PluginContext, cid: int) -> dict[str, Any] | None:
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return None
        key = _lobby_state_key(ctx.account_id, cid)
        try:
            raw = await redis.get(key)
        except Exception:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _lobby_snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
        session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
        data = session.get("data") if isinstance(session.get("data"), dict) else {}
        snapshot = data.get("ten_half_lobby")
        return dict(snapshot) if isinstance(snapshot, dict) else None

    def _game_from_lobby_snapshot(self, snapshot: dict[str, Any], cid: int) -> TenHalfGame | None:
        phase = str(snapshot.get("phase") or "lobby")
        if phase not in {"select_bet", "lobby"}:
            return None
        bet = _pint(snapshot.get("bet"), 0, minimum=1)
        if bet <= 0 and phase != "select_bet":
            return None
        started_wall = float(snapshot.get("started_wall_time") or time.time())
        elapsed = max(0.0, time.time() - started_wall)
        g = TenHalfGame(
            chat_id=cid,
            bet=bet,
            max_players=_pint(snapshot.get("max_players"), self._max_players, minimum=2),
            turn_timeout=_pint(snapshot.get("turn_timeout"), self._turn_timeout, minimum=5),
            lobby_timeout=_pint(snapshot.get("lobby_timeout"), self._lobby_timeout, minimum=10),
            settlement_cleanup_delay=_pint(snapshot.get("settlement_cleanup_delay"), self._settlement_cleanup_delay, minimum=0),
            service_fee_percent=min(100, _pint(snapshot.get("service_fee_percent"), self._service_fee_percent, minimum=0)),
            phase=phase,
            started_at=time.monotonic() - elapsed,
            via_interaction=bool(snapshot.get("via_interaction", True)),
            host_user_id=_pint(snapshot.get("host_user_id"), 0, minimum=0),
            host_name=str(snapshot.get("host_name") or ""),
        )
        g.idle_start_seconds = _pint(snapshot.get("idle_start_seconds"), g.idle_start_seconds, minimum=1)
        g.game_id = str(snapshot.get("game_id") or g.game_id)
        g.join_mode = _normalize_join_mode(snapshot.get("join_mode"), JOIN_MODE_TRANSFER)
        g.dealer_id = _pint(snapshot.get("dealer_id"), 0, minimum=0)
        g.dealer_name = str(snapshot.get("dealer_name") or g.dealer_name)
        g.dealer_locked = bool(snapshot.get("dealer_locked"))
        g.main_message_id = _pint(snapshot.get("main_message_id"), 0) or None
        g.join_notice_msg_id = _pint(snapshot.get("join_notice_msg_id"), 0) or None
        g.join_notice_version = _pint(snapshot.get("join_notice_version"), 0, minimum=0)
        g.payment_receiver_name = str(snapshot.get("payment_receiver_name") or "")
        g.status_note = str(snapshot.get("status_note") or "")
        g.awaiting_start_confirmation = bool(snapshot.get("awaiting_start_confirmation"))
        g.lobby_version = _pint(snapshot.get("lobby_version"), 0, minimum=0)
        raw_players = snapshot.get("lobby_players") if isinstance(snapshot.get("lobby_players"), list) else []
        for item in raw_players:
            if not isinstance(item, dict):
                continue
            uid = _pint(item.get("user_id"), 0, minimum=0)
            name = str(item.get("name") or "玩家").strip() or "玩家"
            if uid:
                g.lobby_players.append((uid, name))
        raw_message_ids = snapshot.get("player_message_ids") if isinstance(snapshot.get("player_message_ids"), dict) else {}
        for key, value in raw_message_ids.items():
            uid = _pint(key, 0, minimum=0)
            mid = _pint(value, 0, minimum=0)
            if uid and mid:
                g.player_message_ids[uid] = mid
        raw_stakes = snapshot.get("paid_stakes") if isinstance(snapshot.get("paid_stakes"), dict) else {}
        for key, value in raw_stakes.items():
            uid = _pint(key, 0, minimum=0)
            amount = _pint(value, 0, minimum=0)
            if uid and amount > 0:
                g.paid_stakes[uid] = amount
        raw_pending = snapshot.get("pending_debits") if isinstance(snapshot.get("pending_debits"), dict) else {}
        for key, value in raw_pending.items():
            if not isinstance(value, dict):
                continue
            uid = _pint(key, 0, minimum=0)
            amount = _pint(value.get("amount"), 0, minimum=0)
            if uid and amount > 0:
                g.pending_debits[uid] = {
                    "name": str(value.get("name") or "玩家").strip() or "玩家",
                    "amount": amount,
                    "message_id": _pint(value.get("message_id"), 0, minimum=0),
                    "requested_at": float(value.get("requested_at") or 0.0),
                }
        for uid, _name in g.lobby_players:
            g.paid_stakes.setdefault(uid, g.bet)
        raw_options = snapshot.get("stake_options") if isinstance(snapshot.get("stake_options"), list) else []
        g.stake_options = []
        seen_options: set[int] = set()
        for item in raw_options:
            amount = _pint(item, 0, minimum=1)
            if amount > 0 and amount not in seen_options:
                g.stake_options.append(amount)
                seen_options.add(amount)
        return g

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

    @staticmethod
    def _remember_pending_debit(
        g: TenHalfGame,
        user_id: int,
        name: str,
        *,
        amount: int,
        message_id: int | None = None,
    ) -> None:
        uid = int(user_id or 0)
        if uid <= 0:
            return
        g.pending_debits[uid] = {
            "name": str(name or "玩家").strip() or "玩家",
            "amount": int(amount or 0),
            "message_id": int(message_id or 0),
            "requested_at": time.time(),
        }

    @staticmethod
    def _prune_pending_debits(g: TenHalfGame, *, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        joined = {int(uid) for uid, _ in g.lobby_players}
        for uid, item in list(g.pending_debits.items()):
            try:
                requested_at = float(item.get("requested_at") or 0.0)
            except (TypeError, ValueError):
                requested_at = 0.0
            if uid in joined or (requested_at and current - requested_at > PENDING_DEBIT_TTL_SECONDS):
                g.pending_debits.pop(uid, None)

    @staticmethod
    def _pending_debit_retryable(item: dict[str, Any], *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        try:
            requested_at = float(item.get("requested_at") or 0.0)
        except (TypeError, ValueError):
            requested_at = 0.0
        return not requested_at or current - requested_at >= PENDING_DEBIT_RETRY_SECONDS

    @staticmethod
    def _resolve_pending_debit_payer(
        g: TenHalfGame,
        *,
        payer_id: int,
        payer_name: str,
        amount: int,
    ) -> tuple[int, str] | None:
        TenHalfPlugin._prune_pending_debits(g)

        candidates: list[tuple[int, dict[str, Any]]] = []
        for uid, item in g.pending_debits.items():
            if _pint(item.get("amount"), 0, minimum=0) == int(amount or 0):
                candidates.append((int(uid), item))
        if not candidates:
            return None

        if payer_id and payer_id in g.pending_debits:
            item = g.pending_debits[payer_id]
            if _pint(item.get("amount"), 0, minimum=0) == int(amount or 0):
                return int(payer_id), str(item.get("name") or payer_name or "玩家").strip() or "玩家"

        payer_token = "" if _is_anonymous_payer_name(payer_name) else _identity_token(payer_name)
        if payer_token:
            name_matches = [
                (uid, item)
                for uid, item in candidates
                if _identity_token(item.get("name")) == payer_token
            ]
            if len(name_matches) == 1:
                uid, item = name_matches[0]
                return uid, str(item.get("name") or payer_name or "玩家").strip() or "玩家"
            return None

        if len(candidates) == 1:
            uid, item = candidates[0]
            return uid, str(item.get("name") or payer_name or "玩家").strip() or "玩家"
        return None

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
        g.join_notice_msg_id = None
        g.known_interaction_message_ids.discard(int(mid))
        g.join_notice_version += 1
        await self._delete_saved_message_id(ctx, key)
        return [_delete_action(mid, chat_id=g.chat_id)]

    def _schedule_join_notice_cleanup(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        version: int,
    ) -> None:
        self._track_task(asyncio.create_task(
            self._join_notice_cleanup_task(
                cid,
                g.started_at,
                version,
                ctx,
                JOIN_NOTICE_AUTO_DELETE_DELAY_SECONDS,
            )
        ))

    async def _join_notice_cleanup_task(
        self,
        cid: int,
        sa: float,
        version: int,
        ctx: PluginContext,
        delay_seconds: int,
    ) -> None:
        await asyncio.sleep(max(0, int(delay_seconds)))
        key = _join_notice_key(ctx.account_id, cid)
        async with self._lock(cid):
            g = self._games.get(cid)
            if (
                not g
                or g.finished
                or g.started_at != sa
                or g.join_notice_version != version
            ):
                return
            mid = await self._read_saved_message_id(ctx, key)
            mid = mid or g.join_notice_msg_id
            if not mid:
                return
            self._remember_interaction_message(g, mid)
            action = _delete_action(mid, chat_id=cid)

        delivered = await self._emit_background_actions(ctx, [action])
        if delivered:
            async with self._lock(cid):
                g = self._games.get(cid)
                if g and g.started_at == sa and g.join_notice_version == version:
                    current_mid = await self._read_saved_message_id(ctx, key)
                    if current_mid == mid:
                        await self._delete_saved_message_id(ctx, key)
                    if g.join_notice_msg_id == mid:
                        g.join_notice_msg_id = None
                    g.known_interaction_message_ids.discard(int(mid))
        if ctx.log:
            level = "info" if delivered else "warn"
            await ctx.log(
                level,
                f"[ten_half] join_notice_cleanup: message_id={mid}, "
                f"delay={delay_seconds}, delivered={delivered}, chat_id={cid}",
            )

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

    def _schedule_transient_userbot_delete(
        self,
        ctx: PluginContext,
        cid: int,
        *,
        message_id: int | None = None,
        message_key: str | None = None,
        delay_seconds: int = TRANSIENT_USERBOT_DELETE_DELAY_SECONDS,
    ) -> None:
        if not message_id and not message_key:
            return
        self._track_task(asyncio.create_task(
            self._transient_userbot_delete_task(
                ctx,
                cid,
                message_id,
                message_key,
                delay_seconds,
            )
        ))

    async def _transient_userbot_delete_task(
        self,
        ctx: PluginContext,
        cid: int,
        message_id: int | None,
        message_key: str | None,
        delay_seconds: int,
    ) -> None:
        await asyncio.sleep(max(0, int(delay_seconds)))
        mid = int(message_id or 0) or None
        if mid is None and message_key:
            mid = await self._read_saved_message_id(ctx, message_key)
        if not mid:
            return
        delivered = await self._emit_background_actions(
            ctx,
            [_delete_action(mid, chat_id=cid, send_via=INTERACTION_SEND_VIA)],
        )
        if delivered and message_key:
            await self._delete_saved_message_id(ctx, message_key)

    def _schedule_lobby_main_refresh(
        self,
        cid: int,
        g: TenHalfGame,
        ctx: PluginContext,
        *,
        delay_seconds: float = 0.75,
    ) -> None:
        self._track_task(asyncio.create_task(
            self._lobby_main_refresh_task(
                cid,
                g.started_at,
                g.lobby_version,
                ctx,
                delay_seconds,
            )
        ))

    async def _lobby_main_refresh_task(
        self,
        cid: int,
        sa: float,
        version: int,
        ctx: PluginContext,
        delay_seconds: float,
    ) -> None:
        await asyncio.sleep(max(0.0, float(delay_seconds)))
        async with self._lock(cid):
            g = self._games.get(cid)
            if (
                not g
                or g.phase != "lobby"
                or g.finished
                or g.started_at != sa
                or g.lobby_version != version
            ):
                return
            reply_markup = None
            if g.awaiting_start_confirmation and g.dealer_locked and 2 <= len(g.lobby_players) < g.max_players:
                controller_uid = self._start_controller_uid(g)
                reply_markup = _kb_start_decision(controller_uid, g.bet, g.join_mode)
            elif len(g.lobby_players) < g.max_players:
                reply_markup = _kb_join(g.bet, g.join_mode)
            action = await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, self._receiver_label(ctx, None, g)),
                reply_markup=reply_markup,
                send_if_missing=False,
            )
        delivered = await self._emit_background_actions(ctx, [action])
        if ctx.log and not delivered:
            await ctx.log("warn", f"[ten_half] lobby_main_refresh_not_delivered: chat_id={cid}")

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
                max(0, int(g.settlement_cleanup_delay)),
            )
        ))

    def _schedule_lobby_timeout(self, cid: int, g: TenHalfGame, ctx: PluginContext) -> None:
        self._track(asyncio.create_task(
            self._lobby_timeout_task(cid, g.started_at, ctx, version=g.lobby_version),
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
        if delay_seconds <= 0:
            if ctx.log:
                await ctx.log("info", f"[ten_half] settlement_cleanup_disabled: chat_id={cid}")
            return
        await asyncio.sleep(max(0, int(delay_seconds)))
        interaction_message_ids: set[int] = set(known_interaction_message_ids or set())
        userbot_message_ids: set[int] = set()

        if main_message_id:
            interaction_message_ids.add(main_message_id)
        if join_notice_msg_id:
            interaction_message_ids.add(join_notice_msg_id)
        if settlement_message_key:
            settlement_mid = await self._read_saved_message_id(ctx, settlement_message_key)
            if settlement_mid:
                interaction_message_ids.add(settlement_mid)
        for key in reward_message_keys:
            reward_mid = await self._read_saved_message_id(ctx, key)
            if reward_mid:
                userbot_message_ids.add(reward_mid)

        actions = [
            _delete_action(mid, chat_id=cid, send_via=INTERACTION_SEND_VIA)
            for mid in sorted(interaction_message_ids)
        ]
        actions.extend(
            _delete_action(mid, chat_id=cid, send_via=INTERACTION_SEND_VIA)
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

    def _touch_lobby(self, g: TenHalfGame, *, clear_status: bool = False) -> None:
        g.lobby_version += 1
        g.awaiting_start_confirmation = False
        if clear_status:
            g.status_note = ""

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
            f"{_display_name(name)} 作为首位加入玩家，已自动成为本局庄家。",
        )

    def _lock_command_dealer(self, g: TenHalfGame, uid: int, name: str) -> None:
        self._lock_dealer(
            g,
            uid,
            name,
            f"{_display_name(name)} 作为开桌者，已直接成为本局庄家。",
        )

    @staticmethod
    def _normalize_player_state(p: PlayerHand) -> None:
        if p.value > 10.5 + 1e-9:
            p.busted = True
        elif p.is_five_small:
            p.stood = True

    @staticmethod
    def _normalize_dealer_state(g: TenHalfGame) -> None:
        if g.dealer_five_small():
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
                g.status_note = f"{_display_name(g.dealer_name)} 超时，自动停牌。"
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
                g.status_note = f"{_display_name(p.name)} 超时，自动停牌。"
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
            g.status_note = f"{g.idle_start_seconds} 秒内没有新玩家加入，{_display_name(g.dealer_name)} 可以选择直接开局或继续等待。"
            if ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] idle_start_prompt: controller={controller_uid}, "
                    f"players={len(g.lobby_players)}/{g.max_players}, chat_id={cid}",
                )
            action = await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, self._receiver_label(ctx, None, g)),
                reply_markup=_kb_start_decision(controller_uid, g.bet, g.join_mode),
                send_if_missing=False,
            )
        delivered = await self._emit_background_actions(ctx, [action])
        if ctx.log and not delivered:
            await ctx.log("warn", f"[ten_half] idle_start_prompt_not_delivered: chat_id={cid}")

    def _game_limits_from_payload(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, int]:
        return {
            "max_players": _config_int(ctx, payload, "max_players", self._max_players, minimum=2, maximum=10),
            "turn_timeout": _config_int(ctx, payload, "timeout", self._turn_timeout, minimum=5, maximum=120),
            "lobby_timeout": _config_int(ctx, payload, "lobby_timeout", self._lobby_timeout, minimum=10, maximum=300),
            "settlement_cleanup_delay": _config_int(
                ctx,
                payload,
                "settlement_cleanup_delay",
                self._settlement_cleanup_delay,
                minimum=0,
                maximum=3600,
            ),
            "service_fee_percent": _config_int(
                ctx,
                payload,
                "service_fee_percent",
                self._service_fee_percent,
                minimum=0,
                maximum=100,
            ),
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
        saved_join_mid = await self._read_saved_message_id(ctx, join_key)
        previous_mid = saved_join_mid or g.join_notice_msg_id
        g.join_notice_version += 1
        notice_version = g.join_notice_version
        actions.append(
            _send_action(
                self._build_join_notice_text(g, payer_name=payer_name, amount=amount),
                reply_to_message_id=_ie_mid(payload),
                save_message_id_key=join_key,
            )
        )
        g.join_notice_msg_id = None
        if previous_mid:
            g.known_interaction_message_ids.discard(int(previous_mid))
            actions.append(_delete_action(previous_mid, chat_id=g.chat_id))
        self._schedule_join_notice_cleanup(g.chat_id, g, ctx, notice_version)
        return actions

    def _build_lobby_text(self, g: TenHalfGame, receiver_label: str) -> str:
        lines = [
            f"🃏 <b>十点半开局 （v {PLUGIN_VERSION}）！</b>",
            f"💰 底注: <b>{g.bet}</b>",
            f"🎛️ 入局模式: <b>{_join_mode_label(g.join_mode)}</b>",
            "",
        ]
        if g.bet > 0 and g.join_mode == JOIN_MODE_SILENT_DEBIT:
            lines.append("📢 点击下方按钮完成自动扣款即可加入本桌")
        elif g.bet > 0:
            lines.append(
                f"📢 请转账 <b>{g.bet}</b> 给 <b>{_html(receiver_label)}</b> 即可参与本桌牌局～"
            )
        else:
            lines.append("📢 点击下方按钮即可参与本桌牌局～")
        lines.append(
            f"⏰ 等待玩家加入中... ({g.lobby_timeout}秒)，当前牌桌 ID 为 <code>{g.game_id}</code>"
        )
        if g.lobby_players:
            players = "、".join(_html_name(name) for _, name in g.lobby_players)
            lines.extend([
                "",
                f"👥 已加入 ({len(g.lobby_players)}/{g.max_players}): {players}",
            ])
        if g.dealer_locked:
            lines.extend(["", f"🎰 庄家: <b>{_html_name(g.dealer_name)}</b>"])
            if 2 <= len(g.lobby_players) < g.max_players:
                lines.append(
                    f"🕒 {g.idle_start_seconds} 秒无人加入可由庄家提前开局，"
                    f"{g.lobby_timeout} 秒后自动开局。"
                )
        if g.status_note:
            lines.extend(["", _html(g.status_note)])
        return "\n".join(lines)

    def _build_join_notice_text(self, g: TenHalfGame, *, payer_name: str, amount: int) -> str:
        players = [f"• {_html_name(name)}" for _, name in g.lobby_players] or ["• 暂无"]
        if g.bet > 0 and amount <= 0:
            amount_label = "免转账"
        elif g.bet > 0 and g.join_mode == JOIN_MODE_SILENT_DEBIT:
            amount_label = f"自动扣款 {amount}"
        else:
            amount_label = str(amount)
        lines = [
            f"✅ <b>{_html_name(payer_name)}</b> 加入牌局成功",
            f"🆔 牌桌 ID: <code>{g.game_id}</code>",
            f"💰 入场金额: {amount_label}",
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
            f"🃏 <b>十点半v{PLUGIN_VERSION} · 牌桌 <code>{g.game_id}</code></b>",
            f"💰 底注: <b>{g.bet}</b> · {phase_text}",
            "",
            f"🎰 庄家 <b>{_html_name(g.dealer_name)}</b>: {_dealer_public_brief(g, reveal=reveal_dealer)}",
        ]
        if g.phase == "playing" and not g.finished:
            active_names = [p.name for p in g.players if not p.is_done]
            if g.dealer_id > 0 and not g.dealer_done():
                active_names.append(g.dealer_name)
            if active_names:
                lines.append("⚡ 所有人共用下方按钮，系统按点击者识别自己的手牌；全部停牌/爆牌后统一结算。")
                lines.append("⚠️ 加倍是闲家特权，需已有 2 张牌；加倍需加一倍底注且只能再要一张牌。若赢，会得到双倍奖金。")
                lines.append("⏳ 等待：" + "、".join(_html_name(name) for name in active_names))
        if g.phase == "dealer_turn" and not g.dealer_is_bot and not g.finished:
            lines.append("👉 所有玩家已行动，庄家请要牌或停牌。")
        if g.players:
            lines.extend(["", "👥 玩家"])
            for p in g.players:
                status = _player_status(p)
                suffix = f" · {status}" if status else ""
                marker = "👉" if not p.is_done else "•"
                lines.append(f"{marker} <b>{_html_name(p.name)}</b>: {_cards_brief(p.cards)}{suffix}")
        if g.status_note:
            lines.extend(["", _html(g.status_note)])
        return "\n".join(lines)

    # ── 生命周期 ─────────────────────────────────────
    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = _normalize_command_name(cfg.get("command", "10d"))
        self._turn_timeout = _pint(cfg.get("timeout"), 45, minimum=5)
        self._lobby_timeout = _pint(cfg.get("lobby_timeout"), 60, minimum=10)
        self._settlement_cleanup_delay = _pint(cfg.get("settlement_cleanup_delay"), 60, minimum=0)
        self._service_fee_percent = min(100, _pint(cfg.get("service_fee_percent"), 10, minimum=0))
        self._max_players = _pint(cfg.get("max_players"), 5, minimum=2)
        await self._load_join_mode(ctx)
        self.commands = {}
        if ctx.log:
            await ctx.log("info", f"[ten_half] 已启动，开局入口：交互 Bot 关键词/规则，当前入局模式：{_join_mode_label(self._join_mode)}")

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
    # 旧命令入口只保留迁移提示；十点半业务开局只走交互 Bot 关键词/规则。
    # ═══════════════════════════════════════════════════
    async def _cmd(
        self, client: Any, event: Any, args: list[str],
        account_id: int, ctx: PluginContext,
    ) -> None:
        await event.reply(
            "十点半现在只通过交互 Bot 关键词/规则开局；账号 userbot 只负责收付款和发奖。"
            " 账号本人可在群内发送「10d模式」切换转账/无感扣款入局；已有等待大厅时发送「入局」可直接加入，普通消息由交互 Bot 通道处理。",
            parse_mode="html",
        )

    # ═══════════════════════════════════════════════════
    # 大厅
    # ═══════════════════════════════════════════════════
    async def _lobby_timeout_task(
        self,
        cid: int,
        sa: float,
        ctx: PluginContext,
        *,
        version: int | None = None,
    ) -> None:
        g0 = self._games.get(cid)
        await asyncio.sleep(g0.lobby_timeout if g0 and g0.started_at == sa else self._lobby_timeout)
        actions: list[dict[str, Any] | None] = []
        async with self._lock(cid):
            g = self._games.get(cid)
            if (
                not g
                or g.phase != "lobby"
                or g.finished
                or g.started_at != sa
                or (version is not None and g.lobby_version != version)
            ):
                return
            if not g.lobby_players:
                g.finished = True
                self._games.pop(cid, None)
                await self._delete_lobby_state(ctx, g)
                g.status_note = "没人加入，牌局已取消。"
                actions.append(await self._main_action(
                    ctx,
                    g,
                    self._build_lobby_text(g, self._receiver_label(ctx, None, g)),
                    reply_markup=None,
                ))
                self._schedule_settlement_cleanup(ctx, g, [])
                actions.append({"type": "end_session"})
                if ctx.log:
                    await ctx.log("info", f"[ten_half] lobby_timeout_cancel: players=0, chat_id={cid}")
            else:
                if len(g.lobby_players) < 2:
                    refunds = self._lobby_cancel_refunds(g)
                    refund_message_keys = self._lobby_refund_message_keys(ctx, g, refunds)
                    g.finished = True
                    self._games.pop(cid, None)
                    await self._delete_lobby_state(ctx, g)
                    g.status_note = f"大厅等待已结束，参与人数不足 2 人，牌局已取消{self._lobby_refund_note(refunds)}。"
                    actions.append(await self._main_action(
                        ctx,
                        g,
                        self._build_lobby_text(g, self._receiver_label(ctx, None, g)),
                        reply_markup=None,
                    ))
                    actions.extend(self._lobby_refund_actions(
                        g,
                        refunds,
                        reward_message_keys=refund_message_keys,
                    ))
                    self._schedule_settlement_cleanup(ctx, g, refund_message_keys)
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

        返回值: win_5s | win | lose
        """
        pfs = p.is_five_small

        if p.busted or p.value > 10.5 + 1e-9:
            return "lose"

        if dealer_busted:
            # 庄家爆牌：没爆的玩家赢
            if pfs:
                return "win_5s"
            return "win"

        # ── 五小最高；五小互比时点数小者胜，同点庄家胜 ──
        if pfs and dealer_five_small:
            return "win_5s" if p.value < dealer_val else "lose"
        if pfs:
            return "win_5s"
        if dealer_five_small:
            return "lose"

        # ── 普通比较；同点庄家胜 ──
        if p.value > dealer_val:
            return "win"
        return "lose"

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
            if outcome == "win_5s":
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

        # 主路径：标准事件信封（event_from_interaction_payload）。
        # 旧平铺 helper（_ie_type/_ie_chat）保留为 fallback：新 runtime 缺字段、
        # 旧 runtime 或测试桩没有 events 模块时继续可用，行为保持不变。
        event = _tp_event(payload)
        etype = _ie_type(payload)
        if event is not None:
            std_type = str(getattr(event, "type", "") or "").strip()
            # 仅当标准信封给出更具体的类型时才采用；泛化默认 "message" 交给旧 helper，
            # 避免 keyword/payment_confirmed 等本插件特有分派被信封默认值覆盖。
            if std_type and std_type != "message" and std_type != etype:
                etype = std_type

        cid = _ie_chat(payload)
        if event is not None:
            std_cid = getattr(getattr(event, "message", None), "chat_id", None)
            if std_cid and not cid:
                cid = int(std_cid)
        if not cid:
            return [_send_action("❌ 十点半需要在群聊里使用。")]

        if etype == "payment_confirmed":
            return await self._ix_payment_join(ctx, payload, cid)
        if etype == "keyword":
            return await self._ix_start(ctx, payload, cid)
        if etype == "command":
            return [{"type": "no_session"}]
        if etype == "callback_query":
            return await self._ix_callback(ctx, payload, cid)
        if etype == "message":
            return await self._ix_message(ctx, payload, cid)
        if etype == "session_close":
            async with self._lock(cid):
                g = self._games.pop(cid, None)
                if g is not None:
                    await self._delete_lobby_state(ctx, g)
            return [{"type": "end_session"}]
        return []

    # ── 交互：开局 ──────────────────────────────────
    async def _ix_start(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        join_mode = await self._load_join_mode(ctx)
        limits = self._game_limits_from_payload(ctx, payload)
        stake_options = _stake_options_from_payload(ctx, payload)

        async with self._lock(cid):
            existing = self._games.get(cid)
            if existing and not existing.finished:
                phase_label = {
                    "select_bet": "正在选择底注",
                    "lobby": "正在等待入局",
                    "playing": "正在进行",
                    "dealer_turn": "正在庄家回合",
                }.get(existing.phase, "正在进行")
                return [
                    _send_action(
                        f"⚠️ 当前已有十点半牌桌（{phase_label}），请等本局结束后再开新局。",
                        reply_to_message_id=_ie_mid(payload),
                    )
                ]
            host_id, host_name = _ie_actor(payload)
            g = TenHalfGame(
                chat_id=cid, bet=0,
                max_players=limits["max_players"],
                turn_timeout=limits["turn_timeout"],
                lobby_timeout=limits["lobby_timeout"],
                settlement_cleanup_delay=limits["settlement_cleanup_delay"],
                service_fee_percent=limits["service_fee_percent"],
                phase="select_bet", join_mode=join_mode, started_at=time.monotonic(),
                via_interaction=True,
                host_user_id=host_id,
                host_name=host_name,
            )
            g.stake_options = stake_options
            g.payment_receiver_name = self._receiver_label(ctx, payload, g)
            self._games[cid] = g
            await self._save_lobby_state(ctx, g)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] stake_select_start: chat_id={cid}, "
                f"host={host_name} ({host_id}), options={stake_options}, "
                f"join_mode={g.join_mode}, max_players={g.max_players}")

        return [
            _session_sync_action(g, payload),
            _send_action(
                _stake_selection_text(g),
                reply_markup=_kb_stake_options(stake_options),
                reply_to_message_id=_ie_mid(payload),
            ),
        ]

    async def _ix_start_lobby_with_bet(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        g: TenHalfGame,
        bet: int,
    ) -> list[dict[str, Any]]:
        g.bet = int(bet)
        g.phase = "lobby"
        g.started_at = time.monotonic()
        g.game_id = secrets.token_hex(3).upper()
        g.status_note = ""
        g.awaiting_start_confirmation = False
        g.lobby_version = 0
        g.payment_receiver_name = self._receiver_label(ctx, payload, g)
        await self._save_lobby_state(ctx, g)

        if ctx.log:
            await ctx.log("info",
                f"[ten_half] game_start: chat_id={g.chat_id}, bet={g.bet}, "
                f"join_mode={g.join_mode}, max_players={g.max_players}, "
                f"lobby_timeout={g.lobby_timeout}, via_interaction=True")

        self._schedule_lobby_timeout(g.chat_id, g, ctx)

        actions: list[dict[str, Any]] = [_answer_action(payload, f"已选择底注 {_format_stake_label(g.bet)}。")]
        selection_mid = _ie_message_mid(payload)
        if selection_mid:
            actions.append(
                _edit_action(
                    selection_mid,
                    f"✅ 已选择底注 <b>{g.bet}</b>，正在开启十点半牌桌。",
                    reply_markup=None,
                )
            )
        actions.extend([
            _session_sync_action(g, payload),
            await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, g.payment_receiver_name),
                reply_markup=_kb_join(g.bet, g.join_mode),
                reply_to_message_id=_ie_mid(payload),
                force_send=True,
            ),
        ])
        return actions


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
        notice_mid = _ie_message_mid(payload)
        payment_status = _ie_payment_status(payload)
        debit_notice = _is_debit_payment_notice(payload)

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
                restored = await self._restore_lobby_state(ctx, payload, cid)
                if restored is None:
                    return await _skip("no_lobby", g)
                self._games[cid] = restored
                g = restored
                self._schedule_lobby_timeout(cid, g, ctx)
                if ctx.log:
                    await ctx.log(
                        "info",
                        f"[ten_half] lobby_restored_for_payment: chat_id={cid}, "
                        f"players={len(g.lobby_players)}/{g.max_players}",
                    )
            if not g.via_interaction:
                return await _skip("not_interaction_lobby", g)
            if not g.payment_receiver_name:
                g.payment_receiver_name = self._receiver_label(ctx, payload, g)

            if g.phase != "lobby":
                return await _skip("phase_not_lobby", g)
            if g.join_mode == JOIN_MODE_SILENT_DEBIT:
                if not debit_notice:
                    return await _skip("silent_debit_lobby", g)
            elif debit_notice:
                return await _skip("debit_notice_not_silent_mode", g)

            if amount != g.bet:
                return await _skip("amount_mismatch", g)

            if g.join_mode == JOIN_MODE_SILENT_DEBIT:
                resolved = self._resolve_pending_debit_payer(
                    g,
                    payer_id=payer_id,
                    payer_name=payer_name,
                    amount=amount,
                )
                if resolved is None:
                    return await _skip("silent_debit_pending_missing", g)
                if resolved[0] != payer_id and ctx.log:
                    await ctx.log(
                        "info",
                        "[ten_half] silent_debit_payer_corrected: "
                        f"parsed={payer_id} ({payer_name}), resolved={resolved[0]} ({resolved[1]}), "
                        f"amount={amount}, chat_id={cid}",
                    )
                payer_id, payer_name = resolved

            if notice_mid:
                self._schedule_transient_userbot_delete(ctx, cid, message_id=notice_mid)

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
            g.paid_stakes[payer_id] = g.bet
            g.pending_debits.pop(payer_id, None)
            if not debit_notice:
                self._remember_player_message(g, payer_id, mid)
            if len(g.lobby_players) == 1:
                self._lock_first_dealer(g, payer_id, payer_name)
            self._touch_lobby(g, clear_status=g.awaiting_start_confirmation)
            await self._save_lobby_state(ctx, g)
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
            actions.append(_session_sync_action(g, payload))

            if cnt >= g.max_players and g.dealer_locked:
                actions.extend(await self._ix_begin(cid, g, g.dealer_id, g.dealer_name, ctx))
                return actions

            self._schedule_lobby_timeout(cid, g, ctx)
            self._schedule_lobby_main_refresh(cid, g, ctx)
            if g.dealer_locked:
                self._schedule_idle_start_prompt(cid, g, ctx)
            return actions

    # ── 交互：callback_query 处理 ────────────────────
    async def _ix_callback(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        """Handle callback_query events from inline keyboard buttons.

        Callback data format: th:<action>:<id>
        Actions: stake, join, rules, hit, stand, double; dealer_yes/dealer_no are stale-button compatibility only.
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
                restored = await self._restore_lobby_state(ctx, payload, cid)
                if restored is None:
                    return [
                        _answer_action(payload, "本局已结束，请等待下一局。"),
                        {"type": "no_session"},
                    ]
                self._games[cid] = restored
                g = restored
            callback_message_id = _ie_message_mid(payload)
            if callback_message_id:
                if action == "stake":
                    self._remember_interaction_message(g, callback_message_id)
                elif action in ("join", "rules", "view", "hit", "stand", "double"):
                    g.main_message_id = callback_message_id
                    self._remember_interaction_message(g, callback_message_id)

            # ── stake selection ──
            if action == "stake":
                if g.phase != "select_bet":
                    return [_answer_action(payload, "本局底注已经选好了。", show_alert=True)]
                if g.host_user_id and aid != g.host_user_id:
                    return [_answer_action(payload, "只有发起开局的人可以选择本局底注。", show_alert=True)]
                options = g.stake_options or _stake_options_from_payload(ctx, payload)
                if cb_id not in options:
                    return [_answer_action(payload, "这个底注额度不可用，请重新开局选择。", show_alert=True)]
                return await self._ix_start_lobby_with_bet(ctx, payload, g, cb_id)

            if g.phase == "select_bet":
                return [_answer_action(payload, "请先由发起开局的人选择底注。", show_alert=True)]

            # ── rules ──
            if action == "rules":
                return [_answer_action(payload, _rules_text(g), show_alert=True)]

            # ── join ──
            if action == "join":
                if g.bet > 0 and g.join_mode != JOIN_MODE_SILENT_DEBIT:
                    return [_answer_action(payload, "请转账底注加入；管理员可发送「入局」直接入桌。", show_alert=True)]
                if g.bet > 0 and g.join_mode == JOIN_MODE_SILENT_DEBIT:
                    if g.phase != "lobby":
                        return [_answer_action(payload, "游戏不在大厅阶段。", show_alert=True)]
                    if any(uid == aid for uid, _ in g.lobby_players):
                        return [_answer_action(payload, "你已经加入了。", show_alert=False)]
                    if len(g.lobby_players) >= g.max_players:
                        return [_answer_action(payload, "人数已满。", show_alert=True)]
                    self._prune_pending_debits(g)
                    if aid in g.pending_debits:
                        if not self._pending_debit_retryable(g.pending_debits[aid]):
                            return [_answer_action(
                                payload,
                                "扣款处理中，请稍等。若刚才提示扣款失败，可手动发言一次后再次尝试扣款加入。",
                                show_alert=True,
                            )]
                        g.pending_debits.pop(aid, None)
                    debit_key = _transient_userbot_msg_key(ctx.account_id, g.chat_id, f"debit_{aid}")
                    self._remember_pending_debit(g, aid, aname, amount=g.bet, message_id=mid)
                    await self._save_lobby_state(ctx, g)
                    action_payload = _debit_action(
                        g,
                        aid,
                        reply_anchor_missing_text=JOIN_DEBIT_ANCHOR_MISSING_TEXT,
                    )
                    action_payload["save_message_id_key"] = debit_key
                    action_payload["failure_callback"] = {
                        "callback_query_id": _ie_callback_id(payload),
                        "error_code": "reply_anchor_missing",
                        "text": JOIN_DEBIT_ANCHOR_MISSING_TEXT,
                        "show_alert": True,
                    }
                    self._schedule_transient_userbot_delete(ctx, g.chat_id, message_key=debit_key)
                    return [action_payload]
                result = await self._ix_join(
                    ctx,
                    payload,
                    g,
                    aid,
                    aname,
                    paid_exempt=False,
                    auto_debit=bool(g.bet > 0 and g.join_mode == JOIN_MODE_SILENT_DEBIT),
                )
                if ctx.log:
                    await ctx.log("info",
                        f"[ten_half] player_joined: uid={aid}, name={aname}, "
                        f"via={'silent_debit' if g.bet > 0 and g.join_mode == JOIN_MODE_SILENT_DEBIT else 'button'}, "
                        f"chat_id={cid}, count={len(g.lobby_players)}/{g.max_players}")
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
        *,
        paid_exempt: bool = False,
        auto_debit: bool = False,
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
        g.paid_stakes[aid] = 0 if paid_exempt else g.bet
        if not is_callback:
            self._remember_player_message(g, aid, mid)
        if len(g.lobby_players) == 1:
            self._lock_first_dealer(g, aid, aname)
        self._touch_lobby(g, clear_status=g.awaiting_start_confirmation)
        await self._save_lobby_state(ctx, g)
        cnt = len(g.lobby_players)
        if is_callback:
            result: list[dict[str, Any]] = [_answer_action(payload, f"加入成功，牌桌 {g.game_id}")]
            if auto_debit:
                result.append(_debit_action(g, aid))
                result.extend(await self._join_notice_actions(
                    ctx,
                    payload,
                    g,
                    payer_name=aname,
                    amount=g.bet,
                ))
        else:
            result = await self._join_notice_actions(
                ctx,
                payload,
                g,
                payer_name=aname,
                amount=0 if paid_exempt else g.bet,
            )
        result.append(_session_sync_action(g, payload))

        if cnt >= g.max_players and g.dealer_locked:
            result.extend(await self._ix_begin(g.chat_id, g, g.dealer_id, g.dealer_name, ctx, payload=payload))
        else:
            self._schedule_lobby_timeout(g.chat_id, g, ctx)
            if g.dealer_locked:
                self._schedule_idle_start_prompt(g.chat_id, g, ctx)
            result.append(
                await self._main_action(
                    ctx,
                    g,
                    self._build_lobby_text(g, self._receiver_label(ctx, payload, g)),
                    reply_markup=_kb_join(g.bet, g.join_mode),
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
            return [_answer_action(payload, "只有庄家可以决定是否开局。", show_alert=True)]
        if g.phase != "lobby" or not g.dealer_locked:
            return [_answer_action(payload, "当前不能直接开局。", show_alert=True)]
        if len(g.lobby_players) < 2:
            return [_answer_action(payload, "至少需要 2 名玩家。", show_alert=True)]

        if action == "start_now":
            g.awaiting_start_confirmation = False
            return await self._ix_begin(g.chat_id, g, g.dealer_id, g.dealer_name, ctx, payload=payload)

        self._touch_lobby(g)
        g.status_note = f"{_display_name(g.dealer_name)} 选择继续等待后续玩家加入。"
        await self._save_lobby_state(ctx, g)
        self._schedule_lobby_timeout(g.chat_id, g, ctx)
        self._schedule_idle_start_prompt(g.chat_id, g, ctx)
        return [
            _answer_action(payload, "继续等待后续玩家加入。"),
            await self._main_action(
                ctx,
                g,
                self._build_lobby_text(g, self._receiver_label(ctx, payload, g)),
                reply_markup=_kb_join(g.bet, g.join_mode),
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
                parsed_uid = int(parts[2])
            except (ValueError, TypeError):
                parsed_uid = 0
            if parsed_uid > 0:
                target_uid = parsed_uid

        if cb_version is not None:
            current_version = _target_action_version(g, target_uid) if parsed_uid > 0 else _board_action_version(g)
        else:
            current_version = None
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
            return [_answer_action(payload or {}, "这不是你的操作按钮。", show_alert=True)] if payload else []

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
                if not _consume_action_click(g, target_uid, action):
                    return [_answer_action(payload or {}, "操作太快了，请看最新牌桌。")] if payload else []
                return await self._ix_dealer_hit(g.chat_id, g, ctx, payload)
            if action == "stand":
                if not _consume_action_click(g, target_uid, action):
                    return [_answer_action(payload or {}, "操作太快了，请看最新牌桌。")] if payload else []
                return await self._ix_dealer_stand(g.chat_id, g, ctx, payload)
            return [_answer_action(payload or {}, "庄家不能加倍。")] if payload else []

        cur = self._find_player(g, target_uid)
        if cur is None:
            return [_answer_action(payload or {}, "你不在本轮付费玩家列表中。", show_alert=True)] if payload else []
        if action == "view":
            return [_answer_action(payload or {}, f"你的手牌：{cur.hand_str()}", show_alert=True)] if payload else []
        if cur.is_done:
            return [_answer_action(payload or {}, "你本轮已经结束。")] if payload else []
        if action == "hit":
            if not _consume_action_click(g, target_uid, action):
                return [_answer_action(payload or {}, "操作太快了，请看最新牌桌。")] if payload else []
            return await self._ix_hit(g.chat_id, g, ctx, payload, player=cur)
        elif action == "stand":
            if not _consume_action_click(g, target_uid, action):
                return [_answer_action(payload or {}, "操作太快了，请看最新牌桌。")] if payload else []
            return await self._ix_stand(g.chat_id, g, ctx, payload, player=cur)
        elif action == "double":
            if not _consume_action_click(g, target_uid, action):
                return [_answer_action(payload or {}, "操作太快了，请看最新牌桌。")] if payload else []
            return await self._ix_double(g.chat_id, g, ctx, payload, player=cur)
        return []

    # ── 交互：消息处理 ──────────────────────────────
    async def _ix_message(
        self, ctx: PluginContext, payload: dict[str, Any], cid: int,
    ) -> list[dict[str, Any]]:
        if _is_userbot_message(payload):
            return []
        text = _ie_text(payload)
        if not text:
            return []
        mid = _ie_mid(payload)
        aid, aname = _ie_actor(payload)
        is_owner = _is_payload_userbot_actor(payload, aid)

        requested_join_mode = _join_mode_from_command_text(text, self._join_mode)
        if requested_join_mode is not None and is_owner:
            previous_mode = self._join_mode
            await self._save_join_mode(ctx, requested_join_mode)
            active_game = self._games.get(cid)
            suffix = "；当前已有牌桌，本次切换从下一局开始生效。" if active_game and not active_game.finished else "。"
            if mid:
                self._schedule_transient_userbot_delete(ctx, cid, message_id=mid)
            result_key = _transient_userbot_msg_key(ctx.account_id, cid, "mode_result")
            self._schedule_transient_userbot_delete(ctx, cid, message_key=result_key)
            if ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] join_mode_changed: from={previous_mode}, to={requested_join_mode}, "
                    f"by={aname} ({aid}), chat_id={cid}",
                )
            return [
                _send_action(
                    f"十点半入局模式已切换为 <b>{_join_mode_label(requested_join_mode)}</b>{suffix}",
                    reply_to_message_id=mid,
                    save_message_id_key=result_key,
                )
            ]

        async with self._lock(cid):
            g = self._games.get(cid)
            if not g or g.finished:
                return [{"type": "no_session"}]

            # ── 大厅 ──
            if g.phase == "lobby":
                if text == "入局" and is_owner:
                    if mid:
                        self._schedule_transient_userbot_delete(ctx, cid, message_id=mid)
                    result = await self._ix_join(ctx, payload, g, aid, aname, paid_exempt=True)
                    if ctx.log:
                        await ctx.log("info",
                            f"[ten_half] player_joined: uid={aid}, name={aname}, "
                            f"via=owner_entry, chat_id={cid}")
                    return result
                if text in ("加入", "join", "入局"):
                    return []
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
        await self._delete_lobby_state(ctx, g)
        g.deck = create_deck()
        g.dealer_cards.clear()
        g.players.clear()
        g.dealer_stood = False
        g.action_versions.clear()
        g.timeout_versions.clear()
        g.status_note = ""

        for uid, name in g.lobby_players:
            if uid != dealer_id:
                g.players.append(PlayerHand(user_id=uid, name=name, stake=int(g.paid_stakes.get(uid) or g.bet)))

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
        g.status_note = f"{_display_name(g.dealer_name)} 当庄，每人起手 1 张；庄家首牌暗牌。所有人共用下方按钮，系统按点击者识别自己的手牌。"
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
        public_points = _dealer_public_action_points(g)
        if g.dealer_busted():
            g.status_note = f"{_display_name(g.dealer_name)} 已要牌，当前 {len(g.dealer_cards)}张，明牌 {public_points}点。"
        if g.dealer_five_small():
            g.dealer_stood = True
            g.status_note = f"{_display_name(g.dealer_name)} 已停牌，当前 {len(g.dealer_cards)}张，明牌 {public_points}点。"
        if not g.dealer_busted() and not g.dealer_five_small():
            g.status_note = f"{_display_name(g.dealer_name)} 已要牌，当前 {len(g.dealer_cards)}张，明牌 {public_points}点。"
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
        g.status_note = f"{_display_name(g.dealer_name)} 停牌，共 {len(g.dealer_cards)}张，明牌 {_dealer_public_action_points(g)}点。"
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
            g.status_note = "等待操作：" + "、".join(_display_name(name) for name in active_names)

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
            g.status_note = f"{_display_name(p.name)} 要牌后爆牌，自动结束本回合。"
        elif p.is_five_small:
            p.stood = True
            g.status_note = f"{_display_name(p.name)} 五小，自动停牌。"
        else:
            g.status_note = f"{_display_name(p.name)} 已要牌，当前 {_cards_brief(p.cards)}。"

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
        g.status_note = f"{_display_name(p.name)} 停牌，{_cards_brief(p.cards)}。"
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
                return [_answer_action(payload, "加倍只能在已有 2 张牌时使用。")]
            return [_send_action("⚠️ 加倍只能在已有 2 张牌时使用。")]
        if g.join_mode != JOIN_MODE_SILENT_DEBIT:
            if payload is not None:
                return [_answer_action(payload, "转账模式不会自动扣款，本局暂不支持按钮加倍；可切换无感模式后再开局。", show_alert=True)]
            return [_send_action("⚠️ 转账模式不会自动扣款，本局暂不支持按钮加倍。")]

        current_stake = int(p.stake or g.paid_stakes.get(p.user_id) or g.bet)
        p.stake = current_stake + g.bet
        g.paid_stakes[p.user_id] = p.stake
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
                f"stake={p.stake}, busted={p.value > 10.5 + 1e-9}, chat_id={cid}")

        actions: list[dict[str, Any]] = []
        if payload is not None:
            actions.append(_answer_action(payload, f"加倍扣款 {g.bet}，要到 {card.display()}，当前 {_fv(p.value)}点。"))
        debit_key = _transient_userbot_msg_key(ctx.account_id, g.chat_id, f"double_{p.user_id}")
        debit_action = _debit_action(g, p.user_id, amount=g.bet)
        debit_action["save_message_id_key"] = debit_key
        self._schedule_transient_userbot_delete(ctx, g.chat_id, message_key=debit_key)
        actions.append(debit_action)
        if p.value > 10.5 + 1e-9:
            p.busted = True
            g.status_note = f"{_display_name(p.name)} 加倍后爆牌，下注按 {p.stake} 计算。"
        else:
            p.stood = True
            g.status_note = f"{_display_name(p.name)} 加倍后停牌，下注按 {p.stake} 计算。"

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
            g.status_note = f"所有玩家都爆牌，{_display_name(g.dealer_name)} 自动获胜。"
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
                g.status_note = f"{_display_name(g.dealer_name)}{suffix} 后爆牌。"
            else:
                suffix = f"，要牌 {'、'.join(draw_notes)}" if draw_notes else ""
                g.status_note = f"{_display_name(g.dealer_name)}{suffix} 后停牌（{_fv(g.dealer_val())}点）。"

        actions: list[dict[str, Any]] = []
        actions.extend(await self._ix_settle(cid, g, ctx))
        return actions

    # ── 交互：结算 ──────────────────────────────────
    async def _ix_settle(self, cid: int, g: TenHalfGame, ctx: PluginContext | None = None) -> list[dict[str, Any]]:
        """结算：每个闲家独立对庄家结算，payout 始终由 userbot 发放。"""
        g.phase = "finished"
        g.finished = True
        dv = g.dealer_val()
        db = g.dealer_busted()
        dn = g.dealer_natural()
        dfs = g.dealer_five_small()

        # 入池金额展示真实已支付 stake；庄家补扣按倍率奖金毛额算，刷屏费只从赢家奖金里扣。
        dealer_bet = int(g.paid_stakes.get(g.dealer_id) or (g.bet if g.dealer_id else 0))
        total_paid = dealer_bet + sum(int(p.stake or g.paid_stakes.get(p.user_id) or g.bet) for p in g.players)
        fee_percent = max(0, min(100, int(g.service_fee_percent)))
        fee_numerator = max(0, 100 - fee_percent)

        # ── 结算明细 ──
        lines = [
            f"🏆 <b>十点半结算 · 牌桌 <code>{g.game_id}</code></b>",
            f"💰 总入池金额: <b>{total_paid}</b>",
            f"🎰 庄家 <b>{_html_name(g.dealer_name)}</b>: {_dealer_public_brief(g, reveal=True)}",
            "",
            "👥 玩家",
        ]

        player_results: list[dict[str, Any]] = []
        winners: list[dict[str, Any]] = []
        losing_stake_total = 0

        def payout_multiplier(outcome: str) -> float:
            if outcome == "win_5s":
                return 3.0
            if outcome == "win":
                return 1.0
            return 0.0

        for p in g.players:
            eb = int(p.stake or g.paid_stakes.get(p.user_id) or g.bet)
            outcome = self._compare(p, dv, db, dn, dfs)
            win_multiplier = payout_multiplier(outcome)
            gross_win = int(eb * win_multiplier) if win_multiplier > 0 else 0
            fee_amount = gross_win * fee_percent // 100 if gross_win > 0 else 0

            # 赢家拿回本金；倍率奖金扣刷屏费，庄家补扣按未扣费前的倍率奖金毛额计算。
            reward = eb + gross_win * fee_numerator // 100 if gross_win > 0 else 0
            loss = eb if outcome == "lose" else 0
            if loss > 0:
                losing_stake_total += loss

            # 显示文案
            outcome_display = self._settlement_outcome_text(
                p,
                outcome,
                eb,
                reward,
                loss,
                html_mode=True,
            )

            lines.append(f"• <b>{_html_name(p.name)}</b>: {_cards_brief(p.cards)} → {outcome_display}")

            pr = {
                "user_id": p.user_id,
                "name": p.name,
                "outcome": outcome,
                "multiplier": win_multiplier,
                "reward": reward,
                "loss": loss,
                "bet": eb,
                "gross_win": gross_win,
                "service_fee": fee_amount,
            }
            player_results.append(pr)
            if reward > 0:
                winners.append(pr)

            if ctx and ctx.log:
                await ctx.log("info",
                    f"[ten_half] settlement: uid={p.user_id}, name={p.name}, "
                    f"outcome={outcome}, multiplier={win_multiplier}, reward={reward}, "
                    f"gross_win={gross_win}, service_fee={fee_amount}, "
                    f"loss={loss}, bet={eb}, total_paid={total_paid}, chat_id={cid}")

        dealer_reward = (
            dealer_bet + losing_stake_total * fee_numerator // 100
            if g.dealer_id and g.players and losing_stake_total > 0 and not winners
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
                "gross_win": losing_stake_total,
                "service_fee": losing_stake_total * fee_percent // 100,
            }
            winners.append(dealer_result)
            player_results.append(dealer_result)
            lines.extend([
                "",
                f"🎰 庄家 <b>{_html_name(g.dealer_name)}</b> 🎉是赢家 获得 <b>{dealer_reward}</b>",
            ])
            if ctx and ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] dealer_reward: uid={g.dealer_id}, name={g.dealer_name}, "
                    f"amount={dealer_reward}, bet={dealer_bet}, total_paid={total_paid}, chat_id={cid}",
                )

        non_dealer_reward_total = sum(
            int(w["reward"])
            for w in winners
            if int(w.get("user_id") or 0) != int(g.dealer_id or 0)
        )
        dealer_gross_liability = sum(
            int(w.get("gross_win") or 0)
            for w in winners
            if int(w.get("user_id") or 0) != int(g.dealer_id or 0)
        )
        dealer_top_up = (
            max(0, dealer_gross_liability - dealer_bet)
            if int(g.dealer_id or 0) > 0 and dealer_gross_liability > 0
            else 0
        )
        if dealer_top_up > 0:
            lines.extend([
                "",
                f"💳 庄家 <b>{_html_name(g.dealer_name)}</b> 补扣 <b>{dealer_top_up}</b>",
            ])
            if ctx and ctx.log:
                await ctx.log(
                    "info",
                    f"[ten_half] dealer_top_up: uid={g.dealer_id}, name={g.dealer_name}, "
                    f"amount={dealer_top_up}, dealer_bet={dealer_bet}, "
                    f"dealer_gross_liability={dealer_gross_liability}, "
                    f"winner_rewards={non_dealer_reward_total}, total_paid={total_paid}, chat_id={cid}",
                )
        elif dealer_gross_liability > dealer_bet and ctx and ctx.log:
            await ctx.log(
                "warn",
                f"[ten_half] dealer_top_up_skipped: no_dealer_id, "
                f"dealer_gross_liability={dealer_gross_liability}, "
                f"dealer_bet={dealer_bet}, total_paid={total_paid}, chat_id={cid}",
            )

        actions: list[dict[str, Any]] = []
        reward_message_keys: list[str] = []

        settlement_message_key = (
            _settlement_msg_key(ctx.account_id, cid, g.game_id)
            if ctx is not None
            else None
        )

        # ── 结算公告固定由交互 Bot 发送 ──
        settlement_action = _send_action(
            "\n".join(lines),
            save_message_id_key=settlement_message_key if ctx is not None else None,
        )
        settlement_action["chat_id"] = cid
        actions.append(settlement_action)

        if dealer_top_up > 0 and int(g.dealer_id or 0) > 0:
            dealer_top_up_key = (
                _transient_userbot_msg_key(ctx.account_id, cid, f"dealer_topup_{g.dealer_id}")
                if ctx is not None
                else ""
            )
            top_up_action = _debit_action(
                g,
                int(g.dealer_id),
                amount=dealer_top_up,
                reply_to_message_id=self._player_reply_message(g, int(g.dealer_id)),
                reply_anchor_missing_text="无法自动补扣庄家，请人工处理。",
                suppress_reply_anchor_missing_notice=False,
            )
            if dealer_top_up_key:
                top_up_action["save_message_id_key"] = dealer_top_up_key
                reward_message_keys.append(dealer_top_up_key)
                self._schedule_transient_userbot_delete(ctx, cid, message_key=dealer_top_up_key)
            actions.append(top_up_action)

        # ── 向每位赢家发放奖励（payout 始终由 userbot 执行） ──
        for w in winners:
            winner_user_id = int(w["user_id"])
            reply_to = self._player_reply_message(g, winner_user_id)
            reward_key = _reward_msg_key(ctx.account_id, cid, g.game_id, winner_user_id) if ctx else ""
            if reward_key:
                reward_message_keys.append(reward_key)
            payout_action = {
                "type": "payout",
                "chat_id": cid,
                "amount": int(w["reward"]),
                "text": f"+{w['reward']}",
                "parse_mode": "plain",
                "reply_to_user_id": winner_user_id,
                "reply_to_search_limit": 50,
                **({"reply_to_message_id": reply_to} if reply_to else {}),
                **({"save_message_id_key": reward_key} if reward_key else {}),
            }
            actions.append(payout_action)
            if ctx and ctx.log:
                await ctx.log("info",
                    f"[ten_half] reward_sent: uid={w['user_id']}, name={w['name']}, "
                    f"amount={w['reward']}, reply_to_message_id={reply_to}, chat_id={cid}")

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
                    "total_pot": total_paid,
                    "total_paid": total_paid,
                    "winner_user_id": primary["user_id"],
                    "winner_name": primary["name"],
                    "winner_count": len(winners),
                    "players": player_results,
                    "payout_mode": "auto",
                    "dealer_top_up": dealer_top_up,
                    "dealer_gross_liability": dealer_gross_liability,
                    "service_fee_percent": fee_percent,
                },
                "settlement": {
                    "mode": "auto",
                    "amount": primary["reward"],
                    "winner_user_id": primary["user_id"],
                    "winner_name": primary["name"],
                    "payout_account_label": "管理员",
                    "status": "payout_requested",
                    "dealer_top_up": dealer_top_up,
                    "dealer_gross_liability": dealer_gross_liability,
                    "service_fee_percent": fee_percent,
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
                    "total_pot": total_paid,
                    "total_paid": total_paid,
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
