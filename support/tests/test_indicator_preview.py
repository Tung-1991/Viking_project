"""Missing decisions must not hide available read-only indicator data."""
from concurrent.futures import Future
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import time

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.rules.business import average_true_range_pct, indicator_snapshot
from viking_v2.trading.market import VN_TZ, market_now, merge_tick_into_daily_bars


def history():
    today = market_now().date()
    return [{"time": int(datetime.combine(today - timedelta(days=80-i), datetime.min.time(), VN_TZ).timestamp()),
             "open": 10+i*.02, "high": 11+i*.02, "low": 9+i*.02,
             "close": 10+i*.02+(i % 3)*.03, "closed": True}
            for i in range(80)]


def subject(mode="PAPER"):
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view.mode = SimpleNamespace(get=lambda: mode)
    view._preview_bars_symbol = "AAA"
    view._preview_bars = history()
    return view


def test_bars_show_ema_rsi_atr_without_any_decision_or_bot_entry():
    view = subject()
    status = {"decisions_by_mode": {"REAL": {}, "PAPER": {}}, "bot_enabled": False}
    before = deepcopy((status, view._preview_bars, view.settings))
    result = view._preview_indicator_details(status, "AAA")
    expected = indicator_snapshot(view._preview_bars)
    assert result["indicators"] == expected
    assert result["atr14_daily_pct"] == pytest.approx(average_true_range_pct(view._preview_bars))
    assert result["dynamic_start_pct"] == pytest.approx(result["atr14_daily_pct"] * .55)
    assert result["dynamic_trail_pct"] == pytest.approx(result["atr14_daily_pct"] * .8)
    assert (status, view._preview_bars, view.settings) == before
    assert "không tạo tín hiệu hoặc đặt lệnh" in view._indicator_preview_hint()
    assert "Phiên cuối" in view._atr_preview_hint()
    assert "action" not in result and "signal" not in result


def test_unavailable_symbol_never_borrows_other_tickers_history():
    view = subject()
    result = view._preview_indicator_details({}, "VIX")
    assert result["indicators"].get("buy_ema_fast") is None
    assert "atr14_daily_pct" not in result
    assert "chờ daemon tải lịch sử" in view._indicator_preview_hint()


def test_paper_preview_never_borrows_real_decision_but_can_read_shared_market_bars():
    view = subject()
    result = view._preview_indicator_details({"decisions_by_mode": {"REAL": {"AAA": {
        "signal": "BUY", "details": {"indicators": {"buy_ema_fast": 999}, "atr14_daily_pct": 99},
    }}, "PAPER": {}}}, "AAA")
    assert result["indicators"]["buy_ema_fast"] != 999
    assert result["atr14_daily_pct"] != 99
    assert "signal" not in result


def test_current_book_decision_has_priority_over_read_only_preview():
    view = subject()
    actual = indicator_snapshot(history())
    actual["buy_ema_fast"] = 20
    result = view._preview_indicator_details({"decisions_by_mode": {"PAPER": {"AAA": {
        "details": {"indicators": actual, "atr14_daily_pct": 4.5},
    }}}}, "AAA")
    assert result["indicators"] == actual
    assert result["atr14_daily_pct"] == 4.5
    assert view._preview_indicator_source["source"] == "DECISION"


def test_setting_change_recalculates_preview_instead_of_showing_old_ema_period():
    view = subject()
    view.settings.rule_parameters.update(buy_ema_fast=5, buy_ema_slow=10, rsi_period=20)
    result = view._preview_indicator_details({"decisions_by_mode": {"PAPER": {"AAA": {
        "details": {"indicators": indicator_snapshot(history())},
    }}}}, "AAA")
    assert result["indicators"] == indicator_snapshot(history(), 5, 10, 20, sell_fast=3, sell_slow=6)


@pytest.mark.parametrize("mode", ["CLOSED", "REALTIME"])
def test_atr_excludes_today_and_closes_saved_yesterday_candle(mode):
    view = subject()
    view.settings.signal_mode = mode
    view._preview_bars[-1]["closed"] = False
    view._preview_bars.append({"time": int(market_now().timestamp()), "open": 10,
                               "high": 200, "low": 1, "close": 150, "closed": False})
    result = view._preview_indicator_details({}, "AAA")
    assert result["atr14_daily_pct"] == pytest.approx(average_true_range_pct(history()))
    working = history() if mode == "CLOSED" else view._preview_bars
    assert result["indicators"]["buy_ema_fast"] == pytest.approx(indicator_snapshot(working)["buy_ema_fast"])


@pytest.mark.parametrize("stale,quote_symbol,phase", [(False, "AAA", "OPEN"), (True, "AAA", "OPEN"),
                                                     (False, "VIX", "OPEN"), (False, "AAA", "CLOSED")])
def test_realtime_merge_requires_fresh_quote_of_same_symbol_and_open_session(stale, quote_symbol, phase):
    view = subject()
    view.settings.signal_mode = "REALTIME"
    tick = {"symbol": quote_symbol, "price": 25, "timestamp": time.time(), "stale": stale}
    status = {"ticks": {"AAA": tick}, "symbol_phases": {"AAA": phase}}
    result = view._preview_indicator_details(status, "AAA")
    working = merge_tick_into_daily_bars(history(), tick) if not stale and quote_symbol == "AAA" and phase == "OPEN" else history()
    assert result["indicators"]["buy_ema_fast"] == pytest.approx(indicator_snapshot(working)["buy_ema_fast"])
    assert result["atr14_daily_pct"] == pytest.approx(average_true_range_pct(history()))


def test_insufficient_history_does_not_invent_rsi_or_atr():
    view = subject()
    view._preview_bars = history()[:2]
    result = view._preview_indicator_details({}, "AAA")
    assert result["indicators"]["rsi"] is None
    assert "atr14_daily_pct" not in result


class DeferredExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn):
        result = Future()
        self.calls.append((fn, result))
        return result

    def complete(self):
        fn, result = self.calls[-1]
        result.set_result(fn())


def reader(tmp_path):
    view = DashboardActionsMixin()
    path = tmp_path / "market_bars.json"
    path.write_text(json.dumps({"symbols": {"AAA": history()}}), encoding="utf-8")
    view.bridge = SimpleNamespace(market_cache_path=path)
    view.running = True
    view.selected = "AAA"
    view.symbol = SimpleNamespace(get=lambda: view.selected)
    view._io_executor = DeferredExecutor()
    view.callbacks = []
    view._post_ui = view.callbacks.append
    view._update_order_preview = lambda: None
    return view


def test_history_read_is_async_rate_limited_and_read_only(tmp_path):
    view = reader(tmp_path)
    saved = view.bridge.market_cache_path.read_bytes()
    view._refresh_preview_bars("AAA")
    assert not hasattr(view, "_preview_bars")
    view._refresh_preview_bars("AAA")
    assert len(view._io_executor.calls) == 1
    view._io_executor.complete()
    assert not hasattr(view, "_preview_bars")  # Worker cannot render/mutate Tk.
    view.callbacks.pop()()
    assert view._preview_bars == history()
    view._refresh_preview_bars("AAA")
    assert len(view._io_executor.calls) == 1
    assert view.bridge.market_cache_path.read_bytes() == saved


def test_ticker_switch_during_history_load_discards_previous_result(tmp_path):
    view = reader(tmp_path)
    view._refresh_preview_bars("AAA")
    view.selected = "VIX"
    view._io_executor.complete()
    view.callbacks.pop()()
    assert not hasattr(view, "_preview_bars")
    assert not view._preview_bars_busy
    view._refresh_preview_bars("VIX")
    view._io_executor.complete()
    view.callbacks.pop()()
    assert view._preview_bars_symbol == "VIX" and view._preview_bars == []


def test_missing_or_invalid_history_cache_is_safe_and_retryable(tmp_path):
    view = reader(tmp_path)
    view.bridge.market_cache_path.write_text("incomplete", encoding="utf-8")
    view._refresh_preview_bars("AAA")
    view._io_executor.complete()
    view.callbacks.pop()()
    assert view._preview_bars == [] and not view._preview_bars_busy
