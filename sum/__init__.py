"""群消息总结远程模块。"""

from .manifest import MANIFEST
from .plugin import SummaryPlugin

PLUGIN_CLASS = SummaryPlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS"]
