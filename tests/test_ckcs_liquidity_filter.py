from datetime import datetime, timedelta

import pandas as pd

from ai_advisor import scan_cache, schedule_settings
from telegram_notify import opportunity_alerts


def _daily_bars(count=60, price=10.0, volume=1_000_000):
    end = datetime(2026, 7, 26)
    rows = []
    for offset in range(count):
        stamp = end - timedelta(days=count - 1 - offset)
        rows.append({
            "time": stamp,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
        })
    return pd.DataFrame(rows)


def test_liquidity_60d_passes_at_ten_billion():
    result = scan_cache.compute_liquidity_60d(
        _daily_bars(),
        symbol="AAA",
        now=datetime(2026, 7, 27, 10, 0),
        min_billion=10,
    )

    assert result["sessions_available"] == 60
    assert result["average_value_billion"] == 10.0
    assert result["status"] == "PASS"


def test_liquidity_60d_requires_sixty_completed_sessions():
    result = scan_cache.compute_liquidity_60d(
        _daily_bars(count=59),
        symbol="AAA",
        now=datetime(2026, 7, 27, 10, 0),
        min_billion=10,
    )

    assert result["sessions_available"] == 59
    assert result["status"] == "INSUFFICIENT"


def test_liquidity_filter_is_not_applied_to_derivatives():
    result = scan_cache.compute_liquidity_60d(
        _daily_bars(),
        symbol="VN30F1M",
        now=datetime(2026, 7, 27, 10, 0),
        min_billion=10,
    )

    assert result["status"] == "NOT_APPLICABLE"


def test_telegram_ckcs_uses_liquidity_gate(monkeypatch):
    monkeypatch.setattr(
        scan_cache,
        "liquidity_filter_allows",
        lambda symbol: symbol == "PASS",
    )
    settings = {
        "opportunity_ckps_enabled": True,
        "opportunity_ckcs_enabled": True,
    }

    assert opportunity_alerts._passes_filter(
        {"symbol": "PASS", "market_type": "CKCS"}, settings
    )
    assert not opportunity_alerts._passes_filter(
        {"symbol": "FAIL", "market_type": "CKCS"}, settings
    )
    assert opportunity_alerts._passes_filter(
        {"symbol": "VN30F1M", "market_type": "CKPS"}, settings
    )


def test_schedule_liquidity_defaults_and_validation():
    defaults = schedule_settings.normalize({})
    assert defaults["ckcs_liquidity_filter_enabled"] is False
    assert defaults["ckcs_liquidity_sessions"] == 60
    assert defaults["ckcs_liquidity_min_billion"] == 10.0

    custom = schedule_settings.normalize({
        "ckcs_liquidity_filter_enabled": True,
        "ckcs_liquidity_sessions": "30",
        "ckcs_liquidity_min_billion": "25.5",
    })
    assert custom["ckcs_liquidity_filter_enabled"] is True
    assert custom["ckcs_liquidity_sessions"] == 30
    assert custom["ckcs_liquidity_min_billion"] == 25.5

    capped = schedule_settings.normalize({"ckcs_liquidity_sessions": 999})
    assert capped["ckcs_liquidity_sessions"] == 100
