from __future__ import annotations

from viking_v2.connections.dnse.paper import PaperBroker
from viking_v2.trading.execution import ExecutionService
from viking_v2.models import OrderIntent
from viking_v2.trading.orders import OrderQueue
from viking_v2.storage import CSVOrderJournal, DailyFeeTracker, JSONLineJournal
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.state import TradeStateStore


def test_market_cache_can_release_in_ato_or_pass_to_open(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    ato = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", allow_ato=True))
    normal = queue.add(OrderIntent.create("VNM", "BUY", 100, "MARKET"))
    assert [item.id for item in queue.claim_due(phase="ATO", execution_mode="PAPER", token_ready=True)] == [ato.id]
    assert queue.get(normal.id).status == "PENDING"
    assert [item.id for item in queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)] == [normal.id]


def test_daily_fee_tracker_rolls_by_date_and_manual_reset_keeps_csv(tmp_path):
    history = CSVOrderJournal(tmp_path / "order_history.csv")
    tracker = DailyFeeTracker(history.path, tmp_path / "daily_stats.json")
    today = 1_786_500_000.0
    yesterday = today - 86_400

    def record(timestamp, mode, fee, tax=0):
        history.append_event({
            "ts": timestamp,
            "intent": {"execution_mode": mode, "symbol": "FPT"},
            "result": {"raw": {"fee": fee, "tax": tax}},
        })

    record(yesterday, "PAPER", 999)
    record(today, "PAPER", 100, 10)
    record(today, "REAL", 50)
    assert tracker.total("PAPER", today + 1) == 110
    assert tracker.total("REAL", today + 1) == 50

    tracker.reset("PAPER", today + 2)
    assert tracker.total("PAPER", today + 3) == 0
    record(today + 4, "PAPER", 20, 2)
    assert tracker.total("PAPER", today + 5) == 22
    assert len(history.read_all()) == 4
    assert tracker.total("PAPER", today + 86_400) == 0


def test_local_lo_only_releases_when_realtime_quote_reaches_limit(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(
        OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, wait_for_trigger=True)
    )
    assert queue.claim_due(
        phase="OPEN",
        execution_mode="PAPER",
        token_ready=True,
        quote_provider=lambda _symbol: {"ask": 101, "health": "OK"},
    ) == []
    assert queue.claim_due(
        phase="OPEN",
        execution_mode="PAPER",
        token_ready=True,
        quote_provider=lambda _symbol: {"ask": 99.9, "health": "OK"},
    )[0].id == intent.id


def test_local_lo_never_triggers_from_stale_quote(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=100, wait_for_trigger=True))
    assert queue.claim_due(
        phase="OPEN",
        execution_mode="PAPER",
        token_ready=True,
        quote_provider=lambda _symbol: {"bid": 101, "stale": True},
    ) == []


def test_unique_buy_intent_survives_repeated_signal(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    first = queue.add_unique(OrderIntent.create("FPT", "BUY", 100, "MARKET", source="BOT"))
    repeated = queue.add_unique(OrderIntent.create("FPT", "BUY", 200, "MARKET", source="BOT"))
    assert repeated.id == first.id
    assert len(queue.find_active("FPT", side="BUY", execution_mode="PAPER")) == 1


def test_sell_waits_for_t2_and_executes_sequentially(tmp_path):
    now = [1_700_000_000.0]
    broker = PaperBroker(
        tmp_path / "paper.json",
        tick_provider=lambda _symbol: {"price": 100, "ask": 100, "bid": 99},
        now=lambda: now[0],
    )
    buy = broker.place_order(OrderIntent.create("FPT", "BUY", 200, "MARKET"))
    assert buy.ok
    state = broker.store.read()
    state["positions"][0]["tradeQuantity"] = 100
    broker.store.write(state)

    queue = OrderQueue(tmp_path / "orders.json")
    sell = queue.add(OrderIntent.create("FPT", "SELL", 200, "MARKET", action="CLOSE"))
    service = ExecutionService(object(), broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"))
    service.process_due(phase="OPEN", execution_mode="PAPER")
    pending = queue.get(sell.id)
    assert pending.status == "WAITING_SETTLEMENT"
    assert pending.filled_quantity == 100
    assert pending.remaining_quantity == 100

    state = broker.store.read()
    state["positions"][0]["tradeQuantity"] = 100
    broker.store.write(state)
    service.process_due(phase="OPEN", execution_mode="PAPER")
    done = queue.get(sell.id)
    assert done.status == "FILLED"
    assert done.filled_quantity == 200
    assert broker.get_positions() == []
    csv_rows = CSVOrderJournal(tmp_path / "order_history.csv").read_all()
    assert len(csv_rows) == 2
    assert csv_rows[-1]["symbol"] == "FPT"
    assert csv_rows[-1]["execution_mode"] == "PAPER"
    # The audit row must describe the persisted queue state, not the temporary
    # slice sent to the broker.  Otherwise History renders a FILLED order as
    # "0 filled / quantity remaining" after a partial T+ release.
    assert int(csv_rows[-1]["filled_quantity"]) == 200
    assert int(csv_rows[-1]["remaining_quantity"]) == 0


def test_real_sell_fails_closed_when_dnse_omits_trade_quantity(tmp_path):
    class RealWithoutSellableQuantity:
        @staticmethod
        def has_trading_token():
            return True

        @staticmethod
        def get_positions(*, force=False):
            return [{"symbol": "FPT", "openQuantity": 100}]

        @staticmethod
        def place_order(_intent):
            raise AssertionError("REAL SELL must not be sent without tradeQuantity")

    paper = PaperBroker(tmp_path / "paper-real-guard.json")
    queue = OrderQueue(tmp_path / "orders-real-guard.json")
    sell = queue.add(OrderIntent.create(
        "FPT", "SELL", 100, "MARKET", execution_mode="REAL", action="CLOSE",
    ))
    service = ExecutionService(
        RealWithoutSellableQuantity(), paper, queue,
        JSONLineJournal(tmp_path / "journal-real-guard.jsonl"),
    )
    assert service.process_due(phase="OPEN", execution_mode="REAL") == []
    assert queue.get(sell.id).status == "WAITING_SETTLEMENT"


def test_trade_cycle_reentry_loss_lock_and_win_reset(tmp_path):
    store = TradeStateStore(tmp_path / "trades.json")
    for _index in range(3):
        cycle = store.create("FPT", "PAPER")
        store.record_buy_fill(cycle.id, 100, 100)
        closed = store.record_sell_fill(cycle.id, 100, 99)
        assert closed and closed.outcome == "LOSS"
    assert store.loss_streak("FPT", "PAPER") == 3
    assert store.is_loss_locked("FPT", "PAPER")

    # Operator/backtest may unlock later; a WIN always resets the cycle counter.
    cycle = store.create("FPT", "PAPER")
    assert cycle.is_reentry
    store.record_buy_fill(cycle.id, 100, 100)
    closed = store.record_sell_fill(cycle.id, 100, 101)
    assert closed and closed.outcome == "WIN"
    assert store.loss_streak("FPT", "PAPER") == 0


def test_trade_loss_lock_expires_after_24_wall_clock_hours(tmp_path):
    store = TradeStateStore(tmp_path / "trades-expiry.json")
    base = 1_800_000_000.0
    for index in range(3):
        cycle = store.create("FPT", "PAPER")
        store.record_buy_fill(cycle.id, 100, 100)
        store.record_sell_fill(cycle.id, 100, 99, closed_at=base + index)

    lock_started = base + 2
    assert store.is_loss_locked(
        "FPT", "PAPER", lock_hours=24, now=lock_started + 24 * 3600 - 1,
    )
    assert not store.is_loss_locked(
        "FPT", "PAPER", lock_hours=24, now=lock_started + 24 * 3600,
    )
    assert store.loss_streak("FPT", "PAPER") == 0


def test_exit_event_is_persistent_and_one_shot(tmp_path):
    store = TradeStateStore(tmp_path / "trades.json")
    cycle = store.create("FPT", "PAPER")
    assert store.mark_exit_once(cycle.id, "NORMAL_PROTECTION")
    assert not store.mark_exit_once(cycle.id, "NORMAL_PROTECTION")
    assert store.get(cycle.id).exit_events == ["NORMAL_PROTECTION"]


def _paper_with_unsettled_position(tmp_path):
    broker = PaperBroker(
        tmp_path / "paper-t2.json",
        tick_provider=lambda _symbol: {"price": 100, "ask": 100, "bid": 99},
    )
    assert broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET")).ok
    state = broker.store.read()
    state["positions"][0]["tradeQuantity"] = 0
    broker.store.write(state)
    return broker


def test_t2_recheck_cancels_rule_sell_when_condition_is_gone(tmp_path):
    broker = _paper_with_unsettled_position(tmp_path)
    queue = OrderQueue(tmp_path / "orders-recheck.json")
    rule_state = RuleStateStore(tmp_path / "rule-recheck.json")
    assert rule_state.claim_signal("FPT", "SELL", "D1", stream="PAPER")
    sell = queue.add(OrderIntent.create(
        "FPT", "SELL", 100, "MARKET", action="CLOSE", source="EM",
        reason="INDICATOR_EXIT", sell_wait_policy="RECHECK", signal="SELL", candle_key="D1",
    ))
    service = ExecutionService(
        object(), broker, queue, JSONLineJournal(tmp_path / "journal-recheck.jsonl"),
        rule_state=rule_state,
        sell_decision_provider=lambda _symbol, _mode: {"action": "WAIT", "event": "", "details": {}},
    )
    service.process_due(phase="OPEN", execution_mode="PAPER")
    assert queue.get(sell.id).status == "WAITING_SETTLEMENT"
    state = broker.store.read()
    state["positions"][0]["tradeQuantity"] = 100
    broker.store.write(state)
    service.process_due(phase="OPEN", execution_mode="PAPER")
    assert queue.get(sell.id).status == "CANCELLED"
    assert rule_state.claim_signal("FPT", "SELL", "D1", stream="PAPER") is True


def test_t2_keep_policy_sells_when_stock_arrives_without_rechecking(tmp_path):
    broker = _paper_with_unsettled_position(tmp_path)
    queue = OrderQueue(tmp_path / "orders-keep.json")
    sell = queue.add(OrderIntent.create(
        "FPT", "SELL", 100, "MARKET", action="CLOSE", source="EM",
        reason="STOP_LOSS", sell_wait_policy="KEEP",
    ))
    service = ExecutionService(
        object(), broker, queue, JSONLineJournal(tmp_path / "journal-keep.jsonl"),
        sell_decision_provider=lambda _symbol, _mode: {"action": "WAIT"},
    )
    service.process_due(phase="OPEN", execution_mode="PAPER")
    state = broker.store.read()
    state["positions"][0]["tradeQuantity"] = 100
    broker.store.write(state)
    service.process_due(phase="OPEN", execution_mode="PAPER")
    assert queue.get(sell.id).status == "FILLED"
