"""诗词填空远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员发送 {prefix}{command} 奖励金额 开启诗词填空，群友回复缺失字词抢答；插件按答对、超时或关闭返回结算结果。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "poetry",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "答题限时（秒）",
            "default": 120,
            "minimum": 10,
            "maximum": 86400,
        },
    },
    "required": ["command", "timeout"],
}


# TelePilot 0.41 Event Bus metadata.
USAGE = ('管理员发送 {prefix}{command} 奖励金额 开启诗词填空，群友回复缺失字词抢答；插件按答对、超时或关闭返回结算结果。事件订阅：管理员命令走 '
 'userbot；群内关键词、按钮和会话消息走 interaction_bot；付款确认来自 external_payment_notice/userbot。输出只使用 '
 'interaction_bot 或 userbot_reply 受控通道。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'},
 {'events': ['message', 'session_close'],
  'source': ['interaction_bot'],
  'scope': 'rule_bound',
  'description': '交互规则命中后由交互 Bot 投递会话事件。'},
 {'events': ['payment_confirmed'],
  'source': ['external_payment_notice', 'userbot'],
  'scope': 'rule_bound',
  'description': '付款确认由外部到账证据和 UserBot 上下文共同确认。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="poetry_blank",
    display_name="诗词填空",
    version="1.0.11",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="古诗词填空抢答，答对获奖",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[{'key': 'start_poetry_blank',
  'title': '开始诗词填空',
  'description': '由交互 Bot 在群内开启一局诗词填空抢答。',
  'interaction_profile': 'session_game',
  'launch_mode': 'hybrid',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'poetry', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 120,
                     'duplicate_start': 'reject',
                     'close_on': ['winner', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'prize': {'type': 'integer',
                                            'title': '奖励',
                                            'default': 100,
                                            'minimum': 1},
                                  'timeout': {'type': 'integer',
                                              'title': '答题限时（秒）',
                                              'default': 120,
                                              'minimum': 10,
                                              'maximum': 86400}}},
  'settlement': {'mode': 'announce_only', 'winner_field': 'actor.user_id', 'amount_field': 'prize'},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'open_race'}],
    config_schema=CONFIG_SCHEMA,
)


# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
