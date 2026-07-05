"""十点半纸牌游戏 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '在交互中心为十点半配置关键词和底注金额后，由交互 Bot 在群内开局、发大厅、刷新按钮、超时提示和结算公告。普通玩家精确转账底注给账号 userbot 后由平台投递 payment_confirmed 入局；账号 userbot 本人在等待大厅可发送“入局”免转账加入。发奖始终返回 payout 并由 userbot 执行，结算后的本局消息默认 60 秒自动清理。',
    "additionalProperties": False,
    "properties": {
        "timeout": {
            "type": "integer",
            "title": "每个玩家抓牌/操作限时（秒）",
            "default": 45,
            "minimum": 5,
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
            "minimum": 2,
            "maximum": 10,
        },
        "settlement_cleanup_delay": {
            "type": "integer",
            "title": "结算后自动清理消息时间（秒）",
            "description": "结算完成后延迟清理本局主消息、结算公告、发奖回复和零散加入提示。填 0 表示不自动清理。",
            "default": 60,
            "minimum": 0,
            "maximum": 3600,
        },
    },
    "required": ["timeout", "lobby_timeout", "max_players", "settlement_cleanup_delay"],
}


# TelePilot 0.41 Event Bus metadata.
USAGE = '十点半只通过交互 Bot 关键词/规则开局；大厅、按钮、后台刷新、超时开局和结算公告固定由 interaction_bot 发送。普通玩家精确转账底注给账号 userbot 后，由平台 payment_confirmed 作为资金证据入局；账号 userbot 本人在等待大厅可发送“入局”免转账加入。结算发奖返回 `payout`，始终由 userbot 执行。'
EVENT_SUBSCRIPTIONS = [{'events': ['message', 'callback_query', 'session_close'],
  'entry_key': 'start_ten_half',
  'source': ['interaction_bot'],
  'scope': 'rule_bound',
  'description': '交互规则命中后由交互 Bot 投递会话事件。'},
 {'events': ['payment_confirmed'],
  'entry_key': 'start_ten_half',
  'source': ['external_payment_notice', 'userbot'],
  'scope': 'all_allowed_chats',
  'description': '付款确认按已允许群投递，插件只接收当前等待牌桌且金额等于本桌底注的转账。'},
 {'events': ['message'],
  'entry_key': 'start_ten_half',
  'source': ['userbot'],
  'scope': 'all_allowed_chats',
  'filters': {'contains': ['入局']},
  'description': '等待大厅中，账号 userbot 本人发送“入局”可作为免转账入局证据；普通玩家仍必须通过 payment_confirmed 入局。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="ten_half",
    display_name="十点半",
    version="0.3.10",
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
  'launch_mode': 'bridge',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'callback_query', 'session_close'],
  'preserve_command_trigger': True,
  'default_trigger_modes': 'keyword_only',
  'session_policy': {'ttl_seconds': 300,
                     'duplicate_start': 'reject',
                     'close_on': ['winner', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message',
                                  'edit_message',
                                  'delete_message',
                                  'answer_callback',
                                  'start_session',
                                  'no_session',
                                  'payout', 'end_session',
                                  'result',
                                  'settlement'],},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'bet': {'type': 'integer',
                                          'title': '下注金额',
                                          'default': 100,
                                          'minimum': 1},
                                  'timeout': {'type': 'integer',
                                              'title': '每个玩家抓牌/操作限时（秒）',
                                              'default': 45,
                                              'minimum': 5,
                                              'maximum': 120},
                                  'lobby_timeout': {'type': 'integer',
                                                    'title': '大厅等待时间（秒）',
                                                    'default': 60,
                                                    'minimum': 10,
                                                    'maximum': 300},
                                  'max_players': {'type': 'integer',
                                                  'title': '最大玩家数',
                                                  'default': 5,
                                                  'minimum': 2,
                                                  'maximum': 10},
                                  'settlement_cleanup_delay': {'type': 'integer',
                                                               'title': '结算后自动清理消息时间（秒）',
                                                               'description': '结算完成后延迟清理本局主消息、结算公告、发奖回复和零散加入提示。填 0 表示不自动清理。',
                                                               'default': 60,
                                                               'minimum': 0,
                                                               'maximum': 3600}}},
  'settlement': {'mode': 'announce_only'},
  'dispatch_modes': ['public_keyword'],
  'message_channels': {'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'paid_pool'}],
    config_schema=CONFIG_SCHEMA,
)


# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
