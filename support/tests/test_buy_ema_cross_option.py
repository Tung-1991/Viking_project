"""BUY fresh-cross opt-in: shared live/replay rule, persistence and UI."""
from copy import deepcopy

import pytest

from viking_v2 import config
from viking_v2.config import AppSettings
from viking_v2.rules.business import StaticRule, StaticRuleParameters, crossover_signal_from_snapshots


def marks(fast=101.0, slow=100.0, rsi=60.0):
    return {"buy_ema_fast": fast, "buy_ema_slow": slow,
            "sell_ema_fast": fast, "sell_ema_slow": slow,
            "rsi": rsi, "rsi_previous": 55.0, "sample_count": 30,
            "buy_ema_fast_period": 3, "buy_ema_slow_period": 6,
            "sell_ema_fast_period": 3, "sell_ema_slow_period": 6, "rsi_period": 14}


def test_new_and_sparse_settings_default_to_fresh_cross():
    assert config.DEFAULT_RULE_PARAMETERS["buy_signal_require_ema_cross"] is True
    assert StaticRuleParameters().buy_signal_require_ema_cross is True
    assert StaticRuleParameters.from_dict({}).buy_signal_require_ema_cross is True
    assert AppSettings.from_dict({"rule_parameters": {"buy_ema_fast": 3}}).rule_parameters["buy_signal_require_ema_cross"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_option_survives_rule_and_app_settings_roundtrip(enabled):
    params = StaticRuleParameters.from_dict({"buy_signal_require_ema_cross": enabled})
    assert StaticRuleParameters.from_dict(params.to_dict()).buy_signal_require_ema_cross is enabled
    app = AppSettings(rule_parameters=params.to_dict())
    assert AppSettings.from_dict(app.to_dict()).rule_parameters["buy_signal_require_ema_cross"] is enabled


@pytest.mark.parametrize("previous", [None, {}, marks(), marks(fast=102.0)])
def test_disabled_option_accepts_ema_already_above_without_requiring_previous_observation(previous):
    assert crossover_signal_from_snapshots(marks(fast=103.0), previous, buy_signal_require_ema_cross=False) == "BUY"


@pytest.mark.parametrize("previous,expected", [
    (None, ""), ({}, ""), (marks(fast=101), ""),
    (marks(fast=99), "BUY"), (marks(fast=100), "BUY"),
])
def test_enabled_option_requires_a_fresh_cross(previous, expected):
    assert crossover_signal_from_snapshots(marks(), previous, buy_signal_require_ema_cross=True) == expected


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("current", [marks(fast=100), marks(fast=99), marks(rsi=55), marks(rsi=54)])
def test_equality_or_failed_current_conditions_never_become_buy(enabled, current):
    assert crossover_signal_from_snapshots(current, marks(fast=99), buy_signal_require_ema_cross=enabled) != "BUY"


def test_option_is_ignored_when_buy_ema_is_disabled():
    assert crossover_signal_from_snapshots(
        {"rsi": 60, "rsi_previous": 55}, {},
        buy_use_ema=False, buy_signal_require_ema_cross=True,
    ) == "BUY"


@pytest.mark.parametrize("enabled", [False, True])
def test_buy_option_does_not_change_sell_cross_condition(enabled):
    current = marks(fast=99, rsi=50)
    assert crossover_signal_from_snapshots(current, marks(fast=101), buy_signal_require_ema_cross=enabled) == "SELL"
    assert crossover_signal_from_snapshots(current, marks(fast=98), buy_signal_require_ema_cross=enabled) == ""


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("enabled,expected", [(False, "BUY"), (True, "WAIT")])
def test_actual_rule_uses_option_for_already_above_ema_in_both_books(mode, enabled, expected):
    params = StaticRuleParameters(buy_signal_require_ema_cross=enabled, whipsaw_enabled=False)
    rule = StaticRule(params)
    decision = rule.evaluate({
        "symbol": "TEST", "bars": [{"close": 100, "closed": True}] * 30,
        "signal_mode": "REALTIME", "indicator_snapshot": marks(), "previous_indicators": marks(),
        "previous_market_state": "UPTREND", "execution_mode": mode,
    }, {"available_capital": 100_000_000, "open_positions": 0})
    assert decision.action == expected


@pytest.mark.parametrize("guard,reason", [
    ({"pending_buy": True}, "BUY_ALREADY_PENDING"),
    ({"loss_blocked": True}, "LOCKED_AFTER_LOSSES"),
    ({"entry_orders_available": False}, "MAX_SYMBOL_ORDERS"),
    ({"entry_slot_available": False}, "MAX_POSITIONS"),
    ({"available_capital": 0}, "NO_AVAILABLE_CAPITAL"),
])
def test_disabled_cross_level_mode_does_not_bypass_entry_guards(guard, reason):
    rule = StaticRule(StaticRuleParameters(whipsaw_enabled=False, buy_signal_require_ema_cross=False))
    decision = rule.evaluate({
        "symbol": "TEST", "bars": [{"close": 100, "closed": True}] * 30,
        "signal_mode": "REALTIME", "indicator_snapshot": marks(), "previous_indicators": {},
        "previous_market_state": "UPTREND",
    }, {"available_capital": 100_000_000, "open_positions": 0, **guard})
    assert decision.action == "WAIT"
    assert decision.reason == reason


@pytest.mark.parametrize("enabled", [False, True])
def test_rule_ui_option_is_explicit_and_saves_without_touching_other_settings(ui_root, monkeypatch, tmp_path, enabled):
    import customtkinter as ctk
    from viking_v2.rules.window import RuleSettingsPopup
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    settings = AppSettings()
    settings.rule_parameters["buy_signal_require_ema_cross"] = enabled
    untouched = deepcopy(settings.rule_parameters)
    popup = RuleSettingsPopup(ui_root, settings, "CROSS_OPTION_UI", lambda: None)
    try:
        variable = popup.buy_signal_require_ema_cross
        assert variable.get() is enabled
        switches = [child for child in variable._viking_row.winfo_children() if isinstance(child, ctk.CTkSwitch)]
        assert switches[0].cget("text") == "BUY CẦN EMA VỪA VƯỢT LÊN"
        variable.set(not enabled)
        popup.save()
        assert "ĐÃ LƯU" in popup.status.cget("text")
        saved = config.load_settings("CROSS_OPTION_UI").rule_parameters
        assert saved["buy_signal_require_ema_cross"] is not enabled
        for key in ("buy_signal_use_ema", "buy_signal_use_rsi", "sell_signal_use_ema", "sell_signal_use_rsi",
                    "whipsaw_enabled", "buy_window_enabled", "buy_confirmation_enabled"):
            assert saved[key] == untouched[key]
    finally:
        popup._close()


@pytest.mark.parametrize("enabled,text", [(False, "không bắt vừa vượt lên"), (True, "vừa vượt từ ≤ lên >")])
def test_preview_hint_explains_the_saved_buy_option(enabled, text):
    from viking_v2.dashboard.panels import DashboardPanelsMixin
    view = DashboardPanelsMixin()
    view.settings = AppSettings(rule_parameters={"buy_signal_require_ema_cross": enabled,
                                               "buy_signal_session_cross_enabled": False})
    assert text in view._indicator_preview_hint()


@pytest.mark.parametrize("enabled", [False, True])
def test_backtest_ui_syncs_and_collects_the_same_option(ui_root, monkeypatch, tmp_path, enabled):
    from viking_v2.backtest.window import BacktestPopup
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(config, "RUNTIME_ROOT", tmp_path / "runtime")
    settings = AppSettings(rule_parameters={"buy_signal_require_ema_cross": enabled})
    popup = BacktestPopup(ui_root, settings, None)
    try:
        monkeypatch.setattr(popup.data, "save_settings", lambda _values: None)
        popup._sync_from_bot()
        assert popup.buy_signal_require_ema_cross.get() is enabled
        assert popup._collect().rule_parameters["buy_signal_require_ema_cross"] is enabled
        popup.buy_signal_require_ema_cross.set(not enabled)
        assert popup._collect().rule_parameters["buy_signal_require_ema_cross"] is not enabled
    finally:
        popup.close()


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_repeated_level_signals_cannot_queue_duplicate_buys(tmp_path, mode):
    from viking_v2.rules.planner import StrategyOrderPlanner
    from viking_v2.rules.state import RuleStateStore
    from viking_v2.trading.orders import OrderQueue
    from viking_v2.trading.state import TradeStateStore
    queue = OrderQueue(tmp_path / "orders.json")
    planner = StrategyOrderPlanner(queue, TradeStateStore(tmp_path / "trades.json"), RuleStateStore(tmp_path / "rules.json"))
    rule = StaticRule(StaticRuleParameters(whipsaw_enabled=False, buy_signal_require_ema_cross=False))
    context = {"symbol": "TEST", "bars": [{"close": 100, "closed": True}] * 30,
               "signal_mode": "REALTIME", "indicator_snapshot": marks(), "previous_indicators": marks(),
               "previous_market_state": "UPTREND"}
    portfolio = {"available_capital": 25_000_000, "available_cash": 50_000_000,
                 "nav": 50_000_000, "order_budget": 25_000_000, "open_positions": 0}
    for attempt in range(2):
        decision = rule.evaluate(context, portfolio)
        assert decision.action == "BUY"
        result = planner.plan(decision, execution_mode=mode, execution_style="MARKET",
                              tick={"ask": 100}, portfolio=portfolio, candle_key="2026-10-09|same-signal")
        if attempt == 0:
            assert result.intent is not None
        else:
            assert result.intent is None and result.reason == "BUY_ALREADY_PENDING"
    assert len(queue.list_all()) == 1


@pytest.mark.parametrize("reason", ["BOT_OFF", "MANUAL_SELL_PAUSE"])
@pytest.mark.parametrize("require_cross", [False, True])
@pytest.mark.parametrize("use_ema", [False, True])
def test_operator_off_or_pause_only_consumes_the_signal_in_fresh_cross_mode(tmp_path, reason, require_cross, use_ema):
    from types import SimpleNamespace
    from viking_v2.models import StrategyDecision
    from viking_v2.dashboard.actions import DashboardActionsMixin
    from viking_v2.rules.state import RuleStateStore
    state = RuleStateStore(tmp_path / "rules.json")
    cycle = "2026-10-09|2026-10-09T14:00:00+07:00"
    subject = SimpleNamespace(settings=AppSettings(rule_parameters={"buy_signal_require_ema_cross": require_cross,
                                                                    "buy_signal_use_ema": use_ema}),
                              rule_state=state)
    blocked = StrategyDecision("WAIT", "TEST", reason, signal="BUY", details={"signal_cycle": cycle})
    DashboardActionsMixin._claim_terminal_buy(subject, blocked, "PAPER")
    assert state.claim_signal("TEST", "BUY", cycle, stream="PAPER") is (not (require_cross and use_ema))


@pytest.mark.parametrize("require_cross", [False, True])
def test_restart_rechecks_levels_without_reusing_old_candidate_or_consumed_id(tmp_path, require_cross):
    from viking_v2.rules.state import RuleStateStore
    state = RuleStateStore(tmp_path / "rules.json")
    old_time, new_time = "2026-10-09T13:40:00+07:00", "2026-10-09T14:00:00+07:00"
    old = state.observe_signal_time("TEST", "PAPER", "BUY", "2026-10-09", old_time)
    state.save_buy_confirmation("TEST", "PAPER", {"window": {"signal_time": old_time}})
    state.start_entry_pause("PAPER", 900)
    state.discard_buy_candidates(recheck_current_conditions=not require_cross)
    assert state.buy_confirmation("TEST", "PAPER") == {}
    assert state.entry_pause("PAPER")["active"]
    assert not state.claim_signal("TEST", "BUY", f"2026-10-09|{old}", stream="PAPER")
    observed = state.observe_signal_time("TEST", "PAPER", "BUY", "2026-10-09", new_time)
    assert observed == (old_time if require_cross else new_time)
