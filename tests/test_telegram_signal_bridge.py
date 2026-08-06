# -*- coding: utf-8 -*-
from telegram_notify import proposals, settings, signal_bridge
from telegram_notify.client import TelegramClient


class FakeTradeManager:
    def __init__(self):
        self.calls = []

    def build_telegram_signal_order(self, symbol, side, context=None, market_mode="ANY"):
        self.calls.append((symbol, side, context or {}, market_mode))
        return {
            "ok": True,
            "symbol": symbol,
            "side": side,
            "lot": 0.03,
            "sl": 1980.0,
            "tp": 2050.0,
            "price": 2000.0,
            "market_mode": market_mode,
        }


def _save_enabled(tmp_path, monkeypatch, enabled=True, cooldown=15):
    monkeypatch.setattr(settings, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(signal_bridge, "account_dir", lambda: str(tmp_path))
    settings.save_settings(
        {
            "control_enabled": True,
            "signal_proposals_enabled": enabled,
            "signal_proposal_cooldown_minutes": cooldown,
            "bot_token_env": "TELE_BOT_KEY",
            "control_chat_id": "1003941549878",
        }
    )


def test_signal_bridge_disabled_skips(monkeypatch, tmp_path):
    _save_enabled(tmp_path, monkeypatch, enabled=False)
    tm = FakeTradeManager()

    result = signal_bridge.maybe_send_signal_proposal(
        tm,
        {"signal_id": "S1", "symbol": "ETHUSD", "action": "BUY", "signal_class": "ENTRY"},
    )

    assert result["skipped"] is True
    assert result["reason"] == "disabled"
    assert tm.calls == []


def test_signal_bridge_creates_pending_and_sends_keyboard(monkeypatch, tmp_path):
    _save_enabled(tmp_path, monkeypatch, enabled=True)
    sent = {}

    def fake_send(self, chat_id, text, keyboard=None):
        sent.update({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return {"ok": True}

    monkeypatch.setenv("TELE_BOT_KEY", "token")
    monkeypatch.setattr(TelegramClient, "send_message_with_keyboard", fake_send)
    tm = FakeTradeManager()

    result = signal_bridge.maybe_send_signal_proposal(
        tm,
        {
            "signal_id": "S1",
            "symbol": "ETHUSD",
            "action": "BUY",
            "signal_class": "ENTRY",
            "market_mode": "TREND",
            "context": {"atr_G2": 10},
        },
    )

    assert result["ok"] is True
    pending = proposals.pending_proposals()
    assert len(pending) == 1
    assert pending[0]["source"] == "SIGNAL"
    assert pending[0]["created_by_label"] == "SIGNAL"
    assert pending[0]["metadata"]["signal_id"] == "S1"
    assert "SIGNAL" in sent["text"]
    assert "ETHUSD BUY" in sent["text"]
    assert sent["keyboard"]


def test_signal_bridge_cooldown_is_symbol_side_specific(monkeypatch, tmp_path):
    _save_enabled(tmp_path, monkeypatch, enabled=True, cooldown=15)

    monkeypatch.setenv("TELE_BOT_KEY", "token")
    monkeypatch.setattr(TelegramClient, "send_message_with_keyboard", lambda self, chat_id, text, keyboard=None: {"ok": True})
    tm = FakeTradeManager()
    buy_signal = {"signal_id": "S1", "symbol": "ETHUSD", "action": "BUY", "signal_class": "ENTRY"}
    sell_signal = {"signal_id": "S2", "symbol": "ETHUSD", "action": "SELL", "signal_class": "ENTRY"}

    first = signal_bridge.maybe_send_signal_proposal(tm, buy_signal)
    second = signal_bridge.maybe_send_signal_proposal(tm, buy_signal)
    third = signal_bridge.maybe_send_signal_proposal(tm, sell_signal)

    assert first["ok"] is True
    assert second["skipped"] is True
    assert second["reason"] == "cooldown"
    assert third["ok"] is True
    assert len(proposals.pending_proposals()) == 2
