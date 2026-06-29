"""21点远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员可发送 {prefix}{command} 下注金额 开启 21 点；也可以在交互中心绑定关键词或付款触发，玩家后续通过按钮或“要牌/停牌/加倍”操作，奖金和结算仍走 userbot/平台受控通道。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "bj",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "牌局限时（秒）",
            "default": 120,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


# TelePilot 0.41 Event Bus metadata.
USAGE = ('管理员可发送 {prefix}{command} 下注金额 开启 21 点；也可以在交互中心绑定关键词或付款触发，玩家后续通过按钮或“要牌/停牌/加倍”操作，奖金和结算仍走 '
 'userbot/平台受控通道。事件订阅：管理员命令走 userbot；群内关键词、按钮和会话消息走 interaction_bot；付款确认来自 '
 'external_payment_notice/userbot。输出只使用 interaction_bot 或 userbot_reply 受控通道。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'},
 {'events': ['message', 'callback_query', 'session_close'],
  'source': ['interaction_bot'],
  'scope': 'rule_bound',
  'description': '交互规则命中后由交互 Bot 投递会话事件。'},
 {'events': ['payment_confirmed'],
  'source': ['external_payment_notice', 'userbot'],
  'scope': 'rule_bound',
  'description': '付款确认由外部到账证据和 UserBot 上下文共同确认。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="blackjack",
    display_name="21点",
    version="1.0.24",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="经典21点纸牌游戏，群内庄家模式，支持要牌/停牌/加倍",
    permissions=["send_message", "edit_message", "read_chat", "delete_message"],

    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[{'key': 'start_blackjack',
  'title': '开始21点',
  'description': '由交互 Bot 在群内发起一局 21 点牌局。',
  'interaction_profile': 'session_game',
  'launch_mode': 'hybrid',
  'session_scope': 'user',
  'events': ['payment_confirmed', 'keyword', 'message', 'callback_query', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'bj', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 120,
                     'duplicate_start': 'allow',
                     'close_on': ['winner', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'prize': {'type': 'integer',
                                            'title': '下注/奖励',
                                            'default': 10,
                                            'minimum': 1,
                                            'maximum': 1000},
                                  'timeout': {'type': 'integer',
                                              'title': '牌局限时（秒）',
                                              'default': 120,
                                              'minimum': 10,
                                              'maximum': 86400}}},
  'settlement': {'mode': 'announce_only', 'winner_field': 'actor.user_id', 'amount_field': 'prize'},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'solo_owner'}],
    config_schema=CONFIG_SCHEMA,
)


# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
