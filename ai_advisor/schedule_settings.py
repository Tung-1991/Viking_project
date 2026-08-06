# -*- coding: utf-8 -*-
"""Account-scoped runtime settings for the AI Advisor scheduler.

The values live inside the existing brain_settings.json so the Advisor does
not create another user-facing configuration file.
"""

from datetime import datetime

from core.storage_manager import load_brain_settings, save_brain_settings


SETTINGS_KEY = "ai_advisor_schedule"
DEFAULT_SETTINGS = {
    "mode": "Manual Only",
    "fixed_time": "",
    "export_days": 7,
    "global_emergency": True,
    "include_previous_response": False,
    "last_fixed_date": "",
    "ckcs_auto_report_morning": True,
    "ckcs_auto_report_afternoon": True,
    "ckcs_send_api_morning": False,
    "ckcs_send_api_afternoon": False,
    "ckcs_morning_time": "11:35",
    "ckcs_afternoon_time": "14:50",
    "ckcs_report_days": 15,
    "ckcs_liquidity_filter_enabled": False,
    "ckcs_liquidity_sessions": 60,
    "ckcs_liquidity_min_billion": 10.0,
    "ckcs_last_morning_date": "",
    "ckcs_last_afternoon_date": "",
}


def _bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def validate_fixed_time(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError as exc:
        raise ValueError("Giờ Advisor phải theo định dạng HH:MM, ví dụ 18:30.") from exc


def normalize(data, *, strict_time=False):
    raw = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        raw.update(data)
    mode = str(raw.get("mode") or "Manual Only").strip()
    if mode not in {"Manual Only", "API Trigger"}:
        mode = "Manual Only"
    try:
        fixed_time = validate_fixed_time(raw.get("fixed_time"))
    except ValueError:
        if strict_time:
            raise
        fixed_time = ""
    try:
        export_days = int(float(raw.get("export_days", 7) or 7))
    except (TypeError, ValueError):
        export_days = 7
    export_days = max(1, min(2500, export_days))
    def _date(value):
        text = str(value or "").strip()
        try:
            if text:
                datetime.strptime(text, "%Y-%m-%d")
            return text
        except ValueError:
            return ""

    def _time(key, default):
        try:
            return validate_fixed_time(raw.get(key, default)) or default
        except ValueError:
            if strict_time:
                raise
            return default

    try:
        ckcs_report_days = int(float(raw.get("ckcs_report_days", 15) or 15))
    except (TypeError, ValueError):
        ckcs_report_days = 15
    try:
        ckcs_liquidity_min_billion = float(
            raw.get("ckcs_liquidity_min_billion", 10.0) or 10.0
        )
    except (TypeError, ValueError):
        ckcs_liquidity_min_billion = 10.0
    try:
        ckcs_liquidity_sessions = int(
            float(raw.get("ckcs_liquidity_sessions", 60) or 60)
        )
    except (TypeError, ValueError):
        ckcs_liquidity_sessions = 60
    return {
        "mode": mode,
        "fixed_time": fixed_time,
        "export_days": export_days,
        "global_emergency": _bool(raw.get("global_emergency"), True),
        "include_previous_response": _bool(raw.get("include_previous_response"), False),
        "last_fixed_date": _date(raw.get("last_fixed_date")),
        "ckcs_auto_report_morning": _bool(raw.get("ckcs_auto_report_morning"), True),
        "ckcs_auto_report_afternoon": _bool(raw.get("ckcs_auto_report_afternoon"), True),
        "ckcs_send_api_morning": _bool(raw.get("ckcs_send_api_morning"), False),
        "ckcs_send_api_afternoon": _bool(raw.get("ckcs_send_api_afternoon"), False),
        "ckcs_morning_time": _time("ckcs_morning_time", "11:35"),
        "ckcs_afternoon_time": _time("ckcs_afternoon_time", "14:50"),
        "ckcs_report_days": max(1, min(2500, ckcs_report_days)),
        "ckcs_liquidity_filter_enabled": _bool(
            raw.get("ckcs_liquidity_filter_enabled"), False
        ),
        # Data Engine hiện nạp tối đa khoảng 100 nến/ngày cho mỗi group.
        "ckcs_liquidity_sessions": max(5, min(100, ckcs_liquidity_sessions)),
        "ckcs_liquidity_min_billion": max(
            0.1, min(100000.0, ckcs_liquidity_min_billion)
        ),
        "ckcs_last_morning_date": _date(raw.get("ckcs_last_morning_date")),
        "ckcs_last_afternoon_date": _date(raw.get("ckcs_last_afternoon_date")),
    }


def load():
    brain = load_brain_settings()
    return normalize(brain.get(SETTINGS_KEY, {}))


def save(values):
    brain = load_brain_settings()
    current = brain.get(SETTINGS_KEY, {})
    merged = dict(current) if isinstance(current, dict) else {}
    merged.update(values or {})
    clean = normalize(merged, strict_time=True)
    brain[SETTINGS_KEY] = clean
    save_brain_settings(brain)
    return clean
