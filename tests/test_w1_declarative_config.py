"""W1 迁移（TelePilot 0.110.0-beta.2）：五个插件改用宿主通用声明式配置页。

- auto_reply / autorepeat：x-ui-mode=rules + x-rule-schema / x-rule-summary / x-dry-run，导出 dry_run_rule；
- chatgpt_image：tokens 声明 x-secret-list，on_config_validate 提取 session JSON 并去重；
- codex_image：on_config_validate 改写下线模型、粘贴 auth.json 提取 access token；
- 都不抬高 min_telepilot_version：新声明 / 钩子旧宿主不认识也不调用，运行时代码不依赖新宿主。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

W1_PLUGINS = ("auto_reply", "autorepeat", "chatgpt_image", "codex_image", "game24")


def _raw(key: str) -> dict:
    return json.loads((ROOT / key / "plugin.json").read_text(encoding="utf-8"))


_STUB_PREFIXES = ("app", "telethon", "auto_reply", "autorepeat", "chatgpt_image", "codex_image", "game24")


def _is_prefixed(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _STUB_PREFIXES)


@pytest.fixture(autouse=True)
def _real_host_modules():
    """其它用例会往 sys.modules 塞 app / telethon 桩模块；本文件要用真实宿主，用完原样还回去。"""
    saved = {name: mod for name, mod in sys.modules.items() if _is_prefixed(name)}
    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in [name for name in sys.modules if _is_prefixed(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def _plugin_module(key: str):
    pytest.importorskip("app.worker.plugins.base")
    return importlib.import_module(f"{key}.plugin")


@pytest.mark.parametrize("key", W1_PLUGINS)
def test_min_version_not_raised(key: str) -> None:
    assert _raw(key)["min_telepilot_version"] == "0.72.0-beta.2"


def test_auto_reply_declares_rule_schema() -> None:
    schema = _raw("auto_reply")["config_schema"]
    assert schema["x-ui-mode"] == "rules"
    rule = schema["x-rule-schema"]
    assert set(schema["x-rule-summary"]) <= set(rule["properties"])
    assert rule["required"] == ["patterns", "reply"]
    assert rule["properties"]["group_ids"]["x-ui-visible-if"] == {"field": "scope", "equals": "group_specific"}
    assert schema["x-dry-run"]["sample_fields"] == ["message", "chat_type", "chat_id"]
    # 与旧专属页写入的字段同名，存量规则无需迁移
    for field in ("match", "scope", "group_ids", "patterns", "reply", "cooldown_seconds", "cooldown_scope",
                  "daily_limit_per_user", "usage_label", "cooldown_notice_enabled", "daily_limit_notice_enabled",
                  "case_sensitive", "reply_to", "whitelist", "blacklist"):
        assert field in rule["properties"], field


def test_autorepeat_declares_summary_and_dry_run() -> None:
    schema = _raw("autorepeat")["config_schema"]
    assert schema["x-rule-summary"] == ["target_chat_id", "time_window", "min_users"]
    assert schema["x-dry-run"]["sample_fields"] == ["message", "chat_id"]


def test_host_lint_accepts_rules_declarations() -> None:
    plugin_ui = pytest.importorskip("app.worker.plugins.plugin_ui")
    lint = getattr(plugin_ui, "lint_rules_declaration", None)
    if lint is None:
        pytest.skip("宿主早于 0.110.0-beta.2，没有通用规则页 lint")
    for key in ("auto_reply", "autorepeat"):
        assert lint(_raw(key)["config_schema"]) == [], key


def test_auto_reply_dry_run_rule_matches_legacy() -> None:
    mod = _plugin_module("auto_reply")
    cfg = {"match": "keyword", "patterns": ["你好"], "scope": "private", "reply": "hi {text}"}
    new = mod.dry_run_rule(cfg, {"message": "你好呀", "chat_type": "private", "chat_id": None})
    assert (new["matched"], new["output"]) == mod._dry_run_match(cfg, "你好呀", "private", None)
    assert new["matched"] is True and new["logs"][-1]["step"] == "result"
    miss = mod.dry_run_rule(cfg, {"message": "你好", "chat_type": "group", "chat_id": -1001})
    assert miss["matched"] is False
    group_cfg = {**cfg, "scope": "group_specific", "group_ids": ["-1001"]}
    hit = mod.dry_run_rule(group_cfg, {"message": "你好", "chat_type": "group", "chat_id": None})
    assert hit["matched"] is True
    assert any("指定群" in item["msg"] for item in hit["logs"])


def test_autorepeat_dry_run_rule() -> None:
    mod = _plugin_module("autorepeat")
    cfg = {"target_chat_id": -100, "time_window": 60, "min_users": 3}
    assert mod.dry_run_rule(cfg, {"message": "x", "chat_id": -100})["matched"] is True
    other = mod.dry_run_rule(cfg, {"message": "x", "chat_id": -200})
    assert other["matched"] is False and "不匹配" in other["output"]


def test_chatgpt_tokens_secret_list_and_validate() -> None:
    field = _raw("chatgpt_image")["config_schema"]["properties"]["tokens"]
    assert field["x-secret-list"] == {"secret_field": "token", "note_field": "note", "id_field": "token_id", "legacy_field": "token"}
    assert _raw("chatgpt_image")["config_schema"]["properties"]["token"]["x-ui-hidden"] is True
    mod = _plugin_module("chatgpt_image")
    session = json.dumps({"user": {"email": "a@b"}, "accessToken": "tok-from-session"})
    result = asyncio.run(mod.ChatGPTImagePlugin().on_config_validate(None, {
        "tokens": [
            {"token": session, "note": "网页"},
            {"token": "tok-from-session", "note": "重复"},
            {"token": "  ", "note": "空"},
            {"token": "tok-2"},
        ],
        "default_count": 2,
        "max_count": 4,
    }, "account"))
    assert result["config"]["tokens"] == [{"token": "tok-from-session", "note": "网页"}, {"token": "tok-2"}]
    assert "errors" not in result
    bad = mod._validate_config({"default_count": 4, "max_count": 2})
    assert "default_count" in bad["errors"]


def test_codex_validate_renames_model_and_extracts_token() -> None:
    mod = _plugin_module("codex_image")
    result = asyncio.run(mod.CodexImagePlugin().on_config_validate(None, {
        "model": "gpt-5.4",
        "command": "  画图 ",
        "access_token": json.dumps({"tokens": {"access_token": "codex-tok"}}),
    }, "account"))
    assert result["config"] == {"model": "gpt-5.5", "command": "画图", "access_token": "codex-tok"}
    assert mod._validate_config({"command": "画 图"})["errors"]["command"]
    assert mod._validate_config({"command": ""})["config"]["command"] == "cximg"
    assert mod._validate_config({"access_token": "plain"})["config"]["access_token"] == "plain"
