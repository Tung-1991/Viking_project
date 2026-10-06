from __future__ import annotations

from viking_v2.trading.execution import ExecutionService
from viking_v2.models import OrderIntent
from viking_v2.trading.orders import OrderQueue
from viking_v2.storage import CSVOrderJournal, JSONLineJournal
from viking_v2.trading.state import TradeStateStore
from viking_v2.rules.state import RuleStateStore


class Real:
    def __init__(self, orders):
        self.orders = orders
        self.calls = 0

    def get_orders(self, force=False):
        self.calls += 1
        return self.orders


def _working(queue, side="BUY", quantity=1000):
    intent = queue.add(OrderIntent.create("FPT", side, quantity, "MARKET", execution_mode="REAL"))
    return queue._update(
        intent.id,
        status="WORKING",
        broker_order_id="88",
        request_tag="V2:TAG:1",
        working_quantity=quantity,
    )


def test_partial_and_final_fill_are_applied_as_deltas(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = _working(queue)
    real = Real([{
        "orderId": "88", "orderStatus": "Partially Filled", "fillQuantity": 400,
        "fee": 40, "tax": 4,
        "averagePrice": 100_000,
    }])
    service = ExecutionService(real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    assert service.reconcile_working("REAL")[0].filled_quantity == 400
    real.orders[0].update(orderStatus="Filled", fillQuantity=1000, fee=100, tax=10)
    done = service.reconcile_working("REAL")[0]
    assert done.status == "FILLED"
    assert done.filled_quantity == 1000
    rows = CSVOrderJournal(tmp_path / "order_history.csv").read_all()
    assert [float(row["fee"]) for row in rows] == [40, 60]
    assert [float(row["tax"]) for row in rows] == [4, 6]


def test_reconcile_does_not_call_dnse_without_active_local_order(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    real = Real([])
    service = ExecutionService(real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    assert service.reconcile_working("REAL") == []
    assert real.calls == 0


def test_partial_exit_fill_marks_protection_event_immediately(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    trades = TradeStateStore(tmp_path / "trades.json")
    cycle = trades.create("FPT", "REAL", trade_id="T1", em_modes=["NORMAL"])
    trades.record_buy_fill(cycle.id, 1_000, 100)
    intent = queue.add(
        OrderIntent.create(
            "FPT", "SELL", 300, "MARKET", execution_mode="REAL",
            source="EM", trade_id="T1", action="CLOSE",
            reason="NORMAL_PROTECTION",
        )
    )
    queue._update(intent.id, status="WORKING", broker_order_id="88", working_quantity=300)
    real = Real([{
        "orderId": "88", "orderStatus": "Partially Filled",
        "fillQuantity": 100, "averagePrice": 107,
    }])
    rule_state = RuleStateStore(tmp_path / "rule.json")
    rule_state.update_position_metrics("FPT", "T1", profit_pct=7.0)
    service = ExecutionService(
        real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"),
        trade_state=trades, rule_state=rule_state,
    )
    service.reconcile_working("REAL")
    assert "NORMAL_PROTECTION" in trades.get("T1").exit_events
    assert rule_state.position_metrics("FPT", "T1")["normal_protection_done"] is True


def test_cancelled_sell_keeps_fill_but_never_requeues_remainder(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = _working(queue, side="SELL", quantity=1000)
    real = Real([{"orderId": "88", "orderStatus": "Cancelled", "fillQuantity": 300, "averagePrice": 100_000}])
    service = ExecutionService(real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    pending = service.reconcile_working("REAL")[0]
    assert pending.status == "CANCELLED"
    assert pending.filled_quantity == 300
    assert pending.remaining_quantity == 700
    assert pending.broker_order_id == "88"
    assert pending.request_tag == "V2:TAG:1"
    assert pending.attempt == 1


def test_unknown_submission_is_recovered_when_request_tag_appears(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    queue._update(intent.id, status="UNKNOWN", request_tag="V2:ABC:1")
    real = Real([{"orderId": "99", "orderStatus": "New", "fillQuantity": 0, "remark": "V2:ABC:1"}])
    service = ExecutionService(real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    recovered = service.reconcile_working("REAL")[0]
    assert recovered.status == "WORKING"


def test_manual_management_metadata_survives_real_partial_fill_and_restart(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(
        OrderIntent.create(
            "FPT", "BUY", 1000, "MARKET", execution_mode="REAL",
            source="MANUAL", trade_id="TRADE-1", em_modes=["NORMAL", "IND_EXIT"],
            sl_mode="PERCENT", sl_value=-4,
        )
    )
    queue._update(
        intent.id, status="WORKING", broker_order_id="88",
        request_tag="V2:MANUAL:1", working_quantity=1000,
    )
    real = Real([{
        "orderId": "88", "orderStatus": "Partially Filled", "fillQuantity": 400,
        "averagePrice": 100, "fee": 40,
    }])
    path = tmp_path / "trades.json"
    service = ExecutionService(
        real, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"),
        trade_state=TradeStateStore(path),
    )
    service.reconcile_working("REAL")

    cycle = TradeStateStore(path).get("TRADE-1")
    assert cycle is not None
    assert cycle.open_quantity == 400
    assert cycle.em_modes == ["NORMAL", "IND_EXIT"]
    assert cycle.sl_mode == "PERCENT"
    assert cycle.sl_value == -4


def test_trade_events_emit_once_on_first_bot_fill_and_final_close(tmp_path):
    queue = OrderQueue(tmp_path / "orders-events.json")
    trades = TradeStateStore(tmp_path / "trades-events.json")
    events = []
    buy = queue.add(OrderIntent.create(
        "FPT", "BUY", 200, "MARKET", execution_mode="REAL",
        source="BOT", trade_id="BOT-1",
    ))
    queue._update(buy.id, status="WORKING", broker_order_id="BUY-1", working_quantity=200)
    real = Real([{
        "orderId": "BUY-1", "orderStatus": "Partially Filled",
        "fillQuantity": 100, "averagePrice": 69.2,
    }])
    service = ExecutionService(
        real, object(), queue, JSONLineJournal(tmp_path / "journal-events.jsonl"),
        trade_state=trades,
        trade_event_callback=lambda event, cycle, intent: events.append((event, cycle.open_quantity)),
    )
    service.reconcile_working("REAL")
    real.orders[0].update(orderStatus="Filled", fillQuantity=200)
    service.reconcile_working("REAL")
    assert events == [("OPEN", 100)]

    sell = queue.add(OrderIntent.create(
        "FPT", "SELL", 200, "MARKET", execution_mode="REAL",
        source="EM", trade_id="BOT-1", action="CLOSE", reason="INDICATOR_EXIT",
    ))
    queue._update(sell.id, status="WORKING", broker_order_id="SELL-1", working_quantity=200)
    real.orders[:] = [{
        "orderId": "SELL-1", "orderStatus": "Partially Filled",
        "fillQuantity": 100, "averagePrice": 74.0,
    }]
    service.reconcile_working("REAL")
    assert events == [("OPEN", 100)]
    real.orders[0].update(orderStatus="Filled", fillQuantity=200)
    service.reconcile_working("REAL")
    assert events[-1] == ("CLOSED", 0)
    assert [event for event, _quantity in events] == ["OPEN", "CLOSED"]
    assert trades.get("BOT-1").avg_exit_price == 74.0


def test_external_dnse_sell_is_reconciled_without_stopping_remaining_trade(tmp_path):
    queue = OrderQueue(tmp_path / "orders-external.json")
    trades = TradeStateStore(tmp_path / "trades-external.json")
    rules = RuleStateStore(tmp_path / "rules-external.json")
    cycle = trades.create("FPT", "REAL", source="BOT", trade_id="BOT-EXT")
    cycle.opened_at = 1
    trades.save(cycle)
    trades.record_buy_fill(cycle.id, 1_000, 100.0)
    pending_buy = queue.add(OrderIntent.create(
        "MBB", "BUY", 100, "MARKET", execution_mode="REAL", source="BOT",
    ))
    events = []
    service = ExecutionService(
        Real([]), object(), queue, JSONLineJournal(tmp_path / "journal-external.jsonl"),
        trade_state=trades, rule_state=rules,
        trade_event_callback=lambda event, current, intent: events.append(
            (event, current.open_quantity, intent.source)
        ),
        manual_sell_pause_seconds_provider=lambda: 900,
    )
    broker_order = {
        "orderId": "APP-SELL-1", "symbol": "FPT", "side": "NS",
        "orderStatus": "Filled", "fillQuantity": 400,
        "averagePrice": 105_000, "fee": 10_000, "tax": 5_000,
        "remark": "",
        "createdDate": "2026-10-05T03:00:00Z",
    }

    reconciled = service.reconcile_external_sells(
        [{"symbol": "FPT", "openQuantity": 600}], [broker_order],
    )

    assert reconciled[0]["status"] == "RECONCILED"
    assert reconciled[0]["remaining_quantity"] == 600
    current = trades.get("BOT-EXT")
    assert current.open_quantity == 600
    assert "EXTERNAL_SELL" in current.exit_events
    assert events == [("EXTERNAL_SELL", 600, "EXTERNAL_DNSE")]
    assert queue.get(pending_buy.id).status == "CANCELLED"
    assert rules.entry_pause("REAL")["active"] is True
    assert service.reconcile_external_sells(
        [{"symbol": "FPT", "openQuantity": 600}], [broker_order],
    ) == []
