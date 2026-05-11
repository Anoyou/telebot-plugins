"""猜数字远程插件。

群内模式：发起者设定范围 → 系统随机选数 → 群友轮流猜 → 提示大/小 → 猜中获胜。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register


# ─────────────────────────────────────────────────────
# 游戏状态
# ─────────────────────────────────────────────────────
@dataclass
class GuessGame:
    target: int = 0
    low: int = 1
    high: int = 100
    prize: int = 0
    attempts: int = 0
    max_attempts: int = 0
    started_at: float = 0.0
    message_id: int | None = None
    finished: bool = False
    winner_name: str = ""
    winner_id: int = 0
    history: list[str] = field(default_factory=list)  # "玩家名: 猜测值 → 大/小/中"


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
            await event.reply(f"请指定奖励金额，例如：,{self._command} 100", parse_mode="html")
            return

        # 解析范围：默认 1-100，可 ,guess 奖励 1 1000 自定义
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
        player_name = getattr(sender, "first_name", "") or "玩家"
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

        lock = self._get_lock(chat_id)
        async with lock:
            if gs.finished:
                return
            await self._handle_guess(chat_id, text, event, ctx)

    # ── 超时 ─────────────────────────────────────────
    async def _auto_timeout(self, chat_id: int, ctx: PluginContext, started_at: float) -> None:
        await asyncio.sleep(self._timeout)
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
