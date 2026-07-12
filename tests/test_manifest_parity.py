from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Manifest:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def _load_python_manifest(plugin_key: str):
    module_names = (
        "app",
        "app.worker",
        "app.worker.plugins",
        "app.worker.plugins.manifest",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    manifest_module = types.ModuleType("app.worker.plugins.manifest")
    manifest_module.Manifest = _Manifest
    sys.modules["app"] = types.ModuleType("app")
    sys.modules["app.worker"] = types.ModuleType("app.worker")
    sys.modules["app.worker.plugins"] = types.ModuleType("app.worker.plugins")
    sys.modules["app.worker.plugins.manifest"] = manifest_module
    try:
        path = ROOT / plugin_key / "manifest.py"
        spec = importlib.util.spec_from_file_location(
            f"{plugin_key.replace('-', '_')}_manifest_parity",
            path,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.MANIFEST
    finally:
        for name, old in previous.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _json_manifest(plugin_key: str) -> dict:
    return json.loads((ROOT / plugin_key / "plugin.json").read_text(encoding="utf-8"))


def test_mindreader_config_schema_matches_static_manifest() -> None:
    raw = _json_manifest("mindreader_survival")
    manifest = _load_python_manifest("mindreader_survival")

    assert manifest.config_schema == raw["config_schema"]


def test_ai_chat_config_schema_matches_static_manifest_byte_for_byte() -> None:
    raw = _json_manifest("ai-chat")
    manifest = _load_python_manifest("ai-chat")

    assert manifest.config_schema == raw["config_schema"]
    assert "\n\n" in manifest.config_schema["properties"]["explain_prompt"]["default"]


def test_pt_promote_cookie_is_sensitive_in_both_manifests() -> None:
    raw = _json_manifest("pt_promote")
    manifest = _load_python_manifest("pt_promote")

    for schema in (raw["config_schema"], manifest.config_schema):
        cookie = schema["properties"]["cookie"]
        assert cookie["x-sensitive"] is True
        assert cookie["format"] == "password"
