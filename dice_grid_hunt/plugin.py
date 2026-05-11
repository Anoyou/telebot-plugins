"""九宫格骰子竞猜远程插件。

玩法：
  1. 一局生成 9 组（每组 6 颗）骰子结果，以九宫格展示。
  2. 仅公布一个目标总点数（该点数在 9 组中唯一）。
  3. 群友回复 1-9 抢答对应格子，首个答对者获奖。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]


def _roll_dice(count: int = 6) -> list[int]:
    return [random.randint(1, 6) for _ in range(count)]


def _sum(values: list[int]) -> int:
    return sum(values)


def _fmt_roll(values: list[int]) -> str:
    return "".join(DICE_FACES[v - 1] for v in values)


@dataclass
class RoundState:
    rolls: list[list[int]]
    sums: list[int]
    answer_index: int  # 1..9
    target_sum: int
    started_at: float
    answered: bool = False
    winner_id: int = 0
    winner_name: str = ""


@register
class DiceGridHuntPlugin(Plugin):
    key = "dice_grid_hunt"
    display_name = "九宫格骰子竞猜"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command", "reward", "reward_unit", "timeout", "auto_next", "next_delay"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "dicegrid"
        self._reward = 10
        self._reward_unit = "积分"
        self._timeout = 90
        self._auto_next = False
        self._next_delay = 3
        self._rounds: dict[int, RoundState] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "dicegrid")
        self._reward = cfg.get("reward", 10)
        self._reward_unit = cfg.get("reward_unit", "积分")
        self._timeout = cfg.get("timeout", 90)
        self._auto_next = bool(cfg.get("auto_next", False))
        self._next_delay = max(1, int(cfg.get("next_delay", 3)))

        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log(
                "info",
                f"[dice_grid_hunt] 已启动，指令：{self._command}，奖励：{self._reward}{self._reward_unit}，超时：{self._timeout}s",
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._rounds.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[dice_grid_hunt] 已停止")

    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        arg = " ".join(args).strip().lower()
        if arg in ("stop", "end", "结束", "停止"):
            lock = self._get_lock(chat_id)
            async with lock:
                self._rounds.pop(chat_id, None)
            await event.reply("✅ 已结束当前九宫格骰子竞猜。", parse_mode="html")
            return

        lock = self._get_lock(chat_id)
        async with lock:
            rd = self._rounds.get(chat_id)
            if rd and not rd.answered:
                await event.reply(
                    self._render_round_text(rd, include_guide=False)
                    + "\n\n直接回复 <code>1-9</code> 抢答，或输入命令 <code>,"
                    + self._command
                    + " stop</code> 结束本轮。",
                    parse_mode="html",
                )
                return

            rd = self._new_round()
            self._rounds[chat_id] = rd

        await event.reply(
            self._render_round_text(rd, include_guide=True),
            parse_mode="html",
        )
        asyncio.create_task(self._auto_timeout(chat_id, ctx))

    def _new_round(self) -> RoundState:
        while True:
            rolls = [_roll_dice(6) for _ in range(9)]
            sums = [_sum(r) for r in rolls]
            count_map: dict[int, int] = {}
            for s in sums:
                count_map[s] = count_map.get(s, 0) + 1
            unique_indexes = [i for i, s in enumerate(sums) if count_map.get(s, 0) == 1]
            if not unique_indexes:
                continue

            answer_zero_based = random.choice(unique_indexes)
            return RoundState(
                rolls=rolls,
                sums=sums,
                answer_index=answer_zero_based + 1,
                target_sum=sums[answer_zero_based],
                started_at=time.monotonic(),
            )

    def _render_round_text(self, rd: RoundState, include_guide: bool) -> str:
        lines = [
            "<b>🎯 九宫格骰子竞猜</b>",
            "",
            f"目标点数：<b>{rd.target_sum}</b>（9 格里唯一）",
            "",
            "<blockquote>",
        ]

        for row in range(3):
            row_cells = []
            for col in range(3):
                idx = row * 3 + col
                row_cells.append(f"{idx + 1}.{_fmt_roll(rd.rolls[idx])}")
            lines.append(" | ".join(row_cells))

        lines.append("</blockquote>")

        if include_guide:
            lines.extend(
                [
                    "",
                    "回复 <code>1-9</code> 选择你认为答案所在的格子。",
                    f"首个答对者奖励：<b>{self._reward} {self._reward_unit}</b> · 超时 {self._timeout} 秒",
                ]
            )

        return "\n".join(lines)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        if not text or text.startswith(",") or text.startswith("/"):
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        rd = self._rounds.get(chat_id)
        if not rd or rd.answered:
            return

        if not text.isdigit():
            return

        pick = int(text)
        if pick < 1 or pick > 9:
            return

        lock = self._get_lock(chat_id)
        async with lock:
            rd = self._rounds.get(chat_id)
            if not rd or rd.answered:
                return

            if pick != rd.answer_index:
                return

            rd.answered = True
            sender = await event.get_sender()
            name = getattr(sender, "first_name", "") or "玩家"
            rd.winner_name = name
            rd.winner_id = int(getattr(sender, "id", 0) or 0)

        elapsed = time.monotonic() - rd.started_at
        answer_roll = rd.rolls[rd.answer_index - 1]
        await event.reply(
            f"🎉 <b>{rd.winner_name}</b> 抢答正确！\n\n"
            f"正确位置：<b>{rd.answer_index}</b>\n"
            f"该格骰子：{_fmt_roll(answer_roll)}\n"
            f"点数和：<b>{rd.target_sum}</b>\n"
            f"⏱️ {elapsed:.1f} 秒 · 奖励 <b>{self._reward} {self._reward_unit}</b>",
            parse_mode="html",
        )

        if not self._auto_next:
            return

        await asyncio.sleep(self._next_delay)
        lock = self._get_lock(chat_id)
        async with lock:
            rd = self._new_round()
            self._rounds[chat_id] = rd
        await event.reply(self._render_round_text(rd, include_guide=True), parse_mode="html")
        asyncio.create_task(self._auto_timeout(chat_id, ctx))

    async def _auto_timeout(self, chat_id: int, ctx: PluginContext) -> None:
        await asyncio.sleep(self._timeout)
        rd = self._rounds.get(chat_id)
        if not rd or rd.answered:
            return

        rd.answered = True
        answer_roll = rd.rolls[rd.answer_index - 1]
        if ctx.log:
            await ctx.log(
                "info",
                f"[dice_grid_hunt] chat {chat_id} 超时，答案格：{rd.answer_index}，骰子：{_fmt_roll(answer_roll)}，点数和：{rd.target_sum}",
            )


PLUGIN_CLASS = DiceGridHuntPlugin

__all__ = ["DiceGridHuntPlugin", "PLUGIN_CLASS"]
