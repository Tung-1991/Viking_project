from datetime import datetime

from viking_v2.config import AppSettings
from viking_v2.trading.market import VN_TZ, market_phase, market_session_clock


def test_custom_holiday_blocks_session_even_on_weekday():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=VN_TZ)
    assert market_phase(now, holidays=["2026-08-13"])[0] == "HOLIDAY"


def test_official_exchange_holiday_blocks_session_without_manual_input():
    now = datetime(2026, 9, 2, 10, 0, tzinfo=VN_TZ)
    assert market_phase(now, holidays=AppSettings().trading_holidays)[0] == "HOLIDAY"


def test_session_clock_shows_window_and_short_countdown_without_seconds():
    assert market_session_clock(datetime(2026, 8, 13, 9, 5, tzinfo=VN_TZ)) == (
        "ATO 09:00-09:15 · 10P", True,
    )
    assert market_session_clock(datetime(2026, 8, 13, 10, 0, tzinfo=VN_TZ)) == (
        "LIVE 09:15-11:30 · 1G30P", True,
    )
    assert market_session_clock(datetime(2026, 8, 13, 14, 35, tzinfo=VN_TZ)) == (
        "ATC 14:30-14:45 · 10P", True,
    )
    assert market_session_clock(datetime(2026, 8, 13, 15, 0, tzinfo=VN_TZ)) == (
        "CLOSED · MỞ MAI 09:00 · 18G00P", False,
    )


def test_session_clock_shows_next_open_during_lunch_and_weekend():
    assert market_session_clock(datetime(2026, 8, 13, 11, 45, tzinfo=VN_TZ)) == (
        "CLOSED · MỞ 13:00 · 1G15P", False,
    )
    assert market_session_clock(datetime(2026, 8, 14, 15, 0, tzinfo=VN_TZ)) == (
        "CLOSED · MỞ T2 09:00 · 2N18G", False,
    )


def test_session_clock_uses_dnse_working_dates_for_next_open():
    assert market_session_clock(
        datetime(2026, 8, 14, 15, 0, tzinfo=VN_TZ),
        working_dates=["2026-08-14", "2026-08-18"],
    ) == ("CLOSED · MỞ T3 09:00 · 3N18G", False)
