"""监听 UserBot 私聊并通过插件专用 Bot 发送聚合通知。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register


VERSION = "0.1.2"
PENDING_PREFIX = "pending:"
MAX_PENDING_MESSAGES = 50
MAX_MESSAGE_LENGTH = 1200
MAX_NOTIFICATION_LENGTH = 4000
MAX_SEND_RETRIES = 3

MEDIA_LABELS = {
    "photo": "[图片]",
    "messagemediaphoto": "[图片]",
    "video": "[视频]",
    "messagemediadocument": "[文件或媒体]",
    "document": "[文件]",
    "audio": "[音频]",
    "voice": "[语音]",
    "sticker": "[贴纸]",
    "animation": "[动图]",
    "video_note": "[视频消息]",
    "contact": "[联系人]",
    "location": "[位置]",
    "venue": "[地点]",
    "poll": "[投票]",
    "dice": "[骰子]",
    "game": "[游戏]",
    "web_page": "[网页]",
    "story": "[动态]",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _aggregation_seconds(config: dict[str, Any]) -> int:
    value = _positive_int(config.get("aggregation_seconds"), 60)
    return min(600, max(10, value))


def _bot_id_from_token(token: str) -> int | None:
    prefix, separator, _secret = str(token or "").partition(":")
    if not separator or not prefix.isdigit():
        return None
    return int(prefix)


def _is_private_chat(payload: dict[str, Any]) -> bool:
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    chat_type = str(chat.get("type") or message.get("chat_type") or "").strip().lower()
    if chat_type in {"private", "user"}:
        return True
    if chat_type:
        return False
    try:
        return int(message.get("chat_id") or chat.get("id") or 0) > 0
    except (TypeError, ValueError):
        return False


def _sender_info(payload: dict[str, Any]) -> tuple[int, str, str]:
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    sender_id = _positive_int(sender.get("user_id") or sender.get("id"))
    username = str(sender.get("username") or "").strip().lstrip("@")
    display_name = str(sender.get("display_name") or sender.get("name") or "").strip()
    if not display_name:
        display_name = f"@{username}" if username else str(sender_id)
    return sender_id, display_name, username


def _message_content(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    if text:
        return text[:MAX_MESSAGE_LENGTH]
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    media_type = str(media.get("type") or "").strip()
    if media_type:
        return MEDIA_LABELS.get(media_type, MEDIA_LABELS.get(media_type.lower(), "[媒体消息]"))
    return "[非文本消息]"


def _monitored_account_name(ctx: PluginContext, payload: dict[str, Any]) -> str:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    config = ctx.config or {}
    value = (
        account.get("display_name")
        or account.get("name")
        or config.get("monitored_account_name")
        or (ctx.account_config or {}).get("monitored_account_name")
    )
    return str(value or f"账号 {getattr(ctx, 'account_id', '')}").strip()


def _format_notification(state: dict[str, Any]) -> str:
    monitored_name = html_escape(
        str(state.get("monitored_account_name") or "当前").strip(), quote=False
    )
    sender_name = str(state.get("display_name") or state.get("sender_id") or "未知联系人")
    username = str(state.get("username") or "").strip().lstrip("@")
    if username and f"@{username}" not in sender_name:
        sender_name = f"{sender_name}（@{username}）"
    sender_name = html_escape(sender_name, quote=False)

    raw_messages = state.get("messages") if isinstance(state.get("messages"), list) else []
    messages = [html_escape(str(item).strip(), quote=False) for item in raw_messages if str(item).strip()]
    if not messages:
        messages = ["[非文本消息]"]
    if len(messages) == 1:
        body = messages[0]
    else:
        body = "\n".join(f"{index}. {text}" for index, text in enumerate(messages, start=1))

    prefix = (
        f"<b>您的 {monitored_name}账号 收到来自 {sender_name} 发的消息</b>\n"
        "<b>消息内容：</b>\n"
    )
    suffix = "\n<b>请及时查看。</b>"
    available = max(0, MAX_NOTIFICATION_LENGTH - len(prefix) - len(suffix))
    if len(body) > available:
        body = body[: max(0, available - 8)].rstrip() + "\n[内容已截断]"
    return f"{prefix}{body}{suffix}"


def _merge_states(older: dict[str, Any], newer: dict[str, Any]) -> dict[str, Any]:
    old_messages = older.get("messages") if isinstance(older.get("messages"), list) else []
    new_messages = newer.get("messages") if isinstance(newer.get("messages"), list) else []
    merged = dict(newer or older)
    merged["messages"] = [*old_messages, *new_messages][-MAX_PENDING_MESSAGES:]
    merged["retry_count"] = max(
        _positive_int(older.get("retry_count")),
        _positive_int(newer.get("retry_count")),
    )
    return merged


@register
class PrivateChatAssistantPlugin(Plugin):
    key = "private_chat_assistant"
    display_name = "私聊助理"
    message_channels = {"incoming"}
    owner_only = False

    def __init__(self) -> None:
        super().__init__()
        self._memory_pending: dict[int, dict[str, Any]] = {}
        self._sender_locks: dict[int, asyncio.Lock] = {}

    def _sender_lock(self, sender_id: int) -> asyncio.Lock:
        return self._sender_locks.setdefault(sender_id, asyncio.Lock())

    async def on_startup(self, ctx: PluginContext) -> None:
        if ctx.log is not None:
            missing = []
            if not str((ctx.config or {}).get("bot_token") or "").strip():
                missing.append("专用通知 Bot Token")
            if not _positive_int((ctx.config or {}).get("recipient_chat_id")):
                missing.append("通知接收 Chat ID")
            if missing:
                await ctx.log("warn", f"[private_chat_assistant] 缺少配置：{'、'.join(missing)}")
            else:
                await ctx.log("info", f"[private_chat_assistant] v{VERSION} 已启动")

        if ctx.storage is None or not getattr(ctx.storage, "available", False):
            return
        stored = await ctx.storage.get_all()
        for key, state in stored.items():
            if not key.startswith(PENDING_PREFIX) or not isinstance(state, dict):
                continue
            sender_id = _positive_int(state.get("sender_id") or key[len(PENDING_PREFIX) :])
            if not sender_id:
                continue
            self._memory_pending[sender_id] = dict(state)
            self._schedule_sender(ctx, sender_id, str(state.get("fire_at") or ""))

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.scheduler is not None:
            ctx.scheduler.unregister_all()
        if ctx.log is not None:
            await ctx.log("info", f"[private_chat_assistant] v{VERSION} 已停止")

    async def on_event(
        self, ctx: PluginContext, payload: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        if source.get("channel") != "userbot" or source.get("type") != "message":
            return None
        if not _is_private_chat(payload):
            return None

        config = ctx.config or {}
        bot_token = str(config.get("bot_token") or "").strip()
        recipient_chat_id = _positive_int(config.get("recipient_chat_id"))
        if not bot_token or not recipient_chat_id:
            return None

        sender_id, display_name, username = _sender_info(payload)
        if not sender_id or sender_id == _bot_id_from_token(bot_token):
            return None

        immediate_flush = ctx.scheduler is None
        async with self._sender_lock(sender_id):
            now = _utc_now()
            state = await self._load_pending(ctx, sender_id)
            if not state:
                fire_at = now + timedelta(seconds=_aggregation_seconds(config))
                state = {
                    "sender_id": sender_id,
                    "monitored_account_name": _monitored_account_name(ctx, payload),
                    "display_name": display_name,
                    "username": username,
                    "messages": [],
                    "created_at": now.isoformat(),
                    "fire_at": fire_at.isoformat(),
                    "retry_count": 0,
                }
            else:
                state["display_name"] = display_name or state.get("display_name")
                state["username"] = username or state.get("username")

            messages = state.get("messages") if isinstance(state.get("messages"), list) else []
            state["messages"] = [*messages, _message_content(payload)][-MAX_PENDING_MESSAGES:]
            state["updated_at"] = now.isoformat()
            await self._save_pending(ctx, sender_id, state)
            if not immediate_flush:
                self._schedule_sender(ctx, sender_id, str(state.get("fire_at") or ""))
        if immediate_flush:
            await self._flush_sender(ctx, sender_id)
        return None

    def _schedule_sender(self, ctx: PluginContext, sender_id: int, fire_at: str) -> None:
        if ctx.scheduler is None:
            return
        try:
            scheduled_at = datetime.fromisoformat(fire_at)
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            scheduled_at = _utc_now() + timedelta(seconds=_aggregation_seconds(ctx.config or {}))
        if scheduled_at <= _utc_now():
            scheduled_at = _utc_now() + timedelta(seconds=1)

        async def callback(_job: Any) -> None:
            await self._flush_sender(ctx, sender_id)

        ctx.scheduler.register(
            f"private_digest_{sender_id}",
            {"kind": "once", "fire_at": scheduled_at.isoformat()},
            callback,
            replace=True,
        )

    async def _flush_sender(self, ctx: PluginContext, sender_id: int) -> None:
        async with self._sender_lock(sender_id):
            state = await self._load_pending(ctx, sender_id)
            if not state:
                return
            await self._delete_pending(ctx, sender_id)

            sent = await self._send_notification(ctx, _format_notification(state))
            if sent:
                if ctx.log is not None:
                    await ctx.log(
                        "info",
                        "[private_chat_assistant] 私聊聚合通知发送成功",
                        sender_id=sender_id,
                        message_count=len(state.get("messages") or []),
                    )
                return

            retry_count = _positive_int(state.get("retry_count")) + 1
            if retry_count > MAX_SEND_RETRIES:
                if ctx.log is not None:
                    await ctx.log(
                        "error",
                        "[private_chat_assistant] 私聊通知连续发送失败，已停止自动重试，请检查 Bot Token、接收 Chat ID 与代理配置",
                        sender_id=sender_id,
                    )
                return

            newer = await self._load_pending(ctx, sender_id)
            retry_state = _merge_states(state, newer or {})
            retry_state["retry_count"] = retry_count
            retry_state["fire_at"] = (
                _utc_now() + timedelta(seconds=_aggregation_seconds(ctx.config or {}))
            ).isoformat()
            await self._save_pending(ctx, sender_id, retry_state)
            self._schedule_sender(ctx, sender_id, str(retry_state["fire_at"]))
            if ctx.log is not None:
                await ctx.log(
                    "warn",
                    "[private_chat_assistant] 私聊通知发送失败，稍后自动重试",
                    sender_id=sender_id,
                    retry_count=retry_count,
                )

    async def _send_notification(self, ctx: PluginContext, text: str) -> bool:
        config = ctx.config or {}
        bot_token = str(config.get("bot_token") or "").strip()
        recipient_chat_id = _positive_int(config.get("recipient_chat_id"))
        if ctx.http is None or not bot_token or not recipient_chat_id:
            return False
        try:
            response = await ctx.http.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": recipient_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if response.status_code != 200:
                return False
            data = response.json()
            return bool(isinstance(data, dict) and data.get("ok") is True)
        except Exception:
            return False

    async def _load_pending(self, ctx: PluginContext, sender_id: int) -> dict[str, Any]:
        memory = self._memory_pending.get(sender_id)
        if memory is not None:
            return dict(memory)
        if ctx.storage is None or not getattr(ctx.storage, "available", False):
            return {}
        stored = await ctx.storage.get(f"{PENDING_PREFIX}{sender_id}", default={})
        if isinstance(stored, dict) and stored:
            self._memory_pending[sender_id] = dict(stored)
            return dict(stored)
        return {}

    async def _save_pending(
        self, ctx: PluginContext, sender_id: int, state: dict[str, Any]
    ) -> None:
        self._memory_pending[sender_id] = dict(state)
        if ctx.storage is not None and getattr(ctx.storage, "available", False):
            await ctx.storage.set(
                f"{PENDING_PREFIX}{sender_id}",
                state,
                ttl=max(3600, _aggregation_seconds(ctx.config or {}) * 10),
            )

    async def _delete_pending(self, ctx: PluginContext, sender_id: int) -> None:
        self._memory_pending.pop(sender_id, None)
        if ctx.storage is not None and getattr(ctx.storage, "available", False):
            await ctx.storage.delete(f"{PENDING_PREFIX}{sender_id}")


__all__ = [
    "PrivateChatAssistantPlugin",
    "_bot_id_from_token",
    "_format_notification",
    "_is_private_chat",
    "_message_content",
]
