"""Per-position exit policies and explicitly reviewed multi-position sells."""
from copy import deepcopy
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import customtkinter as ctk
import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.position_close import close_rows, submit_closes, show_close_positions
from viking_v2.dashboard.tables import DashboardTablesMixin
from viking_v2.models import OrderIntent
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.trading.position_controls import save_position_controls, position_exit_params
from support.tests.test_live_safety import service
from support.tests.test_position_repair_and_tplus_alerts import g1
from support.tests.test_demo_order_display import _widgets


def batch_app(tmp_path, mode="REAL"):
    engine, broker, queue, trades, rules = service(tmp_path, quote_provider=lambda symbol: g1(100, symbol))
    positions, actions = [], []
    for symbol, quantity, available in (("FPT", 300, 300), ("HDB", 400, 200), ("MSN", 100, 0)):
        cycle = trades.create(symbol, mode, em_modes=["NORMAL", "IND_EXIT"])
        trades.record_buy_fill(cycle.id, quantity, 100)
        row = {"symbol": symbol, "openQuantity": quantity, "tradeQuantity": available,
               "costPrice": 100, "marketPrice": 100}
        positions.append(row)
        actions.append({"kind": "position", "mode": mode, "symbol": symbol, "trade_id": cycle.id,
                        "loan_package_id": "", "position": row})
    app = SimpleNamespace(execution=engine, queue=queue, trade_state=trades, rule_state=rules,
                          settings=AppSettings(), snapshots={mode: ({}, positions, [])},
                          _current_market_phase=lambda: "OPEN")
    return app, broker, actions


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_bulk_close_reviews_sellable_only_and_links_each_trade_without_sending(tmp_path, mode):
    app, broker, actions = batch_app(tmp_path, mode)
    reviewed = close_rows(app, [*actions, actions[0]])
    assert [row["quantity"] for row in reviewed] == [300, 200, 0]
    assert reviewed[1]["waiting"] == 200 and reviewed[2]["note"] == "Chờ cổ về"
    before = [cycle.to_dict() for cycle in app.trade_state.list_cycles()]
    results = submit_closes(app, [row for row in reviewed if row["quantity"]])
    assert [(item.symbol, item.quantity, item.trade_id, item.source) for item in results] == [
        ("FPT", 300, actions[0]["trade_id"], "MANUAL"), ("HDB", 200, actions[1]["trade_id"], "MANUAL")]
    assert broker.sent == [] and before == [cycle.to_dict() for cycle in app.trade_state.list_cycles()]
    assert [row["quantity"] for row in close_rows(app, actions)] == [0, 0, 0]


@pytest.mark.parametrize("change", ["new_sell", "fewer_shares", "closed_trade", "duplicate", "invalid_lot"])
def test_batch_revalidates_all_rows_before_writing_any_order(tmp_path, change):
    app, broker, actions = batch_app(tmp_path)
    reviewed = close_rows(app, actions)[:2]
    if change == "new_sell":
        app.queue.add(OrderIntent.create("HDB", "SELL", 100, "MARKET", execution_mode="REAL", trade_id=actions[1]["trade_id"]))
    elif change == "fewer_shares":
        app.snapshots["REAL"][1][1]["tradeQuantity"] = 100
    elif change == "closed_trade":
        app.trade_state.record_sell_fill(actions[1]["trade_id"], 400, 100)
    elif change == "duplicate":
        reviewed.append(reviewed[0])
    else:
        reviewed[1]["quantity"] = 150
    before = [item.to_dict() for item in app.queue.list_all()]
    with pytest.raises(ValueError):
        submit_closes(app, reviewed)
    assert before == [item.to_dict() for item in app.queue.list_all()] and broker.sent == []


def test_external_broker_sell_blocks_duplicate_batch_without_touching_other_symbol(tmp_path):
    app, _, actions = batch_app(tmp_path)
    app.snapshots["REAL"][2].append({"id": "outside", "symbol": "FPT", "side": "NS", "orderStatus": "PartiallyFilled"})
    rows = close_rows(app, actions)
    assert rows[0]["quantity"] == 0 and rows[0]["note"] == "Có SELL đang chờ"
    assert rows[1]["quantity"] == 200


def test_bulk_close_requires_matching_book_symbol_and_unambiguous_package(tmp_path):
    app, broker, actions = batch_app(tmp_path)
    assert close_rows(app, [{**actions[0], "mode": "PAPER"}]) == []
    assert close_rows(app, [{**actions[0], "symbol": "HDB"}]) == []
    app.snapshots["REAL"][1].append({**actions[0]["position"], "loanPackageId": "2"})
    rows = close_rows(app, [actions[0]])
    assert rows[0]["quantity"] == 0 and rows[0]["note"] == "Chưa rõ vị thế/gói"
    assert broker.sent == []


@pytest.mark.parametrize("stage,peak,price,key", [("ARM", 10, 107, "normal_policy"), ("DYN", 4, 102.5, "dynamic_policy")])
@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
def test_one_position_overrides_rule_without_changing_other_positions(stage, peak, price, key, policy):
    params = StaticRuleParameters(normal_policy="ALERT" if policy == "AUTO" else "AUTO",
                                  dynamic_policy="ALERT" if policy == "AUTO" else "AUTO",
                                  normal_atr_activation_enabled=False, normal_atr_trail_enabled=False)
    rule = StaticRule(params)
    before = deepcopy(params.to_dict())
    position = {"quantity": 600, "trade_quantity": 600, "avg_price": 100, "current_price": price,
                "peak_profit_pct": peak, "normal_armed": stage == "ARM", "em_modes": ["NORMAL"], "sl_enabled": False,
                "exit_rule_overrides": {key: policy}}
    decision = rule.evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"}, {"position": position})
    assert decision.action == ("SELL" if policy == "AUTO" else "WAIT")
    assert decision.details["normal_policy"] == policy
    assert rule.params.to_dict() == before
    position["exit_rule_overrides"] = {}
    other = rule.evaluate({"symbol": "FPT", "bars": [], "confirmed_market_state": "UPTREND"}, {"position": position})
    assert other.action != decision.action


@pytest.mark.parametrize("stage,peak,price,key", [("ARM", 10, 107, "normal_arm_enabled"), ("DYN", 4, 102.5, "normal_dynamic_enabled")])
def test_branch_off_does_not_create_sell_or_arm_notice(stage, peak, price, key):
    position = {"quantity": 600, "trade_quantity": 600, "avg_price": 100, "current_price": price,
                "peak_profit_pct": peak, "normal_armed": False, "em_modes": ["NORMAL"], "sl_enabled": False,
                "exit_rule_overrides": {key: False}}
    params = StaticRuleParameters(normal_atr_activation_enabled=False, normal_atr_trail_enabled=False)
    decision = StaticRule(params).evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"}, {"position": position})
    assert decision.action != "SELL" and not decision.details.get("normal_should_arm")


@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
def test_e_override_changes_only_exit_policy_and_keeps_main_settings(policy):
    params = StaticRuleParameters(indicator_exit_policy="ALERT" if policy == "AUTO" else "AUTO")
    before = params.to_dict()
    position = {"quantity": 300, "trade_quantity": 300, "avg_price": 100, "current_price": 100,
                "em_modes": ["IND_EXIT"], "sl_enabled": False,
                "exit_rule_overrides": {"indicator_exit_policy": policy}}
    decision = StaticRule(params)._evaluate_position("FPT", "UPTREND", "SELL", [], position, {})
    assert decision.action == ("SELL" if policy == "AUTO" else "WAIT")
    assert decision.details["indicator_exit_policy"] == policy and params.to_dict() == before


@pytest.mark.parametrize("reason,key", [("NORMAL_PROTECTION", "normal_policy"), ("INDICATOR_EXIT", "indicator_exit_policy")])
def test_mode_edit_during_broker_read_is_checked_before_hand_off(tmp_path, reason, key):
    app, broker, actions = batch_app(tmp_path)
    trade_id = actions[0]["trade_id"]
    original_read = broker.get_positions
    def read(**kwargs):
        app.trade_state.update_management(trade_id, exit_rule_overrides={key: "ALERT"})
        return original_read(**kwargs)
    broker.get_positions = read
    intent = app.queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", source="EM",
                                            reason=reason, trade_id=trade_id))
    app.execution.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent == [] and app.queue.get(intent.id).status == "CANCELLED"


@pytest.mark.parametrize("reason,key", [("NORMAL_PROTECTION", "normal_policy"), ("DYNAMIC_PROTECTION", "dynamic_policy"),
                                        ("INDICATOR_EXIT", "indicator_exit_policy")])
@pytest.mark.parametrize("phase", ["CLOSED", "OPEN"])
def test_alert_override_cancels_unsent_old_auto_and_preserves_manual(tmp_path, reason, key, phase):
    app, broker, actions = batch_app(tmp_path)
    trade_id = actions[0]["trade_id"]
    auto = app.queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", source="EM", reason=reason, trade_id=trade_id))
    manual = app.queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", source="MANUAL", trade_id=trade_id))
    updated, pending = save_position_controls(app.trade_state, app.queue, trade_id, exit_rule_overrides={key: "ALERT"})
    assert app.queue.get(auto.id).status == "CANCELLED" and app.queue.get(manual.id).status == "PENDING"
    assert updated.open_quantity == 300 and pending == []
    # Remove manual reservation; replay a daemon decision made before the edit.
    app.queue.cancel_local(manual.id)
    replay = app.queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", source="EM", reason=reason, trade_id=trade_id))
    app.execution.process_due(phase=phase, execution_mode="REAL")
    assert app.queue.get(replay.id).status == "CANCELLED" and broker.sent == []


def test_auto_override_remains_auto_when_global_protect_is_alert(tmp_path):
    app, broker, actions = batch_app(tmp_path)
    app.execution.protection_policy_provider = lambda stage: "ALERT"
    trade_id = actions[0]["trade_id"]
    app.trade_state.update_management(trade_id, exit_rule_overrides={"normal_policy": "AUTO"})
    intent = app.queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", source="EM",
                                            reason="NORMAL_PROTECTION", trade_id=trade_id))
    app.execution.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.sent) == 1 and app.queue.get(intent.id).status != "CANCELLED"


def test_disabled_protect_rejects_stale_auto_sell_even_with_current_sell_decision(tmp_path):
    app, broker, actions = batch_app(tmp_path)
    trade_id = actions[0]["trade_id"]
    app.trade_state.update_management(trade_id, em_modes=[])
    intent = app.queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", execution_mode="REAL", source="EM",
                                            reason="NORMAL_PROTECTION", trade_id=trade_id))
    app.execution.sell_decision_provider = lambda *_a: {"action": "SELL", "event": "NORMAL_PROTECTION"}
    app.execution.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent == [] and app.queue.get(intent.id).status == "CANCELLED"


def test_overrides_survive_restart_and_inheritance_follows_later_rule_edits(tmp_path):
    from viking_v2.trading.state import TradeStateStore
    app, _, actions = batch_app(tmp_path)
    trade_id = actions[0]["trade_id"]
    app.trade_state.update_management(trade_id, exit_rule_overrides={"normal_policy": "ALERT"})
    cycle = TradeStateStore(app.trade_state.store.path).get(trade_id)
    assert position_exit_params({"normal_policy": "AUTO"}, cycle)["normal_policy"] == "ALERT"
    app.trade_state.update_management(trade_id, exit_rule_overrides={})
    cycle = app.trade_state.get(trade_id)
    assert position_exit_params({"normal_policy": "AUTO"}, cycle)["normal_policy"] == "AUTO"
    assert position_exit_params({"normal_policy": "ALERT"}, cycle)["normal_policy"] == "ALERT"


def test_context_menu_has_only_open_and_close_and_keeps_multiselection(ui_root, monkeypatch):
    tree = ttk.Treeview(ui_root)
    for name in ("A", "B"):
        tree.insert("", "end", iid=name)
    tree.selection_set("A", "B")
    monkeypatch.setattr(tree, "identify_row", lambda y: "B")
    menu = SimpleNamespace(entries=[], add_command=lambda **kw: menu.entries.append(kw),
                           delete=lambda *_a: menu.entries.clear(), post=lambda *_a: None)
    monkeypatch.setattr(tk, "Menu", lambda *_a, **_kw: menu)
    app = SimpleNamespace(trees={"REAL": tree}, _running_row_actions={"REAL": {
        name: {"kind": "position", "symbol": name, "mode": "REAL"} for name in ("A", "B")}},
        _open_running_selection=lambda *_a: None, _close_running_selection=lambda *_a: None)
    try:
        DashboardActionsMixin._running_right_click(app, SimpleNamespace(widget=tree, y=1, x_root=0, y_root=0))
        assert [item["label"] for item in menu.entries] == ["Mở bảng", "Đóng"]
        assert tree.selection() == ("A", "B")
    finally:
        tree.destroy()


def test_select_all_positions_excludes_working_child_orders(ui_root):
    tree = ttk.Treeview(ui_root)
    tree.insert("", "end", iid="A")
    tree.insert("A", "end", iid="child")
    tree.insert("", "end", iid="B")
    app = SimpleNamespace(trees={"REAL": tree}, _running_row_actions={"REAL": {
        "A": {"kind": "position"}, "B": {"kind": "position"}, "child": {"kind": "local"}}}, _sync_cancel_button=lambda: None)
    try:
        assert DashboardTablesMixin._select_all_running_positions(app, SimpleNamespace(widget=tree)) == "break"
        assert tree.selection() == ("A", "B")
    finally:
        tree.destroy()


@pytest.mark.parametrize("scaling", [1., 1.25, 1.5])
def test_compact_popup_edits_targets_and_three_modes_then_returns_to_rule(ui_root, monkeypatch, tmp_path, scaling):
    app, _, actions = batch_app(tmp_path)
    for key, value in vars(app).items():
        monkeypatch.setattr(ui_root, key, value, raising=False)
    for key, value in {"_shared_tick": lambda *_a: g1(100), "_row_time": lambda *_a: "12/10 14:09",
                       "_log": lambda *_a: None, "_refresh_local": lambda: None}.items():
        monkeypatch.setattr(ui_root, key, value, raising=False)
    previous = set(ui_root.winfo_children())
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    top = None
    before = app.trade_state.get(actions[0]["trade_id"])
    try:
        DashboardActionsMixin._show_position_management(ui_root, actions[0])
        top, = [child for child in ui_root.winfo_children() if child not in previous and isinstance(child, ctk.CTkToplevel)]
        top.transient("")
        top.deiconify()
        ui_root.update()
        ui_root.update_idletasks()
        widgets = list(_widgets(top))
        panels = [w for w in top.winfo_children()
                  if isinstance(w, ctk.CTkFrame) and w.cget("fg_color") == "#22262D"]
        assert len(panels) == 2
        left, right = sorted(panels, key=lambda w: w.winfo_x())
        assert abs(left.winfo_width() - right.winfo_width()) <= 1
        assert left.winfo_y() == right.winfo_y()
        assert left.winfo_height() == right.winfo_height()
        assert left.winfo_x() + left.winfo_width() < right.winfo_x()
        assert not any(isinstance(w, ctk.CTkButton) and w.cget("text") == "TẮT TOÀN BỘ E/M" for w in widgets)
        menus = [w for w in widgets if isinstance(w, ctk.CTkOptionMenu)]
        assert len(menus) == 3 and all(w.get().endswith(" · RULE") for w in menus)
        assert menus[0].get() == "AUTO · RULE"
        for detail in (w for w in widgets if isinstance(w, ctk.CTkLabel) and w.cget("text").startswith("RULE ")):
            assert "\n" not in detail.cget("text") and detail.cget("wraplength") == 0
            font = tkfont.Font(root=top, font=detail._label.cget("font"))
            assert font.measure(detail.cget("text")) <= detail.winfo_width() + 2
        for widget in widgets:
            if isinstance(widget, (ctk.CTkEntry, ctk.CTkButton, ctk.CTkOptionMenu)):
                assert widget.winfo_rootx() + widget.winfo_width() <= top.winfo_rootx() + top.winfo_width() + 2
                assert widget.winfo_rooty() + widget.winfo_height() <= top.winfo_rooty() + top.winfo_height() + 2
            if isinstance(widget, ctk.CTkOptionMenu) or isinstance(widget, ctk.CTkEntry) and widget.cget("placeholder_text") == "Giá hoặc %":
                panel = left if isinstance(widget, ctk.CTkEntry) else right
                assert widget.winfo_rootx() >= panel.winfo_rootx()
                assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width() + 2
        targets = [w for w in widgets if isinstance(w, ctk.CTkEntry) and w.cget("placeholder_text") == "Giá hoặc %"]
        for field, value in zip(targets, ("8%", "96,500")):
            field.delete(0, "end")
            field.insert(0, value)
        next(w for w in widgets if isinstance(w, ctk.CTkSwitch) and w.cget("text") == "TP").toggle()
        for menu, value in zip(menus, ("ALERT", "OFF", "AUTO")):
            menu.set(value)
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        current = app.trade_state.get(before.id)
        assert (current.tp_mode, current.tp_value, current.sl_mode, current.sl_value) == ("PERCENT", 8, "PRICE", 96.5)
        assert current.exit_rule_overrides == {"normal_policy": "ALERT", "normal_arm_enabled": True,
                                              "normal_dynamic_enabled": False, "indicator_exit_policy": "AUTO"}
        assert current.entry_quantity == before.entry_quantity and current.open_quantity == before.open_quantity
        assert app.trade_state.get(actions[1]["trade_id"]).exit_rule_overrides == {}
        previous = set(ui_root.winfo_children())
        DashboardActionsMixin._show_position_management(ui_root, actions[0])
        top, = [child for child in ui_root.winfo_children() if child not in previous and isinstance(child, ctk.CTkToplevel)]
        restored_widgets = list(_widgets(top))
        for menu in (w for w in restored_widgets if isinstance(w, ctk.CTkOptionMenu)):
            menu.set("Theo RULE")
        next(w for w in restored_widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        restored = app.trade_state.get(before.id)
        assert restored.exit_rule_overrides == {}
        assert restored.tp_value == 8 and restored.sl_value == 96.5
    finally:
        if top and top.winfo_exists():
            top.destroy()
        ctk.set_widget_scaling(1.)
        ctk.set_window_scaling(1.)


def test_rule_preserves_original_tabs_and_compacts_panels_without_losing_values(ui_root, tmp_path, monkeypatch):
    from viking_v2 import config
    monkeypatch.setattr(config, "RUNTIME_ROOT", tmp_path)
    settings = AppSettings(rule_parameters={"buy_ema_fast": 4, "buy_ema_slow": 9, "rsi_period": 13,
                                           "normal_policy": "ALERT", "dynamic_policy": "AUTO"})
    popup = RuleSettingsPopup(ui_root, settings, "MERGED_UI", lambda: None)
    try:
        assert list(popup.phase_tabs._tab_dict) == ["PHASE 1 · THỊ TRƯỜNG", "PHASE 2 · ENTRY BUY", "PHASE 3 · VỐN & BẢO VỆ"]
        assert popup._phase_frames[0] is not popup._phase_frames[1]
        assert popup._phase1_body is not popup._phase2_body
        assert popup.phase2_mode_card.master is popup._phase2_body
        assert popup.ma_period.master.master.winfo_manager() == "grid"
        popup.ma_period.delete(0, "end")
        popup.ma_period.insert(0, "180")
        popup.save()
        saved = config.load_settings("MERGED_UI")
        assert saved.rule_parameters["buy_ema_fast"] == 4 and saved.rule_parameters["rsi_period"] == 13
        assert saved.rule_parameters["normal_policy"] == "ALERT" and saved.rule_parameters["dynamic_policy"] == "AUTO"
        assert saved.rule_parameters["ma_period"] == 180
    finally:
        popup._close()
