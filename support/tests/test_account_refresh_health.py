"""Offline account-read cadence, fail-closed cache, and useful safe alerts."""
from concurrent.futures import Future
import json
from types import SimpleNamespace

import pytest
import requests

from viking_v2 import config
from viking_v2.connections.dnse.client import BrokerSnapshotError, DNSEClient
from viking_v2.connections.dnse.snapshot_health import account_poll_interval, safe_detail
from viking_v2.connections.telegram import SignalTelegramService
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.rules.state import RuleStateStore


@pytest.mark.parametrize("state", ["SENDING", "WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"])
def test_unresolved_real_orders_keep_fast_closed_account_reads(state):
    assert account_poll_interval("CLOSED", [{"status": state, "execution_mode": "REAL"}]) == 5
    assert account_poll_interval("CLOSED", [SimpleNamespace(status=state, execution_mode="REAL")]) == 5
    assert account_poll_interval("CLOSED", [{"status": state, "execution_mode": "PAPER"}]) == 300


@pytest.mark.parametrize("phase", ["OPEN", "ATO", "ATC", "BREAK", "LUNCH", "UNKNOWN_EXCHANGE", "CALENDAR_LOADING", "CALENDAR_UNKNOWN"])
def test_only_known_closed_markets_can_use_idle_interval(phase):
    assert account_poll_interval(phase, []) == 5


def test_idle_policy_ignores_terminal_and_unsent_future_orders_not_unknown_broker_handoffs():
    for state in ("FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED", "PENDING", "WAITING_SETTLEMENT"):
        assert account_poll_interval("CLOSED", [{"status": state}]) == 300
    assert account_poll_interval("CLOSED", [{"status": "PENDING", "broker_order_id": "broker-1"}]) == 5
    assert account_poll_interval("CLOSED", None) == 5
    assert account_poll_interval("CLOSED", [], symbol_phases={"MSN": "CLOSED", "IDC": "OPEN"}) == 5
    assert account_poll_interval("CLOSED", [], symbol_phases={"MSN": "UNKNOWN_EXCHANGE"}) == 5


class Session:
    def __init__(self):
        self.calls, self.status, self.message = [], 200, ""
        self.payload, self.error = None, None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if self.error:
            raise self.error
        payload = self.payload
        if payload is None:
            payload = {"stock": {"availableCash": 50_000_000}} if url.endswith("balances") else []
        if self.status != 200:
            payload = {"message": self.message}
        return SimpleNamespace(status_code=self.status, headers={}, text=self.message, json=lambda: payload)


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setattr(config, "HTTP_RETRIES", 0)
    clock, session = {"now": 1000.0}, Session()
    client = DNSEClient(api_key="FAKEKEY", api_secret="FAKESECRET", account_no="FAKEACCOUNT", session=session,
                        now=lambda: clock["now"])
    client.set_account_poll_interval(300)
    return client, session, clock


@pytest.mark.parametrize("method", ["get_balance", "get_positions", "get_orders"])
def test_idle_snapshot_success_is_cached_but_forced_preflight_always_reads(broker, method):
    client, session, clock = broker
    read = getattr(client, method)
    first = read()
    clock["now"] += 299
    assert read() == first and len(session.calls) == 1
    read(force=True)
    assert len(session.calls) == 2
    clock["now"] += 300
    read()
    assert len(session.calls) == 3


@pytest.mark.parametrize("method", ["get_balance", "get_positions", "get_orders"])
def test_failed_read_backoff_never_returns_previously_cached_success(broker, method):
    client, session, clock = broker
    read = getattr(client, method)
    read()
    session.status, session.message = 503, "SERVICE_UNAVAILABLE"
    clock["now"] += 300
    with pytest.raises(BrokerSnapshotError, match="HTTP 503"):
        read()
    calls = len(session.calls)
    for _ in range(5):
        clock["now"] += 1
        with pytest.raises(BrokerSnapshotError, match="SERVICE_UNAVAILABLE"):
            read()
    assert len(session.calls) == calls
    session.status = 200
    read(force=True)
    assert len(session.calls) == calls + 1
    assert not client.api_health()["snapshot_errors"]


def test_opening_market_shortens_idle_failure_backoff_and_clears_error_on_recovery(broker):
    client, session, clock = broker
    session.status, session.message = 401, "INVALID_SIGNATURE"
    with pytest.raises(BrokerSnapshotError):
        client.get_balance()
    failure = client.api_health()["snapshot_errors"]["balance"]
    assert failure["first_error_at"] == 1000 and failure["retry_at"] == 1300
    clock["now"] += 6
    client.set_account_poll_interval(5)
    session.status = 200
    assert client.get_balance()["stock"]["availableCash"] == 50_000_000
    assert len(session.calls) == 2 and not client.api_health()["snapshot_errors"]


def test_active_failure_retries_at_fast_interval_and_force_bypasses_it(broker):
    client, session, clock = broker
    client.set_account_poll_interval(5)
    session.status, session.message = 500, "FAILED"
    for seconds in (0, 1, 3):
        clock["now"] = 1000 + seconds
        with pytest.raises(BrokerSnapshotError):
            client.get_balance()
    assert len(session.calls) == 1
    with pytest.raises(BrokerSnapshotError):
        client.get_balance(force=True)
    assert len(session.calls) == 2
    clock["now"] += 5
    with pytest.raises(BrokerSnapshotError):
        client.get_balance()
    assert len(session.calls) == 3


def test_backoff_does_not_retain_a_new_traceback_on_every_daemon_loop(broker):
    client, session, _clock = broker
    session.status, session.message = 503, "UNAVAILABLE"
    with pytest.raises(BrokerSnapshotError):
        client.get_balance()
    original = client._snapshot_errors["balance"]["exception"]
    traceback = original.__traceback__
    for _ in range(20):
        with pytest.raises(BrokerSnapshotError) as caught:
            client.get_balance()
        assert caught.value is not original and caught.value.endpoint == original.endpoint
    assert original.__traceback__ is traceback and len(session.calls) == 1


@pytest.mark.parametrize("method", ["get_balance", "get_positions", "get_orders", "get_order_history"])
def test_http_failure_preserves_endpoint_cause_without_credentials(broker, method):
    client, session, _clock = broker
    session.status = 403
    session.message = 'INVALID_SIGNATURE FAKEKEY api_secret=FAKESECRET token=TOPSECRET eyJabc.def.ghi'
    args = ("2026-10-09", "2026-10-09") if method == "get_order_history" else ()
    with pytest.raises(BrokerSnapshotError) as caught:
        getattr(client, method)(*args)
    message = str(caught.value)
    assert "GET /accounts/*/" in message and "HTTP 403" in message and "INVALID_SIGNATURE" in message
    serialized = message + json.dumps(client.api_health())
    for value in ("FAKEACCOUNT", "FAKEKEY", "FAKESECRET", "TOPSECRET", "eyJabc.def.ghi"):
        assert value not in serialized


@pytest.mark.parametrize("method,payload", [
    ("get_balance", []), ("get_positions", {"positions": [], "total": 1}),
    ("get_orders", {"orders": "bad"}),
])
def test_malformed_success_is_still_an_account_failure(broker, method, payload):
    client, session, _clock = broker
    session.payload = payload
    with pytest.raises(BrokerSnapshotError, match="INVALID_RESPONSE"):
        getattr(client, method)()
    assert client.api_health()["snapshot_errors"]


def test_timeout_is_identified_and_secrets_and_multiline_payloads_are_safe(broker):
    client, session, _clock = broker
    session.error = requests.Timeout("Read timed out FAKESECRET\n forged log line")
    with pytest.raises(BrokerSnapshotError) as caught:
        client.get_balance()
    assert "MẠNG/TIMEOUT" in str(caught.value) and "timed out" in str(caught.value)
    assert "FAKESECRET" not in str(caught.value) and "\n" not in str(caught.value)
    assert "secret-value" not in safe_detail('Authorization: Bearer secret-value')
    assert "signed-value" not in safe_detail('X-Signature=signed-value')


def test_http_success_message_does_not_become_a_health_error(broker):
    client, session, _clock = broker
    session.payload = {"stock": {"availableCash": 50_000_000}, "message": "Success"}
    assert client.get_balance()["stock"]["availableCash"] == 50_000_000
    health = client.api_health()
    assert health["last_status"] == 200 and health["last_error"] == ""
    assert not health["snapshot_errors"]


@pytest.mark.parametrize("method,args", [
    ("get_stock_loan_packages", ("HDB",)),
    ("get_buying_power", ("HDB", "CASH", 22.4)),
])
def test_capital_read_errors_keep_http_and_reason_without_secrets(broker, method, args):
    client, session, _clock = broker
    session.status, session.message = 403, "INVALID_SIGNATURE FAKESECRET"
    with pytest.raises(BrokerSnapshotError) as caught:
        getattr(client, method)(*args)
    assert "HTTP 403" in str(caught.value) and "INVALID_SIGNATURE" in str(caught.value)
    assert "FAKESECRET" not in str(caught.value) and "FAKEACCOUNT" not in str(caught.value)


def test_failed_order_notice_names_the_broker_cause_not_a_generic_failure():
    view = DashboardActionsMixin()
    view.real = SimpleNamespace(api_key="FAKEKEY", api_secret="FAKESECRET", trading_token="FAKETOKEN")
    intent = SimpleNamespace(side="BUY", symbol="HDB")
    result = SimpleNamespace(status="REJECTED", error="", message="INSUFFICIENT_BUYING_POWER FAKESECRET")
    summary = view._broker_failure_summary("REAL", intent, result)
    assert "REAL BUY HDB" in summary and "REJECTED" in summary and "INSUFFICIENT_BUYING_POWER" in summary
    assert "FAKESECRET" not in summary


@pytest.fixture
def dashboard(monkeypatch, tmp_path):
    clock = {"now": 1000.0}
    monkeypatch.setattr("viking_v2.dashboard.actions.time.time", lambda: clock["now"])

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread", ImmediateThread)
    view = DashboardActionsMixin()
    view.running, view._snapshot_busy = True, False
    view.settings = SimpleNamespace(telegram_notifications={"system": True}, telegram_cooldown_minutes={"system": 30})
    view.rule_state = RuleStateStore(tmp_path / "rules.json")
    view.mode = SimpleNamespace(get=lambda: "REAL")
    view.status, view.orders, view.messages, view.logs, view.attempts, view.scheduled = {"market_status": "CLOSED"}, [], [], [], [], []
    view.bridge = SimpleNamespace(read_status=lambda: view.status)
    view.queue = SimpleNamespace(list_all=lambda: view.orders)
    view.real = SimpleNamespace(configured=lambda: True, api_key="PRIVATEKEY", api_secret="PRIVATESECRET",
                                set_account_poll_interval=lambda seconds: setattr(view, "interval", seconds))
    view.telegram = SimpleNamespace(notify_system_alert=lambda **kwargs: view.messages.append(kwargs))
    view.snapshots, view.histories = {"REAL": ({"old": True}, [], [])}, {}
    view.logger = SimpleNamespace(warning=lambda *args: view.logs.append(args), info=lambda *args: view.logs.append(args))
    view._log = lambda *args: view.logs.append(args)
    view._refresh_local = lambda: None
    view._post_ui = lambda callback: callback()
    view.after = lambda *args: view.scheduled.append(args)
    view.fail, view.reconcile_fail = True, False

    def snapshot(mode):
        view.attempts.append(mode)
        if mode == "REAL" and view.fail:
            raise BrokerSnapshotError("GET /accounts/*/balances · HTTP 503 · UNAVAILABLE PRIVATEKEY")
        return ({"new": True}, [], [])

    def reconcile(*args):
        if view.reconcile_fail:
            raise ValueError("cannot reconcile PRIVATESECRET")
        return []

    def submit(work):
        future = Future()
        try:
            future.set_result(work())
        except Exception as exc:
            future.set_exception(exc)
        return future

    view.execution = SimpleNamespace(account_snapshot=snapshot, reconcile_external_sells=reconcile)
    view._io_executor = SimpleNamespace(submit=submit)
    return view, clock


def test_idle_refresh_is_quiet_but_persistent_fault_alerts_with_reason_and_recovers(dashboard):
    view, clock = dashboard
    view._refresh_snapshots()
    assert view.interval == 300 and view.messages == [] and view.logs
    assert view.snapshots["REAL"][0] == {"old": True} and view.snapshots["PAPER"][0] == {"new": True}
    log_count = len(view.logs)
    for delta in (5, 300, 600, 900):
        clock["now"] = 1000 + delta
        view._refresh_snapshots()
    assert view.attempts.count("REAL") == 4 and view.attempts.count("PAPER") == 5
    assert len(view.logs) == log_count and len(view.messages) == 1
    message = view.messages[0]
    assert message["severity"] == "WARNING"
    assert "NGOÀI PHIÊN" in message["summary"] and "HTTP 503" in message["summary"] and "5 phút" in message["summary"]
    assert "PRIVATEKEY" not in message["summary"]
    assert all(seconds == 5000 for seconds, _callback in view.scheduled)
    clock["now"], view.fail = 2200, False
    view._refresh_snapshots()
    assert "REAL" not in view._account_snapshot_faults and view.snapshots["REAL"][0] == {"new": True}
    recovered_count = len(view.logs)
    clock["now"] += 300
    view._refresh_snapshots()
    assert len(view.logs) == recovered_count and recovered_count > log_count


def test_order_worker_failure_reports_cause_and_schedules_retry_without_leaking_secrets(dashboard):
    view, _clock = dashboard
    view._order_worker_busy = False
    view._current_market_phase = lambda: "CLOSED"
    view.logger.error = lambda *args: view.logs.append(args)
    failed = Future()
    failed.set_exception(ValueError("RECONCILE_FAILED PRIVATESECRET"))
    view._io_executor.submit = lambda _work: failed
    view._process_orders()
    assert not view._order_worker_busy
    assert "RECONCILE_FAILED" in view.messages[0]["summary"]
    assert "PRIVATESECRET" not in str(view.messages) + str(view.logs)
    assert view.scheduled[-1][0] == 1000


@pytest.mark.parametrize("change", ["OPEN", "UNKNOWN"])
def test_opening_or_unresolved_order_wakes_idle_read_within_five_seconds(dashboard, change):
    view, clock = dashboard
    view._refresh_snapshots()
    clock["now"] += 5
    if change == "OPEN":
        view.status["market_status"] = "OPEN"
    else:
        view.orders.append({"status": "UNKNOWN", "execution_mode": "REAL"})
    view._refresh_snapshots()
    assert view.interval == 5 and view.attempts.count("REAL") == 2
    assert len(view.messages) == 1 and view.messages[0].get("severity", "ERROR") == "ERROR"
    assert "5 giây" in view.messages[0]["summary"]


def test_reconciliation_errors_are_not_mislabelled_or_throttled_as_idle_api_errors(dashboard):
    view, clock = dashboard
    view.fail, view.reconcile_fail = False, True
    view._refresh_snapshots()
    assert len(view.messages) == 1
    assert "đối soát giao dịch ngoài app" in view.messages[0]["summary"]
    assert "PRIVATESECRET" not in view.messages[0]["summary"]
    clock["now"] += 5
    view._refresh_snapshots()
    assert view.interval == 5 and view.attempts.count("REAL") == 2


def account_status(first_error_at=1000, *, last_endpoint="GET /accounts/*/balances", last_status=503):
    failure = {"endpoint": "GET /accounts/*/balances", "status": 503,
               "error": "GET /accounts/*/balances · HTTP 503 · SERVICE_UNAVAILABLE", "first_error_at": first_error_at}
    return {"error": "account failed", "cycle_error_context": {
        "stage": "ACCOUNT_SNAPSHOT", "execution_mode": "REAL", "snapshot_error": failure,
    }, "api_health": {"rest": {
        "total_requests": 1, "last_endpoint": last_endpoint, "last_status": last_status,
        "account_poll_seconds": 300, "snapshot_errors": {"balance": failure},
    }}}


def test_general_health_does_not_bypass_idle_account_alert_delay(dashboard):
    view, clock = dashboard
    status = account_status()
    view._notify_system_health(status, "RUNNING", "CLOSED", "REAL")
    assert view.messages == []
    clock["now"] += 600
    view._notify_system_health(status, "RUNNING", "CLOSED", "REAL")
    assert len(view.messages) == 1 and view.messages[0]["severity"] == "WARNING"
    assert "HTTP 503" in view.messages[0]["summary"] and "SERVICE_UNAVAILABLE" in view.messages[0]["summary"]


@pytest.mark.parametrize("fault", ["OPEN", "WORKING", "STALE", "CALENDAR", "OTHER_ENDPOINT"])
def test_idle_account_delay_cannot_hide_other_real_health_faults(dashboard, fault):
    view, _clock = dashboard
    status, daemon, phase = account_status(), "RUNNING", "CLOSED"
    if fault == "OPEN":
        phase = "OPEN"
    elif fault == "WORKING":
        view.orders.append({"status": "WORKING"})
    elif fault == "STALE":
        daemon = "STALE"
    elif fault == "CALENDAR":
        phase = "CALENDAR_UNKNOWN"
    else:
        status["api_health"]["rest"].update(last_endpoint="GET /working-dates", last_status=500)
    view._notify_system_health(status, daemon, phase, "REAL")
    assert len(view.messages) == 1 and view.messages[0].get("severity", "ERROR") == "ERROR"


def test_account_fault_in_health_is_not_overwritten_by_unrelated_success(dashboard):
    view, _clock = dashboard
    status = account_status(first_error_at=1, last_endpoint="GET /price", last_status=200)
    view._notify_system_health(status, "RUNNING", "CLOSED", "REAL")
    assert len(view.messages) == 1 and "HTTP 503" in view.messages[0]["summary"]


def test_health_hint_includes_actual_failure_and_retry_schedule_without_raw_secrets():
    subject = DashboardPanelsMixin()
    subject._quote_health_hint = lambda: "MSN · nguồn WS"
    subject._health_hint_status = lambda: account_status()
    subject.real = SimpleNamespace(api_health=lambda: {}, api_key="PRIVATEKEY")
    hint = subject._api_health_hint()
    assert "HTTP 503" in hint and "SERVICE_UNAVAILABLE" in hint and "5 phút" in hint
    assert "OTP: cần khi gửi/sửa/hủy" in hint


def test_health_badge_remains_failed_after_unrelated_rest_success_until_account_recovers(dashboard):
    view, clock = dashboard
    subject = DashboardPanelsMixin()
    rest = account_status(last_endpoint="GET /price", last_status=200)["api_health"]["rest"]
    subject.real = SimpleNamespace(api_health=lambda: rest, configured=lambda: True, has_trading_token=lambda: True)
    subject.mode, subject.symbol = view.mode, SimpleNamespace(get=lambda: "MSN")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        label = SimpleNamespace(options={})
        label.configure = lambda _label=label, **kwargs: _label.options.update(kwargs)
        setattr(subject, f"preview_health_{name}", label)
    status = {"heartbeat_at": clock["now"], "daemon_status": "RUNNING", "market_status": "CLOSED"}
    subject._refresh_api_health_panel(status)
    assert subject.preview_health_rest.options["text"] == "API LỖI"
    assert subject.preview_health_title.options["text"] == "HEALTH LỖI"
    rest["snapshot_errors"].clear()
    subject._refresh_api_health_panel(status)
    assert subject.preview_health_rest.options["text"] == "API OK"
    assert subject.preview_health_title.options["text"] == "HEALTH OK"


def test_persistent_idle_warning_is_distinct_from_active_system_error():
    sent = []
    telegram = SimpleNamespace(send_message=lambda chat, message: sent.append(message) or True)
    service = SignalTelegramService(telegram, chat_id="OFFLINE")
    assert service.notify_system_alert(summary="HTTP 503", execution_mode="REAL", severity="WARNING")
    assert service.notify_system_alert(summary="HTTP 500", execution_mode="REAL")
    assert sent[0].startswith("🟡") and "CẢNH BÁO" in sent[0]
    assert sent[1].startswith("🔴") and "CẦN KIỂM TRA" in sent[1]
