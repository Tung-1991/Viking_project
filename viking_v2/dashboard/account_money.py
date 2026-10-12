"""Read-only account-card valuation. Never supplies capital to trading rules."""
from __future__ import annotations

from math import isfinite
from typing import Any

from ..trading.portfolio import account_price, position_quantity


def _value(body: dict, *keys: str) -> float | None:
    for key in keys:
        raw = body.get(key)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            value = float(raw)
            if isfinite(value):
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def account_money(balance: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any]:
    stock = balance.get("stock") if isinstance(balance.get("stock"), dict) else balance
    available = _value(stock, "availableCash", "cashAvailable", "available_cash", "cash")
    total = _value(stock, "totalCash", "total_cash")
    debt = _value(stock, "totalDebt", "total_debt")
    result = dict(value=available, kind="CASH", available=available, total_cash=total,
                  debt=debt, stock_value=None)
    for body in (balance, stock):
        nav = _value(body, "equity", "nav", "netAssetValue", "totalAsset")
        if nav is not None:
            return {**result, "value": nav, "kind": "NAV"}
    if total is None or debt is None or total < 0 or debt < 0:
        return result
    market_value = 0.0
    for row in positions:
        observed_quantity = _value(row, "openQuantity", "quantity", "volume")
        if observed_quantity is None or observed_quantity < 0 or not observed_quantity.is_integer():
            return result
        quantity = position_quantity(row)
        if not quantity:
            continue
        price = _value(row, "marketPrice", "currentPrice")
        if price is None or price <= 0:
            # Entry price is not a current market valuation.
            return result
        market_value += account_price(row, price) * quantity * 1000
    return {**result, "value": total + market_value - debt, "kind": "ESTIMATED_NAV",
            "stock_value": market_value}


def account_money_hint(summary: dict[str, Any]) -> str:
    def money(key: str) -> str:
        value = summary.get(key)
        return f"{value:,.0f} đ" if value is not None else "Chưa có dữ liệu"
    lines = [
        f"Tiền khả dụng: {money('available')}",
        f"Tổng tiền (totalCash): {money('total_cash')}",
        f"Tổng nợ (totalDebt): {money('debt')}",
    ]
    if summary.get("kind") == "NAV":
        lines.append(f"NAV broker báo: {money('value')}")
    elif summary.get("kind") == "ESTIMATED_NAV":
        lines.extend((f"Giá trị cổ đang giữ: {money('stock_value')}",
                      "NAV ước tính = tổng tiền + giá trị cổ − tổng nợ.",
                      "API chưa trả NAV trực tiếp; số tính từ snapshot có thể khác NAV trên DNSE."))
    else:
        lines.append("Chưa có đủ dữ liệu tính NAV; số lớn hiện tiền khả dụng.")
    lines.append("Không tự coi chênh lệch NAV là phí giao dịch.")
    return "\n".join(lines)
