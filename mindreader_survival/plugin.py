"""读心生存赛远程插件。

参考 dice_grid_hunt 架构：
  - 开局完全通过交互 Bot 的 on_interaction 处理
  - on_message 仅处理 UserBot 通道下的数字答题补充
  - 超时任务通过 ctx.client 直接发消息
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
    COMMAND_START,
    COMMAND_STOP,
    COMMAND_STATUS,
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
    answer: int
    salt: str
    commit_hash: str
    choices: dict[int, int] = field(default_factory=dict)  # uid -> choice (1-based)
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


# ── 工具函数 ──────────────────────────────────────────────────

def _generate_salt(length: int = 16) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(length))


def _compute_commit(answer: int, salt: str) -> str:
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

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def _track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _rounds_config(self, n: int) -> list[int]:
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
                f"[mindreader_survival] 已启动 v{MANIFEST.version}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        self._locks.clear()

    # ══════════════════════════════════════════════════════════
    #  on_interaction — 交互 Bot 主通道（参考 dice_grid_hunt）
    # ══════════════════════════════════════════════════════════

    async def on_interaction(
        self, ctx: PluginContext, entry_key: str, payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_mindreader":
            return None

        etype = self._evt_type(payload)

        if etype in {"payment_confirmed", "keyword"}:
            return await self._interaction_start(ctx, payload)
        if etype == "message":
            return await self._interaction_answer(ctx, payload)
        if etype == "session_close":
            return await self._interaction_close(payload)
        return []

    # ── 开局 / 加入（payment_confirmed / keyword）─────────────

    async def _interaction_start(
        self, ctx: PluginContext, payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chat_id = self._cid(payload)
        if not chat_id:
            return [{"type": "no_session"}]

        etype = self._evt_type(payload)
        uid = self._uid(payload)
        name = self._aname(payload)

        event = payload.get("event", {}) if isinstance(payload.get("event"), dict) else {}
        data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
        text = self._evt_text(payload)
        amount = self._pint(
            data.get("amount") or payload.get("amount") or self._ticket_price,
            self._ticket_price,
        )

        # 判断是管理员命令还是玩家关键词还是转账
        is_admin_cmd = text in {COMMAND_START, "start", "开局"}
        is_play_cmd = text in {"play", "启动", "开始游戏", "开始"}
        is_stop_cmd = text in {COMMAND_STOP, "stop", "结束", "取消"}
        is_player_kw = any(kw in text for kw in PLAYER_KEYWORDS)
        is_payment = etype == "payment_confirmed"

        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)

            # ── 管理员命令：创建游戏 ──
            if is_admin_cmd:
                if session and session.phase == "playing":
                    return [{"type": "send_message", "text": "⚠️ 游戏进行中，请等待结束。"}]
                session = self._create_session(
                    chat_id, mode="admin",
                    admin_uid=uid, admin_name=name,
                    ticket=amount,
                )
                self._sessions[chat_id] = session
                if ctx.log:
                    await ctx.log("info",
                        f"[mindreader_survival] 管理员开局 chat={chat_id} admin={name!r}")
                return [{"type": "send_message",
                         "text": self._r(JOIN_MESSAGE_TEMPLATE, {
                             "ticket_price": session.ticket_price,
                             "total_rounds": session.total_rounds,
                             "prefix": current_command_prefix() or "/",
                             "command": self._command,
                             "admin_name": escape(name),
                         })}]

            # ── 管理员：开始游戏 ──
            if is_play_cmd:
                if not session:
                    return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]
                if session.phase != "waiting":
                    return [{"type": "send_message", "text": "⚠️ 游戏已经在进行中。"}]
                if len(session.players) < 1:
                    return [{"type": "send_message", "text": "⚠️ 还没有玩家加入！"}]
                session.phase = "playing"
                if ctx.log:
                    await ctx.log("info",
                        f"[mindreader_survival] 游戏开始 chat={chat_id} "
                        f"players={len(session.players)} pool={session.pool}")
                return self._build_round(ctx, chat_id, 1)

            # ── 管理员：停止 ──
            if is_stop_cmd:
                return self._do_stop(ctx, chat_id, session)

            # ── 玩家关键词：创建游戏（bot 模式）──
            if is_player_kw:
                if session and session.phase != "finished":
                    return []  # 已有游戏
                session = self._create_session(chat_id, mode="bot", ticket=self._ticket_price)
                self._sessions[chat_id] = session
                if ctx.log:
                    await ctx.log("info",
                        f"[mindreader_survival] 玩家触发开局 chat={chat_id}")
                return [{"type": "send_message",
                         "text": self._r(JOIN_MESSAGE_BOT_TEMPLATE, {
                             "ticket_price": session.ticket_price,
                             "total_rounds": session.total_rounds,
                             "prefix": current_command_prefix() or "/",
                             "command": self._command,
                         })}]

            # ── 转账：玩家加入 ──
            if is_payment:
                if not session or session.phase != "waiting":
                    # 没会话 → 自动创建 bot 模式
                    session = self._create_session(chat_id, mode="bot", ticket=amount)
                    self._sessions[chat_id] = session
                    if ctx.log:
                        await ctx.log("info",
                            f"[mindreader_survival] 自动创建会话 chat={chat_id}（转账触发）")

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

                # bot 模式自动回复；admin 模式不回复（管理员手动确认）
                if session.mode == "bot":
                    return [{"type": "send_message",
                             "text": self._r(PLAYER_JOINED_TEMPLATE, {
                                 "player_name": escape(name),
                                 "player_count": len(session.players),
                             })}]
                return []

        return []

    # ── 答题（message 事件）───────────────────────────────────

    async def _interaction_answer(
        self, ctx: PluginContext, payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chat_id = self._cid(payload)
        if not chat_id:
            return []

        text = self._evt_text(payload)
        uid = self._uid(payload)
        if not uid or not text or not text.isdigit():
            return []
        choice = int(text)

        async with self._get_lock(chat_id):
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

    # ── 会话关闭 ──────────────────────────────────────────────

    async def _interaction_close(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        chat_id = self._cid(payload)
        if chat_id:
            self._sessions.pop(chat_id, None)
        return []

    # ══════════════════════════════════════════════════════════
    #  on_message — 仅处理 UserBot 通道下的数字答题
    #  参考 dice_grid_hunt：不拦截关键词，不创建游戏
    # ══════════════════════════════════════════════════════════

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        prefix = current_command_prefix()
        # 跳过命令格式
        if not text or text.startswith("/") or (prefix and text.startswith(prefix)):
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        # 仅处理已有游戏中的数字选择
        session = self._sessions.get(chat_id)
        if not session or session.phase != "playing":
            return
        if not text.isdigit():
            return

        choice = int(text)
        if choice < 1:
            return

        sender = await event.get_sender()
        uid = int(getattr(sender, "id", 0) or 0)

        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return
            rd = session.current_round
            if not rd or rd.revealed:
                return
            player = session.players.get(uid)
            if not player or not player.alive:
                return
            if choice < 1 or choice > len(rd.options):
                return

            rd.choices[uid] = choice
            player.current_choice = choice

        await event.reply(f"✅ 已记录你的选择：{choice}", parse_mode="html")

    # ══════════════════════════════════════════════════════════
    #  UserBot 命令（管理员直接使用）
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
            async with self._get_lock(chat_id):
                session = self._sessions.get(chat_id)
                if not session or session.phase != "waiting":
                    await event.reply("⚠️ 没有等待中的游戏。", parse_mode="html")
                    return
                if len(session.players) < 1:
                    await event.reply("⚠️ 还没有玩家加入！", parse_mode="html")
                    return
                session.phase = "playing"
            actions = self._build_round(ctx, chat_id, 1)
            for a in actions:
                await self._send_action(ctx, event, a)

        elif arg in {"停止", "stop", "end", "结束", "取消"}:
            async with self._get_lock(chat_id):
                session = self._sessions.get(chat_id)
            actions = self._do_stop(ctx, chat_id, session)
            for a in actions:
                await self._send_action(ctx, event, a)

        elif arg in {"状态", "status"}:
            actions = self._build_status(chat_id)
            for a in actions:
                await self._send_action(ctx, event, a)

        else:
            # 无参：创建游戏（admin 模式）
            async with self._get_lock(chat_id):
                existing = self._sessions.get(chat_id)
                if existing and existing.phase == "playing":
                    await event.reply("⚠️ 游戏进行中，请等待结束。", parse_mode="html")
                    return
                session = self._create_session(
                    chat_id, mode="admin",
                    admin_uid=uid, admin_name=name,
                )
                self._sessions[chat_id] = session

            text = self._r(JOIN_MESSAGE_TEMPLATE, {
                "ticket_price": session.ticket_price,
                "total_rounds": session.total_rounds,
                "prefix": current_command_prefix() or "/",
                "command": self._command,
                "admin_name": escape(name),
            })
            try:
                await ctx.client.edit_message(chat_id, event.id, text, parse_mode="html")
            except Exception:
                await event.reply(text, parse_mode="html")

    # ══════════════════════════════════════════════════════════
    #  游戏逻辑
    # ══════════════════════════════════════════════════════════

    def _create_session(
        self, chat_id: int, *, mode: str = "admin",
        admin_uid: int | None = None, admin_name: str = "",
        ticket: int | None = None,
    ) -> GameSession:
        return GameSession(
            chat_id=chat_id,
            ticket_price=ticket or self._ticket_price,
            total_rounds=self._total_rounds,
            round_timeout=self._round_timeout,
            option_word_pool=list(self._option_word_pool),
            phase="waiting",
            mode=mode,
            admin_user_id=admin_uid,
            admin_name=admin_name,
            created_at=time.monotonic(),
        )

    def _build_round(
        self, ctx: PluginContext, chat_id: int, round_num: int,
    ) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return []

        cfg = self._rounds_config(session.total_rounds)
        if round_num > len(cfg):
            return self._finish_game(session)

        num_opts = cfg[round_num - 1]
        alive = [p for p in session.players.values() if p.alive]
        if not alive:
            return self._finish_game(session)

        pool = list(session.option_word_pool)
        if len(pool) < num_opts:
            pool = pool * ((num_opts // len(pool)) + 1)
        options = random.sample(pool, num_opts)

        answer = random.randint(1, num_opts)
        salt = _generate_salt()
        commit_hash = _compute_commit(answer, salt)

        rd = RoundInfo(
            round_num=round_num, options=options,
            answer=answer, salt=salt, commit_hash=commit_hash,
            started_at=time.monotonic(),
        )
        session.current_round = rd
        session.rounds.append(rd)

        opts_text = "\n".join(
            f"  <b>{i + 1}</b>. {escape(opt)}" for i, opt in enumerate(options)
        )

        # 启动超时任务
        task = asyncio.create_task(self._round_timeout_task(ctx, chat_id, round_num))
        self._track_task(task)

        return [{"type": "send_message",
                 "text": self._r(ROUND_START_TEMPLATE, {
                     "round_num": round_num,
                     "total_rounds": session.total_rounds,
                     "alive_count": len(alive),
                     "pool": session.pool,
                     "options_text": opts_text,
                     "timeout": session.round_timeout,
                 })}]

    # ── 超时任务（通过 ctx.client 直接发消息，参考 dice_grid_hunt）──

    async def _round_timeout_task(
        self, ctx: PluginContext, chat_id: int, round_num: int,
    ) -> None:
        session = self._sessions.get(chat_id)
        if not session:
            return
        await asyncio.sleep(session.round_timeout)

        async with self._get_lock(chat_id):
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

                if ctx.log:
                    await ctx.log("info",
                        f"[mindreader_survival] 第 {round_num} 轮超时无人选择 chat={chat_id}")

        if no_choices:
            await self._send_html(ctx, chat_id,
                self._r(TIMEOUT_NO_PLAYERS_TEMPLATE, {"round_num": round_num}))
            await self._do_finish(ctx, chat_id)
        else:
            await self._do_reveal(ctx, chat_id, round_num)

    # ── 公布结果 ──────────────────────────────────────────────

    async def _do_reveal(self, ctx: PluginContext, chat_id: int, round_num: int) -> None:
        async with self._get_lock(chat_id):
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

            alive_count = sum(1 for p in session.players.values() if p.alive)
            elim_names = "\n".join(f"  ❌ {escape(p.display_name)}" for p in eliminated)

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 第 {rd.round_num} 轮结果 chat={chat_id} "
                    f"answer={rd.answer}({answer_text}) "
                    f"survived={len(survived)} eliminated={len(eliminated)} alive={alive_count}")

        await self._send_html(ctx, chat_id, self._r(ROUND_RESULT_TEMPLATE, {
            "round_num": rd.round_num,
            "answer_text": escape(answer_text),
            "answer": rd.answer,
            "commit_hash": f"<code>{rd.commit_hash[:16]}…</code>",
            "survived_count": len(survived),
            "eliminated_count": len(eliminated),
            "eliminated_names": elim_names,
        }))

        if alive_count == 0 or rd.round_num >= session.total_rounds:
            await self._do_finish(ctx, chat_id)
        else:
            actions = self._build_round(ctx, chat_id, rd.round_num + 1)
            for a in actions:
                if a.get("type") == "send_message":
                    await self._send_html(ctx, chat_id, a["text"])

    # ── 结算（通过 ctx.client 直接发消息）────────────────────

    async def _do_finish(self, ctx: PluginContext, chat_id: int) -> None:
        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session:
                return
            session.phase = "finished"
            pool = session.pool
            alive = [p for p in session.players.values() if p.alive]
            admin_name = session.admin_name or "管理员"

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 结算 chat={chat_id} "
                    f"pool={pool} alive={len(alive)} total={len(session.players)}")

        if not alive:
            await self._send_html(ctx, chat_id,
                self._r(GAME_OVER_ALL_ELIMINATED_TEMPLATE, {
                    "pool": pool, "admin_prize": pool,
                }))
        elif len(alive) == 1:
            w = alive[0]
            prize = int(pool * 0.9)
            fee = pool - prize
            await self._send_html(ctx, chat_id,
                self._r(GAME_OVER_SOLO_TEMPLATE, {
                    "winner_name": escape(w.display_name),
                    "pool": pool, "prize": prize, "admin_fee": fee,
                }))
        else:
            prize_total = int(pool * 0.9)
            fee = pool - prize_total
            each = prize_total // len(alive)
            remainder = prize_total - each * len(alive)
            await self._send_html(ctx, chat_id,
                self._r(GAME_OVER_MULTI_TEMPLATE, {
                    "survived_count": len(alive), "pool": pool,
                    "prize_each": each, "admin_fee": fee + remainder,
                }))

        self._sessions.pop(chat_id, None)

    # ── 停止 ─────────────────────────────────────────────────

    def _do_stop(
        self, ctx: PluginContext, chat_id: int,
        session: GameSession | None,
    ) -> list[dict[str, Any]]:
        if not session:
            return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]

        pool = session.pool
        alive = [p for p in session.players.values() if p.alive]
        self._sessions.pop(chat_id, None)

        if alive:
            refund = pool // len(alive)
            return [
                {"type": "send_message",
                 "text": self._r(GAME_OVER_CANCELLED_TEMPLATE, {
                     "pool": pool, "player_count": len(alive), "refund_each": refund,
                 })},
                {"type": "end_session"},
            ]
        return [
            {"type": "send_message", "text": "⚠️ 游戏已取消。"},
            {"type": "end_session"},
        ]

    # ── 状态查询 ──────────────────────────────────────────────

    def _build_status(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "📋 当前没有进行中的游戏。"}]

        alive = [p for p in session.players.values() if p.alive]
        phase_map = {"waiting": "等待加入", "playing": f"第 {len(session.rounds)}/{session.total_rounds} 轮",
                     "finished": "已结束"}
        plist = "\n".join(
            f"  {'✅' if p.alive else '❌'} {escape(p.display_name)}（{p.paid} 金币）"
            for p in session.players.values()
        ) or "  （暂无）"

        return [{"type": "send_message", "text": (
            f"📋 <b>读心生存赛</b>\n\n"
            f"🎯 {phase_map.get(session.phase, session.phase)}\n"
            f"👤 庄家：{'管理员' if session.mode == 'admin' else 'Bot'}\n"
            f"💰 奖池：<b>{session.pool}</b> 金币\n"
            f"👥 <b>{len(session.players)}</b> 人（存活 {len(alive)}）\n\n"
            f"{plist}"
        )}]

    # ══════════════════════════════════════════════════════════
    #  工具方法
    # ══════════════════════════════════════════════════════════

    async def _send_html(self, ctx: PluginContext, chat_id: int, text: str) -> None:
        if ctx.client:
            try:
                await ctx.client.send_message(chat_id, text, parse_mode="html")
                return
            except Exception:
                pass

    async def _send_action(self, ctx: PluginContext, event: Any, action: dict[str, Any]) -> None:
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

    def _r(self, tpl: str, m: dict[str, Any]) -> str:
        try:
            return tpl.format_map(m)
        except Exception:
            return tpl


PLUGIN_CLASS = MindreaderSurvivalPlugin

__all__ = ["MindreaderSurvivalPlugin", "PLUGIN_CLASS"]
