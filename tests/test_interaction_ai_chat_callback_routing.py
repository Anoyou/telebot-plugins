"""interaction-ai-chat 0.4.13：宿主支持通用回调路由（messages.callback_routing）时按钮改走 cb1. token。

- 新宿主：按钮写成 ``{"text", "callback": {"data": "iac:…"}}``，宿主签发 token、点击只回到本插件，
  投递时 ``payload["callback_data"]`` 还原为 ``iac:…``，插件解析与订阅不变；
- 旧宿主（没有 ``ctx.capabilities`` 或探测为 False）：照旧直接写 ``callback_data``；
- 订阅不新增 ``own_buttons``（同一点击会投递两次 / 旧宿主当 all_allowed_chats）。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import test_interaction_ai_chat as base
import test_interaction_ai_chat_retry as retry_tests

PLUGIN = base.PLUGIN
RTY = retry_tests.RTY
run = base.run


class _Caps:
    def __init__(self, *names: str) -> None:
        self.names_ = set(names)

    def has(self, name: str, min_version: int = 1) -> bool:
        return name in self.names_


def _routed_ctx(ai, caps):
    ctx = retry_tests.make_ctx(ai, telepilot_provider="3", telepilot_model="glm-x")
    ctx.capabilities = caps
    ctx.identities = retry_tests.Identities(admins={retry_tests.ADMIN})
    return ctx


def _host_click(button: dict, message_id: int, *, user_id: int = retry_tests.ASKER) -> dict:
    """模拟宿主：cb1. token 解析后把插件自己的 data 还原进 callback_data，并附 payload.callback。"""

    data = button["callback"]["data"]
    payload = retry_tests.callback(data, message_id, user_id=user_id)
    payload["callback"] = {"data": data, "token": "cb1.fake", "plugin_key": "interaction-ai-chat", "origin": {"kind": "chat"}}
    return payload


class CallbackRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def _fail(self, caps):
        ai = retry_tests.ScriptedAI(RuntimeError("upstream 500"), "晴，25 度", providers=retry_tests.SwitchModelTest.PROVIDERS)
        ctx = _routed_ctx(ai, caps)
        plugin = PLUGIN.InteractionAIChatPlugin()
        actions = run(plugin.on_event(ctx, retry_tests.ask(reply_text="被引用的上下文")))
        markup = retry_tests.markup_of(actions)
        edit = next(a for a in actions if a.get("type") == "edit_message")
        return ai, ctx, plugin, markup, edit["message_id"]

    def test_new_host_buttons_use_callback_routing_and_retry_works(self) -> None:
        ai, ctx, plugin, markup, message_id = self._fail(_Caps("messages.callback_routing"))
        buttons = retry_tests.buttons(markup)
        self.assertEqual([b["text"] for b in buttons], ["🔄 重试", "🔀 换模型重试"])
        for button in buttons:
            self.assertNotIn("callback_data", button)
            self.assertTrue(RTY.is_own_callback(button["callback"]["data"]))
        actions = run(plugin.on_event(ctx, _host_click(buttons[0], message_id)))
        self.assertEqual(len(ai.calls), 2)
        final = [a for a in actions if a.get("type") == "edit_message"]
        self.assertIn("晴，25 度", final[0]["text"])

    def test_new_host_model_menu_buttons_are_routed(self) -> None:
        _ai, ctx, plugin, markup, message_id = self._fail(_Caps("messages.callback_routing"))
        switch = retry_tests.buttons(markup)[1]
        actions = run(plugin.on_event(ctx, _host_click(switch, message_id, user_id=retry_tests.ADMIN)))
        menu = retry_tests.buttons(retry_tests.markup_of(actions))
        self.assertTrue(menu)
        self.assertTrue(all("callback" in b and "callback_data" not in b for b in menu))
        cancel = next(b for b in menu if b["text"] == "✖ 取消")
        actions = run(plugin.on_event(ctx, _host_click(cancel, message_id, user_id=retry_tests.ADMIN)))
        back = retry_tests.buttons(retry_tests.markup_of(actions))
        self.assertEqual([b["text"] for b in back], ["🔄 重试", "🔀 换模型重试"])
        self.assertTrue(all("callback" in b for b in back))

    def test_old_host_or_missing_capability_keeps_raw_callback_data(self) -> None:
        for caps in (None, _Caps()):
            _ai, _ctx, _plugin, markup, _message_id = self._fail(caps)
            for button in retry_tests.buttons(markup):
                self.assertNotIn("callback", button)
                self.assertTrue(button["callback_data"].startswith("iac:"))

    def test_broken_capability_probe_falls_back(self) -> None:
        class Broken:
            def has(self, *_a, **_k):
                raise RuntimeError("boom")

        self.assertFalse(PLUGIN.callback_routing_supported(type("C", (), {"capabilities": Broken()})()))

    def test_subscriptions_do_not_add_own_buttons(self) -> None:
        data = json.loads((Path(base.PLUGIN_DIR) / "plugin.json").read_text(encoding="utf-8"))
        scopes = [sub.get("scope") for sub in data["event_subscriptions"]]
        self.assertNotIn("own_buttons", scopes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
