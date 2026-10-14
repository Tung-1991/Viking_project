"""Priority Alert blocks automatic orders while retaining local observations."""
from copy import deepcopy

import customtkinter as ctk
import pytest

from viking_v2 import config
from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.connections.window import ConnectionPopup
from viking_v2.models import OrderIntent, StrategyDecision
from support.tests.test_business_flow_safety_regressions import headless_app, tick
from support.tests.test_connection_capital_preview import preview
from support.tests.test_live_safety import service


def test_alert_settings_roundtrip_and_removal():
    settings = config.AppSettings.from_dict({
        "watchlist": ["FPT", "SSI"], "priority_symbols": ["FPT"],
        "priority_alert_symbols": [" fpt ", "FPT", "SSI"],
    })
    assert settings.priority_alert_symbols == ["FPT"]
    assert config.is_priority_alert(settings, " fpt ")
    assert not config.is_priority_alert(settings, "SSI")
    assert config.AppSettings.from_dict(settings.to_dict()).priority_alert_symbols == ["FPT"]
    settings.priority_symbols = []
    assert not config.is_priority_alert(settings, "FPT")
    assert settings.normalize().priority_alert_symbols == []
    assert config.AppSettings.from_dict({}).priority_alert_symbols == []


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("source,side", [("BOT", "BUY"), ("BOT", "SELL"), ("EM", "SELL")])
@pytest.mark.parametrize("status", ["PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT", "PAUSED"])
def test_alert_cancels_unsent_automatic_orders_even_outside_session(tmp_path, mode, source, side, status):
    engine, broker, queue, _trades, _rules = service(
        tmp_path, bot_trade_allowed_provider=lambda symbol: symbol != "FPT")
    intent = queue.add(OrderIntent.create("FPT", side, 100, "MARKET", source=source, execution_mode=mode))
    queue._update(intent.id, status=status)
    engine.process_due(phase="CLOSED", execution_mode=mode)
    assert broker.sent == []
    cancelled = queue.get(intent.id)
    assert cancelled.status == "CANCELLED" and "PRIORITY_ALERT" in cancelled.result


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_alert_keeps_manual_order_authority(tmp_path, side, mode):
    engine, broker, queue, _trades, _rules = service(
        tmp_path, bot_trade_allowed_provider=lambda _symbol: False)
    intent = queue.add(OrderIntent.create("FPT", side, 100, "MARKET", source="MANUAL", execution_mode=mode))
    engine.process_due(phase="OPEN", execution_mode=mode)
    assert [item.id for item in broker.sent] == [intent.id]


@pytest.mark.parametrize("status", ["WORKING", "PARTIAL", "UNKNOWN", "SENDING"])
def test_alert_preserves_orders_already_handed_to_broker(tmp_path, status):
    engine, broker, queue, _trades, _rules = service(
        tmp_path, bot_trade_allowed_provider=lambda _symbol: False)
    intent = queue.add(OrderIntent.create("FPT", "BUY", 200, "MARKET", source="BOT", execution_mode="REAL"))
    queue._update(intent.id, status=status, broker_order_id="accepted", handed_off_at=1,
                  filled_quantity=100 if status == "PARTIAL" else 0)
    before = queue.get(intent.id).to_dict()
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).to_dict() == before and broker.sent == []


@pytest.mark.parametrize("source,side", [("BOT", "BUY"), ("EM", "SELL")])
def test_alert_switch_during_slow_quote_blocks_handoff(tmp_path, source, side):
    enabled, calls = {"value": True}, []

    def quote(symbol):
        calls.append(symbol)
        if len(calls) == 2:
            enabled["value"] = False
        return tick(symbol)

    engine, broker, queue, _trades, _rules = service(
        tmp_path, quote_provider=quote,
        bot_trade_allowed_provider=lambda _symbol: enabled["value"])
    intent = queue.add(OrderIntent.create("FPT", side, 100, "MARKET", source=source, execution_mode="REAL"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert len(calls) == 2 and broker.sent == []
    assert queue.get(intent.id).status == "CANCELLED"
    assert queue.get(intent.id).handed_off_at == 0


def test_alert_cancels_local_sell_remainder_without_erasing_fills(tmp_path):
    engine, broker, queue, _trades, _rules = service(
        tmp_path, bot_trade_allowed_provider=lambda _symbol: False)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 200, "MARKET", source="EM", execution_mode="REAL"))
    queue._update(intent.id, status="WAITING_SETTLEMENT", filled_quantity=100, remaining_quantity=100)
    engine.process_due(phase="OPEN", execution_mode="REAL")
    cancelled = queue.get(intent.id)
    assert cancelled.status == "CANCELLED" and cancelled.filled_quantity == 100
    assert cancelled.remaining_quantity == 100 and broker.sent == []


@pytest.mark.parametrize("action,scope", [("BUY", "ENTRY"), ("SELL", "POSITION_MANAGEMENT")])
def test_alert_planning_has_no_order_or_position_mutations(tmp_path, action, scope):
    app = headless_app(tmp_path, balance={"equity": 100e6, "availableCash": 100e6}, positions=[], symbols=["FPT"])
    app.settings.priority_symbols = app.settings.priority_alert_symbols = ["FPT"]
    decision = StrategyDecision(action, "FPT", "SIGNAL", signal=action, scope=scope)
    before = (app.queue._read(), app.trade_state._read(), app.rule_state.store.read())
    result = app._plan_rule_decision(decision, tick(), "REAL")
    assert result.intent is None and result.reason == "PRIORITY_ALERT"
    assert (app.queue._read(), app.trade_state._read(), app.rule_state.store.read()) == before


def test_alert_buy_still_reaches_signal_notifications(tmp_path):
    app = headless_app(tmp_path, balance={"equity": 100e6, "availableCash": 100e6}, positions=[], symbols=["FPT"])
    app.settings.priority_symbols = app.settings.priority_alert_symbols = ["FPT"]
    notices = []
    app._notify_rule_signal = lambda symbol, decision, *_a, **_k: notices.append((symbol, decision.reason, decision.signal))
    decision = StrategyDecision("BUY", "FPT", "BUY_SIGNAL", signal="BUY", details={"candle_key": "test"})
    app._consume_book_decisions({"bot_enabled": True, "decisions": {"FPT": decision.to_dict()},
                                "ticks": {"FPT": tick()}}, "RUNNING", "REAL", False)
    assert notices == [("FPT", "PRIORITY_ALERT", "BUY")]
    assert app.queue.list_all() == []


def test_priority_status_follows_order_lifecycle_and_alert_draft(tmp_path):
    popup, parent = preview(tmp_path)
    popup._priority_alert_symbols = {"MSN"}
    assert popup._priority_preview_data()[1][0][4:6] == ("Chờ", "Alert")
    cycle = parent.trade_state.create("MSN", "PAPER")
    parent.trade_state.record_buy_fill(cycle.id, 100, 20)
    assert popup._priority_preview_data()[1][0][4:6] == ("Vào lệnh", "Alert")
    parent.trade_state.record_sell_fill(cycle.id, 100, 20)
    assert popup._priority_preview_data()[1][0][4:6] == ("Chờ", "Alert")
    assert parent.settings.priority_alert_symbols == []  # Draft only.


def test_compact_popup_alert_is_draft_until_priority_saved(tmp_path, ui_root, monkeypatch):
    import viking_v2.connections.window as module
    saved = []
    monkeypatch.setattr(module, "save_settings", lambda settings, *_args: saved.append(deepcopy(settings.to_dict())))
    settings = config.AppSettings.from_dict({"watchlist": ["FPT"], "priority_symbols": ["FPT"],
        "priority_capital_enabled": False, "priority_allocations": {"FPT": {"limit_vnd": 15e6, "use_pct": 50}}})
    client = DNSEClient(account_no="PAPER")
    popup = ConnectionPopup(ui_root, settings, "PAPER", client, lambda: None)
    previous = set(popup.top.winfo_children())
    top = None
    try:
        popup._configure_priority_symbol("FPT")
        top = next(child for child in popup.top.winfo_children() if child not in previous and isinstance(child, ctk.CTkToplevel))
        bot = next(child for child in top.winfo_children() if isinstance(child, ctk.CTkSwitch))
        assert bot.get() and bot.cget("text") == "Bật"
        bot.toggle()
        assert bot.cget("text") == "Alert"
        next(child for child in top.winfo_children() if isinstance(child, ctk.CTkButton)).invoke()
        assert "FPT" in popup._priority_alert_symbols
        assert settings.priority_alert_symbols == [] and saved == []
        popup._divide_priority_capital()
        assert "FPT" in popup._priority_alert_symbols
        popup._save_priority()
        assert saved[-1]["priority_alert_symbols"] == ["FPT"]
        assert settings.priority_alert_symbols == ["FPT"]
        popup._configure_priority_symbol("FPT")
        top = next(child for child in popup.top.winfo_children() if child not in previous and isinstance(child, ctk.CTkToplevel))
        bot = next(child for child in top.winfo_children() if isinstance(child, ctk.CTkSwitch))
        assert not bot.get() and bot.cget("text") == "Alert"
    finally:
        if top is not None and top.winfo_exists():
            top.destroy()
        popup._close()
        client.close()
