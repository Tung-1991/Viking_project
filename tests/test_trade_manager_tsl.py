# -*- coding: utf-8 -*-
import time
from types import SimpleNamespace

import pytest

import core.trade_manager as trade_manager_module
import config
from core.storage_manager import apply_state_defaults
from core.trade_manager import TradeManager


class _Connector:
    def __init__(self):
        self.modified = []

    def get_account_info(self):
        return {"balance": 1000.0, "equity": 1000.0}

    def modify_position(self, pos, sl, tp):
        self.modified.append((pos.ticket, sl, tp))
        return SimpleNamespace(ok=True, message="MODIFIED", error="")


def test_stale_paper_ticket_does_not_create_fake_pnl(monkeypatch):
    class PaperConnector:
        def get_all_open_positions(self):
            return []

        def get_paper_closed_trade(self, _ticket):
            return None

        def get_account_info(self):
            return {"balance": 100000000.0, "equity": 100000000.0, "realized_pnl": 0.0}

    manager = TradeManager.__new__(TradeManager)
    manager.connector = PaperConnector()
    manager.checklist = None
    manager.log_callback = None
    manager.log = lambda *args, **kwargs: None
    manager._sync_state_lifecycle = lambda: False
    manager.state = apply_state_defaults(
        {
            "date": "2026-07-20",
            "pnl_today": -62000000.0,
            "active_trades": ["PAPER-OLD"],
            "trade_symbols": {"PAPER-OLD": "AAA"},
            "trade_directions": {"PAPER-OLD": "BUY"},
            "trade_volumes": {"PAPER-OLD": 100.0},
            "trade_prices": {"PAPER-OLD": 7.0},
            "trade_magics": {},
        }
    )
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)

    manager.update_running_trades()

    assert manager.state["pnl_today"] == 0.0
    assert manager.state["active_trades"] == []


def test_paper_closed_receipt_is_written_to_detailed_history(monkeypatch):
    calls = []
    manager = TradeManager.__new__(TradeManager)
    manager.state = {
        "current_session_id": "20260722_090000",
        "trade_sl": {"PAPER-3": 17.3},
        "trade_tp": {"PAPER-3": 31.1},
        "trade_excursions": {"PAPER-3": {"mae": -320000.0, "mfe": 10000.0}},
    }
    monkeypatch.setattr(trade_manager_module, "append_trade_log", lambda *args, **kwargs: calls.append((args, kwargs)))

    ok = manager._record_closed_trade_history(
        "PAPER-3",
        "MBS",
        "BUY",
        100,
        21.9,
        -313711.5,
        "MANUAL_CLOSE",
        {
            "price_open": 21.9,
            "price_close": 18.8,
            "profit": -313711.5,
            "fee": 3711.5,
            "open_time": 1783562478.0,
        },
    )

    assert ok is True
    args, kwargs = calls[0]
    assert args[0:5] == ("PAPER-3", "MBS", "BUY", 100.0, 21.9)
    assert args[7] == -3711.5
    assert args[8] == -310000.0
    assert kwargs["market_mode"] == "PAPER"
    assert kwargs["mae_usd"] == -320000.0
    assert kwargs["mfe_usd"] == 10000.0
    assert kwargs["exit_price"] == 18.8


def test_real_closed_receipt_uses_dnse_position_prices_and_fee_profile():
    manager = TradeManager.__new__(TradeManager)
    manager.state = {"trade_excursions": {"R1": {"mae_usd": -50000.0, "mfe_usd": 30000.0}}}
    manager.connector = SimpleNamespace(
        get_position_detail=lambda ticket, symbol: {
            "id": ticket,
            "status": "CLOSED",
            "averageCostPrice": 1900.0,
            "averageClosePrice": 1902.0,
            "closedQuantity": 1,
            "createdDate": "2026-07-22T02:00:00Z",
            "modifiedDate": "2026-07-22T03:00:00Z",
        },
        calculate_profit=lambda *_args: 200000.0,
        calculate_trade_fee=lambda _symbol, _price, _volume, side=None: 1000.0 if side == "BUY" else 1500.0,
    )

    receipt = manager._build_real_closed_receipt("R1", "VN30F1M", "BUY", 1, 1900.0)

    assert receipt["price_open"] == 1900.0
    assert receipt["price_close"] == 1902.0
    assert receipt["fee"] == 2500.0
    assert receipt["profit"] == 197500.0
    assert receipt["history_source"] == "DNSE_POSITION_DETAIL"
    assert receipt["mae"] == -50000.0
    assert receipt["mfe"] == 197500.0


def test_external_partial_close_updates_current_volume_and_keeps_tsl(
    monkeypatch,
):
    position = SimpleNamespace(
        ticket=7788,
        symbol="VN30F1M",
        type=0,
        price_open=1900.0,
        price_current=1901.0,
        sl=1895.0,
        tp=1910.0,
        volume=4.0,
        profit=400000.0,
        swap=0.0,
        commission=0.0,
        time=time.time() - 300,
        magic=0,
        comment="",
    )

    class Connector:
        def get_all_open_positions(self):
            return [position]

        def _is_paper_mode(self):
            return False

        def get_account_info(self):
            return {"balance": 100000000.0, "equity": 100000000.0}

    applied = []
    manager = TradeManager.__new__(TradeManager)
    manager.connector = Connector()
    manager.checklist = None
    manager.log_callback = None
    manager.state = apply_state_defaults(
        {
            "active_trades": ["7788"],
            "trade_symbols": {"7788": "VN30F1M"},
            "trade_directions": {"7788": "BUY"},
            "trade_volumes": {"7788": 5.0},
            "trade_current_volumes": {"7788": 5.0},
            "trade_prices": {"7788": 1900.0},
            "trade_tactics": {"7788": "BE+SWING"},
            "trade_excursions": {"7788": {}},
        }
    )
    manager._sync_state_lifecycle = lambda: False
    manager._market_data_state = lambda: "HEALTHY"
    manager._market_pause_warning_due = lambda *_args: False
    manager._update_trade_excursion = lambda _pos: {}
    manager._apply_independent_tsl = lambda pos, _ctx: (
        applied.append(pos.volume) or "Running"
    )
    manager._check_anti_cash = lambda _pos: None
    manager._check_recovery = lambda _pos, _ctx: None
    manager._get_brain_settings = lambda _symbol=None: {
        "risk_tsl": {},
        "symbol_configs": {},
        "bot_safeguard": {},
    }
    manager.get_trade_tactic = lambda _ticket: "BE+SWING"
    manager.log = lambda *_args, **_kwargs: None
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    monkeypatch.setattr(
        "telegram_notify.position_alerts.observe_position",
        lambda *_args, **_kwargs: None,
    )

    manager.update_running_trades(
        all_market_contexts={"VN30F1M": {"current_price": 1901.0}}
    )

    assert manager.state["trade_volumes"]["7788"] == 5.0
    assert manager.state["trade_current_volumes"]["7788"] == 4.0
    assert applied == [4.0]


def _position(**updates):
    values = {
        "ticket": 7,
        "symbol": "VN30F1M",
        "type": 0,
        "price_open": 100.0,
        "price_current": 103.0,
        "sl": 99.0,
        "tp": 110.0,
        "volume": 1.0,
        "profit": 30.0,
        "swap": 0.0,
        "commission": 0.0,
        "time": time.time() - 600,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _manager(tactic, tsl_config=None, risk_tsl=None):
    manager = TradeManager.__new__(TradeManager)
    manager.connector = _Connector()
    manager.state = {
        "exit_reasons": {},
        "initial_costs": {},
        "trade_excursions": {},
        "be_sl_arms": {},
    }
    manager.get_trade_tactic = lambda _ticket: tactic
    manager._get_symbol_info = lambda _symbol: SimpleNamespace(
        point=0.1,
        spread=0.0,
        trade_contract_size=100.0,
        trade_stops_level=0,
    )
    manager._get_ticket_r_dist = lambda _pos: 1.0
    manager._get_ticket_risk_usd = lambda _pos: 100.0
    manager._position_profit_usd = lambda pos: pos.profit + pos.swap + pos.commission
    manager._get_brain_settings = lambda _symbol=None: {
        "TSL_CONFIG": tsl_config or {},
        "risk_tsl": risk_tsl or {},
        "TSL_LOGIC_MODE": "STATIC",
        "bot_safeguard": {},
    }
    manager.log = lambda *args, **kwargs: None
    return manager


@pytest.mark.parametrize(
    ("tactic", "cfg", "context", "expected_sl", "label"),
    [
        ("STEP_R", {"STEP_R_SIZE": 1.0, "STEP_R_RATIO": 0.8}, {}, 102.4, "STEP 3"),
        (
            "PSAR_TRAIL",
            {"PSAR_GROUP": "G2", "PSAR_PROFIT_ONLY": True, "PSAR_MIN_RR": 0.0},
            {"psar_G2": 102.0},
            102.0,
            "PSAR",
        ),
        (
            "SWING",
            {"SWING_GROUP": "G2"},
            {"swing_high_G2": 104.0, "swing_low_G2": 101.0, "atr_G2": 1.0, "market_mode": "TREND"},
            100.8,
            "SWING",
        ),
        (
            "PNL",
            {"PNL_LEVELS": [[1.0, 0.5]]},
            {},
            100.0,
            "PNL 0.5%",
        ),
        (
            "BE_CASH",
            {
                "BE_CASH_TYPE": "USD",
                "BE_TRIGGER": 10.0,
                "BE_VALUE": 20.0,
                "BE_CASH_FEE_PROTECT": False,
                "BE_CASH_STRAT": "TRAILING (Gap)",
            },
            {},
            100.1,
            "CASH",
        ),
    ],
)
def test_independent_tsl_modes_move_stop(monkeypatch, tactic, cfg, context, expected_sl, label):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    manager = _manager(tactic, cfg, {"sl_atr_multiplier": 0.2})
    result = manager._apply_independent_tsl(_position(profit=30.0), context)

    assert manager.connector.modified[0][1] == pytest.approx(expected_sl)
    assert label in result


def test_be_loss_recovery_arms_before_cutting(monkeypatch):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    manager = _manager(
        "BE",
        {
            "BE_SL_LOSS_ENABLE": True,
            "BE_SL_LOSS_TRIGGER": 0.5,
            "BE_SL_LOSS_STEP": 0.15,
            "BE_SL_GUARD_BUFFER": 0.05,
            "BE_SL_LOSS_UNIT": "R",
        },
    )
    result = manager._apply_independent_tsl(_position(profit=-60.0, price_current=99.4), {})

    assert "7" in manager.state["be_sl_arms"]
    assert "BE_SL armed" in result
    assert manager.connector.modified == []


def test_be_moves_stop_to_break_even_after_profit_trigger(monkeypatch):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    manager = _manager(
        "BE",
        {
            "BE_SL_LOSS_ENABLE": False,
            "BE_OFFSET_RR": 0.8,
            "BE_OFFSET_POINTS": 0,
        },
    )

    result = manager._apply_independent_tsl(
        _position(price_current=101.0, profit=100.0),
        {},
    )

    assert manager.state["be_sl_arms"] == {}
    assert manager.connector.modified[0][1] == pytest.approx(100.0)
    assert "BE_SL" in result


@pytest.mark.parametrize(
    ("position_type", "current_price"),
    [
        (0, 100.5),
        (1, 99.5),
    ],
)
def test_be_status_stays_locked_after_price_retraces_below_trigger(
    monkeypatch,
    position_type,
    current_price,
):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    manager = _manager(
        "BE",
        {
            "BE_SL_LOSS_ENABLE": False,
            "BE_OFFSET_RR": 0.8,
            "BE_OFFSET_POINTS": 0,
        },
    )

    result = manager._apply_independent_tsl(
        _position(
            type=position_type,
            price_current=current_price,
            sl=100.0,
            profit=50.0,
        ),
        {},
    )

    assert result == "BE ĐÃ KHÓA @100.00"
    assert "Đợi" not in result
    assert manager.connector.modified == []


def test_tsl_does_not_claim_success_when_broker_rejects(monkeypatch):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    manager = _manager(
        "BE",
        {
            "BE_SL_LOSS_ENABLE": False,
            "BE_OFFSET_RR": 0.8,
            "BE_OFFSET_POINTS": 0,
        },
    )
    manager.connector.modify_position = lambda *_args: SimpleNamespace(
        ok=False,
        message="DNSE rejected",
        error="REJECTED",
    )

    result = manager._apply_independent_tsl(
        _position(price_current=101.0, profit=100.0),
        {},
    )

    assert "DNSE TỪ CHỐI" in result
    assert manager.state.get("last_tsl_rules", {}) == {}


class _ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def test_anti_cash_hard_stop_closes_position(monkeypatch):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    monkeypatch.setattr(trade_manager_module.threading, "Thread", _ImmediateThread)
    manager = _manager(
        "ANTI_CASH",
        {
            "ANTI_CASH_USD": 10.0,
            "ANTI_CASH_HARD_STOP_UNIT": "USD",
            "ANTI_CASH_REENTRY_LOCK_SEC": 0,
            "ANTI_CASH_MFE_ENABLE": False,
            "ANTI_CASH_MAE_ENABLE": False,
            "ANTI_CASH_TIME_ENABLE": False,
        },
    )
    manager.state["initial_costs"]["7"] = 0.0
    manager.state["trade_excursions"]["7"] = {"mae_usd": -15.0, "mfe_usd": 0.0}
    closed = []
    manager._close_with_t2_log = lambda pos, reason: closed.append((pos.ticket, reason))

    manager._check_anti_cash(_position(profit=-15.0, price_current=99.0))

    assert manager.state["exit_reasons"]["7"] == "Anti_Cash_Hard_Stop"
    assert closed == [(7, "Anti_Cash_Hard_Stop")]


def test_reverse_signal_closes_after_configured_confirmation(monkeypatch):
    monkeypatch.setattr(trade_manager_module, "save_state", lambda _state: None)
    monkeypatch.setattr(trade_manager_module.threading, "Thread", _ImmediateThread)
    manager = _manager("REV_C")
    manager._get_brain_settings = lambda _symbol=None: {
        "bot_safeguard": {
            "CLOSE_ON_REVERSE_MIN_TIME": 0,
            "REV_CONFIRM_SECONDS": 0,
            "REV_CONFIRM_SCANS": 1,
            "CLOSE_ON_REVERSE_USE_PNL": False,
        }
    }
    closed = []
    manager._close_with_t2_log = lambda pos, reason: closed.append((pos.ticket, reason))

    manager._check_recovery(_position(profit=1.0), {"latest_signal": -1, "timestamp": time.time()})

    assert manager.state["exit_reasons"]["7"] == "Recovery_Close"
    assert closed == [(7, "Reverse_Close")]
