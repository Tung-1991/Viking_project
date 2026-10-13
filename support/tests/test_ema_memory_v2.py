"""New watch-admission / multi-session contract, with no live accounts."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from viking_v2.rules.business import StaticRuleParameters, indicator_snapshot
from viking_v2.rules.ema_memory import advance_memory, memory_caption, memory_count_scope, memory_day_counts, memory_hint
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.session_cross import SessionCrossService, SessionPriceCache
from viking_v2.trading.market import VN_TZ

START = datetime(2026, 10, 5, 14, tzinfo=VN_TZ)


def observe(row, fast, slow, now, mode="VA", holidays=None):
    return advance_memory(row, {"buy_ema_fast": fast, "buy_ema_slow": slow}, now=now,
                          pair="3/6", mode=mode, sessions=3, exchange="HOSE", holidays=holidays)


def test_first_above_ignores_pre_admission_cross_then_requires_down_and_up():
    row = observe({}, 101, 100, START)
    assert row["state"] == "WAIT_DOWN" and row["up_count"] == row["down_count"] == 0
    row = observe(row, 99, 100, START + timedelta(seconds=1))
    assert row["state"] == "WAIT_UP" and row["down_count"] == 1
    row = observe(row, 101, 100, START + timedelta(seconds=2))
    assert row["valid"] and row["up_count"] == 1
    identity = row["cross_id"]
    for seconds in range(3, 9):
        row = observe(row, 101, 100, START + timedelta(seconds=seconds))
    assert row["up_count"] == 1 and row["cross_id"] == identity


@pytest.mark.parametrize("bad", [None,"bad",float("nan"),float("inf")])
def test_bad_observation_keeps_counts_but_cannot_authorize_buy(bad):
    row = observe({},99,100,START)
    row = observe(row,101,100,START+timedelta(seconds=1))
    result = observe(row,bad,100,START+timedelta(seconds=2))
    assert result["state"] == "UNKNOWN" and not result["valid"]
    assert result["cross_id"] == row["cross_id"] and result["up_count"] == 1


def test_first_below_only_needs_one_up_and_va_survives_two_weeks():
    row = observe({}, 99, 100, START)
    row = observe(row, 101, 100, START + timedelta(seconds=1))
    row = observe(row, 102, 100, START + timedelta(days=14))
    assert row["state"] == "SESSION_ACTIVE" and row["up_count"] == 1 and row["down_count"] == 0
    assert row["expires_at"] == 0


def test_daily_count_resets_without_expiring_or_reusing_va_cross(tmp_path):
    import json
    row = observe({}, 99, 100, START)
    row = observe(row, 101, 100, START + timedelta(seconds=1))
    identity = row["cross_id"]
    assert memory_day_counts(row, now=START)["up"] == 1
    row["used"] = True
    store = tmp_path / "memory.json"
    store.write_text(json.dumps(row), encoding="utf-8")
    restarted = json.loads(store.read_text(encoding="utf-8"))
    assert memory_day_counts(restarted, now=START)["up"] == 1
    tomorrow = START + timedelta(days=1)
    assert memory_day_counts(restarted, now=tomorrow)["up"] == 0
    kept = observe(restarted, 101, 100, tomorrow)
    assert kept["daily_up_count"] == kept["daily_down_count"] == 0
    assert kept["up_count"] == 1 and kept["cross_id"] == identity
    assert kept["used"] and kept["state"] == "SESSION_USED"
    down = observe(kept, 99, 100, tomorrow + timedelta(seconds=1))
    up = observe(down, 101, 100, tomorrow + timedelta(seconds=2))
    assert up["daily_up_count"] == up["daily_down_count"] == 1
    assert up["up_count"] == 2 and up["cross_id"] != identity
    assert up["valid"] and not up["used"]
    repeated = observe(up, 99, 100, tomorrow + timedelta(seconds=1))
    assert repeated["daily_up_count"] == repeated["daily_down_count"] == 1
    assert repeated["cross_id"] == up["cross_id"]


def test_legacy_counts_are_today_only_when_start_and_observation_prove_it():
    row = observe({}, 99, 100, START)
    row = observe(row, 101, 100, START + timedelta(seconds=1))
    for key in tuple(row):
        if key.startswith("daily_"):
            row.pop(key)
    row.update(up_count=19, down_count=18)
    assert memory_day_counts(row, now=START)["up"] == 19
    assert memory_day_counts(row, now=START)["down"] == 18
    row["started_at"] = (START - timedelta(days=1)).isoformat()
    assert memory_day_counts(row, now=START)["up"] is None
    identity = row["cross_id"]
    result = observe(row, 101, 100, START + timedelta(seconds=2))
    assert result["daily_up_count"] == result["daily_down_count"] == 0
    assert result["daily_count_partial"]
    assert result["up_count"] == 19 and result["down_count"] == 18
    assert result["cross_id"] == identity and result["valid"]


def test_memory_count_scope_uses_saved_watch_and_history_starts():
    row = {"mode": "VA", "started_at": "2026-10-09T14:00:00+07:00",
           "observed_at": "2026-10-13T14:30:00+07:00", "up_count": 19, "down_count": 18}
    assert memory_count_scope(row)["sessions"] == 3
    assert memory_count_scope(row, holidays=["2026-10-12"])["sessions"] == 2
    assert "37 lần cắt = 19 lên + 18 xuống" in memory_hint(row)
    assert "2026-10-09 → 2026-10-13 · 3 phiên" in memory_hint(row)
    row["started_at"] = "2026-10-08T09:00:00+07:00"
    assert memory_count_scope(row)["sessions"] == 4  # Thu, Fri, Mon, Tue.
    row.update(va_lookback_enabled=True, lookback_ready=True,
               lookback_started_at="2026-10-07T00:00:00+07:00")
    assert memory_count_scope(row)["sessions"] == 5


def test_equal_baseline_does_not_count_as_a_downward_cross():
    row = observe({}, 100, 100, START)
    row = observe(row, 101, 100, START + timedelta(seconds=1))
    assert row["state"] == "WAIT_DOWN" and row["up_count"] == 0 and not row.get("cross_id")
    row = observe(row, 99, 100, START + timedelta(seconds=2))
    row = observe(row, 100, 100, START + timedelta(seconds=3))
    row = observe(row, 101, 100, START + timedelta(seconds=4))
    assert row["state"] == "SESSION_ACTIVE" and row["up_count"] == row["down_count"] == 1


def test_equal_to_below_is_neutral_until_strict_up_cross():
    row = observe({}, 100, 100, START)
    row = observe(row, 99, 100, START + timedelta(seconds=1))
    assert row["down_count"] == 0 and row["up_count"] == 0
    row = observe(row, 101, 100, START + timedelta(seconds=2))
    assert row["valid"] and row["up_count"] == 1 and row["down_count"] == 0


def test_atc_up_can_be_used_next_afternoon_but_down_cannot_buy():
    row = observe({}, 99, 100, START.replace(hour=14, minute=29))
    row = observe(row, 101, 100, START.replace(hour=14, minute=44))
    identity = row["cross_id"]
    next_afternoon = START + timedelta(days=1)
    above = observe(row, 101, 100, next_afternoon)
    below = observe(row, 99, 100, next_afternoon)
    assert above["valid"] and above["cross_id"] == identity
    assert not below["valid"] and below["cross_id"] == identity


def test_three_sessions_keeps_record_on_down_then_expires_including_cross_day():
    row = observe({}, 99, 100, START, "SESSIONS")
    row = observe(row, 101, 100, START + timedelta(days=1), "SESSIONS")
    assert row["expiry_sessions"] == ["2026-10-06", "2026-10-07", "2026-10-08"]
    identity = row["cross_id"]
    down = observe(row, 99, 100, START + timedelta(days=2), "SESSIONS")
    assert down["cross_id"] == identity and not down["valid"] and down["remaining_sessions"] == 2
    expired = observe(down, 99, 100, START.replace(day=8, hour=14, minute=45), "SESSIONS")
    assert expired["state"] == "EXPIRED" and expired["cross_id"] == identity
    assert expired["up_count"] == 1 and expired["down_count"] == 1


def test_new_up_restarts_clock_and_weekend_holiday_are_not_sessions():
    friday = START.replace(day=9)
    row = observe({}, 99, 100, friday, "SESSIONS")
    row = observe(row, 101, 100, friday + timedelta(seconds=1), "SESSIONS", ["2026-10-12"])
    assert row["expiry_sessions"] == ["2026-10-09", "2026-10-13", "2026-10-14"]
    old = row["cross_id"]
    row = observe(row, 99, 100, friday + timedelta(seconds=2), "SESSIONS")
    row = observe(row, 101, 100, friday + timedelta(days=4), "SESSIONS")
    assert row["cross_id"] != old and row["expiry_sessions"] == ["2026-10-13", "2026-10-14", "2026-10-15"]


def test_equality_is_neutral_and_caption_uses_current_ema_not_counts():
    row = observe({}, 99, 100, START)
    row = observe(row, 101, 100, START + timedelta(seconds=1))
    row = observe(row, 100, 100, START + timedelta(seconds=2))
    assert not row["valid"] and "CHỜ CẮT LÊN" in memory_caption(row)
    row = observe(row, 101, 100, START + timedelta(seconds=3))
    assert row["up_count"] == 1 and row["down_count"] == 0 and row["valid"]


def test_closed_observations_start_at_admission_and_keep_real_close_time(tmp_path):
    from support.tests.test_session_ema_cross import daily_bars
    state = RuleStateStore(tmp_path / "closed.json")
    client = Minutes()
    service = SessionCrossService(SessionPriceCache(tmp_path / "closed-minutes.json", client), state)
    params = StaticRuleParameters(whipsaw_enabled=False)
    state.sync_ema_watchlist(["A"], "REAL", now=START)
    bars = daily_bars(START, 104)
    initial = service.observe_closed("A", "REAL", bars, params, START, "HOSE")
    assert initial["up_count"] == 0 and not initial.get("cross_id")
    bars[-1]["closed"] = True
    after = START.replace(hour=15)
    up = service.observe_closed("A", "REAL", bars, params, after, "HOSE")
    assert up["cross_at"] == START.replace(hour=14, minute=45).isoformat() and up["up_count"] == 1
    next_day = START + timedelta(days=1)
    kept = service.observe_closed("A", "REAL", bars, params, next_day, "HOSE")
    assert kept["valid"] and kept["cross_id"] == up["cross_id"]
    assert client.calls == []  # CLOSED never fetches minute data.
    realtime = service.observe("A", "REAL", bars, indicator_snapshot(bars,3,6,14), params, next_day, "HOSE")
    assert realtime["observation_mode"] == "REALTIME" and realtime["up_count"] == 0
    assert realtime["started_at"] == next_day.isoformat()  # Changed sampling mode gets a fresh baseline.


class Minutes:
    def __init__(self):
        self.calls, self.fail = [], False

    def get_ohlc(self, symbol, interval, left, right):
        self.calls.append((symbol, interval, left, right))
        if self.fail:
            return None
        stamps = list(range(left, right + 1, 60))
        prices = [104.] * len(stamps)
        return {"s": "ok", "t": stamps, "o": prices, "h": prices, "l": prices,
                "c": prices, "v": [100] * len(stamps)}


def bars(now, price):
    first = START.replace(hour=0) - timedelta(days=40)
    return [{"time": (first + timedelta(days=i)).timestamp(), "close": 100, "closed": True}
            for i in range(40)] + [{"time": now.replace(hour=0).timestamp(), "close": price, "closed": False}]


def service_at(tmp_path):
    broker = Minutes()
    state = RuleStateStore(tmp_path / "rules.json")
    service = SessionCrossService(SessionPriceCache(tmp_path / "minutes.json", broker), state)
    params = StaticRuleParameters(whipsaw_enabled=False)
    def sample(now, price):
        rows = bars(now, price)
        return service.observe("FAKE", "REAL", rows, indicator_snapshot(rows, 3, 6, 14), params, now, "HOSE")
    return state, broker, service, params, sample


def test_restart_used_identity_survives_day_boundary_and_admission_restarts_on_readd(tmp_path):
    state, broker, service, params, sample = service_at(tmp_path)
    state.sync_ema_watchlist(["FAKE"], "REAL", now=START)
    sample(START, 95)
    cross = sample(START + timedelta(seconds=1), 104)
    assert state.claim_session_cross("FAKE", "REAL", cross["cross_id"], now=START.timestamp() + 1)
    next_day = START + timedelta(days=1)
    restarted = SessionCrossService(SessionPriceCache(tmp_path / "minutes.json", broker), RuleStateStore(tmp_path / "rules.json"))
    rows = bars(next_day, 104)
    current = restarted.observe("FAKE", "REAL", rows, indicator_snapshot(rows, 3, 6, 14), params, next_day, "HOSE")
    assert current["state"] == "SESSION_USED" and current["cross_id"] == cross["cross_id"]
    assert not state.claim_session_cross("FAKE", "REAL", cross["cross_id"], now=next_day.timestamp())
    assert state.session_cross_available("FAKE", "REAL", cross["cross_id"], now=next_day.timestamp(), allow_used=True)
    state.sync_ema_watchlist([], "REAL", now=next_day)
    state.sync_ema_watchlist(["FAKE"], "REAL", now=next_day + timedelta(seconds=1))
    new = sample(next_day + timedelta(seconds=1), 104)
    assert new["up_count"] == 0 and not new.get("cross_id") and new["state"] == "WAIT_DOWN"


def test_missing_recovery_blocks_then_retries_without_duplicate_minutes(tmp_path):
    state, broker, service, params, sample = service_at(tmp_path)
    sample(START, 95)
    initial = sample(START + timedelta(seconds=1), 104)
    broker.fail = True
    assert sample(START + timedelta(minutes=5), 104)["state"] == "UNKNOWN"
    calls = len(broker.calls)
    sample(START + timedelta(minutes=5, seconds=5), 104)
    assert len(broker.calls) == calls
    broker.fail = False
    recovered = sample(START + timedelta(minutes=6), 104)
    assert recovered["valid"] and recovered["cross_id"] == initial["cross_id"]
    calls = len(broker.calls)
    sample(START + timedelta(minutes=6, seconds=1), 104)
    assert len(broker.calls) == calls
