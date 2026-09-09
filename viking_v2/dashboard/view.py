from __future__ import annotations

from typing import Any

COL_GREEN = "#22C55E"
COL_RED = "#EF4444"
COL_GRAY = "#3A3F47"
COL_WARN = "#F59E0B"
COL_TEXT = "#F2F4F7"
COL_MUTED = "#C5CBD4"
COL_SURFACE = "#181B20"
COL_SURFACE_2 = "#22262D"
COL_BORDER = "#30353D"
COL_PREVIEW_TEXT = "#D6DAE1"
FONT_BOLD = ("Segoe UI", 14, "bold")
FONT_PREVIEW_TITLE = ("Segoe UI", 13, "bold")
FONT_PREVIEW_VALUE = ("Cascadia Mono", 13, "bold")



def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default

def _cash(balance: dict[str, Any]) -> float:
    stock = balance.get("stock") if isinstance(balance.get("stock"), dict) else balance
    for key in ("availableCash", "cashAvailable", "available_cash", "cash"):
        if key in stock:
            return _number(stock[key])
    return 0.0

def _equity(balance: dict[str, Any]) -> float:
    for key in ("equity", "netAssetValue", "totalAsset"):
        if key in balance:
            return _number(balance[key])
    return _cash(balance)

def _display_price(value: Any) -> str:
    price = _number(value)
    if price <= 0:
        return "---"
    vnd = price * 1000.0 if price < 1000 else price
    return f"{vnd:,.0f}"

def _price_unit(value: Any) -> float:
    """Return the DNSE stock price unit used by OrderIntent (thousand VND)."""
    price = _number(value)
    return price / 1000.0 if price >= 1000 else price

def _compact_vnd(value: Any) -> str:
    amount = _number(value)
    absolute = abs(amount)
    sign = "-" if amount < 0 else ""
    if absolute >= 1_000_000_000:
        return f"{sign}{absolute / 1_000_000_000:.2f} tỷ"
    if absolute >= 1_000_000:
        return f"{sign}{absolute / 1_000_000:.2f} tr"
    if absolute >= 1_000:
        return f"{sign}{absolute / 1_000:.1f}K"
    return f"{amount:,.0f}"
