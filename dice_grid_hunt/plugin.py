"""九宫格骰子竞猜远程插件。

玩法：
  1. 一局生成 9 组（每组 6 颗）骰子结果；以九宫格展示。
  2. 仅公布一个目标总点数（该点数在 9 组中唯一）。
  3. 群友回复 1-9 抢答对应格子；首个答对者获奖。
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
import re
import struct
import time
import zlib
from dataclasses import dataclass
from html import escape
from typing import Any

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register
from .manifest import (
    LEGACY_IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT,
    LEGACY_INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT,
    IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT,
    INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT,
    MANIFEST,
)

DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]

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
    timeout: int = 90
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


def _fill_polygon(buf: bytearray, width: int, height: int, points: list[tuple[int, int]], color: tuple[int, int, int]) -> None:
    if len(points) < 3:
        return
    min_y = max(0, min(y for _, y in points))
    max_y = min(height - 1, max(y for _, y in points))
    for y in range(min_y, max_y + 1):
        intersections: list[int] = []
        for i, (x1, y1) in enumerate(points):
            x2, y2 = points[(i + 1) % len(points)]
            if y1 == y2:
                continue
            if (y >= min(y1, y2)) and (y < max(y1, y2)):
                x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                intersections.append(int(round(x)))
        intersections.sort()
        for i in range(0, len(intersections) - 1, 2):
            _fill_rect(buf, width, height, intersections[i], y, intersections[i + 1] - intersections[i] + 1, 1, color)


def _draw_line(buf: bytearray, width: int, height: int, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    x, y = x1, y1
    while True:
        _set_px(buf, width, height, x, y, color)
        if x == x2 and y == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _draw_polygon_outline(buf: bytearray, width: int, height: int, points: list[tuple[int, int]], color: tuple[int, int, int]) -> None:
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        _draw_line(buf, width, height, x1, y1, x2, y2, color)


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
    pip_colors = [(26, 28, 34), (203, 71, 90), (60, 129, 216), (82, 163, 84), (212, 150, 64), (142, 92, 187)]
    pip_color = random.choice(pip_colors)
    depth = max(7, size // 5)
    face = [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]
    right = [(x + size, y), (x + size + depth, y + depth), (x + size + depth, y + size + depth), (x + size, y + size)]
    bottom = [(x, y + size), (x + size, y + size), (x + size + depth, y + size + depth), (x + depth, y + size + depth)]
    shadow = [(x + depth + 3, y + depth + 4), (x + size + depth + 5, y + depth + 4), (x + size + depth + 5, y + size + depth + 5), (x + depth + 3, y + size + depth + 5)]
    _fill_polygon(buf, width, height, shadow, (88, 88, 88))
    _fill_polygon(buf, width, height, right, (195, 195, 188))
    _fill_polygon(buf, width, height, bottom, (177, 177, 170))
    _fill_polygon(buf, width, height, face, (248, 248, 243))
    _fill_rect(buf, width, height, x + 3, y + 3, size - 7, max(2, size // 9), (255, 255, 253))
    _draw_polygon_outline(buf, width, height, right, (42, 42, 42))
    _draw_polygon_outline(buf, width, height, bottom, (42, 42, 42))
    _draw_polygon_outline(buf, width, height, face, (42, 42, 42))
    _draw_line(buf, width, height, x + size, y, x + size + depth, y + depth, (255, 255, 255))

    p0 = {
        "tl": (x + size // 4, y + size // 4),
        "tr": (x + size * 3 // 4, y + size // 4),
        "ml": (x + size // 4, y + size // 2),
        "mr": (x + size * 3 // 4, y + size // 2),
        "bl": (x + size // 4, y + size * 3 // 4),
        "br": (x + size * 3 // 4, y + size * 3 // 4),
        "cc": (x + size // 2, y + size // 2),
    }
    rotate_map = random.choice([
        {"tl": "tl", "tr": "tr", "ml": "ml", "mr": "mr", "bl": "bl", "br": "br", "cc": "cc"},
        {"tl": "tr", "tr": "br", "mr": "bl", "br": "tl", "bl": "ml", "ml": "mr", "cc": "cc"},
        {"tl": "br", "tr": "bl", "ml": "mr", "mr": "ml", "bl": "tr", "br": "tl", "cc": "cc"},
        {"tl": "bl", "tr": "tl", "ml": "mr", "mr": "ml", "bl": "br", "br": "tr", "cc": "cc"},
    ])
    p = {
        key: p0[mapped]
        for key, mapped in rotate_map.items()
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
        _fill_circle(buf, width, height, cx, cy, max(3, size // 10), pip_color)


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
        die_size = 47
        die_w = die_size + max(7, die_size // 5) + 10
        die_h = die_size + max(7, die_size // 5) + 10
        left = x0 + 26
        top = y0 + 72
        right = x0 + tile - 20 - die_w
        bottom = y0 + tile - 16 - die_h
        chosen: list[tuple[int, int]] = []
        gap_px = 2
        for _ in range(2500):
            if len(chosen) == 6:
                break
            px = random.randint(left + die_w // 2, right + die_w // 2)
            py = random.randint(top + die_h // 2, bottom + die_h // 2)
            if all(abs(px - cx) >= die_w + gap_px or abs(py - cy) >= die_h + gap_px for cx, cy in chosen):
                chosen.append((px, py))
        if len(chosen) < 6:
            chosen = [(x0 + 72, y0 + 104), (x0 + 162, y0 + 88), (x0 + 222, y0 + 138), (x0 + 58, y0 + 190), (x0 + 148, y0 + 162), (x0 + 220, y0 + 210)]
        random.shuffle(chosen)
        for value, (cx, cy) in sorted(zip(values, chosen), key=lambda item: item[1][1]):
            dx = cx - die_w // 2
            dy = cy - die_h // 2
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
        "round_message_template",
        "in_progress_message_template",
        "success_message_template",
        "timeout_message_template",
        "cancel_message_template",
        "invalid_prize_message_template",
        "prize_message_template",
        "delete_after_round",
        "force_stop_command",
    }

    def __init__(self) -> None:
        super().__init__()
        self._command = "dicegrid"
        self._timeout = 90
        self._auto_next = False
        self._next_delay = 3
        self._guess_cooldown = 2.0
        self._template_title = "九宫格竞猜"
        self._template_target_line = "目标点数：<b>{target_sum}</b>（9 格里唯一）"
        self._template_guide_line = "回复 <code>1-9</code> 选择你认为答案所在的格子。"
        self._template_reward_line = "首个答对者奖励：<b>+{prize}</b> · 超时 {timeout} 秒"
        self._round_message_template = (
            "<b>九宫格竞猜</b>\n"
            "目标：<b>{target_sum}</b> · 回 <code>1-9</code>\n"
            "奖 <b>+{prize}</b> · {timeout}s · 冷却 {guess_cooldown}s"
        )
        self._in_progress_message_template = IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT
        self._success_message_template = (
            "{winner} 答对：<b>{answer_index}</b>\n"
            "用时 {elapsed}s · 奖励 <b>+{prize}</b>"
        )
        self._timeout_message_template = (
            "超时。答案是 <b>{answer_index}</b> · 点数和 <b>{target_sum}</b>。"
        )
        self._cancel_message_template = "已结束当前九宫格竞猜。"
        self._invalid_prize_message_template = INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT
        self._prize_message_template = "+{prize}"
        self._delete_after_round = 0
        self._force_stop_command = "stop"
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
        self._round_message_template = str(cfg.get("round_message_template", self._round_message_template))
        self._in_progress_message_template = str(cfg.get("in_progress_message_template", self._in_progress_message_template))
        if self._in_progress_message_template == LEGACY_IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT:
            self._in_progress_message_template = IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT
        self._success_message_template = str(cfg.get("success_message_template", self._success_message_template))
        self._timeout_message_template = str(cfg.get("timeout_message_template", self._timeout_message_template))
        self._cancel_message_template = str(cfg.get("cancel_message_template", self._cancel_message_template))
        self._invalid_prize_message_template = str(
            cfg.get("invalid_prize_message_template", self._invalid_prize_message_template)
        )
        if self._invalid_prize_message_template == LEGACY_INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT:
            self._invalid_prize_message_template = INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT
        self._prize_message_template = str(cfg.get("prize_message_template", self._prize_message_template))
        self._delete_after_round = max(0, int(cfg.get("delete_after_round", 0)))
        self._force_stop_command = str(cfg.get("force_stop_command", "stop")).strip().lower() or "stop"

        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log(
                "info",
                f"[dice_grid_hunt] 已启动 v{MANIFEST.version}；指令：{self._command}；超时：{self._timeout}s",
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

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key not in {"start_dice_grid_hunt", "answer_dice_grid_hunt"}:
            return None
        event_type = self._interaction_event_type(payload)
        if event_type in {"payment_confirmed", "keyword"}:
            return await self._interaction_start(ctx, payload)
        if event_type == "message":
            return await self._interaction_answer(ctx, payload)
        if event_type == "session_close":
            return await self._interaction_close(payload)
        return []

    async def _interaction_start(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]]:
        chat_id = self._payload_chat_id(payload)
        if not chat_id:
            return [{"type": "no_session"}]
        prize = self._positive_int(payload.get("prize"), 100, minimum=1)
        timeout = self._positive_int(
            payload.get("timeout") or payload.get("valid_seconds"),
            self._timeout,
            minimum=10,
            maximum=86400,
        )
        async with self._get_lock(chat_id):
            rd = self._rounds.get(chat_id)
            if rd and not rd.answered and not self._round_expired(rd):
                return [
                    {
                        "type": "send_message",
                        "text": self._render_text(
                            self._in_progress_message_template,
                            {
                                "prefix": current_command_prefix(),
                                "command": self._command,
                                "force_stop_command": self._force_stop_command,
                            },
                        ),
                    }
                ]
            rd = self._new_round(prize, timeout=timeout)
            self._rounds[chat_id] = rd

        if ctx.log:
            await ctx.log("info", f"[dice_grid_hunt] 交互 Bot 已开局 chat={chat_id} prize={prize} timeout={timeout}")
        return [
            {
                "type": "send_photo",
                "photo_base64": base64.b64encode(_render_grid_png(rd)).decode("ascii"),
                "filename": "dice_grid_hunt.png",
                "caption": self._render_round_text(rd, include_guide=True),
                "reply_to_message_id": self._payload_message_id(payload),
            }
        ]

    async def _interaction_answer(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]]:
        chat_id = self._payload_chat_id(payload)
        if not chat_id:
            return []
        text = self._interaction_message_text(payload)
        pick = self._parse_grid_pick(text)
        if pick is None:
            return []

        async with self._get_lock(chat_id):
            rd = self._rounds.get(chat_id)
            if not rd or rd.answered:
                return []
            if self._round_expired(rd):
                rd.answered = True
                self._rounds.pop(chat_id, None)
                return [
                    {
                        "type": "send_message",
                        "text": self._render_timeout_announcement(rd),
                    },
                    {"type": "end_session"},
                ]

            user_id = self._positive_int(payload.get("sender_user_id"), 0, minimum=0)
            now = time.monotonic()
            last_guess_at = rd.last_guess_at if rd.last_guess_at is not None else {}
            last_at = last_guess_at.get(user_id, 0.0)
            if now - last_at < self._guess_cooldown:
                return []
            last_guess_at[user_id] = now
            rd.last_guess_at = last_guess_at

            if pick != rd.answer_index:
                return []

            rd.answered = True
            rd.winner_id = user_id
            rd.winner_name = self._interaction_actor_name(payload)
            rd.winner_message_id = self._payload_message_id(payload)
            self._rounds.pop(chat_id, None)
            payout_account, payout_mode = self._interaction_payout_info(payload)

        if ctx.log:
            await ctx.log(
                "info",
                f"[dice_grid_hunt] 交互 Bot 答对 chat={chat_id} winner={rd.winner_name!r} answer={rd.answer_index} prize={rd.prize}",
            )
        return [
            {
                "type": "send_message",
                "text": self._render_interaction_success(rd, payout_account, payout_mode),
                "reply_to_message_id": rd.winner_message_id,
            },
            {
                "type": "result",
                "success": True,
                "result": {
                    "status": "winner",
                    "winner_user_id": rd.winner_id or None,
                    "winner_name": rd.winner_name,
                    "winner_message_id": rd.winner_message_id,
                    "target_sum": rd.target_sum,
                    "answer_index": rd.answer_index,
                    "prize": rd.prize,
                    "payout_mode": payout_mode,
                    "payout_account_label": payout_account,
                },
                "settlement": {
                    "mode": "announce_only" if payout_mode != "auto" else "auto",
                    "amount": rd.prize,
                    "winner_user_id": rd.winner_id or None,
                    "winner_name": rd.winner_name,
                    "payout_account_label": payout_account,
                    "status": "announced",
                },
            },
            {"type": "end_session"},
        ]

    async def _interaction_close(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        chat_id = self._payload_chat_id(payload)
        if chat_id:
            async with self._get_lock(chat_id):
                self._rounds.pop(chat_id, None)
        return []

    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        arg = " ".join(args).strip().lower()
        force_stop_commands = {self._force_stop_command, "stop", "end", "结束", "停止"}
        if arg in force_stop_commands:
            lock = self._get_lock(chat_id)
            async with lock:
                rd = self._rounds.pop(chat_id, None)
            if rd and rd.message_id:
                self._track_task(asyncio.create_task(self._delete_round_message_later(ctx, chat_id, rd.message_id)))
            await self._edit_trigger_or_reply(ctx, event, self._render_text(self._cancel_message_template, {}))
            return

        lock = self._get_lock(chat_id)
        async with lock:
            rd = self._rounds.get(chat_id)
            if rd and not rd.answered:
                await self._edit_trigger_or_reply(
                    ctx,
                    event,
                    self._render_text(
                        self._in_progress_message_template,
                        {
                            "prefix": current_command_prefix(),
                            "command": self._command,
                            "force_stop_command": self._force_stop_command,
                        },
                    ),
                )
                return

            prize = _parse_prize(args)
            if prize <= 0:
                await self._edit_trigger_or_reply(
                    ctx,
                    event,
                    self._render_text(
                        self._invalid_prize_message_template,
                        {"prefix": current_command_prefix(), "command": self._command, "example": "100"},
                    ),
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

    async def _edit_trigger_or_reply(self, ctx: PluginContext, event: Any, text: str) -> None:
        if ctx.client:
            try:
                await ctx.client.edit_message(event.chat_id, event.id, text, parse_mode="html")
                return
            except Exception:
                pass
        await event.reply(text, parse_mode="html")

    def _new_round(self, prize: int, *, timeout: int | None = None) -> RoundState:
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
                timeout=timeout or self._timeout,
                last_guess_at={},
            )

    def _render_round_text(self, rd: RoundState, include_guide: bool) -> str:
        vars_map = {
            "version": MANIFEST.version,
            "prefix": current_command_prefix(),
            "command": self._command,
            "force_stop_command": self._force_stop_command,
            "target_sum": rd.target_sum,
            "answer_index": rd.answer_index,
            "prize": rd.prize,
            "timeout": rd.timeout,
            "guess_cooldown": self._guess_cooldown,
            "winner": rd.winner_name,
            "elapsed": "0.0",
            "example": "100",
        }
        template = self._round_message_template
        template_vars = vars_map
        if any(placeholder in template for placeholder in ("{title}", "{target_line}", "{guide_line}", "{reward_line}")):
            title = self._render_text(self._template_title, vars_map)
            target_line = self._render_text(self._template_target_line, vars_map)
            guide_line = self._render_text(self._template_guide_line, vars_map)
            reward_line = self._render_text(self._template_reward_line, vars_map)
            template_vars = {
                **vars_map,
                "title": title,
                "target_line": target_line,
                "guide_line": guide_line if include_guide else "",
                "reward_line": reward_line,
            }
            if not include_guide:
                return f"<b>{title}</b>\n\n{target_line}"

        return self._render_text(template, template_vars)

    def _render_interaction_success(self, rd: RoundState, payout_account: str, payout_mode: str) -> str:
        winner = escape(rd.winner_name or "玩家")
        account_holder = escape(payout_account)
        payout_line = (
            f"奖金将由 {account_holder} 账号自动发放。"
            if payout_mode == "auto"
            else f"请由 {account_holder} 人工回复赢家发放奖金。"
        )
        elapsed = max(0.0, time.monotonic() - rd.started_at)
        return (
            f"答对了：{winner}\n"
            f"题目：九宫格竞猜，目标点数 {rd.target_sum}，答案第 {rd.answer_index} 格\n"
            f"用时：{elapsed:.1f}s\n"
            f"奖金：{rd.prize}\n"
            f"{payout_line}"
        )

    def _interaction_event_type(self, payload: dict[str, Any]) -> str:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        return str(
            source.get("type")
            or trigger.get("type")
            or event.get("type")
            or payload.get("event_type")
            or ""
        ).strip()

    def _interaction_message_text(self, payload: dict[str, Any]) -> str:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        return str(payload.get("message_text") or source.get("text") or event.get("text") or "").strip()

    def _interaction_actor_name(self, payload: dict[str, Any]) -> str:
        actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        return str(
            payload.get("sender_name")
            or actor.get("display_name")
            or event.get("display_name")
            or payload.get("payer_name")
            or "玩家"
        ).strip() or "玩家"

    def _interaction_payout_info(self, payload: dict[str, Any]) -> tuple[str, str]:
        settlement = payload.get("settlement") if isinstance(payload.get("settlement"), dict) else {}
        payout_account = str(payload.get("payout_account_label") or settlement.get("payout_account_label") or "账号持有者").strip()
        payout_mode = str(payload.get("payout_mode") or settlement.get("mode") or "manual").strip().lower()
        return payout_account or "账号持有者", payout_mode

    def _render_timeout_announcement(self, rd: RoundState) -> str:
        return self._render_text(
            self._timeout_message_template,
            {
                "answer_index": rd.answer_index,
                "target_sum": rd.target_sum,
            },
        )

    def _render_text(self, template: str, vars_map: dict[str, Any]) -> str:
        try:
            return template.format_map(vars_map)
        except Exception:
            return template

    def _round_expired(self, rd: RoundState) -> bool:
        return time.monotonic() >= rd.started_at + max(1, int(rd.timeout or self._timeout or 90))

    def _parse_grid_pick(self, text: str) -> int | None:
        if text.isdigit():
            pick = int(text)
            return pick if 1 <= pick <= 9 else None
        match = re.search(r"(?<!\d)([1-9])(?!\d)", text)
        return int(match.group(1)) if match else None

    def _payload_chat_id(self, payload: dict[str, Any]) -> int:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        return self._int_value(payload.get("chat_id") or source.get("chat_id") or event.get("chat_id")) or 0

    def _payload_message_id(self, payload: dict[str, Any]) -> int | None:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        value = payload.get("message_id") or source.get("message_id") or payload.get("source_message_id") or event.get("message_id")
        parsed = self._positive_int(value, 0, minimum=0)
        return parsed or None

    def _positive_int(self, value: Any, default: int, *, minimum: int, maximum: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    def _int_value(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        prefix = current_command_prefix()
        if not text or text.startswith("/") or (prefix and text.startswith(prefix)):
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
            name = public_entity_display_name(sender, default="玩家")
            rd.winner_name = name
            rd.winner_id = int(getattr(sender, "id", 0) or 0)
            rd.winner_message_id = int(getattr(event, "id", 0) or 0) or None

        elapsed = time.monotonic() - rd.started_at
        await self._send_prize_reply(ctx, event, chat_id, rd)
        await self._edit_round_message(
            ctx,
            chat_id,
            rd,
            "\n\n" + self._render_text(
                self._success_message_template,
                {
                    "winner": rd.winner_name,
                    "answer_index": rd.answer_index,
                    "target_sum": rd.target_sum,
                    "elapsed": f"{elapsed:.1f}",
                    "prize": rd.prize,
                },
            ),
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
        text = self._render_text(self._prize_message_template, {"prize": rd.prize})
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
                f"[dice_grid_hunt] chat {chat_id} 超时；答案格：{rd.answer_index}；骰子：{_fmt_roll(answer_roll)}；点数和：{rd.target_sum}",
            )
        if ctx.client and rd.message_id:
            await self._edit_round_message(
                ctx,
                chat_id,
                rd,
                "\n\n" + self._render_text(
                    self._timeout_message_template,
                    {
                        "answer_index": rd.answer_index,
                        "target_sum": rd.target_sum,
                    },
                ),
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
