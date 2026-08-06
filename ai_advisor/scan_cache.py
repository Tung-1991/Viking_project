# -*- coding: utf-8 -*-
"""Kho RAW DATA tổng hợp dữ liệu quét theo danh sách mã động và theo ngày.

Chu kỳ 15 phút chỉ cập nhật bản ghi của ngày hiện tại. CHECK là nhánh báo cáo
độc lập: không sửa context, tín hiệu hoặc dữ liệu mà bộ đặt lệnh sử dụng.
"""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime

import pandas as pd

import config
from ai_advisor import paths

logger = logging.getLogger("BotDaemon")

SCHEMA_VERSION = 3
SIGNAL_DEDUP_MINUTES = 30
WEEK_BARS = 5
_SESSION_WINDOWS = ((9 * 60, 11 * 60 + 30), (13 * 60, 14 * 60 + 45))
_MORNING_CLOSE_MINUTE = 11 * 60 + 30
_MARKET_CLOSE_MINUTE = 14 * 60 + 45
_COVERAGE_GRACE_MINUTES = 5
_IO_LOCK = threading.RLock()
LIQUIDITY_SESSIONS = 60
_LIQUIDITY_LOOKUP_LOCK = threading.RLock()
_LIQUIDITY_LOOKUP_CACHE = {
    "path": "",
    "mtime_ns": -1,
    "values": {},
}


def _f(value, nd=4):
    try:
        value = float(value)
        if pd.isna(value):
            return None
        return round(value, nd)
    except Exception:
        return None


def session_elapsed_fraction(now):
    minute = now.hour * 60 + now.minute
    total = sum(end - start for start, end in _SESSION_WINDOWS)
    elapsed = 0
    for start, end in _SESSION_WINDOWS:
        if minute >= end:
            elapsed += end - start
        elif minute > start:
            elapsed += minute - start
    return round(elapsed / total, 4) if total else 1.0


def _parse_hhmm(value):
    try:
        hour, minute = str(value or "").strip().split(":", 1)
        total = int(hour) * 60 + int(minute)
        return total if 0 <= total < 24 * 60 else None
    except (TypeError, ValueError):
        return None


def _coverage_cutoff(close_minute, interval_minutes=None):
    """Mốc mẫu cuối hợp lệ theo chính chu kỳ RAW mà người dùng đã chọn."""
    try:
        interval = float(
            interval_minutes
            if interval_minutes is not None
            else getattr(config, "SCAN_SNAPSHOT_INTERVAL_MINUTES", 15)
        )
    except (TypeError, ValueError):
        interval = 15.0
    interval = max(1.0, min(120.0, interval))
    # Với chu kỳ 15 phút, mẫu 14:25 trở đi đủ đại diện lượt quét cuối phiên.
    return max(13 * 60, int(close_minute - interval - _COVERAGE_GRACE_MINUTES))


def has_session_coverage(entry, session="afternoon", interval_minutes=None):
    """Không coi dữ liệu buổi sáng là dữ liệu cuối ngày chỉ vì app mở sau 14:45."""
    last_scan = _parse_hhmm((entry or {}).get("last_scan"))
    if last_scan is None:
        return False
    close_minute = (
        _MORNING_CLOSE_MINUTE
        if str(session or "").strip().lower() == "morning"
        else _MARKET_CLOSE_MINUTE
    )
    return last_scan >= _coverage_cutoff(close_minute, interval_minutes)


def report_session_quality(cache, session, now=None, selected_symbols=None):
    """Đánh giá kho hiện tại có thực sự chứa lượt quét của phiên cần xuất hay không."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    selected = {
        str(symbol or "").strip().upper()
        for symbol in (selected_symbols or selected_research_symbols())
        if str(symbol or "").strip()
    }
    entries = []
    latest_days = set()
    for symbol, node in ((cache or {}).get("symbols", {}) or {}).items():
        if selected and str(symbol).upper() not in selected:
            continue
        days = (node or {}).get("days", {}) or {}
        if days:
            latest_days.add(sorted(days)[-1])
        entry = days.get(today)
        if entry:
            entries.append(entry)
    covered = sum(has_session_coverage(entry, session) for entry in entries)
    expected = len(selected) if selected else len(entries)
    return {
        "ready": bool(entries and covered),
        "today": today,
        "session": str(session or "").strip().lower(),
        "latest_day": max(latest_days) if latest_days else None,
        "current_entries": len(entries),
        "covered_entries": covered,
        "expected_symbols": expected,
    }


def pick_daily_df(dfs):
    for group in ("G0", "G1", "G2", "G3"):
        df = (dfs or {}).get(group)
        if df is None or df.empty or "time" not in df.columns or len(df) < 2:
            continue
        try:
            delta = (df["time"].iloc[-1] - df["time"].iloc[-2]).total_seconds()
            if delta >= 80000:
                return group, df
        except Exception:
            continue
    return None, None


def pick_intraday_df(dfs, now):
    """Chọn frame intraday nhỏ nhất có nến của hôm nay để dựng OHLC/volume ngày."""
    candidates = []
    required = {"time", "open", "high", "low", "close"}
    for order, group in enumerate(("G3", "G2", "G1", "G0")):
        df = (dfs or {}).get(group)
        if df is None or df.empty or not required.issubset(df.columns):
            continue
        try:
            full = df.copy()
            full["time"] = pd.to_datetime(full["time"])
            full = full.sort_values("time").drop_duplicates(subset=["time"], keep="last")
            work = full[full["time"].dt.date == now.date()]
            if work.empty:
                continue
            deltas = full["time"].diff().dt.total_seconds().dropna()
            interval = float(deltas[deltas > 0].median()) if (deltas > 0).any() else 86400.0
            if interval < 80000:
                candidates.append((interval, order, work))
        except Exception:
            continue
    return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def _is_ckps_symbol(symbol):
    symbol = str(symbol or "").strip().upper()
    configured = {
        str(item or "").strip().upper()
        for item in (getattr(config, "CKPS_SYMBOLS", []) or [])
    }
    return symbol.startswith("VN30F") or symbol in configured


def compute_liquidity_60d(
    daily_df,
    *,
    symbol=None,
    now=None,
    min_billion=10.0,
    sessions=LIQUIDITY_SESSIONS,
):
    """Ước tính GTGD bình quân từ OHLCV ngày; không phải phiếu BUY/SELL.

    DNSE biểu diễn giá CKCS theo nghìn VND và volume theo cổ phiếu. Endpoint
    OHLC không có turnover chính xác, vì vậy dùng typical price × volume.
    Chỉ lấy nến đã hoàn tất trước ngày hiện tại để tránh trộn volume dở dang.
    """
    sessions = max(1, int(sessions or LIQUIDITY_SESSIONS))
    threshold_vnd = max(0.0, float(min_billion or 0.0)) * 1_000_000_000.0
    base = {
        "sessions_required": sessions,
        "sessions_available": 0,
        "average_value_vnd": None,
        "average_value_billion": None,
        "threshold_billion": round(threshold_vnd / 1_000_000_000.0, 4),
        "status": "NOT_APPLICABLE" if _is_ckps_symbol(symbol) else "INSUFFICIENT",
        "method": "TYPICAL_PRICE_X_VOLUME",
        "estimated": True,
    }
    if _is_ckps_symbol(symbol):
        return base
    required = {"time", "high", "low", "close", "volume"}
    if daily_df is None or daily_df.empty or not required.issubset(daily_df.columns):
        return base
    try:
        now = now or datetime.now()
        work = daily_df.loc[:, list(required)].copy()
        work["time"] = pd.to_datetime(work["time"], errors="coerce")
        for column in ("high", "low", "close", "volume"):
            work[column] = pd.to_numeric(work[column], errors="coerce")
        work = work[
            work["time"].notna()
            & (work["time"].dt.date < now.date())
            & (work["high"] > 0)
            & (work["low"] > 0)
            & (work["close"] > 0)
            & (work["volume"] >= 0)
        ].sort_values("time").drop_duplicates(subset=["time"], keep="last")
        work = work.tail(sessions)
        base["sessions_available"] = int(len(work))
        if len(work) < sessions:
            return base
        typical_price_thousand = (
            work["high"] + work["low"] + work["close"]
        ) / 3.0
        values_vnd = typical_price_thousand * 1000.0 * work["volume"]
        average_vnd = float(values_vnd.mean())
        base["average_value_vnd"] = round(average_vnd, 0)
        base["average_value_billion"] = round(average_vnd / 1_000_000_000.0, 3)
        base["status"] = "PASS" if average_vnd >= threshold_vnd else "FAIL"
        return base
    except Exception as exc:
        base["status"] = "ERROR"
        base["error"] = str(exc)
        return base


def _compute_price_block(daily_df, current_price, now, intraday_df=None):
    empty = {
        "open": None, "high": None, "low": None, "close": None,
        "current": _f(current_price), "pct_1d": None, "pct_1w": None,
        "high_1w": None, "low_1w": None, "daily_bar_is_today": False,
    }
    if (daily_df is None or daily_df.empty) and (intraday_df is None or intraday_df.empty):
        return empty
    closes = daily_df["close"] if daily_df is not None and not daily_df.empty else pd.Series(dtype=float)
    try:
        last_bar_is_today = daily_df["time"].iloc[-1].date() == now.date()
    except Exception:
        last_bar_is_today = False
    prev_idx = -2 if last_bar_is_today and len(closes) >= 2 else -1
    previous_close = _f(closes.iloc[prev_idx]) if len(closes) else None
    intraday_close = _f(intraday_df.iloc[-1].get("close")) if intraday_df is not None and not intraday_df.empty else None
    reference = _f(current_price) or intraday_close or (_f(closes.iloc[-1]) if len(closes) else None)
    pct_1d = round((reference - previous_close) / previous_close * 100, 2) if reference is not None and previous_close else None
    week_idx = prev_idx - WEEK_BARS + 1
    week_close = _f(closes.iloc[week_idx]) if len(closes) >= abs(week_idx) else None
    pct_1w = round((reference - week_close) / week_close * 100, 2) if reference is not None and week_close else None
    tail = daily_df.tail(WEEK_BARS) if daily_df is not None and not daily_df.empty else None
    if intraday_df is not None and not intraday_df.empty:
        open_value = _f(intraday_df.iloc[0].get("open"))
        high_value = _f(intraday_df["high"].max())
        low_value = _f(intraday_df["low"].min())
        close_value = reference or intraday_close
        last_bar_is_today = True
    else:
        last = daily_df.iloc[-1]
        open_value = _f(last.get("open"))
        high_value = _f(last.get("high"))
        low_value = _f(last.get("low"))
        close_value = _f(last.get("close"))
    week_high = _f(tail["high"].max()) if tail is not None else None
    week_low = _f(tail["low"].min()) if tail is not None else None
    if high_value is not None:
        week_high = high_value if week_high is None else max(week_high, high_value)
    if low_value is not None:
        week_low = low_value if week_low is None else min(week_low, low_value)
    return {
        "open": open_value, "high": high_value,
        "low": low_value, "close": close_value,
        "current": reference, "pct_1d": pct_1d, "pct_1w": pct_1w,
        "high_1w": week_high, "low_1w": week_low,
        "daily_bar_is_today": bool(last_bar_is_today),
    }


def _compute_volume_block(daily_df, market_open, now, intraday_df=None):
    has_daily_volume = daily_df is not None and not daily_df.empty and "volume" in daily_df.columns
    has_intraday_volume = intraday_df is not None and not intraday_df.empty and "volume" in intraday_df.columns
    if not has_daily_volume and not has_intraday_volume:
        return {"today": None, "avg20": None, "ratio": None, "projected_ratio": None,
                "trend_5d": None, "is_partial_bar": bool(market_open)}
    volumes = daily_df["volume"] if has_daily_volume else pd.Series(dtype=float)
    try:
        last_bar_is_today = daily_df["time"].iloc[-1].date() == now.date()
    except Exception:
        last_bar_is_today = False
    today_volume = _f(intraday_df["volume"].fillna(0).sum(), 0) if has_intraday_volume else (
        _f(volumes.iloc[-1], 0) if last_bar_is_today else None
    )
    closed = volumes.iloc[:-1] if last_bar_is_today else volumes
    avg20 = _f(closed.tail(20).mean(), 0) if len(closed) else None
    ratio = round(today_volume / avg20, 2) if today_volume and avg20 else None
    partial = bool(market_open and last_bar_is_today)
    projected = None
    if partial and today_volume and avg20:
        projected = round((today_volume / max(session_elapsed_fraction(now), 0.1)) / avg20, 2)
    trend = None
    if len(closed) >= 10:
        recent = closed.tail(WEEK_BARS).mean()
        older = closed.tail(WEEK_BARS * 2).head(WEEK_BARS).mean()
        if older and not pd.isna(older):
            change = (recent - older) / older
            trend = "tăng" if change > 0.1 else ("giảm" if change < -0.1 else "đi ngang")
    return {"today": today_volume, "avg20": avg20, "ratio": ratio,
            "projected_ratio": projected, "trend_5d": trend, "is_partial_bar": partial}


def compute_snapshot(dfs, context, signal, now=None, symbol=None, settings=None):
    """Tạo mẫu cập nhật. Indicator báo cáo do CHECK engine trả về động."""
    from signals import check_engine

    now = now or datetime.now()
    context = context or {}
    market_open = bool(context.get("market_open", True))
    daily_group, daily_df = pick_daily_df(dfs)
    intraday_df = pick_intraday_df(dfs, now)
    check = check_engine.evaluate(dfs, context, symbol=symbol, settings=settings)
    try:
        from ai_advisor import schedule_settings

        liquidity_settings = schedule_settings.load()
        min_billion = liquidity_settings.get("ckcs_liquidity_min_billion", 10.0)
        liquidity_sessions = liquidity_settings.get(
            "ckcs_liquidity_sessions", LIQUIDITY_SESSIONS
        )
    except Exception:
        min_billion = 10.0
        liquidity_sessions = LIQUIDITY_SESSIONS
    return {
        "price": _compute_price_block(daily_df, context.get("current_price"), now, intraday_df),
        "volume": _compute_volume_block(daily_df, market_open, now, intraday_df),
        "daily_group": daily_group,
        "liquidity_60d": compute_liquidity_60d(
            daily_df,
            symbol=symbol,
            now=now,
            min_billion=min_billion,
            sessions=liquidity_sessions,
        ),
        "bot": {
            "trend_G0": context.get("trend_G0"), "trend_G1": context.get("trend_G1"),
            "trend_G2": context.get("trend_G2"), "trend_G3": context.get("trend_G3"),
            "market_mode": context.get("market_mode"), "mode_source": context.get("mode_source"),
            "block_reason": context.get("block_reason"), "latest_signal": signal,
            "group_signals": context.get("group_signals"),
        },
        "check": check,
    }


def empty_cache():
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "symbols": {}}


def is_research_symbol(symbol):
    """RAW DATA nhận mọi mã thị trường mà người dùng đã chọn, kể cả VN30F."""
    return bool(str(symbol or "").strip())


def selected_research_symbols():
    """Danh sách động từ Advanced; thiếu setting thì mặc định toàn bộ watchlist."""
    fallback = getattr(
        config,
        "SCAN_SNAPSHOT_SYMBOLS",
        list(getattr(config, "CKPS_SYMBOLS", []) or [])
        + list(getattr(config, "CKCS_WATCHLIST", []) or []),
    )
    try:
        from core.storage_manager import load_brain_settings

        configured = load_brain_settings().get("SCAN_SNAPSHOT_SYMBOLS", fallback)
    except Exception:
        configured = fallback
    return list(dict.fromkeys(
        str(symbol).strip().upper()
        for symbol in (configured or [])
        if is_research_symbol(symbol)
    ))


def _normalize_day_statuses(cache, now=None):
    today = (now or datetime.now()).strftime("%Y-%m-%d")
    for node in (cache.get("symbols", {}) or {}).values():
        for day, entry in (node.get("days", {}) or {}).items():
            if entry.get("eod_final") or entry.get("day_status") == "EOD":
                # Sửa dữ liệu legacy từng bị đóng dấu EOD dù lượt quét cuối còn ở buổi sáng.
                if _parse_hhmm(entry.get("last_scan")) is not None and not has_session_coverage(entry):
                    entry["eod_final"] = False
                    entry["day_status"] = "INCOMPLETE"
                else:
                    entry["eod_final"] = True
                    entry["day_status"] = "EOD"
            elif day < today:
                entry["eod_final"] = False
                entry["day_status"] = "INCOMPLETE"
            else:
                entry["eod_final"] = False
                entry["day_status"] = "INTRADAY"
    return cache


def _remove_empty_check_segments(cache):
    for node in (cache.get("symbols", {}) or {}).values():
        for entry in (node.get("days", {}) or {}).values():
            entry["check_segments"] = [
                segment
                for segment in (entry.get("check_segments") or [])
                if any(
                    isinstance(modules, dict) and modules
                    for modules in (segment.get("groups") or {}).values()
                )
            ]
    return cache


def _research_cache(data):
    migrated = dict(data or empty_cache())
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["symbols"] = {
        str(symbol).upper(): node
        for symbol, node in ((data or {}).get("symbols", {}) or {}).items()
        if is_research_symbol(symbol)
    }
    return _remove_empty_check_segments(_normalize_day_statuses(migrated))


def _merge_legacy_cache(current, legacy):
    """Bổ sung phần lịch sử còn thiếu; dữ liệu kho mới luôn được ưu tiên."""
    current = _research_cache(current)
    legacy = _research_cache(legacy)
    for symbol, legacy_node in (legacy.get("symbols", {}) or {}).items():
        target_node = current.setdefault("symbols", {}).setdefault(symbol, {"days": {}})
        target_days = target_node.setdefault("days", {})
        for day, entry in (legacy_node.get("days", {}) or {}).items():
            target_days.setdefault(day, entry)
    current["legacy_migration_version"] = 2
    return _normalize_day_statuses(current)


def _day_entry(cache, symbol, day):
    node = cache.setdefault("symbols", {}).setdefault(symbol, {"days": {}})
    return node["days"].setdefault(day, {
        "samples": 0, "first_scan": None, "last_scan": None,
        "day_status": "INTRADAY", "eod_final": False,
        "signals": [], "bot_signal_counts": {}, "check_segments": [],
    })


def _merge_price_block(current, incoming):
    current, incoming = dict(current or {}), dict(incoming or {})
    result = dict(incoming)
    result["open"] = current.get("open") if current.get("open") is not None else incoming.get("open")
    highs = [value for value in (current.get("high"), incoming.get("high")) if value is not None]
    lows = [value for value in (current.get("low"), incoming.get("low")) if value is not None]
    result["high"] = max(highs) if highs else None
    result["low"] = min(lows) if lows else None
    for key in ("close", "current", "pct_1d", "pct_1w", "high_1w", "low_1w", "daily_bar_is_today"):
        if result.get(key) is None and key in current:
            result[key] = current.get(key)
    return result


def _merge_volume_block(current, incoming):
    current, incoming = dict(current or {}), dict(incoming or {})
    result = dict(incoming)
    today_values = [value for value in (current.get("today"), incoming.get("today")) if value is not None]
    result["today"] = max(today_values) if today_values else None
    for key in ("avg20", "ratio", "projected_ratio", "trend_5d", "is_partial_bar"):
        if result.get(key) is None and key in current:
            result[key] = current.get(key)
    if result.get("today") is not None and result.get("avg20"):
        result["ratio"] = round(float(result["today"]) / float(result["avg20"]), 2)
    return result


def _aggregate_metric(current, value, hhmm):
    if isinstance(value, bool):
        numeric = False
    else:
        numeric = isinstance(value, (int, float)) and not pd.isna(value)
    if numeric:
        value = float(value)
        if not current or current.get("kind") != "number":
            return {"kind": "number", "first": value, "min": value, "max": value,
                    "sum": value, "count": 1, "avg": value, "last": value}
        current["min"] = min(current["min"], value)
        current["max"] = max(current["max"], value)
        current["sum"] += value
        current["count"] += 1
        current["avg"] = round(current["sum"] / current["count"], 6)
        current["last"] = value
        return current
    if value is None:
        return current
    value = str(value)
    if not current or current.get("kind") != "state":
        return {"kind": "state", "first": value, "last": value, "changes": 0,
                "times": [{"time": hhmm, "value": value}]}
    if current.get("last") != value:
        current["changes"] += 1
        current["last"] = value
        current.setdefault("times", []).append({"time": hhmm, "value": value})
    return current


def _merge_check(entry, check, hhmm):
    if not isinstance(check, dict):
        return
    groups = {
        group: modules
        for group, modules in (check.get("groups") or {}).items()
        if isinstance(modules, dict) and modules
    }
    # CHECK là tùy chọn. Khi không bật module nào thì không tạo segment rỗng,
    # tránh làm cache và báo cáo trông như có dữ liệu kỹ thuật nhưng thực tế không có.
    if not groups:
        return
    config_id = str(check.get("config_id") or "unknown")
    segments = entry.setdefault("check_segments", [])
    segment = segments[-1] if segments and segments[-1].get("config_id") == config_id else None
    if segment is None:
        segment = {"config_id": config_id, "first_scan": hhmm, "last_scan": hhmm,
                   "samples": 0, "groups": {}}
        segments.append(segment)
    segment["samples"] += 1
    segment["last_scan"] = hhmm
    for group, modules in groups.items():
        group_node = segment["groups"].setdefault(group, {})
        for name, result in (modules or {}).items():
            module = group_node.setdefault(name, {
                "params": result.get("params", {}), "signal_counts": {},
                "latest_signal": 0, "metrics": {},
            })
            signal = int(result.get("signal") or 0)
            label = "BUY" if signal > 0 else ("SELL" if signal < 0 else "WAIT")
            module["signal_counts"][label] = module["signal_counts"].get(label, 0) + 1
            module["latest_signal"] = signal
            if result.get("error"):
                module["error"] = str(result["error"])
            for key, value in (result.get("metrics") or {}).items():
                module["metrics"][key] = _aggregate_metric(module["metrics"].get(key), value, hhmm)


def merge_sample(cache, sym, snapshot, now=None, eod=False):
    now = now or datetime.now()
    day, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    cache["schema_version"] = SCHEMA_VERSION
    entry = _day_entry(cache, sym, day)
    entry["price"] = _merge_price_block(entry.get("price"), snapshot.get("price"))
    entry["volume"] = _merge_volume_block(entry.get("volume"), snapshot.get("volume"))
    for key in ("bot", "daily_group", "liquidity_60d"):
        entry[key] = snapshot.get(key)
    latest_signal = (snapshot.get("bot") or {}).get("latest_signal")
    label = "BUY" if latest_signal == 1 else ("SELL" if latest_signal == -1 else "WAIT")
    counts = entry.setdefault("bot_signal_counts", {})
    counts[label] = counts.get(label, 0) + 1
    _merge_check(entry, snapshot.get("check"), hhmm)
    entry["samples"] += 1
    entry["first_scan"] = entry.get("first_scan") or hhmm
    entry["last_scan"] = hhmm
    if eod:
        entry["eod_final"] = True
        entry["day_status"] = "EOD"
    elif not entry.get("eod_final"):
        entry["day_status"] = "INTRADAY"
    cache["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    return entry


def record_signal_event(cache, sym, side, context, now=None):
    now = now or datetime.now()
    day, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    entry = _day_entry(cache, sym, day)
    for event in reversed(entry["signals"]):
        if event.get("side") == side:
            try:
                previous = datetime.strptime(f"{day} {event['time']}", "%Y-%m-%d %H:%M")
                if (now - previous).total_seconds() < SIGNAL_DEDUP_MINUTES * 60:
                    return False
            except Exception:
                pass
            break
    entry["signals"].append({"time": hhmm, "side": side,
                             "mode": context.get("market_mode"),
                             "groups": context.get("group_signals"),
                             "note": context.get("block_reason")})
    cache["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    return True


def prune(cache, retention_days=None):
    keep = max(1, int(retention_days or getattr(config, "SCAN_SNAPSHOT_RETENTION_DAYS", 250)))
    for symbol in list(cache.get("symbols", {})):
        days = cache["symbols"][symbol].get("days", {})
        for day in sorted(days)[:-keep] if len(days) > keep else []:
            days.pop(day, None)
        if not days:
            cache["symbols"].pop(symbol, None)
    return cache


def derive_weekly(sym_node):
    buy = sell = 0
    for day_data in sym_node.get("days", {}).values():
        for event in day_data.get("signals", []):
            buy += event.get("side") == "BUY"
            sell += event.get("side") == "SELL"
    return {"buy": int(buy), "sell": int(sell)}


def load_cache(path=None):
    path = path or paths.scan_cache_path()
    default_path = os.path.abspath(paths.scan_cache_path())
    # Migration một lần: sao chép phần CKCS từ cache cũ, giữ nguyên file cũ làm dự phòng.
    if os.path.abspath(path) == default_path and not os.path.exists(path):
        legacy_path = paths.legacy_scan_cache_path()
        if os.path.isfile(legacy_path):
            try:
                with _IO_LOCK:
                    with open(legacy_path, "r", encoding="utf-8") as handle:
                        legacy = json.load(handle)
                if isinstance(legacy, dict) and isinstance(legacy.get("symbols"), dict):
                    migrated = _merge_legacy_cache(empty_cache(), legacy)
                    if save_cache(migrated, path):
                        logger.info(
                            "scan_cache: migrated %s selected symbols to %s",
                            len(migrated.get("symbols", {})),
                            path,
                        )
                        return migrated
            except Exception as exc:
                logger.warning("scan_cache: migration failed (%s)", exc)
    try:
        with _IO_LOCK:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict) and isinstance(data.get("symbols"), dict):
                    data = _research_cache(data)
                    if (
                        os.path.abspath(path) == default_path
                        and int(data.get("legacy_migration_version", 0) or 0) < 2
                        and os.path.isfile(paths.legacy_scan_cache_path())
                    ):
                        try:
                            with open(paths.legacy_scan_cache_path(), "r", encoding="utf-8") as handle:
                                legacy = json.load(handle)
                            data = _merge_legacy_cache(data, legacy)
                            save_cache(data, path)
                        except Exception as exc:
                            logger.warning("scan_cache: legacy merge failed (%s)", exc)
                    return data
    except Exception as exc:
        logger.warning("scan_cache: file hỏng, khởi tạo lại (%s)", exc)
    return empty_cache()


def save_cache(cache, path=None):
    path = path or paths.scan_cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _IO_LOCK:
        for attempt in range(5):
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(cache, handle, indent=2, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
                return True
            except (PermissionError, OSError) as exc:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
                if attempt == 4:
                    logger.error("scan_cache: lỗi ghi sau 5 lần thử: %s", exc)
                time.sleep(0.05)
    return False


def _latest_liquidity_lookup():
    """Đọc cache một lần theo mtime để Telegram không parse file cho từng mã."""
    path = os.path.abspath(paths.scan_cache_path())
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    with _LIQUIDITY_LOOKUP_LOCK:
        if (
            _LIQUIDITY_LOOKUP_CACHE["path"] == path
            and _LIQUIDITY_LOOKUP_CACHE["mtime_ns"] == mtime_ns
        ):
            return dict(_LIQUIDITY_LOOKUP_CACHE["values"])
        cache = load_cache(path)
        values = {}
        for symbol, node in (cache.get("symbols", {}) or {}).items():
            days = node.get("days", {}) or {}
            if not days:
                continue
            latest = days[sorted(days)[-1]]
            values[str(symbol).upper()] = dict(latest.get("liquidity_60d") or {})
        _LIQUIDITY_LOOKUP_CACHE.update({
            "path": path,
            "mtime_ns": mtime_ns,
            "values": values,
        })
        return dict(values)


def liquidity_filter_allows(symbol):
    """Rule chỉ dành cho báo cáo/gợi ý CKCS; không được gọi từ logic đặt lệnh."""
    symbol = str(symbol or "").strip().upper()
    if _is_ckps_symbol(symbol):
        return True
    try:
        from ai_advisor import schedule_settings

        settings = schedule_settings.load()
    except Exception:
        return True
    if not settings.get("ckcs_liquidity_filter_enabled", False):
        return True
    result = _latest_liquidity_lookup().get(symbol) or {}
    return str(result.get("status") or "").upper() == "PASS"


class ScanSnapshotRecorder:
    def __init__(self):
        self._cache = None
        self._cache_path = None
        self._dirty = False
        self._last_sample_ts = {}

    def _ensure_loaded(self):
        current_path = paths.scan_cache_path()
        if self._cache is None or self._cache_path != current_path:
            self._cache = load_cache(current_path)
            self._cache_path = current_path
            self._dirty = False
            self._last_sample_ts = {}

    def maybe_record(self, sym, dfs, context, signal, now=None):
        now = now or datetime.now()
        sym = str(sym or "").upper()
        if not is_research_symbol(sym):
            return False
        self._ensure_loaded()
        # 14:45 trở đi chỉ khóa bản ghi đã có; tuyệt đối không tạo thêm mẫu mới.
        if now.hour * 60 + now.minute >= _MARKET_CLOSE_MINUTE:
            self.finalize_closed_day([sym], now=now)
            return self._dirty
        market_open = bool(context.get("market_open", True))
        if market_open and signal in (1, -1):
            side = "BUY" if signal == 1 else "SELL"
            if record_signal_event(self._cache, sym, side, context, now=now):
                self._dirty = True
        interval = float(getattr(config, "SCAN_SNAPSHOT_INTERVAL_MINUTES", 15)) * 60
        due = (time.time() - self._last_sample_ts.get(sym, 0.0)) >= interval
        day = now.strftime("%Y-%m-%d")
        entry = self._cache.get("symbols", {}).get(sym, {}).get("days", {}).get(day)
        if market_open and due:
            merge_sample(self._cache, sym, compute_snapshot(dfs, context, signal, now, sym), now)
            self._last_sample_ts[sym] = time.time()
            self._dirty = True
        return self._dirty

    def flush(self):
        if self._cache is not None and self._dirty:
            prune(self._cache)
            if save_cache(self._cache, self._cache_path):
                self._dirty = False

    def finalize_closed_day(self, symbols=None, now=None):
        """Khóa bản ghi hôm nay sau 14:45 nhưng không nâng dữ liệu buổi sáng thành EOD."""
        now = now or datetime.now()
        if now.hour * 60 + now.minute < _MARKET_CLOSE_MINUTE:
            return False
        self._ensure_loaded()
        day = now.strftime("%Y-%m-%d")
        allowed = {str(symbol).upper() for symbol in (symbols or []) if is_research_symbol(symbol)}
        changed = False
        for symbol, node in self._cache.get("symbols", {}).items():
            if allowed and str(symbol).upper() not in allowed:
                continue
            entry = node.get("days", {}).get(day)
            if entry and not entry.get("eod_final"):
                complete = has_session_coverage(entry, "afternoon")
                next_status = "EOD" if complete else "INCOMPLETE"
                if (
                    bool(entry.get("eod_final")) != complete
                    or entry.get("day_status") != next_status
                ):
                    entry["eod_final"] = complete
                    entry["day_status"] = next_status
                    changed = True
            for old_day, old_entry in (node.get("days", {}) or {}).items():
                if old_day < day and not old_entry.get("eod_final") and old_entry.get("day_status") != "INCOMPLETE":
                    old_entry["day_status"] = "INCOMPLETE"
                    changed = True
        if changed:
            self._cache["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            self._dirty = True
        return changed

    def status(self):
        self._ensure_loaded()
        symbols = self._cache.get("symbols", {})
        today = datetime.now().strftime("%Y-%m-%d")
        today_entries = [node.get("days", {}).get(today) for node in symbols.values()]
        today_entries = [entry for entry in today_entries if entry]
        statuses = {entry.get("day_status", "INTRADAY") for entry in today_entries}
        today_status = next(iter(statuses)) if len(statuses) == 1 else ("MIXED" if statuses else "NO_DATA")
        all_days = {
            day
            for node in symbols.values()
            for day in (node.get("days", {}) or {})
        }
        return {
            "updated_at": self._cache.get("updated_at"),
            "symbols": len(symbols),
            "days": len(all_days),
            "latest_day": max(all_days) if all_days else None,
            "today_status": today_status,
            "today_samples": sum(int(entry.get("samples", 0) or 0) for entry in today_entries),
            "selected_symbols": len(selected_research_symbols()),
        }


recorder = ScanSnapshotRecorder()
