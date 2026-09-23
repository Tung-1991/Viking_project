from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from viking_v2.models import OrderIntent, RuntimeConfig
from viking_v2.services.daemon import active_runtime_symbols
from viking_v2.services.runtime import RuntimeBridge
from viking_v2.rules.business import StaticRule


def test_static_rule_waits_without_business_bars():
    decision = StaticRule().evaluate({"symbol": "FPT", "price": 100}, {"cash": 1_000_000})
    assert decision.action == "WAIT"
    assert decision.symbol == "FPT"
    assert decision.reason == "NO_NEW_BUY_SIGNAL"


def test_runtime_bridge_disarms_stale_bot(tmp_path):
    bridge = RuntimeBridge(root=tmp_path)
    bridge.write_config(RuntimeConfig(["fpt"], False, True))
    result = bridge.disarm()
    assert result.bot_enabled is False
    assert bridge.read_config().bot_enabled is False
    assert bridge.read_config().watchlist == ["FPT"]


def test_active_runtime_symbols_keep_removed_open_trades_and_pending_orders():
    open_cycle = SimpleNamespace(
        symbol="MBB", execution_mode="PAPER", status="OPEN", open_quantity=100,
    )
    closed_cycle = SimpleNamespace(
        symbol="VCB", execution_mode="PAPER", status="CLOSED", open_quantity=0,
    )
    pending = OrderIntent.create("SSI", "SELL", 100, "MARKET", execution_mode="PAPER")
    real = OrderIntent.create("VNM", "BUY", 100, "MARKET", execution_mode="REAL")

    symbols = active_runtime_symbols(
        ["FPT"], ["HPG"], [open_cycle, closed_cycle], [pending, real], "PAPER",
    )

    assert symbols == ["FPT", "HPG", "MBB", "SSI"]


def test_daemon_writes_heartbeat_and_starts_off(monkeypatch):
    import viking_v2.services.daemon as daemon

    statuses = []

    class Bridge:
        log_dir = ".pytest_cache/v2-daemon"
        rule_state_path = ".pytest_cache/v2-daemon/rule.json"
        trade_state_path = ".pytest_cache/v2-daemon/trades.json"
        pending_orders_path = ".pytest_cache/v2-daemon/orders.json"
        paper_state_path = ".pytest_cache/v2-daemon/paper.json"
        market_cache_path = ".pytest_cache/v2-daemon/market.json"
        signal_log_path = ".pytest_cache/v2-daemon/signals.csv"

        def disarm(self):
            return RuntimeConfig(["FPT"], True, False)

        def read_config(self):
            return RuntimeConfig(["FPT"], True, True)

        def write_status(self, status):
            statuses.append(status)

        def read_status(self):
            return {}

    class Client:
        api_key = ""
        api_secret = ""

        def connect(self):
            return False

        def close(self):
            pass

        def get_working_dates(self):
            return []

    class Market:
        def __init__(self, *_args):
            pass

        def start(self, _symbols):
            pass

        def stop(self):
            pass

        def set_symbols(self, _symbols):
            pass

        def health(self):
            return {}

        def get_tick(self, _symbol):
            return None

    calls = 0

    def stop_after_one(_seconds):
        nonlocal calls
        calls += 1
        raise SystemExit

    monkeypatch.setattr(daemon, "RuntimeBridge", lambda _account: Bridge())
    monkeypatch.setattr(daemon, "DNSEClient", lambda **_kwargs: Client())
    monkeypatch.setattr(daemon, "DNSEMarketWS", lambda *_args: object())
    monkeypatch.setattr(daemon, "MarketDataService", Market)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(daemon.time, "sleep", stop_after_one)
    with pytest.raises(SystemExit):
        daemon.run("TEST")
    assert statuses[0].daemon_status == "RUNNING"
    assert statuses[-1].daemon_status == "STOPPED"
    assert statuses[-1].bot_enabled is False
    assert statuses[0].working_dates == []
    assert time.time() - statuses[0].heartbeat_at < 2
