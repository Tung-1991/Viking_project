from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ..models import OrderIntent, StrategyDecision
from ..trading.orders import FINAL_STATUSES


RETRYABLE_BUY_WAITS = frozenset({"BUY_CONFIRMATION_WAIT", "BUY_WINDOW_WAIT"})


def is_terminal_buy_block(reason: object) -> bool:
    """Only deliberate confirmation/window waits may mature without a new signal."""
    normalized = str(reason or "").strip().upper()
    return bool(normalized) and normalized not in RETRYABLE_BUY_WAITS


def _parse_signal_time(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.max.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def decision_signal_time(decision: StrategyDecision) -> str:
    details = decision.details if isinstance(decision.details, dict) else {}
    window = details.get("buy_window") if isinstance(details.get("buy_window"), dict) else {}
    confirmation = (
        details.get("buy_confirmation")
        if isinstance(details.get("buy_confirmation"), dict)
        else {}
    )
    return str(
        window.get("signal_time")
        or confirmation.get("signal_time")
        or details.get("signal_time")
        or confirmation.get("observed_time")
        or ""
    )


@dataclass(frozen=True, slots=True)
class RankedDecision:
    symbol: str
    decision: StrategyDecision
    watchlist_priority: int
    signal_time: str
    is_priority: bool = False


@dataclass(frozen=True, slots=True)
class BuyAttempt:
    """Planner result consumed by the coordinator without depending on UI types."""

    payload: Any = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CoordinatedBuy:
    candidate: RankedDecision
    payload: Any = None
    blocked_by: str = ""


def rank_buy_decisions(
    decisions: Mapping[str, StrategyDecision],
    watchlist: Iterable[str],
    priority_symbols: Iterable[str] = (),
) -> list[RankedDecision]:
    """Rank Priority BUYs first, then by signal time and watchlist order."""
    priority = {
        str(symbol or "").strip().upper(): index
        for index, symbol in enumerate(watchlist)
    }
    promoted = {
        str(symbol or "").strip().upper()
        for symbol in priority_symbols
        if str(symbol or "").strip()
    }
    fallback = len(priority)
    rows = [
        RankedDecision(
            symbol=str(symbol or "").strip().upper(),
            decision=decision,
            watchlist_priority=priority.get(str(symbol or "").strip().upper(), fallback),
            signal_time=decision_signal_time(decision),
            is_priority=str(symbol or "").strip().upper() in promoted,
        )
        for symbol, decision in decisions.items()
        if decision.action == "BUY" and str(decision.signal or "").upper() == "BUY"
    ]
    return sorted(
        rows,
        key=lambda row: (
            0 if row.is_priority else 1,
            _parse_signal_time(row.signal_time),
            row.watchlist_priority,
            row.symbol,
        ),
    )


def coordinate_buy_decisions(
    decisions: Mapping[str, StrategyDecision],
    watchlist: Iterable[str],
    allocator: "BuySlotAllocator",
    *,
    bot_enabled: bool,
    plan: Callable[[RankedDecision], BuyAttempt],
    disabled_reason: str = "BOT_OFF",
    priority_symbols: Iterable[str] = (),
) -> list[CoordinatedBuy]:
    """Arbitrate every current BUY without importing dashboard or broker code.

    Slot reservation happens before local planning. A local validation failure
    releases it immediately so the next signal can still be considered.
    """
    output: list[CoordinatedBuy] = []
    for candidate in rank_buy_decisions(decisions, watchlist, priority_symbols):
        if candidate.symbol in allocator.occupied_symbols:
            continue
        if not bot_enabled:
            output.append(CoordinatedBuy(
                candidate,
                blocked_by=str(disabled_reason or "BOT_OFF").upper(),
            ))
            continue
        if not allocator.reserve(candidate.symbol, bypass_limit=candidate.is_priority):
            output.append(CoordinatedBuy(candidate, blocked_by="MAX_POSITIONS"))
            continue
        attempt = plan(candidate)
        if attempt.payload is None:
            allocator.release(candidate.symbol)
            output.append(CoordinatedBuy(
                candidate,
                blocked_by=str(attempt.reason or "LOCAL_VALIDATION_FAILED"),
            ))
            continue
        output.append(CoordinatedBuy(candidate, payload=attempt.payload))
    return output


def _position_symbol(row: Mapping[str, Any]) -> str:
    try:
        quantity = int(float(row.get("openQuantity", row.get("quantity", row.get("volume", 0))) or 0))
    except (TypeError, ValueError):
        quantity = 0
    return str(row.get("symbol", "") or "").strip().upper() if quantity > 0 else ""


class BuySlotAllocator:
    """Reserve unique symbol slots before order intents are persisted."""

    def __init__(
        self,
        max_positions: int,
        occupied_symbols: Iterable[str] = (),
        bypass_symbols: Iterable[str] = (),
    ):
        self.max_positions = max(1, int(max_positions or 1))
        self.occupied_symbols = {
            str(symbol or "").strip().upper()
            for symbol in occupied_symbols
            if str(symbol or "").strip()
        }
        self.bypass_symbols = {
            str(symbol or "").strip().upper()
            for symbol in bypass_symbols
            if str(symbol or "").strip().upper() in self.occupied_symbols
        }

    @classmethod
    def from_runtime(
        cls,
        max_positions: int,
        positions: Iterable[Mapping[str, Any]],
        intents: Iterable[OrderIntent],
        execution_mode: str,
        *,
        bot_symbols: Iterable[str] | None = None,
        priority_symbols: Iterable[str] = (),
    ) -> "BuySlotAllocator":
        mode = str(execution_mode or "PAPER").strip().upper()
        occupied = (
            {
                str(symbol or "").strip().upper()
                for symbol in bot_symbols
                if str(symbol or "").strip()
            }
            if bot_symbols is not None
            else {
                _position_symbol(row)
                for row in positions
                if str(row.get("source", "") or "").strip().upper() == "BOT"
            }
        )
        occupied.discard("")
        occupied.update(
            intent.symbol
            for intent in intents
            if intent.side == "BUY"
            and intent.source == "BOT"
            and intent.execution_mode == mode
            and str(intent.status or "").upper() not in FINAL_STATUSES
        )
        priority = {
            str(symbol or "").strip().upper()
            for symbol in priority_symbols
            if str(symbol or "").strip()
        }
        return cls(max_positions, occupied, occupied & priority)

    @property
    def used(self) -> int:
        return len(self.occupied_symbols - self.bypass_symbols)

    @property
    def available(self) -> int:
        return max(0, self.max_positions - self.used)

    def reserve(self, symbol: str, *, bypass_limit: bool = False) -> bool:
        normalized = str(symbol or "").strip().upper()
        if (
            not normalized
            or normalized in self.occupied_symbols
            or (self.available <= 0 and not bypass_limit)
        ):
            return False
        self.occupied_symbols.add(normalized)
        if bypass_limit:
            self.bypass_symbols.add(normalized)
        return True

    def release(self, symbol: str) -> None:
        normalized = str(symbol or "").strip().upper()
        self.occupied_symbols.discard(normalized)
        self.bypass_symbols.discard(normalized)
