from __future__ import annotations

import json

from viking_v2.rules.state import RuleStateStore


def test_market_state_requires_three_distinct_sessions(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    assert store.observe_market_candidate("UPTREND", "2026-08-10", 3) == "UNKNOWN"
    assert store.observe_market_candidate("UPTREND", "2026-08-10", 3) == "UNKNOWN"
    assert store.observe_market_candidate("UPTREND", "2026-08-11", 3) == "UNKNOWN"
    assert store.observe_market_candidate("UPTREND", "2026-08-12", 3) == "UPTREND"


def test_unconfirmed_market_exposes_candidate_instead_of_claiming_missing_data(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    store.observe_market_candidate("TRANSITION", "2026-08-12", 3)

    status = store.market_confirmation(3)

    assert status == {
        "confirmed": "UNKNOWN",
        "candidate": "TRANSITION",
        "display": "TRANSITION",
        "count": 1,
        "required": 3,
        "pending": True,
        "session": "2026-08-12",
    }


def test_market_candidate_change_restarts_confirmation(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    store.observe_market_candidate("UPTREND", "1", 3)
    store.observe_market_candidate("UPTREND", "2", 3)
    assert store.observe_market_candidate("DOWNTREND", "3", 3) == "UNKNOWN"
    assert store.observe_market_candidate("DOWNTREND", "4", 3) == "UNKNOWN"
    assert store.observe_market_candidate("DOWNTREND", "5", 3) == "DOWNTREND"


def test_market_confirmation_replays_existing_history_at_startup(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")

    confirmed = store.rebuild_market_confirmation(
        [
            ("UPTREND", "1"),
            ("UPTREND", "2"),
            ("UPTREND", "3"),
            ("DOWNTREND", "4"),
            ("DOWNTREND", "5"),
            ("DOWNTREND", "6"),
        ],
        required=3,
    )

    assert confirmed == "DOWNTREND"
    assert store.market_confirmation(3) == {
        "confirmed": "DOWNTREND",
        "candidate": "DOWNTREND",
        "display": "DOWNTREND",
        "count": 3,
        "required": 3,
        "pending": False,
        "session": "6",
    }


def test_position_peak_and_protection_state_persist(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    first = store.update_position_metrics("FPT", "T1", profit_pct=7.0)
    second = store.update_position_metrics("FPT", "T1", profit_pct=4.0)
    assert first["peak_profit_pct"] == 7.0
    assert second["peak_profit_pct"] == 7.0
    store.mark_protection_done("FPT", "T1", ["NORMAL_PROTECTION"])
    assert store.position_metrics("FPT", "T1")["normal_protection_done"] is True


def test_position_mae_mfe_and_current_net_pnl_persist_by_trade(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    store.update_position_metrics("FPT", "T1", profit_pct=-2.0, net_pnl=-200_000, market_price=98)
    store.update_position_metrics("FPT", "T1", profit_pct=7.0, net_pnl=700_000, market_price=107)
    current = store.update_position_metrics("FPT", "T1", profit_pct=3.0, net_pnl=300_000, market_price=103)
    assert current["current_net_pnl"] == 300_000
    assert current["mae_net_pnl"] == -200_000
    assert current["mfe_net_pnl"] == 700_000
    assert current["mae_pct"] == -2.0
    assert current["mfe_pct"] == 7.0

    reset = store.update_position_metrics("FPT", "T2", profit_pct=1.0, net_pnl=100_000)
    assert reset["mae_net_pnl"] == 0.0
    assert reset["mfe_net_pnl"] == 100_000


def test_same_symbol_trade_metrics_do_not_overwrite_each_other(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    store.update_position_metrics("FPT", "REAL-T1", profit_pct=5, net_pnl=500_000)
    store.update_position_metrics("FPT", "PAPER-T1", profit_pct=-2, net_pnl=-200_000)
    assert store.position_metrics("FPT", "REAL-T1")["current_net_pnl"] == 500_000
    assert store.position_metrics("FPT", "PAPER-T1")["current_net_pnl"] == -200_000


def test_signal_is_claimed_once_per_symbol_direction_and_candle(tmp_path):
    store = RuleStateStore(tmp_path / "rule.json")
    assert store.claim_signal("FPT", "BUY", "2026-08-11")
    assert not store.claim_signal("FPT", "BUY", "2026-08-11")
    assert store.claim_signal("FPT", "BUY", "2026-08-12")
    assert store.claim_signal("FPT", "SELL", "2026-08-12")


def test_real_and_paper_do_not_consume_each_others_signal(tmp_path):
    store = RuleStateStore(tmp_path / "rule-streams.json")
    assert store.claim_signal("FPT", "BUY", "D1", stream="PAPER")
    assert store.claim_signal("FPT", "BUY", "D1", stream="REAL")
    assert not store.claim_signal("FPT", "BUY", "D1", stream="PAPER")
    assert store.release_signal("FPT", "BUY", "D1", stream="PAPER")
    assert store.claim_signal("FPT", "BUY", "D1", stream="PAPER")
    assert not store.claim_signal("FPT", "BUY", "D1", stream="REAL")


def test_first_buy_signal_time_persists_until_signal_or_candle_changes(tmp_path):
    store = RuleStateStore(tmp_path / "rule-signal-time.json")
    first = "2026-09-09T09:15:01+07:00"
    later = "2026-09-09T09:16:00+07:00"
    assert store.observe_signal_time("FPT", "PAPER", "BUY", "D1", first) == first
    assert store.observe_signal_time("FPT", "PAPER", "BUY", "D1", later) == first
    assert store.observe_signal_time("FPT", "PAPER", "SELL", "D1", later) == ""
    assert store.observe_signal_time("FPT", "PAPER", "BUY", "D1", later) == later
    assert store.claim_signal("FPT", "BUY", f"D1|{first}", stream="PAPER")
    assert store.claim_signal("FPT", "BUY", f"D1|{later}", stream="PAPER")


def test_live_indicator_observations_survive_restart_and_keep_modes_separate(tmp_path):
    path = tmp_path / "rule-indicators.json"
    first = RuleStateStore(path)
    one = {
        "buy_ema_fast_period": 3, "buy_ema_slow_period": 6,
        "sell_ema_fast_period": 3, "sell_ema_slow_period": 6,
        "rsi_period": 14, "buy_ema_fast": 10.0, "buy_ema_slow": 10.1,
    }
    two = {**one, "buy_ema_fast": 10.2}
    assert first.observe_indicators("FPT", "REAL", "D1", one) == {}

    restarted = RuleStateStore(path)
    assert restarted.observe_indicators("FPT", "REAL", "D1", two) == one
    assert restarted.observe_indicators("FPT", "PAPER", "D1", two) == {}


def test_indicator_observation_resets_when_periods_change(tmp_path):
    store = RuleStateStore(tmp_path / "rule-periods.json")
    old = {
        "buy_ema_fast_period": 3, "buy_ema_slow_period": 6,
        "sell_ema_fast_period": 3, "sell_ema_slow_period": 6,
        "rsi_period": 14,
    }
    changed = {**old, "buy_ema_fast_period": 5}
    store.observe_indicators("FPT", "REAL", "D1", old)
    assert store.observe_indicators("FPT", "REAL", "D1", changed) == {}


def test_signals_claimed_before_the_buy_sell_rename_stay_claimed(tmp_path):
    path = tmp_path / "rule.json"
    path.write_text(
        json.dumps({"processed_signals": {"FPT|M": "2026-08-11", "FPT|B": "2026-08-11"}}),
        encoding="utf-8",
    )
    store = RuleStateStore(path)
    assert not store.claim_signal("FPT", "BUY", "2026-08-11")
    assert not store.claim_signal("FPT", "SELL", "2026-08-11")
    assert store.claim_signal("FPT", "BUY", "2026-08-12")


def test_manual_sell_entry_pause_is_mode_specific_persistent_and_expires(tmp_path):
    path = tmp_path / "rule-pause.json"
    store = RuleStateStore(path)
    pause = store.start_entry_pause(
        "PAPER", 15 * 60, symbol="FPT", now=1_000.0,
    )

    assert pause["until"] == 1_900.0
    assert pause["symbol"] == "FPT"
    assert store.entry_pause("REAL", now=1_001.0)["active"] is False

    restarted = RuleStateStore(path)
    active = restarted.entry_pause("PAPER", now=1_300.0)
    assert active["active"] is True
    assert active["remaining_seconds"] == 600.0
    assert restarted.entry_pause("PAPER", now=1_900.0)["active"] is False
