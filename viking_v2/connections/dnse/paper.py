from __future__ import annotations

from datetime import datetime, timedelta
import time
from pathlib import Path
from typing import Any, Callable

from ... import config
from ...models import BrokerOrderResult, OrderIntent
from ...trading.market import VN_TZ, stock_is_sellable_after_settlement
from ...trading.portfolio import available_to_sell, board_price, validate_quantity
from ...storage import AtomicJSONStore


def _next_business_day(value: datetime, days: int = 2, working_dates: list[str] | None = None) -> datetime:
    if working_dates is not None:
        candidates = []
        current = value.date()
        for raw in working_dates:
            try:
                parsed = datetime.strptime(str(raw)[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if parsed.date() > current:
                candidates.append(parsed)
        candidates.sort()
        if len(candidates) >= days:
            return candidates[days - 1]
        raise ValueError("Lịch giao dịch không đủ để xác định ngày T+2.")
    result = value
    added = 0
    while added < days:
        result += timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    return result


class PaperBroker:
    """Persistent, deliberately simple PAPER adapter matching the legacy fill model."""

    def __init__(
        self,
        path: str | Path,
        *,
        initial_balance: float = 100_000_000.0,
        tick_provider: Callable[[str], dict[str, Any] | None] | None = None,
        now: Callable[[], float] = time.time,
        working_dates_provider: Callable[[], list[str]] | None = None,
        fee_rates: Callable[[], tuple[float, float, float]] | None = None,
    ):
        # Buy fee, sell fee, sell tax as fractions.  Reading them through a
        # callable keeps the paper book in step when the rates are edited.
        self.fee_rates = fee_rates or (
            lambda: (config.PAPER_BUY_FEE_RATE, config.PAPER_SELL_FEE_RATE, config.PAPER_SELL_TAX_RATE)
        )
        self.store = AtomicJSONStore(path, default=lambda: self._empty(initial_balance))
        self.initial_balance = float(initial_balance)
        self.tick_provider = tick_provider
        self._now = now
        self.working_dates_provider = working_dates_provider

    @staticmethod
    def _empty(balance: float) -> dict[str, Any]:
        return {
            "cash": float(balance),
            "initial_balance": float(balance),
            "next_id": 1,
            "positions": [],
            "orders": [],
            "realized_pnl": 0.0,
        }

    def reset(self, balance: float | None = None) -> dict[str, Any]:
        state = self._empty(float(self.initial_balance if balance is None else balance))
        self.store.write(state)
        return state

    def _state(self) -> dict[str, Any]:
        state = self.store.read()
        if not isinstance(state, dict):
            state = self._empty(self.initial_balance)
        state.setdefault("positions", [])
        state.setdefault("orders", [])
        self._refresh_settlement(state)
        return state

    def _refresh_settlement(self, state: dict[str, Any]) -> None:
        now = datetime.fromtimestamp(self._now(), VN_TZ)
        changed = False
        for position in state.get("positions", []):
            settle = str(position.get("settleDate", "") or "")[:10]
            if settle and int(position.get("tradeQuantity", 0) or 0) <= 0:
                try:
                    if stock_is_sellable_after_settlement(settle, now):
                        position["tradeQuantity"] = int(position.get("openQuantity", 0) or 0)
                        changed = True
                except ValueError:
                    pass
        if changed:
            self.store.write(state)

    def _price(self, intent: OrderIntent) -> float:
        if intent.order_type == "LO" and intent.limit_price > 0:
            return float(intent.limit_price)
        tick = self.tick_provider(intent.symbol) if self.tick_provider else None
        if not isinstance(tick, dict):
            return 0.0
        if intent.side == "BUY":
            return float(tick.get("ask", tick.get("price", 0.0)) or 0.0)
        return float(tick.get("bid", tick.get("price", 0.0)) or 0.0)

    def get_balance(self) -> dict[str, Any]:
        state = self._state()
        changed = False
        for row in state["positions"]:
            tick = self.tick_provider(str(row.get("symbol", "") or "")) if self.tick_provider else None
            if not isinstance(tick, dict):
                continue
            marked = board_price(
                tick.get("price", tick.get("lastPrice", tick.get("matchPrice", 0.0)))
            )
            if marked > 0 and marked != float(row.get("marketPrice", 0.0) or 0.0):
                row["marketPrice"] = marked
                changed = True
        if changed:
            self.store.write(state)
        market_value = sum(
            float(row.get("marketPrice", row.get("costPrice", 0.0)) or 0.0)
            * int(row.get("openQuantity", 0) or 0)
            * 1000.0
            for row in state["positions"]
        )
        cash = float(state.get("cash", 0.0) or 0.0)
        return {
            "stock": {"totalCash": cash, "availableCash": cash, "totalDebt": 0.0},
            "paper": True,
            "equity": cash + market_value,
            "realizedPnl": float(state.get("realized_pnl", 0.0) or 0.0),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        return list(self._state()["positions"])

    def get_orders(self) -> list[dict[str, Any]]:
        return list(self._state()["orders"])

    def place_order(self, intent: OrderIntent) -> BrokerOrderResult:
        valid, reason, quantity = validate_quantity(intent.quantity)
        if not valid:
            return BrokerOrderResult(False, "REJECTED", message=reason, error="INVALID_QUANTITY")
        state = self._state()
        price = self._price(intent)
        if price <= 0:
            return BrokerOrderResult(False, "REJECTED", message="PAPER không có giá hợp lệ.", error="NO_MARKET_PRICE")
        gross = price * quantity * 1000.0
        order_id = f"PAPER-{int(state.get('next_id', 1))}"
        state["next_id"] = int(state.get("next_id", 1)) + 1
        if intent.side == "BUY":
            fee = gross * self.fee_rates()[0]
            cash = float(state.get("cash", 0.0) or 0.0)
            if cash < gross + fee:
                return BrokerOrderResult(False, "REJECTED", message="PAPER không đủ tiền.", error="INSUFFICIENT_CASH")
            working_dates = self.working_dates_provider() if self.working_dates_provider else None
            try:
                settle = _next_business_day(
                    datetime.fromtimestamp(self._now(), VN_TZ), working_dates=working_dates,
                ).strftime("%Y-%m-%d")
            except ValueError as exc:
                return BrokerOrderResult(
                    False,
                    "REJECTED",
                    message=str(exc),
                    error="TRADING_CALENDAR_UNAVAILABLE",
                )
            state["cash"] = cash - gross - fee
            state["positions"].append(
                {
                    "positionId": order_id,
                    "symbol": intent.symbol,
                    "side": "NB",
                    "status": "OPEN",
                    "openQuantity": quantity,
                    "tradeQuantity": 0,
                    "costPrice": price,
                    "marketPrice": price,
                    "settleDate": settle,
                    "paper": True,
                    "tradeId": intent.trade_id,
                    "source": intent.source,
                    "buyFee": fee,
                }
            )
        else:
            buy_rate, sell_rate, tax_rate = self.fee_rates()
            fee = gross * sell_rate
            tax = gross * tax_rate
            sellable = available_to_sell(state["positions"], intent.symbol)
            if sellable < quantity:
                return BrokerOrderResult(False, "REJECTED", message=f"PAPER chỉ có {sellable} CP bán được.", error="INSUFFICIENT_SELLABLE")
            remaining = quantity
            survivors: list[dict[str, Any]] = []
            realized = 0.0
            for row in state["positions"]:
                if remaining <= 0 or str(row.get("symbol", "")).upper() != intent.symbol:
                    survivors.append(row)
                    continue
                available = int(row.get("tradeQuantity", 0) or 0)
                open_before = max(0, int(row.get("openQuantity", 0) or 0))
                take = min(available, remaining)
                row["tradeQuantity"] = available - take
                row["openQuantity"] = open_before - take
                allocated_buy_fee = float(row.get("buyFee", 0.0) or 0.0) * (
                    take / max(1, open_before)
                )
                row["buyFee"] = max(0.0, float(row.get("buyFee", 0.0) or 0.0) - allocated_buy_fee)
                realized += (price - float(row.get("costPrice", price) or price)) * take * 1000.0 - allocated_buy_fee
                remaining -= take
                if int(row.get("openQuantity", 0) or 0) > 0:
                    survivors.append(row)
            state["positions"] = survivors
            state["cash"] = float(state.get("cash", 0.0) or 0.0) + gross - fee - tax
            realized -= fee + tax
            state["realized_pnl"] = float(state.get("realized_pnl", 0.0) or 0.0) + realized
        order = {
            "orderId": order_id,
            "symbol": intent.symbol,
            "side": "NB" if intent.side == "BUY" else "NS",
            "orderType": intent.order_type,
            "orderStatus": "Filled",
            "price": price,
            "averagePrice": price,
            "quantity": quantity,
            "fillQuantity": quantity,
            "leaveQuantity": 0,
            "marketType": "STOCK",
            "paper": True,
            "createdAt": self._now(),
            "fee": fee,
            "tax": tax if intent.side == "SELL" else 0.0,
        }
        state["orders"].append(order)
        self.store.write(state)
        return BrokerOrderResult(True, "FILLED", order_id=order_id, message="PAPER_FILLED", status_code=200, raw=order)

    def cancel_order(self, order_id: str) -> BrokerOrderResult:
        return BrokerOrderResult(False, "REJECTED", order_id=order_id, message="PAPER đã fill ngay; không có lệnh để hủy.", error="PAPER_ALREADY_FILLED")
