"""话痨挑战远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员发送 {prefix}{command} 创建话痨挑战，插件按当前聊天内成员发言统计规则自动记录和结算；配置页可调整触发指令名。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "chat",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
    },
    "required": ["command"],
}


# TelePilot 0.41 Event Bus metadata.
USAGE = ('管理员发送 {prefix}{command} 创建话痨挑战，插件按当前聊天内成员发言统计规则自动记录和结算；配置页可调整触发指令名。事件订阅：管理员通过 userbot '
 '命令启动挑战，挑战期间监听群消息并记录违规；结果消息由平台受控通道发送。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'},
 {'events': ['message'],
  'source': ['userbot'],
  'scope': 'all_allowed_chats',
  'description': '管理员通过 userbot 命令创建挑战后，插件监听群消息统计违规并按规则扣分。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="chatter_challenge",
    display_name="话痨挑战",
    version="1.0.7",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="设定聊天规则，违反者自动扣分，全程被动监听",
    permissions=["send_message", "edit_message", "read_chat"],

    category="automation",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)


# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
