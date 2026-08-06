# -*- coding: utf-8 -*-
import time


def _as_float(value, default=0.0):
    try:
        return float(value or default)
    except Exception:
        return default


def evaluate(state=None, connector=None):
    """
    Advisor API triggers are intentionally narrow:
    - fixed-time reports are handled by the UI caller;
    - this function only detects true global cooldown/brake emergency.

    Symbol cooldowns and raw daily-loss/basket/streak counters are recorded in
    Advisor History, but they do not auto-send API reports here.
    """
    state = state or {}
    now = time.time()
    reasons = []

    active_brake = state.get("active_brake") or {}
    global_brake = active_brake.get("global") if isinstance(active_brake, dict) else None
    if isinstance(global_brake, dict):
        until = _as_float(global_brake.get("until"), 0.0)
        if until <= 0 or until > now:
            reasons.append("global_cooldown_emergency")

    return reasons
