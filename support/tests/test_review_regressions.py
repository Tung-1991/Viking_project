"""Regression coverage for the independently reproduced trading defects.

Run via support/tools/run_offline.py. All account state uses pytest tmp_path;
all broker writes terminate in the fake _request below, never HTTP.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import random
import time

import pytest

from viking_v2.config import AppSettings
from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
import viking_v2.dashboard.actions as ui_module
from viking_v2.models import BrokerOrderResult, OrderIntent, StrategyDecision, TradeCycle
from viking_v2.rules.state import RuleStateStore
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.business import StaticRule, StaticRuleParameters, ema, rsi, indicator_snapshot
from viking_v2.rules.entry_filters import apply_buy_filters
from viking_v2.storage import JSONLineJournal, CSVOrderJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.state import TradeStateStore


class FakeDNSE(DNSEClient):
    def __init__(self):
        super().__init__(api_key="REVIEW_FAKE", api_secret="REVIEW_FAKE", account_no="REVIEW_FAKE")
        self.calls = []
        self.rows = []
        self.positions = []
        self.cash = 100_000_000
        self.next_result = None

    def has_trading_token(self):
        return True

    def cash_package(self, symbol):
        return {"id": 1, "initialRate": 1, "brokerFirmBuyingFeeRate": 0}

    def get_balance(self, **kwargs):
        return {"equity": 100_000_000, "stock": {"availableCash": self.cash}}

    def get_positions(self, **kwargs):
        return deepcopy(self.positions)

    def get_orders(self, **kwargs):
        return deepcopy(self.rows)

    def get_order_detail(self, order_id, **kwargs):
        return next((deepcopy(row) for row in self.rows if row["id"] == order_id), None)

    def get_buying_power(self, *args):
        return {"qmaxBuy": 100_000}

    def get_secdef(self, symbol):
        return {"marketId": "STO", "ceilingPrice": 110_000, "floorPrice": 10_000}

    def _request(self, method, path, **kwargs):
        # This is the broker boundary. There is no HTTP session call.
        self.calls.append((method, path, deepcopy(kwargs.get("payload"))))
        if self.next_result is not None:
            result, self.next_result = self.next_result, None
            return result
        payload = kwargs.get("payload") or {}
        if method == "POST":
            row = dict(payload, id=f"B{sum(c[0] == 'POST' for c in self.calls)}",
                       orderStatus="New", fillQuantity=0, price_unit="VND")
            self.rows.append(row)
        elif method == "PUT":
            order_id = path.rsplit("/", 1)[-1]
            row = next(row for row in self.rows if row["id"] == order_id)
            row.update(payload)
        else:
            raise AssertionError(f"Unexpected fake request: {method} {path}")
        return True, deepcopy(row), 200, ""


def fixture_book(path, broker=None, **kwargs):
    broker = broker or FakeDNSE()
    queue = OrderQueue(path / "orders.json")
    trades = TradeStateStore(path / "trades.json")
    rules = RuleStateStore(path / "rules.json")
    service = ExecutionService(broker, broker, queue, JSONLineJournal(path / "journal.jsonl"),
                               trade_state=trades, rule_state=rules, **kwargs)
    return broker, queue, trades, rules, service


def fresh_quote(symbol="FPT", price=50):
    return dict(symbol=symbol, price=price, ask=price, bid=price,
                timestamp=time.time(), source="WS", ceiling_price=110)


def app_fixture(broker, queue, trades, rules):
    app = DashboardActionsMixin()
    app.real = app.paper = broker
    app.queue, app.trade_state, app.rule_state = queue, trades, rules
    app.settings = AppSettings(watchlist=["FPT"], priority_symbols=["FPT"],
        priority_capital_enabled=True, priority_total_capital=10_000_000,
        priority_allocations={"FPT": {"limit_vnd": 10_000_000, "use_pct": 100, "max_orders": 1}},
        buy_fee_pct=0, market_phase_override_enabled=True,
        market_phase_override_exposure_pct=100).normalize()
    app._io_executor = SimpleNamespace(submit=lambda task: task())
    app._post_ui = lambda callback: callback()
    app._refresh_local = lambda: None
    return app


def mock_edit_widgets(monkeypatch):
    entries, buttons = [], []

    class Widget:
        def __init__(self, *args, **kwargs):
            self.options, self.value = dict(kwargs), ""

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

        def configure(self, **kwargs):
            self.options.update(kwargs)

        def insert(self, index, value):
            self.value = str(value)

        def get(self):
            return self.value

    def entry(*args, **kwargs):
        widget = Widget(*args, **kwargs)
        entries.append(widget)
        return widget

    def button(*args, **kwargs):
        widget = Widget(*args, **kwargs)
        buttons.append(widget)
        return widget

    for name in ("CTkToplevel", "CTkFrame", "CTkLabel"):
        monkeypatch.setattr(ui_module.ctk, name, Widget)
    monkeypatch.setattr(ui_module.ctk, "CTkEntry", entry)
    monkeypatch.setattr(ui_module.ctk, "CTkButton", button)
    monkeypatch.setattr(ui_module.tk, "BooleanVar", lambda **kwargs: SimpleNamespace(
        get=lambda: kwargs["value"], set=lambda value: None))
    monkeypatch.setattr(ui_module, "_HoverHint", lambda *args, **kwargs: None)
    return entries, buttons


@pytest.mark.parametrize("source", ["MANUAL", "BOT"])
def test_replace_buy_must_keep_priority_limit(tmp_path, monkeypatch, source):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    service.bot_entry_guard = app._check_bot_entry_limits
    service.manual_buy_guard = app._check_manual_buy_capital
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        source=source, execution_mode="REAL", trade_id="T", entry_market_state="UPTREND"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "WORKING", queue.get(intent.id).to_dict()
    assert len(broker.calls) == 1 and broker.calls[0][0] == "POST"
    # The ordinary manual BUY guard already knows that 300 x 50K exceeds 10M.
    feedback = app._manual_buy_capital_feedback("FPT", "REAL", 300, "LO", 50,
        tick=fresh_quote(), balance=broker.get_balance(), positions=[], exclude_intent_id=intent.id)
    assert feedback["reason"]
    entries, buttons = mock_edit_widgets(monkeypatch)
    app._edit_running_order(dict(local_id=intent.id, broker_order_id="B1", symbol="FPT",
                                 side="BUY", mode="REAL", order_type="LO", status="WORKING"))
    entries[0].value = "300"
    next(button for button in buttons if button.options.get("text") == "LƯU").options["command"]()
    # If a PUT escaped the limit, simulate its real economic consequence.
    if any(call[0] == "PUT" for call in broker.calls):
        broker.rows[0].update(orderStatus="Filled", fillQuantity=300, averagePrice=50_000, fee=0)
        service.reconcile_working("REAL")
    cycle = trades.get("T")
    spent = cycle.buy_notional + cycle.fees_paid if cycle else 0
    assert spent <= 10_000_000, {"spent": spent, "cap": 10_000_000, "calls": broker.calls}


def test_valid_buy_and_oversized_buy_control(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    service.bot_entry_guard = app._check_bot_entry_limits
    valid = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        source="BOT", execution_mode="REAL", trade_id="T", entry_market_state="UPTREND"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert [call[0] for call in broker.calls] == ["POST"]
    assert broker.calls[0][2]["quantity"] == 100
    broker.rows[0].update(orderStatus="Cancelled", quantity=100)
    service.reconcile_working("REAL")
    invalid = queue.add(OrderIntent.create("FPT", "BUY", 300, "LO", limit_price=50,
        source="BOT", execution_mode="REAL", trade_id="T", entry_market_state="UPTREND"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == 1
    assert queue.get(invalid.id).status == "CANCELLED"


def test_cost_return_to_prior_snapshot_must_update_ledger(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        execution_mode="REAL", trade_id="T"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    for fee in (1500, 3000, 1500):
        broker.rows[0].update(orderStatus="Filled", fillQuantity=100, averagePrice=50_000, fee=fee)
        service.reconcile_working("REAL")
    actual = trades.get("T")
    assert actual.entry_quantity == 100
    assert actual.fees_paid == 1500, actual.to_dict()
    assert actual.net_pnl == -1500


class TrancheBroker:
    def __init__(self):
        self.sent, self.rows = [], []
        self.sellable = 100
        self.holding = 300

    def has_trading_token(self):
        return True

    def get_positions(self, **kwargs):
        return [dict(symbol="FPT", openQuantity=self.holding, tradeQuantity=self.sellable)]

    def get_orders(self, **kwargs):
        return deepcopy(self.rows)

    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        number = len(self.sent)
        row = dict(id=f"S{number}", symbol="FPT", side="NS", quantity=intent.quantity,
                   averagePrice=50_000, fee=0, price_unit="VND", remark=intent.request_tag,
                   orderStatus="Filled" if number == 1 else "New",
                   fillQuantity=intent.quantity if number == 1 else 0)
        self.rows.append(row)
        if number == 1:
            self.holding -= intent.quantity
        return BrokerOrderResult(True, row["orderStatus"], order_id=row["id"], raw=deepcopy(row))


def test_completed_sell_tranche_must_keep_late_fee_tracking(tmp_path):
    broker = TrancheBroker()
    broker, queue, trades, rules, service = fixture_book(tmp_path, broker, quote_provider=fresh_quote)
    trades.create("FPT", "REAL", trade_id="T")
    trades.record_buy_fill("T", 300, 50)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 300, "MARKET", execution_mode="REAL",
        trade_id="T", source="MANUAL", action="CLOSE", sell_wait_policy="KEEP"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "WAITING_SETTLEMENT"
    assert trades.get("T").sold_quantity == 100
    # A second accepted order now manages the next sellable tranche.
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.sent) == 2 and all(item.quantity == 100 for item in broker.sent)
    broker.rows[0]["fee"] = 1500
    service.reconcile_working("REAL")
    service.reconcile_external_sells(broker.get_positions(), broker.get_orders())
    actual = trades.get("T")
    assert actual.sold_quantity == 100
    assert actual.fees_paid == 1500, {"cycle": actual.to_dict(), "intent": queue.get(intent.id).to_dict()}


def test_timeout_never_resubmits_and_late_fill_is_once(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    broker.next_result = (False, None, 0, "FAKE_TIMEOUT")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL",
                                         source="BOT", trade_id="T"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "UNKNOWN"
    for _ in range(3):
        service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == 1
    tag = queue.get(intent.id).request_tag
    broker.rows = [dict(id="LATE", symbol="FPT", side="NB", loanPackageId=1,
        quantity=100, fillQuantity=100, orderStatus="Filled", averagePrice=50_000,
        price_unit="VND", remark=tag, fee=1500)]
    # Construct fresh stores and service to exercise persisted restart state.
    broker, queue, trades, rules, restarted = fixture_book(tmp_path, broker, quote_provider=fresh_quote)
    for _ in range(3):
        restarted.reconcile_working("REAL")
        restarted.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == 1
    assert trades.get("T").open_quantity == 100
    assert trades.get("T").fees_paid == 1500
    assert queue.get(intent.id).status == "FILLED"
    filled_rows = [row for row in CSVOrderJournal(tmp_path / "order_history.csv").read_all()
                   if row.get("message") == "BROKER_FILL_RECONCILED"]
    assert len(filled_rows) == 1


def test_stop_loss_must_cover_shares_outside_working_partial_protect(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    trades.create("FPT", "REAL", trade_id="T", source="BOT", em_modes=["NORMAL"])
    trades.record_buy_fill("T", 300, 50)
    broker.positions = [dict(symbol="FPT", loanPackageId=1, openQuantity=300, tradeQuantity=300,
                             costPrice=50_000, marketPrice=48_000, price_unit="VND")]
    # Produce the partial PROTECT with the actual rule and planner first.
    rule = StaticRule(StaticRuleParameters(buy_window_enabled=False,
        normal_policy="AUTO", normal_dynamic_enabled=False,
        normal_giveback_pct=2, normal_sell_pct=50))
    context = dict(symbol="FPT", signal_mode="REALTIME",
        confirmed_market_state="UPTREND", bars=[])
    position = dict(quantity=300, avg_price=50, current_price=53.25,
        peak_profit_pct=9, normal_armed=True, managed_by_app=True,
        sl_enabled=True, em_modes=["NORMAL"])
    protect_decision = rule.evaluate(context, {"position": position})
    assert protect_decision.action == "SELL"
    assert protect_decision.quantity_fraction == .5
    planner = StrategyOrderPlanner(queue, trades, rules)
    planned = planner.plan(protect_decision, execution_mode="REAL", execution_style="MARKET",
        tick=fresh_quote(price=53.25), portfolio={"position_quantity": 300, "trade_id": "T"},
        candle_key="REVIEW_PROTECT")
    assert planned.intent is not None and planned.intent.quantity == 100
    protect = planned.intent
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(protect.id).status == "WORKING"
    assert broker.calls[0][2]["orderType"] == "MTL"
    position["current_price"] = 48
    decision = rule.evaluate(context, {"position": position})
    assert decision.action == "SELL" and decision.event == "STOP_LOSS"
    result = planner.plan(decision, execution_mode="REAL", execution_style="MARKET",
        tick=fresh_quote(price=48), portfolio={"position_quantity": 300, "trade_id": "T"},
        candle_key="REVIEW_SL")
    service.process_due(phase="OPEN", execution_mode="REAL")
    reserved = sum(item.remaining_quantity for item in queue.list_all()
                   if item.side == "SELL" and item.status not in {"CANCELLED", "FILLED", "REJECTED"})
    assert reserved >= 300, {"planner_reason": result.reason, "covered": reserved,
                             "holding": 300, "fake_calls": broker.calls}
    assert [call[2]["quantity"] for call in broker.calls] == [100, 200]
    repeated = planner.plan(decision, execution_mode="REAL", execution_style="MARKET",
        tick=fresh_quote(price=48), portfolio={"position_quantity": 300, "trade_id": "T"},
        candle_key="REVIEW_SL_AGAIN")
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert repeated.intent is None and len(broker.calls) == 2


@pytest.mark.parametrize("issue", ["old", "wrong_symbol", "frozen", "source_error", "missing_time"])
def test_bad_quote_blocks_broker_but_fresh_control_sends(tmp_path, issue):
    quote = fresh_quote()
    if issue == "old":
        quote["timestamp"] -= 60
    elif issue == "wrong_symbol":
        quote["symbol"] = "VIX"
    elif issue == "frozen":
        quote["frozen"] = True
    elif issue == "source_error":
        quote["health"] = "REST_UNAVAILABLE"
    else:
        quote.pop("timestamp")
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=lambda symbol: quote)
    bad = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", source="BOT", execution_mode="REAL"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.calls == [] and queue.get(bad.id).status == "CANCELLED"
    quote.clear()
    quote.update(fresh_quote())
    valid = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", source="BOT", execution_mode="REAL"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == 1 and broker.calls[0][2]["orderType"] == "MTL"


def test_replace_sell_must_recheck_sellable_before_gateway(tmp_path, monkeypatch):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    broker.positions = [dict(symbol="FPT", loanPackageId=1, openQuantity=300, tradeQuantity=100)]
    trades.create("FPT", "REAL", trade_id="T")
    trades.record_buy_fill("T", 300, 50)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=50,
        execution_mode="REAL", source="MANUAL", trade_id="T", action="CLOSE"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == 1 and broker.calls[0][2]["quantity"] == 100
    entries, buttons = mock_edit_widgets(monkeypatch)
    app._edit_running_order(dict(local_id=intent.id, broker_order_id="B1", symbol="FPT",
                                 side="SELL", mode="REAL", order_type="LO", status="WORKING"))
    entries[0].value = "300"
    next(button for button in buttons if button.options.get("text") == "LƯU").options["command"]()
    puts = [call for call in broker.calls if call[0] == "PUT"]
    assert not puts, {"sellable": 100, "calls": puts}


@pytest.mark.parametrize("initial_failure", [False, True])
def test_failed_closed_telegram_notice_must_survive_restart(tmp_path, monkeypatch, initial_failure):
    class InlineThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs
        def start(self):
            self.target(**self.kwargs)
    monkeypatch.setattr(ui_module.threading, "Thread", InlineThread)
    class FakeTelegram:
        def __init__(self):
            self.fail = initial_failure
            self.sent, self.attempts = [], 0
        def send_message(self, chat_id, text):
            self.attempts += 1
            if self.fail:
                raise RuntimeError("REVIEW_FAKE_DELIVERY_FAILURE")
            self.sent.append(text)
    client = FakeTelegram()
    broker, queue, trades, rules, service = fixture_book(tmp_path)
    app = app_fixture(broker, queue, trades, rules)
    app.settings.telegram_enabled = True
    app.telegram = SignalTelegramService(client, chat_id="FAKE_CHAT")
    rules.open_telegram_signal("FPT", "DAY", price=50, market_state="UPTREND", signal_id="T", stream="REAL")
    cycle = TradeCycle(id="T", symbol="FPT", execution_mode="REAL", source="BOT")
    cycle.record_buy_fill(100, 50)
    cycle.record_sell_fill(100, 51)
    intent = OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", trade_id="T")
    app._notify_bot_trade_event("CLOSED", cycle, intent)
    assert client.attempts == 1
    assert len(client.sent) == (0 if initial_failure else 1)
    client.fail = False
    if initial_failure:
        assert len(rules.pending_telegram_notices()) == 1
    # Same persisted state after a process restart, then an attempted redelivery.
    app.rule_state = RuleStateStore(tmp_path / "rules.json")
    app.telegram = SignalTelegramService(client, chat_id="FAKE_CHAT")
    # Existing Telegram outbox deliberately waits 60 seconds between retries.
    recovery_time = time.time() + 61
    monkeypatch.setattr(time, "time", lambda: recovery_time)
    app._notify_bot_trade_event("CLOSED", cycle, intent)
    app._retry_telegram_notices()
    assert len(client.sent) == 1, {"attempts": client.attempts, "sent": client.sent,
        "pending": app.rule_state.pending_telegram_notices(),
        "active": app.rule_state.active_telegram_signal("FPT", "REAL")}


@pytest.mark.parametrize("cash,expected", [(4_999_999, 0), (5_000_000, 1)])
def test_available_cash_boundary_reaches_fake_gateway_only_if_funded(tmp_path, cash, expected):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    broker.cash = cash
    queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50, execution_mode="REAL"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == expected


@pytest.mark.parametrize("sellable,expected", [(0, 0), (99, 0), (100, 100), (300, 300)])
def test_sell_tranche_never_exceeds_explicit_sellable(tmp_path, sellable, expected):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    broker.positions = [dict(symbol="FPT", loanPackageId=1, openQuantity=300, tradeQuantity=sellable)]
    trades.create("FPT", "REAL", trade_id="T")
    trades.record_buy_fill("T", 300, 50)
    queue.add(OrderIntent.create("FPT", "SELL", 300, "MARKET", execution_mode="REAL",
                                 trade_id="T", action="CLOSE", sell_wait_policy="KEEP"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    sent = sum(call[2]["quantity"] for call in broker.calls if call[0] == "POST")
    assert sent == expected and sent <= sellable


def test_buy_add_two_entries_then_third_is_blocked(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    app.settings.priority_allocations["FPT"].update(use_pct=50, max_orders=2)
    service.bot_entry_guard = app._check_bot_entry_limits
    for number in range(3):
        intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
            source="BOT", execution_mode="REAL", trade_id=f"T{number}", entry_market_state="UPTREND"))
        service.process_due(phase="OPEN", execution_mode="REAL")
        if number < 2:
            assert queue.get(intent.id).status == "WORKING", queue.get(intent.id).to_dict()
            broker.rows[-1].update(orderStatus="Filled", fillQuantity=100, averagePrice=50_000, fee=0)
            service.reconcile_working("REAL")
            broker.positions = [dict(symbol="FPT", loanPackageId=1, openQuantity=100*(number+1),
                tradeQuantity=0, costPrice=50_000, marketPrice=50_000, price_unit="VND")]
        else:
            assert queue.get(intent.id).status == "CANCELLED"
    assert len(broker.calls) == 2
    cycle = trades.active_for("FPT", "REAL")
    assert cycle.open_quantity == 200 and len(cycle.entry_order_ids) == 2
    assert cycle.buy_notional == 10_000_000


@pytest.mark.parametrize("seed", [7, 23, 91])
def test_arbitrary_partial_fills_duplicates_and_restart_keep_cashflow(tmp_path, seed):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    queue.add(OrderIntent.create("FPT", "BUY", 1000, "LO", limit_price=100,
                                 execution_mode="REAL", trade_id="T"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    rng = random.Random(seed)
    quantity, notional = 0, 0
    for index in range(9):
        delta = min(1000-quantity, rng.randint(1, 150)) if index < 8 else 1000-quantity
        price = rng.randint(40_000, 60_000)
        quantity += delta
        notional += delta * price
        fee = notional * 0.00045
        broker.rows[0].update(orderStatus="Filled" if quantity == 1000 else "PartiallyFilled",
            fillQuantity=quantity, averagePrice=notional/quantity, fee=fee)
        for _ in range(2):
            service.reconcile_working("REAL")
        if index == 3:
            broker, queue, trades, rules, service = fixture_book(tmp_path, broker, quote_provider=fresh_quote)
        cycle = trades.get("T")
        assert cycle.entry_quantity == quantity and cycle.open_quantity == quantity
        assert cycle.buy_notional == pytest.approx(notional)
        assert cycle.fees_paid == pytest.approx(fee)
        assert cycle.net_pnl == pytest.approx(-fee, abs=.01)
    assert len(broker.calls) == 1


def test_fill_crash_rolls_back_then_recovery_applies_once(tmp_path, monkeypatch):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
                                 execution_mode="REAL", trade_id="T"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    broker.rows[0].update(orderStatus="Filled", fillQuantity=100, averagePrice=50_000, fee=1500)
    real_fill = service._record_trade_fill
    with monkeypatch.context() as fault:
        def crash_after_mutation(*args, **kwargs):
            real_fill(*args, **kwargs)
            raise OSError("REVIEW_CRASH_AFTER_LEDGER_WRITE")
        fault.setattr(service, "_record_trade_fill", crash_after_mutation)
        with pytest.raises(OSError):
            service.reconcile_working("REAL")
    assert trades.get("T") is None and queue.get(intent.id).filled_quantity == 0
    broker, queue, trades, rules, restarted = fixture_book(tmp_path, broker, quote_provider=fresh_quote)
    restarted.reconcile_working("REAL")
    assert queue.get(intent.id).filled_quantity == trades.get("T").open_quantity == 100
    assert trades.get("T").fees_paid == 1500 and len(broker.calls) == 1


def test_indicator_arithmetic_against_separate_reference():
    prices = [44, 45, 44.5, 44, 46, 45, 47, 46.5, 45, 46, 48, 47, 49, 48, 50, 49, 51, 50]
    running, expected_ema = prices[0], [prices[0]]
    for price in prices[1:]:
        running = running + (price-running)*.5
        expected_ema.append(running)
    assert ema(prices, 3) == pytest.approx(expected_ema)
    period = 14
    changes = [right-left for left, right in zip(prices, prices[1:])]
    gain = sum(max(change, 0) for change in changes[:period])/period
    loss = sum(max(-change, 0) for change in changes[:period])/period
    reference = [100-100/(1+gain/loss)]
    for change in changes[period:]:
        gain = gain + (max(change, 0)-gain)/period
        loss = loss + (max(-change, 0)-loss)/period
        reference.append(100-100/(1+gain/loss))
    assert rsi(prices, period)[period:] == pytest.approx(reference)


def test_buy_window_release_has_actual_broker_post_control(tmp_path, monkeypatch):
    now = datetime(2026, 10, 9, 13, 59, tzinfo=VN_TZ)
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    import viking_v2.rules.planner as planner_module
    monkeypatch.setattr(planner_module, "datetime", FrozenDatetime)
    monkeypatch.setattr(time, "time", lambda: now.timestamp())
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    queue._now = lambda: now.timestamp()
    rule = StaticRule(StaticRuleParameters(whipsaw_enabled=False))
    marks = dict(sample_count=50, buy_ema_fast=51, buy_ema_slow=50,
        sell_ema_fast=51, sell_ema_slow=50, rsi=60, rsi_previous=55,
        buy_ema_slow_period=6, sell_ema_slow_period=6, rsi_period=14)
    before = {**marks, "buy_ema_fast": 49, "rsi": 54}
    portfolio = dict(nav=100_000_000, available_cash=100_000_000, order_budget=5_000_000,
        available_capital=5_000_000, entry_slot_available=True, open_positions=0)
    context = dict(symbol="FPT", signal_mode="REALTIME", previous_indicators=before,
        indicator_snapshot=marks, bars=[], exchange="HOSE", confirmed_market_state="UPTREND",
        max_observation_gap_seconds=20)
    raw = rule.evaluate(context, portfolio)
    state, waiting = apply_buy_filters(rule, raw, context, portfolio, {}, observed_at=now,
                                      exchange="HOSE", working_dates=["2026-10-09"])
    assert waiting.reason == "BUY_WINDOW_WAIT"
    assert broker.calls == []
    now += timedelta(minutes=1)
    # Keep observation continuity across the minute; the window isn't downtime.
    state["window"]["observed_time"] = (now-timedelta(seconds=1)).isoformat()
    context["previous_indicators"] = marks
    raw = rule.evaluate(context, portfolio)
    state, released = apply_buy_filters(rule, raw, context, portfolio, state, observed_at=now,
                                       exchange="HOSE", working_dates=["2026-10-09"])
    assert released.action == "BUY"
    planned = StrategyOrderPlanner(queue, trades, rules).plan(released, execution_mode="REAL",
        execution_style="MARKET", tick=fresh_quote(), portfolio=portfolio, candle_key="REVIEW_WINDOW")
    assert planned.intent is not None, planned.reason
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.calls) == 1 and broker.calls[0][2]["orderType"] == "MTL"


@pytest.mark.parametrize("source", ["MANUAL", "BOT"])
@pytest.mark.parametrize("quantity,cash,accepted", [(100, 0, True), (200, 5_000_000, True),
    (200, 4_999_999, False), (300, 10_000_000, False)])
def test_replacement_cash_and_priority_boundary_with_valid_put_control(tmp_path, monkeypatch,
                                                                       source, quantity, cash, accepted):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        source=source, execution_mode="REAL", trade_id="T", entry_market_state="UPTREND"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    broker.cash = cash  # Free cash after the original 5M order was reserved.
    entries, buttons = mock_edit_widgets(monkeypatch)
    app._edit_running_order(dict(local_id=intent.id, broker_order_id="B1", symbol="FPT",
        side="BUY", mode="REAL", order_type="LO", status="WORKING"))
    entries[0].value = str(quantity)
    save = next(button for button in buttons if button.options.get("text") == "LƯU").options["command"]
    save()
    assert [call[0] for call in broker.calls] == (["POST", "PUT"] if accepted else ["POST"])
    assert queue.get(intent.id).status == ("REPLACE_PENDING" if accepted else "WORKING")
    if accepted:
        assert broker.calls[-1][2]["quantity"] == quantity
        save()  # Repeated clicks while the first edit is unresolved never send another PUT.
        assert len(broker.calls) == 2


@pytest.mark.parametrize("remaining,accepted", [(100, True), (200, False)])
@pytest.mark.parametrize("max_orders", [1, 2])
def test_partial_buy_edit_keeps_existing_entry_and_total_per_order_cap(tmp_path, remaining, accepted, max_orders):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    app.settings.priority_total_capital = 10_000_000 * max_orders
    app.settings.priority_allocations["FPT"].update(limit_vnd=10_000_000 * max_orders,
        use_pct=100 / max_orders, max_orders=max_orders)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "LO", limit_price=50,
        source="BOT", execution_mode="REAL", trade_id="T", entry_market_state="UPTREND"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    broker.rows[0].update(orderStatus="PartiallyFilled", fillQuantity=100, averagePrice=50_000, fee=0)
    service.reconcile_working("REAL")
    broker.positions = [dict(symbol="FPT", loanPackageId=1, openQuantity=100,
        tradeQuantity=0, costPrice=50_000, marketPrice=50_000, price_unit="VND")]
    broker.cash = 5_000_000
    action = dict(local_id=intent.id, broker_order_id="B1", symbol="FPT", side="BUY")
    if accepted:
        app._reserve_running_order_replace(action, remaining, 50)
        assert queue.get(intent.id).status == "REPLACE_PENDING"
        assert queue.get(intent.id).details["requested_replace"]["quantity"] == 200
    else:
        with pytest.raises(ValueError, match="Priority"):
            app._reserve_running_order_replace(action, remaining, 50)
        assert queue.get(intent.id).status == "PARTIAL"
    assert trades.get("T").open_quantity == 100 and len(broker.calls) == 1


@pytest.mark.parametrize("quantity,accepted", [(100, True), (200, False)])
def test_sell_edit_accounts_for_another_reserved_exit(tmp_path, quantity, accepted):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    trades.create("FPT", "REAL", trade_id="T")
    trades.record_buy_fill("T", 300, 50)
    broker.positions = [dict(symbol="FPT", loanPackageId=1, openQuantity=300, tradeQuantity=300)]
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=50,
        execution_mode="REAL", trade_id="T", action="CLOSE"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    other = queue.add(OrderIntent.create("FPT", "SELL", 200, "MARKET", execution_mode="REAL",
        trade_id="T", action="CLOSE"))
    action = dict(local_id=intent.id, broker_order_id="B1", symbol="FPT", side="SELL")
    if accepted:
        app._reserve_running_order_replace(action, quantity, 50)
        assert broker.replace_order("B1", price=50, quantity=quantity).ok
        assert broker.calls[-1][0] == "PUT" and broker.calls[-1][2]["quantity"] == quantity
    else:
        with pytest.raises(ValueError, match="cổ bán được"):
            app._reserve_running_order_replace(action, quantity, 50)
    assert queue.get(other.id).remaining_quantity == 200 and len(broker.calls) == (2 if accepted else 1)


@pytest.mark.parametrize("status,quantity,filled,expected", [
    ("WORKING", 100, 0, 200), ("WORKING", 300, 0, 0),
    ("PARTIAL", 100, 50, 200), ("UNKNOWN", 100, 0, 0),
])
def test_stop_loss_reserves_only_uncovered_shares_and_preserves_unknown(tmp_path, status,
                                                                     quantity, filled, expected):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    trades.create("FPT", "REAL", trade_id="T")
    trades.record_buy_fill("T", 300, 50)
    if filled:
        trades.record_sell_fill("T", filled, 51)
    existing = queue.add(OrderIntent.create("FPT", "SELL", quantity, "MARKET", source="EM",
        execution_mode="REAL", trade_id="T", action="CLOSE", reason="NORMAL_PROTECTION"))
    queue._update(existing.id, status=status, filled_quantity=filled,
                  remaining_quantity=quantity-filled, broker_order_id="OLD")
    planner = StrategyOrderPlanner(queue, trades, rules)
    decision = StrategyDecision("SELL", "FPT", "STOP_LOSS", event="STOP_LOSS", quantity_fraction=1)
    result = planner.plan(decision, execution_mode="REAL", execution_style="MARKET",
        tick=fresh_quote(), portfolio={"position_quantity": 300-filled, "trade_id": "T"},
        candle_key="SL")
    assert (result.intent.quantity if result.intent else 0) == expected
    assert queue.get(existing.id).status == status
    total = sum(item.remaining_quantity for item in queue.find_active("FPT", side="SELL", execution_mode="REAL"))
    assert total <= 300-filled
    if expected:
        repeated = planner.plan(decision, execution_mode="REAL", execution_style="MARKET",
            tick=fresh_quote(), portfolio={"position_quantity": 300-filled, "trade_id": "T"}, candle_key="SL2")
        assert repeated.intent is None


def test_repeated_signed_fee_corrections_and_restart_do_not_reuse_old_event(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        execution_mode="REAL", trade_id="T"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    for index, fee in enumerate((1500, 3000, 1500, 3000, 1500)):
        broker.rows[0].update(orderStatus="Filled", fillQuantity=100, averagePrice=50_000, fee=fee)
        for _ in range(2):
            service.reconcile_working("REAL")
        assert trades.get("T").fees_paid == fee
        assert trades.get("T").entry_quantity == 100
        if index == 2:
            broker, queue, trades, rules, service = fixture_book(tmp_path, broker, quote_provider=fresh_quote)
    rows = CSVOrderJournal(tmp_path / "order_history.csv").read_all()
    assert len([row for row in rows if row.get("message") == "BROKER_COST_RECONCILED"]) == 4
    assert len(broker.calls) == 1


@pytest.mark.parametrize("initial_fill_response", [True, False])
def test_completed_sell_fee_before_next_tranche_keeps_waiting_state(tmp_path, initial_fill_response):
    class DelayedTrancheBroker(TrancheBroker):
        def place_order(self, intent):
            result = super().place_order(intent)
            if len(self.sent) == 1 and not initial_fill_response:
                self.rows[0].update(orderStatus="New", fillQuantity=0)
                self.holding = 300
                return BrokerOrderResult(True, "New", order_id="S1", raw=deepcopy(self.rows[0]))
            return result
    broker, queue, trades, rules, service = fixture_book(tmp_path, DelayedTrancheBroker(), quote_provider=fresh_quote)
    trades.create("FPT", "REAL", trade_id="T")
    trades.record_buy_fill("T", 300, 50)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 300, "MARKET", execution_mode="REAL",
        trade_id="T", source="MANUAL", action="CLOSE", sell_wait_policy="KEEP"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    if not initial_fill_response:
        broker.rows[0].update(orderStatus="Filled", fillQuantity=100)
        broker.holding = 200
        service.reconcile_working("REAL")
    before = queue.get(intent.id)
    assert before.status == "WAITING_SETTLEMENT" and before.broker_order_id == ""
    assert "S1" in before.broker_order_ids
    broker.rows[0]["fee"] = 1500
    broker, queue, trades, rules, service = fixture_book(tmp_path, broker, quote_provider=fresh_quote)
    for _ in range(2):
        service.reconcile_working("REAL")
    after = queue.get(intent.id)
    assert after.status == before.status and after.attempt == before.attempt
    assert after.broker_order_id == "" and trades.get("T").sold_quantity == 100
    assert trades.get("T").fees_paid == 1500 and len(broker.sent) == 1


def test_pending_replace_holds_bigger_cap_until_confirmed_or_rejected(tmp_path):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    app.settings.priority_allocations["FPT"]["max_orders"] = 2
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        execution_mode="REAL", source="BOT", entry_market_state="UPTREND"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    queue.mark_broker_replaced(intent.id, quantity=200, broker_quantity=200, limit_price=50)
    context = app._build_entry_limits("FPT", "REAL", fresh_quote(), broker.get_balance(), [], 1)
    assert context["priority_capital"]["committed_vnd"] == 10_000_000
    assert context["order_budget"] == 0
    queue.reject_broker_replace(intent.id, "FAKE_REJECTED")
    context = app._build_entry_limits("FPT", "REAL", fresh_quote(), broker.get_balance(), [], 1)
    assert context["priority_capital"]["committed_vnd"] == 5_000_000


def test_edit_fetches_fresh_order_detail_instead_of_cached_working_status(tmp_path):
    class CachedDetailBroker(FakeDNSE):
        def __init__(self):
            super().__init__()
            self.detail_reads = 0

        def get_order_detail(self, order_id, **kwargs):
            return DNSEClient.get_order_detail(self, order_id, **kwargs)

        def _request(self, method, path, **kwargs):
            if method == "GET":
                self.detail_reads += 1
                return True, deepcopy(self.rows[0]), 200, ""
            return super()._request(method, path, **kwargs)

    broker, queue, trades, rules, service = fixture_book(tmp_path, CachedDetailBroker(), quote_provider=fresh_quote)
    app = app_fixture(broker, queue, trades, rules)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        source="MANUAL", execution_mode="REAL"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.get_order_detail("B1")["orderStatus"] == "New"
    broker.rows[0].update(orderStatus="Filled", fillQuantity=100)
    with pytest.raises(ValueError, match="đang chạy"):
        app._reserve_running_order_replace(dict(local_id=intent.id, broker_order_id="B1",
            symbol="FPT", side="BUY"), 100, 50)
    assert broker.detail_reads == 2 and [call[0] for call in broker.calls] == ["POST"]


@pytest.mark.parametrize("cash,accepted", [(5_007_499, False), (5_007_500, True)])
def test_buy_edit_includes_actual_fee_in_incremental_cash(tmp_path, cash, accepted):
    broker, queue, trades, rules, service = fixture_book(tmp_path, quote_provider=fresh_quote)
    broker.cash_package = lambda symbol: {"id": 1, "initialRate": 1, "brokerFirmBuyingFeeRate": .0015}
    app = app_fixture(broker, queue, trades, rules)
    app.settings.priority_total_capital = 20_000_000
    app.settings.priority_allocations["FPT"]["limit_vnd"] = 20_000_000
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=50,
        execution_mode="REAL"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    broker.cash = cash
    action = dict(local_id=intent.id, broker_order_id="B1", symbol="FPT", side="BUY")
    if accepted:
        app._reserve_running_order_replace(action, 200, 50)
        assert broker.replace_order("B1", price=50, quantity=200).ok
    else:
        with pytest.raises(ValueError):
            app._reserve_running_order_replace(action, 200, 50)
    assert [call[0] for call in broker.calls] == (["POST", "PUT"] if accepted else ["POST"])
