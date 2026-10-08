"""Fixed VA envelopes apply to typed MANUAL buys; all brokers are offline."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import BrokerOrderResult, OrderIntent
from viking_v2.rules.state import RuleStateStore
from viking_v2.storage import CSVOrderJournal, JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


PRIORITY = ["MSN", "CTS", "HDB", "IDC"]


class CashBroker(DNSEClient):
    def __init__(self, cash):
        super().__init__(api_key="OFFLINE", api_secret="OFFLINE", account_no="OFFLINE")
        self.cash, self.positions, self.sent = cash, [], []
        self.fee_rate = .001

    def has_trading_token(self):
        return True

    def cash_package(self, symbol):
        return {"id": 1, "initialRate": 1, "brokerFirmBuyingFeeRate": self.fee_rate}

    def get_balance(self, **kwargs):
        return {"stock": {"availableCash": self.cash}}

    def get_positions(self, **kwargs):
        return deepcopy(self.positions)

    def get_orders(self, **kwargs):
        return []

    def get_secdef(self, symbol):
        return {"marketId": "STO", "ceilingPrice": 80_000}

    def get_buying_power(self, *args):
        return {"qmaxBuy": 100_000}

    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        price = intent.limit_price or 80.0
        gross = intent.quantity * price * 1000
        self.cash -= gross * (1 + self.fee_rate)
        # Deliberately lag the broker position snapshot: durable fills must
        # still consume the envelope when the next ticket is checked.
        return BrokerOrderResult(True, "Filled", order_id="OFFLINE-" + str(len(self.sent)),
                                 raw={"price_unit": "VND", "fillQuantity": intent.quantity,
                                      "averagePrice": price * 1000, "fee": gross * self.fee_rate})


def app_at(tmp_path, cash=60e6, *, mode="PAPER", use_pct=100):
    app = DashboardActionsMixin()
    app.settings = AppSettings.from_dict({
        "watchlist": PRIORITY, "priority_symbols": PRIORITY,
        "priority_capital_enabled": True, "priority_total_capital": 60e6,
        "priority_allocations": {symbol: {"limit_vnd": 15e6, "use_pct": use_pct, "max_orders": 1}
                                 for symbol in PRIORITY},
        "market_phase_override_enabled": True, "market_phase_override_exposure_pct": 100,
        "buy_fee_pct": .1,
    })
    app.queue = OrderQueue(tmp_path / "orders.json")
    app.trade_state = TradeStateStore(tmp_path / "trades.json")
    app.rule_state = RuleStateStore(tmp_path / "rules.json")
    app.real, app.paper = CashBroker(cash), CashBroker(cash)
    app.snapshots = {book: (broker.get_balance(), broker.get_positions(), [])
                     for book, broker in (("REAL", app.real), ("PAPER", app.paper))}
    app.mode = SimpleNamespace(get=lambda: mode)
    app.execution = ExecutionService(
        app.real, app.paper, app.queue, JSONLineJournal(tmp_path / "journal.jsonl"),
        trade_state=app.trade_state, rule_state=app.rule_state,
        manual_buy_guard=app._check_manual_buy_capital,
    )
    return app


def ticket(symbol="MSN", quantity=100, price=80, *, mode="PAPER", side="BUY", kind="LO", source="MANUAL"):
    return OrderIntent.create(symbol, side, quantity, kind, limit_price=price if kind == "LO" else 0,
                              execution_mode=mode, source=source, trade_id=symbol + "-" + mode,
                              details={"reservation_price": price})


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("kind", ["LO", "MARKET", "ATO", "ATC"])
def test_typed_buy_cannot_borrow_fully_reserved_priority_money(tmp_path, mode, kind):
    app = app_at(tmp_path, mode=mode)
    feedback = app._manual_buy_capital_feedback("FPT", mode, 300, kind, 100,
                                               tick={"ceiling_price": 80})
    assert feedback["reason"] == "THIẾU VỐN SAU KHI GIỮ PRIORITY"
    assert "60,000,000 đ" in feedback["hint"]
    assert app._check_manual_buy_capital(ticket("FPT", 300, 100, mode=mode, kind=kind), {})


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_manual_can_buy_priority_inside_cap_with_bot_off(tmp_path, mode):
    app = app_at(tmp_path, mode=mode)
    intent = app.execution.submit(ticket(mode=mode), phase="OPEN", process_immediately=False)
    app.execution.process_due(phase="OPEN", execution_mode=mode, allow_bot_buys=False)
    assert app.queue.get(intent.id).status == "FILLED"
    assert app.trade_state.get(intent.trade_id).open_quantity == 100


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_priority_typed_buy_above_remaining_cap_is_rejected_and_logged(tmp_path, mode):
    app = app_at(tmp_path, cash=100e6, mode=mode)
    intent = app.execution.submit(ticket(quantity=200, mode=mode), phase="OPEN")
    assert intent.status == "REJECTED"
    assert "THIẾU VỐN SAU KHI GIỮ PRIORITY" in intent.result
    assert not (app.real if mode == "REAL" else app.paper).sent
    rows = CSVOrderJournal(tmp_path / "order_history.csv").read_all()
    assert rows[-1]["queue_status"] == "REJECTED"


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_manual_can_use_unreserved_cash_without_bot_per_position_quota(tmp_path, mode):
    app = app_at(tmp_path, cash=100e6, mode=mode)
    # P1 is zero and BOT slots are only one: neither changes fixed MANUAL
    # cash authority. 40m is outside the VA pool, so a 30.03m ticket is legal.
    app.settings.market_phase_override_exposure_pct = 0
    app.settings.rule_parameters["max_positions"] = 1
    intent = app.execution.submit(ticket("FPT", 300, 100, mode=mode), phase="OPEN")
    assert intent.status == "FILLED"
    assert app.settings.priority_total_capital == 60e6
    assert all(row["limit_vnd"] == 15e6 for row in app.settings.priority_allocations.values())


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_fill_then_second_manual_buy_cannot_reuse_cap_when_positions_lag(tmp_path, mode):
    app = app_at(tmp_path, cash=100e6, mode=mode)
    first = app.execution.submit(ticket(mode=mode), phase="OPEN")
    assert first.status == "FILLED"
    second = app.execution.submit(ticket(mode=mode), phase="OPEN")
    assert second.status == "REJECTED"
    broker = app.real if mode == "REAL" else app.paper
    assert len(broker.sent) == 1
    assert app.trade_state.get(first.trade_id).open_quantity == 100


def test_pending_buy_reserves_cash_without_double_subtracting_broker_cash(tmp_path):
    app = app_at(tmp_path, cash=100e6)
    pending = app.queue.add(ticket(quantity=100))
    assert app._check_manual_buy_capital(ticket(), {})
    # Once accepted, broker cash already includes its 8.008m reservation.
    app.queue._update(pending.id, status="WORKING", broker_order_id="DNSE-1")
    app.paper.cash = 100e6 - 8_008_000
    feedback = app._manual_buy_capital_feedback("MSN", "PAPER", 100, "LO", 60,
                                               tick={}, balance=app.paper.get_balance(), positions=[])
    assert not feedback["reason"]


def test_partial_fill_preserves_holding_and_only_reserves_unfilled_remainder(tmp_path):
    app = app_at(tmp_path, cash=100e6)
    cycle = app.trade_state.create("MSN", "PAPER", trade_id="partial")
    cycle.record_buy_fill(100, 50, 5000)
    app.trade_state.save(cycle)
    app.paper.positions = [{"symbol": "MSN", "openQuantity": 100, "costPrice": 50}]
    pending = app.queue.add(ticket(quantity=200, price=50))
    app.queue._update(pending.id, status="PARTIAL", broker_order_id="DNSE-1", filled_quantity=100, remaining_quantity=100)
    feedback = app._manual_buy_capital_feedback("MSN", "PAPER", 100, "LO", 50,
                                               tick={}, balance=app.paper.get_balance(), positions=app.paper.positions)
    assert feedback["reason"]  # 5.005m holding + 5.005m pending + 5.005m new > 15m.
    assert "10,010,000 đ" in feedback["hint"]


def test_fee_is_included_and_typed_quantity_can_fit_at_lo_instead_of_ceiling(tmp_path):
    app = app_at(tmp_path, cash=100e6)
    assert not app._check_manual_buy_capital(ticket(quantity=200, price=74.2), {})
    assert app._check_manual_buy_capital(ticket(quantity=200, price=75), {})  # Shares alone =15m; fee exceeds cap.
    assert app._check_manual_buy_capital(ticket(quantity=200, kind="MARKET"), {})


def test_usage_savings_and_unassigned_pool_cannot_be_borrowed(tmp_path):
    app = app_at(tmp_path, use_pct=50)
    assert app._check_manual_buy_capital(ticket(price=80), {})  # Per-ticket limit =7.5m.
    app.settings.priority_allocations["IDC"]["limit_vnd"] = 5e6
    assert app._check_manual_buy_capital(ticket("FPT", price=1), {})  # Includes unassigned 10m.


def test_book_isolation_and_missing_snapshot_fail_closed(tmp_path):
    app = app_at(tmp_path)
    app.paper.cash = 100e6
    assert app._check_manual_buy_capital(ticket("FPT", quantity=100, price=100, mode="REAL"), {})
    assert not app._check_manual_buy_capital(ticket("FPT", quantity=100, price=100), {})
    app.snapshots["PAPER"] = ({}, [], [])
    assert app._manual_buy_capital_feedback("MSN", "PAPER", 100, "LO", 80, tick={})["reason"] == "CHỜ DỮ LIỆU VỐN"


def test_private_off_reserves_rule_envelopes_but_no_priority_leaves_manual_unchanged(tmp_path):
    app = app_at(tmp_path)
    app.settings.priority_capital_enabled = False
    app.settings.rule_parameters["max_positions"] = 4
    assert app._check_manual_buy_capital(ticket("FPT"), {})
    app.settings.priority_symbols = []
    assert not app._check_manual_buy_capital(ticket("FPT"), {})


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_cached_buy_is_rechecked_against_new_setting_at_release(tmp_path, mode):
    app = app_at(tmp_path, cash=100e6, mode=mode)
    intent = app.execution.submit(ticket(mode=mode), phase="CLOSED", process_immediately=False)
    assert intent.status == "PENDING"
    app.settings.priority_allocations["MSN"]["limit_vnd"] = 5e6
    app.execution.process_due(phase="OPEN", execution_mode=mode)
    assert app.queue.get(intent.id).status == "REJECTED"
    assert not (app.real if mode == "REAL" else app.paper).sent


def test_manual_guard_does_not_restrict_sell_or_bot_order(tmp_path):
    app = app_at(tmp_path)
    calls = []
    app.execution.manual_buy_guard = lambda intent, quote: calls.append(intent.id) or "BLOCKED"
    app.paper.positions = [{"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 100, "costPrice": 80}]
    sell = app.execution.submit(ticket("FPT", mode="PAPER", side="SELL"), phase="OPEN")
    bot = app.execution.submit(ticket(source="BOT"), phase="OPEN")
    assert sell.status == bot.status == "FILLED"
    assert not calls


def test_guard_failure_never_hands_off_and_is_audited(tmp_path):
    app = app_at(tmp_path)
    app.execution.manual_buy_guard = lambda *_: (_ for _ in ()).throw(ValueError("missing snapshot"))
    intent = app.execution.submit(ticket(), phase="OPEN")
    assert intent.status == "REJECTED"
    assert not app.paper.sent
    assert CSVOrderJournal(tmp_path / "order_history.csv").read_all()


def test_holding_snapshot_and_durable_fill_are_not_counted_twice(tmp_path):
    app = app_at(tmp_path, cash=100e6)
    cycle = app.trade_state.create("MSN", "PAPER", trade_id="confirmed")
    cycle.record_buy_fill(100, 80, 8000)
    app.trade_state.save(cycle)
    app.paper.positions = [{"symbol": "MSN", "openQuantity": 100, "costPrice": 80}]
    feedback = app._manual_buy_capital_feedback("MSN", "PAPER", 100, "LO", 60,
                                               tick={}, balance=app.paper.get_balance(), positions=app.paper.positions)
    assert not feedback["reason"]
    assert "8,008,000 đ" in feedback["hint"]


def test_other_book_pending_buy_does_not_consume_current_cap(tmp_path):
    app = app_at(tmp_path)
    app.queue.add(ticket(quantity=200, mode="REAL"))
    assert not app._check_manual_buy_capital(ticket(mode="PAPER"), {})


def test_cached_ticket_edit_and_changed_cash_are_checked_at_hand_off(tmp_path):
    app = app_at(tmp_path)
    intent = app.execution.submit(ticket(), phase="CLOSED", process_immediately=False)
    app.queue.replace_local(intent.id, quantity=200, limit_price=80)
    app.execution.process_due(phase="OPEN", execution_mode="PAPER")
    assert app.queue.get(intent.id).status == "REJECTED"
    assert not app.paper.sent
    next_intent = app.execution.submit(ticket(), phase="CLOSED", process_immediately=False)
    app.paper.cash = 40e6  # The UI snapshot still says 60m.
    app.execution.process_due(phase="OPEN", execution_mode="PAPER")
    assert app.queue.get(next_intent.id).status == "REJECTED"
    assert not app.paper.sent


def test_queued_batch_never_exceeds_one_envelope(tmp_path):
    app = app_at(tmp_path, cash=100e6)
    intents = [app.execution.submit(ticket(), phase="CLOSED", process_immediately=False) for _ in range(2)]
    app.execution.process_due(phase="OPEN", execution_mode="PAPER")
    assert len(app.paper.sent) == 1
    assert sorted(app.queue.get(item.id).status for item in intents) == ["FILLED", "REJECTED"]


@pytest.mark.parametrize("auto", [False, True])
def test_click_buy_blocks_before_enqueue_and_reports_inline(tmp_path, auto):
    app = app_at(tmp_path)
    app.symbol = SimpleNamespace(get=lambda: "FPT")
    app.order_type = SimpleNamespace(get=lambda: "LO")
    app.price = SimpleNamespace(get=lambda: "100000")
    app.quantity = SimpleNamespace(get=lambda: "" if auto else "300")
    app._suggested_order_quantity = lambda *_: (300, 30e6, False)
    app._shared_tick = lambda *_: {"price": 100}
    app._refresh_local = lambda: None
    messages = []
    app._log = lambda text, target="manual": messages.append((text, target))
    app._submit("BUY")
    assert not app.queue.list_all()
    assert "THIẾU VỐN SAU KHI GIỮ PRIORITY" in app._manual_order_notice["hint"]
    assert messages[-1][1] == "manual"
