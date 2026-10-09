"""Display-only identities and recoverable trash for signal observations.

Never deletes the signal CSV, capture database, order journal or rule state.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from ..storage import AtomicJSONStore


def observation_id(row: dict[str, Any]) -> str:
    periodic = row.get("record_kind") == "PERIODIC"
    fields = ("scheduled_at", "execution_mode", "symbol") if periodic else (
        "timestamp", "execution_mode", "symbol", "signal", "acted", "blocked_by",
        "signal_cycle", "candle_key", "price", "ema_fast", "ema_slow", "rsi",
        "record_kind", "signal_event", "rsi_previous", "rsi_previous_date",
        "ema_cross_state", "ema_cross_at", "observation_profile",
    )
    values = ["PERIODIC" if periodic else "EVENT", *[str(row.get(key, "") or "") for key in fields]]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def history_sort_key(row: dict[str, Any]) -> str:
    # ISO strings with T and legacy strings with a space must sort together.
    return str(row.get("timestamp", "")).replace("T", " ").replace("Z", "+00:00")


def periodic_history_row(sample: dict[str, Any]) -> dict[str, Any]:
    """Adapt a sample to the event table without inventing an ENTRY or order."""
    row = dict(sample)
    row.update(record_kind="PERIODIC", signal="PERIODIC", acted="",
               price=(sample["price_vnd"] / 1000 if sample.get("price_vnd") is not None else ""),
               watchlist_priority=sample.get("priority", ""), market_state=sample.get("market_state", ""))
    row["_history_ids"] = [observation_id(row)]
    return row


def recording_options(enabled: bool, interval: Any, start: str, end: str) -> dict[str, Any]:
    """Validate this small form strictly instead of silently clamping inputs."""
    text = str(interval).strip()
    if not text.isdigit() or not 1 <= int(text) <= 30:
        raise ValueError("Nhịp ghi phải là số nguyên từ 1 đến 30 phút.")
    start, end = str(start).strip(), str(end).strip()
    for value in (start, end):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Giờ ghi theo HH:MM, ví dụ 14:00.")
    if start > end:
        raise ValueError("Giờ bắt đầu không được sau giờ kết thúc.")
    return {"signal_trace_enabled": bool(enabled), "signal_trace_interval_minutes": int(text),
            "signal_trace_start": start, "signal_trace_end": end}


class SignalHistoryTrash:
    """Only IDs are hidden; restore does not replay any trading/Telegram event."""

    def __init__(self, path: str | Path):
        self.store = AtomicJSONStore(path, default={"hidden": []})

    def hidden_ids(self) -> set[str]:
        raw = self.store.read()
        values = raw.get("hidden", []) if isinstance(raw, dict) else []
        if not isinstance(values, list):
            return set()
        return {value for value in values if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)}

    def visible(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        hidden = self.hidden_ids()
        return [row for row in rows if observation_id(row) not in hidden]

    def hide(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.store._lock:
            hidden = self.hidden_ids()
            previous = len(hidden)
            for row in rows:
                hidden.update(value for value in (row.get("_history_ids") or [observation_id(row)])
                              if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value))
            if len(hidden) != previous:
                self.store.write({"hidden": sorted(hidden), "updated_at": datetime.now().isoformat()})
            return len(hidden) - previous

    def restore(self) -> int:
        with self.store._lock:
            count = len(self.hidden_ids())
            if count:
                self.store.write({"hidden": [], "updated_at": datetime.now().isoformat()})
            return count
