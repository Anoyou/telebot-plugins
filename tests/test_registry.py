"""插件市场索引 registry.json 与各插件 plugin.json 保持一致。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_registry", ROOT / "scripts" / "build_registry.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_registry_is_up_to_date() -> None:
    builder = _load_builder()
    assert (ROOT / "registry.json").read_text(encoding="utf-8") == builder.render(builder.build())


def test_registry_entries_cover_every_plugin_with_required_fields() -> None:
    data = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["repository"].startswith("https://")
    dirs = sorted(p.parent.name for p in ROOT.glob("*/plugin.json"))
    assert [item["key"] for item in data["plugins"]] == dirs
    for item in data["plugins"]:
        for field in ("key", "name", "kind", "icon", "summary", "version", "permissions", "channels", "path"):
            assert field in item, (item["key"], field)
        assert set(item["name"]) == {"zh", "en"}
        assert item["kind"] in {"interactive", "automation", "utility", "middleware", "payment_source", "ui"}
        assert item["channels"] == {"stable": "main"}
    kinds = {item["key"]: item["kind"] for item in data["plugins"]}
    assert kinds["payment-source-cn-transfer"] == "payment_source"
