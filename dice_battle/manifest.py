"""骰子比大小远程插件 Manifest。"""

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
            "default": "dice",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "邀请限时（秒）",
            "default": 60,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


MANIFEST = Manifest(
    key="dice_battle",
    display_name="骰子比大小",
    version="1.0.7",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="群内骰子对战，两人各掷骰子比点数，支持下注",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_profile="challenge_game",
    interaction_entries=[{'key': 'start_dice_battle',
  'title': '开始骰子对战',
  'description': '由交互 Bot 在群内发起一局骰子比大小挑战。',
  'interaction_profile': 'challenge_game',
  'launch_mode': 'hybrid',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'dice', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 60,
                     'duplicate_start': 'reject',
                     'close_on': ['winner', 'draw', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply', 'bbot_notice']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'prize': {'type': 'integer',
                                            'title': '奖励',
                                            'default': 0,
                                            'minimum': 0},
                                  'timeout': {'type': 'integer',
                                              'title': '邀请限时（秒）',
                                              'default': 60,
                                              'minimum': 10,
                                              'maximum': 86400},
                                  'dice_count': {'type': 'integer',
                                                 'title': '骰子数量',
                                                 'default': 2,
                                                 'minimum': 1,
                                                 'maximum': 6}}},
  'settlement': {'mode': 'announce_only', 'winner_field': 'actor.user_id', 'amount_field': 'prize'},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'open_race'}],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
