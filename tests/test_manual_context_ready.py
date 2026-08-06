# -*- coding: utf-8 -*-
"""Test _manual_context_ready: check readiness data ky thuat (atr/swing theo group)
cho SL/TP mode cua preset truoc khi resolve Sandbox/Swing SL.

Dung shim bind unbound method cua BotUI de khong phai khoi tao Tk.
"""
import main


class _TradeMgrStub:
    def _get_brain_settings(self, symbol):
        return {"risk_tsl": {"sl_atr_multiplier": 0.2}}


class Shim:
    _safe_float = main.BotUI._safe_float
    _manual_rule_mode = main.BotUI._manual_rule_mode
    _resolve_manual_preset_group = main.BotUI._resolve_manual_preset_group
    _sandbox_sl_group_for_symbol = main.BotUI._sandbox_sl_group_for_symbol
    _manual_context_ready = main.BotUI._manual_context_ready
    _resolve_manual_sl_price = main.BotUI._resolve_manual_sl_price
    trade_mgr = _TradeMgrStub()


APP = Shim()

CTX_G2_FULL = {"atr_G2": 1.0, "swing_low_G2": 10.0, "swing_high_G2": 12.0}
CTX_G1_FULL = {"atr_G1": 1.0, "swing_low_G1": 10.0, "swing_high_G1": 12.0}


def test_sandbox_empty_context_not_ready():
    params = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "G2"}
    assert APP._manual_context_ready(params, {}) is False


def test_sandbox_full_context_ready():
    params = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "G2"}
    assert APP._manual_context_ready(params, CTX_G2_FULL) is True


def test_sandbox_partial_context_not_ready():
    params = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "G2"}
    ctx = {"atr_G2": 1.0, "swing_low_G2": 10.0}  # thieu swing_high
    assert APP._manual_context_ready(params, ctx) is False


def test_percent_mode_always_ready():
    params = {"MANUAL_SL_MODE": "PERCENT", "MANUAL_TP_MODE": "RR"}
    assert APP._manual_context_ready(params, {}) is True


def test_empty_params_default_ready():
    assert APP._manual_context_ready({}, {}) is True


def test_dynamic_group_trend_checks_g1():
    params = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "DYNAMIC"}
    ctx_trend = dict(CTX_G1_FULL, market_mode="TREND")
    assert APP._manual_context_ready(params, ctx_trend) is True
    # TREND -> G1; chi co data G2 thi chua du
    ctx_wrong = dict(CTX_G2_FULL, market_mode="TREND")
    assert APP._manual_context_ready(params, ctx_wrong) is False


def test_dynamic_group_sideway_checks_g2():
    params = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "DYNAMIC"}
    ctx = dict(CTX_G2_FULL, market_mode="SIDEWAY")
    assert APP._manual_context_ready(params, ctx) is True


def test_legacy_use_swing_sl_missing_not_ready():
    params = {"USE_SWING_SL": True, "MANUAL_SWING_SL_GROUP": "G2"}
    assert APP._manual_context_ready(params, {}) is False
    assert APP._manual_context_ready(params, CTX_G2_FULL) is True


def test_tp_mode_swing_needs_tp_group():
    params = {
        "MANUAL_SL_MODE": "PERCENT",
        "MANUAL_TP_MODE": "SWING_REJECTION",
        "MANUAL_TP_GROUP": "G1",
    }
    assert APP._manual_context_ready(params, {}) is False
    assert APP._manual_context_ready(params, CTX_G1_FULL) is True


def test_sl_and_tp_different_groups_both_required():
    params = {
        "MANUAL_SL_MODE": "SANDBOX",
        "MANUAL_SL_GROUP": "G2",
        "MANUAL_TP_MODE": "FIB",
        "MANUAL_TP_GROUP": "G1",
    }
    only_sl = dict(CTX_G2_FULL)
    assert APP._manual_context_ready(params, only_sl) is False
    both = dict(CTX_G2_FULL, **CTX_G1_FULL)
    assert APP._manual_context_ready(params, both) is True


# --- Wrong-side guard trong _resolve_manual_sl_price ---

SANDBOX_PARAMS = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "G2", "SL_PERCENT": 0.5}


def test_legacy_sandbox_sl_is_migrated_to_manual_swing_buy():
    # Giá 33.95, swing low 33.30, atr 0.5, buffer 0.2 -> SL 33.20 < giá: hợp lệ
    ctx = {"atr_G2": 0.5, "swing_low_G2": 33.30, "swing_high_G2": 34.50}
    sl, dist, label, missing = APP._resolve_manual_sl_price(
        "CTG", "BUY", 33.95, SANDBOX_PARAMS, ctx
    )
    assert label.startswith("SWING")
    assert missing is False
    assert sl < 33.95 and abs(sl - 33.20) < 1e-9


def test_legacy_sandbox_wrong_side_reports_missing_without_percent_fallback():
    # Giá đã thủng đáy swing 15m: swing low 34.27 > giá 33.95 -> SL 34.17 nằm TRÊN giá
    # -> guard đẩy về Percent thay vì trả SL vô nghĩa + distance hẹp giả.
    ctx = {"atr_G2": 0.5, "swing_low_G2": 34.27, "swing_high_G2": 35.00}
    sl, dist, label, missing = APP._resolve_manual_sl_price(
        "CTG", "BUY", 33.95, SANDBOX_PARAMS, ctx
    )
    assert label == "SWING_REJECTION:MISSING"
    assert missing is True


def test_swing_sl_wrong_side_buy_reports_missing():
    params = {"MANUAL_SL_MODE": "SWING_REJECTION", "MANUAL_SL_GROUP": "G2", "SL_PERCENT": 0.5}
    ctx = {"atr_G2": 0.5, "swing_low_G2": 34.27, "swing_high_G2": 35.00}
    sl, dist, label, missing = APP._resolve_manual_sl_price(
        "CTG", "BUY", 33.95, params, ctx
    )
    assert missing is True  # caller sẽ tự fallback Percent + hiện cảnh báo


def test_manual_sl_input_bypasses_guard():
    # SL gõ tay luôn được tôn trọng, không qua guard
    sl, dist, label, missing = APP._resolve_manual_sl_price(
        "CTG", "BUY", 33.95, SANDBOX_PARAMS, {}, manual_sl=33.0
    )
    assert label == "MANUAL"
    assert sl == 33.0


class _OverrideTradeMgrStub:
    def _get_brain_settings(self, symbol):
        return {
            "risk_tsl": {
                "base_sl": "G1",
                "sl_atr_multiplier": 0.2,
            }
        }


class OverrideShim(Shim):
    trade_mgr = _OverrideTradeMgrStub()


OVERRIDE_APP = OverrideShim()


def test_legacy_sandbox_keeps_manual_group_not_bot_symbol_override():
    params = {
        "MANUAL_SL_MODE": "SANDBOX",
        "MANUAL_SL_GROUP": "G0",
        "SL_PERCENT": 0.5,
    }
    context = {
        "atr_G0": 10.0,
        "swing_low_G0": 1700.0,
        "swing_high_G0": 1900.0,
        "atr_G1": 2.0,
        "swing_low_G1": 1815.0,
        "swing_high_G1": 1825.0,
    }
    sl, _, label, missing = OVERRIDE_APP._resolve_manual_sl_price(
        "VN30F1M",
        "BUY",
        1820.0,
        params,
        context,
    )
    assert label == "SWING:G0"
    assert missing is False
    assert sl == 1698.0


def test_context_readiness_uses_manual_preset_group_not_bot_group():
    params = {"MANUAL_SL_MODE": "SANDBOX", "MANUAL_SL_GROUP": "G0"}
    assert OVERRIDE_APP._manual_context_ready(
        params,
        CTX_G1_FULL,
        "VN30F1M",
    ) is False
    assert OVERRIDE_APP._manual_context_ready(
        params,
        {"atr_G0": 1.0, "swing_low_G0": 10.0, "swing_high_G0": 12.0},
        "VN30F1M",
    ) is True
