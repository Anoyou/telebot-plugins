from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        pass

    def register(cls):
        return cls

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules.setdefault("app.worker.plugins.base", base_module)

    spec = importlib.util.spec_from_file_location(
        "pt_promote_plugin_under_test",
        ROOT / "pt_promote" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin_module()


class PTPromoteMetaTest(unittest.TestCase):
    def test_extracts_subtitle_from_table_cell_pair(self) -> None:
        html = """
        <html>
          <head><title>备用标题 - 青娃PT</title></head>
          <body>
            <h1>A Record Of Mortal's Journey To Immortality S01 Part1</h1>
            <table>
              <tr>
                <td>副标题</td>
                <td>凡人修仙传 | S01E001-017 | 风起天南 | 4K HEVC</td>
              </tr>
            </table>
          </body>
        </html>
        """

        meta = plugin._extract_torrent_meta_from_html(html)

        self.assertEqual(meta["title"], "A Record Of Mortal's Journey To Immortality S01 Part1")
        self.assertEqual(meta["subtitle"], "凡人修仙传 | S01E001-017 | 风起天南 | 4K HEVC")

    def test_extracts_subtitle_from_next_plain_line(self) -> None:
        html = """
        <html><body>
          <h1>测试标题</h1>
          <div>副标题</div>
          <div>测试副标题</div>
        </body></html>
        """

        meta = plugin._extract_torrent_meta_from_html(html)

        self.assertEqual(meta["subtitle"], "测试副标题")

    def test_extracts_subtitle_from_api_alias_html(self) -> None:
        meta = plugin._extract_torrent_meta({"small_desc_html": "<span>测试副标题</span>"})

        self.assertEqual(meta["subtitle"], "测试副标题")

    def test_cleans_title_suffix_status(self) -> None:
        self.assertEqual(
            plugin._clean_html_title("A Record Of Mortal's Journey [ 免费 ] 剩余时间： 23时38分钟"),
            "A Record Of Mortal's Journey",
        )

    def test_renders_message_template_and_preserves_unknown_placeholders(self) -> None:
        text = plugin._render_template(
            "{icon} 种子 {torrent_id}：{missing}",
            {"icon": "✅", "torrent_id": "12345"},
        )

        self.assertEqual(text, "✅ 种子 12345：{missing}")

    def test_default_success_template_exposes_code_language_block(self) -> None:
        text = plugin._render_template(
            plugin.PROMOTE_SUCCESS_TEMPLATE_DEFAULT,
            {
                "torrent_header": "种子：测试标题（ID：32728）",
                "subtitle": "测试副标题",
                "params": "促销类型：Free\n促销时长：1 天",
                "cost": "8,000",
            },
        )

        self.assertIn("<b>种子：测试标题（ID：32728）</b>\n", text)
        self.assertIn('<pre><code class="language-副标题与促销明细">测试副标题\n', text)
        self.assertIn("促销类型：Free", text)
        self.assertIn("消耗：8,000 蝌蚪</code></pre>", text)

    def test_formats_details_as_expandable_blockquote(self) -> None:
        details = plugin._format_promotion_details(
            {"subtitle": "凡人修仙传 | S01E001-017"},
            "促销类型：Free\n促销时长：1 天",
            "1,234",
        )

        self.assertTrue(details.startswith("<blockquote expandable>"))
        self.assertTrue(details.endswith("</blockquote>"))
        self.assertIn(
            '<pre><code class="language-副标题与促销明细">凡人修仙传 | S01E001-017\n',
            details,
        )
        self.assertIn("凡人修仙传 | S01E001-017", details)
        self.assertNotIn("副标题：", details)
        self.assertNotIn("language-转账成功", details)
        self.assertIn("促销类型：Free", details)
        self.assertIn("消耗：1,234 蝌蚪", details)


if __name__ == "__main__":
    unittest.main()
