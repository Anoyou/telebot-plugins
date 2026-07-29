from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "ais-byRBQ/legacy_main.py": (1, {"LEGACY_DATA_DIR", "LEGACY_DATA_FILE"}),
    "cai-byRBQ/legacy_main.py": (1, {"LEGACY_CONFIG_FILE"}),
    "gi2-byRBQ/legacy_main.py": (1, {"LEGACY_CONFIG_FILE"}),
    "jpm-byRBQ/legacy_main.py": (2, {"LEGACY_CONFIG_FILE", "LEGACY_TRIGGER_LOG_FILE"}),
    "jpmai-byRBQ/legacy_main.py": (2, {"LEGACY_CONFIG_FILE", "LEGACY_TRIGGER_LOG_FILE"}),
    "luckydraw-byRBQ/legacy_main.py": (1, {"LEGACY_CONFIG_FILE"}),
    "redpack-byRBQ/legacy_main.py": (2, {"LEGACY_CONFIG_FILE", "LEGACY_ACCOUNT_CONFIG_NAME"}),
    "sar-byRBQ/legacy_main.py": (1, {"LEGACY_CONFIG_FILE"}),
    "sfl-byRBQ/legacy_main.py": (1, {"LEGACY_CONFIG_FILE"}),
}


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def _assigned_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _load_copy_helper(path: Path, tree: ast.Module):
    helper = _function(tree, "_copy_legacy_file_once")
    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Path": Path, "os": os, "tempfile": tempfile}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_copy_legacy_file_once"]


def test_byrbq_data_migrations_are_atomic_and_non_destructive() -> None:
    for relative_path, (expected_calls, expected_constants) in CASES.items():
        path = ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert expected_constants <= _assigned_names(tree), relative_path

        configure = _function(tree, "configure_data_dir")
        calls = [
            node
            for node in ast.walk(configure)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_copy_legacy_file_once"
        ]
        assert len(calls) == expected_calls, relative_path

        helper = _load_copy_helper(path, tree)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "legacy.json"
            target = root / "data" / "config.json"
            source.write_bytes(b'{"source":"legacy"}')

            assert helper(source, target) is True, relative_path
            assert target.read_bytes() == source.read_bytes(), relative_path
            assert source.exists(), relative_path

            target.write_bytes(b'{"source":"current"}')
            assert helper(source, target) is False, relative_path
            assert target.read_bytes() == b'{"source":"current"}', relative_path
            assert source.exists(), relative_path
            assert not list(target.parent.glob(f".{target.name}.*.tmp")), relative_path
