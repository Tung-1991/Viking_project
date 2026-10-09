from __future__ import annotations

from datetime import datetime

import pytest

from viking_v2.trading.market import MarketDataService, VN_TZ, merge_tick_into_daily_bars
from viking_v2.services.daemon import merge_live_tick
from viking_v2.trading.validation import quote_is_fresh


class Client:
    api_key = ""
    api_secret = ""

    def __init__(self):
        self.calls = 0

    def get_ohlc(self, symbol, resolution, from_ts, to_ts):
        self.calls += 1
        assert symbol == "FPT"
        assert resolution == "1D"
        return {
            "t": [1_700_000_000, 1_700_086_400],
            "o": [100, 101],
            "h": [102, 103],
            "l": [99, 100],
            "c": [101, 102],
            "v": [1000, 2000],
        }

    def api_health(self):
        return {}


class WS:
    def snapshot(self):
        return {}


def test_daily_bars_normalize_parallel_dnse_arrays_and_cache():
    client = Client()
    service = MarketDataService(client, WS())
    first = service.get_daily_bars("fpt")
    second = service.get_daily_bars("FPT")
    assert first[0]["open"] == 100
    assert first[-1]["close"] == 102
    assert first[-1]["volume"] == 2000
    assert second == first
    assert client.calls == 1


def test_invalid_ohlc_shape_is_rejected():
    assert MarketDataService._normalize_ohlc({"t": [1], "o": [], "h": [], "l": [], "c": [], "v": []}) == []


def test_today_daily_bar_is_not_closed_during_lunch(monkeypatch):
    now = datetime(2026, 8, 24, 12, 0, tzinfo=VN_TZ)
    monkeypatch.setattr("viking_v2.trading.market.market_now", lambda: now)
    stamp = int(datetime(2026, 8, 24, 9, 0, tzinfo=VN_TZ).timestamp())
    rows = MarketDataService._normalize_ohlc({
        "t": [stamp], "o": [100], "h": [101], "l": [99],
        "c": [100.5], "v": [1000],
    })
    assert rows[0]["closed"] is False


def test_today_daily_bar_closes_only_after_atc(monkeypatch):
    now = datetime(2026, 8, 24, 15, 0, tzinfo=VN_TZ)
    monkeypatch.setattr("viking_v2.trading.market.market_now", lambda: now)
    stamp = int(datetime(2026, 8, 24, 9, 0, tzinfo=VN_TZ).timestamp())
    rows = MarketDataService._normalize_ohlc({
        "t": [stamp], "o": [100], "h": [101], "l": [99],
        "c": [100.5], "v": [1000],
    })
    assert rows[0]["closed"] is True


def test_last_daily_close_becomes_a_frozen_tick_outside_session():
    tick = MarketDataService.frozen_tick_from_bars(
        "fpt",
        [
            {"time": 1_700_000_000, "open": 99, "high": 102, "low": 98, "close": 100, "volume": 1_000},
            {"time": 1_700_086_400, "open": 101, "high": 105, "low": 100, "close": 104, "volume": 2_000},
        ],
    )
    assert tick is not None
    assert tick["symbol"] == "FPT"
    assert tick["price"] == 104
    assert tick["reference"] == 100
    assert tick["source"] == "CLOSE"
    assert tick["frozen"] is True


def test_bid_ask_only_ws_tick_keeps_last_price_for_preview():
    merged = merge_live_tick(
        {"symbol": "FPT", "bid": 69.2, "ask": 69.3, "source": "WS", "timestamp": 2.0},
        {"symbol": "FPT", "price": 69.2, "reference": 70.8, "source": "CLOSE"},
        None,
    )
    assert merged["price"] == 69.2
    assert merged["bid"] == 69.2
    assert merged["ask"] == 69.3
    assert merged["price_frozen"] is True
    assert merged["frozen"] is False


def test_bid_ask_only_first_tick_uses_daily_close_fallback():
    merged = merge_live_tick(
        {"symbol": "FPT", "bid": 69.2, "ask": 69.3, "source": "WS"},
        None,
        {"symbol": "FPT", "price": 69.2, "reference": 70.8, "source": "CLOSE"},
    )
    assert merged["price"] == 69.2
    assert merged["reference"] == 70.8
    assert merged["source"] == "WS"
    assert merged["price_frozen"] is True


def test_zero_reference_from_ws_does_not_erase_daily_reference():
    merged = merge_live_tick(
        {"symbol": "FPT", "bid": 69.4, "ask": 69.5, "reference": 0.0},
        {"symbol": "FPT", "price": 69.2, "reference": 0.0},
        {"symbol": "FPT", "price": 69.2, "reference": 70.8},
    )
    assert merged["reference"] == 70.8


@pytest.mark.parametrize("source", ["WS", "REST"])
@pytest.mark.parametrize("previous", [
    {"stale": True},
    {"health": "REST_UNAVAILABLE", "stale": True},
    {"received_at": 1, "frozen": True, "quote_issue": "FROZEN"},
])
def test_new_quote_recovers_from_restart_and_failed_source(source, previous):
    old = {"symbol": "MSN", "price": 74.2, "timestamp": 1, **previous}
    live = {"symbol": "MSN", "price": 74.4, "timestamp": 1000, "source": source}
    merged = merge_live_tick(live, old, {"frozen": True, "received_at": 1})
    assert quote_is_fresh(merged, "MSN", now=1001)
    assert merged["price"] == 74.4
    assert merged["source"] == source
    assert "quote_issue" not in merged
    assert old == {"symbol": "MSN", "price": 74.2, "timestamp": 1, **previous}


@pytest.mark.parametrize("updates", [
    {"stale": True}, {"frozen": True}, {"health": "REST_UNAVAILABLE"},
    {"timestamp": 1}, {"timestamp": "bad"}, {"timestamp": float("nan")},
    {"timestamp": 1100}, {"symbol": "CTS"},
])
def test_merge_never_makes_a_bad_new_quote_tradable(updates):
    live = {"symbol": "MSN", "price": 74.4, "timestamp": 1000, "source": "WS", **updates}
    merged = merge_live_tick(live, {"symbol": "MSN", "price": 74.2, "timestamp": 1000}, None)
    assert not quote_is_fresh(merged, "MSN", now=1001)


def test_partial_quote_cannot_borrow_cached_receipt_time():
    merged = merge_live_tick(
        {"symbol": "MSN", "bid": 74.3, "source": "WS"},
        {"symbol": "MSN", "price": 74.2, "timestamp": 1000, "received_at": 1000},
        None,
    )
    assert merged["price"] == 74.2  # Preview may still show the old price.
    assert not quote_is_fresh(merged, "MSN", now=1001)


def test_fresh_receipt_time_from_new_quote_is_kept():
    merged = merge_live_tick(
        {"symbol": "MSN", "price": 74.4, "timestamp": 1, "received_at": 1000},
        {"symbol": "MSN", "price": 74.2, "stale": True, "received_at": 1},
        None,
    )
    assert quote_is_fresh(merged, "MSN", now=1001)
    assert merged["received_at"] == 1000


def test_live_tick_updates_current_daily_bar_without_refetching_history():
    now = datetime(2026, 8, 14, 10, 0, tzinfo=VN_TZ)
    rows = [{
        "time": int(datetime(2026, 8, 14, 9, 0, tzinfo=VN_TZ).timestamp()),
        "open": 69.0, "high": 69.5, "low": 68.8, "close": 69.2,
        "volume": 1_000, "closed": False,
    }]
    merged = merge_tick_into_daily_bars(
        rows,
        {"price": 69_700, "high": 69_900, "low": 68_700, "volume": 2_000},
        now=now,
    )
    assert len(merged) == 1
    assert merged[0]["close"] == 69.7
    assert merged[0]["high"] == 69.9
    assert merged[0]["low"] == 68.7
    assert merged[0]["volume"] == 2_000
    assert merged[0]["closed"] is False


def test_rest_tick_failure_is_backed_off_when_websocket_is_offline(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr("viking_v2.trading.market.time.time", lambda: now[0])

    class OfflineClient:
        api_key = ""
        api_secret = ""

        def __init__(self):
            self.trade_calls = 0
            self.quote_calls = 0

        def get_latest_trade(self, _symbol):
            self.trade_calls += 1
            return None

        def get_latest_quote(self, _symbol):
            self.quote_calls += 1
            return None

    class OfflineWS:
        def latest_tick(self, _symbol):
            return None

    client = OfflineClient()
    market = MarketDataService(client, OfflineWS())
    assert market.get_tick("FPT") is None
    assert market.get_tick("FPT") is None
    assert (client.trade_calls, client.quote_calls) == (1, 1)
