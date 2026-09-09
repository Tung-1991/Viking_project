from __future__ import annotations

import math
import pytest

from viking_v2.rules.business import (
    crossover_signal,
    crossover_signal_from_snapshots,
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
    assert crossover_signal_from_snapshots(current, previous) == "BUY"

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
    rule = StaticRule()
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
    realtime = StaticRule().evaluate({**context, "signal_mode": "REALTIME"}, portfolio)
    closed = StaticRule().evaluate({**context, "signal_mode": "CLOSED"}, portfolio)
    assert realtime.signal == "" and realtime.action == "WAIT"
    assert closed.signal == "BUY" and closed.action == "BUY"


def test_entry_respects_pending_loss_lock_capacity_and_capital():
    rule = StaticRule()
    context = {"symbol": "FPT", "bars": _bars(M_VALUES), "previous_market_state": "UPTREND"}
    assert rule.evaluate(context, {"pending_buy": True, "available_capital": 1}).reason == "BUY_ALREADY_PENDING"
    assert rule.evaluate(context, {"loss_streak": 3, "available_capital": 1}).reason == "LOCKED_AFTER_LOSSES"
    assert rule.evaluate(context, {"open_positions": 5, "available_capital": 1}).reason == "MAX_POSITIONS"
    assert rule.evaluate(context, {"open_positions": 0, "available_capital": 0}).reason == "NO_AVAILABLE_CAPITAL"


def test_stop_loss_is_realtime_and_has_priority_over_indicator_and_protection():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {
            "position": {
                "quantity": 1000,
                "avg_price": 100,
                "current_price": 96.9,
                "peak_profit_pct": 25,
                "highest_close": 110,
            }
        },
    )
    assert decision.action == "SELL"
    assert decision.event == "STOP_LOSS"
    assert decision.quantity_fraction == 1.0


def test_reentry_uses_minus_2_1_percent_stop():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars([100] * 20), "previous_market_state": "UPTREND"},
        {"position": {"quantity": 100, "avg_price": 100, "current_price": 97.8, "is_reentry": True}},
    )
    assert decision.event == "STOP_LOSS"


def test_normal_protection_sells_one_third_once_condition_is_met():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars([103.5] * 20), "previous_market_state": "UPTREND"},
        {"position": {"quantity": 900, "avg_price": 100, "current_price": 103.5, "peak_profit_pct": 7.2, "em_modes": ["NORMAL"]}},
    )
    assert decision.action == "SELL"
    assert decision.event == "PRICE_PROTECTION"
    assert decision.details["triggered_events"] == ["NORMAL_PROTECTION"]
    assert math.isclose(decision.quantity_fraction, 0.33)


def test_indicator_b_sells_all_remaining_position():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars(B_VALUES), "previous_market_state": "UPTREND"},
        {"position": {"quantity": 700, "avg_price": 100, "current_price": 99, "em_modes": ["IND_EXIT"]}},
    )
    assert decision.action == "SELL"
    assert decision.event == "INDICATOR_EXIT"
    assert decision.quantity_fraction == 1.0


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
    """How much each protection sells is configurable; 33% is only the default."""
    position = {
        "quantity": 300, "avg_price": 100, "current_price": 104,
        "peak_profit_pct": 8, "em_modes": ["NORMAL"],
    }
    context = {"symbol": "FPT", "bars": _bars([104] * 24), "confirmed_market_state": "UPTREND"}
    assert math.isclose(
        StaticRule().evaluate(context, {"position": position}).quantity_fraction, 0.33)
    whole = StaticRule(StaticRuleParameters(normal_sell_pct=100)).evaluate(
        context, {"position": position})
    assert math.isclose(whole.quantity_fraction, 1.0)
    half = StaticRule(StaticRuleParameters(normal_sell_pct=50)).evaluate(
        context, {"position": position})
    assert math.isclose(half.quantity_fraction, 0.5)
