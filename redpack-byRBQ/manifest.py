from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "redpack",
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
    key="redpack-byRBQ",
    display_name="红包",
    version="1.1.0",
    min_telepilot_version="0.15.0",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="口令红包模块，支持文字红包、img 数学题图片红包、自动领取结算和高额转账确认",
    permissions=["send_message", "edit_message", "read_chat", "send_file", "delete_message"],
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
