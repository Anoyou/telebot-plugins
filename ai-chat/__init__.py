"""AI-Chat plugin package."""

from .manifest import MANIFEST
from .plugin import AIChatPlugin

PLUGIN_CLASS = AIChatPlugin

__all__ = ["AIChatPlugin", "MANIFEST", "PLUGIN_CLASS"]
