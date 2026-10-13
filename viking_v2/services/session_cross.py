"""Recover EMA crossings after watchlist admission from completed minute prices."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path

from ..rules.business import ema
from ..storage import AtomicJSONStore
from ..trading.market import VN_TZ, exchange_sessions
from ..trading.validation import MAX_DECISION_AGE


def merge_ranges(ranges):
    merged = []
    for left, right in sorted(ranges):
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(right, merged[-1][1])
        else:
            merged.append([left, right])
    return merged


def missing_ranges(targets, covered):
    missing = []
    for start, end in targets:
        cursor = start
        for left, right in merge_ranges(covered):
            if right <= cursor or left >= end:
                continue
            if left > cursor:
                missing.append([cursor, min(left, end)])
            cursor = max(cursor, right)
        if cursor < end:
            missing.append([cursor, end])
    return missing


class SessionPriceCache:
    """Prices are shared by both books; mode switches never refetch history."""

    def __init__(self, path: str | Path, client):
        self.store = AtomicJSONStore(path, default={"symbols": {}})
        raw = self.store.read()
        self.rows = raw.get("symbols", {}) if isinstance(raw, dict) else {}
        self.client = client
        self._saved_at = {}

    def observe(self, symbol, price, now, exchange, *, recover=False):
        symbol = str(symbol).upper()
        now = now.astimezone(VN_TZ)
        stamp, day = now.timestamp(), now.date().isoformat()
        bucket = int(stamp) // 60 * 60
        midnight = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        targets = [[midnight + start * 60, min(bucket, midnight + end * 60)]
                   for start, end, _ in exchange_sessions(exchange)
                   if midnight + start * 60 < bucket]
        targets = merge_ranges(targets)
        row = self.rows.get(symbol) or {}
        if row.get("day") != day:
            row = {"day": day, "prices": {}, "ranges": [], "retry_at": 0.0, "revision": 0}
        prices = row["prices"]
        if not isfinite(float(price)) or float(price) <= 0:
            return {**row, "ready": False, "cutoff": bucket, "midnight": midnight}
        prior = row.get("pending") or {}
        if prior and bucket < prior["bucket"]:
            return {**row, "ready": False, "cutoff": bucket, "midnight": midnight}
        revision_before = row["revision"]
        if prior and bucket > prior["bucket"]:
            if bucket == prior["bucket"] + 60 and 0 <= stamp - prior["seen_at"] <= MAX_DECISION_AGE:
                prices[str(prior["bucket"])] = prior["price"]
                row["ranges"] = merge_ranges([*row["ranges"], [prior["bucket"], bucket]])
                row["revision"] += 1
        if not prior or bucket >= prior["bucket"]:
            row["pending"] = {"bucket": bucket, "price": float(price), "seen_at": stamp}
        gaps = missing_ranges(targets, row["ranges"])
        if recover and gaps and stamp >= row.get("retry_at", 0):
            for left, right in gaps:
                try:
                    data = self.client.get_ohlc(symbol, "1", left, right - 1)
                    keys = ("t", "o", "h", "l", "c", "v")
                    if isinstance(data, dict) and data.get("s") == "no_data":
                        data = {"s": "no_data", **{key: data.get(key, []) for key in keys}}
                    if (not isinstance(data, dict) or any(not isinstance(data.get(key), list) for key in keys)
                            or any(len(data[key]) != len(data["t"]) for key in keys)
                            or str(data.get("s", "ok")).lower() not in {"ok", "no_data"}):
                        raise ValueError("MINUTE_HISTORY_UNAVAILABLE")
                    fetched = {}
                    for raw_time, raw_price in zip(data["t"], data["c"]):
                        point, close = int(float(raw_time)), float(raw_price)
                        if not isfinite(close) or close <= 0 or point % 60:
                            raise ValueError("MINUTE_HISTORY_INVALID")
                        if left <= point < right:
                            fetched[str(point)] = close
                    prices.update(fetched)
                    row["ranges"] = merge_ranges([*row["ranges"], [left, right]])
                    row["revision"] += 1
                    row["retry_at"] = 0.0
                except Exception:
                    row["retry_at"] = stamp + 60.0
                    break
        self.rows[symbol] = row
        if (not prior or bucket != prior["bucket"] or revision_before != row["revision"]
                or stamp - self._saved_at.get(symbol, 0) >= 10):
            self.store.write({**self.store.read(), "symbols": self.rows})
            self._saved_at[symbol] = stamp
        return {**row, "ready": not missing_ranges(targets, row["ranges"]),
                "cutoff": bucket, "midnight": midnight}


class SessionCrossService:
    """Durable memory from admission; closed minutes recover only unseen gaps."""

    def __init__(self, cache, rule_state):
        self.cache, self.rule_state = cache, rule_state
        raw = cache.store.read()
        self.recovered = raw.get("cross_recovery", {}) if isinstance(raw, dict) else {}

    def observe_closed(self, symbol, stream, bars, params, now, exchange, *, working_dates=None, holidays=None):
        """CLOSED counts only completed daily observations after admission."""
        from ..rules.business import indicator_snapshot
        from ..rules.ema_memory import advance_memory, refresh_memory
        from ..trading.market import exchange_close_minute
        row = self.rule_state.session_cross(symbol, stream)
        if row.get("observation_mode") not in {None, "CLOSED"}:
            row["profile"] = "RESET:MODE"
        args = dict(pair=f"{params.buy_ema_fast}/{params.buy_ema_slow}", mode=params.buy_ema_cross_mode,
                    sessions=params.buy_ema_cross_sessions, exchange=exchange,
                    working_dates=working_dates, holidays=holidays)
        completed = [bar for bar in bars if bool(bar.get("closed", True))]
        def marks(values):
            return indicator_snapshot(values, params.buy_ema_fast, params.buy_ema_slow, params.rsi_period)
        if not row.get("initialized") or row.get("profile") not in {None, "", args["pair"]}:
            row = advance_memory(row, marks(completed), now=now, **args)
        else:
            for index, bar in enumerate(completed):
                day = datetime.fromtimestamp(float(bar["time"]), VN_TZ).date()
                closed_at = datetime.combine(day, datetime.min.time(), VN_TZ) + timedelta(minutes=exchange_close_minute(exchange))
                if float(row.get("observed_at_epoch", 0)) < closed_at.timestamp() <= now.timestamp():
                    row = advance_memory(row, marks(completed[:index+1]), now=closed_at, **args)
        row = refresh_memory(row, now=now, **{key: value for key,value in args.items() if key != "pair"})
        row["exchange"] = exchange
        row["observation_mode"] = "CLOSED"
        return self.rule_state.save_session_cross(symbol, stream, row)

    def _samples(self, symbol, start, now, exchange, working_dates, holidays):
        from ..trading.market import is_working_day
        row = self.recovered.setdefault(symbol, {"ranges": [], "prices": {}, "retry_at": 0})
        targets = []
        day = datetime.fromtimestamp(start, VN_TZ).date()
        while day <= now.date():
            if is_working_day(day, working_dates, holidays):
                midnight = datetime.combine(day, datetime.min.time(), VN_TZ).timestamp()
                for left, right, _ in exchange_sessions(exchange):
                    lo = max(int(start) // 60 * 60, int(midnight + left * 60))
                    hi = min(int(now.timestamp()) // 60 * 60, int(midnight + right * 60))
                    if hi > lo:
                        targets.append([lo, hi])
            day += timedelta(days=1)
        gaps = missing_ranges(targets, row["ranges"])
        if gaps and now.timestamp() < row.get("retry_at", 0):
            return [], False
        for left, right in gaps:
            try:
                data = self.cache.client.get_ohlc(symbol, "1", left, right - 1)
                if not isinstance(data, dict) or data.get("s", "ok") not in {"ok", "no_data"}:
                    raise ValueError("MINUTE_HISTORY_UNAVAILABLE")
                keys = ("t", "o", "h", "l", "c", "v")
                if any(not isinstance(data.get(key, []), list) for key in keys):
                    raise ValueError("MINUTE_HISTORY_UNAVAILABLE")
                if any(len(data.get(key, [])) != len(data.get("t", [])) for key in keys):
                    raise ValueError("MINUTE_HISTORY_INCOMPLETE")
                for stamp, price in zip(data.get("t", []), data.get("c", [])):
                    stamp, price = int(float(stamp)), float(price)
                    if stamp % 60 or not isfinite(price) or price <= 0:
                        raise ValueError("MINUTE_HISTORY_INVALID")
                    if left <= stamp < right:
                        row["prices"][str(stamp)] = price
                row["ranges"] = merge_ranges([*row["ranges"], [left, right]])
                row["retry_at"] = 0
            except Exception:
                row["retry_at"] = now.timestamp() + 60
                raw = self.cache.store.read()
                self.cache.store.write({**raw, "cross_recovery": self.recovered})
                return [], False
        if gaps:
            raw = self.cache.store.read()
            self.cache.store.write({**raw, "cross_recovery": self.recovered})
        return sorted((int(stamp) + 60, price) for stamp, price in row["prices"].items()
                      if start < int(stamp) + 60 <= now.timestamp()), True

    @staticmethod
    def _marks(bars, price, day, params):
        history = []
        for bar in bars:
            try:
                if datetime.fromtimestamp(float(bar["time"]), VN_TZ).date() < day:
                    value = float(bar["close"])
                    if isfinite(value) and value > 0:
                        history.append((float(bar["time"]), value))
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                continue
        values = [price for _, price in sorted(dict(history).items())]
        if len(values) < params.buy_ema_slow:
            return None, None
        fast = ema(values, params.buy_ema_fast)[-1]
        slow = ema(values, params.buy_ema_slow)[-1]
        a, b = 2 / (params.buy_ema_fast + 1), 2 / (params.buy_ema_slow + 1)
        threshold = (slow * (1 - b) - fast * (1 - a)) / (a - b)
        return {"buy_ema_fast": fast + a * (price - fast),
                "buy_ema_slow": slow + b * (price - slow)}, threshold

    def observe(self, symbol, stream, bars, indicators, params, now, exchange, *,
                recover=True, working_dates=None, holidays=None):
        from ..rules.ema_memory import advance_memory
        now = now.astimezone(VN_TZ)
        row = self.rule_state.session_cross(symbol, stream)
        if row.get("tracking_version") != 2:
            row = {}
        if row.get("watch_active") is False:
            return {**row, "valid": False, "ready": False, "state": "UNKNOWN"}
        if row.get("observation_mode") not in {None, "REALTIME"}:
            row["profile"] = "RESET:MODE"
        args = dict(pair=f"{params.buy_ema_fast}/{params.buy_ema_slow}",
                    mode=params.buy_ema_cross_mode, sessions=params.buy_ema_cross_sessions,
                    exchange=exchange, working_dates=working_dates, holidays=holidays)
        if row.get("profile") not in {None, "", args["pair"]}:
            # A changed pair starts a new baseline now, not in recovered history.
            row = advance_memory(row, indicators, now=now, **args)
        last = float(row.get("observed_at_epoch", 0))
        if row.get("initialized") and now.timestamp() - last > MAX_DECISION_AGE:
            points, ready = self._samples(symbol, last, now, exchange, working_dates, holidays) if recover else ([], False)
            if not ready:
                return self.rule_state.save_session_cross(symbol, stream,
                    {**row, "current_ready": False, "ready": False, "valid": False, "state": "UNKNOWN"})
            for stamp, price in points:
                if stamp >= now.timestamp():
                    # The current fresh observation owns this exact boundary.
                    continue
                when = datetime.fromtimestamp(stamp, VN_TZ)
                marks, _ = self._marks(bars, price, when.date(), params)
                if marks is None:
                    return self.rule_state.save_session_cross(symbol, stream,
                        {**row, "current_ready": False, "ready": False, "valid": False, "state": "UNKNOWN"})
                row = advance_memory(row, marks, now=when, **args)
        row = advance_memory(row, indicators, now=now, **args)
        _, threshold = self._marks(bars, float(bars[-1]["close"]), now.date(), params) if bars else (None, None)
        row.update(threshold_price=threshold if threshold is not None else 1e30, exchange=exchange, observation_mode="REALTIME")
        if threshold is None or float(bars[-1]["close"]) <= threshold:
            row.update(valid=False, state="WAIT_UP" if row.get("ready") else "UNKNOWN")
        return self.rule_state.save_session_cross(symbol, stream, row)
