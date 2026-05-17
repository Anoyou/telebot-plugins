#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = ["plugin.json", "manifest.py", "plugin.py", "__init__.py", "legacy_main.py"]
REQUIRED_PLUGIN_JSON_FIELDS = [
    "name",
    "display_name",
    "description",
    "author",
    "version",
    "entry",
    "min_telebot_version",
    "commands",
    "cleanup_mode",
    "permissions",
    "config_schema",
]


@dataclass
class PluginReport:
    name: str
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    command_count: int = 0
    listener_count: int = 0
    command_listener_count: int = 0

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def extract_listener_commands(tree: ast.AST) -> tuple[list[str], int, int]:
    commands: list[str] = []
    listener_count = 0
    command_listener_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "listener":
            listener_count += 1
            cmd = None
            for kw in node.keywords:
                if kw.arg == "command" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    cmd = kw.value.value.strip()
                    break
            if cmd:
                command_listener_count += 1
                if cmd not in commands:
                    commands.append(cmd)
    return commands, listener_count, command_listener_count


def parse_manifest_key_and_version(text: str) -> tuple[str | None, str | None]:
    key_match = re.search(r'key\s*=\s*"([^"]+)"', text)
    ver_match = re.search(r'version\s*=\s*"([^"]+)"', text)
    return (key_match.group(1) if key_match else None, ver_match.group(1) if ver_match else None)


def validate_plugin(plugin_dir: Path) -> PluginReport:
    report = PluginReport(name=plugin_dir.name)

    for filename in REQUIRED_FILES:
        if not (plugin_dir / filename).exists():
            report.fail(f"missing required file: {filename}")

    if report.errors:
        return report

    plugin_json_path = plugin_dir / "plugin.json"
    manifest_path = plugin_dir / "manifest.py"
    plugin_py_path = plugin_dir / "plugin.py"
    legacy_py_path = plugin_dir / "legacy_main.py"

    try:
        plugin_json = json.loads(plugin_json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        report.fail(f"plugin.json invalid JSON: {exc}")
        return report

    for field in REQUIRED_PLUGIN_JSON_FIELDS:
        if field not in plugin_json:
            report.fail(f"plugin.json missing field: {field}")

    if plugin_json.get("name") != plugin_dir.name:
        report.fail(f"plugin.json.name mismatch: {plugin_json.get('name')} != {plugin_dir.name}")

    if plugin_json.get("entry") != "plugin.py":
        report.fail("plugin.json.entry should be plugin.py")

    if not isinstance(plugin_json.get("commands"), list) or not plugin_json.get("commands"):
        report.fail("plugin.json.commands should be non-empty list")

    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_key, manifest_version = parse_manifest_key_and_version(manifest_text)

    if manifest_key != plugin_json.get("name"):
        report.fail(f"manifest key mismatch: {manifest_key} != {plugin_json.get('name')}")

    if manifest_version != plugin_json.get("version"):
        report.fail(f"manifest version mismatch: {manifest_version} != {plugin_json.get('version')}")

    for py_path in [manifest_path, plugin_py_path, legacy_py_path, plugin_dir / "__init__.py"]:
        src = py_path.read_text(encoding="utf-8")
        try:
            compile(src, str(py_path), "exec")
        except Exception as exc:  # noqa: BLE001
            report.fail(f"python syntax invalid: {py_path.name}: {exc}")

    legacy_tree = ast.parse(legacy_py_path.read_text(encoding="utf-8"))
    legacy_commands, listener_count, command_listener_count = extract_listener_commands(legacy_tree)
    report.listener_count = listener_count
    report.command_listener_count = command_listener_count
    report.command_count = len(legacy_commands)

    command_set_legacy = set(legacy_commands)
    command_set_json = set(plugin_json.get("commands", []))

    if command_set_legacy and command_set_legacy != command_set_json:
        report.fail(
            f"commands mismatch between legacy listener and plugin.json: "
            f"legacy={sorted(command_set_legacy)} json={sorted(command_set_json)}"
        )

    plugin_py_text = plugin_py_path.read_text(encoding="utf-8")
    required_bridge_markers = [
        "class _CompatRuntime",
        "class _CompatMessage",
        "class _CompatClient",
        "from . import legacy_main",
        "dispatch_command",
        "dispatch_message",
    ]
    for marker in required_bridge_markers:
        if marker not in plugin_py_text:
            report.fail(f"plugin bridge marker missing: {marker}")

    if "command listeners are dispatched via on_command path" not in plugin_py_text:
        report.warn("on_message command skip guard marker not found")

    high_risk_markers = {
        "network": ["httpx", "aiohttp", "requests"],
        "media": ["send_photo", "send_document", "send_media_group", "reply_sticker"],
        "reaction": ["send_reaction", "ReactionType"],
        "click": [".click("],
    }
    legacy_text = legacy_py_path.read_text(encoding="utf-8")
    touched = [k for k, markers in high_risk_markers.items() if any(m in legacy_text for m in markers)]
    if touched:
        report.warn("high-risk capabilities detected: " + ", ".join(touched))

    return report


def main() -> int:
    plugin_dirs = sorted([p for p in ROOT.iterdir() if p.is_dir() and p.name.endswith("-byRBQ")])
    if not plugin_dirs:
        print("No *-byRBQ plugins found")
        return 1

    reports = [validate_plugin(d) for d in plugin_dirs]
    ok_count = sum(1 for r in reports if r.ok)

    print("=== BYRBQ Plugin Smoke Check ===")
    print(f"Root: {ROOT}")
    print(f"Plugins: {len(reports)} | Passed: {ok_count} | Failed: {len(reports)-ok_count}")
    print("")

    for r in reports:
        status = "PASS" if r.ok else "FAIL"
        print(f"[{status}] {r.name} | listeners={r.listener_count} command_listeners={r.command_listener_count} commands={r.command_count}")
        for e in r.errors:
            print(f"  ERROR: {e}")
        for w in r.warnings:
            print(f"  WARN:  {w}")

    failed = [r for r in reports if not r.ok]
    if failed:
        print("\nResult: FAILED")
        return 2

    print("\nResult: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
