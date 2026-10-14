"""Alternate history fetches prices without mutating trading memory or blocking orders."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from queue import SimpleQueue
from threading import Event
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.ema_history import load_history, view_counts, with_history
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.rules.business import StaticRuleParameters
from viking_v2.rules.ema_memory import refresh_memory
from viking_v2.trading.market import VN_TZ

NOW = datetime(2026, 10, 14, 9, 31, tzinfo=VN_TZ)


def daily():
    return [{"time": int((NOW.replace(hour=0, minute=0) - timedelta(days=70-i)).timestamp()),
             "open": 8, "high": 8, "low": 8, "close": 8, "volume": 100, "closed": True}
            for i in range(70)]


class Prices:
    def __init__(self, *, fail=False):
        self.calls, self.fail = [], fail
        self.points = []
        for day, values in [(9, [("09:15", 7), ("09:16", 9), ("09:17", 7), ("14:44", 8)]),
                            (12, [("09:15", 9), ("14:44", 9)]),
                            (13, [("09:15", 7), ("09:16", 9), ("14:44", 9)])]:
            for clock, value in values:
                self.points.append((int(datetime.fromisoformat(f"2026-10-{day:02d}T{clock}:00").replace(tzinfo=VN_TZ).timestamp()), value))

    def get_ohlc(self, symbol, resolution, left, right):
        self.calls.append((symbol, resolution, left, right))
        if self.fail:
            return None
        assert resolution == "1"
        selected = [(stamp, price) for stamp, price in self.points if left <= stamp <= right]
        return {"s": "ok", "t": [x[0] for x in selected], "v": [100] * len(selected),
                **{key: [x[1] for x in selected] for key in ("o", "h", "l", "c")}}


def params():
    return StaticRuleParameters(buy_ema_fast=2, buy_ema_slow=3, rsi_period=2, buy_ema_cross_sessions=3)


def source():
    return {"tracking_version": 2, "profile": "2/3", "watch_id": "MSN-REAL-WATCH", "initialized": True,
            "started_at": "2026-10-13T09:00:00+07:00", "observed_at": NOW.isoformat(),
            "current_ready": True, "current_fast": 7.5, "current_slow": 8,
            "up_count": 99, "down_count": 98, "daily_count_day": NOW.date().isoformat(),
            "daily_up_count": 0, "daily_down_count": 1}


def test_recent_three_sessions_use_minute_prices_and_cache_without_fetching_today(tmp_path):
    client = Prices()
    cache = tmp_path / "preview-prices.json"
    result = load_history(client, cache, "MSN", daily(), params(), "REALTIME", NOW, "HOSE", [])
    assert result["ready"] and result["up"] == 3 and result["down"] == 2
    assert result["start"] == "2026-10-09" and result["cross"]["cross_at"].startswith("2026-10-13")
    assert client.calls and all(row[3] < NOW.replace(hour=0, minute=0).timestamp() for row in client.calls)
    requests = len(client.calls)
    repeated = load_history(client, cache, "MSN", daily(), params(), "REALTIME", NOW, "HOSE", [])
    assert repeated == result and len(client.calls) == requests
    original = source()
    before = deepcopy(original)
    evidence = with_history(original, result)
    counts = view_counts(evidence, now=NOW, prior_sessions=3, holidays=[])
    assert (counts["up"], counts["down"]) == (3, 3)  # 3 previous up + 2 previous down + 1 live down today.
    assert original == before


def test_holiday_and_weekend_are_skipped_and_failure_is_not_reported_as_zero(tmp_path):
    client = Prices()
    result = load_history(client, tmp_path / "holidays.json", "MSN", daily(), params(), "REALTIME", NOW, "HOSE", ["2026-10-12"])
    days = {datetime.fromtimestamp(row[2], VN_TZ).date().isoformat() for row in client.calls}
    assert result["start"] == "2026-10-08" and days == {"2026-10-08", "2026-10-09", "2026-10-13"}
    failure = load_history(Prices(fail=True), tmp_path / "failure.json", "MSN", daily(), params(), "REALTIME", NOW, "HOSE", [])
    assert not failure["ready"] and "up" not in failure and failure["issue"] == "MINUTE_HISTORY"


def test_new_watch_today_recovers_pre_admission_cross_without_counting_live_segment_twice(tmp_path):
    client = Prices()
    client.points.extend((int(NOW.replace(hour=9, minute=minute).timestamp()), value)
                         for minute, value in [(15, 7), (16, 9), (45, 7)])
    baseline = {"at": NOW.isoformat(), "buy_ema_fast": 9, "buy_ema_slow": 8}
    result = load_history(client, tmp_path / "admission.json", "MSN", daily(), params(), "REALTIME", NOW, "HOSE", [], baseline)
    assert result["ready"] and (result["up"], result["down"]) == (4, 3)
    assert result["cross"]["cross_at"].startswith("2026-10-14T09:17")
    assert all(row[3] < NOW.timestamp() for row in client.calls)
    raw = {**source(), "started_at": NOW.isoformat(), "daily_down_count": 0, "daily_up_count": 0}
    counts = view_counts(with_history(raw, result), now=NOW, prior_sessions=3, holidays=[])
    assert (counts["up"], counts["down"]) == (4, 3)


def test_closed_mode_uses_daily_closes_without_minute_api(tmp_path):
    client = Prices()
    result = load_history(client, tmp_path / "closed.json", "MSN", daily(), params(), "CLOSED", NOW, "HOSE", [])
    assert result["ready"] and not client.calls and not (tmp_path / "closed.json").exists()


def test_preview_preserves_live_consumption_and_never_overwrites_va_source():
    original = {**source(), "cross_id": "LIVE-ALREADY-USED", "cross_source": "LIVE",
                "cross_at": "2026-10-13T09:17:00+07:00", "used": True, "current_fast": 9}
    before = deepcopy(original)
    scan = {"ready": True, "start": "2026-10-09", "up": 3, "down": 2,
            "cross": {"cross_id": "PREVIEW", "cross_at": original["cross_at"], "used": False}}
    viewed = refresh_memory(with_history(original, scan), now=NOW, mode="SESSIONS", sessions=3, exchange="HOSE")
    assert viewed["cross_id"] == original["cross_id"] and viewed["used"] and viewed["state"] == "SESSION_USED"
    assert original == before


@pytest.mark.parametrize("cross_source", ["HISTORY", "VA_HISTORY"])
def test_rebuilt_history_keeps_a_consumed_historical_cross_consumed(cross_source):
    original = {**source(), "cross_id": "CONSUMED-HISTORY", "cross_source": cross_source,
                "cross_at": "2026-10-13T09:17:00+07:00", "used": True, "current_fast": 9}
    before = deepcopy(original)
    scan = {"ready": True, "start": "2026-10-09", "up": 3, "down": 2,
            "cross": {"cross_id": "PREVIEW", "cross_at": original["cross_at"], "used": False}}
    viewed = refresh_memory(with_history(original, scan), now=NOW, mode="SESSIONS", sessions=3, exchange="HOSE")
    assert viewed["used"] and viewed["state"] == "SESSION_USED" and original == before


def test_alternate_view_does_not_seed_the_active_va_rule(ui_root, monkeypatch):
    import customtkinter as ctk
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: NOW)
    top = ctk.CTkToplevel(ui_root)
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view._build_order_preview_tab(top)
    raw = {**source(), "profile": "3/6", "current_fast": 9, "current_slow": 8, "daily_down_count": 0}
    before = deepcopy((raw, view.settings.to_dict()))
    view._alternate_ema_history = lambda _: {"ready": True, "start": "2026-10-09", "up": 3, "down": 2,
        "cross": {"cross_id": "PREVIEW", "cross_at": "2026-10-13T09:17:00+07:00", "used": False}}
    try:
        view._render_ema_cross_preview({"ema_cross": raw})
        assert not view._preview_ema_gate_evidence["valid"]
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "5 CẮT - 3P"
        assert view._preview_ema_gate_evidence["valid"]
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        assert not view._preview_ema_gate_evidence["valid"]
        assert (raw, view.settings.to_dict()) == before
    finally:
        top.destroy()


def test_failed_history_preview_cannot_present_a_live_zero_as_verified_three_session_zero(ui_root, monkeypatch):
    import customtkinter as ctk
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: NOW)
    top = ctk.CTkToplevel(ui_root)
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view._build_order_preview_tab(top)
    raw = {**source(), "started_at": "2026-10-01T09:00:00+07:00", "current_fast": 7.5,
           "up_count": 0, "down_count": 0, "daily_up_count": 0, "daily_down_count": 0}
    view._alternate_ema_history = lambda _: {"ready": False, "issue": "MINUTE_HISTORY"}
    try:
        view._render_ema_cross_preview({"ema_cross": raw})
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "— CẮT - 3P"
        assert "thử lại sau 60 giây" in view._ema_memory_preview_hint()
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
    finally:
        top.destroy()


def test_slow_preview_api_does_not_block_order_worker_or_apply_to_another_book(tmp_path, monkeypatch):
    entered, release, done = Event(), Event(), Event()
    clients = []
    class SlowClient(Prices):
        def __init__(self, **credentials):
            super().__init__()
            self.credentials, self.trading_token, self.closed = credentials, "UNEXPECTED_TOKEN", False
            clients.append(self)
        def get_ohlc(self, *args):
            assert self.trading_token == ""
            entered.set()
            assert release.wait(5)
            return super().get_ohlc(*args)
        def close(self):
            self.closed = True
    monkeypatch.setattr("viking_v2.dashboard.actions.DNSEClient", SlowClient)
    monkeypatch.setattr("viking_v2.trading.market.market_now", lambda: NOW)
    view = DashboardActionsMixin()
    view.running, view.settings = True, AppSettings()
    view.settings.rule_parameters.update(buy_ema_fast=2, buy_ema_slow=3, rsi_period=2)
    symbol, book = ["MSN"], ["REAL"]
    view.symbol, view.mode = SimpleNamespace(get=lambda: symbol[0]), SimpleNamespace(get=lambda: book[0])
    view._symbol_exchange = lambda _: "HOSE"
    view.real = SimpleNamespace(configured=lambda: True, api_key="TEST", api_secret="TEST", account_no="TEST", base_url="")
    view.bridge = SimpleNamespace(root=tmp_path, market_cache_path=tmp_path / "absent.json")
    view._preview_bars_symbol, view._preview_bars = "MSN", daily()
    callbacks, draws = SimpleQueue(), []
    view._post_ui = lambda callback: (callbacks.put(callback), done.set())
    view._update_order_preview = lambda: draws.append(True)
    raw = source()
    before = deepcopy((raw, view.settings.to_dict()))
    with ThreadPoolExecutor(max_workers=1) as orders:
        try:
            assert view._alternate_ema_history(raw)["pending"]
            assert entered.wait(2)
            assert orders.submit(lambda: "ORDERS_STILL_RESPOND").result(timeout=1) == "ORDERS_STILL_RESPOND"
            assert view._alternate_ema_history(raw)["pending"] and len(clients) == 1
            book[0] = "PAPER"
            release.set()
            assert done.wait(4)
            callbacks.get()()
            assert draws == [] and clients[0].closed
            book[0] = "REAL"
            assert view._alternate_ema_history(raw)["ready"] and len(clients) == 1
            assert (raw, view.settings.to_dict()) == before
            assert {path.name for path in tmp_path.iterdir()} == {"ema_preview_prices.json"}
        finally:
            release.set()
            view.running = False
            view._stop_ema_history_preview()
