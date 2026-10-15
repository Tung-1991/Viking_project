"""Capture an exit condition while stock is unsettled, without creating an order."""
from __future__ import annotations

from ..models import StrategyDecision


def settlement_exit_snapshot(decision: StrategyDecision, position: dict, *, mode: str) -> dict | None:
    if not position or (int(position.get("trade_quantity", position.get("quantity", 0)) or 0) >= 100
                        and position.get("auto_sell_enabled") is not False
                        and position.get("bot_trade_enabled") is not False):
        return None
    details = decision.details
    if (decision.reason == "PROTECT_WAITING_SETTLEMENT"
            or (decision.event == "PROTECT_ALERT" and details.get("auto_sell_paused"))):
        kind = "PROTECT " + str(details.get("protection_stage", "ARM"))
        policy = details.get("normal_policy", "ALERT")
    elif decision.event in {"INDICATOR_EXIT", "INDICATOR_EXIT_ALERT"}:
        kind, policy = "E", details.get("indicator_exit_policy", "ALERT")
    elif decision.event in {"STOP_LOSS", "TAKE_PROFIT", "EXIT_ALERT"}:
        kind, policy = details.get("held_exit_reason", decision.reason), "AUTO"
    else:
        return None
    paused = position.get("auto_sell_enabled") is False or position.get("bot_trade_enabled") is False
    if paused:
        policy = "ALERT"
    return {"symbol": decision.symbol, "execution_mode": mode, "kind": kind,
            "policy": policy, "price": float(details.get("current_price", 0) or 0),
            "sellable_quantity": int(position.get("trade_quantity", 0) or 0),
            "held_quantity": int(position.get("quantity", 0) or 0),
            "auto_sell_paused": paused,
            "peak_price": float(details.get("normal_peak_price", 0) or 0),
            "protect_price": float(details.get("normal_trigger_price", 0) or 0),
            "event_occurrence": str(details.get("protect_occurrence", "") or "")}


def exit_condition_cleared(decision: StrategyDecision, position: dict) -> bool:
    """No event on this poll is not proof that a held exit condition ended."""
    kind = str(position.get("exit_wait_active_kind", ""))
    if not kind:
        return True
    if position.get("analysis_price_available") is False:
        return False
    details = decision.details
    current = float(position.get("current_price", 0) or 0)
    if current <= 0:
        return False
    if kind.startswith("PROTECT "):
        prefix = "dynamic" if kind == "PROTECT DYN" else "normal"
        level = float(details.get("normal_trigger_price", position.get(prefix + "_trigger_price", 0)) or 0)
        return level > 0 and current > level
    if kind == "STOP_LOSS":
        level = float(details.get("sl_price", 0) or 0)
        return level > 0 and current > level
    if kind == "TAKE_PROFIT":
        mode, value = details.get("tp_mode"), float(details.get("tp_value", 0) or 0)
        level = value if mode == "PRICE" else float(position.get("avg_price", 0) or 0) * (1 + value / 100)
        return value > 0 and level > 0 and current < level
    if kind == "E":
        marks = details.get("indicators") or {}
        ready = marks.get("signal_ready") is True or all(marks.get(key) is not None for key in (
            "sell_ema_fast", "sell_ema_slow", "rsi", "rsi_previous"))
        return ready and marks.get("signal_ready") is not False and decision.signal != "SELL"
    return False
