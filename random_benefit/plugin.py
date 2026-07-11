"""随机福利插件。

主路径使用 TelePilot Event Bus：命令事件控制当前群组开关，普通消息事件按概率返回
标准 send_message action，由平台 MessageOps 负责投递。
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register


PLUGIN_KEY = "random_benefit"
PLUGIN_VERSION = "1.0.0"

DEFAULT_COMMAND = "随机福利"
DEFAULT_REPLY_TEMPLATE = "+1-6666"
DEFAULT_PROBABILITY = 0.05

COMMAND_ON = {"on", "start", "enable", "开启", "启动", "恢复"}
COMMAND_OFF = {"off", "stop", "disable", "pause", "暂停", "关闭", "停止"}
COMMAND_STATUS = {"status", "state", "状态", "查看"}
COMMAND_HELP = {"help", "帮助", "?"}
COMMAND_PREFIXES = (",", "/", "!", "！", "，")


class _SafeVars(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _int_set(value: Any) -> set[int]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    result: set[int] = set()
    for item in value:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _payload_event_type(payload: Mapping[str, Any]) -> str:
    for value in (
        payload.get("type"),
        _nested(payload, "event", "type"),
        _nested(payload, "tp_event", "type"),
        _nested(payload, "source", "type"),
    ):
        if value:
            return str(value)
    return ""


def _payload_message(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return _as_mapping(payload.get("message") or _nested(payload, "event", "message"))


def _payload_text(payload: Mapping[str, Any]) -> str:
    message = _payload_message(payload)
    value = (
        message.get("text")
        or message.get("raw_text")
        or payload.get("text")
        or payload.get("raw_text")
        or _nested(payload, "trigger", "text")
    )
    return str(value or "")


def _payload_chat_id(payload: Mapping[str, Any]) -> int | None:
    message = _payload_message(payload)
    chat = _as_mapping(payload.get("chat") or _nested(payload, "event", "chat"))
    for value in (
        message.get("chat_id"),
        chat.get("id"),
        payload.get("chat_id"),
        _nested(payload, "trigger", "chat_id"),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _payload_message_id(payload: Mapping[str, Any]) -> int | None:
    message = _payload_message(payload)
    for value in (message.get("message_id"), message.get("id"), payload.get("message_id")):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _payload_sender(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return _as_mapping(
        payload.get("sender")
        or payload.get("actor")
        or payload.get("source_actor")
        or _nested(payload, "event", "sender")
        or _nested(payload, "event", "actor")
    )


def _payload_sender_id(payload: Mapping[str, Any]) -> int | None:
    sender = _payload_sender(payload)
    for value in (
        sender.get("user_id"),
        sender.get("id"),
        payload.get("sender_id"),
        payload.get("user_id"),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _payload_sender_name(payload: Mapping[str, Any], sender_id: int | None) -> str:
    sender = _payload_sender(payload)
    for key in ("display_name", "name", "first_name", "username"):
        value = sender.get(key)
        if value:
            return str(value)
    return str(sender_id) if sender_id is not None else "群友"


def _payload_is_outgoing(payload: Mapping[str, Any]) -> bool:
    message = _payload_message(payload)
    return bool(message.get("out") or message.get("outgoing") or payload.get("outgoing"))


def _payload_actor_is_bot(payload: Mapping[str, Any]) -> bool:
    sender = _payload_sender(payload)
    return bool(sender.get("is_bot") or sender.get("bot"))


def _command_name_from_text(text: str) -> str:
    token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return token.lstrip("".join(COMMAND_PREFIXES))


def _command_args(payload: Mapping[str, Any], command: str) -> list[str] | None:
    trigger = _as_mapping(payload.get("trigger"))
    trigger_command = str(trigger.get("command") or "").lstrip("".join(COMMAND_PREFIXES))
    args = trigger.get("args")
    if trigger_command == command:
        if isinstance(args, str):
            return args.split()
        if isinstance(args, list):
            return [str(item) for item in args]

    text = _payload_text(payload).strip()
    if not text:
        return None
    parts = text.split()
    if _command_name_from_text(text) != command:
        return None
    return parts[1:]


def _send_action(
    text: str,
    *,
    chat_id: int | None = None,
    reply_to_message_id: int | None = None,
    parse_mode: str = "plain",
) -> dict[str, Any]:
    action: dict[str, Any] = {"type": "send_message", "text": text, "parse_mode": parse_mode}
    if chat_id is not None:
        action["chat_id"] = int(chat_id)
    if reply_to_message_id:
        action["reply_to_message_id"] = int(reply_to_message_id)
    return action


@register
class RandomBenefitPlugin(Plugin):
    """监听指定群组发言，随机引用回复福利语。"""

    key = PLUGIN_KEY
    display_name = "随机福利"
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self._allowed_chat_ids: set[int] = set()
        self._reply_template = DEFAULT_REPLY_TEMPLATE
        self._probability = DEFAULT_PROBABILITY
        self._default_enabled = True
        self._active_fallback: dict[tuple[int, int], bool] = {}
        self.commands = {self._command: self._legacy_command}

    async def on_startup(self, ctx: PluginContext) -> None:
        self._reload_config(ctx)
        if ctx.log:
            group_text = f"{len(self._allowed_chat_ids)} 个群" if self._allowed_chat_ids else "未选择群组"
            await ctx.log(
                "info",
                f"[random_benefit] v{PLUGIN_VERSION} 已启动，指令：{self._command}，目标：{group_text}",
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._active_fallback.clear()

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        self._reload_config(ctx)
        event_type = _payload_event_type(payload)
        if event_type == "command":
            return await self._handle_command(ctx, payload)
        if event_type != "message":
            return []
        return await self._handle_message(ctx, payload)

    async def _handle_command(self, ctx: PluginContext, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        chat_id = _payload_chat_id(payload)
        message_id = _payload_message_id(payload)
        if chat_id is None:
            return []

        args = _command_args(payload, self._command)
        if args is None:
            return []
        action = args[0].casefold() if args else "status"

        if action in COMMAND_HELP:
            return [_send_action(self._help_text(), chat_id=chat_id, reply_to_message_id=message_id)]
        if action in COMMAND_ON:
            if not self._chat_configured(chat_id):
                return [_send_action(self._not_configured_text(), chat_id=chat_id, reply_to_message_id=message_id)]
            await self._set_active(ctx, chat_id, True)
            return [_send_action("随机福利已开启。", chat_id=chat_id, reply_to_message_id=message_id)]
        if action in COMMAND_OFF:
            if not self._chat_configured(chat_id):
                return [_send_action(self._not_configured_text(), chat_id=chat_id, reply_to_message_id=message_id)]
            await self._set_active(ctx, chat_id, False)
            return [_send_action("随机福利已暂停。", chat_id=chat_id, reply_to_message_id=message_id)]
        if action in COMMAND_STATUS:
            return [_send_action(await self._status_text(ctx, chat_id), chat_id=chat_id, reply_to_message_id=message_id)]
        return [_send_action(self._help_text(), chat_id=chat_id, reply_to_message_id=message_id)]

    async def _handle_message(self, ctx: PluginContext, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        chat_id = _payload_chat_id(payload)
        if chat_id is None or not self._chat_configured(chat_id):
            return []
        if not await self._is_active(ctx, chat_id):
            return []
        if self._probability <= 0:
            return []
        if _payload_is_outgoing(payload) or _payload_actor_is_bot(payload):
            return []

        text = _payload_text(payload)
        if not text.strip() or text.lstrip().startswith(COMMAND_PREFIXES):
            return []
        if random.random() >= self._probability:
            return []

        sender_id = _payload_sender_id(payload)
        reply_text = self._render_reply(
            sender=_payload_sender_name(payload, sender_id),
            sender_id=sender_id,
            chat_id=chat_id,
            message=text,
        )
        if not reply_text.strip():
            return []
        return [
            _send_action(
                reply_text,
                chat_id=chat_id,
                reply_to_message_id=_payload_message_id(payload),
            )
        ]

    def _reload_config(self, ctx: PluginContext) -> None:
        cfg = getattr(ctx, "config", None) or {}
        command = str(cfg.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND
        self._command = command
        self.commands = {self._command: self._legacy_command}
        self._allowed_chat_ids = _int_set(cfg.get("allowed_chat_ids"))
        self._reply_template = str(cfg.get("reply_template") or DEFAULT_REPLY_TEMPLATE)
        try:
            self._probability = max(0.0, min(1.0, float(cfg.get("trigger_probability", DEFAULT_PROBABILITY))))
        except (TypeError, ValueError):
            self._probability = DEFAULT_PROBABILITY
        self._default_enabled = bool(cfg.get("default_enabled", True))

    def _chat_configured(self, chat_id: int) -> bool:
        return int(chat_id) in self._allowed_chat_ids

    def _state_key(self, ctx: PluginContext, chat_id: int) -> str:
        account_id = int(getattr(ctx, "account_id", 0) or 0)
        return f"random_benefit:active:{account_id}:{int(chat_id)}"

    async def _is_active(self, ctx: PluginContext, chat_id: int) -> bool:
        if not self._chat_configured(chat_id):
            return False
        redis = getattr(ctx, "redis", None)
        if redis is not None:
            value = await redis.get(self._state_key(ctx, chat_id))
            if value is not None:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                return str(value) == "1"
        account_id = int(getattr(ctx, "account_id", 0) or 0)
        return self._active_fallback.get((account_id, int(chat_id)), self._default_enabled)

    async def _set_active(self, ctx: PluginContext, chat_id: int, active: bool) -> None:
        account_id = int(getattr(ctx, "account_id", 0) or 0)
        self._active_fallback[(account_id, int(chat_id))] = active
        redis = getattr(ctx, "redis", None)
        if redis is not None:
            await redis.set(self._state_key(ctx, chat_id), "1" if active else "0")

    async def _status_text(self, ctx: PluginContext, chat_id: int) -> str:
        if not self._chat_configured(chat_id):
            return self._not_configured_text()
        active = await self._is_active(ctx, chat_id)
        status = "开启" if active else "暂停"
        return f"当前群组随机福利：{status}；随机回复概率：{self._probability:g}。"

    def _help_text(self) -> str:
        prefix = current_command_prefix(fallback=",")
        return "\n".join(
            [
                "随机福利指令：",
                f"{prefix}{self._command} on - 开启当前群组",
                f"{prefix}{self._command} off - 暂停当前群组",
                f"{prefix}{self._command} status - 查看状态",
            ]
        )

    @staticmethod
    def _not_configured_text() -> str:
        return "当前群组未在随机福利配置页的目标群组中选择。"

    def _render_reply(self, *, sender: str, sender_id: int | None, chat_id: int, message: str) -> str:
        values = _SafeVars(
            sender=sender,
            user_id=str(sender_id or ""),
            chat_id=str(chat_id),
            message=message,
        )
        try:
            return self._reply_template.format_map(values)
        except Exception:
            return self._reply_template

    async def _legacy_command(self, ctx: PluginContext, event: Any) -> None:
        """兼容旧命令入口；新运行时优先使用 on_event。"""
        return None
