from __future__ import annotations

from pathlib import Path
import hashlib
import threading
import time
import uuid
from typing import Any, Callable

from ..trading.durable import DurableJSONStore


_LEGACY_SIGNAL_NAMES = {"M": "BUY", "B": "SELL"}


def _renamed_signal_keys(raw: Any) -> dict[str, Any]:
    """Read `SYMBOL|M` / `SYMBOL|B` state written before the BUY/SELL rename.

    Without this, an upgraded runtime would treat every already-processed
    signal as new and could re-enter a symbol on the same candle.
    """
    if not isinstance(raw, dict):
        return {}
    renamed: dict[str, Any] = {}
    for key, value in raw.items():
        symbol, _, signal = str(key).rpartition("|")
        renamed[f"{symbol}|{_LEGACY_SIGNAL_NAMES[signal]}" if symbol and signal in _LEGACY_SIGNAL_NAMES else key] = value
    return renamed


class RuleStateStore:
    def __init__(self, path: str | Path):
        self.store = DurableJSONStore(
            path,
            default={
                "market": {},
                "symbols": {},
                "processed_signals": {},
                "processed_alerts": {},
                "telegram_signals": {},
                "telegram_buy_watches": {},
                "indicator_streams": {},
                "buy_confirmations": {},
                "signal_observations": {},
                "entry_pauses": {},
            },
        )
        self._lock = self.store.transaction

    @staticmethod
    def _position_key(symbol: str, trade_id: str) -> str:
        return f"{str(symbol or '').upper()}|{str(trade_id or '')}"

    def _read(self) -> dict[str, Any]:
        raw = self.store.read()
        raw = raw if isinstance(raw, dict) else {}
        raw["market"] = raw.get("market") if isinstance(raw.get("market"), dict) else {}
        raw["symbols"] = raw.get("symbols") if isinstance(raw.get("symbols"), dict) else {}
        raw["processed_signals"] = _renamed_signal_keys(raw.get("processed_signals"))
        raw["processed_alerts"] = raw.get("processed_alerts") if isinstance(raw.get("processed_alerts"), dict) else {}
        raw["telegram_signals"] = raw.get("telegram_signals") if isinstance(raw.get("telegram_signals"), dict) else {}
        raw["telegram_buy_watches"] = raw.get("telegram_buy_watches") if isinstance(raw.get("telegram_buy_watches"), dict) else {}
        raw["indicator_streams"] = raw.get("indicator_streams") if isinstance(raw.get("indicator_streams"), dict) else {}
        raw["buy_confirmations"] = raw.get("buy_confirmations") if isinstance(raw.get("buy_confirmations"), dict) else {}
        raw["signal_observations"] = raw.get("signal_observations") if isinstance(raw.get("signal_observations"), dict) else {}
        raw["entry_pauses"] = raw.get("entry_pauses") if isinstance(raw.get("entry_pauses"), dict) else {}
        return raw

    def start_entry_pause(
        self,
        execution_mode: str,
        seconds: float,
        *,
        reason: str = "MANUAL_SELL",
        symbol: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Persist a mode-specific BUY pause across UI/daemon restarts."""
        mode = "REAL" if str(execution_mode or "").upper() == "REAL" else "PAPER"
        started_at = time.time() if now is None else float(now)
        duration = max(0.0, float(seconds or 0.0))
        if duration <= 0:
            return self.entry_pause(mode, now=started_at)
        with self._lock:
            raw = self._read()
            current = raw["entry_pauses"].get(mode)
            current_until = (
                float(current.get("until", 0.0) or 0.0)
                if isinstance(current, dict) else 0.0
            )
            pause = {
                "active": True,
                "started_at": started_at,
                "until": max(current_until, started_at + duration),
                "reason": str(reason or "MANUAL_SELL").upper(),
                "symbol": str(symbol or "").upper(),
            }
            raw["entry_pauses"][mode] = pause
            self.store.write(raw)
            return dict(pause)

    def entry_pause(
        self,
        execution_mode: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return the active BUY pause, removing it once its deadline passes."""
        mode = "REAL" if str(execution_mode or "").upper() == "REAL" else "PAPER"
        checked_at = time.time() if now is None else float(now)
        with self._lock:
            raw = self._read()
            current = raw["entry_pauses"].get(mode)
            if not isinstance(current, dict):
                return {"active": False, "until": 0.0, "remaining_seconds": 0.0}
            until = float(current.get("until", 0.0) or 0.0)
            if until <= checked_at:
                raw["entry_pauses"].pop(mode, None)
                self.store.write(raw)
                return {"active": False, "until": 0.0, "remaining_seconds": 0.0}
            return {
                **current,
                "active": True,
                "until": until,
                "remaining_seconds": max(0.0, until - checked_at),
            }

    def clear_operating_cooldowns(self, execution_mode: str) -> bool:
        """Clear mode-scoped rule pauses without touching signal/trade history."""
        mode = "REAL" if str(execution_mode or "").upper() == "REAL" else "PAPER"
        with self._lock:
            raw = self._read()
            removed = raw["entry_pauses"].pop(mode, None) is not None
            if removed:
                self.store.write(raw)
            return removed

    @staticmethod
    def _buy_confirmation_key(symbol: str, stream: str) -> str:
        return f"{str(stream or '').strip().upper()}|{str(symbol or '').strip().upper()}"

    def buy_confirmation(self, symbol: str, stream: str) -> dict[str, Any]:
        key = self._buy_confirmation_key(symbol, stream)
        with self._lock:
            value = self._read()["buy_confirmations"].get(key)
            return dict(value) if isinstance(value, dict) else {}

    def discard_buy_candidates(self, *, recheck_current_conditions: bool = False) -> None:
        """Restart drops action candidates, not cooldowns or indicator history."""
        with self._lock:
            raw = self._read()
            for key, observation in list(raw["signal_observations"].items()):
                if observation.get("signal") == "BUY" and observation.get("first_seen"):
                    stream, _, symbol = key.partition("|")
                    raw["processed_signals"][f"{stream}|{symbol}|BUY"] = f"{observation['candle_key']}|{observation['first_seen']}"
                    if recheck_current_conditions:
                        # Drop the old observation, not its consumed ID. A
                        # fresh valid level creates a new observation after
                        # startup; old queued requests are never resurrected.
                        raw["signal_observations"].pop(key, None)
            raw["buy_confirmations"] = {}
            self.store.write(raw)

    def save_buy_confirmation(self, symbol: str, stream: str, value: dict[str, Any]) -> None:
        key = self._buy_confirmation_key(symbol, stream)
        with self._lock:
            raw = self._read()
            if raw["buy_confirmations"].get(key, {}) == (value or {}):
                return
            if value:
                raw["buy_confirmations"][key] = dict(value)
            else:
                raw["buy_confirmations"].pop(key, None)
            self.store.write(raw)

    def observe_signal_time(
        self,
        symbol: str,
        stream: str,
        signal: str,
        candle_key: str,
        observed_at: str,
    ) -> str:
        """Persist the first BUY observation even when entry filters are off."""
        key = self._buy_confirmation_key(symbol, stream)
        signal = str(signal or "").strip().upper()
        candle_key = str(candle_key or "").strip()
        observed_at = str(observed_at or "").strip()
        with self._lock:
            raw = self._read()
            current = raw["signal_observations"].get(key)
            if signal != "BUY" or not candle_key or not observed_at:
                if key in raw["signal_observations"]:
                    raw["signal_observations"].pop(key, None)
                    self.store.write(raw)
                return ""
            if (
                isinstance(current, dict)
                and current.get("signal") == signal
                and current.get("candle_key") == candle_key
            ):
                return str(current.get("first_seen", "") or "")
            raw["signal_observations"][key] = {
                "signal": signal,
                "candle_key": candle_key,
                "first_seen": observed_at,
            }
            self.store.write(raw)
            return observed_at

    def observe_indicators(
        self,
        symbol: str,
        stream: str,
        session_key: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist consecutive live EMA observations and return the prior one.

        REAL and PAPER are separate streams.  Persisting this small snapshot
        also prevents a daemon restart during the session from silently falling
        back to comparing the live candle with yesterday again.
        """
        symbol = str(symbol or "").strip().upper()
        stream = str(stream or "").strip().upper()
        session_key = str(session_key or "").strip()
        snapshot = dict(snapshot or {})
        if not symbol or not stream or not snapshot:
            return {}
        key = f"{stream}|{symbol}"
        with self._lock:
            raw = self._read()
            existing = raw["indicator_streams"].get(key)
            previous = (
                dict(existing.get("snapshot") or {})
                if isinstance(existing, dict)
                and str(existing.get("interval", "TICK") or "TICK").upper() == "TICK"
                else {}
            )
            periods = (
                "buy_ema_fast_period", "buy_ema_slow_period",
                "sell_ema_fast_period", "sell_ema_slow_period", "rsi_period",
            )
            if previous and any(previous.get(name) != snapshot.get(name) for name in periods):
                previous = {}
            raw["indicator_streams"][key] = {
                "session": session_key,
                "interval": "TICK",
                "snapshot": snapshot,
                "previous_snapshot": previous,
                "updated_at": time.time(),
            }
            self.store.write(raw)
            return previous

    def observe_indicator_bucket(
        self,
        symbol: str,
        stream: str,
        session_key: str,
        interval: str,
        bucket_key: int,
        close_price: float,
        baseline_snapshot: dict[str, Any],
        build_snapshot: Callable[[float], dict[str, Any]],
    ) -> dict[str, Any]:
        """Accept one indicator snapshot per completed minute bucket.

        The current bucket only accumulates its latest price.  When the next
        bucket starts, that saved close becomes the provisional close of the
        unfinished 1D candle.  All state needed to resume after a daemon restart
        is persisted here.
        """
        symbol = str(symbol or "").strip().upper()
        stream = str(stream or "").strip().upper()
        session_key = str(session_key or "").strip()
        interval = str(interval or "").strip().upper()
        bucket_key = int(bucket_key or 0)
        close_price = float(close_price or 0.0)
        baseline = dict(baseline_snapshot or {})
        if not symbol or not stream or interval not in {"1M", "2M", "5M"}:
            return {"current": baseline, "previous": baseline, "advanced": False, "bucket": 0}
        key = f"{stream}|{symbol}"
        periods = (
            "buy_ema_fast_period", "buy_ema_slow_period",
            "sell_ema_fast_period", "sell_ema_slow_period", "rsi_period",
        )
        with self._lock:
            raw = self._read()
            existing = raw["indicator_streams"].get(key)
            compatible = (
                isinstance(existing, dict)
                and str(existing.get("session", "")) == session_key
                and str(existing.get("interval", "")).upper() == interval
            )
            accepted = dict(existing.get("snapshot") or {}) if compatible else baseline
            if accepted and baseline and any(
                accepted.get(name) != baseline.get(name) for name in periods
            ):
                compatible = False
                accepted = baseline
            if not compatible:
                raw["indicator_streams"][key] = {
                    "session": session_key,
                    "interval": interval,
                    "snapshot": accepted,
                    "previous_snapshot": accepted,
                    "pending_bucket": bucket_key,
                    "pending_close": close_price,
                    "accepted_bucket": 0,
                    "updated_at": time.time(),
                }
                self.store.write(raw)
                return {
                    "current": accepted, "previous": accepted,
                    "advanced": False, "bucket": 0,
                }

            pending_bucket = int(existing.get("pending_bucket", 0) or 0)
            if bucket_key <= pending_bucket:
                # Out-of-order ticks must not roll the close backwards. The
                # latest observation in the active bucket wins.
                if bucket_key == pending_bucket and close_price > 0:
                    existing["pending_close"] = close_price
                    existing["updated_at"] = time.time()
                    raw["indicator_streams"][key] = existing
                    self.store.write(raw)
                return {
                    "current": accepted,
                    "previous": dict(existing.get("previous_snapshot") or accepted),
                    "advanced": False,
                    "bucket": int(existing.get("accepted_bucket", 0) or 0),
                }

            completed_close = float(existing.get("pending_close", 0.0) or 0.0)
            previous = accepted
            current = dict(build_snapshot(completed_close) or {}) if completed_close > 0 else accepted
            if not current:
                current = accepted
            existing.update(
                session=session_key,
                interval=interval,
                snapshot=current,
                previous_snapshot=previous,
                pending_bucket=bucket_key,
                pending_close=close_price,
                accepted_bucket=pending_bucket,
                updated_at=time.time(),
            )
            raw["indicator_streams"][key] = existing
            self.store.write(raw)
            return {
                "current": current, "previous": previous,
                "advanced": True, "bucket": pending_bucket,
            }

    def confirmed_market_state(self) -> str:
        return str(self._read()["market"].get("confirmed", "UNKNOWN") or "UNKNOWN").upper()

    def market_confirmation(self, required: int = 3) -> dict[str, Any]:
        """Return the Phase-1 confirmation state without hiding its candidate.

        A new runtime can legitimately have no confirmed state yet while the
        current VNINDEX classification is already known.  Consumers must show
        that candidate and its progress instead of calling the data UNKNOWN.
        """
        state = dict(self._read()["market"])
        confirmed = str(state.get("confirmed", "UNKNOWN") or "UNKNOWN").upper()
        candidate = str(state.get("candidate", "TRANSITION") or "TRANSITION").upper()
        count = max(0, int(state.get("candidate_count", 0) or 0))
        required = max(1, int(required or 3))
        return {
            "confirmed": confirmed,
            "candidate": candidate,
            "display": confirmed if confirmed != "UNKNOWN" else candidate,
            "count": required if candidate == confirmed else min(count, required),
            "required": required,
            "pending": confirmed == "UNKNOWN" or candidate != confirmed,
            "session": str(state.get("candidate_session", "") or ""),
        }

    def observe_market_candidate(self, candidate: str, session_key: str, required: int = 3) -> str:
        candidate = str(candidate or "TRANSITION").upper()
        session_key = str(session_key or "")
        required = max(1, int(required or 3))
        with self._lock:
            raw = self._read()
            state = raw["market"]
            confirmed = str(state.get("confirmed", "UNKNOWN") or "UNKNOWN").upper()
            previous_candidate = str(state.get("candidate", "") or "").upper()
            previous_session = str(state.get("candidate_session", "") or "")
            count = max(0, int(state.get("candidate_count", 0) or 0))
            if candidate == confirmed:
                state.update(candidate=candidate, candidate_count=0, candidate_session=session_key)
            elif candidate != previous_candidate:
                state.update(candidate=candidate, candidate_count=1, candidate_session=session_key)
            elif session_key and session_key != previous_session:
                count += 1
                state.update(candidate_count=count, candidate_session=session_key)
            if candidate != confirmed and int(state.get("candidate_count", 0) or 0) >= required:
                confirmed = candidate
                state.update(confirmed=confirmed, candidate_count=0)
            state.setdefault("confirmed", confirmed)
            raw["market"] = state
            self.store.write(raw)
            return str(state.get("confirmed", confirmed) or confirmed).upper()

    def rebuild_market_confirmation(
        self,
        observations: list[tuple[str, str]],
        required: int = 3,
    ) -> str:
        """Rebuild Phase-1 confirmation from historical daily classifications.

        Startup must not wait for future sessions when the required sessions
        already exist in the downloaded VNINDEX history.
        """
        required = max(1, int(required or 3))
        valid_states = {"UPTREND", "DOWNTREND", "ACCUMULATION", "DISTRIBUTION"}
        confirmed = "UNKNOWN"
        candidate = ""
        count = 0
        last_session = ""
        for raw_candidate, raw_session in observations:
            next_candidate = str(raw_candidate or "").upper()
            session = str(raw_session or "")
            if next_candidate not in valid_states or not session:
                continue
            if next_candidate == confirmed:
                candidate = next_candidate
                count = 0
            elif next_candidate != candidate:
                candidate = next_candidate
                count = 1
            elif session != last_session:
                count += 1
            if next_candidate != confirmed and count >= required:
                confirmed = next_candidate
                count = 0
            last_session = session

        with self._lock:
            raw = self._read()
            raw["market"] = {
                "candidate": candidate or confirmed,
                "candidate_count": count,
                "candidate_session": last_session,
                "confirmed": confirmed,
            }
            self.store.write(raw)
        return confirmed

    def update_position_metrics(
        self,
        symbol: str,
        trade_id: str,
        *,
        profit_pct: float,
        net_pnl: float | None = None,
        market_price: float = 0.0,
        t2_dynamic_enabled: bool = False,
        sellable: bool = False,
        normal_arm_pct: float = 7.0,
        entry_avg_price: float = 0.0,
    ) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        trade_id = str(trade_id or "")
        with self._lock:
            raw = self._read()
            key = self._position_key(symbol, trade_id)
            current = raw["symbols"].get(key)
            if not isinstance(current, dict):
                legacy = raw["symbols"].get(symbol)
                current = legacy if isinstance(legacy, dict) and str(legacy.get("trade_id", "")) == trade_id else None
            current = current if isinstance(current, dict) and str(current.get("trade_id", "")) == trade_id else {"trade_id": trade_id}
            if entry_avg_price > 0:
                old_basis = float(current.get("entry_avg_price", 0.0) or 0.0)
                if old_basis <= 0 and float(current.get("market_price", 0.0) or 0.0) > 0:
                    ratio = 1 + float(current.get("current_profit_pct", 0.0) or 0.0) / 100
                    old_basis = float(current["market_price"]) / ratio if ratio > 0 else 0.0
                if old_basis > 0 and abs(old_basis - entry_avg_price) > 1e-9:
                    # Scale-in changes the cost basis, not the historical market
                    # high. Never erase/lower the persisted absolute SELL floor.
                    for field in ("peak_profit_pct", "mfe_pct", "mae_pct", "normal_sellable_peak_profit_pct",
                                  "normal_last_trigger_peak_pct", "normal_last_alert_peak_pct"):
                        if field in current:
                            absolute = old_basis * (1 + float(current[field]) / 100)
                            current[field] = (absolute / entry_avg_price - 1) * 100
                current["entry_avg_price"] = entry_avg_price
            current["peak_profit_pct"] = max(float(current.get("peak_profit_pct", profit_pct) or profit_pct), float(profit_pct))
            current["current_profit_pct"] = float(profit_pct)
            current["mae_pct"] = min(0.0, float(current.get("mae_pct", 0.0) or 0.0), float(profit_pct))
            current["mfe_pct"] = max(0.0, float(current.get("mfe_pct", 0.0) or 0.0), float(profit_pct))
            if t2_dynamic_enabled:
                if not sellable:
                    current["normal_t2_seen_unsellable"] = True
                elif not bool(current.get("normal_t2_started")):
                    current["normal_t2_started"] = True
                    if (
                        bool(current.get("normal_t2_seen_unsellable"))
                        and float(current["peak_profit_pct"]) + 1e-9 < normal_arm_pct
                        and not bool(current.get("normal_armed"))
                    ):
                        current["normal_t2_reset_applied"] = True
                        current["normal_trigger_price"] = 0.0
                        current["normal_sellable_peak_profit_pct"] = max(0.0, float(profit_pct))
                elif bool(current.get("normal_t2_reset_applied")):
                    current["normal_sellable_peak_profit_pct"] = max(
                        float(current.get("normal_sellable_peak_profit_pct", 0.0) or 0.0),
                        max(0.0, float(profit_pct)),
                    )
            if net_pnl is not None:
                current["current_net_pnl"] = float(net_pnl)
                current["mae_net_pnl"] = min(
                    0.0,
                    float(current.get("mae_net_pnl", 0.0) or 0.0),
                    float(net_pnl),
                )
                current["mfe_net_pnl"] = max(
                    0.0,
                    float(current.get("mfe_net_pnl", 0.0) or 0.0),
                    float(net_pnl),
                )
            if float(market_price or 0.0) > 0:
                current["market_price"] = float(market_price)
            current["updated_at"] = time.time()
            raw["symbols"][key] = current
            raw["symbols"].pop(symbol, None)
            self.store.write(raw)
            return dict(current)

    def mark_protection_done(
        self,
        symbol: str,
        trade_id: str,
        events: list[str],
        *,
        trigger_peak_pct: float = 0.0,
        rearm_mfe_pct: float = 0.0,
        execution_id: str = "",
    ) -> None:
        symbol = str(symbol or "").upper()
        with self._lock:
            raw = self._read()
            key = self._position_key(symbol, trade_id)
            current = raw["symbols"].get(key)
            if not isinstance(current, dict):
                current = raw["symbols"].get(symbol)
            if not isinstance(current, dict) or str(current.get("trade_id", "")) != str(trade_id):
                return
            normalized = {str(event or "").upper() for event in events}
            if "NORMAL_PROTECTION" in normalized:
                execution_id = str(execution_id or "")
                processed = [
                    str(value) for value in current.get("normal_processed_order_ids", [])
                    if str(value)
                ]
                if execution_id and execution_id in processed:
                    return
                current["normal_protection_done"] = True
                current["normal_protection_count"] = max(
                    0, int(current.get("normal_protection_count", 0) or 0),
                ) + 1
                # REPEAT is anchored to the peak that created this request,
                # not a later peak observed while the request was waiting for
                # settlement/fill.  The current MFE is only a compatibility
                # fallback for old intents that did not persist trigger data.
                requested_peak = max(0.0, float(trigger_peak_pct or 0.0))
                peak = requested_peak if requested_peak > 0 else max(
                    0.0, float(current.get("peak_profit_pct", 0.0) or 0.0),
                )
                current["normal_last_trigger_peak_pct"] = peak
                if float(rearm_mfe_pct or 0.0) > 0:
                    current["normal_rearm_mfe_pct"] = float(rearm_mfe_pct)
                current["normal_last_fill_at"] = time.time()
                current["updated_at"] = time.time()
                if execution_id:
                    current["normal_processed_order_ids"] = (processed + [execution_id])[-20:]
            self.store.write(raw)

    def mark_protection_alert(
        self,
        symbol: str,
        trade_id: str,
        *,
        occurrence: str,
        trigger_peak_pct: float,
        rearm_mfe_pct: float,
    ) -> dict[str, Any]:
        """Persist one dry-run PROTECT occurrence across UI/daemon restarts."""
        symbol = str(symbol or "").upper()
        trade_id = str(trade_id or "")
        occurrence = str(occurrence or "")
        if not symbol or not trade_id or not occurrence:
            return {}
        with self._lock:
            raw = self._read()
            key = self._position_key(symbol, trade_id)
            current = raw["symbols"].get(key)
            if not isinstance(current, dict):
                return {}
            if str(current.get("normal_alert_occurrence", "")) != occurrence:
                current["normal_alert_occurrence"] = occurrence
                current["normal_alert_count"] = max(
                    0, int(current.get("normal_alert_count", 0) or 0),
                ) + 1
                current["normal_last_alert_peak_pct"] = max(
                    0.0, float(trigger_peak_pct or 0.0),
                )
                current["normal_alert_rearm_mfe_pct"] = max(
                    0.0, float(rearm_mfe_pct or 0.0),
                )
                current["normal_last_alert_at"] = time.time()
                current["updated_at"] = time.time()
                raw["symbols"][key] = current
                self.store.write(raw)
            return dict(current)

    def arm_normal(self, symbol: str, trade_id: str) -> dict[str, Any]:
        """Persist a PROTECT AUTO arm once for the current trade."""
        symbol = str(symbol or "").upper()
        trade_id = str(trade_id or "")
        if not symbol or not trade_id:
            return {}
        with self._lock:
            raw = self._read()
            key = self._position_key(symbol, trade_id)
            current = raw["symbols"].get(key)
            if not isinstance(current, dict):
                return {}
            current["normal_armed"] = True
            current.setdefault("normal_armed_at", time.time())
            current["updated_at"] = time.time()
            raw["symbols"][key] = current
            self.store.write(raw)
            return dict(current)

    def update_protect_metrics(
        self,
        symbol: str,
        trade_id: str,
        *,
        trigger_price: float,
        atr_pct: float,
        atr_multiplier: float,
        atr_activation_multiplier: float | None = None,
        retention_pct: float | None = None,
        retention_until_pct: float | None = None,
    ) -> dict[str, Any]:
        """Persist the active PROTECT floor so it cannot fall after restart."""
        symbol = str(symbol or "").upper()
        trade_id = str(trade_id or "")
        trigger = max(0.0, float(trigger_price or 0.0))
        if not symbol or not trade_id or trigger <= 0:
            return {}
        with self._lock:
            raw = self._read()
            key = self._position_key(symbol, trade_id)
            current = raw["symbols"].get(key)
            if not isinstance(current, dict):
                legacy = raw["symbols"].get(symbol)
                current = (
                    legacy
                    if isinstance(legacy, dict)
                    and str(legacy.get("trade_id", "")) == trade_id
                    else None
                )
            if not isinstance(current, dict) or str(current.get("trade_id", "")) != trade_id:
                return {}
            current["normal_trigger_price"] = max(
                max(0.0, float(current.get("normal_trigger_price", 0.0) or 0.0)),
                trigger,
            )
            current["normal_atr_pct"] = max(0.0, float(atr_pct or 0.0))
            current["normal_atr_multiplier"] = max(
                0.0, float(atr_multiplier or 0.0),
            )
            current["normal_atr_activation_multiplier"] = max(
                0.0,
                float(
                    atr_multiplier
                    if atr_activation_multiplier is None
                    else atr_activation_multiplier
                ),
            )
            if retention_pct is not None:
                current["normal_retention_pct"] = min(
                    100.0, max(0.0, float(retention_pct or 0.0)),
                )
            if retention_until_pct is not None:
                current["normal_retention_until_pct"] = max(
                    0.0, float(retention_until_pct or 0.0),
                )
            current["updated_at"] = time.time()
            raw["symbols"][key] = current
            raw["symbols"].pop(symbol, None)
            self.store.write(raw)
            return dict(current)

    def position_metrics(self, symbol: str, trade_id: str) -> dict[str, Any]:
        raw = self._read()["symbols"]
        current = raw.get(self._position_key(symbol, trade_id))
        if not isinstance(current, dict):
            current = raw.get(str(symbol or "").upper())
        if not isinstance(current, dict) or str(current.get("trade_id", "")) != str(trade_id or ""):
            return {}
        return dict(current)

    def claim_signal(
        self,
        symbol: str,
        signal: str,
        candle_key: str,
        stream: str = "",
    ) -> bool:
        symbol = str(symbol or "").upper()
        signal = str(signal or "").upper()
        candle_key = str(candle_key or "")
        stream = str(stream or "").strip().upper()
        if not symbol or signal not in {"BUY", "SELL"} or not candle_key:
            return False
        key = f"{stream}|{symbol}|{signal}" if stream else f"{symbol}|{signal}"
        with self._lock:
            raw = self._read()
            if str(raw["processed_signals"].get(key, "")) == candle_key:
                return False
            raw["processed_signals"][key] = candle_key
            self.store.write(raw)
            return True

    def release_signal(
        self,
        symbol: str,
        signal: str,
        candle_key: str,
        stream: str = "",
    ) -> bool:
        stream = str(stream or "").strip().upper()
        base = f"{str(symbol or '').upper()}|{str(signal or '').upper()}"
        key = f"{stream}|{base}" if stream else base
        with self._lock:
            raw = self._read()
            if str(raw["processed_signals"].get(key, "")) != str(candle_key or ""):
                return False
            raw["processed_signals"].pop(key, None)
            self.store.write(raw)
            return True

    def claim_alert(self, key: str, occurrence: str) -> bool:
        key, occurrence = str(key or ""), str(occurrence or "")
        if not key or not occurrence:
            return False
        with self._lock:
            raw = self._read()
            if str(raw["processed_alerts"].get(key, "")) == occurrence:
                return False
            raw["processed_alerts"][key] = occurrence
            self.store.write(raw)
            return True

    def claim_alert_with_cooldown(
        self,
        key: str,
        occurrence: str,
        cooldown_seconds: float = 0.0,
        *,
        now: float | None = None,
    ) -> bool:
        """Claim one occurrence and suppress new ones during a delivery cooldown."""
        key, occurrence = str(key or ""), str(occurrence or "")
        if not key or not occurrence:
            return False
        claimed_at = time.time() if now is None else float(now)
        cooldown_seconds = max(0.0, float(cooldown_seconds or 0.0))
        with self._lock:
            raw = self._read()
            previous = raw["processed_alerts"].get(key)
            if isinstance(previous, dict):
                if str(previous.get("occurrence", "")) == occurrence:
                    return False
                previous_at = float(previous.get("claimed_at", 0.0) or 0.0)
                if cooldown_seconds > 0 and claimed_at < previous_at + cooldown_seconds:
                    return False
            elif str(previous or "") == occurrence:
                return False
            raw["processed_alerts"][key] = {
                "occurrence": occurrence,
                "claimed_at": claimed_at,
            }
            self.store.write(raw)
            return True

    def observe_telegram_buy(
        self, symbol: str, stream: str, valid: bool | None, *,
        session_key: str, queued: bool = False, snapshot: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Track technical transitions separately from trade IDs and order state.

        Only transitions/order creation write state; price changes alone do not.
        Missing indicators never mean a lost BUY. One bounded record per book/mã
        survives restarts, with a new episode on recovery or a new session/rule.
        """
        key = self._buy_confirmation_key(symbol, stream)
        with self._lock:
            raw = self._read()
            saved = raw["telegram_buy_watches"].get(key)
            watch = dict(saved) if isinstance(saved, dict) else {}
            if watch and watch.get("session_key") != session_key:
                watch = {}
            if valid is True and not watch.get("active"):
                watch = {
                    "id": uuid.uuid4().hex, "symbol": str(symbol).upper(),
                    "execution_mode": str(stream).upper(), "session_key": session_key,
                    "active": True, "announced": False, "queued": False,
                }
            changed_to_lost = valid is False and bool(watch.get("active"))
            if changed_to_lost:
                watch["active"] = False
                watch["lost_snapshot"] = dict(snapshot or {})
            if queued and watch:
                watch["queued"] = True
            if watch != (saved or {}):
                if watch:
                    raw["telegram_buy_watches"][key] = watch
                else:
                    raw["telegram_buy_watches"].pop(key, None)
                self.store.write(raw)
            lost = (
                dict(watch) if changed_to_lost and watch.get("announced")
                and not watch.get("queued") else None
            )
            return dict(watch), lost

    def mark_telegram_buy_announced(
        self, symbol: str, stream: str, watch_id: str,
    ) -> dict[str, Any] | None:
        """A successful send may finish after a loss; preserve that ordering."""
        key = self._buy_confirmation_key(symbol, stream)
        with self._lock:
            raw = self._read()
            watch = raw["telegram_buy_watches"].get(key)
            if not isinstance(watch, dict) or watch.get("id") != watch_id:
                return None
            if not watch.get("announced"):
                watch["announced"] = True
                self.store.write(raw)
            return dict(watch) if not watch.get("active") and not watch.get("queued") else None

    def open_telegram_signal(
        self,
        symbol: str,
        candle_key: str,
        *,
        price: float,
        market_state: str,
        signal_id: str = "",
        stream: str = "",
    ) -> dict[str, Any] | None:
        """Create one persistent Telegram position record per book/symbol."""
        symbol = str(symbol or "").strip().upper()
        candle_key = str(candle_key or "").strip()
        price = float(price or 0.0)
        if not symbol or not candle_key or price <= 0:
            return None
        with self._lock:
            raw = self._read()
            key = f"{str(stream).upper()}|{symbol}" if stream else symbol
            active = raw["telegram_signals"].get(key)
            if isinstance(active, dict):
                return None
            legacy = raw["telegram_signals"].get(symbol) if stream else None
            if isinstance(legacy, dict) and signal_id and legacy.get("id") == signal_id:
                raw["telegram_signals"][key] = {**legacy, "execution_mode": str(stream).upper()}
                raw["telegram_signals"].pop(symbol)
                self.store.write(raw)
                return None
            signal_id = str(signal_id or "").strip() or hashlib.sha256(
                (f"{str(stream).upper()}|{symbol}|{candle_key}" if stream else f"{symbol}|{candle_key}").encode("utf-8")
            ).hexdigest()[:10].upper()
            record = {
                "id": signal_id,
                "symbol": symbol,
                "candle_key": candle_key,
                "buy_price": price,
                "market_state": str(market_state or "UNKNOWN").upper(),
                "opened_at": time.time(),
                "execution_mode": str(stream).upper(),
            }
            raw["telegram_signals"][key] = record
            self.store.write(raw)
            return dict(record)

    def active_telegram_signal(self, symbol: str, stream: str = "") -> dict[str, Any] | None:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return None
        with self._lock:
            raw = self._read()
            key = f"{str(stream).upper()}|{symbol}" if stream else symbol
            active = raw["telegram_signals"].get(key)
            return dict(active) if isinstance(active, dict) else None

    def discard_telegram_signal(self, symbol: str, stream: str = "", *, signal_id: str = "") -> dict[str, Any] | None:
        """Forget an alerted BUY that never became an actual position."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return None
        with self._lock:
            raw = self._read()
            key = f"{str(stream).upper()}|{symbol}" if stream else symbol
            active = raw["telegram_signals"].get(key)
            if active is None and stream and signal_id:
                legacy = raw["telegram_signals"].get(symbol)
                if isinstance(legacy, dict) and legacy.get("id") == signal_id:
                    key, active = symbol, legacy
            if signal_id and isinstance(active, dict) and active.get("id") != signal_id:
                return None
            if not isinstance(active, dict):
                return None
            raw["telegram_signals"].pop(key)
            self.store.write(raw)
            return dict(active)

    def claim_closed_telegram_signal(
        self,
        symbol: str,
        signal_id: str,
        stream: str = "",
    ) -> dict[str, Any] | None:
        """Take the matching BUY record only when its actual trade closes."""
        symbol = str(symbol or "").strip().upper()
        signal_id = str(signal_id or "").strip()
        if not symbol or not signal_id:
            return None
        with self._lock:
            raw = self._read()
            key = f"{str(stream).upper()}|{symbol}" if stream else symbol
            active = raw["telegram_signals"].get(key)
            if (not isinstance(active, dict) or active.get("id") != signal_id) and stream:
                legacy = raw["telegram_signals"].get(symbol)
                if isinstance(legacy, dict) and legacy.get("id") == signal_id:
                    key, active = symbol, legacy
            if not isinstance(active, dict) or str(active.get("id", "")) != signal_id:
                return None
            raw["telegram_signals"].pop(key, None)
            self.store.write(raw)
            return dict(active)
