"""十点半纸牌游戏 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员发送 {prefix}{command} 下注金额 创建十点半大厅；玩家精确转账底注给账号 userbot 后由 payment_confirmed 加入牌局；交互 Bot 承接选庄、要牌、停牌、加倍和主消息编辑，结算发奖走 userbot_reply 受控通道。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "10d",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "每回合限时（秒）",
            "default": 30,
            "minimum": 10,
            "maximum": 120,
        },
        "lobby_timeout": {
            "type": "integer",
            "title": "大厅等待时间（秒）",
            "default": 60,
            "minimum": 10,
            "maximum": 300,
        },
        "max_players": {
            "type": "integer",
            "title": "最大玩家数",
            "default": 5,
            "minimum": 1,
            "maximum": 10,
        },
    },
    "required": ["command", "timeout", "lobby_timeout", "max_players"],
}


# TelePilot 0.41 Event Bus metadata.
USAGE = ('管理员发送 {prefix}{command} 下注金额 创建十点半大厅；玩家精确转账底注给账号 userbot 后由 payment_confirmed 加入牌局；交互 Bot '
 '承接选庄、要牌、停牌、加倍和主消息编辑，结算发奖走 userbot_reply 受控通道。事件订阅：管理员命令走 userbot；群内关键词、按钮和会话消息走 '
 'interaction_bot；付款确认来自 external_payment_notice/userbot。输出只使用 interaction_bot 或 userbot_reply '
 '受控通道。')
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
    key="ten_half",
    display_name="十点半",
    version="0.2.12",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="经典十点半纸牌游戏：支持多人对战、加倍、五小等规则",
    permissions=["send_message", "edit_message", "delete_message", "read_chat"],

    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[{'key': 'start_ten_half',
  'title': '开始十点半',
  'description': '由交互 Bot 在群内开启一局十点半纸牌游戏。',
  'interaction_profile': 'session_game',
  'launch_mode': 'hybrid',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'callback_query', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': '10d', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 300,
                     'duplicate_start': 'reject',
                     'close_on': ['winner', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message',
                                  'edit_message',
                                  'delete_message',
                                  'answer_callback',
                                  'no_session',
                                  'end_session',
                                  'result',
                                  'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'bet': {'type': 'integer',
                                          'title': '下注金额',
                                          'default': 100,
                                          'minimum': 1},
                                  'timeout': {'type': 'integer',
                                              'title': '每回合限时（秒）',
                                              'default': 30,
                                              'minimum': 10,
                                              'maximum': 120},
                                  'lobby_timeout': {'type': 'integer',
                                                    'title': '大厅等待时间（秒）',
                                                    'default': 60,
                                                    'minimum': 10,
                                                    'maximum': 300},
                                  'max_players': {'type': 'integer',
                                                  'title': '最大玩家数',
                                                  'default': 5,
                                                  'minimum': 1,
                                                  'maximum': 10}}},
  'settlement': {'mode': 'announce_only'},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'paid_pool'}],
    config_schema=CONFIG_SCHEMA,
)


# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
