from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from viking_v2.backtest.engine import (
    _Pending,
    _Position,
    _normal_policy_fill,
    _pending_matches_decision,
    _pending_protect_matches,
    _start_sellable_dynamic,
    exit_comparison_variants,
)
from viking_v2.backtest.models import BacktestScenario
from viking_v2.backtest.replay import ReplayDataStore
from viking_v2.config import AppSettings
from viking_v2.rules.business import (
    StaticRule,
    StaticRuleParameters,
    StrategyDecision,
    average_true_range_pct,
    protect_level,
    protect_rearm_mfe,
)
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.daemon import realtime_indicator_bucket


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _bars(close: float = 107.0) -> list[dict]:
    return [
        {
            "time": index,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000,
            "closed": True,
        }
        for index in range(30)
    ]


def _atr_bars(current: float = 100.0) -> list[dict]:
    completed = [
        {
            "time": index,
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 1_000,
            "closed": True,
        }
        for index in range(15)
    ]
    return [*completed, {
        "time": 16,
        "open": current,
        "high": current,
        "low": current,
        "close": current,
        "volume": 1_000,
        "closed": False,
    }]


def _position(**updates) -> dict:
    value = {
        "quantity": 1_000,
        "avg_price": 100.0,
        "current_price": 107.0,
        "peak_profit_pct": 10.0,
        "em_modes": ["NORMAL"],
    }
    value.update(updates)
    return value


def test_protect_defaults_use_the_reviewed_dynamic_configuration() -> None:
    params = StaticRuleParameters()
    assert params.normal_policy == "AUTO"
    assert params.normal_giveback_pct == pytest.approx(2.5)
    assert params.normal_atr_activation_multiplier == pytest.approx(0.55)
    assert params.normal_atr_multiplier == pytest.approx(0.8)
    assert params.normal_retention_pct == pytest.approx(90.0)
    assert params.normal_retention_until_pct == pytest.approx(5.0)
    assert params.normal_sell_pct == pytest.approx(100.0)
    assert params.normal_dynamic_enabled is True
    assert params.normal_repeat_enabled is False


def test_normal_auto_arms_first_then_sells_configured_fraction() -> None:
    rule = StaticRule(StaticRuleParameters(
        normal_policy="AUTO", normal_giveback_pct=2, normal_sell_pct=50,
    ))
    context = {"symbol": "FPT", "bars": _bars(), "confirmed_market_state": "UPTREND"}

    armed = rule.evaluate(
        context,
        {"position": _position(current_price=107.0, peak_profit_pct=7.0)},
    )
    assert armed.action == "WAIT"
    assert armed.reason == "NORMAL_ARMED"
    assert armed.details["normal_protected_profit_pct"] == pytest.approx(4.86)

    sold = rule.evaluate(
        context,
        {"position": _position(current_price=106.5, peak_profit_pct=9.0, normal_armed=True)},
    )
    assert sold.action == "SELL"
    assert sold.reason == "NORMAL_PROTECTION"
    assert sold.details["normal_protected_profit_pct"] == pytest.approx(6.82)
    assert sold.quantity_fraction == pytest.approx(0.5)


def test_removed_and_classic_normal_policy_migrate_to_auto() -> None:
    params = StaticRuleParameters.from_dict({"normal_policy": "REMOVED"})
    assert params.normal_policy == "AUTO"
    assert StaticRuleParameters.from_dict({"normal_policy": "CLASSIC"}).normal_policy == "AUTO"
    assert StaticRuleParameters.from_dict({}).indicator_exit_policy == "ALERT"
    assert StaticRuleParameters.from_dict({"indicator_exit_policy": "bad"}).indicator_exit_policy == "ALERT"
    migrated = StaticRuleParameters.from_dict({
        "normal_policy": "CLASSIC", "normal_dynamic_enabled": True,
        "normal_giveback_pct": 3, "normal_sell_pct": 33,
    })
    assert migrated.normal_dynamic_enabled is False
    assert migrated.normal_giveback_pct == pytest.approx(3.0)
    assert migrated.normal_sell_pct == pytest.approx(33.0)


def test_dynamic_atr_uses_completed_t_minus_one_daily_bars() -> None:
    bars = _atr_bars(130.0)
    assert average_true_range_pct(bars[:-1]) == pytest.approx(4.0)
    decision = StaticRule(StaticRuleParameters(
        normal_dynamic_enabled=True,
    )).evaluate(
        {"symbol": "FPT", "bars": bars, "confirmed_market_state": "UPTREND"},
        {"position": _position(current_price=103.0, peak_profit_pct=6.0)},
    )
    assert decision.details["normal_atr_pct"] == pytest.approx(4.0)
    assert decision.details["normal_activation_mfe_pct"] == pytest.approx(2.2)
    assert decision.details["normal_effective_trail_pct"] == pytest.approx(3.2)


def test_dynamic_start_and_trail_atr_multipliers_are_independent() -> None:
    waiting = protect_level(
        100, 3.0, 7, 2, dynamic_enabled=True, sl_price=97,
        atr_pct=4, atr_multiplier=0.6, atr_activation_multiplier=1.0,
    )
    assert waiting.activation_mfe_pct == pytest.approx(4.0)
    assert waiting.effective_trail_pct == pytest.approx(2.4)
    assert waiting.state == "WAIT"
    assert waiting.active is False

    active = protect_level(
        100, 4.0, 7, 2, dynamic_enabled=True, sl_price=97,
        atr_pct=4, atr_multiplier=0.6, atr_activation_multiplier=1.0,
    )
    assert active.activation_mfe_pct == pytest.approx(4.0)
    assert active.effective_trail_pct == pytest.approx(2.4)
    assert active.trigger_price == pytest.approx(101.504)
    assert active.state == "DYN"


def test_legacy_atr_multiplier_maps_to_both_start_and_trail() -> None:
    params = StaticRuleParameters.from_dict({"normal_atr_multiplier": 1.25})
    assert params.normal_atr_activation_multiplier == pytest.approx(1.25)
    assert params.normal_atr_multiplier == pytest.approx(1.25)


def test_dynamic_protect_floor_never_moves_down_when_atr_changes() -> None:
    level = protect_level(
        100, 5, 7, 2, dynamic_enabled=True, sl_price=97,
        atr_pct=6, atr_multiplier=0.6, previous_trigger_price=103,
    )
    assert level.trigger_price == pytest.approx(103)
    assert level.state == "DYN"


def test_dynamic_v3_retains_profit_only_inside_configured_mfe_band() -> None:
    retained = protect_level(
        100, 4, 7, 2, dynamic_enabled=True, sl_price=97,
        atr_pct=4, atr_multiplier=0.6,
        retention_pct=75, retention_until_pct=5,
    )
    assert retained.trigger_price == pytest.approx(103.0)
    assert retained.trigger_profit_pct == pytest.approx(3.0)

    released = protect_level(
        100, 5, 7, 2, dynamic_enabled=True, sl_price=97,
        atr_pct=4, atr_multiplier=0.6,
        retention_pct=75, retention_until_pct=5,
    )
    assert released.trigger_price == pytest.approx(102.48)


def test_dynamic_v3_settings_validate_the_retention_band() -> None:
    StaticRuleParameters(
        normal_arm_pct=7, normal_retention_pct=75,
        normal_retention_until_pct=5,
    ).validate()
    with pytest.raises(ValueError, match="giữ MFE"):
        StaticRuleParameters(normal_retention_pct=101).validate()
    with pytest.raises(ValueError, match="giữ MFE"):
        StaticRuleParameters(
            normal_arm_pct=7, normal_retention_pct=75,
            normal_retention_until_pct=8,
        ).validate()


def test_dynamic_subrule_switches_keep_old_settings_and_change_each_layer() -> None:
    legacy = StaticRuleParameters.from_dict({
        "normal_dynamic_enabled": True,
        "normal_atr_activation_multiplier": 0.6,
        "normal_atr_multiplier": 0.8,
        "normal_retention_pct": 87.5,
        "normal_retention_until_pct": 5,
    })
    assert all((
        legacy.normal_atr_activation_enabled,
        legacy.normal_atr_trail_enabled,
        legacy.normal_retention_enabled,
        legacy.normal_retention_until_enabled,
    ))
    assert legacy.to_dict()["normal_retention_until_enabled"] is True

    common = dict(
        dynamic_enabled=True, sl_price=97, atr_pct=4,
        atr_multiplier=0.8, atr_activation_multiplier=0.6,
        retention_pct=87.5, retention_until_pct=5,
    )
    waiting = protect_level(100, 1, 7, 2, **common)
    immediate = protect_level(
        100, 1, 7, 2, **common,
        atr_activation_enabled=False, atr_trail_enabled=False,
    )
    assert waiting.active is False
    assert immediate.active is True
    assert immediate.activation_mfe_pct == 0
    assert immediate.trigger_price == pytest.approx(100.875)

    atr_only = protect_level(
        100, 4, 7, 2, **common, retention_enabled=False,
    )
    retention_only = protect_level(
        100, 4, 7, 2, **common, atr_trail_enabled=False,
    )
    assert atr_only.trigger_price == pytest.approx(100.672)
    assert retention_only.trigger_price == pytest.approx(103.5)
    assert retention_only.effective_trail_pct == 0

    cutoff_at_five = protect_level(
        100, 6, 7, 2, **common, atr_trail_enabled=False,
        previous_trigger_price=104.2875,
    )
    continue_to_arm = protect_level(
        100, 6, 7, 2, **common, atr_trail_enabled=False,
        retention_until_enabled=False, previous_trigger_price=104.2875,
    )
    assert cutoff_at_five.trigger_price == pytest.approx(104.2875)
    assert continue_to_arm.trigger_price == pytest.approx(105.25)

    no_pre_arm_rule = protect_level(
        100, 4, 7, 2, **common,
        atr_trail_enabled=False, retention_enabled=False,
        previous_trigger_price=103.5,
    )
    after_arm = protect_level(
        100, 7, 7, 2, **common,
        atr_trail_enabled=False, retention_enabled=False,
    )
    assert no_pre_arm_rule.active is False
    assert no_pre_arm_rule.trigger_price == 0
    assert after_arm.trigger_price == pytest.approx(104.86)
    assert after_arm.state == "ARM"


def test_live_rule_can_use_retention_without_start_or_trail_atr() -> None:
    params = StaticRuleParameters(
        normal_dynamic_enabled=True,
        normal_atr_activation_enabled=False,
        normal_atr_trail_enabled=False,
        normal_retention_enabled=True,
        normal_retention_pct=75,
        normal_retention_until_enabled=False,
        normal_arm_pct=7,
    )
    context = {
        "symbol": "FPT", "bars": _atr_bars(102),
        "confirmed_market_state": "UPTREND",
    }
    position = {"position": _position(current_price=102, peak_profit_pct=4)}
    sold = StaticRule(params).evaluate(context, position)
    assert sold.reason == "NORMAL_PROTECTION"
    assert sold.details["normal_trigger_price"] == pytest.approx(103)
    assert sold.details["normal_atr_trail_enabled"] is False

    params.normal_retention_enabled = False
    waiting = StaticRule(params).evaluate(context, position)
    assert waiting.action == "WAIT"
    assert waiting.details["normal_state"] == "WAIT"


@pytest.mark.parametrize(
    ("mfe", "effective_trail", "trigger_price", "state", "active"),
    [
        (2.0, 2.4, 0.0, "WAIT", False),
        (2.5, 2.4, 100.04, "DYN", True),
        (4.0, 2.4, 101.504, "DYN", True),
        (6.0, 2.4, 103.456, "DYN", True),
        (7.0, 2.0, 104.86, "ARM", True),
        (10.0, 2.0, 107.8, "ARM", True),
    ],
)
def test_dynamic_formula_and_sl_floor(
    mfe: float, effective_trail: float, trigger_price: float, state: str, active: bool,
) -> None:
    level = protect_level(
        100.0, mfe, 7.0, 2.0, dynamic_enabled=True, sl_price=97.0,
        atr_pct=4.0, atr_multiplier=0.6,
    )
    assert level.effective_trail_pct == pytest.approx(effective_trail)
    assert level.trigger_price == pytest.approx(trigger_price)
    assert level.state == state
    assert level.active is active


def test_dynamic_off_waits_for_arm_but_dynamic_on_can_protect_below_arm() -> None:
    off = protect_level(100, 6, 7, 2, dynamic_enabled=False, sl_price=97)
    on = protect_level(
        100, 6, 7, 2, dynamic_enabled=True, sl_price=97,
        atr_pct=4, atr_multiplier=0.6,
    )
    assert off.state == "WAIT" and off.active is False
    assert on.state == "DYN" and on.active is True
    decision = StaticRule(StaticRuleParameters(
        normal_policy="AUTO", normal_dynamic_enabled=True,
        normal_atr_activation_multiplier=0.6, normal_atr_multiplier=0.6,
        normal_retention_pct=0,
    )).evaluate(
        {"symbol": "FPT", "bars": _atr_bars(103), "confirmed_market_state": "UPTREND"},
        {"position": _position(current_price=103, peak_profit_pct=6)},
    )
    assert decision.event == "PRICE_PROTECTION"
    assert decision.details["normal_state"] == "DYN"


def test_alert_uses_auto_condition_but_never_sells() -> None:
    rule = StaticRule(StaticRuleParameters(
        normal_policy="ALERT", normal_dynamic_enabled=True,
    ))
    decision = rule.evaluate(
        {"symbol": "FPT", "bars": _atr_bars(101.4), "confirmed_market_state": "UPTREND"},
        {"position": _position(
            current_price=101.4, peak_profit_pct=4, trade_id="T1",
        )},
    )
    assert decision.action == "WAIT"
    assert decision.event == "PROTECT_ALERT"
    assert decision.quantity_fraction == 0.0
    assert decision.details["normal_state"] == "ALERT"
    deduped = rule.evaluate(
        {"symbol": "FPT", "bars": _atr_bars(101.4), "confirmed_market_state": "UPTREND"},
        {"position": _position(
            current_price=101.4, peak_profit_pct=4, trade_id="T1",
            normal_alert_count=1, normal_last_alert_peak_pct=4,
        )},
    )
    assert deduped.action == "WAIT"
    assert deduped.event == ""
    assert deduped.details["normal_state"] == "ALERT"


def test_repeat_requires_a_new_peak_and_sell_100_disables_it() -> None:
    params = StaticRuleParameters(
        normal_sell_pct=50, normal_repeat_enabled=True, normal_giveback_pct=2,
    )
    rule = StaticRule(params)
    context = {"symbol": "FPT", "bars": _bars(107), "confirmed_market_state": "UPTREND"}
    base = _position(
        current_price=107, normal_armed=True, normal_protection_count=1,
        normal_last_trigger_peak_pct=7,
    )
    waiting = rule.evaluate(context, {"position": {**base, "peak_profit_pct": 9}})
    assert waiting.details["normal_state"] == "REARM"
    assert waiting.details["normal_rearm_mfe_pct"] == pytest.approx(protect_rearm_mfe(7, 2))
    repeated = rule.evaluate(context, {"position": {**base, "peak_profit_pct": 10}})
    assert repeated.action == "SELL"
    assert repeated.quantity_fraction == pytest.approx(0.5)

    full = StaticRule(StaticRuleParameters(
        normal_sell_pct=100, normal_repeat_enabled=True,
    )).evaluate(context, {"position": {**base, "peak_profit_pct": 10}})
    assert full.action == "WAIT"
    assert full.details["normal_state"] == "DONE"
    assert full.details["normal_repeat_enabled"] is False


def test_normal_auto_never_uses_new_high_and_low_from_the_same_bar() -> None:
    bars = [
        {"time": 1, "open": 106.0, "high": 107.5, "low": 104.0, "close": 107.0},
        {"time": 2, "open": 108.0, "high": 112.0, "low": 107.5, "close": 111.0},
        {"time": 3, "open": 110.5, "high": 110.5, "low": 109.0, "close": 109.5},
    ]
    observed = _normal_policy_fill(
        bars, policy="AUTO", entry_price=100.0, peak_profit_pct=0.0,
        already_armed=False, mfe_after_arm_pct=0.0,
        arm_pct=7.0, giveback_pct=2.0,
    )
    assert observed.armed is True
    assert observed.armed_at == 1
    assert observed.peak_profit_pct == pytest.approx(12.0)
    assert observed.protected_profit_pct == pytest.approx(9.76)
    assert observed.fill == pytest.approx(109.76)


def test_normal_auto_gap_fills_at_the_observed_open() -> None:
    observed = _normal_policy_fill(
        [{"time": 2, "open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0}],
        policy="AUTO", entry_price=100.0, peak_profit_pct=10.0,
        already_armed=True, mfe_after_arm_pct=10.0,
        arm_pct=7.0, giveback_pct=2.0,
    )
    assert observed.fill == pytest.approx(105.0)


def test_normal_arm_and_indicator_bucket_state_survive_restart(tmp_path) -> None:
    path = tmp_path / "rule.json"
    store = RuleStateStore(path)
    store.update_position_metrics("FPT", "T1", profit_pct=8.0)
    store.arm_normal("FPT", "T1")
    assert RuleStateStore(path).position_metrics("FPT", "T1")["normal_armed"] is True

    store.update_protect_metrics(
        "FPT", "T1", trigger_price=103.4, atr_pct=4.0, atr_multiplier=0.6,
        atr_activation_multiplier=1.0, retention_pct=75,
        retention_until_pct=5,
    )
    RuleStateStore(path).update_protect_metrics(
        "FPT", "T1", trigger_price=102.0, atr_pct=4.2, atr_multiplier=0.6,
        atr_activation_multiplier=1.1,
    )
    restarted_metrics = RuleStateStore(path).position_metrics("FPT", "T1")
    assert restarted_metrics["normal_trigger_price"] == pytest.approx(103.4)
    assert restarted_metrics["normal_atr_pct"] == pytest.approx(4.2)
    assert restarted_metrics["normal_atr_activation_multiplier"] == pytest.approx(1.1)
    assert restarted_metrics["normal_retention_pct"] == pytest.approx(75)
    assert restarted_metrics["normal_retention_until_pct"] == pytest.approx(5)

    base = {
        "buy_ema_fast_period": 3,
        "buy_ema_slow_period": 6,
        "sell_ema_fast_period": 3,
        "sell_ema_slow_period": 6,
        "rsi_period": 14,
        "sample_close": 9.0,
    }
    first = store.observe_indicator_bucket(
        "FPT", "REAL", "2026-09-08", "2M", 100, 10.0, base,
        lambda close: {**base, "sample_close": close},
    )
    assert first["advanced"] is False
    store.observe_indicator_bucket(
        "FPT", "REAL", "2026-09-08", "2M", 100, 11.0, base,
        lambda close: {**base, "sample_close": close},
    )
    advanced = RuleStateStore(path).observe_indicator_bucket(
        "FPT", "REAL", "2026-09-08", "2M", 101, 12.0, base,
        lambda close: {**base, "sample_close": close},
    )
    assert advanced["advanced"] is True
    assert advanced["current"]["sample_close"] == pytest.approx(11.0)
    assert advanced["previous"]["sample_close"] == pytest.approx(9.0)
    repeated = RuleStateStore(path).observe_indicator_bucket(
        "FPT", "REAL", "2026-09-08", "2M", 101, 13.0, base,
        lambda close: {**base, "sample_close": close},
    )
    assert repeated["advanced"] is False
    assert repeated["current"]["sample_close"] == pytest.approx(11.0)


def test_protect_fill_repeat_and_alert_dedupe_survive_restart(tmp_path) -> None:
    path = tmp_path / "rule.json"
    store = RuleStateStore(path)
    store.update_position_metrics("FPT", "T1", profit_pct=10.0)
    # MFE may advance while a T+2 request is pending.  REPEAT must still use
    # the peak captured by the original trigger.
    store.update_position_metrics("FPT", "T1", profit_pct=14.0)
    store.mark_protection_done(
        "FPT", "T1", ["NORMAL_PROTECTION"], trigger_peak_pct=10,
        rearm_mfe_pct=12.2, execution_id="ORDER-1",
    )
    # A partial-fill update from the same broker order cannot consume another
    # REPEAT turn.
    RuleStateStore(path).mark_protection_done(
        "FPT", "T1", ["NORMAL_PROTECTION"], trigger_peak_pct=10,
        rearm_mfe_pct=12.2, execution_id="ORDER-1",
    )
    first_alert = RuleStateStore(path).mark_protection_alert(
        "FPT", "T1", occurrence="FPT|T1|1", trigger_peak_pct=10,
        rearm_mfe_pct=12.2,
    )
    repeated_alert = RuleStateStore(path).mark_protection_alert(
        "FPT", "T1", occurrence="FPT|T1|1", trigger_peak_pct=10,
        rearm_mfe_pct=12.2,
    )
    metrics = RuleStateStore(path).position_metrics("FPT", "T1")
    assert metrics["normal_protection_count"] == 1
    assert metrics["normal_last_trigger_peak_pct"] == pytest.approx(10)
    assert metrics["normal_rearm_mfe_pct"] == pytest.approx(12.2)
    assert first_alert["normal_alert_count"] == repeated_alert["normal_alert_count"] == 1


def test_backtest_t2_recheck_uses_current_protect_rule_and_exact_exit_event() -> None:
    position = _Position(
        "T1", "FPT", 100, 100, 100.0, "2026-09-01", "2026-09-03",
        0.0, 10_000_000.0, ["NORMAL"], peak_profit_pct=4.0, sl_pct=-3.0,
    )
    protect = _Pending(
        "SELL", "FPT", "2026-09-01", "PRICE_PROTECTION",
        trade_id="T1", triggered_events=["NORMAL_PROTECTION"],
        settlement_waited=True, reason="NORMAL_PROTECTION",
    )
    params = StaticRuleParameters(
        normal_dynamic_enabled=True,
        normal_atr_activation_multiplier=0.6,
        normal_atr_multiplier=0.6,
        normal_retention_pct=0,
    )
    assert _pending_protect_matches(protect, position, params, 101.4, atr_pct=4.0) is True
    assert _pending_protect_matches(protect, position, params, 101.6, atr_pct=4.0) is False
    params.normal_policy = "ALERT"
    assert _pending_protect_matches(protect, position, params, 98.0) is False

    indicator = _Pending(
        "SELL", "FPT", "2026-09-01", "INDICATOR_EXIT",
        trade_id="T1", settlement_waited=True, reason="INDICATOR_EXIT",
    )
    assert _pending_matches_decision(
        indicator, StrategyDecision("SELL", "FPT", "INDICATOR_EXIT", event="INDICATOR_EXIT"),
    ) is True
    assert _pending_matches_decision(
        indicator, StrategyDecision("SELL", "FPT", "STOP_LOSS", event="STOP_LOSS"),
    ) is False


def test_dynamic_t2_reset_rechecks_pending_protect_from_sellable_open() -> None:
    params = StaticRuleParameters(
        normal_dynamic_enabled=True, normal_t2_reset_enabled=True,
        normal_atr_activation_multiplier=0.6, normal_atr_multiplier=0.8,
        normal_retention_pct=87.5, normal_retention_until_pct=5.0,
    )
    position = _Position(
        "T1", "FPT", 100, 100, 100.0, "2026-09-01", "2026-09-03",
        0.0, 10_000_000.0, ["NORMAL"], peak_profit_pct=4.0,
        normal_trigger_price=103.5, normal_t2_seen_unsellable=True, sl_pct=-4.0,
    )
    pending = _Pending(
        "SELL", "FPT", "2026-09-02", "PRICE_PROTECTION",
        trade_id="T1", triggered_events=["NORMAL_PROTECTION"],
        settlement_waited=True, reason="NORMAL_PROTECTION",
    )
    _start_sellable_dynamic(position, params, 102.0)
    assert position.normal_t2_reset_applied is True
    assert position.normal_trigger_price == 0.0
    assert position.normal_sellable_peak_profit_pct == pytest.approx(2.0)
    assert _pending_protect_matches(pending, position, params, 102.0, atr_pct=4.0) is False
    _start_sellable_dynamic(position, params, 101.0)
    assert position.normal_sellable_peak_profit_pct == pytest.approx(2.0)

    armed = _Position(
        "T2", "FPT", 100, 100, 100.0, "2026-09-01", "2026-09-03",
        0.0, 10_000_000.0, ["NORMAL"], peak_profit_pct=8.0,
        normal_armed=True, normal_trigger_price=105.0,
        normal_t2_seen_unsellable=True, sl_pct=-4.0,
    )
    _start_sellable_dynamic(armed, params, 102.0)
    assert armed.normal_t2_reset_applied is False
    assert armed.normal_trigger_price == pytest.approx(105.0)
    assert _pending_protect_matches(pending, armed, params, 102.0, atr_pct=4.0) is True


def test_live_dynamic_t2_reset_is_persisted_once_and_keeps_arm_protection(tmp_path) -> None:
    path = tmp_path / "rule.json"
    store = RuleStateStore(path)
    store.update_position_metrics(
        "FPT", "T1", profit_pct=4.0, t2_dynamic_enabled=True,
        sellable=False,
    )
    store.update_protect_metrics(
        "FPT", "T1", trigger_price=103.5, atr_pct=4.0,
        atr_multiplier=0.8,
    )
    store.update_position_metrics(
        "FPT", "T1", profit_pct=2.0, t2_dynamic_enabled=True,
        sellable=True,
    )
    restarted = RuleStateStore(path)
    first = restarted.position_metrics("FPT", "T1")
    assert first["peak_profit_pct"] == pytest.approx(4.0)
    assert first["normal_t2_reset_applied"] is True
    assert first["normal_trigger_price"] == 0.0
    assert first["normal_sellable_peak_profit_pct"] == pytest.approx(2.0)
    restarted.update_protect_metrics(
        "FPT", "T1", trigger_price=101.8, atr_pct=4.0,
        atr_multiplier=0.8,
    )
    restarted.update_position_metrics(
        "FPT", "T1", profit_pct=3.0, t2_dynamic_enabled=True,
        sellable=True,
    )
    second = RuleStateStore(path).position_metrics("FPT", "T1")
    assert second["normal_trigger_price"] == pytest.approx(101.8)
    assert second["normal_sellable_peak_profit_pct"] == pytest.approx(3.0)

    store.update_position_metrics(
        "FPT", "T2", profit_pct=8.0, t2_dynamic_enabled=True,
        sellable=False,
    )
    store.update_protect_metrics(
        "FPT", "T2", trigger_price=105.0, atr_pct=4.0,
        atr_multiplier=0.8,
    )
    store.update_position_metrics(
        "FPT", "T2", profit_pct=2.0, t2_dynamic_enabled=True,
        sellable=True,
    )
    armed = RuleStateStore(path).position_metrics("FPT", "T2")
    assert armed.get("normal_t2_reset_applied", False) is False
    assert armed["normal_trigger_price"] == pytest.approx(105.0)


def test_live_rule_uses_sellable_peak_below_arm_after_t2_reset() -> None:
    params = StaticRuleParameters(
        normal_dynamic_enabled=True, normal_t2_reset_enabled=True,
        normal_atr_activation_multiplier=0.6, normal_atr_multiplier=0.8,
        normal_retention_pct=87.5, normal_retention_until_pct=5.0,
    )
    context = {
        "symbol": "FPT", "bars": _atr_bars(102.0),
        "confirmed_market_state": "UPTREND",
    }
    before = StaticRule(params).evaluate(context, {"position": _position(
        current_price=102.0, peak_profit_pct=4.0,
        normal_trigger_price=103.5,
    )})
    after = StaticRule(params).evaluate(context, {"position": _position(
        current_price=102.0, peak_profit_pct=4.0,
        normal_trigger_price=0.0, normal_t2_reset_applied=True,
        normal_sellable_peak_profit_pct=2.0,
    )})
    assert before.reason == "NORMAL_PROTECTION"
    assert after.action == "WAIT"
    assert after.details["normal_mfe_pct"] == pytest.approx(2.0)


def test_realtime_indicator_cadence_never_delays_sl_or_tp() -> None:
    params = StaticRuleParameters(take_profit_pct=7.0)
    rule = StaticRule(params)
    snapshot = {
        "buy_ema_fast": 100.0,
        "buy_ema_slow": 100.0,
        "sell_ema_fast": 100.0,
        "sell_ema_slow": 100.0,
        "rsi": 50.0,
        "rsi_previous": 50.0,
        "sample_count": 30,
        "buy_ema_slow_period": 6,
        "sell_ema_slow_period": 6,
        "rsi_period": 14,
    }
    context = {
        "symbol": "FPT",
        "bars": _bars(),
        "confirmed_market_state": "UPTREND",
        "signal_mode": "REALTIME",
        "indicator_interval": "5M",
        "indicator_snapshot": snapshot,
        "previous_indicators": snapshot,
    }

    stop = rule.evaluate(
        context,
        {"position": _position(current_price=96.0, em_modes=["IND_EXIT"])},
    )
    target = rule.evaluate(
        context,
        {"position": _position(current_price=107.0, em_modes=["TP"])},
    )

    assert stop.reason == "STOP_LOSS"
    assert target.reason == "TAKE_PROFIT"


def test_realtime_bucket_and_setting_normalization() -> None:
    at = datetime(2026, 9, 8, 9, 15, 59, tzinfo=VN_TZ)
    assert realtime_indicator_bucket(at, "1M") != realtime_indicator_bucket(
        at.replace(minute=16, second=0), "1M",
    )
    assert realtime_indicator_bucket(at, "2M") == realtime_indicator_bucket(
        at.replace(minute=14, second=0), "2M",
    )
    assert AppSettings.from_dict({"realtime_indicator_interval": "2m"}).realtime_indicator_interval == "2M"
    assert AppSettings.from_dict({"realtime_indicator_interval": "bad"}).realtime_indicator_interval == "TICK"


def test_exit_comparison_builds_all_four_policy_and_dynamic_modes() -> None:
    scenario = BacktestScenario(
        "CTS", ["CTS"], "2026-03-14", "2026-08-20", "ACCUMULATION",
    )
    variants = exit_comparison_variants(
        scenario, StaticRuleParameters(normal_arm_pct=7, take_profit_pct=7).to_dict(),
    )
    assert [item.em_modes for item, _params in variants] == [
        ["NORMAL", "IND_EXIT"], ["NORMAL", "IND_EXIT"],
        ["NORMAL", "IND_EXIT"], ["NORMAL", "IND_EXIT"],
    ]
    assert [params["normal_policy"] for _item, params in variants] == [
        "AUTO", "AUTO", "ALERT", "ALERT",
    ]
    assert [params["normal_dynamic_enabled"] for _item, params in variants] == [
        False, True, False, True,
    ]
    assert [item.name.rsplit(" · ", 1)[-1] for item, _params in variants] == [
        "E + PROTECT DYNAMIC OFF", "E + PROTECT DYNAMIC ON",
        "E + PROTECT ALERT DYNAMIC OFF", "E + PROTECT ALERT DYNAMIC ON",
    ]


def _csv(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "time,open,high,low,close,Volume\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_cts_one_minute_can_be_replayed_as_two_minute_buckets(tmp_path) -> None:
    source = _csv(tmp_path / "HOSE_DLY_CTS, 1.csv", [
        "2026-08-20T02:15:00Z,10,11,9,10.5,100",
        "2026-08-20T02:16:00Z,10.5,12,10,11.5,200",
        "2026-08-20T07:45:00Z,11.5,12,11,11,300",
    ])
    store = ReplayDataStore(tmp_path / "replay")
    store.import_file(source, price_scale=1)

    rows, resolution, quality = store.load_day("CTS", "2026-08-20", resolution="2")

    assert resolution == "2"
    assert quality == "FULL_AGGREGATED_1_TO_2"
    assert len(rows) == 3
    assert rows[0]["open"] == pytest.approx(10.0)
    assert rows[-1]["volume"] == pytest.approx(300.0)
