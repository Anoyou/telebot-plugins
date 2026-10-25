"""把模型输出的 Markdown 安全转换成 Telegram HTML（parse_mode=HTML），并按上限分段。

设计原则：

- 先整体转义 ``& < >``，模型自己输出的任何 HTML 标签都会变成可见文本，不会被
  Telegram 解析；只有本模块生成的标签（``b/i/s/u/code/pre/a/blockquote/tg-spoiler``）
  才是真标签。
- 代码块 / 行内代码 / 链接先抽成占位符，避免内部再被粗体、斜体规则误伤。
- 链接只接受 ``http(s)://`` 与 ``tg://``；其它协议保留为普通文本。
- :func:`split_html` 按标签栈切分超长 HTML：切点优先落在换行/空格，被切断的标签在
  本段末尾补闭合、下一段开头重新打开，保证每段都是合法 Telegram HTML。
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
_LINK_RE = re.compile(r'\[([^\]\n]+)\]\(\s*([^)\s]+)(?:\s+"[^\n]*?")?\s*\)')
_AUTO_LINK_RE = re.compile(r"&lt;(https?://[^\s&]+)&gt;")
_BOLD_RE = re.compile(r"\*\*(?=\S)([\s\S]+?)(?<=\S)\*\*|__(?=\S)([\s\S]+?)(?<=\S)__")
_ITALIC_STAR_RE = re.compile(r"(?<![\*\w])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\*\w])")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?=\S)([^\n]+?)(?<=\S)~~")
_SPOILER_RE = re.compile(r"\|\|(?=\S)([^\n]+?)(?<=\S)\|\|")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t#]*$")
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+(.*)$")
_HR_RE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SAFE_URL_RE = re.compile(r"^(?:https?://|tg://)[^\s<>\"']+$", re.IGNORECASE)


def escape(text: str) -> str:
    """Telegram HTML 转义（只需要 ``& < >``，属性值另外转义引号）。"""

    return html.escape(str(text or ""), quote=False)


def _attr(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def safe_url(url: str) -> str | None:
    value = html.unescape(str(url or "").strip())
    return value if _SAFE_URL_RE.match(value) else None


def markdown_to_telegram_html(text: str) -> str:
    """把常见 Markdown 转成 Telegram HTML；转换不了的语法保留为转义后的原文。"""

    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return ""
    store: list[str] = []

    def keep(fragment: str) -> str:
        store.append(fragment)
        return _PH.format(len(store) - 1)

    def fence(match: re.Match[str]) -> str:
        lang = re.sub(r"[^\w+#.-]", "", match.group(2) or "")[:32]
        code = match.group(3) or ""
        cls = f' class="language-{_attr(lang)}"' if lang else ""
        return "\n" + keep(f"<pre><code{cls}>{escape(code.rstrip())}</code></pre>") + "\n"

    work = _FENCE_RE.sub(fence, source)
    work = _INLINE_CODE_RE.sub(lambda m: keep(f"<code>{escape(m.group(1))}</code>"), work)
    work = escape(work)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), safe_url(match.group(2))
        if not url:
            return match.group(0)
        return keep(f'<a href="{_attr(url)}">{_inline(label)}</a>')

    work = _LINK_RE.sub(link, work)
    work = _AUTO_LINK_RE.sub(
        lambda m: keep(f'<a href="{_attr(html.unescape(m.group(1)))}">{m.group(1)}</a>') if safe_url(m.group(1)) else m.group(0),
        work,
    )
    work = _blocks(work)
    for _ in range(3):  # 占位符可能嵌套（链接文字里的行内代码）
        if not _PH_RE.search(work):
            break
        work = _PH_RE.sub(lambda m: store[int(m.group(1))], work)
    return re.sub(r"\n{3,}", "\n\n", work).strip()


def _inline(text: str) -> str:
    out = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    out = _STRIKE_RE.sub(r"<s>\1</s>", out)
    out = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", out)
    out = _ITALIC_STAR_RE.sub(r"<i>\1</i>", out)
    out = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", out)
    return out


def _blocks(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    quote: list[str] = []
    table: list[str] = []

    def flush_quote() -> None:
        if quote:
            out.append("<blockquote>" + "\n".join(_inline(item) for item in quote) + "</blockquote>")
            quote.clear()

    def flush_table() -> None:
        if not table:
            return
        rows = [row for row in table if not _TABLE_SEP_RE.match(row)]
        if len(table) >= 2 and any(_TABLE_SEP_RE.match(row) for row in table):
            out.append("<pre>" + "\n".join(row.strip() for row in rows) + "</pre>")
        else:
            out.extend(_inline(row) for row in table)
        table.clear()

    for line in lines:
        stripped = line.strip()
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
        heading = _HEADING_RE.match(line)
        if heading:
            out.append(f"<b>{_inline(heading.group(1))}</b>")
            continue
        if _HR_RE.match(line):
            out.append("──────────")
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            indent = "  " * (len(bullet.group(1).expandtabs(2)) // 2)
            out.append(f"{indent}• {_inline(bullet.group(2))}")
            continue
        out.append(_inline(line))
    flush_quote()
    flush_table()
    return "\n".join(out)


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
    "TELEGRAM_TEXT_LIMIT",
    "escape",
    "html_to_plain",
    "markdown_to_telegram_html",
    "safe_url",
    "sanitize_template_html",
    "split_html",
    "visible_length",
]
