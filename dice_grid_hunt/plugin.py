"""九宫格骰子竞猜远程插件。

玩法：
  1. 一局生成 9 组（每组 6 颗）骰子结果，以九宫格展示。
  2. 仅公布一个目标总点数（该点数在 9 组中唯一）。
  3. 群友回复 1-9 抢答对应格子，首个答对者获奖。
"""

from __future__ import annotations

import asyncio
import io
import random
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
from .manifest import MANIFEST

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
    prize: int
    started_at: float
    message_id: int | None = None
    answered: bool = False
    winner_id: int = 0
    winner_name: str = ""
    winner_message_id: int | None = None
    last_guess_at: dict[int, float] | None = None


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _set_px(buf: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        i = (y * width + x) * 3
        buf[i:i + 3] = bytes(color)


def _fill_rect(buf: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    for yy in range(y, y + h):
        if yy < 0 or yy >= height:
            continue
        row_start = (yy * width + max(0, x)) * 3
        row_end = (yy * width + min(width, x + w)) * 3
        if row_start < row_end:
            buf[row_start:row_end] = bytes(color) * ((row_end - row_start) // 3)


def _fill_circle(buf: bytearray, width: int, height: int, cx: int, cy: int, radius: int, color: tuple[int, int, int]) -> None:
    rr = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= rr:
                _set_px(buf, width, height, x, y, color)


SEGMENTS = {
    "0": "abcfed",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abfgcd",
}


def _draw_digit(buf: bytearray, width: int, height: int, digit: str, x: int, y: int, scale: int, color: tuple[int, int, int]) -> None:
    t = max(2, scale)
    long = scale * 7
    short = scale * 10
    segments = SEGMENTS.get(digit, "")
    if "a" in segments:
        _fill_rect(buf, width, height, x + t, y, long, t, color)
    if "b" in segments:
        _fill_rect(buf, width, height, x + long + t, y + t, t, short, color)
    if "c" in segments:
        _fill_rect(buf, width, height, x + long + t, y + short + 2 * t, t, short, color)
    if "d" in segments:
        _fill_rect(buf, width, height, x + t, y + 2 * short + 2 * t, long, t, color)
    if "e" in segments:
        _fill_rect(buf, width, height, x, y + short + 2 * t, t, short, color)
    if "f" in segments:
        _fill_rect(buf, width, height, x, y + t, t, short, color)
    if "g" in segments:
        _fill_rect(buf, width, height, x + t, y + short + t, long, t, color)


def _draw_die(buf: bytearray, width: int, height: int, x: int, y: int, size: int, value: int) -> None:
    # 主面 + 简单立体高光/阴影，让骰子更接近参考图风格
    _fill_rect(buf, width, height, x, y, size, size, (244, 244, 240))
    _fill_rect(buf, width, height, x + 2, y + 2, size - 6, max(2, size // 7), (255, 255, 252))
    _fill_rect(buf, width, height, x + size - 5, y + 3, 3, size - 8, (198, 198, 194))
    _fill_rect(buf, width, height, x + 3, y + size - 5, size - 8, 3, (186, 186, 182))
    _fill_rect(buf, width, height, x, y, size, 2, (52, 52, 52))
    _fill_rect(buf, width, height, x, y + size - 2, size, 2, (52, 52, 52))
    _fill_rect(buf, width, height, x, y, 2, size, (52, 52, 52))
    _fill_rect(buf, width, height, x + size - 2, y, 2, size, (52, 52, 52))
    p = {
        "tl": (x + size // 4, y + size // 4),
        "tr": (x + size * 3 // 4, y + size // 4),
        "ml": (x + size // 4, y + size // 2),
        "mr": (x + size * 3 // 4, y + size // 2),
        "bl": (x + size // 4, y + size * 3 // 4),
        "br": (x + size * 3 // 4, y + size * 3 // 4),
        "cc": (x + size // 2, y + size // 2),
    }
    dots = {
        1: ["cc"],
        2: ["tl", "br"],
        3: ["tl", "cc", "br"],
        4: ["tl", "tr", "bl", "br"],
        5: ["tl", "tr", "cc", "bl", "br"],
        6: ["tl", "tr", "ml", "mr", "bl", "br"],
    }[value]
    for key in dots:
        cx, cy = p[key]
        _fill_circle(buf, width, height, cx, cy, max(3, size // 10), (26, 28, 34))


def _render_grid_png(rd: RoundState) -> bytes:
    width = height = 900
    buf = bytearray((244, 240, 230) * width * height)
    tile = 284
    gap = 12
    margin = 18
    colors = [
        (235, 111, 91), (242, 197, 92), (96, 156, 147),
        (63, 118, 163), (224, 149, 81), (126, 178, 111),
        (141, 113, 167), (207, 92, 105), (83, 137, 98),
    ]
    for idx, values in enumerate(rd.rolls):
        row = idx // 3
        col = idx % 3
        x0 = margin + col * (tile + gap)
        y0 = margin + row * (tile + gap)
        _fill_rect(buf, width, height, x0, y0, tile, tile, colors[idx])
        _draw_digit(buf, width, height, str(idx + 1), x0 + 18, y0 + 18, 5, (255, 255, 255))
        # 真随机散布（拒绝采样），保证不重叠且不超出边界
        die_size = 58
        left = x0 + 26
        top = y0 + 70
        right = x0 + tile - 26 - die_size
        bottom = y0 + tile - 24 - die_size
        # 先分区再随机，每区只放一个，避免“整齐两排”的视觉
        zones = [
            (left, top, left + 75, top + 70),
            (left + 78, top - 2, left + 155, top + 64),
            (left + 158, top + 6, right, top + 78),
            (left + 4, top + 84, left + 82, bottom - 6),
            (left + 90, top + 92, left + 166, bottom - 10),
            (left + 170, top + 84, right, bottom),
        ]
        random.shuffle(zones)
        chosen: list[tuple[int, int]] = []
        min_center_dist = die_size + 12
        min_center_dist_sq = min_center_dist * min_center_dist
        for zx0, zy0, zx1, zy1 in zones:
            px = random.randint(zx0, max(zx0, zx1))
            py = random.randint(zy0, max(zy0, zy1))
            if all(((px - cx) * (px - cx) + (py - cy) * (py - cy)) >= min_center_dist_sq for cx, cy in chosen):
                chosen.append((px, py))
        for _ in range(1000):
            if len(chosen) == 6:
                break
            px = random.randint(left, right)
            py = random.randint(top, bottom)
            if all(((px - cx) * (px - cx) + (py - cy) * (py - cy)) >= min_center_dist_sq for cx, cy in chosen):
                chosen.append((px, py))
        if len(chosen) < 6:
            # 兜底布局（绝不重叠）
            chosen = [(x0 + 40, y0 + 84), (x0 + 118, y0 + 84), (x0 + 196, y0 + 84), (x0 + 40, y0 + 164), (x0 + 118, y0 + 164), (x0 + 196, y0 + 164)]
        for value, (dx, dy) in zip(values, chosen):
            _draw_die(buf, width, height, dx, dy, die_size, value)
    raw = b"".join(b"\x00" + buf[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )


def _parse_prize(args: list[str]) -> int:
    if not args:
        return 0
    try:
        return max(0, min(1_000_000, int(args[0])))
    except ValueError:
        return 0


@register
class DiceGridHuntPlugin(Plugin):
    key = "dice_grid_hunt"
    display_name = "九宫格骰子竞猜"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {
        "command",
        "timeout",
        "auto_next",
        "next_delay",
        "guess_cooldown",
        "template_title",
        "template_target_line",
        "template_guide_line",
        "template_reward_line",
        "delete_after_round",
    }

    def __init__(self) -> None:
        super().__init__()
        self._command = "dicegrid"
        self._timeout = 90
        self._auto_next = False
        self._next_delay = 3
        self._guess_cooldown = 2.0
        self._template_title = "🎯 九宫格骰子竞猜（v{version}）"
        self._template_target_line = "目标点数：<b>{target_sum}</b>（9 格里唯一）"
        self._template_guide_line = "回复 <code>1-9</code> 选择你认为答案所在的格子。"
        self._template_reward_line = "首个答对者奖励：<b>+{prize}</b> · 超时 {timeout} 秒"
        self._delete_after_round = 0
        self._rounds: dict[int, RoundState] = {}
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
        self._command = cfg.get("command", "dicegrid")
        self._timeout = cfg.get("timeout", 90)
        self._auto_next = bool(cfg.get("auto_next", False))
        self._next_delay = max(1, int(cfg.get("next_delay", 3)))
        self._guess_cooldown = max(0.0, float(cfg.get("guess_cooldown", 2.0)))
        self._template_title = str(cfg.get("template_title", self._template_title))
        self._template_target_line = str(cfg.get("template_target_line", self._template_target_line))
        self._template_guide_line = str(cfg.get("template_guide_line", self._template_guide_line))
        self._template_reward_line = str(cfg.get("template_reward_line", self._template_reward_line))
        self._delete_after_round = max(0, int(cfg.get("delete_after_round", 0)))

        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log(
                "info",
                f"[dice_grid_hunt] 已启动，指令：{self._command}，超时：{self._timeout}s",
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
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

        prize = _parse_prize(args)
        if prize <= 0:
            await event.reply(f"请指定奖励金额，例如：,{self._command} 100", parse_mode="html")
            return

        lock = self._get_lock(chat_id)
        async with lock:
            rd = self._rounds.get(chat_id)
            if rd and not rd.answered:
                if ctx.client and rd.message_id:
                    try:
                        await ctx.client.edit_message(
                            chat_id,
                            rd.message_id,
                            self._render_round_text(rd, include_guide=True)
                            + "\n\n<i>本轮进行中，直接回复 <code>1-9</code> 抢答。</i>",
                            parse_mode="html",
                        )
                        return
                    except Exception:
                        pass
                await event.reply(
                    self._render_round_text(rd, include_guide=False)
                    + "\n\n本轮进行中，直接回复 <code>1-9</code> 抢答，或输入命令 <code>,"
                    + self._command
                    + " stop</code> 结束本轮。",
                    parse_mode="html",
                )
                return

            rd = self._new_round(prize)
            self._rounds[chat_id] = rd

        msg = await self._send_round(ctx, event, rd)
        rd.message_id = int(getattr(msg, "id", 0) or 0) or None
        self._track_task(asyncio.create_task(self._auto_timeout(chat_id, ctx, rd.started_at)))

    async def _send_round(self, ctx: PluginContext, event: Any, rd: RoundState) -> Any:
        image_file = io.BytesIO(_render_grid_png(rd))
        image_file.name = "dice_grid_hunt.png"
        caption = self._render_round_text(rd, include_guide=True)
        # 优先编辑触发命令的消息（在可编辑场景下）
        if ctx.client:
            try:
                edited = await ctx.client.edit_message(event.chat_id, event.id, caption, file=image_file, parse_mode="html")
                return edited
            except Exception:
                image_file.seek(0)
        if ctx.client:
            return await ctx.client.send_file(event.chat_id, image_file, caption=caption, parse_mode="html")
        return await event.reply(caption, parse_mode="html")

    def _new_round(self, prize: int) -> RoundState:
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
                prize=prize,
                started_at=time.monotonic(),
                last_guess_at={},
            )

    def _render_round_text(self, rd: RoundState, include_guide: bool) -> str:
        vars_map = {
            "version": MANIFEST.version,
            "target_sum": rd.target_sum,
            "prize": rd.prize,
            "timeout": self._timeout,
        }
        lines = [
            f"<b>{self._template_title.format_map(vars_map)}</b>",
            "",
            self._template_target_line.format_map(vars_map),
        ]

        if include_guide:
            lines.extend(
                [
                    "",
                    self._template_guide_line.format_map(vars_map),
                    self._template_reward_line.format_map(vars_map),
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
            sender = await event.get_sender()
            user_id = int(getattr(sender, "id", 0) or 0)
            now = time.monotonic()
            last_guess_at = rd.last_guess_at if rd.last_guess_at is not None else {}
            last_at = last_guess_at.get(user_id, 0.0)
            if now - last_at < self._guess_cooldown:
                return
            last_guess_at[user_id] = now
            rd.last_guess_at = last_guess_at

            if pick != rd.answer_index:
                return

            rd.answered = True
            name = getattr(sender, "first_name", "") or "玩家"
            rd.winner_name = name
            rd.winner_id = int(getattr(sender, "id", 0) or 0)
            rd.winner_message_id = int(getattr(event, "id", 0) or 0) or None

        elapsed = time.monotonic() - rd.started_at
        await self._send_prize_reply(ctx, event, chat_id, rd)
        await self._edit_round_message(
            ctx,
            chat_id,
            rd,
            f"\n\n🏆 {rd.winner_name} 答对！答案是 <b>{rd.answer_index}</b>，点数和 <b>{rd.target_sum}</b>\n"
            f"⏱️ {elapsed:.1f} 秒 · 奖励 <b>+{rd.prize}</b>",
        )
        self._track_task(asyncio.create_task(self._delete_round_message_later(ctx, chat_id, rd.message_id)))

        if not self._auto_next:
            return

        await asyncio.sleep(self._next_delay)
        lock = self._get_lock(chat_id)
        async with lock:
            rd = self._new_round(rd.prize)
            self._rounds[chat_id] = rd
        msg = await self._send_round(ctx, event, rd)
        rd.message_id = int(getattr(msg, "id", 0) or 0) or None
        self._track_task(asyncio.create_task(self._auto_timeout(chat_id, ctx, rd.started_at)))

    async def _send_prize_reply(self, ctx: PluginContext, event: Any, chat_id: int, rd: RoundState) -> None:
        text = f"+{rd.prize}"
        try:
            await event.reply(text)
            return
        except Exception:
            pass
        if ctx.client and rd.winner_message_id:
            try:
                await ctx.client.send_message(chat_id, text, reply_to=rd.winner_message_id)
                return
            except Exception:
                pass
        if ctx.client:
            await ctx.client.send_message(chat_id, text)

    async def _edit_round_message(self, ctx: PluginContext, chat_id: int, rd: RoundState, suffix: str) -> None:
        if not ctx.client or not rd.message_id:
            return
        try:
            await ctx.client.edit_message(chat_id, rd.message_id, self._render_round_text(rd, include_guide=True) + suffix, parse_mode="html")
        except Exception as exc:
            if ctx.log:
                await ctx.log("warn", f"[dice_grid_hunt] 题目消息更新失败：{type(exc).__name__}: {exc}")

    async def _auto_timeout(self, chat_id: int, ctx: PluginContext, started_at: float) -> None:
        await asyncio.sleep(self._timeout)
        async with self._get_lock(chat_id):
            rd = self._rounds.get(chat_id)
            if not rd or rd.answered or rd.started_at != started_at:
                return

            rd.answered = True
            answer_roll = rd.rolls[rd.answer_index - 1]
        if ctx.log:
            await ctx.log(
                "info",
                f"[dice_grid_hunt] chat {chat_id} 超时，答案格：{rd.answer_index}，骰子：{_fmt_roll(answer_roll)}，点数和：{rd.target_sum}",
            )
        self._track_task(asyncio.create_task(self._delete_round_message_later(ctx, chat_id, rd.message_id)))

    async def _delete_round_message_later(self, ctx: PluginContext, chat_id: int, message_id: int | None) -> None:
        if not ctx.client or not message_id or self._delete_after_round <= 0:
            return
        await asyncio.sleep(self._delete_after_round)
        try:
            await ctx.client.delete_messages(chat_id, message_id)
        except Exception:
            pass


PLUGIN_CLASS = DiceGridHuntPlugin

__all__ = ["DiceGridHuntPlugin", "PLUGIN_CLASS"]
