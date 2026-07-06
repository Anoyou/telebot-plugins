"""TelePilot remote plugin: AI-Chat."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register

PLUGIN_VERSION = "0.1.0"
DEFAULT_COMMAND = "ask"
MAX_TELEGRAM_TEXT = 3900
HISTORY_TTL_SECONDS = 6 * 60 * 60
THINK_RE = re.compile(r"<think(?:ing)?\b[^>]*>[\s\S]*?</think(?:ing)?>", re.IGNORECASE)

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
    return {
        "command": str(raw.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND,
        "telepilot_provider": str(raw.get("telepilot_provider") or "").strip(),
        "telepilot_model": str(raw.get("telepilot_model") or "").strip(),
        "timeout_seconds": _int(raw.get("timeout_seconds"), 60, 10, 600),
        "max_tokens": _int(raw.get("max_tokens"), 1200, 256, 8000),
        "max_output_chars": _int(raw.get("max_output_chars"), 0, 0, 20000),
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


def _trim_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[内容已截断]"


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
        text = _event_text(event)
        if not text or _looks_like_command(text):
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
            await self._prune_history(key)
            history = self._history.get(key, [])
            user_prompt = self._build_chat_prompt(prompt_text, history)
            try:
                reply = await self._call_ai(ctx, cfg, cfg["system_prompt"], user_prompt, provider_tag="chat", source="plugin:ai-chat:chat")
            except Exception as exc:  # noqa: BLE001
                await _log(ctx, "warning", _classify_ai_error(exc), chat_id=chat_id)
                return

            reply = _trim_text(reply, cfg["max_output_chars"])
            if not reply:
                return
            self._remember(key, "user", prompt_text, cfg["max_history"])
            self._remember(key, "assistant", reply, cfg["max_history"])
            reply_to = _message_id(event) if not is_private else None
            for chunk in _split_text(reply):
                await _send_text(ctx, event, chunk, reply_to=reply_to)
                reply_to = None

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
        text = str(getattr(result, "text", result) or "").strip()
        if cfg["strip_thinking"]:
            text = THINK_RE.sub("", text).strip()
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
            f"{prefix}{cmd} reset，清空当前会话记忆\n\n"
            "私聊可直接对话；群聊中 @当前账号 或回复当前账号消息时触发。"
        )


__all__ = ["AIChatPlugin", "PLUGIN_VERSION"]
