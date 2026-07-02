"""快问快答积分淘汰赛插件。"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import random
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.worker.plugins.base import Plugin, PluginContext, register

try:
    from app.worker.command import current_command_prefix
except Exception:  # pragma: no cover - old TelePilot compatibility
    def current_command_prefix(*, fallback: str = ",") -> str:
        return fallback


PLUGIN_VERSION = "1.2.4"
DATA_PATH = Path(__file__).with_name("quickqa_data.json")

CALLBACK_PREFIX = "qqa"
ENTRY_KEY = "join_quick_qa"
ADMIN_ENTRY_KEY = "quick_qa_admin"
DEFAULT_COMMAND = "quickqa"
DEFAULT_START_KEYWORD = "开始答题"
DEFAULT_INITIAL_POINTS = 20
DEFAULT_CORRECT_POINTS = 3
DEFAULT_WRONG_POINTS = 5
DEFAULT_ENTRY_FEE = 100
DEFAULT_REWARD_RATIO = 0.9
DEFAULT_MAX_QUESTIONS_PER_GAME = 50
DEFAULT_QUESTION_TIMEOUT_SECONDS = 45
DEFAULT_SELECTION_TIMEOUT_SECONDS = 120
DEFAULT_MIN_PLAYERS = 2
DEFAULT_MAX_PLAYERS = 30
DEFAULT_MAX_SOURCE_CHARS = 120000
DEFAULT_AI_QUESTION_COUNT = 80
DEFAULT_AI_TIMEOUT_SECONDS = 600
MIN_AI_TIMEOUT_SECONDS = 300
MAX_AI_TIMEOUT_SECONDS = 3600
MAX_AI_QUESTION_COUNT = 200
MAX_SOURCE_CHARS = 800000
MAX_QUESTIONS_PER_GAME = 1000
MAX_PLAYERS = 500

AI_SYSTEM_PROMPT = """你是 TelePilot 快问快答插件的题库整理助手。
你会收到一个网页的纯文本内容。请只基于原文整理适合群聊快问快答的三选一题库。
要求：
1. 输出严格 JSON，不要 Markdown，不要解释。
2. JSON 结构必须是：
{
  "title": "题库标题",
  "summary": "不超过 120 字的来源摘要",
  "questions": [
    {
      "question": "题目正文",
      "options": ["选项 A", "选项 B", "选项 C"],
      "answer_index": 0,
      "explanation": "一句话解释正确答案"
    }
  ]
}
3. 每题必须有且只有 3 个选项，answer_index 只能是 0、1、2。
4. 题目要能从原文明确找到答案，避免主观题、无依据题、过细碎的数字记忆题。
5. 题干和选项要短，适合 Telegram 按钮展示。"""


@dataclass
class QAQuestion:
    question: str
    options: list[str]
    answer_index: int
    explanation: str = ""


@dataclass
class KnowledgeBase:
    kb_id: str
    title: str
    url: str
    summary: str = ""
    questions: list[QAQuestion] = field(default_factory=list)
    enabled: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class Player:
    user_id: int
    name: str
    points: int
    active: bool = True
    joined_at: float = field(default_factory=time.time)


@dataclass
class CurrentQuestion:
    question_id: str
    question: QAQuestion
    index: int
    answered_user_ids: set[int] = field(default_factory=set)
    resolved: bool = False
    started_at: float = field(default_factory=time.time)
    message_id: int | None = None


@dataclass
class QuickQAGame:
    game_id: str
    account_id: int
    chat_id: int
    entry_fee: int
    initial_points: int
    correct_points: int
    wrong_points: int
    reward_ratio: float
    min_players: int
    max_players: int
    max_questions: int
    question_timeout_seconds: int
    selection_timeout_seconds: int
    host_user_id: int
    host_name: str
    send_via: str = "interaction_bot"
    phase: str = "lobby"
    players: dict[int, Player] = field(default_factory=dict)
    selector_user_id: int = 0
    selected_kb_ids: set[str] = field(default_factory=set)
    question_pool: list[QAQuestion] = field(default_factory=list)
    question_index: int = -1
    current_question: CurrentQuestion | None = None
    started_at: float = field(default_factory=time.time)
    lobby_message_id: int | None = None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "y", "启用"}:
        return True
    if normalized in {"0", "false", "no", "off", "n", "停用"}:
        return False
    return default


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    parsed = _int(value, default)
    return min(max(parsed, minimum), maximum)


def _ai_timeout_seconds(config: dict[str, Any] | None) -> int:
    raw = _int((config or {}).get("ai_timeout_seconds"), DEFAULT_AI_TIMEOUT_SECONDS)
    if raw < MIN_AI_TIMEOUT_SECONDS:
        return DEFAULT_AI_TIMEOUT_SECONDS
    return min(raw, MAX_AI_TIMEOUT_SECONDS)


def _safe_text(value: Any, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _html(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _code(value: Any) -> str:
    return f"<code>{_html(value)}</code>"


def _command_prefix() -> str:
    return current_command_prefix(fallback=",")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


async def _progress_log(ctx: PluginContext, level: str, message: str, **detail: Any) -> None:
    writer = getattr(ctx, "log", None)
    if writer is None:
        return
    await writer(level, message, plugin_key="quick_qa", **detail)


def _source(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("source"))


def _event(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("event"))


def _message(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("message"))


def _actor(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("actor"))


def _payment(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("payment"))


def _event_type(payload: dict[str, Any]) -> str:
    source = _source(payload)
    event = _event(payload)
    trigger = _dict(payload.get("trigger"))
    message = _message(payload)
    return str(
        payload.get("event_type")
        or event.get("type")
        or source.get("type")
        or trigger.get("type")
        or message.get("type")
        or ""
    ).strip()


def _source_channel(payload: dict[str, Any]) -> str:
    source = _source(payload)
    return str(payload.get("source_channel") or source.get("channel") or source.get("type") or "").strip()


def _chat_id(payload: dict[str, Any]) -> int:
    event = _event(payload)
    source = _source(payload)
    message = _message(payload)
    chat = _dict(payload.get("chat"))
    return _int(
        payload.get("chat_id")
        or message.get("chat_id")
        or chat.get("id")
        or event.get("chat_id")
        or source.get("chat_id"),
        0,
    )


def _message_id(payload: dict[str, Any]) -> int | None:
    event = _event(payload)
    source = _source(payload)
    message = _message(payload)
    reply_to = _dict(payload.get("reply_to"))
    value = _int(
        payload.get("message_id")
        or message.get("message_id")
        or reply_to.get("message_id")
        or event.get("message_id")
        or source.get("message_id")
        or payload.get("source_message_id"),
        0,
    )
    return value or None


def _message_text(payload: dict[str, Any]) -> str:
    event = _event(payload)
    source = _source(payload)
    message = _message(payload)
    trigger = _dict(payload.get("trigger"))
    return str(
        payload.get("message_text")
        or payload.get("text")
        or message.get("text")
        or event.get("text")
        or source.get("text")
        or trigger.get("text")
        or ""
    ).strip()


def _callback_data(payload: dict[str, Any]) -> str:
    event = _event(payload)
    source = _source(payload)
    callback = _dict(payload.get("callback_query"))
    return str(
        payload.get("callback_data")
        or callback.get("data")
        or event.get("callback_data")
        or source.get("callback_data")
        or ""
    ).strip()


def _callback_query_id(payload: dict[str, Any]) -> str:
    event = _event(payload)
    source = _source(payload)
    callback = _dict(payload.get("callback_query"))
    return str(
        payload.get("callback_query_id")
        or callback.get("id")
        or event.get("callback_query_id")
        or source.get("callback_query_id")
        or ""
    )


def _actor_id_name(payload: dict[str, Any], *, prefer_payment: bool = False) -> tuple[int, str]:
    actor = _actor(payload)
    event = _event(payload)
    payment = _payment(payload)
    player = _dict(payload.get("player"))
    reply_to = _dict(payload.get("reply_to"))
    if prefer_payment:
        raw_id = (
            payload.get("payer_user_id")
            or payment.get("payer_user_id")
            or _dict(payment.get("payer")).get("user_id")
            or player.get("user_id")
            or reply_to.get("user_id")
            or actor.get("user_id")
            or event.get("user_id")
        )
        raw_name = (
            payload.get("payer_name")
            or payment.get("payer_name")
            or payment.get("payer_display_name")
            or _dict(payment.get("payer")).get("display_name")
            or player.get("display_name")
            or reply_to.get("display_name")
            or actor.get("display_name")
            or event.get("display_name")
        )
    else:
        raw_id = (
            actor.get("user_id")
            or actor.get("id")
            or payload.get("sender_user_id")
            or payload.get("user_id")
            or event.get("user_id")
        )
        raw_name = (
            actor.get("display_name")
            or actor.get("name")
            or payload.get("sender_name")
            or event.get("display_name")
        )
    user_id = _int(raw_id, 0)
    name = _safe_text(raw_name or user_id or "玩家", limit=32)
    return user_id, name or "玩家"


def _payment_amount(payload: dict[str, Any]) -> int:
    event = _event(payload)
    payment = _payment(payload)
    source = _source(payload)
    data = _dict(event.get("data"))
    return _int(
        payload.get("amount")
        or payload.get("payment_amount")
        or payment.get("amount")
        or source.get("amount")
        or data.get("amount"),
        0,
    )


def _send_action(
    text: str,
    *,
    reply_to_message_id: int | None = None,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "html",
    send_via: str | list[str] = "interaction_bot",
    save_message_id_key: str | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "send_message",
        "text": text,
        "send_via": send_via,
    }
    if parse_mode:
        action["parse_mode"] = parse_mode
    if reply_to_message_id:
        action["reply_to_message_id"] = reply_to_message_id
    if reply_markup is not None:
        action["reply_markup"] = reply_markup
    if save_message_id_key:
        action["save_message_id_key"] = save_message_id_key
    return action


def _edit_action(message_id: int | None, text: str, *, reply_markup: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not message_id:
        return None
    action: dict[str, Any] = {
        "type": "edit_message",
        "message_id": int(message_id),
        "text": text,
        "parse_mode": "html",
        "send_via": "interaction_bot",
    }
    if reply_markup is not None:
        action["reply_markup"] = reply_markup
    return action


def _answer_action(payload: dict[str, Any], text: str, *, show_alert: bool = False) -> dict[str, Any]:
    return {
        "type": "answer_callback",
        "callback_query_id": _callback_query_id(payload),
        "text": text,
        "show_alert": show_alert,
    }


def _result_action(success: bool, result: dict[str, Any] | None = None, settlement: dict[str, Any] | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"type": "result", "success": success, "result": result or {}}
    if settlement is not None:
        action["settlement"] = settlement
    return action


def _game_key(account_id: int, chat_id: int) -> str:
    return f"quick_qa:game:{account_id}:{chat_id}"


def _kb_id(url: str, title: str) -> str:
    raw = f"{url}\n{title}\n{time.time()}\n{secrets.token_hex(4)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _draft_id(url: str) -> str:
    raw = f"{url}\n{time.time()}\n{secrets.token_hex(4)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _clean_html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript\b[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)</h[1-6]\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", body, re.IGNORECASE)
    if fence:
        body = fence.group(1).strip()
    if not body.startswith("{"):
        start = body.find("{")
        end = body.rfind("}")
        if start >= 0 and end > start:
            body = body[start : end + 1]
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("AI 返回的 JSON 根节点不是对象")
    return data


def _question_from_dict(value: Any) -> QAQuestion | None:
    if not isinstance(value, dict):
        return None
    question = _safe_text(value.get("question"), limit=180)
    options = [_safe_text(x, limit=80) for x in _list(value.get("options"))]
    answer_index = _int(value.get("answer_index"), -1)
    explanation = _safe_text(value.get("explanation"), limit=180)
    if not question or len(options) != 3 or answer_index not in {0, 1, 2}:
        return None
    if any(not option for option in options):
        return None
    return QAQuestion(question=question, options=options, answer_index=answer_index, explanation=explanation)


def _kb_from_dict(value: Any) -> KnowledgeBase | None:
    if not isinstance(value, dict):
        return None
    questions = [_question_from_dict(item) for item in _list(value.get("questions"))]
    valid_questions = [q for q in questions if q is not None]
    title = _safe_text(value.get("title") or value.get("name"), limit=60)
    url = str(value.get("url") or "").strip()
    kb_id = str(value.get("kb_id") or value.get("id") or "").strip()[:32]
    if not title or not valid_questions:
        return None
    return KnowledgeBase(
        kb_id=kb_id or _kb_id(url, title),
        title=title,
        url=url,
        summary=_safe_text(value.get("summary"), limit=160),
        questions=valid_questions,
        enabled=_bool(value.get("enabled"), True),
        created_at=_float(value.get("created_at"), time.time()),
    )


def _kb_to_json(kb: KnowledgeBase) -> dict[str, Any]:
    return {
        "kb_id": kb.kb_id,
        "title": kb.title,
        "url": kb.url,
        "summary": kb.summary,
        "enabled": kb.enabled,
        "questions": [asdict(q) for q in kb.questions],
        "created_at": kb.created_at,
    }


def _normalized_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(value or "").strip().rstrip("/")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _question_signature(question: QAQuestion) -> str:
    return re.sub(r"[\s\W_]+", "", question.question.lower())


def _merge_questions(existing: list[QAQuestion], incoming: list[QAQuestion], limit: int = 0) -> list[QAQuestion]:
    seen: set[str] = set()
    merged: list[QAQuestion] = []
    for question in [*existing, *incoming]:
        marker = _question_signature(question)
        if not marker or marker in seen:
            continue
        seen.add(marker)
        merged.append(question)
        if limit > 0 and len(merged) >= limit:
            break
    return merged


def _config_kb_items(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    cfg = config or {}
    items: list[dict[str, Any]] = []
    for item in _list(cfg.get("knowledge_bases")):
        if isinstance(item, dict):
            items.append(dict(item))
    raw_config = str(cfg.get("knowledge_bases_json") or "").strip()
    if raw_config:
        try:
            parsed = json.loads(raw_config)
            entries = parsed if isinstance(parsed, list) else _list(_dict(parsed).get("knowledge_bases"))
            for item in entries:
                if isinstance(item, dict):
                    items.append(dict(item))
        except Exception:
            pass
    return _dedupe_kb_items(items)


def _dedupe_kb_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        kb_id = str(item.get("kb_id") or item.get("id") or "").strip()
        marker = kb_id or f"{item.get('url')}\n{item.get('title')}"
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def _find_matching_kb_index(
    items: list[dict[str, Any]],
    *,
    url: str = "",
    title: str = "",
    kb_id: str = "",
) -> int:
    normalized_url = _normalized_url(url)
    normalized_title = _safe_text(title, limit=80).lower()
    for idx, item in enumerate(items):
        kb = _kb_from_dict(item)
        if kb is None:
            continue
        if kb_id and kb.kb_id == kb_id:
            return idx
        if normalized_url and _normalized_url(kb.url) == normalized_url:
            return idx
        if normalized_title and kb.title.lower() == normalized_title:
            return idx
    return -1


def _question_to_json(question: QAQuestion) -> dict[str, Any]:
    return asdict(question)


def _load_store() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {"version": 1, "accounts": {}}
    try:
        data = json.loads(DATA_PATH.read_text("utf-8"))
    except Exception:
        return {"version": 1, "accounts": {}}
    if not isinstance(data, dict):
        return {"version": 1, "accounts": {}}
    data.setdefault("version", 1)
    data.setdefault("accounts", {})
    if not isinstance(data["accounts"], dict):
        data["accounts"] = {}
    return data


def _save_store(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(DATA_PATH)


def _account_store(data: dict[str, Any], account_id: int) -> dict[str, Any]:
    accounts = data.setdefault("accounts", {})
    account = accounts.setdefault(str(account_id), {})
    if not isinstance(account, dict):
        account = {}
        accounts[str(account_id)] = account
    account.setdefault("knowledge_bases", [])
    account.setdefault("drafts", {})
    if not isinstance(account["knowledge_bases"], list):
        account["knowledge_bases"] = []
    if not isinstance(account["drafts"], dict):
        account["drafts"] = {}
    return account


@register
class QuickQAPlugin(Plugin):
    key = "quick_qa"
    display_name = "快问快答"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self.commands = {self._command: self._cmd_handler}
        self._games: dict[int, QuickQAGame] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = str(cfg.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[quick_qa] 已启动，指令：{self._command}，版本：{PLUGIN_VERSION}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._games.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[quick_qa] 已停止")

    def _lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Event Bus 主入口。"""
        return await self._handle_interaction(ctx, payload)

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key not in {ENTRY_KEY, ADMIN_ENTRY_KEY, "start_quick_qa"}:
            return None
        return await self._handle_interaction(ctx, payload)

    async def on_config_action(
        self,
        ctx: PluginContext,
        action_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if action_key != "generate_knowledge_base":
            return None
        action_input = _dict(payload.get("input"))
        current_config = _dict(payload.get("config"))
        ctx.config = {**(ctx.config or {}), **current_config}
        url = str(action_input.get("url") or "").strip()
        title_hint = str(action_input.get("title") or action_input.get("name") or "").strip()
        if not url:
            raise ValueError("请先填写题库来源 URL")

        mode = str(action_input.get("mode") or "append").strip().lower()
        if mode not in {"append", "replace"}:
            mode = "append"
        current_items = _config_kb_items(current_config)
        match_index = _find_matching_kb_index(current_items, url=url, title=title_hint)
        existing_kb = _kb_from_dict(current_items[match_index]) if match_index >= 0 else None
        configured_count = _clamp_int(
            (ctx.config or {}).get("ai_question_count"),
            DEFAULT_AI_QUESTION_COUNT,
            3,
            MAX_AI_QUESTION_COUNT,
        )
        requested_count = _int(action_input.get("question_count"), 0)
        question_count = (
            _clamp_int(requested_count, configured_count, 3, MAX_AI_QUESTION_COUNT)
            if requested_count > 0
            else configured_count
        )
        target_total = _int(action_input.get("target_total"), 0)
        if mode == "append" and existing_kb is not None and target_total > 0:
            remaining = max(0, target_total - len(existing_kb.questions))
            if remaining <= 0:
                return {
                    "message": f"题库：{existing_kb.title} 已有 {len(existing_kb.questions)} 题，已达到目标题数。",
                    "config_patch": {"knowledge_bases": current_items},
                }
            question_count = min(MAX_AI_QUESTION_COUNT, max(3, remaining))

        draft = await self._generate_kb_draft(
            ctx,
            url,
            title_hint,
            question_count=question_count,
            existing_questions=existing_kb.questions if mode == "append" and existing_kb is not None else None,
        )
        kb = _kb_from_dict({**draft, "enabled": True})
        if kb is None:
            raise RuntimeError("AI 返回的题库不可用")

        match_index = _find_matching_kb_index(current_items, url=url, title=title_hint or kb.title, kb_id=kb.kb_id)
        added_count = len(kb.questions)
        if mode == "append" and match_index >= 0:
            existing = _kb_from_dict(current_items[match_index])
            if existing is not None:
                merged_questions = _merge_questions(existing.questions, kb.questions)
                added_count = max(0, len(merged_questions) - len(existing.questions))
                kb = KnowledgeBase(
                    kb_id=existing.kb_id,
                    title=kb.title or existing.title,
                    url=kb.url or existing.url,
                    summary=kb.summary or existing.summary,
                    questions=merged_questions,
                    enabled=existing.enabled,
                    created_at=existing.created_at,
                )
                current_items[match_index] = _kb_to_json(kb)
            else:
                current_items.append(_kb_to_json(kb))
        elif match_index >= 0:
            current_items[match_index] = _kb_to_json(kb)
        else:
            current_items.append(_kb_to_json(kb))
        action_text = "已增量补充" if mode == "append" and match_index >= 0 else "已生成题库"
        return {
            "message": f"{action_text}：{kb.title}（新增 {added_count} 题，当前 {len(kb.questions)} 题），请保存配置后生效。",
            "config_patch": {"knowledge_bases": current_items},
        }

    async def _handle_interaction(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = _event_type(payload)
        chat_id = _chat_id(payload)
        if not chat_id:
            return []
        if event_type == "payment_confirmed":
            return await self._handle_payment(ctx, payload, chat_id)
        if event_type == "callback_query" or _callback_data(payload):
            return await self._handle_callback(ctx, payload, chat_id)
        if event_type in {"message", "keyword", "command"}:
            text = _message_text(payload)
            if self._looks_like_admin_text(text):
                return []
            if self._should_create_lobby_from_keyword(ctx, payload, text, event_type):
                return await self._create_lobby(ctx, payload, chat_id, self._cfg_entry_fee(ctx, payload))
            return []
        if event_type == "session_close":
            async with self._lock(chat_id):
                self._games.pop(chat_id, None)
            return [{"type": "end_session"}]
        return []

    def _looks_like_admin_text(self, text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return False
        if normalized.startswith("/"):
            normalized = normalized[1:]
        prefix = _command_prefix()
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
        return normalized == self._command or normalized.startswith(f"{self._command} ")

    async def _handle_admin_interaction(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        chat_id: int,
        text: str,
    ) -> list[dict[str, Any]]:
        args = self._args_from_text(text)
        if args and args[0] == self._command:
            args = args[1:]
        reply_to = _message_id(payload)
        if args[:2] == ["kb", "import"] and len(args) >= 3:
            return await self._import_kb_actions(ctx, args[2], " ".join(args[3:]), reply_to_message_id=reply_to)
        if args[:2] == ["kb", "list"]:
            return [_send_action(self._render_kb_list(ctx), reply_to_message_id=reply_to)]
        if args[:2] == ["kb", "save"] and len(args) >= 3:
            text = self._save_draft(ctx.account_id, args[2])
            return [_send_action(text, reply_to_message_id=reply_to)]
        if args[:2] == ["kb", "delete"] and len(args) >= 3:
            text = self._delete_kb(ctx.account_id, args[2])
            return [_send_action(text, reply_to_message_id=reply_to)]
        if args and args[0] in {"start", "开始"}:
            return await self._begin_selection(ctx, payload, chat_id)
        if args and args[0] in {"cancel", "取消"}:
            async with self._lock(chat_id):
                self._games.pop(chat_id, None)
            return [_send_action("已取消当前快问快答游戏。", reply_to_message_id=reply_to), {"type": "end_session"}]
        if args and args[0].isdigit():
            payload = dict(payload)
            if len(args) >= 2 and args[1].isdigit():
                module_config = dict(_dict(payload.get("module_config")))
                module_config["max_questions_per_game"] = _int(args[1], DEFAULT_MAX_QUESTIONS_PER_GAME)
                payload["module_config"] = module_config
            return await self._create_lobby(ctx, payload, chat_id, _int(args[0], DEFAULT_ENTRY_FEE))
        return [_send_action(self._usage(ctx), reply_to_message_id=reply_to)]

    def _args_from_text(self, text: str) -> list[str]:
        normalized = text.strip()
        prefix = _command_prefix()
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
        if normalized.startswith("/"):
            normalized = normalized[1:].strip()
        return [x for x in normalized.split() if x]

    def _should_create_lobby_from_keyword(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        text: str,
        event_type: str,
    ) -> bool:
        if not text:
            return False
        if text == self._cfg_start_keyword(ctx, payload):
            return True
        if event_type == "keyword":
            return True
        trigger = _dict(payload.get("trigger"))
        if str(trigger.get("type") or trigger.get("event_type") or "").strip() == "keyword":
            return True
        return _source_channel(payload) == "interaction_bot" and event_type == "message"

    async def _handle_callback(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        data = _callback_data(payload)
        parts = data.split(":")
        if len(parts) < 2 or parts[0] != CALLBACK_PREFIX:
            return []
        action = parts[1]
        if action == "save" and len(parts) >= 3:
            text = self._save_draft(ctx.account_id, parts[2])
            edit = _edit_action(_message_id(payload), text)
            return [_answer_action(payload, "已处理"), *([edit] if edit else [_send_action(text)])]
        if action == "drop" and len(parts) >= 3:
            text = self._drop_draft(ctx.account_id, parts[2])
            edit = _edit_action(_message_id(payload), text)
            return [_answer_action(payload, "已取消"), *([edit] if edit else [_send_action(text)])]
        async with self._lock(chat_id):
            game = self._find_game(parts[2] if len(parts) > 2 else "", chat_id)
            if game is None:
                return [_answer_action(payload, "游戏已结束或不存在", show_alert=True)]
            if action == "start":
                return await self._begin_selection_locked(ctx, payload, game)
            if action == "kb" and len(parts) >= 4:
                return self._toggle_kb(ctx, payload, game, parts[3])
            if action == "go":
                return await self._start_questions_locked(ctx, payload, game)
            if action == "ans" and len(parts) >= 5:
                return await self._answer_question_locked(ctx, payload, game, parts[3], _int(parts[4], -1))
        return []

    def _find_game(self, game_id: str, chat_id: int) -> QuickQAGame | None:
        game = self._games.get(chat_id)
        if game is None:
            return None
        if game_id and game.game_id != game_id:
            return None
        return game

    async def _handle_payment(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        amount = _payment_amount(payload)
        entry_fee = self._cfg_entry_fee(ctx, payload)
        if amount < entry_fee:
            return [
                _send_action(
                    f"报名金额不足：本局门槛是 {_code(entry_fee)}，当前到账 {_code(amount)}。",
                    reply_to_message_id=_message_id(payload),
                    send_via="userbot_reply" if _source_channel(payload) == "userbot" else "interaction_bot",
                )
            ]

        async with self._lock(chat_id):
            game = self._games.get(chat_id)
            if game is None or game.phase == "finished":
                return []
            if game.phase != "lobby":
                return [_send_action("本局已经开始，无法继续报名。", reply_to_message_id=_message_id(payload), send_via=game.send_via)]
            if amount < game.entry_fee:
                return [
                    _send_action(
                        f"本局门槛是 {_code(game.entry_fee)}，金额不足。",
                        reply_to_message_id=_message_id(payload),
                        send_via=game.send_via,
                    )
                ]
            user_id, name = _actor_id_name(payload, prefer_payment=True)
            if not user_id:
                return [_send_action("没有识别到付款玩家，无法报名。", reply_to_message_id=_message_id(payload), send_via=game.send_via)]
            if user_id in game.players:
                return [
                    _send_action(
                        f"{_html(game.players[user_id].name)} 已在本局中。",
                        reply_to_message_id=_message_id(payload),
                        send_via=game.send_via,
                    )
                ]
            if len(game.players) >= game.max_players:
                return [_send_action("本局人数已满，无法继续报名。", reply_to_message_id=_message_id(payload), send_via=game.send_via)]
            game.players[user_id] = Player(user_id=user_id, name=name, points=game.initial_points)
            actions: list[dict[str, Any]] = [
                _send_action(
                    f"{_html(name)} 报名成功，初始积分 {_code(game.initial_points)}。",
                    reply_to_message_id=_message_id(payload),
                    send_via=game.send_via,
                )
            ]
            actions.append(_send_action(self._render_lobby(game), reply_markup=self._lobby_markup(game), send_via=game.send_via))
            return actions

    async def _create_lobby(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        chat_id: int,
        entry_fee: int,
    ) -> list[dict[str, Any]]:
        actor_id, actor_name = _actor_id_name(payload)
        async with self._lock(chat_id):
            current = self._games.get(chat_id)
            if current and current.phase != "finished":
                return [_send_action("当前聊天已有进行中的快问快答。", reply_to_message_id=_message_id(payload))]
            game = self._new_game(ctx, payload, chat_id, entry_fee, actor_id, actor_name)
            self._games[chat_id] = game
            return [
                _send_action(
                    self._render_lobby(game),
                    reply_to_message_id=_message_id(payload),
                    reply_markup=self._lobby_markup(game),
                    send_via=game.send_via,
                )
            ]

    def _new_game(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        chat_id: int,
        entry_fee: int,
        host_user_id: int,
        host_name: str,
    ) -> QuickQAGame:
        cfg = {**(ctx.config or {}), **_dict(payload.get("module_config"))}
        initial_points = _clamp_int(cfg.get("initial_points"), DEFAULT_INITIAL_POINTS, 1, 1000)
        correct_points = _clamp_int(cfg.get("correct_points"), DEFAULT_CORRECT_POINTS, 0, 1000)
        wrong_points = _clamp_int(cfg.get("wrong_points"), DEFAULT_WRONG_POINTS, 1, 1000)
        reward_ratio = min(max(_float(cfg.get("reward_ratio"), DEFAULT_REWARD_RATIO), 0.01), 1.0)
        source_channel = _source_channel(payload)
        return QuickQAGame(
            game_id=secrets.token_hex(4),
            account_id=ctx.account_id,
            chat_id=chat_id,
            entry_fee=max(1, entry_fee),
            initial_points=initial_points,
            correct_points=correct_points,
            wrong_points=wrong_points,
            reward_ratio=reward_ratio,
            min_players=_clamp_int(cfg.get("min_players"), DEFAULT_MIN_PLAYERS, 2, 100),
            max_players=_clamp_int(cfg.get("max_players"), DEFAULT_MAX_PLAYERS, 2, MAX_PLAYERS),
            max_questions=_clamp_int(
                cfg.get("max_questions_per_game"),
                DEFAULT_MAX_QUESTIONS_PER_GAME,
                1,
                MAX_QUESTIONS_PER_GAME,
            ),
            question_timeout_seconds=_clamp_int(
                cfg.get("question_timeout_seconds"),
                DEFAULT_QUESTION_TIMEOUT_SECONDS,
                5,
                1800,
            ),
            selection_timeout_seconds=_clamp_int(
                cfg.get("selection_timeout_seconds"),
                DEFAULT_SELECTION_TIMEOUT_SECONDS,
                10,
                3600,
            ),
            host_user_id=host_user_id,
            host_name=host_name,
            send_via="userbot_reply" if source_channel == "userbot" or _event_type(payload) == "command" else "interaction_bot",
        )

    async def _begin_selection(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        async with self._lock(chat_id):
            game = self._games.get(chat_id)
            if game is None:
                return [_send_action("还没有快问快答大厅，请先创建或通过转账报名。", reply_to_message_id=_message_id(payload))]
            return await self._begin_selection_locked(ctx, payload, game)

    async def _begin_selection_locked(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        game: QuickQAGame,
    ) -> list[dict[str, Any]]:
        if game.phase not in {"lobby", "selecting"}:
            return [_answer_action(payload, "当前阶段不能选择题库", show_alert=True)] if _callback_query_id(payload) else []
        if len(game.players) < game.min_players:
            text = f"人数不足，至少需要 {game.min_players} 人，当前 {len(game.players)} 人。"
            return [_answer_action(payload, text, show_alert=True)] if _callback_query_id(payload) else [_send_action(text)]
        kbs = self._available_kbs(ctx)
        if not kbs:
            text = "还没有可用题库。请先在 TelePilot Web 配置页添加 URL，获取并整理为题库后保存配置。"
            return [_answer_action(payload, text, show_alert=True)] if _callback_query_id(payload) else [_send_action(text)]
        selector = random.choice(list(game.players.values()))
        game.selector_user_id = selector.user_id
        game.phase = "selecting"
        game.selected_kb_ids = set()
        actions: list[dict[str, Any]] = []
        if _callback_query_id(payload):
            actions.append(_answer_action(payload, "已进入题库选择"))
        actions.append(_send_action(self._render_kb_selection(game, kbs), reply_markup=self._kb_markup(game, kbs)))
        self._track(asyncio.create_task(self._selection_timeout(ctx, game.chat_id, game.game_id, game.selection_timeout_seconds)))
        return actions

    def _toggle_kb(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        game: QuickQAGame,
        kb_id: str,
    ) -> list[dict[str, Any]]:
        actor_id, _ = _actor_id_name(payload)
        if actor_id != game.selector_user_id:
            return [_answer_action(payload, "只有被抽中的玩家可以选择题库", show_alert=True)]
        if game.phase != "selecting":
            return [_answer_action(payload, "当前不能修改题库", show_alert=True)]
        if kb_id in game.selected_kb_ids:
            game.selected_kb_ids.remove(kb_id)
        else:
            game.selected_kb_ids.add(kb_id)
        kbs = self._available_kbs(ctx)
        edit = _edit_action(_message_id(payload), self._render_kb_selection(game, kbs), reply_markup=self._kb_markup(game, kbs))
        return [_answer_action(payload, "已更新选择"), *([edit] if edit else [])]

    async def _start_questions_locked(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        game: QuickQAGame,
    ) -> list[dict[str, Any]]:
        actor_id, _ = _actor_id_name(payload)
        if actor_id != game.selector_user_id:
            return [_answer_action(payload, "只有被抽中的玩家可以开始出题", show_alert=True)]
        if game.phase != "selecting":
            return [_answer_action(payload, "当前阶段不能开始出题", show_alert=True)]
        kbs = self._available_kbs(ctx)
        selected = [kb for kb in kbs if not game.selected_kb_ids or kb.kb_id in game.selected_kb_ids]
        questions = [q for kb in selected for q in kb.questions]
        if not questions:
            return [_answer_action(payload, "选中的题库没有可用题目", show_alert=True)]
        random.shuffle(questions)
        game.question_pool = questions[: game.max_questions]
        game.question_index = -1
        game.phase = "playing"
        actions: list[dict[str, Any]] = [_answer_action(payload, "题库已确认，开始出题")]
        edit = _edit_action(_message_id(payload), self._render_game_start(game, selected), reply_markup=None)
        if edit:
            actions.append(edit)
        actions.extend(await self._next_question_actions(ctx, game))
        return actions

    async def _answer_question_locked(
        self,
        ctx: PluginContext,
        payload: dict[str, Any],
        game: QuickQAGame,
        question_id: str,
        option_index: int,
    ) -> list[dict[str, Any]]:
        actor_id, actor_name = _actor_id_name(payload)
        player = game.players.get(actor_id)
        if player is None or not player.active:
            return [_answer_action(payload, "你已经不在本局中", show_alert=True)]
        current = game.current_question
        if game.phase != "playing" or current is None or current.question_id != question_id:
            return [_answer_action(payload, "本题已经结束", show_alert=True)]
        if current.resolved:
            return [_answer_action(payload, "本题已经有人答对", show_alert=True)]
        if actor_id in current.answered_user_ids:
            return [_answer_action(payload, "你本题已经答过了", show_alert=True)]
        current.answered_user_ids.add(actor_id)
        answer_ok = option_index == current.question.answer_index
        actions: list[dict[str, Any]] = []
        if answer_ok:
            current.resolved = True
            player.points += game.correct_points
            actions.append(_answer_action(payload, f"答对了，+{game.correct_points} 分"))
            edit = _edit_action(_message_id(payload), self._render_question_result(game, actor_name, True), reply_markup=None)
            if edit:
                actions.append(edit)
            actions.extend(await self._next_question_actions(ctx, game))
            return actions

        player.points -= game.wrong_points
        if player.points <= 0:
            player.points = 0
            player.active = False
        actions.append(_answer_action(payload, f"答错了，-{game.wrong_points} 分", show_alert=True))
        if not player.active:
            actions.append(_send_action(f"{_html(player.name)} 积分扣完，已出局。", send_via=game.send_via))
        survivor = self._single_survivor(game)
        if survivor is not None:
            actions.extend(await self._finish_game_actions(ctx, game, survivor, reason="只剩最后一名玩家"))
            return actions
        alive_answered = all(p.user_id in current.answered_user_ids for p in game.players.values() if p.active)
        if alive_answered:
            current.resolved = True
            edit = _edit_action(_message_id(payload), self._render_question_timeout(game, exhausted=True), reply_markup=None)
            if edit:
                actions.append(edit)
            actions.extend(await self._next_question_actions(ctx, game))
        return actions

    async def _next_question_actions(self, ctx: PluginContext, game: QuickQAGame) -> list[dict[str, Any]]:
        survivor = self._single_survivor(game)
        if survivor is not None:
            return await self._finish_game_actions(ctx, game, survivor, reason="只剩最后一名玩家")
        game.question_index += 1
        if game.question_index >= len(game.question_pool):
            winner = self._highest_unique_player(game)
            if winner is None:
                game.phase = "finished"
                self._games.pop(game.chat_id, None)
                return [_send_action("题库已经用完，当前最高分并列，本局不自动发奖。", send_via=game.send_via), {"type": "end_session"}]
            return await self._finish_game_actions(ctx, game, winner, reason="题库已用完，按当前最高分结算")
        q = game.question_pool[game.question_index]
        current = CurrentQuestion(question_id=secrets.token_hex(4), question=q, index=game.question_index + 1)
        game.current_question = current
        text = self._render_question(game)
        actions = [_send_action(text, reply_markup=self._answer_markup(game, current), send_via=game.send_via)]
        self._track(asyncio.create_task(self._question_timeout(ctx, game.chat_id, game.game_id, current.question_id, game.question_timeout_seconds)))
        return actions

    def _single_survivor(self, game: QuickQAGame) -> Player | None:
        alive = [p for p in game.players.values() if p.active]
        return alive[0] if len(alive) == 1 and len(game.players) >= game.min_players else None

    def _highest_unique_player(self, game: QuickQAGame) -> Player | None:
        alive = [p for p in game.players.values() if p.active]
        if not alive:
            return None
        ordered = sorted(alive, key=lambda p: p.points, reverse=True)
        if len(ordered) > 1 and ordered[0].points == ordered[1].points:
            return None
        return ordered[0]

    async def _finish_game_actions(
        self,
        ctx: PluginContext,
        game: QuickQAGame,
        winner: Player,
        *,
        reason: str,
    ) -> list[dict[str, Any]]:
        game.phase = "finished"
        self._games.pop(game.chat_id, None)
        reward = self._reward_amount(game, winner)
        settlement_mode = str((ctx.config or {}).get("payout_mode") or "announce_only").strip() or "announce_only"
        text = self._render_finish(game, winner, reward, reason)
        return [
            _send_action(text, send_via=game.send_via),
            _result_action(
                True,
                {
                    "status": "finished",
                    "winner_user_id": winner.user_id,
                    "winner_name": winner.name,
                    "amount": reward,
                    "winner_points": winner.points,
                    "entry_fee": game.entry_fee,
                    "participants": len(game.players),
                },
                {
                    "mode": "auto" if settlement_mode == "auto" else "announce_only",
                    "winner_user_id": winner.user_id,
                    "winner_name": winner.name,
                    "amount": reward,
                    "status": "announced",
                    "amount_field": "reward",
                },
            ),
            {"type": "end_session"},
        ]

    def _reward_amount(self, game: QuickQAGame, winner: Player) -> int:
        pool = max(0, game.entry_fee * len(game.players))
        cap = int(round(pool * game.reward_ratio))
        if cap <= 0:
            return 0
        raw = int(round(cap * max(0, winner.points) / max(1, game.initial_points)))
        return min(cap, max(0, raw))

    async def _selection_timeout(self, ctx: PluginContext, chat_id: int, game_id: str, seconds: int) -> None:
        await asyncio.sleep(seconds)
        async with self._lock(chat_id):
            game = self._games.get(chat_id)
            if game is None or game.game_id != game_id or game.phase != "selecting":
                return
            kbs = self._available_kbs(ctx)
            game.selected_kb_ids = set()
            game.question_pool = [q for kb in kbs for q in kb.questions]
            random.shuffle(game.question_pool)
            game.question_pool = game.question_pool[: game.max_questions]
            game.phase = "playing"
            game.question_index = -1
            actions = [_send_action("题库选择超时，默认使用全部已保存题库。", send_via=game.send_via)]
            actions.extend(await self._next_question_actions(ctx, game))
        await self._emit_actions(ctx, actions)

    async def _question_timeout(self, ctx: PluginContext, chat_id: int, game_id: str, question_id: str, seconds: int) -> None:
        await asyncio.sleep(seconds)
        async with self._lock(chat_id):
            game = self._games.get(chat_id)
            current = game.current_question if game else None
            if game is None or game.game_id != game_id or game.phase != "playing":
                return
            if current is None or current.question_id != question_id or current.resolved:
                return
            current.resolved = True
            actions = [_send_action(self._render_question_timeout(game), send_via=game.send_via)]
            actions.extend(await self._next_question_actions(ctx, game))
        await self._emit_actions(ctx, actions)

    async def _emit_actions(self, ctx: PluginContext, actions: list[dict[str, Any]]) -> None:
        messages = getattr(ctx, "messages", None)
        if messages is None:
            return
        for action in actions:
            try:
                if action.get("type") == "send_message" and hasattr(messages, "send"):
                    kwargs = {k: v for k, v in action.items() if k not in {"type"}}
                    await messages.send(**kwargs)
                elif action.get("type") == "edit_message" and hasattr(messages, "edit"):
                    kwargs = {k: v for k, v in action.items() if k not in {"type"}}
                    await messages.edit(**kwargs)
            except Exception:
                continue

    async def _cmd_handler(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        if not args:
            await self._event_edit_or_reply(event, self._usage(ctx))
            return
        if args[:2] == ["kb", "import"] and len(args) >= 3:
            await self._cmd_import_kb(ctx, event, args[2], " ".join(args[3:]))
            return
        if args[:2] == ["kb", "list"]:
            await self._event_edit_or_reply(event, self._render_kb_list(ctx))
            return
        if args[:2] == ["kb", "save"] and len(args) >= 3:
            await self._event_edit_or_reply(event, self._save_draft(ctx.account_id, args[2]))
            return
        if args[:2] == ["kb", "delete"] and len(args) >= 3:
            await self._event_edit_or_reply(event, self._delete_kb(ctx.account_id, args[2]))
            return
        if args[0] in {"cancel", "取消"}:
            chat_id = self._event_chat_id(event)
            if chat_id:
                async with self._lock(chat_id):
                    self._games.pop(chat_id, None)
            await self._event_edit_or_reply(event, "已取消当前聊天的快问快答游戏。")
            return
        if args[0] in {"start", "开始"}:
            chat_id = self._event_chat_id(event)
            if chat_id:
                payload = {
                    "source": {"type": "command", "channel": "userbot"},
                    "actor": {"user_id": self._event_sender_id(event), "display_name": await self._event_sender_name(event)},
                    "chat_id": chat_id,
                    "message_id": self._event_message_id(event),
                }
                actions = await self._begin_selection(ctx, payload, chat_id)
                await self._event_edit_or_reply(event, self._actions_text(actions))
            return
        if args[0].isdigit():
            chat_id = self._event_chat_id(event)
            sender_id = self._event_sender_id(event)
            sender_name = await self._event_sender_name(event)
            if not chat_id:
                return
            payload = {
                "source": {"type": "command", "channel": "userbot"},
                "actor": {"user_id": sender_id, "display_name": sender_name},
                "chat_id": chat_id,
                "message_id": self._event_message_id(event),
            }
            if len(args) >= 2 and args[1].isdigit():
                payload["module_config"] = {"max_questions_per_game": _int(args[1], DEFAULT_MAX_QUESTIONS_PER_GAME)}
            actions = await self._create_lobby(ctx, payload, chat_id, _int(args[0], DEFAULT_ENTRY_FEE))
            await self._event_edit_or_reply(event, self._actions_text(actions))
            return
        await self._event_edit_or_reply(event, self._usage(ctx))

    async def _cmd_import_kb(self, ctx: PluginContext, event: Any, url: str, title_hint: str) -> None:
        await self._event_edit_or_reply(event, "正在获取网页并调用 AI 生成题库，请稍候。")
        try:
            draft = await self._generate_kb_draft(ctx, url, title_hint)
        except Exception as exc:
            await self._event_edit_or_reply(event, f"题库生成失败：{_html(str(exc)[:300])}")
            return
        self._put_draft(ctx.account_id, draft)
        kb = _kb_from_dict(draft)
        if kb is None:
            await self._event_edit_or_reply(event, "AI 返回的题库不可用。")
            return
        await self._event_edit_or_reply(
            event,
            (
                "题库草稿已生成。\n"
                f"草稿 ID：{_code(draft['draft_id'])}\n"
                f"标题：{_html(kb.title)}\n"
                f"题目数：{len(kb.questions)}\n\n"
                f"确认保存：{_code(_command_prefix() + self._command + ' kb save ' + draft['draft_id'])}"
            ),
        )

    async def _import_kb_actions(
        self,
        ctx: PluginContext,
        url: str,
        title_hint: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            draft = await self._generate_kb_draft(ctx, url, title_hint)
        except Exception as exc:
            return [_send_action(f"题库生成失败：{_html(str(exc)[:300])}", reply_to_message_id=reply_to_message_id)]
        self._put_draft(ctx.account_id, draft)
        kb = _kb_from_dict(draft)
        if kb is None:
            return [_send_action("AI 返回的题库不可用。", reply_to_message_id=reply_to_message_id)]
        text = (
            "题库草稿已生成，请确认是否保存。\n"
            f"草稿 ID：{_code(draft['draft_id'])}\n"
            f"标题：{_html(kb.title)}\n"
            f"题目数：{len(kb.questions)}\n"
            f"来源：{_html(kb.url)}\n\n"
            f"{_html(kb.summary)}"
        )
        return [_send_action(text, reply_to_message_id=reply_to_message_id, reply_markup=self._draft_markup(draft["draft_id"]))]

    async def _generate_kb_draft(
        self,
        ctx: PluginContext,
        url: str,
        title_hint: str,
        *,
        question_count: int | None = None,
        existing_questions: list[QAQuestion] | None = None,
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("只支持 http/https URL")
        await _progress_log(ctx, "info", "已读取 URL，开始检查来源域名", step="validate_url", host=parsed.hostname or "")
        self._check_runtime_host_allowlist(ctx, parsed.hostname or "")
        http_client = getattr(ctx, "http", None)
        if http_client is None:
            raise RuntimeError("当前插件没有 ctx.http，请确认 external_http 与 allowed_hosts 已生效")
        ai_client = getattr(ctx, "ai", None)
        complete = getattr(ai_client, "complete", None) if ai_client is not None else None
        if complete is None:
            raise RuntimeError("当前插件没有 ctx.ai，请确认 ai_text 权限和 AI Provider 已配置")
        await _progress_log(ctx, "info", "开始抓取网页内容", step="fetch_url", url=url)
        response = await http_client.get(url)
        status = _int(getattr(response, "status_code", 0), 0)
        if status < 200 or status >= 300:
            raise RuntimeError(f"网页请求失败：HTTP {status}")
        raw_text = str(getattr(response, "text", "") or "")
        await _progress_log(ctx, "info", "网页内容抓取完成，开始清洗正文", step="clean_source", status=status, raw_chars=len(raw_text))
        source_text = _clean_html_to_text(raw_text)
        max_chars = _clamp_int((ctx.config or {}).get("max_source_chars"), DEFAULT_MAX_SOURCE_CHARS, 1000, MAX_SOURCE_CHARS)
        if len(source_text) > max_chars:
            source_text = source_text[:max_chars]
        if len(source_text) < 200:
            raise RuntimeError("网页正文太短，无法整理题库")
        question_count = _clamp_int(
            question_count if question_count is not None else (ctx.config or {}).get("ai_question_count"),
            DEFAULT_AI_QUESTION_COUNT,
            3,
            MAX_AI_QUESTION_COUNT,
        )
        ai_timeout = _ai_timeout_seconds(ctx.config or {})
        await _progress_log(
            ctx,
            "info",
            "正文清洗完成，开始调用 AI 整理题库",
            step="call_ai",
            source_chars=len(source_text),
            question_count=question_count,
            timeout_seconds=ai_timeout,
        )
        system_prompt = str((ctx.config or {}).get("question_generation_prompt") or AI_SYSTEM_PROMPT)
        existing_hint = ""
        if existing_questions:
            sample = "\n".join(f"- {q.question}" for q in existing_questions[:120])
            existing_hint = (
                "\n\n已有题目（请尽量避开重复题干和同义改写）：\n"
                f"{sample}"
            )
        user_prompt = (
            f"来源 URL：{url}\n"
            f"期望题数：{question_count}\n"
            f"标题提示：{title_hint or '自动判断'}\n\n"
            "网页正文：\n"
            f"{source_text}"
            f"{existing_hint}"
        )
        result = await complete(
            system_prompt,
            user_prompt,
            provider=(ctx.config or {}).get("telepilot_provider") or None,
            model=(ctx.config or {}).get("telepilot_model") or None,
            provider_tag="long_context",
            max_tokens=max(6000, min(24000, question_count * 160)),
            timeout_seconds=ai_timeout,
            source="plugin:quick_qa",
        )
        await _progress_log(ctx, "info", "AI 已返回，开始解析题库 JSON", step="parse_ai_result")
        data = _extract_json_object(str(getattr(result, "text", "") or ""))
        questions = [_question_from_dict(item) for item in _list(data.get("questions"))]
        valid_questions = [q for q in questions if q is not None]
        if len(valid_questions) < 3:
            raise RuntimeError("AI 生成的有效题目少于 3 道")
        title = _safe_text(title_hint or data.get("title") or parsed.netloc, limit=60)
        draft = {
            "draft_id": _draft_id(url),
            "kb_id": _kb_id(url, title),
            "title": title,
            "url": url,
            "summary": _safe_text(data.get("summary"), limit=160),
            "questions": [_question_to_json(q) for q in valid_questions[:question_count]],
            "created_at": time.time(),
        }
        await _progress_log(
            ctx,
            "info",
            "题库草稿生成完成",
            step="draft_ready",
            title=title,
            question_count=len(draft["questions"]),
        )
        return draft

    def _check_runtime_host_allowlist(self, ctx: PluginContext, host: str) -> None:
        raw = str((ctx.config or {}).get("allowed_source_hosts") or "").strip()
        if not raw:
            return
        allowed = {item.strip().lower() for item in re.split(r"[,;\s]+", raw) if item.strip()}
        candidate = host.lower().strip(".")
        if candidate not in allowed:
            raise ValueError(f"当前配置不允许抓取 {host}，请先加入允许来源域名")

    def _put_draft(self, account_id: int, draft: dict[str, Any]) -> None:
        store = _load_store()
        account = _account_store(store, account_id)
        account["drafts"][str(draft["draft_id"])] = draft
        _save_store(store)

    def _save_draft(self, account_id: int, draft_id: str) -> str:
        store = _load_store()
        account = _account_store(store, account_id)
        draft = _dict(account["drafts"].pop(str(draft_id), {}))
        kb = _kb_from_dict(draft)
        if kb is None:
            _save_store(store)
            return "没有找到可保存的题库草稿。"
        items = [_kb_from_dict(item) for item in account["knowledge_bases"]]
        existing = [item for item in items if item is not None and item.kb_id != kb.kb_id]
        existing.append(kb)
        account["knowledge_bases"] = [_kb_to_json(item) for item in existing]
        _save_store(store)
        return f"题库已保存：{_html(kb.title)}（{len(kb.questions)} 题，ID {_code(kb.kb_id)}）。"

    def _drop_draft(self, account_id: int, draft_id: str) -> str:
        store = _load_store()
        account = _account_store(store, account_id)
        removed = account["drafts"].pop(str(draft_id), None)
        _save_store(store)
        return "题库草稿已取消。" if removed else "没有找到这个题库草稿。"

    def _delete_kb(self, account_id: int, kb_id: str) -> str:
        store = _load_store()
        account = _account_store(store, account_id)
        before = len(account["knowledge_bases"])
        account["knowledge_bases"] = [
            item for item in account["knowledge_bases"] if str(_dict(item).get("kb_id") or _dict(item).get("id")) != kb_id
        ]
        _save_store(store)
        if len(account["knowledge_bases"]) == before:
            return "没有找到这个题库。"
        return f"题库已删除：{_code(kb_id)}"

    def _available_kbs(self, ctx: PluginContext) -> list[KnowledgeBase]:
        return self._available_kbs_for_account(ctx.account_id, ctx.config or {})

    def _available_kbs_for_account(self, account_id: int, config: dict[str, Any] | None = None) -> list[KnowledgeBase]:
        kbs: list[KnowledgeBase] = []
        store = _load_store()
        account = _account_store(store, account_id)
        for item in account["knowledge_bases"]:
            kb = _kb_from_dict(item)
            if kb is not None and kb.enabled:
                kbs.append(kb)
        for item in _config_kb_items(config):
            kb = _kb_from_dict(item)
            if kb is not None and kb.enabled:
                kbs.append(kb)
        seen: set[str] = set()
        unique: list[KnowledgeBase] = []
        for kb in kbs:
            if kb.kb_id in seen:
                continue
            seen.add(kb.kb_id)
            unique.append(kb)
        return unique

    def _render_lobby(self, game: QuickQAGame) -> str:
        lines = [
            "<b>快问快答报名中</b>",
            f"门槛金额：{_code(game.entry_fee)}",
            f"初始积分：{_code(game.initial_points)}",
            f"人数：{len(game.players)}/{game.max_players}（至少 {game.min_players} 人开局）",
            f"本局最多题数：{_code(game.max_questions)}",
            "",
            "参与方式：对收款人的消息转账门槛金额，到账后自动报名。",
        ]
        if game.players:
            lines.append("")
            lines.append("<b>已报名</b>")
            for player in sorted(game.players.values(), key=lambda p: p.joined_at):
                lines.append(f"- {_html(player.name)}：{player.points} 分")
        lines.append("")
        lines.append(f"开局：已报名玩家点击按钮，或发送 {_code(_command_prefix() + self._command + ' start')}。")
        return "\n".join(lines)

    def _lobby_markup(self, game: QuickQAGame) -> dict[str, Any]:
        return {"inline_keyboard": [[{"text": "开始选择题库", "callback_data": f"{CALLBACK_PREFIX}:start:{game.game_id}"}]]}

    def _render_kb_selection(self, game: QuickQAGame, kbs: list[KnowledgeBase]) -> str:
        selector = game.players.get(game.selector_user_id)
        selected_count = len(game.selected_kb_ids) if game.selected_kb_ids else len(kbs)
        lines = [
            "<b>题库选择</b>",
            f"本轮随机抽中：{_html(selector.name if selector else game.selector_user_id)}",
            f"当前将使用：{selected_count} 个题库",
            f"本局最多出题：{game.max_questions} 题",
            "",
            "可以点选一个或多个题库；不选直接开始则默认使用全部题库。",
        ]
        return "\n".join(lines)

    def _kb_markup(self, game: QuickQAGame, kbs: list[KnowledgeBase]) -> dict[str, Any]:
        rows: list[list[dict[str, str]]] = []
        for kb in kbs[:30]:
            selected = (not game.selected_kb_ids) or kb.kb_id in game.selected_kb_ids
            mark = "已选" if selected else "未选"
            rows.append([
                {
                    "text": f"{mark} {kb.title[:20]}",
                    "callback_data": f"{CALLBACK_PREFIX}:kb:{game.game_id}:{kb.kb_id}",
                }
            ])
        rows.append([{"text": "开始出题", "callback_data": f"{CALLBACK_PREFIX}:go:{game.game_id}"}])
        return {"inline_keyboard": rows}

    def _render_game_start(self, game: QuickQAGame, kbs: list[KnowledgeBase]) -> str:
        titles = "、".join(kb.title for kb in kbs[:5])
        if len(kbs) > 5:
            titles += f" 等 {len(kbs)} 个题库"
        return (
            "<b>快问快答开始</b>\n"
            f"题库：{_html(titles)}\n"
            f"本局题数：{len(game.question_pool)} 题\n"
            f"答对 +{game.correct_points} 分，答错 -{game.wrong_points} 分，扣完出局。\n"
            f"剩最后一人时按积分折算发奖，奖池上限为总门槛金额的 {int(game.reward_ratio * 100)}%。"
        )

    def _render_question(self, game: QuickQAGame) -> str:
        current = game.current_question
        if current is None:
            return ""
        q = current.question
        lines = [
            f"<b>第 {current.index}/{len(game.question_pool)} 题</b>",
            _html(q.question),
            "",
        ]
        for idx, option in enumerate(q.options):
            lines.append(f"{chr(65 + idx)}. {_html(option)}")
        lines.append("")
        lines.append(f"本题限时 {game.question_timeout_seconds} 秒。")
        lines.append(self._scoreboard(game))
        return "\n".join(lines)

    def _answer_markup(self, game: QuickQAGame, current: CurrentQuestion) -> dict[str, Any]:
        row = [
            {
                "text": chr(65 + idx),
                "callback_data": f"{CALLBACK_PREFIX}:ans:{game.game_id}:{current.question_id}:{idx}",
            }
            for idx in range(3)
        ]
        return {"inline_keyboard": [row]}

    def _render_question_result(self, game: QuickQAGame, actor_name: str, correct: bool) -> str:
        current = game.current_question
        if current is None:
            return ""
        q = current.question
        answer = q.options[q.answer_index]
        status = "答对" if correct else "答错"
        lines = [
            f"<b>第 {current.index} 题结果</b>",
            _html(q.question),
            f"{_html(actor_name)} {status}。",
            f"正确答案：{chr(65 + q.answer_index)}. {_html(answer)}",
        ]
        if q.explanation:
            lines.append(f"解释：{_html(q.explanation)}")
        lines.append("")
        lines.append(self._scoreboard(game))
        return "\n".join(lines)

    def _render_question_timeout(self, game: QuickQAGame, *, exhausted: bool = False) -> str:
        current = game.current_question
        if current is None:
            return ""
        q = current.question
        answer = q.options[q.answer_index]
        title = "本题无人答对" if exhausted else "本题超时"
        lines = [
            f"<b>{title}</b>",
            _html(q.question),
            f"正确答案：{chr(65 + q.answer_index)}. {_html(answer)}",
        ]
        if q.explanation:
            lines.append(f"解释：{_html(q.explanation)}")
        lines.append("")
        lines.append(self._scoreboard(game))
        return "\n".join(lines)

    def _render_finish(self, game: QuickQAGame, winner: Player, reward: int, reason: str) -> str:
        pool = game.entry_fee * len(game.players)
        cap = int(round(pool * game.reward_ratio))
        lines = [
            "<b>快问快答结束</b>",
            f"原因：{_html(reason)}",
            f"赢家：{_html(winner.name)}",
            f"剩余积分：{winner.points}",
            f"总门槛金额：{game.entry_fee} × {len(game.players)} = {pool}",
            f"发奖上限：{cap}",
            f"本次奖励：{reward}",
            "",
            self._scoreboard(game, include_out=True),
        ]
        return "\n".join(lines)

    def _scoreboard(self, game: QuickQAGame, *, include_out: bool = False) -> str:
        players = sorted(game.players.values(), key=lambda p: (-p.points, p.joined_at))
        if not include_out:
            players = [p for p in players if p.active]
        if not players:
            return "积分榜：暂无"
        lines = ["<b>积分榜</b>"]
        for player in players:
            status = "出局" if not player.active else "存活"
            lines.append(f"- {_html(player.name)}：{player.points} 分（{status}）")
        return "\n".join(lines)

    def _render_kb_list(self, ctx: PluginContext) -> str:
        kbs = self._available_kbs(ctx)
        if not kbs:
            return (
                "当前还没有题库。\n"
                "请在 TelePilot Web 配置页的题库管理里添加 URL，并保存配置。"
            )
        lines = ["<b>已保存题库</b>"]
        for kb in kbs:
            lines.append(f"- {_code(kb.kb_id)} {_html(kb.title)}（{len(kb.questions)} 题）")
        return "\n".join(lines)

    def _draft_markup(self, draft_id: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "保存题库", "callback_data": f"{CALLBACK_PREFIX}:save:{draft_id}"},
                    {"text": "取消", "callback_data": f"{CALLBACK_PREFIX}:drop:{draft_id}"},
                ]
            ]
        }

    def _usage(self, ctx: PluginContext) -> str:
        prefix = _command_prefix()
        return (
            "<b>快问快答</b>\n"
            "题库：在 TelePilot Web 配置页添加 URL，获取并整理后保存配置\n"
            f"{_code(prefix + self._command + ' 100')} 创建报名大厅\n"
            f"{_code(prefix + self._command + ' 100 20')} 创建本局最多 20 题的报名大厅\n"
            f"{_code(prefix + self._command + ' start')} 达到人数后开始选择题库\n"
            f"{_code(prefix + self._command + ' cancel')} 取消当前局\n"
            f"{_code(prefix + self._command + ' kb list')} 查看题库"
        )

    def _cfg_start_keyword(self, ctx: PluginContext, payload: dict[str, Any]) -> str:
        module_config = _dict(payload.get("module_config"))
        return str(module_config.get("start_keyword") or (ctx.config or {}).get("start_keyword") or DEFAULT_START_KEYWORD).strip()

    def _cfg_entry_fee(self, ctx: PluginContext, payload: dict[str, Any]) -> int:
        module_config = _dict(payload.get("module_config"))
        return _clamp_int(
            payload.get("entry_fee") or module_config.get("entry_fee") or (ctx.config or {}).get("entry_fee"),
            DEFAULT_ENTRY_FEE,
            1,
            10_000_000,
        )

    def _event_chat_id(self, event: Any) -> int:
        raw = getattr(event, "chat_id", None) or getattr(getattr(event, "message", event), "chat_id", None)
        channel_id = getattr(raw, "channel_id", None)
        if channel_id is not None:
            return int(f"-100{channel_id}")
        return _int(raw, 0)

    def _event_message_id(self, event: Any) -> int | None:
        return _int(getattr(getattr(event, "message", event), "id", None) or getattr(event, "id", None), 0) or None

    def _event_sender_id(self, event: Any) -> int:
        return _int(getattr(event, "sender_id", None) or getattr(getattr(event, "message", event), "sender_id", None), 0)

    async def _event_sender_name(self, event: Any) -> str:
        getter = getattr(event, "get_sender", None)
        sender = None
        if callable(getter):
            try:
                sender = await getter()
            except Exception:
                sender = None
        username = str(getattr(sender, "username", "") or "").strip().lstrip("@")
        if username:
            return username
        name = " ".join(
            part
            for part in (
                str(getattr(sender, "first_name", "") or "").strip(),
                str(getattr(sender, "last_name", "") or "").strip(),
            )
            if part
        )
        return name or str(self._event_sender_id(event) or "管理员")

    async def _event_edit_or_reply(self, event: Any, text: str) -> None:
        for method_name in ("edit", "reply"):
            method = getattr(event, method_name, None)
            if not callable(method):
                continue
            try:
                await method(text, parse_mode="html")
                return
            except TypeError:
                try:
                    await method(text)
                    return
                except Exception:
                    continue
            except Exception:
                continue

    def _actions_text(self, actions: list[dict[str, Any]] | None) -> str:
        if not actions:
            return "没有可执行动作。"
        for action in actions:
            if action.get("type") in {"send_message", "edit_message"} and action.get("text"):
                return str(action["text"])
        return "已处理。"


PLUGIN_CLASS = QuickQAPlugin

__all__ = [
    "AI_SYSTEM_PROMPT",
    "KnowledgeBase",
    "Player",
    "QAQuestion",
    "QuickQAGame",
    "QuickQAPlugin",
    "PLUGIN_CLASS",
    "_clean_html_to_text",
    "_extract_json_object",
    "_question_from_dict",
]
