"""群消息总结远程模块 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-category": "utility",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "sum",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "default_count": {
            "type": "integer",
            "title": "默认总结消息数",
            "default": 100,
            "minimum": 1,
            "maximum": 1000,
        },
        "max_fetch_count": {
            "type": "integer",
            "title": "单次最多读取消息数",
            "default": 300,
            "minimum": 10,
            "maximum": 2000,
        },
        "default_provider": {
            "type": "string",
            "title": "默认 AI 配置名",
            "description": "可填 telepilot 直接使用 TelePilot 内置 AI，也可填通过 sum config add 创建的外部配置名。",
            "default": "telepilot",
        },
        "default_prompt": {
            "type": "string",
            "title": "默认总结提示词",
            "default": "请总结以下群聊消息的主要内容，提取关键话题和重要信息：",
            "x-ui-widget": "textarea",
            "minLength": 1,
            "maxLength": 4000,
        },
        "default_spoiler": {
            "type": "boolean",
            "title": "默认折叠显示",
            "default": False,
        },
        "timeout_seconds": {
            "type": "integer",
            "title": "AI 请求超时（秒）",
            "default": 60,
            "minimum": 10,
            "maximum": 600,
        },
        "reply_mode": {
            "type": "boolean",
            "title": "回复模式",
            "description": "开启后快捷总结会发送新消息；关闭后编辑原指令消息。",
            "default": True,
        },
        "max_output_length": {
            "type": "integer",
            "title": "最大输出字符数",
            "description": "0 表示不限制。",
            "default": 0,
            "minimum": 0,
            "maximum": 50000,
        },
        "default_push_target": {
            "type": "string",
            "title": "定时总结默认推送目标",
            "description": "为空时推送到任务来源聊天；可填 me、群 ID 或 @username。",
            "default": "",
        },
        "providers_json": {
            "type": "string",
            "title": "AI 配置 JSON（可选）",
            "description": "高级用法：用 JSON 预置 providers；也可通过 sum config 命令维护。",
            "default": "",
            "x-ui-widget": "textarea",
        },
    },
    "required": [
        "command",
        "default_count",
        "max_fetch_count",
        "default_provider",
        "default_prompt",
        "default_spoiler",
        "timeout_seconds",
        "reply_mode",
        "max_output_length",
        "default_push_target",
        "providers_json",
    ],
}


MANIFEST = Manifest(
    key="sum",
    display_name="群消息总结",
    version="1.1.0",
    min_telepilot_version="0.21.0",
    author="Anoyou",
    description="使用 TelePilot 内置 AI 或 OpenAI/Gemini 兼容接口总结群组消息，支持快捷总结与定时任务",
    permissions=["send_message", "edit_message", "read_chat"],
    category="utility",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
