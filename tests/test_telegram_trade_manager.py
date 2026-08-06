# -*- coding: utf-8 -*-
from types import SimpleNamespace
import sys
import types


sys.modules.setdefault(
    "core.data_engine",
    types.SimpleNamespace(data_engine=SimpleNamespace(_last_tick={})),
)

import core.trade_manager as trade_manager_mod
from core.dnse_connector import BrokerOrderResult
from core.trade_manager import TradeManager


class FakeConnector:
    _is_connected = True

    def __init__(self):
        self.orders = []
        self.fee_calls = []

    def get_account_info(self):
        return {"balance": 1000.0, "equity": 1000.0}

    def get_all_open_positions(self):
        return []

    def calculate_profit(self, symbol, side, volume, entry_price, sl_price):
        return -10.0

    def calculate_lot_size(self, symbol, risk_usd, sl_price, order_type, strict_fee_per_lot=0.0):
        return 1.0, sl_price

    def calculate_trade_fee(self, symbol, price, volume, side=None):
        self.fee_calls.append((symbol, price, volume, side))
        return 10.0 if side == "BUY" else 20.0

    def place_order(self, symbol, order_type, lot, sl, tp, magic, comment):
        self.orders.append((symbol, order_type, lot, sl, tp, magic, comment))
        return BrokerOrderResult(ok=True, order_id="12345")

    def get_tick(self, symbol):
        return SimpleNamespace(ask=2000.0, bid=1999.0, last=2000.0, spread=1.0)

    def get_symbol_info(self, symbol):
        return SimpleNamespace(
            volume_min=1.0,
            volume_max=200.0,
            volume_step=1.0,
            point=0.1,
            trade_contract_size=100000.0,
            spread=1.0,
        )


class FakeChecklist:
    def __init__(self):
        self.passed = True

    def run_pre_trade_checks(self, account_info, state, symbol, strict_mode=True):
        if self.passed:
            return {"passed": True, "checks": []}
        return {"passed": False, "checks": [{"status": "FAIL", "msg": "Spr 700 (Max 150)"}]}


def _manager(monkeypatch):
    monkeypatch.setattr(trade_manager_mod, "is_symbol_trade_window_open", lambda symbol: (True, ""))
    monkeypatch.setattr(trade_manager_mod, "save_state", lambda state: None)

    import core.storage_manager as storage_manager

    monkeypatch.setattr(storage_manager, "get_magic_numbers", lambda: {"manual_magic": 8888})
    mgr = TradeManager.__new__(TradeManager)
    mgr.connector = FakeConnector()
    mgr.checklist = FakeChecklist()
    mgr.log_callback = None
    mgr.state = {
        "trade_tactics": {},
        "entry_exit_tactics": {},
        "initial_r_dist": {},
        "initial_r_usd": {},
        "manual_trades_today": 0,
        "trades_today_count": 0,
    }
    mgr._sync_state_lifecycle = lambda: False
    mgr._get_brain_settings = lambda symbol=None: {
        "risk_tsl": {"bot_tsl": "BE+STEP_R+SWING"},
        "dca_config": {"ENABLED": True},
        "pca_config": {"ENABLED": False},
        # RISK_GATE tắt (0): fixture equity 1000 tí hon so với contract_size 100k —
        # các test này kiểm tra tactic/checklist, không kiểm tra van risk.
        "bot_safeguard": {"CLOSE_ON_REVERSE": True, "RISK_GATE_MAX_PCT_PS": 0.0, "RISK_GATE_MAX_PCT_CS": 0.0},
        "entry_exit": {"enabled": True, "active_tactics": ["FALLBACK_R"], "exit_tactic": "AUTO"},
    }
    mgr.log = lambda *args, **kwargs: None
    return mgr


def test_execute_telegram_sandbox_order_success_uses_sandbox_tactic(monkeypatch):
    mgr = _manager(monkeypatch)

    result = mgr.execute_telegram_sandbox_order("VN30F1M", "BUY", 1.0, 1980.0, 2050.0)

    assert result == "SUCCESS|12345"
    assert mgr.connector.orders == [("VN30F1M", 0, 1.0, 1980.0, 2050.0, 8888, "[USER]_TELEGRAM")]
    assert mgr.state["trade_tactics"]["12345"] == "BE+STEP_R+SWING+AUTO_DCA+REV_C"
    assert mgr.state["entry_exit_tactics"]["12345"] == "FALLBACK_R->AUTO"
    assert mgr.state["manual_trades_today"] == 1


def test_execute_telegram_sandbox_order_rejects_bad_buy_sl(monkeypatch):
    mgr = _manager(monkeypatch)

    result = mgr.execute_telegram_sandbox_order("VN30F1M", "BUY", 1.0, 2010.0, 2050.0)

    assert "BAD_SL" in result
    assert mgr.connector.orders == []


def test_execute_telegram_sandbox_order_can_bypass_checklist(monkeypatch):
    mgr = _manager(monkeypatch)
    mgr.checklist.passed = False

    blocked = mgr.execute_telegram_sandbox_order("VN30F1M", "BUY", 1.0, 1980.0, 2050.0)
    bypassed = mgr.execute_telegram_sandbox_order("VN30F1M", "BUY", 1.0, 1980.0, 2050.0, bypass_checklist=True)

    assert "CHECKLIST" in blocked
    assert bypassed == "SUCCESS|12345"


def test_build_telegram_signal_order_uses_sandbox_defaults(monkeypatch):
    mgr = _manager(monkeypatch)
    context = {
        "atr_G2": 10.0,
        "swing_low_G2": 1980.0,
        "swing_high_G2": 2020.0,
    }

    result = mgr.build_telegram_signal_order("VN30F1M", "BUY", context=context, market_mode="TREND")

    assert result["ok"] is True
    assert result["symbol"] == "VN30F1M"
    assert result["side"] == "BUY"
    assert result["lot"] == 1.0
    assert result["sl"] == 1978.0
    assert result["tp"] == 2033.0
    assert result["tp_source"] == "RR"
    assert result["entry_mode"] == "MARKET"
    assert result["tactic"] == "BE+STEP_R+SWING+AUTO_DCA+REV_C"
    assert result["entry_exit_tactic"] == "FALLBACK_R->FALLBACK_R"
    assert result["risk_amount"] > 0
    assert result["reward_amount"] > 0
    assert "gate_action" in result


def test_build_telegram_signal_order_obeys_bot_entry_exit_wait(monkeypatch):
    mgr = _manager(monkeypatch)
    mgr._get_brain_settings = lambda symbol=None: {
        "risk_tsl": {
            "base_sl": "G2",
            "sl_atr_multiplier": 0.2,
            "bot_tsl": "BE+SWING",
        },
        "bot_safeguard": {
            "BOT_USE_SWING_TP": True,
            "BOT_USE_RR_TP": False,
            "RISK_GATE_MAX_PCT_PS": 0.0,
            "RISK_GATE_MAX_PCT_CS": 0.0,
        },
        "entry_exit": {
            "enabled": True,
            "preview_only": False,
            "active_tactics": ["SWING_REJECTION"],
            "entry_tactics": ["SWING_REJECTION"],
            "exit_tactic": "SWING_REJECTION",
            "sl_mode": "SANDBOX",
            "missing_data_policy": "BLOCK",
            "swing_rejection": {
                "source_group": "G2",
                "max_atr_from_swing": 0.7,
                "sl_atr_buffer": 0.2,
            },
        },
    }
    context = {
        "atr_G2": 10.0,
        "swing_low_G2": 1980.0,
        "swing_high_G2": 2020.0,
    }

    result = mgr.build_telegram_signal_order(
        "VN30F1M", "BUY", context=context, market_mode="TREND"
    )

    assert result["ok"] is False
    assert result["error"].startswith("ENTRY_EXIT_WAIT|")


def test_strict_risk_cost_includes_both_sides_and_spread(monkeypatch):
    mgr = _manager(monkeypatch)
    info = SimpleNamespace(spread=0.5, trade_contract_size=100000.0)

    cost = mgr._estimate_round_trip_cost_per_unit(
        "VN30F1M", 2000.0, 1980.0, "BUY", info
    )

    assert cost == 50030.0
    assert mgr.connector.fee_calls == [
        ("VN30F1M", 2000.0, 1.0, "BUY"),
        ("VN30F1M", 1980.0, 1.0, "SELL"),
    ]
