"""近期发言回复测试插件。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.worker.plugins.base import (
    Plugin,
    PluginContext,
    register,
    resolve_public_sender_identity,
    sanitize_public_display_name,
)
from app.worker.plugins.events import event_from_interaction_payload
from telethon.tl.types import PeerUser

PLUGIN_KEY = "reply_anchor_test"
ENTRY_KEY = "reply_to_recent_message"
NAME_ENTRY_KEY = "resolve_public_name"
DEFAULT_COMMAND = "send"
NAME_COMMAND = "name"
DEFAULT_NAME_RESULT_TEMPLATE = (
    "用户公开信息：\n"
    "TG 姓名：{tg_name}\n"
    "TG 用户名：{tg_username}\n"
    "TG ID：{tg_id}\n"
    "在本群是否管理员：{is_admin}\n"
    "在本群的小尾巴：{tag}"
)
LEGACY_NAME_RESULT_TEMPLATE = (
    "TelePilot 公开姓名解析结果\n"
    "公开姓名：{display_name}\n"
    "身份状态：{identity_status}\n"
    "管理员/成员标签：{tag}\n"
    "解析状态：{resolved_status}"
)
DEFAULT_SEARCH_LIMIT = 200
MAX_SEARCH_LIMIT = 500


@dataclass(frozen=True)
class _TargetPublicProfile:
    user_id: int
    name: str = ""
    username: str = ""
    tag: str = ""
    from_message: bool = False


def _clean_command(value: Any) -> str:
    command = str(value or "").strip().lstrip("/")
    return command or DEFAULT_COMMAND


def _search_limit(config: dict[str, Any] | None) -> int:
    try:
        value = int((config or {}).get("reply_to_search_limit", DEFAULT_SEARCH_LIMIT))
    except (TypeError, ValueError):
        value = DEFAULT_SEARCH_LIMIT
    return max(1, min(MAX_SEARCH_LIMIT, value))


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _args_from_payload(payload: dict[str, Any]) -> list[str]:
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    raw_args = trigger.get("args")
    if isinstance(raw_args, list):
        return [str(item).strip() for item in raw_args if str(item).strip()]
    args_raw = str(trigger.get("args_raw") or "").strip()
    return args_raw.split() if args_raw else []


def _numbers_from_text(text: str) -> list[str]:
    return re.findall(r"(?<!\d)\d+(?!\d)", text or "")


def _parse_target(args: list[str], *, fallback_text: str = "") -> tuple[int | None, int | None]:
    if len(args) >= 2:
        user_id = _positive_int(args[0])
        amount = _positive_int(args[1])
        if user_id is not None and amount is not None:
            return user_id, amount

    numbers = _numbers_from_text(fallback_text)
    if len(numbers) >= 2:
        return _positive_int(numbers[0]), _positive_int(numbers[1])
    return None, None


def _parse_user_id(args: list[str], *, fallback_text: str = "") -> int | None:
    if args:
        user_id = _positive_int(args[0])
        if user_id is not None:
            return user_id
    numbers = _numbers_from_text(fallback_text)
    return _positive_int(numbers[0]) if numbers else None


async def _reply_target_identity(
    ctx: PluginContext,
    chat_id: int,
    message_id: int | None,
) -> _TargetPublicProfile | None:
    if message_id is None:
        return None
    client = getattr(ctx, "client", None)
    get_messages = getattr(client, "get_messages", None) if client is not None else None
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
    get_sender = getattr(message, "get_sender", None)
    if sender is None and callable(get_sender):
        try:
            sender = await get_sender()
        except Exception:
            sender = None
    if sender is not None and int(getattr(sender, "id", 0) or 0) != user_id:
        sender = None
    raw_name = " ".join(
        value
        for value in (
            str(getattr(sender, "first_name", "") or "").strip(),
            str(getattr(sender, "last_name", "") or "").strip(),
        )
        if value
    )
    return _TargetPublicProfile(
        user_id=user_id,
        name=sanitize_public_display_name(raw_name, fallback=""),
        username=str(getattr(sender, "username", "") or "").strip().lstrip("@"),
        tag=sanitize_public_display_name(getattr(message, "from_rank", None), fallback=""),
        from_message=True,
    )


def _usage_text(command: str, limit: int) -> str:
    return (
        f"用法：{command} 用户ID 金额\n"
        f"示例：{command} 123456789 88\n"
        "如果账号启用了系统命令前缀，请在命令前加上当前系统前缀。\n"
        f"平台会在当前群最多向前搜索 {limit} 条消息，找到该用户最近一次发言后回复 +金额。"
    )


def _name_usage_text() -> str:
    return (
        f"用法一：回复目标用户的消息后发送 {NAME_COMMAND}\n"
        f"用法二：{NAME_COMMAND} 用户ID\n"
        f"示例：{NAME_COMMAND} 123456789\n"
        "平台只返回经过安全身份解析和 Unicode 清洗后的公开姓名，不会回显原始姓名。"
    )


def _identity_result_values(identity: Any, profile: _TargetPublicProfile) -> dict[str, str]:
    resolved = bool(getattr(identity, "resolved", False))
    anonymous = bool(getattr(identity, "is_anonymous_admin", False))
    admin = bool(getattr(identity, "is_admin", False))
    if not resolved:
        identity_type = "未确认（安全回退）"
        tg_name = "未确认"
        tg_username = "未确认"
        tg_id = "未确认"
        admin_status = "未确认"
    elif anonymous:
        identity_type = "匿名管理员"
        tg_name = "不可公开"
        tg_username = "不可公开"
        tg_id = "不可公开"
        admin_status = "是"
    else:
        identity_type = "非匿名公开身份"
        tg_name = profile.name or ("未获取" if not profile.from_message else "无")
        tg_username = f"@{profile.username}" if profile.username else (
            "无" if profile.from_message else "未获取"
        )
        tg_id = str(profile.user_id)
        admin_status = "是" if admin else "否"
    tag = str(getattr(identity, "tag", None) or profile.tag or "无")
    return {
        "tg_name": tg_name,
        "tg_username": tg_username,
        "tg_id": tg_id,
        "is_admin": admin_status,
        "admin_status": admin_status,
        "username": tg_username,
        "user_id": tg_id,
        "display_name": str(identity.display_name),
        "identity_status": identity_type,
        "tag": tag,
        "resolved_status": "已确认" if resolved else "未确认",
    }


class _SafeTemplateValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _identity_result_text(
    ctx: PluginContext,
    identity: Any,
    profile: _TargetPublicProfile,
) -> str:
    values = _identity_result_values(identity, profile)
    configured = str((ctx.config or {}).get("name_result_template") or "").strip()
    template = DEFAULT_NAME_RESULT_TEMPLATE if configured in {"", LEGACY_NAME_RESULT_TEMPLATE} else configured
    try:
        return template.format_map(_SafeTemplateValues(values))
    except (KeyError, ValueError):
        return DEFAULT_NAME_RESULT_TEMPLATE.format_map(values)


@register
class ReplyAnchorTestPlugin(Plugin):
    key = PLUGIN_KEY
    display_name = "近期发言回复测试"
    owner_only = True
    message_channels = {"outgoing"}
    command_config_keys = {"command", "reply_to_search_limit"}

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self.commands: dict[str, Any] = {}

    async def on_startup(self, ctx: PluginContext) -> None:
        self._command = _clean_command((ctx.config or {}).get("command"))
        # 当前 runtime 的 interaction_entries.triggers.command 是静态注册。
        # 默认 send 走最新 on_interaction 入口；配置为其它命令时，补充注册一个
        # 兼容命令入口，但仍通过 ctx.messages 走平台 MessageOps/Trace/限流。
        self.commands = {}
        if self._command != DEFAULT_COMMAND:
            self.commands[self._command] = self._handle_configured_command
        if ctx.log:
            await ctx.log("info", f"[reply_anchor_test] 已启动，默认命令：{DEFAULT_COMMAND}，配置命令：{self._command}")

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key not in {ENTRY_KEY, NAME_ENTRY_KEY}:
            return None
        event = event_from_interaction_payload(payload)
        if event.type != "command":
            return [{"type": "end_session"}]

        if entry_key == NAME_ENTRY_KEY:
            args = _args_from_payload(payload)
            user_id = _parse_user_id(args, fallback_text=event.message.text or "")
            profile = _TargetPublicProfile(user_id=user_id) if user_id is not None else None
            target_source = "user_id"
            if user_id is None:
                profile = await _reply_target_identity(
                    ctx,
                    int(event.message.chat_id),
                    event.message.reply_to_message_id,
                )
                user_id = profile.user_id if profile is not None else None
                target_source = "reply_message"
            if user_id is None:
                return [
                    {
                        "type": "send_message",
                        "chat_id": event.message.chat_id,
                        "reply_to_message_id": event.message.message_id,
                        "text": _name_usage_text(),
                    },
                    {"type": "end_session"},
                ]
            identity = await resolve_public_sender_identity(
                ctx,
                chat_id=int(event.message.chat_id),
                user_id=user_id,
                fallback_display_name=profile.name if profile is not None else "",
            )
            assert profile is not None
            return [
                {
                    "type": "send_message",
                    "chat_id": event.message.chat_id,
                    "reply_to_message_id": event.message.message_id,
                    "text": _identity_result_text(ctx, identity, profile),
                    "parse_mode": "plain",
                },
                {
                    "type": "result",
                    "success": True,
                    "result": {
                        "target_user_id": user_id,
                        "target_display_name": identity.display_name,
                        "is_anonymous_admin": bool(identity.is_anonymous_admin),
                        "tag": identity.tag,
                        "resolved": bool(identity.resolved),
                        "target_source": target_source,
                    },
                },
                {"type": "end_session"},
            ]

        limit = _search_limit(ctx.config)
        user_id, amount = _parse_target(_args_from_payload(payload), fallback_text=event.message.text or "")
        if user_id is None or amount is None:
            return [
                {
                    "type": "send_message",
                    "chat_id": event.message.chat_id,
                    "reply_to_message_id": event.message.message_id,
                    "text": _usage_text(DEFAULT_COMMAND, limit),
                },
                {"type": "end_session"},
            ]

        identity = await resolve_public_sender_identity(
            ctx,
            chat_id=int(event.message.chat_id),
            user_id=user_id,
        )

        return [
            {
                "type": "payout",
                "chat_id": event.message.chat_id,
                "amount": amount,
                "text": f"+{amount}",
                "parse_mode": "plain",
                "reply_to_user_id": user_id,
                "reply_to_display_name": identity.display_name,
                "reply_to_search_limit": limit,
            },
            {
                "type": "result",
                "success": True,
                "result": {
                    "target_user_id": user_id,
                    "target_display_name": identity.display_name,
                    "amount": amount,
                    "reply_to_search_limit": limit,
                },
            },
            {"type": "end_session"},
        ]

    async def _handle_configured_command(
        self,
        _client: Any,
        event: Any,
        args: list[str],
        _account_id: int,
        ctx: PluginContext,
    ) -> None:
        limit = _search_limit(ctx.config)
        text = str(getattr(event, "raw_text", "") or getattr(event, "text", "") or "")
        user_id, amount = _parse_target([str(item) for item in args], fallback_text=text)
        chat_id = getattr(event, "chat_id", None)
        message_id = getattr(event, "id", None)

        if user_id is None or amount is None:
            await ctx.messages.send(
                chat_id=chat_id,
                reply_to_message_id=message_id,
                text=_usage_text(self._command, limit),
            )
            return

        identity = await resolve_public_sender_identity(
            ctx,
            chat_id=int(chat_id),
            user_id=user_id,
        )

        await ctx.messages.payout(
            chat_id=chat_id,
            amount=amount,
            text=f"+{amount}",
            reply_to_user_id=user_id,
            reply_to_display_name=identity.display_name,
            reply_to_search_limit=limit,
        )


__all__ = ["ReplyAnchorTestPlugin"]
