"""Exercise the real app constructor and layout in an isolated Tk process."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


STARTUP_SMOKE = r'''
import os
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
        hint = _HoverHint(app.info_title, "PREVIEW / Manual / Bot", placement="inside")
        hint._show()
        assert hint.popup is not None
        hint._hide()
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
