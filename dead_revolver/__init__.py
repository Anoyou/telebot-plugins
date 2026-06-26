"""死亡左轮插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import DeadRevolverPlugin

PLUGIN_CLASS = DeadRevolverPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
