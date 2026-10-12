"""Preserve UI updates while avoiding duplicate draws and nested idle work."""
import tkinter as tk

import customtkinter as ctk
import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.windows import StatusLabel


def test_unchanged_status_does_not_redraw_but_changed_values_are_visible(ui_root, monkeypatch):
    label = StatusLabel(ui_root, text="CHỜ BUY", text_color="#FF9900")
    draws = []
    native_updates = []
    original_draw = label._draw
    original_configure = label._label.configure
    monkeypatch.setattr(label, "_draw", lambda *args, **kwargs: (draws.append(True), original_draw(*args, **kwargs))[-1])
    monkeypatch.setattr(label._label, "configure", lambda *args, **kwargs: (
        native_updates.append(kwargs), original_configure(*args, **kwargs))[-1])
    try:
        for _ in range(20):
            label.configure(text="CHỜ BUY", text_color="#FF9900")
        assert draws == native_updates == []
        label.configure(text="ĐÃ XẾP BUY", text_color="#22C55E")
        assert label.cget("text") == label._label.cget("text") == "ĐÃ XẾP BUY"
        assert label._label.cget("fg") == "#22C55E"
        assert len(draws) == 1
        draws.clear()
        native_updates.clear()
        label.configure(text="ĐÃ XẾP BUY", text_color="#22C55E", wraplength=200)
        assert label.cget("wraplength") == 200
        assert native_updates
        label.configure(require_redraw=True)
        assert draws
    finally:
        label.destroy()


def test_status_textvariable_keeps_native_tk_updates(ui_root):
    value = tk.StringVar(master=ui_root, value="OTP CÒN HẠN")
    label = StatusLabel(ui_root, textvariable=value)
    try:
        value.set("OTP HẾT HẠN")
        assert label._label.cget("text") == "OTP HẾT HẠN"
        label.configure(text_color="#EF4444")
        assert label._label.cget("text") == "OTP HẾT HẠN"
    finally:
        label.destroy()


@pytest.mark.parametrize("popup_type", ["RULE", "CONNECTION", "BACKTEST", "HISTORY", "TABLE"])
def test_popup_creation_leaves_pending_idle_callbacks_to_normal_event_loop(ui_root, popup_type):
    from viking_v2.backtest.window import BacktestPopup
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup
    from viking_v2.dashboard.windows import DataTablePopup, HistoryPopup
    from viking_v2.rules.window import RuleSettingsPopup

    pending = []
    ui_root.after_idle(lambda: pending.append("normal idle"))
    settings = AppSettings()
    factories = {
        "RULE": lambda: RuleSettingsPopup(ui_root, settings, "PAPER", lambda: None),
        "CONNECTION": lambda: ConnectionPopup(ui_root, settings, "PAPER", DNSEClient(), lambda: None),
        "BACKTEST": lambda: BacktestPopup(ui_root, settings, None),
        "HISTORY": lambda: HistoryPopup(ui_root, lambda mode: [], signals_provider=lambda: []),
        "TABLE": lambda: DataTablePopup(ui_root, title="OFFLINE", columns=(("symbol", "MÃ", 100, "w"),),
                                        rows_provider=lambda mode: []),
    }
    popup = factories[popup_type]()
    try:
        assert pending == []
        ui_root.update_idletasks()
        assert pending == ["normal idle"]
        assert popup.top.winfo_exists()
    finally:
        closer = getattr(popup, "close", None) or popup._close
        closer()
