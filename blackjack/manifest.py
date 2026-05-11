"""21点远程插件 Manifest。"""

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
            "default": "bj",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "牌局限时（秒）",
            "default": 120,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


MANIFEST = Manifest(
    key="blackjack",
    display_name="21点",
    version="1.0.1",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="经典21点纸牌游戏，群内庄家模式，支持要牌/停牌/加倍",
    permissions=["send_message", "edit_message", "read_chat", "delete_message"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
