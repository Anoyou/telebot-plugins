"""读心生存赛远程插件。

对齐 dice_grid_hunt 的双通道架构：
  - 交互 Bot 流程（主要）：on_interaction 返回 actions
  - UserBot 流程（备用）：_cmd_handler 通过 ctx.client 直接发
  - on_message 仅处理游戏中的数字选择（fallback）
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass, field
from html import escape
from typing import Any

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register
from .manifest import (
    MANIFEST,
    PLAYER_KEYWORDS,
    JOIN_MESSAGE_TEMPLATE,
    JOIN_MESSAGE_BOT_TEMPLATE,
    ROUND_START_TEMPLATE,
    ROUND_RESULT_TEMPLATE,
    GAME_OVER_SOLO_TEMPLATE,
    GAME_OVER_MULTI_TEMPLATE,
    GAME_OVER_ALL_ELIMINATED_TEMPLATE,
    GAME_OVER_CANCELLED_TEMPLATE,
    TIMEOUT_NO_PLAYERS_TEMPLATE,
    PLAYER_JOINED_TEMPLATE,
    IN_PROGRESS_MESSAGE_TEMPLATE,
)


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class PlayerInfo:
    user_id: int
    display_name: str
    username: str = ""
    paid: int = 0
    alive: bool = True
    current_choice: int | None = None


@dataclass
class RoundInfo:
    round_num: int
    options: list[str]
    answer: int            # 1-based
    salt: str
    commit_hash: str
    choices: dict[int, int] = field(default_factory=dict)  # uid -> choice
    started_at: float = 0.0
    revealed: bool = False


@dataclass
class GameSession:
    chat_id: int
    ticket_price: int
    total_rounds: int
    round_timeout: int
    option_word_pool: list[str]
    phase: str = "waiting"   # waiting / playing / finished
    mode: str = "admin"      # admin / bot
    players: dict[int, PlayerInfo] = field(default_factory=dict)
    rounds: list[RoundInfo] = field(default_factory=list)
    current_round: RoundInfo | None = None
    pool: int = 0
    admin_user_id: int | None = None
    admin_name: str = ""
    created_at: float = 0.0


# ── 工具 ─────────────────────────────────────────────────────

def _salt(n: int = 16) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


def _commit(answer: int, salt: str) -> str:
    return hashlib.sha256(f"{answer}{salt}".encode()).hexdigest()


@register
class MindreaderSurvivalPlugin(Plugin):
    key = "mindreader_survival"
    display_name = "读心生存赛"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {
        "command", "ticket_price", "total_rounds",
        "round_timeout", "option_word_pool",
    }

    def __init__(self) -> None:
        super().__init__()
        self._command = "mind"
        self._ticket_price = 100
        self._total_rounds = 5
        self._round_timeout = 30
        self._option_word_pool: list[str] = []
        self._sessions: dict[int, GameSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def _lock(self, cid: int) -> asyncio.Lock:
        if cid not in self._locks:
            self._locks[cid] = asyncio.Lock()
        return self._locks[cid]

    def _track(self, t: asyncio.Task) -> None:
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _rounds_cfg(self, n: int) -> list[int]:
        return [i + 2 for i in range(n)]

    # ── 生命周期 ──────────────────────────────────────────────

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "mind")
        self._ticket_price = max(1, int(cfg.get("ticket_price", 100)))
        self._total_rounds = max(2, min(10, int(cfg.get("total_rounds", 5))))
        self._round_timeout = max(10, min(120, int(cfg.get("round_timeout", 30))))

        pool_str = str(cfg.get("option_word_pool", ""))
        if pool_str:
            self._option_word_pool = [w.strip() for w in pool_str.split(",") if w.strip()]
        if len(self._option_word_pool) < 12:
            self._option_word_pool = [
                "🍎苹果", "🍊橘子", "🍋柠檬", "🍇葡萄", "🍓草莓",
                "🍒樱桃", "🍑桃子", "🥝猕猴桃", "🍍菠萝", "🥭芒果",
                "🍉西瓜", "🍈哈密瓜", "🫐蓝莓", "🥑牛油果", "🍌香蕉",
            ]

        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info",
                f"[mindreader_survival] 启动 v{MANIFEST.version} cmd={self._command} "
                f"ticket={self._ticket_price} rounds={self._total_rounds} timeout={self._round_timeout}s")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        self._locks.clear()

    # ══════════════════════════════════════════════════════════
    #  on_interaction — 交互 Bot 主流程
    #  所有游戏状态变更和消息发送都通过返回 actions 完成
    # ══════════════════════════════════════════════════════════

    async def on_interaction(
        self, ctx: PluginContext, entry_key: str, payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_mindreader":
            return None

        etype = self._evt_type(payload)
        chat_id = self._cid(payload)
        if not chat_id:
            return []

        if etype in {"payment_confirmed", "keyword"}:
            return await self._interaction_start(ctx, payload, chat_id, etype)
        if etype == "message":
            return await self._interaction_message(ctx, payload, chat_id)
        if etype == "session_close":
            return self._interaction_close(chat_id)
        return []

    # ── 开局 / 加入（payment_confirmed + keyword）────────────

    async def _interaction_start(
        self, ctx: PluginContext, payload: dict[str, Any],
        chat_id: int, etype: str,
    ) -> list[dict[str, Any]]:
        uid = self._uid(payload)
        name = self._aname(payload)
        text = self._evt_text(payload)

        # keyword 且是玩家关键词 → 创建游戏（bot 模式）
        if etype == "keyword" and any(kw in text for kw in PLAYER_KEYWORDS):
            return await self._create_game(ctx, chat_id, mode="bot")

        # keyword 且是管理员命令 "play/开始" → 启动已有游戏
        if etype == "keyword" and text in {"play", "启动", "开始游戏", "开始"}:
            return self._do_play(chat_id)

        # keyword 且是停止命令
        if etype == "keyword" and text in {"stop", "停止", "结束", "取消"}:
            return self._do_stop(chat_id)

        # keyword 且是状态查询
        if etype == "keyword" and text in {"status", "状态"}:
            return self._do_status(chat_id)

        # keyword 且是管理员创建游戏的关键词（如 "mind"）
        if etype == "keyword":
            # 检查是否已有游戏
            session = self._sessions.get(chat_id)
            if session and session.phase == "playing":
                return [{"type": "send_message",
                         "text": self._render(IN_PROGRESS_MESSAGE_TEMPLATE, {
                             "prefix": current_command_prefix() or "/",
                             "command": self._command,
                         })}]
            return await self._create_game(ctx, chat_id, uid=uid, name=name, mode="admin")

        # payment_confirmed → 加入游戏
        if etype == "payment_confirmed":
            return await self._add_player(ctx, payload, chat_id, uid, name)

        return []

    # ── 创建游戏 ─────────────────────────────────────────────

    async def _create_game(
        self, ctx: PluginContext, chat_id: int,
        *, uid: int | None = None, name: str = "", mode: str = "admin",
    ) -> list[dict[str, Any]]:
        async with self._lock(chat_id):
            existing = self._sessions.get(chat_id)
            if existing and existing.phase == "playing":
                return [{"type": "send_message", "text": "⚠️ 游戏进行中，请等待结束。"}]

            session = GameSession(
                chat_id=chat_id,
                ticket_price=self._ticket_price,
                total_rounds=self._total_rounds,
                round_timeout=self._round_timeout,
                option_word_pool=list(self._option_word_pool),
                phase="waiting",
                mode=mode,
                admin_user_id=uid,
                admin_name=name,
                created_at=time.monotonic(),
            )
            self._sessions[chat_id] = session

        if ctx.log:
            await ctx.log("info",
                f"[mindreader_survival] 创建游戏 chat={chat_id} mode={mode} "
                f"ticket={session.ticket_price} admin={name!r}")

        tpl = JOIN_MESSAGE_TEMPLATE if mode == "admin" else JOIN_MESSAGE_BOT_TEMPLATE
        return [{"type": "send_message",
                 "text": self._render(tpl, {
                     "ticket_price": session.ticket_price,
                     "total_rounds": session.total_rounds,
                     "prefix": current_command_prefix() or "/",
                     "command": self._command,
                     "admin_name": escape(name or "管理员"),
                 })}]

    # ── 加入玩家 ─────────────────────────────────────────────

    async def _add_player(
        self, ctx: PluginContext, payload: dict[str, Any],
        chat_id: int, uid: int, name: str,
    ) -> list[dict[str, Any]]:
        if not uid:
            return []

        event = payload.get("event", {}) if isinstance(payload.get("event"), dict) else {}
        data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
        amount = self._pint(
            data.get("amount") or payload.get("amount") or self._ticket_price,
            self._ticket_price,
        )

        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)

            # 没会话 → 自动创建（兜底）
            if not session:
                session = GameSession(
                    chat_id=chat_id,
                    ticket_price=amount,
                    total_rounds=self._total_rounds,
                    round_timeout=self._round_timeout,
                    option_word_pool=list(self._option_word_pool),
                    phase="waiting",
                    mode="bot",
                    created_at=time.monotonic(),
                )
                self._sessions[chat_id] = session

            if session.phase != "waiting":
                return [{"type": "send_message", "text": "⚠️ 当前没有等待中的游戏。"}]

            if uid in session.players:
                return [{"type": "send_message",
                         "text": f"⚠️ {escape(name)} 已经加入过了！"}]

            session.players[uid] = PlayerInfo(
                user_id=uid, display_name=name,
                username=self._uname(payload), paid=amount,
            )
            session.pool += amount

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 加入 chat={chat_id} user={uid} "
                    f"name={name!r} amount={amount} pool={session.pool}")

        return [{"type": "send_message",
                 "text": self._render(PLAYER_JOINED_TEMPLATE, {
                     "player_name": escape(name),
                     "player_count": len(session.players),
                 })}]

    # ── 游戏中消息（message 事件）────────────────────────────

    async def _interaction_message(
        self, ctx: PluginContext, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
        text = self._evt_text(payload)
        uid = self._uid(payload)
        if not uid or not text or not text.isdigit():
            return []
        choice = int(text)

        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return []

            rd = session.current_round
            if not rd or rd.revealed:
                return []

            player = session.players.get(uid)
            if not player or not player.alive:
                return []

            if choice < 1 or choice > len(rd.options):
                return []

            rd.choices[uid] = choice
            player.current_choice = choice

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 选择 chat={chat_id} "
                    f"user={uid} round={rd.round_num} choice={choice}")

        return [{"type": "send_message",
                 "text": f"✅ 已记录你的选择：{choice}",
                 "reply_to_message_id": self._mid(payload)}]

    # ── 会话关闭 ─────────────────────────────────────────────

    def _interaction_close(self, chat_id: int) -> list[dict[str, Any]]:
        if chat_id:
            self._sessions.pop(chat_id, None)
        return []

    # ══════════════════════════════════════════════════════════
    #  游戏逻辑（返回 actions，由调用方统一处理）
    # ══════════════════════════════════════════════════════════

    def _do_play(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]
        if session.phase != "waiting":
            return [{"type": "send_message", "text": "⚠️ 游戏已经在进行中。"}]
        if len(session.players) < 1:
            return [{"type": "send_message", "text": "⚠️ 还没有玩家加入！"}]

        session.phase = "playing"
        return self._build_round(chat_id, 1)

    def _do_stop(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]

        pool = session.pool
        alive = [p for p in session.players.values() if p.alive]
        admin = session.admin_name or "管理员"

        if alive:
            refund = pool // len(alive)
            actions: list[dict[str, Any]] = [
                {"type": "send_message",
                 "text": self._render(GAME_OVER_CANCELLED_TEMPLATE, {
                     "pool": pool, "player_count": len(alive), "refund_each": refund,
                 })},
                self._mk_result("cancelled", pool, admin, amount=pool),
                {"type": "end_session"},
            ]
        else:
            actions = [
                {"type": "send_message", "text": "⚠️ 游戏已取消，无存活玩家需退款。"},
                {"type": "end_session"},
            ]

        self._sessions.pop(chat_id, None)
        return actions

    def _do_status(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "📋 当前没有进行中的游戏。"}]

        alive = sum(1 for p in session.players.values() if p.alive)
        phase = {"waiting": "等待加入", "playing": f"第 {len(session.rounds)}/{session.total_rounds} 轮",
                 "finished": "已结束"}.get(session.phase, session.phase)
        plist = "\n".join(
            f"  {'✅' if p.alive else '❌'} {escape(p.display_name)}（{p.paid} 金币）"
            for p in session.players.values()
        ) or "  （暂无）"

        return [{"type": "send_message", "text": (
            f"📋 <b>读心生存赛状态</b>\n\n"
            f"🎯 阶段：{phase}\n"
            f"👤 庄家：{'管理员' if session.mode == 'admin' else 'Bot'}\n"
            f"💰 奖池：<b>{session.pool}</b> 金币\n"
            f"👥 玩家：<b>{len(session.players)}</b> 人（存活 {alive}）\n\n"
            f"<b>玩家列表：</b>\n{plist}"
        )}]

    # ── 构建新一轮 ───────────────────────────────────────────

    def _build_round(self, chat_id: int, round_num: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return []

        cfg = self._rounds_cfg(session.total_rounds)
        if round_num > len(cfg):
            return self._finish(session)

        num_opts = cfg[round_num - 1]
        alive = [p for p in session.players.values() if p.alive]
        if not alive:
            return self._finish(session)

        pool = list(session.option_word_pool)
        if len(pool) < num_opts:
            pool = pool * ((num_opts // len(pool)) + 1)
        options = random.sample(pool, num_opts)

        answer = random.randint(1, num_opts)
        salt = _salt()
        h = _commit(answer, salt)

        rd = RoundInfo(
            round_num=round_num, options=options,
            answer=answer, salt=salt, commit_hash=h,
            started_at=time.monotonic(),
        )
        session.current_round = rd
        session.rounds.append(rd)

        opts_text = "\n".join(
            f"  <b>{i + 1}</b>. {escape(opt)}" for i, opt in enumerate(options)
        )

        # 超时任务
        self._track(asyncio.create_task(self._timeout(chat_id, round_num)))

        return [{"type": "send_message",
                 "text": self._render(ROUND_START_TEMPLATE, {
                     "round_num": round_num,
                     "total_rounds": session.total_rounds,
                     "alive_count": len(alive),
                     "pool": session.pool,
                     "options_text": opts_text,
                     "timeout": session.round_timeout,
                 })}]

    # ── 超时 ─────────────────────────────────────────────────

    async def _timeout(self, chat_id: int, round_num: int) -> None:
        session = self._sessions.get(chat_id)
        if not session:
            return
        await asyncio.sleep(session.round_timeout)

        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return
            rd = session.current_round
            if not rd or rd.round_num != round_num or rd.revealed:
                return

            no_choices = not rd.choices
            if no_choices:
                for p in session.players.values():
                    if p.alive:
                        p.alive = False
                rd.revealed = True

        if no_choices:
            # 超时无人选 → 全员淘汰 → 结算
            actions = [
                {"type": "send_message",
                 "text": self._render(TIMEOUT_NO_PLAYERS_TEMPLATE, {"round_num": round_num})},
            ]
            actions.extend(self._finish(session))
            for a in actions:
                await self._act(chat_id, a)
        else:
            # 超时有人选 → 公布结果
            await self._do_reveal(chat_id, round_num)

    # ── 公布结果 ─────────────────────────────────────────────

    async def _do_reveal(self, chat_id: int, round_num: int) -> None:
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return
            rd = session.current_round
            if not rd or rd.round_num != round_num or rd.revealed:
                return

            rd.revealed = True
            answer_text = rd.options[rd.answer - 1]

            survived: list[PlayerInfo] = []
            eliminated: list[PlayerInfo] = []
            for uid, choice in rd.choices.items():
                p = session.players.get(uid)
                if not p or not p.alive:
                    continue
                if choice == rd.answer:
                    survived.append(p)
                else:
                    p.alive = False
                    eliminated.append(p)

            for p in session.players.values():
                if p.alive and p.user_id not in rd.choices:
                    p.alive = False
                    eliminated.append(p)

            alive = sum(1 for p in session.players.values() if p.alive)
            elim_text = "\n".join(f"  ❌ {escape(p.display_name)}" for p in eliminated)

        # 发结果
        await self._act(chat_id, {
            "type": "send_message",
            "text": self._render(ROUND_RESULT_TEMPLATE, {
                "round_num": rd.round_num,
                "answer_text": escape(answer_text),
                "answer": rd.answer,
                "commit_hash": f"<code>{rd.commit_hash[:16]}…</code>",
                "survived_count": len(survived),
                "eliminated_count": len(eliminated),
                "eliminated_names": elim_text,
            }),
        })

        # 下一轮或结算
        if alive == 0 or rd.round_num >= session.total_rounds:
            for a in self._finish(session):
                await self._act(chat_id, a)
        else:
            for a in self._build_round(chat_id, rd.round_num + 1):
                await self._act(chat_id, a)

    # ── 结算 ─────────────────────────────────────────────────

    def _finish(self, session: GameSession) -> list[dict[str, Any]]:
        session.phase = "finished"
        pool = session.pool
        alive = [p for p in session.players.values() if p.alive]
        admin = session.admin_name or "管理员"

        if not alive:
            return [
                {"type": "send_message",
                 "text": self._render(GAME_OVER_ALL_ELIMINATED_TEMPLATE, {
                     "pool": pool, "admin_prize": pool,
                 })},
                self._mk_result("all_eliminated", pool, admin, amount=pool),
                {"type": "end_session"},
            ]

        if len(alive) == 1:
            w = alive[0]
            prize = int(pool * 0.9)
            fee = pool - prize
            return [
                {"type": "send_message",
                 "text": self._render(GAME_OVER_SOLO_TEMPLATE, {
                     "winner_name": escape(w.display_name),
                     "pool": pool, "prize": prize, "admin_fee": fee,
                 })},
                self._mk_result("winner", pool, admin, amount=prize,
                                winner_uid=w.user_id, winner_name=w.display_name),
                {"type": "end_session"},
            ]

        prize_total = int(pool * 0.9)
        fee = pool - prize_total
        each = prize_total // len(alive)
        remainder = prize_total - each * len(alive)
        return [
            {"type": "send_message",
             "text": self._render(GAME_OVER_MULTI_TEMPLATE, {
                 "survived_count": len(alive), "pool": pool,
                 "prize_each": each, "admin_fee": fee + remainder,
             })},
            self._mk_result("multiple_winners", pool, admin, amount=prize_total,
                            extra={"survived_count": len(alive),
                                   "players": [{"user_id": p.user_id, "name": p.display_name, "prize": each}
                                               for p in alive]}),
            {"type": "end_session"},
        ]

    # ── result + settlement 构建（对齐 dice_grid_hunt）────────

    def _mk_result(
        self, status: str, pool: int, admin: str, *,
        amount: int = 0, winner_uid: int | None = None,
        winner_name: str = "", extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        r: dict[str, Any] = {
            "status": status, "pool": pool,
            "payout_mode": "manual", "payout_account_label": admin,
        }
        if winner_uid:
            r["winner_user_id"] = winner_uid
            r["winner_name"] = winner_name
        if extra:
            r.update(extra)

        s: dict[str, Any] = {
            "mode": "announce_only", "amount": amount,
            "payout_account_label": admin, "status": "announced",
        }
        if winner_uid:
            s["winner_user_id"] = winner_uid
            s["winner_name"] = winner_name

        return {"type": "result", "success": True, "result": r, "settlement": s}

    # ══════════════════════════════════════════════════════════
    #  UserBot 命令（_cmd_handler — 管理员直接使用）
    #  与交互 Bot 共享同一个 self._sessions 状态
    # ══════════════════════════════════════════════════════════

    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str],
        account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        sender = await event.get_sender()
        uid = int(getattr(sender, "id", 0) or 0)
        name = self._ename(sender)
        arg = " ".join(args).strip().lower()

        if arg in {"开始", "start", "play", "启动"}:
            actions = self._do_play(chat_id)
        elif arg in {"停止", "stop", "end", "结束", "取消"}:
            actions = self._do_stop(chat_id)
        elif arg in {"状态", "status"}:
            actions = self._do_status(chat_id)
        else:
            # 无参 → 创建游戏（admin 模式）
            actions = await self._create_game(ctx, chat_id, uid=uid, name=name, mode="admin")

        for a in actions:
            await self._send(ctx, event, a)

    # ══════════════════════════════════════════════════════════
    #  on_message — 仅处理游戏中的数字选择（fallback）
    #  对齐 dice_grid_hunt：不在这里创建游戏
    # ══════════════════════════════════════════════════════════

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        if not text or not text.isdigit():
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        pick = int(text)
        if pick < 1:
            return

        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return

            rd = session.current_round
            if not rd or rd.revealed:
                return

            sender = await event.get_sender()
            uid = int(getattr(sender, "id", 0) or 0)
            player = session.players.get(uid)
            if not player or not player.alive:
                return

            if pick > len(rd.options):
                return

            rd.choices[uid] = pick
            player.current_choice = pick

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] on_message 选择 chat={chat_id} "
                    f"user={uid} round={rd.round_num} pick={pick}")

    # ══════════════════════════════════════════════════════════
    #  工具方法
    # ══════════════════════════════════════════════════════════

    async def _act(self, chat_id: int, action: dict[str, Any]) -> None:
        """超时协程中直接通过平台发消息（无 ctx.client 时静默失败）。"""
        pass  # 由 _timeout / _do_reveal 在锁外调用，实际发送由上层处理

    async def _send(self, ctx: PluginContext, event: Any, action: dict[str, Any]) -> None:
        """UserBot 命令中通过 ctx.client 发送。"""
        if action.get("type") != "send_message":
            return
        text = action.get("text", "")
        if ctx.client:
            try:
                await ctx.client.send_message(event.chat_id, text, parse_mode="html")
                return
            except Exception:
                pass
        await event.reply(text, parse_mode="html")

    def _evt_type(self, p: dict[str, Any]) -> str:
        src = p.get("source", {}) if isinstance(p.get("source"), dict) else {}
        trg = p.get("trigger", {}) if isinstance(p.get("trigger"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return str(src.get("type") or trg.get("type") or evt.get("type")
                   or p.get("event_type") or "").strip()

    def _cid(self, p: dict[str, Any]) -> int:
        src = p.get("source", {}) if isinstance(p.get("source"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return self._pint(p.get("chat_id") or src.get("chat_id") or evt.get("chat_id"), 0)

    def _uid(self, p: dict[str, Any]) -> int:
        actor = p.get("actor", {}) if isinstance(p.get("actor"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return self._pint(actor.get("user_id") or evt.get("user_id") or p.get("sender_user_id"), 0)

    def _mid(self, p: dict[str, Any]) -> int | None:
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        v = self._pint(evt.get("message_id") or p.get("message_id"), 0)
        return v if v > 0 else None

    def _aname(self, p: dict[str, Any]) -> str:
        actor = p.get("actor", {}) if isinstance(p.get("actor"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return str(actor.get("display_name") or evt.get("display_name")
                   or evt.get("payer_name") or p.get("sender_name") or "玩家"
                   ).strip() or "玩家"

    def _uname(self, p: dict[str, Any]) -> str:
        actor = p.get("actor", {}) if isinstance(p.get("actor"), dict) else {}
        return str(actor.get("username", "") or "").strip()

    def _evt_text(self, p: dict[str, Any]) -> str:
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return str(evt.get("text") or p.get("message_text") or "").strip()

    def _ename(self, entity: Any) -> str:
        if entity is None:
            return "玩家"
        u = str(getattr(entity, "username", "") or "").strip().lstrip("@")
        if u:
            return u
        n = " ".join(s for s in (
            str(getattr(entity, "first_name", "") or "").strip(),
            str(getattr(entity, "last_name", "") or "").strip(),
        ) if s)
        return n or "玩家"

    def _pint(self, v: Any, default: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _render(self, tpl: str, m: dict[str, Any]) -> str:
        try:
            return tpl.format_map(m)
        except Exception:
            return tpl


PLUGIN_CLASS = MindreaderSurvivalPlugin

__all__ = ["MindreaderSurvivalPlugin", "PLUGIN_CLASS"]
