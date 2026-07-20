"""近期发言回复测试插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_KEY = "reply_anchor_test"
PLUGIN_VERSION = "0.1.5"
ENTRY_KEY = "reply_to_recent_message"
NAME_ENTRY_KEY = "resolve_public_name"
DEFAULT_COMMAND = "send"
NAME_COMMAND = "name"
DEFAULT_NAME_RESULT_TEMPLATE = (
    "TelePilot 公开姓名解析结果\n"
    "公开姓名：{display_name}\n"
    "身份状态：{identity_status}\n"
    "管理员/成员标签：{tag}\n"
    "解析状态：{resolved_status}"
)
DEFAULT_SEARCH_LIMIT = 200

USAGE = (
    "用于验证 TelePilot 的 payout 近期发言锚点搜索能力。账号本人或授权管理员在已允许群里发送 "
    "`{prefix}{command} 用户ID 金额`，插件返回 `payout`，由平台 userbot 在当前群搜索该用户最近一次发言并回复 "
    "`+金额`。示例：`{prefix}send 123456789 88`。目标用户必须在当前群近期发过言；找不到锚点时，"
    "平台会让本次动作失败并在日志里记录原因，不会退化成普通群消息。"
    "独立发送 `{prefix}name 用户ID`，或回复目标用户消息后发送 `{prefix}name`，可测试 TelePilot 实际解析出的"
    "安全公开姓名、匿名状态、标签和解析状态；"
    "该入口不发奖，也不搜索近期发言。支持在配置页编辑姓名解析结果模板。"
)

EVENT_SUBSCRIPTIONS = [
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "filters": {"commands": [DEFAULT_COMMAND]},
        "entry_key": ENTRY_KEY,
        "description": "账号本人或授权管理员通过 UserBot 命令触发近期发言回复测试。",
    },
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "filters": {"commands": [NAME_COMMAND]},
        "entry_key": NAME_ENTRY_KEY,
        "description": "账号本人或授权管理员通过 UserBot 命令测试公开姓名解析。",
    },
]

INTERACTION_ENTRIES = [
    {
        "key": ENTRY_KEY,
        "title": "近期发言回复测试",
        "description": "按用户 ID 在当前群搜索近期发言，并返回 payout 回复 +金额。",
        "interaction_profile": "utility_trigger",
        "launch_mode": "userbot_command",
        "session_scope": "none",
        "events": ["command"],
        "preserve_command_trigger": True,
        "triggers": {"command": DEFAULT_COMMAND},
        "default_trigger_modes": "all",
        "payload_contract": {
            "required_envelope": ["source", "sender", "message", "trigger"],
            "required_event_fields": ["type", "chat_id", "message_id"],
        },
        "result_contract": {"actions": ["payout", "send_message", "result", "end_session"]},
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "user_id": {
                    "type": "integer",
                    "title": "目标用户 ID",
                    "description": "命令参数中的第一个数字。",
                },
                "amount": {
                    "type": "integer",
                    "title": "回复金额",
                    "description": "命令参数中的第二个数字；实际回复文本为 +金额。",
                    "minimum": 1,
                },
            },
            "required": ["user_id", "amount"],
        },
    },
    {
        "key": NAME_ENTRY_KEY,
        "title": "公开姓名解析测试",
        "description": "回复目标用户消息或提供用户 ID，获取 TelePilot 清洗后的安全公开姓名、匿名状态和标签。",
        "interaction_profile": "utility_trigger",
        "launch_mode": "userbot_command",
        "session_scope": "none",
        "events": ["command"],
        "preserve_command_trigger": True,
        "triggers": {"command": NAME_COMMAND},
        "default_trigger_modes": "all",
        "payload_contract": {
            "required_envelope": ["source", "sender", "message", "trigger"],
            "required_event_fields": ["type", "chat_id", "message_id"],
        },
        "result_contract": {"actions": ["send_message", "result", "end_session"]},
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "user_id": {
                    "type": "integer",
                    "title": "目标用户 ID",
                    "description": "可选；未填写时从被回复的真实用户消息读取。",
                },
            },
            "required": [],
        },
    },
]

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
            "description": "默认 send；只填写命令本体，不要带系统命令前缀。",
            "default": DEFAULT_COMMAND,
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "level": "account",
        },
        "reply_to_search_limit": {
            "type": "integer",
            "title": "近期消息搜索条数",
            "description": "userbot 最多向前搜索多少条群消息来寻找目标用户最近一次发言。",
            "default": DEFAULT_SEARCH_LIMIT,
            "minimum": 1,
            "maximum": 500,
            "level": "account",
        },
        "name_template_placeholders": {
            "type": "string",
            "title": "姓名解析模板占位符（只读）",
            "readOnly": True,
            "default": (
                "{display_name} 清洗后的安全公开姓名\n"
                "{identity_status} 匿名管理员、非匿名公开身份或安全回退状态\n"
                "{tag} 清洗后的管理员/成员标签，无标签时为“无”\n"
                "{resolved_status} 已确认或未确认"
            ),
        },
        "name_result_template": {
            "type": "string",
            "title": "公开姓名解析结果模板",
            "description": "用于 name 测试命令的回复。只提供安全公开字段，不提供原始姓名。",
            "default": DEFAULT_NAME_RESULT_TEMPLATE,
            "x-ui-widget": "textarea",
            "minLength": 1,
            "maxLength": 2000,
            "level": "account",
        },
        "name_result_preview": {
            "type": "string",
            "title": "公开姓名解析结果预览（只读）",
            "description": "使用固定示例值展示默认模板效果。",
            "readOnly": True,
            "default": (
                "TelePilot 公开姓名解析结果\n"
                "公开姓名：示例用户\n"
                "身份状态：非匿名公开身份\n"
                "管理员/成员标签：无\n"
                "解析状态：已确认"
            ),
        },
    },
    "required": [],
}

MANIFEST = Manifest(
    key=PLUGIN_KEY,
    display_name="近期发言回复测试",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.70.9",
    author="Anoyou",
    description="测试近期发言回复和 TelePilot 安全公开姓名解析。",
    usage=USAGE,
    category="utility",
    permissions=["send_message", "read_chat"],
    config_schema=CONFIG_SCHEMA,
    interaction_entries=INTERACTION_ENTRIES,
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    interaction_profile="utility_trigger",
    capabilities={},
)

__all__ = [
    "CONFIG_SCHEMA",
    "DEFAULT_COMMAND",
    "DEFAULT_NAME_RESULT_TEMPLATE",
    "DEFAULT_SEARCH_LIMIT",
    "ENTRY_KEY",
    "EVENT_SUBSCRIPTIONS",
    "INTERACTION_ENTRIES",
    "MANIFEST",
    "NAME_COMMAND",
    "NAME_ENTRY_KEY",
    "PLUGIN_KEY",
    "PLUGIN_VERSION",
]
