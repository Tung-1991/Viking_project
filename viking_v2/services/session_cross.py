"""Recover same-session BUY EMA crossings from cached completed minute prices."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
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
            self.store.write({"symbols": self.rows})
            self._saved_at[symbol] = stamp
        return {**row, "ready": not missing_ranges(targets, row["ranges"]),
                "cutoff": bucket, "midnight": midnight}


class SessionCrossService:
    def __init__(self, cache: SessionPriceCache, rule_state):
        self.cache, self.rule_state = cache, rule_state
        self._calculations = {}

    def observe(self, symbol, stream, bars, indicators, params, now, exchange, *, recover=True):
        now = now.astimezone(VN_TZ)
        history = []
        for row in bars:
            try:
                if datetime.fromtimestamp(float(row["time"]), VN_TZ).date() < now.date():
                    price = float(row["close"])
                    if isfinite(price) and price > 0:
                        history.append((float(row["time"]), price))
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                continue
        history = sorted(dict(history).items())
        price_row = self.cache.observe(symbol, float(bars[-1]["close"]), now, exchange, recover=recover)
        signature = hashlib.sha256(json.dumps([history, params.buy_ema_fast, params.buy_ema_slow,
                                              price_row["day"], price_row["revision"]]).encode()).hexdigest()
        result = self._calculations.get(symbol)
        if not result or result[0] != signature:
            cross = self._calculate(history, price_row, params)
            self._calculations[symbol] = (signature, cross)
        else:
            cross = dict(result[1])
        fast, slow = indicators.get("buy_ema_fast"), indicators.get("buy_ema_slow")
        current_ready = (indicators.get("signal_ready") is not False and fast is not None and slow is not None
                         and isfinite(float(fast)) and isfinite(float(slow)))
        prior = self.rule_state.session_cross(symbol, stream)
        profile = f"{params.buy_ema_fast}/{params.buy_ema_slow}"
        same_profile = prior.get("day") == price_row["day"] and prior.get("profile") == profile
        invalid_after = float(prior.get("invalid_after", 0) or 0) if same_profile else 0.0
        # Keep a live crossing's identity when its minute is subsequently
        # backfilled. A completed down minute during a gap invalidates it.
        prior_stamp = float(prior.get("cross_timestamp", 0) or 0)
        if (same_profile and prior.get("cross_id") and prior_stamp > invalid_after
                and prior_stamp > float(cross.get("last_below_timestamp", 0) or 0)
                and prior_stamp >= float(cross.get("cross_timestamp", 0) or 0)):
            cross = {**cross, **{key: prior[key] for key in (
                "cross_id", "cross_at", "cross_timestamp", "previous_fast", "previous_slow",
            ) if key in prior}}
        prior_fast, prior_slow = prior.get("current_fast"), prior.get("current_slow")
        live_transition = bool(same_profile and current_ready and prior.get("current_ready")
                               and 0 < now.timestamp() - float(prior.get("observed_at_epoch", 0)) <= MAX_DECISION_AGE
                               and prior_fast <= prior_slow and fast > slow)
        if live_transition and (not cross.get("cross_id")
                                or float(cross.get("cross_timestamp", 0)) <= invalid_after):
            minute_end = int(now.timestamp()) // 60 * 60 + 60
            cross = {**cross, "cross_id": f"SESSION:{price_row['day']}:{profile}:{minute_end}",
                     "cross_timestamp": minute_end, "cross_at": now.isoformat(),
                     "previous_fast": prior_fast, "previous_slow": prior_slow}
        live_above = float(bars[-1]["close"]) > float(cross.get("threshold_price", float("inf")))
        valid = bool(cross.get("cross_id") and price_row["ready"] and current_ready and fast > slow and live_above)
        expires = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=max((end for _, end, _ in exchange_sessions(exchange)), default=0))
        valid = valid and now < expires
        state = ("UNKNOWN" if not price_row["ready"] or not current_ready else "EXPIRED" if now >= expires
                 else "SESSION_ACTIVE" if valid else "WAIT_UP" if fast <= slow or not live_above else "WAIT_DOWN")
        # A live drop also invalidates the earlier crossing before the next
        # minute closes. Persist its cutoff so restart cannot revive it.
        if current_ready and (fast <= slow or not live_above):
            invalid_after = max(invalid_after, now.timestamp(), float(cross.get("cross_timestamp", 0) or 0))
        if valid and float(cross.get("cross_timestamp", 0)) <= invalid_after:
            valid, state = False, "WAIT_UP" if fast <= slow or not live_above else "WAIT_DOWN"
        evidence = {**cross, "required": True, "session": True, "state": state, "valid": valid,
                    "ready": bool(price_row["ready"] and current_ready), "day": price_row["day"],
                    "expires_at": expires.timestamp(), "invalid_after": invalid_after,
                    "current_fast": fast, "current_slow": slow, "current_ready": current_ready,
                    "observed_at_epoch": now.timestamp(), "profile": profile}
        return self.rule_state.save_session_cross(symbol, stream, evidence)

    @staticmethod
    def _calculate(history, row, params):
        if len(history) < params.buy_ema_slow:
            return {"cross_id": "", "cross_at": ""}
        values = [price for _, price in history]
        base_fast, base_slow = ema(values, params.buy_ema_fast)[-1], ema(values, params.buy_ema_slow)[-1]
        previous_fast, previous_slow = base_fast, base_slow
        alpha_fast, alpha_slow = 2 / (params.buy_ema_fast + 1), 2 / (params.buy_ema_slow + 1)
        threshold = (base_slow * (1 - alpha_slow) - base_fast * (1 - alpha_fast)) / (alpha_fast - alpha_slow)
        cross = {"cross_id": "", "cross_at": "", "threshold_price": threshold}
        for stamp, price in sorted((int(stamp), price) for stamp, price in row["prices"].items()):
            if stamp >= row["cutoff"] or stamp < row["midnight"]:
                continue
            fast = base_fast + 2 / (params.buy_ema_fast + 1) * (price - base_fast)
            slow = base_slow + 2 / (params.buy_ema_slow + 1) * (price - base_slow)
            if fast <= slow:
                cross = {"cross_id": "", "cross_at": "", "threshold_price": threshold,
                         "last_below_timestamp": stamp + 60}
            elif previous_fast <= previous_slow:
                when = datetime.fromtimestamp(stamp + 60, VN_TZ).isoformat()
                cross = {"cross_at": when, "cross_timestamp": stamp + 60,
                         "cross_id": f"SESSION:{row['day']}:{params.buy_ema_fast}/{params.buy_ema_slow}:{stamp + 60}",
                         "previous_fast": previous_fast, "previous_slow": previous_slow, "threshold_price": threshold,
                         "last_below_timestamp": cross.get("last_below_timestamp", 0)}
            previous_fast, previous_slow = fast, slow
        return cross
