"""猜数字远程插件。

群内模式：发起者设定范围 → 系统随机选数 → 群友轮流猜 → 提示大/小 → 猜中获胜。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from app.worker.command import current_command_prefix
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
# 游戏状态
# ─────────────────────────────────────────────────────
@dataclass
class GuessGame:
    target: int = 0
    low: int = 1
    high: int = 100
    prize: int = 0
    timeout: int = 300
    attempts: int = 0
    max_attempts: int = 0
    started_at: float = 0.0
    message_id: int | None = None
    finished: bool = False
    winner_name: str = ""
    winner_id: int = 0
    via_interaction: bool = False  # 是否通过交互bot发起
    history: list[str] = field(default_factory=list)  # "玩家名: 猜测值 → 大/小/中"


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
    return str(
        event.get("type")
        or trigger.get("event")
        or trigger.get("type")
        or source.get("event_type")
        or payload.get("event_type")
        or ""
    ).strip()


def _interaction_chat_id(payload: dict[str, Any]) -> int:
    event = _payload_event(payload)
    source = _payload_source(payload)
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    return _positive_int(payload.get("chat_id") or event.get("chat_id") or source.get("chat_id") or session.get("chat_id"), 0, minimum=-10**20)


def _interaction_message_id(payload: dict[str, Any]) -> int | None:
    event = _payload_event(payload)
    source = _payload_source(payload)
    reply_to = _payload_reply_to(payload)
    value = _positive_int(
        payload.get("message_id")
        or payload.get("source_message_id")
        or reply_to.get("message_id")
        or event.get("message_id")
        or source.get("message_id"),
        0,
    )
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
    raw_id = (
        actor.get("user_id")
        or actor.get("id")
        or payload.get("sender_user_id")
        or payload.get("payer_user_id")
        or event.get("user_id")
        or data.get("payer_user_id")
    )
    raw_name = (
        actor.get("display_name")
        or actor.get("name")
        or payload.get("sender_name")
        or payload.get("payer_name")
        or event.get("display_name")
        or data.get("payer_name")
        or "玩家"
    )
    return _positive_int(raw_id, 0, minimum=0), str(raw_name).strip() or "玩家"


# ─────────────────────────────────────────────────────
# 插件
# ─────────────────────────────────────────────────────
@register
class GuessNumberPlugin(Plugin):
    key = "guess_number"
    display_name = "猜数字"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command", "timeout"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "guess"
        self._timeout = 300
        self._games: dict[int, GuessGame] = {}
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
        self._command = cfg.get("command", "guess")
        self._timeout = cfg.get("timeout", 300)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[guess_number] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._games.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[guess_number] 已停止")

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_guess_number":
            return None
        event_type = _interaction_event_type(payload)
        chat_id = _interaction_chat_id(payload)
        if not chat_id:
            return [{"type": "send_message", "text": "❌ 猜数字需要在群聊里使用。"}]
        if event_type in {"payment_confirmed", "keyword"}:
            return await self._interaction_start(ctx, payload, chat_id)
        if event_type == "message":
            return await self._interaction_guess(ctx, payload, chat_id)
        if event_type == "session_close":
            async with self._get_lock(chat_id):
                self._games.pop(chat_id, None)
            return [{"type": "end_session"}]
        return []

    async def _interaction_start(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        chat_id: int,
    ) -> list[dict[str, Any]]:
        prize = _positive_int(payload.get("prize") or _interaction_amount(payload), 0, minimum=1)
        if prize <= 0:
            return [
                {
                    "type": "send_message",
                    "text": f"请指定奖励金额。例：{{prefix}}{self._command} 100",
                    "reply_to_message_id": _interaction_message_id(payload),
                },
                {"type": "end_session"},
            ]
        timeout = _positive_int(payload.get("timeout") or payload.get("valid_seconds"), self._timeout, minimum=10)
        low = _positive_int(payload.get("low"), 1, minimum=1)
        high = _positive_int(payload.get("high"), 100, minimum=2)
        if low >= high:
            low, high = 1, 100
        max_attempts = _positive_int(payload.get("max_attempts"), 0, minimum=0)
        target = random.randint(low, high)
        game = GuessGame(
            target=target,
            low=low,
            high=high,
            prize=prize,
            timeout=timeout,
            max_attempts=max_attempts,
            started_at=time.monotonic(),
            via_interaction=True,
        )
        async with self._get_lock(chat_id):
            current = self._games.get(chat_id)
            if current and not current.finished:
                return [
                    {
                        "type": "send_message",
                        "text": "🎯 当前聊天已有进行中的猜数字。",
                        "reply_to_message_id": _interaction_message_id(payload),
                    }
                ]
            self._games[chat_id] = game

        self._track_task(asyncio.create_task(self._auto_timeout(chat_id, ctx, game.started_at, timeout)))
        limit_hint = f"（最多 {max_attempts} 次）" if max_attempts else ""
        return [
            {
                "type": "send_message",
                "text": (
                    f"<b>🔢 猜数字</b>\n\n"
                    f"奖励：<b>+{prize}</b>\n"
                    f"范围：{low} ~ {high}{limit_hint}\n"
                    f"限时 {timeout} 秒，直接发送数字即可。"
                ),
                "parse_mode": "html",
                "reply_to_message_id": _interaction_message_id(payload),
            }
        ]

    async def _interaction_guess(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        chat_id: int,
    ) -> list[dict[str, Any]]:
        text = _interaction_message_text(payload)
        if not text.lstrip("-").isdigit():
            return []
        guess = int(text)
        reply_to = _interaction_message_id(payload)
        async with self._get_lock(chat_id):
            game = self._games.get(chat_id)
            if not game or game.finished:
                return [{"type": "no_session"}]
            actor_id, actor_name = _interaction_actor(payload)
            game.attempts += 1
            if guess == game.target:
                game.finished = True
                game.winner_id = actor_id
                game.winner_name = actor_name
                game.history.append(f"{actor_name}: {guess} → ✅ 中！")
                self._games.pop(chat_id, None)
                return [
                    {"type": "send_message", "text": f"+{game.prize}", "reply_to_message_id": reply_to, "send_via": "userbot_reply"},
                    {
                        "type": "send_message",
                        "text": (
                            f"🏆 {actor_name} 猜中了！答案 <b>{game.target}</b>\n"
                            f"奖励 <b>+{game.prize}</b> · 共 {game.attempts} 次猜测"
                        ),
                        "parse_mode": "html",
                    },
                    {
                        "type": "result",
                        "success": True,
                        "result": {
                            "winner_user_id": actor_id,
                            "winner_name": actor_name,
                            "amount": game.prize,
                            "answer": game.target,
                        },
                        "settlement": {
                            "mode": "announce_only",
                            "winner_user_id": actor_id,
                            "winner_name": actor_name,
                            "amount": game.prize,
                            "amount_field": "prize",
                        },
                    },
                    {"type": "end_session"},
                ]
            hint = "📈 大一点" if guess < game.target else "📉 小一点"
            game.history.append(f"{actor_name}: {guess} → {'小了' if guess < game.target else '大了'}")
            if game.max_attempts and game.attempts >= game.max_attempts:
                game.finished = True
                self._games.pop(chat_id, None)
                return [
                    {
                        "type": "send_message",
                        "text": f"<b>💀 次数用完了！</b>\n答案是 <b>{game.target}</b>",
                        "parse_mode": "html",
                    },
                    {"type": "end_session"},
                ]
            limit_hint = f"（{game.attempts}/{game.max_attempts}）" if game.max_attempts else f"（第 {game.attempts} 次）"
            return [{"type": "send_message", "text": f"{hint} {limit_hint}", "reply_to_message_id": reply_to}]

    # ── 命令入口 ─────────────────────────────────────
    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        arg_str = " ".join(args).strip()

        # 有进行中的游戏 → 当作猜测
        lock = self._get_lock(chat_id)
        async with lock:
            gs = self._games.get(chat_id)
            if gs and not gs.finished:
                return await self._handle_guess(chat_id, arg_str, event, ctx)

        # 没有游戏 → 开始新游戏
        prize = self._parse_prize(args)
        if prize <= 0:
            prefix = current_command_prefix(fallback=",")
            await event.reply(f"请指定奖励金额，例如：{prefix}{self._command} 100", parse_mode="html")
            return

        # 解析范围：默认 1-100，可用“指令 奖励 1 1000”自定义。
        low, high = 1, 100
        max_attempts = 0  # 0 = 不限制
        game_args = args[1:]
        if len(game_args) >= 2:
            try:
                low = int(game_args[0])
                high = int(game_args[1])
                if low >= high:
                    low, high = 1, 100
            except ValueError:
                pass
        if len(game_args) >= 3:
            try:
                max_attempts = max(1, int(game_args[2]))
            except ValueError:
                pass

        target = random.randint(low, high)

        gs = GuessGame(
            target=target,
            low=low,
            high=high,
            prize=prize,
            max_attempts=max_attempts,
            started_at=time.monotonic(),
        )

        async with lock:
            self._games[chat_id] = gs

        limit_hint = f"（最多 {max_attempts} 次）" if max_attempts else ""
        msg = await event.reply(
            f"<b>🔢 猜数字</b>\n\n"
            f"奖励：<b>+{prize}</b>\n"
            f"范围：{low} ~ {high}{limit_hint}\n"
            f"直接发数字就能猜，或者用 ,{self._command} 数字\n\n"
            f"来猜吧！",
            parse_mode="html",
        )
        gs.message_id = int(getattr(msg, "id", 0) or 0) or None

        self._track_task(asyncio.create_task(self._auto_timeout(chat_id, ctx, gs.started_at)))

    # ── 处理猜测 ─────────────────────────────────────
    async def _handle_guess(self, chat_id: int, arg_str: str, event: Any, ctx: PluginContext) -> None:
        gs = self._games.get(chat_id)
        if not gs or gs.finished:
            return

        # 解析猜测值
        guess_str = arg_str.strip()
        if not guess_str:
            return
        try:
            guess = int(guess_str)
        except ValueError:
            return

        sender = await event.get_sender()
        player_name = public_entity_display_name(sender, default="玩家")
        player_id = int(getattr(sender, "id", 0) or 0)

        gs.attempts += 1

        if guess == gs.target:
            # 猜中了！
            gs.finished = True
            gs.winner_name = player_name
            gs.winner_id = player_id
            gs.history.append(f"{player_name}: {guess} → ✅ 中！")

            history_text = "\n".join(gs.history[-10:]) if gs.history else ""
            message_id = int(getattr(event, "id", 0) or 0) or None
            await self._send_prize_reply(ctx, event, chat_id, message_id, gs.prize)
            await self._edit_game_message(
                ctx,
                chat_id,
                gs,
                f"\n\n🏆 {player_name} 猜中了！答案 <b>{gs.target}</b>\n奖励 <b>+{gs.prize}</b> · 共 {gs.attempts} 次猜测\n<i>最近记录：\n{history_text}</i>",
            )
            self._games.pop(chat_id, None)
            return

        # 没猜中
        if guess < gs.target:
            hint = "📈 大一点"
            gs.history.append(f"{player_name}: {guess} → 小了")
        else:
            hint = "📉 小一点"
            gs.history.append(f"{player_name}: {guess} → 大了")

        # 检查次数限制
        if gs.max_attempts and gs.attempts >= gs.max_attempts:
            gs.finished = True
            history_text = "\n".join(gs.history[-10:]) if gs.history else ""
            await event.reply(
                f"<b>💀 次数用完了！</b>\n\n"
                f"答案是 <b>{gs.target}</b>\n"
                f"📊 用了 {gs.attempts} 次都没猜到\n\n"
                f"<i>记录：\n{history_text}</i>",
                parse_mode="html",
            )
            self._games.pop(chat_id, None)
            return

        # 接着猜
        limit_hint = f"（{gs.attempts}/{gs.max_attempts}）" if gs.max_attempts else f"（第 {gs.attempts} 次）"
        await event.reply(
            f"{hint} {limit_hint}",
            parse_mode="html",
        )

    @staticmethod
    def _parse_prize(args: list[str]) -> int:
        if not args:
            return 0
        try:
            return max(0, min(1_000_000, int(args[0])))
        except ValueError:
            return 0

    async def _send_prize_reply(self, ctx: PluginContext, event: Any, chat_id: int, message_id: int | None, prize: int) -> None:
        text = f"+{prize}"
        try:
            await event.reply(text)
            return
        except Exception:
            pass
        if ctx.client and message_id:
            try:
                await ctx.client.send_message(chat_id, text, reply_to=message_id)
                return
            except Exception:
                pass
        if ctx.client:
            await ctx.client.send_message(chat_id, text)

    async def _edit_game_message(self, ctx: PluginContext, chat_id: int, gs: GuessGame, suffix: str) -> None:
        if not ctx.client or not gs.message_id:
            return
        limit_hint = f"（最多 {gs.max_attempts} 次）" if gs.max_attempts else ""
        try:
            await ctx.client.edit_message(
                chat_id,
                gs.message_id,
                f"<b>🔢 猜数字</b>\n\n奖励：<b>+{gs.prize}</b>\n范围：{gs.low} ~ {gs.high}{limit_hint}\n{suffix}",
                parse_mode="html",
            )
        except Exception as exc:
            if ctx.log:
                await ctx.log("warn", f"[guess_number] 题目消息更新失败：{type(exc).__name__}: {exc}")

    # ── 消息钩子（监听纯数字猜测）──────────────────
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = getattr(event, "raw_text", "") or ""
        text = text.strip()

        # 只处理纯数字消息
        if not text.lstrip("-").isdigit():
            return
        # 忽略命令
        if text.startswith(",") or text.startswith("/"):
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        gs = self._games.get(chat_id)
        if not gs or gs.finished:
            return

        # 交互bot发起的游戏由interaction处理，不走on_message
        if gs.via_interaction:
            return

        lock = self._get_lock(chat_id)
        async with lock:
            if gs.finished:
                return
            await self._handle_guess(chat_id, text, event, ctx)

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
            await ctx.log("info", f"[guess_number] chat {chat_id} 猜数字超时，答案是 {gs.target}")


PLUGIN_CLASS = GuessNumberPlugin

__all__ = ["GuessNumberPlugin", "PLUGIN_CLASS"]
