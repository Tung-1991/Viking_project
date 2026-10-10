"""Independent offline checks for recoverable intraday daily-EMA crossings."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.models import BrokerOrderResult
from viking_v2.rules.business import StaticRule, StaticRuleParameters, indicator_snapshot, ema
from viking_v2.rules.entry_filters import apply_buy_filters
from viking_v2.rules.observations import ema_cross_caption, advance_session_cross
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.session_cross import SessionCrossService, SessionPriceCache, missing_ranges
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore

NOW = datetime(2026, 10, 9, 14, 20, tzinfo=VN_TZ)
CROSS = NOW.replace(minute=10)


class MinuteBroker:
    def __init__(self):
        self.calls = []
        self.fail = False

    def get_ohlc(self, symbol, resolution, left, right):
        self.calls.append((symbol, resolution, left, right))
        if self.fail:
            return None
        stamps = list(range(left, right + 1, 60))
        prices = [104.0 if s >= CROSS.timestamp() - 60 else 95.0 for s in stamps]
        return {"s": "ok", "t": stamps, "c": prices, "o": prices, "h": prices, "l": prices,
                "v": [100] * len(stamps)}


def daily_bars(now=NOW, price=104):
    rows = [{"time": (now.replace(hour=0, minute=0) - timedelta(days=40-i)).timestamp(),
             "close": 100 - i * .1, "closed": True} for i in range(40)]
    return [*rows, {"time": now.replace(hour=0, minute=0).timestamp(), "close": price, "closed": False}]


def parameters(**changes):
    return StaticRuleParameters(buy_signal_session_cross_enabled=True, whipsaw_enabled=False, **changes)


def observed(tmp_path, *, now=NOW, broker=None, price=104, stream="REAL", params=None):
    broker = broker or MinuteBroker()
    params = params or parameters()
    state = RuleStateStore(tmp_path / "rules.json")
    cache = SessionPriceCache(tmp_path / "minutes.json", broker)
    service = SessionCrossService(cache, state)
    bars = daily_bars(now, price)
    marks = indicator_snapshot(bars, 3, 6, 14)
    cross = service.observe("TEST", stream, bars, marks, params, now, "HOSE")
    return service, state, broker, bars, marks, cross


def rule_context(bars, marks, cross):
    return {"symbol": "TEST", "exchange": "HOSE", "bars": bars, "signal_mode": "REALTIME",
            "indicator_snapshot": marks, "previous_indicators": marks,
            "previous_market_state": "UPTREND", "session_ema_cross": cross}


def portfolio(**changes):
    return {"available_capital": 25_000_000, "available_cash": 50_000_000,
            "nav": 50_000_000, "order_budget": 25_000_000, "open_positions": 0, **changes}


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW.timestamp())


def test_restart_1420_recovers_1410_cross_and_can_buy_without_new_cross(tmp_path):
    service, state, broker, bars, marks, cross = observed(tmp_path)
    assert cross["state"] == "SESSION_ACTIVE"
    assert cross["cross_at"] == CROSS.isoformat()
    assert StaticRule(parameters()).evaluate(rule_context(bars, marks, cross), portfolio()).action == "BUY"
    assert StaticRule(parameters(buy_signal_require_ema_cross=False)).params.buy_signal_require_ema_cross is False
    old = StaticRule(StaticRuleParameters(whipsaw_enabled=False))
    assert old.evaluate(rule_context(bars, marks, cross), portfolio()).action == "WAIT"
    assert len(broker.calls) == 2  # Morning and afternoon, excluding lunch.
    restarted = SessionCrossService(SessionPriceCache(tmp_path / "minutes.json", broker),
                                    RuleStateStore(tmp_path / "rules.json"))
    assert restarted.observe("TEST", "REAL", bars, marks, parameters(), NOW, "HOSE")["cross_id"] == cross["cross_id"]
    assert len(broker.calls) == 2


def test_reconstructed_ema_uses_daily_history_and_one_provisional_daily_close(tmp_path):
    service, state, broker, bars, marks, cross = observed(tmp_path)
    for period, key in ((3, "buy_ema_fast"), (6, "buy_ema_slow")):
        assert marks[key] == pytest.approx(ema([row["close"] for row in bars], period)[-1])
    before = indicator_snapshot(daily_bars(price=95), 3, 6, 14)
    assert cross["previous_fast"] == pytest.approx(before["buy_ema_fast"])
    assert cross["previous_slow"] == pytest.approx(before["buy_ema_slow"])
    assert 95 < cross["threshold_price"] < 104


def test_live_cache_toggles_and_continuous_quotes_do_not_refetch(tmp_path):
    service, state, broker, bars, marks, cross = observed(tmp_path)
    for seconds in (10, 20, 30, 40, 50, 60, 70):
        at = NOW + timedelta(seconds=seconds)
        service.cache.observe("TEST", 104, at, "HOSE", recover=seconds >= 60)
    assert len(broker.calls) == 2
    assert service.observe("TEST", "PAPER", bars, marks, parameters(), NOW + timedelta(seconds=70), "HOSE")["valid"]
    assert len(broker.calls) == 2


def test_restart_gap_only_fetches_missing_minutes(tmp_path):
    service, state, broker, bars, marks, cross = observed(tmp_path)
    restarted = SessionCrossService(SessionPriceCache(tmp_path / "minutes.json", broker), state)
    later = NOW + timedelta(minutes=10)
    assert restarted.observe("TEST", "REAL", bars, marks, parameters(), later, "HOSE")["valid"]
    assert broker.calls[2][2:] == (int(NOW.timestamp()), int(later.timestamp()) - 1)
    assert len(broker.calls) == 3


def test_missing_history_blocks_buy_with_retry_backoff_then_recovers(tmp_path):
    broker = MinuteBroker()
    broker.fail = True
    service, state, broker, bars, marks, cross = observed(tmp_path, broker=broker)
    assert cross["state"] == "UNKNOWN" and not cross["valid"]
    assert StaticRule(parameters()).evaluate(rule_context(bars, marks, cross), portfolio()).action == "WAIT"
    broker.fail = False
    assert not service.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(seconds=10), "HOSE")["valid"]
    assert len(broker.calls) == 1
    assert service.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(minutes=1), "HOSE")["valid"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5, 0])
def test_invalid_history_price_never_marks_coverage(tmp_path, bad):
    broker = MinuteBroker()
    broker.get_ohlc = lambda *args: {"s": "ok", "t": [int(NOW.timestamp()) - 60],
                                   "c": [bad], "o": [1], "h": [1], "l": [1], "v": [1]}
    _, _, _, _, _, cross = observed(tmp_path, broker=broker)
    assert not cross["ready"] and not cross["valid"]


def test_live_down_invalidates_across_restart_and_new_up_cross_is_new_event(tmp_path):
    service, state, broker, bars, marks, first = observed(tmp_path)
    down_at = NOW + timedelta(seconds=10)
    down_bars = daily_bars(price=95)
    down = indicator_snapshot(down_bars, 3, 6, 14)
    assert not service.observe("TEST", "REAL", down_bars, down, parameters(), down_at, "HOSE")["valid"]
    restarted = SessionCrossService(SessionPriceCache(tmp_path / "minutes.json", broker), state)
    assert not restarted.observe("TEST", "REAL", bars, marks, parameters(), down_at, "HOSE")["valid"]
    # A completed minute below, followed by a completed minute above.
    cache = restarted.cache
    for sec, price in ((20, 95), (30, 95), (40, 95), (50, 95), (60, 104), (70, 104),
                       (80, 104), (90, 104), (100, 104), (110, 104), (120, 104)):
        cache.observe("TEST", price, NOW + timedelta(seconds=sec), "HOSE")
    next_cross = restarted.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(minutes=2), "HOSE")
    assert next_cross["valid"] and next_cross["cross_id"] != first["cross_id"]


def test_live_cross_is_immediate_and_keeps_identity_when_minute_closes(tmp_path):
    service, state, broker, _, _, old = observed(tmp_path, price=95)
    at = NOW + timedelta(seconds=10)
    bars, marks = daily_bars(price=104), indicator_snapshot(daily_bars(price=104), 3, 6, 14)
    live = service.observe("TEST", "REAL", bars, marks, parameters(), at, "HOSE")
    assert live["valid"] and live["cross_at"] == at.isoformat()
    assert state.claim_session_cross("TEST", "REAL", live["cross_id"], now=at.timestamp())
    for seconds in (20, 30, 40, 50, 60):
        service.cache.observe("TEST", 104, NOW + timedelta(seconds=seconds), "HOSE")
    completed = service.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(minutes=1), "HOSE")
    assert completed["cross_id"] == live["cross_id"] and completed["cross_at"] == live["cross_at"]
    assert completed["state"] == "SESSION_USED"
    assert len(broker.calls) == 2


def test_live_cross_survives_temporary_history_failure_after_gap(tmp_path):
    service, state, broker, _, _, _ = observed(tmp_path, price=95)
    bars = daily_bars(price=104)
    marks = indicator_snapshot(bars, 3, 6, 14)
    live = service.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(seconds=10), "HOSE")
    broker.fail = True
    missing = service.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(minutes=2), "HOSE")
    assert missing["state"] == "UNKNOWN" and not missing["valid"]
    broker.fail = False
    recovered = service.observe("TEST", "REAL", bars, marks, parameters(), NOW + timedelta(minutes=3), "HOSE")
    assert recovered["valid"] and recovered["cross_id"] == live["cross_id"]


@pytest.mark.parametrize("guard,reason", [
    ({"pending_buy": True}, "BUY_ALREADY_PENDING"), ({"loss_blocked": True}, "LOCKED_AFTER_LOSSES"),
    ({"entry_orders_available": False}, "MAX_SYMBOL_ORDERS"), ({"entry_slot_available": False}, "MAX_POSITIONS"),
    ({"available_capital": 0}, "NO_AVAILABLE_CAPITAL"),
])
def test_recovered_cross_preserves_entry_guards(tmp_path, guard, reason):
    _, _, _, bars, marks, cross = observed(tmp_path)
    result = StaticRule(parameters()).evaluate(rule_context(bars, marks, cross), portfolio(**guard))
    assert result.action == "WAIT" and result.reason == reason


def test_rsi_and_indicator_readiness_still_required(tmp_path):
    _, _, _, bars, marks, cross = observed(tmp_path)
    rule = StaticRule(parameters())
    for changed in ({"rsi": marks["rsi_previous"]}, {"signal_ready": False}):
        assert rule.evaluate(rule_context(bars, {**marks, **changed}, cross), portfolio()).action == "WAIT"
    assert rule.evaluate(rule_context(bars, marks, cross), portfolio()).action == "BUY"


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_queue_claim_survives_restart_cancel_and_toggle_and_is_book_specific(tmp_path, clock, mode):
    _, state, _, bars, marks, cross = observed(tmp_path, stream=mode)
    queue = OrderQueue(tmp_path / "orders.json")
    planner = StrategyOrderPlanner(queue, TradeStateStore(tmp_path / "trades.json"), state)
    decision = StaticRule(parameters()).evaluate(rule_context(bars, marks, cross), portfolio())
    result = planner.plan(decision, execution_mode=mode, execution_style="MARKET", tick={"ask": 104},
                          portfolio=portfolio(), candle_key=cross["cross_id"])
    assert result.intent and result.intent.order_type == "MARKET"
    queue.cancel_local(result.intent.id)
    state = RuleStateStore(tmp_path / "rules.json")
    state.discard_buy_candidates()
    assert state.save_session_cross("TEST", mode, cross)["state"] == "SESSION_USED"
    assert not state.claim_session_cross("TEST", mode, cross["cross_id"])
    other = "REAL" if mode == "PAPER" else "PAPER"
    state.save_session_cross("TEST", other, cross)
    assert state.claim_session_cross("TEST", other, cross["cross_id"])
    assert len(queue.list_all()) == 1


def test_queue_write_failure_rolls_back_cross_claim(tmp_path, monkeypatch, clock):
    _, state, _, bars, marks, cross = observed(tmp_path)
    queue = OrderQueue(tmp_path / "orders.json")
    planner = StrategyOrderPlanner(queue, TradeStateStore(tmp_path / "trades.json"), state)
    def fail(_intent):
        raise RuntimeError("simulated disk failure")
    monkeypatch.setattr(queue, "add", fail)
    decision = StaticRule(parameters()).evaluate(rule_context(bars, marks, cross), portfolio())
    with pytest.raises(RuntimeError, match="disk failure"):
        planner.plan(decision, execution_mode="REAL", execution_style="MARKET", tick={"ask": 104},
                     portfolio=portfolio(), candle_key=cross["cross_id"])
    assert state.session_cross_available("TEST", "REAL", cross["cross_id"])
    assert queue.list_all() == []


@pytest.mark.parametrize("scenario", ["valid", "down", "last_quote_down", "expired", "unknown"])
def test_actual_fake_broker_submission_and_last_moment_checks(tmp_path, clock, scenario):
    _, state, _, bars, marks, cross = observed(tmp_path)
    queue = OrderQueue(tmp_path / "orders.json")
    queue._now = lambda: NOW.timestamp()
    trades = TradeStateStore(tmp_path / "trades.json")
    planner = StrategyOrderPlanner(queue, trades, state)
    decision = StaticRule(parameters()).evaluate(rule_context(bars, marks, cross), portfolio())
    intent = planner.plan(decision, execution_mode="REAL", execution_style="MARKET", tick={"ask": 104},
                          portfolio=portfolio(), candle_key=cross["cross_id"]).intent
    sent = []
    def send(order):
        sent.append(order)
        if scenario == "unknown":
            raise TimeoutError("fake lost response")
        return BrokerOrderResult(True, "WORKING", order_id="FAKE-123")
    broker = SimpleNamespace(has_trading_token=lambda: True, place_order=send)
    price = 95 if scenario == "down" else 104
    quote_calls = []
    def quote(_symbol):
        quote_calls.append(1)
        latest = 95 if scenario == "last_quote_down" and len(quote_calls) >= 2 else price
        return {"symbol": "TEST", "price": latest, "ask": latest, "timestamp": NOW.timestamp(), "source": "WS"}
    if scenario == "expired":
        state.save_session_cross("TEST", "REAL", {**cross, "expires_at": NOW.timestamp()})
    execution = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"),
                                 quote_provider=quote,
                                 trade_state=trades, rule_state=state)
    execution.process_due(phase="OPEN", execution_mode="REAL")
    execution.process_due(phase="OPEN", execution_mode="REAL")
    assert len(sent) == (1 if scenario in {"valid", "unknown"} else 0)
    assert queue.get(intent.id).status == {"valid": "WORKING", "unknown": "UNKNOWN",
                                           "down": "CANCELLED", "last_quote_down": "CANCELLED", "expired": "CANCELLED"}[scenario]


def test_off_does_not_consume_cross_and_buy_window_still_waits(tmp_path):
    from viking_v2.dashboard.actions import DashboardActionsMixin
    service, state, broker, bars, marks, cross = observed(tmp_path)
    rule = StaticRule(parameters(buy_window_enabled=True, buy_window_start="14:30"))
    context = rule_context(bars, marks, cross)
    decision = rule.evaluate(context, portfolio())
    saved, waiting = apply_buy_filters(rule, decision, context, portfolio(), {}, observed_at=NOW, exchange="HOSE")
    assert waiting.action == "WAIT" and waiting.reason == "BUY_WINDOW_WAIT"
    off = rule.evaluate(context, portfolio())
    off.reason = "BOT_OFF"
    DashboardActionsMixin._claim_terminal_buy(SimpleNamespace(rule_state=state), off, "REAL")
    assert state.session_cross_available("TEST", "REAL", cross["cross_id"], now=NOW.timestamp())
    later = NOW + timedelta(minutes=11)
    cross = service.observe("TEST", "REAL", bars, marks, rule.params, later, "HOSE")
    context["session_ema_cross"] = cross
    _, released = apply_buy_filters(rule, rule.evaluate(context, portfolio()), context, portfolio(), saved,
                                    observed_at=later, exchange="HOSE")
    assert released.action == "BUY"


def test_explicit_no_trades_response_can_be_cached_without_inventing_a_cross(tmp_path):
    broker = MinuteBroker()
    broker.get_ohlc = lambda *_: {"s": "no_data"}
    _, _, _, _, _, cross = observed(tmp_path, broker=broker)
    assert cross["ready"] and not cross["valid"] and not cross["cross_id"]


def test_new_day_fetches_new_session_and_never_reuses_old_identity(tmp_path):
    service, state, broker, bars, marks, old = observed(tmp_path)
    later = NOW + timedelta(days=3)
    new_bars = daily_bars(later)
    new_marks = indicator_snapshot(new_bars, 3, 6, 14)
    current = service.observe("TEST", "REAL", new_bars, new_marks, parameters(), later, "HOSE")
    assert current["day"] == later.date().isoformat() and current["cross_id"] != old["cross_id"]
    assert len(broker.calls) == 4


def test_old_window_or_confirmation_cannot_revive_used_cross(tmp_path):
    _, _, _, bars, marks, cross = observed(tmp_path)
    params = parameters(buy_window_enabled=True, buy_confirmation_enabled=True)
    context = rule_context(bars, marks, {**cross, "state": "SESSION_USED"})
    rule = StaticRule(params)
    decision = rule.evaluate(context, portfolio())
    saved = {"session_cross_id": cross["cross_id"], "window": {"date": NOW.date().isoformat(),
             "start": params.buy_window_start, "end": "14:45", "released": False,
             "signal_time": CROSS.isoformat()}, "confirmation": {"active": True}}
    next_state, approved = apply_buy_filters(rule, decision, context, portfolio(), saved,
                                             observed_at=NOW, exchange="HOSE")
    assert next_state == {} and approved.action == "WAIT"
    assert rule.evaluate({**context, "confirmed_buy": True}, portfolio()).action == "WAIT"


def test_confirmation_starts_now_and_is_not_backdated_to_historical_cross(tmp_path):
    _, _, _, bars, marks, cross = observed(tmp_path)
    rule = StaticRule(parameters(buy_confirmation_enabled=True))
    context = rule_context(bars, marks, cross)
    state, decision = apply_buy_filters(rule, rule.evaluate(context, portfolio()), context, portfolio(), {},
                                       observed_at=NOW, exchange="HOSE")
    assert decision.action == "WAIT" and decision.reason == "BUY_CONFIRMATION_WAIT"
    assert decision.details["buy_confirmation"]["minutes_held"] == 0
    assert state["session_cross_id"] == cross["cross_id"]


def test_confirmed_session_does_not_restart_timer_while_waiting_for_capital(tmp_path):
    _, _, _, bars, marks, cross = observed(tmp_path)
    rule = StaticRule(parameters(buy_confirmation_enabled=True))
    context = {**rule_context(bars, marks, cross), "max_observation_gap_seconds": 20}
    saved = {}
    for seconds in range(0, 301, 15):
        blocked_portfolio = portfolio(available_capital=0)
        saved, decision = apply_buy_filters(rule, rule.evaluate(context, blocked_portfolio), context,
                                            blocked_portfolio, saved, observed_at=NOW + timedelta(seconds=seconds),
                                            exchange="HOSE")
    assert decision.action == "WAIT" and decision.reason == "NO_AVAILABLE_CAPITAL"
    assert decision.details["buy_confirmation"]["state"] == "CONFIRMED"
    _, approved = apply_buy_filters(rule, rule.evaluate(context, portfolio()), context, portfolio(), saved,
                                    observed_at=NOW + timedelta(seconds=315), exchange="HOSE")
    assert approved.action == "BUY" and approved.details["buy_confirmation"]["minutes_held"] == 5


def test_cross_expires_after_close_and_old_day_cannot_be_claimed(tmp_path, clock):
    service, state, broker, bars, marks, cross = observed(tmp_path)
    closed = service.observe("TEST", "REAL", bars, marks, parameters(), NOW.replace(hour=15), "HOSE")
    assert closed["state"] == "EXPIRED" and not closed["valid"]
    state.save_session_cross("TEST", "REAL", cross)
    assert not state.claim_session_cross("TEST", "REAL", cross["cross_id"], now=(NOW + timedelta(days=1)).timestamp())


def test_switch_roundtrip_and_preview_are_small_and_explicit(ui_root, monkeypatch, tmp_path):
    from viking_v2 import config
    from viking_v2.rules.window import RuleSettingsPopup
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    settings = AppSettings()
    assert not StaticRuleParameters().buy_signal_session_cross_enabled
    popup = RuleSettingsPopup(ui_root, settings, "SESSION_UI", lambda: None)
    try:
        popup.buy_signal_session_cross_enabled.set(True)
        popup.save()
        assert "ĐÃ LƯU" in popup.status.cget("text")
        saved = config.load_settings("SESSION_UI")
        assert StaticRuleParameters.from_dict(saved.rule_parameters).buy_signal_session_cross_enabled
    finally:
        popup._close()
    for state, text in (("SESSION_ACTIVE", "CÒN HIỆU LỰC 14:10"), ("SESSION_USED", "ĐÃ DÙNG 14:10")):
        caption, _ = ema_cross_caption({"state": state, "session": True, "required": True,
                                       "cross_at": CROSS.isoformat()},
                                      {"state": "WAITING", "ema_cross": {"crossed_up": True}})
        assert text in caption


def test_replay_retains_cross_until_used_or_down_and_resets_on_new_day():
    above = {"buy_ema_fast": 101, "buy_ema_slow": 100}
    below = {"buy_ema_fast": 99, "buy_ema_slow": 100}
    args = dict(day=NOW.date().isoformat(), observed_at=NOW.isoformat(), pair="3/6", expires_at=NOW.timestamp()+1500)
    first = advance_session_cross({}, above, below, **args)
    retained = advance_session_cross(first, above, above, **args)
    assert retained["state"] == "SESSION_ACTIVE" and retained["cross_id"] == first["cross_id"]
    assert advance_session_cross({**retained, "used": True}, above, above, **args)["state"] == "SESSION_USED"
    assert not advance_session_cross(retained, below, above, **args)["valid"]
    assert not advance_session_cross(retained, above, above, **{**args, "day": "2026-10-12"})["valid"]


@pytest.mark.parametrize("enabled", [False, True])
def test_backtest_ui_syncs_same_session_option(ui_root, monkeypatch, tmp_path, enabled):
    from viking_v2 import config
    from viking_v2.backtest.window import BacktestPopup
    monkeypatch.setattr(config, "RUNTIME_ROOT", tmp_path / "runtime")
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    settings = AppSettings(rule_parameters={"buy_signal_session_cross_enabled": enabled})
    popup = BacktestPopup(ui_root, settings, None)
    try:
        monkeypatch.setattr(popup.data, "save_settings", lambda _: None)
        popup._sync_from_bot()
        assert popup.buy_signal_session_cross_enabled.get() is enabled
        assert popup._collect().rule_parameters["buy_signal_session_cross_enabled"] is enabled
    finally:
        popup.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_real_replay_accepts_later_rsi_only_if_session_option_is_enabled(tmp_path, monkeypatch, enabled):
    from viking_v2.backtest.data import HistoricalDataStore
    from viking_v2.backtest.replay import ReplayDataStore
    from viking_v2.backtest.models import BacktestConfig
    from viking_v2.backtest.engine import BacktestEngine
    import viking_v2.backtest.engine as engine_module
    original = engine_module.indicator_snapshot
    def indicator(rows, *args, **kwargs):
        result = original(rows, *args, **kwargs)
        # Isolate the EMA recovery rule from the RSI formula: RSI qualifies
        # only after the one observation where EMA first crosses up.
        intraday_close = float(rows[-1]["close"]) if rows else 0
        result.update(rsi=60 if intraday_close >= 10 else 55, rsi_previous=55)
        return result
    monkeypatch.setattr(engine_module, "indicator_snapshot", indicator)
    start = NOW.replace(hour=0, minute=0) - timedelta(days=220)
    stamps = [(start + timedelta(days=i)).timestamp() for i in range(221)]
    payload = {"t": stamps, "o": [8]*221, "h": [8]*221, "l": [8]*221, "c": [8]*221, "v": [100]*221}
    store = HistoricalDataStore(root=tmp_path / "data", fetcher=lambda _s, r, *_: payload if r == "1D" else None)
    replay = ReplayDataStore(store.root / "replay")
    source = tmp_path / "HOSE_DLY_FPT, 1.csv"
    source.write_text("time,open,high,low,close,Volume\n"
                      "2026-10-09T02:15:00Z,8,8,8,8,100\n"
                      "2026-10-09T07:09:00Z,8,9,8,9,100\n"
                      "2026-10-09T07:20:00Z,9,10,9,10,100\n"
                      "2026-10-09T07:21:00Z,10,10,10,10,100\n"
                      "2026-10-09T07:45:00Z,10,10,10,10,100\n", encoding="utf-8")
    replay.import_file(source, price_scale=1)
    result = BacktestEngine(store, replay).run(BacktestConfig(
        ["FPT"], NOW.date().isoformat(), NOW.date().isoformat(), initial_capital=100_000_000,
        fixed_market_phase="UPTREND", fixed_exposure_pct=100, simulation_mode="REPLAY", fill_session="CONTINUOUS",
        rule_parameters={"buy_window_enabled": False, "buy_ema_fast": 2, "buy_ema_slow": 3,
                         "rsi_period": 2, "whipsaw_enabled": False, "max_positions": 1,
                         "buy_signal_session_cross_enabled": enabled}), save=False)
    buys = [event for event in result.events if event.side == "BUY"]
    assert len(buys) == int(enabled)
    if enabled:
        assert datetime.fromisoformat(buys[0].fill_time).strftime("%H:%M") == "14:21"


def test_range_subtraction_does_not_request_covered_minutes():
    assert missing_ranges([[0, 100]], [[0, 30], [40, 60], [60, 90]]) == [[30, 40], [90, 100]]
