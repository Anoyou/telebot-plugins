"""骰子比大小远程插件。

玩法：
  ,dice          — 自己掷骰子看运气
  ,dice @某人    — 发起对战邀请
  ,dice accept   — 接受对战
  ,dice 数字     — 带下注的掷骰子
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
# 骰子动画符号
# ─────────────────────────────────────────────────────
DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]  # 1-6


def _roll_dice(count: int = 1) -> list[int]:
    return [random.randint(1, 6) for _ in range(count)]


def _format_dice(values: list[int]) -> str:
    return " ".join(DICE_FACES[v - 1] for v in values)


def _total(values: list[int]) -> int:
    return sum(values)


# ─────────────────────────────────────────────────────
# 游戏状态
# ─────────────────────────────────────────────────────
@dataclass
class DiceBattle:
    challenger_id: int = 0
    challenger_name: str = ""
    challenger_roll: list[int] = field(default_factory=list)
    opponent_id: int = 0
    opponent_name: str = ""
    opponent_roll: list[int] = field(default_factory=list)
    bet: int = 0
    dice_count: int = 2
    started_at: float = 0.0
    phase: str = "waiting"  # waiting / rolling / finished
    message_id: int | None = None


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
class DiceBattlePlugin(Plugin):
    key = "dice_battle"
    display_name = "骰子比大小"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "dice"
        self._timeout = 60
        self._battles: dict[int, DiceBattle] = {}
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
        self._command = cfg.get("command", "dice")
        self._timeout = cfg.get("timeout", 60)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[dice_battle] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._battles.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[dice_battle] 已停止")

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_dice_battle":
            return None
        event_type = _interaction_event_type(payload)
        chat_id = _interaction_chat_id(payload)
        if not chat_id:
            return [{"type": "send_message", "text": "❌ 骰子对战需要在群聊里使用。"}]
        if event_type in {"payment_confirmed", "keyword"}:
            return await self._interaction_start(ctx, payload, chat_id)
        if event_type == "message":
            return await self._interaction_accept(payload, chat_id)
        if event_type == "session_close":
            async with self._get_lock(chat_id):
                self._battles.pop(chat_id, None)
            return [{"type": "end_session"}]
        return []

    async def _interaction_start(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        challenger_id, challenger_name = _interaction_actor(payload)
        bet = _positive_int(payload.get("prize") or payload.get("bet") or _interaction_amount(payload), 0, minimum=0)
        dice_count = _positive_int(payload.get("dice_count"), 2, minimum=1)
        dice_count = min(dice_count, 6)
        timeout = _positive_int(payload.get("timeout") or payload.get("valid_seconds"), self._timeout, minimum=10)
        async with self._get_lock(chat_id):
            if chat_id in self._battles and self._battles[chat_id].phase == "waiting":
                return [{"type": "send_message", "text": "🎲 当前聊天已有等待中的骰子对战。", "reply_to_message_id": _interaction_message_id(payload)}]
            battle = DiceBattle(
                challenger_id=challenger_id,
                challenger_name=challenger_name,
                bet=bet,
                dice_count=dice_count,
                started_at=time.monotonic(),
                phase="waiting",
            )
            self._battles[chat_id] = battle
        self._track_task(asyncio.create_task(self._battle_timeout(chat_id, ctx, battle.started_at, timeout)))
        bet_text = f" · 奖励 +{bet}" if bet > 0 else ""
        return [
            {
                "type": "send_message",
                "text": (
                    f"<b>🎲 骰子比大小</b>{bet_text}\n\n"
                    f"{challenger_name} 发起挑战。首个发送 <code>accept</code> / <code>接受</code> 的玩家应战。\n"
                    f"每人 {dice_count} 颗骰子，邀请有效期 {timeout} 秒。"
                ),
                "parse_mode": "html",
                "reply_to_message_id": _interaction_message_id(payload),
            }
        ]

    async def _interaction_accept(self, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        text = _interaction_message_text(payload).lower()
        if text not in {"accept", "接受", "应战"}:
            return []
        actor_id, actor_name = _interaction_actor(payload)
        async with self._get_lock(chat_id):
            battle = self._battles.get(chat_id)
            if not battle or battle.phase != "waiting":
                return [{"type": "no_session"}]
            if actor_id == battle.challenger_id:
                return [{"type": "send_message", "text": "不能跟自己对战啦。", "reply_to_message_id": _interaction_message_id(payload)}]
            battle.opponent_id = actor_id
            battle.opponent_name = actor_name
            battle.challenger_roll = _roll_dice(battle.dice_count)
            battle.opponent_roll = _roll_dice(battle.dice_count)
            battle.phase = "finished"
            self._battles.pop(chat_id, None)

        c_total = _total(battle.challenger_roll)
        o_total = _total(battle.opponent_roll)
        winner_id = 0
        winner_name = ""
        if c_total > o_total:
            winner_id = battle.challenger_id
            winner_name = battle.challenger_name
            result = f"🎉 {battle.challenger_name} 获胜！"
        elif o_total > c_total:
            winner_id = battle.opponent_id
            winner_name = battle.opponent_name
            result = f"🎉 {battle.opponent_name} 获胜！"
        else:
            result = "🤝 平局！"
        bet_text = f"\n💰 {winner_name} 赢得 +{battle.bet}" if battle.bet > 0 and winner_name else ""
        actions: list[dict[str, Any]] = [
            {
                "type": "send_message",
                "text": (
                    f"<b>🎲 骰子对战结果！</b>\n\n"
                    f"{battle.challenger_name}：{_format_dice(battle.challenger_roll)} = {c_total} 点\n"
                    f"{battle.opponent_name}：{_format_dice(battle.opponent_roll)} = {o_total} 点\n\n"
                    f"{result}{bet_text}"
                ),
                "parse_mode": "html",
                "reply_to_message_id": _interaction_message_id(payload),
            }
        ]
        if battle.bet > 0 and winner_id:
            actions.extend(
                [
                    {"type": "send_message", "text": f"+{battle.bet}", "reply_to_message_id": _interaction_message_id(payload)},
                    {
                        "type": "result",
                        "success": True,
                        "result": {"winner_user_id": winner_id, "winner_name": winner_name, "amount": battle.bet},
                        "settlement": {"mode": "announce_only", "winner_user_id": winner_id, "winner_name": winner_name, "amount": battle.bet, "amount_field": "prize"},
                    },
                ]
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

        sender = await event.get_sender()
        sender_id = int(getattr(sender, "id", 0) or 0)
        sender_name = public_entity_display_name(sender, default="玩家")

        arg_str = " ".join(args).strip().lower()

        lock = self._get_lock(chat_id)

        # accept 接受对战
        if arg_str in ("accept", "接受", "应战"):
            async with lock:
                return await self._accept_battle(chat_id, sender_id, sender_name, event, ctx)

        # 解析下注和骰子数
        bet = 0
        dice_count = 2
        target_id: int | None = None

        for a in args:
            a = a.strip()
            # @某人 (reply 消息)
            if a.startswith("@"):
                continue  # @用户名暂不解析，用回复消息
            # 数字 → 下注
            if a.isdigit():
                n = int(a)
                if 1 <= n <= 6:
                    dice_count = n
                else:
                    bet = max(0, min(1000, n))

        # 检查是否回复了某人 → 发起对战
        reply_msg = await event.get_reply_message()
        if reply_msg:
            reply_sender = await reply_msg.get_sender()
            if reply_sender:
                target_id = int(getattr(reply_sender, "id", 0) or 0)
                target_name = public_entity_display_name(reply_sender, default="对手")

                if target_id == sender_id:
                    await event.reply("不能跟自己对战啦~", parse_mode="html")
                    return

                # 发起对战
                battle = DiceBattle(
                    challenger_id=sender_id,
                    challenger_name=sender_name,
                    opponent_id=target_id,
                    opponent_name=target_name,
                    bet=bet,
                    dice_count=dice_count,
                    started_at=time.monotonic(),
                    phase="waiting",
                )
                async with lock:
                    self._battles[chat_id] = battle

                bet_text = f" 下注 {bet} 筹码" if bet > 0 else ""
                await event.reply(
                    f"<b>🎲 骰子对战邀请！</b>{bet_text}\n\n"
                    f"{sender_name} 向 {target_name} 发起挑战！\n"
                    f"每人 {dice_count} 颗骰子\n\n"
                    f"{target_name}，输入 ,{self._command} accept 应战！",
                    parse_mode="html",
                )
                self._track_task(asyncio.create_task(self._battle_timeout(chat_id, ctx, battle.started_at)))
                return

        # 没有回复 → 自己掷骰子
        roll = _roll_dice(dice_count)
        total = _total(roll)
        await event.reply(
            f"<b>🎲 掷骰子</b>\n\n"
            f"{_format_dice(roll)}\n"
            f"总计：<b>{total}</b> 点",
            parse_mode="html",
        )

    # ── 接受对战 ─────────────────────────────────────
    async def _accept_battle(
        self, chat_id: int, sender_id: int, sender_name: str, event: Any, ctx: PluginContext,
    ) -> None:
        battle = self._battles.get(chat_id)
        if not battle or battle.phase != "waiting":
            await event.reply("没有等待中的对战邀请~", parse_mode="html")
            return

        if sender_id != battle.opponent_id:
            await event.reply(
                f"这个邀请是给 {battle.opponent_name} 的哦~",
                parse_mode="html",
            )
            return

        # 双方掷骰子
        battle.challenger_roll = _roll_dice(battle.dice_count)
        battle.opponent_roll = _roll_dice(battle.dice_count)
        battle.phase = "finished"

        c_total = _total(battle.challenger_roll)
        o_total = _total(battle.opponent_roll)

        if c_total > o_total:
            winner = battle.challenger_name
            result = f"🎉 {battle.challenger_name} 获胜！"
        elif o_total > c_total:
            winner = battle.opponent_name
            result = f"🎉 {battle.opponent_name} 获胜！"
        else:
            winner = ""
            result = "🤝 平局！"

        bet_text = ""
        if battle.bet > 0 and winner:
            bet_text = f"\n💰 {winner} 赢得 {battle.bet} 筹码"

        await event.reply(
            f"<b>🎲 骰子对战结果！</b>\n\n"
            f"{battle.challenger_name}：{_format_dice(battle.challenger_roll)} = {c_total} 点\n"
            f"{battle.opponent_name}：{_format_dice(battle.opponent_roll)} = {o_total} 点\n\n"
            f"{result}{bet_text}",
            parse_mode="html",
        )
        self._battles.pop(chat_id, None)

    # ── 超时 ─────────────────────────────────────────
    async def _battle_timeout(self, chat_id: int, ctx: PluginContext, started_at: float, timeout: int | None = None) -> None:
        await asyncio.sleep(timeout or self._timeout)
        async with self._get_lock(chat_id):
            battle = self._battles.get(chat_id)
            if not battle or battle.phase != "waiting" or battle.started_at != started_at:
                return
            self._battles.pop(chat_id, None)
        if ctx.log:
            await ctx.log("info", f"[dice_battle] chat {chat_id} 对战邀请超时")

    # ── 消息钩子 ─────────────────────────────────────
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        pass


PLUGIN_CLASS = DiceBattlePlugin

__all__ = ["DiceBattlePlugin", "PLUGIN_CLASS"]
