"""Shared LIVE/PAPER/intraday entry filters; position exits bypass this layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from ..models import StrategyDecision
from ..trading.market import VN_TZ, market_phase, validate_buy_window, exchange_close_minute
from .business import StaticRule, advance_buy_confirmation, buy_confirmation_conditions
from ..trading.validation import MAX_DECISION_AGE


def apply_buy_filters(
    rule: StaticRule,
    decision: StrategyDecision,
    context: dict[str, Any],
    portfolio: dict[str, Any],
    state: dict[str, Any] | None,
    *,
    observed_at: datetime,
    exchange: str,
    working_dates: Iterable[str] | None = None,
    holidays: Iterable[str] | None = None,
) -> tuple[dict[str, Any], StrategyDecision]:
    params = rule.params
    position = portfolio.get("position") or {}
    if ((position.get("quantity", portfolio.get("position_quantity", 0)) and not portfolio.get("scale_in_allowed", False))
            or portfolio.get("pending_buy")
            or not (params.buy_window_enabled or params.buy_confirmation_enabled)):
        return {}, decision
    saved = dict(state or {})
    # Read candidates written by the previous confirmation-only version.
    confirmation = dict(saved.get("confirmation") or (saved if saved.get("active") else {}))
    window = dict(saved.get("window") or {})
    details = dict(decision.details)
    now = observed_at.astimezone(VN_TZ) if observed_at.tzinfo else observed_at.replace(tzinfo=VN_TZ)
    observation_gap = context.get("max_observation_gap_seconds")
    if window.get("observed_time") and observation_gap is not None:
        previous_observation = datetime.fromisoformat(str(window["observed_time"]))
        if (now - previous_observation).total_seconds() > float(observation_gap):
            window, confirmation = {}, {}
    if window:
        window["observed_time"] = now.isoformat()
    trigger = decision.signal == "BUY"
    window_info: dict[str, Any] = {}

    def waiting(reason: str, text: str, next_state: dict[str, Any]) -> tuple[dict, StrategyDecision]:
        return next_state, StrategyDecision(
            "WAIT", decision.symbol, reason, signal="BUY",
            market_state=decision.market_state,
            details={**details, "status_text": text},
        )

    if str(context.get("signal_mode", "CLOSED")).upper() != "REALTIME":
        return waiting("BUY_FILTER_NEEDS_REALTIME", "LỌC BUY THEO GIỜ CẦN REALTIME", {})

    if params.buy_window_enabled:
        start, _ = validate_buy_window(params.buy_window_start, "15:00")
        end = exchange_close_minute(exchange)
        end_text = f"{end // 60:02d}:{end % 60:02d}"
        window_info = {
            "start": params.buy_window_start, "end": end_text,
            "date": now.date().isoformat(), "observed_time": now.isoformat(),
        }
        minute = now.hour * 60 + now.minute
        if window and (window.get("date") != window_info["date"]
                       or window.get("start") != window_info["start"]
                       or window.get("end") != window_info["end"]):
            window, confirmation = {}, {}
        phase = market_phase(now, working_dates, holidays, exchange)[0]
        if minute >= end:
            if window or trigger or confirmation:
                details["buy_window"] = {**window_info, "signal_time": window.get("signal_time", ""), "state": "EXPIRED"}
                return waiting("BUY_WINDOW_EXPIRED", "HẾT KHUNG GIỜ MUA", {})
            return {}, decision
        if not window and trigger and phase in {"ATO", "OPEN", "ATC"}:
            window = {**window_info, "signal_time": now.isoformat(), "released": False}
        if not window:
            if trigger:
                details["buy_window"] = {**window_info, "state": "CLOSED"}
                return waiting("BUY_WINDOW_MARKET_CLOSED", "CHỜ PHIÊN GIAO DỊCH", {})
            return {}, decision
        window_info["signal_time"] = window["signal_time"]
        checks = buy_confirmation_conditions(details.get("indicators"), params)
        # Before release, keep exactly the base BUY conditions selected in
        # settings. After release the independent X-minute filter follows its
        # own EMA/RSI confirmation checkboxes.
        base_conditions_ok = (
            (not params.buy_signal_use_ema or checks["ema"])
            and (not params.buy_signal_use_rsi or checks["rsi"])
        )
        if not window.get("released") and not base_conditions_ok:
            details["buy_window"] = {**window_info, "state": "CANCELLED"}
            return waiting("BUY_WINDOW_BROKEN", "HỦY CHỜ GIỜ · ĐIỀU KIỆN BUY KHÔNG CÒN ĐẠT", {})
        if minute < start or phase not in {"ATO", "OPEN", "ATC"}:
            details["buy_window"] = {**window_info, "state": "WAITING"}
            return waiting("BUY_WINDOW_WAIT", f"CHỜ GIỜ MUA · TỪ {params.buy_window_start}", {"window": window})
        trigger = not window.get("released", False)
        window["released"] = True
        details["buy_window"] = {**window_info, "state": "ALLOWED"}

    if params.buy_confirmation_enabled:
        next_confirmation, status, audit = advance_buy_confirmation(
            confirmation, raw_trigger=trigger, indicators=details.get("indicators"),
            observed_at=now, exchange=exchange, params=params,
            working_dates=working_dates, holidays=holidays,
            max_observation_gap_seconds=observation_gap,
        )
        audit["state"] = status
        details["buy_confirmation"] = audit
        if status == "WAITING":
            return waiting(
                "BUY_CONFIRMATION_WAIT",
                f"CHỜ BUY · {int(audit['minutes_held'])}/{audit['minutes_required']}P",
                {"window": window, "confirmation": next_confirmation},
            )
        if status in {"CANCELLED", "INVALID"}:
            return waiting(str(audit.get("reason") or "BUY_CONFIRMATION_CANCELLED"), "HỦY XÁC NHẬN BUY", {})
        if status != "CONFIRMED":
            return {}, decision
    # Re-evaluate all entry guards at the actual release/confirmation time.
    approved = rule.evaluate({**context, "confirmed_buy": True}, portfolio)
    for key in ("buy_window", "buy_confirmation"):
        if key in details:
            approved.details[key] = details[key]
    return {}, approved
