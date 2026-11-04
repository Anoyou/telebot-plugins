"""interaction-ai-chat 0.5.0：TelePilot 0.119.0 的 AI 回复 inline 代发（messages.inline_via_bot）。

- 新宿主 + 服务器“AI 回复方式”= inline 代发 + 账号本人在群里触发：占位改为“用户名 via @Bot”的 inline 消息，
  答案按 inline_message_id 编辑；
- 旧宿主（没有方法 / 能力探测为 False）或平台返回 ok=False：照旧由交互 Bot 发占位并编辑。
"""

from __future__ import annotations

import unittest

import test_interaction_ai_chat as base
import test_interaction_ai_chat_retry as retry_tests

PLUGIN = base.PLUGIN
run = base.run


class _Caps:
    def __init__(self, *names: str) -> None:
        self.names_ = set(names)

    def has(self, name: str, min_version: int = 1) -> bool:
        return name in self.names_


class _ViaMessages(base.LiveMessages):
    live_inline_edit = True

    def __init__(self, result: dict | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.via_calls: list[dict] = []
        self.result = result if result is not None else {"ok": True, "inline_message_id": "via-1", "message_id": 5}

    async def inline_via_bot(self, **kwargs):
        self.via_calls.append(kwargs)
        return dict(self.result)


def _ctx(ai, messages, caps):
    ctx = base.Ctx({"bot_username": "helper_bot", "rich_text": False}, ai=ai)
    ctx.messages = messages
    ctx.capabilities = caps
    return ctx


class InlineViaBotTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_owner_trigger_uses_inline_placeholder_and_edits_by_inline_id(self) -> None:
        messages = _ViaMessages()
        ctx = _ctx(base.CapAI("晴，25 度"), messages, _Caps("messages.inline_via_bot"))
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, retry_tests.ask()))
        self.assertEqual(len(messages.via_calls), 1)
        call = messages.via_calls[0]
        self.assertEqual(call["requester_user_id"], retry_tests.ASKER)
        self.assertEqual(call["delete_message_id"], 88)
        # 没有交互 Bot 自己发的占位。
        self.assertFalse(any(a.get("type") == "send_message" for a in messages.applied))
        edit = next(a for a in actions if a.get("type") == "edit_message")
        self.assertEqual(edit["inline_message_id"], "via-1")
        self.assertNotIn("message_id", edit)
        self.assertIn("晴", edit["text"])

    def test_platform_declines_falls_back_to_bot_placeholder(self) -> None:
        messages = _ViaMessages({"ok": False, "reason": "style_normal"})
        ctx = _ctx(base.CapAI("晴，25 度"), messages, _Caps("messages.inline_via_bot"))
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, retry_tests.ask()))
        self.assertTrue(any(a.get("type") == "send_message" for a in messages.applied))
        edit = next(a for a in actions if a.get("type") == "edit_message")
        self.assertIn("message_id", edit)

    def test_old_host_without_capability_never_calls_inline_via_bot(self) -> None:
        messages = _ViaMessages()
        ctx = _ctx(base.CapAI("晴，25 度"), messages, _Caps())
        run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, retry_tests.ask()))
        self.assertEqual(messages.via_calls, [])
        self.assertTrue(any(a.get("type") == "send_message" for a in messages.applied))


if __name__ == "__main__":
    unittest.main()
