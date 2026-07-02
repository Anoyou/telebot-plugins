"""拼手气口令红包插件。

账号主人通过命令创建红包，群友发送当前财富密码领取。奖励消息必须由
UserBot 回复领取者消息，以便复用平台现有的转账链路。
"""

from __future__ import annotations

import asyncio
import random
import shlex
import string
import time
from dataclasses import dataclass, field
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

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


PLUGIN_VERSION = "1.0.1"
PLUGIN_KEY = "lucky_redpack"
DEFAULT_COMMAND = "rp"
DEFAULT_AMOUNT = 88888
DEFAULT_COUNT = 10
DEFAULT_MIN_SHARE_AMOUNT = 1
DEFAULT_SUFFIX_LENGTH = 4
DEFAULT_TTL_SECONDS = 3600
MAX_AMOUNT = 999_999_999
MAX_COUNT = 500
SUFFIX_CHARS = string.ascii_uppercase + string.digits


@dataclass
class ClaimRecord:
    user_id: int
    display_name: str
    amount: int
    message_id: int | None
    claimed_at: float = field(default_factory=time.time)


@dataclass
class LuckyRedpack:
    chat_id: int
    creator_user_id: int
    base_keyword: str
    total_amount: int
    total_count: int
    min_share_amount: int
    suffix_length: int
    created_at: float
    expires_at: float
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
    return " ".join(str(value or "").strip().split()).casefold()


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


def render_redpack_message(pack: LuckyRedpack) -> str:
    claimed_count = pack.total_count - pack.remaining_count
    return (
        "🧧 拼手气红包\n"
        f"总额：{pack.total_amount}｜剩余：{pack.remaining_count}/{pack.total_count}\n"
        f"财富密码：{pack.current_password}\n"
        "发送财富密码即可领取\n"
        "提示：财富密码被领一次会随机变动"
        + (f"\n已领取：{claimed_count} 人" if claimed_count else "")
    )


def render_settlement(pack: LuckyRedpack, *, expired: bool = False) -> str:
    title = "🕒 拼手气红包已超时" if expired else "🧧 拼手气红包已领完"
    lines = [
        title,
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


@register
class LuckyRedpackPlugin(Plugin):
    key = PLUGIN_KEY
    display_name = "拼手气口令红包"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = DEFAULT_COMMAND
        self._default_amount = DEFAULT_AMOUNT
        self._default_count = DEFAULT_COUNT
        self._min_share_amount = DEFAULT_MIN_SHARE_AMOUNT
        self._suffix_length = DEFAULT_SUFFIX_LENGTH
        self._ttl_seconds = DEFAULT_TTL_SECONDS
        self._delete_command_message = False
        self._allow_owner_claim = False
        self._packs: dict[int, LuckyRedpack] = {}
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

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = str(cfg.get("command") or DEFAULT_COMMAND).strip() or DEFAULT_COMMAND
        self._default_amount = _clamp_int(cfg.get("default_amount"), DEFAULT_AMOUNT, 1, MAX_AMOUNT)
        self._default_count = _clamp_int(cfg.get("default_count"), DEFAULT_COUNT, 1, MAX_COUNT)
        self._min_share_amount = _clamp_int(cfg.get("min_share_amount"), DEFAULT_MIN_SHARE_AMOUNT, 1, MAX_AMOUNT)
        self._suffix_length = _clamp_int(cfg.get("suffix_length"), DEFAULT_SUFFIX_LENGTH, 1, 12)
        self._ttl_seconds = _clamp_int(cfg.get("ttl_seconds"), DEFAULT_TTL_SECONDS, 30, 86400)
        self._delete_command_message = bool(cfg.get("delete_command_message", False))
        self._allow_owner_claim = bool(cfg.get("allow_owner_claim", False))
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[lucky_redpack] 已启动，指令：{self._command}")

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
            await self._reply(event, self._active_text(chat_id))
            return
        if action in {"clear", "清空"}:
            async with self._get_lock(chat_id):
                existed = self._packs.pop(chat_id, None)
            await self._reply(event, "已清空当前聊天的进行中红包。" if existed else "当前聊天没有进行中的红包。")
            return

        keyword, amount, count, error = parse_create_args(args, self._default_amount, self._default_count)
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
        pack = LuckyRedpack(
            chat_id=chat_id,
            creator_user_id=creator_id,
            base_keyword=keyword,
            total_amount=amount,
            total_count=count,
            min_share_amount=self._min_share_amount,
            suffix_length=self._suffix_length,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            remaining_amount=amount,
            remaining_count=count,
        )
        pack.current_suffix = self._new_suffix(pack)
        pack.used_passwords.add(_normalize_password(pack.current_password))

        async with self._get_lock(chat_id):
            current = self._packs.get(chat_id)
            if current and not current.is_finished() and not current.is_expired():
                await self._reply(event, "当前聊天已有进行中的拼手气红包，领完或清空后再发新的。")
                return
            self._packs[chat_id] = pack

        sent = await self._reply(event, render_redpack_message(pack))
        pack.message_id = _message_id_from_event(sent) if sent is not None else _message_id_from_event(event)
        self._track_task(asyncio.create_task(self._auto_expire(chat_id, ctx, pack.created_at)))
        if self._delete_command_message:
            await self._delete_event(event)

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        if ctx.client is None:
            return
        text = _event_text(event)
        if not text or text.startswith((",", "/", "，")):
            return
        chat_id = _chat_id_from_event(event)
        if not chat_id:
            return
        if _is_outgoing_event(event):
            return

        async with self._get_lock(chat_id):
            pack = self._packs.get(chat_id)
            if not pack or pack.is_finished():
                return
            if pack.is_expired():
                self._packs.pop(chat_id, None)
                settlement = render_settlement(pack, expired=True)
                send_after_lock = [("send", settlement, None)]
                pack_to_edit: LuckyRedpack | None = None
                claim_amount = 0
                claim_message_id = None
                finished = False
            else:
                send_after_lock = []
                pack_to_edit = None
                claim_amount = 0
                claim_message_id = None
                finished = False
                if _normalize_password(text) != _normalize_password(pack.current_password):
                    return
                sender = await self._sender(event)
                sender_id = int(getattr(sender, "id", 0) or _sender_id_from_event(event))
                if not sender_id:
                    return
                if sender_id == pack.creator_user_id and not self._allow_owner_claim:
                    return
                if getattr(sender, "is_bot", False):
                    return
                if sender_id in pack.claimed_user_ids:
                    return

                claim_amount = calculate_random_claim_amount(pack)
                if claim_amount <= 0:
                    return
                claim_message_id = _message_id_from_event(event)
                display_name = public_entity_display_name(sender, fallback_id=sender_id, default="玩家")
                pack.remaining_amount = max(0, pack.remaining_amount - claim_amount)
                pack.remaining_count = max(0, pack.remaining_count - 1)
                pack.claimed_user_ids.add(sender_id)
                pack.claims.append(
                    ClaimRecord(
                        user_id=sender_id,
                        display_name=display_name,
                        amount=claim_amount,
                        message_id=claim_message_id,
                    )
                )
                finished = pack.is_finished()
                if finished:
                    self._packs.pop(chat_id, None)
                    send_after_lock.append(("send", render_settlement(pack), pack.message_id))
                else:
                    pack.current_suffix = self._new_suffix(pack)
                    pack.used_passwords.add(_normalize_password(pack.current_password))
                    pack_to_edit = pack

        if claim_amount and claim_message_id:
            await ctx.client.send_message(chat_id, f"+{claim_amount}", reply_to=claim_message_id)
        if pack_to_edit is not None:
            await self._edit_pack_message(ctx, pack_to_edit)
        for action, text_value, reply_to in send_after_lock:
            if action == "send":
                await ctx.client.send_message(chat_id, text_value, reply_to=reply_to)
        if finished:
            return

    def _validate_amount_count(self, amount: int, count: int) -> str | None:
        if amount < count * self._min_share_amount:
            return f"总额太小：{count} 个红包至少需要 {count * self._min_share_amount}。"
        return None

    def _help_text(self) -> str:
        return (
            "🧧 拼手气口令红包\n"
            f"{self._usage_example()}\n"
            f"{current_command_prefix(fallback=',')}{self._command} active 查看当前红包\n"
            f"{current_command_prefix(fallback=',')}{self._command} clear 清空当前红包"
        )

    def _usage_example(self) -> str:
        return f"用法：{current_command_prefix(fallback=',')}{self._command} 发财 88888 10"

    def _active_text(self, chat_id: int) -> str:
        pack = self._packs.get(chat_id)
        if not pack or pack.is_finished() or pack.is_expired():
            return "当前聊天没有进行中的红包。"
        return render_redpack_message(pack)

    async def _reply(self, event: Any, text: str) -> Any:
        reply = getattr(event, "reply", None)
        if callable(reply):
            return await reply(text)
        respond = getattr(event, "respond", None)
        if callable(respond):
            return await respond(text)
        return None

    async def _sender(self, event: Any) -> Any:
        getter = getattr(event, "get_sender", None)
        if callable(getter):
            sender = await getter()
            if sender is not None:
                return sender
        return getattr(event, "sender", None) or getattr(getattr(event, "message", None), "sender", None)

    async def _edit_pack_message(self, ctx: PluginContext, pack: LuckyRedpack) -> None:
        if ctx.client is None or pack.message_id is None:
            return
        try:
            await ctx.client.edit_message(pack.chat_id, pack.message_id, render_redpack_message(pack))
        except Exception as exc:
            if ctx.log:
                await ctx.log("warn", f"[lucky_redpack] 红包消息更新失败：{type(exc).__name__}: {exc}")

    async def _delete_event(self, event: Any) -> None:
        delete = getattr(event, "delete", None) or getattr(getattr(event, "message", None), "delete", None)
        if callable(delete):
            try:
                await delete()
            except Exception:
                pass

    async def _auto_expire(self, chat_id: int, ctx: PluginContext, created_at: float) -> None:
        await asyncio.sleep(self._ttl_seconds)
        async with self._get_lock(chat_id):
            pack = self._packs.get(chat_id)
            if not pack or pack.created_at != created_at or pack.is_finished():
                return
            self._packs.pop(chat_id, None)
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
    "render_redpack_message",
    "render_settlement",
]
