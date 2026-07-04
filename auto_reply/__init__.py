"""auto_reply 插件包入口。"""

from .manifest import MANIFEST
from .plugin import (
    AutoReplyPlugin,
    _dry_run_match,
    _match,
    _parse_duration_seconds,
    _render,
    _scope_ok,
)

# loader.discover_plugins 读取这两个常量，无须显式 @register
PLUGIN_CLASS = AutoReplyPlugin

__all__ = [
    "AutoReplyPlugin",
    "MANIFEST",
    "PLUGIN_CLASS",
    "_dry_run_match",
    "_match",
    "_parse_duration_seconds",
    "_render",
    "_scope_ok",
]
