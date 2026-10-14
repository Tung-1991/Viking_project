"""Capture an exit condition while stock is unsettled, without creating an order."""
from __future__ import annotations

from ..models import StrategyDecision


def settlement_exit_snapshot(decision: StrategyDecision, position: dict, *, mode: str) -> dict | None:
    if not position or (int(position.get("trade_quantity", position.get("quantity", 0)) or 0) >= 100
                        and position.get("auto_sell_enabled") is not False):
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
    if position.get("auto_sell_enabled") is False:
        policy = "ALERT"
    return {"symbol": decision.symbol, "execution_mode": mode, "kind": kind,
            "policy": policy, "price": float(details.get("current_price", 0) or 0),
            "sellable_quantity": int(position.get("trade_quantity", 0) or 0),
            "held_quantity": int(position.get("quantity", 0) or 0),
            "auto_sell_paused": position.get("auto_sell_enabled") is False,
            "peak_price": float(details.get("normal_peak_price", 0) or 0),
            "protect_price": float(details.get("normal_trigger_price", 0) or 0)}
