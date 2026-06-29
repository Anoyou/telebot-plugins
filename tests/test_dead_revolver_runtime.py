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
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")
    telethon_module = types.ModuleType("telethon")
    events_module = types.ModuleType("telethon.events")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id: int = 1) -> None:
            self.account_id = account_id
            self.feature_key = "dead_revolver"
            self.log = None
            self.config = {}
            self.client = None
            self.redis = None

    def register(cls):
        return cls

    def public_entity_display_name(entity, *, fallback_id=None, default="玩家"):
        name = getattr(entity, "first_name", None) or getattr(entity, "username", None)
        if name:
            return str(name)
        return str(fallback_id) if fallback_id not in (None, "") else default

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    base_module.public_entity_display_name = public_entity_display_name
    telethon_module.events = events_module

    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module
    sys.modules.setdefault("telethon", telethon_module)
    sys.modules.setdefault("telethon.events", events_module)

    spec = importlib.util.spec_from_file_location(
        "dead_revolver_plugin_under_test",
        ROOT / "dead_revolver" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


class DeadRevolverRuntimeTest(unittest.TestCase):
    def test_cancel_turn_timer_does_not_cancel_current_timeout_task(self) -> None:
        async def run_case() -> bool:
            plugin = plugin_module.DeadRevolverPlugin()
            gs = plugin_module.GameState(game_id="g1", chat_id=100, host_user_id=1, entry_fee=10)
            gs.turn_timer = asyncio.current_task()
            plugin._cancel_turn_timer(gs)
            await asyncio.sleep(0)
            return True

        self.assertTrue(asyncio.run(run_case()))

    def test_next_turn_guidance_deletes_previous_button_message(self) -> None:
        deleted: list[int] = []
        sent: list[dict] = []

        account_bot_service = types.ModuleType("app.services.account_bot_service")

        async def send_message(token, chat_id, text, reply_to_message_id=None, reply_markup=None):
            sent.append({"token": token, "chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return {"message_id": 12}

        async def delete_message(token, chat_id, msg_id):
            deleted.append(int(msg_id))

        account_bot_service.send_message = send_message
        account_bot_service.delete_message = delete_message
        sys.modules.setdefault("app.services", types.ModuleType("app.services"))
        sys.modules["app.services.account_bot_service"] = account_bot_service

        async def run_case() -> None:
            plugin = plugin_module.DeadRevolverPlugin()

            async def get_bot_token(ctx):
                return "bot-token"

            plugin._get_bot_token = get_bot_token
            ctx = PluginContext()
            gs = plugin_module.GameState(game_id="g1", chat_id=100, host_user_id=1, entry_fee=10)
            gs.interaction_bot = True
            gs.guidance_msg_id = 11
            gs.tracked_msg_ids = [11]
            current = plugin_module.Player(player_id=1, user_id=10, display_name="玩家A")
            other = plugin_module.Player(player_id=2, user_id=20, display_name="玩家B")

            await plugin._send_turn_guidance(ctx, gs, current, [current, other])

            self.assertEqual(deleted, [11])
            self.assertEqual(gs.guidance_msg_id, 12)
            self.assertEqual(gs.tracked_msg_ids, [12])
            self.assertEqual(len(sent), 1)

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
