"""读心生存赛远程插件。

两种触发模式：
  - 管理员触发：管理员发指令，原地编辑为游戏消息，结算通过管理员发放
  - 玩家触发：  玩家发关键词，bot 发送游戏消息，结算通过管理员发放
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
    phase: str = "waiting"          # waiting / playing / finished
    mode: str = "admin"             # admin / bot
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
                f"[mindreader_survival] 已启动 v{MANIFEST.version}；"
                f"指令：{self._command}；门票：{self._ticket_price}；"
                f"轮数：{self._total_rounds}；超时：{self._round_timeout}s")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        self._locks.clear()

    # ══════════════════════════════════════════════════════════
    #  on_message — 监听管理员指令 & 玩家关键词
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

        # ── 管理员指令：{prefix}mind ──
        cmd_with_prefix = f"{prefix}{self._command}" if prefix else self._command
        if text == cmd_with_prefix or text == self._command:
            await self._admin_trigger(ctx, event, chat_id, uid, name)
            return

        # ── 玩家关键词 ──
        if any(kw in text for kw in PLAYER_KEYWORDS):
            await self._player_trigger(ctx, event, chat_id, uid, name)
            return

        # ── 游戏中：数字选择 ──
        if text.isdigit():
            await self._handle_choice(ctx, event, chat_id, uid, int(text))

    # ── 管理员触发：原地编辑 ─────────────────────────────────

    async def _admin_trigger(
        self, ctx: PluginContext, event: Any,
        chat_id: int, uid: int, name: str,
    ) -> None:
        async with self._get_lock(chat_id):
            existing = self._sessions.get(chat_id)
            if existing and existing.phase == "playing":
                await event.reply("⚠️ 游戏进行中，请等待结束。", parse_mode="html")
                return

            session = GameSession(
                chat_id=chat_id,
                ticket_price=self._ticket_price,
                total_rounds=self._total_rounds,
                round_timeout=self._round_timeout,
                option_word_pool=list(self._option_word_pool),
                phase="waiting",
                mode="admin",
                admin_user_id=uid,
                admin_name=name,
                created_at=time.monotonic(),
            )
            self._sessions[chat_id] = session

        # 原地编辑为游戏模板
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

        if ctx.log:
            await ctx.log("info",
                f"[mindreader_survival] 管理员开局 chat={chat_id} admin={name!r} "
                f"ticket={session.ticket_price}")

    # ── 玩家触发：bot 发新消息 ───────────────────────────────

    async def _player_trigger(
        self, ctx: PluginContext, event: Any,
        chat_id: int, uid: int, name: str,
    ) -> None:
        async with self._get_lock(chat_id):
            existing = self._sessions.get(chat_id)
            if existing and existing.phase != "finished":
                return  # 已有游戏，忽略

            session = GameSession(
                chat_id=chat_id,
                ticket_price=self._ticket_price,
                total_rounds=self._total_rounds,
                round_timeout=self._round_timeout,
                option_word_pool=list(self._option_word_pool),
                phase="waiting",
                mode="bot",
                created_at=time.monotonic(),
            )
            self._sessions[chat_id] = session

        text = self._r(JOIN_MESSAGE_BOT_TEMPLATE, {
            "ticket_price": session.ticket_price,
            "total_rounds": session.total_rounds,
            "prefix": current_command_prefix() or "/",
            "command": self._command,
        })
        await event.reply(text, parse_mode="html")

        if ctx.log:
            await ctx.log("info",
                f"[mindreader_survival] 玩家触发开局 chat={chat_id} "
                f"ticket={session.ticket_price}")

    # ══════════════════════════════════════════════════════════
    #  on_interaction — 交互 Bot 路由
    # ══════════════════════════════════════════════════════════

    async def on_interaction(
        self, ctx: PluginContext, entry_key: str, payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_mindreader":
            return None

        etype = self._evt_type(payload)
        chat_id = self._cid(payload)

        if etype == "payment_confirmed":
            return self._on_payment(payload, chat_id)
        elif etype == "keyword":
            return self._on_keyword(ctx, payload, chat_id)
        elif etype == "message":
            return await self._on_game_message(ctx, payload, chat_id)
        elif etype == "session_close":
            return self._on_session_close(chat_id)
        return []

    # ── 支付：玩家加入 ───────────────────────────────────────

    def _on_payment(
        self, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
        if not chat_id:
            return []

        uid = self._uid(payload)
        name = self._aname(payload)
        if not uid:
            return []

        event = payload.get("event", {}) if isinstance(payload.get("event"), dict) else {}
        data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
        amount = self._pint(data.get("amount") or payload.get("amount") or self._ticket_price, self._ticket_price)

        session = self._sessions.get(chat_id)

        # 没会话 → 自动创建（管理员通过 on_message 开局但交互 bot 无会话的场景）
        if not session:
            session = GameSession(
                chat_id=chat_id,
                ticket_price=amount,
                total_rounds=self._total_rounds,
                round_timeout=self._round_timeout,
                option_word_pool=list(self._option_word_pool),
                phase="waiting",
                mode="bot",  # 兜底默认 bot 模式
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

        # bot 模式：bot 回复通知
        if session.mode == "bot":
            return [{"type": "send_message",
                     "text": self._r(PLAYER_JOINED_TEMPLATE, {
                         "player_name": escape(name),
                         "player_count": len(session.players),
                     })}]
        # admin 模式：不自动回复，管理员手动确认
        return []

    # ── 关键词：开始 / 停止 ──────────────────────────────────

    def _on_keyword(
        self, ctx: PluginContext, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
        if not chat_id:
            return []
        text = self._evt_text(payload)

        if text in {"play", "启动", "开始游戏", "开始"}:
            return self._start_game(chat_id)
        if text in {COMMAND_STOP, "stop", "结束", "取消"}:
            return self._stop_game(chat_id)
        return []

    def _start_game(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]
        if session.phase != "waiting":
            return [{"type": "send_message", "text": "⚠️ 游戏已经在进行中。"}]
        if len(session.players) < 1:
            return [{"type": "send_message", "text": "⚠️ 还没有玩家加入！"}]

        session.phase = "playing"
        return self._build_round(chat_id, 1)

    def _stop_game(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "⚠️ 没有进行中的游戏。"}]

        pool = session.pool
        alive = [p for p in session.players.values() if p.alive]
        admin_name = session.admin_name or "管理员"

        if alive:
            refund = pool // len(alive)
            actions = [
                {"type": "send_message",
                 "text": self._r(GAME_OVER_CANCELLED_TEMPLATE, {
                     "pool": pool, "player_count": len(alive), "refund_each": refund,
                 })},
                self._build_result_action(
                    status="cancelled", pool=pool,
                    payout_account=admin_name, amount=pool,
                    extra={"player_count": len(alive), "refund_each": refund},
                ),
                {"type": "end_session"},
            ]
        else:
            actions = [
                {"type": "send_message", "text": "⚠️ 游戏已取消，无存活玩家需退款。"},
                {"type": "end_session"},
            ]

        self._sessions.pop(chat_id, None)
        return actions

    # ── 游戏中：数字选择（交互 Bot message 事件）──────────────

    async def _on_game_message(
        self, ctx: PluginContext, payload: dict[str, Any], chat_id: int,
    ) -> list[dict[str, Any]]:
        if not chat_id:
            return []

        text = self._evt_text(payload)
        uid = self._uid(payload)
        if not uid or not text or not text.isdigit():
            return []
        choice = int(text)

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

        return [{"type": "send_message",
                 "text": f"✅ 已记录你的选择：{choice}",
                 "reply_to_message_id": self._mid(payload)}]

    def _on_session_close(self, chat_id: int) -> list[dict[str, Any]]:
        if chat_id:
            self._sessions.pop(chat_id, None)
        return []

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
            actions = self._start_game(chat_id)
        elif arg in {"停止", "stop", "end", "结束", "取消"}:
            actions = self._stop_game(chat_id)
        elif arg in {"状态", "status"}:
            actions = self._build_status(chat_id)
        else:
            # 无参 → 原地编辑开局
            await self._admin_trigger(ctx, event, chat_id, uid, name)
            return

        for a in actions:
            await self._send_action(ctx, event, a)

    # ══════════════════════════════════════════════════════════
    #  游戏逻辑
    # ══════════════════════════════════════════════════════════

    def _build_round(self, chat_id: int, round_num: int) -> list[dict[str, Any]]:
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
        task = asyncio.create_task(self._round_timeout(chat_id, round_num))
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

    async def _round_timeout(self, chat_id: int, round_num: int) -> None:
        session = self._sessions.get(chat_id)
        if not session:
            return
        await asyncio.sleep(session.round_timeout)

        session = self._sessions.get(chat_id)
        if not session or session.phase != "playing":
            return
        rd = session.current_round
        if not rd or rd.round_num != round_num or rd.revealed:
            return

        # 超时强制公布
        actions = self._reveal_round(session, rd)
        for a in actions:
            if a.get("type") == "send_message":
                await self._send_html(chat_id, a["text"], session)

    def _reveal_round(self, session: GameSession, rd: RoundInfo) -> list[dict[str, Any]]:
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

        actions: list[dict[str, Any]] = [
            {"type": "send_message",
             "text": self._r(ROUND_RESULT_TEMPLATE, {
                 "round_num": rd.round_num,
                 "answer_text": escape(answer_text),
                 "answer": rd.answer,
                 "commit_hash": f"<code>{rd.commit_hash[:16]}…</code>",
                 "survived_count": len(survived),
                 "eliminated_count": len(eliminated),
                 "eliminated_names": elim_names,
             })},
        ]

        if alive_count == 0 or rd.round_num >= session.total_rounds:
            actions.extend(self._finish_game(session))
        else:
            actions.extend(self._build_round(session.chat_id, rd.round_num + 1))

        return actions

    # ── 结算 ─────────────────────────────────────────────────

    def _finish_game(self, session: GameSession) -> list[dict[str, Any]]:
        session.phase = "finished"
        pool = session.pool
        alive = [p for p in session.players.values() if p.alive]
        admin_name = session.admin_name or "管理员"

        if not alive:
            return [
                {"type": "send_message",
                 "text": self._r(GAME_OVER_ALL_ELIMINATED_TEMPLATE, {
                     "pool": pool, "admin_prize": pool,
                 })},
                self._build_result_action(
                    status="all_eliminated", pool=pool,
                    payout_account=admin_name, amount=pool,
                ),
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
                self._build_result_action(
                    status="winner", pool=pool,
                    payout_account=admin_name, amount=prize,
                    winner_user_id=w.user_id, winner_name=w.display_name,
                ),
                {"type": "end_session"},
            ]

        # 多人存活
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
            self._build_result_action(
                status="multiple_winners", pool=pool,
                payout_account=admin_name, amount=prize_total,
                extra={
                    "survived_count": len(alive),
                    "survived_players": [
                        {"user_id": p.user_id, "name": p.display_name, "prize": each}
                        for p in alive
                    ],
                },
            ),
            {"type": "end_session"},
        ]

    # ── 构建 result + settlement 动作 ────────────────────────

    def _build_result_action(
        self,
        *,
        status: str,
        pool: int,
        payout_account: str,
        amount: int,
        winner_user_id: int | None = None,
        winner_name: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """对齐 dice_grid_hunt 的 result + settlement 结构。"""
        result: dict[str, Any] = {
            "status": status,
            "pool": pool,
            "payout_mode": "manual",
            "payout_account_label": payout_account,
        }
        if winner_user_id:
            result["winner_user_id"] = winner_user_id
            result["winner_name"] = winner_name
        if extra:
            result.update(extra)

        settlement: dict[str, Any] = {
            "mode": "announce_only",
            "amount": amount,
            "payout_account_label": payout_account,
            "status": "announced",
        }
        if winner_user_id:
            settlement["winner_user_id"] = winner_user_id
            settlement["winner_name"] = winner_name

        return {
            "type": "result",
            "success": True,
            "result": result,
            "settlement": settlement,
        }

    # ── 状态查询 ─────────────────────────────────────────────

    def _build_status(self, chat_id: int) -> list[dict[str, Any]]:
        session = self._sessions.get(chat_id)
        if not session:
            return [{"type": "send_message", "text": "📋 当前没有进行中的游戏。"}]

        alive = [p for p in session.players.values() if p.alive]
        phase = {"waiting": "等待加入", "playing": f"第 {len(session.rounds)} / {session.total_rounds} 轮",
                 "finished": "已结束"}.get(session.phase, session.phase)
        mode = "管理员" if session.mode == "admin" else "Bot"
        plist = "\n".join(
            f"  {'✅' if p.alive else '❌'} {escape(p.display_name)}（{p.paid} 金币）"
            for p in session.players.values()
        ) or "  （暂无）"

        return [{"type": "send_message", "text": (
            f"📋 <b>读心生存赛状态</b>\n\n"
            f"🎯 阶段：{phase}\n"
            f"👤 庄家：{mode}\n"
            f"💰 奖池：<b>{session.pool}</b> 金币\n"
            f"👥 玩家：<b>{len(session.players)}</b> 人（存活 {len(alive)}）\n"
            f"🎫 门票：<b>{session.ticket_price}</b> 金币\n\n"
            f"<b>玩家列表：</b>\n{plist}"
        )}]

    # ══════════════════════════════════════════════════════════
    #  工具方法
    # ══════════════════════════════════════════════════════════

    async def _send_html(self, chat_id: int, text: str, session: GameSession) -> None:
        """超时任务中直接发消息（需要 ctx.client，此处用 None 兜底）。"""
        pass  # 由 _reveal_round 返回 actions，外层统一发送

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
