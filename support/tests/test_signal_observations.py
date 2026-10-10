from copy import deepcopy

import pytest

from viking_v2.config import AppSettings
from viking_v2.rules.observations import ema_cross_evidence, ema_cross_caption
from viking_v2.storage import SignalLog


def row(clock="13:00:00", **changes):
    return {"timestamp": "2026-10-09 " + clock, "symbol": "HDB", "execution_mode": "REAL",
            "signal": "BUY", "record_kind": "SIGNAL_EVENT", "acted": "WAIT",
            "blocked_by": "BUY_WINDOW_WAIT", "buy_window_state": "WAITING",
            "signal_cycle": "C1", "candle_key": "D1", "price": 22.5,
            "ema_fast": 22.45, "ema_slow": 22.40, "rsi": 58, "rsi_previous": 57.5,
            **changes}


@pytest.mark.parametrize("restart", [False, True])
def test_failed_csv_append_does_not_claim_observation_and_can_retry(tmp_path, monkeypatch, restart):
    from pathlib import Path
    path = tmp_path / "signals.csv"
    log = SignalLog(path)
    original_open = Path.open
    def fail_append(self, mode="r", *args, **kwargs):
        if self == path and mode == "a":
            raise OSError("SIMULATED_DISK_ERROR")
        return original_open(self, mode, *args, **kwargs)
    with monkeypatch.context() as faults:
        faults.setattr(Path, "open", fail_append)
        with pytest.raises(OSError):
            log.observe(row(), entry_condition=True, exit_condition=False)
    assert log.state.read() == {} and not log.observations.read()
    if restart:
        log = SignalLog(path)
    assert log.observe(row(), entry_condition=True, exit_condition=False) == ["ENTRY"]
    assert len(log.read_all()) == 1


def test_failed_dedup_state_write_retries_without_duplicate_csv_and_survives_restart(tmp_path, monkeypatch):
    path = tmp_path / "signals.csv"
    log = SignalLog(path)
    with monkeypatch.context() as faults:
        faults.setattr(log.state, "write", lambda _value: (_ for _ in ()).throw(OSError("SIMULATED_STATE_ERROR")))
        with pytest.raises(OSError):
            log.observe(row(), entry_condition=True, exit_condition=False)
    assert len(log.read_all()) == 1 and log._state_dirty
    log.observe(row(), entry_condition=True, exit_condition=False)
    assert len(log.read_all()) == 1 and not log._state_dirty
    assert not SignalLog(path).observe(row(), entry_condition=True, exit_condition=False)


def test_log_io_failure_buffers_original_entry_and_loss_without_interrupting_runtime(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from viking_v2.dashboard.actions import DashboardActionsMixin
    log = SignalLog(tmp_path / "signals.csv")
    app = SimpleNamespace(signal_log=log, logger=Mock())
    first, lost = row(), row("13:30:00", signal="", blocked_by="NO_NEW_BUY_SIGNAL")
    before = deepcopy([first, lost])
    original = log.observe
    with monkeypatch.context() as faults:
        faults.setattr(log, "observe", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("SIMULATED_DISK_ERROR")))
        DashboardActionsMixin._append_signal_log(app, first, entry_condition=True, exit_condition=False)
        DashboardActionsMixin._append_signal_log(app, lost, entry_condition=False, exit_condition=False)
    # A later tick no longer carries the ENTRY pulse. Retain and replay the original observation, not the order.
    DashboardActionsMixin._append_signal_log(app, {**lost, "timestamp": "2026-10-09 13:31:00"},
                                             entry_condition=False, exit_condition=False)
    saved = log.read_all()
    assert [value["signal_event"] for value in saved] == ["ENTRY", "ENTRY_LOST"]
    assert [value["timestamp"] for value in saved] == [first["timestamp"], lost["timestamp"]]
    assert not app._pending_signal_logs and app.logger.warning.call_count == 1
    assert [first, lost] == before and log.observe == original


def test_log_outage_dedup_is_per_symbol_not_just_last_global_poll(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from viking_v2.dashboard.actions import DashboardActionsMixin
    app = SimpleNamespace(signal_log=SignalLog(tmp_path / "signals.csv"), logger=Mock())
    with monkeypatch.context() as faults:
        faults.setattr(app.signal_log, "observe", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("SIMULATED_DISK_ERROR")))
        for _ in range(10):
            for symbol in ("HDB", "IDC"):
                DashboardActionsMixin._append_signal_log(app, row(symbol=symbol), entry_condition=True, exit_condition=False)
    assert len(app._pending_signal_logs) == 2
    DashboardActionsMixin._append_signal_log(app, row(), entry_condition=True, exit_condition=False)
    assert len(app.signal_log.read_all()) == 2 and not app._pending_signal_logs


def test_direct_order_result_log_also_retries_without_replanning(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from viking_v2.dashboard.actions import DashboardActionsMixin
    app = SimpleNamespace(signal_log=SignalLog(tmp_path / "signals.csv"), logger=Mock())
    value = row(signal_event="ORDER_RESULT", blocked_by="BROKER_FAILED")
    with monkeypatch.context() as faults:
        faults.setattr(app.signal_log, "record", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("SIMULATED_DISK_ERROR")))
        DashboardActionsMixin._append_signal_log(app, value, direct=True)
    DashboardActionsMixin._append_signal_log(app, value, direct=True)
    assert len(app.signal_log.read_all()) == 1 and not app._pending_signal_logs


def test_entry_only_notes_appearance_loss_reappearance_and_processing_change(tmp_path):
    log = SignalLog(tmp_path / "signals.csv")
    assert log.observe(row(), entry_condition=True, exit_condition=False) == ["ENTRY"]
    assert not log.observe(row("13:01:00", price=22.55, confirmation_minutes=1), entry_condition=True, exit_condition=False)
    # A fresh-cross signal is a pulse. Its NONE does not mean EMA/RSI were lost.
    assert not log.observe(row("13:02:00", signal="", blocked_by="NO_NEW_BUY_SIGNAL"), entry_condition=True, exit_condition=False)
    assert not log.observe(row("13:03:00", signal=""), entry_condition=None, exit_condition=None)
    assert log.observe(row("13:30:00", signal="", blocked_by="NO_NEW_BUY_SIGNAL"), entry_condition=False, exit_condition=False) == ["ENTRY_LOST"]
    assert not log.observe(row("13:31:00", signal=""), entry_condition=False, exit_condition=False)
    assert log.observe(row("13:39:00", signal_cycle="C2"), entry_condition=True, exit_condition=False) == ["ENTRY"]
    assert log.observe(row("14:00:00", signal_cycle="C2", acted="BUY", blocked_by="", buy_window_state="ALLOWED"), entry_condition=True, exit_condition=False) == ["ENTRY"]
    assert [r["signal_event"] for r in log.read_all()] == ["ENTRY", "ENTRY_LOST", "ENTRY", "ENTRY"]


def test_exit_does_not_rearm_on_none_or_price_only_and_survives_restart(tmp_path):
    path = tmp_path / "signals.csv"
    value = row(signal="SELL", blocked_by="NO_NEW_BUY_SIGNAL", signal_cycle="")
    assert SignalLog(path).observe(value, entry_condition=False, exit_condition=True) == ["EXIT_E"]
    reopened = SignalLog(path)
    assert not reopened.observe({**value, "signal": ""}, entry_condition=False, exit_condition=True)
    assert not reopened.observe({**value, "timestamp": "2026-10-09 13:10:00", "price": 22.3}, entry_condition=False, exit_condition=True)
    assert not reopened.observe({**value, "signal": ""}, entry_condition=False, exit_condition=False)
    assert reopened.observe({**value, "timestamp": "2026-10-09 13:20:00"}, entry_condition=False, exit_condition=True) == ["EXIT_E"]
    assert len(reopened.read_all()) == 2


def test_entry_loss_and_real_exit_are_distinct_events_with_correct_ema_pairs(tmp_path):
    log = SignalLog(tmp_path / "signals.csv")
    log.observe(row(), entry_condition=True, exit_condition=False)
    result = log.observe(row("13:40:00", signal="SELL", blocked_by="NO_NEW_BUY_SIGNAL",
                             exit_ema_fast=21, exit_ema_slow=22), entry_condition=False, exit_condition=True)
    assert result == ["ENTRY_LOST", "EXIT_E"]
    lost, exited = log.read_all()[-2:]
    assert float(lost["ema_fast"]) == 22.45 and float(exited["ema_fast"]) == 21


def test_event_state_is_book_session_and_rule_profile_local(tmp_path):
    log = SignalLog(tmp_path / "signals.csv")
    assert log.observe(row(), entry_condition=True, exit_condition=False)
    assert log.observe(row(execution_mode="PAPER"), entry_condition=True, exit_condition=False)
    assert log.observe(row(timestamp="2026-10-12 13:00:00", signal_cycle="C2"), entry_condition=True, exit_condition=False)
    assert log.observe(row(timestamp="2026-10-12 13:01:00", observation_profile="changed", signal_cycle="C3"), entry_condition=True, exit_condition=False)


@pytest.mark.parametrize("previous,current,state", [
    ((22.45, 22.4), (22.46, 22.41), "WAIT_DOWN"),
    ((22.45, 22.4), (22.39, 22.4), "WAIT_UP"),
    ((22.4, 22.4), (22.45, 22.4), "CROSSED_UP"),
    ((None, 22.4), (22.45, 22.4), "UNKNOWN"),
])
def test_cross_preview_uses_consecutive_backend_numbers(previous, current, state):
    marks = lambda values: dict(buy_ema_fast=values[0], buy_ema_slow=values[1])
    evidence = ema_cross_evidence(marks(current), marks(previous), required=True,
                                  observed_at="2026-10-09T13:39:31+07:00")
    assert evidence["state"] == state
    assert evidence["crossed_up"] == (state == "CROSSED_UP")
    assert (ema_cross_caption(evidence)[1] == "ok") == (state == "CROSSED_UP")


def test_accepted_window_keeps_cross_evidence_but_cancel_does_not():
    evidence = {"required": True, "state": "WAIT_DOWN"}
    pending = {"state": "WAITING", "signal_time": "2026-10-09T13:39:31+07:00",
               "ema_cross": {"crossed_up": True, "cross_at": "2026-10-09T13:39:31+07:00"}}
    assert ema_cross_caption(evidence, pending) == ("CẮT EMA · ĐÃ LÊN 13:39:31", "ok")
    assert ema_cross_caption(evidence, {**pending, "state": "CANCELLED"})[1] == "wait"
    assert ema_cross_evidence({}, {}, required=False)["state"] == "OFF"
    assert ema_cross_caption(evidence, {"state": "WAITING"})[1] == "wait"


def test_capture_toggle_inside_signals_does_not_toggle_bot_or_create_signals(ui_root, tmp_path):
    from viking_v2.dashboard.windows import HistoryPopup
    from viking_v2.services.signal_trace import SignalTraceStore
    configured = AppSettings()
    before = deepcopy(configured.to_dict())
    def save(options):
        for key, value in options.items():
            setattr(configured, key, value)
    popup = HistoryPopup(ui_root, lambda _mode: [], trace_store=SignalTraceStore(tmp_path / "trace.db"),
                         trace_settings_provider=lambda: configured, on_trace_settings=save)
    try:
        popup._open_recording_settings()
        assert popup.recording_popup.enabled.get()
        popup.recording_popup.enabled.set(False)
        popup.recording_popup.save()
        assert not configured.signal_trace_enabled
        assert configured.to_dict() == {**before, "signal_trace_enabled": False}
        assert not popup.trace_store.path.exists() and not popup.signal_tree.get_children()
    finally:
        popup.close()


def test_compact_history_keeps_new_cycles_and_does_not_modify_legacy_journal():
    from viking_v2.dashboard.windows import signal_rows_by_day
    raw = [row(record_kind="", signal="SELL", signal_cycle="", blocked_by="NO_NEW_BUY_SIGNAL"),
           row("13:01:00", record_kind="", signal="SELL", signal_cycle="", blocked_by="NO_NEW_BUY_SIGNAL"),
           row("13:39:00", signal_cycle="C2"), row("13:40:00", signal_cycle="C3")]
    original = deepcopy(raw)
    result = signal_rows_by_day(raw, compact=True)[0]["rows"]
    assert len(result) == 3 and raw == original
    assert result[-1]["legacy_repeat_count"] == 2
    assert result[0]["signal_cycle"] == "C3"


def test_phase2_cross_preview_occupies_existing_right_hand_space(ui_root):
    import customtkinter as ctk
    from viking_v2.dashboard.panels import DashboardPanelsMixin
    from viking_v2.dashboard.view import COL_WARN, COL_GREEN
    parent = ctk.CTkFrame(ui_root)
    parent.grid(row=0, column=0, sticky="nsew")
    subject = DashboardPanelsMixin()
    subject.settings = AppSettings()
    subject._build_order_preview_tab(parent)
    try:
        assert subject.preview_rule_cross.master is subject.preview_rule_ema.master
        assert subject.preview_rule_cross.grid_info()["column"] == 2
        assert subject.preview_rule_ema.grid_info()["column"] == 1
        evidence = ema_cross_evidence(dict(buy_ema_fast=22.45, buy_ema_slow=22.4),
                                      dict(buy_ema_fast=22.44, buy_ema_slow=22.4), required=True)
        subject._render_ema_cross_preview({"ema_cross": evidence})
        assert subject.preview_rule_cross.cget("text") == "CHỜ XUỐNG"
        assert subject.preview_rule_cross.cget("text_color") == COL_WARN
        subject._render_ema_cross_preview({"ema_cross": evidence, "buy_window": {
            "state": "WAITING", "signal_time": "2026-10-09T13:39:31+07:00",
            "ema_cross": {"crossed_up": True, "cross_at": "2026-10-09T13:39:31+07:00"}}})
        assert subject.preview_rule_cross.cget("text") == "ĐÃ LÊN 13:39:31"
        assert subject.preview_rule_cross.cget("text_color") == COL_GREEN
        subject.settings.rule_parameters["buy_signal_require_ema_cross"] = False
        subject._render_ema_cross_preview({})
        assert subject.preview_rule_cross.cget("text") == "OFF"
    finally:
        parent.destroy()


def test_history_ema_cross_uses_saved_evidence_and_old_rows_remain_unknown():
    from viking_v2.dashboard.windows import signal_rows_by_day
    values = [row(ema_cross_state="WAIT_DOWN", ema_cross_required=True,
                  ema_cross_at="2026-10-09T13:39:31+07:00"),
              row("14:00:00", signal_cycle="C2", record_kind="")]
    displayed = signal_rows_by_day(values)[0]["rows"]
    assert displayed[0]["ema_cross_display"] == "Bản cũ chưa lưu"
    assert displayed[1]["ema_cross_display"] == "ĐÃ LÊN 13:39:31"


def test_buy_window_preserves_actual_cross_proof_without_requiring_another_cross_at_14h():
    from datetime import datetime
    from viking_v2.models import StrategyDecision
    from viking_v2.rules.business import StaticRule, StaticRuleParameters
    from viking_v2.rules.entry_filters import apply_buy_filters
    from viking_v2.trading.market import VN_TZ
    rule = StaticRule(StaticRuleParameters(whipsaw_enabled=False))
    marks = dict(buy_ema_fast=22.45, buy_ema_slow=22.4, rsi=58, rsi_previous=57.5)
    evidence = ema_cross_evidence(marks, dict(buy_ema_fast=22.39, buy_ema_slow=22.4),
                                  required=True, observed_at="2026-10-09T13:39:31+07:00")
    first = StrategyDecision("BUY", "HDB", "BUY_SIGNAL", signal="BUY",
                             details={"indicators": marks, "ema_cross": evidence})
    context = dict(signal_mode="REALTIME", symbol="HDB", confirmed_market_state="ACCUMULATION",
                   indicator_snapshot=marks, previous_indicators=marks)
    portfolio = dict(available_capital=15000000)
    state, waiting = apply_buy_filters(rule, first, context, portfolio, {},
        observed_at=datetime(2026, 10, 9, 13, 39, 31, tzinfo=VN_TZ), exchange="HOSE")
    assert state["window"]["ema_cross"]["crossed_up"] is True
    assert ema_cross_caption(evidence, waiting.details["buy_window"])[1] == "ok"
    next_observation = ema_cross_evidence(marks, marks, required=True)
    assert next_observation["crossed_up"] is False
    later = StrategyDecision("WAIT", "HDB", "NO_NEW_BUY_SIGNAL",
                             details={"indicators": marks, "ema_cross": next_observation})
    state, released = apply_buy_filters(rule, later, context, portfolio, state,
        observed_at=datetime(2026, 10, 9, 14, 0, 0, tzinfo=VN_TZ), exchange="HOSE")
    assert released.action == "BUY" and state == {}
    assert ema_cross_caption(next_observation, released.details["buy_window"]) == ("CẮT EMA · ĐÃ LÊN 13:39:31", "ok")
