"""读心生存赛远程插件。

玩法：
  1. 管理员通过关键词开局，玩家通过转账加入。
  2. 每轮 Bot 随机选一个答案，用 SHA256 commit-reveal 机制。
  3. 玩家在限时内回复数字选择，选错淘汰，选对晋级。
  4. 最终存活者平分 90% 奖池，管理员得 10%。
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
    JOIN_MESSAGE_TEMPLATE,
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
    options: list[str]       # 选项文本
    answer: int              # 正确答案（1-based）
    salt: str
    commit_hash: str
    choices: dict[int, int] = field(default_factory=dict)  # user_id -> choice (1-based)
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
    players: dict[int, PlayerInfo] = field(default_factory=dict)
    rounds: list[RoundInfo] = field(default_factory=list)
    current_round: RoundInfo | None = None
    pool: int = 0
    admin_user_id: int | None = None
    admin_name: str = ""
    created_at: float = 0.0


# ── 工具函数 ──────────────────────────────────────────────────

def _generate_salt(length: int = 16) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(chars) for _ in range(length))


def _compute_commit(answer: int, salt: str) -> str:
    return hashlib.sha256(f"{answer}{salt}".encode()).hexdigest()


def _verify_commit(answer: int, salt: str, commit_hash: str) -> bool:
    return _compute_commit(answer, salt) == commit_hash


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

    def _rounds_config(self, total_rounds: int) -> list[int]:
        """每轮选项数：2, 3, 4, 5, 6 …"""
        return [i + 2 for i in range(total_rounds)]

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
                f"[mindreader_survival] 已启动 v{MANIFEST.version}；"
                f"指令：{self._command}；门票：{self._ticket_price}；"
                f"轮数：{self._total_rounds}；超时：{self._round_timeout}s")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        self._locks.clear()
        if ctx.log:
            await ctx.log("info", "[mindreader_survival] 已停止")

    # ══════════════════════════════════════════════════════════
    #  交互入口（交互 Bot 路由）
    # ══════════════════════════════════════════════════════════

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_mindreader":
            return None

        etype = self._evt_type(payload)
        chat_id = self._cid(payload)

        if etype == "payment_confirmed":
            return await self._on_payment(ctx, payload, chat_id)
        elif etype == "keyword":
            return await self._on_keyword(ctx, payload, chat_id)
        elif etype == "message":
            return await self._on_player_message(ctx, payload, chat_id)
        elif etype == "session_close":
            return self._on_session_close(chat_id)

        return []

    # ── 支付：玩家加入 ───────────────────────────────────────

    async def _on_payment(
        self, ctx: PluginContext, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
        if not chat_id:
            return []

        user_id = self._uid(payload)
        name = self._aname(payload)
        if not user_id:
            return []

        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "waiting":
                return [{"type": "send_message", "text": "⚠️ 当前没有等待中的游戏。"}]

            if user_id in session.players:
                return [{"type": "send_message",
                         "text": f"⚠️ {escape(name)} 已经加入过了！"}]

            session.players[user_id] = PlayerInfo(
                user_id=user_id, display_name=name,
                username=self._uname(payload), paid=session.ticket_price,
            )
            session.pool += session.ticket_price

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 加入 chat={chat_id} user={user_id} "
                    f"name={name!r} pool={session.pool}")

        return [{"type": "send_message",
                 "text": self._r(PLAYER_JOINED_TEMPLATE, {
                     "player_name": escape(name),
                     "player_count": len(session.players),
                 })}]

    # ── 关键词：管理员命令 ───────────────────────────────────

    async def _on_keyword(
        self, ctx: PluginContext, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
        if not chat_id:
            return []

        text = self._evt_text(payload)
        uid = self._uid(payload)
        name = self._aname(payload)

        if text in {COMMAND_START, "start", "开局"}:
            return await self._cmd_create_game(ctx, payload, chat_id, uid, name)
        if text in {"play", "启动", "开始游戏"}:
            return await self._cmd_play(ctx, chat_id)
        if text in {COMMAND_STOP, "stop", "结束", "取消"}:
            return await self._cmd_stop(ctx, chat_id)
        if text in {COMMAND_STATUS, "status", "状态"}:
            return self._build_status(chat_id)

        # 其他关键词 → 显示加入提示（如果有等待中的游戏）
        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if session and session.phase == "waiting":
                return [{"type": "send_message",
                         "text": self._r(JOIN_MESSAGE_TEMPLATE, {
                             "ticket_price": session.ticket_price,
                             "total_rounds": session.total_rounds,
                             "prefix": current_command_prefix(),
                             "command": self._command,
                         })}]
        return []

    # ── 关键词子命令：创建游戏 ───────────────────────────────

    async def _cmd_create_game(
        self, ctx: PluginContext, payload: dict[str, Any],
        chat_id: int, uid: int | None, name: str,
    ) -> list[dict[str, Any]]:
        ticket = self._pint(payload.get("ticket_price"), self._ticket_price)
        rounds = self._pint(payload.get("total_rounds"), self._total_rounds)
        timeout = self._pint(payload.get("round_timeout"), self._round_timeout)

        async with self._get_lock(chat_id):
            existing = self._sessions.get(chat_id)
            if existing and existing.phase == "playing":
                return [{"type": "send_message",
                         "text": "⚠️ 游戏进行中，请等待当前游戏结束。"}]

            session = GameSession(
                chat_id=chat_id, ticket_price=ticket,
                total_rounds=rounds, round_timeout=timeout,
                option_word_pool=list(self._option_word_pool),
                phase="waiting", admin_user_id=uid,
                admin_name=name, created_at=time.monotonic(),
            )
            self._sessions[chat_id] = session

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 创建 chat={chat_id} "
                    f"ticket={ticket} rounds={rounds} timeout={timeout}")

        return [{"type": "send_message",
                 "text": self._r(JOIN_MESSAGE_TEMPLATE, {
                     "ticket_price": ticket,
                     "total_rounds": rounds,
                     "prefix": current_command_prefix(),
                     "command": self._command,
                 })}]

    # ── 关键词子命令：开始游戏 ───────────────────────────────

    async def _cmd_play(self, ctx: PluginContext, chat_id: int) -> list[dict[str, Any]]:
        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
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

        return await self._start_round(ctx, chat_id, 1)

    # ── 开始新一轮 ───────────────────────────────────────────

    async def _start_round(
        self, ctx: PluginContext, chat_id: int, round_num: int,
    ) -> list[dict[str, Any]]:
        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return []

            cfg = self._rounds_config(session.total_rounds)
            if round_num > len(cfg):
                return await self._finish_game(ctx, chat_id)

            num_opts = cfg[round_num - 1]
            alive = [p for p in session.players.values() if p.alive]
            if not alive:
                return await self._finish_game(ctx, chat_id)

            # 随机选选项
            pool = list(session.option_word_pool)
            if len(pool) < num_opts:
                pool = pool * ((num_opts // len(pool)) + 1)
            options = random.sample(pool, num_opts)

            # 随机答案 + commit
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

            # 构建选项文本
            opts_text = "\n".join(
                f"  <b>{i + 1}</b>. {escape(opt)}"
                for i, opt in enumerate(options)
            )

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 第 {round_num} 轮 chat={chat_id} "
                    f"opts={num_opts} answer={answer} alive={len(alive)}")

        # 启动超时（通过 ctx.client 直接发消息）
        task = asyncio.create_task(
            self._round_timeout_task(ctx, chat_id, round_num)
        )
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

    # ── 超时任务（独立协程，直接发消息）──────────────────────

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
                # 无人选择 → 全员淘汰
                for p in session.players.values():
                    if p.alive:
                        p.alive = False
                rd.revealed = True

                if ctx.log:
                    await ctx.log("info",
                        f"[mindreader_survival] 第 {round_num} 轮超时无人选择 chat={chat_id}")

        if no_choices:
            # 发超时消息并结算
            await self._send_html(ctx, chat_id,
                self._r(TIMEOUT_NO_PLAYERS_TEMPLATE, {"round_num": round_num}))
            await self._do_finish(ctx, chat_id)
        else:
            # 有人选择但超时到了 → 公布结果
            await self._do_reveal(ctx, chat_id, round_num)

    # ── 玩家选择（message 事件）───────────────────────────────

    async def _on_player_message(
        self, ctx: PluginContext, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
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

    # ── 会话关闭 ─────────────────────────────────────────────

    def _on_session_close(self, chat_id: int) -> list[dict[str, Any]]:
        if chat_id:
            self._sessions.pop(chat_id, None)
        return []

    # ══════════════════════════════════════════════════════════
    #  UserBot 命令（管理员直接使用）
    # ══════════════════════════════════════════════════════════

    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(
            getattr(event.chat_id, "channel_id", None) or event.chat_id or 0
        )
        if not chat_id:
            return

        sender = await event.get_sender()
        uid = int(getattr(sender, "id", 0) or 0)
        name = self._ename(sender)
        arg = " ".join(args).strip().lower()

        if arg in {"开始", "start", "play", "启动", "开始游戏"}:
            actions = await self._cmd_play(ctx, chat_id)
        elif arg in {"停止", "stop", "end", "结束", "取消"}:
            actions = await self._cmd_stop(ctx, chat_id)
        elif arg in {"状态", "status"}:
            actions = self._build_status(chat_id)
        else:
            # 无参 / 开局
            payload = {
                "event": {"text": COMMAND_START, "chat_id": chat_id},
                "actor": {"user_id": uid, "display_name": name},
            }
            actions = await self._cmd_create_game(ctx, payload, chat_id, uid, name)

        for a in actions:
            await self._send_action(ctx, event, a)

    # ── 停止游戏 ─────────────────────────────────────────────

    async def _cmd_stop(self, ctx: PluginContext, chat_id: int) -> list[dict[str, Any]]:
        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session:
                return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]

            pool = session.pool
            alive = [p for p in session.players.values() if p.alive]

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 停止 chat={chat_id} pool={pool} alive={len(alive)}")

        if alive:
            refund = pool // len(alive)
            actions = [
                {"type": "send_message",
                 "text": self._r(GAME_OVER_CANCELLED_TEMPLATE, {
                     "pool": pool, "player_count": len(alive), "refund_each": refund,
                 })},
                {"type": "result", "success": False,
                 "result": {"status": "cancelled", "pool": pool,
                            "player_count": len(alive), "refund_each": refund},
                 "settlement": {"mode": "announce_only", "amount": pool, "status": "refunded"}},
                {"type": "end_session"},
            ]
        else:
            actions = [
                {"type": "send_message", "text": "⚠️ 游戏已取消，无存活玩家需退款。"},
                {"type": "end_session"},
            ]

        self._sessions.pop(chat_id, None)
        return actions

    # ── 状态查询 ─────────────────────────────────────────────

    def _build_status(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "📋 当前没有进行中的游戏。"}]

        alive = [p for p in session.players.values() if p.alive]
        phase = {"waiting": "等待加入", "playing": f"第 {len(session.rounds)} / {session.total_rounds} 轮",
                 "finished": "已结束"}.get(session.phase, session.phase)
        plist = "\n".join(
            f"  {'✅' if p.alive else '❌'} {escape(p.display_name)}（{p.paid} 金币）"
            for p in session.players.values()
        ) or "  （暂无）"

        return [{"type": "send_message", "text": (
            f"📋 <b>读心生存赛状态</b>\n\n"
            f"🎯 阶段：{phase}\n"
            f"💰 奖池：<b>{session.pool}</b> 金币\n"
            f"👥 玩家：<b>{len(session.players)}</b> 人（存活 {len(alive)}）\n"
            f"🎫 门票：<b>{session.ticket_price}</b> 金币\n\n"
            f"<b>玩家列表：</b>\n{plist}"
        )}]

    # ══════════════════════════════════════════════════════════
    #  公布结果 + 结算（独立协程 & 交互回调共用）
    # ══════════════════════════════════════════════════════════

    async def _do_reveal(self, ctx: PluginContext, chat_id: int, round_num: int) -> None:
        """公布第 round_num 轮结果，必要时结算。"""
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
                player = session.players.get(uid)
                if not player or not player.alive:
                    continue
                if choice == rd.answer:
                    survived.append(player)
                else:
                    player.alive = False
                    eliminated.append(player)

            # 没做选择的存活玩家也淘汰
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

        # 发结果消息
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
            # 下一轮
            actions = await self._start_round(ctx, chat_id, rd.round_num + 1)
            for a in actions:
                await self._send_html(ctx, chat_id, a.get("text", ""))

    async def _do_finish(self, ctx: PluginContext, chat_id: int) -> None:
        """结算游戏，直接发消息。"""
        async with self._get_lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session:
                return
            session.phase = "finished"
            pool = session.pool
            alive = [p for p in session.players.values() if p.alive]

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
        reply_to = action.get("reply_to_message_id")
        if ctx.client:
            try:
                await ctx.client.send_message(
                    event.chat_id, text, parse_mode="html",
                    reply_to=reply_to,
                )
                return
            except Exception:
                pass
        await event.reply(text, parse_mode="html")

    # ── payload 解析 ─────────────────────────────────────────

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
        return self._pint(actor.get("user_id") or evt.get("user_id")
                          or p.get("sender_user_id"), 0)

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
