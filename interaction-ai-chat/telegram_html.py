"""把模型输出的 Markdown 安全转换成 Telegram 富文本，并按上限分段。

两条输出路径（插件优先第一条，失败再退第二条，平台最后兜底纯文本）：

- :func:`markdown_to_rich_markdown`：Bot API 10.2+ 原生 Rich Message（``InputRichMessage.markdown``），
  标题、列表、任务列表、表格、折叠详情、公式都由 Telegram 原生渲染；
- :func:`markdown_to_telegram_html`：普通消息 ``parse_mode=HTML``，对照
  https://core.telegram.org/bots/api#html-style 只使用官方列出的标签。

HTML 路径的设计原则：

- 先整体转义 ``& < >``，模型自己输出的任何 HTML 标签都会变成可见文本，不会被
  Telegram 解析；只有本模块生成的标签（``b/i/s/u/code/pre/a/blockquote/tg-spoiler``）
  才是真标签。
- 代码块 / 行内代码 / 链接先抽成占位符，避免内部再被粗体、斜体规则误伤。
- 链接只接受 ``http(s)://`` 与 ``tg://``；其它协议保留为普通文本。
- :func:`split_html` 按标签栈切分超长 HTML：切点优先落在换行/空格，被切断的标签在
  本段末尾补闭合、下一段开头重新打开，保证每段都是合法 Telegram HTML。
- 输出经 :func:`balance_tags` 修正嵌套，交叉/未闭合标签不会让 Telegram 拒绝整条消息。
- 标题转粗体、列表转 “• ”、表格转逐行列表、``<details>`` 转可折叠引用；块元素前后只留一个换行。
- :func:`sanitize_template_html` 用于用户自定义模板：保留 Telegram 支持的标签与
  安全属性，其余尖括号与裸 ``&`` 一律转义，并补齐/丢弃不配对的标签。
"""

from __future__ import annotations

import html
import re

TELEGRAM_TEXT_LIMIT = 4096

_PH = "\u0000{}\u0000"
_PH_RE = re.compile("\u0000(\\d+)\u0000")

_FENCE_RE = re.compile(r"(?m)^[ \t]*(```|~~~)[ \t]*([\w+#.-]*)[^\n]*\n([\s\S]*?)(?:\n[ \t]*\1[ \t]*(?=\n|$)|\Z)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_DISPLAY_MATH_RE = re.compile(r"\$\$([^$\n][^$]*?)\$\$")
_DETAILS_RE = re.compile(
    r"<details(?:\s+open)?\s*>\s*(?:<summary>([\s\S]*?)</summary>)?([\s\S]*?)</details>",
    re.IGNORECASE,
)
_IMAGE_RE = re.compile(r'!\[([^\]\n]*)\]\(\s*([^)\s]+)(?:\s+"[^\n]*?")?\s*\)')
_LINK_RE = re.compile(r'\[([^\]\n]+)\]\(\s*([^)\s]+)(?:\s+"[^\n]*?")?\s*\)')
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]{1,32})\]")
_AUTO_LINK_RE = re.compile(r"&lt;(https?://[^\s&]+)&gt;")
_BOLD_RE = re.compile(r"\*\*(?=\S)([\s\S]+?)(?<=\S)\*\*|__(?=\S)([\s\S]+?)(?<=\S)__")
# 单星号斜体：只拿 ASCII 字母数字当“词内”边界（``2*3*4`` 不算斜体），中文紧挨着也能识别
# （``这是*斜体*。``）；Python 的 ``\w`` 会把汉字当成单词字符，旧实现因此漏掉中文斜体。
_ITALIC_STAR_RE = re.compile(r"(?<![*\\A-Za-z0-9])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*\\])\*(?![*A-Za-z0-9])")
# 下划线斜体沿用 GFM：前后紧挨字母（含汉字）时不算强调，避免 ``snake_case``、``报告_v1_终版`` 误伤。
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?=\S)([^\n]+?)(?<=\S)~~")
_SPOILER_RE = re.compile(r"\|\|(?=\S)([^\n]+?)(?<=\S)\|\|")
_MARK_RE = re.compile(r"==(?=\S)([^\n=]+?)(?<=\S)==")
# 模型写的少量行内 HTML（转义后形态）：Telegram HTML 支持的直接还原，其它（sub/sup）保留文字。
_INLINE_HTML_RE = re.compile(
    r"&lt;(u|ins|b|strong|i|em|s|del|strike|tg-spoiler|mark|sub|sup)&gt;(.*?)&lt;/\1&gt;",
    re.IGNORECASE,
)
_INLINE_HTML_MAP = {
    "u": "u", "ins": "u", "mark": "u",
    "b": "b", "strong": "b",
    "i": "i", "em": "i",
    "s": "s", "del": "s", "strike": "s",
    "tg-spoiler": "tg-spoiler",
}
_BR_RE = re.compile(r"&lt;br\s*/?&gt;", re.IGNORECASE)
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t#]*$")
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+(?:\[([ xX])\][ \t]+)?(.*)$")
_ORDERED_RE = re.compile(r"^([ \t]*)(\d{1,3})[.)][ \t]+(.*)$")
_HR_RE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SAFE_URL_RE = re.compile(r"^(?:https?://|tg://)[^\s<>\"']+$", re.IGNORECASE)
_EXPANDABLE_MARK = "\u0002"
# 引用超过这么多行时改为可折叠引用（<blockquote expandable>），避免长引用把气泡撑满。
EXPANDABLE_QUOTE_LINES = 8


def escape(text: str) -> str:
    """Telegram HTML 转义（只需要 ``& < >``，属性值另外转义引号）。"""

    return html.escape(str(text or ""), quote=False)


def _attr(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def safe_url(url: str) -> str | None:
    value = html.unescape(str(url or "").strip())
    return value if _SAFE_URL_RE.match(value) else None


def markdown_to_telegram_html(text: str) -> str:
    """把常见 Markdown 转成 Telegram HTML（parse_mode=HTML）；转换不了的语法保留为转义后的原文。

    Telegram 普通消息没有标题、列表、表格实体：标题转粗体，列表转 “• ” 文本，表格转
    “每行一条” 的列表，``<details>`` 转粗体摘要 + 可折叠引用。输出保证标签正确嵌套。
    """

    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return ""
    store: list[str] = []
    blocks: set[str] = set()

    def keep(fragment: str, *, block: bool = False) -> str:
        store.append(fragment)
        token = _PH.format(len(store) - 1)
        if block:
            blocks.add(token)
        return token

    def fence(match: re.Match[str]) -> str:
        lang = re.sub(r"[^\w+#.-]", "", match.group(2) or "")[:32]
        code = (match.group(3) or "").rstrip()
        if not code.strip():
            return "\n"
        cls = f' class="language-{_attr(lang)}"' if lang else ""
        return "\n" + keep(f"<pre><code{cls}>{escape(code)}</code></pre>", block=True) + "\n"

    def details(match: re.Match[str]) -> str:
        summary = (match.group(1) or "").strip() or "详情"
        body = [line for line in (match.group(2) or "").strip().split("\n")]
        quoted = "\n".join(_EXPANDABLE_MARK + line for line in body) if any(line.strip() for line in body) else ""
        return f"\n**{summary}**\n{quoted}\n"

    work = _FENCE_RE.sub(fence, source)
    work = _DETAILS_RE.sub(details, work)
    work = _INLINE_CODE_RE.sub(lambda m: keep(f"<code>{escape(m.group(1))}</code>"), work)
    work = _DISPLAY_MATH_RE.sub(lambda m: keep(f"<code>{escape(m.group(1).strip())}</code>"), work)
    work = escape(work)

    def image(match: re.Match[str]) -> str:
        label, url = match.group(1).strip(), safe_url(match.group(2))
        if not url or not url.lower().startswith("http"):
            return label
        return keep(f'<a href="{_attr(url)}">{_inline(label) or "图片"}</a>')

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), safe_url(match.group(2))
        if not url:
            return match.group(0)
        # 链接与 code/pre 实体不能互相包含：链接文字里的行内代码降级为普通文字。
        label = _PH_RE.sub(lambda m: html_to_plain_escaped(store[int(m.group(1))]), label)
        return keep(f'<a href="{_attr(url)}">{_inline(label)}</a>')

    work = _IMAGE_RE.sub(image, work)
    work = _LINK_RE.sub(link, work)
    work = _FOOTNOTE_REF_RE.sub(r"[\1]", work)
    work = _AUTO_LINK_RE.sub(
        lambda m: keep(f'<a href="{_attr(html.unescape(m.group(1)))}">{m.group(1)}</a>') if safe_url(m.group(1)) else m.group(0),
        work,
    )
    work = _blocks(work, blocks)
    for _ in range(3):  # 占位符可能嵌套（链接文字里的格式）
        if not _PH_RE.search(work):
            break
        work = _PH_RE.sub(lambda m: store[int(m.group(1))], work)
    return balance_tags(work).strip()


def html_to_plain_escaped(fragment: str) -> str:
    """去掉标签但保留 HTML 转义（用于把片段降级成普通文字再嵌回 HTML）。"""

    return re.sub(r"<[^>]+>", "", fragment)


def _inline(text: str) -> str:
    out = _BR_RE.sub("\n", text)
    out = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", out)
    out = _STRIKE_RE.sub(r"<s>\1</s>", out)
    out = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", out)
    out = _MARK_RE.sub(r"<u>\1</u>", out)
    out = _ITALIC_STAR_RE.sub(r"<i>\1</i>", out)
    out = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", out)
    for _ in range(3):  # 允许 <u><b>x</b></u> 这类嵌套
        replaced = _INLINE_HTML_RE.sub(_inline_html, out)
        if replaced == out:
            break
        out = replaced
    return out


def _inline_html(match: re.Match[str]) -> str:
    tag = _INLINE_HTML_MAP.get(match.group(1).lower())
    inner = match.group(2)
    return f"<{tag}>{inner}</{tag}>" if tag else inner


def _table_rows(rows: list[str]) -> list[list[str]]:
    parsed: list[list[str]] = []
    for row in rows:
        value = row.strip()
        if value.startswith("|"):
            value = value[1:]
        if value.endswith("|"):
            value = value[:-1]
        parsed.append([cell.strip() for cell in value.split("|")])
    return parsed


def _table_to_lines(table: list[str]) -> list[str]:
    """Telegram 普通消息没有表格：表头 + 每行转成 “• 首列 — 列名：值；…”。"""

    rows = [row for row in table if not _TABLE_SEP_RE.match(row)]
    cells = _table_rows(rows)
    if not cells:
        return []
    header, body = cells[0], cells[1:]
    if not body:
        return ["• " + "｜".join(_inline(cell) for cell in header if cell)]
    lines: list[str] = []
    for row in body:
        first = _inline(row[0]) if row else ""
        rest = []
        for index, cell in enumerate(row[1:], start=1):
            if not cell:
                continue
            name = header[index] if index < len(header) else ""
            rest.append(f"{_inline(name)}：{_inline(cell)}" if name else _inline(cell))
        head = f"<b>{first}</b>" if first else ""
        lines.append("• " + head + (" — " if head and rest else "") + "；".join(rest))
    return lines


# 输出块的种类：段落/列表之间保留源文本里的一个空行；标题、引用、代码块、表格、分割线前后只留一个换行。
_LOOSE_KINDS = {"para", "list"}


def _blocks(text: str, block_tokens: set[str] | None = None) -> str:
    block_tokens = block_tokens or set()
    items: list[tuple[str, str]] = []  # (kind, html)；kind == "blank" 表示源文本空行
    quote: list[str] = []
    quote_expandable = False
    table: list[str] = []

    def flush_quote() -> None:
        nonlocal quote_expandable
        if not quote:
            return
        lines: list[str] = []
        for item in quote:
            if not item.strip() and (not lines or not lines[-1].strip()):
                continue  # 引用内部连续空行压成一个
            lines.append(_inline(item))
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            expandable = quote_expandable or len(lines) > EXPANDABLE_QUOTE_LINES
            tag = "<blockquote expandable>" if expandable else "<blockquote>"
            items.append(("quote", tag + "\n".join(lines) + "</blockquote>"))
        quote.clear()
        quote_expandable = False

    def flush_table() -> None:
        if not table:
            return
        if len(table) >= 2 and any(_TABLE_SEP_RE.match(row) for row in table):
            lines = _table_to_lines(table)
            if lines:
                items.append(("table", "\n".join(lines)))
        else:
            items.extend(("para", _inline(row)) for row in table)
        table.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(_EXPANDABLE_MARK):
            flush_table()
            quote_expandable = True
            content = stripped[1:].strip()
            if content.startswith("&gt;"):
                content = content[4:].lstrip()
            quote.append(content)
            continue
        if stripped.startswith("&gt;"):
            flush_table()
            content = stripped[4:]
            quote.append(content[1:] if content.startswith(" ") else content)
            continue
        flush_quote()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            table.append(line)
            continue
        flush_table()
        if not stripped:
            items.append(("blank", ""))
            continue
        if stripped in block_tokens:
            items.append(("block", stripped))
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            items.append(("heading", f"<b>{_inline(heading.group(1))}</b>"))
            continue
        if _HR_RE.match(line):
            items.append(("hr", "──────────"))
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            indent = "  " * (len(bullet.group(1).expandtabs(2)) // 2)
            task = bullet.group(2)
            marker = "• " if task is None else ("☑️ " if task.lower() == "x" else "☐ ")
            items.append(("list", f"{indent}{marker}{_inline(bullet.group(3))}"))
            continue
        ordered = _ORDERED_RE.match(line)
        if ordered:
            indent = "  " * (len(ordered.group(1).expandtabs(2)) // 2)
            items.append(("list", f"{indent}{ordered.group(2)}. {_inline(ordered.group(3))}"))
            continue
        items.append(("para", _inline(line)))
    flush_quote()
    flush_table()
    return _join_blocks(items)


def _join_blocks(items: list[tuple[str, str]]) -> str:
    out: list[str] = []
    prev_kind: str | None = None
    pending_blank = False
    for kind, value in items:
        if kind == "blank":
            pending_blank = prev_kind is not None
            continue
        if prev_kind is not None:
            keep_blank = pending_blank and (
                (prev_kind in _LOOSE_KINDS and kind in _LOOSE_KINDS and not (prev_kind == kind == "list"))
                or (kind == "heading" and prev_kind != "heading")
            )
            out.append("\n\n" if keep_blank else "\n")
        out.append(value)
        prev_kind = kind
        pending_blank = False
    return "".join(out)


_BALANCE_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^<>]*)?>")


def balance_tags(text: str) -> str:
    """保证 Telegram HTML 标签正确嵌套：交叉的标签拆开重开，孤立的闭标签丢弃，未闭合的补齐。

    Telegram 遇到交叉或未闭合的标签会以 “can't parse entities” 拒绝整条消息，平台随之
    把整条回答退成纯文本；这里在发送前修好，避免因为一处格式让所有格式失效。
    """

    out: list[str] = []
    stack: list[tuple[str, str]] = []
    pos = 0
    for match in _BALANCE_TAG_RE.finditer(text):
        out.append(text[pos : match.start()])
        pos = match.end()
        closing, name = match.group(1), match.group(2).lower()
        if not closing:
            out.append(match.group(0))
            stack.append((name, match.group(0)))
            continue
        if not any(item[0] == name for item in stack):
            continue  # 孤立闭标签
        reopen: list[tuple[str, str]] = []
        while stack:
            top = stack.pop()
            out.append(f"</{top[0]}>")
            if top[0] == name:
                break
            reopen.append(top)
        for item in reversed(reopen):
            out.append(item[1])
            stack.append(item)
    out.append(text[pos:])
    while stack:
        out.append(f"</{stack.pop()[0]}>")
    # 拆开重开可能留下空标签对，去掉以免 Telegram 报空实体。
    result = "".join(out)
    for _ in range(3):
        cleaned = re.sub(r"<(b|i|u|s|tg-spoiler|code)>(</\1>)", "", result)
        if cleaned == result:
            break
        result = cleaned
    return result


# ---------------------------------------------------------------------------
# Telegram Rich Markdown（Bot API 10.2+ InputRichMessage.markdown）
# ---------------------------------------------------------------------------

RICH_TEXT_LIMIT = 32768
_RICH_ALLOWED_TAGS = {
    "u", "ins", "sub", "sup", "s", "del", "strike", "b", "strong", "i", "em",
    "tg-spoiler", "mark", "code", "br", "details", "summary",
}
_RICH_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^<>]*)>")
_RICH_SAFE_LINK_RE = re.compile(r"^(?:https?://|mailto:)[^\s<>\"']+$", re.IGNORECASE)
_RICH_AUTOLINK_RE = re.compile(r"^<https?://[^\s<>]+>$")


def markdown_to_rich_markdown(text: str) -> str:
    """把模型 Markdown 清洗成可直接放进 ``InputRichMessage.markdown`` 的内容。

    Rich Markdown 兼容 GFM，原生支持标题、列表、任务列表、表格、引用、代码块、公式、
    ``<details>`` 折叠详情等，所以正文基本原样保留；只做安全收敛：

    - 只保留少量行内/折叠 HTML 标签（u/sub/sup/s/b/i/tg-spoiler/mark/code/br/details/summary），
      其它标签（tg-button、tg-map、img、tg-collage 等）的 ``<`` 一律转义成可见文字；
    - 媒体块 ``![](url)`` 改成普通链接，不让模型往回复里嵌图片/视频/文件或自定义表情；
    - 链接只放行 http(s)/mailto，其它协议（javascript:、tg:// 等）只保留文字；
    - 代码块与行内代码内部不做任何改动。
    """

    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return ""
    store: list[str] = []

    def keep(fragment: str) -> str:
        store.append(fragment)
        return _PH.format(len(store) - 1)

    work = _FENCE_RE.sub(lambda m: keep(m.group(0)), source)
    work = re.sub(r"`[^`\n]+`", lambda m: keep(m.group(0)), work)

    def image(match: re.Match[str]) -> str:
        label, url = match.group(1).strip(), match.group(2)
        if _RICH_SAFE_LINK_RE.match(url) and url.lower().startswith("http"):
            return f"[{label or url}]({url})"
        return label

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        return match.group(0) if _RICH_SAFE_LINK_RE.match(url) else label

    work = _IMAGE_RE.sub(image, work)
    work = _LINK_RE.sub(link, work)

    def tag(match: re.Match[str]) -> str:
        raw = match.group(0)
        if _RICH_AUTOLINK_RE.match(raw):
            return raw
        name = match.group(2).lower()
        attrs = match.group(3).strip().rstrip("/").strip()
        if name in _RICH_ALLOWED_TAGS and (not attrs or (name == "details" and attrs.lower() == "open")):
            return f"<{match.group(1)}{name}{' open' if attrs else ''}{'/' if name == 'br' and not match.group(1) else ''}>"
        return "&lt;" + raw[1:]

    work = _RICH_TAG_RE.sub(tag, work)
    work = work.replace("<!--", "&lt;!--")
    work = re.sub(r"\n{3,}", "\n\n", work)
    for _ in range(3):
        if not _PH_RE.search(work):
            break
        work = _PH_RE.sub(lambda m: store[int(m.group(1))], work)
    return work.strip()


def html_to_rich_markdown(fragment: str) -> str:
    """把插件自己生成/清洗过的 Telegram HTML 片段（模板、来源、提示）嵌进 Rich Markdown。

    Rich Markdown 可以直接内嵌 HTML，但行内标签里的 Markdown 也会被解析；这里只把标签外
    文字里的 Markdown 标点加反斜杠转义，标签原样保留，保证模板文案按字面显示。
    """

    out: list[str] = []
    pos = 0
    for match in re.finditer(r"<[^>]+>", str(fragment or "")):
        out.append(_escape_rich_text(fragment[pos : match.start()]))
        out.append(match.group(0))
        pos = match.end()
    out.append(_escape_rich_text(str(fragment or "")[pos:]))
    return "".join(out)


def _escape_rich_text(text: str) -> str:
    return re.sub(r"([\\`*_\[\]#|~=$>])", r"\\\1", text)


# ---------------------------------------------------------------------------
# 用户模板 HTML 清洗
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^<>]*?)?)\s*(/?)>")
_ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#\d{1,7}|#x[0-9a-fA-F]{1,6});")
_ATTR_RE = re.compile(r"([a-zA-Z-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\"'>]+)")
_ALIASES = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}
_SIMPLE_TAGS = {"b", "i", "u", "s", "tg-spoiler"}


def _normalize_tag(name: str, attrs: str) -> str | None:
    """返回规范化后的开标签；不支持的标签返回 None（调用方转义）。"""

    tag = _ALIASES.get(name, name)
    parsed = {key.lower(): value.strip("\"'") for key, value in _ATTR_RE.findall(attrs or "")}
    if tag in _SIMPLE_TAGS:
        return f"<{tag}>"
    if tag == "span":
        return '<span class="tg-spoiler">' if parsed.get("class") == "tg-spoiler" else None
    if tag == "a":
        url = safe_url(parsed.get("href", ""))
        return f'<a href="{_attr(url)}">' if url else None
    if tag == "code":
        lang = parsed.get("class", "")
        return f'<code class="{_attr(lang)}">' if re.fullmatch(r"language-[\w+#.-]{1,32}", lang) else "<code>"
    if tag == "pre":
        return "<pre>"
    if tag == "blockquote":
        return "<blockquote expandable>" if "expandable" in (attrs or "") else "<blockquote>"
    if tag == "tg-time":
        unix = parsed.get("unix", "")
        fmt = parsed.get("format", "")
        if not unix.isdigit():
            return None
        fmt_attr = f' format="{_attr(fmt)}"' if re.fullmatch(r"r|w?[dD]?[tT]?", fmt or "") and fmt else ""
        return f'<tg-time unix="{unix}"{fmt_attr}>'
    if tag == "tg-emoji":
        emoji_id = parsed.get("emoji-id", "")
        return f'<tg-emoji emoji-id="{_attr(emoji_id)}">' if emoji_id.isdigit() else None
    return None


def sanitize_template_html(template: str) -> str:
    """清洗用户模板：保留 Telegram 支持的标签，其余转义；补齐不配对的标签。"""

    source = str(template or "")
    out: list[str] = []
    stack: list[str] = []
    pos = 0

    def text(chunk: str) -> str:
        pieces: list[str] = []
        last = 0
        for match in _ENTITY_RE.finditer(chunk):
            pieces.append(escape(chunk[last : match.start()]))
            pieces.append(match.group(0))
            last = match.end()
        pieces.append(escape(chunk[last:]))
        return "".join(pieces)

    for match in _TAG_RE.finditer(source):
        out.append(text(source[pos : match.start()]))
        pos = match.end()
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
        tag = _ALIASES.get(name, name)
        if closing:
            if tag in stack:
                while stack:
                    top = stack.pop()
                    out.append(f"</{top}>")
                    if top == tag:
                        break
            else:
                out.append(escape(match.group(0)))
            continue
        normalized = _normalize_tag(name, attrs)
        if normalized is None:
            out.append(escape(match.group(0)))
            continue
        out.append(normalized)
        stack.append(normalized[1:].split(" ", 1)[0].rstrip(">"))
    out.append(text(source[pos:]))
    while stack:
        out.append(f"</{stack.pop()}>")
    return "".join(out)


def html_to_plain(text: str) -> str:
    """去掉标签、反转义实体（发送 HTML 失败时的纯文本兜底）。"""

    stripped = re.sub(r"<br\s*/?>", "\n", str(text or ""), flags=re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", "", stripped)
    return html.unescape(stripped).strip()


def visible_length(text: str) -> int:
    """Telegram 计算的长度是解析实体后的可见文本（按 UTF-16 码元）。"""

    plain = html_to_plain(text)
    return len(plain.encode("utf-16-le")) // 2


# ---------------------------------------------------------------------------
# 分段
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"<[^>]+>|&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);|[^<&]+|[<&]")


def _tag_name(tag: str) -> str:
    return re.sub(r"[</>]", " ", tag).split()[0].lower()


def split_html(text: str, limit: int = 4000) -> list[str]:
    """把 Telegram HTML 切成每段可见长度 ≤ ``limit`` 的片段，保持标签配对。"""

    value = str(text or "").strip()
    if not value:
        return []
    limit = max(50, min(int(limit), TELEGRAM_TEXT_LIMIT))
    if visible_length(value) <= limit:
        return [value]

    chunks: list[str] = []
    open_tags: list[tuple[str, str]] = []  # (name, 原始开标签)
    current: list[str] = []
    visible = 0

    def close_all() -> str:
        return "".join(f"</{name}>" for name, _ in reversed(open_tags))

    def reopen() -> str:
        return "".join(raw for _, raw in open_tags)

    def emit() -> None:
        nonlocal current, visible
        body = "".join(current) + close_all()
        if html_to_plain(body):
            chunks.append(body.strip())
        current = [reopen()]
        visible = 0

    for token in _TOKEN_RE.findall(value):
        if token.startswith("<") and len(token) > 1 and token.endswith(">"):
            name = _tag_name(token)
            if token.startswith("</"):
                for index in range(len(open_tags) - 1, -1, -1):
                    if open_tags[index][0] == name:
                        del open_tags[index]
                        break
            elif not token.endswith("/>"):
                open_tags.append((name, token))
            current.append(token)
            continue
        if token.startswith("&") and token.endswith(";") and len(token) > 1:
            if visible + 1 > limit:
                emit()
            current.append(token)
            visible += 1
            continue
        remaining = token
        while remaining:
            room = limit - visible
            size = len(remaining.encode("utf-16-le")) // 2
            if size <= room:
                current.append(remaining)
                visible += size
                break
            cut = _cut_point(remaining, room)
            if cut <= 0:
                emit()
                continue
            current.append(remaining[:cut].rstrip(" "))
            remaining = remaining[cut:].lstrip(" ")
            emit()
    tail = "".join(current) + close_all()
    if html_to_plain(tail):
        chunks.append(tail.strip())
    return chunks


def _cut_point(text: str, room: int) -> int:
    if room <= 0:
        return 0
    # 按 UTF-16 码元换算可容纳的字符数。
    count = 0
    units = 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > room:
            break
        units += width
        count += 1
    if count >= len(text):
        return len(text)
    window = text[:count]
    for sep in ("\n\n", "\n", "。", "！", "？", ". ", " "):
        index = window.rfind(sep)
        if index >= count // 3:
            return index + len(sep)
    return count


__all__ = [
    "RICH_TEXT_LIMIT",
    "TELEGRAM_TEXT_LIMIT",
    "balance_tags",
    "escape",
    "html_to_rich_markdown",
    "markdown_to_rich_markdown",
    "html_to_plain",
    "markdown_to_telegram_html",
    "safe_url",
    "sanitize_template_html",
    "split_html",
    "visible_length",
]
