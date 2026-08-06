# -*- coding: utf-8 -*-
from telegram_notify import opportunity_alerts, settings
from telegram_notify.client import TelegramClient


class _FakeTimer:
    def __init__(self, delay, callback, kwargs=None):
        self.delay = delay
        self.callback = callback
        self.kwargs = kwargs or {}
        self.daemon = False

    def start(self):
        return None

    def cancel(self):
        return None


def _configure(monkeypatch, tmp_path, **overrides):
    monkeypatch.setattr(settings, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(opportunity_alerts, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(opportunity_alerts, "_market_session_open", lambda _symbol: True)
    monkeypatch.setattr(opportunity_alerts.threading, "Timer", _FakeTimer)
    # Các bài test này kiểm tra gom/chống lặp Telegram, không kiểm tra rule
    # thanh khoản (rule đó có bộ test riêng).
    monkeypatch.setattr(
        "ai_advisor.scan_cache.liquidity_filter_allows", lambda _symbol: True
    )
    data = {
        "opportunity_alerts_enabled": True,
        "opportunity_chat_id": "1001234567890",
        "opportunity_mode_filter": "ALL",
        "opportunity_ckps_enabled": True,
        "opportunity_ckcs_enabled": True,
        "opportunity_duplicate_cooldown_minutes": 0,
        "opportunity_batch_minutes": 0.5,
    }
    data.update(overrides)
    settings.save_settings(data)
    opportunity_alerts.reset_runtime_state()


def _item(symbol, side="BUY", mode="PAPER", market="CKCS"):
    sl = 11.5 if side == "BUY" else 14.0
    tp = 14.0 if side == "BUY" else 10.0
    return {
        "symbol": symbol,
        "side": side,
        "execution_mode": mode,
        "market_type": market,
        "last_price": 12.3,
        "block_reason": "BOT_OFF",
        "order_setup": {"price": 12.3, "lot": 100, "sl": sl, "tp": tp},
    }


def test_many_symbols_are_sent_as_one_read_only_digest(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    sent = []

    def fake_send(self, chat_id, text, chunk_size=3500, title=""):
        sent.append((chat_id, text, title))
        return {"ok": True, "sent": 1}

    monkeypatch.setattr(TelegramClient, "send_long_message", fake_send)

    for index in range(40):
        result = opportunity_alerts.queue_opportunity(_item(f"CK{index:02d}"))
        assert result["queued"] is True

    result = opportunity_alerts.flush_now()

    assert result["ok"] is True
    assert len(sent) == 1
    assert sent[0][0] == "1001234567890"
    assert "🟢 CKCS BUY" in sent[0][1]
    assert "CK00 | Giá 12.3 | Entry NOW @12.3 (100 CP)" in sent[0][1]
    assert "BOT_OFF" not in sent[0][1]
    assert "PAPER" not in sent[0][1]
    assert sent[0][2] == ""


def test_opportunity_sender_uses_same_ssl_mode_as_report_sender(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    created = []

    class FakeClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def send_long_message(self, chat_id, text, chunk_size=3500, title=""):
            return {"ok": True, "sent": 1, "chat_id": chat_id}

    monkeypatch.setattr(opportunity_alerts, "TelegramClient", FakeClient)
    opportunity_alerts.queue_opportunity(_item("VN30F1M", market="CKPS"))

    result = opportunity_alerts.flush_now()

    assert result["ok"] is True
    assert created[0]["allow_insecure_ssl"] is True


def test_unchanged_signal_is_not_resent_but_reversal_is(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    first = opportunity_alerts.queue_opportunity(_item("AAA", "BUY"))
    duplicate = opportunity_alerts.queue_opportunity(_item("AAA", "BUY"))
    opposite = opportunity_alerts.queue_opportunity(_item("AAA", "SELL"))

    assert first["queued"] is True
    assert duplicate["reason"] == "unchanged"
    assert opposite["queued"] is True


def test_shortlist_is_one_overwritten_market_markdown(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    opportunity_alerts.queue_opportunity(_item("VN30F1M", "SELL", market="CKPS"))
    opportunity_alerts.queue_opportunity(_item("AAA", "BUY"))
    path = opportunity_alerts.shortlist_path()
    first = open(path, "r", encoding="utf-8").read()

    assert "## CKPS" in first
    assert "VN30F1M SHORT | Giá 12.3 | Entry NOW @12.3 (100 HĐ)" in first
    assert "## CKCS BUY" in first
    assert "AAA | Giá 12.3 | Entry NOW @12.3 (100 CP)" in first

    opportunity_alerts.mark_wait("AAA")
    second = open(path, "r", encoding="utf-8").read()

    assert "AAA | BUY" not in second
    assert "VN30F1M SHORT | Giá 12.3 | Entry NOW @12.3 (100 HĐ)" in second


def test_shortlist_still_updates_when_telegram_suggestions_are_disabled(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path, opportunity_alerts_enabled=False)

    result = opportunity_alerts.queue_opportunity(_item("AAA", "SELL"))
    content = open(
        opportunity_alerts.shortlist_path(), "r", encoding="utf-8"
    ).read()

    assert result["stored"] is True
    assert result["reason"] == "telegram_disabled"
    assert "## CKCS SELL" in content
    assert "AAA | Giá 12.3 | Entry NOW @12.3 (100 CP)" in content


def test_paper_real_do_not_filter_read_only_feed_but_market_toggles_do(monkeypatch, tmp_path):
    _configure(
        monkeypatch,
        tmp_path,
        opportunity_mode_filter="PAPER",
        opportunity_ckps_enabled=True,
        opportunity_ckcs_enabled=False,
    )

    assert opportunity_alerts.queue_opportunity(
        _item("VN30F1M", mode="PAPER", market="CKPS")
    )["queued"] is True
    assert opportunity_alerts.queue_opportunity(
        _item("AAA", mode="PAPER", market="CKCS")
    )["reason"] == "filtered"
    assert opportunity_alerts.queue_opportunity(
        _item("VN30F1M", mode="REAL", market="CKPS")
    )["reason"] == "unchanged"
