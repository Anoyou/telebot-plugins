"""sum 1.1.38：声明式卡片「定时总结任务」（TelePilot 0.110.0+ ui.cards / on_ui_card）。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path

from test_sum_persistence import ROOT, plugin_module


class SumUiCardTest(unittest.TestCase):
    def _card(self, data_dir):
        plugin = plugin_module.SummaryPlugin()
        ctx = types.SimpleNamespace(data_dir=data_dir, config={})
        return asyncio.run(plugin.on_ui_card(ctx, "scheduled_tasks", {"slot": "plugin.overview", "account_id": None}))

    def test_card_reads_saved_tasks_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / plugin_module.DB_FILE_NAME
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {"id": "1", "chatId": "-1001", "chatDisplay": "测试群", "interval": "1d", "lastRunAt": "1767225600000", "lastResult": "ok"},
                            {"id": "2", "chatId": "-1002", "cron": "0 9 * * *", "lastError": "无权限", "pushTarget": "me"},
                            {"id": "3", "chatId": "-1003", "interval": "6h", "disabled": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            before = path.read_text(encoding="utf-8")
            data = self._card(tmp)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual((data["total"], data["enabled"], data["errors"]), (3, 2, 1))
        first, second, third = data["rows"]
        self.assertEqual((first["chat"], first["schedule"], first["push"], first["status"]), ("测试群", "1d", "原群", "正常"))
        self.assertEqual(first["last_run"], 1767225600)
        self.assertEqual((second["schedule"], second["push"], second["status"], second["note"]), ("0 9 * * *", "me", "出错", "无权限"))
        self.assertEqual(third["status"], "已停用")

    def test_missing_or_broken_file_gives_empty_card_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._card(tmp), {"total": 0, "enabled": 0, "errors": 0, "rows": []})
            self.assertFalse((Path(tmp) / plugin_module.DB_FILE_NAME).exists())
            (Path(tmp) / plugin_module.DB_FILE_NAME).write_text("{oops", encoding="utf-8")
            self.assertEqual(self._card(tmp)["rows"], [])
        self.assertEqual(self._card(None)["total"], 0)

    def test_plugin_json_declares_overview_card(self) -> None:
        raw = json.loads((ROOT / "sum" / "plugin.json").read_text(encoding="utf-8"))
        card = raw["ui"]["cards"][0]
        self.assertEqual((card["id"], card["slot"], card["source"]), ("scheduled_tasks", "plugin.overview", {"action": "scheduled_tasks"}))
        table = next(block for block in card["blocks"] if block["type"] == "table")
        keys = {column["key"] for column in table["columns"]}
        self.assertLessEqual(keys, {"id", "chat", "schedule", "push", "last_run", "status", "note"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
