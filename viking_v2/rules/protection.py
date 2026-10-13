"""Shared sale basis and repeat eligibility for ARM and pre-ARM DYNAMIC."""
from __future__ import annotations

PROTECTION_EVENTS = frozenset({"NORMAL_PROTECTION", "DYNAMIC_PROTECTION"})


def sale_options(params, stage):
    dynamic = stage == "DYN"
    return (params.dynamic_sell_pct if dynamic else params.normal_sell_pct,
            params.dynamic_sell_basis if dynamic else params.normal_sell_basis,
            bool(params.dynamic_repeat_enabled if dynamic else params.normal_repeat_enabled))


def sale_quantity(remaining, original, fraction, basis="REMAINING"):
    from ..trading.portfolio import round_lot_down, sell_quantity_for_fraction
    base = max(remaining, original) if basis == "ORIGINAL" and fraction < 1 else remaining
    return min(round_lot_down(remaining), sell_quantity_for_fraction(base, fraction))


def repeat_ready(position, *, stage, policy, repeat, price, level):
    prefix = "dynamic" if stage == "DYN" else "normal"
    alert = policy == "ALERT"
    count = int(position.get(prefix + ("_alert_count" if alert else "_protection_count"), 0) or 0)
    if not alert and position.get(prefix + "_protection_done"):
        count = max(1, count)
    if count == 0:
        return True
    if not repeat:
        return False
    previous = float(position.get(prefix + ("_last_alert_price" if alert else "_last_trigger_price"), 0) or 0)
    if previous <= 0:
        return False  # Legacy state is baselined by the durable metric writer.
    rearmed = bool(position.get(prefix + "_repeat_rearmed"))
    last_level = float(position.get(prefix + "_last_trigger_level", 0) or 0)
    return bool(rearmed or price < previous - 1e-9 or
                (abs(price - previous) > 1e-9 and last_level > 0 and level > last_level + 1e-9))
