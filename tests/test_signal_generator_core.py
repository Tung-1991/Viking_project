# -*- coding: utf-8 -*-
import copy

import pandas as pd
import numpy as np

import config
from core.storage_manager import brain_strategy_fingerprint
from core.data_engine import data_engine
from signals.signal_generator import SignalGenerator


def _ohlcv(rows=250):
    close = [100.0 + i * 0.05 for i in range(rows)]
    return pd.DataFrame(
        {
            "open": [value - 0.1 for value in close],
            "high": [value + 0.3 for value in close],
            "low": [value - 0.3 for value in close],
            "close": close,
            "volume": [1000.0 + i for i in range(rows)],
        }
    )


def _vn30_trend_frame(direction=1, rows=120, flat=False):
    index = np.arange(rows, dtype=float)
    if flat:
        close = np.full(rows, 100.0)
    else:
        close = 100.0 + direction * index * 0.25 + np.sin(index / 4.0) * 0.03
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + 0.12,
            "low": np.minimum(open_, close) - 0.12,
            "close": close,
            "volume": 10_000.0 + index * 10.0,
        }
    )


def _vn30_pipeline_settings():
    return {
        "MASTER_EVAL_MODE": "VETO",
        "MIN_MATCHING_VOTES": 3,
        "FORCE_ANY_MODE": False,
        "G0_TIMEFRAME": "1h",
        "G1_TIMEFRAME": "15m",
        "G2_TIMEFRAME": "5m",
        "G3_TIMEFRAME": "1m",
        "voting_rules": {
            "G0": {"max_opposite": 0, "max_none": 1, "master_rule": "PASS"},
            "G1": {"max_opposite": 0, "max_none": 2, "master_rule": "FIX"},
            "G2": {"max_opposite": 0, "max_none": 1, "master_rule": "PASS"},
            "G3": {"max_opposite": 0, "max_none": 1, "master_rule": "IGNORE"},
        },
        "indicators": {
            "adx": {
                "active": True,
                "groups": ["G0", "G1"],
                "macro_role": "BREAKOUT",
                "active_modes": ["ANY"],
                "params": {"period": 14, "strong": 20},
                "trigger_mode": "REALTIME_TICK",
            },
            "ema": {
                "active": True,
                "groups": ["G0", "G1"],
                "is_trend": True,
                "macro_role": "BASE",
                "active_modes": ["ANY"],
                "params": {"period": 20},
                "trigger_mode": "REALTIME_TICK",
            },
            "supertrend": {
                "active": True,
                "groups": ["G0", "G1"],
                "is_trend": True,
                "macro_role": "NONE",
                "active_modes": ["ANY"],
                "params": {"period": 10, "multiplier": 2.0},
                "trigger_mode": "REALTIME_TICK",
            },
            "macd": {
                "active": True,
                "groups": ["G1"],
                "macro_role": "NONE",
                "active_modes": ["ANY"],
                "params": {"fast": 12, "slow": 26, "signal": 9},
                "trigger_mode": "REALTIME_TICK",
            },
            "simple_breakout": {
                "active": True,
                "groups": ["G2"],
                "is_trend": True,
                "macro_role": "BREAKOUT",
                "active_modes": ["ANY"],
                "params": {"lookback": 2, "atr_buffer": 0.0},
                "trigger_mode": "REALTIME_TICK",
            },
        },
    }


def _run_vn30_pipeline(monkeypatch, direction=1, flat=False):
    generator = SignalGenerator()
    settings = _vn30_pipeline_settings()
    monkeypatch.setattr(generator, "_get_brain_settings", lambda _symbol=None: settings)
    frames = {}
    for group in ("G0", "G1", "G2", "G3"):
        indicators = data_engine._effective_group_indicators(
            settings["indicators"],
            group,
            include_trend=True,
        )
        frames[group] = data_engine._apply_ta(
            _vn30_trend_frame(direction=direction, flat=flat),
            indicators,
            {},
        )
    context = {"atr_G2": 1.0}
    signal = generator.generate_signal_v4(frames, context, symbol="VN30F1M")
    return signal, context


def _run_ckcs_pipeline(monkeypatch, direction=1, flat=False):
    generator = SignalGenerator()
    settings = copy.deepcopy(config.SANDBOX_CONFIG)
    settings.update(
        {
            "MASTER_EVAL_MODE": "VETO",
            "MIN_MATCHING_VOTES": 3,
            "FORCE_ANY_MODE": False,
            "G0_TIMEFRAME": "1d",
            "G1_TIMEFRAME": "1h",
            "G2_TIMEFRAME": "15m",
            "G3_TIMEFRAME": "15m",
        }
    )
    monkeypatch.setattr(generator, "_get_brain_settings", lambda _symbol=None: settings)
    frames = {}
    for group in ("G0", "G1", "G2", "G3"):
        indicators = data_engine._effective_group_indicators(
            settings["indicators"],
            group,
            include_trend=True,
        )
        frames[group] = data_engine._apply_ta(
            _vn30_trend_frame(direction=direction, flat=flat),
            indicators,
            {},
        )
    context = {"atr_G2": 1.0}
    signal = generator.generate_signal_v4(frames, context, symbol="FPT")
    return signal, context


def test_vn30_pipeline_emits_buy_on_clear_uptrend(monkeypatch):
    signal, context = _run_vn30_pipeline(monkeypatch, direction=1)
    assert signal == 1
    assert context["group_signals"] == {"G0": 1, "G1": 1, "G2": 1, "G3": 0}


def test_vn30_pipeline_emits_sell_on_clear_downtrend(monkeypatch):
    signal, context = _run_vn30_pipeline(monkeypatch, direction=-1)
    assert signal == -1
    assert context["group_signals"] == {"G0": -1, "G1": -1, "G2": -1, "G3": 0}


def test_vn30_pipeline_waits_when_market_is_flat(monkeypatch):
    signal, context = _run_vn30_pipeline(monkeypatch, flat=True)
    assert signal == 0
    assert context["group_signals"] == {"G0": 0, "G1": 0, "G2": 0, "G3": 0}


def test_ckcs_pipeline_emits_buy_on_clear_uptrend(monkeypatch):
    signal, context = _run_ckcs_pipeline(monkeypatch, direction=1)
    assert signal == 1
    assert context["group_signals"] == {"G0": 1, "G1": 1, "G2": 1, "G3": 0}


def test_ckcs_pipeline_emits_sell_on_clear_downtrend(monkeypatch):
    signal, context = _run_ckcs_pipeline(monkeypatch, direction=-1)
    assert signal == -1
    assert context["group_signals"] == {"G0": -1, "G1": -1, "G2": -1, "G3": 0}


def test_ckcs_pipeline_waits_when_market_is_flat(monkeypatch):
    signal, context = _run_ckcs_pipeline(monkeypatch, flat=True)
    assert signal == 0
    assert context["group_signals"]["G0"] == 0
    assert context["block_reason"] == "Blocked by G0 (FIX rule)"


def test_all_registered_indicators_accept_standard_ohlcv():
    generator = SignalGenerator()
    expected = {
        "rsi", "macd", "bollinger_bands", "ema", "ema_cross", "stochastic",
        "atr", "adx", "supertrend", "psar", "volume", "multi_candle", "candle",
        "swing_point", "fibonacci", "pivot_points", "simple_breakout",
    }
    assert set(generator.indicator_map) == expected

    frame = _ohlcv()
    context_indicators = {"fibonacci", "pivot_points", "swing_point", "simple_breakout"}
    for name, func in generator.indicator_map.items():
        if name in context_indicators:
            result = func(frame.copy(), {}, {"atr_G2": 1.0, "symbol": "FPT"})
        else:
            result = func(frame.copy(), {})
        assert result in (-1, 0, 1), name


def test_default_ckcs_groups_separate_trend_confirmation_and_timing():
    sandbox = config.SANDBOX_CONFIG
    rules = sandbox["voting_rules"]
    indicators = sandbox["indicators"]

    assert rules["G0"] == {
        "max_opposite": 0,
        "max_none": 0,
        "master_rule": "FIX",
    }
    assert rules["G1"] == {
        "max_opposite": 0,
        "max_none": 1,
        "master_rule": "PASS",
    }
    assert rules["G2"] == {
        "max_opposite": 0,
        "max_none": 2,
        "master_rule": "PASS",
    }

    active_by_group = {
        group: {
            name
            for name, cfg in indicators.items()
            if cfg.get("active") and group in cfg.get("groups", [])
        }
        for group in ("G0", "G1", "G2", "G3")
    }
    assert active_by_group == {
        "G0": {"ema", "supertrend"},
        "G1": {"ema", "adx", "macd"},
        "G2": {"swing_point", "volume", "simple_breakout"},
        "G3": set(),
    }
    assert indicators["ema"]["params"]["period"] == 50
    assert indicators["ema"]["group_params"]["G1"]["period"] == 20
    assert all(
        indicators[name]["trigger_mode"] == "STRICT_CLOSE"
        for name in active_by_group["G0"] | active_by_group["G1"] | active_by_group["G2"]
    )


def test_group_evaluation_uses_closed_bar_and_group_params():
    generator = SignalGenerator()
    observed = {}

    def indicator(frame, params):
        observed["rows"] = len(frame)
        observed["params"] = params
        return 1

    generator.indicator_map = {"probe": indicator}
    context = {}
    result = generator._evaluate_group(
        "G2",
        {
            "probe": {
                "params": {"period": 10},
                "group_params": {"G2": {"period": 21}},
                "trigger_mode": "STRICT_CLOSE",
            }
        },
        _ohlcv(5),
        context,
        "ANY",
        {"max_opposite": 0, "max_none": 0},
    )

    assert result == 1
    assert observed == {"rows": 4, "params": {"period": 21}}
    assert context["group_details"]["G2"]["status"] == 1


def test_generate_signal_veto_pipeline(monkeypatch):
    generator = SignalGenerator()
    generator.indicator_map = {"probe": lambda _frame, _params: 1}
    settings = {
        "FORCE_ANY_MODE": True,
        "MASTER_EVAL_MODE": "VETO",
        "MIN_MATCHING_VOTES": 1,
        "indicators": {
            "probe": {"active": True, "groups": ["G2"], "active_modes": ["ANY"], "params": {}}
        },
        "voting_rules": {
            "G0": {"master_rule": "IGNORE"},
            "G1": {"master_rule": "IGNORE"},
            "G2": {"master_rule": "FIX", "max_opposite": 0, "max_none": 0},
            "G3": {"master_rule": "IGNORE"},
        },
    }
    monkeypatch.setattr(generator, "_get_brain_settings", lambda _symbol=None: settings)
    context = {}

    result = generator.generate_signal_v4({"G2": _ohlcv(20)}, context, symbol="FPT")

    assert result == 1
    assert context["market_mode"] == "ANY"
    assert context["group_signals"]["G2"] == 1
    assert context["block_reason"] == "OK / Ready"
    assert context["strategy_fingerprint"] == brain_strategy_fingerprint(settings)


def test_generate_signal_keeps_details_for_all_groups_before_fix_veto(monkeypatch):
    generator = SignalGenerator()
    generator.indicator_map = {
        "g0_probe": lambda *_args: -1,
        "g1_probe": lambda *_args: 0,
        "simple_breakout": lambda *_args: 1,
    }
    settings = {
        "FORCE_ANY_MODE": True,
        "MASTER_EVAL_MODE": "VETO",
        "MIN_MATCHING_VOTES": 1,
        "indicators": {
            "g0_probe": {
                "active": True,
                "groups": ["G0"],
                "active_modes": ["ANY"],
                "params": {},
            },
            "g1_probe": {
                "active": True,
                "groups": ["G1"],
                "active_modes": ["ANY"],
                "params": {},
            },
            "simple_breakout": {
                "active": True,
                "groups": ["G2"],
                "active_modes": ["ANY"],
                "params": {},
            },
        },
        "voting_rules": {
            "G0": {"master_rule": "PASS", "max_opposite": 0, "max_none": 0},
            "G1": {"master_rule": "FIX", "max_opposite": 0, "max_none": 0},
            "G2": {"master_rule": "PASS", "max_opposite": 0, "max_none": 0},
            "G3": {"master_rule": "IGNORE", "max_opposite": 0, "max_none": 0},
        },
    }
    monkeypatch.setattr(generator, "_get_brain_settings", lambda _symbol=None: settings)
    frames = {group: _ohlcv(20) for group in ("G0", "G1", "G2", "G3")}
    context = {}

    assert generator.generate_signal_v4(frames, context, symbol="VN30F1M") == 0
    assert len(context["group_details"]["G0"]["inds"]) == 1
    assert len(context["group_details"]["G1"]["inds"]) == 1
    assert context["group_details"]["G2"]["inds"] == [
        "● [BUY]  SIMPLE BREAKOUT [NONE|ANY]"
    ]


def test_generate_signal_voting_requires_configured_group_count(monkeypatch):
    generator = SignalGenerator()
    generator.indicator_map = {"probe": lambda _frame, _params: 1}
    rules = {
        group: {"master_rule": "PASS", "max_opposite": 0, "max_none": 0}
        for group in ("G0", "G1", "G2", "G3")
    }
    settings = {
        "FORCE_ANY_MODE": True,
        "MASTER_EVAL_MODE": "VOTING",
        "MIN_MATCHING_VOTES": 3,
        "indicators": {
            "probe": {
                "active": True,
                "groups": ["G1", "G2", "G3"],
                "active_modes": ["ANY"],
                "params": {},
            }
        },
        "voting_rules": rules,
    }
    monkeypatch.setattr(generator, "_get_brain_settings", lambda _symbol=None: settings)
    frames = {group: _ohlcv(20) for group in ("G1", "G2", "G3")}

    assert generator.generate_signal_v4(frames, {}, symbol="FPT") == 1


def test_inactive_trend_indicator_never_votes():
    generator = SignalGenerator()
    generator.indicator_map = {"trend_probe": lambda _frame, _params: -1}
    context = {}
    trends = generator._detect_dynamic_trend(
        {"G0": _ohlcv(20)},
        context,
        {
            "trend_probe": {
                "active": False,
                "is_trend": True,
                "groups": ["G0"],
                "params": {},
            }
        },
    )

    assert trends["G0"] == "NONE"
