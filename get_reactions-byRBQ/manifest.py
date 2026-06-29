from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '发送 {prefix}{command} 按原 Pagermaid 方式读取或测试消息 reaction；可在配置页调整触发指令名和启用状态。需要 reaction 相关权限时请先确认账号能力。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "get_reactions",
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

# TelePilot 0.41 Event Bus metadata.
USAGE = ('发送 {prefix}{command} 按原 Pagermaid 方式读取或测试消息 reaction；可在配置页调整触发指令名和启用状态。需要 reaction '
 '相关权限时请先确认账号能力。事件订阅：账号主人或授权管理员通过 userbot 命令触发；输出通过平台 MessageOps 受控发送，并可在日志 Trace 中排查。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="get_reactions-byRBQ",
    display_name="get_reactions-byRBQ",
    version="1.0.6",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.2",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="表情获取辅助命令，用于测试环境是否支持自定义表情反应",
    permissions=["send_message", "edit_message", "read_chat"],

    category="utility",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)

# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
