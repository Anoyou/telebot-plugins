"""TelePilot 原生红包模块入口。

红包的玩法逻辑仍集中在 legacy_main.py，本文件只负责把 TelePilot
Plugin API 的事件、客户端和配置适配到业务层。
"""

from __future__ import annotations

import logging
import io
import sys
import types
from pathlib import Path
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
try:
    from app.worker.plugins.events import event_from_interaction_payload
except ImportError:  # pragma: no cover - isolated unit-test stubs
    event_from_interaction_payload = None  # type: ignore[assignment]


DEFAULT_COMMAND = "redpack"
PLUGIN_KEY = "redpack-byRBQ"


async def _message_op(messages: Any, name: str, **kwargs: Any) -> dict[str, Any]:
    method = getattr(messages, name, None)
    if callable(method):
        return await method(**kwargs)
    apply = getattr(messages, "apply", None)
    if not callable(apply):
        raise RuntimeError(f"TelePilot MessageOps 不支持 {name}")
    from app.worker.plugins.message_ops import BufferedMessageOps

    buffered = BufferedMessageOps()
    action = await getattr(buffered, name)(**kwargs)
    await apply([action])
    return action


def _install_legacy_import_stubs() -> None:
    """让历史业务层可被导入，但不再通过 Pagermaid 注册监听器。"""
    pagermaid = types.ModuleType("pagermaid")
    listener_mod = types.ModuleType("pagermaid.listener")
    hook_mod = types.ModuleType("pagermaid.hook")
    enums_mod = types.ModuleType("pagermaid.enums")
    utils_mod = types.ModuleType("pagermaid.utils")

    def listener(**_kwargs):
        def deco(func):
            return func

        return deco

    class Hook:
        @staticmethod
        def on_startup():
            def deco(func):
                return func

            return deco

        @staticmethod
        def on_shutdown():
            def deco(func):
                return func

            return deco

    class _Logs:
        def __init__(self) -> None:
            self._logger = logging.getLogger(f"plugin.{PLUGIN_KEY}")

        def info(self, msg: str) -> None:
            self._logger.info(msg)

        def warning(self, msg: str) -> None:
            self._logger.warning(msg)

        def error(self, msg: str) -> None:
            self._logger.error(msg)

        def debug(self, msg: str) -> None:
            self._logger.debug(msg)

    listener_mod.listener = listener
    hook_mod.Hook = Hook
    enums_mod.Client = object
    enums_mod.Message = object
    enums_mod.bot = None
    utils_mod.logs = _Logs()

    pagermaid.listener = listener_mod
    pagermaid.hook = hook_mod
    pagermaid.enums = enums_mod
    pagermaid.utils = utils_mod

    sys.modules["pagermaid"] = pagermaid
    sys.modules["pagermaid.listener"] = listener_mod
    sys.modules["pagermaid.hook"] = hook_mod
    sys.modules["pagermaid.enums"] = enums_mod
    sys.modules["pagermaid.utils"] = utils_mod


_install_legacy_import_stubs()
from . import legacy_main as redpack_core  # noqa: E402


def _event_message(event: Any) -> Any:
    return getattr(event, "message", event)


def _current_command_prefix() -> str:
    try:
        from app.worker.command import current_command_prefix  # type: ignore

        return str(current_command_prefix(fallback=",") or ",")
    except Exception:
        return ","


def _get_client_method(client: Any, name: str) -> Any:
    try:
        method = getattr(client, name)
    except (AttributeError, PermissionError):
        return None
    return method if callable(method) else None


def _telegram_file(photo: Any) -> Any:
    if isinstance(photo, (str, Path)):
        path = Path(photo)
        if path.exists() and path.is_file():
            file_obj = io.BytesIO(path.read_bytes())
            file_obj.name = path.name or "redpack.png"
            return file_obj
    return photo


def _chat_id(event: Any) -> int:
    value = getattr(event, "chat_id", None)
    if value is None:
        value = getattr(_event_message(event), "chat_id", None)
    value = getattr(value, "channel_id", value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _event_sender_id(event: Any) -> int | None:
    for target in (event, _event_message(event)):
        sender_id = getattr(target, "sender_id", None)
        if sender_id is None:
            sender = getattr(target, "sender", None) or getattr(target, "from_user", None)
            sender_id = getattr(sender, "id", None) if sender is not None else None
        if sender_id is None:
            from_id = getattr(target, "from_id", None)
            sender_id = (
                getattr(from_id, "user_id", None)
                or getattr(from_id, "channel_id", None)
                or getattr(from_id, "chat_id", None)
                or getattr(from_id, "id", None)
            )
        if sender_id is None:
            continue
        try:
            return int(sender_id)
        except (TypeError, ValueError):
            continue
    return None


def _is_outgoing_event(event: Any) -> bool:
    for target in (event, _event_message(event)):
        for attr in ("outgoing", "out", "is_outgoing"):
            value = getattr(target, attr, None)
            if callable(value):
                value = value()
            if value is not None and bool(value):
                return True
    return False


async def _is_account_command_event(client: Any, event: Any) -> bool:
    if _is_outgoing_event(event):
        return True

    sender_id = _event_sender_id(event)
    get_me = getattr(client, "get_me", None) if client is not None else None
    if sender_id is None or not callable(get_me):
        return False

    try:
        me = await get_me()
        me_id = int(getattr(me, "id", 0) or 0)
    except Exception:
        return False
    return sender_id == me_id


async def _resolve_sender(event: Any) -> Any:
    for target in (event, _event_message(event)):
        sender = getattr(target, "sender", None) or getattr(target, "from_user", None)
        if sender is not None:
            return sender

    getter = getattr(event, "get_sender", None)
    if callable(getter):
        try:
            sender = await getter()
            if sender is not None:
                return sender
        except Exception:
            pass

    sender_id = _event_sender_id(event)
    if sender_id is not None:
        return types.SimpleNamespace(id=sender_id, first_name="", last_name="", username="", is_bot=False)
    return None


class _NativeClientAdapter:
    def __init__(self, ctx: PluginContext, raw_client: Any) -> None:
        self._ctx = ctx
        self._client = raw_client
        if getattr(ctx, "messages", None) is None:
            raise RuntimeError("TelePilot MessageOps 不可用，拒绝绕过平台处理红包消息")

    async def get_me(self) -> Any:
        return await self._client.get_me()

    @staticmethod
    def _normalize_send_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(kwargs)
        reply_to = normalized.pop("reply_to_message_id", None)
        if reply_to is not None and "reply_to" not in normalized:
            normalized["reply_to"] = reply_to
        return normalized

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Any:
        normalized = self._normalize_send_kwargs(kwargs)
        return await self._ctx.messages.send(
            chat_id=int(chat_id),
            text=text,
            parse_mode="html" if str(normalized.get("parse_mode") or "").lower() == "html" else "plain",
            reply_to_message_id=normalized.get("reply_to"),
        )

    async def payout(self, chat_id: int, amount: int, **kwargs: Any) -> Any:
        normalized = self._normalize_send_kwargs(kwargs)
        return await self._ctx.messages.payout(
            chat_id=int(chat_id),
            amount=int(amount),
            text=normalized.get("text"),
            parse_mode="html" if str(normalized.get("parse_mode") or "").lower() == "html" else "plain",
            reply_to_message_id=normalized.get("reply_to"),
            reply_to_user_id=normalized.get("reply_to_user_id"),
        )

    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any) -> Any:
        kwargs = self._normalize_send_kwargs(kwargs)
        source = _telegram_file(photo)
        if isinstance(source, io.BytesIO):
            payload = source.getvalue()
            filename = getattr(source, "name", "redpack.png")
        elif isinstance(source, (bytes, bytearray, memoryview)):
            payload = bytes(source)
            filename = "redpack.png"
        else:
            raise TypeError("红包图片必须是本地文件或字节数据")
        return await _message_op(
            self._ctx.messages,
            "send_photo",
            chat_id=int(chat_id),
            photo=payload,
            filename=filename,
            caption=kwargs.get("caption"),
            parse_mode="html" if str(kwargs.get("parse_mode") or "").lower() == "html" else "plain",
            reply_to_message_id=kwargs.get("reply_to"),
        )

    async def edit_message_caption(self, chat_id: int, message_id: int, caption: str, **kwargs: Any) -> Any:
        return await _message_op(
            self._ctx.messages,
            "edit_caption",
            chat_id=int(chat_id), message_id=int(message_id), caption=caption,
            parse_mode="html" if str(kwargs.get("parse_mode") or "").lower() == "html" else "plain",
        )

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs: Any) -> Any:
        return await self._ctx.messages.edit(
            chat_id=int(chat_id), message_id=int(message_id), text=text,
            parse_mode="html" if str(kwargs.get("parse_mode") or "").lower() == "html" else "plain",
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _NativeMessageAdapter:
    def __init__(self, ctx: PluginContext, event: Any, args: list[str] | None = None, sender: Any = None) -> None:
        self._ctx = ctx
        self._event = event
        self._message = _event_message(event)
        self._sender = sender
        self.arguments = " ".join(args or []).strip()

    def _remember_result_message(self, result: Any) -> Any:
        if result is not None and getattr(result, "id", None) is not None:
            self._message = result
        return result

    async def _respond(self, text: str, **kwargs: Any) -> Any:
        result = await self._ctx.messages.send(
            chat_id=_chat_id(self._event),
            text=text,
            parse_mode="html" if str(kwargs.get("parse_mode") or "").lower() == "html" else "plain",
            reply_to_message_id=getattr(self._event, "id", None),
        )
        return self._remember_result_message(result)

    @property
    def id(self) -> Any:
        return getattr(self._message, "id", getattr(self._event, "id", None))

    @property
    def text(self) -> str:
        return str(
            getattr(self._event, "raw_text", None)
            or getattr(self._message, "raw_text", None)
            or getattr(self._message, "text", None)
            or ""
        )

    @property
    def caption(self) -> str:
        return str(getattr(self._message, "caption", "") or "")

    @property
    def chat(self) -> Any:
        chat = getattr(self._event, "chat", None) or getattr(self._message, "chat", None)
        chat_id = _chat_id(self._event)
        if chat_id:
            return types.SimpleNamespace(
                id=chat_id,
                title=getattr(chat, "title", "") if chat is not None else "",
                first_name=getattr(chat, "first_name", "") if chat is not None else "",
            )
        if chat is not None:
            return chat
        return types.SimpleNamespace(id=0, title="", first_name="")

    @property
    def from_user(self) -> Any:
        sender_id = _event_sender_id(self._event)
        return (
            self._sender
            or getattr(self._event, "from_user", None)
            or getattr(self._message, "from_user", None)
            or getattr(self._event, "sender", None)
            or getattr(self._message, "sender", None)
            or (
                types.SimpleNamespace(
                    id=sender_id,
                    first_name="",
                    last_name="",
                    username="",
                    is_bot=False,
                )
                if sender_id is not None
                else None
            )
        )

    @property
    def reply_to_message(self) -> Any:
        return getattr(self._message, "reply_to_message", getattr(self._event, "reply_to_message", None))

    @property
    def reply_markup(self) -> Any:
        return getattr(self._message, "reply_markup", getattr(self._event, "reply_markup", None))

    async def edit(self, text: str, **kwargs: Any) -> Any:
        if not _is_outgoing_event(self._event):
            return await self._respond(text, **kwargs)
        message_id = self.id
        if message_id is None:
            return await self._respond(text, **kwargs)
        result = await self._ctx.messages.edit(
            chat_id=_chat_id(self._event), message_id=int(message_id), text=text,
            parse_mode="html" if str(kwargs.get("parse_mode") or "").lower() == "html" else "plain",
        )
        return self._remember_result_message(result)

    async def delete(self) -> Any:
        if self.id is None:
            return None
        return await self._ctx.messages.delete(chat_id=_chat_id(self._event), message_id=int(self.id))

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._respond(text, **kwargs)

    async def click(self, row: int, col: int) -> Any:
        raise RuntimeError("TelePilot 0.97.0 未提供该资金确认按钮的受控动作，已拒绝直连点击")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)


@register
class RedpackByRBQPlugin(Plugin):
    key = PLUGIN_KEY
    display_name = "红包"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self._config_path: Path | None = None
        self._data_dir: Path | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = str(cfg.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND
        if ctx.data_dir is None:
            raise RuntimeError("TelePilot 未提供 ctx.data_dir，无法保存 redpack 配置")
        self._data_dir = Path(ctx.data_dir)
        self._bind_core_config(ctx.account_id)
        self._apply_core_settings(ctx)
        self.commands = {self._command: self._cmd_redpack}
        if ctx.log:
            await ctx.log("info", f"[redpack-byRBQ] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.log:
            await ctx.log("info", "[redpack-byRBQ] 已停止")

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        event = event_from_interaction_payload(payload) if event_from_interaction_payload is not None else None
        trigger = event.trigger if event is not None else (payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {})
        return await self.on_interaction(
            ctx,
            str(trigger.get("entry_key") or "start_redpack"),
            payload,
        )

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_redpack":
            return None
        total_amount = int(payload.get("total_amount") or 88888)
        count = int(payload.get("count") or 10)
        return [{"type": "send_message", "text": f"🧧 口令红包入口已触发，总额 {total_amount}，个数 {count}。发送 ,{self._command} 口令 {total_amount} {count} 开始。"}]

    def _bind_core_config(self, account_id: int) -> None:
        if self._data_dir is None:
            raise RuntimeError("TelePilot 未提供 ctx.data_dir，无法保存 redpack 配置")
        config_path = self._data_dir / str(int(account_id)) / "redpack_config.json"
        if self._config_path == config_path:
            return
        self._config_path = config_path
        redpack_core.configure_data_dir(self._data_dir, account_id)

    def _apply_core_settings(self, ctx: PluginContext) -> None:
        cfg = dict(ctx.config or {})
        cfg["command"] = self._command
        cfg["command_prefix"] = _current_command_prefix()
        redpack_core.apply_runtime_settings(cfg)

    async def _cmd_redpack(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        command_client = client or ctx.client
        if not await _is_account_command_event(command_client, event):
            if ctx.log:
                await ctx.log(
                    "info",
                    "[redpack-byRBQ] 已忽略非账号本人发出的红包命令",
                    chat_id=getattr(event, "chat_id", None),
                    sender_id=_event_sender_id(event),
                    event_outgoing=getattr(event, "outgoing", None),
                    message_out=getattr(_event_message(event), "out", None),
                )
            return
        self._bind_core_config(account_id)
        self._apply_core_settings(ctx)
        message = _NativeMessageAdapter(ctx, event, args)
        bot = _NativeClientAdapter(ctx, command_client)
        await redpack_core.redpack_command(message, bot)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        if ctx.client is None:
            return
        self._bind_core_config(ctx.account_id)
        self._apply_core_settings(ctx)
        message = _NativeMessageAdapter(ctx, event, sender=await _resolve_sender(event))
        bot = _NativeClientAdapter(ctx, ctx.client)
        await redpack_core.redpack_claim_listener(message, bot)
