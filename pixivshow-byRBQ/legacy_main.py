"""
PixivShow 插件

功能：
- 推送 Pixiv 普通美少女动漫图
- 推送 Pixiv R18 美少女动漫图，并自动加 Telegram 遮罩
- 支持按命令指定单次推送数量
"""

import asyncio
import contextlib
import random
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import aiohttp
from PIL import Image, ImageOps
from pyrogram.types import InputMediaPhoto

from pagermaid.enums import Client, Message
from pagermaid.listener import listener
from pagermaid.utils import logs

__version__ = "1.0.4"

LOLICON_API_URL = "https://api.lolicon.app/setu/v2"
DEFAULT_PUSH_COUNT = 1
MAX_PUSH_COUNT = 20
ALBUM_BATCH_SIZE = 10
MAX_FETCH_ATTEMPTS = 6
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
DOWNLOAD_CONCURRENCY = 4
MAX_IMAGE_SIDE = 2560

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    )
}

SAFE_TAGS = ("女の子", "少女", "女孩子", "animegirl")
R18_TAGS = ("女の子", "少女", "女孩子")
GIRL_KEYWORDS = (
    "女の子",
    "女孩子",
    "少女",
    "girl",
    "animegirl",
    "young girl",
    "cat and girl",
)
EXCLUDED_KEYWORDS = ("男の子", "男孩子", "少年", "boy")

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS


def clamp_count(raw_value: Optional[str]) -> int:
    """解析并约束推送数量"""
    text = (raw_value or "").strip()
    if not text:
        return DEFAULT_PUSH_COUNT

    try:
        count = int(text)
    except ValueError as exc:
        raise ValueError("数量必须是整数") from exc

    if count < 1:
        raise ValueError("数量必须大于 0")
    if count > MAX_PUSH_COUNT:
        raise ValueError(f"单次最多推送 {MAX_PUSH_COUNT} 张图")
    return count


def normalize_tags(tags: Any) -> list[str]:
    """清洗标签列表"""
    if not isinstance(tags, list):
        return []
    result: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if text:
            result.append(text)
    return result


def looks_like_bishoujo(tags: list[str]) -> bool:
    """尽量保证返回的是美少女向图片"""
    if not tags:
        return True

    lowered = [tag.lower() for tag in tags]
    if any(any(word in tag for word in EXCLUDED_KEYWORDS) for tag in tags):
        return any(any(word in tag for word in GIRL_KEYWORDS) for tag in tags)

    return any(any(keyword in tag for keyword in GIRL_KEYWORDS) for tag in lowered)


async def request_lolicon(
    session: aiohttp.ClientSession, *, is_r18: bool, tag: str, count: int
) -> list[dict[str, Any]]:
    """向图源接口请求图片"""
    params = {
        "r18": 1 if is_r18 else 0,
        "tag": tag,
        "num": max(1, min(ALBUM_BATCH_SIZE, count)),
        "size": "regular",
    }

    async with session.get(
        LOLICON_API_URL,
        params=params,
        headers=COMMON_HEADERS,
    ) as response:
        data = await response.json(content_type=None)

    if response.status != 200:
        raise RuntimeError(f"图源接口返回异常状态码：{response.status}")

    error_message = str(data.get("error") or "").strip()
    if error_message:
        raise RuntimeError(error_message)

    result = data.get("data")
    if not isinstance(result, list):
        return []
    return result


def normalize_item(raw_item: dict[str, Any], *, is_r18: bool) -> Optional[dict[str, Any]]:
    """标准化单张图片信息"""
    urls = raw_item.get("urls") or {}
    image_url = str(urls.get("regular") or urls.get("original") or "").strip()
    if not image_url:
        return None

    tags = normalize_tags(raw_item.get("tags"))
    if not looks_like_bishoujo(tags):
        return None

    pid_raw = raw_item.get("pid")
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        return None

    item_is_r18 = bool(raw_item.get("r18"))
    if is_r18 and not item_is_r18:
        return None
    if not is_r18 and item_is_r18:
        return None

    return {
        "pid": pid,
        "title": str(raw_item.get("title") or "未命名作品").strip(),
        "author": str(raw_item.get("author") or "未知作者").strip(),
        "tags": tags,
        "url": image_url,
        "ext": str(raw_item.get("ext") or "jpg").strip().lower() or "jpg",
    }


async def fetch_pixiv_items(count: int, *, is_r18: bool) -> list[dict[str, Any]]:
    """获取指定数量的图片，自动去重并补拉"""
    candidates = list(R18_TAGS if is_r18 else SAFE_TAGS)
    random.shuffle(candidates)

    result: list[dict[str, Any]] = []
    seen_pids: set[int] = set()

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        for attempt in range(MAX_FETCH_ATTEMPTS):
            if len(result) >= count:
                break

            tag = candidates[attempt % len(candidates)]
            need_count = count - len(result)
            batch_items = await request_lolicon(
                session,
                is_r18=is_r18,
                tag=tag,
                count=need_count,
            )

            for raw_item in batch_items:
                normalized = normalize_item(raw_item, is_r18=is_r18)
                if not normalized:
                    continue
                if normalized["pid"] in seen_pids:
                    continue

                seen_pids.add(normalized["pid"])
                result.append(normalized)

                if len(result) >= count:
                    break

    return result


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """按批次切分列表"""
    return [items[index : index + size] for index in range(0, len(items), size)]


async def download_item(
    session: aiohttp.ClientSession,
    item: dict[str, Any],
    target_dir: Path,
    index: int,
) -> Path:
    """下载单张图片到本地临时目录"""
    async with session.get(item["url"], headers=COMMON_HEADERS) as response:
        if response.status != 200:
            raise RuntimeError(f"下载图片失败，状态码：{response.status}")
        image_bytes = await response.read()

    return await asyncio.to_thread(
        normalize_image_bytes,
        image_bytes,
        target_dir,
        item["pid"],
        index,
    )


def normalize_image_bytes(image_bytes: bytes, target_dir: Path, pid: int, index: int) -> Path:
    """将图片规范化为 Telegram 更稳定的 JPEG 尺寸"""
    target_path = target_dir / f"pixiv_{pid}_{index}.jpg"
    with Image.open(BytesIO(image_bytes)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")

        width, height = image.size
        if max(width, height) > MAX_IMAGE_SIDE:
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), RESAMPLE_LANCZOS)

        if image.width < 10 or image.height < 10:
            raise RuntimeError("图片尺寸过小，无法发送为 Telegram 照片")

        image.save(target_path, format="JPEG", quality=90, optimize=True)

    return target_path


async def download_pixiv_items(items: list[dict[str, Any]], target_dir: Path) -> list[Path]:
    """批量下载图片，避免 Telegram 直接抓取外链失败"""
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def worker(index: int, item: dict[str, Any]) -> Path:
            async with semaphore:
                return await download_item(session, item, target_dir, index)

        tasks = [
            worker(index, item)
            for index, item in enumerate(items, start=1)
        ]
        return await asyncio.gather(*tasks)


async def send_pixiv_items(
    message: Message,
    bot: Client,
    items: list[dict[str, Any]],
    *,
    is_r18: bool,
):
    """发送单图或图集，图集优先走相册"""
    chat_id = message.chat.id
    reply_to_message_id = message.reply_to_message.id if message.reply_to_message else None
    total_count = len(items)
    has_spoiler = True
    with tempfile.TemporaryDirectory(prefix="pixivshow_") as tmp_dir:
        downloaded_paths = await download_pixiv_items(items, Path(tmp_dir))

        if total_count == 1:
            await bot.send_photo(
                chat_id=chat_id,
                photo=str(downloaded_paths[0]),
                has_spoiler=has_spoiler,
                reply_to_message_id=reply_to_message_id,
            )
            return

        first_batch = True
        for batch_index, batch in enumerate(chunked(items, ALBUM_BATCH_SIZE)):
            path_batch = chunked(downloaded_paths, ALBUM_BATCH_SIZE)[batch_index]
            media: list[InputMediaPhoto] = []
            for index, _item in enumerate(batch):
                media.append(
                    InputMediaPhoto(
                        media=str(path_batch[index]),
                        has_spoiler=has_spoiler,
                    )
                )

            try:
                await bot.send_media_group(
                    chat_id=chat_id,
                    media=media,
                    reply_to_message_id=reply_to_message_id if first_batch else None,
                )
            except Exception as exc:
                logs.warning("PixivShow 相册发送失败，改为逐张发送：%s", exc)
                for index, _item in enumerate(batch):
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=str(path_batch[index]),
                        has_spoiler=has_spoiler,
                        reply_to_message_id=reply_to_message_id
                        if first_batch and index == 0
                        else None,
                    )

            first_batch = False


async def handle_pixiv_command(message: Message, bot: Client, *, is_r18: bool):
    """处理普通图 / R18 推图命令"""
    try:
        count = clamp_count(message.arguments)
    except ValueError as exc:
        return await message.edit(f"参数错误：{exc}")

    mode_text = "R18 美少女图片" if is_r18 else "美少女图片"
    await message.edit(f"正在获取 Pixiv {mode_text}，目标 {count} 张...")

    try:
        items = await fetch_pixiv_items(count, is_r18=is_r18)
    except Exception as exc:
        logs.error("PixivShow 获取图片失败：%s", exc)
        return await message.edit(f"获取 Pixiv 图片失败：{exc}")

    if not items:
        return await message.edit("没有拿到符合条件的 Pixiv 图片，请稍后再试。")

    try:
        await send_pixiv_items(message, bot, items, is_r18=is_r18)
    except Exception as exc:
        logs.error("PixivShow 发送图片失败：%s", exc)
        return await message.edit(f"发送 Pixiv 图片失败：{exc}")

    tip = "，已自动加遮罩" if is_r18 else ""
    await message.edit(f"已推送 {len(items)} 张 Pixiv {mode_text}{tip}。")
    with contextlib.suppress(Exception):
        await message.delete()


@listener(
    command="pixiv",
    description="推送 Pixiv 普通美少女动漫图",
    parameters="[数量]",
)
async def pixiv_push(message: Message, bot: Client):
    """推送普通图"""
    await handle_pixiv_command(message, bot, is_r18=False)


@listener(
    command="pixivr18",
    description="推送 Pixiv R18 美少女动漫图（自动遮罩）",
    parameters="[数量]",
)
async def pixiv_r18_push(message: Message, bot: Client):
    """推送 R18 图"""
    await handle_pixiv_command(message, bot, is_r18=True)
