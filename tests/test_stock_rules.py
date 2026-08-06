# -*- coding: utf-8 -*-
import config
from core import stock_rules


def test_round_lot_down():
    assert stock_rules.round_lot_down(150, 100) == 100
    assert stock_rules.round_lot_down(100, 100) == 100
    assert stock_rules.round_lot_down(90, 100) == 0
    assert stock_rules.round_lot_down(250, 100) == 200
    assert stock_rules.round_lot_down(0, 100) == 0
    assert stock_rules.round_lot_down(-50, 100) == 0


def test_round_lot_down_uses_config_default(monkeypatch):
    monkeypatch.setattr(config, "STOCK_ROUND_LOT", 100)
    assert stock_rules.round_lot_down(199) == 100


def test_max_shares_for_value():
    # cap 20tr, giá 33.65, point_value 1000 -> 20.000.000/33.650 = 594 -> 500 CP (lô 100)
    assert stock_rules.max_shares_for_value(20_000_000, 33.65, 1000, 100) == 500
    # đúng kịch bản lỗi: nếu KHÔNG cap, risk-size ra 5900; cap 20%NAV(100m)=20tr -> 500
    assert stock_rules.max_shares_for_value(20_000_000, 33.65, 1000, 100) < 5900
    # NAV nhỏ: cap 2tr, giá 33.65 -> 59 cp -> < 1 lô -> 0
    assert stock_rules.max_shares_for_value(2_000_000, 33.65, 1000, 100) == 0
    # đủ nhiều lô
    assert stock_rules.max_shares_for_value(50_000_000, 33.65, 1000, 100) == 1400
    # dữ liệu xấu -> 0
    assert stock_rules.max_shares_for_value(0, 33.65, 1000, 100) == 0
    assert stock_rules.max_shares_for_value(20_000_000, 0, 1000, 100) == 0


def test_resolve_band_prefers_dnse_ceiling_floor():
    fl, ce = stock_rules.resolve_band(reference=100.0, ceiling=107.0, floor=93.0, band_pct=0.07)
    assert (fl, ce) == (93.0, 107.0)


def test_resolve_band_falls_back_to_reference():
    fl, ce = stock_rules.resolve_band(reference=100.0, ceiling=0.0, floor=0.0, band_pct=0.07)
    assert round(fl, 4) == 93.0
    assert round(ce, 4) == 107.0


def test_resolve_band_unknown_when_no_data():
    assert stock_rules.resolve_band(0.0, 0.0, 0.0, 0.07) == (0.0, 0.0)


def test_price_in_band():
    assert stock_rules.price_in_band(100.0, 93.0, 107.0) is True
    assert stock_rules.price_in_band(93.0, 93.0, 107.0) is True   # đúng biên sàn
    assert stock_rules.price_in_band(107.0, 93.0, 107.0) is True  # đúng biên trần
    assert stock_rules.price_in_band(108.0, 93.0, 107.0) is False
    assert stock_rules.price_in_band(92.0, 93.0, 107.0) is False
    # band không xác định -> không chặn
    assert stock_rules.price_in_band(999.0, 0.0, 0.0) is True


def test_band_pct_for_default_hose():
    assert stock_rules.band_pct_for("FPT") == 0.07


def test_band_pct_for_override(monkeypatch):
    monkeypatch.setattr(config, "STOCK_SYMBOL_EXCHANGE", {"SHS": "HNX"})
    assert stock_rules.band_pct_for("SHS") == 0.10
