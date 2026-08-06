# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Tuple

from core.money import format_vnd_full

DEFAULT_MANUAL_MARGIN = {
    "ENABLE_MANUAL_MARGIN": False,
    "MARGIN_RISK_BASE": "EQUITY_NAV",
    "MAX_MARGIN_ORDER_VALUE_PCT": 50.0,
    "MIN_RTT_TO_OPEN": 100.0,
    "CALL_RTT": 87.0,
    "FORCE_RTT": 80.0,
    "MAX_MANUAL_MARGIN_LOSS_PCT": 3.0,
    "BOT_ALLOW_MARGIN": False,
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def settings_from_brain(brain: Dict[str, Any] | None) -> Dict[str, Any]:
    settings = dict(DEFAULT_MANUAL_MARGIN)
    raw = (brain or {}).get("manual_margin", {})
    if isinstance(raw, dict):
        settings.update(raw)
    settings["ENABLE_MANUAL_MARGIN"] = bool(settings.get("ENABLE_MANUAL_MARGIN", False))
    settings["MARGIN_RISK_BASE"] = str(settings.get("MARGIN_RISK_BASE", "EQUITY_NAV") or "EQUITY_NAV").upper()
    settings["BOT_ALLOW_MARGIN"] = bool(settings.get("BOT_ALLOW_MARGIN", False))
    for key in (
        "MAX_MARGIN_ORDER_VALUE_PCT",
        "MIN_RTT_TO_OPEN",
        "CALL_RTT",
        "FORCE_RTT",
        "MAX_MANUAL_MARGIN_LOSS_PCT",
    ):
        settings[key] = _float(settings.get(key), DEFAULT_MANUAL_MARGIN[key])
    return settings


def account_snapshot(account_info: Dict[str, Any] | None, settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    account_info = account_info or {}
    settings = settings or DEFAULT_MANUAL_MARGIN
    equity = _float(account_info.get("equity", account_info.get("balance", 0.0)), 0.0)
    balance = _float(account_info.get("balance", equity), equity)
    cash_available = _float(account_info.get("cash_available", account_info.get("free_margin", 0.0)), 0.0)
    free_margin = _float(account_info.get("free_margin", account_info.get("margin_free", cash_available)), cash_available)
    buying_power = _float(account_info.get("buying_power", account_info.get("availableBalance", free_margin)), free_margin)
    margin_debt = _float(account_info.get("margin_debt", account_info.get("margin", 0.0)), 0.0)
    rtt_raw = account_info.get("rtt", None)
    rtt = None if rtt_raw in (None, "", "UNKNOWN") else _float(rtt_raw, 0.0)
    return {
        "balance": balance,
        "equity": equity,
        "cash_available": cash_available,
        "free_margin": free_margin,
        "buying_power": buying_power,
        "margin_debt": margin_debt,
        "rtt": rtt,
        "margin_call_level": _float(account_info.get("margin_call_level"), settings.get("CALL_RTT", 87.0)),
        "margin_force_level": _float(account_info.get("margin_force_level"), settings.get("FORCE_RTT", 80.0)),
    }


def resolve_risk_base(account_info: Dict[str, Any] | None, settings: Dict[str, Any] | None = None) -> Tuple[float, str, str]:
    settings = settings or DEFAULT_MANUAL_MARGIN
    snap = account_snapshot(account_info, settings)
    mode = str(settings.get("MARGIN_RISK_BASE", "EQUITY_NAV") or "EQUITY_NAV").upper()
    if mode == "FREE_CASH":
        base = snap["cash_available"] if snap["cash_available"] > 0 else snap["free_margin"]
        warning = "" if snap["cash_available"] > 0 else "cash_available missing; fallback free_margin"
        return max(0.0, base), "FREE_CASH", warning
    return max(0.0, snap["equity"]), "EQUITY_NAV", ""


def manual_margin_check(
    account_info: Dict[str, Any] | None,
    settings: Dict[str, Any] | None,
    order_value: float,
    risk_usd: float,
    strict: bool = True,
) -> Dict[str, Any]:
    settings = settings_from_brain({"manual_margin": settings or {}})
    snap = account_snapshot(account_info, settings)
    checks = []
    passed = True

    rtt = snap["rtt"]
    min_rtt = settings["MIN_RTT_TO_OPEN"]
    if rtt is None:
        checks.append({"name": "Margin RTT", "status": "FAIL" if strict else "WARN", "msg": "UNKNOWN"})
        if strict:
            passed = False
    elif rtt < min_rtt:
        checks.append({"name": "Margin RTT", "status": "FAIL", "msg": f"{rtt:.1f}% < min {min_rtt:.1f}%"})
        passed = False
    elif rtt <= settings["CALL_RTT"]:
        checks.append({"name": "Margin RTT", "status": "WARN", "msg": f"{rtt:.1f}% gan call {settings['CALL_RTT']:.1f}%"})
    else:
        checks.append({"name": "Margin RTT", "status": "OK", "msg": f"{rtt:.1f}%"})

    equity = snap["equity"]
    max_order_value = equity * (settings["MAX_MARGIN_ORDER_VALUE_PCT"] / 100.0) if equity > 0 else 0.0
    if max_order_value > 0 and float(order_value or 0.0) > max_order_value:
        checks.append({"name": "Margin Size", "status": "FAIL", "msg": f"value {format_vnd_full(order_value)} > cap {format_vnd_full(max_order_value)}"})
        passed = False
    else:
        checks.append({"name": "Margin Size", "status": "OK", "msg": f"value {format_vnd_full(order_value)}"})

    max_loss = equity * (settings["MAX_MANUAL_MARGIN_LOSS_PCT"] / 100.0) if equity > 0 else 0.0
    if max_loss > 0 and float(risk_usd or 0.0) > max_loss:
        checks.append({"name": "Margin Loss", "status": "FAIL", "msg": f"risk {format_vnd_full(risk_usd)} > cap {format_vnd_full(max_loss)}"})
        passed = False
    else:
        checks.append({"name": "Margin Loss", "status": "OK", "msg": f"risk {format_vnd_full(risk_usd)}"})

    return {"passed": passed, "checks": checks, "snapshot": snap, "settings": settings}


def bot_margin_block_reason(account_info: Dict[str, Any] | None, settings: Dict[str, Any] | None = None) -> str:
    settings = settings_from_brain({"manual_margin": settings or {}})
    if settings.get("BOT_ALLOW_MARGIN", False):
        return ""
    snap = account_snapshot(account_info, settings)
    if snap["margin_debt"] > 0:
        return f"BOT_MARGIN_DISABLED|margin debt {format_vnd_full(snap['margin_debt'])}"
    if snap["rtt"] is not None and snap["rtt"] < settings["MIN_RTT_TO_OPEN"]:
        return f"BOT_MARGIN_RTT_LOW|RTT {snap['rtt']:.1f}% < {settings['MIN_RTT_TO_OPEN']:.1f}%"
    return ""
