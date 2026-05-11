from .manifest import MANIFEST
from .plugin import GuessNumberPlugin


PLUGIN_CLASS = GuessNumberPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
