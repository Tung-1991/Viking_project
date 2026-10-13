"""Regressions for stale NAV prices, premature PROTECT and settlement RECHECK."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.account_money import account_money, account_money_hint, account_valuation_quotes, close_quote_from_ohlc
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.state import RuleStateStore
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.services.daemon import remember_last_valid_tick
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue


NOW = datetime(2026, 10, 13, 12, 10, tzinfo=VN_TZ).timestamp()


def test_dnse_same_day_close_replaces_stale_position_price_in_display_nav():
    closed_at = datetime(2026, 10, 13, 15, 7, tzinfo=VN_TZ).timestamp()
    balance = {"stock": {"totalCash": 36392140, "availableCash": 36390023, "totalDebt": 0}}
    positions = [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": 0,
                  "marketPrice": 22600, "price_unit": "VND"}]
    quote = close_quote_from_ohlc("HDB", {"t": [closed_at - 1800], "c": [23.1]}, now=closed_at)
    assert quote and quote["source"] == "CLOSE" and quote["frozen"]
    money = account_money(balance, positions, {"HDB": quote}, now=closed_at)
    assert money["value"] == 50252140
    assert "HDB: 23,100đ × 600 CP (giá đóng cửa DNSE)" in account_money_hint(money)
    assert account_money(balance, positions, now=closed_at)["value"] == 49952140
    assert close_quote_from_ohlc("HDB", {"t": [closed_at - 86400], "c": [23.1]}, now=closed_at) is None
    assert close_quote_from_ohlc("HDB", {"t": [closed_at], "c": [0]}, now=closed_at) is None


def test_nav_holds_final_valid_websocket_price_after_close_and_restart():
    closed_at = datetime(2026, 10, 13, 15, 7, tzinfo=VN_TZ).timestamp()
    price_at = closed_at - 1200
    last = {"symbol": "HDB", "price": 23.15, "timestamp": price_at, "source": "WS"}
    status = {"market_status": "CLOSED", "ticks": {"HDB": {**last, "stale": True}},
              "last_valid_ticks": {"HDB": last}}
    close = {"HDB": close_quote_from_ohlc("HDB", {"t": [closed_at - 900], "c": [23.1]}, now=closed_at)}
    selected = account_valuation_quotes(status, close, now=closed_at)
    assert selected["HDB"]["price"] == 23.15
    assert selected["HDB"]["source"] == "WS_LAST" and selected["HDB"]["frozen"]
    assert not selected["HDB"].get("stale")
    balance = {"stock": {"totalCash": 36392140, "totalDebt": 0}}
    positions = [{"symbol": "HDB", "openQuantity": 600, "marketPrice": 22600}]
    money = account_money(balance, positions, selected, now=closed_at)
    assert money["value"] == 50282140
    assert "giá WebSocket cuối phiên" in account_money_hint(money)
    assert status["ticks"]["HDB"]["stale"]  # Display valuation does not repair trading state.


def test_nav_live_websocket_updates_and_falls_back_only_without_valid_session_tick():
    closed_at = datetime(2026, 10, 13, 15, 7, tzinfo=VN_TZ).timestamp()
    last = {"symbol": "HDB", "price": 23.05, "timestamp": closed_at - 600, "source": "WS"}
    fresh = {"symbol": "HDB", "price": 23.15, "timestamp": closed_at - 1, "source": "WS"}
    close = {"HDB": close_quote_from_ohlc("HDB", {"t": [closed_at - 900], "c": [23.1]}, now=closed_at)}
    status = {"market_status": "OPEN", "ticks": {"HDB": fresh}, "last_valid_ticks": {"HDB": last}}
    assert account_valuation_quotes(status, close, now=closed_at)["HDB"]["price"] == 23.15
    status["ticks"]["HDB"] = {**fresh, "stale": True}
    held = account_valuation_quotes(status, close, now=closed_at)["HDB"]
    assert held["price"] == 23.05 and held["source"] == "WS_HELD"
    status["ticks"]["HDB"] = fresh
    status["market_status"] = "CLOSED"
    assert account_valuation_quotes(status, close, now=closed_at)["HDB"]["price"] == 23.05
    status["last_valid_ticks"]["HDB"] = {**last, "stale": True}
    assert account_valuation_quotes(status, close, now=closed_at)["HDB"]["price"] == 23.15
    status["ticks"]["HDB"] = {**fresh, "stale": True}
    assert account_valuation_quotes(status, close, now=closed_at)["HDB"]["source"] == "CLOSE"
    status["last_valid_ticks"]["HDB"] = {**last, "timestamp": closed_at - 86400}
    assert account_valuation_quotes(status, close, now=closed_at)["HDB"]["source"] == "CLOSE"


def test_last_valid_session_tick_prefers_ws_and_ignores_bad_observations():
    checked = datetime(2026, 10, 13, 14, 29, tzinfo=VN_TZ).timestamp()
    cache = {}
    ws = {"symbol": "HDB", "price": 23.1, "timestamp": checked - 5, "source": "WS"}
    remember_last_valid_tick(cache, "HDB", ws, now=checked)
    remember_last_valid_tick(cache, "HDB", {**ws, "price": 23.2, "source": "REST", "timestamp": checked - 5}, now=checked)
    remember_last_valid_tick(cache, "HDB", {**ws, "price": 23.3, "timestamp": checked, "stale": True}, now=checked)
    remember_last_valid_tick(cache, "HDB", {**ws, "price": 23.4, "timestamp": checked,
                                            "price_frozen": True}, now=checked)
    assert cache["HDB"]["price"] == 23.1
    remember_last_valid_tick(cache, "HDB", {**ws, "price": 23.15, "timestamp": checked}, now=checked)
    assert cache["HDB"]["price"] == 23.15
    remember_last_valid_tick(cache, "HDB", {**ws, "price": 23.2, "source": "REST", "timestamp": checked + 1},
                             now=checked + 1)
    assert cache["HDB"]["price"] == 23.2
    remember_last_valid_tick(cache, "HDB", {**ws, "timestamp": checked - 86400}, now=checked)
    assert cache["HDB"]["price"] == 23.2


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
    from viking_v2.trading.market import market_now
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
                    "daily_count_day": market_now().date().isoformat(),
                    "daily_up_count": 2, "daily_down_count": 1,
                    "previous_fast": 22.6, "previous_slow": 22.3}
        subject._render_ema_cross_preview({"ema_cross": evidence})
        ui_root.update_idletasks()
        assert subject.preview_rule_cross.master._viking_cross_narrow == (
            subject.preview_rule_cross.master.winfo_width()
            / subject.preview_rule_cross.master._get_widget_scaling() < 440
        )
        for widget in (subject.preview_rule_cross_counts, subject.preview_rule_cross,
                       subject.preview_rule_cross_title, subject.preview_rule_cross_detail):
            assert widget.winfo_manager() == "grid"
            assert widget.winfo_x() >= 0
            assert widget.winfo_x() + widget.winfo_width() <= widget.master.winfo_width()
            assert widget.winfo_y() + widget.winfo_height() <= widget.master.winfo_height()
        assert subject.preview_rule_cross_counts.cget("text") == "3 CẮT - 0P"
        assert subject.preview_rule_cross_counts.cget("font")[1] == 14
        assert subject.preview_rule_cross.cget("font")[1] == 11
        assert subject.preview_rule_ema.cget("font")[1] == 12
        subject._render_ema_cross_preview({"ema_cross": {
            "tracking_version": 2, "initialized": True, "current_ready": True,
            "current_fast": 19.35, "current_slow": 19.59,
            "up_count": 0, "down_count": 0,
        }})
        ui_root.update_idletasks()
        assert subject.preview_rule_cross.cget("text") == (
            "CHỜ CẮT LÊN" if subject.preview_rule_cross.master._viking_cross_narrow else "CHỜ EMA3 > EMA6"
        )
        assert subject.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        assert subject.preview_rule_cross_detail.cget("text") == (
            "CHƯA GHI NHẬN" if subject.preview_rule_cross.master._viking_cross_narrow else "TRƯỚC: —"
        )
        assert "⏸" not in subject.preview_rule_cross.cget("text")
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


@pytest.mark.parametrize("mode", ["VA", "SESSIONS"])
@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
@pytest.mark.parametrize("required,width", [(False, 1000), (True, 1000), (True, 1600)])
def test_preview_can_swap_cached_va_and_history_without_changing_rules(ui_root, monkeypatch, mode, scale, required, width):
    from copy import deepcopy
    from datetime import datetime
    import customtkinter as ctk
    from viking_v2.trading.market import VN_TZ

    ctk.set_widget_scaling(scale)
    now = datetime(2026, 10, 13, 13, tzinfo=VN_TZ)
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: now)
    top = ctk.CTkToplevel(ui_root)
    top.geometry(f"{width}x720+0+0")
    subject = DashboardPanelsMixin()
    subject.settings = AppSettings()
    subject.settings.rule_parameters.update(buy_signal_require_ema_cross=required, buy_ema_cross_mode=mode)
    subject._build_order_preview_tab(top)
    source = {"tracking_version": 2, "initialized": True, "current_ready": True,
              "started_at": "2026-10-12T10:00:00+07:00", "observed_at": "2026-10-13T13:00:00+07:00",
              "lookback_started_at": "2026-10-08T00:00:00+07:00",
              "current_fast": 23, "current_slow": 22, "up_count": 0, "down_count": 0,
              "lookback_ready": True, "lookback_up_count": 1, "lookback_down_count": 0,
              "lookback_cross": {"cross_id": "EMA:HISTORY:TEST", "cross_at": "2026-10-12T14:10:00+07:00",
                                  "cross_timestamp": now.timestamp()-86400, "used": False,
                                  "previous_fast": 250.12, "previous_slow": 250.42}}
    details = {"ema_memory_preview_source": source}
    before = deepcopy((subject.settings.to_dict(), details))
    try:
        top.deiconify()
        top.update()
        subject._render_ema_cross_preview(details)
        from tkinter import font as tkfont
        positions = None
        for alternate in (False, True, False):
            if getattr(subject, "_preview_ema_alternate", False) != alternate:
                subject.preview_ema_swap.invoke()
            ui_root.update_idletasks()
            widgets = (subject.preview_rule_cross.master, subject.preview_rule_cross_title.master,
                       subject.preview_rule_cross_title, subject.preview_ema_swap,
                       subject.preview_rule_cross_counts, subject.preview_rule_cross,
                       subject.preview_rule_cross_detail,
                       subject.preview_rule_ema, subject.preview_rule_rsi,
                       subject.preview_focus_panel, subject.preview_health_group)
            current_positions = tuple((w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())
                                      for w in widgets)
            if positions is None:
                positions = current_positions
            else:
                assert current_positions == positions
            for label in (subject.preview_rule_cross_title, subject.preview_rule_cross_counts,
                          subject.preview_rule_cross, subject.preview_rule_cross_detail):
                rendered = tkfont.Font(root=ui_root, font=label._label.cget("font"))
                assert rendered.measure(label.cget("text")) <= label.winfo_width()
            evidence = subject._preview_ema_cross_evidence[1]
            viewing_history = (mode == "SESSIONS") != alternate
            assert evidence["mode"] == ("SESSIONS" if viewing_history else "VA")
            assert evidence["valid"] == viewing_history
            assert subject.preview_rule_cross_counts.cget("text") == ("1 CẮT - 3P" if viewing_history else "0 CẮT - 0P")
            assert subject.preview_rule_cross_title.cget("text") == ("⏳ 3P" if viewing_history else "∞ VA")
            assert int(subject.preview_rule_cross.grid_info()["column"]) == 2
            assert int(subject.preview_rule_cross_detail.grid_info()["column"]) == 2
            if subject.preview_rule_cross.master._viking_cross_narrow and viewing_history:
                assert subject.preview_rule_cross_detail.cget("text") == ("CẮT 12/10" if required else "OFF · 12/10")
            assert (subject.settings.to_dict(), details) == before
    finally:
        top.destroy()
        ctk.set_widget_scaling(1.0)


def test_preview_counts_today_without_losing_yesterdays_unused_cross(ui_root, monkeypatch):
    from copy import deepcopy
    from datetime import datetime, timedelta
    import customtkinter as ctk
    from viking_v2.trading.market import VN_TZ
    now = datetime(2026, 10, 13, 14, tzinfo=VN_TZ)
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: now)
    top = ctk.CTkToplevel(ui_root)
    subject = DashboardPanelsMixin()
    subject.settings = AppSettings()
    subject._build_order_preview_tab(top)
    source = {"tracking_version": 2, "initialized": True, "current_ready": True,
              "started_at": "2026-10-07T09:00:00+07:00", "observed_at": now.isoformat(),
              "current_fast": 23, "current_slow": 22, "up_count": 99, "down_count": 98,
              "daily_count_day": "2026-10-13", "daily_up_count": 19, "daily_down_count": 18,
              "count_days": {"2026-10-07": {"up": 79, "down": 79},
                             "2026-10-12": {"up": 1, "down": 1},
                             "2026-10-13": {"up": 19, "down": 18}},
              "cross_id": "EMA:UNUSED", "cross_at": "2026-10-12T14:10:00+07:00"}
    before = deepcopy(source)
    try:
        subject._render_ema_cross_preview({"ema_cross": source})
        assert subject.preview_rule_cross_counts.cget("text") == "37 CẮT - 0P"
        assert "Hôm nay: 19 lên + 18 xuống" in subject._ema_memory_preview_hint()
        assert "0P: chỉ đếm phiên hôm nay" in subject._ema_memory_preview_hint()
        subject.settings.rule_parameters['buy_ema_va_lookback_enabled'] = True
        subject._render_ema_cross_preview({"ema_cross": source})
        assert subject.preview_rule_cross_counts.cget("text") == "39 CẮT - 3P"
        hint = subject._ema_memory_preview_hint()
        assert '2026-10-08 → 2026-10-13' in hint
        assert '3P: hôm nay + 3 phiên trước' in hint
        assert subject._preview_ema_cross_evidence[1]['cross_id'] == source['cross_id']
        subject.settings.rule_parameters['buy_ema_va_lookback_enabled'] = False
        monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: now + timedelta(days=1))
        subject._render_ema_cross_preview({"ema_cross": source})
        assert subject.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        assert subject._preview_ema_cross_evidence[1]["valid"]
        assert subject._preview_ema_cross_evidence[1]["cross_id"] == source["cross_id"]
        assert source == before
    finally:
        top.destroy()


def test_ema_prerequisite_shows_initial_wait_cross_and_consumption_even_after_close(ui_root, monkeypatch):
    from copy import deepcopy
    from datetime import datetime, timedelta
    import customtkinter as ctk
    from viking_v2.rules.ema_memory import advance_memory
    from viking_v2.trading.market import VN_TZ
    now = datetime(2026, 10, 13, 13, tzinfo=VN_TZ)
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: now)
    top = ctk.CTkToplevel(ui_root)
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view._build_order_preview_tab(top)
    try:
        args = dict(now=now, pair="3/6", mode="VA", sessions=3, exchange="HOSE")
        below = advance_memory({}, {"buy_ema_fast": 99, "buy_ema_slow": 100}, **args)
        view._render_ema_cross_preview({"ema_cross": below})
        assert view.preview_ema_entry_gate.cget("text") == "EMA CẮT: CHƯA ĐẠT"
        assert view.preview_rule_cross.cget("text") == "CHỜ EMA3 > EMA6"
        assert "Mẫu đầu: EMA3 < EMA6. Chỉ cần một lần cắt lên." in view._ema_entry_gate_hint()
        above = advance_memory(below, {"buy_ema_fast": 101, "buy_ema_slow": 100},
                               **{**args, "now": now + timedelta(seconds=1)})
        original = deepcopy(above)
        for closed in (False, True):
            view._render_ema_cross_preview({"ema_cross": above, "ema_cross_preview_closed": closed})
            assert view.preview_ema_entry_gate.cget("text") == "EMA CẮT: ĐẠT"
            assert view.preview_rule_cross.cget("text") == "ĐÃ CẮT LÊN"
            if closed:
                assert not view._preview_ema_cross_evidence[1]["valid"]
        assert above == original
        view._render_ema_cross_preview({"ema_cross": {**above, "used": True}})
        assert view.preview_ema_entry_gate.cget("text") == "EMA CẮT: CHƯA ĐẠT"
        assert view.preview_rule_cross.cget("text") == "ĐÃ DÙNG"
        first_above = advance_memory({}, {"buy_ema_fast": 101, "buy_ema_slow": 100}, **args)
        view._render_ema_cross_preview({"ema_cross": first_above})
        assert view.preview_ema_entry_gate.cget("text") == "EMA CẮT: CHƯA ĐẠT"
        assert "Mẫu đầu: EMA3 > EMA6. Chưa có lần cắt: chờ xuống rồi lên." in view._ema_entry_gate_hint()
        view.settings.rule_parameters["buy_signal_require_ema_cross"] = False
        view._render_ema_cross_preview({"ema_cross": above})
        assert view.preview_ema_entry_gate.cget("text") == "EMA CẮT: OFF"
    finally:
        top.destroy()


def test_alternate_history_preview_marks_absent_cache_as_unknown(ui_root, monkeypatch):
    from datetime import datetime
    import customtkinter as ctk
    from viking_v2.trading.market import VN_TZ
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: datetime(2026, 10, 13, 13, tzinfo=VN_TZ))
    top = ctk.CTkToplevel(ui_root)
    subject = DashboardPanelsMixin()
    subject.settings = AppSettings()
    subject.settings.rule_parameters["buy_ema_cross_mode"] = "VA"
    subject._build_order_preview_tab(top)
    try:
        subject._render_ema_cross_preview({"ema_cross": {"tracking_version": 2, "initialized": True,
            "current_ready": True, "current_fast": 23, "current_slow": 22, "up_count": 0, "down_count": 0}})
        subject.preview_ema_swap.invoke()
        assert subject.preview_rule_cross_counts.cget("text") == "— CẮT - 3P"
        assert subject._preview_ema_cross_evidence[1]["state"] == "UNKNOWN"
        assert not subject._preview_ema_cross_evidence[1]["valid"]
        assert "CHỜ DỮ LIỆU" in subject.preview_rule_cross.cget("text")
    finally:
        top.destroy()


@pytest.mark.parametrize("closed,state,valid", [(False, "SESSION_ACTIVE", True), (True, "MARKET_CLOSED", False)])
def test_saved_version_two_live_cross_keeps_identity_and_respects_closed_preview(ui_root, monkeypatch, closed, state, valid):
    from copy import deepcopy
    from datetime import datetime
    import customtkinter as ctk
    from viking_v2.trading.market import VN_TZ
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: datetime(2026, 10, 13, 13, tzinfo=VN_TZ))
    top = ctk.CTkToplevel(ui_root)
    subject = DashboardPanelsMixin()
    subject.settings = AppSettings()
    subject.settings.rule_parameters["buy_ema_cross_mode"] = "VA"
    subject._build_order_preview_tab(top)
    source = {"tracking_version": 2, "initialized": True, "current_ready": True,
              "current_fast": 23, "current_slow": 22, "up_count": 1, "down_count": 0,
              "cross_id": "EMA:OLD-V2:LIVE", "cross_at": "2026-10-12T14:10:00+07:00"}
    before = deepcopy(source)
    try:
        subject._render_ema_cross_preview({"ema_cross": source, "ema_cross_preview_closed": closed})
        subject.preview_ema_swap.invoke()
        evidence = subject._preview_ema_cross_evidence[1]
        assert evidence["cross_id"] == source["cross_id"] and evidence["cross_source"] == "LIVE"
        assert evidence["state"] == state and evidence["valid"] == valid
        assert source == before
    finally:
        top.destroy()
