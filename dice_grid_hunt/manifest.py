"""九宫格骰子竞猜远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


TEMPLATE_SAMPLE_VARS = {
    "version": "1.1.5",
    "command": "dicegrid",
    "force_stop_command": "stop",
    "target_sum": "17",
    "answer_index": "6",
    "prize": "100",
    "timeout": "90",
    "guess_cooldown": "2.0",
    "winner": "小明",
    "elapsed": "8.2",
    "example": "100",
}


def _safe_render(template: str) -> str:
    try:
        return template.format_map(TEMPLATE_SAMPLE_VARS)
    except Exception:
        return template


ROUND_MESSAGE_TEMPLATE_DEFAULT = (
    "<b>九宫格竞猜</b>\n"
    "目标：<b>{target_sum}</b>，回 <code>1-9</code>\n"
    "奖 <b>+{prize}</b> · {timeout}s · 冷却 {guess_cooldown}s"
)
IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT = (
    "上一局还没结束。继续猜，或发 <code>,{command} {force_stop_command}</code> 结束。"
)
SUCCESS_MESSAGE_TEMPLATE_DEFAULT = (
    "{winner} 答对：<b>{answer_index}</b>\n"
    "用时 {elapsed}s · 奖励 <b>+{prize}</b>"
)
TIMEOUT_MESSAGE_TEMPLATE_DEFAULT = (
    "超时，答案是 <b>{answer_index}</b>，点数和 <b>{target_sum}</b>。"
)
CANCEL_MESSAGE_TEMPLATE_DEFAULT = "已结束当前九宫格竞猜。"
INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT = "请指定奖励金额，例如：,{command} {example}"
PRIZE_MESSAGE_TEMPLATE_DEFAULT = "+{prize}"
TEMPLATE_PREVIEW_RENDERED = _safe_render(ROUND_MESSAGE_TEMPLATE_DEFAULT)


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "schema",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "dicegrid",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "答题限时（秒）",
            "default": 90,
            "minimum": 10,
            "maximum": 86400,
        },
        "auto_next": {
            "type": "boolean",
            "title": "结束后自动下一轮",
            "default": False,
        },
        "next_delay": {
            "type": "integer",
            "title": "下一轮延迟（秒）",
            "default": 3,
            "minimum": 1,
            "maximum": 60,
        },
        "guess_cooldown": {
            "type": "number",
            "title": "同一用户答题冷却（秒）",
            "default": 2.0,
            "minimum": 0,
            "maximum": 30,
        },
        "template_placeholders": {
            "type": "string",
            "title": "可用占位符说明",
            "default": "开局：{target_sum} 目标点数，{prize} 奖励，{timeout} 限时秒数，{guess_cooldown} 答题冷却。\n结果：{winner} 答对者，{answer_index} 正确格子，{elapsed} 用时秒数。\n指令：{command} 触发指令，{force_stop_command} 结束参数，{example} 示例奖励。\n\n预览只使用固定示例值，不读取真实群消息，也不会触发发送。",
            "readOnly": True,
        },
        "round_message_template": {
            "type": "string",
            "title": "开局消息模板",
            "description": "支持占位符：{target_sum}、{prize}、{timeout}、{guess_cooldown}、{command}。示例见下方预览。",
            "default": ROUND_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 1200,
        },
        "in_progress_message_template": {
            "type": "string",
            "title": "进行中提示模板",
            "description": "支持占位符：{command}、{force_stop_command}",
            "default": IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 800,
        },
        "success_message_template": {
            "type": "string",
            "title": "答对结果模板",
            "description": "支持占位符：{winner}、{answer_index}、{target_sum}、{elapsed}、{prize}",
            "default": SUCCESS_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 1200,
        },
        "timeout_message_template": {
            "type": "string",
            "title": "超时结果模板",
            "description": "支持占位符：{answer_index}、{target_sum}",
            "default": TIMEOUT_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 800,
        },
        "cancel_message_template": {
            "type": "string",
            "title": "强制结束结果模板",
            "description": "强制结束当前回合时的提示文案。",
            "default": CANCEL_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 400,
        },
        "invalid_prize_message_template": {
            "type": "string",
            "title": "奖励参数错误模板",
            "description": "支持占位符：{command}、{example}",
            "default": INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 400,
        },
        "prize_message_template": {
            "type": "string",
            "title": "奖励入账消息模板",
            "description": "支持占位符：{prize}",
            "default": PRIZE_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 120,
        },
        "delete_after_round": {
            "type": "integer",
            "title": "回合结束后删除消息延迟（秒，0为不删）",
            "default": 0,
            "minimum": 0,
            "maximum": 3600,
        },
        "force_stop_command": {
            "type": "string",
            "title": "强制结束游戏参数",
            "default": "stop",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "template_preview": {
            "type": "string",
            "title": "模板渲染预览（只读，开局消息）",
            "description": "使用固定示例数据渲染最终消息，仅用于配置预览。",
            "default": TEMPLATE_PREVIEW_RENDERED,
            "readOnly": True,
        },
    },
    "required": [
        "command",
        "timeout",
        "auto_next",
        "next_delay",
        "guess_cooldown",
        "round_message_template",
        "in_progress_message_template",
        "success_message_template",
        "timeout_message_template",
        "cancel_message_template",
        "invalid_prize_message_template",
        "prize_message_template",
        "delete_after_round",
        "force_stop_command",
    ],
}


MANIFEST = Manifest(
    key="dice_grid_hunt",
    display_name="九宫格骰子竞猜",
    version="1.1.5",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="发送九宫格骰子图片，公布唯一目标点数，群内抢答格子赢奖励",
    permissions=["send_message", "edit_message", "read_chat", "send_file"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
