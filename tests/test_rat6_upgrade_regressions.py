import json
import os
import threading
import time
import urllib.error
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot_daemon
import config
import main
from ai_advisor import api_client, config_snapshot, exporter, paths
from core import data_engine as data_engine_module
from core import market_hours
from core.data_engine import DataEngine
from core.dnse_connector import DNSEConnector
from core.dnse_ws import DNSEMarketWS
from core.trade_manager import TradeManager
import core.trade_manager as trade_manager_module
from telegram_notify.client import TelegramClient


def _patch_advisor_account(monkeypatch, tmp_path):
    account = tmp_path / "account"
    monkeypatch.setattr(paths, "account_dir", lambda: str(account))
    monkeypatch.setattr(paths, "account_id", lambda: "0011223344")
    paths.ensure_advisor_dirs()


@pytest.mark.parametrize(
    ("when", "symbol", "expected"),
    [
        (datetime(2026, 7, 10, 8, 40), "VN30F1M", "WARMUP"),
        (datetime(2026, 7, 10, 8, 55), "FPT", "WARMUP"),
        (datetime(2026, 7, 10, 11, 45), "FPT", "LUNCH"),
        (datetime(2026, 7, 10, 14, 46), "FPT", "CLOSED"),
        (datetime(2026, 7, 11, 10, 0), "FPT", "WEEKEND"),
    ],
)
def test_network_session_phases(monkeypatch, when, symbol, expected):
    monkeypatch.setattr(market_hours, "_market_now", lambda: when)
    monkeypatch.setattr(config, "MARKET_HOLIDAYS", set(), raising=False)
    assert market_hours.market_network_phase(symbol)[0] == expected


def test_configured_holiday_is_offline(monkeypatch):
    monkeypatch.setattr(market_hours, "_market_now", lambda: datetime(2026, 7, 10, 10, 0))
    monkeypatch.setattr(config, "MARKET_HOLIDAYS", {"2026-07-10"}, raising=False)
    assert market_hours.market_session_phase("FPT")[0] == "HOLIDAY"
    assert market_hours.is_symbol_network_window_open("FPT")[0] is False


def test_real_data_engine_never_calls_dnse_when_closed(monkeypatch):
    connector = DNSEConnector(api_key="k", api_secret="s", account_no="1")
    monkeypatch.setattr(data_engine_module, "dnse_api", connector)
    monkeypatch.setattr(data_engine_module, "is_symbol_trade_window_open", lambda _s: (False, "closed"))
    monkeypatch.setattr(data_engine_module, "is_symbol_network_window_open", lambda _s, include_preopen=True: (False, "closed"))
    monkeypatch.setattr(connector, "get_ohlc", lambda *_a, **_k: pytest.fail("OHLC network call outside session"))
    monkeypatch.setattr(connector, "get_latest_trade", lambda *_a, **_k: pytest.fail("trade network call outside session"))
    monkeypatch.setattr(connector, "get_latest_quote", lambda *_a, **_k: pytest.fail("quote network call outside session"))

    engine = DataEngine()
    assert engine._fetch_bars("FPT", "15m", 20, {}).empty
    assert engine.fetch_realtime_tick("FPT") is None


def test_daemon_closed_session_keeps_account_api_but_skips_market_scan(monkeypatch):
    daemon = bot_daemon.StandaloneBotDaemon.__new__(bot_daemon.StandaloneBotDaemon)
    daemon.running = True
    account_calls = []
    daemon.connector = SimpleNamespace(get_account_info=lambda: account_calls.append(1) or {"login": ""})
    daemon._read_live_config = lambda: {"BOT_ACTIVE_SYMBOLS": ["FPT"]}
    daemon._active_symbols = []
    daemon._tick_thread = None
    daemon.pending_signals = []
    monkeypatch.setattr(bot_daemon, "is_any_network_window_open", lambda *_a, **_k: False)
    monkeypatch.setattr(bot_daemon, "seconds_until_network_open", lambda *_a, **_k: 30.0)
    monkeypatch.setattr(bot_daemon.data_engine, "set_stream_symbols", lambda _symbols: None)

    def stop(_seconds):
        daemon.running = False

    monkeypatch.setattr(bot_daemon.time, "sleep", stop)
    daemon.run()
    assert account_calls == [1]


def test_ui_closed_session_does_not_touch_connector(monkeypatch):
    app = main.BotUI.__new__(main.BotUI)
    app.running = True
    app.cbo_symbol = SimpleNamespace(get=lambda: "FPT")
    app.connector = SimpleNamespace(get_account_info=lambda: pytest.fail("account network call"))
    app._run_pending_order_scheduler = lambda: None
    advisor_checks = []
    app.run_advisor_triggers_tick = lambda: advisor_checks.append("checked")
    rendered = []
    app._render_cached_ui_snapshot = lambda symbol: rendered.append(symbol)
    monkeypatch.setattr(market_hours, "is_symbol_network_window_open", lambda *_a, **_k: (False, "closed"))
    monkeypatch.setattr(market_hours, "is_symbol_trade_window_open", lambda *_a, **_k: (False, "closed"))
    monkeypatch.setattr(market_hours, "seconds_until_network_open", lambda *_a, **_k: 30.0)

    def stop(_seconds):
        app.running = False

    monkeypatch.setattr(main.time, "sleep", stop)
    main.BotUI.bg_update_loop(app)
    assert rendered == ["FPT"]
    assert advisor_checks == ["checked"]


def test_off_hours_paper_positions_are_rendered_from_local_broker(monkeypatch):
    position = SimpleNamespace(
        ticket=77,
        symbol="VN30F1M",
        type=0,
        price_current=1325.5,
        price_open=1320.0,
        commission=0.0,
    )
    connector = SimpleNamespace(
        get_account_info=lambda: {"login": "PAPER", "balance": 100_000_000.0, "equity": 100_000_000.0},
        get_all_open_positions=lambda: [position],
    )
    app = main.BotUI.__new__(main.BotUI)
    app.connector = connector
    app.latest_market_context = {}
    app.trade_mgr = SimpleNamespace(state={"pnl_today": 0.0})
    app.checklist_mgr = SimpleNamespace(run_pre_trade_checks=lambda *_a, **_k: {"passed": False, "checks": []})
    app.var_strict_mode = SimpleNamespace(get=lambda: True)
    app._is_derivative_symbol = lambda _symbol: True
    rendered = []
    app.update_ui = lambda *args: rendered.append(args)
    app.after = lambda _delay, callback, *args: callback(*args)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(main.data_engine, "fetch_realtime_tick", lambda _symbol: None)
    monkeypatch.setattr(main, "is_manual_position", lambda *_a, **_k: True)
    monkeypatch.setattr("core.storage_manager.get_magic_numbers", lambda: {})

    main.BotUI._render_cached_ui_snapshot(app, "VN30F1M")

    assert len(rendered) == 1
    args = rendered[0]
    assert args[0]["login"] == "PAPER"
    assert args[6] == [position]
    assert args[3].bid == args[3].ask == 1325.5


def test_off_hours_real_account_apis_stay_live_while_price_is_cache_only(monkeypatch):
    calls = []
    position = SimpleNamespace(
        ticket=88, symbol="FPT", type=0, price_current=100.0, price_open=99.0, commission=0.0
    )
    connector = SimpleNamespace(
        account_no="1",
        order_category="NORMAL",
        get_account_info=lambda: calls.append("account") or {"login": "1", "balance": 1.0, "equity": 1.0},
        get_all_open_positions=lambda: calls.append("positions") or [position],
        get_orders=lambda **_kwargs: calls.append("orders") or [],
    )
    app = main.BotUI.__new__(main.BotUI)
    app.connector = connector
    app.latest_market_context = {}
    app.trade_mgr = SimpleNamespace(state={})
    app.checklist_mgr = SimpleNamespace(run_pre_trade_checks=lambda *_a, **_k: {"passed": False, "checks": []})
    app.var_strict_mode = SimpleNamespace(get=lambda: True)
    app._is_derivative_symbol = lambda _symbol: False
    app.update_ui = lambda *_args: None
    app.after = lambda _delay, callback, *args: callback(*args)
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(main.data_engine, "fetch_realtime_tick", lambda _symbol: None)
    monkeypatch.setattr(main, "is_manual_position", lambda *_a, **_k: True)
    monkeypatch.setattr("core.storage_manager.get_magic_numbers", lambda: {})

    main.BotUI._render_cached_ui_snapshot(app, "FPT")
    assert calls == ["account", "positions", "orders"]


def test_symbol_change_immediately_loads_cached_snapshot_and_selects_scope(monkeypatch):
    calls = []
    contract_date_calls = []

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    class Tabs:
        def __init__(self):
            self.selected = ""

        def set(self, value):
            self.selected = value

    app = main.BotUI.__new__(main.BotUI)
    app.connector = object()
    app.trade_mgr = object()
    app.running_tabs = Tabs()
    app.running_trees = {"CKCS PAPER": object(), "CKPS PAPER": object()}
    app.tree = None
    app.var_direction = Var("BUY")
    app.var_manual_entry = Var("1887.7")
    app.var_manual_lot = Var("1")
    app.var_manual_tp = Var("1890.67")
    app.var_manual_sl = Var("1851.17")
    app.lbl_dashboard_price = SimpleNamespace(configure=lambda **_kwargs: None)
    app.lbl_manual_qty_title = SimpleNamespace(configure=lambda **_kwargs: None)
    app.lbl_prev_lot = SimpleNamespace(configure=lambda **_kwargs: None)
    app.lbl_preview_symbol = SimpleNamespace(configure=lambda **_kwargs: None)
    app._quantity_unit = lambda _symbol: "CP"
    app._quantity_label = lambda _symbol: "Cổ phiếu"
    app._is_derivative_symbol = lambda symbol: str(symbol).startswith("VN30F")
    app._save_brain_live_config = lambda: None
    app._update_contract_dates_label = lambda symbol: contract_date_calls.append(symbol)
    app.on_direction_change = lambda _value: None
    app.refresh_manual_preview_tab = lambda: None
    app._render_cached_ui_snapshot = lambda symbol: calls.append(symbol)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)

    main.BotUI.on_symbol_change(app, "AAA")

    assert calls == ["AAA"]
    assert contract_date_calls == ["AAA"]
    assert app.running_tabs.selected == "CKCS PAPER"
    assert app.tree is app.running_trees["CKCS PAPER"]
    assert app.var_manual_entry.get() == ""
    assert app.var_manual_lot.get() == ""
    assert app.var_manual_tp.get() == ""
    assert app.var_manual_sl.get() == ""


def test_manual_trade_closed_session_checks_hours_before_account(monkeypatch):
    manager = TradeManager.__new__(TradeManager)
    manager.connector = SimpleNamespace(get_account_info=lambda: pytest.fail("account network call"))
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _s: (False, "closed"))
    result = manager.execute_manual_trade("BUY", "SCALPING", "FPT", True, {})
    assert result.startswith("SAFEGUARD_FAIL|Market Hours")


def test_ws_auth_subscribes_market_and_trading_channels(monkeypatch):
    client = DNSEMarketWS(api_key="key", api_secret="secret")
    sent = []
    client._ws = SimpleNamespace(send=sent.append)
    client._desired = {"FPT"}
    client._market_data_enabled = True
    client._running = False
    client._handle_control("auth_success", {})
    payloads = [json.loads(item) for item in sent]
    names = [channel["name"] for payload in payloads for channel in payload["channels"]]
    assert "tick.G1.json" in names
    assert "order.STOCK.json" in names
    assert "position.DERIVATIVE.json" in names


def test_ws_ingests_only_configured_account_events():
    client = DNSEMarketWS(api_key="key", api_secret="secret")
    client._account_numbers = {"123"}
    client._ingest_trading({"id": 1, "accountNo": "999", "orderStatus": "New"})
    client._ingest_trading({"id": 2, "accountNo": "123", "orderStatus": "Filled"})
    client._ingest_trading({"id": 3, "accountNo": "123", "openQuantity": 4, "symbol": "FPT"})
    assert [item["id"] for item in client.latest_order_events()] == [2]
    assert [item["id"] for item in client.latest_position_events()] == [3]


def test_unchanged_ws_tick_does_not_fall_back_to_rest(monkeypatch):
    calls = []
    fake_api = SimpleNamespace(
        get_latest_trade=lambda _s: calls.append("trade") or {"matchPrice": 25.5},
        get_latest_quote=lambda _s: {"bid": [{"price": 25.4}], "offer": [{"price": 25.6}]},
    )
    ws = SimpleNamespace(
        available=True,
        is_running=lambda: True,
        is_connected=lambda: True,
        stop=lambda: None,
        set_market_data_enabled=lambda _enabled: None,
        start=lambda: True,
        subscribe=lambda _s: None,
        latest_tick=lambda _s: {"last": 24.0, "timestamp": time.time() - 60},
    )
    monkeypatch.setattr(data_engine_module, "dnse_api", fake_api)
    monkeypatch.setattr(data_engine_module, "market_ws", ws)
    monkeypatch.setattr(data_engine_module, "is_symbol_network_window_open", lambda *_a, **_k: (True, "open"))
    monkeypatch.setattr(config, "DNSE_WS_ENABLED", True)
    monkeypatch.setattr(config, "DNSE_WS_STALE_SECONDS", 5.0)
    tick = DataEngine().fetch_realtime_tick("FPT")
    assert tick["last"] == 24.0
    assert tick["source"] == "WS"
    assert calls == []


def test_ws_msgpack_binary_frame_is_decoded():
    import msgpack

    client = DNSEMarketWS(api_key="key", api_secret="secret")
    client._on_message(None, msgpack.packb({"symbol": "FPT", "matchPrice": 25.5}, use_bin_type=True))
    assert client.latest_tick("FPT")["last"] == 25.5


class _Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


def test_dnse_quota_headers_close_only_affected_endpoint():
    reset = time.time() + 60
    session = SimpleNamespace(
        request=lambda *_a, **_k: _Response(
            200,
            {"ok": True},
            {"X-RateLimit-Limit": "10000", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)},
        )
    )
    connector = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=session)
    first = connector._request("GET", "/price/FPT/quotes/latest")
    second = connector._request("GET", "/price/FPT/quotes/latest")
    other = connector._endpoint_throttle_seconds("GET", "/accounts/1/balances")
    assert first[0] is True
    assert second[2] == 429 and "LOCAL_RATE_LIMIT" in second[3]
    assert other == 0.0
    health = connector.get_api_health_snapshot()
    assert health["rate_limits"]["GET /price/FPT/quotes/latest"]["remaining"] == 0


def test_dnse_429_retries_once_without_storm(monkeypatch):
    responses = [_Response(429, {"message": "slow"}, {"Retry-After": "0.01"}), _Response(200, {"ok": True})]
    calls = []

    def request(*_a, **_k):
        calls.append(1)
        return responses.pop(0)

    connector = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    monkeypatch.setattr(config, "DNSE_RATE_LIMIT_RETRIES", 1)
    monkeypatch.setattr("core.dnse_connector.random.uniform", lambda *_a: 0.0)
    monkeypatch.setattr("core.dnse_connector.time.sleep", lambda *_a: None)
    assert connector._request("GET", "/accounts/1/balances")[0] is True
    assert len(calls) == 2


def test_dnse_concurrent_429_is_serialized_per_endpoint(monkeypatch):
    calls = []

    def request(*_a, **_k):
        calls.append(1)
        return _Response(429, {"message": "quota"}, {"Retry-After": "60"})

    connector = DNSEConnector(api_key="k", api_secret="s", account_no="1", session=SimpleNamespace(request=request))
    monkeypatch.setattr(config, "DNSE_RATE_LIMIT_RETRIES", 0)
    results = []
    workers = [threading.Thread(target=lambda: results.append(connector._request("GET", "/price/FPT/trades/latest"))) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
    assert len(calls) == 1
    assert len(results) == 2 and all(result[2] == 429 for result in results)


def test_email_otp_is_sent_before_prompt(monkeypatch):
    import customtkinter

    events = []
    connector = SimpleNamespace(
        has_trading_token=lambda: False,
        send_email_otp=lambda: events.append("sent") or True,
        verify_otp=lambda otp_type, code: events.append((otp_type, code)) or True,
    )
    app = main.BotUI.__new__(main.BotUI)
    app.connector = connector
    app.log_message = lambda *_a, **_k: None
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setenv("DNSE_OTP_TYPE", "email_otp")
    monkeypatch.setattr(customtkinter, "CTkInputDialog", lambda **_k: SimpleNamespace(get_input=lambda: "123456"))
    assert main.BotUI._ensure_trading_otp(app) is True
    assert events == ["sent", ("email_otp", "123456")]


def test_paper_mode_never_requests_otp(monkeypatch):
    app = main.BotUI.__new__(main.BotUI)
    app.connector = SimpleNamespace(send_email_otp=lambda: pytest.fail("paper must not send OTP"))
    app.log_message = lambda *_a, **_k: None
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    assert main.BotUI._ensure_trading_otp(app) is True


def test_openai_56_payload_has_medium_reasoning():
    _endpoint, _headers, payload = api_client._build_request(
        "openai", "gpt-5.6-terra", "SYS", "BODY", 8000, True, "KEY", "medium"
    )
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["tools"] == [{"type": "web_search"}]


def test_api_tls_uses_verified_default_context(monkeypatch):
    seen = {}

    def fake(req, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(api_client.urllib.request, "urlopen", fake)
    api_client._urlopen(object(), timeout=12)
    assert seen == {"timeout": 12.0}


def test_ai_network_error_retries_then_succeeds(monkeypatch, tmp_path):
    from openpyxl import Workbook

    _patch_advisor_account(monkeypatch, tmp_path)
    for path, content in (
        (paths.advisor_flow_path(), "flow"),
        (paths.technical_settings_path(), "{}"),
        (paths.user_context_path(), "context"),
    ):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    wb = Workbook()
    wb.save(paths.export_path())
    calls = []

    class Success:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"model":"gpt-5.6-terra","output_text":"ok"}'

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError("temporary")
        return Success()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "ADVISOR_API_RETRIES", 1)
    monkeypatch.setattr(api_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(api_client.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(api_client.random, "uniform", lambda *_a: 0.0)
    assert api_client.send_package_to_api()["ok"] is True
    assert len(calls) == 2


def test_openai_citations_are_extracted_and_rendered():
    data = {
        "output": [{"content": [{"annotations": [{"type": "url_citation", "url": "https://example.test/a", "title": "A"}]}]}]
    }
    citations = api_client._extract_citations(data)
    assert citations == [{"title": "A", "url": "https://example.test/a"}]
    assert "[A](https://example.test/a)" in api_client._append_citations("answer", citations)


def test_old_default_model_migrates_to_plain_56(monkeypatch, tmp_path):
    _patch_advisor_account(monkeypatch, tmp_path)
    with open(paths.advisor_api_settings_path(), "w", encoding="utf-8") as f:
        json.dump({"provider": "openai", "model": "gpt-5.4-mini"}, f)
    settings = api_client.load_api_settings()
    assert settings["settings_version"] == 2
    assert settings["model"] == "gpt-5.6"
    assert settings["reasoning_effort"] == "medium"


def test_external_ai_redaction_removes_secrets_accounts_and_paths(monkeypatch):
    monkeypatch.setenv("DNSE_API_SECRET", "super-secret")
    monkeypatch.setenv("DNSE_ACCOUNT_NO", "0011223344")
    monkeypatch.setenv("TELE_BOT_KEY", "telegram-secret-value")
    clean = api_client._sanitize_external_text(
        "secret=super-secret telegram-secret-value account 0011223344 "
        "path C:\\Users\\Kaiser\\private.json"
    )
    assert "super-secret" not in clean
    assert "0011223344" not in clean
    assert "telegram-secret-value" not in clean
    assert "C:\\Users" not in clean
    assert "ACCOUNT#" in clean


def test_external_package_legacy_is_removed_and_direct_files_are_returned(monkeypatch, tmp_path):
    from openpyxl import Workbook

    _patch_advisor_account(monkeypatch, tmp_path)
    monkeypatch.setenv("DNSE_API_SECRET", "package-secret")
    for path, content in (
        (paths.advisor_prompt_path(), "prompt"),
        (paths.advisor_flow_path(), "flow"),
        (paths.technical_settings_path(), "{}"),
        (paths.user_context_path(), "secret=package-secret C:\\Users\\Kaiser\\state.json"),
    ):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    Workbook().save(paths.export_path())
    external_root = tmp_path / "account" / "advisor" / "external_package"
    external_root.mkdir(parents=True, exist_ok=True)
    (external_root / "scan_summary.md").write_text("stale", encoding="utf-8")
    (external_root / "scan_report.md").write_text("stale", encoding="utf-8")
    result = exporter.write_external_package()
    assert result["files"]
    assert result["root"] == paths.advisor_root()
    assert result["manifest"] is None
    assert not external_root.exists()


def test_config_snapshot_redacts_recursive_sensitive_values():
    clean = config_snapshot.redact_for_external_ai(
        {"DNSE_API_KEY": "key", "nested": {"DNSE_ACCOUNT_NO": "123", "path": "C:\\secret\\state.json"}}
    )
    assert clean["DNSE_API_KEY"] == "[REDACTED]"
    assert clean["nested"]["DNSE_ACCOUNT_NO"].startswith("ACCOUNT#")
    assert "C:\\secret" not in clean["nested"]["path"]


def test_telegram_tls_is_secure_by_default_and_errors_hide_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOW_INSECURE_SSL", raising=False)
    client = TelegramClient(token="bot-secret-token")
    assert client.allow_insecure_ssl is False
    monkeypatch.setattr(
        client,
        "_urlopen",
        lambda _req: (_ for _ in ()).throw(RuntimeError("https://api.telegram.org/botbot-secret-token/sendMessage")),
    )
    result = client._request("sendMessage", {"chat_id": "1", "text": "x"})
    assert "bot-secret-token" not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_custom_template_is_preserved_without_latest_file(monkeypatch, tmp_path):
    _patch_advisor_account(monkeypatch, tmp_path)
    template_root = tmp_path / "templates"
    template_root.mkdir()
    monkeypatch.setattr(paths, "template_root", lambda: str(template_root))
    (template_root / "advisor_flow.md").write_text("new shipped flow", encoding="utf-8")
    paths.advisor_flow_path()
    with open(paths.advisor_flow_path(), "w", encoding="utf-8") as f:
        f.write("custom operator flow")
    exporter.ensure_advisor_flow()
    with open(paths.advisor_flow_path(), encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("custom operator flow")
    assert "Phạm vi Advisor và CKCS Research" in text
    latest = paths.advisor_flow_path().replace(".md", ".latest.md")
    assert not os.path.exists(latest)


def test_legacy_template_references_are_migrated_without_version_files(monkeypatch, tmp_path):
    _patch_advisor_account(monkeypatch, tmp_path)
    template_root = tmp_path / "templates"
    template_root.mkdir()
    monkeypatch.setattr(paths, "template_root", lambda: str(template_root))
    (template_root / "advisor_flow.md").write_text("new shipped flow", encoding="utf-8")
    old = "old flow\nexternal_package\nscan_summary.md\n"
    with open(paths.advisor_flow_path(), "w", encoding="utf-8") as f:
        f.write(old)
    exporter.ensure_advisor_flow()
    with open(paths.advisor_flow_path(), encoding="utf-8") as f:
        text = f.read()
    assert "external_package" not in text
    assert "scan_summary.md" not in text
    assert "Phạm vi Advisor và CKCS Research" in text
    assert not os.path.exists(os.path.join(paths.advisor_root(), ".template_versions.json"))
