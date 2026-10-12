"""Exercise remote-like DPI changes in the actual isolated/offline application."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys

import pytest


DPI_PROBE = r'''
        import faulthandler
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
        original_dpi_reader = ScalingTracker.__dict__["get_window_dpi_scaling"]
        baseline = (app._current_width, app._current_height)
        try:
            for step, dpi in enumerate((1.0, 1.25, 1.5, 1.0, 1.5, 1.25, 1.0, 1.5, 1.0)):
                print("DPI_BEGIN", step, dpi, flush=True)
                faulthandler.dump_traceback_later(10, repeat=True)
                ScalingTracker.get_window_dpi_scaling = classmethod(lambda cls, window, value=dpi: value)
                ScalingTracker.check_dpi_scaling()
                app._select_info_tab("PREVIEW")
                app.event_generate("<Button-1>", x=5, y=5)
                heartbeat = tk.BooleanVar(master=app, value=False)
                started = time.monotonic()
                app.after(100, lambda: heartbeat.set(True))
                app.wait_variable(heartbeat)
                elapsed = time.monotonic() - started
                faulthandler.cancel_dump_traceback_later()
                print("DPI_STEP", step, dpi, "heartbeat_seconds", round(elapsed, 3),
                      "logical_size", (app._current_width, app._current_height), flush=True)
                assert elapsed < 5, (step, dpi, elapsed)
            # Returning to 100% must not retain the oversized intermediate layout.
            assert abs(app._current_width - baseline[0]) < 2, (baseline, app._current_width)
            assert abs(app._current_height - baseline[1]) < 2, (baseline, app._current_height)
            assert not callback_errors, callback_errors
        finally:
            ScalingTracker.get_window_dpi_scaling = original_dpi_reader
            faulthandler.cancel_dump_traceback_later()
'''


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_remote_like_dpi_changes_and_clicks_preserve_responsiveness_and_size(mode):
    project = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("startup_harness", project / "support/tests/test_app_startup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = "        assert not callback_errors, callback_errors\n"
    assert module.STARTUP_SMOKE.count(marker) == 1
    script = module.STARTUP_SMOKE.replace(marker, DPI_PROBE + marker)
    artifacts = project / ".artifacts" / "dpi_reconnect_20261012"
    artifacts.mkdir(parents=True, exist_ok=True)
    output_path = artifacts / f"{mode.lower()}_output.txt"
    try:
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=project,
                                env={**os.environ, "STARTUP_SMOKE_MODE": mode, "STARTUP_SMOKE_SCALING": "1.0"},
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        output_path.write_text(decoded(error.stdout) + decoded(error.stderr), encoding="utf-8")
        raise AssertionError(f"UI probe exceeded 45 seconds; stack saved in {output_path}") from None
    output_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STARTUP_SMOKE_OK" in result.stdout


def test_draw_failure_restores_normal_tk_idle_processing(ui_root):
    import customtkinter as ctk
    from viking_v2.dashboard.ui_compat import _without_idle_flush
    scrollbar = ctk.CTkScrollbar(ui_root)
    handled = []
    try:
        scrollbar._canvas.after_idle(lambda: handled.append(True))
        def failing_draw(widget):
            widget._canvas.update_idletasks()
            assert handled == []
            raise ValueError("offline drawing failure")
        with pytest.raises(ValueError, match="offline drawing failure"):
            _without_idle_flush(failing_draw)(scrollbar)
        scrollbar._canvas.update_idletasks()
        assert handled == [True]
    finally:
        scrollbar.destroy()
