"""Run real daemon, OHLC adapter, indicators and Telegram with fake services."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from viking_v2 import config
from viking_v2.models import RuntimeConfig, StrategyDecision
from viking_v2.rules.business import indicator_snapshot
from viking_v2.services import daemon
from viking_v2.services.runtime import RuntimeBridge
from viking_v2.trading.market import VN_TZ, MarketDataService
from viking_v2.trading.orders import OrderQueue


DAY = datetime(2026, 10, 12, tzinfo=VN_TZ)
POINTS = [DAY.replace(hour=14, minute=28), DAY.replace(hour=14, minute=28, second=1),
          DAY.replace(hour=14, minute=44, second=59), DAY.replace(hour=14, minute=45),
          DAY.replace(hour=14, minute=58), DAY.replace(hour=15, minute=28, second=5),
          DAY.replace(hour=15, minute=28, second=6)]


def history():
    days = []
    current = DAY - timedelta(days=1)
    while len(days) < 40:
        if current.weekday() < 5:
            days.append(current.replace(hour=9))
        current -= timedelta(days=1)
    days.reverse()
    return [{"time": int(at.timestamp()), "open": price, "high": price + .1,
             "low": price - .1, "close": price, "volume": 10000, "closed": True}
            for i, at in enumerate(days) for price in [33 + .1 * (i % 7) + .02 * i]]


def run_daemon(monkeypatch, tmp_path, variant, mode, require_cross):
    import viking_v2.trading.market as market_module
    import viking_v2.dashboard.actions as actions
    clock = {"index": 0, "at": POINTS[0]}
    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["at"].astimezone(tz) if tz else clock["at"].replace(tzinfo=None)
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(daemon, "datetime", ClockDateTime)
    monkeypatch.setattr(actions, "datetime", ClockDateTime)
    monkeypatch.setattr(market_module, "market_now", lambda: clock["at"])
    monkeypatch.setattr(daemon.time, "time", lambda: clock["at"].timestamp())
    bridge = RuntimeBridge("OFFLINE_AFTER_CLOSE")
    bridge.write_config(RuntimeConfig(["IDC"], mode == "PAPER", False))
    settings = config.AppSettings(watchlist=["IDC"], priority_symbols=[],
                                  paper_mode=mode == "PAPER", signal_mode="REALTIME",
                                  realtime_indicator_interval="TICK", telegram_enabled=True,
                                  market_phase_override_enabled=True,
                                  market_phase_override="ACCUMULATION",
                                  market_phase_override_exposure_pct=100,
                                  rule_parameters={"buy_signal_require_ema_cross": require_cross,
                                                   "buy_signal_session_cross_enabled": True,
                                                   "whipsaw_enabled": False,
                                                   "buy_window_enabled": True,
                                                   "buy_window_start": "14:00"}).normalize()
    monkeypatch.setattr(daemon, "load_settings", lambda *_args: settings)
    prior = history()
    today_row = {"time": int(DAY.replace(hour=9).timestamp()), "open": 34.8,
                 "high": 34.8, "low": 34.8, "close": 34.8, "volume": 20000}
    requests, live_ticks, statuses, handlers = [], [], [], {}

    class Client:
        api_key = api_secret = "OFFLINE"
        def __init__(self, **_kwargs): pass
        def connect(self): return True
        def close(self): pass
        def api_health(self): return {}
        def get_working_dates(self): return ["2026-10-12"]
        def get_secdef(self, _symbol): return {"marketId": "STX", "ceilingPrice": 40}
        def get_positions(self): return []
        def get_balance(self): return {"stock": {"availableCash": 50_000_000}}
        def get_ohlc(self, symbol, resolution, left, right):
            requests.append({"symbol": symbol, "resolution": resolution,
                             "at": clock["at"].isoformat()})
            if resolution == "1":
                stamps = list(range(left, right + 1, 60))
                prices = [33 if point < DAY.replace(hour=14).timestamp() else 34.8 for point in stamps]
                return {"s": "ok", "t": stamps, "o": prices, "h": prices, "l": prices,
                        "c": prices, "v": [100] * len(stamps)}
            rows = deepcopy(prior)
            if clock["index"] >= 4:
                rows.append(dict(today_row))
            if clock["index"] >= 5 and variant == "omit_today":
                rows = deepcopy(prior)
            elif clock["index"] >= 5 and variant == "revise_today":
                rows[-1]["close"] = 33.8
            elif clock["index"] >= 5 and variant == "duplicate_today":
                rows.append({**today_row, "time": today_row["time"] + 3600, "close": 34.6})
            return {key: [row[column] for row in rows]
                    for key, column in (("t", "time"), ("o", "open"), ("h", "high"),
                                        ("l", "low"), ("c", "close"), ("v", "volume"))}

    class WS:
        def __init__(self, *_args): pass
        def set_symbols(self, _symbols): pass
        def start(self): pass
        def stop(self): pass
        def snapshot(self): return {}
        def latest_tick(self, symbol):
            live_ticks.append(clock["at"].isoformat())
            return {"symbol": symbol, "price": 34.8, "bid": 34.8, "ask": 34.8,
                    "timestamp": clock["at"].timestamp(), "source": "WS"}

    monkeypatch.setattr(daemon, "DNSEClient", Client)
    monkeypatch.setattr(daemon, "DNSEMarketWS", WS)
    # MarketDataService stays real, including its cache and raw OHLC normalizer.
    monkeypatch.setattr(daemon.signal, "signal", lambda _sig, handler: handlers.update(stop=handler))
    original_write = RuntimeBridge.write_status
    def publish(value, status):
        statuses.append((clock["at"], deepcopy(status.to_dict())))
        original_write(value, status)
    def next_cycle(*_args):
        clock["index"] += 1
        if clock["index"] == len(POINTS):
            handlers["stop"]()
        else:
            clock["at"] = POINTS[clock["index"]]
    monkeypatch.setattr(RuntimeBridge, "write_status", publish)
    monkeypatch.setattr(daemon.time, "sleep", next_cycle)
    assert daemon.run("OFFLINE_AFTER_CLOSE") == 0
    complete = [(at, status) for at, status in statuses
                if status["daemon_status"] == "RUNNING" and status["decisions"].get("IDC")]
    assert len(complete) == len(POINTS), [(at, status["error"]) for at, status in statuses]
    assert all(not status["error"] for _, status in complete)
    assert all(datetime.fromisoformat(at) < POINTS[3] for at in live_ticks)
    assert OrderQueue(bridge.pending_orders_path).list_all() == []
    assert not bridge.read_config().bot_enabled
    return clock, settings, bridge, complete, requests


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("require_cross", [True, False])
@pytest.mark.parametrize("variant", ["same", "omit_today", "revise_today", "duplicate_today"])
def test_daemon_rest_refresh_after_close_cannot_generate_buy_or_loss(monkeypatch, tmp_path, mode, require_cross, variant):
    clock, settings, bridge, complete, requests = run_daemon(monkeypatch, tmp_path, variant, mode, require_cross)
    from support.tests.test_telegram_buy_lost import ImmediateThread, Telegram
    from viking_v2.connections.telegram import SignalTelegramService
    from viking_v2.dashboard.actions import DashboardActionsMixin
    from viking_v2.rules.state import RuleStateStore
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    app = DashboardActionsMixin()
    app.settings = settings
    app.rule_state = RuleStateStore(tmp_path / "notices.json")
    client = Telegram()
    app.telegram = SignalTelegramService(client, chat_id="offline")
    original_settings = settings.to_dict()
    observations = []
    for at, status in complete:
        clock["at"] = at
        app._technical_signal_status = status
        decision = StrategyDecision.from_dict(status["decisions"]["IDC"])
        before = deepcopy(decision.to_dict())
        tick = status["ticks"]["IDC"]
        assert tick["price"] == 34.8
        app._notify_rule_signal("IDC", decision, tick, execution_mode=mode)
        app._retry_telegram_notices()
        marks = decision.details["indicators"]
        observations.append({"at": at.isoformat(), "phase": status["symbol_phases"]["IDC"],
                             "quote_frozen": tick.get("frozen", False), "samples": marks["sample_count"],
                             "fast": marks["buy_ema_fast"], "rsi": marks["rsi"], "rsi_previous": marks["rsi_previous"],
                             "messages": len(client.sent)})
        assert decision.to_dict() == before
        assert len(client.sent) == 1
        if status["symbol_phases"]["IDC"] == "CLOSED":
            assert app.rule_state.telegram_buy_watch("IDC", mode) == {}
    first, last = observations[0], observations[-1]
    if variant == "same":
        assert all(row["fast"] == first["fast"] and row["rsi"] == first["rsi"] for row in observations)
    else:
        assert last["fast"] != first["fast"]
    if variant == "omit_today":
        assert last["samples"] == first["samples"] - 1
    elif variant == "duplicate_today":
        assert last["samples"] == first["samples"] + 1
        assert last["rsi_previous"] == first["rsi"]
    assert settings.to_dict() == original_settings
    assert app.rule_state.pending_telegram_notices() == []
    assert OrderQueue(bridge.pending_orders_path).list_all() == []
    output = config.PROJECT_ROOT / ".artifacts/telegram_session_fix_20261012"
    output.mkdir(parents=True, exist_ok=True)
    (output / f"daemon_{variant}_{mode.lower()}_{require_cross}.json").write_text(
        json.dumps({"observations": observations, "messages": client.sent}, ensure_ascii=False, indent=2), encoding="utf-8")