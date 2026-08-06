# -*- coding: utf-8 -*-
import json
import threading
import time
from types import SimpleNamespace

import config
from core.data_engine import DataEngine
import core.data_engine as data_engine_module
from core.dnse_connector import DNSEConnector
from core.dnse_ws import DNSEMarketWS
from core.process_lock import ProcessLock
from core.trade_manager import TradeManager
import core.trade_manager as trade_manager_module


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_ws_uses_application_ping_pong_and_all_boards():
    client = DNSEMarketWS(api_key="k", api_secret="s")
    sent = []
    client._ws = SimpleNamespace(send=sent.append)
    client._handle_control("ping", {})
    assert json.loads(sent[-1]) == {"action": "pong"}
    client._handle_control("pong", {})
    assert client._last_app_pong > 0
    names = client._channels()
    assert "tick.G1.json" in names
    assert "tick.T6.json" in names
    assert "top_price.G7.json" in names
    assert "expected_price.G3.json" in names


def test_auth_success_starts_only_one_heartbeat_per_connection(monkeypatch):
    started = []

    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs)

    client = DNSEMarketWS(api_key="k", api_secret="s")
    client._connection_generation = 7
    client._ws = SimpleNamespace(send=lambda _payload: None)
    monkeypatch.setattr("core.dnse_ws.threading.Thread", Thread)
    client._handle_control("auth_success", {})
    client._handle_control("auth_success", {})
    assert len(started) == 1
    assert started[0]["args"] == (7,)


def test_consumer_reads_shared_feed_without_rest(monkeypatch):
    engine = DataEngine()
    calls = []
    monkeypatch.setattr(
        data_engine_module,
        "dnse_api",
        SimpleNamespace(
            get_latest_trade=lambda _s: calls.append("trade"),
            get_latest_quote=lambda _s: calls.append("quote"),
        ),
    )
    engine.configure_market_data_owner(
        False,
        tick_provider=lambda _s: {
            "last": 25.0,
            "bid": 24.9,
            "ask": 25.1,
            "timestamp": time.time(),
            "source": "WS",
            "market_state": "LIVE",
        },
        state_provider=lambda: "LIVE",
    )
    tick = engine.fetch_realtime_tick("FPT")
    assert tick["last"] == 25.0
    assert tick["source"] == "WS"
    assert calls == []


def test_owner_waits_before_rest_fallback(monkeypatch):
    calls = []
    ws = SimpleNamespace(
        available=True,
        is_running=lambda: True,
        is_connected=lambda: False,
        set_market_data_enabled=lambda _enabled: None,
        start=lambda: True,
        subscribe=lambda _symbols: None,
        latest_tick=lambda _symbol: None,
        snapshot=lambda: {"connection_generation": 1},
    )
    api = SimpleNamespace(
        get_latest_trade=lambda _s: calls.append("trade") or {"matchPrice": 10.0},
        get_latest_quote=lambda _s: calls.append("quote") or {"bid": [{"price": 9.9}], "offer": [{"price": 10.1}]},
    )
    monkeypatch.setattr(data_engine_module, "market_ws", ws)
    monkeypatch.setattr(data_engine_module, "dnse_api", api)
    monkeypatch.setattr(data_engine_module, "is_symbol_network_window_open", lambda *_a, **_k: (True, "open"))
    monkeypatch.setattr(data_engine_module, "is_symbol_trade_window_open", lambda *_a, **_k: (True, "open"))
    monkeypatch.setattr(config, "DNSE_WS_ENABLED", True)
    monkeypatch.setattr(config, "DNSE_WS_FALLBACK_DELAY_SECONDS", 5.0)
    engine = DataEngine()
    assert engine.fetch_realtime_tick("FPT") is None
    assert calls == []
    engine._ws_unavailable_since = time.time() - 6
    tick = engine.fetch_realtime_tick("FPT")
    assert tick["source"] == "REST"
    assert tick["market_state"] == "REST FALLBACK"
    assert calls == ["trade", "quote"]


def test_healthy_ws_missing_one_symbol_keeps_global_live(monkeypatch):
    calls = []
    ws = SimpleNamespace(
        available=True,
        is_running=lambda: True,
        is_connected=lambda: True,
        set_market_data_enabled=lambda _enabled: None,
        start=lambda: True,
        subscribe=lambda _symbols: None,
        latest_tick=lambda _symbol: None,
        snapshot=lambda: {"connection_generation": 1},
    )
    api = SimpleNamespace(
        get_latest_trade=lambda _s: calls.append("trade") or {"matchPrice": 10.0},
        get_latest_quote=lambda _s: calls.append("quote") or {
            "bid": [{"price": 9.9}],
            "offer": [{"price": 10.1}],
        },
    )
    monkeypatch.setattr(data_engine_module, "market_ws", ws)
    monkeypatch.setattr(data_engine_module, "dnse_api", api)
    monkeypatch.setattr(data_engine_module, "is_symbol_network_window_open", lambda *_a, **_k: (True, "open"))
    monkeypatch.setattr(data_engine_module, "is_symbol_trade_window_open", lambda *_a, **_k: (True, "open"))
    monkeypatch.setattr(config, "DNSE_WS_ENABLED", True)
    monkeypatch.setattr(config, "DNSE_WS_FALLBACK_DELAY_SECONDS", 0.0)
    engine = DataEngine()

    tick = engine.fetch_realtime_tick("FPT")

    assert tick["source"] == "REST"
    assert tick["market_state"] == "REST FALLBACK"  # trạng thái riêng của FPT
    assert engine.market_data_state() == "LIVE"  # kết nối chung không bị hạ
    assert calls == ["trade", "quote"]


def test_cache_is_stale_even_when_global_connection_is_live():
    tick = DataEngine._decorate_tick(
        {"last": 10.0, "timestamp": time.time() - 30.0},
        "CACHE",
        "LIVE",
    )
    assert tick["freshness"] == "STALE"
    assert tick["stale"] is True


def test_recovering_warning_waits_for_grace_period(monkeypatch):
    manager = TradeManager.__new__(TradeManager)
    monkeypatch.setattr(config, "DNSE_MARKET_WARNING_GRACE_SECONDS", 5.0, raising=False)

    assert manager._market_pause_warning_due("RECOVERING", now=100.0) is False
    assert manager._market_pause_warning_due("RECOVERING", now=104.9) is False
    assert manager._market_pause_warning_due("RECOVERING", now=105.1) is True
    assert manager._market_pause_warning_due("LIVE", now=106.0) is False
    assert manager._market_pause_warning_due("RECOVERING", now=107.0) is False


def test_429_blocks_route_family_across_symbols(monkeypatch):
    calls = []

    def request(_method, url, **_kwargs):
        calls.append(url)
        return Response(429, {"message": "quota"})

    conn = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    monkeypatch.setattr(config, "DNSE_RATE_LIMIT_RETRIES", 0)
    first = conn._request("GET", "/price/FPT/trades/latest")
    second = conn._request("GET", "/price/AAA/trades/latest")
    assert first[2] == 429
    assert second[2] == 429 and "LOCAL_RATE_LIMIT" in second[3]
    assert len(calls) == 1
    health = conn.get_api_health_snapshot()
    assert "GET /price/*/trades/latest" in health["throttled_endpoints"]
    assert health["suppressed_429"] == 1
    assert conn._endpoint_throttle_seconds("GET", "/accounts/1/balances") == 0


def test_connector_shared_provider_never_calls_latest_rest():
    session = SimpleNamespace(request=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network called")))
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=session)
    conn.set_market_data_provider(
        lambda _s: {"last": 12.0, "bid": 11.9, "ask": 12.1, "timestamp": time.time()},
        lambda: "LIVE",
    )
    tick = conn.get_tick("FPT")
    assert tick.last == 12.0
    assert tick.bid == 11.9


def test_connector_reads_closed_position_detail(monkeypatch):
    captured = {}

    def request(method, url, **kwargs):
        captured.update({"method": method, "url": url, "params": kwargs.get("params")})
        return Response(200, {"data": {"id": "P1", "status": "CLOSED", "averageClosePrice": 1901.5}})

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    detail = conn.get_position_detail("P1", "VN30F1M")

    assert detail["status"] == "CLOSED"
    assert captured["url"].endswith("/accounts/positions/P1")
    assert captured["params"] == {"marketType": "DERIVATIVE"}


def test_bot_entry_stops_before_account_call_when_feed_is_recovering(monkeypatch):
    manager = TradeManager.__new__(TradeManager)
    manager.connector = SimpleNamespace(
        get_market_data_state=lambda: "RECOVERING",
        get_account_info=lambda: (_ for _ in ()).throw(AssertionError("account called")),
    )
    manager._sync_state_lifecycle = lambda: None
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _s: (True, "OPEN"))
    result = manager.execute_bot_trade("BUY", "FPT", {}, signal_class="ENTRY")
    assert "MARKET_DATA_UNAVAILABLE" in result


def test_manual_down_feed_requires_explicit_limit_confirmation(monkeypatch):
    manager = TradeManager.__new__(TradeManager)
    manager.connector = SimpleNamespace(
        get_market_data_state=lambda: "MARKET DATA DOWN",
        get_account_info=lambda: (_ for _ in ()).throw(AssertionError("account called")),
    )
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _s: (True, "OPEN"))
    blocked = manager.execute_manual_trade("BUY", "SCALPING", "FPT", True, {}, manual_entry_price=0)
    confirm = manager.execute_manual_trade("BUY", "SCALPING", "FPT", True, {}, manual_entry_price=25.0)
    assert "MARKET_DATA_DOWN" in blocked
    assert confirm.startswith("MARKET_DATA_CONFIRM|")


def test_connector_blocks_bot_and_market_order_but_allows_manual_lo(monkeypatch):
    calls = []

    def request(method, _url, **_kwargs):
        calls.append(method)
        return Response(200, {"orderId": "O1", "status": "NEW"})

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", True)
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    conn.set_market_data_provider(lambda _s: None, lambda: "MARKET DATA DOWN")
    conn.trading_token = "tok"
    conn.trading_token_expires_at = time.time() + 3600
    conn._symbol_map = {"VN30F1M": "41I1G6000"}
    conn._symbol_map_ts = time.time()
    assert conn.send_order("VN30F1M", "BUY", 1, price=1900, comment="[BOT]").error == "MARKET_DATA_DOWN"
    assert conn.send_order("VN30F1M", "BUY", 1, price=0, comment="[USER]").error == "MARKET_DATA_DOWN"
    allowed = conn.send_order("VN30F1M", "BUY", 1, price=1900, comment="[USER]")
    assert allowed.ok is True
    assert calls == ["POST"]


def test_order_timeout_is_reconciled_by_request_id(monkeypatch):
    calls = []
    submitted = {}

    def request(method, _url, **kwargs):
        calls.append(method)
        if method == "POST":
            submitted.update(kwargs["json"])
            raise TimeoutError("socket timeout")
        return Response(200, {"orders": [{"orderId": "O-FOUND", "remark": submitted["remark"]}]})

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", True)
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    conn.trading_token = "tok"
    conn.trading_token_expires_at = time.time() + 3600
    conn._symbol_map = {"VN30F1M": "41I1G6000"}
    conn._symbol_map_ts = time.time()
    result = conn.send_order("VN30F1M", "BUY", 1, price=1900.0, comment="[USER]")
    assert result.ok is True
    assert result.order_id == "O-FOUND"
    assert calls.count("POST") == 1
    assert calls.count("GET") == 1


def test_order_timeout_without_match_is_unknown_not_retried(monkeypatch):
    calls = []

    def request(method, _url, **_kwargs):
        calls.append(method)
        if method == "POST":
            raise TimeoutError("socket timeout")
        return Response(200, {"orders": []})

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "AUTO_TRADE_ENABLED", True)
    conn = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    conn.trading_token = "tok"
    conn.trading_token_expires_at = time.time() + 3600
    conn._symbol_map = {"VN30F1M": "41I1G6000"}
    conn._symbol_map_ts = time.time()
    result = conn.send_order("VN30F1M", "BUY", 1, price=1900.0, comment="[USER]")
    assert result.ok is False
    assert result.error == "ORDER_STATUS_UNKNOWN"
    assert result.status == "UNKNOWN"
    assert calls.count("POST") == 1


def test_process_lock_rejects_second_holder(tmp_path):
    path = tmp_path / "app.lock"
    first = ProcessLock(str(path))
    second = ProcessLock(str(path))
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()
