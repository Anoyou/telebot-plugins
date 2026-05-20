"""骰子比大小远程插件 Manifest。"""

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
            "default": "dice",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "邀请限时（秒）",
            "default": 60,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


MANIFEST = Manifest(
    key="dice_battle",
    display_name="骰子比大小",
    version="1.0.3",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="群内骰子对战，两人各掷骰子比点数，支持下注",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_entries=[
        {
            "key": "start_dice_battle",
            "title": "开始骰子对战",
            "description": "由交互 Bot 在群内发起一局骰子比大小。",
            "session_scope": "chat",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "timeout": {
                        "type": "integer",
                        "title": "邀请限时（秒）",
                        "default": 60,
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
