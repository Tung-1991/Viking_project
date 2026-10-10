"""Offline Telegram digests: real observation/outbox paths, no broker or network."""
from __future__ import annotations

from copy import deepcopy

import pytest

from viking_v2.config import AppSettings
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import OrderIntent, StrategyDecision, TradeCycle
from viking_v2.rules.state import RuleStateStore


class Telegram:
    def __init__(self):
        self.sent = []
        self.calls = 0
        self.fail_calls = set()

    def send_message(self, _chat, text):
        self.calls += 1
        if self.calls in self.fail_calls:
            raise RuntimeError("offline delivery failure")
        self.sent.append(text)


class ImmediateThread:
    def __init__(self, *, target, kwargs, daemon):
        self.target, self.kwargs = target, kwargs

    def start(self):
        self.target(**self.kwargs)


@pytest.fixture
def subject(monkeypatch, tmp_path):
    clock = [1_000_000.0]
    monkeypatch.setattr("viking_v2.rules.state.time.time", lambda: clock[0])
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    app = DashboardActionsMixin()
    app.settings = AppSettings(telegram_enabled=True, telegram_buy_delivery_mode="BATCH",
                               telegram_buy_batch_minutes=10, telegram_batch_technical_signals=True).normalize()
    app.rule_state = RuleStateStore(tmp_path / "rules.json")
    client = Telegram()
    app.telegram = SignalTelegramService(client, chat_id="offline")
    return app, client, clock


def observe(app, *, symbol="HDB", mode="REAL", valid=True, rsi=None, **kwargs):
    marks = {"buy_ema_fast": 22.45, "buy_ema_slow": 22.40,
             "rsi": rsi if rsi is not None else 57.70 if valid else 50.0, "rsi_previous": 56.79}
    decision = StrategyDecision(kwargs.pop("action", "WAIT"), symbol, "BUY_WINDOW_WAIT",
                                signal="BUY" if valid else "", details={
                                    "signal_cycle": "CROSS-A", "indicators": marks})
    original = deepcopy(decision.details)
    app._notify_rule_signal(symbol, decision, {"price": 22.4}, execution_mode=mode, **kwargs)
    assert decision.details == original


def flush(app, clock, seconds=600):
    clock[0] += seconds
    app._retry_telegram_notices()


def test_fifty_symbols_share_one_digest_and_repeated_prices_do_not_add_notices(subject):
    app, client, clock = subject
    original_rules = deepcopy(app.settings.rule_parameters)
    for sample in range(4):
        for index in range(50):
            observe(app, symbol=f"S{index:02d}", rsi=57.70 + sample / 10)
    assert client.sent == [] and len(app.rule_state.pending_telegram_notices()) == 50
    flush(app, clock, 599)
    assert client.sent == []
    flush(app, clock, 1)
    assert len(client.sent) == 1 and "58.00 > 56.79" in client.sent[0]
    for index in range(50):
        assert client.sent[0].count(f"S{index:02d} REAL · BUY") == 1
        observe(app, symbol=f"S{index:02d}")
    app._retry_telegram_notices()
    assert app.rule_state.pending_telegram_notices() == [] and len(client.sent) == 1
    assert app.settings.rule_parameters == original_rules


@pytest.mark.parametrize("states,last", [([True, False], "MẤT BUY"), ([True, False, True], "BUY")])
def test_latest_state_replaces_pending_buy_loss_without_resetting_window(subject, states, last):
    app, client, clock = subject
    started = clock[0]
    for valid in states:
        observe(app, valid=valid)
        clock[0] += 60
    notices = app.rule_state.pending_telegram_notices()
    assert len(notices) == 1 and notices[0]["created_at"] == started
    clock[0] = started + 600
    app._retry_telegram_notices()
    assert len(client.sent) == 1
    assert f"HDB REAL · {last} ·" in client.sent[0]
    assert client.sent[0].count("HDB REAL") == 1


def test_books_and_window_survive_a_new_store_and_service(subject):
    app, client, clock = subject
    observe(app, mode="REAL")
    observe(app, mode="PAPER", valid=True)
    observe(app, mode="PAPER", valid=False)
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    app.telegram = SignalTelegramService(client, chat_id="offline")
    flush(app, clock)
    assert len(client.sent) == 1
    assert "HDB REAL · BUY" in client.sent[0] and "HDB PAPER · MẤT BUY" in client.sent[0]
    assert app.rule_state.pending_telegram_notices() == []


@pytest.mark.parametrize("latest_lost", [False, True])
def test_failed_digest_retries_after_restart_and_backoff(subject, latest_lost):
    app, client, clock = subject
    observe(app)
    client.fail_calls.add(1)
    flush(app, clock)
    assert client.sent == [] and len(app.rule_state.pending_telegram_notices()) == 1
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    app.telegram = SignalTelegramService(client, chat_id="offline")
    if latest_lost:
        observe(app, valid=False)
    flush(app, clock, 59)
    assert client.calls == 1
    flush(app, clock, 1)
    app._retry_telegram_notices()
    assert len(client.sent) == 1 and app.rule_state.pending_telegram_notices() == []
    assert ("HDB REAL · MẤT BUY" in client.sent[0]) is latest_lost


def test_long_digest_is_split_and_successful_chunks_are_not_replayed(subject):
    app, client, clock = subject
    for index in range(150):
        observe(app, symbol=f"S{index:03d}")
    client.fail_calls.add(2)
    flush(app, clock)
    assert len(client.sent) == 1 and app.rule_state.pending_telegram_notices()
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    flush(app, clock, 60)
    assert app.rule_state.pending_telegram_notices() == []
    assert all(len(text) < 4096 for text in client.sent)
    joined = "\n".join(client.sent)
    for index in range(150):
        assert joined.count(f"S{index:03d} REAL · BUY") == 1


@pytest.mark.parametrize("change", ["checkbox_off", "immediate"])
def test_switching_to_individual_delivery_flushes_retained_latest_notice(subject, change):
    app, client, _clock = subject
    observe(app)
    observe(app, valid=False)
    if change == "checkbox_off":
        app.settings.telegram_batch_technical_signals = False
    else:
        app.settings.telegram_buy_delivery_mode = "IMMEDIATE"
    app._retry_telegram_notices()
    assert len(client.sent) == 1 and "MẤT BUY · HDB" in client.sent[0]
    assert app.rule_state.pending_telegram_notices() == []


@pytest.mark.parametrize("change", ["master_off", "category_off", "destination"])
def test_disabling_notifications_or_changing_destination_discards_the_digest(subject, change):
    app, client, clock = subject
    observe(app)
    if change == "master_off":
        app.settings.telegram_enabled = False
    elif change == "category_off":
        app.settings.telegram_notifications["blocked_buy"] = False
    else:
        app.telegram = SignalTelegramService(client, chat_id="other-offline")
    flush(app, clock)
    assert client.sent == [] and app.rule_state.pending_telegram_notices() == []


def test_loss_option_off_drops_obsolete_pending_buy(subject):
    app, client, clock = subject
    app.settings.telegram_notifications["buy_lost"] = False
    observe(app)
    observe(app, valid=False)
    flush(app, clock)
    assert client.sent == [] and app.rule_state.pending_telegram_notices() == []


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_already_queued_buy_never_generates_loss_or_a_stale_positive_digest(subject, mode):
    app, client, clock = subject
    app.settings.telegram_notifications.update(buy_queued=False, closed=False)
    observe(app, mode=mode)
    observe(app, mode=mode, action="BUY", signal_id="T1")
    observe(app, mode=mode, valid=False)
    flush(app, clock)
    assert client.sent == [] and app.rule_state.pending_telegram_notices() == []


@pytest.mark.parametrize("batch_enabled", [True, False])
def test_recovered_conditions_do_not_send_an_obsolete_loss_without_a_new_buy_signal(subject, batch_enabled):
    app, client, clock = subject
    observe(app)
    observe(app, valid=False)
    decision = StrategyDecision("WAIT", "HDB", "NO_NEW_BUY_SIGNAL", signal="", details={
        "indicators": {"buy_ema_fast": 22.45, "buy_ema_slow": 22.40,
                       "rsi": 57.70, "rsi_previous": 56.79}})
    app._notify_rule_signal("HDB", decision, {"price": 22.4}, execution_mode="REAL")
    app.settings.telegram_batch_technical_signals = batch_enabled
    flush(app, clock)
    assert client.sent == [] and app.rule_state.pending_telegram_notices() == []


def test_empty_or_invalid_observations_do_not_invent_a_loss(subject):
    app, client, clock = subject
    observe(app)
    decision = StrategyDecision("WAIT", "HDB", "NO_DATA", details={"indicators": {"rsi": None}})
    app._notify_rule_signal("HDB", decision, {"price": 22.4}, execution_mode="REAL")
    flush(app, clock)
    assert len(client.sent) == 1 and "HDB REAL · BUY" in client.sent[0]


def test_concurrent_polls_cannot_claim_the_same_batch_and_crash_lease_expires(subject, monkeypatch):
    app, client, clock = subject
    workers = []
    class DeferredThread(ImmediateThread):
        def start(self):
            workers.append(self)
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", DeferredThread)
    observe(app)
    flush(app, clock)
    for _ in range(5):
        app._retry_telegram_notices()
    assert len(workers) == 1
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    flush(app, clock, 299)
    assert len(workers) == 1
    flush(app, clock, 1)
    assert len(workers) == 2  # A crashed worker's persisted lease can be reclaimed.
    workers[-1].target(**workers[-1].kwargs)
    assert len(client.sent) == 1 and app.rule_state.pending_telegram_notices() == []


def test_indicator_alert_and_closed_are_immediate_during_a_pending_digest(subject):
    app, client, _clock = subject
    observe(app)
    decision = StrategyDecision("WAIT", "HDB", "INDICATOR_EXIT_ALERT",
                                event="INDICATOR_EXIT_ALERT", signal="SELL", details={
                                    "signal_cycle": "E-A", "indicators": {}})
    app._notify_rule_signal("HDB", decision, {"price": 22.4}, execution_mode="REAL")
    assert len(client.sent) == 1 and "E ALERT" in client.sent[0]
    cycle = TradeCycle(id="T1", symbol="HDB", execution_mode="REAL", source="BOT")
    cycle.record_buy_fill(100, 22.4)
    cycle.record_sell_fill(100, 23.0)
    app.rule_state.open_telegram_signal("HDB", "A", price=22.4, market_state="UPTREND",
                                        signal_id="T1", stream="REAL")
    intent = OrderIntent(id="SELL-1", symbol="HDB", side="SELL", quantity=100,
                         order_type="MARKET", execution_mode="REAL", reason="STOP_LOSS")
    app._notify_bot_trade_event("CLOSED", cycle, intent)
    assert len(client.sent) == 2 and "CLOSED" in client.sent[1]
    assert len(app.rule_state.pending_telegram_notices()) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_option_roundtrips_without_changing_existing_telegram_modes(enabled):
    settings = AppSettings.from_dict({"telegram_batch_technical_signals": enabled})
    restored = AppSettings.from_dict(settings.to_dict())
    assert restored.telegram_batch_technical_signals is enabled
    assert restored.telegram_buy_delivery_mode == "IMMEDIATE"
    assert AppSettings.from_dict({}).telegram_batch_technical_signals is False


def test_single_popup_option_is_a_draft_and_reuses_batch_minutes(ui_root, monkeypatch):
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup
    settings = AppSettings().normalize()
    before = settings.to_dict()
    writes = []
    monkeypatch.setattr("viking_v2.connections.window.save_settings", lambda value, _account: writes.append(value.to_dict()))
    monkeypatch.setattr(ConnectionPopup, "_store_telegram_token", lambda *_args: "RAM")
    monkeypatch.setattr(ConnectionPopup, "show", lambda _self: None)
    client = DNSEClient(account_no="PAPER", api_key="", api_secret="")
    popup = ConnectionPopup(ui_root, settings, "PAPER", client, lambda: None)
    try:
        assert popup.tele_batch_technical_switch.cget("state") == "disabled"
        popup.tele_buy_mode_selector.set("GOM TIN")
        popup.tele_batch_technical.set(True)
        popup._telegram_buy_mode_changed()
        assert popup.tele_batch_technical_switch.cget("state") == "normal"
        assert popup.tele_cooldown_entries["blocked_buy"].cget("state") == "disabled"
        assert popup.tele_cooldown_entries["buy_lost"].cget("state") == "disabled"
        assert popup.tele_cooldown_entries["indicator_exit"].cget("state") == "normal"
        assert settings.to_dict() == before
        popup.tele_batch.delete(0, "end")
        popup.tele_batch.insert(0, "10")
        popup._save_telegram()
        assert len(writes) == 1 and writes[0]["telegram_batch_technical_signals"] is True
        assert settings.telegram_buy_batch_minutes == 10
        popup.tele_buy_mode_selector.set("GỬI NGAY")
        popup._telegram_buy_mode_changed()
        popup._save_telegram()
        assert settings.telegram_batch_technical_signals is True  # Remember preference for next batch use.
        assert not DashboardActionsMixin._technical_telegram_batch_enabled(type("App", (), {"settings": settings})())
        assert popup.tele_cooldown_entries["blocked_buy"].cget("state") == "normal"
        assert settings.rule_parameters == before["rule_parameters"]
    finally:
        popup._close()
        client.close()
