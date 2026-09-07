from __future__ import annotations

from pathlib import Path
import hashlib
import threading
import time
from typing import Any

from ..storage import AtomicJSONStore


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
        self.store = AtomicJSONStore(
            path,
            default={
                "market": {},
                "symbols": {},
                "processed_signals": {},
                "processed_alerts": {},
                "telegram_signals": {},
                "indicator_streams": {},
                "buy_confirmations": {},
            },
        )
        self._lock = threading.RLock()

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
        raw["indicator_streams"] = raw.get("indicator_streams") if isinstance(raw.get("indicator_streams"), dict) else {}
        raw["buy_confirmations"] = raw.get("buy_confirmations") if isinstance(raw.get("buy_confirmations"), dict) else {}
        return raw

    @staticmethod
    def _buy_confirmation_key(symbol: str, stream: str) -> str:
        return f"{str(stream or '').strip().upper()}|{str(symbol or '').strip().upper()}"

    def buy_confirmation(self, symbol: str, stream: str) -> dict[str, Any]:
        key = self._buy_confirmation_key(symbol, stream)
        with self._lock:
            value = self._read()["buy_confirmations"].get(key)
            return dict(value) if isinstance(value, dict) else {}

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
                if isinstance(existing, dict) else {}
            )
            periods = (
                "buy_ema_fast_period", "buy_ema_slow_period",
                "sell_ema_fast_period", "sell_ema_slow_period", "rsi_period",
            )
            if previous and any(previous.get(name) != snapshot.get(name) for name in periods):
                previous = {}
            raw["indicator_streams"][key] = {
                "session": session_key,
                "snapshot": snapshot,
                "updated_at": time.time(),
            }
            self.store.write(raw)
            return previous

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
        close_price: float = 0.0,
        closed_bar: bool = False,
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
            current["peak_profit_pct"] = max(float(current.get("peak_profit_pct", profit_pct) or profit_pct), float(profit_pct))
            current["current_profit_pct"] = float(profit_pct)
            current["mae_pct"] = min(0.0, float(current.get("mae_pct", 0.0) or 0.0), float(profit_pct))
            current["mfe_pct"] = max(0.0, float(current.get("mfe_pct", 0.0) or 0.0), float(profit_pct))
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
            if closed_bar and float(close_price or 0.0) > 0:
                current["highest_close"] = max(float(current.get("highest_close", 0.0) or 0.0), float(close_price))
            current["updated_at"] = time.time()
            raw["symbols"][key] = current
            raw["symbols"].pop(symbol, None)
            self.store.write(raw)
            return dict(current)

    def mark_protection_done(self, symbol: str, trade_id: str, events: list[str]) -> None:
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
                current["normal_protection_done"] = True
            if "HIGH_PROFIT_PROTECTION" in normalized:
                current["high_profit_protection_done"] = True
            self.store.write(raw)

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

    def open_telegram_signal(
        self,
        symbol: str,
        candle_key: str,
        *,
        price: float,
        market_state: str,
    ) -> dict[str, Any] | None:
        """Create one persistent Telegram BUY signal per symbol."""
        symbol = str(symbol or "").strip().upper()
        candle_key = str(candle_key or "").strip()
        price = float(price or 0.0)
        if not symbol or not candle_key or price <= 0:
            return None
        with self._lock:
            raw = self._read()
            active = raw["telegram_signals"].get(symbol)
            if isinstance(active, dict):
                return None
            signal_id = hashlib.sha256(f"{symbol}|{candle_key}".encode("utf-8")).hexdigest()[:10].upper()
            record = {
                "id": signal_id,
                "symbol": symbol,
                "candle_key": candle_key,
                "buy_price": price,
                "market_state": str(market_state or "UNKNOWN").upper(),
                "opened_at": time.time(),
            }
            raw["telegram_signals"][symbol] = record
            self.store.write(raw)
            return dict(record)

    def active_telegram_signal(self, symbol: str) -> dict[str, Any] | None:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return None
        with self._lock:
            raw = self._read()
            active = raw["telegram_signals"].get(symbol)
            return dict(active) if isinstance(active, dict) else None

    def discard_telegram_signal(self, symbol: str) -> dict[str, Any] | None:
        """Forget an alerted BUY that never became an actual position."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return None
        with self._lock:
            raw = self._read()
            active = raw["telegram_signals"].pop(symbol, None)
            if not isinstance(active, dict):
                return None
            self.store.write(raw)
            return dict(active)

    def claim_closed_telegram_signal(
        self,
        symbol: str,
        signal_id: str,
    ) -> dict[str, Any] | None:
        """Take the matching BUY record only when its actual trade closes."""
        symbol = str(symbol or "").strip().upper()
        signal_id = str(signal_id or "").strip()
        if not symbol or not signal_id:
            return None
        with self._lock:
            raw = self._read()
            active = raw["telegram_signals"].get(symbol)
            if not isinstance(active, dict) or str(active.get("id", "")) != signal_id:
                return None
            raw["telegram_signals"].pop(symbol, None)
            self.store.write(raw)
            return dict(active)
