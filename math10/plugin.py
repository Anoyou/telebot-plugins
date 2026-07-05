"""交互 Bot 随机算数题插件。"""

from __future__ import annotations

import json
import random
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.plugins.events import event_from_interaction_payload

MATH10_GAME_PREFIX = "account_bot:math10:"
MATH10_CLAIM_PREFIX = "account_bot:math10_claim:"
MESSAGE_ID_NAMESPACE_PREFIX = "tp:msgid"
DEFAULT_PRIZE = 123
DEFAULT_TTL_SECONDS = 900
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 86400


@dataclass
class Math10GameState:
    account_id: int
    chat_id: int
    question: str
    answer: int
    prize: int = DEFAULT_PRIZE
    active: bool = True
    game_id: str = ""
    created_at: float = 0.0
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    source_update_id: int | None = None
    source_message_id: int | None = None
    winner_update_id: int | None = None
    winner_message_id: int | None = None


def _new_math_question() -> tuple[str, int]:
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    op = random.choice(["+", "-", "x"])
    if op == "+":
        return f"{a} + {b}", a + b
    if op == "-":
        high, low = max(a, b), min(a, b)
        return f"{high} - {low}", high - low
    return f"{a} x {b}", a * b


def _int_payload(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any, default: int) -> int:
    parsed = _int_payload(value)
    if parsed is None or parsed <= 0:
        return default
    return parsed


def _ttl_from_payload(payload: dict[str, Any]) -> int:
    ttl = _positive_int(payload.get("valid_seconds"), DEFAULT_TTL_SECONDS)
    return min(max(ttl, MIN_TTL_SECONDS), MAX_TTL_SECONDS)


def _game_key(account_id: int, chat_id: int) -> str:
    return f"{MATH10_GAME_PREFIX}{int(account_id)}:{int(chat_id)}"


def _claim_key(state: Math10GameState) -> str:
    return f"{MATH10_CLAIM_PREFIX}{state.account_id}:{state.chat_id}:{state.game_id}"


def _question_message_key(state: Math10GameState) -> str:
    return f"math10:{state.account_id}:{state.chat_id}:{state.game_id}:question"


def _saved_message_redis_key(account_id: int, save_key: str) -> str:
    return f"{MESSAGE_ID_NAMESPACE_PREFIX}:{int(account_id)}:{save_key}"


def _state_from_payload(payload: Any) -> Math10GameState | None:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    try:
        return Math10GameState(
            account_id=int(payload["account_id"]),
            chat_id=int(payload["chat_id"]),
            question=str(payload["question"]),
            answer=int(payload["answer"]),
            prize=int(payload.get("prize") or DEFAULT_PRIZE),
            active=bool(payload.get("active", True)),
            game_id=str(payload.get("game_id") or ""),
            created_at=float(payload.get("created_at") or 0.0),
            ttl_seconds=_ttl_from_payload({"valid_seconds": payload.get("ttl_seconds")}),
            source_update_id=_int_payload(payload.get("source_update_id")),
            source_message_id=_int_payload(payload.get("source_message_id")),
            winner_update_id=_int_payload(payload.get("winner_update_id")),
            winner_message_id=_int_payload(payload.get("winner_message_id")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _state_expired(state: Math10GameState) -> bool:
    if not state.active:
        return False
    if state.created_at <= 0:
        return True
    return time.time() >= state.created_at + _ttl_from_payload({"valid_seconds": state.ttl_seconds})


def _event_dict(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    return event if isinstance(event, dict) else {}


def _payload_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _interaction_event_type(payload: dict[str, Any], event: dict[str, Any]) -> str:
    source = _payload_dict(payload, "source")
    trigger = _payload_dict(payload, "trigger")
    return str(
        source.get("type")
        or trigger.get("type")
        or event.get("type")
        or payload.get("event_type")
        or ""
    )


def _interaction_chat_id(payload: dict[str, Any], event: dict[str, Any]) -> int | None:
    source = _payload_dict(payload, "source")
    return _int_payload(payload.get("chat_id") or source.get("chat_id") or event.get("chat_id"))


def _interaction_message_text(payload: dict[str, Any], event: dict[str, Any]) -> str:
    source = _payload_dict(payload, "source")
    return str(payload.get("message_text") or source.get("text") or event.get("text") or "").strip()


def _interaction_message_id(payload: dict[str, Any], event: dict[str, Any]) -> int | None:
    source = _payload_dict(payload, "source")
    return _int_payload(payload.get("message_id") or source.get("message_id") or event.get("message_id"))


def _interaction_update_id(payload: dict[str, Any], event: dict[str, Any]) -> int | None:
    source = _payload_dict(payload, "source")
    return _int_payload(payload.get("source_update_id") or source.get("update_id") or event.get("update_id"))


def _interaction_actor_name(payload: dict[str, Any], event: dict[str, Any]) -> str:
    actor = _payload_dict(payload, "actor")
    return str(
        payload.get("sender_name")
        or actor.get("display_name")
        or event.get("display_name")
        or payload.get("payer_name")
        or "未知用户"
    )


def _short_actor_name(name: str) -> str:
    compact = " ".join(str(name or "").strip().split())
    return (compact or "未知用户")[:10]


def _interaction_payout_info(payload: dict[str, Any]) -> tuple[str, str]:
    settlement = _payload_dict(payload, "settlement")
    payout_account = str(
        payload.get("payout_account_label")
        or settlement.get("payout_account_label")
        or "账号持有者"
    ).strip()
    payout_mode = str(payload.get("payout_mode") or settlement.get("mode") or "manual").strip().lower()
    return payout_account, payout_mode


def _render_payout_notice(payout_mode: str, payout_account_display: str) -> str:
    return ""


async def _log(ctx: PluginContext, level: str, message: str, **detail: Any) -> None:
    if ctx.log is None:
        return
    await ctx.log(level, message, **detail)


async def _interaction_send(
    ctx: PluginContext,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    save_message_id_key: str | None = None,
) -> list[dict[str, Any]]:
    if ctx.messages is not None:
        await ctx.messages.send(
            text=text,
            reply_to_message_id=reply_to_message_id,
            save_message_id_key=save_message_id_key,
        )
        return []
    action: dict[str, Any] = {"type": "send_message", "text": text}
    if reply_to_message_id is not None:
        action["reply_to_message_id"] = reply_to_message_id
    if save_message_id_key:
        action["save_message_id_key"] = save_message_id_key
    return [action]


async def _interaction_edit(
    ctx: PluginContext,
    text: str,
    *,
    chat_id: int,
    message_id: int,
) -> list[dict[str, Any]]:
    if ctx.messages is not None:
        await ctx.messages.edit(chat_id=chat_id, message_id=message_id, text=text)
        return []
    return [{"type": "edit_message", "chat_id": chat_id, "message_id": message_id, "text": text}]


@register
class Math10Plugin(Plugin):
    """交互 Bot 随机算数题插件。"""

    key = "math10"
    display_name = "随机算数题"
    message_channels: set[str] = set()
    owner_only = False

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key not in {"start_math_game", "start_math10"}:
            return None
        framework_event = event_from_interaction_payload(payload)
        event = _event_dict(payload)
        event_type = framework_event.type or _interaction_event_type(payload, event)
        chat_id = framework_event.message.chat_id or _interaction_chat_id(payload, event)
        if chat_id is None:
            return []
        if event_type in {"payment_confirmed", "keyword"}:
            return await self._handle_start(ctx, payload, event, chat_id)
        if event_type == "message":
            return await self._handle_answer(ctx, payload, event, chat_id)
        if event_type == "session_close":
            return await self._handle_close(ctx, chat_id)
        return []

    async def _handle_start(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        event: dict[str, Any],
        chat_id: int,
    ) -> list[dict[str, Any]]:
        active = await self._load_state(ctx, ctx.account_id, chat_id)
        if active is not None and active.active:
            return await _interaction_send(ctx, "当前已有进行中的算数题，请先答完再开新局。")

        question, answer = _new_math_question()
        prize = _positive_int(payload.get("prize"), DEFAULT_PRIZE)
        ttl = _ttl_from_payload(payload)
        state = Math10GameState(
            account_id=ctx.account_id,
            chat_id=chat_id,
            question=question,
            answer=answer,
            prize=prize,
            game_id=secrets.token_hex(8),
            created_at=time.time(),
            ttl_seconds=ttl,
            source_update_id=_interaction_update_id(payload, event),
            source_message_id=_interaction_message_id(payload, event),
        )
        await self._save_state(ctx, state)
        await _log(
            ctx,
            "info",
            f"随机算数题交互 Bot 开局：聊天 {chat_id}，题目 {question}，奖金 {prize}。",
            chat_id=chat_id,
            question=question,
            answer=answer,
            prize=prize,
            ttl_seconds=ttl,
            game_id=state.game_id,
        )
        return await _interaction_send(
            ctx,
            self._render_start_message(question, prize),
            save_message_id_key=_question_message_key(state),
        )

    async def _handle_answer(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        event: dict[str, Any],
        chat_id: int,
    ) -> list[dict[str, Any]]:
        state = await self._load_state(ctx, ctx.account_id, chat_id)
        if state is None or not state.active:
            return []

        raw_answer = _interaction_message_text(payload, event)
        try:
            answer = int(raw_answer)
        except ValueError:
            return []
        if answer != state.answer:
            return []
        if not await self._claim_winner(ctx, state, payload, event):
            return []

        winner = _interaction_actor_name(payload, event)
        actor = _payload_dict(payload, "actor")
        winner_user_id = _int_payload(actor.get("user_id"))
        payout_account, payout_mode = _interaction_payout_info(payload)
        winner_display = _short_actor_name(winner)
        reply_to_message_id = _interaction_message_id(payload, event)
        await _log(
            ctx,
            "info",
            f"随机算数题交互 Bot 识别到正确答案：{winner} 答对 {state.question}。",
            chat_id=chat_id,
            winner=winner,
            answer=answer,
            prize=state.prize,
            winner_message_id=reply_to_message_id,
        )
        result_text = self._render_success_message(state, winner_display)
        question_message_id = await self._read_question_message_id(ctx, state)
        if question_message_id is not None:
            message_actions = await _interaction_edit(
                ctx,
                result_text,
                chat_id=chat_id,
                message_id=question_message_id,
            )
        else:
            message_actions = await _interaction_send(ctx, result_text, reply_to_message_id=reply_to_message_id)
        return [
            *message_actions,
            {
                "type": "payout",
                "chat_id": chat_id,
                "amount": state.prize,
                "text": f"+{state.prize}",
                "parse_mode": "plain",
                "reply_to_message_id": reply_to_message_id,
                **({"reply_to_user_id": winner_user_id, "reply_to_search_limit": 50} if winner_user_id is not None else {}),
            },
            {
                "type": "result",
                "success": True,
                "result": {
                    "status": "winner",
                    "winner_user_id": winner_user_id,
                    "winner_name": winner,
                    "winner_message_id": reply_to_message_id,
                    "question": state.question,
                    "answer": state.answer,
                    "prize": state.prize,
                    "payout_mode": payout_mode,
                    "payout_account_label": payout_account,
                },
                "settlement": {
                    "mode": "auto",
                    "amount": state.prize,
                    "winner_user_id": winner_user_id,
                    "winner_name": winner,
                    "payout_account_label": payout_account,
                    "status": "payout_requested",
                },
            },
            {"type": "end_session"},
        ]

    async def _handle_close(self, ctx: PluginContext, chat_id: int) -> list[dict[str, Any]]:
        state = await self._load_state(ctx, ctx.account_id, chat_id, allow_expired=True)
        if ctx.redis is not None:
            await ctx.redis.delete(_game_key(ctx.account_id, chat_id))
            if state is not None and state.game_id:
                await ctx.redis.delete(_claim_key(state))
        await _log(ctx, "info", f"随机算数题交互 Bot 会话已清理：聊天 {chat_id}。", chat_id=chat_id)
        return []

    async def _load_state(
        self,
        ctx: PluginContext,
        account_id: int,
        chat_id: int,
        *,
        allow_expired: bool = False,
    ) -> Math10GameState | None:
        if ctx.redis is None:
            return None
        raw = await ctx.redis.get(_game_key(account_id, chat_id))
        state = _state_from_payload(raw)
        if state is not None and not allow_expired and _state_expired(state):
            state.active = False
            await self._save_state(ctx, state)
            return None
        return state

    async def _save_state(self, ctx: PluginContext, state: Math10GameState) -> None:
        if ctx.redis is None:
            return
        ttl = _ttl_from_payload({"valid_seconds": state.ttl_seconds})
        await ctx.redis.set(
            _game_key(state.account_id, state.chat_id),
            json.dumps(asdict(state), ensure_ascii=False),
            ex=ttl,
        )

    async def _claim_winner(
        self,
        ctx: PluginContext,
        state: Math10GameState,
        payload: dict[str, Any],
        event: dict[str, Any],
    ) -> bool:
        if ctx.redis is None:
            return False
        acquired = await ctx.redis.set(
            _claim_key(state),
            str(_interaction_message_id(payload, event) or _interaction_update_id(payload, event) or ""),
            ex=_ttl_from_payload({"valid_seconds": state.ttl_seconds}),
            nx=True,
        )
        if not acquired:
            return False
        state.active = False
        state.winner_update_id = _interaction_update_id(payload, event)
        state.winner_message_id = _interaction_message_id(payload, event)
        await self._save_state(ctx, state)
        return True

    async def _read_question_message_id(self, ctx: PluginContext, state: Math10GameState) -> int | None:
        if ctx.redis is None:
            return None
        save_key = _question_message_key(state)
        try:
            raw = await ctx.redis.get(_saved_message_redis_key(state.account_id, save_key))
            if raw is None:
                raw = await ctx.redis.get(save_key)
        except Exception:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return _int_payload(raw)

    @staticmethod
    def _render_start_message(question: str, prize: int) -> str:
        return (
            "算数题测试开始\n"
            f"题目：{question} = ?\n"
            f"奖金：{prize}\n"
            "直接发送数字答案，答对后我会公告赢家。"
        )

    @staticmethod
    def _render_success_message(state: Math10GameState, winner_display: str) -> str:
        return (
            "本题结束\n"
            f"题目：{state.question} = ? 答案是 {state.answer}。\n"
            f"奖金：{state.prize}\n"
            f"你心里已经有答案了（{winner_display}）  答对了。"
        )


PLUGIN_CLASS = Math10Plugin

__all__ = [
    "MATH10_GAME_PREFIX",
    "MATH10_CLAIM_PREFIX",
    "Math10GameState",
    "Math10Plugin",
    "PLUGIN_CLASS",
    "_new_math_question",
]
