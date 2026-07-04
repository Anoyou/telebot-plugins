"""近期发言回复测试插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_KEY = "reply_anchor_test"
PLUGIN_VERSION = "0.1.1"
ENTRY_KEY = "reply_to_recent_message"
DEFAULT_COMMAND = "send"
DEFAULT_SEARCH_LIMIT = 200

USAGE = (
    "用于验证 TelePilot 的 payout 近期发言锚点搜索能力。账号本人或授权管理员在已允许群里发送 "
    "`{prefix}{command} 用户ID 金额`，插件返回 `payout`，由平台 userbot 在当前群搜索该用户最近一次发言并回复 "
    "`+金额`。示例：`{prefix}send 123456789 88`。目标用户必须在当前群近期发过言；找不到锚点时，"
    "平台会让本次动作失败并在日志里记录原因，不会退化成普通群消息。"
)

EVENT_SUBSCRIPTIONS = [
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "filters": {"commands": [DEFAULT_COMMAND]},
        "entry_key": ENTRY_KEY,
        "description": "账号本人或授权管理员通过 UserBot 命令触发近期发言回复测试。",
    }
]

INTERACTION_ENTRIES = [
    {
        "key": ENTRY_KEY,
        "title": "近期发言回复测试",
        "description": "按用户 ID 在当前群搜索近期发言，并返回 payout 回复 +金额。",
        "interaction_profile": "utility_trigger",
        "launch_mode": "userbot_command",
        "session_scope": "chat",
        "events": ["command"],
        "preserve_command_trigger": True,
        "triggers": {"command": DEFAULT_COMMAND},
        "default_trigger_modes": "all",
        "payload_contract": {
            "required_envelope": ["source", "sender", "message", "trigger"],
            "required_event_fields": ["type", "chat_id", "message_id"],
        },
        "result_contract": {"actions": ["payout", "send_message", "result"]},
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
    }
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
    },
    "required": [],
}

MANIFEST = Manifest(
    key=PLUGIN_KEY,
    display_name="近期发言回复测试",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.49.6",
    author="Anoyou",
    description="测试 payout 按用户 ID 搜索当前群近期发言并回复 +金额。",
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
    "DEFAULT_SEARCH_LIMIT",
    "ENTRY_KEY",
    "EVENT_SUBSCRIPTIONS",
    "INTERACTION_ENTRIES",
    "MANIFEST",
    "PLUGIN_KEY",
    "PLUGIN_VERSION",
]
