"""Offline technical BUY/loss notices cannot create or cancel orders."""
from __future__ import annotations

from copy import deepcopy

import pytest

from viking_v2.config import AppSettings
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import StrategyDecision
from viking_v2.rules.state import RuleStateStore


class Telegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append(text)


class ImmediateThread:
    def __init__(self, *, target, kwargs, daemon):
        self.target, self.kwargs = target, kwargs

    def start(self):
        self.target(**self.kwargs)


@pytest.fixture
def subject(monkeypatch, tmp_path):
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    value = DashboardActionsMixin()
    value.settings = AppSettings(telegram_enabled=True).normalize()
    value.rule_state = RuleStateStore(tmp_path / "rules.json")
    client = Telegram()
    value.telegram = SignalTelegramService(client, chat_id="offline")
    return value, client


def observe(value, *, mode="REAL", cycle="A", signal="BUY", reason="BUY_WINDOW_WAIT", **changes):
    marks = {"buy_ema_fast": 22.45, "buy_ema_slow": 22.40,
             "rsi": 57.70, "rsi_previous": 56.79}
    decision = StrategyDecision("WAIT", "HDB", reason, signal=signal, details={
        "signal_cycle": cycle, "indicators": {**marks, **changes},
    })
    before = deepcopy(decision.details)
    value._notify_rule_signal("HDB", decision, {"price": 22.4}, execution_mode=mode)
    assert decision.details == before


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("signal,changes", [
    ("BUY", {"rsi": 56.79}),  # Synthetic cancellation still carries the old BUY label.
    ("SELL", {"buy_ema_fast": 22.3, "rsi": 52.29}),
    ("", {"rsi": 56.78}),
])
def test_sent_buy_loses_once_with_numbers_and_no_reason_paragraph(subject, mode, signal, changes):
    value, client = subject
    observe(value, mode=mode)
    for _ in range(10):
        observe(value, mode=mode, signal=signal, reason="BUY_WINDOW_BROKEN", **changes)
    assert len(client.sent) == 2
    lost = client.sent[1]
    assert f"MẤT BUY · HDB · {mode}" in lost
    assert "EMA 3/6:" in lost and "RSI14 nay / phiên trước:" in lost
    assert "56.79" in lost and "Chưa tạo lệnh." in lost
    assert "không còn tăng" not in lost and "BUY_WINDOW_BROKEN" not in lost
    assert len(lost.splitlines()) == 5 and len(lost) < 280
    if changes.get("rsi") == 56.79:
        assert "56.79 = 56.79" in lost
    elif changes.get("rsi") == 52.29:
        assert "52.29 < 56.79" in lost


def test_loss_requires_a_buy_notice_that_was_really_sent(subject):
    value, client = subject
    observe(value, rsi=50.0)
    value.settings.telegram_notifications["blocked_buy"] = False
    observe(value)
    observe(value, rsi=50.0)
    assert client.sent == []


def test_failed_buy_delivery_does_not_produce_orphan_loss(subject, monkeypatch):
    value, client = subject
    monkeypatch.setattr(value.telegram, "notify_technical_buy", lambda **_kwargs: False)
    observe(value)
    observe(value, rsi=50.0)
    assert client.sent == []


def test_loss_option_off_does_not_cancel_later_candidates_or_change_rules(subject):
    value, client = subject
    value.settings.telegram_notifications["buy_lost"] = False
    before = value.settings.to_dict()
    observe(value)
    observe(value, rsi=50.0)
    assert len(client.sent) == 1
    assert value.settings.to_dict() == before


@pytest.mark.parametrize("changes", [
    {"rsi": None}, {"rsi": float("inf")}, {"rsi": -1},
    {"rsi_previous": 101}, {"buy_ema_fast": None},
])
def test_missing_or_invalid_indicators_never_claim_loss(subject, changes):
    value, client = subject
    observe(value)
    observe(value, **changes)
    assert len(client.sent) == 1
    observe(value, rsi=56.79)
    assert len(client.sent) == 2


@pytest.mark.parametrize("action,reason,signal_id", [
    ("BUY", "BUY_SIGNAL", "T1"), ("WAIT", "BUY_ALREADY_PENDING", ""),
])
def test_queued_or_pending_buy_cannot_be_reported_as_lost(subject, action, reason, signal_id):
    value, client = subject
    value.settings.telegram_notifications["buy_queued"] = False
    value.settings.telegram_notifications["closed"] = False
    observe(value)
    decision = StrategyDecision(action, "HDB", reason, signal="BUY", details={
        "indicators": {"buy_ema_fast": 22.45, "buy_ema_slow": 22.40,
                       "rsi": 57.70, "rsi_previous": 56.79},
    })
    value._notify_rule_signal("HDB", decision, {"price": 22.4}, execution_mode="REAL", signal_id=signal_id)
    value.rule_state = RuleStateStore(value.rule_state.store.path)
    observe(value, rsi=50.0)
    assert len(client.sent) == 1


def test_loss_cooldown_is_separate_by_mode_and_persists_restart(subject, monkeypatch):
    value, client = subject
    clock = [1000.0]
    monkeypatch.setattr("viking_v2.rules.state.time.time", lambda: clock[0])
    value.settings.telegram_cooldown_minutes["blocked_buy"] = 0
    observe(value)
    value.rule_state = RuleStateStore(value.rule_state.store.path)
    observe(value, rsi=50.0)
    assert len(client.sent) == 2
    clock[0] += 1
    observe(value, cycle="B")
    observe(value, cycle="B", rsi=50.0)
    assert len(client.sent) == 3  # New BUY delivered, second loss suppressed by its own cooldown.
    observe(value, mode="PAPER")
    observe(value, mode="PAPER", rsi=50.0)
    assert len(client.sent) == 5  # REAL cannot suppress PAPER.
    clock[0] = 4600.0
    observe(value, cycle="C")
    observe(value, cycle="C", rsi=50.0)
    assert len(client.sent) == 7


def test_zero_loss_cooldown_still_deduplicates_each_episode(subject):
    value, client = subject
    value.settings.telegram_cooldown_minutes.update(blocked_buy=0, buy_lost=0)
    for cycle in ("A", "B"):
        observe(value, cycle=cycle)
        for _ in range(4):
            observe(value, cycle=cycle, rsi=50.0)
    assert len(client.sent) == 4


def test_unchanged_valid_ticks_do_not_write_watch_state(subject, monkeypatch):
    value, _client = subject
    observe(value)
    writes = []
    monkeypatch.setattr(value.rule_state.store, "write", lambda data: writes.append(data))
    for price in (57.8, 57.9, 58.0):
        observe(value, rsi=price)
    assert writes == []


def test_late_buy_send_is_followed_by_loss_not_an_orphan(subject, monkeypatch):
    value, client = subject
    workers = []

    class DeferredThread(ImmediateThread):
        def start(self):
            workers.append(self)

    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", DeferredThread)
    observe(value)
    observe(value, rsi=50.0)
    assert client.sent == [] and len(workers) == 1
    worker = workers.pop(0)
    worker.target(**worker.kwargs)
    assert len(client.sent) == 1 and len(workers) == 1
    worker = workers.pop(0)
    worker.target(**worker.kwargs)
    assert len(client.sent) == 2 and "MẤT BUY" in client.sent[1]


def test_loss_message_does_not_round_distinct_comparisons_into_equal_labels():
    assert "56.791 > 56.790" in SignalTelegramService._comparison(56.791, 56.790)
    assert "=" in SignalTelegramService._comparison(56.79, 56.79)


def test_new_option_defaults_roundtrip_and_preserve_explicit_opt_out():
    settings = AppSettings.from_dict({})
    assert settings.telegram_notifications["buy_lost"] is True
    assert settings.telegram_cooldown_minutes["buy_lost"] == 60
    settings.telegram_notifications["buy_lost"] = False
    settings.telegram_cooldown_minutes["buy_lost"] = 7
    saved = AppSettings.from_dict(settings.to_dict())
    assert saved.telegram_notifications["buy_lost"] is False
    assert saved.telegram_cooldown_minutes["buy_lost"] == 7


def test_new_session_does_not_cancel_yesterdays_unfilled_notice(subject):
    value, client = subject
    observe(value)
    value.settings.rule_parameters["buy_ema_slow"] = 8
    observe(value, rsi=50.0)
    assert len(client.sent) == 1


@pytest.mark.parametrize("ema_enabled,rsi_enabled", [(True, False), (False, True)])
def test_loss_uses_only_enabled_buy_indicators(subject, ema_enabled, rsi_enabled):
    value, client = subject
    value.settings.rule_parameters.update(buy_signal_use_ema=ema_enabled, buy_signal_use_rsi=rsi_enabled)
    observe(value)
    observe(value, buy_ema_fast=22.3 if ema_enabled else None, rsi=50.0 if rsi_enabled else None)
    assert len(client.sent) == 2
    assert ("EMA 3/6:" in client.sent[1]) is ema_enabled
    assert ("RSI14 nay / phiên trước:" in client.sent[1]) is rsi_enabled


def test_buy_queued_while_notice_is_in_flight_suppresses_loss(subject, monkeypatch):
    value, client = subject
    value.settings.telegram_notifications.update(buy_queued=False, closed=False)
    workers = []

    class DeferredThread(ImmediateThread):
        def start(self):
            workers.append(self)

    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", DeferredThread)
    observe(value)
    decision = StrategyDecision("BUY", "HDB", "BUY_SIGNAL", signal="BUY", details={
        "indicators": {"buy_ema_fast": 22.45, "buy_ema_slow": 22.40,
                       "rsi": 57.70, "rsi_previous": 56.79},
    })
    value._notify_rule_signal("HDB", decision, {"price": 22.4}, execution_mode="REAL", signal_id="T1")
    observe(value, rsi=50.0)
    worker = workers.pop(0)
    worker.target(**worker.kwargs)
    assert len(client.sent) == 1 and workers == []
