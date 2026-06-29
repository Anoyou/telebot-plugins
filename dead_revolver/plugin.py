"""死亡左轮插件。"""
from __future__ import annotations

import asyncio
import html
import random
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from telethon import events

from app.worker.plugins.base import Plugin, PluginContext, public_entity_display_name, register

# ─────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────
TOTAL_CHAMBER = 6
JOIN_TIMEOUT = 60
TURN_TIMEOUT = 30
COURAGE_MULTIPLIER: dict[int, float] = {0: 1.00, 1: 1.10, 2: 1.25, 3: 1.45, 4: 1.70}
REDIS_MSG_KEY_PREFIX = "dead_revolver:msg:"
DEFAULT_START_KEYWORD = "开始挑战"
LEGACY_START_COMMAND = "dr_start"


def _courage_multiplier(courage: int) -> float:
    return COURAGE_MULTIPLIER.get(courage, 2.00)


def _bullet_count(round_num: int, alive_count: int = 6) -> int:
    """动态计算实弹数：基础轮次 + 存活人数惩罚。"""
    if round_num <= 2: base = 1
    elif round_num <= 4: base = 2
    else: base = 3
    # 存活人数惩罚：3人+1，2人+2，1人+3
    penalty = max(0, 4 - alive_count)
    return min(3, max(1, base + penalty))


def _next_player_id(players: list[Player]) -> int:
    if not players: return 1
    return max(p.player_id for p in players) + 1


def _interaction_msg_key(account_id: int, chat_id: int) -> str:
    return f"{REDIS_MSG_KEY_PREFIX}{account_id}:{chat_id}"


def _int_or_zero(val: Any) -> int:
    try: return int(val)
    except (TypeError, ValueError): return 0


def _int_payload(value: Any) -> int | None:
    try: return int(value)
    except (TypeError, ValueError): return None


def _payload_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _normalized_identity(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("@"):
        text = text[1:].strip()
    return text.casefold()


def _extract_chat_id(payload: dict[str, Any]) -> int | None:
    source = _payload_dict(payload, "source")
    return _int_payload(payload.get("chat_id") or source.get("chat_id"))


def _extract_event_type(payload: dict[str, Any]) -> str:
    for container in ("source", "trigger", "event"):
        t = payload.get(container)
        if isinstance(t, dict) and t.get("type"):
            return str(t["type"])
    return str(payload.get("event_type") or "")


def _extract_user_id(payload: dict[str, Any]) -> int | None:
    actor = _payload_dict(payload, "actor")
    return _int_payload(payload.get("user_id") or actor.get("user_id") or payload.get("payer_user_id"))


def _extract_display_name(payload: dict[str, Any]) -> str:
    actor = _payload_dict(payload, "actor")
    return str(payload.get("sender_name") or actor.get("display_name") or payload.get("payer_name") or "未知用户")


def _extract_message_text(payload: dict[str, Any]) -> str:
    source = _payload_dict(payload, "source")
    trigger = _payload_dict(payload, "trigger")
    event = _payload_dict(payload, "event")
    value = (
        payload.get("message_text")
        or payload.get("text")
        or source.get("message_text")
        or source.get("text")
        or trigger.get("message_text")
        or trigger.get("text")
        or event.get("message_text")
        or event.get("text")
    )
    return str(value or "").strip()


def _configured_start_keyword(*configs: dict[str, Any] | None) -> str:
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        value = cfg.get("start_keyword") or cfg.get("start_command")
        text = str(value or "").strip()
        if text:
            return text
    return DEFAULT_START_KEYWORD


def _event_chat_id(event: Any) -> int | None:
    msg = getattr(event, "message", event)
    return getattr(event, "chat_id", None) or getattr(msg, "chat_id", None)


def _event_sender_id(event: Any) -> int | None:
    msg = getattr(event, "message", event)
    return getattr(event, "sender_id", None) or getattr(msg, "sender_id", None)


async def _event_sender_name(event: Any) -> str:
    sender = None
    for target in (event, getattr(event, "message", event)):
        getter = getattr(target, "get_sender", None)
        if not callable(getter): continue
        try: sender = await getter()
        except Exception: sender = None
        if sender is not None: break
    return public_entity_display_name(sender, fallback_id=_event_sender_id(event), default="用户")


# ─────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────
@dataclass
class Chamber:
    total: int = TOTAL_CHAMBER
    bullet_positions: list[int] = field(default_factory=list)
    current_position: int = 0

    @staticmethod
    def load(num_bullets: int) -> Chamber:
        num_bullets = max(1, min(num_bullets, TOTAL_CHAMBER))
        return Chamber(bullet_positions=sorted(random.sample(range(TOTAL_CHAMBER), num_bullets)))

    def fire(self) -> bool:
        hit = self.current_position in self.bullet_positions
        if hit: self.bullet_positions.remove(self.current_position)
        self.current_position += 1
        return hit

    def reset(self, num_bullets: int) -> None:
        num_bullets = max(1, min(num_bullets, TOTAL_CHAMBER))
        self.bullet_positions = sorted(random.sample(range(TOTAL_CHAMBER), num_bullets))
        self.current_position = 0

    def bullets_remaining(self) -> int:
        return sum(1 for pos in self.bullet_positions if pos >= self.current_position)


@dataclass
class Player:
    player_id: int
    user_id: int
    display_name: str
    alive: bool = True
    courage: int = 0
    paid: int = 0
    message_id: int | None = None
    killed_by: str = ""


@dataclass
class GameState:
    game_id: str
    chat_id: int
    host_user_id: int
    entry_fee: int
    players: list[Player] = field(default_factory=list)
    chamber: Chamber | None = None
    round_num: int = 1
    turn_order: list[int] = field(default_factory=list)
    turn_index: int = 0
    game_message_id: int | None = None
    phase: str = "joining"
    timeout_task: asyncio.Task | None = None
    turn_timer: asyncio.Task | None = None
    interaction_bot: bool = False
    tracked_msg_ids: list[int] = field(default_factory=list)
    guidance_msg_id: int | None = None
    created_at: float = 0.0
    start_keyword: str = DEFAULT_START_KEYWORD


# ─────────────────────────────────────────────────────
# 插件
# ─────────────────────────────────────────────────────
@register
class DeadRevolverPlugin(Plugin):
    key = "dead_revolver"
    display_name = "死亡左轮"
    message_channels = {"incoming"}
    owner_only = False

    def __init__(self) -> None:
        super().__init__()
        self._games: dict[int, GameState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._ctx: PluginContext | None = None
        self._self_tg_user_id: int | None = None
        self._self_tg_username: str | None = None
        self._self_receiver_names: set[str] = set()
        self._start_keyword = DEFAULT_START_KEYWORD

    # ── 生命周期 ────────────────────────────────
    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._start_keyword = _configured_start_keyword(getattr(ctx, "config", None) or {})
        self.commands = {
            "dr": self._cmd_create,
            LEGACY_START_COMMAND: self._cmd_start,
            DEFAULT_START_KEYWORD: self._cmd_start,
            self._start_keyword: self._cmd_start,
        }
        try:
            me = await ctx.client.get_me()
            self._self_tg_user_id = int(getattr(me, "id", 0) or 0) or None
            self._self_tg_username = getattr(me, "username", None)
            self._self_receiver_names = self._receiver_names_from_entity(me)
        except Exception:
            self._self_tg_user_id = None
            self._self_tg_username = None
            self._self_receiver_names = set()
        await self._log("info", "死亡左轮插件已启动。")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for gs in list(self._games.values()):
            if gs.timeout_task and not gs.timeout_task.done():
                gs.timeout_task.cancel()
            if gs.game_message_id is not None and ctx.client:
                try: await ctx.client.unpin_message(gs.chat_id, gs.game_message_id)
                except Exception: pass
                try: await ctx.client.delete_messages(gs.chat_id, gs.game_message_id)
                except Exception: pass
        self._games.clear(); self._locks.clear()
        await self._log("info", "死亡左轮插件已停止。")

    # ── 交互 Bot 入口 ──────────────────────────
    async def on_interaction(self, ctx: PluginContext, entry_key: str, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if entry_key != "join_paid_game": return None
        event_type = _extract_event_type(payload)
        chat_id = _extract_chat_id(payload)
        if chat_id is None: return []
        if event_type == "keyword":
            return await self._ibot_keyword(ctx, payload, chat_id)
        if event_type == "payment_confirmed":
            return await self._ibot_payment(ctx, payload, chat_id)
        if event_type == "callback_query":
            return await self._ibot_button(ctx, payload, chat_id)
        if event_type == "session_close":
            await self._ibot_close(ctx, chat_id)
        return []

    async def _ibot_keyword(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        text = _extract_message_text(payload)
        sender_id = _extract_user_id(payload)
        configured_keyword = _configured_start_keyword(
            _payload_dict(payload, "module_config"),
            getattr(ctx, "config", None) or {},
            {"start_keyword": self._start_keyword},
        )
        is_start_keyword = text in {LEGACY_START_COMMAND, DEFAULT_START_KEYWORD, configured_keyword}
        async with self._lock_for(chat_id):
            gs = self._games.get(chat_id)
            if gs is not None and self._matches_start_keyword(gs, text):
                error = await self._start_existing_game(ctx, gs, sender_id)
                if error:
                    return [{"type": "send_message", "text": error, "send_via": "interaction_bot"}]
                return []
        if is_start_keyword:
            return [{"type": "send_message", "text": "当前没有进行中的死亡左轮游戏。", "send_via": "interaction_bot"}]
        return await self._ibot_create(ctx, payload, chat_id)

    async def _ibot_create(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        sender_id = _extract_user_id(payload)
        if sender_id is None: return []
        module_config = _payload_dict(payload, "module_config")
        fee = _int_payload(module_config.get("entry_fee")) or 100
        start_keyword = _configured_start_keyword(module_config, getattr(ctx, "config", None) or {}, {"start_keyword": self._start_keyword})
        msg_text = str(payload.get("message_text") or "").strip()
        parsed_fee = self._parse_fee(msg_text.split())
        if parsed_fee > 0: fee = parsed_fee

        async with self._lock_for(chat_id):
            existing = self._games.get(chat_id)
            if existing and existing.phase in ("joining", "playing"):
                return [{"type": "send_message", "text": "当前已有进行中的死亡左轮游戏。"}]
            game = GameState(game_id=secrets.token_hex(4), chat_id=chat_id, host_user_id=sender_id,
                             entry_fee=fee, interaction_bot=True, created_at=time.time(),
                             start_keyword=start_keyword)
            self._games[chat_id] = game
            game.timeout_task = asyncio.create_task(self._join_timeout(ctx, game))
        await self._log("info", f"死亡左轮游戏已创建（交互Bot）：{game.game_id} 门票 {fee}。",
                        chat_id=chat_id, game_id=game.game_id, entry_fee=fee)
        return [{"type": "send_message", "text": self._render_lobby(game), "pin": True, "save_message_id_key": _interaction_msg_key(ctx.account_id, chat_id)}]

    async def _ibot_payment(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        user_id = _extract_user_id(payload)
        if user_id is None: return []
        display_name = _extract_display_name(payload)
        source = _payload_dict(payload, "source")
        paid = _int_payload(payload.get("amount") or source.get("amount")) or 0
        if not self._payment_receiver_matches_self(payload):
            return [{
                "type": "send_message",
                "text": f"这笔付款没有转给{self._receiver_label()}，不会加入死亡左轮。",
                "send_via": "interaction_bot",
            }]

        async with self._lock_for(chat_id):
            gs = self._games.get(chat_id)
            if gs is None or gs.phase != "joining":
                return [{"type": "send_message", "text": "当前没有等待加入的死亡左轮游戏。"}]
            if any(p.user_id == user_id for p in gs.players): return []
            if paid != gs.entry_fee:
                return [{"type": "send_message", "text": f"转账金额不符，本局门票为 {gs.entry_fee}，请转账恰好此金额报名。"}]
            player = Player(player_id=_next_player_id(gs.players), user_id=user_id, display_name=display_name,
                            paid=paid, message_id=_int_payload(payload.get("source_message_id")) or _int_payload(payload.get("message_id")))
            gs.players.append(player)

        lobby = self._render_lobby(gs)
        msg_key = _interaction_msg_key(ctx.account_id, chat_id)
        actions: list[dict[str, Any]] = [
            {"type": "send_message", "text": f"{html.escape(display_name)} 已报名死亡左轮！当前 {len(gs.players)} 名玩家。", "send_via": "interaction_bot"},
        ]
        lobby_action: dict[str, Any] = {"type": "send_message", "text": lobby, "send_via": "interaction_bot"}
        if ctx.redis:
            raw = await ctx.redis.get(msg_key)
            if raw: lobby_action["edit_message_id"] = _int_or_zero(raw)
            else: lobby_action.update(pin=True, save_message_id_key=msg_key)
        else:
            lobby_action["pin"] = True
        actions.append(lobby_action)
        return actions

    async def _ibot_button(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        data = str(payload.get("callback_data") or payload.get("message_text") or "").strip()
        parts = data.split("_")
        if len(parts) < 3 or parts[0] != "dr" or parts[1] != "shoot":
            return []
        target_id = _int_payload(parts[2])
        if target_id is None: return []
        user_id = _extract_user_id(payload)
        if user_id is None: return []

        async with self._lock_for(chat_id):
            gs = self._games.get(chat_id)
            if gs is None or gs.phase != "playing": return []
            current = self._current_player(gs)
            if current is None or current.user_id != user_id: return []
            target = next((p for p in gs.players if p.player_id == target_id), None)
            if target is None or not target.alive: return []
            self._cancel_turn_timer(gs)
            sn = html.escape(current.display_name)
            if target_id == current.player_id:
                hit = gs.chamber.fire()
                if hit:
                    current.alive = False; current.killed_by = "自己"
                    result = f"💀 {sn} 对自己开枪，中弹身亡！"
                else:
                    current.courage += 1
                    result = f"🔫 {sn} 对自己开枪——空枪！勇气 +1（当前 {current.courage}）"
            else:
                tn = html.escape(target.display_name)
                hit = gs.chamber.fire()
                if hit:
                    target.alive = False; target.killed_by = current.display_name
                    result = f"💀 {sn} 对 {tn} 开枪，{tn} 被击杀了！"
                else:
                    result = f"🔫 {sn} 对 {tn} 开枪——未命中！"
                gs.chamber.reset(_bullet_count(gs.round_num, len([p for p in gs.players if p.alive])))
            await self._send_bot_msg(ctx, gs, result)
            await self._advance_turn(ctx, gs)
            return []

    async def _ibot_close(self, ctx: PluginContext, chat_id: int) -> None:
        gs = self._games.pop(chat_id, None)
        if gs and gs.timeout_task and not gs.timeout_task.done(): gs.timeout_task.cancel()
        self._locks.pop(chat_id, None)
        if ctx.redis: await ctx.redis.delete(_interaction_msg_key(ctx.account_id, chat_id))
        await self._log("info", f"死亡左轮交互 Bot 会话已清理：聊天 {chat_id}。", chat_id=chat_id)

    # ── 命令 handler ────────────────────────────
    async def _cmd_create(self, client: Any, event: events.NewMessage.Event, args: list[str],
                          account_id: int, ctx: PluginContext) -> None:
        fee = self._parse_fee(args)
        if fee <= 0: await event.reply("请指定门票金额，例如：dr 100"); return
        start_keyword = _configured_start_keyword(getattr(ctx, "config", None) or {}, {"start_keyword": self._start_keyword})
        chat_id = _event_chat_id(event)
        if chat_id is None: return
        async with self._lock_for(chat_id):
            existing = self._games.get(chat_id)
            if existing and existing.phase in ("joining", "playing"):
                await event.reply("当前已有进行中的死亡左轮游戏。"); return
            sender_id = _event_sender_id(event)
            if sender_id is None: return
            game = GameState(game_id=secrets.token_hex(4), chat_id=chat_id, host_user_id=sender_id,
                             entry_fee=fee, created_at=time.time(), start_keyword=start_keyword)
            msg = await event.reply(self._render_lobby(game))
            game.game_message_id = msg.id
            try: await client.pin_message(chat_id, msg.id)
            except Exception: pass
            self._games[chat_id] = game
            game.timeout_task = asyncio.create_task(self._join_timeout(ctx, game))
        await self._log("info", f"死亡左轮游戏已创建：{game.game_id} 门票 {fee}。",
                        chat_id=chat_id, game_id=game.game_id, entry_fee=fee)

    async def _cmd_start(self, client: Any, event: events.NewMessage.Event, args: list[str],
                         account_id: int, ctx: PluginContext) -> None:
        chat_id = _event_chat_id(event)
        if chat_id is None: return
        async with self._lock_for(chat_id):
            gs = self._games.get(chat_id)
            if gs is None: await event.reply("当前没有进行中的死亡左轮游戏。"); return
            sender_id = _event_sender_id(event)
            error = await self._start_existing_game(ctx, gs, sender_id)
            if error:
                await event.reply(error)

    # ── on_message ───────────────────────────────
    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None:
        if self._self_tg_user_id is not None and getattr(event, "outgoing", False): return
        chat_id = _event_chat_id(event)
        sender_id = _event_sender_id(event)
        text = getattr(event, "raw_text", None) or getattr(event, "text", None) or ""
        text = str(text).strip()
        if chat_id is None or sender_id is None: return
        gs = self._games.get(chat_id)
        if gs is None: return

        if gs.interaction_bot:
            if self._matches_start_keyword(gs, text):
                error: str | None = None
                start_gs: GameState | None = None
                async with self._lock_for(chat_id):
                    gs2 = self._games.get(chat_id)
                    if gs2 is not None and self._matches_start_keyword(gs2, text):
                        start_gs = gs2
                        error = await self._start_existing_game(ctx, gs2, sender_id)
                if error and start_gs is not None:
                    msg_id = await self._send_bot_msg(ctx, start_gs, error)
                    if msg_id is None:
                        try: await ctx.client.send_message(chat_id, error)
                        except Exception: pass
                return
            handled = await self._userbot_transfer(ctx, event, gs, text, sender_id)
            if not handled:
                async with self._lock_for(chat_id):
                    gs2 = self._games.get(chat_id)
                    if gs2 is not None:
                        await self._handle_input(ctx, gs2, text, sender_id)
            return

        async with self._lock_for(chat_id):
            gs2 = self._games.get(chat_id)
            if gs2 is None: return
            if self._matches_start_keyword(gs2, text):
                error = await self._start_existing_game(ctx, gs2, sender_id)
                if error:
                    try: await ctx.client.send_message(chat_id, error)
                    except Exception: pass
                return
            if text.startswith("+"):
                await self._userbot_join(ctx, event, gs2, sender_id, text)
                return
            await self._handle_input(ctx, gs2, text, sender_id)

    async def _userbot_join(self, ctx: PluginContext, event: events.NewMessage.Event, gs: GameState,
                            sender_id: int, text: str) -> None:
        if gs.phase != "joining": return
        fee = _int_payload(text.lstrip("+"))
        if fee is None: return
        if any(p.user_id == sender_id for p in gs.players): return
        if gs.players: gs.entry_fee = fee
        sender_name = await _event_sender_name(event)
        player = Player(player_id=_next_player_id(gs.players), user_id=sender_id, display_name=sender_name)
        gs.players.append(player)
        lobby = self._render_lobby(gs)
        await self._update_lobby(ctx, gs, lobby)
        if gs.game_message_id is None:
            try:
                msg = await ctx.client.send_message(gs.chat_id, lobby)
                gs.game_message_id = msg.id
                try: await ctx.client.pin_message(gs.chat_id, msg.id)
                except Exception: pass
            except Exception: pass
        try: await ctx.client.send_message(gs.chat_id, f"{html.escape(sender_name)} 已报名！当前 {len(gs.players)} 名玩家。")
        except Exception: pass

    async def _userbot_transfer(self, ctx: PluginContext, event: events.NewMessage.Event,
                                gs: GameState, text: str, sender_id: int) -> bool:
        payer_match = re.search(r"^\s*(.+?)\s*(?:转出|射出|转账)\s*(\d+)\b", text, re.M)
        receiver_match = re.search(r"^\s*(.+?)\s*(?:收到|接收|收款)\s*(\d+)\b", text, re.M)
        if not payer_match or not receiver_match: return False
        try: paid = int(payer_match.group(2))
        except ValueError: return False
        if paid != gs.entry_fee: return False

        from app.db.base import AsyncSessionLocal
        from app.services.account_bot_service import get_transfer_notice_config
        async with AsyncSessionLocal() as db:
            cfg = await get_transfer_notice_config(db, ctx.account_id)
        if not cfg.get("enabled"): return False
        trusted = cfg.get("trusted_bot_id")
        if trusted and str(sender_id) != str(trusted): return False
        rules = cfg.get("rules") or []
        has_payment_rule = any(
            isinstance(r, dict) and r.get("enabled", True)
            and str(r.get("trigger_mode") or "payment") in ("payment", "both")
            and (not r.get("chat_ids") or gs.chat_id in (r.get("chat_ids") or []))
            for r in rules
        )
        if not has_payment_rule: return False

        receiver_name = receiver_match.group(1).strip()
        if not self._transfer_notice_receiver_matches_self(event, receiver_name):
            return False

        reply_id = getattr(event.message, "reply_to_msg_id", None)
        user_id: int | None = None
        if reply_id:
            try:
                replied = await ctx.client.get_messages(gs.chat_id, ids=reply_id)
                user_id = getattr(replied, "sender_id", None)
            except Exception: pass
        # 通知消息没有 reply_id 时回退到发送者，避免付款报名漏记。
        if user_id is None:
            user_id = sender_id
        if user_id is None: return False

        payload: dict[str, Any] = {
            "event": {"type": "payment_confirmed", "chat_id": gs.chat_id},
            "source": {"type": "payment_confirmed", "chat_id": gs.chat_id, "amount": paid},
            "actor": {"user_id": user_id, "display_name": payer_match.group(1).strip()},
            "trigger": {"type": "payment_confirmed"},
            "session": {},
            "event_type": "payment_confirmed",
            "account_id": ctx.account_id,
            "chat_id": gs.chat_id,
            "message_text": text,
            "amount": paid,
            "payer_user_id": user_id,
            "receiver_name": receiver_name,
            "payment": {
                "amount": paid,
                "payer_name": payer_match.group(1).strip(),
                "receiver_name": receiver_name,
            },
            "source_message_id": reply_id,
        }
        actions = await self._ibot_payment(ctx, payload, gs.chat_id)
        if actions:
            from app.db.base import AsyncSessionLocal
            from app.services.account_bot_service import get_interaction_bot_token, send_message, edit_message, call_bot_api
            async with AsyncSessionLocal() as db:
                token = await get_interaction_bot_token(db, ctx.account_id)
            if token:
                for action in actions:
                    action_type = str(action.get("type") or "")
                    if action_type != "send_message": continue
                    txt = str(action.get("text") or "").strip()
                    if not txt: continue
                    edit_id = _int_payload(action.get("edit_message_id"))
                    if edit_id:
                        try: await edit_message(token, gs.chat_id, edit_id, txt)
                        except Exception: pass
                    else:
                        try:
                            result = await send_message(token, gs.chat_id, txt)
                            sent_id = result.get("message_id") if isinstance(result, dict) else None
                            if sent_id:
                                gs.tracked_msg_ids.append(sent_id)
                                asyncio.create_task(self._delete_after(token, gs.chat_id, sent_id, 15))
                            if action.get("pin") and sent_id:
                                try: await call_bot_api(token, "pinChatMessage", {"chat_id": gs.chat_id, "message_id": sent_id})
                                except Exception: pass
                        except Exception: pass
        return True

    async def _handle_input(self, ctx: PluginContext, gs: GameState, text: str, sender_id: int) -> None:
        """统一的游戏输入处理（交互 Bot 按钮 + userbot 文字）。"""
        if gs.phase != "playing": return
        current = self._current_player(gs)
        if current is None or current.user_id != sender_id: return
        target_id = _int_payload(text)
        if target_id is None: return
        target = next((p for p in gs.players if p.player_id == target_id), None)
        if target is None or not target.alive: return
        self._cancel_turn_timer(gs)
        await self._fire_shot(ctx, gs, current, target if target_id != current.player_id else None)

    def _matches_start_keyword(self, gs: GameState, text: str) -> bool:
        value = str(text or "").strip()
        return value in {LEGACY_START_COMMAND, DEFAULT_START_KEYWORD, gs.start_keyword}

    async def _start_existing_game(self, ctx: PluginContext, gs: GameState, sender_id: int | None) -> str | None:
        if gs.phase != "joining":
            return "游戏已经开始或已结束。"
        if sender_id != gs.host_user_id:
            return "只有庄家可以开始游戏。"
        if len(gs.players) < 2:
            return "至少需要 2 名玩家才能开始。"
        if gs.timeout_task and not gs.timeout_task.done():
            gs.timeout_task.cancel()
        await self._start_game(ctx, gs)
        return None

    # ── 定时器 ───────────────────────────────────
    def _cancel_turn_timer(self, gs: GameState) -> None:
        task = gs.turn_timer
        if task is None:
            return
        if task.done():
            gs.turn_timer = None
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        gs.turn_timer = None

    def _start_turn_timer(self, ctx: PluginContext, gs: GameState) -> None:
        self._cancel_turn_timer(gs)
        gs.turn_timer = asyncio.create_task(self._turn_timeout(ctx, gs))

    async def _turn_timeout(self, ctx: PluginContext, gs: GameState) -> None:
        await asyncio.sleep(TURN_TIMEOUT)
        async with self._lock_for(gs.chat_id):
            if gs.phase != "playing": return
            current = self._current_player(gs)
            if current is None: return
            if gs.guidance_msg_id and gs.interaction_bot:
                await self._edit_bot_msg(ctx, gs, gs.guidance_msg_id,
                                         f"⏰ {html.escape(current.display_name)} 超时未操作！")
            else:
                await self._send_bot_msg(ctx, gs, f"⏰ {html.escape(current.display_name)} 超时未操作！")
            await self._fire_shot(ctx, gs, current, None)

    # ── 射击核心 ─────────────────────────────────
    async def _fire_shot(self, ctx: PluginContext, gs: GameState, shooter: Player, target: Player | None) -> None:
        """统一射击逻辑：target=None 表示对自己开枪。"""
        if gs.chamber is None: return
        hit = gs.chamber.fire()
        sn = html.escape(shooter.display_name)
        if target is None:
            if hit:
                shooter.alive = False; shooter.killed_by = "自己"
                result = f"💀 {sn} 对自己开枪，中弹身亡！"
            else:
                shooter.courage += 1
                result = f"🔫 {sn} 对自己开枪——空枪！勇气 +1（当前 {shooter.courage}）"
        else:
            tn = html.escape(target.display_name)
            if hit:
                target.alive = False; target.killed_by = shooter.display_name
                result = f"💀 {sn} 对 {tn} 开枪，{tn} 被击杀了！"
            else:
                result = f"🔫 {sn} 对 {tn} 开枪——未命中！"
            gs.chamber.reset(_bullet_count(gs.round_num, len([p for p in gs.players if p.alive])))
        await self._send_bot_msg(ctx, gs, result)
        await self._advance_turn(ctx, gs)

    # ── 回合管理 ─────────────────────────────────
    async def _advance_turn(self, ctx: PluginContext, gs: GameState) -> None:
        alive = [p for p in gs.players if p.alive]
        if len(alive) <= 1:
            await self._end_game(ctx, gs, alive[0] if alive else None)
            return
        gs.turn_index += 1
        while gs.turn_index < len(gs.turn_order):
            pid = gs.turn_order[gs.turn_index]
            ply = next((p for p in gs.players if p.player_id == pid), None)
            if ply and ply.alive: break
            gs.turn_index += 1
        if gs.turn_index >= len(gs.turn_order):
            gs.round_num += 1
            gs.chamber = Chamber.load(_bullet_count(gs.round_num, len(alive)))
            alive_pids = [p.player_id for p in alive]
            random.shuffle(alive_pids)
            gs.turn_order = alive_pids
            gs.turn_index = 0
        await self._update_lobby(ctx, gs, self._render_game(gs))
        current = self._current_player(gs)
        if current:
            await self._send_turn_guidance(ctx, gs, current, alive)
            self._start_turn_timer(ctx, gs)

    async def _start_game(self, ctx: PluginContext, gs: GameState) -> None:
        gs.phase = "playing"
        alive_count = len([p for p in gs.players if p.alive])
        gs.chamber = Chamber.load(_bullet_count(gs.round_num, alive_count))
        alive_pids = [p.player_id for p in gs.players if p.alive]
        random.shuffle(alive_pids)
        gs.turn_order = alive_pids
        gs.turn_index = 0
        await self._update_lobby(ctx, gs, self._render_game(gs))
        current = self._current_player(gs)
        alive = [p for p in gs.players if p.alive]
        if current:
            await self._send_turn_guidance(ctx, gs, current, alive, start=True)
            self._start_turn_timer(ctx, gs)

    async def _join_timeout(self, ctx: PluginContext, gs: GameState) -> None:
        try:
            remaining = JOIN_TIMEOUT
            while remaining > 0 and gs.phase == "joining":
                sleep_time = min(10, remaining)
                await asyncio.sleep(sleep_time)
                remaining -= sleep_time
                if gs.phase != "joining": return
                await self._update_lobby(ctx, gs, self._render_lobby(gs))
            async with self._lock_for(gs.chat_id):
                if gs.phase != "joining": return
                if len(gs.players) < 2:
                    await self._cancel_game(ctx, gs, "等待超时，玩家人数不足 2 人，游戏取消。")
                else:
                    try: await self._start_game(ctx, gs)
                    except Exception:
                        await self._cancel_game(ctx, gs, "游戏启动失败，已取消。")
        except asyncio.CancelledError:
            return
        except Exception:
            pass

    # ── 发送引导 ─────────────────────────────────
    async def _send_turn_guidance(self, ctx: PluginContext, gs: GameState, current: Player,
                                  alive: list[Player], *, start: bool = False) -> None:
        await self._delete_guidance_message(ctx, gs)
        if start:
            txt = f"🔫 <b>游戏开始！</b>（第 1 轮，弹巢 {_bullet_count(gs.round_num, len(alive))} 发实弹）\n"
        else:
            txt = f"⏱ 第 {gs.round_num} 轮 · 弹巢 {_bullet_count(gs.round_num, len(alive))} 发实弹\n"
        txt += f"<b>轮到 {html.escape(current.display_name)}</b>，点击按钮选择射击目标："
        keyboard = [[{"text": f"{p.player_id}.{html.escape(p.display_name)}", "callback_data": f"dr_shoot_{p.player_id}"}] for p in alive]
        msg_id = await self._send_bot_msg(ctx, gs, txt, reply_markup={"inline_keyboard": keyboard})
        if msg_id: gs.guidance_msg_id = msg_id

    # ── 结算 ─────────────────────────────────────
    async def _end_game(self, ctx: PluginContext, gs: GameState, winner: Player | None) -> None:
        gs.phase = "ended"
        self._cancel_turn_timer(gs)
        if gs.timeout_task and not gs.timeout_task.done(): gs.timeout_task.cancel()
        pool = gs.entry_fee * len(gs.players)
        prize = int(pool * 0.9 * _courage_multiplier(winner.courage)) if winner else 0
        result_text = self._render_result(gs, winner, pool, prize)

        if gs.interaction_bot:
            lobby_id = await self._resolve_lobby_id(ctx, gs)
            token = await self._get_bot_token(ctx)
            if token:
                if lobby_id:
                    try: await self._bot_api(token, "editMessageText", {"chat_id": gs.chat_id, "message_id": lobby_id, "text": result_text, "parse_mode": "HTML"})
                    except Exception: pass
                if winner:
                    try: await self._bot_api(token, "sendMessage", {"chat_id": gs.chat_id, "text": f"🏆 {html.escape(winner.display_name)} 获胜！勇气 {winner.courage}，奖金 +{prize}", "parse_mode": "HTML"})
                    except Exception: pass
                if winner and prize > 0:
                    try:
                        if winner.message_id: await ctx.client.send_message(gs.chat_id, f"+{prize}", reply_to=winner.message_id)
                        else: await ctx.client.send_message(gs.chat_id, f"+{prize}")
                    except Exception: pass
                await self._cleanup_messages(ctx, gs, token, lobby_id)
        else:
            if gs.game_message_id:
                try: await ctx.client.edit_message(gs.chat_id, gs.game_message_id, result_text)
                except Exception: pass
            if winner and prize > 0:
                try:
                    if winner.message_id: await ctx.client.send_message(gs.chat_id, f"+{prize}", reply_to=winner.message_id)
                    else: await ctx.client.send_message(gs.chat_id, f"+{prize}")
                except Exception: pass
            if gs.game_message_id:
                try: await ctx.client.unpin_message(gs.chat_id, gs.game_message_id)
                except Exception: pass
                try: await ctx.client.delete_messages(gs.chat_id, gs.game_message_id)
                except Exception: pass

        if gs.interaction_bot and ctx.redis:
            await ctx.redis.delete(_interaction_msg_key(ctx.account_id, gs.chat_id))
        self._games.pop(gs.chat_id, None); self._locks.pop(gs.chat_id, None)

    async def _cancel_game(self, ctx: PluginContext, gs: GameState, reason: str) -> None:
        gs.phase = "ended"
        self._cancel_turn_timer(gs)
        refund_players = [p for p in gs.players if p.paid > 0]
        refund_count = len(refund_players)
        total_refund = sum(p.paid for p in refund_players)
        cancel_text = self._render_lobby(gs) + f"\n\n⚠️ {reason}"
        if refund_players:
            cancel_text += f"\n📋 正在自动退还 {refund_count} 名玩家的门票费用（共 {total_refund}）…"

        if gs.interaction_bot:
            lobby_id = await self._resolve_lobby_id(ctx, gs)
            token = await self._get_bot_token(ctx)
            if token:
                try: await self._bot_api(token, "sendMessage", {"chat_id": gs.chat_id, "text": f"⚠️ {html.escape(reason)}", "parse_mode": "HTML"})
                except Exception: pass
                for p in refund_players:
                    try:
                        params: dict[str, Any] = {"chat_id": gs.chat_id, "text": f"+{p.paid}"}
                        if p.message_id:
                            params["reply_to_message_id"] = p.message_id
                        await self._bot_api(token, "sendMessage", params)
                    except Exception: pass
                if refund_players:
                    try: await self._bot_api(token, "sendMessage", {"chat_id": gs.chat_id, "text": f"✅ 已自动退还 {refund_count} 名玩家的门票费用。", "parse_mode": "HTML"})
                    except Exception: pass
                await self._cleanup_messages(ctx, gs, token, lobby_id)
        else:
            if gs.game_message_id:
                try: await ctx.client.edit_message(gs.chat_id, gs.game_message_id, cancel_text)
                except Exception: pass
                try: await ctx.client.unpin_message(gs.chat_id, gs.game_message_id)
                except Exception: pass
                try: await ctx.client.delete_messages(gs.chat_id, gs.game_message_id)
                except Exception: pass
            for p in refund_players:
                try:
                    if p.message_id: await ctx.client.send_message(gs.chat_id, f"+{p.paid}", reply_to=p.message_id)
                    else: await ctx.client.send_message(gs.chat_id, f"+{p.paid}")
                except Exception: pass

        if gs.interaction_bot and ctx.redis:
            await ctx.redis.delete(_interaction_msg_key(ctx.account_id, gs.chat_id))
        self._games.pop(gs.chat_id, None); self._locks.pop(gs.chat_id, None)

    # ── 交互 Bot 工具 ───────────────────────────
    async def _get_bot_token(self, ctx: PluginContext) -> str | None:
        from app.db.base import AsyncSessionLocal
        from app.services.account_bot_service import get_interaction_bot_token
        async with AsyncSessionLocal() as db:
            return await get_interaction_bot_token(db, ctx.account_id)

    async def _bot_api(self, token: str, method: str, payload: dict[str, Any]) -> Any:
        from app.services.account_bot_service import call_bot_api
        return await call_bot_api(token, method, payload)

    async def _resolve_lobby_id(self, ctx: PluginContext, gs: GameState) -> int | None:
        if gs.game_message_id: return gs.game_message_id
        if ctx.redis:
            raw = await ctx.redis.get(_interaction_msg_key(ctx.account_id, gs.chat_id))
            return _int_or_zero(raw) if raw else None
        return None

    async def _send_bot_msg(self, ctx: PluginContext, gs: GameState, txt: str,
                            reply_to: int | None = None, reply_markup: dict | None = None) -> int | None:
        token = await self._get_bot_token(ctx)
        if not token: return None
        try:
            from app.services.account_bot_service import send_message
            result = await send_message(token, gs.chat_id, txt, reply_to_message_id=reply_to, reply_markup=reply_markup)
            msg_id = result.get("message_id") if isinstance(result, dict) else None
            if msg_id: gs.tracked_msg_ids.append(msg_id)
            return msg_id
        except Exception:
            return None

    async def _edit_bot_msg(self, ctx: PluginContext, gs: GameState, msg_id: int, txt: str) -> None:
        token = await self._get_bot_token(ctx)
        if not token: return
        try:
            from app.services.account_bot_service import edit_message
            await edit_message(token, gs.chat_id, msg_id, txt)
        except Exception: pass

    async def _delete_guidance_message(self, ctx: PluginContext, gs: GameState) -> None:
        msg_id = gs.guidance_msg_id
        if msg_id is None:
            return
        token = await self._get_bot_token(ctx)
        if not token:
            return
        try:
            from app.services.account_bot_service import delete_message as del_msg
            await del_msg(token, gs.chat_id, msg_id)
        except Exception:
            return
        if gs.guidance_msg_id == msg_id:
            gs.guidance_msg_id = None
        gs.tracked_msg_ids = [mid for mid in gs.tracked_msg_ids if mid != msg_id]

    async def _cleanup_messages(self, ctx: PluginContext, gs: GameState, token: str, lobby_id: int | None) -> None:
        from app.services.account_bot_service import delete_message as del_msg
        for mid in gs.tracked_msg_ids:
            try: await del_msg(token, gs.chat_id, mid)
            except Exception: pass
        if lobby_id:
            try: await self._bot_api(token, "unpinChatMessage", {"chat_id": gs.chat_id, "message_id": lobby_id})
            except Exception: pass

    # ── 消息管理 ─────────────────────────────────
    async def _update_lobby(self, ctx: PluginContext, gs: GameState, text: str) -> None:
        """编辑大厅消息（支持 userbot 和交互 Bot）。"""
        msg_id = gs.game_message_id
        if gs.interaction_bot and msg_id is None:
            msg_key = _interaction_msg_key(ctx.account_id, gs.chat_id)
            if ctx.redis:
                raw = await ctx.redis.get(msg_key)
                if raw: msg_id = _int_or_zero(raw)
        if msg_id is None: return
        if gs.interaction_bot:
            await self._edit_bot_msg(ctx, gs, msg_id, text)
        else:
            try: await ctx.client.edit_message(gs.chat_id, msg_id, text)
            except Exception: pass

    async def _delete_after(self, token: str, chat_id: int, msg_id: int, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            from app.services.account_bot_service import delete_message as del_msg
            await del_msg(token, chat_id, msg_id)
        except Exception: pass

    def _receiver_names_from_entity(self, entity: Any) -> set[str]:
        names: set[str] = set()
        username = getattr(entity, "username", None)
        if username:
            names.add(_normalized_identity(username))
        first_name = str(getattr(entity, "first_name", "") or "").strip()
        last_name = str(getattr(entity, "last_name", "") or "").strip()
        for item in (first_name, last_name, f"{first_name} {last_name}".strip()):
            if item:
                names.add(_normalized_identity(item))
        display_name = public_entity_display_name(
            entity,
            fallback_id=None,
            default="",
        )
        if display_name:
            names.add(_normalized_identity(display_name))
        return {item for item in names if item}

    def _receiver_label(self) -> str:
        if self._self_tg_username:
            return f"@{self._self_tg_username}"
        if self._self_receiver_names:
            return "当前 UserBot 收款人"
        return "当前 UserBot 收款人（暂未读取到账号名）"

    def _receiver_text_matches_self(self, value: Any) -> bool:
        candidate = _normalized_identity(value)
        return bool(candidate and candidate in self._self_receiver_names)

    def _transfer_notice_receiver_matches_self(self, event: Any, receiver_name: str) -> bool:
        if self._receiver_text_matches_self(receiver_name):
            return True
        if self._self_tg_user_id is None:
            return False
        entities = getattr(getattr(event, "message", None), "entities", None) or []
        return any(
            getattr(getattr(ent, "user", None), "id", None) == self._self_tg_user_id
            for ent in entities
        )

    def _payment_receiver_matches_self(self, payload: dict[str, Any]) -> bool:
        payment = _payload_dict(payload, "payment")
        source = _payload_dict(payload, "source")
        receiver = _payload_dict(payment, "receiver")
        receiver_ids = [
            receiver.get("user_id"),
            payment.get("receiver_user_id"),
            payload.get("receiver_user_id"),
            source.get("receiver_user_id"),
        ]
        clean_ids = [item for item in (_int_payload(value) for value in receiver_ids) if item is not None]
        if clean_ids:
            return self._self_tg_user_id is not None and any(item == self._self_tg_user_id for item in clean_ids)

        receiver_names = [
            receiver.get("username"),
            receiver.get("display_name"),
            receiver.get("name"),
            payment.get("receiver_username"),
            payment.get("receiver_display_name"),
            payment.get("receiver_name"),
            payload.get("receiver_username"),
            payload.get("receiver_display_name"),
            payload.get("receiver_name"),
            source.get("receiver_username"),
            source.get("receiver_display_name"),
            source.get("receiver_name"),
        ]
        clean_names = [item for item in receiver_names if str(item or "").strip()]
        if clean_names:
            return any(self._receiver_text_matches_self(item) for item in clean_names)
        return False

    def _current_player(self, gs: GameState) -> Player | None:
        if gs.turn_index >= len(gs.turn_order): return None
        pid = gs.turn_order[gs.turn_index]
        return next((p for p in gs.players if p.player_id == pid), None)

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None: lock = asyncio.Lock(); self._locks[chat_id] = lock
        return lock

    @staticmethod
    def _parse_fee(args: list[str]) -> int:
        for arg in args:
            try: v = int(arg); return v if v > 0 else 0
            except ValueError: continue
        return 0

    # ── 渲染 ─────────────────────────────────────
    def _render_lobby(self, gs: GameState) -> str:
        lines = [
            "🔫 <b>死亡左轮</b>",
            f"门票：{gs.entry_fee}",
            "",
            "📋 <b>玩法规则</b>",
            f"• 实弹数动态调整：基础轮次（1-2轮1发，3-4轮2发，5-6轮3发）+ 存活人数惩罚（人少时增加）",
            "• 对自己开枪后弹巢不重填；对目标开枪后弹巢重填；轮流操作，每轮顺序随机",
            "• 对自己开枪未命中则 <b>勇气 +1</b>，勇气越高最终奖金倍率越高",
            "",
            f"玩家列表（{len(gs.players)} 人）：",
        ]
        for p in gs.players:
            name = html.escape(p.display_name)
            paid_tag = " 💰" if p.paid > 0 else ""
            lines.append(f"  {p.player_id}. {name}{paid_tag}")
        if not gs.players:
            lines.append("  （暂无玩家）")
        start_keyword = html.escape(gs.start_keyword or self._start_keyword or DEFAULT_START_KEYWORD)
        lines.extend([
            "",
            "📌 参与方式",
            f"• 转账 {gs.entry_fee} → 自动报名",
            f"• 庄家发送{start_keyword} 开始（需至少 2 人）",
            f"⏰ {max(0, int(JOIN_TIMEOUT - (time.time() - gs.created_at)))} 秒后自动开始或取消",
        ])
        return "\n".join(lines)

    def _render_game(self, gs: GameState) -> str:
        alive = [p for p in gs.players if p.alive]
        lines = [
            "🔫 <b>死亡左轮</b>",
            f"游戏 ID：<code>{gs.game_id}</code>",
            f"第 {gs.round_num} 轮（弹巢：{_bullet_count(gs.round_num, len(alive))} 发实弹）",
            "",
            f"存活玩家（{len(alive)} 人）：",
        ]
        for p in gs.players:
            name = html.escape(p.display_name)
            if p.alive:
                courage = f" [勇气: {p.courage}]" if p.courage > 0 else ""
                lines.append(f"  🟢 {p.player_id}. {name}{courage}")
            else:
                killer = f"（击杀：{html.escape(p.killed_by)}）" if p.killed_by else ""
                lines.append(f"  💀 {p.player_id}. {name} {killer}")
        current = self._current_player(gs)
        if current:
            lines.extend(["", f"轮到 <b>{current.player_id}. {html.escape(current.display_name)}</b> 操作：",
                           "发送自己的<b>编号</b>对自己开枪，或发送其他玩家<b>编号</b>对目标开枪"])
        return "\n".join(lines)

    def _render_result(self, gs: GameState, winner: Player | None, pool: int, prize: int) -> str:
        lines = [
            "🔫 <b>死亡左轮 — 游戏结束</b>",
            f"游戏 ID：<code>{gs.game_id}</code>",
            f"总轮次：{gs.round_num}",
            "",
        ]
        if winner:
            name = html.escape(winner.display_name)
            lines.extend([
                f"🏆 赢家：<b>{winner.player_id}. {name}</b>",
                f"勇气值：{winner.courage}（倍率 x{_courage_multiplier(winner.courage):.2f}）",
                f"奖池：{pool} × 90% = {int(pool * 0.9)}",
                f"奖金：{int(pool * 0.9)} × {_courage_multiplier(winner.courage):.2f} = <b>{prize}</b>",
            ])
        else:
            lines.append("☠️ <b>无人存活！</b>")
        return "\n".join(lines)

    async def _log(self, level: str, message: str, **detail: Any) -> None:
        if self._ctx is None or self._ctx.log is None: return
        await self._ctx.log(level, message, **detail)


PLUGIN_CLASS = DeadRevolverPlugin
__all__ = ["Chamber", "Player", "GameState", "DeadRevolverPlugin", "PLUGIN_CLASS"]
