from __future__ import annotations

import asyncio
import importlib
import sys
import time
import types
import unittest


def _install_framework_stubs() -> type:
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")
    manifest_module = types.ModuleType("app.worker.plugins.manifest")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id=1, feature_key="", log=None, config=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}

    class Manifest:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    def register(cls):
        return cls

    def current_command_prefix(*, fallback=None):
        return fallback or ","

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
    manifest_module.Manifest = Manifest

    sys.modules["app"] = app_module
    sys.modules["app.worker"] = worker_module
    sys.modules["app.worker.command"] = command_module
    sys.modules["app.worker.plugins"] = plugins_module
    sys.modules["app.worker.plugins.base"] = base_module
    sys.modules["app.worker.plugins.manifest"] = manifest_module
    return PluginContext


PluginContext = _install_framework_stubs()
plugin_module = importlib.import_module("dice_grid_hunt.plugin")


def start_payload(*, prize: int = 100) -> dict:
    return {
        "source": {"type": "keyword", "chat_id": -100123, "message_id": 70},
        "actor": {"user_id": 999, "display_name": "管理员"},
        "session": {"scope": "chat", "ttl_seconds": 90},
        "prize": prize,
        "valid_seconds": 90,
    }


def answer_payload(*, text: str, user_id: int = 222, name: str = "uhaveanswer", reply_to_message_id: int | None = None) -> dict:
    payload = {
        "source": {"type": "message", "chat_id": -100123, "message_id": 100, "text": text},
        "actor": {"user_id": user_id, "display_name": name},
        "sender_user_id": user_id,
        "sender_name": name,
        "message_text": text,
    }
    if reply_to_message_id is not None:
        payload["reply_to"] = {"message_id": reply_to_message_id}
    return payload


def telepilot_message_payload(*, text: str, user_id: int = 222, name: str = "uhaveanswer") -> dict:
    return {
        "raw": {"event_type": "message", "message_id": 100, "text": text},
        "source": {
            "type": "message",
            "driver": "telegram_bot_api",
            "channel": "interaction_bot",
            "chat_id": -100123,
            "message_id": 100,
            "account_id": 1,
        },
        "message": {
            "chat_id": -100123,
            "message_id": 100,
            "text": text,
            "reply_to_message_id": None,
            "edited": False,
        },
        "actor": {"user_id": user_id, "display_name": name},
        "sender": {"user_id": user_id, "display_name": name},
        "player": {"user_id": user_id, "display_name": name},
        "source_actor": {"user_id": user_id, "display_name": name},
        "event_type": "message",
    }


class DiceGridHuntInteractionTests(unittest.TestCase):
    def test_correct_answer_edits_original_photo_caption(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.DiceGridHuntPlugin()
            ctx = PluginContext(account_id=1, feature_key="dice_grid_hunt")
            plugin_module._render_grid_png = lambda _rd: b"png-bytes"
            plugin._new_round = lambda prize, timeout=None: plugin_module.RoundState(
                rolls=[[1, 1, 1, 1, 1, 1]] * 9,
                sums=[6] * 9,
                answer_index=2,
                target_sum=22,
                prize=prize,
                started_at=time.monotonic() - 54.1,
                timeout=timeout or 90,
                last_guess_at={},
            )

            start_actions = await plugin.on_interaction(ctx, "start_dice_grid_hunt", start_payload(prize=1000))
            self.assertEqual(start_actions[0]["type"], "send_photo")
            self.assertEqual(start_actions[0]["chat_id"], -100123)
            self.assertEqual(start_actions[0]["filename"], "dice_grid_hunt.png")
            self.assertEqual(start_actions[0]["parse_mode"], "html")
            self.assertEqual(start_actions[0]["send_via"], "interaction_bot")
            self.assertEqual(start_actions[0]["send_via_options"], ["interaction_bot", "userbot_reply"])
            self.assertEqual(start_actions[0]["save_message_id_key"], "dice_grid_hunt:1:-100123:round")
            self.assertIn("九宫格竞猜v1.1.26 开始", start_actions[0]["caption"])

            answer_actions = await plugin.on_interaction(
                ctx,
                "start_dice_grid_hunt",
                answer_payload(text="2", reply_to_message_id=777),
            )

            self.assertEqual(answer_actions[0]["type"], "edit_caption")
            self.assertEqual(answer_actions[0]["chat_id"], -100123)
            self.assertNotIn("message_id", answer_actions[0])
            self.assertNotIn("edit_message_id", answer_actions[0])
            self.assertEqual(answer_actions[0]["message_id_key"], "dice_grid_hunt:1:-100123:round")
            self.assertEqual(answer_actions[0]["parse_mode"], "html")
            self.assertEqual(answer_actions[0]["send_via"], "interaction_bot")
            self.assertEqual(answer_actions[0]["send_via_options"], ["interaction_bot", "userbot_reply"])
            self.assertIn("九宫格竞猜", answer_actions[0]["caption"])
            self.assertEqual(answer_actions[0]["text"], answer_actions[0]["caption"])
            self.assertIn("恭喜 uhaveanswer 答对！", answer_actions[0]["caption"])
            self.assertIn("答案：图 2", answer_actions[0]["caption"])
            self.assertEqual(answer_actions[0]["caption"].count("+1000"), 1)
            self.assertFalse(any(action.get("type") == "send_message" for action in answer_actions))
            self.assertEqual(answer_actions[1]["type"], "payout")
            self.assertEqual(answer_actions[1]["amount"], 1000)
            self.assertEqual(answer_actions[1]["reply_to_message_id"], 100)
            self.assertEqual(answer_actions[1]["reply_to_user_id"], 222)

        asyncio.run(scenario())

    def test_correct_answer_accepts_current_telepilot_payload_shape(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.DiceGridHuntPlugin()
            ctx = PluginContext(account_id=1, feature_key="dice_grid_hunt")
            plugin_module._render_grid_png = lambda _rd: b"png-bytes"
            plugin._new_round = lambda prize, timeout=None: plugin_module.RoundState(
                rolls=[[1, 1, 1, 1, 1, 1]] * 9,
                sums=[6] * 9,
                answer_index=9,
                target_sum=18,
                prize=prize,
                started_at=time.monotonic() - 12.3,
                timeout=timeout or 90,
                last_guess_at={},
            )

            await plugin.on_interaction(ctx, "start_dice_grid_hunt", start_payload(prize=2333))
            answer_actions = await plugin.on_interaction(
                ctx,
                "start_dice_grid_hunt",
                telepilot_message_payload(text="9", user_id=1682400007, name="uhaveanswer"),
            )

            self.assertEqual(answer_actions[0]["type"], "edit_caption")
            self.assertEqual(answer_actions[0]["message_id_key"], "dice_grid_hunt:1:-100123:round")
            self.assertNotIn("message_id", answer_actions[0])
            self.assertEqual(answer_actions[1]["type"], "payout")
            self.assertEqual(answer_actions[1]["reply_to_message_id"], 100)
            self.assertEqual(answer_actions[1]["reply_to_user_id"], 1682400007)
            self.assertEqual(answer_actions[2]["result"]["winner_user_id"], 1682400007)

        asyncio.run(scenario())

    def test_correct_answer_keeps_saved_key_fallback_without_reply_target(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.DiceGridHuntPlugin()
            ctx = PluginContext(account_id=1, feature_key="dice_grid_hunt")
            plugin_module._render_grid_png = lambda _rd: b"png-bytes"
            plugin._new_round = lambda prize, timeout=None: plugin_module.RoundState(
                rolls=[[1, 1, 1, 1, 1, 1]] * 9,
                sums=[6] * 9,
                answer_index=2,
                target_sum=22,
                prize=prize,
                started_at=time.monotonic() - 54.1,
                timeout=timeout or 90,
                last_guess_at={},
            )

            await plugin.on_interaction(ctx, "start_dice_grid_hunt", start_payload(prize=1000))
            answer_actions = await plugin.on_interaction(
                ctx,
                "start_dice_grid_hunt",
                answer_payload(text="2"),
            )

            self.assertEqual(answer_actions[0]["type"], "edit_caption")
            self.assertNotIn("message_id", answer_actions[0])
            self.assertEqual(answer_actions[0]["message_id_key"], "dice_grid_hunt:1:-100123:round")

        asyncio.run(scenario())

    def test_saved_legacy_round_template_gets_versioned_title(self) -> None:
        plugin = plugin_module.DiceGridHuntPlugin()
        plugin._round_message_template = (
            "<b>九宫格竞猜</b>\n"
            "竞猜目标：找出点数和为 {target_sum} 的图片，回复 1-9 的图片数字即可\n"
            "竞猜奖励： +{prize} · 限制 {timeout}s 内 · 每人发的消息有冷却 {guess_cooldown}s"
        )
        rd = plugin_module.RoundState(
            rolls=[[1, 1, 1, 1, 1, 1]] * 9,
            sums=[6] * 9,
            answer_index=2,
            target_sum=21,
            prize=2333,
            started_at=time.monotonic(),
            timeout=90,
            last_guess_at={},
        )

        text = plugin._render_round_text(rd, include_guide=True)

        self.assertIn("<b>九宫格竞猜v1.1.26 开始</b>", text)
        self.assertIn("竞猜目标：找出点数和为 21", text)


if __name__ == "__main__":
    unittest.main()
