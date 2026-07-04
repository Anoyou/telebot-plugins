"""近期发言回复测试插件包入口。"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import ReplyAnchorTestPlugin

PLUGIN_CLASS = ReplyAnchorTestPlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS", "ReplyAnchorTestPlugin"]
