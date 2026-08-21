from __future__ import annotations

from viking_v2.trading.market import action_for_symbol, previous_working_day


def test_corporate_exit_is_last_working_day_before_ex_date():
    dates = ["2026-08-07", "2026-08-10", "2026-08-12"]
    assert previous_working_day("2026-08-12", dates).isoformat() == "2026-08-10"
    action = action_for_symbol(
        [{"symbol": "FPT", "ex_date": "2026-08-12", "sell_enabled": True}],
        "FPT",
        today="2026-08-10",
        working_dates=dates,
    )
    assert action["exit_due"] is True
    assert action["warning_due"] is True


def test_corporate_action_can_be_warning_only():
    action = action_for_symbol(
        [{"symbol": "FPT", "ex_date": "2026-08-12", "sell_enabled": False}],
        "FPT",
        today="2026-08-11",
    )
    assert action["warning_due"] is True
    assert action["exit_due"] is False


def test_active_corporate_mark_blocks_entry_without_creating_auto_sell():
    action = action_for_symbol(
        [{"symbol": "FPT", "ex_date": "2026-08-12", "enabled": True, "sell_enabled": False}],
        "FPT",
        today="2026-08-01",
    )
    assert action["active"] is True
    assert action["blocks_entry"] is True
    assert action["exit_due"] is False
