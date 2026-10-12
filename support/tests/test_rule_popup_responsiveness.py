"""RULE remains responsive while its display-only account read is waiting."""
import sqlite3
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor

import pytest

from viking_v2.config import AppSettings
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.trading.state import TradeStateStore


def pump(root, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "RULE background display did not settle"
        ready = tk.BooleanVar(master=root, value=False)
        root.after(20, lambda: ready.set(True))
        root.wait_variable(ready)


@pytest.mark.parametrize("opening", ["NEW", "REOPEN"])
def test_account_writer_does_not_block_rule_opening_or_tk_heartbeat(ui_root, tmp_path, opening):
    state = TradeStateStore(tmp_path / "trade_state.json")
    state.loss_blocks("PAPER")
    popup = None
    if opening == "REOPEN":
        popup = RuleSettingsPopup(ui_root, AppSettings(), "PAPER", lambda: None, trade_state=state)
        pump(ui_root, lambda: popup._block_future is None)
        ui_root.update_idletasks()
        popup.hide()
    ready = threading.Event()
    release = threading.Event()
    released = threading.Event()
    errors = []
    observed = []

    def writer():
        connection = sqlite3.connect(next(tmp_path.glob("*.sqlite3")))
        try:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            release.wait(4)
            connection.rollback()
        except Exception as error:
            errors.append(error)
        finally:
            connection.close()
            released.set()

    thread = threading.Thread(target=writer)
    thread.start()
    assert ready.wait(2)
    pulse = tk.BooleanVar(master=ui_root, value=False)
    started = time.monotonic()
    def open_rule():
        nonlocal popup
        if popup is None:
            popup = RuleSettingsPopup(ui_root, AppSettings(), "PAPER", lambda: None, trade_state=state)
        else:
            popup.show()
    ui_root.after(0, open_rule)
    ui_root.after(50, lambda: (observed.append((time.monotonic() - started, released.is_set())), pulse.set(True)))
    try:
        ui_root.wait_variable(pulse)
        assert observed[0][1] is False, observed
        assert popup.block_unlock.cget("state") == "disabled"
        assert popup.block_unlock.cget("text") == "ĐANG ĐỌC KHÓA LỖ"
        release.set()
        thread.join(5)
        pump(ui_root, lambda: popup._block_future is None)
        assert popup.block_unlock.cget("text") == "CHƯA CÓ MÃ KHÓA TAY"
        assert errors == []
    finally:
        release.set()
        thread.join(5)
        if popup is not None:
            popup._close()


class WaitingState:
    def __init__(self, gate, *, fail=False):
        self.gate = gate
        self.fail = fail
        self.calls = []

    def loss_blocks(self, mode):
        self.calls.append((mode, threading.get_ident()))
        assert self.gate.wait(5)
        if self.fail:
            raise RuntimeError("offline blocked read")
        return ["MSN"] if mode == "REAL" else ["FPT"]


def test_pending_read_uses_current_book_and_preserves_selected_symbol(ui_root):
    gate = threading.Event()
    state = WaitingState(gate)
    popup = RuleSettingsPopup(ui_root, AppSettings(), "PAPER", lambda: None, trade_state=state)
    try:
        popup.block_book.set("REAL")
        popup._refresh_blocks()
        gate.set()
        pump(ui_root, lambda: popup._block_future is None)
        assert popup.block_symbol.cget("values") == ["MSN"]
        assert popup.block_symbol.get() == "MSN"
        assert all(thread_id != threading.get_ident() for _, thread_id in state.calls)
        popup._refresh_blocks()
        pump(ui_root, lambda: popup._block_future is None)
        assert popup.block_symbol.get() == "MSN"
    finally:
        gate.set()
        popup._close()


def test_failed_read_does_not_claim_no_locks_and_can_recover(ui_root):
    gate = threading.Event()
    gate.set()
    state = WaitingState(gate, fail=True)
    popup = RuleSettingsPopup(ui_root, AppSettings(), "PAPER", lambda: None, trade_state=state)
    try:
        pump(ui_root, lambda: popup._block_future is None)
        assert popup.block_unlock.cget("text") == "KHÔNG ĐỌC ĐƯỢC KHÓA"
        assert popup.block_unlock.cget("state") == "disabled"
        assert popup.loss_unlock_row.winfo_manager() == "pack"
        state.fail = False
        popup._refresh_blocks()
        pump(ui_root, lambda: popup._block_future is None)
        assert popup.block_symbol.get() == "FPT"
        assert popup.block_unlock.cget("state") == "normal"
    finally:
        popup._close()


def test_closing_pending_popup_cancels_tk_poll_and_keeps_shared_executor_usable(ui_root, monkeypatch):
    gate = threading.Event()
    state = WaitingState(gate)
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(ui_root, "_io_executor", executor, raising=False)
    popup = RuleSettingsPopup(ui_root, AppSettings(), "PAPER", lambda: None, trade_state=state)
    callback_errors = []
    monkeypatch.setattr(ui_root, "report_callback_exception", lambda *args: callback_errors.append(args))
    try:
        poll = popup._block_poll_after
        assert poll in ui_root.tk.call("after", "info")
        popup._close()
        assert poll not in ui_root.tk.call("after", "info")
        gate.set()
        done = executor.submit(lambda: "executor alive")
        pump(ui_root, done.done)
        assert done.result() == "executor alive"
        assert callback_errors == []
    finally:
        gate.set()
        executor.shutdown(wait=True)
