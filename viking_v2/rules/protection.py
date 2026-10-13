"""Shared sale basis and repeat eligibility for ARM and pre-ARM DYNAMIC."""
from __future__ import annotations

PROTECTION_EVENTS = frozenset({"NORMAL_PROTECTION", "DYNAMIC_PROTECTION"})


def policy_for_stage(params, stage):
    """Resolve ARM/DYNAMIC independently, retaining legacy shared settings."""
    from ..exit_modes import normalize_normal_policy
    read = params.get if isinstance(params, dict) else lambda key, default=None: getattr(params, key, default)
    arm = read("normal_policy", "AUTO")
    return normalize_normal_policy((read("dynamic_policy", None) or arm) if stage == "DYN" else arm)


def protection_stages(reason, details):
    """Identify old/new protection requests without mistaking SL/E for them."""
    details = details if isinstance(details, dict) else {}
    events = set(str(reason or "").upper().split("+"))
    events.update(str(value).upper() for value in details.get("triggered_events", []) or [])
    stages = {stage for stage, event in (("ARM", "NORMAL_PROTECTION"), ("DYN", "DYNAMIC_PROTECTION"))
              if event in events}
    if not stages and events.intersection({"PRICE_PROTECTION", "PROTECT"}):
        stage = str(details.get("protection_stage", "")).upper()
        # A legacy request with no stage cannot be proved safe under a mixed
        # policy. Cancel it locally if either protection group is now ALERT.
        stages = {stage} if stage in {"ARM", "DYN"} else {"ARM", "DYN"}
    return stages


def dynamic_floor_previous(position, retention_pct):
    """An operator edit to retention rebases DYNAMIC; unchanged rules keep their floor."""
    previous = position.get("dynamic_retention_pct", position.get("normal_retention_pct"))
    if previous is not None and abs(float(previous) - float(retention_pct)) > 1e-9:
        return 0.0
    return float(position.get("dynamic_trigger_price", 0.0) or 0.0)


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
