from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    command_module = types.ModuleType("app.worker.command")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        pass

    command_module.current_command_prefix = lambda *, fallback=",": fallback
    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = lambda cls: cls

    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules["app.worker.command"] = command_module
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module

    spec = importlib.util.spec_from_file_location("sum_plugin_persistence_test", ROOT / "sum" / "plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin_module = _load_plugin_module()


class SummaryPersistenceTest(unittest.TestCase):
    def test_legacy_config_is_copied_to_context_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            legacy = root / "installed" / "summary_config.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"seq": 7}', encoding="utf-8")
            ctx = types.SimpleNamespace(data_dir=root / "data")

            with patch.object(plugin_module, "LEGACY_DB_PATH", legacy):
                target = plugin_module.SummaryPlugin._prepare_data_path(ctx)

            self.assertEqual(target, root / "data" / "summary_config.json")
            self.assertEqual(target.read_text(encoding="utf-8"), '{"seq": 7}')


if __name__ == "__main__":
    unittest.main()
