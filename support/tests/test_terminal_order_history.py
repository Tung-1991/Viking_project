"""Completed DNSE orders use historical reads; unresolved orders stay live."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.models import OrderIntent
from viking_v2.storage import CSVOrderJournal, JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


@pytest.fixture
def archived_order(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 12, 14, 9, 48, tzinfo=VN_TZ).timestamp()]
    monkeypatch.setattr("viking_v2.trading.execution.time.time", lambda: clock[0])
    filled = {"orderId": "236032", "symbol": "HDB", "side": "NB", "quantity": 600,
              "fillQuantity": 600, "averagePrice": 22650, "orderStatus": "Filled", "loanPackageId": "1"}
    session = SimpleNamespace(calls=[], orders=[filled], history=[{
        **filled, "orderId": "20261012_236032", "createdDate": "2026-10-12T07:09:48Z",
    }], history_error=False)

    def request(method, url, **kwargs):
        session.calls.append((method, url, deepcopy(kwargs.get("params"))))
        assert method == "GET", "Reconciliation must not send another order"
        status, message = 200, ""
        if url.endswith("/orders/history"):
            data = session.history
            if session.history_error:
                status, message = 503, "HISTORY_UNAVAILABLE"
        elif url.endswith("/orders"):
            data = session.orders
        else:
            data = None
            status, message = 400, f"OrderRepository: cannot find object with id: {url.rsplit('/', 1)[-1]}"
        return SimpleNamespace(status_code=status, headers={}, text=message,
                               json=lambda: deepcopy(data) if status == 200 else {"message": message})
    session.request = request
    broker = DNSEClient(api_key="OFFLINE", api_secret="OFFLINE", account_no="OFFLINE", session=session, now=lambda: clock[0])
    broker.trading_token, broker.trading_token_expires_at = "OFFLINE", clock[0] + 36000
    queue, trades = OrderQueue(tmp_path / "orders.json"), TradeStateStore(tmp_path / "trades.json")
    service = ExecutionService(broker, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"), trade_state=trades)
    intent = queue.add(OrderIntent("LOCAL-HDB", "HDB", "BUY", 600, "MARKET", execution_mode="REAL",
                                   trade_id="HDB-REAL", created_at=clock[0], loan_package_id="1"))
    queue._update(intent.id, status="WORKING", broker_order_id="236032", working_quantity=600, handed_off_at=clock[0])
    service.reconcile_working("REAL")
    assert queue.get(intent.id).status == "FILLED" and trades.get("HDB-REAL").open_quantity == 600
    session.orders = []
    session.calls.clear()
    clock[0] = datetime(2026, 10, 12, 20, 30, tzinfo=VN_TZ).timestamp()
    return service, broker, queue, trades, session, clock


def test_finished_order_never_calls_the_missing_current_detail_endpoint(archived_order):
    service, broker, queue, trades, session, _clock = archived_order
    service.reconcile_working("REAL")
    assert not any(url.endswith("/orders/236032") for _method, url, _params in session.calls)
    assert any(url.endswith("/orders/history") for _method, url, _params in session.calls)
    assert broker.api_health()["last_status"] == 200 and not broker.api_health()["last_error"]
    assert queue.get("LOCAL-HDB").status == "FILLED"
    assert trades.get("HDB-REAL").open_quantity == 600
    assert len(CSVOrderJournal(service.csv_journal.path).read_all()) == 1


def test_late_historical_fee_is_applied_once_and_survives_restart(archived_order):
    service, broker, queue, trades, session, clock = archived_order
    service.reconcile_working("REAL")
    session.history[0].update(fee=16308, tax=0)
    for _ in range(3):
        clock[0] += 301
        service.reconcile_working("REAL")
    assert trades.get("HDB-REAL").fees_paid == 16308
    assert trades.get("HDB-REAL").open_quantity == 600
    assert trades.get("HDB-REAL").net_pnl == -16308
    assert queue.get("LOCAL-HDB").broker_fee_logged == 16308
    rows = CSVOrderJournal(service.csv_journal.path).read_all()
    assert len(rows) == 2 and rows[-1]["broker_status"] == "COST_ADJUSTMENT"
    restarted = ExecutionService(broker, object(), queue, service.journal, trade_state=trades)
    restarted.reconcile_working("REAL")
    assert trades.get("HDB-REAL").fees_paid == 16308 and len(CSVOrderJournal(service.csv_journal.path).read_all()) == 2
    assert not any(url.endswith("/orders/236032") for _method, url, _params in session.calls)


def test_plain_history_id_and_zero_fee_confirmation_do_not_add_stock(archived_order):
    service, _broker, queue, trades, session, _clock = archived_order
    session.history[0].update(orderId=236032, fee=0, tax=0)
    service.reconcile_working("REAL")
    service.reconcile_working("REAL")
    assert trades.get("HDB-REAL").open_quantity == 600 and trades.get("HDB-REAL").fees_paid == 0
    assert queue.get("LOCAL-HDB").details["broker_progress"]["236032"]["fee_known"]
    rows = CSVOrderJournal(service.csv_journal.path).read_all()
    assert len(rows) == 2 and rows[-1]["message"] == "BROKER_COST_CONFIRMED"


def test_crash_during_historical_fee_application_recovers_once_without_post(archived_order, monkeypatch):
    service, broker, queue, trades, session, _clock = archived_order
    session.history[0].update(fee=16308, tax=0)
    original = trades.adjust_costs
    monkeypatch.setattr(trades, "adjust_costs", lambda *_args: (_ for _ in ()).throw(OSError("OFFLINE_CRASH")))
    with pytest.raises(OSError, match="OFFLINE_CRASH"):
        service.reconcile_working("REAL")
    assert trades.get("HDB-REAL").fees_paid == 0 and queue.get("LOCAL-HDB").broker_fee_logged == 0
    monkeypatch.setattr(trades, "adjust_costs", original)
    restarted = ExecutionService(broker, object(), queue, service.journal, trade_state=trades)
    restarted.reconcile_working("REAL")
    assert trades.get("HDB-REAL").fees_paid == 16308 and trades.get("HDB-REAL").open_quantity == 600
    assert len(CSVOrderJournal(service.csv_journal.path).read_all()) == 2
    assert not any(url.endswith("/orders/236032") for _method, url, _params in session.calls)


def test_cancelled_partial_order_uses_history_without_requeuing_remainder(tmp_path):
    class Broker:
        row = {"orderId": "88", "symbol": "HDB", "side": "NB", "quantity": 1000,
               "fillQuantity": 600, "averagePrice": 22650, "price_unit": "VND", "orderStatus": "Cancelled"}
        archived = False
        details = 0
        def get_orders(self, **_kwargs):
            return [] if self.archived else [deepcopy(self.row)]
        def get_order_history(self, _start, _end):
            return [dict(self.row, fee=16308, tax=0)]
        def get_order_detail(self, _id):
            self.details += 1
            return None
        def has_trading_token(self):
            return True
        def place_order(self, _intent):
            raise AssertionError("Cancelled remainder must never be sent")
    broker = Broker()
    queue, trades = OrderQueue(tmp_path / "orders.json"), TradeStateStore(tmp_path / "trades.json")
    service = ExecutionService(broker, object(), queue, JSONLineJournal(tmp_path / "journal.jsonl"), trade_state=trades)
    intent = queue.add(OrderIntent.create("HDB", "BUY", 1000, "MARKET", execution_mode="REAL", trade_id="PARTIAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="88", working_quantity=1000)
    service.reconcile_working("REAL")
    broker.archived = True
    service.reconcile_working("REAL")
    service.process_due(phase="OPEN", execution_mode="REAL")
    current = queue.get(intent.id)
    assert current.status == "CANCELLED" and current.filled_quantity == 600 and current.remaining_quantity == 400
    assert trades.get("PARTIAL").open_quantity == 600 and trades.get("PARTIAL").fees_paid == 16308
    assert broker.details == 0


def test_empty_or_failed_history_never_undoes_confirmed_stock_or_retries_order(archived_order):
    service, _broker, queue, trades, session, clock = archived_order
    session.history_error = True
    before = trades.get("HDB-REAL").to_dict()
    for _ in range(5):
        service.reconcile_working("REAL")
        clock[0] += 1
    assert sum(url.endswith("/orders/history") for _method, url, _params in session.calls) == 1
    assert trades.get("HDB-REAL").to_dict() == before and queue.get("LOCAL-HDB").status == "FILLED"
    clock[0] += 301
    session.history_error, session.history = False, []
    service.reconcile_working("REAL")
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert trades.get("HDB-REAL").to_dict() == before
    assert all(method == "GET" for method, _url, _params in session.calls)


@pytest.mark.parametrize("change", [{"orderId": "20261013_236032"}, {"symbol": "MSN"},
                                    {"side": "NS"}, {"loanPackageId": "OTHER"}])
def test_history_match_is_scoped_to_day_symbol_side_and_package(archived_order, change):
    service, _broker, queue, trades, session, _clock = archived_order
    session.history[0].update(fee=16308, **change)
    service.reconcile_working("REAL")
    assert trades.get("HDB-REAL").fees_paid == 0 and trades.get("HDB-REAL").open_quantity == 600
    assert queue.get("LOCAL-HDB").status == "FILLED"


@pytest.mark.parametrize("state", ["UNKNOWN", "SENDING", "WORKING", "PARTIAL", "CANCEL_PENDING", "REPLACE_PENDING"])
def test_unresolved_orders_still_request_detail_and_never_resend_when_missing(archived_order, state):
    service, _broker, queue, trades, session, _clock = archived_order
    intent = queue.add(OrderIntent.create("MSN", "BUY", 100, "MARKET", execution_mode="REAL", trade_id="MSN-REAL"))
    changes = dict(status=state, broker_order_id="236033", working_quantity=100, handed_off_at=1)
    if state == "PARTIAL":
        trades.create("MSN", "REAL", trade_id="MSN-REAL")
        trades.record_buy_fill("MSN-REAL", 50, 73.1)
        changes.update(filled_quantity=50, broker_filled_quantity=50, broker_notional_logged=3655000)
    queue._update(intent.id, **changes)
    before = queue.get(intent.id).to_dict()
    service.reconcile_working("REAL")
    service.process_due(phase="OPEN", execution_mode="REAL")
    assert any(url.endswith("/orders/236033") for _method, url, _params in session.calls)
    assert queue.get(intent.id).to_dict() == before
    assert trades.get("HDB-REAL").open_quantity == 600
    assert all(method == "GET" for method, _url, _params in session.calls)
    assert _broker.api_health()["last_status"] == 400  # An unresolved missing order still reports a real fault.
