"""Bot 防广告守卫远程模块 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-category": "automation",
    "x-interaction-entries": [],
    "additionalProperties": False,
    "properties": {
        "target_chats": {
            "type": "string",
            "title": "目标群组",
            "description": "建议填写 -100... 群 ID；也支持 @username（仅在事件对象已带 chat.username 时匹配）。多个用逗号、空格或换行分隔。为空时不处理任何群。",
            "default": "",
            "x-ui-widget": "textarea",
            "minLength": 0,
        },
        "allowed_bots": {
            "type": "string",
            "title": "Bot 白名单",
            "description": "每行一个 Bot username，@ 可写可不写，大小写不敏感。也可混填 Bot ID；@bot 文本提及按 username 判断，inline/bot 发言可按 Bot ID 或 @username 判断。",
            "default": "",
            "x-ui-widget": "textarea",
        },
        "delete_untrusted_bot_mentions": {
            "type": "boolean",
            "title": "删除非白名单 @bot 提及消息",
            "description": "用户消息中出现 @xxxbot 且该 Bot 不在白名单内时，删除这条消息并写日志。远程模块沙箱不支持踢人/禁言。",
            "default": True,
        },
        "delete_inline_bot_messages": {
            "type": "boolean",
            "title": "删除非白名单 inline Bot 消息",
            "description": "删除用户通过非白名单 inline Bot 发送的消息。",
            "default": True,
        },
        "delete_bot_sender_messages": {
            "type": "boolean",
            "title": "删除非白名单 Bot 发言",
            "description": "当事件对象可识别发送者是 Bot 且不在白名单内时，删除该消息。",
            "default": True,
        },
        "delete_join_messages_for_known_bots": {
            "type": "boolean",
            "title": "删除可识别 Bot 入群服务消息",
            "description": "仅在入群事件对象直接包含 Bot 用户信息时生效；远程模块不会调用 get_entity 额外解析成员。",
            "default": True,
        },
        "announce": {
            "type": "boolean",
            "title": "群内提示",
            "description": "删除成功后是否在群内发送提示。广告群建议关闭，只看模块日志。",
            "default": False,
        },
        "dry_run": {
            "type": "boolean",
            "title": "演练模式",
            "description": "只写日志，不删除消息。建议首次配置目标群和白名单时先开启。",
            "default": False,
        },
    },
    "required": [
        "target_chats",
        "allowed_bots",
        "delete_untrusted_bot_mentions",
        "delete_inline_bot_messages",
        "delete_bot_sender_messages",
        "delete_join_messages_for_known_bots",
        "announce",
        "dry_run",
    ],
}


MANIFEST = Manifest(
    key="bot_mute_guard",
    display_name="Bot 防广告守卫",
    version="1.0.2",
    min_telepilot_version="0.19.2",
    author="Anoyou",
    description="针对指定群组删除非白名单 @bot 提及、inline Bot 与 Bot 发言广告触发消息",
    permissions=["send_message", "delete_message"],
    category="automation",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)


__all__ = ["MANIFEST"]
