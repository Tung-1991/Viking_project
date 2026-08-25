from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..models import StrategyDecision


def closes(rows: Iterable[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows or []:
        try:
            value = float(row.get("close", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        if value > 0:
            values.append(value)
    return values

def ema(values: Iterable[float], period: int) -> list[float]:
    source = [float(value) for value in values]
    if not source:
        return []
    period = max(1, int(period or 1))
    alpha = 2.0 / (period + 1.0)
    output = [source[0]]
    for value in source[1:]:
        output.append((value * alpha) + (output[-1] * (1.0 - alpha)))
    return output

def sma(values: Iterable[float], period: int) -> list[float | None]:
    source = [float(value) for value in values]
    period = max(1, int(period or 1))
    output: list[float | None] = []
    running = 0.0
    for index, value in enumerate(source):
        running += value
        if index >= period:
            running -= source[index - period]
        output.append(running / period if index + 1 >= period else None)
    return output

def rsi(values: Iterable[float], period: int = 14) -> list[float | None]:
    source = [float(value) for value in values]
    if not source:
        return []
    period = max(1, int(period or 14))
    output: list[float | None] = [None] * len(source)
    if len(source) <= period:
        return output
    gains = [max(0.0, source[i] - source[i - 1]) for i in range(1, len(source))]
    losses = [max(0.0, source[i - 1] - source[i]) for i in range(1, len(source))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + gain / loss))

    output[period] = value(avg_gain, avg_loss)
    for index in range(period + 1, len(source)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index - 1]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index - 1]) / period
        output[index] = value(avg_gain, avg_loss)
    return output

def crossover_signal(
    rows: Iterable[dict[str, Any]],
    fast: int = 3,
    slow: int = 6,
    rsi_period: int = 14,
    *,
    sell_fast: int | None = None,
    sell_slow: int | None = None,
    prefer: str = "BUY",
) -> str:
    rows = list(rows or [])
    values = closes(rows)
    buy_fast = max(1, int(fast or 3))
    buy_slow = max(1, int(slow or 6))
    exit_fast = max(1, int(sell_fast if sell_fast is not None else buy_fast))
    exit_slow = max(1, int(sell_slow if sell_slow is not None else buy_slow))
    if len(values) < max(buy_slow + 1, exit_slow + 1, rsi_period + 2):
        return ""
    current = indicator_snapshot(
        rows, buy_fast, buy_slow, rsi_period,
        sell_fast=exit_fast, sell_slow=exit_slow,
    )
    previous = indicator_snapshot(
        rows[:-1], buy_fast, buy_slow, rsi_period,
        sell_fast=exit_fast, sell_slow=exit_slow,
    )
    return crossover_signal_from_snapshots(current, previous, prefer=prefer)


def crossover_signal_from_snapshots(
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    *,
    prefer: str = "BUY",
) -> str:
    """Evaluate one EMA transition between two consecutive observations.

    ``current`` may be an unfinished daily candle rebuilt from a live tick or
    an intraday source bar.  Its EMA values must be compared with the preceding
    observation of that same candle, not repeatedly with yesterday's close.
    RSI keeps the documented daily comparison through ``rsi_previous``.
    """
    current = current if isinstance(current, dict) else {}
    previous = previous if isinstance(previous, dict) else {}

    def number(source: dict[str, Any], key: str) -> float | None:
        try:
            value = source.get(key)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    current_rsi = number(current, "rsi")
    previous_daily_rsi = number(current, "rsi_previous")
    current_buy_fast = number(current, "buy_ema_fast")
    current_buy_slow = number(current, "buy_ema_slow")
    previous_buy_fast = number(previous, "buy_ema_fast")
    previous_buy_slow = number(previous, "buy_ema_slow")
    current_sell_fast = number(current, "sell_ema_fast")
    current_sell_slow = number(current, "sell_ema_slow")
    previous_sell_fast = number(previous, "sell_ema_fast")
    previous_sell_slow = number(previous, "sell_ema_slow")
    required = (
        current_rsi, previous_daily_rsi,
        current_buy_fast, current_buy_slow, previous_buy_fast, previous_buy_slow,
        current_sell_fast, current_sell_slow, previous_sell_fast, previous_sell_slow,
    )
    if any(value is None for value in required):
        return ""

    crossed_up = previous_buy_fast <= previous_buy_slow and current_buy_fast > current_buy_slow
    crossed_down = previous_sell_fast >= previous_sell_slow and current_sell_fast < current_sell_slow
    buy_signal = crossed_up and current_rsi > previous_daily_rsi
    sell_signal = crossed_down and current_rsi < previous_daily_rsi
    if str(prefer or "BUY").upper() == "SELL":
        if sell_signal:
            return "SELL"
        if buy_signal:
            return "BUY"
    else:
        if buy_signal:
            return "BUY"
        if sell_signal:
            return "SELL"
    return ""

def indicator_snapshot(
    rows: Iterable[dict[str, Any]],
    fast: int = 3,
    slow: int = 6,
    rsi_period: int = 14,
    *,
    sell_fast: int | None = None,
    sell_slow: int | None = None,
) -> dict[str, Any]:
    """Return the exact latest values used by the static M/B rule."""
    values = closes(rows)
    fast = max(1, int(fast or 3))
    slow = max(1, int(slow or 6))
    rsi_period = max(1, int(rsi_period or 14))
    sell_fast = max(1, int(sell_fast if sell_fast is not None else fast))
    sell_slow = max(1, int(sell_slow if sell_slow is not None else slow))
    fast_values = ema(values, fast) if values else []
    slow_values = ema(values, slow) if values else []
    sell_fast_values = ema(values, sell_fast) if values else []
    sell_slow_values = ema(values, sell_slow) if values else []
    rsi_values = rsi(values, rsi_period) if values else []
    current_rsi = rsi_values[-1] if rsi_values else None
    previous_rsi = rsi_values[-2] if len(rsi_values) >= 2 else None
    return {
        "ema_fast_period": fast,
        "ema_slow_period": slow,
        "rsi_period": rsi_period,
        "ema_fast": fast_values[-1] if fast_values else None,
        "ema_slow": slow_values[-1] if slow_values else None,
        "buy_ema_fast_period": fast,
        "buy_ema_slow_period": slow,
        "buy_ema_fast": fast_values[-1] if fast_values else None,
        "buy_ema_slow": slow_values[-1] if slow_values else None,
        "sell_ema_fast_period": sell_fast,
        "sell_ema_slow_period": sell_slow,
        "sell_ema_fast": sell_fast_values[-1] if sell_fast_values else None,
        "sell_ema_slow": sell_slow_values[-1] if sell_slow_values else None,
        "rsi": current_rsi,
        "rsi_previous": previous_rsi,
    }

def crossover_count(rows: Iterable[dict[str, Any]], fast: int = 3, slow: int = 6, window: int = 10) -> int:
    values = closes(rows)
    if len(values) < 2:
        return 0
    fast_values = ema(values, fast)
    slow_values = ema(values, slow)
    start = max(1, len(values) - max(1, int(window or 10)))
    return sum(
        1
        for index in range(start, len(values))
        if (fast_values[index - 1] <= slow_values[index - 1] and fast_values[index] > slow_values[index])
        or (fast_values[index - 1] >= slow_values[index - 1] and fast_values[index] < slow_values[index])
    )

def pivot_points(rows: list[dict[str, Any]], left: int = 3, right: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    left = max(1, int(left or 3))
    right = max(1, int(right or 3))
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for index in range(left, len(rows) - right):
        try:
            high = float(rows[index].get("high", 0.0) or 0.0)
            low = float(rows[index].get("low", 0.0) or 0.0)
            left_rows = rows[index - left:index]
            right_rows = rows[index + 1:index + right + 1]
            neighbor_highs = [float(row.get("high", 0.0) or 0.0) for row in left_rows + right_rows]
            neighbor_lows = [float(row.get("low", 0.0) or 0.0) for row in left_rows + right_rows]
        except (AttributeError, TypeError, ValueError):
            continue
        if high > 0 and all(high > value for value in neighbor_highs):
            highs.append((index, high))
        if low > 0 and all(low < value for value in neighbor_lows if value > 0):
            lows.append((index, low))
    return highs, lows

EXPOSURE_DEFAULTS = {
    "ACCUMULATION": 0.60,
    "DISTRIBUTION": 0.50,
    "UPTREND": 0.90,
    "DOWNTREND": 0.10,
}

@dataclass
class StaticRuleParameters:
    ma_period: int = 200
    pivot_left: int = 3
    pivot_right: int = 3
    pivot_horizontal_pct: float = 1.0
    ma_zone_pct: float = 1.0
    confirm_sessions: int = 3
    volume_confirmation: bool = False
    volume_average_sessions: int = 20
    high_volume_ratio: float = 1.5
    low_volume_ratio: float = 0.8
    buy_ema_fast: int = 3
    buy_ema_slow: int = 6
    sell_ema_fast: int = 3
    sell_ema_slow: int = 6
    rsi_period: int = 14
    max_positions: int = 5
    initial_sl_pct: float = -3.0
    reentry_sl_pct: float = -2.1
    loss_lock_count: int = 3
    loss_lock_hours: int = 24
    no_compound_enabled: bool = True
    force_min_lot_enabled: bool = True
    take_profit_pct: float = 7.0
    normal_arm_pct: float = 7.0
    normal_sell_pct: float = 33.0
    normal_giveback_pct: float = 3.0
    high_profit_arm_pct: float = 20.0
    high_profit_close_drawdown_pct: float = 5.0
    high_sell_pct: float = 33.0
    whipsaw_enabled: bool = True
    whipsaw_n: int = 3
    whipsaw_x: int = 7
    exposure: dict[str, float] = field(default_factory=lambda: dict(EXPOSURE_DEFAULTS))

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "StaticRuleParameters":
        raw = raw if isinstance(raw, dict) else {}
        # Older account settings used one EMA pair for both directions.  Map
        # that pair to BUY and SELL so upgrading never changes live behavior.
        legacy_fast = raw.get("ema_fast", 3)
        legacy_slow = raw.get("ema_slow", 6)
        raw = {
            **raw,
            "buy_ema_fast": raw.get("buy_ema_fast", legacy_fast),
            "buy_ema_slow": raw.get("buy_ema_slow", legacy_slow),
            "sell_ema_fast": raw.get("sell_ema_fast", legacy_fast),
            "sell_ema_slow": raw.get("sell_ema_slow", legacy_slow),
        }
        allowed = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in allowed}
        if isinstance(values.get("exposure"), dict):
            values["exposure"] = {
                **EXPOSURE_DEFAULTS,
                **{str(key).upper(): float(value) for key, value in values["exposure"].items()},
            }
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def ema_fast(self) -> int:
        """Compatibility alias for callers that still mean the BUY pair."""
        return self.buy_ema_fast

    @ema_fast.setter
    def ema_fast(self, value: int) -> None:
        self.buy_ema_fast = int(value)

    @property
    def ema_slow(self) -> int:
        """Compatibility alias for callers that still mean the BUY pair."""
        return self.buy_ema_slow

    @ema_slow.setter
    def ema_slow(self, value: int) -> None:
        self.buy_ema_slow = int(value)

def _working_bars(rows: list[dict[str, Any]], signal_mode: str) -> list[dict[str, Any]]:
    values = [dict(row) for row in rows or [] if isinstance(row, dict)]
    # Anything other than an explicit REALTIME drops the unfinished candle: a
    # caller that forgot the mode must not trade on a bar that can still change.
    if str(signal_mode or "CLOSED").upper() != "REALTIME" and values and not bool(values[-1].get("closed", True)):
        values.pop()
    return values

def classify_market_state(
    rows: list[dict[str, Any]],
    *,
    previous_state: str = "UNKNOWN",
    params: StaticRuleParameters | None = None,
) -> tuple[str, dict[str, Any]]:
    params = params or StaticRuleParameters()
    previous = str(previous_state or "UNKNOWN").upper()
    values = closes(rows)
    if len(values) < params.ma_period:
        return (previous if previous in EXPOSURE_DEFAULTS else "TRANSITION"), {"reason": "INSUFFICIENT_MARKET_BARS"}
    ma_values = sma(values, params.ma_period)
    ma = float(ma_values[-1] or 0.0)
    current = values[-1]
    highs, lows = pivot_points(rows, params.pivot_left, params.pivot_right)
    details = {"ma200": ma, "price": current, "pivot_highs": highs[-3:], "pivot_lows": lows[-3:]}
    volumes = [float(row.get("volume", 0.0) or 0.0) for row in rows if isinstance(row, dict)]
    lookback = max(1, params.volume_average_sessions)
    history = volumes[-lookback - 1:-1]
    average_volume = sum(history) / len(history) if history else 0.0
    volume_ratio = volumes[-1] / average_volume if volumes and average_volume > 0 else 0.0
    details.update(
        volume_confirmation_enabled=params.volume_confirmation,
        volume_average=average_volume,
        volume_ratio=volume_ratio,
        volume_confidence=(
            "HIGH" if volume_ratio >= params.high_volume_ratio
            else "LOW" if 0 < volume_ratio < params.low_volume_ratio
            else "NORMAL"
        ),
    )
    if len(highs) < 2 or len(lows) < 2:
        return (previous if previous in EXPOSURE_DEFAULTS else "TRANSITION"), details
    previous_high, current_high = highs[-2][1], highs[-1][1]
    previous_low, current_low = lows[-2][1], lows[-1][1]
    above_context = current >= ma * (1.0 - params.ma_zone_pct / 100.0)
    below_context = current <= ma * (1.0 + params.ma_zone_pct / 100.0)
    if current_high > previous_high and current_low > previous_low and above_context:
        return "UPTREND", details
    if current_high < previous_high and current_low < previous_low and below_context:
        return "DOWNTREND", details
    horizontal = params.pivot_horizontal_pct / 100.0
    high_flat = abs(current_high - previous_high) / previous_high <= horizontal if previous_high else False
    low_flat = abs(current_low - previous_low) / previous_low <= horizontal if previous_low else False
    if previous == "DOWNTREND" and current_low >= previous_low * (1.0 - horizontal) and (high_flat or low_flat):
        return "ACCUMULATION", details
    if previous == "UPTREND" and current_high <= previous_high * (1.0 + horizontal) and (high_flat or low_flat):
        return "DISTRIBUTION", details
    return (previous if previous in EXPOSURE_DEFAULTS else "TRANSITION"), details

class StaticRule:
    def __init__(self, params: StaticRuleParameters | None = None):
        self.params = params or StaticRuleParameters()

    def evaluate(self, market_context: dict[str, Any], portfolio_state: dict[str, Any]) -> StrategyDecision:
        context = market_context or {}
        portfolio = portfolio_state or {}
        symbol = str(context.get("symbol", "") or "").upper()
        signal_mode = str(context.get("signal_mode", "CLOSED") or "CLOSED")
        bars = _working_bars(list(context.get("bars") or []), signal_mode)
        vnindex = _working_bars(list(context.get("vnindex_bars") or []), signal_mode)
        previous_state = str(context.get("previous_market_state", "UNKNOWN") or "UNKNOWN")
        precomputed_market = context.get("precomputed_market")
        if isinstance(precomputed_market, dict):
            raw_market_state = str(
                precomputed_market.get("candidate", precomputed_market.get("state", previous_state))
                or previous_state
            ).upper()
            market_details = dict(precomputed_market.get("details") or {})
        else:
            raw_market_state, market_details = classify_market_state(
                vnindex, previous_state=previous_state, params=self.params
            )
        market_state = str(context.get("confirmed_market_state", raw_market_state) or raw_market_state).upper()
        market_details["candidate_state"] = raw_market_state
        confirmation = context.get("market_confirmation")
        if isinstance(confirmation, dict):
            market_details["confirmed_state"] = str(
                confirmation.get("confirmed", market_state) or market_state
            ).upper()
            market_details["display_state"] = str(
                confirmation.get("display", raw_market_state) or raw_market_state
            ).upper()
            market_details["confirmation_count"] = max(
                0, int(confirmation.get("count", 0) or 0)
            )
            market_details["confirmation_required"] = max(
                1, int(confirmation.get("required", self.params.confirm_sessions) or self.params.confirm_sessions)
            )
            market_details["confirmation_pending"] = bool(confirmation.get("pending", False))
        position = portfolio.get("position") if isinstance(portfolio.get("position"), dict) else {}
        quantity = max(0, int(position.get("quantity", portfolio.get("position_quantity", 0)) or 0))
        indicators = indicator_snapshot(
            bars,
            self.params.buy_ema_fast,
            self.params.buy_ema_slow,
            self.params.rsi_period,
            sell_fast=self.params.sell_ema_fast,
            sell_slow=self.params.sell_ema_slow,
        )
        previous_indicators = context.get("previous_indicators")
        if signal_mode.upper() == "REALTIME" and isinstance(previous_indicators, dict):
            signal = crossover_signal_from_snapshots(
                indicators,
                previous_indicators,
                prefer="SELL" if quantity > 0 else "BUY",
            )
        else:
            signal = crossover_signal(
                bars,
                self.params.buy_ema_fast,
                self.params.buy_ema_slow,
                self.params.rsi_period,
                sell_fast=self.params.sell_ema_fast,
                sell_slow=self.params.sell_ema_slow,
                prefer="SELL" if quantity > 0 else "BUY",
            )
        # Whipsaw is an entry guard, so it follows the BUY EMA pair only.
        crosses = crossover_count(
            bars,
            self.params.buy_ema_fast,
            self.params.buy_ema_slow,
            self.params.whipsaw_x,
        )
        details = {
            "market": market_details,
            "exposure": self.params.exposure.get(market_state, 0.0),
            "indicators": indicators,
            "entry_checks": {
                "nav": float(portfolio.get("nav", 0.0) or 0.0),
                "available_cash": float(portfolio.get("available_cash", 0.0) or 0.0),
                "open_positions": int(portfolio.get("open_positions", 0) or 0),
                "max_positions": self.params.max_positions,
                "available_capital": float(portfolio.get("available_capital", 0.0) or 0.0),
                "order_budget": float(portfolio.get("order_budget", 0.0) or 0.0),
                "force_min_lot_enabled": self.params.force_min_lot_enabled,
                "loss_streak": int(portfolio.get("loss_streak", 0) or 0),
                "loss_lock_count": self.params.loss_lock_count,
                "loss_lock_hours": self.params.loss_lock_hours,
                "whipsaw_enabled": self.params.whipsaw_enabled,
                "whipsaw_crossovers": crosses,
                "whipsaw_limit": self.params.whipsaw_n,
                "whipsaw_window": self.params.whipsaw_x,
                "corporate_action_blocked": bool(portfolio.get("corporate_action_blocked", False)),
                "pending_buy": bool(portfolio.get("pending_buy", False)),
            },
        }
        corporate_action = portfolio.get("corporate_action")
        if isinstance(corporate_action, dict) and corporate_action:
            details["corporate_action"] = dict(corporate_action)
            details["corporate_action_warning"] = bool(portfolio.get("corporate_action_warning", False))

        if quantity > 0:
            if position.get("managed_by_app") is False and position.get("managed_by_bot") is False:
                return StrategyDecision(
                    "WAIT", symbol, "MANUAL_OR_EXTERNAL_POSITION", signal=signal,
                    market_state=market_state, details=details, scope="POSITION_MANAGEMENT",
                )
            return self._evaluate_position(symbol, market_state, signal, bars, position, details)

        if signal != "BUY":
            return StrategyDecision("WAIT", symbol, "NO_NEW_BUY_SIGNAL", signal=signal, market_state=market_state, details=details)
        if bool(portfolio.get("corporate_action_blocked")):
            return StrategyDecision("WAIT", symbol, "CORPORATE_ACTION_BLOCK", signal=signal, market_state=market_state, details=details)
        if market_state not in EXPOSURE_DEFAULTS:
            return StrategyDecision("WAIT", symbol, "MARKET_STATE_UNKNOWN", signal=signal, market_state=market_state, details=details)
        if bool(portfolio.get("pending_buy")):
            return StrategyDecision("WAIT", symbol, "BUY_ALREADY_PENDING", signal=signal, market_state=market_state, details=details)
        if int(portfolio.get("loss_streak", 0) or 0) >= self.params.loss_lock_count:
            return StrategyDecision("WAIT", symbol, "LOCKED_AFTER_3_LOSSES", signal=signal, market_state=market_state, details=details)
        details["whipsaw_crossovers"] = crosses
        if self.params.whipsaw_enabled and crosses >= self.params.whipsaw_n:
            return StrategyDecision("WAIT", symbol, "WHIPSAW_LOCK", signal=signal, market_state=market_state, details=details)
        if int(portfolio.get("open_positions", 0) or 0) >= self.params.max_positions:
            return StrategyDecision("WAIT", symbol, "MAX_POSITIONS", signal=signal, market_state=market_state, details=details)
        if float(portfolio.get("available_capital", 0.0) or 0.0) <= 0:
            return StrategyDecision("WAIT", symbol, "NO_AVAILABLE_CAPITAL", signal=signal, market_state=market_state, details=details)
        return StrategyDecision(
            "BUY",
            symbol,
            "BUY_SIGNAL",
            event="ENTRY_BUY",
            signal="BUY",
            market_state=market_state,
            quantity_fraction=1.0,
            details=details,
        )

    def _evaluate_position(
        self,
        symbol: str,
        market_state: str,
        signal: str,
        bars: list[dict[str, Any]],
        position: dict[str, Any],
        details: dict[str, Any],
    ) -> StrategyDecision:
        entry = float(position.get("avg_price", 0.0) or 0.0)
        current = float(position.get("current_price", closes(bars)[-1] if closes(bars) else 0.0) or 0.0)
        current_profit = ((current / entry) - 1.0) * 100.0 if entry > 0 and current > 0 else 0.0
        peak_profit = max(current_profit, float(position.get("peak_profit_pct", current_profit) or current_profit))
        details.update({"current_profit_pct": current_profit, "peak_profit_pct": peak_profit})
        sl_mode = str(position.get("sl_mode", "DEFAULT") or "DEFAULT").upper()
        sl_value = float(position.get("sl_value", 0.0) or 0.0)
        if sl_mode == "PRICE" and sl_value > 0:
            stop_hit = current > 0 and current <= sl_value
            details.update(sl_mode="PRICE", sl_value=sl_value)
        else:
            sl_pct = (
                -abs(sl_value)
                if sl_mode == "PERCENT" and sl_value
                else self.params.reentry_sl_pct if position.get("is_reentry") else self.params.initial_sl_pct
            )
            stop_hit = current_profit <= float(sl_pct)
            details.update(sl_mode="PERCENT", sl_value=float(sl_pct))
        if stop_hit:
            return StrategyDecision(
                "SELL", symbol, "STOP_LOSS", event="STOP_LOSS", signal=signal,
                market_state=market_state, quantity_fraction=1.0, details=details,
                scope="POSITION_MANAGEMENT",
            )

        em_modes = {
            str(value or "").strip().upper()
            for value in (position.get("em_modes") or [])
            if str(value or "").strip()
        }
        details["em_modes"] = sorted(em_modes)

        # Take profit is the mirror of stop loss above: a target typed onto the
        # position wins outright, and with nothing typed the TP tactic falls back
        # to the global percentage.  Like SL it closes the whole position, so it
        # needs no other exit layer switched on.
        tp_mode = str(position.get("tp_mode", "NONE") or "NONE").upper()
        tp_value = float(position.get("tp_value", 0.0) or 0.0)
        if tp_mode == "PRICE" and tp_value > 0:
            target_hit = current > 0 and current >= tp_value
            details.update(tp_mode="PRICE", tp_value=tp_value)
        elif tp_mode == "PERCENT" and tp_value > 0:
            target_hit = current_profit >= abs(tp_value)
            details.update(tp_mode="PERCENT", tp_value=abs(tp_value))
        elif "TP" in em_modes and self.params.take_profit_pct > 0:
            target_hit = current_profit >= self.params.take_profit_pct
            details.update(tp_mode="PERCENT", tp_value=self.params.take_profit_pct)
        else:
            target_hit = False
        if target_hit:
            return StrategyDecision(
                "SELL", symbol, "TAKE_PROFIT", event="TAKE_PROFIT", signal=signal,
                market_state=market_state, quantity_fraction=1.0, details=details,
                scope="POSITION_MANAGEMENT",
            )
        if "IND_EXIT" in em_modes and signal == "SELL":
            return StrategyDecision(
                "SELL", symbol, "SELL_SIGNAL", event="INDICATOR_EXIT", signal="SELL",
                market_state=market_state, quantity_fraction=1.0, details=details,
                scope="POSITION_MANAGEMENT",
            )

        triggered: list[str] = []
        if "NORMAL" in em_modes and not bool(position.get("normal_protection_done")):
            # Giveback is measured on price, not on profit points, so the
            # room to breathe stays the same 3% whether the trade is up
            # 8% or up 90%.
            peak_price = entry * (1.0 + peak_profit / 100.0)
            trigger = peak_price * (1.0 - self.params.normal_giveback_pct / 100.0)
            if peak_profit >= self.params.normal_arm_pct and current <= trigger:
                triggered.append("NORMAL_PROTECTION")
        highest_close = float(position.get("highest_close", 0.0) or 0.0)
        latest_close = closes(bars)[-1] if closes(bars) else 0.0
        high_profit_armed = peak_profit >= self.params.high_profit_arm_pct
        if "HIGH" in em_modes and not bool(position.get("high_profit_protection_done")):
            if high_profit_armed and highest_close > 0 and latest_close <= highest_close * (1.0 - self.params.high_profit_close_drawdown_pct / 100.0):
                triggered.append("HIGH_PROFIT_PROTECTION")
        if triggered:
            details["triggered_events"] = triggered
            # How much each layer sells is a setting, not a constant.  When both
            # fire on the same bar the larger share wins rather than stacking.
            share = max(
                self.params.normal_sell_pct if "NORMAL_PROTECTION" in triggered else 0.0,
                self.params.high_sell_pct if "HIGH_PROFIT_PROTECTION" in triggered else 0.0,
            )
            fraction = min(1.0, max(0.0, share / 100.0))
            details["sell_share_pct"] = share
            return StrategyDecision(
                "SELL",
                symbol,
                "+".join(triggered),
                event="PRICE_PROTECTION",
                signal=signal,
                market_state=market_state,
                quantity_fraction=fraction,
                details=details,
                scope="POSITION_MANAGEMENT",
            )
        return StrategyDecision(
            "WAIT", symbol, "HOLD_POSITION", signal=signal,
            market_state=market_state, details=details, scope="POSITION_MANAGEMENT",
        )

def evaluate(market_context: dict[str, Any], portfolio_state: dict[str, Any]) -> StrategyDecision:
    return StaticRule().evaluate(market_context, portfolio_state)
