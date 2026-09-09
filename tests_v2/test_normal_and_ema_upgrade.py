from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from viking_v2.backtest.engine import (
    _normal_policy_fill,
    exit_comparison_variants,
)
from viking_v2.backtest.models import BacktestScenario
from viking_v2.backtest.replay import ReplayDataStore
from viking_v2.config import AppSettings
from viking_v2.rules.business import StaticRule, StaticRuleParameters
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


def test_normal_classic_remains_the_default_policy() -> None:
    params = StaticRuleParameters()
    assert params.normal_policy == "CLASSIC"
    assert params.normal_giveback_pct == pytest.approx(3.0)
    assert params.normal_sell_pct == pytest.approx(33.0)
    decision = StaticRule(params).evaluate(
        {"symbol": "FPT", "bars": _bars(103.5), "confirmed_market_state": "UPTREND"},
        {"position": _position(current_price=103.5, peak_profit_pct=7.2)},
    )
    assert decision.reason == "NORMAL_PROTECTION"


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
    assert armed.details["normal_protected_profit_pct"] == pytest.approx(5.0)

    sold = rule.evaluate(
        context,
        {"position": _position(current_price=107.0, peak_profit_pct=9.0, normal_armed=True)},
    )
    assert sold.action == "SELL"
    assert sold.reason == "NORMAL_PROTECTION"
    assert sold.details["normal_protected_profit_pct"] == pytest.approx(7.0)
    assert sold.quantity_fraction == pytest.approx(0.5)


def test_removed_normal_policy_falls_back_to_classic() -> None:
    params = StaticRuleParameters.from_dict({"normal_policy": "REMOVED"})
    assert params.normal_policy == "CLASSIC"


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
    assert observed.protected_profit_pct == pytest.approx(10.0)
    assert observed.fill == pytest.approx(110.0)


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


def test_exit_comparison_builds_two_independent_protect_policies() -> None:
    scenario = BacktestScenario(
        "CTS", ["CTS"], "2026-03-14", "2026-08-20", "ACCUMULATION",
    )
    variants = exit_comparison_variants(
        scenario, StaticRuleParameters(normal_arm_pct=7, take_profit_pct=7).to_dict(),
    )
    assert [item.em_modes for item, _params in variants] == [
        ["NORMAL", "IND_EXIT"], ["NORMAL", "IND_EXIT"],
    ]
    assert [params["normal_policy"] for _item, params in variants] == [
        "CLASSIC", "AUTO",
    ]
    assert [item.name.rsplit(" · ", 1)[-1] for item, _params in variants] == [
        "E + PROTECT CLASSIC", "E + PROTECT AUTO",
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
