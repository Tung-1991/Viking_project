from __future__ import annotations

from types import SimpleNamespace
from tkinter import ttk

import customtkinter as ctk
import pytest

from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.dashboard.tables import DashboardTablesMixin
from viking_v2.dashboard.view import COL_SETTLEMENT_BG
from viking_v2.models import OrderIntent


def _table_subject(tree, mode, items, positions=()):
    subject = DashboardTablesMixin()
    subject.queue = SimpleNamespace(list_all=lambda: items)
    subject.trees = {mode: tree}
    subject._running_row_actions = {}
    subject.snapshots = {mode: ({}, list(positions), [])}
    subject.settings = SimpleNamespace(rule_parameters={})
    subject.trade_state = SimpleNamespace(
        get=lambda _trade_id: None,
        active_for=lambda _symbol, _mode: None,
    )
    subject._cached_fee_rate = lambda _symbol, _side: None
    subject._row_time = lambda value: str(value)
    subject._sync_cancel_button = lambda: None
    return subject


@pytest.mark.parametrize("mode", ["PAPER", "REAL"])
@pytest.mark.parametrize("status,tag", [
    ("PENDING", "pending_order"),
    ("WAITING_TOKEN", "pending_order"),
    ("PAUSED", "pending_order"),
    ("WAITING_SETTLEMENT", "settlement_order"),
    ("WORKING", "dnse_order"),
    ("PARTIAL", "partial_order"),
    ("UNKNOWN", "error_order"),
])
def test_waiting_sell_has_distinct_tag_from_local_cache(ui_root, mode, status, tag):
    tree = ttk.Treeview(ui_root, show="headings")
    item = OrderIntent(
        "test-sell", "FPT", "SELL", 100, "LO", limit_price=100,
        execution_mode=mode, status=status, action="CLOSE",
    )
    subject = _table_subject(tree, mode, [item])
    try:
        subject._render_tables({})
        row = tree.item(f"LOCAL:{item.id}")
        assert tuple(row["tags"]) == (tag,)
        if status == "WAITING_SETTLEMENT":
            assert "[T+2] CHỜ CỔ VỀ" in row["values"][7]
    finally:
        tree.destroy()


@pytest.mark.parametrize("mode", ["PAPER", "REAL"])
@pytest.mark.parametrize("status,tag", [
    (None, "position_waiting"),
    ("WAITING_SETTLEMENT", "position_waiting"),
    ("PENDING", "position_closing"),
    ("PARTIAL", "position_closing"),
])
def test_position_waiting_settlement_is_not_colored_as_active_close(ui_root, mode, status, tag):
    tree = ttk.Treeview(ui_root, show="headings")
    items = [] if status is None else [OrderIntent(
        "test-close", "FPT", "SELL", 100, "LO", limit_price=100,
        execution_mode=mode, status=status, action="CLOSE",
    )]
    position = {"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 0, "costPrice": 100}
    subject = _table_subject(tree, mode, items, [position])
    try:
        subject._render_tables({})
        rows = tree.get_children()
        assert len(rows) == 1
        assert tuple(tree.item(rows[0], "tags")) == (tag,)
    finally:
        tree.destroy()


def test_running_legend_separates_purple_settlement_from_amber_cache(ui_root):
    existing = set(ui_root.winfo_children())
    try:
        DashboardPanelsMixin._show_running_legend(ui_root)
        popup, = set(ui_root.winfo_children()) - existing
        chips = {}

        def visit(widget):
            if isinstance(widget, ctk.CTkLabel):
                text = widget.cget("text")
                if text in {"T+2", "CACHE"}:
                    chips[text] = widget.cget("fg_color")
            for child in widget.winfo_children():
                visit(child)

        visit(popup)
        assert chips == {"T+2": COL_SETTLEMENT_BG, "CACHE": "#42351B"}
        assert chips["T+2"] != chips["CACHE"]
    finally:
        for popup in set(ui_root.winfo_children()) - existing:
            popup.destroy()
