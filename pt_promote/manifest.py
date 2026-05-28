"""青娃PT (qingwapt.com) 置顶促销模块 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="pt_promote",
    display_name="PT 种子促销",
    version="1.0.5",
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
        },
        "required": ["command", "cookie"],
    },
)

__all__ = ["MANIFEST"]
