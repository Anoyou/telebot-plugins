"""interaction-ai-chat 0.4.3/0.4.4：推理草稿不得当作答案发送；访客模式占位优先用 Rich Message 编辑。

复现：GLM-5.3-Flash（Z.ai，始终开启推理）在群里被问空枪资源，Bot 发出的是截断的英文推理草稿
"The user is asking again whether "空枪" ... Let me organize the data into a table as suggested: ..."。
"""

from __future__ import annotations

import types
import unittest

import test_interaction_ai_chat as base
from test_interaction_ai_chat_richtext import RichMessages

PLUGIN = base.PLUGIN
run = base.run

DRAFT = (
    'The user is asking again whether "空枪" has resources. Let me organize the data into a table as suggested: '
    "• Columns: title, size • Recommendation: ... Table columns: 标题、大小、做"
)
TABLE_ANSWER = "空枪站里有 2 个结果：\n\n| 标题 | 大小 |\n|---|---|\n| **[Kong Qiang 2026](https://pt.example/details.php?id=1)** | 20 GB |"


class ScriptedAI:
    """按顺序返回脚本结果；支持 thinking 参数（模拟 TelePilot 0.106.0-beta.2+ 的 ctx.ai.complete）。"""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def complete(self, system, user, *, max_tokens=512, timeout_seconds=None, thinking=None, **kwargs):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens, "thinking": thinking, **kwargs})
        item = self.results.pop(0) if self.results else ""
        if isinstance(item, Exception):
            raise item
        return types.SimpleNamespace(text=item, finish_reason="stop", reasoning_tokens=0, output_tokens=10)


class OldAI:
    """旧平台：ctx.ai.complete 没有 thinking 参数。"""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        return types.SimpleNamespace(text=self.results.pop(0))


def AIResponseTruncated(message: str) -> Exception:  # noqa: N802 - 模拟平台异常类名
    return type("AIResponseTruncated", (RuntimeError,), {})(message)


def answer_text(actions: list[dict]) -> str:
    return "\n".join(str(action.get("text") or "") for action in actions)


class DraftDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_detects_reasoning_drafts(self) -> None:
        detect = PLUGIN.looks_like_reasoning_draft
        self.assertTrue(detect(DRAFT, "空枪有资源吗"))
        self.assertTrue(detect("Okay, so the user wants a table.", "空枪有资源吗"))
        self.assertTrue(detect("<think>先查列表，再整理", "空枪有资源吗"))
        self.assertTrue(detect("Let me check the search results first.", "空枪有资源吗"))
        self.assertTrue(detect("I should present this as a table with columns: title, size. Recommendation: pick the first one. " * 2, "空枪有资源吗"))

    def test_normal_answers_are_not_drafts(self) -> None:
        detect = PLUGIN.looks_like_reasoning_draft
        self.assertFalse(detect(TABLE_ANSWER, "空枪有资源吗"))
        self.assertFalse(detect("<think>想一下</think>站里有 2 个结果。", "空枪有资源吗"))
        self.assertFalse(detect("Let me explain: TCP uses a three-way handshake.", "How does TCP work?"))
        self.assertFalse(
            detect("| Title | Size |\n|---|---|\n| **[Kong Qiang 2026 2160p WEB-DL HEVC](https://pt.example/details.php?id=1)** | 20 GB |", "空枪有资源吗")
        )


class DraftGuardFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_system_prompt_requires_chinese_final_answer_only(self) -> None:
        ai = ScriptedAI(TABLE_ANSWER)
        ctx = base.Ctx(ai=ai)
        run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        system = ai.calls[0]["system"]
        self.assertIn("始终用简体中文回答", system)
        self.assertIn("只输出给用户看的最终答案", system)

    def test_normal_chinese_table_answer_is_untouched(self) -> None:
        ai = ScriptedAI(TABLE_ANSWER)
        ctx = base.Ctx(ai=ai)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(len(ai.calls), 1)
        self.assertIsNone(ai.calls[0]["thinking"])
        text = answer_text(actions)
        self.assertIn("空枪站里有 2 个结果", text)
        self.assertIn('<b><a href="https://pt.example/details.php?id=1">Kong Qiang 2026</a></b>', text)

    def test_draft_is_dropped_and_retried_with_thinking_disabled(self) -> None:
        ai = ScriptedAI(DRAFT, TABLE_ANSWER)
        ctx = base.Ctx(ai=ai, config={"max_tokens": 800})
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(len(ai.calls), 2)
        retry = ai.calls[1]
        self.assertEqual(retry["thinking"], "disabled")
        self.assertEqual(retry["max_tokens"], 2000)
        self.assertIn("直接输出给用户的最终中文回答", retry["system"])
        text = answer_text(actions)
        self.assertNotIn("The user is asking", text)
        self.assertIn("空枪站里有 2 个结果", text)
        self.assertTrue(any("推理草稿" in log[1] for log in ctx.logs))

    def test_retry_still_draft_sends_error_template(self) -> None:
        ai = ScriptedAI(DRAFT, "Okay, so the user wants a table...")
        ctx = base.Ctx(ai=ai)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(len(ai.calls), 2)
        text = answer_text(actions)
        self.assertEqual(text, "模型没有给出最终答案，请稍后再试或换模型。")
        self.assertNotIn("The user", text)
        # 失败不扣次数、不写入记忆
        self.assertTrue(any("没有给出最终答案" in log[1] for log in ctx.logs))

    def test_platform_truncation_error_triggers_retry(self) -> None:
        ai = ScriptedAI(AIResponseTruncated("模型输出被截断（finish_reason=length）"), TABLE_ANSWER)
        ctx = base.Ctx(ai=ai)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(len(ai.calls), 2)
        self.assertEqual(ai.calls[1]["thinking"], "disabled")
        self.assertIn("空枪站里有 2 个结果", answer_text(actions))

    def test_truncation_twice_sends_error_template(self) -> None:
        ai = ScriptedAI(AIResponseTruncated("模型输出被截断（finish_reason=length）"), AIResponseTruncated("again"))
        ctx = base.Ctx(ai=ai)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(answer_text(actions), "模型没有给出最终答案，请稍后再试或换模型。")

    def test_old_platform_retries_without_thinking_kwarg(self) -> None:
        ai = OldAI(DRAFT, TABLE_ANSWER)
        ctx = base.Ctx(ai=ai)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(len(ai.calls), 2)
        self.assertNotIn("thinking", ai.calls[1])
        self.assertIn("空枪站里有 2 个结果", answer_text(actions))


class GuestInlineRichEditTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_guest_placeholder_uses_inline_rich_edit_when_platform_supports(self) -> None:
        messages = RichMessages()
        messages.inline_rich_edit = True
        messages.live_inline_edit = True
        ctx = base.live_ctx(ai=base.CapAI(text=TABLE_ANSWER), messages=messages)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual(actions, [])
        self.assertEqual([a["type"] for a in messages.applied], ["answer_guest_query", "edit_message"])
        edit = messages.applied[1]
        self.assertEqual(edit["inline_message_id"], "inline-abc")
        self.assertIn("| 标题 | 大小 |", edit["rich_message"]["markdown"])
        self.assertNotIn("text", edit)

    def test_guest_inline_rich_rejected_falls_back_to_html(self) -> None:
        messages = RichMessages(rich_result={"ok": False, "failed": 1})
        messages.inline_rich_edit = True
        messages.live_inline_edit = True
        ctx = base.live_ctx(ai=base.CapAI(text="## 标题\n*斜*"), messages=messages)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("问")))
        self.assertEqual(
            actions,
            [{"type": "edit_message", "inline_message_id": "inline-abc", "text": "<b>标题</b>\n<i>斜</i>", "parse_mode": "html", "send_via": ["interaction_bot"]}],
        )

    def test_guest_final_answer_renders_pt_table_as_native_rich(self) -> None:
        """访客模式最终渲染路径：PT 表格（标题加粗即详情页链接）原样进 Rich Markdown，不退回 HTML。"""

        messages = RichMessages()
        messages.inline_rich_edit = True
        messages.live_inline_edit = True
        answer = (
            "找到 2 个种子：\n\n| 标题 | 大小 | 做种/下载 | 优惠 | 发布时间 |\n|---|---|---|---|---|\n"
            "| **[沙丘3 2026 2160p](https://pt.example/details.php?id=11)** | 25 GB | 12/3 | 免费 | 2026-10-25 |\n"
            "| **[沙丘3 2026 1080p](https://pt.example/details.php?id=12)** | 9 GB | 30/1 | 无 | 2026-10-24 |"
        )
        ctx = base.live_ctx(ai=base.CapAI(text=answer), messages=messages)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("站里有没有沙丘3")))
        self.assertEqual(actions, [])
        self.assertEqual([a["type"] for a in messages.applied], ["answer_guest_query", "edit_message"])
        markdown = messages.applied[1]["rich_message"]["markdown"]
        self.assertIn("| 标题 | 大小 | 做种/下载 | 优惠 | 发布时间 |", markdown)
        self.assertIn("**[沙丘3 2026 2160p](https://pt.example/details.php?id=11)**", markdown)
        self.assertEqual(messages.applied[1]["send_via"], ["interaction_bot"])
        self.assertFalse(any("改用 Telegram HTML" in log[1] for log in ctx.logs))

    def test_guest_beta2_platform_without_live_inline_edit_goes_html(self) -> None:
        """TelePilot 0.106.0-beta.2 只声明 inline_rich_edit，实时 apply 不认 inline_message_id：不再白试一次 Rich。"""

        messages = RichMessages()
        messages.inline_rich_edit = True
        ctx = base.live_ctx(ai=base.CapAI(text=TABLE_ANSWER), messages=messages)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("空枪有资源吗")))
        self.assertEqual([a["type"] for a in messages.applied], ["answer_guest_query"])
        self.assertEqual(actions[0]["type"], "edit_message")
        self.assertEqual(actions[0]["inline_message_id"], "inline-abc")
        self.assertEqual(actions[0]["parse_mode"], "html")
        self.assertFalse(any("Rich Message 被拒绝" in log[1] for log in ctx.logs))

    def test_guest_inline_rich_rejected_logs_telegram_error(self) -> None:
        messages = RichMessages(
            rich_result={
                "ok": False,
                "executed": 1,
                "failed": 1,
                "errors": [{"index": 0, "type": "edit_message", "error_code": "telegram_api_error", "error": "Bad Request: MESSAGE_NOT_MODIFIED"}],
            }
        )
        messages.inline_rich_edit = True
        messages.live_inline_edit = True
        ctx = base.live_ctx(ai=base.CapAI(text="**答**"), messages=messages)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("问")))
        self.assertEqual(actions[0]["parse_mode"], "html")
        [log] = [log for log in ctx.logs if "Rich Message 被拒绝" in log[1]]
        self.assertEqual(log[2]["error_code"], "telegram_api_error")
        self.assertEqual(log[2]["telegram_error"], "Bad Request: MESSAGE_NOT_MODIFIED")
        self.assertEqual(log[2]["kind"], "guest")

    def test_guest_placeholder_without_capability_keeps_html(self) -> None:
        messages = RichMessages()
        ctx = base.live_ctx(ai=base.CapAI(text="**答**"), messages=messages)
        actions = run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("问")))
        self.assertEqual([a["type"] for a in messages.applied], ["answer_guest_query"])
        self.assertEqual(actions[0]["type"], "edit_message")
        self.assertEqual(actions[0]["parse_mode"], "html")


if __name__ == "__main__":
    unittest.main()
