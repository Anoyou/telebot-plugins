"""Bot 防广告守卫远程模块冒烟检查。

覆盖 @xxxbot 白名单判断的关键形态，避免误删。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module() -> Any:
    base = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        commands: dict[str, Any] = {}
        message_channels: set[str] = {"incoming"}
        owner_only = True

    def register(cls: type) -> type:
        return cls

    @dataclass
    class PluginContext:
        account_id: int = 1
        feature_key: str = "bot_mute_guard"
        config: dict[str, Any] | None = None
        client: Any = None
        log: Any = None

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = register
    sys.modules["app"] = types.ModuleType("app")
    sys.modules["app.worker"] = types.ModuleType("app.worker")
    sys.modules["app.worker.plugins"] = types.ModuleType("app.worker.plugins")
    sys.modules["app.worker.plugins.base"] = base

    spec = importlib.util.spec_from_file_location(
        "bot_mute_guard_plugin",
        ROOT / "bot_mute_guard" / "plugin.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class User:
    def __init__(self, user_id: int, username: str, *, bot: bool = False) -> None:
        self.id = user_id
        self.username = username
        self.bot = bot
        self.first_name = username


class Chat:
    id = -1001234567890
    username = "target_group"


class Message:
    def __init__(self, message_id: int, text: str, *, via_bot_id: int | None = None) -> None:
        self.id = message_id
        self.message = text
        self.raw_text = text
        self.action = None
        self.via_bot_id = via_bot_id


class FakeClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []
        self.sent: list[tuple[int, str]] = []

    async def delete_messages(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class Event:
    def __init__(self, client: FakeClient, message: Message, sender: User) -> None:
        self.chat_id = -1001234567890
        self.chat = Chat()
        self.message = message
        self.raw_text = message.raw_text
        self.sender_id = sender.id
        self.sender = sender


async def main() -> None:
    plugin_mod = _load_plugin_module()
    logs: list[tuple[str, str, dict[str, Any]]] = []

    async def log(level: str, message: str, **detail: Any) -> None:
        logs.append((level, message, detail))

    client = FakeClient()
    ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={"target_chats": "-1001234567890", "allowed_bots": "@qqqbot 99"},
        client=client,
        log=log,
    )
    plugin = plugin_mod.BotMuteGuardPlugin()
    await plugin.on_startup(ctx)

    sender_a = User(777, "sender_a")
    await plugin.on_message(
        ctx,
        Event(client, Message(1, "我是 abc，@dddbot（非白名单） 踢掉！"), sender_a),
    )
    assert client.deleted == [(-1001234567890, 1)]

    sender_b = User(778, "sender_b")
    await plugin.on_message(ctx, Event(client, Message(2, "/命令@qqqbot"), sender_b))
    assert client.deleted == [(-1001234567890, 1)]

    sender_c = User(779, "sender_c")
    await plugin.on_message(ctx, Event(client, Message(3, "@defbot"), sender_c))
    assert client.deleted[-1] == (-1001234567890, 3)

    sender_d = User(780, "sender_d")
    await plugin.on_message(ctx, Event(client, Message(4, "mail abc@qqqbot.com"), sender_d))
    assert client.deleted[-1] == (-1001234567890, 3)

    sender_e = User(781, "sender_e")
    await plugin.on_message(ctx, Event(client, Message(5, "", via_bot_id=99), sender_e))
    assert client.deleted[-1] == (-1001234567890, 3)

    sender_f = User(782, "sender_f")
    await plugin.on_message(ctx, Event(client, Message(6, "", via_bot_id=100), sender_f))
    assert client.deleted[-1] == (-1001234567890, 6)

    dry_client = FakeClient()
    dry_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={
            "target_chats": "-1001234567890",
            "allowed_bots": "",
            "dry_run": True,
        },
        client=dry_client,
        log=log,
    )
    dry_plugin = plugin_mod.BotMuteGuardPlugin()
    await dry_plugin.on_startup(dry_ctx)
    await dry_plugin.on_message(dry_ctx, Event(dry_client, Message(7, "@dryrunbot"), sender_f))
    assert dry_client.deleted == []

    print("bot_mute_guard smoke ok")


if __name__ == "__main__":
    asyncio.run(main())
