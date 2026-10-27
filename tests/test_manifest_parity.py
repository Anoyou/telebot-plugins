from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Core 的标准动作词表，与 backend/app/services/interaction/action_core.py 的
# CANONICAL_ACTION_TYPES 保持一致；只有这些值才是插件可返回的动作。
CANONICAL_ACTION_TYPES = frozenset(
    {
        "send_message",
        "send_rich_message",
        "send_photo",
        "send_file",
        "edit_message",
        "edit_caption",
        "delete_message",
        "pin_message",
        "click_callback_button",
        "answer_callback",
        "answer_inline_query",
        "answer_guest_query",
        "payout",
        "update_session",
        "start_session",
        "settlement",
        "result",
        "end_session",
        "close_session",
        "no_session",
    }
)


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


def _string_literals(mapping: ast.Dict, key: str) -> set[str]:
    return {
        value.value
        for name, value in zip(mapping.keys, mapping.values)
        if isinstance(name, ast.Constant)
        and name.value == key
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    }


def _emitted_action_types(plugin_key: str) -> set[str]:
    """Return action types the plugin actually returns, excluding event types.

    Only dictionary keys literally named ``type`` are inspected, and only values
    that are part of the Core action vocabulary are kept, so event names such as
    ``command`` or ``payment_confirmed`` cannot be mistaken for actions.
    """

    source = ast.parse((ROOT / plugin_key / "plugin.py").read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Dict):
            found |= _string_literals(node, "type")
    return {item for item in found if item in CANONICAL_ACTION_TYPES}


def _interaction_plugin_keys() -> Iterator[str]:
    for path in sorted(ROOT.glob("*/plugin.json")):
        if not (path.parent / "plugin.py").is_file():
            continue
        entries = json.loads(path.read_text(encoding="utf-8")).get("interaction_entries")
        if isinstance(entries, list) and any(isinstance(item, dict) for item in entries):
            yield path.parent.name


def test_interaction_plugins_declare_every_returned_action() -> None:
    failures: list[str] = []
    for plugin_key in _interaction_plugin_keys():
        emitted = _emitted_action_types(plugin_key)
        raw = _json_manifest(plugin_key)
        manifest = _load_python_manifest(plugin_key)
        for label, entries in (
            ("plugin.json", raw["interaction_entries"]),
            ("manifest.py", manifest.interaction_entries),
        ):
            declared = {
                str(action)
                for entry in entries
                for action in (entry.get("result_contract") or {}).get("actions") or []
            }
            missing = sorted(emitted - declared)
            if missing:
                failures.append(f"{plugin_key} ({label}): {missing}")
    assert not failures, "返回动作未在 result_contract.actions 声明：" + "; ".join(failures)


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

    assert manifest.config_schema == raw["config_schema"]
    for schema in (raw["config_schema"], manifest.config_schema):
        cookie = schema["properties"]["cookie"]
        assert cookie["x-sensitive"] is True
        assert cookie["format"] == "password"
        site_url = schema["properties"]["site_url"]
        assert site_url["enum"] == ["https://www.qingwapt.com"]
        assert site_url["readOnly"] is True


def test_pt_promote_declares_all_interaction_result_actions() -> None:
    raw = _json_manifest("pt_promote")
    manifest = _load_python_manifest("pt_promote")
    assert raw["version"] == manifest.version
    assert raw["interaction_entries"] == manifest.interaction_entries

    emitted_actions = _emitted_action_types("pt_promote")
    assert "no_session" in emitted_actions
    for entries in (raw["interaction_entries"], manifest.interaction_entries):
        entry = next(item for item in entries if item["key"] == "promote_torrent")
        declared_actions = set(entry["result_contract"]["actions"])
        assert emitted_actions <= declared_actions, emitted_actions - declared_actions
