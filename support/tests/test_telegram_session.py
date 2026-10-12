"""Session expiry and delayed technical notices never create trading actions."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from support.tests.telegram_support import set_technical_test_session
from support.tests.test_telegram_buy_lost import ImmediateThread, Telegram
from viking_v2.config import AppSettings
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import StrategyDecision
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


DAY = datetime(2026, 10, 12, tzinfo=VN_TZ)


@pytest.fixture
def subject(monkeypatch, tmp_path):
    clock = [DAY.replace(hour=14, minute=9).timestamp()]
    monkeypatch.setattr("viking_v2.dashboard.actions.time.time", lambda: clock[0])
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    app = DashboardActionsMixin()
    app.settings = AppSettings(telegram_enabled=True).normalize()
    set_technical_test_session(app)
    app._technical_signal_status["symbol_exchanges"].update(IDC="HNX", UPX="UPCOM")
    app._technical_signal_status["working_dates"].append("2026-10-13")
    app.rule_state = RuleStateStore(tmp_path / "rules.json")
    app.queue = OrderQueue(tmp_path / "orders.json")
    app.trade_state = TradeStateStore(tmp_path / "trades.json")
    client = Telegram()
    app.telegram = SignalTelegramService(client, chat_id="offline")
    return app, client, clock


def at(clock, hour, minute, second=0, day=0):
    clock[0] = (DAY + timedelta(days=day)).replace(hour=hour, minute=minute, second=second).timestamp()


def observe(app, *, symbol="HDB", mode="REAL", valid=True, cycle="A"):
    decision = StrategyDecision("WAIT", symbol, "NO_NEW_BUY_SIGNAL", signal="BUY" if valid else "", details={
        "signal_cycle": cycle,
        "ema_cross": {"session": True, "state": "EXPIRED", "valid": False},
        "indicators": {"buy_ema_fast": 22.5, "buy_ema_slow": 22.2,
                       "rsi": 60 if valid else 45, "rsi_previous": 50},
    })
    before = deepcopy(decision.to_dict())
    app._notify_rule_signal(symbol, decision, {"price": 22.4, "timestamp": clock_time(), "frozen": False}, execution_mode=mode)
    assert decision.to_dict() == before


def clock_time():
    import time
    return time.time()


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("symbol", ["HDB", "IDC"])
def test_close_1445_ends_watch_and_no_new_buy_or_loss_until_next_session(subject, mode, symbol):
    app, client, clock = subject
    original_settings = app.settings.to_dict()
    at(clock, 14, 44, 59)
    observe(app, symbol=symbol, mode=mode)
    assert len(client.sent) == 1 and "14:44:59" in client.sent[0]
    for hour, minute in ((14, 45), (14, 49), (15, 28), (16, 9)):
        at(clock, hour, minute)
        observe(app, symbol=symbol, mode=mode, valid=False, cycle=f"lost-{hour}-{minute}")
        observe(app, symbol=symbol, mode=mode, valid=True, cycle=f"new-{hour}-{minute}")
        app._retry_telegram_notices()
        assert len(client.sent) == 1
        assert app.rule_state.telegram_buy_watch(symbol, mode) == {}
    assert app.settings.to_dict() == original_settings
    assert app.queue.list_all() == [] and app.trade_state.list_cycles() == []


@pytest.mark.parametrize("hour,minute,exchange,active", [(9, 5, "HOSE", True), (9, 20, "HOSE", True),
    (14, 35, "HOSE", True), (14, 45, "HOSE", False), (14, 45, "HNX", False),
    (14, 45, "UPCOM", True), (15, 0, "UPCOM", False)])
def test_notification_follows_each_exchange_not_selected_symbol_or_auction_permissions(subject, hour, minute, exchange, active):
    app, client, clock = subject
    app.settings.allow_ato = app.settings.allow_atc = False
    app._technical_signal_status["symbol_exchanges"]["HDB"] = exchange
    at(clock, hour, minute)
    observe(app)
    assert bool(client.sent) is active


def test_lunch_pauses_observation_without_losing_morning_buy(subject):
    app, client, clock = subject
    at(clock, 11, 29)
    observe(app)
    identity = app.rule_state.telegram_buy_watch("HDB", "REAL")["id"]
    at(clock, 11, 30)
    observe(app, valid=False)
    app._retry_telegram_notices()
    assert len(client.sent) == 1
    assert app.rule_state.telegram_buy_watch("HDB", "REAL")["id"] == identity
    at(clock, 13, 0)
    observe(app, valid=False)
    assert len(client.sent) == 2 and "MẤT BUY" in client.sent[1]
    assert "13:00:00" in client.sent[1]


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("batch", [False, True])
def test_preclose_notice_delivered_late_has_original_clock_and_closed_label_without_new_loss(subject, monkeypatch, mode, batch):
    app, client, clock = subject
    app.settings.telegram_buy_delivery_mode = "BATCH" if batch else "IMMEDIATE"
    app.settings.telegram_batch_technical_signals = batch
    app.settings.telegram_buy_batch_minutes = 2
    original = app.telegram.notify_technical_buy
    if not batch:
        monkeypatch.setattr(app.telegram, "notify_technical_buy", lambda **_kw: False)
    at(clock, 14, 44)
    observe(app, mode=mode)
    assert not client.sent and len(app.rule_state.pending_telegram_notices()) == 1
    retained = deepcopy(app.rule_state.pending_telegram_notices()[0]["payload"])
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    at(clock, 15, 28, 5)
    observe(app, mode=mode, valid=False)
    monkeypatch.setattr(app.telegram, "notify_technical_buy", original)
    for _ in range(3):
        app._retry_telegram_notices()
    assert len(client.sent) == 1 and "14:44" in client.sent[0]
    assert "GỬI CHẬM" in client.sent[0] and "PHIÊN ĐÃ ĐÓNG" in client.sent[0]
    assert "15:28:05" not in client.sent[0] and "MẤT BUY" not in client.sent[0].splitlines()[-1]
    assert retained["observed_at"].startswith("2026-10-12T14:44:00")
    assert app.rule_state.telegram_buy_watch("HDB", mode) == {}
    assert app.rule_state.pending_telegram_notices() == []


def test_pending_previous_day_and_old_watch_are_removed_without_replaying_next_day(subject, monkeypatch):
    app, client, clock = subject
    monkeypatch.setattr(app.telegram, "notify_technical_buy", lambda **_kw: False)
    observe(app)
    at(clock, 9, 15, day=1)
    app._retry_telegram_notices()
    assert app.rule_state.pending_telegram_notices() == []
    assert app.rule_state.telegram_buy_watch("HDB", "REAL") == {}
    assert client.sent == []


@pytest.mark.parametrize("origin_hour,origin_minute,delivered", [(14, 44, True), (15, 28, False)])
@pytest.mark.parametrize("batch", [False, True])
def test_legacy_pending_notice_uses_created_time_and_rejects_postclose_or_undated_digest(subject, origin_hour, origin_minute, delivered, batch):
    app, client, clock = subject
    at(clock, origin_hour, origin_minute)
    app.rule_state.queue_telegram_notice("legacy", "legacy", {
        "category": "blocked_buy", "symbol": "HDB", "execution_mode": "REAL", "destination": "offline", "batch_technical": batch,
        "payload": {"symbol": "HDB", "execution_mode": "REAL", "price": 22.4, "market_state": "ACCUMULATION",
                    "indicators": {"buy_ema_fast": 22.5, "buy_ema_slow": 22.2,
                                   "buy_ema_fast_period": 3, "buy_ema_slow_period": 6,
                                   "rsi": 60, "rsi_previous": 50, "rsi_period": 14},
                    "ema_enabled": True, "rsi_enabled": True}}, 0)
    at(clock, 16, 9)
    app._retry_telegram_notices()
    delivered = delivered and not batch
    assert bool(client.sent) is delivered
    assert not app.rule_state.pending_telegram_notices()
    if delivered:
        assert "14:44" in client.sent[0] and "PHIÊN ĐÃ ĐÓNG" in client.sent[0]


def test_clock_crosses_close_between_enqueue_and_worker_start(subject, monkeypatch):
    app, client, clock = subject
    workers = []
    class DeferredThread(ImmediateThread):
        def start(self): workers.append(self)
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", DeferredThread)
    at(clock, 14, 44, 59)
    observe(app)
    assert len(workers) == 1 and client.sent == []
    at(clock, 14, 45)
    workers[0].target(**workers[0].kwargs)
    assert len(client.sent) == 1 and "14:44:59" in client.sent[0] and "PHIÊN ĐÃ ĐÓNG" in client.sent[0]
    app._retry_telegram_notices()
    assert app.rule_state.telegram_buy_watch("HDB", "REAL") == {}


def test_digest_after_hose_close_can_include_upcom_still_open_with_distinct_labels(subject):
    app, client, clock = subject
    app.settings.telegram_buy_delivery_mode = "BATCH"
    app.settings.telegram_batch_technical_signals = True
    app.settings.telegram_buy_batch_minutes = 10
    at(clock, 14, 40)
    observe(app, symbol="HDB")
    observe(app, symbol="UPX")
    at(clock, 14, 50)
    app._retry_telegram_notices()
    assert len(client.sent) == 1
    rows = client.sent[0].splitlines()
    assert "PHIÊN ĐÃ ĐÓNG" in next(row for row in rows if row.startswith("HDB REAL"))
    assert "PHIÊN ĐÃ ĐÓNG" not in next(row for row in rows if row.startswith("UPX REAL"))


@pytest.mark.parametrize("issue", ["calendar", "exchange"])
def test_unknown_calendar_or_exchange_never_produces_new_technical_notice(subject, issue):
    app, client, _ = subject
    if issue == "calendar":
        app._technical_signal_status["working_dates"] = []
    else:
        app._technical_signal_status["symbol_exchanges"].pop("HDB")
    observe(app)
    assert not client.sent and app.rule_state.telegram_buy_watch("HDB", "REAL") == {}
