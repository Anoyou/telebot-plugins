"""中文转账通知解析（payment_sources）插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

PLUGIN_KEY = "payment-source-cn-transfer"
PLUGIN_VERSION = "1.0.0"

USAGE = (
    "TelePilot 0.111.0 起的「转账通知解析」扩展点（payment_sources）官方实现：识别交互 Bot 收到的中文转账 / "
    "扣减通知，例如“付款人：A / 收款人：B / 金额：100”或“A 转出 100 / B 收到 100”（也认 射出、转账、扣减 / "
    "接收、收款）。解析规则是 TelePilot 宿主内置解析器的逐字迁移，结果完全一致。"
    "安装并启用后无需配置：宿主只在交互 Bot「可信转账 Bot」白名单通过后才调用本插件，本插件只返回付款人、"
    "收款人、金额，结算（规则匹配、幂等、补偿）仍由 TelePilot 执行。双跑对照期内默认仍以宿主旧解析为准，"
    "本插件结果只做对照，不一致会记日志、Trace 并显示在运行统计里；系统设置打开「转账通知以插件结果为准」"
    "后才改用本插件结果。旧版本 TelePilot 不认识这个扩展点，安装后不会调用，也没有副作用。"
)

PAYMENT_SOURCES = [
    {
        "id": "cn_transfer",
        "title": "中文转账通知",
        "description": "付款人/收款人/金额 标签格式，以及 转出|射出|转账|扣减 + 收到|接收|收款 简洁格式",
    }
]

MANIFEST = Manifest(
    key=PLUGIN_KEY,
    display_name="转账通知解析（中文）",
    version=PLUGIN_VERSION,
    min_telepilot_version="0.72.0-beta.2",
    author="Anoyou",
    description="交互 Bot 转账通知解析：识别中文转账 / 扣减通知的付款人、收款人与金额（payment_sources 扩展点）。",
    usage=USAGE,
    category="utility",
    permissions=[],
    requires_platform_capabilities=["interaction_bot"],
    capabilities={},
)

__all__ = ["MANIFEST", "PAYMENT_SOURCES", "PLUGIN_KEY", "PLUGIN_VERSION", "USAGE"]
