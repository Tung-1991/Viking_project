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


def position_override_items(settings, position) -> list[str]:
    """Explain persisted local settings without changing any execution policy."""
    if position is None:
        return []
    params = position_exit_params(getattr(settings, "rule_parameters", {}))
    defaults = set(getattr(settings, "bot_em_modes", ["NORMAL", "IND_EXIT"]))
    modes = set(position.em_modes)
    overrides = normalize_exit_overrides(getattr(position, "exit_rule_overrides", {}))
    items = []
    if getattr(position, "auto_sell_enabled", True) is False:
        items.append("Tự bán: TẠM DỪNG")
    if position.sl_enabled != bool(getattr(settings, "bot_sl_enabled", True)):
        items.append(f"SL: {'BẬT' if position.sl_enabled else 'OFF'} · RULE {'BẬT' if getattr(settings, 'bot_sl_enabled', True) else 'OFF'}")
    sl_pct = params["reentry_sl_pct" if getattr(position, "is_reentry", False) else "initial_sl_pct"]
    if position.sl_mode in {"PRICE", "PERCENT"} and position.sl_value:
        value = f"{position.sl_value * 1000:,.0f}đ" if position.sl_mode == "PRICE" else f"-{abs(position.sl_value):g}%"
        items.append(f"SL: {value} · RULE {sl_pct:g}%")
    explicit_tp = position.tp_mode in {"PRICE", "PERCENT"} and position.tp_value > 0
    if explicit_tp:
        value = f"{position.tp_value * 1000:,.0f}đ" if position.tp_mode == "PRICE" else f"+{position.tp_value:g}%"
        items.append(f"TP: {value} · RULE +{params['take_profit_pct']:g}%")
    if (explicit_tp or "TP" in modes) != ("TP" in defaults):
        items.append(f"TP: {'BẬT' if explicit_tp or 'TP' in modes else 'OFF'} · RULE {'BẬT' if 'TP' in defaults else 'OFF'}")
    for stage, label, key, switch_key, tactic in (
            ("ARM", "SAU ARM", "normal_policy", "normal_arm_enabled", "NORMAL"),
            ("DYN", "DYNAMIC", "dynamic_policy", "normal_dynamic_enabled", "NORMAL"),
            ("E", "E", "indicator_exit_policy", None, "IND_EXIT")):
        inherited_active = tactic in defaults and (stage != "DYN" or params["normal_dynamic_enabled"])
        local_active = tactic in modes and (switch_key is None or overrides.get(switch_key, True))
        if stage == "DYN" and switch_key not in overrides:
            local_active = tactic in modes and params["normal_dynamic_enabled"]
        inherited = params[key] if inherited_active else "OFF"
        local = overrides.get(key, params[key]) if local_active else "OFF"
        if key in overrides or switch_key in overrides or local != inherited:
            items.append(f"{label}: {local} · RULE {inherited}")
    return items


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
