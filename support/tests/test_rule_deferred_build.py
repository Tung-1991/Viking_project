"""Yield between RULE sections while preserving all settings and lifecycle."""
import copy
import time
import tkinter as tk

import pytest

from viking_v2 import config
from viking_v2.rules.window import RuleSettingsPopup


def pump(root, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Deferred RULE did not settle"
        ready = tk.BooleanVar(master=root, value=False)
        root.after(20, lambda: ready.set(True))
        root.wait_variable(ready)


def test_deferred_load_cannot_save_partial_settings_and_matches_full_constructor(ui_root):
    initial = config.AppSettings(signal_mode="REALTIME",
                                 sell_wait_policy="KEEP", allow_atc=True,
                                 signal_trace_enabled=False, signal_trace_interval_minutes=3,
                                 rule_parameters={"buy_ema_fast": 4, "buy_ema_slow": 9,
                                                  "sell_ema_fast": 5, "sell_ema_slow": 10,
                                                  "rsi_period": 11, "loss_lock_mode": "BLOCK",
                                                  "buy_signal_session_cross_enabled": False}).normalize()
    sync_settings = config.AppSettings.from_dict(copy.deepcopy(initial.to_dict()))
    deferred_settings = config.AppSettings.from_dict(copy.deepcopy(initial.to_dict()))
    config.save_settings(sync_settings, "BUILD_SYNC")
    config.save_settings(deferred_settings, "BUILD_DEFERRED")
    before = config.load_settings("BUILD_DEFERRED").to_dict()
    saved = []
    full = RuleSettingsPopup(ui_root, sync_settings, "BUILD_SYNC", lambda: None)
    deferred = RuleSettingsPopup(ui_root, deferred_settings, "BUILD_DEFERRED", lambda: saved.append(True),
                                 defer_build=True)
    pulses = []
    original_next = deferred._build_next
    def stage():
        original_next()
        ui_root.after(0, lambda: pulses.append(deferred._building))
    deferred._build_next = stage
    try:
        assert deferred._building
        assert deferred.save_button.cget("state") == "disabled"
        assert all(button.cget("state") == "disabled" for button in deferred.tabs._segmented_button._buttons_dict.values())
        deferred.save()
        assert saved == []
        assert config.load_settings("BUILD_DEFERRED").to_dict() == before
        pump(ui_root, lambda: not deferred._building)
        assert any(pulses), "Tk must handle input callbacks before loading completes"
        assert deferred.save_button.cget("state") == "normal"
        assert all(button.cget("state") == "normal" for button in deferred.tabs._segmented_button._buttons_dict.values())
        assert all(button.cget("state") == "normal" for button in deferred.phase_tabs._segmented_button._buttons_dict.values())
        # Save without visiting E/M or execution: all their values must survive.
        full.save()
        deferred.save()
        assert "ĐÃ LƯU" in deferred.status.cget("text")
        assert saved == [True]
        assert config.load_settings("BUILD_DEFERRED").to_dict() == config.load_settings("BUILD_SYNC").to_dict()
        deferred.shared_rsi_period.set("13")
        deferred.save()
        assert config.load_settings("BUILD_DEFERRED").rule_parameters["rsi_period"] == 13
        assert deferred.execution_preview["IND_EXIT"].cget("text").endswith(deferred._execution_preview_values()["IND_EXIT"])
    finally:
        deferred._close()
        full._close()


@pytest.mark.parametrize("close_after_section", [False, True])
def test_close_mid_build_cancels_next_stage_and_does_not_write_settings(ui_root, close_after_section, monkeypatch):
    settings = config.AppSettings()
    config.save_settings(settings, "BUILD_CANCEL")
    before = config.load_settings("BUILD_CANCEL").to_dict()
    errors = []
    monkeypatch.setattr(ui_root, "report_callback_exception", lambda *args: errors.append(args))
    popup = RuleSettingsPopup(ui_root, settings, "BUILD_CANCEL", lambda: pytest.fail("Closed load must not save"),
                              defer_build=True)
    if close_after_section:
        pump(ui_root, lambda: hasattr(popup, "ma_period"))
    handle = popup._build_after
    assert handle is not None
    popup._close()
    assert handle not in ui_root.tk.call("after", "info")
    done = tk.BooleanVar(master=ui_root, value=False)
    ui_root.after(100, lambda: done.set(True))
    ui_root.wait_variable(done)
    assert errors == []
    assert popup._build_steps == []
    assert config.load_settings("BUILD_CANCEL").to_dict() == before


def test_hide_during_load_reopens_same_completed_popup(ui_root):
    popup = RuleSettingsPopup(ui_root, config.AppSettings(), "BUILD_HIDE", lambda: None, defer_build=True)
    try:
        original_top = popup.top
        popup.hide()
        pump(ui_root, lambda: not popup._building)
        popup.show()
        assert popup.top is original_top
        assert popup.top.state() != "withdrawn"
        assert popup.save_button.cget("state") == "normal"
    finally:
        popup._close()


def test_failed_section_keeps_save_disabled_and_reports_load_failure(ui_root, monkeypatch):
    popup = RuleSettingsPopup(ui_root, config.AppSettings(), "BUILD_FAILURE", lambda: pytest.fail("Partial settings"),
                              defer_build=True)
    monkeypatch.setattr(popup, "_phase2", lambda *args: (_ for _ in ()).throw(RuntimeError("offline section failure")))
    try:
        pump(ui_root, lambda: "KHÔNG TẢI ĐƯỢC" in popup.status.cget("text"))
        assert popup._building
        assert popup.save_button.cget("state") == "disabled"
        popup.save()
        assert not (config.account_root("BUILD_FAILURE") / "settings.json").exists()
    finally:
        popup._close()
