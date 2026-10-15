"""Rebuild position price extrema from the requested trading sessions."""
from __future__ import annotations

from datetime import datetime, timedelta
import math

from .market import VN_TZ, exchange_close_minute, is_working_day, normalize_exchange


def session_count(value) -> int:
    text = str(value).strip()
    if not text.isdigit() or not 1 <= int(text) <= 30:
        raise ValueError("Số phiên phải là số nguyên từ 1 đến 30.")
    return int(text)


def position_basis(cycle) -> dict:
    # Confirming an old preview must not reset a changed position.
    return {key: getattr(cycle, key) for key in (
        "id", "symbol", "execution_mode", "opened_at", "avg_entry_price",
        "buy_notional", "open_quantity", "sold_quantity", "net_pnl", "fees_paid",
    )}


def _bars(payload, *, minute=False) -> list[dict]:
    keys = ("t", "o", "h", "l", "c", "v")
    if (isinstance(payload, dict) and payload.get("boardId", "G1") == "G1"
            and payload.get("s") == "no_data" and not payload.get("t")):
        return []
    if (not isinstance(payload, dict) or payload.get("boardId", "G1") != "G1"
            or str(payload.get("s", "ok")).lower() not in {"ok", "no_data"}
            or any(not isinstance(payload.get(key), list) for key in keys)
            or any(len(payload[key]) != len(payload["t"]) for key in keys)):
        raise ValueError("Lịch sử nến không hợp lệ; giữ nguyên MAE/MFE cũ.")
    rows = {}
    for values in zip(*(payload[key] for key in keys)):
        try:
            stamp, op, high, low, close, volume = map(float, values)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Lịch sử có giá không hợp lệ.") from exc
        if (not all(math.isfinite(v) for v in (stamp, op, high, low, close, volume))
                or stamp <= 0 or stamp != int(stamp) or minute and stamp % 60
                or not 0 < low <= min(op, close) <= max(op, close) <= high or volume < 0):
            raise ValueError("Lịch sử có nến không hợp lệ.")
        if volume == 0:
            continue
        # DNSE stock candles use thousand VND; accept explicit VND too.
        unit = str(payload.get("price_unit", "THOUSAND_VND")).upper()
        if unit not in {"THOUSAND_VND", "VND"}:
            raise ValueError("Đơn vị giá lịch sử không hợp lệ.")
        factor = 1000 if unit == "VND" else 1
        row = {"time": int(stamp), "high": high / factor, "low": low / factor}
        if int(stamp) in rows and rows[int(stamp)] != row:
            raise ValueError("Lịch sử có nến trùng thời điểm nhưng khác giá.")
        rows[int(stamp)] = row
    return sorted(rows.values(), key=lambda row: row["time"])


def load_position_history(client, cycle, sessions=3, *, now=None, holidays=(), exchange="") -> dict:
    """Fetch per-day minute candles; never substitute a current quote for history.

    A minute straddling the purchase is omitted to exclude pre-entry prices.
    Completed full sessions are checked against daily high/low to detect truncation.
    """
    count = session_count(sessions)
    now = now or datetime.now(VN_TZ)
    now = now.astimezone(VN_TZ)
    # Without a known exchange, wait until every cash-equity session has closed.
    close_minute = exchange_close_minute(normalize_exchange(exchange) or "UPCOM")
    if cycle.status != "OPEN" or cycle.open_quantity <= 0:
        raise ValueError("Vị thế đã thay đổi hoặc đã đóng.")
    if not math.isfinite(cycle.opened_at) or not 0 < cycle.opened_at <= now.timestamp():
        raise ValueError("Chưa có thời điểm mua hợp lệ.")
    entered = datetime.fromtimestamp(cycle.opened_at, VN_TZ)
    # /market/working-dates describes the year FROM today; it need not
    # contain historical sessions. Never treat that forward schedule as a
    # complete historical calendar, or keep retrying it instead of OHLC.
    working_dates = client.get_working_dates(force=True)
    end_day = now.date() if now.hour >= 9 else now.date() - timedelta(days=1)
    dates = sorted({datetime.strptime(str(day)[:10], "%Y-%m-%d").date() for day in working_dates})
    dates = [day for day in dates if day <= end_day and is_working_day(day, holidays=holidays)]
    if len(dates) < count and (not dates or entered.date() < dates[0]):
        # Index bars establish actual market sessions, including holiday gaps.
        # Use the market index rather than the held stock: a suspended stock
        # must not silently turn a missing session into an older one.
        start_day = end_day - timedelta(days=max(45, count * 4 + 15))
        index_left = datetime.combine(start_day, datetime.min.time(), VN_TZ)
        index_rows = _bars(client.get_ohlc("VNINDEX", "1D", int(index_left.timestamp()), int(now.timestamp())))
        observed = {datetime.fromtimestamp(row["time"], VN_TZ).date() for row in index_rows
                    if index_left.timestamp() <= row["time"] <= now.timestamp()}
        dates = sorted(set(dates) | {day for day in observed if day <= end_day and is_working_day(day, holidays=holidays)})
    if not dates:
        raise ValueError("Chưa có nến ngày VNINDEX để xác nhận các phiên cũ; giữ nguyên MAE/MFE.")
    chosen = [day for day in dates[-count:] if day >= entered.date()]
    if not chosen:
        raise ValueError("Chưa có phiên giao dịch kể từ lúc mua.")
    if len(dates) < count and entered.date() < dates[0]:
        raise ValueError(f"Nến ngày mới xác nhận {len(dates)}/{count} phiên đến {dates[-1]:%d/%m}; chờ bổ sung lịch sử.")
    left = datetime.combine(chosen[0], datetime.min.time(), VN_TZ)
    daily_rows = _bars(client.get_ohlc(cycle.symbol, "1D", int(left.timestamp()), int(now.timestamp())))
    daily = {datetime.fromtimestamp(row["time"], VN_TZ).date(): row for row in daily_rows}
    if any(day > chosen[-1] and day <= end_day for day in daily):
        raise ValueError("Lịch phiên chưa bao phủ dữ liệu nến mới nhất.")
    all_rows = []
    ranges = []
    pending_sessions = []
    for day in chosen:
        midnight = datetime.combine(day, datetime.min.time(), VN_TZ)
        # Ceil to the first minute entirely after purchase, including exact boundaries.
        start = max(int(midnight.timestamp()), math.ceil(cycle.opened_at / 60) * 60)
        end = min(int((midnight + timedelta(days=1)).timestamp()) - 1, int(now.timestamp()))
        if start > end:
            raise ValueError("Chưa có nến phút sau thời điểm mua; thử lại sau.")
        rows = _bars(client.get_ohlc(cycle.symbol, "1", start, end), minute=True)
        rows = [row for row in rows if start <= row["time"] <= end]
        completed = day < now.date() or now.hour * 60 + now.minute >= close_minute
        if not rows:
            if not completed:
                pending_sessions.append(day.isoformat())
                continue
            raise ValueError(f"Thiếu nến phút phiên {day:%d/%m/%Y}; giữ nguyên MAE/MFE cũ.")
        if completed and start == int(midnight.timestamp()):
            reference = daily.get(day)
            high, low = max(row["high"] for row in rows), min(row["low"] for row in rows)
            if (not reference or not math.isclose(high, reference["high"], rel_tol=1e-7)
                    or not math.isclose(low, reference["low"], rel_tol=1e-7)):
                raise ValueError(f"Lịch sử phút phiên {day:%d/%m/%Y} chưa khớp đỉnh/đáy nến ngày.")
        all_rows.extend(rows)
        ranges.append({"day": day.isoformat(), "from": start, "to": end, "bars": len(rows)})
    if not all_rows:
        raise ValueError("Chưa có nến phút sau lúc mua; chờ API cập nhật và thử lại.")
    return {
        "symbol": cycle.symbol, "basis": position_basis(cycle),
        "high": max(row["high"] for row in all_rows), "low": min(row["low"] for row in all_rows),
        "requested_sessions": count, "loaded_sessions": len(ranges), "session_dates": [row["day"] for row in ranges],
        "pending_sessions": pending_sessions,
        "started_at": ranges[0]["from"], "ended_at": ranges[-1]["to"],
        "bar_count": len(all_rows), "ranges": ranges, "resolution": "1",
        "entry_minute_omitted": cycle.opened_at % 60 != 0 and entered.date() in chosen,
    }
