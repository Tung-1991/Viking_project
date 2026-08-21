from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
import uuid

from ..rules.business import (
    EXPOSURE_DEFAULTS,
    StaticRule,
    StaticRuleParameters,
    classify_market_state,
    indicator_snapshot,
)
from ..trading.portfolio import order_budget, round_lot_down, size_buy_order
from .data import HistoricalDataStore, VN_TZ, bar_date
from .models import BacktestConfig, BacktestEvent, BacktestResult, BacktestScenario, BacktestTrade


def _indicator_columns(details: dict[str, Any] | None) -> dict[str, float]:
    """Flatten the rule snapshot so every exported row has real numeric columns."""
    values = (details or {}).get("indicators")
    values = values if isinstance(values, dict) else {}
    return {
        "ema_fast": float(values.get("sell_ema_fast") or values.get("ema_fast") or 0.0),
        "ema_slow": float(values.get("sell_ema_slow") or values.get("ema_slow") or 0.0),
        "rsi_previous": float(values.get("rsi_previous") or 0.0),
        "rsi": float(values.get("rsi") or 0.0),
    }


def _opening_fill_price(bars: list[dict[str, Any]], fill_session: str) -> float:
    """Return the first price a trader is allowed to use without look-ahead."""
    if not bars:
        return 0.0
    selected = bars[0]
    if str(fill_session or "ATO").upper() == "CONTINUOUS":
        eligible: list[dict[str, Any]] = []
        for bar in bars:
            stamp = datetime.fromtimestamp(int(bar.get("time", 0) or 0), VN_TZ)
            if stamp.hour * 60 + stamp.minute >= 9 * 60 + 15:
                eligible.append(bar)
        if eligible:
            selected = eligible[0]
    return float(selected.get("open", 0.0) or selected.get("close", 0.0) or 0.0)


@dataclass(slots=True)
class _Position:
    trade_id: str
    symbol: str
    quantity: int
    entry_quantity: int
    avg_price: float
    opened_date: str
    settle_date: str
    buy_fee: float
    capital_principal: float
    em_modes: list[str]
    is_reentry: bool = False
    peak_profit_pct: float = 0.0
    highest_close: float = 0.0
    normal_done: bool = False
    high_done: bool = False
    sold_quantity: int = 0
    exit_value: float = 0.0
    exit_fills: list[dict[str, Any]] = field(default_factory=list)
    exit_ema_fast: float = 0.0
    exit_ema_slow: float = 0.0
    exit_rsi: float = 0.0
    fees: float = 0.0
    tax: float = 0.0
    net_pnl: float = 0.0
    exit_events: list[str] = field(default_factory=list)
    entry_market_state: str = ""
    entry_exposure_pct: float = 0.0
    entry_reason: str = ""
    entry_signal_date: str = ""
    entry_rule: str = ""
    cycle_id: str = ""
    entry_value: float = 0.0
    sl_pct: float = 0.0
    entry_ema_fast: float = 0.0
    entry_ema_slow: float = 0.0
    entry_rsi: float = 0.0


@dataclass(slots=True)
class _Pending:
    side: str
    symbol: str
    created_date: str
    event: str
    fraction: float = 1.0
    trade_id: str = ""
    market_state: str = ""
    triggered_events: list[str] = field(default_factory=list)
    settlement_waited: bool = False
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class _MarketConfirmation:
    def __init__(self, required: int):
        self.required = max(1, int(required or 3))
        self.confirmed = "UNKNOWN"
        self.candidate = ""
        self.count = 0

    def observe(self, candidate: str) -> str:
        candidate = str(candidate or "TRANSITION").upper()
        if candidate not in EXPOSURE_DEFAULTS:
            return self.confirmed
        if candidate == self.confirmed:
            self.candidate, self.count = candidate, 0
        elif candidate != self.candidate:
            self.candidate, self.count = candidate, 1
        else:
            self.count += 1
        if candidate != self.confirmed and self.count >= self.required:
            self.confirmed, self.count = candidate, 0
        return self.confirmed


def _normal_trail_fill(
    bars: list[dict[str, Any]],
    *,
    entry_price: float,
    peak_profit_pct: float,
    arm_pct: float,
    giveback_pct: float,
) -> tuple[float, float]:
    """Return the first deterministic intraday NORMAL fill and updated peak.

    A bar may arm the trail, but the same bar cannot also hit it because OHLC
    does not reveal whether high or low happened first.  The next bar can hit
    the level.  This conservative ordering prevents look-ahead.
    """
    peak = float(peak_profit_pct or 0.0)
    if entry_price <= 0:
        return 0.0, peak
    for bar in bars:
        opened = float(bar.get("open", 0.0) or 0.0)
        low = float(bar.get("low", 0.0) or 0.0)
        if peak >= arm_pct:
            trigger = entry_price * (1.0 + peak / 100.0) * (1.0 - giveback_pct / 100.0)
            if 0 < opened <= trigger:
                return opened, peak
            if 0 < low <= trigger:
                return trigger, peak
        high = float(bar.get("high", 0.0) or 0.0)
        if high > 0:
            peak = max(peak, (high / entry_price - 1.0) * 100.0)
    return 0.0, peak


class BacktestEngine:
    """Deterministic daily replay; it never touches live queue, token or state."""

    def __init__(self, data: HistoricalDataStore):
        self.data = data

    def run_scenario(
        self,
        scenario: BacktestScenario,
        *,
        initial_capital: float,
        rule_parameters: dict[str, Any] | None = None,
        progress: Callable[[float, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        save: bool = False,
        carry: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> BacktestResult:
        raw_params = dict(rule_parameters or {})
        # A scenario only pins the symbols, the window and the market state.
        # Indicators stay global so one backtest never silently tests a rule the
        # live bot cannot run.  A row may also switch Phase 1 off and pin its own
        # exposure; the rule still needs a valid state name, so it gets a
        # placeholder while every state shares that one exposure.
        exposure = StaticRuleParameters.from_dict(raw_params).exposure
        uses_phase = scenario.uses_market_phase
        # The row's own switches win over the shared settings, so two rows can
        # compare configurations over the same window.
        raw_params["max_positions"] = scenario.max_positions
        kwargs.setdefault("em_modes", list(scenario.em_modes))
        kwargs.setdefault("whipsaw_enabled", scenario.whipsaw_enabled)
        return self.run(BacktestConfig(
            symbols=scenario.symbols,
            start_date=scenario.start_date,
            end_date=scenario.end_date,
            initial_capital=initial_capital,
            auto_market_phase=False,
            fixed_market_phase=scenario.market_phase if uses_phase else "ACCUMULATION",
            fixed_exposure_pct=(
                exposure.get(scenario.market_phase, 0.0) * 100.0 if uses_phase
                else scenario.exposure_pct
            ),
            use_market_phase=uses_phase,
            rule_parameters=raw_params,
            run_name=scenario.name,
            **kwargs,
        ), progress=progress, cancelled=cancelled, save=save, carry=carry)

    def run(
        self,
        settings: BacktestConfig,
        *,
        progress: Callable[[float, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        save: bool = False,
        carry: dict[str, Any] | None = None,
    ) -> BacktestResult:
        """``carry`` chains one account across several runs.

        Passing the same dict to consecutive runs hands the next one the cash,
        open positions, queued orders and protection state the previous one
        ended with, so the account behaves like one trader moving through time.
        """
        started = datetime.now().astimezone().isoformat()
        carried = carry or {}
        carried_positions: dict[str, _Position] = dict(carried.get("positions") or {})
        carried_pending: dict[str, _Pending] = dict(carried.get("pending") or {})
        active_symbols = list(settings.symbols)
        managed_symbols = list(dict.fromkeys([
            *active_symbols,
            *carried_positions.keys(),
            *carried_pending.keys(),
        ]))
        params = StaticRuleParameters.from_dict(settings.rule_parameters)
        # max_positions is taken exactly as entered, like the live bot: it is the
        # number of slots the capital is split between, not a cap derived from
        # the symbol list.  Running fewer symbols than slots therefore leaves the
        # spare slots in cash on purpose; the popup spells that out before a run.
        # Baseline backtest của VA tắt Whipsaw. Không được âm thầm kế thừa
        # công tắc của tài khoản giao dịch thật.
        params.whipsaw_enabled = settings.whipsaw_enabled
        if not settings.loss_lock_enabled:
            params.loss_lock_count = 10**9
        if not settings.auto_market_phase:
            params.exposure = {key: settings.fixed_exposure_pct / 100.0 for key in EXPOSURE_DEFAULTS}
        # Persist the effective values, not only sparse account overrides, so
        # the Excel Config sheet can reproduce the run exactly.
        settings.rule_parameters = params.to_dict()
        rule = StaticRule(params)
        all_symbols = ["VNINDEX", *managed_symbols]
        loaded: dict[str, list[dict[str, Any]]] = {}
        for offset, symbol in enumerate(all_symbols):
            if cancelled and cancelled():
                raise RuntimeError("Backtest đã hủy.")
            loaded[symbol] = self.data.load_daily(
                symbol,
                settings.start_date,
                settings.end_date,
                warmup_sessions=settings.warmup_sessions,
                progress=(lambda message, n=offset: progress(n / max(1, len(all_symbols)) * .15, message)) if progress else None,
            )
        execution_rows: dict[str, list[dict[str, Any]]] = {}
        execution_resolution: dict[str, str] = {}
        data_warnings: list[str] = []
        for offset, symbol in enumerate(managed_symbols):
            if cancelled and cancelled():
                raise RuntimeError("Backtest đã hủy.")
            rows, used_resolution, warnings = self.data.load_execution(
                symbol,
                settings.start_date,
                settings.end_date,
                resolution=settings.execution_resolution,
                progress=(
                    lambda message, n=offset: progress(
                        .15 + n / max(1, len(managed_symbols)) * .15,
                        message,
                    )
                ) if progress else None,
            )
            execution_rows[symbol] = rows
            execution_resolution[symbol] = used_resolution
            data_warnings.extend(warnings)
        vn_rows = loaded.get("VNINDEX", [])
        if len(vn_rows) < max(params.ma_period, settings.warmup_sessions // 2):
            raise RuntimeError("Dữ liệu VNINDEX không đủ warm-up để chạy Phase 1.")
        for symbol in managed_symbols:
            if not loaded.get(symbol):
                raise RuntimeError(f"Không có dữ liệu cho {symbol}.")

        rows_by_symbol = {
            symbol: {bar_date(row).isoformat(): row for row in rows}
            for symbol, rows in loaded.items()
        }
        execution_by_symbol_day: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for symbol, rows in execution_rows.items():
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(bar_date(row).isoformat(), []).append(row)
            execution_by_symbol_day[symbol] = {
                day: sorted(values, key=lambda item: int(item.get("time", 0) or 0))
                for day, values in grouped.items()
            }
        # Every session any symbol traded, not just the ones VNINDEX has a bar
        # for.  Taking VNINDEX alone dropped that day's bar for every stock, so
        # EMA and RSI were computed on a series with holes and every crossover
        # landed a session late.
        calendar = sorted({
            key
            for symbol in all_symbols
            for key in rows_by_symbol.get(symbol, {})
            if settings.start_date <= key <= settings.end_date
            and key > str(carried.get("last_processed_date") or "")
        })
        if not calendar:
            raise RuntimeError("Khoảng ngày đã chọn không có phiên giao dịch.")
        calendar_index = {value: index for index, value in enumerate(calendar)}
        history: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in all_symbols}
        for symbol, rows in loaded.items():
            history[symbol] = [row for row in rows if bar_date(row).isoformat() < settings.start_date]

        positions: dict[str, _Position] = carried_positions
        cash = float(carried["cash"]) if "cash" in carried else float(settings.initial_capital)
        pending: dict[str, _Pending] = carried_pending
        loss_streaks = {symbol: 0 for symbol in managed_symbols}
        loss_streaks.update(carried.get("loss_streaks") or {})
        loss_locked_until: dict[str, datetime | None] = {symbol: None for symbol in managed_symbols}
        loss_locked_until.update(carried.get("loss_locked_until") or {})
        capital_ledgers: dict[str, dict[str, float]] = dict(carried.get("capital_ledgers") or {})
        cycle_numbers = {symbol: 0 for symbol in managed_symbols}
        cycle_numbers.update(carried.get("cycle_numbers") or {})
        events: list[BacktestEvent] = []
        completed: list[BacktestTrade] = []
        equity_curve: list[dict[str, Any]] = []
        phase_history: list[dict[str, Any]] = []
        signal_history: list[dict[str, Any]] = []
        total_fees = total_tax = 0.0
        buy_count = sell_count = 0
        confirmation = _MarketConfirmation(params.confirm_sessions)
        current_phase = settings.fixed_market_phase if not settings.auto_market_phase else "UNKNOWN"

        # Rebuild Phase 1 from warm-up history, not from future sessions.
        if settings.auto_market_phase:
            warmup_vn: list[dict[str, Any]] = []
            previous = "UNKNOWN"
            for row in history["VNINDEX"]:
                warmup_vn.append(row)
                candidate, _ = classify_market_state(warmup_vn, previous_state=previous, params=params)
                confirmed = confirmation.observe(candidate)
                if confirmed in EXPOSURE_DEFAULTS:
                    previous = confirmed
            current_phase = confirmation.confirmed

        def latest_price(symbol: str, day: str, key: str = "close") -> float:
            row = rows_by_symbol.get(symbol, {}).get(day)
            return float(row.get(key, 0.0) or 0.0) if row else 0.0

        def execution_day(symbol: str, day: str) -> list[dict[str, Any]]:
            return execution_by_symbol_day.get(symbol, {}).get(day, [])

        def opening_price(symbol: str, day: str) -> float:
            bars = execution_day(symbol, day)
            if bars:
                # ATO fills at the auction price, which is the day's open.  The
                # live bot has ATO switched off, so it only gets in once
                # continuous trading starts.  Use the OPEN of the first bar at
                # or after 09:15; using its close would look into the future.
                return _opening_fill_price(bars, settings.fill_session)
            return latest_price(symbol, day, "open") or latest_price(symbol, day, "close")

        def stop_fill_price(symbol: str, day: str, stop_price: float) -> float:
            bars = execution_day(symbol, day)
            if bars:
                for bar in bars:
                    low = float(bar.get("low", 0.0) or 0.0)
                    if low > 0 and low <= stop_price:
                        opened = float(bar.get("open", 0.0) or 0.0)
                        return opened if 0 < opened < stop_price else stop_price
                return 0.0
            low = latest_price(symbol, day, "low")
            opened = opening_price(symbol, day)
            if low > 0 and low <= stop_price:
                return opened if 0 < opened < stop_price else stop_price
            return 0.0

        def portfolio_value(day: str) -> tuple[float, float]:
            value = sum(latest_price(symbol, day) * position.quantity * 1000.0 for symbol, position in positions.items())
            return cash + value, value

        def target_fill_price(symbol: str, day: str, target: float) -> float:
            """Mirror of stop_fill_price for an upside target."""
            bars = execution_day(symbol, day)
            if bars:
                for bar in bars:
                    high = float(bar.get("high", 0.0) or 0.0)
                    if high > 0 and high >= target:
                        opened = float(bar.get("open", 0.0) or 0.0)
                        return opened if opened > target else target
                return 0.0
            high = latest_price(symbol, day, "high")
            opened = opening_price(symbol, day)
            if high > 0 and high >= target:
                return opened if opened > target else target
            return 0.0

        def sell_position(
            position: _Position,
            quantity: int,
            price: float,
            day: str,
            event: str,
            phase: str,
            triggered: list[str] | None = None,
            *,
            signal_date: str = "",
            reason: str = "",
            details: dict[str, Any] | None = None,
        ) -> None:
            nonlocal cash, total_fees, total_tax, sell_count
            quantity = min(max(0, int(quantity)), position.quantity)
            if quantity <= 0 or price <= 0:
                return
            gross = price * quantity * 1000.0
            fee = gross * settings.sell_fee_rate
            tax = gross * settings.sell_tax_rate
            cash += gross - fee - tax
            pnl = (price - position.avg_price) * quantity * 1000.0 - fee - tax
            position.quantity -= quantity
            position.sold_quantity += quantity
            position.exit_value += gross
            position.fees += fee
            position.tax += tax
            position.net_pnl += pnl
            total_fees += fee
            total_tax += tax
            sell_count += 1
            event_names = list(triggered or []) or [event]
            for name in event_names:
                if name and name not in position.exit_events:
                    position.exit_events.append(name)
            if "NORMAL_PROTECTION" in event_names:
                position.normal_done = True
            if "HIGH_PROFIT_PROTECTION" in event_names:
                position.high_done = True
            details = dict(details or {})
            equity_after = portfolio_value(day)[0]
            profit_pct = (price / position.avg_price - 1.0) * 100.0 if position.avg_price > 0 else 0.0
            indicators = _indicator_columns(details)
            position.exit_fills.append({
                "event": "+".join(event_names), "quantity": quantity, "price": price,
            })
            position.exit_ema_fast = indicators["ema_fast"]
            position.exit_ema_slow = indicators["ema_slow"]
            position.exit_rsi = indicators["rsi"]
            events.append(BacktestEvent(
                day, position.trade_id, position.symbol, "SELL", event, quantity, price,
                gross, fee, tax, cash, phase, pnl,
                signal_date=signal_date,
                reason=reason or event,
                details=details,
                cycle_id=position.cycle_id,
                profit_pct=profit_pct,
                peak_profit_pct=position.peak_profit_pct,
                equity_after=equity_after,
                **_indicator_columns(details),
            ))
            if position.quantity <= 0:
                avg_exit = position.exit_value / max(1, position.sold_quantity) / 1000.0
                outcome = "WIN" if position.net_pnl >= 0 else "LOSS"
                completed.append(BacktestTrade(
                    trade_id=position.trade_id,
                    symbol=position.symbol,
                    opened_date=position.opened_date,
                    closed_date=day,
                    entry_quantity=position.entry_quantity,
                    remaining_quantity=0,
                    avg_entry_price=position.avg_price,
                    avg_exit_price=avg_exit,
                    fees=position.fees,
                    tax=position.tax,
                    net_pnl=position.net_pnl,
                    outcome=outcome,
                    exit_events=list(position.exit_events),
                    entry_market_state=position.entry_market_state,
                    entry_exposure_pct=position.entry_exposure_pct,
                    entry_reason=position.entry_reason,
                    entry_signal_date=position.entry_signal_date,
                    entry_rule=position.entry_rule,
                    cycle_id=position.cycle_id,
                    sessions_held=max(0, calendar_index.get(day, 0) - calendar_index.get(position.opened_date, 0)),
                    entry_value=position.entry_value,
                    sl_pct=position.sl_pct,
                    peak_profit_pct=position.peak_profit_pct,
                    pnl_pct=(position.net_pnl / position.entry_value * 100.0) if position.entry_value > 0 else 0.0,
                    equity_after=equity_after,
                    entry_ema_fast=position.entry_ema_fast,
                    entry_ema_slow=position.entry_ema_slow,
                    entry_rsi=position.entry_rsi,
                    exit_ema_fast=position.exit_ema_fast,
                    exit_ema_slow=position.exit_ema_slow,
                    exit_rsi=position.exit_rsi,
                    exit_fills=list(position.exit_fills),
                ))
                if outcome == "WIN":
                    loss_streaks[position.symbol] = 0
                    loss_locked_until[position.symbol] = None
                else:
                    loss_streaks[position.symbol] += 1
                    if settings.loss_lock_enabled and loss_streaks[position.symbol] >= params.loss_lock_count:
                        closed = datetime.strptime(day, "%Y-%m-%d")
                        loss_locked_until[position.symbol] = closed + timedelta(hours=settings.loss_lock_hours)
                ledger = capital_ledgers.get(position.symbol)
                if params.no_compound_enabled and ledger:
                    ledger["available"] = min(ledger["principal"], max(0.0, ledger["available"] + position.net_pnl))
                positions.pop(position.symbol, None)

        for day_number, day in enumerate(calendar):
            if cancelled and cancelled():
                raise RuntimeError("Backtest đã hủy.")
            for symbol in all_symbols:
                row = rows_by_symbol.get(symbol, {}).get(day)
                if row:
                    history[symbol].append(row)
            # DNSE has no VNINDEX bar on a few sessions the market did trade.
            # Re-reading the same series on those days would count one candle
            # twice towards the confirmation streak, so the state simply holds.
            index_moved = day in rows_by_symbol.get("VNINDEX", {})
            if settings.auto_market_phase and index_moved:
                candidate, details = classify_market_state(history["VNINDEX"], previous_state=current_phase, params=params)
                confirmed = confirmation.observe(candidate)
                if confirmed in EXPOSURE_DEFAULTS:
                    current_phase = confirmed
                phase_history.append({
                    "date": day, "candidate": candidate, "confirmed": current_phase,
                    "ma": float(details.get("ma200", 0.0) or 0.0),
                })
            elif settings.auto_market_phase:
                phase_history.append({"date": day, "candidate": current_phase, "confirmed": current_phase})
            else:
                current_phase = settings.fixed_market_phase
                phase_history.append({"date": day, "candidate": current_phase, "confirmed": current_phase})

            # Orders decided at the previous close fill at today's open, oldest
            # signal first so a queue never reorders itself by symbol name.
            def fill_pending() -> None:
                """Fill everything queued at the previous close, at today's open."""
                nonlocal cash, total_fees, buy_count
                for symbol, order in sorted(pending.items(), key=lambda kv: (kv[1].created_date, kv[0])):
                    row = rows_by_symbol.get(symbol, {}).get(day)
                    if not row:
                        continue
                    open_price = opening_price(symbol, day)
                    if order.side == "BUY":
                        if symbol in positions or len(positions) >= params.max_positions:
                            pending.pop(symbol, None)
                            continue
                        nav, stock_value = portfolio_value(day)
                        entry_phase = order.market_state if order.market_state in EXPOSURE_DEFAULTS else current_phase
                        exposure = params.exposure.get(entry_phase, 0.0)
                        pending_buy_value = 0.0
                        budget = order_budget(
                            nav=nav,
                            exposure=exposure,
                            max_positions=params.max_positions,
                            current_stock_value=stock_value,
                            pending_buy_value=pending_buy_value,
                            available_cash=cash,
                            fee_rate=settings.buy_fee_rate,
                        )
                        if params.no_compound_enabled and symbol in capital_ledgers:
                            budget = min(budget, capital_ledgers[symbol]["available"])
                        sizing = size_buy_order(
                            budget_vnd=budget,
                            price_board=open_price,
                            available_cash=cash,
                            nav=nav,
                            force_min_lot_enabled=params.force_min_lot_enabled,
                        )
                        quantity = sizing.quantity
                        gross = open_price * quantity * 1000.0
                        fee = gross * settings.buy_fee_rate
                        if quantity > 0 and gross + fee <= cash:
                            cash -= gross + fee
                            settle_index = min(len(calendar) - 1, calendar_index[day] + 2)
                            settle_day = calendar[settle_index]
                            principal = gross + fee
                            ledger = capital_ledgers.setdefault(symbol, {"principal": principal, "available": principal})
                            principal = min(principal, ledger["available"]) if params.no_compound_enabled else principal
                            attempt = loss_streaks[symbol]
                            if attempt == 0:
                                cycle_numbers[symbol] += 1
                            cycle_id = f"{symbol}-{cycle_numbers[symbol]:02d}" + (f".{attempt}" if attempt else "")
                            entry_indicators = _indicator_columns(order.details)
                            position = _Position(
                                uuid.uuid4().hex, symbol, quantity, quantity, open_price, day, settle_day,
                                fee, principal, list(settings.em_modes),
                                is_reentry=attempt > 0,
                                highest_close=open_price, fees=fee, net_pnl=-fee,
                                entry_market_state=entry_phase,
                                entry_exposure_pct=exposure * 100.0,
                                entry_reason=order.reason or order.event,
                                entry_signal_date=order.created_date,
                                entry_rule=(
                                    f"EMA {params.buy_ema_fast}/{params.buy_ema_slow} + "
                                    f"RSI{params.rsi_period} tăng"
                                ),
                                cycle_id=cycle_id,
                                entry_value=gross + fee,
                                sl_pct=params.reentry_sl_pct if attempt > 0 else params.initial_sl_pct,
                                entry_ema_fast=entry_indicators["ema_fast"],
                                entry_ema_slow=entry_indicators["ema_slow"],
                                entry_rsi=entry_indicators["rsi"],
                            )
                            positions[symbol] = position
                            total_fees += fee
                            buy_count += 1
                            events.append(BacktestEvent(
                                day, position.trade_id, symbol, "BUY", "ENTRY_BUY", quantity,
                                open_price, gross, fee, 0.0, cash, entry_phase, -fee,
                                signal_date=order.created_date,
                                reason=order.reason or "Tín hiệu BUY",
                                details=dict(order.details),
                                cycle_id=cycle_id,
                                equity_after=portfolio_value(day)[0],
                                **entry_indicators,
                            ))
                        pending.pop(symbol, None)
                    else:
                        position = positions.get(symbol)
                        if not position or position.trade_id != order.trade_id:
                            pending.pop(symbol, None)
                            continue
                        if day < position.settle_date:
                            order.settlement_waited = True
                            continue
                        quantity = round_lot_down(position.quantity * order.fraction)
                        if order.fraction >= 1.0:
                            quantity = round_lot_down(position.quantity)
                        if quantity > 0:
                            sell_position(
                                position,
                                quantity,
                                open_price,
                                day,
                                order.event,
                                current_phase,
                                order.triggered_events,
                                signal_date=order.created_date,
                                reason=order.reason,
                                details=order.details,
                            )
                        pending.pop(symbol, None)


            fill_pending()

            # SL has absolute priority. NORMAL can then use intraday replay;
            # HIGH and indicator EXIT remain daily-close rules by definition.
            for symbol, position in list(positions.items()):
                row = rows_by_symbol.get(symbol, {}).get(day)
                if not row or symbol in pending:
                    continue
                sl_pct = params.reentry_sl_pct if position.is_reentry else params.initial_sl_pct
                stop_price = position.avg_price * (1.0 + sl_pct / 100.0)
                fill = stop_fill_price(symbol, day, stop_price)
                if fill > 0:
                    # Stop loss must carry the same indicator snapshot as every
                    # other exit, otherwise its rows export blank in the report.
                    stop_details = {
                        "indicators": indicator_snapshot(
                            history[symbol],
                            params.buy_ema_fast, params.buy_ema_slow, params.rsi_period,
                            sell_fast=params.sell_ema_fast, sell_slow=params.sell_ema_slow,
                        ),
                        "sl_value": sl_pct,
                        "stop_price": stop_price,
                    }
                    if day >= position.settle_date:
                        sell_position(
                            position, round_lot_down(position.quantity), fill, day,
                            "STOP_LOSS", current_phase, signal_date=day,
                            reason="STOP_LOSS", details=stop_details,
                        )
                    else:
                        pending[symbol] = _Pending(
                            "SELL", symbol, day, "STOP_LOSS", 1.0, position.trade_id,
                            current_phase, settlement_waited=True,
                            reason="STOP_LOSS", details=stop_details,
                        )
                    continue
                # Stop loss keeps priority; a bar that touches both is resolved
                # pessimistically because OHLC hides which came first.
                if "TP" in position.em_modes and params.take_profit_pct > 0:
                    target = position.avg_price * (1.0 + params.take_profit_pct / 100.0)
                    fill = target_fill_price(symbol, day, target)
                    if fill > 0:
                        details = {
                            "indicators": indicator_snapshot(
                                history[symbol],
                                params.buy_ema_fast, params.buy_ema_slow, params.rsi_period,
                                sell_fast=params.sell_ema_fast, sell_slow=params.sell_ema_slow,
                            ),
                            "take_profit_pct": params.take_profit_pct,
                            "target_price": target,
                        }
                        if day >= position.settle_date:
                            sell_position(
                                position, round_lot_down(position.quantity), fill, day,
                                "TAKE_PROFIT", current_phase, signal_date=day,
                                reason="TAKE_PROFIT", details=details,
                            )
                        else:
                            pending[symbol] = _Pending(
                                "SELL", symbol, day, "TAKE_PROFIT", 1.0, position.trade_id,
                                current_phase, settlement_waited=True,
                                reason="TAKE_PROFIT", details=details,
                            )
                        continue
                if (
                    execution_resolution.get(symbol) != "1D"
                    and "NORMAL" in position.em_modes
                    and not position.normal_done
                ):
                    normal_fill, peak = _normal_trail_fill(
                        execution_day(symbol, day),
                        entry_price=position.avg_price,
                        peak_profit_pct=position.peak_profit_pct,
                        arm_pct=params.normal_arm_pct,
                        giveback_pct=params.normal_giveback_pct,
                    )
                    position.peak_profit_pct = max(position.peak_profit_pct, peak)
                    if normal_fill > 0:
                        details = {
                            "triggered_events": ["NORMAL_PROTECTION"],
                            "peak_profit_pct": peak,
                            "execution_resolution": execution_resolution.get(symbol),
                        }
                        share = min(1.0, max(0.0, params.normal_sell_pct / 100.0))
                        if day >= position.settle_date:
                            quantity = (
                                round_lot_down(position.quantity) if share >= 1.0
                                else round_lot_down(position.quantity * share)
                            )
                            if quantity > 0:
                                sell_position(
                                    position, quantity, normal_fill, day,
                                    "PRICE_PROTECTION", current_phase,
                                    ["NORMAL_PROTECTION"], signal_date=day,
                                    reason="NORMAL_PROTECTION", details=details,
                                )
                        else:
                            pending[symbol] = _Pending(
                                "SELL", symbol, day, "PRICE_PROTECTION", share,
                                position.trade_id, current_phase,
                                ["NORMAL_PROTECTION"], True,
                                "NORMAL_PROTECTION", details,
                            )

            nav, stock_value = portfolio_value(day)
            for symbol in settings.symbols:
                # A buy queued earlier today already owns its slot.  Recounting
                # per symbol keeps the allocation first come first served: the
                # symbols reached first take the free slots and the rest are
                # told the book is full, instead of queueing an order that
                # would be silently dropped at tomorrow's open.
                open_positions = len(positions) + sum(
                    1 for order in pending.values() if order.side == "BUY"
                )
                row = rows_by_symbol.get(symbol, {}).get(day)
                if not row or not history[symbol]:
                    continue
                position = positions.get(symbol)
                if position:
                    high = float(row.get("high", row.get("close", 0.0)) or 0.0)
                    close = float(row.get("close", 0.0) or 0.0)
                    if position.avg_price > 0:
                        position.peak_profit_pct = max(position.peak_profit_pct, (high / position.avg_price - 1.0) * 100.0)
                    position.highest_close = max(position.highest_close, close)
                locked = False
                until = loss_locked_until.get(symbol)
                if until is not None:
                    current_time = datetime.strptime(day, "%Y-%m-%d")
                    locked = current_time < until
                    if not locked:
                        loss_streaks[symbol] = 0
                        loss_locked_until[symbol] = None
                exposure = params.exposure.get(current_phase, 0.0)
                budget = order_budget(
                    nav=nav, exposure=exposure, max_positions=params.max_positions,
                    current_stock_value=stock_value, pending_buy_value=0.0, available_cash=cash,
                )
                if params.no_compound_enabled and symbol in capital_ledgers:
                    budget = min(budget, capital_ledgers[symbol]["available"])
                portfolio = {
                    "nav": nav,
                    "available_cash": cash,
                    "available_capital": max(0.0, budget),
                    "order_budget": max(0.0, budget),
                    "open_positions": open_positions,
                    "pending_buy": symbol in pending and pending[symbol].side == "BUY",
                    "loss_streak": params.loss_lock_count if locked else loss_streaks[symbol],
                    "position_quantity": position.quantity if position else 0,
                    "position": ({
                        "quantity": position.quantity,
                        "avg_price": position.avg_price,
                        "current_price": float(row.get("close", 0.0) or 0.0),
                        "peak_profit_pct": position.peak_profit_pct,
                        "highest_close": position.highest_close,
                        "is_reentry": position.is_reentry,
                        "em_modes": position.em_modes,
                        "normal_protection_done": position.normal_done,
                        "high_profit_protection_done": position.high_done,
                        "managed_by_app": True,
                        "managed_by_bot": True,
                    } if position else {}),
                }
                decision = rule.evaluate({
                    "symbol": symbol,
                    "bars": history[symbol],
                    "vnindex_bars": history["VNINDEX"],
                    "signal_mode": "CLOSED",
                    "previous_market_state": current_phase,
                    "confirmed_market_state": current_phase,
                    "precomputed_market": {
                        "candidate": current_phase,
                        "state": current_phase,
                        "details": {},
                    },
                }, portfolio)

                if settings.export_signals or decision.action != "WAIT":
                    details = dict(decision.details or {}) if isinstance(decision.details, dict) else {}
                    signal_history.append({
                        "date": day,
                        "symbol": symbol,
                        "action": decision.action,
                        "event": decision.event,
                        "reason": decision.reason,
                        "market_state": current_phase,
                        "buy_ema": f"{params.buy_ema_fast}/{params.buy_ema_slow}",
                        "sell_ema": f"{params.sell_ema_fast}/{params.sell_ema_slow}",
                        "rsi_period": params.rsi_period,
                        "details": details,
                    })

                existing = pending.get(symbol)
                if existing and existing.side == "SELL" and existing.settlement_waited and settings.sell_wait_policy == "RECHECK":
                    if decision.action != "SELL":
                        pending.pop(symbol, None)
                        existing = None
                if existing:
                    continue
                if decision.action == "BUY" and position is None and not locked:
                    pending[symbol] = _Pending(
                        "BUY", symbol, day, decision.event or decision.reason, 1.0,
                        market_state=current_phase,
                        reason=decision.reason,
                        details=dict(decision.details or {}) if isinstance(decision.details, dict) else {},
                    )
                elif decision.action == "SELL" and position is not None:
                    triggered = decision.details.get("triggered_events", []) if isinstance(decision.details, dict) else []
                    pending[symbol] = _Pending(
                        "SELL", symbol, day, decision.event or decision.reason,
                        float(decision.quantity_fraction or 1.0), position.trade_id,
                        current_phase, list(triggered or []), day < position.settle_date,
                        decision.reason,
                        dict(decision.details or {}) if isinstance(decision.details, dict) else {},
                    )

            nav, stock_value = portfolio_value(day)
            equity_curve.append({"date": day, "equity": nav, "cash": cash, "market_value": stock_value})
            if progress:
                progress(.30 + .70 * ((day_number + 1) / len(calendar)), f"Replay {day}")

        if settings.fill_session == "CONTINUOUS":
            flat = [s for s, r in execution_resolution.items() if r == "1D"]
            if flat:
                data_warnings.append(
                    "Chọn khớp sau 9h15 nhưng "
                    + ", ".join(sorted(flat)[:6])
                    + (" và các mã khác" if len(flat) > 6 else "")
                    + " không có nến trong phiên, vẫn phải khớp ở giá mở cửa."
                )

        last_day = calendar[-1]
        final_equity, market_value = portfolio_value(last_day)
        if carry is not None:
            carry.update(
                cash=cash, positions=positions, cycle_numbers=cycle_numbers,
                pending=pending, loss_streaks=loss_streaks,
                loss_locked_until=loss_locked_until,
                capital_ledgers=capital_ledgers,
                last_processed_date=last_day,
            )
        open_trades = [
            BacktestTrade(
                position.trade_id, position.symbol, position.opened_date,
                entry_quantity=position.entry_quantity,
                remaining_quantity=position.quantity,
                avg_entry_price=position.avg_price,
                fees=position.fees,
                tax=position.tax,
                net_pnl=position.net_pnl + (latest_price(position.symbol, last_day) - position.avg_price) * position.quantity * 1000.0,
                outcome="OPEN",
                exit_events=list(position.exit_events),
                # A position still open may already have been trimmed by NORMAL
                # or HIGH; dropping those fills made the report read as if the
                # protections had never fired at all.
                exit_fills=list(position.exit_fills),
                avg_exit_price=(position.exit_value / (position.sold_quantity * 1000.0)
                                if position.sold_quantity else 0.0),
                entry_market_state=position.entry_market_state,
                entry_exposure_pct=position.entry_exposure_pct,
                entry_reason=position.entry_reason,
                entry_signal_date=position.entry_signal_date,
                entry_rule=position.entry_rule,
                cycle_id=position.cycle_id,
                sessions_held=max(0, len(calendar) - 1 - calendar_index.get(position.opened_date, 0)),
                entry_value=position.entry_value,
                sl_pct=position.sl_pct,
                peak_profit_pct=position.peak_profit_pct,
                entry_ema_fast=position.entry_ema_fast,
                entry_ema_slow=position.entry_ema_slow,
                entry_rsi=position.entry_rsi,
            )
            for position in positions.values()
        ]
        peak = 0.0
        max_drawdown = 0.0
        for point in equity_curve:
            equity = float(point["equity"])
            peak = max(peak, equity)
            drawdown = ((equity / peak) - 1.0) * 100.0 if peak > 0 else 0.0
            point["drawdown_pct"] = drawdown
            max_drawdown = min(max_drawdown, drawdown)
        wins = sum(1 for trade in completed if trade.outcome == "WIN")
        losses = sum(1 for trade in completed if trade.outcome == "LOSS")
        net_pnl = final_equity - settings.initial_capital
        result = BacktestResult(
            run_id=f"BT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}",
            config=settings,
            started_at=started,
            completed_at=datetime.now().astimezone().isoformat(),
            initial_capital=settings.initial_capital,
            final_equity=final_equity,
            cash=cash,
            market_value=market_value,
            net_pnl=net_pnl,
            return_pct=(net_pnl / settings.initial_capital * 100.0),
            total_fees=total_fees,
            total_tax=total_tax,
            max_drawdown_pct=abs(max_drawdown),
            buy_count=buy_count,
            sell_count=sell_count,
            closed_trades=len(completed),
            win_count=wins,
            loss_count=losses,
            win_rate_pct=(wins / len(completed) * 100.0 if completed else 0.0),
            events=events,
            trades=[*completed, *open_trades],
            equity_curve=equity_curve,
            phase_history=phase_history,
            signals=signal_history,
            data_quality={
                "signal_resolution": "1D",
                "execution_resolution": execution_resolution,
                "execution_coverage": {
                    symbol: {
                        "from": bar_date(rows[0]).isoformat() if rows else "",
                        "to": bar_date(rows[-1]).isoformat() if rows else "",
                    }
                    for symbol, rows in execution_rows.items()
                },
                "execution_mode": "AUTO" if settings.execution_resolution == "AUTO" else settings.execution_resolution,
                "daily_bars": {symbol: len(rows) for symbol, rows in loaded.items()},
                "execution_bars": {symbol: len(rows) for symbol, rows in execution_rows.items()},
                "order_timing": "signal at daily close; fill from next session",
            },
            warnings=[
                "Tín hiệu được chốt theo nến ngày; lệnh hợp lệ khớp ở giá mở cửa phiên kế tiếp.",
                "Không áp dụng thêm điều chỉnh chốt quyền ngoài dữ liệu giá DNSE trả về.",
                *data_warnings,
            ],
        )
        if save:
            self.data.save_run(result)
        return result
