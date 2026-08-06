# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest

import main
from ai_advisor import schedule_settings
from core import storage_manager


class _Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_schedule_settings_roundtrip_inside_existing_brain(monkeypatch):
    brain = {"existing": {"keep": True}}
    saved_payloads = []
    monkeypatch.setattr(schedule_settings, "load_brain_settings", lambda: dict(brain))

    def save_brain(payload):
        brain.clear()
        brain.update(payload)
        saved_payloads.append(payload)

    monkeypatch.setattr(schedule_settings, "save_brain_settings", save_brain)
    saved = schedule_settings.save(
        {
            "mode": "API Trigger",
            "fixed_time": "18:30",
            "export_days": "15",
            "global_emergency": False,
            "include_previous_response": True,
        }
    )

    assert saved["mode"] == "API Trigger"
    assert saved["fixed_time"] == "18:30"
    assert saved["export_days"] == 15
    assert brain["existing"] == {"keep": True}
    assert schedule_settings.load()["include_previous_response"] is True
    assert saved["ckcs_auto_report_morning"] is True
    assert saved["ckcs_auto_report_afternoon"] is True
    assert saved["ckcs_send_api_morning"] is False
    assert saved["ckcs_send_api_afternoon"] is False
    assert saved["ckcs_morning_time"] == "11:35"
    assert saved["ckcs_afternoon_time"] == "14:50"
    assert saved_payloads


def test_storage_manager_preserves_advisor_schedule_from_account_file(
    monkeypatch, tmp_path
):
    brain_path = tmp_path / "brain_settings.json"
    brain_path.write_text(
        '{"ai_advisor_schedule": {'
        '"ckcs_liquidity_filter_enabled": true,'
        '"ckcs_liquidity_sessions": 60,'
        '"ckcs_liquidity_min_billion": 10'
        "}}",
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_manager, "BRAIN_FILE", str(brain_path))
    storage_manager.invalidate_settings_cache()

    loaded = storage_manager.load_brain_settings()

    assert loaded["ai_advisor_schedule"]["ckcs_liquidity_filter_enabled"] is True
    assert loaded["ai_advisor_schedule"]["ckcs_liquidity_sessions"] == 60
    assert loaded["ai_advisor_schedule"]["ckcs_liquidity_min_billion"] == 10


def test_schedule_rejects_invalid_fixed_time(monkeypatch):
    monkeypatch.setattr(schedule_settings, "load_brain_settings", lambda: {})
    monkeypatch.setattr(schedule_settings, "save_brain_settings", lambda _payload: None)
    with pytest.raises(ValueError, match="HH:MM"):
        schedule_settings.save({"fixed_time": "25:61"})


def test_fixed_time_fires_only_once_per_day_and_persists_date(monkeypatch):
    app = main.BotUI.__new__(main.BotUI)
    app.var_advisor_mode = _Var("API Trigger")
    app.var_advisor_fixed_time = _Var("18:30")
    app.var_advisor_global_emergency = _Var(False)
    app._advisor_last_trigger_check = 0.0
    app._advisor_last_trigger_fire = {}
    app._advisor_last_fixed_date = ""
    app._advisor_worker_active = False
    app._ckcs_api_worker_active = True
    app.trade_mgr = SimpleNamespace(state={})
    app.connector = SimpleNamespace()
    app.log_message = lambda *_args, **_kwargs: None
    saved_dates = []
    app.save_advisor_schedule_settings = lambda silent=False: saved_dates.append(
        app._advisor_last_fixed_date
    )
    started = []

    class _Thread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs)

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _Thread)
    monkeypatch.setattr(main.time, "time", lambda: 10000.0)
    monkeypatch.setattr(
        main.time,
        "strftime",
        lambda fmt: "18:30" if fmt == "%H:%M" else "2026-07-21",
    )

    main.BotUI.run_advisor_triggers_tick(app)
    app._advisor_last_trigger_check = 0.0
    main.BotUI.run_advisor_triggers_tick(app)

    assert len(started) == 1
    assert saved_dates == ["2026-07-21"]


def test_ckcs_morning_schedule_runs_even_when_bot_advisor_is_manual(monkeypatch):
    app = main.BotUI.__new__(main.BotUI)
    app.var_advisor_mode = _Var("Manual Only")
    app._advisor_last_trigger_check = 0.0
    app._advisor_worker_active = False
    app._ckcs_api_worker_active = False
    app._ckcs_auto_retry_after = {"morning": 0.0, "afternoon": 0.0}
    app.var_ckcs_auto_report_morning = _Var(True)
    app.var_ckcs_auto_report_afternoon = _Var(True)
    app.var_ckcs_send_api_morning = _Var(False)
    app.var_ckcs_send_api_afternoon = _Var(False)
    app.var_ckcs_morning_time = _Var("11:35")
    app.var_ckcs_afternoon_time = _Var("14:50")
    app._ckcs_last_morning_date = ""
    app._ckcs_last_afternoon_date = ""
    app.log_message = lambda *_args, **_kwargs: None
    started = []

    class _Thread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs)

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _Thread)
    monkeypatch.setattr(main.time, "time", lambda: 10000.0)
    monkeypatch.setattr(
        main.time,
        "strftime",
        lambda fmt: "11:35" if fmt == "%H:%M" else "2026-07-21",
    )
    monkeypatch.setattr("core.market_calendar.date_status", lambda: {"status": "TRADING"})

    main.BotUI.run_advisor_triggers_tick(app)

    assert len(started) == 1
    assert started[0]["kwargs"]["session"] == "morning"
    assert started[0]["kwargs"]["send_api"] is False


def test_ckcs_schedule_skips_holiday(monkeypatch):
    app = main.BotUI.__new__(main.BotUI)
    app.var_advisor_mode = _Var("Manual Only")
    app._advisor_last_trigger_check = 0.0
    app._advisor_worker_active = False
    app._ckcs_api_worker_active = False
    app._ckcs_auto_retry_after = {"morning": 0.0, "afternoon": 0.0}
    app.var_ckcs_auto_report_morning = _Var(True)
    app.var_ckcs_auto_report_afternoon = _Var(True)
    app.var_ckcs_send_api_morning = _Var(False)
    app.var_ckcs_send_api_afternoon = _Var(False)
    app.var_ckcs_morning_time = _Var("11:35")
    app.var_ckcs_afternoon_time = _Var("14:50")
    app._ckcs_last_morning_date = ""
    app._ckcs_last_afternoon_date = ""
    app.log_message = lambda *_args, **_kwargs: None
    started = []

    class _Thread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs)

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _Thread)
    monkeypatch.setattr(main.time, "time", lambda: 10000.0)
    monkeypatch.setattr(
        main.time,
        "strftime",
        lambda fmt: "14:50" if fmt == "%H:%M" else "2026-07-21",
    )
    monkeypatch.setattr("core.market_calendar.date_status", lambda: {"status": "HOLIDAY"})

    main.BotUI.run_advisor_triggers_tick(app)

    assert started == []
