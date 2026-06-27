"""猜数字远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员发送 {prefix}{command} 奖励金额 开启猜数字；群友在同一群内回复数字抢答，插件提示大了/小了并在答对、超时或关闭时结束会话。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "guess",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "答题限时（秒）",
            "default": 300,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


MANIFEST = Manifest(
    key="guess_number",
    display_name="猜数字",
    version="1.0.10",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="群内猜数字游戏，系统随机一个数字，群友轮流猜，提示大了/小了",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[{'key': 'start_guess_number',
  'title': '开始猜数字',
  'description': '由交互 Bot 在群内开启一局猜数字游戏。',
  'interaction_profile': 'session_game',
  'launch_mode': 'hybrid',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'guess', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 300,
                     'duplicate_start': 'reject',
                     'close_on': ['winner', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply', 'bbot_notice']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'prize': {'type': 'integer',
                                            'title': '奖励',
                                            'default': 100,
                                            'minimum': 1},
                                  'timeout': {'type': 'integer',
                                              'title': '答题限时（秒）',
                                              'default': 300,
                                              'minimum': 10,
                                              'maximum': 86400},
                                  'low': {'type': 'integer',
                                          'title': '最小数字',
                                          'default': 1,
                                          'minimum': 1},
                                  'high': {'type': 'integer',
                                           'title': '最大数字',
                                           'default': 100,
                                           'minimum': 2},
                                  'max_attempts': {'type': 'integer',
                                                   'title': '最大猜测次数（0 表示不限）',
                                                   'default': 0,
                                                   'minimum': 0,
                                                   'maximum': 1000}}},
  'settlement': {'mode': 'announce_only', 'winner_field': 'actor.user_id', 'amount_field': 'prize'},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'open_race'}],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
