from .manifest import MANIFEST
from .plugin import AIRedpacketPlugin


PLUGIN_CLASS = AIRedpacketPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
