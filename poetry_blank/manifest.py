"""诗词填空远程插件 Manifest。"""

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
            "default": "poetry",
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
            "title": "答题限时（秒）",
            "default": 120,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "reward", "timeout"],
}


MANIFEST = Manifest(
    key="poetry_blank",
    display_name="诗词填空",
    version="1.0.1",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="古诗词填空抢答，答对获奖",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
