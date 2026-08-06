# -*- coding: utf-8 -*-
from core import margin_rules


def test_margin_risk_base_equity_vs_free_cash():
    account = {
        "equity": 10.0,
        "balance": 10.0,
        "cash_available": 9.0,
        "free_margin": 9.5,
    }

    base, label, warning = margin_rules.resolve_risk_base(
        account,
        {"MARGIN_RISK_BASE": "EQUITY_NAV"},
    )
    assert base == 10.0
    assert label == "EQUITY_NAV"
    assert warning == ""

    base, label, warning = margin_rules.resolve_risk_base(
        account,
        {"MARGIN_RISK_BASE": "FREE_CASH"},
    )
    assert base == 9.0
    assert label == "FREE_CASH"
    assert warning == ""


def test_margin_guard_blocks_unknown_or_low_rtt():
    settings = {
        "ENABLE_MANUAL_MARGIN": True,
        "MIN_RTT_TO_OPEN": 100.0,
        "MAX_MARGIN_ORDER_VALUE_PCT": 100.0,
        "MAX_MANUAL_MARGIN_LOSS_PCT": 10.0,
    }

    missing = margin_rules.manual_margin_check(
        {"equity": 100_000_000.0},
        settings,
        order_value=10_000_000.0,
        risk_usd=1_000_000.0,
        strict=True,
    )
    assert missing["passed"] is False
    assert missing["checks"][0]["msg"] == "UNKNOWN"

    low = margin_rules.manual_margin_check(
        {"equity": 100_000_000.0, "rtt": 90.0},
        settings,
        order_value=10_000_000.0,
        risk_usd=1_000_000.0,
        strict=True,
    )
    assert low["passed"] is False
    assert "90.0%" in low["checks"][0]["msg"]


def test_bot_margin_is_hard_blocked_when_debt_exists():
    reason = margin_rules.bot_margin_block_reason(
        {"equity": 100_000_000.0, "margin_debt": 1_000_000.0, "rtt": 120.0},
        {"BOT_ALLOW_MARGIN": False},
    )
    assert "BOT_MARGIN_DISABLED" in reason
