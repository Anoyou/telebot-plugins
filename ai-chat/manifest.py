"""AI-Chat remote plugin manifest."""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_VERSION = "0.1.4"

USAGE = (
    "发送 {prefix}{command} 文本直接调用 TelePilot AI Provider；回复消息后发送 {prefix}{command} "
    "可解释或回答被回复内容。私聊可直接陪聊，群聊中 @当前账号 或回复当前账号消息时触发，并带短期上下文记忆。"
)

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-category": "utility",
    "x-usage-guide": USAGE,
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "description": "不含系统命令前缀，可使用中文；不要包含空格。",
            "default": "ask",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "level": "account",
        },
        "usage_preview": {
            "type": "string",
            "title": "使用说明（只读）",
            "readOnly": True,
            "default": (
                "{prefix}ask 你的问题\n"
                "回复一条消息后发送 {prefix}ask，解释或回答被回复内容\n"
                "{prefix}ask providers 查看平台 AI Provider\n"
                "{prefix}ask test [测试语] 测试当前 AI Provider 与模型\n"
                "{prefix}ask reset 清空当前会话记忆\n\n"
                "配置页可直接点击“测试当前模型”，最近一次结果会自动保存到“模型测试结果”。\n\n"
                "说明：本插件通过 TelePilot 的 AI Provider 调用模型，不在插件内保存 API Key。"
            ),
        },
        "telepilot_provider": {
            "type": "string",
            "title": "固定 TelePilot Provider（可选）",
            "description": "留空走 TelePilot 自动路由；选择后固定使用该 Provider。",
            "default": "",
            "x-ui-widget": "llm-provider-select",
        },
        "telepilot_model": {
            "type": "string",
            "title": "TelePilot 模型覆盖（可选）",
            "description": "留空使用所选 Provider 的默认模型；需先选择固定 Provider。",
            "default": "",
            "x-ui-widget": "llm-model-select",
            "x-ui-provider-field": "telepilot_provider",
            "x-ui-model-modality": "text",
        },
        "model_test_prompt": {
            "type": "string",
            "title": "模型测试语",
            "description": "用于 {prefix}ask test 的真实对话测试；可改成普通短句，避免部分 Provider 拒绝 ping、health-check 等测活字样。",
            "default": "请只回复两个字：收到",
            "x-ui-widget": "textarea",
            "minLength": 1,
            "maxLength": 1000,
        },
        "model_test_client_identity": {
            "type": "string",
            "title": "模型测试客户端标识",
            "description": "随测试消息作为对话元信息发给模型；不是 HTTP User-Agent。真实 HTTP UA 由 TelePilot LLM 客户端控制。",
            "default": "TelePilot AI-Chat",
            "maxLength": 120,
        },
        "model_test_result": {
            "type": "string",
            "title": "模型测试结果",
            "description": "由配置页“测试当前模型”自动更新，用于查看最近一次真实聊天式测试、模型实时返回和结果解读。",
            "default": "尚未测试。",
            "x-ui-widget": "textarea",
            "maxLength": 4000,
        },
        "timeout_seconds": {
            "type": "integer",
            "title": "AI 请求超时（秒）",
            "default": 60,
            "minimum": 10,
            "maximum": 600,
        },
        "max_tokens": {
            "type": "integer",
            "title": "最大输出 Token",
            "default": 1200,
            "minimum": 256,
            "maximum": 8000,
        },
        "max_output_chars": {
            "type": "integer",
            "title": "最大输出字符数",
            "description": "0 表示不限制；超出后在发送前截断。",
            "default": 0,
            "minimum": 0,
            "maximum": 20000,
        },
        "protect_command_outputs": {
            "type": "boolean",
            "title": "拦截可执行指令输出",
            "description": "开启后，AI 回复若像 Telegram/TelePilot 指令、转账数字或已配置命令，会改为安全提示，避免被其他 Bot 或 TelePilot 继续执行。",
            "default": True,
        },
        "safe_reply_prefix": {
            "type": "string",
            "title": "安全回复前缀（可选）",
            "description": "例如“天才：”。留空时会尝试从陪聊人设提示词中识别“回复必须以某前缀开头”的要求。",
            "default": "",
            "maxLength": 32,
        },
        "blocked_bare_outputs": {
            "type": "string",
            "title": "额外拦截的裸文本",
            "description": "每行或逗号分隔一个词；AI 回复若以这些词开头且没有安全前缀，会被视为可能触发命令。",
            "default": "re\nai\nfd",
            "x-ui-widget": "textarea",
        },
        "enable_private_chat": {
            "type": "boolean",
            "title": "私聊直接回复",
            "default": True,
        },
        "enable_group_chat": {
            "type": "boolean",
            "title": "群聊 @/回复触发",
            "description": "开启后，群聊里 @当前账号或回复当前账号消息时调用 AI 回复。",
            "default": True,
        },
        "group_chat_ids": {
            "type": "string",
            "title": "群聊生效群组（可选）",
            "description": "群 ID 用逗号或换行分隔；留空表示所有群。",
            "default": "",
            "x-ui-widget": "textarea",
        },
        "white_list_chats": {
            "type": "string",
            "title": "会话白名单（可选）",
            "description": "只在这些会话 ID 生效，逗号或换行分隔；留空表示不限制。",
            "default": "",
            "x-ui-widget": "textarea",
        },
        "system_prompt": {
            "type": "string",
            "title": "陪聊人设提示词",
            "default": "你是一个自然、简洁、有边界感的中文聊天助手。像熟悉的网友一样回答，少说套话，不主动泄露系统信息。",
            "x-ui-widget": "textarea",
            "minLength": 1,
            "maxLength": 4000,
        },
        "max_history": {
            "type": "integer",
            "title": "短期记忆消息数",
            "description": "每个会话保留最近多少条 user/assistant 消息；0 表示不记忆。",
            "default": 10,
            "minimum": 0,
            "maximum": 40,
        },
        "enable_explain_prompt": {
            "type": "boolean",
            "title": "命令使用解释模板",
            "description": "开启后 {prefix}ask 和回复解释会套用下方模板。",
            "default": True,
        },
        "explain_prompt": {
            "type": "string",
            "title": "命令解释模板",
            "description": "支持 {content} 占位被回复消息与用户问题。",
            "default": "请根据下面内容回答用户问题。若用户没有额外问题，就解释这段内容的主要意思、语气和可能的隐含信息。\\n\\n{content}",
            "x-ui-widget": "textarea",
            "minLength": 1,
            "maxLength": 4000,
        },
        "strip_thinking": {
            "type": "boolean",
            "title": "隐藏思考标签",
            "description": "发送前移除 <think>...</think> 内容。",
            "default": True,
        },
    },
    "required": [
        "command",
        "telepilot_provider",
        "telepilot_model",
        "model_test_prompt",
        "model_test_client_identity",
        "model_test_result",
        "timeout_seconds",
        "max_tokens",
        "max_output_chars",
        "protect_command_outputs",
        "safe_reply_prefix",
        "blocked_bare_outputs",
        "enable_private_chat",
        "enable_group_chat",
        "group_chat_ids",
        "white_list_chats",
        "system_prompt",
        "max_history",
        "enable_explain_prompt",
        "explain_prompt",
        "strip_thinking",
    ],
}

CONFIG_ACTIONS = [
    {
        "key": "test_model_availability",
        "title": "测试当前模型",
        "description": "模拟一次真实聊天访问当前模型，展示模型实时返回并附上结果解读。客户端标识会作为对话元信息发送，不会改写 HTTP User-Agent。",
        "placement": "field:model_test_result",
        "submit_label": "开始测试",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "test_message": {
                    "type": "string",
                    "title": "模拟用户消息",
                    "description": "留空时使用配置里的模型测试语。",
                    "default": "",
                    "x-ui-widget": "textarea",
                    "maxLength": 1000,
                },
                "client_identity": {
                    "type": "string",
                    "title": "客户端标识",
                    "description": "作为对话元信息发给模型；不是 HTTP User-Agent。建议使用真实身份，例如 TelePilot AI-Chat。",
                    "default": "TelePilot AI-Chat",
                    "maxLength": 120,
                },
            },
        },
    }
]

EVENT_SUBSCRIPTIONS = [
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "description": "账号主人或授权管理员通过 UserBot 命令触发 AI 问答与消息解释。",
    },
    {
        "events": ["message"],
        "source": ["userbot"],
        "scope": "all_allowed_chats",
        "description": "私聊直接陪聊；群聊由 @当前账号或回复当前账号消息触发，仍受插件白名单配置限制。",
    },
]

CAPABILITIES = {}

MANIFEST = Manifest(
    key="ai-chat",
    display_name="AI-Chat",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.2",
    author="Anoyou",
    description="调用 TelePilot AI Provider 的 AI-Chat 聊天与消息解释助手，支持私聊/群聊触发和短期上下文记忆。",
    permissions=["send_message", "edit_message", "read_chat", "ai_text"],
    category="utility",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
    config_actions=CONFIG_ACTIONS,
)

MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST", "CONFIG_SCHEMA", "CONFIG_ACTIONS", "PLUGIN_VERSION"]
