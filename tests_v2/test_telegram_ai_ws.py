from __future__ import annotations

import json

import msgpack

from viking_v2.connections.dnse.websocket import DNSEMarketWS
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import StrategyDecision, TradeCycle
from viking_v2.rules.state import RuleStateStore


class Telegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))


def test_telegram_sends_one_buy_and_only_its_matching_closed_summary():
    tele = Telegram()
    service = SignalTelegramService(tele, chat_id="7")
    assert service.notify_buy(
        symbol="FPT", signal_id="ABCDEF1234", price=69.2, market_state="UPTREND"
    )
    cycle = TradeCycle(
        id="ABCDEF1234", symbol="FPT", execution_mode="REAL",
        em_modes=["NORMAL", "HIGH", "IND_EXIT"],
    )
    cycle.record_buy_fill(100, 69.2, 3_000)
    cycle.mark_exit_once("NORMAL_PROTECTION")
    cycle.mark_exit_once("INDICATOR_EXIT")
    cycle.record_sell_fill(100, 74.0, 4_000)
    assert service.notify_closed(cycle=cycle, reason="INDICATOR_EXIT")

    assert len(tele.sent) == 2
    assert "BUY · FPT" in tele.sent[0][1]
    assert "ABCDEF1234" in tele.sent[0][1]
    assert "CLOSED · FPT · REAL" in tele.sent[1][1]
    assert "ABCDEF1234" in tele.sent[1][1]
    assert "Lãi/lỗ ròng: +473,000đ (+6.84%)" in tele.sent[1][1]
    assert "EM bật: NORMAL · HIGH · EXIT SELL" in tele.sent[1][1]
    assert "EM kích hoạt: 2 lần · NORMAL ×1 · EXIT SELL ×1" in tele.sent[1][1]
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
    assert "BUY SIGNAL · 2 MÃ" in tele.sent[0][1]
    assert "FPT · 69,200đ · UPTREND · FPT1" in tele.sent[0][1]
    assert "HPG · 27,500đ · UPTREND · HPG1" in tele.sent[0][1]


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
