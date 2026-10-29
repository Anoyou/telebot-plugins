"""TelePilot 0.112.0（Plugin API 1.5）业务下沉声明：entry_policy / provides / ui.rule_editor。

兼容约定（两种组合都必须成立）：

- 新插件 + 旧宿主（≤ 0.110.0-beta.2）：旧宿主只读入口的顶层字段（session_scope / participant_policy /
  input_schema 里的 prize），忽略 entry_policy / provides / ui.rule_editor，所以 entry_policy 必须与顶层字段
  完全一致；provides 只写在 plugin.json（旧宿主的 Manifest 不接受这个参数）。
- 新插件 + 新宿主：宿主读到 entry_policy 就不再查兜底入口表，结论与旧宿主相同。
- min_telepilot_version 不因这些声明提高。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DECLARED = {
    "game24": "start_paid_game",
    "pt_promote": "promote_torrent",
    "blackjack": "start_blackjack",
}


def _meta(key: str) -> dict:
    return json.loads((ROOT / key / "plugin.json").read_text(encoding="utf-8"))


def _entry(key: str, entry_key: str) -> dict:
    return next(item for item in _meta(key)["interaction_entries"] if item["key"] == entry_key)


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_entry_policy_matches_top_level_fields(key: str) -> None:
    entry = _entry(key, DECLARED[key])
    policy = entry["entry_policy"]
    assert policy.get("session_scope") == entry.get("session_scope")
    if "participant_policy" in policy:
        assert policy["participant_policy"] == entry.get("participant_policy")
    else:
        assert "participant_policy" not in entry
    has_prize = "prize" in ((entry.get("input_schema") or {}).get("properties") or {})
    assert policy.get("prize") is has_prize


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_manifest_py_mirrors_entry_policy(key: str) -> None:
    tree = ast.parse((ROOT / key / "manifest.py").read_text(encoding="utf-8"))
    entries = None
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "interaction_entries":
            entries = ast.literal_eval(node.value)
    assert entries is not None
    by_key = {item["key"]: item for item in entries}
    assert by_key[DECLARED[key]]["entry_policy"] == _entry(key, DECLARED[key])["entry_policy"]


def test_codex_image_provides_only_in_plugin_json() -> None:
    assert _meta("codex_image")["provides"] == ["ai.image_backend"]
    source = (ROOT / "codex_image" / "manifest.py").read_text(encoding="utf-8")
    assert "provides" not in source  # 旧宿主 Manifest(...) 不接受 provides 参数


def test_rule_editor_only_names_declared_entries() -> None:
    meta = _meta("game24")
    entries = meta["ui"]["rule_editor"]["entries"]
    declared = {item["key"] for item in meta["interaction_entries"]}
    assert set(entries) <= declared


@pytest.mark.parametrize("key", [*sorted(DECLARED), "codex_image"])
def test_min_version_not_raised(key: str) -> None:
    assert _meta(key)["min_telepilot_version"] == "0.72.0-beta.2"
