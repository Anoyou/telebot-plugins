"""Bot 防广告守卫远程模块。

严格遵循 TelePilot 远程模块沙箱：只通过 ``ctx.client`` 调用已声明的
``delete_message`` / ``send_message`` 能力，不访问 raw client、不做成员管理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register


VERSION = "1.0.2"
TOKEN_SPLIT_RE = re.compile(r"[\s,，;；]+")
BOT_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_.])@([A-Za-z0-9_]{2,29}bot)"
    r"(?=$|[\s,，;；:：!！?？、（()）\[\]【】<>《》\"'“”‘’]|\.(?:\s|$))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedTargets:
    ids: set[int]
    names: set[str]


def _split_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = TOKEN_SPLIT_RE.split(str(value))
    return [item for item in raw_items if item]


def _parse_targets(value: Any) -> ParsedTargets:
    ids: set[int] = set()
    names: set[str] = set()
    for token in _split_tokens(value):
        normalized = token.strip()
        if not normalized:
            continue
        if normalized.startswith("@"):
            names.add(normalized[1:].lower())
            continue
        try:
            ids.add(int(normalized))
        except ValueError:
            names.add(normalized.lower())
    return ParsedTargets(ids=ids, names=names)


def _chat_id(event: Any) -> int:
    raw = getattr(event, "chat_id", None)
    channel_id = getattr(raw, "channel_id", None)
    if channel_id is not None:
        return int(channel_id)
    return int(raw or 0)


def _id_variants(raw_id: int) -> set[int]:
    variants = {raw_id, abs(raw_id)}
    abs_str = str(abs(raw_id))
    if abs_str.startswith("100") and len(abs_str) > 3:
        try:
            variants.add(int(abs_str[3:]))
        except ValueError:
            pass
    return variants


def _message_id(event: Any) -> int | None:
    msg = getattr(event, "message", event)
    raw = getattr(msg, "id", None)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _message_text(event: Any) -> str:
    msg = getattr(event, "message", event)
    return str(
        getattr(event, "raw_text", None)
        or getattr(msg, "raw_text", None)
        or getattr(msg, "message", None)
        or ""
    )


def _sender_id(event: Any) -> int:
    raw = getattr(event, "sender_id", None)
    if raw is None:
        raw = getattr(getattr(event, "message", event), "sender_id", None)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _entity_id(entity: Any) -> int:
    raw = getattr(entity, "id", entity)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _entity_username(entity: Any) -> str:
    return str(getattr(entity, "username", "") or "").strip("@").lower()


def _bot_mentions(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in BOT_MENTION_RE.finditer(text or ""):
        username = match.group(1).lower()
        if username in seen:
            continue
        seen.add(username)
        out.append(username)
    return out


@register
class BotMuteGuardPlugin(Plugin):
    key = "bot_mute_guard"
    display_name = "Bot 防广告守卫"
    message_channels = {"incoming"}
    owner_only = False

    def __init__(self) -> None:
        super().__init__()
        self._targets = ParsedTargets(ids=set(), names=set())
        self._allowed_bots = ParsedTargets(ids=set(), names=set())
        self._delete_untrusted_bot_mentions = True
        self._delete_inline_bot_messages = True
        self._delete_bot_sender_messages = True
        self._delete_join_messages_for_known_bots = True
        self._announce = False
        self._dry_run = False

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._targets = _parse_targets(cfg.get("target_chats", ""))
        self._allowed_bots = _parse_targets(cfg.get("allowed_bots", ""))
        self._delete_untrusted_bot_mentions = bool(
            cfg.get("delete_untrusted_bot_mentions", True)
        )
        self._delete_inline_bot_messages = bool(cfg.get("delete_inline_bot_messages", True))
        self._delete_bot_sender_messages = bool(cfg.get("delete_bot_sender_messages", True))
        self._delete_join_messages_for_known_bots = bool(
            cfg.get("delete_join_messages_for_known_bots", True)
        )
        self._announce = bool(cfg.get("announce", False))
        self._dry_run = bool(cfg.get("dry_run", False))

        if ctx.log:
            if not self._targets.ids and not self._targets.names:
                await ctx.log(
                    "warn",
                    f"[bot_mute_guard] v{VERSION} 未配置 target_chats，模块不会处理任何群组。",
                )
            else:
                await ctx.log(
                    "info",
                    (
                        f"[bot_mute_guard] v{VERSION} 已启动，目标群数量："
                        f"{len(self._targets.ids) + len(self._targets.names)}，"
                        f"白名单 Bot 数量：{len(self._allowed_bots.ids) + len(self._allowed_bots.names)}"
                    ),
                )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.log:
            await ctx.log("info", f"[bot_mute_guard] v{VERSION} 已停止")

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        chat_id = _chat_id(event)
        if not chat_id or not self._is_target_chat(event, chat_id):
            return

        if self._delete_join_messages_for_known_bots:
            handled = await self._handle_join_action(ctx, event, chat_id)
            if handled:
                return

        if self._delete_untrusted_bot_mentions:
            handled = await self._handle_untrusted_bot_mentions(ctx, event, chat_id)
            if handled:
                return

        if self._delete_inline_bot_messages:
            handled = await self._handle_inline_bot_message(ctx, event, chat_id)
            if handled:
                return

        if self._delete_bot_sender_messages:
            await self._handle_bot_sender(ctx, event, chat_id)

    def _is_target_chat(self, event: Any, chat_id: int) -> bool:
        if not self._targets.ids and not self._targets.names:
            return False
        if self._targets.ids.intersection(_id_variants(chat_id)):
            return True
        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", event), "chat", None)
        chat_name = _entity_username(chat)
        return bool(chat_name and chat_name in self._targets.names)

    async def _handle_join_action(
        self,
        ctx: PluginContext,
        event: Any,
        chat_id: int,
    ) -> bool:
        msg = getattr(event, "message", event)
        action = getattr(msg, "action", None)
        if action is None:
            return False

        candidates: list[Any] = []
        users = getattr(action, "users", None)
        if users:
            candidates.extend(list(users))
        user = getattr(action, "user", None)
        if user is not None:
            candidates.append(user)

        blocked: list[str] = []
        for candidate in candidates:
            if not bool(getattr(candidate, "bot", False)):
                continue
            if self._is_allowed_bot_ref(candidate):
                continue
            username = _entity_username(candidate)
            blocked.append(f"@{username}" if username else f"id:{_entity_id(candidate)}")

        if not blocked:
            return False

        reason = "检测到非白名单 Bot 入群服务消息：" + "、".join(blocked)
        await self._handle_violation(ctx, chat_id, _message_id(event), reason, event)
        return True

    async def _handle_untrusted_bot_mentions(
        self,
        ctx: PluginContext,
        event: Any,
        chat_id: int,
    ) -> bool:
        mentions = _bot_mentions(_message_text(event))
        if not mentions:
            return False

        blocked = [username for username in mentions if username not in self._allowed_bots.names]
        if not blocked:
            return False

        reason = "提及非白名单 Bot：" + "、".join(f"@{name}" for name in blocked)
        await self._handle_violation(ctx, chat_id, _message_id(event), reason, event)
        return True

    async def _handle_inline_bot_message(
        self,
        ctx: PluginContext,
        event: Any,
        chat_id: int,
    ) -> bool:
        msg = getattr(event, "message", event)
        via_ref = getattr(msg, "via_bot_id", None) or getattr(msg, "via_bot", None)
        if not via_ref or self._is_allowed_bot_ref(via_ref):
            return False

        label = self._bot_label(via_ref)
        reason = f"使用非白名单 inline Bot：{label}"
        await self._handle_violation(ctx, chat_id, _message_id(event), reason, event)
        return True

    async def _handle_bot_sender(
        self,
        ctx: PluginContext,
        event: Any,
        chat_id: int,
    ) -> bool:
        sender = getattr(event, "sender", None) or getattr(getattr(event, "message", event), "sender", None)
        if not sender or not bool(getattr(sender, "bot", False)):
            return False
        if self._is_allowed_bot_ref(sender):
            return False

        reason = f"非白名单 Bot 发言：{self._bot_label(sender)}"
        await self._handle_violation(ctx, chat_id, _message_id(event), reason, event)
        return True

    def _is_allowed_bot_ref(self, ref: Any) -> bool:
        bot_id = _entity_id(ref)
        username = _entity_username(ref)
        if bot_id and bot_id in self._allowed_bots.ids:
            return True
        return bool(username and username in self._allowed_bots.names)

    def _bot_label(self, ref: Any) -> str:
        username = _entity_username(ref)
        if username:
            return f"@{username}"
        bot_id = _entity_id(ref)
        return f"id:{bot_id}" if bot_id else str(ref)

    async def _handle_violation(
        self,
        ctx: PluginContext,
        chat_id: int,
        message_id: int | None,
        reason: str,
        event: Any,
    ) -> None:
        sender_id = _sender_id(event)
        if self._dry_run:
            await self._log(ctx, "info", f"演练模式：不会删除消息，原因：{reason}", chat_id, message_id, sender_id)
            return

        if message_id:
            deleted = await self._delete_message(ctx, chat_id, message_id, reason, sender_id)
        else:
            deleted = False
            await self._log(ctx, "warn", f"命中规则但缺少 message_id，无法删除消息，原因：{reason}", chat_id, None, sender_id)

        if deleted and self._announce:
            await self._send_notice(ctx, chat_id, reason)

    async def _delete_message(
        self,
        ctx: PluginContext,
        chat_id: int,
        message_id: int,
        reason: str,
        sender_id: int,
    ) -> bool:
        if ctx.client is None:
            await self._log(ctx, "error", "ctx.client 未初始化，无法删除消息", chat_id, message_id, sender_id)
            return False
        try:
            await ctx.client.delete_messages(chat_id, message_id)
        except PermissionError as exc:
            await self._log(
                ctx,
                "error",
                f"缺少 delete_message 权限，无法删除消息：{exc}",
                chat_id,
                message_id,
                sender_id,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            await self._log(
                ctx,
                "error",
                f"删除违规消息失败：{type(exc).__name__}: {exc}",
                chat_id,
                message_id,
                sender_id,
            )
            return False

        await self._log(ctx, "info", f"已删除违规消息，原因：{reason}", chat_id, message_id, sender_id)
        return True

    async def _send_notice(self, ctx: PluginContext, chat_id: int, reason: str) -> None:
        if ctx.client is None:
            return
        try:
            await ctx.client.send_message(chat_id, f"已删除疑似 Bot 广告触发消息：{reason}")
        except PermissionError as exc:
            await self._log(ctx, "error", f"缺少 send_message 权限，无法发送提示：{exc}", chat_id, None, 0)
        except Exception as exc:  # noqa: BLE001
            await self._log(ctx, "warn", f"发送群内提示失败：{type(exc).__name__}: {exc}", chat_id, None, 0)

    async def _log(
        self,
        ctx: PluginContext,
        level: str,
        message: str,
        chat_id: int,
        message_id: int | None,
        sender_id: int,
    ) -> None:
        if not ctx.log:
            return
        await ctx.log(
            level,
            f"[bot_mute_guard] {message}",
            chat_id=chat_id,
            message_id=message_id,
            sender_id=sender_id or None,
        )


PLUGIN_CLASS = BotMuteGuardPlugin

__all__ = ["BotMuteGuardPlugin", "PLUGIN_CLASS"]
