"""快问快答插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_VERSION = "1.3.1"
DEFAULT_COMMAND = "quickqa"
DEFAULT_START_KEYWORD = "开始答题"
DEFAULT_INITIAL_POINTS = 20
DEFAULT_CORRECT_POINTS = 3
DEFAULT_WRONG_POINTS = 5
DEFAULT_ENTRY_FEE = 100
DEFAULT_REWARD_RATIO = 0.9
DEFAULT_SETTLEMENT_BASE_AMOUNT = 1000
DEFAULT_SETTLEMENT_POINT_MULTIPLIER = 666
DEFAULT_SETTLEMENT_BASE_POINTS = 4
DEFAULT_CLEANUP_DELAY_SECONDS = 120
DEFAULT_PAYOUT_INTERVAL_SECONDS = 2
DEFAULT_FREE_JOIN_KEYWORD = "报名"
DEFAULT_MAX_QUESTIONS_PER_GAME = 50
DEFAULT_QUESTION_TIMEOUT_SECONDS = 45
DEFAULT_SELECTION_TIMEOUT_SECONDS = 120
DEFAULT_MIN_PLAYERS = 2
DEFAULT_MAX_PLAYERS = 30
DEFAULT_MAX_SOURCE_CHARS = 120000
DEFAULT_AI_QUESTION_COUNT = 80
DEFAULT_AI_TIMEOUT_SECONDS = 600
MIN_AI_TIMEOUT_SECONDS = 300
MAX_AI_TIMEOUT_SECONDS = 3600
MAX_AI_QUESTION_COUNT = 200
MAX_SOURCE_CHARS = 800000
MAX_QUESTIONS_PER_GAME = 1000
MAX_PLAYERS = 500
CONFIG_MIN_AI_TIMEOUT_SECONDS = 1
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
5. 题干和选项要短，适合 Telegram 按钮展示。
6. 正确答案不要固定放在第一个选项，A/B/C 三个位置应尽量均匀分布。"""


USAGE = (
    "管理员在 TelePilot Web 配置页的题库管理里填入 URL，点击获取并整理为题库，确认列表后保存配置；"
    "游戏开局时由随机玩家按钮多选题库。门槛金额为 0 时玩家发送“报名”参与并记录消息 ID；"
    "付费局通过转账报名。每人默认 20 积分，答对加分、答错扣分，扣完出局；"
    "结束后按每位玩家积分余额生成发奖列表，可由 userbot 逐条回复报名消息自动发奖。"
)

EVENT_SUBSCRIPTIONS = [
    {
        "events": ["payment_confirmed"],
        "source": ["external_payment_notice", "userbot"],
        "scope": "all_allowed_chats",
        "entry_key": "join_quick_qa",
        "description": "付款确认用于付费快问快答大厅的报名入场，插件按门槛金额二次校验。",
    },
    {
        "events": ["callback_query", "session_close"],
        "source": ["interaction_bot"],
        "scope": "all_allowed_chats",
        "entry_key": "join_quick_qa",
        "description": "交互 Bot 承接题库选择、三选一抢答和题库草稿保存按钮；开局消息由规则关键词路由。",
    },
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "description": "账号主人或授权管理员通过 UserBot 命令管理大厅；题库主路径在 Web 配置页维护。",
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
                "题库：在 Web 配置页添加 URL，点击获取并整理为题库后保存配置\n"
                "{prefix}quickqa 0 创建免费报名大厅，玩家发送“报名”参与\n"
                "{prefix}quickqa 100 创建转账报名大厅\n"
                "{prefix}quickqa 100 20 创建本局最多 20 题的报名大厅\n"
                "报名大厅下方按钮开始选择题库\n"
                "{prefix}quickqa kb list 查看题库"
            ),
        },
        "entry_fee": {
            "type": "integer",
            "title": "默认门槛金额",
            "default": DEFAULT_ENTRY_FEE,
            "minimum": 0,
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
            "maximum": MAX_PLAYERS,
        },
        "max_questions_per_game": {
            "type": "integer",
            "title": "每局最多题数",
            "default": DEFAULT_MAX_QUESTIONS_PER_GAME,
            "minimum": 1,
            "maximum": MAX_QUESTIONS_PER_GAME,
        },
        "question_timeout_seconds": {
            "type": "integer",
            "title": "每题限时（秒）",
            "default": DEFAULT_QUESTION_TIMEOUT_SECONDS,
            "minimum": 5,
            "maximum": 1800,
        },
        "selection_timeout_seconds": {
            "type": "integer",
            "title": "题库选择限时（秒）",
            "default": DEFAULT_SELECTION_TIMEOUT_SECONDS,
            "minimum": 10,
            "maximum": 3600,
        },
        "reward_ratio": {
            "type": "number",
            "title": "发奖比例",
            "description": "基础奖池的可发奖金展示比例，默认 0.9。",
            "default": DEFAULT_REWARD_RATIO,
            "minimum": 0.01,
            "maximum": 1,
        },
        "settlement_base_amount": {
            "type": "integer",
            "title": "基础奖池单价",
            "description": "基础奖池 = 本值 × 参与人数，默认 1000。",
            "default": DEFAULT_SETTLEMENT_BASE_AMOUNT,
            "minimum": 0,
            "maximum": 100000000,
        },
        "settlement_point_multiplier": {
            "type": "integer",
            "title": "积分奖励系数",
            "description": "单人奖励 = 基础奖池单价 + 本值 × (积分 - 基准积分)。",
            "default": DEFAULT_SETTLEMENT_POINT_MULTIPLIER,
            "minimum": 0,
            "maximum": 100000000,
        },
        "settlement_base_points": {
            "type": "integer",
            "title": "基准积分",
            "description": "奖励公式中的基准积分，默认 4。",
            "default": DEFAULT_SETTLEMENT_BASE_POINTS,
            "minimum": 0,
            "maximum": 1000,
        },
        "payout_mode": {
            "type": "string",
            "title": "结算模式",
            "default": "auto",
            "enum": ["announce_only", "auto"],
            "description": "auto 会让 userbot 按报名消息 ID 逐条回复 +金额 发奖；announce_only 只公告发奖列表。",
        },
        "payout_interval_seconds": {
            "type": "number",
            "title": "自动发奖间隔（秒）",
            "description": "逐条回复 +金额 的间隔，用于降低高并发风控风险。",
            "default": DEFAULT_PAYOUT_INTERVAL_SECONDS,
            "minimum": 0,
            "maximum": 60,
        },
        "cleanup_delay_seconds": {
            "type": "integer",
            "title": "结束后清理延迟（秒）",
            "description": "问答结束后延迟多久删除本局相关消息，默认 120 秒。",
            "default": DEFAULT_CLEANUP_DELAY_SECONDS,
            "minimum": 0,
            "maximum": 3600,
        },
        "start_keyword": {
            "type": "string",
            "title": "开局关键词",
            "default": DEFAULT_START_KEYWORD,
            "minLength": 1,
            "maxLength": 32,
        },
        "free_join_keyword": {
            "type": "string",
            "title": "免费局报名关键词",
            "default": DEFAULT_FREE_JOIN_KEYWORD,
            "minLength": 1,
            "maxLength": 16,
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
            "maximum": MAX_AI_QUESTION_COUNT,
        },
        "ai_timeout_seconds": {
            "type": "integer",
            "title": "AI 请求超时（秒）",
            "description": "建议至少 600 秒；低于 300 秒的旧配置可保存，运行时会按 600 秒执行。",
            "default": DEFAULT_AI_TIMEOUT_SECONDS,
            "minimum": CONFIG_MIN_AI_TIMEOUT_SECONDS,
            "maximum": MAX_AI_TIMEOUT_SECONDS,
        },
        "max_source_chars": {
            "type": "integer",
            "title": "网页正文最大字符数",
            "default": DEFAULT_MAX_SOURCE_CHARS,
            "minimum": 1000,
            "maximum": MAX_SOURCE_CHARS,
        },
        "question_generation_prompt": {
            "type": "string",
            "title": "题库生成系统提示词",
            "default": AI_SYSTEM_PROMPT,
            "x-ui-widget": "textarea",
            "minLength": 20,
            "maxLength": 6000,
        },
        "knowledge_bases": {
            "type": "array",
            "title": "题库",
            "description": "可添加多组题库；启用的题库会在开局时供随机玩家选择，一个或多个题库可同时使用。",
            "default": [],
            "level": "account",
            "x-ui-widget": "config-list",
            "x-ui-summary": "{questions.length} 题 · {summary}",
            "x-ui-title-field": "title",
            "x-ui-description-field": "url",
            "x-ui-enabled-field": "enabled",
            "x-ui-reorderable": True,
            "x-ui-add-label": "手动添加题库",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "title": "启用",
                        "default": True,
                    },
                    "kb_id": {
                        "type": "string",
                        "title": "题库 ID",
                        "default": "",
                        "readOnly": True,
                    },
                    "title": {
                        "type": "string",
                        "title": "题库标题",
                        "default": "",
                    },
                    "url": {
                        "type": "string",
                        "title": "来源 URL",
                        "default": "",
                    },
                    "summary": {
                        "type": "string",
                        "title": "摘要",
                        "default": "",
                        "x-ui-widget": "textarea",
                    },
                    "questions": {
                        "type": "array",
                        "title": "题目 JSON",
                        "description": "通常由 AI 自动生成；手工编辑时保持 question/options/answer_index/explanation 结构。",
                        "default": [],
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "question": {"type": "string", "title": "题目"},
                                "options": {
                                    "type": "array",
                                    "title": "三个选项",
                                    "items": {"type": "string"},
                                },
                                "answer_index": {
                                    "type": "integer",
                                    "title": "正确选项序号",
                                    "minimum": 0,
                                    "maximum": 2,
                                },
                                "explanation": {"type": "string", "title": "解释"},
                            },
                            "required": ["question", "options", "answer_index"],
                        },
                    },
                    "created_at": {
                        "type": "number",
                        "title": "创建时间",
                        "default": 0,
                        "x-ui-hidden": True,
                    },
                },
                "required": ["enabled", "title", "questions"],
            },
        },
        "knowledge_bases_json": {
            "type": "string",
            "title": "旧版题库数据 JSON",
            "description": "兼容旧版本配置；新版本请使用结构化题库列表。",
            "default": "[]",
            "x-ui-widget": "textarea",
            "x-ui-hidden": True,
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
        "knowledge_bases",
    ],
}

CONFIG_ACTIONS = [
    {
        "key": "generate_knowledge_base",
        "title": "获取并整理为题库",
        "description": "填入公开 URL 后，插件会通过受控 HTTP 抓取网页正文，并调用 TelePilot AI 整理成三选一题库。",
        "placement": "field:knowledge_bases",
        "submit_label": "生成题库",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "title": "来源 URL",
                    "description": "支持 http/https；可用题库来源域名白名单限制可抓取范围。",
                    "default": "",
                },
                "title": {
                    "type": "string",
                    "title": "标题提示（可选）",
                    "description": "留空时由 AI 根据网页内容判断题库标题。",
                    "default": "",
                },
                "mode": {
                    "type": "string",
                    "title": "保存方式",
                    "description": "追加会合并到同 URL/同题库并按题干去重；替换会用本次结果覆盖匹配题库。",
                    "default": "append",
                    "enum": ["append", "replace"],
                },
                "question_count": {
                    "type": "integer",
                    "title": "本次生成题数（可选）",
                    "description": "填 0 使用配置里的单个 URL 生成题数。",
                    "default": 0,
                    "minimum": 0,
                    "maximum": MAX_AI_QUESTION_COUNT,
                },
                "target_total": {
                    "type": "integer",
                    "title": "增量补到题数（可选）",
                    "description": "追加模式下，如果已有同 URL 题库，会尽量补到这个总题数。",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 5000,
                },
            },
            "required": ["url"],
        },
    }
]


MANIFEST = Manifest(
    key="quick_qa",
    display_name="快问快答",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.33.0",
    author="Anoyou",
    description="支持 URL + AI 生成题库的转账报名三选一快问快答积分淘汰赛。",
    usage=USAGE,
    category="interactive",
    permissions=["send_message", "edit_message", "delete_message", "read_chat", "external_http", "ai_text"],
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
    config_actions=CONFIG_ACTIONS,
    interaction_entries=[
        {
            "key": "join_quick_qa",
            "title": "快问快答报名与抢答",
            "description": "玩家转账或发送免费报名关键词参与，达到人数后通过按钮选择题库并进行三选一抢答。",
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
                "actions": ["send_message", "edit_message", "delete_message", "answer_callback", "end_session", "result", "settlement"],
                "send_via": ["interaction_bot", "userbot_reply"],
            },
            "settlement": {
                "mode": "auto",
                "recipient_field": "items[].user_id",
                "amount_field": "items[].amount",
            },
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entry_fee": {
                        "type": "integer",
                        "title": "门槛金额",
                        "default": DEFAULT_ENTRY_FEE,
                        "minimum": 0,
                    },
                    "start_keyword": {
                        "type": "string",
                        "title": "开局关键词",
                        "default": DEFAULT_START_KEYWORD,
                    },
                    "max_questions_per_game": {
                        "type": "integer",
                        "title": "本局最多题数",
                        "default": DEFAULT_MAX_QUESTIONS_PER_GAME,
                        "minimum": 1,
                        "maximum": MAX_QUESTIONS_PER_GAME,
                    },
                },
                "required": [],
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
