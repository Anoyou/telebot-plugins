from .manifest import MANIFEST
from .plugin import SarByRBQPlugin

PLUGIN_CLASS = SarByRBQPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
