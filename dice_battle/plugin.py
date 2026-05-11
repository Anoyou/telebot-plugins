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

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "dice")
        self._timeout = cfg.get("timeout", 60)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[dice_battle] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._battles.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[dice_battle] 已停止")

    # ── 命令入口 ─────────────────────────────────────
    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        sender = await event.get_sender()
        sender_id = int(getattr(sender, "id", 0) or 0)
        sender_name = getattr(sender, "first_name", "") or "玩家"

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
                target_name = getattr(reply_sender, "first_name", "") or "对手"

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
                asyncio.create_task(self._battle_timeout(chat_id, ctx))
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
    async def _battle_timeout(self, chat_id: int, ctx: PluginContext) -> None:
        await asyncio.sleep(self._timeout)
        battle = self._battles.get(chat_id)
        if battle and battle.phase == "waiting":
            self._battles.pop(chat_id, None)
            if ctx.log:
                await ctx.log("info", f"[dice_battle] chat {chat_id} 对战邀请超时")

    # ── 消息钩子 ─────────────────────────────────────
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        pass


PLUGIN_CLASS = DiceBattlePlugin

__all__ = ["DiceBattlePlugin", "PLUGIN_CLASS"]
