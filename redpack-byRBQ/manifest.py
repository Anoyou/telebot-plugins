from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "x-ui-mode": "single",
    "additionalProperties": False,
    "properties": {
        "usage_preview": {
            "type": "string",
            "title": "玩法说明",
            "readOnly": True,
            "default": (
                "当前页面上方的“功能总开关”控制模块是否在当前账号运行。\n"
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
                "领取方式：别人直接发送正确口令即可领取；模块会回复 +金额，领完后自动发送结算榜单。"
            ),
            "description": "只读预览，会按当前系统前缀和触发指令名实时渲染。",
        },
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "redpack",
            "minLength": 1,
            "maxLength": 32,
            "pattern": "^\\S+$",
            "description": "例如填 rp 后，实际命令为“系统前缀 + rp”；不要在这里填写逗号或斜杠。"
        }
    },
    "required": ["command"]
}

MANIFEST = Manifest(
    key="redpack-byRBQ",
    display_name="红包",
    version="1.1.8",
    min_telepilot_version="0.15.0",
    author="RBQ (migrated from zhiluop/pagermaid_plugins)",
    description="口令红包模块，支持文字红包、img 数学题图片红包、自动领取结算和高额转账确认",
    permissions=["send_message", "edit_message", "read_chat", "send_file", "delete_message"],
    config_schema=CONFIG_SCHEMA,
)

__all__ = ["MANIFEST"]
