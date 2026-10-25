"""interaction-ai-chat：Telegram 富文本输出审计（Rich Message 优先、HTML 兜底）。

对照 https://core.telegram.org/bots/api#formatting-options （HTML style / MessageEntity）
与 Rich Message Formatting Options 断言插件真正发出去的 HTML / Rich Markdown，区分
“实体没发出去”（插件问题）和“客户端字体不显示”（例如中文斜体、中文行内代码在 iOS 上
看起来和正文差不多，属于客户端渲染）。
"""

from __future__ import annotations

import re
import unittest

import test_interaction_ai_chat as base

PLUGIN = base.PLUGIN
TH = base.TH
run = base.run
CapAI = base.CapAI
message_payload = base.message_payload
guest_payload = base.guest_payload

# Telegram Bot API HTML style 官方支持的标签（https://core.telegram.org/bots/api#html-style）。
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "a", "tg-emoji", "tg-time", "code", "pre", "blockquote"}


def assert_valid_telegram_html(case: unittest.TestCase, value: str) -> None:
    """只用官方标签，且严格嵌套（交叉/未闭合会让 Telegram 以 can't parse entities 拒绝整条）。"""

    stack: list[str] = []
    for match in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^>]*>", value):
        closing, name = match.group(1), match.group(2).lower()
        case.assertIn(name, ALLOWED_TAGS, f"非官方标签 <{name}>：{value!r}")
        if closing:
            case.assertTrue(stack and stack[-1] == name, f"标签嵌套错误 </{name}>：{value!r}")
            stack.pop()
        else:
            stack.append(name)
    case.assertEqual(stack, [], f"未闭合标签：{value!r}")
    case.assertNotRegex(re.sub(r"<[^>]+>", "", value), r"[<>]", "正文里出现未转义的尖括号")


class RichMessages(base.LiveMessages):
    """支持 Rich Message 的交互入口 ctx.messages（平台 0.67.1+ 才有 send_rich/edit_rich）。"""

    def __init__(self, *, rich_result=None, rich_raises: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rich_result = rich_result if rich_result is not None else {"ok": True, "executed": 1, "failed": 0}
        self.rich_raises = rich_raises

    async def send_rich(self, **kwargs):  # pragma: no cover - 插件用 apply 实时执行，不走缓冲 helper
        raise AssertionError("应通过 apply 实时执行 rich 动作")

    async def apply(self, actions, *, entry_key=None):
        if any("rich_message" in action for action in actions):
            self.applied.extend(dict(action) for action in actions)
            if self.rich_raises:
                raise RuntimeError("rich down")
            return dict(self.rich_result)
        return await super().apply(actions, entry_key=entry_key)


SAMPLE = """## 富文本示例


这是**粗体**，这是*斜体示例*，这是`行内代码`，<u>下划线</u>，~~删除线~~，||剧透||。



- 列表一

- 列表二
- [x] 已完成
- [ ] 待办


> 引用第一行
> 引用第二行


```python
print("<b>")
```


| 名称 | 价格 | 备注 |
|:---|---:|---|
| 苹果 | 3 | 新鲜 |
| 香蕉 | 2 |  |


---
结尾段落，[链接](https://example.com/?a=1&b=2)。
"""


class HtmlAuditTest(base._StubbedTestCase):
    def test_cjk_adjacent_italic_is_emitted(self) -> None:
        # 旧实现用 \\w 当边界，汉字也算 \\w，导致“这是*斜体*”整段不转换（实体根本没发出去）。
        self.assertEqual(TH.markdown_to_telegram_html("这是*斜体示例*。"), "这是<i>斜体示例</i>。")
        self.assertEqual(TH.markdown_to_telegram_html("（_斜体_）"), "（<i>斜体</i>）")
        # 安全边界保持：乘法、snake_case、中文词内下划线（GFM 规则）不算斜体。
        self.assertEqual(TH.markdown_to_telegram_html("2*3*4 snake_case_name 报告_v1_终版"), "2*3*4 snake_case_name 报告_v1_终版")

    def test_chinese_inline_code_entity_is_sent(self) -> None:
        # 实体已发出：iOS 上中文没有等宽字形，<code> 里的汉字看起来和正文几乎一样，这是客户端渲染限制。
        self.assertEqual(TH.markdown_to_telegram_html("这是`行内代码`。"), "这是<code>行内代码</code>。")

    def test_underline_spoiler_mark_and_inline_html(self) -> None:
        out = TH.markdown_to_telegram_html("<u>下</u> <ins>插</ins> ==标记== ||藏|| ~~删~~ <b>粗</b> <sup>2</sup> <br> x")
        self.assertEqual(out, "<u>下</u> <u>插</u> <u>标记</u> <tg-spoiler>藏</tg-spoiler> <s>删</s> <b>粗</b> 2 \n x")

    def test_code_block_language_and_escaping(self) -> None:
        out = TH.markdown_to_telegram_html("```js\nif (a < b && c) {}\n```\n```\n\n```")
        self.assertEqual(out, '<pre><code class="language-js">if (a &lt; b &amp;&amp; c) {}</code></pre>')

    def test_blank_lines_are_compressed_around_blocks(self) -> None:
        out = TH.markdown_to_telegram_html(SAMPLE)
        self.assertNotIn("\n\n\n", out)
        # 标题后、块元素（引用、代码块、表格、分割线）前后都只有一个换行。
        self.assertTrue(out.startswith("<b>富文本示例</b>\n这是"))
        for marker in ("<blockquote>", "</blockquote>", "<pre>", "</pre>", "──────────"):
            index = out.index(marker)
            self.assertNotEqual(out[max(0, index - 2) : index], "\n\n", marker)
            end = index + len(marker)
            self.assertNotEqual(out[end : end + 2], "\n\n", marker)
        # 段落与列表之间保留一个空行；同一列表的松散项之间不空行。
        self.assertIn("。\n\n• 列表一\n• 列表二\n☑️ 已完成\n☐ 待办", out)
        assert_valid_telegram_html(self, out)

    def test_table_becomes_row_list(self) -> None:
        out = TH.markdown_to_telegram_html(SAMPLE)
        self.assertIn("• <b>苹果</b> — 价格：3；备注：新鲜\n• <b>香蕉</b> — 价格：2", out)
        self.assertNotIn("|", out)

    def test_long_quote_and_details_are_expandable(self) -> None:
        quote = "\n".join(f"> 第{i}行" for i in range(12))
        self.assertTrue(TH.markdown_to_telegram_html(quote).startswith("<blockquote expandable>第0行"))
        out = TH.markdown_to_telegram_html("<details><summary>更多</summary>\n隐藏一\n隐藏二\n</details>")
        self.assertEqual(out, "<b>更多</b>\n<blockquote expandable>隐藏一\n隐藏二</blockquote>")

    def test_crossing_markup_is_rebalanced(self) -> None:
        out = TH.markdown_to_telegram_html("**粗 *斜** 体* ~~a **b~~ c**")
        assert_valid_telegram_html(self, out)
        self.assertEqual(TH.balance_tags("<b>a<i>b</b>c</i>"), "<b>a<i>b</i></b><i>c</i>")
        self.assertEqual(TH.balance_tags("x</b><i>y"), "x<i>y</i>")

    def test_links_images_and_footnotes(self) -> None:
        out = TH.markdown_to_telegram_html("[看 `代码`](https://e.com) ![图](https://e.com/a.png) ![x](tg://emoji?id=1) 引用[^1]")
        self.assertEqual(out, '<a href="https://e.com">看 代码</a> <a href="https://e.com/a.png">图</a> x 引用[1]')

    def test_heading_only_and_raw_html_stay_safe(self) -> None:
        out = TH.markdown_to_telegram_html("### 标题\n<script>alert(1)</script> <tg-button>x</tg-button> a < b > c")
        self.assertEqual(out, "<b>标题</b>\n&lt;script&gt;alert(1)&lt;/script&gt; &lt;tg-button&gt;x&lt;/tg-button&gt; a &lt; b &gt; c")

    def test_sample_and_split_segments_are_valid_telegram_html(self) -> None:
        out = TH.markdown_to_telegram_html(SAMPLE * 40)
        assert_valid_telegram_html(self, out)
        parts = TH.split_html(out, 4000)
        self.assertGreater(len(parts), 1)
        for part in parts:
            assert_valid_telegram_html(self, part)
            self.assertLessEqual(TH.visible_length(part), 4000)

    def test_template_supports_tg_time(self) -> None:
        out = TH.sanitize_template_html('<tg-time unix="1647531900" format="wDT">明天</tg-time><tg-time unix="x">坏</tg-time>')
        self.assertEqual(out, '<tg-time unix="1647531900" format="wDT">明天</tg-time>&lt;tg-time unix="x"&gt;坏&lt;/tg-time&gt;')


class RichMarkdownTest(base._StubbedTestCase):
    def test_native_structures_are_kept(self) -> None:
        out = TH.markdown_to_rich_markdown(SAMPLE + "\n$$E = mc^2$$\n<details open><summary>更多</summary>\n内容\n</details>")
        for fragment in ("## 富文本示例", "| 名称 | 价格 | 备注 |", "- [x] 已完成", "> 引用第一行", '```python\nprint("<b>")\n```', "$$E = mc^2$$", "<details open><summary>更多</summary>", "<u>下划线</u>", "||剧透||"):
            self.assertIn(fragment, out)
        self.assertNotIn("\n\n\n", out)

    def test_unsafe_html_media_and_links_are_neutralized(self) -> None:
        out = TH.markdown_to_rich_markdown(
            '<tg-button type="callback_data" data="x">点</tg-button> <img src="https://e.com/a.png"> <script>1</script> '
            '<details onclick="x">d</details> <!-- c --> ![图](https://e.com/a.png) ![t](tg://time?unix=1) '
            "[坏](javascript:alert) [人](tg://user?id=1) [好](https://e.com) <https://e.com/x> `<tg-map/>`"
        )
        self.assertEqual(
            out,
            '&lt;tg-button type="callback_data" data="x">点&lt;/tg-button> &lt;img src="https://e.com/a.png"> &lt;script>1&lt;/script> '
            '&lt;details onclick="x">d</details> &lt;!-- c --> [图](https://e.com/a.png) t '
            "坏 人 [好](https://e.com) <https://e.com/x> `<tg-map/>`",
        )

    def test_html_fragment_text_is_escaped_for_markdown(self) -> None:
        self.assertEqual(
            TH.html_to_rich_markdown('<i>注意 *不* 是_强调_ #1 | a</i> <a href="https://e.com/a_b">t_x</a>'),
            '<i>注意 \\*不\\* 是\\_强调\\_ \\#1 \\| a</i> <a href="https://e.com/a_b">t\\_x</a>',
        )


class RichDeliveryTest(base._StubbedTestCase):
    def test_private_answer_edits_placeholder_with_rich_markdown(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = base.live_ctx(ai=CapAI(text="## 标题\n| a | b |\n|---|---|\n| 1 | 2 |"), messages=RichMessages())
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions, [])
        placeholder, edit = ctx.messages.applied
        self.assertEqual(placeholder["type"], "send_message")
        self.assertEqual(
            edit,
            {
                "type": "edit_message",
                "chat_id": 601,
                "message_id": 901,
                "rich_message": {"markdown": "## 标题\n| a | b |\n|---|---|\n| 1 | 2 |"},
                "send_via": ["interaction_bot"],
            },
        )

    def test_rejected_rich_edit_falls_back_to_html(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        messages = RichMessages(rich_result={"ok": False, "executed": 0, "failed": 1})
        ctx = base.live_ctx(ai=CapAI(text="**答** *斜*"), messages=messages)
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "edit_message")
        self.assertEqual(actions[0]["text"], "<b>答</b> <i>斜</i>")
        self.assertEqual(actions[0]["parse_mode"], "html")
        self.assertTrue(actions[0]["send_on_edit_failure"])
        self.assertTrue(any("Rich Message" in item[1] for item in ctx.logs))

    def test_rich_exception_falls_back_to_html(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = base.live_ctx(ai=CapAI(text="答"), messages=RichMessages(rich_raises=True))
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions[0]["text"], "答")

    def test_without_placeholder_sends_rich_reply_in_group(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = base.live_ctx(config={"thinking_placeholder": False, "bot_username": "helper_bot"}, ai=CapAI(text="- a\n- b"), messages=RichMessages())
        actions = run(plugin.on_event(ctx, message_payload("@helper_bot 问")))
        self.assertEqual(actions, [])
        self.assertEqual(
            ctx.messages.applied,
            [
                {
                    "type": "send_rich_message",
                    "chat_id": -100777,
                    "rich_message": {"markdown": "- a\n- b"},
                    "send_via": ["interaction_bot"],
                    "reply_to_message_id": 88,
                }
            ],
        )

    def test_guest_placeholder_uses_html_inline_edit(self) -> None:
        # 平台 inline 消息编辑（Guest Mode 占位）只支持 text/parse_mode，没有 rich_message。
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = base.live_ctx(ai=CapAI(text="## 标题\n*斜*"), messages=RichMessages())
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual([a["type"] for a in ctx.messages.applied], ["answer_guest_query"])
        self.assertEqual(
            actions,
            [{"type": "edit_message", "inline_message_id": "inline-abc", "text": "<b>标题</b>\n<i>斜</i>", "parse_mode": "html", "send_via": ["interaction_bot"]}],
        )

    def test_guest_without_placeholder_answers_with_rich_message(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = base.live_ctx(config={"thinking_placeholder": False}, ai=CapAI(text="**答**"), messages=RichMessages())
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(actions, [])
        answer = ctx.messages.applied[0]
        self.assertEqual(answer["type"], "answer_guest_query")
        self.assertEqual(answer["rich_message"], {"markdown": "**答**"})
        self.assertEqual(answer["text"], "答")

    def test_guest_rich_rejected_falls_back_to_html_answer(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        messages = RichMessages(rich_result={"ok": False, "failed": 1})
        ctx = base.live_ctx(config={"thinking_placeholder": False}, ai=CapAI(text="**答**"), messages=messages)
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(actions[0]["type"], "answer_guest_query")
        self.assertEqual(actions[0]["text"], "<b>答</b>")
        self.assertEqual(actions[0]["parse_mode"], "html")

    def test_errors_and_plain_mode_do_not_use_rich(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = base.live_ctx(ai=CapAI(exc=RuntimeError("boom")), messages=RichMessages())
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions[0]["text"], "AI 暂时不可用，请稍后再试。")
        self.assertEqual(len(ctx.messages.applied), 1)
        ctx = base.live_ctx(config={"rich_text": False}, ai=CapAI(text="**答**"), messages=RichMessages())
        actions = run(plugin.on_event(ctx, message_payload("问", chat_id=601, chat_type="private")))
        self.assertEqual(actions[0]["text"], "**答**")
        self.assertEqual(actions[0]["parse_mode"], "plain")

    def test_rich_reply_wraps_template_notices_and_sources(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ai = CapAI(text="答案 *重点*", unsupported={"web_search"}, sources=[{"url": "https://s.com/a_b", "title": "来源_一"}])
        ctx = base.live_ctx(
            config={"enable_web_search": True, "answer_template": "<b>{user}</b> 问：\n{notices}{answer}{sources}"},
            ai=ai,
            messages=RichMessages(),
        )
        payload = message_payload("问", chat_id=601, chat_type="private")
        payload["sender"]["display_name"] = "张_三*"
        run(plugin.on_event(ctx, payload))
        markdown = ctx.messages.applied[-1]["rich_message"]["markdown"]
        self.assertTrue(markdown.startswith("<b>张\\_三\\*</b> 问：\n<i>当前模型不支持联网搜索"))
        self.assertIn("\n\n答案 *重点*", markdown)
        self.assertNotIn("\n\n\n", markdown)

    def test_guest_html_source_length_is_fitted(self) -> None:
        # 访客回复按源码长度（含标签）判断 4096：超出时平台会把整条退成纯文本。
        plugin = PLUGIN.InteractionAIChatPlugin()
        text = " ".join("**粗**" for _ in range(900))
        ctx = base.live_ctx(config={"thinking_placeholder": False, "max_output_chars": 9000}, ai=CapAI(text=text))
        actions = run(plugin.on_event(ctx, guest_payload("问")))
        self.assertEqual(actions[0]["type"], "answer_guest_query")
        self.assertLessEqual(len(actions[0]["text"]), 4096)
        assert_valid_telegram_html(self, actions[0]["text"])


class PromptTest(base._StubbedTestCase):
    def test_system_prompt_says_output_is_rich_text(self) -> None:
        prompt = PLUGIN.build_system_prompt(PLUGIN._cfg(base.Ctx()))
        for fragment in ("Telegram 富文本", "不要声称自己只能发纯文字", "*斜体*（用单个星号）", "<u>下划线</u>", "||剧透||", "Markdown 表格", "## 小标题", "<details>", "段落之间最多空一行"):
            self.assertIn(fragment, prompt)
        self.assertNotIn("不要输出 HTML 和表格", prompt)
        self.assertNotIn("只用文字回答", prompt)

    def test_plain_mode_prompt_is_unchanged(self) -> None:
        prompt = PLUGIN.build_system_prompt(PLUGIN._cfg(base.Ctx(config={"rich_text": False})))
        self.assertIn("使用纯文本回答", prompt)


if __name__ == "__main__":
    unittest.main()
