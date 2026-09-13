from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable
from typing import Any

from ..connections.dnse.client import DNSEClient
from ..connections.dnse.paper import PaperBroker
from .market import market_phase
from ..models import BrokerOrderResult, OrderIntent, TradeCycle
from .orders import OrderQueue
from .portfolio import (
    available_to_sell,
    board_price,
    position_quantity,
    round_lot_down,
    sell_quantity_for_fraction,
    validate_quantity,
)
from ..storage import CSVOrderJournal, JSONLineJournal
from .state import TradeStateStore
from ..rules.state import RuleStateStore


class ExecutionService:
    def __init__(
        self,
        real: DNSEClient,
        paper: PaperBroker,
        queue: OrderQueue,
        journal: JSONLineJournal,
        quote_provider: Callable[[str], dict[str, Any] | None] | None = None,
        trade_state: TradeStateStore | None = None,
        rule_state: RuleStateStore | None = None,
        sell_decision_provider: Callable[[str, str], Any | None] | None = None,
        trade_event_callback: Callable[[str, TradeCycle, OrderIntent], None] | None = None,
    ):
        self.real = real
        self.paper = paper
        self.queue = queue
        self.journal = journal
        self.csv_journal = CSVOrderJournal(journal.path.with_name("order_history.csv"))
        self.quote_provider = quote_provider
        self.trade_state = trade_state
        self.rule_state = rule_state
        self.sell_decision_provider = sell_decision_provider
        self.trade_event_callback = trade_event_callback

    def submit(
        self,
        intent: OrderIntent,
        phase: str | None = None,
        *,
        process_immediately: bool = True,
    ) -> OrderIntent:
        valid, reason, _quantity = validate_quantity(intent.quantity)
        if not valid:
            intent.status = "REJECTED"
            intent.result = reason
        phase = phase or market_phase()[0]
        if not self.queue._phase_is_due(intent, phase):
            intent.defer_expiry_until_eligible = True
        self.queue.add(intent)
        if not valid:
            return intent
        # UI callers enqueue only.  The serialized execution worker sends the
        # order so a slow broker request can never block Tk's event loop.
        if process_immediately:
            self.process_due(phase=phase, execution_mode=intent.execution_mode)
        return self.queue.get(intent.id) or intent

    def cancel_unsubmitted_protect_sells(self, execution_mode: str) -> tuple[list[OrderIntent], list[OrderIntent]]:
        """Cancel local PROTECT requests when global mode changes to ALERT.

        Orders DNSE may already have accepted are returned separately and are
        never cancelled or replaced without an explicit broker operation.
        """
        mode = "REAL" if str(execution_mode or "").upper() == "REAL" else "PAPER"
        cancelled: list[OrderIntent] = []
        broker_managed: list[OrderIntent] = []
        for intent in self.queue.list_all():
            if (
                intent.execution_mode != mode
                or intent.side != "SELL"
                or "NORMAL_PROTECTION" not in {
                    value for value in str(intent.reason or "").upper().split("+") if value
                }
                or intent.status.upper() in {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}
            ):
                continue
            if (
                intent.filled_quantity > 0
                or intent.status.upper() not in {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"}
            ):
                broker_managed.append(intent)
                continue
            updated = self.queue.cancel_local(intent.id)
            if not updated:
                continue
            cancelled.append(updated)
            event = {
                "ts": time.time(),
                "intent": updated.to_dict(),
                "queue_status": "CANCELLED",
                "result": {
                    "ok": True,
                    "status": "CANCELLED",
                    "order_id": "",
                    "message": "PROTECT chuyển sang ALERT",
                    "error": "",
                    "status_code": 0,
                    "raw": {},
                },
            }
            self.journal.append(event)
            self.csv_journal.append_event(event)
        return cancelled, broker_managed

    def process_due(
        self, *, phase: str, execution_mode: str,
        phase_provider: Callable[[str], str] | None = None,
    ) -> list[tuple[OrderIntent, BrokerOrderResult]]:
        mode = str(execution_mode).upper()
        token_ready = True if mode == "PAPER" else self.real.has_trading_token()
        broker: Any = self.paper if mode == "PAPER" else self.real
        self._revalidate_waiting_sells(phase, mode, broker, phase_provider)
        due = self.queue.claim_due(
            phase=phase,
            execution_mode=mode,
            token_ready=token_ready,
            quote_provider=self.quote_provider,
            phase_provider=phase_provider,
        )
        completed: list[tuple[OrderIntent, BrokerOrderResult]] = []
        for intent in due:
            intent_phase = phase_provider(intent.symbol) if phase_provider else phase
            send_quantity = intent.remaining_quantity or intent.quantity
            if intent.side == "SELL":
                try:
                    positions = broker.get_positions() if mode == "PAPER" else broker.get_positions(force=True)
                except TypeError:
                    positions = broker.get_positions()
                sellable = available_to_sell(positions, intent.symbol)
                send_quantity = round_lot_down(min(send_quantity, sellable))
                if send_quantity <= 0:
                    self.queue.wait_for_settlement(intent.id, "Chờ cổ phiếu về")
                    continue
            send_intent = replace(
                intent,
                quantity=send_quantity,
                filled_quantity=0,
                remaining_quantity=send_quantity,
                order_type=intent_phase if intent.order_type == "MARKET" and intent_phase in {"ATO", "ATC"} else intent.order_type,
            )
            try:
                # Earlier broker calls in this batch may have crossed the
                # deadline. Check again immediately before sending each BUY.
                if not self.queue.buy_window_is_due(send_intent):
                    result = BrokerOrderResult(
                        False, "EXPIRED", message="Hết khung giờ mua", error="BUY_WINDOW_EXPIRED",
                    )
                else:
                    result = broker.place_order(send_intent)
            except Exception as exc:
                result = BrokerOrderResult(False, "FAILED", message=str(exc), error="EXECUTION_EXCEPTION")
            persisted = self.queue.finish(
                intent,
                result,
                submitted_quantity=send_quantity,
                keep_sell_remainder=intent.side == "SELL",
            )
            self._record_trade_fill(
                intent,
                result,
                send_quantity,
                mark_events=bool(
                    persisted
                    and persisted.filled_quantity > intent.filled_quantity
                ),
            )
            event = {
                "ts": time.time(),
                # Journal the state *after* the broker result has been applied.
                # The submitted copy always has filled=0, which made a FILLED
                # PAPER order look like it was still entirely outstanding in
                # History even though the position had already been created.
                "intent": (persisted or send_intent).to_dict(),
                "queue_status": persisted.status if persisted else "",
                "result": {
                    "ok": result.ok,
                    "status": result.status,
                    "order_id": result.order_id,
                    "message": result.message,
                    "error": result.error,
                    "status_code": result.status_code,
                    "raw": result.raw if isinstance(result.raw, dict) else {},
                },
            }
            self.journal.append(event)
            self.csv_journal.append_event(event)
            completed.append((intent, result))
        return completed

    @staticmethod
    def _decision_matches_sell(intent: OrderIntent, raw: Any) -> bool | None:
        if raw is None:
            return None
        if hasattr(raw, "to_dict"):
            raw = raw.to_dict()
        if not isinstance(raw, dict):
            return None
        if str(raw.get("action", "WAIT") or "WAIT").upper() != "SELL":
            return False
        current = {str(raw.get("event", "") or "").upper()}
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        current.update(str(value or "").upper() for value in details.get("triggered_events", []) or [])
        requested = {value for value in str(intent.reason or "").upper().split("+") if value}
        return bool(requested & current) if requested else True

    def _revalidate_waiting_sells(
        self, phase: str, mode: str, broker: Any,
        phase_provider: Callable[[str], str] | None = None,
    ) -> None:
        candidates = [
            item for item in self.queue.list_all()
            if item.execution_mode == mode
            and item.side == "SELL"
            and item.source == "EM"
            and item.status in {"PENDING", "WAITING_SETTLEMENT"}
            and item.settlement_waited
            and item.sell_wait_policy == "RECHECK"
            and self.queue._phase_is_due(
                item, phase_provider(item.symbol) if phase_provider else phase,
            )
        ]
        if not candidates or not self.sell_decision_provider:
            return
        try:
            positions = broker.get_positions() if mode == "PAPER" else broker.get_positions(force=True)
        except TypeError:
            positions = broker.get_positions()
        except Exception:
            return
        for intent in candidates:
            if round_lot_down(min(intent.remaining_quantity, available_to_sell(positions, intent.symbol))) <= 0:
                continue
            raw_decision = self.sell_decision_provider(intent.symbol, intent.execution_mode)
            matches = self._decision_matches_sell(
                intent,
                raw_decision,
            )
            if matches is True:
                if intent.filled_quantity <= 0:
                    if hasattr(raw_decision, "to_dict"):
                        raw_decision = raw_decision.to_dict()
                    decision_details = (
                        raw_decision.get("details")
                        if isinstance(raw_decision, dict)
                        and isinstance(raw_decision.get("details"), dict)
                        else {}
                    )
                    fraction = float(
                        raw_decision.get("quantity_fraction", 1.0)
                        if isinstance(raw_decision, dict) else 1.0
                    )
                    holding = sum(
                        position_quantity(
                            row if isinstance(row, dict) else getattr(row, "raw", {}) or {}
                        )
                        for row in positions
                        if str(
                            (row if isinstance(row, dict) else getattr(row, "raw", {}) or {}).get(
                                "symbol", getattr(row, "symbol", ""),
                            ) or ""
                        ).upper() == intent.symbol
                    )
                    desired = sell_quantity_for_fraction(holding, fraction)
                    refreshed_details = {
                        key: decision_details.get(key)
                        for key in (
                            "normal_policy", "normal_dynamic_enabled", "normal_repeat_enabled",
                            "normal_arm_pct", "normal_giveback_pct", "sell_share_pct",
                            "normal_mfe_pct", "normal_peak_price", "normal_effective_trail_pct",
                            "normal_trigger_price", "normal_protected_profit_pct",
                            "normal_trigger_peak_pct", "normal_rearm_after_pct",
                        )
                        if key in decision_details
                    }
                    is_protect = "NORMAL_PROTECTION" in {
                        value
                        for value in str(intent.reason or "").upper().split("+")
                        if value
                    }
                    if desired > 0 and (
                        desired != intent.quantity
                        or (is_protect and refreshed_details != intent.details)
                    ):
                        self.queue.replace_local(
                            intent.id,
                            quantity=desired,
                            details=refreshed_details if is_protect else None,
                        )
                continue
            if matches is None:
                continue
            cancelled = self.queue.cancel_waiting_sell(
                intent.id,
                "Điều kiện SELL không còn đúng khi cổ phiếu về",
            )
            if not cancelled:
                continue
            if intent.filled_quantity > 0:
                self._mark_exit_events(intent)
            elif self.rule_state and intent.signal in {"BUY", "SELL"} and intent.candle_key:
                self.rule_state.release_signal(
                    intent.symbol,
                    intent.signal,
                    intent.candle_key,
                    stream=intent.execution_mode,
                )
            event = {
                "ts": time.time(),
                "intent": cancelled.to_dict(),
                "queue_status": "CANCELLED",
                "result": {
                    "ok": True,
                    "status": "CANCELLED",
                    "order_id": "",
                    "message": cancelled.result,
                    "error": "",
                    "status_code": 0,
                    "raw": {},
                },
            }
            self.journal.append(event)
            self.csv_journal.append_event(event)

    def _record_trade_fill(
        self,
        intent: OrderIntent,
        result: BrokerOrderResult,
        submitted: int,
        *,
        mark_events: bool = False,
    ) -> None:
        if not self.trade_state or not intent.trade_id or not result.ok:
            return
        filled, _leaves = self.queue._fill_quantities(result, submitted)
        if filled <= 0:
            return
        raw = result.raw if isinstance(result.raw, dict) else {}
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        price = board_price(body.get("averagePrice", body.get("price", 0.0)))
        fee = float(body.get("fee", body.get("totalFee", 0.0)) or 0.0) + float(body.get("tax", 0.0) or 0.0)
        if intent.side == "BUY":
            previous = self.trade_state.get(intent.trade_id)
            if not previous:
                self.trade_state.create(
                    intent.symbol,
                    intent.execution_mode,
                    source=intent.source,
                    trade_id=intent.trade_id,
                    em_modes=intent.em_modes,
                    sl_mode=intent.sl_mode,
                    sl_value=intent.sl_value,
                    tp_mode=intent.tp_mode,
                    tp_value=intent.tp_value,
                    entry_market_state=intent.entry_market_state,
                    entry_exposure=intent.entry_exposure,
                    entry_budget=intent.entry_budget,
                )
            cycle = self.trade_state.record_buy_fill(intent.trade_id, filled, price, fee)
            if cycle and (not previous or previous.entry_quantity == 0):
                self._emit_trade_event("OPEN", cycle, intent)
        else:
            previous = self.trade_state.get(intent.trade_id)
            cycle = self.trade_state.record_sell_fill(intent.trade_id, filled, price, fee)
            if mark_events:
                self._mark_exit_events(intent)
                cycle = self.trade_state.get(intent.trade_id) or cycle
            if cycle and previous and previous.open_quantity > 0 and cycle.status == "CLOSED":
                self._emit_trade_event("CLOSED", cycle, intent)

    def _emit_trade_event(self, event: str, cycle: TradeCycle, intent: OrderIntent) -> None:
        if not self.trade_event_callback:
            return
        try:
            self.trade_event_callback(str(event or "").upper(), cycle, intent)
        except Exception:
            # Notification failures must never interrupt broker reconciliation.
            return

    def _mark_exit_events(self, intent: OrderIntent) -> None:
        events = [value for value in str(intent.reason or "").upper().split("+") if value]
        if self.trade_state:
            for event in events:
                self.trade_state.mark_exit_once(intent.trade_id, event)
        if self.rule_state and events:
            details = intent.details if isinstance(intent.details, dict) else {}
            self.rule_state.mark_protection_done(
                intent.symbol,
                intent.trade_id,
                events,
                trigger_peak_pct=float(details.get("normal_trigger_peak_pct", 0.0) or 0.0),
                rearm_mfe_pct=float(details.get("normal_rearm_after_pct", 0.0) or 0.0),
                execution_id=intent.id,
            )

    def account_snapshot(self, mode: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        if str(mode).upper() == "PAPER":
            return self.paper.get_balance(), self.paper.get_positions(), self.paper.get_orders()
        return self.real.get_balance() or {}, self.real.get_positions(), self.real.get_orders()

    def reconcile_working(self, execution_mode: str) -> list[OrderIntent]:
        mode = str(execution_mode or "PAPER").upper()
        if mode == "PAPER":
            return []
        active = [
            intent
            for intent in self.queue.list_all()
            if intent.execution_mode == mode
            and intent.status in {"WORKING", "PARTIAL", "UNKNOWN"}
        ]
        # Do not poll DNSE /orders when Viking has nothing to reconcile.
        if not active:
            return []
        try:
            # Reuse the DNSE client's short order cache. Reconciliation runs
            # frequently, but the REST endpoint must not be hit every second.
            broker_orders = list(self.real.get_orders(force=False) or [])
        except Exception:
            return []
        updated: list[OrderIntent] = []
        for intent in active:
            match = next(
                (
                    row
                    for row in broker_orders
                    if (intent.broker_order_id and str(row.get("orderId", row.get("id", ""))) == intent.broker_order_id)
                    or (intent.request_tag and intent.request_tag in str(row.get("remark", "") or ""))
                ),
                None,
            )
            if not isinstance(match, dict):
                continue
            broker_fee, broker_tax = self.queue._broker_costs(match)
            fee_delta = max(0.0, broker_fee - intent.broker_fee_logged)
            tax_delta = max(0.0, broker_tax - intent.broker_tax_logged)
            reconciled, delta = self.queue.reconcile_broker(intent, match)
            if delta > 0:
                delta_raw = {
                    **match,
                    "fillQuantity": delta,
                    "fee": fee_delta,
                    "tax": tax_delta,
                }
                result = BrokerOrderResult(
                    True,
                    str(match.get("orderStatus", "")),
                    order_id=str(match.get("orderId", match.get("id", "")) or ""),
                    raw=delta_raw,
                )
                self._record_trade_fill(
                    intent,
                    result,
                    delta,
                    mark_events=True,
                )
                event_intent = intent.to_dict()
                event_intent.update(
                    filled_quantity=delta,
                    remaining_quantity=reconciled.remaining_quantity if reconciled else 0,
                )
                event = {
                    "ts": time.time(),
                    "intent": event_intent,
                    "queue_status": reconciled.status if reconciled else "",
                    "result": {
                        "ok": True,
                        "status": result.status,
                        "order_id": result.order_id,
                        "message": "BROKER_FILL_RECONCILED",
                        "error": "",
                        "status_code": 200,
                        "raw": delta_raw,
                    },
                }
                self.journal.append(event)
                self.csv_journal.append_event(event)
            if reconciled:
                updated.append(reconciled)
        return updated
