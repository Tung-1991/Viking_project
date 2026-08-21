from __future__ import annotations

import csv
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any


_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}
_REPLACE_RETRY_DELAYS = (0.005, 0.01, 0.02, 0.04, 0.08, 0.16, 0.25)


def _store_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _replace_with_retry(source: Path, target: Path) -> None:
    """Tolerate short Windows locks without hiding persistent disk errors."""
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {
                5, 32, 33,
            }
            if not retryable or attempt >= len(_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


class AtomicJSONStore:
    def __init__(self, path: str | Path, default: Any = None):
        self.path = Path(path)
        self.default = default
        self._lock = _store_lock(self.path)

    def read(self) -> Any:
        with self._lock:
            try:
                with self.path.open("r", encoding="utf-8-sig") as handle:
                    return json.load(handle)
            except (OSError, ValueError, TypeError):
                if callable(self.default):
                    return self.default()
                return json.loads(json.dumps(self.default)) if self.default is not None else None

    def write(self, value: Any) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(tmp, self.path)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass


class JSONLineJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def append(self, value: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")

    def read_all(self, limit: int = 0) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        except (OSError, ValueError):
            return []
        return rows[-limit:] if limit > 0 else rows


class SignalLog:
    """Every change of BUY/SELL signal, whether or not the bot could act.

    The bot only trades five symbols at a time, so most signals never become
    orders and leave no trace anywhere else.  This is the record used to judge
    the rule itself rather than the bot's ability to act on it.
    """

    FIELDS = (
        "timestamp", "symbol", "signal", "price", "ema_fast", "ema_slow",
        "rsi", "market_state", "acted", "blocked_by",
    )

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._last: dict[str, str] = {}

    def record(self, row: dict[str, Any]) -> bool:
        """Write only when this symbol's signal differs from the last one."""
        symbol = str(row.get("symbol", "") or "")
        signal = str(row.get("signal", "") or "")
        if not symbol or self._last.get(symbol) == signal:
            return False
        self._last[symbol] = signal
        if not signal:
            return False
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDS, extrasaction="ignore")
                if new_file:
                    writer.writeheader()
                writer.writerow({key: row.get(key, "") for key in self.FIELDS})
        return True

    def read_all(self, limit: int = 0) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, ValueError):
            return []
        return rows[-limit:] if limit > 0 else rows


class CSVOrderJournal:
    """Append-only flat history mirror for independent UI/report consumption."""

    FIELDS = (
        "timestamp", "execution_mode", "cache_id", "broker_order_id", "trade_id",
        "symbol", "side", "action", "order_type", "limit_price", "quantity",
        "filled_quantity", "remaining_quantity", "source", "queue_status",
        "broker_status", "fee", "tax", "message", "error",
    )

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def append_event(self, event: dict[str, Any]) -> None:
        intent = event.get("intent") if isinstance(event.get("intent"), dict) else {}
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        row = {
            "timestamp": event.get("ts", ""),
            "execution_mode": intent.get("execution_mode", ""),
            "cache_id": intent.get("id", ""),
            "broker_order_id": result.get("order_id", ""),
            "trade_id": intent.get("trade_id", ""),
            "symbol": intent.get("symbol", ""),
            "side": intent.get("side", ""),
            "action": intent.get("action", ""),
            "order_type": intent.get("order_type", ""),
            "limit_price": intent.get("limit_price", 0),
            "quantity": intent.get("quantity", 0),
            "filled_quantity": intent.get("filled_quantity", 0),
            "remaining_quantity": intent.get("remaining_quantity", 0),
            "source": intent.get("source", ""),
            "queue_status": event.get("queue_status", ""),
            "broker_status": result.get("status", ""),
            "fee": body.get("fee", body.get("totalFee", 0)) if isinstance(body, dict) else 0,
            "tax": body.get("tax", 0) if isinstance(body, dict) else 0,
            "message": result.get("message", ""),
            "error": result.get("error", ""),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDS, extrasaction="ignore")
                if needs_header:
                    writer.writeheader()
                writer.writerow(row)

    def read_all(self, limit: int = 0) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            return []
        return rows[-limit:] if limit > 0 else rows


class DailyFeeTracker:
    """Daily fee counter backed by the append-only order CSV.

    Automatic rollover comes from filtering by the local calendar date. A manual
    reset only stores a per-mode cutoff; it never deletes audit history.
    """

    def __init__(self, history_path: str | Path, state_path: str | Path):
        self.history = CSVOrderJournal(history_path)
        self.state = AtomicJSONStore(state_path, default={})
        self._lock = threading.RLock()

    @staticmethod
    def _timestamp(raw: Any) -> float:
        try:
            return float(raw or 0.0)
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                return 0.0

    def _cutoff(self, mode: str, now: float) -> float:
        mode = "REAL" if str(mode).upper() == "REAL" else "PAPER"
        today = datetime.fromtimestamp(now).date().isoformat()
        state = self.state.read()
        state = state if isinstance(state, dict) else {}
        row = state.get(mode) if isinstance(state.get(mode), dict) else {}
        if str(row.get("date") or "") != today:
            state[mode] = {"date": today, "reset_at": 0.0}
            self.state.write(state)
            return 0.0
        return max(0.0, float(row.get("reset_at", 0.0) or 0.0))

    def total(self, mode: str, now: float | None = None) -> float:
        current = float(now or datetime.now().timestamp())
        selected_mode = "REAL" if str(mode).upper() == "REAL" else "PAPER"
        today = datetime.fromtimestamp(current).date()
        with self._lock:
            cutoff = self._cutoff(selected_mode, current)
            total = 0.0
            for row in self.history.read_all():
                if str(row.get("execution_mode") or "").upper() != selected_mode:
                    continue
                timestamp = self._timestamp(row.get("timestamp"))
                if timestamp <= cutoff or timestamp <= 0:
                    continue
                if datetime.fromtimestamp(timestamp).date() != today:
                    continue
                try:
                    fee = abs(float(row.get("fee", 0.0) or 0.0))
                    tax = abs(float(row.get("tax", 0.0) or 0.0))
                except (TypeError, ValueError):
                    continue
                total += fee + tax
            return total

    def reset(self, mode: str, now: float | None = None) -> None:
        current = float(now or datetime.now().timestamp())
        selected_mode = "REAL" if str(mode).upper() == "REAL" else "PAPER"
        with self._lock:
            state = self.state.read()
            state = state if isinstance(state, dict) else {}
            state[selected_mode] = {
                "date": datetime.fromtimestamp(current).date().isoformat(),
                "reset_at": current,
            }
            self.state.write(state)
