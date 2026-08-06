# -*- coding: utf-8 -*-
import json
import logging
import urllib.parse

from telegram_notify import settings
from telegram_notify.client import TelegramClient, _chat_id_candidates, _chunk_text
import telegram_notify.client as telegram_client
from telegram_notify import reporter


def test_telegram_settings_defaults_and_save_load(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "account_dir", lambda: str(tmp_path))

    loaded = settings.load_settings()
    assert loaded["bot_token_env"] == "TELE_BOT_KEY"
    assert loaded["report_chat_id"] == "1003772881044"
    assert loaded["control_chat_id"] == "1003941549878"
    assert loaded["control_enabled"] is False
    assert loaded["signal_proposals_enabled"] is False
    assert loaded["signal_proposal_cooldown_minutes"] == 15.0
    assert loaded["opportunity_alerts_enabled"] is False
    assert loaded["position_reversal_alerts_enabled"] is True
    assert loaded["position_level_alerts_enabled"] is False
    assert loaded["opportunity_daily_digest_enabled"] is False
    assert loaded["opportunity_chat_id"] == ""
    assert loaded["opportunity_duplicate_cooldown_minutes"] == 0.0
    assert loaded["opportunity_batch_minutes"] == 5.0
    assert loaded["system_alerts_enabled"] is True
    assert loaded["opportunity_mode_filter"] == "ALL"
    assert loaded["owner_user_id"] == ""

    saved = settings.save_settings(
        {
            "enabled": True,
            "control_enabled": True,
            "signal_proposals_enabled": True,
            "opportunity_alerts_enabled": True,
            "position_alerts_enabled": False,
            "opportunity_daily_digest_enabled": True,
            "bot_token_env": "TELE_BOT_KEY",
            "report_chat_id": "123",
            "opportunity_chat_id": "789",
            "control_chat_id": "456",
            "owner_user_id": "111",
            "operator_user_ids": "222,333",
            "control_poll_interval_seconds": "0",
            "signal_proposal_cooldown_minutes": "15",
            "opportunity_duplicate_cooldown_minutes": "90",
            "opportunity_batch_minutes": "3",
            "opportunity_mode_filter": "all",
            "opportunity_ckps_enabled": False,
            "opportunity_ckcs_enabled": True,
            "chunk_size": "999999",
        }
    )
    assert saved["enabled"] is True
    assert saved["control_enabled"] is True
    assert saved["signal_proposals_enabled"] is True
    assert saved["opportunity_alerts_enabled"] is True
    assert saved["position_reversal_alerts_enabled"] is False
    assert saved["position_level_alerts_enabled"] is False
    assert saved["opportunity_daily_digest_enabled"] is True
    assert saved["opportunity_chat_id"] == "789"
    assert saved["opportunity_duplicate_cooldown_minutes"] == 90.0
    assert saved["opportunity_batch_minutes"] == 3.0
    assert saved["opportunity_mode_filter"] == "ALL"
    assert saved["opportunity_ckps_enabled"] is False
    assert saved["opportunity_ckcs_enabled"] is True
    assert saved["signal_proposal_cooldown_minutes"] == 15.0
    assert saved["chunk_size"] == 3900
    assert saved["control_poll_interval_seconds"] == 0.5
    assert settings.allowed_user_ids(saved) == {111, 222, 333}
    assert settings.load_settings()["report_chat_id"] == "123"


def test_legacy_opportunity_timers_are_migrated():
    migrated = settings.normalize_settings(
        {
            "opportunity_duplicate_cooldown_minutes": 60,
            "opportunity_batch_minutes": 5,
        }
    )
    invalid = settings.normalize_settings(
        {
            "opportunity_duplicate_cooldown_minutes": "khong-hop-le",
            "opportunity_batch_minutes": "khong-hop-le",
        }
    )

    assert migrated["opportunity_duplicate_cooldown_minutes"] == 0.0
    assert migrated["opportunity_batch_minutes"] == 5.0
    assert invalid["opportunity_duplicate_cooldown_minutes"] == 0.0
    assert invalid["opportunity_batch_minutes"] == 5.0


def test_chat_id_candidates_accepts_channel_id_without_minus():
    assert _chat_id_candidates("1003772881044") == ["1003772881044", "-1003772881044"]
    assert _chat_id_candidates("-1003772881044") == ["-1003772881044"]
    assert _chat_id_candidates("3772881044") == ["3772881044", "-1003772881044"]


def test_chunk_text_respects_safe_telegram_size():
    chunks = _chunk_text("x" * 8200, chunk_size=3500)
    assert len(chunks) == 3
    assert "".join(chunks) == "x" * 8200
    assert max(len(chunk) for chunk in chunks) <= 3500


def test_send_message_falls_back_to_negative_channel_id(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        body = urllib.parse.parse_qs(req.data.decode("utf-8"))
        chat_id = body["chat_id"][0]
        calls.append(chat_id)
        if chat_id.startswith("-"):
            return FakeResponse({"ok": True, "result": {"message_id": 1}})
        return FakeResponse({"ok": False, "description": "chat not found"})

    monkeypatch.setenv("TELE_BOT_KEY", "token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = TelegramClient(token_env="TELE_BOT_KEY").send_message("1003772881044", "hello")
    assert result["ok"] is True
    assert calls == ["1003772881044", "-1003772881044"]


def test_send_long_message_requires_token(monkeypatch):
    monkeypatch.delenv("TELE_BOT_KEY", raising=False)

    result = TelegramClient(token_env="TELE_BOT_KEY").send_long_message("1003772881044", "hello")
    assert result["ok"] is False
    assert "TELE_BOT_KEY" in result["error"]


def test_failed_send_is_logged_once_without_queue(monkeypatch, caplog):
    monkeypatch.setenv("TELE_BOT_KEY", "secret-token")
    monkeypatch.setattr(
        TelegramClient,
        "_request",
        lambda self, method, payload: {"ok": False, "error": "network down"},
    )
    telegram_client._failure_log_times.clear()
    caplog.set_level(logging.WARNING, logger="RAT_CKVN")

    client = TelegramClient(token_env="TELE_BOT_KEY")
    first = client.send_message("123", "hello")
    second = client.send_message("123", "hello again")

    assert first["ok"] is False and second["ok"] is False
    messages = [record.getMessage() for record in caplog.records if "[TELEGRAM]" in record.getMessage()]
    assert messages == ["[TELEGRAM] sendMessage failed: network down"]
    assert "secret-token" not in messages[0]


def test_send_long_message_sends_all_chunks(monkeypatch):
    sent = []

    def fake_send(self, chat_id, text, parse_mode=None):
        sent.append(text)
        return {"ok": True, "chat_id": chat_id}

    monkeypatch.setenv("TELE_BOT_KEY", "token")
    monkeypatch.setattr(TelegramClient, "send_message", fake_send)
    monkeypatch.setattr("telegram_notify.client.time.sleep", lambda _seconds: None)

    result = TelegramClient(token_env="TELE_BOT_KEY").send_long_message(
        "1003772881044",
        "x" * 7000,
        chunk_size=3500,
        title="Report",
    )

    assert result == {"ok": True, "sent": 2}
    assert sent[0].startswith("Report (1/2)")
    assert sent[1].startswith("Report (2/2)")


def test_send_long_message_can_use_text_heading_without_duplicate_title(monkeypatch):
    sent = []
    monkeypatch.setenv("TELE_BOT_KEY", "token")
    monkeypatch.setattr(
        TelegramClient,
        "send_message",
        lambda self, chat_id, text, parse_mode=None: (
            sent.append(text) or {"ok": True, "chat_id": chat_id}
        ),
    )

    result = TelegramClient(token_env="TELE_BOT_KEY").send_long_message(
        "123",
        "RAT6 GỢI Ý BOT — 09:30\n\nCKPS",
        title="",
    )

    assert result["ok"]
    assert sent == ["RAT6 GỢI Ý BOT — 09:30\n\nCKPS"]


def test_report_diagnostics_does_not_expose_token(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "account_dir", lambda: str(tmp_path))
    settings.save_settings(
        {
            "enabled": True,
            "bot_token_env": "TELE_BOT_KEY",
            "report_chat_id": "1003772881044",
        }
    )
    monkeypatch.setenv("TELE_BOT_KEY", "123456:secret-token")

    diag = reporter.report_diagnostics()

    assert diag["token_present"] is True
    assert diag["token_length"] == len("123456:secret-token")
    assert "secret-token" not in str(diag)


def test_manual_text_report_can_bypass_enabled_checkbox(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "account_dir", lambda: str(tmp_path))
    settings.save_settings(
        {
            "enabled": False,
            "bot_token_env": "TELE_BOT_KEY",
            "report_chat_id": "1003772881044",
            "control_chat_id": "1003941549878",
            "chunk_size": 3500,
        }
    )

    sent = {}

    def fake_send(self, chat_id, text, chunk_size=3500, title=""):
        sent.update({"chat_id": chat_id, "text": text, "chunk_size": chunk_size, "title": title})
        return {"ok": True, "sent": 1}

    monkeypatch.setenv("TELE_BOT_KEY", "token")
    monkeypatch.setattr(TelegramClient, "send_long_message", fake_send)

    auto_result = reporter.send_text_report("auto", require_enabled=True)
    manual_result = reporter.send_text_report("manual", require_enabled=False)

    assert auto_result["skipped"] is True
    assert manual_result["ok"] is True
    assert sent["text"] == "manual"
    assert sent["chat_id"] == "1003772881044"
