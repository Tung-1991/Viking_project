from __future__ import annotations

import logging

import pytest

from viking_v2.services.daemon import QuoteHealthMonitor
from viking_v2.trading.validation import MAX_QUOTE_AGE, quote_diagnostics, quote_is_fresh


@pytest.mark.parametrize("updates, reason", [
    ({"stale": True}, "MARKED_STALE"),
    ({"frozen": True}, "FROZEN"),
    ({"symbol": "CTS"}, "SYMBOL_MISMATCH"),
    ({"health": "REST_UNAVAILABLE"}, "SOURCE_ERROR"),
    ({"timestamp": None}, "MISSING_TIMESTAMP"),
    ({"timestamp": "bad"}, "INVALID_TIMESTAMP"),
    ({"timestamp": float("nan")}, "INVALID_TIMESTAMP"),
    ({"timestamp": float("inf")}, "INVALID_TIMESTAMP"),
    ({"timestamp": 1000 - MAX_QUOTE_AGE - 1}, "QUOTE_TOO_OLD"),
    ({"timestamp": 1003}, "CLOCK_SKEW"),
])
def test_quote_diagnostics_explains_the_existing_guard(updates, reason):
    quote = {"symbol": "MSN", "timestamp": 1000, "price": 74.4, "source": "WS", **updates}
    before = dict(quote)
    details = quote_diagnostics(quote, "MSN", now=1000)
    assert not details["valid"]
    assert details["valid"] == quote_is_fresh(quote, "MSN", now=1000)
    assert details["reason"] == reason
    assert details["reason_text"]
    assert quote == before


def test_quote_diagnostics_keeps_actual_rejection_reason_after_display_is_marked_stale():
    details = quote_diagnostics({
        "symbol": "MSN", "timestamp": 1000, "price": 74.4,
        "stale": True, "quote_issue": "NO_QUOTE", "source": "WS",
    }, "MSN", now=1001)
    assert details["reason"] == "NO_QUOTE"
    assert details["age_seconds"] == 1


def test_diagnostics_uses_receipt_time_and_does_not_expose_raw_error_or_source():
    details = quote_diagnostics({
        "symbol": "MSN", "timestamp": 1, "received_at": 1000,
        "price": 74.4, "source": "source with a SECRET", "health": "failure SECRET",
    }, "MSN", now=1001)
    assert details["age_seconds"] == 1
    assert details["source"] == "UNKNOWN"
    assert details["reason"] == "SOURCE_ERROR"
    assert "SECRET" not in str(details)


def test_quote_monitor_logs_only_loss_and_recovery_not_every_tick(caplog):
    logger = logging.getLogger("offline-quote-monitor")
    caplog.set_level(logging.INFO, logger=logger.name)
    monitor = QuoteHealthMonitor(logger)
    fresh = {"symbol": "MSN", "price": 74.4, "timestamp": 1000, "source": "WS"}
    monitor.observe("MSN", fresh, now=1000)
    for _ in range(200):
        monitor.observe("MSN", {**fresh, "timestamp": 1}, now=1000)
    monitor.observe("MSN", None, now=1000)  # Still the same failure episode.
    for _ in range(200):
        monitor.observe("MSN", fresh, now=1001)
    records = [record for record in caplog.records if record.name == logger.name]
    assert len(records) == 2
    assert records[0].levelno == logging.WARNING
    assert "BỊ LOẠI" in records[0].message and "Giá quá thời gian" in records[0].message
    assert records[1].levelno == logging.INFO
    assert "PHỤC HỒI" in records[1].message
    assert all("MSN" in record.message and "tuổi" in record.message for record in records)


def test_new_failure_after_recovery_is_logged_again_and_symbols_are_independent(caplog):
    logger = logging.getLogger("offline-quote-episodes")
    caplog.set_level(logging.INFO, logger=logger.name)
    monitor = QuoteHealthMonitor(logger)
    monitor.observe("MSN", None, now=1000)
    monitor.observe("CTS", None, now=1000)
    monitor.observe("MSN", {"symbol": "MSN", "timestamp": 1000}, now=1000)
    monitor.observe("MSN", None, now=1001)
    records = [record for record in caplog.records if record.name == logger.name]
    assert [record.levelno for record in records] == [logging.WARNING, logging.WARNING, logging.INFO, logging.WARNING]


def test_quote_monitor_does_not_report_session_open_as_network_recovery(caplog):
    logger = logging.getLogger("offline-quote-session")
    caplog.set_level(logging.INFO, logger=logger.name)
    monitor = QuoteHealthMonitor(logger)
    monitor.observe("MSN", None, now=1000)
    monitor.pause("MSN")
    monitor.observe("MSN", {"symbol": "MSN", "timestamp": 1000}, now=1000)
    assert len([record for record in caplog.records if record.name == logger.name]) == 1
