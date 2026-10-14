"""Read-only START thresholds; this module never changes rule or order state."""
from __future__ import annotations

from math import isfinite
from typing import Any

from ..rules.business import StaticRuleParameters, protect_level
from ..rules.protection import policy_for_stage


def _positive(value: Any) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if isfinite(value) and value > 0 else 0.0


def dynamic_start_preview(entry: float, params: dict[str, Any], details: dict[str, Any], *,
                          position: bool = False, enabled: bool = False,
                          mfe: float | None = None) -> dict[str, Any]:
    """Separate an unfilled order illustration from an observed position."""
    rule = StaticRuleParameters.from_dict(params)
    entry = _positive(entry)
    atr = _positive(details.get("atr14_daily_pct", details.get("normal_atr_pct")))
    start_pct = atr * rule.normal_atr_activation_multiplier if rule.normal_atr_activation_enabled else 0.0
    start = entry * (1 + start_pct / 100) if entry and (start_pct > 0 or not rule.normal_atr_activation_enabled) else 0.0
    known_mfe = mfe is not None and isfinite(float(mfe))
    peak = max(0.0, float(mfe)) if known_mfe else 0.0
    previous = _positive(details.get("normal_trigger_price")) if details.get("protection_stage") == "DYN" else 0.0
    level = protect_level(
        entry, peak, rule.normal_arm_pct, rule.normal_giveback_pct,
        dynamic_enabled=rule.normal_dynamic_enabled, atr_pct=atr,
        atr_multiplier=rule.normal_atr_multiplier,
        atr_activation_multiplier=rule.normal_atr_activation_multiplier,
        atr_activation_enabled=rule.normal_atr_activation_enabled,
        atr_trail_enabled=rule.normal_atr_trail_enabled,
        retention_pct=rule.normal_retention_pct, retention_until_pct=rule.normal_retention_until_pct,
        retention_enabled=rule.normal_retention_enabled, retention_until_enabled=rule.normal_retention_until_enabled,
        sl_price=_positive(details.get("sl_price")), previous_trigger_price=previous,
    )
    if not rule.normal_dynamic_enabled:
        state = "OFF"
    elif entry <= 0:
        state = "CHỜ GIÁ VỐN"
    elif position and enabled and known_mfe and peak + 1e-9 >= rule.normal_arm_pct:
        state = "SAU ARM"
    elif rule.normal_atr_activation_enabled and atr <= 0 and not (position and enabled and level.active):
        state = "CHỜ ATR"
    elif not position or not enabled:
        state = "PREVIEW"
    elif not known_mfe:
        state = "CHỜ ĐỈNH"
    elif level.active:
        state = "ĐÃ BẬT"
    elif not rule.normal_atr_trail_enabled and not rule.normal_retention_enabled:
        state = "CHƯA CHỌN BẢO VỆ"
    else:
        state = "CHƯA TỚI"
    return {
        "entry": entry, "start": start, "start_pct": start_pct,
        "start_enabled": rule.normal_atr_activation_enabled,
        "policy": policy_for_stage(params, "DYN"), "state": state,
        "position": position, "enabled": enabled, "mfe": peak if known_mfe else None,
    }


def start_caption(preview: dict[str, Any], *, compact: bool = False) -> str:
    if preview["state"] == "OFF":
        return "DYN OFF"
    price = f"{preview['start'] * 1000:,.0f}" if preview["start"] > 0 else "—"
    target = price if preview["start_enabled"] else "CÓ LÃI"
    prefix = f"DYN {preview['policy']}"
    state = {"ĐÃ BẬT": "ĐẠT", "CHƯA CHỌN BẢO VỆ": "CHƯA CHỌN"}.get(preview["state"], preview["state"]) if compact else preview["state"]
    return f"{prefix} · START {target} · {state}"
