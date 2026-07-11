"""随机福利插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


PLUGIN_KEY = "random_benefit"
PLUGIN_VERSION = "1.0.0"

REPLY_TEMPLATE_DEFAULT = "+1-6666"
TEMPLATE_PREVIEW_DEFAULT = "+1-6666"

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": "从当前账号的已允许会话中选择要开启随机福利的群组。选中群组默认开启监听，管理员可在群内发送 {prefix}{command} off 暂停、{prefix}{command} on 恢复、{prefix}{command} status 查看状态。插件会监听群友发言，并按配置概率随机引用其中一条消息回复自定义福利语。",
    "additionalProperties": False,
    "properties": {
        "usage_preview": {
            "type": "string",
            "title": "使用说明",
            "readOnly": True,
            "default": "1. 在“目标群组”中从已允许会话选择需要开启的群组。\n2. 群组默认开启随机福利；管理员可发送 {prefix}{command} off 暂停，发送 {prefix}{command} on 恢复。\n3. 插件会随机引用群友发言回复福利语，默认回复：+1-6666。",
            "description": "只读说明；实际系统前缀由 TelePilot 当前命令前缀决定。",
        },
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "随机福利",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "只填写命令本体，不要填写系统前缀。支持中文；用于 on/off/status 控制当前群组。",
        },
        "allowed_chat_ids": {
            "type": "array",
            "title": "目标群组",
            "items": {"type": "integer"},
            "default": [],
            "x-ui-widget": "allowed-peer-multi-select",
            "description": "从当前账号的已允许会话中选择需要开启随机福利的群组。留空表示不监听任何群组。",
        },
        "reply_template": {
            "type": "string",
            "title": "随机回复语",
            "default": REPLY_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 500,
            "description": "随机命中后引用发言发送的文本。支持占位符：{sender}、{user_id}、{chat_id}、{message}。",
        },
        "trigger_probability": {
            "type": "number",
            "title": "随机回复概率",
            "default": 0.05,
            "minimum": 0,
            "maximum": 1,
            "description": "每条普通发言触发回复的概率，0 表示不自动回复，1 表示每条都回复。",
        },
        "default_enabled": {
            "type": "boolean",
            "title": "选中群组默认开启",
            "default": True,
            "description": "开启后，配置页选中的群组无需再发送 on 指令即可生效；off 指令仍可临时暂停。",
        },
        "template_preview": {
            "type": "string",
            "title": "随机回复预览",
            "readOnly": True,
            "default": TEMPLATE_PREVIEW_DEFAULT,
            "description": "使用示例上下文渲染后的最终消息。仅用于配置预览，不会保存或发送。",
        },
    },
    "required": ["command", "allowed_chat_ids", "reply_template", "trigger_probability", "default_enabled"],
}

USAGE = "从配置页的已允许会话选择器中选择要开启随机福利的群组。选中群组默认监听群友发言，并按概率随机引用其中一条消息回复自定义福利语；管理员可发送 {prefix}{command} off 暂停当前群组，发送 {prefix}{command} on 恢复，发送 {prefix}{command} status 查看状态。"

EVENT_SUBSCRIPTIONS = [
    {
        "source": ["userbot"],
        "events": ["command"],
        "scope": "owner_only",
        "description": "账号主人或授权管理员通过 UserBot 命令开启、暂停或查看当前群组随机福利状态。",
    },
    {
        "source": ["userbot"],
        "events": ["message"],
        "scope": "all_allowed_chats",
        "entry_key": "random_benefit_message",
        "description": "监听已允许会话内的普通群友发言，按配置概率随机引用回复福利语。",
    },
]

CAPABILITIES = {}

MANIFEST = Manifest(
    key=PLUGIN_KEY,
    display_name="随机福利",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.33.0",
    author="Anoyou",
    description="监听指定群组发言，随机引用某条消息回复自定义福利语",
    usage=USAGE,
    category="interactive",
    permissions=["send_message", "read_chat"],
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    capabilities=CAPABILITIES,
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
