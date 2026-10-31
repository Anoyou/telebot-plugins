"""用 TelePilot 主仓库真实运行时检查全部插件（scripts/check_host_compat.py）。

需要主仓库检出：设置 ``TELEPILOT_REPO=/path/to/Telebot``，或直接用主仓库
``backend/.venv`` 的 Python 运行 pytest。找不到主仓库时跳过（CI 的 host-compat job 必跑）。

检查放在子进程里执行：其它测试会往 ``sys.modules`` 塞 ``app.*`` 桩模块，
与真实宿主模块不能共存于同一进程。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_host_compat.py"


def _host_repo() -> Path | None:
    for raw in (os.environ.get("TELEPILOT_REPO"), str(Path(sys.prefix).resolve().parent.parent)):
        if raw and (Path(raw) / "backend" / "app" / "worker" / "plugins" / "loader.py").is_file():
            return Path(raw).resolve()
    return None


@pytest.fixture(scope="module")
def compat_report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    repo = _host_repo()
    if repo is None:
        pytest.skip("未找到 TelePilot 主仓库（设置 TELEPILOT_REPO 后运行宿主兼容性检查）")
    out = tmp_path_factory.mktemp("host-compat") / "report.json"
    env = dict(os.environ, TELEPILOT_REPO=str(repo))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", str(out), "--quiet"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if not out.is_file():
        pytest.fail("宿主兼容性检查没有产出报告:\n" + result.stdout + result.stderr)
    return json.loads(out.read_text(encoding="utf-8"))


def test_report_includes_host_contract_version(compat_report: dict) -> None:
    """报告带宿主的 Plugin API 与契约版本；契约版本在旧宿主上为 None（只展示，不影响结论）。"""

    assert "plugin_api_version" in compat_report
    assert "contract_version" in compat_report
    contract = compat_report["contract_version"]
    assert contract is None or contract.split(".")[0].isdigit()


def _plugin_keys() -> list[str]:
    return sorted(p.parent.name for p in ROOT.glob("*/plugin.json"))


def test_report_covers_every_plugin(compat_report: dict) -> None:
    assert sorted(item["key"] for item in compat_report["plugins"]) == _plugin_keys()


@pytest.mark.parametrize("plugin_key", _plugin_keys())
def test_plugin_is_compatible_with_host(compat_report: dict, plugin_key: str) -> None:
    item = next(entry for entry in compat_report["plugins"] if entry["key"] == plugin_key)
    assert not item["errors"], (
        f"{plugin_key} 与 TelePilot {compat_report['host_version']} 不兼容:\n" + "\n".join(item["errors"])
    )


@pytest.mark.parametrize("plugin_key", _plugin_keys())
def test_min_version_covers_used_capabilities(compat_report: dict, plugin_key: str) -> None:
    item = next(entry for entry in compat_report["plugins"] if entry["key"] == plugin_key)
    low = [w for w in item["warnings"] if "min_telepilot_version" in w and "偏低" in w]
    assert not low, "\n".join(low)
