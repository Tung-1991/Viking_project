# -*- coding: utf-8 -*-
"""Manual Preview must size and report risk exactly like the BOT."""

from types import SimpleNamespace

import pytest

import config
import main
from core.dnse_connector import DNSEConnector
from telegram_notify.opportunity_alerts import format_digest


class _Connector:
    calculate_lot_size = DNSEConnector.calculate_lot_size

    def __init__(self):
        self.tick_reads = 0

    def get_tick(self, _symbol):
        self.tick_reads += 1
        raise AssertionError("entry_price đã có thì không được đọc tick lần nữa")

    def get_symbol_info(self, symbol, poll_tick=True):
        if str(symbol).upper().startswith("VN30F"):
            return SimpleNamespace(
                trade_contract_size=100000.0,
                volume_min=1.0,
                volume_max=200.0,
                volume_step=1.0,
                point=0.1,
            )
        return SimpleNamespace(
            trade_contract_size=1000.0,
            volume_min=100.0,
            volume_max=1_000_000.0,
            volume_step=100.0,
            point=0.01,
        )

    def get_account_info(self):
        return {"equity": 100_000_000.0, "balance": 100_000_000.0}


class _TradeManager:
    def _get_brain_settings(self, _symbol):
        return {
            "G0_TIMEFRAME": "1h",
            "G1_TIMEFRAME": "15m",
            "risk_tsl": {"base_sl": "G1", "sl_atr_multiplier": 0.2},
            "entry_exit": {"sl_distance": {"max_atr": 2.5}},
            "symbol_configs": {"VN30F1M": {"max_lot_cap": 1}},
        }

    @staticmethod
    def _stock_settled_long_volume(_symbol):
        return 300.0


class _PreviewShim:
    _resolve_manual_setup_preview = main.BotUI._resolve_manual_setup_preview
    build_telegram_preview_order = main.BotUI.build_telegram_preview_order
    _safe_float = main.BotUI._safe_float
    _manual_rule_mode = main.BotUI._manual_rule_mode
    _resolve_manual_preset_group = main.BotUI._resolve_manual_preset_group
    _sandbox_sl_group_for_symbol = main.BotUI._sandbox_sl_group_for_symbol
    _normalize_contracts = main.BotUI._normalize_contracts
    _manual_source_label = main.BotUI._manual_source_label
    _symbol_group_timeframe = main.BotUI._symbol_group_timeframe
    _parse_preview_levels = main.BotUI._parse_preview_levels

    connector = _Connector()
    trade_mgr = _TradeManager()

    @staticmethod
    def _is_derivative_symbol(symbol):
        return str(symbol).upper().startswith("VN30F")

    @staticmethod
    def calculate_round_trip_trade_fee(*_args, **_kwargs):
        return 0.0

    @staticmethod
    def calculate_trade_fee(*_args, **_kwargs):
        return 0.0


def test_manual_preview_clamps_fractional_derivative_lot_and_reports_actual_risk(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "PREVIEW_RISK_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "RR",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "SL_PERCENT": 0.5,
            "TP_RR_RATIO": 1.5,
            "RISK_PERCENT": 1.0,
        },
    )
    app = _PreviewShim()
    app.connector.tick_reads = 0

    setup = app._resolve_manual_setup_preview(
        "VN30F1M",
        "BUY",
        "PREVIEW_RISK_TEST",
        {"current_price": 1823.0, "bid": 1823.0, "ask": 1823.0},
        manual_values={
            "entry": 1823.0,
            "lot": 0.0,
            "sl": 1794.62,
            "tp": 1865.57,
        },
    )

    assert setup["ready"] is True
    assert setup["lot"] == 1.0
    assert setup["risk_usd"] == pytest.approx(2_838_000.0)
    assert setup["reward_usd"] == pytest.approx(4_257_000.0)
    assert setup["target_risk_pct"] == 1.0
    assert setup["risk_pct"] == pytest.approx(2.838)
    assert app.connector.tick_reads == 0


def test_default_scalping_preview_uses_manual_g1_swing_sl_and_fib_tp_not_bot_g0():
    app = _PreviewShim()
    setup = app._resolve_manual_setup_preview(
        "VN30F1M",
        "BUY",
        "SCALPING",
        {
            "current_price": 1890.0,
            "bid": 1890.0,
            "ask": 1890.0,
            # G0 cố ý rất xa để bắt lỗi lẫn cấu hình BOT/Manual.
            "atr_G0": 17.0,
            "swing_low_G0": 1795.0,
            "swing_high_G0": 1900.0,
            # Preset SCALPING hiện phải đọc G1 (15m).
            "atr_G1": 6.0,
            "swing_low_G1": 1878.0,
            "swing_high_G1": 1896.0,
        },
        manual_values={"entry": 0.0, "lot": 1.0, "sl": 0.0, "tp": 0.0},
    )

    assert setup["ready"] is True
    assert setup["manual_sl_group"] == "G1"
    assert setup["manual_tp_group"] == "G1"
    assert setup["sl"] == pytest.approx(1876.8)
    assert setup["manual_tp_mode"] == "FIB"
    assert setup["tp"] == pytest.approx(1900.896)
    assert setup["tp_targets"] == pytest.approx([1900.896, 1907.124, 1914.0])
    assert setup["tp_target_sources"] == ["F", "F", "F"]
    assert setup["sl_source"] == "MANUAL_SWING_RETEST:G1"
    assert setup["tp_source"] == "MANUAL_FIB:G1"


def test_telegram_preview_uses_same_stock_lot_and_configured_rr_ladder(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "TELEGRAM_STOCK_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "RR",
            "MANUAL_SL_GROUP": "G0",
            "MANUAL_TP_GROUP": "G0",
            "SL_PERCENT": 5.0,
            "TP_RR_RATIO": 1.5,
            # 0,1% NAV chỉ cho raw lot 40 CP; DNSE/BOT phải ép thành 100 CP.
            "RISK_PERCENT": 0.1,
        },
    )
    monkeypatch.setattr(config, "DEFAULT_PRESET", "TELEGRAM_STOCK_TEST")
    app = _PreviewShim()

    stock_setup = app._resolve_manual_setup_preview(
        "FPT",
        "BUY",
        "TELEGRAM_STOCK_TEST",
        {"current_price": 50.0, "bid": 50.0, "ask": 50.0},
        manual_values={
            "entry": 50.0,
            "lot": 0.0,
            "sl": 47.5,
            "tp": 53.75,
        },
    )
    buy = app.build_telegram_preview_order(
        "FPT",
        "BUY",
        context={"current_price": 50.0, "bid": 50.0, "ask": 50.0},
    )
    sell = app.build_telegram_preview_order(
        "MBS",
        "SELL",
        context={"current_price": 50.0, "bid": 50.0, "ask": 50.0},
    )

    assert stock_setup["lot"] == 100.0
    assert stock_setup["risk_usd"] == pytest.approx(250_000.0)
    assert stock_setup["target_risk_pct"] == 0.1
    assert stock_setup["risk_pct"] == pytest.approx(0.25)
    assert buy["ok"] is True
    assert buy["preview_version"] == 4
    assert buy["lot"] == 100.0
    assert buy["display_quantity"] == 100.0
    assert buy["quantity_unit"] == "CP"
    assert buy["tp_targets"] == pytest.approx([53.75, 54.375, 55.0])
    assert buy["tp_target_sources"] == ["R", "R", "R"]

    assert sell["ok"] is True
    assert sell["analysis_only"] is True
    assert sell["display_quantity"] == 300.0
    assert sell["quantity_unit"] == "CP"
    assert sell["tp_targets"] == pytest.approx([46.25, 45.625, 45.0])
    assert sell["tp_target_sources"] == ["R", "R", "R"]

    text = format_digest(
        [
            {
                "symbol": "FPT",
                "side": "BUY",
                "market_type": "CKCS",
                "order_setup": buy,
            },
            {
                "symbol": "MBS",
                "side": "SELL",
                "market_type": "CKCS",
                "order_setup": sell,
            },
        ]
    )
    assert "🟢 CKCS BUY" in text
    assert "FPT | Giá 50 | Entry NOW @50 (100 CP) | CẮT 47.5 | TP1 53.75 (R)" in text
    assert "TP2 54.38 (R) | TP3 55 (R)" in text
    assert "🔴 CKCS SELL" in text
    assert "MBS | Giá 50 | Entry NOW @50 (300 CP) | CẮT 52.5 | TP1 46.25" in text


def test_telegram_preview_derivative_uses_one_contract_and_rr_ladder(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "TELEGRAM_CKPS_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "RR",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "SL_PERCENT": 0.5,
            "TP_RR_RATIO": 1.5,
            "RISK_PERCENT": 1.0,
        },
    )
    monkeypatch.setattr(config, "DEFAULT_PRESET", "TELEGRAM_CKPS_TEST")
    app = _PreviewShim()

    order = app.build_telegram_preview_order(
        "VN30F1M",
        "BUY",
        context={"current_price": 1800.0, "bid": 1800.0, "ask": 1800.0},
    )

    assert order["ok"] is True
    assert order["lot"] == 1.0
    assert order["display_quantity"] == 1.0
    assert order["quantity_unit"] == "HĐ"
    assert order["tp_targets"] == pytest.approx([1813.5, 1815.75, 1818.0])
    assert order["tp_target_sources"] == ["R", "R", "R"]

    text = format_digest(
        [
            {
                "symbol": "VN30F1M",
                "side": "BUY",
                "market_type": "CKPS",
                "order_setup": order,
            }
        ]
    )
    assert (
        "🟢 VN30F1M LONG | Giá 1800 | Entry NOW @1800 (1 HĐ) | SL 1791 | "
        "TP1 1813.5 (R)"
    ) in text
    assert "TP2 1815.75 (R) | TP3 1818 (R)" in text


def test_rr_target_is_recomputed_from_connector_safe_sl(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "SAFE_SL_RR_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "RR",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "SL_PERCENT": 1.0,
            "TP_RR_RATIO": 1.5,
            "RISK_PERCENT": 1.0,
        },
    )
    app = _PreviewShim()
    monkeypatch.setattr(
        app.connector,
        "calculate_lot_size",
        lambda *_args, **_kwargs: (100.0, 98.0),
    )

    setup = app._resolve_manual_setup_preview(
        "FPT",
        "BUY",
        "SAFE_SL_RR_TEST",
        {"current_price": 100.0, "bid": 100.0, "ask": 100.0},
        manual_values={"entry": 0.0, "lot": 0.0, "sl": 0.0, "tp": 0.0},
    )

    assert setup["ready"] is True
    assert setup["sl"] == pytest.approx(98.0)
    assert setup["tp"] == pytest.approx(103.0)
    assert setup["tp_targets"] == pytest.approx([103.0, 103.5, 104.0])
    assert setup["tp_target_sources"] == ["R", "R", "R"]


def test_swing_tp_uses_real_swing_target_then_fib_display_targets(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "SWING_TARGET_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "SWING_REJECTION",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "SL_PERCENT": 5.0,
            "MANUAL_SWING_TP_ATR_MULT": 0.2,
            "RISK_PERCENT": 1.0,
        },
    )
    app = _PreviewShim()

    setup = app._resolve_manual_setup_preview(
        "FPT",
        "BUY",
        "SWING_TARGET_TEST",
        {
            "current_price": 50.0,
            "bid": 50.0,
            "ask": 50.0,
            "atr_G1": 2.0,
            "swing_low_G1": 45.0,
            "swing_high_G1": 55.0,
        },
        manual_values={"entry": 0.0, "lot": 100.0, "sl": 0.0, "tp": 0.0},
    )

    assert setup["ready"] is True
    assert setup["tp"] == pytest.approx(54.6)
    assert setup["tp_targets"][0] == pytest.approx(54.6)
    assert setup["tp_targets"][1:] == pytest.approx([57.72, 61.18])
    assert setup["tp_target_sources"] == ["S", "F", "F"]


def test_legacy_sandbox_manual_mode_migrates_to_its_own_swing_group(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "LINKED_RETEST_TEST",
        {
            "MANUAL_SL_MODE": "SANDBOX",
            "MANUAL_TP_MODE": "SWING_REJECTION",
            "MANUAL_SL_GROUP": "G0",
            "MANUAL_TP_GROUP": "G0",
            "MANUAL_SWING_TP_ATR_MULT": 0.2,
            "MANUAL_FIB_TP_LEVELS": "1.272,1.618,2.0",
            "RISK_PERCENT": 1.0,
        },
    )
    app = _PreviewShim()

    setup = app._resolve_manual_setup_preview(
        "VN30F1M",
        "BUY",
        "LINKED_RETEST_TEST",
        {
            "current_price": 1800.0,
            "bid": 1800.0,
            "ask": 1800.0,
            "atr_G0": 30.0,
            "swing_low_G0": 1700.0,
            "swing_high_G0": 1900.0,
            "atr_G1": 5.0,
            "swing_low_G1": 1790.0,
            "swing_high_G1": 1810.0,
        },
        manual_values={"entry": 0.0, "lot": 1.0, "sl": 0.0, "tp": 0.0},
    )

    assert setup["manual_sl_mode"] == "SWING_REJECTION"
    assert setup["manual_sl_group"] == "G0"
    assert setup["manual_tp_group"] == "G0"
    assert setup["tp_targets"] == pytest.approx([1894.0, 1954.4, 2023.6])
    assert setup["tp_target_sources"] == ["S", "F", "F"]

    monkeypatch.setattr(config, "DEFAULT_PRESET", "LINKED_RETEST_TEST")
    telegram = app.build_telegram_preview_order(
        "VN30F1M",
        "BUY",
        context={
            "current_price": 1800.0,
            "bid": 1800.0,
            "ask": 1800.0,
            "atr_G0": 30.0,
            "swing_low_G0": 1700.0,
            "swing_high_G0": 1900.0,
        },
    )
    # Manual preview vẫn được hiển thị, nhưng lớp gửi Telegram từ preset cũ
    # phải từ chối kế hoạch có SL vượt trần ATR thay vì lén dùng BOT override.
    assert telegram["ok"] is False
    assert "SL_TOO_WIDE" in telegram["error"]


def test_fib_tp_keeps_only_explicitly_configured_multiple_levels(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "FIB_TARGET_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "FIB",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "SL_PERCENT": 5.0,
            "MANUAL_FIB_TP_LEVELS": "1.272,1.618,2.0",
            "RISK_PERCENT": 1.0,
        },
    )
    app = _PreviewShim()

    setup = app._resolve_manual_setup_preview(
        "FPT",
        "BUY",
        "FIB_TARGET_TEST",
        {
            "current_price": 50.0,
            "bid": 50.0,
            "ask": 50.0,
            "atr_G1": 2.0,
            "swing_low_G1": 45.0,
            "swing_high_G1": 50.0,
        },
        manual_values={"entry": 0.0, "lot": 100.0, "sl": 0.0, "tp": 0.0},
    )

    assert setup["ready"] is True
    assert setup["tp_targets"] == pytest.approx([51.36, 53.09, 55.0])
    assert setup["tp_target_sources"] == ["F", "F", "F"]


def test_telegram_rejects_swing_sl_wider_than_existing_entry_exit_max_atr(
    monkeypatch,
):
    monkeypatch.setitem(
        config.PRESETS,
        "TELEGRAM_WIDE_SL_TEST",
        {
            "MANUAL_SL_MODE": "SANDBOX",
            "MANUAL_TP_MODE": "RR",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "TP_RR_RATIO": 1.5,
            "RISK_PERCENT": 1.0,
        },
    )
    monkeypatch.setattr(config, "DEFAULT_PRESET", "TELEGRAM_WIDE_SL_TEST")
    app = _PreviewShim()

    order = app.build_telegram_preview_order(
        "MBS",
        "SELL",
        context={
            "current_price": 17.0,
            "bid": 17.0,
            "ask": 17.0,
            "atr_G1": 1.0,
            "swing_low_G1": 12.0,
            "swing_high_G1": 20.8,
        },
    )

    assert order["ok"] is False
    assert order["error"] == "INVALID_LEVELS|SL_TOO_WIDE|4.00ATR>2.5ATR"
    assert order["preview_version"] == 4


def test_fib_mode_does_not_silently_fill_missing_targets_with_rr(monkeypatch):
    monkeypatch.setitem(
        config.PRESETS,
        "TELEGRAM_BAD_FIB_TEST",
        {
            "MANUAL_SL_MODE": "PERCENT",
            "MANUAL_TP_MODE": "FIB",
            "MANUAL_SL_GROUP": "G1",
            "MANUAL_TP_GROUP": "G1",
            "SL_PERCENT": 5.0,
            "MANUAL_FIB_TP_LEVELS": "1.272,1.618,2.0",
            "RISK_PERCENT": 1.0,
        },
    )
    monkeypatch.setattr(config, "DEFAULT_PRESET", "TELEGRAM_BAD_FIB_TEST")
    app = _PreviewShim()

    order = app.build_telegram_preview_order(
        "MBS",
        "SELL",
        context={
            "current_price": 15.0,
            "bid": 15.0,
            "ask": 15.0,
            "atr_G1": 1.0,
            "swing_low_G1": 10.0,
            "swing_high_G1": 20.0,
        },
    )

    assert order["ok"] is True
    assert order["tp_targets"] == pytest.approx([7.28, 3.82])
    assert order["tp_target_sources"] == ["F", "F"]
