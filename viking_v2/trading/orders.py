from __future__ import annotations

import math
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from .market import order_is_due, VN_TZ, in_buy_window
from ..models import BrokerOrderResult, OrderIntent
from .portfolio import validate_quantity, board_price, account_price
from .durable import DurableJSONStore


FINAL_STATUSES = {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}

# A finished order stays in the queue long enough for the dashboard to show it,
# then goes.  The record itself lives on in order_history.csv and the journal,
# so nothing is lost - only the working set stays small enough to rewrite on
# every single add.
SETTLED_KEEP_SECONDS = 24 * 60 * 60
CLAIMABLE_STATUSES = {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"}
LOCALLY_CONTROLLABLE_STATUSES = CLAIMABLE_STATUSES | {"PAUSED"}


def _transactional(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class OrderQueue:
    def __init__(self, path: str | Path, *, now=time.time):
        self.store = DurableJSONStore(path, default=[], validator=self._validate_rows)
        self._now = now
        self._lock = self.store.transaction

    @staticmethod
    def _validate_rows(rows):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid order queue structure")
        intents = [OrderIntent.from_dict(row) for row in rows]
        if len({item.id for item in intents}) != len(intents):
            raise ValueError("Duplicate order IDs")
        if any(item.filled_quantity > item.quantity for item in intents):
            raise ValueError("Order filled quantity exceeds total")
        return [item.to_dict() for item in intents]

    def _read(self) -> list[dict[str, Any]]:
        value = self.store.read()
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def list_all(self) -> list[OrderIntent]:
        return [OrderIntent.from_dict(row) for row in self._read()]

    def add(self, intent: OrderIntent) -> OrderIntent:
        with self._lock:
            rows = self._read()
            if any(str(row.get("id")) == intent.id for row in rows):
                return intent
            rows.append(intent.to_dict())
            self.store.write(rows)
        return intent

    def find_active(
        self,
        symbol: str,
        *,
        side: str | None = None,
        execution_mode: str | None = None,
    ) -> list[OrderIntent]:
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        execution_mode = str(execution_mode or "").upper()
        return [
            item
            for item in self.list_all()
            if item.symbol == symbol
            # UNKNOWN means DNSE may already have accepted the request. It must
            # keep blocking duplicates until reconciliation resolves it.
            and item.status.upper() not in FINAL_STATUSES
            and (not side or item.side == side)
            and (not execution_mode or item.execution_mode == execution_mode)
        ]

    def add_unique(self, intent: OrderIntent) -> OrderIntent:
        with self._lock:
            active = self.find_active(intent.symbol, side=intent.side if intent.side == "BUY" else None, execution_mode=intent.execution_mode)
            if intent.side == "BUY" and active:
                return active[0]
            return self.add(intent)

    def discard_unsubmitted_bot_buys(self, reason: str) -> None:
        with self._lock:
            for intent in self.list_all():
                if intent.source == "BOT" and intent.side == "BUY" and intent.status in LOCALLY_CONTROLLABLE_STATUSES and not intent.handed_off_at and not intent.broker_order_id:
                    self._update(intent.id, status="CANCELLED", result=reason)

    def recover_claims(self) -> None:
        with self._lock:
            for intent in self.list_all():
                if intent.status != "SENDING":
                    continue
                if intent.handed_off_at or intent.details.get("claim_protocol") != "durable-v1":
                    self._update(intent.id, status="UNKNOWN", result="Restart: đối soát hand-off, không tự gửi lại")
                elif intent.source == "BOT" and intent.side == "BUY":
                    self._update(intent.id, status="CANCELLED", result="Restart: bỏ BUY chưa gửi")
                else:
                    self.release(intent.id, "PENDING", "Restart: giữ yêu cầu chưa hand-off")

    def get(self, order_id: str) -> OrderIntent | None:
        for row in self._read():
            if str(row.get("id")) == str(order_id):
                return OrderIntent.from_dict(row)
        return None

    def _update(self, order_id: str, **changes: Any) -> OrderIntent | None:
        with self._lock:
            rows = self._read()
            result = None
            for row in rows:
                if str(row.get("id")) == str(order_id):
                    row.update(changes)
                    result = OrderIntent.from_dict(row)
                    row.update(result.to_dict())
                    break
            if result:
                self.store.write(rows)
            return result

    def _drop_settled(self, rows: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Forget orders that finished more than a day ago."""
        kept: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("status", "")).upper() not in FINAL_STATUSES:
                kept.append(row)
                continue
            done = float(row.get("claimed_at") or row.get("created_at") or 0.0)
            if not done or now - done < SETTLED_KEEP_SECONDS:
                kept.append(row)
        return kept

    def expire(self) -> list[OrderIntent]:
        now = self._now()
        expired: list[OrderIntent] = []
        with self._lock:
            rows = self._read()
            trimmed = self._drop_settled(rows, now)
            changed = len(trimmed) != len(rows)
            rows = trimmed
            for row in rows:
                status = str(row.get("status", "")).upper()
                if status in FINAL_STATUSES | {"UNKNOWN", "WORKING", "PARTIAL", "CANCEL_PENDING", "REPLACE_PENDING"}:
                    continue
                # A sell waiting for T+2 is not a stale trading instruction.
                # It cannot legally be sent yet, and weekends/holidays routinely
                # make that wait longer than the generic 24-hour queue TTL.
                if status == "WAITING_SETTLEMENT" or (
                    status == "WAITING_TOKEN" and bool(row.get("settlement_waited", False))
                ):
                    continue
                if bool(row.get("defer_expiry_until_eligible", False)) and not bool(row.get("eligible_session_seen", False)):
                    continue
                if status == "SENDING":
                    claimed = float(row.get("claimed_at", 0.0) or 0.0)
                    if claimed and now - claimed > 120.0:
                        row["status"] = "UNKNOWN"
                        row["result"] = "Recovered SENDING; reconcile without retry"
                        changed = True
                    continue
                if (float(row.get("expires_at", 0.0) or 0.0) <= now
                        and (not row.get("buy_window_end") or status in LOCALLY_CONTROLLABLE_STATUSES)):
                    row["status"] = "EXPIRED"
                    row["result"] = "Hết khung giờ mua" if row.get("buy_window_end") else "Hết thời hạn yêu cầu chưa gửi"
                    expired.append(OrderIntent.from_dict(row))
                    changed = True
            if changed:
                self.store.write(rows)
        return expired

    @staticmethod
    def _phase_is_due(intent: OrderIntent, phase: str) -> bool:
        phase = str(phase or "").upper()
        if intent.order_type == "MARKET":
            return phase == "OPEN" or (phase == "ATO" and intent.allow_ato) or (phase == "ATC" and intent.allow_atc)
        return order_is_due(intent.order_type, phase)

    @staticmethod
    def _trigger_is_ready(intent: OrderIntent, quote: dict[str, Any] | None) -> bool:
        if not intent.wait_for_trigger:
            return True
        if intent.order_type != "LO" or intent.limit_price <= 0 or not isinstance(quote, dict):
            return False
        if bool(quote.get("stale", False)) or str(quote.get("health", "OK")).upper() not in {"", "OK", "HEALTHY"}:
            return False
        key = "ask" if intent.side == "BUY" else "bid"
        price = float(quote.get(key, quote.get("price", 0.0)) or 0.0)
        if price <= 0:
            return False
        return price <= intent.limit_price if intent.side == "BUY" else price >= intent.limit_price

    def buy_window_is_due(self, intent: OrderIntent) -> bool:
        if intent.side != "BUY" or not intent.buy_window_start:
            return True
        local = datetime.fromtimestamp(self._now(), VN_TZ)
        return local.date().isoformat() == intent.buy_window_date and in_buy_window(
            local, intent.buy_window_start, intent.buy_window_end,
        )

    def claim_due(
        self,
        *,
        phase: str,
        execution_mode: str,
        token_ready: bool,
        allow_bot_buys: bool = True,
        limit: int = 20,
        quote_provider: Callable[[str], dict[str, Any] | None] | None = None,
        phase_provider: Callable[[str], str] | None = None,
    ) -> list[OrderIntent]:
        self.expire()
        claimed: list[OrderIntent] = []
        with self._lock:
            rows = self._read()
            changed = False
            for row in rows:
                if len(claimed) >= limit:
                    break
                if str(row.get("status", "")).upper() not in CLAIMABLE_STATUSES:
                    continue
                intent = OrderIntent.from_dict(row)
                if intent.execution_mode != str(execution_mode).upper():
                    continue
                # BOT OFF is a hard execution boundary, not merely a planner
                # hint.  A BUY cached before the operator switched the bot off
                # must never leak through the serialized worker afterwards.
                # MANUAL orders remain operator-authorised and are unaffected.
                if (
                    not allow_bot_buys
                    and intent.side == "BUY"
                    and intent.source == "BOT"
                ):
                    row.update(status="CANCELLED", result="MUA TỰ ĐỘNG đang OFF")
                    changed = True
                    continue
                if not self.buy_window_is_due(intent):
                    continue
                intent_phase = phase_provider(intent.symbol) if phase_provider else phase
                if not self._phase_is_due(intent, intent_phase):
                    continue
                if intent.defer_expiry_until_eligible and not intent.eligible_session_seen:
                    row["eligible_session_seen"] = True
                    deadline = datetime.fromtimestamp(self._now(), VN_TZ).replace(hour=15, minute=0, second=0, microsecond=0).timestamp()
                    row["expires_at"] = deadline
                    intent = OrderIntent.from_dict(row)
                    changed = True
                if intent.wait_for_trigger:
                    quote = quote_provider(intent.symbol) if quote_provider else None
                    if not self._trigger_is_ready(intent, quote):
                        continue
                if intent.execution_mode == "REAL" and not token_ready:
                    if intent.side == "BUY" and intent.source == "BOT":
                        row.update(status="CANCELLED", result="BUY bỏ qua: thiếu trading token")
                        changed = True
                        continue
                    if intent.status != "WAITING_TOKEN":
                        row["status"] = "WAITING_TOKEN"
                        row["result"] = "Trading token required"
                        changed = True
                    continue
                row["status"] = "SENDING"
                row["claimed_at"] = self._now()
                row["details"] = {**(row.get("details") or {}), "claim_protocol": "durable-v1"}
                row["eligible_session_seen"] = True
                intent = OrderIntent.from_dict(row)
                claimed.append(intent)
                changed = True
            if changed:
                self.store.write(rows)
        return claimed

    @staticmethod
    def _fill_quantities(result: BrokerOrderResult, submitted_quantity: int) -> tuple[int, int]:
        raw = result.raw if isinstance(result.raw, dict) else {}
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        filled = body.get("fillQuantity", body.get("filledQuantity"))
        leaves = body.get("leaveQuantity", body.get("remainingQuantity"))
        try:
            filled_qty = max(0, int(float(filled))) if filled is not None else (submitted_quantity if str(result.status).upper() in {"FILLED", "MATCHED", "COMPLETED", "DONE"} else 0)
        except (TypeError, ValueError):
            filled_qty = 0
        try:
            leaves_qty = max(0, int(float(leaves))) if leaves is not None else max(0, submitted_quantity - filled_qty)
        except (TypeError, ValueError):
            leaves_qty = max(0, submitted_quantity - filled_qty)
        return min(submitted_quantity, filled_qty), min(submitted_quantity, leaves_qty)

    @staticmethod
    def _broker_costs(raw: dict[str, Any] | None) -> tuple[float, float]:
        raw = raw if isinstance(raw, dict) else {}
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        try:
            notional = OrderQueue._broker_notional(body)
            # feeRate is documented as the TOTAL rate: never add exchangeFeeRate
            # again. Explicit broker amounts take precedence over rate estimates.
            fee = abs(float(body.get("fee", body.get("totalFee", notional * float(body.get("feeRate", 0.0) or 0.0))) or 0.0))
            tax = abs(float(body.get("tax", notional * float(body.get("taxRate", 0.0) or 0.0)) or 0.0))
        except (AttributeError, TypeError, ValueError):
            return 0.0, 0.0
        return fee, tax

    @staticmethod
    def _broker_notional(body: dict[str, Any]) -> float:
        quantity = max(0, int(float(body.get("fillQuantity", body.get("filledQuantity", 0)) or 0)))
        return quantity * account_price(body, body.get("averagePrice", body.get("price", 0))) * 1000.0

    def finish(
        self,
        intent: OrderIntent,
        result: BrokerOrderResult,
        *,
        submitted_quantity: int | None = None,
        keep_sell_remainder: bool = False,
    ) -> OrderIntent | None:
        submitted = max(0, int(submitted_quantity or intent.remaining_quantity or intent.quantity))
        status = self._normalized_broker_status(result.status) if result.ok else str(result.status or "FAILED").upper()
        if not result.ok and result.error == "ORDER_STATUS_UNKNOWN":
            status = "UNKNOWN"
        elif not result.ok and result.error == "TRADING_TOKEN_REQUIRED":
            status = "WAITING_TOKEN"
        elif not result.ok and status not in FINAL_STATUSES:
            status = "REJECTED" if result.status_code and result.status_code < 500 else "FAILED"
        filled_now, broker_leaves = self._fill_quantities(result, submitted)
        broker_fee, broker_tax = self._broker_costs(result.raw)
        filled_total = min(intent.quantity, intent.filled_quantity + filled_now)
        remaining = max(0, intent.quantity - filled_total)
        if result.ok:
            if broker_leaves > 0 and status not in FINAL_STATUSES | {"CANCEL_PENDING", "REPLACE_PENDING"}:
                status = "PARTIAL" if filled_now > 0 else "WORKING"
            elif remaining > 0 and keep_sell_remainder:
                status = "WAITING_SETTLEMENT"
            elif remaining == 0 and status in {"FILLED", "MATCHED", "COMPLETED", "DONE"}:
                status = "FILLED"
            elif status not in FINAL_STATUSES | {"PARTIAL", "WORKING", "CANCEL_PENDING", "REPLACE_PENDING"}:
                status = "WORKING"
        reset_broker_costs = status == "WAITING_SETTLEMENT"
        return self._update(
            intent.id,
            status=status,
            result=result.message or result.error,
            broker_order_id="" if reset_broker_costs else result.order_id,
            request_tag="" if reset_broker_costs else intent.request_tag,
            filled_quantity=filled_total,
            remaining_quantity=remaining,
            working_quantity=submitted if status in {"WORKING", "PARTIAL", "CANCEL_PENDING", "REPLACE_PENDING", "UNKNOWN"} else 0,
            broker_filled_quantity=0 if reset_broker_costs else filled_now,
            broker_fee_logged=0.0 if reset_broker_costs else broker_fee,
            broker_tax_logged=0.0 if reset_broker_costs else broker_tax,
            broker_notional_logged=0.0 if reset_broker_costs else self._broker_notional(result.raw.get("data", result.raw)),
            attempt=intent.attempt + 1 if reset_broker_costs else intent.attempt,
            handed_off_at=0.0 if reset_broker_costs else intent.handed_off_at,
            settlement_waited=bool(intent.settlement_waited or status == "WAITING_SETTLEMENT"),
        )

    @staticmethod
    def _normalized_broker_status(value: str) -> str:
        compact = str(value or "").upper().replace("_", "").replace(" ", "")
        if compact in {"FILLED", "MATCHED", "COMPLETED", "DONE"}:
            return "FILLED"
        if compact in {"PARTIAL", "PARTIALLYFILLED", "PARTIALFILLED"}:
            return "PARTIAL"
        if compact in {"PENDINGCANCEL", "CANCELPENDING"}:
            return "CANCEL_PENDING"
        if compact in {"PENDINGREPLACE", "REPLACEPENDING"}:
            return "REPLACE_PENDING"
        if compact in {"EXPIRED", "DONEFORDAY"}:
            return "EXPIRED"
        if compact in {"CANCELLED", "CANCELED"}:
            return "CANCELLED"
        if compact in {"REJECTED", "REJECT", "FAILED"}:
            return "REJECTED"
        return "WORKING"

    def reconcile_broker(self, intent: OrderIntent, broker_order: dict[str, Any]) -> tuple[OrderIntent | None, int]:
        broker_id = str(broker_order.get("orderId", broker_order.get("id", intent.broker_order_id)) or intent.broker_order_id)
        details = dict(intent.details)
        progress = dict(details.get("broker_progress") or {})
        previous = progress.get(broker_id) or {}
        previous_filled = int(previous.get("filled", intent.broker_filled_quantity if broker_id == intent.broker_order_id else 0))
        normalized = self._normalized_broker_status(
            str(broker_order.get("orderStatus", broker_order.get("status", "")) or "")
        )
        try:
            absolute_filled = max(0, int(float(broker_order.get("fillQuantity", broker_order.get("filledQuantity", 0)) or 0)))
        except (TypeError, ValueError):
            absolute_filled = 0
        delta = max(0, absolute_filled - previous_filled)
        if absolute_filled < previous_filled:
            return intent, 0  # Older broker snapshot must not rewind progress.
        broker_fee, broker_tax = self._broker_costs(broker_order)
        requested = details.get("requested_replace") or {}
        logical_quantity = intent.quantity
        replace_confirmed = False
        if requested and broker_id == intent.broker_order_id and int(broker_order.get("quantity", 0) or 0) == int(requested.get("broker_quantity", -1)):
            logical_quantity = int(requested["quantity"])
            details.pop("requested_replace", None)
            replace_confirmed = True
        filled_total = intent.filled_quantity + delta
        # Preserve the actual executed quantity even if it exceeds the local
        # replacement estimate. It is money spent, never an ignorable excess.
        logical_quantity = max(logical_quantity, filled_total)
        remaining = max(0, logical_quantity - filled_total)
        progress[broker_id] = {"filled": absolute_filled, "notional": self._broker_notional(broker_order), "fee": broker_fee, "tax": broker_tax, "status": normalized}
        details["broker_progress"] = progress
        changes: dict[str, Any] = {
            "quantity": logical_quantity,
            "filled_quantity": filled_total,
            "remaining_quantity": remaining,
            "broker_filled_quantity": absolute_filled,
            "result": normalized,
            "broker_fee_logged": broker_fee,
            "broker_tax_logged": broker_tax,
            "broker_notional_logged": self._broker_notional(broker_order),
            "details": details,
            "broker_order_id": intent.broker_order_id or broker_id,
        }
        if replace_confirmed:
            changes["limit_price"] = account_price(broker_order, broker_order.get("price", 0)) or float(requested["price"])
        if normalized in {"CANCEL_PENDING", "REPLACE_PENDING"}:
            changes["status"] = normalized
        elif normalized in {"WORKING", "PARTIAL"}:
            changes["status"] = "PARTIAL" if absolute_filled > 0 else "WORKING"
        elif normalized == "FILLED":
            changes["working_quantity"] = 0
            changes["status"] = "WAITING_SETTLEMENT" if intent.side == "SELL" and remaining > 0 else "FILLED"
            reset_broker = intent.side == "SELL" and remaining > 0
            changes["broker_order_id"] = "" if reset_broker else (intent.broker_order_id or broker_id)
            changes["request_tag"] = "" if reset_broker else intent.request_tag
            if reset_broker:
                changes["attempt"] = intent.attempt + 1
                changes["settlement_waited"] = True
                changes["broker_filled_quantity"] = 0
                changes["broker_fee_logged"] = 0.0
                changes["broker_tax_logged"] = 0.0
                changes["broker_notional_logged"] = 0.0
                changes["handed_off_at"] = 0.0
        elif normalized in {"CANCELLED", "REJECTED", "EXPIRED"}:
            changes["working_quantity"] = 0
            changes["status"] = normalized
        if broker_id != intent.broker_order_id and intent.broker_order_id:
            # A replaced OLD ID can still report fills; its cancellation must
            # not terminate the NEW active ID.
            for key in ("status", "broker_order_id", "request_tag", "attempt", "working_quantity", "broker_filled_quantity", "broker_notional_logged", "broker_fee_logged", "broker_tax_logged", "handed_off_at"):
                changes.pop(key, None)
        elif intent.status == "REPLACE_PENDING" and normalized in {"WORKING", "PARTIAL"} and not replace_confirmed and details.get("requested_replace"):
            changes["status"] = "REPLACE_PENDING"
        if intent.status in FINAL_STATUSES and changes.get("status") in {"WORKING", "PARTIAL", "CANCEL_PENDING", "REPLACE_PENDING"}:
            changes["status"] = intent.status
        if intent.status == "REPLACE_PENDING" and normalized in {"CANCELLED", "EXPIRED"} and details.get("requested_replace") and not any(value != intent.broker_order_id for value in intent.broker_order_ids):
            changes["status"] = "REPLACE_PENDING"
        other_open_ids = [value for value in intent.broker_order_ids if value != broker_id and (progress.get(value) or {}).get("status") not in FINAL_STATUSES]
        if changes.get("status") in FINAL_STATUSES and other_open_ids:
            changes["status"] = "REPLACE_PENDING"
        current_status = (progress.get(intent.broker_order_id) or {}).get("status")
        if intent.status == "REPLACE_PENDING" and not details.get("requested_replace") and not any((progress.get(value) or {}).get("status") not in FINAL_STATUSES for value in intent.broker_order_ids) and current_status in FINAL_STATUSES:
            changes["status"] = current_status
        return self._update(intent.id, **changes), delta

    def release(self, order_id: str, status: str, result: str = "") -> OrderIntent | None:
        target = str(status or "PENDING").upper()
        if target not in CLAIMABLE_STATUSES:
            raise ValueError(f"Unsupported release status: {target}")
        return self._update(order_id, status=target, result=result, claimed_at=0.0)

    def wait_for_settlement(self, order_id: str, result: str = "") -> OrderIntent | None:
        return self._update(
            order_id,
            status="WAITING_SETTLEMENT",
            result=result,
            claimed_at=0.0,
            settlement_waited=True,
        )

    @_transactional
    def cancel_waiting_sell(self, order_id: str, result: str) -> OrderIntent | None:
        item = self.get(order_id)
        if not item or item.side != "SELL" or item.status.upper() not in {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"}:
            return None
        return self._update(
            order_id,
            status="CANCELLED",
            result=result,
            claimed_at=0.0,
            working_quantity=0,
        )

    @_transactional
    def cancel_local(self, order_id: str) -> OrderIntent | None:
        item = self.get(order_id)
        if not item or item.status.upper() not in LOCALLY_CONTROLLABLE_STATUSES:
            return None
        return self._update(order_id, status="CANCELLED", result="Cancelled locally")

    @_transactional
    def cancel_claimed_local(self, order_id: str, result: str) -> OrderIntent | None:
        """Cancel a worker-claimed intent only before any broker hand-off."""
        item = self.get(order_id)
        if (
            not item
            or item.status.upper() != "SENDING"
            or item.broker_order_id
            or item.handed_off_at
            or item.filled_quantity > 0
        ):
            return None
        return self._update(
            order_id,
            status="CANCELLED",
            result=str(result or "Cancelled before broker hand-off"),
            claimed_at=0.0,
            working_quantity=0,
        )

    @_transactional
    def pause_local(self, order_id: str) -> OrderIntent | None:
        """Pause one unsent local intent without freeing its reserved slot."""
        item = self.get(order_id)
        if not item or item.status.upper() not in CLAIMABLE_STATUSES:
            return None
        details = dict(item.details or {})
        details["_operator_paused_from_status"] = item.status.upper()
        return self._update(
            order_id,
            status="PAUSED",
            result="Tạm dừng bởi operator",
            claimed_at=0.0,
            details=details,
        )

    @_transactional
    def resume_local(self, order_id: str) -> OrderIntent | None:
        """Return a paused local intent to the serialized execution queue."""
        item = self.get(order_id)
        if not item or item.status.upper() != "PAUSED":
            return None
        details = dict(item.details or {})
        previous_status = str(
            details.pop("_operator_paused_from_status", "PENDING") or "PENDING"
        ).upper()
        if previous_status not in CLAIMABLE_STATUSES:
            previous_status = "PENDING"
        return self._update(
            order_id,
            status=previous_status,
            result="Tiếp tục bởi operator",
            claimed_at=0.0,
            details=details,
        )

    @_transactional
    def replace_local(
        self,
        order_id: str,
        *,
        quantity: int,
        limit_price: float = 0.0,
        details: dict[str, Any] | None = None,
        em_modes: list[str] | None = None,
        sl_enabled: bool | None = None,
        sl_mode: str | None = None,
        sl_value: float | None = None,
        tp_mode: str | None = None,
        tp_value: float | None = None,
    ) -> OrderIntent | None:
        item = self.get(order_id)
        if not item or item.status.upper() not in LOCALLY_CONTROLLABLE_STATUSES:
            return None
        valid, _reason, normalized = validate_quantity(quantity)
        if not valid:
            return None
        if normalized < item.filled_quantity:
            return None
        try:
            price = float(limit_price or 0.0)
            if not all(math.isfinite(float(value)) for value in (price, sl_value, tp_value) if value is not None):
                return None
        except (ValueError, TypeError, OverflowError):
            return None
        if item.order_type == "LO" and price <= 0:
            return None
        changes: dict[str, Any] = {
            "quantity": normalized,
            "remaining_quantity": normalized - item.filled_quantity,
            "limit_price": price if item.order_type == "LO" else 0.0,
            "result": "Updated locally",
        }
        if details is not None:
            changes["details"] = dict(details)
        # A cached BUY has not created a position yet.  Persist its complete
        # management policy here so the eventual fill creates the TradeCycle
        # with exactly the controls shown in the edit popup.
        if em_modes is not None:
            changes["em_modes"] = list(em_modes)
        if sl_enabled is not None:
            changes["sl_enabled"] = bool(sl_enabled)
        if sl_mode is not None:
            changes["sl_mode"] = str(sl_mode)
        if sl_value is not None:
            changes["sl_value"] = float(sl_value)
        if tp_mode is not None:
            changes["tp_mode"] = str(tp_mode)
        if tp_value is not None:
            changes["tp_value"] = float(tp_value)
        return self._update(
            order_id,
            **changes,
        )

    @_transactional
    def mark_broker_replaced(
        self,
        order_id: str,
        *,
        quantity: int,
        limit_price: float,
        result: str = "Updated at broker",
        broker_order_id: str = "",
        broker_quantity: int | None = None,
    ) -> OrderIntent | None:
        item = self.get(order_id)
        if not item:
            return None
        details = dict(item.details)
        if int(quantity) < item.filled_quantity:
            return None
        details["requested_replace"] = {"quantity": int(quantity), "broker_quantity": int(quantity if broker_quantity is None else broker_quantity), "price": float(limit_price)}
        aliases = list(dict.fromkeys([*item.broker_order_ids, item.broker_order_id, broker_order_id]))
        if broker_order_id and broker_order_id != item.broker_order_id:
            progress = dict(details.get("broker_progress") or {})
            progress.setdefault(item.broker_order_id, {"filled": item.broker_filled_quantity, "notional": item.broker_notional_logged, "fee": item.broker_fee_logged, "tax": item.broker_tax_logged})
            details["broker_progress"] = progress
        return self._update(
            order_id,
            status="REPLACE_PENDING",
            details=details,
            broker_order_ids=[value for value in aliases if value],
            broker_order_id=broker_order_id or item.broker_order_id,
            broker_filled_quantity=0 if broker_order_id and broker_order_id != item.broker_order_id else item.broker_filled_quantity,
            broker_notional_logged=0.0 if broker_order_id and broker_order_id != item.broker_order_id else item.broker_notional_logged,
            broker_fee_logged=0.0 if broker_order_id and broker_order_id != item.broker_order_id else item.broker_fee_logged,
            broker_tax_logged=0.0 if broker_order_id and broker_order_id != item.broker_order_id else item.broker_tax_logged,
            result=result,
        )

    def mark_broker_cancelled(self, order_id: str, result: str = "Cancelled at broker") -> OrderIntent | None:
        return self._update(
            order_id,
            status="CANCEL_PENDING",
            cancel_requested=True,
            result=result,
        )
