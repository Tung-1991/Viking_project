"""Regressions for previously reproduced risks; assertions now require fixes.

Passing means these specific safety properties hold in the offline model.
Run only via run_offline.py, which blocks network and isolates runtime.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
import requests

from viking_v2 import config
from viking_v2.connections.dnse.client import DNSEClient, BrokerSnapshotError
from viking_v2.models import BrokerOrderResult, OrderIntent, StrategyDecision
from viking_v2.rules.state import RuleStateStore
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.services.runtime import RuntimeBridge
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import MarketDataService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


class Response:
    status_code = 200
    headers = {}
    text = ""

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class AcceptedThenTimedOut:
    def __init__(self):
        self.calls = []
        self.accepted = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        assert method == "POST"
        row = {**kwargs["json"], "id": str(len(self.accepted) + 1), "orderStatus": "New"}
        self.accepted.append(row)
        if len(self.accepted) == 1:
            raise requests.Timeout("broker accepted first order; response was lost")
        return Response(row)


def make_client(session, monkeypatch):
    broker = DNSEClient(api_key="AUDIT-FAKE", api_secret="AUDIT-FAKE", account_no="AUDIT", session=session)
    broker.connect()
    broker.trading_token = "AUDIT-FAKE"
    broker.trading_token_expires_at = time.time() + 600
    monkeypatch.setattr(broker, "cash_package", lambda symbol: {"id": 1, "initialRate": 1})
    monkeypatch.setattr(broker, "get_secdef", lambda symbol: {"marketId": "STO"})
    return broker


class FakeBroker:
    def __init__(self, positions=None, result=None):
        self.positions = positions or []
        self.orders = []
        self.sent = []
        self.result = result or BrokerOrderResult(True, "New", order_id="AUDIT-1", raw={"fillQuantity": 0})

    def has_trading_token(self):
        return True

    def get_positions(self, **kwargs):
        return deepcopy(self.positions)

    def get_orders(self, **kwargs):
        return deepcopy(self.orders)

    def place_order(self, intent):
        intent.request_tag = intent.request_tag or f"V2:{intent.id[:8].upper()}:{intent.attempt}"
        self.sent.append(deepcopy(intent))
        return self.result


def service_at(tmp_path, broker, **kwargs):
    queue = OrderQueue(tmp_path / "orders.json")
    trades = TradeStateStore(tmp_path / "trades.json")
    service = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "journal.jsonl"), trade_state=trades, **kwargs)
    return service, queue, trades


def waiting_sell(queue, *, trade_id="T1"):
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", source="EM", execution_mode="REAL", trade_id=trade_id, reason="NORMAL_PROTECTION", sell_wait_policy="RECHECK"))
    return queue._update(intent.id, status="WAITING_SETTLEMENT", settlement_waited=True)


def test_no_duplicate_post_after_accepted_timeout(monkeypatch):
    session = AcceptedThenTimedOut()
    broker = make_client(session, monkeypatch)
    monkeypatch.setattr(broker, "get_orders", lambda **kwargs: deepcopy(session.accepted))
    result = broker.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    assert result.ok and result.order_id == "1"
    assert len(session.accepted) == 1
    assert [c[0] for c in session.calls] == ["POST"]


def test_timeout_tag_is_durable_and_late_fill_recovers(tmp_path):
    broker = FakeBroker(result=BrokerOrderResult(False, "UNKNOWN", error="ORDER_STATUS_UNKNOWN"))
    service, queue, trades = service_at(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL", trade_id="T1"))
    service.process_due(phase="OPEN", execution_mode="REAL")
    persisted = queue.get(intent.id)
    assert persisted.status == "UNKNOWN" and persisted.request_tag
    broker.orders = [{"id": "LATE", "remark": broker.sent[0].request_tag, "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100}]
    restarted = ExecutionService(broker, broker, OrderQueue(queue.store.path), JSONLineJournal(tmp_path / "journal.jsonl"), trade_state=TradeStateStore(trades.store.path))
    assert restarted.reconcile_working("REAL")
    assert queue.get(intent.id).status == "FILLED"
    assert trades.get("T1").open_quantity == 100


def test_crash_does_not_lose_managed_fill(tmp_path, monkeypatch):
    broker = FakeBroker(result=BrokerOrderResult(True, "Filled", order_id="AUDIT-1", raw={"fillQuantity": 100, "averagePrice": 100}))
    service, queue, trades = service_at(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL", trade_id="T1"))
    monkeypatch.setattr(service, "_record_trade_fill", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("simulated disk/crash boundary")))
    with pytest.raises(OSError):
        service.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "SENDING"
    assert trades.get("T1") is None
    broker.orders = [{"id": "AUDIT-1", "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100}]
    restarted = ExecutionService(broker, broker, OrderQueue(queue.store.path), JSONLineJournal(tmp_path / "journal.jsonl"), trade_state=TradeStateStore(trades.store.path))
    restarted.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 100
    assert queue.get(intent.id).status == "FILLED"


def test_recheck_missing_live_decision_waits_without_submitting(tmp_path):
    broker = FakeBroker(positions=[{"symbol": "FPT", "quantity": 100, "tradeQuantity": 100}])
    service, queue, _ = service_at(tmp_path, broker, sell_decision_provider=lambda *args: None)
    waiting_sell(queue)
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent == []


def test_recheck_never_expands_sell_before_deal_reconciliation(tmp_path):
    broker = FakeBroker(positions=[{"symbol": "FPT", "quantity": 300, "tradeQuantity": 300}])
    service, queue, trades = service_at(tmp_path, broker, sell_decision_provider=lambda *args: {"action": "SELL", "event": "NORMAL_PROTECTION", "quantity_fraction": 1.0, "details": {"position_quantity": 100}})
    trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    waiting_sell(queue)
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert trades.get("T1").open_quantity == 100
    assert broker.sent[0].quantity == 100


def test_concurrent_daemon_write_preserves_manual_entry_pause(tmp_path, monkeypatch):
    path = tmp_path / "rule.json"
    ui = RuleStateStore(path)
    daemon = RuleStateStore(path)
    snapshot_taken = threading.Event()
    continue_write = threading.Event()
    original_read = daemon._read
    errors = []

    def stale_read():
        raw = original_read()
        snapshot_taken.set()
        assert continue_write.wait(3)
        return raw

    monkeypatch.setattr(daemon, "_read", stale_read)

    def write_confirmation():
        try:
            daemon.save_buy_confirmation("FPT", "REAL", {"active": True})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=write_confirmation)
    worker.start()
    assert snapshot_taken.wait(3)
    ui_worker = threading.Thread(target=lambda: ui.start_entry_pause("REAL", 900))
    ui_worker.start()
    continue_write.set()
    worker.join(3)
    ui_worker.join(3)
    assert not errors and not worker.is_alive()
    assert ui.entry_pause("REAL")["active"] is True


def test_daemon_missing_tick_never_reuses_another_symbol_price(tmp_path, monkeypatch):
    from viking_v2.services import daemon

    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    bridge = RuntimeBridge("AUDIT")
    bridge.disarm(watchlist=["FPT", "VIX"], paper_mode=True)
    settings = config.load_settings("AUDIT")
    settings.watchlist = ["FPT", "VIX"]
    settings.signal_mode = "CLOSED"
    monkeypatch.setattr(daemon, "load_settings", lambda *args: settings)
    stopped = {}
    monkeypatch.setattr(daemon.signal, "signal", lambda sig, handler: stopped.update(handler=handler))
    monkeypatch.setattr(daemon, "market_phase", lambda *args, **kwargs: ("OPEN", "OPEN"))
    monkeypatch.setattr(daemon.time, "sleep", lambda *args: None)
    seen = []

    class Client:
        api_key = "AUDIT"
        api_secret = "AUDIT"

        def __init__(self, **kwargs): pass
        def connect(self): return True
        def get_working_dates(self): return ["2026-10-05"]
        def get_secdef(self, symbol): return {"marketId": "STO"}
        def close(self): pass

    class Market:
        frozen_tick_from_bars = staticmethod(MarketDataService.frozen_tick_from_bars)

        def __init__(self, *args): pass
        def start(self, symbols): pass
        def set_symbols(self, symbols): pass
        def stop(self): pass
        def health(self): return {}
        def get_daily_bars(self, symbol, **kwargs): return []
        def get_tick(self, symbol):
            if symbol == "FPT":
                return {"symbol": "FPT", "price": 100, "bid": 99, "ask": 100, "timestamp": time.time()}
            stopped["handler"]()
            return None

    class Rule:
        def __init__(self, params): self.params = params
        def evaluate(self, context, portfolio):
            seen.append((context["symbol"], context["price"]))
            return StrategyDecision("WAIT", context["symbol"], "AUDIT")

    class Builder:
        def __init__(self, *args, **kwargs): pass
        def build(self, symbol, **kwargs): return {}

    monkeypatch.setattr(daemon, "DNSEClient", Client)
    monkeypatch.setattr(daemon, "DNSEMarketWS", lambda *args: object())
    monkeypatch.setattr(daemon, "MarketDataService", Market)
    monkeypatch.setattr(daemon, "StaticRule", Rule)
    monkeypatch.setattr(daemon, "PortfolioContextBuilder", Builder)
    assert daemon.run("AUDIT") == 0
    status = bridge.read_status()
    assert seen == [("FPT", 100)]
    assert "VIX" not in status["ticks"]
    assert "VIX" not in status["decisions"]


def test_rest_outage_marks_old_tick_and_planner_rejects_it(tmp_path):
    ws = SimpleNamespace(latest_tick=lambda symbol: None)
    client = SimpleNamespace(get_latest_trade=lambda symbol: None, get_latest_quote=lambda symbol: None)
    market = MarketDataService(client, ws)
    old_tick = {"symbol": "FPT", "price": 100, "bid": 100, "ask": 100, "timestamp": time.time() - 3600}
    market._rest_cache["FPT"] = (time.time() - 3600, old_tick)
    returned = market.get_tick("FPT")
    assert returned["timestamp"] < time.time() - 3500
    queue = OrderQueue(tmp_path / "orders.json")
    planner = StrategyOrderPlanner(queue, TradeStateStore(tmp_path / "trades.json"), RuleStateStore(tmp_path / "rule.json"))
    decision = StrategyDecision("BUY", "FPT", "BUY_SIGNAL", event="ENTRY_BUY", signal="BUY")
    plan = planner.plan(decision, execution_mode="REAL", execution_style="MARKET", tick=returned, portfolio={"order_budget": 20_000_000, "available_cash": 50_000_000, "nav": 50_000_000}, candle_key="AUDIT")
    assert plan.intent is None


def test_old_sell_decision_rejected_despite_fresh_heartbeat():
    from viking_v2.dashboard.actions import DashboardActionsMixin
    old = {"action": "SELL", "event": "STOP_LOSS", "details": {"updated_at": "2020-01-01T14:00:00+07:00"}}
    view = SimpleNamespace(bridge=SimpleNamespace(read_config=lambda: SimpleNamespace(paper_mode=False), read_status=lambda: {"heartbeat_at": time.time(), "daemon_status": "RUNNING", "decisions": {"FPT": old}}))
    assert DashboardActionsMixin._latest_sell_decision(view, "FPT", "REAL") is None


def test_paper_selection_and_off_preserve_existing_real_sell_management(tmp_path):
    from viking_v2.dashboard.actions import DashboardActionsMixin
    broker = FakeBroker(positions=[{"symbol": "FPT", "quantity": 100, "tradeQuantity": 100}])
    service, queue, _ = service_at(tmp_path, broker, sell_decision_provider=lambda *args: {"action": "SELL", "event": "NORMAL_PROTECTION"})
    waiting_sell(queue)

    def synchronous_submit(fn):
        future = Future()
        try:
            future.set_result(fn())
        except BaseException as exc:
            future.set_exception(exc)
        return future

    view = SimpleNamespace(running=True, _order_worker_busy=False, _current_market_phase=lambda: "OPEN", bridge=SimpleNamespace(read_status=lambda: {"symbol_phases": {"FPT": "OPEN"}}, read_config=lambda: SimpleNamespace(paper_mode=True, bot_enabled=False)), _io_executor=SimpleNamespace(submit=synchronous_submit), execution=service, rule_state=RuleStateStore(tmp_path / "rule.json"), _post_ui=lambda callback: None)
    DashboardActionsMixin._process_orders(view)
    assert len(broker.sent) == 1
    assert broker.sent[0].execution_mode == "REAL" and broker.sent[0].side == "SELL"


def test_cancel_ack_keeps_queue_tracking_until_broker_confirmation(tmp_path, monkeypatch):
    from viking_v2.dashboard import actions
    broker = FakeBroker()
    broker.cancel_order = lambda order_id: BrokerOrderResult(True, "PendingCancel", order_id=order_id)
    service, queue, trades = service_at(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, execution_mode="REAL", trade_id="T1"))
    queue._update(intent.id, status="WORKING", broker_order_id="AUDIT-1", working_quantity=100)
    action = {"local_id": intent.id, "broker_order_id": "AUDIT-1", "mode": "REAL", "cancellable": True}
    monkeypatch.setattr(actions.messagebox, "askyesno", lambda *args, **kwargs: True)
    view = SimpleNamespace(tabs=SimpleNamespace(get=lambda: "CKCS REAL"), trees={"REAL": SimpleNamespace(selection=lambda: ["row"])}, _running_row_actions={"REAL": {"row": action}}, real=broker, queue=queue, _io_executor=SimpleNamespace(submit=lambda fn: fn()), _post_ui=lambda callback: None)
    actions.DashboardActionsMixin._cancel_selected(view)
    assert queue.get(intent.id).status == "CANCEL_PENDING"
    broker.orders = [{"id": "AUDIT-1", "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100}]
    assert service.reconcile_working("REAL")
    assert trades.get("T1").open_quantity == 100


def test_failed_positions_request_never_closes_trade_as_empty_account(tmp_path, monkeypatch):
    broker = make_client(SimpleNamespace(), monkeypatch)
    older_same_day_sell = datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat()
    old_order = {"id": "OLD-EXTERNAL", "symbol": "FPT", "side": "NS", "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100, "createdDate": older_same_day_sell}

    def request(method, path, **kwargs):
        if path.endswith("/positions"):
            return False, None, 503, "positions service temporarily unavailable"
        if path.endswith("/balances"):
            return True, {"stock": {"availableCash": 20_000_000}}, 200, ""
        return True, {"orders": [old_order]}, 200, ""

    monkeypatch.setattr(broker, "_request", request)
    service, queue, trades = service_at(tmp_path, broker)
    cycle = trades.create("FPT", "REAL", trade_id="T1")
    trades.record_buy_fill("T1", 100, 100)
    cycle = trades.get("T1")
    cycle.opened_at = time.time() - 60
    trades.save(cycle)
    with pytest.raises(BrokerSnapshotError):
        service.account_snapshot("REAL")
    assert trades.get("T1").status == "OPEN"


def test_partial_fills_use_delta_notional_not_cumulative_average(tmp_path):
    broker = FakeBroker()
    service, queue, trades = service_at(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "MARKET", execution_mode="REAL", trade_id="T1"))
    queue._update(intent.id, status="WORKING", broker_order_id="AUDIT-1", working_quantity=200)
    broker.orders = [{"id": "AUDIT-1", "orderStatus": "PartiallyFilled", "fillQuantity": 100, "averagePrice": 100_000}]
    service.reconcile_working("REAL")
    assert trades.get("T1").avg_entry_price == 100
    # First 100 @100, second 100 @120 -> broker cumulative average =110.
    broker.orders[0].update(orderStatus="Filled", fillQuantity=200, averagePrice=110_000)
    service.reconcile_working("REAL")
    assert trades.get("T1").entry_quantity == 200
    assert trades.get("T1").avg_entry_price == 110


def test_off_mid_batch_cancels_unsent_buy_but_not_broker_order(tmp_path):
    broker = FakeBroker()
    service, queue, _ = service_at(tmp_path, broker)
    for symbol in ("FPT", "VIX"):
        queue.add(OrderIntent.create(symbol, "BUY", 100, "MARKET", source="BOT", execution_mode="REAL"))
    original_place = broker.place_order
    disarm_observed = []

    def place_and_disarm(intent):
        result = original_place(intent)
        if len(broker.sent) == 1:
            # Mimic UI OFF after work() already read runtime.bot_enabled=True.
            cancelled, broker_managed = service.cancel_unsubmitted_bot_buys("REAL")
            disarm_observed.extend((len(cancelled), len(broker_managed)))
        return result

    broker.place_order = place_and_disarm
    service.process_due(phase="OPEN", execution_mode="REAL", allow_bot_buys=True)
    assert disarm_observed == [1, 1]
    assert [order.symbol for order in broker.sent] == ["FPT"]


def test_documented_total_fee_rate_is_booked_without_exchange_double_count(tmp_path):
    broker = FakeBroker()
    service, queue, trades = service_at(tmp_path, broker)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL", trade_id="T1"))
    queue._update(intent.id, status="WORKING", broker_order_id="AUDIT-1", working_quantity=100)
    broker.orders = [{"id": "AUDIT-1", "orderStatus": "Filled", "fillQuantity": 100, "averagePrice": 100_000, "feeRate": 0.0015, "taxRate": 0}]
    service.reconcile_working("REAL")
    cycle = trades.get("T1")
    assert cycle.fees_paid == 15_000 and cycle.net_pnl == -15_000
