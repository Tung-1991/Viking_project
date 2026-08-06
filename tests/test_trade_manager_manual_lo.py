# -*- coding: utf-8 -*-
from types import SimpleNamespace

import core.trade_manager as trade_manager_module
from core.trade_manager import TradeManager


class DummyChecklist:
    def run_pre_trade_checks(self, *args, **kwargs):
        return {"passed": True, "checks": []}


class DummyConnector:
    def __init__(self, account_info=None):
        self.orders = []
        self.account_info = account_info or {"balance": 100000000.0, "equity": 100000000.0}

    def get_account_info(self):
        return dict(self.account_info)

    def get_all_open_positions(self):
        return []

    def get_tick(self, symbol):
        return SimpleNamespace(symbol=symbol, bid=1209.0, ask=1210.0, last=1210.0, spread=1.0)

    def get_symbol_info(self, symbol):
        is_stock = str(symbol).upper() == "FPT"
        return SimpleNamespace(
            symbol=symbol,
            point=0.1,
            trade_contract_size=1000.0 if is_stock else 100000.0,
            volume_min=1.0,
            volume_max=200.0,
            volume_step=1.0,
            trade_stops_level=0.0,
            spread=1.0,
        )

    def calculate_profit(self, symbol, side, volume, entry_price, exit_price):
        direction = 1 if str(side).upper() in ("0", "BUY", "NB") else -1
        return (float(exit_price) - float(entry_price)) * direction * float(volume) * 100000.0

    def place_order(self, *args, **kwargs):
        self.orders.append((args, kwargs))
        return SimpleNamespace(ok=True, order_id="O1", position_id="P1")


def test_symbol_base_sl_group_overrides_manual_preset_group(monkeypatch):
    mgr = object.__new__(TradeManager)
    monkeypatch.setattr(
        mgr,
        "_get_brain_settings",
        lambda _symbol=None: {"risk_tsl": {"base_sl": "G1"}},
    )

    assert mgr._resolve_base_sl_group("VN30F1M", {}, fallback="G0") == "G1"


def test_dynamic_base_sl_group_follows_market_mode():
    mgr = object.__new__(TradeManager)
    risk_tsl = {"base_sl": "DYNAMIC"}

    assert mgr._resolve_base_sl_group(
        "VN30F1M",
        {"market_mode": "TREND"},
        risk_tsl=risk_tsl,
    ) == "G1"
    assert mgr._resolve_base_sl_group(
        "VN30F1M",
        {"market_mode": "RANGE"},
        risk_tsl=risk_tsl,
    ) == "G2"


def test_manual_entry_price_flows_to_connector_and_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _symbol: (True, ""))
    monkeypatch.setattr(
        "core.storage_manager.get_magic_numbers",
        lambda: {"bot_magic": 9999, "manual_magic": 8888},
    )
    mgr = TradeManager(DummyConnector(), DummyChecklist(), log_callback=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mgr,
        "_get_brain_settings",
        lambda _symbol=None: {"bot_safeguard": {}, "risk_tsl": {}, "entry_exit": {}},
    )

    result = mgr.execute_manual_trade(
        "BUY",
        "SCALPING",
        "VN30F1M",
        False,
        {},
        manual_lot=1,
        manual_tp=1220,
        manual_sl=1190,
        tactic_str="BE",
        manual_entry_price=1200,
        entry_exit_tactic="FALLBACK_R",
    )

    assert result.startswith("SUCCESS")
    args, kwargs = mgr.connector.orders[0]
    assert kwargs["price"] == 1200
    assert mgr.state["trade_prices"]["O1"] == 1200
    assert mgr.state["trade_tactics"]["O1"] == "BE"
    assert mgr.state["entry_exit_tactics"]["O1"] == "FALLBACK_R"


def test_manual_ckcs_margin_adds_tag_and_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _symbol: (True, ""))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "core.storage_manager.get_magic_numbers",
        lambda: {"bot_magic": 9999, "manual_magic": 8888},
    )
    connector = DummyConnector(
        {
            "balance": 1_000_000_000.0,
            "equity": 1_000_000_000.0,
            "cash_available": 900_000_000.0,
            "free_margin": 900_000_000.0,
            "rtt": 120.0,
        }
    )
    mgr = TradeManager(connector, DummyChecklist(), log_callback=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mgr,
        "_get_brain_settings",
        lambda _symbol=None: {
            "bot_safeguard": {},
            "risk_tsl": {},
            "entry_exit": {},
            "manual_margin": {
                "ENABLE_MANUAL_MARGIN": True,
                "MARGIN_RISK_BASE": "FREE_CASH",
                "MAX_MARGIN_ORDER_VALUE_PCT": 50.0,
                "MIN_RTT_TO_OPEN": 100.0,
                "MAX_MANUAL_MARGIN_LOSS_PCT": 3.0,
            },
        },
    )

    result = mgr.execute_manual_trade(
        "BUY",
        "SCALPING",
        "FPT",
        False,
        {},
        manual_lot=100,
        manual_tp=1220,
        manual_sl=1190,
    )

    assert result.startswith("SUCCESS")
    args, kwargs = connector.orders[0]
    assert "[MARGIN][MANUAL]" in args[6]
    assert mgr.state["trade_margin_meta"]["O1"]["risk_base"] == "FREE_CASH"
    assert mgr.state["trade_margin_meta"]["O1"]["snapshot"]["rtt"] == 120.0


def test_manual_ckcs_margin_blocks_low_rtt(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _symbol: (True, ""))
    monkeypatch.chdir(tmp_path)
    connector = DummyConnector(
        {
            "balance": 1_000_000_000.0,
            "equity": 1_000_000_000.0,
            "cash_available": 900_000_000.0,
            "free_margin": 900_000_000.0,
            "rtt": 70.0,
        }
    )
    mgr = TradeManager(connector, DummyChecklist(), log_callback=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mgr,
        "_get_brain_settings",
        lambda _symbol=None: {
            "bot_safeguard": {},
            "risk_tsl": {},
            "entry_exit": {},
            "manual_margin": {"ENABLE_MANUAL_MARGIN": True, "MIN_RTT_TO_OPEN": 100.0},
        },
    )

    result = mgr.execute_manual_trade(
        "BUY",
        "SCALPING",
        "FPT",
        False,
        {},
        manual_lot=100,
        manual_tp=1220,
        manual_sl=1190,
    )

    assert result.startswith("SAFEGUARD_FAIL|MANUAL_MARGIN")
    assert connector.orders == []
