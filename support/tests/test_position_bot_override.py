"""Local BOT Alert preserves observations and blocks both sides before handoff."""
from copy import deepcopy
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from viking_v2.config import AppSettings
from viking_v2.models import OrderIntent, StrategyDecision, TradeCycle
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.services.exit_notifications import settlement_exit_snapshot
from viking_v2.trading.position_controls import save_position_controls, position_bot_cell
from viking_v2.trading.state import TradeStateStore
from support.tests.test_live_safety import service
from support.tests.test_position_modes_and_bulk_close import batch_app
from support.tests.test_position_repair_and_tplus_alerts import g1
from support.tests.test_demo_order_display import _widgets


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("status", ["PENDING", "WAITING_SETTLEMENT", "WAITING_TOKEN", "PAUSED", "SENDING"])
def test_local_bot_alert_cancels_cached_auto_orders_preserving_manual_and_ledger(tmp_path, mode, side, status):
    app, broker, actions = batch_app(tmp_path, mode)
    trade_id = actions[0]["trade_id"]
    before = app.trade_state.get(trade_id).to_dict()
    settings = deepcopy(app.settings.to_dict())
    automatic = app.queue.add(OrderIntent.create("FPT", side, 100, "MARKET", execution_mode=mode,
        source="BOT", trade_id=trade_id, reason="STOP_LOSS" if side == "SELL" else "BUY_SIGNAL"))
    app.queue._update(automatic.id, status=status)
    manual = app.queue.add(OrderIntent.create("FPT", side, 100, "MARKET", execution_mode=mode, source="MANUAL", trade_id=trade_id))
    cycle, broker_pending = save_position_controls(app.trade_state, app.queue, trade_id, bot_trade_enabled=False)
    assert app.queue.get(automatic.id).status == "CANCELLED"
    assert app.queue.get(manual.id).status == "PENDING" and not broker_pending
    assert cycle.to_dict() == {**before, "bot_trade_enabled": False}
    assert app.settings.to_dict() == settings and broker.sent == []
    assert app.trade_state.get(actions[1]["trade_id"]).bot_trade_enabled is True
    assert position_bot_cell(app.settings, cycle) == "Alert · X"
    assert TradeStateStore(app.trade_state.store.path).get(trade_id).bot_trade_enabled is False


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("phase", ["OPEN", "CLOSED"])
@pytest.mark.parametrize("link", ["linked", "missing", "stale"])
def test_replayed_cached_intent_cannot_bypass_local_alert(tmp_path, side, phase, link):
    engine, broker, queue, trades, _ = service(tmp_path, quote_provider=lambda symbol: g1(100, symbol))
    cycle = trades.create("FPT", "REAL", bot_trade_enabled=False)
    trades.record_buy_fill(cycle.id, 300, 100)
    intent = queue.add(OrderIntent.create("FPT", side, 100, "MARKET", execution_mode="REAL", source="BOT",
        trade_id=cycle.id if link == "linked" else "old-id" if link == "stale" else "", reason="STOP_LOSS"))
    engine.process_due(phase=phase, execution_mode="REAL")
    assert broker.sent == [] and queue.get(intent.id).status == "CANCELLED"
    assert "POSITION_BOT_ALERT" in queue.get(intent.id).result


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_operator_switch_during_broker_read_is_rechecked_before_post(tmp_path, side):
    engine, broker, queue, trades, _ = service(tmp_path, quote_provider=lambda symbol: g1(100, symbol))
    cycle = trades.create("FPT", "REAL")
    trades.record_buy_fill(cycle.id, 300, 100)
    reads = []
    original = broker.get_positions if side == "SELL" else engine.quote_provider
    def read(*args, **kwargs):
        reads.append(True)
        trades.update_management(cycle.id, bot_trade_enabled=False)
        return original(*args, **kwargs)
    if side == "SELL":
        broker.get_positions = read
    else:
        engine.quote_provider = read
    item = queue.add(OrderIntent.create("FPT", side, 100, "MARKET", execution_mode="REAL", source="BOT", trade_id=cycle.id))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert reads and broker.sent == [] and queue.get(item.id).status == "CANCELLED"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_manual_order_still_reaches_broker_with_local_bot_alert(tmp_path, side):
    engine, broker, queue, trades, _ = service(tmp_path, quote_provider=lambda symbol: g1(100, symbol))
    cycle = trades.create("FPT", "REAL", bot_trade_enabled=False)
    trades.record_buy_fill(cycle.id, 300, 100)
    item = queue.add(OrderIntent.create("FPT", side, 100, "MARKET", execution_mode="REAL", source="MANUAL", trade_id=cycle.id))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert len(broker.sent) == 1 and queue.get(item.id).status != "CANCELLED"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_local_alert_blocks_stale_planner_decision_before_queue_or_budget_reads(tmp_path, side):
    app, broker, actions = batch_app(tmp_path)
    app.trade_state.update_management(actions[0]["trade_id"], bot_trade_enabled=False)
    decision = StrategyDecision(side, "FPT", "BUY_SIGNAL" if side == "BUY" else "STOP_LOSS",
                                details={"trade_id": actions[0]["trade_id"]})
    result = DashboardActionsMixin._plan_rule_decision_locked(app, decision, g1(100), "REAL")
    assert result.intent is None and result.reason == "POSITION_BOT_ALERT"
    assert app.queue.list_all() == [] and broker.sent == []


@pytest.mark.parametrize("changes,reason", [({"current_price": 95}, "STOP_LOSS"),
    ({"current_price": 108, "tp_mode": "PERCENT", "tp_value": 7}, "TAKE_PROFIT"),
    ({"current_price": 103, "em_modes": ["IND_EXIT"]}, "SELL_SIGNAL"),
    ({"current_price": 107, "em_modes": ["NORMAL"], "normal_armed": True, "peak_profit_pct": 10}, "NORMAL_PROTECTION")])
def test_bot_alert_keeps_exit_signal_and_durable_telegram_notice(monkeypatch, changes, reason):
    monkeypatch.setattr("viking_v2.rules.business.crossover_signal", lambda *_a, **_k: "SELL")
    position = {"quantity": 600, "trade_quantity": 600, "avg_price": 100, "sl_enabled": True,
                "bot_trade_enabled": False, "auto_sell_enabled": True, **changes}
    rule = StaticRule(StaticRuleParameters(indicator_exit_policy="AUTO"))
    before = deepcopy(rule.params.to_dict())
    decision = rule.evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"}, {"position": position})
    assert decision.action == "WAIT" and decision.reason == "POSITION_BOT_ALERT"
    assert decision.details["held_exit_reason"] == reason and decision.details["position_bot_alert"]
    notice = settlement_exit_snapshot(decision, position, mode="REAL")
    assert notice["policy"] == "ALERT" and notice["auto_sell_paused"]
    assert rule.params.to_dict() == before


def test_legacy_trades_keep_their_existing_auto_sell_hold():
    assert TradeCycle.from_dict({"id": "legacy", "symbol": "HDB"}).bot_trade_enabled is True
    held = TradeCycle.from_dict({"id": "legacy", "symbol": "HDB", "auto_sell_enabled": False})
    assert held.bot_trade_enabled is True and held.auto_sell_enabled is False
    assert position_bot_cell(AppSettings(), held) == "Bán Alert · X"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_alert_reports_broker_handoff_without_cancelling_or_erasing_fill(tmp_path, side):
    app, _, actions = batch_app(tmp_path)
    cycle = app.trade_state.get(actions[0]["trade_id"])
    item = app.queue.add(OrderIntent.create("FPT", side, 200, "MARKET", source="BOT", trade_id=cycle.id, execution_mode="REAL"))
    app.queue._update(item.id, status="PARTIAL", broker_order_id="BROKER-1", handed_off_at=1,
                      filled_quantity=100, remaining_quantity=100)
    before = app.queue.get(item.id).to_dict()
    _, pending = save_position_controls(app.trade_state, app.queue, cycle.id, bot_trade_enabled=False)
    assert pending == [item.id] and app.queue.get(item.id).to_dict() == before


def test_bot_alert_does_not_cancel_another_symbol_book_or_package(tmp_path):
    app, _, actions = batch_app(tmp_path)
    cycle = app.trade_state.get(actions[0]["trade_id"])
    unlinked = app.queue.add(OrderIntent.create("FPT", "BUY", 100, "MARKET", source="BOT", execution_mode="REAL", trade_id="old"))
    others = []
    for symbol, mode, package in (("HDB", "REAL", ""), ("FPT", "PAPER", ""), ("FPT", "REAL", "OTHER")):
        item = OrderIntent.create(symbol, "BUY", 100, "MARKET", source="BOT", execution_mode=mode)
        item.loan_package_id = package
        others.append(app.queue.add(item))
    save_position_controls(app.trade_state, app.queue, cycle.id, bot_trade_enabled=False)
    assert app.queue.get(unlinked.id).status == "CANCELLED"
    assert all(app.queue.get(item.id).status == "PENDING" for item in others)


def test_portfolio_context_carries_local_bot_alert_without_dropping_scale_in_signal(tmp_path):
    from support.tests.test_priority_multiple_entries import builder, context, holding
    portfolio = builder(tmp_path)
    cycle = portfolio.trades.create("MSN", "PAPER", bot_trade_enabled=False)
    portfolio.trades.record_buy_fill(cycle.id, 100, 70, order_id="first")
    result = context(portfolio, positions=[holding()])
    assert result["position"]["bot_trade_enabled"] is False
    assert result["scale_in_allowed"]  # Preserve technical observation; planning still blocks its order.


@pytest.mark.parametrize("scaling", [1., 1.5])
def test_popup_bot_override_is_draft_until_save_and_reset_restores_rule(ui_root, monkeypatch, tmp_path, scaling):
    app, broker, actions = batch_app(tmp_path)
    for name, value in vars(app).items():
        monkeypatch.setattr(ui_root, name, value, raising=False)
    for name, value in {"_shared_tick": lambda *_a: g1(100), "_row_time": lambda *_a: "14:09",
                        "_log": lambda *_a: None, "_refresh_local": lambda: None}.items():
        monkeypatch.setattr(ui_root, name, value, raising=False)
    cycle = app.trade_state.get(actions[0]["trade_id"])
    before, settings = cycle.to_dict(), deepcopy(app.settings.to_dict())
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    top = None
    def open_popup():
        previous = set(ui_root.winfo_children())
        DashboardActionsMixin._show_position_management(ui_root, actions[0])
        window, = [w for w in ui_root.winfo_children() if w not in previous and isinstance(w, ctk.CTkToplevel)]
        window.transient("")
        window.deiconify()
        ui_root.update_idletasks()
        return window, list(_widgets(window))
    try:
        top, widgets = open_popup()
        switch = next(w for w in widgets if isinstance(w, ctk.CTkSwitch) and w.cget("text") == "BOT")
        switch.toggle()
        assert app.trade_state.get(cycle.id).to_dict() == before
        assert any(isinstance(w, ctk.CTkLabel) and w.cget("text") == "Alert" for w in widgets)
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        saved = app.trade_state.get(cycle.id)
        assert not saved.bot_trade_enabled and not saved.auto_sell_enabled
        expected = {**before, "em_modes": sorted(before["em_modes"])}
        assert saved.to_dict() == {**expected, "bot_trade_enabled": False, "auto_sell_enabled": False}
        top, widgets = open_popup()
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "VỀ RULE").invoke()
        assert not app.trade_state.get(cycle.id).bot_trade_enabled
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        assert app.trade_state.get(cycle.id).to_dict() == expected
        assert app.settings.to_dict() == settings and broker.sent == []
    finally:
        if top and top.winfo_exists():
            top.destroy()
        ctk.set_widget_scaling(1.)
        ctk.set_window_scaling(1.)


@pytest.mark.parametrize("scaling", [1., 1.25, 1.5, 2.])
def test_rule_keeps_tabs_merges_market_panel_and_equalizes_four_buy_panels(ui_root, scaling):
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    popup = RuleSettingsPopup(ui_root, AppSettings(), "COMPACT_RULE_REVIEW", lambda: None)
    try:
        popup.top.geometry("1080x720+0+0")
        popup.phase_tabs.set("PHASE 1 · THỊ TRƯỜNG")
        ui_root.update_idletasks()
        assert popup.market_phase_override_exposure.master.master is popup.phase1_market_card
        assert len(popup.phase_tabs._tab_dict) == 3
        popup.phase_tabs.set("PHASE 2 · ENTRY BUY")
        ui_root.update_idletasks()
        cards = popup.phase2_cards
        assert max(card.winfo_width() for card in cards) - min(card.winfo_width() for card in cards) <= 1
        assert len({card.winfo_height() for card in cards}) == 1
        for card in cards:
            for child in _widgets(card):
                if isinstance(child, (ctk.CTkEntry, ctk.CTkCheckBox, ctk.CTkOptionMenu, ctk.CTkSwitch)):
                    assert child.winfo_rootx() >= card.winfo_rootx()
                    assert child.winfo_rootx() + child.winfo_width() <= card.winfo_rootx() + card.winfo_width() + 2
                    assert child.winfo_rooty() + child.winfo_height() <= card.winfo_rooty() + card.winfo_height() + 2
    finally:
        popup._close()
        ctk.set_widget_scaling(1.)
        ctk.set_window_scaling(1.)
