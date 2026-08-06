# -*- coding: utf-8 -*-
from datetime import datetime

import config
from core import market_hours
from core.dnse_connector import DNSEConnector


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers or {}})
        return self.responses.pop(0)

    def close(self):
        pass


def test_resolve_order_kind_by_phase(monkeypatch):
    # 2026-06-17 là Thứ 4 (ngày trong tuần).
    # Phái sinh: ATO 8:45-9:00
    monkeypatch.setattr(market_hours, "_market_now", lambda: datetime(2026, 6, 17, 8, 50))
    assert market_hours.resolve_order_kind("VN30F1M", "AUTO") == "ATO"
    assert market_hours.resolve_order_kind("VN30F1M", "NORMAL") is None
    # ATC 14:30-14:45 (cả 2 thị trường)
    monkeypatch.setattr(market_hours, "_market_now", lambda: datetime(2026, 6, 17, 14, 35))
    assert market_hours.resolve_order_kind("FPT", "AUTO") == "ATC"
    assert market_hours.resolve_order_kind("VN30F1M", "AUTO") == "ATC"
    # Khớp liên tục -> AUTO trả None
    monkeypatch.setattr(market_hours, "_market_now", lambda: datetime(2026, 6, 17, 10, 0))
    assert market_hours.resolve_order_kind("VN30F1M", "AUTO") is None
    # Cổ phiếu ATO chỉ từ 9:00; lúc 8:50 cổ phiếu CHƯA mở -> None
    monkeypatch.setattr(market_hours, "_market_now", lambda: datetime(2026, 6, 17, 8, 50))
    assert market_hours.resolve_order_kind("FPT", "AUTO") is None


def test_place_order_forwards_order_kind_to_payload(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", True)
    session = FakeSession([FakeResponse(200, {"orderId": "O1", "status": "NEW"})])
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="ACC1", base_url="https://x.test", session=session)
    assert conn.connect()
    conn.trading_token = "tok"
    conn.trading_token_expires_at = 9999999999
    conn._symbol_map = {"VN30F1M": "41I1G6000"}
    conn._symbol_map_ts = 9999999999

    result = conn.place_order("VN30F1M", "BUY", 1, 1200, 1220, 77, "[BOT]", order_kind="ATO")

    assert result.ok is True
    assert session.calls[0]["json"]["orderType"] == "ATO"
    assert session.calls[0]["json"]["symbol"] == "41I1G6000"


def test_place_order_with_price_uses_limit_order(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", True)
    session = FakeSession([FakeResponse(200, {"orderId": "O2", "status": "NEW"})])
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="ACC1", base_url="https://x.test", session=session)
    assert conn.connect()
    conn.trading_token = "tok"
    conn.trading_token_expires_at = 9999999999
    conn._symbol_map = {"VN30F1M": "41I1G6000"}
    conn._symbol_map_ts = 9999999999

    result = conn.place_order("VN30F1M", "BUY", 1, 1190, 1220, 77, "[USER]", price=1200)

    assert result.ok is True
    assert session.calls[0]["json"]["orderType"] == "LO"
    assert session.calls[0]["json"]["price"] == 1200.0
