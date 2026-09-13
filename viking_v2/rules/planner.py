from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid
from typing import Any

from ..models import OrderIntent, StrategyDecision
from ..trading.orders import OrderQueue
from ..trading.portfolio import sell_quantity_for_fraction, size_buy_order
from ..trading.state import TradeStateStore
from ..trading.market import VN_TZ, in_buy_window, parse_clock_minute
from .state import RuleStateStore


@dataclass(slots=True)
class PlanResult:
    intent: OrderIntent | None
    reason: str


class StrategyOrderPlanner:
    """Translate an approved rule decision into one persistent order intent."""

    def __init__(self, queue: OrderQueue, trades: TradeStateStore, rule_state: RuleStateStore):
        self.queue = queue
        self.trades = trades
        self.rule_state = rule_state

    def plan(
        self,
        decision: StrategyDecision,
        *,
        execution_mode: str,
        execution_style: str,
        tick: dict[str, Any],
        portfolio: dict[str, Any],
        candle_key: str,
        allow_ato: bool = False,
        allow_atc: bool = False,
        bot_em_modes: list[str] | None = None,
        sell_wait_policy: str = "RECHECK",
    ) -> PlanResult:
        if decision.action == "WAIT":
            return PlanResult(None, decision.reason)
        symbol = decision.symbol
        style = str(execution_style or "MARKET").upper()
        side = "BUY" if decision.action == "BUY" else "SELL"
        price_key = "ask" if side == "BUY" else "bid"
        price = float(tick.get(price_key, tick.get("price", 0.0)) or 0.0)
        if price <= 0 or bool(tick.get("stale", False)):
            return PlanResult(None, "NO_LIVE_EXECUTION_PRICE")

        if side == "BUY":
            window = decision.details.get("buy_window") or {}
            if window:
                now = datetime.now(VN_TZ)
                if window.get("date") != now.date().isoformat() or not in_buy_window(
                    now, window["start"], window["end"],
                ):
                    return PlanResult(None, "BUY_WINDOW_EXPIRED")
            if self.queue.find_active(symbol, side="BUY", execution_mode=execution_mode):
                return PlanResult(None, "BUY_ALREADY_PENDING")
            checks = (
                decision.details.get("entry_checks")
                if isinstance(decision.details, dict)
                and isinstance(decision.details.get("entry_checks"), dict)
                else {}
            )
            available_cash = float(
                portfolio.get("available_cash", checks.get("available_cash", 0.0)) or 0.0
            )
            nav = float(portfolio.get("nav", checks.get("nav", 0.0)) or 0.0)
            minimum_room = portfolio.get(
                "minimum_order_room", checks.get("minimum_order_room")
            )
            if minimum_room is None:
                minimum_room = nav
            sizing = size_buy_order(
                budget_vnd=float(portfolio.get("order_budget", 0.0) or 0.0),
                price_board=price,
                available_cash=available_cash,
                nav=nav,
                force_min_lot_enabled=bool(checks.get("force_min_lot_enabled", False)),
                minimum_order_room_vnd=float(minimum_room or 0.0),
                buy_fee_rate=float(
                    portfolio.get("buy_fee_rate", checks.get("buy_fee_rate", 0.0)) or 0.0
                ),
            )
            quantity = sizing.quantity
            if quantity <= 0:
                return PlanResult(None, sizing.reason)
            if not self.rule_state.claim_signal(
                symbol, "BUY", candle_key, stream=execution_mode,
            ):
                return PlanResult(None, "BUY_SIGNAL_ALREADY_PROCESSED")
            alerted_id = (
                decision.details.get("telegram_signal_id", "")
                if isinstance(decision.details, dict)
                else ""
            )
            trade_id = str(alerted_id or uuid.uuid4().hex)
        else:
            trade_id = str(portfolio.get("trade_id", "") or "")
            remaining = max(0, int(portfolio.get("position_quantity", 0) or 0))
            quantity = sell_quantity_for_fraction(
                remaining, float(decision.quantity_fraction or 1.0)
            )
            if quantity <= 0:
                return PlanResult(None, "ODD_LOT_REMAINDER")
            active_sell = [
                item
                for item in self.queue.find_active(symbol, side="SELL", execution_mode=execution_mode)
                if not trade_id or item.trade_id == trade_id
            ]
            if active_sell:
                priority = {
                    "NORMAL_PROTECTION": 1,
                    "PRICE_PROTECTION": 1,
                    "INDICATOR_EXIT": 2,
                    "TAKE_PROFIT": 3,
                    "STOP_LOSS": 4,
                }
                requested_reason = str(decision.event or decision.reason or "").upper()
                requested_priority = priority.get(requested_reason, 0)
                replaced = False
                for existing in active_sell:
                    existing_reasons = [
                        value for value in str(existing.reason or "").upper().split("+") if value
                    ]
                    existing_priority = max(
                        (priority.get(value, 0) for value in existing_reasons), default=0,
                    )
                    if (
                        requested_priority > existing_priority
                        and existing.status.upper() in {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"}
                    ):
                        replaced = bool(self.queue.cancel_local(existing.id)) or replaced
                        continue
                    return PlanResult(None, "SELL_ALREADY_PENDING")
                if not replaced:
                    return PlanResult(None, "SELL_ALREADY_PENDING")
            if (
                decision.event == "INDICATOR_EXIT"
                and not self.rule_state.claim_signal(
                    symbol, "SELL", candle_key, stream=execution_mode,
                )
            ):
                return PlanResult(None, "SELL_SIGNAL_ALREADY_PROCESSED")

        local_limit = style == "LO_LOCAL"
        reason = decision.event or decision.reason
        triggered = decision.details.get("triggered_events") if isinstance(decision.details, dict) else None
        if isinstance(triggered, list) and triggered:
            reason = "+".join(str(value) for value in triggered)
        intent = OrderIntent.create(
            symbol,
            side,
            quantity,
            "LO" if local_limit else "MARKET",
            limit_price=price if local_limit else 0.0,
            execution_mode="REAL" if str(execution_mode).upper() == "REAL" else "PAPER",
            source="EM" if decision.scope == "POSITION_MANAGEMENT" else "BOT",
            trade_id=trade_id,
            action="OPEN" if side == "BUY" else "CLOSE",
            reason=reason,
            wait_for_trigger=local_limit,
            allow_ato=bool(allow_ato and not local_limit),
            allow_atc=bool(allow_atc and not local_limit),
            em_modes=list(bot_em_modes or []) if side == "BUY" else [],
            sell_wait_policy=sell_wait_policy if side == "SELL" else "KEEP",
            signal=decision.signal,
            candle_key=candle_key,
            entry_market_state=decision.market_state if side == "BUY" else "UNKNOWN",
            entry_exposure=(
                float(decision.details.get("exposure", 0.0) or 0.0)
                if side == "BUY" and isinstance(decision.details, dict) else 0.0
            ),
            entry_budget=(
                float(portfolio.get("order_budget", 0.0) or 0.0)
                if side == "BUY" else 0.0
            ),
            details=(
                {
                    key: decision.details.get(key)
                    for key in (
                        "normal_policy", "normal_dynamic_enabled", "normal_repeat_enabled",
                        "normal_arm_pct", "normal_giveback_pct", "sell_share_pct",
                        "normal_mfe_pct", "normal_peak_price", "normal_effective_trail_pct",
                        "normal_trigger_price", "normal_protected_profit_pct",
                        "normal_trigger_peak_pct", "normal_rearm_after_pct",
                    )
                    if key in decision.details
                }
                if side == "SELL" and isinstance(decision.details, dict) else {}
            ),
        )
        if side == "BUY" and window:
            intent.buy_window_start = window["start"]
            intent.buy_window_end = window["end"]
            intent.buy_window_date = window["date"]
            end_minute = parse_clock_minute(window["end"])
            deadline = datetime.fromisoformat(window["date"]).replace(
                hour=end_minute // 60, minute=end_minute % 60, tzinfo=VN_TZ,
            ).timestamp()
            intent.expires_at = min(intent.expires_at, deadline)
        self.queue.add(intent)
        return PlanResult(intent, "PLANNED")
