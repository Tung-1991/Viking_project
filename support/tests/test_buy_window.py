from datetime import datetime

import pytest

from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.entry_filters import apply_buy_filters
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.market import VN_TZ, validate_buy_window
from viking_v2.trading.orders import OrderQueue
from viking_v2.backtest.engine import BacktestEngine
from viking_v2.backtest.models import BacktestConfig
from viking_v2.backtest.data import HistoricalDataStore
from viking_v2.backtest.replay import ReplayDataStore


def clock(value, day=4):
    hour, minute = map(int, value.split(":"))
    return datetime(2026, 9, day, hour, minute, tzinfo=VN_TZ)


def step(state, at, *, raw=False, ema=True, rsi=True, params=None, portfolio=None):
    rule = StaticRule(params or StaticRuleParameters(buy_window_enabled=True, whipsaw_enabled=False))
    indicators = {"buy_ema_fast": 11 if ema else 9, "buy_ema_slow": 10,
                  "rsi": 51 if rsi else 49, "rsi_previous": 50}
    decision = StrategyDecision("BUY" if raw else "WAIT", "CTS", "BUY_SIGNAL" if raw else "NO_NEW_BUY_SIGNAL",
                                signal="BUY" if raw else "", details={"indicators": indicators})
    context = {"signal_mode": "REALTIME", "symbol": "CTS", "confirmed_market_state": "ACCUMULATION"}
    return apply_buy_filters(rule, decision, context,
                             {"available_capital": 600_000_000, **(portfolio or {})},
                             state, observed_at=at, exchange="HOSE")


def test_morning_cross_is_rechecked_at_window_open_not_bought_in_morning():
    state, decision = step({}, clock("09:52"), raw=True)
    assert decision.reason == "BUY_WINDOW_WAIT"
    state, decision = step(state, clock("13:59"))
    assert decision.action == "WAIT"
    state, decision = step(state, clock("14:00"))
    assert decision.action == "BUY"
    assert decision.details["buy_window"]["signal_time"] == clock("09:52").isoformat()
    assert state == {}


@pytest.mark.parametrize("broken", [{"ema": False}, {"rsi": False}])
def test_broken_morning_signal_requires_a_new_cross(broken):
    state, _ = step({}, clock("09:52"), raw=True)
    state, decision = step(state, clock("13:50"), **broken)
    assert decision.reason == "BUY_WINDOW_BROKEN"
    state, decision = step(state, clock("14:00"))
    assert decision.action == "WAIT"
    _, decision = step(state, clock("14:02"), raw=True)
    assert decision.action == "BUY"


def test_hdb_rsi_equality_cancels_waiting_buy_without_placing_an_order():
    rule = StaticRule(StaticRuleParameters(buy_window_enabled=True, whipsaw_enabled=False))
    baseline = 56.79212294979958
    context = {"symbol": "HDB", "signal_mode": "REALTIME", "confirmed_market_state": "ACCUMULATION"}
    portfolio = {"available_capital": 15_000_000}
    state = {}
    for at, fast, slow, current_rsi, raw, expected_reason in (
        ("13:39:31", 22.4413, 22.4174, 57.7026, True, "BUY_WINDOW_WAIT"),
        ("13:39:41", 22.4163, 22.4031, baseline, False, "BUY_WINDOW_BROKEN"),
    ):
        decision = StrategyDecision(
            "BUY" if raw else "WAIT", "HDB", "BUY_SIGNAL" if raw else "NO_NEW_BUY_SIGNAL",
            signal="BUY" if raw else "",
            details={"indicators": {"buy_ema_fast": fast, "buy_ema_slow": slow,
                                    "rsi": current_rsi, "rsi_previous": baseline}},
        )
        state, result = apply_buy_filters(
            rule, decision, context, portfolio, state,
            observed_at=datetime.fromisoformat(f"2026-10-09T{at}+07:00"), exchange="HOSE",
        )
        assert result.action == "WAIT" and result.reason == expected_reason
        # The BUY label refers to the waiting candidate, not a fresh valid BUY.
        assert result.signal == "BUY"
    assert state == {}
    assert result.details["buy_window"]["state"] == "CANCELLED"
    assert result.details["indicators"]["rsi"] == result.details["indicators"]["rsi_previous"]


def test_window_holds_only_the_enabled_base_buy_conditions():
    params = StaticRuleParameters(
        buy_window_enabled=True,
        buy_signal_use_ema=True,
        buy_signal_use_rsi=False,
        whipsaw_enabled=False,
    )
    state, _ = step({}, clock("09:52"), raw=True, params=params)
    state, decision = step(state, clock("13:50"), rsi=False, params=params)
    assert decision.reason == "BUY_WINDOW_WAIT"
    _, decision = step(state, clock("14:00"), rsi=False, params=params)
    assert decision.action == "BUY"


@pytest.mark.parametrize("portfolio,reason", [
    ({"available_capital": 0}, "NO_AVAILABLE_CAPITAL"),
    ({"loss_streak": 3}, "LOCKED_AFTER_LOSSES"),
    ({"open_positions": 5}, "MAX_POSITIONS"),
    ({"corporate_action_blocked": True}, "CORPORATE_ACTION_BLOCK"),
])
def test_window_release_rechecks_entry_guards(portfolio, reason):
    state, _ = step({}, clock("09:52"), raw=True)
    _, decision = step(state, clock("14:00"), portfolio=portfolio)
    assert decision.action == "WAIT" and decision.reason == reason


def test_combined_filters_start_timer_at_1400_and_allow_rsi_only():
    params = StaticRuleParameters(buy_window_enabled=True, buy_confirmation_enabled=True,
                                  buy_confirmation_require_ema=False, buy_confirmation_require_rsi=True,
                                  whipsaw_enabled=False)
    state, _ = step({}, clock("09:52"), raw=True, params=params)
    state, decision = step(state, clock("14:00"), params=params)
    assert decision.reason == "BUY_CONFIRMATION_WAIT"
    assert decision.details["buy_confirmation"]["minutes_held"] == 0
    state, decision = step(state, clock("14:04"), ema=False, params=params)
    assert decision.action == "WAIT"
    _, decision = step(state, clock("14:05"), ema=False, params=params)
    assert decision.action == "BUY"


def test_candidate_expires_at_end_and_does_not_survive_into_next_day():
    state, _ = step({}, clock("13:59"), raw=True)
    _, decision = step(state, clock("14:45"))
    assert decision.reason == "BUY_WINDOW_EXPIRED"
    _, decision = step(state, clock("14:00", day=7))
    assert decision.action == "WAIT"


def test_start_time_filter_does_not_introduce_a_1430_cutoff():
    _, decision = step({}, clock("14:35"), raw=True)
    assert decision.action == "BUY"
    assert decision.details["buy_window"]["end"] == "14:45"


@pytest.mark.parametrize("at", [clock("08:55"), clock("12:00"), clock("14:00", day=5)])
def test_raw_signal_outside_session_cannot_bypass_window(at):
    _, decision = step({}, at, raw=True)
    assert decision.action == "WAIT"
    assert decision.reason == "BUY_WINDOW_MARKET_CLOSED"


def test_window_candidate_survives_restart_separate_real_paper(tmp_path):
    state, _ = step({}, clock("13:59"), raw=True)
    store = RuleStateStore(tmp_path / "state.json")
    store.save_buy_confirmation("CTS", "PAPER", state)
    restored = RuleStateStore(tmp_path / "state.json")
    assert restored.buy_confirmation("CTS", "REAL") == {}
    _, decision = step(restored.buy_confirmation("CTS", "PAPER"), clock("14:00"))
    assert decision.action == "BUY"


@pytest.mark.parametrize("event", ["STOP_LOSS", "INDICATOR_EXIT"])
def test_sell_bypasses_both_buy_filters_even_in_closed_mode(event):
    rule = StaticRule(StaticRuleParameters(buy_window_enabled=True, buy_confirmation_enabled=True))
    decision = StrategyDecision("SELL", "CTS", event, event=event)
    state, actual = apply_buy_filters(rule, decision, {"signal_mode": "CLOSED"},
                                     {"position_quantity": 100}, {"window": {}},
                                     observed_at=clock("09:30"), exchange="HOSE")
    assert actual is decision and state == {}


@pytest.mark.parametrize("start,end", [("14", "14:30"), ("15:00", "14:00"), ("09:00", "25:00"), ("08:00", "14:00")])
def test_invalid_window_rejected(start, end):
    with pytest.raises(ValueError):
        validate_buy_window(start, end)


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_queue_enforces_buy_window_but_allows_sells(mode, tmp_path):
    now = [clock("13:59").timestamp()]
    queue = OrderQueue(tmp_path / "orders.json", now=lambda: now[0])
    buy = OrderIntent.create("CTS", "BUY", 100, "MARKET", execution_mode=mode)
    buy.created_at = now[0]
    buy.expires_at = clock("14:30").timestamp()
    buy.buy_window_start, buy.buy_window_end, buy.buy_window_date = "14:00", "14:30", "2026-09-04"
    queue.add(buy)
    sell = OrderIntent.create("VIX", "SELL", 100, "MARKET", execution_mode=mode)
    sell.created_at, sell.expires_at = now[0], clock("15:00").timestamp()
    queue.add(sell)
    due = queue.claim_due(phase="OPEN", execution_mode=mode, token_ready=True)
    assert [order.side for order in due] == ["SELL"]
    now[0] = clock("14:00").timestamp()
    due = queue.claim_due(phase="OPEN", execution_mode=mode, token_ready=True)
    assert [order.side for order in due] == ["BUY"]
    queue._update(buy.id, status="PENDING")
    now[0] = clock("14:30").timestamp()
    assert queue.claim_due(phase="OPEN", execution_mode=mode, token_ready=True) == []
    assert queue.get(buy.id).status == "EXPIRED"


def test_broker_working_order_is_not_falsely_expired_at_window_end(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json", now=lambda: clock("14:31").timestamp())
    intent = OrderIntent.create("CTS", "BUY", 100, "LO", limit_price=27)
    intent.created_at, intent.expires_at = clock("14:00").timestamp(), clock("14:30").timestamp()
    intent.buy_window_end = "14:30"
    intent.status = "WORKING"
    queue.add(intent)
    assert queue.expire() == []
    assert queue.get(intent.id).status == "WORKING"


@pytest.mark.parametrize("mode", ["DAILY", "AUTO_HYBRID"])
def test_window_rejects_daily_or_hybrid_before_loading_data(tmp_path, mode):
    engine = BacktestEngine(HistoricalDataStore(root=tmp_path / "data", fetcher=lambda *_: None),
                            ReplayDataStore(tmp_path / "replay"))
    config = BacktestConfig(["CTS"], "2026-09-04", "2026-09-04", simulation_mode=mode,
                            rule_parameters={"buy_window_enabled": True})
    with pytest.raises(RuntimeError, match="KHUNG GIỜ MUA cần MODE 2"):
        engine.run(config, save=False)
