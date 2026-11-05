"""interaction-ai-chat 0.5.1：回答排版（引用 → 分割线 → 回答 → 分割线 → 署名）与模板换行。

- 模板里的单个换行按字面保留（Rich Markdown 用 ``<br>``），手打横线和署名不再挤成一行；
- 宿主声明 ``messages.rich_divider``（TelePilot 0.120.0-beta.2+）时：整行横线换成原生分割线 ``---``，
  回答前后自动补分割线；普通（HTML）回复用整行「━」；
- 旧宿主（没有 ctx.capabilities / 探测为 False）不加分割线，照旧输出；
- 系统提示要求模型分节时用单独一行 ``---``。
"""

from __future__ import annotations

import unittest

import test_interaction_ai_chat as base
from test_interaction_ai_chat_richtext import RichMessages

PLUGIN = base.PLUGIN
run = base.run

SIGNED = "{notices}{answer}{sources}\n————————\n✦ 小助手 ✦"


class _Caps:
    def __init__(self, *names: str) -> None:
        self.names_ = set(names)

    def has(self, name: str, min_version: int = 1) -> bool:
        return name in self.names_


class _Boom:
    def has(self, name: str, min_version: int = 1) -> bool:
        raise RuntimeError("probe failed")


def _rich_ctx(template: str, *, caps=None, text: str = "第一段\n\n第二段"):
    ctx = base.live_ctx(
        config={"answer_template": template, "thinking_placeholder": False},
        ai=base.CapAI(text=text),
        messages=RichMessages(),
    )
    if caps is not None:
        ctx.capabilities = caps
    return ctx


def _markdown(ctx) -> str:
    plugin = PLUGIN.InteractionAIChatPlugin()
    run(plugin.on_event(ctx, base.message_payload("问", chat_id=601, chat_type="private")))
    return ctx.messages.applied[-1]["rich_message"]["markdown"]


class HelperTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_rule_line_detection(self) -> None:
        for line in ("————————", "━━━━", "---", "\\_\\_\\_", "＝＝＝", "- - -"):
            self.assertTrue(PLUGIN.is_rule_line(line), line)
        for line in ("--", "答案", "- 列表项", "✦ 小助手 ✦"):
            self.assertFalse(PLUGIN.is_rule_line(line), line)

    def test_template_lines_keep_single_newlines(self) -> None:
        out = PLUGIN.rich_template_lines("第一行\n第二行\n\n另一段", dividers=False)
        self.assertEqual(out, "第一行<br>第二行\n\n另一段")

    def test_template_lines_keep_lists_and_convert_rules(self) -> None:
        out = PLUGIN.rich_template_lines("**来源**\n1. a\n2. b\n————\n署名", dividers=True)
        self.assertEqual(out, "**来源**\n\n1. a\n2. b\n\n---\n\n署名")
        kept = PLUGIN.rich_template_lines("————\n署名", dividers=False)
        self.assertEqual(kept, "————<br>署名")

    def test_host_probe(self) -> None:
        self.assertTrue(PLUGIN.host_supports_layout(type("C", (), {"capabilities": _Caps("messages.rich_divider")})()))
        self.assertFalse(PLUGIN.host_supports_layout(type("C", (), {"capabilities": _Caps()})()))
        self.assertFalse(PLUGIN.host_supports_layout(type("C", (), {"capabilities": _Boom()})()))
        self.assertFalse(PLUGIN.host_supports_layout(object()))


class RichLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_new_host_layout_with_signature(self) -> None:
        markdown = _markdown(_rich_ctx(SIGNED, caps=_Caps("messages.rich_divider")))
        self.assertTrue(markdown.startswith("第一段\n\n第二段\n\n---\n\n✦ 小助手 ✦"), markdown)
        self.assertEqual(markdown.count("---"), 1)  # 模板已有横线：换成原生分割线，不重复补

    def test_new_host_adds_dividers_around_answer(self) -> None:
        markdown = _markdown(_rich_ctx("<b>{user}</b> 问：\n{answer}\n署名", caps=_Caps("messages.rich_divider")))
        head, _, rest = markdown.partition("\n\n---\n\n")
        self.assertIn("问：", head)
        self.assertTrue(rest.startswith("第一段\n\n第二段\n\n---\n\n署名"), markdown)

    def test_answer_on_same_line_gets_no_divider(self) -> None:
        markdown = _markdown(_rich_ctx("答：{answer}", caps=_Caps("messages.rich_divider"), text="好"))
        self.assertNotIn("---", markdown)

    def test_old_host_keeps_newlines_without_dividers(self) -> None:
        markdown = _markdown(_rich_ctx(SIGNED))
        self.assertNotIn("\n---\n", markdown)
        self.assertIn("————————<br>✦ 小助手 ✦", markdown)


class HtmlLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_compose_text_layout(self) -> None:
        out = PLUGIN.compose_text_layout("<b>问</b>\n", "答案", "\n————\n署名")
        self.assertEqual(out, f"<b>问</b>\n{PLUGIN.TEXT_DIVIDER}\n答案\n{PLUGIN.TEXT_DIVIDER}\n署名")
        self.assertEqual(PLUGIN.compose_text_layout("答：", "好", ""), "答：好")

    def test_html_reply_uses_text_divider_on_new_host(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        trigger = PLUGIN._Trigger(kind="mention", chat_id=1, chat_type="group", user_id=2, display_name="张三", text="问")
        cfg = PLUGIN._cfg(base.live_ctx(config={"answer_template": "{answer}\n署名"}))
        result = type("R", (), {"model": "m", "provider_name": "p", "sources": []})()
        new = plugin._format_answer(cfg, trigger, "答案", result, [], layout=True)
        old = plugin._format_answer(cfg, trigger, "答案", result, [], layout=False)
        self.assertEqual(new[0], f"答案\n{PLUGIN.TEXT_DIVIDER}\n署名")
        self.assertEqual(old[0], "答案\n署名")


class PromptTest(unittest.TestCase):
    def test_prompt_asks_for_divider_lines(self) -> None:
        self.assertIn("单独一行 ---", PLUGIN.build_system_prompt({"rich_text": True}))
        self.assertIn("单独一行 ---", PLUGIN.build_system_prompt({"rich_text": False}))


# ---------------------------------------------------------------------------
# 0.5.2：问题引用 + 署名（第 28 轮真机反馈：关键词 / inline 提问缺引用、底部分割线和署名）
# ---------------------------------------------------------------------------
NEW_HOST = ("messages.rich_divider",)


def _group_markdown(ctx, text: str = "阿光 什么是做种") -> str:
    plugin = PLUGIN.InteractionAIChatPlugin()
    run(plugin.on_event(ctx, base.message_payload(text, chat_id=-100777, chat_type="supergroup")))
    return ctx.messages.applied[-1]["rich_message"]["markdown"]


def _keyword_ctx(config: dict | None = None, *, caps=_Caps(*NEW_HOST)):
    cfg = {"thinking_placeholder": False, "trigger_keywords": "阿光"}
    cfg.update(config or {})
    ctx = base.live_ctx(config=cfg, ai=base.CapAI(text="第一段\n\n第二段"), messages=RichMessages())
    if caps is not None:
        ctx.capabilities = caps
    return ctx


class QuoteSignatureTest(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()

    def test_keyword_reply_has_quote_dividers_and_signature(self) -> None:
        markdown = _group_markdown(_keyword_ctx())
        blocks = markdown.split("\n\n")
        self.assertEqual(blocks[0], "> ❓ 什么是做种", markdown)
        self.assertEqual(blocks[1], "---")
        self.assertEqual(blocks[2:4], ["第一段", "第二段"])
        self.assertEqual(blocks[4], "---")
        self.assertTrue(blocks[5].startswith("— "), markdown)

    def test_template_signature_is_not_duplicated(self) -> None:
        markdown = _group_markdown(_keyword_ctx({"answer_template": SIGNED}))
        self.assertIn("✦ 小助手 ✦", markdown)
        self.assertNotIn("— ", markdown)
        self.assertEqual(markdown.count("\n---\n"), 2, markdown)

    def test_switches_off(self) -> None:
        markdown = _group_markdown(_keyword_ctx({"quote_question": False, "signature_template": ""}))
        self.assertTrue(markdown.startswith("第一段"), markdown)
        self.assertNotIn("---", markdown)

    def test_old_host_unchanged(self) -> None:
        markdown = _group_markdown(_keyword_ctx(caps=None))
        self.assertNotIn("❓", markdown)
        self.assertNotIn("— ", markdown)

    def test_private_chat_has_signature_but_no_quote(self) -> None:
        markdown = _markdown(_rich_ctx("{notices}{answer}{sources}", caps=_Caps(*NEW_HOST)))
        self.assertNotIn("❓", markdown)
        self.assertIn("\n\n---\n\n— ", markdown)

    def test_extras_rules(self) -> None:
        cfg = {"quote_question": True, "signature_template": "— {model}"}
        values = {"model": "m", "provider": "p", "user": "u", "sources": "<b>来源</b>"}
        inline_ask = PLUGIN._Trigger(kind="guest", chat_id=None, chat_type="group", user_id=1, display_name="u", text="问", guest_query_id="tpask:abc")
        guest = PLUGIN._Trigger(kind="guest", chat_id=None, chat_type="group", user_id=1, display_name="u", text="问", guest_query_id="g1")
        extras = PLUGIN.InteractionAIChatPlugin._layout_extras
        self.assertEqual(extras(cfg, inline_ask, values, "", ""), ("", "— m"))  # 服务器已加引用
        self.assertEqual(extras(cfg, guest, values, "", "\n<b>来源</b>"), ("问", "— m"))  # 只有来源：仍加署名
        self.assertEqual(extras(cfg, guest, values, "", "\n署名"), ("问", ""))  # 模板自带署名
        self.assertEqual(extras(cfg, guest, values, "答：", ""), ("问", ""))  # 回答和文字同一行
        self.assertEqual(extras(cfg, guest, values, "<blockquote>{q}</blockquote>\n", ""), ("", "— m"))  # 模板自带引用
        self.assertEqual(extras(cfg, guest, {**values, "model": ""}, "", ""), ("问", ""))  # 署名只剩标点

    def test_html_layout_has_quote_and_signature(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        trigger = PLUGIN._Trigger(kind="keyword", chat_id=1, chat_type="group", user_id=2, display_name="张三", text="什么<是>")
        cfg = PLUGIN._cfg(base.live_ctx(config={}))
        result = type("R", (), {"model": "m", "provider_name": "p", "sources": []})()
        out = plugin._format_answer(cfg, trigger, "答案", result, [], layout=True)[0]
        self.assertEqual(
            out,
            f"<blockquote>❓ 什么&lt;是&gt;</blockquote>\n{PLUGIN.TEXT_DIVIDER}\n答案\n{PLUGIN.TEXT_DIVIDER}\n— m",
        )

    def test_bot_reply_preferred_is_silent(self) -> None:
        self.assertIn("bot_reply_preferred", PLUGIN.InteractionAIChatPlugin._inline_via_bot_placeholder.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
