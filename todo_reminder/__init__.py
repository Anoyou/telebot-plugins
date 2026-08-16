"""TelePilot Todo 自然语言提醒插件。"""

from .manifest import MANIFEST
from .plugin import TodoReminderPlugin

PLUGIN_CLASS = TodoReminderPlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS", "TodoReminderPlugin"]
