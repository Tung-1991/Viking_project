# -*- coding: utf-8 -*-
"""Test core/risk_gate: van risk per-lệnh (tiền-mất-nếu-SL / NAV) + NAV cap CKCS dùng chung."""
from types import SimpleNamespace

import core.trade_manager as trade_manager_module
from core import risk_gate
from core.trade_manager import TradeManager

GATE = {"RISK_GATE_MAX_PCT_PS": 10.0, "RISK_GATE_MAX_PCT_CS": 3.0}


# --- evaluate() ---

def test_ps_over_threshold_bot_blocks():
    # NAV 10M, VN30F entry 1995 SL 1931.33 lot 1 cs 100k -> est_loss ~6.367M = 63.7% > 10%
    res = risk_gate.evaluate("VN30F1M", 1995.0, 1931.33, 1, 100000.0, 10_000_000.0, GATE, source="BOT")
    assert res["action"] == "BLOCK"
    assert res["passed"] is False
    assert 63.0 < res["risk_pct"] < 64.5
    assert "VN30F1M" in res["msg"]


def test_ps_over_threshold_manual_confirms_telegram_blocks():
    args = ("VN30F1M", 1995.0, 1931.33, 1, 100000.0, 10_000_000.0, GATE)
    assert risk_gate.evaluate(*args, source="MANUAL")["action"] == "CONFIRM"
    assert risk_gate.evaluate(*args, source="TELEGRAM")["action"] == "BLOCK"


def test_cs_under_threshold_ok():
    # NAV 100M, FPT 1000 CP, SL cách 2 (nghìn) -> est_loss 2M = 2% < 3%
    res = risk_gate.evaluate("FPT", 100.0, 98.0, 1000, 1000.0, 100_000_000.0, GATE, source="MANUAL")
    assert res["action"] == "OK"
    assert res["passed"] is True
    assert abs(res["risk_pct"] - 2.0) < 1e-9


def test_threshold_zero_is_off():
    off = {"RISK_GATE_MAX_PCT_PS": 0.0, "RISK_GATE_MAX_PCT_CS": 0.0}
    res = risk_gate.evaluate("VN30F1M", 1995.0, 1931.33, 1, 100000.0, 10_000_000.0, off, source="BOT")
    assert res["action"] == "OK"
    assert res["checks"][0]["status"] == "WARN"


def test_missing_data_never_blocks():
    for kwargs in (
        {"sl_price": 0.0},   # lệnh không SL / DCA con
        {"nav": 0.0},
        {"lot_size": 0.0},
    ):
        base = dict(symbol="VN30F1M", entry_price=1995.0, sl_price=1900.0,
                    lot_size=1, contract_size=100000.0, nav=10_000_000.0)
        base.update(kwargs)
        res = risk_gate.evaluate(settings=GATE, source="BOT", **base)
        assert res["action"] == "OK"
        assert res["checks"][0]["status"] == "WARN"


def test_settings_from_brain_reads_bot_safeguard_and_falls_back():
    s = risk_gate.settings_from_brain({"bot_safeguard": {"RISK_GATE_MAX_PCT_PS": 25.0}})
    assert s["RISK_GATE_MAX_PCT_PS"] == 25.0
    # key CS không set trong brain -> fallback config.BOT_SAFEGUARD/default
    assert s["RISK_GATE_MAX_PCT_CS"] > 0
    s2 = risk_gate.settings_from_brain(None)
    assert s2["RISK_GATE_MAX_PCT_PS"] > 0


# --- apply_stock_caps() parity với logic bot cũ ---

def _sym_info(cs=1000.0):
    return SimpleNamespace(trade_contract_size=cs)


def test_stock_caps_nav_trims_lot():
    # NAV 100M cap 20% = 20M; giá 33.65 -> 594 -> 500 CP lô chẵn
    out = risk_gate.apply_stock_caps(
        "FPT", 1000, 33.65, {"equity": 100_000_000.0}, _sym_info(),
        {"STOCK_MAX_ORDER_NAV_PCT": 20.0},
    )
    assert out["lot"] == 500
    assert out["capped_by"] == "NAV"
    assert out["error"] == ""


def test_stock_caps_cash_cap_wins_when_cash_low():
    # cash 10M * 0.99 = 9.9M < NAV cap 20M -> cap theo TIỀN MẶT: 9.9M/33.65k = 294 -> 200 CP
    out = risk_gate.apply_stock_caps(
        "FPT", 1000, 33.65,
        {"equity": 100_000_000.0, "cash_available": 10_000_000.0}, _sym_info(),
        {"STOCK_MAX_ORDER_NAV_PCT": 20.0},
    )
    assert out["capped_by"] == "TIỀN MẶT"
    assert out["lot"] == 200


def test_stock_caps_too_small_returns_safeguard_string():
    out = risk_gate.apply_stock_caps(
        "FPT", 100, 33.65, {"equity": 1_000_000.0}, _sym_info(),
        {"STOCK_MAX_ORDER_NAV_PCT": 20.0},
    )
    assert out["error"].startswith("SAFEGUARD_FAIL|CKCS_CAP_TOO_SMALL|FPT")


def test_stock_caps_ignores_derivatives_and_disabled():
    out = risk_gate.apply_stock_caps(
        "VN30F1M", 5, 1995.0, {"equity": 10_000_000.0}, _sym_info(100000.0),
        {"STOCK_MAX_ORDER_NAV_PCT": 20.0},
    )
    assert out["lot"] == 5 and out["error"] == ""
    out2 = risk_gate.apply_stock_caps(
        "FPT", 99999, 33.65, {"equity": 1_000_000.0}, _sym_info(),
        {"STOCK_MAX_ORDER_NAV_PCT": 0.0},
    )
    assert out2["lot"] == 99999 and out2["error"] == ""


# --- Trade-manager-level: 2 pha manual + bot block ---

class DummyChecklist:
    def run_pre_trade_checks(self, *args, **kwargs):
        return {"passed": True, "checks": []}


class DummyConnector:
    def __init__(self, equity):
        self.orders = []
        self.account_info = {"balance": equity, "equity": equity}

    def get_account_info(self):
        return dict(self.account_info)

    def get_all_open_positions(self):
        return []

    def get_tick(self, symbol):
        return SimpleNamespace(symbol=symbol, bid=1994.0, ask=1995.0, last=1995.0, spread=1.0)

    def get_symbol_info(self, symbol):
        return SimpleNamespace(
            symbol=symbol, point=0.1, trade_contract_size=100000.0,
            volume_min=1.0, volume_max=200.0, volume_step=1.0,
            trade_stops_level=0.0, spread=1.0,
        )

    def calculate_profit(self, symbol, side, volume, entry_price, exit_price):
        direction = 1 if str(side).upper() in ("0", "BUY", "NB") else -1
        return (float(exit_price) - float(entry_price)) * direction * float(volume) * 100000.0

    def place_order(self, *args, **kwargs):
        self.orders.append((args, kwargs))
        return SimpleNamespace(ok=True, order_id="O1", position_id="P1")


def _make_mgr(monkeypatch, equity):
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _s: (True, ""))
    monkeypatch.setattr(
        "core.storage_manager.get_magic_numbers",
        lambda: {"bot_magic": 9999, "manual_magic": 8888},
    )
    mgr = TradeManager(DummyConnector(equity), DummyChecklist(), log_callback=lambda *a, **k: None)
    monkeypatch.setattr(
        mgr, "_get_brain_settings",
        lambda _s=None: {"bot_safeguard": {}, "risk_tsl": {}, "entry_exit": {}},
    )
    return mgr


def test_manual_two_phase_confirm_then_ack(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # NAV 10M, SL cách 63.67 điểm -> risk ~63.7% > trần PS 10% -> CONFIRM, chưa đặt lệnh
    mgr = _make_mgr(monkeypatch, 10_000_000.0)
    kwargs = dict(manual_lot=1, manual_tp=2090.5, manual_sl=1931.33, manual_entry_price=1995.0)
    res1 = mgr.execute_manual_trade("BUY", "SCALPING", "VN30F1M", False, {}, **kwargs)
    assert res1.startswith("RISK_GATE_CONFIRM")
    assert mgr.connector.orders == []
    assert mgr.state.get("manual_trades_today", 0) == 0

    # Gọi lại với ack -> đặt lệnh, counter +1 đúng 1 lần
    res2 = mgr.execute_manual_trade("BUY", "SCALPING", "VN30F1M", False, {}, risk_gate_ack=True, **kwargs)
    assert res2.startswith("SUCCESS")
    assert len(mgr.connector.orders) == 1
    assert mgr.state["manual_trades_today"] == 1


def test_manual_small_risk_passes_without_confirm(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # NAV 1 tỷ -> cùng lệnh chỉ ~0.64% NAV -> đi thẳng
    mgr = _make_mgr(monkeypatch, 1_000_000_000.0)
    res = mgr.execute_manual_trade(
        "BUY", "SCALPING", "VN30F1M", False, {},
        manual_lot=1, manual_tp=2090.5, manual_sl=1931.33, manual_entry_price=1995.0,
    )
    assert res.startswith("SUCCESS")
