from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from viking_v2 import config
from viking_v2.connections.dnse.paper import PaperBroker
from viking_v2.trading.execution import ExecutionService
from viking_v2.models import OrderIntent
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import available_to_sell, price_in_band, validate_quantity
from viking_v2.storage import JSONLineJournal


def test_stock_lot_and_price_band_rules():
    assert validate_quantity(100)[0]
    assert not validate_quantity(50)[0]
    assert not validate_quantity(150)[0]
    assert price_in_band(100, 90, 110)
    assert not price_in_band(89.9, 90, 110)


def test_sellable_quantity_never_guesses_from_open_quantity():
    assert available_to_sell([{"symbol": "FPT", "openQuantity": 100}], "FPT") == 0
    assert available_to_sell([
        {"symbol": "FPT", "openQuantity": 200, "tradeQuantity": 100},
    ], "FPT") == 100


def test_paper_buy_fills_and_is_t_plus_locked(tmp_path):
    broker = PaperBroker(tmp_path / "paper.json", tick_provider=lambda _symbol: {"price": 100, "ask": 100, "bid": 99})
    result = broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET"))
    assert result.ok and result.status == "FILLED"
    position = broker.get_positions()[0]
    assert position["openQuantity"] == 100
    assert position["tradeQuantity"] == 0
    assert available_to_sell(broker.get_positions(), "FPT") == 0


def test_paper_does_not_allow_short_sale(tmp_path):
    broker = PaperBroker(tmp_path / "paper.json", tick_provider=lambda _symbol: {"price": 100, "bid": 99})
    result = broker.place_order(OrderIntent.create("FPT", "SELL", 100, "MARKET"))
    assert not result.ok
    assert result.error == "INSUFFICIENT_SELLABLE"


def test_two_paper_accounts_do_not_share_state(tmp_path):
    first = PaperBroker(tmp_path / "a.json", tick_provider=lambda _symbol: {"price": 10})
    second = PaperBroker(tmp_path / "b.json", tick_provider=lambda _symbol: {"price": 10})
    assert first.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET")).ok
    assert first.get_positions()
    assert second.get_positions() == []


def test_paper_same_price_round_trip_is_loss_after_fee_and_tax(tmp_path):
    now = [1_700_000_000.0]
    broker = PaperBroker(
        tmp_path / "paper.json",
        tick_provider=lambda _symbol: {"price": 100, "ask": 100, "bid": 100},
        now=lambda: now[0],
    )
    assert broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET")).ok
    state = broker.store.read()
    state["positions"][0]["tradeQuantity"] = 100
    broker.store.write(state)
    assert broker.place_order(OrderIntent.create("FPT", "SELL", 100, "MARKET")).ok
    assert broker.get_balance()["realizedPnl"] < 0


def test_paper_nav_marks_open_position_to_latest_price(tmp_path):
    tick = {"ask": 100.0, "price": 100.0}
    broker = PaperBroker(
        tmp_path / "paper-mark.json",
        initial_balance=100_000_000,
        tick_provider=lambda _symbol: dict(tick),
    )
    assert broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET")).ok
    bought = broker.get_balance()["equity"]
    tick.update(price=110.0)
    marked = broker.get_balance()["equity"]
    assert marked - bought == 1_000_000
    assert broker.get_positions()[0]["marketPrice"] == 110.0


def test_paper_partial_sell_allocates_buy_fee_only_once(tmp_path):
    now = [1_700_000_000.0]
    broker = PaperBroker(
        tmp_path / "paper-partial.json",
        initial_balance=100_000_000,
        tick_provider=lambda _symbol: {"ask": 100.0, "bid": 110.0, "price": 110.0},
        now=lambda: now[0],
    )
    assert broker.place_order(OrderIntent.create("FPT", "BUY", 200, "MARKET")).ok
    state = broker.store.read()
    original_buy_fee = state["positions"][0]["buyFee"]
    state["positions"][0]["tradeQuantity"] = 200
    broker.store.write(state)

    assert broker.place_order(OrderIntent.create("FPT", "SELL", 100, "MARKET")).ok
    remaining = broker.get_positions()[0]
    assert remaining["openQuantity"] == 100
    assert remaining["buyFee"] == original_buy_fee / 2
    expected = (
        (110 - 100) * 100 * 1000
        - original_buy_fee / 2
        - 110 * 100 * 1000 * (config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE)
    )
    assert broker.get_balance()["realizedPnl"] == expected


def test_paper_t2_uses_dnse_working_dates_when_available(tmp_path):
    broker = PaperBroker(
        tmp_path / "paper.json",
        tick_provider=lambda _symbol: {"ask": 100},
        now=lambda: 1_786_377_600.0,  # 2026-08-10 local date
        working_dates_provider=lambda: ["2026-08-11", "2026-08-13", "2026-08-14"],
    )
    assert broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET")).ok
    assert broker.get_positions()[0]["settleDate"] == "2026-08-13"


def test_paper_buy_fails_closed_when_dnse_calendar_has_no_t2_dates(tmp_path):
    broker = PaperBroker(
        tmp_path / "paper-calendar-missing.json",
        tick_provider=lambda _symbol: {"ask": 100},
        working_dates_provider=lambda: [],
    )
    result = broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET"))
    assert not result.ok
    assert result.error == "TRADING_CALENDAR_UNAVAILABLE"
    assert broker.get_positions() == []


def test_paper_t2_skips_holiday_and_releases_at_afternoon_session(tmp_path):
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now = [datetime(2026, 8, 10, 10, 0, tzinfo=vn_tz).timestamp()]
    broker = PaperBroker(
        tmp_path / "paper-t2-time.json",
        tick_provider=lambda _symbol: {"ask": 100},
        now=lambda: now[0],
        # 12/08 is deliberately absent: it is a non-trading day in this test.
        working_dates_provider=lambda: ["2026-08-11", "2026-08-13", "2026-08-14"],
    )
    assert broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET")).ok
    assert broker.get_positions()[0]["settleDate"] == "2026-08-13"

    now[0] = datetime(2026, 8, 13, 12, 59, 59, tzinfo=vn_tz).timestamp()
    assert broker.get_positions()[0]["tradeQuantity"] == 0
    now[0] = datetime(2026, 8, 13, 13, 0, 0, tzinfo=vn_tz).timestamp()
    assert broker.get_positions()[0]["tradeQuantity"] == 100


def test_execution_rejects_odd_lot_before_queue_release(tmp_path):
    broker = PaperBroker(tmp_path / "paper.json", tick_provider=lambda _symbol: {"price": 10})
    queue = OrderQueue(tmp_path / "orders.json")
    service = ExecutionService(object(), broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    result = service.submit(OrderIntent.create("FPT", "BUY", 150, "MARKET"), phase="CLOSED")
    assert result.status == "REJECTED"
    assert queue.get(result.id).status == "REJECTED"
