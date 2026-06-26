"""十点半纸牌游戏 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "10d",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
        },
        "timeout": {
            "type": "integer",
            "title": "每回合限时（秒）",
            "default": 30,
            "minimum": 10,
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
            "minimum": 1,
            "maximum": 10,
        },
    },
    "required": ["command", "timeout", "lobby_timeout", "max_players"],
}


MANIFEST = Manifest(
    key="ten_half",
    display_name="十点半",
    version="0.2.0",
    min_telepilot_version="0.30.4",
    min_telebot_version="0.10.0",
    author="Anoyou",
    description="经典十点半纸牌游戏：支持多人对战、加倍、五小等规则",
    permissions=["send_message", "edit_message", "read_chat"],

    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[{
        "key": "start_ten_half",
        "title": "开始十点半",
        "description": "由交互 Bot 在群内开启一局十点半纸牌游戏。",
        "interaction_profile": "session_game",
        "launch_mode": "hybrid",
        "session_scope": "chat",
        "events": ["payment_confirmed", "keyword", "message", "callback_query", "session_close"],
        "preserve_command_trigger": True,
        "command_fallback": {"enabled": True, "command": "10d", "mode": "hint_only"},
        "session_policy": {
            "ttl_seconds": 300,
            "duplicate_start": "reject",
            "close_on": ["winner", "timeout", "session_close"],
        },
        "payload_contract": {
            "required_envelope": ["source", "actor", "trigger", "session"],
            "required_event_fields": ["type", "chat_id"],
        },
        "result_contract": {
            "actions": ["send_message", "end_session", "result", "settlement"],
            "send_via": ["interaction_bot", "userbot_reply", "bbot_notice"],
        },
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "bet": {
                    "type": "integer",
                    "title": "下注金额",
                    "default": 100,
                    "minimum": 1,
                },
                "timeout": {
                    "type": "integer",
                    "title": "每回合限时（秒）",
                    "default": 30,
                    "minimum": 10,
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
                    "minimum": 1,
                    "maximum": 10,
                },
            },
        },
        "settlement": {
            "mode": "announce_only",
        },
    }],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
