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
from viking_v2.dashboard.panels import DashboardPanelsMixin, _rsi_comparison_preview
from viking_v2.dashboard.view import COL_GREEN, COL_PREVIEW_TEXT, COL_RED, COL_TEXT
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


@pytest.mark.parametrize("current,previous,text,color", [
    (58.5756, 56.7921, "56.79 → 58.58 ↑", COL_GREEN),
    (56.79212294979958, 56.79212294979958, "56.79 → 56.79 =", COL_TEXT),
    (52.2897, 56.7921, "56.79 → 52.29 ↓", COL_RED),
    (56.794, 56.791, "56.791 → 56.794 ↑", COL_GREEN),
    (56.79209, 56.79212, "56.79212 → 56.79209 ↓", COL_RED),
    (None, None, "-- → --", COL_PREVIEW_TEXT),
    (-1, 50, "50.00 → --", COL_PREVIEW_TEXT),
    (100, 0, "0.00 → 100.00 ↑", COL_GREEN),
])
def test_rsi_comparison_preview_is_precise_and_validated(current, previous, text, color):
    assert _rsi_comparison_preview(current, previous) == (text, color)


@pytest.mark.parametrize("mode,expected", [
    ("REALTIME", "phiên trước đã đóng → phiên hiện tại theo giá realtime"),
    ("CLOSED", "hai phiên đã đóng liên tiếp"),
])
def test_indicator_hint_explains_daily_rsi_baseline_and_strict_comparison(mode, expected):
    view = subject()
    view.settings.signal_mode = mode
    hint = view._indicator_preview_hint()
    assert expected in hint
    assert "không so với tick trước" in hint
    assert "bằng không đạt" in hint


@pytest.mark.parametrize('ready', [True, False])
def test_minute_preview_identifies_completed_sample_or_initialization(ready):
    view = subject()
    values = indicator_snapshot(view._preview_bars)
    sample_time = '2026-10-09T14:00:00+07:00'
    values.update(signal_ready=ready, indicator_observed_at=sample_time if ready else '')
    view._preview_indicator_details({'decisions_by_mode': {'PAPER': {'AAA': {
        'details': {'indicators': values, 'updated_at': ''}}}}}, 'AAA')
    assert view._preview_indicator_source['signal_ready'] is ready
    if ready:
        assert view._preview_indicator_source['asof'] == sample_time
    else:
        assert 'chỉ số nền 1D chưa dùng để BUY/E' in view._indicator_preview_hint()


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


@pytest.mark.parametrize("mode", ["PAPER", "REAL"])
def test_stale_daemon_decision_does_not_freeze_indicator_preview(mode):
    view = subject(mode)
    old = indicator_snapshot(history())
    old["buy_ema_fast"] = 999
    status = {"decisions_by_mode": {mode: {"AAA": {
        "symbol": "AAA", "details": {"execution_mode": mode, "updated_at": time.time() - 120,
                                    "indicators": old, "atr14_daily_pct": 99},
    }}}}
    result = view._preview_indicator_details(status, "AAA")
    assert result["indicators"]["buy_ema_fast"] == pytest.approx(indicator_snapshot(history())["buy_ema_fast"])
    assert result["atr14_daily_pct"] == pytest.approx(average_true_range_pct(history()))
    assert view._preview_indicator_source["source"] == "DAILY_PREVIEW"


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


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("phase", ["CLOSED", "WEEKEND"])
def test_closed_preview_recovers_latest_book_observation_after_restart_without_writes(tmp_path, monkeypatch, mode, phase):
    from viking_v2.rules.state import RuleStateStore
    now = market_now().replace(hour=17, minute=29)
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: now)
    view = subject(mode)
    state_path = tmp_path / "rules.json"
    state = RuleStateStore(state_path)
    for book, fast, slow in (("REAL", 74.0, 73.0), ("PAPER", 72.0, 73.0)):
        state.save_session_cross("AAA", book, {"profile": "3/6", "current_ready": True,
            "current_fast": fast, "current_slow": slow, "valid": False, "state": "WAIT_DOWN",
            "observed_at_epoch": now.replace(hour=14, minute=44).timestamp(),
            "expires_at": now.replace(hour=14, minute=45).timestamp()})
    view.rule_state = RuleStateStore(state_path)
    before = deepcopy(view.rule_state.store.read())
    monkeypatch.setattr(view.rule_state.store, "write", lambda *_args: pytest.fail("Preview must not write rule state"))
    result = view._preview_indicator_details({"symbol_phases": {"AAA": phase}}, "AAA")
    saved = result["ema_cross_last_preview"]
    assert saved["fast"] == (74 if mode == "REAL" else 72)
    assert saved["slow"] == 73 and saved["label"] == "CUỐI 14:44"
    assert result["ema_cross_preview_expired"]
    assert "ema_cross" not in result and "action" not in result and "signal" not in result
    assert view.rule_state.store.read() == before


def test_closed_preview_uses_completed_daily_cache_when_saved_observation_is_wrong_profile(monkeypatch):
    now = market_now().replace(hour=17, minute=29)
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: now)
    view = subject()
    view.rule_state = SimpleNamespace(session_cross=lambda *_args: {
        "profile": "5/10", "current_ready": True, "current_fast": 999, "current_slow": 998,
        "observed_at_epoch": now.replace(hour=14, minute=44).timestamp(),
        "expires_at": now.replace(hour=14, minute=45).timestamp()})
    view._preview_bars.append({"time": now.timestamp(), "close": 10000, "closed": False})
    before = deepcopy(view._preview_bars)
    result = view._preview_indicator_details({"market_status": "CLOSED"}, "AAA")
    saved = result["ema_cross_last_preview"]
    expected = indicator_snapshot(history())
    assert saved["source"] == "DAILY_CLOSE"
    assert saved["fast"] == expected["buy_ema_fast"] and saved["slow"] == expected["buy_ema_slow"]
    assert saved["label"].startswith("ĐÓNG ")
    assert "ema_cross" not in result and view._preview_bars == before


def test_live_preview_does_not_import_expired_observation_as_cross_evidence():
    view = subject()
    view.rule_state = SimpleNamespace(session_cross=lambda *_args: pytest.fail("Live preview must use live evidence"))
    result = view._preview_indicator_details({"symbol_phases": {"AAA": "OPEN"}}, "AAA")
    assert "ema_cross_last_preview" not in result and "ema_cross" not in result


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
