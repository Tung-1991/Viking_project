from __future__ import annotations

import math
import time
from typing import Any

COL_GREEN = "#22C55E"
COL_RED = "#EF4444"
COL_GRAY = "#3A3F47"
COL_WARN = "#F59E0B"
COL_SETTLEMENT_BG = "#39254F"
COL_SETTLEMENT_TEXT = "#E9D5FF"
COL_TEXT = "#F2F4F7"
COL_MUTED = "#C5CBD4"
COL_SURFACE = "#181B20"
COL_SURFACE_2 = "#22262D"
COL_BORDER = "#30353D"
COL_PREVIEW_TEXT = "#D6DAE1"
# Neutral titles/keys stay subordinate to actionable values and status colours.
# It remains below COL_TEXT while keeping keys easy to distinguish from the panel.
COL_TITLE = "#BABEC5"
FONT_BOLD = ("Segoe UI", 14)
FONT_PREVIEW_TITLE = ("Segoe UI", 14, "bold", "italic")
FONT_PREVIEW_VALUE = ("Cascadia Mono", 14)


def _order_form_key(subject: Any) -> tuple[str, ...]:
    """Scope a short-lived receipt/error to the form that produced it."""
    return tuple(
        str(getattr(subject, name).get()) if hasattr(subject, name) else ""
        for name in ("mode", "symbol", "order_type", "quantity", "price", "sl", "tp")
    )


def _active_order_notice(subject: Any) -> dict[str, Any]:
    notice = getattr(subject, "_manual_order_notice", None)
    if not isinstance(notice, dict):
        return {}
    if notice.get("form") != _order_form_key(subject) or time.monotonic() >= notice.get("until", 0):
        subject._manual_order_notice = {}
        return {}
    return notice


def _render_order_notice(subject: Any) -> None:
    """Reuse the existing PREVIEW status row; never grow the panel or trade."""
    notice = _active_order_notice(subject)
    if not notice:
        return
    badge, background, foreground = (
        ("CHỜ", "#4A3B16", "#FFF3B0") if notice.get("level") == "WARNING"
        else ("CHẶN", "#5A1E1E", "#FFCDD2")
    )
    label = getattr(subject, "preview_status_reason", None)
    if label is not None:
        label.configure(text=notice["summary"], text_color=foreground)
    label = getattr(subject, "preview_status_badge", None)
    if label is not None:
        label.configure(text=badge, fg_color=background, text_color=foreground)



def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value or default)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
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
