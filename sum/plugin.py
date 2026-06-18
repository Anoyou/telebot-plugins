"""群消息总结远程模块。

兼容迁移 TeleBox `sum.ts` 的核心能力，但运行时遵循 TelePilot 远程模块边界：
只使用 ``ctx.client``、``ctx.scheduler`` 和已声明权限，不访问全局客户端或 raw MTProto。
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import math
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register

try:
    from app.worker.plugins.base import public_entity_display_name
except ImportError:  # pragma: no cover - older TelePilot compatibility
    def public_entity_display_name(entity: Any, *, fallback_id: int | str | None = None, default: str = "未知用户") -> str:
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


VERSION = "1.1.27"
DB_PATH = Path(__file__).with_name("summary_config.json")
URL_RE = re.compile(r"https?://[^\s\]）】>]+", re.IGNORECASE)
THINK_RE = re.compile(r"<think(?:ing)?\b[^>]*>[\s\S]*?</think(?:ing)?>", re.IGNORECASE)
WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}")
CN_BOUNDARY_RE = re.compile(
    r"(?:已经|可以|应该|可能|需要|就是|不是|没有|不要|知道|看看|直接|确实|"
    r"如果|因为|所以|但是|然后|现在|还是|感觉|进行|使用|更新|发布|包括|"
    r"这个|那个|一下|一个|什么|怎么|为什么|这里|那里|这样|那样|"
    r"今日份|今天|昨天|明天|时候|里面|来源|情况|问题|最近|消息|有效|"
    r"即可|请|我要|你要|他要|她要|我们|你们|他们|自己|任意|"
    r"[我你他她它咱您的是有在把被给对向从到和与或及并而就都也还只再又很挺太更最了得地着过吗呢吧啊呀哦])"
)
SUMMARY_MESSAGE_TEMPLATE_DEFAULT = (
    "📊 群组总结\n"
    "来源: {chat_display}\n"
    "时间: {time}\n"
    "数量: {message_count}\n\n"
    "{summary}"
)


@dataclass
class AIConfig:
    default_prompt: str = "请总结以下群聊消息的主要内容，提取关键话题和重要信息："
    default_spoiler: bool = False
    default_timeout: int = 60000
    reply_mode: bool = True
    max_output_length: int = 0
    telepilot_provider: str = ""
    telepilot_model: str = ""
    message_template: str = SUMMARY_MESSAGE_TEMPLATE_DEFAULT


@dataclass
class SummaryTask:
    id: str
    cron: str
    chat_id: str
    chat_display: str = ""
    interval: str = ""
    message_count: int = 100
    time_range: int = 0
    push_target: str = ""
    ai_prompt: str = ""
    use_spoiler: bool = False
    created_at: str = ""
    last_run_at: str = ""
    last_result: str = ""
    last_error: str = ""
    disabled: bool = False
    remark: str = ""
    managed_by_config: bool = False


@dataclass
class SummaryDB:
    seq: int = 0
    tasks: list[SummaryTask] = field(default_factory=list)
    ai_config: AIConfig = field(default_factory=AIConfig)
    default_push_target: str = ""


@dataclass
class MessageData:
    text: str
    content: str
    telegram_link: str
    urls: list[str] = field(default_factory=list)
    file_name: str = ""


def _html(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _code(value: Any) -> str:
    return f"<code>{_html(value)}</code>"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "开启", "开", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "关闭", "关", "否", ""}:
        return False
    return default


def _format_date(value: datetime | int | float | None = None) -> str:
    if value is None:
        dt = datetime.now()
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromtimestamp(float(value))
    return dt.strftime("%Y-%m-%d %H:%M")


def _duration_to_seconds(raw: str) -> int:
    text = str(raw or "").strip().lower()
    match = re.fullmatch(r"(\d+)([mhd])", text)
    if not match:
        return 0
    value = int(match.group(1))
    unit = match.group(2)
    if value <= 0:
        return 0
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    return value * 86400


def _normalize_cli_token(raw: str) -> str:
    text = str(raw or "").strip()
    # 兼容全角/长短横线输入
    for ch in ("—", "–", "－", "﹣", "−"):
        text = text.replace(ch, "-")
    return text


def _raw_text_wants_cloud(raw_text: str) -> bool:
    text = _normalize_cli_token(raw_text).lower()
    return bool(re.search(r"(^|\s)(--?cy|cy|词云)(\s|$)", text))


def _safe_filename(value: str) -> str:
    base = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return base or "sum_wordcloud"


def _command_prefix() -> str:
    return current_command_prefix(fallback=",")


def _is_noisy_chinese_word(word: str) -> bool:
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,}", word):
        return False
    if len(word) > 12:
        return True
    if re.search(r"(?:认转账联|账联动是|付费娱|今日份阳|对收款|心里已|经有答案|答案了|了的|已经有)", word):
        return True
    if re.search(r"[我你他她它咱您的是有在把被给对向从到和与或及并而就都也还只再又很挺太更最了得地着过吗呢吧啊呀哦]", word[1:-1]):
        return True
    if re.search(r"(?:的|了|是|有|在|给|把|被|和|与|就|都|也|还|吗|呢|吧)$", word):
        return True
    if re.search(r"^(?:我|你|他|她|它|咱|您|的|了|是|有|在|给|把|被|和|与|就|都|也|还|请)", word):
        return True
    return False


def _event_chat_id(event: Any) -> int:
    raw = getattr(event, "chat_id", None)
    if isinstance(raw, int):
        return int(raw)
    channel_id = getattr(raw, "channel_id", None)
    if channel_id is not None:
        return int(f"-100{channel_id}")
    chat_id = getattr(raw, "chat_id", None)
    if chat_id is not None:
        return -int(chat_id)
    user_id = getattr(raw, "user_id", None)
    if user_id is not None:
        return int(user_id)
    return int(raw or 0)


def _event_message_id(event: Any) -> int | None:
    raw = getattr(getattr(event, "message", event), "id", None) or getattr(event, "id", None)
    return _int(raw) or None


def _event_text(event: Any) -> str:
    msg = getattr(event, "message", event)
    return str(
        getattr(event, "raw_text", None)
        or getattr(msg, "raw_text", None)
        or getattr(msg, "message", None)
        or ""
    )


def _parse_chat_identifier(raw: str) -> str:
    value = raw.strip()
    if re.fullmatch(r"-?\d+", value):
        return value
    private_link = re.search(r"(?:https?://)?t\.me/c/(\d+)", value)
    if private_link:
        return f"-100{private_link.group(1)}"
    public_link = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)", value)
    if public_link:
        return public_link.group(1)
    if value.startswith("@"):
        return value[1:]
    return value


def _is_placeholder_chat_identifier(raw: Any) -> bool:
    normalized = re.sub(r"[\s<>【】\[\]（）()]+", "", str(raw or "").strip().lower())
    return normalized in {"", "群组id", "群id", "chatid", "groupid", "群组标识", "真实群id", "群组"}


def _build_message_link(chat_id: str, message_id: int, username: str = "") -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    numeric = str(chat_id).replace("-100", "", 1)
    return f"https://t.me/c/{numeric}/{message_id}"


def _extract_file_name(message: Any) -> str:
    media = getattr(message, "media", None)
    if media is None:
        return ""
    doc = getattr(media, "document", None)
    if doc is not None:
        for attr in getattr(doc, "attributes", []) or []:
            file_name = getattr(attr, "file_name", None) or getattr(attr, "fileName", None)
            if file_name:
                return str(file_name)
        mime = getattr(doc, "mime_type", None) or getattr(doc, "mimeType", None)
        return f"[{mime}]" if mime else "[文件]"
    if getattr(media, "photo", None) is not None or getattr(media, "className", "") == "MessageMediaPhoto":
        return "[图片]"
    return ""


def _extract_entity_urls(message: Any, text: str) -> list[str]:
    urls: list[str] = []
    for entity in getattr(message, "entities", []) or []:
        url = getattr(entity, "url", None)
        if url:
            urls.append(str(url))
            continue
        offset = getattr(entity, "offset", None)
        length = getattr(entity, "length", None)
        class_name = str(getattr(entity, "className", "") or type(entity).__name__)
        if offset is not None and length is not None and class_name.endswith("Url"):
            urls.append(text[int(offset): int(offset) + int(length)])
    urls.extend(URL_RE.findall(text or ""))
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _default_db() -> SummaryDB:
    return SummaryDB()


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _telegram_target(value: Any) -> Any:
    if isinstance(value, str):
        target = value.strip()
        if re.fullmatch(r"-?\d+", target):
            return int(target)
        if target.startswith("@"):
            return target[1:]
        return target
    return value


def _target_candidates(*values: Any) -> list[Any]:
    out: list[Any] = []
    seen: set[tuple[str, str]] = set()

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
            return
        if isinstance(value, str) and not value.strip():
            return
        key = (type(value).__name__, str(value))
        if key in seen:
            return
        seen.add(key)
        out.append(value)

    for value in values:
        add(value)
    return out


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _is_entity_lookup_error(exc: BaseException) -> bool:
    text = str(exc)
    return (
        "Cannot find any entity corresponding" in text
        or "Could not find the input entity" in text
    )


def _chat_lookup_error(chat_id: Any, exc: BaseException) -> RuntimeError:
    return RuntimeError(
        f"无法解析聊天 {chat_id}。请确认当前账号仍在该群/频道内；"
        "如果是定时任务，请优先使用公开 @用户名或 t.me 链接创建任务。"
        f"原始错误：{exc}"
    )


@register
class SummaryPlugin(Plugin):
    key = "sum"
    display_name = "群消息总结"
    message_channels = {"outgoing"}
    owner_only = True
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "sum"
        self._cfg: dict[str, Any] = {}
        self._scheduled: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    async def on_startup(self, ctx: PluginContext) -> None:
        self._cfg = dict(ctx.config or {})
        self._command = str(self._cfg.get("command") or "sum").strip() or "sum"
        aliases = {self._command, "总结"}
        self.commands = {alias: self._cmd_sum for alias in aliases if alias}
        await self._bootstrap_tasks(ctx)
        if ctx.log:
            await ctx.log("info", f"[sum] 已启动 v{VERSION}，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        await self._unregister_all(ctx)
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if ctx.log:
            await ctx.log("info", "[sum] 已停止")

    async def _cmd_sum(self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext) -> None:
        try:
            # 配置页保存后 worker 可能只热更新 config，不一定重建插件实例；
            # 每次命令入口都同步一次内存配置，确保模板等字段立即生效。
            self._cfg = dict(ctx.config or self._cfg or {})
            if not args:
                await self._quick_summary(event, [], ctx)
                return

            sub = str(args[0]).strip().lower()
            rest = args[1:]

            if sub in {"help", "帮助"}:
                await self._edit_or_reply(event, self._help_text(), parse_mode="html")
                return
            if sub == "prompts":
                await self._show_prompts(event)
                return
            if sub == "debug":
                await self._debug_messages(event, rest, ctx)
                return
            if sub == "add":
                await self._add_task(event, rest, ctx)
                return
            if sub in {"list", "ls"}:
                await self._list_tasks(event, ctx)
                return
            if sub in {"del", "rm"}:
                await self._delete_task(event, rest, ctx)
                return
            if sub in {"run", "now"}:
                await self._run_task_command(event, rest, ctx)
                return
            if sub == "edit":
                await self._edit_task(event, rest, ctx)
                return
            if sub in {"disable", "enable"}:
                await self._toggle_task(event, rest, ctx, enable=(sub == "enable"))
                return
            if sub in {"reorder", "sort"}:
                await self._reorder_tasks(event, ctx)
                return
            if sub == "config":
                await self._config_command(event, rest, ctx)
                return
            if self._looks_like_quick_summary_args(args):
                await self._quick_summary(event, args, ctx)
                return

            await self._edit_or_reply(event, self._help_text(), parse_mode="html")
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[sum] 命令执行失败：{type(exc).__name__}: {exc}")
            await self._edit_or_reply(event, f"❌ 错误：{_html(exc)}", parse_mode="html")

    @staticmethod
    def _looks_like_quick_summary_args(args: list[str]) -> bool:
        if not args:
            return True
        for arg in args:
            text = _normalize_cli_token(arg).lower()
            if text in {"--cy", "-cy", "cy", "词云", "--time", "-time", "time"}:
                continue
            if text.isdigit():
                continue
            if _duration_to_seconds(text) > 0:
                continue
            return False
        return True

    async def _load_db(self) -> SummaryDB:
        if not DB_PATH.exists():
            db = _default_db()
            await self._save_db(db)
            return db
        try:
            data = json.loads(DB_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        db = _default_db()
        db.seq = _int(data.get("seq"), 0)
        db.default_push_target = str(data.get("defaultPushTarget") or data.get("default_push_target") or "")
        ai = data.get("aiConfig") or data.get("ai_config") or {}
        db.ai_config.default_prompt = str(ai.get("default_prompt") or db.ai_config.default_prompt)
        db.ai_config.default_spoiler = _bool(ai.get("default_spoiler"), db.ai_config.default_spoiler)
        db.ai_config.default_timeout = _int(ai.get("default_timeout"), db.ai_config.default_timeout)
        db.ai_config.reply_mode = _bool(ai.get("reply_mode"), db.ai_config.reply_mode)
        db.ai_config.max_output_length = _int(ai.get("max_output_length"), db.ai_config.max_output_length)
        db.ai_config.telepilot_provider = str(ai.get("telepilot_provider") or "")
        db.ai_config.telepilot_model = str(ai.get("telepilot_model") or "")
        db.ai_config.message_template = str(ai.get("message_template") or db.ai_config.message_template)
        old_telepilot = (ai.get("providers") or {}).get("telepilot") if isinstance(ai.get("providers"), dict) else None
        if isinstance(old_telepilot, dict):
            db.ai_config.telepilot_provider = db.ai_config.telepilot_provider or str(
                old_telepilot.get("base_url") or old_telepilot.get("baseUrl") or ""
            )
            db.ai_config.telepilot_model = db.ai_config.telepilot_model or str(old_telepilot.get("model") or "")
        db.tasks = [self._coerce_task(item) for item in data.get("tasks", []) if isinstance(item, dict)]
        self._merge_runtime_config(db)
        return db

    async def _save_db(self, db: SummaryDB) -> None:
        payload = {
            "seq": db.seq,
            "tasks": [self._task_to_json(t) for t in db.tasks],
            "aiConfig": {
                "default_prompt": db.ai_config.default_prompt,
                "default_spoiler": db.ai_config.default_spoiler,
                "default_timeout": db.ai_config.default_timeout,
                "reply_mode": db.ai_config.reply_mode,
                "max_output_length": db.ai_config.max_output_length,
                "telepilot_provider": db.ai_config.telepilot_provider,
                "telepilot_model": db.ai_config.telepilot_model,
                "message_template": db.ai_config.message_template,
            },
            "defaultPushTarget": db.default_push_target,
        }
        DB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _merge_runtime_config(self, db: SummaryDB) -> None:
        cfg = self._cfg
        db.ai_config.default_prompt = str(cfg.get("default_prompt") or db.ai_config.default_prompt)
        db.ai_config.default_spoiler = _bool(cfg.get("default_spoiler"), db.ai_config.default_spoiler)
        db.ai_config.default_timeout = max(10, _int(cfg.get("timeout_seconds"), 60)) * 1000
        db.ai_config.reply_mode = _bool(cfg.get("reply_mode"), db.ai_config.reply_mode)
        db.ai_config.max_output_length = max(0, _int(cfg.get("max_output_length"), db.ai_config.max_output_length))
        db.default_push_target = str(cfg.get("default_push_target") or db.default_push_target or "")
        db.ai_config.telepilot_provider = str(cfg.get("telepilot_provider") or db.ai_config.telepilot_provider or "").strip()
        db.ai_config.telepilot_model = str(cfg.get("telepilot_model") or db.ai_config.telepilot_model or "").strip()
        db.ai_config.message_template = str(cfg.get("message_template") or db.ai_config.message_template or "").strip() or SUMMARY_MESSAGE_TEMPLATE_DEFAULT
        self._merge_configured_tasks(db, cfg.get("scheduled_tasks_json"))

    def _merge_configured_tasks(self, db: SummaryDB, raw_value: Any) -> None:
        raw = str(raw_value or "").strip()
        previous = {task.id: task for task in db.tasks if task.managed_by_config}
        db.tasks = [task for task in db.tasks if not task.managed_by_config]
        if not raw or raw == "[]":
            return
        try:
            parsed = json.loads(raw)
        except Exception:
            db.tasks.append(
                SummaryTask(
                    id="cfg:error",
                    cron="",
                    chat_id="",
                    disabled=True,
                    managed_by_config=True,
                    last_error="配置页 scheduled_tasks_json 不是有效 JSON。",
                    remark="配置页定时任务解析失败",
                )
            )
            return
        items = parsed.get("tasks", parsed) if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            return
        configured: list[SummaryTask] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            task = self._coerce_config_task(item, index)
            if task:
                old = previous.get(task.id)
                if old:
                    task.last_run_at = old.last_run_at
                    task.last_result = old.last_result
                    task.last_error = task.last_error or old.last_error
                configured.append(task)
        db.tasks.extend(configured)

    def _coerce_config_task(self, item: dict[str, Any], index: int) -> SummaryTask | None:
        chat_raw = str(item.get("chatId") or item.get("chat_id") or item.get("chat") or "").strip()
        task_id = str(item.get("id") or index).strip()
        task_id = task_id if task_id.startswith("cfg:") else f"cfg:{task_id}"
        if not chat_raw:
            return SummaryTask(
                id=task_id,
                cron="",
                chat_id="",
                disabled=True,
                managed_by_config=True,
                last_error="配置页定时任务缺少 chatId。",
                remark=str(item.get("remark") or "配置页定时任务缺少 chatId"),
            )
        chat_id = _parse_chat_identifier(chat_raw)
        cron = str(item.get("cron") or "").strip()
        interval = str(item.get("interval") or "").strip()
        if not cron and interval:
            cron, interval, _ = self._parse_interval([interval])
        if cron and not interval:
            interval = cron
        disabled = _bool(item.get("disabled"), False)
        last_error = ""
        if not cron:
            disabled = True
            last_error = "配置页定时任务缺少 interval 或 cron。"
        if _is_placeholder_chat_identifier(chat_id):
            disabled = True
            last_error = "请把 chatId 改成真实群 ID、@用户名或 t.me 链接。"
        return SummaryTask(
            id=task_id,
            cron=cron,
            chat_id=chat_id,
            chat_display=str(item.get("chatDisplay") or item.get("chat_display") or chat_raw),
            interval=interval,
            message_count=max(1, _int(item.get("messageCount") or item.get("message_count") or item.get("count"), 100)),
            time_range=max(0, _int(item.get("timeRange") or item.get("time_range") or item.get("hours"), 0)),
            push_target=str(item.get("pushTarget") or item.get("push_target") or item.get("push") or ""),
            ai_prompt=str(item.get("prompt") or item.get("aiPrompt") or item.get("ai_prompt") or ""),
            use_spoiler=_bool(item.get("useSpoiler") if "useSpoiler" in item else item.get("spoiler"), False),
            created_at=str(item.get("createdAt") or item.get("created_at") or int(time.time() * 1000)),
            last_error=last_error,
            disabled=disabled,
            remark=str(item.get("remark") or "配置页定时任务"),
            managed_by_config=True,
        )

    @staticmethod
    def _coerce_task(item: dict[str, Any]) -> SummaryTask:
        return SummaryTask(
            id=str(item.get("id") or ""),
            cron=str(item.get("cron") or ""),
            chat_id=str(item.get("chatId") or item.get("chat_id") or ""),
            chat_display=str(item.get("chatDisplay") or item.get("chat_display") or ""),
            interval=str(item.get("interval") or ""),
            message_count=max(1, _int(item.get("messageCount") or item.get("message_count"), 100)),
            time_range=max(0, _int(item.get("timeRange") or item.get("time_range"), 0)),
            push_target=str(item.get("pushTarget") or item.get("push_target") or ""),
            ai_prompt=str(item.get("aiPrompt") or item.get("ai_prompt") or ""),
            use_spoiler=_bool(item.get("useSpoiler") if "useSpoiler" in item else item.get("use_spoiler"), False),
            created_at=str(item.get("createdAt") or item.get("created_at") or ""),
            last_run_at=str(item.get("lastRunAt") or item.get("last_run_at") or ""),
            last_result=str(item.get("lastResult") or item.get("last_result") or ""),
            last_error=str(item.get("lastError") or item.get("last_error") or ""),
            disabled=_bool(item.get("disabled"), False),
            remark=str(item.get("remark") or ""),
            managed_by_config=_bool(item.get("managedByConfig") or item.get("managed_by_config"), False),
        )

    @staticmethod
    def _task_to_json(task: SummaryTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "cron": task.cron,
            "chatId": task.chat_id,
            "chatDisplay": task.chat_display,
            "interval": task.interval,
            "messageCount": task.message_count,
            "timeRange": task.time_range or None,
            "pushTarget": task.push_target,
            "aiPrompt": task.ai_prompt,
            "useSpoiler": task.use_spoiler,
            "createdAt": task.created_at,
            "lastRunAt": task.last_run_at,
            "lastResult": task.last_result,
            "lastError": task.last_error,
            "disabled": task.disabled,
            "remark": task.remark,
            "managedByConfig": task.managed_by_config,
        }

    async def _quick_summary(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        count = max(1, _int(self._cfg.get("default_count"), 100))
        max_count = max(10, _int(self._cfg.get("max_fetch_count"), 300))
        since_seconds = 0
        enable_cloud = False
        raw_text = _event_text(event)
        i = 0
        while i < len(args):
            arg = _normalize_cli_token(args[i]).lower()
            if arg in {"--cy", "-cy", "cy", "词云"}:
                enable_cloud = True
                i += 1
                continue
            if arg in {"--time", "-time", "time"}:
                if i + 1 >= len(args):
                    await self._edit_command_message(event, "❌ --time 需要跟时间段，例如 1h / 1d / 30m")
                    return
                since_seconds = _duration_to_seconds(_normalize_cli_token(args[i + 1]))
                if since_seconds <= 0:
                    await self._edit_command_message(event, "❌ 时间段格式无效，支持 30m / 1h / 1d")
                    return
                i += 2
                continue
            if arg.isdigit():
                count = _int(arg, count)
            else:
                duration_seconds = _duration_to_seconds(arg)
                if duration_seconds > 0:
                    since_seconds = duration_seconds
            i += 1
        if not enable_cloud and _raw_text_wants_cloud(raw_text):
            enable_cloud = True
        if ctx.log:
            await ctx.log("info", f"[sum] quick raw={raw_text!r} args={args!r} parsed count={count} since={since_seconds}s cloud={enable_cloud}")
        count = min(max(1, count), max_count)
        chat_id = str(_event_chat_id(event))
        if not chat_id or chat_id == "0":
            await self._edit_command_message(event, "❌ 无法识别当前聊天。")
            return

        await self._edit_command_message(event, "⏳ 正在获取消息...")
        chat_targets = await self._event_chat_targets(event, chat_id)
        message_data = await self._get_group_messages(ctx, chat_id, count, since_seconds=since_seconds, target=chat_targets)
        if not message_data:
            await self._edit_command_message(event, "❌ 未找到可处理的消息")
            return

        if enable_cloud:
            await self._edit_command_message(event, "⏳ 正在生成热词云...")
            cloud_ok, cloud_message = await self._maybe_send_wordcloud(ctx, event, message_data)
            if cloud_ok:
                await self._delete_command_message(ctx, event)
            else:
                await self._edit_command_message(event, f"❌ {cloud_message}")
            return

        db = await self._load_db()
        task = SummaryTask(
            id="temp",
            cron="",
            chat_id=chat_id,
            chat_display=await self._chat_display(ctx, chat_id, target=chat_targets),
            message_count=len(message_data),
            created_at=str(int(time.time() * 1000)),
            use_spoiler=db.ai_config.default_spoiler,
        )
        result = await self._summarize_messages(ctx, task, message_data, db)
        if not result["success"]:
            await self._edit_command_message(event, f"❌ {result['error']}")
            return

        summary_text, need_html = self._build_summary_text(task, str(result["result"]), db)
        await self._edit_command_message(event, summary_text, parse_mode="html" if need_html else None)

    async def _maybe_send_wordcloud(self, ctx: PluginContext, event: Any, message_data: list[MessageData]) -> tuple[bool, str]:
        image_data, error = self._render_wordcloud_png(message_data)
        if not image_data:
            return False, f"词云生成失败：{error or '未知错误'}"
        chat_id = str(_event_chat_id(event))
        caption = ""
        try:
            await self._send_photo(ctx, chat_id, image_data, caption=caption)
            return True, f"☁️ 词云已生成并发送（最近 {len(message_data)} 条有效消息）"
        except Exception as exc:
            return False, f"词云发送失败：{exc}"

    def _wordcloud_font_path(self) -> str | None:
        assets_dir = Path(__file__).resolve().parent / "assets"
        bundled_candidates = [
            assets_dir / "font.ttc",
            assets_dir / "font.otf",
            assets_dir / "font.ttf",
            assets_dir / "PingFang.ttc",
            assets_dir / "PingFangSC-Regular.ttf",
        ]
        for item in bundled_candidates:
            if item.exists():
                return str(item)
        # 使用常规系统中文字体，避免花体/装饰体影响可读性。
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
        for item in candidates:
            if Path(item).exists():
                return item
        return None

    def _collect_wordcloud_counts(self, message_data: list[MessageData]) -> Counter[str]:
        stop_words = {
            "这个", "那个", "就是", "不是", "可以", "没有", "一下", "一个", "什么", "怎么", "为什么",
            "然后", "现在", "还是", "但是", "因为", "所以", "如果", "已经", "应该", "可能", "感觉",
            "不要", "知道", "看看", "哈哈", "哈哈哈", "你们", "我们", "他们", "自己", "直接", "确实",
            "来源", "情况", "情况下", "耗时", "输入", "输出", "回复", "问题", "最近", "消息", "有效",
            "今天", "昨天", "明天", "时候", "东西", "里面", "这里", "那里", "这样", "那样", "进行",
            "使用", "需要", "更新", "主要", "内容", "新增", "版本", "发布", "包括", "所有", "不会",
            "the", "and", "for", "with", "this", "that", "you", "are", "not", "but", "from", "have",
            "http", "https", "com", "www", "telegram", "t.me", "true", "false", "null", "undefined",
            "条有效消息", "有效消息", "正在获取消息", "消息并总结", "词云已生成并发送", "热词云", "群组总结",
            "总结最近", "最近条", "最近消息", "生成并发送", "正在生成热词云",
        }
        blocked_fragments = (
            "正在获取消息", "消息并总结", "词云已生成并发送", "热词云", "群组总结", "总结最近", "条有效消息",
        )
        counts: Counter[str] = Counter()
        prefix = _command_prefix()
        for item in message_data:
            text = str(item.content or item.text or "")
            if not text:
                continue
            normalized = text.strip().lower()
            if any(fragment in normalized for fragment in blocked_fragments):
                continue
            if normalized.startswith(prefix + self._command) or normalized.startswith(prefix + "总结"):
                continue
            text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
            text = re.sub(r"[@#][\w_\u4e00-\u9fff-]+", " ", text)
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_+\-.]{1,24}", text):
                w = token.lower().strip()
                if w in stop_words or w.isdigit() or re.fullmatch(r"[a-z]{1,2}", w):
                    continue
                counts[w] += 2
            for token in re.findall(r"\d{2,}[A-Za-z%]?", text):
                w = token.lower().strip()
                if w not in stop_words:
                    counts[w] += 1
            for part in re.findall(r"[\u4e00-\u9fff]{2,}", text):
                for word, weight in self._wordcloud_chinese_candidates(part, stop_words):
                    counts[word] += weight
        return self._wordcloud_filter_counts(counts)

    @staticmethod
    def _wordcloud_chinese_candidates(part: str, stop_words: set[str]) -> list[tuple[str, int]]:
        candidates: list[tuple[str, int]] = []
        for match in re.finditer(r"[\u4e00-\u9fff]{2,}(?:模块|模式|插件|联动|转账|收款|红包|答案|回复|娱乐|消息|配置|词云|总结|用户|群组|群聊|文件|图片)", part):
            word = match.group(0)
            if word not in stop_words and not _is_noisy_chinese_word(word):
                candidates.append((word, 5 + min(len(word), 8)))
        pieces = [piece for piece in CN_BOUNDARY_RE.split(part) if piece]
        for piece in pieces:
            if CN_BOUNDARY_RE.fullmatch(piece):
                continue
            if not re.fullmatch(r"[\u4e00-\u9fff]{2,}", piece):
                continue
            if piece in stop_words or _is_noisy_chinese_word(piece):
                continue
            length = len(piece)
            if length <= 10:
                candidates.append((piece, 3 + min(length, 6)))
            else:
                for size in (10, 8, 6, 4):
                    for i in range(0, length - size + 1):
                        word = piece[i:i + size]
                        if word in stop_words or _is_noisy_chinese_word(word):
                            continue
                        edge_bonus = 1 if (i == 0 or i == length - size) else 0
                        candidates.append((word, 2 + size // 2 + edge_bonus))
        return candidates

    @staticmethod
    def _wordcloud_filter_counts(counts: Counter[str]) -> Counter[str]:
        entries = [(word, count) for word, count in counts.items() if count >= 2 and not _is_noisy_chinese_word(word)]
        entries.sort(key=lambda item: (item[1], len(item[0])), reverse=True)
        filtered: Counter[str] = Counter()
        for word, count in entries:
            skip = False
            remove: list[str] = []
            for kept, kept_count in list(filtered.items()):
                if word in kept and len(word) < len(kept) and count <= kept_count * 1.25:
                    skip = True
                    break
                if kept in word and len(kept) < len(word) and kept_count <= count * 0.8:
                    remove.append(kept)
            if not skip:
                for kept in remove:
                    filtered.pop(kept, None)
                filtered[word] = count
            if len(filtered) >= 240:
                break
        return filtered

    def _render_wordcloud_png(self, message_data: list[MessageData]) -> tuple[bytes | None, str]:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return None, "缺少 Pillow 依赖"

        counts = self._collect_wordcloud_counts(message_data)
        if not counts:
            return None, "未提取到可用热词"
        freq = [(w, c) for w, c in counts.most_common(220) if c >= 2]
        if not freq:
            return None, "没有统计到足够的热词"
        width, height = 900, 640
        margin = 32
        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        font_path = self._wordcloud_font_path()
        if not font_path:
            return None, "缺少中文字体。请将 Noto Sans CJK/思源黑体等常规中文字体放到 sum/assets/font.otf 或 font.ttc，或在 VPS 安装 fonts-noto-cjk 后重载模块。"
        max_count = freq[0][1]
        min_count = freq[-1][1]
        spread = max(1, max_count - min_count)
        placed: list[tuple[int, int, int, int, str]] = []
        palette = [(214, 39, 40), (31, 119, 180), (44, 160, 44), (148, 103, 189), (23, 190, 207), (188, 139, 9), (10, 110, 96)]
        center_x = width / 2
        center_y = height / 2 - 28

        for index, (word, count) in enumerate(freq):
            placed_ok = False
            ratio = (count - min_count) / spread
            base_size = round(12 + (ratio ** 0.7) * 68)
            color = palette[index % len(palette)]
            for shrink in range(4):
                size = max(10, base_size - shrink * 4)
                try:
                    font = ImageFont.truetype(font_path, size)
                except Exception as exc:
                    return None, f"中文字体加载失败：{font_path}（{exc}）"
                bbox = draw.textbbox((0, 0), word, font=font)
                tw = max(1, bbox[2] - bbox[0])
                th = max(1, bbox[3] - bbox[1])
                if tw > width - margin * 2:
                    continue
                for t in range(3600):
                    angle = t * 0.38
                    radius = 5.2 * math.sqrt(t)
                    x = int(center_x + math.cos(angle) * radius - tw / 2)
                    y = int(center_y + math.sin(angle) * radius - th / 2)
                    if x < margin or y < margin or x + tw > width - margin or y + th > height - 78:
                        continue
                    rect = (x, y, x + tw, y + th, word)
                    if self._wordcloud_overlaps(rect, placed):
                        continue
                    placed.append(rect)
                    draw.text((x, y), word, font=font, fill=color)
                    placed_ok = True
                    break
                if placed_ok:
                    break
        if not placed:
            return None, "热词密度过高，排版失败"

        buff = io.BytesIO()
        image.save(buff, format="PNG")
        return buff.getvalue(), ""

    @staticmethod
    def _wordcloud_overlaps(rect: tuple[int, int, int, int, str], placed: list[tuple[int, int, int, int, str]]) -> bool:
        padding = 4
        ax1, ay1, ax2, ay2, word = rect
        ax1 -= padding
        ay1 -= padding
        ax2 += padding
        ay2 += padding
        for bx1, by1, bx2, by2, other_word in placed:
            if word in other_word or other_word in word:
                return True
            bx1 -= padding
            by1 -= padding
            bx2 += padding
            by2 += padding
            if ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1:
                return True
        return False

    async def _send_photo(self, ctx: PluginContext, chat_id: Any, image_data: bytes, *, caption: str = "") -> Any:
        if not ctx.client:
            raise RuntimeError("Telegram 客户端未初始化")
        send_file = getattr(ctx.client, "send_file", None)
        if send_file is None:
            raise RuntimeError("缺少 send_file 权限，无法发送词云图片")
        file_obj = io.BytesIO(image_data)
        file_obj.name = f"{_safe_filename(str(chat_id))}_cy.png"
        clean_caption = caption[:1000] if caption else ""
        return await send_file(_telegram_target(chat_id), file_obj, caption=clean_caption, force_document=False)

    async def _event_chat_target(self, event: Any, fallback: Any) -> Any:
        targets = await self._event_chat_targets(event, fallback)
        return targets[0] if targets else _telegram_target(fallback)

    async def _event_chat_targets(self, event: Any, fallback: Any) -> list[Any]:
        candidates: list[Any] = []
        for source in (event, getattr(event, "message", None)):
            if source is None:
                continue
            for attr in ("input_chat", "chat"):
                target = _safe_attr(source, attr, None)
                if target is not None:
                    candidates.append(target)
        candidates.append(fallback)
        return _target_candidates(candidates)

    async def _client_chat_targets(self, ctx: PluginContext, target: Any) -> list[Any]:
        return []

    async def _get_group_messages(self, ctx: PluginContext, chat_id: str, count: int, *, hours: int = 0, since_seconds: int = 0, target: Any = None) -> list[MessageData]:
        if not ctx.client:
            raise RuntimeError("Telegram 客户端未初始化")
        get_messages = getattr(ctx.client, "get_messages", None)
        if not get_messages:
            raise RuntimeError("当前客户端没有读取消息能力")
        messages = None
        used_target = None
        last_lookup_error: BaseException | None = None
        for candidate in _target_candidates(target, chat_id):
            try:
                messages = await _maybe_await(get_messages(_telegram_target(candidate), limit=count))
                used_target = candidate
                break
            except Exception as exc:
                if _is_entity_lookup_error(exc):
                    last_lookup_error = exc
                    continue
                raise
        if messages is None:
            for candidate in await self._client_chat_targets(ctx, chat_id):
                try:
                    messages = await _maybe_await(get_messages(_telegram_target(candidate), limit=count))
                    used_target = candidate
                    break
                except Exception as exc:
                    if _is_entity_lookup_error(exc):
                        last_lookup_error = exc
                        continue
                    raise
        if messages is None and last_lookup_error is not None:
            raise _chat_lookup_error(chat_id, last_lookup_error) from last_lookup_error
        if messages is None:
            return []
        if not isinstance(messages, list):
            messages = list(messages)

        username = ""
        username = str(_safe_attr(used_target, "username", "") or "")

        start_ts = time.time() - since_seconds if since_seconds > 0 else (time.time() - hours * 3600 if hours else 0)
        rows: list[MessageData] = []
        for message in messages:
            msg_text = str(getattr(message, "message", None) or getattr(message, "raw_text", None) or "")
            date_raw = getattr(message, "date", None)
            if isinstance(date_raw, datetime):
                ts = date_raw.timestamp()
            else:
                ts = float(date_raw or 0)
            if start_ts and ts and ts < start_ts:
                continue
            file_name = _extract_file_name(message)
            if not msg_text and not file_name:
                continue
            sender = getattr(message, "sender", None)
            sender_name = public_entity_display_name(sender, fallback_id=getattr(message, "sender_id", None), default="未知用户")
            text_content = msg_text
            if file_name:
                text_content = f"{text_content} [文件: {file_name}]" if text_content else f"[文件: {file_name}]"
            message_id = _int(getattr(message, "id", None), 0)
            link = _build_message_link(chat_id, message_id, username) if message_id else ""
            rows.append(
                MessageData(
                    text=f"[{_format_date(ts or None)}] {sender_name}: {text_content}",
                    content=msg_text,
                    telegram_link=link,
                    urls=_extract_entity_urls(message, msg_text),
                    file_name=file_name,
                )
            )
        rows.reverse()
        return rows

    async def _safe_get_chat(self, ctx: PluginContext, target: Any) -> Any:
        return None

    async def _chat_display(self, ctx: PluginContext, chat_id: str, *, target: Any = None) -> str:
        entity = await self._safe_get_chat(ctx, target if target is not None else chat_id)
        if not entity:
            return chat_id
        parts = []
        title = getattr(entity, "title", None)
        username = getattr(entity, "username", None)
        if title:
            parts.append(_html(title))
        if username:
            parts.append(_html(f"@{username}"))
        parts.append(_code(chat_id))
        return " ".join(parts)

    @staticmethod
    def _format_messages_for_ai(messages: list[MessageData]) -> str:
        lines = []
        urls: list[tuple[str, str]] = []
        files: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for item in messages:
            source = f" [来源]({item.telegram_link})" if item.telegram_link else ""
            lines.append(f"{item.text}{source}")
            for url in item.urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    urls.append((url, item.telegram_link))
            if item.file_name:
                files.append((item.file_name, item.telegram_link))
        result = "\n".join(lines)
        if urls:
            result += "\n\n--- 消息中包含的外部链接（资源URL - 来源消息链接）---\n"
            result += "\n".join(f"{url} - [查看原消息]({link})" for url, link in urls if link)
        if files:
            result += "\n\n--- 消息中包含的附件（文件名 - 来源消息链接）---\n"
            result += "\n".join(f"{name} - [查看原消息]({link})" for name, link in files if link)
        return result

    async def _summarize_messages(self, ctx: PluginContext, task: SummaryTask, message_data: list[MessageData], db: SummaryDB) -> dict[str, Any]:
        prompt = task.ai_prompt or db.ai_config.default_prompt
        messages = self._format_messages_for_ai(message_data)
        try:
            result = await self._call_telepilot_ai(ctx, messages, prompt, db.ai_config)
            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": f"AI 调用失败: {exc}"}

    async def _call_telepilot_ai(self, ctx: PluginContext, messages: str, prompt: str, ai_config: AIConfig) -> str:
        ai = getattr(ctx, "ai", None)
        complete = getattr(ai, "complete", None) if ai is not None else None
        if complete is None:
            raise RuntimeError("当前 TelePilot 未向插件暴露 ctx.ai；请启用 ai_text 权限并升级到支持 ctx.ai 的 TelePilot 版本。")

        override_model = ai_config.telepilot_model.strip() or None
        user_prompt = f"{prompt}\n\n{messages}"
        result = await complete(
            "你是一个专业、简洁、可靠的中文群聊总结助手。",
            user_prompt,
            provider=ai_config.telepilot_provider.strip() or None,
            model=override_model,
            provider_tag="long_context",
            max_tokens=4000,
            timeout_seconds=max(10, ai_config.default_timeout // 1000),
            source="plugin:sum",
        )
        text = result.text.strip()
        if not text:
            raise RuntimeError("TelePilot AI 返回内容为空")
        return text

    def _build_summary_text(self, task: SummaryTask, summary: str, db: SummaryDB) -> tuple[str, bool]:
        content = THINK_RE.sub("", summary).strip()
        max_len = max(0, db.ai_config.max_output_length)
        if max_len and len(content) > max_len:
            content = content[:max_len] + "\n\n⚠️ 内容已截断（超过最大长度限制）"
        use_spoiler = bool(task.use_spoiler)
        if use_spoiler and "<blockquote expandable>" not in content:
            content = f"<blockquote expandable>{content}</blockquote>"
        values = {
            "summary": content,
            "chat_id": str(task.chat_id),
            "chat_display": task.chat_display or task.chat_id,
            "time": _format_date(),
            "message_count": str(max(1, int(task.message_count or 1))),
        }
        rendered = self._render_message_template(db.ai_config.message_template, values)
        need_html = use_spoiler or "<" in rendered
        return rendered, need_html

    @staticmethod
    def _render_message_template(template: str, values: dict[str, str]) -> str:
        raw = (template or "").strip() or SUMMARY_MESSAGE_TEMPLATE_DEFAULT
        return re.sub(
            r"\{([a-zA-Z0-9_]+)\}",
            lambda m: values.get(m.group(1), m.group(0)),
            raw,
        )

    async def _send_message(self, ctx: PluginContext, chat_id: Any, text: str, **kwargs: Any) -> Any:
        if not ctx.client:
            raise RuntimeError("Telegram 客户端未初始化")
        clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        last_lookup_error: BaseException | None = None
        for target in _target_candidates(chat_id):
            try:
                return await ctx.client.send_message(_telegram_target(target), text, **clean_kwargs)
            except Exception as exc:
                if _is_entity_lookup_error(exc):
                    last_lookup_error = exc
                    continue
                raise
        for target in await self._client_chat_targets(ctx, chat_id):
            try:
                return await ctx.client.send_message(_telegram_target(target), text, **clean_kwargs)
            except Exception as exc:
                if _is_entity_lookup_error(exc):
                    last_lookup_error = exc
                    continue
                raise
        if last_lookup_error is not None:
            raise _chat_lookup_error(chat_id, last_lookup_error) from last_lookup_error
        raise RuntimeError("无法识别发送目标")

    async def _edit_or_reply(self, event: Any, text: str, *, parse_mode: str | None = None) -> None:
        try:
            await event.edit(text, parse_mode=parse_mode)
            return
        except Exception:
            pass
        try:
            await event.reply(text, parse_mode=parse_mode)
        except Exception:
            pass

    async def _edit_command_message(self, event: Any, text: str, *, parse_mode: str | None = None) -> None:
        try:
            await event.edit(text, parse_mode=parse_mode)
            return
        except Exception:
            if parse_mode:
                await event.edit(text)
                return
            raise RuntimeError("当前消息无法编辑（已按你的要求禁用 reply 回退）")

    async def _delete_command_message(self, ctx: PluginContext, event: Any) -> None:
        # 优先走事件删除；失败再用客户端按 chat_id+message_id 强删。
        try:
            delete = getattr(event, "delete", None)
            if callable(delete):
                await delete()
                return
        except Exception as exc:
            if ctx.log:
                await ctx.log("warning", f"[sum] event.delete 失败：{type(exc).__name__}: {exc}")
        message_id = _event_message_id(event)
        chat_id = str(_event_chat_id(event))
        if not message_id or not chat_id or not ctx.client:
            return
        try:
            delete_messages = getattr(ctx.client, "delete_messages", None)
            if callable(delete_messages):
                await _maybe_await(delete_messages(_telegram_target(chat_id), [int(message_id)]))
                return
        except Exception as exc:
            if ctx.log:
                await ctx.log("warning", f"[sum] client.delete_messages 失败：{type(exc).__name__}: {exc}")

    async def _show_prompts(self, event: Any) -> None:
        prompts = [
            ("默认总结", "请总结以下群聊消息的主要内容，提取关键话题和重要信息："),
            ("简洁版", "用3-5个要点总结以下群聊消息的核心内容："),
            ("详细版", "详细分析以下群聊消息，包括：1.主要话题 2.关键观点 3.重要决策 4.待办事项"),
            ("技术讨论", "总结以下技术讨论的内容，重点提取：技术方案、问题、解决方案、待确认事项"),
            ("会议纪要", "整理以下会议讨论内容，格式化为：讨论议题、关键决策、行动项、责任人"),
            ("问答整理", "整理以下对话中的问答内容，格式：Q: 问题 A: 答案"),
        ]
        lines = ["📝 推荐提示词", ""]
        for name, prompt in prompts:
            lines.append(f"<b>{_html(name)}</b>")
            lines.append(_code(prompt))
            lines.append("")
        lines.append("使用方法：")
        lines.append(_code(f"{_command_prefix()}{self._command} config set prompt 您的提示词"))
        await self._edit_or_reply(event, "\n".join(lines), parse_mode="html")

    async def _debug_messages(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        count = max(1, min(_int(args[0], 50) if args else 50, _int(self._cfg.get("max_fetch_count"), 300)))
        chat_id = str(_event_chat_id(event))
        await self._edit_or_reply(event, "⏳ 正在获取消息...")
        chat_targets = await self._event_chat_targets(event, chat_id)
        data = await self._get_group_messages(ctx, chat_id, count, target=chat_targets)
        if not data:
            await self._edit_or_reply(event, "❌ 未找到消息")
            return
        formatted = self._format_messages_for_ai(data)
        preview = formatted[-2000:]
        if len(formatted) > 2000:
            preview = "...(前面省略)...\n\n" + preview
        await self._edit_or_reply(event, f"📋 发送给 AI 的文本预览（最后2000字符）：\n\n{_code(preview)}", parse_mode="html")

    def _parse_interval(self, tokens: list[str]) -> tuple[str, str, int]:
        if not tokens:
            return "", "", 0
        first = tokens[0]
        if re.fullmatch(r"\d+[hmHM]", first):
            value = int(first[:-1])
            unit = first[-1].lower()
            return (f"interval:{value}{unit}", first, 1)
        for n in (6, 5):
            if len(tokens) >= n:
                candidate = " ".join(tokens[:n])
                if self._looks_like_cron(candidate):
                    return (candidate, candidate, n)
        return "", "", 0

    @staticmethod
    def _looks_like_cron(value: str) -> bool:
        fields = value.strip().split()
        if len(fields) not in {5, 6}:
            return False
        return all(re.fullmatch(r"[\d*/,\-]+", item) for item in fields)

    async def _add_task(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        if len(args) < 2:
            await self._edit_or_reply(event, f"❌ 用法：{_code(f'{_command_prefix()}{self._command} add <群组标识> <间隔> [消息数] [选项]')}", parse_mode="html")
            return
        chat_input = args[0]
        cron_expr, interval_text, used = self._parse_interval(args[1:])
        if not cron_expr:
            await self._edit_or_reply(event, "❌ 无效间隔。支持 2h、30m，或 5/6 字段 cron 表达式。")
            return
        db = await self._load_db()
        db.seq += 1
        task_id = str(db.seq)
        parsed_chat = _parse_chat_identifier(chat_input)
        if _is_placeholder_chat_identifier(parsed_chat):
            await self._edit_or_reply(event, "❌ 请填写真实群 ID、@用户名或 t.me 链接，不要使用“群组 ID”占位文字。")
            return
        chat_display = await self._chat_display(ctx, parsed_chat)
        message_count = max(1, _int(self._cfg.get("default_count"), 100))
        time_range = 0
        use_spoiler = db.ai_config.default_spoiler
        remark_parts: list[str] = []
        rest = args[1 + used:]
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--time" and i + 1 < len(rest):
                time_range = max(0, _int(rest[i + 1], 0))
                i += 2
            elif arg in {"--provider", "-p"}:
                await self._edit_or_reply(event, "❌ 已移除模块内 AI 配置选择；总结会直接调用 TelePilot 已配置的 AI。")
                return
            elif arg == "--spoiler":
                use_spoiler = True
                i += 1
            elif arg == "--no-spoiler":
                use_spoiler = False
                i += 1
            elif str(arg).isdigit():
                message_count = max(1, _int(arg, message_count))
                i += 1
            else:
                remark_parts.append(arg)
                i += 1
        task = SummaryTask(
            id=task_id,
            cron=cron_expr,
            chat_id=parsed_chat,
            chat_display=chat_display,
            interval=interval_text,
            message_count=message_count,
            time_range=time_range,
            push_target=db.default_push_target,
            use_spoiler=use_spoiler,
            created_at=str(int(time.time() * 1000)),
            remark=" ".join(remark_parts),
        )
        db.tasks.append(task)
        await self._save_db(db)
        scheduled = await self._schedule_task(ctx, task)
        lines = [
            "✅ 已添加总结任务",
            f"ID: {_code(task.id)}",
            f"群组: {task.chat_display or _code(task.chat_id)}",
            f"间隔: {_code(task.interval)}",
            f"范围: {'过去' + str(task.time_range) + '小时' if task.time_range else str(task.message_count) + '条消息'}",
            "AI: TelePilot 已配置的 AI",
            f"推送: {_code(task.push_target or task.chat_id)}",
        ]
        if task.remark:
            lines.append(f"备注: {_html(task.remark)}")
        if not scheduled:
            lines.append("提示: 当前运行环境没有可用调度器，只有简化间隔会由模块内后台任务执行。")
        await self._edit_or_reply(event, "\n".join(lines), parse_mode="html")

    async def _schedule_task(self, ctx: PluginContext, task: SummaryTask) -> bool:
        if task.disabled or task.id in self._scheduled:
            return True
        job_id = f"sum:{task.id}"

        async def run() -> None:
            await self._execute_task_by_id(ctx, task.id)

        scheduler = getattr(ctx, "scheduler", None)
        register = getattr(scheduler, "register", None) if scheduler else None
        if register and not task.cron.startswith("interval:"):
            await _maybe_await(register(job_id, task.cron, run, replace=True))
            self._scheduled.add(task.id)
            return True
        if task.cron.startswith("interval:"):
            seconds = self._interval_seconds(task.cron)
            if seconds <= 0:
                return False
            bg = asyncio.create_task(self._interval_loop(ctx, task.id, seconds))
            self._tasks.add(bg)
            bg.add_done_callback(self._tasks.discard)
            self._scheduled.add(task.id)
            return True
        return False

    @staticmethod
    def _interval_seconds(expr: str) -> int:
        match = re.fullmatch(r"interval:(\d+)([hm])", expr)
        if not match:
            return 0
        value = int(match.group(1))
        return value * (3600 if match.group(2) == "h" else 60)

    async def _interval_loop(self, ctx: PluginContext, task_id: str, seconds: int) -> None:
        try:
            while True:
                await asyncio.sleep(seconds)
                await self._execute_task_by_id(ctx, task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[sum] 定时循环异常：{type(exc).__name__}: {exc}", task_id=task_id)

    async def _bootstrap_tasks(self, ctx: PluginContext) -> None:
        db = await self._load_db()
        for task in db.tasks:
            if not task.disabled:
                await self._schedule_task(ctx, task)

    async def _unregister_all(self, ctx: PluginContext) -> None:
        scheduler = getattr(ctx, "scheduler", None)
        unregister = getattr(scheduler, "unregister", None) if scheduler else None
        if unregister:
            for task_id in list(self._scheduled):
                try:
                    await _maybe_await(unregister(f"sum:{task_id}"))
                except Exception:
                    pass
        self._scheduled.clear()

    async def _execute_task_by_id(self, ctx: PluginContext, task_id: str) -> dict[str, Any]:
        db = await self._load_db()
        task = next((item for item in db.tasks if item.id == task_id), None)
        if not task or task.disabled:
            return {"success": False, "message": "任务不存在或已禁用"}
        result = await self._execute_summary_task(ctx, task, db)
        task.last_run_at = str(int(time.time() * 1000))
        if result["success"]:
            task.last_result = str(result["message"])
            task.last_error = ""
        else:
            task.last_error = str(result["message"])
        await self._save_db(db)
        return result

    async def _execute_summary_task(self, ctx: PluginContext, task: SummaryTask, db: SummaryDB) -> dict[str, Any]:
        if _is_placeholder_chat_identifier(task.chat_id):
            return {"success": False, "message": "任务 chatId 不是有效聊天标识，请填写真实群 ID、@用户名或 t.me 链接"}
        count = min(task.message_count, max(10, _int(self._cfg.get("max_fetch_count"), 300)))
        messages = await self._get_group_messages(ctx, task.chat_id, count, hours=task.time_range)
        if not messages:
            return {"success": False, "message": "未找到可总结的消息"}
        result = await self._summarize_messages(ctx, task, messages, db)
        if not result["success"]:
            return {"success": False, "message": result["error"]}
        summary_text, need_html = self._build_summary_text(task, str(result["result"]), db)
        target = task.push_target or db.default_push_target or task.chat_id
        await self._send_message(ctx, target, summary_text, parse_mode="html" if need_html else None)
        return {"success": True, "message": f"总结完成，已推送到 {target}"}

    async def _list_tasks(self, event: Any, ctx: PluginContext) -> None:
        db = await self._load_db()
        if not db.tasks:
            await self._edit_or_reply(event, "暂无总结任务")
            return
        lines = ["📋 所有总结任务", ""]
        for task in sorted(db.tasks, key=lambda t: _int(t.id)):
            lines.append(f"{_code(task.id)} • {_html(task.remark or task.chat_display or task.chat_id)}")
            lines.append(f"群组: {task.chat_display or _html(task.chat_id)}")
            lines.append(f"间隔: {_code(task.interval)}")
            lines.append(f"范围: {'过去' + str(task.time_range) + '小时' if task.time_range else str(task.message_count) + '条消息'}")
            lines.append("AI: TelePilot 已配置的 AI")
            lines.append(f"推送: {_code(task.push_target or db.default_push_target or task.chat_id)}")
            lines.append(f"来源: {'配置页' if task.managed_by_config else '命令'}")
            lines.append(f"状态: {'已禁用' if task.disabled else '运行中'}")
            if task.last_run_at:
                lines.append(f"上次: {_format_date(_int(task.last_run_at) / 1000)}")
            if task.last_result:
                lines.append(f"结果: {_html(task.last_result)}")
            if task.last_error:
                lines.append(f"错误: {_html(task.last_error)}")
            lines.append("")
        await self._edit_or_reply(event, "\n".join(lines), parse_mode="html")

    async def _delete_task(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        task_id = args[0] if args else ""
        if not task_id:
            await self._edit_or_reply(event, "请提供任务ID")
            return
        db = await self._load_db()
        existing = next((task for task in db.tasks if task.id == task_id), None)
        if existing and existing.managed_by_config:
            await self._edit_or_reply(event, "❌ 该任务来自配置页，请在插件配置里的定时任务 JSON 中删除。")
            return
        before = len(db.tasks)
        db.tasks = [task for task in db.tasks if task.id != task_id]
        if len(db.tasks) == before:
            await self._edit_or_reply(event, f"未找到任务: {_code(task_id)}", parse_mode="html")
            return
        await self._save_db(db)
        await self._unregister_one(ctx, task_id)
        await self._edit_or_reply(event, f"✅ 已删除任务 {_code(task_id)}", parse_mode="html")

    async def _unregister_one(self, ctx: PluginContext, task_id: str) -> None:
        scheduler = getattr(ctx, "scheduler", None)
        unregister = getattr(scheduler, "unregister", None) if scheduler else None
        if unregister:
            try:
                await _maybe_await(unregister(f"sum:{task_id}"))
            except Exception:
                pass
        self._scheduled.discard(task_id)

    async def _run_task_command(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        task_id = args[0] if args else ""
        if not task_id:
            await self._edit_or_reply(event, "请提供任务ID")
            return
        await self._edit_or_reply(event, f"⏳ 正在执行总结任务 {_code(task_id)}...", parse_mode="html")
        result = await self._execute_task_by_id(ctx, task_id)
        await self._edit_or_reply(event, f"{'✅' if result['success'] else '❌'} {_html(result['message'])}", parse_mode="html")

    async def _edit_task(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        if len(args) < 2:
            await self._edit_or_reply(event, "❌ 用法：sum edit <任务ID> <spoiler/prompt/push> <值>")
            return
        task_id, prop = args[0], args[1].lower()
        value = " ".join(args[2:])
        db = await self._load_db()
        task = next((item for item in db.tasks if item.id == task_id), None)
        if not task:
            await self._edit_or_reply(event, f"未找到任务: {_code(task_id)}", parse_mode="html")
            return
        if task.managed_by_config:
            await self._edit_or_reply(event, "❌ 该任务来自配置页，请直接修改插件配置里的定时任务 JSON。")
            return
        if prop == "spoiler":
            task.use_spoiler = _bool(value, task.use_spoiler)
        elif prop == "prompt":
            task.ai_prompt = value
        elif prop == "push":
            task.push_target = value
        else:
            await self._edit_or_reply(event, "❌ 未知属性，支持 spoiler/prompt/push")
            return
        await self._save_db(db)
        await self._edit_or_reply(event, f"✅ 已更新任务 {_code(task_id)} 的 {_code(prop)}", parse_mode="html")

    async def _toggle_task(self, event: Any, args: list[str], ctx: PluginContext, *, enable: bool) -> None:
        task_id = args[0] if args else ""
        if not task_id:
            await self._edit_or_reply(event, "请提供任务ID")
            return
        db = await self._load_db()
        task = next((item for item in db.tasks if item.id == task_id), None)
        if not task:
            await self._edit_or_reply(event, f"未找到任务: {_code(task_id)}", parse_mode="html")
            return
        if task.managed_by_config:
            await self._edit_or_reply(event, "❌ 该任务来自配置页，请在插件配置里的定时任务 JSON 中修改 disabled。")
            return
        task.disabled = not enable
        await self._save_db(db)
        if enable:
            await self._schedule_task(ctx, task)
        else:
            await self._unregister_one(ctx, task_id)
        await self._edit_or_reply(event, f"{'▶️ 已启用' if enable else '⏸️ 已禁用'}任务 {_code(task_id)}", parse_mode="html")

    async def _reorder_tasks(self, event: Any, ctx: PluginContext) -> None:
        db = await self._load_db()
        command_tasks = [task for task in db.tasks if not task.managed_by_config]
        old_ids = [task.id for task in command_tasks]
        for idx, task in enumerate(command_tasks, start=1):
            task.id = str(idx)
        db.seq = len(command_tasks)
        await self._save_db(db)
        await self._unregister_all(ctx)
        await self._bootstrap_tasks(ctx)
        mapping = ", ".join(f"{old} → {i + 1}" for i, old in enumerate(old_ids))
        await self._edit_or_reply(event, f"✅ 已重新排序 {len(command_tasks)} 个命令任务\n\n{_html(mapping)}", parse_mode="html")

    async def _config_command(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        action = args[0].lower() if args else ""
        db = await self._load_db()
        if action in {"list", "ls"}:
            lines = ["🤖 sum 配置", ""]
            lines.append("AI: TelePilot 已配置的 AI")
            lines.append(f"Provider: {_code(db.ai_config.telepilot_provider or '自动路由')}")
            lines.append(f"Model 覆盖: {_code(db.ai_config.telepilot_model or '使用平台默认')}")
            lines.append("")
            lines.append("⚙️ 全局设置")
            lines.append(f"默认推送: {_code(db.default_push_target or '来源聊天')}")
            lines.append(f"折叠显示: {'开启' if db.ai_config.default_spoiler else '关闭'}")
            lines.append(f"超时时间: {db.ai_config.default_timeout // 1000}秒")
            lines.append(f"回复模式: {'开启' if db.ai_config.reply_mode else '关闭'}")
            lines.append(f"最大输出: {db.ai_config.max_output_length or '不限制'}")
            config_count = len([task for task in db.tasks if task.managed_by_config])
            command_count = len(db.tasks) - config_count
            lines.append(f"定时任务: 配置页 {config_count} 个 / 命令 {command_count} 个")
            await self._edit_or_reply(event, "\n".join(lines), parse_mode="html")
            return
        if action in {"providers", "provider", "llm"}:
            await self._list_telepilot_providers(event, ctx)
            return
        if action == "set":
            await self._config_set(event, args[1:], db)
            return
        if action in {"add", "del", "rm"}:
            await self._edit_or_reply(event, "❌ 已移除模块内 AI 配置管理；请在 TelePilot 的 AI Provider 中维护模型。")
            return
        await self._edit_or_reply(event, self._help_text(), parse_mode="html")

    async def _list_telepilot_providers(self, event: Any, ctx: PluginContext) -> None:
        ai = getattr(ctx, "ai", None)
        list_providers = getattr(ai, "list_providers", None) if ai is not None else None
        if list_providers is None:
            await self._edit_or_reply(event, "ℹ️ 当前插件上下文未提供 Provider 列表接口。请在 TelePilot 的 AI 设置页查看可用 Provider；sum 默认会通过 ctx.ai 自动路由。")
            return

        providers = list(await _maybe_await(list_providers()) or [])
        if not providers:
            await self._edit_or_reply(event, "❌ TelePilot 尚未配置任何 LLM Provider。")
            return

        prefix = _command_prefix()
        cmd = self._command
        lines = [
            "🤖 TelePilot 可用 LLM Provider",
            "",
            f"自动路由无需选择；如需固定某个 Provider，使用：{_code(f'{prefix}{cmd} config set provider <ID或名称>')}",
            f"恢复自动路由：{_code(f'{prefix}{cmd} config set provider auto')}",
            "",
        ]
        for provider in providers:
            provider_id = self._provider_value(provider, "id", "-")
            name = self._provider_value(provider, "name", "-")
            provider_kind = self._provider_value(provider, "provider", self._provider_value(provider, "type", "-"))
            default_model = self._provider_value(provider, "default_model", self._provider_value(provider, "model", "-"))
            tags_raw = self._provider_value(provider, "tags", [])
            tags = ",".join(tags_raw) if isinstance(tags_raw, list) else str(tags_raw or "-")
            has_api_key = self._provider_value(provider, "has_api_key", None)
            ready = "可用" if has_api_key is True else ("未配置 API Key" if has_api_key is False else "状态由平台管理")
            cost_tier = self._provider_value(provider, "cost_tier", "-")
            lines.append(f"{_code(provider_id)} <b>{_html(name or '-')}</b>")
            lines.append(f"类型: {_code(provider_kind)} · 默认模型: {_code(default_model or '-')}")
            lines.append(f"标签: {_code(tags or '-')} · 成本档: {_code(cost_tier)} · {ready}")
            lines.append("")
        await self._edit_or_reply(event, "\n".join(lines).strip(), parse_mode="html")

    @staticmethod
    def _provider_value(provider: Any, key: str, default: Any = None) -> Any:
        if isinstance(provider, dict):
            return provider.get(key, default)
        return getattr(provider, key, default)

    async def _config_set(self, event: Any, args: list[str], db: SummaryDB) -> None:
        if not args:
            await self._edit_or_reply(event, "用法：sum config set provider|model|push|prompt|spoiler|timeout|reply|maxoutput <值>")
            return
        name = args[0].lower()
        prop = args[1] if len(args) > 1 else ""
        value = " ".join(args[2:])
        combined = " ".join(args[1:]).strip()
        if name == "push":
            db.default_push_target = combined
        elif name == "prompt":
            db.ai_config.default_prompt = "请总结以下群聊消息的主要内容，提取关键话题和重要信息：" if prop == "reset" else combined
        elif name == "spoiler":
            db.ai_config.default_spoiler = _bool(prop, db.ai_config.default_spoiler)
        elif name == "timeout":
            seconds = _int(prop, 0)
            if seconds < 10:
                await self._edit_or_reply(event, "❌ 超时时间必须至少为10秒")
                return
            db.ai_config.default_timeout = seconds * 1000
        elif name == "reply":
            db.ai_config.reply_mode = _bool(prop, db.ai_config.reply_mode)
        elif name == "maxoutput":
            db.ai_config.max_output_length = max(0, _int(prop, 0))
        elif name == "provider":
            db.ai_config.telepilot_provider = "" if prop.lower() in {"auto", "reset", "clear", "default", "自动", "默认", "清空"} else combined
        elif name == "model":
            db.ai_config.telepilot_model = "" if prop.lower() in {"auto", "reset", "clear", "default", "自动", "默认", "清空"} else combined
        elif name == "telepilot" and prop.lower() in {"provider", "provider_id"}:
            db.ai_config.telepilot_provider = "" if value.lower() in {"auto", "reset", "clear", "default", "自动", "默认", "清空"} else value
        elif name == "telepilot" and prop.lower() == "model":
            db.ai_config.telepilot_model = "" if value.lower() in {"auto", "reset", "clear", "default", "自动", "默认", "清空"} else value
        else:
            await self._edit_or_reply(event, "❌ 无效配置项。AI 只调用 TelePilot 已配置的 Provider，不再支持模块内 OpenAI/Gemini。")
            return
        await self._save_db(db)
        await self._edit_or_reply(event, "✅ 配置已更新")

    def _help_text(self) -> str:
        prefix = _command_prefix()
        cmd = self._command
        return f"""▎群消息总结

使用 AI 自动总结群组消息。

<b>快捷总结当前群：</b>
{_code(f"{prefix}{cmd}")} - 总结最近默认数量消息
{_code(f"{prefix}{cmd} 100")} - 指定本次总结最近100条消息
{_code(f"{prefix}{cmd} 1h")} - 按最近1小时消息总结
{_code(f"{prefix}{cmd} 1d")} - 按最近1天消息总结
{_code(f"{prefix}{cmd} 100 1h")} - 最近1小时内，最多读取100条消息
{_code(f"{prefix}{cmd} 100 --cy")} - 仅生成并发送热词云（简写参数）

<b>定时总结：</b>
{_code(f"{prefix}{cmd} add <群组标识> <间隔> [消息数] [选项]")}
间隔支持 2h、30m，或平台 scheduler 支持的 5/6 字段 cron。
选项：--time 小时、--spoiler、--no-spoiler。
群组标识请填真实群 ID、@用户名或 t.me 链接。

<b>管理命令：</b>
{_code(f"{prefix}{cmd} list")} / {_code(f"{prefix}{cmd} del <任务ID>")} / {_code(f"{prefix}{cmd} run <任务ID>")}
{_code(f"{prefix}{cmd} edit <任务ID> spoiler|prompt|push <值>")}
{_code(f"{prefix}{cmd} disable|enable <任务ID>")} / {_code(f"{prefix}{cmd} reorder")}

<b>AI 配置：</b>
AI 调用固定走 TelePilot 已配置的 Provider，不再在模块内填写 API Key。
{_code(f"{prefix}{cmd} config list")}
{_code(f"{prefix}{cmd} config providers")} - 查看 TelePilot 可固定的 Provider
{_code(f"{prefix}{cmd} config set provider <Provider ID或名称>")} - 固定内置 AI Provider
{_code(f"{prefix}{cmd} config set provider auto")} - 恢复自动路由
{_code(f"{prefix}{cmd} config set model <模型ID>")} - 可选覆盖模型
{_code(f"{prefix}{cmd} prompts")} - 查看推荐提示词"""


PLUGIN_CLASS = SummaryPlugin

__all__ = ["SummaryPlugin", "PLUGIN_CLASS"]
