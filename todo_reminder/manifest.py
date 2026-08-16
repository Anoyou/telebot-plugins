"""Todo 提醒远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_KEY = "todo_reminder"
PLUGIN_VERSION = "0.1.3"
DEFAULT_COMMAND = "todo"
USAGE = (
    "发送 {prefix}todo 五分钟后提醒我喝水，或回复某人的消息发送 {prefix}todo 五分钟后提醒他喝水。"
    "默认只提醒一次；在任务末尾写“重复提醒”或“每隔五分钟再次提醒”才会在首次提醒后开始重复。"
    "回复他/她的已发送提醒并发送“已完成”即可停止。"
    "直接提醒自己由 Interaction Bot 在当前会话 @自己，避免 UserBot 自己发消息无法产生通知。"
    "支持发送 {prefix}undo ID 取消提醒；ID 不带 # 并可点击复制。命令结果原地编辑，默认 30 秒后自动删除。"
    "自然语言时间示例：五分钟后、半小时后、明天上午九点、2026-08-17 14:30，并提供列表指令。"
)
EVENT_SUBSCRIPTIONS = [
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "description": "账号主人通过可配置的 UserBot 指令创建、查看 Todo 提醒，并通过 undo 指令取消。",
    },
    {
        "events": ["message"],
        "source": ["userbot", "interaction_bot"],
        "scope": "all_allowed_chats",
        "description": "监听提醒目标回复的完成关键词；插件内部严格校验目标用户、会话和回复锚点。",
    }
]
CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": USAGE,
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "description": "只填写命令本体，不要带系统命令前缀。",
            "default": DEFAULT_COMMAND,
            "minLength": 1,
            "maxLength": 32,
            "pattern": r"^\S+$",
            "level": "account",
        },
        "repeat_interval_minutes": {
            "type": "integer",
            "title": "重复提醒间隔（分钟）",
            "description": "指令只写“重复提醒”而未指定间隔时使用；重复计时从首次提醒后开始。",
            "default": 5,
            "minimum": 1,
            "maximum": 1440,
            "level": "account",
        },
        "auto_delete_enabled": {
            "type": "boolean",
            "title": "自动删除命令结果",
            "description": "命令反馈编辑到原命令消息后，是否在延迟结束时删除该消息。",
            "default": True,
            "level": "account",
        },
        "auto_delete_delay_seconds": {
            "type": "integer",
            "title": "命令结果自动删除延迟（秒）",
            "description": "仅在开启自动删除时生效；0 表示不自动删除。",
            "default": 30,
            "minimum": 0,
            "maximum": 86400,
            "level": "account",
        },
        "completion_keywords": {
            "type": "string",
            "title": "完成关键词",
            "description": "多个关键词用逗号或换行分隔；默认“已完成,完成”。",
            "default": "已完成,完成",
            "minLength": 1,
            "maxLength": 500,
            "level": "account",
        },
        "timezone": {
            "type": "string",
            "title": "时区",
            "description": "自然语言绝对时间使用的 IANA 时区。",
            "default": "Asia/Shanghai",
            "minLength": 1,
            "maxLength": 64,
            "level": "account",
        },
        "reminder_template": {
            "type": "string",
            "title": "提醒消息模板",
            "description": "支持 {mention}、{todo}、{id}、{count}、{reminder_count}、{repeat}、{repeat_interval}、{repeat_interval_minutes} 占位符；{mention} 会使用真实 Telegram 提及。",
            "default": "{mention} 提醒：{todo}",
            "minLength": 1,
            "maxLength": 1000,
            "level": "account",
        },
    },
    "required": [],
}

MANIFEST = Manifest(
    key=PLUGIN_KEY,
    display_name="Todo 提醒",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.97.0-beta.1",
    author="Anoyou",
    description="用自然语言创建单次或重复、可完成确认的 Telegram Todo 提醒。",
    usage=USAGE,
    category="automation",
    permissions=["send_message", "edit_message", "delete_message", "read_chat", "resolve_entity"],
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    requires_platform_capabilities=["interaction_bot"],
    capabilities={},
    config_schema=CONFIG_SCHEMA,
    interaction_send_via=["interaction_bot", "userbot_reply"],
)

__all__ = ["CONFIG_SCHEMA", "DEFAULT_COMMAND", "EVENT_SUBSCRIPTIONS", "MANIFEST", "PLUGIN_KEY", "PLUGIN_VERSION", "USAGE"]
