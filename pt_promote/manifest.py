"""青娃PT (qingwapt.com) 置顶促销模块 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="pt_promote",
    display_name="PT 种子促销",
    version="1.0.0",
    author="xiaoyou",
    description="在青娃PT置顶促销某个种子（消耗蝌蚪）",
    category="utility",
    permissions=["send_message", "edit_message", "external_http"],
    allowed_hosts=["www.qingwapt.com"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
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
        },
        "required": ["command", "cookie"],
    },
)

__all__ = ["MANIFEST"]
