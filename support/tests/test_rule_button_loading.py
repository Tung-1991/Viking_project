"""Open RULE through the real dashboard in a mapped, isolated/offline app."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


RULE_BUTTON_PROBE = r'''
        app.update()
        assert app.winfo_ismapped()
        initial_settings = app.settings.to_dict()
        window_count = sum(isinstance(child, ctk.CTkToplevel) for child in app.winfo_children())
        pulses = []
        measuring = [True]
        started = time.monotonic()
        def input_pulse():
            popup = app._rule_settings_popup
            pulses.append((time.monotonic() - started, bool(popup and popup._building)))
            if measuring[0]:
                app.after(50, input_pulse)
        app.after(50, input_pulse)
        app.after(0, app._rule_settings)  # a second click arriving during construction
        app._rule_settings()
        popup = app._rule_settings_popup
        assert popup.save_button.cget("state") == "disabled"
        assert popup._building
        deadline = time.monotonic() + 12
        while popup._building:
            assert time.monotonic() < deadline, popup.status.cget("text")
            ready = tk.BooleanVar(master=app, value=False)
            app.after(20, lambda: ready.set(True))
            app.wait_variable(ready)
        measuring[0] = False
        gaps = [b - a for a, b in zip([0, *[p[0] for p in pulses]], [p[0] for p in pulses])]
        assert any(loading for _, loading in pulses), pulses
        assert max(gaps) < 2, gaps
        print("RULE_BUTTON_READY", os.environ.get("STARTUP_SMOKE_MODE"),
              os.environ.get("RULE_BUTTON_DPI"), round(time.monotonic() - started, 3),
              "max_input_gap", round(max(gaps), 3), flush=True)
        assert popup.save_button.cget("state") == "normal"
        assert popup.buy_ema_fast.get() and popup.sell_ema_fast.get() and popup.buy_fee.get()
        assert sum(isinstance(child, ctk.CTkToplevel) for child in app.winfo_children()) == window_count + 1
        app._rule_settings()
        assert app._rule_settings_popup is popup
        assert app.settings.to_dict() == initial_settings
        assert not app.queue.list_all()
        assert not app.bridge.read_config().bot_enabled
        assert not callback_errors, callback_errors
        popup._close()
        app._rule_settings_popup = None
'''


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("dpi", ["1.0", "1.5", "2.0"])
def test_rule_button_loading_yields_reuses_popup_and_keeps_settings_and_orders(mode, dpi):
    project = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("rule_button_harness", project / "support/tests/test_app_startup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = '        # Native Tk typing must survive periodic AUTO refreshes.'
    cleanup = module.STARTUP_SMOKE[module.STARTUP_SMOKE.rindex('    finally:\n        for popup in popups:'):]
    script = module.STARTUP_SMOKE.split(marker)[0] + RULE_BUTTON_PROBE + cleanup
    script = script.replace('    app = VikingApp(account_id="PAPER")', '''    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, window: float(os.environ["RULE_BUTTON_DPI"]))
    app = VikingApp(account_id="PAPER")''', 1)
    artifacts = project / ".artifacts/rule_first_paint_20261012"
    artifacts.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=project,
                            env={**os.environ, "STARTUP_SMOKE_MODE": mode, "STARTUP_SMOKE_SCALING": "1.0",
                                 "RULE_BUTTON_DPI": dpi}, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    (artifacts / f"app_{mode.lower()}_{dpi}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
