from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "x-usage-guide": '管理员发送 {prefix}{command} 口令 总额 个数 创建文字或图片口令红包；群友直接发送正确口令领取，插件自动回复 +金额 并在领完后发送结算榜单。',
    "additionalProperties": False,
    "properties": {
        "usage_preview": {
            "type": "string",
            "title": "玩法说明",
            "readOnly": True,
            "default": (
                "当前页面上方的“功能总开关”控制插件是否在当前账号运行。\n"
                "触发指令名只填写命令本体，不要带系统前缀；示例会自动使用当前系统前缀。\n\n"
                "基础用法：\n"
                "{prefix}{command} help                         查看完整帮助\n"
                "{prefix}{command} 我超有挂 88888 10             发文字口令红包：口令/总额/个数\n"
                "{prefix}{command} send \"我 超 有 挂\" 52000 5    口令包含空格时用引号包起来\n\n"
                "图片数学题红包：\n"
                "{prefix}{command} img 8888 10                   自动生成图片口令和数学题\n"
                "{prefix}{command} img 图片口令 8888 10           指定图片口令、总额和个数\n\n"
                "管理命令：\n"
                "{prefix}{command} status                        查看默认金额、默认个数和统计\n"
                "{prefix}{command} active                        查看当前聊天进行中的红包\n"
                "{prefix}{command} clear                         清空当前聊天红包\n"
                "{prefix}{command} amount 88888                  设置默认金额\n"
                "{prefix}{command} count 10                      设置默认个数\n"
                "{prefix}{command} name 红包昵称                  设置展示名称\n"
                "{prefix}{command} name auto                     切回自动展示名称\n"
                "{prefix}{command} reset                         恢复默认金额、个数和展示名称\n\n"
                "安全规则：只有当前账号本人发出的指令会创建红包；群成员只能发送口令领取。\n"
                "领取方式：别人直接发送正确口令即可领取；插件会回复 +金额，领完后自动发送结算榜单。\n"
                "配置页可调整默认金额、默认个数、最低单包、展示名、发出后删原指令、领取提示删除、高额确认和子命令别名。"
            ),
            "description": "只读预览；展示内容会跟随当前系统前缀与触发指令名实时渲染。",
        },
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "redpack",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "例如填 rp 后，实际触发格式为“系统前缀 + rp”；此处只填写命令词本体，不要填写前缀符号。"
        },
        "default_amount": {
            "type": "integer",
            "title": "默认总额",
            "default": 88888,
            "minimum": 100,
            "maximum": 999999999,
            "description": "发红包指令未填写金额时使用。"
        },
        "default_count": {
            "type": "integer",
            "title": "默认个数",
            "default": 10,
            "minimum": 1,
            "maximum": 500,
            "description": "发红包指令未填写个数时使用。"
        },
        "min_share_amount": {
            "type": "integer",
            "title": "最低单包金额",
            "default": 100,
            "minimum": 1,
            "maximum": 999999999,
            "description": "每个红包至少保留的金额；用于校验总额与拼手气拆分。"
        },
        "custom_name": {
            "type": "string",
            "title": "红包展示名",
            "default": "",
            "maxLength": 64,
            "description": "留空时自动使用当前账号名称。"
        },
        "claim_reply_delete_delay": {
            "type": "integer",
            "title": "领取提示删除秒数",
            "default": 8,
            "minimum": 0,
            "maximum": 86400,
            "description": "领取后回复 +金额 的保留时长；填 0 表示不自动删除。"
        },
        "delete_command_message": {
            "type": "boolean",
            "title": "发出后删除原指令",
            "default": True,
            "description": "文字红包与图片红包发送成功后，是否删除原始触发指令。"
        },
        "auto_confirm_enabled": {
            "type": "boolean",
            "title": "自动确认高额转账",
            "default": True,
            "description": "检测到高额转账确认按钮时自动点击确认。"
        },
        "auto_confirm_ttl": {
            "type": "integer",
            "title": "确认消息有效秒数",
            "default": 180,
            "minimum": 1,
            "maximum": 86400,
            "description": "只在领取提示发出后的这段时间内尝试自动确认。"
        },
        "auto_confirm_click_delay": {
            "type": "number",
            "title": "确认点击延迟秒数",
            "default": 0.8,
            "minimum": 0,
            "maximum": 60,
            "description": "点击确认按钮前等待的秒数。"
        },
        "help_aliases": {"type": "string", "title": "help 别名", "default": "help 帮助"},
        "send_aliases": {"type": "string", "title": "send 别名", "default": "send"},
        "img_aliases": {"type": "string", "title": "img 别名", "default": "img image 图片"},
        "status_aliases": {"type": "string", "title": "status 别名", "default": "status 状态"},
        "active_aliases": {"type": "string", "title": "active 别名", "default": "active list 列表"},
        "clear_aliases": {"type": "string", "title": "clear 别名", "default": "clear 清空"},
        "amount_aliases": {"type": "string", "title": "amount 别名", "default": "amount 金额"},
        "count_aliases": {"type": "string", "title": "count 别名", "default": "count 个数 数量"},
        "name_aliases": {"type": "string", "title": "name 别名", "default": "name 昵称 名称"},
        "reset_aliases": {"type": "string", "title": "reset 别名", "default": "reset 重置"}
    },
    "required": ["command"]
}

# TelePilot 0.41 Event Bus metadata.
USAGE = ('管理员发送 {prefix}{command} 口令 总额 个数 创建文字或图片口令红包；群友直接发送正确口令领取，插件自动回复 +金额 并在领完后发送结算榜单。事件订阅：管理员命令走 '
 'userbot；群内关键词、按钮和会话消息走 interaction_bot；付款确认来自 external_payment_notice/userbot。输出只使用 '
 'interaction_bot 或 userbot_reply 受控通道。')
EVENT_SUBSCRIPTIONS = [{'events': ['command'],
  'source': ['userbot'],
  'scope': 'owner_only',
  'description': '账号主人或授权管理员通过 UserBot 命令触发。'},
 {'events': ['message', 'session_close'],
  'source': ['interaction_bot'],
  'scope': 'rule_bound',
  'description': '交互规则命中后由交互 Bot 投递会话事件。'},
 {'events': ['payment_confirmed'],
  'source': ['external_payment_notice', 'userbot'],
  'scope': 'rule_bound',
  'description': '付款确认由外部到账证据和 UserBot 上下文共同确认。'}]
CAPABILITIES = {}

MANIFEST = Manifest(
    key="redpack-byRBQ",
    display_name="红包",
    version="1.1.23",
    min_telepilot_version="0.33.0",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="口令红包插件，支持文字红包与图片数学题红包，并提供自动领取结算和高额转账确认",
    permissions=["send_message", "edit_message", "read_chat", "send_file", "delete_message"],

    category="interactive",
    interaction_profile="reward_pool",
    interaction_entries=[{'key': 'start_redpack',
  'title': '发起口令红包',
  'description': '由交互 Bot 在群内发起一轮口令红包。',
  'interaction_profile': 'reward_pool',
  'launch_mode': 'hybrid',
  'session_scope': 'chat',
  'events': ['payment_confirmed', 'keyword', 'message', 'session_close'],
  'preserve_command_trigger': True,
  'command_fallback': {'enabled': True, 'command': 'redpack', 'mode': 'hint_only'},
  'session_policy': {'ttl_seconds': 3600,
                     'duplicate_start': 'reject',
                     'close_on': ['completed', 'timeout', 'session_close']},
  'payload_contract': {'required_envelope': ['source', 'actor', 'trigger', 'session'],
                       'required_event_fields': ['type', 'chat_id']},
  'result_contract': {'actions': ['send_message',
                                  'send_file',
                                  'end_session',
                                  'result',
                                  'settlement'],
                      'send_via': ['interaction_bot', 'userbot_reply']},
  'input_schema': {'type': 'object',
                   'additionalProperties': False,
                   'properties': {'total_amount': {'type': 'integer',
                                                   'title': '总额',
                                                   'default': 88888,
                                                   'minimum': 100},
                                  'count': {'type': 'integer',
                                            'title': '个数',
                                            'default': 10,
                                            'minimum': 1,
                                            'maximum': 500}}},
  'settlement': {'mode': 'announce_only',
                 'winner_field': 'actor.user_id',
                 'amount_field': 'total_amount'},
  'dispatch_modes': ['admin_command', 'public_keyword'],
  'message_channels': {'admin_command': 'userbot_reply', 'public_keyword': 'interaction_bot'},
  'money_channel': 'userbot_reply',
  'participant_policy': 'open_race'}],
    config_schema=CONFIG_SCHEMA,
)

# Expose 0.41 metadata without requiring older Manifest dataclasses to accept new kwargs.
MANIFEST.usage = USAGE
MANIFEST.event_subscriptions = EVENT_SUBSCRIPTIONS
MANIFEST.capabilities = CAPABILITIES

__all__ = ["MANIFEST"]
