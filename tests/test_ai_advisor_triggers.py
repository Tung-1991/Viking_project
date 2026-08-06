# -*- coding: utf-8 -*-

from ai_advisor.triggers import evaluate


def test_advisor_trigger_ignores_symbol_brake_and_raw_counters():
    state = {
        "cooldown_until": 9999999999,
        "bot_pnl_today": -999999,
        "bot_losing_streak": 999,
        "active_brake": {
            "global": None,
            "symbols": {"ETHUSD": {"until": 9999999999}},
        },
    }

    assert evaluate(state) == []


def test_advisor_trigger_fires_only_global_brake():
    state = {
        "active_brake": {
            "global": {"reason": "Max daily loss", "until": 9999999999},
            "symbols": {},
        }
    }

    assert evaluate(state) == ["global_cooldown_emergency"]
