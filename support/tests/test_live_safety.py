"""Money-path regressions. Run through support/tools/run_offline.py."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import threading
import time

import pytest
import requests

from viking_v2.connections.dnse.client import DNSEClient, BrokerSnapshotError
from viking_v2.models import OrderIntent, BrokerOrderResult, TradeCycle, StrategyDecision
from viking_v2.rules.business import StaticRuleParameters, advance_buy_confirmation
from viking_v2.rules.state import RuleStateStore
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.storage import JSONLineJournal, CSVOrderJournal
from viking_v2.trading.durable import DurableJSONStore, StateCorruptionError, AccountLease
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore
from viking_v2.trading.portfolio import cash_from_balance
from viking_v2.trading.validation import quote_is_fresh
from viking_v2.trading.market import VN_TZ
from viking_v2.connections.dnse.paper import PaperBroker
from viking_v2.trading.portfolio import position_cost, price_in_band, validate_quantity


class Broker:
    def __init__(self):
        self.sent, self.orders = [], []
        self.positions = [{"symbol": "FPT", "openQuantity": 300, "tradeQuantity": 300}]
        self.result = BrokerOrderResult(True, "New", order_id="B1", raw={"fillQuantity": 0})

    def has_trading_token(self): return True
    def get_positions(self, **kwargs): return deepcopy(self.positions)
    def get_orders(self, **kwargs): return deepcopy(self.orders)
    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        return self.result


def service(tmp_path, broker=None, **kwargs):
    broker = broker or Broker()
    queue = OrderQueue(tmp_path / "orders.json")
    trades = TradeStateStore(tmp_path / "trades.json")
    rules = RuleStateStore(tmp_path / "rules.json")
    engine = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"),
                              trade_state=trades, rule_state=rules, **kwargs)
    return engine, broker, queue, trades, rules


def test_fill_transaction_rolls_back_then_restart_recovers_without_second_post(tmp_path, monkeypatch):
    engine, broker, queue, trades, _ = service(tmp_path)
    broker.result = BrokerOrderResult(True, "Filled", order_id="B1", raw={"fillQuantity": 100, "averagePrice": 100_000})
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", trade_id="T1", execution_mode="REAL"))
    monkeypatch.setattr(engine, "_record_trade_fill", lambda *a, **k: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        engine.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "SENDING"
    assert trades.get("T1") is None
    restarted, _, _, _, _ = service(tmp_path, broker)
    assert queue.get(intent.id).status == "FILLED"
    assert trades.get("T1").open_quantity == 100
    restarted.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.sent) == 1
    assert len(CSVOrderJournal(tmp_path / "order_history.csv").read_all()) == 1


def test_partial_fill_uses_delta_notional_and_same_management_unit(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "MARKET", trade_id="T1", execution_mode="REAL", sl_value=-5, sl_mode="PERCENT"))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=200)
    broker.orders = [{"id": "B1", "symbol": "FPT", "orderStatus": "PartiallyFilled", "fillQuantity": 100, "averagePrice": 100_000}]
    engine.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 100
    assert trades.get("T1").sl_value == -5
    broker.orders[0].update(orderStatus="Filled", fillQuantity=200, averagePrice=110_000)
    engine.reconcile_working("REAL")
    assert trades.get("T1").avg_entry_price == 110
    engine.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 200
    assert len(trades.list_cycles()) == 1


def test_remaining_cost_after_sell_then_buy_and_late_buy_reopens():
    cycle = TradeCycle(id="T", symbol="FPT")
    cycle.record_buy_fill(8000, 100)
    cycle.record_buy_fill(2000, 120)
    cycle.record_sell_fill(4000, 110)
    cycle.record_buy_fill(2000, 80)
    assert cycle.open_quantity == 8000 and cycle.avg_entry_price == 98
    cycle.record_sell_fill(8000, 110)
    cycle.record_buy_fill(2000, 100)
    assert cycle.status == "OPEN" and cycle.open_quantity == 2000


def test_edit_partial_sell_preserves_total_filled_remaining(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "SELL", 10000, "MARKET"))
    queue._update(intent.id, status="WAITING_SETTLEMENT", filled_quantity=8000, remaining_quantity=2000)
    edited = queue.replace_local(intent.id, quantity=10000)
    assert (edited.quantity, edited.filled_quantity, edited.remaining_quantity) == (10000, 8000, 2000)
    assert queue.replace_local(intent.id, quantity=7000) is None


@pytest.mark.parametrize("status", ["WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"])
def test_local_ttl_never_forgets_broker_orders(tmp_path, status):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET"))
    queue._update(intent.id, status=status, expires_at=time.time()-1, broker_order_id="B1")
    assert queue.expire() == []
    assert queue.get(intent.id).status == status


@pytest.mark.parametrize("broker_status", ["Cancelled", "Rejected", "Expired", "DoneForDay"])
def test_cancelled_or_expired_sell_does_not_resurrect(tmp_path, broker_status):
    engine, broker, queue, _, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 300, "MARKET", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=300)
    broker.orders = [{"id": "B1", "orderStatus": broker_status, "fillQuantity": 100, "averagePrice": 100_000}]
    engine.reconcile_working("REAL")
    assert queue.get(intent.id).filled_quantity == 100
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent == []


def test_pending_cancel_still_records_late_fill(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL", trade_id="T1"))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=100)
    queue.mark_broker_cancelled(intent.id)
    assert queue.get(intent.id).status == "CANCEL_PENDING"
    broker.orders = [{"id": "B1", "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100_000}]
    engine.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 100
    assert queue.get(intent.id).status == "FILLED"


def test_off_before_second_handoff_cancels_only_bot_buy(tmp_path):
    enabled = {"value": True}
    engine, broker, queue, _, _ = service(tmp_path, bot_buy_allowed_provider=lambda mode: enabled["value"])
    for symbol in ("FPT", "VIX"):
        queue.add(OrderIntent.create(symbol, "BUY", 100, "MARKET", source="BOT", execution_mode="REAL"))
    original = broker.place_order
    def place(intent):
        result = original(intent)
        enabled["value"] = False
        return result
    broker.place_order = place
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert [item.symbol for item in broker.sent] == ["FPT"]
    assert queue.list_all()[1].status == "CANCELLED"


def test_unknown_keeps_pre_send_tag_and_recovers_late_order(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    broker.result = BrokerOrderResult(False, "UNKNOWN", error="ORDER_STATUS_UNKNOWN")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", trade_id="T1", execution_mode="REAL"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    persisted = queue.get(intent.id)
    assert persisted.request_tag and persisted.handed_off_at
    broker.orders = [{"id": "B1", "remark": persisted.request_tag, "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100_000}]
    engine.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 100
    assert len(broker.sent) == 1


def test_missing_recheck_waits_then_false_cancels_then_true_uses_managed_quantity(tmp_path):
    decision = {"value": None}
    engine, broker, queue, trades, _ = service(tmp_path, sell_decision_provider=lambda *args: decision["value"])
    trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", trade_id="T1", source="EM", execution_mode="REAL", reason="STOP_LOSS"))
    queue._update(intent.id, status="WAITING_SETTLEMENT", settlement_waited=True)
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert not broker.sent
    decision["value"] = {"action": "SELL", "event": "STOP_LOSS", "quantity_fraction": 1}
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent[0].quantity == 100


@pytest.mark.parametrize("settlement_waited", [False, True])
@pytest.mark.parametrize("decision_kind", ["missing", "false", "true"])
def test_recheck_after_waiting_token_requires_current_sell_decision(tmp_path, settlement_waited, decision_kind):
    token = {"ready": False}
    decision = {"value": {"action": "SELL", "event": "STOP_LOSS", "quantity_fraction": 1}}
    engine, broker, queue, trades, _ = service(tmp_path, sell_decision_provider=lambda *args: decision["value"])
    broker.has_trading_token = lambda: token["ready"]
    trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", trade_id="T1",
                                         source="EM", execution_mode="REAL", reason="STOP_LOSS"))
    if settlement_waited:
        queue._update(intent.id, status="WAITING_SETTLEMENT", settlement_waited=True)
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "WAITING_TOKEN"
    assert not broker.sent
    decision["value"] = {
        "missing": None,
        "false": {"action": "WAIT"},
        "true": {"action": "SELL", "event": "STOP_LOSS", "quantity_fraction": 1},
    }[decision_kind]
    token["ready"] = True
    engine.process_due(phase="OPEN", execution_mode="REAL")
    if decision_kind == "true":
        assert [item.quantity for item in broker.sent] == [100]
    else:
        assert not broker.sent
        assert queue.get(intent.id).status == ("CANCELLED" if decision_kind == "false" else "WAITING_SETTLEMENT")


@pytest.mark.parametrize("source,policy", [("EM", "KEEP"), ("MANUAL", "RECHECK")])
def test_waiting_token_preserves_keep_and_manual_sell_authority(tmp_path, source, policy):
    token = {"ready": False}
    engine, broker, queue, trades, _ = service(tmp_path, sell_decision_provider=lambda *args: None)
    broker.has_trading_token = lambda: token["ready"]
    trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", trade_id="T1", source=source,
                                         execution_mode="REAL", sell_wait_policy=policy, reason="STOP_LOSS"))
    queue._update(intent.id, status="WAITING_SETTLEMENT", settlement_waited=True)
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "WAITING_TOKEN"
    token["ready"] = True
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert [item.quantity for item in broker.sent] == [100]


def test_stale_or_wrong_symbol_quotes_never_authorize_buy(tmp_path):
    for tick in ({"symbol": "FPT", "timestamp": time.time()-3600, "price": 100},
                 {"symbol": "VIX", "timestamp": time.time(), "price": 100}):
        assert not quote_is_fresh(tick, "FPT")
        planner = StrategyOrderPlanner(OrderQueue(tmp_path / "orders.json"), TradeStateStore(tmp_path / "trades.json"), RuleStateStore(tmp_path / "rules.json"))
        result = planner.plan(StrategyDecision("BUY", "FPT", "ENTRY_BUY"), execution_mode="REAL", execution_style="MARKET", tick=tick, portfolio={"order_budget": 20_000_000, "available_cash": 50_000_000}, candle_key="C")
        assert result.intent is None


def test_confirmation_downtime_is_not_observed_time():
    params = StaticRuleParameters(buy_confirmation_enabled=True, buy_confirmation_minutes=5, buy_confirmation_require_ema=True, buy_confirmation_require_rsi=False)
    start = datetime(2026, 10, 5, 9, 5, tzinfo=VN_TZ)
    kwargs = dict(indicators={"buy_ema_fast": 2, "buy_ema_slow": 1}, exchange="HOSE", params=params, max_observation_gap_seconds=20)
    state, status, _ = advance_buy_confirmation({}, raw_trigger=True, observed_at=start, **kwargs)
    assert status == "WAITING"
    _, status, _ = advance_buy_confirmation(state, raw_trigger=False, observed_at=start+timedelta(minutes=30), **kwargs)
    assert status == "IDLE"


def test_continuous_confirmation_still_fires_without_caching_missed_signal():
    params = StaticRuleParameters(buy_confirmation_enabled=True, buy_confirmation_minutes=5, buy_confirmation_require_ema=True, buy_confirmation_require_rsi=False)
    start = datetime(2026, 10, 5, 9, 5, tzinfo=VN_TZ)
    state = {}
    for seconds in range(0, 301, 10):
        state, status, _ = advance_buy_confirmation(state, raw_trigger=seconds==0,
            observed_at=start+timedelta(seconds=seconds), indicators={"buy_ema_fast": 2, "buy_ema_slow": 1},
            exchange="HOSE", params=params, max_observation_gap_seconds=20)
    assert status == "CONFIRMED"


def test_restart_drops_candidates_but_preserves_cooldown_and_consumed_ids(tmp_path):
    rules = RuleStateStore(tmp_path / "rules.json")
    rules.start_entry_pause("REAL", 900)
    rules.claim_signal("FPT", "BUY", "OLD", "REAL")
    rules.save_buy_confirmation("FPT", "REAL", {"active": True})
    reopened = RuleStateStore(rules.store.path)
    reopened.discard_buy_candidates()
    assert reopened.buy_confirmation("FPT", "REAL") == {}
    assert reopened.entry_pause("REAL")["active"]
    assert not reopened.claim_signal("FPT", "BUY", "OLD", "REAL")


def test_financial_state_updates_are_serialized_across_instances(tmp_path):
    first = DurableJSONStore(tmp_path / "counter.json", default={"value": 0})
    second = DurableJSONStore(first.path, default={"value": 0})
    def increment(store):
        for _ in range(30):
            with store.transaction:
                value = store.read()
                value["value"] += 1
                store.write(value)
    workers = [threading.Thread(target=increment, args=(item,)) for item in (first, second)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert first.read()["value"] == 60


def test_legacy_import_is_backed_up_and_corruption_is_not_empty(tmp_path):
    legacy = tmp_path / "trades.json"
    legacy.write_text('{"cycles": []}', encoding="utf-8")
    store = DurableJSONStore(legacy, default={})
    assert store.read() == {"cycles": []}
    assert (tmp_path / "migration-backup" / "trades.json").read_text() == '{"cycles": []}'
    broken = tmp_path / "broken.json"
    broken.write_text('{broken', encoding="utf-8")
    with pytest.raises(StateCorruptionError): DurableJSONStore(broken, default={}).read()


def test_account_lifetime_lease_prevents_duplicate_instance(tmp_path):
    lease = AccountLease(tmp_path)
    try:
        with pytest.raises(RuntimeError): AccountLease(tmp_path)
    finally: lease.close()
    AccountLease(tmp_path).close()


def test_total_cash_is_not_usable_cash_and_zero_never_falls_back():
    assert cash_from_balance({"stock": {"totalCash": 100_000_000}}) == 0
    assert cash_from_balance({"stock": {"availableCash": 0}, "cash": 100_000_000}) == 0


def test_snapshot_failure_is_not_an_empty_account(monkeypatch):
    client = DNSEClient(api_key="FAKE", api_secret="FAKE", account_no="AUDIT")
    monkeypatch.setattr(client, "_request", lambda *a, **k: (False, None, 503, "down"))
    with pytest.raises(BrokerSnapshotError): client.get_positions(force=True)


def test_fee_rate_is_total_and_not_double_counted_with_exchange_fee():
    fee, tax = OrderQueue._broker_costs({"fillQuantity": 100, "averagePrice": 100_000, "feeRate": .0015, "exchangeFeeRate": .0003, "taxRate": 0})
    assert (fee, tax) == (15000, 0)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_mutations_never_retry_uncertain_transport(monkeypatch, method):
    class Session:
        calls = 0
        def request(self, *args, **kwargs):
            self.calls += 1
            raise requests.Timeout("accepted; reply lost")
    session = Session()
    client = DNSEClient(api_key="FAKE", api_secret="FAKE", account_no="AUDIT", session=session)
    assert not client._request(method, "/accounts/orders")[0]
    assert session.calls == 1


@pytest.mark.parametrize("data", [{}, {"positions": None}, {"positions": [None]}, {"positions": [], "total": 1}])
def test_invalid_positions_schema_never_becomes_empty_account(monkeypatch, data):
    client = DNSEClient(api_key="FAKE", api_secret="FAKE", account_no="AUDIT")
    monkeypatch.setattr(client, "_request", lambda *a, **k: (True, data, 200, ""))
    with pytest.raises(BrokerSnapshotError): client.get_positions(force=True)


def test_sub_thousand_vnd_price_is_not_misread_as_board_price(tmp_path, monkeypatch):
    client = DNSEClient(api_key="FAKE", api_secret="FAKE", account_no="AUDIT")
    monkeypatch.setattr(client, "_request", lambda *a, **k: (True, {"positions": [{"symbol": "FPT", "costPrice": 850}]}, 200, ""))
    assert position_cost(client.get_positions(force=True)[0]) == .85
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "LO", limit_price=.85, trade_id="T1", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=200)
    broker.orders = [{"id": "B1", "orderStatus": "PartiallyFilled", "fillQuantity": 100, "averagePrice": 850, "price_unit": "VND"}]
    engine.reconcile_working("REAL")
    broker.orders[0].update(orderStatus="Filled", fillQuantity=200, averagePrice=900)
    engine.reconcile_working("REAL")
    assert trades.get("T1").avg_entry_price == .9


def test_completed_order_fee_correction_is_logged_once_with_signed_delta(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", trade_id="T1", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=100)
    broker.orders = [{"id": "B1", "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100_000, "fee": 15000}]
    engine.reconcile_working("REAL")
    broker.orders[0]["fee"] = 10000
    engine.reconcile_working("REAL")
    engine.reconcile_working("REAL")
    assert trades.get("T1").fees_paid == 10000
    assert trades.get("T1").net_pnl == -10000
    rows = engine.csv_journal.read_all()
    assert len(rows) == 2 and sum(float(row["fee"]) for row in rows) == 10000


def test_incomplete_or_older_fill_snapshot_does_not_poison_recovery(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "MARKET", trade_id="T1", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="B1", working_quantity=200)
    broker.orders = [{"id": "B1", "orderStatus": "PartiallyFilled", "fillQuantity": 100, "averagePrice": 0}]
    engine.reconcile_working("REAL")
    engine.recover_results()
    assert trades.get("T1") is None
    broker.orders[0].update(averagePrice=100_000, fee=1000)
    engine.reconcile_working("REAL")
    broker.orders[0].update(fillQuantity=0, fee=0)
    engine.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 100 and trades.get("T1").fees_paid == 1000


def test_multiple_manual_buys_attach_to_one_deal_without_losing_fill(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    broker.result = BrokerOrderResult(True, "Filled", raw={"fillQuantity": 100, "averagePrice": 100_000})
    intents = [queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, trade_id=f"T{i}", source="MANUAL", execution_mode="REAL")) for i in range(2)]
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert len(trades.list_cycles()) == 1
    assert trades.list_cycles()[0].open_quantity == 200
    assert queue.get(intents[0].id).trade_id == queue.get(intents[1].id).trade_id


def test_off_runs_manual_buy_and_managed_sell_in_both_books(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path, bot_buy_allowed_provider=lambda mode: False)
    broker.result = BrokerOrderResult(True, "Filled", raw={"fillQuantity": 100, "averagePrice": 100_000})
    for mode in ("REAL", "PAPER"):
        cycle = trades.create("FPT", mode, trade_id=mode)
        trades.record_buy_fill(cycle.id, 100, 100)
        queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=100, trade_id=cycle.id, execution_mode=mode, source="EM", reason="STOP_LOSS"))
        queue.add(OrderIntent.create("VIX", "BUY", 100, "LO", limit_price=100, trade_id=mode+"BUY", execution_mode=mode, source="MANUAL"))
        queue.add(OrderIntent.create("HPG", "BUY", 100, "MARKET", execution_mode=mode, source="BOT"))
        engine.process_due(phase="OPEN", execution_mode=mode, allow_bot_buys=False)
        assert trades.get(cycle.id).status == "CLOSED"
    assert [(item.execution_mode, item.side) for item in broker.sent] == [("REAL", "SELL"), ("REAL", "BUY"), ("PAPER", "SELL"), ("PAPER", "BUY")]


def test_restart_drops_bot_but_keeps_manual_and_inflight(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    bot = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", source="BOT"))
    manual = queue.add(OrderIntent.create("VIX", "BUY", 100, "MARKET", source="MANUAL"))
    inflight = queue.add(OrderIntent.create("HPG", "BUY", 100, "MARKET", source="BOT"))
    queue._update(inflight.id, status="UNKNOWN", request_tag="V2:INFLIGHT")
    queue.discard_unsubmitted_bot_buys("restart")
    assert [queue.get(item.id).status for item in (bot, manual, inflight)] == ["CANCELLED", "PENDING", "UNKNOWN"]


def test_paper_handoff_crash_recovers_fill_without_resend(tmp_path):
    engine, _, queue, trades, _ = service(tmp_path)
    paper = PaperBroker(tmp_path / "paper.json", initial_balance=50_000_000)
    engine.paper = paper
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, execution_mode="PAPER", trade_id="T1"))
    claimed = queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)[0]
    queue._update(intent.id, handed_off_at=time.time(), request_tag="V2:PAPER")
    claimed.request_tag = "V2:PAPER"
    result = paper.place_order(claimed)
    assert paper.place_order(claimed).order_id == result.order_id
    queue.recover_claims()
    engine.reconcile_working("PAPER")
    assert trades.get("T1").open_quantity == 100
    assert len(paper.get_orders()) == 1


def test_external_buy_partial_and_full_sell_sync_only_opted_in_package(tmp_path):
    engine, broker, _, trades, _ = service(tmp_path)
    cycle = trades.create("FPT", "REAL", trade_id="T1", loan_package_id="1", sl_mode="PERCENT", sl_value=-5)
    trades.record_buy_fill(cycle.id, 100, 100)
    cycle = trades.get(cycle.id)
    cycle.opened_at = time.time()-120
    trades.save(cycle)
    def row(oid, side, quantity, price):
        return {"id": oid, "symbol": "FPT", "loanPackageId": "1", "side": side, "orderStatus": "Filled", "fillQuantity": quantity, "averagePrice": price, "createdAt": time.time()-30}
    orders = [row("X1", "NB", 100, 120_000), {**row("OTHER", "NB", 900, 10_000), "loanPackageId": "2"}]
    positions = [{"symbol": "FPT", "loanPackageId": "1", "openQuantity": 200, "tradeQuantity": 100, "costPrice": 110_000}]
    assert engine.reconcile_external_sells(positions, orders)[0]["status"] == "RECONCILED"
    assert trades.get("T1").avg_entry_price == 110 and trades.get("T1").sl_value == -5
    orders.append(row("X2", "NS", 100, 115_000))
    positions[0].update(openQuantity=100, tradeQuantity=100)
    engine.reconcile_external_sells(positions, orders)
    assert trades.get("T1").open_quantity == 100
    engine.reconcile_external_sells(positions, orders)
    assert trades.get("T1").sold_quantity == 100
    orders[-1].update(fillQuantity=200, averagePrice=117_500)
    engine.reconcile_external_sells([], orders)
    assert trades.get("T1").status == "CLOSED"
    assert trades.get("T1").avg_exit_price == 117.5
    assert trades.get("T1").net_pnl == 1_500_000


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 100.5, "wrong"])
def test_invalid_financial_numbers_cannot_be_tradable_quantity(value):
    assert not validate_quantity(value)[0]


def test_nan_price_never_passes_band_check():
    assert not price_in_band(float("nan"), 1, 100)


@pytest.mark.parametrize("new_first", [True, False])
def test_replaced_old_and_new_id_fill_once_and_finalize_only_when_both_resolve(tmp_path, new_first):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 10000, "LO", limit_price=100, trade_id="T1", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="OLD", working_quantity=10000)
    broker.orders = [{"id": "OLD", "orderStatus": "PartiallyFilled", "quantity": 10000, "fillQuantity": 8000, "averagePrice": 100_000}]
    engine.reconcile_working("REAL")
    queue.mark_broker_replaced(intent.id, quantity=10000, broker_quantity=2000, limit_price=120, broker_order_id="NEW")
    old = {**broker.orders[0], "orderStatus": "Cancelled"}
    new = {"id": "NEW", "orderStatus": "Filled", "quantity": 2000, "fillQuantity": 2000, "averagePrice": 120_000}
    broker.orders = [new, old] if new_first else [old, new]
    engine.reconcile_working("REAL")
    engine.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 10000 and trades.get("T1").avg_entry_price == 104
    final = queue.get(intent.id)
    assert final.status == "FILLED" and final.filled_quantity == 10000 and final.remaining_quantity == 0
    assert len(engine.csv_journal.read_all()) == 2


def test_two_processes_update_shared_financial_database_and_os_crash_rolls_back(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    store = DurableJSONStore(tmp_path / "counter.json", default={"value": 0})
    store.read()
    # A clean child environment cannot load account tokens or a trading app.
    child_env = {key: value for key, value in os.environ.items() if not key.startswith(("DNSE_", "TELE_"))}
    script = "import dotenv; dotenv.load_dotenv=lambda *a,**k: False; import sys; from viking_v2.trading.durable import DurableJSONStore; s=DurableJSONStore(sys.argv[1],default={'value':0});\nfor i in range(15):\n with s.transaction:\n  v=s.read(); v['value']+=1; s.write(v)"
    processes = [subprocess.Popen([sys.executable, "-c", script, str(store.path)], cwd=Path(__file__).resolve().parents[2], env=child_env) for _ in range(2)]
    for process in processes: assert process.wait(timeout=15) == 0
    assert store.read()["value"] == 30
    crash = "import dotenv; dotenv.load_dotenv=lambda *a,**k: False; import sys,os; from viking_v2.trading.durable import DurableJSONStore; s=DurableJSONStore(sys.argv[1],default={'value':0});\nwith s.transaction:\n s.write({'value':999}); os._exit(17)"
    result = subprocess.run([sys.executable, "-c", crash, str(store.path)], cwd=Path(__file__).resolve().parents[2], env=child_env, timeout=15)
    assert result.returncode == 17
    assert store.read()["value"] == 30


class CashBroker(DNSEClient):
    def __init__(self, cash=100_000_000):
        super().__init__(api_key="FAKE", api_secret="FAKE", account_no="AUDIT")
        self.cash, self.sent, self.orders, self.positions = cash, [], [], []
    def has_trading_token(self): return True
    def cash_package(self, symbol): return {"id": 1, "initialRate": 1, "brokerFirmBuyingFeeRate": .0015}
    def get_balance(self, **kwargs): return {"stock": {"availableCash": self.cash, "totalCash": 100_000_000}}
    def get_positions(self, **kwargs): return deepcopy(self.positions)
    def get_orders(self, **kwargs): return deepcopy(self.orders)
    def get_buying_power(self, *args): return {"qmaxBuy": 10000}
    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        gross = intent.quantity * intent.limit_price * 1000
        self.cash -= gross * 1.0015
        return BrokerOrderResult(True, "Filled", order_id="B"+str(len(self.sent)), raw={"price_unit": "VND", "fillQuantity": intent.quantity, "averagePrice": intent.limit_price * 1000, "fee": gross * .0015})


def test_real_manual_quantity_overrides_suggestion_but_still_checks_cash_and_fees(tmp_path):
    broker = CashBroker()
    engine, _, queue, trades, _ = service(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "LO", limit_price=100, trade_id="T1", execution_mode="REAL", source="MANUAL", entry_budget=1))
    engine.process_due(phase="OPEN", execution_mode="REAL", allow_bot_buys=False)
    assert queue.get(intent.id).status == "FILLED"
    assert trades.get("T1").fees_paid == 30000 and trades.get("T1").open_quantity == 200


@pytest.mark.parametrize("cash", [0, 10_000_000])
def test_real_buy_never_uses_total_cash_or_omits_fee(tmp_path, cash):
    engine, broker, queue, _, _ = service(tmp_path, CashBroker(cash))
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, trade_id="T1", execution_mode="REAL", source="MANUAL"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert not broker.sent and queue.get(intent.id).status == "REJECTED"


def test_due_batch_uses_fresh_cash_in_order_without_double_reserving_later_requests(tmp_path):
    engine, broker, queue, _, _ = service(tmp_path, CashBroker(10_015_000))
    intents = [queue.add(OrderIntent.create(symbol, "BUY", 100, "LO", limit_price=100, trade_id=symbol, execution_mode="REAL", source="MANUAL")) for symbol in ("FPT", "VIX")]
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert [item.symbol for item in broker.sent] == ["FPT"]
    assert [queue.get(item.id).status for item in intents] == ["FILLED", "REJECTED"]


def test_new_buy_adopts_entire_existing_cash_deal_but_not_other_package(tmp_path):
    broker = CashBroker()
    broker.positions = [{"symbol": "FPT", "loanPackageId": "1", "id": "DEAL", "openQuantity": 300, "tradeQuantity": 300, "costPrice": 90_000, "price_unit": "VND"},
                        {"symbol": "FPT", "loanPackageId": "2", "openQuantity": 900, "costPrice": 90_000}]
    engine, _, queue, trades, _ = service(tmp_path, broker)
    queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, trade_id="T1", execution_mode="REAL", source="MANUAL"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    cycle = trades.get("T1")
    assert cycle.open_quantity == 400 and cycle.avg_entry_price == 92.5
    assert cycle.deal_id == "DEAL" and cycle.loan_package_id == "1"


def test_wrong_package_is_not_silently_switched_to_cash_deal(tmp_path):
    broker = CashBroker()
    engine, _, queue, _, _ = service(tmp_path, broker)
    intent = OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, trade_id="T1", execution_mode="REAL", source="MANUAL")
    intent.loan_package_id = "2"
    queue.add(intent)
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert not broker.sent and queue.get(intent.id).status == "REJECTED"


def test_no_holdings_manual_sell_never_becomes_future_t2_sale(tmp_path):
    broker = Broker()
    broker.positions = []
    engine, _, queue, _, _ = service(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=100, execution_mode="REAL", source="MANUAL"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert not broker.sent and queue.get(intent.id).status == "REJECTED"


def test_closed_position_discards_leftover_unsent_sell(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    trades.record_sell_fill("T1", 100, 110)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=100, trade_id="T1", execution_mode="REAL", source="EM"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert not broker.sent and queue.get(intent.id).status == "CANCELLED"


def test_unknown_operator_binding_without_remark_requires_exact_scope_and_never_posts(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, trade_id="T1", execution_mode="REAL"))
    queue._update(intent.id, status="UNKNOWN", handed_off_at=time.time()-30, working_quantity=100)
    row = {"id": "CONFIRMED", "symbol": "FPT", "side": "NB", "quantity": 100, "fillQuantity": 100, "averagePrice": 100000, "orderStatus": "Filled", "createdAt": time.time()-29}
    with pytest.raises(ValueError): engine.bind_confirmed_order(intent.id, {**row, "symbol": "VIX"})
    engine.bind_confirmed_order(intent.id, row)
    assert queue.get(intent.id).status == "FILLED" and trades.get("T1").open_quantity == 100
    assert not broker.sent


def test_journal_io_failure_never_rolls_back_manual_sell_fill_or_cooldown(tmp_path, monkeypatch):
    engine, broker, queue, trades, rules = service(tmp_path, manual_sell_pause_seconds_provider=lambda: 900)
    trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    sell = queue.add(OrderIntent.create("FPT", "SELL", 100, "LO", limit_price=110, trade_id="T1", source="MANUAL", execution_mode="REAL"))
    buy = queue.add(OrderIntent.create("VIX", "BUY", 100, "MARKET", source="BOT", execution_mode="REAL"))
    broker.result = BrokerOrderResult(True, "Filled", raw={"fillQuantity": 100, "averagePrice": 110000})
    monkeypatch.setattr(engine.journal, "append", lambda event: (_ for _ in ()).throw(OSError("journal unavailable")))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert trades.get("T1").status == "CLOSED" and queue.get(sell.id).status == "FILLED"
    assert rules.entry_pause("REAL")["active"] and queue.get(buy.id).status == "CANCELLED"
    assert engine.database.pending_events()


@pytest.mark.parametrize("cached", [False, True])
def test_daemon_keeps_removed_real_symbol_managed_while_paper_selected(tmp_path, monkeypatch, cached):
    from viking_v2 import config
    from viking_v2.services import daemon
    from viking_v2.models import RuntimeConfig
    from viking_v2.services.runtime import RuntimeBridge
    from viking_v2.trading.market import MarketDataService
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    bridge = RuntimeBridge("AUDIT")
    bridge.write_config(RuntimeConfig(["VIX"], True, False))
    if cached:
        bridge.status_store.write({"ticks": {"FPT": {
            "symbol": "FPT", "price": 99, "timestamp": 1, "received_at": 1,
            "stale": True, "health": "REST_UNAVAILABLE", "quote_issue": "SOURCE_ERROR",
        }}})
    trades = TradeStateStore(bridge.trade_state_path)
    trades.create("FPT", "REAL", trade_id="REAL-FPT")
    trades.record_buy_fill("REAL-FPT", 100, 100)
    settings = config.load_settings("AUDIT")
    settings.watchlist, settings.signal_mode = ["VIX"], "CLOSED"
    monkeypatch.setattr(daemon, "load_settings", lambda *args: settings)
    stopped, ticks, statuses = {}, [], []
    monkeypatch.setattr(daemon.signal, "signal", lambda sig, handler: stopped.update(handler=handler))
    monkeypatch.setattr(daemon, "market_phase", lambda *a, **k: ("OPEN", "OPEN"))
    monkeypatch.setattr(daemon.time, "sleep", lambda *a: None)
    original_publish = RuntimeBridge.write_status
    def publish(self, status):
        statuses.append(status.to_dict())
        original_publish(self, status)
    monkeypatch.setattr(RuntimeBridge, "write_status", publish)
    class Client:
        api_key, api_secret = "FAKE", "FAKE"
        def __init__(self, **kwargs): pass
        def connect(self): return True
        def close(self): pass
        def get_working_dates(self): return ["2026-10-05", "2026-10-06", "2026-10-07"]
        def get_secdef(self, symbol): return {"marketId": "STO"}
        def get_balance(self): return {"stock": {"availableCash": 10_000_000}}
        def get_positions(self): return [{"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 100, "costPrice": 100000}]
    class Market:
        frozen_tick_from_bars = staticmethod(MarketDataService.frozen_tick_from_bars)
        def __init__(self, *args): pass
        def start(self, symbols): pass
        def set_symbols(self, symbols): pass
        def stop(self): pass
        def health(self): return {}
        def get_daily_bars(self, symbol, **kwargs): return []
        def get_tick(self, symbol):
            ticks.append(symbol)
            if ticks.count("FPT") == 2: stopped["handler"]()
            return {"symbol": symbol, "price": 99, "bid": 99, "ask": 99, "timestamp": time.time()}
    class Rule:
        def __init__(self, params): self.params = params
        def evaluate(self, context, portfolio):
            if portfolio.get("position_quantity"):
                return StrategyDecision("SELL", context["symbol"], "STOP_LOSS", event="STOP_LOSS", quantity_fraction=1, scope="POSITION_MANAGEMENT")
            return StrategyDecision("WAIT", context["symbol"], "NO_SIGNAL")
    monkeypatch.setattr(daemon, "DNSEClient", Client)
    monkeypatch.setattr(daemon, "DNSEMarketWS", lambda *args: object())
    monkeypatch.setattr(daemon, "MarketDataService", Market)
    monkeypatch.setattr(daemon, "StaticRule", Rule)
    assert daemon.run("AUDIT") == 0
    running = [status for status in statuses if status.get("decisions_by_mode", {}).get("REAL", {}).get("FPT")][-1]
    assert "FPT" in running["active_symbols"]
    assert running["decisions_by_mode"]["REAL"]["FPT"]["action"] == "SELL"
    assert running["decisions_by_mode"]["REAL"]["FPT"]["details"]["execution_mode"] == "REAL"
    assert running["decisions"]["FPT"]["action"] == "WAIT"


def test_off_hours_manual_buy_survives_weekend_skips_ato_and_expires_first_eligible_session(tmp_path):
    clock = {"now": datetime(2026, 10, 2, 20, tzinfo=VN_TZ).timestamp()}
    queue = OrderQueue(tmp_path / "orders.json", now=lambda: clock["now"])
    intent = OrderIntent.create("FPT", "BUY", 100, "MARKET", source="MANUAL", defer_expiry_until_eligible=True, allow_ato=False)
    intent.created_at, intent.expires_at = clock["now"], clock["now"]+86400
    queue.add(intent)
    clock["now"] = datetime(2026, 10, 5, 9, tzinfo=VN_TZ).timestamp()
    assert queue.expire() == []
    assert queue.claim_due(phase="ATO", execution_mode="PAPER", token_ready=True) == []
    clock["now"] = datetime(2026, 10, 5, 9, 15, tzinfo=VN_TZ).timestamp()
    claimed = queue.claim_due(phase="OPEN", execution_mode="PAPER", token_ready=True)[0]
    assert datetime.fromtimestamp(claimed.expires_at, VN_TZ).hour == 15
    queue.release(intent.id, "PENDING", "chờ giá")
    clock["now"] = datetime(2026, 10, 5, 15, tzinfo=VN_TZ).timestamp()
    assert queue.expire()[0].id == intent.id


def test_blocked_buy_is_consumed_and_does_not_revive_when_slot_opens(tmp_path):
    from types import SimpleNamespace
    from viking_v2.dashboard.actions import DashboardActionsMixin
    rules = RuleStateStore(tmp_path / "rules.json")
    blocked = StrategyDecision("WAIT", "FPT", "MAX_POSITIONS", signal="BUY", details={"signal_cycle": "DAY10|SIGNAL1"})
    DashboardActionsMixin._claim_terminal_buy(SimpleNamespace(rule_state=rules), blocked, "REAL")
    planner = StrategyOrderPlanner(OrderQueue(tmp_path / "orders.json"), TradeStateStore(tmp_path / "trades.json"), rules)
    result = planner.plan(StrategyDecision("BUY", "FPT", "ENTRY_BUY", signal="BUY"), execution_mode="REAL", execution_style="MARKET",
                          tick={"price": 100, "ask": 100}, portfolio={"order_budget": 20_000_000, "available_cash": 50_000_000}, candle_key="DAY10|SIGNAL1")
    assert result.intent is None and result.reason == "BUY_SIGNAL_ALREADY_PROCESSED"


def test_out_of_order_buy_and_external_sell_snapshots_preserve_exact_cashflow_pnl(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 10000, "LO", limit_price=100, trade_id="T1", execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="OWN", working_quantity=10000)
    broker.orders = [{"id": "OWN", "symbol": "FPT", "orderStatus": "PartiallyFilled", "fillQuantity": 8000, "averagePrice": 100_000}]
    engine.reconcile_working("REAL")
    cycle = trades.get("T1")
    cycle.opened_at = time.time()-120
    trades.save(cycle)
    # Actual chronology: BUY 8k@100, SELL 4k@110, BUY residual 2k@120.
    # Polling receives cumulative BUY before discovering the external SELL.
    broker.orders[0].update(orderStatus="Filled", fillQuantity=10000, averagePrice=104_000)
    engine.reconcile_working("REAL")
    outside = {"id": "EXT", "symbol": "FPT", "side": "NS", "fillQuantity": 4000, "averagePrice": 110_000, "createdAt": time.time()-60}
    positions = [{"symbol": "FPT", "openQuantity": 6000, "tradeQuantity": 4000, "costPrice": (4000*100+2000*120)/6000}]
    engine.reconcile_external_sells(positions, [*broker.orders, outside])
    cycle = trades.get("T1")
    assert cycle.open_quantity == 6000 and cycle.avg_entry_price == pytest.approx(106.6666666667)
    assert cycle.net_pnl == 40_000_000
    assert cycle.buy_notional == 1_040_000_000 and cycle.sell_notional == 440_000_000
    outside.update(fillQuantity=10000, averagePrice=116_000)
    engine.reconcile_external_sells([], [*broker.orders, outside])
    assert trades.get("T1").net_pnl == 120_000_000


def test_paper_reset_never_reuses_broker_order_id(tmp_path):
    paper = PaperBroker(tmp_path / "paper.json", initial_balance=50_000_000)
    first = paper.place_order(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100))
    paper.reset(50_000_000)
    second = paper.place_order(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100))
    assert first.order_id != second.order_id


def test_external_final_fee_after_full_close_updates_ledger_and_history_once(tmp_path):
    engine, broker, _, trades, _ = service(tmp_path)
    cycle = trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    cycle = trades.get("T1")
    cycle.opened_at = time.time()-120
    trades.save(cycle)
    row = {"id": "EXT", "symbol": "FPT", "side": "NS", "fillQuantity": 100, "averagePrice": 110000, "fee": 10000, "createdAt": time.time()-30}
    engine.reconcile_external_sells([], [row])
    assert trades.get("T1").status == "CLOSED" and trades.get("T1").net_pnl == 990000
    row["fee"] = 15000
    engine.reconcile_external_sells([], [row])
    engine.reconcile_external_sells([], [row])
    assert trades.get("T1").net_pnl == 985000
    assert sum(float(row["fee"]) for row in engine.csv_journal.read_all()) == 15000
