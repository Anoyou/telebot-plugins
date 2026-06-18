from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "gi2",
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
    key="gi2-byRBQ",
    display_name="gi2-byRBQ",
    version="1.0.3",
    min_telepilot_version="0.30.4",
    min_telebot_version="0.10.2",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="图片生成与改图插件，支持直接生图、回复图片改图、GI2 结果图原地继续编辑，并对高风险原图断流做诊断提示",
    permissions=["send_message", "edit_message", "read_chat"],

    category="utility",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
