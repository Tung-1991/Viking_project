"""Order grouping and cancellation checks using fake snapshots, no broker IO."""
from copy import deepcopy
from types import SimpleNamespace
from tkinter import ttk

import pytest

from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.tables import DashboardTablesMixin
from viking_v2.models import BrokerOrderResult, OrderIntent, TradeCycle
from viking_v2.trading.orders import OrderQueue


def partial_subject(root, mode="REAL"):
    intent = OrderIntent(
        "buy-500", "FPT", "BUY", 500, "MARKET", execution_mode=mode,
        source="BOT", trade_id="T1", loan_package_id="1", status="PARTIAL",
        broker_order_id="B1", filled_quantity=400, remaining_quantity=100,
    )
    cycle = TradeCycle(
        "T1", "FPT", mode, source="BOT", loan_package_id="1",
        entry_quantity=400, open_quantity=400, avg_entry_price=100,
    )
    position = {
        "symbol": "FPT", "tradeId": "T1", "loanPackageId": "1",
        "openQuantity": 400, "tradeQuantity": 0,
        "costPrice": 100000, "marketPrice": 100000, "price_unit": "VND",
    }
    tree = ttk.Treeview(root, show="headings")
    subject = DashboardTablesMixin()
    subject.trees = {mode: tree}
    subject._running_row_actions = {}
    items, cycles = [intent], [cycle]
    subject.queue = SimpleNamespace(list_all=lambda: list(items))
    subject.snapshots = {mode: ({}, [position], [])}
    subject.settings = SimpleNamespace(rule_parameters={}, paper_mode=mode == "PAPER")
    subject.trade_state = SimpleNamespace(list_cycles=lambda: list(cycles))
    subject.rule_state = SimpleNamespace(position_metrics=lambda *_: {})
    subject._cached_fee_rate = lambda *_: 0.0
    subject._preview_buy_fee = lambda *_: 0.0
    subject._row_time = lambda value: str(value)
    subject._sync_cancel_button = lambda: None
    return subject, tree, intent, cycle, position, items, cycles


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_partial_market_buy_groups_under_position_without_counting_pending_as_owned(ui_root, mode):
    subject, tree, intent, cycle, position, _items, _cycles = partial_subject(ui_root, mode)
    before = (deepcopy(intent.to_dict()), deepcopy(cycle.to_dict()), deepcopy(position))
    try:
        subject._render_tables({})
        parent, = tree.get_children()
        child, = tree.get_children(parent)
        assert child == "LOCAL:buy-500"
        assert "tree" in tuple(map(str, tree["show"]))
        assert tree.item(parent, "open")
        assert "KL 400" in tree.item(parent, "values")[2]
        assert "BÁN ĐƯỢC 0 CP · CHỜ VỀ 400 CP" in tree.item(parent, "values")[7]
        assert "KL 500" in tree.item(child, "values")[2]
        assert "Khớp 400/500 · CÒN 100 CHỜ KHỚP" in tree.item(child, "values")[7]
        actions = subject._running_row_actions[mode]
        assert actions[parent]["kind"] == "position" and not actions[parent]["cancellable"]
        assert actions[child]["broker_order_id"] == "B1"
        assert actions[child]["cancellable"] == (mode == "REAL")
        assert before == (intent.to_dict(), cycle.to_dict(), position)
    finally:
        tree.destroy()


@pytest.mark.parametrize("mismatch", ["trade", "package", "ambiguous", "unbound"])
def test_order_is_not_grouped_with_unverified_or_ambiguous_position(ui_root, mismatch):
    subject, tree, intent, _cycle, position, _items, _cycles = partial_subject(ui_root)
    if mismatch == "trade":
        intent.trade_id = "different-trade"
    elif mismatch == "package":
        intent.loan_package_id = "2"
    elif mismatch == "ambiguous":
        intent.loan_package_id = ""
        subject.snapshots["REAL"][1].append({**position, "loanPackageId": "2"})
    else:
        intent.trade_id = ""
    try:
        subject._render_tables({})
        assert tree.parent("LOCAL:buy-500") == ""
        assert "LOCAL:buy-500" in tree.get_children()
    finally:
        tree.destroy()


def test_collapsed_group_keeps_child_selection_and_updates_cancel_permission(ui_root, monkeypatch):
    subject, tree, intent, _cycle, _position, _items, _cycles = partial_subject(ui_root)
    try:
        subject._render_tables({})
        parent, = tree.get_children()
        child, = tree.get_children(parent)
        tree.selection_set(child)
        tree.focus(child)
        tree.item(parent, open=False)
        monkeypatch.setattr(tree, "insert", lambda *_args, **_kwargs: pytest.fail("reinserted child"))
        monkeypatch.setattr(tree, "delete", lambda *_args: pytest.fail("deleted child"))
        for _ in range(3):
            subject._render_tables({})
        intent.status = "CANCEL_PENDING"
        subject._render_tables({})
        assert not tree.item(parent, "open")
        assert tree.selection() == (child,) and tree.focus() == child
        assert tree.get_children(parent) == (child,)
        assert "CHỜ XÁC NHẬN HỦY" in tree.item(child, "values")[7]
        assert "CÒN 100" in tree.item(child, "values")[7]
        assert not subject._running_row_actions["REAL"][child]["cancellable"]
    finally:
        tree.destroy()


def test_child_stays_visible_when_position_snapshot_disappears_then_returns(ui_root):
    subject, tree, intent, _cycle, position, _items, _cycles = partial_subject(ui_root)
    try:
        subject._render_tables({})
        child = "LOCAL:buy-500"
        tree.selection_set(child)
        subject.snapshots["REAL"] = ({}, [], [])
        subject._render_tables({})
        assert tree.get_children() == (child,)
        assert tree.selection() == (child,)
        assert subject._running_row_actions["REAL"][child]["broker_order_id"] == "B1"
        subject.snapshots["REAL"] = ({}, [position], [])
        subject._render_tables({})
        parent, = tree.get_children()
        assert tree.get_children(parent) == (child,)
        assert tree.selection() == (child,)
        assert tree.item(parent, "open")
        intent.status = "FILLED"
        subject.snapshots["REAL"] = ({}, [], [])
        subject._render_tables({})
        assert not tree.get_children() and not tree.exists(child)
        assert not tree.selection()
    finally:
        tree.destroy()


@pytest.mark.parametrize("status", ["FILLED", "CANCELLED"])
def test_final_child_is_removed_while_confirmed_position_stays(ui_root, status):
    subject, tree, intent, _cycle, position, _items, _cycles = partial_subject(ui_root)
    try:
        subject._render_tables({})
        parent, = tree.get_children()
        tree.selection_set(parent, "LOCAL:buy-500")
        intent.status = status
        if status == "FILLED":
            intent.filled_quantity, intent.remaining_quantity = 500, 0
            position["openQuantity"] = 500
        subject._render_tables({})
        assert tree.get_children() == (parent,)
        assert not tree.get_children(parent) and not tree.exists("LOCAL:buy-500")
        assert tree.selection() == (parent,)
        assert set(subject._running_row_actions["REAL"]) == {parent}
        assert f"KL {500 if status == 'FILLED' else 400}" in tree.item(parent, "values")[2]
    finally:
        tree.destroy()


def test_settlement_wait_is_not_presented_as_unfilled_order(ui_root):
    subject, tree, _intent, _cycle, position, items, _cycles = partial_subject(ui_root)
    items.clear()
    position.update(openQuantity=500, tradeQuantity=400)
    try:
        subject._render_tables({})
        parent, = tree.get_children()
        text = tree.item(parent, "values")[7]
        assert "BÁN ĐƯỢC 400 CP · CHỜ VỀ 100 CP" in text
        assert "CHỜ KHỚP" not in text
        assert not tree.get_children(parent)
        assert not subject._running_row_actions["REAL"][parent]["cancellable"]
    finally:
        tree.destroy()


def test_multiple_children_reorder_without_losing_selection(ui_root):
    subject, tree, intent, _cycle, _position, items, _cycles = partial_subject(ui_root)
    another = OrderIntent.from_dict({**intent.to_dict(), "id": "another", "broker_order_id": "B2"})
    items.append(another)
    try:
        subject._render_tables({})
        parent, = tree.get_children()
        assert tree.get_children(parent) == ("LOCAL:another", "LOCAL:buy-500")
        tree.selection_set("LOCAL:buy-500")
        items.reverse()
        subject._render_tables({})
        assert tree.get_children(parent) == ("LOCAL:buy-500", "LOCAL:another")
        assert tree.selection() == ("LOCAL:buy-500",)
        assert subject._running_row_actions["REAL"]["LOCAL:buy-500"]["broker_order_id"] == "B1"
    finally:
        tree.destroy()


def test_cancel_selected_child_targets_original_broker_order_and_keeps_filled_stock(
    ui_root, tmp_path, monkeypatch,
):
    import viking_v2.dashboard.actions as actions_module

    subject, tree, intent, cycle, position, _items, _cycles = partial_subject(ui_root)
    queue = OrderQueue(tmp_path / "orders.json")
    queue.add(intent)
    subject.queue = queue
    cancelled = []

    def cancel_order(order_id):
        cancelled.append(order_id)
        return BrokerOrderResult(True, "Cancelled", order_id=order_id)

    subject.real = SimpleNamespace(has_trading_token=lambda: True, cancel_order=cancel_order)
    subject.tabs = SimpleNamespace(get=lambda: "CKCS REAL")
    subject._io_executor = SimpleNamespace(submit=lambda callback: callback())
    subject._post_ui = lambda callback: callback()
    subject._log = lambda *_: None
    subject._refresh_local = lambda: None
    monkeypatch.setattr(actions_module.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    try:
        subject._render_tables({})
        parent, = tree.get_children()
        tree.selection_set(parent)
        DashboardActionsMixin._cancel_selected(subject)
        assert cancelled == []
        tree.selection_set("LOCAL:buy-500")
        DashboardActionsMixin._cancel_selected(subject)
        assert cancelled == ["B1"]
        pending = queue.get(intent.id)
        assert pending.status == "CANCEL_PENDING"
        assert pending.filled_quantity == 400 and pending.remaining_quantity == 100
        assert cycle.open_quantity == position["openQuantity"] == 400
        subject._render_tables({})
        DashboardActionsMixin._cancel_selected(subject)
        assert cancelled == ["B1"]
    finally:
        tree.destroy()
