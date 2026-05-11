from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "schema",
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
    version="1.0.0",
    min_telebot_version="0.10.2",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="迁移自 pagermaid_plugins/luckydraw，已适配 Telebot 远程插件标准结构。",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
