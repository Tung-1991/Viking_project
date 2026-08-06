# -*- coding: utf-8 -*-
"""Spread = khoảng giá ask-bid (điểm PS / nghìn VND CS), KHÔNG chia/nhân 1000 hay point.

Khóa hành vi sau đợt sửa lỗi đơn vị spread:
  - fetch_realtime_tick gắn synthetic_quote khi bid/ask là fallback từ giá khớp
    (ngoài giờ / nghỉ trưa) để UI hiện "chờ giá" thay vì "Spread: 0.00".
  - DNSEMarketWS._ingest gắn/clear cờ tương tự cho tick WebSocket.
"""
from types import SimpleNamespace

import config
import core.data_engine as data_engine_module
from core.data_engine import DataEngine
from core.dnse_ws import DNSEMarketWS


def _engine_with_api(monkeypatch, trade, quote):
    engine = DataEngine()
    monkeypatch.setattr(config, "DNSE_WS_ENABLED", False, raising=False)
    fake_dnse = SimpleNamespace(
        get_latest_trade=lambda _symbol: trade,
        get_latest_quote=lambda _symbol: quote,
    )
    monkeypatch.setattr(data_engine_module, "dnse_api", fake_dnse)
    return engine


def test_fetch_realtime_tick_marks_synthetic_when_no_orderbook(monkeypatch):
    # Nghỉ trưa: chỉ có giá khớp, không có sổ lệnh -> bid=ask=last + cờ synthetic.
    trade = {"matchPrice": 2006.8, "highestPrice": 2010.0, "lowestPrice": 2000.0, "openPrice": 2005.0}
    engine = _engine_with_api(monkeypatch, trade, None)

    tick = engine.fetch_realtime_tick("VN30F1M")

    assert tick["bid"] == tick["ask"] == 2006.8
    assert tick["spread"] == 0.0
    assert tick["synthetic_quote"] is True


def test_fetch_realtime_tick_real_quote_keeps_price_unit_spread(monkeypatch):
    # Phiên liên tục: spread giữ nguyên đơn vị giá (0.2 điểm), không cờ synthetic.
    trade = {"matchPrice": 2006.8}
    quote = {"bid": [{"price": 2006.7}], "offer": [{"price": 2006.9}]}
    engine = _engine_with_api(monkeypatch, trade, quote)

    tick = engine.fetch_realtime_tick("VN30F1M")

    assert tick["bid"] == 2006.7
    assert tick["ask"] == 2006.9
    assert abs(tick["spread"] - 0.2) < 1e-9
    assert not tick.get("synthetic_quote")


def test_ws_ingest_synthetic_then_cleared_by_real_quote():
    ws = DNSEMarketWS(api_key="k", api_secret="s")

    ws._ingest({"symbol": "VN30F1M", "matchPrice": 2006.8})
    tick = ws.latest_tick("VN30F1M")
    assert tick["bid"] == tick["ask"] == 2006.8
    assert tick["synthetic_quote"] is True

    ws._ingest({"symbol": "VN30F1M", "bid": [{"price": 2006.7}], "offer": [{"price": 2006.9}]})
    tick = ws.latest_tick("VN30F1M")
    assert tick["synthetic_quote"] is False
    assert abs(tick["spread"] - 0.2) < 1e-9
