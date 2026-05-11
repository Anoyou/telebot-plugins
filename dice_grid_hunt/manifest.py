"""九宫格骰子竞猜远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


TEMPLATE_SAMPLE_VARS = {
    "version": "1.1.4",
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


TEMPLATE_TITLE_DEFAULT = "🎯 九宫格骰子竞猜（v{version}）"
TEMPLATE_TARGET_LINE_DEFAULT = "目标点数：<b>{target_sum}</b>（9 格里唯一）"
TEMPLATE_GUIDE_LINE_DEFAULT = "回复 <code>1-9</code> 选择你认为答案所在的格子。"
TEMPLATE_REWARD_LINE_DEFAULT = "首个答对者奖励：<b>+{prize}</b> · 超时 {timeout} 秒"
ROUND_MESSAGE_TEMPLATE_DEFAULT = (
    "<b>{title}</b>\n\n"
    "{target_line}\n\n"
    "{guide_line}\n"
    "{reward_line}"
)
IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT = (
    "上一轮进行中，请先完成上一轮游戏。\n\n"
    "如需强制结束，请输入 <code>,{command} {force_stop_command}</code>。"
)
SUCCESS_MESSAGE_TEMPLATE_DEFAULT = (
    "🏆 {winner} 答对！答案是 <b>{answer_index}</b>，点数和 <b>{target_sum}</b>\n"
    "⏱️ {elapsed} 秒 · 奖励 <b>+{prize}</b>"
)
TIMEOUT_MESSAGE_TEMPLATE_DEFAULT = (
    "⏰ 本轮超时，答案是第 <b>{answer_index}</b> 格（点数和 <b>{target_sum}</b>）。"
)
CANCEL_MESSAGE_TEMPLATE_DEFAULT = "✅ 已强制结束当前九宫格骰子竞猜。"
INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT = "请指定奖励金额，例如：,{command} {example}"
PRIZE_MESSAGE_TEMPLATE_DEFAULT = "+{prize}"
TEMPLATE_PREVIEW_RENDERED = _safe_render(ROUND_MESSAGE_TEMPLATE_DEFAULT).format(
    title=_safe_render(TEMPLATE_TITLE_DEFAULT),
    target_line=_safe_render(TEMPLATE_TARGET_LINE_DEFAULT),
    guide_line=_safe_render(TEMPLATE_GUIDE_LINE_DEFAULT),
    reward_line=_safe_render(TEMPLATE_REWARD_LINE_DEFAULT),
)


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
            "default": "{version}：插件版本号（示例 1.1.3）\n{command}：插件指令名（示例 dicegrid）\n{force_stop_command}：强制结束参数（示例 stop）\n{target_sum}：本回合目标点数（示例 17）\n{answer_index}：正确格子序号（示例 6）\n{prize}：本回合奖励金额（示例 100）\n{timeout}：本回合超时秒数（示例 90）\n{guess_cooldown}：答题冷却秒数（示例 2.0）\n{winner}：答对用户昵称（示例 小明）\n{elapsed}：答题用时秒（示例 8.2）\n{example}：示例奖励参数（示例 100）\n\n预览仅使用固定示例值，不读取真实群消息，也不会触发发送。",
            "readOnly": True,
        },
        "template_title": {
            "type": "string",
            "title": "消息模板：标题",
            "description": "用于题目消息首行。支持占位符：{version}",
            "default": TEMPLATE_TITLE_DEFAULT,
            "minLength": 1,
            "maxLength": 120,
        },
        "template_target_line": {
            "type": "string",
            "title": "消息模板：目标点数行",
            "description": "用于展示目标点数。支持占位符：{target_sum}",
            "default": TEMPLATE_TARGET_LINE_DEFAULT,
            "minLength": 1,
            "maxLength": 200,
        },
        "template_guide_line": {
            "type": "string",
            "title": "消息模板：引导行",
            "description": "用于引导用户参与。通常不需要占位符。",
            "default": TEMPLATE_GUIDE_LINE_DEFAULT,
            "minLength": 1,
            "maxLength": 200,
        },
        "template_reward_line": {
            "type": "string",
            "title": "消息模板：奖励行",
            "description": "用于展示奖励和超时。支持占位符：{prize}、{timeout}",
            "default": TEMPLATE_REWARD_LINE_DEFAULT,
            "minLength": 1,
            "maxLength": 200,
        },
        "round_message_template": {
            "type": "string",
            "title": "消息模板：开局完整消息",
            "description": "支持占位符：{title}、{target_line}、{guide_line}、{reward_line}",
            "default": ROUND_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 1200,
        },
        "in_progress_message_template": {
            "type": "string",
            "title": "消息模板：进行中提示",
            "description": "支持占位符：{command}、{force_stop_command}",
            "default": IN_PROGRESS_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 800,
        },
        "success_message_template": {
            "type": "string",
            "title": "消息模板：答对结果",
            "description": "支持占位符：{winner}、{answer_index}、{target_sum}、{elapsed}、{prize}",
            "default": SUCCESS_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 1200,
        },
        "timeout_message_template": {
            "type": "string",
            "title": "消息模板：超时结果",
            "description": "支持占位符：{answer_index}、{target_sum}",
            "default": TIMEOUT_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 800,
        },
        "cancel_message_template": {
            "type": "string",
            "title": "消息模板：强制结束结果",
            "description": "强制结束当前回合时的提示文案。",
            "default": CANCEL_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 400,
        },
        "invalid_prize_message_template": {
            "type": "string",
            "title": "消息模板：奖励参数错误",
            "description": "支持占位符：{command}、{example}",
            "default": INVALID_PRIZE_MESSAGE_TEMPLATE_DEFAULT,
            "minLength": 1,
            "maxLength": 400,
        },
        "prize_message_template": {
            "type": "string",
            "title": "消息模板：奖励入账消息",
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
        "success_preview": {
            "type": "string",
            "title": "模板渲染预览（只读，答对结果）",
            "description": "使用固定示例数据渲染最终消息，仅用于配置预览。",
            "default": _safe_render(SUCCESS_MESSAGE_TEMPLATE_DEFAULT),
            "readOnly": True,
        },
        "timeout_preview": {
            "type": "string",
            "title": "模板渲染预览（只读，超时结果）",
            "description": "使用固定示例数据渲染最终消息，仅用于配置预览。",
            "default": _safe_render(TIMEOUT_MESSAGE_TEMPLATE_DEFAULT),
            "readOnly": True,
        },
    },
    "required": [
        "command",
        "timeout",
        "auto_next",
        "next_delay",
        "guess_cooldown",
        "template_title",
        "template_target_line",
        "template_guide_line",
        "template_reward_line",
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
    version="1.1.4",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="发送九宫格骰子图片，公布唯一目标点数，群内抢答格子赢奖励",
    permissions=["send_message", "edit_message", "read_chat", "send_file"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
