"""青娃PT (qingwapt.com) 置顶促销模块。

功能：
  - 在青娃PT上为种子设置置顶促销
  - 支持自定义促销类型、时长、竞价和奖励
  - 支持查询促销历史

用法：
  {prefix}pt <种子ID> [选项]

选项（可选，用空格分隔）：
  free        促销类型：free=Free, 2x=2X Free（默认 free）
  1d/2d/3d/7d 时长：1天/2天/3天/7天（默认 1天）
  bid=100     竞价蝌蚪（越高排名越靠前）
  reward=50   奖励蝌蚪（吸引下载者）
  users=10    奖励人数

示例：
  {prefix}pt 12345                    # 默认：Free 1天
  {prefix}pt 12345 free 7d            # Free 7天
  {prefix}pt 12345 2x 3d bid=200      # 2X Free 3天 竞价200
  {prefix}pt 12345 free 7d bid=100 reward=50 users=10

查询：
  {prefix}ptinfo <种子ID>

注意：
  - 需要站点账号权限（消耗蝌蚪）
  - Cookie 需要在配置中设置
"""
from __future__ import annotations

import re
from html import escape as html_escape
from html import unescape as html_unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from app.worker.plugins.base import Plugin, PluginContext, register

# 促销类型映射
PROMO_TYPES = {
    "free": "2",
    "2x": "4",
    "2xfree": "4",
}

# 时长映射（天 -> 小时）
DURATION_MAP = {
    "1d": "24",
    "2d": "48",
    "3d": "72",
    "7d": "168",
    "1": "24",
    "2": "48",
    "3": "72",
    "7": "168",
}


class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


PROMOTE_USAGE_TEMPLATE_DEFAULT = (
    "用法：{prefix}{command} <种子ID> [选项]\n\n"
    "选项：\n"
    "  free/2x — 促销类型（默认 free）\n"
    "  1d/2d/3d/7d — 时长（默认 1天）\n"
    "  bid=100 — 竞价蝌蚪\n"
    "  reward=50 — 奖励蝌蚪\n"
    "  users=10 — 奖励人数\n\n"
    "示例：{prefix}{command} 12345 free 7d bid=100"
)
PROMOTE_STATUS_TEMPLATE_DEFAULT = "{icon} {message}"
PROMOTE_READY_TEMPLATE_DEFAULT = (
    "📋 ID 为 {torrent_id} 的种子符合促销条件\n\n"
    "{params}\n\n"
    "⏳ 正在计算预计消耗..."
)
PROMOTE_CONFIRMING_TEMPLATE_DEFAULT = (
    "📋 ID 为 {torrent_id} 的种子符合促销条件\n\n"
    "{params}\n"
    "预计消耗：{cost} 蝌蚪\n"
    "计算方式：{expression}\n\n"
    "⏳ 正在确认置顶..."
)
PROMOTE_SUCCESS_TEMPLATE_DEFAULT = (
    "✅ 种子置顶促销成功！\n\n"
    "{torrent_header}\n\n"
    "<blockquote expandable><pre><code class=\"language-副标题与促销明细\">"
    "{subtitle}\n"
    "{params}\n"
    "消耗：{cost} 蝌蚪"
    "</code></pre></blockquote>"
)
INFO_OK_TEMPLATE_DEFAULT = (
    "📋 ID 为 {torrent_id} 的种子当前符合促销条件。\n"
    "{details_url}"
)


def _command_prefix() -> str:
    try:
        from app.worker.command import current_command_prefix

        return current_command_prefix(fallback=",")
    except Exception:
        return ","


def _render_template(template: Any, payload: dict[str, Any]) -> str:
    text = str(template or "")
    try:
        return text.format_map(_SafeTemplateDict(payload))
    except Exception:
        return text


def _cfg_template(ctx: PluginContext, key: str, default: str, payload: dict[str, Any]) -> str:
    configured = None
    try:
        configured = (ctx.config or {}).get(key)
    except Exception:
        configured = None
    return _render_template(configured or default, payload)


def _status_template(
    ctx: PluginContext,
    icon: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> str:
    values = {"icon": icon, "message": message, **(payload or {})}
    return _cfg_template(ctx, "promote_status_template", PROMOTE_STATUS_TEMPLATE_DEFAULT, values)


def _parse_args(args: list[str]) -> dict[str, str]:
    """解析命令参数。"""
    result = {
        "promotion_type": "2",  # 默认 Free
        "duration": "24",       # 默认 1天
        "competitive_bonus": "",
        "reward_bonus": "",
        "reward_user_num": "",
    }

    for arg in args:
        arg_lower = arg.lower()

        # 促销类型
        if arg_lower in PROMO_TYPES:
            result["promotion_type"] = PROMO_TYPES[arg_lower]

        # 时长
        elif arg_lower in DURATION_MAP:
            result["duration"] = DURATION_MAP[arg_lower]

        # 键值对参数
        elif "=" in arg:
            key, _, value = arg.partition("=")
            key = key.lower().strip()
            value = value.strip()

            if key == "bid":
                result["competitive_bonus"] = value
            elif key == "reward":
                result["reward_bonus"] = value
            elif key == "users":
                result["reward_user_num"] = value

    return result


def _format_params(params: dict[str, str]) -> str:
    """格式化参数为可读文本。"""
    lines = []

    # 促销类型
    lines.append(f"促销类型：{_promotion_type_label(params)}")

    # 时长
    lines.append(f"促销时长：{_duration_label(params)}")

    # 竞价
    if params["competitive_bonus"]:
        lines.append(f"竞价：{params['competitive_bonus']} 蝌蚪")

    # 奖励
    if params["reward_bonus"]:
        lines.append(f"奖励：{params['reward_bonus']} 蝌蚪")

    # 奖励人数
    if params["reward_user_num"]:
        lines.append(f"奖励人数：{params['reward_user_num']}人")

    return "\n".join(lines)


def _promotion_type_label(params: dict[str, str]) -> str:
    return "Free" if params["promotion_type"] == "2" else "2X Free"


def _duration_label(params: dict[str, str]) -> str:
    hours = int(params["duration"])
    if hours % 24 == 0:
        return f"{hours // 24} 天"
    return f"{hours} 小时"


def _format_bonus_amount(value: Any) -> str:
    try:
        return f"{int(str(value).replace(',', '').strip()):,}"
    except (TypeError, ValueError):
        return str(value)


def _duration_seconds(value: Any, default: int) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return default
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", text)
    if not match:
        return default
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return max(0, min(amount * multiplier, 30 * 86400))


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds >= 86400:
        days, rest = divmod(seconds, 86400)
        hours = rest // 3600
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        minutes = rest // 60
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    if seconds >= 60:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes}分钟{rest}秒" if rest else f"{minutes}分钟"
    return f"{seconds}秒"


def _details_url(site_url: str, torrent_id: str) -> str:
    return f"{site_url}/details.php?id={torrent_id}"


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_meta_text(value: Any) -> str:
    text = str(value or "")
    if "<" in text and ">" in text:
        text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
    return _compact_text(html_unescape(text))


def _first_meta_value(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        lowered = {str(key).lower(): value for key, value in payload.items()}
        for key in keys:
            value = lowered.get(key)
            if value not in (None, ""):
                return _clean_meta_text(value)
        for value in payload.values():
            found = _first_meta_value(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _first_meta_value(item, keys)
            if found:
                return found
    return ""


def _extract_torrent_meta(payload: Any) -> dict[str, str]:
    return {
        "title": _first_meta_value(
            payload,
            (
                "title",
                "name",
                "torrent_title",
                "torrent_name",
                "subject",
                "filename",
            ),
        ),
        "subtitle": _first_meta_value(
            payload,
            (
                "subtitle",
                "sub_title",
                "subheading",
                "small_descr",
                "small_descr_html",
                "small_descr_plain",
                "small_desc",
                "small_desc_html",
                "small_desc_plain",
                "small_description",
                "secondary_title",
                "description",
                "descr_html",
                "descr",
                "intro",
            ),
        ),
    }


class _TorrentDetailsHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_chunks: list[str] = []
        self.title_chunks: list[str] = []
        self.h1_chunks: list[str] = []
        self.rows: list[list[str]] = []
        self._in_title = False
        self._h1_depth = 0
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._h1_depth += 1
        elif tag == "tr":
            self._finish_cell()
            self._finish_row()
            self._current_row = []
        elif tag in {"td", "th"}:
            self._finish_cell()
            if self._current_row is None:
                self._current_row = []
            self._current_cell = []
        if tag in {"br", "div", "p", "tr", "td", "th", "li", "h1", "h2", "h3"}:
            self.text_chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._h1_depth > 0:
            self._h1_depth -= 1
        elif tag in {"td", "th"}:
            self._finish_cell()
        elif tag == "tr":
            self._finish_cell()
            self._finish_row()
        if tag in {"div", "p", "tr", "td", "th", "li", "h1", "h2", "h3"}:
            self.text_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        text = _compact_text(data)
        if not text:
            return
        self.text_chunks.append(text)
        if self._in_title:
            self.title_chunks.append(text)
        if self._h1_depth > 0:
            self.h1_chunks.append(text)
        if self._current_cell is not None:
            self._current_cell.append(text)

    def finish(self) -> None:
        self._finish_cell()
        self._finish_row()

    def _finish_cell(self) -> None:
        if self._current_cell is None:
            return
        cell = _compact_text(" ".join(self._current_cell))
        if cell and self._current_row is not None:
            self._current_row.append(cell)
        self._current_cell = None

    def _finish_row(self) -> None:
        if self._current_row is None:
            return
        row = [_compact_text(cell) for cell in self._current_row if _compact_text(cell)]
        if row:
            self.rows.append(row)
        self._current_row = None


def _clean_html_title(value: str) -> str:
    title = _compact_text(value)
    if not title:
        return ""
    title = re.sub(r"\s*\[\s*免费\s*\].*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*剩余时间[:：].*$", "", title)
    for separator in (" - ", " – ", " | ", "::"):
        if separator in title:
            title = title.split(separator, 1)[0].strip()
    return title


_SUBTITLE_LABELS = (
    "副标题",
    "小标题",
    "简介",
    "描述",
    "subtitle",
    "sub title",
    "small descr",
    "small description",
    "small_desc",
    "secondary title",
)


def _split_subtitle_label(value: str) -> tuple[bool, str]:
    text = _compact_text(value)
    if not text:
        return False, ""
    for label in _SUBTITLE_LABELS:
        if re.fullmatch(rf"{re.escape(label)}\s*[:：]?", text, flags=re.IGNORECASE):
            return True, ""
        match = re.match(rf"^{re.escape(label)}\s*[:：]\s*(.+)$", text, flags=re.IGNORECASE)
        if match:
            return True, _compact_text(match.group(1))
    return False, ""


def _clean_subtitle_candidate(value: str, title: str) -> str:
    candidate = _clean_meta_text(value)
    if not candidate:
        return ""
    if _compact_text(candidate) == _compact_text(title):
        return ""
    if any(re.fullmatch(rf"{re.escape(label)}\s*[:：]?", candidate, flags=re.IGNORECASE) for label in _SUBTITLE_LABELS):
        return ""
    return candidate[:500]


def _extract_subtitle_from_rows(rows: list[list[str]], title: str) -> str:
    for row in rows:
        for index, cell in enumerate(row):
            is_label, inline_value = _split_subtitle_label(cell)
            if not is_label:
                continue
            candidate = _clean_subtitle_candidate(inline_value, title)
            if candidate:
                return candidate
            for next_cell in row[index + 1:]:
                candidate = _clean_subtitle_candidate(next_cell, title)
                if candidate:
                    return candidate
    return ""


def _extract_subtitle_from_plain_text(plain: str, title: str) -> str:
    lines = [_compact_text(line) for line in html_unescape(plain).splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        is_label, inline_value = _split_subtitle_label(line)
        if not is_label:
            continue
        candidate = _clean_subtitle_candidate(inline_value, title)
        if candidate:
            return candidate
        if index + 1 < len(lines):
            candidate = _clean_subtitle_candidate(lines[index + 1], title)
            if candidate:
                return candidate
    return ""


def _extract_torrent_meta_from_html(html: str) -> dict[str, str]:
    parser = _TorrentDetailsHTMLParser()
    parser.feed(html or "")
    parser.finish()
    plain = "\n".join(parser.text_chunks)
    title = _clean_html_title(" ".join(parser.h1_chunks))
    if not title:
        title = _clean_html_title(" ".join(parser.title_chunks))

    subtitle = _extract_subtitle_from_rows(parser.rows, title)
    if not subtitle:
        subtitle = _extract_subtitle_from_plain_text(plain, title)

    return {"title": title, "subtitle": subtitle}


def _format_torrent_header(site_url: str, torrent_id: str, meta: dict[str, str]) -> str:
    url = html_escape(urljoin(f"{site_url.rstrip('/')}/", f"details.php?id={torrent_id}"), quote=True)
    title = _compact_text(meta.get("title")) or f"ID {torrent_id}"

    return (
        f"种子：<a href=\"{url}\">{html_escape(title, quote=False)}</a>"
        f"（ID：<code>{html_escape(torrent_id, quote=False)}</code>）"
    )


def _format_promotion_details(meta: dict[str, str], params_desc: str, cost: str) -> str:
    subtitle = _compact_text(meta.get("subtitle"))
    heading = "副标题与促销明细" if subtitle else "促销明细"
    lines: list[str] = []
    if subtitle:
        lines.append(subtitle)
    lines.extend(line for line in str(params_desc or "").splitlines() if _compact_text(line))
    lines.append(f"消耗：{cost} 蝌蚪")
    code = html_escape("\n".join(lines), quote=False)
    language = html_escape(heading, quote=True)
    return f"<blockquote expandable><pre><code class=\"language-{language}\">{code}</code></pre></blockquote>"


def _int_value(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _interaction_args_from_payload(payload: dict[str, Any]) -> list[str]:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    raw_text = str(
        payload.get("message_text")
        or payload.get("text")
        or event.get("text")
        or ""
    ).strip()
    raw_options = payload.get("options") or payload.get("default_options") or ""
    options = str(raw_options or "").strip().split()

    for key in ("torrent_id", "id"):
        value = payload.get(key) or event.get(key)
        if _int_value(value) is not None:
            return [str(value).strip(), *options]

    id_match = re.search(r"(?<!\w)id\s*=\s*(\d+)", raw_text, flags=re.IGNORECASE)
    if id_match:
        tail_options = raw_text[id_match.end():].strip().split()
        return [id_match.group(1), *tail_options, *options]

    first_number = re.search(r"\b(\d{2,})\b", raw_text)
    if first_number:
        tail_options = raw_text[first_number.end():].strip().split()
        return [first_number.group(1), *tail_options, *options]

    return options


def _payload_message_id(payload: dict[str, Any]) -> int | None:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    return _int_value(payload.get("message_id") or event.get("message_id"))


def _redirect_error(status_code: int) -> str:
    return (
        f"站点返回 HTTP {status_code} 重定向，可能已处于置顶状态或站点拒绝重复提交，"
        "本次不再继续处理。"
    )


class _InteractionReplyEvent:
    """让交互 Bot 入口复用命令处理逻辑，只收集最终消息。"""

    def __init__(self) -> None:
        self.outputs: list[tuple[str, dict[str, Any]]] = []
        self.success: bool = False

    async def edit(self, text: str, **kwargs: Any) -> None:
        self.outputs.append((str(text or ""), dict(kwargs or {})))

    async def respond(self, text: str, **kwargs: Any) -> None:
        await self.edit(text, **kwargs)

    async def reply(self, text: str, **kwargs: Any) -> None:
        await self.edit(text, **kwargs)

    @property
    def final_text(self) -> str:
        return self.outputs[-1][0] if self.outputs else ""


@register
class PTPromotePlugin(Plugin):
    key = "pt_promote"
    display_name = "PT 种子促销"
    message_channels = {"outgoing"}
    owner_only = True
    command_config_keys = {"command"}

    def __init__(self) -> None:
        self.commands = {
            "pt": self._handle_promote,
            "促销": self._handle_promote,
            "ptinfo": self._handle_info,
        }

    async def on_startup(self, ctx: PluginContext) -> None:
        command = str((ctx.config or {}).get("command") or "pt").strip() or "pt"
        self.commands = {
            command: self._handle_promote,
            "促销": self._handle_promote,
            "ptinfo": self._handle_info,
        }

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "promote_torrent":
            return None
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        event_type = str(payload.get("event_type") or event.get("type") or "")
        if event_type not in {"keyword", "payment_confirmed", "message"}:
            return []

        args = _interaction_args_from_payload(payload)
        if not args:
            return [
                {
                    "type": "send_message",
                    "text": _status_template(ctx, "❌", "没有种子 ID，请使用：置顶 id=12345"),
                    "reply_to_message_id": _payload_message_id(payload),
                },
                {"type": "no_session"},
            ]

        event = _InteractionReplyEvent()
        await self._handle_promote(ctx.client, event, args, ctx.account_id, ctx)
        text = event.final_text or _status_template(ctx, "❌", "置顶流程没有返回结果，请稍后再试。")
        return [
            {
                "type": "send_message",
                "text": text,
                "reply_to_message_id": _payload_message_id(payload),
            },
            {"type": "result", "success": event.success},
            {"type": "no_session"},
        ]

    async def _handle_promote(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        """处理置顶促销命令。"""
        prefix = _command_prefix()
        command = str((ctx.config or {}).get("command") or "pt").strip() or "pt"
        if not args:
            await event.edit(
                _cfg_template(
                    ctx,
                    "promote_usage_template",
                    PROMOTE_USAGE_TEMPLATE_DEFAULT,
                    {"prefix": prefix, "command": command},
                )
            )
            return

        torrent_id = args[0]
        site_url = ctx.config.get("site_url", "https://www.qingwapt.com").rstrip("/")
        cookie = ctx.config.get("cookie", "")

        if not cookie:
            await event.edit(_status_template(ctx, "❌", "请先配置 Cookie"))
            return

        if ctx.http is None:
            await event.edit(_status_template(ctx, "❌", "缺少 external_http 权限"))
            return

        guard = await self._claim_torrent_guard(ctx, torrent_id)
        if not guard["allowed"]:
            await event.edit(str(guard["message"]))
            return

        # 解析参数
        params = _parse_args(args[1:])
        params_desc = _format_params(params)

        await event.edit(
            _status_template(
                ctx,
                "⏳",
                "正在获取 ID 为 {torrent_id} 的种子的促销信息...",
                {"torrent_id": torrent_id},
            )
        )

        try:
            # Step 1: 获取促销信息
            info_result = await self._get_promotion_info(ctx, site_url, cookie, torrent_id)
            if not info_result["success"]:
                await event.edit(
                    _status_template(
                        ctx,
                        "❌",
                        "ID 为 {torrent_id} 的种子当前不符合促销条件：{error}",
                        {"torrent_id": torrent_id, "error": info_result["error"]},
                    )
                )
                return

            is_exists = info_result["is_exists"]
            torrent_meta = info_result.get("torrent_meta", {})
            if not torrent_meta.get("title") or not torrent_meta.get("subtitle"):
                fetched_meta = await self._fetch_torrent_meta(ctx, site_url, cookie, torrent_id)
                torrent_meta = {**fetched_meta, **{key: value for key, value in torrent_meta.items() if value}}
            if str(is_exists) == "1":
                await self._mark_torrent_promoted(ctx, torrent_id)
                await event.edit(
                    _status_template(
                        ctx,
                        "ℹ️",
                        "ID 为 {torrent_id} 的种子已处于置顶状态或 12 小时内已置顶过，本次不再处理。",
                        {"torrent_id": torrent_id},
                    )
                )
                return

            # Step 2: 预计算消耗
            await event.edit(
                _cfg_template(
                    ctx,
                    "promote_ready_template",
                    PROMOTE_READY_TEMPLATE_DEFAULT,
                    {"torrent_id": torrent_id, "params": params_desc},
                )
            )
            calc_result = await self._calculate_cost(ctx, site_url, cookie, torrent_id, params, is_exists)
            if not calc_result["success"]:
                await event.edit(
                    _status_template(
                        ctx,
                        "❌",
                        "计算消耗失败：{error}",
                        {"torrent_id": torrent_id, "error": calc_result["error"]},
                    )
                )
                return

            cost = _format_bonus_amount(calc_result["cost_bonus"])
            expression = calc_result["expression"]

            # Step 3: 确认促销
            await event.edit(
                _cfg_template(
                    ctx,
                    "promote_confirming_template",
                    PROMOTE_CONFIRMING_TEMPLATE_DEFAULT,
                    {
                        "torrent_id": torrent_id,
                        "params": params_desc,
                        "cost": cost,
                        "expression": expression,
                    },
                )
            )
            confirm_result = await self._confirm_promotion(ctx, site_url, cookie, torrent_id, params, is_exists)

            if confirm_result["success"]:
                await self._mark_torrent_promoted(ctx, torrent_id)
                if isinstance(event, _InteractionReplyEvent):
                    event.success = True
                await event.edit(
                    _cfg_template(
                        ctx,
                        "promote_success_template",
                        PROMOTE_SUCCESS_TEMPLATE_DEFAULT,
                        {
                            "torrent_id": torrent_id,
                            "details_url": _details_url(site_url, torrent_id),
                            "torrent_header": _format_torrent_header(site_url, torrent_id, torrent_meta),
                            "promotion_details": _format_promotion_details(torrent_meta, params_desc, cost),
                            "title": html_escape(_compact_text(torrent_meta.get("title")) or f"ID {torrent_id}", quote=False),
                            "subtitle": html_escape(_compact_text(torrent_meta.get("subtitle")), quote=False),
                            "params": html_escape(params_desc, quote=False),
                            "cost": html_escape(cost, quote=False),
                            "expression": html_escape(str(expression or ""), quote=False),
                        },
                    ),
                    parse_mode="html",
                )
            else:
                await event.edit(
                    _status_template(
                        ctx,
                        "❌",
                        "置顶失败：{error}",
                        {"torrent_id": torrent_id, "error": confirm_result["error"]},
                    )
                )

        except Exception as e:
            await event.edit(
                _status_template(
                    ctx,
                    "❌",
                    "发生错误：{error}",
                    {"torrent_id": torrent_id, "error": str(e)[:200]},
                )
            )
        finally:
            await self._release_torrent_guard(ctx, str(guard.get("lock_key") or ""))

    async def _handle_info(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        """查询种子促销历史。"""
        if not args:
            await event.edit(
                _cfg_template(
                    ctx,
                    "info_usage_template",
                    "用法：{prefix}ptinfo <种子ID>",
                    {"prefix": _command_prefix(), "command": "ptinfo"},
                )
            )
            return

        torrent_id = args[0]
        site_url = ctx.config.get("site_url", "https://www.qingwapt.com").rstrip("/")
        cookie = ctx.config.get("cookie", "")

        if not cookie:
            await event.edit(_status_template(ctx, "❌", "请先配置 Cookie"))
            return

        if ctx.http is None:
            await event.edit(_status_template(ctx, "❌", "缺少 external_http 权限"))
            return

        await event.edit(
            _status_template(
                ctx,
                "⏳",
                "正在查询 ID 为 {torrent_id} 的种子是否符合促销条件...",
                {"torrent_id": torrent_id},
            )
        )

        try:
            url = f"{site_url}/plugin/sticky-promotion-history?torrent_id={torrent_id}"
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            response = await ctx.http.get(url, headers=headers)

            if response.status_code == 200:
                await event.edit(
                    _cfg_template(
                        ctx,
                        "info_ok_template",
                        INFO_OK_TEMPLATE_DEFAULT,
                        {
                            "torrent_id": torrent_id,
                            "details_url": _details_url(site_url, torrent_id),
                        },
                    )
                )
            else:
                await event.edit(
                    _status_template(
                        ctx,
                        "❌",
                        "查询失败：HTTP {status_code}",
                        {"torrent_id": torrent_id, "status_code": response.status_code},
                    )
                )

        except Exception as e:
            await event.edit(
                _status_template(
                    ctx,
                    "❌",
                    "查询失败：{error}",
                    {"torrent_id": torrent_id, "error": str(e)[:200]},
                )
            )

    async def _get_promotion_info(
        self, ctx: PluginContext, site_url: str, cookie: str, torrent_id: str,
    ) -> dict[str, Any]:
        """获取促销信息。"""
        url = f"{site_url}/plugin/sticky-promotion-info"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        response = await ctx.http.get(url, params={"torrent_id": torrent_id}, headers=headers)

        if response.status_code != 200:
            if response.status_code in {301, 302, 303, 307, 308}:
                return {"success": False, "error": _redirect_error(response.status_code)}
            return {"success": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        is_exists = payload.get("is_exists", 0)
        return {
            "success": True,
            "is_exists": is_exists,
            "torrent_meta": _extract_torrent_meta(payload),
        }

    async def _fetch_torrent_meta(
        self, ctx: PluginContext, site_url: str, cookie: str, torrent_id: str,
    ) -> dict[str, str]:
        """从详情页尽量补齐标题/副标题；失败时不阻断置顶流程。"""
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        try:
            response = await ctx.http.get(_details_url(site_url, torrent_id), headers=headers)
        except Exception:
            return {}
        if response.status_code != 200:
            return {}
        return _extract_torrent_meta_from_html(response.text)

    async def _calculate_cost(
        self, ctx: PluginContext, site_url: str, cookie: str,
        torrent_id: str, params: dict[str, str], is_exists: int,
    ) -> dict[str, Any]:
        """预计算消耗。"""
        request_params = {
            "torrent_id": torrent_id,
            "promotion_type": params["promotion_type"],
            "duration": params["duration"],
            "competitive_bonus": params["competitive_bonus"] or "0",
            "reward_bonus": params["reward_bonus"] or "0",
            "reward_user_num": params["reward_user_num"] or "0",
            "__just_calculate": "1",
        }

        url = f"{site_url}/plugin/sticky-promotion-append" if is_exists == 1 else f"{site_url}/plugin/sticky-promotion"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await ctx.http.post(url, params=request_params, headers=headers)

        if response.status_code != 200:
            if response.status_code in {301, 302, 303, 307, 308}:
                return {"success": False, "error": _redirect_error(response.status_code)}
            return {"success": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        return {
            "success": True,
            "cost_bonus": data.get("data", {}).get("cost_bonus", "未知"),
            "expression": data.get("data", {}).get("expression", ""),
        }

    async def _confirm_promotion(
        self, ctx: PluginContext, site_url: str, cookie: str,
        torrent_id: str, params: dict[str, str], is_exists: int,
    ) -> dict[str, Any]:
        """确认促销。"""
        request_params = {
            "torrent_id": torrent_id,
            "promotion_type": params["promotion_type"],
            "duration": params["duration"],
            "competitive_bonus": params["competitive_bonus"] or "0",
            "reward_bonus": params["reward_bonus"] or "0",
            "reward_user_num": params["reward_user_num"] or "0",
        }

        url = f"{site_url}/plugin/sticky-promotion-append" if is_exists == 1 else f"{site_url}/plugin/sticky-promotion"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await ctx.http.post(url, params=request_params, headers=headers)

        if response.status_code != 200:
            if response.status_code in {301, 302, 303, 307, 308}:
                return {"success": False, "error": _redirect_error(response.status_code)}
            return {"success": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        return {"success": True}

    def _torrent_guard_keys(self, ctx: PluginContext, torrent_id: str) -> tuple[str, str]:
        base = f"pt_promote:{ctx.account_id}:{torrent_id}"
        return f"{base}:lock", f"{base}:cooldown"

    async def _claim_torrent_guard(self, ctx: PluginContext, torrent_id: str) -> dict[str, Any]:
        redis = ctx.redis
        if redis is None:
            return {"allowed": True, "lock_key": ""}
        lock_key, cooldown_key = self._torrent_guard_keys(ctx, torrent_id)
        ttl = getattr(redis, "ttl", None)
        remaining = 0
        try:
            value = await redis.get(cooldown_key)
            if value:
                if callable(ttl):
                    remaining = int(await ttl(cooldown_key) or 0)
                message = _status_template(
                    ctx,
                    "ℹ️",
                    "种子 ID {torrent_id} 已处于置顶状态或 12 小时内已置顶过，剩余约 {remaining}，本次不再处理。",
                    {"torrent_id": torrent_id, "remaining": _format_duration(remaining)},
                )
                return {"allowed": False, "message": message, "lock_key": ""}
            claimed = await redis.set(lock_key, "1", ex=300, nx=True)
            if not claimed:
                return {
                    "allowed": False,
                    "message": _status_template(
                        ctx,
                        "⏳",
                        "种子 ID {torrent_id} 正在处理，请不要重复触发。",
                        {"torrent_id": torrent_id},
                    ),
                    "lock_key": "",
                }
        except Exception:
            return {"allowed": True, "lock_key": ""}
        return {"allowed": True, "lock_key": lock_key}

    async def _mark_torrent_promoted(self, ctx: PluginContext, torrent_id: str) -> None:
        redis = ctx.redis
        if redis is None:
            return
        _, cooldown_key = self._torrent_guard_keys(ctx, torrent_id)
        cooldown = _duration_seconds(ctx.config.get("torrent_cooldown_seconds"), 12 * 3600)
        if cooldown <= 0:
            return
        try:
            await redis.set(cooldown_key, "1", ex=cooldown)
        except Exception:
            return

    async def _release_torrent_guard(self, ctx: PluginContext, lock_key: str) -> None:
        redis = ctx.redis
        if redis is None or not lock_key:
            return
        try:
            await redis.delete(lock_key)
        except Exception:
            return


__all__ = ["PTPromotePlugin"]
