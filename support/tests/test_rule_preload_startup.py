"""Measure the actual mapped dashboard's automatic, hidden RULE warmup."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


PRELOAD_PROBE = r'''
        import sys
        app.update()
        assert app.winfo_ismapped()
        settings_before = app.settings.to_dict()
        runtime_before = app.bridge.read_config().to_dict()
        focus_calls = []
        read_calls = []
        app.trade_state.loss_blocks = lambda mode: read_calls.append(mode) or []
        app._set_tool_popup_state = lambda name, visible: focus_calls.append((name, visible))
        app.quantity._entry.focus_force()
        app.update()
        initial_focus = app.focus_get()
        pulses = []
        measuring = [True]
        started = time.monotonic()
        def pulse():
            pulses.append(time.monotonic() - started)
            if measuring[0]:
                app.after(50, pulse)
        app.after(50, pulse)
        deadline = time.monotonic() + 12
        while app._rule_settings_popup is None or app._rule_settings_popup._building:
            assert time.monotonic() < deadline, "Automatic warmup did not complete"
            settled = tk.BooleanVar(master=app, value=False)
            app.after(20, lambda: settled.set(True))
            app.wait_variable(settled)
        measuring[0] = False
        popup = app._rule_settings_popup
        if sys.platform == "win32":
            assert popup.top.attributes("-alpha") == 0
            assert popup.top.attributes("-disabled") and popup.top.attributes("-toolwindow")
        else:
            assert popup.top.state() == "withdrawn" and not popup.top.winfo_ismapped()
        assert not popup._has_been_shown
        assert not read_calls and not focus_calls
        assert app.focus_get() is initial_focus
        assert popup.buy_ema_fast.get() and popup.sell_ema_fast.get() and popup.buy_fee.get()
        assert app.settings.to_dict() == settings_before
        assert app.bridge.read_config().to_dict() == runtime_before
        assert not app.queue.list_all()
        gaps = [b - a for a, b in zip([0, *pulses], pulses)]
        assert pulses and max(gaps) < 2, gaps
        clicked = time.monotonic()
        app._rule_settings()
        callback_time = time.monotonic() - clicked
        assert app._rule_settings_popup is popup and not popup._building
        assert popup.top.attributes("-alpha") == 1
        if sys.platform == "win32":
            assert not popup.top.attributes("-disabled") and not popup.top.attributes("-toolwindow")
        app.update()
        first_paint = time.monotonic() - clicked
        assert popup.top.winfo_ismapped()
        assert popup.save_button.cget("state") == "normal"
        for tab in ("E/M", "THỰC THI", "NGHIỆP VỤ"):
            popup.tabs.set(tab)
        settled = tk.BooleanVar(master=app, value=False)
        app.after(200, lambda: settled.set(True))
        app.wait_variable(settled)
        assert popup.tabs.tab("NGHIỆP VỤ").winfo_ismapped()
        popup._close()
        assert app._rule_preload_after is not None
        app._rule_settings_popup = None
        assert app.settings.to_dict() == settings_before
        assert app.bridge.read_config().to_dict() == runtime_before
        assert not app.queue.list_all() and not callback_errors, callback_errors
        handle = app._rule_preload_after
        app.running = False
        app._cancel_rule_preload()
        assert handle not in app.tk.call("after", "info")
        print("RULE_PRELOAD_OK", os.environ["STARTUP_SMOKE_MODE"], os.environ["RULE_PRELOAD_DPI"],
              "warm_input_gap", round(max(gaps), 4), "click_callback", round(callback_time, 4),
              "first_paint", round(first_paint, 4), flush=True)
'''


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("dpi", ["1.0", "1.5", "2.0"])
def test_automatic_preload_does_not_show_steal_focus_write_settings_or_touch_trading(mode, dpi):
    project = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("rule_preload_harness", project / "support/tests/test_app_startup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = '        # Native Tk typing must survive periodic AUTO refreshes.'
    cleanup = module.STARTUP_SMOKE[module.STARTUP_SMOKE.rindex('    finally:\n        for popup in popups:'):]
    script = module.STARTUP_SMOKE.split(marker)[0] + PRELOAD_PROBE + cleanup
    script = script.replace('    app = VikingApp(account_id="PAPER")', '''    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, window: float(os.environ["RULE_PRELOAD_DPI"]))
    app = VikingApp(account_id="PAPER")''', 1)
    artifacts = project / ".artifacts/rule_preload_20261012"
    artifacts.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=project,
        env={**os.environ, "STARTUP_SMOKE_MODE": mode, "STARTUP_SMOKE_SCALING": "1.0", "RULE_PRELOAD_DPI": dpi},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    (artifacts / f"app_{mode.lower()}_{dpi}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RULE_PRELOAD_OK" in result.stdout
