# -*- coding: utf-8 -*-
import pytest

from core.entry_exit_engine import evaluate_entry_exit, format_decision


def _cfg(tactic, **overrides):
    cfg = {
        "enabled": True,
        "active_tactics": [tactic],
        "entry_tactics": [tactic],
        "sl_mode": "AUTO",
        "exit_tactic": "AUTO",
    }
    cfg.update(overrides)
    return cfg


def test_entry_exit_disabled_is_off():
    decision = evaluate_entry_exit("FPT", "BUY", 100.0, {}, {}, None)
    assert decision["status"] == "OFF"
    assert format_decision(decision) == "E/E: OFF"


def test_fallback_r_is_ready_without_fabricating_sandbox_sl():
    decision = evaluate_entry_exit("FPT", "BUY", 100.0, {}, _cfg("FALLBACK_R"), None)
    assert decision["status"] == "READY"
    assert decision["entry_tactic"] == "FALLBACK_R"
    assert decision["sl"] is None
    assert decision["sl_source"] == "SANDBOX"


def test_swing_rejection_builds_buy_zone_sl_and_tp():
    context = {"swing_high_G2": 110.0, "swing_low_G2": 100.0, "atr_G2": 2.0}
    decision = evaluate_entry_exit("FPT", "BUY", 101.0, context, _cfg("SWING_REJECTION"), None)
    assert decision["status"] == "READY"
    assert decision["entry_zone"] == pytest.approx((100.0, 101.4))
    assert decision["sl"] == pytest.approx(99.6)
    assert decision["tp"] == pytest.approx(109.6)


def test_swing_structure_uses_market_structure_context():
    context = {
        "atr_G2": 2.0,
        "ms_G2_bias": "UP",
        "ms_G2_hh": 110.0,
        "ms_G2_hl": 102.0,
    }
    decision = evaluate_entry_exit("FPT", "BUY", 103.0, context, _cfg("SWING_STRUCTURE"), None)
    assert decision["status"] == "READY"
    assert decision["entry_zone"] == pytest.approx((102.0, 103.4))
    assert decision["sl"] == pytest.approx(101.6)


def test_fibonacci_retrace_builds_natural_levels():
    context = {"swing_high_G2": 110.0, "swing_low_G2": 100.0, "atr_G2": 2.0}
    decision = evaluate_entry_exit("FPT", "BUY", 104.0, context, _cfg("FIB_RETRACE"), None)
    assert decision["status"] == "READY"
    assert decision["entry_zone"] == pytest.approx((103.82, 105.0))
    assert decision["sl"] == pytest.approx(99.7)
    assert decision["tp"] == pytest.approx(116.18)


def test_pullback_zone_builds_atr_exit():
    context = {"ema20_G2": 105.0, "atr_G2": 2.0}
    decision = evaluate_entry_exit("FPT", "BUY", 105.0, context, _cfg("PULLBACK_ZONE"), None)
    assert decision["status"] == "READY"
    assert decision["entry_zone"] == pytest.approx((104.0, 106.0))
    assert decision["sl"] == pytest.approx(103.6)
    assert decision["tp"] == pytest.approx(108.0)


def test_no_tp_mode_is_explicitly_disabled():
    context = {"ema20_G2": 105.0, "atr_G2": 2.0}
    decision = evaluate_entry_exit(
        "FPT", "SELL", 105.0, context, _cfg("PULLBACK_ZONE", exit_tactic="NO_TP"), None
    )
    assert decision["status"] == "READY"
    assert decision["tp"] == 0.0
    assert decision["tp_disabled"] is True
    assert decision["tp_source"] == "OFF"
