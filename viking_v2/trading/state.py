from __future__ import annotations

from pathlib import Path
import threading
import time
import uuid
from typing import Any

from ..models import TradeCycle
from ..storage import AtomicJSONStore


class TradeStateStore:
    """Persistent trade-cycle state; business decisions live outside this class."""

    def __init__(self, path: str | Path):
        self.store = AtomicJSONStore(
            path,
            default={
                "cycles": [],
                "loss_streaks": {},
                "loss_streak_updated_at": {},
                "capital": {},
            },
        )
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        raw = self.store.read()
        raw = raw if isinstance(raw, dict) else {}
        cycles = raw.get("cycles") if isinstance(raw.get("cycles"), list) else []
        streaks = raw.get("loss_streaks") if isinstance(raw.get("loss_streaks"), dict) else {}
        streak_updates = (
            raw.get("loss_streak_updated_at")
            if isinstance(raw.get("loss_streak_updated_at"), dict)
            else {}
        )
        capital = raw.get("capital") if isinstance(raw.get("capital"), dict) else {}
        return {
            "cycles": cycles,
            "loss_streaks": streaks,
            "loss_streak_updated_at": streak_updates,
            "capital": capital,
        }

    @staticmethod
    def _key(symbol: str, execution_mode: str) -> str:
        return f"{str(execution_mode or 'PAPER').upper()}|{str(symbol or '').upper()}"

    def list_cycles(self) -> list[TradeCycle]:
        return [TradeCycle.from_dict(row) for row in self._read()["cycles"] if isinstance(row, dict)]

    def get(self, trade_id: str) -> TradeCycle | None:
        return next((cycle for cycle in self.list_cycles() if cycle.id == str(trade_id)), None)

    def active_for(self, symbol: str, execution_mode: str) -> TradeCycle | None:
        key = self._key(symbol, execution_mode)
        return next(
            (
                cycle
                for cycle in self.list_cycles()
                if cycle.status == "OPEN" and self._key(cycle.symbol, cycle.execution_mode) == key
            ),
            None,
        )

    def create(
        self,
        symbol: str,
        execution_mode: str,
        *,
        source: str = "BOT",
        trade_id: str = "",
        em_modes: list[str] | None = None,
        sl_enabled: bool = True,
        sl_mode: str = "DEFAULT",
        sl_value: float = 0.0,
        tp_mode: str = "NONE",
        tp_value: float = 0.0,
        entry_market_state: str = "UNKNOWN",
        entry_exposure: float = 0.0,
        entry_budget: float = 0.0,
    ) -> TradeCycle:
        with self._lock:
            active = self.active_for(symbol, execution_mode)
            if active:
                return active
            streak = self.loss_streak(symbol, execution_mode)
            cycle = TradeCycle(
                id=str(trade_id or uuid.uuid4().hex),
                symbol=symbol,
                execution_mode=execution_mode,
                source=source,
                is_reentry=streak > 0,
                em_modes=list(em_modes or []),
                sl_enabled=sl_enabled,
                sl_mode=sl_mode,
                sl_value=sl_value,
                tp_mode=tp_mode,
                tp_value=tp_value,
                entry_market_state=entry_market_state,
                entry_exposure=entry_exposure,
                entry_budget=entry_budget,
            )
            raw = self._read()
            raw["cycles"].append(cycle.to_dict())
            self.store.write(raw)
            return cycle

    def save(self, cycle: TradeCycle) -> TradeCycle:
        with self._lock:
            raw = self._read()
            previous = next(
                (TradeCycle.from_dict(row) for row in raw["cycles"] if str(row.get("id")) == cycle.id),
                None,
            )
            rows = [row for row in raw["cycles"] if str(row.get("id")) != cycle.id]
            rows.append(cycle.to_dict())
            raw["cycles"] = rows
            if cycle.status == "CLOSED" and (not previous or previous.status != "CLOSED"):
                key = self._key(cycle.symbol, cycle.execution_mode)
                if cycle.outcome == "WIN":
                    raw["loss_streaks"][key] = 0
                    raw["loss_streak_updated_at"].pop(key, None)
                else:
                    raw["loss_streaks"][key] = int(raw["loss_streaks"].get(key, 0) or 0) + 1
                    # Keep the close time of the latest loss. When this loss
                    # reaches the configured threshold it is also the exact
                    # beginning of the wall-clock cooldown.
                    raw["loss_streak_updated_at"][key] = float(cycle.closed_at or time.time())
                capital = raw["capital"].get(key)
                capital = dict(capital) if isinstance(capital, dict) else {}
                principal = max(
                    0.0,
                    float(capital.get("principal", 0.0) or 0.0),
                    float(cycle.capital_principal or 0.0),
                )
                available = (
                    float(capital.get("available", principal))
                    if capital.get("available") is not None
                    else principal
                )
                if principal > 0:
                    raw["capital"][key] = {
                        "principal": principal,
                        "available": min(principal, max(0.0, available + cycle.net_pnl)),
                        "updated_at": time.time(),
                    }
            self.store.write(raw)
        return cycle

    def record_buy_fill(self, trade_id: str, quantity: int, price: float, fee: float = 0.0) -> TradeCycle | None:
        cycle = self.get(trade_id)
        if not cycle:
            return None
        cycle.record_buy_fill(quantity, price, fee)
        return self.save(cycle)

    def record_sell_fill(
        self,
        trade_id: str,
        quantity: int,
        price: float,
        fee: float = 0.0,
        *,
        closed_at: float | None = None,
    ) -> TradeCycle | None:
        cycle = self.get(trade_id)
        if not cycle:
            return None
        cycle.record_sell_fill(quantity, price, fee, closed_at=closed_at or time.time())
        return self.save(cycle)

    def mark_exit_once(self, trade_id: str, event: str) -> bool:
        cycle = self.get(trade_id)
        if not cycle or not cycle.mark_exit_once(event):
            return False
        self.save(cycle)
        return True

    def loss_streak(self, symbol: str, execution_mode: str) -> int:
        raw = self._read()
        return max(0, int(raw["loss_streaks"].get(self._key(symbol, execution_mode), 0) or 0))

    def clear_loss_cooldowns(self, execution_mode: str) -> int:
        """Clear every symbol loss lock for one book, preserving all cycles."""
        mode = "REAL" if str(execution_mode or "").upper() == "REAL" else "PAPER"
        prefix = f"{mode}|"
        with self._lock:
            raw = self._read()
            keys = {
                key for key in (
                    set(raw["loss_streaks"]) | set(raw["loss_streak_updated_at"])
                )
                if str(key).upper().startswith(prefix)
            }
            if not keys:
                return 0
            for key in keys:
                raw["loss_streaks"].pop(key, None)
                raw["loss_streak_updated_at"].pop(key, None)
            self.store.write(raw)
            return len(keys)

    def active_loss_streak(
        self,
        symbol: str,
        execution_mode: str,
        *,
        threshold: int = 3,
        lock_hours: float = 24.0,
        now: float | None = None,
    ) -> int:
        """Return the current streak and expire a completed loss cooldown.

        The duration is wall-clock time, including nights, weekends and
        holidays. This keeps LIVE/PAPER identical to backtest's timedelta
        based cooldown instead of turning 24 hours into trading-session hours.
        """
        key = self._key(symbol, execution_mode)
        limit = max(1, int(threshold or 3))
        duration = max(0.0, float(lock_hours or 0.0)) * 3600.0
        current = float(time.time() if now is None else now)
        with self._lock:
            raw = self._read()
            streak = max(0, int(raw["loss_streaks"].get(key, 0) or 0))
            if streak < limit:
                return streak

            started = float(raw["loss_streak_updated_at"].get(key, 0.0) or 0.0)
            if started <= 0:
                # Migrate state written before cooldown timestamps existed.
                closed_losses = [
                    TradeCycle.from_dict(row)
                    for row in raw["cycles"]
                    if isinstance(row, dict)
                    and self._key(row.get("symbol", ""), row.get("execution_mode", "")) == key
                    and str(row.get("status", "")).upper() == "CLOSED"
                ]
                latest = max(
                    (cycle.closed_at for cycle in closed_losses if cycle.outcome == "LOSS"),
                    default=0.0,
                )
                started = float(latest or current)
                raw["loss_streak_updated_at"][key] = started
                self.store.write(raw)

            if duration > 0 and current < started + duration:
                return streak

            raw["loss_streaks"][key] = 0
            raw["loss_streak_updated_at"].pop(key, None)
            self.store.write(raw)
            return 0

    def is_loss_locked(
        self,
        symbol: str,
        execution_mode: str,
        threshold: int = 3,
        *,
        lock_hours: float = 24.0,
        now: float | None = None,
    ) -> bool:
        return self.active_loss_streak(
            symbol,
            execution_mode,
            threshold=threshold,
            lock_hours=lock_hours,
            now=now,
        ) >= max(1, int(threshold or 3))

    def capital_available(self, symbol: str, execution_mode: str, proposed: float) -> float:
        """Cap a new order by the symbol's non-compounding capital ledger."""
        proposed = max(0.0, float(proposed or 0.0))
        row = self._read()["capital"].get(self._key(symbol, execution_mode))
        if not isinstance(row, dict):
            return proposed
        return min(proposed, max(0.0, float(row.get("available", proposed) or 0.0)))

    def update_management(
        self,
        trade_id: str,
        *,
        em_modes: list[str] | None = None,
        sl_mode: str | None = None,
        sl_value: float | None = None,
        tp_mode: str | None = None,
        tp_value: float | None = None,
    ) -> TradeCycle | None:
        cycle = self.get(trade_id)
        if not cycle or cycle.status != "OPEN":
            return None
        if em_modes is not None:
            cycle.em_modes = list(em_modes)
        if sl_mode is not None:
            cycle.sl_mode = str(sl_mode)
        if sl_value is not None:
            cycle.sl_value = float(sl_value)
        if tp_mode is not None:
            cycle.tp_mode = str(tp_mode)
        if tp_value is not None:
            cycle.tp_value = float(tp_value)
        cycle.__post_init__()
        return self.save(cycle)
