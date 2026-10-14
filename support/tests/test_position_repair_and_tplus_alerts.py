"""Operator hold, T+ notifications and an explicit restart preserve real fills."""
from copy import deepcopy
from types import SimpleNamespace
import time

import pytest

from viking_v2.models import OrderIntent, StrategyDecision, TradeCycle
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.exit_notifications import settlement_exit_snapshot
from viking_v2.trading.position_repair import pause_position_auto_sell, restart_price_observations
from viking_v2.trading.quote_boards import MATCHED_DATA_POLICY
from viking_v2.trading.state import TradeStateStore
from viking_v2.trading.orders import OrderQueue
from viking_v2.dashboard.tables import DashboardTablesMixin, RUNNING_COLUMNS
from support.tests.test_live_safety import service
from support.tests.test_business_flow_safety_regressions import tick, headless_app
from support.tests.test_telegram_buy_lost import subject
from support.tests.telegram_support import TELEGRAM_START
from support.tests.test_dashboard_ui_regressions import _running_subject


def g1(price=23.1, symbol="HDB"):
    return {**tick(symbol, price), "boardId": "G1", "price_source": "MATCH", "market_data_policy": MATCHED_DATA_POLICY}


def account(tmp_path, mode="REAL"):
    trades, rules, queue = TradeStateStore(tmp_path / "trades.json"), RuleStateStore(tmp_path / "rules.json"), OrderQueue(tmp_path / "orders.json")
    cycle = trades.create("HDB", mode, em_modes=["NORMAL", "IND_EXIT"])
    trades.record_buy_fill(cycle.id, 600, 22.65)
    trades.record_sell_fill(cycle.id, 200, 23.15)
    rules.update_position_metrics("HDB", cycle.id, profit_pct=8.6092715, net_pnl=1170000, market_price=24.6)
    rules.update_position_metrics("HDB", cycle.id, profit_pct=-6.843267, net_pnl=-930000, market_price=21.1)
    rules.arm_normal("HDB", cycle.id)
    for identifier in ("first", "second"):
        rules.mark_protection_done("HDB", cycle.id, ["NORMAL_PROTECTION"], execution_id=identifier, completed=True)
    return trades, rules, queue, trades.get(cycle.id)


def test_existing_accounts_default_to_enabled_and_hold_survives_restart(tmp_path):
    assert TradeCycle.from_dict({"id": "legacy", "symbol": "HDB"}).auto_sell_enabled is True
    trades, rules, queue, cycle = account(tmp_path)
    before = cycle.to_dict()
    pause_position_auto_sell(trades, queue, cycle.id, enabled=False)
    current = TradeStateStore(trades.store.path).get(cycle.id).to_dict()
    assert current == {**before, "auto_sell_enabled": False}
    assert rules.position_metrics("HDB", cycle.id)["normal_protection_count"] == 2


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("reason", ["STOP_LOSS", "TAKE_PROFIT", "INDICATOR_EXIT", "NORMAL_PROTECTION", "DYNAMIC_PROTECTION"])
@pytest.mark.parametrize("status", ["PENDING", "WAITING_SETTLEMENT", "WAITING_TOKEN", "PAUSED", "SENDING"])
def test_hold_cancels_only_unsent_automatic_exit(tmp_path, mode, reason, status):
    trades, rules, queue, cycle = account(tmp_path, mode)
    auto = queue.add(OrderIntent.create("HDB", "SELL", 100, "MARKET", source="EM", execution_mode=mode, trade_id=cycle.id, reason=reason))
    queue._update(auto.id, status=status)
    manual = queue.add(OrderIntent.create("HDB", "SELL", 100, "LO", source="MANUAL", execution_mode=mode, trade_id=cycle.id, limit_price=24))
    _, cancelled, broker_pending = pause_position_auto_sell(trades, queue, cycle.id, enabled=False)
    assert cancelled == [auto.id] and broker_pending == []
    assert queue.get(auto.id).status == "CANCELLED" and queue.get(manual.id).status == "PENDING"
    assert trades.get(cycle.id).open_quantity == 400


@pytest.mark.parametrize("reason,source,expected", [("STOP_LOSS", "EM", False), ("TAKE_PROFIT", "EM", False),
    ("INDICATOR_EXIT", "EM", False), ("NORMAL_PROTECTION", "EM", False), ("DYNAMIC_PROTECTION", "BOT", False), ("MANUAL", "MANUAL", True)])
def test_new_or_stale_exit_cannot_bypass_hold_but_manual_still_sends(tmp_path, reason, source, expected):
    engine, broker, queue, trades, rules = service(tmp_path)
    cycle = trades.create("FPT", "REAL")
    trades.record_buy_fill(cycle.id, 300, 100)
    trades.update_management(cycle.id, auto_sell_enabled=False)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", source=source, trade_id=cycle.id, execution_mode="REAL", reason=reason))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert bool(broker.sent) == expected
    if not expected:
        assert queue.get(intent.id).status == "CANCELLED"


def test_hold_is_checked_again_after_account_read(tmp_path):
    engine, broker, queue, trades, _ = service(tmp_path)
    cycle = trades.create("FPT", "REAL")
    trades.record_buy_fill(cycle.id, 300, 100)
    original = broker.get_positions
    def positions(**kwargs):
        trades.update_management(cycle.id, auto_sell_enabled=False)
        return original(**kwargs)
    broker.get_positions = positions
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", source="EM", trade_id=cycle.id, execution_mode="REAL", reason="STOP_LOSS"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent == [] and queue.get(intent.id).status == "CANCELLED"


@pytest.mark.parametrize("changes,held_reason", [
    ({"current_price": 95}, "STOP_LOSS"),
    ({"current_price": 108, "tp_mode": "PERCENT", "tp_value": 7}, "TAKE_PROFIT"),
    ({"current_price": 103, "em_modes": ["IND_EXIT"]}, "SELL_SIGNAL"),
    ({"current_price": 107, "em_modes": ["NORMAL"], "normal_armed": True, "peak_profit_pct": 10}, "NORMAL_PROTECTION"),
])
def test_held_position_reports_exit_without_selling(monkeypatch, changes, held_reason):
    monkeypatch.setattr("viking_v2.rules.business.crossover_signal", lambda *_a, **_k: "SELL")
    position = {"quantity": 600, "trade_quantity": 600, "avg_price": 100, "sl_enabled": True,
                "auto_sell_enabled": False, **changes}
    decision = StaticRule(StaticRuleParameters(indicator_exit_policy="AUTO")).evaluate(
        {"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"}, {"position": position})
    assert decision.action == "WAIT" and decision.reason == "POSITION_AUTO_SELL_PAUSED"
    assert decision.details["held_exit_reason"] == held_reason


@pytest.mark.parametrize("stage,reason,event", [("ARM", "PROTECT_WAITING_SETTLEMENT", ""),
    ("DYN", "PROTECT_WAITING_SETTLEMENT", ""), ("E", "SELL_SIGNAL", "INDICATOR_EXIT"),
    ("E", "INDICATOR_EXIT_ALERT", "INDICATOR_EXIT_ALERT")])
def test_wait_notice_survives_missed_event_and_retries_once(subject, monkeypatch, stage, reason, event):
    app, telegram = subject
    state = app.rule_state
    state.update_position_metrics("HDB", "trade", profit_pct=2, market_price=23.1)
    decision = StrategyDecision("SELL" if event == "INDICATOR_EXIT" else "WAIT", "HDB", reason, event=event,
        details={"current_price": 23.1, "normal_peak_price": 23.45, "normal_trigger_price": 23.2,
                 "normal_policy": "AUTO", "indicator_exit_policy": "AUTO", "protection_stage": stage})
    snapshot = settlement_exit_snapshot(decision, {"quantity": 600, "trade_quantity": 0}, mode="REAL")
    notices = state.observe_exit_wait("HDB", "trade", snapshot)
    for _ in range(4):
        assert state.observe_exit_wait("HDB", "trade", snapshot) == notices
    original = app.telegram.notify_exit_wait
    monkeypatch.setattr(app.telegram, "notify_exit_wait", lambda **_k: False)
    # The GUI missed the trigger; a later HOLD still carries its captured notice.
    later = StrategyDecision("WAIT", "HDB", "HOLD_POSITION", details={"trade_id": "trade", "exit_wait_notices": notices})
    app._notify_rule_signal("HDB", later, {"price": 23.1}, execution_mode="REAL")
    assert len(state.pending_telegram_notices()) == 1 and not telegram.sent
    app.rule_state = RuleStateStore(state.store.path)
    monkeypatch.setattr("viking_v2.dashboard.actions.time.time", lambda: TELEGRAM_START + 61)
    monkeypatch.setattr(app.telegram, "notify_exit_wait", original)
    app._retry_telegram_notices()
    app._notify_rule_signal("HDB", later, {"price": 23.1}, execution_mode="REAL")
    assert len(telegram.sent) == 1
    assert "CHỜ CỔ VỀ" in telegram.sent[0] and "Chưa gửi SELL" in telegram.sent[0]
    assert "Tạm dừng tự bán" in telegram.sent[0]


def test_wait_episode_can_notify_again_only_after_condition_clears(tmp_path):
    state = RuleStateStore(tmp_path / "rules.json")
    state.update_position_metrics("HDB", "trade", profit_pct=2)
    first = state.observe_exit_wait("HDB", "trade", {"kind": "E"})
    state.observe_exit_wait("HDB", "trade", None)
    second = state.observe_exit_wait("HDB", "trade", {"kind": "E"})
    assert first[0]["occurrence"] != second[0]["occurrence"]
    assert len(second) == 1


def test_tplus_e_alert_does_not_send_a_second_plain_alert(subject):
    app, telegram = subject
    snapshot = {"symbol": "HDB", "execution_mode": "REAL", "kind": "E", "policy": "ALERT",
        "price": 23.1, "sellable_quantity": 0, "held_quantity": 600, "peak_price": 0,
        "protect_price": 0, "observed_at": TELEGRAM_START, "occurrence": "tplus-E"}
    decision = StrategyDecision("WAIT", "HDB", "INDICATOR_EXIT_ALERT", event="INDICATOR_EXIT_ALERT", details={
        "sellable_quantity": 0, "exit_wait_notices": [snapshot], "trade_id": "trade", "signal_cycle": "day"})
    app._notify_rule_signal("HDB", decision, {"price": 23.1}, execution_mode="REAL")
    assert len(telegram.sent) == 1 and "CHỜ CỔ VỀ" in telegram.sent[0]


@pytest.mark.parametrize("kind,event", [("STOP_LOSS", "EXIT_ALERT"), ("TAKE_PROFIT", "EXIT_ALERT"),
                                       ("E", "INDICATOR_EXIT_ALERT"), ("PROTECT ARM", "PROTECT_ALERT")])
def test_paused_exit_has_a_durable_notice_even_after_stock_settles(kind, event):
    decision = StrategyDecision("WAIT", "HDB", "POSITION_AUTO_SELL_PAUSED", event=event, details={
        "held_exit_reason": kind, "auto_sell_paused": True, "protection_stage": "ARM", "current_price": 23.1})
    snapshot = settlement_exit_snapshot(decision, {"quantity": 400, "trade_quantity": 400, "auto_sell_enabled": False}, mode="REAL")
    assert snapshot["kind"] == kind and snapshot["auto_sell_paused"] is True
    assert snapshot["policy"] == "ALERT" and snapshot["sellable_quantity"] == 400


def test_restart_preserves_400_shares_realized_profit_and_two_fill_counters(tmp_path):
    trades, rules, queue, cycle = account(tmp_path)
    before = cycle.to_dict()
    repaired = restart_price_observations(trades, rules, queue, cycle.id, g1())
    assert trades.get(cycle.id).to_dict() == {**before, "auto_sell_enabled": False}
    assert repaired["normal_protection_count"] == 2 and "normal_armed" not in repaired
    assert repaired["price_history_before_repair"]["peak_profit_pct"] == pytest.approx(8.6092715)
    assert repaired["price_history_evidence"]["coverage"] == "FROM_RESTART"
    assert repaired["mfe_pct"] == pytest.approx((23.1 / 22.65 - 1) * 100)
    assert repaired["mae_pct"] == 0 and repaired["current_net_pnl"] == pytest.approx(280000)
    assert repaired["current_net_pnl_pct"] == pytest.approx(280000 / 13590000 * 100)


@pytest.mark.parametrize("change", [{"boardId": "T1"}, {"price_source": "EXPECTED"}, {"stale": True},
                                    {"frozen": True}, {"price": float("nan")}, {"timestamp": 0}])
def test_invalid_quote_cannot_reset_or_modify_account(tmp_path, change):
    trades, rules, queue, cycle = account(tmp_path)
    before = trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)
    with pytest.raises(ValueError):
        restart_price_observations(trades, rules, queue, cycle.id, {**g1(), **change})
    assert (trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)) == before


@pytest.mark.parametrize("source", ["EM", "MANUAL"])
def test_working_broker_sell_blocks_reset_and_is_retained_for_reconciliation(tmp_path, source):
    trades, rules, queue, cycle = account(tmp_path)
    intent = queue.add(OrderIntent.create("HDB", "SELL", 100, "MARKET", source=source, trade_id=cycle.id, execution_mode="REAL"))
    queue._update(intent.id, status="WORKING", broker_order_id="broker-accepted", handed_off_at=time.time())
    before = rules.position_metrics("HDB", cycle.id)
    result = restart_price_observations(trades, rules, queue, cycle.id, g1())
    assert result["blocked_by_broker_orders"] == [intent.id]
    assert trades.get(cycle.id).auto_sell_enabled is False
    assert queue.get(intent.id).status == "WORKING" and rules.position_metrics("HDB", cycle.id) == before


@pytest.mark.parametrize("verified", [True, False])
def test_running_row_has_correct_original_basis_prices_and_explicit_history_state(ui_root, verified):
    from tkinter import ttk
    tree = ttk.Treeview(ui_root, show="headings")
    cycle = TradeCycle("real-trade", "HDB", execution_mode="REAL", auto_sell_enabled=False, em_modes=["NORMAL"])
    cycle.record_buy_fill(600, 22.65)
    cycle.record_sell_fill(200, 23.15)
    position = {"symbol": "HDB", "openQuantity": 400, "tradeQuantity": 400,
                "price_unit": "VND", "costPrice": 22650, "marketPrice": 23100}
    app = _running_subject(tree, "REAL", positions=[position], cycles=[cycle])
    app.rule_state = SimpleNamespace(position_metrics=lambda *_a: {
        "current_net_pnl": 280000, "mae_pct": -.8830022, "mfe_pct": 3.53200883,
        "price_history_verified": verified, "market_data_policy": MATCHED_DATA_POLICY})
    try:
        app._render_tables({})
        iid, = tree.get_children()
        values = tree.item(iid, "values")
        pnl = values[RUNNING_COLUMNS.index("PnL_MAE_MFE")]
        assert "+2.06%" in pnl and "+3.09%" not in pnl and "12.91%" not in pnl
        if verified:
            assert "22,450" in pnl and "23,450" in pnl and "+3.53%" in pnl
        else:
            assert "CHỜ XÁC MINH" in pnl and "23,450" not in pnl
        assert "TỰ BÁN TẠM DỪNG" in values[RUNNING_COLUMNS.index("Status")]
        assert "Đã mua 600 CP · Bán khớp 200 CP" in tree._viking_column_hint.text_for(iid, "Order")
        assert "vốn mua gốc" in tree._viking_column_hint.text_for("", "PnL_MAE_MFE")
    finally:
        tree.destroy()


def test_management_popup_saves_hold_and_sl_off_without_altering_fills(ui_root, monkeypatch, tmp_path):
    import customtkinter as ctk
    from viking_v2.config import AppSettings
    from viking_v2.dashboard.actions import DashboardActionsMixin
    from support.tests.test_demo_order_display import _widgets
    trades, rules, queue, cycle = account(tmp_path)
    for name, value in {"trade_state": trades, "rule_state": rules, "queue": queue, "settings": AppSettings(),
        "_shared_tick": lambda *_a: g1(), "_row_time": lambda *_a: "14:09", "_log": lambda *_a: None,
        "_refresh_local": lambda: None}.items():
        monkeypatch.setattr(ui_root, name, value, raising=False)
    previous = set(ui_root.winfo_children())
    before = trades.get(cycle.id).to_dict()
    DashboardActionsMixin._show_position_management(ui_root, {"symbol": "HDB", "mode": "REAL", "trade_id": cycle.id,
        "position": {"openQuantity": 400, "tradeQuantity": 400, "costPrice": 22.65, "marketPrice": 23.1}})
    top, = [w for w in ui_root.winfo_children() if w not in previous and isinstance(w, ctk.CTkToplevel)]
    try:
        widgets = list(_widgets(top))
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "TỰ BÁN · BẬT").invoke()
        next(w for w in widgets if isinstance(w, ctk.CTkSwitch) and w.cget("text") == "SL").toggle()
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        after = trades.get(cycle.id).to_dict()
        assert set(after["em_modes"]) == set(before["em_modes"])
        assert after == {**before, "em_modes": after["em_modes"], "auto_sell_enabled": False, "sl_enabled": False}
    finally:
        if top.winfo_exists():
            top.destroy()


@pytest.mark.parametrize("paused", [False, True])
def test_bottom_preview_does_not_arm_from_unverified_peak(paused):
    from viking_v2.dashboard.panels import DashboardPanelsMixin
    app = DashboardPanelsMixin()
    app.settings = SimpleNamespace(rule_parameters={})
    app.mode = SimpleNamespace(get=lambda: "REAL")
    app.trade_state = SimpleNamespace(active_for=lambda *_a: SimpleNamespace(
        id="trade", open_quantity=400, avg_entry_price=22.65, em_modes=["NORMAL"], auto_sell_enabled=not paused))
    app.rule_state = SimpleNamespace(position_metrics=lambda *_a: {"peak_profit_pct": 8.60927, "price_history_verified": False})
    preview = app._dynamic_start_context("HDB", 23.1, {"normal_mfe_pct": 8.60927, "atr14_daily_pct": 2.38})
    assert preview["history_unverified"] is True and preview["mfe"] is None
    assert preview["state"] != "SAU ARM" and preview["auto_sell_paused"] is paused
    app._preview_dynamic_start = preview
    hint = app._protect_preview_hint()
    assert ("tạm dừng" if paused else "chưa xác minh") in hint


def test_stale_sell_decision_is_rejected_before_planner_after_operator_hold(tmp_path):
    app = headless_app(tmp_path, balance={"equity": 100e6, "availableCash": 100e6},
        positions=[{"symbol": "FPT", "openQuantity": 300, "tradeQuantity": 300, "costPrice": 100}], symbols=["FPT"])
    cycle = app.trade_state.create("FPT", "REAL")
    app.trade_state.record_buy_fill(cycle.id, 300, 100)
    old = StrategyDecision("SELL", "FPT", "STOP_LOSS", event="STOP_LOSS", scope="POSITION_MANAGEMENT",
                           details={"trade_id": cycle.id, "position_quantity": 300})
    pause_position_auto_sell(app.trade_state, app.queue, cycle.id, enabled=False)
    assert app._plan_rule_decision(old, g1(95, "FPT"), "REAL").reason == "POSITION_AUTO_SELL_PAUSED"
    assert app.queue.list_all() == []


def test_e_preview_uses_alert_while_auto_sell_is_paused():
    from viking_v2.dashboard.panels import DashboardPanelsMixin
    app = DashboardPanelsMixin()
    values = {}
    app.preview_exit_value = SimpleNamespace(configure=lambda **kw: values.update(value=kw.get("text")))
    app.preview_exit_detail = SimpleNamespace(configure=lambda **kw: values.update(detail=kw.get("text")))
    app._preview_dynamic_start = {"auto_sell_paused": True}
    app._current_tick_price = 23.1
    app._render_exit_sell_preview("SELL", True, 400, "AUTO")
    assert app._preview_exit_state["policy"] == "ALERT"
    assert "CHỈ BÁO" in values["detail"] and "100%" not in values["detail"]
