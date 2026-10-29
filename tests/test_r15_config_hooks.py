"""第 15 轮：配置页适配（on_config_validate 保存校验 + x-ui-visible-if 按开关显隐）。

- 旧平台不调用 on_config_validate、不认识 x-ui-visible-if（字段照常显示），运行时行为不变；
- 显隐条件统一写 not_in：只有开关被明确关掉 / 选了无关选项时才隐藏，值缺省或在另一作用域时照常显示。
"""

from __future__ import annotations

import asyncio
import json
import types
import unittest
from pathlib import Path

import test_interaction_ai_chat as iac_base
import test_private_chat_assistant as pca_tests
import test_todo_reminder as todo_tests

ROOT = Path(__file__).resolve().parents[1]


def _schema(key: str) -> dict:
    return json.loads((ROOT / key / "plugin.json").read_text(encoding="utf-8"))["config_schema"]


def _visible(field: dict, values: dict) -> bool:
    """与 Web pluginRules.isFieldVisible / iOS PluginRuleForm.isVisible 同一判定。"""

    cond = field.get("x-ui-visible-if")
    if not isinstance(cond, dict) or not isinstance(cond.get("field"), str):
        return True
    actual = values.get(cond["field"])

    def same(a, b):
        return a == b or (a is not None and b is not None and str(a) == str(b))

    if "equals" in cond:
        return same(actual, cond["equals"])
    if isinstance(cond.get("in"), list):
        return any(same(actual, item) for item in cond["in"])
    if isinstance(cond.get("not_in"), list):
        return not any(same(actual, item) for item in cond["not_in"])
    return True


class VisibleIfTest(unittest.TestCase):
    CASES = {
        "interaction-ai-chat": [("pt_cookie", "pt_enabled", False), ("keyword_burst_users", "keyword_burst_guard", False),
                                ("allowed_user_ids", "access_mode", "所有人"), ("payment_guard_reply", "protect_command_outputs", False)],
        "ai-chat": [("explain_prompt", "enable_explain_prompt", False), ("group_chat_ids", "enable_group_chat", False)],
        "todo_reminder": [("auto_delete_delay_seconds", "auto_delete_enabled", False)],
        "bot_mute_guard": [("mute_duration_seconds", "violation_action", "delete_only")],
    }

    def test_hidden_only_when_explicitly_off_and_controller_is_sibling(self) -> None:
        for key, cases in self.CASES.items():
            props = _schema(key)["properties"]
            for field, controller, off in cases:
                with self.subTest(plugin=key, field=field):
                    self.assertIn(controller, props)
                    spec = props[field]
                    self.assertNotIn(field, _schema(key).get("required") or [])
                    self.assertFalse(_visible(spec, {controller: off}))
                    self.assertTrue(_visible(spec, {}))  # 缺省 / 另一作用域：照常显示

    def test_manifest_and_plugin_json_agree(self) -> None:
        for key in self.CASES:
            text = (ROOT / key / "manifest.py").read_text(encoding="utf-8")
            json_count = json.dumps(_schema(key), ensure_ascii=False).count("x-ui-visible-if")
            self.assertEqual(text.count("x-ui-visible-if"), json_count, key)


class ConfigValidateTest(unittest.TestCase):
    def test_interaction_ai_chat_site_urls(self) -> None:
        iac_base._install_stubs()
        plugin = iac_base.PLUGIN.InteractionAIChatPlugin()
        run = lambda cfg: asyncio.run(plugin.on_config_validate(types.SimpleNamespace(), cfg, "account"))  # noqa: E731
        self.assertIsNone(run({}))
        self.assertIsNone(run({"pt_enabled": True, "pt_site_url": "pt.example.com", "wiki_base_url": "https://wiki.example.com/docs"}))
        bad = run({"pt_enabled": True, "pt_site_url": "http://pt.example.com", "wiki_base_url": "https://wiki.example.com/other"})
        self.assertEqual(set(bad["errors"]), {"pt_site_url", "wiki_base_url"})
        self.assertIsNone(run({"pt_enabled": False, "pt_site_url": "http://pt.example.com"}))  # PT 关掉时不拦

    def test_todo_reminder_timezone(self) -> None:
        plugin = todo_tests.plugin_module.TodoReminderPlugin()
        run = lambda cfg: asyncio.run(plugin.on_config_validate(types.SimpleNamespace(), cfg, "account"))  # noqa: E731
        self.assertIsNone(run({"timezone": "Asia/Shanghai"}))
        self.assertIsNone(run({}))
        self.assertIn("timezone", run({"timezone": "Mars/Base"})["errors"])

    def test_private_chat_assistant_bot_token(self) -> None:
        plugin = pca_tests.plugin_module.PrivateChatAssistantPlugin()
        run = lambda cfg: asyncio.run(plugin.on_config_validate(types.SimpleNamespace(), cfg, "global"))  # noqa: E731
        self.assertIsNone(run({}))
        self.assertIsNone(run({"bot_token": "123456:" + "A" * 35}))
        for bad in ("abc", "123456", "abc:" + "A" * 35, "123:short"):
            self.assertIn("bot_token", run({"bot_token": bad})["errors"], bad)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
