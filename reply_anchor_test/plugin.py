"""近期发言回复测试插件。"""

from __future__ import annotations

import re
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.plugins.events import event_from_interaction_payload

PLUGIN_KEY = "reply_anchor_test"
ENTRY_KEY = "reply_to_recent_message"
DEFAULT_COMMAND = "send"
DEFAULT_SEARCH_LIMIT = 200
MAX_SEARCH_LIMIT = 500


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


def _usage_text(command: str, limit: int) -> str:
    return (
        f"用法：{command} 用户ID 金额\n"
        f"示例：{command} 123456789 88\n"
        "如果账号启用了系统命令前缀，请在命令前加上当前系统前缀。\n"
        f"平台会在当前群最多向前搜索 {limit} 条消息，找到该用户最近一次发言后回复 +金额。"
    )


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
        if entry_key != ENTRY_KEY:
            return None
        event = event_from_interaction_payload(payload)
        if event.type != "command":
            return []

        limit = _search_limit(ctx.config)
        user_id, amount = _parse_target(_args_from_payload(payload), fallback_text=event.message.text or "")
        if user_id is None or amount is None:
            return [
                {
                    "type": "send_message",
                    "chat_id": event.message.chat_id,
                    "reply_to_message_id": event.message.message_id,
                    "text": _usage_text(DEFAULT_COMMAND, limit),
                }
            ]

        return [
            {
                "type": "payout",
                "chat_id": event.message.chat_id,
                "amount": amount,
                "text": f"+{amount}",
                "parse_mode": "plain",
                "reply_to_user_id": user_id,
                "reply_to_search_limit": limit,
            },
            {
                "type": "result",
                "success": True,
                "result": {
                    "target_user_id": user_id,
                    "amount": amount,
                    "reply_to_search_limit": limit,
                },
            },
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

        await ctx.messages.payout(
            chat_id=chat_id,
            amount=amount,
            text=f"+{amount}",
            reply_to_user_id=user_id,
            reply_to_search_limit=limit,
        )


__all__ = ["ReplyAnchorTestPlugin"]
