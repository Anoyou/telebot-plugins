"""快问快答插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_VERSION = "1.0.1"
DEFAULT_COMMAND = "quickqa"
DEFAULT_START_KEYWORD = "开始答题"
DEFAULT_INITIAL_POINTS = 20
DEFAULT_CORRECT_POINTS = 3
DEFAULT_WRONG_POINTS = 5
DEFAULT_ENTRY_FEE = 100
DEFAULT_REWARD_RATIO = 0.9
DEFAULT_MAX_QUESTIONS_PER_GAME = 30
DEFAULT_QUESTION_TIMEOUT_SECONDS = 45
DEFAULT_SELECTION_TIMEOUT_SECONDS = 120
DEFAULT_MIN_PLAYERS = 2
DEFAULT_MAX_PLAYERS = 30
DEFAULT_MAX_SOURCE_CHARS = 60000
DEFAULT_AI_QUESTION_COUNT = 24
DEFAULT_AI_TIMEOUT_SECONDS = 90
AI_SYSTEM_PROMPT = """你是 TelePilot 快问快答插件的题库整理助手。
你会收到一个网页的纯文本内容。请只基于原文整理适合群聊快问快答的三选一题库。
要求：
1. 输出严格 JSON，不要 Markdown，不要解释。
2. JSON 结构必须是：
{
  "title": "题库标题",
  "summary": "不超过 120 字的来源摘要",
  "questions": [
    {
      "question": "题目正文",
      "options": ["选项 A", "选项 B", "选项 C"],
      "answer_index": 0,
      "explanation": "一句话解释正确答案"
    }
  ]
}
3. 每题必须有且只有 3 个选项，answer_index 只能是 0、1、2。
4. 题目要能从原文明确找到答案，避免主观题、无依据题、过细碎的数字记忆题。
5. 题干和选项要短，适合 Telegram 按钮展示。"""


USAGE = (
    "管理员使用 {prefix}quickqa kb import <URL> [标题] 抓取公开网页，并通过 TelePilot AI 生成三选一题库草稿；"
    "草稿确认保存后可在游戏开局时由随机玩家按钮多选题库。玩家通过转账门槛金额报名，每人默认 20 积分，"
    "答对加分、答错扣分，扣完出局；剩最后一人或题库用完时通过平台 settlement 公告奖励。"
)

EVENT_SUBSCRIPTIONS = [
    {
        "events": ["payment_confirmed"],
        "source": ["external_payment_notice", "userbot"],
        "scope": "rule_bound",
        "entry_key": "join_quick_qa",
        "description": "付款确认用于报名入场，插件按门槛金额二次校验。",
    },
    {
        "events": ["message", "callback_query", "session_close"],
        "source": ["interaction_bot"],
        "scope": "all_allowed_chats",
        "entry_key": "join_quick_qa",
        "description": "交互 Bot 承接开局、题库选择、三选一抢答、题库草稿保存按钮。",
    },
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "description": "账号主人或授权管理员通过 UserBot 命令管理题库和大厅。",
    },
]

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-category": "interactive",
    "x-usage-guide": USAGE,
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": DEFAULT_COMMAND,
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "level": "account",
        },
        "usage_preview": {
            "type": "string",
            "title": "使用说明（只读）",
            "readOnly": True,
            "default": (
                "{prefix}quickqa 100 创建报名大厅\n"
                "{prefix}quickqa start 开始选择题库\n"
                "{prefix}quickqa kb import <URL> [标题] 生成题库草稿\n"
                "{prefix}quickqa kb save <草稿ID> 保存题库\n"
                "{prefix}quickqa kb list 查看题库"
            ),
        },
        "entry_fee": {
            "type": "integer",
            "title": "默认门槛金额",
            "default": DEFAULT_ENTRY_FEE,
            "minimum": 1,
            "maximum": 10000000,
            "level": "account",
        },
        "initial_points": {
            "type": "integer",
            "title": "初始积分",
            "default": DEFAULT_INITIAL_POINTS,
            "minimum": 1,
            "maximum": 1000,
        },
        "correct_points": {
            "type": "integer",
            "title": "答对加分",
            "default": DEFAULT_CORRECT_POINTS,
            "minimum": 0,
            "maximum": 1000,
        },
        "wrong_points": {
            "type": "integer",
            "title": "答错扣分",
            "default": DEFAULT_WRONG_POINTS,
            "minimum": 1,
            "maximum": 1000,
        },
        "min_players": {
            "type": "integer",
            "title": "最低开局人数",
            "default": DEFAULT_MIN_PLAYERS,
            "minimum": 2,
            "maximum": 100,
        },
        "max_players": {
            "type": "integer",
            "title": "最高报名人数",
            "default": DEFAULT_MAX_PLAYERS,
            "minimum": 2,
            "maximum": 200,
        },
        "max_questions_per_game": {
            "type": "integer",
            "title": "每局最多题数",
            "default": DEFAULT_MAX_QUESTIONS_PER_GAME,
            "minimum": 1,
            "maximum": 500,
        },
        "question_timeout_seconds": {
            "type": "integer",
            "title": "每题限时（秒）",
            "default": DEFAULT_QUESTION_TIMEOUT_SECONDS,
            "minimum": 5,
            "maximum": 600,
        },
        "selection_timeout_seconds": {
            "type": "integer",
            "title": "题库选择限时（秒）",
            "default": DEFAULT_SELECTION_TIMEOUT_SECONDS,
            "minimum": 10,
            "maximum": 1800,
        },
        "reward_ratio": {
            "type": "number",
            "title": "发奖比例",
            "description": "总门槛金额的发奖上限，默认 0.9。",
            "default": DEFAULT_REWARD_RATIO,
            "minimum": 0.01,
            "maximum": 1,
        },
        "payout_mode": {
            "type": "string",
            "title": "结算模式",
            "default": "announce_only",
            "enum": ["announce_only", "auto"],
            "description": "auto 仍由平台受控 settlement 执行，普通 Bot 不直接转账。",
        },
        "start_keyword": {
            "type": "string",
            "title": "开局关键词",
            "default": DEFAULT_START_KEYWORD,
            "minLength": 1,
            "maxLength": 32,
        },
        "allowed_source_hosts": {
            "type": "string",
            "title": "题库来源域名白名单（可选）",
            "description": "留空表示使用 manifest 的公共域名范围；填写后只允许这些域名，多个用逗号或空格分隔。",
            "default": "",
            "level": "account",
        },
        "telepilot_provider": {
            "type": "string",
            "title": "固定 TelePilot Provider（可选）",
            "default": "",
            "x-ui-widget": "llm-provider-select",
        },
        "telepilot_model": {
            "type": "string",
            "title": "TelePilot 模型覆盖（可选）",
            "default": "",
            "x-ui-widget": "llm-model-select",
            "x-ui-provider-field": "telepilot_provider",
            "x-ui-model-modality": "text",
        },
        "ai_question_count": {
            "type": "integer",
            "title": "单个 URL 生成题数",
            "default": DEFAULT_AI_QUESTION_COUNT,
            "minimum": 3,
            "maximum": 80,
        },
        "ai_timeout_seconds": {
            "type": "integer",
            "title": "AI 请求超时（秒）",
            "default": DEFAULT_AI_TIMEOUT_SECONDS,
            "minimum": 20,
            "maximum": 600,
        },
        "max_source_chars": {
            "type": "integer",
            "title": "网页正文最大字符数",
            "default": DEFAULT_MAX_SOURCE_CHARS,
            "minimum": 1000,
            "maximum": 300000,
        },
        "question_generation_prompt": {
            "type": "string",
            "title": "题库生成系统提示词",
            "default": AI_SYSTEM_PROMPT,
            "x-ui-widget": "textarea",
            "minLength": 20,
            "maxLength": 6000,
        },
        "knowledge_bases_json": {
            "type": "string",
            "title": "手工题库 JSON（可选）",
            "description": "可手工填入题库数组；命令保存的题库会写入插件本地数据文件，不需要同步填这里。",
            "default": "[]",
            "x-ui-widget": "textarea",
        },
    },
    "required": [
        "command",
        "entry_fee",
        "initial_points",
        "correct_points",
        "wrong_points",
        "min_players",
        "max_players",
        "max_questions_per_game",
        "question_timeout_seconds",
        "selection_timeout_seconds",
        "reward_ratio",
        "payout_mode",
        "start_keyword",
        "allowed_source_hosts",
        "ai_question_count",
        "ai_timeout_seconds",
        "max_source_chars",
        "question_generation_prompt",
        "knowledge_bases_json",
    ],
}


MANIFEST = Manifest(
    key="quick_qa",
    display_name="快问快答",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.33.0",
    author="Anoyou",
    description="支持 URL + AI 生成题库的转账报名三选一快问快答积分淘汰赛。",
    usage=USAGE,
    category="interactive",
    permissions=["send_message", "edit_message", "read_chat", "external_http", "ai_text"],
    allowed_hosts=[
        "**.com",
        "**.net",
        "**.org",
        "**.io",
        "**.dev",
        "**.app",
        "**.xyz",
        "**.cn",
        "**.top",
        "**.site",
        "**.online",
        "**.info",
        "**.me",
        "**.tv",
        "**.ai",
        "**.co",
        "**.cc",
        "**.wiki",
        "**.edu",
        "**.gov",
        "**.jp",
        "**.hk",
        "**.tw",
    ],
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    capabilities={},
    interaction_profile="session_game",
    interaction_entries=[
        {
            "key": "join_quick_qa",
            "title": "快问快答报名与抢答",
            "description": "玩家转账门槛金额报名，达到人数后通过按钮选择题库并进行三选一抢答。",
            "interaction_profile": "session_game",
            "launch_mode": "bridge",
            "session_scope": "chat",
            "events": ["payment_confirmed", "keyword", "message", "callback_query", "session_close"],
            "preserve_command_trigger": True,
            "payload_contract": {
                "required_envelope": ["source", "actor", "trigger", "session"],
                "required_event_fields": ["type", "chat_id"],
            },
            "result_contract": {
                "actions": ["send_message", "edit_message", "answer_callback", "end_session", "result", "settlement"],
                "send_via": ["interaction_bot", "userbot_reply"],
            },
            "settlement": {
                "mode": "announce_only",
                "winner_field": "actor.user_id",
                "amount_field": "reward",
            },
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entry_fee": {
                        "type": "integer",
                        "title": "门槛金额",
                        "default": DEFAULT_ENTRY_FEE,
                        "minimum": 1,
                    },
                    "start_keyword": {
                        "type": "string",
                        "title": "开局关键词",
                        "default": DEFAULT_START_KEYWORD,
                    },
                },
                "required": ["entry_fee"],
            },
            "dispatch_modes": ["public_keyword"],
            "message_channels": {"public_keyword": "interaction_bot"},
            "money_channel": "userbot_reply",
            "participant_policy": "paid_pool",
            "command_fallback": {"enabled": True, "command": DEFAULT_COMMAND, "mode": "hint_only"},
            "session_policy": {
                "ttl_seconds": 3600,
                "duplicate_start": "reject",
                "close_on": ["game_over", "cancelled", "session_close"],
            },
        }
    ],
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
