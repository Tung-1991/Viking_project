from __future__ import annotations

import pytest

from viking_v2.models import BrokerOrderResult, OrderIntent
from viking_v2.trading.orders import OrderQueue


@pytest.mark.parametrize(
    ("order_type", "eligible", "blocked"),
    [
        ("MARKET", "OPEN", ["CLOSED", "ATO", "LUNCH", "ATC"]),
        ("LO", "OPEN", ["CLOSED", "ATO", "LUNCH", "ATC"]),
        ("ATO", "ATO", ["CLOSED", "OPEN", "LUNCH", "ATC"]),
        ("ATC", "ATC", ["CLOSED", "ATO", "OPEN", "LUNCH"]),
    ],
)
def test_order_types_release_only_in_exact_phase(tmp_path, order_type, eligible, blocked):
    for index, phase in enumerate(blocked):
        queue = OrderQueue(tmp_path / f"{order_type}-{index}.json")
        queue.add(OrderIntent.create("FPT", "BUY", 100, order_type, limit_price=100 if order_type == "LO" else 0))
        assert queue.claim_due(phase=phase, execution_mode="PAPER", token_ready=True) == []
    queue = OrderQueue(tmp_path / f"{order_type}-eligible.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, order_type, limit_price=100 if order_type == "LO" else 0))
    due = queue.claim_due(phase=eligible, execution_mode="PAPER", token_ready=True)
    assert [row.id for row in due] == [intent.id]


def test_real_order_waits_for_token_without_being_sent(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    assert queue.claim_due(phase="OPEN", execution_mode="REAL", token_ready=False) == []
    assert queue.get(intent.id).status == "WAITING_TOKEN"
    assert queue.claim_due(phase="OPEN", execution_mode="REAL", token_ready=True)[0].status == "SENDING"


def test_claim_is_atomic_and_prevents_duplicate_send(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET"))
    assert len(queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)) == 1
    assert queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True) == []


def test_order_expires_after_24_hours(tmp_path):
    now = [1000.0]
    queue = OrderQueue(tmp_path / "orders.json", now=lambda: now[0])
    intent = OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=99)
    intent.created_at = now[0]
    intent.expires_at = now[0] + 86400
    queue.add(intent)
    now[0] += 86401
    assert queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True) == []
    assert queue.get(intent.id).status == "EXPIRED"


def test_sell_waiting_for_t2_does_not_expire_after_24_hours(tmp_path):
    now = [1000.0]
    queue = OrderQueue(tmp_path / "orders-settlement.json", now=lambda: now[0])
    intent = OrderIntent.create("FPT", "SELL", 100, "MARKET")
    intent.created_at = now[0]
    intent.expires_at = now[0] + 86400
    intent.status = "WAITING_SETTLEMENT"
    intent.settlement_waited = True
    queue.add(intent)
    now[0] += 4 * 86400
    assert queue.expire() == []
    assert queue.get(intent.id).status == "WAITING_SETTLEMENT"


def test_outside_session_cache_survives_tet_until_first_eligible_session(tmp_path):
    now = [1000.0]
    queue = OrderQueue(tmp_path / "orders.json", now=lambda: now[0])
    intent = OrderIntent.create("FPT", "BUY", 100, "MARKET", defer_expiry_until_eligible=True)
    intent.created_at = now[0]
    intent.expires_at = now[0] + 86400
    queue.add(intent)
    now[0] += 7 * 86400
    assert queue.claim_due(phase="HOLIDAY", execution_mode="PAPER", token_ready=True) == []
    assert queue.get(intent.id).status == "PENDING"
    assert queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)[0].id == intent.id


def test_unknown_transport_status_is_never_retried(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET"))
    claimed = queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)[0]
    queue.finish(claimed, BrokerOrderResult(False, "UNKNOWN", error="ORDER_STATUS_UNKNOWN"))
    assert queue.get(intent.id).status == "UNKNOWN"
    assert queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True) == []


def test_unknown_buy_still_blocks_duplicate_until_reconciled(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    original = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET"))
    queue._update(original.id, status="UNKNOWN", request_tag="V2:UNKNOWN:1")

    duplicate = OrderIntent.create("FPT", "BUY", 200, "MARKET")
    assert queue.add_unique(duplicate).id == original.id
    assert len(queue.list_all()) == 1


def test_real_and_paper_queue_state_are_separate(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    real = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    paper = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="PAPER"))
    due = queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)
    assert [row.id for row in due] == [paper.id]
    assert queue.get(real.id).status == "PENDING"


def test_queue_forgets_finished_orders_after_a_day_but_never_live_ones(tmp_path):
    """Every add rewrites the whole file, so the working set has to stay small.

    A finished order sticks around long enough for the dashboard to show it,
    then goes; order_history.csv and the journal keep the permanent record.
    """
    import time as _time
    from viking_v2.models import OrderIntent
    from viking_v2.trading.orders import OrderQueue

    clock = [_time.time()]
    queue = OrderQueue(tmp_path / "queue.json", now=lambda: clock[0])

    def put(order_id: str, status: str, hours_ago: float) -> None:
        intent = OrderIntent(
            id=order_id, symbol="HSG", side="BUY", quantity=100, order_type="MARKET",
        )
        intent.created_at = clock[0] - hours_ago * 3600
        intent.expires_at = clock[0] + 9999
        intent.status = status
        queue.add(intent)

    put("live-30h", "PENDING", 30)      # chưa xong, giữ bất kể tuổi
    put("done-1h", "FILLED", 1)         # vừa xong, dashboard còn cần
    put("done-20h", "CANCELLED", 20)
    put("done-30h", "FILLED", 30)       # quá một ngày, quên được
    put("done-70h", "EXPIRED", 70)

    queue.expire()
    kept = {order.id for order in queue.list_all()}
    assert kept == {"live-30h", "done-1h", "done-20h"}


def test_bot_off_blocks_cached_bot_buy_but_not_manual_buy_or_sell(tmp_path):
    queue = OrderQueue(tmp_path / "bot-off.json")
    bot_buy = queue.add(OrderIntent.create(
        "FPT", "BUY", 100, "MARKET", source="BOT",
    ))
    manual_buy = queue.add(OrderIntent.create(
        "MBB", "BUY", 100, "MARKET", source="MANUAL",
    ))
    sell = queue.add(OrderIntent.create(
        "VCB", "SELL", 100, "MARKET", source="EM", action="CLOSE",
    ))

    due = queue.claim_due(
        phase="OPEN", execution_mode="PAPER", token_ready=True,
        allow_bot_buys=False,
    )

    assert {item.id for item in due} == {manual_buy.id, sell.id}
    assert queue.get(bot_buy.id).status == "PENDING"


def test_cached_order_can_be_paused_resumed_edited_and_cancelled(tmp_path):
    queue = OrderQueue(tmp_path / "pause.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=10))

    assert queue.pause_local(intent.id).status == "PAUSED"
    assert queue.claim_due(
        phase="OPEN", execution_mode="PAPER", token_ready=True,
    ) == []
    assert queue.replace_local(intent.id, quantity=200, limit_price=10.5).quantity == 200
    assert queue.resume_local(intent.id).status == "PENDING"
    assert queue.pause_local(intent.id).status == "PAUSED"
    assert queue.cancel_local(intent.id).status == "CANCELLED"
