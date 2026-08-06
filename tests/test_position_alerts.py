# -*- coding: utf-8 -*-
from types import SimpleNamespace

from telegram_notify import position_alerts
from telegram_notify.client import TelegramClient


def _configure(monkeypatch, tmp_path):
    monkeypatch.setattr(position_alerts, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        position_alerts,
        "load_settings",
        lambda: {
            "position_reversal_alerts_enabled": True,
            "position_level_alerts_enabled": True,
            "position_alert_distance_r": 0.2,
            "position_alert_cooldown_minutes": 15.0,
            "opportunity_chat_id": "-1002",
            "bot_token_env": "TELE_BOT_KEY",
            "chunk_size": 3500,
        },
    )


def _position(*, current=99.2):
    return SimpleNamespace(
        ticket="PAPER-1",
        symbol="VN30F1M",
        type=0,
        price_open=100.0,
        price_current=current,
        sl=99.0,
        tp=102.0,
    )


def test_near_level_alert_is_available_but_deduplicated(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(
        TelegramClient,
        "send_long_message",
        lambda self, chat_id, text, chunk_size=3500, title="": (
            sent.append(text) or {"ok": True}
        ),
    )
    pos = _position(current=99.1)

    position_alerts.observe_position(
        pos,
        99.1,
        initial_r_dist=1.0,
        now=1000,
    )
    position_alerts.observe_position(
        pos,
        99.05,
        initial_r_dist=1.0,
        now=1010,
    )

    assert len(sent) == 1
    assert "GẦN SL" in sent[0]


def test_reversal_alert_only_targets_opposite_open_position(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(
        TelegramClient,
        "send_long_message",
        lambda self, chat_id, text, chunk_size=3500, title="": (
            sent.append(text) or {"ok": True}
        ),
    )
    pos = _position()

    position_alerts.observe_reversal([pos], "VN30F1M", "BUY")
    position_alerts.observe_reversal(
        [pos],
        "VN30F1M",
        "SELL",
        current_price=99.5,
    )
    position_alerts.observe_reversal(
        [pos],
        "VN30F1M",
        "SELL",
        current_price=99.4,
    )

    assert len(sent) == 1
    assert "ĐẢO CHIỀU" in sent[0]
    assert "Tín hiệu mới: SELL" in sent[0]

    position_alerts.observe_reversal([pos], "VN30F1M", "NONE")
    position_alerts.observe_reversal(
        [pos],
        "VN30F1M",
        "SELL",
        current_price=99.3,
    )
    assert len(sent) == 2
