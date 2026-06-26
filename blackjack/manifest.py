"""21点远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
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


MANIFEST = Manifest(
    key="blackjack",
    display_name="21点",
    version="1.0.11",
    min_telepilot_version="0.30.4",
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
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'callback_query', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'bj', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 120,
                     'duplicate_start': 'reject',
                     'close_on': ['winner', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply', 'bbot_notice']},
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
  'settlement': {'mode': 'announce_only',
                 'winner_field': 'actor.user_id',
                 'amount_field': 'prize'}}],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
