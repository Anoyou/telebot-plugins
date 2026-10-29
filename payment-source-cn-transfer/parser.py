"""中文转账通知解析（TelePilot payment_sources 扩展点的官方实现）。

``parse_transfer_notice`` 是 TelePilot 宿主旧解析器（``backend/app/services/payment_notice_legacy.py``）
的**逐字迁移**，正则一个字符都没有改；golden 测试（``tests/fixtures/payment_notice_golden.json``）
保证两边对历史样本的结果 100% 一致。本文件只用标准库，不依赖宿主，可以单独测试。
"""

from __future__ import annotations

import re
from typing import Any

# 简洁格式里金额后面紧跟的单位（如“蝌蚪”“魔力值”），只作为附加信息返回，宿主不拿它参与对照或结算。
_UNIT_RE = re.compile(r"(?:转出|射出|转账|扣减)\s*\d+[ \t]*([^\s\d<>]{1,8})")


def parse_transfer_notice(text: str) -> dict[str, Any] | None:
    """解析官方或测试阶段自定义转账通知文案。"""

    if not text.strip():
        return None
    text = re.sub(r"<[^>]+>", "", text)
    labeled = {
        "payer_name": r"付款人\s*[:：]\s*(.+)",
        "receiver_name": r"收款人\s*[:：]\s*(.+)",
        "amount": r"金额\s*[:：]\s*(\d+)",
    }
    out: dict[str, Any] = {}
    for key, pattern in labeled.items():
        match = re.search(pattern, text)
        if not match:
            break
        value = match.group(1).strip()
        out[key] = int(value) if key == "amount" else value
    if set(out) == {"payer_name", "receiver_name", "amount"}:
        payer_id_match = re.search(r"付款人\s*(?:用户)?(?:ID|id)\s*[:：]\s*(\d+)", text)
        if payer_id_match:
            out["payer_user_id"] = int(payer_id_match.group(1))
        receiver_id_match = re.search(r"收款人\s*(?:ID|id)\s*[:：]\s*(\d+)", text)
        if receiver_id_match:
            out["receiver_user_id"] = int(receiver_id_match.group(1))
        return out

    payer_match = re.search(r"^\s*(.+?)\s*(?:转出|射出|转账|扣减)\s*(\d+)\b", text, re.M)
    receiver_match = re.search(r"^\s*(.+?)\s*(?:收到|接收|收款)\s*(\d+)\b", text, re.M)
    if payer_match and receiver_match:
        payer_amount = int(payer_match.group(2))
        receiver_amount = int(receiver_match.group(2))
        if payer_amount != receiver_amount:
            return None
        parsed = {
            "payer_name": payer_match.group(1).strip(),
            "receiver_name": receiver_match.group(1).strip(),
            "amount": payer_amount,
        }
        payer_id_match = re.search(r"付款人\s*(?:用户)?(?:ID|id)\s*[:：]\s*(\d+)", text)
        if payer_id_match:
            parsed["payer_user_id"] = int(payer_id_match.group(1))
        receiver_id_match = re.search(r"收款人\s*(?:ID|id)\s*[:：]\s*(\d+)", text)
        if receiver_id_match:
            parsed["receiver_user_id"] = int(receiver_id_match.group(1))
        return parsed
    return None


def _unit(text: str) -> str | None:
    match = _UNIT_RE.search(re.sub(r"<[^>]+>", "", text))
    return match.group(1) if match else None


def parse_payment_notice(text: str, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """payment_sources 协议入口：``(text, meta) -> {payer_name, receiver_name, amount, ...} | None``。

    ``meta`` 由宿主提供（account_id / chat_id / message_id / sender_id / source），这里不需要；
    宿主只会在可信 Bot ID 白名单通过后调用本函数，本函数只做解析、没有任何副作用。
    """

    del meta
    parsed = parse_transfer_notice(str(text or ""))
    if parsed is None:
        return None
    out = dict(parsed)
    unit = _unit(str(text or ""))
    if unit:
        out["unit"] = unit
    return out


__all__ = ["parse_payment_notice", "parse_transfer_notice"]
