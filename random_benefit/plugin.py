"""随机福利插件。

默认主路径使用 TelePilot Event Bus：命令事件控制当前群组开关，普通消息事件按概率
返回标准 send_message action，由平台 MessageOps 负责投递。声明裸直通能力并在账号配置
二次开启后，incoming userbot 消息也可通过 on_direct_message 低延时处理。
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register


PLUGIN_KEY = "random_benefit"
PLUGIN_VERSION = "1.5.0"

DEFAULT_START_COMMAND = "福利开启"
DEFAULT_STOP_COMMAND = "福利暂停"
DEFAULT_STATUS_COMMAND = "福利状态"
DEFAULT_REPLY_TEMPLATE = "+1-6666"
DEFAULT_PROBABILITY = 0.05
DEFAULT_CHAT_COOLDOWN_SECONDS = 30
DEFAULT_USER_COOLDOWN_SECONDS = 120

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


def _bounded_int(value: Any, *, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _bounded_float(value: Any, *, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _config_command(cfg: Mapping[str, Any], key: str, default: str) -> str:
    value = str(cfg.get(key) or default).strip()
    return value or default


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


def _int_from_any(value: Any) -> int | None:
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        return None
    return None


def _payload_sender_id(payload: Mapping[str, Any]) -> int | None:
    sender = _payload_sender(payload)
    for value in (
        sender.get("user_id"),
        sender.get("id"),
        payload.get("sender_id"),
        payload.get("user_id"),
    ):
        parsed = _int_from_any(value)
        if parsed is not None:
            return parsed
    return None


def _int_ids_from_any(value: Any) -> set[int]:
    ids: set[int] = set()
    if isinstance(value, Mapping):
        for key in ("user_id", "id", "tg_user_id", "owner_user_id", "account_owner_user_id", "userbot_user_id"):
            parsed = _int_from_any(value.get(key))
            if parsed:
                ids.add(parsed)
        for key in ("owner_user_ids", "admin_user_ids", "userbot_user_ids"):
            ids.update(_int_ids_from_any(value.get(key)))
        return ids
    if isinstance(value, (list, tuple, set)):
        for item in value:
            ids.update(_int_ids_from_any(item))
        return ids
    parsed = _int_from_any(value)
    if parsed:
        ids.add(parsed)
    return ids


def _payload_account_user_ids(payload: Mapping[str, Any]) -> set[int]:
    ids: set[int] = set()
    for key in ("owner_user_ids", "admin_user_ids", "userbot_user_ids"):
        ids.update(_int_ids_from_any(payload.get(key)))
    for key in ("userbot_user_id", "owner_user_id", "account_owner_user_id", "tg_user_id"):
        ids.update(_int_ids_from_any(payload.get(key)))
    for envelope_key in ("account", "source"):
        envelope = payload.get(envelope_key)
        if isinstance(envelope, Mapping):
            for key in ("owner_user_ids", "admin_user_ids", "userbot_user_ids"):
                ids.update(_int_ids_from_any(envelope.get(key)))
            for key in ("userbot_user_id", "owner_user_id", "account_owner_user_id", "tg_user_id"):
                ids.update(_int_ids_from_any(envelope.get(key)))
    return ids


def _payload_sender_name(payload: Mapping[str, Any], sender_id: int | None) -> str:
    sender = _payload_sender(payload)
    for key in ("display_name", "name", "first_name", "username"):
        value = sender.get(key)
        if value:
            return str(value)
    return str(sender_id) if sender_id is not None else "群友"


def _payload_is_outgoing(payload: Mapping[str, Any]) -> bool:
    message = _payload_message(payload)
    source = _as_mapping(payload.get("source"))
    return bool(
        message.get("out")
        or message.get("outgoing")
        or message.get("is_outgoing")
        or payload.get("out")
        or payload.get("outgoing")
        or payload.get("is_outgoing")
        or source.get("outgoing")
        or source.get("is_outgoing")
    )


def _payload_actor_is_bot(payload: Mapping[str, Any]) -> bool:
    for key in ("sender", "actor", "source_actor", "player"):
        actor = _as_mapping(payload.get(key))
        actor_type = str(actor.get("type") or actor.get("kind") or "").casefold()
        if bool(actor.get("is_bot") or actor.get("bot")):
            return True
        if actor_type in {"bot", "external_bot", "interaction_bot", "account_bot"}:
            return True
    return False


def _payload_is_self_actor(payload: Mapping[str, Any], me_id: int | None) -> bool:
    sender_id = _payload_sender_id(payload)
    if sender_id is None:
        return False
    if me_id is not None and sender_id == me_id:
        return True
    return sender_id in _payload_account_user_ids(payload)


def _command_name_from_text(text: str) -> str:
    token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return token.lstrip("".join(COMMAND_PREFIXES))


def _payload_command_name(payload: Mapping[str, Any]) -> str:
    trigger = _as_mapping(payload.get("trigger"))
    trigger_command = str(trigger.get("command") or "").lstrip("".join(COMMAND_PREFIXES))
    if trigger_command:
        return trigger_command

    text = _payload_text(payload).strip()
    if not text:
        return ""
    return _command_name_from_text(text)


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


def _event_text(event: Any) -> str:
    return str(
        getattr(event, "raw_text", None)
        or getattr(event, "text", None)
        or getattr(getattr(event, "message", None), "message", None)
        or ""
    )


def _event_chat_id(event: Any) -> int | None:
    for value in (getattr(event, "chat_id", None), getattr(getattr(event, "message", None), "chat_id", None)):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _event_command_name(event: Any) -> str:
    return _command_name_from_text(_event_text(event))


def _event_message_id(event: Any) -> int | None:
    for value in (
        getattr(event, "id", None),
        getattr(event, "message_id", None),
        getattr(getattr(event, "message", None), "id", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _event_sender_id(event: Any) -> int | None:
    for value in (
        getattr(event, "sender_id", None),
        getattr(getattr(event, "message", None), "sender_id", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _event_is_outgoing(event: Any) -> bool:
    return bool(
        getattr(event, "out", False)
        or getattr(event, "outgoing", False)
        or getattr(event, "is_outgoing", False)
        or getattr(getattr(event, "message", None), "out", False)
        or getattr(getattr(event, "message", None), "outgoing", False)
    )


async def _event_sender_name(event: Any, sender_id: int | None) -> str:
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    for attr in ("first_name", "username", "title"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)
    return str(sender_id) if sender_id is not None else "群友"


async def _event_actor_is_bot(event: Any) -> bool:
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    return bool(getattr(sender, "bot", False) or getattr(sender, "is_bot", False))


@register
class RandomBenefitPlugin(Plugin):
    """监听指定群组发言，随机引用回复福利语。"""

    key = PLUGIN_KEY
    display_name = "随机福利"
    owner_only = False
    command_config_keys = {"start_command", "stop_command", "status_command"}

    def __init__(self) -> None:
        super().__init__()
        self._start_command = DEFAULT_START_COMMAND
        self._stop_command = DEFAULT_STOP_COMMAND
        self._status_command = DEFAULT_STATUS_COMMAND
        self._allowed_chat_ids: set[int] = set()
        self._reply_template = DEFAULT_REPLY_TEMPLATE
        self._probability = DEFAULT_PROBABILITY
        self._chat_cooldown_seconds = DEFAULT_CHAT_COOLDOWN_SECONDS
        self._user_cooldown_seconds = DEFAULT_USER_COOLDOWN_SECONDS
        self._default_enabled = True
        self._me_id: int | None = None
        self._active_fallback: dict[tuple[int, int], bool] = {}
        self._cooldown_fallback: dict[str, float] = {}
        self.commands = {
            self._start_command: self._legacy_command,
            self._stop_command: self._legacy_command,
            self._status_command: self._legacy_command,
        }

    async def on_startup(self, ctx: PluginContext) -> None:
        self._reload_config(ctx)
        await self._load_me(ctx)
        if ctx.log:
            group_text = f"{len(self._allowed_chat_ids)} 个群" if self._allowed_chat_ids else "未选择群组"
            await ctx.log(
                "info",
                f"[random_benefit] v{PLUGIN_VERSION} 已启动，开启指令：{self._start_command}，暂停指令：{self._stop_command}，目标：{group_text}",
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._me_id = None
        self._active_fallback.clear()
        self._cooldown_fallback.clear()

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        self._reload_config(ctx)
        event_type = _payload_event_type(payload)
        if event_type == "command":
            return await self._handle_command(ctx, payload)
        if event_type != "message":
            return []
        return await self._handle_message(ctx, payload)

    async def on_direct_message(self, ctx: PluginContext, event: Any) -> None:
        """裸直通入口：收到 live Telethon event 后直接回复，不返回标准 action。"""
        self._reload_config(ctx)
        await self._load_me(ctx)
        chat_id = _event_chat_id(event)
        if chat_id is None or not self._chat_configured(chat_id):
            return
        if not await self._is_active(ctx, chat_id):
            return
        if self._probability <= 0:
            return
        if _event_is_outgoing(event):
            return

        text = _event_text(event)
        if not text.strip() or text.lstrip().startswith(COMMAND_PREFIXES):
            return
        if await _event_actor_is_bot(event):
            return
        sender_id = _event_sender_id(event)
        if sender_id is not None and self._me_id is not None and sender_id == self._me_id:
            return
        if await self._in_reply_cooldown(ctx, chat_id, sender_id):
            return
        if random.random() >= self._probability:
            return

        reply_text = self._render_reply(
            sender=await _event_sender_name(event, sender_id),
            sender_id=sender_id,
            chat_id=chat_id,
            message=text,
        )
        if not reply_text.strip():
            return

        try:
            await event.reply(reply_text)
            await self._mark_reply_cooldown(ctx, chat_id, sender_id)
        except Exception as exc:  # noqa: BLE001
            if ctx.log:
                await ctx.log(
                    "warning",
                    "[random_benefit] 裸直通回复失败",
                    chat_id=chat_id,
                    message_id=_event_message_id(event),
                    error=str(exc),
                )

    async def _handle_command(self, ctx: PluginContext, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        chat_id = _payload_chat_id(payload)
        message_id = _payload_message_id(payload)
        if chat_id is None:
            return []

        command = _payload_command_name(payload)

        if command == self._start_command:
            if not self._chat_configured(chat_id):
                return [_send_action(self._not_configured_text(), chat_id=chat_id, reply_to_message_id=message_id)]
            await self._set_active(ctx, chat_id, True)
            return [_send_action("随机福利已开启。", chat_id=chat_id, reply_to_message_id=message_id)]
        if command == self._stop_command:
            if not self._chat_configured(chat_id):
                return [_send_action(self._not_configured_text(), chat_id=chat_id, reply_to_message_id=message_id)]
            await self._set_active(ctx, chat_id, False)
            return [_send_action("随机福利已暂停。", chat_id=chat_id, reply_to_message_id=message_id)]
        if command == self._status_command:
            return [_send_action(await self._status_text(ctx, chat_id), chat_id=chat_id, reply_to_message_id=message_id)]
        return []

    async def _handle_message(self, ctx: PluginContext, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        await self._load_me(ctx)
        chat_id = _payload_chat_id(payload)
        if chat_id is None or not self._chat_configured(chat_id):
            return []
        if not await self._is_active(ctx, chat_id):
            return []
        if self._probability <= 0:
            return []
        if (
            _payload_is_outgoing(payload)
            or _payload_actor_is_bot(payload)
            or _payload_is_self_actor(payload, self._me_id)
        ):
            return []

        text = _payload_text(payload)
        if not text.strip() or text.lstrip().startswith(COMMAND_PREFIXES):
            return []
        sender_id = _payload_sender_id(payload)
        if await self._in_reply_cooldown(ctx, chat_id, sender_id):
            return []
        if random.random() >= self._probability:
            return []

        reply_text = self._render_reply(
            sender=_payload_sender_name(payload, sender_id),
            sender_id=sender_id,
            chat_id=chat_id,
            message=text,
        )
        if not reply_text.strip():
            return []
        await self._mark_reply_cooldown(ctx, chat_id, sender_id)
        return [
            _send_action(
                reply_text,
                chat_id=chat_id,
                reply_to_message_id=_payload_message_id(payload),
            )
        ]

    def _reload_config(self, ctx: PluginContext) -> None:
        cfg = getattr(ctx, "config", None) or {}
        legacy_command = str(cfg.get("command") or "").strip()
        self._start_command = _config_command(cfg, "start_command", legacy_command or DEFAULT_START_COMMAND)
        self._stop_command = _config_command(cfg, "stop_command", DEFAULT_STOP_COMMAND)
        self._status_command = _config_command(cfg, "status_command", DEFAULT_STATUS_COMMAND)
        self.commands = {
            self._start_command: self._legacy_command,
            self._stop_command: self._legacy_command,
            self._status_command: self._legacy_command,
        }
        self._allowed_chat_ids = _int_set(cfg.get("allowed_chat_ids"))
        self._reply_template = str(cfg.get("reply_template") or DEFAULT_REPLY_TEMPLATE)
        self._probability = _bounded_float(
            cfg.get("trigger_probability", DEFAULT_PROBABILITY),
            minimum=0.0,
            maximum=1.0,
            default=DEFAULT_PROBABILITY,
        )
        self._chat_cooldown_seconds = _bounded_int(
            cfg.get("chat_cooldown_seconds", DEFAULT_CHAT_COOLDOWN_SECONDS),
            minimum=0,
            maximum=86400,
            default=DEFAULT_CHAT_COOLDOWN_SECONDS,
        )
        self._user_cooldown_seconds = _bounded_int(
            cfg.get("user_cooldown_seconds", DEFAULT_USER_COOLDOWN_SECONDS),
            minimum=0,
            maximum=86400,
            default=DEFAULT_USER_COOLDOWN_SECONDS,
        )
        self._default_enabled = bool(cfg.get("default_enabled", True))

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
        self._me_id = _int_from_any(getattr(me, "id", None))

    def _chat_configured(self, chat_id: int) -> bool:
        return int(chat_id) in self._allowed_chat_ids

    def _state_key(self, ctx: PluginContext, chat_id: int) -> str:
        account_id = int(getattr(ctx, "account_id", 0) or 0)
        return f"random_benefit:active:{account_id}:{int(chat_id)}"

    def _cooldown_key(self, ctx: PluginContext, scope: str, chat_id: int, sender_id: int | None = None) -> str:
        account_id = int(getattr(ctx, "account_id", 0) or 0)
        if scope == "user":
            return f"random_benefit:cooldown:user:{account_id}:{int(chat_id)}:{int(sender_id or 0)}"
        return f"random_benefit:cooldown:chat:{account_id}:{int(chat_id)}"

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

    async def _in_reply_cooldown(self, ctx: PluginContext, chat_id: int, sender_id: int | None) -> bool:
        keys: list[str] = []
        if self._chat_cooldown_seconds > 0:
            keys.append(self._cooldown_key(ctx, "chat", chat_id))
        if self._user_cooldown_seconds > 0 and sender_id is not None:
            keys.append(self._cooldown_key(ctx, "user", chat_id, sender_id))
        if not keys:
            return False

        redis = getattr(ctx, "redis", None)
        if redis is not None:
            for key in keys:
                if await redis.get(key) is not None:
                    return True
            return False

        now = time.time()
        expired = [key for key, until in self._cooldown_fallback.items() if until <= now]
        for key in expired:
            self._cooldown_fallback.pop(key, None)
        return any(key in self._cooldown_fallback for key in keys)

    async def _mark_reply_cooldown(self, ctx: PluginContext, chat_id: int, sender_id: int | None) -> None:
        entries: list[tuple[str, int]] = []
        if self._chat_cooldown_seconds > 0:
            entries.append((self._cooldown_key(ctx, "chat", chat_id), self._chat_cooldown_seconds))
        if self._user_cooldown_seconds > 0 and sender_id is not None:
            entries.append((self._cooldown_key(ctx, "user", chat_id, sender_id), self._user_cooldown_seconds))
        if not entries:
            return

        redis = getattr(ctx, "redis", None)
        if redis is not None:
            for key, ttl in entries:
                await redis.set(key, "1", ex=int(ttl))
            return

        now = time.time()
        for key, ttl in entries:
            self._cooldown_fallback[key] = now + float(ttl)

    async def _status_text(self, ctx: PluginContext, chat_id: int) -> str:
        if not self._chat_configured(chat_id):
            return self._not_configured_text()
        active = await self._is_active(ctx, chat_id)
        status = "开启" if active else "暂停"
        return (
            f"当前群组随机福利：{status}；随机回复概率：{self._probability:g}；"
            f"全群冷却：{self._chat_cooldown_seconds}s；同用户冷却：{self._user_cooldown_seconds}s。"
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

    async def _legacy_command(self, ctx: PluginContext, event: Any, *args: Any, **kwargs: Any) -> None:
        """兼容旧命令入口；新运行时优先使用 on_event。"""
        self._reload_config(ctx)
        chat_id = _event_chat_id(event)
        if chat_id is None:
            return None

        command = _event_command_name(event)
        message: str | None = None
        if command == self._start_command:
            if not self._chat_configured(chat_id):
                message = self._not_configured_text()
            else:
                await self._set_active(ctx, chat_id, True)
                message = "随机福利已开启。"
        elif command == self._stop_command:
            if not self._chat_configured(chat_id):
                message = self._not_configured_text()
            else:
                await self._set_active(ctx, chat_id, False)
                message = "随机福利已暂停。"
        elif command == self._status_command:
            message = await self._status_text(ctx, chat_id)

        if message and hasattr(event, "reply"):
            await event.reply(message)
        return None
