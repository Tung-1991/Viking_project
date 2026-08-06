# -*- coding: utf-8 -*-
from types import SimpleNamespace

import config
from core.dnse_connector import ORDER_TYPE_BUY, ORDER_TYPE_SELL
from core.paper_broker import PaperBroker


def test_paper_open_modify_close_buy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PAPER_INITIAL_BALANCE", 100000000.0)
    monkeypatch.setattr(config, "DNSE_BROKER_FEE_PER_CONTRACT", 1000.0)
    ticks = {"price": 1000.0}

    def tick_provider(symbol):
        price = ticks["price"]
        return SimpleNamespace(symbol=symbol, bid=price - 0.1, ask=price + 0.1, last=price, spread=0.2)

    broker = PaperBroker("ACC1", tick_provider=tick_provider)
    result = broker.place_order("VN30F1M", "BUY", 2, sl=995, tp=1010)

    assert result.ok is True
    assert len(broker.get_positions()) == 1

    broker.modify_position(result.position_id, sl=998, tp=1005)
    ticks["price"] = 1002.0
    pos = broker.get_positions()[0]

    assert pos.type == ORDER_TYPE_BUY
    assert pos.sl == 998
    assert pos.tp == 1005
    assert pos.profit > 0

    close = broker.close_position(pos)
    account = broker.get_account_info()

    assert close.ok is True
    assert broker.get_positions() == []
    assert account["balance"] > 100000000.0
    closed = broker.get_closed_trade(result.position_id)
    assert closed["symbol"] == "VN30F1M"
    assert closed["profit"] > 0
    assert closed["type"] == ORDER_TYPE_BUY
    assert closed["fee"] > 0
    assert closed["open_time"] > 0
    assert closed["price_close"] == pos.price_current
    assert closed["mae"] <= closed["profit"]
    assert broker.get_closed_trades()[0]["ticket"] == result.position_id
    day = __import__("datetime").datetime.fromtimestamp(closed["closed_at"]).strftime("%Y-%m-%d")
    assert broker.delete_closed_trades_for_day(day) == 1
    assert broker.get_closed_trades() == []


def test_paper_sell_stop_take_profit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PAPER_INITIAL_BALANCE", 100000000.0)
    ticks = {"price": 1000.0}

    def tick_provider(symbol):
        price = ticks["price"]
        return SimpleNamespace(symbol=symbol, bid=price - 0.1, ask=price + 0.1, last=price, spread=0.2)

    broker = PaperBroker("ACC1", tick_provider=tick_provider)
    result = broker.place_order("VN30F1M", "SELL", 1, sl=1005, tp=995)
    ticks["price"] = 994.9

    positions = broker.get_positions()
    account = broker.get_account_info()

    assert result.ok is True
    assert positions == []
    assert account["realized_pnl"] > 0


def test_paper_fee_profile_includes_dnse_fixed_and_tax(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PAPER_INITIAL_BALANCE", 100000000.0)
    monkeypatch.setattr(config, "DNSE_BROKER_FEE_PER_CONTRACT", 1000.0)
    monkeypatch.setattr(config, "DNSE_EXCHANGE_FEE_PER_CONTRACT", 2700.0)
    monkeypatch.setattr(config, "DNSE_CLEARING_FEE_PER_CONTRACT", 2550.0)
    monkeypatch.setattr(config, "DNSE_TAX_RATE", 0.0005)
    monkeypatch.setattr(config, "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", 0.20)

    ticks = {"price": 1000.0}

    def tick_provider(symbol):
        price = ticks["price"]
        return SimpleNamespace(symbol=symbol, bid=price, ask=price, last=price, spread=0.0)

    broker = PaperBroker("ACC1", tick_provider=tick_provider)
    result = broker.place_order("VN30F1M", "BUY", 2)
    assert result.ok is True

    ticks["price"] = 1002.0
    pos = broker.get_positions()[0]

    fixed_each_side = 2 * (1000.0 + 2700.0 + 2550.0)
    open_tax = 1000.0 * 2 * 100000.0 * 0.20 / 2 * 0.0005
    close_tax = 1002.0 * 2 * 100000.0 * 0.20 / 2 * 0.0005
    expected = (1002.0 - 1000.0) * 2 * 100000.0 - fixed_each_side * 2 - open_tax - close_tax

    assert pos.commission == fixed_each_side * 2 + open_tax + close_tax
    assert pos.profit == expected


def _stock_broker(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PAPER_INITIAL_BALANCE", 100000000.0)
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])

    def tick_provider(symbol):
        return SimpleNamespace(symbol=symbol, bid=73.2, ask=73.3, last=73.25, spread=0.1)

    return PaperBroker("ACC1", tick_provider=tick_provider)


def test_stock_buy_tags_settle_date_and_blocks_early_sell(monkeypatch, tmp_path):
    broker = _stock_broker(monkeypatch, tmp_path)
    res = broker.place_order("FPT", "BUY", 100, sl=72.0, tp=75.0)
    assert res.ok is True
    pos = broker.state["positions"][0]
    assert pos["settle_date"]  # cổ phiếu phải có ngày về T+2
    assert broker.available_to_sell("FPT") == 0  # chưa về
    assert broker.pending_to_settle("FPT") == 100

    # Đóng/bán khi chưa về -> bị chặn
    close = broker.close_position(pos["ticket"])
    assert close.ok is False
    assert close.error == "STOCK_NOT_SETTLED_T2"
    sell = broker.place_order("FPT", "SELL", 100)
    assert sell.ok is False
    assert sell.error == "STOCK_NOT_SETTLED_T2"


def test_stock_settled_can_close(monkeypatch, tmp_path):
    broker = _stock_broker(monkeypatch, tmp_path)
    broker.place_order("FPT", "BUY", 100, sl=72.0, tp=75.0)
    # Giả lập CK đã về (settle_date quá khứ)
    broker.state["positions"][0]["settle_date"] = "2000-01-01"
    assert broker.available_to_sell("FPT") == 100
    close = broker.close_position(broker.state["positions"][0]["ticket"])
    assert close.ok is True
    assert broker.get_positions() == []


def test_stock_sell_without_holding_is_blocked(monkeypatch, tmp_path):
    broker = _stock_broker(monkeypatch, tmp_path)
    sell = broker.place_order("FPT", "SELL", 100)
    assert sell.ok is False  # không bán khống cổ phiếu


def test_derivative_not_affected_by_t2(monkeypatch, tmp_path):
    broker = _stock_broker(monkeypatch, tmp_path)
    buy = broker.place_order("VN30F1M", "BUY", 1, sl=1900, tp=2000)
    assert buy.ok is True
    assert broker.state["positions"][0]["settle_date"] == ""  # phái sinh không có T+2
    close = broker.close_position(broker.state["positions"][0]["ticket"])
    assert close.ok is True  # phái sinh đóng được ngay
