"""Price-only history for the alternate EMA view; never reads/writes order state."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..backtest.data import normalize_ohlc
from ..rules.business import StaticRuleParameters, indicator_snapshot
from ..rules.ema_memory import advance_memory, lookback_start
from ..services.session_cross import SessionCrossService, SessionPriceCache
from ..trading.market import VN_TZ, exchange_close_minute, is_working_day


def preview_key(symbol, book, source, params, signal_mode, exchange, holidays, now):
    return (str(symbol).upper(), book, source.get("watch_id"), source.get("started_at"),
            params.buy_ema_fast, params.buy_ema_slow, params.rsi_period,
            params.buy_ema_cross_sessions, signal_mode, exchange,
            tuple(sorted(holidays)), now.date().isoformat())


def load_history(client, cache_path: Path, symbol: str, bars: list[dict], params: StaticRuleParameters,
                 signal_mode: str, now: datetime, exchange: str, holidays: list[str],
                 baseline: dict | None = None) -> dict[str, Any]:
    """Reconstruct the selected prior sessions only. Today's live count stays separate."""
    start = lookback_start(now, params.buy_ema_cross_sessions, holidays=holidays)
    if start is None:
        return {"ready": False, "issue": "CALENDAR"}
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = today
    baseline = baseline or {}
    try:
        admitted = datetime.fromisoformat(str(baseline.get("at", "")))
        admitted = admitted.replace(tzinfo=VN_TZ) if admitted.tzinfo is None else admitted.astimezone(VN_TZ)
        if today <= admitted <= now:
            cutoff = admitted  # Also recover today's pre-admission segment, without double-counting live samples.
    except (TypeError, ValueError, OverflowError):
        pass
    bars = sorted((dict(row) for row in bars if isinstance(row, dict)), key=lambda row: float(row.get("time", 0)))
    prior = [row for row in bars if float(row.get("time", 0)) < start.timestamp()
             and bool(row.get("closed", True))]
    warmup = max(params.buy_ema_slow, params.rsi_period + 1)
    if len(prior) < warmup:
        data = client.get_ohlc(symbol, "1D", int((start - timedelta(days=max(400, warmup * 2))).timestamp()),
                               int(cutoff.timestamp()) - 1)
        bars = normalize_ohlc(data) if isinstance(data, dict) and data.get("s", "ok") == "ok" else []
        prior = [row for row in bars if row["time"] < start.timestamp()]
    if len(prior) < warmup:
        return {"ready": False, "issue": "DAILY_HISTORY"}
    args = dict(pair=f"{params.buy_ema_fast}/{params.buy_ema_slow}", mode="SESSIONS",
                sessions=params.buy_ema_cross_sessions, exchange=exchange, holidays=holidays)
    initial = indicator_snapshot(prior, params.buy_ema_fast, params.buy_ema_slow, params.rsi_period)
    row = advance_memory({}, initial, now=start - timedelta(microseconds=1), **args)
    if not row.get("current_ready"):
        return {"ready": False, "issue": "DAILY_HISTORY"}
    if signal_mode == "CLOSED":
        observations = []
        for index, bar in enumerate(bars):
            at = datetime.fromtimestamp(float(bar["time"]), VN_TZ)
            closed = at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=exchange_close_minute(exchange))
            if (start.timestamp() <= closed.timestamp() < cutoff.timestamp()
                    and bool(bar.get("closed", True)) and is_working_day(at.date(), holidays=holidays)):
                marks = indicator_snapshot(bars[:index + 1], params.buy_ema_fast, params.buy_ema_slow, params.rsi_period)
                observations.append((closed, marks))
    else:
        service = SessionCrossService(SessionPriceCache(cache_path, client), None)
        points, ready = service._samples(symbol, start.timestamp(), cutoff, exchange, None, holidays, checked_at=now)
        if not ready:
            return {"ready": False, "issue": "MINUTE_HISTORY"}
        observations = []
        for stamp, price in points:
            at = datetime.fromtimestamp(stamp, VN_TZ)
            if stamp >= cutoff.timestamp():
                continue
            marks, _ = service._marks(bars, price, at.date(), params)
            if marks is None:
                return {"ready": False, "issue": "DAILY_HISTORY"}
            observations.append((at, marks))
    if cutoff > today and signal_mode == "REALTIME" and all(baseline.get(key) is not None for key in ("buy_ema_fast", "buy_ema_slow")):
        observations.append((cutoff, baseline))
    for at, marks in observations:
        row = advance_memory(row, marks, now=at, **args)
        if not row.get("current_ready"):
            return {"ready": False, "issue": "DAILY_HISTORY"}
    cross = {key: row[key] for key in ("cross_id", "cross_at", "cross_timestamp", "previous_fast", "previous_slow", "used") if key in row}
    if cross:
        cross["cross_id"] = f"EMA:PREVIEW:{args['pair']}:{cross['cross_timestamp']}"
    return {"ready": True, "start": start.date().isoformat(), "end": (cutoff - timedelta(microseconds=1)).date().isoformat(),
            "up": row.get("up_count", 0), "down": row.get("down_count", 0), "cross": cross,
            "last_down_at": row.get("last_down_at", "")}


def with_history(source: dict, result: dict) -> dict:
    """Keep live identity and consumption; allow historical evidence only in the view."""
    row = dict(source)
    if not result.get("ready"):
        return row
    row.update(lookback_ready=True, ema_history_preview=result)
    candidate = result.get("cross") or {}
    saved = (row if row.get("cross_source") in {"HISTORY", "VA_HISTORY"} else row.get("lookback_cross")) or {}
    if candidate and saved.get("used") and saved.get("cross_at") == candidate.get("cross_at"):
        candidate = {**candidate, "used": True}
    live = row.get("cross_id") and row.get("cross_source") not in {"HISTORY", "VA_HISTORY"}
    if not live:
        from ..rules.ema_memory import _CROSS_KEYS
        for key in _CROSS_KEYS:
            row.pop(key, None)
        row.pop("cross_source", None)
        row["lookback_cross"] = dict(candidate)
    return row


def view_counts(evidence, *, now, prior_sessions, holidays):
    from ..rules.ema_memory import memory_day_counts, memory_window_counts
    if not prior_sessions and evidence.get("mode", "VA") == "VA":
        # VA retains its observed crosses across sessions. A daily counter
        # must not replace the evidence still used by the entry prerequisite.
        started = watch_started(evidence, now=now)
        return {"up": int(evidence.get("up_count", 0)), "down": int(evidence.get("down_count", 0)),
                "start": started.date().isoformat() if started else "", "end": now.date().isoformat(),
                "from": evidence.get("started_at", ""), "partial": False, "retained": True}
    history = evidence.get("ema_history_preview") or {}
    if prior_sessions and history.get("ready"):
        live = memory_day_counts(evidence, now=now)
        return {**live, "start": history["start"], "end": now.date().isoformat(),
                "up": live["up"] + history["up"] if live["up"] is not None else None,
                "down": live["down"] + history["down"] if live["down"] is not None else None}
    return memory_window_counts(evidence, now=now, prior_sessions=prior_sessions, holidays=holidays)


def latest_cross(evidence: dict, *, now: datetime) -> dict:
    """Latest known up/down timestamp for display, separate from the retained BUY ID."""
    events = []
    def add(value, direction):
        try:
            at = datetime.fromisoformat(str(value))
            at = at.replace(tzinfo=VN_TZ) if at.tzinfo is None else at.astimezone(VN_TZ)
            if at <= now:
                events.append({"at": at.isoformat(), "timestamp": at.timestamp(), "direction": direction})
        except (TypeError, ValueError, OverflowError):
            pass
    historical = evidence.get("mode") == "SESSIONS" or evidence.get("va_lookback_enabled")
    if historical or evidence.get("cross_source") not in {"HISTORY", "VA_HISTORY"}:
        add(evidence.get("cross_at"), "UP")
    add(evidence.get("last_down_at"), "DOWN")
    if historical:
        if evidence.get("lookback_ready") is True:
            add((evidence.get("lookback_cross") or {}).get("cross_at"), "UP")
            add(evidence.get("lookback_last_down_at"), "DOWN")
        history = evidence.get("ema_history_preview") or {}
        if history.get("ready"):
            add((history.get("cross") or {}).get("cross_at"), "UP")
            add(history.get("last_down_at"), "DOWN")
    return max(events, key=lambda event: event["timestamp"], default={})


def watch_started(evidence: dict, *, now: datetime) -> datetime | None:
    for value in (evidence.get("started_at"), (evidence.get("baseline") or {}).get("at")):
        try:
            at = datetime.fromisoformat(str(value))
            at = at.replace(tzinfo=VN_TZ) if at.tzinfo is None else at.astimezone(VN_TZ)
            if at <= now:
                return at
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def preview_info(evidence: dict, *, now: datetime, mode: str, sessions: int,
                 holidays: list[str], history_status: dict | None = None) -> tuple[str, str]:
    """One display line: latest cross, VA admission, or the selected history range."""
    history_status = history_status or {}
    if history_status.get("pending"):
        return "ĐANG TẢI", "muted"
    if history_status and not history_status.get("ready"):
        return "CHƯA TẢI ĐƯỢC", "wait"
    event = latest_cross({**evidence, "mode": mode}, now=now)
    if event:
        at = datetime.fromisoformat(event["at"])
        up = event["direction"] == "UP"
        return f"{'LÊN' if up else 'XUỐNG'} {at:%d/%m %H:%M}", "ok" if up else "down"
    if mode == "VA":
        admitted = watch_started(evidence, now=now)
        return (f"THEO DÕI {admitted:%d/%m %H:%M}" if admitted else ""), "muted"
    start = lookback_start(now, sessions, holidays=holidays)
    end = lookback_start(now, 1, holidays=holidays)
    if start is None or end is None:
        return "", "muted"
    history = evidence.get("ema_history_preview") or {}
    if history.get("ready"):
        try:
            start = datetime.fromisoformat(history["start"])
            end = datetime.fromisoformat(history["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    span = (f"{start:%d/%m}" if start.date() == end.date() else
            f"{start:%d}–{end:%d/%m}" if (start.year, start.month) == (end.year, end.month) else
            f"{start:%d/%m}–{end:%d/%m}")
    return f"QUÉT {span}", "muted"
