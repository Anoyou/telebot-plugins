"""青娃PT (qingwapt.com) 置顶促销插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest


PROMOTE_USAGE_TEMPLATE_DEFAULT = (
    "用法：{prefix}{command} <种子ID> [选项]\n\n"
    "选项：\n"
    "  free/2x — 促销类型（默认 free）\n"
    "  1d/2d/3d/7d — 时长（默认 1天）\n"
    "  bid=100 — 竞价蝌蚪\n"
    "  reward=50 — 奖励蝌蚪\n"
    "  users=10 — 奖励人数\n\n"
    "示例：{prefix}{command} 12345 free 7d bid=100"
)
PROMOTE_STATUS_TEMPLATE_DEFAULT = "{icon} {message}"
PROMOTE_READY_TEMPLATE_DEFAULT = (
    "📋 ID 为 {torrent_id} 的种子符合促销条件\n\n"
    "{params}\n\n"
    "⏳ 正在计算预计消耗..."
)
PROMOTE_CONFIRMING_TEMPLATE_DEFAULT = (
    "📋 ID 为 {torrent_id} 的种子符合促销条件\n\n"
    "{params}\n"
    "预计消耗：{cost} 蝌蚪\n"
    "计算方式：{expression}\n\n"
    "⏳ 正在确认置顶..."
)
PROMOTE_SUCCESS_TEMPLATE_DEFAULT = (
    "✅ 种子置顶促销成功！\n\n"
    "<b>{torrent_header}</b>\n"
    "<pre><code class=\"language-副标题与促销明细\">"
    "{subtitle}\n"
    "{params}\n"
    "消耗：{cost} 蝌蚪"
    "</code></pre>"
)
INFO_OK_TEMPLATE_DEFAULT = (
    "📋 ID 为 {torrent_id} 的种子当前符合促销条件。\n"
    "{details_url}"
)

# TelePilot 0.41 Event Bus metadata.
USAGE = ('发送 {prefix}{command} 种子ID [促销参数] 触发青娃 PT 置顶促销；先在全局配置填站点 Cookie，再按示例设置 '
 'free/2x、时长、竞价和奖励参数。事件订阅：管理员命令走 userbot；群内关键词和会话消息走 interaction_bot。输出只使用 '
 'interaction_bot 或 userbot_reply 受控通道。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'},
 {'events': ['message', 'session_close'],
  'source': ['interaction_bot'],
  'scope': 'rule_bound',
  'description': '交互规则命中后由交互 Bot 投递会话事件。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="pt_promote",
    display_name="PT 种子促销",
    version="1.0.20",
    min_telepilot_version="0.33.0",
    author="xiaoyou",
    description="在青娃PT置顶促销某个种子（消耗蝌蚪）",
    category="utility",
    interaction_profile="utility_trigger",
    interaction_entries=[{'key': 'promote_torrent',
  'title': '触发种子促销',
  'description': '由交互 Bot 收到关键词或回复消息后触发 PT 种子促销流程。',
  'interaction_profile': 'utility_trigger',
  'launch_mode': 'hybrid',
  'session_scope': 'user',
  'events': ['keyword', 'message', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'pt', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 600,
                     'duplicate_start': 'reject',
                     'close_on': ['completed', 'failed', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message', 'end_session', 'result'],
                      'send_via': ['interaction_bot', 'userbot_reply']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'torrent_id': {'type': 'string', 'title': '种子 ID', 'default': ''},
                                  'options': {'type': 'string', 'title': '促销参数', 'default': ''}}},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'participant_policy': 'notify_only'}],
    permissions=["send_message", "edit_message", "external_http"],
    allowed_hosts=["www.qingwapt.com"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "x-usage-guide": '发送 {prefix}{command} 种子ID [促销参数] 触发青娃 PT 置顶促销；先在全局配置填站点 Cookie，再按示例设置 free/2x、时长、竞价和奖励参数。',
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "title": "触发指令名",
                "description": "不含系统命令前缀。例：pt、促销",
                "default": "pt",
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
                    "置顶促销（消耗蝌蚪）：\n"
                    "{prefix}pt <种子ID>\n"
                    "{prefix}pt 12345 free 7d\n"
                    "{prefix}pt 12345 2x 3d bid=200\n"
                    "{prefix}pt 12345 free 7d bid=100 reward=50 users=10\n\n"
                    "查询促销历史：\n"
                    "{prefix}ptinfo <种子ID>\n\n"
                    "参数说明：\n"
                    "  free / 2x — 促销类型（默认 free）\n"
                    "  1d / 2d / 3d / 7d — 时长（默认 1天）\n"
                    "  bid=N — 竞价蝌蚪，越高排名越靠前\n"
                    "  reward=N — 奖励蝌蚪，吸引下载者\n"
                    "  users=N — 奖励人数"
                ),
            },
            "site_url": {
                "type": "string",
                "title": "PT 站点地址",
                "description": "站点根 URL，默认 https://www.qingwapt.com",
                "default": "https://www.qingwapt.com",
                "level": "global",
            },
            "cookie": {
                "type": "string",
                "title": "Cookie",
                "description": "登录后浏览器复制的完整 Cookie 字符串",
                "default": "",
                "level": "global",
            },
            "torrent_cooldown_seconds": {
                "type": "string",
                "title": "同一种子促销冷却",
                "description": "成功置顶后，同一种子再次触发前需要等待的时间。支持 2s、2m、2h、2d，默认 12h。",
                "default": "12h",
                "level": "account",
            },
            "template_placeholders": {
                "type": "string",
                "title": "消息模板占位符（只读）",
                "readOnly": True,
                "default": (
                    "通用：{prefix} 全局指令前缀，{command} 指令名，{torrent_id} 种子 ID，{details_url} 种子详情页，{error} 错误信息。\n"
                    "促销：{params} 促销参数明细，{cost} 消耗蝌蚪，{expression} 计算方式，{title} 标题，{subtitle} 副标题。\n"
                    "HTML：{torrent_header} 带链接的种子标题，{promotion_details} 可展开促销明细。成功模板会以 HTML 模式发送。\n"
                    "状态模板：{icon} 状态图标，{message} 状态正文。"
                ),
            },
            "promote_usage_template": {
                "type": "string",
                "title": "促销用法模板",
                "description": "支持占位符：{prefix}、{command}",
                "default": PROMOTE_USAGE_TEMPLATE_DEFAULT,
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 1500,
                "level": "account",
            },
            "promote_status_template": {
                "type": "string",
                "title": "通用状态消息模板",
                "description": "用于错误、等待、冷却、重复触发等短消息。支持占位符：{icon}、{message} 以及当前场景变量。",
                "default": PROMOTE_STATUS_TEMPLATE_DEFAULT,
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 800,
                "level": "account",
            },
            "promote_ready_template": {
                "type": "string",
                "title": "符合条件/计算消耗模板",
                "description": "支持占位符：{torrent_id}、{params}",
                "default": PROMOTE_READY_TEMPLATE_DEFAULT,
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 1500,
                "level": "account",
            },
            "promote_confirming_template": {
                "type": "string",
                "title": "确认置顶中模板",
                "description": "支持占位符：{torrent_id}、{params}、{cost}、{expression}",
                "default": PROMOTE_CONFIRMING_TEMPLATE_DEFAULT,
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 1800,
                "level": "account",
            },
            "promote_success_template": {
                "type": "string",
                "title": "置顶成功模板",
                "description": "支持占位符：{torrent_id}、{details_url}、{torrent_header}、{promotion_details}、{title}、{subtitle}、{params}、{cost}、{expression}。按 HTML 模式发送。",
                "default": PROMOTE_SUCCESS_TEMPLATE_DEFAULT,
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 3000,
                "level": "account",
            },
            "info_usage_template": {
                "type": "string",
                "title": "查询用法模板",
                "description": "支持占位符：{prefix}、{command}",
                "default": "用法：{prefix}ptinfo <种子ID>",
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 500,
                "level": "account",
            },
            "info_ok_template": {
                "type": "string",
                "title": "查询符合条件模板",
                "description": "支持占位符：{torrent_id}、{details_url}",
                "default": INFO_OK_TEMPLATE_DEFAULT,
                "x-ui-widget": "textarea",
                "minLength": 1,
                "maxLength": 1000,
                "level": "account",
            },
            "promote_success_preview": {
                "type": "string",
                "title": "置顶成功模板预览（只读）",
                "description": "使用固定示例值渲染，仅用于配置预览。",
                "readOnly": True,
                "default": (
                    "✅ 种子置顶促销成功！\n\n"
                    "Qi Miao Meng Ke Zhi Mo Fa Tian Xin S01 2024 1080p WEB-DL H.265 AAC-ZmWeb\n\n"
                    "[副标题与促销明细]\n"
                    "奇妙萌可之魔法甜心 | 全26集 | 导演：何佩祺 | 声优：王愫稣 | 蒋丽 | 萧清源\n"
                    "促销类型：Free\n"
                    "促销时长：1 天\n"
                    "消耗：8,000 蝌蚪"
                ),
            },
        },
        "required": ["command", "cookie", "torrent_cooldown_seconds"],
    },
)

# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
