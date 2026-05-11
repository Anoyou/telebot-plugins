"""九宫格骰子竞猜远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


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
        "template_title": {
            "type": "string",
            "title": "消息模板：标题",
            "default": "🎯 九宫格骰子竞猜（v{version}）",
            "minLength": 1,
            "maxLength": 120,
        },
        "template_target_line": {
            "type": "string",
            "title": "消息模板：目标点数行",
            "default": "目标点数：<b>{target_sum}</b>（9 格里唯一）",
            "minLength": 1,
            "maxLength": 200,
        },
        "template_guide_line": {
            "type": "string",
            "title": "消息模板：引导行",
            "default": "回复 <code>1-9</code> 选择你认为答案所在的格子。",
            "minLength": 1,
            "maxLength": 200,
        },
        "template_reward_line": {
            "type": "string",
            "title": "消息模板：奖励行",
            "default": "首个答对者奖励：<b>+{prize}</b> · 超时 {timeout} 秒",
            "minLength": 1,
            "maxLength": 200,
        },
        "delete_after_round": {
            "type": "integer",
            "title": "回合结束后删除消息延迟（秒，0为不删）",
            "default": 0,
            "minimum": 0,
            "maximum": 3600,
        },
        "template_preview": {
            "type": "string",
            "title": "模板变量预览",
            "default": "示例：🎯 九宫格骰子竞猜（v{version}）\\n\\n目标点数：{target_sum}（9 格里唯一）\\n\\n首个答对者奖励：+{prize} · 超时 {timeout} 秒\\n可用变量：{version} {target_sum} {prize} {timeout}",
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
        "delete_after_round",
    ],
}


MANIFEST = Manifest(
    key="dice_grid_hunt",
    display_name="九宫格骰子竞猜",
    version="1.0.10",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="发送九宫格骰子图片，公布唯一目标点数，群内抢答格子赢奖励",
    permissions=["send_message", "edit_message", "read_chat", "send_file"],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
