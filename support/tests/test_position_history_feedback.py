"""A historical read keeps the popup visible and reports every outcome."""
from concurrent.futures import Future
from types import MethodType

import customtkinter as ctk
import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.trading.position_history import load_position_history
from support.tests.test_demo_order_display import _widgets
from support.tests.test_position_history import HistoryClient, NOW, historical_account, payload, stamp
from support.tests.test_position_popup_responsiveness import open_popup


class DeferredReader:
    def __init__(self):
        self.jobs = []

    def submit(self, work):
        future = Future()
        self.jobs.append((work, future))
        return future

    def finish(self):
        work, future = self.jobs.pop(0)
        try:
            value = work()
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(value)


@pytest.fixture
def history_popup(ui_root, monkeypatch, tmp_path):
    import viking_v2.trading.position_history as module
    trades, rules, queue, cycle = historical_account(tmp_path)
    client, reader = HistoryClient(), DeferredReader()
    client.get_latest_trade = lambda *_: {"boardId": "G1", "matchPrice": 23.1}
    client.get_latest_quote = lambda *_: {}
    scheduled, confirmations = [], []
    clock = {"now": NOW, "accept": True}
    original = module.load_position_history
    monkeypatch.setattr(module, "load_position_history",
                        lambda *args, **kw: original(*args, now=clock["now"], **kw))
    def confirm(*_args, **kwargs):
        confirmations.append(kwargs)
        return clock["accept"]
    monkeypatch.setattr("viking_v2.dashboard.actions.messagebox.askyesno", confirm)
    for key, value in {"trade_state": trades, "rule_state": rules, "queue": queue,
                       "settings": AppSettings(), "real": client, "running": True,
                       "_shared_tick": lambda *_: {"price": 23.1}, "_row_time": str,
                       "_log": lambda *_: None, "_refresh_local": lambda: None,
                       "_post_ui": lambda callback: callback(), "_symbol_exchange": lambda *_: "HOSE",
                       "_position_read_executor": None, "_position_history_requests": {},
                       "_restart_position_metrics": MethodType(DashboardActionsMixin._restart_position_metrics, ui_root)}.items():
        monkeypatch.setattr(ui_root, key, value, raising=False)
    action = {"symbol": "HDB", "mode": "REAL", "trade_id": cycle.id,
              "position": {"openQuantity": 400, "tradeQuantity": 400, "costPrice": 22.65, "marketPrice": 23.1}}
    top = open_popup(ui_root, action)
    monkeypatch.setattr(ui_root, "_position_read_executor", reader, raising=False)
    monkeypatch.setattr(ui_root, "_io_executor", reader, raising=False)
    native_after = ui_root.after
    def after(delay, callback=None, *args):
        if delay == 60_000:
            scheduled.append((delay, callback))
            return "history-retry"
        return native_after(delay, callback, *args)
    monkeypatch.setattr(ui_root, "after", after)
    widgets = list(_widgets(top))
    repair = next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "Đo lại MAE/MFE")
    def text():
        return "\n".join(w.cget("text") for w in _widgets(top) if isinstance(w, ctk.CTkLabel))
    yield ui_root, top, repair, text, reader, client, trades, rules, queue, cycle, scheduled, confirmations, clock
    if top.winfo_exists():
        top.destroy()


@pytest.mark.parametrize("accept", [True, False])
def test_short_calendar_is_visible_then_retry_reports_result_and_keeps_popup(history_popup, accept):
    app, top, repair, text, reader, client, trades, rules, queue, cycle, scheduled, confirmations, clock = history_popup
    before, metrics = cycle.to_dict(), rules.position_metrics("HDB", cycle.id)
    client.calendar = ["2026-10-13", "2026-10-14"]
    client.index_days = (13, 14)
    top.transient("")
    top.deiconify()
    repair.invoke()
    assert top.winfo_exists() and repair.cget("state") == "disabled" and "Đang tải" in text()
    reader.finish()
    assert "2/3 phiên" in text() and "60 giây" in text()
    top.update()
    feedback = next(w for w in _widgets(top) if isinstance(w, ctk.CTkLabel) and "2/3 phiên" in w.cget("text"))
    assert feedback.winfo_ismapped()
    for widget in _widgets(top):
        if isinstance(widget, ctk.CTkButton):
            assert widget.winfo_rooty() + widget.winfo_height() <= top.winfo_rooty() + top.winfo_height() + 2
    assert top.winfo_exists() and repair.cget("state") == "normal"
    assert not confirmations and trades.get(cycle.id).to_dict() == before
    assert rules.position_metrics("HDB", cycle.id) == metrics
    client.calendar = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
    clock["accept"] = accept
    delay, retry = scheduled.pop()
    assert delay == 60_000
    retry()
    assert "Đang tải" in text()
    reader.finish()
    assert confirmations == [{"parent": top}] and top.winfo_exists()
    assert repair.cget("state") == "normal" and not scheduled
    assert not app._position_history_requests and not queue.list_all()
    if accept:
        assert "Đã đo MAE/MFE 3 phiên" in text() and "Tự bán tạm dừng" in text()
        assert trades.get(cycle.id).to_dict() == {**before, "auto_sell_enabled": False}
        assert next(w for w in _widgets(top) if isinstance(w, ctk.CTkButton) and w.cget("text").startswith("BOT · ")).cget("text") == "BOT · OFF"
    else:
        assert "Đã hủy đo lại" in text()
        assert trades.get(cycle.id).to_dict() == before and rules.position_metrics("HDB", cycle.id) == metrics


def test_closing_an_unconfirmed_read_discards_late_result_without_a_prompt(history_popup):
    app, top, repair, _, reader, _, trades, rules, _, cycle, _, confirmations, _ = history_popup
    before, metrics = cycle.to_dict(), rules.position_metrics("HDB", cycle.id)
    repair.invoke()
    top.destroy()
    reader.finish()
    assert not app._position_history_requests and not confirmations
    assert trades.get(cycle.id).to_dict() == before and rules.position_metrics("HDB", cycle.id) == metrics


def test_closing_after_partial_confirmation_keeps_background_completion_without_another_prompt(history_popup):
    app, top, repair, text, reader, client, trades, rules, _, cycle, scheduled, confirmations, clock = history_popup
    clock["now"] = NOW.replace(hour=9, minute=10)
    client.minutes[14] = payload([])
    before = cycle.to_dict()
    repair.invoke()
    reader.finish()
    assert "Chờ nến 2026-10-14" in text() and "60 giây" in text()
    top.destroy()
    request = app._position_history_requests[cycle.id]
    assert request["parent"] is None and request["on_progress"] is None
    clock["now"] = NOW.replace(hour=9, minute=31)
    client.minutes[14] = payload([(stamp(14, "09:30:00"), 23, 24, 21, 23, 100)])
    scheduled.pop()[1]()
    reader.finish()
    assert len(confirmations) == 1 and not scheduled and not app._position_history_requests
    assert not rules.position_metrics("HDB", cycle.id)["price_history_evidence"]["pending_sessions"]
    assert trades.get(cycle.id).to_dict() == {**before, "auto_sell_enabled": False}


def test_explicit_history_read_refreshes_calendar_instead_of_reusing_short_cache(tmp_path):
    _, _, _, cycle = historical_account(tmp_path)
    client = HistoryClient()
    reads = []
    fresh = list(client.calendar)
    def calendar(*, force=False):
        reads.append(force)
        return fresh if force else fresh[-2:]
    client.get_working_dates = calendar
    history = load_position_history(client, cycle, now=NOW)
    assert reads == [True] and history["loaded_sessions"] == 3
