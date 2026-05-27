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
        "usage_preview": {
            "type": "string",
            "title": "使用说明预览（只读）",
            "readOnly": True,
            "default": (
                "常用命令：\n"
                "{prefix}sum\n"
                "{prefix}sum 100\n"
                "{prefix}sum 1h\n"
                "{prefix}sum --time 90m\n"
                "{prefix}sum 500 --cy\n"
                "{prefix}sum 100 1h --cy\n\n"
                "说明：sum 只调用 TelePilot 已配置的 AI Provider；一般保持自动路由即可。"
            ),
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
        "telepilot_provider": {
            "type": "string",
            "title": "固定 TelePilot Provider（高级，可选）",
            "description": "留空走 TelePilot 自动路由；选择后固定使用该 Provider。",
            "default": "",
            "x-ui-widget": "llm-provider-select",
        },
        "telepilot_model": {
            "type": "string",
            "title": "TelePilot 模型覆盖（高级，可选）",
            "description": "留空使用所选 Provider 的默认模型；需先选择固定 Provider。",
            "default": "",
            "x-ui-widget": "llm-model-select",
            "x-ui-provider-field": "telepilot_provider",
            "x-ui-model-modality": "text",
        },
        "template_placeholders": {
            "type": "string",
            "title": "结果模板占位符（只读）",
            "readOnly": True,
            "default": (
                "{summary} 总结正文（保留 AI 返回的 HTML）\n"
                "{chat_display} 聊天显示名（回退 chat_id）\n"
                "{chat_id} 聊天 ID\n"
                "{time} 生成时间（yyyy-mm-dd HH:MM）\n"
                "{message_count} 本次总结消息数"
            ),
        },
        "message_template": {
            "type": "string",
            "title": "结果输出模板",
            "description": "支持占位符：{summary}、{chat_display}、{chat_id}、{time}、{message_count}",
            "default": "📊 群组总结\n来源: {chat_display}\n时间: {time}\n数量: {message_count}\n\n{summary}",
            "x-ui-widget": "textarea",
            "minLength": 1,
            "maxLength": 8000,
        },
        "template_preview": {
            "type": "string",
            "title": "结果模板预览（只读）",
            "description": "使用固定示例值渲染最终消息，仅用于配置预览。",
            "readOnly": True,
            "default": "📊 群组总结\n来源: {chat_display}\n时间: {time}\n数量: {message_count}\n\n{summary}",
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
        "scheduled_tasks_json": {
            "type": "string",
            "title": "定时总结任务 JSON（可选）",
            "description": "配置页维护的定时任务列表；留空或 [] 表示不使用配置页定时任务。示例：[{\n  \"id\": \"daily\",\n  \"chatId\": \"-1001234567890\",\n  \"interval\": \"2h\",\n  \"messageCount\": 100,\n  \"pushTarget\": \"\",\n  \"disabled\": false,\n  \"remark\": \"工作群总结\"\n}]",
            "default": "[]",
            "x-ui-widget": "textarea",
        },
    },
    "required": [
        "command",
        "default_count",
        "max_fetch_count",
        "message_template",
        "default_prompt",
        "default_spoiler",
        "timeout_seconds",
        "reply_mode",
        "max_output_length",
        "default_push_target",
        "scheduled_tasks_json",
    ],
}


MANIFEST = Manifest(
    key="sum",
    display_name="群消息总结",
    version="1.1.25",
    min_telepilot_version="0.24.2",
    author="Anoyou",
    description="调用 TelePilot 已配置的 AI 总结群组消息，支持快捷总结与可配置定时任务",
    permissions=["send_message", "edit_message", "read_chat", "resolve_entity", "ai_text", "send_file", "delete_message"],
    category="utility",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
