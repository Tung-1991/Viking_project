from __future__ import annotations

import time
import uuid
import hashlib
import json
from dataclasses import asdict
from dataclasses import replace
from typing import Callable
from typing import Any

from ..connections.dnse.client import DNSEClient, BrokerSnapshotError
from ..connections.dnse.paper import PaperBroker
from .market import market_phase
from ..models import BrokerOrderResult, OrderIntent, TradeCycle
from .orders import OrderQueue
from .portfolio import (
    available_to_sell,
    board_price,
    account_price,
    position_quantity,
    round_lot_down,
    sell_quantity_for_fraction,
    validate_quantity,
)
from ..storage import CSVOrderJournal, JSONLineJournal
from .state import TradeStateStore
from .orders import FINAL_STATUSES
from .validation import quote_is_fresh
from .portfolio import cash_from_balance
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
        manual_sell_pause_seconds_provider: Callable[[], float] | None = None,
        bot_buy_allowed_provider: Callable[[str], bool] | None = None,
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
        self.manual_sell_pause_seconds_provider = manual_sell_pause_seconds_provider
        self.bot_buy_allowed_provider = bot_buy_allowed_provider
        self.database = queue.store.database
        self._blocked_rechecks: set[str] = set()
        self._deferred_events: list[tuple[str, TradeCycle, OrderIntent]] | None = None
        self.stopping = False
        self.recover_results()

    def recover_results(self) -> None:
        for key, payload in self.database.pending_results():
            self._apply_submission(key, payload)
        self.flush_events()

    @staticmethod
    def validate_order_binding(intent: OrderIntent, row: dict[str, Any]) -> str:
        """Validate an operator-selected ID; never guess an ID automatically."""
        from .validation import timestamp_value
        broker_id = str(row.get("orderId", row.get("id", "")) or "")
        if intent.execution_mode != "REAL" or intent.status not in {"UNKNOWN", "SENDING", "REPLACE_PENDING"}:
            raise ValueError("Lệnh local không thuộc trạng thái cần liên kết DNSE")
        if not broker_id or str(row.get("symbol", "")).upper() != intent.symbol:
            raise ValueError("Broker ID/mã không khớp")
        if str(row.get("side", "")).upper() not in ({"NB", "BUY"} if intent.side == "BUY" else {"NS", "SELL"}):
            raise ValueError("Chiều BUY/SELL không khớp")
        if intent.loan_package_id and str(row.get("loanPackageId", "")) != intent.loan_package_id:
            raise ValueError("Gói giao dịch không khớp")
        expected = int((intent.details.get("requested_replace") or {}).get("broker_quantity", intent.working_quantity or intent.remaining_quantity or intent.quantity))
        if int(row.get("quantity", 0) or 0) != expected:
            raise ValueError("Khối lượng lệnh DNSE không khớp yêu cầu")
        if row.get("remark") and intent.request_tag and row["remark"] != intent.request_tag:
            raise ValueError("Định danh yêu cầu không khớp")
        created = timestamp_value(row.get("createdDate", row.get("createdAt")))
        if not created or created < (intent.handed_off_at or intent.created_at)-5:
            raise ValueError("Lệnh DNSE không có thời điểm tạo phù hợp")
        return broker_id

    def bind_confirmed_order(self, intent_id: str, row: dict[str, Any]) -> OrderIntent | None:
        with self.database:
            intent = self.queue.get(intent_id)
            if not intent:
                raise ValueError("Không tìm thấy lệnh local")
            broker_id = self.validate_order_binding(intent, row)
            self.database.bind_order(broker_id, intent.id)
            requested = intent.details.get("requested_replace") or {}
            if intent.status == "REPLACE_PENDING":
                self.queue.mark_broker_replaced(intent.id, quantity=int(requested["quantity"]), broker_quantity=int(requested["broker_quantity"]), limit_price=float(requested["price"]), broker_order_id=broker_id)
            else:
                self.queue._update(intent.id, broker_order_id=broker_id, status="UNKNOWN")
            key = "bound:" + intent.id + ":" + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            payload = {"kind": "reconcile", "intent": intent.to_dict(), "broker": row}
            self.database.put_result(key, payload)
        return self._apply_broker_row(key, payload)

    def flush_events(self) -> None:
        for event in self.database.pending_events():
            try:
                self.journal.append(event)
                self.csv_journal.append_event(event)
                self.database.mark_exported(event["event_id"])
            except OSError:
                # The durable event remains pending; archive I/O must not stop
                # management or cause a broker order to be submitted again.
                break

    def _append_local_event(self, event: dict[str, Any]) -> None:
        event.setdefault("event_id", "local:" + hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest())
        with self.database:
            self.database.add_event(event)
        if self._deferred_events is None:
            self.flush_events()

    def _apply_submission(self, key: str, payload: dict[str, Any]) -> OrderIntent | None:
        if payload.get("kind") == "reconcile":
            return self._apply_broker_row(key, payload)
        intent = OrderIntent.from_dict(payload["intent"])
        result = BrokerOrderResult(**payload["result"])
        submitted = int(payload["submitted"])
        notifications: list[tuple[str, TradeCycle, OrderIntent]] = []
        try:
            with self.database:
                if self.database.result_applied(key):
                    return self.queue.get(intent.id)
                self._deferred_events = notifications
                self.database.bind_order(result.order_id, intent.id)
                persisted = self.queue.finish(intent, result, submitted_quantity=submitted, keep_sell_remainder=intent.side == "SELL")
                self._record_trade_fill(intent, result, submitted, mark_events=bool(persisted and persisted.filled_quantity > intent.filled_quantity))
                persisted = self.queue.get(intent.id) or persisted
                self.database.add_event({"event_id": key, "ts": time.time(), "intent": (persisted or intent).to_dict(), "queue_status": persisted.status if persisted else "", "result": asdict(result)})
                self.database.mark_applied(key)
        finally:
            self._deferred_events = None
        for args in notifications:
            self._emit_trade_event(*args)
        return persisted

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
            self._append_local_event(event)
        return cancelled, broker_managed

    def cancel_unsubmitted_bot_buys(
        self,
        execution_mode: str,
        *,
        reason: str = "MUA TỰ ĐỘNG chuyển sang OFF",
    ) -> tuple[list[OrderIntent], list[OrderIntent]]:
        """Cancel safe local BOT BUYs when automatic entry is switched off.

        A request that may already be at the broker is never guessed away;
        those rows are returned for an explicit operator check/cancel instead.
        MANUAL BUYs are deliberately untouched because they carry separate
        operator authority.
        """
        mode = "REAL" if str(execution_mode or "").upper() == "REAL" else "PAPER"
        cancelled: list[OrderIntent] = []
        broker_managed: list[OrderIntent] = []
        for intent in self.queue.list_all():
            if (
                intent.execution_mode != mode
                or intent.side != "BUY"
                or intent.source != "BOT"
                or intent.status.upper() in {
                    "FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED",
                }
            ):
                continue
            if (
                intent.filled_quantity > 0
                or intent.broker_order_id
                or intent.handed_off_at
                or intent.status.upper() in {"WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"}
            ):
                broker_managed.append(intent)
                continue
            updated = (self.queue.cancel_claimed_local(intent.id, reason) if intent.status == "SENDING" else self.queue.cancel_local(intent.id))
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
                    "message": str(reason or "MUA TỰ ĐỘNG chuyển sang OFF"),
                    "error": "",
                    "status_code": 0,
                    "raw": {},
                },
            }
            self._append_local_event(event)
        return cancelled, broker_managed

    def process_due(
        self, *, phase: str, execution_mode: str,
        phase_provider: Callable[[str], str] | None = None,
        allow_bot_buys: bool = True,
    ) -> list[tuple[OrderIntent, BrokerOrderResult]]:
        self.recover_results()
        mode = str(execution_mode).upper()
        token_ready = True if mode == "PAPER" else self.real.has_trading_token()
        broker: Any = self.paper if mode == "PAPER" else self.real
        self._revalidate_waiting_sells(phase, mode, broker, phase_provider)
        due = self.queue.claim_due(
            phase=phase,
            execution_mode=mode,
            token_ready=token_ready,
            allow_bot_buys=allow_bot_buys,
            quote_provider=self.quote_provider,
            phase_provider=phase_provider,
        )
        completed: list[tuple[OrderIntent, BrokerOrderResult]] = []
        # Exit risk first.  Besides being the safer order, this lets a filled
        # MANUAL SELL activate its BUY cooldown before another cached BOT BUY
        # from the same worker batch can reach the broker.
        due.sort(key=lambda item: 0 if item.side == "SELL" else 1)
        due_ids = {item.id for item in due}
        for intent in due:
            if self.stopping:
                if intent.source == "BOT" and intent.side == "BUY":
                    self.queue.cancel_claimed_local(intent.id, "BUY bỏ qua: ứng dụng đang đóng")
                else:
                    self.queue.release(intent.id, "PENDING", "Ứng dụng đang đóng; giữ yêu cầu chưa gửi")
                continue
            current = self.queue.get(intent.id)
            if not current or current.status != "SENDING":
                if current and current.status == "CANCELLED":
                    completed.append((current, BrokerOrderResult(True, "CANCELLED", message=current.result)))
                continue
            intent = current
            if intent.id in self._blocked_rechecks:
                self.queue.wait_for_settlement(intent.id, "RECHECK: chờ quyết định mới hợp lệ")
                continue
            pause_active = bool(
                self.rule_state
                and self.rule_state.entry_pause(mode).get("active", False)
            )
            if (
                intent.side == "BUY"
                and intent.source == "BOT"
                and (not allow_bot_buys or pause_active or (
                    self.bot_buy_allowed_provider is not None
                    and not self.bot_buy_allowed_provider(mode)
                ))
            ):
                message = (
                    "Khóa BUY sau SELL MANUAL"
                    if pause_active else "MUA TỰ ĐỘNG đang OFF"
                )
                cancelled = self.queue.cancel_claimed_local(intent.id, message)
                if cancelled:
                    result = BrokerOrderResult(
                        True, "CANCELLED", message=message,
                    )
                    event = {
                        "ts": time.time(),
                        "intent": cancelled.to_dict(),
                        "queue_status": "CANCELLED",
                        "result": {
                            "ok": True,
                            "status": "CANCELLED",
                            "order_id": "",
                            "message": message,
                            "error": "",
                            "status_code": 0,
                            "raw": {},
                        },
                    }
                    self._append_local_event(event)
                    completed.append((intent, result))
                continue
            intent_phase = phase_provider(intent.symbol) if phase_provider else phase
            send_quantity = intent.remaining_quantity or intent.quantity
            if intent.side == "SELL":
                try:
                    positions = broker.get_positions() if mode == "PAPER" else broker.get_positions(force=True)
                except TypeError:
                    positions = broker.get_positions()
                except Exception:
                    self.queue.release(intent.id, "PENDING", "Chờ snapshot tài khoản hợp lệ; chưa gửi broker")
                    continue
                if intent.loan_package_id:
                    positions = [row for row in positions if str(row.get("loanPackageId", "")) == intent.loan_package_id]
                sellable = available_to_sell(positions, intent.symbol)
                cycle = self.trade_state.get(intent.trade_id) if self.trade_state and intent.trade_id else None
                broker_holding = sum(position_quantity(row) for row in positions if str(row.get("symbol", "")).upper() == intent.symbol)
                if cycle and cycle.open_quantity == 0 and not any(item.trade_id == cycle.id and item.side == "BUY" and item.status not in FINAL_STATUSES for item in self.queue.list_all()):
                    self.queue.cancel_claimed_local(intent.id, "Vị thế đã đóng; bỏ SELL chưa gửi")
                    continue
                if broker_holding == 0 and not cycle:
                    result = BrokerOrderResult(False, "REJECTED", error="NO_POSITION", message="Không có cổ phiếu để bán; không tạo cache T+2")
                    self.queue.finish(intent, result, submitted_quantity=send_quantity)
                    completed.append((intent, result))
                    continue
                raw_sellable = sellable
                if cycle:
                    reserved = sum(item.remaining_quantity for item in self.queue.list_all()
                                   if item.id != intent.id and item.trade_id == intent.trade_id
                                   and item.side == "SELL" and item.status in {"WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"})
                    sellable = min(sellable, max(0, cycle.open_quantity - reserved))
                send_quantity = round_lot_down(min(send_quantity, sellable))
                if send_quantity <= 0:
                    if broker_holding <= 0 or raw_sellable > 0:
                        self.queue.release(intent.id, "PENDING", "Chờ đối soát vị thế/lệnh SELL trước; chưa gửi broker")
                    else:
                        self.queue.wait_for_settlement(intent.id, "Chờ cổ phiếu về")
                    continue
            send_intent = replace(
                intent,
                quantity=send_quantity,
                filled_quantity=0,
                remaining_quantity=send_quantity,
                order_type=intent_phase if intent.order_type == "MARKET" and intent_phase in {"ATO", "ATC"} else intent.order_type,
            )
            needs_quote = intent.order_type == "MARKET" or intent.source in {"BOT", "EM"}
            if self.quote_provider and needs_quote:
                quote = self.quote_provider(intent.symbol)
                if not quote_is_fresh(quote, intent.symbol):
                    if intent.side == "BUY" and intent.source == "BOT":
                        self.queue.cancel_claimed_local(intent.id, "BUY bỏ qua: giá không còn mới/đúng mã")
                    else:
                        self.queue.release(intent.id, "PENDING", "Chờ giá mới hợp lệ")
                    continue
            else:
                quote = None
            if mode == "REAL" and isinstance(broker, DNSEClient):
                try:
                    package = broker.cash_package(intent.symbol)
                    if intent.loan_package_id and intent.loan_package_id != str(package["id"]):
                        raise ValueError("Deal không thuộc gói tiền mặt đã xác minh; không đổi sang Deal khác")
                    intent.loan_package_id = str(package["id"])
                    send_intent.loan_package_id = intent.loan_package_id
                    if intent.side == "BUY":
                        execution_price = intent.limit_price if intent.order_type == "LO" else board_price((quote or {}).get("ask", (quote or {}).get("price", 0)))
                        if execution_price <= 0:
                            raise ValueError("Không có giá để kiểm tra tiền")
                        fee_rate = float(package.get("brokerFirmBuyingFeeRate", 0) or 0)
                        cash = cash_from_balance(broker.get_balance(force=True) or {})
                        for other in self.queue.list_all():
                            if other.id == intent.id or other.execution_mode != mode or other.side != "BUY" or other.status in FINAL_STATUSES:
                                continue
                            if other.status == "UNKNOWN":
                                raise ValueError("Chờ đối soát BUY chưa rõ trạng thái trước khi phân bổ tiền tiếp")
                            if other.broker_order_id or other.handed_off_at:
                                continue  # Broker availableCash already reserves accepted orders.
                            if other.id in due_ids:
                                continue  # Serialized batch validates fresh cash before each hand-off.
                            reservation_price = other.limit_price or float(other.details.get("reservation_price", 0) or 0)
                            if not reservation_price:
                                raise ValueError(f"Thiếu giá giữ chỗ của {other.symbol}")
                            cash -= other.remaining_quantity * reservation_price * 1000 * (1 + fee_rate)
                        power = broker.get_buying_power(intent.symbol, intent.loan_package_id, execution_price)
                        if send_quantity > int(power["qmaxBuy"]) or send_quantity * execution_price * 1000 * (1 + fee_rate) > cash:
                            raise ValueError("Không đủ tiền/sức mua tiền mặt gồm phí")
                        if self.trade_state and intent.trade_id and not self.trade_state.active_for(intent.symbol, mode, intent.loan_package_id):
                            from .portfolio import position_cost
                            existing = [row for row in broker.get_positions(force=True) if str(row.get("symbol", "")).upper() == intent.symbol and str(row.get("loanPackageId", "")) == intent.loan_package_id and position_quantity(row) > 0]
                            if len(existing) > 1:
                                raise ValueError("Không xác định duy nhất Deal tiền mặt")
                            if existing:
                                row = existing[0]
                                if position_cost(row) <= 0:
                                    raise ValueError("Deal hiện có thiếu giá vốn hợp lệ")
                                intent.details["existing_deal"] = {"quantity": position_quantity(row), "cost": position_cost(row), "id": str(row.get("id", row.get("positionId", ""))), "observed_at": time.time()}
                                baseline_progress = {}
                                for previous_order in broker.get_orders(force=True):
                                    if str(previous_order.get("symbol", "")).upper() != intent.symbol or str(previous_order.get("loanPackageId", "")) != intent.loan_package_id:
                                        continue
                                    previous_id = str(previous_order.get("orderId", previous_order.get("id", "")) or "")
                                    if previous_id:
                                        fee, tax = self.queue._broker_costs(previous_order)
                                        baseline_progress[previous_id] = {"filled": int(previous_order.get("fillQuantity", 0) or 0), "notional": self.queue._broker_notional(previous_order), "cost": fee+tax}
                                intent.details["existing_deal"]["progress"] = baseline_progress
                except BrokerSnapshotError as exc:
                    if intent.source == "BOT" and intent.side == "BUY":
                        self.queue.cancel_claimed_local(intent.id, "BUY bỏ qua: snapshot DNSE không hợp lệ")
                    else:
                        self.queue.release(intent.id, "PENDING", "Chờ dữ liệu tài khoản DNSE hợp lệ")
                    continue
                except Exception as exc:
                    result = BrokerOrderResult(False, "REJECTED", error="SEND_VALIDATION_FAILED", message=str(exc))
                    self.queue.finish(intent, result, submitted_quantity=send_quantity)
                    self._append_local_event({"ts": time.time(), "intent": (self.queue.get(intent.id) or intent).to_dict(), "queue_status": "REJECTED", "result": asdict(result)})
                    completed.append((intent, result))
                    continue
            if intent.side == "BUY" and intent.source == "BOT" and self.bot_buy_allowed_provider is not None and not self.bot_buy_allowed_provider(mode):
                self.queue.cancel_claimed_local(intent.id, "BUY bỏ qua: OFF trước hand-off")
                continue
            if self.quote_provider and needs_quote and not quote_is_fresh(self.quote_provider(intent.symbol), intent.symbol):
                if intent.side == "BUY" and intent.source == "BOT":
                    self.queue.cancel_claimed_local(intent.id, "BUY bỏ qua: quote hết hạn trước hand-off")
                else:
                    self.queue.release(intent.id, "PENDING", "Chờ quote mới")
                continue
            # Durable hand-off identity BEFORE the network call. A timeout never
            # authorizes another POST. Persist the package on the original, too.
            if intent.side == "BUY" and self.trade_state:
                active_cycle = self.trade_state.active_for(intent.symbol, mode, intent.loan_package_id)
                if active_cycle:
                    intent.trade_id = send_intent.trade_id = active_cycle.id
            intent.request_tag = intent.request_tag or f"V2:{intent.id.upper()}:{intent.attempt}"
            intent.handed_off_at = time.time()
            send_intent.request_tag = intent.request_tag
            send_intent.handed_off_at = intent.handed_off_at
            self.queue._update(intent.id, request_tag=intent.request_tag, handed_off_at=intent.handed_off_at, loan_package_id=intent.loan_package_id, trade_id=intent.trade_id, details=intent.details)
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
                result = BrokerOrderResult(False, "UNKNOWN", message=str(exc), error="ORDER_STATUS_UNKNOWN")
            key = f"submit:{intent.id}:{intent.attempt}"
            payload = {"intent": intent.to_dict(), "result": asdict(result), "submitted": send_quantity}
            self.database.put_result(key, payload)
            self._apply_submission(key, payload)
            self.flush_events()
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
            and item.status in {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"}
            and (item.settlement_waited or item.status == "WAITING_TOKEN")
            and item.sell_wait_policy == "RECHECK"
            and self.queue._phase_is_due(
                item, phase_provider(item.symbol) if phase_provider else phase,
            )
        ]
        self._blocked_rechecks = {item.id for item in candidates}
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
                self._blocked_rechecks.discard(intent.id)
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
                    cycle = self.trade_state.get(intent.trade_id) if self.trade_state and intent.trade_id else None
                    if cycle:
                        holding = min(holding, cycle.open_quantity)
                    desired = sell_quantity_for_fraction(holding, fraction)
                    refreshed_details = {
                        key: decision_details.get(key)
                        for key in (
                            "normal_policy", "normal_dynamic_enabled", "normal_repeat_enabled",
                            "normal_arm_pct", "normal_giveback_pct", "normal_atr_pct",
                            "normal_activation_mfe_pct", "normal_atr_activation_multiplier",
                            "normal_atr_multiplier", "normal_retention_pct",
                            "normal_retention_until_pct", "normal_atr_activation_enabled",
                            "normal_atr_trail_enabled", "normal_retention_enabled",
                            "normal_retention_until_enabled", "sell_share_pct",
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
            self._append_local_event(event)

    def _record_trade_fill(
        self,
        intent: OrderIntent,
        result: BrokerOrderResult,
        submitted: int,
        *,
        mark_events: bool = False,
    ) -> None:
        if not result.ok:
            return
        filled, _leaves = self.queue._fill_quantities(result, submitted)
        if filled <= 0:
            return
        # A manual exit is an explicit operator judgement. On its first fill,
        # pause only automatic entry for this PAPER/REAL book and discard safe
        # unsent BOT BUYs so a stale signal cannot immediately refill the slot.
        if (
            intent.side == "SELL"
            and intent.source == "MANUAL"
            and intent.filled_quantity <= 0
            and self.rule_state
        ):
            try:
                pause_seconds = max(
                    0.0,
                    float(
                        self.manual_sell_pause_seconds_provider()
                        if self.manual_sell_pause_seconds_provider else 0.0
                    ),
                )
            except (TypeError, ValueError):
                pause_seconds = 0.0
            if pause_seconds > 0:
                self.rule_state.start_entry_pause(
                    intent.execution_mode,
                    pause_seconds,
                    reason="MANUAL_SELL",
                    symbol=intent.symbol,
                )
                self.cancel_unsubmitted_bot_buys(
                    intent.execution_mode,
                    reason=f"Khóa BUY sau SELL MANUAL {intent.symbol}",
                )
        if not self.trade_state or not intent.trade_id:
            return
        raw = result.raw if isinstance(result.raw, dict) else {}
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        price = account_price(body, body.get("averagePrice", body.get("price", 0.0)))
        broker_fee, broker_tax = self.queue._broker_costs(body)
        fee = broker_fee + broker_tax
        if intent.side == "BUY":
            previous = self.trade_state.get(intent.trade_id)
            if not previous:
                created_cycle = self.trade_state.create(
                    intent.symbol,
                    intent.execution_mode,
                    source=intent.source,
                    trade_id=intent.trade_id,
                    em_modes=intent.em_modes,
                    sl_enabled=intent.sl_enabled,
                    sl_mode=intent.sl_mode,
                    sl_value=intent.sl_value,
                    tp_mode=intent.tp_mode,
                    tp_value=intent.tp_value,
                    entry_market_state=intent.entry_market_state,
                    entry_exposure=intent.entry_exposure,
                    entry_budget=intent.entry_budget,
                    loan_package_id=intent.loan_package_id,
                    deal_id=intent.deal_id,
                )
                if created_cycle.id != intent.trade_id:
                    intent.trade_id = created_cycle.id
                    self.queue._update(intent.id, trade_id=created_cycle.id)
                    previous = created_cycle
                else:
                    baseline = intent.details.get("existing_deal") or {}
                    if baseline:
                        created_cycle.opened_at = float(baseline["observed_at"])
                        created_cycle.deal_id = str(baseline["id"])
                        created_cycle.external_progress = dict(baseline.get("progress") or {})
                        created_cycle.record_buy_fill(int(baseline["quantity"]), float(baseline["cost"]))
                        self.trade_state.save(created_cycle)
            cycle = self.trade_state.record_buy_fill(intent.trade_id, filled, price, fee)
            if cycle and (not previous or previous.entry_quantity == 0):
                self._emit_trade_event("OPEN", cycle, intent)
        else:
            previous = self.trade_state.get(intent.trade_id)
            keep_open = any(item.trade_id == intent.trade_id and item.side == "BUY" and item.status not in FINAL_STATUSES for item in self.queue.list_all())
            cycle = self.trade_state.record_sell_fill(intent.trade_id, filled, price, fee, keep_open=keep_open)
            if mark_events:
                self._mark_exit_events(intent)
                cycle = self.trade_state.get(intent.trade_id) or cycle
            if cycle and previous and previous.open_quantity > 0 and cycle.status == "CLOSED":
                self._emit_trade_event("CLOSED", cycle, intent)

    def _emit_trade_event(self, event: str, cycle: TradeCycle, intent: OrderIntent) -> None:
        if self._deferred_events is not None:
            self._deferred_events.append((event, cycle, intent))
            return
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
        self.recover_results()
        mode = str(execution_mode or "PAPER").upper()
        broker = self.paper if mode == "PAPER" else self.real
        active = [
            intent
            for intent in self.queue.list_all()
            if intent.execution_mode == mode
            and (intent.status in {"WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING", "SENDING"} or bool(intent.broker_order_id))
        ]
        # Do not poll DNSE /orders when Viking has nothing to reconcile.
        if not active:
            return []
        try:
            # Reuse the DNSE client's short order cache. Reconciliation runs
            # frequently, but the REST endpoint must not be hit every second.
            broker_orders = list(broker.get_orders() if mode == "PAPER" else broker.get_orders(force=False) or [])
        except Exception:
            return []
        updated: list[OrderIntent] = []
        for intent in active:
            ids = {value for value in [intent.broker_order_id, *intent.broker_order_ids] if value}
            matches = [row for row in broker_orders if (
                str(row.get("orderId", row.get("id", ""))) in ids
                or (intent.request_tag and intent.request_tag == str(row.get("remark", "") or ""))
            )]
            missing_ids = ids - {str(row.get("orderId", row.get("id", ""))) for row in matches}
            if hasattr(broker, "get_order_detail"):
                for broker_id in missing_ids:
                    row = broker.get_order_detail(broker_id)
                    if isinstance(row, dict) and row:
                        matches.append(row)
            for match in matches:
                if str(match.get("symbol", intent.symbol)).upper() != intent.symbol:
                    continue
                if match.get("side") and str(match["side"]).upper() not in ({"NB", "BUY"} if intent.side == "BUY" else {"NS", "SELL"}):
                    continue
                if intent.loan_package_id and str(match.get("loanPackageId", "")) != intent.loan_package_id:
                    continue
                key = "reconcile:" + intent.id + ":" + hashlib.sha256(json.dumps(match, sort_keys=True).encode()).hexdigest()
                payload = {"kind": "reconcile", "intent": intent.to_dict(), "broker": match}
                self.database.put_result(key, payload)
                reconciled = self._apply_broker_row(key, payload)
                if reconciled:
                    updated.append(reconciled)
        return updated

    def _apply_broker_row(self, key: str, payload: dict[str, Any]) -> OrderIntent | None:
        notifications: list[tuple[str, TradeCycle, OrderIntent]] = []
        event = None
        try:
            with self.database:
                if self.database.result_applied(key):
                    return None
                intent = self.queue.get(payload["intent"]["id"])
                if not intent:
                    raise RuntimeError("Broker result has no durable order")
                match = dict(payload["broker"])
                broker_id = str(match.get("orderId", match.get("id", intent.broker_order_id)) or intent.broker_order_id)
                self.database.bind_order(broker_id, intent.id)
                progress = (intent.details.get("broker_progress") or {}).get(broker_id)
                if progress is None:
                    progress = {"filled": intent.broker_filled_quantity, "notional": intent.broker_notional_logged, "fee": intent.broker_fee_logged, "tax": intent.broker_tax_logged} if broker_id == intent.broker_order_id else {}
                absolute = int(match.get("fillQuantity", match.get("filledQuantity", 0)) or 0)
                if absolute < int(progress.get("filled", 0)):
                    self.database.mark_applied(key)
                    return None
                delta = max(0, absolute - int(progress.get("filled", 0)))
                notional = self.queue._broker_notional(match)
                if delta and notional <= float(progress.get("notional", 0)):
                    # Do not poison replay with an incomplete broker snapshot.
                    # Keep tracking the order; a later complete detail is a new
                    # inbox item and supplies the authoritative fill price.
                    self.database.mark_applied(key)
                    return None
                if not any(name in match for name in ("fee", "totalFee", "feeRate")):
                    match["fee"] = progress.get("fee", 0)
                if not any(name in match for name in ("tax", "taxRate")):
                    match["tax"] = progress.get("tax", 0)
                fee, tax = self.queue._broker_costs(match)
                fee_delta, tax_delta = fee - float(progress.get("fee", 0)), tax - float(progress.get("tax", 0))
                self._deferred_events = notifications
                reconciled, delta = self.queue.reconcile_broker(intent, match)
                if delta:
                    delta_raw = {**match, "price_unit": "VND", "fillQuantity": delta, "averagePrice": (notional - float(progress.get("notional", 0))) / delta, "fee": max(0, fee_delta + tax_delta), "tax": 0}
                    result = BrokerOrderResult(True, str(match.get("orderStatus", "")), order_id=broker_id, raw=delta_raw)
                    self._record_trade_fill(intent, result, delta, mark_events=True)
                    result.raw.update(fee=fee_delta, tax=tax_delta)
                    if fee_delta + tax_delta < 0 and self.trade_state:
                        self.trade_state.adjust_costs(intent.trade_id, fee_delta + tax_delta)
                    event_intent = intent.to_dict()
                    event_intent.update(filled_quantity=delta, remaining_quantity=reconciled.remaining_quantity if reconciled else 0)
                    event = {"ts": time.time(), "event_id": key, "intent": event_intent, "queue_status": reconciled.status if reconciled else "", "result": {**asdict(result), "message": "BROKER_FILL_RECONCILED", "status_code": 200}}
                elif fee_delta + tax_delta and self.trade_state:
                    self.trade_state.adjust_costs(intent.trade_id, fee_delta + tax_delta)
                    event_intent = intent.to_dict()
                    event_intent["filled_quantity"] = 0
                    event = {"ts": time.time(), "event_id": key, "intent": event_intent, "queue_status": reconciled.status if reconciled else "",
                             "result": {"ok": True, "status": "COST_ADJUSTMENT", "order_id": broker_id, "message": "BROKER_COST_RECONCILED", "error": "", "status_code": 200,
                                        "raw": {"fee": fee_delta, "tax": tax_delta}}}
                if intent.side == "BUY" and reconciled and reconciled.status in FINAL_STATUSES and self.trade_state:
                    cycle = self.trade_state.get(intent.trade_id)
                    if cycle and cycle.status == "OPEN" and cycle.open_quantity == 0 and not any(item.trade_id == cycle.id and item.side == "BUY" and item.status not in FINAL_STATUSES for item in self.queue.list_all()):
                        cycle.status, cycle.closed_at = "CLOSED", time.time()
                        self.trade_state.save(cycle)
                if event:
                    self.database.add_event(event)
                self.database.mark_applied(key)
        finally:
            self._deferred_events = None
        if event:
            self.flush_events()
        for args in notifications:
            self._emit_trade_event(*args)
        return reconciled

    def _reconcile_external_costs(self, broker_orders: list[dict[str, Any]]) -> None:
        if not self.trade_state:
            return
        by_id = {str(row.get("orderId", row.get("id", ""))): row for row in broker_orders}
        for stored in self.trade_state.list_cycles():
            if stored.execution_mode != "REAL" or not stored.external_progress:
                continue
            with self.database:
                cycle = self.trade_state.get(stored.id)
                for broker_id, previous in list(cycle.external_progress.items()):
                    row = by_id.get(broker_id)
                    if not row or str(row.get("symbol", "")).upper() != cycle.symbol or (cycle.loan_package_id and str(row.get("loanPackageId", "")) != cycle.loan_package_id):
                        continue
                    if int(row.get("fillQuantity", 0) or 0) != int(previous.get("filled", 0)) or not any(key in row for key in ("fee", "totalFee", "feeRate", "tax", "taxRate")):
                        continue
                    fee, tax = self.queue._broker_costs(row)
                    cost = fee+tax
                    delta = cost-float(previous.get("cost", 0))
                    if not delta:
                        continue
                    previous["cost"] = cost
                    self.trade_state.save(cycle)
                    self.trade_state.adjust_costs(cycle.id, delta)
                    cycle = self.trade_state.get(cycle.id)
                    synthetic = OrderIntent.create(cycle.symbol, "BUY" if str(row.get("side", "")).upper() in {"BUY", "NB"} else "SELL", int(previous.get("filled", 0)) or 100, "MARKET", execution_mode="REAL", source="EXTERNAL_DNSE", trade_id=cycle.id)
                    synthetic.status = "FILLED"
                    self.database.add_event({"event_id": f"external-cost:{cycle.id}:{broker_id}:{previous.get('filled')}:{cost}", "ts": time.time(), "intent": synthetic.to_dict(), "queue_status": "FILLED",
                        "result": {"ok": True, "status": "COST_ADJUSTMENT", "order_id": broker_id, "message": "BROKER_COST_RECONCILED", "raw": {"fee": delta, "tax": 0}}})
        self.flush_events()

    def reconcile_external_sells(
        self,
        positions: list[dict[str, Any]],
        broker_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Sync opted-in Deals, including BUY/SELL outside Viking.

        A successful quantity snapshot alone never invents a fill or its price.
        Cumulative order progress is consumed once per ID, not once per poll.
        """
        if not self.trade_state or not isinstance(positions, list):
            return []
        self._reconcile_external_costs(broker_orders)
        results = []
        own_ids = self.database.owned_order_ids() | {value for item in self.queue.list_all() for value in
                   [item.broker_order_id, *item.broker_order_ids] if value}
        for cycle in self.trade_state.list_cycles():
            if cycle.execution_mode != "REAL" or cycle.status != "OPEN":
                continue
            latest_cycle_snapshot = cycle.to_dict()
            if time.time() - cycle.opened_at < 15:
                continue
            matching = [row for row in positions if str(row.get("symbol", "")).upper() == cycle.symbol]
            if not cycle.loan_package_id and isinstance(self.real, DNSEClient):
                try:
                    cycle.loan_package_id = str(self.real.cash_package(cycle.symbol)["id"])
                except Exception as exc:
                    results.append({"status": "RECONCILE_REQUIRED", "symbol": cycle.symbol, "reason": str(exc)})
                    continue
            if cycle.loan_package_id:
                matching = [row for row in matching if str(row.get("loanPackageId", "")) == cycle.loan_package_id]
            if len(matching) > 1:
                results.append({"status": "RECONCILE_REQUIRED", "symbol": cycle.symbol, "reason": "Không xác định duy nhất Deal được quản lý"})
                continue
            if matching:
                cycle.deal_id = str(matching[0].get("id", matching[0].get("positionId", cycle.deal_id)) or cycle.deal_id)
            actual = sum(position_quantity(row) for row in matching)
            # Unknown local hand-offs must first be bound/reconciled. They are
            # not reclassified as somebody else's external trade.
            if any(item.execution_mode == "REAL" and item.symbol == cycle.symbol
                   and item.status in {"SENDING", "UNKNOWN"}
                   for item in self.queue.list_all()):
                continue
            orders = list(broker_orders)
            if actual != cycle.open_quantity and hasattr(self.real, "get_order_history"):
                start_date = time.strftime("%Y-%m-%d", time.localtime(cycle.opened_at))
                end_date = time.strftime("%Y-%m-%d")
                cache_key = (start_date, end_date)
                cache = getattr(self, "_external_histories", {})
                cached_at, cached_rows = cache.get(cache_key, (0, []))
                if time.time() - cached_at >= 15:
                    try:
                        cached_rows = list(self.real.get_order_history(start_date, end_date) or [])
                    except Exception:
                        cached_rows = []
                    cache[cache_key] = (time.time(), cached_rows)
                    self._external_histories = cache
                orders.extend(cached_rows)
            unique = {}
            for row in orders:
                broker_id = str(row.get("orderId", row.get("id", "")) or "")
                if not broker_id or broker_id in own_ids or str(row.get("remark", "")).startswith("V2:"):
                    continue
                if str(row.get("symbol", "")).upper() != cycle.symbol:
                    continue
                if cycle.loan_package_id and str(row.get("loanPackageId", "")) != cycle.loan_package_id:
                    continue
                from .validation import timestamp_value
                observed = timestamp_value(row.get("modifiedDate", row.get("updatedAt", row.get("createdDate", row.get("createdAt")))))
                if not observed or observed < cycle.opened_at:
                    continue
                previous_row = unique.get(broker_id)
                if not previous_row or int(row.get("fillQuantity", 0) or 0) > int(previous_row.get("fillQuantity", 0) or 0):
                    unique[broker_id] = row
            changes = []
            projected = cycle.open_quantity
            progress = dict(cycle.external_progress)
            for broker_id, row in sorted(unique.items(), key=lambda item: timestamp_value(item[1].get("modifiedDate", item[1].get("createdDate")))):
                previous = progress.get(broker_id, {})
                filled = int(row.get("fillQuantity", row.get("filledQuantity", 0)) or 0)
                delta = filled - int(previous.get("filled", 0))
                if delta <= 0:
                    continue
                side = str(row.get("side", "")).upper()
                if side not in {"NB", "BUY", "NS", "SELL"}:
                    continue
                notional = self.queue._broker_notional(row)
                delta_notional = notional - float(previous.get("notional", 0))
                if delta_notional <= 0:
                    continue
                fee, tax = self.queue._broker_costs(row)
                delta_cost = max(0, fee + tax - float(previous.get("cost", 0)))
                is_buy = side in {"NB", "BUY"}
                projected += delta if is_buy else -delta
                changes.append((broker_id, row, delta, delta_notional / delta / 1000, delta_cost, is_buy))
                progress[broker_id] = {"filled": filled, "notional": notional, "cost": fee + tax}
            if projected != actual or projected < 0:
                results.append({"status": "RECONCILE_REQUIRED", "symbol": cycle.symbol,
                                "quantity": actual, "trade_id": cycle.id,
                                "reason": "Số cổ và lịch sử fill chưa đối soát được; không đoán giao dịch"})
                continue
            if not changes:
                if matching:
                    from .portfolio import position_cost
                    broker_cost = position_cost(matching[0])
                    if broker_cost > 0:
                        cycle.sync_remaining_cost(broker_cost)
                    with self.database:
                        current = self.trade_state.get(cycle.id)
                        if current and current.to_dict() == latest_cycle_snapshot:
                            self.trade_state.save(cycle)
                continue
            events = []
            with self.database:
                latest = self.trade_state.get(cycle.id)
                if not latest or latest.to_dict() != latest_cycle_snapshot:
                    continue
                cycle.external_progress = progress
                for broker_id, row, delta, price, cost, is_buy in changes:
                    if is_buy:
                        cycle.record_buy_fill(delta, price, cost)
                    else:
                        if delta > cycle.open_quantity:
                            raise RuntimeError("External SELL exceeds managed Deal")
                        cycle.record_sell_fill(delta, price, cost)
                        cycle.mark_exit_once("EXTERNAL_SELL")
                    synthetic = OrderIntent.create(cycle.symbol, "BUY" if is_buy else "SELL", delta,
                                                   "MARKET", execution_mode="REAL", source="EXTERNAL_DNSE",
                                                   trade_id=cycle.id, action="OPEN" if is_buy else "CLOSE")
                    synthetic.loan_package_id, synthetic.deal_id = cycle.loan_package_id, cycle.deal_id
                    synthetic.broker_order_id = broker_id
                    synthetic.details["fill_price"] = price
                    synthetic.status, synthetic.filled_quantity, synthetic.remaining_quantity = "FILLED", delta, 0
                    event_id = f"external:{cycle.id}:{broker_id}:{progress[broker_id]['filled']}"
                    events.append({"event_id": event_id, "ts": time.time(), "intent": synthetic.to_dict(),
                                   "queue_status": "FILLED", "result": {"ok": True, "status": "FILLED", "order_id": broker_id,
                                   "message": "EXTERNAL_FILL_RECONCILED", "error": "", "status_code": 200,
                                   "raw": {**row, "fillQuantity": delta, "averagePrice": price * 1000, "fee": cost, "tax": 0}}})
                # A later external BUY must not inherit an intermediate CLOSED
                # marker from an earlier SELL in the same reconciliation batch.
                cycle.status = "OPEN" if cycle.open_quantity else "CLOSED"
                if cycle.status == "OPEN":
                    cycle.closed_at = 0
                if matching:
                    from .portfolio import position_cost
                    broker_cost = position_cost(matching[0])
                    if broker_cost > 0:
                        cycle.sync_remaining_cost(broker_cost)
                self.trade_state.save(cycle)
                for event in events:
                    self.database.add_event(event)
                if any(not change[-1] for change in changes):
                    for intent in self.queue.list_all():
                        if intent.trade_id == cycle.id and intent.side == "SELL" and intent.status in {"PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT", "PAUSED"}:
                            self.queue.cancel_local(intent.id)
                    seconds = float(self.manual_sell_pause_seconds_provider() or 0) if self.manual_sell_pause_seconds_provider else 0
                    if self.rule_state and seconds > 0:
                        self.rule_state.start_entry_pause("REAL", seconds, reason="EXTERNAL_SELL", symbol=cycle.symbol)
                        self.cancel_unsubmitted_bot_buys("REAL", reason="Khóa BUY sau SELL ngoài DNSE")
            self.flush_events()
            for event in events:
                synthetic = OrderIntent.from_dict(event["intent"])
                self._emit_trade_event("EXTERNAL_BUY" if synthetic.side == "BUY" else "EXTERNAL_SELL", cycle, synthetic)
            results.append({"status": "RECONCILED", "symbol": cycle.symbol, "quantity": sum(change[2] for change in changes), "remaining_quantity": cycle.open_quantity, "trade_id": cycle.id})
        return results
