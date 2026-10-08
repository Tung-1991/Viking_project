"""Startup is not an API failure; actual missing calendars stay fail-closed."""
from types import SimpleNamespace
import time

import pytest

from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import OrderIntent, RuntimeConfig
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.runtime import RuntimeBridge
from viking_v2.storage import AtomicJSONStore
from viking_v2.trading.market import market_now
from viking_v2.trading.orders import OrderQueue


@pytest.fixture
def alert_subject(monkeypatch, tmp_path):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    subject = SimpleNamespace(
        telegram=SimpleNamespace(notify_system_alert=lambda **kwargs: calls.append(kwargs)),
        rule_state=RuleStateStore(tmp_path / "rules.json"),
        settings=SimpleNamespace(
            telegram_notifications={"system": True}, telegram_cooldown_minutes={"system": 0},
        ),
        real=SimpleNamespace(configured=lambda: True),
    )
    return subject, calls


def test_calendar_loading_does_not_send_a_failure_alert(alert_subject):
    subject, calls = alert_subject
    DashboardActionsMixin._notify_system_health(
        subject, {"api_health": {"rest": {"total_requests": 1, "last_status": 200}}},
        "RUNNING", "CALENDAR_LOADING", "REAL",
    )
    assert calls == []


@pytest.mark.parametrize("state", ["STARTING", "SYNC"])
def test_old_process_errors_are_not_replayed_during_startup(alert_subject, state):
    subject, calls = alert_subject
    subject._daemon_started_at = time.time()
    DashboardActionsMixin._notify_system_health(
        subject, {"heartbeat_at": subject._daemon_started_at - 1, "error": "previous process failed"},
        state, "CALENDAR_UNKNOWN", "REAL",
    )
    assert calls == []


@pytest.mark.parametrize("fault", ["API", "CYCLE"])
def test_sync_does_not_hide_a_fault_from_the_new_daemon(alert_subject, fault):
    subject, calls = alert_subject
    subject._daemon_started_at = time.time() - 20
    status = {"heartbeat_at": subject._daemon_started_at + 1}
    if fault == "API":
        status["api_health"] = {"rest": {"total_requests": 1, "last_status": 500}}
    else:
        status["error"] = "new process failure"
    DashboardActionsMixin._notify_system_health(subject, status, "SYNC", "CALENDAR_LOADING", "REAL")
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_actual_calendar_failure_alerts_once_and_can_recur_after_recovery(alert_subject, mode, monkeypatch):
    subject, calls = alert_subject
    clock = {"now": time.time()}
    monkeypatch.setattr("viking_v2.dashboard.actions.time.time", lambda: clock["now"])
    for _ in range(2):
        DashboardActionsMixin._notify_system_health(subject, {}, "RUNNING", "CALENDAR_UNKNOWN", mode)
    assert len(calls) == 1
    assert calls[0]["summary"] == "KHÔNG ĐỌC ĐƯỢC LỊCH GIAO DỊCH"
    assert calls[0]["execution_mode"] == mode
    DashboardActionsMixin._notify_system_health(subject, {}, "RUNNING", "CLOSED", mode)
    clock["now"] += 1  # Runtime polling is once a second, not several transitions in one instant.
    DashboardActionsMixin._notify_system_health(subject, {}, "RUNNING", "CALENDAR_UNKNOWN", mode)
    assert len(calls) == 2


def test_turning_telegram_on_does_not_lose_an_unsent_current_fault(alert_subject):
    subject, calls = alert_subject
    subject.settings.telegram_notifications["system"] = False
    DashboardActionsMixin._notify_system_health(subject, {}, "RUNNING", "CALENDAR_UNKNOWN", "REAL")
    assert calls == []
    subject.settings.telegram_notifications["system"] = True
    DashboardActionsMixin._notify_system_health(subject, {}, "RUNNING", "CALENDAR_UNKNOWN", "REAL")
    DashboardActionsMixin._notify_system_health(subject, {}, "RUNNING", "CALENDAR_UNKNOWN", "REAL")
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["STOPPED", "STALE", "API", "CYCLE"])
def test_calendar_loading_does_not_hide_other_real_faults(alert_subject, fault):
    subject, calls = alert_subject
    state = fault if fault in {"STOPPED", "STALE"} else "RUNNING"
    status = (
        {"api_health": {"rest": {"total_requests": 1, "last_status": 500}}} if fault == "API"
        else {"error": "cycle failure"} if fault == "CYCLE" else {}
    )
    DashboardActionsMixin._notify_system_health(subject, status, state, "CALENDAR_LOADING", "REAL")
    assert len(calls) == 1
    assert "KHÔNG ĐỌC ĐƯỢC LỊCH GIAO DỊCH" not in calls[0]["summary"]


@pytest.mark.parametrize("phase", ["CALENDAR_LOADING", "CALENDAR_UNKNOWN"])
@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("order_type", ["MARKET", "LO", "ATO", "ATC"])
def test_no_orders_are_claimed_without_a_ready_calendar(tmp_path, phase, mode, order_type):
    queue = OrderQueue(tmp_path / "orders.json")
    for side in ("BUY", "SELL"):
        for source in ("BOT", "MANUAL"):
            queue.add(OrderIntent.create(
                "FPT", side, 100, order_type, limit_price=100,
                execution_mode=mode, source=source, allow_ato=True, allow_atc=True,
            ))
    assert queue.claim_due(phase=phase, execution_mode=mode, token_ready=True) == []
    assert all(item.status == "PENDING" for item in queue.list_all())


@pytest.mark.parametrize("has_live_calendar,has_cache", [(True, False), (False, False), (False, True)])
def test_daemon_publishes_calendar_result_before_slow_history_downloads(
    monkeypatch, tmp_path, has_live_calendar, has_cache,
):
    from viking_v2 import config
    from viking_v2.services import daemon

    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    bridge = RuntimeBridge("STARTUP")
    bridge.write_config(RuntimeConfig(["FPT"], False, False))
    today = market_now().date().isoformat()
    if has_cache:
        AtomicJSONStore(bridge.market_cache_path).write({"working_dates": [today]})
    settings = config.AppSettings(watchlist=["FPT"], priority_symbols=[], paper_mode=False)
    monkeypatch.setattr(daemon, "load_settings", lambda *_args: settings)
    handlers, statuses, history_phases = {}, [], []
    monkeypatch.setattr(daemon.signal, "signal", lambda sig, handler: handlers.update(stop=handler))
    monkeypatch.setattr(daemon.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(daemon, "market_phase", lambda *args, **kwargs: ("OPEN", "OPEN"))
    original_write = RuntimeBridge.write_status

    def publish(self, status):
        statuses.append(status.to_dict())
        original_write(self, status)

    monkeypatch.setattr(RuntimeBridge, "write_status", publish)

    class Client:
        api_key, api_secret = "OFFLINE", "OFFLINE"

        def __init__(self, **kwargs): pass
        def connect(self): return True
        def close(self): pass
        def get_working_dates(self): return [today] if has_live_calendar else []
        def get_secdef(self, symbol): return {"marketId": "STO"}
        def get_balance(self): return {"stock": {"availableCash": 10_000_000}}
        def get_positions(self): return []

    class Market:
        def __init__(self, *args): pass
        def start(self, symbols): pass
        def set_symbols(self, symbols): pass
        def stop(self): pass
        def health(self): return {}
        def get_tick(self, symbol): return None
        def frozen_tick_from_bars(self, symbol, bars): return None

        def get_daily_bars(self, symbol, **kwargs):
            history_phases.append(bridge.read_status()["market_status"])
            handlers["stop"]()
            return []

    monkeypatch.setattr(daemon, "DNSEClient", Client)
    monkeypatch.setattr(daemon, "DNSEMarketWS", lambda *_args: object())
    monkeypatch.setattr(daemon, "MarketDataService", Market)
    assert daemon.run("STARTUP") == 0
    expected = "OPEN" if has_live_calendar or has_cache else "CALENDAR_UNKNOWN"
    assert statuses[0]["market_status"] == "CALENDAR_LOADING"
    assert statuses[0]["working_dates"] == []
    assert history_phases and set(history_phases) == {expected}
    assert statuses[1]["market_status"] == expected
    assert statuses[1]["working_dates"] == ([today] if has_live_calendar or has_cache else [])
    assert statuses[1]["decisions"] == {}
    assert statuses[-1]["daemon_status"] == "STOPPED"
