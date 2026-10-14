"""RULE references and Override markers are informational, never cancel actions."""
from types import SimpleNamespace

import customtkinter as ctk
import pytest
from tkinter import ttk

from viking_v2.config import AppSettings
from viking_v2.models import OrderIntent, TradeCycle
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.tables import RUNNING_COLUMNS
from viking_v2.trading.position_controls import position_override_items
from support.tests.test_demo_order_display import table_subject, _widgets
from support.tests.test_position_modes_and_bulk_close import batch_app
from support.tests.test_rule_deferred_build import pump


def default_cycle(settings):
    cycle = TradeCycle("one", "FPT", execution_mode="REAL", em_modes=list(settings.bot_em_modes), sl_enabled=settings.bot_sl_enabled)
    cycle.record_buy_fill(100, 100)
    return cycle


def test_default_position_has_no_override_and_explicit_equal_rule_is_still_fixed():
    settings = AppSettings()
    cycle = default_cycle(settings)
    assert position_override_items(settings, cycle) == []
    cycle.exit_rule_overrides = {"normal_policy": settings.rule_parameters["normal_policy"]}
    assert position_override_items(settings, cycle) == ["SAU ARM: AUTO · RULE AUTO"]
    cycle.exit_rule_overrides = {}
    cycle.tp_mode, cycle.tp_value = "PERCENT", 7
    assert "TP: +7% · RULE +7%" in position_override_items(settings, cycle)
    cycle.tp_mode, cycle.tp_value = "NONE", 0
    cycle.sl_mode, cycle.sl_value = "PERCENT", 3.5
    assert position_override_items(settings, cycle) == ["SL: -3.5% · RULE -3.5%"]


@pytest.mark.parametrize("change", ["hold", "sl_off", "modes_off", "dynamic_off", "price_target"])
def test_only_persisted_local_controls_mark_override(change):
    settings = AppSettings()
    cycle = default_cycle(settings)
    if change == "hold":
        cycle.auto_sell_enabled = False
    elif change == "sl_off":
        cycle.sl_enabled = False
    elif change == "modes_off":
        cycle.em_modes = []
    elif change == "dynamic_off":
        cycle.exit_rule_overrides = {"normal_dynamic_enabled": False}
    else:
        cycle.sl_mode, cycle.sl_value = "PRICE", 98
    before = cycle.to_dict()
    assert position_override_items(settings, cycle)
    assert cycle.to_dict() == before


def test_running_override_column_updates_without_making_x_a_cancel_button(ui_root):
    settings = AppSettings()
    cycle = default_cycle(settings)
    row = {"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 100, "costPrice": 100, "marketPrice": 101}
    order = OrderIntent.create("FPT", "BUY", 100, "LO", execution_mode="REAL", limit_price=100,
                              em_modes=list(settings.bot_em_modes))
    app, tree = table_subject(ui_root, cycles=[cycle], positions=[row], items=[order])
    try:
        app._render_tables({})
        position = next(iid for iid in tree.get_children() if app._running_row_actions["REAL"][iid]["kind"] == "position")
        assert tree.heading("Override", "text") == "Override"
        assert tree.set(position, "Override") == ""
        assert tree.set(f"LOCAL:{order.id}", "Override") == ""
        assert app._running_row_actions["REAL"][f"LOCAL:{order.id}"]["cancellable"]
        cycle.auto_sell_enabled = False
        app._render_tables({})
        assert tree.set(position, "Override") == "X"
        assert "Tự bán: TẠM DỪNG" in tree._viking_column_hint.text_for(position, "Override")
        cycle.auto_sell_enabled = True
        app._render_tables({})
        assert tree.set(position, "Override") == "" and position not in tree._viking_override_rows
    finally:
        tree.destroy()


def test_yellow_x_retains_selection_color_and_forwards_click_without_cancel(ui_root):
    from viking_v2.dashboard.override_cells import OverrideCells
    top = ctk.CTkToplevel(ui_root)
    top.transient("")
    top.geometry("600x220")
    style = ttk.Style(top)
    style.theme_use("clam")
    style.configure("OverrideTest.Treeview", background="#22262D", rowheight=36)
    style.map("OverrideTest.Treeview", background=[("selected", "#5545A6")])
    tree = ttk.Treeview(top, columns=("Status", "Override"), show="headings", style="OverrideTest.Treeview")
    tree.pack(fill="both", expand=True)
    tree.column("Override", width=160)
    tree.insert("", "end", iid="A", values=("CHỜ", "X"), tags=("waiting",))
    tree.tag_configure("waiting", background="#42351B")
    tree._viking_override_rows = {"A": ["E: ALERT"]}
    events = []
    tree.bind("<Button-1>", lambda _e: events.append("select"), add="+")
    markers = OverrideCells(tree)
    try:
        top.deiconify()
        ui_root.update_idletasks()
        markers.draw()
        label = markers.labels["A"]
        assert label.cget("fg") == "#F59E0B" and label.cget("bg") == "#42351B"
        markers.forward(SimpleNamespace(x=5, y=5, state=0), "<Button-1>", label)
        assert events == ["select"] and tree.selection() == ("A",)
        markers.draw()
        assert label.cget("bg") == "#5545A6" and label.cget("fg") == "#F59E0B"
        tree._viking_override_rows = {}
        markers.draw()
        assert markers.labels == {}
    finally:
        top.destroy()


@pytest.mark.parametrize("scaling", [1., 1.5])
def test_popup_references_current_rule_keeps_custom_targets_and_colors_each_override(ui_root, monkeypatch, tmp_path, scaling):
    app, _, actions = batch_app(tmp_path)
    before = app.trade_state.get(actions[0]["trade_id"]).to_dict()
    for key, value in vars(app).items():
        monkeypatch.setattr(ui_root, key, value, raising=False)
    for key, value in {"_shared_tick": lambda *_a: {"price": 100}, "_row_time": lambda *_a: "14:09",
                       "_log": lambda *_a: None, "_refresh_local": lambda: None}.items():
        monkeypatch.setattr(ui_root, key, value, raising=False)
    previous = set(ui_root.winfo_children())
    top = None
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    try:
        DashboardActionsMixin._show_position_management(ui_root, actions[0])
        top, = [w for w in ui_root.winfo_children() if w not in previous and isinstance(w, ctk.CTkToplevel)]
        widgets = list(_widgets(top))
        menus = [w for w in widgets if isinstance(w, ctk.CTkOptionMenu)]
        fields = [w for w in widgets if isinstance(w, ctk.CTkEntry)]
        assert all(menu.get() == "Theo RULE" and menu.cget("fg_color") != "#F59E0B" for menu in menus)
        menus[0].set("ALERT")
        assert menus[0].cget("fg_color") == "#F59E0B"
        tp_switch = next(w for w in widgets if isinstance(w, ctk.CTkSwitch) and w.cget("text") == "TP")
        tp_switch.toggle()
        fields[0].delete(0, "end")
        fields[0].insert(0, "8%")
        fields[0].event_generate("<KeyRelease>")
        app.settings.rule_parameters.update(take_profit_pct=9, initial_sl_pct=-4,
                                            normal_arm_pct=10, normal_retention_pct=90, rsi_period=17)
        pump(ui_root, lambda: any(isinstance(w, ctk.CTkLabel) and "RULE +9%" in w.cget("text") for w in widgets))
        assert fields[0].get() == "8%" and fields[0].cget("text_color") == "#F59E0B"
        assert any(isinstance(w, ctk.CTkLabel) and "108,000" in w.cget("text") and "RULE +9%" in w.cget("text") for w in widgets)
        assert any(isinstance(w, ctk.CTkLabel) and "RULE -4%" in w.cget("text") for w in widgets)
        assert any(isinstance(w, ctk.CTkLabel) and "ARM +10%" in w.cget("text") for w in widgets)
        assert any(isinstance(w, ctk.CTkLabel) and "Giữ 90%" in w.cget("text") for w in widgets)
        assert menus[0].get() == "ALERT"
        menus[0].set("Theo RULE")
        assert menus[0].cget("fg_color") != "#F59E0B"
        fields[0].delete(0, "end")
        fields[0].insert(0, "AUTO")
        fields[0].event_generate("<KeyRelease>")
        # Closing this draft must not save its controls or change broker fills.
        assert app.trade_state.get(actions[0]["trade_id"]).to_dict() == before
    finally:
        if top and top.winfo_exists():
            top.destroy()
        ctk.set_widget_scaling(1.)
        ctk.set_window_scaling(1.)
