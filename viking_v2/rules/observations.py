"""Read-only evidence for EMA transitions; never creates or approves orders."""
from __future__ import annotations

from math import isfinite
from typing import Any


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def ema_cross_evidence(current: dict, previous: dict, *, required: bool,
                       use_ema: bool = True, observed_at: str = "") -> dict:
    fast, slow = (finite(current.get(f"buy_ema_{side}")) for side in ("fast", "slow"))
    prior_fast, prior_slow = (finite(previous.get(f"buy_ema_{side}")) for side in ("fast", "slow"))
    ready = current.get("signal_ready") is not False and all(
        value is not None for value in (fast, slow, prior_fast, prior_slow))
    observed_at = str(current.get("indicator_observed_at") or observed_at or "")
    up = bool(ready and prior_fast <= prior_slow and fast > slow)
    down = bool(ready and prior_fast >= prior_slow and fast < slow)
    state = ("OFF" if not use_ema or not required else "UNKNOWN" if not ready
             else "CROSSED_UP" if up else "WAIT_DOWN" if fast > slow else "WAIT_UP")
    return {"required": bool(required and use_ema), "state": state,
            "previous_fast": prior_fast, "previous_slow": prior_slow,
            "current_fast": fast, "current_slow": slow,
            "crossed_up": up, "crossed_down": down,
            "observed_at": str(observed_at or ""),
            "cross_at": str(observed_at or "") if up else ""}


def ema_cross_caption(evidence: dict | None, window: dict | None = None) -> tuple[str, str]:
    """Return compact text and a colour role from recorded backend evidence."""
    evidence, window = evidence or {}, window or {}
    state = str(evidence.get("state", "UNKNOWN"))
    when = str(evidence.get("cross_at", "") or "")
    accepted = window.get("ema_cross") or {}
    if (evidence.get("required") is True and window.get("state") in {"WAITING", "ALLOWED"}
            and accepted.get("crossed_up") is True):
        # An accepted candidate may wait for the buy window without crossing
        # again on every tick. Do not revive cancelled/expired candidates.
        state, when = "CROSSED_UP", str(accepted.get("cross_at", "") or window.get("signal_time", "") or when)
    clock = when[11:19] if len(when) >= 19 else ""
    return {
        "OFF": ("CẮT EMA · OFF", "muted"),
        "UNKNOWN": ("CẮT EMA · CHƯA ĐỦ DỮ LIỆU", "muted"),
        "WAIT_DOWN": ("CẮT EMA · CHỜ XUỐNG", "wait"),
        "WAIT_UP": ("CẮT EMA · CHỜ LÊN", "wait"),
        "CROSSED_UP": ("CẮT EMA · ĐÃ LÊN" + (f" {clock}" if clock else ""), "ok"),
    }.get(state, ("CẮT EMA · —", "muted"))


def exit_conditions(marks: dict, params: dict) -> bool | None:
    if marks.get("signal_ready") is False:
        return None
    chosen = []
    for enabled, left, right in (
        (params.get("sell_signal_use_ema", True), marks.get("sell_ema_fast"), marks.get("sell_ema_slow")),
        (params.get("sell_signal_use_rsi", True), marks.get("rsi"), marks.get("rsi_previous")),
    ):
        if enabled:
            a, b = finite(left), finite(right)
            chosen.append(None if a is None or b is None else a < b)
    return None if not chosen or any(value is None for value in chosen) else all(chosen)
