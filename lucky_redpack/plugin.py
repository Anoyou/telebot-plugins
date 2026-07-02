"""拼手气口令红包插件。

账号主人通过命令创建红包，群友发送当前财富密码领取。奖励消息必须由
UserBot 回复领取者消息，以便复用平台现有的转账链路。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import html
import inspect
import json
import os
import random
import shlex
import string
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

try:
    from app.worker.command import current_command_prefix
except Exception:  # pragma: no cover - old TelePilot compatibility
    def current_command_prefix(*, fallback: str = ",") -> str:
        return fallback

try:
    from app.worker.plugins.base import public_entity_display_name
except ImportError:  # pragma: no cover - older TelePilot compatibility
    def public_entity_display_name(entity: Any, *, fallback_id: int | str | None = None, default: str = "玩家") -> str:
        if entity is not None:
            username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
            if username:
                return username
            entity_id = getattr(entity, "id", None)
            name = " ".join(
                part
                for part in (
                    str(getattr(entity, "first_name", "") or "").strip(),
                    str(getattr(entity, "last_name", "") or "").strip(),
                )
                if part
            )
            if name:
                return name
            if entity_id not in (None, ""):
                return str(entity_id)
        return str(fallback_id) if fallback_id not in (None, "") else default

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:  # pragma: no cover - depends on worker environment
    Image = ImageDraw = ImageFont = None
    HAS_PIL = False


PLUGIN_VERSION = "1.3.1"
PLUGIN_KEY = "lucky_redpack"
DEFAULT_COMMAND = "rp"
DEFAULT_AMOUNT = 88888
DEFAULT_COUNT = 10
DEFAULT_MIN_SHARE_AMOUNT = 1
DEFAULT_SUFFIX_LENGTH = 4
DEFAULT_TTL_SECONDS = 3600
DEFAULT_IMAGE_PASSWORD_ENABLED = False
DEFAULT_ALLOW_OWNER_CLAIM = True
MAX_AMOUNT = 999_999_999
MAX_COUNT = 500
SUFFIX_CHARS = string.ascii_uppercase + string.digits
PACK_CODE_CHARS = string.ascii_uppercase + string.digits
IMAGE_WIDTH = 980
IMAGE_HEIGHT = 320
PLUGIN_DIR = Path(__file__).resolve().parent
STATE_DIR_ENV = "LUCKY_REDPACK_STATE_DIR"
DEFAULT_STATE_DIR = Path(tempfile.gettempdir()) / "telepilot_lucky_redpack_state"
BUNDLED_FONT_CANDIDATES = [
    PLUGIN_DIR.parent / "redpack-byRBQ" / "assets" / "font.ttf",
]
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]
FONT_SEARCH_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")]
FONT_SEARCH_KEYWORDS = ["notosanscjk", "notoserifcjk", "sourcehansans", "wqy", "wenquanyi", "pingfang"]
FONT_EXTENSIONS = {".ttf", ".ttc", ".otf"}
_font_path_cache: str | None = None
_font_search_done = False
_image_last_error = ""


@dataclass
class ClaimRecord:
    user_id: int
    display_name: str
    amount: int
    message_id: int | None
    claimed_at: float = field(default_factory=time.time)


@dataclass
class LuckyRedpack:
    pack_code: str
    chat_id: int
    creator_user_id: int
    base_keyword: str
    total_amount: int
    total_count: int
    min_share_amount: int
    suffix_length: int
    created_at: float
    expires_at: float
    image_mode: bool = False
    message_id: int | None = None
    current_suffix: str = ""
    remaining_amount: int = 0
    remaining_count: int = 0
    claimed_user_ids: set[int] = field(default_factory=set)
    used_passwords: set[str] = field(default_factory=set)
    claims: list[ClaimRecord] = field(default_factory=list)

    @property
    def current_password(self) -> str:
        return f"{self.base_keyword}{self.current_suffix}"

    def is_finished(self) -> bool:
        return self.remaining_count <= 0 or self.remaining_amount <= 0

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_event(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("event"))


def _payload_message(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("message"))


def _payload_source(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("source"))


def _payload_actor(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("actor"))


def _payload_sender(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("sender"))


def _payload_reply_to(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict(payload.get("reply_to"))


def _payload_event_type(payload: dict[str, Any]) -> str:
    event = _payload_event(payload)
    source = _payload_source(payload)
    trigger = _dict(payload.get("trigger"))
    message = _payload_message(payload)
    return str(
        payload.get("event_type")
        or event.get("type")
        or source.get("event_type")
        or source.get("type")
        or trigger.get("event")
        or trigger.get("type")
        or message.get("type")
        or ""
    ).strip()


def _payload_chat_id(payload: dict[str, Any]) -> int:
    event = _payload_event(payload)
    source = _payload_source(payload)
    message = _payload_message(payload)
    session = _dict(payload.get("session"))
    chat = _dict(payload.get("chat"))
    try:
        return int(
            payload.get("chat_id")
            or message.get("chat_id")
            or chat.get("id")
            or event.get("chat_id")
            or source.get("chat_id")
            or session.get("chat_id")
            or 0
        )
    except (TypeError, ValueError):
        return 0


def _payload_message_id(payload: dict[str, Any]) -> int | None:
    event = _payload_event(payload)
    source = _payload_source(payload)
    message = _payload_message(payload)
    reply_to = _payload_reply_to(payload)
    try:
        value = int(
            payload.get("message_id")
            or payload.get("source_message_id")
            or message.get("message_id")
            or message.get("id")
            or reply_to.get("message_id")
            or event.get("message_id")
            or event.get("id")
            or source.get("message_id")
            or 0
        )
    except (TypeError, ValueError):
        value = 0
    return value or None


def _payload_message_text(payload: dict[str, Any]) -> str:
    event = _payload_event(payload)
    source = _payload_source(payload)
    message = _payload_message(payload)
    trigger = _dict(payload.get("trigger"))
    return str(
        payload.get("message_text")
        or payload.get("text")
        or message.get("text")
        or message.get("raw_text")
        or event.get("text")
        or event.get("raw_text")
        or source.get("text")
        or trigger.get("text")
        or ""
    ).strip()


def _payload_sender_id(payload: dict[str, Any]) -> int:
    actor = _payload_actor(payload)
    sender = _payload_sender(payload)
    event = _payload_event(payload)
    source = _payload_source(payload)
    message = _payload_message(payload)
    try:
        return int(
            sender.get("user_id")
            or sender.get("id")
            or actor.get("user_id")
            or actor.get("id")
            or payload.get("sender_user_id")
            or event.get("user_id")
            or event.get("sender_id")
            or message.get("sender_id")
            or source.get("user_id")
            or 0
        )
    except (TypeError, ValueError):
        return 0


def _payload_sender_name(payload: dict[str, Any], sender_id: int) -> str:
    actor = _payload_actor(payload)
    sender = _payload_sender(payload)
    event = _payload_event(payload)
    source = _payload_source(payload)
    message = _payload_message(payload)
    name = (
        sender.get("display_name")
        or sender.get("name")
        or actor.get("display_name")
        or actor.get("name")
        or payload.get("sender_name")
        or event.get("display_name")
        or message.get("display_name")
        or source.get("display_name")
        or sender_id
        or "玩家"
    )
    return str(name).strip() or "玩家"


def _payload_actor_is_bot(payload: dict[str, Any]) -> bool:
    actor = _payload_actor(payload)
    sender = _payload_sender(payload)
    message = _payload_message(payload)
    return bool(sender.get("is_bot") or actor.get("is_bot") or message.get("sender_is_bot"))


def _chat_id_from_event(event: Any) -> int:
    value = getattr(event, "chat_id", None)
    value = getattr(value, "channel_id", value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sender_id_from_event(event: Any) -> int:
    for target in (event, getattr(event, "message", None)):
        if target is None:
            continue
        sender_id = getattr(target, "sender_id", None)
        if sender_id is None:
            sender = getattr(target, "sender", None) or getattr(target, "from_user", None)
            sender_id = getattr(sender, "id", None) if sender is not None else None
        if sender_id is None:
            from_id = getattr(target, "from_id", None)
            sender_id = (
                getattr(from_id, "user_id", None)
                or getattr(from_id, "channel_id", None)
                or getattr(from_id, "chat_id", None)
                or getattr(from_id, "id", None)
            )
        try:
            return int(sender_id or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _message_id_from_event(event: Any) -> int | None:
    try:
        value = int(getattr(event, "id", 0) or getattr(getattr(event, "message", None), "id", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value or None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _is_outgoing_event(event: Any) -> bool:
    for target in (event, getattr(event, "message", None)):
        if target is None:
            continue
        for attr in ("outgoing", "out", "is_outgoing"):
            value = getattr(target, attr, None)
            if callable(value):
                value = value()
            if value is not None and bool(value):
                return True
    return False


def _event_text(event: Any) -> str:
    return str(
        getattr(event, "raw_text", None)
        or getattr(getattr(event, "message", None), "raw_text", None)
        or getattr(event, "text", None)
        or getattr(getattr(event, "message", None), "text", None)
        or ""
    ).strip()


def _normalize_password(value: str) -> str:
    return "".join(str(value or "").split()).casefold()


def _split_args(args: list[str]) -> list[str]:
    raw = " ".join(args).strip()
    if not raw:
        return []
    try:
        return [item for item in shlex.split(raw) if item]
    except ValueError:
        return [item for item in raw.split() if item]


def _is_int_token(value: str) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def parse_create_args(args: list[str], default_amount: int, default_count: int) -> tuple[str, int, int, str | None]:
    tokens = _split_args(args)
    if not tokens:
        return "", default_amount, default_count, "请输入口令。"

    amount = default_amount
    count = default_count
    if len(tokens) >= 3 and _is_int_token(tokens[-1]) and _is_int_token(tokens[-2]):
        count = int(tokens[-1])
        amount = int(tokens[-2])
        keyword = " ".join(tokens[:-2]).strip()
    elif len(tokens) >= 2 and _is_int_token(tokens[-1]):
        amount = int(tokens[-1])
        keyword = " ".join(tokens[:-1]).strip()
    else:
        keyword = " ".join(tokens).strip()

    if not keyword:
        return "", amount, count, "请输入口令。"
    return keyword, amount, count, None


def calculate_random_claim_amount(pack: LuckyRedpack) -> int:
    if pack.remaining_count <= 1:
        return pack.remaining_amount

    min_amount = max(1, pack.min_share_amount)
    minimum_reserved = min_amount * (pack.remaining_count - 1)
    max_amount = pack.remaining_amount - minimum_reserved
    average_amount = pack.remaining_amount // pack.remaining_count
    lucky_ceiling = max(min_amount, average_amount * 2)
    upper_bound = min(max_amount, lucky_ceiling)
    if upper_bound <= min_amount:
        return min_amount
    return random.randint(min_amount, upper_bound)


def _html(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _set_image_error(reason: str) -> None:
    global _image_last_error
    _image_last_error = str(reason or "").strip()


def get_image_error() -> str:
    return _image_last_error or "未知原因"


def _can_font_render_text(font_path: str, text: str) -> bool:
    if not HAS_PIL or not font_path:
        return False
    try:
        font = ImageFont.truetype(font_path, 72)
        mask = font.getmask(text or "图")
        return mask.getbbox() is not None
    except Exception:
        return False


def _find_font_path(sample_text: str) -> str | None:
    global _font_path_cache, _font_search_done

    if _font_path_cache and _can_font_render_text(_font_path_cache, sample_text):
        return _font_path_cache

    for candidate in [*BUNDLED_FONT_CANDIDATES, *[Path(path) for path in FONT_CANDIDATES]]:
        if candidate.exists() and _can_font_render_text(str(candidate), sample_text):
            _font_path_cache = str(candidate)
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
                if not candidate.is_file() or candidate.suffix.lower() not in FONT_EXTENSIONS:
                    continue
                lowered_name = candidate.name.lower()
                if not any(keyword in lowered_name for keyword in FONT_SEARCH_KEYWORDS):
                    continue
                if _can_font_render_text(str(candidate), sample_text):
                    _font_path_cache = str(candidate)
                    return _font_path_cache
        except Exception:
            continue
    return _font_path_cache


def _load_password_font(password: str, size: int) -> Any:
    if not HAS_PIL:
        return None
    font_path = _find_font_path(password)
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def build_password_image(password: str) -> Path | None:
    if not HAS_PIL:
        _set_image_error("未安装 Pillow/PIL，请在 TelePilot worker 环境安装 Pillow 后重启")
        return None

    password = str(password or "").strip()
    if not password:
        _set_image_error("财富密码为空")
        return None

    try:
        scale = 2
        work_width = IMAGE_WIDTH * scale
        work_height = IMAGE_HEIGHT * scale
        image = Image.new("RGBA", (work_width, work_height), (250, 248, 244, 255))
        draw = ImageDraw.Draw(image)

        for band in range(6):
            top = band * work_height // 6
            bottom = (band + 1) * work_height // 6
            draw.rectangle(
                (0, top, work_width, bottom),
                fill=(248 + random.randint(-5, 5), 244 + random.randint(-4, 6), 238 + random.randint(-4, 8), 255),
            )

        for _ in range(950):
            x = random.randint(0, work_width - 1)
            y = random.randint(0, work_height - 1)
            radius = random.randint(2, 7)
            draw.ellipse(
                (x, y, x + radius, y + radius),
                fill=(
                    random.randint(120, 240),
                    random.randint(110, 220),
                    random.randint(95, 210),
                    random.randint(28, 88),
                ),
            )

        for _ in range(26):
            x1 = random.randint(-80, work_width)
            y1 = random.randint(0, work_height)
            x2 = x1 + random.randint(100, 340)
            y2 = y1 + random.randint(-120, 120)
            draw.line(
                (x1, y1, x2, y2),
                fill=(
                    random.randint(70, 180),
                    random.randint(70, 180),
                    random.randint(70, 180),
                    random.randint(55, 125),
                ),
                width=random.randint(4, 9),
            )

        length = max(1, len(password))
        font_size = 176 if length <= 8 else 142 if length <= 12 else 116 if length <= 18 else 92
        font = _load_password_font(password, font_size)
        if font is None:
            _set_image_error("没有可用字体")
            return None

        chars = list(password)
        layers: list[Image.Image] = []
        total_width = 0
        for char in chars:
            bbox = draw.textbbox((0, 0), char, font=font)
            width = max(font_size // 2, bbox[2] - bbox[0])
            height = max(font_size, bbox[3] - bbox[1])
            layer = Image.new("RGBA", (width + 56, height + 58), (0, 0, 0, 0))
            layer_draw = ImageDraw.Draw(layer)
            color = random.choice([(34, 48, 96, 255), (128, 42, 42, 255), (34, 102, 74, 255), (112, 69, 20, 255)])
            layer_draw.text((34, 34), char, font=font, fill=(20, 20, 20, 105), stroke_width=2, stroke_fill=(0, 0, 0, 35))
            layer_draw.text((28, 26), char, font=font, fill=color, stroke_width=4, stroke_fill=(255, 249, 240, 232))
            rotated = layer.rotate(random.randint(-14, 14), resample=Image.Resampling.BICUBIC if hasattr(Image, "Resampling") else Image.BICUBIC, expand=True)
            layers.append(rotated)
            total_width += rotated.size[0] + 10

        x = max(24, (work_width - total_width) // 2)
        center_y = work_height // 2
        for layer in layers:
            y = center_y - layer.size[1] // 2 + random.randint(-18, 18)
            image.alpha_composite(layer, dest=(x, y))
            x += layer.size[0] + random.randint(4, 16)

        for _ in range(240):
            x = random.randint(0, work_width - 1)
            y = random.randint(0, work_height - 1)
            radius = random.randint(3, 8)
            draw.ellipse((x, y, x + radius, y + radius), fill=(random.randint(110, 240), random.randint(110, 240), random.randint(110, 240), random.randint(18, 58)))

        resampling = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        final_image = image.resize((IMAGE_WIDTH, IMAGE_HEIGHT), resample=resampling)
        temp_dir = Path(tempfile.mkdtemp(prefix="lucky_redpack_"))
        target_path = temp_dir / "wealth_password.png"
        final_image.convert("RGB").save(target_path, format="PNG", optimize=True)
        _set_image_error("")
        return target_path
    except Exception as exc:
        _set_image_error(f"{type(exc).__name__}: {exc}")
        return None


def render_claim_details(pack: LuckyRedpack) -> str:
    if not pack.claims:
        return ""
    best = max(pack.claims, key=lambda claim: claim.amount)
    lines = ["领取详情："]
    for index, claim in enumerate(pack.claims, start=1):
        suffix = " 🏆" if claim is best else ""
        lines.append(f"{index}. {_html(claim.display_name)} +{claim.amount}{suffix}")
    return f"<blockquote expandable>{chr(10).join(lines)}</blockquote>"


def render_redpack_message(pack: LuckyRedpack) -> str:
    claimed_count = pack.total_count - pack.remaining_count
    password_line = "财富密码：见图片" if pack.image_mode else f"财富密码：{_html(pack.current_password)}"
    text = (
        "🧧 拼手气红包\n"
        f"红包代码：{pack.pack_code}\n"
        f"总额：{pack.total_amount}｜剩余：{pack.remaining_count}/{pack.total_count}\n"
        f"{password_line}\n"
        "发送财富密码即可领取\n"
        "提示：财富密码被领一次会随机变动"
        + (f"\n已领取：{claimed_count} 人" if claimed_count else "")
    )
    claim_details = render_claim_details(pack)
    if claim_details:
        text = f"{text}\n{claim_details}"
    return text


def render_settlement(pack: LuckyRedpack, *, expired: bool = False) -> str:
    title = "🕒 拼手气红包已超时" if expired else "🧧 拼手气红包已领完"
    lines = [
        title,
        f"红包代码：{pack.pack_code}",
        f"总额：{pack.total_amount}｜已领：{len(pack.claims)}/{pack.total_count}｜剩余：{pack.remaining_amount}",
    ]
    if not pack.claims:
        return "\n".join(lines)
    best = max(pack.claims, key=lambda claim: claim.amount)
    lines.append("领取详情：")
    for index, claim in enumerate(pack.claims, start=1):
        suffix = " 🏆" if claim is best else ""
        lines.append(f"{index}. {claim.display_name} +{claim.amount}{suffix}")
    return "\n".join(lines)


def _send_action(
    text: str,
    *,
    reply_to_message_id: int | None = None,
    send_via: str = "userbot_reply",
    parse_mode: str | None = "html",
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
    return action


def _delete_action(message_id: int, *, chat_id: int, send_via: str = "userbot_reply") -> dict[str, Any]:
    return {
        "type": "delete_message",
        "message_id": int(message_id),
        "chat_id": int(chat_id),
        "send_via": send_via,
    }


def _pack_to_payload(pack: LuckyRedpack) -> dict[str, Any]:
    return {
        "pack_code": pack.pack_code,
        "chat_id": pack.chat_id,
        "creator_user_id": pack.creator_user_id,
        "base_keyword": pack.base_keyword,
        "total_amount": pack.total_amount,
        "total_count": pack.total_count,
        "min_share_amount": pack.min_share_amount,
        "suffix_length": pack.suffix_length,
        "created_at": pack.created_at,
        "expires_at": pack.expires_at,
        "image_mode": pack.image_mode,
        "message_id": pack.message_id,
        "current_suffix": pack.current_suffix,
        "remaining_amount": pack.remaining_amount,
        "remaining_count": pack.remaining_count,
        "claimed_user_ids": sorted(pack.claimed_user_ids),
        "used_passwords": sorted(pack.used_passwords),
        "claims": [
            {
                "user_id": claim.user_id,
                "display_name": claim.display_name,
                "amount": claim.amount,
                "message_id": claim.message_id,
                "claimed_at": claim.claimed_at,
            }
            for claim in pack.claims
        ],
    }


def _pack_from_payload(payload: dict[str, Any]) -> LuckyRedpack | None:
    try:
        pack = LuckyRedpack(
            pack_code=str(payload.get("pack_code") or ""),
            chat_id=int(payload.get("chat_id") or 0),
            creator_user_id=int(payload.get("creator_user_id") or 0),
            base_keyword=str(payload.get("base_keyword") or ""),
            total_amount=int(payload.get("total_amount") or 0),
            total_count=int(payload.get("total_count") or 0),
            min_share_amount=int(payload.get("min_share_amount") or DEFAULT_MIN_SHARE_AMOUNT),
            suffix_length=int(payload.get("suffix_length") or DEFAULT_SUFFIX_LENGTH),
            created_at=float(payload.get("created_at") or time.time()),
            expires_at=float(payload.get("expires_at") or 0),
            image_mode=bool(payload.get("image_mode")),
            message_id=(
                int(payload["message_id"])
                if payload.get("message_id") not in (None, "")
                else None
            ),
            current_suffix=str(payload.get("current_suffix") or ""),
            remaining_amount=int(payload.get("remaining_amount") or 0),
            remaining_count=int(payload.get("remaining_count") or 0),
        )
    except (TypeError, ValueError):
        return None
    pack.claimed_user_ids = {
        int(item)
        for item in payload.get("claimed_user_ids", [])
        if str(item).strip()
    }
    pack.used_passwords = {str(item) for item in payload.get("used_passwords", []) if str(item)}
    claims = []
    for item in payload.get("claims", []):
        if not isinstance(item, dict):
            continue
        try:
            claims.append(
                ClaimRecord(
                    user_id=int(item.get("user_id") or 0),
                    display_name=str(item.get("display_name") or "玩家"),
                    amount=int(item.get("amount") or 0),
                    message_id=(int(item["message_id"]) if item.get("message_id") not in (None, "") else None),
                    claimed_at=float(item.get("claimed_at") or time.time()),
                )
            )
        except (TypeError, ValueError):
            continue
    pack.claims = claims
    return pack if pack.pack_code and pack.chat_id else None


def _packs_from_json(raw: Any) -> list[LuckyRedpack]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not raw:
        return []
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    packs: list[LuckyRedpack] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pack = _pack_from_payload(item)
        if pack is not None:
            packs.append(pack)
    return packs


def _packs_to_json(packs: list[LuckyRedpack]) -> str:
    return json.dumps([_pack_to_payload(pack) for pack in packs], ensure_ascii=False)


def _looks_like_password_attempt(text: str, suffix_length: int) -> bool:
    normalized = _normalize_password(text)
    if len(normalized) <= suffix_length or len(normalized) > 80:
        return False
    suffix = normalized[-suffix_length:].upper()
    return all(char in SUFFIX_CHARS for char in suffix)


@register
class LuckyRedpackPlugin(Plugin):
    key = PLUGIN_KEY
    display_name = "拼手气口令红包"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {
        "command",
        "default_amount",
        "default_count",
        "min_share_amount",
        "suffix_length",
        "ttl_seconds",
        "image_password_enabled",
        "delete_command_message",
        "allow_owner_claim",
    }

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self._default_amount = DEFAULT_AMOUNT
        self._default_count = DEFAULT_COUNT
        self._min_share_amount = DEFAULT_MIN_SHARE_AMOUNT
        self._suffix_length = DEFAULT_SUFFIX_LENGTH
        self._ttl_seconds = DEFAULT_TTL_SECONDS
        self._image_password_enabled = DEFAULT_IMAGE_PASSWORD_ENABLED
        self._delete_command_message = False
        self._allow_owner_claim = DEFAULT_ALLOW_OWNER_CLAIM
        self._packs: dict[int, list[LuckyRedpack]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def _track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _new_suffix(self, pack: LuckyRedpack | None = None) -> str:
        used = pack.used_passwords if pack is not None else set()
        for _ in range(100):
            suffix = "".join(random.choice(SUFFIX_CHARS) for _ in range(self._suffix_length))
            password = f"{pack.base_keyword}{suffix}" if pack is not None else suffix
            if _normalize_password(password) not in used:
                return suffix
        return "".join(random.choice(SUFFIX_CHARS) for _ in range(self._suffix_length))

    def _new_pack_code(self, chat_id: int) -> str:
        existing = {pack.pack_code for pack in self._packs.get(chat_id, [])}
        return self._new_pack_code_for_existing(existing)

    def _new_pack_code_for_existing(self, existing: set[str]) -> str:
        for _ in range(100):
            code = "".join(random.choice(PACK_CODE_CHARS) for _ in range(6))
            if code not in existing:
                return code
        return "".join(random.choice(PACK_CODE_CHARS) for _ in range(8))

    def _active_packs(self, chat_id: int) -> list[LuckyRedpack]:
        packs = self._packs.get(chat_id, [])
        active = [pack for pack in packs if not pack.is_finished() and not pack.is_expired()]
        if active:
            self._packs[chat_id] = active
        else:
            self._packs.pop(chat_id, None)
        return active

    def _state_key(self, account_id: int, chat_id: int) -> str:
        return f"lucky_redpack:{int(account_id)}:{int(chat_id)}:packs"

    def _state_dir(self) -> Path:
        configured = str(os.environ.get(STATE_DIR_ENV) or "").strip()
        return Path(configured).expanduser() if configured else DEFAULT_STATE_DIR

    def _state_file(self, account_id: int, chat_id: int) -> Path:
        return self._state_dir() / f"{int(account_id)}_{int(chat_id)}.json"

    @contextmanager
    def _state_file_lock(self, account_id: int, chat_id: int):
        lock_file = self._state_dir() / f"{int(account_id)}_{int(chat_id)}.lock"
        lock_handle = None
        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = lock_file.open("a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if lock_handle is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_handle.close()

    def _load_file_packs(self, account_id: int, chat_id: int) -> list[LuckyRedpack]:
        path = self._state_file(account_id, chat_id)
        if not path.exists():
            return []
        try:
            return _packs_from_json(path.read_text(encoding="utf-8"))
        except OSError:
            return []

    def _save_file_packs(self, account_id: int, chat_id: int, packs: list[LuckyRedpack]) -> None:
        path = self._state_file(account_id, chat_id)
        if not packs:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(f".{os.getpid()}.tmp")
            tmp_path.write_text(_packs_to_json(packs), encoding="utf-8")
            os.replace(tmp_path, path)
        except OSError:
            return

    def _active_from(self, packs: list[LuckyRedpack]) -> list[LuckyRedpack]:
        return [pack for pack in packs if not pack.is_finished() and not pack.is_expired()]

    def _sync_memory_packs(self, chat_id: int, packs: list[LuckyRedpack]) -> None:
        if packs:
            self._packs[chat_id] = packs
        else:
            self._packs.pop(chat_id, None)

    def _sync_loaded_packs(self, chat_id: int, packs: list[LuckyRedpack]) -> list[LuckyRedpack]:
        existing_by_code = {pack.pack_code: pack for pack in self._packs.get(chat_id, [])}
        merged: list[LuckyRedpack] = []
        for pack in packs:
            existing = existing_by_code.get(pack.pack_code)
            if existing is not None and existing is not pack:
                for field_name in pack.__dataclass_fields__:
                    setattr(existing, field_name, getattr(pack, field_name))
                merged.append(existing)
            else:
                merged.append(pack)
        self._sync_memory_packs(chat_id, merged)
        return merged

    async def _load_active_packs(self, ctx: PluginContext, chat_id: int) -> list[LuckyRedpack]:
        redis = getattr(ctx, "redis", None)
        packs: list[LuckyRedpack] = []
        if redis is not None:
            try:
                packs = _packs_from_json(await redis.get(self._state_key(ctx.account_id, chat_id)))
            except Exception as exc:
                if ctx.log:
                    await ctx.log("warn", f"[lucky_redpack] 读取 Redis 红包状态失败，改用本地状态：{type(exc).__name__}")
                packs = []
        if not packs:
            packs = self._load_file_packs(ctx.account_id, chat_id)
        if not packs:
            packs = self._active_packs(chat_id)
        active = self._active_from(packs)
        active = self._sync_loaded_packs(chat_id, active)
        if len(active) != len(packs):
            await self._save_active_packs(ctx, chat_id, active)
        return active

    async def _save_active_packs(self, ctx: PluginContext, chat_id: int, packs: list[LuckyRedpack]) -> None:
        active = self._active_from(packs)
        self._sync_memory_packs(chat_id, active)
        self._save_file_packs(ctx.account_id, chat_id, active)
        redis = getattr(ctx, "redis", None)
        if redis is None:
            return
        key = self._state_key(ctx.account_id, chat_id)
        try:
            if not active:
                await redis.delete(key)
                return
            ttl = max(30, int(max(pack.expires_at for pack in active) - time.time()) + 60)
            await redis.set(key, _packs_to_json(active), ex=ttl)
        except Exception as exc:
            if ctx.log:
                await ctx.log("warn", f"[lucky_redpack] 写入 Redis 红包状态失败，已保留本地状态：{type(exc).__name__}")

    async def _persist_pack(self, ctx: PluginContext, pack: LuckyRedpack) -> None:
        async with self._get_lock(pack.chat_id):
            with self._state_file_lock(ctx.account_id, pack.chat_id):
                packs = await self._load_active_packs(ctx, pack.chat_id)
                replaced = False
                next_packs: list[LuckyRedpack] = []
                for item in packs:
                    if item.pack_code == pack.pack_code:
                        next_packs.append(pack)
                        replaced = True
                    else:
                        next_packs.append(item)
                if not replaced and not pack.is_finished() and not pack.is_expired():
                    next_packs.append(pack)
                await self._save_active_packs(ctx, pack.chat_id, next_packs)

    def _find_pack_by_code(self, chat_id: int, pack_code: str) -> LuckyRedpack | None:
        target = str(pack_code or "").strip().casefold()
        if not target:
            return None
        for pack in self._active_packs(chat_id):
            if pack.pack_code.casefold() == target:
                return pack
        return None

    def _remove_pack(self, chat_id: int, pack: LuckyRedpack) -> None:
        packs = [item for item in self._packs.get(chat_id, []) if item is not pack]
        if packs:
            self._packs[chat_id] = packs
        else:
            self._packs.pop(chat_id, None)

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = str(cfg.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND
        self._default_amount = _clamp_int(cfg.get("default_amount"), DEFAULT_AMOUNT, 1, MAX_AMOUNT)
        self._default_count = _clamp_int(cfg.get("default_count"), DEFAULT_COUNT, 1, MAX_COUNT)
        self._min_share_amount = _clamp_int(cfg.get("min_share_amount"), DEFAULT_MIN_SHARE_AMOUNT, 1, MAX_AMOUNT)
        self._suffix_length = _clamp_int(cfg.get("suffix_length"), DEFAULT_SUFFIX_LENGTH, 1, 12)
        self._ttl_seconds = _clamp_int(cfg.get("ttl_seconds"), DEFAULT_TTL_SECONDS, 30, 86400)
        self._image_password_enabled = bool(cfg.get("image_password_enabled", DEFAULT_IMAGE_PASSWORD_ENABLED))
        self._delete_command_message = bool(cfg.get("delete_command_message", False))
        self._allow_owner_claim = bool(cfg.get("allow_owner_claim", DEFAULT_ALLOW_OWNER_CLAIM))
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            storage = "redis+file" if getattr(ctx, "redis", None) is not None else "file"
            await ctx.log("info", f"[lucky_redpack] v{PLUGIN_VERSION} 已启动，指令：{self._command}，状态存储：{storage}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._packs.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[lucky_redpack] 已停止")

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Event Bus 主入口，0.33+ 运行态优先从这里处理领取口令。"""
        if _payload_event_type(payload) != "message":
            return []
        text = _payload_message_text(payload)
        if not text or text.startswith((",", "/", "，")):
            return []
        chat_id = _payload_chat_id(payload)
        if not chat_id:
            return []
        sender_id = _payload_sender_id(payload)
        actions, pack_to_resend = await self._claim_password(
            ctx,
            text=text,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=_payload_sender_name(payload, sender_id),
            sender_is_bot=_payload_actor_is_bot(payload),
            claim_message_id=_payload_message_id(payload),
        )
        if pack_to_resend is not None:
            await self._resend_pack_message(ctx, pack_to_resend)
            await self._persist_pack(ctx, pack_to_resend)
        return actions or []

    async def _cmd_handler(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        chat_id = _chat_id_from_event(event)
        if not chat_id:
            return

        tokens = _split_args(args)
        action = tokens[0].casefold() if tokens else ""
        if action in {"help", "帮助"}:
            await self._reply(event, self._help_text())
            return
        if action in {"active", "状态"}:
            await self._reply(event, await self._active_text(ctx, chat_id))
            return
        if action in {"list", "列表"}:
            await self._reply(event, await self._active_text(ctx, chat_id))
            return
        if action in {"off", "关闭"}:
            pack_code = tokens[1] if len(tokens) >= 2 else ""
            if not pack_code:
                await self._reply(event, f"请指定红包代码。例：{current_command_prefix(fallback=',')}{self._command} off ABC123")
                return
            async with self._get_lock(chat_id):
                with self._state_file_lock(ctx.account_id, chat_id):
                    packs = await self._load_active_packs(ctx, chat_id)
                    pack = next((item for item in packs if item.pack_code.casefold() == pack_code.casefold()), None)
                    if not pack:
                        await self._reply(event, f"未找到进行中的红包：{pack_code}")
                        return
                    packs = [item for item in packs if item is not pack]
                    await self._save_active_packs(ctx, chat_id, packs)
            if pack.message_id:
                await self._delete_message(ctx, chat_id, pack.message_id)
            await self._reply(event, f"已关闭红包 {pack.pack_code}。")
            return
        if action in {"clear", "清空"}:
            async with self._get_lock(chat_id):
                with self._state_file_lock(ctx.account_id, chat_id):
                    existed = await self._load_active_packs(ctx, chat_id)
                    await self._save_active_packs(ctx, chat_id, [])
            for pack in existed:
                if pack.message_id:
                    await self._delete_message(ctx, chat_id, pack.message_id)
            await self._reply(event, f"已清空当前聊天的 {len(existed)} 个进行中红包。" if existed else "当前聊天没有进行中的红包。")
            return

        image_mode = self._image_password_enabled
        create_args = args
        if action in {"img", "image", "图片"}:
            image_mode = True
            create_args = tokens[1:]
        elif action in {"text", "文字"}:
            image_mode = False
            create_args = tokens[1:]

        keyword, amount, count, error = parse_create_args(create_args, self._default_amount, self._default_count)
        if error:
            await self._reply(event, f"{error}\n{self._usage_example()}")
            return

        amount = _clamp_int(amount, self._default_amount, 1, MAX_AMOUNT)
        count = _clamp_int(count, self._default_count, 1, MAX_COUNT)
        validation_error = self._validate_amount_count(amount, count)
        if validation_error:
            await self._reply(event, validation_error)
            return

        creator_id = _sender_id_from_event(event)
        now = time.time()
        async with self._get_lock(chat_id):
            with self._state_file_lock(ctx.account_id, chat_id):
                existing_packs = await self._load_active_packs(ctx, chat_id)
                pack_code = self._new_pack_code_for_existing({item.pack_code for item in existing_packs})
        pack = LuckyRedpack(
            pack_code=pack_code,
            chat_id=chat_id,
            creator_user_id=creator_id,
            base_keyword=keyword,
            total_amount=amount,
            total_count=count,
            min_share_amount=self._min_share_amount,
            suffix_length=self._suffix_length,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            image_mode=image_mode,
            remaining_amount=amount,
            remaining_count=count,
        )
        pack.current_suffix = self._new_suffix(pack)
        pack.used_passwords.add(_normalize_password(pack.current_password))

        try:
            sent = await self._send_pack_message(ctx, pack, reply_to=_message_id_from_event(event))
        except RuntimeError as exc:
            if ctx.log:
                await ctx.log("error", f"[lucky_redpack] 图片财富密码生成失败：{exc}")
            await self._reply(event, f"图片财富密码生成失败：{exc}")
            return
        pack.message_id = _message_id_from_event(sent) if sent is not None else _message_id_from_event(event)
        async with self._get_lock(chat_id):
            with self._state_file_lock(ctx.account_id, chat_id):
                packs = await self._load_active_packs(ctx, chat_id)
                if any(item.pack_code == pack.pack_code for item in packs):
                    pack.pack_code = self._new_pack_code_for_existing({item.pack_code for item in packs})
                packs.append(pack)
                await self._save_active_packs(ctx, chat_id, packs)
        self._track_task(asyncio.create_task(self._auto_expire(chat_id, ctx, pack.created_at)))
        if self._delete_command_message:
            await self._delete_event(ctx, event)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        if ctx.client is None:
            return
        text = _event_text(event)
        if not text or text.startswith((",", "/", "，")):
            return
        chat_id = _chat_id_from_event(event)
        if not chat_id:
            return
        sender = await self._sender(event)
        sender_id = int(getattr(sender, "id", 0) or _sender_id_from_event(event))
        display_name = public_entity_display_name(sender, fallback_id=sender_id, default="玩家")
        actions, pack_to_resend = await self._claim_password(
            ctx,
            text=text,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=display_name,
            sender_is_bot=bool(getattr(sender, "is_bot", False)),
            claim_message_id=_message_id_from_event(event),
        )
        await self._apply_legacy_actions(ctx, chat_id, actions)
        if pack_to_resend is not None:
            await self._resend_pack_message(ctx, pack_to_resend)
            await self._persist_pack(ctx, pack_to_resend)

    async def _claim_password(
        self,
        ctx: PluginContext,
        *,
        text: str,
        chat_id: int,
        sender_id: int,
        sender_name: str,
        sender_is_bot: bool,
        claim_message_id: int | None,
    ) -> tuple[list[dict[str, Any]], LuckyRedpack | None]:
        actions: list[dict[str, Any]] = []
        pack_to_resend: LuckyRedpack | None = None
        normalized_text = _normalize_password(text)
        async with self._get_lock(chat_id):
            with self._state_file_lock(ctx.account_id, chat_id):
                packs = await self._load_active_packs(ctx, chat_id)
                pack = next(
                    (item for item in reversed(packs) if normalized_text == _normalize_password(item.current_password)),
                    None,
                )
                if not pack or pack.is_finished():
                    if ctx.log:
                        if packs and any(normalized_text.startswith(_normalize_password(item.base_keyword)) for item in packs):
                            active_codes = ",".join(f"{item.pack_code}:{item.remaining_count}/{item.total_count}" for item in packs[-5:])
                            await ctx.log(
                                "info",
                                f"[lucky_redpack] 领取口令未命中：chat={chat_id} msg={claim_message_id} active={active_codes}",
                            )
                        elif not packs and _looks_like_password_attempt(text, self._suffix_length):
                            await ctx.log(
                                "warn",
                                f"[lucky_redpack] 领取口令未找到红包状态：chat={chat_id} msg={claim_message_id}",
                            )
                    return [], None
                if pack.is_expired():
                    packs = [item for item in packs if item.pack_code != pack.pack_code]
                    await self._save_active_packs(ctx, chat_id, packs)
                    if pack.message_id:
                        actions.append(_delete_action(pack.message_id, chat_id=chat_id))
                    actions.append(_send_action(render_settlement(pack, expired=True), send_via="userbot_reply"))
                    return actions, None
                if not sender_id:
                    if ctx.log:
                        await ctx.log("warn", f"[lucky_redpack] 领取口令命中但缺少发送者 ID：chat={chat_id} msg={claim_message_id}")
                    return [], None
                if sender_id == pack.creator_user_id and not self._allow_owner_claim:
                    return [], None
                if sender_is_bot:
                    return [], None
                if sender_id in pack.claimed_user_ids:
                    return [], None

                claim_amount = calculate_random_claim_amount(pack)
                if claim_amount <= 0:
                    return [], None
                pack.remaining_amount = max(0, pack.remaining_amount - claim_amount)
                pack.remaining_count = max(0, pack.remaining_count - 1)
                pack.claimed_user_ids.add(sender_id)
                pack.claims.append(
                    ClaimRecord(
                        user_id=sender_id,
                        display_name=sender_name or str(sender_id),
                        amount=claim_amount,
                        message_id=claim_message_id,
                    )
                )
                if claim_message_id:
                    actions.append(_send_action(f"+{claim_amount}", reply_to_message_id=claim_message_id, send_via="userbot_reply", parse_mode=None))
                if pack.is_finished():
                    packs = [item for item in packs if item.pack_code != pack.pack_code]
                    await self._save_active_packs(ctx, chat_id, packs)
                    if pack.message_id:
                        actions.append(_delete_action(pack.message_id, chat_id=chat_id))
                    actions.append(_send_action(render_settlement(pack), send_via="userbot_reply"))
                else:
                    pack.current_suffix = self._new_suffix(pack)
                    pack.used_passwords.add(_normalize_password(pack.current_password))
                    await self._save_active_packs(ctx, chat_id, packs)
                    pack_to_resend = pack

        if actions and ctx.log:
            await ctx.log(
                "info",
                f"[lucky_redpack] 领取成功：chat={chat_id} msg={claim_message_id} user={sender_id}",
            )
        return actions, pack_to_resend

    async def _apply_legacy_actions(self, ctx: PluginContext, chat_id: int, actions: list[dict[str, Any]]) -> None:
        if ctx.client is None:
            return
        for action in actions:
            action_type = action.get("type")
            if action_type == "send_message":
                kwargs: dict[str, Any] = {}
                if action.get("reply_to_message_id"):
                    kwargs["reply_to"] = action["reply_to_message_id"]
                if action.get("parse_mode"):
                    kwargs["parse_mode"] = action["parse_mode"]
                await ctx.client.send_message(chat_id, str(action.get("text") or ""), **kwargs)
            elif action_type == "delete_message" and action.get("message_id"):
                await self._delete_message(ctx, chat_id, int(action["message_id"]))

    def _validate_amount_count(self, amount: int, count: int) -> str | None:
        if amount < count * self._min_share_amount:
            return f"总额太小：{count} 个红包至少需要 {count * self._min_share_amount}。"
        return None

    def _help_text(self) -> str:
        return (
            f"🧧 拼手气口令红包 v{PLUGIN_VERSION}\n"
            f"{self._usage_example()}\n"
            f"{current_command_prefix(fallback=',')}{self._command} img 发财 88888 10 发送图片财富密码红包\n"
            f"{current_command_prefix(fallback=',')}{self._command} text 发财 88888 10 发送文字财富密码红包\n"
            f"{current_command_prefix(fallback=',')}{self._command} list 查看当前红包列表\n"
            f"{current_command_prefix(fallback=',')}{self._command} off ABC123 关闭指定红包\n"
            f"{current_command_prefix(fallback=',')}{self._command} clear 清空当前红包"
        )

    def _usage_example(self) -> str:
        return f"用法：{current_command_prefix(fallback=',')}{self._command} 发财 88888 10"

    async def _active_text(self, ctx: PluginContext, chat_id: int) -> str:
        packs = await self._load_active_packs(ctx, chat_id)
        if not packs:
            return "当前聊天没有进行中的红包。"
        lines = ["🧧 当前聊天红包列表"]
        for index, pack in enumerate(reversed(packs), start=1):
            mode = "图片" if pack.image_mode else "文字"
            claimed = pack.total_count - pack.remaining_count
            lines.append(
                f"{index}. {pack.pack_code}｜{mode}｜剩余 {pack.remaining_count}/{pack.total_count}｜已领 {claimed}｜总额 {pack.total_amount}"
            )
        lines.append(f"关闭红包：{current_command_prefix(fallback=',')}{self._command} off <红包代码>")
        return "\n".join(lines)

    async def _reply(self, event: Any, text: str, **kwargs: Any) -> Any:
        reply = getattr(event, "reply", None)
        if callable(reply):
            return await reply(text, **kwargs)
        respond = getattr(event, "respond", None)
        if callable(respond):
            return await respond(text, **kwargs)
        return None

    async def _sender(self, event: Any) -> Any:
        getter = getattr(event, "get_sender", None)
        if callable(getter):
            sender = await getter()
            if sender is not None:
                return sender
        return getattr(event, "sender", None) or getattr(getattr(event, "message", None), "sender", None)

    async def _resend_pack_message(self, ctx: PluginContext, pack: LuckyRedpack) -> None:
        if ctx.client is None:
            return
        if pack.message_id is not None:
            await self._delete_message(ctx, pack.chat_id, pack.message_id)
        try:
            sent = await self._send_pack_message(ctx, pack)
            pack.message_id = _message_id_from_event(sent) or pack.message_id
        except Exception as exc:
            if ctx.log:
                await ctx.log("warn", f"[lucky_redpack] 红包消息重发失败：{type(exc).__name__}: {exc}")

    async def _send_pack_message(self, ctx: PluginContext, pack: LuckyRedpack, *, reply_to: int | None = None) -> Any:
        if ctx.client is None:
            return None
        caption = render_redpack_message(pack)
        if not pack.image_mode:
            return await ctx.client.send_message(pack.chat_id, caption, parse_mode="html", reply_to=reply_to)

        image_path = build_password_image(pack.current_password)
        if image_path is None:
            raise RuntimeError(get_image_error())
        try:
            send_file = getattr(ctx.client, "send_file", None)
            if callable(send_file):
                return await send_file(pack.chat_id, str(image_path), caption=caption, parse_mode="html", reply_to=reply_to, force_document=False)
            send_photo = getattr(ctx.client, "send_photo", None)
            if callable(send_photo):
                return await send_photo(pack.chat_id, str(image_path), caption=caption, parse_mode="html", reply_to=reply_to)
            raise RuntimeError("当前客户端没有 send_file/send_photo 能力")
        finally:
            try:
                image_path.unlink(missing_ok=True)
                image_path.parent.rmdir()
            except Exception:
                pass

    async def _delete_event(self, ctx: PluginContext, event: Any) -> None:
        delete = getattr(event, "delete", None) or getattr(getattr(event, "message", None), "delete", None)
        if callable(delete):
            try:
                await delete()
                return
            except Exception as exc:
                if ctx.log:
                    await ctx.log("warn", f"[lucky_redpack] event.delete 失败：{type(exc).__name__}: {exc}")

        chat_id = _chat_id_from_event(event)
        message_id = _message_id_from_event(event)
        if not chat_id or not message_id or ctx.client is None:
            return

        delete_messages = getattr(ctx.client, "delete_messages", None)
        if callable(delete_messages):
            try:
                await _maybe_await(delete_messages(chat_id, [int(message_id)]))
            except Exception:
                pass

    async def _delete_message(self, ctx: PluginContext, chat_id: int, message_id: int) -> None:
        if ctx.client is None:
            return
        delete_messages = getattr(ctx.client, "delete_messages", None)
        if not callable(delete_messages):
            return
        try:
            await _maybe_await(delete_messages(chat_id, [int(message_id)]))
        except Exception as exc:
            if ctx.log:
                await ctx.log("warn", f"[lucky_redpack] 删除旧红包消息失败：{type(exc).__name__}: {exc}")

    async def _auto_expire(self, chat_id: int, ctx: PluginContext, created_at: float) -> None:
        await asyncio.sleep(self._ttl_seconds)
        async with self._get_lock(chat_id):
            with self._state_file_lock(ctx.account_id, chat_id):
                packs = await self._load_active_packs(ctx, chat_id)
                pack = next((item for item in packs if item.created_at == created_at), None)
                if not pack or pack.created_at != created_at or pack.is_finished():
                    return
                packs = [item for item in packs if item.pack_code != pack.pack_code]
                await self._save_active_packs(ctx, chat_id, packs)
                settlement = render_settlement(pack, expired=True)
        if ctx.client is not None:
            await ctx.client.send_message(chat_id, settlement, reply_to=pack.message_id)


PLUGIN_CLASS = LuckyRedpackPlugin

__all__ = [
    "ClaimRecord",
    "LuckyRedpack",
    "LuckyRedpackPlugin",
    "PLUGIN_CLASS",
    "calculate_random_claim_amount",
    "parse_create_args",
    "render_claim_details",
    "render_redpack_message",
    "render_settlement",
]
