from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '发送 {prefix}{command} 后按原 Pagermaid 玩法触发 AI 查询能力；可在配置页调整触发指令名并开启或关闭插件。命令只填写本体，不要包含系统前缀。',
    "additionalProperties": False,
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "ais",
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
USAGE = ('发送 {prefix}{command} 后按原 Pagermaid 玩法触发 AI '
 '查询能力；可在配置页调整触发指令名并开启或关闭插件。命令只填写本体，不要包含系统前缀。事件订阅：账号主人或授权管理员通过 userbot 命令触发；输出通过平台 MessageOps '
 '受控发送，并可在日志 Trace 中排查。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="ais-byRBQ",
    display_name="ais-byRBQ",
    version="1.0.6",
    min_telepilot_version="0.33.0",
    min_telebot_version="0.10.2",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="AI 查询插件，支持联网搜索增强、参考网址追加、实体查询图片预览、API URL/base url 快速切换与备用模型重试",
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
