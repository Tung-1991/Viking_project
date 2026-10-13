"""Technical notices distinguish held stock, queued exits and actual fills offline."""
from copy import deepcopy

import pytest

from viking_v2.models import OrderIntent, TradeCycle
from viking_v2.services.position_notifications import position_notice_context, position_notice_lines


def cycle(*, sold=0, mode="REAL", package=""):
    return TradeCycle(id="HDB-CYCLE", symbol="HDB", execution_mode=mode,
                      entry_quantity=600, open_quantity=600-sold, sold_quantity=sold,
                      loan_package_id=package, status="CLOSED" if sold == 600 else "OPEN")


def sell(*, status="WAITING_SETTLEMENT", filled=0, mode="REAL"):
    return OrderIntent(id="SELL-HDB", symbol="HDB", side="SELL", quantity=100,
                       filled_quantity=filled, order_type="MARKET", execution_mode=mode,
                       trade_id="HDB-CYCLE", action="CLOSE", status=status,
                       details={"sell_share_pct": 33})


@pytest.mark.parametrize("sold,status,filled,sellable,expected", [
    (0, "WAITING_SETTLEMENT", 0, 0,
     ("đang giữ 600 CP", "Đã bán khớp 0 CP", "Chờ cổ về 600 CP", "SELL còn chờ 100 CP", "CHỜ CỔ VỀ")),
    (100, "PARTIAL", 50, 500,
     ("đang giữ 500 CP", "Đã bán khớp 100 CP (16.67%", "SELL còn chờ 50 CP", "KHỚP MỘT PHẦN")),
    (600, "FILLED", 100, 600,
     ("đang giữ 0 CP", "Đã bán khớp 600 CP (100.00%",)),
])
def test_notice_never_counts_an_exit_request_as_a_sale(sold, status, filled, sellable, expected):
    trade, intent = cycle(sold=sold), sell(status=status, filled=filled)
    positions = [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": sellable}]
    before = deepcopy((trade.to_dict(), intent.to_dict(), positions))
    context = position_notice_context("HDB", "REAL", cycles=[trade], positions=positions, intents=[intent])
    text = "\n".join(position_notice_lines(context))
    assert all(value in text for value in expected)
    assert ("Tỷ lệ yêu cầu 33%" in text) == (status != "FILLED")
    assert "Đã bán khớp 198" not in text
    assert (trade.to_dict(), intent.to_dict(), positions) == before


def test_cancelled_exit_is_absent_and_other_book_cannot_pollute_position():
    context = position_notice_context("HDB", "REAL", cycles=[cycle(), cycle(sold=600, mode="PAPER")],
        intents=[sell(status="CANCELLED"), sell(mode="PAPER")], positions=[])
    text = "\n".join(position_notice_lines(context))
    assert "đang giữ 600 CP" in text and "Đã bán khớp 0 CP" in text
    assert "SELL còn chờ" not in text


def test_sellability_is_not_borrowed_from_another_loan_package():
    context = position_notice_context("HDB", "REAL", cycles=[cycle(package="OWN")], positions=[
        {"symbol": "HDB", "loanPackageId": "OWN", "openQuantity": 600, "tradeQuantity": 0},
        {"symbol": "HDB", "loanPackageId": "OTHER", "openQuantity": 900, "tradeQuantity": 900},
    ])
    assert context["sellable_quantity"] == 0
    assert "Chờ cổ về 600 CP" in "\n".join(position_notice_lines(context))


@pytest.mark.parametrize("positions", [None, [], [{"symbol": "HDB", "openQuantity": 600}],
    [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": None}],
    [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": "bad"}],
    [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": float("nan")}],
])
def test_missing_snapshot_does_not_invent_a_settlement_status(positions):
    context = position_notice_context("HDB", "REAL", cycles=[cycle()], positions=positions)
    text = "\n".join(position_notice_lines(context))
    assert "đang giữ 600 CP" in text
    assert "Bán được" not in text and "Chờ cổ về" not in text
    assert position_notice_context("HDB", "REAL") is None


def test_broker_only_position_does_not_invent_past_sales():
    context = position_notice_context("HDB", "REAL", positions=[
        {"symbol": "HDB", "openQuantity": 600, "tradeQuantity": 0},
    ])
    text = "\n".join(position_notice_lines(context))
    assert "đang giữ 600 CP" in text and "Chờ cổ về 600 CP" in text
    assert "Đã bán khớp" not in text
