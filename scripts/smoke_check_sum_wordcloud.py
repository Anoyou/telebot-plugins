#!/usr/bin/env python3
"""sum 词云中文取词冒烟检查。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load_sum_module() -> Any:
    command = types.ModuleType("app.worker.command")
    command.current_command_prefix = lambda fallback=",": "。"

    base = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        pass

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = lambda cls: cls

    sys.modules["app"] = types.ModuleType("app")
    sys.modules["app.worker"] = types.ModuleType("app.worker")
    sys.modules["app.worker.command"] = command
    sys.modules["app.worker.plugins"] = types.ModuleType("app.worker.plugins")
    sys.modules["app.worker.plugins.base"] = base

    spec = importlib.util.spec_from_file_location("sum_plugin", ROOT / "sum" / "plugin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = _load_sum_module()
    plugin = module.SummaryPlugin()
    plugin._command = "sum"
    texts = [
        "蝌蚪 射出 阳光普照 答案了的 认转账联 即可参与 有答案了 你心里已经有 经有答案 消息回复 娱乐模块 对收款人 默认转账",
        "答案了的 里已经有 认转账联 账联动是 是付费娱 今日份阳 动是付费 请对收款 心里已经 红包 奖励",
        "转账联动 默认转账 付费娱乐 娱乐模块 消息回复 对收款人 showmaker",
    ]
    messages = [module.MessageData(text, text, "") for text in texts]
    counts = plugin._collect_wordcloud_counts(messages)
    bad_words = {
        "答案了的",
        "里已经有",
        "认转账联",
        "账联动是",
        "是付费娱",
        "今日份阳",
        "动是付费",
        "请对收款",
        "心里已经",
    }
    leaked = sorted(word for word in bad_words if word in counts)
    assert not leaked, f"词云候选仍包含滑窗碎片: {leaked}"
    for expected in ("娱乐模块", "默认转账", "消息回复", "阳光普照"):
        assert expected in counts, f"缺少预期关键词: {expected}"

    image_data, error = plugin._render_wordcloud_png(messages)
    assert image_data, f"词云 PNG 生成失败: {error}"
    print("sum wordcloud smoke: PASS")
    print("top words:", counts.most_common(12))
    print("png bytes:", len(image_data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
