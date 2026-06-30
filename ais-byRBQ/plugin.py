
from __future__ import annotations

import inspect
import logging
import re
import sys
import types
from dataclasses import dataclass
from typing import Any, Callable

from app.worker.plugins.base import Plugin, PluginContext, register


def _current_command_prefix() -> str:
    try:
        from app.worker.command import current_command_prefix

        return str(current_command_prefix(fallback=",") or ",")
    except Exception:  # pragma: no cover - standalone compatibility
        return ","


def _normalize_legacy_command_examples(text: str) -> str:
    return text.replace(",ais", f"{_current_command_prefix()}ais")


@dataclass
class _ListenerSpec:
    func: Callable[..., Any]
    command: str | None
    incoming: bool
    outgoing: bool


class _CompatRuntime:
    def __init__(self, key: str, default_command: str) -> None:
        self.key = key
        self.default_command = default_command
        self.listeners: list[_ListenerSpec] = []
        self.startup_hooks: list[Callable[..., Any]] = []
        self.shutdown_hooks: list[Callable[..., Any]] = []
        self.ctx: PluginContext | None = None
        self.client: Any = None
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True

        runtime = self

        pagermaid = types.ModuleType("pagermaid")
        listener_mod = types.ModuleType("pagermaid.listener")
        hook_mod = types.ModuleType("pagermaid.hook")
        enums_mod = types.ModuleType("pagermaid.enums")
        utils_mod = types.ModuleType("pagermaid.utils")

        def listener(**kwargs):
            def deco(func):
                runtime.listeners.append(
                    _ListenerSpec(
                        func=func,
                        command=kwargs.get("command"),
                        incoming=bool(kwargs.get("incoming", True)),
                        outgoing=bool(kwargs.get("outgoing", True)),
                    )
                )
                return func
            return deco

        class Hook:
            @staticmethod
            def on_startup():
                def deco(func):
                    runtime.startup_hooks.append(func)
                    return func
                return deco

            @staticmethod
            def on_shutdown():
                def deco(func):
                    runtime.shutdown_hooks.append(func)
                    return func
                return deco

        class _Logs:
            def __init__(self):
                self._l = logging.getLogger(f"plugin.{runtime.key}")
            def info(self, msg): self._l.info(msg)
            def warning(self, msg): self._l.warning(msg)
            def error(self, msg): self._l.error(msg)
            def debug(self, msg): self._l.debug(msg)

        listener_mod.listener = listener
        hook_mod.Hook = Hook
        enums_mod.Message = object
        enums_mod.Client = object
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

    async def _call(self, func: Callable[..., Any], message: Any, bot: Any) -> None:
        params = list(inspect.signature(func).parameters.values())
        argc = len(params)
        if argc <= 1:
            out = func(message)
        else:
            out = func(message, bot)
        if inspect.isawaitable(out):
            await out

    @staticmethod
    def _is_outgoing(event: Any) -> bool:
        return bool(getattr(event, "out", False) or getattr(event, "is_outgoing", False))

    async def run_hooks(self, startup: bool) -> None:
        hooks = self.startup_hooks if startup else self.shutdown_hooks
        bot = _CompatClient(self.client)
        for h in hooks:
            try:
                out = h()
                if inspect.isawaitable(out):
                    await out
            except TypeError:
                out = h(bot)
                if inspect.isawaitable(out):
                    await out

    async def dispatch_command(self, cmd: str, event: Any, args: list[str]) -> bool:
        if not self.ctx:
            return False
        text = (getattr(event, "raw_text", None) or getattr(event, "text", None) or "").strip()
        msg = _CompatMessage(event, text, args)
        bot = _CompatClient(self.client)
        handled = False
        is_out = self._is_outgoing(event)
        for spec in self.listeners:
            if not spec.command:
                continue
            if spec.command != cmd:
                continue
            if is_out and not spec.outgoing:
                continue
            if (not is_out) and not spec.incoming:
                continue
            handled = True
            await self._call(spec.func, msg, bot)
        return handled

    async def dispatch_message(self, event: Any) -> None:
        if not self.ctx:
            return
        text = (getattr(event, "raw_text", None) or getattr(event, "text", None) or "").strip()
        cmd, args = _parse_command(text)
        msg = _CompatMessage(event, text, args)
        bot = _CompatClient(self.client)
        is_out = self._is_outgoing(event)

        for spec in self.listeners:
            if is_out and not spec.outgoing:
                continue
            if (not is_out) and not spec.incoming:
                continue
            if spec.command:
                # command listeners are dispatched via on_command path
                continue
            await self._call(spec.func, msg, bot)


def _parse_command(text: str) -> tuple[str | None, list[str]]:
    if not text:
        return None, []
    m = re.match(r"^[,/]([A-Za-z0-9_\-]+)(?:\s+(.*))?$", text)
    if not m:
        return None, []
    c = m.group(1)
    arg_str = (m.group(2) or "").strip()
    return c, ([x for x in arg_str.split() if x] if arg_str else [])


class _CompatClient:
    def __init__(self, raw_client: Any) -> None:
        self._c = raw_client

    async def get_me(self):
        if hasattr(self._c, "get_me"):
            return await self._c.get_me()
        return types.SimpleNamespace(id=0)

    async def send_message(self, chat_id: int, text: str, **kwargs):
        text = _normalize_legacy_command_examples(text)
        return await self._c.send_message(chat_id, text, **kwargs)

    async def send_photo(self, chat_id: int, photo: Any, **kwargs):
        if hasattr(self._c, "send_photo"):
            return await self._c.send_photo(chat_id, photo, **kwargs)
        return await self._c.send_file(chat_id, photo, **kwargs)

    async def send_document(self, chat_id: int, document: Any, **kwargs):
        if hasattr(self._c, "send_document"):
            return await self._c.send_document(chat_id, document, **kwargs)
        return await self._c.send_file(chat_id, document, **kwargs)

    async def send_media_group(self, chat_id: int, media: list[Any], **kwargs):
        if hasattr(self._c, "send_media_group"):
            return await self._c.send_media_group(chat_id, media, **kwargs)
        out = []
        for m in media:
            cap = getattr(m, "caption", None)
            file = getattr(m, "media", None) or getattr(m, "file", None) or m
            out.append(await self.send_photo(chat_id, file, caption=cap, **kwargs))
        return out

    async def get_messages(self, chat_id: int, message_ids: Any):
        return await self._c.get_messages(chat_id, message_ids)

    async def edit_message_caption(self, chat_id: int, message_id: int, caption: str, **kwargs):
        caption = _normalize_legacy_command_examples(caption)
        if hasattr(self._c, "edit_message_caption"):
            return await self._c.edit_message_caption(chat_id, message_id, caption, **kwargs)
        return await self._c.edit_message(chat_id, message_id, caption, **kwargs)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs):
        text = _normalize_legacy_command_examples(text)
        if hasattr(self._c, "edit_message_text"):
            return await self._c.edit_message_text(chat_id, message_id, text, **kwargs)
        return await self._c.edit_message(chat_id, message_id, text, **kwargs)

    async def send_reaction(self, chat_id: int, message_id: int, emoji: Any):
        if hasattr(self._c, "send_reaction"):
            return await self._c.send_reaction(chat_id, message_id, emoji)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._c, name)


class _CompatMessage:
    def __init__(self, event: Any, text: str, args: list[str]) -> None:
        self._e = event
        self.text = text
        self.caption = getattr(event, "caption", None)
        self.arguments = " ".join(args).strip()

    @property
    def id(self):
        return getattr(self._e, "id", None)

    @property
    def chat(self):
        c = getattr(self._e, "chat", None)
        if c is not None:
            return c
        chat_id = getattr(self._e, "chat_id", None)
        if chat_id is None:
            return None
        return types.SimpleNamespace(id=chat_id, title="", first_name="")

    @property
    def from_user(self):
        u = getattr(self._e, "from_user", None) or getattr(self._e, "sender", None)
        return u

    @property
    def reply_to_message(self):
        return getattr(self._e, "reply_to_message", None)

    @property
    def sticker(self):
        return getattr(self._e, "sticker", None)

    @property
    def photo(self):
        return getattr(self._e, "photo", None)

    @property
    def document(self):
        return getattr(self._e, "document", None)

    @property
    def reactions(self):
        return getattr(self._e, "reactions", None)

    async def edit(self, text: str, **kwargs):
        text = _normalize_legacy_command_examples(text)
        if hasattr(self._e, "edit"):
            return await self._e.edit(text, **kwargs)
        if hasattr(self._e, "reply"):
            return await self._e.reply(text, **kwargs)
        return None

    async def delete(self):
        if hasattr(self._e, "delete"):
            return await self._e.delete()
        return None

    async def reply(self, text: str, **kwargs):
        text = _normalize_legacy_command_examples(text)
        if hasattr(self._e, "reply"):
            return await self._e.reply(text, **kwargs)
        return await self.edit(text, **kwargs)

    async def reply_sticker(self, sticker: Any, **kwargs):
        if hasattr(self._e, "reply_sticker"):
            return await self._e.reply_sticker(sticker, **kwargs)
        if hasattr(self._e, "reply"):
            return await self._e.reply(f"[sticker] {sticker}", **kwargs)
        return None

    async def click(self, row: int, col: int):
        if hasattr(self._e, "click"):
            return await self._e.click(row, col)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)


RUNTIME = _CompatRuntime(key="ais-byRBQ", default_command="ais")
RUNTIME.install()

from . import legacy_main  # noqa: E402,F401


@register
class AisByRBQPlugin(Plugin):
    key = "ais-byRBQ"
    display_name = "ais-byRBQ"
    message_channels = {"incoming", "outgoing"}
    owner_only = False

    async def on_startup(self, ctx: PluginContext) -> None:
        RUNTIME.ctx = ctx
        RUNTIME.client = ctx.client
        cmds = {spec.command for spec in RUNTIME.listeners if spec.command}
        self.commands = {c: self._cmd_dispatch for c in sorted(cmds)}
        await RUNTIME.run_hooks(startup=True)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        RUNTIME.ctx = ctx
        RUNTIME.client = ctx.client
        await RUNTIME.run_hooks(startup=False)

    async def _cmd_dispatch(self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext) -> None:
        text = (getattr(event, "raw_text", None) or getattr(event, "text", None) or "").strip()
        cmd, parsed_args = _parse_command(text)
        if not cmd:
            return
        RUNTIME.ctx = ctx
        RUNTIME.client = client
        await RUNTIME.dispatch_command(cmd, event, parsed_args)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        RUNTIME.ctx = ctx
        RUNTIME.client = ctx.client
        await RUNTIME.dispatch_message(event)
