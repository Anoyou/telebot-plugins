"""九宫格骰子竞猜远程插件 Manifest。"""

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
            "default": "dicegrid",
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
        "reward_unit": {
            "type": "string",
            "title": "奖励单位",
            "default": "积分",
            "minLength": 1,
            "maxLength": 20,
        },
        "timeout": {
            "type": "integer",
            "title": "答题限时（秒）",
            "default": 90,
            "minimum": 10,
            "maximum": 86400,
        },
        "auto_next": {
            "type": "boolean",
            "title": "结束后自动下一轮",
            "default": False,
        },
        "next_delay": {
            "type": "integer",
            "title": "下一轮延迟（秒）",
            "default": 3,
            "minimum": 1,
            "maximum": 60,
        },
    },
    "required": ["command", "reward", "reward_unit", "timeout", "auto_next", "next_delay"],
}


MANIFEST = Manifest(
    key="dice_grid_hunt",
    display_name="九宫格骰子竞猜",
    version="1.0.2",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="九宫格展示 9 组六骰结果，公布唯一目标点数，群内抢答格子赢奖励",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
