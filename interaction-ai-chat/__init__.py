"""AI 答疑（交互 Bot）插件包。"""

from .manifest import MANIFEST
from .plugin import InteractionAIChatPlugin

PLUGIN_CLASS = InteractionAIChatPlugin

__all__ = ["InteractionAIChatPlugin", "MANIFEST", "PLUGIN_CLASS"]
