"""registry.json 目录版本 1 已定死（TelePilot Plugin API 1.0 冻结范围，第 22 轮）。"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_registry_v1", ROOT / "scripts" / "build_registry.py")
assert _spec is not None and _spec.loader is not None
build_registry = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = build_registry
_spec.loader.exec_module(build_registry)


def test_committed_registry_is_v1() -> None:
    data = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
    build_registry.validate_v1(data)
    assert data["schema_version"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=2),
        lambda d: d.update(extra_top="x"),
        lambda d: d["plugins"][0].update(new_field=1),
        lambda d: d["plugins"][0].update(kind="game"),
        lambda d: d.update(trust="verified"),
    ],
)
def test_v1_violations_are_rejected(mutate) -> None:
    data = copy.deepcopy(build_registry.build())
    mutate(data)
    with pytest.raises(build_registry.RegistryError):
        build_registry.validate_v1(data)


def test_v1_fields_match_host_when_available() -> None:
    """宿主（TelePilot 0.115.0-beta.4+）登记的目录版本 1 字段与本仓库一致；旧宿主跳过。"""

    repo = os.environ.get("TELEPILOT_REPO")
    if repo:
        backend = str(Path(repo) / "backend")
        if backend not in sys.path:
            sys.path.insert(0, backend)
    try:
        from app.worker.plugins import stability  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        pytest.skip("宿主早于 Plugin API 1.0 冻结范围（没有 app.worker.plugins.stability）")
    assert stability.REGISTRY_SCHEMA_VERSION == build_registry.SCHEMA_VERSION == 1
    assert tuple(stability.REGISTRY_V1_TOP_FIELDS) == build_registry.REGISTRY_V1_TOP_FIELDS
    assert tuple(stability.REGISTRY_V1_ENTRY_FIELDS) == build_registry.REGISTRY_V1_ENTRY_FIELDS
    additive = getattr(stability, "REGISTRY_V1_ADDITIVE_ENTRY_FIELDS", None)
    if additive is not None:  # TelePilot 0.117.0+ 登记了目录版本 1.1 追加字段；更早的宿主忽略这些字段
        assert tuple(additive) == build_registry.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS


def test_additive_fields_are_optional_and_old_fields_unchanged() -> None:
    """1.1 追加字段只增不减：原 16 个字段照旧，summary 仍是字符串（旧服务器照读）。"""

    data = build_registry.build()
    for entry in data["plugins"]:
        assert set(build_registry.REGISTRY_V1_ENTRY_FIELDS) - {"repository", "trust"} <= set(entry)
        assert isinstance(entry["summary"], str)
        assert entry["summary_i18n"]["zh"] == entry["summary"]
        assert entry["channel_versions"]["stable"] == entry["version"]
        assert entry["homepage"].endswith("/" + entry["key"])
    stripped = copy.deepcopy(data)
    for entry in stripped["plugins"]:
        for field in build_registry.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS:
            entry.pop(field, None)
    build_registry.validate_v1(stripped)


def test_release_notes_only_for_current_version(tmp_path: Path) -> None:
    plugin = tmp_path / "demo"
    plugin.mkdir()
    (plugin / "CHANGELOG.md").write_text("# 更新日志\n\n## 1.2.0 (2026-10-30)\n- 新功能\n\n## 1.1.0 (2026-10-01)\n- 旧\n", encoding="utf-8")
    assert build_registry._release_notes(plugin, "1.2.0") == ("- 新功能", "2026-10-30")
    assert build_registry._release_notes(plugin, "1.3.0") == (None, None)
    (plugin / "CHANGELOG.md").write_text("## 1.0.0（2026-10-29）\n\n- 首个版本\n", encoding="utf-8")
    assert build_registry._release_notes(plugin, "1.0.0") == ("- 首个版本", "2026-10-29")


def test_asset_urls(tmp_path: Path) -> None:
    plugin = tmp_path / "demo"
    (plugin / "docs").mkdir(parents=True)
    (plugin / "docs" / "shot.png").write_bytes(b"x")
    assert build_registry._asset_url(plugin, "docs/shot.png").endswith("/demo/docs/shot.png")
    assert build_registry._asset_url(plugin, "https://example.com/a.png") == "https://example.com/a.png"
    for bad in ("http://example.com/a.png", "../x.png", "/etc/x.png", "missing.png"):
        with pytest.raises(build_registry.RegistryError):
            build_registry._asset_url(plugin, bad)
