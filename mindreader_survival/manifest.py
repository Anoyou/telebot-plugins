"""读心生存赛远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


# ── 默认消息模板 ──────────────────────────────────────────────

JOIN_MESSAGE_BOT_TEMPLATE = (
    "<b>🧠 读心生存赛</b> · 庄家：Bot\n"
    "\n"
    "💰 门票：<b>{ticket_price}</b> 金币\n"
    "🎯 共 <b>{total_rounds}</b> 轮，选项逐轮递增\n"
    "🏆 存活者平分 <b>90%</b> 奖池\n"
    "\n"
    "💳 转账 <b>{ticket_price}</b> 金币加入游戏！\n"
    "⏰ 管理员发 <code>{prefix}{command} 开始</code> 启动"
)

JOIN_MESSAGE_TEMPLATE = (
    "<b>🧠 读心生存赛</b> · 庄家：{admin_name}\n"
    "\n"
    "💰 门票：<b>{ticket_price}</b> 金币\n"
    "🎯 共 <b>{total_rounds}</b> 轮，选项逐轮递增\n"
    "🏆 存活者平分 <b>90%</b> 奖池\n"
    "\n"
    "💳 转账 <b>{ticket_price}</b> 金币加入游戏！\n"
    "⏰ 等管理员发 <code>{prefix}{command} 开始</code> 启动"
)

ROUND_START_TEMPLATE = (
    "🧠 <b>第 {round_num}/{total_rounds} 轮</b>\n"
    "👥 存活玩家：<b>{alive_count}</b> 人\n"
    "💰 当前奖池：<b>{pool}</b> 金币\n"
    "\n"
    "{options_text}\n"
    "\n"
    "⏰ <b>{timeout}</b> 秒内回复数字选择！\n"
    "💡 选错即淘汰，选对晋级下一轮"
)

ROUND_RESULT_TEMPLATE = (
    "🧠 <b>第 {round_num} 轮结果</b>\n"
    "✅ 正确答案：<b>{answer_text}</b>（第 {answer} 项）\n"
    "🔒 验证：{commit_hash}\n"
    "\n"
    "👥 晋级：<b>{survived_count}</b> 人\n"
    "❌ 淘汰：<b>{eliminated_count}</b> 人\n"
    "{eliminated_names}"
)

GAME_OVER_SOLO_TEMPLATE = (
    "🏆 <b>读心生存赛结束！</b>\n"
    "\n"
    "👑 最终赢家：{winner_name}\n"
    "💰 奖池总额：<b>{pool}</b> 金币\n"
    "🎁 获得：<b>{prize}</b> 金币（90%）\n"
    "👑 管理员抽成：<b>{admin_fee}</b> 金币（10%）"
)

GAME_OVER_MULTI_TEMPLATE = (
    "🏆 <b>读心生存赛结束！</b>\n"
    "\n"
    "👥 存活玩家：<b>{survived_count}</b> 人\n"
    "💰 奖池总额：<b>{pool}</b> 金币\n"
    "🎁 每人获得：<b>{prize_each}</b> 金币（平分 90%）\n"
    "👑 管理员抽成：<b>{admin_fee}</b> 金币（10%）"
)

GAME_OVER_ALL_ELIMINATED_TEMPLATE = (
    "💀 <b>全员淘汰！</b>\n"
    "\n"
    "💰 奖池总额：<b>{pool}</b> 金币\n"
    "👑 管理员独享：<b>{admin_prize}</b> 金币（100%）"
)

GAME_OVER_CANCELLED_TEMPLATE = (
    "⚠️ <b>游戏已取消</b>\n"
    "\n"
    "💰 奖池：<b>{pool}</b> 金币\n"
    "👥 退还给 <b>{player_count}</b> 位玩家，每人 <b>{refund_each}</b> 金币"
)

TIMEOUT_NO_PLAYERS_TEMPLATE = (
    "⏰ <b>第 {round_num} 轮超时！</b>\n"
    "\n"
    "😱 没有人在限时内做出选择\n"
    "💀 所有存活玩家视为淘汰"
)

PLAYER_JOINED_TEMPLATE = (
    "✅ {player_name} 已加入！\n"
    "👥 当前玩家：<b>{player_count}</b> 人"
)

COMMAND_START = "开始"
COMMAND_STOP = "停止"
COMMAND_STATUS = "状态"

PLAYER_KEYWORDS = ["我要玩读心", "读心生存", "读心赛", "玩读心"]

IN_PROGRESS_MESSAGE_TEMPLATE = (
    "⚠️ 游戏进行中，请等待当前游戏结束。\n"
    "发 <code>{prefix}{command} 停止</code> 可强制结束。"
)


# ── 配置 Schema ──────────────────────────────────────────────

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员发送 {prefix}{command} 创建读心生存赛，玩家通过付款或关键词加入，每轮回复数字选择答案；最终存活者按规则瓜分奖池。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "mind",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "ticket_price": {
            "type": "integer",
            "title": "门票价格（金币）",
            "default": 100,
            "minimum": 1,
            "maximum": 100000,
        },
        "total_rounds": {
            "type": "integer",
            "title": "总轮数",
            "default": 5,
            "minimum": 2,
            "maximum": 10,
        },
        "round_timeout": {
            "type": "integer",
            "title": "每轮选择限时（秒）",
            "default": 30,
            "minimum": 10,
            "maximum": 120,
        },
        "option_word_pool": {
            "type": "string",
            "title": "选项词库（逗号分隔）",
            "default": "🍎苹果,🍊橘子,🍋柠檬,🍇葡萄,🍓草莓,🍒樱桃,🍑桃子,🥝猕猴桃,🍍菠萝,🥭芒果,🍉西瓜,🍈哈密瓜,🫐蓝莓,🥑牛油果,🍌香蕉",
            "minLength": 1,
            "maxLength": 2000,
        },
        "join_message_template": {
            "type": "string",
            "title": "等待加入消息模板",
            "description": "占位符：{ticket_price}、{total_rounds}、{prefix}、{command}",
            "default": JOIN_MESSAGE_TEMPLATE,
            "minLength": 1,
            "maxLength": 1000,
        },
        "round_start_template": {
            "type": "string",
            "title": "每轮开始消息模板",
            "description": "占位符：{round_num}、{total_rounds}、{alive_count}、{pool}、{options_text}、{timeout}",
            "default": ROUND_START_TEMPLATE,
            "minLength": 1,
            "maxLength": 1000,
        },
        "round_result_template": {
            "type": "string",
            "title": "每轮结果消息模板",
            "description": "占位符：{round_num}、{answer_text}、{answer}、{commit_hash}、{survived_count}、{eliminated_count}、{eliminated_names}",
            "default": ROUND_RESULT_TEMPLATE,
            "minLength": 1,
            "maxLength": 1000,
        },
        "game_over_solo_template": {
            "type": "string",
            "title": "游戏结束模板（单人获胜）",
            "description": "占位符：{winner_name}、{pool}、{prize}、{admin_fee}",
            "default": GAME_OVER_SOLO_TEMPLATE,
            "minLength": 1,
            "maxLength": 800,
        },
        "game_over_multi_template": {
            "type": "string",
            "title": "游戏结束模板（多人获胜）",
            "description": "占位符：{survived_count}、{pool}、{prize_each}、{admin_fee}",
            "default": GAME_OVER_MULTI_TEMPLATE,
            "minLength": 1,
            "maxLength": 800,
        },
        "game_over_all_eliminated_template": {
            "type": "string",
            "title": "全员淘汰模板",
            "description": "占位符：{pool}、{admin_prize}",
            "default": GAME_OVER_ALL_ELIMINATED_TEMPLATE,
            "minLength": 1,
            "maxLength": 800,
        },
        "game_over_cancelled_template": {
            "type": "string",
            "title": "游戏取消模板",
            "description": "占位符：{pool}、{player_count}、{refund_each}",
            "default": GAME_OVER_CANCELLED_TEMPLATE,
            "minLength": 1,
            "maxLength": 800,
        },
    },
    "required": [
        "command",
        "ticket_price",
        "total_rounds",
        "round_timeout",
        "option_word_pool",
    ],
}


# ── Manifest ─────────────────────────────────────────────────

# TelePilot 0.41 Event Bus metadata.
USAGE = ('管理员发送 {prefix}{command} 创建读心生存赛，玩家通过付款或关键词加入，每轮回复数字选择答案；最终存活者按规则瓜分奖池。事件订阅：管理员命令走 '
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
    key="mindreader_survival",
    display_name="读心生存赛",
    version="1.1.7",
    min_telepilot_version="0.33.0",
    author="Anoyou",
    description="多人读心生存赛游戏。玩家转账加入，通过读心（猜庄家答案）逐轮淘汰，最终存活者瓜分奖池。",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_profile="reward_pool",
    interaction_entries=[{'key': 'start_mindreader',
  'title': '读心生存赛',
  'description': '玩家通过转账加入，管理员通过关键词管理游戏。',
  'interaction_profile': 'reward_pool',
  'launch_mode': 'hybrid',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'mind', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 1800,
                     'duplicate_start': 'reject',
                     'close_on': ['game_over', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result', 'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'ticket_price': {'type': 'integer',
                                                   'title': '门票价格',
                                                   'default': 100,
                                                   'minimum': 1},
                                  'total_rounds': {'type': 'integer',
                                                   'title': '总轮数',
                                                   'default': 5,
                                                   'minimum': 2,
                                                   'maximum': 10},
                                  'round_timeout': {'type': 'integer',
                                                    'title': '每轮限时（秒）',
                                                    'default': 30,
                                                    'minimum': 10,
                                                    'maximum': 120}},
                   'required': ['ticket_price']},
  'settlement': {'mode': 'announce_only', 'winner_field': 'actor.user_id', 'amount_field': 'prize'},
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
