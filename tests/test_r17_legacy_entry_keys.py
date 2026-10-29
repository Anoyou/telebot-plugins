"""TelePilot 0.112.0-beta.3（Plugin API 1.6）旧入口 key 声明：math10 / dice_grid_hunt / guess_number。

宿主兜底入口表（TelePilot ``app/plugin_compat.py``）里这三个插件各有一个旧入口 key 插件没正式声明，
兜底表对它只给出“按群会话”。插件在正式入口里写 ``legacy_entry_keys`` 把这条推断收回到自己的声明。

兼容约定（三种组合都必须成立）：

- 新插件 + 新宿主（≥ 0.112.0-beta.3）：读到 ``legacy_entry_keys`` 就不再查兜底表，结论与兜底表相同；
- 新插件 + 旧宿主（0.110.0-beta.2 / 0.112.0-beta.2）：旧宿主忽略 ``legacy_entry_keys``；因为**没有写
  entry_policy**（0.112.0-beta.2 见到它会整表跳过兜底），旧宿主照旧走兜底表，结论相同；
- 正式入口只有一个（“规则没写 module_action 时取唯一入口”的推断不受影响），其余字段与上一版完全一致；
- ``min_telepilot_version`` 不提高。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 插件 → (正式入口, 旧入口 key)。与宿主兜底表 FALLBACK_CHAT_SESSION_MODULE_ENTRIES 的对应行一致。
DECLARED = {
    "math10": ("start_math_game", "start_math10"),
    "dice_grid_hunt": ("start_dice_grid_hunt", "answer_dice_grid_hunt"),
    "guess_number": ("start_guess_number", "start_game"),
}


def _meta(key: str) -> dict:
    return json.loads((ROOT / key / "plugin.json").read_text(encoding="utf-8"))


def _manifest_entries(key: str) -> list[dict]:
    tree = ast.parse((ROOT / key / "manifest.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "interaction_entries":
            return ast.literal_eval(node.value)
    raise AssertionError(f"{key}/manifest.py 没有 interaction_entries")


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_single_formal_entry_with_legacy_key(key: str) -> None:
    formal, legacy = DECLARED[key]
    entries = _meta(key)["interaction_entries"]
    assert [item["key"] for item in entries] == [formal]
    assert entries[0]["legacy_entry_keys"] == [{"key": legacy, "session_scope": "chat"}]


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_no_entry_policy_for_old_host_compat(key: str) -> None:
    for entry in _meta(key)["interaction_entries"]:
        assert "entry_policy" not in entry


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_manifest_py_mirrors_plugin_json(key: str) -> None:
    assert _manifest_entries(key) == _meta(key)["interaction_entries"]


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_plugin_code_still_accepts_legacy_key_where_it_did(key: str) -> None:
    """插件代码对旧 key 的处理不变（math10 / dice_grid_hunt 照旧接受；guess_number 本来就不接受 start_game）。"""

    source = (ROOT / key / "plugin.py").read_text(encoding="utf-8")
    _formal, legacy = DECLARED[key]
    if key == "guess_number":
        assert f'"{legacy}"' not in source
    else:
        assert f'"{legacy}"' in source


@pytest.mark.parametrize("key", sorted(DECLARED))
def test_min_version_not_raised(key: str) -> None:
    assert _meta(key)["min_telepilot_version"] == "0.72.0-beta.2"
