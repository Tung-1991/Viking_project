"""Opt-in scale-in policy: isolated books, durable entry IDs, no broker IO."""
from copy import deepcopy
from types import SimpleNamespace
import time

import pytest

from viking_v2 import config
from viking_v2.backtest.models import BacktestConfig
from viking_v2.connections.window import ConnectionPopup
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.connections.dnse.paper import PaperBroker
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.entry_filters import apply_buy_filters
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.signal_coordinator import BuySlotAllocator, BuyAttempt, coordinate_buy_decisions
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder
from viking_v2.trading.state import TradeStateStore


def builder(tmp_path):
    return PortfolioContextBuilder(OrderQueue(tmp_path / "orders.json"),
                                   TradeStateStore(tmp_path / "trades.json"),
                                   RuleStateStore(tmp_path / "rules.json"), lambda: 0.00045)


def context(b, *, maximum=2, mode="PAPER", positions=None, **overrides):
    values = dict(execution_mode=mode, balance={"equity": 60_000_000, "availableCash": 60_000_000},
                  positions=positions or [], tick={"symbol": "MSN", "price": 70, "ceiling_price": 75},
                  exposure=1, max_positions=4, priority_symbols=["MSN", "CTS", "HDB", "IDC"],
                  priority_capital_enabled=True, priority_total_capital=60_000_000,
                  priority_allocations={symbol: {"limit_vnd": 15_000_000, "use_pct": 50,
                                                "max_orders": maximum} for symbol in ["MSN", "CTS", "HDB", "IDC"]},
                  no_compound_enabled=False)
    values.update(overrides)
    return b.build("MSN", **values)


def holding(quantity=100, price=70):
    return {"symbol": "MSN", "openQuantity": quantity, "tradeQuantity": 0,
            "costPrice": price, "marketPrice": price}


@pytest.mark.parametrize("value", [0, -1, 1.5, 101, "bad", float("inf"), None])
def test_invalid_persisted_maximum_fails_closed(value):
    rows = config.normalize_priority_allocations({"MSN": {"limit_vnd": 15e6, "use_pct": 50, "max_orders": value}}, ["MSN"])
    assert rows["MSN"]["max_orders"] == 1
    assert config.priority_buy_limit(rows["MSN"]) == 7_500_000


def test_old_settings_default_one_and_new_setting_round_trips():
    raw = {"watchlist": ["MSN"], "priority_symbols": ["MSN"],
           "priority_allocations": {"MSN": {"limit_vnd": 15e6, "use_pct": 50}}}
    settings = config.AppSettings.from_dict(raw)
    assert settings.priority_allocations["MSN"]["max_orders"] == 1
    settings.priority_allocations["MSN"]["max_orders"] = 2
    assert config.AppSettings.from_dict(settings.to_dict()).priority_allocations["MSN"]["max_orders"] == 2


@pytest.mark.parametrize("maximum,total", [(1, 7.5e6), (2, 15e6), (3, 15e6)])
def test_per_entry_cap_and_total_envelope_are_distinct(tmp_path, maximum, total):
    result = context(builder(tmp_path), maximum=maximum)
    assert result["order_budget"] == pytest.approx(7.5e6 / 1.00045)
    assert result["priority_capital"]["buy_limit_vnd"] == total
    assert result["priority_capital"]["per_order_limit_vnd"] == 7.5e6
    assert result["entry_orders_used"] == 0


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_next_entry_uses_existing_slot_and_only_own_book(tmp_path, mode):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", mode)
    b.trades.record_buy_fill(cycle.id, 100, 70, order_id="first")
    result = context(b, mode=mode, positions=[holding()])
    assert result["scale_in_allowed"] and result["entry_slot_available"]
    assert result["entry_orders_used"] == 1
    assert result["order_budget"] == pytest.approx(7.5e6 / 1.00045)
    other = context(b, mode="PAPER" if mode == "REAL" else "REAL")
    assert other["entry_orders_used"] == 0 and not other["scale_in_allowed"]
    assert not context(b, mode=mode, maximum=1, positions=[holding()])["entry_slot_available"]


def test_partial_fill_counts_once_unknown_pending_reserves_second(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 20, order_id="first")
    first = OrderIntent.create("MSN", "BUY", 200, "LO", limit_price=20, trade_id=cycle.id, source="BOT")
    first.id = "first"
    b.queue.add(first)
    b.queue._update(first.id, status="PARTIAL", filled_quantity=100, remaining_quantity=100)
    assert context(b, positions=[holding(100, 20)])["entry_orders_used"] == 1
    assert not context(b, positions=[holding(100, 20)])["scale_in_allowed"]
    b.queue._update(first.id, status="CANCELLED", remaining_quantity=0)
    second = b.queue.add(OrderIntent.create("MSN", "BUY", 100, "LO", limit_price=20, trade_id=cycle.id, source="BOT"))
    b.queue._update(second.id, status="UNKNOWN")
    assert context(b, positions=[holding(100, 20)])["entry_orders_used"] == 2
    assert not context(b, positions=[holding(100, 20)])["entry_orders_available"]
    b.queue._update(second.id, status="CANCELLED")
    assert context(b, positions=[holding(100, 20)])["entry_orders_used"] == 1


def test_ids_survive_restart_and_legacy_first_fill_does_not_disappear(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 20)  # legacy position, no ID
    assert context(b, positions=[holding(100, 20)])["entry_orders_used"] == 1
    b.trades.record_buy_fill(cycle.id, 100, 20, order_id="second")
    b.trades = TradeStateStore(tmp_path / "trades.json")
    result = context(b, positions=[holding(200, 20)])
    assert result["entry_orders_used"] == 2 and not result["scale_in_allowed"]
    b.trades.record_sell_fill(cycle.id, 200, 20)
    assert context(b)["entry_orders_used"] == 0


@pytest.mark.parametrize("source,exiting,partial", [("MANUAL", False, False), ("BOT", True, False), ("BOT", False, True)])
def test_no_addition_to_manual_or_exiting_position(tmp_path, source, exiting, partial):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER", source=source)
    b.trades.record_buy_fill(cycle.id, 200, 20, order_id="first")
    if exiting:
        b.queue.add(OrderIntent.create("MSN", "SELL", 100, "LO", limit_price=20, trade_id=cycle.id))
    if partial:
        b.trades.record_sell_fill(cycle.id, 100, 20)
    assert not context(b, positions=[holding(100, 20)])["scale_in_allowed"]


def test_second_buy_cannot_exceed_remaining_envelope_or_phase_one(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 80, order_id="first")
    result = context(b, positions=[holding(100, 80)])
    assert result["order_budget"] == pytest.approx((15e6 - 8e6 * 1.00045) / 1.00045)
    assert context(b, positions=[holding(100, 80)], exposure=0.1)["order_budget"] == 0


def test_actual_fee_is_not_erased_by_reducing_configured_rate(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 80, 80_000, order_id="first")
    result = context(b, positions=[holding(100, 80)])
    assert result["priority_capital"]["committed_vnd"] == pytest.approx(8_080_000)
    assert result["order_budget"] == pytest.approx((15e6 - 8_080_000) / 1.00045)


def test_missing_or_incomplete_broker_holdings_never_allow_scale_in(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 200, 20, order_id="first")
    for positions in ([], [holding(100, 20)]):
        result = context(b, positions=positions)
        assert not result["scale_in_allowed"] and not result["entry_slot_available"]


def test_manual_buy_does_not_consume_bot_entry_counter(tmp_path):
    from viking_v2.models import BrokerOrderResult
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 20, order_id="bot-first")
    manual = OrderIntent.create("MSN", "BUY", 100, "MARKET", source="MANUAL", trade_id=cycle.id)
    b.queue.add(manual)
    before = context(b, positions=[holding(100, 20)])
    assert before["entry_orders_used"] == 1 and not before["scale_in_allowed"]
    service = ExecutionService(SimpleNamespace(), SimpleNamespace(), b.queue,
                               JSONLineJournal(tmp_path / "manual.jsonl"), trade_state=b.trades)
    service._record_trade_fill(manual, BrokerOrderResult(True, "FILLED",
        raw={"price_unit": "BOARD", "fillQuantity": 100, "averagePrice": 20}), 100)
    b.queue._update(manual.id, status="FILLED", filled_quantity=100, remaining_quantity=0)
    after = context(b, positions=[holding(200, 20)])
    assert after["entry_orders_used"] == 1 and after["scale_in_allowed"]
    assert after["priority_capital"]["committed_vnd"] == pytest.approx(4e6 * 1.00045)


def test_final_guard_rechecks_changed_maximum_cap_and_snapshot(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 70, order_id="first")
    rows = [holding()]
    broker = SimpleNamespace(get_balance=lambda **_kwargs: {"equity": 60e6, "availableCash": 53e6},
                             get_positions=lambda **_kwargs: rows)
    app = DashboardActionsMixin()
    app.paper, app.real = broker, SimpleNamespace(get_secdef=lambda _symbol: {"ceilingPrice": 74})
    app.queue, app.trade_state, app.rule_state = b.queue, b.trades, b.rule_state
    app.settings = config.AppSettings.from_dict({"watchlist": ["MSN"], "priority_symbols": ["MSN"],
        "priority_capital_enabled": True, "priority_total_capital": 15e6,
        "market_phase_override_enabled": True, "market_phase_override_exposure_pct": 100,
        "rule_parameters": {"no_compound_enabled": False},
        "priority_allocations": {"MSN": {"limit_vnd": 15e6, "use_pct": 50, "max_orders": 2}}})
    second = b.queue.add(OrderIntent.create("MSN", "BUY", 100, "MARKET", source="BOT", trade_id=cycle.id))
    assert app._check_bot_entry_limits(second, {"price": 70}) == ""
    app.settings.priority_allocations["MSN"]["max_orders"] = 1
    assert app._check_bot_entry_limits(second, {"price": 70}) == "MAX_SYMBOL_ORDERS"
    app.settings.priority_allocations["MSN"]["max_orders"] = 2
    app.settings.priority_allocations["MSN"]["limit_vnd"] = 1e6
    assert app._check_bot_entry_limits(second, {"price": 70}) == "PRIORITY_CAPITAL_LIMIT"
    app.settings.priority_allocations["MSN"]["limit_vnd"] = 15e6
    rows.clear()
    assert app._check_bot_entry_limits(second, {"price": 70}) == "POSITION_NOT_READY_FOR_ADD"


def test_allocator_scale_in_failure_never_releases_existing_symbol():
    allocator = BuySlotAllocator(4, ["MSN", "CTS", "HDB", "IDC"], ["MSN", "CTS", "HDB", "IDC"], ["MSN"])
    outcomes = coordinate_buy_decisions({"MSN": StrategyDecision("BUY", "MSN", "BUY_SIGNAL", signal="BUY")},
                                        ["MSN"], allocator, bot_enabled=True,
                                        plan=lambda _candidate: BuyAttempt(reason="NO_PRICE"))
    assert outcomes[0].blocked_by == "NO_PRICE"
    assert allocator.used == 4 and "MSN" in allocator.occupied_symbols
    assert allocator.reserve("MSN")
    assert not allocator.reserve("MSN")


def buy_context():
    # A fresh EMA/RSI transition known to produce a BUY in the existing suite.
    from support.tests.test_static_business_rule import _bars, M_VALUES
    return {"symbol": "MSN", "bars": _bars(M_VALUES), "previous_market_state": "UPTREND"}


def test_new_signal_can_add_but_exits_and_maximum_win(tmp_path):
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER", em_modes=["NORMAL"])
    b.trades.record_buy_fill(cycle.id, 100, 70, order_id="first")
    portfolio = context(b, positions=[holding()])
    rule = StaticRule()
    decision = rule.evaluate(buy_context(), portfolio)
    assert decision.action == "BUY" and decision.scope == "ENTRY"
    stop = deepcopy(portfolio)
    stop["position"]["current_price"] = 65
    assert rule.evaluate(buy_context(), stop).event == "STOP_LOSS"
    portfolio["entry_orders_available"], portfolio["scale_in_allowed"] = False, False
    assert rule.evaluate(buy_context(), portfolio).reason == "MAX_SYMBOL_ORDERS"


def test_scale_in_still_obeys_buy_confirmation(tmp_path):
    from datetime import datetime
    from viking_v2.trading.market import VN_TZ
    rule = StaticRule(StaticRuleParameters.from_dict({"buy_confirmation_enabled": True, "buy_confirmation_minutes": 5,
                                                    "buy_signal_session_cross_enabled": False}))
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 70, order_id="first")
    portfolio = context(b, positions=[holding()])
    source = {**buy_context(), "signal_mode": "REALTIME", "exchange": "HOSE"}
    decision = rule.evaluate(source, portfolio)
    _state, filtered = apply_buy_filters(rule, decision, source, portfolio, {},
        observed_at=datetime(2026, 10, 8, 14, 0, tzinfo=VN_TZ), exchange="HOSE")
    assert filtered.reason == "BUY_CONFIRMATION_WAIT"


def test_paper_planner_execution_two_entries_then_block_and_restart(tmp_path):
    b = builder(tmp_path)
    quote = {"symbol": "MSN", "price": 70, "ask": 70, "ceiling_price": 74, "timestamp": time.time()}
    paper = PaperBroker(tmp_path / "paper.json", initial_balance=60e6,
                        tick_provider=lambda _symbol: quote, fee_rates=lambda: (0.00045, 0.00045, 0.001))
    settings = config.AppSettings.from_dict({"priority_symbols": ["MSN", "CTS", "HDB", "IDC"],
        "watchlist": ["MSN", "CTS", "HDB", "IDC"], "priority_capital_enabled": True,
        "priority_total_capital": 60e6, "market_phase_override_enabled": True, "market_phase_override_exposure_pct": 100,
        "rule_parameters": {"max_positions": 4, "no_compound_enabled": False},
        "priority_allocations": {symbol: {"limit_vnd": 15e6, "use_pct": 50, "max_orders": 2}
                                 for symbol in ["MSN", "CTS", "HDB", "IDC"]}})
    app = DashboardActionsMixin()
    app.settings, app.queue, app.trade_state, app.rule_state = settings, b.queue, b.trades, b.rule_state
    app.paper, app.real = paper, SimpleNamespace(get_secdef=lambda _symbol: {"ceilingPrice": 74})
    planner = StrategyOrderPlanner(b.queue, b.trades, b.rule_state)
    service = ExecutionService(paper, paper, b.queue, JSONLineJournal(tmp_path / "journal.jsonl"),
        trade_state=b.trades, rule_state=b.rule_state, quote_provider=lambda _symbol: quote,
        bot_entry_guard=app._check_bot_entry_limits)
    ids = []
    for number in (1, 2):
        portfolio = context(b, positions=paper.get_positions(), balance=paper.get_balance(), tick=quote)
        decision = StaticRule().evaluate(buy_context(), portfolio)
        decision.details["trade_id"] = portfolio.get("trade_id", "")
        assert decision.action == "BUY"
        planned = planner.plan(decision, execution_mode="PAPER", execution_style="MARKET", tick=quote,
                               portfolio=portfolio, candle_key=f"signal-{number}")
        assert planned.intent and planned.intent.quantity == 100
        # A single candle cannot produce an extra entry while its first is pending.
        assert planner.plan(decision, execution_mode="PAPER", execution_style="MARKET", tick=quote,
                            portfolio=portfolio, candle_key=f"signal-{number}").reason == "BUY_ALREADY_PENDING"
        service.process_due(phase="OPEN", execution_mode="PAPER")
        assert b.queue.get(planned.intent.id).status == "FILLED"
        ids.append(planned.intent.id)
        if number == 1:
            updated = context(b, positions=paper.get_positions(), balance=paper.get_balance(), tick=quote)
            repeated = StaticRule().evaluate(buy_context(), updated)
            assert planner.plan(repeated, execution_mode="PAPER", execution_style="MARKET", tick=quote,
                                portfolio=updated, candle_key="signal-1").reason == "BUY_SIGNAL_ALREADY_PROCESSED"
    cycle = b.trades.active_for("MSN", "PAPER")
    assert cycle.open_quantity == 200 and cycle.entry_order_ids == ids
    assert cycle.buy_notional + cycle.fees_paid <= 15e6
    assert len(b.trades.list_cycles()) == 1
    b.trades = TradeStateStore(tmp_path / "trades.json")
    final = context(b, positions=paper.get_positions(), balance=paper.get_balance())
    assert final["entry_orders_used"] == 2
    assert StaticRule().evaluate(buy_context(), final).reason == "MAX_SYMBOL_ORDERS"


def test_protect_cost_basis_rebase_keeps_absolute_trigger(tmp_path):
    state = builder(tmp_path).rule_state
    state.update_position_metrics("MSN", "T", profit_pct=10, market_price=77, entry_avg_price=70)
    state.update_protect_metrics("MSN", "T", trigger_price=75, atr_pct=2, atr_multiplier=0.8)
    result = state.update_position_metrics("MSN", "T", profit_pct=2, market_price=76.5, entry_avg_price=75)
    assert result["peak_profit_pct"] == pytest.approx((77 / 75 - 1) * 100)
    assert result["normal_trigger_price"] == 75


def test_backtest_refuses_silently_ignoring_multiple_entries():
    with pytest.raises(ValueError, match="MAX LỆNH > 1"):
        BacktestConfig(["MSN"], "2026-10-01", "2026-10-08", priority_symbols=["MSN"],
            priority_capital_enabled=True, priority_total_capital=15e6,
            priority_allocations={"MSN": {"limit_vnd": 15e6, "use_pct": 50, "max_orders": 2}})


@pytest.mark.parametrize("maximum", ["0", "1.5", "101", "nan"])
def test_ui_rejects_invalid_maximum(maximum):
    with pytest.raises(ValueError):
        ConnectionPopup._priority_allocation_inputs("15", "50", maximum)


def test_telegram_batch_retains_both_entries_of_same_symbol():
    messages = []
    client = SimpleNamespace(send_message=lambda chat, text: messages.append(text))
    service = SignalTelegramService(client, chat_id="offline", buy_batch_minutes=30)
    try:
        for suffix in ("one", "two"):
            service.notify_buy(symbol="MSN", signal_id=f"cycle/{suffix}", price=70, market_state="UPTREND", execution_mode="PAPER")
        assert service.flush_buys()
        assert "2 LỆNH · 1 MÃ" in messages[0]
        assert "CYCLE/ONE" in messages[0] and "CYCLE/TWO" in messages[0]
    finally:
        service.cancel_pending_buys()


def test_telegram_trade_records_are_book_specific_and_migrate_exact_legacy_id(tmp_path):
    state = builder(tmp_path).rule_state
    state.open_telegram_signal("MSN", "old", price=70, market_state="UPTREND", signal_id="R1")
    assert state.open_telegram_signal("MSN", "next", price=71, market_state="UPTREND", signal_id="R1", stream="REAL") is None
    state.open_telegram_signal("MSN", "same", price=70, market_state="UPTREND", signal_id="P1", stream="PAPER")
    assert state.active_telegram_signal("MSN", "REAL")["id"] == "R1"
    assert state.active_telegram_signal("MSN", "PAPER")["id"] == "P1"
    assert state.claim_closed_telegram_signal("MSN", "R1", "PAPER") is None
    assert state.claim_closed_telegram_signal("MSN", "P1", "PAPER")
    assert state.active_telegram_signal("MSN", "REAL")["id"] == "R1"


def test_additional_buy_notification_keeps_position_id_and_dedupes(tmp_path, monkeypatch):
    import viking_v2.dashboard.actions as module
    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs
        def start(self):
            self.target(**self.kwargs)
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)
    b = builder(tmp_path)
    app = DashboardActionsMixin()
    sent = []
    app.telegram = SimpleNamespace(notify_buy=lambda **kwargs: sent.append(kwargs))
    app.rule_state, app.trade_state = b.rule_state, b.trades
    app.settings = config.AppSettings.from_dict({"telegram_notifications": {"buy_queued": True}, "telegram_cooldown_minutes": {}})
    decision = StrategyDecision("BUY", "MSN", "BUY_SIGNAL", signal="BUY", market_state="UPTREND",
                                details={"candle_key": "ONE", "entry_checks": {"scale_in_allowed": False}})
    app._notify_rule_signal("MSN", decision, {"price": 70}, signal_id="T", entry_id="one", execution_mode="PAPER")
    decision.details = {"candle_key": "TWO", "entry_checks": {"scale_in_allowed": True}}
    for _attempt in range(2):
        app._notify_rule_signal("MSN", decision, {"price": 71}, signal_id="T", entry_id="two", execution_mode="PAPER")
    assert [item["signal_id"] for item in sent] == ["T/one", "T/two"]
    assert b.rule_state.active_telegram_signal("MSN", "PAPER")["id"] == "T"


def test_failed_additional_buy_keeps_existing_closed_notification_record(tmp_path):
    from viking_v2.models import BrokerOrderResult
    b = builder(tmp_path)
    cycle = b.trades.create("MSN", "PAPER")
    b.trades.record_buy_fill(cycle.id, 100, 70, order_id="first")
    b.rule_state.open_telegram_signal("MSN", "ONE", price=70, market_state="UPTREND", signal_id=cycle.id, stream="PAPER")
    app = DashboardActionsMixin()
    app.rule_state, app.trade_state = b.rule_state, b.trades
    app.queue, app.settings = b.queue, config.AppSettings()
    app.snapshots = {"PAPER": ({}, [holding()], [])}
    app._slot_sources = lambda *_args: ({"MSN"}, set())
    app._shared_tick = lambda _symbol: {"price": 70}
    app._record_signal_decision = app._notify_rule_signal = lambda *_args, **_kwargs: None
    intent = OrderIntent.create("MSN", "BUY", 100, "MARKET", trade_id=cycle.id, source="BOT")
    app._record_failed_buy_execution("PAPER", intent, BrokerOrderResult(False, "REJECTED"))
    assert b.rule_state.claim_closed_telegram_signal("MSN", cycle.id, "PAPER")


@pytest.mark.parametrize("reason,waiting", [("CHỜ GIÁ TRẦN TÍNH KL", True), ("HẠN MỨC CHƯA ĐỦ 100 CP", False)])
@pytest.mark.parametrize("skip", [True, False])
def test_manual_zero_quantity_reports_actual_preview_reason(monkeypatch, reason, waiting, skip):
    import viking_v2.dashboard.actions as module
    view = DashboardActionsMixin()
    field = lambda value: SimpleNamespace(get=lambda: value)
    view.symbol, view.order_type, view.quantity = field("MSN"), field("MARKET"), field("")
    view.mode = field("PAPER")
    view.settings = config.AppSettings(skip_order_popups=skip)
    logs = []
    view._log = lambda *args: logs.append(args)
    view._current_tick_price = 70
    view._suggested_order_quantity = lambda _price: (0, 0, False)
    view._preview_auto_feedback = {"reason": reason, "waiting": waiting, "hint": reason + "\nChi tiết đúng sổ"}
    errors = []
    monkeypatch.setattr(module.messagebox, "showerror", lambda _title, text, **_kwargs: errors.append(text))
    view._submit("BUY")
    assert errors == ([] if skip else [view._preview_auto_feedback["hint"]])
    assert view._manual_order_notice["hint"] == view._preview_auto_feedback["hint"]
    assert len(logs) == 1 and reason in logs[0][0] and logs[0][1] == "manual"
