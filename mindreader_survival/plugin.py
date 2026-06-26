"""读心生存赛远程插件。

严格遵守开发指南的双通道架构：
  - on_message：处理管理员命令 + 玩家关键词 + 数字选择（UserBot 通道）
  - on_interaction：处理 payment_confirmed + keyword + message（交互 Bot 通道）
  - 两条路调同一份业务函数，共享 self._sessions
  - 玩家可以免费加入（关键词）或付费加入（转账），均可选
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
    answer: int
    salt: str
    commit_hash: str
    choices: dict[int, int] = field(default_factory=dict)
    started_at: float = 0.0
    revealed: bool = False


@dataclass
class GameSession:
    chat_id: int
    ticket_price: int
    total_rounds: int
    round_timeout: int
    option_word_pool: list[str]
    phase: str = "waiting"
    mode: str = "admin"
    players: dict[int, PlayerInfo] = field(default_factory=dict)
    rounds: list[RoundInfo] = field(default_factory=list)
    current_round: RoundInfo | None = None
    pool: int = 0
    admin_user_id: int | None = None
    admin_name: str = ""
    created_at: float = 0.0


def _salt(n: int = 16) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


def _commit(answer: int, salt: str) -> str:
    return hashlib.sha256(f"{answer}{salt}".encode()).hexdigest()


@register
class MindreaderSurvivalPlugin(Plugin):
    key = "mindreader_survival"
    display_name = "读心生存赛"
    message_channels = {"incoming", "outgoing"}
    owner_only = False  # 允许普通成员消息进入 on_message
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
        self._ctx: PluginContext | None = None

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
        self._ctx = ctx
        if ctx.log:
            await ctx.log("info",
                f"[mindreader_survival] 启动 v{MANIFEST.version}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        self._locks.clear()

    # ══════════════════════════════════════════════════════════
    #  业务函数（共享，两条通道都调同一份）
    # ══════════════════════════════════════════════════════════

    async def biz_create_game(
        self, ctx: PluginContext, chat_id: int,
        *, uid: int | None = None, name: str = "", mode: str = "admin",
    ) -> list[dict[str, Any]]:
        """创建游戏会话。admin 模式或 bot 模式。"""
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
                f"ticket={session.ticket_price}")

        tpl = JOIN_MESSAGE_TEMPLATE if mode == "admin" else JOIN_MESSAGE_BOT_TEMPLATE
        return [{"type": "send_message",
                 "text": self._r(tpl, {
                     "ticket_price": session.ticket_price,
                     "total_rounds": session.total_rounds,
                     "prefix": current_command_prefix() or "/",
                     "command": self._command,
                     "admin_name": escape(name or "管理员"),
                 })}]

    async def biz_add_player(
        self, ctx: PluginContext, chat_id: int,
        uid: int, name: str, amount: int = 0,
        username: str = "",
    ) -> list[dict[str, Any]]:
        """加入玩家。amount=0 表示免费加入（关键词），>0 表示付费加入（转账）。"""
        if not uid:
            return []

        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "waiting":
                return [{"type": "send_message", "text": "⚠️ 当前没有等待中的游戏。"}]

            if uid in session.players:
                return [{"type": "send_message",
                         "text": f"⚠️ {escape(name)} 已经加入过了！"}]

            paid = amount or 0
            session.players[uid] = PlayerInfo(
                user_id=uid, display_name=name,
                username=username, paid=paid,
            )
            session.pool += paid

            if ctx.log:
                await ctx.log("info",
                    f"[mindreader_survival] 加入 chat={chat_id} user={uid} "
                    f"name={name!r} paid={paid} pool={session.pool}")

        return [{"type": "send_message",
                 "text": self._r(PLAYER_JOINED_TEMPLATE, {
                     "player_name": escape(name),
                     "player_count": len(session.players),
                 })}]

    def biz_start_game(self, chat_id: int) -> list[dict[str, Any]]:
        """开始游戏。"""
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]
        if session.phase != "waiting":
            return [{"type": "send_message", "text": "⚠️ 游戏已经在进行中。"}]
        if len(session.players) < 1:
            return [{"type": "send_message", "text": "⚠️ 还没有玩家加入！"}]

        session.phase = "playing"
        return self.biz_build_round(chat_id, 1)

    def biz_stop_game(self, chat_id: int) -> list[dict[str, Any]]:
        """停止游戏。"""
        session = self._sessions.get(chat_id)
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

    def biz_record_choice(
        self, chat_id: int, uid: int, choice: int,
    ) -> tuple[bool, str]:
        """记录玩家选择。返回 (ok, message)。"""
        session = self._sessions.get(chat_id)
        if not session or session.phase != "playing":
            return False, ""

        rd = session.current_round
        if not rd or rd.revealed:
            return False, ""

        player = session.players.get(uid)
        if not player or not player.alive:
            return False, ""

        if choice < 1 or choice > len(rd.options):
            return False, ""

        rd.choices[uid] = choice
        player.current_choice = choice
        return True, f"✅ 已记录你的选择：{choice}"

    def biz_build_round(self, chat_id: int, round_num: int) -> list[dict[str, Any]]:
        """构建新一轮题目。"""
        session = self._sessions.get(chat_id)
        if not session:
            return []

        cfg = self._rounds_cfg(session.total_rounds)
        if round_num > len(cfg):
            return self.biz_finish(session)

        num_opts = cfg[round_num - 1]
        alive = [p for p in session.players.values() if p.alive]
        if not alive:
            return self.biz_finish(session)

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

        # 启动超时任务
        if self._ctx:
            self._track(asyncio.create_task(
                self._timeout_task(self._ctx, chat_id, round_num)))

        return [{"type": "send_message",
                 "text": self._r(ROUND_START_TEMPLATE, {
                     "round_num": round_num,
                     "total_rounds": session.total_rounds,
                     "alive_count": len(alive),
                     "pool": session.pool,
                     "options_text": opts_text,
                     "timeout": session.round_timeout,
                 })}]

    def biz_finish(self, session: GameSession) -> list[dict[str, Any]]:
        """结算游戏。"""
        session.phase = "finished"
        pool = session.pool
        alive = [p for p in session.players.values() if p.alive]
        admin = session.admin_name or "管理员"

        if not alive:
            return [
                {"type": "send_message",
                 "text": self._r(GAME_OVER_ALL_ELIMINATED_TEMPLATE, {
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
                 "text": self._r(GAME_OVER_SOLO_TEMPLATE, {
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
             "text": self._r(GAME_OVER_MULTI_TEMPLATE, {
                 "survived_count": len(alive), "pool": pool,
                 "prize_each": each, "admin_fee": fee + remainder,
             })},
            self._mk_result("multiple_winners", pool, admin, amount=prize_total,
                            extra={"survived_count": len(alive),
                                   "players": [{"user_id": p.user_id, "name": p.display_name, "prize": each}
                                               for p in alive]}),
            {"type": "end_session"},
        ]

    def _mk_result(self, status, pool, admin, *, amount=0, winner_uid=None, winner_name="", extra=None):
        r = {"status": status, "pool": pool, "payout_mode": "manual", "payout_account_label": admin}
        if winner_uid:
            r["winner_user_id"] = winner_uid
            r["winner_name"] = winner_name
        if extra:
            r.update(extra)
        s = {"mode": "announce_only", "amount": amount, "payout_account_label": admin, "status": "announced"}
        if winner_uid:
            s["winner_user_id"] = winner_uid
            s["winner_name"] = winner_name
        return {"type": "result", "success": True, "result": r, "settlement": s}

    def biz_status(self, chat_id: int) -> list[dict[str, Any]]:
        """查询状态。"""
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "📋 当前没有进行中的游戏。"}]

        alive = sum(1 for p in session.players.values() if p.alive)
        phase = {"waiting": "等待加入", "playing": f"第 {len(session.rounds)}/{session.total_rounds} 轮",
                 "finished": "已结束"}.get(session.phase, session.phase)
        plist = "\n".join(
            f"  {'✅' if p.alive else '❌'} {escape(p.display_name)}（{'免费' if p.paid == 0 else f'{p.paid} 金币'}）"
            for p in session.players.values()
        ) or "  （暂无）"

        return [{"type": "send_message", "text": (
            f"📋 <b>读心生存赛</b>\n\n"
            f"🎯 阶段：{phase}\n"
            f"👤 庄家：{'管理员' if session.mode == 'admin' else 'Bot'}\n"
            f"💰 奖池：<b>{session.pool}</b> 金币\n"
            f"👥 玩家：<b>{len(session.players)}</b> 人（存活 {alive}）\n\n"
            f"<b>玩家列表：</b>\n{plist}"
        )}]

    # ── 超时任务 ─────────────────────────────────────────────

    async def _timeout_task(self, ctx: PluginContext, chat_id: int, round_num: int) -> None:
        session = self._sessions.get(chat_id)
        if not session:
            return
        await asyncio.sleep(session.round_timeout)

        no_choices = False
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
            await self._send_ctx(ctx, chat_id,
                self._r(TIMEOUT_NO_PLAYERS_TEMPLATE, {"round_num": round_num}))
            for a in self.biz_finish(session):
                await self._send_ctx_action(ctx, chat_id, a)
        else:
            await self._do_reveal(ctx, chat_id, round_num)

    async def _do_reveal(self, ctx: PluginContext, chat_id: int, round_num: int) -> None:
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if not session or session.phase != "playing":
                return
            rd = session.current_round
            if not rd or rd.round_num != round_num or rd.revealed:
                return
            rd.revealed = True
            answer_text = rd.options[rd.answer - 1]

            survived, eliminated = [], []
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

        await self._send_ctx(ctx, chat_id, self._r(ROUND_RESULT_TEMPLATE, {
            "round_num": rd.round_num,
            "answer_text": escape(answer_text),
            "answer": rd.answer,
            "commit_hash": f"<code>{rd.commit_hash[:16]}…</code>",
            "survived_count": len(survived),
            "eliminated_count": len(eliminated),
            "eliminated_names": elim_text,
        }))

        if alive == 0 or rd.round_num >= session.total_rounds:
            for a in self.biz_finish(session):
                await self._send_ctx_action(ctx, chat_id, a)
        else:
            for a in self.biz_build_round(chat_id, rd.round_num + 1):
                await self._send_ctx_action(ctx, chat_id, a)

    # ══════════════════════════════════════════════════════════
    #  on_interaction — 交互 Bot 通道
    #  开发指南：交互入口是新增触发面，不是替代品
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
            return await self._ib_start(ctx, payload, chat_id, etype)
        if etype == "message":
            return self._ib_message(ctx, payload, chat_id)
        if etype == "session_close":
            return self._ib_close(chat_id)
        return []

    async def _ib_start(
        self, ctx: PluginContext, payload: dict[str, Any],
        chat_id: int, etype: str,
    ) -> list[dict[str, Any]]:
        """交互 Bot 的 keyword / payment_confirmed 入口。"""
        uid = self._uid(payload)
        name = self._aname(payload)
        text = self._evt_text(payload)

        # 玩家关键词 → 创建游戏（bot 模式）或加入
        if etype == "keyword" and any(kw in text for kw in PLAYER_KEYWORDS):
            session = self._sessions.get(chat_id)
            if session and session.phase != "finished":
                return await self.biz_add_player(ctx, chat_id, uid, name)
            return await self.biz_create_game(ctx, chat_id, mode="bot")

        # 管理员命令
        if etype == "keyword":
            if text in {"play", "启动", "开始游戏", "开始"}:
                return self.biz_start_game(chat_id)
            if text in {"stop", "停止", "结束", "取消"}:
                return self.biz_stop_game(chat_id)
            if text in {"status", "状态"}:
                return self.biz_status(chat_id)
            # 默认：创建游戏
            session = self._sessions.get(chat_id)
            if session and session.phase == "playing":
                return [{"type": "send_message",
                         "text": self._r(IN_PROGRESS_MESSAGE_TEMPLATE, {
                             "prefix": current_command_prefix() or "/",
                             "command": self._command,
                         })}]
            return await self.biz_create_game(ctx, chat_id, uid=uid, name=name, mode="admin")

        # 付费加入
        if etype == "payment_confirmed":
            event = payload.get("event", {}) if isinstance(payload.get("event"), dict) else {}
            data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
            amount = self._pint(data.get("amount") or payload.get("amount") or self._ticket_price, self._ticket_price)
            return await self.biz_add_player(ctx, chat_id, uid, name, amount=amount, username=self._uname(payload))

        return []

    def _ib_message(self, ctx: PluginContext, payload: dict[str, Any], chat_id: int) -> list[dict[str, Any]]:
        """交互 Bot 的 message 入口：数字选择。"""
        text = self._evt_text(payload)
        uid = self._uid(payload)
        if not uid or not text or not text.isdigit():
            return []
        choice = int(text)
        ok, msg = self.biz_record_choice(chat_id, uid, choice)
        if ok:
            return [{"type": "send_message", "text": msg,
                     "reply_to_message_id": self._mid(payload)}]
        return []

    def _ib_close(self, chat_id: int) -> list[dict[str, Any]]:
        if chat_id:
            self._sessions.pop(chat_id, None)
        return []

    # ══════════════════════════════════════════════════════════
    #  on_message — UserBot 通道
    #  开发指南：原有 on_message 语义必须保持不变
    #  处理：管理员命令 + 玩家关键词 + 数字选择
    # ══════════════════════════════════════════════════════════

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        if not text:
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        sender = await event.get_sender()
        uid = int(getattr(sender, "id", 0) or 0)
        name = self._ename(sender)
        prefix = current_command_prefix()

        # 管理员指令 {prefix}mind
        cmd_prefix = f"{prefix}{self._command}" if prefix else self._command
        if text == cmd_prefix or text == self._command:
            session = self._sessions.get(chat_id)
            if session and session.phase == "playing":
                await event.reply(self._r(IN_PROGRESS_MESSAGE_TEMPLATE, {
                    "prefix": prefix or "/", "command": self._command,
                }), parse_mode="html")
                return
            actions = await self.biz_create_game(ctx, chat_id, uid=uid, name=name, mode="admin")
            for a in actions:
                await self._send_action(ctx, event, a)
            return

        # 玩家关键词
        if any(kw in text for kw in PLAYER_KEYWORDS):
            session = self._sessions.get(chat_id)
            if session and session.phase != "finished":
                # 已有游戏 → 免费加入
                actions = await self.biz_add_player(ctx, chat_id, uid, name, amount=0)
                for a in actions:
                    await self._send_action(ctx, event, a)
            else:
                # 没游戏 → 创建游戏（bot 模式）
                actions = await self.biz_create_game(ctx, chat_id, mode="bot")
                for a in actions:
                    await self._send_action(ctx, event, a)
            return

        # 数字选择（游戏进行中）
        if text.isdigit():
            choice = int(text)
            if choice >= 1:
                ok, msg = self.biz_record_choice(chat_id, uid, choice)
                if ok:
                    await event.reply(msg, parse_mode="html")

    # ══════════════════════════════════════════════════════════
    #  _cmd_handler — UserBot 命令（管理员直接使用）
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
            actions = self.biz_start_game(chat_id)
        elif arg in {"停止", "stop", "end", "结束", "取消"}:
            actions = self.biz_stop_game(chat_id)
        elif arg in {"状态", "status"}:
            actions = self.biz_status(chat_id)
        else:
            actions = await self.biz_create_game(ctx, chat_id, uid=uid, name=name, mode="admin")

        for a in actions:
            await self._send_action(ctx, event, a)

    # ══════════════════════════════════════════════════════════
    #  工具方法
    # ══════════════════════════════════════════════════════════

    async def _send_ctx(self, ctx: PluginContext, chat_id: int, text: str) -> None:
        if ctx.client:
            try:
                await ctx.client.send_message(chat_id, text, parse_mode="html")
            except Exception:
                pass

    async def _send_ctx_action(self, ctx: PluginContext, chat_id: int, action: dict[str, Any]) -> None:
        if action.get("type") == "send_message":
            await self._send_ctx(ctx, chat_id, action.get("text", ""))

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

    def _evt_type(self, p):
        src = p.get("source", {}) if isinstance(p.get("source"), dict) else {}
        trg = p.get("trigger", {}) if isinstance(p.get("trigger"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return str(src.get("type") or trg.get("type") or evt.get("type") or p.get("event_type") or "").strip()

    def _cid(self, p):
        src = p.get("source", {}) if isinstance(p.get("source"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return self._pint(p.get("chat_id") or src.get("chat_id") or evt.get("chat_id"), 0)

    def _uid(self, p):
        actor = p.get("actor", {}) if isinstance(p.get("actor"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return self._pint(actor.get("user_id") or evt.get("user_id") or p.get("sender_user_id"), 0)

    def _mid(self, p):
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        v = self._pint(evt.get("message_id") or p.get("message_id"), 0)
        return v if v > 0 else None

    def _aname(self, p):
        actor = p.get("actor", {}) if isinstance(p.get("actor"), dict) else {}
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return str(actor.get("display_name") or evt.get("display_name") or evt.get("payer_name") or p.get("sender_name") or "玩家").strip() or "玩家"

    def _uname(self, p):
        actor = p.get("actor", {}) if isinstance(p.get("actor"), dict) else {}
        return str(actor.get("username", "") or "").strip()

    def _evt_text(self, p):
        evt = p.get("event", {}) if isinstance(p.get("event"), dict) else {}
        return str(evt.get("text") or p.get("message_text") or "").strip()

    def _ename(self, entity):
        if entity is None:
            return "玩家"
        u = str(getattr(entity, "username", "") or "").strip().lstrip("@")
        if u:
            return u
        n = " ".join(s for s in (str(getattr(entity, "first_name", "") or "").strip(),
                                  str(getattr(entity, "last_name", "") or "").strip()) if s)
        return n or "玩家"

    def _pint(self, v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _r(self, tpl, m):
        try:
            return tpl.format_map(m)
        except Exception:
            return tpl


PLUGIN_CLASS = MindreaderSurvivalPlugin

__all__ = ["MindreaderSurvivalPlugin", "PLUGIN_CLASS"]
