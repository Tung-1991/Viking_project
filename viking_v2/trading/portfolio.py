from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
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
    except (TypeError, ValueError, OverflowError):
        return 0
    lot = max(1, int(lot or STOCK_ROUND_LOT))
    return value - (value % lot)


def sell_quantity_for_fraction(
    remaining: Any,
    fraction: Any,
    lot: int = STOCK_ROUND_LOT,
) -> int:
    """Return a tradable partial-sale quantity, with one lot as the minimum.

    A configured 33% exit must still do something for a 100–300 share
    position; simple floor rounding turns 300 × 33% into zero shares.
    """
    available = max(0, int(float(remaining or 0)))
    share = min(1.0, max(0.0, float(fraction or 0.0)))
    minimum = max(1, int(lot or STOCK_ROUND_LOT))
    if available < minimum or share <= 0:
        return 0
    if share >= 1.0:
        return round_lot_down(available, minimum)
    return min(round_lot_down(available, minimum), max(
        minimum,
        round_lot_down(available * share, minimum),
    ))

def validate_quantity(quantity: Any, lot: int = STOCK_ROUND_LOT) -> tuple[bool, str, int]:
    normalized = round_lot_down(quantity, lot)
    try:
        supplied = float(quantity or 0)
    except (TypeError, ValueError):
        supplied = 0
    if not math.isfinite(supplied) or normalized < lot:
        return False, f"Khối lượng CKCS tối thiểu {lot} và phải là bội số {lot}.", normalized
    if normalized != supplied:
        return False, f"Khối lượng CKCS phải là bội số {lot}.", normalized
    return True, "", normalized

def price_in_band(price: float, floor_price: float, ceiling_price: float) -> bool:
    price = float(price or 0.0)
    floor_price = float(floor_price or 0.0)
    ceiling_price = float(ceiling_price or 0.0)
    if not all(math.isfinite(value) for value in (price, floor_price, ceiling_price)) or price <= 0:
        return False
    if floor_price > 0 and price < floor_price - 1e-9:
        return False
    if ceiling_price > 0 and price > ceiling_price + 1e-9:
        return False
    return True


def board_price(value: Any) -> float:
    """Return the canonical internal stock price (thousand-VND board units).

    DNSE market-data endpoints use board prices such as ``23.35`` while the
    trading/account endpoints use VND such as ``23350``.  Everything outside
    the DNSE adapter uses board prices so PnL, sizing and protection rules can
    never compare values with different units.
    """
    try:
        price = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(price):
        return 0.0
    return price / 1000.0 if price >= 1000.0 else price


def account_price(row: dict[str, Any], value: Any) -> float:
    """Prefer explicit trading-adapter units over legacy price heuristics."""
    if row.get("price_unit") == "VND":
        try:
            price = float(value or 0)
            return max(0.0, price / 1000) if math.isfinite(price) else 0.0
        except (ValueError, TypeError):
            return 0.0
    return board_price(value)


def dnse_price(value: Any) -> float:
    """Convert an internal board price to the VND unit required by trading APIs."""
    return board_price(value) * 1000.0

def available_to_sell(positions: Iterable[Any], symbol: str) -> int:
    target = str(symbol or "").upper()
    total = 0
    for position in positions or []:
        raw = position if isinstance(position, dict) else getattr(position, "raw", {}) or {}
        pos_symbol = str(raw.get("symbol") or getattr(position, "symbol", "") or "").upper()
        if pos_symbol != target:
            continue
        # ``openQuantity`` includes stock which may still be waiting for T+2.
        # Selling must fail closed if the broker omits its explicit sellable
        # quantity; guessing here could turn an API schema issue into an
        # illegal early sell request on a REAL account.
        value = raw.get("tradeQuantity", 0)
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


def bot_slot_state(max_positions: int, priority_symbols: Iterable[str], occupied_symbols: Iterable[str], symbol: str = "") -> dict[str, Any]:
    """Priority owns slots inside the total quota, including while unoccupied."""
    maximum = max(1, int(max_positions))
    priority = {str(value).strip().upper() for value in priority_symbols if str(value).strip()}
    occupied = {str(value).strip().upper() for value in occupied_symbols if str(value).strip()}
    target = str(symbol).strip().upper()
    regular_used = len(occupied - priority)
    regular_limit = max(0, maximum - len(priority))
    allowed = (len(priority) <= maximum and len(occupied) < maximum and target not in occupied
               and (target in priority or regular_used < regular_limit))
    return {"used": len(occupied), "reserved": len(priority - occupied),
            "regular_used": regular_used, "regular_limit": regular_limit, "entry_available": allowed}


def priority_capital_budget(
    symbol: str, *, total: float, symbols: Iterable[str], allocations: dict[str, Any],
    budget: float, account_room: float, cash: float, fee_rate: float,
    holding_costs: dict[str, float], pending_costs: dict[str, float],
    pending_cash: float = 0.0,
) -> dict[str, Any]:
    """Fixed Priority envelopes, including fees; unused money is never lent out.

    Holdings consume acquisition capital, not fluctuating market value. The
    latter is still checked separately through Phase 1's account_room.
    """
    priority = {str(value).upper() for value in symbols}
    rows = config.normalize_priority_allocations(allocations, priority)
    try:
        config.validate_priority_capital(total, priority, rows)
    except ValueError:
        return {"budget": 0.0, "minimum_room": 0.0, "reason": "INVALID_PRIORITY_CAPITAL"}
    fee_factor = 1.0 + max(0.0, fee_rate)
    limits = {value: rows.get(value, {}).get("limit_vnd", 0.0) for value in priority}
    committed = {value: max(0.0, holding_costs.get(value, 0.0))
                 + max(0.0, pending_costs.get(value, 0.0)) for value in priority}
    unassigned = max(0.0, total - sum(limits.values()))
    reserved = unassigned + sum(max(0.0, limits[value] - committed[value])
                                for value in priority if value != symbol)
    limit = limits.get(symbol, 0.0)
    use_pct = rows.get(symbol, {}).get("use_pct", 100.0)
    per_order_limit = limit * use_pct / 100.0
    max_orders = config.priority_max_orders(rows.get(symbol, {}).get("max_orders", 1))
    buy_limit = config.priority_buy_limit(rows.get(symbol, {}))
    if symbol in priority:
        # Even the current symbol cannot spend its explicit savings.
        reserved += max(0.0, limit - max(buy_limit, committed[symbol]))
        symbol_room = min(per_order_limit, max(0.0, buy_limit - committed[symbol])) / fee_factor
    else:
        symbol_room = max(0.0, budget)
    cash_room = max(0.0, cash - pending_cash - reserved) / fee_factor
    allowed = max(0.0, min(symbol_room, account_room, cash_room))
    return {
        "budget": allowed, "minimum_room": allowed,
        "limit_vnd": limit, "use_pct": use_pct, "buy_limit_vnd": buy_limit,
        "per_order_limit_vnd": per_order_limit, "max_orders": max_orders,
        "committed_vnd": committed.get(symbol, 0.0), "reserved_cash": reserved,
        "holding_cost_vnd": max(0.0, holding_costs.get(symbol, 0.0)),
        "pending_cost_vnd": max(0.0, pending_costs.get(symbol, 0.0)),
        "reason": "" if allowed > 0 else "PRIORITY_CAPITAL_LIMIT",
    }


def priority_entry_orders(
    symbol: str, mode: str, trades: TradeStateStore, queue: OrderQueue,
    enabled: bool, symbols: Iterable[str], allocations: dict[str, Any],
    *, exclude_intent_id: str = "",
) -> dict[str, Any]:
    """Count entries in the current position, independently for each book.

    Filled IDs survive queue cleanup/restart. Old positions without IDs count
    as at least one entry. A partially filled pending order is counted once.
    """
    cycle = trades.active_for(symbol, mode)
    maximum = config.priority_max_orders(allocations.get(symbol, {}).get("max_orders", 1)) if enabled and symbol in symbols else 1
    intents = [item for item in queue.list_all() if item.symbol == symbol and item.execution_mode == mode]
    bot_cycle = cycle if cycle and cycle.source == "BOT" else None
    filled = set(bot_cycle.entry_order_ids) if bot_cycle else set()
    if bot_cycle and not filled:
        filled.update(item.id for item in intents if item.side == "BUY" and item.source == "BOT"
                      and item.trade_id == bot_cycle.id and item.filled_quantity > 0)
    used = max(len(filled), int(bool(bot_cycle and bot_cycle.entry_quantity > 0)))
    pending_buys = [item for item in intents if item.side == "BUY" and item.id != exclude_intent_id
                    and item.status not in {"FILLED", "CANCELLED", "EXPIRED", "FAILED", "REJECTED"}]
    pending = {item.id for item in pending_buys if item.source == "BOT"}
    # The legacy marker stands for the original filled order if its old row
    # is still working. Do not count that same partial fill twice.
    covered = filled | {item.id for item in intents if bot_cycle and item.source == "BOT"
                        and item.trade_id == bot_cycle.id and item.filled_quantity > 0}
    used += len(pending - covered)
    exiting = any(item.side == "SELL" and item.status not in {"FILLED", "CANCELLED", "EXPIRED", "FAILED", "REJECTED"} for item in intents)
    allowed = bool(enabled and symbol in symbols and maximum > 1 and cycle
                   and cycle.source == "BOT" and cycle.open_quantity > 0
                   and cycle.sold_quantity == 0 and not pending_buys and not exiting and used < maximum)
    return {"entry_orders_used": used, "entry_orders_max": maximum,
            "entry_orders_available": used < maximum, "scale_in_allowed": allowed}


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
    minimum_order_room_vnd: float | None = None,
    buy_fee_rate: float = 0.0,
    lot: int = STOCK_ROUND_LOT,
) -> BuySizing:
    """Single sizing source used by rule planning and every UI preview.

    The budget handed in is already net of the buy fee; see ``order_budget``.
    """
    budget = max(0.0, float(budget_vnd or 0.0))
    price = max(0.0, float(price_board or 0.0))
    minimum_lot = max(1, int(lot or STOCK_ROUND_LOT))
    minimum_value = price * minimum_lot * 1000.0
    minimum_total = minimum_value * (1.0 + max(0.0, float(buy_fee_rate or 0.0)))
    minimum_room = (
        max(0.0, float(minimum_order_room_vnd))
        if minimum_order_room_vnd is not None else max(0.0, float(nav or 0.0))
    )
    if price <= 0:
        return BuySizing(0, budget, 0.0, False, "NO_EXECUTION_PRICE")
    quantity = affordable_quantity(budget, price)
    used_minimum = bool(
        quantity <= 0
        and force_min_lot_enabled
        and float(available_cash or 0.0) >= minimum_total
        and minimum_room >= minimum_value
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
    return account_price(row, _number(row, "marketPrice", "currentPrice", "price", "costPrice", "averagePrice"))

def position_cost(row: dict[str, Any]) -> float:
    return account_price(row, _number(row, "costPrice", "averagePrice", "avgPrice", "price"))

def stock_value(positions: Iterable[dict[str, Any]]) -> float:
    return sum(position_quantity(row) * position_price(row) * 1000.0 for row in positions or [])

def cash_from_balance(balance: dict[str, Any]) -> float:
    stock = balance.get("stock") if isinstance(balance.get("stock"), dict) else {}
    if stock:
        return max(0.0, _number(stock, "availableCash", "cashAvailable"))
    return max(0.0, _number(balance, "availableCash", "cashAvailable", "cash"))

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
        priority_symbols: Iterable[str] = (),
        priority_capital_enabled: bool = False,
        priority_total_capital: float = 0.0,
        priority_allocations: dict[str, Any] | None = None,
        no_compound_enabled: bool = True,
        loss_lock_count: int = 3,
        loss_lock_hours: float = 24.0,
        loss_lock_mode: str = "TIMED",
        exclude_intent_id: str = "",
        now: float | None = None,
        corporate_actions: list[dict[str, Any]] | None = None,
        working_dates: list[str] | None = None,
        today: date | None = None,
        normal_t2_reset_enabled: bool = False,
        normal_arm_pct: float = 7.0,
        budget_only: bool = False,
        manual_buy: bool = False,
    ) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        priority_symbols = tuple(priority_symbols)
        mode = str(execution_mode or "PAPER").upper()
        rows = [row for row in positions or [] if isinstance(row, dict)]
        nav = nav_from_balance(balance or {}, rows)
        cash = cash_from_balance(balance or {})
        current_value = stock_value(rows)
        pending_buys = [
            item for item in self.queue.list_all()
            if item.side == "BUY"
            and item.id != exclude_intent_id
            and item.execution_mode == mode
            # UNKNOWN may already exist at DNSE, so its slot and capital stay
            # reserved until broker reconciliation resolves the request.
            and item.status not in {"FILLED", "CANCELLED", "EXPIRED", "FAILED", "REJECTED"}
        ]
        pending_value = 0.0
        pending_costs: dict[str, float] = {}
        pending_cash = 0.0
        symbol_pending_buys = [item for item in pending_buys if item.symbol == symbol and item.remaining_quantity > 0]
        pending_summary = {
            "symbol_pending_buy_count": len(symbol_pending_buys),
            "symbol_pending_buy_quantity": sum(item.remaining_quantity for item in symbol_pending_buys),
        }
        fee_rate = max(0.0, float(self.buy_fee_rate() or 0.0))
        for item in pending_buys:
            price = item.limit_price or float(item.details.get("reservation_price", 0) or 0)
            if not price and item.symbol == symbol:
                price = float(tick.get("ask", tick.get("price", 0.0)) or 0.0)
            if price:
                value = max(0, item.remaining_quantity) * max(0.0, price) * 1000.0
            elif item.entry_budget:
                value = max(0, item.entry_budget)
            else:
                # Unknown reservation is not free money. Wait for that order's
                # quote/price rather than borrowing another symbol's price.
                value = cash
            pending_value += value
            cost = value * (1.0 + fee_rate)
            pending_costs[item.symbol] = pending_costs.get(item.symbol, 0.0) + cost
            if not item.broker_order_id and not item.handed_off_at:
                pending_cash += cost
        budget = order_budget(
            nav=nav,
            exposure=exposure,
            max_positions=max_positions,
            current_stock_value=current_value,
            pending_buy_value=pending_value,
            available_cash=cash,
            fee_rate=self.buy_fee_rate(),
        )
        exposure_room = max(
            0.0,
            stock_exposure_limit(nav, exposure) - current_value - pending_value,
        )
        minimum_order_room = min(
            exposure_room,
            cash / (1.0 + max(0.0, float(self.buy_fee_rate() or 0.0))),
        )
        if manual_buy:
            # A typed MANUAL ticket keeps Priority envelopes, but does not
            # inherit BOT position quotas, P1 exposure or signal/loss locks.
            budget = max(0.0, cash - pending_cash) / (1.0 + fee_rate)
            minimum_order_room = budget
        priority_capital = {}
        if priority_capital_enabled or priority_symbols:
            holding_costs: dict[str, float] = {}
            for row in rows:
                value = str(row.get("symbol", "")).upper()
                cost = position_quantity(row) * (position_cost(row) or position_price(row)) * 1000.0 * (1.0 + fee_rate)
                holding_costs[value] = holding_costs.get(value, 0.0) + cost
            durable_costs: dict[str, float] = {}
            for cycle in self.trades.list_cycles():
                if cycle.execution_mode == mode and cycle.status == "OPEN" and cycle.open_quantity > 0:
                    durable_costs[cycle.symbol] = durable_costs.get(cycle.symbol, 0.0) + (
                        cycle.avg_entry_price * cycle.open_quantity * 1000.0 * (1.0 + fee_rate)
                        + (max(0.0, cycle.fees_paid - cycle.buy_notional * fee_rate)
                           if cycle.sold_quantity == 0 else 0.0)
                    )
                if (cycle.execution_mode == mode and cycle.status == "OPEN"
                        and cycle.open_quantity > 0 and cycle.sold_quantity == 0):
                    # Lowering today's fee must not erase fees already paid.
                    extra_fee = max(0.0, cycle.fees_paid - cycle.buy_notional * fee_rate)
                    holding_costs[cycle.symbol] = holding_costs.get(cycle.symbol, 0.0) + extra_fee
            if manual_buy:
                # A lagging broker position snapshot must not free capital
                # already spent by a confirmed local fill. Never add the same
                # holding twice when both snapshots contain it.
                for value, cost in durable_costs.items():
                    holding_costs[value] = max(holding_costs.get(value, 0.0), cost)
            envelope = stock_exposure_limit(nav, exposure) / max(1, max_positions) * (1.0 + fee_rate)
            allocations = (priority_allocations or {}) if priority_capital_enabled else {
                value: {"limit_vnd": envelope, "use_pct": 100.0} for value in priority_symbols}
            total = priority_total_capital if priority_capital_enabled else envelope * len(priority_symbols)
            priority_capital = priority_capital_budget(
                symbol, total=total, symbols=priority_symbols,
                allocations=allocations, budget=budget,
                account_room=minimum_order_room, cash=cash, fee_rate=fee_rate,
                holding_costs=holding_costs, pending_costs=pending_costs, pending_cash=pending_cash,
            )
            budget = priority_capital["budget"]
            minimum_order_room = priority_capital["minimum_room"]
        no_compound_limited = False
        if no_compound_enabled and not manual_buy:
            before_no_compound = budget
            budget = self.trades.capital_available(symbol, mode, budget)
            no_compound_limited = budget < before_no_compound
            minimum_order_room = min(
                minimum_order_room,
                self.trades.capital_available(symbol, mode, float("inf"))
                / (1.0 + max(0.0, float(self.buy_fee_rate() or 0.0))),
            )
        entry_orders = priority_entry_orders(
            symbol, mode, self.trades, self.queue, priority_capital_enabled,
            priority_symbols, priority_allocations or {}, exclude_intent_id=exclude_intent_id,
        )
        if budget_only:
            # UI sizing reads the same money rules without updating cooldowns,
            # settlement/protection state or evaluating a trading signal.
            return {
                "nav": nav, "available_cash": cash,
                "exposure": exposure, "exposure_room": exposure_room,
                "current_stock_value": current_value, "pending_buy_value": pending_value,
                "pending_buy_cash": pending_cash, "no_compound_limited": no_compound_limited,
                **pending_summary,
                "order_budget": budget, "available_capital": budget,
                "minimum_order_room": minimum_order_room, "buy_fee_rate": fee_rate,
                "priority_capital": priority_capital,
                "priority_capital_enabled": bool(priority_capital),
                "buy_budget_price": board_price(tick.get("ceiling_price", 0.0)) if priority_capital else 0.0,
                **entry_orders,
            }
        matching_rows = [
            row for row in rows
            if str(row.get("symbol", "") or "").upper() == symbol
            and position_quantity(row) > 0
        ]
        bot_pending_buys = [item for item in pending_buys if item.source == "BOT"]
        bot_open_symbols = {
            cycle.symbol
            for cycle in self.trades.list_cycles()
            if cycle.status == "OPEN"
            and cycle.execution_mode == mode
            and cycle.source == "BOT"
            and cycle.open_quantity > 0
        }
        priority_set = {
            str(value or "").strip().upper()
            for value in priority_symbols
            if str(value or "").strip()
        }
        active_trade = self.trades.active_for(symbol, mode)
        if active_trade and active_trade.loan_package_id:
            matching_rows = [row for row in matching_rows if str(row.get("loanPackageId", "")) == active_trade.loan_package_id]
        if active_trade and sum(position_quantity(row) for row in matching_rows) < active_trade.open_quantity:
            # A lagging/incomplete position snapshot must not authorize an add.
            entry_orders["scale_in_allowed"] = False
        active_loss_streak = self.trades.active_loss_streak(
            symbol,
            mode,
            threshold=loss_lock_count,
            lock_hours=loss_lock_hours,
            lock_mode=loss_lock_mode,
            now=now,
        )
        slots = bot_slot_state(max_positions, priority_set,
                               bot_open_symbols | {item.symbol for item in bot_pending_buys}, symbol)
        if entry_orders["scale_in_allowed"]:
            # Adding to an existing symbol uses its existing slot, not a fifth code.
            slots["entry_available"] = (len(priority_set) <= max_positions and
                                       len(bot_open_symbols | {item.symbol for item in bot_pending_buys}) <= max_positions)
        context: dict[str, Any] = {
            "nav": nav,
            "available_cash": cash,
            "current_stock_value": current_value,
            "pending_buy_value": pending_value,
            "available_capital": budget,
            "order_budget": budget,
            **pending_summary,
            "minimum_order_room": minimum_order_room,
            "buy_fee_rate": max(0.0, float(self.buy_fee_rate() or 0.0)),
            # max_positions is a BOT quota. MANUAL/EXTERNAL holdings still
            # consume cash/exposure above, but never consume a BOT slot.
            "open_positions": slots["used"],
            "entry_slot_available": slots["entry_available"],
            "priority_reserved": slots["reserved"],
            "pending_buy": bool(self.queue.find_active(symbol, side="BUY", execution_mode=mode)),
            **entry_orders,
            "loss_streak": active_loss_streak,
            "loss_blocked": symbol in self.trades.loss_blocks(mode),
            "priority_capital": priority_capital,
            "buy_budget_price": board_price(tick.get("ceiling_price", 0.0)) if priority_capital else 0.0,
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
        if not matching_rows:
            return context
        broker_quantity = sum(position_quantity(row) for row in matching_rows)
        weighted_cost = sum(
            position_quantity(row) * position_cost(row) for row in matching_rows
        )
        broker_avg_price = weighted_cost / broker_quantity if broker_quantity > 0 else 0.0
        fallback_price = next(
            (position_price(row) for row in matching_rows if position_price(row) > 0),
            0.0,
        )
        current_price = board_price(tick.get("price", fallback_price)) or fallback_price
        first_matching = matching_rows[0]
        trade_id = active_trade.id if active_trade else str(
            first_matching.get(
                "tradeId",
                first_matching.get("positionId", first_matching.get("id", f"{mode}:{symbol}")),
            )
            or f"{mode}:{symbol}"
        )
        managed = bool(active_trade) or any(
            str(row.get("source", "") or "").upper() == "BOT" for row in matching_rows
        )
        quantity = (
            min(broker_quantity, active_trade.open_quantity)
            if active_trade and active_trade.open_quantity > 0
            else broker_quantity
        )
        avg_price = (
            active_trade.avg_entry_price
            if active_trade and active_trade.avg_entry_price > 0
            else broker_avg_price
        )
        profit_pct = ((current_price / avg_price) - 1.0) * 100.0 if avg_price > 0 and current_price > 0 else 0.0
        realized_net = (
            float(active_trade.net_pnl)
            if active_trade
            else -sum(abs(_number(row, "buyFee", "fee")) for row in matching_rows)
        )
        unrealized = (current_price - avg_price) * quantity * 1000.0
        estimated_exit_cost = 0.0
        if mode == "PAPER" and current_price > 0 and quantity > 0:
            exit_value = current_price * quantity * 1000.0
            estimated_exit_cost = exit_value * (
                config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE
            )
        current_net_pnl = realized_net + unrealized - estimated_exit_cost
        fully_sellable = available_to_sell(matching_rows, symbol) >= quantity > 0
        metrics = self.rule_state.update_position_metrics(
            symbol,
            trade_id,
            profit_pct=profit_pct,
            net_pnl=current_net_pnl,
            market_price=current_price,
            t2_dynamic_enabled=normal_t2_reset_enabled,
            sellable=fully_sellable,
            normal_arm_pct=normal_arm_pct,
            entry_avg_price=avg_price if entry_orders["entry_orders_max"] > 1 else 0.0,
        ) if trade_id else {}
        context["position"] = {
            "quantity": quantity,
            "avg_price": avg_price,
            "current_price": current_price,
            "trade_quantity": sum(
                max(0, int(_number(row, "tradeQuantity"))) for row in matching_rows
            ) if not active_trade else min(
                quantity,
                sum(max(0, int(_number(row, "tradeQuantity"))) for row in matching_rows),
            ),
            "sellable": fully_sellable,
            "trade_id": trade_id,
            "managed_by_bot": managed,
            "managed_by_app": managed,
            "broker_quantity": broker_quantity,
            "external_quantity": max(0, broker_quantity - quantity) if active_trade else broker_quantity,
            "is_reentry": bool(active_trade.is_reentry) if active_trade else False,
            "sl_enabled": bool(active_trade.sl_enabled) if active_trade else True,
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
