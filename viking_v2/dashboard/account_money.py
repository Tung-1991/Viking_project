"""Read-only account-card valuation. Never supplies capital to trading rules."""
from __future__ import annotations

from math import isfinite
from datetime import datetime
import time
from typing import Any

from ..trading.portfolio import account_price, board_price, position_quantity
from ..trading.market import VN_TZ
from ..trading.validation import quote_is_fresh, timestamp_value


def displayed_account_snapshot(view: Any, mode: str) -> tuple[dict, list, list]:
    """Display-only partial reads must never replace a trading snapshot."""
    return getattr(view, "_account_display_snapshots", {}).get(
        mode, view.snapshots.get(mode, ({}, [], [])),
    )


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


def _valuation_quote(quote: Any, symbol: str, now: float) -> float | None:
    if not isinstance(quote, dict) or str(quote.get("symbol", "")).upper() != symbol:
        return None
    if quote.get("stale") or str(quote.get("health", "OK")).upper() not in {"", "OK", "HEALTHY"}:
        return None
    observed = timestamp_value(quote.get("received_at", quote.get("timestamp")))
    if not isfinite(observed) or observed <= 0 or observed > now + 2:
        return None
    # A frozen lunch/closing price can value holdings, but never authorizes an order.
    same_day = datetime.fromtimestamp(observed, VN_TZ).date() == datetime.fromtimestamp(now, VN_TZ).date()
    if not (quote_is_fresh(quote, symbol, now=now) or (quote.get("frozen") and same_day)):
        return None
    price = board_price(quote.get("price"))
    return price if price > 0 else None


def account_money(balance: dict[str, Any], positions: list[dict[str, Any]],
                  quotes: dict[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
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
    quote_symbols: set[str] = set()
    checked = time.time() if now is None else now
    for row in positions:
        observed_quantity = _value(row, "openQuantity", "quantity", "volume")
        if observed_quantity is None or observed_quantity < 0 or not observed_quantity.is_integer():
            return result
        quantity = position_quantity(row)
        if not quantity:
            continue
        symbol = str(row.get("symbol", "")).upper()
        price = _valuation_quote((quotes or {}).get(symbol), symbol, checked)
        if price is not None:
            quote_symbols.add(symbol)
        else:
            raw_price = _value(row, "marketPrice", "currentPrice")
            price = account_price(row, raw_price) if raw_price is not None else None
        if price is None or price <= 0:
            # Entry price is not a current market valuation.
            return result
        market_value += price * quantity * 1000
    return {**result, "value": total + market_value - debt, "kind": "ESTIMATED_NAV",
            "stock_value": market_value, "quote_symbols": sorted(quote_symbols)}


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
        if summary.get("quote_symbols"):
            lines.append("Giá thị trường từ bảng giá: " + ", ".join(summary["quote_symbols"]) + ".")
    else:
        lines.append("Chưa có đủ dữ liệu tính NAV; số lớn hiện tiền khả dụng.")
    lines.append("Không tự coi chênh lệch NAV là phí giao dịch.")
    return "\n".join(lines)
