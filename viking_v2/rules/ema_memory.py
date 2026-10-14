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


def _local_day(value):
    try:
        at = datetime.fromisoformat(str(value))
        return (at.replace(tzinfo=VN_TZ) if at.tzinfo is None else at.astimezone(VN_TZ)).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def memory_day_counts(row, *, now):
    """Today's observed flips, independently of the retained BUY evidence."""
    today = now.astimezone(VN_TZ).date().isoformat()
    result = {"day": today, "up": None, "down": None, "from": "", "partial": False}
    day = _local_day(row.get("daily_count_day"))
    if day == today:
        try:
            return {**result, "up": max(0, int(row["daily_up_count"])),
                    "down": max(0, int(row["daily_down_count"])),
                    "from": row.get("daily_count_from", ""),
                    "partial": bool(row.get("daily_count_partial"))}
        except (KeyError, TypeError, ValueError, OverflowError):
            return result
    if day and day < today:
        return {**result, "up": 0, "down": 0}
    if day:
        return result
    started = _local_day(row.get("started_at") or (row.get("baseline") or {}).get("at"))
    observed = _local_day(row.get("observed_at"))
    if started == observed == today:
        # A legacy total is safe only when all admitted samples are from today.
        try:
            return {**result, "up": max(0, int(row.get("up_count", 0))),
                    "down": max(0, int(row.get("down_count", 0))),
                    "from": row.get("started_at") or (row.get("baseline") or {}).get("at", "")}
        except (TypeError, ValueError, OverflowError):
            return result
    if not row.get("initialized") or (observed and observed < today):
        return {**result, "up": 0, "down": 0}
    if row.get("up_count", 0) == row.get("down_count", 0) == 0:
        return {**result, "up": 0, "down": 0}
    return result  # A legacy multi-day total cannot invent today's count.


def memory_window_counts(row, *, now, prior_sessions=0, working_dates=None, holidays=None):
    """Count observed flips inside today plus the selected prior trading sessions."""
    today = now.astimezone(VN_TZ).date().isoformat()
    start = lookback_start(now, prior_sessions, working_dates, holidays) if prior_sessions else now
    result = {**memory_day_counts(row, now=now), "start": _local_day(start), "end": today}
    if not prior_sessions:
        return result
    if start is None:
        return {**result, "up": None, "down": None}
    start_day = result["start"]

    def count(ledger, up, down, first, last):
        ledger = dict(ledger or {})
        if not first or not last:
            return (0, 0) if not up and not down else (None, None)
        if last < start_day or first > today:
            return 0, 0
        try:
            totals = tuple(sum(int(value[key]) for value in ledger.values()) for key in ("up", "down"))
            if totals != (int(up), int(down)):
                if start_day <= first <= last <= today:
                    return int(up), int(down)  # The complete legacy span is inside the window.
                earliest = min(ledger) if ledger else last
                unknown_end = (earliest if ledger.get(earliest, {}).get("partial") else
                               (datetime.fromisoformat(earliest) - timedelta(days=1)).date().isoformat())
                if start_day <= unknown_end and first <= today:
                    return None, None
            selected = [value for day, value in ledger.items() if start_day <= day <= today
                        and (day == today or is_working_day(datetime.fromisoformat(day).date(), working_dates, holidays))]
            return tuple(sum(int(value[key]) for value in selected) for key in ("up", "down"))
        except (TypeError, ValueError, KeyError, OverflowError):
            return None, None

    live = dict(row.get("count_days") or {})
    recorded_day = _local_day(row.get("daily_count_day"))
    if recorded_day:
        live[recorded_day] = {"up": row.get("daily_up_count"), "down": row.get("daily_down_count"),
                              "partial": bool(row.get("daily_count_partial"))}
    up, down = count(live, row.get("up_count", 0), row.get("down_count", 0),
                     _local_day(row.get("started_at") or (row.get("baseline") or {}).get("at")),
                     _local_day(row.get("observed_at")))
    if row.get("mode") == "SESSIONS" or row.get("va_lookback_enabled"):
        admitted_day = _local_day(row.get("started_at") or (row.get("baseline") or {}).get("at"))
        unscanned = (row.get("lookback_ready") is not True
                     and (not admitted_day or start_day < admitted_day))
        if row.get("lookback_ready") is False or unscanned:
            up = down = None
        else:
            historical = count(row.get("lookback_count_days"), row.get("lookback_up_count", 0),
                               row.get("lookback_down_count", 0), _local_day(row.get("lookback_started_at")),
                               _local_day(row.get("lookback_anchor_at") or row.get("started_at")))
            if up is None or historical[0] is None:
                up = down = None
            else:
                up, down = up + historical[0], down + historical[1]
    return {**result, "up": up, "down": down, "partial": False}


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
            "lookback_down_count": history.get("down_count", 0),
            "lookback_last_down_at": history.get("last_down_at", ""),
            "lookback_count_days": history.get("count_days", {}), "lookback_cross": cross}


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
    count_days = dict(row.get("count_days") or {})
    old_day = _local_day(row.get("daily_count_day"))
    if old_day:
        count_days[old_day] = {"up": row.get("daily_up_count", 0), "down": row.get("daily_down_count", 0),
                               "partial": bool(row.get("daily_count_partial"))}
    daily = memory_day_counts(row, now=now)
    row.update(daily_count_day=daily["day"], daily_up_count=daily["up"] or 0,
               daily_down_count=daily["down"] or 0,
               daily_count_from=daily["from"] or now.isoformat(),
               daily_count_partial=daily["partial"] or daily["up"] is None)
    row["profile"] = pair
    previous_fast, previous_slow = row.get("current_fast"), row.get("current_slow")
    if row.get("initialized") and now.timestamp() > float(row.get("observed_at_epoch", 0)):
        # Count strict sign changes. Touching equality is neutral: it cannot
        # generate multiple crosses by bouncing on the same boundary.
        previous_relation = int(row.get("last_relation", 0))
        relation = 1 if fast > slow else -1 if fast < slow else 0
        if relation == 1 and previous_relation == -1:
            row["up_count"] = int(row.get("up_count", 0)) + 1
            row["daily_up_count"] += 1
            row.update(cross_at=now.isoformat(), cross_timestamp=now.timestamp(), used=False,
                       cross_id=f"EMA:{row['watch_id']}:{row['up_count']}:{now.timestamp()}",
                       previous_fast=previous_fast, previous_slow=previous_slow)
            row["cross_source"] = "LIVE"
        elif relation == -1 and previous_relation == 1:
            row["down_count"] = int(row.get("down_count", 0)) + 1
            row["daily_down_count"] += 1
            row["last_down_at"] = now.isoformat()
        if relation:
            row["last_relation"] = relation
    else:
        row["last_relation"] = 1 if fast > slow else -1 if fast < slow else 0
        row["baseline"] = {"at": now.isoformat(), "buy_ema_fast": fast, "buy_ema_slow": slow}
    row.update(initialized=True, current_ready=True, current_fast=float(fast), current_slow=float(slow),
               observed_at_epoch=now.timestamp(), observed_at=now.isoformat())
    count_days[daily["day"]] = {"up": row["daily_up_count"], "down": row["daily_down_count"],
                                "partial": row["daily_count_partial"]}
    # Only the display ledger is bounded; retained VA evidence and used IDs are unchanged.
    eligible_days = [day for day in sorted(count_days) if _local_day(day) == day and
                     (day == daily["day"] or is_working_day(datetime.fromisoformat(day).date(), working_dates, holidays))]
    row["count_days"] = {day: count_days[day] for day in eligible_days[-251:]}
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


def memory_count_scope(row, *, holidays=None, working_dates=None):
    """Show the trading-session span behind cumulative ↑/↓, including old saved rows."""
    if not isinstance(row, dict):
        return None
    historical = row.get("mode") == "SESSIONS" or bool(row.get("va_lookback_enabled"))
    start = (row.get("lookback_started_at") if historical and row.get("lookback_ready") is True else None)
    start = start or row.get("started_at") or (row.get("baseline") or {}).get("at")
    end = row.get("observed_at")
    if not start or not end:
        return None
    try:
        first_at = datetime.fromisoformat(str(start))
        last_at = datetime.fromisoformat(str(end))
        first = (first_at.replace(tzinfo=VN_TZ) if first_at.tzinfo is None else first_at.astimezone(VN_TZ)).date()
        last = (last_at.replace(tzinfo=VN_TZ) if last_at.tzinfo is None else last_at.astimezone(VN_TZ)).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    span = (last - first).days
    if span < 0 or span > 1500:
        return None
    sessions = sum(is_working_day(first + timedelta(days=offset), working_dates, holidays)
                   for offset in range(span + 1))
    return {"start": first.isoformat(), "end": last.isoformat(), "sessions": sessions}


def memory_hint(row, *, holidays=None, working_dates=None):
    history = ("⏳: quét X phiên trước khi bắt đầu theo dõi mã; giữ X phiên từ lần cắt (tính cả phiên cắt).\n"
               if row.get("mode") == "SESSIONS" else
               "∞ VA + quét: lấy lần cắt trong X phiên trước khi bắt đầu theo dõi mã, rồi nhớ tới khi dùng.\n"
               if row.get("va_lookback_enabled") else "∞: chỉ ghi nhận từ khi thêm mã, nhớ tới khi dùng.\n")
    scan_start = row.get("lookback_started_at") if row.get("mode") == "SESSIONS" or row.get("va_lookback_enabled") else None
    scope = memory_count_scope(row, holidays=holidays, working_dates=working_dates)
    fields = memory_fields(row)
    up = int(fields["ema_memory_up_count"])
    down = int(fields["ema_memory_down_count"])
    period = (f"{up + down} lần cắt = {up} lên + {down} xuống. "
              f"Phạm vi đếm: {scope['start']} → {scope['end']} · {scope['sessions']} phiên giao dịch theo lịch. "
              "Bộ đếm là tích lũy trong phạm vi này, không phải số lần cắt của từng phiên.\n"
              if scope else f"{up + down} lần cắt = {up} lên + {down} xuống. Phạm vi đếm: chưa có đủ mốc thời gian đã lưu.\n")
    return (history + period + "↑↓: số lần EMA 1D tạm tính đổi phía theo mẫu giá app nhận trong phiên; có thể cắt nhiều lần cùng ngày. Quét trước tái dựng từ giá đóng mỗi phút, không phát lại từng tick; ⏳: phiên còn/tổng.\n"
            "EMA nhanh < EMA chậm: chỉ chờ cắt LÊN, không cần xuống thêm lần nữa. "
            "EMA bằng nhau là trung tính, chưa ghi nhận cắt lên. "
            "Nếu bắt đầu theo dõi khi EMA nhanh đã ở trên mà chưa có lần cắt hợp lệ: chờ xuống rồi lên.\n"
            f"Bắt đầu theo dõi: {row.get('started_at', '—')}. Quét trước từ: {scan_start or 'OFF'}. "
            f"Cắt lên gần nhất: {row.get('cross_at', '—')}.\n"
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
