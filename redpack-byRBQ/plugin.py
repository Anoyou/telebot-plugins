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


DEFAULT_COMMAND = "redpack"
PLUGIN_KEY = "redpack-byRBQ"


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
    def __init__(self, raw_client: Any) -> None:
        self._client = raw_client

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
        return await self._client.send_message(chat_id, text, **self._normalize_send_kwargs(kwargs))

    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any) -> Any:
        kwargs = self._normalize_send_kwargs(kwargs)
        send_file = _get_client_method(self._client, "send_file")
        if send_file is not None:
            kwargs.setdefault("force_document", False)
            return await send_file(chat_id, _telegram_file(photo), **kwargs)
        send_photo = _get_client_method(self._client, "send_photo")
        if send_photo is not None:
            return await send_photo(chat_id, photo, **kwargs)
        raise PermissionError("当前客户端没有可用的 send_file/send_photo 能力")

    async def edit_message_caption(self, chat_id: int, message_id: int, caption: str, **kwargs: Any) -> Any:
        edit_caption = _get_client_method(self._client, "edit_message_caption")
        if edit_caption is not None:
            return await edit_caption(chat_id, message_id, caption, **kwargs)
        return await self._client.edit_message(chat_id, message_id, caption, **kwargs)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs: Any) -> Any:
        edit_text = _get_client_method(self._client, "edit_message_text")
        if edit_text is not None:
            return await edit_text(chat_id, message_id, text, **kwargs)
        return await self._client.edit_message(chat_id, message_id, text, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _NativeMessageAdapter:
    def __init__(self, event: Any, args: list[str] | None = None, sender: Any = None) -> None:
        self._event = event
        self._message = _event_message(event)
        self._sender = sender
        self.arguments = " ".join(args or []).strip()

    def _remember_result_message(self, result: Any) -> Any:
        if result is not None and getattr(result, "id", None) is not None:
            self._message = result
        return result

    async def _respond(self, text: str, **kwargs: Any) -> Any:
        for method_name in ("respond", "reply"):
            method = getattr(self._event, method_name, None)
            if callable(method):
                return self._remember_result_message(await method(text, **kwargs))
        return None

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
        edit = getattr(self._event, "edit", None)
        if callable(edit):
            return self._remember_result_message(await edit(text, **kwargs))
        return await self._respond(text, **kwargs)

    async def delete(self) -> Any:
        delete = getattr(self._event, "delete", None) or getattr(self._message, "delete", None)
        if callable(delete):
            return await delete()
        return None

    async def reply(self, text: str, **kwargs: Any) -> Any:
        reply = getattr(self._event, "reply", None)
        if callable(reply):
            return self._remember_result_message(await reply(text, **kwargs))
        return await self.edit(text, **kwargs)

    async def click(self, row: int, col: int) -> Any:
        click = getattr(self._event, "click", None) or getattr(self._message, "click", None)
        if callable(click):
            return await click(row, col)
        return None

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
        self._config_path = Path(__file__).with_name("redpack_config.json")

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = str(cfg.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND
        self._bind_core_config(ctx.account_id)
        self._apply_core_settings(ctx)
        self.commands = {self._command: self._cmd_redpack}
        if ctx.log:
            await ctx.log("info", f"[redpack-byRBQ] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.log:
            await ctx.log("info", "[redpack-byRBQ] 已停止")

    def _bind_core_config(self, account_id: int) -> None:
        config_path = Path(__file__).with_name(f"redpack_config_{account_id}.json")
        if self._config_path == config_path:
            return
        self._config_path = config_path
        redpack_core.config_file = config_path
        redpack_core.config = redpack_core.RedPackConfig()

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
        message = _NativeMessageAdapter(event, args)
        bot = _NativeClientAdapter(command_client)
        await redpack_core.redpack_command(message, bot)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        if ctx.client is None:
            return
        self._bind_core_config(ctx.account_id)
        self._apply_core_settings(ctx)
        message = _NativeMessageAdapter(event, sender=await _resolve_sender(event))
        bot = _NativeClientAdapter(ctx.client)
        await redpack_core.redpack_claim_listener(message, bot)
        if not _is_outgoing_event(event):
            await redpack_core.redpack_transfer_confirm_listener(message, bot)
