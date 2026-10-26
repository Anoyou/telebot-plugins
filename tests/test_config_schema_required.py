"""配置 schema 的 required 只能列出真正必填的字段。

App/Web 会给 required 字段标“必填”。有默认值的字段运行时会按 schema 默认值补齐，
留空也能工作，不应列为必填；标题/说明写“可选”的字段更不能出现在 required 里。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 有默认值但留空时插件无法工作的字段，作为真正必填保留。
ALLOWED_REQUIRED_WITH_DEFAULT = {
    ("pt_promote", "cookie"),  # 站点 Cookie，默认空串仅为表单占位
    ("private_chat_assistant", "bot_token"),  # 默认空串不满足 minLength=1
}


def _plugin_schemas():
    for path in sorted(ROOT.glob("*/plugin.json")):
        schema = json.loads(path.read_text(encoding="utf-8")).get("config_schema")
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            yield path.parent.name, schema


@pytest.mark.parametrize(("plugin", "schema"), list(_plugin_schemas()), ids=lambda v: v if isinstance(v, str) else "")
def test_required_lists_only_truly_required_fields(plugin: str, schema: dict) -> None:
    properties = schema["properties"]
    problems = []
    for key in schema.get("required") or []:
        field = properties.get(key)
        if field is None:
            problems.append(f"{key}: 不在 properties 里")
            continue
        if "可选" in str(field.get("title", "")):
            problems.append(f"{key}: 标题写“可选”却在 required")
        if field.get("readOnly"):
            problems.append(f"{key}: 只读字段不应 required")
        if field.get("default") is not None and (plugin, key) not in ALLOWED_REQUIRED_WITH_DEFAULT:
            problems.append(f"{key}: 有默认值 {field['default']!r}，留空会按默认值处理，不应 required")
    assert not problems, f"{plugin}: " + "; ".join(problems)
