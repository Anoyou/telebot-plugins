from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "ldraw",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$"
        },
        "enabled": {
            "type": "boolean",
            "title": "是否启用",
            "default": True
        }
    },
    "required": ["command", "enabled"]
}

MANIFEST = Manifest(
    key="luckydraw-byRBQ",
    display_name="luckydraw-byRBQ",
    version="1.0.3",
    min_telepilot_version="0.30.4",
    min_telebot_version="0.10.2",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="# 自动抽奖插件 (LuckyDraw) 在指定群组中自动识别红包/抽奖活动并发送口令参与，支持机...",
    permissions=["send_message", "edit_message", "read_chat"],

    category="automation",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
