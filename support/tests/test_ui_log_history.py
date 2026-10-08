"""Persistent UI log tails are display-only and isolated by account/tab."""
import json
import logging
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from viking_v2.dashboard.tables import DashboardTablesMixin
from viking_v2.services.runtime import JsonLineFormatter, recent_ui_logs, setup_logging


def event(index, target="manual", **overrides):
    return {"ts": f"2026-10-08T12:00:{index % 60:02d}", "ui_target": target,
            "message": f"event-{index}", **overrides}


def test_ui_log_formatter_preserves_tab_without_adding_it_to_daemon_records():
    record = logging.LogRecord("ui", logging.INFO, __file__, 1, "message", (), None)
    assert "ui_target" not in json.loads(JsonLineFormatter().format(record))
    record.ui_target = "bot"
    assert json.loads(JsonLineFormatter().format(record))["ui_target"] == "bot"


def test_log_tail_skips_old_unclassified_and_corrupt_records_and_keeps_order(tmp_path):
    rotated = tmp_path / "ui.jsonl.2026-10-07"
    rotated.write_text(json.dumps(event(1, "bot")) + "\n", encoding="utf-8")
    rows = [event(2), event(3, "bot"), event(4, ts="bad-date"),
            event(5, "bad-target"), event(6, message=None), {"message": "old-format"}]
    (tmp_path / "ui.jsonl").write_bytes(
        b"not-json\n\xff\n[]\n" + "\n".join(json.dumps(row) for row in rows).encode("utf-8")
    )
    result = recent_ui_logs(tmp_path)
    assert [row["message"] for row in result] == ["event-1", "event-2", "event-3"]
    assert [row["target"] for row in result] == ["bot", "manual", "bot"]
    assert [row["message"] for row in recent_ui_logs(tmp_path, limit=1)] == ["event-3"]
    assert not recent_ui_logs(tmp_path, limit=0)


def test_log_tail_is_read_bounded_and_account_local(tmp_path):
    # Oversized earlier data is deliberately not loaded into Tk on startup.
    first = json.dumps(event(0, message="x" * 1_100_000))
    (tmp_path / "ui.jsonl").write_text(first + "\n" + json.dumps(event(1)), encoding="utf-8")
    assert [row["message"] for row in recent_ui_logs(tmp_path)] == ["event-1"]
    assert not recent_ui_logs(tmp_path / "another-account")


def test_persisted_logs_return_after_logger_reopen_without_rewriting_file(tmp_path, ui_root):
    logger = setup_logging(tmp_path, "ui")
    try:
        logger.info("REAL BUY MSN · CHƯA GỬI: thiếu tiền", extra={"ui_target": "manual"})
        logger.info("PAPER SELL MSN · KHỚP HẾT · E AUTO", extra={"ui_target": "bot"})
        for handler in logger.handlers:
            handler.flush()
        before = (tmp_path / "ui.jsonl").read_bytes()
        subject = DashboardTablesMixin()
        subject.bridge = SimpleNamespace(log_dir=tmp_path)
        subject.logger = SimpleNamespace(info=lambda *_args, **_kwargs: pytest.fail("history was logged again"))
        subject._set_log_unread = lambda *_args: pytest.fail("history marked unread")
        subject.log_manual, subject.log_bot = ctk.CTkTextbox(ui_root), ctk.CTkTextbox(ui_root)
        try:
            subject._restore_visible_logs()
            manual, bot = subject.log_manual.get("1.0", "end-1c"), subject.log_bot.get("1.0", "end-1c")
            assert "REAL BUY MSN" in manual and "PAPER SELL MSN" not in manual
            assert "PAPER SELL MSN" in bot and "REAL BUY MSN" not in bot
            subject._restore_visible_logs()
            assert manual == subject.log_manual.get("1.0", "end-1c")
            assert bot == subject.log_bot.get("1.0", "end-1c")
            assert before == (tmp_path / "ui.jsonl").read_bytes()
        finally:
            subject.log_manual.destroy()
            subject.log_bot.destroy()
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
