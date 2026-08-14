"""私聊助理远程插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


VERSION = "0.1.3"
USAGE = (
    "在需要监听私聊的账号上安装并启用插件，填写本插件专用的 Telegram Bot Token 和通知接收 Chat ID。"
    "接收人必须先私聊该 Bot 并点击开始。插件只监听该账号收到的 incoming 私聊，不处理群聊或账号自己发出的消息；"
    "同一发送者在 60 秒窗口内的多条消息会合并为一条通知。运行问题可在插件日志与外部 HTTP 记录中排查。"
)
EVENT_SUBSCRIPTIONS = [
    {
        "events": ["message"],
        "source": ["userbot"],
        "scope": "all_allowed_chats",
        "description": "监听启用账号收到的私聊消息；插件内部再次校验私聊类型并排除专用通知 Bot 自身。",
    }
]
CAPABILITIES: dict[str, object] = {}

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": USAGE,
    "additionalProperties": False,
    "properties": {
        "bot_token": {
            "type": "string",
            "title": "专用通知 Bot Token",
            "description": "从 BotFather 获取，仅供本插件向指定接收人单向发送通知。配置会按敏感字段加密保存。",
            "format": "password",
            "x-sensitive": True,
            "default": "",
            "level": "account",
            "minLength": 1,
        },
        "monitored_account_name": {
            "type": "string",
            "title": "监控账号名（可选覆盖）",
            "description": "默认自动读取当前账号姓名和用户名；只有希望使用自定义显示名称时才填写。",
            "default": "",
            "level": "account",
        },
        "recipient_chat_id": {
            "type": "integer",
            "title": "通知接收 Chat ID",
            "description": "接收通知的 Telegram 用户 Chat ID，可填写当前被监听账号，也可填写另一个账号。接收人须先启动上面的 Bot。",
            "level": "account",
        },
        "aggregation_seconds": {
            "type": "integer",
            "title": "消息汇总窗口（秒）",
            "description": "从同一发送者第一条消息开始计算，在窗口内收到的消息合并发送。默认 60 秒。",
            "default": 60,
            "minimum": 10,
            "maximum": 600,
            "level": "account",
        },
    },
    "required": ["bot_token", "recipient_chat_id", "aggregation_seconds"],
}

MANIFEST = Manifest(
    key="private_chat_assistant",
    display_name="私聊助理",
    version=VERSION,
    min_telepilot_version="0.97.0-beta.1",
    author="Anoyou",
    description="监听账号收到的私聊消息，并通过插件专用 Bot 向指定账号发送聚合提醒",
    usage=USAGE,
    category="automation",
    permissions=["read_chat", "resolve_entity", "external_http"],
    allowed_hosts=["api.telegram.org"],
    event_subscriptions=EVENT_SUBSCRIPTIONS,
    requires_platform_capabilities=[],
    capabilities=CAPABILITIES,
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
