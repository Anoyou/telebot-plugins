"""TelePilot 原生红包模块入口。

红包的玩法逻辑仍集中在 legacy_main.py，本文件只负责把 TelePilot
Plugin API 的事件、客户端和配置适配到业务层。
"""

from __future__ import annotations

import logging
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


def _chat_id(event: Any) -> int:
    value = getattr(event, "chat_id", None)
    if value is None:
        value = getattr(_event_message(event), "chat_id", None)
    value = getattr(value, "channel_id", value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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

    sender_id = getattr(event, "sender_id", None) or getattr(_event_message(event), "sender_id", None)
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
        if hasattr(self._client, "send_photo"):
            return await self._client.send_photo(chat_id, photo, **kwargs)
        return await self._client.send_file(chat_id, photo, **kwargs)

    async def edit_message_caption(self, chat_id: int, message_id: int, caption: str, **kwargs: Any) -> Any:
        if hasattr(self._client, "edit_message_caption"):
            return await self._client.edit_message_caption(chat_id, message_id, caption, **kwargs)
        return await self._client.edit_message(chat_id, message_id, caption, **kwargs)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs: Any) -> Any:
        if hasattr(self._client, "edit_message_text"):
            return await self._client.edit_message_text(chat_id, message_id, text, **kwargs)
        return await self._client.edit_message(chat_id, message_id, text, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _NativeMessageAdapter:
    def __init__(self, event: Any, args: list[str] | None = None, sender: Any = None) -> None:
        self._event = event
        self._message = _event_message(event)
        self._sender = sender
        self.arguments = " ".join(args or []).strip()

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
        if chat is not None:
            return chat
        return types.SimpleNamespace(id=_chat_id(self._event), title="", first_name="")

    @property
    def from_user(self) -> Any:
        sender_id = getattr(self._event, "sender_id", None) or getattr(self._message, "sender_id", None)
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
        edit = getattr(self._event, "edit", None)
        if callable(edit):
            return await edit(text, **kwargs)
        reply = getattr(self._event, "reply", None)
        if callable(reply):
            return await reply(text, **kwargs)
        return None

    async def delete(self) -> Any:
        delete = getattr(self._event, "delete", None) or getattr(self._message, "delete", None)
        if callable(delete):
            return await delete()
        return None

    async def reply(self, text: str, **kwargs: Any) -> Any:
        reply = getattr(self._event, "reply", None)
        if callable(reply):
            return await reply(text, **kwargs)
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

    async def _cmd_redpack(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        self._bind_core_config(account_id)
        message = _NativeMessageAdapter(event, args)
        bot = _NativeClientAdapter(client or ctx.client)
        await redpack_core.redpack_command(message, bot)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        if ctx.client is None:
            return
        self._bind_core_config(ctx.account_id)
        message = _NativeMessageAdapter(event, sender=await _resolve_sender(event))
        bot = _NativeClientAdapter(ctx.client)
        await redpack_core.redpack_claim_listener(message, bot)
        await redpack_core.redpack_transfer_confirm_listener(message, bot)
