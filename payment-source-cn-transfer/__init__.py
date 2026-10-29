"""中文转账通知解析（payment_sources）插件包入口。"""

from __future__ import annotations

from .manifest import MANIFEST
from .parser import parse_payment_notice
from .plugin import PaymentSourceCnTransferPlugin

PLUGIN_CLASS = PaymentSourceCnTransferPlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS", "PaymentSourceCnTransferPlugin", "parse_payment_notice"]
