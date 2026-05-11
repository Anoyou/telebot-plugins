"""
GI2 图片生成与改图插件

支持：
- `,gi2 <提示词>` 直接生图
- 回复照片 / 图片文档 / 静态贴纸后 `,gi2 <提示词>` 改图
- 回复插件自己发出的结果图后，再次 `,gi2 <提示词>` 原地编辑结果图
"""

import asyncio
import base64
import contextlib
import json
import mimetypes
import struct
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from pyrogram.types import InputMediaPhoto

from pagermaid.enums import Client, Message
from pagermaid.listener import listener
from pagermaid.utils import logs

__version__ = "1.0"

PLUGIN_NAME = "GI2"
GI2_MARKER = "🧩 GI2 |"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_API_BASE = "https://api.openai.com/v1"
CONFIG_FILE = Path(__file__).parent / "gi2_config.json"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=300)
RESULT_IMAGE_NAME = "gi2_result.png"
SUPPORTED_OUTPUT_FORMAT = "png"
GI2_RUNTIME_VERSION = "1.0.3-probe-input"

DEFAULT_CONFIG = {
    "api_key": "",
    "api_base": DEFAULT_API_BASE,
    "model": DEFAULT_MODEL,
}


def normalize_api_base(api_base: str) -> str:
    """把用户传入的根地址/完整端点收敛到统一 API 根路径。"""
    raw_base = str(api_base or "").strip()
    if not raw_base:
        return DEFAULT_API_BASE

    normalized = raw_base.rstrip("/")
    lowered = normalized.lower()
    for suffix in (
        "/images/generations",
        "/images/edits",
        "/chat/completions",
        "/responses",
    ):
        if lowered.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    parsed = urlparse(normalized)
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return f"{parsed.scheme}://{parsed.netloc}/v1"
    return normalized.rstrip("/")


def normalize_config(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    """补齐并清洗配置。"""
    result = DEFAULT_CONFIG.copy()
    if isinstance(config, dict):
        result.update(config)

    result["api_key"] = str(result.get("api_key") or "").strip()
    result["model"] = str(result.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    result["api_base"] = normalize_api_base(str(result.get("api_base") or DEFAULT_API_BASE))
    return result


def load_config() -> dict[str, Any]:
    """加载插件配置。"""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logs.error("[GI2] 配置读取失败：%s", exc)
        return DEFAULT_CONFIG.copy()
    return normalize_config(data)


def save_config(config: dict[str, Any]) -> bool:
    """保存插件配置。"""
    try:
        CONFIG_FILE.write_text(
            json.dumps(normalize_config(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logs.error("[GI2] 配置保存失败：%s", exc)
        return False


def mask_secret(secret: str) -> str:
    """隐藏敏感信息。"""
    if not secret:
        return "未设置"
    if len(secret) <= 8:
        return f"{secret[:4]}..."
    return f"{secret[:8]}...{secret[-4:]}"


def build_api_url(api_base: str, mode: str) -> str:
    """根据模式拼接图片接口地址。"""
    base = normalize_api_base(api_base)
    if mode == "edit":
        return f"{base}/images/edits"
    return f"{base}/images/generations"


def collapse_text(text: str) -> str:
    """压缩多余空白，避免 caption 过长或过乱。"""
    return " ".join(str(text or "").split())


def build_result_caption(prompt: str, model: str, elapsed_seconds: float) -> str:
    """生成结果图 caption。"""
    footer = f"{GI2_MARKER} 🤖 Model: `{model}` | ⏱️ `{elapsed_seconds:.1f}s`"
    compact_prompt = collapse_text(prompt)
    prefix = "🎨 Prompt: "
    max_prompt_length = max(20, 1024 - len(footer) - len(prefix) - 2)
    if len(compact_prompt) > max_prompt_length:
        compact_prompt = compact_prompt[: max_prompt_length - 1].rstrip() + "…"
    return f"{prefix}{compact_prompt}\n{footer}"


def help_text() -> str:
    """插件帮助。"""
    return (
        "🖼️ **GI2 图片插件**\n\n"
        "`,gi2 <提示词>` - 直接生图\n"
        "回复照片 / 图片文档 / 静态贴纸后使用 `,gi2 <提示词>` - 改图\n"
        "回复 GI2 自己发出的结果图后使用 `,gi2 <提示词>` - 原地继续改图\n\n"
        "管理命令：\n"
        "`,gi2 setkey <API Key>`\n"
        "`,gi2 setmodel <模型名>`\n"
        "`,gi2 setbase <API 根地址>`\n"
        "`,gi2 status`\n"
        "`,gi2 help`\n\n"
        f"默认模型：`{DEFAULT_MODEL}`"
    )


def get_reply_media_kind(reply_message: Any) -> Optional[str]:
    """识别被回复消息的媒体类型。"""
    if reply_message is None:
        return None
    if getattr(reply_message, "photo", None):
        return "photo"

    document = getattr(reply_message, "document", None)
    mime_type = str(getattr(document, "mime_type", "") or "").lower()
    if document and mime_type.startswith("image/"):
        return "image_document"

    sticker = getattr(reply_message, "sticker", None)
    if sticker:
        if getattr(sticker, "is_animated", False):
            return "animated_sticker"
        if getattr(sticker, "is_video", False):
            return "video_sticker"
        return "sticker"
    return None


def is_plugin_result_message(message: Any) -> bool:
    """判断消息是否是 GI2 发出的可原地改图结果。"""
    if message is None:
        return False
    from_user = getattr(message, "from_user", None)
    is_self = bool(from_user and getattr(from_user, "is_self", False))
    caption = str(getattr(message, "caption", "") or "")
    has_editable_media = bool(
        getattr(message, "photo", None) or get_reply_media_kind(message) == "image_document"
    )
    return is_self and has_editable_media and GI2_MARKER in caption


def resolve_request_context(message: Any) -> dict[str, Any]:
    """根据当前命令和回复目标，决定走生图、改图还是报错。"""
    reply_message = getattr(message, "reply_to_message", None)
    if reply_message is None:
        return {
            "mode": "generate",
            "reply_media_kind": None,
            "should_edit_result_message": False,
            "target_message": None,
        }

    reply_media_kind = get_reply_media_kind(reply_message)
    if reply_media_kind in {"photo", "image_document", "sticker"}:
        return {
            "mode": "edit",
            "reply_media_kind": reply_media_kind,
            "should_edit_result_message": is_plugin_result_message(reply_message),
            "target_message": reply_message,
        }

    if reply_media_kind in {"animated_sticker", "video_sticker"}:
        return {
            "mode": "unsupported",
            "reply_media_kind": reply_media_kind,
            "should_edit_result_message": False,
            "target_message": reply_message,
            "error": "目前只支持回复照片、图片文档或静态贴纸，暂不支持动画贴纸和视频贴纸。",
        }

    return {
        "mode": "unsupported",
        "reply_media_kind": reply_media_kind,
        "should_edit_result_message": False,
        "target_message": reply_message,
        "error": "回复的消息里没有可编辑图片。请回复照片、图片文档或静态贴纸后再使用 `,gi2 <提示词>`。",
    }


def extract_error_message(data: Any) -> str:
    """从接口响应中尽量提取可读错误。"""
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return ""

    error = data.get("error")
    if isinstance(error, dict):
        for key in ("message", "detail", "code", "type"):
            value = str(error.get(key) or "").strip()
            if value:
                return value
    elif error:
        return str(error).strip()

    for key in ("message", "detail", "msg"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def extract_image_result(data: Any) -> dict[str, str]:
    """兼容多种 OpenAI/兼容接口返回格式。"""
    if not isinstance(data, dict):
        return {}

    output_items = data.get("output")
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict):
                continue
            for key in ("result", "b64_json", "base64", "image_base64"):
                value = str(item.get(key) or "").strip()
                if value:
                    return {"kind": "base64", "value": value}
            for key in ("url", "image_url"):
                value = str(item.get(key) or "").strip()
                if value:
                    return {"kind": "url", "value": value}

    image_items = data.get("data")
    if isinstance(image_items, list):
        for item in image_items:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    return {"kind": "base64", "value": value}
            if not isinstance(item, dict):
                continue
            for key in ("b64_json", "base64", "image_base64"):
                value = str(item.get(key) or "").strip()
                if value:
                    return {"kind": "base64", "value": value}
            for key in ("url", "image_url"):
                value = str(item.get(key) or "").strip()
                if value:
                    return {"kind": "url", "value": value}

    for key in ("b64_json", "base64", "image_base64"):
        value = str(data.get(key) or "").strip()
        if value:
            return {"kind": "base64", "value": value}
    for key in ("url", "image_url", "result"):
        value = str(data.get(key) or "").strip()
        if value:
            kind = "url" if "url" in key else "base64"
            return {"kind": kind, "value": value}
    return {}


def extract_no_image_reason(data: Any) -> str:
    """当接口没返回图片时，尽量给出更接近真实原因的文案。"""
    if not isinstance(data, dict):
        return ""

    error_message = extract_error_message(data)
    if error_message:
        return error_message

    if data.get("success") is False:
        return "接口返回 success=false，但没有提供更多错误信息"

    status_value = str(data.get("status") or "").strip().lower()
    if status_value in {"failed", "error"}:
        return f"接口返回状态：{status_value}"

    base_response = data.get("base_resp")
    if isinstance(base_response, dict):
        status_code = str(base_response.get("status_code") or "").strip()
        status_msg = str(base_response.get("status_msg") or "").strip()
        if status_code and status_code != "0":
            return status_msg or f"接口返回状态码：{status_code}"

    return ""


def detect_image_file_info(image_path: Path) -> dict[str, Any]:
    """读取图片头信息，辅助定位上游按图片特征断流的问题。"""
    data = image_path.read_bytes()[:64]
    info: dict[str, Any] = {
        "size_bytes": image_path.stat().st_size,
        "format": "unknown",
        "width": None,
        "height": None,
    }
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        info["format"] = "png"
        info["width"], info["height"] = struct.unpack(">II", data[16:24])
    elif data.startswith(b"\xff\xd8"):
        info["format"] = "jpeg"
        full_data = image_path.read_bytes()
        offset = 2
        while offset + 9 < len(full_data):
            if full_data[offset] != 0xFF:
                break
            marker = full_data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(full_data):
                break
            segment_length = struct.unpack(">H", full_data[offset : offset + 2])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if offset + 7 < len(full_data):
                    info["height"] = struct.unpack(">H", full_data[offset + 3 : offset + 5])[0]
                    info["width"] = struct.unpack(">H", full_data[offset + 5 : offset + 7])[0]
                break
            offset += segment_length
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        info["format"] = "webp"
    return info


def should_reject_large_source_image(image_info: dict[str, Any]) -> Optional[str]:
    """拦截高风险原图，避免上游直接断流导致无效等待。"""
    width = image_info.get("width") or 0
    height = image_info.get("height") or 0
    size_bytes = int(image_info.get("size_bytes") or 0)
    if width and height and width * height > 4_000_000:
        return f"原图分辨率过大（{width}x{height}），容易触发上游断流；请先压缩/裁剪到 2000x2000 以内再试。"
    if size_bytes > 8 * 1024 * 1024:
        return f"原图文件过大（{size_bytes / 1024 / 1024:.1f} MB），容易触发上游断流；请先压缩到 8 MB 内再试。"
    return None


def guess_download_mime_type(reply_message: Message, downloaded_path: Path) -> str:
    """尽量推断下载文件的 MIME，便于转 data URL。"""
    if getattr(reply_message, "photo", None):
        return "image/jpeg"

    document = getattr(reply_message, "document", None)
    document_mime = str(getattr(document, "mime_type", "") or "").strip()
    if document_mime:
        return document_mime

    if getattr(reply_message, "sticker", None):
        return "image/webp"

    guessed, _ = mimetypes.guess_type(str(downloaded_path))
    return guessed or "application/octet-stream"


def encode_image_to_data_url(image_path: Path, mime_type: str) -> str:
    """把本地图片编码为 data URL，用于 JSON 编辑请求。"""
    image_base64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{image_base64}"


def build_edit_form_spec(
    model: str,
    prompt: str,
    image_path: Path,
    mime_type: str,
) -> dict[str, Any]:
    """构造图片编辑请求的 multipart 规格。"""
    safe_mime_type = str(mime_type or "application/octet-stream").strip() or "application/octet-stream"
    return {
        "fields": {
            "model": model,
            "prompt": prompt,
            "output_format": SUPPORTED_OUTPUT_FORMAT,
        },
        "files": [
            {
                "field_name": "image[]",
                "filename": image_path.name or "input-image",
                "mime_type": safe_mime_type,
                "path": image_path,
            }
        ],
    }


def build_responses_edit_payload(
    model: str,
    prompt: str,
    image_path: Path,
    mime_type: str,
) -> dict[str, Any]:
    """构造 Responses API 的图片编辑请求。"""
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                    {
                        "type": "input_image",
                        "image_url": encode_image_to_data_url(image_path, mime_type),
                        "detail": "auto",
                    },
                ],
            }
        ],
        "tools": [
            {
                "type": "image_generation",
            }
        ],
        "tool_choice": "required",
    }


async def parse_response_payload(response: Any) -> Any:
    """兼容 JSON / 文本报错响应。"""
    try:
        return await response.json(content_type=None)
    except Exception:
        text = ""
        with contextlib.suppress(Exception):
            text = await response.text()
        return {"message": text.strip() or f"HTTP {getattr(response, 'status', 'unknown')}"}


def normalize_runtime_error(exc: Exception) -> str:
    """把底层运行时异常转换成更可读的提示。"""
    raw_message = str(exc or "").strip()
    lowered = raw_message.lower()
    if "stream disconnected before completion" in lowered:
        return "上游接口在返回完成前主动断开了连接"
    return raw_message or exc.__class__.__name__


def is_raw_disconnect_error(exc: Exception) -> bool:
    """识别尚未转换过的底层断流错误。"""
    return "stream disconnected before completion" in str(exc or "").lower()


def should_try_responses_edit_fallback(exc: Exception) -> bool:
    """判断是否应从 images/edits 回退到 responses。"""
    lowered = str(exc or "").strip().lower()
    return any(
        token in lowered
        for token in (
            "stream disconnected before completion",
            "server disconnected",
            "connection reset",
            "broken pipe",
            "connection closed",
        )
    )


def supports_responses_image_edit_fallback(model: str) -> bool:
    """判断当前模型是否可走 Responses 图片工具回退。"""
    normalized_model = str(model or "").strip().lower()
    return normalized_model != "gpt-image-2"


async def download_image_from_url(session: aiohttp.ClientSession, image_url: str) -> bytes:
    """下载兼容接口返回的外链图片。"""
    async with session.get(image_url) as response:
        if response.status >= 400:
            payload = await parse_response_payload(response)
            raise RuntimeError(extract_error_message(payload) or f"下载图片失败：HTTP {response.status}")
        image_bytes = await response.read()
        if not image_bytes:
            raise RuntimeError("接口返回了图片 URL，但下载内容为空")
        return image_bytes


def prepare_image_result(payload: Any) -> dict[str, str]:
    """从接口响应中提取图片结果描述。"""
    image_result = extract_image_result(payload)
    if not image_result:
        detail = extract_no_image_reason(payload)
        if detail:
            raise RuntimeError(detail)
        if isinstance(payload, dict):
            logs.warning("[GI2] 接口成功响应但未识别到图片字段，keys=%s", sorted(payload.keys()))
        raise RuntimeError("接口没有返回图片数据")
    return image_result


def decode_image_result_sync(payload: Any) -> bytes:
    """同步解码接口返回的 base64 图片。"""
    image_result = prepare_image_result(payload)
    kind = image_result.get("kind")
    value = image_result.get("value", "")
    if kind == "base64":
        try:
            return base64.b64decode(value)
        except Exception as exc:
            raise RuntimeError("返回图片 base64 解码失败") from exc
    if kind == "url":
        raise RuntimeError("接口返回了图片 URL，但当前请求路径不支持同步下载")
    raise RuntimeError("接口返回了未知图片格式")


async def decode_image_result(session: aiohttp.ClientSession, payload: Any) -> bytes:
    """从接口响应中提取最终图片字节。"""
    image_result = prepare_image_result(payload)
    kind = image_result.get("kind")
    value = image_result.get("value", "")
    if kind == "base64":
        try:
            return base64.b64decode(value)
        except Exception as exc:
            raise RuntimeError("返回图片 base64 解码失败") from exc
    if kind == "url":
        return await download_image_from_url(session, value)
    raise RuntimeError("接口返回了未知图片格式")


async def request_generation(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    prompt: str,
) -> bytes:
    """文生图请求。"""
    url = build_api_url(config["api_base"], "generate")
    payload = {
        "model": config["model"],
        "prompt": prompt,
        "output_format": SUPPORTED_OUTPUT_FORMAT,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    async with session.post(url, headers=headers, json=payload) as response:
        result = await parse_response_payload(response)
        if response.status >= 400:
            raise RuntimeError(extract_error_message(result) or f"生图失败：HTTP {response.status}")
        return await decode_image_result(session, result)


async def request_edit_with_aiohttp(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    prompt: str,
    image_path: Path,
    mime_type: str,
) -> bytes:
    """使用 aiohttp 调用改图接口。"""
    url = build_api_url(config["api_base"], "edit")
    spec = build_edit_form_spec(config["model"], prompt, image_path, mime_type)
    form = aiohttp.FormData()
    for field_name, field_value in spec["fields"].items():
        form.add_field(field_name, str(field_value))
    for file_item in spec["files"]:
        form.add_field(
            file_item["field_name"],
            Path(file_item["path"]).read_bytes(),
            filename=file_item["filename"],
            content_type=file_item["mime_type"],
        )

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
    }

    async with session.post(url, headers=headers, data=form) as response:
        result = await parse_response_payload(response)
        if response.status >= 400:
            raise RuntimeError(extract_error_message(result) or f"改图失败：HTTP {response.status}")
        return await decode_image_result(session, result)


def request_edit_with_curl_sync(
    config: dict[str, Any],
    prompt: str,
    image_path: Path,
    mime_type: str,
) -> bytes:
    """使用 curl 调用改图接口，绕过部分 aiohttp+代理的 chunked 断流问题。"""
    import subprocess

    url = build_api_url(config["api_base"], "edit")
    spec = build_edit_form_spec(config["model"], prompt, image_path, mime_type)
    with tempfile.TemporaryDirectory(prefix="gi2_curl_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        body_path = tmp_path / "response.json"
        header_path = tmp_path / "headers.txt"
        command = [
            "curl",
            "--http1.1",
            "-sS",
            "-L",
            "--max-time",
            str(int(REQUEST_TIMEOUT.total or 300)),
            "-D",
            str(header_path),
            "-o",
            str(body_path),
            "-H",
            f"Authorization: Bearer {config['api_key']}",
        ]
        for field_name, field_value in spec["fields"].items():
            command.extend(["-F", f"{field_name}={field_value}"])
        for file_item in spec["files"]:
            command.extend(
                [
                    "-F",
                    (
                        f"{file_item['field_name']}=@{file_item['path']};"
                        f"filename={file_item['filename']};type={file_item['mime_type']}"
                    ),
                ]
            )
        command.append(url)

        completed = subprocess.run(command, capture_output=True, text=True, timeout=int(REQUEST_TIMEOUT.total or 300) + 10)
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or f"curl 退出码 {completed.returncode}").strip()
            if "stream disconnected before completion" in stderr.lower():
                raise RuntimeError("上游接口在处理这张原图时主动断开，通常是图片内容/尺寸触发了上游风控或处理失败；请换图、裁剪/压缩后再试。")
            raise RuntimeError(stderr)

        raw_body = body_path.read_bytes() if body_path.exists() else b""
        header_text = header_path.read_text(encoding="utf-8", errors="replace") if header_path.exists() else ""
        status_codes = []
        for line in header_text.splitlines():
            if line.startswith("HTTP/"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    status_codes.append(int(parts[1]))
        status_code = status_codes[-1] if status_codes else 0
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            payload = {"message": raw_body.decode("utf-8", errors="replace").strip() or f"HTTP {status_code or 'unknown'}"}
        if status_code >= 400:
            raise RuntimeError(extract_error_message(payload) or f"改图失败：HTTP {status_code}")
        return decode_image_result_sync(payload)


async def request_edit(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    prompt: str,
    image_path: Path,
    mime_type: str,
) -> bytes:
    """改图请求。优先使用 curl，避免 aiohttp 在部分代理上接收大响应时断流。"""
    logs.warning("[GI2] 使用 curl 改图路径：version=%s", GI2_RUNTIME_VERSION)
    return await asyncio.to_thread(request_edit_with_curl_sync, config, prompt, image_path, mime_type)


async def request_edit_via_responses(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
    prompt: str,
    image_path: Path,
    mime_type: str,
) -> bytes:
    """使用 Responses API 作为图片编辑回退路径。"""
    url = f"{normalize_api_base(config['api_base'])}/responses"
    payload = build_responses_edit_payload(config["model"], prompt, image_path, mime_type)
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    async with session.post(url, headers=headers, json=payload) as response:
        result = await parse_response_payload(response)
        if response.status >= 400:
            raise RuntimeError(extract_error_message(result) or f"Responses 改图失败：HTTP {response.status}")
        return await decode_image_result(session, result)


async def generate_or_edit_image(
    config: dict[str, Any],
    prompt: str,
    source_image_path: Optional[Path] = None,
    source_mime_type: str = "",
) -> bytes:
    """按上下文自动走文生图或改图。"""
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        if source_image_path is None:
            return await request_generation(session, config, prompt)
        try:
            return await request_edit(session, config, prompt, source_image_path, source_mime_type)
        except Exception as exc:
            if not should_try_responses_edit_fallback(exc):
                raise
            if not is_raw_disconnect_error(exc):
                raise
            if not supports_responses_image_edit_fallback(config.get("model", "")):
                logs.warning(
                    "[GI2] images/edits 断流，但模型 %s 不支持 responses 图片回退",
                    config.get("model", ""),
                )
                raise RuntimeError(normalize_runtime_error(exc)) from exc
            logs.warning("[GI2] images/edits 断流，尝试回退到 responses：%s", exc)
            try:
                return await request_edit_via_responses(
                    session,
                    config,
                    prompt,
                    source_image_path,
                    source_mime_type,
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"{normalize_runtime_error(exc)}；Responses 回退也失败：{normalize_runtime_error(fallback_exc)}"
                ) from fallback_exc


async def download_reply_image(
    bot: Client,
    reply_message: Message,
    download_dir: Path,
) -> tuple[Path, str]:
    """下载被回复的图片，供改图接口使用。"""
    downloaded = await bot.download_media(reply_message, file_name=str(download_dir / "gi2_source"))
    if not downloaded:
        raise RuntimeError("下载原图失败")
    image_path = Path(downloaded)
    mime_type = guess_download_mime_type(reply_message, image_path)
    image_info = detect_image_file_info(image_path)
    logs.warning(
        "[GI2] 原图信息：kind=%s mime=%s format=%s size=%s width=%s height=%s path_suffix=%s",
        get_reply_media_kind(reply_message),
        mime_type,
        image_info.get("format"),
        image_info.get("size_bytes"),
        image_info.get("width"),
        image_info.get("height"),
        image_path.suffix,
    )
    reject_reason = should_reject_large_source_image(image_info)
    if reject_reason:
        raise RuntimeError(reject_reason)
    return image_path, mime_type


async def write_result_image(image_bytes: bytes, target_dir: Path) -> Path:
    """把生成结果写入本地临时文件。"""
    result_path = target_dir / RESULT_IMAGE_NAME
    result_path.write_bytes(image_bytes)
    return result_path


async def send_new_result(
    bot: Client,
    command_message: Message,
    target_reply_message: Optional[Message],
    result_path: Path,
    caption: str,
) -> None:
    """发送新图片结果。"""
    reply_to_message_id = getattr(target_reply_message, "id", None)
    await bot.send_photo(
        chat_id=command_message.chat.id,
        photo=str(result_path),
        caption=caption,
        reply_to_message_id=reply_to_message_id,
    )


async def edit_existing_result(
    bot: Client,
    target_message: Message,
    result_path: Path,
    caption: str,
) -> None:
    """原地替换之前的 GI2 结果图。"""
    media = InputMediaPhoto(media=str(result_path), caption=caption)
    await bot.edit_message_media(
        chat_id=target_message.chat.id,
        message_id=target_message.id,
        media=media,
    )


def status_text(config: dict[str, Any]) -> str:
    """插件当前配置。"""
    api_base_status = "已设置" if str(config.get("api_base") or "").strip() else "未设置"
    return (
        "🖼️ **GI2 当前配置**\n\n"
        f"🔑 API Key: `{mask_secret(config['api_key'])}`\n"
        f"🤖 Model: `{config['model']}`\n"
        f"🔗 API 地址: `{api_base_status}`"
    )


def build_processing_text(mode: str, model: str) -> str:
    """生成处理中提示，不回显敏感接口地址。"""
    mode_label = "改图" if mode == "edit" else "生图"
    return f"🖼️ GI2 正在{mode_label}...\n🤖 Model: `{model}`"


@listener(
    command="gi2",
    description="GI2 图片生成与改图",
    parameters="<提示词>",
)
async def gi2_command(message: Message, bot: Client):
    """GI2 主入口。"""
    raw_args = (message.arguments or "").strip()
    config = load_config()

    if not raw_args:
        return await message.edit(help_text())

    first, _, rest = raw_args.partition(" ")
    action = first.lower()

    if action in {"help", "h", "帮助"} and not rest.strip():
        return await message.edit(help_text())

    if action in {"status", "配置", "config"} and not rest.strip():
        return await message.edit(status_text(config))

    if action in {"setkey", "key", "set"}:
        api_key = rest.strip()
        if not api_key:
            return await message.edit("请使用：`,gi2 setkey <API Key>`")
        config["api_key"] = api_key
        save_config(config)
        return await message.edit(f"✅ API Key 已保存：`{mask_secret(api_key)}`")

    if action in {"setmodel", "model"}:
        model = rest.strip()
        if not model:
            return await message.edit("请使用：`,gi2 setmodel <模型名>`")
        config["model"] = model
        save_config(config)
        return await message.edit(f"✅ 当前图片模型已切换为：`{model}`")

    if action in {"setbase", "base", "url"}:
        api_base = rest.strip()
        if not api_base:
            return await message.edit("请使用：`,gi2 setbase <API 根地址>`")
        normalized_base = normalize_api_base(api_base)
        config["api_base"] = normalized_base
        save_config(config)
        return await message.edit("✅ 图片接口根地址已保存。")

    prompt = raw_args
    if not config.get("api_key"):
        return await message.edit("⚠️ 请先配置 API Key：`,gi2 setkey <API Key>`")

    context = resolve_request_context(message)
    if context["mode"] == "unsupported":
        return await message.edit(f"❌ {context['error']}")

    target_message = context.get("target_message")
    await message.edit(build_processing_text(context["mode"], config["model"]))

    started_at = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="gi2_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_image_path: Optional[Path] = None
            source_mime_type = ""
            if context["mode"] == "edit" and target_message is not None:
                source_image_path, source_mime_type = await download_reply_image(
                    bot,
                    target_message,
                    tmp_path,
                )

            image_bytes = await generate_or_edit_image(
                config,
                prompt,
                source_image_path=source_image_path,
                source_mime_type=source_mime_type,
            )
            elapsed = time.perf_counter() - started_at
            caption = build_result_caption(prompt, config["model"], elapsed)
            result_path = await write_result_image(image_bytes, tmp_path)

            if context["should_edit_result_message"] and target_message is not None:
                try:
                    await edit_existing_result(bot, target_message, result_path, caption)
                except Exception as exc:
                    logs.warning("[GI2] 原地编辑结果图失败，回退为发送新图：%s", exc)
                    await send_new_result(bot, message, target_message, result_path, caption)
            else:
                reply_target = target_message if context["mode"] == "edit" else None
                await send_new_result(bot, message, reply_target, result_path, caption)
    except Exception as exc:
        logs.error("[GI2] 处理失败：%s", exc)
        return await message.edit(f"❌ GI2 处理失败：{normalize_runtime_error(exc)}")

    with contextlib.suppress(Exception):
        await message.delete()
