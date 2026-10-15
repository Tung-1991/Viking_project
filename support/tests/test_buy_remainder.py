"""Offline BUY lifecycle: final broker quantities, one extra session, one entry."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import BrokerOrderResult, OrderIntent
from viking_v2.rules.state import RuleStateStore
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


def timestamp(day, clock="14:00"):
    return datetime.fromisoformat(f"2026-10-{day}T{clock}:00").replace(tzinfo=VN_TZ).timestamp()


class Broker:
    def __init__(self):
        self.orders, self.history, self.sent = [], [], []
        self.history_error = False
        self.detail_calls = []

    def has_trading_token(self):
        return True

    def get_orders(self, **kwargs):
        return deepcopy(self.orders)

    def get_order_history(self, start, end):
        assert start == end == "2026-10-16"
        if self.history_error:
            raise OSError("OFFLINE_HISTORY_UNAVAILABLE")
        return deepcopy(self.history)

    def get_order_detail(self, order_id):
        self.detail_calls.append(order_id)
        return None

    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        return BrokerOrderResult(True, "New", order_id="B2", raw={"fillQuantity": 0})


@pytest.fixture(params=["REAL", "PAPER"])
def purchase(tmp_path, monkeypatch, request):
    clock = [timestamp(16)]
    monkeypatch.setattr("viking_v2.trading.execution.time.time", lambda: clock[0])
    broker = Broker()
    queue = OrderQueue(tmp_path / "orders.json", now=lambda: clock[0])
    trades = TradeStateStore(tmp_path / "trades.json")
    calendar = ["2026-10-16", "2026-10-20", "2026-10-21"]  # Weekend and Monday holiday.
    service = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"),
                               trade_state=trades, working_dates_provider=lambda: calendar)
    intent = queue.add(OrderIntent("BUY-CTS", "CTS", "BUY", 300, "LO", limit_price=20.45,
                                   source="BOT", execution_mode=request.param, trade_id="CTS-TRADE",
                                   created_at=clock[0], buy_window_start="14:00", buy_window_end="14:30",
                                   buy_window_date="2026-10-16",
                                   details={"session_ema_cross": {"session": True}}))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=300,
                  handed_off_at=clock[0])
    row = {"orderId": "B1", "symbol": "CTS", "side": "NB", "quantity": 300,
           "fillQuantity": 100, "averagePrice": 20450, "price": 20450,
           "orderType": "LO", "orderStatus": "PartiallyFilled", "price_unit": "VND"}
    broker.orders = [deepcopy(row)]
    service.reconcile_working(request.param)
    assert trades.get(intent.trade_id).open_quantity == 100
    return SimpleNamespace(clock=clock, broker=broker, queue=queue, trades=trades, service=service,
                           intent=intent, row=row, mode=request.param, calendar=calendar)


def expire(purchase):
    purchase.clock[0] = timestamp(16, "18:00")
    purchase.broker.orders = [{**purchase.row, "orderStatus": "Expired"}]
    purchase.service.reconcile_working(purchase.mode)
    assert purchase.queue.get(purchase.intent.id).status == "WAITING_REMAINDER"


def next_session(purchase, **changes):
    purchase.clock[0] = timestamp(20, "09:20")
    purchase.broker.orders = []
    purchase.broker.history = [{**purchase.row, "orderId": "20261016_B1", "orderStatus": "Expired", **changes}]
    purchase.service.reconcile_working(purchase.mode)


def test_reconciles_late_fill_and_buys_only_remainder_before_14_without_extra_entry(purchase):
    p = purchase
    expire(p)
    next_session(p, fillQuantity=200)
    ready = p.queue.get(p.intent.id)
    assert ready.status == "PENDING" and ready.remaining_quantity == 100
    assert ready.details["buy_remainder"] and not ready.buy_window_start
    assert "session_ema_cross" not in ready.details
    assert ready.order_type == "LO" and ready.limit_price == 20.45
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    sent, = p.broker.sent
    assert sent.quantity == 100 and sent.limit_price == 20.45 and sent.order_type == "LO"
    assert sent.id == p.intent.id and sent.attempt == 2
    p.broker.orders = [{**p.row, "orderId": "B2", "quantity": 100, "fillQuantity": 100, "orderStatus": "Filled"}]
    p.service.reconcile_working(p.mode)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert p.queue.get(p.intent.id).status == "FILLED"
    cycle = p.trades.get(p.intent.trade_id)
    assert cycle.open_quantity == 300 and cycle.entry_order_ids == [p.intent.id]
    assert len(p.broker.sent) == 1


@pytest.mark.parametrize("history", ["missing", "working", "failed", "missing_quantity", "missing_price"])
def test_unconfirmed_old_order_never_authorizes_a_new_post(purchase, history):
    p = purchase
    expire(p)
    p.clock[0] = timestamp(20, "09:20")
    p.broker.orders = [{**p.row, "orderId": "B1", "orderStatus": "Filled", "fillQuantity": 300}]
    p.broker.history = [] if history == "missing" else [{**p.row, "orderId": "20261016_B1",
        "orderStatus": "PartiallyFilled" if history == "working" else "Expired"}]
    p.broker.history_error = history == "failed"
    if history == "missing_quantity":
        p.broker.history[0].pop("fillQuantity")
    if history == "missing_price":
        p.broker.history[0].update(price=0, averagePrice=0)
    p.service.reconcile_working(p.mode)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and not p.broker.detail_calls
    assert p.queue.get(p.intent.id).filled_quantity == 100


def test_remainder_survives_restart_but_expires_after_one_extra_trading_day(purchase):
    p = purchase
    expire(p)
    next_session(p)
    p.queue.discard_unsubmitted_bot_buys("Restart")
    assert p.queue.get(p.intent.id).status == "PENDING"
    p.clock[0] = timestamp(21, "09:20")
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and p.queue.get(p.intent.id).status == "EXPIRED"


def test_off_prevents_buy_remainder(purchase):
    p = purchase
    expire(p)
    next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    assert not p.broker.sent and p.queue.get(p.intent.id).status == "CANCELLED"
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent


def test_sale_closes_remainder_instead_of_buying_back(purchase):
    p = purchase
    expire(p)
    p.trades.record_sell_fill(p.intent.trade_id, 100, 20.45)
    next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and p.queue.get(p.intent.id).status == "EXPIRED"


def test_missing_calendar_never_assumes_weekdays(purchase):
    p = purchase
    expire(p)
    p.calendar.clear()
    next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and p.queue.get(p.intent.id).status == "WAITING_REMAINDER"


def test_remainder_bypasses_new_entry_quota_but_keeps_capital_guard(purchase):
    p = purchase
    expire(p)
    next_session(p)
    subject = DashboardActionsMixin()
    subject.settings = AppSettings().normalize()
    subject.queue, subject.trade_state = p.queue, p.trades
    subject.paper = subject.real = p.broker
    context = {"entry_orders_available": False, "scale_in_allowed": False,
               "entry_slot_available": False, "order_budget": 10_000_000}
    subject._build_entry_limits = lambda *args, **kwargs: context
    intent = p.queue.get(p.intent.id)
    assert subject._check_bot_entry_limits(intent, {}, balance={}, positions=[]) == ""
    context["order_budget"] = 1
    assert subject._check_bot_entry_limits(intent, {}, balance={}, positions=[]) == "PORTFOLIO_EXPOSURE_LIMIT"


def test_a_fresh_buy_still_waits_until_14_and_stale_unsent_buy_expires(purchase):
    p = purchase
    next_session(p, orderStatus="PartiallyFilled")
    new = p.queue.add(OrderIntent("NEW", "MSN", "BUY", 100, "LO", limit_price=73,
                                 source="BOT", execution_mode=p.mode, created_at=p.clock[0],
                                 buy_window_start="14:00", buy_window_end="14:30", buy_window_date="2026-10-20"))
    stale = p.queue.add(OrderIntent("STALE", "HDB", "BUY", 100, "LO", limit_price=22,
                                   source="BOT", execution_mode=p.mode, created_at=timestamp(16),
                                   defer_expiry_until_eligible=True, expires_at=timestamp(21)))
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and p.queue.get(new.id).status == "PENDING"
    assert p.queue.get(stale.id).status == "EXPIRED"


def test_can_cancel_waiting_remainder_locally(purchase):
    p = purchase
    expire(p)
    assert p.queue.cancel_local(p.intent.id).status == "CANCELLED"
    next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent


@pytest.mark.parametrize("claimed", [False, True])
def test_operator_off_cancels_local_remainder_even_when_original_already_filled(purchase, claimed):
    p = purchase
    expire(p)
    if claimed:
        next_session(p)
        p.queue._update(p.intent.id, status="SENDING")
        assert p.service._cancel_unsent_order(p.queue.get(p.intent.id), "POSITION_BOT_ALERT").status == "CANCELLED"
    else:
        cancelled, broker_managed = p.service.cancel_unsubmitted_bot_buys(p.mode)
        assert [item.id for item in cancelled] == [p.intent.id] and not broker_managed
        next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent


def test_zero_fill_does_not_carry_or_reserve_a_buy_next_morning(purchase):
    p = purchase
    other = p.queue.add(OrderIntent("ZERO", "MSN", "BUY", 300, "LO", limit_price=73,
                                    execution_mode=p.mode, source="BOT", created_at=p.clock[0]))
    p.queue._update(other.id, status="WORKING", broker_order_id="ZERO-B", handed_off_at=p.clock[0])
    p.broker.orders.append({**p.row, "orderId": "ZERO-B", "symbol": "MSN", "fillQuantity": 0, "orderStatus": "Expired"})
    p.service.reconcile_working(p.mode)
    p.clock[0] = timestamp(20, "09:20")
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and not p.queue.find_active("MSN", side="BUY", execution_mode=p.mode)


def test_archived_attempt_keeps_late_fee_corrections_without_adding_stock(purchase):
    p = purchase
    expire(p)
    next_session(p)
    for fee in (1000, 2000, 1000):
        p.clock[0] += 301
        p.broker.history[0]["fee"] = fee
        p.service.reconcile_working(p.mode)
        assert p.trades.get(p.intent.trade_id).fees_paid == fee
        assert p.trades.get(p.intent.trade_id).open_quantity == 100
    assert p.queue.get(p.intent.id).remaining_quantity == 200 and not p.broker.sent
    events = p.service.csv_journal.read_all()
    assert sum(float(row.get("fee", 0) or 0) for row in events) == 1000


@pytest.mark.parametrize("broker_price", [0, 20450])
def test_mtl_partial_requires_broker_limit_price_and_never_uses_average_fill_price(purchase, broker_price):
    p = purchase
    current = p.queue.get(p.intent.id)
    details = dict(current.details)
    details.pop("remainder_limit_price", None)
    p.queue._update(p.intent.id, order_type="MARKET", limit_price=0, details=details)
    p.row.update(orderType="MTL", price=broker_price)
    p.broker.orders = [deepcopy(p.row)]
    p.service.reconcile_working(p.mode)
    p.clock[0] = timestamp(16, "18:00")
    p.broker.orders = [{**p.row, "orderStatus": "Expired"}]
    p.service.reconcile_working(p.mode)
    next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    if broker_price:
        sent, = p.broker.sent
        assert sent.order_type == "LO" and sent.quantity == 200 and sent.limit_price == 20.45
    else:
        assert not p.broker.sent


def test_a_correction_to_final_old_fill_blocks_continuation_for_reconciliation(purchase):
    p = purchase
    expire(p)
    next_session(p)
    p.clock[0] += 301
    p.broker.history[0]["fillQuantity"] = 200
    p.service.reconcile_working(p.mode)
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    assert not p.broker.sent and p.queue.get(p.intent.id).status == "UNKNOWN"
    assert p.queue.get(p.intent.id).details["fill_accounting_reconcile_required"] == "B1"


def test_broker_id_reused_next_day_has_independent_cumulative_fills(purchase):
    p = purchase
    expire(p)
    next_session(p)
    def accepted(intent):
        p.broker.sent.append(deepcopy(intent))
        return BrokerOrderResult(True, "New", order_id="B1", raw={"fillQuantity": 0})
    p.broker.place_order = accepted
    p.service.process_due(phase="OPEN", execution_mode=p.mode)
    for filled in (100, 200):
        p.broker.orders = [{**p.row, "quantity": 200, "fillQuantity": filled,
                           "orderStatus": "Filled" if filled == 200 else "PartiallyFilled"}]
        p.service.reconcile_working(p.mode)
        assert p.queue.get(p.intent.id).filled_quantity == 100 + filled
    assert p.trades.get(p.intent.trade_id).open_quantity == 300
    assert p.trades.get(p.intent.trade_id).entry_order_ids == [p.intent.id]


def test_progress_telegram_reports_cancelled_zero_fill_and_normal_expiry():
    messages = []
    telegram = SignalTelegramService(SimpleNamespace(send_message=lambda chat, text: messages.append(text)), chat_id="offline")
    intent = OrderIntent("CTS-ID", "CTS", "BUY", 300, "MARKET", source="BOT", status="CANCELLED")
    assert telegram.notify_buy_progress(intent)
    assert "ĐÃ HỦY" in messages[-1] and "Khớp 0/300" in messages[-1]
    assert "không tự mua lại" in messages[-1] and "MTL" in messages[-1]
    intent.status = "EXPIRED"
    assert telegram.notify_buy_progress(intent)
    assert "HẾT HIỆU LỰC" in messages[-1] and "lỗi hệ thống" not in messages[-1]


def test_progress_telegram_uses_durable_delivery_and_deduplicates(tmp_path, monkeypatch):
    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs
        def start(self):
            self.target(**self.kwargs)
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    messages = []
    subject = DashboardActionsMixin()
    subject.settings = AppSettings(telegram_enabled=True).normalize()
    subject.rule_state = RuleStateStore(tmp_path / "rules.json")
    subject.telegram = SignalTelegramService(SimpleNamespace(send_message=lambda chat, text: messages.append(text)), chat_id="offline")
    intent = OrderIntent("CTS-ID", "CTS", "BUY", 300, "MARKET", source="BOT", status="CANCELLED")
    for _ in range(3):
        subject._notify_buy_order_progress(intent)
    assert len(messages) == 1 and not subject.rule_state.pending_telegram_notices()
