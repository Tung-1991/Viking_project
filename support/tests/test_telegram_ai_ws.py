from __future__ import annotations

import json
from types import SimpleNamespace

import msgpack
import pytest

from viking_v2.connections.dnse.websocket import DNSEMarketWS
from viking_v2.connections.telegram import SignalTelegramService, TelegramClient
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import BrokerOrderResult, OrderIntent, StrategyDecision, TradeCycle
from viking_v2.rules.state import RuleStateStore
from viking_v2.storage import SignalLog


class Telegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))


def test_telegram_client_redacts_token_from_transport_errors():
    client = TelegramClient("secret-token")
    message = client.safe_error(
        "500 https://api.telegram.org/botsecret-token/sendMessage"
    )

    assert "secret-token" not in message
    assert "<REDACTED>" in message


def test_protect_alert_names_the_enabled_dynamic_layers_without_sending_orders():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_protect_alert(
        symbol="VIX", price=14.0, mfe_pct=5.5, peak_price=14.25,
        protect_price=14.06875, sell_pct=100, dynamic=True,
        atr_pct=4.118, atr_activation_multiplier=0.6,
        atr_multiplier=0.8, atr_activation_enabled=False,
        atr_trail_enabled=False, retention_pct=87.5,
        retention_until_pct=5, retention_enabled=True,
        retention_until_enabled=False,
    )
    message = tele.sent[0][1]
    assert "START ATR: OFF · TRAIL ATR: OFF" in message
    assert "Giữ 87.5% lãi cao nhất tới ARM" in message
    assert "ALERT chỉ ghi nhận, không đặt lệnh" in message


def test_protect_alert_hides_dormant_layers_when_dynamic_is_off():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_protect_alert(
        symbol="VIX", price=14.0, mfe_pct=7.0, peak_price=14.25,
        protect_price=13.9, sell_pct=100, dynamic=False,
        atr_pct=4.118, atr_activation_multiplier=0.6,
        atr_multiplier=0.8, retention_pct=87.5,
        retention_until_pct=5,
    )
    message = tele.sent[0][1]
    assert "START ATR: OFF · TRAIL ATR: OFF" in message
    assert "Giữ lãi: OFF" in message


def test_protect_auto_notification_states_that_sell_was_requested():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")

    assert service.notify_protect_alert(
        symbol="VIX", price=14.0, mfe_pct=7.0, peak_price=14.25,
        protect_price=13.9, sell_pct=100, dynamic=False, policy="AUTO",
    )

    message = tele.sent[0][1]
    assert "PROTECT HIT · VIX" in message
    assert "AUTO · ĐÃ TẠO YÊU CẦU BÁN" in message
    assert "ALERT chỉ ghi nhận" not in message


def test_indicator_exit_alert_explains_signal_and_never_claims_an_order():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_indicator_exit_alert(
        symbol="VIX", price=14.0,
        ema_fast_period=3, ema_slow_period=6,
        ema_fast=13.9, ema_slow=14.1,
        rsi_period=14, rsi=47.2, rsi_previous=51.4,
    )
    message = tele.sent[0][1]
    assert "E ALERT · VIX" in message
    assert "EMA 3/6" in message
    assert "RSI14: 51.40 → 47.20" in message
    assert "ALERT chỉ ghi nhận, không đặt lệnh" in message


def test_telegram_sends_one_buy_and_only_its_matching_closed_summary():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_buy(
        symbol="FPT", signal_id="ABCDEF1234", price=69.2, market_state="UPTREND"
    )
    cycle = TradeCycle(
        id="ABCDEF1234", symbol="FPT", execution_mode="REAL",
        em_modes=["NORMAL", "IND_EXIT"],
    )
    cycle.record_buy_fill(100, 69.2, 3_000)
    cycle.mark_exit_once("NORMAL_PROTECTION")
    cycle.mark_exit_once("INDICATOR_EXIT")
    cycle.record_sell_fill(100, 74.0, 4_000)
    assert service.notify_closed(cycle=cycle, reason="INDICATOR_EXIT")

    assert len(tele.sent) == 2
    assert "BUY · FPT" in tele.sent[0][1]
    assert "ĐÃ XẾP LỆNH" in tele.sent[0][1]
    assert "không xác nhận đã gửi/khớp" in tele.sent[0][1]
    assert "ABCDEF1234" in tele.sent[0][1]
    assert "CLOSED · FPT · REAL" in tele.sent[1][1]
    assert "ABCDEF1234" in tele.sent[1][1]
    assert "Lãi/lỗ ròng: +473,000đ (+6.84%)" in tele.sent[1][1]
    assert "E/M bật: PROTECT · E" in tele.sent[1][1]
    assert "E/M kích hoạt: 2 lần · PROTECT ×1 · E ×1" in tele.sent[1][1]
    assert "Lý do: INDICATOR EXIT" in tele.sent[1][1]


def test_telegram_batches_buy_signals_in_one_fixed_window():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=30)
    assert service.notify_buy(
        symbol="FPT", signal_id="FPT1", price=69.2, market_state="UPTREND"
    )
    first_timer = service._buy_timer
    assert service.notify_buy(
        symbol="HPG", signal_id="HPG1", price=27.5, market_state="UPTREND"
    )
    assert service._buy_timer is first_timer
    assert tele.sent == []

    assert service.flush_buys()
    assert len(tele.sent) == 1
    assert "BUY ĐÃ XẾP LỆNH · 2 MÃ" in tele.sent[0][1]
    assert "không xác nhận đã gửi/khớp" in tele.sent[0][1]
    assert "FPT · 69,200đ · UPTREND · FPT1" in tele.sent[0][1]
    assert "HPG · 27,500đ · UPTREND · HPG1" in tele.sent[0][1]


def test_single_buy_is_reported_after_batch_window_without_another_buy():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=30)
    assert service.notify_buy(
        symbol="HDB", signal_id="HDB1", price=22.5, market_state="ACCUMULATION",
    )
    assert service._buy_timer is not None and tele.sent == []
    assert service.flush_buys()
    assert len(tele.sent) == 1 and "BUY · HDB" in tele.sent[0][1]
    assert service._buy_timer is None
    assert service.flush_buys() is False
    assert len(tele.sent) == 1  # No empty periodic digests after this window.


@pytest.fixture
def buy_timers(monkeypatch):
    timers = []

    class Timer:
        def __init__(self, interval, function):
            self.interval, self.function = interval, function
            self.canceled = False
            self.started = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.canceled = True

    monkeypatch.setattr("viking_v2.connections.telegram.threading.Timer", Timer)
    return timers


def test_immediate_buy_sends_each_order_without_batch_timer(buy_timers):
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=0)
    for symbol in ("HDB", "MSN"):
        assert service.notify_buy(symbol=symbol, signal_id=symbol + "1", price=22.5,
                                  market_state="ACCUMULATION", execution_mode="REAL")
    assert len(tele.sent) == 2
    assert "BUY · HDB · REAL · ĐÃ XẾP LỆNH" in tele.sent[0][1]
    assert "BUY · MSN · REAL · ĐÃ XẾP LỆNH" in tele.sent[1][1]
    assert all("không xác nhận đã gửi/khớp" in message for _, message in tele.sent)
    assert buy_timers == [] and service._pending_buys == {}


def test_switch_to_immediate_flushes_existing_batch_once_in_worker(buy_timers):
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=30)
    for symbol in ("HDB", "MSN"):
        service.notify_buy(symbol=symbol, signal_id=symbol + "1", price=22.5,
                           market_state="ACCUMULATION")
    old_timer = service._buy_timer
    service.configure_buy_delivery(buy_batch_minutes=0)
    assert old_timer.canceled and tele.sent == []  # No network work in UI callback.
    assert service._buy_timer.interval == 0 and service._buy_timer.started
    service._buy_timer.function()
    old_timer.function()  # Even a racing old callback cannot send the batch twice.
    assert len(tele.sent) == 1 and "2 MÃ" in tele.sent[0][1]
    assert service._pending_buys == {} and service._buy_timer is None
    service.notify_buy(symbol="CTS", signal_id="CTS1", price=19.65, market_state="UPTREND")
    assert len(tele.sent) == 2 and "BUY · CTS" in tele.sent[1][1]


def test_failed_flush_after_immediate_switch_retries_without_zero_delay_loop(buy_timers):
    class OfflineTelegram(Telegram):
        def send_message(self, chat_id, text):
            raise RuntimeError("offline")

    service = SignalTelegramService(OfflineTelegram(), chat_id="7", buy_batch_minutes=30)
    service.notify_buy(symbol="HDB", signal_id="HDB1", price=22.5, market_state="UPTREND")
    service.configure_buy_delivery(buy_batch_minutes=0)
    service._buy_timer.function()
    assert len(service._pending_buys) == 1
    assert service._buy_timer.interval >= 60
    service.client = Telegram()
    service._buy_timer.function()
    assert len(service.client.sent) == 1 and service._pending_buys == {}


def test_disabled_category_during_failed_flush_cannot_requeue_notices(buy_timers):
    class TurnOffWhileSending(Telegram):
        def send_message(self, chat_id, text):
            service.cancel_pending_buys()
            raise RuntimeError("offline")

    service = SignalTelegramService(TurnOffWhileSending(), chat_id="7", buy_batch_minutes=30)
    service.notify_buy(symbol="HDB", signal_id="HDB1", price=22.5, market_state="UPTREND")
    assert service.flush_buys() is False
    assert service._buy_timer is None and service._pending_buys == {}


def _reload_subject(monkeypatch):
    from viking_v2.config import AppSettings

    tele = Telegram()
    monkeypatch.setattr("viking_v2.dashboard.actions.TelegramClient", lambda _token: tele)
    subject = DashboardActionsMixin()
    subject.settings = AppSettings(telegram_enabled=True, telegram_chat_id="7").normalize()
    subject.telegram = None
    subject._telegram_signature = None
    subject._telegram_session_token = "fake-test-token"
    subject.logs = []
    subject._log = subject.logs.append
    return subject, tele


def test_reload_telegram_defaults_immediate_and_only_changes_delivery(monkeypatch, buy_timers):
    subject, tele = _reload_subject(monkeypatch)
    original_rules = dict(subject.settings.rule_parameters)
    original_categories = dict(subject.settings.telegram_notifications)
    subject._reload_telegram()
    assert subject.telegram.buy_batch_seconds == 0
    assert "BUY gửi ngay" in subject.logs[-1]
    subject.telegram.notify_buy(symbol="HDB", signal_id="HDB1", price=22.5, market_state="UPTREND")
    assert len(tele.sent) == 1 and buy_timers == []
    service = subject.telegram
    subject.settings.telegram_buy_delivery_mode = "BATCH"
    subject.settings.telegram_buy_batch_minutes = 7
    subject._reload_telegram()
    assert subject.telegram is service and service.buy_batch_seconds == 420
    assert "BUY gom 7 phút" in subject.logs[-1]
    service.notify_buy(symbol="MSN", signal_id="MSN1", price=74.2, market_state="UPTREND")
    assert len(tele.sent) == 1
    subject.settings.telegram_buy_delivery_mode = "IMMEDIATE"
    subject._reload_telegram()
    assert subject.telegram is service and service.buy_batch_seconds == 0
    service._buy_timer.function()
    assert len(tele.sent) == 2 and "BUY · MSN" in tele.sent[-1][1]
    assert subject.settings.rule_parameters == original_rules
    assert subject.settings.telegram_notifications == original_categories


@pytest.mark.parametrize("disabled", ["master", "category", "recipient"])
def test_reload_drops_pending_buy_if_off_or_destination_changes(monkeypatch, buy_timers, disabled):
    subject, tele = _reload_subject(monkeypatch)
    subject.settings.telegram_buy_delivery_mode = "BATCH"
    subject._reload_telegram()
    service = subject.telegram
    service.notify_buy(symbol="HDB", signal_id="HDB1", price=22.5, market_state="UPTREND")
    timer = service._buy_timer
    subject.settings.telegram_buy_delivery_mode = "IMMEDIATE"
    if disabled == "master":
        subject.settings.telegram_enabled = False
    elif disabled == "category":
        subject.settings.telegram_notifications["buy_queued"] = False
    else:
        subject.settings.telegram_chat_id = "8"
    subject._reload_telegram()
    assert timer.canceled and service._pending_buys == {}
    timer.function()
    assert tele.sent == []


def test_telegram_waiting_for_buy_window_never_claims_a_sent_order():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_signal_only(
        symbol="MSN", signal="BUY", price=74.4, market_state="UPTREND",
        blocked_by="CHỜ GIỜ MUA · TỪ 14:00", execution_mode="REAL",
    )
    assert "CHƯA GỬI: CHỜ GIỜ MUA · TỪ 14:00" in tele.sent[0][1]
    assert "ĐÃ XẾP LỆNH" not in tele.sent[0][1]


def test_telegram_blocked_buy_explains_whipsaw_without_internal_reason_code():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_signal_only(
        symbol="MSN", signal="BUY", price=74.4, market_state="UPTREND",
        blocked_by="WHIPSAW_LOCK",
    )
    assert "CHƯA GỬI: WHIPSAW đang khóa BUY" in tele.sent[0][1]
    assert "WHIPSAW_LOCK" not in tele.sent[0][1]


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("reason,text", [
    ("BUY_WINDOW_WAIT", "CHỜ GIỜ MUA · TỪ 14:00"),
    ("NO_AVAILABLE_CAPITAL", "KHÔNG ĐỦ VỐN"),
    ("WHIPSAW_LOCK", "WHIPSAW KHÓA BUY"),
    ("LOCKED_AFTER_LOSSES", "KHÓA SAU LỖ"),
    ("BOT_OFF", "BOT OFF"),
    ("MAX_POSITIONS", "ĐỦ SLOT"),
])
def test_default_technical_buy_alert_ignores_entry_locks_and_creates_no_order(
    monkeypatch, tmp_path, mode, reason, text,
):
    from viking_v2.config import AppSettings
    from viking_v2.trading.orders import OrderQueue
    from viking_v2.trading.state import TradeStateStore

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    now = [1000.0]
    monkeypatch.setattr("viking_v2.rules.state.time.time", lambda: now[0])
    tele = Telegram()
    subject = DashboardActionsMixin()
    subject.settings = AppSettings(telegram_enabled=True).normalize()
    subject.telegram = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=30)
    subject.rule_state = RuleStateStore(tmp_path / "rules.json")
    subject.queue = OrderQueue(tmp_path / "orders.json")
    subject.trade_state = TradeStateStore(tmp_path / "trades.json")
    settings_before = subject.settings.to_dict()
    decision = StrategyDecision("WAIT", "MSN", reason, signal="BUY", details={
        "signal_cycle": "SIGNAL1", "status_text": text,
        "indicators": {"buy_ema_fast": 74.4, "buy_ema_slow": 73.0,
                       "rsi": 55.0, "rsi_previous": 50.0},
    })
    for _ in range(10):
        subject._notify_rule_signal("MSN", decision, {"price": 74.4}, execution_mode=mode)
    assert len(tele.sent) == 1
    assert f"TÍN HIỆU BUY · MSN · {mode}" in tele.sent[0][1]
    assert "EMA 3/6" in tele.sent[0][1]
    assert "RSI14: 50.00 → 55.00" in tele.sent[0][1]
    assert "CHƯA GỬI" not in tele.sent[0][1]
    assert text not in tele.sent[0][1]
    assert "ĐÃ XẾP LỆNH" not in tele.sent[0][1]
    assert subject.telegram._buy_timer is None  # No 30-minute BUY batching delay.
    decision.details["signal_cycle"] = "SIGNAL2"
    now[0] += 60
    subject._notify_rule_signal("MSN", decision, {"price": 74.4}, execution_mode=mode)
    assert len(tele.sent) == 1
    now[0] = 4599.0
    subject._notify_rule_signal("MSN", decision, {"price": 74.4}, execution_mode=mode)
    assert len(tele.sent) == 1
    now[0] = 4600.0
    subject._notify_rule_signal("MSN", decision, {"price": 74.4}, execution_mode=mode)
    assert len(tele.sent) == 2
    subject.settings.telegram_notifications["blocked_buy"] = False
    decision.details["signal_cycle"] = "SIGNAL3"
    now[0] += 3600
    subject._notify_rule_signal("MSN", decision, {"price": 74.4}, execution_mode=mode)
    assert len(tele.sent) == 2  # Explicit opt-out still works.
    subject.settings.telegram_notifications["blocked_buy"] = True
    assert subject.settings.to_dict() == settings_before
    assert subject.queue.list_all() == []
    assert subject.trade_state.list_cycles() == []
    assert subject.rule_state.active_telegram_signal("MSN", mode) is None


def _technical_subject(monkeypatch, tmp_path):
    from viking_v2.config import AppSettings

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    tele = Telegram()
    subject = DashboardActionsMixin()
    subject.settings = AppSettings(telegram_enabled=True).normalize()
    subject.telegram = SignalTelegramService(tele, chat_id="7")
    subject.rule_state = RuleStateStore(tmp_path / "rules.json")
    return subject, tele


def test_technical_alert_and_successful_buy_notification_are_separate(monkeypatch, tmp_path):
    subject, tele = _technical_subject(monkeypatch, tmp_path)
    decision = StrategyDecision("BUY", "HDB", "BUY_SIGNAL", signal="BUY", details={
        "signal_cycle": "CROSS1", "candle_key": "DAY1",
        "indicators": {"buy_ema_fast": 22.5, "buy_ema_slow": 22.2,
                       "rsi": 55.0, "rsi_previous": 50.0},
    })
    subject._notify_rule_signal(
        "HDB", decision, {"price": 22.5}, signal_id="T1", execution_mode="REAL",
    )
    assert len(tele.sent) == 2
    assert "TÍN HIỆU BUY" in tele.sent[0][1] and "ĐÃ XẾP LỆNH" not in tele.sent[0][1]
    assert "ĐÃ XẾP LỆNH" in tele.sent[1][1]
    assert "không xác nhận đã gửi/khớp" in tele.sent[1][1]


@pytest.mark.parametrize("mode,fee_rate", [("REAL", .0012), ("PAPER", .00045)])
@pytest.mark.parametrize("order_type,budget_price", [("MARKET", 79.3), ("LO", 74.2)])
def test_compact_buy_uses_actual_intent_quantity_and_reserved_price_not_signal_price(
    monkeypatch, tmp_path, mode, fee_rate, order_type, budget_price,
):
    from datetime import datetime
    from viking_v2.trading.market import VN_TZ

    subject, tele = _technical_subject(monkeypatch, tmp_path)
    subject.settings.telegram_notifications["blocked_buy"] = False
    intent = OrderIntent(
        id="abc12345-rest", symbol="MSN", side="BUY", quantity=200, order_type=order_type,
        source="BOT", execution_mode=mode, trade_id="T1",
        created_at=datetime(2026, 10, 9, 14, 5, tzinfo=VN_TZ).timestamp(),
        entry_budget=16_000_000 / (1 + fee_rate), details={"reservation_price": budget_price},
    )
    before = intent.to_dict()
    decision = StrategyDecision("BUY", "MSN", "BUY_SIGNAL", signal="BUY", details={
        "signal_cycle": "CROSS1", "entry_checks": {"buy_fee_rate": fee_rate},
    })
    subject._notify_rule_signal(
        "MSN", decision, {"price": 74.2}, signal_id="T1", entry_id=intent.id,
        execution_mode=mode, order_intent=intent,
    )
    assert len(tele.sent) == 1
    message = tele.sent[0][1]
    assert f"BUY · MSN · {mode} · ĐÃ XẾP LỆNH · #abc12345" in message
    assert f"14:05 · {order_type} · 200 CP · Giá tín hiệu 74,200đ" in message
    assert f"Giá tính vốn {budget_price * 1000:,.0f}đ" in message
    assert f"{200 * budget_price * 1000 * (1 + fee_rate) / 1_000_000:.2f} tr" in message
    assert f"gồm phí {200 * budget_price * fee_rate:.1f}K" in message
    assert "/ vốn 16.00 tr" in message
    assert "Chưa xác nhận đã gửi/khớp" in message
    assert len(message.splitlines()) == 4 and len(message) < 330
    assert intent.to_dict() == before  # Notification cannot mutate the actual order.


def test_batched_buy_keeps_compact_order_information_and_original_creation_time(buy_timers):
    from datetime import datetime
    from viking_v2.trading.market import VN_TZ

    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=30)
    info = {
        "order_id": "abcdef123", "quantity": 200, "order_type": "MARKET",
        "created_at": datetime(2026, 10, 9, 14, 5, tzinfo=VN_TZ).timestamp(),
        "budget_price": 79.3, "gross_vnd": 15_879_032, "fee_vnd": 19_032,
        "budget_vnd": 16_000_000,
    }
    for mode in ("REAL", "PAPER"):
        service.notify_buy(symbol="MSN", signal_id=mode, price=74.2, market_state="UPTREND",
                           execution_mode=mode, order_info=info)
    info["quantity"] = 100  # Queued payloads must be snapshots, not mutable UI drafts.
    assert tele.sent == [] and service.flush_buys()
    message = tele.sent[0][1]
    assert "2 LỆNH · 1 MÃ" in message
    assert "BUY · MSN · REAL" in message and "BUY · MSN · PAPER" in message
    assert message.count("14:05 · MARKET · 200 CP") == 2
    assert message.count("gồm phí 19.0K") == 2
    assert message.count("Chưa xác nhận đã gửi/khớp") == 1


@pytest.mark.parametrize("changes", [
    {"buy_ema_fast": 22.1}, {"rsi": 49.0}, {"rsi": None},
    {"buy_ema_slow": float("nan")}, {"rsi": float("inf")},
])
def test_lost_or_invalid_conditions_do_not_alert_or_consume_cooldown(monkeypatch, tmp_path, changes):
    subject, tele = _technical_subject(monkeypatch, tmp_path)
    indicators = {"buy_ema_fast": 22.5, "buy_ema_slow": 22.2,
                  "rsi": 55.0, "rsi_previous": 50.0}
    decision = StrategyDecision("WAIT", "HDB", "BUY_WINDOW_BROKEN", signal="BUY", details={
        "signal_cycle": "CROSS1", "indicators": {**indicators, **changes},
    })
    subject._notify_rule_signal("HDB", decision, {"price": 22.5}, execution_mode="REAL")
    assert tele.sent == []
    decision.details["indicators"] = indicators
    decision.reason = "WHIPSAW_LOCK"
    subject._notify_rule_signal("HDB", decision, {"price": 22.5}, execution_mode="REAL")
    assert len(tele.sent) == 1  # Bad data must not suppress the next valid candidate.


@pytest.mark.parametrize("ema_enabled,rsi_enabled,expected", [
    (True, False, "EMA 3/6"), (False, True, "RSI14"),
])
def test_technical_alert_only_requires_selected_ema_rsi(monkeypatch, tmp_path, ema_enabled, rsi_enabled, expected):
    subject, tele = _technical_subject(monkeypatch, tmp_path)
    subject.settings.rule_parameters.update(
        buy_signal_use_ema=ema_enabled, buy_signal_use_rsi=rsi_enabled,
    )
    marks = ({"buy_ema_fast": 22.5, "buy_ema_slow": 22.2} if ema_enabled
             else {"rsi": 55.0, "rsi_previous": 50.0})
    decision = StrategyDecision("WAIT", "HDB", "WHIPSAW_LOCK", signal="BUY", details={
        "signal_cycle": "CROSS1", "indicators": marks,
    })
    subject._notify_rule_signal("HDB", decision, {"price": 22.5}, execution_mode="REAL")
    assert len(tele.sent) == 1 and expected in tele.sent[0][1]
    assert ("EMA 3/6" in tele.sent[0][1]) is ema_enabled
    assert ("RSI14" in tele.sent[0][1]) is rsi_enabled


def test_changing_block_reason_does_not_duplicate_the_same_technical_signal(monkeypatch, tmp_path):
    subject, tele = _technical_subject(monkeypatch, tmp_path)
    subject.settings.telegram_cooldown_minutes["blocked_buy"] = 0
    decision = StrategyDecision("WAIT", "HDB", "BUY_WINDOW_WAIT", signal="BUY", details={
        "signal_cycle": "CROSS1", "indicators": {
            "buy_ema_fast": 22.5, "buy_ema_slow": 22.2,
            "rsi": 55.0, "rsi_previous": 50.0,
        },
    })
    for reason in ("BUY_WINDOW_WAIT", "WHIPSAW_LOCK", "LOCKED_AFTER_LOSSES", "BOT_OFF"):
        decision.reason = reason
        subject._notify_rule_signal("HDB", decision, {"price": 22.5}, execution_mode="REAL")
    assert len(tele.sent) == 1


@pytest.mark.parametrize("reason,portfolio,whipsaw_count", [
    ("WHIPSAW_LOCK", {"available_capital": 100_000_000}, 3),
    ("LOCKED_AFTER_LOSSES", {"available_capital": 100_000_000, "loss_streak": 3}, 0),
    ("NO_AVAILABLE_CAPITAL", {"available_capital": 0}, 0),
])
def test_technical_notification_does_not_open_entry_guards_after_14h(
    monkeypatch, tmp_path, reason, portfolio, whipsaw_count,
):
    from datetime import datetime
    from viking_v2.rules.business import StaticRule
    from viking_v2.rules.entry_filters import apply_buy_filters
    from viking_v2.trading.market import VN_TZ

    subject, tele = _technical_subject(monkeypatch, tmp_path)
    monkeypatch.setattr("viking_v2.rules.business.crossover_count", lambda *_args: whipsaw_count)
    bars = [{"close": value, "volume": 1_000_000, "closed": True}
            for value in ([100] * 15 + [99, 98, 97, 98, 100])]
    context = {"symbol": "HDB", "bars": bars, "previous_market_state": "UPTREND", "signal_mode": "REALTIME"}
    rule = StaticRule()
    original = rule.evaluate(context, portfolio)
    _, decision = apply_buy_filters(
        rule, original, context, portfolio, {},
        observed_at=datetime(2026, 10, 9, 14, 1, tzinfo=VN_TZ), exchange="HOSE",
    )
    decision.details["signal_cycle"] = "CROSS1"
    assert decision.action == "WAIT" and decision.reason == reason
    subject._notify_rule_signal("HDB", decision, {"price": 22.5}, execution_mode="REAL")
    assert len(tele.sent) == 1 and "TÍN HIỆU BUY" in tele.sent[0][1]
    assert decision.action == "WAIT" and decision.reason == reason
    assert subject.rule_state.active_telegram_signal("HDB", "REAL") is None


def test_closed_flushes_queued_buy_before_summary():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7", buy_batch_minutes=30)
    service.notify_buy(
        symbol="FPT", signal_id="T1", price=69.2, market_state="UPTREND"
    )
    cycle = TradeCycle(id="T1", symbol="FPT", execution_mode="PAPER")
    cycle.record_buy_fill(100, 69.2)
    cycle.record_sell_fill(100, 70.0)

    assert service.notify_closed(cycle=cycle, reason="STOP_LOSS")
    assert len(tele.sent) == 2
    assert "BUY · FPT" in tele.sent[0][1]
    assert "CLOSED · FPT" in tele.sent[1][1]


def test_telegram_signal_state_is_persistent_and_deduplicated(tmp_path):
    state = RuleStateStore(tmp_path / "rule_state.json")
    opened = state.open_telegram_signal(
        "FPT", "2026-08-14", price=69.2, market_state="UPTREND"
    )
    assert opened
    assert state.open_telegram_signal(
        "FPT", "2026-08-14", price=69.2, market_state="UPTREND"
    ) is None

    reopened = RuleStateStore(tmp_path / "rule_state.json")
    assert reopened.active_telegram_signal("FPT")["id"] == opened["id"]
    assert reopened.claim_closed_telegram_signal("FPT", "WRONG") is None
    closed = reopened.claim_closed_telegram_signal("FPT", opened["id"])
    assert closed and closed["id"] == opened["id"]
    assert closed["buy_price"] == 69.2
    assert state.active_telegram_signal("FPT") is None


def test_telegram_signal_can_use_the_trade_id_and_corporate_action_is_outbound(tmp_path):
    state = RuleStateStore(tmp_path / "rule_state.json")
    opened = state.open_telegram_signal(
        "FPT", "2026-09-09", price=100.0, market_state="ACCUMULATION",
        signal_id="TRADE-001",
    )
    assert opened and opened["id"] == "TRADE-001"

    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_corporate_action(symbol="FPT", ex_date="2026-09-21")
    assert "CHỐT QUYỀN · FPT" in tele.sent[0][1]
    assert "Ngày GDKHQ: 2026-09-21" in tele.sent[0][1]
    assert "không tự bán" in tele.sent[0][1]


def test_telegram_reports_market_holiday_and_system_error_separately():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")

    assert service.notify_market_holiday(
        holiday_date="2026-09-02", execution_mode="PAPER",
    )
    assert service.notify_system_alert(
        summary="DNSE WS MẤT KẾT NỐI", execution_mode="REAL",
    )

    assert "NGHỈ GIAO DỊCH · PAPER" in tele.sent[0][1]
    assert "2026-09-02" in tele.sent[0][1]
    assert "HỆ THỐNG CẦN KIỂM TRA · REAL" in tele.sent[1][1]
    assert "DNSE WS MẤT KẾT NỐI" in tele.sent[1][1]


def test_dashboard_calendar_and_health_notifications_are_filtered_and_deduplicated(
    monkeypatch, tmp_path,
):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    class Service:
        @staticmethod
        def notify_market_holiday(**kwargs):
            calls.append(("holiday", kwargs))
            return True

        @staticmethod
        def notify_system_alert(**kwargs):
            calls.append(("system", kwargs))
            return True

    class Subject:
        telegram = Service()
        rule_state = RuleStateStore(tmp_path / "rule_state.json")
        settings = SimpleNamespace(
            telegram_notifications={"corporate_action": True, "system": True},
            telegram_cooldown_minutes={"corporate_action": 1440, "system": 15},
        )
        real = SimpleNamespace(configured=lambda: True)

    monkeypatch.setattr(
        "viking_v2.dashboard.actions.threading.Thread", ImmediateThread,
    )
    subject = Subject()
    DashboardActionsMixin._notify_market_holiday(subject, "HOLIDAY", "PAPER")
    DashboardActionsMixin._notify_market_holiday(subject, "HOLIDAY", "PAPER")
    DashboardActionsMixin._notify_system_health(
        subject,
        {
            "error": "cycle failed",
            "api_health": {
                "rest": {"total_requests": 3, "last_status": 500},
                "websocket": {"running": False, "connected": False, "authenticated": False},
            },
        },
        "STALE",
        "OPEN",
        "REAL",
    )
    DashboardActionsMixin._notify_system_health(
        subject,
        {
            "error": "cycle failed",
            "api_health": {
                "rest": {"total_requests": 3, "last_status": 500},
                "websocket": {"running": False, "connected": False, "authenticated": False},
            },
        },
        "STALE",
        "OPEN",
        "REAL",
    )

    assert [kind for kind, _kwargs in calls] == ["holiday", "system"]
    system_summary = calls[1][1]["summary"]
    assert "DAEMON STALE" in system_summary
    assert "DNSE API HTTP 500" in system_summary
    assert "DNSE WS MẤT KẾT NỐI" in system_summary


def test_rejected_bot_buy_is_written_back_to_signal_history(tmp_path):
    class Subject(DashboardActionsMixin):
        pass

    subject = object.__new__(Subject)
    subject.rule_state = RuleStateStore(tmp_path / "rule_state.json")
    subject.signal_log = SignalLog(tmp_path / "signal_log.csv")
    subject.settings = SimpleNamespace(
        rule_parameters={"max_positions": 5}, watchlist=["FPT"],
    )
    subject.snapshots = {"PAPER": ({}, [], [])}
    subject.queue = SimpleNamespace(list_all=lambda: [])
    subject.telegram = None
    subject._shared_tick = lambda _symbol: {"price": 100.0}
    subject._symbol_exchange = lambda _symbol: "HOSE"
    subject.rule_state.open_telegram_signal(
        "FPT", "D1", price=100.0, market_state="UPTREND", signal_id="T1",
    )
    intent = OrderIntent.create(
        "FPT", "BUY", 100, "MARKET", execution_mode="PAPER",
        source="BOT", trade_id="T1", signal="BUY", candle_key="D1",
    )

    subject._record_failed_buy_execution(
        "PAPER", intent, BrokerOrderResult(False, "REJECTED"),
    )

    assert subject.rule_state.active_telegram_signal("FPT") is None
    row = subject.signal_log.read_all()[-1]
    assert (row["acted"], row["blocked_by"], row["candle_key"]) == (
        "WAIT", "BROKER_REJECTED", "D1",
    )


@pytest.mark.parametrize("broker_status", ["REJECTED", "FAILED", "EXPIRED"])
def test_failed_buy_reports_system_error_not_a_new_technical_signal(monkeypatch, tmp_path, broker_status):
    subject, tele = _technical_subject(monkeypatch, tmp_path)
    subject.signal_log = SignalLog(tmp_path / "signal_log.csv")
    subject.snapshots = {"REAL": ({}, [], [])}
    subject.queue = SimpleNamespace(list_all=lambda: [])
    subject._shared_tick = lambda _symbol: {"price": 22.5}
    subject._symbol_exchange = lambda _symbol: "HOSE"
    intent = OrderIntent.create(
        "HDB", "BUY", 100, "MARKET", execution_mode="REAL",
        source="BOT", trade_id="T1", signal="BUY", candle_key="D1",
    )
    for _ in range(2):
        subject._record_failed_buy_execution("REAL", intent, BrokerOrderResult(False, broker_status))
    assert len(tele.sent) == 1
    assert "HỆ THỐNG CẦN KIỂM TRA · REAL" in tele.sent[0][1]
    assert f"REAL BUY HDB: {broker_status}" in tele.sent[0][1]
    assert "TÍN HIỆU BUY" not in tele.sent[0][1]
    assert len(subject.signal_log.read_all()) == 1


def test_telegram_does_not_report_a_partial_exit_as_closed():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    cycle = TradeCycle(id="T1", symbol="FPT", execution_mode="PAPER")
    cycle.record_buy_fill(200, 69.2)
    cycle.record_sell_fill(100, 70.0)

    assert service.notify_closed(cycle=cycle, reason="NORMAL_PROTECTION") is False
    assert tele.sent == []


def test_raw_sell_signal_is_silent_when_alerted_buy_never_became_a_position(tmp_path):
    tele = Telegram()
    state = RuleStateStore(tmp_path / "rule_state.json")
    state.open_telegram_signal("FPT", "2026-08-14", price=69.2, market_state="UPTREND")

    class NoTrades:
        @staticmethod
        def get(_trade_id):
            return None

    class Subject:
        telegram = SignalTelegramService(tele, chat_id="7")
        rule_state = state
        trade_state = NoTrades()

    decision = StrategyDecision("WAIT", "FPT", "SELL_SIGNAL", signal="SELL")
    assert DashboardActionsMixin._notify_rule_signal(Subject(), "FPT", decision, {"price": 74}) is None
    assert tele.sent == []
    assert state.active_telegram_signal("FPT") is None


def test_dashboard_sends_each_indicator_exit_alert_once(monkeypatch, tmp_path):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    class Service:
        @staticmethod
        def notify_indicator_exit_alert(**kwargs):
            calls.append(kwargs)
            return True

    class Subject:
        telegram = Service()
        rule_state = RuleStateStore(tmp_path / "rule_state.json")
        settings = SimpleNamespace(
            telegram_notifications={"indicator_exit": True},
            telegram_cooldown_minutes={"indicator_exit": 0},
        )

    monkeypatch.setattr(
        "viking_v2.dashboard.actions.threading.Thread", ImmediateThread,
    )
    decision = StrategyDecision(
        "WAIT", "FPT", "INDICATOR_EXIT_ALERT",
        event="INDICATOR_EXIT_ALERT", signal="SELL",
        details={
            "candle_key": "2026-09-21|TICK|7",
            "indicators": {
                "sell_ema_fast_period": 3, "sell_ema_slow_period": 6,
                "sell_ema_fast": 99, "sell_ema_slow": 100,
                "rsi_period": 14, "rsi": 47, "rsi_previous": 51,
            },
        },
    )
    subject = Subject()
    DashboardActionsMixin._notify_rule_signal(
        subject, "FPT", decision, {"price": 99}, execution_mode="PAPER",
    )
    DashboardActionsMixin._notify_rule_signal(
        subject, "FPT", decision, {"price": 99}, execution_mode="PAPER",
    )
    assert len(calls) == 1
    assert calls[0]["symbol"] == "FPT"


def test_websocket_decodes_msgpack_and_merges_tick_quote():
    ws = DNSEMarketWS("key", "secret")
    ws._on_message(None, msgpack.packb({"symbol": "FPT", "matchPrice": 100, "referencePrice": 99}, use_bin_type=True))
    ws._on_message(None, msgpack.packb({"symbol": "FPT", "bid": [{"price": 99.5}], "offer": [{"price": 100.5}]}, use_bin_type=True))
    tick = ws.latest_tick("FPT")
    assert tick["price"] == 100
    assert tick["bid"] == 99.5
    assert tick["ask"] == 100.5
    assert tick["source"] == "WS"


def test_ato_atc_zero_expected_price_does_not_erase_last_valid_price():
    ws = DNSEMarketWS("key", "secret")
    ws._ingest({"symbol": "FPT", "matchPrice": 100})
    ws._ingest({"symbol": "FPT", "matchPrice": 0, "expectedPrice": 0})
    assert ws.latest_tick("FPT")["price"] == 100
    ws._ingest({"symbol": "FPT", "expectedPrice": 101.5})
    assert ws.latest_tick("FPT")["price"] == 101.5


def test_websocket_auth_and_tls_path_are_legacy_compatible(monkeypatch):
    ws = DNSEMarketWS("key", "secret")
    auth = ws._auth_payload()
    assert auth["action"] == "auth"
    assert auth["api_key"] == "key"
    assert len(auth["signature"]) == 64
    assert ws.url.startswith("wss://")
