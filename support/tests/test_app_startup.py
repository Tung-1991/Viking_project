"""Exercise the real app constructor and layout in an isolated Tk process."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


STARTUP_SMOKE = r'''
import os
import time
import socket
import tempfile
from pathlib import Path
import subprocess
import tkinter as tk
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
for key in list(os.environ):
    if key.startswith(("DNSE_", "TELE_", "TELEGRAM_")):
        os.environ.pop(key, None)

def forbidden(*args, **kwargs):
    raise AssertionError("Startup smoke must not access network or launch services")

socket.socket.connect = socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden

import customtkinter as ctk
import viking_v2.config as config

with tempfile.TemporaryDirectory(prefix="money-hunter-startup-") as directory:
    root = Path(directory)
    config.RUNTIME_ROOT = root
    config.ACCOUNTS_ROOT = root / "accounts"
    config.ENV_PATH = root / ".env"
    config.update_env.__defaults__ = (config.ENV_PATH,)
    config.save_settings(config.AppSettings(
        paper_mode=os.environ.get("STARTUP_SMOKE_MODE", "PAPER") == "PAPER",
    ), "PAPER")

    from viking_v2.dashboard.window import VikingApp
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup
    from viking_v2.rules.window import RuleSettingsPopup
    from viking_v2.backtest.window import BacktestPopup
    from viking_v2.dashboard.windows import _HoverHint

    DNSEClient.connect = lambda self: None
    # Keep actual constructor, widget creation and rendering. Disable only
    # scheduled service/broker I/O, never the layout under test.
    for name in ("_start_services", "_poll_runtime", "_process_orders", "_refresh_snapshots"):
        setattr(VikingApp, name, lambda self: None)
    ctk.set_appearance_mode("Dark")
    ctk.set_widget_scaling(float(os.environ.get("STARTUP_SMOKE_SCALING", "1.0")))
    app = VikingApp(account_id="PAPER")
    callback_errors = []
    app.report_callback_exception = lambda *args: callback_errors.append(args)
    popups = []
    try:
        assert app.bridge.root.is_relative_to(root)
        assert not app.bridge.read_config().bot_enabled
        assert app.mode.get() == os.environ.get("STARTUP_SMOKE_MODE", "PAPER")
        assert "ⓘ" in app.info_title.cget("text")
        app.update_idletasks()
        # Native Tk typing must survive periodic AUTO refreshes. CTkEntry's
        # programmatic insert() hides the placeholder bug, so use the inner Tk
        # widget just as Tk's keyboard class binding does.
        old_preview_checks = app._preview_entry_checks
        app._preview_entry_checks = lambda *_args, **_kwargs: {
            "order_budget": 7_500_000, "available_cash": 50_000_000, "nav": 50_000_000,
        }
        for book in ("REAL", "PAPER"):
            app.mode.set(book)
            app.quantity.delete(0, tk.END)
            app.quantity._entry.event_generate("<FocusOut>")
            app._suggested_order_quantity(74.2, {})
            assert app.quantity.cget("state") == "normal"
            assert app.quantity.get() == ""
            assert app.quantity.cget("placeholder_text") == "100 CP"
            app.quantity._entry.event_generate("<FocusIn>")
            assert app.quantity._viking_editing
            for _refresh in range(3):
                app._suggested_order_quantity(74.2, {})
                assert app.quantity._entry.get() == ""
            for digit in "200":
                app.quantity._entry.insert(tk.END, digit)
                app.quantity._entry.event_generate("<KeyRelease>")
                app._suggested_order_quantity(74.2, {})
            assert app.quantity.get() == "200"
            assert app.quantity._viking_title_widget.cget("text") == "KL · TAY"
            # Removing the final digit while focused must not restore a
            # placeholder that swallows the next native keystroke.
            app.quantity._entry.delete(0, tk.END)
            app._suggested_order_quantity(74.2, {})
            assert app.quantity._entry.get() == ""
            app.quantity._entry.insert(0, "300")
            app._suggested_order_quantity(74.2, {})
            assert app.quantity.get() == "300"
            app.quantity._entry.delete(0, tk.END)
            app.quantity._entry.event_generate("<FocusOut>")
            app._suggested_order_quantity(74.2, {})
            assert not app.quantity._viking_editing
            assert app.quantity.get() == ""
            assert app.quantity._viking_title_widget.cget("text") == "KL · AUTO"
        app._preview_entry_checks = old_preview_checks
        # The visible money/fee must match the capital guard, not the last-price
        # estimate, including native labels and both book-specific fee sources.
        from viking_v2.dashboard.view import _compact_vnd
        saved_settings = app.settings.to_dict()
        saved_read_status = app.bridge.read_status
        saved_cached_fee = app._cached_fee_rate
        app.settings.priority_symbols = ["MSN", "CTS", "HDB", "IDC"]
        app.settings.priority_capital_enabled = True
        app.settings.priority_total_capital = 50_000_000
        app.settings.priority_allocations = {symbol: {
            "limit_vnd": (5 if symbol == "IDC" else 15) * 1_000_000, "use_pct": 100,
        } for symbol in app.settings.priority_symbols}
        status = {"market_status": "CLOSED", "ticks": {"MSN": {
            "symbol": "MSN", "price": 74.2, "ceiling_price": 79.3,
        }}}
        app.bridge.read_status = lambda: status
        app._cached_fee_rate = lambda *_args: .0012
        app._fee_rates[("MSN", "BUY")] = .0012
        app.symbol.set("MSN")
        app._current_tick_price = 74.2
        app.quantity.delete(0, tk.END)
        app.quantity.insert(0, "200")
        app._select_info_tab("PREVIEW")
        app.update()
        settled_money = tk.BooleanVar(master=app, value=False)
        app.after(250, lambda: settled_money.set(True))
        app.wait_variable(settled_money)
        for book in ("REAL", "PAPER"):
            app.mode.set(book)
            app.order_type.set("MARKET")
            app.snapshots[book] = ({"equity": 50_000_000, "availableCash": 50_000_000}, [], [])
            app._update_order_preview()
            total = 15_860_000 * (1 + (.0012 if book == "REAL" else app.settings.buy_fee_pct / 100))
            assert app.lbl_order_value.cget("text") == f"{total:,.0f} ₫"
            assert app.preview_cash_value.cget("text") == _compact_vnd(total)
            assert app.lbl_fee_preview.cget("text") == app.preview_fee_value.cget("text")
            assert "79,300" in app._ticket_money_hint()
            assert " + phí " in app._ticket_money_hint()
            assert "không cộng lần nữa" in app._ticket_fee_hint()
            app.update_idletasks()
            if os.environ.get("VIKING_CAPTURE_UI") == "1":
                from PIL import ImageGrab
                import ctypes
                capture_dir = Path.cwd() / ".artifacts" / "ui-review"
                capture_dir.mkdir(parents=True, exist_ok=True)
                hwnd = ctypes.windll.user32.GetParent(app.winfo_id())
                ImageGrab.grab(window=hwnd).save(capture_dir / f"money-{book}-{os.environ['STARTUP_SMOKE_SCALING']}.png")
            # Check native money labels fit without extra rows/wrapping.
            from tkinter import font as tkfont
            for label in (app.lbl_order_value, app.preview_cash_value, app.preview_fee_value):
                font = tkfont.Font(root=app, font=label._label.cget("font"))
                assert font.measure(label.cget("text")) <= label.winfo_width(), (
                    label.cget("text"), font.measure(label.cget("text")), label.winfo_width(),
                    [(ancestor.winfo_class(), ancestor.winfo_width(), ancestor.winfo_height(), ancestor.winfo_ismapped())
                     for ancestor in (label.master, label.master.master, app.preview_scroll, app.log_tabview, app)])
            app.order_type.set("LO")
            app.price.delete(0, tk.END)
            app.price.insert(0, "74,200")
            app._update_order_preview()
            assert "giá LO" in app._ticket_money_hint()
        app.settings = config.AppSettings.from_dict(saved_settings)
        app.bridge.read_status = saved_read_status
        app._cached_fee_rate = saved_cached_fee
        app.snapshots = {}
        app.quantity.delete(0, tk.END)
        app.order_type.set("MARKET")
        app.mode.set(os.environ.get("STARTUP_SMOKE_MODE", "PAPER"))
        for tab in ("Manual", "Bot", "PREVIEW"):
            app._select_info_tab(tab)
            assert app.log_tabview.get() == tab
        app._set_log_unread("bot", True)
        app._set_log_unread("manual", True)
        app._select_info_tab("Bot")
        app._select_info_tab("Manual")
        app._select_info_tab("PREVIEW")
        for mode in ("REAL", "PAPER"):
            app.mode.set(mode)
            app._refresh_full_order_preview({})
            assert f" {mode} " in app.preview_order_title.cget("text")
            settings_before = app.settings.to_dict()
            app.preview_p1_swap.invoke()
            assert app._preview_p1_alternate
            app.preview_p1_swap.invoke()
            assert not app._preview_p1_alternate
            assert app.settings.to_dict() == settings_before
            assert app.mode.get() == mode
        hint = _HoverHint(app.info_title, "PREVIEW / Manual / Bot", placement="inside")
        hint._show()
        assert hint.popup is not None
        hint._hide()
        # The new diagnostics use existing labels only. Exercise native hover
        # text at both CTk scalings without live services or account credentials.
        saved_read_status = app.bridge.read_status
        app.bridge.read_status = lambda: {
            "market_status": "OPEN", "ticks": {"MSN": {
                "symbol": "MSN", "price": 74.4, "timestamp": time.time(), "source": "WS",
            }},
        }
        app.symbol.set("MSN")
        health_cells = len(app.preview_health_trade.master.grid_slaves())
        assert health_cells == 7
        for widget, callback in (
            (app.preview_health_hint, app._api_health_hint),
            (app.preview_health_trade, app._quote_health_hint),
            (app.preview_live_value, app._quote_health_hint),
            (app.preview_route_value, app._order_lifecycle_hint),
            (app.preview_rule_reason, app._rule_decision_hint),
        ):
            hint = _HoverHint(widget, callback, placement="inside")
            hint._show()
            assert hint.popup is not None and hint.popup.cget("text")
            app.update_idletasks()
            assert hint.popup.winfo_x() >= 0 and hint.popup.winfo_y() >= 0
            assert hint.popup.winfo_width() <= app.winfo_width()
            assert hint.popup.winfo_height() <= app.winfo_height()
            hint._hide()
        assert len(app.preview_health_trade.master.grid_slaves()) == health_cells
        app.bridge.read_status = saved_read_status
        popups.append(RuleSettingsPopup(app, app.settings, app.account_id, lambda: None))
        popups.append(ConnectionPopup(app, app.settings, app.account_id, app.real, lambda: None))
        popups.append(BacktestPopup(app, app.settings, None))
        app.update_idletasks()
        settled = tk.BooleanVar(master=app, value=False)
        app.after(1000, lambda: settled.set(True))
        app.wait_variable(settled)
        assert not callback_errors, callback_errors
        assert not app.queue.list_all()
        assert not app.bridge.read_config().bot_enabled
    finally:
        for popup in popups:
            closer = getattr(popup, "close", None) or getattr(popup, "_close", None)
            if callable(closer) and popup.top.winfo_exists():
                closer()
        app.close()
        for handler in app.logger.handlers:
            handler.close()
        app.logger.handlers.clear()
print("STARTUP_SMOKE_OK")
'''


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("scaling", ["1.0", "1.5"])
def test_actual_app_startup_tabs_and_popups_without_live_services(mode, scaling):
    project = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-B", "-c", STARTUP_SMOKE], cwd=project,
        env={**os.environ, "STARTUP_SMOKE_MODE": mode, "STARTUP_SMOKE_SCALING": scaling},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STARTUP_SMOKE_OK" in result.stdout
