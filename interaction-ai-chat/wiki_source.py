"""站点 Wiki 只读数据源（例如青蛙 https://wiki.qingwapt.org/docs）。

安全边界全部在这里，用代码硬性限制：

- 只发 GET；URL 必须与配置的 Wiki 地址同 host（同 scheme/端口），路径必须在 ``/docs`` 之下；
  不带查询参数、片段、用户名密码；不自动跟随重定向，同 host 且仍在 ``/docs`` 下的重定向最多跟 2 次。
- 只提取页面 ``<h1>`` 到「本页内容」（目录导航）之前的正文，去掉脚本、样式与图片，按上限截断。
- 送给模型前剔除所有包含 passkey / RSS 的行，以及任何形似 passkey 的值（≥32 位十六进制串、
  ``passkey=…`` / ``authkey=…`` / ``torrent_pass=…`` 参数）。
- 提取结果按页面缓存 6 小时（缓存的是已清洗正文），减少对站点的访问。
"""

from __future__ import annotations

import html as html_lib
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

DOCS_PREFIX = "/docs"
MAX_REDIRECTS = 2
CACHE_TTL_SECONDS = 6 * 3600
CACHE_MAX_ENTRIES = 64
PAGE_MAX_CHARS = 3500
MAX_PAGES = 2
DEFAULT_UA = "Mozilla/5.0 (compatible; TelePilot-AI-Chat/0.4; +wiki-reader)"

# 正文结束标记：右侧「本页内容」目录、导航、页脚。取 <h1> 之后最先出现的一个。
END_MARKERS = ("本页内容", "On this page", "<nav", "</main>", "<footer", "Copyright ©")
SKIP_TAGS = frozenset({"script", "style", "svg", "noscript", "template", "button", "img", "picture", "iframe", "form", "input"})
BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "li", "ul", "ol", "tr", "table", "thead", "tbody", "pre", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "dl", "hr", "details", "summary", "figure", "figcaption",
    }
)
PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
SENSITIVE_LINE_RE = re.compile(r"passkey|rss|torrent_pass|authkey", re.IGNORECASE)
SENSITIVE_VALUE_RES = (
    re.compile(r"(?i)\b(passkey|authkey|torrent_pass|downhash|token|sign)\s*[=:：]\s*[^\s&\"'<>]+"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)
CATALOG_LINE_RE = re.compile(r"`(/docs(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*)`\s*([^\n]*)")


class WikiError(RuntimeError):
    """读取 Wiki 失败（中文原因，可直接写日志）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WikiPolicyError(WikiError):
    """请求不在白名单内，未发出。"""


# ---------------------------------------------------------------------------
# 地址与白名单
# ---------------------------------------------------------------------------


def normalize_base(raw: Any) -> str:
    """把配置的 Wiki 地址规范为 ``https://host[:port]/docs``；不合法或留空返回空串。

    只接受 https；必须指向 ``/docs``（只填域名时自动补 ``/docs``）；其它路径一律拒绝。
    """

    text = str(raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        return ""
    host = parts.hostname.lower().rstrip(".")
    if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host or host.startswith(".") or ".." in host:
        return ""
    path = (parts.path or "").rstrip("/")
    if path not in ("", DOCS_PREFIX):
        return ""
    return f"https://{host}" + (f":{port}" if port and port != 443 else "") + DOCS_PREFIX


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or (443 if scheme == "https" else 80 if scheme == "http" else 0)
    return scheme, (parts.hostname or "").lower().rstrip("."), port


def clean_path(raw: Any) -> str:
    """校验并规范 Wiki 页面路径：必须是 ``/docs`` 或 ``/docs/...``，每段只含安全字符。"""

    path = str(raw or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/") or "/"
    if path == DOCS_PREFIX:
        return path
    if not path.startswith(DOCS_PREFIX + "/"):
        return ""
    segments = path[len(DOCS_PREFIX) + 1 :].split("/")
    if not segments or any(not PATH_SEGMENT_RE.match(item) or item in {".", ".."} or ".." in item for item in segments):
        return ""
    return path


def page_url(base: str, path: str) -> str:
    safe = clean_path(path)
    if not base or not safe:
        return ""
    return base[: -len(DOCS_PREFIX)] + safe


def assert_request_allowed(method: str, url: str, base: str) -> None:
    """请求白名单：任何一项不满足都抛 :class:`WikiPolicyError`，请求不会发出。"""

    if str(method or "").upper() != "GET":
        raise WikiPolicyError("站点 Wiki 只允许 GET 请求")
    if not base:
        raise WikiPolicyError("未配置合法的站点 Wiki 地址")
    try:
        parts = urlsplit(str(url or ""))
        if _origin(url) != _origin(base):
            raise WikiPolicyError("拒绝访问与 Wiki 地址不同 host 的页面")
    except ValueError as exc:
        raise WikiPolicyError("Wiki 地址不合法") from exc
    if parts.username or parts.password or parts.fragment or parts.query:
        raise WikiPolicyError("Wiki 页面地址不能带参数")
    if not clean_path(parts.path or "/"):
        raise WikiPolicyError("只允许读取 /docs 下的 Wiki 页面")


# ---------------------------------------------------------------------------
# 目录（来自 qingwa-wiki skill 正文）
# ---------------------------------------------------------------------------


def parse_catalog(skill_body: str, *, limit: int = 80) -> list[tuple[str, str]]:
    """从 skill 正文解析页面目录：每行 ```/docs/...` 标题 · 说明``。"""

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in CATALOG_LINE_RE.finditer(str(skill_body or "")):
        path = clean_path(match.group(1))
        if not path or path in seen:
            continue
        seen.add(path)
        result.append((path, match.group(2).strip()[:160]))
        if len(result) >= limit:
            break
    return result


def skill_guidance(skill_body: str, *, limit: int = 1500) -> str:
    """skill 正文中目录之前的说明部分（管理员可编辑），用作回答指引。"""

    body = str(skill_body or "")
    cut = body.find("## 页面目录")
    if cut < 0:
        match = CATALOG_LINE_RE.search(body)
        cut = match.start() if match else len(body)
    text = body[:cut].strip()
    return text[:limit]


# ---------------------------------------------------------------------------
# 正文提取与清洗
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            if tag not in {"img", "input"}:
                self._skip += 1
            return
        if self._skip:
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
            if tag in {"h2", "h3", "h4"}:
                self.parts.append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self.parts.append("- ")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and tag not in {"img", "input"}:
            self._skip = max(0, self._skip - 1)
            return
        if not self._skip and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self.parts.append(data)


def _cut_article(page: str) -> str:
    start = page.find("<h1")
    if start < 0:
        return ""
    end = len(page)
    for marker in END_MARKERS:
        index = page.find(marker, start + 3)
        if 0 <= index < end:
            end = index
    return page[start:end]


def extract_article(page: str) -> tuple[str, str]:
    """返回 (标题, 正文)：从 ``<h1>`` 到「本页内容」之前；没有 ``<h1>`` 时返回空。"""

    fragment = _cut_article(str(page or ""))
    if not fragment:
        return "", ""
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", fragment, re.DOTALL | re.IGNORECASE)
    title = ""
    if title_match:
        title = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).split())
    parser = _TextExtractor()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:  # noqa: BLE001 - 解析失败时退回粗略去标签
        parser.parts = [re.sub(r"<[^>]+>", "\n", fragment)]
    raw = "".join(parser.parts)
    lines: list[str] = []
    for line in raw.splitlines():
        text = " ".join(line.split())
        if not text or text in {"-", "|"}:
            continue
        if lines and lines[-1] == text:
            continue
        lines.append(text)
    if lines and title and lines[0] == title:
        lines = lines[1:]
    return title, "\n".join(lines)


def sanitize(text: str) -> tuple[str, int]:
    """剔除含 passkey/RSS 的整行与任何 passkey 值；返回 (清洗后文本, 命中次数)。"""

    hits = 0
    kept: list[str] = []
    for line in str(text or "").splitlines():
        if SENSITIVE_LINE_RE.search(line):
            hits += 1
            continue
        for pattern in SENSITIVE_VALUE_RES:
            line, count = pattern.subn("[已隐藏]", line)
            hits += count
        kept.append(line)
    return "\n".join(kept), hits


def truncate(text: str, limit: int = PAGE_MAX_CHARS) -> tuple[str, bool]:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value, False
    cut = value[:limit]
    newline = cut.rfind("\n")
    if newline > limit * 0.6:
        cut = cut[:newline]
    return cut.rstrip() + "\n…（已截断，完整内容见页面链接）", True


# ---------------------------------------------------------------------------
# 抓取与缓存
# ---------------------------------------------------------------------------


class WikiCache:
    """进程内缓存：键为页面 URL，值为已清洗的 (标题, 正文)。"""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, url: str) -> dict[str, Any] | None:
        item = self._items.get(url)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._items.pop(url, None)
            return None
        return value

    def set(self, url: str, value: dict[str, Any]) -> None:
        if len(self._items) >= self.max_entries and url not in self._items:
            oldest = min(self._items.items(), key=lambda kv: kv[1][0])[0]
            self._items.pop(oldest, None)
        self._items[url] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        self._items.clear()


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None) or {}
    for key, value in dict(headers).items():
        if str(key).lower() == name:
            return str(value or "").strip()
    return ""


def _text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", b"") or b""
    return bytes(content).decode("utf-8", errors="replace")


def _network_reason(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return "Wiki 响应超时"
    if "too large" in text or "toolarge" in name or "超过上限" in text:
        return "Wiki 页面过大"
    if "allowed_hosts" in text or "policy" in name:
        return "Wiki 域名不在插件允许的出网范围内"
    return "无法连接站点 Wiki"


async def fetch_html(http: Any, base: str, url: str) -> str:
    """按白名单 GET 页面 HTML；``http`` 为平台 ``ctx.http``（不自动跟随重定向）。"""

    if http is None or getattr(http, "get", None) is None:
        raise WikiError("平台未提供 ctx.http（需要 external_http 权限）")
    headers = {"User-Agent": DEFAULT_UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9"}
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        assert_request_allowed("GET", current, base)
        try:
            response = await http.get(current, headers=headers)
        except WikiError:
            raise
        except Exception as exc:  # noqa: BLE001 - 不带出底层异常文案
            raise WikiError(_network_reason(exc)) from None
        status = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status < 400:
            location = _header(response, "location")
            if not location:
                raise WikiError("Wiki 返回了无目标的重定向")
            current = urljoin(current, location)  # 下一轮先过白名单
            continue
        if status == 404:
            raise WikiError("Wiki 页面不存在")
        if status == 429:
            raise WikiError("Wiki 限流，请稍后再试")
        if status != 200:
            raise WikiError(f"Wiki 返回异常状态 {status}")
        return _text(response)
    raise WikiError("Wiki 重定向次数过多")


async def read_page(http: Any, base: str, path: str, *, cache: WikiCache, limit: int = PAGE_MAX_CHARS) -> dict[str, Any]:
    """读取一页：返回 {path, url, title, content, truncated, redacted, cached}。"""

    url = page_url(base, path)
    if not url:
        raise WikiPolicyError("只允许读取 /docs 下的 Wiki 页面")
    cached = cache.get(url)
    if cached is not None:
        return {**cached, "cached": True}
    page = await fetch_html(http, base, url)
    title, body = extract_article(page)
    if not body:
        raise WikiError("没有从 Wiki 页面提取到正文")
    body, hits = sanitize(body)
    title, title_hits = sanitize(title)
    content, truncated = truncate(body, limit)
    value = {
        "path": clean_path(path),
        "url": url,
        "title": title.strip() or clean_path(path),
        "content": content,
        "truncated": truncated,
        "redacted": hits + title_hits,
    }
    cache.set(url, value)
    return {**value, "cached": False}


__all__ = [
    "CACHE_TTL_SECONDS",
    "MAX_PAGES",
    "WikiCache",
    "WikiError",
    "WikiPolicyError",
    "assert_request_allowed",
    "clean_path",
    "extract_article",
    "fetch_html",
    "normalize_base",
    "page_url",
    "parse_catalog",
    "read_page",
    "sanitize",
    "skill_guidance",
]
