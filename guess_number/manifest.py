"""猜数字远程插件 Manifest。"""

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
            "default": "guess",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "答题限时（秒）",
            "default": 300,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


MANIFEST = Manifest(
    key="guess_number",
    display_name="猜数字",
    version="1.0.3",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="群内猜数字游戏，系统随机一个数字，群友轮流猜，提示大了/小了",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_entries=[
        {
            "key": "start_guess_number",
            "title": "开始猜数字",
            "description": "由交互 Bot 在群内开启一局猜数字游戏。",
            "session_scope": "chat",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "timeout": {
                        "type": "integer",
                        "title": "答题限时（秒）",
                        "default": 300,
                        "minimum": 10,
                        "maximum": 86400
                    }
                },
            },
        }
    ],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
