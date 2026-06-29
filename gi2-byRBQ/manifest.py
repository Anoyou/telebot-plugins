from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '发送 {prefix}{command} 按原 Pagermaid 方式触发图片生成/查询能力；可在配置页调整触发指令名和启用状态。外部请求失败时请检查插件日志和站点可用性。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "gi2",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$"
        },
        "enabled": {
            "type": "boolean",
            "title": "是否启用",
            "default": True
        }
    },
    "required": ["command", "enabled"]
}

# TelePilot 0.41 Event Bus metadata.
USAGE = ('发送 {prefix}{command} 按原 Pagermaid '
 '方式触发图片生成/查询能力；可在配置页调整触发指令名和启用状态。外部请求失败时请检查插件日志和站点可用性。事件订阅：账号主人或授权管理员通过 userbot 命令触发；输出通过平台 '
 'MessageOps 受控发送，并可在日志 Trace 中排查。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="gi2-byRBQ",
    display_name="gi2-byRBQ",
    version="1.0.6",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.2",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="图片生成与改图插件，支持直接生图、回复图片改图、GI2 结果图原地继续编辑，并对高风险原图断流做诊断提示",
    permissions=["send_message", "edit_message", "read_chat"],

    category="utility",
    interaction_entries=[],
    config_schema=CONFIG_SCHEMA,
)

# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
