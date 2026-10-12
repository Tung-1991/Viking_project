"""Exercise both real price labels with synthetic prices and no live services."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from support.tests.test_app_startup import STARTUP_SMOKE


QUOTE_PROBE = r'''
        from copy import deepcopy
        from viking_v2.dashboard.quote_direction import TICK_UP_BG, TICK_DOWN_BG
        from viking_v2.dashboard.view import COL_SURFACE_2, COL_RED, COL_WARN
        app.update()
        before_settings = app.settings.to_dict()
        before_runtime = app.bridge.read_config().to_dict()
        app._preview_entry_checks = lambda *_a, **_kw: {
            "order_budget": 7_500_000, "available_cash": 50_000_000, "nav": 50_000_000,
        }
        app._cached_fee_rate = lambda *_a: .0012
        app.snapshots = {mode: ({"equity": 50_000_000, "availableCash": 50_000_000}, [], [])
                         for mode in ("REAL", "PAPER")}
        app.symbol.set("HDB")
        app.order_type.set("MARKET")
        app.quantity.delete(0, tk.END)
        app.quantity.insert(0, "100")
        source = {}
        app.bridge.read_status = lambda: source
        for price, background in ((22.3, COL_SURFACE_2), (22.4, TICK_UP_BG),
                                  (22.35, TICK_DOWN_BG), (22.35, TICK_DOWN_BG)):
            quote = {"symbol": "HDB", "price": price, "reference": 22.5, "ceiling_price": 24,
                     "timestamp": time.time(), "source": "WS"}
            source = {"market_status": "OPEN", "ticks": {"HDB": quote}}
            original = deepcopy(source)
            app._current_tick = dict(quote)
            app._current_tick_price = price
            app._current_market_status = "OPEN"
            app._update_order_preview()
            app._refresh_full_order_preview(source)
            app.update()
            for label in (app.lbl_price, app.preview_live_value):
                assert label.cget("fg_color") == background, label.cget("fg_color")
                assert label.cget("text") == f"{price * 1000:,.0f}"
            assert app.lbl_price.cget("text_color") == COL_RED  # still below reference
            assert source == original
        for phase, changes in (("LUNCH", {}), ("OPEN", {"timestamp": time.time() - 30})):
            quote = {**quote, **changes}
            source = {"market_status": phase, "ticks": {"HDB": quote}}
            app._current_tick = dict(quote)
            app._current_market_status = phase
            app._update_order_preview()
            app._refresh_full_order_preview(source)
            for label in (app.lbl_price, app.preview_live_value):
                assert label.cget("fg_color") == COL_SURFACE_2
                assert label.cget("text_color") == COL_WARN
        assert app.settings.to_dict() == before_settings
        assert app.bridge.read_config().to_dict() == before_runtime
        assert app.quantity.get() == "100" and not app.queue.list_all()
        assert not callback_errors, callback_errors
        print("QUOTE_DIRECTION_OK", os.environ["STARTUP_SMOKE_MODE"], os.environ["STARTUP_SMOKE_SCALING"], flush=True)
'''


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("scaling", ["1.0", "1.5"])
def test_native_price_backgrounds_preserve_settings_queue_and_order_inputs(mode, scaling):
    project = Path(__file__).resolve().parents[2]
    marker = '        # Native Tk typing must survive periodic AUTO refreshes.'
    cleanup = STARTUP_SMOKE[STARTUP_SMOKE.rindex('    finally:\n        for popup in popups:'):]
    script = STARTUP_SMOKE.split(marker)[0] + QUOTE_PROBE + cleanup
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=project,
        env={**os.environ, "STARTUP_SMOKE_MODE": mode, "STARTUP_SMOKE_SCALING": scaling},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    artifacts = project / ".artifacts/quote_direction_20261012"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / f"native_{mode.lower()}_{scaling}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QUOTE_DIRECTION_OK" in result.stdout
