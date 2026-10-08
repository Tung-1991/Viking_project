"""Offline coverage for the two opt-in policies; no broker credentials needed."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from viking_v2 import config
from viking_v2.backtest.data import HistoricalDataStore, VN_TZ
from viking_v2.backtest.engine import BacktestEngine
from viking_v2.backtest.models import BacktestConfig
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.models import BrokerOrderResult, OrderIntent, StrategyDecision
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder, size_buy_order
from viking_v2.trading.state import TradeStateStore


PRIORITY = ["FPT", "SSI", "VIX"]
ALLOCATIONS = {symbol: {"limit_vnd": 20_000_000, "use_pct": 50 if symbol == "VIX" else 100}
               for symbol in PRIORITY}


def _builder(tmp_path, fee=0.0):
    return PortfolioContextBuilder(OrderQueue(tmp_path / "orders.json"),
                                   TradeStateStore(tmp_path / "trades.json"),
                                   RuleStateStore(tmp_path / "rules.json"), lambda: fee)


def _context(builder, symbol="FPT", **overrides):
    values = dict(execution_mode="PAPER", balance={"equity": 60_000_000, "availableCash": 60_000_000},
                  positions=[], tick={"ask": 20, "ceiling_price": 21.4}, exposure=1.0, max_positions=5,
                  priority_symbols=PRIORITY, priority_capital_enabled=True,
                  priority_total_capital=60_000_000, priority_allocations=ALLOCATIONS,
                  no_compound_enabled=False)
    values.update(overrides)
    return builder.build(symbol, **values)


def _losses(trades, symbol="FPT", mode="REAL", count=3):
    for _index in range(count):
        cycle = trades.create(symbol, mode)
        trades.record_buy_fill(cycle.id, 100, 20)
        trades.record_sell_fill(cycle.id, 100, 19, closed_at=1_700_000_000)


def test_block_is_latched_at_fill_and_survives_restart_reset_and_policy_change(tmp_path):
    path = tmp_path / "state.json"
    trades = TradeStateStore(path, loss_lock_policy=lambda: (3, "BLOCK"))
    _losses(trades)
    assert trades.loss_blocks("REAL") == ["FPT"]
    restarted = TradeStateStore(path)
    assert restarted.is_loss_locked("FPT", "REAL", lock_mode="TIMED", now=1_900_000_000)
    assert restarted.clear_loss_cooldowns("REAL") == 0
    assert not restarted.is_loss_locked("FPT", "PAPER")
    assert not restarted.is_loss_locked("SSI", "REAL")
    # A manual WIN must not implicitly unlock a permanent latch.
    cycle = restarted.create("FPT", "REAL", source="MANUAL")
    restarted.record_buy_fill(cycle.id, 100, 20)
    restarted.record_sell_fill(cycle.id, 100, 21)
    assert restarted.is_loss_locked("FPT", "REAL")
    assert restarted.unlock_loss_block("FPT", "REAL")
    assert not TradeStateStore(path).is_loss_locked("FPT", "REAL", lock_mode="BLOCK")
    assert not restarted.unlock_loss_block("FPT", "REAL")


def test_block_migrates_existing_timed_streak_without_expiring_it(tmp_path):
    trades = TradeStateStore(tmp_path / "trades.json")
    _losses(trades)
    assert trades.active_loss_streak("FPT", "REAL", lock_mode="BLOCK", now=1_900_000_000) == 3
    assert trades.loss_blocks("REAL") == ["FPT"]


@pytest.mark.parametrize("symbol,expected", [("FPT", 20_000_000), ("SSI", 20_000_000),
                                            ("VIX", 10_000_000), ("MBB", 0)])
def test_priority_envelopes_override_slot_division_and_reserve_savings(tmp_path, symbol, expected):
    context = _context(_builder(tmp_path), symbol)
    assert context["order_budget"] == expected
    assert context["minimum_order_room"] == expected


def test_priority_off_preserves_original_slot_budget(tmp_path):
    assert _context(_builder(tmp_path), priority_capital_enabled=False)["order_budget"] == 12_000_000


def test_default_priority_reserves_money_even_without_private_pool(tmp_path):
    builder = _builder(tmp_path)
    values = dict(priority_capital_enabled=False, priority_symbols=["FPT"],
                  balance={"equity": 1_000_000_000, "availableCash": 100_000_000},
                  exposure=0.9, max_positions=5)
    normal = _context(builder, "MBB", **values)
    priority = _context(builder, "FPT", **values)
    assert normal["order_budget"] == 0
    assert normal["priority_capital"]["reserved_cash"] == 180_000_000
    assert priority["order_budget"] == 100_000_000
    assert normal["priority_reserved"] == 1


def test_va_four_caps_protect_savings_and_do_not_lend_them_to_idc(tmp_path):
    symbols = ["MSN", "CTS", "HDB", "IDC"]
    allocations = {value: {"limit_vnd": (5 if value == "HDB" else 15) * 1_000_000, "use_pct": 50.0}
                   for value in symbols}
    for symbol, expected in zip(symbols, [7_500_000, 7_500_000, 2_500_000, 7_500_000]):
        result = _context(_builder(tmp_path), symbol, priority_symbols=symbols,
                          priority_allocations=allocations, priority_total_capital=50_000_000,
                          balance={"equity": 50_000_000, "availableCash": 50_000_000})
        assert result["order_budget"] == expected
    assert _context(_builder(tmp_path), "MBB", priority_symbols=symbols,
                    priority_allocations=allocations, priority_total_capital=50_000_000,
                    balance={"equity": 50_000_000, "availableCash": 50_000_000})["order_budget"] == 0


def test_regular_symbols_only_spend_money_outside_priority_pool(tmp_path):
    context = _context(_builder(tmp_path), "MBB", balance={"equity": 100_000_000, "availableCash": 100_000_000})
    assert context["order_budget"] == 20_000_000
    assert context["priority_capital"]["reserved_cash"] == 60_000_000


def test_unassigned_or_zero_use_never_falls_back_to_old_budget(tmp_path):
    builder = _builder(tmp_path)
    assert _context(builder, "VIX", priority_allocations={"FPT": ALLOCATIONS["FPT"]})["order_budget"] == 0
    rows = {**ALLOCATIONS, "VIX": {"limit_vnd": 20_000_000, "use_pct": 0}}
    assert _context(builder, "VIX", priority_allocations=rows)["order_budget"] == 0
    assert _context(builder, priority_total_capital=0)["order_budget"] == 0
    assert _context(builder, priority_total_capital=30_000_000)["order_budget"] == 0


def test_fees_partial_fills_and_pending_remainders_consume_same_envelope(tmp_path):
    builder = _builder(tmp_path, fee=0.001)
    holding = {"symbol": "VIX", "openQuantity": 100, "tradeQuantity": 0, "costPrice": 20, "marketPrice": 19}
    intent = builder.queue.add(OrderIntent.create("VIX", "BUY", 400, "LO", limit_price=20))
    builder.queue._update(intent.id, status="PARTIAL", filled_quantity=100, remaining_quantity=300,
                          broker_order_id="B1", handed_off_at=1.0)
    context = _context(builder, "VIX", positions=[holding])
    assert context["priority_capital"]["committed_vnd"] == pytest.approx(8_008_000)
    assert context["order_budget"] == pytest.approx((10_000_000 - 8_008_000) / 1.001)
    assert _context(builder, "VIX", positions=[holding], exclude_intent_id=intent.id)["order_budget"] == pytest.approx((10_000_000 - 2_002_000) / 1.001)


def test_local_pending_cash_is_not_double_counted_or_borrowed(tmp_path):
    builder = _builder(tmp_path)
    builder.queue.add(OrderIntent.create("FPT", "BUY", 1000, "LO", limit_price=20, source="BOT"))
    assert _context(builder, "SSI")["order_budget"] == 20_000_000
    assert _context(builder, "VIX")["order_budget"] == 10_000_000
    assert _context(builder, "MBB")["order_budget"] == 0
    assert _context(builder, "FPT")["order_budget"] == 0
    assert _context(builder, "FPT", execution_mode="REAL")["order_budget"] == 20_000_000


def test_reservation_remains_after_all_three_buys_and_after_a_sale(tmp_path):
    builder = _builder(tmp_path)
    holdings = [{"symbol": symbol, "openQuantity": qty, "tradeQuantity": qty, "costPrice": 20, "marketPrice": 20}
                for symbol, qty in [("FPT", 1000), ("SSI", 1000), ("VIX", 500)]]
    context = _context(builder, "MBB", positions=holdings,
                       balance={"equity": 60_000_000, "availableCash": 10_000_000})
    assert context["order_budget"] == 0
    assert context["priority_capital"]["reserved_cash"] == 10_000_000
    # Selling FPT makes its own envelope available, not an MBB BUY job.
    context = _context(builder, "MBB", positions=holdings[1:],
                       balance={"equity": 60_000_000, "availableCash": 30_000_000})
    assert context["order_budget"] == 0
    assert _context(builder, "FPT", positions=holdings[1:],
                    balance={"equity": 60_000_000, "availableCash": 30_000_000})["order_budget"] == 20_000_000


def test_phase_one_and_force_minimum_cannot_break_priority_cap(tmp_path):
    context = _context(_builder(tmp_path), "VIX", exposure=0.1)
    assert context["order_budget"] == 6_000_000
    sizing = size_buy_order(budget_vnd=context["order_budget"], price_board=100,
                           available_cash=context["available_cash"], nav=context["nav"],
                           minimum_order_room_vnd=context["minimum_order_room"], force_min_lot_enabled=True)
    assert sizing.quantity == 0


def test_settings_round_trip_filters_priority_only_and_preserves_defaults():
    settings = config.AppSettings.from_dict({"watchlist": PRIORITY, "priority_symbols": PRIORITY,
        "priority_capital_enabled": True, "priority_total_capital": 60_000_000,
        "priority_allocations": {**ALLOCATIONS, "MBB": {"limit_vnd": 20_000_000, "use_pct": 10}}})
    assert config.AppSettings.from_dict(settings.to_dict()).priority_allocations == ALLOCATIONS
    assert not config.AppSettings.from_dict({}).priority_capital_enabled
    assert config.AppSettings.from_dict({}).rule_parameters["loss_lock_mode"] == "TIMED"
    assert config.AppSettings.from_dict({"priority_total_capital": float("nan")}).priority_total_capital == 0


def test_market_sizing_reserves_ceiling_price_and_lo_sizing_uses_limit(tmp_path):
    builder = _builder(tmp_path)
    planner = StrategyOrderPlanner(builder.queue, builder.trades, builder.rule_state)
    decision = StrategyDecision("BUY", "VIX", "BUY_SIGNAL", signal="BUY",
        details={"entry_checks": {"priority_capital_enabled": True, "buy_budget_price": 21.4}})
    result = planner.plan(decision, execution_mode="PAPER", execution_style="MARKET", tick={"ask": 20},
                          portfolio={"order_budget": 10_000_000}, candle_key="one")
    assert result.intent.quantity == 400
    assert result.intent.details["reservation_price"] == 21.4
    builder.queue.cancel_local(result.intent.id)
    result = planner.plan(decision, execution_mode="PAPER", execution_style="LO_LOCAL", tick={"ask": 20},
                          portfolio={"order_budget": 10_000_000}, candle_key="two")
    assert result.intent.quantity == 500


@pytest.mark.parametrize("order_type,quantity", [("MARKET", 400), ("ATO", 400), ("LO", 500)])
def test_default_preview_matches_priority_sizing(order_type, quantity):
    preview = DashboardPanelsMixin()
    preview.order_type = SimpleNamespace(get=lambda: order_type)
    status = {"decisions": {"VIX": {"details": {"entry_checks": {
        "order_budget": 10_000_000, "minimum_order_room": 10_000_000,
        "available_cash": 60_000_000, "nav": 60_000_000,
        "priority_capital_enabled": True, "buy_budget_price": 21.4,
    }}}}}
    assert preview._suggested_order_quantity(20, status, "VIX")[0] == quantity


@pytest.mark.parametrize("mode,nav", [("REAL", 50_000_000), ("PAPER", 100_000_000)])
@pytest.mark.parametrize("override", [False, True])
def test_ticket_auto_preview_uses_own_book_and_p1_without_any_bot_decision(tmp_path, monkeypatch, mode, nav, override):
    class Ticket(DashboardPanelsMixin, DashboardActionsMixin):
        pass

    builder = _builder(tmp_path)
    ticket = Ticket()
    ticket.settings = config.AppSettings.from_dict({"market_phase_override_enabled": override,
        "market_phase_override_exposure_pct": 100, "priority_symbols": [], "buy_fee_pct": 0.045})
    ticket.mode = SimpleNamespace(get=lambda: mode)
    ticket.symbol = SimpleNamespace(get=lambda: "AAA")
    ticket.order_type = SimpleNamespace(get=lambda: "MARKET")
    ticket.queue, ticket.trade_state, ticket.rule_state = builder.queue, builder.trades, builder.rule_state
    ticket.snapshots = {book: ({"equity": money, "availableCash": money}, [], [])
                        for book, money in [("REAL", 50_000_000), ("PAPER", 100_000_000)]}
    monkeypatch.setattr(ticket.rule_state, "confirmed_market_state", lambda: "UPTREND")
    monkeypatch.setattr(ticket.trade_state, "active_loss_streak",
                        lambda *_args, **_kwargs: pytest.fail("Preview must not alter a cooldown"))
    status = {"decisions_by_mode": {"REAL": {}, "PAPER": {}}, "ticks": {"AAA": {"price": 7.25}}}
    quantity, budget, _forced = ticket._suggested_order_quantity(7.25, status, "AAA")
    exposure = 1.0 if override else 0.9
    assert budget == pytest.approx(nav * exposure / 5)
    assert quantity == int(budget / 7250) // 100 * 100
    assert quantity > 0
    assert ticket._projected_pnl("7%", 7.25, quantity) == pytest.approx(quantity * 7250 * 0.07)
    assert ticket._projected_pnl("-3.5%", 7.25, quantity) == pytest.approx(-quantity * 7250 * 0.035)
    from viking_v2.dashboard.view import _compact_vnd
    def label():
        values = {}
        return SimpleNamespace(options=values, configure=lambda **updates: values.update(updates))
    ticket.quantity = SimpleNamespace(get=lambda: "", configure=lambda **_updates: None)
    ticket.tp, ticket.sl = SimpleNamespace(get=lambda: "7%"), SimpleNamespace(get=lambda: "-3.5%")
    ticket.bridge = SimpleNamespace(read_status=lambda: status)
    ticket._current_tick_price = 7.25
    ticket._refresh_main_quote_display = ticket._refresh_full_order_preview = lambda: None
    ticket._cached_fee_rate = lambda *_args: 0.00045
    for name in ("lbl_order_value", "lbl_quote_symbol", "lbl_fee_preview", "lbl_tp_title", "lbl_sl_title", "lbl_tp_preview", "lbl_sl_preview"):
        setattr(ticket, name, label())
    ticket._update_order_preview()
    assert ticket.lbl_tp_preview.options["text"] == "+" + _compact_vnd(quantity * 7250 * 0.07)
    assert ticket.lbl_sl_preview.options["text"] == "-" + _compact_vnd(quantity * 7250 * 0.035)
    assert ticket.lbl_fee_preview.options["text"] == _compact_vnd(quantity * 7250 * 0.00045)
    assert ticket.queue.list_all() == []


def test_preview_rebuilds_priority_cap_without_stale_or_other_book_budget(tmp_path, monkeypatch):
    class Ticket(DashboardPanelsMixin, DashboardActionsMixin):
        pass

    builder = _builder(tmp_path)
    ticket = Ticket()
    ticket.settings = config.AppSettings.from_dict({"priority_symbols": PRIORITY,
        "priority_capital_enabled": True, "priority_total_capital": 60_000_000,
        "priority_allocations": ALLOCATIONS, "market_phase_override_enabled": True,
        "market_phase_override_exposure_pct": 100})
    ticket.mode = SimpleNamespace(get=lambda: "PAPER")
    ticket.order_type = SimpleNamespace(get=lambda: "MARKET")
    ticket._fee_rates = {("VIX", "BUY"): 0.01}  # REAL fee must not leak into PAPER.
    ticket.queue, ticket.trade_state, ticket.rule_state = builder.queue, builder.trades, builder.rule_state
    ticket.snapshots = {"PAPER": ({"equity": 60_000_000, "availableCash": 60_000_000}, [], [])}
    status = {"ticks": {"VIX": {"ceiling_price": 21.4}}, "decisions_by_mode": {
        "PAPER": {}, "REAL": {"VIX": {"details": {"entry_checks": {"order_budget": 100_000_000}}}}}}
    quantity, budget, _forced = ticket._suggested_order_quantity(20, status, "VIX")
    assert budget == pytest.approx(10_000_000 / 1.00045)
    assert quantity == 400
    ticket.settings.priority_allocations["VIX"]["use_pct"] = 0
    assert ticket._suggested_order_quantity(20, status, "VIX")[0] == 0
    ticket.snapshots["PAPER"] = ({}, [], [])
    assert ticket._suggested_order_quantity(20, status, "VIX")[0] == 0


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_dashboard_rechecks_batch_and_changes_before_submission(tmp_path, mode):
    builder = _builder(tmp_path)
    settings = config.AppSettings.from_dict({
        "watchlist": PRIORITY + ["MBB"], "priority_symbols": PRIORITY,
        "priority_capital_enabled": True, "priority_total_capital": 60_000_000,
        "priority_allocations": ALLOCATIONS, "buy_fee_pct": 0,
        "market_phase_override_enabled": True, "market_phase_override_exposure_pct": 100,
        "rule_parameters": {"force_min_lot_enabled": True, "no_compound_enabled": False},
    })
    class Harness(DashboardActionsMixin):
        def _symbol_exchange(self, symbol): return "HOSE"
    app = Harness()
    app.settings, app.queue, app.trade_state, app.rule_state = settings, builder.queue, builder.trades, builder.rule_state
    app.strategy_planner = StrategyOrderPlanner(app.queue, app.trade_state, app.rule_state)
    balance = {"equity": 60_000_000, "availableCash": 60_000_000}
    app.snapshots = {mode: (balance, [], [])}
    account = SimpleNamespace(get_balance=lambda **kw: balance, get_positions=lambda **kw: [],
                              get_secdef=lambda _symbol: {"ceilingPrice": 21.4})
    app.paper, app.real = account, account
    intents = []
    for symbol in PRIORITY:
        checks = _context(builder, symbol, execution_mode=mode)
        decision = StrategyDecision("BUY", symbol, "BUY_SIGNAL", signal="BUY", market_state="UPTREND",
            details={"order_budget": checks["order_budget"], "exposure": 1, "candle_key": "2026-10-08",
                     "entry_checks": {**checks, "priority_capital_enabled": True, "buy_budget_price": 21.4,
                                      "force_min_lot_enabled": True}})
        planned = app._plan_rule_decision(decision, {"ask": 20}, mode, available_cash=60_000_000)
        assert planned.intent
        intents.append(planned.intent)
    assert [item.quantity for item in intents] == [900, 900, 400]
    assert app._check_bot_entry_limits(intents[-1], {"ask": 20}) == ""
    settings.priority_allocations["VIX"]["use_pct"] = 40
    assert app._check_bot_entry_limits(intents[-1], {"ask": 20}) == "PRIORITY_CAPITAL_LIMIT"
    _losses(app.trade_state, "VIX", mode)
    settings.rule_parameters["loss_lock_mode"] = "BLOCK"
    assert app._check_bot_entry_limits(intents[-1], {"ask": 20}) == "LOCKED_AFTER_LOSSES"
    decision = StrategyDecision("BUY", "MBB", "OLD_BUY_SIGNAL", signal="BUY",
        details={"order_budget": 12_000_000, "exposure": 1,
                 "entry_checks": {"minimum_order_room": 60_000_000, "buy_budget_price": 21.4,
                                  "force_min_lot_enabled": True}})
    blocked = app._plan_rule_decision(decision, {"ask": 20}, mode, available_cash=60_000_000)
    assert blocked.intent is None


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_final_entry_guard_cancels_only_bot_buy_not_manual_or_management(tmp_path, mode):
    class Broker:
        sent = []
        def get_orders(self, **kwargs): return []
        def get_positions(self, **kwargs):
            return [{"symbol": "VIX", "openQuantity": 100, "tradeQuantity": 100}]
        def has_trading_token(self): return True
        def place_order(self, intent):
            self.sent.append(intent)
            return BrokerOrderResult(True, "New", order_id=f"B{len(self.sent)}", raw={"fillQuantity": 0})
    broker = Broker()
    broker.sent = []
    queue = OrderQueue(tmp_path / "orders.json")
    engine = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"),
                              bot_entry_guard=lambda _intent, _quote: "LOCKED_AFTER_LOSSES")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=20, source="BOT", execution_mode=mode))
    engine.process_due(phase="OPEN", execution_mode=mode)
    assert not broker.sent
    assert queue.get(intent.id).status == "CANCELLED"
    manual = queue.add(OrderIntent.create("SSI", "BUY", 100, "LO", limit_price=20, source="MANUAL", execution_mode=mode))
    engine.process_due(phase="OPEN", execution_mode=mode)
    assert len(broker.sent) == 1 and broker.sent[0].id == manual.id
    sell = queue.add(OrderIntent.create("VIX", "SELL", 100, "LO", limit_price=20, source="EM", execution_mode=mode))
    engine.process_due(phase="OPEN", execution_mode=mode)
    assert len(broker.sent) == 2 and broker.sent[-1].id == sell.id


def test_backtest_priority_budget_and_block_are_applied_at_fill(tmp_path):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [10.0] * 260 + [9.8, 9.6, 9.7, 10.0, 10.3, 10.1, 9.8, 9.5]
    payload = {"t": [int((start + timedelta(days=i)).timestamp()) for i in range(len(values))],
               "o": values, "c": values, "h": [v * 1.01 for v in values],
               "l": [v * 0.99 for v in values], "v": [1_000_000] * len(values)}
    store = HistoricalDataStore(root=tmp_path, fetcher=lambda *_args: payload)
    result = BacktestEngine(store).run(BacktestConfig(
        symbols=["FPT"], start_date=(start + timedelta(days=255)).date().isoformat(),
        end_date=(start + timedelta(days=len(values) - 1)).date().isoformat(),
        initial_capital=60_000_000, fixed_exposure_pct=100, priority_symbols=["FPT"],
        priority_capital_enabled=True, priority_total_capital=60_000_000,
        priority_allocations={"FPT": {"limit_vnd": 20_000_000, "use_pct": 50}},
        loss_lock_enabled=True, rule_parameters={"buy_window_enabled": False, "loss_lock_mode": "BLOCK"},
    ))
    buys = [event for event in result.events if event.side == "BUY"]
    assert buys and 8_000_000 < buys[0].gross < 10_000_000
    assert buys[0].gross + buys[0].fee <= 10_000_000
    assert result.config.rule_parameters["loss_lock_mode"] == "BLOCK"


@pytest.mark.parametrize("simulation", ["DAILY", "AUTO_HYBRID"])
def test_backtest_block_does_not_expire_after_24_hours(tmp_path, simulation):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [10.0] * 260 + [9.8, 9.6, 9.7, 10.0, 10.3, 10.1, 9.8, 9.5] * 6
    payload = {"t": [int((start + timedelta(days=i)).timestamp()) for i in range(len(values))],
               "o": values, "c": values, "h": [v * 1.01 for v in values],
               "l": [v * 0.99 for v in values], "v": [1_000_000] * len(values)}
    store = HistoricalDataStore(root=tmp_path, fetcher=lambda *_args: payload if _args[1] == "1D" else {})
    buys = {}
    for lock_mode in ("TIMED", "BLOCK"):
        result = BacktestEngine(store).run(BacktestConfig(
            symbols=["FPT"], start_date=(start + timedelta(days=255)).date().isoformat(),
            end_date=(start + timedelta(days=len(values) - 1)).date().isoformat(),
            initial_capital=60_000_000, fixed_exposure_pct=100, em_modes=[],
            simulation_mode=simulation, loss_lock_enabled=True,
            rule_parameters={"buy_window_enabled": False, "loss_lock_mode": lock_mode,
                             "loss_lock_count": 1, "initial_sl_pct": -0.1, "reentry_sl_pct": -0.1},
        ))
        buys[lock_mode] = sum(event.side == "BUY" for event in result.events)
    assert buys["TIMED"] >= 2
    assert buys["BLOCK"] == 1


def test_minimal_setting_controls_construct_save_and_unlock_offline(tmp_path, ui_root, monkeypatch):
    from viking_v2.connections.window import ConnectionPopup
    from viking_v2.rules.window import RuleSettingsPopup
    root = ui_root
    previous_children = set(root.winfo_children())
    try:
        settings = config.AppSettings.from_dict({"watchlist": PRIORITY, "priority_symbols": PRIORITY,
            "priority_capital_enabled": True, "priority_total_capital": 60_000_000,
            "priority_allocations": ALLOCATIONS})
        trades = TradeStateStore(tmp_path / "state.json", loss_lock_policy=lambda: (3, "BLOCK"))
        _losses(trades, mode="PAPER")
        rule = RuleSettingsPopup(root, settings, "CONTROL_TEST", lambda: None, trade_state=trades)
        assert rule.block_symbol.get() == "FPT"
        rule.loss_block.set(True)
        rule.save()
        assert "ĐÃ LƯU" in rule.status.cget("text"), rule.status.cget("text")
        assert settings.rule_parameters["loss_lock_mode"] == "BLOCK"
        monkeypatch.setattr("viking_v2.rules.window.messagebox.askyesno", lambda *_a, **_kw: True)
        rule._unlock_block()
        assert trades.loss_blocks("PAPER") == []
        assert "ĐÃ MỞ BLOCK" in rule.status.cget("text")
        client = SimpleNamespace(account_no="", otp_type="email_otp", has_trading_token=lambda: False)
        connection = ConnectionPopup(root, settings, "CONTROL_TEST", client, lambda: None)
        connection._divide_priority_capital()
        assert connection._priority_allocations == ALLOCATIONS
        connection._save_priority()
        saved = config.load_settings("CONTROL_TEST")
        assert saved.priority_allocations == ALLOCATIONS
        assert saved.priority_capital_enabled
        connection._configure_priority_symbol("FPT")
        root.update_idletasks()
    finally:
        for child in root.winfo_children():
            if child not in previous_children:
                child.destroy()
