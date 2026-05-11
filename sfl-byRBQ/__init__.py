from .manifest import MANIFEST
from .plugin import SflByRBQPlugin

PLUGIN_CLASS = SflByRBQPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
