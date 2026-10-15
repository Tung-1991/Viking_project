"""Regression scenarios from the HDB review; fake broker/Telegram only."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings, TELEGRAM_NOTIFICATION_GROUPS
from viking_v2.dashboard.ema_history import view_counts
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.ema_memory import advance_memory, refresh_memory
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.exit_notifications import exit_condition_cleared, settlement_exit_snapshot
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.position_history import load_position_history
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore
from support.tests.test_position_history import HistoryClient, historical_account, payload, stamp
from support.tests.test_telegram_buy_lost import subject, observe
from support.tests.telegram_support import TELEGRAM_START


@pytest.mark.parametrize("empty", [payload([]), {"s": "no_data"}])
def test_ato_keeps_prior_sessions_then_loads_today_without_changing_fills(tmp_path, empty):
    trades, rules, _, cycle = historical_account(tmp_path)
    client = HistoryClient()
    client.minutes[14] = deepcopy(empty)
    before = trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)
    history = load_position_history(client, cycle,
        now=datetime.fromtimestamp(stamp(14, "09:10:00"), VN_TZ), exchange="HOSE")
    assert history["loaded_sessions"] == 2
    assert history["session_dates"] == ["2026-10-12", "2026-10-13"]
    assert history["pending_sessions"] == ["2026-10-14"]
    assert history["high"] == 24 and history["low"] == 21
    client.minutes[14] = payload([(stamp(14, "09:30:00"), 23, 25, 20, 23, 100)])
    later = load_position_history(client, cycle,
        now=datetime.fromtimestamp(stamp(14, "09:31:00"), VN_TZ), exchange="HOSE")
    assert later["loaded_sessions"] == 3 and not later["pending_sessions"]
    assert (later["high"], later["low"]) == (25, 20)
    assert (trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)) == before


def test_completed_session_missing_candles_is_never_skipped(tmp_path):
    _, _, _, cycle = historical_account(tmp_path)
    client = HistoryClient()
    client.minutes[14] = {"s": "no_data"}
    with pytest.raises(ValueError, match="Thiếu nến phút"):
        load_position_history(client, cycle,
            now=datetime.fromtimestamp(stamp(14, "14:45:00"), VN_TZ), exchange="HOSE")


def test_va_remembers_october_13_cross_on_october_15_without_mutation():
    args = dict(pair="3/6", mode="VA", sessions=3, exchange="HOSE")
    now = datetime(2026, 10, 13, 13, 43, tzinfo=VN_TZ)
    row = advance_memory({}, {"buy_ema_fast": 22, "buy_ema_slow": 23}, now=now, **args)
    row = advance_memory(row, {"buy_ema_fast": 23, "buy_ema_slow": 22}, now=now.replace(minute=44), **args)
    today = now.replace(day=15, hour=9)
    row = refresh_memory(row, now=today, mode="VA", sessions=3, exchange="HOSE")
    before = deepcopy(row)
    count = view_counts(row, now=today, prior_sessions=0, holidays=[])
    assert count["up"] == 1 and count["down"] == 0 and count["retained"]
    assert row["valid"] and "13:44" in row["cross_at"]
    assert row == before


def protect_event(occurrence="event-1"):
    return StrategyDecision("WAIT", "HDB", "PROTECT_ALERT", event="PROTECT_ALERT", details={
        "trade_id": "trade", "protection_stage": "DYN", "protect_occurrence": occurrence,
        "current_price": 23, "normal_peak_price": 23.2, "normal_trigger_price": 23.046,
        "normal_policy": "ALERT", "normal_mfe_pct": 2.43})


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("family", ["DYN", "E"])
def test_protect_and_paused_notices_share_cooldown_across_restart(subject, monkeypatch, mode, family):
    app, telegram = subject
    app.settings.telegram_cooldown_minutes["dynamic_protect" if family == "DYN" else "indicator_exit"] = 10
    event = protect_event() if family == "DYN" else StrategyDecision(
        "WAIT", "HDB", "INDICATOR_EXIT_ALERT", event="INDICATOR_EXIT_ALERT",
        details={"trade_id": "trade", "signal_cycle": "event-1", "current_price": 23})
    app._notify_rule_signal("HDB", event, {"price": 23}, execution_mode=mode)
    assert len(telegram.sent) == 1
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    position = {"quantity": 400, "trade_quantity": 400, "auto_sell_enabled": False}
    event.details.update(auto_sell_paused=True, protect_occurrence="event-2")
    notice = settlement_exit_snapshot(event, position, mode=mode)
    notice.update(observed_at=TELEGRAM_START + 240, occurrence="event-2")
    event.details["exit_wait_notices"] = [notice]
    monkeypatch.setattr("time.time", lambda: TELEGRAM_START + 240)
    app._notify_rule_signal("HDB", event, {"price": 22.95}, execution_mode=mode)
    assert len(telegram.sent) == 1
    monkeypatch.setattr("time.time", lambda: TELEGRAM_START + 601)
    app._notify_rule_signal("HDB", event, {"price": 22.9}, execution_mode=mode)
    assert len(telegram.sent) == 2 and "TỰ BÁN TẠM DỪNG" in telegram.sent[-1]
    app._notify_rule_signal("HDB", event, {"price": 22.9}, execution_mode=mode)
    assert len(telegram.sent) == 2


@pytest.mark.parametrize("cooldown,counts", [(30, [1, 2, 2, 2, 3]), (0, [1, 2, 3, 4, 5])])
def test_paused_dynamic_screenshot_cadence_respects_saved_cooldown(subject, monkeypatch, cooldown, counts):
    app, telegram = subject
    app.settings.telegram_cooldown_minutes["dynamic_protect"] = cooldown
    position = {"quantity": 400, "trade_quantity": 400, "auto_sell_enabled": False}
    ticks = [("09:46:57", 22.9), ("10:19:04", 22.85), ("10:26:29", 22.8),
             ("10:31:34", 22.75), ("10:49:04", 22.7)]
    for index, ((clock, price), expected) in enumerate(zip(ticks, counts)):
        observed_at = datetime.fromisoformat("2026-10-15T" + clock).replace(tzinfo=VN_TZ).timestamp()
        monkeypatch.setattr("time.time", lambda: observed_at)
        event = protect_event(f"screenshot-{index}")
        event.details.update(auto_sell_paused=True, current_price=price)
        snapshot = settlement_exit_snapshot(event, position, mode="REAL")
        snapshot.update(observed_at=observed_at, occurrence=f"screenshot-{index}")
        event.details["exit_wait_notices"] = [snapshot]
        app.rule_state = RuleStateStore(app.rule_state.store.path)
        app._notify_rule_signal("HDB", event, {"price": price}, execution_mode="REAL")
        app._notify_rule_signal("HDB", event, {"price": price}, execution_mode="REAL")
        assert len(telegram.sent) == expected
    assert app.settings.telegram_cooldown_minutes["dynamic_protect"] == cooldown
    sample = telegram.sent[1]
    assert len(sample.splitlines()) == 4
    assert "HDB · REAL · PROTECT DYNAMIC · ALERT" in sample
    assert "15/10 10:19:04 · Giá 22,850đ · PROTECT 23,046đ" in sample
    assert "TỰ BÁN TẠM DỪNG · Bán được 400/400 CP" in sample
    assert "Chưa gửi SELL." in sample and "Đỉnh 23,200đ" in sample


def test_a_quiet_poll_below_protect_does_not_clear_the_episode(tmp_path):
    state = RuleStateStore(tmp_path / "state.json")
    state.update_position_metrics("HDB", "trade", profit_pct=2.43)
    snapshot = {"kind": "PROTECT DYN", "protect_price": 23.046}
    first = state.observe_exit_wait("HDB", "trade", snapshot)
    event = StrategyDecision("WAIT", "HDB", "POSITION_BOT_ALERT", details={"normal_trigger_price": 23.046})
    position = {"current_price": 22.95, "exit_wait_active_kind": "PROTECT DYN"}
    cleared = exit_condition_cleared(event, position)
    assert not cleared
    state.observe_exit_wait("HDB", "trade", None, cleared=cleared)
    assert state.observe_exit_wait("HDB", "trade", snapshot) == first
    position["current_price"] = 23.1
    assert exit_condition_cleared(event, position)


def test_failed_protect_delivery_is_retained_and_can_retry(subject, monkeypatch):
    app, telegram = subject
    original = app.telegram.notify_protect_alert
    monkeypatch.setattr(app.telegram, "notify_protect_alert", lambda **_kw: False)
    app._notify_rule_signal("HDB", protect_event(), {"price": 23}, execution_mode="REAL")
    assert not telegram.sent and len(app.rule_state.pending_telegram_notices()) == 1
    app.rule_state = RuleStateStore(app.rule_state.store.path)
    monkeypatch.setattr("time.time", lambda: TELEGRAM_START + 61)
    monkeypatch.setattr(app.telegram, "notify_protect_alert", original)
    app._retry_telegram_notices()
    assert len(telegram.sent) == 1 and not app.rule_state.pending_telegram_notices()


def test_notification_groups_keep_independent_children_and_legacy_cooldown(subject):
    app, _ = subject
    app.settings = AppSettings.from_dict({"telegram_notifications": {"protect": False},
        "telegram_cooldown_minutes": {"protect": 17}})
    assert len(TELEGRAM_NOTIFICATION_GROUPS) == 4
    assert not app.settings.telegram_notifications["dynamic_protect"]
    assert app.settings.telegram_cooldown_minutes["dynamic_protect"] == 17
    app.settings.telegram_notifications["dynamic_protect"] = True
    assert app._telegram_notification_enabled("dynamic_protect")
    assert not app._telegram_notification_enabled("protect")
    app.settings.telegram_groups["position_exits"] = False
    assert not app._telegram_notification_enabled("dynamic_protect")
    assert AppSettings.from_dict(app.settings.to_dict()).telegram_groups["position_exits"] is False


def test_existing_holding_can_report_a_second_entry_signal_without_a_max_change_notice(subject, tmp_path):
    app, telegram = subject
    app.trade_state = TradeStateStore(tmp_path / "trades.json")
    app.queue = OrderQueue(tmp_path / "orders.json")
    trade = app.trade_state.create("HDB", "REAL")
    app.trade_state.record_buy_fill(trade.id, 200, 22.65, order_id="first")
    app.settings.priority_capital_enabled = True
    app.settings.priority_symbols = ["HDB"]
    app.settings.priority_allocations = {"HDB": {"max_orders": 1}}
    app.settings.priority_allocations["HDB"]["max_orders"] = 2
    assert not telegram.sent
    observe(app, mode="REAL", reason="HOLD_POSITION")
    assert len(telegram.sent) == 1 and "Đã dùng 1/2 lượt mua" in telegram.sent[0]
    assert "Còn 1 lượt" in telegram.sent[0]


def test_a_real_queued_buy_replaces_the_same_ticks_technical_message(subject, tmp_path):
    app, telegram = subject
    app.trade_state = TradeStateStore(tmp_path / "trades.json")
    order = OrderIntent.create("HDB", "BUY", 200, "MARKET", execution_mode="REAL",
                               details={"reservation_price": 24})
    event = StrategyDecision("BUY", "HDB", "BUY_SIGNAL", signal="BUY", details={
        "signal_cycle": "new", "indicators": {"buy_ema_fast": 23, "buy_ema_slow": 22,
        "rsi": 60, "rsi_previous": 59},
        "entry_checks": {"entry_orders_used": 1, "entry_orders_max": 2}})
    before = order.to_dict()
    app._notify_rule_signal("HDB", event, {"price": 23}, execution_mode="REAL",
                           signal_id=order.trade_id or order.id, entry_id=order.id, order_intent=order)
    assert len(telegram.sent) == 1
    assert "ĐÃ XẾP LỆNH" in telegram.sent[0] and "Lượt 2/2" in telegram.sent[0]
    assert order.to_dict() == before


@pytest.mark.parametrize("fee", [0, None, "invalid"])
def test_trade_row_shows_sl_cash_and_zero_fee_without_changing_the_ledger(ui_root, tmp_path, fee):
    from tkinter import ttk
    from support.tests.test_dashboard_ui_regressions import _running_subject
    from support.tests.test_position_repair_and_tplus_alerts import account
    from viking_v2.dashboard.tables import RUNNING_COLUMNS
    trades, rules, _, cycle = account(tmp_path)
    row = {"symbol": "HDB", "openQuantity": 400, "tradeQuantity": 400,
           "costPrice": 22650, "marketPrice": 23000, "price_unit": "VND", "buyFee": fee}
    before = cycle.to_dict(), deepcopy(row)
    tree = ttk.Treeview(ui_root, show="headings")
    app = _running_subject(tree, "REAL", positions=[row], cycles=[cycle])
    app.rule_state = rules
    app._cached_fee_rate = lambda _symbol, side: .0012 if side == "BUY" else None
    try:
        app._render_tables({})
        item, = tree.get_children()
        values = dict(zip(RUNNING_COLUMNS, tree.item(item, "values")))
        assert "-317.1K" in values["Targets"] and "-3.5%" in values["Targets"]
        if fee == 0:
            assert "MUA 0" in values["CostInfo"] and "~10.9K" not in values["CostInfo"]
        else:
            assert "MUA ~10.9K" in values["CostInfo"]
        assert "BÁN CHỜ DNSE" in values["CostInfo"]
        assert (trades.get(cycle.id).to_dict(), row) == before
        assert "Ticket" not in tree["displaycolumns"] and "Time" not in tree["displaycolumns"]
        assert "Bot" in tree["displaycolumns"]
        assert f"Mở {cycle.opened_at}" in tree._viking_column_hint.text_for(item, "Order")
    finally:
        tree.destroy()


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("verified", [True, False])
def test_running_position_keeps_prices_cash_and_activation_visible_without_mutating_fills(ui_root, mode, verified):
    import time
    from tkinter import ttk
    from support.tests.test_demo_order_display import table_subject
    from viking_v2.dashboard.tables import RUNNING_COLUMNS
    from viking_v2.models import TradeCycle
    cycle = TradeCycle("visible-HDB", "HDB", execution_mode=mode, em_modes=["NORMAL", "IND_EXIT"])
    cycle.record_buy_fill(600, 22.65)
    cycle.record_sell_fill(200, 23.15)
    row = {"symbol": "HDB", "tradeId": cycle.id, "openQuantity": 400, "tradeQuantity": 300,
           "costPrice": 22650, "marketPrice": 23100, "price_unit": "VND"}
    metrics = {"current_net_pnl": 280000, "mae_pct": (22.45 / 22.65 - 1) * 100,
               "mfe_pct": (23.2 / 22.65 - 1) * 100, "price_history_verified": verified,
               "market_data_policy": "G1_MATCH_V1"}
    decision = {"symbol": "HDB", "details": {"trade_id": cycle.id, "execution_mode": mode,
        "updated_at": time.time(), "protection_stage": "DYN", "normal_state": "DYN",
        "normal_atr_pct": 2.32, "normal_mfe_pct": metrics["mfe_pct"],
        "normal_trigger_price": 23.046, "normal_effective_trail_pct": 1.856}}
    before = deepcopy((cycle.to_dict(), metrics, decision, row))
    app, tree = table_subject(ui_root, mode, positions=[row], cycles=[cycle])
    app.rule_state = SimpleNamespace(position_metrics=lambda *_a: metrics)
    app.settings.rule_parameters.update(normal_atr_activation_multiplier=.55)
    try:
        app._render_tables({"decisions_by_mode": {mode: {"HDB": decision}}})
        iid, = tree.get_children()
        cells = dict(zip(RUNNING_COLUMNS, tree.item(iid, "values")))
        assert "SL -3.5% -317.1K @ 21,857" in cells["Targets"]
        assert "ARM 24,236 · START 22,939" in cells["Order"]
        assert "+280.0K (+2.06%)" in cells["PnL_MAE_MFE"]
        if verified:
            assert "MAE -0.88% · 22,450 · -80.0K" in cells["PnL_MAE_MFE"]
            assert "MFE +2.43% · 23,200 · +220.0K" in cells["PnL_MAE_MFE"]
            assert "PROTECT DYN 23,046" in cells["Targets"]
        else:
            assert "CHỜ XÁC MINH" in cells["PnL_MAE_MFE"] and "23,200" not in cells["PnL_MAE_MFE"]
            assert "PROTECT DYN CHỜ XÁC MINH" in cells["Targets"] and "23,046" not in cells["Targets"]
        assert cells["Status"].splitlines()[0].startswith("BÁN ĐƯỢC 300 CP · CHỜ VỀ 100 CP")
        assert len(cells["Status"].splitlines()) == 3
        assert len(cells["Targets"].splitlines()) == 3
        assert len(cells["Order"].splitlines()) == 3
        assert (cycle.to_dict(), metrics, decision, row) == before
    finally:
        tree.destroy()


@pytest.mark.parametrize("resume", [False, True])
def test_partial_history_retries_without_reconfirming_or_overriding_operator_resume(tmp_path, monkeypatch, resume):
    from concurrent.futures import Future
    from viking_v2.dashboard.actions import DashboardActionsMixin
    import viking_v2.trading.position_history as history_module
    trades, rules, queue, cycle = historical_account(tmp_path)
    client = HistoryClient()
    client.minutes[14] = payload([])
    client.get_latest_trade = lambda *_: {"boardId": "G1", "matchPrice": 23.1}
    client.get_latest_quote = lambda *_: {}
    times = [datetime.fromtimestamp(stamp(14, "09:10:00"), VN_TZ),
             datetime.fromtimestamp(stamp(14, "09:31:00"), VN_TZ)]
    original = history_module.load_position_history
    monkeypatch.setattr(history_module, "load_position_history",
        lambda broker, trade, sessions, **kw: original(broker, trade, sessions, now=times.pop(0), **kw))
    class Worker:
        def submit(self, fn):
            future = Future()
            try:
                future.set_result(fn())
            except Exception as exc:
                future.set_exception(exc)
            return future
    confirmations, scheduled = [], []
    monkeypatch.setattr("viking_v2.dashboard.actions.messagebox.askyesno",
                        lambda *a, **kw: confirmations.append(a) or True)
    app = DashboardActionsMixin()
    app.trade_state, app.rule_state, app.queue, app.real = trades, rules, queue, client
    app.settings = AppSettings()
    app._io_executor = app._position_read_executor = Worker()
    app._symbol_exchange = lambda _: "HOSE"
    app.running = True
    app._log = lambda *a: None
    app._post_ui = lambda fn: fn()
    app._refresh_local = lambda: None
    app.after = lambda delay, fn: scheduled.append((delay, fn))
    before = cycle.buy_notional, cycle.open_quantity, cycle.sold_quantity, cycle.net_pnl
    app._restart_position_metrics({"symbol": "HDB", "trade_id": cycle.id})
    assert len(confirmations) == 1 and len(scheduled) == 1
    assert scheduled[0][0] == 60_000
    assert rules.position_metrics("HDB", cycle.id)["price_history_evidence"]["pending_sessions"]
    assert rules.position_metrics("HDB", cycle.id)["price_history_verified"] is False
    if resume:
        trades.update_management(cycle.id, auto_sell_enabled=True)
    client.minutes[14] = payload([(stamp(14, "09:30:00"), 23, 24, 21, 23, 100)])
    scheduled.pop()[1]()
    assert len(confirmations) == 1 and not scheduled
    evidence = rules.position_metrics("HDB", cycle.id)["price_history_evidence"]
    assert bool(evidence["pending_sessions"]) is resume
    current = trades.get(cycle.id)
    assert current.auto_sell_enabled is resume
    assert (current.buy_notional, current.open_quantity, current.sold_quantity, current.net_pnl) == before
