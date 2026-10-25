"""PT 站点（NexusPHP）只读数据源。

只做一件事：用用户自己填的 Cookie，GET 站点 ``torrents.php``（可选 ``special.php``）的
搜索列表，解析成结构化条目交给模型作为“数据”。安全边界全部在代码层：

- 请求层：只允许 GET；URL 必须与配置站点同源（scheme + host + port 完全一致）；路径白名单
  只有 ``/torrents.php``（启用特别区时再加 ``/special.php``）；查询参数白名单只有搜索用的几个键；
  不自动跟随重定向，跨域重定向直接拒绝，被重定向到 login / takelogin 视为 Cookie 失效，
  只报“站点登录已失效”，绝不自动登录。``details.php`` / ``download.php`` / RSS / thanks /
  bookmark / comment / take* 一律不访问。
- 数据层：种子链接由代码按 ``{站点}/details.php?id={id}`` 重新拼接，不照搬页面 href；
  只保留标题、副标题、分类、大小、做种/下载/完成数、优惠、发布时间与 id。
- 脱敏：送给模型前、发给用户前各过滤一次 passkey / authkey / torrent_pass / downhash /
  token / sign / uid 参数、长度 ≥32 的十六进制串、download.php 链接、magnet:、Cookie 内容、
  站点用户名与 UID；命中即删除，由调用方记一条不含敏感值的 info 日志。
- Cookie 只放进请求头，任何异常文案、日志、缓存都不包含它。
"""

from __future__ import annotations

import hashlib
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

PATH_TORRENTS = "/torrents.php"
PATH_SPECIAL = "/special.php"
# 只放行搜索列表用到的查询参数。
ALLOWED_QUERY_KEYS = frozenset({"search", "search_area", "search_mode", "incldead", "spstate", "notnewword", "cat", "page"})
MAX_REDIRECTS = 2
MAX_KEYWORD_CHARS = 80
DEFAULT_UA = "Mozilla/5.0 (compatible; TelePilot-interaction-ai-chat; read-only torrent list)"

# 优惠标记：NexusPHP 的 img class。
PROMOTION_CLASSES = {
    "pro_free": "Free",
    "pro_free2up": "2xFree",
    "pro_2up": "2x上传",
    "pro_50pctdown": "50%",
    "pro_30pctdown": "30%",
    "pro_50pctdown2up": "2x 50%",
}
PROMOTION_ROW_CLASSES = {
    "free_bg": "Free",
    "twoupfree_bg": "2xFree",
    "twoup_bg": "2x上传",
    "halfdown_bg": "50%",
    "thirtypercentdown_bg": "30%",
    "twouphalfdown_bg": "2x 50%",
}


class PTError(RuntimeError):
    """PT 数据源失败；``reason`` 是可以直接展示给用户的简短中文原因，不含任何敏感值。"""

    code = "unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PTPolicyError(PTError):
    """违反只读白名单（方法、同源、路径、参数、重定向）。"""

    code = "policy"


class PTLoginExpired(PTError):
    code = "login_expired"

    def __init__(self, reason: str = "站点登录已失效") -> None:
        super().__init__(reason)


class PTRateLimited(PTError):
    code = "rate_limited"


# ---------------------------------------------------------------------------
# 站点与请求白名单
# ---------------------------------------------------------------------------


def normalize_site(raw: Any) -> str:
    """把配置的站点地址规范成 ``https://host[:port]``；不合法时返回空字符串。

    只接受 https；不允许用户名/密码、路径之外的花样（路径、查询、片段一律丢弃）。
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
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or (443 if scheme == "https" else 80 if scheme == "http" else 0)
    return scheme, (parts.hostname or "").lower().rstrip("."), port


def allowed_paths(allow_special: bool) -> frozenset[str]:
    return frozenset({PATH_TORRENTS, PATH_SPECIAL}) if allow_special else frozenset({PATH_TORRENTS})


def assert_request_allowed(method: str, url: str, site: str, *, allow_special: bool = False) -> None:
    """请求白名单：任何一项不满足都抛 :class:`PTPolicyError`，请求不会发出。"""

    if str(method or "").upper() != "GET":
        raise PTPolicyError("PT 数据源只允许 GET 请求")
    if not site:
        raise PTPolicyError("未配置合法的 PT 站点地址")
    try:
        parts = urlsplit(str(url or ""))
        if _origin(url) != _origin(site):
            raise PTPolicyError("拒绝访问与配置站点不同源的地址")
    except ValueError as exc:
        raise PTPolicyError("PT 请求地址不合法") from exc
    if parts.username or parts.password or parts.fragment:
        raise PTPolicyError("PT 请求地址不合法")
    path = parts.path or "/"
    if path not in allowed_paths(allow_special):
        raise PTPolicyError("拒绝访问白名单之外的站点路径")
    for key, _value in parse_qsl(parts.query, keep_blank_values=True):
        if key not in ALLOWED_QUERY_KEYS:
            raise PTPolicyError("拒绝携带白名单之外的查询参数")


def clean_keyword(raw: Any) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_KEYWORD_CHARS]


def build_search_url(site: str, keyword: str, *, category: str = "", special: bool = False) -> str:
    params: list[tuple[str, str]] = [
        ("search", keyword),
        ("search_area", "0"),
        ("search_mode", "0"),
        ("incldead", "0"),
        ("notnewword", "1"),
    ]
    cat = str(category or "").strip()
    if cat.isdigit() and len(cat) <= 6:
        params.append(("cat", cat))
    path = PATH_SPECIAL if special else PATH_TORRENTS
    return f"{site}{path}?{urlencode(params, quote_via=quote)}"


def is_login_location(location: str) -> bool:
    path = urlsplit(location).path.lower()
    name = path.rsplit("/", 1)[-1]
    return name.startswith(("login", "takelogin")) or "/login" in path


def details_url(site: str, torrent_id: Any) -> str:
    tid = str(torrent_id or "").strip()
    if not tid.isdigit():
        return ""
    return f"{site}/details.php?id={int(tid)}"


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------


async def fetch_list(
    http: Any,
    site: str,
    url: str,
    *,
    cookie: str,
    user_agent: str = "",
    allow_special: bool = False,
) -> str:
    """按白名单 GET 列表页并返回 HTML 文本。

    ``http`` 为平台 ``ctx.http``（不会自动跟随重定向）。同源且仍在白名单内的重定向最多跟
    2 次；跨域、白名单外直接拒绝；登录页视为 Cookie 失效。
    """

    if http is None or getattr(http, "get", None) is None:
        raise PTError("平台未提供 ctx.http（需要 external_http 权限）")
    if not cookie:
        raise PTLoginExpired("未配置站点 Cookie")
    headers = {
        "Cookie": cookie,
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        assert_request_allowed("GET", current, site, allow_special=allow_special)
        try:
            response = await http.get(current, headers=headers)
        except PTError:
            raise
        except Exception as exc:  # noqa: BLE001 - 不把底层异常文案（可能含请求细节）带出去
            raise PTError(_network_reason(exc)) from None
        status = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status < 400:
            location = _header(response, "location")
            if not location:
                raise PTError("站点返回了无目标的重定向")
            target = urljoin(current, location)
            if is_login_location(target):
                raise PTLoginExpired()
            try:
                if _origin(target) != _origin(site):
                    raise PTPolicyError("站点重定向到其它域名，已拒绝跟随")
            except ValueError:
                raise PTPolicyError("站点重定向地址不合法") from None
            current = target  # 下一轮循环先过白名单
            continue
        if status in (401, 403):
            body = _text(response)
            if _looks_like_login_page(body):
                raise PTLoginExpired()
            raise PTError("站点拒绝访问（可能触发了防护或需要验证）")
        if status == 429:
            raise PTError("站点限流，请稍后再试")
        if status >= 500 or status == 0:
            raise PTError("站点暂时无法访问")
        if status != 200:
            raise PTError(f"站点返回异常状态 {status}")
        body = _text(response)
        if _looks_like_login_page(body):
            raise PTLoginExpired()
        return body
    raise PTError("站点重定向次数过多")


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
        return "站点响应超时"
    if "too large" in text or "toolarge" in name or "超过上限" in text:
        return "站点页面过大"
    if "allowed_hosts" in text or "policy" in name:
        return "站点域名不在插件允许的出网范围内"
    if "代理" in text or "proxy" in text:
        return "账号代理不可用"
    return "无法连接站点"


def _looks_like_login_page(body: str) -> bool:
    lower = (body or "").lower()
    if 'class="torrents"' in lower or "class='torrents'" in lower or "logout.php" in lower:
        return False
    return "takelogin.php" in lower or bool(re.search(r"<input[^>]+type=[\"']?password", lower))


# ---------------------------------------------------------------------------
# 迷你 DOM（标准库 html.parser，无第三方依赖）
# ---------------------------------------------------------------------------

VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []
        self.parent = parent

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, tag: str) -> list[Node]:
        return [node for node in self.iter() if node.tag == tag]

    def child_elements(self, *tags: str) -> list[Node]:
        return [child for child in self.children if isinstance(child, Node) and child.tag in tags]

    def text(self, *, br: str = " ", skip_tags: tuple[str, ...] = ()) -> str:
        out: list[str] = []
        self._collect(out, br, skip_tags)
        return re.sub(r"[ \t\r\f\v]+", " ", "".join(out)).strip()

    def _collect(self, out: list[str], br: str, skip_tags: tuple[str, ...]) -> None:
        for child in self.children:
            if isinstance(child, str):
                out.append(child)
            elif child.tag == "br":
                out.append(br)
            elif child.tag in ("script", "style") or child.tag in skip_tags:
                continue
            else:
                child._collect(out, br, skip_tags)

    def has_ancestor(self, predicate) -> bool:
        node = self.parent
        while node is not None:
            if predicate(node):
                return True
            node = node.parent
        return False


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.stack: list[Node] = [self.root]

    def _close_until(self, tags: set[str], stop: set[str]) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[index].tag
            if tag in stop:
                return
            if tag in tags:
                del self.stack[index:]
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("td", "th"):
            self._close_until({"td", "th"}, {"tr", "table"})
        elif tag == "tr":
            self._close_until({"tr"}, {"table"})
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
        builder.close()
    except Exception:  # noqa: BLE001 - 解析尽力而为
        pass
    return builder.root


# ---------------------------------------------------------------------------
# 列表解析
# ---------------------------------------------------------------------------

_DETAILS_ID_RE = re.compile(r"details\.php\?(?:[^\"'#]*&)?id=(\d+)", re.IGNORECASE)
_USER_ID_RE = re.compile(r"userdetails\.php\?(?:[^\"'#]*&)?id=(\d+)", re.IGNORECASE)
_HEADER_KEYS = ("comments", "time", "size", "seeders", "leechers", "snatched")
_HEADER_TEXT = {
    "comments": ("评论", "comment"),
    "time": ("时间", "存活", "added", "time"),
    "size": ("大小", "size"),
    "seeders": ("做种", "种子数", "seeders", "上传"),
    "leechers": ("下载", "leechers"),
    "snatched": ("完成", "snatched"),
}


def _is_torrents_table(node: Node) -> bool:
    return node.tag == "table" and "torrents" in node.classes()


def _rows(table: Node) -> list[Node]:
    rows = table.child_elements("tr")
    for section in table.child_elements("tbody", "thead"):
        rows.extend(section.child_elements("tr"))
    return rows


def _header_map(header: Node) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header.child_elements("td", "th")):
        marks = set(cell.classes())
        for img in cell.find_all("img"):
            marks |= img.classes()
            marks.add((img.attrs.get("alt") or "").lower())
            marks.add((img.attrs.get("title") or "").lower())
        label = cell.text().lower()
        for key in _HEADER_KEYS:
            if key in mapping:
                continue
            if key in marks or any(word in label for word in _HEADER_TEXT[key]) or any(
                word in mark for mark in marks for word in _HEADER_TEXT[key] if mark
            ):
                mapping[key] = index
    return mapping


def _int_text(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _promotion(title_cell: Node, row: Node) -> str:
    for img in title_cell.find_all("img"):
        for cls in img.classes():
            if cls in PROMOTION_CLASSES:
                return PROMOTION_CLASSES[cls]
    for cls in row.classes():
        if cls in PROMOTION_ROW_CLASSES:
            return PROMOTION_ROW_CLASSES[cls]
    return ""


def _category(cell: Node | None) -> str:
    if cell is None:
        return ""
    for img in cell.find_all("img"):
        name = (img.attrs.get("alt") or img.attrs.get("title") or "").strip()
        if name:
            return name[:40]
    return cell.text()[:40]


def _title_link(cell: Node) -> Node | None:
    for link in cell.find_all("a"):
        if _DETAILS_ID_RE.search(link.attrs.get("href") or ""):
            return link
    return None


def _subtitle(title_link: Node) -> str:
    """副标题：标题链接所在单元里第一个 <br> 之后的文字（跳过链接与脚本）。"""

    container = title_link.parent
    while container is not None and container.tag not in ("td",):
        container = container.parent
    if container is None:
        return ""
    out: list[str] = []
    seen_br = False

    def walk(node: Node) -> None:
        nonlocal seen_br
        for child in node.children:
            if isinstance(child, str):
                if seen_br:
                    out.append(child)
                continue
            if child.tag == "br":
                seen_br = True
                out.append(" ")
                continue
            if child.tag in ("script", "style", "a", "table") and child is not title_link:
                if child.tag == "table":
                    walk(child)
                continue
            if child is title_link:
                continue
            walk(child)

    walk(container)
    return re.sub(r"\s+", " ", "".join(out)).strip()[:200]


def _time_text(cell: Node | None) -> str:
    if cell is None:
        return ""
    for span in cell.find_all("span"):
        title = (span.attrs.get("title") or "").strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", title):
            return title[:19]
    return cell.text(br=" ")[:40]


def parse_current_user(root: Node) -> tuple[str, str]:
    """页面顶部的当前登录用户（用户名, UID），用于加入屏蔽词；解析不到返回空。"""

    info = next((node for node in root.iter() if node.attrs.get("id") in ("info_block", "userbar", "user_info")), None)
    scope = info if info is not None else root
    for link in scope.find_all("a"):
        match = _USER_ID_RE.search(link.attrs.get("href") or "")
        if not match:
            continue
        if link.has_ancestor(_is_torrents_table):
            continue
        return link.text()[:64], match.group(1)
    return "", ""


def parse_torrent_list(html: str, site: str, *, limit: int = 10) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """解析 NexusPHP 列表页，返回 (条目列表, 当前用户 {name, uid})。

    每条只含白名单字段；``url`` 由代码按 ``{site}/details.php?id={id}`` 重新拼接。
    """

    root = parse_html(html)
    name, uid = parse_current_user(root)
    user = {"name": name, "uid": uid}
    table = next((node for node in root.iter() if _is_torrents_table(node)), None)
    if table is None:
        return [], user
    rows = _rows(table)
    header_map: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        cells = row.child_elements("td", "th")
        title_index = next((i for i, cell in enumerate(cells) if _title_link(cell) is not None), None)
        if title_index is None:
            if not header_map and cells:
                header_map = _header_map(row)
            continue
        link = _title_link(cells[title_index])
        assert link is not None
        match = _DETAILS_ID_RE.search(link.attrs.get("href") or "")
        tid = match.group(1) if match else ""
        if not tid or tid in seen:
            continue
        seen.add(tid)

        def cell(
            key: str, offset: int, cells: list[Node] = cells, title_index: int = title_index, header_map: dict[str, int] = header_map
        ) -> Node | None:
            index = header_map.get(key, title_index + offset)
            return cells[index] if 0 <= index < len(cells) and index != title_index else None

        title = (link.attrs.get("title") or "").strip() or link.text()
        size_cell, seed_cell, leech_cell, snatch_cell = cell("size", 3), cell("seeders", 4), cell("leechers", 5), cell("snatched", 6)
        items.append(
            {
                "id": int(tid),
                "title": unescape(title)[:200],
                "subtitle": _subtitle(link),
                "category": _category(cells[0] if title_index > 0 else None),
                "size": size_cell.text(br=" ")[:24] if size_cell is not None else "",
                "seeders": _int_text(seed_cell.text()) if seed_cell is not None else None,
                "leechers": _int_text(leech_cell.text()) if leech_cell is not None else None,
                "completed": _int_text(snatch_cell.text()) if snatch_cell is not None else None,
                "promotion": _promotion(cells[title_index], row),
                "added": _time_text(cell("time", 2)),
                "url": details_url(site, tid),
            }
        )
        if len(items) >= max(1, int(limit)):
            break
    return items, user


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

SENSITIVE_PARAM_RE = re.compile(
    r"(?i)\b(?:passkey|authkey|auth_key|torrent_pass|downhash|token|sign|secret|uid|rsskey|apikey|api_key)\s*[=:：]\s*[^\s&\"'<>()\[\]]+"
)
HEX_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32,}(?![0-9a-f])")
MAGNET_RE = re.compile(r"(?i)magnet:\?[^\s\"'<>)\]]*")
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'）)\]]+")
MD_LINK_RE = re.compile(r"\[([^\]\n]{0,300})\]\(\s*<?([^)\s>]+)>?\s*\)")
BAD_URL_RE = re.compile(
    r"(?i)(download\.php|dl\.php|torrentrss|rss\.php|getrss|myrss|linksrss|takelogin|login\.php|logout|thanks\.php|bookmark|comment\.php|take[a-z]*\.php"
    r"|passkey|authkey|torrent_pass|downhash|[?&](?:token|sign|uid|rsskey)=)"
)
BARE_DOWNLOAD_RE = re.compile(r"(?i)\b[\w./-]*download\.php\?[^\s\"'<>)\]]*")


def cookie_values(cookie: str) -> list[str]:
    """Cookie 中每个值（以及整串）都当作屏蔽词；过短的值（<6）不作为屏蔽词避免误伤正文。"""

    out: list[str] = []
    text = str(cookie or "").strip()
    if len(text) >= 6:
        out.append(text)
    for part in text.split(";"):
        _name, sep, value = part.strip().partition("=")
        value = value.strip()
        if sep and len(value) >= 6:
            out.append(value)
    return out


def build_blockwords(cookie: str = "", username: str = "", uid: str = "") -> dict[str, list[str]]:
    words: dict[str, list[str]] = {"cookie": cookie_values(cookie), "username": [], "uid": []}
    name = str(username or "").strip()
    if len(name) >= 3:
        words["username"].append(name)
    if str(uid or "").strip().isdigit():
        words["uid"].append(str(uid).strip())
    return words


class Sanitizer:
    """双重脱敏共用的过滤器；``hits`` 记录命中的类别与次数（不记录值）。"""

    def __init__(self, site: str = "", blockwords: dict[str, list[str]] | None = None) -> None:
        self.site = site
        self.site_origin = _origin(site) if site else None
        self.blockwords = blockwords or {}
        self.hits: dict[str, int] = {}

    def _hit(self, kind: str, count: int = 1) -> None:
        if count:
            self.hits[kind] = self.hits.get(kind, 0) + count

    @property
    def hit_count(self) -> int:
        return sum(self.hits.values())

    def clean_url(self, url: str) -> str:
        """链接规则：敏感链接删除；指向 PT 站点的链接只放行 details.php?id=N（并重新拼接）。"""

        raw = str(url or "").strip()
        if not raw:
            return ""
        if raw.lower().startswith("magnet:"):
            self._hit("magnet")
            return ""
        if BAD_URL_RE.search(raw) or HEX_RE.search(raw):
            self._hit("download_or_secret_link")
            return ""
        if self.site_origin is not None:
            candidate = raw if "://" in raw else "https://" + raw
            try:
                # 同一主机（不论 http/https、端口）都按站点链接处理。
                same_site = _origin(candidate)[1] == self.site_origin[1]
            except ValueError:
                same_site = False
            if same_site:
                parts = urlsplit(candidate)
                query = dict(parse_qsl(parts.query))
                if parts.path == "/details.php" and str(query.get("id", "")).isdigit():
                    return details_url(self.site, query["id"])
                self._hit("site_non_detail_link")
                return ""
        return raw

    def clean_text(self, text: str) -> str:
        value = str(text or "")
        if not value:
            return value

        def md_link(match: re.Match[str]) -> str:
            label, url = match.group(1), match.group(2)
            safe = self.clean_url(url)
            return f"[{label}]({safe})" if safe else label

        value = MD_LINK_RE.sub(md_link, value)
        value, count = MAGNET_RE.subn("", value)
        self._hit("magnet", count)

        def bare_url(match: re.Match[str]) -> str:
            url = match.group(0)
            trail = ""
            while url and url[-1] in ".,;:!?，。；：！？":
                trail = url[-1] + trail
                url = url[:-1]
            safe = self.clean_url(url)
            return safe + trail if safe else trail

        value = URL_RE.sub(bare_url, value)
        value, count = BARE_DOWNLOAD_RE.subn("", value)
        self._hit("download_or_secret_link", count)
        value, count = SENSITIVE_PARAM_RE.subn("", value)
        self._hit("secret_param", count)
        value, count = HEX_RE.subn("", value)
        self._hit("long_hex", count)
        for word in sorted(self.blockwords.get("cookie", []), key=len, reverse=True):
            if word and word in value:
                self._hit("cookie", value.count(word))
                value = value.replace(word, "")
        for word in self.blockwords.get("username", []):
            pattern = re.compile(rf"(?i)(?<![\w]){re.escape(word)}(?![\w])")
            value, count = pattern.subn("", value)
            self._hit("username", count)
        for uid in self.blockwords.get("uid", []):
            pattern = re.compile(rf"(?i)(?:\buid\b|用户\s*id|userdetails\.php\?id=)\s*[:：=#]?\s*{re.escape(uid)}(?!\d)")
            value, count = pattern.subn("", value)
            self._hit("uid", count)
        return value

    def clean_item(self, item: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in item.items():
            if key == "url":
                out[key] = self.clean_url(str(value)) if value else ""
            elif isinstance(value, str):
                out[key] = re.sub(r"\s+", " ", self.clean_text(value)).strip()
            else:
                out[key] = value
        return out


def _seeders_key(item: dict[str, Any]) -> int:
    value = item.get("seeders")
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def sort_by_seeders(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按做种数降序（稳定排序，做种数未知的排在最后）。"""

    return sorted(items, key=_seeders_key, reverse=True)


def format_items_for_model(items: list[dict[str, Any]], *, keyword: str, area: str, order: str = "按站点默认排序") -> str:
    if not items:
        return f"站点{area}搜索“{keyword}”没有找到种子。"
    lines = [f"站点{area}搜索“{keyword}”找到 {len(items)} 条（{order}，仅列表信息）："]
    for index, item in enumerate(items, 1):
        parts = [f"{index}. {item.get('title') or '（无标题）'}"]
        if item.get("subtitle"):
            parts.append(f"副标题：{item['subtitle']}")
        if item.get("category"):
            parts.append(f"分类：{item['category']}")
        if item.get("size"):
            parts.append(f"大小：{item['size']}")
        stats = []
        for key, label in (("seeders", "做种"), ("leechers", "下载"), ("completed", "完成")):
            if item.get(key) is not None:
                stats.append(f"{label} {item[key]}")
        if stats:
            parts.append(" / ".join(stats))
        if item.get("promotion"):
            parts.append(f"优惠：{item['promotion']}")
        if item.get("added"):
            parts.append(f"发布：{item['added']}")
        if item.get("url"):
            parts.append(f"详情页：{item['url']}")
        lines.append("；".join(parts))
    return "\n".join(lines)


def cache_key(site: str, keyword: str, category: str, special: bool) -> str:
    raw = "\x1f".join([site, keyword.casefold(), str(category or ""), "special" if special else "normal"])
    return "pt:cache:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "PTError",
    "PTLoginExpired",
    "PTPolicyError",
    "PTRateLimited",
    "Sanitizer",
    "assert_request_allowed",
    "build_blockwords",
    "build_search_url",
    "cache_key",
    "clean_keyword",
    "details_url",
    "fetch_list",
    "format_items_for_model",
    "normalize_site",
    "parse_torrent_list",
]
