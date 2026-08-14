"""随机福利插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


PLUGIN_KEY = "random_benefit"
PLUGIN_VERSION = "1.5.3"

REPLY_TEMPLATE_DEFAULT = "+1-6666"
TEMPLATE_PREVIEW_DEFAULT = "+1-6666"

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": "从当前账号的已允许会话中选择要开启随机福利的群组。选中群组默认纳入作用范围；管理员可在群内发送独立配置的开启指令、暂停指令和状态指令控制当前群组。插件只会从普通群友发言中随机命中，会排除当前账号自己的消息和 Bot 账号消息。为防刷屏，命中回复后会进入全群冷却和同一用户冷却，冷却秒数设为 0 可关闭。需要低延时裸直通时，请在本配置页单独开启账号级二次开关；关闭后标准 Event Bus 入口仍可运行。",
    "additionalProperties": False,
    "properties": {
        "usage_preview": {
            "type": "string",
            "title": "使用说明",
            "readOnly": True,
            "default": "1. 在“目标群组”中从已允许会话选择需要纳入随机福利作用范围的群组。\n2. 群组默认开启；管理员可在群内发送 {prefix}福利暂停 暂停，发送 {prefix}福利开启 恢复，发送 {prefix}福利状态 查看状态。三个指令名都可单独自定义。\n3. 插件会随机引用普通群友发言回复福利语，默认回复：+1-6666；当前账号自己的消息和 Bot 账号消息会被排除。\n4. 为防刷屏，默认每个群 30 秒最多回复一次，同一用户 120 秒最多触发一次；对应冷却秒数设为 0 可关闭。\n5. 需要低延时裸直通时，请在本配置页单独开启账号级二次开关；关闭后标准 Event Bus 入口仍可运行。",
            "description": "只读说明；实际系统前缀由 TelePilot 当前命令前缀决定。",
        },
        "start_command": {
            "type": "string",
            "title": "开启指令名",
            "default": "福利开启",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "只填写命令本体，不要填写系统前缀。仅账号主人或授权管理员可用。",
        },
        "stop_command": {
            "type": "string",
            "title": "暂停指令名",
            "default": "福利暂停",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "只填写命令本体，不要填写系统前缀。仅账号主人或授权管理员可用。",
        },
        "status_command": {
            "type": "string",
            "title": "状态指令名",
            "default": "福利状态",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "只填写命令本体，不要填写系统前缀。仅账号主人或授权管理员可用。",
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
            "type": ["string", "number"],
            "title": "随机回复概率",
            "default": "0.05",
            "minimum": 0,
            "maximum": 1,
            "pattern": "^(0(\\.\\d+)?|1(\\.0+)?)$",
            "description": "每条普通发言触发回复的概率，按 0 到 1 的小数填写；例如 0.09 表示 9%，0 表示不自动回复，1 表示每条都回复。",
        },
        "chat_cooldown_seconds": {
            "type": "integer",
            "title": "全群回复冷却（秒）",
            "default": 30,
            "minimum": 0,
            "maximum": 86400,
            "description": "任意一次随机福利回复后，当前群组进入冷却。0 表示关闭全群冷却。",
        },
        "user_cooldown_seconds": {
            "type": "integer",
            "title": "同一用户回复冷却（秒）",
            "default": 120,
            "minimum": 0,
            "maximum": 86400,
            "description": "某个用户触发随机福利回复后，该用户在当前群组进入冷却。0 表示关闭同用户冷却。",
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
    "required": [
        "start_command",
        "stop_command",
        "status_command",
        "allowed_chat_ids",
        "reply_template",
        "trigger_probability",
        "chat_cooldown_seconds",
        "user_cooldown_seconds",
        "default_enabled",
    ],
}

USAGE = "从配置页的已允许会话选择器中选择要纳入随机福利作用范围的群组。选中群组默认监听普通群友发言，并按概率随机引用其中一条消息回复自定义福利语；当前账号自己的消息和 Bot 账号消息会被排除。账号主人或授权管理员可发送独立配置的开启、暂停和状态指令控制当前群组。为防刷屏，命中回复后会进入全群冷却和同一用户冷却，冷却秒数设为 0 可关闭。"

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

CAPABILITIES = {
    "telegram_direct_passthrough": {
        "enabled": True,
        "reason": "用于测试 TelePilot userbot incoming 裸直通链路，并在低延时路径随机引用群友发言回复福利语。",
        "sources": ["userbot"],
        "directions": ["incoming"],
    }
}

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
    requires_platform_capabilities=[],
    capabilities=CAPABILITIES,
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
