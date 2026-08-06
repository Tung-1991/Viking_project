# -*- coding: utf-8 -*-
import config
import pytest
from core import portfolio as p


def _pos(symbol, open_qty, trade_qty=None, cost=0.0, market=0.0, status="OPEN"):
    """Dựng dict raw kiểu DNSE position (build_holdings đọc trực tiếp dict)."""
    raw = {
        "symbol": symbol,
        "openQuantity": open_qty,
        "costPrice": cost,
        "marketPrice": market,
        "status": status,
    }
    if trade_qty is not None:
        raw["tradeQuantity"] = trade_qty
    return raw


def test_build_holdings_filters_derivative(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    positions = [
        _pos("FPT", 100, cost=120000, market=130000),
        _pos("VN30F1M", 5, cost=1300, market=1320),  # phái sinh -> bỏ
    ]
    rows = p.build_holdings(positions)
    assert [r.symbol for r in rows] == ["FPT"]


def test_build_holding_computes_value_and_pnl(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    [row] = p.build_holdings([_pos("FPT", 100, trade_qty=100, cost=120000, market=130000)])
    assert row.quantity == 100
    assert row.sellable == 100
    assert row.pending == 0
    assert row.market_value == 100 * 130000
    assert row.cost_value == 100 * 120000
    assert row.pnl == 100 * (130000 - 120000)
    assert round(row.pnl_pct, 4) == round((130000 - 120000) / 120000 * 100, 4)
    assert row.is_odd_lot is False


def test_board_price_in_thousand_vnd_is_scaled_for_portfolio_value(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    [row] = p.build_holdings([_pos("AAA", 500, trade_qty=500, cost=7.20, market=7.21)])
    assert row.avg_cost == 7.20
    assert row.market_price == 7.21
    assert row.market_value == 500 * 7.21 * 1000
    assert row.cost_value == 500 * 7.20 * 1000
    assert row.pnl == pytest.approx(5_000)


def test_pending_is_quantity_minus_sellable(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    # Mua 300, mới về 100 (T+2) -> chờ về 200
    [row] = p.build_holdings([_pos("CTG", 300, trade_qty=100, cost=30000, market=31000)])
    assert row.sellable == 100
    assert row.pending == 200


def test_missing_trade_quantity_treated_as_settled(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    # Vị thế cũ mua ngoài bot, không có tradeQuantity -> coi như bán được hết
    [row] = p.build_holdings([_pos("VCB", 200, trade_qty=None, cost=90000, market=95000)])
    assert row.sellable == 200
    assert row.pending == 0


def test_odd_lot_by_status(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    [row] = p.build_holdings([_pos("FPT", 100, cost=120000, market=130000, status="ODD_LOT")])
    assert row.is_odd_lot is True
    # status=ODD_LOT nhưng KL chia hết lô -> coi cả KL là lẻ.
    assert row.odd_quantity == 100
    assert "Lô lẻ" in row.note


def test_odd_lot_by_non_multiple_of_lot(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    # 37 cổ (vd cổ tức) -> lô lẻ dù status không phải ODD_LOT
    [row] = p.build_holdings([_pos("MBB", 37, cost=20000, market=21000)])
    assert row.is_odd_lot is True
    assert row.odd_quantity == 37


def test_odd_quantity_remainder_only(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    # 150 cổ = 100 lô chẵn + 50 lô lẻ -> chỉ 50 là phần kẹt.
    [row] = p.build_holdings([_pos("SSI", 150, cost=20000, market=22000)])
    assert row.is_odd_lot is True
    assert row.odd_quantity == 50


def test_odd_lot_value_subtotal(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    positions = [
        _pos("SSI", 150, cost=20000, market=22000),  # lẻ 50 * 22000 = 1,100,000
        _pos("VNM", 200, cost=60000, market=62000),  # lô chẵn -> 0
        _pos("MBB", 30, cost=20000, market=21000),   # lẻ 30 * 21000 = 630,000
    ]
    summary = p.portfolio_summary(positions, {"cash_available": 0})
    assert summary["assets"]["odd_lot_count"] == 2
    assert summary["assets"]["odd_lot_value"] == 50 * 22000 + 30 * 21000


def test_multiple_symbols_sorted_desc(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    positions = [
        _pos("CTG", 100, cost=30000, market=33000),   # value 3,300,000
        _pos("VNM", 200, cost=60000, market=62000),   # value 12,400,000
    ]
    rows = p.build_holdings(positions)
    # Sắp theo giá trị giảm dần: VNM trước CTG
    assert [r.symbol for r in rows] == ["VNM", "CTG"]


def test_same_symbol_multiple_lots_aggregated(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    # CTG mua 2 lô: 100@30000 (đã về) + 100@32000 (chờ về)
    positions = [
        _pos("CTG", 100, trade_qty=100, cost=30000, market=33000),
        _pos("CTG", 100, trade_qty=0, cost=32000, market=33000),
    ]
    rows = p.build_holdings(positions)
    assert len(rows) == 1                      # gộp 1 dòng
    row = rows[0]
    assert row.symbol == "CTG"
    assert row.quantity == 200
    assert row.sellable == 100                 # chỉ lô đã về bán được
    assert row.pending == 100                  # lô chờ về T+2
    assert row.avg_cost == 31000               # bình quân gia quyền (30000+32000)/2
    assert row.market_value == 200 * 33000


def test_skip_zero_quantity(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    rows = p.build_holdings([_pos("FPT", 0, cost=120000, market=130000)])
    assert rows == []


def test_split_assets_total():
    assets = p.split_assets(300_000_000, 700_000_000)
    assert assets["cash"] == 300_000_000
    assert assets["stock_value"] == 700_000_000
    assert assets["total"] == 1_000_000_000


def test_extract_stock_cash_from_account_info():
    info = {"raw": {"stock": {"availableCash": 300_000_000, "totalCash": 320_000_000}}}
    assert p.extract_stock_cash(info) == 300_000_000


def test_extract_stock_account_full_fields():
    info = {"raw": {"stock": {
        "totalCash": 320_000_000, "availableCash": 350_000_000,
        "totalDebt": 30_000_000, "cashDividendReceiving": 2_000_000,
    }}}
    a = p.extract_stock_account(info)
    assert a["total_cash"] == 320_000_000      # tiền mặt thật
    assert a["available_cash"] == 350_000_000  # sức mua > tiền (có vay)
    assert a["debt"] == 30_000_000             # nợ vay
    assert a["dividend"] == 2_000_000          # cổ tức sắp về


def test_portfolio_summary_nav_minus_debt(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    account = {"raw": {"stock": {
        "totalCash": 10_000_000, "availableCash": 10_000_000,
        "totalDebt": 3_000_000, "cashDividendReceiving": 500_000,
    }}}
    positions = [_pos("CTG", 100, cost=30000, market=33000)]  # CP = 3.3tr
    s = p.portfolio_summary(positions, account)
    a = s["assets"]
    # NAV = tiền mặt + CP − nợ = 10tr + 3.3tr − 3tr = 10.3tr
    assert a["total"] == 10_000_000 + 100 * 33000 - 3_000_000
    assert a["cash"] == 10_000_000
    assert a["debt"] == 3_000_000
    assert a["dividend"] == 500_000


def test_extract_stock_cash_fallback_total_cash():
    info = {"raw": {"stock": {"totalCash": 250_000_000}}}
    assert p.extract_stock_cash(info) == 250_000_000


def test_extract_stock_cash_paper_flat_dict():
    # PAPER: get_account_info trả dict phẳng (không có raw.stock) -> dùng cash_available.
    info = {"login": "PAPER", "balance": 100_000_000, "cash_available": 100_000_000}
    assert p.extract_stock_cash(info) == 100_000_000


def test_extract_stock_cash_flat_balance_fallback():
    info = {"balance": 80_000_000}
    assert p.extract_stock_cash(info) == 80_000_000


def test_portfolio_summary_end_to_end(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    positions = [
        _pos("FPT", 100, trade_qty=100, cost=120000, market=130000),  # value 13,000,000
        _pos("VN30F1M", 5, cost=1300, market=1320),                   # phái sinh -> bỏ
    ]
    account_info = {"raw": {"stock": {"availableCash": 50_000_000}}}
    summary = p.portfolio_summary(positions, account_info)
    assert [h.symbol for h in summary["holdings"]] == ["FPT"]
    assert summary["assets"]["stock_value"] == 13_000_000
    assert summary["assets"]["cash"] == 50_000_000
    assert summary["assets"]["total"] == 63_000_000


def test_paper_portfolio_summary_does_not_add_stock_value_twice(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    positions = [
        _pos("AAA", 500, trade_qty=500, cost=7.20, market=7.00),
        _pos("POW", 200, trade_qty=200, cost=14.70, market=14.00),
    ]
    account_info = {
        "status": "PAPER",
        "balance": 100_000_000,
        "equity": 99_800_000,
        "cash_available": 99_800_000,
    }

    summary = p.paper_portfolio_summary(positions, account_info)
    assets = summary["assets"]

    assert assets["stock_value"] == 500 * 7_000 + 200 * 14_000
    assert assets["total"] == 99_800_000
    assert assets["cash"] == 99_800_000 - assets["stock_value"]
    assert assets["cash"] + assets["stock_value"] == assets["total"]


def test_paper_portfolio_summary_accepts_raw_paper_state_rows(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    positions = [{
        "symbol": "AAA",
        "volume": 500,
        "price_open": 7.49,
        "price_current": 7.16,
    }]
    summary = p.paper_portfolio_summary(
        positions,
        {"status": "PAPER", "balance": 100_000_000, "equity": 99_800_000},
    )

    assert summary["assets"]["stock_value"] == 500 * 7.16 * 1000
    assert summary["assets"]["total"] == 99_800_000
