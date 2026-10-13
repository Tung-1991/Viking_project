"""EMA evidence since watchlist admission, independent of order execution."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite

from ..trading.market import VN_TZ, exchange_close_minute, is_working_day


def memory_deadline(cross_at, count, exchange, working_dates=None, holidays=None):
    start = datetime.fromisoformat(cross_at).astimezone(VN_TZ).date()
    selected = []
    day = start
    for _ in range(1500):
        if is_working_day(day, working_dates, holidays):
            selected.append(day.isoformat())
            if len(selected) == count:
                end = datetime.combine(day, datetime.min.time(), VN_TZ)
                return (end + timedelta(minutes=exchange_close_minute(exchange))).timestamp(), selected
        day += timedelta(days=1)
    return 0.0, []  # An incomplete explicit calendar cannot invent a deadline.


def refresh_memory(row, *, now, mode, sessions, exchange, working_dates=None, holidays=None):
    row = dict(row or {})
    expires, dates = (0.0, [])
    if mode == "SESSIONS" and row.get("cross_at"):
        expires, dates = memory_deadline(row["cross_at"], sessions, exchange, working_dates, holidays)
    expired = bool(expires and now.timestamp() >= expires)
    calendar_ready = mode == "VA" or not row.get("cross_id") or bool(expires)
    ready = bool(row.get("initialized") and row.get("current_ready") and calendar_ready
                 and row.get("watch_active", True))
    above = row.get("current_fast", 0) > row.get("current_slow", 0)
    valid = bool(ready and row.get("cross_id") and not expired and above)
    used = bool(row.get("used"))
    state = ("UNKNOWN" if not ready else "EXPIRED" if expired else
             "WAIT_UP" if not above else "SESSION_USED" if used and row.get("cross_id") else "SESSION_ACTIVE" if valid else
             "WAIT_UP" if not above else "WAIT_DOWN")
    return {**row, "required": True, "session": True, "tracking_version": 2,
            "mode": mode, "sessions": sessions, "expires_at": expires,
            "remaining_sessions": sum(day >= now.date().isoformat() for day in dates) if not expired else 0,
            "expiry_sessions": dates, "ready": ready, "valid": valid, "state": state,
            "day": now.date().isoformat()}


def advance_memory(row, current, *, now, pair, mode, sessions, exchange,
                   working_dates=None, holidays=None):
    row = dict(row or {})
    try:
        fast, slow = float(current.get("buy_ema_fast")), float(current.get("buy_ema_slow"))
        ready = current.get("signal_ready") is not False and isfinite(fast) and isfinite(slow)
    except (TypeError, ValueError, OverflowError):
        ready = False
    if not ready:
        return {**row, "current_ready": False, "ready": False, "valid": False, "state": "UNKNOWN"}
    if row.get("profile") not in {None, "", pair} or not row.get("tracking_version") == 2:
        row = {"tracking_version": 2, "profile": pair, "watch_id": f"{pair}:{now.timestamp()}",
               "started_at": now.isoformat(), "started_at_epoch": now.timestamp(),
               "up_count": 0, "down_count": 0, "initialized": False}
    if row.get("initialized") and now.timestamp() <= float(row.get("observed_at_epoch", 0)):
        return refresh_memory(row, now=now, mode=mode, sessions=sessions, exchange=exchange,
                              working_dates=working_dates, holidays=holidays)
    row["profile"] = pair
    previous_fast, previous_slow = row.get("current_fast"), row.get("current_slow")
    if row.get("initialized") and now.timestamp() > float(row.get("observed_at_epoch", 0)):
        # Count strict sign changes. Touching equality is neutral: it cannot
        # generate multiple crosses by bouncing on the same boundary.
        previous_relation = int(row.get("last_relation", 0))
        relation = 1 if fast > slow else -1 if fast < slow else 0
        if relation == 1 and previous_relation <= 0:
            row["up_count"] = int(row.get("up_count", 0)) + 1
            row.update(cross_at=now.isoformat(), cross_timestamp=now.timestamp(), used=False,
                       cross_id=f"EMA:{row['watch_id']}:{row['up_count']}:{now.timestamp()}",
                       previous_fast=previous_fast, previous_slow=previous_slow)
        elif relation == -1 and previous_relation >= 0:
            row["down_count"] = int(row.get("down_count", 0)) + 1
            row["last_down_at"] = now.isoformat()
        if relation:
            row["last_relation"] = relation
    else:
        row["last_relation"] = 1 if fast > slow else -1 if fast < slow else 0
    row.update(initialized=True, current_ready=True, current_fast=float(fast), current_slow=float(slow),
               observed_at_epoch=now.timestamp(), observed_at=now.isoformat())
    return refresh_memory(row, now=now, mode=mode, sessions=sessions, exchange=exchange,
                          working_dates=working_dates, holidays=holidays)


def memory_caption(row):
    mode = "∞" if row.get("mode", "VA") == "VA" else f"⏳{row.get('remaining_sessions', 0)}/{row.get('sessions', 3)}P"
    symbol = {"SESSION_ACTIVE": "✓", "SESSION_USED": "☑", "EXPIRED": "⌛",
              "WAIT_UP": "⏸", "WAIT_DOWN": "↓→↑", "UNKNOWN": "?"}.get(row.get("state"), "⏸")
    return f"{mode} · ↑{row.get('up_count', 0)} ↓{row.get('down_count', 0)} · {symbol}"


def memory_hint(row):
    return ("∞: nhớ tới khi dùng; ⏳: phiên còn/tổng (tính cả phiên cắt). ↑↓: số lần cắt từ lúc thêm mã.\n"
            "✓: EMA đạt; ⏸: chưa đạt/ngoài phiên; ☑: đã dùng; ⌛: hết hạn; ?: thiếu dữ liệu.\n"
            f"Theo dõi: {row.get('started_at', '—')}. Cắt lên gần nhất: {row.get('cross_at', '—')}.\n"
            "Bỏ giao cắt trước khi thêm mã. EMA xuống vẫn giữ ghi nhận; BUY kiểm tra EMA/RSI hiện tại.")


def memory_fields(row):
    """Flat metadata survives both CSV events and periodic TRACE exports."""
    return {"ema_memory_" + key: row.get(key, "") for key in
            ("tracking_version", "mode", "sessions", "remaining_sessions", "up_count", "down_count", "started_at")}


def memory_from_fields(raw):
    row = {key: raw.get("ema_memory_" + key) for key in
           ("tracking_version", "mode", "sessions", "remaining_sessions", "up_count", "down_count", "started_at")}
    for key in ("tracking_version", "sessions", "remaining_sessions", "up_count", "down_count"):
        try:
            row[key] = int(row[key] or 0)
        except (ValueError, TypeError):
            row[key] = 0
    row.update(state=raw.get("ema_cross_state", "UNKNOWN"), cross_at=raw.get("ema_cross_at", ""),
               required=str(raw.get("ema_cross_required", "")).lower() == "true")
    return row
