from __future__ import annotations

from viking_v2.models import OrderIntent
from viking_v2.trading.orders import OrderQueue


def test_broker_cancel_is_distinct_from_local_cancel(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="88")
    assert queue.cancel_local(intent.id) is None
    cancelled = queue.mark_broker_cancelled(intent.id, "USER_CANCELLED_DNSE")
    assert cancelled.status == "CANCELLED"
    assert cancelled.result == "USER_CANCELLED_DNSE"


def test_cached_lo_can_be_replaced_before_submission(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=99))
    updated = queue.replace_local(intent.id, quantity=200, limit_price=101)
    assert updated is not None
    assert updated.quantity == 200
    assert updated.remaining_quantity == 200
    assert updated.limit_price == 101


def test_working_order_cannot_be_replaced_as_local_cache(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=99))
    queue._update(intent.id, status="WORKING", broker_order_id="88")
    assert queue.replace_local(intent.id, quantity=200, limit_price=101) is None
