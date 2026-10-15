"""A cancelled zero-fill BUY is passive until an explicit manual activation."""
from copy import deepcopy
from concurrent.futures import Future
from datetime import datetime
from types import SimpleNamespace
from tkinter import ttk

import pytest

from support.tests.test_buy_remainder import Broker, timestamp
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.buy_watch import show_arm_buy
from support.tests.test_demo_order_display import table_subject
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.buy_watch import watch_visible, watch_armable
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder, priority_entry_orders
from viking_v2.trading.state import TradeStateStore


@pytest.fixture(params=["REAL", "PAPER"])
def watch(tmp_path, monkeypatch, request):
    clock = [timestamp(16)]
    monkeypatch.setattr("viking_v2.trading.execution.time.time", lambda: clock[0])
    broker = Broker()
    queue, trades = OrderQueue(tmp_path / "orders.json", now=lambda: clock[0]), TradeStateStore(tmp_path / "trades.json")
    rules = RuleStateStore(tmp_path / "rules.json")
    dates = ["2026-10-16", "2026-10-20", "2026-10-21"]
    service = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"),
        trade_state=trades, rule_state=rules, working_dates_provider=lambda: dates,
        bot_buy_allowed_provider=lambda mode: False)
    intent = queue.add(OrderIntent("ZERO-CTS", "CTS", "BUY", 300, "MARKET", source="BOT",
        execution_mode=request.param, trade_id="OLD-ZERO", created_at=clock[0],
        em_modes=["TP", "NORMAL", "IND_EXIT"], entry_market_state="UPTREND", entry_exposure=0.9))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", handed_off_at=clock[0])
    row = {"orderId": "B1", "symbol": "CTS", "side": "NB", "quantity": 300, "fillQuantity": 0,
           "averagePrice": 0, "price": 0, "orderType": "MTL", "orderStatus": "Cancelled", "price_unit": "VND"}
    broker.orders, broker.history = [deepcopy(row)], [{**row, "orderId": "20261016_B1"}]
    service.reconcile_working(request.param)
    return SimpleNamespace(clock=clock, broker=broker, queue=queue, trades=trades, rules=rules, dates=dates,
                           service=service, intent=intent, row=row, mode=request.param)


def arm(watch, **kwargs):
    return watch.service.arm_zero_fill_buy(watch.intent.id, quantity=kwargs.get("quantity", 300),
        limit_price=kwargs.get("limit_price", 20.45))


def open_next_session(watch):
    watch.clock[0] = timestamp(20, "09:20")
    watch.broker.orders = []
    watch.service.reconcile_working(watch.mode)


def count(watch):
    return priority_entry_orders("CTS", watch.mode, watch.trades, watch.queue, True,
                                 ["CTS"], {"CTS": {"max_orders": 1}})


def test_passive_record_reserves_no_cash_slot_or_entry_and_never_posts(watch):
    p = watch
    item = p.queue.get(p.intent.id)
    assert watch_armable(item, p.clock[0]) and item.status == "CANCELLED"
    assert not p.queue.find_active("CTS", execution_mode=p.mode) and count(p)["entry_orders_used"] == 0
    builder = PortfolioContextBuilder(p.queue, p.trades, p.rules)
    context = builder.build("CTS", execution_mode=p.mode, balance={"equity": 20_000_000, "availableCash": 20_000_000},
        positions=[], tick={"price": 20.45}, exposure=0.9, max_positions=1, budget_only=True)
    assert context["pending_buy_value"] == context["pending_buy_cash"] == 0
    open_next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=True)
    assert not p.broker.sent and not p.trades.list_cycles()


def test_passive_record_does_not_block_a_new_automatic_buy_signal(watch):
    p = watch
    planner = StrategyOrderPlanner(p.queue, p.trades, p.rules)
    result = planner.plan(StrategyDecision("BUY", "CTS", "BUY_SIGNAL", signal="BUY"),
        execution_mode=p.mode, execution_style="MARKET", tick={"ask": 20.45},
        portfolio={"order_budget": 7_000_000}, candle_key="fresh-signal")
    assert result.intent and result.intent.id != p.intent.id
    with pytest.raises(ValueError, match="lệnh chờ"):
        arm(p)


def test_watch_lasts_through_weekend_and_holiday_then_disappears(watch):
    p = watch
    for day in (17, 18, 19, 20):
        p.clock[0] = timestamp(day, "10:00")
        p.queue.expire()
        assert watch_visible(p.queue.get(p.intent.id), p.clock[0])
    p.clock[0] = timestamp(20, "15:00")
    p.queue.expire()
    assert p.queue.get(p.intent.id) is None


def test_explicit_activation_is_one_new_manual_buy_and_waits_for_next_session(watch):
    p = watch
    intent = arm(p)
    assert intent.id != p.intent.id and intent.trade_id != p.intent.trade_id
    assert intent.source == "MANUAL" and intent.order_type == "LO" and intent.limit_price == 20.45
    assert intent.buy_window_date == "2026-10-20" and intent.buy_window_start == "09:00"
    assert intent.em_modes == ["TP", "NORMAL", "IND_EXIT"] and count(p)["entry_orders_used"] == 1
    assert p.rules.active_telegram_signal("CTS", p.mode)["id"] == intent.trade_id
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    assert not p.broker.sent and p.queue.get(intent.id).status == "PENDING"
    with pytest.raises(ValueError, match="đã được bật mua"):
        arm(p)


def test_operator_armed_buy_executes_before_14_with_bot_off_and_has_one_managed_entry(watch):
    p = watch
    intent = arm(p)
    open_next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    sent, = p.broker.sent
    assert sent.id == intent.id and sent.quantity == 300 and sent.order_type == "LO" and sent.limit_price == 20.45
    p.broker.orders = [{**p.row, "orderId": "B2", "fillQuantity": 300, "averagePrice": 20450,
                        "price": 20450, "orderType": "LO", "orderStatus": "Filled"}]
    p.service.reconcile_working(p.mode)
    cycle = p.trades.get(intent.trade_id)
    assert cycle.source == "BOT" and cycle.open_quantity == 300 and cycle.entry_order_ids == [intent.id]
    assert cycle.em_modes == ["TP", "NORMAL", "IND_EXIT"] and cycle.sl_enabled
    assert count(p)["entry_orders_used"] == 1 and p.queue.get(p.intent.id).filled_quantity == 0
    assert p.rules.claim_closed_telegram_signal("CTS", intent.trade_id, p.mode)["id"] == intent.trade_id


def test_manual_authorization_survives_restart_without_turning_bot_on(watch):
    p = watch
    intent = arm(p)
    p.queue.discard_unsubmitted_bot_buys("Restart")
    p.queue.recover_claims()
    assert p.queue.get(intent.id).status == "PENDING"
    p.queue._update(intent.id, status="SENDING", details={**intent.details, "claim_protocol": "durable-v1"})
    p.queue.recover_claims()
    assert p.queue.get(intent.id).status == "PENDING"


@pytest.mark.parametrize("quantity,price", [(0, 20.45), (150, 20.45), (300, 0), (300, float("nan"))])
def test_activation_rejects_invalid_reviewed_values(watch, quantity, price):
    with pytest.raises(ValueError):
        arm(watch, quantity=quantity, limit_price=price)
    assert len(watch.queue.list_all()) == 1 and not watch.broker.sent


def test_unresolved_original_or_late_fill_prevents_activation(watch):
    p = watch
    p.queue._update(p.intent.id, details={"buy_watch": p.queue.get(p.intent.id).details["buy_watch"]})
    p.broker.orders, p.broker.history = [], []
    p.service._finished_histories = {}
    with pytest.raises(ValueError, match="Chưa xác nhận"):
        arm(p)
    assert not p.broker.sent


def test_late_original_fill_after_arming_prevents_duplicate_purchase(watch):
    p = watch
    intent = arm(p)
    p.broker.history[0].update(fillQuantity=100, averagePrice=20450)
    open_next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    assert not p.broker.sent and p.queue.get(intent.id).status == "CANCELLED"
    assert p.trades.get(p.intent.trade_id).open_quantity == 100


def test_armed_buy_still_passes_current_capital_guard(watch):
    p = watch
    intent = arm(p)
    open_next_session(p)
    p.service.bot_entry_guard = lambda item, quote: "PRIORITY_CAPITAL_LIMIT"
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    assert not p.broker.sent and p.queue.get(intent.id).status == "CANCELLED"


def test_expired_armed_buy_does_not_create_another_passive_retry(watch):
    p = watch
    intent = arm(p)
    p.clock[0] = timestamp(20, "15:00")
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    item = p.queue.get(intent.id)
    assert item.status == "EXPIRED" and "buy_watch" not in item.details and not p.broker.sent


def test_passive_watch_row_and_right_click_offer_explicit_activation(ui_root, watch, monkeypatch):
    p = watch
    class Menu:
        _viking_icons = [None, None]
        def __init__(self): self.commands = []
        def delete(self, *args): self.commands = []
        def add_command(self, **kwargs): self.commands.append(kwargs)
        def post(self, *args): pass
    menu = Menu()
    subject, tree = table_subject(ui_root, p.mode, items=p.queue.list_all())
    try:
        subject._render_tables({})
        iid, = tree.get_children()
        text = tree.item(iid, "values")[7]
        assert "LƯU THAM KHẢO" in text and "CHƯA BẬT MUA" in text and "KHÔNG GIỮ VỐN/LƯỢT MUA" in text
        assert tree.item(iid, "values")[4] == "LƯU ĐẾN 20/10 15:00"
        action = subject._running_row_actions[p.mode][iid]
        assert action["armable"] and not action["cancellable"] and not action["editable"]
        tree.selection_set(iid)
        monkeypatch.setattr(tree, "identify_row", lambda y: iid)
        monkeypatch.setattr(DashboardActionsMixin, "_running_menu", lambda app, widget: menu)
        subject._arm_running_buy = lambda chosen: chosen
        DashboardActionsMixin._running_right_click(subject, SimpleNamespace(widget=tree, y=1, x_root=0, y_root=0))
        command = next(row for row in menu.commands if row["label"] == "Bật mua…")
        assert command["state"] == "normal" and command["command"]() == action
    finally:
        tree.destroy()


def test_next_day_reused_broker_id_is_not_hidden_by_old_passive_record(ui_root, watch, monkeypatch):
    p = watch
    open_next_session(p)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.fromtimestamp(p.clock[0], tz)
    monkeypatch.setattr("viking_v2.dashboard.tables.datetime", Clock)
    row = {"orderId": "B1", "symbol": "MSN", "side": "NB", "quantity": 100,
           "price": 73000, "orderType": "LO", "orderStatus": "New"}
    subject, tree = table_subject(ui_root, p.mode, items=p.queue.list_all(), orders=[row])
    try:
        subject._render_tables({})
        assert set(tree.get_children()) == {"LOCAL:ZERO-CTS", f"BROKER:{p.mode}:B1"}
    finally:
        tree.destroy()


def test_partial_fill_of_armed_manual_buy_keeps_same_entry_for_remainder(watch):
    p = watch
    intent = arm(p)
    open_next_session(p)
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    p.broker.orders = [{**p.row, "orderId": "B2", "fillQuantity": 100, "averagePrice": 20450,
                       "price": 20450, "orderType": "LO", "orderStatus": "Expired"}]
    p.service.reconcile_working(p.mode)
    assert p.queue.get(intent.id).status == "WAITING_REMAINDER"
    p.clock[0] = timestamp(21, "09:20")
    p.broker.orders = []
    def history(start, end):
        return [{**p.row, "orderId": "20261020_B2", "fillQuantity": 100, "averagePrice": 20450,
                 "price": 20450, "orderType": "LO", "orderStatus": "Expired"}] if start == "2026-10-20" else deepcopy(p.broker.history)
    p.broker.get_order_history = history
    p.service.reconcile_working(p.mode)
    p.service.process_due(phase="OPEN", execution_mode=p.mode, allow_bot_buys=False)
    assert [row.quantity for row in p.broker.sent] == [300, 200]
    assert {row.id for row in p.broker.sent} == {intent.id} and count(p)["entry_orders_used"] == 1


def test_popup_only_arms_after_explicit_button_press(ui_root, watch, monkeypatch):
    import customtkinter as ctk
    p = watch
    def submit(fn):
        future = Future()
        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)
        return future
    for name, value in {
        "queue": p.queue, "execution": p.service, "_shared_tick": lambda symbol: {"price": 20.45},
        "settings": SimpleNamespace(bot_em_modes=["TP", "NORMAL", "IND_EXIT"], bot_sl_enabled=True),
        "_io_executor": SimpleNamespace(submit=submit), "_post_ui": lambda callback: callback(),
        "_refresh_local": lambda: None, "_log": lambda message: None,
    }.items():
        monkeypatch.setattr(ui_root, name, value, raising=False)
    before = set(ui_root.winfo_children())
    show_arm_buy(ui_root, {"local_id": p.intent.id})
    top, = [child for child in ui_root.winfo_children() if child not in before]
    top.withdraw()
    try:
        assert len(p.queue.list_all()) == 1 and not p.broker.sent
        button = next(child for child in top.winfo_children() if isinstance(child, ctk.CTkButton)
                      and child.cget("text") == "BẬT MUA TAY")
        button.invoke()
        assert len(p.queue.list_all()) == 2 and not p.broker.sent
        new = next(item for item in p.queue.list_all() if item.id != p.intent.id)
        assert new.source == "MANUAL" and new.quantity == 300 and new.limit_price == 20.45
    finally:
        if top.winfo_exists():
            top.destroy()
