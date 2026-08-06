# -*- coding: utf-8 -*-
from types import SimpleNamespace

import core.trade_manager as trade_manager_module
from core.trade_manager import TradeManager


class DummyChecklist:
    def run_bot_safeguard_checks(self, *args, **kwargs):
        return {"passed": True, "checks": []}

    def run_pre_trade_checks(self, *args, **kwargs):
        return {"passed": True, "checks": []}


class DummyConnector:
    def __init__(self, positions=None):
        self.positions = positions or []
        self.orders = []
        self.closed = []

    def get_account_info(self):
        return {"balance": 100000000.0, "equity": 100000000.0}

    def get_all_open_positions(self):
        return list(self.positions)

    def get_tick(self, symbol):
        return SimpleNamespace(symbol=symbol, bid=1209.0, ask=1210.0, last=1210.0, spread=1.0)

    def get_symbol_info(self, symbol):
        return SimpleNamespace(
            symbol=symbol,
            point=0.1,
            trade_contract_size=100000.0,
            volume_min=1.0,
            volume_max=200.0,
            volume_step=1.0,
            trade_stops_level=0.0,
            spread=1.0,
        )

    def calculate_profit(self, symbol, side, volume, entry_price, exit_price):
        direction = 1 if str(side).upper() in ("0", "BUY", "NB") else -1
        return (float(exit_price) - float(entry_price)) * direction * float(volume) * 100000.0

    def close_position(self, pos):
        self.closed.append(pos)
        return SimpleNamespace(ok=True, error="", message="")

    def place_order(self, *args, **kwargs):
        self.orders.append((args, kwargs))
        return SimpleNamespace(ok=True, order_id="O1", position_id="P1")


def _manager(monkeypatch, tmp_path, positions=None, brain=None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _symbol: (True, ""))
    # Các test này chỉ kiểm tra long-only/T+2; không để giờ chạy test trước
    # 09:30 làm rule chờ sau ATO che mất kết quả cần kiểm tra.
    monkeypatch.setattr(
        "core.market_calendar.bot_entry_block_reason",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "core.storage_manager.get_magic_numbers",
        lambda: {"bot_magic": 9999, "manual_magic": 8888},
    )
    mgr = TradeManager(DummyConnector(positions), DummyChecklist(), log_callback=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mgr,
        "_get_brain_settings",
        lambda _symbol=None: brain or {"bot_safeguard": {}, "risk_tsl": {}, "entry_exit": {}},
    )
    return mgr


def test_stock_sell_without_settled_long_is_blocked(monkeypatch, tmp_path):
    mgr = _manager(monkeypatch, tmp_path, positions=[])

    result = mgr.execute_bot_trade("SELL", "FPT", {}, signal_class="ENTRY")

    assert result.startswith("SAFEGUARD_FAIL|NO_SETTLED_LONG|")
    assert mgr.connector.orders == []


def test_stock_reverse_close_only_does_not_open_short(monkeypatch, tmp_path):
    pos = SimpleNamespace(
        ticket="P1",
        position_id="P1",
        order_id="O1",
        symbol="FPT",
        type=0,
        volume=100,
        profit=1000.0,
        swap=0.0,
        commission=0.0,
        time=0.0,
        magic=9999,
        comment="[BOT]_AUTO_ENTRY",
        raw={"settle_date": "2000-01-01"},
    )
    mgr = _manager(
        monkeypatch,
        tmp_path,
        positions=[pos],
        brain={"bot_safeguard": {"CLOSE_ON_REVERSE": True, "CLOSE_ON_REVERSE_MIN_TIME": 0}, "risk_tsl": {}, "entry_exit": {}},
    )

    result = mgr.execute_bot_trade("SELL", "FPT", {}, signal_class="ENTRY")

    assert result.startswith("SAFEGUARD_FAIL|REV_CLOSE_ONLY|")
    assert mgr.connector.orders == []


def test_derivative_sell_is_not_blocked_by_stock_guard(monkeypatch, tmp_path):
    mgr = _manager(monkeypatch, tmp_path, positions=[])

    assert mgr._stock_long_only_guard("VN30F1M", "SELL") is None


def _long_pos(symbol, volume, settle_date):
    return SimpleNamespace(
        ticket="P1", position_id="P1", order_id="O1", symbol=symbol, type=0,
        volume=volume, profit=0.0, swap=0.0, commission=0.0, time=0.0, magic=9999,
        comment="[BOT]_AUTO_ENTRY", raw={"settle_date": settle_date},
    )


def test_settled_long_volume_counts_only_arrived_shares(monkeypatch, tmp_path):
    # 100 đã về (settle quá khứ) + 200 chưa về (settle tương lai) -> chỉ 100 bán được.
    positions = [_long_pos("FPT", 100, "2000-01-01"), _long_pos("FPT", 200, "2999-01-01")]
    mgr = _manager(monkeypatch, tmp_path, positions=positions)

    assert mgr._stock_settled_long_volume("FPT") == 100
    # pre-cap dựa trên số này: lệnh bán sẽ bị cắt về 100 nếu tính ra lớn hơn.
    assert mgr._stock_settled_long_volume("VN30F1M") == 0  # phái sinh không tính T+2
