"""
发红包插件

功能：
- 按指定格式快速生成拼手气红包文案
- 支持自定义领取口令、金额和份数
- 支持识别别人发送的口令并按随机金额发放红包
- 支持保存默认金额、默认份数和展示名称
"""

import asyncio
import html
import json
import math
import random
import re
import shlex
import string
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

from pagermaid.enums import Client, Message
from pagermaid.hook import Hook
from pagermaid.listener import listener
from pagermaid.utils import logs

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

__version__ = "1.5.4"

plugin_dir = Path(__file__).parent
config_file = plugin_dir / "redpack_config.json"

DEFAULT_AMOUNT = 88888
DEFAULT_COUNT = 10
DEFAULT_SUFFIX = "发了一个拼手气红包"
REDPACK_ID_LENGTH = 6
DEFAULT_MIN_SHARE_AMOUNT = 100
MIN_SHARE_AMOUNT = DEFAULT_MIN_SHARE_AMOUNT
DEFAULT_CLAIM_REPLY_DELETE_DELAY = 8
CLAIM_REPLY_DELETE_DELAY = DEFAULT_CLAIM_REPLY_DELETE_DELAY
DEFAULT_AUTO_CONFIRM_TTL = 180
AUTO_CONFIRM_TTL = DEFAULT_AUTO_CONFIRM_TTL
DEFAULT_AUTO_CONFIRM_CLICK_DELAY = 0.8
AUTO_CONFIRM_CLICK_DELAY = DEFAULT_AUTO_CONFIRM_CLICK_DELAY
AUTO_CONFIRM_ENABLED = True
DELETE_COMMAND_MESSAGE = True
COMMAND_PREFIX = ","
COMMAND_NAME = "redpack"
DEFAULT_SUBCOMMAND_ALIASES = {
    "help": {"help", "帮助"},
    "send": {"send"},
    "img": {"img", "image", "图片"},
    "status": {"status", "状态"},
    "active": {"active", "list", "列表"},
    "clear": {"clear", "清空"},
    "amount": {"amount", "金额"},
    "count": {"count", "个数", "数量"},
    "name": {"name", "昵称", "名称"},
    "reset": {"reset", "重置"},
}
SUBCOMMAND_ALIASES = {key: set(value) for key, value in DEFAULT_SUBCOMMAND_ALIASES.items()}
CAPTCHA_WIDTH = 1280
CAPTCHA_HEIGHT = 420
CAPTCHA_DOT_COUNT = 1400
CAPTCHA_LINE_COUNT = 28
CAPTCHA_DECOY_COUNT = 70
DYNAMIC_AFFIX_LENGTH = 3
DYNAMIC_AFFIX_CHARS = string.ascii_uppercase + "23456789"
IMAGE_CODE_LENGTH = 4
PHOTO_CAPTION_SAFE_LIMIT = 1000
BUNDLED_FONT_CANDIDATES = [
    plugin_dir / "assets" / "font.ttf",
    plugin_dir.parent / "redpack" / "assets" / "font.ttf",
]
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
]
FONT_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    str(Path.home() / ".fonts"),
]
FONT_SEARCH_KEYWORDS = [
    "notosanscjk",
    "notoserifcjk",
    "sourcehansans",
    "sourcehanserif",
    "wqy",
    "wenquanyi",
    "ukai",
    "uming",
    "droidsansfallback",
]
FONT_EXTENSIONS = {".ttf", ".ttc", ".otf"}
_font_path_cache: Optional[str] = None
_font_search_done = False
_captcha_last_error = ""
_pending_transfer_confirms: dict[str, dict[int, float]] = {}


def parse_aliases(value: Any, defaults: set[str]) -> set[str]:
    """解析配置页里的别名列表，兼容逗号、空白和 JSON 数组。"""
    items: list[Any]
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "").strip()
        items = re.split(r"[\s,，]+", text) if text else []
    aliases = {str(item).strip().lower() for item in items if str(item).strip()}
    return aliases or set(defaults)


def apply_runtime_settings(settings: dict[str, Any]) -> None:
    """应用 TelePilot 配置页参数到历史业务层。"""
    global MIN_SHARE_AMOUNT
    global CLAIM_REPLY_DELETE_DELAY
    global AUTO_CONFIRM_TTL
    global AUTO_CONFIRM_CLICK_DELAY
    global AUTO_CONFIRM_ENABLED
    global DELETE_COMMAND_MESSAGE
    global COMMAND_PREFIX
    global COMMAND_NAME
    global SUBCOMMAND_ALIASES

    cfg = settings or {}
    MIN_SHARE_AMOUNT = clamp_int(
        cfg.get("min_share_amount", DEFAULT_MIN_SHARE_AMOUNT),
        DEFAULT_MIN_SHARE_AMOUNT,
        1,
        999999999,
    )
    CLAIM_REPLY_DELETE_DELAY = clamp_int(
        cfg.get("claim_reply_delete_delay", DEFAULT_CLAIM_REPLY_DELETE_DELAY),
        DEFAULT_CLAIM_REPLY_DELETE_DELAY,
        0,
        86400,
    )
    AUTO_CONFIRM_TTL = clamp_int(cfg.get("auto_confirm_ttl", DEFAULT_AUTO_CONFIRM_TTL), DEFAULT_AUTO_CONFIRM_TTL, 1, 86400)
    AUTO_CONFIRM_CLICK_DELAY = max(
        0,
        min(float(cfg.get("auto_confirm_click_delay", DEFAULT_AUTO_CONFIRM_CLICK_DELAY)), 60),
    )
    AUTO_CONFIRM_ENABLED = bool(cfg.get("auto_confirm_enabled", AUTO_CONFIRM_ENABLED))
    DELETE_COMMAND_MESSAGE = bool(
        cfg.get("delete_command_message", cfg.get("delete_image_command_message", DELETE_COMMAND_MESSAGE))
    )
    COMMAND_PREFIX = str(cfg.get("command_prefix") or COMMAND_PREFIX or ",")
    COMMAND_NAME = str(cfg.get("command") or COMMAND_NAME or "redpack").strip() or "redpack"

    SUBCOMMAND_ALIASES = {
        key: parse_aliases(cfg.get(f"{key}_aliases"), defaults)
        for key, defaults in DEFAULT_SUBCOMMAND_ALIASES.items()
    }

    if "default_amount" in cfg:
        config.default_amount = clamp_int(cfg.get("default_amount"), DEFAULT_AMOUNT, MIN_SHARE_AMOUNT, 999999999)
    if "default_count" in cfg:
        config.default_count = clamp_int(cfg.get("default_count"), DEFAULT_COUNT, 1, 500)
    if "custom_name" in cfg:
        config.custom_name = str(cfg.get("custom_name") or "").strip()


def command_example(suffix: str = "") -> str:
    """生成运行时帮助里的命令示例。"""
    tail = str(suffix or "").strip()
    body = f"{COMMAND_PREFIX}{COMMAND_NAME}"
    if tail:
        body = f"{body} {tail}"
    return f"`{body}`"


def resolve_subcommand(action: str) -> str | None:
    """把用户输入的子命令映射到内部动作名。"""
    token = str(action or "").strip().lower()
    for name, aliases in SUBCOMMAND_ALIASES.items():
        if token in aliases:
            return name
    return None


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """约束整数范围"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def split_command_text(text: str) -> list[str]:
    """兼容引号的参数切分"""
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    try:
        return [item for item in shlex.split(cleaned) if item]
    except ValueError:
        return [item for item in cleaned.split() if item]


def is_int_token(value: str) -> bool:
    """判断是否为整数文本"""
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def normalize_keyword(value: str) -> str:
    """标准化口令，便于匹配"""
    return " ".join(str(value or "").strip().split()).casefold()


def build_auto_sender_name(user: Any) -> str:
    """根据当前账号信息构建展示名称"""
    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    user_id = getattr(user, "id", None)

    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    if full_name:
        return full_name
    if username:
        return f"@{username}"
    if user_id is not None:
        return str(user_id)
    return "你"


def generate_redpack_id(length: int = REDPACK_ID_LENGTH) -> str:
    """生成红包 ID"""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def generate_dynamic_affix(length: int = DYNAMIC_AFFIX_LENGTH) -> str:
    """生成图片红包口令的随机片段"""
    return "".join(random.choice(DYNAMIC_AFFIX_CHARS) for _ in range(length))


def generate_image_code(length: int = IMAGE_CODE_LENGTH) -> str:
    """生成图片中显示的一次性口令"""
    return "".join(random.choice(DYNAMIC_AFFIX_CHARS) for _ in range(length))


def generate_math_challenge() -> tuple[str, int]:
    """生成 3 位数以内的随机四则运算题，除法保证整除"""
    operator = random.choice(["+", "-", "×", "÷"])
    if operator == "+":
        left = random.randint(10, 999)
        right = random.randint(1, 999)
        return f"{left} + {right}", left + right
    if operator == "-":
        left = random.randint(10, 999)
        right = random.randint(1, left)
        return f"{left} - {right}", left - right
    if operator == "×":
        left = random.randint(2, 99)
        right = random.randint(2, 99)
        return f"{left} × {right}", left * right

    divisor = random.randint(2, 99)
    answer = random.randint(1, max(1, 999 // divisor))
    dividend = divisor * answer
    return f"{dividend} ÷ {divisor}", answer


def build_math_claim_keyword(answer: int, image_code: str) -> str:
    """构建图片红包真正需要发送的答案：数学答案 + 图片口令"""
    return f"{int(answer)}{str(image_code or '').strip()}"


def generate_image_math_challenge(image_code: str) -> dict[str, Any]:
    """生成一轮图片红包挑战"""
    image_code = str(image_code or "").strip() or generate_image_code()
    math_question, math_answer = generate_math_challenge()
    claim_keyword = build_math_claim_keyword(math_answer, image_code)
    return {
        "image_code": image_code,
        "math_question": math_question,
        "math_answer": math_answer,
        "claim_keyword": claim_keyword,
    }


def normalize_math_challenge(challenge: Any, index: int, image_code: str) -> Optional[dict[str, Any]]:
    """标准化一条图片红包数学题记录"""
    if not isinstance(challenge, dict):
        return None

    math_question = str(challenge.get("math_question", "") or "").strip()
    math_answer = challenge.get("math_answer")
    try:
        math_answer = int(math_answer)
    except (TypeError, ValueError):
        return None

    claim_keyword = str(challenge.get("claim_keyword", "") or "").strip()
    if not claim_keyword:
        claim_keyword = build_math_claim_keyword(math_answer, image_code)
    if not math_question:
        return None

    return {
        "index": clamp_int(challenge.get("index", index), index, 1, 999999),
        "math_question": math_question,
        "math_answer": math_answer,
        "claim_keyword": claim_keyword,
        "keyword_norm": normalize_keyword(claim_keyword),
        "claimed": bool(challenge.get("claimed", False)),
    }


def generate_image_math_challenges(image_code: str, count: int) -> list[dict[str, Any]]:
    """一次性生成图片红包的全部数学题，答案口令尽量不重复"""
    challenges: list[dict[str, Any]] = []
    used_norms: set[str] = set()
    target_count = max(1, int(count))

    attempts = 0
    while len(challenges) < target_count and attempts < target_count * 80:
        attempts += 1
        challenge = generate_image_math_challenge(image_code)
        keyword_norm = normalize_keyword(challenge["claim_keyword"])
        if keyword_norm in used_norms:
            continue
        used_norms.add(keyword_norm)
        challenges.append(
            {
                "index": len(challenges) + 1,
                "math_question": challenge["math_question"],
                "math_answer": challenge["math_answer"],
                "claim_keyword": challenge["claim_keyword"],
                "keyword_norm": keyword_norm,
                "claimed": False,
            }
        )

    while len(challenges) < target_count:
        answer = random.randint(10000, 99999)
        claim_keyword = build_math_claim_keyword(answer, image_code)
        keyword_norm = normalize_keyword(claim_keyword)
        if keyword_norm in used_norms:
            continue
        used_norms.add(keyword_norm)
        challenges.append(
            {
                "index": len(challenges) + 1,
                "math_question": str(answer),
                "math_answer": answer,
                "claim_keyword": claim_keyword,
                "keyword_norm": keyword_norm,
                "claimed": False,
            }
        )

    return challenges


def build_dynamic_claim_keyword(base_keyword: str, used_keywords: Optional[list[str]] = None) -> str:
    """基于原始口令生成一次性图片口令"""
    base = str(base_keyword or "").strip()
    used_norms = {normalize_keyword(item) for item in (used_keywords or [])}

    for _ in range(50):
        affix = generate_dynamic_affix()
        if random.choice([True, False]):
            candidate = f"{affix}{base}"
        else:
            candidate = f"{base}{affix}"
        if normalize_keyword(candidate) not in used_norms:
            return candidate

    return f"{generate_dynamic_affix(5)}{base}"


def get_pack_claim_keyword(pack: dict[str, Any]) -> str:
    """获取当前真正可领取的口令"""
    return str(pack.get("keyword", "") or "").strip()


def build_math_questions_text(pack: dict[str, Any]) -> str:
    """构建图片红包一次性数学题列表"""
    challenges = pack.get("math_challenges", [])
    lines = [
        "🧮 数学题（任选一道）",
        "发送：答案+图片口令",
        "",
    ]
    for index, challenge in enumerate(challenges, start=1):
        question_index = int(challenge.get("index", index) or index)
        question = str(challenge.get("math_question", "") or "").strip()
        if not question:
            continue
        suffix = " ✅已领取" if challenge.get("claimed") else ""
        lines.append(f"{question_index}. {question} = ?{suffix}")
    return "\n".join(lines).strip()


def build_image_redpack_caption(pack: dict[str, Any]) -> tuple[str, bool]:
    """构建图片红包 caption，并标记是否完整包含题目"""
    caption_text = render_redpack_caption_text(
        sender_name=str(pack.get("sender_name", "") or ""),
        redpack_id=str(pack.get("redpack_id", "")),
        keyword=str(pack.get("base_keyword", "") or ""),
        amount=int(pack.get("total_amount", 0)),
        count=int(pack.get("total_count", 0)),
    )
    math_text = build_math_questions_text(pack)
    combined_caption = f"{caption_text}\n\n{math_text}".strip()
    if len(combined_caption) <= PHOTO_CAPTION_SAFE_LIMIT:
        return combined_caption, True
    return f"{caption_text}\n\n题目较多，已单独发送完整题目列表。", False


def split_long_text(text: str, limit: int = 3500) -> list[str]:
    """按行拆分长文本，避免超过 Telegram 单条消息长度限制"""
    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    for line in str(text or "").splitlines():
        line_length = len(line) + 1
        if current_lines and current_length + line_length > limit:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0
        current_lines.append(line)
        current_length += line_length

    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks or [text]


def render_redpack_text(sender_name: str, redpack_id: str, keyword: str, amount: int, count: int) -> str:
    """渲染红包文案"""
    return (
        f"🧧 {sender_name}{DEFAULT_SUFFIX}\n"
        f"💰 总额: {amount}｜📦 共{count}个\n"
        f"🆔 红包ID: {redpack_id}\n\n"
        f"发送下方口令领取：\n"
        f"{keyword}"
    )


def render_redpack_text_copyable(sender_name: str, redpack_id: str, keyword: str, amount: int, count: int) -> str:
    """渲染纯文字红包文案，保留反引号方便复制"""
    return (
        f"🧧 {sender_name}{DEFAULT_SUFFIX}\n"
        f"💰 总额: {amount}｜📦 共{count}个\n"
        f"🆔 红包ID: {redpack_id}\n\n"
        f"发送下方口令领取：\n"
        f"`{keyword}`"
    )


def render_redpack_text_rich(sender_name: str, redpack_id: str, keyword: str, amount: int, count: int) -> str:
    """渲染纯文字红包富文本，走 message.edit 兼容链路"""
    safe_sender_name = html.escape(sender_name)
    safe_redpack_id = html.escape(redpack_id)
    safe_keyword = html.escape(keyword)
    return (
        f"🧧 {safe_sender_name}{DEFAULT_SUFFIX}\n"
        f"💰 总额: {amount}｜📦 共{count}个\n"
        f"🆔 红包ID: {safe_redpack_id}\n\n"
        f"发送下方口令领取：\n"
        f"<code>{safe_keyword}</code>"
    )


def render_redpack_caption_text(
    sender_name: str,
    redpack_id: str,
    keyword: str,
    amount: int,
    count: int,
) -> str:
    """渲染红包图文 caption"""
    return (
        f"🧧 {sender_name}{DEFAULT_SUFFIX}\n"
        f"💰 总额: {amount}｜📦 共{count}个\n"
        f"🆔 红包ID: {redpack_id}\n\n"
        f"请识别图片中的口令，并从下方题目中任选一道发送：数学答案 + 图片口令。\n"
        f"每道题只能成功领取一次。"
    )


def build_chat_key(chat_id: Any) -> str:
    """统一聊天 ID 键，兼容 Telethon entity id 与 marked peer id。"""
    value = getattr(chat_id, "channel_id", chat_id)
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return str(value)
    if value_int < 0:
        raw = str(value_int)
        if raw.startswith("-100") and len(raw) > 4:
            return raw[4:]
        return str(abs(value_int))
    return str(value_int)


def build_user_key(user_id: Any) -> str:
    """统一用户 ID 键"""
    return str(user_id)


def build_claim_reply(amount: int) -> str:
    """构建领取回复文本"""
    return f"+{amount}"


def cleanup_pending_transfer_confirms(chat_id: Any) -> None:
    """清理过期的待确认转账消息记录"""
    chat_key = build_chat_key(chat_id)
    pending = _pending_transfer_confirms.get(chat_key)
    if not pending:
        return

    now = time.time()
    alive = {message_id: expires_at for message_id, expires_at in pending.items() if expires_at > now}
    if alive:
        _pending_transfer_confirms[chat_key] = alive
    else:
        _pending_transfer_confirms.pop(chat_key, None)


def register_pending_transfer_confirm(chat_id: Any, message_id: Optional[int]) -> None:
    """登记刚发出的 +金额 消息，后续若出现转账确认框则允许自动点击"""
    if message_id is None:
        return

    chat_key = build_chat_key(chat_id)
    cleanup_pending_transfer_confirms(chat_id)
    _pending_transfer_confirms.setdefault(chat_key, {})[int(message_id)] = time.time() + AUTO_CONFIRM_TTL


def is_pending_transfer_confirm_reply(chat_id: Any, reply_to_message_id: Optional[int]) -> bool:
    """判断一条机器人确认消息是否回复到了最近的 +金额 消息"""
    if reply_to_message_id is None:
        return False

    cleanup_pending_transfer_confirms(chat_id)
    pending = _pending_transfer_confirms.get(build_chat_key(chat_id), {})
    return int(reply_to_message_id) in pending


async def delete_message_later(message: Any, delay: float = CLAIM_REPLY_DELETE_DELAY) -> None:
    """延迟撤回消息，避免领取提示长期占屏"""
    try:
        await asyncio.sleep(max(0, float(delay)))
        await message.delete()
    except Exception:
        pass


def wrap_expandable(content: str) -> str:
    """将内容包装为 Telegram 可折叠 blockquote"""
    return f"<blockquote expandable>{content}</blockquote>"


def extract_message_text(message: Message) -> str:
    """提取消息文本"""
    return str(message.text or message.caption or "").strip()


def extract_claim_text_candidates(text: str) -> list[str]:
    """从普通消息或转发红包文案中提取可能的领取口令"""
    raw_text = str(text or "").strip()
    if not raw_text:
        return []

    candidates = [raw_text]

    for match in re.finditer(r"`([^`\n]+)`", raw_text):
        value = match.group(1).strip()
        if value:
            candidates.append(value)

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "发送下方口令领取" in line and index + 1 < len(lines):
            candidates.append(lines[index + 1].strip("`").strip())
        if line.startswith("口令") and "：" in line:
            candidates.append(line.split("：", 1)[1].strip("`").strip())
        if line.startswith("口令") and ":" in line:
            candidates.append(line.split(":", 1)[1].strip("`").strip())

    deduped = []
    seen = set()
    for candidate in candidates:
        normalized = normalize_keyword(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(candidate)
    return deduped


def build_claim_user_name(user: Any) -> str:
    """构建领取人的展示名称"""
    username = str(getattr(user, "username", "") or "").strip()
    if username:
        return f"@{username}"

    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    if full_name:
        return full_name

    user_id = getattr(user, "id", None)
    if user_id is not None:
        return str(user_id)
    return "未知用户"


def sanitize_display_name(name: Any) -> str:
    """清洗昵称里的换行、控制符和双向文本控制字符，避免榜单排版错乱"""
    text = str(name or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return "未知用户"

    cleaned_chars = []
    bidi_control_chars = {
        "\u200e",
        "\u200f",
        "\u061c",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
    for char in text:
        if char in bidi_control_chars:
            continue
        category = unicodedata.category(char)
        if category.startswith("C") and char not in (" ", "\t"):
            continue
        cleaned_chars.append(char)

    cleaned = " ".join("".join(cleaned_chars).split()).strip()
    return cleaned or "未知用户"


def compact_settlement_name(name: Any, limit: int = 14) -> str:
    """压缩结算榜单里的用户名，避免手机窄屏换行错位"""
    text = sanitize_display_name(name)
    if len(text) <= limit:
        return text
    return f"{text[:limit-3]}..."


def sort_claims_for_settlement(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """结算榜单按金额从高到低排序，同金额按领取时间从早到晚"""
    return sorted(
        claims,
        key=lambda item: (
            -int(item.get("amount", 0)),
            float(item.get("claimed_at", 0) or 0),
        ),
    )


def normalize_claim_record(claim: Any) -> Optional[dict[str, Any]]:
    """标准化单条领取记录，便于补偿旧数据和做重复校验"""
    if not isinstance(claim, dict):
        return None

    user_id = claim.get("user_id")
    try:
        normalized_user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        normalized_user_id = None

    message_id = claim.get("message_id")
    try:
        normalized_message_id = int(message_id) if message_id is not None else None
    except (TypeError, ValueError):
        normalized_message_id = None

    return {
        "user_id": normalized_user_id,
        "amount": clamp_int(claim.get("amount", 0), 0, 0, 999999999),
        "display_name": str(claim.get("display_name", "未知用户") or "未知用户"),
        "message_id": normalized_message_id,
        "claimed_at": float(claim.get("claimed_at", time.time()) or time.time()),
    }


def load_captcha_font(size: int) -> Any:
    """加载验证码字体"""
    if not HAS_PIL:
        return None

    font_path = find_captcha_font_path("图片口令")
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception as error:
            logs.warning(f"[REDPACK] 加载字体失败: {font_path} | {error}")
    return ImageFont.load_default()


def can_font_render_text(font_path: str, text: str) -> bool:
    """判断字体是否能渲染指定文本"""
    if not font_path:
        return False

    try:
        font = ImageFont.truetype(font_path, 72)
        mask = font.getmask(text or "图")
        return mask.getbbox() is not None
    except Exception:
        return False


def find_captcha_font_path(sample_text: str = "图片口令") -> Optional[str]:
    """查找可用的中文字体路径"""
    global _font_path_cache, _font_search_done

    if _font_path_cache and can_font_render_text(_font_path_cache, sample_text):
        return _font_path_cache

    for bundled_path in BUNDLED_FONT_CANDIDATES:
        bundled_path_str = str(bundled_path)
        if bundled_path.exists() and can_font_render_text(bundled_path_str, sample_text):
            _font_path_cache = bundled_path_str
            logs.info(f"[REDPACK] 使用内置验证码字体: {_font_path_cache}")
            return _font_path_cache

    for font_path in FONT_CANDIDATES:
        if Path(font_path).exists() and can_font_render_text(font_path, sample_text):
            _font_path_cache = font_path
            logs.info(f"[REDPACK] 使用验证码字体: {_font_path_cache}")
            return _font_path_cache

    if _font_search_done:
        return _font_path_cache

    _font_search_done = True
    for search_dir in FONT_SEARCH_DIRS:
        directory = Path(search_dir)
        if not directory.exists():
            continue
        try:
            for candidate in directory.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in FONT_EXTENSIONS:
                    continue
                lowered_name = candidate.name.lower()
                if not any(keyword in lowered_name for keyword in FONT_SEARCH_KEYWORDS):
                    continue
                candidate_path = str(candidate)
                if can_font_render_text(candidate_path, sample_text):
                    _font_path_cache = candidate_path
                    logs.info(f"[REDPACK] 自动发现验证码字体: {_font_path_cache}")
                    return _font_path_cache
        except Exception as error:
            logs.warning(f"[REDPACK] 搜索字体目录失败: {directory} | {error}")

    logs.warning("[REDPACK] 未找到可渲染中文口令的字体，图片红包可能无法显示中文")
    return _font_path_cache


def measure_text(font: Any, text: str) -> tuple[int, int]:
    """测量文本尺寸"""
    if not HAS_PIL:
        return 0, 0

    dummy = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text or "口令", font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def pick_keyword_font(keyword: str) -> Any:
    """根据口令长度选择主字体大小"""
    if not HAS_PIL:
        return None

    text_length = max(1, len(keyword))
    if text_length <= 4:
        size = 108
    elif text_length <= 8:
        size = 90
    elif text_length <= 14:
        size = 74
    else:
        size = 60
    return load_captcha_font(size)


def cubic_bezier_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """计算三次贝塞尔曲线上的点"""
    mt = 1 - t
    x = (
        (mt ** 3) * p0[0]
        + 3 * (mt ** 2) * t * p1[0]
        + 3 * mt * (t ** 2) * p2[0]
        + (t ** 3) * p3[0]
    )
    y = (
        (mt ** 3) * p0[1]
        + 3 * (mt ** 2) * t * p1[1]
        + 3 * mt * (t ** 2) * p2[1]
        + (t ** 3) * p3[1]
    )
    return x, y


def get_resampling_attr() -> Any:
    """兼容不同 Pillow 版本的重采样常量"""
    if hasattr(Image, "Resampling"):
        return Image.Resampling
    return Image


def set_captcha_error(reason: str) -> None:
    global _captcha_last_error
    _captcha_last_error = str(reason or "").strip()


def get_captcha_error() -> str:
    return _captcha_last_error or "未知原因"


def build_captcha_image(keyword: str, redpack_id: str) -> Optional[Path]:
    """生成带干扰彩点的图片口令"""
    if not HAS_PIL:
        set_captcha_error("未安装 Pillow/PIL，请在 TelePilot worker 环境安装 Pillow 后重启")
        return None

    try:
        resampling = get_resampling_attr()
    except Exception as error:
        set_captcha_error(f"Pillow 初始化失败: {type(error).__name__}: {error}")
        return None
    scale = 2
    work_width = CAPTCHA_WIDTH * scale
    work_height = CAPTCHA_HEIGHT * scale

    image = Image.new("RGBA", (work_width, work_height), (248, 249, 251, 255))
    draw = ImageDraw.Draw(image)

    # 轻微背景分层
    for band_index in range(7):
        top = band_index * work_height // 7
        bottom = (band_index + 1) * work_height // 7
        fill = (
            244 + random.randint(-4, 5),
            246 + random.randint(-4, 5),
            250 + random.randint(-5, 4),
            255,
        )
        draw.rectangle((0, top, work_width, bottom), fill=fill)

    # 背景彩点：多但偏淡，不盖主字
    for _ in range(CAPTCHA_DOT_COUNT * 2):
        x = random.randint(0, work_width - 1)
        y = random.randint(0, work_height - 1)
        radius = random.randint(2, 8)
        color = (
            random.randint(120, 255),
            random.randint(120, 255),
            random.randint(120, 255),
            random.randint(35, 110),
        )
        draw.ellipse((x, y, x + radius, y + radius), fill=color)

    # 贝塞尔干扰曲线：参考验证码常见样式，但透明度更克制
    for _ in range(max(6, CAPTCHA_LINE_COUNT // 3)):
        p0 = (random.randint(-80, work_width // 5), random.randint(0, work_height))
        p1 = (random.randint(work_width // 6, work_width // 2), random.randint(0, work_height))
        p2 = (random.randint(work_width // 2, work_width * 5 // 6), random.randint(0, work_height))
        p3 = (random.randint(work_width * 4 // 5, work_width + 80), random.randint(0, work_height))
        points = [
            cubic_bezier_point(p0, p1, p2, p3, step / 36)
            for step in range(37)
        ]
        color = (
            random.randint(90, 185),
            random.randint(90, 185),
            random.randint(90, 185),
            random.randint(70, 120),
        )
        draw.line(points, fill=color, width=random.randint(5, 10))

    # 假字符，存在感要比真实口令弱
    small_font = load_captcha_font(40)
    decoy_chars = string.ascii_uppercase + string.digits + "#?&%$"
    for _ in range(CAPTCHA_DECOY_COUNT):
        if not small_font:
            break
        token = random.choice(decoy_chars)
        x = random.randint(20, work_width - 90)
        y = random.randint(90, work_height - 90)
        color = (
            random.randint(145, 205),
            random.randint(145, 205),
            random.randint(145, 205),
            random.randint(45, 95),
        )
        draw.text((x, y), token, font=small_font, fill=color)

    chars = list(keyword)
    text_length = max(1, len(chars))
    if text_length <= 4:
        base_size = 220
    elif text_length <= 8:
        base_size = 188
    elif text_length <= 12:
        base_size = 156
    elif text_length <= 18:
        base_size = 128
    else:
        base_size = 108

    spacing = 18
    char_layers: list[tuple[Image.Image, int, int, int]] = []

    for char in chars:
        if char == " ":
            width = base_size // 2
            char_layers.append((Image.new("RGBA", (1, 1), (0, 0, 0, 0)), width, 0, 0))
            continue

        font_size = max(108, base_size + random.randint(-16, 16))
        char_font = load_captcha_font(font_size)
        if not char_font:
            set_captcha_error("Pillow 可用，但没有可用验证码字体")
            return None

        width, height = measure_text(char_font, char)
        width = max(width, font_size)
        height = max(height, font_size)
        pad_x = 28
        pad_y = 24
        layer = Image.new("RGBA", (width + pad_x * 2, height + pad_y * 2), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)

        main_color = random.choice(
            [
                (35, 46, 92, 255),
                (132, 48, 48, 255),
                (39, 110, 78, 255),
                (112, 71, 19, 255),
            ]
        )
        shadow_color = (24, 24, 24, 118)
        stroke_color = random.choice(
            [
                (246, 246, 246, 235),
                (249, 245, 238, 235),
                (240, 248, 255, 235),
                (255, 244, 248, 235),
            ]
        )

        layer_draw.text(
            (pad_x + 6, pad_y + 8),
            char,
            font=char_font,
            fill=shadow_color,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 32),
        )
        layer_draw.text(
            (pad_x, pad_y),
            char,
            font=char_font,
            fill=main_color,
            stroke_width=4,
            stroke_fill=stroke_color,
        )

        angle = random.randint(-16, 16)
        rotated = layer.rotate(angle, resample=resampling.BICUBIC, expand=True)
        char_layers.append((rotated, rotated.size[0], rotated.size[1], font_size))

    max_line_width = work_width - 220
    lines: list[list[tuple[Image.Image, int, int, int]]] = []
    current_line: list[tuple[Image.Image, int, int, int]] = []
    current_line_width = 0

    for layer, width, height, font_size in char_layers:
        if font_size == 0 and not current_line:
            continue

        extra_spacing = spacing if current_line else 0
        next_width = current_line_width + extra_spacing + width
        if current_line and next_width > max_line_width:
            while current_line and current_line[-1][3] == 0:
                current_line.pop()
            if current_line:
                lines.append(current_line)
            current_line = []
            current_line_width = 0
            if font_size == 0:
                continue
            extra_spacing = 0
            next_width = width

        current_line.append((layer, width, height, font_size))
        current_line_width = next_width

    while current_line and current_line[-1][3] == 0:
        current_line.pop()
    if current_line:
        lines.append(current_line)

    if not lines:
        lines = [char_layers]

    if len(lines) > 3:
        merged_lines: list[list[tuple[Image.Image, int, int, int]]] = []
        for index, line in enumerate(lines):
            if index < 2:
                merged_lines.append(line)
                continue
            if len(merged_lines) < 3:
                merged_lines.append(line)
            else:
                merged_lines[-1].extend(line)
        lines = merged_lines

    line_spacing = max(34, base_size // 3)
    line_metrics: list[tuple[int, int]] = []
    for line in lines:
        line_width = 0
        line_height = 0
        for item_index, (_, width, height, _) in enumerate(line):
            if item_index > 0:
                line_width += spacing
            line_width += width
            line_height = max(line_height, height)
        line_metrics.append((line_width, line_height))

    total_height = sum(height for _, height in line_metrics)
    if len(line_metrics) > 1:
        total_height += line_spacing * (len(line_metrics) - 1)
    current_y = max(60, (work_height - total_height) // 2)

    for line_index, line in enumerate(lines):
        line_width, line_height = line_metrics[line_index]
        current_x = max(70, (work_width - line_width) // 2)

        for layer, width, height, font_size in line:
            if font_size == 0:
                current_x += width + spacing
                continue

            bobbing = int(math.sin(current_x / 180) * 10) + random.randint(-10, 10)
            dest_x = current_x + random.randint(-6, 8)
            dest_y = current_y + (line_height - height) // 2 + bobbing + random.randint(-12, 12)
            image.alpha_composite(layer, dest=(dest_x, dest_y))
            current_x += width + spacing

        current_y += line_height + line_spacing

    # 前景少量彩点和短线，增强“验证码感”，但避免遮挡主体
    for _ in range(CAPTCHA_DOT_COUNT // 3):
        x = random.randint(0, work_width - 1)
        y = random.randint(0, work_height - 1)
        radius = random.randint(3, 8)
        color = (
            random.randint(110, 255),
            random.randint(110, 255),
            random.randint(110, 255),
            random.randint(18, 65),
        )
        draw.ellipse((x, y, x + radius, y + radius), fill=color)

    for _ in range(18):
        x1 = random.randint(0, work_width)
        y1 = random.randint(0, work_height)
        x2 = x1 + random.randint(-120, 120)
        y2 = y1 + random.randint(-80, 80)
        draw.line(
            (x1, y1, x2, y2),
            fill=(
                random.randint(120, 220),
                random.randint(120, 220),
                random.randint(120, 220),
                random.randint(18, 55),
            ),
            width=random.randint(2, 5),
        )

    final_image = image.resize((CAPTCHA_WIDTH, CAPTCHA_HEIGHT), resample=resampling.LANCZOS)
    temp_dir = Path(tempfile.mkdtemp(prefix="redpack_"))
    target_path = temp_dir / f"redpack_{redpack_id}.png"
    final_image.convert("RGB").save(target_path, format="PNG", optimize=True)
    set_captcha_error("")
    return target_path


class RedPackConfig:
    """发红包配置管理"""

    def __init__(self):
        self.default_amount: int = DEFAULT_AMOUNT
        self.default_count: int = DEFAULT_COUNT
        self.custom_name: str = ""
        self.self_user_id: Optional[int] = None
        self.stats: dict[str, int] = {
            "total_sent": 0,
            "total_claims": 0,
            "total_amount_sent": 0,
        }
        self.active_packs: dict[str, list[dict[str, Any]]] = {}
        self.chat_locks: dict[str, asyncio.Lock] = {}
        self.load()

    def load(self) -> None:
        """加载配置"""
        if not config_file.exists():
            return

        try:
            with open(config_file, "r", encoding="utf-8") as file:
                data = json.load(file)
            self.default_amount = clamp_int(
                data.get("default_amount", DEFAULT_AMOUNT),
                DEFAULT_AMOUNT,
                MIN_SHARE_AMOUNT,
                999999999,
            )
            self.default_count = clamp_int(
                data.get("default_count", DEFAULT_COUNT),
                DEFAULT_COUNT,
                1,
                500,
            )
            self.custom_name = str(data.get("custom_name", "") or "").strip()
            self.self_user_id = data.get("self_user_id")
            raw_stats = data.get("stats", {})
            if isinstance(raw_stats, dict):
                self.stats = {
                    "total_sent": clamp_int(raw_stats.get("total_sent", 0), 0, 0, 999999999),
                    "total_claims": clamp_int(raw_stats.get("total_claims", 0), 0, 0, 999999999),
                    "total_amount_sent": clamp_int(
                        raw_stats.get("total_amount_sent", 0), 0, 0, 999999999
                    ),
                }
            else:
                self.stats = {
                    "total_sent": 0,
                    "total_claims": 0,
                    "total_amount_sent": 0,
                }

            self.active_packs = {}
            raw_active_packs = data.get("active_packs", {})
            if isinstance(raw_active_packs, dict):
                for chat_id, packs in raw_active_packs.items():
                    if not isinstance(packs, list):
                        continue
                    normalized_packs = []
                    for pack in packs:
                        normalized_pack = self.normalize_pack(pack)
                        if normalized_pack:
                            normalized_packs.append(normalized_pack)
                    if normalized_packs:
                        self.active_packs[str(chat_id)] = normalized_packs
        except Exception as error:
            logs.error(f"[REDPACK] 加载配置失败: {error}")
            self.default_amount = DEFAULT_AMOUNT
            self.default_count = DEFAULT_COUNT
            self.custom_name = ""
            self.self_user_id = None
            self.stats = {
                "total_sent": 0,
                "total_claims": 0,
                "total_amount_sent": 0,
            }
            self.active_packs = {}

    def save(self) -> bool:
        """保存配置"""
        try:
            with open(config_file, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "default_amount": self.default_amount,
                        "default_count": self.default_count,
                        "custom_name": self.custom_name,
                        "self_user_id": self.self_user_id,
                        "stats": self.stats,
                        "active_packs": self.active_packs,
                    },
                    file,
                    indent=4,
                    ensure_ascii=False,
                )
            return True
        except Exception as error:
            logs.error(f"[REDPACK] 保存配置失败: {error}")
            return False

    def normalize_pack(self, pack: Any) -> Optional[dict[str, Any]]:
        """标准化红包记录"""
        if not isinstance(pack, dict):
            return None

        keyword = str(pack.get("keyword", "") or "").strip()
        base_keyword = str(pack.get("base_keyword", keyword) or keyword).strip()
        dynamic_image = bool(pack.get("dynamic_image", False))
        image_code = str(pack.get("image_code", "") or "").strip()
        redpack_id = str(pack.get("redpack_id", "") or "").strip() or generate_redpack_id()
        total_amount = clamp_int(pack.get("total_amount", 0), 0, MIN_SHARE_AMOUNT, 999999999)
        total_count = clamp_int(pack.get("total_count", 0), 0, 1, 500)
        remaining_amount = clamp_int(
            pack.get("remaining_amount", total_amount),
            total_amount,
            0,
            999999999,
        )
        remaining_count = clamp_int(pack.get("remaining_count", total_count), total_count, 0, 500)

        if not keyword or total_amount <= 0 or total_count <= 0:
            return None

        claimed_users = pack.get("claimed_users", {})
        if not isinstance(claimed_users, dict):
            claimed_users = {}

        claims = pack.get("claims", [])
        if not isinstance(claims, list):
            claims = []

        normalized_claims = []
        for claim in claims[-200:]:
            normalized_claim = normalize_claim_record(claim)
            if normalized_claim:
                normalized_claims.append(normalized_claim)

        normalized_claimed_users = {
            build_user_key(user_id): int(amount) for user_id, amount in claimed_users.items()
        }
        for claim in normalized_claims:
            claim_user_id = claim.get("user_id")
            if claim_user_id is None:
                continue
            normalized_claimed_users[build_user_key(claim_user_id)] = int(claim.get("amount", 0))

        used_keywords = pack.get("used_keywords", [])
        if not isinstance(used_keywords, list):
            used_keywords = []
        normalized_used_keywords = []
        for used_keyword in used_keywords[-500:]:
            used_keyword_text = str(used_keyword or "").strip()
            if used_keyword_text:
                normalized_used_keywords.append(used_keyword_text)

        if dynamic_image and not normalized_used_keywords:
            normalized_used_keywords.append(keyword)

        if dynamic_image and not image_code:
            image_code = base_keyword or generate_image_code()

        raw_challenges = pack.get("math_challenges", [])
        if not isinstance(raw_challenges, list):
            raw_challenges = []
        math_challenges: list[dict[str, Any]] = []
        for index, challenge in enumerate(raw_challenges, start=1):
            normalized_challenge = normalize_math_challenge(challenge, index, image_code)
            if normalized_challenge:
                math_challenges.append(normalized_challenge)

        if dynamic_image and not math_challenges:
            old_question = str(pack.get("math_question", "") or "").strip()
            old_answer = pack.get("math_answer")
            try:
                old_answer_int = int(old_answer) if old_answer is not None else None
            except (TypeError, ValueError):
                old_answer_int = None
            if old_question and old_answer_int is not None:
                old_keyword = build_math_claim_keyword(old_answer_int, image_code)
                math_challenges.append(
                    {
                        "index": 1,
                        "math_question": old_question,
                        "math_answer": old_answer_int,
                        "claim_keyword": old_keyword,
                        "keyword_norm": normalize_keyword(old_keyword),
                        "claimed": False,
                    }
                )

        if dynamic_image and not math_challenges:
            math_challenges = generate_image_math_challenges(image_code, total_count)

        if dynamic_image:
            first_available = next((item for item in math_challenges if not item.get("claimed")), math_challenges[0])
            keyword = str(first_available.get("claim_keyword", "") or keyword)
            normalized_used_keywords = [
                str(item.get("claim_keyword", "") or "").strip()
                for item in math_challenges
                if str(item.get("claim_keyword", "") or "").strip()
            ]

        normalized_pack = {
            "redpack_id": redpack_id,
            "keyword": keyword,
            "keyword_norm": normalize_keyword(keyword),
            "base_keyword": base_keyword,
            "dynamic_image": dynamic_image,
            "image_code": image_code,
            "math_question": "",
            "math_answer": None,
            "math_challenges": math_challenges,
            "math_message_id": pack.get("math_message_id"),
            "math_in_caption": bool(pack.get("math_in_caption", True)),
            "used_keywords": normalized_used_keywords,
            "sender_name": str(pack.get("sender_name", "") or "").strip(),
            "total_amount": total_amount,
            "total_count": total_count,
            "remaining_amount": remaining_amount,
            "remaining_count": remaining_count,
            "min_amount": clamp_int(pack.get("min_amount", MIN_SHARE_AMOUNT), MIN_SHARE_AMOUNT, 1, 999999999),
            "message_id": pack.get("message_id"),
            "created_at": float(pack.get("created_at", time.time()) or time.time()),
            "claimed_users": normalized_claimed_users,
            "claims": normalized_claims,
        }

        if normalized_pack["remaining_count"] <= 0 or normalized_pack["remaining_amount"] <= 0:
            return None
        return normalized_pack

    def get_chat_lock(self, chat_id: Any) -> asyncio.Lock:
        """获取聊天锁，避免并发重复领取"""
        key = build_chat_key(chat_id)
        if key not in self.chat_locks:
            self.chat_locks[key] = asyncio.Lock()
        return self.chat_locks[key]

    def set_self_user_id(self, user_id: int) -> None:
        """记录自己的账号 ID"""
        if self.self_user_id == user_id:
            return
        self.self_user_id = user_id
        self.save()

    def validate_amount_count(self, amount: int, count: int) -> Optional[str]:
        """校验总额和个数是否合法"""
        minimum_required = count * MIN_SHARE_AMOUNT
        if amount < minimum_required:
            return (
                f"❌ 总额不足，`{count}` 个红包至少需要 `{minimum_required}`\n"
                f"当前规则：每个红包最少 `{MIN_SHARE_AMOUNT}`"
            )
        return None

    def set_default_amount(self, amount: int) -> str:
        """设置默认金额"""
        self.default_amount = clamp_int(amount, DEFAULT_AMOUNT, MIN_SHARE_AMOUNT, 999999999)
        self.save()
        return f"✅ 默认金额已更新为 `{self.default_amount}`"

    def set_default_count(self, count: int) -> str:
        """设置默认个数"""
        self.default_count = clamp_int(count, DEFAULT_COUNT, 1, 500)
        self.save()
        return f"✅ 默认个数已更新为 `{self.default_count}`"

    def set_custom_name(self, name: str) -> str:
        """设置自定义展示名称"""
        self.custom_name = str(name or "").strip()
        self.save()
        if self.custom_name:
            return f"✅ 红包展示名称已设置为 `{self.custom_name}`"
        return "✅ 已切回自动展示名称"

    def reset(self) -> str:
        """重置默认配置"""
        self.default_amount = DEFAULT_AMOUNT
        self.default_count = DEFAULT_COUNT
        self.custom_name = ""
        self.save()
        return "✅ 已恢复默认配置"

    def remove_pack(self, chat_id: Any, redpack_id: str) -> None:
        """删除指定红包"""
        chat_key = build_chat_key(chat_id)
        packs = self.active_packs.get(chat_key, [])
        remained = [pack for pack in packs if str(pack.get("redpack_id")) != str(redpack_id)]
        if remained:
            self.active_packs[chat_key] = remained
        else:
            self.active_packs.pop(chat_key, None)
        self.save()

    def create_pack(
        self,
        chat_id: Any,
        keyword: str,
        amount: int,
        count: int,
        sender_name: str,
        message_id: Optional[int] = None,
        dynamic_image: bool = False,
    ) -> dict[str, Any]:
        """创建并记录红包"""
        redpack_id = generate_redpack_id()
        base_keyword = keyword.strip()
        image_code = base_keyword or generate_image_code()
        math_challenges = generate_image_math_challenges(image_code, count) if dynamic_image else []
        claim_keyword = (
            str(math_challenges[0].get("claim_keyword", "")) if dynamic_image and math_challenges else base_keyword
        )
        pack = {
            "redpack_id": redpack_id,
            "keyword": claim_keyword,
            "keyword_norm": normalize_keyword(claim_keyword),
            "base_keyword": base_keyword,
            "dynamic_image": dynamic_image,
            "image_code": image_code if dynamic_image else "",
            "math_question": "",
            "math_answer": None,
            "math_challenges": math_challenges,
            "math_message_id": None,
            "math_in_caption": True,
            "used_keywords": [
                str(item.get("claim_keyword", "") or "")
                for item in math_challenges
                if str(item.get("claim_keyword", "") or "")
            ] if dynamic_image else [],
            "sender_name": sender_name.strip(),
            "total_amount": amount,
            "total_count": count,
            "remaining_amount": amount,
            "remaining_count": count,
            "min_amount": MIN_SHARE_AMOUNT,
            "message_id": message_id,
            "created_at": time.time(),
            "claimed_users": {},
            "claims": [],
        }
        chat_key = build_chat_key(chat_id)
        self.active_packs.setdefault(chat_key, []).append(pack)
        self.stats["total_sent"] = clamp_int(self.stats.get("total_sent", 0) + 1, 0, 0, 999999999)
        self.save()
        return pack

    def mark_math_challenge_claimed(self, pack: dict[str, Any], keyword_norm: str) -> None:
        """标记图片红包中的某一道数学题已被领取"""
        if not pack.get("dynamic_image"):
            return

        for challenge in pack.get("math_challenges", []):
            if challenge.get("keyword_norm") != keyword_norm:
                continue
            challenge["claimed"] = True
            break

        next_available = next(
            (item for item in pack.get("math_challenges", []) if not item.get("claimed")),
            None,
        )
        if next_available:
            pack["keyword"] = str(next_available.get("claim_keyword", ""))
            pack["keyword_norm"] = str(next_available.get("keyword_norm", ""))

    def get_active_pack(self, chat_id: Any, keyword: str) -> Optional[dict[str, Any]]:
        """按口令获取当前聊天中最新的可领取红包"""
        chat_key = build_chat_key(chat_id)
        packs = self.active_packs.get(chat_key, [])
        normalized_keyword = normalize_keyword(keyword)
        for pack in reversed(packs):
            if pack.get("remaining_count", 0) <= 0 or pack.get("remaining_amount", 0) <= 0:
                continue
            if pack.get("dynamic_image"):
                for challenge in pack.get("math_challenges", []):
                    if challenge.get("claimed"):
                        continue
                    if challenge.get("keyword_norm") != normalized_keyword:
                        continue
                    pack["_matched_challenge_norm"] = normalized_keyword
                    return pack
                continue
            if pack.get("keyword_norm") != normalized_keyword:
                continue
            return pack
        return None

    def get_active_pack_by_candidates(self, chat_id: Any, candidates: list[str]) -> Optional[dict[str, Any]]:
        """按多个候选口令匹配红包，兼容转发文案中的文字口令"""
        for candidate in candidates:
            pack = self.get_active_pack(chat_id, candidate)
            if pack:
                return pack
        return None

    def list_active_packs(self, chat_id: Any) -> list[dict[str, Any]]:
        """列出当前聊天的有效红包"""
        chat_key = build_chat_key(chat_id)
        packs = self.active_packs.get(chat_key, [])
        valid_packs = [
            pack
            for pack in packs
            if pack.get("remaining_count", 0) > 0 and pack.get("remaining_amount", 0) > 0
        ]
        if len(valid_packs) != len(packs):
            if valid_packs:
                self.active_packs[chat_key] = valid_packs
            else:
                self.active_packs.pop(chat_key, None)
            self.save()
        return valid_packs

    def cleanup_empty_chat(self, chat_id: Any) -> None:
        """清理已空的聊天红包列表"""
        chat_key = build_chat_key(chat_id)
        packs = self.active_packs.get(chat_key, [])
        valid_packs = [
            pack
            for pack in packs
            if pack.get("remaining_count", 0) > 0 and pack.get("remaining_amount", 0) > 0
        ]
        if valid_packs:
            self.active_packs[chat_key] = valid_packs
        else:
            self.active_packs.pop(chat_key, None)

    def clear_chat_packs(self, chat_id: Any) -> str:
        """清空当前聊天中的红包"""
        chat_key = build_chat_key(chat_id)
        count = len(self.active_packs.get(chat_key, []))
        if count == 0:
            return "ℹ️ 当前聊天没有进行中的红包"
        self.active_packs.pop(chat_key, None)
        self.save()
        return f"✅ 已清空当前聊天的 `{count}` 个进行中红包"

    def mark_claim(
        self,
        chat_id: Any,
        pack: dict[str, Any],
        user_id: int,
        amount: int,
        message_id: Optional[int],
        display_name: str,
    ) -> None:
        """记录领取结果"""
        user_key = build_user_key(user_id)
        matched_challenge_norm = str(pack.pop("_matched_challenge_norm", "") or "")
        pack["remaining_amount"] = max(0, int(pack.get("remaining_amount", 0)) - amount)
        pack["remaining_count"] = max(0, int(pack.get("remaining_count", 0)) - 1)
        pack.setdefault("claimed_users", {})[user_key] = amount
        pack.setdefault("claims", []).append(
            {
                "user_id": user_id,
                "amount": amount,
                "display_name": str(display_name or "未知用户"),
                "message_id": message_id,
                "claimed_at": time.time(),
            }
        )
        pack["claims"] = pack["claims"][-200:]
        self.stats["total_claims"] = clamp_int(self.stats.get("total_claims", 0) + 1, 0, 0, 999999999)
        self.stats["total_amount_sent"] = clamp_int(
            self.stats.get("total_amount_sent", 0) + amount,
            0,
            0,
            999999999,
        )
        if matched_challenge_norm:
            self.mark_math_challenge_claimed(pack, matched_challenge_norm)
        self.cleanup_empty_chat(chat_id)
        self.save()

    def is_pack_finished(self, pack: dict[str, Any]) -> bool:
        """判断红包是否已领完"""
        return int(pack.get("remaining_count", 0)) <= 0 or int(pack.get("remaining_amount", 0)) <= 0

    def has_user_claimed(self, pack: dict[str, Any], user_id: int) -> bool:
        """判断用户是否已经领过当前红包"""
        user_key = build_user_key(user_id)
        if user_key in pack.get("claimed_users", {}):
            return True

        for claim in pack.get("claims", []):
            try:
                if int(claim.get("user_id")) == int(user_id):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def has_message_claimed(self, pack: dict[str, Any], message_id: Optional[int]) -> bool:
        """判断同一条消息是否已经触发过领取，避免重复处理"""
        if message_id is None:
            return False

        for claim in pack.get("claims", []):
            try:
                if int(claim.get("message_id")) == int(message_id):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def build_status_text(self, auto_name: str, chat_id: Optional[int] = None) -> str:
        """生成状态文本"""
        display_name = self.custom_name or auto_name
        mode = "自定义" if self.custom_name else "自动识别"
        lines = [
            "**🧧 发红包插件状态**",
            "",
            f"展示名称: `{display_name}` ({mode})",
            f"默认金额: `{self.default_amount}`",
            f"默认个数: `{self.default_count}`",
            f"最低单包: `{MIN_SHARE_AMOUNT}`",
            f"累计发红包: `{self.stats.get('total_sent', 0)}` 次",
            f"累计领取次数: `{self.stats.get('total_claims', 0)}` 次",
            f"累计发出金额: `{self.stats.get('total_amount_sent', 0)}`",
        ]

        if chat_id is not None:
            active_packs = self.list_active_packs(chat_id)
            lines.extend(["", f"当前聊天进行中红包: `{len(active_packs)}` 个"])
            for index, pack in enumerate(reversed(active_packs[-3:]), start=1):
                keyword_label = "图片动态口令" if pack.get("dynamic_image") else pack["keyword"]
                lines.append(
                    f"{index}. 口令 `{keyword_label}` | 剩余 `{pack['remaining_amount']}` / `{pack['remaining_count']}` 个"
                )

        lines.extend(
            [
                "",
                "直接发送示例：",
                command_example("我超有挂 88888 10"),
                command_example("send 我超有挂"),
                command_example('send "我 超 有 挂" 52000 5'),
                "",
                "管理命令：",
                f"{command_example('active')} 查看当前聊天红包",
                f"{command_example('clear')} 清空当前聊天红包",
            ]
        )
        return "\n".join(lines)


config = RedPackConfig()


def parse_send_payload(payload: str) -> tuple[Optional[str], int, int, Optional[str]]:
    """解析发送参数"""
    tokens = split_command_text(payload)
    if not tokens:
        return None, config.default_amount, config.default_count, "❌ 请输入领取口令"

    amount = config.default_amount
    count = config.default_count

    if len(tokens) >= 2 and is_int_token(tokens[-1]) and is_int_token(tokens[-2]):
        amount = clamp_int(tokens[-2], config.default_amount, MIN_SHARE_AMOUNT, 999999999)
        count = clamp_int(tokens[-1], config.default_count, 1, 500)
        keyword = " ".join(tokens[:-2]).strip()
    elif len(tokens) >= 2 and is_int_token(tokens[-1]):
        amount = clamp_int(tokens[-1], config.default_amount, MIN_SHARE_AMOUNT, 999999999)
        keyword = " ".join(tokens[:-1]).strip()
    else:
        keyword = " ".join(tokens).strip()

    if not keyword:
        return None, amount, count, "❌ 请输入领取口令"

    validation_error = config.validate_amount_count(amount, count)
    if validation_error:
        return None, amount, count, validation_error

    return keyword, amount, count, None


def parse_image_payload(payload: str) -> tuple[str, int, int, Optional[str]]:
    """解析图片红包参数，允许省略口令并自动生成图片口令"""
    tokens = split_command_text(payload)
    amount = config.default_amount
    count = config.default_count

    if not tokens:
        keyword = ""
    elif len(tokens) >= 2 and is_int_token(tokens[-1]) and is_int_token(tokens[-2]):
        amount = clamp_int(tokens[-2], config.default_amount, MIN_SHARE_AMOUNT, 999999999)
        count = clamp_int(tokens[-1], config.default_count, 1, 500)
        keyword = " ".join(tokens[:-2]).strip()
    elif is_int_token(tokens[-1]):
        amount = clamp_int(tokens[-1], config.default_amount, MIN_SHARE_AMOUNT, 999999999)
        keyword = " ".join(tokens[:-1]).strip()
    else:
        keyword = " ".join(tokens).strip()

    validation_error = config.validate_amount_count(amount, count)
    if validation_error:
        return keyword, amount, count, validation_error

    return keyword, amount, count, None


def calculate_random_claim_amount(pack: dict[str, Any]) -> int:
    """按拼手气规则计算本次领取金额"""
    remaining_amount = int(pack.get("remaining_amount", 0))
    remaining_count = int(pack.get("remaining_count", 0))
    min_amount = max(1, int(pack.get("min_amount", MIN_SHARE_AMOUNT)))

    if remaining_count <= 1:
        return remaining_amount

    minimum_reserved = min_amount * (remaining_count - 1)
    max_amount = remaining_amount - minimum_reserved
    average_amount = remaining_amount // remaining_count
    lucky_ceiling = max(min_amount, average_amount * 2)
    upper_bound = min(max_amount, lucky_ceiling)

    if upper_bound <= min_amount:
        return min_amount
    return random.randint(min_amount, upper_bound)


def build_settlement_text(pack: dict[str, Any]) -> str:
    """构建红包结算消息"""
    claims = sort_claims_for_settlement(list(pack.get("claims", [])))
    if not claims:
        return (
            f"🧧 拼手气红包[{str(pack.get('redpack_id', ''))}]已结算！\n"
            f"💰 总额: {int(pack.get('total_amount', 0))}｜📦 共 {int(pack.get('total_count', 0))} 个"
        )

    best_claim = max(claims, key=lambda item: int(item.get("amount", 0)))
    detail_lines = []
    for index, claim in enumerate(claims, start=1):
        display_name = compact_settlement_name(claim.get("display_name", "未知用户"))
        amount = int(claim.get("amount", 0))
        suffix = "  🏆" if claim is best_claim else ""
        detail_lines.append(f"{index}. {display_name}")
        detail_lines.append(f"   +{amount}{suffix}")

    return (
        f"🧧 拼手气红包[{str(pack.get('redpack_id', ''))}]已结算！\n"
        f"💰 总额: {int(pack.get('total_amount', 0))}｜📦 共 {int(pack.get('total_count', 0))} 个\n"
        f"领取详情:\n"
        f"{chr(10).join(detail_lines)}"
    )


def build_settlement_rich_text(pack: dict[str, Any]) -> str:
    """构建可展开的红包结算消息"""
    claims = sort_claims_for_settlement(list(pack.get("claims", [])))
    safe_redpack_id = html.escape(str(pack.get("redpack_id", "")))
    total_amount = int(pack.get("total_amount", 0))
    total_count = int(pack.get("total_count", 0))

    if not claims:
        return (
            f"🧧 拼手气红包[{safe_redpack_id}]已结算！\n"
            f"💰 总额: {total_amount}｜📦 共 {total_count} 个"
        )

    best_claim = max(claims, key=lambda item: int(item.get("amount", 0)))
    detail_lines = []
    for index, claim in enumerate(claims, start=1):
        display_name = html.escape(compact_settlement_name(claim.get("display_name", "未知用户")))
        amount = int(claim.get("amount", 0))
        suffix = "  🏆" if claim is best_claim else ""
        detail_lines.append(f"{index}. {display_name}")
        detail_lines.append(f"   +{amount}{suffix}")

    return (
        f"🧧 拼手气红包[{safe_redpack_id}]已结算！\n"
        f"💰 总额: {total_amount}｜📦 共 {total_count} 个\n"
        f"领取详情:\n"
        f"{wrap_expandable(chr(10).join(detail_lines))}"
    )


async def show_help(message: Message) -> None:
    """显示帮助信息"""
    quoted_img_example = command_example('img "图 片 口 令" 52000 5')
    help_text = (
        "**🧧 发红包插件说明**\n\n"
        "文字红包：\n"
        f"{command_example('我超有挂 88888 10')}\n"
        f"{command_example('send 我超有挂')}\n\n"
        "图片红包：\n"
        f"{command_example('img 8888 10')}\n"
        f"{command_example('img 图片口令 8888 10')}\n"
        f"{quoted_img_example}\n\n"
        "发出效果：\n"
        "1. 默认命令发送纯文字红包\n"
        "2. `img` 子命令发送数学题 + 图片口令红包\n"
        "3. 图片红包会一次性给出全部数学题，每道题对应一份红包\n\n"
        "管理命令：\n"
        f"{command_example('status')} 查看插件状态\n"
        f"{command_example('active')} 查看当前聊天红包\n"
        f"{command_example('clear')} 清空当前聊天红包\n"
        f"{command_example('amount <金额>')} 设置默认金额\n"
        f"{command_example('count <个数>')} 设置默认个数\n"
        f"{command_example('name <展示名>')} 设置红包展示名称\n"
        f"{command_example('name auto')} 切回自动展示名称\n"
        f"{command_example('reset')} 恢复默认配置\n\n"
        "玩法说明：\n"
        f"1. 发红包后，插件会记住当前聊天的口令、总额和个数。\n"
        "2. 默认红包是纯文字模式，口令会用 `<code>` 样式包起来方便复制。\n"
        "3. `img` 红包会一次性发送全部数学题和一张图片口令图。\n"
        f"4. 别人发送正确口令时，插件会回复 `+金额`。\n"
        f"5. 总额会按拼手气随机拆分，每个红包最少 `{MIN_SHARE_AMOUNT}`。\n"
        "6. 同一个用户对同一个红包只能领取一次。\n"
        "7. 口令里有空格时，建议用引号包起来。"
    )
    await message.edit(help_text)


@Hook.on_startup()
async def redpack_startup() -> None:
    """插件启动日志"""
    logs.info("[REDPACK] 插件已加载")


@Hook.on_shutdown()
async def redpack_shutdown() -> None:
    """插件关闭日志"""
    logs.info("[REDPACK] 插件已卸载")


async def ensure_self_id(bot: Client) -> int:
    """确保已记录自己的账号 ID"""
    me = await bot.get_me()
    config.set_self_user_id(me.id)
    return me.id


def build_active_text(chat_id: Any) -> str:
    """查看当前聊天进行中的红包"""
    active_packs = config.list_active_packs(chat_id)
    if not active_packs:
        return "ℹ️ 当前聊天没有进行中的红包"

    lines = ["**🧧 当前聊天红包列表**", ""]
    for index, pack in enumerate(reversed(active_packs), start=1):
        keyword_label = "图片动态口令" if pack.get("dynamic_image") else pack["keyword"]
        lines.append(
            f"{index}. 口令 `{keyword_label}` | ID `{pack['redpack_id']}` | "
            f"剩余 `{pack['remaining_amount']}` / `{pack['remaining_count']}` 个"
        )
    return "\n".join(lines)


async def send_redpack_output(
    message: Message,
    bot: Client,
    pack: dict[str, Any],
    sender_name: str,
    keyword: str,
    amount: int,
    count: int,
    *,
    image_mode: bool,
) -> Optional[Any]:
    """发送文字红包或图片红包"""
    reply_to_message_id = message.reply_to_message.id if message.reply_to_message else None

    if not image_mode:
        raise RuntimeError("文字红包应直接走 message.edit 链路")

    captcha_keyword = str(pack.get("image_code", "") or "").strip() or get_pack_claim_keyword(pack) or keyword
    captcha_path = build_captcha_image(captcha_keyword, pack["redpack_id"])
    if not captcha_path:
        raise RuntimeError(f"图片口令生成失败：{get_captcha_error()}")

    try:
        photo_caption, use_combined_caption = build_image_redpack_caption(pack)
        sent_message = await bot.send_photo(
            chat_id=message.chat.id,
            photo=str(captcha_path),
            caption=photo_caption,
            reply_to_message_id=reply_to_message_id,
        )
        last_message = sent_message
        if not use_combined_caption:
            math_text = build_math_questions_text(pack)
            for chunk in split_long_text(math_text):
                last_message = await bot.send_message(
                    chat_id=message.chat.id,
                    text=chunk,
                    reply_to_message_id=getattr(sent_message, "id", None),
                )
        pack["math_message_id"] = getattr(last_message, "id", None)
        pack["math_in_caption"] = use_combined_caption
        return sent_message
    finally:
        if captcha_path.exists():
            try:
                captcha_path.unlink()
                captcha_path.parent.rmdir()
            except Exception:
                pass


async def send_settlement_output(
    bot: Client,
    chat_id: int,
    reply_to_message_id: Optional[int],
    settlement_rich: str,
    settlement_plain: str,
) -> None:
    """优先尝试发送可展开结算，失败则回退为纯文本"""
    placeholder = await bot.send_message(
        chat_id=chat_id,
        text="🧧 红包已结算，正在整理领取详情...",
        reply_to_message_id=reply_to_message_id,
    )

    try:
        if hasattr(placeholder, "edit"):
            await placeholder.edit(settlement_rich, parse_mode="html")
            return
        if hasattr(placeholder, "edit_text"):
            await placeholder.edit_text(settlement_rich, parse_mode="html")
            return
    except Exception as error:
        logs.warning(f"[REDPACK] 结算富文本发送失败，改用纯文本: {error}")

    try:
        if hasattr(placeholder, "edit"):
            await placeholder.edit(settlement_plain)
            return
        if hasattr(placeholder, "edit_text"):
            await placeholder.edit_text(settlement_plain)
            return
    except Exception as error:
        logs.warning(f"[REDPACK] 结算纯文本编辑失败，改为新消息发送: {error}")

    await bot.send_message(
        chat_id=chat_id,
        text=settlement_plain,
        reply_to_message_id=reply_to_message_id,
    )


async def edit_text_redpack_message(
    message: Message,
    *,
    sender_name: str,
    redpack_id: str,
    keyword: str,
    amount: int,
    count: int,
) -> bool:
    """编辑文字红包消息，富文本失败时确保有可读兜底输出。"""
    markdown_text = render_redpack_text_copyable(
        sender_name=sender_name,
        redpack_id=redpack_id,
        keyword=keyword,
        amount=amount,
        count=count,
    )
    try:
        await message.edit(markdown_text, parse_mode="md")
        return True
    except Exception as error:
        logs.warning(f"[REDPACK] 文字红包 Markdown 渲染失败，尝试 HTML 兜底: {error}")

    rich_text = render_redpack_text_rich(
        sender_name=sender_name,
        redpack_id=redpack_id,
        keyword=keyword,
        amount=amount,
        count=count,
    )
    try:
        await message.edit(rich_text, parse_mode="html")
        return True
    except Exception as error:
        logs.warning(f"[REDPACK] 文字红包 HTML 渲染失败，尝试纯文本兜底: {error}")

    try:
        await message.edit(
            render_redpack_text(
                sender_name=sender_name,
                redpack_id=redpack_id,
                keyword=keyword,
                amount=amount,
                count=count,
            )
        )
        return True
    except Exception as error:
        logs.error(f"[REDPACK] 文字红包消息发送失败: {error}")
        return False


async def update_image_math_status(bot: Client, chat_id: int, pack: dict[str, Any]) -> None:
    """刷新图片红包题目领取状态标识"""
    if not pack.get("dynamic_image"):
        return

    try:
        if pack.get("math_in_caption", True):
            caption, use_caption = build_image_redpack_caption(pack)
            if not use_caption:
                return
            message_id = pack.get("message_id")
            if message_id is None:
                return
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=int(message_id),
                caption=caption,
            )
            return

        math_message_id = pack.get("math_message_id")
        if math_message_id is None:
            return
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(math_message_id),
            text=build_math_questions_text(pack),
        )
    except Exception as error:
        logs.warning(
            f"[REDPACK] 刷新图片红包题目状态失败 | chat_id={chat_id} | "
            f"redpack_id={pack.get('redpack_id')} | error={error}"
        )


def should_auto_confirm_transfer(message: Message) -> bool:
    """判断是否为需要自动点击的高额转账确认消息"""
    reply_markup = getattr(message, "reply_markup", None)
    if not reply_markup:
        return False

    inline_keyboard = getattr(reply_markup, "inline_keyboard", None)
    if not inline_keyboard:
        return False

    reply_to_message = getattr(message, "reply_to_message", None)
    reply_to_message_id = getattr(reply_to_message, "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id is None or not is_pending_transfer_confirm_reply(chat_id, reply_to_message_id):
        return False

    text = extract_message_text(message)
    required_keywords = ("转账金额过大", "请确认你的转账")
    if not text or not all(keyword in text for keyword in required_keywords):
        return False

    return True


async def click_transfer_confirm_button(message: Message) -> bool:
    """自动点击转账确认消息中的“确认”按钮"""
    reply_markup = getattr(message, "reply_markup", None)
    inline_keyboard = getattr(reply_markup, "inline_keyboard", None) if reply_markup else None
    if not inline_keyboard:
        return False

    target: Optional[tuple[int, int, str]] = None
    fallback: Optional[tuple[int, int, str]] = None
    for row_idx, row in enumerate(inline_keyboard):
        for col_idx, button in enumerate(row):
            button_text = str(getattr(button, "text", "") or "").strip()
            if not button_text:
                continue
            if "取消" in button_text:
                continue
            if button_text == "确认":
                target = (row_idx, col_idx, button_text)
                break
            if "确认" in button_text and fallback is None:
                fallback = (row_idx, col_idx, button_text)
        if target is not None:
            break

    chosen = target or fallback
    if chosen is None:
        return False

    row_idx, col_idx, button_text = chosen
    await asyncio.sleep(AUTO_CONFIRM_CLICK_DELAY)
    await message.click(row_idx, col_idx)
    logs.info(
        f"[REDPACK] 已自动点击转账确认按钮 | chat_id={getattr(message.chat, 'id', None)} | "
        f"message_id={getattr(message, 'id', None)} | button={button_text}"
    )
    return True


@listener(
    command="redpack",
    description="快速生成红包文案",
    parameters="<口令> [金额] [个数] 或 <img|send|status|active|clear|amount|count|name|reset>",
    is_plugin=True,
)
async def redpack_command(message: Message, bot: Client) -> None:
    """处理发红包命令"""
    me = await bot.get_me()
    config.set_self_user_id(me.id)
    auto_name = build_auto_sender_name(me)
    sender_name = config.custom_name or auto_name

    raw_args = (message.arguments or "").strip()
    if not raw_args:
        await show_help(message)
        return

    parts = raw_args.split(maxsplit=1)
    action = parts[0].lower()
    resolved_action = resolve_subcommand(action)
    rest = parts[1].strip() if len(parts) > 1 else ""
    image_mode = False

    if resolved_action == "help":
        await show_help(message)
        return

    if resolved_action == "status":
        await message.edit(config.build_status_text(auto_name, getattr(message.chat, "id", None)))
        return

    if resolved_action == "active":
        await message.edit(build_active_text(getattr(message.chat, "id", 0)))
        return

    if resolved_action == "clear":
        await message.edit(config.clear_chat_packs(getattr(message.chat, "id", 0)))
        return

    if resolved_action == "amount":
        if not rest or not is_int_token(rest):
            await message.edit(f"❌ 用法：{command_example('amount <金额>')}")
            return
        amount = int(rest)
        if amount < MIN_SHARE_AMOUNT:
            await message.edit(f"❌ 默认金额不能小于 `{MIN_SHARE_AMOUNT}`")
            return
        await message.edit(config.set_default_amount(amount))
        return

    if resolved_action == "count":
        if not rest or not is_int_token(rest):
            await message.edit(f"❌ 用法：{command_example('count <个数>')}")
            return
        count = int(rest)
        validation_error = config.validate_amount_count(config.default_amount, count)
        if validation_error:
            await message.edit(
                f"{validation_error}\n\n先把默认金额调大，或把默认个数调小。"
            )
            return
        await message.edit(config.set_default_count(count))
        return

    if resolved_action == "name":
        lowered = rest.lower()
        if not rest:
            await message.edit(f"❌ 用法：{command_example('name <展示名>')} 或 {command_example('name auto')}")
            return
        if lowered in {"auto", "reset", "default"}:
            await message.edit(config.set_custom_name(""))
            return
        await message.edit(config.set_custom_name(rest))
        return

    if resolved_action == "reset":
        await message.edit(config.reset())
        return

    if resolved_action == "img":
        image_mode = True
        payload = rest
        keyword, amount, count, error = parse_image_payload(payload)
    else:
        payload = rest if resolved_action == "send" else raw_args
        keyword, amount, count, error = parse_send_payload(payload)
    if error:
        await message.edit(error)
        return

    pack = config.create_pack(
        chat_id=getattr(message.chat, "id", 0),
        keyword=keyword or "",
        amount=amount,
        count=count,
        sender_name=sender_name,
        message_id=getattr(message, "id", None),
        dynamic_image=image_mode,
    )

    if not image_mode:
        edited = await edit_text_redpack_message(
            message,
            sender_name=sender_name,
            redpack_id=pack["redpack_id"],
            keyword=keyword or "",
            amount=amount,
            count=count,
        )
        if not edited:
            config.remove_pack(message.chat.id, pack["redpack_id"])
            await message.edit("❌ 红包发送失败，请稍后重试")
            return
        pack["message_id"] = getattr(message, "id", None)
        config.save()
        return

    sent_message = None
    try:
        sent_message = await send_redpack_output(
            message=message,
            bot=bot,
            pack=pack,
            sender_name=sender_name,
            keyword=keyword or "",
            amount=amount,
            count=count,
            image_mode=image_mode,
        )
    except Exception as error:
        config.remove_pack(message.chat.id, pack["redpack_id"])
        logs.error(f"[REDPACK] 发送红包消息失败: {error}")
        await message.edit("❌ 红包发送失败，请稍后重试")
        return

    if sent_message:
        pack["message_id"] = getattr(sent_message, "id", None)
        config.save()
    if DELETE_COMMAND_MESSAGE:
        try:
            await message.delete()
        except Exception:
            pass


@listener(is_plugin=True, incoming=True, outgoing=True, ignore_edited=True)
async def redpack_claim_listener(message: Message, bot: Client) -> None:
    """监听别人发送口令并发放红包"""
    if not getattr(message, "chat", None) or not getattr(message.chat, "id", None):
        return
    if not getattr(message, "from_user", None):
        return

    sender = message.from_user
    config.self_user_id = config.self_user_id or await ensure_self_id(bot)
    if getattr(sender, "is_bot", False):
        return
    if getattr(sender, "id", None) is None:
        return

    text = extract_message_text(message)
    if not text or text.startswith(COMMAND_PREFIX) or text.startswith("/") or text.startswith(","):
        return

    chat_id = message.chat.id
    claim_message_id = getattr(message, "id", None)
    claim_candidates = extract_claim_text_candidates(text)
    lock = config.get_chat_lock(chat_id)
    settlement_text = None
    settlement_rich_text = None
    should_update_math_status = False

    async with lock:
        pack = config.get_active_pack_by_candidates(chat_id, claim_candidates)
        if not pack:
            return

        if config.has_message_claimed(pack, claim_message_id):
            return

        if config.has_user_claimed(pack, sender.id):
            return

        claim_amount = calculate_random_claim_amount(pack)
        if claim_amount <= 0:
            return

        config.mark_claim(
            chat_id=chat_id,
            pack=pack,
            user_id=sender.id,
            amount=claim_amount,
            message_id=claim_message_id,
            display_name=build_claim_user_name(sender),
        )
        should_update_math_status = bool(pack.get("dynamic_image"))
        if config.is_pack_finished(pack):
            settlement_text = build_settlement_text(pack)
            settlement_rich_text = build_settlement_rich_text(pack)

    claim_reply_message = None
    try:
        claim_reply_message = await bot.send_message(
            chat_id=chat_id,
            text=build_claim_reply(claim_amount),
            reply_to_message_id=claim_message_id,
        )
    except Exception as error:
        logs.warning(f"[REDPACK] 发送领取提示失败: {error}")

    if claim_reply_message is not None:
        register_pending_transfer_confirm(chat_id, getattr(claim_reply_message, "id", None))
        if CLAIM_REPLY_DELETE_DELAY > 0:
            asyncio.create_task(delete_message_later(claim_reply_message))

    if should_update_math_status:
        await update_image_math_status(bot, chat_id, pack)

    if settlement_text:
        await send_settlement_output(
            bot=bot,
            chat_id=chat_id,
            reply_to_message_id=pack.get("message_id"),
            settlement_rich=settlement_rich_text or settlement_text,
            settlement_plain=settlement_text,
        )


@listener(is_plugin=True, incoming=True, outgoing=False, ignore_edited=False, priority=20)
async def redpack_transfer_confirm_listener(message: Message, bot: Client) -> None:
    """监听高额转账确认消息并自动点击确认按钮"""
    if not AUTO_CONFIRM_ENABLED:
        return
    if not getattr(message, "chat", None) or not getattr(message.chat, "id", None):
        return
    if not should_auto_confirm_transfer(message):
        return

    try:
        clicked = await click_transfer_confirm_button(message)
        if not clicked:
            logs.warning(
                f"[REDPACK] 检测到转账确认消息，但未找到可点击的确认按钮 | "
                f"chat_id={message.chat.id} | message_id={getattr(message, 'id', None)}"
            )
    except Exception as error:
        logs.error(
            f"[REDPACK] 自动点击转账确认失败 | "
            f"chat_id={message.chat.id} | message_id={getattr(message, 'id', None)} | error={error}"
        )
