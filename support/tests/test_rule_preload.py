"""Warm RULE while hidden without reading accounts or changing settings."""
from copy import deepcopy
from types import SimpleNamespace

import customtkinter as ctk
import pytest
import sys

from viking_v2 import config
from viking_v2.dashboard.actions import DashboardActionsMixin
from support.tests.test_rule_deferred_build import pump


def assert_hidden(popup):
    assert not popup._has_been_shown
    if sys.platform == "win32":
        assert popup.top.attributes("-alpha") == 0
        assert popup.top.attributes("-disabled")
        assert popup.top.attributes("-toolwindow")
    else:
        assert popup.top.state() == "withdrawn"


class RuleHarness(DashboardActionsMixin, ctk.CTkFrame):
    def __init__(self, root):
        ctk.CTkFrame.__init__(self, root)
        self.running = True
        self.account_id = "RULE_PRELOAD_TEST"
        self.settings = config.AppSettings().normalize()
        self._rule_settings_popup = None
        self._rule_preload_after = None
        self.visibility = []
        self.saved = []
        self.errors = []
        self.reads = []
        self.trade_state = SimpleNamespace(loss_blocks=lambda mode: self.reads.append(mode) or [])
        self.logger = SimpleNamespace(exception=lambda *args: self.errors.append(args))

    def _set_tool_popup_state(self, name, visible):
        self.visibility.append((name, visible))

    def _settings_saved(self):
        self.saved.append(deepcopy(self.settings.to_dict()))

    def stop(self):
        self.running = False
        self._cancel_rule_preload()
        popup = self._rule_settings_popup
        if popup and popup.top.winfo_exists():
            popup._close()
        self.destroy()


@pytest.fixture
def rule_app(ui_root):
    app = RuleHarness(ui_root)
    yield app
    app.stop()


def test_preload_is_hidden_read_only_and_first_click_reuses_complete_form(ui_root, rule_app):
    app = rule_app
    config.save_settings(app.settings, app.account_id)
    path = config.account_root(app.account_id) / "settings.json"
    before = path.read_bytes()
    app._schedule_rule_preload()
    handle = app._rule_preload_after
    app._schedule_rule_preload()
    assert app._rule_preload_after == handle
    app._cancel_rule_preload()
    app._preload_rule_settings()
    popup = app._rule_settings_popup
    pump(ui_root, lambda: not popup._building)
    assert_hidden(popup)
    from viking_v2.dashboard.windows import minimize_popup
    assert not minimize_popup(popup)
    assert_hidden(popup)
    assert app.visibility == app.reads == app.saved == app.errors == []
    popup.save()
    assert path.read_bytes() == before
    app._rule_settings()
    assert app._rule_settings_popup is popup
    assert not popup._building
    assert popup.save_button.cget("state") == "normal"
    assert app.visibility == [("RULE", True)]
    assert popup.top.attributes("-alpha") == 1
    if sys.platform == "win32":
        assert not popup.top.attributes("-disabled") and not popup.top.attributes("-toolwindow")
    pump(ui_root, lambda: popup._block_future is None)
    assert sorted(app.reads) == ["PAPER", "REAL"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("building", [True, False])
def test_changed_hidden_form_is_replaced_using_current_settings(ui_root, rule_app, building):
    app = rule_app
    app._preload_rule_settings()
    old = app._rule_settings_popup
    if not building:
        pump(ui_root, lambda: not old._building)
    app.settings.rule_parameters["buy_ema_fast"] = 4
    app.settings.rule_parameters["buy_ema_slow"] = 9
    app.settings.buy_fee_pct = .12
    app._rule_settings()
    popup = app._rule_settings_popup
    assert popup is not old and not old.top.winfo_exists()
    pump(ui_root, lambda: not popup._building)
    assert popup.buy_ema_fast.get() == "4" and popup.buy_ema_slow.get() == "9"
    assert float(popup.buy_fee.get()) == .12
    assert app._rule_preload_after is None
    assert not app.saved


@pytest.mark.parametrize("early", [True, False])
def test_early_click_reuses_loading_popup_and_cancels_pending_warmup(ui_root, rule_app, early):
    app = rule_app
    app._schedule_rule_preload()
    if not early:
        app._cancel_rule_preload()
        app._preload_rule_settings()
    app._rule_settings()
    popup = app._rule_settings_popup
    app._rule_settings()
    assert app._rule_settings_popup is popup
    assert app._rule_preload_after is None
    pump(ui_root, lambda: not popup._building)
    assert popup.save_button.cget("state") == "normal"
    assert not app.saved


def test_trace_and_other_settings_replacement_reuses_form_and_saves_latest_object(ui_root, rule_app):
    app = rule_app
    app._preload_rule_settings()
    popup = app._rule_settings_popup
    pump(ui_root, lambda: not popup._building)
    current = config.AppSettings.from_dict(app.settings.to_dict())
    current.signal_trace_interval_minutes = 3
    current.telegram_enabled = True
    current.watchlist = ["IDC"]
    config.save_settings(current, app.account_id)
    app.settings = current
    app._rule_settings()
    assert app._rule_settings_popup is popup and popup.settings is current
    popup.shared_rsi_period.set("13")
    popup.save()
    assert app.settings.rule_parameters["rsi_period"] == 13
    assert app.saved[-1]["rule_parameters"]["rsi_period"] == 13
    assert app.saved[-1]["signal_trace_interval_minutes"] == 3
    assert app.saved[-1]["telegram_enabled"] is True
    assert app.saved[-1]["watchlist"] == ["IDC"]


def test_close_discards_draft_and_schedules_next_hidden_form(ui_root, rule_app):
    app = rule_app
    app._rule_settings()
    popup = app._rule_settings_popup
    pump(ui_root, lambda: not popup._building)
    popup.shared_rsi_period.set("13")
    popup._close()
    handle = app._rule_preload_after
    assert handle is not None
    app._cancel_rule_preload()
    app._preload_rule_settings()
    fresh = app._rule_settings_popup
    pump(ui_root, lambda: not fresh._building)
    assert fresh.shared_rsi_period.get() == "14"
    assert_hidden(fresh)
    assert not app.saved
    app.running = False
    fresh._close()
    app._schedule_rule_preload()
    assert app._rule_preload_after is None


def test_failed_hidden_build_retries_on_actual_click(ui_root, rule_app, monkeypatch):
    app = rule_app
    app._preload_rule_settings()
    old = app._rule_settings_popup
    monkeypatch.setattr(old, "_phase2", lambda *args: (_ for _ in ()).throw(RuntimeError("fixture failure")))
    pump(ui_root, lambda: old._build_failed)
    assert_hidden(old)
    assert app.visibility == []
    app._rule_settings()
    assert app._rule_settings_popup is not old
    pump(ui_root, lambda: not app._rule_settings_popup._building)
    assert app._rule_settings_popup.save_button.cget("state") == "normal"
    assert not app.saved


def test_fast_switching_rule_tabs_and_phases_keeps_last_selected_page_visible(ui_root, rule_app):
    import tkinter as tk
    app = rule_app
    app._rule_settings()
    popup = app._rule_settings_popup
    pump(ui_root, lambda: not popup._building)
    for tab in ("E/M", "THỰC THI", "NGHIỆP VỤ"):
        popup.tabs.set(tab)
    for phase in list(popup.phase_tabs._tab_dict) + [popup.phase_tabs.get()]:
        popup.phase_tabs.set(phase)
    selected_phase = popup.phase_tabs.get()
    settled = tk.BooleanVar(master=ui_root, value=False)
    ui_root.after(200, lambda: settled.set(True))
    ui_root.wait_variable(settled)
    assert popup.tabs.tab("NGHIỆP VỤ").winfo_ismapped()
    assert popup.phase_tabs.tab(selected_phase).winfo_ismapped()
    assert not app.saved


@pytest.mark.skipif(sys.platform != "win32", reason="Transparent layout preparation is Windows-only")
def test_click_during_page_preparation_stops_hidden_page_changes(ui_root, rule_app, monkeypatch):
    app = rule_app
    app._preload_rule_settings()
    popup = app._rule_settings_popup
    original = popup._prime_page
    opened = []
    def prime(tabs, name):
        original(tabs, name)
        if tabs is popup.tabs and name == "E/M" and not opened:
            opened.append(True)
            app._rule_settings()
    monkeypatch.setattr(popup, "_prime_page", prime)
    pump(ui_root, lambda: not popup._building)
    assert opened == [True]
    assert app._rule_settings_popup is popup
    assert popup.tabs.get() == "NGHIỆP VỤ"
    assert popup.phase_tabs.get() == next(iter(popup.phase_tabs._tab_dict))
    assert popup.top.attributes("-alpha") == 1
    assert not app.saved
