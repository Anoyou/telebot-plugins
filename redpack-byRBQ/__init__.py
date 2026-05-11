from .manifest import MANIFEST
from .plugin import RedpackByRBQPlugin

PLUGIN_CLASS = RedpackByRBQPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
