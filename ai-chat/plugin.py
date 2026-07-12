"""TelePilot remote plugin: AI-Chat."""

from __future__ import annotations

import asyncio
import importlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.worker.command import current_command_prefix

try:  # TelePilot 0.33+ exposes the worker-local command context here.
    from app.worker.command import get_command_context
except Exception:  # pragma: no cover - older runtimes
    get_command_context = None  # type: ignore[assignment]

from app.worker.plugins.base import Plugin, PluginContext, register

PLUGIN_VERSION = "0.1.9"
DEFAULT_COMMAND = "ask"
MAX_TELEGRAM_TEXT = 3900
HISTORY_TTL_SECONDS = 6 * 60 * 60
DEFAULT_BLOCKED_BARE_OUTPUTS = "re\nai\nfd"
DEFAULT_COMMAND_REFUSAL = "我不能代你发送可能触发 TelePilot 或其他 Bot 的可执行指令。"
DEFAULT_PAYMENT_GUARD_REPLY = "-66666"
DEFAULT_MEDIA_UNSUPPORTED_REPLY = "我现在看不了图片，发文字我再回你。"
DEFAULT_MODEL_TEST_PROMPT = "请只回复两个字：收到"
DEFAULT_MODEL_TEST_CLIENT_IDENTITY = "TelePilot AI-Chat"
DEFAULT_MODEL_TEST_RESULT = "尚未测试。"
HTTP_USER_AGENT_NOTE = (
    "HTTP UA：由 TelePilot AI facade / LLM client 控制；AI-Chat 插件当前不能自定义。"
    "OpenAI 兼容请求未显式设置 User-Agent，通常使用 httpx 默认 UA。"
)
THINK_RE = re.compile(r"<think(?:ing)?\b[^>]*>[\s\S]*?</think(?:ing)?>", re.IGNORECASE)
PAYMENT_LIKE_RE = re.compile(r"^\+\s*\d+(?:\.\d+)?(?:\s*[-–—~～至到]\s*\d+(?:\.\d+)?)?$")
REQUIRED_PREFIX_PATTERNS = (
    re.compile(r"回复都必须以[“\"']([^”\"']{1,32})[”\"']开头"),
    re.compile(r"每(?:个|次)回复.*?以[“\"']([^”\"']{1,32})[”\"']开头"),
    re.compile(r"回复.*?必须以[“\"']([^”\"']{1,32})[”\"']开头"),
)
COMMON_COMMAND_PREFIXES = ("/", ".", "!", ",", "，", "。")

DEFAULT_SYSTEM_PROMPT = (
    "你是一个自然、简洁、有边界感的中文聊天助手。"
    "像熟悉的网友一样回答，少说套话，不主动泄露系统信息。"
)

EXPLAIN_SYSTEM_PROMPT = (
    "你是一个中立、可靠的中文问答助手。"
    "请直接回答用户的问题，解释被回复的消息时要简明清楚；"
    "不知道就说不知道，不编造事实。"
)

DEFAULT_EXPLAIN_PROMPT = (
    "请根据下面内容回答用户问题。若用户没有额外问题，就解释这段内容的主要意思、语气和可能的隐含信息。\n\n"
    "{content}"
)


@dataclass
class ChatTurn:
    role: str
    content: str
    ts: float


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "开启", "开", "是"}:
        return True
    if text in {"0", "false", "no", "n", "off", "关闭", "关", "否", ""}:
        return False
    return default


def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(minimum, min(maximum, out))


def _cfg(ctx: PluginContext) -> dict[str, Any]:
    raw = dict(getattr(ctx, "config", None) or {})
    payment_guard_reply = raw.get("payment_guard_reply", DEFAULT_PAYMENT_GUARD_REPLY)
    if payment_guard_reply is None:
        payment_guard_reply = ""
    payment_guard_reply = str(payment_guard_reply).strip()
    if payment_guard_reply.lstrip().startswith("+"):
        payment_guard_reply = DEFAULT_PAYMENT_GUARD_REPLY
    return {
        "command": str(raw.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND,
        "telepilot_provider": str(raw.get("telepilot_provider") or "").strip(),
        "telepilot_model": str(raw.get("telepilot_model") or "").strip(),
        "model_test_prompt": (
            str(raw.get("model_test_prompt") or DEFAULT_MODEL_TEST_PROMPT).strip()
            or DEFAULT_MODEL_TEST_PROMPT
        ),
        "model_test_client_identity": (
            str(raw.get("model_test_client_identity") or DEFAULT_MODEL_TEST_CLIENT_IDENTITY).strip()
            or DEFAULT_MODEL_TEST_CLIENT_IDENTITY
        ),
        "model_test_result": str(raw.get("model_test_result") or DEFAULT_MODEL_TEST_RESULT),
        "timeout_seconds": _int(raw.get("timeout_seconds"), 60, 10, 600),
        "max_tokens": _int(raw.get("max_tokens"), 1200, 256, 8000),
        "max_output_chars": _int(raw.get("max_output_chars"), 0, 0, 20000),
        "protect_command_outputs": _bool(raw.get("protect_command_outputs"), True),
        "payment_guard_reply": payment_guard_reply,
        "safe_reply_prefix": str(raw.get("safe_reply_prefix") or ""),
        "blocked_bare_outputs": str(raw.get("blocked_bare_outputs") or DEFAULT_BLOCKED_BARE_OUTPUTS),
        "enable_private_chat": _bool(raw.get("enable_private_chat"), True),
        "enable_group_chat": _bool(raw.get("enable_group_chat"), True),
        "group_chat_ids": str(raw.get("group_chat_ids") or ""),
        "white_list_chats": str(raw.get("white_list_chats") or ""),
        "system_prompt": str(raw.get("system_prompt") or DEFAULT_SYSTEM_PROMPT).strip() or DEFAULT_SYSTEM_PROMPT,
        "max_history": _int(raw.get("max_history"), 10, 0, 40),
        "enable_explain_prompt": _bool(raw.get("enable_explain_prompt"), True),
        "explain_prompt": str(raw.get("explain_prompt") or DEFAULT_EXPLAIN_PROMPT),
        "strip_thinking": _bool(raw.get("strip_thinking"), True),
    }


def _command_prefix() -> str:
    try:
        return str(current_command_prefix(fallback=",") or ",")
    except Exception:  # pragma: no cover - compatibility with older runtimes
        return ","


def _parse_ids(raw: Any) -> set[int]:
    ids: set[int] = set()
    for item in re.findall(r"-?\d+", str(raw or "")):
        try:
            ids.add(int(item))
        except ValueError:
            continue
    return ids


def _event_text(event: Any) -> str:
    msg = getattr(event, "message", event)
    return str(
        getattr(event, "raw_text", None)
        or getattr(event, "text", None)
        or getattr(msg, "raw_text", None)
        or getattr(msg, "text", None)
        or getattr(msg, "message", None)
        or getattr(msg, "caption", None)
        or ""
    ).strip()


def _event_has_media(event: Any) -> bool:
    msg = getattr(event, "message", event)
    for attr in ("media", "photo", "document", "video", "audio", "voice", "sticker", "gif"):
        if getattr(event, attr, None) is not None or getattr(msg, attr, None) is not None:
            return True
    return False


def _message_text(message: Any) -> str:
    if message is None:
        return ""
    return str(
        getattr(message, "raw_text", None)
        or getattr(message, "text", None)
        or getattr(message, "message", None)
        or getattr(message, "caption", None)
        or ""
    ).strip()


def _event_chat_id(event: Any) -> int | None:
    msg = getattr(event, "message", event)
    raw = getattr(event, "chat_id", None) or getattr(msg, "chat_id", None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _message_id(message: Any) -> int | None:
    raw = getattr(message, "id", None) or getattr(getattr(message, "message", None), "id", None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_message_missing(message: Any) -> bool:
    if message is None:
        return True
    return message.__class__.__name__ == "MessageEmpty" or bool(getattr(message, "empty", False))


def _first_message(raw: Any) -> Any:
    if isinstance(raw, (list, tuple)):
        return raw[0] if raw else None
    return raw


def _sender_id(message: Any) -> int | None:
    msg = getattr(message, "message", message)
    raw = getattr(message, "sender_id", None) or getattr(msg, "sender_id", None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_outgoing(event: Any) -> bool:
    return bool(getattr(event, "out", False) or getattr(event, "outgoing", False) or getattr(event, "is_outgoing", False))


def _is_private(event: Any, chat_id: int | None) -> bool:
    if bool(getattr(event, "is_private", False)):
        return True
    return chat_id is not None and chat_id > 0 and not bool(getattr(event, "is_group", False))


def _chat_allowed(chat_id: int | None, raw_ids: str) -> bool:
    if chat_id is None:
        return False
    allowed = _parse_ids(raw_ids)
    return not allowed or chat_id in allowed


def _looks_like_command(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    prefix = _command_prefix()
    return stripped.startswith((prefix, "/", ".", "!"))


def _split_words(raw: Any) -> set[str]:
    words: set[str] = set()
    for item in re.split(r"[\s,，;；]+", str(raw or "")):
        text = item.strip()
        if text:
            words.add(text)
    return words


def _command_module() -> Any | None:
    try:
        return importlib.import_module("app.worker.command")
    except Exception:
        return None


def _available_command_words(cfg: dict[str, Any]) -> set[str]:
    """Best-effort TelePilot command inventory for output guarding."""

    words = _split_words(cfg.get("blocked_bare_outputs"))
    command = str(cfg.get("command") or DEFAULT_COMMAND).strip()
    if command:
        words.add(command)

    module = _command_module()
    if module is not None:
        for attr in ("_BUILTIN_ALIAS_TO_PRIMARY", "_PLUGIN_COMMANDS", "_BUILTIN"):
            value = getattr(module, attr, None)
            if isinstance(value, dict):
                words.update(str(key).strip() for key in value.keys() if str(key).strip())

    getter = get_command_context
    if getter is not None:
        try:
            command_ctx = getter()
        except Exception:
            command_ctx = None
        templates = getattr(command_ctx, "templates", None) or {}
        if isinstance(templates, dict):
            words.update(str(key).strip() for key in templates.keys() if str(key).strip())
            for tpl in templates.values():
                if not isinstance(tpl, dict):
                    continue
                words.update(_split_words(tpl.get("name")))
                words.update(_split_words("\n".join(str(x) for x in tpl.get("aliases") or [])))
        aliases = getattr(command_ctx, "aliases", None) or {}
        if isinstance(aliases, dict):
            words.update(str(key).strip() for key in aliases.keys() if str(key).strip())

    return {word for word in words if word}


def _required_reply_prefix(cfg: dict[str, Any]) -> str:
    configured = str(cfg.get("safe_reply_prefix") or "").strip()
    if configured:
        return configured[:32]
    prompt = str(cfg.get("system_prompt") or "")
    for pattern in REQUIRED_PREFIX_PATTERNS:
        match = pattern.search(prompt)
        if match:
            return match.group(1).strip()[:32]
    return ""


def _ensure_reply_prefix(text: str, prefix: str) -> str:
    if not prefix:
        return text
    stripped = text.lstrip()
    if stripped.startswith(prefix):
        return text
    return f"{prefix}{stripped}"


def _command_prefixes() -> tuple[str, ...]:
    prefixes = {_command_prefix(), *COMMON_COMMAND_PREFIXES}
    return tuple(prefix for prefix in prefixes if prefix)


def _starts_with_command_word(line: str, words: set[str]) -> bool:
    candidate = line.casefold()
    for word in sorted(words, key=len, reverse=True):
        folded = word.casefold()
        if not folded:
            continue
        if candidate == folded or candidate.startswith(folded + " "):
            return True
    return False


def _looks_like_executable_output(line: str, command_words: set[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _looks_like_payment_output(stripped):
        return True
    if stripped.startswith(_command_prefixes()):
        return True
    return _starts_with_command_word(stripped, command_words)


def _looks_like_payment_output(line: str) -> bool:
    return bool(PAYMENT_LIKE_RE.match(str(line or "").strip()))


def _first_executable_output_line(text: str, command_words: set[str]) -> str:
    for line in text.splitlines():
        if _looks_like_executable_output(line, command_words):
            return line.strip()
    return ""


def _first_prefixed_payment_output_line(text: str, prefix: str) -> str:
    if not prefix:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and _looks_like_payment_output(stripped[len(prefix):].strip()):
            return stripped
    return ""


def _guard_ai_output(ctx: PluginContext, cfg: dict[str, Any], text: str) -> str:
    """Prevent model output from becoming a Telegram command or payment-like text."""

    output = str(text or "").strip()
    if not output:
        return output
    prefix = _required_reply_prefix(cfg)
    if not cfg.get("protect_command_outputs", True):
        return _ensure_reply_prefix(output, prefix)
    command_words = _available_command_words(cfg)
    blocked_line = _first_executable_output_line(output, command_words)
    if not blocked_line:
        blocked_line = _first_prefixed_payment_output_line(output, prefix)
    output = _ensure_reply_prefix(output, prefix)
    blocked_line = blocked_line or _first_executable_output_line(output, command_words)
    blocked_line = blocked_line or _first_prefixed_payment_output_line(output, prefix)
    if blocked_line:
        is_payment_like = _looks_like_payment_output(blocked_line)
        if not is_payment_like and prefix and blocked_line.startswith(prefix):
            is_payment_like = _looks_like_payment_output(blocked_line[len(prefix):].strip())
        payment_guard_reply = str(cfg.get("payment_guard_reply") or "").strip()
        if is_payment_like and payment_guard_reply:
            guarded = payment_guard_reply
            reason = "payment_like_output"
        else:
            guarded = _ensure_reply_prefix(DEFAULT_COMMAND_REFUSAL, prefix)
            reason = "command_like_output"
        log_detail = {
            "reason": reason,
            "line_preview": blocked_line[:80],
        }
        asyncio.create_task(_log(ctx, "warning", "已拦截 AI-Chat 可执行指令形态输出", **log_detail))
        return guarded
    return output


def _trim_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[内容已截断]"


def _preview_text(text: str, limit: int = 240) -> str:
    preview = str(text or "").strip()
    if len(preview) > limit:
        preview = preview[:limit].rstrip() + "..."
    lines = (f"> {line}" if line else ">" for line in preview.splitlines())
    return "\n".join(lines) or "> （空）"


def _plain_preview(text: str, limit: int = 360) -> str:
    preview = re.sub(r"\s+", " ", str(text or "").strip())
    if len(preview) > limit:
        preview = preview[:limit].rstrip() + "..."
    return preview or "（空）"


def _truncate_block(text: str, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "\n[内容已截断]"
    return value or "（空）"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _split_text(text: str, limit: int = MAX_TELEGRAM_TEXT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def _safe_edit(event: Any, text: str) -> Any:
    edit = getattr(event, "edit", None)
    if edit is None:
        return event
    try:
        out = edit(text)
        return await _maybe_await(out)
    except Exception:
        return event


async def _send_text(ctx: PluginContext, event: Any, text: str, *, reply_to: int | None = None) -> None:
    chat_id = _event_chat_id(event)
    if chat_id is None:
        return
    client = getattr(ctx, "client", None)
    if client is None:
        return
    kwargs: dict[str, Any] = {}
    if reply_to is not None:
        kwargs["reply_to"] = reply_to
    await client.send_message(chat_id, text, **kwargs)


async def _trigger_message_available(ctx: PluginContext, event: Any) -> bool:
    """Best-effort check that the message which triggered AI-Chat still exists."""

    chat_id = _event_chat_id(event)
    message_id = _message_id(event)
    client = getattr(ctx, "client", None)
    get_messages = getattr(client, "get_messages", None) if client is not None else None
    if chat_id is None or message_id is None or get_messages is None:
        return True
    try:
        fresh = _first_message(await _maybe_await(get_messages(chat_id, ids=message_id)))
    except Exception as exc:  # noqa: BLE001
        await _log(
            ctx,
            "warning",
            "AI-Chat 原消息存在性检查失败，按兼容策略继续回复",
            chat_id=chat_id,
            message_id=message_id,
            error=_plain_preview(str(exc), 160),
        )
        return True
    if _is_message_missing(fresh):
        await _log(
            ctx,
            "info",
            "AI-Chat 触发消息已删除，跳过回复",
            chat_id=chat_id,
            message_id=message_id,
        )
        return False
    return True


async def _get_reply_message(event: Any) -> Any:
    getter = getattr(event, "get_reply_message", None)
    if getter is None:
        return None
    try:
        return await _maybe_await(getter())
    except Exception:
        return None


async def _log(ctx: PluginContext, level: str, message: str, **detail: Any) -> None:
    writer = getattr(ctx, "log", None)
    if writer is None:
        return
    try:
        await writer(level, message, **detail)
    except Exception:
        return


def _classify_ai_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    lower = text.lower()
    if "api_key" in lower or "authorization" in lower or "bearer" in lower:
        text = "(错误信息已脱敏)"
    if len(text) > 300:
        text = text[:300] + "..."
    if "ctx.ai" in lower or "ai_text" in lower:
        return "AI 调用失败：当前 TelePilot 没有向插件暴露 ctx.ai。请确认插件声明了 ai_text 权限，并在 TelePilot 里配置可用的 AI Provider。"
    if any(key in lower for key in ("model_not_found", "model not found", "no available channel")):
        return f"AI 模型不可用：{text}"
    if any(key in lower for key in ("401", "403", "unauthorized", "forbidden")):
        return f"AI 鉴权失败：{text}"
    if any(key in lower for key in ("429", "rate limit", "too many requests")):
        return f"AI 请求过于频繁：{text}"
    if any(key in lower for key in ("timeout", "timed out", "超时")):
        return f"AI 请求超时：{text}"
    return f"AI 调用失败：{text}"


@register
class AIChatPlugin(Plugin):
    """AI-Chat powered by TelePilot providers."""

    key = "ai-chat"
    display_name = "AI-Chat"
    message_channels = {"incoming"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self.commands = {DEFAULT_COMMAND: self._cmd_ai}
        self._history: dict[str, list[ChatTurn]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._me_id: int | None = None
        self._me_username: str = ""

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = _cfg(ctx)
        self.commands = {cfg["command"]: self._cmd_ai}
        await self._load_me(ctx)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._history.clear()
        self._locks.clear()
        self._me_id = None
        self._me_username = ""

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        if _is_outgoing(event):
            return
        has_media = _event_has_media(event)
        text = _event_text(event)
        if _looks_like_command(text):
            return
        if not text and not has_media:
            return

        cfg = _cfg(ctx)
        chat_id = _event_chat_id(event)
        if not _chat_allowed(chat_id, cfg["white_list_chats"]):
            return

        try:
            sender = await _maybe_await(getattr(event, "get_sender", lambda: None)())
        except Exception:
            sender = None
        if bool(getattr(sender, "bot", False) or getattr(sender, "is_bot", False)):
            return

        await self._load_me(ctx)
        sender_id = _sender_id(event)
        if sender_id is not None and self._me_id is not None and sender_id == self._me_id:
            return

        is_private = _is_private(event, chat_id)
        if has_media:
            await self._reply_media_unsupported(ctx, event, cfg, chat_id, is_private, text)
            return

        if is_private:
            if not cfg["enable_private_chat"]:
                return
            prompt_text = text
        else:
            if not cfg["enable_group_chat"] or not _chat_allowed(chat_id, cfg["group_chat_ids"]):
                return
            prompt_text = await self._group_prompt_text(event, text)
            if not prompt_text:
                return

        if chat_id is None:
            return
        key = self._history_key(ctx, chat_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not await _trigger_message_available(ctx, event):
                return
            await self._prune_history(key)
            history = self._history.get(key, [])
            user_prompt = self._build_chat_prompt(prompt_text, history)
            try:
                reply = await self._call_ai(ctx, cfg, cfg["system_prompt"], user_prompt, provider_tag="chat", source="plugin:ai-chat:chat")
            except Exception as exc:  # noqa: BLE001
                await _log(ctx, "warning", _classify_ai_error(exc), chat_id=chat_id)
                return

            reply = _guard_ai_output(ctx, cfg, reply)
            reply = _trim_text(reply, cfg["max_output_chars"])
            if not reply:
                return
            if not await _trigger_message_available(ctx, event):
                return
            self._remember(key, "user", prompt_text, cfg["max_history"])
            self._remember(key, "assistant", reply, cfg["max_history"])
            reply_to = _message_id(event) if not is_private else None
            for chunk in _split_text(reply):
                await _send_text(ctx, event, chunk, reply_to=reply_to)
                reply_to = None

    async def _reply_media_unsupported(
        self,
        ctx: PluginContext,
        event: Any,
        cfg: dict[str, Any],
        chat_id: int | None,
        is_private: bool,
        text: str,
    ) -> None:
        if chat_id is None:
            return
        if is_private:
            if not cfg["enable_private_chat"]:
                return
            reply_to = None
        else:
            if not cfg["enable_group_chat"] or not _chat_allowed(chat_id, cfg["group_chat_ids"]):
                return
            prompt_text = await self._group_prompt_text(event, text)
            if not prompt_text:
                return
            reply_to = _message_id(event)
        if not await _trigger_message_available(ctx, event):
            return
        await _send_text(ctx, event, DEFAULT_MEDIA_UNSUPPORTED_REPLY, reply_to=reply_to)

    async def _cmd_ai(self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext) -> None:
        cfg = _cfg(ctx)
        sub = (args[0].lower() if args else "").strip()
        if sub in {"help", "-h", "--help", "帮助"}:
            await _safe_edit(event, self._help_text(cfg))
            return
        if sub in {"reset", "clear", "forget", "清空", "重置"}:
            chat_id = _event_chat_id(event)
            if chat_id is not None:
                self._history.pop(self._history_key(ctx, chat_id), None)
            await _safe_edit(event, "已清空当前会话记忆。")
            return
        if sub in {"providers", "provider", "llm", "模型"}:
            await self._cmd_providers(event, ctx)
            return
        if sub in {"test", "check", "probe", "检测", "测试", "测活"}:
            test_prompt = " ".join(args[1:]).strip() or cfg["model_test_prompt"]
            await self._cmd_model_test(event, ctx, cfg, test_prompt)
            return

        query = " ".join(args).strip()
        reply = await _get_reply_message(event)
        reply_text = _message_text(reply)
        if not query and not reply_text:
            await _safe_edit(event, self._help_text(cfg))
            return

        content = self._build_command_content(query, reply_text)
        prompt = self._build_explain_prompt(cfg, content)
        await _safe_edit(event, "正在调用 AI，请稍等...")
        try:
            answer = await self._call_ai(
                ctx,
                cfg,
                EXPLAIN_SYSTEM_PROMPT,
                prompt,
                provider_tag="chat",
                source="plugin:ai-chat:command",
            )
        except Exception as exc:  # noqa: BLE001
            await _safe_edit(event, _classify_ai_error(exc))
            return

        answer = _guard_ai_output(ctx, cfg, answer)
        answer = _trim_text(answer, cfg["max_output_chars"])
        chunks = _split_text(answer or "AI 未返回内容。")
        await _safe_edit(event, chunks[0])
        for chunk in chunks[1:]:
            await _send_text(ctx, event, chunk)

    async def _cmd_providers(self, event: Any, ctx: PluginContext) -> None:
        ai = getattr(ctx, "ai", None)
        list_providers = getattr(ai, "list_providers", None) if ai is not None else None
        if list_providers is None:
            await _safe_edit(event, "当前插件上下文未提供 Provider 列表接口；请在 TelePilot 的 AI Provider 设置页查看。")
            return
        try:
            providers = list(await _maybe_await(list_providers()) or [])
        except Exception as exc:  # noqa: BLE001
            await _safe_edit(event, _classify_ai_error(exc))
            return
        if not providers:
            await _safe_edit(event, "TelePilot 尚未配置任何 AI Provider。")
            return
        lines = ["TelePilot 可用 AI Provider", ""]
        for provider in providers:
            get = provider.get if isinstance(provider, dict) else lambda key, default=None: getattr(provider, key, default)
            provider_id = get("id", "-")
            name = get("name", "-")
            model = get("default_model", get("model", "-"))
            tags = get("tags", [])
            tag_text = ",".join(tags) if isinstance(tags, list) else str(tags or "-")
            lines.append(f"{provider_id} {name}")
            lines.append(f"默认模型: {model} / 标签: {tag_text}")
            lines.append("")
        await _safe_edit(event, "\n".join(lines).strip())

    async def _cmd_model_test(
        self,
        event: Any,
        ctx: PluginContext,
        cfg: dict[str, Any],
        test_prompt: str,
    ) -> None:
        await _safe_edit(event, "正在测试 AI-Chat 模型，请稍等...")
        started = time.perf_counter()
        try:
            answer = await self._call_ai(
                ctx,
                cfg,
                cfg["system_prompt"],
                self._build_model_test_prompt(test_prompt, cfg["model_test_client_identity"]),
                provider_tag="chat",
                source="plugin:ai-chat:test",
            )
        except Exception as exc:  # noqa: BLE001
            error_text = _classify_ai_error(exc)
            await _log(
                ctx,
                "warning",
                error_text,
                provider=cfg["telepilot_provider"] or "auto",
                model=cfg["telepilot_model"] or "default",
            )
            await _safe_edit(event, f"AI-Chat 模型不可用\n\n{error_text}")
            return

        latency_ms = int((time.perf_counter() - started) * 1000)
        provider = cfg["telepilot_provider"] or "自动路由"
        model = cfg["telepilot_model"] or "默认模型"
        await _log(
            ctx,
            "info",
            "AI-Chat 模型测试成功",
            provider=provider,
            model=model,
            latency_ms=latency_ms,
        )
        await _safe_edit(
            event,
            "\n".join(
                [
                    "AI-Chat 模型可用",
                    f"Provider: {provider}",
                    f"Model: {model}",
                    f"耗时: {latency_ms}ms",
                    "",
                    "返回预览:",
                    _preview_text(answer),
                ]
            ),
        )

    async def on_config_action(
        self,
        ctx: PluginContext,
        action_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if action_key != "test_model_availability":
            return None

        current_config = dict(payload.get("config") or {})
        ctx.config = {**(getattr(ctx, "config", None) or {}), **current_config}
        action_input = dict(payload.get("input") or {})
        cfg = _cfg(ctx)
        test_prompt = str(action_input.get("test_message") or cfg["model_test_prompt"]).strip()
        if not test_prompt:
            test_prompt = DEFAULT_MODEL_TEST_PROMPT
        client_identity = str(
            action_input.get("client_identity") or cfg["model_test_client_identity"]
        ).strip() or DEFAULT_MODEL_TEST_CLIENT_IDENTITY
        started = time.perf_counter()
        provider = cfg["telepilot_provider"] or "自动路由"
        model = cfg["telepilot_model"] or "默认模型"
        try:
            answer, raw_answer, result = await self._complete_ai(
                ctx,
                cfg,
                cfg["system_prompt"],
                self._build_model_test_prompt(test_prompt, client_identity),
                provider_tag="chat",
                source="plugin:ai-chat:config-test",
            )
            model = str(getattr(result, "model", None) or model)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            error_text = _classify_ai_error(exc)
            error_preview = _plain_preview(error_text, 500)
            result_text = self._model_test_result_text(
                ok=False,
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                test_prompt=test_prompt,
                client_identity=client_identity,
                error=error_text,
            )
            await _log(
                ctx,
                "warning",
                f"AI-Chat 配置页模型测试失败：{error_preview}",
                provider=provider,
                model=model,
                latency_ms=latency_ms,
            )
            return {
                "message": f"AI-Chat 模型不可用：{_plain_preview(error_text, 180)}",
                "config_patch": {"model_test_result": result_text},
                "result": {
                    "ok": False,
                    "latency_ms": latency_ms,
                    "provider": provider,
                    "model": model,
                    "error": error_preview,
                    "model_test_result": result_text,
                },
            }

        latency_ms = int((time.perf_counter() - started) * 1000)
        if not answer:
            raw_preview = _plain_preview(raw_answer, 800)
            result_text = self._model_test_result_text(
                ok=False,
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                test_prompt=test_prompt,
                client_identity=client_identity,
                empty_response=True,
                response=raw_answer,
            )
            await _log(
                ctx,
                "warning",
                f"AI-Chat 配置页模型测试返回为空；模型原始返回：{raw_preview}",
                provider=provider,
                model=model,
                latency_ms=latency_ms,
            )
            return {
                "message": f"AI-Chat 模型请求已完成但没有可展示文本；模型原始返回：{_plain_preview(raw_answer, 180)}",
                "config_patch": {"model_test_result": result_text},
                "result": {
                    "ok": False,
                    "empty_response": True,
                    "latency_ms": latency_ms,
                    "provider": provider,
                    "model": model,
                    "response_preview": raw_preview,
                    "model_test_result": result_text,
                },
            }

        response_preview = _plain_preview(answer, 800)
        result_text = self._model_test_result_text(
            ok=True,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            test_prompt=test_prompt,
            client_identity=client_identity,
            response=answer,
        )
        await _log(
            ctx,
            "info",
            f"AI-Chat 配置页模型测试成功；模型实时返回：{response_preview}",
            provider=provider,
            model=model,
            latency_ms=latency_ms,
        )
        return {
            "message": f"AI-Chat 模型可用，模型返回：{_plain_preview(answer, 240)}",
            "config_patch": {"model_test_result": result_text},
            "result": {
                "ok": True,
                "latency_ms": latency_ms,
                "provider": provider,
                "model": model,
                "response_preview": response_preview,
                "model_test_result": result_text,
            },
        }

    async def _load_me(self, ctx: PluginContext) -> None:
        if self._me_id is not None:
            return
        client = getattr(ctx, "client", None)
        if client is None:
            return
        try:
            me = await client.get_me()
        except Exception:
            return
        try:
            self._me_id = int(getattr(me, "id", None) or 0) or None
        except (TypeError, ValueError):
            self._me_id = None
        self._me_username = str(getattr(me, "username", "") or "").strip().lower().lstrip("@")

    async def _group_prompt_text(self, event: Any, text: str) -> str:
        mentioned = False
        cleaned = text
        if self._me_username:
            pattern = re.compile(rf"@{re.escape(self._me_username)}\b", re.IGNORECASE)
            mentioned = bool(pattern.search(text))
            cleaned = pattern.sub("", text).strip()

        reply = await _get_reply_message(event)
        reply_to_me = bool(reply is not None and self._me_id is not None and _sender_id(reply) == self._me_id)
        if not (mentioned or reply_to_me):
            return ""
        return cleaned or "继续刚才的话题。"

    def _history_key(self, ctx: PluginContext, chat_id: int) -> str:
        return f"{getattr(ctx, 'account_id', 0)}:{chat_id}"

    async def _prune_history(self, key: str) -> None:
        history = self._history.get(key)
        if not history:
            return
        cutoff = time.time() - HISTORY_TTL_SECONDS
        kept = [turn for turn in history if turn.ts >= cutoff]
        if kept:
            self._history[key] = kept
        else:
            self._history.pop(key, None)

    def _remember(self, key: str, role: str, content: str, max_history: int) -> None:
        if max_history <= 0:
            self._history.pop(key, None)
            return
        turns = self._history.setdefault(key, [])
        turns.append(ChatTurn(role=role, content=content, ts=time.time()))
        if len(turns) > max_history:
            del turns[:-max_history]

    def _build_chat_prompt(self, text: str, history: list[ChatTurn]) -> str:
        lines = []
        if history:
            lines.append("以下是最近对话，请只把它当作上下文，不要逐字复述：")
            for turn in history[-20:]:
                name = "用户" if turn.role == "user" else "你"
                lines.append(f"{name}: {turn.content}")
            lines.append("")
        lines.append("用户刚刚说：")
        lines.append(text)
        return "\n".join(lines).strip()

    def _build_command_content(self, query: str, reply_text: str) -> str:
        if query and reply_text:
            return f"被回复消息：\n{reply_text}\n\n用户问题：\n{query}"
        if reply_text:
            return f"被回复消息：\n{reply_text}"
        return query

    def _build_explain_prompt(self, cfg: dict[str, Any], content: str) -> str:
        if not cfg["enable_explain_prompt"]:
            return content
        template = cfg["explain_prompt"] or DEFAULT_EXPLAIN_PROMPT
        try:
            return template.format(content=content)
        except Exception:
            return f"{DEFAULT_EXPLAIN_PROMPT}\n\n{content}"

    def _build_model_test_prompt(self, test_prompt: str, client_identity: str) -> str:
        return self._build_chat_prompt(test_prompt, [])

    def _model_test_result_text(
        self,
        *,
        ok: bool,
        provider: str,
        model: str,
        latency_ms: int,
        test_prompt: str,
        client_identity: str,
        response: str = "",
        error: str = "",
        empty_response: bool = False,
    ) -> str:
        lines = [
            f"状态：{self._model_test_status_text(ok=ok, empty_response=empty_response)}",
            f"时间：{_utc_timestamp()}",
            f"Provider：{provider}",
            f"Model：{model}",
            f"耗时：{latency_ms}ms",
            f"客户端标识：{_plain_preview(client_identity, 160)}",
            f"测试语：{_plain_preview(test_prompt, 160)}",
            HTTP_USER_AGENT_NOTE,
        ]
        if ok:
            lines.extend(
                [
                    "",
                    "模型实时返回：",
                    _truncate_block(response, 1200),
                    "",
                    "结果解读：模型返回了非空文本，说明 Provider 鉴权、模型路由、请求体和返回解析这条链路本次可用。",
                ]
            )
        elif empty_response:
            lines.extend(
                [
                    "",
                    "模型实时返回：",
                    _truncate_block(response, 1200)
                    if response
                    else "（TelePilot 收到的可展示文本为空）",
                    "",
                    "结果解读：上游请求已完成，但没有拿到可展示文本；这不等同于 Provider 不可用。"
                    "常见原因是测试语触发空回复、仅返回被隐藏的 <think> 内容，或上游返回结构里没有可解析文本。",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    f"错误：{_plain_preview(error, 500)}",
                    "",
                    "结果解读：本次没有拿到可用模型文本。请优先检查 Provider 鉴权、额度/限流、模型名、base_url 与上游服务状态。",
                ]
            )
        return "\n".join(lines)

    def _model_test_status_text(self, *, ok: bool, empty_response: bool) -> str:
        if ok:
            return "可用"
        if empty_response:
            return "返回为空"
        return "不可用"

    async def _complete_ai(
        self,
        ctx: PluginContext,
        cfg: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        *,
        provider_tag: str,
        source: str,
    ) -> tuple[str, str, Any]:
        ai = getattr(ctx, "ai", None)
        complete = getattr(ai, "complete", None) if ai is not None else None
        if complete is None:
            raise RuntimeError("当前 TelePilot 未向插件暴露 ctx.ai；请启用 ai_text 权限并配置 AI Provider。")
        result = await complete(
            system_prompt,
            user_prompt,
            provider=cfg["telepilot_provider"] or None,
            model=cfg["telepilot_model"] or None,
            provider_tag=provider_tag,
            max_tokens=cfg["max_tokens"],
            timeout_seconds=cfg["timeout_seconds"],
            source=source,
        )
        raw_text = str(getattr(result, "text", result) or "").strip()
        text = raw_text
        if cfg["strip_thinking"]:
            text = THINK_RE.sub("", text).strip()
        return text, raw_text, result

    async def _call_ai(
        self,
        ctx: PluginContext,
        cfg: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        *,
        provider_tag: str,
        source: str,
    ) -> str:
        text, _raw_text, _result = await self._complete_ai(
            ctx,
            cfg,
            system_prompt,
            user_prompt,
            provider_tag=provider_tag,
            source=source,
        )
        if not text:
            raise RuntimeError("TelePilot AI 返回内容为空")
        return text

    def _help_text(self, cfg: dict[str, Any]) -> str:
        prefix = _command_prefix()
        cmd = cfg["command"]
        return (
            "AI-Chat\n\n"
            f"{prefix}{cmd} 你的问题\n"
            f"回复一条消息后发送 {prefix}{cmd}，解释或回答这条消息\n"
            f"{prefix}{cmd} providers，查看 TelePilot 可用 AI Provider\n"
            f"{prefix}{cmd} test [测试语]，测试当前 AI Provider 与模型是否可用\n"
            f"{prefix}{cmd} reset，清空当前会话记忆\n\n"
            "私聊可直接对话；群聊中 @当前账号 或回复当前账号消息时触发。"
        )


__all__ = ["AIChatPlugin", "PLUGIN_VERSION"]
