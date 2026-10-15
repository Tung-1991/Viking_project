"""DPI, visible position metrics and actionable system diagnostics, offline."""
from datetime import datetime
from types import SimpleNamespace
from tkinter import font as tkfont, ttk

import customtkinter as ctk
import pytest

from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.dashboard.tables import DashboardTablesMixin, RUNNING_COLUMNS
from viking_v2.services.runtime import RuntimeBridge
from viking_v2.trading.market import VN_TZ
from support.tests.test_demo_order_display import _widgets
from support.tests.test_telegram_buy_lost import subject


@pytest.mark.parametrize("scale", [1., 1.25, 1.5, 2.])
def test_running_font_matches_ctk_and_compact_rows_fit_after_dpi_change(ui_root, scale):
    top = ctk.CTkToplevel(ui_root)
    sample = ctk.CTkLabel(top, text="PnL", font=("Segoe UI", 14))
    tree = ttk.Treeview(top, show="headings")
    try:
        DashboardTablesMixin._configure_tree(tree, RUNNING_COLUMNS)
        tree.insert("", "end", iid="keep", values=("",) * len(RUNNING_COLUMNS))
        tree.selection_set("keep")
        widths = [int(tree.column(key, "width")) for key in RUNNING_COLUMNS]
        ctk.set_widget_scaling(scale)
        style = ttk.Style(tree)
        font = tkfont.Font(root=top, font=style.lookup(tree.cget("style"), "font"))
        ctk_font = tkfont.Font(root=top, font=sample._label.cget("font"))
        assert abs(font.metrics("linespace") - ctk_font.metrics("linespace")) <= 1
        height = int(style.lookup(tree.cget("style"), "rowheight"))
        assert 3 * font.metrics("linespace") + 8 <= height < 4 * font.metrics("linespace") + 8
        for key, before in zip(RUNNING_COLUMNS, widths):
            assert int(tree.column(key, "width")) >= before * scale - 2
        assert tree.selection() == ("keep",)
    finally:
        top.destroy()
        ctk.set_widget_scaling(1.)


def test_system_500_identifies_endpoint_time_log_location_and_redacts_secrets(subject, tmp_path, monkeypatch):
    app, telegram = subject
    now = datetime(2026, 10, 15, 11, 0, tzinfo=VN_TZ).timestamp()
    monkeypatch.setattr("time.time", lambda: now)
    app.real = SimpleNamespace(api_key="PRIVATEKEY", api_secret="PRIVATESECRET", configured=lambda: False)
    app.bridge = RuntimeBridge(root=tmp_path / "account with spaces")
    logs = []
    app.logger = SimpleNamespace(warning=lambda *args: logs.append(args))
    status = {"api_health": {"rest": {"total_requests": 1, "last_status": 500,
        "last_endpoint": "GET /price/ohlc", "last_error": "INTERNAL_ERROR PRIVATEKEY PRIVATESECRET"}}}
    app._notify_system_health(status, "RUNNING", "OPEN", "REAL")
    app._notify_system_health(status, "RUNNING", "OPEN", "REAL")
    assert len(telegram.sent) == 1 and len(logs) == 1
    message = telegram.sent[0]
    assert "15/10 11:00:00" in message and "HTTP 500 · GET /price/ohlc" in message
    assert "INTERNAL_ERROR" in message and "HEALTH → MỞ LOG" in message
    assert str(app.bridge.log_dir) in message and "daemon.log / ui.log" in message
    assert "PRIVATEKEY" not in message + str(logs) and "PRIVATESECRET" not in message + str(logs)


def test_health_popup_opens_only_current_accounts_logs_and_copies_safe_diagnostics(ui_root, tmp_path, monkeypatch):
    log_dir = tmp_path / "current account" / "logs"
    log_dir.mkdir(parents=True)
    unrelated = tmp_path / "other account" / "logs"
    unrelated.mkdir(parents=True)
    opened, copied = [], []
    monkeypatch.setattr("viking_v2.dashboard.panels.os.startfile", opened.append, raising=False)
    monkeypatch.setattr(ui_root, "bridge", SimpleNamespace(log_dir=log_dir), raising=False)
    monkeypatch.setattr(ui_root, "_api_health_hint", lambda: "GET /price/ohlc · HTTP 500", raising=False)
    monkeypatch.setattr(ui_root, "clipboard_clear", copied.clear)
    monkeypatch.setattr(ui_root, "clipboard_append", copied.append)
    before = set(ui_root.winfo_children())
    DashboardPanelsMixin._show_api_health_details(ui_root)
    top, = [child for child in ui_root.winfo_children() if child not in before]
    try:
        widgets = list(_widgets(top))
        report = next(widget for widget in widgets if isinstance(widget, ctk.CTkTextbox))
        assert report._textbox.cget("state") == "disabled"
        next(widget for widget in widgets if isinstance(widget, ctk.CTkButton) and widget.cget("text") == "MỞ LOG").invoke()
        next(widget for widget in widgets if isinstance(widget, ctk.CTkButton) and widget.cget("text") == "SAO CHÉP").invoke()
        assert opened == [str(log_dir.resolve())]
        assert "GET /price/ohlc · HTTP 500" in copied[0]
        assert str(log_dir) in copied[0] and str(unrelated) not in copied[0]
    finally:
        top.destroy()
