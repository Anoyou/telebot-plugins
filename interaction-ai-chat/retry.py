"""AI 答疑：模型调用失败后的重试（inline 按钮 + 回复“重试”文本）。

只放与平台无关的纯函数：callback 数据编解码、按钮键盘、重试词识别、审核类错误识别、
“换模型重试”候选列表。状态读写与投递在 ``plugin.py``。

callback 数据格式（Telegram 上限 64 字节，这里最长约 24 字节）::

    iac:r:<token>          直接重试（提问者或管理员）
    iac:m:<token>          打开模型列表（仅管理员）
    iac:p:<token>:<page>   模型列表翻页（仅管理员）
    iac:s:<token>:<index>  选择模型并重试（仅管理员，index 指向记录里保存的候选列表）
    iac:c:<token>          取消，回到两个按钮（仅管理员）

``token`` 是 12 位随机十六进制，映射到 ``ctx.storage`` 里保存的完整原请求（原问题、引用、图片 file_id、
历史快照、原模型、发起人、会话、消息 ID），重试时按原请求重放，不靠模型猜“要重试什么”。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

CALLBACK_PREFIX = "iac"
ACTION_RETRY = "r"
ACTION_MENU = "m"
ACTION_PAGE = "p"
ACTION_SELECT = "s"
ACTION_CANCEL = "c"
ACTIONS = {ACTION_RETRY, ACTION_MENU, ACTION_PAGE, ACTION_SELECT, ACTION_CANCEL}
ADMIN_ACTIONS = {ACTION_MENU, ACTION_PAGE, ACTION_SELECT, ACTION_CANCEL}

# 失败记录（原请求）保存 30 分钟；过期后点按钮提示“已过期，请重新提问”。
RETRY_TTL_SECONDS = 30 * 60
MODELS_PER_PAGE = 6
MAX_MODEL_CANDIDATES = 48
CALLBACK_DATA_LIMIT = 64
TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")

BUTTON_RETRY = "🔄 重试"
BUTTON_SWITCH = "🔀 换模型重试"
BUTTON_PREV = "◀ 上一页"
BUTTON_NEXT = "下一页 ▶"
BUTTON_CANCEL = "✖ 取消"

# 回复失败消息（或私聊里直接发）这些词时，按保存的原请求重放；比较前去掉首尾空白与标点、转小写。
RETRY_WORDS = {
    "重试",
    "重新试试",
    "再试",
    "再试一次",
    "再试试",
    "再来",
    "再来一次",
    "重新回答",
    "重答",
    "重新生成",
    "retry",
    "try again",
    "again",
}
_RETRY_STRIP_RE = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)

# 供应商内容审核 / 安全拦截：0.4.8 起用独立提示模板；固定模型不可用时平台可能回退到其它 Provider，
# 拒答常来自回退的那家，所以仍附重试按钮（换模型往往能过）。
MODERATION_MARKERS = (
    "content_filter",
    "content filter",
    "content_policy",
    "content policy",
    "content management policy",
    "data_inspection_failed",
    "inappropriate content",
    "safety system",
    "safety_violation",
    "responsibleaipolicyviolation",
    # 0.4.8：不再用裸词 “sensitive”——“Model names are case-sensitive …” 之类的模型不存在报错会被误判为审核拒答。
    "sensitive content",
    "sensitive words",
    "sensitive_words",
    "内容审核",
    "内容安全",
    "敏感内容",
    "敏感词",
    "违规内容",
    "不当内容",
)


def new_token() -> str:
    return uuid.uuid4().hex[:12]


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(str(token or "")))


def callback_data(action: str, token: str, arg: int | None = None) -> str:
    data = f"{CALLBACK_PREFIX}:{action}:{token}" + (f":{int(arg)}" if arg is not None else "")
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:  # pragma: no cover - 格式固定，不会超
        raise ValueError("callback_data 超过 64 字节")
    return data


def parse_callback(data: Any) -> tuple[str, str, int | None] | None:
    """解析本插件的 callback 数据；不是本插件的或格式不对返回 None。"""

    parts = str(data or "").split(":")
    if len(parts) not in (3, 4) or parts[0] != CALLBACK_PREFIX:
        return None
    action, token = parts[1], parts[2]
    if action not in ACTIONS or not valid_token(token):
        return None
    arg: int | None = None
    if len(parts) == 4:
        if not parts[3].isdigit() or len(parts[3]) > 3:
            return None
        arg = int(parts[3])
    if action in (ACTION_PAGE, ACTION_SELECT) and arg is None:
        return None
    return action, token, arg


def is_own_callback(data: Any) -> bool:
    return str(data or "").startswith(f"{CALLBACK_PREFIX}:")


def retry_keyboard(token: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": BUTTON_RETRY, "callback_data": callback_data(ACTION_RETRY, token)},
                {"text": BUTTON_SWITCH, "callback_data": callback_data(ACTION_MENU, token)},
            ]
        ]
    }


def page_count(total: int) -> int:
    return max(1, (max(0, total) + MODELS_PER_PAGE - 1) // MODELS_PER_PAGE)


def model_keyboard(token: str, models: list[dict[str, Any]], page: int) -> dict[str, Any]:
    """模型列表：每行一个模型，最多 ``MODELS_PER_PAGE`` 个；多页时加翻页行；最后一行“取消”。"""

    pages = page_count(len(models))
    page = min(max(0, int(page)), pages - 1)
    start = page * MODELS_PER_PAGE
    rows: list[list[dict[str, Any]]] = []
    for index, item in enumerate(models[start : start + MODELS_PER_PAGE], start=start):
        rows.append([{"text": _clip_label(str(item.get("label") or item.get("m") or "")), "callback_data": callback_data(ACTION_SELECT, token, index)}])
    if pages > 1:
        nav: list[dict[str, Any]] = []
        if page > 0:
            nav.append({"text": BUTTON_PREV, "callback_data": callback_data(ACTION_PAGE, token, page - 1)})
        if page < pages - 1:
            nav.append({"text": BUTTON_NEXT, "callback_data": callback_data(ACTION_PAGE, token, page + 1)})
        rows.append(nav)
    rows.append([{"text": BUTTON_CANCEL, "callback_data": callback_data(ACTION_CANCEL, token)}])
    return {"inline_keyboard": rows}


def _clip_label(text: str, limit: int = 48) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def is_retry_text(text: Any) -> bool:
    value = _RETRY_STRIP_RE.sub("", str(text or "")).casefold()
    return bool(value) and value in RETRY_WORDS


# 明确是“模型不可用”的报错（连接失败、模型不存在、鉴权失败、未启用）：即使夹带审核类字样也不算审核拒答。
UNAVAILABLE_MARKERS = (
    "model_not_found",
    "model not found",
    "does not exist",
    "connection error",
    "connection refused",
    "connecterror",
    "name or service not known",
    "unauthorized",
    "invalid_api_key",
    "incorrect api key",
    "未在 provider",
    "没有已启用模型",
    "模型不存在",
)


def is_moderation_error(exc: BaseException) -> bool:
    """供应商内容审核拒答（0.4.8 起用独立模板 ``error_moderation_template``，仍可重试/换模型）。"""

    text = f"{type(exc).__name__} {exc}".casefold()
    if any(marker in text for marker in UNAVAILABLE_MARKERS):
        return False
    return any(marker in text for marker in MODERATION_MARKERS)


def _field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _same_provider(info: Any, provider: str) -> bool:
    raw = str(provider or "").strip()
    if not raw:
        return False
    if raw.isdigit() and str(_field(info, "id", "")) == raw:
        return True
    return str(_field(info, "name", "") or "").casefold() == raw.casefold()


def model_candidates(
    providers: list[Any],
    *,
    exclude_provider: str = "",
    exclude_model: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """从 ``ctx.ai.list_providers()`` 生成“换模型重试”候选。返回 (候选, 是否按启用状态精确过滤)。

    - 只取有 API Key、文本模态的 Provider；
    - TelePilot 0.106.0-beta.13+ 的模型元数据带 ``enabled``：列出每个 Provider 里所有已启用的文本模型；
    - 更旧的平台不暴露 ``enabled``（列全部可能包含未启用模型，选中会被平台拒绝）：每个 Provider 只列默认模型；
    - 排除刚失败的模型（固定 Provider+模型时为该组合；自动路由时无法得知实际模型，不排除）。

    每项：``{"p": Provider id 字符串, "m": 模型 id, "label": "Provider · 模型显示名"}``。
    """

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    exact = False
    for info in providers or []:
        if not bool(_field(info, "has_api_key", True)):
            continue
        modality = str(_field(info, "modality", "text") or "text").casefold()
        if modality not in ("text", "chat", "multimodal"):
            continue
        provider_id = str(_field(info, "id", "") or "").strip()
        provider_name = str(_field(info, "name", "") or provider_id).strip()
        if not provider_id:
            continue
        models = [item for item in (_field(info, "models", None) or []) if isinstance(item, dict) and str(item.get("id") or "").strip()]
        entries: list[tuple[str, str]] = []
        if any("enabled" in item for item in models):
            exact = True
            for item in models:
                if item.get("enabled") is not True:
                    continue
                item_modality = str(item.get("modality") or "text").casefold()
                if item_modality not in ("text", "chat", "multimodal"):
                    continue
                label = str(item.get("display_name") or item.get("label") or item.get("name") or item["id"]).strip()
                entries.append((str(item["id"]).strip(), label))
        else:
            default_model = str(_field(info, "default_model", "") or "").strip()
            if default_model:
                label = default_model
                for item in models:
                    if str(item.get("id")).strip() == default_model:
                        label = str(item.get("display_name") or item.get("label") or item.get("name") or default_model).strip()
                        break
                entries.append((default_model, label))
        for model_id, label in entries:
            if exclude_model and model_id == exclude_model and (not exclude_provider or _same_provider(info, exclude_provider)):
                continue
            key = (provider_id, model_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({"p": provider_id, "m": model_id, "label": f"{provider_name} · {label}"})
            if len(out) >= MAX_MODEL_CANDIDATES:
                return out, exact
    return out, exact


__all__ = [
    "ACTION_CANCEL",
    "ACTION_MENU",
    "ACTION_PAGE",
    "ACTION_RETRY",
    "ACTION_SELECT",
    "ADMIN_ACTIONS",
    "RETRY_TTL_SECONDS",
    "callback_data",
    "is_moderation_error",
    "is_own_callback",
    "is_retry_text",
    "model_candidates",
    "model_keyboard",
    "new_token",
    "parse_callback",
    "retry_keyboard",
]
