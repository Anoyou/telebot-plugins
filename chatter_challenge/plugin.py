"""话痨挑战远程插件。

群内设定聊天规则，违反者自动扣分。全程被动监听，不需要打命令。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register


def _has_emoji(text: str) -> bool:
    """检测文本中是否含有 emoji。"""
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("So",):  # Symbol, other
            return True
        # 常见 emoji 范围
        cp = ord(ch)
        if 0x1F600 <= cp <= 0x1F64F:  # emoticons
            return True
        if 0x1F300 <= cp <= 0x1F5FF:  # misc symbols
            return True
        if 0x1F680 <= cp <= 0x1F6FF:  # transport
            return True
        if 0x1F900 <= cp <= 0x1F9FF:  # supplemental
            return True
        if 0x2600 <= cp <= 0x26FF:  # misc symbols
            return True
        if 0x2700 <= cp <= 0x27BF:  # dingbats
            return True
        if 0xFE00 <= cp <= 0xFE0F:  # variation selectors
            return True
        if 0x200D <= cp <= 0x200D:  # ZWJ
            return True
    return False


@dataclass
class ChallengeState:
    """一局话痨挑战的状态。"""
    rules: list[dict[str, Any]] = field(default_factory=list)
    scores: dict[int, dict[str, Any]] = field(default_factory=dict)  # user_id -> {name, score, violations}
    started_by: int = 0
    active: bool = True

    def record_violation(self, user_id: int, user_name: str, rule_desc: str) -> str:
        """记录一次违规，返回违规说明。"""
        if user_id not in self.scores:
            self.scores[user_id] = {"name": user_name, "score": 0, "violations": []}
        self.scores[user_id]["score"] -= 1
        self.scores[user_id]["violations"].append(rule_desc)
        return rule_desc

    def check_message(self, text: str, user_id: int) -> list[str]:
        """检查消息是否违反规则，返回违规描述列表。"""
        violations = []
        for rule in self.rules:
            rtype = rule["type"]
            if rtype == "max_chars":
                max_n = rule["value"]
                # 排除空格和标点计算
                clean = text.replace(" ", "")
                if len(clean) > max_n:
                    violations.append(f"超过{max_n}字限制（你发了{len(clean)}字）")
            elif rtype == "min_chars":
                min_n = rule["value"]
                clean = text.replace(" ", "")
                if len(clean) < min_n:
                    violations.append(f"少于{min_n}字要求（你只发了{len(clean)}字）")
            elif rtype == "banned_word":
                word = rule["value"]
                if word in text:
                    violations.append(f"说了禁词「{word}」")
            elif rtype == "no_emoji":
                if _has_emoji(text):
                    violations.append("发了表情")
        return violations


# ─────────────────────────────────────────────────────
# 插件
# ─────────────────────────────────────────────────────
@register
class ChatterChallengePlugin(Plugin):
    key = "chatter_challenge"
    display_name = "话痨挑战"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "chat"
        self._challenges: dict[int, ChallengeState] = {}  # chat_id -> state

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "chat")
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[chatter_challenge] 已启动，指令：{self._command}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._challenges.clear()
        if ctx.log:
            await ctx.log("info", "[chatter_challenge] 已停止")

    # ── 命令入口 ─────────────────────────────────────
    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        sender = await event.get_sender()
        sender_id = int(getattr(sender, "id", 0) or 0)
        sender_name = getattr(sender, "first_name", "") or "玩家"

        arg_str = " ".join(args).strip()

        # 结束挑战
        if arg_str in ("结束", "end", "stop", "结算"):
            return await self._end_challenge(chat_id, sender_id, event)

        # 查看当前规则
        if arg_str in ("规则", "rules", "状态", "status"):
            return await self._show_status(chat_id, event)

        # 查看排行榜
        if arg_str in ("排行", "ranking", "board", "分数"):
            return await self._show_ranking(chat_id, event)

        # 添加规则
        ch = self._challenges.get(chat_id)
        if not ch or not ch.active:
            # 没有进行中的挑战，创建一个新的
            ch = ChallengeState(started_by=sender_id)
            self._challenges[chat_id] = ch

        # 解析规则
        rule = self._parse_rule(arg_str)
        if rule:
            # 检查是否已存在同类规则
            for existing in ch.rules:
                if existing["type"] == rule["type"] and existing.get("value") == rule.get("value"):
                    await event.reply(f"⚠️ 这条规则已经存在了~", parse_mode="html")
                    return
            ch.rules.append(rule)
            desc = self._describe_rule(rule)
            await event.reply(
                f"✅ 已添加规则：{desc}\n\n"
                f"当前共 {len(ch.rules)} 条规则。输入 ,{self._command} 规则 查看全部\n"
                f"输入 ,{self._command} 结束 结束挑战并结算",
                parse_mode="html",
            )
            return

        # 没匹配到规则，显示帮助
        await event.reply(
            f"<b>🗣 话痨挑战</b>\n\n"
            f"<b>添加规则：</b>\n"
            f"  ,{self._command} 5字 — 每条消息最多5字\n"
            f"  ,{self._command} 至少3字 — 每条消息至少3字\n"
            f"  ,{self._command} 不许说的 — 禁止说「的」\n"
            f"  ,{self._command} 禁止表情 — 不许发表情\n\n"
            f"<b>其他命令：</b>\n"
            f"  ,{self._command} 规则 — 查看当前规则\n"
            f"  ,{self._command} 排行 — 查看分数排行\n"
            f"  ,{self._command} 结束 — 结束并结算",
            parse_mode="html",
        )

    def _parse_rule(self, text: str) -> dict[str, Any] | None:
        """解析用户输入的规则文本。"""
        text = text.strip()

        # N字 (最多N字)
        m = re.match(r"^(\d+)字$", text)
        if m:
            return {"type": "max_chars", "value": int(m.group(1)), "label": f"最多{m.group(1)}字"}

        # 至少N字
        m = re.match(r"^至少(\d+)字$", text)
        if m:
            return {"type": "min_chars", "value": int(m.group(1)), "label": f"至少{m.group(1)}字"}

        # 不许说X / 禁止X / 不准X
        m = re.match(r"^(?:不许说|禁止|不准|不能说|不让说)(.+)$", text)
        if m:
            word = m.group(1).strip()
            if word in ("表情", "emoji", "表情包"):
                return {"type": "no_emoji", "value": True, "label": "禁止表情"}
            if word:
                return {"type": "banned_word", "value": word, "label": f"禁词「{word}」"}

        # 禁止表情 / 不许表情
        if text in ("禁止表情", "不许表情", "不准表情", "不能发表情", "no emoji"):
            return {"type": "no_emoji", "value": True, "label": "禁止表情"}

        return None

    def _describe_rule(self, rule: dict[str, Any]) -> str:
        return rule.get("label", str(rule))

    # ── 结束挑战 ─────────────────────────────────────
    async def _end_challenge(self, chat_id: int, sender_id: int, event: Any) -> None:
        ch = self._challenges.get(chat_id)
        if not ch or not ch.active:
            await event.reply("没有进行中的话痨挑战~", parse_mode="html")
            return

        ch.active = False

        if not ch.scores:
            await event.reply(
                f"<b>🗣 话痨挑战结束！</b>\n\n"
                f"没人违规，大家都很棒！🎉\n"
                f"共 {len(ch.rules)} 条规则",
                parse_mode="html",
            )
            self._challenges.pop(chat_id, None)
            return

        # 排名：按分数降序（违规少的排前面）
        ranked = sorted(ch.scores.items(), key=lambda x: x[1]["score"], reverse=True)

        lines = ["<b>🗣 话痨挑战结算！</b>\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, info) in enumerate(ranked):
            medal = medals[i] if i < 3 else f"#{i + 1}"
            v_count = len(info["violations"])
            lines.append(f"{medal} {info['name']}：{info['score']} 分（{v_count} 次违规）")

        lines.append(f"\n共 {len(ch.rules)} 条规则，{len(ranked)} 人参与")
        await event.reply("\n".join(lines), parse_mode="html")
        self._challenges.pop(chat_id, None)

    # ── 查看状态 ─────────────────────────────────────
    async def _show_status(self, chat_id: int, event: Any) -> None:
        ch = self._challenges.get(chat_id)
        if not ch or not ch.active:
            await event.reply("没有进行中的话痨挑战。输入 ,chat 开始~", parse_mode="html")
            return

        rules_text = "\n".join(f"  • {self._describe_rule(r)}" for r in ch.rules)
        await event.reply(
            f"<b>🗣 当前挑战规则：</b>\n{rules_text}\n\n"
            f"输入 ,{self._command} 结束 结束并结算",
            parse_mode="html",
        )

    # ── 排行榜 ───────────────────────────────────────
    async def _show_ranking(self, chat_id: int, event: Any) -> None:
        ch = self._challenges.get(chat_id)
        if not ch:
            await event.reply("没有进行中的话痨挑战~", parse_mode="html")
            return

        if not ch.scores:
            await event.reply("暂时还没有人违规~", parse_mode="html")
            return

        ranked = sorted(ch.scores.items(), key=lambda x: x[1]["score"], reverse=True)
        lines = ["<b>📊 当前排行：</b>\n"]
        for i, (uid, info) in enumerate(ranked):
            v_count = len(info["violations"])
            lines.append(f"  {i + 1}. {info['name']}：{info['score']} 分（{v_count} 次违规）")
        await event.reply("\n".join(lines), parse_mode="html")

    # ── 被动监听 ─────────────────────────────────────
    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        if not text:
            return
        # 忽略命令
        if text.startswith(",") or text.startswith("/"):
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        ch = self._challenges.get(chat_id)
        if not ch or not ch.active:
            return

        sender = await event.get_sender()
        if not sender:
            return
        sender_id = int(getattr(sender, "id", 0) or 0)
        sender_name = getattr(sender, "first_name", "") or "玩家"

        # 跳过 bot 自己
        me = await event.client.get_me() if event.client else None
        if me and sender_id == int(getattr(me, "id", 0) or 0):
            return

        violations = ch.check_message(text, sender_id)
        if not violations:
            return

        # 记录违规
        desc = violations[0]  # 一次只报第一个违规
        ch.record_violation(sender_id, sender_name, desc)

        score = ch.scores[sender_id]["score"]
        v_count = len(ch.scores[sender_id]["violations"])

        await event.reply(
            f"🚨 {sender_name} {desc}！\n"
            f"当前分数：{score}（第 {v_count} 次违规）",
            parse_mode="html",
        )


PLUGIN_CLASS = ChatterChallengePlugin

__all__ = ["ChatterChallengePlugin", "PLUGIN_CLASS"]
