"""Read-only position context for technical alerts; never infer a fill from a signal."""
from math import isfinite

from ..trading.orders import FINAL_STATUSES
from ..trading.portfolio import available_to_sell, position_quantity


def position_notice_context(symbol, mode, *, cycles=(), positions=None, intents=()):
    symbol, mode = str(symbol).upper(), str(mode).upper()
    trades = [row for row in cycles if row.symbol == symbol and row.execution_mode == mode
              and row.entry_quantity > 0]
    active = [row for row in trades if row.open_quantity > 0]
    latest = max(trades, key=lambda row: row.opened_at, default=None)
    rows = [row for row in positions or [] if str(row.get("symbol", "")).upper() == symbol]
    if active:
        packages = {row.loan_package_id for row in active if row.loan_package_id}
        if packages:
            rows = [row for row in rows if str(row.get("loanPackageId", "")) in packages]
        held = sum(row.open_quantity for row in active)
        bought, sold = sum(row.entry_quantity for row in active), sum(row.sold_quantity for row in active)
    elif latest:
        held, bought, sold = latest.open_quantity, latest.entry_quantity, latest.sold_quantity
    elif positions is not None:
        held, bought, sold = sum(position_quantity(row) for row in rows), 0, None
    else:
        return None
    pending = [row for row in intents if row.symbol == symbol and row.execution_mode == mode
               and row.side == "SELL" and row.status not in FINAL_STATUSES]
    sellable = None
    if positions is not None and rows:
        try:
            if all(isfinite(float(row["tradeQuantity"])) and float(row["tradeQuantity"]) >= 0 for row in rows):
                sellable = min(held, available_to_sell(rows, symbol))
        except (KeyError, TypeError, ValueError, OverflowError):
            pass  # Missing broker availability is not evidence of T+ stock.
    return {"held_quantity": held, "bought_quantity": bought, "sold_quantity": sold,
            "sellable_quantity": sellable,
            "pending_sell_quantity": sum(row.remaining_quantity for row in pending),
            "pending_sell_status": sorted({row.status for row in pending}),
            "pending_sell_pct": sorted({float(row.details["sell_share_pct"]) for row in pending
                                        if row.details.get("sell_share_pct") is not None})}


def position_notice_lines(context):
    if not context:
        return []
    held, bought, sold = context["held_quantity"], context["bought_quantity"], context["sold_quantity"]
    lines = [f"Vị thế app: đang giữ {held:,} CP"]
    if sold is not None:
        lines[0] += f" · Đã bán khớp {sold:,} CP"
        if bought > 0 and sold > 0:
            lines[0] += f" ({sold / bought * 100:.2f}% của {bought:,} CP đã mua)"
    sellable = context.get("sellable_quantity")
    if sellable is not None and held > 0:
        lines.append(f"Bán được {sellable:,} CP · Chờ cổ về {max(0, held - sellable):,} CP")
    pending = context.get("pending_sell_quantity", 0)
    if pending:
        statuses = {"WAITING_SETTLEMENT": "CHỜ CỔ VỀ", "WAITING_TOKEN": "CHỜ OTP", "PENDING": "CHỜ GỬI",
                    "WORKING": "CHỜ KHỚP", "PARTIAL": "KHỚP MỘT PHẦN", "UNKNOWN": "CHỜ ĐỐI SOÁT",
                    "CANCEL_PENDING": "CHỜ HUỶ", "SENDING": "ĐANG GỬI"}
        status = "/".join(statuses.get(value, value) for value in context.get("pending_sell_status", []))
        pct = "/".join(f"{value:g}%" for value in context.get("pending_sell_pct", []))
        lines.append(f"SELL còn chờ {pending:,} CP · {status}" + (f" · Tỷ lệ yêu cầu {pct}" if pct else ""))
        lines.append("Phần SELL đang chờ chưa được tính là đã bán.")
    return lines
