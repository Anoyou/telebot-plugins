"""auto_reply 插件 manifest。

Config Schema 说明：
- level: "global" 的字段为全局配置，所有账号共享
- 无 level 或 level: "account" 的字段为账号级配置
- 配置合并顺序：schema defaults < global config < account config
"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

FEATURE_AUTO_REPLY = "auto_reply"

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_AUTO_REPLY,
    display_name="自动回复",
    version="1.1.0",
    min_telepilot_version="0.72.0-beta.2",
    author="TelePilot Official",
    description="按规则匹配关键词或正则后自动回复目标会话",
    usage="自动回复通过规则触发：每条规则配置匹配范围、关键词或正则、回复模板和冷却限制。安装后到插件中心选择账号，再进入自动回复配置页新增规则。规则保存后会随账号 worker 热更新生效。",
    category="automation",
    permissions=["send_message", "edit_message", "read_chat", "resolve_entity"],
    event_subscriptions=[
        {
            "source": ["userbot"],
            "events": ["message"],
            "scope": "rule_bound",
            "entry_key": "rules",
        }
    ],
    requires_platform_capabilities=[],
    capabilities={},
    # 规则驱动（x-ui-mode=rules）：每条规则的表单由 x-rule-schema 声明，宿主通用规则页渲染（0.110.0-beta.2+）。
    config_schema={
        "type": "object",
        "x-ui-mode": "rules",
        "x-usage-guide": "自动回复通过规则触发：每条规则配置作用范围、关键词 / 变量模式 / 正则、回复内容和冷却限制，按优先级从高到低匹配，第一条命中即回复。回复内容支持 {sender}、{chat}、{text}、{prefix}；正则规则还支持 {1}、{name}、{1|默认值}。规则保存后随账号 worker 热更新生效。",
        # 账号级配置：无（全部设置都在规则里）。旧宿主（<0.110.0-beta.2）不认识下面的 x-rule-* 键，
        # Web 仍用旧专属页、iOS 用通用 JSON 编辑，不影响运行。
        "properties": {},
        "x-rule-summary": ["match", "patterns", "scope"],
        "x-dry-run": {
            "sample_fields": ["message", "chat_type", "chat_id"],
            "title": "试运行",
            "description": "输入一条样例消息并选择会话类型，验证规则是否命中、回复渲染成什么样；不会真的发送。指定群规则不填会话 ID 时取规则里的第一个群。",
        },
        "x-rule-schema": {
            "type": "object",
            "required": ["patterns", "reply"],
            "properties": {
                "match": {
                    "type": "string",
                    "title": "匹配类型",
                    "enum": ["keyword", "template", "regex"],
                    "enumNames": ["关键词", "变量模式", "正则"],
                    "default": "keyword",
                    "description": "关键词：消息包含任一模式即命中；变量模式：用 {变量} 占位提取内容；正则：Python 正则，可用分组。",
                    "x-ui-columns": 2,
                },
                "scope": {
                    "type": "string",
                    "title": "作用范围",
                    "enum": ["private", "group_all", "group_specific"],
                    "enumNames": ["仅私聊", "所有群", "指定群"],
                    "default": "private",
                },
                "group_ids": {
                    "type": "array",
                    "x-ui-widget": "textarea",
                    "title": "指定群 ID",
                    "description": "每行一个群 chat_id（超级群通常是 -100 开头）。",
                    "items": {"type": "string"},
                    "x-ui-placeholder": "-1001234567890",
                    "x-ui-visible-if": {"field": "scope", "equals": "group_specific"},
                },
                "patterns": {
                    "type": "array",
                    "x-ui-widget": "textarea",
                    "title": "模式",
                    "description": "每行一条。关键词规则命中任一即可；正则规则每行一个表达式。",
                    "items": {"type": "string"},
                    "x-ui-placeholder": "你好",
                },
                "reply": {
                    "type": "string",
                    "title": "回复内容",
                    "description": "支持变量：{sender}、{chat}、{text}、{prefix}；正则规则还支持 {1}、{name}、{1|默认值}。",
                    "x-ui-widget": "textarea",
                    "x-ui-placeholder": "你好 {sender}",
                },
                "usage_label": {
                    "type": "string",
                    "title": "提示名称",
                    "description": "用于冷却和每日上限提示文案，例如：置顶、猜骰、推广。",
                    "default": "",
                    "x-ui-section": "冷却与上限",
                },
                "cooldown_seconds": {
                    "type": "string",
                    "title": "冷却时间",
                    "description": "0 表示不限制；支持秒数或 30s、5m、6h、2d。",
                    "default": "0",
                    "x-ui-placeholder": "0、30s、5m、6h、2d",
                    "x-ui-section": "冷却与上限",
                },
                "cooldown_scope": {
                    "type": "string",
                    "title": "冷却对象",
                    "enum": ["chat", "user"],
                    "enumNames": ["当前会话", "每个用户"],
                    "default": "chat",
                    "x-ui-section": "冷却与上限",
                },
                "daily_limit_per_user": {
                    "type": "integer",
                    "title": "每人每日上限",
                    "description": "0 表示不限制。",
                    "default": 0,
                    "minimum": 0,
                    "x-ui-section": "冷却与上限",
                },
                "cooldown_notice_enabled": {
                    "type": "boolean",
                    "title": "冷却中提示",
                    "default": True,
                    "x-ui-section": "冷却与上限",
                },
                "daily_limit_notice_enabled": {
                    "type": "boolean",
                    "title": "每日上限提示",
                    "default": True,
                    "x-ui-section": "冷却与上限",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "title": "区分大小写",
                    "default": False,
                    "x-ui-section": "高级",
                },
                "reply_to": {
                    "type": "boolean",
                    "title": "以「引用」形式回复",
                    "default": True,
                    "x-ui-section": "高级",
                },
                "whitelist": {
                    "type": "array",
                    "x-ui-widget": "textarea",
                    "title": "白名单会话 ID",
                    "description": "可选；填写后只在这些会话里回复，每行一个 chat_id。",
                    "items": {"type": "string"},
                    "x-ui-section": "高级",
                },
                "blacklist": {
                    "type": "array",
                    "x-ui-widget": "textarea",
                    "title": "黑名单会话 ID",
                    "description": "可选；这些会话里不回复，每行一个 chat_id。",
                    "items": {"type": "string"},
                    "x-ui-section": "高级",
                },
            },
        },
    },
)

__all__ = ["MANIFEST"]
