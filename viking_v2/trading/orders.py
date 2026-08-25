from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from .market import order_is_due
from ..models import BrokerOrderResult, OrderIntent
from .portfolio import validate_quantity
from ..storage import AtomicJSONStore


FINAL_STATUSES = {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}

# A finished order stays in the queue long enough for the dashboard to show it,
# then goes.  The record itself lives on in order_history.csv and the journal,
# so nothing is lost - only the working set stays small enough to rewrite on
# every single add.
SETTLED_KEEP_SECONDS = 24 * 60 * 60
CLAIMABLE_STATUSES = {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"}


class OrderQueue:
    def __init__(self, path: str | Path, *, now=time.time):
        self.store = AtomicJSONStore(path, default=[])
        self._now = now
        self._lock = threading.RLock()

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
        active = self.find_active(
            intent.symbol,
            side=intent.side if intent.side == "BUY" else None,
            execution_mode=intent.execution_mode,
        )
        if intent.side == "BUY" and active:
            return active[0]
        return self.add(intent)

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
                if status in FINAL_STATUSES | {"UNKNOWN"}:
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
                if float(row.get("expires_at", 0.0) or 0.0) <= now:
                    row["status"] = "EXPIRED"
                    row["result"] = "Expired after 24 hours"
                    expired.append(OrderIntent.from_dict(row))
                    changed = True
                elif str(row.get("status", "")).upper() == "SENDING":
                    claimed = float(row.get("claimed_at", 0.0) or 0.0)
                    if claimed and now - claimed > 120.0:
                        row["status"] = "UNKNOWN"
                        row["result"] = "Recovered stale SENDING without retry"
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

    def claim_due(
        self,
        *,
        phase: str,
        execution_mode: str,
        token_ready: bool,
        limit: int = 20,
        quote_provider: Callable[[str], dict[str, Any] | None] | None = None,
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
                if not self._phase_is_due(intent, phase):
                    continue
                if intent.defer_expiry_until_eligible and not intent.eligible_session_seen:
                    row["eligible_session_seen"] = True
                    row["expires_at"] = max(float(row.get("expires_at", 0.0) or 0.0), self._now() + 6 * 60 * 60)
                    intent = OrderIntent.from_dict(row)
                    changed = True
                if intent.wait_for_trigger:
                    quote = quote_provider(intent.symbol) if quote_provider else None
                    if not self._trigger_is_ready(intent, quote):
                        continue
                if intent.execution_mode == "REAL" and not token_ready:
                    if intent.status != "WAITING_TOKEN":
                        row["status"] = "WAITING_TOKEN"
                        row["result"] = "Trading token required"
                        changed = True
                    continue
                row["status"] = "SENDING"
                row["claimed_at"] = self._now()
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
            fee = abs(float(body.get("fee", body.get("totalFee", 0.0)) or 0.0))
            tax = abs(float(body.get("tax", 0.0) or 0.0))
        except (AttributeError, TypeError, ValueError):
            return 0.0, 0.0
        return fee, tax

    def finish(
        self,
        intent: OrderIntent,
        result: BrokerOrderResult,
        *,
        submitted_quantity: int | None = None,
        keep_sell_remainder: bool = False,
    ) -> OrderIntent | None:
        submitted = max(0, int(submitted_quantity or intent.remaining_quantity or intent.quantity))
        status = str(result.status or ("FILLED" if result.ok else "FAILED")).upper()
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
            if broker_leaves > 0:
                status = "PARTIAL" if filled_now > 0 else "WORKING"
            elif remaining > 0 and keep_sell_remainder:
                status = "WAITING_SETTLEMENT"
            elif remaining == 0 and status in {"FILLED", "MATCHED", "COMPLETED", "DONE"}:
                status = "FILLED"
            elif status not in {"FILLED", "PARTIAL", "WORKING"}:
                status = "WORKING"
        reset_broker_costs = status == "WAITING_SETTLEMENT"
        return self._update(
            intent.id,
            status=status,
            result=result.message or result.error,
            broker_order_id=result.order_id,
            request_tag=intent.request_tag,
            filled_quantity=filled_total,
            remaining_quantity=remaining,
            working_quantity=submitted if status in {"WORKING", "PARTIAL"} else 0,
            broker_filled_quantity=filled_now if status in {"WORKING", "PARTIAL"} else 0,
            broker_fee_logged=0.0 if reset_broker_costs else broker_fee,
            broker_tax_logged=0.0 if reset_broker_costs else broker_tax,
            settlement_waited=bool(intent.settlement_waited or status == "WAITING_SETTLEMENT"),
        )

    @staticmethod
    def _normalized_broker_status(value: str) -> str:
        compact = str(value or "").upper().replace("_", "").replace(" ", "")
        if compact in {"FILLED", "MATCHED", "COMPLETED", "DONE"}:
            return "FILLED"
        if compact in {"PARTIAL", "PARTIALLYFILLED", "PARTIALFILLED"}:
            return "PARTIAL"
        if compact in {"CANCELLED", "CANCELED", "EXPIRED"}:
            return "CANCELLED"
        if compact in {"REJECTED", "REJECT", "FAILED"}:
            return "REJECTED"
        return "WORKING"

    def reconcile_broker(self, intent: OrderIntent, broker_order: dict[str, Any]) -> tuple[OrderIntent | None, int]:
        normalized = self._normalized_broker_status(
            str(broker_order.get("orderStatus", broker_order.get("status", "")) or "")
        )
        try:
            absolute_filled = max(0, int(float(broker_order.get("fillQuantity", broker_order.get("filledQuantity", 0)) or 0)))
        except (TypeError, ValueError):
            absolute_filled = 0
        delta = max(0, absolute_filled - intent.broker_filled_quantity)
        broker_fee, broker_tax = self._broker_costs(broker_order)
        filled_total = min(intent.quantity, intent.filled_quantity + delta)
        remaining = max(0, intent.quantity - filled_total)
        changes: dict[str, Any] = {
            "filled_quantity": filled_total,
            "remaining_quantity": remaining,
            "broker_filled_quantity": absolute_filled,
            "result": normalized,
            "broker_fee_logged": broker_fee,
            "broker_tax_logged": broker_tax,
        }
        if normalized in {"WORKING", "PARTIAL"}:
            changes["status"] = "PARTIAL" if absolute_filled > 0 else "WORKING"
        elif normalized == "FILLED":
            changes["working_quantity"] = 0
            changes["status"] = "WAITING_SETTLEMENT" if intent.side == "SELL" and remaining > 0 else "FILLED"
            changes["broker_order_id"] = "" if remaining > 0 else intent.broker_order_id
            changes["request_tag"] = "" if remaining > 0 else intent.request_tag
            if remaining > 0:
                changes["attempt"] = intent.attempt + 1
                changes["settlement_waited"] = True
                changes["broker_filled_quantity"] = 0
                changes["broker_fee_logged"] = 0.0
                changes["broker_tax_logged"] = 0.0
        elif normalized in {"CANCELLED", "REJECTED"}:
            changes["working_quantity"] = 0
            if intent.side == "SELL" and remaining > 0:
                changes.update(
                    status="PENDING",
                    broker_order_id="",
                    request_tag="",
                    attempt=intent.attempt + 1,
                    broker_filled_quantity=0,
                    broker_fee_logged=0.0,
                    broker_tax_logged=0.0,
                    settlement_waited=True,
                )
            else:
                changes["status"] = normalized
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

    def cancel_waiting_sell(self, order_id: str, result: str) -> OrderIntent | None:
        item = self.get(order_id)
        if not item or item.side != "SELL" or item.status.upper() not in {"PENDING", "WAITING_SETTLEMENT"}:
            return None
        return self._update(
            order_id,
            status="CANCELLED",
            result=result,
            claimed_at=0.0,
            working_quantity=0,
        )

    def cancel_local(self, order_id: str) -> OrderIntent | None:
        item = self.get(order_id)
        if not item or item.status.upper() not in CLAIMABLE_STATUSES:
            return None
        return self._update(order_id, status="CANCELLED", result="Cancelled locally")

    def replace_local(self, order_id: str, *, quantity: int, limit_price: float = 0.0) -> OrderIntent | None:
        item = self.get(order_id)
        if not item or item.status.upper() not in CLAIMABLE_STATUSES:
            return None
        valid, _reason, normalized = validate_quantity(quantity)
        if not valid:
            return None
        price = float(limit_price or 0.0)
        if item.order_type == "LO" and price <= 0:
            return None
        return self._update(
            order_id,
            quantity=normalized,
            remaining_quantity=normalized,
            limit_price=price if item.order_type == "LO" else 0.0,
            result="Updated locally",
        )

    def mark_broker_replaced(
        self,
        order_id: str,
        *,
        quantity: int,
        limit_price: float,
        result: str = "Updated at broker",
    ) -> OrderIntent | None:
        item = self.get(order_id)
        if not item:
            return None
        filled = max(0, item.filled_quantity)
        normalized = max(filled, int(quantity or 0))
        return self._update(
            order_id,
            quantity=normalized,
            remaining_quantity=max(0, normalized - filled),
            working_quantity=max(0, normalized - filled),
            limit_price=float(limit_price or 0.0),
            result=result,
        )

    def mark_broker_cancelled(self, order_id: str, result: str = "Cancelled at broker") -> OrderIntent | None:
        return self._update(
            order_id,
            status="CANCELLED",
            result=result,
            working_quantity=0,
        )
