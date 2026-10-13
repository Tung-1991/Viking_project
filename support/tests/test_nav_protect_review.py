"""Regressions for stale NAV prices, premature PROTECT and settlement RECHECK."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.account_money import account_money
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.state import RuleStateStore
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue


NOW = datetime(2026, 10, 13, 12, 10, tzinfo=VN_TZ).timestamp()


@pytest.mark.parametrize("frozen", [False, True])
def test_nav_uses_latest_hdb_price_including_unsettled_shares(frozen):
    balance = {"stock": {"totalCash": 36392140, "availableCash": 36390023, "totalDebt": 0}}
    positions = [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": 0,
                  "marketPrice": 22600, "price_unit": "VND"}]
    quotes = {"HDB": {"symbol": "HDB", "price": 23.15, "timestamp": NOW - (3600 if frozen else 1),
                      "frozen": frozen, "source": "WS"}}
    original = deepcopy((balance, positions, quotes))
    result = account_money(balance, positions, quotes, now=NOW)
    assert result["value"] == 50282140
    assert result["stock_value"] == 13890000 and result["available"] == 36390023
    assert result["quote_symbols"] == ["HDB"]
    assert (balance, positions, quotes) == original
    balance["netAssetValue"] = 49952140
    assert account_money(balance, positions, quotes, now=NOW)["value"] == 49952140


@pytest.mark.parametrize("updates", [
    {"stale": True}, {"health": "REST_UNAVAILABLE"}, {"symbol": "OTHER"},
    {"timestamp": NOW - 86400, "frozen": True}, {"timestamp": NOW + 60},
    {"timestamp": 0}, {"price": "nan"}, {"timestamp": NOW - 3600},
])
def test_nav_rejects_unusable_quotes_and_retains_broker_valuation(updates):
    quotes = {"HDB": {"symbol": "HDB", "price": 23.15, "timestamp": NOW, **updates}}
    result = account_money({"stock": {"totalCash": 36392140, "totalDebt": 0}},
                           [{"symbol": "HDB", "openQuantity": 600, "marketPrice": 22600}], quotes, now=NOW)
    assert result["value"] == 49952140 and not result["quote_symbols"]


def protection_decision(*, policy="AUTO", trade_quantity=0, current=103.0, peak=4, **metrics):
    rule = StaticRule(StaticRuleParameters(normal_policy=policy, normal_atr_activation_enabled=False,
                                          normal_atr_trail_enabled=False))
    position = {"quantity": 600, "avg_price": 100, "current_price": current,
                "peak_profit_pct": peak, "em_modes": ["NORMAL"], "sl_enabled": False,
                "trade_quantity": trade_quantity, "normal_armed": peak >= 7, **metrics}
    return rule.evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"},
                         {"position": position, "position_quantity": 600})


@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
@pytest.mark.parametrize("peak,current,event", [(4, 102.5, "DYNAMIC_PROTECTION"), (10, 107, "NORMAL_PROTECTION")])
def test_unsettled_protect_does_not_create_sell_or_hit(policy, peak, current, event):
    waiting = protection_decision(policy=policy, peak=peak, current=current)
    assert waiting.action == "WAIT" and waiting.event not in {"PROTECT_ALERT", "PRICE_PROTECTION"}
    assert waiting.reason == "PROTECT_WAITING_SETTLEMENT"
    assert waiting.details["protection_waiting_event"] == event
    ready = protection_decision(policy=policy, trade_quantity=600, peak=peak, current=current)
    assert (ready.action, ready.event) == (("SELL", "PRICE_PROTECTION") if policy == "AUTO" else ("WAIT", "PROTECT_ALERT"))


def test_retention_edit_rebases_saved_floor_and_survives_restart(tmp_path):
    state = RuleStateStore(tmp_path / "rules.json")
    state.update_position_metrics("HDB", "T", profit_pct=4, market_price=104)
    state.update_protect_metrics("HDB", "T", stage="DYN", trigger_price=103.6,
                                 atr_pct=0, atr_multiplier=.8, retention_pct=90)
    edited = protection_decision(trade_quantity=600, current=103, **state.position_metrics("HDB", "T"))
    assert edited.action == "WAIT"
    assert edited.details["normal_trigger_price"] == pytest.approx(102.88)
    state.update_protect_metrics("HDB", "T", stage="DYN", trigger_price=102.88,
                                 atr_pct=0, atr_multiplier=.8, retention_pct=72)
    metrics = RuleStateStore(tmp_path / "rules.json").position_metrics("HDB", "T")
    assert metrics["dynamic_trigger_price"] == pytest.approx(102.88)
    state.update_protect_metrics("HDB", "T", stage="DYN", trigger_price=102.5,
                                 atr_pct=5, atr_multiplier=.8, retention_pct=72)
    assert state.position_metrics("HDB", "T")["dynamic_trigger_price"] == pytest.approx(102.88)


@pytest.mark.parametrize("policy", ["RECHECK", "KEEP"])
@pytest.mark.parametrize("decision", ["RECOVERED", "MISSING", "STILL_HIT"])
def test_recheck_can_cancel_before_stock_arrives_without_manual_action(tmp_path, policy, decision):
    queue = OrderQueue(tmp_path / "queue.json")
    intent = OrderIntent.create("HDB", "SELL", 200, "MARKET", execution_mode="REAL", source="EM",
                                reason="DYNAMIC_PROTECTION", sell_wait_policy=policy)
    intent.status, intent.settlement_waited = "WAITING_SETTLEMENT", True
    queue.add(intent)
    broker = SimpleNamespace(has_trading_token=lambda: True,
                             get_positions=lambda **_: [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": 0}])
    current = (None if decision == "MISSING" else protection_decision(current=103 if decision == "RECOVERED" else 102.5))
    execution = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "events.jsonl"),
                                 sell_decision_provider=lambda *_: current)
    execution.process_due(phase="OPEN", execution_mode="REAL")
    expected = "CANCELLED" if policy == "RECHECK" and decision == "RECOVERED" else "WAITING_SETTLEMENT"
    assert queue.get(intent.id).status == expected


def test_arriving_stock_waits_for_sell_decision_instead_of_using_wait_fraction(tmp_path):
    queue = OrderQueue(tmp_path / "queue.json")
    intent = OrderIntent.create("HDB", "SELL", 100, "MARKET", execution_mode="REAL", source="EM",
                                reason="DYNAMIC_PROTECTION", sell_wait_policy="RECHECK")
    intent.status, intent.settlement_waited = "WAITING_SETTLEMENT", True
    queue.add(intent)
    broker = SimpleNamespace(has_trading_token=lambda: True,
                             get_positions=lambda **_: [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": 600}])
    waiting = protection_decision(current=102.5)
    execution = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "events.jsonl"),
                                 sell_decision_provider=lambda *_: waiting)
    execution.process_due(phase="OPEN", execution_mode="REAL")
    assert queue.get(intent.id).status == "WAITING_SETTLEMENT"
    assert queue.get(intent.id).quantity == 100


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_ema_preview_keeps_mode_counts_and_details_visible_at_narrow_width(ui_root, scale):
    import customtkinter as ctk
    ctk.set_widget_scaling(scale)
    top = ctk.CTkToplevel(ui_root)
    top.geometry("1000x720+0+0")
    parent = ctk.CTkFrame(top)
    parent.pack(fill="both", expand=True)
    parent.grid_columnconfigure(0, weight=1)
    subject = DashboardPanelsMixin()
    subject.settings = AppSettings()
    subject._build_order_preview_tab(parent)
    try:
        top.deiconify()
        top.update()
        subject._fit_phase2_row()
        evidence = {"tracking_version": 2, "mode": "VA", "state": "SESSION_ACTIVE", "required": True,
                    "up_count": 2, "down_count": 1, "ready": True, "valid": True, "above": True,
                    "previous_fast": 22.6, "previous_slow": 22.3}
        subject._render_ema_cross_preview({"ema_cross": evidence})
        ui_root.update_idletasks()
        assert subject.preview_rule_cross.master._viking_cross_narrow
        for widget in (subject.preview_rule_cross, subject.preview_rule_cross_title, subject.preview_rule_cross_detail):
            assert widget.winfo_manager() == "grid"
            assert widget.winfo_x() >= 0
            assert widget.winfo_x() + widget.winfo_width() <= widget.master.winfo_width()
            assert widget.winfo_y() + widget.winfo_height() <= widget.master.winfo_height()
        assert "↑2" in subject.preview_rule_cross.cget("text") and "↓1" in subject.preview_rule_cross.cget("text")
        assert subject.preview_rule_cross.cget("font")[1] == 11
        assert subject.preview_rule_ema.cget("font")[1] == 12
    finally:
        top.destroy()
        ctk.set_widget_scaling(1.0)


def test_protect_policy_is_common_to_arm_and_dynamic_and_saves_alert(ui_root, tmp_path, monkeypatch):
    from viking_v2 import config
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    popup = RuleSettingsPopup(ui_root, AppSettings(), "PROTECT_MODE", lambda: None)
    try:
        assert popup.normal_policy_menu.master.master is popup.protect_card
        popup.normal_policy.set("ALERT")
        popup.save()
        settings = config.load_settings("PROTECT_MODE")
        assert settings.rule_parameters["normal_policy"] == "ALERT"
        assert settings.rule_parameters["normal_retention_pct"] == 72
    finally:
        popup._close()
