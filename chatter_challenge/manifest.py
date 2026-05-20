"""话痨挑战远程插件 Manifest。"""

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
            "default": "chat",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
    },
    "required": ["command"],
}


MANIFEST = Manifest(
    key="chatter_challenge",
    display_name="话痨挑战",
    version="1.0.2",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="设定聊天规则，违反者自动扣分，全程被动监听",
    permissions=["send_message", "edit_message", "read_chat"],

    category="automation",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
