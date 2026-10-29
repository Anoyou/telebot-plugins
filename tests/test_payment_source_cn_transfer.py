"""payment-source-cn-transfer：与 TelePilot 宿主旧解析器的 golden 对比（必须 100% 一致）。

golden（tests/fixtures/payment_notice_golden.json）由主仓库生成：样本来自主仓库历史/现有测试里的
转账通知文案、默认转账 / 扣减模板的批量渲染和手工边界样本，期望值是宿主旧解析器的输出。
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "payment-source-cn-transfer"
GOLDEN = Path(__file__).with_name("fixtures") / "payment_notice_golden.json"


def _parser():
    spec = importlib.util.spec_from_file_location("_payment_source_cn_transfer_parser", PLUGIN_DIR / "parser.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cases():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_golden_parse_transfer_notice_matches_host_100_percent() -> None:
    parser = _parser()
    cases = _cases()
    assert len(cases) >= 400
    mismatches = [
        (case["origin"], case["text"][:80], case["expected"], parser.parse_transfer_notice(case["text"]))
        for case in cases
        if parser.parse_transfer_notice(case["text"]) != case["expected"]
    ]
    assert mismatches == []


def test_golden_parse_payment_notice_protocol_matches_host() -> None:
    parser = _parser()
    for case in _cases():
        result = parser.parse_payment_notice(case["text"], {"account_id": 1, "source": "text"})
        if case["expected"] is None:
            assert result is None, case["text"]
            continue
        assert result is not None, case["text"]
        unit = result.pop("unit", None)
        assert result == case["expected"], case["text"]
        assert unit is None or (isinstance(unit, str) and 0 < len(unit) <= 8)


def test_unit_is_extra_information_only() -> None:
    parser = _parser()
    assert parser.parse_payment_notice("玩家A 扣减 100 蝌蚪\nOwner 接收 100 蝌蚪", None) == {
        "payer_name": "玩家A",
        "receiver_name": "Owner",
        "amount": 100,
        "unit": "蝌蚪",
    }
    assert parser.parse_payment_notice("付款人：A\n收款人：B\n金额：100", None) == {
        "payer_name": "A",
        "receiver_name": "B",
        "amount": 100,
    }
    assert parser.parse_payment_notice("", None) is None


def test_parser_is_pure_standard_library() -> None:
    tree = ast.parse((PLUGIN_DIR / "parser.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "re", "typing"}


def test_parse_function_is_verbatim_copy_of_host_when_available() -> None:
    repo = os.environ.get("TELEPILOT_REPO")
    host = Path(repo) / "backend/app/services/payment_notice_legacy.py" if repo else None
    if host is None or not host.is_file():
        import pytest

        pytest.skip("TELEPILOT_REPO 未指向含 payment_notice_legacy.py 的主仓库（0.111.0 之前的宿主没有该文件）")
    spec = importlib.util.spec_from_file_location("_host_payment_notice_legacy", host)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert inspect.getsource(module.parse_transfer_notice) == inspect.getsource(_parser().parse_transfer_notice)


def test_plugin_json_declares_payment_source() -> None:
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert raw["name"] == "payment-source-cn-transfer"
    assert [item["id"] for item in raw["payment_sources"]] == ["cn_transfer"]
    # 不订阅事件、不申请权限：旧宿主上装了也什么都不做。
    assert raw.get("event_subscriptions", []) == []
    assert raw["permissions"] == []


def test_async_wrapper_not_required() -> None:
    parser = _parser()
    assert not asyncio.iscoroutinefunction(parser.parse_payment_notice)
