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
    attempts: int = 0
    max_attempts: int = 0
    started_at: float = 0.0
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
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "guess"
        self._timeout = 300
        self._games: dict[int, GuessGame] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "guess")
        self._timeout = cfg.get("timeout", 300)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[guess_number] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
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
        # 解析范围：默认 1-100，可 ,guess 1 1000 自定义
        low, high = 1, 100
        max_attempts = 0  # 0 = 不限制
        if len(args) >= 2:
            try:
                low = int(args[0])
                high = int(args[1])
                if low >= high:
                    low, high = 1, 100
            except ValueError:
                pass
        if len(args) >= 3:
            try:
                max_attempts = max(1, int(args[2]))
            except ValueError:
                pass

        target = random.randint(low, high)

        gs = GuessGame(
            target=target,
            low=low,
            high=high,
            max_attempts=max_attempts,
            started_at=time.monotonic(),
        )

        async with lock:
            self._games[chat_id] = gs

        limit_hint = f"（最多 {max_attempts} 次）" if max_attempts else ""
        await event.reply(
            f"<b>🔢 猜数字</b>\n\n"
            f"范围：{low} ~ {high}{limit_hint}\n"
            f"直接发数字就能猜，或者用 ,{self._command} 数字\n\n"
            f"来猜吧！",
            parse_mode="html",
        )

        asyncio.create_task(self._auto_timeout(chat_id, ctx))

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
            await event.reply(
                f"<b>🎉 猜中了！</b>\n\n"
                f"答案就是 <b>{gs.target}</b>\n"
                f"🏆 获胜者：{player_name}\n"
                f"📊 共 {gs.attempts} 次猜测\n\n"
                f"<i>最近记录：\n{history_text}</i>",
                parse_mode="html",
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
    async def _auto_timeout(self, chat_id: int, ctx: PluginContext) -> None:
        await asyncio.sleep(self._timeout)
        gs = self._games.get(chat_id)
        if gs and not gs.finished:
            gs.finished = True
            self._games.pop(chat_id, None)
            if ctx.log:
                await ctx.log("info", f"[guess_number] chat {chat_id} 猜数字超时，答案是 {gs.target}")


PLUGIN_CLASS = GuessNumberPlugin

__all__ = ["GuessNumberPlugin", "PLUGIN_CLASS"]
