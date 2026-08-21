from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .. import config
from ..config import STOCK_ROUND_LOT
from .market import action_for_symbol

if TYPE_CHECKING:
    from ..rules.state import RuleStateStore
    from .orders import OrderQueue
    from .state import TradeStateStore


def round_lot_down(quantity: Any, lot: int = STOCK_ROUND_LOT) -> int:
    try:
        value = max(0, int(float(quantity or 0)))
    except (TypeError, ValueError):
        return 0
    lot = max(1, int(lot or STOCK_ROUND_LOT))
    return value - (value % lot)

def validate_quantity(quantity: Any, lot: int = STOCK_ROUND_LOT) -> tuple[bool, str, int]:
    normalized = round_lot_down(quantity, lot)
    if normalized < lot:
        return False, f"Khối lượng CKCS tối thiểu {lot} và phải là bội số {lot}.", normalized
    if normalized != int(float(quantity or 0)):
        return False, f"Khối lượng CKCS phải là bội số {lot}.", normalized
    return True, "", normalized

def price_in_band(price: float, floor_price: float, ceiling_price: float) -> bool:
    price = float(price or 0.0)
    floor_price = float(floor_price or 0.0)
    ceiling_price = float(ceiling_price or 0.0)
    if price <= 0:
        return False
    if floor_price > 0 and price < floor_price - 1e-9:
        return False
    if ceiling_price > 0 and price > ceiling_price + 1e-9:
        return False
    return True

def available_to_sell(positions: Iterable[Any], symbol: str) -> int:
    target = str(symbol or "").upper()
    total = 0
    for position in positions or []:
        raw = position if isinstance(position, dict) else getattr(position, "raw", {}) or {}
        pos_symbol = str(raw.get("symbol") or getattr(position, "symbol", "") or "").upper()
        if pos_symbol != target:
            continue
        value = raw.get("tradeQuantity")
        if value is None:
            value = raw.get("openQuantity", raw.get("quantity", 0))
        try:
            total += max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            continue
    return total

def stock_exposure_limit(nav: float, exposure: float) -> float:
    return max(0.0, float(nav or 0.0)) * min(1.0, max(0.0, float(exposure or 0.0)))

def order_budget(
    *,
    nav: float,
    exposure: float,
    max_positions: int,
    current_stock_value: float,
    pending_buy_value: float,
    available_cash: float,
    fee_rate: float = 0.0,
) -> float:
    """Cash the account can actually commit, fee already taken out.

    Spending every last đồng on shares leaves nothing to pay the fee with, so
    the ticket costs more than the balance and the broker rejects it.  Taking
    the fee off here means nothing downstream has to think about it.
    """
    spendable = max(0.0, available_cash) / (1.0 + max(0.0, float(fee_rate or 0.0)))
    limit = stock_exposure_limit(nav, exposure)
    remaining = max(0.0, limit - max(0.0, current_stock_value) - max(0.0, pending_buy_value))
    per_symbol = limit / max(1, int(max_positions or 1))
    return max(0.0, min(per_symbol, remaining, spendable))

def affordable_quantity(budget_vnd: float, price_board: float) -> int:
    price_vnd = max(0.0, float(price_board or 0.0)) * 1000.0
    if price_vnd <= 0:
        return 0
    return round_lot_down(max(0.0, float(budget_vnd or 0.0)) / price_vnd)


@dataclass(frozen=True, slots=True)
class BuySizing:
    quantity: int
    budget: float
    minimum_value: float
    used_minimum: bool
    reason: str = ""


def size_buy_order(
    *,
    budget_vnd: float,
    price_board: float,
    available_cash: float = 0.0,
    nav: float = 0.0,
    force_min_lot_enabled: bool = False,
    lot: int = STOCK_ROUND_LOT,
) -> BuySizing:
    """Single sizing source used by rule planning and every UI preview.

    The budget handed in is already net of the buy fee; see ``order_budget``.
    """
    budget = max(0.0, float(budget_vnd or 0.0))
    price = max(0.0, float(price_board or 0.0))
    minimum_lot = max(1, int(lot or STOCK_ROUND_LOT))
    minimum_value = price * minimum_lot * 1000.0
    if price <= 0:
        return BuySizing(0, budget, 0.0, False, "NO_EXECUTION_PRICE")
    quantity = affordable_quantity(budget, price)
    used_minimum = bool(
        quantity <= 0
        and force_min_lot_enabled
        and float(available_cash or 0.0) >= minimum_value
        and float(nav or 0.0) >= minimum_value
    )
    if used_minimum:
        quantity = minimum_lot
    reason = "" if quantity > 0 else "INSUFFICIENT_BUDGET_FOR_ROUND_LOT"
    return BuySizing(quantity, budget, minimum_value, used_minimum, reason)

def _number(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                continue
    return 0.0

def position_quantity(row: dict[str, Any]) -> int:
    return max(0, int(_number(row, "openQuantity", "quantity", "volume")))

def position_price(row: dict[str, Any]) -> float:
    return _number(row, "marketPrice", "currentPrice", "price", "costPrice", "averagePrice")

def position_cost(row: dict[str, Any]) -> float:
    return _number(row, "costPrice", "averagePrice", "avgPrice", "price")

def stock_value(positions: Iterable[dict[str, Any]]) -> float:
    return sum(position_quantity(row) * position_price(row) * 1000.0 for row in positions or [])

def cash_from_balance(balance: dict[str, Any]) -> float:
    stock = balance.get("stock") if isinstance(balance.get("stock"), dict) else {}
    return _number(stock, "availableCash", "totalCash") or _number(balance, "availableCash", "cash", "balance")

def nav_from_balance(balance: dict[str, Any], positions: Iterable[dict[str, Any]]) -> float:
    explicit = _number(balance, "equity", "nav", "netAssetValue", "totalAsset")
    return explicit if explicit > 0 else cash_from_balance(balance) + stock_value(positions)

class PortfolioContextBuilder:
    def __init__(self, queue: OrderQueue, trades: TradeStateStore, rule_state: RuleStateStore,
                 buy_fee_rate: Callable[[], float] | None = None):
        # Read through a callable so editing the rate in the RULE popup
        # takes effect without rebuilding anything.
        self.buy_fee_rate = buy_fee_rate or (lambda: 0.0)
        self.queue = queue
        self.trades = trades
        self.rule_state = rule_state

    def build(
        self,
        symbol: str,
        *,
        execution_mode: str,
        balance: dict[str, Any],
        positions: list[dict[str, Any]],
        tick: dict[str, Any],
        exposure: float,
        max_positions: int,
        no_compound_enabled: bool = True,
        corporate_actions: list[dict[str, Any]] | None = None,
        working_dates: list[str] | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        mode = str(execution_mode or "PAPER").upper()
        rows = [row for row in positions or [] if isinstance(row, dict)]
        nav = nav_from_balance(balance or {}, rows)
        cash = cash_from_balance(balance or {})
        current_value = stock_value(rows)
        pending_buys = [
            item for item in self.queue.list_all()
            if item.side == "BUY"
            and item.execution_mode == mode
            # UNKNOWN may already exist at DNSE, so its slot and capital stay
            # reserved until broker reconciliation resolves the request.
            and item.status not in {"FILLED", "CANCELLED", "EXPIRED", "FAILED", "REJECTED"}
        ]
        pending_value = 0.0
        for item in pending_buys:
            price = item.limit_price or float(tick.get("ask", tick.get("price", 0.0)) or 0.0)
            pending_value += max(0, item.remaining_quantity) * max(0.0, price) * 1000.0
        budget = order_budget(
            nav=nav,
            exposure=exposure,
            max_positions=max_positions,
            current_stock_value=current_value,
            pending_buy_value=pending_value,
            available_cash=cash,
            fee_rate=self.buy_fee_rate(),
        )
        if no_compound_enabled:
            budget = self.trades.capital_available(symbol, mode, budget)
        matching = next((row for row in rows if str(row.get("symbol", "") or "").upper() == symbol and position_quantity(row) > 0), None)
        active_trade = self.trades.active_for(symbol, mode)
        context: dict[str, Any] = {
            "nav": nav,
            "available_cash": cash,
            "current_stock_value": current_value,
            "pending_buy_value": pending_value,
            "available_capital": budget,
            "order_budget": budget,
            "open_positions": len({str(row.get("symbol", "") or "").upper() for row in rows if position_quantity(row) > 0}),
            "pending_buy": bool(self.queue.find_active(symbol, side="BUY", execution_mode=mode)),
            "loss_streak": self.trades.loss_streak(symbol, mode),
        }
        action = action_for_symbol(
            corporate_actions or [],
            symbol,
            today=today or date.today(),
            working_dates=working_dates,
        )
        if action:
            context["corporate_action"] = action
            context["corporate_action_blocked"] = bool(action.get("blocks_entry", False))
        if not matching:
            return context
        quantity = position_quantity(matching)
        avg_price = position_cost(matching)
        current_price = float(tick.get("price", position_price(matching)) or position_price(matching))
        trade_id = active_trade.id if active_trade else str(
            matching.get("tradeId", matching.get("positionId", matching.get("id", f"{mode}:{symbol}")))
            or f"{mode}:{symbol}"
        )
        managed = bool(active_trade) or str(matching.get("source", "") or "").upper() == "BOT"
        profit_pct = ((current_price / avg_price) - 1.0) * 100.0 if avg_price > 0 and current_price > 0 else 0.0
        realized_net = (
            float(active_trade.net_pnl)
            if active_trade
            else -abs(_number(matching, "buyFee", "fee"))
        )
        unrealized = (current_price - avg_price) * quantity * 1000.0
        estimated_exit_cost = 0.0
        if mode == "PAPER" and current_price > 0 and quantity > 0:
            exit_value = current_price * quantity * 1000.0
            estimated_exit_cost = exit_value * (
                config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE
            )
        current_net_pnl = realized_net + unrealized - estimated_exit_cost
        metrics = self.rule_state.update_position_metrics(
            symbol,
            trade_id,
            profit_pct=profit_pct,
            net_pnl=current_net_pnl,
            market_price=current_price,
            close_price=float(tick.get("daily_close", current_price) or current_price),
            closed_bar=bool(tick.get("daily_bar_closed", False)),
        ) if trade_id else {}
        context["position"] = {
            "quantity": quantity,
            "avg_price": avg_price,
            "current_price": current_price,
            "trade_quantity": max(0, int(_number(matching, "tradeQuantity"))),
            "trade_id": trade_id,
            "managed_by_bot": managed,
            "managed_by_app": managed,
            "is_reentry": bool(active_trade.is_reentry) if active_trade else False,
            "sl_mode": str(active_trade.sl_mode) if active_trade else "DEFAULT",
            "sl_value": float(active_trade.sl_value) if active_trade else 0.0,
            "tp_mode": str(active_trade.tp_mode) if active_trade else "NONE",
            "tp_value": float(active_trade.tp_value) if active_trade else 0.0,
            "em_modes": list(active_trade.em_modes) if active_trade else [],
            "current_net_pnl": current_net_pnl,
            "estimated_exit_cost": estimated_exit_cost,
            **metrics,
        }
        context["position_quantity"] = quantity
        context["trade_id"] = trade_id
        if action and action.get("active"):
            context["corporate_action_warning"] = True
        return context
