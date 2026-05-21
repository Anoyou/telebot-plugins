"""群消息总结远程模块。

兼容迁移 TeleBox `sum.ts` 的核心能力，但运行时遵循 TelePilot 远程模块边界：
只使用 ``ctx.client``、``ctx.scheduler`` 和已声明权限，不访问全局客户端或 raw MTProto。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register


VERSION = "1.0.0"
DB_PATH = Path(__file__).with_name("summary_config.json")
URL_RE = re.compile(r"https?://[^\s\]）】>]+", re.IGNORECASE)
THINK_RE = re.compile(r"<think(?:ing)?\b[^>]*>[\s\S]*?</think(?:ing)?>", re.IGNORECASE)


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    type: str = "openai"


@dataclass
class AIConfig:
    providers: dict[str, Provider] = field(default_factory=dict)
    default_provider: str = "openai"
    default_prompt: str = "请总结以下群聊消息的主要内容，提取关键话题和重要信息："
    default_spoiler: bool = False
    default_timeout: int = 60000
    reply_mode: bool = True
    max_output_length: int = 0


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
    ai_provider: str = ""
    ai_prompt: str = ""
    use_spoiler: bool = False
    created_at: str = ""
    last_run_at: str = ""
    last_result: str = ""
    last_error: str = ""
    disabled: bool = False
    remark: str = ""


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


DEFAULT_PROVIDER_PRESETS = {
    "openai": Provider(
        name="OpenAI",
        base_url="https://api.openai.com",
        api_key="",
        model="gpt-4o",
        type="openai",
    ),
    "gemini": Provider(
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com",
        api_key="",
        model="gemini-2.5-flash",
        type="gemini",
    ),
}


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


def _command_prefix() -> str:
    return current_command_prefix(fallback=",")


def _event_chat_id(event: Any) -> int:
    raw = getattr(event, "chat_id", None)
    channel_id = getattr(raw, "channel_id", None)
    if channel_id is not None:
        return int(channel_id)
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


def _coerce_provider(value: Any) -> Provider:
    if isinstance(value, Provider):
        return value
    data = dict(value or {})
    return Provider(
        name=str(data.get("name") or ""),
        base_url=str(data.get("base_url") or data.get("baseUrl") or ""),
        api_key=str(data.get("api_key") or data.get("apiKey") or ""),
        model=str(data.get("model") or ""),
        type=str(data.get("type") or "openai").lower(),
    )


def _default_db() -> SummaryDB:
    cfg = AIConfig(providers={k: Provider(**asdict(v)) for k, v in DEFAULT_PROVIDER_PRESETS.items()})
    return SummaryDB(ai_config=cfg)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


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
            if sub.isdigit() or sub in {"--provider", "-p"}:
                await self._quick_summary(event, args, ctx)
                return

            await self._edit_or_reply(event, self._help_text(), parse_mode="html")
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[sum] 命令执行失败：{type(exc).__name__}: {exc}")
            await self._edit_or_reply(event, f"❌ 错误：{_html(exc)}", parse_mode="html")

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
        providers = ai.get("providers") or {}
        for name, provider_data in providers.items():
            db.ai_config.providers[str(name)] = _coerce_provider(provider_data)
        db.ai_config.default_provider = str(ai.get("default_provider") or db.ai_config.default_provider)
        db.ai_config.default_prompt = str(ai.get("default_prompt") or db.ai_config.default_prompt)
        db.ai_config.default_spoiler = _bool(ai.get("default_spoiler"), db.ai_config.default_spoiler)
        db.ai_config.default_timeout = _int(ai.get("default_timeout"), db.ai_config.default_timeout)
        db.ai_config.reply_mode = _bool(ai.get("reply_mode"), db.ai_config.reply_mode)
        db.ai_config.max_output_length = _int(ai.get("max_output_length"), db.ai_config.max_output_length)
        db.tasks = [self._coerce_task(item) for item in data.get("tasks", []) if isinstance(item, dict)]
        self._merge_runtime_config(db)
        return db

    async def _save_db(self, db: SummaryDB) -> None:
        payload = {
            "seq": db.seq,
            "tasks": [self._task_to_json(t) for t in db.tasks],
            "aiConfig": {
                "providers": {k: asdict(v) for k, v in db.ai_config.providers.items()},
                "default_provider": db.ai_config.default_provider,
                "default_prompt": db.ai_config.default_prompt,
                "default_spoiler": db.ai_config.default_spoiler,
                "default_timeout": db.ai_config.default_timeout,
                "reply_mode": db.ai_config.reply_mode,
                "max_output_length": db.ai_config.max_output_length,
            },
            "defaultPushTarget": db.default_push_target,
        }
        DB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _merge_runtime_config(self, db: SummaryDB) -> None:
        cfg = self._cfg
        db.ai_config.default_provider = str(cfg.get("default_provider") or db.ai_config.default_provider or "openai")
        db.ai_config.default_prompt = str(cfg.get("default_prompt") or db.ai_config.default_prompt)
        db.ai_config.default_spoiler = _bool(cfg.get("default_spoiler"), db.ai_config.default_spoiler)
        db.ai_config.default_timeout = max(10, _int(cfg.get("timeout_seconds"), 60)) * 1000
        db.ai_config.reply_mode = _bool(cfg.get("reply_mode"), db.ai_config.reply_mode)
        db.ai_config.max_output_length = max(0, _int(cfg.get("max_output_length"), db.ai_config.max_output_length))
        db.default_push_target = str(cfg.get("default_push_target") or db.default_push_target or "")
        providers_json = str(cfg.get("providers_json") or "").strip()
        if providers_json:
            try:
                raw = json.loads(providers_json)
                raw_providers = raw.get("providers", raw) if isinstance(raw, dict) else {}
                for name, provider_data in raw_providers.items():
                    db.ai_config.providers[str(name)] = _coerce_provider(provider_data)
            except Exception:
                pass

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
            ai_provider=str(item.get("aiProvider") or item.get("ai_provider") or ""),
            ai_prompt=str(item.get("aiPrompt") or item.get("ai_prompt") or ""),
            use_spoiler=_bool(item.get("useSpoiler") if "useSpoiler" in item else item.get("use_spoiler"), False),
            created_at=str(item.get("createdAt") or item.get("created_at") or ""),
            last_run_at=str(item.get("lastRunAt") or item.get("last_run_at") or ""),
            last_result=str(item.get("lastResult") or item.get("last_result") or ""),
            last_error=str(item.get("lastError") or item.get("last_error") or ""),
            disabled=_bool(item.get("disabled"), False),
            remark=str(item.get("remark") or ""),
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
            "aiProvider": task.ai_provider,
            "aiPrompt": task.ai_prompt,
            "useSpoiler": task.use_spoiler,
            "createdAt": task.created_at,
            "lastRunAt": task.last_run_at,
            "lastResult": task.last_result,
            "lastError": task.last_error,
            "disabled": task.disabled,
            "remark": task.remark,
        }

    async def _quick_summary(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        count = max(1, _int(self._cfg.get("default_count"), 100))
        max_count = max(10, _int(self._cfg.get("max_fetch_count"), 300))
        provider = ""
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in {"--provider", "-p"} and i + 1 < len(args):
                provider = args[i + 1]
                i += 2
            elif str(arg).isdigit():
                count = _int(arg, count)
                i += 1
            else:
                i += 1
        count = min(count, max_count)
        chat_id = str(_event_chat_id(event))
        if not chat_id or chat_id == "0":
            await self._edit_or_reply(event, "❌ 无法识别当前聊天。")
            return

        await self._edit_or_reply(event, "⏳ 正在获取消息并总结...")
        message_data = await self._get_group_messages(ctx, chat_id, count)
        if not message_data:
            await self._edit_or_reply(event, "❌ 未找到可总结的消息")
            return

        db = await self._load_db()
        task = SummaryTask(
            id="temp",
            cron="",
            chat_id=chat_id,
            chat_display=await self._chat_display(ctx, chat_id),
            message_count=count,
            ai_provider=provider or db.ai_config.default_provider,
            created_at=str(int(time.time() * 1000)),
            use_spoiler=db.ai_config.default_spoiler,
        )
        result = await self._summarize_messages(task, message_data, db)
        if not result["success"]:
            await self._edit_or_reply(event, f"❌ {_html(result['error'])}", parse_mode="html")
            return

        summary_text, need_html = self._build_summary_text(task, str(result["result"]), db)
        if db.ai_config.reply_mode:
            await self._send_message(ctx, chat_id, summary_text, parse_mode="html" if need_html else None, reply_to=_event_message_id(event))
        else:
            await self._edit_or_reply(event, summary_text, parse_mode="html" if need_html else None)

    async def _get_group_messages(self, ctx: PluginContext, chat_id: str, count: int, *, hours: int = 0) -> list[MessageData]:
        if not ctx.client:
            raise RuntimeError("Telegram 客户端未初始化")
        get_messages = getattr(ctx.client, "get_messages", None)
        if not get_messages:
            raise RuntimeError("当前客户端没有读取消息能力")
        messages = await _maybe_await(get_messages(chat_id, limit=count))
        if messages is None:
            return []
        if not isinstance(messages, list):
            messages = list(messages)

        username = ""
        entity = await self._safe_get_entity(ctx, chat_id)
        if entity is not None:
            username = str(getattr(entity, "username", "") or "")

        start_ts = time.time() - hours * 3600 if hours else 0
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
            sender_name = (
                getattr(sender, "first_name", None)
                or getattr(sender, "firstName", None)
                or getattr(sender, "username", None)
                or getattr(message, "sender_id", None)
                or "未知用户"
            )
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

    async def _safe_get_entity(self, ctx: PluginContext, target: Any) -> Any:
        if not ctx.client:
            return None
        get_entity = getattr(ctx.client, "get_entity", None)
        if not get_entity:
            return None
        try:
            return await _maybe_await(get_entity(target))
        except Exception:
            return None

    async def _chat_display(self, ctx: PluginContext, chat_id: str) -> str:
        entity = await self._safe_get_entity(ctx, chat_id)
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

    async def _summarize_messages(self, task: SummaryTask, message_data: list[MessageData], db: SummaryDB) -> dict[str, Any]:
        provider_name = task.ai_provider or db.ai_config.default_provider or "openai"
        provider = db.ai_config.providers.get(provider_name)
        if not provider:
            return {"success": False, "error": f"未找到 AI 配置: {provider_name}"}
        if not provider.api_key:
            return {"success": False, "error": f"AI 配置 {provider_name} 尚未设置 API Key"}
        prompt = task.ai_prompt or db.ai_config.default_prompt
        messages = self._format_messages_for_ai(message_data)
        try:
            if provider.type == "gemini":
                result = await self._call_gemini(provider, messages, prompt, db.ai_config.default_timeout)
            else:
                result = await self._call_openai(provider, messages, prompt, db.ai_config.default_timeout)
            return {"success": True, "result": result}
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"AI 调用失败: {type(exc).__name__}"}
        except Exception as exc:
            return {"success": False, "error": f"AI 调用失败: {exc}"}

    async def _call_openai(self, provider: Provider, messages: str, prompt: str, timeout_ms: int) -> str:
        base_url = provider.base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": provider.model,
            "messages": [{"role": "user", "content": f"{prompt}\n\n{messages}"}],
            "max_tokens": 2000,
        }
        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            raise RuntimeError("OpenAI 返回内容为空")
        return str(content).strip()

    async def _call_gemini(self, provider: Provider, messages: str, prompt: str, timeout_ms: int) -> str:
        base_url = provider.base_url.rstrip("/")
        url = f"{base_url}/v1beta/models/{provider.model}:generateContent"
        payload = {"contents": [{"role": "user", "parts": [{"text": f"{prompt}\n\n{messages}"}]}]}
        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            resp = await client.post(
                url,
                params={"key": provider.api_key},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
        if not content:
            raise RuntimeError("Gemini 返回内容为空")
        return str(content).strip()

    def _build_summary_text(self, task: SummaryTask, summary: str, db: SummaryDB) -> tuple[str, bool]:
        content = THINK_RE.sub("", summary).strip()
        max_len = max(0, db.ai_config.max_output_length)
        if max_len and len(content) > max_len:
            content = content[:max_len] + "\n\n⚠️ 内容已截断（超过最大长度限制）"
        use_spoiler = bool(task.use_spoiler)
        if use_spoiler and "<blockquote expandable>" not in content:
            content = f"<blockquote expandable>{content}</blockquote>"
        header = f"📊 群组总结\n来源: {task.chat_display or _html(task.chat_id)}\n时间: {_format_date()}\n\n"
        need_html = use_spoiler or "<" in content
        return header + content, need_html

    async def _send_message(self, ctx: PluginContext, chat_id: Any, text: str, **kwargs: Any) -> Any:
        if not ctx.client:
            raise RuntimeError("Telegram 客户端未初始化")
        return await ctx.client.send_message(chat_id, text, **{k: v for k, v in kwargs.items() if v is not None})

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
        data = await self._get_group_messages(ctx, chat_id, count)
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
        chat_display = await self._chat_display(ctx, parsed_chat)
        message_count = max(1, _int(self._cfg.get("default_count"), 100))
        time_range = 0
        ai_provider = db.ai_config.default_provider
        use_spoiler = db.ai_config.default_spoiler
        remark_parts: list[str] = []
        rest = args[1 + used:]
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--time" and i + 1 < len(rest):
                time_range = max(0, _int(rest[i + 1], 0))
                i += 2
            elif arg in {"--provider", "-p"} and i + 1 < len(rest):
                ai_provider = rest[i + 1]
                i += 2
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
            ai_provider=ai_provider,
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
            f"AI配置: {_code(task.ai_provider)}",
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
        count = min(task.message_count, max(10, _int(self._cfg.get("max_fetch_count"), 300)))
        messages = await self._get_group_messages(ctx, task.chat_id, count, hours=task.time_range)
        if not messages:
            return {"success": False, "message": "未找到可总结的消息"}
        result = await self._summarize_messages(task, messages, db)
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
            lines.append(f"AI配置: {_code(task.ai_provider or db.ai_config.default_provider)}")
            lines.append(f"推送: {_code(task.push_target or db.default_push_target or task.chat_id)}")
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
            await self._edit_or_reply(event, "❌ 用法：sum edit <任务ID> <spoiler/provider/prompt/push> <值>")
            return
        task_id, prop = args[0], args[1].lower()
        value = " ".join(args[2:])
        db = await self._load_db()
        task = next((item for item in db.tasks if item.id == task_id), None)
        if not task:
            await self._edit_or_reply(event, f"未找到任务: {_code(task_id)}", parse_mode="html")
            return
        if prop == "spoiler":
            task.use_spoiler = _bool(value, task.use_spoiler)
        elif prop == "provider":
            task.ai_provider = value
        elif prop == "prompt":
            task.ai_prompt = value
        elif prop == "push":
            task.push_target = value
        else:
            await self._edit_or_reply(event, "❌ 未知属性，支持 spoiler/provider/prompt/push")
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
        task.disabled = not enable
        await self._save_db(db)
        if enable:
            await self._schedule_task(ctx, task)
        else:
            await self._unregister_one(ctx, task_id)
        await self._edit_or_reply(event, f"{'▶️ 已启用' if enable else '⏸️ 已禁用'}任务 {_code(task_id)}", parse_mode="html")

    async def _reorder_tasks(self, event: Any, ctx: PluginContext) -> None:
        db = await self._load_db()
        old_ids = [task.id for task in db.tasks]
        for idx, task in enumerate(db.tasks, start=1):
            task.id = str(idx)
        db.seq = len(db.tasks)
        await self._save_db(db)
        await self._unregister_all(ctx)
        await self._bootstrap_tasks(ctx)
        mapping = ", ".join(f"{old} → {i + 1}" for i, old in enumerate(old_ids))
        await self._edit_or_reply(event, f"✅ 已重新排序 {len(db.tasks)} 个任务\n\n{_html(mapping)}", parse_mode="html")

    async def _config_command(self, event: Any, args: list[str], ctx: PluginContext) -> None:
        action = args[0].lower() if args else ""
        db = await self._load_db()
        if action in {"list", "ls"}:
            lines = ["🤖 AI 配置列表", ""]
            for key, provider in db.ai_config.providers.items():
                lines.append(f"<b>{_html(provider.name or key)}</b> ({_code(key)})")
                lines.append(f"类型: {_code(provider.type)}")
                lines.append(f"Base URL: {_code(provider.base_url)}")
                lines.append(f"Model: {_code(provider.model)}")
                lines.append(f"API Key: {'已设置' if provider.api_key else '未设置'}")
                lines.append("")
            lines.append("⚙️ 全局设置")
            lines.append(f"默认配置: {_code(db.ai_config.default_provider)}")
            lines.append(f"默认推送: {_code(db.default_push_target or '来源聊天')}")
            lines.append(f"折叠显示: {'开启' if db.ai_config.default_spoiler else '关闭'}")
            lines.append(f"超时时间: {db.ai_config.default_timeout // 1000}秒")
            lines.append(f"回复模式: {'开启' if db.ai_config.reply_mode else '关闭'}")
            lines.append(f"最大输出: {db.ai_config.max_output_length or '不限制'}")
            await self._edit_or_reply(event, "\n".join(lines), parse_mode="html")
            return
        if action == "add":
            await self._config_add(event, args[1:], db)
            return
        if action == "set":
            await self._config_set(event, args[1:], db)
            return
        if action in {"del", "rm"}:
            name = args[1] if len(args) > 1 else ""
            if not name or name not in db.ai_config.providers:
                await self._edit_or_reply(event, f"❌ 未找到配置: {_html(name)}", parse_mode="html")
                return
            del db.ai_config.providers[name]
            if db.ai_config.default_provider == name:
                db.ai_config.default_provider = next(iter(db.ai_config.providers), "")
            await self._save_db(db)
            await self._edit_or_reply(event, f"✅ 已删除配置 {_code(name)}", parse_mode="html")
            return
        await self._edit_or_reply(event, self._help_text(), parse_mode="html")

    async def _config_add(self, event: Any, args: list[str], db: SummaryDB) -> None:
        if not args:
            await self._edit_or_reply(event, "❌ 请提供配置名称")
            return
        name = args[0].lower().replace(" ", "_")
        if name in DEFAULT_PROVIDER_PRESETS and len(args) >= 2:
            preset = Provider(**asdict(DEFAULT_PROVIDER_PRESETS[name]))
            preset.api_key = args[1]
            db.ai_config.providers[name] = preset
        else:
            if len(args) < 4:
                await self._edit_or_reply(event, "❌ 自定义用法：sum config add <名称> <类型> <BaseURL> <Model>")
                return
            provider_type, base_url, model = args[1], args[2], args[3]
            if provider_type not in {"openai", "gemini"}:
                await self._edit_or_reply(event, "❌ 类型必须是 openai 或 gemini")
                return
            db.ai_config.providers[name] = Provider(name=args[0], base_url=base_url, api_key="", model=model, type=provider_type)
        await self._save_db(db)
        await self._edit_or_reply(event, f"✅ 已添加配置 {_code(name)}", parse_mode="html")

    async def _config_set(self, event: Any, args: list[str], db: SummaryDB) -> None:
        if len(args) < 2:
            await self._edit_or_reply(event, "用法：sum config set <名称/选项> <属性> <值>")
            return
        name, prop = args[0], args[1]
        value = " ".join(args[2:])
        if name == "push":
            db.default_push_target = prop
        elif name == "default":
            if prop not in db.ai_config.providers:
                await self._edit_or_reply(event, f"❌ 未找到配置: {_html(prop)}", parse_mode="html")
                return
            db.ai_config.default_provider = prop
        elif name == "prompt":
            db.ai_config.default_prompt = "请总结以下群聊消息的主要内容，提取关键话题和重要信息：" if prop == "reset" else " ".join([prop, value]).strip()
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
        else:
            provider = db.ai_config.providers.get(name)
            if not provider:
                await self._edit_or_reply(event, f"❌ 未找到配置: {_html(name)}", parse_mode="html")
                return
            if not value:
                await self._edit_or_reply(event, "请提供值")
                return
            if prop == "key":
                provider.api_key = value
            elif prop == "model":
                provider.model = value
            elif prop == "url":
                provider.base_url = value
            else:
                await self._edit_or_reply(event, "❌ 无效属性，支持 key/model/url")
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
{_code(f"{prefix}{cmd} 200")} - 总结最近200条消息
{_code(f"{prefix}{cmd} --provider deepseek")} - 指定 AI 配置总结

<b>定时总结：</b>
{_code(f"{prefix}{cmd} add <群组标识> <间隔> [消息数] [选项]")}
间隔支持 2h、30m，或平台 scheduler 支持的 5/6 字段 cron。
选项：--time 小时、--provider 名称、--spoiler、--no-spoiler。

<b>管理命令：</b>
{_code(f"{prefix}{cmd} list")} / {_code(f"{prefix}{cmd} del <任务ID>")} / {_code(f"{prefix}{cmd} run <任务ID>")}
{_code(f"{prefix}{cmd} edit <任务ID> spoiler|provider|prompt|push <值>")}
{_code(f"{prefix}{cmd} disable|enable <任务ID>")} / {_code(f"{prefix}{cmd} reorder")}

<b>AI 配置：</b>
{_code(f"{prefix}{cmd} config list")}
{_code(f"{prefix}{cmd} config add openai sk-xxx")}
{_code(f"{prefix}{cmd} config add deepseek openai https://api.deepseek.com deepseek-chat")}
{_code(f"{prefix}{cmd} config set <名称> key|model|url <值>")}
{_code(f"{prefix}{cmd} config set default <名称>")}
{_code(f"{prefix}{cmd} prompts")} - 查看推荐提示词"""


PLUGIN_CLASS = SummaryPlugin

__all__ = ["SummaryPlugin", "PLUGIN_CLASS"]
