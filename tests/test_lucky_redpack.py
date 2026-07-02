from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id: int = 1) -> None:
            self.account_id = account_id
            self.feature_key = "lucky_redpack"
            self.log = None
            self.config = {}
            self.client = None
            self.redis = None
            self.messages = None

    def register(cls):
        return cls

    def current_command_prefix(*, fallback=","):
        return fallback

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        name = getattr(entity, "first_name", None) or getattr(entity, "username", None)
        if name:
            return str(name)
        return str(fallback_id) if fallback_id not in (None, "") else default

    command_module.current_command_prefix = current_command_prefix
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name

    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules["app.worker.command"] = command_module
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module

    spec = importlib.util.spec_from_file_location(
        "lucky_redpack_plugin_under_test",
        ROOT / "lucky_redpack" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


class FakeMessage:
    _next_id = 1000

    def __init__(self, text: str = "", *, chat_id: int = 1, sender_id: int = 1, outgoing: bool = False) -> None:
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.raw_text = text
        self.text = text
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.outgoing = outgoing
        self.deleted = False
        self.replies: list[FakeMessage] = []
        self.sender = types.SimpleNamespace(id=sender_id, first_name=f"用户{sender_id}", username="", is_bot=False)

    async def reply(self, text: str):
        msg = FakeMessage(text, chat_id=self.chat_id, sender_id=0, outgoing=True)
        self.replies.append(msg)
        return msg

    async def get_sender(self):
        return self.sender

    async def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return FakeMessage(text, chat_id=chat_id, sender_id=0, outgoing=True)

    async def edit_message(self, chat_id, message_id, text, **kwargs):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})


class LuckyRedpackTest(unittest.TestCase):
    def test_parse_create_args_supports_keyword_amount_count(self) -> None:
        keyword, amount, count, error = plugin_module.parse_create_args(["发财", "88888", "10"], 100, 2)

        self.assertEqual(keyword, "发财")
        self.assertEqual(amount, 88888)
        self.assertEqual(count, 10)
        self.assertIsNone(error)

    def test_render_message_uses_requested_template(self) -> None:
        pack = plugin_module.LuckyRedpack(
            chat_id=1,
            creator_user_id=10,
            base_keyword="发财",
            total_amount=88888,
            total_count=10,
            min_share_amount=1,
            suffix_length=4,
            created_at=1,
            expires_at=999,
            current_suffix="A7K9",
            remaining_amount=77777,
            remaining_count=7,
        )

        text = plugin_module.render_redpack_message(pack)

        self.assertIn("🧧 拼手气红包", text)
        self.assertIn("总额：88888｜剩余：7/10", text)
        self.assertIn("财富密码：发财A7K9（发财是口令，后4位是随机码）", text)
        self.assertIn("发送财富密码即可领取，提示：财富密码被领一次会随机变动哦", text)

    def test_claim_sends_userbot_transfer_reply_and_refreshes_password(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.LuckyRedpackPlugin()
            ctx = PluginContext()
            ctx.client = FakeClient()
            ctx.config = {
                "command": "rp",
                "default_amount": 100,
                "default_count": 2,
                "min_share_amount": 1,
                "ttl_seconds": 60,
            }
            await plugin.on_startup(ctx)

            command_event = FakeMessage(",rp 发财 100 2", chat_id=100, sender_id=1, outgoing=True)
            await plugin._cmd_handler(ctx.client, command_event, ["发财", "100", "2"], 1, ctx)
            pack = plugin._packs[100]
            first_password = pack.current_password

            claim_event = FakeMessage(first_password, chat_id=100, sender_id=2, outgoing=False)
            await plugin.on_message(ctx, claim_event)

            self.assertEqual(len(ctx.client.sent), 1)
            self.assertEqual(ctx.client.sent[0]["reply_to"], claim_event.id)
            self.assertRegex(ctx.client.sent[0]["text"], r"^\+\d+$")
            self.assertEqual(len(ctx.client.edited), 1)
            self.assertNotEqual(pack.current_password, first_password)
            self.assertIn("财富密码：", ctx.client.edited[0]["text"])

            await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_last_claim_finishes_with_remaining_amount(self) -> None:
        pack = plugin_module.LuckyRedpack(
            chat_id=1,
            creator_user_id=1,
            base_keyword="发财",
            total_amount=100,
            total_count=2,
            min_share_amount=1,
            suffix_length=4,
            created_at=1,
            expires_at=999,
            current_suffix="A7K9",
            remaining_amount=37,
            remaining_count=1,
        )

        self.assertEqual(plugin_module.calculate_random_claim_amount(pack), 37)


if __name__ == "__main__":
    unittest.main()

