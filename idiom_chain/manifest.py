"""成语接龙远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "schema",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "cy",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "reward": {
            "type": "integer",
            "title": "奖励数值",
            "default": 10,
            "minimum": 0,
            "maximum": 1000000,
        },
        "timeout": {
            "type": "integer",
            "title": "接龙限时（秒）",
            "default": 120,
            "minimum": 10,
            "maximum": 86400,
        },
        "forbidden_words": {
            "type": "array",
            "title": "禁词列表",
            "default": [],
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
            },
        },
    },
    "required": ["command", "reward", "timeout", "forbidden_words"],
}


MANIFEST = Manifest(
    key="idiom_chain",
    display_name="成语接龙",
    version="1.0.1",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="群内成语接龙，第一个答对的获奖，支持禁词规则",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
