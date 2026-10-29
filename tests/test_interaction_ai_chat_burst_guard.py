"""interaction-ai-chat 0.4.12：群关键词触发的口令保护与每群每分钟上限。"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

import test_interaction_ai_chat as base

PLUGIN = base.PLUGIN
CHAT_A = -100777
CHAT_B = -100888


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0


class TTLStorage(base.FakeStorage):
    """带过期时间的 ctx.storage 模拟：incr 每次刷新 TTL（与 PluginStorage 一致）。"""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock = clock
        self.expires: dict[str, float] = {}

    def _expire(self, key: str) -> None:
        exp = self.expires.get(key)
        if exp is not None and exp <= self.clock.now:
            self.data.pop(key, None)
            self.expires.pop(key, None)

    async def get(self, key, default=None):
        self._expire(key)
        return await super().get(key, default)

    async def set(self, key, value, *, ttl=None):
        if ttl is not None:
            self.expires[key] = self.clock.now + float(ttl)
        return await super().set(key, value, ttl=ttl)

    async def incr(self, key, amount=1, *, ttl=None):
        self._expire(key)
        if ttl is not None:
            self.expires[key] = self.clock.now + float(ttl)
        return await super().incr(key, amount, ttl=ttl)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    base._install_stubs()
    clock = Clock()
    monkeypatch.setattr(PLUGIN, "BURST_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(PLUGIN, "_minute_bucket", lambda: int(clock.now // 60))
    return clock


def _ctx(clock: Clock, config=None, storage=None) -> base.Ctx:
    return base.Ctx(config={"trigger_keywords": "问", **(config or {})}, storage=storage or TTLStorage(clock))


def _send(plugin, ctx, text, *, user_id, chat_id=CHAT_A, chat_type="supergroup", **kwargs):
    payload = base.message_payload(text, chat_id=chat_id, chat_type=chat_type, user_id=user_id, **kwargs)
    return base.run(plugin.on_event(ctx, payload))


def _burst_logs(ctx):
    return [log for log in ctx.logs if "识别到群口令" in log[1]]


def _rate_logs(ctx):
    return [log for log in ctx.logs if "本分钟回答次数已达上限" in log[1]]


def _quota_total(ctx) -> int:
    return sum(int(v) for k, v in ctx.storage.data.items() if k.startswith("quota:global:"))


# -- 归一化与配置 ----------------------------------------------------------------


def test_normalize_burst_text() -> None:
    norm = PLUGIN.normalize_burst_text
    assert norm("  问答抽奖 ") == "问答抽奖"
    assert norm("AI  Lottery\n2026") == norm("ai lottery 2026") == "ai lottery 2026"
    assert norm("") == norm(None) == ""


def test_config_defaults_and_bounds() -> None:
    cfg = PLUGIN._cfg(types.SimpleNamespace(config={}))
    assert (cfg["burst_guard"], cfg["burst_users"], cfg["burst_window_seconds"], cfg["burst_cooldown_minutes"]) == (True, 3, 60, 10)
    assert cfg["group_answers_per_minute"] == 6
    cfg = PLUGIN._cfg(
        types.SimpleNamespace(
            config={
                "keyword_burst_guard": False,
                "keyword_burst_users": 1,
                "keyword_burst_window_seconds": 99999,
                "keyword_burst_cooldown_minutes": "abc",
                "group_answers_per_minute": -5,
            }
        )
    )
    assert (cfg["burst_guard"], cfg["burst_users"], cfg["burst_window_seconds"], cfg["burst_cooldown_minutes"]) == (False, 2, 600, 10)
    assert cfg["group_answers_per_minute"] == 0


def test_schema_fields_in_trigger_section() -> None:
    schema = base.PACKAGE.MANIFEST.config_schema["properties"]
    expected = {
        "keyword_burst_guard": True,
        "keyword_burst_users": 3,
        "keyword_burst_window_seconds": 60,
        "keyword_burst_cooldown_minutes": 10,
        "group_answers_per_minute": 6,
    }
    for key, default in expected.items():
        assert schema[key]["default"] == default, key
        assert schema[key]["x-ui-section"] == "触发方式", key
    raw = json.loads((base.PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert raw["version"] == base.PACKAGE.MANIFEST.version == "0.4.12"


# -- 口令判定 --------------------------------------------------------------------


def test_password_burst_detected_then_silenced(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env)
    results = [_send(plugin, ctx, "问答抽奖", user_id=uid) for uid in range(1, 8)]
    # 前 N-1=2 条在判定前已触发；第 3 人起判定为口令，静默（无占位、无回复）。
    assert len(ctx.ai.calls) == 2
    assert all(results[i] for i in range(2))
    assert all(r == [] for r in results[2:])
    assert ctx.messages.calls == []
    # 不扣额度：只有回答过的 2 条占用了全局次数。
    assert _quota_total(ctx) == 2
    logs = _burst_logs(ctx)
    assert len(logs) == 1  # 每个口令只记一次
    assert logs[0][2]["users"] == 3 and logs[0][2]["text_preview"] == "答抽奖"


def test_normalization_counts_variants_as_same_text(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"trigger_keywords": "AI"})
    for uid, text in ((1, "AI Lottery  Go"), (2, "ai lottery go "), (3, "AI LOTTERY GO"), (4, "ai\tlottery go")):
        _send(plugin, ctx, text, user_id=uid)
    assert len(ctx.ai.calls) == 2
    assert len(_burst_logs(ctx)) == 1


def test_same_user_repeating_is_not_a_password(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env)
    for _ in range(5):
        _send(plugin, ctx, "问 今天几号", user_id=42)
    assert len(ctx.ai.calls) == 5
    assert _burst_logs(ctx) == []


def test_debounce_stops_messages_arriving_together(monkeypatch, _env) -> None:
    """已有别人发过同一句时先等一小会儿：同时到达的第 2 条在等待期间被第 3 条判定拦下。"""

    monkeypatch.setattr(PLUGIN, "BURST_DEBOUNCE_SECONDS", 0.05)
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env)

    async def scenario():
        payloads = [base.message_payload("问答抽奖", chat_id=CHAT_A, user_id=uid) for uid in (1, 2, 3)]
        return await asyncio.gather(*(plugin.on_event(ctx, p) for p in payloads))

    results = asyncio.run(scenario())
    assert len(ctx.ai.calls) == 1
    assert results[0] and results[1] == [] and results[2] == []


def test_cooldown_expires_and_text_answers_again(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"keyword_burst_cooldown_minutes": 10})
    for uid in (1, 2, 3, 4):
        _send(plugin, ctx, "问答抽奖", user_id=uid)
    assert len(ctx.ai.calls) == 2
    _env.now += 9 * 60  # 冷却期内仍静默（不再刷新冷却）
    assert _send(plugin, ctx, "问答抽奖", user_id=5) == []
    _env.now += 61  # 判定起已过 10 分钟
    assert _send(plugin, ctx, "问答抽奖", user_id=6)
    assert len(ctx.ai.calls) == 3


def test_window_expiry_resets_count(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"keyword_burst_window_seconds": 60})
    _send(plugin, ctx, "问 天气", user_id=1)
    _send(plugin, ctx, "问 天气", user_id=2)
    _env.now += 61
    _send(plugin, ctx, "问 天气", user_id=3)  # 窗口已过：重新计数，不算口令
    assert len(ctx.ai.calls) == 3
    assert _burst_logs(ctx) == []


def test_different_chats_are_isolated(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env)
    for uid in (1, 2, 3):
        _send(plugin, ctx, "问答抽奖", user_id=uid, chat_id=CHAT_A)
    assert _send(plugin, ctx, "问答抽奖", user_id=4, chat_id=CHAT_A) == []
    assert _send(plugin, ctx, "问答抽奖", user_id=4, chat_id=CHAT_B)
    assert _send(plugin, ctx, "问答抽奖", user_id=5, chat_id=CHAT_B)
    assert len(ctx.ai.calls) == 4


def test_private_and_guest_are_not_affected(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"group_answers_per_minute": 1})
    for uid in range(1, 6):
        _send(plugin, ctx, "问答抽奖", user_id=uid, chat_id=uid, chat_type="private")
    for uid in range(1, 6):
        base.run(plugin.on_event(ctx, base.guest_payload("问答抽奖", user_id=uid, query_id=f"g{uid}")))
    assert len(ctx.ai.calls) == 10
    assert _burst_logs(ctx) == [] and _rate_logs(ctx) == []


def test_mention_triggers_are_not_burst_checked(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"bot_username": "helper_bot"})
    for uid in (1, 2, 3, 4):
        _send(plugin, ctx, "@helper_bot 问答抽奖", user_id=uid)
    assert len(ctx.ai.calls) == 4
    assert _burst_logs(ctx) == []


def test_guard_can_be_disabled(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"keyword_burst_guard": False, "group_answers_per_minute": 0})
    for uid in range(1, 8):
        _send(plugin, ctx, "问答抽奖", user_id=uid)
    assert len(ctx.ai.calls) == 7


def test_custom_threshold(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"keyword_burst_users": 2})
    _send(plugin, ctx, "问答抽奖", user_id=1)
    assert _send(plugin, ctx, "问答抽奖", user_id=2) == []
    assert len(ctx.ai.calls) == 1


# -- 每群每分钟上限 --------------------------------------------------------------


def test_per_chat_minute_limit(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"group_answers_per_minute": 2})
    _env.now = 60 * 20000  # 自然分钟起点
    results = [_send(plugin, ctx, f"问 问题{uid}", user_id=uid) for uid in range(1, 6)]
    assert len(ctx.ai.calls) == 2
    assert all(r == [] for r in results[2:])
    assert _quota_total(ctx) == 2  # 超出的不扣次数
    assert len(_rate_logs(ctx)) == 1  # 每分钟最多记一条
    # 另一个群不受影响
    assert _send(plugin, ctx, "问 别的群", user_id=9, chat_id=CHAT_B)
    # 下一分钟恢复
    _env.now += 60
    assert _send(plugin, ctx, "问 下一分钟", user_id=10)
    assert _send(plugin, ctx, "问 下一分钟2", user_id=11)
    assert _send(plugin, ctx, "问 下一分钟3", user_id=12) == []
    assert len(_rate_logs(ctx)) == 2


def test_per_chat_limit_counts_mentions_and_replies(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"group_answers_per_minute": 2, "bot_username": "helper_bot"})
    _env.now = 60 * 20000
    _send(plugin, ctx, "@helper_bot 第一问", user_id=1)
    _send(plugin, ctx, "继续说", user_id=2, reply_username="helper_bot", reply_text="上一条回答")
    assert _send(plugin, ctx, "问 第三问", user_id=3) == []
    assert len(ctx.ai.calls) == 2


def test_per_chat_limit_zero_means_unlimited(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"group_answers_per_minute": 0})
    for uid in range(1, 15):
        _send(plugin, ctx, f"问 问题{uid}", user_id=uid)
    assert len(ctx.ai.calls) == 14


def test_admins_are_exempt_from_per_chat_limit(_env) -> None:
    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"group_answers_per_minute": 1, "retry_admin_user_ids": "900"})
    _env.now = 60 * 20000
    _send(plugin, ctx, "问 普通人", user_id=1)
    assert _send(plugin, ctx, "问 又一个普通人", user_id=2) == []
    # 插件配置的管理员、平台下发的 TelePilot 管理员不受上限约束
    assert _send(plugin, ctx, "问 配置管理员", user_id=900)
    payload = base.message_payload("问 TelePilot 管理员", chat_id=CHAT_A, user_id=901)
    payload["admin_user_ids"] = [901]
    assert base.run(plugin.on_event(ctx, payload))

    # 群管理员（平台核验）超限后也放行，且不占本群名额
    class Identities:
        def __init__(self) -> None:
            self.calls = 0

        async def resolve(self, *, chat_id, user_id):
            self.calls += 1
            return types.SimpleNamespace(is_admin=user_id == 902, resolved=True)

    ctx.identities = Identities()
    assert _send(plugin, ctx, "问 群管理员", user_id=902)
    assert _send(plugin, ctx, "问 群管理员再问", user_id=902)
    assert _send(plugin, ctx, "问 普通人3", user_id=3) == []
    assert _send(plugin, ctx, "问 普通人3 再问", user_id=3) == []
    # 非管理员核验结果缓存：同一人重复刷屏只查一次
    assert ctx.identities.calls == 2
    assert len(ctx.ai.calls) == 5


def test_burst_check_runs_before_rate_limit(_env) -> None:
    """口令被拦下的消息不占每群每分钟名额。"""

    plugin = PLUGIN.InteractionAIChatPlugin()
    ctx = _ctx(_env, config={"group_answers_per_minute": 3})
    _env.now = 60 * 20000
    for uid in range(1, 10):
        _send(plugin, ctx, "问答抽奖", user_id=uid)
    assert len(ctx.ai.calls) == 2
    assert _send(plugin, ctx, "问 正常提问", user_id=20)  # 第 3 次，未超限
    assert _rate_logs(ctx) == []
