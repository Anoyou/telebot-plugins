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
        self.muted: list[tuple[int, int, int]] = []
        self.kicked: list[tuple[int, int]] = []
        self.banned: list[tuple[int, int]] = []

    async def delete_messages(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    async def mute_user(self, chat_id: int, user_id: int, *, duration_seconds: int) -> None:
        self.muted.append((chat_id, user_id, duration_seconds))

    async def kick_user(self, chat_id: int, user_id: int) -> None:
        self.kicked.append((chat_id, user_id))

    async def ban_user(self, chat_id: int, user_id: int) -> None:
        self.banned.append((chat_id, user_id))


class Event:
    def __init__(
        self,
        client: FakeClient,
        message: Message,
        sender: User,
        *,
        expose_chat: bool = True,
        expose_sender: bool = True,
    ) -> None:
        self.chat_id = -1001234567890
        self.message = message
        self.raw_text = message.raw_text
        self.sender_id = sender.id
        self._sender = sender
        if expose_chat:
            self.chat = Chat()
        if expose_sender:
            self.sender = sender

    async def get_chat(self) -> Chat:
        return Chat()

    async def get_sender(self) -> User:
        return self._sender


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

    bot_sender = User(783, "not_allowed_bot", bot=True)
    await plugin.on_message(
        ctx,
        Event(client, Message(7, "bot message"), bot_sender, expose_sender=False),
    )
    assert client.deleted[-1] == (-1001234567890, 7)

    username_bot_sender = User(784, "suffixbot", bot=False)
    await plugin.on_message(
        ctx,
        Event(client, Message(8, "bot flag missing"), username_bot_sender, expose_sender=False),
    )
    assert client.deleted[-1] == (-1001234567890, 8)

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
    await dry_plugin.on_message(dry_ctx, Event(dry_client, Message(9, "@dryrunbot"), sender_f))
    assert dry_client.deleted == []

    false_string_client = FakeClient()
    false_string_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={
            "target_chats": "-1001234567890",
            "allowed_bots": "",
            "dry_run": "false",
        },
        client=false_string_client,
        log=log,
    )
    false_string_plugin = plugin_mod.BotMuteGuardPlugin()
    await false_string_plugin.on_startup(false_string_ctx)
    await false_string_plugin.on_message(
        false_string_ctx,
        Event(false_string_client, Message(10, "@notdryrunbot"), sender_f),
    )
    assert false_string_client.deleted == [(-1001234567890, 10)]

    hot_reload_client = FakeClient()
    hot_reload_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={
            "target_chats": "-1001234567890",
            "allowed_bots": "@hotbot",
            "dry_run": True,
        },
        client=hot_reload_client,
        log=log,
    )
    hot_reload_plugin = plugin_mod.BotMuteGuardPlugin()
    await hot_reload_plugin.on_startup(hot_reload_ctx)
    await hot_reload_plugin.on_message(
        hot_reload_ctx,
        Event(hot_reload_client, Message(11, "@hotbot"), sender_f),
    )
    assert hot_reload_client.deleted == []

    hot_reload_ctx.config["allowed_bots"] = ""
    hot_reload_ctx.config["dry_run"] = "false"
    await hot_reload_plugin.on_message(
        hot_reload_ctx,
        Event(hot_reload_client, Message(12, "@hotbot"), sender_f),
    )
    assert hot_reload_client.deleted == [(-1001234567890, 12)]

    mute_client = FakeClient()
    mute_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={
            "target_chats": "-1001234567890",
            "allowed_bots": "",
            "violation_action": "mute_sender",
            "mute_duration_seconds": "120",
        },
        client=mute_client,
        log=log,
    )
    mute_plugin = plugin_mod.BotMuteGuardPlugin()
    await mute_plugin.on_startup(mute_ctx)
    await mute_plugin.on_message(
        mute_ctx,
        Event(mute_client, Message(13, "@muteme_bot"), sender_f),
    )
    assert mute_client.deleted == [(-1001234567890, 13)]
    assert mute_client.muted == [(-1001234567890, 782, 120)]

    kick_client = FakeClient()
    kick_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={
            "target_chats": "-1001234567890",
            "allowed_bots": "",
            "violation_action": "kick_sender",
        },
        client=kick_client,
        log=log,
    )
    kick_plugin = plugin_mod.BotMuteGuardPlugin()
    await kick_plugin.on_startup(kick_ctx)
    await kick_plugin.on_message(
        kick_ctx,
        Event(kick_client, Message(14, "@kickmebot"), sender_f),
    )
    assert kick_client.deleted == [(-1001234567890, 14)]
    assert kick_client.kicked == [(-1001234567890, 782)]

    disabled_rule_client = FakeClient()
    disabled_rule_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={
            "target_chats": "-1001234567890",
            "allowed_bots": "",
            "delete_untrusted_bot_mentions": "false",
        },
        client=disabled_rule_client,
        log=log,
    )
    disabled_rule_plugin = plugin_mod.BotMuteGuardPlugin()
    await disabled_rule_plugin.on_startup(disabled_rule_ctx)
    await disabled_rule_plugin.on_message(
        disabled_rule_ctx,
        Event(disabled_rule_client, Message(15, "@disabledbot"), sender_f),
    )
    assert disabled_rule_client.deleted == []

    name_target_client = FakeClient()
    name_target_ctx = sys.modules["app.worker.plugins.base"].PluginContext(
        config={"target_chats": "@target_group", "allowed_bots": ""},
        client=name_target_client,
        log=log,
    )
    name_target_plugin = plugin_mod.BotMuteGuardPlugin()
    await name_target_plugin.on_startup(name_target_ctx)
    await name_target_plugin.on_message(
        name_target_ctx,
        Event(name_target_client, Message(16, "@byusernamebot"), sender_f, expose_chat=False),
    )
    assert name_target_client.deleted == [(-1001234567890, 16)]

    print("bot_mute_guard smoke ok")


if __name__ == "__main__":
    asyncio.run(main())
