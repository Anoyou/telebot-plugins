"""死亡左轮插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="dead_revolver",
    display_name="死亡左轮",
    version="1.0.1",
    min_telepilot_version="0.33.0",
    author="builtin",
    description="群聊俄罗斯轮盘游戏。创建游戏后群成员转账加入，轮流对自己或他人开枪，最终存活者赢得奖池。",
    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[{'key': 'join_paid_game',
  'title': '转账加入游戏',
  'description': '用户转账命中后自动加入当前等待中的死亡左轮游戏。',
  'interaction_profile': 'session_game',
  'launch_mode': 'bridge',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'callback_query', 'session_close'],
  'preserve_command_trigger': True,
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'result', 'end_session', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply', 'bbot_notice']},
  'settlement': {'mode': 'announce_only', 'winner_field': 'actor.user_id', 'amount_field': 'prize'},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'entry_fee': {'type': 'integer',
                                                'title': '门票金额',
                                                'description': '每个玩家的入场费，发送 dr <金额> 或用此默认值',
                                                'default': 100,
                                                'minimum': 1}},
                   'required': ['entry_fee']},
  'dispatch_modes': ['public_keyword'],
  'message_channels': {'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'paid_pool',
  'command_fallback': {'enabled': True, 'command': 'dr', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 600,
                     'duplicate_start': 'reject',
                     'close_on': ['started', 'cancelled', 'game_over', 'session_close']}}],
    permissions=["send_message", "edit_message", "read_chat", "delete_message"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "additionalProperties": False,
        "properties": {},
    },
)

__all__ = ["MANIFEST"]
