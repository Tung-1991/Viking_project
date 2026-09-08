from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from ..models import StrategyDecision
from ..trading.market import active_trading_minutes, normalize_exchange, validate_buy_window


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
    buy_use_ema: bool = True,
    buy_use_rsi: bool = True,
    sell_use_ema: bool = True,
    sell_use_rsi: bool = True,
) -> str:
    rows = list(rows or [])
    values = closes(rows)
    buy_fast = max(1, int(fast or 3))
    buy_slow = max(1, int(slow or 6))
    exit_fast = max(1, int(sell_fast if sell_fast is not None else buy_fast))
    exit_slow = max(1, int(sell_slow if sell_slow is not None else buy_slow))
    if len(values) < 2:
        return ""
    current = indicator_snapshot(
        rows, buy_fast, buy_slow, rsi_period,
        sell_fast=exit_fast, sell_slow=exit_slow,
    )
    previous = indicator_snapshot(
        rows[:-1], buy_fast, buy_slow, rsi_period,
        sell_fast=exit_fast, sell_slow=exit_slow,
    )
    return crossover_signal_from_snapshots(
        current,
        previous,
        prefer=prefer,
        buy_use_ema=buy_use_ema,
        buy_use_rsi=buy_use_rsi,
        sell_use_ema=sell_use_ema,
        sell_use_rsi=sell_use_rsi,
    )


def crossover_signal_from_snapshots(
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    *,
    prefer: str = "BUY",
    buy_use_ema: bool = True,
    buy_use_rsi: bool = True,
    sell_use_ema: bool = True,
    sell_use_rsi: bool = True,
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
    sample_count = current.get("sample_count")
    try:
        count = int(sample_count) if sample_count is not None else None
    except (TypeError, ValueError):
        count = None
    buy_slow_period = int(current.get("buy_ema_slow_period", 1) or 1)
    sell_slow_period = int(current.get("sell_ema_slow_period", 1) or 1)
    period = int(current.get("rsi_period", 1) or 1)

    def side_ready(*, use_ema: bool, use_rsi: bool, slow_period: int,
                   ema_values: tuple[float | None, ...]) -> bool:
        required: list[float | None] = []
        if use_rsi:
            required.extend((current_rsi, previous_daily_rsi))
        if use_ema:
            required.extend(ema_values)
        history = max(2, slow_period + 1 if use_ema else 2, period + 2 if use_rsi else 2)
        return (count is None or count >= history) and not any(value is None for value in required)

    buy_ready = side_ready(
        use_ema=buy_use_ema, use_rsi=buy_use_rsi, slow_period=buy_slow_period,
        ema_values=(current_buy_fast, current_buy_slow, previous_buy_fast, previous_buy_slow),
    )
    sell_ready = side_ready(
        use_ema=sell_use_ema, use_rsi=sell_use_rsi, slow_period=sell_slow_period,
        ema_values=(current_sell_fast, current_sell_slow, previous_sell_fast, previous_sell_slow),
    )

    crossed_up = not buy_use_ema or (
        previous_buy_fast <= previous_buy_slow and current_buy_fast > current_buy_slow
    )
    crossed_down = not sell_use_ema or (
        previous_sell_fast >= previous_sell_slow and current_sell_fast < current_sell_slow
    )
    buy_signal = buy_ready and bool(buy_use_ema or buy_use_rsi) and (
        (not buy_use_ema or crossed_up)
        and (not buy_use_rsi or current_rsi > previous_daily_rsi)
    )
    sell_signal = sell_ready and bool(sell_use_ema or sell_use_rsi) and (
        (not sell_use_ema or crossed_down)
        and (not sell_use_rsi or current_rsi < previous_daily_rsi)
    )
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
        "sample_count": len(values),
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
    buy_signal_use_ema: bool = True
    buy_signal_use_rsi: bool = True
    sell_signal_use_ema: bool = True
    sell_signal_use_rsi: bool = True
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
    # CLASSIC keeps the original peak-price drawdown. AUTO protects profit
    # points; ALERT waits for an explicit operator choice.
    normal_policy: str = "CLASSIC"
    high_profit_arm_pct: float = 20.0
    high_profit_close_drawdown_pct: float = 5.0
    high_sell_pct: float = 33.0
    whipsaw_enabled: bool = True
    whipsaw_n: int = 3
    whipsaw_x: int = 7
    buy_confirmation_enabled: bool = False
    buy_confirmation_minutes: int = 5
    buy_confirmation_require_ema: bool = True
    buy_confirmation_require_rsi: bool = True
    buy_window_enabled: bool = False
    buy_window_start: str = "14:00"
    exposure: dict[str, float] = field(default_factory=lambda: dict(EXPOSURE_DEFAULTS))

    def __post_init__(self) -> None:
        self.buy_confirmation_enabled = bool(self.buy_confirmation_enabled)
        self.buy_signal_use_ema = bool(self.buy_signal_use_ema)
        self.buy_signal_use_rsi = bool(self.buy_signal_use_rsi)
        self.sell_signal_use_ema = bool(self.sell_signal_use_ema)
        self.sell_signal_use_rsi = bool(self.sell_signal_use_rsi)
        try:
            self.buy_confirmation_minutes = max(1, min(120, int(self.buy_confirmation_minutes or 5)))
        except (TypeError, ValueError):
            self.buy_confirmation_minutes = 5
        self.buy_confirmation_require_ema = bool(self.buy_confirmation_require_ema)
        self.buy_confirmation_require_rsi = bool(self.buy_confirmation_require_rsi)
        self.buy_window_enabled = bool(self.buy_window_enabled)
        self.buy_window_start = str(self.buy_window_start).strip()
        self.normal_policy = str(self.normal_policy or "CLASSIC").strip().upper()
        if self.normal_policy == "TSL":  # compatibility with the short-lived draft name
            self.normal_policy = "AUTO"
        if self.normal_policy not in {"CLASSIC", "AUTO", "ALERT"}:
            self.normal_policy = "CLASSIC"
        validate_buy_window(self.buy_window_start, "15:00")

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

    def validate(self) -> "StaticRuleParameters":
        """Reject settings the engine cannot execute as the UI describes."""
        positive_integers = {
            "MA dài hạn": self.ma_period,
            "Pivot trái": self.pivot_left,
            "Pivot phải": self.pivot_right,
            "Số phiên xác nhận": self.confirm_sessions,
            "Volume trung bình": self.volume_average_sessions,
            "BUY EMA nhanh": self.buy_ema_fast,
            "BUY EMA chậm": self.buy_ema_slow,
            "SELL EMA nhanh": self.sell_ema_fast,
            "SELL EMA chậm": self.sell_ema_slow,
            "RSI": self.rsi_period,
            "Số position": self.max_positions,
            "Số LOSS khóa": self.loss_lock_count,
            "Giờ khóa": self.loss_lock_hours,
            "Whipsaw N": self.whipsaw_n,
            "Whipsaw X": self.whipsaw_x,
        }
        invalid = next((name for name, value in positive_integers.items() if int(value) < 1), "")
        if invalid:
            raise ValueError(f"{invalid} phải lớn hơn 0")
        if self.buy_ema_slow <= self.buy_ema_fast:
            raise ValueError("BUY EMA chậm phải lớn hơn BUY EMA nhanh")
        if self.sell_ema_slow <= self.sell_ema_fast:
            raise ValueError("SELL EMA chậm phải lớn hơn SELL EMA nhanh")
        if self.whipsaw_x < 2:
            raise ValueError("Whipsaw X phải từ 2 phiên")
        if not (self.buy_signal_use_ema or self.buy_signal_use_rsi):
            raise ValueError("Tín hiệu BUY phải bật ít nhất EMA hoặc RSI")
        if not (self.sell_signal_use_ema or self.sell_signal_use_rsi):
            raise ValueError("Tín hiệu SELL phải bật ít nhất EMA hoặc RSI")
        if self.initial_sl_pct >= 0 or self.reentry_sl_pct >= 0:
            raise ValueError("Stop Loss phải là số âm")
        if not 0 < self.normal_sell_pct <= 100 or not 0 < self.high_sell_pct <= 100:
            raise ValueError("Tỷ lệ bán NORMAL/HIGH phải lớn hơn 0 và không quá 100%")
        if not 0 < self.normal_giveback_pct <= 100:
            raise ValueError("Mức giảm NORMAL phải lớn hơn 0 và không quá 100%")
        if not 0 < self.high_profit_close_drawdown_pct <= 100:
            raise ValueError("Mức giảm HIGH phải lớn hơn 0 và không quá 100%")
        if min(self.take_profit_pct, self.normal_arm_pct, self.high_profit_arm_pct) < 0:
            raise ValueError("Ngưỡng lợi nhuận không được là số âm")
        if min(self.pivot_horizontal_pct, self.ma_zone_pct) < 0:
            raise ValueError("Sai số Pivot và vùng MA không được là số âm")
        if self.high_volume_ratio < self.low_volume_ratio or self.low_volume_ratio < 0:
            raise ValueError("Volume cao phải lớn hơn hoặc bằng Volume thấp")
        if any(not 0 <= float(value) <= 1 for value in self.exposure.values()):
            raise ValueError("Tỷ trọng thị trường phải nằm trong 0–100%")
        if self.buy_confirmation_enabled:
            if not (self.buy_confirmation_require_ema or self.buy_confirmation_require_rsi):
                raise ValueError("Xác nhận BUY phải chọn ít nhất EMA hoặc RSI")
            if not 1 <= self.buy_confirmation_minutes <= 120:
                raise ValueError("Xác nhận BUY phải từ 1 đến 120 phút")
        validate_buy_window(self.buy_window_start, "15:00")
        return self

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


def buy_confirmation_conditions(
    indicators: dict[str, Any] | None,
    params: StaticRuleParameters,
) -> dict[str, bool]:
    """Current hold conditions layered on the enabled base BUY conditions."""
    values = indicators if isinstance(indicators, dict) else {}
    try:
        ema_ok = float(values.get("buy_ema_fast")) > float(values.get("buy_ema_slow"))
    except (TypeError, ValueError):
        ema_ok = False
    try:
        rsi_ok = float(values.get("rsi")) > float(values.get("rsi_previous"))
    except (TypeError, ValueError):
        rsi_ok = False
    selected_ok = (
        (not params.buy_confirmation_require_ema or ema_ok)
        and (not params.buy_confirmation_require_rsi or rsi_ok)
    )
    return {"ema": ema_ok, "rsi": rsi_ok, "selected": selected_ok}


def advance_buy_confirmation(
    state: dict[str, Any] | None,
    *,
    raw_trigger: bool,
    indicators: dict[str, Any] | None,
    observed_at: datetime,
    exchange: str,
    params: StaticRuleParameters,
    working_dates: Iterable[str] | None = None,
    holidays: Iterable[str] | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Advance one BUY candidate using only active minutes of its exchange."""
    if not params.buy_confirmation_enabled:
        return {}, "BYPASS", {}
    if not (params.buy_confirmation_require_ema or params.buy_confirmation_require_rsi):
        return {}, "INVALID", {"reason": "BUY_CONFIRMATION_NO_CONDITION"}
    market = normalize_exchange(exchange)
    if not market:
        return {}, "INVALID", {"reason": "UNKNOWN_EXCHANGE"}
    current = dict(state or {})
    rule_signature = (
        f"{params.buy_confirmation_minutes}:"
        f"{int(params.buy_confirmation_require_ema)}:"
        f"{int(params.buy_confirmation_require_rsi)}"
    )
    if current and current.get("rule_signature") != rule_signature:
        current = {}
    started_now = False
    if not current.get("active"):
        if not raw_trigger:
            return {}, "IDLE", {}
        current = {
            "active": True,
            "started_at": observed_at.isoformat(),
            "exchange": market,
            "rule_signature": rule_signature,
        }
        started_now = True
    try:
        started_at = datetime.fromisoformat(str(current.get("started_at") or ""))
    except ValueError:
        return {}, "CANCELLED", {"reason": "BUY_CONFIRMATION_STATE_INVALID"}
    checks = buy_confirmation_conditions(indicators, params)
    held = active_trading_minutes(
        started_at, observed_at, market,
        working_dates=working_dates, holidays=holidays,
    )
    details = {
        "signal_time": started_at.isoformat(),
        "observed_time": observed_at.isoformat(),
        "minutes_held": min(float(params.buy_confirmation_minutes), max(0.0, held)),
        "minutes_required": params.buy_confirmation_minutes,
        "require_ema": params.buy_confirmation_require_ema,
        "require_rsi": params.buy_confirmation_require_rsi,
        "ema_ok": checks["ema"],
        "rsi_ok": checks["rsi"],
    }
    if not checks["selected"]:
        details["reason"] = "BUY_CONFIRMATION_BROKEN"
        return {}, "CANCELLED", details
    if held >= params.buy_confirmation_minutes and not started_now:
        details["confirmed_time"] = observed_at.isoformat()
        return {}, "CONFIRMED", details
    current["updated_at"] = observed_at.isoformat()
    return current, "WAITING", details

def _working_bars(rows: list[dict[str, Any]], signal_mode: str) -> list[dict[str, Any]]:
    values = [dict(row) for row in rows or [] if isinstance(row, dict)]
    # Anything other than an explicit REALTIME drops the unfinished candle: a
    # caller that forgot the mode must not trade on a bar that can still change.
    if str(signal_mode or "CLOSED").upper() != "REALTIME" and values and not bool(values[-1].get("closed", True)):
        values.pop()
    return values


def normal_auto_stop_profit(
    peak_profit_pct: float,
    arm_pct: float,
    trailing_gap_pct: float,
) -> float:
    """Profit percentage protected by NORMAL AUTO.

    ``trailing_gap_pct`` is expressed in percentage points.  The protected
    level trails MFE by a fixed number of percentage points after arming.
    """
    return float(peak_profit_pct) - float(trailing_gap_pct)

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
            "OFF" if not params.volume_confirmation
            else "HIGH" if volume_ratio >= params.high_volume_ratio
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
        supplied_indicators = context.get("indicator_snapshot")
        indicators = (
            dict(supplied_indicators)
            if isinstance(supplied_indicators, dict) and supplied_indicators
            else indicator_snapshot(
                bars,
                self.params.buy_ema_fast,
                self.params.buy_ema_slow,
                self.params.rsi_period,
                sell_fast=self.params.sell_ema_fast,
                sell_slow=self.params.sell_ema_slow,
            )
        )
        previous_indicators = context.get("previous_indicators")
        if signal_mode.upper() == "REALTIME" and isinstance(previous_indicators, dict):
            signal = crossover_signal_from_snapshots(
                indicators,
                previous_indicators,
                prefer="SELL" if quantity > 0 else "BUY",
                buy_use_ema=self.params.buy_signal_use_ema,
                buy_use_rsi=self.params.buy_signal_use_rsi,
                sell_use_ema=self.params.sell_signal_use_ema,
                sell_use_rsi=self.params.sell_signal_use_rsi,
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
                buy_use_ema=self.params.buy_signal_use_ema,
                buy_use_rsi=self.params.buy_signal_use_rsi,
                sell_use_ema=self.params.sell_signal_use_ema,
                sell_use_rsi=self.params.sell_signal_use_rsi,
            )
        if quantity <= 0 and bool(context.get("confirmed_buy")):
            signal = "BUY"
        # Whipsaw is an entry guard, so it follows the BUY EMA pair only.
        crosses = (
            crossover_count(
                bars,
                self.params.buy_ema_fast,
                self.params.buy_ema_slow,
                self.params.whipsaw_x,
            )
            if self.params.buy_signal_use_ema else 0
        )
        details = {
            "exchange": normalize_exchange(context.get("exchange")),
            "market": market_details,
            "exposure": self.params.exposure.get(market_state, 0.0),
            "indicators": indicators,
            "indicator_interval": str(context.get("indicator_interval", "") or ""),
            "buy_confirmation_forced": bool(context.get("confirmed_buy")),
            "entry_checks": {
                "nav": float(portfolio.get("nav", 0.0) or 0.0),
                "available_cash": float(portfolio.get("available_cash", 0.0) or 0.0),
                "open_positions": int(portfolio.get("open_positions", 0) or 0),
                "max_positions": self.params.max_positions,
                "available_capital": float(portfolio.get("available_capital", 0.0) or 0.0),
                "order_budget": float(portfolio.get("order_budget", 0.0) or 0.0),
                "minimum_order_room": float(portfolio.get("minimum_order_room", 0.0) or 0.0),
                "buy_fee_rate": float(portfolio.get("buy_fee_rate", 0.0) or 0.0),
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
            return StrategyDecision("WAIT", symbol, "LOCKED_AFTER_LOSSES", signal=signal, market_state=market_state, details=details)
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
        normal_enabled = (
            "NORMAL" in em_modes
            and not bool(position.get("normal_protection_done"))
            and not bool(position.get("normal_execution_managed"))
        )
        if normal_enabled:
            policy = self.params.normal_policy
            details.update(
                normal_policy=policy,
                normal_arm_pct=self.params.normal_arm_pct,
                normal_giveback_pct=self.params.normal_giveback_pct,
                sell_share_pct=self.params.normal_sell_pct,
            )
            if policy == "ALERT" and peak_profit >= self.params.normal_arm_pct:
                alert_status = str(position.get("normal_alert_status", "") or "").upper()
                protected = normal_auto_stop_profit(
                    peak_profit, self.params.normal_arm_pct, self.params.normal_giveback_pct,
                )
                details.update(
                    normal_alert=True,
                    normal_alert_status=alert_status or "PENDING",
                    normal_protected_profit_pct=protected,
                    sell_share_pct=self.params.normal_sell_pct,
                )
                if alert_status == "SELL":
                    details["triggered_events"] = ["NORMAL_ALERT_EXIT"]
                    return StrategyDecision(
                        "SELL", symbol, "NORMAL_ALERT_EXIT", event="NORMAL_ALERT_EXIT",
                        signal=signal, market_state=market_state,
                        quantity_fraction=min(1.0, self.params.normal_sell_pct / 100.0),
                        details=details, scope="POSITION_MANAGEMENT",
                    )
                if alert_status != "CONTINUE":
                    return StrategyDecision(
                        "WAIT", symbol, "NORMAL_ALERT", event="NORMAL_ALERT",
                        signal=signal, market_state=market_state, details=details,
                        scope="POSITION_MANAGEMENT",
                    )
            elif policy == "AUTO" and peak_profit >= self.params.normal_arm_pct:
                protected = normal_auto_stop_profit(
                    peak_profit, self.params.normal_arm_pct, self.params.normal_giveback_pct,
                )
                details["normal_protected_profit_pct"] = protected
                # Arming and triggering are separate observations. This prevents
                # an exact +7% first touch from selling on that same observation.
                if not bool(position.get("normal_armed")):
                    return StrategyDecision(
                        "WAIT", symbol, "NORMAL_ARMED", event="NORMAL_ARMED",
                        signal=signal, market_state=market_state, details=details,
                        scope="POSITION_MANAGEMENT",
                    )
                if current_profit <= protected + 1e-9:
                    triggered.append("NORMAL_PROTECTION")
            elif policy == "CLASSIC":
                # Original behaviour: giveback is a percentage of peak price.
                peak_price = entry * (1.0 + peak_profit / 100.0)
                trigger = peak_price * (1.0 - self.params.normal_giveback_pct / 100.0)
                if peak_profit >= self.params.normal_arm_pct and current <= trigger:
                    triggered.append("NORMAL_PROTECTION")
        highest_close = float(position.get("highest_close", 0.0) or 0.0)
        closed_values = closes(
            row for row in bars if isinstance(row, dict) and bool(row.get("closed", True))
        )
        latest_close = closed_values[-1] if closed_values else 0.0
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
