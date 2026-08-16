"""可配置的 Todo 自然语言提醒。

所有发送、调度和状态都经过 TelePilot facade；插件不直接调用 Telegram API。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.worker.plugins.base import Plugin, PluginContext, register
from telethon.tl.types import PeerUser

from .manifest import DEFAULT_COMMAND, PLUGIN_VERSION

TASK_PREFIX = "task:"
ENTRY_KEY = "todo_reminder"
DEFAULT_REPEAT_MINUTES = 5
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_TEMPLATE = "{mention} 提醒：{todo}"
DEFAULT_COMPLETION_KEYWORDS = ("已完成", "完成")
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _cn_number(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw == "半":
        return 0.5
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return float(raw)
    if "点" in raw:
        left, right = raw.split("点", 1)
        whole = _cn_number(left) or 0
        decimals = "".join(str(CN_DIGITS.get(char, "")) for char in right)
        return float(f"{int(whole)}.{decimals}") if decimals else float(whole)
    total = 0
    section = 0
    number = 0
    for char in raw:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return float(total + section + number)


def _timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or DEFAULT_TIMEZONE))
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _clock_value(text: str) -> tuple[int, int, int] | None:
    match = re.match(
        r"(?P<hour>[零〇一二两三四五六七八九十百千万\d]+)\s*(?:点|:)\s*(?P<minute>[零〇一二两三四五六七八九十百千万\d]+)?(?:分)?",
        text,
    )
    if not match:
        return None
    hour = _cn_number(match.group("hour"))
    minute_raw = match.group("minute")
    minute = _cn_number(minute_raw) if minute_raw else 0
    if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    consumed = match.end()
    if text[consumed:consumed + 1] == "半":
        minute = 30
        consumed += 1
    return int(hour), int(minute), consumed


def parse_natural_time(text: str, *, now: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> tuple[datetime, str] | None:
    """解析时间开头，返回带时区的 UTC 时间与剩余任务文本。"""
    tz = _timezone(timezone_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    source = str(text or "").strip()
    relative = re.match(
        r"^(?P<value>半|[零〇一二两三四五六七八九十百千万\d]+(?:\.\d+)?)\s*(?P<unit>秒钟?|分钟?|小时?|天|周|星期)\s*(?:之后|以后|后)",
        source,
    )
    if relative:
        amount = _cn_number(relative.group("value"))
        unit = relative.group("unit")
        if amount is None:
            return None
        seconds = amount * {"秒": 1, "秒钟": 1, "分钟": 60, "分": 60, "小时": 3600, "小时钟": 3600, "天": 86400, "周": 604800, "星期": 604800}[unit]
        return (local_now + timedelta(seconds=seconds)).astimezone(timezone.utc), source[relative.end():].strip()

    iso = re.match(r"^(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?:[ T]+|\s+)(?P<clock>.*)$", source)
    date_parts: tuple[int, int, int] | None = None
    date_mode = "clock"
    remainder = source
    if iso:
        date_parts = (int(iso.group("y")), int(iso.group("m")), int(iso.group("d")))
        date_mode = "absolute"
        remainder = iso.group("clock").strip()
    else:
        month_day = re.match(r"^(?P<m>\d{1,2})月(?P<d>\d{1,2})日?(?:\s+|$)(?P<clock>.*)$", source)
        if month_day:
            date_parts = (local_now.year, int(month_day.group("m")), int(month_day.group("d")))
            date_mode = "month_day"
            remainder = month_day.group("clock").strip()

    day_offset = 0
    period = ""
    if date_parts is None:
        day_match = re.match(r"^(?P<day>大后天|后天|明天|今天|今晚|明晚|明早)(?P<rest>.*)$", source)
        if day_match:
            date_mode = "named_day"
            day_word = day_match.group("day")
            day_offset = {"今天": 0, "今晚": 0, "明天": 1, "明晚": 1, "明早": 1, "后天": 2, "大后天": 3}[day_word]
            period = {"今晚": "晚上", "明晚": "晚上", "明早": "早上"}.get(day_word, "")
            remainder = day_match.group("rest").strip()

    period_match = re.match(r"^(上午|下午|晚上|早上|早晨|中午|凌晨)(?P<rest>.*)$", remainder)
    if period_match:
        period = period_match.group(1)
        remainder = period_match.group("rest").strip()
    clock = _clock_value(remainder)
    if clock is None:
        return None
    hour, minute, consumed = clock
    if period in {"下午", "晚上", "今晚", "明晚"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period in {"凌晨", "早上", "早晨", "明早"} and hour == 12:
        hour = 0
    if date_parts is None:
        base = local_now + timedelta(days=day_offset)
        date_parts = (base.year, base.month, base.day)
    try:
        candidate = datetime(*date_parts, hour, minute, tzinfo=tz)
    except ValueError:
        return None
    if date_mode == "clock" and candidate <= local_now:
        candidate += timedelta(days=1)
    elif date_mode == "month_day" and candidate <= local_now:
        for next_year in range(local_now.year + 1, local_now.year + 9):
            try:
                candidate = candidate.replace(year=next_year)
                break
            except ValueError:
                continue
    return candidate.astimezone(timezone.utc), remainder[consumed:].strip()


def parse_reminder_request(text: str, *, now: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> tuple[datetime, str, bool] | None:
    parsed = parse_natural_time(text, now=now, timezone_name=timezone_name)
    if parsed is None:
        return None
    fire_at, remainder = parsed
    match = re.match(r"^提醒(?:我|自己|他|她|ta|TA)?\s*(?P<todo>.+)$", remainder, re.S)
    if match:
        target_self = remainder.startswith(("提醒我", "提醒自己"))
        todo = match.group("todo").strip()
    else:
        target_self = False
        todo = remainder.strip(" ，,：:。")
    if not todo:
        return None
    return fire_at, todo, target_self


def _requests_other_target(remainder: str) -> bool:
    value = str(remainder or "").lstrip()
    return value.startswith(("提醒他", "提醒她")) or value.lower().startswith("提醒ta")


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _nonzero_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number != 0 else None


def _clean_command(value: Any) -> str:
    command = str(value or "").strip().lstrip("/")
    return command or DEFAULT_COMMAND


def _repeat_minutes(config: Mapping[str, Any]) -> int:
    try:
        value = int(config.get("repeat_interval_minutes", DEFAULT_REPEAT_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_REPEAT_MINUTES
    return max(1, min(1440, value))


def _keywords(config: Mapping[str, Any]) -> tuple[str, ...]:
    raw = str(config.get("completion_keywords", "")).replace("，", ",").replace("\n", ",")
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or DEFAULT_COMPLETION_KEYWORDS


def _chat_id(payload: Mapping[str, Any]) -> int | None:
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    chat = payload.get("chat") if isinstance(payload.get("chat"), Mapping) else {}
    return _nonzero_int(message.get("chat_id") or chat.get("id"))


def _message_id(payload: Mapping[str, Any]) -> int | None:
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    return _positive_int(message.get("message_id") or message.get("id"))


def _reply_to_id(payload: Mapping[str, Any]) -> int | None:
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    return _positive_int(message.get("reply_to_message_id") or message.get("reply_to_msg_id"))


def _sender_id(payload: Mapping[str, Any]) -> int | None:
    sender = payload.get("sender") if isinstance(payload.get("sender"), Mapping) else {}
    actor = payload.get("actor") if isinstance(payload.get("actor"), Mapping) else {}
    return _positive_int(sender.get("user_id") or sender.get("id") or actor.get("user_id") or actor.get("id"))


def _message_text(payload: Mapping[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    return str(message.get("text") or message.get("caption") or "").strip()


@register
class TodoReminderPlugin(Plugin):
    key = "todo_reminder"
    display_name = "Todo 提醒"
    owner_only = True
    message_channels = {"incoming", "outgoing"}
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self.commands: dict[str, Any] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._me_id: int | None = None
        self._me_username = ""

    async def on_startup(self, ctx: PluginContext) -> None:
        self._command = _clean_command((ctx.config or {}).get("command"))
        self.commands = {"undo": self._handle_undo_command}
        if self._command != "undo":
            self.commands[self._command] = self._handle_configured_command
        await self._load_me(ctx)
        self._tasks.clear()
        if ctx.storage is not None and getattr(ctx.storage, "available", False):
            stored = await ctx.storage.get_all()
            for key, state in stored.items():
                if key.startswith(TASK_PREFIX) and isinstance(state, dict) and state.get("id"):
                    self._tasks[str(state["id"])] = dict(state)
                    self._schedule(ctx, self._tasks[str(state["id"])])
        elif ctx.log:
            await ctx.log("warn", "[todo_reminder] ctx.storage 不可用，重启后无法恢复提醒")
        if ctx.log:
            await ctx.log("info", f"[todo_reminder] v{PLUGIN_VERSION} 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.scheduler is not None:
            ctx.scheduler.unregister_all()
        self._tasks.clear()

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        source = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
        if source.get("type") not in (None, "message"):
            return None
        text = _message_text(payload)
        if not text or text not in _keywords(ctx.config or {}):
            return None
        chat_id = _chat_id(payload)
        sender_id = _sender_id(payload)
        reply_id = _reply_to_id(payload)
        if chat_id is None or sender_id is None or reply_id is None:
            return None
        for task in list(self._tasks.values()):
            if int(task.get("chat_id", 0)) != chat_id or int(task.get("target_user_id", 0)) != sender_id:
                continue
            keys = task.get("reminder_message_keys") if isinstance(task.get("reminder_message_keys"), list) else []
            matched = False
            for key in keys:
                if await self._read_saved_message_id(ctx, str(key)) == reply_id:
                    matched = True
                    break
            if matched:
                await self._complete_task(ctx, str(task["id"]))
        return None

    async def _load_me(self, ctx: PluginContext) -> None:
        client = getattr(ctx, "client", None)
        get_me = getattr(client, "get_me", None) if client else None
        if callable(get_me):
            try:
                me = await get_me()
                self._me_id = _positive_int(getattr(me, "id", None))
                self._me_username = str(getattr(me, "username", "") or "").lstrip("@")
            except Exception:
                pass

    async def _reply_target(self, ctx: PluginContext, chat_id: int, message_id: int) -> dict[str, Any] | None:
        client = getattr(ctx, "client", None)
        get_messages = getattr(client, "get_messages", None) if client else None
        if not callable(get_messages):
            return None
        try:
            message = await get_messages(chat_id, ids=message_id)
        except Exception:
            return None
        if isinstance(message, list):
            message = message[0] if message else None
        from_id = getattr(message, "from_id", None)
        if not isinstance(from_id, PeerUser):
            return None
        user_id = _positive_int(getattr(from_id, "user_id", None))
        if user_id is None:
            return None
        sender = getattr(message, "sender", None)
        username = str(getattr(sender, "username", "") or "").lstrip("@")
        name = " ".join(v for v in (str(getattr(sender, "first_name", "") or "").strip(), str(getattr(sender, "last_name", "") or "").strip()) if v)
        return {"user_id": user_id, "username": username, "display_name": name or username or str(user_id)}

    async def _handle_configured_command(self, _client: Any, event: Any, args: list[str], _account_id: int, ctx: PluginContext) -> None:
        chat_id = _nonzero_int(getattr(event, "chat_id", None))
        message_id = _positive_int(getattr(event, "id", None))
        raw = " ".join(str(item) for item in args).strip()
        if not raw:
            event_text = str(getattr(event, "raw_text", "") or getattr(event, "text", "") or "").strip()
            raw = event_text.split(None, 1)[1].strip() if event_text and len(event_text.split(None, 1)) == 2 else ""
        await self._handle_command_text(ctx, chat_id, message_id, raw, event)

    async def _handle_undo_command(self, _client: Any, event: Any, args: list[str], _account_id: int, ctx: PluginContext) -> None:
        chat_id = _nonzero_int(getattr(event, "chat_id", None))
        message_id = _positive_int(getattr(event, "id", None))
        task_id = str(args[0] if args else "").strip().lstrip("#")
        ok = await self._cancel_task(ctx, task_id) if task_id else False
        await ctx.messages.send(
            chat_id=chat_id,
            reply_to_message_id=message_id,
            text="已取消提醒。" if ok else "未找到该提醒 ID，请先发送 todo 列表。",
        )

    async def _handle_command_text(self, ctx: PluginContext, chat_id: int | None, message_id: int | None, raw: str, event: Any) -> None:
        if chat_id is None:
            return
        head, _, rest = raw.partition(" ")
        if head in {"列表", "list", "ls"}:
            await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text=self._list_text(chat_id))
            return
        if head in {"取消", "cancel", "del", "delete"}:
            task_id = rest.strip().lstrip("#")
            ok = await self._cancel_task(ctx, task_id) if task_id else False
            await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text="已取消提醒。" if ok else "未找到该提醒 ID，请先发送 todo 列表。")
            return
        if head in {"帮助", "help", "?"} or not raw:
            await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text=self._help_text())
            return
        parsed = parse_reminder_request(raw, timezone_name=str((ctx.config or {}).get("timezone") or DEFAULT_TIMEZONE))
        if parsed is None:
            await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text=f"无法识别时间。示例：{self._command} 五分钟后提醒我喝水")
            return
        fire_at, todo, target_self = parsed
        if fire_at <= datetime.now(timezone.utc):
            await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text="提醒时间已经过去，请指定一个未来时间。")
            return
        reply_to_message_id = getattr(event, "reply_to_msg_id", None) or getattr(event, "reply_to_message_id", None)
        if reply_to_message_id is None:
            reply = getattr(event, "message", None)
            reply_to_message_id = getattr(reply, "reply_to_msg_id", None) or getattr(reply, "reply_to_message_id", None)
        target = None if target_self or reply_to_message_id is None else await self._reply_target(ctx, chat_id, int(reply_to_message_id))
        if target is None:
            if not target_self and reply_to_message_id is not None:
                await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text="无法读取被回复用户；频道、匿名管理员或非用户消息不能作为提醒目标。")
                return
            parsed_time = parse_natural_time(raw, timezone_name=str((ctx.config or {}).get("timezone") or DEFAULT_TIMEZONE))
            wants_other = bool(parsed_time and _requests_other_target(parsed_time[1]))
            if wants_other:
                await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text="“提醒他/她”需要回复目标用户的消息后再发送指令。")
                return
            target = {"user_id": self._me_id or 0, "username": self._me_username, "display_name": "自己"}
        if not target.get("user_id"):
            await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text="暂时无法解析当前账号 ID，请稍后重试。")
            return
        target_is_self = bool(self._me_id and int(target["user_id"]) == self._me_id)
        task_id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc)
        state = {
            "id": task_id, "chat_id": chat_id, "target_user_id": int(target["user_id"]),
            "target_username": str(target.get("username") or ""), "target_display_name": str(target.get("display_name") or ""),
            "target_is_self": target_is_self, "todo": todo, "fire_at": fire_at.isoformat(),
            "repeat_interval_minutes": _repeat_minutes(ctx.config or {}), "reply_to_message_id": int(reply_to_message_id) if reply_to_message_id is not None and not target_is_self else None,
            "reminder_count": 0, "reminder_message_keys": [], "created_at": now.isoformat(),
        }
        self._tasks[task_id] = state
        await self._save_task(ctx, state)
        self._schedule(ctx, state)
        local_fire = fire_at.astimezone(_timezone((ctx.config or {}).get("timezone")))
        await ctx.messages.send(chat_id=chat_id, reply_to_message_id=message_id, text=f"已创建提醒 #{task_id}，将在 {local_fire.strftime('%Y-%m-%d %H:%M')} 触发。")

    def _schedule(self, ctx: PluginContext, state: dict[str, Any]) -> None:
        if ctx.scheduler is None:
            return
        task_id = str(state["id"])
        try:
            fire_at = datetime.fromisoformat(str(state.get("fire_at")))
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=timezone.utc)
        except ValueError:
            fire_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        if fire_at <= datetime.now(timezone.utc):
            fire_at = datetime.now(timezone.utc) + timedelta(seconds=1)
            state["fire_at"] = fire_at.isoformat()

        async def callback(_job: Any) -> None:
            await self._fire_task(ctx, task_id)

        ctx.scheduler.register(f"todo_reminder_{task_id}", {"kind": "once", "fire_at": fire_at.astimezone(timezone.utc).isoformat()}, callback, replace=True)

    async def _fire_task(self, ctx: PluginContext, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if not state:
            return
        count = int(state.get("reminder_count") or 0) + 1
        key = f"reminder:{task_id}:{count}"
        username = str(state.get("target_username") or "").strip().lstrip("@")
        display_name = str(state.get("target_display_name") or state.get("target_user_id") or "用户")
        template = str((ctx.config or {}).get("reminder_template") or DEFAULT_TEMPLATE)
        is_self = bool(state.get("target_is_self"))
        if is_self:
            mention = f'<a href="tg://user?id={int(state["target_user_id"])}">{html_escape(display_name)}</a>'
            todo = html_escape(str(state.get("todo") or ""))
        else:
            label = f"@{username}" if username else display_name
            mention = f'<a href="tg://user?id={int(state["target_user_id"])}">{html_escape(label)}</a>'
            todo = html_escape(str(state.get("todo") or ""))
        try:
            text = template.format(mention=mention, todo=todo, id=task_id)
        except (KeyError, ValueError):
            text = DEFAULT_TEMPLATE.format(mention=mention, todo=todo)
        state["reminder_count"] = count
        keys = state.get("reminder_message_keys") if isinstance(state.get("reminder_message_keys"), list) else []
        state["reminder_message_keys"] = [*keys, key][-20:]
        state["last_sent_at"] = datetime.now(timezone.utc).isoformat()
        state["fire_at"] = (datetime.now(timezone.utc) + timedelta(minutes=int(state.get("repeat_interval_minutes") or DEFAULT_REPEAT_MINUTES))).isoformat()
        await self._save_task(ctx, state)
        action: dict[str, Any] = {"type": "send_message", "chat_id": int(state["chat_id"]), "text": text, "parse_mode": "html", "save_message_id_key": key}
        if is_self:
            action["send_via"] = "interaction_bot"
        else:
            action.update({"send_via": "userbot_reply", "reply_to_message_id": state.get("reply_to_message_id"), "reply_to_user_id": int(state["target_user_id"]), "reply_to_username": state.get("target_username") or None})
        try:
            await ctx.messages.apply([action], entry_key=ENTRY_KEY)
        finally:
            if task_id in self._tasks:
                self._schedule(ctx, state)

    async def _complete_task(self, ctx: PluginContext, task_id: str) -> None:
        state = self._tasks.pop(task_id, None)
        if not state:
            return
        if ctx.scheduler is not None:
            ctx.scheduler.unregister(f"todo_reminder_{task_id}")
        for key in state.get("reminder_message_keys") or []:
            await self._delete_saved_message_id(ctx, str(key))
        await self._delete_task(ctx, task_id)

    async def _cancel_task(self, ctx: PluginContext, task_id: str) -> bool:
        if task_id not in self._tasks:
            return False
        await self._complete_task(ctx, task_id)
        return True

    async def _save_task(self, ctx: PluginContext, state: dict[str, Any]) -> None:
        if ctx.storage is not None and getattr(ctx.storage, "available", False):
            await ctx.storage.set(f"{TASK_PREFIX}{state['id']}", state)

    async def _delete_task(self, ctx: PluginContext, task_id: str) -> None:
        if ctx.storage is not None and getattr(ctx.storage, "available", False):
            await ctx.storage.delete(f"{TASK_PREFIX}{task_id}")

    async def _read_saved_message_id(self, ctx: PluginContext, key: str) -> int | None:
        reader = getattr(ctx.messages, "read_saved_message_id", None)
        if callable(reader):
            try:
                return await reader(key)
            except Exception:
                return None
        return None

    async def _delete_saved_message_id(self, ctx: PluginContext, key: str) -> None:
        deleter = getattr(ctx.messages, "delete_saved_message_id", None)
        if callable(deleter):
            try:
                await deleter(key)
            except Exception:
                pass

    def _list_text(self, chat_id: int) -> str:
        rows = [task for task in self._tasks.values() if int(task.get("chat_id", 0)) == chat_id]
        if not rows:
            return "当前会话没有待办提醒。"
        return "待办提醒：\n" + "\n".join(f"#{item['id']}｜{item['todo']}｜下次 {item['fire_at']}" for item in rows)

    def _help_text(self) -> str:
        return f"用法：{self._command} 五分钟后提醒我喝水；回复某人的消息后发送 {self._command} 明天上午九点提醒他开会。\n列表：{self._command} 列表\n取消：undo <ID>"


__all__ = ["TodoReminderPlugin", "parse_natural_time", "parse_reminder_request"]
