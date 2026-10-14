"""Historical repair reads session highs/lows and preserves position executions."""
from copy import deepcopy
from datetime import datetime

import pytest

from viking_v2.trading.market import VN_TZ
from viking_v2.trading.position_history import load_position_history, session_count
from viking_v2.trading.position_repair import restart_price_observations
from support.tests.test_position_repair_and_tplus_alerts import account, g1


def stamp(day, clock="00:00:00"):
    return int(datetime.fromisoformat(f"2026-10-{day:02d}T{clock}").replace(tzinfo=VN_TZ).timestamp())


NOW = datetime.fromtimestamp(stamp(14, "15:00:00"), VN_TZ)


def payload(rows):
    keys = ("t", "o", "h", "l", "c", "v")
    return {"s": "ok", **{key: [row[index] for row in rows] for index, key in enumerate(keys)}}


class HistoryClient:
    def __init__(self):
        self.calls = []
        self.calendar = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
        self.days = {day: payload([(stamp(day), 23, 24, 21, 23, 100)]) for day in (9, 12, 13, 14)}
        self.minutes = {day: payload([(stamp(day, "09:30:00"), 23, 24, 21, 23, 100)])
                        for day in (9, 12, 13, 14)}

    def get_working_dates(self):
        return self.calendar

    def get_ohlc(self, symbol, resolution, left, right):
        self.calls.append((symbol, resolution, left, right))
        if resolution == "1D":
            rows = []
            for day in (9, 12, 13, 14):
                if left <= stamp(day) <= right:
                    data = self.days[day]
                    rows.extend(zip(*(data[key] for key in ("t", "o", "h", "l", "c", "v"))))
            return payload(rows)
        day = datetime.fromtimestamp(left, VN_TZ).day
        # Intentionally return out-of-range bars too; the reader must clip them.
        return deepcopy(self.minutes[day])


def historical_account(tmp_path, *, entered=stamp(9, "10:00:35"), mode="REAL"):
    trades, rules, queue, cycle = account(tmp_path, mode)
    cycle.opened_at = entered
    trades.save(cycle)
    return trades, rules, queue, cycle


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_three_sessions_use_historical_extrema_not_current_price(tmp_path, mode):
    trades, rules, queue, cycle = historical_account(tmp_path, mode=mode)
    client = HistoryClient()
    before = cycle.to_dict()
    history = load_position_history(client, cycle, now=NOW)
    assert history["session_dates"] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert history["requested_sessions"] == history["loaded_sessions"] == 3
    assert history["high"] == 24 and history["low"] == 21
    repaired = restart_price_observations(trades, rules, queue, cycle.id, g1(23.1), history=history)
    assert repaired["mfe_pct"] == pytest.approx((24 / 22.65 - 1) * 100)
    assert repaired["mae_pct"] == pytest.approx((21 / 22.65 - 1) * 100)
    assert repaired["current_profit_pct"] == pytest.approx((23.1 / 22.65 - 1) * 100)
    assert repaired["normal_protection_count"] == 2
    assert trades.get(cycle.id).to_dict() == {**before, "auto_sell_enabled": False}
    assert repaired["price_history_evidence"]["coverage"] == "HISTORICAL_SESSIONS"
    # Continued observation after rebuilding keeps older high/low.
    later = rules.update_position_metrics("HDB", cycle.id, profit_pct=0, market_price=22.65)
    assert later["mfe_pct"] == repaired["mfe_pct"] and later["mae_pct"] == repaired["mae_pct"]


def test_sessions_skip_weekends_holidays_and_count_current_session(tmp_path):
    _, _, _, cycle = historical_account(tmp_path, entered=stamp(8, "10:00:00"))
    client = HistoryClient()
    result = load_position_history(client, cycle, 3, now=NOW, holidays=["2026-10-13"])
    assert result["session_dates"] == ["2026-10-09", "2026-10-12", "2026-10-14"]


def test_before_market_open_does_not_count_today(tmp_path):
    _, _, _, cycle = historical_account(tmp_path, entered=stamp(8, "10:00:00"))
    result = load_position_history(HistoryClient(), cycle, now=NOW.replace(hour=8))
    assert result["session_dates"] == ["2026-10-09", "2026-10-12", "2026-10-13"]


def test_recent_entry_clips_pre_buy_bars_and_straddling_minute(tmp_path):
    _, _, _, cycle = historical_account(tmp_path, entered=stamp(14, "09:30:35"))
    client = HistoryClient()
    client.minutes[14] = payload([
        (stamp(14, "09:29:00"), 23, 99, 1, 23, 100),
        (stamp(14, "09:30:00"), 23, 90, 2, 23, 100),
        (stamp(14, "09:31:00"), 23, 23.5, 22.5, 23, 100),
        (stamp(15, "09:31:00"), 23, 88, 3, 23, 100),
    ])
    result = load_position_history(client, cycle, now=NOW)
    assert result["high"] == 23.5 and result["low"] == 22.5
    assert result["requested_sessions"] == 3 and result["loaded_sessions"] == 1
    assert result["started_at"] == stamp(14, "09:31:00")
    assert result["entry_minute_omitted"] is True


def test_exact_purchase_minute_is_included(tmp_path):
    _, _, _, cycle = historical_account(tmp_path, entered=stamp(14, "09:30:00"))
    result = load_position_history(HistoryClient(), cycle, now=NOW)
    assert result["started_at"] == stamp(14, "09:30:00")
    assert not result["entry_minute_omitted"]


@pytest.mark.parametrize("bad", ["", "0", "31", "3.5", "-3", "nan", True])
def test_session_count_rejects_invalid_input(bad):
    with pytest.raises(ValueError, match="Số phiên"):
        session_count(bad)


@pytest.mark.parametrize("failure", ["calendar", "empty_day", "truncated_high", "malformed", "wrong_board", "nan"])
def test_missing_or_unreliable_history_changes_no_account_state(tmp_path, failure):
    trades, rules, _, cycle = historical_account(tmp_path)
    client = HistoryClient()
    if failure == "calendar":
        client.calendar = []
    elif failure == "empty_day":
        client.minutes[13] = payload([])
    elif failure == "truncated_high":
        client.minutes[13]["h"] = [23.5]
    elif failure == "malformed":
        client.minutes[13]["l"] = []
    elif failure == "wrong_board":
        client.minutes[13]["boardId"] = "T1"
    else:
        client.minutes[13]["h"] = [float("nan")]
    before = trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)
    with pytest.raises(ValueError):
        load_position_history(client, cycle, now=NOW)
    assert (trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)) == before


def test_changed_position_cannot_apply_a_stale_preview(tmp_path):
    trades, rules, queue, cycle = historical_account(tmp_path)
    history = load_position_history(HistoryClient(), cycle, now=NOW)
    trades.record_buy_fill(cycle.id, 100, 25)
    before = trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)
    with pytest.raises(ValueError, match="thay đổi"):
        restart_price_observations(trades, rules, queue, cycle.id, g1(), history=history)
    assert (trades.get(cycle.id).to_dict(), rules.position_metrics("HDB", cycle.id)) == before


def test_historical_repair_is_blocked_by_an_existing_broker_sell(tmp_path):
    from viking_v2.models import OrderIntent
    trades, rules, queue, cycle = historical_account(tmp_path)
    order = queue.add(OrderIntent.create("HDB", "SELL", 100, "MARKET", source="MANUAL",
                                      execution_mode="REAL", trade_id=cycle.id))
    queue._update(order.id, status="WORKING", broker_order_id="accepted")
    before = rules.position_metrics("HDB", cycle.id)
    history = load_position_history(HistoryClient(), cycle, now=NOW)
    result = restart_price_observations(trades, rules, queue, cycle.id, g1(), history=history)
    assert result["blocked_by_broker_orders"] == [order.id]
    assert rules.position_metrics("HDB", cycle.id) == before
    assert queue.get(order.id).status == "WORKING"


def test_explicit_vnd_candles_are_normalized(tmp_path):
    _, _, _, cycle = historical_account(tmp_path)
    client = HistoryClient()
    client.minutes[14] = payload([(stamp(14, "09:30:00"), 23000, 24000, 21000, 23000, 100)])
    client.minutes[14]["price_unit"] = "VND"
    result = load_position_history(client, cycle, now=NOW)
    assert result["high"] == 24 and result["low"] == 21


def test_popup_validates_and_forwards_session_count_without_saving_controls(ui_root, monkeypatch, tmp_path):
    import customtkinter as ctk
    from support.tests.test_position_popup_responsiveness import prepare, open_popup
    from support.tests.test_demo_order_display import _widgets
    app, _, action = prepare(ui_root, monkeypatch, tmp_path)
    before = app.trade_state.get(action["trade_id"]).to_dict()
    calls = []
    monkeypatch.setattr(ui_root, "_restart_position_metrics", lambda action, **kwargs: calls.append((action, kwargs)), raising=False)
    top = open_popup(ui_root, action)
    try:
        widgets = list(_widgets(top))
        count, = [w for w in widgets if isinstance(w, ctk.CTkEntry) and w.cget("justify") == "center"]
        assert count.get() == "3"
        repair = next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "Đo lại MAE/MFE")
        count.delete(0, "end")
        count.insert(0, "0")
        repair.invoke()
        assert top.winfo_exists() and not calls
        assert any(isinstance(w, ctk.CTkLabel) and "Số phiên phải" in w.cget("text") for w in _widgets(top))
        count.delete(0, "end")
        count.insert(0, "5")
        repair.invoke()
        assert calls[0][1] == {"sessions": 5}
        assert calls[0][0]["trade_id"] == action["trade_id"]
        assert app.trade_state.get(action["trade_id"]).to_dict() == before
    finally:
        if top.winfo_exists():
            top.destroy()


@pytest.mark.parametrize("default_modes", [["NORMAL", "IND_EXIT"], []])
def test_inherited_dropdown_tracks_rule_updates_without_creating_an_override(ui_root, monkeypatch, tmp_path, default_modes):
    import customtkinter as ctk
    import tkinter as tk
    from support.tests.test_position_popup_responsiveness import prepare, open_popup
    from support.tests.test_demo_order_display import _widgets
    app, _, action = prepare(ui_root, monkeypatch, tmp_path)
    app.settings.bot_em_modes = default_modes
    app.settings.rule_parameters.update(normal_policy="AUTO", dynamic_policy="ALERT", normal_dynamic_enabled=True)
    top = open_popup(ui_root, action)
    try:
        menus = [w for w in _widgets(top) if isinstance(w, ctk.CTkOptionMenu)]
        assert menus[0].get() == "AUTO · RULE"
        assert menus[1].get() == "ALERT · RULE"
        app.settings.rule_parameters["normal_policy"] = "ALERT"
        ready = tk.BooleanVar(master=ui_root, value=False)
        ui_root.after(600, lambda: ready.set(True))
        ui_root.wait_variable(ready)
        assert menus[0].get() == "ALERT · RULE"
        next(w for w in _widgets(top) if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        assert app.trade_state.get(action["trade_id"]).exit_rule_overrides == {}
        assert set(app.trade_state.get(action["trade_id"]).em_modes) == {"NORMAL", "IND_EXIT"}
    finally:
        if top.winfo_exists():
            top.destroy()


@pytest.mark.parametrize("accept", [True, False])
def test_action_history_fetch_yields_to_tk_and_confirmation_can_cancel(ui_root, monkeypatch, tmp_path, accept):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import threading
    from viking_v2.dashboard.actions import DashboardActionsMixin
    from support.tests.test_position_popup_responsiveness import prepare, pump
    import viking_v2.trading.position_history as module
    import viking_v2.dashboard.actions as actions_module
    app, _, action = prepare(ui_root, monkeypatch, tmp_path)
    loaded, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    observed = []
    def read(client, cycle, sessions, **kwargs):
        observed.append((threading.get_ident(), sessions))
        loaded.set()
        assert release.wait(5)
        return {"loaded_sessions": 3, "session_dates": ["2026-10-12", "2026-10-14"],
                "high": 110, "low": 90, "entry_minute_omitted": False}
    monkeypatch.setattr(module, "load_position_history", read)
    monkeypatch.setattr(ui_root, "real", SimpleNamespace(get_latest_trade=lambda *_: {"boardId": "G1", "matchPrice": 100},
                                                       get_latest_quote=lambda *_: {}), raising=False)
    posted = []
    monkeypatch.setattr(ui_root, "_post_ui", posted.append, raising=False)
    confirmations, applied = [], []
    def confirm(_title, message, **kwargs):
        assert threading.get_ident() == main_thread
        confirmations.append(message)
        return accept
    monkeypatch.setattr(actions_module.messagebox, "askyesno", confirm)
    monkeypatch.setattr("viking_v2.trading.position_repair.restart_price_observations",
                        lambda *_args, **kwargs: applied.append(kwargs["history"]) or {})
    worker = ThreadPoolExecutor(1)
    monkeypatch.setattr(ui_root, "_position_read_executor", worker, raising=False)
    monkeypatch.setattr(ui_root, "_io_executor", SimpleNamespace(submit=lambda *_: pytest.fail("Used broker worker")), raising=False)
    try:
        DashboardActionsMixin._restart_position_metrics(ui_root, action, sessions=3)
        pump(ui_root, loaded.is_set)
        assert len(observed) == 1 and observed[0][0] != main_thread and observed[0][1] == 3
        assert not posted and not confirmations and not applied
        release.set()
        pump(ui_root, lambda: bool(posted))
        posted.pop()()
        assert "Đỉnh 110,000" in confirmations[0] and "Đáy 90,000" in confirmations[0]
        assert bool(applied) is accept
    finally:
        release.set()
        worker.shutdown(wait=True, cancel_futures=True)
