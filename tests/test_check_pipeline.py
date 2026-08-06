# -*- coding: utf-8 -*-
import copy
from datetime import datetime, timedelta

import pandas as pd

import config
from ai_advisor import scan_cache, scan_report
from core.data_engine import DataEngine
from signals import check_engine
from signals.signal_generator import SignalGenerator


def _frame(rows=80):
    close = [100.0 + index * 0.1 for index in range(rows)]
    return pd.DataFrame({
        "time": pd.date_range("2026-04-01", periods=rows, freq="D"),
        "open": [value - 0.1 for value in close],
        "high": [value + 0.3 for value in close],
        "low": [value - 0.3 for value in close],
        "close": close,
        "volume": [1000 + index for index in range(rows)],
    })


def test_trade_and_check_same_indicator_same_params_computed_once(monkeypatch):
    engine = DataEngine()
    calls = []

    def fake_apply(df, indicators, _tsl=None):
        name, cfg = next(iter(indicators.items()))
        calls.append((name, cfg["params"]["period"]))
        df[f"RSI_{cfg['params']['period']}"] = 50.0
        return df

    monkeypatch.setattr(engine, "_apply_ta", fake_apply)
    cfg = {"rsi": {"active": True, "params": {"period": 14}}}
    result = engine._apply_trade_and_check_ta(_frame(), cfg, copy.deepcopy(cfg))
    assert calls == [("rsi", 14)]
    assert result.attrs["check_indicator_columns"]["rsi"] == ["RSI_14"]


def test_trade_and_check_different_params_are_kept_separate(monkeypatch):
    engine = DataEngine()
    calls = []

    def fake_apply(df, indicators, _tsl=None):
        name, cfg = next(iter(indicators.items()))
        period = cfg["params"]["period"]
        calls.append((name, period))
        df[f"RSI_{period}"] = float(period)
        return df

    monkeypatch.setattr(engine, "_apply_ta", fake_apply)
    trade = {"rsi": {"active": True, "params": {"period": 14}}}
    check = {"rsi": {"active": True, "params": {"period": 21}}}
    result = engine._apply_trade_and_check_ta(_frame(), trade, check)
    assert calls == [("rsi", 14), ("rsi", 21)]
    assert result.attrs["check_indicator_columns"]["rsi"] == ["RSI_21"]


def test_trade_rsi_result_is_identical_when_check_macd_bb_are_enabled():
    from signals.rsi import get_signal_vector as rsi_signal

    engine = DataEngine()
    trade = {"rsi": {"active": True, "params": {"period": 14, "upper": 70, "lower": 30}}}
    check = {
        "macd": {"active": True, "params": {"fast": 12, "slow": 26, "signal": 9}},
        "bollinger_bands": {"active": True, "params": {"period": 20, "std_dev": 2.0}},
    }
    trade_only = engine._apply_ta(_frame().copy(), trade)
    combined = engine._apply_trade_and_check_ta(_frame().copy(), trade, check)
    assert trade_only["RSI_14"].equals(combined["RSI_14"])
    assert rsi_signal(trade_only, trade["rsi"]["params"]) == rsi_signal(combined, trade["rsi"]["params"])


def test_check_only_returns_enabled_check_modules_and_does_not_mutate_trade():
    frame = _frame()
    frame["RSI_14"] = 75.0
    frame["MACD_12_26_9"] = 0.5
    frame["MACDh_12_26_9"] = 0.1
    frame["MACDs_12_26_9"] = 0.4
    frame["BBL_20_2.0"] = 95.0
    frame["BBU_20_2.0"] = 120.0
    settings = {
        "indicators": {"rsi": {"active": True, "groups": ["G2"], "params": {"period": 14}}},
        "check_indicators": {
            "rsi": {"active": False, "groups": ["G2"], "params": {"period": 14}},
            "macd": {"active": True, "groups": ["G2"], "params": {"fast": 12, "slow": 26, "signal": 9}},
            "bollinger_bands": {"active": True, "groups": ["G2"], "params": {"period": 20, "std_dev": 2.0}},
        },
    }
    context = {"symbol": "FPT", "check_indicator_columns": {"G2": {
        "macd": ["MACD_12_26_9", "MACDh_12_26_9", "MACDs_12_26_9"],
        "bollinger_bands": ["BBL_20_2.0", "BBU_20_2.0"],
    }}}
    before_settings, before_context = copy.deepcopy(settings), copy.deepcopy(context)
    result = check_engine.evaluate({"G2": frame}, context, "FPT", settings=settings)
    assert set(result["groups"]["G2"]) == {"macd", "bollinger_bands"}
    assert settings == before_settings
    assert context == before_context


def test_check_volume_exports_compact_numeric_metrics():
    frame = _frame()
    settings = {
        "check_indicators": {
            "volume": {
                "active": True,
                "groups": ["G0"],
                "params": {"period": 20, "multiplier": 1.1},
            }
        }
    }
    result = check_engine.evaluate({"G0": frame}, {}, "FPT", settings=settings)
    metrics = result["groups"]["G0"]["volume"]["metrics"]
    assert metrics["volume"] == float(frame["volume"].iloc[-1])
    assert metrics["volume_sma_20"] == float(frame["volume"].tail(20).mean())
    assert metrics["volume_ratio_20"] > 0
    assert metrics["candle_direction"] == "UP"


def test_check_evaluation_cannot_change_trade_signal(monkeypatch):
    generator = SignalGenerator()
    generator.indicator_map = {"probe": lambda _df, _params: 1}
    settings = {
        "FORCE_ANY_MODE": True, "MASTER_EVAL_MODE": "VETO", "MIN_MATCHING_VOTES": 1,
        "indicators": {"probe": {"active": True, "groups": ["G2"], "active_modes": ["ANY"], "params": {}}},
        "check_indicators": {"macd": {"active": True, "groups": ["G2"], "params": {"fast": 12, "slow": 26, "signal": 9}}},
        "voting_rules": {group: {"master_rule": "FIX" if group == "G2" else "IGNORE", "max_opposite": 0, "max_none": 0}
                         for group in ("G0", "G1", "G2", "G3")},
    }
    monkeypatch.setattr(generator, "_get_brain_settings", lambda _symbol=None: settings)
    frame = _frame()
    before = generator.generate_signal_v4({"G2": frame.copy()}, {}, symbol="FPT")
    check_engine.evaluate({"G2": frame.copy()}, {"symbol": "FPT"}, "FPT", settings=settings)
    after = generator.generate_signal_v4({"G2": frame.copy()}, {}, symbol="FPT")
    assert before == after == 1


def _snapshot(config_id, value, state="UP"):
    return {
        "price": {"open": 10, "high": 11, "low": 9, "close": 10.5, "current": 10.5},
        "volume": {"today": 1000}, "daily_group": "G0",
        "bot": {"latest_signal": 1, "market_mode": "ANY"},
        "check": {"config_id": config_id, "groups": {"G2": {"custom_module": {
            "params": {"period": 7}, "signal": 1,
            "metrics": {"score": value, "state": state},
        }}}},
    }


def test_many_scans_stay_one_daily_record_and_aggregate_dynamic_metrics():
    cache = scan_cache.empty_cache()
    now = datetime(2026, 7, 17, 9, 15)
    for index, value in enumerate((2.0, 5.0, 3.0)):
        scan_cache.merge_sample(cache, "FPT", _snapshot("cfg-a", value, "UP" if index < 2 else "DOWN"),
                                now + timedelta(minutes=15 * index))
    days = cache["symbols"]["FPT"]["days"]
    assert len(days) == 1
    entry = next(iter(days.values()))
    assert entry["samples"] == 3
    assert entry["bot_signal_counts"] == {"BUY": 3}
    metric = entry["check_segments"][0]["groups"]["G2"]["custom_module"]["metrics"]["score"]
    assert (metric["first"], metric["min"], metric["max"], metric["avg"], metric["last"]) == (2, 2, 5, 3.333333, 3)
    state = entry["check_segments"][0]["groups"]["G2"]["custom_module"]["metrics"]["state"]
    assert state["changes"] == 1 and state["first"] == "UP" and state["last"] == "DOWN"


def test_check_config_change_creates_new_segment_and_report_is_dynamic():
    cache = scan_cache.empty_cache()
    now = datetime(2026, 7, 17, 9, 15)
    scan_cache.merge_sample(cache, "FPT", _snapshot("cfg-a", 2), now)
    scan_cache.merge_sample(cache, "FPT", _snapshot("cfg-b", 8), now + timedelta(minutes=15))
    entry = next(iter(cache["symbols"]["FPT"]["days"].values()))
    assert [segment["config_id"] for segment in entry["check_segments"]] == ["cfg-a", "cfg-b"]
    report = scan_report.render_full_report(cache, report_days=1)
    assert "custom_module" in report and "score" in report
    assert "`G2.rsi`" not in report and "`G2.macd`" not in report


def test_fetch_data_keeps_one_market_fetch_per_group_with_check(monkeypatch):
    engine = DataEngine()
    calls = []
    settings = {
        "G0_TIMEFRAME": "1d", "G1_TIMEFRAME": "1h", "G2_TIMEFRAME": "15m", "G3_TIMEFRAME": "5m",
        "NUM_H1_BARS": 50, "TSL_CONFIG": {},
        "indicators": {"rsi": {"active": True, "groups": ["G2"], "params": {"period": 14}}},
        "check_indicators": {"macd": {"active": True, "groups": ["G2"], "params": {"fast": 12, "slow": 26, "signal": 9}}},
    }
    monkeypatch.setattr(engine, "_get_brain_settings", lambda _symbol=None: settings)

    def fake_fetch(symbol, timeframe, bars, trade, tsl=None, check_config=None):
        calls.append((symbol, timeframe, bool(trade), bool(check_config)))
        return _frame(bars)

    monkeypatch.setattr(engine, "_fetch_bars", fake_fetch)
    frames, _context = engine.fetch_data_v4("FPT")
    assert frames is not None and len(calls) == 4
    assert sum(call[3] for call in calls) == 1


def test_ai_default_is_plain_gpt_56_medium():
    from ai_advisor import api_client

    assert config.AI_ADVISOR_PROVIDERS["openai"]["default_model"] == "gpt-5.6"
    assert api_client.DEFAULT_MODEL == "gpt-5.6"
    assert api_client.DEFAULT_API_SETTINGS["reasoning_effort"] == "medium"
    assert api_client.normalize_reasoning_effort("none") == "none"
    assert api_client.normalize_reasoning_effort("max") == "max"
