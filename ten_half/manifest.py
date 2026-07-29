"""十点半纸牌游戏 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '在交互中心配置关键词后，由交互 Bot 先让触发者选择本局底注额度，再进入对应额度的开局大厅。当前规则为每人起手 1 张，庄家首牌暗牌，五小最大；按钮里可点击“规则”快速查看。转账模式下普通玩家精确转账底注给账号 userbot 后由平台投递 payment_confirmed 入局；无感模式下玩家点击开局消息按钮，由 userbot 回复玩家近期发言 -底注 完成扣款并入局，已有 2 张牌后加倍会再次扣除底注并补 1 张停牌。账号本人可在群内发送“10d模式”切换入局模式，也可在等待大厅发送“入局”免转账加入；这些普通消息由交互 Bot 通道处理，userbot 只负责收付款。庄家制结算下，闲家倍率奖金由庄家出，若倍率奖金毛额超过庄家已入局金额，会先向庄家补扣差额；刷屏费从赢家倍率奖金里抽取。发奖始终返回 payout 并由 userbot 执行，结算后的本局消息默认 60 秒自动清理。',
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
        "service_fee_percent": {
            "type": "integer",
            "title": "刷屏费比例（%）",
            "description": "从赢家倍率奖金里抽取的比例；补扣庄家时先按未扣刷屏费的倍率奖金毛额计算。",
            "default": 10,
            "minimum": 0,
            "maximum": 100,
        },
        "join_mode": {
            "type": "string",
            "title": "入局模式",
            "description": "转账模式：玩家转账底注后入局。无感模式：玩家点击开局消息按钮，由账号 userbot 回复玩家近期发言 -底注 完成扣款并入局。账号本人可在群内发送“10d模式”切换。",
            "default": "transfer",
            "enum": ["transfer", "silent_debit"],
            "enumNames": ["转账模式", "无感扣款模式"],
        },
        "stake_options": {
            "type": "array",
            "title": "可选底注额度",
            "description": "关键词触发后展示给开局发起人的底注按钮，按顺序显示；默认 1000、10000、50000、100000。",
            "default": [1000, 10000, 50000, 100000],
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000000000,
            },
        },
    },
    "required": ["timeout", "lobby_timeout", "max_players", "settlement_cleanup_delay"],
}


# TelePilot 0.41 Event Bus metadata.
USAGE = '十点半只通过交互 Bot 关键词/规则开局；大厅、按钮、后台刷新、超时开局和结算公告固定由 interaction_bot 发送。当前规则为每人起手 1 张，庄家首牌暗牌，五小最大；“规则”按钮会弹窗展示精简规则。转账模式下普通玩家精确转账底注给账号 userbot 后，由平台 payment_confirmed 作为资金证据入局；无感模式下玩家点击开局消息按钮，由 userbot 回复玩家近期发言 -底注 完成扣款并入局，已有 2 张牌后加倍会再次扣除底注并补 1 张停牌。账号本人可在群内发送“10d模式”切换模式，也可在等待大厅发送“入局”免转账加入；这些普通消息由交互 Bot 通道处理，userbot 只负责收付款。庄家制结算下，闲家倍率奖金由庄家出，若倍率奖金毛额超过庄家已入局金额，会先向庄家补扣差额；刷屏费从赢家倍率奖金里抽取。结算发奖返回 `payout`，始终由 userbot 执行。'
EVENT_SUBSCRIPTIONS = [{'events': ['message', 'callback_query', 'session_close'],
  'entry_key': 'start_ten_half',
  'source': ['interaction_bot'],
  'scope': 'rule_bound',
  'description': '交互规则命中后由交互 Bot 投递会话事件。'},
 {'events': ['payment_confirmed'],
  'entry_key': 'start_ten_half',
  'source': ['external_payment_notice', 'userbot'],
  'scope': 'all_allowed_chats',
  'description': '付款确认按已允许群投递；只有当前等待牌桌为转账模式且金额等于本桌底注时才入局。'},
 {'events': ['message'],
  'entry_key': 'start_ten_half',
  'source': ['interaction_bot'],
  'scope': 'all_allowed_chats',
  'filters': {'contains': ['入局', '10d模式']},
  'description': '账号本人在群内发送“10d模式”可切换转账/无感扣款入局；等待大厅中发送“入局”可作为免转账入局证据。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="ten_half",
    display_name="十点半",
    version="0.4.17",
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
                   'properties': {'stake_options': {'type': 'array',
                                                    'title': '可选底注额度',
                                                    'description': '关键词触发后展示给开局发起人的底注按钮，按顺序显示。',
                                                    'default': [1000, 10000, 50000, 100000],
                                                    'minItems': 1,
                                                    'maxItems': 8,
                                                    'items': {'type': 'integer',
                                                              'minimum': 1,
                                                              'maximum': 1000000000}},
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
                                                               'maximum': 3600},
                                  'service_fee_percent': {'type': 'integer',
                                                          'title': '刷屏费比例（%）',
                                                          'description': '从赢家倍率奖金里抽取的比例；补扣庄家时先按未扣刷屏费的倍率奖金毛额计算。',
                                                          'default': 10,
                                                          'minimum': 0,
                                                          'maximum': 100}}},
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
