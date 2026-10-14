"""Small, persisted exit overrides belonging to one position only."""
from __future__ import annotations

from ..rules.protection import policy_for_stage, protection_stages

POLICY_KEYS = {"normal_policy", "dynamic_policy", "indicator_exit_policy"}
SWITCH_KEYS = {"normal_arm_enabled", "normal_dynamic_enabled"}


def normalize_exit_overrides(value) -> dict:
    result = {}
    for key, item in (value.items() if isinstance(value, dict) else ()):
        if key in POLICY_KEYS and str(item).upper() in {"AUTO", "ALERT"}:
            result[key] = str(item).upper()
        elif key in SWITCH_KEYS and isinstance(item, bool):
            result[key] = item
    return result


def position_exit_params(params, cycle=None, *, overrides=None) -> dict:
    from ..config import merge_rule_parameters
    base = merge_rule_parameters(params or {})
    base.update(normalize_exit_overrides(overrides if overrides is not None else
                                      getattr(cycle, "exit_rule_overrides", {})))
    return base


def exit_stage_policy(params, cycle, stage: str) -> str:
    effective = position_exit_params(params, cycle)
    if cycle and "NORMAL" not in cycle.em_modes:
        return "OFF"
    if stage == "ARM" and not effective.get("normal_arm_enabled", True):
        return "OFF"
    if stage == "DYN" and not effective.get("normal_dynamic_enabled", False):
        return "OFF"
    return policy_for_stage(effective, stage)


def automatic_exit_allowed(cycle, intent) -> bool:
    if intent.source not in {"BOT", "EM"} or intent.side != "SELL" or not cycle:
        return True
    if not cycle.auto_sell_enabled:
        return False
    reasons = set(str(intent.reason or "").upper().split("+"))
    if "STOP_LOSS" in reasons and not cycle.sl_enabled:
        return False
    if "TAKE_PROFIT" in reasons and cycle.tp_mode == "NONE" and "TP" not in cycle.em_modes:
        return False
    if reasons.intersection({"SELL_SIGNAL", "INDICATOR_EXIT"}) and (
            "IND_EXIT" not in cycle.em_modes or cycle.exit_rule_overrides.get("indicator_exit_policy") == "ALERT"):
        return False
    for stage in protection_stages(intent.reason, intent.details):
        if "NORMAL" not in cycle.em_modes:
            return False
        overrides = cycle.exit_rule_overrides
        if overrides.get("normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled") is False:
            return False
        if overrides.get("normal_policy" if stage == "ARM" else "dynamic_policy") == "ALERT":
            return False
    return True


def save_position_controls(trades, queue, trade_id, **changes):
    with trades.store.database:
        cycle = trades.update_management(trade_id, **changes)
        if not cycle:
            raise ValueError("Vị thế đã thay đổi hoặc đã đóng.")
        broker_pending = []
        for item in queue.list_all():
            if item.trade_id != trade_id or item.status in {"FILLED", "CANCELLED", "EXPIRED", "FAILED", "REJECTED"}:
                continue
            if automatic_exit_allowed(cycle, item):
                continue
            if item.broker_order_id or item.handed_off_at:
                broker_pending.append(item.id)
            elif item.status == "SENDING":
                if item.filled_quantity:
                    queue.release(item.id, "CANCELLED", "POSITION_EXIT_MODE_CHANGED")
                else:
                    queue.cancel_claimed_local(item.id, "POSITION_EXIT_MODE_CHANGED")
            else:
                queue.cancel_local(item.id)
        return cycle, broker_pending
