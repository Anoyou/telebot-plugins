"""PT 站点种子置顶促销模块。"""

from .manifest import MANIFEST
from .plugin import PTPromotePlugin

PLUGIN_CLASS = PTPromotePlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS"]
