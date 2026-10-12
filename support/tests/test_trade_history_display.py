"""One real order shown once; raw audit and monetary accounting stay intact."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from tkinter import font as tkfont, ttk

from viking_v2 import config
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.trade_history import history_datetime, history_groups, order_identity
from viking_v2.dashboard.windows import HistoryPopup
from viking_v2.models import TradeCycle
from viking_v2.storage import CSVOrderJournal, DailyFeeTracker
from viking_v2.trading.market import VN_TZ


def epoch(day=12, clock="14:09:48"):
    return datetime.fromisoformat(f"2026-10-{day:02d}T{clock}").replace(tzinfo=VN_TZ).timestamp()


def local(status="PENDING", *, day=12, fill=0, **changes):
    return {"timestamp": epoch(day, "14:09:49" if fill else "14:09:48"), "execution_mode": "REAL",
            "broker_order_id": "236032", "trade_id": "T1", "cache_id": "C1", "symbol": "HDB", "side": "BUY",
            "order_type": "MARKET", "quantity": 600, "filled_quantity": fill, "remaining_quantity": 600-fill,
            "broker_status": status, "fee": 0, "tax": 0, **changes}


def broker(*, day=12, **changes):
    return {"id": f"202610{day:02d}_236032", "symbol": "HDB", "side": "NB", "orderType": "MTL",
            "quantity": 600, "fillQuantity": 600, "leaveQuantity": 0, "averagePrice": 22650,
            "createdDate": f"2026-10-{day:02d}T07:09:48Z", "orderStatus": "Filled", **changes}


def cycle(**changes):
    return TradeCycle("T1", "HDB", execution_mode="REAL", entry_quantity=600,
                      open_quantity=600, avg_entry_price=22.65, **changes)


def test_screenshot_order_is_one_trade_one_order_three_preserved_details():
    journal, remote, cycles = [local(), local("FILLED", fill=600)], [broker()], [cycle()]
    before = deepcopy((journal, remote, [row.to_dict() for row in cycles]))
    day, = history_groups(journal, remote, cycles, "REAL")
    trade, = day["trades"]
    order, = trade["events"]
    assert order["values"][:4] == ("14:09:48 → 14:09:49", "236032", "22,650 · khớp 600/600 CP", "CHỜ DNSE")
    assert order["values"][5] == "KHỚP HẾT"
    assert len(order["details"]) == 3
    assert all(detail["values"][0].startswith("14:09") for detail in order["details"])
    assert "1 giao dịch" in day["label"]
    assert before == (journal, remote, [row.to_dict() for row in cycles])


@pytest.mark.parametrize("change", [dict(day=13), dict(symbol="IDC"), dict(side="NS")])
def test_daily_broker_ids_do_not_merge_distinct_days_symbols_or_sides(change):
    day = change.pop("day", 12)
    groups = history_groups([local()], [broker(day=day, **change)], [cycle()], "REAL")
    assert sum(len(group["trades"]) for group in groups) == 2


def test_same_plain_id_on_two_days_remains_two_trades():
    old = local(day=11)
    old.update(trade_id="T0", cache_id="C0")
    groups = history_groups([old, local()], [broker(day=11), broker()], [cycle()], "REAL")
    assert len(groups) == 2
    assert sum(len(group["trades"]) for group in groups) == 2
    assert sum(len(order["details"]) for group in groups for trade in group["trades"] for order in trade["events"]) == 4


def test_partial_fill_deltas_and_cumulative_broker_snapshot_are_not_added_twice():
    rows = [local(), local("PARTIAL", fill=200, event_id="first", fill_price_vnd=22500, fee=5400),
            local("FILLED", fill=400, event_id="second", fill_price_vnd=22725, fee=10908)]
    rows[1].update(remaining_quantity=400)
    rows[2].update(remaining_quantity=0)
    result = history_groups([*rows, deepcopy(rows[2])], [broker()], [cycle(fees_paid=16308)], "REAL")
    order = result[0]["trades"][0]["events"][0]
    assert order["values"][2] == "22,650 · khớp 600/600 CP"
    assert order["costs"] == 16308
    assert len(order["details"]) == 4  # three unique CSV events + DNSE mirror
    assert result[0]["trades"][0]["costs"] == 16308


@pytest.mark.parametrize("raw,expected", [(broker(fee=0), "0"), (broker(), "CHỜ DNSE")])
def test_explicit_zero_fee_is_distinct_from_missing_fee(raw, expected):
    result = history_groups([local("FILLED", fill=600)], [raw], [cycle()], "REAL")
    assert result[0]["trades"][0]["events"][0]["values"][3] == expected


def test_cost_correction_does_not_change_order_status_or_fill_total():
    rows = [local("FILLED", fill=600, fee=16000), local("COST_ADJUSTMENT", fee=-1000)]
    order = history_groups(rows, [broker()], [cycle(fees_paid=15000)], "REAL")[0]["trades"][0]["events"][0]
    assert order["values"][5] == "KHỚP HẾT" and "600/600" in order["values"][2]
    assert order["costs"] == 15000


def test_second_partial_sell_leg_uses_broker_size_and_delta_not_intent_cumulative_fill():
    row = local("PARTIAL", fill=400, side="SELL", broker_quantity=200, fill_delta_quantity=100, fill_price_vnd=23000)
    remote = broker(side="NS", quantity=200, fillQuantity=100, orderStatus="Partial", averagePrice=23000)
    order = history_groups([row], [remote], [cycle()], "REAL")[0]["trades"][0]["events"][0]
    assert order["values"][2] == "23,000 · khớp 100/200 CP"


def test_broker_fill_not_yet_recorded_in_local_audit_is_named_as_waiting_reconciliation():
    order = history_groups([local()], [broker()], [cycle()], "REAL")[0]["trades"][0]["events"][0]
    assert order["values"][5] == "KHỚP · CHỜ ĐỐI SOÁT"
    assert "600/600 CP" in order["values"][2]


@pytest.mark.parametrize("nav,title", [(49983692, "TỔNG TÀI SẢN / NAV"), (None, "TIỀN KHẢ DỤNG · DNSE")])
def test_account_distinguishes_assets_available_cash_and_paid_fee(nav, title):
    class Label:
        def configure(self, **values):
            self.values = values
    balance = {"stock": {"availableCash": 36393692}}
    if nav is not None:
        balance["netAssetValue"] = nav
    app = DashboardActionsMixin()
    app.mode = SimpleNamespace(get=lambda: "REAL")
    app.settings = config.AppSettings()
    app.account_id = "OFFLINE"
    app.snapshots = {"REAL": (balance, [], [])}
    app.trade_state = SimpleNamespace(list_cycles=lambda: [])
    app.daily_fees = SimpleNamespace(summary=lambda *_args, **_kw: {"pnl": 0, "fees": 16308})
    for name in ("equity", "equity_title", "account", "pnl", "cash"):
        setattr(app, f"lbl_{name}", Label())
    app._paint_account()
    assert app.lbl_equity_title.values["text"] == title
    assert app.lbl_equity.values["text"] == f"{nav if nav is not None else 36393692:,.0f} ₫"
    assert app.lbl_account.values["text"].endswith("TIỀN KHẢ DỤNG 36,393,692")
    assert app.lbl_pnl.values["text"] == "PNL NGÀY: 0"
    assert app.lbl_cash.values["text"] == "PHÍ NGÀY: -16.3K"


def test_history_times_use_vietnam_not_host_timezone_and_keep_id_date():
    expected = datetime(2026, 10, 12, 14, 9, 48)
    assert history_datetime("2026-10-12T07:09:48Z", broker=True) == expected
    assert history_datetime("2026-10-12 07:09:48", broker=True) == expected
    assert history_datetime("2026-10-12T14:09:48+07:00") == expected
    assert history_datetime(epoch()) == expected
    assert history_datetime("bad") == datetime.min
    assert history_datetime(0) == datetime.min
    assert order_identity("20261011_236032", expected, "HDB", "NB")[:2] == ("20261011", "236032")


def journal_fill(tmp_path, *, fee=None, timestamp=None):
    journal = CSVOrderJournal(tmp_path / "orders.csv")
    body = dict(fillQuantity=600, averagePrice=22650, price_unit="VND")
    if fee is not None:
        body["fee"] = fee
    journal.append_event(dict(ts=timestamp or epoch(), intent=dict(execution_mode="REAL", id="C1", trade_id="T1", symbol="HDB", side="BUY", filled_quantity=600), result=dict(order_id="236032", status="FILLED", raw=body)))
    return journal


@pytest.mark.parametrize("fee,pending", [(None, 1), (0, 0), (16308, 0)])
def test_journal_and_daily_display_distinguish_unknown_zero_and_actual_fee(tmp_path, fee, pending):
    journal = journal_fill(tmp_path, fee=fee)
    row, = journal.read_all()
    assert row["fill_price_vnd"] == "22650.0"
    tracker = DailyFeeTracker(journal.path, tmp_path / "stats.json")
    result = tracker.display_summary("REAL", [cycle()], now=epoch(clock="14:30:00"))
    assert result["fees"] == (fee or 0) and result["fees_pending"] == pending
    assert result["pnl"] == 0  # BUY itself is not a closed trade's profit


def test_1500_closing_retains_totals_overnight_restarts_and_0900_starts_new_display(tmp_path):
    journal = journal_fill(tmp_path, fee=16308)
    tracker = DailyFeeTracker(journal.path, tmp_path / "stats.json")
    closed = TradeCycle("CLOSED", "MSN", execution_mode="REAL", status="CLOSED", net_pnl=200000, closed_at=epoch(clock="14:40:00"))
    assert tracker.display_summary("REAL", [closed], now=epoch(clock="14:59:59"))["fees"] == 16308
    at_close = tracker.display_summary("REAL", [closed], now=epoch(clock="15:00:00"))
    assert at_close["rolled_over"] and at_close["pnl"] == 200000 and at_close["fees"] == 16308
    reopened = DailyFeeTracker(journal.path, tracker.state.path)
    for timestamp in (epoch(clock="17:00:00"), epoch(day=13, clock="00:01:00"), epoch(day=13, clock="08:59:59")):
        values = reopened.display_summary("REAL", [closed], now=timestamp)
        assert values["fees"] == 16308 and values["pnl"] == 200000 and not values["rolled_over"]
        assert len(reopened.state.read()["history"]) == 1
    next_day = reopened.display_summary("REAL", [closed], now=epoch(day=13, clock="09:00:00"))
    assert next_day["fees"] == 0 and next_day["pnl"] == 0
    assert len(journal.read_all()) == 1


def test_cold_start_after_close_and_late_fee_correction_are_visible_once(tmp_path):
    journal = journal_fill(tmp_path, fee=16000)
    tracker = DailyFeeTracker(journal.path, tmp_path / "stats.json")
    assert tracker.display_summary("REAL", now=epoch(clock="17:00:00"))["fees"] == 16000
    journal.append_event(dict(ts=epoch(clock="17:01:00"), intent=dict(execution_mode="REAL", id="C1", symbol="HDB", side="BUY"), result=dict(status="COST_ADJUSTMENT", message="BROKER_COST_RECONCILED", raw=dict(fee=-1000))))
    assert tracker.display_summary("REAL", now=epoch(clock="17:02:00"))["fees"] == 15000
    assert len(tracker.state.read()["history"]) == 1
    assert tracker.display_summary("REAL", now=epoch(day=13, clock="09:00:00"))["fees"] == 0
    # Manual reset remains immediate even while retaining a closed day.
    tracker = DailyFeeTracker(journal.path, tmp_path / "manual.json")
    tracker.reset("REAL", epoch(clock="17:03:00"))
    assert tracker.display_summary("REAL", now=epoch(clock="17:04:00"))["fees"] == 0


def test_schedule_migration_is_once_and_preserves_new_explicit_midnight_choice():
    assert config.AppSettings().daily_stats_reset_time == "15:00"
    assert config.AppSettings.from_dict({"daily_stats_reset_time": "00:00"}).daily_stats_reset_time == "15:00"
    saved = config.AppSettings(daily_stats_reset_time="00:00").to_dict()
    assert config.AppSettings.from_dict(saved).daily_stats_reset_time == "00:00"
    assert config.AppSettings.from_dict({"daily_stats_reset_time": "16:00"}).daily_stats_reset_time == "16:00"


def test_broker_confirms_zero_fee_later_without_replaying_fill_or_cost(tmp_path, monkeypatch):
    from viking_v2.models import OrderIntent
    from viking_v2.storage import JSONLineJournal
    from viking_v2.trading.orders import OrderQueue
    from viking_v2.trading.execution import ExecutionService
    from viking_v2.trading.state import TradeStateStore

    monkeypatch.setattr("viking_v2.trading.execution.time.time", lambda: epoch(clock="14:30:00"))
    queue = OrderQueue(tmp_path / "queue.json")
    queue.add(OrderIntent("C1", "HDB", "BUY", 600, "MARKET", execution_mode="REAL", trade_id="T1",
                         status="FILLED", filled_quantity=600, remaining_quantity=0, broker_order_id="236032",
                         broker_filled_quantity=600, broker_notional_logged=13590000))
    trades = TradeStateStore(tmp_path / "trades.json")
    trades.save(cycle())
    row = broker(id="236032", price_unit="VND")
    real = SimpleNamespace(get_orders=lambda **_: [deepcopy(row)])
    service = ExecutionService(real, object(), queue, JSONLineJournal(tmp_path / "audit.jsonl"), trade_state=trades)
    service.csv_journal = journal_fill(tmp_path)
    tracker = DailyFeeTracker(service.csv_journal.path, tmp_path / "daily.json")
    assert tracker.display_summary("REAL", trades.list_cycles(), now=epoch(clock="14:30:00"))["fees_pending"] == 1
    before = trades.get("T1").to_dict()
    service.reconcile_working("REAL")
    row["fee"] = 0
    service.reconcile_working("REAL")
    service.reconcile_working("REAL")
    assert trades.get("T1").to_dict() == before
    assert queue.get("C1").filled_quantity == 600
    assert tracker.display_summary("REAL", trades.list_cycles(), now=epoch(clock="14:30:00"))["fees_pending"] == 0
    confirmed = [item for item in service.csv_journal.read_all() if item.get("message") == "BROKER_COST_CONFIRMED"]
    assert len(confirmed) == 1 and float(confirmed[0]["fee"]) == 0
    assert len(service.csv_journal.read_all()) == 2


def test_zero_fee_on_pending_order_is_not_a_final_confirmation_for_next_fill(tmp_path, monkeypatch):
    from viking_v2.models import OrderIntent
    from viking_v2.storage import JSONLineJournal
    from viking_v2.trading.orders import OrderQueue
    from viking_v2.trading.execution import ExecutionService
    from viking_v2.trading.state import TradeStateStore
    monkeypatch.setattr("viking_v2.trading.execution.time.time", lambda: epoch(clock="14:30:00"))
    queue = OrderQueue(tmp_path / "queue.json")
    queue.add(OrderIntent("C1", "HDB", "BUY", 600, "MARKET", execution_mode="REAL", trade_id="T1",
                         status="FILLED", filled_quantity=600, broker_order_id="236032"))
    queue.add(OrderIntent("C2", "HDB", "BUY", 100, "MARKET", execution_mode="REAL", trade_id="T1",
                         status="WORKING", broker_order_id="236033", working_quantity=100))
    trades = TradeStateStore(tmp_path / "trades.json")
    trades.save(cycle(fees_paid=16000))
    row = broker(id="236033", quantity=100, fillQuantity=0, leaveQuantity=100, orderStatus="Pending", fee=0, price_unit="VND")
    service = ExecutionService(SimpleNamespace(get_orders=lambda **_: [deepcopy(row)]), object(), queue,
                               JSONLineJournal(tmp_path / "audit.jsonl"), trade_state=trades)
    service.reconcile_working("REAL")
    assert not service.csv_journal.read_all()
    row.update(fillQuantity=100, leaveQuantity=0, orderStatus="Filled")
    del row["fee"]
    service.reconcile_working("REAL")
    assert trades.get("T1").entry_quantity == 700
    tracker = DailyFeeTracker(service.csv_journal.path, tmp_path / "daily.json")
    assert tracker.display_summary("REAL", trades.list_cycles(), now=epoch(clock="14:30:00"))["fees_pending"] == 1
    row["fee"] = 0
    service.reconcile_working("REAL")
    service.reconcile_working("REAL")
    assert trades.get("T1").entry_quantity == 700 and trades.get("T1").fees_paid == 16000
    assert tracker.display_summary("REAL", trades.list_cycles(), now=epoch(clock="14:30:00"))["fees_pending"] == 0


def test_history_ui_is_readable_compact_with_collapsed_audit_details(ui_root, tmp_path):
    groups = history_groups([local(), local("FILLED", fill=600)], [broker()], [cycle()], "REAL")
    trace = SimpleNamespace(path=tmp_path / "trace.sqlite3", read=lambda **_: [])
    popup = HistoryPopup(ui_root, lambda mode: groups if mode == "REAL" else [], initial_mode="REAL", trace_store=trace)
    try:
        tree = popup.trees["REAL"]
        day, = tree.get_children()
        trade, = tree.get_children(day)
        order, = tree.get_children(trade)
        assert len(tree.get_children(order)) == 3 and not tree.item(order, "open")
        tree.item(order, open=True)
        tree.selection_set(order)
        popup.refresh()
        assert tree.item(order, "open") and tree.selection() == (order,)
        assert int(tree.column("#0", "width")) <= 350
        font = tkfont.Font(root=ui_root, font=ttk.Style().lookup("History.Treeview", "font"))
        assert abs(font.actual("size")) >= 18
        for column in ("time", "order", "price_qty", "cost", "status"):
            assert font.measure(tree.set(order, column)) + 12 <= int(tree.column(column, "width")), column
        assert "symbol" not in tuple(popup.signal_tree.cget("displaycolumns"))
        assert "rsi_previous_date" in tuple(popup.signal_tree.cget("columns"))
    finally:
        popup.close()


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_native_history_signal_selection_keeps_original_data_and_full_context(ui_root, tmp_path, scale):
    import customtkinter as ctk
    import os
    from pathlib import Path
    from viking_v2.services.signal_history import periodic_history_row

    samples = [dict(timestamp=f"2026-10-12T14:{minute:02d}:00+07:00", scheduled_at=f"2026-10-12T14:{minute:02d}:00+07:00",
                    symbol=symbol, execution_mode="REAL", priority=index + 1, price_vnd=19550,
                    ema_fast=19.6334, ema_slow=19.7997, rsi=30.05, rsi_previous=30.47,
                    rsi_previous_date="2026-10-09", entry=False, exit_e=False, slot_usage="1/4",
                    ema_cross_required=False, ema_cross_state="DISABLED", market_state="ACCUMULATION", reason="NO_NEW_BUY_SIGNAL")
               for index, symbol in enumerate(("MSN", "CTS", "HDB", "IDC")) for minute in range(0, 31, 2)]
    originals = deepcopy(samples)
    trace = SimpleNamespace(path=tmp_path / "trace.sqlite3", read=lambda **_: deepcopy(samples))
    ctk.set_widget_scaling(scale)
    popup = HistoryPopup(ui_root, lambda _: history_groups([local(), local("FILLED", fill=600)], [broker()], [cycle()], "REAL"), trace_store=trace)
    try:
        popup.tabs.set("TÍN HIỆU")
        tree = popup.signal_tree
        assert len(tree.get_children("day:2026-10-12")) == 4
        cts = tree.get_children("symbol:2026-10-12:CTS")
        assert len(cts) == 16
        tree.selection_set(cts[0])
        tree.item("symbol:2026-10-12:CTS", open=True)
        popup._signal_selection_changed()
        detail = popup.signal_selection_detail.cget("text")
        assert "CTS · REAL · 14:30:00" in detail and "2026-10-09" in detail and "ACCUMULATION" in detail
        assert samples == originals
        assert len(popup._visible_history_sources) == 64
        font = tkfont.Font(root=ui_root, font=ttk.Style().lookup("Signal.Treeview", "font"))
        for column in ("execution_mode", "watchlist_priority", "slot_usage", "display_price", "ema_comparison", "rsi_comparison"):
            assert font.measure(tree.set(cts[0], column)) + 12 <= int(tree.column(column, "width")), column
        if os.environ.get("VIKING_HISTORY_SCREENSHOTS"):
            from PIL import ImageGrab
            import time
            folder = Path(".artifacts/history_display_20261012")
            folder.mkdir(parents=True, exist_ok=True)
            popup.top.geometry("2200x1100+20+20")
            popup.top.lift()
            popup.top.update()
            x, y = popup.top.winfo_rootx(), popup.top.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + popup.top.winfo_width(), y + popup.top.winfo_height())).save(folder / f"signals_{scale}.png")
            popup.tabs.set("CKCS REAL")
            until = time.monotonic() + 0.2
            while time.monotonic() < until:
                popup.top.update()
                time.sleep(0.01)
            ImageGrab.grab(bbox=(x, y, x + popup.top.winfo_width(), y + popup.top.winfo_height())).save(folder / f"orders_{scale}.png")
    finally:
        popup.close()
        ctk.set_widget_scaling(1.0)
