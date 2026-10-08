"""Operator-visible order regressions, with isolated queues and no broker IO."""
from types import SimpleNamespace
import time
from tkinter import ttk

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.tables import DashboardTablesMixin
from viking_v2.dashboard.view import _active_order_notice
from viking_v2.models import OrderIntent, TradeCycle


def table_subject(root, mode="REAL", *, items=(), positions=(), orders=(), cycles=()):
    tree = ttk.Treeview(root, show="headings")
    subject = DashboardTablesMixin()
    subject.trees = {mode: tree}
    subject._running_row_actions = {}
    subject.queue = SimpleNamespace(list_all=lambda: list(items))
    subject.snapshots = {mode: ({}, list(positions), list(orders))}
    subject.settings = AppSettings(paper_mode=mode == "PAPER")
    subject.trade_state = SimpleNamespace(list_cycles=lambda: list(cycles))
    subject.rule_state = SimpleNamespace(position_metrics=lambda *_: {})
    subject._cached_fee_rate = lambda *_: 0.0
    subject._preview_buy_fee = lambda *_: 0.0
    subject._row_time = lambda value: str(value)
    subject._sync_cancel_button = lambda: None
    return subject, tree


def position(mode="REAL", *, price=11, **policy):
    cycle = TradeCycle(f"{mode}-trade", "MSN", execution_mode=mode,
                       entry_quantity=100, open_quantity=100, avg_entry_price=10, **policy)
    row = {"symbol": "MSN", "tradeId": cycle.id, "openQuantity": 100,
           "tradeQuantity": 100, "costPrice": 10, "marketPrice": price}
    return cycle, row


def decision(mode, *, state="ARM", trigger=10.5, **details):
    return {"symbol": "MSN", "signal": "SELL", "market_state": "UPTREND",
            "details": {"execution_mode": mode, "updated_at": time.time(),
                        "trade_id": f"{mode}-trade", "normal_state": state,
                        "normal_trigger_price": trigger, **details}}


@pytest.mark.parametrize("mode,expected,other", [("REAL", "ARM", "DYN"), ("PAPER", "DYN", "ARM")])
def test_position_management_display_uses_its_own_book(ui_root, mode, expected, other):
    cycle, row = position(mode, em_modes=["NORMAL", "IND_EXIT"])
    subject, tree = table_subject(ui_root, mode, positions=[row], cycles=[cycle])
    books = {"REAL": {"MSN": decision("REAL")},
             "PAPER": {"MSN": decision("PAPER", state="DYN", trigger=10.1)}}
    try:
        subject._render_tables({"decisions": books["PAPER" if mode == "REAL" else "REAL"],
                                "decisions_by_mode": books})
        text = tree.item(tree.get_children()[0], "values")[7]
        assert f"PROTECT AUTO/{expected}" in text
        assert f"PROTECT AUTO/{other}" not in text
    finally:
        tree.destroy()


@pytest.mark.parametrize("change", [
    {"updated_at": 1}, {"execution_mode": "PAPER"}, {"trade_id": "previous-position"},
])
def test_stale_other_book_or_previous_position_does_not_show_active_exit(ui_root, change):
    cycle, row = position(em_modes=["NORMAL", "IND_EXIT"])
    subject, tree = table_subject(ui_root, positions=[row], cycles=[cycle])
    try:
        subject._render_tables({"decisions_by_mode": {"REAL": {"MSN": decision("REAL", **change)}}})
        text = tree.item(tree.get_children()[0], "values")[7]
        assert "PROTECT AUTO/ARM" not in text
        assert "E ALERT·SIGNAL" not in text
        assert "CHỜ DỮ LIỆU" in text and "MFE 0.0%" not in text
    finally:
        tree.destroy()


def test_unmanaged_external_position_does_not_claim_automatic_stop_loss(ui_root):
    _cycle, row = position()
    subject, tree = table_subject(ui_root, positions=[row])
    try:
        subject._render_tables({})
        values = tree.item(tree.get_children()[0], "values")
        assert "SL OFF" in values[3] and "TP OFF" in values[7]
        assert "PROTECT OFF" in values[7] and "MFE 0.0%" not in values[7]
    finally:
        tree.destroy()


def test_position_id_cannot_bind_management_from_other_book(ui_root):
    real_cycle, row = position("REAL", sl_mode="PERCENT", sl_value=20, em_modes=["NORMAL"])
    subject, tree = table_subject(ui_root, "PAPER", positions=[row], cycles=[real_cycle])
    try:
        subject._render_tables({})
        values = tree.item(tree.get_children()[0], "values")
        assert "SL OFF" in values[3] and "(-20%)" not in values[3]
        assert "PROTECT OFF" in values[7]
        assert not subject._running_row_actions["PAPER"][tree.get_children()[0]]["trade_id"]
    finally:
        tree.destroy()


@pytest.mark.parametrize("status", ["WORKING", "PARTIAL", "CANCEL_PENDING", "UNKNOWN"])
def test_close_order_merged_into_position_is_not_repeated_as_external_order(ui_root, status):
    cycle, row = position()
    closing = OrderIntent("closing", "MSN", "SELL", 100, "LO", limit_price=11,
                          execution_mode="REAL", action="CLOSE", trade_id=cycle.id,
                          status=status, broker_order_id="broker-close")
    order = {"id": "broker-close", "symbol": "MSN", "side": "NS", "quantity": 100,
             "price": 11000, "orderStatus": "PartiallyFilled" if status == "PARTIAL" else "New"}
    subject, tree = table_subject(ui_root, items=[closing], positions=[row], orders=[order], cycles=[cycle])
    try:
        subject._render_tables({})
        iid, = tree.get_children()
        assert subject._running_row_actions["REAL"][iid]["kind"] == "position"
        assert subject._running_row_actions["REAL"][iid]["cancellable"] == (status in {"WORKING", "PARTIAL"})
    finally:
        tree.destroy()


@pytest.mark.parametrize("status,expected", [
    ("PendingCancel", "CHỜ XÁC NHẬN HỦY"), ("CANCEL_PENDING", "CHỜ XÁC NHẬN HỦY"),
    ("PendingReplace", "CHỜ XÁC NHẬN SỬA"), ("REPLACE_PENDING", "CHỜ XÁC NHẬN SỬA"),
    ("UNKNOWN", "CHƯA RÕ TRẠNG THÁI"),
])
def test_external_pending_acknowledgement_stays_visible_without_repeat_actions(ui_root, status, expected):
    order = {"id": "external", "symbol": "MSN", "side": "NB", "quantity": 100,
             "price": 10000, "orderType": "LO", "orderStatus": status}
    subject, tree = table_subject(ui_root, orders=[order])
    try:
        subject._render_tables({})
        iid, = tree.get_children()
        assert expected in tree.item(iid, "values")[7]
        action = subject._running_row_actions["REAL"][iid]
        assert not action["editable"] and not action["cancellable"]
    finally:
        tree.destroy()


@pytest.mark.parametrize("tp_mode,tp_value,em_modes,price,expected", [
    ("PERCENT", 10, [], 10.8, "TP WAIT·+10%"),
    ("PRICE", 11, [], 11, "TP HIT·+10%"),
    ("NONE", 0, ["TP"], 10.7, "TP HIT·+7%"),
    ("NONE", 0, [], 11, "TP OFF"),
])
def test_tp_status_matches_actual_target_not_global_percentage_or_net_pnl(
    ui_root, tp_mode, tp_value, em_modes, price, expected,
):
    cycle, row = position(price=price, tp_mode=tp_mode, tp_value=tp_value, em_modes=em_modes)
    subject, tree = table_subject(ui_root, positions=[row], cycles=[cycle])
    subject.rule_state.position_metrics = lambda *_: {"current_net_pnl": -1}  # Not the TP trigger.
    try:
        subject._render_tables({})
        values = tree.item(tree.get_children()[0], "values")
        assert expected in values[7]
        if em_modes == ["TP"]:
            assert "TP▲ 10,700 (+7%)" in values[3]
    finally:
        tree.destroy()


def manual_subject(status):
    subject = DashboardActionsMixin()
    subject.settings = AppSettings(watchlist=["MSN"])
    subject.symbol = SimpleNamespace(get=lambda: "MSN")
    subject.mode = SimpleNamespace(get=lambda: "PAPER")
    subject.order_type = SimpleNamespace(get=lambda: "LO")
    subject.price = SimpleNamespace(get=lambda: "10000")
    subject.quantity = SimpleNamespace(get=lambda: "100")
    subject.sl = SimpleNamespace(get=lambda: "-3.5%")
    subject.tp = SimpleNamespace(get=lambda: "7%")
    subject._em_states = {}
    subject.trade_state = SimpleNamespace(active_for=lambda *_: None)
    subject._symbol_exchange = lambda *_: "HOSE"
    subject.bridge = SimpleNamespace(read_status=lambda: status)
    subject._current_market_phase = lambda: "OPEN"
    subject._shared_tick = lambda *_: {"price": 10}
    subject._default_sl_text = lambda: "-3.5%"
    subject._refresh_local = lambda: None
    subject._log = lambda *_: None
    sent = []
    subject.execution = SimpleNamespace(submit=lambda intent, **_kw: sent.append(intent) or intent)
    return subject, sent


def test_manual_order_records_current_book_entry_context():
    paper = decision("PAPER", exposure=0.5, entry_checks={"order_budget": 5e6})
    real = decision("REAL", exposure=0.9, entry_checks={"order_budget": 15e6})
    subject, sent = manual_subject({"decisions": {"MSN": real},
                                   "decisions_by_mode": {"REAL": {"MSN": real}, "PAPER": {"MSN": paper}}})
    subject._submit("BUY")
    assert len(sent) == 1
    assert sent[0].entry_exposure == 0.5 and sent[0].entry_budget == 5e6


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("skip", [True, False])
@pytest.mark.parametrize("answer", [True, False])
def test_manual_confirmation_bypass_only_skips_yes_no(monkeypatch, mode, side, skip, answer):
    import viking_v2.dashboard.actions as module
    confirmations = []
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *args, **kwargs: confirmations.append(args) or answer)
    subject, sent = manual_subject({})
    subject.mode = SimpleNamespace(get=lambda: mode)
    subject.settings.skip_order_popups = skip
    subject._submit(side)
    asking = mode == "REAL" and not skip
    assert len(confirmations) == int(asking)
    assert len(sent) == int(not asking or answer)
    if sent:
        assert sent[0].execution_mode == mode
        assert sent[0].source == "MANUAL"
        assert sent[0].side == side


@pytest.mark.parametrize("field,value", [("quantity", "bad"), ("price", "nan"), ("sl", "nan%"), ("tp", "0%")])
def test_real_confirmation_bypass_does_not_bypass_invalid_inputs(monkeypatch, field, value):
    import viking_v2.dashboard.actions as module
    errors, confirmations = [], []
    monkeypatch.setattr(module.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *args, **kwargs: confirmations.append(args) or True)
    subject, sent = manual_subject({})
    subject.mode = SimpleNamespace(get=lambda: "REAL")
    assert subject.settings.skip_order_popups is True
    setattr(subject, field, SimpleNamespace(get=lambda: value))
    subject._submit("BUY")
    assert not sent and not errors
    assert not confirmations
    assert subject._manual_order_notice["hint"]


def test_confirmation_bypass_keeps_closed_phase_and_deferred_processing(monkeypatch):
    import viking_v2.dashboard.actions as module
    confirmations, handoffs = [], []
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *args, **kwargs: confirmations.append(args) or True)
    subject, _ = manual_subject({})
    subject.mode = SimpleNamespace(get=lambda: "REAL")
    subject._current_market_phase = lambda: "CLOSED"
    subject.execution.submit = lambda intent, **kwargs: handoffs.append((intent, kwargs)) or intent
    subject._submit("BUY")
    assert not confirmations
    assert len(handoffs) == 1
    assert handoffs[0][1] == {"phase": "CLOSED", "process_immediately": False}


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("status", ["WAITING_TOKEN", "REJECTED", "FAILED"])
@pytest.mark.parametrize("skip", [True, False])
def test_manual_order_messages_follow_popup_option_and_remain_visible_inline(monkeypatch, mode, status, skip):
    import viking_v2.dashboard.actions as module
    dialogs = []
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module.messagebox, "showerror", lambda *args, **kwargs: dialogs.append(args))
    monkeypatch.setattr(module.messagebox, "showwarning", lambda *args, **kwargs: dialogs.append(args))
    subject, _ = manual_subject({})
    subject.mode = SimpleNamespace(get=lambda: mode)
    subject.settings.skip_order_popups = skip

    class Label:
        def __init__(self): self.options = {}
        def configure(self, **kwargs): self.options.update(kwargs)

    subject.preview_status_reason, subject.preview_status_badge = Label(), Label()
    subject._refresh_local = lambda: subject.preview_status_reason.configure(text="preview refreshed")

    def submit(intent, **kwargs):
        intent.status = status
        intent.result = "TỪ CHỐI · Không đủ tiền" if status != "WAITING_TOKEN" else ""
        return intent

    subject.execution.submit = submit
    subject._submit("BUY")
    assert len(dialogs) == int(not skip)
    assert subject.preview_status_reason.options["text"] != "preview refreshed"
    assert _active_order_notice(subject)["hint"]
    assert subject.preview_status_badge.options["text"] == ("CHỜ" if status == "WAITING_TOKEN" else "CHẶN")


@pytest.mark.parametrize("changed_field", ["mode", "symbol", "order_type", "quantity", "price", "sl", "tp"])
def test_inline_order_notice_does_not_follow_another_ticket(changed_field):
    subject, _ = manual_subject({})
    subject._show_order_notice("Đặt lệnh", "Hạn mức không đủ")
    assert _active_order_notice(subject)
    setattr(subject, changed_field, SimpleNamespace(get=lambda: "changed"))
    assert _active_order_notice(subject) == {}


def test_inline_order_notice_expires_after_fifteen_seconds(monkeypatch):
    import viking_v2.dashboard.view as view_module
    clock = [100.0]
    monkeypatch.setattr(view_module.time, "monotonic", lambda: clock[0])
    subject, _ = manual_subject({})
    subject._show_order_notice("Đặt lệnh", "Hạn mức không đủ")
    assert _active_order_notice(subject)
    clock[0] += 15
    assert _active_order_notice(subject) == {}


@pytest.mark.parametrize("field,value", [
    ("price", "nan"), ("price", "inf"), ("sl", "nan%"), ("tp", "inf%"),
    ("sl", "nan"), ("tp", "0%"),
])
def test_invalid_manual_numeric_input_is_reported_without_queuing(monkeypatch, field, value):
    import viking_v2.dashboard.actions as module
    errors = []
    monkeypatch.setattr(module.messagebox, "showerror", lambda *args, **_kw: errors.append(args))
    subject, sent = manual_subject({})
    subject.settings.skip_order_popups = False  # Explicit opt-in to modal errors.
    messages = []
    subject._log = lambda *args: messages.append(args)
    setattr(subject, field, SimpleNamespace(get=lambda: value))
    subject._submit("BUY")
    assert not sent and errors
    assert len(messages) == 1 and "CHƯA GỬI" in messages[0][0] and messages[0][1] == "manual"


def test_manual_auto_block_is_logged_without_creating_or_submitting_order(monkeypatch):
    import viking_v2.dashboard.actions as module
    errors, messages = [], []
    monkeypatch.setattr(module.messagebox, "showerror", lambda *args, **_kw: errors.append(args))
    subject, sent = manual_subject({})
    subject.quantity = SimpleNamespace(get=lambda: "")
    subject._suggested_order_quantity = lambda _price: (0, 0, False)
    subject._preview_auto_feedback = {"hint": "THIẾU TIỀN CHO 100 CP\nTiền khả dụng: 2,192 đ."}
    subject._log = lambda *args: messages.append(args)
    subject._submit("BUY")
    assert not sent and errors == [] and len(messages) == 1
    assert "2,192 đ" in subject._manual_order_notice["hint"]
    assert "PAPER BUY MSN" in messages[0][0] and "2,192 đ" in messages[0][0]
    assert messages[0][1] == "manual"


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_automatic_exit_progress_belongs_to_bot_log(mode):
    subject = DashboardActionsMixin()
    messages = []
    subject._log = lambda *args: messages.append(args)
    intent = OrderIntent("exit", "MSN", "SELL", 100, "MARKET", execution_mode=mode,
                         source="EM", status="WORKING", reason="INDICATOR_EXIT")
    subject._log_order_progress(intent)
    subject._log_order_progress(intent)
    assert len(messages) == 1 and messages[0][1] == "bot"
    assert f"{mode} SELL MSN" in messages[0][0]
    assert "E AUTO" in messages[0][0]


def test_order_progress_log_tracks_fills_once_in_correct_book_and_tab():
    subject = DashboardActionsMixin()
    messages = []
    subject._log = lambda text, target="manual": messages.append((text, target))
    real = OrderIntent("real-order", "MSN", "BUY", 200, "LO", limit_price=10,
                       execution_mode="REAL", source="BOT", status="WORKING", broker_order_id="88")
    paper = OrderIntent("paper-order", "MSN", "BUY", 100, "LO", limit_price=10,
                        execution_mode="PAPER", source="MANUAL", status="WORKING")
    subject._sync_order_progress([real, paper])  # Restart doesn't replay history.
    assert not messages
    real.status, real.filled_quantity, real.remaining_quantity = "PARTIAL", 100, 100
    subject._sync_order_progress([real, paper])
    subject._sync_order_progress([real, paper])
    assert len(messages) == 1
    assert "REAL BUY MSN" in messages[0][0] and "KHỚP 100 · CÒN 100" in messages[0][0]
    assert "DNSE #88" in messages[0][0] and messages[0][1] == "bot"
    real.status, real.filled_quantity, real.remaining_quantity = "FILLED", 200, 0
    paper.status = "CANCEL_PENDING"
    subject._sync_order_progress([real, paper])
    assert "KHỚP HẾT" in messages[1][0] and "CÒN 0" in messages[1][0]
    assert "CHỜ XÁC NHẬN HỦY" in messages[2][0] and messages[2][1] == "manual"
    paper.status = "CANCELLED"
    subject._sync_order_progress([real, paper])
    assert "PAPER BUY MSN" in messages[-1][0] and "ĐÃ HỦY" in messages[-1][0]
    subject._sync_order_progress([])
    assert not subject._order_log_states


def test_order_progress_log_reports_unknown_without_relabeling_as_failure_or_retry():
    subject = DashboardActionsMixin()
    subject._order_log_states = {}
    messages = []
    subject._log = lambda *args: messages.append(args)
    intent = OrderIntent("unknown-order", "MSN", "BUY", 100, "MARKET",
                         execution_mode="REAL", status="UNKNOWN")
    subject._sync_order_progress([intent])
    subject._sync_order_progress([intent])
    assert len(messages) == 1 and "CHƯA RÕ KẾT QUẢ · KHÔNG GỬI LẠI" in messages[0][0]
    assert intent.status == "UNKNOWN" and not intent.broker_order_id


def test_paper_order_id_in_log_never_claims_a_real_dnse_order():
    subject = DashboardActionsMixin()
    messages = []
    subject._log = lambda *args: messages.append(args)
    intent = OrderIntent("simulated", "MSN", "BUY", 100, "LO", limit_price=10,
                         status="FILLED", filled_quantity=100, broker_order_id="PAPER-1")
    subject._log_order_progress(intent)
    assert "PAPER #PAPER-1" in messages[0][0] and "DNSE" not in messages[0][0]


@pytest.mark.parametrize("tp_mode,tp_value,em_modes,expected", [
    ("NONE", 0, [], "TP OFF"), ("NONE", 0, ["TP"], "TP +7%"),
    ("PERCENT", 10, [], "TP +10%"), ("PRICE", 11, [], "TP 11,000"),
])
def test_cached_manual_order_targets_match_policy_and_do_not_claim_fills(ui_root, tp_mode, tp_value, em_modes, expected):
    item = OrderIntent("cached", "MSN", "BUY", 100, "LO", limit_price=10,
                       status="WORKING", tp_mode=tp_mode, tp_value=tp_value, em_modes=em_modes)
    subject, tree = table_subject(ui_root, "PAPER", items=[item])
    try:
        subject._render_tables({})
        values = tree.item(tree.get_children()[0], "values")
        assert expected in values[7]
        assert "[PAPER] CHỜ KHỚP" in values[7] and "[DNSE]" not in values[7]
        if tp_mode == "NONE" and not em_modes:
            assert "+7%" not in values[3]
    finally:
        tree.destroy()


def test_reconciled_broker_fill_reaches_the_visible_order_log(tmp_path):
    from viking_v2.storage import JSONLineJournal
    from viking_v2.trading.orders import OrderQueue
    from viking_v2.trading.execution import ExecutionService
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent("broker-fill", "MSN", "BUY", 200, "LO",
                                   limit_price=10, execution_mode="REAL", source="BOT"))
    queue._update(intent.id, status="WORKING", broker_order_id="88", working_quantity=200)
    rows = [{"orderId": "88", "symbol": "MSN", "side": "NB", "orderStatus": "PartiallyFilled",
             "fillQuantity": 100, "averagePrice": 10000, "fee": 450}]
    fake_real = SimpleNamespace(get_orders=lambda **_kw: rows)
    service = ExecutionService(fake_real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    subject = DashboardActionsMixin()
    messages = []
    subject._log = lambda *args: messages.append(args)
    subject._sync_order_progress(queue.list_all())
    service.reconcile_working("REAL")
    subject._sync_order_progress(queue.list_all())
    assert "KHỚP MỘT PHẦN" in messages[-1][0]
    assert "KHỚP 100 · CÒN 100" in messages[-1][0]
    rows[0].update(orderStatus="Filled", fillQuantity=200, fee=900)
    service.reconcile_working("REAL")
    subject._sync_order_progress(queue.list_all())
    service.reconcile_working("REAL")
    subject._sync_order_progress(queue.list_all())
    assert len(messages) == 2 and "KHỚP HẾT" in messages[-1][0]


@pytest.mark.parametrize("field", ["limit_price", "sl_value", "tp_value"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad"])
def test_invalid_edit_does_not_persist_to_order_queue(tmp_path, field, value):
    from viking_v2.trading.orders import OrderQueue
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("MSN", "BUY", 100, "LO", limit_price=10))
    before = queue.get(intent.id).to_dict()
    values = {"quantity": 100, "limit_price": 10, field: value}
    assert queue.replace_local(intent.id, **values) is None
    assert queue.get(intent.id).to_dict() == before


@pytest.mark.parametrize("field", ["sl_value", "tp_value"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad"])
def test_invalid_management_edit_does_not_persist_to_trade(tmp_path, field, value):
    from viking_v2.trading.state import TradeStateStore
    trades = TradeStateStore(tmp_path / "trades.json")
    cycle = trades.create("MSN", "PAPER")
    before = trades.get(cycle.id).to_dict()
    assert trades.update_management(cycle.id, **{field: value}) is None
    assert trades.get(cycle.id).to_dict() == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "nan", "Infinity", "bad"])
def test_nonfinite_broker_numbers_have_no_nan_or_inf_display(value):
    from viking_v2.dashboard.view import _number, _display_price
    assert _number(value, -1) == -1
    assert _display_price(value) == "---"


@pytest.mark.parametrize("raw", [None, [], {"decisions_by_mode": {"REAL": []}},
    {"decisions_by_mode": {"REAL": {"MSN": {"details": "broken"}}}},
    {"decisions_by_mode": {"REAL": {"MSN": {"symbol": "CTS"}}}}])
def test_malformed_decision_books_never_supply_a_display_decision(raw):
    from viking_v2.trading.validation import decisions_for_mode
    assert decisions_for_mode(raw, "REAL") == {}


def _widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from _widgets(child)


@pytest.mark.parametrize("scaling", [1.0, 1.25])
@pytest.mark.parametrize("popup", ["order", "position"])
@pytest.mark.parametrize("target,value", [("SL", "nan%"), ("TP", "inf%"), ("TP", "0%")])
def test_management_popups_reject_invalid_targets_without_saving(ui_root, monkeypatch, tmp_path, popup, target, value, scaling):
    import customtkinter as ctk
    from viking_v2.trading.orders import OrderQueue
    from viking_v2.trading.state import TradeStateStore
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("MSN", "BUY", 100, "LO", limit_price=10))
    trades = TradeStateStore(tmp_path / "trades.json")
    cycle = trades.create("MSN", "PAPER")
    trades.record_buy_fill(cycle.id, 100, 10, 0)
    for name, obj in {
        "queue": queue, "trade_state": trades, "settings": AppSettings(),
        "_shared_tick": lambda *_: {"price": 10}, "_row_time": lambda *_: "10:00",
        "_log": lambda *_: None, "_refresh_local": lambda: None,
    }.items():
        monkeypatch.setattr(ui_root, name, obj, raising=False)
    before_order, before_cycle = queue.get(intent.id).to_dict(), trades.get(cycle.id).to_dict()
    previous = set(ui_root.winfo_children())
    top = None
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    try:
        if popup == "order":
            DashboardActionsMixin._edit_running_order(ui_root, {
                "local_id": intent.id, "symbol": "MSN", "side": "BUY", "mode": "PAPER",
            })
        else:
            DashboardActionsMixin._show_position_management(ui_root, {
                "symbol": "MSN", "mode": "PAPER", "trade_id": cycle.id,
                "position": {"openQuantity": 100, "tradeQuantity": 100, "costPrice": 10, "marketPrice": 10},
            })
        top, = [w for w in ui_root.winfo_children() if w not in previous and isinstance(w, ctk.CTkToplevel)]
        # The session root is hidden; clear transient so Tk maps this test dialog.
        top.transient("")
        top.deiconify()
        ui_root.update_idletasks()
        ui_root.update()
        for widget in _widgets(top):
            if isinstance(widget, (ctk.CTkEntry, ctk.CTkButton)):
                x = widget.winfo_rootx() - top.winfo_rootx()
                y = widget.winfo_rooty() - top.winfo_rooty()
                assert x >= 0 and y >= 0
                assert x + widget.winfo_width() <= top.winfo_width() + 2
                assert y + widget.winfo_height() <= top.winfo_height() + 2
        entries = [w for w in _widgets(top) if isinstance(w, ctk.CTkEntry) and ("AUTO" in w.get() or "%" in w.get())]
        if popup == "position":
            chosen = next(w for w in entries if int(w.grid_info()["column"]) == (3 if target == "SL" else 1))
        else:
            chosen = next(w for w in entries if ("-" in w.get()) == (target == "SL"))
        chosen.delete(0, "end")
        chosen.insert(0, value)
        save = next(w for w in _widgets(top) if isinstance(w, ctk.CTkButton) and str(w.cget("text")).startswith("LƯU"))
        save.invoke()
        assert top.winfo_exists()
        assert queue.get(intent.id).to_dict() == before_order
        assert trades.get(cycle.id).to_dict() == before_cycle
        assert any(isinstance(w, ctk.CTkLabel) and "không hợp lệ" in str(w.cget("text")) for w in _widgets(top))
    finally:
        if top and top.winfo_exists():
            top.destroy()
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
