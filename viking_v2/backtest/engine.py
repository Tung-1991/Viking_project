from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Callable
import uuid

from ..rules.business import (
    EXPOSURE_DEFAULTS,
    StaticRule,
    StaticRuleParameters,
    average_true_range_pct,
    classify_market_state,
    indicator_snapshot,
    protect_level,
    protect_rearm_mfe,
)
from ..rules.entry_filters import apply_buy_filters
from ..trading.portfolio import (
    order_budget,
    round_lot_down,
    sell_quantity_for_fraction,
    size_buy_order,
)
from ..trading.market import (
    stock_is_sellable_after_settlement,
    normalize_exchange,
    phase_at_minute,
    exchange_open_minute,
    exchange_close_minute,
    in_buy_window,
)
from .data import HistoricalDataStore, VN_TZ, bar_date
from .models import BacktestConfig, BacktestEvent, BacktestResult, BacktestScenario, BacktestTrade
from .replay import ReplayDataStore


UNKNOWN_SETTLE_DATE = "9999-12-31"

EXIT_PRIORITY = {
    "NORMAL_PROTECTION": 1,
    "PRICE_PROTECTION": 1,
    "INDICATOR_EXIT": 2,
    "TAKE_PROFIT": 3,
    "STOP_LOSS": 4,
}


def _exit_priority(event: str, triggered: list[str] | None = None) -> int:
    values = [str(event or "").upper(), *[str(item or "").upper() for item in (triggered or [])]]
    return max((EXIT_PRIORITY.get(value, 0) for value in values), default=0)


def _buy_rule_text(params: StaticRuleParameters) -> str:
    terms: list[str] = []
    if params.buy_signal_use_ema:
        terms.append(f"EMA {params.buy_ema_fast}/{params.buy_ema_slow} cắt lên")
    if params.buy_signal_use_rsi:
        terms.append(f"RSI{params.rsi_period} tăng")
    return " + ".join(terms) or "OFF"


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


def _opening_fill_price(
    bars: list[dict[str, Any]], fill_session: str, exchange: str = "HOSE",
) -> float:
    """Return the first price a trader is allowed to use without look-ahead."""
    if not bars:
        return 0.0
    selected = bars[0]
    market = normalize_exchange(exchange) or "HOSE"
    if str(fill_session or "ATO").upper() == "CONTINUOUS" or market != "HOSE":
        eligible: list[dict[str, Any]] = []
        for bar in bars:
            stamp = datetime.fromtimestamp(int(bar.get("time", 0) or 0), VN_TZ)
            if stamp.hour * 60 + stamp.minute >= exchange_open_minute(market):
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
    normal_done: bool = False
    normal_armed: bool = False
    normal_arm_time: str = ""
    mfe_after_arm_pct: float = 0.0
    normal_protected_profit_pct: float = 0.0
    normal_effective_trail_pct: float = 0.0
    normal_trigger_price: float = 0.0
    normal_count: int = 0
    normal_last_trigger_peak_pct: float = 0.0
    normal_rearm_mfe_pct: float = 0.0
    normal_alert_count: int = 0
    normal_last_alert_peak_pct: float = 0.0
    normal_alert_rearm_mfe_pct: float = 0.0
    mae_profit_pct: float = 0.0
    peak_at: str = ""
    profit_path: list[dict[str, Any]] = field(default_factory=list)
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
    opened_at: str = ""


def _protect_repeat_enabled(params: StaticRuleParameters) -> bool:
    return bool(params.normal_repeat_enabled and params.normal_sell_pct < 100.0)


def _protect_event_ready(
    position: _Position,
    params: StaticRuleParameters,
    *,
    alert: bool = False,
) -> bool:
    count = position.normal_alert_count if alert else position.normal_count
    if count <= 0:
        return True
    if not _protect_repeat_enabled(params):
        return False
    required = (
        position.normal_alert_rearm_mfe_pct
        if alert else position.normal_rearm_mfe_pct
    )
    return position.peak_profit_pct + 1e-9 >= required


def _record_protect_event(
    position: _Position,
    params: StaticRuleParameters,
    *,
    alert: bool,
    peak_profit_pct: float,
) -> None:
    peak = max(0.0, float(peak_profit_pct or 0.0))
    rearm = protect_rearm_mfe(peak, params.normal_giveback_pct)
    if alert:
        position.normal_alert_count += 1
        position.normal_last_alert_peak_pct = peak
        position.normal_alert_rearm_mfe_pct = rearm
        return
    position.normal_count += 1
    position.normal_last_trigger_peak_pct = peak
    position.normal_rearm_mfe_pct = rearm
    position.normal_done = not _protect_repeat_enabled(params)


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
    created_time: str = ""
    source_resolution: str = "1D"
    data_quality: str = "FULL"


def _is_pending_protect(order: _Pending) -> bool:
    values = {
        str(order.event or "").upper(),
        *(
            value
            for value in str(order.reason or "").upper().split("+")
            if value
        ),
        *(str(value or "").upper() for value in order.triggered_events),
    }
    return bool(values & {"NORMAL_PROTECTION", "PRICE_PROTECTION"})


def _pending_protect_matches(
    order: _Pending,
    position: _Position,
    params: StaticRuleParameters,
    price: float,
    *,
    atr_pct: float = 0.0,
) -> bool:
    """Recheck a T+2 PROTECT request with current price and current rule."""
    if not _is_pending_protect(order):
        return False
    if params.normal_policy != "AUTO" or "NORMAL" not in position.em_modes:
        return False
    level = protect_level(
        position.avg_price,
        position.peak_profit_pct,
        params.normal_arm_pct,
        params.normal_giveback_pct,
        dynamic_enabled=params.normal_dynamic_enabled,
        sl_price=position.avg_price * (1.0 + position.sl_pct / 100.0),
        atr_pct=atr_pct,
        atr_multiplier=params.normal_atr_multiplier,
        previous_trigger_price=position.normal_trigger_price,
    )
    current = float(price or 0.0)
    return bool(level.active and current > 0 and current <= level.trigger_price + 1e-9)


def _pending_matches_decision(order: _Pending, raw: Any) -> bool | None:
    """Mirror live RECHECK event matching for non-PROTECT exits."""
    if raw is None:
        return None
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()
    if isinstance(raw, str):
        return True if raw.upper() == "SELL" else False
    if not isinstance(raw, dict):
        return None
    if str(raw.get("action", "WAIT") or "WAIT").upper() != "SELL":
        return False
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    current = {
        str(raw.get("event", "") or "").upper(),
        str(raw.get("reason", "") or "").upper(),
        *(str(value or "").upper() for value in details.get("triggered_events", []) or []),
    }
    requested = {
        str(order.event or "").upper(),
        *(
            value
            for value in str(order.reason or "").upper().split("+")
            if value
        ),
        *(str(value or "").upper() for value in order.triggered_events),
    }
    current.discard("")
    requested.discard("")
    return bool(requested & current) if requested else True


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
        level = protect_level(
            entry_price, peak, arm_pct, giveback_pct,
            dynamic_enabled=False,
        )
        if level.active:
            if 0 < opened <= level.trigger_price:
                return opened, peak
            if 0 < low <= level.trigger_price:
                return level.trigger_price, peak
        high = float(bar.get("high", 0.0) or 0.0)
        if high > 0:
            peak = max(peak, (high / entry_price - 1.0) * 100.0)
    return 0.0, peak


@dataclass(slots=True)
class _NormalObservation:
    fill: float
    triggered: bool
    peak_profit_pct: float
    armed: bool
    armed_at: int
    mfe_after_arm_pct: float
    protected_profit_pct: float
    effective_trail_pct: float
    trigger_price: float
    state: str


def _normal_policy_fill(
    bars: list[dict[str, Any]],
    *,
    policy: str,
    entry_price: float,
    peak_profit_pct: float,
    already_armed: bool,
    mfe_after_arm_pct: float,
    arm_pct: float,
    giveback_pct: float,
    dynamic_enabled: bool = False,
    sl_price: float = 0.0,
    atr_pct: float = 0.0,
    atr_multiplier: float = 0.6,
    previous_trigger_price: float = 0.0,
) -> _NormalObservation:
    """Observe one or more bars using deterministic NORMAL semantics.

    The carried stop is checked before accepting the current bar's high, so
    one OHLC bar cannot raise PROTECT and then use an unknowable later low.
    ALERT observes the same hit but never creates a fill.
    """
    policy = "ALERT" if str(policy or "AUTO").upper() == "ALERT" else "AUTO"
    peak = float(peak_profit_pct or 0.0)
    armed = bool(already_armed or peak >= arm_pct)
    armed_at = 0
    mfe = max(0.0, float(mfe_after_arm_pct or 0.0), peak)
    carried_trigger = max(0.0, float(previous_trigger_price or 0.0))
    level = protect_level(
        entry_price, peak, arm_pct, giveback_pct,
        dynamic_enabled=dynamic_enabled, sl_price=sl_price,
        atr_pct=atr_pct, atr_multiplier=atr_multiplier,
        previous_trigger_price=carried_trigger,
    )
    if entry_price <= 0:
        return _NormalObservation(
            0.0, False, peak, armed, 0, mfe, level.trigger_profit_pct,
            level.effective_trail_pct, level.trigger_price, level.state,
        )

    for bar in bars:
        opened = float(bar.get("open", 0.0) or 0.0)
        low = float(bar.get("low", 0.0) or 0.0)
        level = protect_level(
            entry_price, peak, arm_pct, giveback_pct,
            dynamic_enabled=dynamic_enabled, sl_price=sl_price,
            atr_pct=atr_pct, atr_multiplier=atr_multiplier,
            previous_trigger_price=carried_trigger,
        )
        if level.active:
            carried_trigger = max(carried_trigger, level.trigger_price)
        if level.active:
            fill = opened if 0 < opened <= level.trigger_price else level.trigger_price if 0 < low <= level.trigger_price else 0.0
            if fill > 0:
                return _NormalObservation(
                    fill if policy == "AUTO" else 0.0,
                    True,
                    peak,
                    armed,
                    armed_at,
                    mfe,
                    level.trigger_profit_pct,
                    level.effective_trail_pct,
                    level.trigger_price,
                    "ALERT" if policy == "ALERT" else level.state,
                )

        high = float(bar.get("high", 0.0) or 0.0)
        if high > 0:
            peak = max(peak, (high / entry_price - 1.0) * 100.0)
        if not armed and peak >= arm_pct:
            armed = True
            armed_at = int(bar.get("time", 0) or 0)
        if armed:
            mfe = max(mfe, peak)
        else:
            mfe = max(mfe, peak)
    level = protect_level(
        entry_price, peak, arm_pct, giveback_pct,
        dynamic_enabled=dynamic_enabled, sl_price=sl_price,
        atr_pct=atr_pct, atr_multiplier=atr_multiplier,
        previous_trigger_price=carried_trigger,
    )
    return _NormalObservation(
        0.0, False, peak, armed, armed_at, mfe, level.trigger_profit_pct,
        level.effective_trail_pct, level.trigger_price, level.state,
    )


def _normal_trade_metrics(
    position: _Position,
    params: StaticRuleParameters,
    profit_pct: float,
) -> dict[str, Any]:
    armed = bool(position.normal_armed)
    mfe = float(position.mfe_after_arm_pct or 0.0)
    return {
        "normal_policy": params.normal_policy,
        "normal_arm_time": position.normal_arm_time,
        "normal_arm_price": (
            position.avg_price * (1.0 + params.normal_arm_pct / 100.0) if armed else 0.0
        ),
        "mfe_after_arm_pct": mfe,
        "mfe_extra_pct": max(0.0, mfe - params.normal_arm_pct) if armed else 0.0,
        "exit_profit_pct": float(profit_pct),
        "profit_giveback_pct": max(0.0, mfe - float(profit_pct)) if armed else 0.0,
        "exit_mode": "+".join(position.exit_events),
    }


def _elapsed_hours(start: str, end: str) -> float:
    if not start or not end:
        return 0.0
    try:
        left = datetime.fromisoformat(str(start))
        right = datetime.fromisoformat(str(end))
    except ValueError:
        return 0.0
    if left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    elif right.tzinfo is not None and left.tzinfo is None:
        left = left.replace(tzinfo=right.tzinfo)
    return max(0.0, (right - left).total_seconds() / 3600.0)


def _record_profit_path(
    position: _Position,
    observed_at: str,
    *,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    source_resolution: str,
) -> None:
    """Record the observed OHLC path after execution logic consumed the bar."""
    if position.avg_price <= 0:
        return

    def pnl(price: float) -> float:
        return (float(price or 0.0) / position.avg_price - 1.0) * 100.0

    opened = float(open_price or close_price or position.avg_price)
    high = float(high_price or close_price or opened)
    low = float(low_price or close_price or opened)
    closed = float(close_price or opened)
    point = {
        "time": str(observed_at or ""),
        "source_resolution": str(source_resolution or ""),
        "open_profit_pct": pnl(opened),
        "high_profit_pct": pnl(high),
        "low_profit_pct": pnl(low),
        "close_profit_pct": pnl(closed),
    }
    previous_path_peak = max(
        (float(item.get("high_profit_pct", 0.0) or 0.0) for item in position.profit_path),
        default=0.0,
    )
    position.profit_path.append(point)
    high_profit = point["high_profit_pct"]
    low_profit = point["low_profit_pct"]
    if high_profit > previous_path_peak + 1e-9 or not position.peak_at:
        position.peak_at = str(observed_at or "")
    position.peak_profit_pct = max(position.peak_profit_pct, high_profit, 0.0)
    position.mae_profit_pct = min(position.mae_profit_pct, low_profit)


def _profit_path_trade_metrics(
    position: _Position,
    exit_at: str,
    final_profit_pct: float,
) -> dict[str, Any]:
    opened_at = position.opened_at or position.opened_date
    peak_at = position.peak_at or opened_at
    return {
        "mae_profit_pct": float(position.mae_profit_pct),
        "peak_at": peak_at,
        "entry_to_peak_hours": _elapsed_hours(opened_at, peak_at),
        "peak_to_exit_hours": _elapsed_hours(peak_at, exit_at),
        "max_giveback_pct": max(
            0.0, float(position.peak_profit_pct) - float(final_profit_pct),
        ),
        "profit_path": [dict(item) for item in position.profit_path],
    }


def _record_normal_telemetry(
    position: _Position,
    params: StaticRuleParameters,
    observed_at: str,
) -> None:
    """Record +arm/MFE for every exit case without enabling NORMAL."""
    if position.peak_profit_pct + 1e-9 < params.normal_arm_pct:
        return
    if not position.normal_armed:
        position.normal_armed = True
        position.normal_arm_time = observed_at
    position.mfe_after_arm_pct = max(
        position.mfe_after_arm_pct, position.peak_profit_pct,
    )


def exit_comparison_variants(
    scenario: BacktestScenario,
    rule_parameters: dict[str, Any],
) -> list[tuple[BacktestScenario, dict[str, Any]]]:
    """Build DYNAMIC OFF, DYNAMIC ON, and ALERT from one entry scenario."""
    base = StaticRuleParameters.from_dict(rule_parameters)
    specs = (
        ("E + PROTECT DYNAMIC OFF", ["NORMAL", "IND_EXIT"], "AUTO", False),
        ("E + PROTECT DYNAMIC ON", ["NORMAL", "IND_EXIT"], "AUTO", True),
        ("E + PROTECT ALERT", ["NORMAL", "IND_EXIT"], "ALERT", True),
    )
    variants: list[tuple[BacktestScenario, dict[str, Any]]] = []
    for label, modes, policy, dynamic in specs:
        scenario_values = scenario.to_dict()
        scenario_values.update(
            id=f"{scenario.id}-{policy}-{label}",
            name=f"{scenario.name} · {label}",
            em_modes=modes,
        )
        params = base.to_dict()
        params["normal_policy"] = policy
        params["normal_dynamic_enabled"] = dynamic
        variants.append((BacktestScenario.from_dict(scenario_values), params))
    return variants


class BacktestEngine:
    """Deterministic daily replay; it never touches live queue, token or state."""

    def __init__(self, data: HistoricalDataStore, replay: ReplayDataStore | None = None):
        self.data = data
        self.replay = replay or ReplayDataStore(self.data.root / "replay")

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
        requested_params = StaticRuleParameters.from_dict(
            settings.rule_parameters
        ).validate()
        if requested_params.buy_window_enabled and settings.simulation_mode != "REPLAY":
            raise RuntimeError("KHUNG GIỜ MUA cần MODE 2 · REPLAY intraday FULL; không dùng DAILY/AUTO HYBRID.")
        if requested_params.buy_confirmation_enabled and settings.simulation_mode != "REPLAY":
            raise RuntimeError(
                "XÁC NHẬN BUY theo phút chỉ chạy với MODE 2 · REPLAY intraday FULL; "
                "không dùng DAILY hoặc AUTO HYBRID."
            )
        if settings.simulation_mode in {"REPLAY", "AUTO_HYBRID"}:
            return self._run_replay(
                settings, progress=progress, cancelled=cancelled, save=save, carry=carry,
            )
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
        settlement_end = (
            datetime.strptime(settings.end_date, "%Y-%m-%d").date() + timedelta(days=14)
        ).isoformat()
        for offset, symbol in enumerate(all_symbols):
            if cancelled and cancelled():
                raise RuntimeError("Backtest đã hủy.")
            loaded[symbol] = self.data.load_daily(
                symbol,
                settings.start_date,
                settlement_end,
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
        settlement_anchor = min(
            [settings.start_date, *(position.opened_date for position in carried_positions.values())]
        )
        settlement_calendar = sorted({
            key
            for symbol in managed_symbols
            for key in rows_by_symbol.get(symbol, {})
            if key >= settlement_anchor
        })
        settlement_index = {value: index for index, value in enumerate(settlement_calendar)}
        for position in carried_positions.values():
            if position.settle_date != UNKNOWN_SETTLE_DATE or position.opened_date not in settlement_index:
                continue
            due_index = settlement_index[position.opened_date] + 2
            if due_index < len(settlement_calendar):
                position.settle_date = settlement_calendar[due_index]
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
                return _opening_fill_price(
                    bars, settings.fill_session,
                    settings.symbol_exchanges.get(symbol, "HOSE"),
                )
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
                detail_values = details if isinstance(details, dict) else {}
                _record_protect_event(
                    position,
                    params,
                    alert=False,
                    peak_profit_pct=float(
                        detail_values.get("normal_trigger_peak_pct", position.peak_profit_pct)
                        or position.peak_profit_pct
                    ),
                )
            details = dict(details or {})
            equity_after = portfolio_value(day)[0]
            profit_pct = (price / position.avg_price - 1.0) * 100.0 if position.avg_price > 0 else 0.0
            _record_profit_path(
                position, day,
                open_price=price, high_price=price, low_price=price, close_price=price,
                source_resolution="1D",
            )
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
                final_profit_pct = (
                    position.net_pnl / position.entry_value * 100.0
                    if position.entry_value > 0 else 0.0
                )
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
                    pnl_pct=final_profit_pct,
                    equity_after=equity_after,
                    entry_ema_fast=position.entry_ema_fast,
                    entry_ema_slow=position.entry_ema_slow,
                    entry_rsi=position.entry_rsi,
                    exit_ema_fast=position.exit_ema_fast,
                    exit_ema_slow=position.exit_ema_slow,
                    exit_rsi=position.exit_rsi,
                    exit_fills=list(position.exit_fills),
                    **_profit_path_trade_metrics(position, day, final_profit_pct),
                    **_normal_trade_metrics(position, params, final_profit_pct),
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
                        minimum_room = max(0.0, nav * exposure - stock_value)
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
                            minimum_room = min(
                                minimum_room,
                                capital_ledgers[symbol]["available"] / (1.0 + settings.buy_fee_rate),
                            )
                        sizing = size_buy_order(
                            budget_vnd=budget,
                            price_board=open_price,
                            available_cash=cash,
                            nav=nav,
                            force_min_lot_enabled=params.force_min_lot_enabled,
                            minimum_order_room_vnd=minimum_room,
                            buy_fee_rate=settings.buy_fee_rate,
                        )
                        quantity = sizing.quantity
                        gross = open_price * quantity * 1000.0
                        fee = gross * settings.buy_fee_rate
                        if quantity > 0 and gross + fee <= cash:
                            cash -= gross + fee
                            due_index = settlement_index[day] + 2
                            settle_day = (
                                settlement_calendar[due_index]
                                if due_index < len(settlement_calendar)
                                else UNKNOWN_SETTLE_DATE
                            )
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
                                fees=fee, net_pnl=-fee,
                                entry_market_state=entry_phase,
                                entry_exposure_pct=exposure * 100.0,
                                entry_reason=order.reason or order.event,
                                entry_signal_date=order.created_date,
                                entry_rule=_buy_rule_text(params),
                                cycle_id=cycle_id,
                                entry_value=gross + fee,
                                sl_pct=params.reentry_sl_pct if attempt > 0 else params.initial_sl_pct,
                                entry_ema_fast=entry_indicators["ema_fast"],
                                entry_ema_slow=entry_indicators["ema_slow"],
                                entry_rsi=entry_indicators["rsi"],
                                opened_at=day,
                            )
                            positions[symbol] = position
                            _record_profit_path(
                                position, day,
                                open_price=open_price, high_price=open_price,
                                low_price=open_price, close_price=open_price,
                                source_resolution="1D",
                            )
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
                        # DAILY has no afternoon price.  On T+2 it must not use
                        # the morning Open, so it waits for the next session;
                        # REPLAY handles the legal 13:00 boundary precisely.
                        if day <= position.settle_date:
                            order.settlement_waited = True
                            continue
                        if (
                            order.settlement_waited
                            and settings.sell_wait_policy == "RECHECK"
                            and _is_pending_protect(order)
                            and not _pending_protect_matches(
                                order, position, params, open_price,
                                atr_pct=average_true_range_pct(history[symbol][:-1]),
                            )
                        ):
                            pending.pop(symbol, None)
                            continue
                        quantity = sell_quantity_for_fraction(
                            position.quantity, order.fraction
                        )
                        if quantity > 0:
                            fill_details = {
                                **dict(order.details or {}),
                                "settlement_waited": bool(order.settlement_waited),
                                "trigger_time": order.created_time or order.created_date,
                                "fill_time": day,
                            }
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
                                details=fill_details,
                            )
                        pending.pop(symbol, None)


            fill_pending()

            # SL has absolute priority. PROTECT can then use intraday replay;
            # indicator E remains a daily-close rule by definition.
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
                    if day > position.settle_date:
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
                        position.peak_profit_pct = max(
                            position.peak_profit_pct,
                            (fill / position.avg_price - 1.0) * 100.0,
                        )
                        _record_normal_telemetry(position, params, day)
                        details = {
                            "indicators": indicator_snapshot(
                                history[symbol],
                                params.buy_ema_fast, params.buy_ema_slow, params.rsi_period,
                                sell_fast=params.sell_ema_fast, sell_slow=params.sell_ema_slow,
                            ),
                            "take_profit_pct": params.take_profit_pct,
                            "target_price": target,
                        }
                        if day > position.settle_date:
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
                    "NORMAL" in position.em_modes
                    and _protect_event_ready(
                        position, params, alert=params.normal_policy == "ALERT",
                    )
                ):
                    intraday = execution_resolution.get(symbol) != "1D"
                    normal_bars = execution_day(symbol, day) if intraday else [row]
                    atr_pct = average_true_range_pct(history[symbol][:-1])
                    observation = _normal_policy_fill(
                        normal_bars,
                        policy=params.normal_policy,
                        entry_price=position.avg_price,
                        peak_profit_pct=position.peak_profit_pct,
                        already_armed=position.normal_armed,
                        mfe_after_arm_pct=position.mfe_after_arm_pct,
                        arm_pct=params.normal_arm_pct,
                        giveback_pct=params.normal_giveback_pct,
                        dynamic_enabled=params.normal_dynamic_enabled,
                        sl_price=position.avg_price * (1.0 + position.sl_pct / 100.0),
                        atr_pct=atr_pct,
                        atr_multiplier=params.normal_atr_multiplier,
                        previous_trigger_price=position.normal_trigger_price,
                    )
                    was_armed = position.normal_armed
                    position.normal_armed = observation.armed
                    position.peak_profit_pct = max(
                        position.peak_profit_pct, observation.peak_profit_pct,
                    )
                    position.mfe_after_arm_pct = max(
                        position.mfe_after_arm_pct, observation.mfe_after_arm_pct,
                    )
                    position.normal_protected_profit_pct = observation.protected_profit_pct
                    position.normal_effective_trail_pct = observation.effective_trail_pct
                    position.normal_trigger_price = observation.trigger_price
                    if observation.armed and not was_armed and not position.normal_arm_time:
                        position.normal_arm_time = day
                        if observation.armed_at:
                            position.normal_arm_time = datetime.fromtimestamp(
                                observation.armed_at, VN_TZ,
                            ).isoformat()
                    normal_fill = observation.fill
                    if observation.triggered and params.normal_policy == "ALERT":
                        _record_protect_event(
                            position, params, alert=True,
                            peak_profit_pct=observation.peak_profit_pct,
                        )
                        signal_history.append({
                            "date": day, "symbol": symbol, "action": "WAIT",
                            "signal": "PROTECT", "event": "PROTECT_ALERT",
                            "reason": "PROTECT_ALERT", "market_state": current_phase,
                            "details": {
                                "normal_policy": "ALERT",
                                "normal_state": "ALERT",
                                "normal_mfe_pct": observation.peak_profit_pct,
                                "normal_peak_price": position.avg_price * (
                                    1.0 + observation.peak_profit_pct / 100.0
                                ),
                                "normal_effective_trail_pct": observation.effective_trail_pct,
                                "normal_trigger_price": observation.trigger_price,
                                "normal_protected_profit_pct": observation.protected_profit_pct,
                                "normal_atr_pct": atr_pct,
                                "normal_atr_multiplier": params.normal_atr_multiplier,
                                "normal_dynamic_enabled": params.normal_dynamic_enabled,
                                "sell_share_pct": params.normal_sell_pct,
                                "hypothetical_quantity": sell_quantity_for_fraction(
                                    position.quantity, params.normal_sell_pct / 100.0,
                                ),
                            },
                        })
                    if normal_fill > 0:
                        details = {
                            "triggered_events": ["NORMAL_PROTECTION"],
                            "normal_policy": params.normal_policy,
                            "normal_arm_time": position.normal_arm_time,
                            "mfe_after_arm_pct": position.mfe_after_arm_pct,
                            "normal_protected_profit_pct": position.normal_protected_profit_pct,
                            "normal_effective_trail_pct": position.normal_effective_trail_pct,
                            "normal_trigger_price": position.normal_trigger_price,
                            "normal_atr_pct": atr_pct,
                            "normal_atr_multiplier": params.normal_atr_multiplier,
                            "normal_dynamic_enabled": params.normal_dynamic_enabled,
                            "normal_trigger_peak_pct": position.peak_profit_pct,
                            "normal_rearm_after_pct": protect_rearm_mfe(
                                position.peak_profit_pct, params.normal_giveback_pct,
                            ),
                            "peak_profit_pct": position.peak_profit_pct,
                            "execution_resolution": execution_resolution.get(symbol),
                        }
                        share = min(1.0, max(0.0, params.normal_sell_pct / 100.0))
                        if day > position.settle_date:
                            quantity = sell_quantity_for_fraction(position.quantity, share)
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
                    _record_profit_path(
                        position, day,
                        open_price=float(row.get("open", row.get("close", 0.0)) or 0.0),
                        high_price=float(row.get("high", row.get("close", 0.0)) or 0.0),
                        low_price=float(row.get("low", row.get("close", 0.0)) or 0.0),
                        close_price=float(row.get("close", 0.0) or 0.0),
                        source_resolution=execution_resolution.get(symbol, "1D"),
                    )
                    _record_normal_telemetry(position, params, day)
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
                        "is_reentry": position.is_reentry,
                        "em_modes": position.em_modes,
                        "normal_protection_done": position.normal_done,
                        "normal_execution_managed": True,
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
                if (
                    existing and existing.side == "SELL" and existing.settlement_waited
                    and settings.sell_wait_policy == "RECHECK"
                    and position is not None
                    and day >= position.settle_date
                ):
                    recheck_price = float(row.get("close", 0.0) or 0.0)
                    matches = (
                        _pending_protect_matches(
                            existing, position, params, recheck_price,
                            atr_pct=average_true_range_pct(history[symbol][:-1]),
                        )
                        if _is_pending_protect(existing)
                        else _pending_matches_decision(existing, decision)
                    )
                    if matches is False:
                        pending.pop(symbol, None)
                        existing = None
                if existing and existing.side == "SELL" and decision.action == "SELL":
                    triggered = (
                        decision.details.get("triggered_events", [])
                        if isinstance(decision.details, dict) else []
                    )
                    if _exit_priority(
                        decision.event or decision.reason, triggered,
                    ) > _exit_priority(existing.event, existing.triggered_events):
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
                        current_phase, list(triggered or []), day <= position.settle_date,
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
                # A position still open may already have been trimmed by
                # PROTECT; retain those fills in the report.
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
                pnl_pct=(
                    position.net_pnl
                    + (latest_price(position.symbol, last_day) - position.avg_price)
                    * position.quantity * 1000.0
                ) / position.entry_value * 100.0 if position.entry_value else 0.0,
                entry_ema_fast=position.entry_ema_fast,
                entry_ema_slow=position.entry_ema_slow,
                entry_rsi=position.entry_rsi,
                **_profit_path_trade_metrics(
                    position,
                    last_day,
                    (
                        position.net_pnl
                        + (latest_price(position.symbol, last_day) - position.avg_price)
                        * position.quantity * 1000.0
                    ) / position.entry_value * 100.0 if position.entry_value else 0.0,
                ),
                **_normal_trade_metrics(
                    position,
                    params,
                    (
                        position.net_pnl
                        + (latest_price(position.symbol, last_day) - position.avg_price)
                        * position.quantity * 1000.0
                    ) / position.entry_value * 100.0 if position.entry_value else 0.0,
                ),
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

    def _run_replay(
        self,
        settings: BacktestConfig,
        *,
        progress: Callable[[float, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        save: bool = False,
        carry: dict[str, Any] | None = None,
    ) -> BacktestResult:
        """Replay imported intraday bars while indicators remain daily.

        A temporary, unfinished 1D candle is rebuilt after every source bar.
        Decisions therefore see exactly the history known at that close; only a
        later bar may fill the order.
        """
        if settings.auto_market_phase:
            raise RuntimeError("MODE 2 · REPLAY cần trạng thái thị trường cố định theo scenario.")
        started = datetime.now().astimezone().isoformat()
        carried = carry or {}
        carried_positions: dict[str, _Position] = dict(carried.get("positions") or {})
        carried_pending: dict[str, _Pending] = dict(carried.get("pending") or {})
        active_symbols = list(settings.symbols)
        managed_symbols = list(dict.fromkeys([
            *active_symbols, *carried_positions.keys(), *carried_pending.keys(),
        ]))
        params = StaticRuleParameters.from_dict(settings.rule_parameters)
        params.whipsaw_enabled = settings.whipsaw_enabled
        if not settings.loss_lock_enabled:
            params.loss_lock_count = 10**9
        params.exposure = {
            key: settings.fixed_exposure_pct / 100.0 for key in EXPOSURE_DEFAULTS
        }
        settings.rule_parameters = params.to_dict()
        rule = StaticRule(params)

        loaded: dict[str, list[dict[str, Any]]] = {}
        settlement_end = (
            datetime.strptime(settings.end_date, "%Y-%m-%d").date() + timedelta(days=14)
        ).isoformat()
        for offset, symbol in enumerate(managed_symbols):
            if cancelled and cancelled():
                raise RuntimeError("Backtest đã hủy.")
            loaded[symbol] = self.data.load_daily(
                symbol, settings.start_date, settlement_end,
                warmup_sessions=settings.warmup_sessions,
                progress=(
                    lambda message, n=offset: progress(
                        n / max(1, len(managed_symbols)) * .15, message,
                    )
                ) if progress else None,
            )
            if not loaded[symbol]:
                raise RuntimeError(f"Không có dữ liệu 1D warm-up cho {symbol}.")
        rows_by_symbol = {
            symbol: {bar_date(row).isoformat(): dict(row) for row in rows}
            for symbol, rows in loaded.items()
        }
        calendar = sorted({
            day
            for values in rows_by_symbol.values()
            for day in values
            if settings.start_date <= day <= settings.end_date
            and day > str(carried.get("last_processed_date") or "")
        })
        if not calendar:
            raise RuntimeError("Khoảng ngày đã chọn không có phiên giao dịch mới.")
        calendar_index = {day: index for index, day in enumerate(calendar)}
        settlement_anchor = min(
            [settings.start_date, *(position.opened_date for position in carried_positions.values())]
        )
        settlement_calendar = sorted({
            day for values in rows_by_symbol.values() for day in values if day >= settlement_anchor
        })
        settlement_index = {day: index for index, day in enumerate(settlement_calendar)}
        for position in carried_positions.values():
            if position.settle_date != UNKNOWN_SETTLE_DATE or position.opened_date not in settlement_index:
                continue
            due_index = settlement_index[position.opened_date] + 2
            if due_index < len(settlement_calendar):
                position.settle_date = settlement_calendar[due_index]

        replay_days: dict[str, dict[str, list[dict[str, Any]]]] = {
            symbol: {} for symbol in managed_symbols
        }
        source_resolution: dict[str, dict[str, str]] = {symbol: {} for symbol in managed_symbols}
        source_quality: dict[str, dict[str, str]] = {symbol: {} for symbol in managed_symbols}
        missing: list[str] = []
        fallback_days: list[str] = []
        requested_replay_resolution = (
            None if settings.execution_resolution == "AUTO"
            else settings.execution_resolution
        )
        for symbol in managed_symbols:
            for day in calendar:
                daily = rows_by_symbol.get(symbol, {}).get(day)
                if daily is None:
                    continue
                bars, resolution, status = self.replay.load_day(
                    symbol, day, resolution=requested_replay_resolution,
                    complete_only=True,
                )
                if bars:
                    replay_days[symbol][day] = bars
                    source_resolution[symbol][day] = resolution
                    source_quality[symbol][day] = status
                elif settings.simulation_mode == "REPLAY":
                    missing.append(f"{symbol} {day}")
                else:
                    source_resolution[symbol][day] = "1D"
                    source_quality[symbol][day] = "FALLBACK_1D"
                    fallback_days.append(f"{symbol} {day}")
        if missing:
            sample = ", ".join(missing[:12])
            suffix = f" và {len(missing) - 12} ngày/mã khác" if len(missing) > 12 else ""
            raise RuntimeError(f"REPLAY thiếu dữ liệu FULL: {sample}{suffix}.")

        symbol_exchanges: dict[str, str] = {}
        unknown_exchanges: list[str] = []
        for symbol in managed_symbols:
            exchange = (
                normalize_exchange(settings.symbol_exchanges.get(symbol))
                or self.replay.exchange_for(symbol, requested_replay_resolution)
            )
            # Compatibility for old AUTO-HYBRID runs containing no replay at
            # all. Historically that path was explicitly the HOSE daily model.
            if not exchange and settings.simulation_mode == "AUTO_HYBRID" and all(
                source_quality[symbol].get(day) == "FALLBACK_1D"
                for day in calendar if day in rows_by_symbol.get(symbol, {})
            ):
                exchange = "HOSE"
            if exchange:
                symbol_exchanges[symbol] = exchange
            else:
                unknown_exchanges.append(symbol)
        if unknown_exchanges:
            raise RuntimeError(
                "Chưa xác định SÀN cho: " + ", ".join(unknown_exchanges)
                + ". Hãy chọn HOSE/HNX/UPCOM khi import dữ liệu."
            )
        settings.symbol_exchanges = dict(symbol_exchanges)

        history: dict[str, list[dict[str, Any]]] = {}
        warmup_sources: dict[str, str] = {}
        # EMA can be seeded from the available replay closes immediately.  If
        # RSI does not yet have ``period + 2`` closes, crossover_signal simply
        # stays silent until it does; importing foreign-vendor daily prices to
        # manufacture the missing warm-up would be worse than skipping those
        # first source days.
        replay_warmup_required = 1
        for symbol, rows in loaded.items():
            replay_warmup = self.replay.load_daily_aggregates(
                symbol, before=settings.start_date,
            )
            if len(replay_warmup) >= replay_warmup_required:
                history[symbol] = replay_warmup
                warmup_sources[symbol] = "REPLAY_INTRADAY_AGGREGATED"
            else:
                history[symbol] = [
                    dict(row) for row in rows
                    if bar_date(row).isoformat() < settings.start_date
                ]
                warmup_sources[symbol] = "DAILY_FALLBACK"
        indicator_streams: dict[str, dict[str, Any]] = {
            symbol: indicator_snapshot(
                values,
                params.buy_ema_fast,
                params.buy_ema_slow,
                params.rsi_period,
                sell_fast=params.sell_ema_fast,
                sell_slow=params.sell_ema_slow,
            )
            for symbol, values in history.items()
        }
        marks = {
            symbol: float(values[-1].get("close", 0.0) or 0.0) if values else 0.0
            for symbol, values in history.items()
        }
        positions: dict[str, _Position] = carried_positions
        pending: dict[str, _Pending] = carried_pending
        buy_confirmations: dict[str, dict[str, Any]] = {
            str(symbol).upper(): dict(value)
            for symbol, value in (carried.get("buy_confirmations") or {}).items()
            if isinstance(value, dict)
        }
        cash = float(carried.get("cash", settings.initial_capital))
        loss_streaks = {symbol: 0 for symbol in managed_symbols}
        loss_streaks.update(carried.get("loss_streaks") or {})
        loss_locked_until: dict[str, datetime | None] = {symbol: None for symbol in managed_symbols}
        loss_locked_until.update(carried.get("loss_locked_until") or {})
        capital_ledgers: dict[str, dict[str, float]] = dict(carried.get("capital_ledgers") or {})
        cycle_numbers = {symbol: 0 for symbol in managed_symbols}
        cycle_numbers.update(carried.get("cycle_numbers") or {})
        last_decisions: dict[str, Any] = dict(carried.get("last_decisions") or {})
        events: list[BacktestEvent] = []
        completed: list[BacktestTrade] = []
        equity_curve: list[dict[str, Any]] = []
        phase_history: list[dict[str, Any]] = []
        signal_history: list[dict[str, Any]] = []
        signal_dedupe: dict[tuple[str, str], tuple[str, ...]] = {}
        total_fees = total_tax = 0.0
        buy_count = sell_count = 0
        current_phase = settings.fixed_market_phase

        def iso_time(stamp: int) -> str:
            return datetime.fromtimestamp(int(stamp), VN_TZ).isoformat()

        def portfolio_value() -> tuple[float, float]:
            stock = sum(
                marks.get(symbol, position.avg_price) * position.quantity * 1000.0
                for symbol, position in positions.items()
            )
            return cash + stock, stock

        def sell_position(
            position: _Position,
            quantity: int,
            price: float,
            day: str,
            event: str,
            *,
            stamp: int,
            signal_time: str = "",
            reason: str = "",
            details: dict[str, Any] | None = None,
            triggered: list[str] | None = None,
            resolution: str = "1D",
            quality: str = "FULL",
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
            names = list(triggered or []) or [event]
            for name in names:
                if name and name not in position.exit_events:
                    position.exit_events.append(name)
            if "NORMAL_PROTECTION" in names:
                detail_values = details if isinstance(details, dict) else {}
                _record_protect_event(
                    position,
                    params,
                    alert=False,
                    peak_profit_pct=float(
                        detail_values.get("normal_trigger_peak_pct", position.peak_profit_pct)
                        or position.peak_profit_pct
                    ),
                )
            detail_values = dict(details or {})
            _record_profit_path(
                position, iso_time(stamp),
                open_price=price, high_price=price, low_price=price, close_price=price,
                source_resolution=resolution,
            )
            indicators = _indicator_columns(detail_values)
            position.exit_fills.append({
                "event": "+".join(names), "quantity": quantity, "price": price,
                "fill_time": iso_time(stamp), "source_resolution": resolution,
            })
            position.exit_ema_fast = indicators["ema_fast"]
            position.exit_ema_slow = indicators["ema_slow"]
            position.exit_rsi = indicators["rsi"]
            profit_pct = (price / position.avg_price - 1.0) * 100.0 if position.avg_price else 0.0
            events.append(BacktestEvent(
                day, position.trade_id, position.symbol, "SELL", event, quantity, price,
                gross, fee, tax, cash, current_phase, pnl,
                signal_date=(signal_time or day)[:10], reason=reason or event,
                details=detail_values, cycle_id=position.cycle_id,
                profit_pct=profit_pct, peak_profit_pct=position.peak_profit_pct,
                equity_after=portfolio_value()[0],
                signal_time=signal_time, decision_time=signal_time,
                fill_time=iso_time(stamp), simulation_mode=settings.simulation_mode,
                source_resolution=resolution, data_quality=quality,
                **indicators,
            ))
            if position.quantity > 0:
                return
            avg_exit = position.exit_value / max(1, position.sold_quantity) / 1000.0
            outcome = "WIN" if position.net_pnl >= 0 else "LOSS"
            equity_after = portfolio_value()[0]
            final_profit_pct = (
                position.net_pnl / position.entry_value * 100.0
                if position.entry_value else 0.0
            )
            completed.append(BacktestTrade(
                trade_id=position.trade_id, symbol=position.symbol,
                opened_date=position.opened_date, closed_date=day,
                entry_quantity=position.entry_quantity, remaining_quantity=0,
                avg_entry_price=position.avg_price, avg_exit_price=avg_exit,
                fees=position.fees, tax=position.tax, net_pnl=position.net_pnl,
                outcome=outcome, exit_events=list(position.exit_events),
                entry_market_state=position.entry_market_state,
                entry_exposure_pct=position.entry_exposure_pct,
                entry_reason=position.entry_reason,
                entry_signal_date=position.entry_signal_date,
                entry_rule=position.entry_rule, cycle_id=position.cycle_id,
                sessions_held=max(0, calendar_index.get(day, 0) - calendar_index.get(position.opened_date, 0)),
                entry_value=position.entry_value, sl_pct=position.sl_pct,
                peak_profit_pct=position.peak_profit_pct,
                pnl_pct=final_profit_pct,
                equity_after=equity_after,
                entry_ema_fast=position.entry_ema_fast,
                entry_ema_slow=position.entry_ema_slow, entry_rsi=position.entry_rsi,
                exit_ema_fast=position.exit_ema_fast,
                exit_ema_slow=position.exit_ema_slow, exit_rsi=position.exit_rsi,
                exit_fills=list(position.exit_fills),
                **_profit_path_trade_metrics(position, iso_time(stamp), final_profit_pct),
                **_normal_trade_metrics(position, params, final_profit_pct),
            ))
            if outcome == "WIN":
                loss_streaks[position.symbol] = 0
                loss_locked_until[position.symbol] = None
            else:
                loss_streaks[position.symbol] += 1
                if settings.loss_lock_enabled and loss_streaks[position.symbol] >= params.loss_lock_count:
                    loss_locked_until[position.symbol] = datetime.fromtimestamp(stamp, VN_TZ) + timedelta(
                        hours=settings.loss_lock_hours
                    )
            ledger = capital_ledgers.get(position.symbol)
            if params.no_compound_enabled and ledger:
                ledger["available"] = min(
                    ledger["principal"], max(0.0, ledger["available"] + position.net_pnl),
                )
            positions.pop(position.symbol, None)

        def fill_is_eligible(order: _Pending, stamp: int, *, fallback_open: bool) -> bool:
            local = datetime.fromtimestamp(stamp, VN_TZ)
            if order.created_time:
                try:
                    if stamp <= int(datetime.fromisoformat(order.created_time).timestamp()):
                        return False
                except ValueError:
                    pass
            if fallback_open:
                return order.created_date < local.date().isoformat()
            minute = local.hour * 60 + local.minute
            exchange = symbol_exchanges.get(order.symbol, "")
            phase = phase_at_minute(exchange, minute)
            if order.created_date == local.date().isoformat():
                # New intraday signals fill on the next continuous bar. Auctions
                # are never fabricated between source bars.
                return phase == "OPEN"
            if exchange == "HOSE" and settings.fill_session == "ATO":
                size = 60 if order.source_resolution == "1H" else int(order.source_resolution or 1)
                expected = exchange_open_minute(exchange)
                return (
                    minute == expected - expected % max(1, size)
                    or (phase == "OPEN" and minute > expected)
                )
            if exchange == "HOSE":
                return phase == "OPEN" and minute > exchange_open_minute(exchange)
            # HNX/UPCOM have no ATO path; use their first real continuous bar.
            return phase == "OPEN"

        def fill_pending_symbol(
            symbol: str,
            day: str,
            stamp: int,
            price: float,
            resolution: str,
            quality: str,
            *,
            fallback_open: bool = False,
        ) -> None:
            nonlocal cash, total_fees, buy_count
            order = pending.get(symbol)
            if order and order.side == "BUY" and params.buy_window_enabled:
                local = datetime.fromtimestamp(stamp, VN_TZ)
                if (local.date().isoformat() != order.created_date
                        or local.hour * 60 + local.minute >= exchange_close_minute(symbol_exchanges[symbol])):
                    pending.pop(symbol, None)
                    signal_history.append({
                        "symbol": symbol, "date": day, "time": iso_time(stamp),
                        "signal_time": (order.details.get("buy_window") or {}).get("signal_time", order.created_time),
                        "decision_time": iso_time(stamp), "signal": "BUY", "action": "WAIT",
                        "reason": "BUY_WINDOW_EXPIRED", "details": order.details,
                        "simulation_mode": settings.simulation_mode,
                    })
                    return
                end_minute = exchange_close_minute(symbol_exchanges[symbol])
                if not in_buy_window(local, params.buy_window_start, f"{end_minute // 60:02d}:{end_minute % 60:02d}"):
                    return
            if not order or not fill_is_eligible(order, stamp, fallback_open=fallback_open):
                return
            if order.side == "BUY":
                if symbol in positions or len(positions) >= params.max_positions:
                    pending.pop(symbol, None)
                    return
                nav, stock_value = portfolio_value()
                entry_phase = order.market_state if order.market_state in EXPOSURE_DEFAULTS else current_phase
                exposure = params.exposure.get(entry_phase, 0.0)
                minimum_room = max(0.0, nav * exposure - stock_value)
                budget = order_budget(
                    nav=nav, exposure=exposure, max_positions=params.max_positions,
                    current_stock_value=stock_value, pending_buy_value=0.0,
                    available_cash=cash, fee_rate=settings.buy_fee_rate,
                )
                if params.no_compound_enabled and symbol in capital_ledgers:
                    budget = min(budget, capital_ledgers[symbol]["available"])
                    minimum_room = min(
                        minimum_room,
                        capital_ledgers[symbol]["available"] / (1.0 + settings.buy_fee_rate),
                    )
                sizing = size_buy_order(
                    budget_vnd=budget, price_board=price, available_cash=cash,
                    nav=nav, force_min_lot_enabled=params.force_min_lot_enabled,
                    minimum_order_room_vnd=minimum_room,
                    buy_fee_rate=settings.buy_fee_rate,
                )
                quantity = sizing.quantity
                gross = price * quantity * 1000.0
                fee = gross * settings.buy_fee_rate
                if quantity > 0 and gross + fee <= cash:
                    cash -= gross + fee
                    due_index = settlement_index.get(day, 0) + 2
                    settle_day = (
                        settlement_calendar[due_index]
                        if day in settlement_index and due_index < len(settlement_calendar)
                        else UNKNOWN_SETTLE_DATE
                    )
                    principal = gross + fee
                    ledger = capital_ledgers.setdefault(symbol, {"principal": principal, "available": principal})
                    principal = min(principal, ledger["available"]) if params.no_compound_enabled else principal
                    attempt = loss_streaks[symbol]
                    if attempt == 0:
                        cycle_numbers[symbol] += 1
                    cycle_id = f"{symbol}-{cycle_numbers[symbol]:02d}" + (f".{attempt}" if attempt else "")
                    indicators = _indicator_columns(order.details)
                    confirmation_values = order.details.get("buy_confirmation") or {}
                    original_signal_time = str(
                        (order.details.get("buy_window") or {}).get("signal_time")
                        or confirmation_values.get("signal_time") or order.created_time
                    )
                    position = _Position(
                        uuid.uuid4().hex, symbol, quantity, quantity, price, day, settle_day,
                        fee, principal, list(settings.em_modes), is_reentry=attempt > 0,
                        fees=fee, net_pnl=-fee,
                        entry_market_state=entry_phase, entry_exposure_pct=exposure * 100.0,
                        entry_reason=order.reason or order.event,
                        entry_signal_date=(original_signal_time or order.created_date)[:10],
                        entry_rule=_buy_rule_text(params),
                        cycle_id=cycle_id, entry_value=gross + fee,
                        sl_pct=params.reentry_sl_pct if attempt else params.initial_sl_pct,
                        entry_ema_fast=indicators["ema_fast"],
                        entry_ema_slow=indicators["ema_slow"], entry_rsi=indicators["rsi"],
                        opened_at=iso_time(stamp),
                    )
                    positions[symbol] = position
                    _record_profit_path(
                        position, iso_time(stamp),
                        open_price=price, high_price=price, low_price=price, close_price=price,
                        source_resolution=resolution,
                    )
                    total_fees += fee
                    buy_count += 1
                    events.append(BacktestEvent(
                        day, position.trade_id, symbol, "BUY", "ENTRY_BUY", quantity,
                        price, gross, fee, 0.0, cash, entry_phase, -fee,
                        signal_date=(original_signal_time or order.created_date)[:10], reason=order.reason or "Tín hiệu BUY",
                        details=dict(order.details), cycle_id=cycle_id,
                        equity_after=portfolio_value()[0], signal_time=original_signal_time,
                        decision_time=order.created_time, fill_time=iso_time(stamp),
                        simulation_mode=settings.simulation_mode,
                        source_resolution=resolution, data_quality=quality, **indicators,
                    ))
                pending.pop(symbol, None)
                return
            position = positions.get(symbol)
            if not position or position.trade_id != order.trade_id:
                pending.pop(symbol, None)
                return
            if not stock_is_sellable_after_settlement(
                position.settle_date, datetime.fromtimestamp(stamp, VN_TZ),
            ):
                order.settlement_waited = True
                return
            if (
                order.settlement_waited and settings.sell_wait_policy == "RECHECK"
            ):
                matches = (
                    _pending_protect_matches(
                        order, position, params, price,
                        atr_pct=average_true_range_pct(history[symbol]),
                    )
                    if _is_pending_protect(order)
                    else _pending_matches_decision(order, last_decisions.get(symbol))
                )
                if matches is False:
                    pending.pop(symbol, None)
                    return
            quantity = sell_quantity_for_fraction(position.quantity, order.fraction)
            if quantity > 0:
                fill_details = {
                    **dict(order.details or {}),
                    "settlement_waited": bool(order.settlement_waited),
                    "trigger_time": order.created_time or order.created_date,
                    "fill_time": iso_time(stamp),
                }
                sell_position(
                    position, quantity, price, day, order.event, stamp=stamp,
                    signal_time=order.created_time, reason=order.reason,
                    details=fill_details, triggered=order.triggered_events,
                    resolution=resolution, quality=quality,
                )
            pending.pop(symbol, None)

        partials: dict[str, dict[str, Any]] = {}
        total_source_bars = sum(
            len(bars) for values in replay_days.values() for bars in values.values()
        ) + len(fallback_days)
        processed_source_bars = 0
        # A long 1-minute replay can contain tens of thousands of bars.  Posting
        # one Tk callback per bar makes the popup spend longer repainting than
        # the engine spends calculating, so only publish each whole-percent
        # milestone.  Cancellation is still checked for every source timestamp.
        last_progress_bucket = -1
        for day_number, day in enumerate(calendar):
            if cancelled and cancelled():
                raise RuntimeError("Backtest đã hủy.")
            phase_history.append({"date": day, "candidate": current_phase, "confirmed": current_phase})
            partials.clear()

            # DAILY fallback can only fill an overnight order at its known day
            # open. It never pretends to know an intraday path.
            for symbol in managed_symbols:
                if source_quality[symbol].get(day) != "FALLBACK_1D":
                    continue
                fallback_minute = exchange_open_minute(symbol_exchanges[symbol])
                fallback_stamp = int((datetime.combine(
                    datetime.strptime(day, "%Y-%m-%d").date(), time(0, 0), VN_TZ,
                ) + timedelta(minutes=fallback_minute)).timestamp())
                daily = rows_by_symbol[symbol][day]
                opened = float(daily.get("open", 0.0) or daily.get("close", 0.0) or 0.0)
                marks[symbol] = opened
                fill_pending_symbol(
                    symbol, day, fallback_stamp, opened, "1D", "FALLBACK_1D", fallback_open=True,
                )

            by_time: dict[int, dict[str, dict[str, Any]]] = {}
            for symbol in managed_symbols:
                bars = replay_days[symbol].get(day)
                if bars:
                    for bar in bars:
                        by_time.setdefault(int(bar["time"]), {})[symbol] = bar
                else:
                    daily = rows_by_symbol.get(symbol, {}).get(day)
                    if daily is not None:
                        close_stamp = int((datetime.combine(
                            datetime.strptime(day, "%Y-%m-%d").date(), time(0, 0), VN_TZ,
                        ) + timedelta(minutes=exchange_close_minute(symbol_exchanges[symbol]))).timestamp())
                        fallback = dict(daily)
                        fallback["time"] = close_stamp
                        fallback["_fallback"] = True
                        by_time.setdefault(close_stamp, {})[symbol] = fallback

            for stamp in sorted(by_time):
                current = by_time[stamp]
                for symbol in managed_symbols:
                    bar = current.get(symbol)
                    if not bar or bar.get("_fallback"):
                        continue
                    opened = float(bar.get("open", 0.0) or 0.0)
                    marks[symbol] = opened
                    fill_pending_symbol(
                        symbol, day, stamp, opened,
                        source_resolution[symbol][day], source_quality[symbol][day],
                    )

                for symbol in managed_symbols:
                    bar = current.get(symbol)
                    if not bar:
                        continue
                    previous = partials.get(symbol)
                    if previous is None:
                        partial = {
                            "time": stamp, "open": float(bar.get("open", 0.0) or 0.0),
                            "high": float(bar.get("high", 0.0) or 0.0),
                            "low": float(bar.get("low", 0.0) or 0.0),
                            "close": float(bar.get("close", 0.0) or 0.0),
                            "volume": float(bar.get("volume", 0.0) or 0.0), "closed": False,
                        }
                    else:
                        partial = dict(previous)
                        partial["time"] = stamp
                        partial["high"] = max(float(partial["high"]), float(bar.get("high", 0.0) or 0.0))
                        partial["low"] = min(float(partial["low"]), float(bar.get("low", 0.0) or 0.0))
                        partial["close"] = float(bar.get("close", 0.0) or 0.0)
                        partial["volume"] = float(partial["volume"]) + float(bar.get("volume", 0.0) or 0.0)
                    partials[symbol] = partial
                    marks[symbol] = float(partial["close"])

                for symbol in managed_symbols:
                    bar = current.get(symbol)
                    partial = partials.get(symbol)
                    if not bar or not partial:
                        continue
                    resolution = source_resolution[symbol].get(day, "1D")
                    quality = source_quality[symbol].get(day, "FALLBACK_1D")
                    previous_indicators = indicator_streams.get(symbol, {})
                    current_indicators = indicator_snapshot(
                        [*history[symbol], partial],
                        params.buy_ema_fast,
                        params.buy_ema_slow,
                        params.rsi_period,
                        sell_fast=params.sell_ema_fast,
                        sell_slow=params.sell_ema_slow,
                    )
                    # Advance on every source bar, including a bar consumed by
                    # a fill or protection exit.  The next comparison must
                    # never jump back over a processed minute.
                    indicator_streams[symbol] = current_indicators
                    position = positions.get(symbol)
                    event_count_before_exit = len(events)
                    if position and symbol not in pending:
                        stop_pct = params.reentry_sl_pct if position.is_reentry else params.initial_sl_pct
                        stop = position.avg_price * (1.0 + stop_pct / 100.0)
                        opened = float(bar.get("open", 0.0) or 0.0)
                        low = float(bar.get("low", 0.0) or 0.0)
                        high = float(bar.get("high", 0.0) or 0.0)
                        details = {"indicators": indicator_snapshot(
                            [*history[symbol], partial], params.buy_ema_fast, params.buy_ema_slow,
                            params.rsi_period, sell_fast=params.sell_ema_fast,
                            sell_slow=params.sell_ema_slow,
                        )}
                        if low > 0 and low <= stop:
                            fill = opened if 0 < opened < stop else stop
                            details.update(sl_value=stop_pct, stop_price=stop)
                            if stock_is_sellable_after_settlement(
                                position.settle_date, datetime.fromtimestamp(stamp, VN_TZ),
                            ):
                                sell_position(
                                    position, round_lot_down(position.quantity), fill, day,
                                    "STOP_LOSS", stamp=stamp, signal_time=iso_time(stamp),
                                    reason="STOP_LOSS", details=details,
                                    resolution=resolution, quality=quality,
                                )
                            else:
                                pending[symbol] = _Pending(
                                    "SELL", symbol, day, "STOP_LOSS", 1.0, position.trade_id,
                                    current_phase, settlement_waited=True, reason="STOP_LOSS",
                                    details=details, created_time=iso_time(stamp),
                                    source_resolution=resolution, data_quality=quality,
                                )
                            position = positions.get(symbol)
                        elif (
                            position and "TP" in position.em_modes and params.take_profit_pct > 0
                            and high >= position.avg_price * (1.0 + params.take_profit_pct / 100.0)
                        ):
                            target = position.avg_price * (1.0 + params.take_profit_pct / 100.0)
                            fill = opened if opened > target else target
                            position.peak_profit_pct = max(
                                position.peak_profit_pct,
                                (fill / position.avg_price - 1.0) * 100.0,
                            )
                            _record_normal_telemetry(position, params, iso_time(stamp))
                            details.update(take_profit_pct=params.take_profit_pct, target_price=target)
                            if stock_is_sellable_after_settlement(
                                position.settle_date, datetime.fromtimestamp(stamp, VN_TZ),
                            ):
                                sell_position(
                                    position, round_lot_down(position.quantity), fill, day,
                                    "TAKE_PROFIT", stamp=stamp, signal_time=iso_time(stamp),
                                    reason="TAKE_PROFIT", details=details,
                                    resolution=resolution, quality=quality,
                                )
                            else:
                                pending[symbol] = _Pending(
                                    "SELL", symbol, day, "TAKE_PROFIT", 1.0, position.trade_id,
                                    current_phase, settlement_waited=True, reason="TAKE_PROFIT",
                                    details=details, created_time=iso_time(stamp),
                                    source_resolution=resolution, data_quality=quality,
                                )
                            position = positions.get(symbol)
                        elif (
                            position
                            and "NORMAL" in position.em_modes
                            and _protect_event_ready(
                                position, params, alert=params.normal_policy == "ALERT",
                            )
                        ):
                            atr_pct = average_true_range_pct(history[symbol])
                            observation = _normal_policy_fill(
                                [bar], policy=params.normal_policy,
                                entry_price=position.avg_price,
                                peak_profit_pct=position.peak_profit_pct,
                                already_armed=position.normal_armed,
                                mfe_after_arm_pct=position.mfe_after_arm_pct,
                                arm_pct=params.normal_arm_pct,
                                giveback_pct=params.normal_giveback_pct,
                                dynamic_enabled=params.normal_dynamic_enabled,
                                sl_price=position.avg_price * (1.0 + position.sl_pct / 100.0),
                                atr_pct=atr_pct,
                                atr_multiplier=params.normal_atr_multiplier,
                                previous_trigger_price=position.normal_trigger_price,
                            )
                            was_armed = position.normal_armed
                            position.normal_armed = observation.armed
                            position.peak_profit_pct = max(
                                position.peak_profit_pct, observation.peak_profit_pct,
                            )
                            position.mfe_after_arm_pct = max(
                                position.mfe_after_arm_pct, observation.mfe_after_arm_pct,
                            )
                            position.normal_protected_profit_pct = observation.protected_profit_pct
                            position.normal_effective_trail_pct = observation.effective_trail_pct
                            position.normal_trigger_price = observation.trigger_price
                            if observation.armed and not was_armed and not position.normal_arm_time:
                                position.normal_arm_time = iso_time(
                                    observation.armed_at or stamp,
                                )
                            fill = observation.fill
                            if observation.triggered and params.normal_policy == "ALERT":
                                _record_protect_event(
                                    position, params, alert=True,
                                    peak_profit_pct=observation.peak_profit_pct,
                                )
                                signal_history.append({
                                    "date": day, "time": iso_time(stamp),
                                    "signal_time": iso_time(stamp), "decision_time": iso_time(stamp),
                                    "symbol": symbol, "action": "WAIT", "signal": "PROTECT",
                                    "event": "PROTECT_ALERT", "reason": "PROTECT_ALERT",
                                    "market_state": current_phase,
                                    "simulation_mode": settings.simulation_mode,
                                    "source_resolution": resolution, "data_quality": quality,
                                    "details": {
                                        "normal_policy": "ALERT",
                                        "normal_state": "ALERT",
                                        "normal_mfe_pct": observation.peak_profit_pct,
                                        "normal_peak_price": position.avg_price * (
                                            1.0 + observation.peak_profit_pct / 100.0
                                        ),
                                        "normal_effective_trail_pct": observation.effective_trail_pct,
                                        "normal_trigger_price": observation.trigger_price,
                                        "normal_protected_profit_pct": observation.protected_profit_pct,
                                        "normal_atr_pct": atr_pct,
                                        "normal_atr_multiplier": params.normal_atr_multiplier,
                                        "normal_dynamic_enabled": params.normal_dynamic_enabled,
                                        "sell_share_pct": params.normal_sell_pct,
                                        "hypothetical_quantity": sell_quantity_for_fraction(
                                            position.quantity, params.normal_sell_pct / 100.0,
                                        ),
                                    },
                                })
                            if fill > 0:
                                share = min(1.0, max(0.0, params.normal_sell_pct / 100.0))
                                quantity = sell_quantity_for_fraction(position.quantity, share)
                                details.update(
                                    triggered_events=["NORMAL_PROTECTION"],
                                    normal_policy=params.normal_policy,
                                    normal_arm_time=position.normal_arm_time,
                                    mfe_after_arm_pct=position.mfe_after_arm_pct,
                                    normal_protected_profit_pct=position.normal_protected_profit_pct,
                                    normal_effective_trail_pct=position.normal_effective_trail_pct,
                                    normal_trigger_price=position.normal_trigger_price,
                                    normal_atr_pct=atr_pct,
                                    normal_atr_multiplier=params.normal_atr_multiplier,
                                    normal_dynamic_enabled=params.normal_dynamic_enabled,
                                    normal_trigger_peak_pct=position.peak_profit_pct,
                                    normal_rearm_after_pct=protect_rearm_mfe(
                                        position.peak_profit_pct, params.normal_giveback_pct,
                                    ),
                                    peak_profit_pct=position.peak_profit_pct,
                                )
                                if quantity > 0 and stock_is_sellable_after_settlement(
                                    position.settle_date, datetime.fromtimestamp(stamp, VN_TZ),
                                ):
                                    sell_position(
                                        position, quantity, fill, day, "PRICE_PROTECTION",
                                        stamp=stamp, signal_time=iso_time(stamp), reason="NORMAL_PROTECTION",
                                        details=details, triggered=["NORMAL_PROTECTION"],
                                        resolution=resolution, quality=quality,
                                    )
                                elif quantity > 0:
                                    pending[symbol] = _Pending(
                                        "SELL", symbol, day, "PRICE_PROTECTION", share,
                                        position.trade_id, current_phase, ["NORMAL_PROTECTION"], True,
                                        "NORMAL_PROTECTION", details, iso_time(stamp), resolution, quality,
                                    )
                    # A waiting T+2 request does not freeze MFE/MAE.  REAL
                    # keeps observing the position until shares are sold, so
                    # replay must keep the same path while an order is pending.
                    position = positions.get(symbol)
                    if position and position.avg_price > 0:
                        _record_profit_path(
                            position, iso_time(stamp),
                            open_price=float(bar.get("open", bar.get("close", 0.0)) or 0.0),
                            high_price=float(bar.get("high", bar.get("close", 0.0)) or 0.0),
                            low_price=float(bar.get("low", bar.get("close", 0.0)) or 0.0),
                            close_price=float(bar.get("close", 0.0) or 0.0),
                            source_resolution=resolution,
                        )
                        _record_normal_telemetry(
                            position, params, iso_time(stamp),
                        )

                    # A real fill already consumed this bar. Do not use the
                    # same close to immediately re-enter or trigger another exit.
                    if len(events) != event_count_before_exit:
                        continue

                    nav, stock_value = portfolio_value()
                    position = positions.get(symbol)
                    now = datetime.fromtimestamp(stamp, VN_TZ)
                    until = loss_locked_until.get(symbol)
                    locked = bool(until and now < until)
                    if until and not locked:
                        loss_streaks[symbol] = 0
                        loss_locked_until[symbol] = None
                    open_positions = len(positions) + sum(1 for item in pending.values() if item.side == "BUY")
                    exposure = params.exposure.get(current_phase, 0.0)
                    budget = order_budget(
                        nav=nav, exposure=exposure, max_positions=params.max_positions,
                        current_stock_value=stock_value, pending_buy_value=0.0, available_cash=cash,
                    )
                    if params.no_compound_enabled and symbol in capital_ledgers:
                        budget = min(budget, capital_ledgers[symbol]["available"])
                    rule_context = {
                        "symbol": symbol,
                        "exchange": symbol_exchanges[symbol],
                        "bars": [*history[symbol], partial],
                        "vnindex_bars": [],
                        "signal_mode": "REALTIME",
                        "previous_market_state": current_phase,
                        "confirmed_market_state": current_phase,
                        "precomputed_market": {"candidate": current_phase, "state": current_phase, "details": {}},
                        "previous_indicators": previous_indicators,
                    }
                    portfolio_context = {
                        "nav": nav, "available_cash": cash,
                        "available_capital": max(0.0, budget), "order_budget": max(0.0, budget),
                        "open_positions": open_positions,
                        "pending_buy": symbol in pending and pending[symbol].side == "BUY",
                        "loss_streak": params.loss_lock_count if locked else loss_streaks[symbol],
                        "position_quantity": position.quantity if position else 0,
                        "position": ({
                            "quantity": position.quantity, "avg_price": position.avg_price,
                            "current_price": float(partial["close"]),
                            "peak_profit_pct": position.peak_profit_pct,
                            "is_reentry": position.is_reentry, "em_modes": position.em_modes,
                            "normal_protection_done": position.normal_done,
                            "normal_execution_managed": True,
                            "managed_by_app": True, "managed_by_bot": True,
                        } if position else {}),
                    }
                    decision = rule.evaluate(rule_context, portfolio_context)
                    next_filters = {}
                    if params.buy_window_enabled or params.buy_confirmation_enabled:
                        next_filters, decision = apply_buy_filters(
                            rule, decision, rule_context, portfolio_context,
                            buy_confirmations.get(symbol), observed_at=now,
                            exchange=symbol_exchanges[symbol], working_dates=settlement_calendar,
                        )
                    if next_filters:
                        buy_confirmations[symbol] = next_filters
                    else:
                        buy_confirmations.pop(symbol, None)
                    last_decisions[symbol] = decision.to_dict()
                    details = dict(decision.details or {}) if isinstance(decision.details, dict) else {}
                    signal_value = str(getattr(decision, "signal", "") or "")
                    confirmation_values = details.get("buy_confirmation") or {}
                    window_values = details.get("buy_window") or {}
                    signature = (
                        decision.action, decision.event, decision.reason,
                        str(int(float(confirmation_values.get("minutes_held", 0) or 0))),
                        str(bool(confirmation_values.get("ema_ok", False))),
                        str(bool(confirmation_values.get("rsi_ok", False))),
                        str(window_values.get("state", "")),
                    )
                    dedupe_key = (symbol, day)
                    if (
                        (settings.export_signals or decision.action != "WAIT" or signal_value in {"BUY", "SELL"})
                        and signal_dedupe.get(dedupe_key) != signature
                    ):
                        signal_history.append({
                            "date": day, "time": iso_time(stamp),
                            "signal_time": str(window_values.get("signal_time") or confirmation_values.get("signal_time") or iso_time(stamp)),
                            "decision_time": iso_time(stamp), "fill_time": "",
                            "symbol": symbol, "action": decision.action,
                            "signal": signal_value, "event": decision.event, "reason": decision.reason,
                            "market_state": current_phase,
                            "buy_ema": f"{params.buy_ema_fast}/{params.buy_ema_slow}",
                            "sell_ema": f"{params.sell_ema_fast}/{params.sell_ema_slow}",
                            "rsi_period": params.rsi_period, "details": details,
                            "simulation_mode": settings.simulation_mode,
                            "source_resolution": resolution, "data_quality": quality,
                        })
                        signal_dedupe[dedupe_key] = signature
                    existing = pending.get(symbol)
                    if (
                        existing and existing.side == "SELL" and existing.settlement_waited
                        and settings.sell_wait_policy == "RECHECK"
                        and position is not None
                        and stock_is_sellable_after_settlement(
                            position.settle_date, datetime.fromtimestamp(stamp, VN_TZ),
                        )
                    ):
                        matches = (
                            _pending_protect_matches(
                                existing, position, params, float(partial["close"]),
                                atr_pct=average_true_range_pct(history[symbol]),
                            )
                            if _is_pending_protect(existing)
                            else _pending_matches_decision(existing, decision)
                        )
                        if matches is False:
                            pending.pop(symbol, None)
                            existing = None
                    if existing and existing.side == "SELL" and decision.action == "SELL":
                        triggered = details.get("triggered_events", [])
                        if _exit_priority(
                            decision.event or decision.reason, triggered,
                        ) > _exit_priority(existing.event, existing.triggered_events):
                            pending.pop(symbol, None)
                            existing = None
                    if existing:
                        continue
                    if decision.action == "BUY" and position is None and not locked:
                        pending[symbol] = _Pending(
                            "BUY", symbol, day, decision.event or decision.reason, 1.0,
                            market_state=current_phase, reason=decision.reason, details=details,
                            created_time=iso_time(stamp), source_resolution=resolution,
                            data_quality=quality,
                        )
                    elif decision.action == "SELL" and position is not None:
                        triggered = details.get("triggered_events", [])
                        pending[symbol] = _Pending(
                            "SELL", symbol, day, decision.event or decision.reason,
                            float(decision.quantity_fraction or 1.0), position.trade_id,
                            current_phase, list(triggered or []), not stock_is_sellable_after_settlement(
                                position.settle_date, datetime.fromtimestamp(stamp, VN_TZ),
                            ),
                            decision.reason, details, iso_time(stamp), resolution, quality,
                        )
                processed_source_bars += len(current)
                if progress and total_source_bars:
                    ratio = min(1.0, processed_source_bars / total_source_bars)
                    bucket = int(ratio * 100.0)
                    if bucket != last_progress_bucket:
                        progress(
                            .15 + .80 * ratio,
                            f"REPLAY {datetime.fromtimestamp(stamp, VN_TZ):%d/%m/%Y %H:%M}",
                        )
                        last_progress_bucket = bucket

            for symbol in managed_symbols:
                partial = partials.get(symbol)
                if not partial:
                    continue
                final = dict(partial)
                final["closed"] = True
                history[symbol].append(final)
            nav, stock_value = portfolio_value()
            equity_curve.append({
                "date": day, "equity": nav, "cash": cash, "market_value": stock_value,
                "simulation_mode": settings.simulation_mode,
            })

        last_day = calendar[-1]
        final_equity, market_value = portfolio_value()
        if carry is not None:
            carry.update(
                cash=cash, positions=positions, cycle_numbers=cycle_numbers,
                pending=pending, loss_streaks=loss_streaks,
                loss_locked_until=loss_locked_until, capital_ledgers=capital_ledgers,
                last_decisions=last_decisions, buy_confirmations=buy_confirmations,
                last_processed_date=last_day,
            )
        open_trades = [
            BacktestTrade(
                position.trade_id, position.symbol, position.opened_date,
                entry_quantity=position.entry_quantity, remaining_quantity=position.quantity,
                avg_entry_price=position.avg_price, fees=position.fees, tax=position.tax,
                net_pnl=position.net_pnl + (marks.get(position.symbol, position.avg_price) - position.avg_price)
                * position.quantity * 1000.0,
                outcome="OPEN", exit_events=list(position.exit_events),
                entry_market_state=position.entry_market_state,
                entry_exposure_pct=position.entry_exposure_pct,
                entry_reason=position.entry_reason, entry_signal_date=position.entry_signal_date,
                entry_rule=position.entry_rule, cycle_id=position.cycle_id,
                sessions_held=max(0, len(calendar) - 1 - calendar_index.get(position.opened_date, 0)),
                entry_value=position.entry_value, sl_pct=position.sl_pct,
                peak_profit_pct=position.peak_profit_pct,
                pnl_pct=(
                    position.net_pnl
                    + (marks.get(position.symbol, position.avg_price) - position.avg_price)
                    * position.quantity * 1000.0
                ) / position.entry_value * 100.0 if position.entry_value else 0.0,
                entry_ema_fast=position.entry_ema_fast,
                entry_ema_slow=position.entry_ema_slow, entry_rsi=position.entry_rsi,
                exit_ema_fast=position.exit_ema_fast,
                exit_ema_slow=position.exit_ema_slow, exit_rsi=position.exit_rsi,
                exit_fills=list(position.exit_fills),
                avg_exit_price=(position.exit_value / (position.sold_quantity * 1000.0)
                                if position.sold_quantity else 0.0),
                **_profit_path_trade_metrics(
                    position,
                    last_day,
                    (
                        position.net_pnl
                        + (marks.get(position.symbol, position.avg_price) - position.avg_price)
                        * position.quantity * 1000.0
                    ) / position.entry_value * 100.0 if position.entry_value else 0.0,
                ),
                **_normal_trade_metrics(
                    position,
                    params,
                    (
                        position.net_pnl
                        + (marks.get(position.symbol, position.avg_price) - position.avg_price)
                        * position.quantity * 1000.0
                    ) / position.entry_value * 100.0 if position.entry_value else 0.0,
                ),
            ) for position in positions.values()
        ]
        peak = max_drawdown = 0.0
        for point in equity_curve:
            equity = float(point["equity"])
            peak = max(peak, equity)
            drawdown = ((equity / peak) - 1.0) * 100.0 if peak else 0.0
            point["drawdown_pct"] = drawdown
            max_drawdown = min(max_drawdown, drawdown)
        wins = sum(1 for trade in completed if trade.outcome == "WIN")
        losses = sum(1 for trade in completed if trade.outcome == "LOSS")
        net_pnl = final_equity - settings.initial_capital
        coverage = {
            symbol: {
                day: {
                    "exchange": symbol_exchanges[symbol],
                    "source_resolution": source_resolution[symbol].get(day, ""),
                    "data_quality": source_quality[symbol].get(day, "MISSING"),
                } for day in calendar if day in rows_by_symbol.get(symbol, {})
            } for symbol in managed_symbols
        }
        warnings = [
            "REPLAY không có bid/ask, order book, trượt giá hoặc thanh khoản khớp lệnh.",
            "Không biết thứ tự High/Low trong cùng một nến; khi SL và TP cùng chạm, SL được ưu tiên.",
        ]
        if fallback_days:
            warnings.append(
                f"AUTO HYBRID đã dùng FALLBACK 1D cho {len(fallback_days)} ngày/mã; "
                "các ngày này không có tín hiệu intraday."
            )
        result = BacktestResult(
            run_id=f"BT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}",
            config=settings, started_at=started,
            completed_at=datetime.now().astimezone().isoformat(),
            initial_capital=settings.initial_capital, final_equity=final_equity,
            cash=cash, market_value=market_value, net_pnl=net_pnl,
            return_pct=(net_pnl / settings.initial_capital * 100.0),
            total_fees=total_fees, total_tax=total_tax,
            max_drawdown_pct=abs(max_drawdown), buy_count=buy_count, sell_count=sell_count,
            closed_trades=len(completed), win_count=wins, loss_count=losses,
            win_rate_pct=(wins / len(completed) * 100.0 if completed else 0.0),
            events=events, trades=[*completed, *open_trades], equity_curve=equity_curve,
            phase_history=phase_history, signals=signal_history,
            data_quality={
                "simulation_mode": settings.simulation_mode,
                "signal_resolution": "1D_REALTIME",
                "source_coverage": coverage,
                "indicator_warmup_source": warmup_sources,
                "symbol_exchanges": symbol_exchanges,
                "fallback_count": len(fallback_days),
                "order_timing": "signal after source close; fill at next eligible source open",
            },
            warnings=warnings,
        )
        if save:
            self.data.save_run(result)
        return result
