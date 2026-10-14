"""Position reads yield to Tk and use a worker independent of broker requests."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from viking_v2.dashboard.actions import DashboardActionsMixin
from support.tests.test_demo_order_display import _widgets
from support.tests.test_position_modes_and_bulk_close import batch_app


def pump(root, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Position display did not settle"
        ready = tk.BooleanVar(master=root, value=False)
        root.after(20, lambda: ready.set(True))
        root.wait_variable(ready)


def prepare(root, monkeypatch, tmp_path):
    app, broker, actions = batch_app(tmp_path)
    for key, value in vars(app).items():
        monkeypatch.setattr(root, key, value, raising=False)
    for key, value in {"_shared_tick": lambda *_: {"price": 100}, "_row_time": lambda *_: "14:09",
                       "_log": lambda *_: None, "_refresh_local": lambda: None}.items():
        monkeypatch.setattr(root, key, value, raising=False)
    return app, broker, actions[0]


def open_popup(root, action):
    previous = set(root.winfo_children())
    DashboardActionsMixin._show_position_management(root, action)
    top, = [w for w in root.winfo_children() if w not in previous and isinstance(w, ctk.CTkToplevel)]
    return top


@pytest.mark.parametrize("waiting", ["ACCOUNT_LOCK", "BROKER_WORKER"])
def test_loading_does_not_block_tk_or_wait_behind_broker(ui_root, monkeypatch, tmp_path, waiting):
    app, broker, action = prepare(ui_root, monkeypatch, tmp_path)
    before = app.trade_state.get(action["trade_id"]).to_dict()
    ready, release = threading.Event(), threading.Event()
    view_worker, order_worker = ThreadPoolExecutor(1), ThreadPoolExecutor(1)
    monkeypatch.setattr(ui_root, "_position_read_executor", view_worker, raising=False)
    monkeypatch.setattr(ui_root, "_io_executor", order_worker, raising=False)
    main_thread = threading.get_ident()
    observed_threads = []
    monkeypatch.setattr(ui_root, "_shared_tick", lambda *_: (observed_threads.append(threading.get_ident()), {"price": 100})[1])

    def busy():
        connection = sqlite3.connect(tmp_path / "trading.sqlite3") if waiting == "ACCOUNT_LOCK" else None
        try:
            if connection is not None:
                connection.execute("BEGIN IMMEDIATE")
            ready.set()
            assert release.wait(8)
        finally:
            if connection is not None:
                connection.rollback()
                connection.close()
    blocked = order_worker.submit(busy)
    assert ready.wait(2)
    top = None
    try:
        top = open_popup(ui_root, action)
        pulse = tk.BooleanVar(master=ui_root, value=False)
        ui_root.after(30, lambda: pulse.set(True))
        ui_root.wait_variable(pulse)
        assert not blocked.done(), "Tk must handle input before the blocking work finishes"
        if waiting == "ACCOUNT_LOCK":
            assert not top._viking_load_future.done()
            assert any(w.cget("text") == "ĐANG ĐỌC VỊ THẾ…" for w in _widgets(top) if isinstance(w, ctk.CTkLabel))
            release.set()
            blocked.result(3)
        pump(ui_root, lambda: any(isinstance(w, ctk.CTkButton) and w.cget("text").startswith("BOT · ") for w in _widgets(top)))
        assert observed_threads and all(thread != main_thread for thread in observed_threads)
        assert app.trade_state.get(action["trade_id"]).to_dict() == before
        assert app.queue.list_all() == [] and broker.sent == []
    finally:
        release.set()
        if top is not None and top.winfo_exists():
            top.destroy()
        order_worker.shutdown(wait=True)
        view_worker.shutdown(wait=True)


@pytest.mark.parametrize("fail", [False, True])
def test_closing_pending_read_cancels_poll_without_late_widgets(ui_root, monkeypatch, tmp_path, fail):
    app, broker, action = prepare(ui_root, monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    worker = ThreadPoolExecutor(1)
    monkeypatch.setattr(ui_root, "_position_read_executor", worker, raising=False)
    errors = []
    monkeypatch.setattr(ui_root, "report_callback_exception", lambda *args: errors.append(args))
    def quote(*_):
        entered.set()
        assert release.wait(5)
        if fail:
            raise RuntimeError("offline read failed")
        return {"price": 100}
    monkeypatch.setattr(ui_root, "_shared_tick", quote)
    top = open_popup(ui_root, action)
    try:
        assert entered.wait(2)
        poll = top._viking_load_poll
        assert poll in ui_root.tk.call("after", "info")
        top.destroy()
        assert poll not in ui_root.tk.call("after", "info")
        release.set()
        done = worker.submit(lambda: "worker reusable")
        pump(ui_root, done.done)
        assert done.result() == "worker reusable"
        assert errors == [] and not top.winfo_exists()
        assert app.queue.list_all() == [] and broker.sent == []
    finally:
        release.set()
        worker.shutdown(wait=True)


def test_popup_constructor_does_not_run_unrelated_idle_work(ui_root, monkeypatch, tmp_path):
    _, _, action = prepare(ui_root, monkeypatch, tmp_path)
    pending = []
    ui_root.after_idle(lambda: pending.append(True))
    top = open_popup(ui_root, action)
    try:
        assert pending == []
        ui_root.update_idletasks()
        assert pending == [True]
    finally:
        top.destroy()


def test_failed_display_read_keeps_close_available_and_does_not_offer_stale_controls(ui_root, monkeypatch, tmp_path):
    app, broker, action = prepare(ui_root, monkeypatch, tmp_path)
    worker = ThreadPoolExecutor(1)
    monkeypatch.setattr(ui_root, "_position_read_executor", worker, raising=False)
    def fail(*_):
        raise RuntimeError("offline quote read failed")
    monkeypatch.setattr(ui_root, "_shared_tick", fail)
    top = open_popup(ui_root, action)
    try:
        pump(ui_root, lambda: any(isinstance(w, ctk.CTkLabel) and "KHÔNG ĐỌC ĐƯỢC" in w.cget("text") for w in _widgets(top)))
        widgets = list(_widgets(top))
        assert not any(isinstance(w, (ctk.CTkEntry, ctk.CTkSwitch)) for w in widgets)
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "ĐÓNG").invoke()
        assert not top.winfo_exists()
        assert app.queue.list_all() == [] and broker.sent == []
    finally:
        if top.winfo_exists():
            top.destroy()
        worker.shutdown(wait=True)


def test_menu_reuses_images_and_keeps_actions_current(ui_root, monkeypatch):
    from PIL import ImageTk
    tree = ttk.Treeview(ui_root)
    for row in ("A", "B"):
        tree.insert("", "end", iid=row)
    subject = SimpleNamespace(trees={"REAL": tree}, _running_row_actions={"REAL": {
        "A": {"kind": "position", "symbol": "A"}, "B": {"kind": "order", "symbol": "B", "cancellable": True}}})
    opened, closed, images = [], [], []
    subject._open_running_selection = lambda rows: opened.extend(rows)
    subject._close_running_selection = lambda rows: closed.extend(rows)
    original_image = ImageTk.PhotoImage
    def create_image(*args, **kwargs):
        result = original_image(*args, **kwargs)
        images.append(result)
        return result
    monkeypatch.setattr(ImageTk, "PhotoImage", create_image)
    monkeypatch.setattr(tk.Menu, "post", lambda *_: None)
    # This test subject is not a Tk widget; its menu belongs to the real root.
    original_menu = tk.Menu
    monkeypatch.setattr(tk, "Menu", lambda _parent, **kwargs: original_menu(ui_root, **kwargs))
    try:
        for row in ("A", "B"):
            monkeypatch.setattr(tree, "identify_row", lambda _y, row=row: row)
            DashboardActionsMixin._running_right_click(subject, SimpleNamespace(widget=tree, y=1, x_root=0, y_root=0))
            menu = subject._running_context_menu
            menu.invoke(0 if row == "A" else 1)
        assert len(images) == 2
        assert [row["symbol"] for row in opened] == ["A"]
        assert [row["symbol"] for row in closed] == ["B"]
        assert menu.entrycget(0, "state") == "disabled"
        assert menu.entrycget(1, "state") == "normal"
        scale = ctk.ScalingTracker.get_widget_scaling(tree)
        monkeypatch.setattr(ctk.ScalingTracker, "get_widget_scaling", classmethod(lambda cls, widget: scale * 1.5))
        DashboardActionsMixin._running_right_click(subject, SimpleNamespace(widget=tree, y=1, x_root=0, y_root=0))
        assert subject._running_context_menu is not menu
        assert not menu.winfo_exists()
        assert len(images) == 4
    finally:
        subject._running_context_menu.destroy()
        tree.destroy()
