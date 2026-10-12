"""Display projections of the audit journal and DNSE history; never write fills."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from ..trading.market import VN_TZ
from .view import _compact_vnd, _display_price, _number, _price_unit


def history_datetime(value: Any, *, broker: bool = False) -> datetime:
    try:
        if float(value) > 0:
            return datetime.fromtimestamp(float(value), VN_TZ).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%d/%m/%Y %H:%M:%S")
        except ValueError:
            return datetime.min
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc if broker else VN_TZ)
    return parsed.astimezone(VN_TZ).replace(tzinfo=None)


def order_identity(order_id: Any, timestamp: datetime, symbol: Any, side: Any) -> tuple[str, ...]:
    """DNSE history prefixes daily IDs; scope plain IDs to day/symbol/side."""
    identity = str(order_id or "").strip()
    day = timestamp.strftime("%Y%m%d") if timestamp != datetime.min else ""
    match = re.fullmatch(r"(\d{8})_(.+)", identity)
    if match:
        day, identity = match.groups()
    direction = {"NB": "BUY", "NS": "SELL"}.get(str(side).upper(), str(side).upper())
    return day, identity, str(symbol or "").upper(), direction


def fee_known(row: dict[str, Any], field: str = "fee") -> bool:
    flag = row.get(f"{field}_known")
    if flag not in (None, ""):
        return str(flag).lower() in {"true", "1"}
    # Legacy journal zero cannot distinguish an omitted fee from a free order.
    return _number(row.get(field)) != 0 or row.get("_source") != "CSV" and row.get(field) not in (None, "")


def fill_delta(row: dict[str, Any]) -> int:
    if str(row.get("broker_status", "")).upper() == "COST_ADJUSTMENT":
        return 0
    value = row.get("fill_delta_quantity")
    return int(_number(value if value not in (None, "") else row.get("filled_quantity")))


def history_groups(journal: list[dict[str, Any]], broker_rows: list[dict[str, Any]],
                   cycles: list[Any], mode: str, *, day_limit: int = 7) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    local_links: dict[tuple[str, ...], set[str]] = {}
    seen_events: set[str] = set()
    for raw in journal:
        if str(raw.get("execution_mode", "PAPER")).upper() != mode:
            continue
        event_id = str(raw.get("event_id") or "")
        if event_id and event_id in seen_events:
            continue
        if event_id:
            seen_events.add(event_id)
        item = {**raw, "_source": "CSV", "_dt": history_datetime(raw.get("timestamp"))}
        item["side"] = {"NB": "BUY", "NS": "SELL"}.get(str(item.get("side", "")).upper(), str(item.get("side", "")).upper())
        item["_identity"] = order_identity(item.get("broker_order_id"), item["_dt"], item.get("symbol"), item["side"])
        item["_trade"] = str(item.get("trade_id") or item.get("cache_id") or "")
        if item.get("broker_order_id"):
            local_links.setdefault(item["_identity"], set()).add(item["_trade"])
        events.append(item)
    for raw in broker_rows:
        dt = history_datetime(raw.get("createdAt", raw.get("createdDate", "")), broker=mode == "REAL")
        identity = order_identity(raw.get("orderId", raw.get("id")), dt,
                                  raw.get("symbol", raw.get("instrumentId", "?")), raw.get("side", ""))
        linked = local_links.get(identity, set())
        trade = next(iter(linked)) if len(linked) == 1 else "broker:" + "|".join(identity)
        events.append(dict(
            timestamp=raw.get("createdAt", raw.get("createdDate", "")),
            broker_order_id=identity[1], trade_id=raw.get("tradeId", ""),
            symbol=identity[2], side=identity[3], order_type=raw.get("orderType", ""),
            limit_price=raw.get("averagePrice") or raw.get("price", 0),
            quantity=raw.get("quantity", raw.get("orderQuantity", 0)),
            filled_quantity=raw.get("fillQuantity", raw.get("filledQuantity", 0)),
            remaining_quantity=raw.get("leaveQuantity", 0), fee=raw.get("fee", raw.get("totalFee")),
            tax=raw.get("tax"), broker_status=raw.get("orderStatus", raw.get("status", "")),
            message=raw.get("message", raw.get("result", "")), _source="DNSE" if mode == "REAL" else "PAPER",
            _dt=dt, _identity=identity, _trade=trade, _raw=raw,
        ))
    cycles_by_id = {cycle.id: cycle for cycle in cycles if cycle.execution_mode == mode}
    trades: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(events):
        key = item["_trade"] or ("broker:" + "|".join(item["_identity"]) if item.get("broker_order_id") else f"event:{index}")
        trades.setdefault(key, []).append(item)
    days: dict[str, list[dict[str, Any]]] = {}
    for trade_id, items in trades.items():
        items.sort(key=lambda row: row["_dt"])
        cycle = cycles_by_id.get(trade_id)
        last_dt = history_datetime(cycle.closed_at) if cycle and cycle.closed_at else items[-1]["_dt"]
        day = last_dt.strftime("%Y-%m-%d") if last_dt != datetime.min else "0000-00-00"
        symbol = cycle.symbol if cycle else str(items[-1].get("symbol", "?"))
        order_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        cache_links: dict[str, set[tuple[str, ...]]] = {}
        for item in items:
            if item.get("cache_id") and item.get("broker_order_id"):
                cache_links.setdefault(str(item["cache_id"]), set()).add(item["_identity"])
        for item_index, item in enumerate(items):
            identity = item["_identity"]
            if not item.get("broker_order_id"):
                linked = cache_links.get(str(item.get("cache_id", "")), set())
                identity = next(iter(linked)) if len(linked) == 1 else (str(item.get("cache_id") or f"event:{item_index}"),)
            order_groups.setdefault(identity, []).append(item)
        orders = []
        for identity, snapshots in order_groups.items():
            local = [row for row in snapshots if row["_source"] == "CSV"]
            authoritative = local[-1] if local else snapshots[-1]
            # A cost correction updates amounts without erasing the fill status.
            statuses = [row for row in local if str(row.get("broker_status", "")).upper() != "COST_ADJUSTMENT"]
            latest = statuses[-1] if statuses else authoritative
            status = str(latest.get("broker_status") or latest.get("queue_status") or "").upper()
            broker_quantities = [int(_number(row.get("quantity"))) for row in snapshots if row["_source"] != "CSV" and _number(row.get("quantity")) > 0]
            submitted = [int(_number(row.get("broker_quantity"))) for row in local if _number(row.get("broker_quantity")) > 0]
            quantity = (broker_quantities or submitted or [max((int(_number(row.get("quantity"))) for row in snapshots), default=0)])[-1]
            recorded_fill = sum(fill_delta(row) for row in local)
            observed_fill = max((int(_number(row.get("filled_quantity"))) for row in snapshots if row["_source"] != "CSV"), default=0)
            filled = max(recorded_fill, observed_fill)
            if status in {"FILLED", "MATCHED"} and not filled:
                filled = quantity
            filled = min(quantity, filled)
            side = str(latest.get("side") or "")
            prices = [_price_unit(row.get("limit_price")) for row in reversed(snapshots) if _number(row.get("limit_price")) > 0]
            broker_prices = [_price_unit(row.get("limit_price")) for row in reversed(snapshots)
                             if row["_source"] != "CSV" and _number(row.get("filled_quantity")) > 0 and _number(row.get("limit_price")) > 0]
            priced_fills = [(fill_delta(row), _number(row.get("fill_price_vnd")))
                            for row in local if fill_delta(row) > 0 and _number(row.get("fill_price_vnd")) > 0]
            price = sum(qty * price for qty, price in priced_fills) / sum(qty for qty, _ in priced_fills) / 1000 if priced_fills else next(iter(broker_prices or prices), 0)
            if not price and cycle:
                price = cycle.avg_entry_price if side == "BUY" else cycle.avg_exit_price
            costs = sum(_number(row.get("fee")) + _number(row.get("tax")) for row in local) if local else _number(authoritative.get("fee")) + _number(authoritative.get("tax"))
            pending_fee = mode == "REAL" and filled > 0 and costs == 0 and not any(fee_known(row) for row in snapshots)
            first, last = snapshots[0]["_dt"], max(row["_dt"] for row in snapshots)
            clock = first.strftime("%H:%M:%S") if first != datetime.min else "—"
            if last > first:
                clock += " → " + last.strftime("%H:%M:%S")
            caption = {"FILLED": "KHỚP HẾT", "MATCHED": "KHỚP HẾT", "PARTIAL": "KHỚP MỘT PHẦN", "PENDING": "ĐÃ GỬI / CHỜ KHỚP", "CANCELLED": "ĐÃ HỦY", "REJECTED": "TỪ CHỐI"}.get(status, status)
            awaiting_reconcile = bool(local and observed_fill > recorded_fill)
            if awaiting_reconcile:
                caption = "KHỚP · CHỜ ĐỐI SOÁT"
            detail_rows = []
            for row in snapshots:
                detail_rows.append(dict(label=f"{row['_source']} · {row.get('broker_status') or row.get('queue_status') or 'LỆNH'}",
                    values=(row["_dt"].strftime("%H:%M:%S") if row["_dt"] != datetime.min else "—",
                            str(row.get("broker_order_id") or row.get("cache_id") or ""),
                            f"{_display_price(_price_unit(row.get('fill_price_vnd') or row.get('limit_price')))} · {fill_delta(row)} CP",
                            "CHỜ DNSE" if mode == "REAL" and not fee_known(row) else f"{_compact_vnd(row.get('fee'))} / {_compact_vnd(row.get('tax'))}",
                            "", str(row.get("broker_status") or row.get("queue_status") or ""), str(row.get("message") or row.get("error") or "")), tag="buy" if side == "BUY" else "sell"))
            orders.append(dict(id="order:" + trade_id + ":" + "|".join(identity), label=f"{side or 'LỆNH'} · {latest.get('order_type') or ''}",
                values=(clock, identity[1] if len(identity) == 4 else str(latest.get("cache_id") or ""),
                        f"{_display_price(price)} · khớp {filled}/{quantity} CP", "CHỜ DNSE" if pending_fee else _compact_vnd(costs), "", caption,
                        "DNSE báo khớp; chờ app đối soát" if awaiting_reconcile else "Phí chưa được DNSE cung cấp" if pending_fee else "Xổ để xem lần gửi / khớp / đối soát"),
                tag="buy" if side == "BUY" else "sell", details=detail_rows, costs=costs, pending_fee=pending_fee))
        quantity = cycle.entry_quantity if cycle else max((int(_number(row.get("quantity"))) for row in items), default=0)
        entry = cycle.avg_entry_price if cycle else next((_price_unit(row.get("limit_price")) for row in reversed(items) if row.get("side") == "BUY" and _number(row.get("limit_price")) > 0), 0)
        pnl = cycle.net_pnl if cycle else 0
        costs = cycle.fees_paid if cycle else sum(row["costs"] for row in orders)
        status = cycle.status if cycle else str(items[-1].get("broker_status") or "").upper()
        pending_fee = any(row["pending_fee"] for row in orders)
        label = f"{symbol} · " + ("ĐANG GIỮ" if status == "OPEN" else "ĐÃ ĐÓNG" if status == "CLOSED" else "LỆNH DNSE")
        days.setdefault(day, []).append(dict(id="trade:" + trade_id, label=label,
            values=("", trade_id.removeprefix("broker:")[:14], f"{_display_price(entry)} · {quantity} CP", "CHỜ DNSE" if pending_fee and not costs else _compact_vnd(costs), _compact_vnd(pnl) if status == "CLOSED" else "—", status, ""),
            tag="trade_win" if status == "CLOSED" and pnl >= 0 else "trade_loss" if status == "CLOSED" else "trade_open",
            events=orders, pnl=pnl, costs=costs, closed=status == "CLOSED", pending_fee=pending_fee))
    output = []
    for day in sorted(days, reverse=True)[:day_limit]:
        trades_for_day = days[day]
        closed = sum(row["closed"] for row in trades_for_day)
        costs = sum(row["costs"] for row in trades_for_day)
        pending = any(row["pending_fee"] for row in trades_for_day)
        date = datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y") if day != "0000-00-00" else "Chưa có ngày"
        output.append(dict(id="day:" + day, label=f"{date} · {len(trades_for_day)} giao dịch",
            values=("", "", "", "CHỜ DNSE" if pending and not costs else _compact_vnd(costs), _compact_vnd(sum(row["pnl"] for row in trades_for_day if row["closed"])), f"{closed} đã đóng", "Có phí đang chờ DNSE" if pending else ""), trades=trades_for_day))
    return output
