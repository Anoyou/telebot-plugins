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

    async def reply(self, text: str, **kwargs):
        msg = FakeMessage(text, chat_id=self.chat_id, sender_id=0, outgoing=True)
        msg.kwargs = kwargs
        self.replies.append(msg)
        return msg

    async def get_sender(self):
        return self.sender

    async def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.files: list[dict] = []
        self.edited: list[dict] = []
        self.deleted: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        msg = FakeMessage(text, chat_id=chat_id, sender_id=0, outgoing=True)
        self.sent.append({"chat_id": chat_id, "text": text, "message_id": msg.id, **kwargs})
        return msg

    async def send_file(self, chat_id, file, **kwargs):
        msg = FakeMessage(kwargs.get("caption", ""), chat_id=chat_id, sender_id=0, outgoing=True)
        self.files.append({"chat_id": chat_id, "file": file, "message_id": msg.id, **kwargs})
        return msg

    async def edit_message(self, chat_id, message_id, text, **kwargs):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})

    async def delete_messages(self, chat_id, message_ids):
        self.deleted.append({"chat_id": chat_id, "message_ids": list(message_ids)})


class LuckyRedpackTest(unittest.TestCase):
    def test_parse_create_args_supports_keyword_amount_count(self) -> None:
        keyword, amount, count, error = plugin_module.parse_create_args(["发财", "88888", "10"], 100, 2)

        self.assertEqual(keyword, "发财")
        self.assertEqual(amount, 88888)
        self.assertEqual(count, 10)
        self.assertIsNone(error)

    def test_render_message_uses_requested_template(self) -> None:
        pack = plugin_module.LuckyRedpack(
            pack_code="ABC123",
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
        self.assertIn("红包代码：ABC123", text)
        self.assertIn("总额：88888｜剩余：7/10", text)
        self.assertIn("财富密码：发财A7K9", text)
        self.assertIn("发送财富密码即可领取\n提示：财富密码被领一次会随机变动", text)
        self.assertNotIn("是口令", text)
        self.assertNotIn("随机码）", text)

    def test_render_message_uses_folded_claim_details(self) -> None:
        pack = plugin_module.LuckyRedpack(
            pack_code="ABC123",
            chat_id=1,
            creator_user_id=10,
            base_keyword="发财",
            total_amount=666,
            total_count=3,
            min_share_amount=1,
            suffix_length=4,
            created_at=1,
            expires_at=999,
            current_suffix="8ZB9",
            remaining_amount=111,
            remaining_count=1,
        )
        pack.claims.append(plugin_module.ClaimRecord(user_id=2, display_name="用户<2>", amount=222, message_id=10))
        pack.claims.append(plugin_module.ClaimRecord(user_id=3, display_name="用户3", amount=333, message_id=11))

        text = plugin_module.render_redpack_message(pack)

        self.assertIn("已领取：2 人", text)
        self.assertIn("<blockquote expandable>领取详情：", text)
        self.assertIn("1. 用户&lt;2&gt; +222", text)
        self.assertIn("2. 用户3 +333 🏆", text)
        self.assertTrue(text.endswith("</blockquote>"))

    def test_claim_sends_userbot_transfer_reply_and_resends_redpack_message(self) -> None:
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
            pack = plugin._packs[100][0]
            first_password = pack.current_password
            old_redpack_message_id = pack.message_id

            claim_event = FakeMessage(first_password, chat_id=100, sender_id=2, outgoing=False)
            await plugin.on_message(ctx, claim_event)

            self.assertEqual(len(ctx.client.sent), 3)
            self.assertEqual(ctx.client.sent[1]["reply_to"], claim_event.id)
            self.assertRegex(ctx.client.sent[1]["text"], r"^\+\d+$")
            self.assertEqual(len(ctx.client.edited), 0)
            self.assertEqual(ctx.client.deleted[0]["message_ids"], [old_redpack_message_id])
            self.assertNotEqual(pack.current_password, first_password)
            self.assertIn("财富密码：", ctx.client.sent[2]["text"])
            self.assertIn("<blockquote expandable>领取详情：", ctx.client.sent[2]["text"])
            self.assertEqual(ctx.client.sent[2]["parse_mode"], "html")

            await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_claim_password_ignores_inner_spaces(self) -> None:
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

            command_event = FakeMessage(",rp 测试 100 2", chat_id=100, sender_id=1, outgoing=True)
            await plugin._cmd_handler(ctx.client, command_event, ["测试", "100", "2"], 1, ctx)
            pack = plugin._packs[100][0]
            spaced_password = f"{pack.base_keyword} {pack.current_suffix}"

            claim_event = FakeMessage(spaced_password, chat_id=100, sender_id=2, outgoing=False)
            await plugin.on_message(ctx, claim_event)

            self.assertEqual(ctx.client.sent[1]["reply_to"], claim_event.id)
            self.assertRegex(ctx.client.sent[1]["text"], r"^\+\d+$")

            await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_creator_can_claim_with_outgoing_message_by_default(self) -> None:
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

            command_event = FakeMessage(",rp 测试 100 2", chat_id=100, sender_id=1, outgoing=True)
            await plugin._cmd_handler(ctx.client, command_event, ["测试", "100", "2"], 1, ctx)
            pack = plugin._packs[100][0]

            claim_event = FakeMessage(pack.current_password, chat_id=100, sender_id=1, outgoing=True)
            await plugin.on_message(ctx, claim_event)

            self.assertEqual(ctx.client.sent[1]["reply_to"], claim_event.id)
            self.assertRegex(ctx.client.sent[1]["text"], r"^\+\d+$")

            await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_image_mode_sends_password_as_file_without_caption_password(self) -> None:
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
            original_builder = plugin_module.build_password_image
            try:
                fake_path = ROOT / "tests" / "_fake_lucky_redpack.png"
                fake_path.write_bytes(b"png")

                def fake_build_password_image(password):
                    fake_path.write_bytes(password.encode("utf-8"))
                    return fake_path

                plugin_module.build_password_image = fake_build_password_image
                command_event = FakeMessage(",rp img 发财 100 2", chat_id=100, sender_id=1, outgoing=True)
                await plugin._cmd_handler(ctx.client, command_event, ["img", "发财", "100", "2"], 1, ctx)

                pack = plugin._packs[100][0]
                self.assertTrue(pack.image_mode)
                self.assertEqual(len(ctx.client.files), 1)
                self.assertIn("财富密码：见图片", ctx.client.files[0]["caption"])
                self.assertNotIn(pack.current_password, ctx.client.files[0]["caption"])
                self.assertEqual(ctx.client.files[0]["parse_mode"], "html")
            finally:
                plugin_module.build_password_image = original_builder
                fake_path = ROOT / "tests" / "_fake_lucky_redpack.png"
                if fake_path.exists():
                    fake_path.unlink()
                await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_delete_command_message_falls_back_to_client_delete_messages(self) -> None:
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
                "delete_command_message": True,
            }
            await plugin.on_startup(ctx)

            command_event = FakeMessage(",rp 发财 100 2", chat_id=100, sender_id=1, outgoing=True)
            command_event.delete = None
            await plugin._cmd_handler(ctx.client, command_event, ["发财", "100", "2"], 1, ctx)

            self.assertEqual(ctx.client.deleted[0]["message_ids"], [command_event.id])
            await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_multiple_redpacks_can_run_and_list_off_by_code(self) -> None:
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

            first_event = FakeMessage(",rp 发财 100 2", chat_id=100, sender_id=1, outgoing=True)
            second_event = FakeMessage(",rp 好运 200 2", chat_id=100, sender_id=1, outgoing=True)
            await plugin._cmd_handler(ctx.client, first_event, ["发财", "100", "2"], 1, ctx)
            await plugin._cmd_handler(ctx.client, second_event, ["好运", "200", "2"], 1, ctx)

            self.assertEqual(len(plugin._packs[100]), 2)
            first_pack, second_pack = plugin._packs[100]
            self.assertNotEqual(first_pack.pack_code, second_pack.pack_code)

            list_event = FakeMessage(",rp list", chat_id=100, sender_id=1, outgoing=True)
            await plugin._cmd_handler(ctx.client, list_event, ["list"], 1, ctx)
            self.assertIn(first_pack.pack_code, list_event.replies[-1].text)
            self.assertIn(second_pack.pack_code, list_event.replies[-1].text)

            off_event = FakeMessage(f",rp off {first_pack.pack_code}", chat_id=100, sender_id=1, outgoing=True)
            await plugin._cmd_handler(ctx.client, off_event, ["off", first_pack.pack_code], 1, ctx)
            self.assertEqual([pack.pack_code for pack in plugin._packs[100]], [second_pack.pack_code])
            self.assertIn(f"已关闭红包 {first_pack.pack_code}", off_event.replies[-1].text)

            await plugin.on_shutdown(ctx)

        asyncio.run(run_case())

    def test_last_claim_finishes_with_remaining_amount(self) -> None:
        pack = plugin_module.LuckyRedpack(
            pack_code="ABC123",
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
