"""EMA evidence and bounded historical lookback, independent of execution."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite

from ..trading.market import VN_TZ, exchange_close_minute, is_working_day


def lookback_start(now, count, working_dates=None, holidays=None):
    """Midnight of the Xth prior trading session; never count today or holidays."""
    day = now.astimezone(VN_TZ).date()
    selected = 0
    for _ in range(1500):
        day -= timedelta(days=1)
        if is_working_day(day, working_dates, holidays):
            selected += 1
            if selected == count:
                return datetime.combine(day, datetime.min.time(), VN_TZ)
    return None


_CROSS_KEYS = ("cross_id", "cross_at", "cross_timestamp", "previous_fast", "previous_slow", "used")


def seed_lookback(row, observations, *, start, now, **args):
    """Keep pre-admission evidence separate so VA and consumed live IDs survive toggles."""
    row = dict(row or {})
    history = {"tracking_version": 2, "watch_id": row.get("watch_id", str(now.timestamp())),
               "initialized": False, "up_count": 0, "down_count": 0}
    for at, marks in observations:
        history = advance_memory(history, marks, now=at, **args)
        if not history.get("current_ready"):
            return {**row, "lookback_ready": False}
    cross = {key: history[key] for key in _CROSS_KEYS if key in history}
    if cross:
        cross["cross_id"] = f"EMA:HISTORY:{history['watch_id']}:{args['pair']}:{cross['cross_timestamp']}"
    return {**row, "lookback_ready": True, "lookback_started_at": start.isoformat(),
            "lookback_up_count": history.get("up_count", 0),
            "lookback_down_count": history.get("down_count", 0), "lookback_cross": cross}


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


def refresh_memory(row, *, now, mode, sessions, exchange, working_dates=None, holidays=None,
                   va_lookback_enabled=False):
    row = dict(row or {})
    if row.get("cross_source") in {"HISTORY", "VA_HISTORY"}:
        # Preserve consumption even if the UI switches to VA and back.
        row["lookback_cross"] = {key: row[key] for key in _CROSS_KEYS if key in row}
        for key in _CROSS_KEYS:
            row.pop(key, None)
        row.pop("cross_source", None)
    historical = mode == "SESSIONS" or (mode == "VA" and va_lookback_enabled)
    if historical and not row.get("cross_id") and row.get("lookback_cross"):
        row.update(row["lookback_cross"],
                   cross_source="HISTORY" if mode == "SESSIONS" else "VA_HISTORY")
    expires, dates = (0.0, [])
    if mode == "SESSIONS" and row.get("cross_at"):
        expires, dates = memory_deadline(row["cross_at"], sessions, exchange, working_dates, holidays)
    expired = bool(expires and now.timestamp() >= expires)
    calendar_ready = mode == "VA" or not row.get("cross_id") or bool(expires)
    ready = bool(row.get("initialized") and row.get("current_ready") and calendar_ready
                 and row.get("watch_active", True)
                  and (not historical or row.get("lookback_ready") is not False
                      or row.get("cross_source") == "LIVE"))
    above = row.get("current_fast", 0) > row.get("current_slow", 0)
    valid = bool(ready and row.get("cross_id") and not expired and above)
    used = bool(row.get("used"))
    state = ("UNKNOWN" if not ready else "EXPIRED" if expired else
             "WAIT_UP" if not above else "SESSION_USED" if used and row.get("cross_id") else "SESSION_ACTIVE" if valid else
             "WAIT_UP" if not above else "WAIT_DOWN")
    return {**row, "required": True, "session": True, "tracking_version": 2,
             "mode": mode, "sessions": sessions,
             "va_lookback_enabled": bool(va_lookback_enabled), "expires_at": expires,
             "va_lookback_active": bool(mode == "VA" and va_lookback_enabled),
            "remaining_sessions": sum(day >= now.date().isoformat() for day in dates) if not expired else 0,
            "expiry_sessions": dates, "ready": ready, "valid": valid, "state": state,
            "day": now.date().isoformat()}


def advance_memory(row, current, *, now, pair, mode, sessions, exchange,
                   working_dates=None, holidays=None, va_lookback_enabled=False):
    row = dict(row or {})
    if row.get('tracking_version') == 2 and row.get('cross_id') and not row.get('cross_source'):
        row['cross_source'] = 'LIVE'  # Version 2 before lookback recorded only admitted live crossings.
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
                              working_dates=working_dates, holidays=holidays,
                              va_lookback_enabled=va_lookback_enabled)
    row["profile"] = pair
    previous_fast, previous_slow = row.get("current_fast"), row.get("current_slow")
    if row.get("initialized") and now.timestamp() > float(row.get("observed_at_epoch", 0)):
        # Count strict sign changes. Touching equality is neutral: it cannot
        # generate multiple crosses by bouncing on the same boundary.
        previous_relation = int(row.get("last_relation", 0))
        relation = 1 if fast > slow else -1 if fast < slow else 0
        if relation == 1 and previous_relation == -1:
            row["up_count"] = int(row.get("up_count", 0)) + 1
            row.update(cross_at=now.isoformat(), cross_timestamp=now.timestamp(), used=False,
                       cross_id=f"EMA:{row['watch_id']}:{row['up_count']}:{now.timestamp()}",
                       previous_fast=previous_fast, previous_slow=previous_slow)
            row["cross_source"] = "LIVE"
        elif relation == -1 and previous_relation == 1:
            row["down_count"] = int(row.get("down_count", 0)) + 1
            row["last_down_at"] = now.isoformat()
        if relation:
            row["last_relation"] = relation
    else:
        row["last_relation"] = 1 if fast > slow else -1 if fast < slow else 0
        row["baseline"] = {"at": now.isoformat(), "buy_ema_fast": fast, "buy_ema_slow": slow}
    row.update(initialized=True, current_ready=True, current_fast=float(fast), current_slow=float(slow),
               observed_at_epoch=now.timestamp(), observed_at=now.isoformat())
    return refresh_memory(row, now=now, mode=mode, sessions=sessions, exchange=exchange,
                          working_dates=working_dates, holidays=holidays,
                          va_lookback_enabled=va_lookback_enabled)


def memory_caption(row):
    mode = (f"∞+{row.get('sessions', 3)}P" if row.get("va_lookback_enabled") else "∞") if row.get("mode", "VA") == "VA" else f"⏳{row.get('remaining_sessions', 0)}/{row.get('sessions', 3)}P"
    symbol = {"SESSION_ACTIVE": "ĐÃ CẮT LÊN", "SESSION_USED": "ĐÃ DÙNG", "EXPIRED": "HẾT HẠN",
              "WAIT_UP": "CHỜ CẮT LÊN", "WAIT_DOWN": "CHỜ XUỐNG RỒI LÊN",
              "UNKNOWN": "CHỜ DỮ LIỆU"}.get(row.get("state"), "CHỜ")
    historical = row.get("mode") == "SESSIONS" or row.get("va_lookback_enabled")
    up = int(row.get('up_count', 0)) + (int(row.get('lookback_up_count', 0)) if historical else 0)
    down = int(row.get('down_count', 0)) + (int(row.get('lookback_down_count', 0)) if historical else 0)
    return f"{mode} · ↑{up} ↓{down} · {symbol}"


def memory_hint(row):
    history = ("⏳: quét X phiên trước và phiên hiện tại khi bắt đầu; giữ X phiên từ lần cắt (tính cả phiên cắt).\n"
               if row.get("mode") == "SESSIONS" else
               "∞ VA + quét: lấy lần cắt trong X phiên trước khi bật, rồi nhớ tới khi dùng.\n"
               if row.get("va_lookback_enabled") else "∞: chỉ ghi nhận từ khi thêm mã, nhớ tới khi dùng.\n")
    start = row.get("lookback_started_at") if row.get("mode") == "SESSIONS" or row.get("va_lookback_enabled") else None
    return (history + "↑↓: số lần cắt trong dữ liệu theo dõi; ⏳: phiên còn/tổng.\n"
            "EMA nhanh < EMA chậm: chỉ chờ cắt LÊN, không cần xuống thêm lần nữa. "
            "EMA bằng nhau là trung tính, chưa ghi nhận cắt lên. "
            "Nếu bắt đầu theo dõi khi EMA nhanh đã ở trên mà chưa có lần cắt hợp lệ: chờ xuống rồi lên.\n"
            f"Theo dõi: {start or row.get('started_at', '—')}. Cắt lên gần nhất: {row.get('cross_at', '—')}.\n"
            "Sau khi ghi nhận cắt lên, VA giữ lần cắt tới khi dùng; RSI có thể đạt sau, không cần cắt lên lần nữa. "
            "BUY vẫn kiểm tra EMA/RSI hiện tại và các khóa vào lệnh.")


def memory_fields(row):
    """Flat metadata survives both CSV events and periodic TRACE exports."""
    values = {key: row.get(key, "") for key in
              ("tracking_version", "mode", "sessions", "remaining_sessions", "up_count", "down_count", "started_at")}
    if row.get("mode") == "SESSIONS" or row.get("va_lookback_enabled"):
        for key in ("up_count", "down_count"):
            values[key] = int(row.get(key, 0)) + int(row.get("lookback_" + key, 0))
        values["started_at"] = row.get("lookback_started_at") or values["started_at"]
    return {"ema_memory_" + key: value for key, value in values.items()}


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
