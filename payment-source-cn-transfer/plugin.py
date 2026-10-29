"""中文转账通知解析插件：只导出 ``parse_payment_notice``，没有任何事件订阅或命令。

TelePilot 0.111.0+ 读取 ``plugin.json`` 的 ``payment_sources`` 声明后，在主进程里调用本模块的
``parse_payment_notice(text, meta)``；旧版本宿主不认识该声明，本插件只是一个不做任何事的空插件。
"""

from __future__ import annotations

from app.worker.plugins.base import Plugin, register

from .manifest import MANIFEST, PLUGIN_KEY
from .parser import parse_payment_notice, parse_transfer_notice


@register
class PaymentSourceCnTransferPlugin(Plugin):
    key = PLUGIN_KEY
    manifest = MANIFEST


__all__ = ["PaymentSourceCnTransferPlugin", "parse_payment_notice", "parse_transfer_notice"]
