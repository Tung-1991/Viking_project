from __future__ import annotations

import math
import pytest

from viking_v2.rules.business import (
    crossover_count,
    crossover_signal,
    crossover_signal_from_snapshots,
    entry_volume_snapshot,
    indicator_snapshot,
)
from viking_v2.rules.business import StaticRule, StaticRuleParameters, classify_market_state


def _bars(values, *, last_closed=True):
    rows = [
        {"open": value, "high": value + 1, "low": value - 1, "close": value, "volume": 1_000_000, "closed": True}
        for value in values
    ]
    if rows:
        rows[-1]["closed"] = last_closed
    return rows


def test_whipsaw_counts_daily_ema_crosses_not_each_intraday_signal_flip():
    history = [100.0] * 20 + [99.0, 98.0, 97.0]
    # Same provisional daily candle observed at 13:00, 13:40, then after 14:00.
    counts = [crossover_count(_bars(history + [price], last_closed=False), window=7)
              for price in (101.0, 101.0, 96.0)]
    assert counts == [2, 2, 1]  # Recomputed from daily bars; not an accumulating tick counter.
    rule = StaticRule()
    decisions = [rule.evaluate(
        {"symbol": "TEST", "bars": _bars(history + [price], last_closed=False),
         "signal_mode": "REALTIME", "previous_market_state": "UPTREND"},
        {"available_capital": 100_000_000, "open_positions": 0},
    ) for price in (101.0, 101.0, 96.0)]
    assert [decision.details["entry_checks"]["whipsaw_crossovers"] for decision in decisions] == counts


M_VALUES = [100] * 15 + [99, 98, 97, 98, 100]
B_VALUES = [100] * 14 + [101, 102, 103, 104, 102, 99]


def test_phase2_generates_only_documented_m_and_b_signals():
    assert crossover_signal(_bars(M_VALUES)) == "BUY"
    assert crossover_signal(_bars(B_VALUES)) == "SELL"


def test_phase2_can_use_independent_buy_and_sell_ema_pairs():
    assert crossover_signal(
        _bars(M_VALUES), 3, 6, 14, sell_fast=5, sell_slow=10
    ) == "BUY"
    assert crossover_signal(
        _bars(B_VALUES), 3, 6, 14, sell_fast=5, sell_slow=10
    ) == ""
    assert crossover_signal(
        _bars(B_VALUES), 5, 10, 14, sell_fast=3, sell_slow=6
    ) == "SELL"


def test_default_exit_ema_pair_remains_three_six():
    params = StaticRuleParameters()
    assert (params.sell_ema_fast, params.sell_ema_slow) == (3, 6)
    restored = StaticRuleParameters.from_dict({"buy_ema_fast": 3, "buy_ema_slow": 6})
    assert (restored.sell_ema_fast, restored.sell_ema_slow) == (3, 6)


def test_long_sell_ema_history_does_not_suppress_a_ready_buy_signal():
    assert crossover_signal(
        _bars(M_VALUES), 3, 6, 14,
        sell_fast=100, sell_slow=200,
        buy_use_ema=True, buy_use_rsi=True,
        sell_use_ema=True, sell_use_rsi=True,
        prefer="BUY",
    ) == "BUY"


def test_realtime_crossover_compares_two_consecutive_observations():
    previous = {
        "buy_ema_fast": 9.9, "buy_ema_slow": 10.0,
        "sell_ema_fast": 9.9, "sell_ema_slow": 10.0,
    }
    current = {
        "buy_ema_fast": 10.1, "buy_ema_slow": 10.0,
        "sell_ema_fast": 10.1, "sell_ema_slow": 10.0,
        "rsi": 55.0, "rsi_previous": 50.0,
    }
    assert crossover_signal_from_snapshots(current, previous, buy_signal_require_ema_cross=False) == "BUY"

    previous = {
        "buy_ema_fast": 10.1, "buy_ema_slow": 10.0,
        "sell_ema_fast": 10.1, "sell_ema_slow": 10.0,
    }
    current = {
        "buy_ema_fast": 9.9, "buy_ema_slow": 10.0,
        "sell_ema_fast": 9.9, "sell_ema_slow": 10.0,
        "rsi": 45.0, "rsi_previous": 50.0,
    }
    assert crossover_signal_from_snapshots(current, previous, prefer="SELL") == "SELL"


@pytest.mark.parametrize("current_rsi,previous_tick_rsi,expected", [
    (55.0, 90.0, "BUY"),  # RSI falls from the last tick but exceeds the daily baseline.
    (49.0, 20.0, ""),     # RSI rises from the last tick but is below the daily baseline.
    (50.0, 20.0, ""),     # Equality with yesterday is not a BUY.
])
def test_realtime_buy_rsi_compares_with_previous_daily_candle_not_previous_tick(
    current_rsi, previous_tick_rsi, expected,
):
    previous = {"buy_ema_fast": 9.9, "buy_ema_slow": 10.0, "rsi": previous_tick_rsi}
    current = {"buy_ema_fast": 10.1, "buy_ema_slow": 10.0,
               "rsi": current_rsi, "rsi_previous": 50.0}
    assert crossover_signal_from_snapshots(current, previous, sell_use_ema=False, sell_use_rsi=False) == expected


def test_realtime_rsi_recovery_without_a_new_ema_cross_depends_on_optional_filter():
    previous = {"buy_ema_fast": 22.4163, "buy_ema_slow": 22.4031, "rsi": 56.7921}
    current = {"buy_ema_fast": 22.4413, "buy_ema_slow": 22.4174,
               "rsi": 57.7026, "rsi_previous": 56.7921}
    assert crossover_signal_from_snapshots(current, previous, buy_signal_require_ema_cross=False) == "BUY"
    assert crossover_signal_from_snapshots(current, previous, buy_signal_require_ema_cross=True) == ""


@pytest.mark.parametrize("current_rsi,previous_tick_rsi,expected", [
    (45.0, 20.0, "SELL"), (51.0, 90.0, ""), (50.0, 90.0, ""),
])
def test_realtime_sell_rsi_also_uses_daily_baseline(current_rsi, previous_tick_rsi, expected):
    previous = {"sell_ema_fast": 10.1, "sell_ema_slow": 10.0, "rsi": previous_tick_rsi}
    current = {"sell_ema_fast": 9.9, "sell_ema_slow": 10.0,
               "rsi": current_rsi, "rsi_previous": 50.0}
    assert crossover_signal_from_snapshots(current, previous, buy_use_ema=False, buy_use_rsi=False) == expected


def test_rebuilding_today_daily_candle_keeps_yesterdays_rsi_fixed():
    completed = _bars(M_VALUES)
    baseline = indicator_snapshot(completed)["rsi"]
    snapshots = [indicator_snapshot(completed + _bars([price], last_closed=False))
                 for price in (99.0, 100.0, 101.0)]
    assert all(snapshot["rsi_previous"] == baseline for snapshot in snapshots)
    assert len({snapshot["rsi"] for snapshot in snapshots}) == 3


def test_phase2_only_requires_the_indicators_enabled_for_each_signal():
    previous = {
        "buy_ema_fast": 9.9, "buy_ema_slow": 10.0,
        "sell_ema_fast": 9.9, "sell_ema_slow": 10.0,
    }
    current = {
        "buy_ema_fast": 10.1, "buy_ema_slow": 10.0,
        "sell_ema_fast": 10.1, "sell_ema_slow": 10.0,
        "rsi": 45.0, "rsi_previous": 50.0,
    }

    assert crossover_signal_from_snapshots(
        current, previous, buy_use_ema=True, buy_use_rsi=False,
    ) == "BUY"
    assert crossover_signal_from_snapshots(
        current, previous, buy_use_ema=True, buy_use_rsi=True,
    ) == ""

    no_cross = {
        **current,
        "buy_ema_fast": 9.9,
        "rsi": 55.0,
    }
    assert crossover_signal_from_snapshots(
        no_cross, previous, buy_use_ema=False, buy_use_rsi=True,
    ) == "BUY"
    assert crossover_signal_from_snapshots(
        no_cross, previous, buy_use_ema=False, buy_use_rsi=False,
    ) == ""


def test_indicator_snapshot_exposes_the_exact_preview_values():
    snapshot = indicator_snapshot(_bars(M_VALUES), 3, 6, 14, sell_fast=5, sell_slow=10)
    assert snapshot["ema_fast_period"] == 3
    assert snapshot["ema_slow_period"] == 6
    assert snapshot["rsi_period"] == 14
    assert snapshot["ema_fast"] is not None
    assert snapshot["ema_slow"] is not None
    assert snapshot["buy_ema_fast_period"] == 3
    assert snapshot["buy_ema_slow_period"] == 6
    assert snapshot["sell_ema_fast_period"] == 5
    assert snapshot["sell_ema_slow_period"] == 10
    assert snapshot["sell_ema_fast"] is not None
    assert snapshot["sell_ema_slow"] is not None
    assert snapshot["rsi"] is not None
    assert snapshot["rsi_previous"] is not None


def test_strategy_decision_carries_indicator_snapshot_for_the_ui():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars(M_VALUES), "previous_market_state": "UPTREND"},
        {"available_capital": 100_000_000, "open_positions": 0},
    )
    assert decision.details["indicators"] == indicator_snapshot(_bars(M_VALUES))


def test_strategy_decision_carries_phase1_confirmation_and_phase3_checks_for_preview():
    decision = StaticRule().evaluate(
        {
            "symbol": "FPT",
            "bars": _bars([100] * 20),
            "vnindex_bars": _bars([100] * 240),
            "confirmed_market_state": "UNKNOWN",
            "market_confirmation": {
                "confirmed": "UNKNOWN",
                "display": "TRANSITION",
                "count": 1,
                "required": 3,
                "pending": True,
            },
        },
        {
            "available_capital": 97_000_000,
            "order_budget": 90_000_000,
            "open_positions": 2,
            "loss_streak": 1,
        },
    )
    assert decision.details["market"]["display_state"] == "TRANSITION"
    assert decision.details["market"]["confirmation_count"] == 1
    assert decision.details["entry_checks"]["open_positions"] == 2
    assert decision.details["entry_checks"]["available_capital"] == 97_000_000


def test_realtime_mode_can_use_live_daily_bar_but_closed_mode_cannot():
    rule = StaticRule(StaticRuleParameters(buy_signal_session_cross_enabled=False))
    context = {
        "symbol": "FPT",
        "bars": _bars(M_VALUES, last_closed=False),
        "vnindex_bars": [],
        "previous_market_state": "UPTREND",
    }
    realtime = rule.evaluate(
        {**context, "signal_mode": "REALTIME"},
        {"available_capital": 100_000_000, "open_positions": 0},
    )
    closed = rule.evaluate(
        {**context, "signal_mode": "CLOSED"},
        {"available_capital": 100_000_000, "open_positions": 0},
    )
    default = rule.evaluate(context, {"available_capital": 100_000_000, "open_positions": 0})
    assert realtime.action == "BUY" and realtime.signal == "BUY"
    assert closed.action == "WAIT"
    # An omitted mode must behave like CLOSED, never like REALTIME.
    assert default.action == "WAIT"


def test_closed_and_realtime_use_independent_comparison_points():
    bars = _bars(M_VALUES)
    current = indicator_snapshot(bars)
    context = {
        "symbol": "FPT",
        "bars": bars,
        "previous_market_state": "UPTREND",
        # The live stream was already above on its preceding observation, so
        # there is no new intraday transition now.
        "previous_indicators": current,
    }
    portfolio = {"available_capital": 100_000_000, "open_positions": 0}
    rule = StaticRule(StaticRuleParameters(buy_signal_require_ema_cross=True))
    realtime = rule.evaluate({**context, "signal_mode": "REALTIME"}, portfolio)
    closed = rule.evaluate({**context, "signal_mode": "CLOSED"}, portfolio)
    assert realtime.signal == "" and realtime.action == "WAIT"
    assert closed.signal == "BUY" and closed.action == "BUY"


def test_entry_respects_pending_loss_lock_capacity_and_capital():
    rule = StaticRule()
    context = {"symbol": "FPT", "bars": _bars(M_VALUES), "previous_market_state": "UPTREND"}
    assert rule.evaluate(context, {"pending_buy": True, "available_capital": 1}).reason == "BUY_ALREADY_PENDING"
    assert rule.evaluate(context, {"loss_streak": 3, "available_capital": 1}).reason == "LOCKED_AFTER_LOSSES"
    assert rule.evaluate(context, {"open_positions": 5, "available_capital": 1}).reason == "MAX_POSITIONS"
    assert rule.evaluate(context, {"open_positions": 0, "available_capital": 0}).reason == "NO_AVAILABLE_CAPITAL"


def test_priority_entry_cannot_bypass_total_positions():
    rule = StaticRule()
    context = {
        "symbol": "FPT", "bars": _bars(M_VALUES),
        "previous_market_state": "UPTREND", "priority_entry": True,
    }
    allowed = rule.evaluate(context, {"open_positions": 5, "available_capital": 1})
    no_capital = rule.evaluate(context, {"open_positions": 4, "entry_slot_available": True, "available_capital": 0})
    removed = rule.evaluate(
        {**context, "entry_allowed": False},
        {"open_positions": 5, "available_capital": 1},
    )

    assert allowed.reason == "MAX_POSITIONS"
    assert no_capital.reason == "NO_AVAILABLE_CAPITAL"
    assert removed.reason == "NOT_IN_WATCHLIST"


def test_stop_loss_is_realtime_and_has_priority_over_indicator_and_protection():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {
            "position": {
                "quantity": 1000,
                "avg_price": 100,
                "current_price": 96.4,
                "peak_profit_pct": 25,
                "highest_close": 110,
            }
        },
    )
    assert decision.action == "SELL"
    assert decision.event == "STOP_LOSS"
    assert decision.quantity_fraction == 1.0


def test_reentry_uses_minus_2_5_percent_stop():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars([100] * 20), "previous_market_state": "UPTREND"},
        {"position": {"quantity": 100, "avg_price": 100, "current_price": 97.4, "is_reentry": True}},
    )
    assert decision.event == "STOP_LOSS"


def test_normal_protection_sells_all_by_default_once_condition_is_met():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars([103.5] * 20), "previous_market_state": "UPTREND"},
        {"position": {"quantity": 900, "avg_price": 100, "current_price": 103.5, "peak_profit_pct": 7.2, "normal_armed": True, "em_modes": ["NORMAL"]}},
    )
    assert decision.action == "SELL"
    assert decision.event == "PRICE_PROTECTION"
    assert decision.details["triggered_events"] == ["NORMAL_PROTECTION"]
    assert math.isclose(decision.quantity_fraction, 1.0)


def test_indicator_b_sells_all_remaining_position():
    decision = StaticRule(StaticRuleParameters(indicator_exit_policy="AUTO")).evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {"position": {"quantity": 700, "avg_price": 100, "current_price": 99, "em_modes": ["IND_EXIT"]}},
    )
    assert decision.action == "SELL"
    assert decision.event == "INDICATOR_EXIT"
    assert decision.quantity_fraction == 1.0


def test_entry_volume_compares_current_daily_candle_with_prior_closed_sessions():
    bars = _bars([100] * 5)
    for row, volume in zip(bars, [100, 200, 300, 400, 600]):
        row["volume"] = volume
    snapshot = entry_volume_snapshot(bars, sessions=3)
    assert snapshot["ready"] is True
    assert snapshot["average"] == 300
    assert snapshot["ratio"] == 2


def test_entry_volume_filter_blocks_low_volume_and_allows_high_volume_buy():
    context = {
        "symbol": "FPT", "bars": _bars(M_VALUES),
        "previous_market_state": "UPTREND",
    }
    portfolio = {"available_capital": 100_000_000, "open_positions": 0}
    params = StaticRuleParameters(
        buy_volume_enabled=True,
        buy_volume_average_sessions=3,
        buy_volume_min_ratio=1.2,
    )
    low_bars = _bars(M_VALUES)
    low_bars[-1]["volume"] = 1_000
    low = StaticRule(params).evaluate({**context, "bars": low_bars}, portfolio)
    assert low.reason == "BUY_VOLUME_LOW"
    high_bars = _bars(M_VALUES)
    high_bars[-1]["volume"] = 2_000_000
    high = StaticRule(params).evaluate({**context, "bars": high_bars}, portfolio)
    assert high.action == "BUY"


def test_disabled_trade_stop_loss_does_not_hide_e_or_protect_logic():
    context = {"symbol": "FPT", "bars": _bars([90] * 20), "previous_market_state": "UPTREND"}
    no_exit = StaticRule().evaluate(
        context,
        {"position": {
            "quantity": 100, "avg_price": 100, "current_price": 90,
            "sl_enabled": False, "em_modes": [],
        }},
    )
    assert no_exit.action == "WAIT"
    assert no_exit.details["sl_mode"] == "OFF"

    indicator_exit = StaticRule(
        StaticRuleParameters(indicator_exit_policy="AUTO")
    ).evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {"position": {
            "quantity": 100, "avg_price": 100, "current_price": 90,
            "sl_enabled": False, "em_modes": ["IND_EXIT"],
        }},
    )
    assert indicator_exit.event == "INDICATOR_EXIT"


def test_indicator_exit_alert_reports_signal_without_selling():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {"position": {
            "quantity": 700, "avg_price": 100, "current_price": 99,
            "em_modes": ["IND_EXIT"],
        }},
    )
    assert decision.action == "WAIT"
    assert decision.event == "INDICATOR_EXIT_ALERT"
    assert decision.signal == "SELL"
    assert decision.quantity_fraction == 0.0
    assert decision.details["indicator_exit_policy"] == "ALERT"


def test_indicator_exit_alert_never_blocks_auto_protect_exit():
    decision = StaticRule(StaticRuleParameters(
        indicator_exit_policy="ALERT",
        normal_policy="AUTO",
    )).evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {"position": {
            "quantity": 700, "avg_price": 100, "current_price": 99,
            "peak_profit_pct": 7.2, "normal_armed": True,
            "em_modes": ["NORMAL", "IND_EXIT"],
        }},
    )
    assert decision.action == "SELL"
    assert decision.event == "PRICE_PROTECTION"
    assert decision.details["triggered_events"] == ["NORMAL_PROTECTION"]


def test_market_override_is_reported_and_uses_fixed_exposure():
    decision = StaticRule().evaluate(
        {
            "symbol": "FPT", "bars": _bars(M_VALUES),
            "previous_market_state": "DOWNTREND",
            "confirmed_market_state": "ACCUMULATION",
            "effective_exposure": 0.65,
            "market_override": {"enabled": True, "state": "ACCUMULATION"},
        },
        {"available_capital": 100_000_000, "open_positions": 0},
    )
    assert decision.market_state == "ACCUMULATION"
    assert decision.details["exposure"] == 0.65
    assert decision.details["market"]["override_enabled"] is True


def test_phase1_identifies_rising_and_falling_market_structure():
    rising = [100 + index * 0.25 + math.sin(index / 3) * 3 for index in range(240)]
    falling = [200 - index * 0.25 + math.sin(index / 3) * 3 for index in range(240)]
    assert classify_market_state(_bars(rising))[0] == "UPTREND"
    assert classify_market_state(_bars(falling))[0] == "DOWNTREND"


def test_volume_is_confidence_metadata_not_a_separate_state_or_signal():
    values = [100 + index * 0.25 + math.sin(index / 3) * 3 for index in range(240)]
    rows = _bars(values)
    rows[-1]["volume"] = 10_000_000
    state, details = classify_market_state(
        rows, params=StaticRuleParameters(volume_confirmation=True)
    )
    assert state == "UPTREND"
    assert details["volume_confidence"] == "CAO"
    _, disabled = classify_market_state(rows)
    assert disabled["volume_confidence"] == "OFF"


def test_rule_validation_rejects_a_zero_percent_exit():
    with pytest.raises(ValueError, match="Tỷ lệ bán"):
        StaticRuleParameters(normal_sell_pct=0).validate()


def test_corporate_action_is_not_executed_before_execution_rule_exists():
    context = {"symbol": "FPT", "bars": _bars([100] * 20), "previous_market_state": "UPTREND"}
    position = {"quantity": 100, "avg_price": 100, "current_price": 100}
    assert StaticRule().evaluate(context, {"position": position}).action == "WAIT"
    decision = StaticRule().evaluate(context, {"position": {**position, "corporate_exit_due": True}})
    assert decision.event == ""
    assert decision.action == "WAIT"


def test_corporate_action_mark_blocks_new_buy_but_only_warns_for_open_position():
    context = {"symbol": "FPT", "bars": _bars(M_VALUES), "previous_market_state": "UPTREND"}
    blocked = StaticRule().evaluate(
        context,
        {
            "available_capital": 100_000_000,
            "open_positions": 0,
            "corporate_action_blocked": True,
            "corporate_action": {"symbol": "FPT", "ex_date": "2026-08-20"},
        },
    )
    assert blocked.action == "WAIT"
    assert blocked.reason == "CORPORATE_ACTION_BLOCK"

    held = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars([100] * 20), "previous_market_state": "UPTREND"},
        {
            "position": {"quantity": 100, "avg_price": 100, "current_price": 100},
            "corporate_action": {"symbol": "FPT", "ex_date": "2026-08-20"},
            "corporate_action_warning": True,
        },
    )
    assert held.action == "WAIT"
    assert held.details["corporate_action_warning"] is True


def test_protect_sell_share_is_a_setting_not_a_constant():
    """How much each protection sells is configurable; 100% is the default."""
    position = {
        "quantity": 300, "avg_price": 100, "current_price": 104,
        "peak_profit_pct": 8, "normal_armed": True, "em_modes": ["NORMAL"],
    }
    context = {"symbol": "FPT", "bars": _bars([104] * 24), "confirmed_market_state": "UPTREND"}
    assert math.isclose(
        StaticRule().evaluate(context, {"position": position}).quantity_fraction, 1.0)
    whole = StaticRule(StaticRuleParameters(normal_sell_pct=100)).evaluate(
        context, {"position": position})
    assert math.isclose(whole.quantity_fraction, 1.0)
    half = StaticRule(StaticRuleParameters(normal_sell_pct=50)).evaluate(
        context, {"position": position})
    assert math.isclose(half.quantity_fraction, 0.5)
