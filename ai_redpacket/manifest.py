"""AI 答题红包插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


PLUGIN_VERSION = "0.1.12"
QUESTION_PROMPT_PLACEHOLDER = """你是 TelePilot AI 红包插件的题库生成器。
只依据网页正文生成三选一选择题，并按每行一道题的 JSONL 输出，不要 Markdown。
每题必须恰好三个互不重复的选项，只有一个正确答案，answer 只能是 0、1、2。
题目必须能从正文中直接得到答案，不要编造，不要出主观题。"""
USAGE = (
    "配置页填写 URL、Provider 和模型后点击“生成/补齐题库”。"
    "命令：{prefix}{command} 按默认配置发红包；{prefix}{command} create 总金额 [题目数] [题库ID] 自定义创建；"
    "{prefix}{command} bank list 查看题库；{prefix}{command} list 查看红包；{prefix}{command} close 红包ID 关闭；"
    "{prefix}{command} reset [用户ID] 重置当天领取与答题限制；"
    "{prefix}{command} reset all 重置当天所有人的参与限制；{prefix}{command}-7 查询本周排行榜；"
    "{prefix}{command} help 查看帮助。红包领完或到期后自动结算，每周日 10:00 默认发布上一完整周期周榜。"
    "管理员创建、重置或关闭红包成功后会自动删除原命令消息，失败时保留。"
    "重置结果由交互 Bot 发送并在 3 秒后删除；题目按预约时间计时，超时提示 5 秒后删除且不消耗次数。"
    "答对结果会显示答题者姓名；未收到奖励时先在群里发言，再点击“申请补发奖励”，平台会校验状态并避免重复发放。"
    "{prefix}{command} list 仅显示进行中红包的题目/金额领取进度和开题消息链接，并提供历史未到账奖励补发入口；原命令自动删除，列表回执保留。"
)

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-category": "interactive",
    "x-usage-guide": USAGE,
    "additionalProperties": False,
    "properties": {
        "usage_preview": {
            "type": "string",
            "title": "使用说明（只读）",
            "readOnly": True,
            "default": (
                "1. 填写题库来源 URL，并保存插件配置。\n"
                "2. 选择 Provider 和模型，点击“生成/补齐题库”；已有题目会保留并补齐到目标数量。\n"
                "3. 直接发送 {prefix}{command} 按默认配置创建总额 150000、40 份的红包。\n"
                "4. 用户点击领取按钮，通过交互 Bot 完成三选一答题。\n"
                "5. 答对奖励固定由 userbot payout 发放；金额只支持整数。\n"
                "6. 测试后可发送 {prefix}{command} reset 重置自己，或发送 {prefix}{command} reset all 重置当天所有人的参与限制。\n"
                "7. 发送 {prefix}{command} bank list 查看题库；{prefix}{command} list 仅查看进行中红包的领取进度、开题消息链接和补发入口，原命令自动删除但列表回执保留。\n"
                "8. 发送 {prefix}{command} close 红包ID 关闭红包，{prefix}{command} help 查看完整帮助。\n"
                "9. 发送 {prefix}{command}-7 查询本周排行榜；每周日 10:00 默认自动发布上一完整周期。\n"
                "10. 管理员创建、重置或关闭红包成功后会自动删除原命令消息；参数错误或操作失败时保留。\n"
                "11. 重置结果由交互 Bot 发送并在 3 秒后删除；题目按预约时间计时，超时提示 5 秒后删除且不消耗次数。\n"
                "12. 答题结果显示答题者姓名；未收到奖励时先在群里发言，再点击“申请补发奖励”，平台会校验状态且不会重复发放。"
            ),
        },
        "command": {
            "type": "string",
            "title": "管理指令名",
            "x-ui-section": "基础设置",
            "x-ui-columns": 2,
            "x-ui-order": 10,
            "default": "airp",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "只填写指令本体，不包含系统指令前缀。",
        },
        "question_source_url": {
            "type": "string",
            "title": "题库来源 URL",
            "x-ui-section": "题库来源",
            "x-ui-columns": 1,
            "x-ui-order": 100,
            "default": "",
            "description": "首次生成时抓取并缓存正文；后续补齐题库会复用缓存，创建红包不会抓网页或调用 AI。",
        },
        "telepilot_provider": {
            "type": "string",
            "title": "题库生成 Provider（可选）",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 200,
            "default": "",
            "x-ui-widget": "llm-provider-select",
            "description": "留空由 TelePilot 自动选择可用 Provider。",
        },
        "telepilot_model": {
            "type": "string",
            "title": "题库生成模型（可选）",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 210,
            "default": "",
            "x-ui-widget": "llm-model-select",
            "x-ui-provider-field": "telepilot_provider",
            "x-ui-model-modality": "text",
            "description": "选择 Provider 后可指定模型；留空使用该 Provider 的默认模型。",
        },
        "generation_count": {
            "type": "integer",
            "title": "题库目标题数",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 220,
            "default": 200,
            "minimum": 100,
            "maximum": 500,
            "description": "范围 100-500。已有题库不足目标数量时会保留原题并继续补齐；达到目标后不会调用 AI。",
        },
        "default_questions": {
            "type": "integer",
            "title": "默认红包份数（每份一题）",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 400,
            "default": 40,
            "minimum": 1,
            "maximum": 500,
            "description": "创建红包未指定数量时，从题库抽取多少道题并分成多少份奖励；每位用户只回答其中一道题。",
        },
        "default_total_amount": {
            "type": "integer",
            "title": "默认红包总金额",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 390,
            "default": 150000,
            "minimum": 1,
            "maximum": 1000000000,
            "description": "单独发送管理指令时使用的总奖池金额，必须是整数。",
        },
        "question_bank_status": {
            "type": "string",
            "title": "题库状态（只读）",
            "x-ui-section": "题库来源",
            "x-ui-columns": 1,
            "x-ui-order": 110,
            "default": "尚未生成",
            "readOnly": True,
            "description": "生成成功后显示题库名称、实际有效题目数和默认题库 ID。",
        },
        "question_bank_id": {
            "type": "string",
            "title": "默认题库",
            "x-ui-section": "基础设置",
            "x-ui-columns": 2,
            "x-ui-order": 20,
            "x-ui-widget": "dynamic-select",
            "x-ui-options-field": "question_bank_options",
            "default": "",
            "description": "选择创建红包时默认使用的题库；点击生成/补齐题库后会自动刷新选项。",
        },
        "question_bank_options": {
            "type": "array",
            "title": "题库选项",
            "default": [],
            "x-ui-hidden": True,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["value", "label"],
            },
        },
        "question_bank_count": {
            "type": "integer",
            "title": "题库实际题目数",
            "default": 0,
            "readOnly": True,
            "x-ui-hidden": True,
        },
        "question_bank_generated_at": {
            "type": "string",
            "title": "题库生成时间",
            "default": "",
            "readOnly": True,
            "x-ui-hidden": True,
        },
        "daily_limit": {
            "type": "integer",
            "title": "每日成功领取上限",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 410,
            "default": 1,
            "minimum": 1,
            "maximum": 100,
            "description": "同一个 Telegram 用户每天最多答对并领取多少次红包。",
        },
        "retry_count": {
            "type": "integer",
            "title": "答错后重试次数",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 420,
            "default": 1,
            "minimum": 0,
            "maximum": 10,
            "description": "每道题首次答错后还能重试多少次；设为 0 表示答错后立即结束当天挑战。",
        },
        "pin_packet_message": {
            "type": "boolean",
            "title": "自动置顶红包入口",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 425,
            "default": True,
            "description": "创建红包后由交互 Bot 置顶带领取按钮的原消息；需要 Bot 具备群管理置顶权限。",
        },
        "reward_min": {
            "type": "integer",
            "title": "单题最低金额",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 430,
            "default": 1,
            "minimum": 1,
            "maximum": 1000000000,
            "description": "红包金额只允许整数。",
        },
        "reward_max": {
            "type": "integer",
            "title": "单题最高金额",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 440,
            "default": 10000,
            "minimum": 1,
            "maximum": 1000000000,
            "description": "红包金额只允许整数，且不得低于最低金额。",
        },
        "redpacket_ttl_seconds": {
            "type": "integer",
            "title": "红包有效期（秒）",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 450,
            "default": 86400,
            "minimum": 60,
            "maximum": 604800,
        },
        "answer_timeout_seconds": {
            "type": "integer",
            "title": "题目预约时间（秒）",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 460,
            "default": 300,
            "minimum": 30,
            "maximum": 3600,
            "description": "按此实际配置为每次答题或重试计时；超时后题目消息会提示失效并回归题库，本次不消耗次数，5 秒后自动删除。",
        },
        "timezone": {
            "type": "string",
            "title": "每日限制与周榜时区",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 470,
            "default": "Asia/Shanghai",
        },
        "weekly_auto_publish": {
            "type": "boolean",
            "title": "每周日自动发布周榜",
            "x-ui-section": "红包与答题",
            "x-ui-columns": 2,
            "x-ui-order": 480,
            "default": True,
            "description": "开启后，每周日 10:00 自动发布上一周期（上周日 10:00 至本周日 10:00）的排行榜。",
        },
        "max_source_chars": {
            "type": "integer",
            "title": "网页正文最大字符数",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 230,
            "default": 120000,
            "minimum": 1000,
            "maximum": 300000,
        },
        "ai_timeout_seconds": {
            "type": "integer",
            "title": "AI 生成超时（秒）",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 240,
            "default": 600,
            "minimum": 30,
            "maximum": 3600,
        },
        "generation_concurrency": {
            "type": "integer",
            "title": "AI 并发批次数",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 250,
            "default": 3,
            "minimum": 1,
            "maximum": 5,
            "description": "仅在目标超过单次 200 题或需要补齐时生效。Provider 容易限流时可调低。",
        },
        "generation_max_output_tokens": {
            "type": "integer",
            "title": "单次最大输出 Token",
            "x-ui-section": "AI 生成",
            "x-ui-columns": 2,
            "x-ui-order": 260,
            "default": 65536,
            "minimum": 4096,
            "maximum": 131072,
            "description": "题库属于长时间离线任务。默认允许一次输出完整 100-200 题；模型最大输出较小时可调低。",
        },
        "question_generation_prompt": {
            "type": "string",
            "title": "AI 出题系统提示词",
            "x-ui-section": "AI 出题要求",
            "x-ui-columns": 1,
            "x-ui-order": 300,
            "default": "",
            "x-ui-placeholder": QUESTION_PROMPT_PLACEHOLDER,
            "description": "留空使用插件内置提示词；自定义时仍必须要求 JSONL 和三选一题型。",
        },
        "packet_message_template": {
            "type": "string",
            "title": "红包开场模板",
            "default": "<b>AI 答题红包</b>\n总金额：<code>{total_amount}</code>\n题目数量：<code>{question_count}</code>\n红包 ID：<code>{redpacket_id}</code>\n\n今日日期：<code>{date}</code>\n每人每天最多成功领取 {daily_limit} 次；每题答错后可重试 {retry_count} 次。",
            "description": "占位符：{total_amount} 总金额；{question_count} 题目数；{redpacket_id} 红包 ID；{date} 今日日期；{daily_limit} 每日成功上限；{retry_count} 重试次数。支持 Telegram HTML。",
        },
        "question_message_template": {
            "type": "string",
            "title": "答题题面模板",
            "default": "<b>AI 红包题目</b>\n{question}\n\n{options}\n\n请选择唯一正确答案。",
            "description": "占位符：{question} 题目；{options} 三个选项；{date} 今日日期；{daily_limit} 每日成功上限；{retry_count} 重试次数。支持 Telegram HTML。",
        },
        "success_message_template": {
            "type": "string",
            "title": "答对结果模板",
            "default": "<b>AI 红包答题结果</b>\n{question}\n\n结果：<b>答对了，获得 {reward}</b>\n正确答案：{answer}\n解析：{explanation}\n来源：{source}",
            "description": "占位符：{question}、{reward}、{answer}、{explanation}、{source}、{date}、{daily_limit}、{retry_count}。",
        },
        "failed_message_template": {
            "type": "string",
            "title": "挑战失败模板",
            "default": "<b>AI 红包答题结果</b>\n{question}\n\n结果：<b>答题机会已用完，今天的挑战已结束</b>\n正确答案：{answer}\n解析：{explanation}\n来源：{source}",
            "description": "占位符：{question}、{reward}、{answer}、{explanation}、{source}、{date}、{daily_limit}、{retry_count}。",
        },
        "settlement_message_template": {
            "type": "string",
            "title": "红包结算模板",
            "default": "<b>AI 红包每日结算</b>\n红包 ID：<code>{redpacket_id}</code>\n状态：{status}\n已领取：<code>{claimed_amount}</code> / <code>{total_amount}</code>\n领取人数：<code>{claim_count}</code>\n\n运气王：<b>{luckiest_name}</b> · {luckiest_reward}\n倒霉蛋：<b>{unluckiest_name}</b> · {unluckiest_reward}\n\n{ranking}",
            "description": "占位符：{redpacket_id}、{status}、{claimed_amount}、{total_amount}、{claim_count}、{luckiest_name}、{luckiest_reward}、{unluckiest_name}、{unluckiest_reward}、{ranking}。",
        },
        "weekly_message_template": {
            "type": "string",
            "title": "每周榜单模板",
            "default": "<b>{weekly_title}</b>\n周期：<code>{period_start}</code> 至 <code>{period_end}</code>\n\n<blockquote expandable><b>答对次数 TOP 5</b>\n{count_ranking}\n\n<b>获得奖金 TOP 5</b>\n{reward_ranking}</blockquote>",
            "description": "占位符：{weekly_title}、{period_start}、{period_end}、{count_ranking}、{reward_ranking}。",
        },
        "template_placeholders": {
            "type": "string",
            "title": "模板占位符（只读）",
            "readOnly": True,
            "default": "通用：{date} {daily_limit} {retry_count}\n红包：{total_amount} {question_count} {redpacket_id}\n题目：{question} {options}\n结果：{question} {reward} {answer} {explanation} {source}\n结算：{status} {claimed_amount} {claim_count} {luckiest_name} {luckiest_reward} {unluckiest_name} {unluckiest_reward} {ranking}\n周榜：{weekly_title} {period_start} {period_end} {count_ranking} {reward_ranking}",
        },
        "packet_message_preview": {
            "type": "string",
            "title": "红包开场预览",
            "readOnly": True,
            "default": "",
        },
        "question_message_preview": {
            "type": "string",
            "title": "答题题面预览",
            "readOnly": True,
            "default": "",
        },
        "success_message_preview": {
            "type": "string",
            "title": "答对结果预览",
            "readOnly": True,
            "default": "",
        },
        "failed_message_preview": {
            "type": "string",
            "title": "挑战失败预览",
            "readOnly": True,
            "default": "",
        },
        "settlement_message_preview": {
            "type": "string",
            "title": "红包结算预览",
            "readOnly": True,
            "default": "",
        },
        "weekly_message_preview": {
            "type": "string",
            "title": "每周榜单预览",
            "readOnly": True,
            "default": "",
        },
    },
    "required": [
        "command",
        "question_source_url",
        "default_questions",
        "reward_min",
        "reward_max",
    ],
}

CONFIG_ACTIONS = [
    {
        "key": "generate_question_bank",
        "title": "继续生成/补齐题库",
        "description": "首次抓取并缓存上方 URL 正文；每批成功后立即保存。可在任务窗口中断或终止，切换 Provider/模型后从已有题目继续。",
        "placement": "field:question_source_url",
        "submit_label": "继续生成/补齐题库",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": [],
        },
    }
]

EVENT_SUBSCRIPTIONS = [
    {
        "events": ["command"],
        "source": ["userbot"],
        "scope": "owner_only",
        "description": "管理员通过 UserBot 命令生成题库、创建和管理红包。",
    },
    {
        "events": ["callback_query", "session_close", "session_expired"],
        "source": ["interaction_bot"],
        "scope": "all_allowed_chats",
        "entry_key": "ai_redpacket_claim",
        "description": "交互 Bot 承接领取按钮、三选一答题和会话清理。",
    },
]

ALLOWED_HOSTS = [
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
]

INTERACTION_ENTRIES = [
    {
        "key": "ai_redpacket_claim",
        "title": "AI 红包领取与答题",
        "description": "处理红包领取按钮和三选一答题按钮。",
        "interaction_profile": "session_game",
        "launch_mode": "hybrid",
        "dispatch_modes": ["public_keyword"],
        "events": ["callback_query", "session_close", "session_expired"],
        "session_scope": "user",
        "session_policy": {
            "ttl_seconds": 3600,
            "duplicate_start": "continue",
            "close_on": ["completed", "session_close", "session_expired"],
        },
        "payload_contract": {
            "required_envelope": ["source", "actor", "trigger", "session"],
            "required_event_fields": ["type", "chat_id"],
        },
        "result_contract": {
            "actions": ["send_message", "edit_message", "delete_message", "pin_message", "answer_callback", "payout", "end_session"],
        },
        "settlement": {
            "mode": "auto",
            "recipient_field": "reply_to_user_id",
            "amount_field": "amount",
        },
        "message_channels": {"public_keyword": "interaction_bot"},
        "money_channel": "userbot_reply",
        "preserve_command_trigger": True,
        "callback_fast_ack": False,
    }
]

MANIFEST = Manifest(
    key="ai_redpacket",
    display_name="AI 答题红包",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.59.1",
    author="Anoyou",
    description="从网页生成 AI 三选一题库，并通过交互 Bot 答题、UserBot payout 发放整数红包。",
    usage=USAGE,
    category="interactive",
    permissions=["send_message", "edit_message", "delete_message", "read_chat", "external_http", "ai_text", "payout"],
    allowed_hosts=ALLOWED_HOSTS,
    config_schema=CONFIG_SCHEMA,
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    capabilities={},
    strict_trace=True,
    interaction_profile="session_game",
    config_actions=CONFIG_ACTIONS,
    interaction_entries=INTERACTION_ENTRIES,
)


__all__ = ["MANIFEST"]
