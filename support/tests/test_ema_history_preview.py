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
from viking_v2.dashboard.ema_history import latest_cross, load_history, view_counts, with_history
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.rules.business import StaticRuleParameters
from viking_v2.rules.ema_memory import refresh_memory, seed_lookback
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
    assert result["last_down_at"] == "2026-10-13T09:16:00+07:00"  # Minute-close time.
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


def test_seven_prior_sessions_can_contain_only_a_down_cross(ui_root, monkeypatch, tmp_path):
    import customtkinter as ctk
    from viking_v2.dashboard import panels

    monkeypatch.setattr(panels, "market_now", lambda: NOW)
    history_params = params()
    history_params.buy_ema_cross_sessions = 7
    bars = daily()
    # Start above before the seven-session window; every subsequent quote is below.
    for bar in bars:
        if datetime.fromtimestamp(bar["time"], VN_TZ).date().isoformat() == "2026-10-04":
            bar.update(open=9, high=9, low=9, close=9)
    client = Prices()
    client.points = [(int(NOW.replace(day=day, hour=9, minute=15).timestamp()), 7)
                     for day in (5, 6, 7, 8, 9, 12, 13)]
    scan = load_history(client, tmp_path / "seven.json", "MSN", bars, history_params,
                        "REALTIME", NOW, "HOSE", [])
    assert scan["ready"] and scan["start"] == "2026-10-05"
    assert (scan["up"], scan["down"], scan["cross"]) == (0, 1, {})
    requested_days = {datetime.fromtimestamp(row[2], VN_TZ).day for row in client.calls}
    assert requested_days == {5, 6, 7, 8, 9, 12, 13}

    hints = []
    real_hint = panels._HoverHint
    def capture_hint(*args, **kwargs):
        hint = real_hint(*args, **kwargs)
        hints.append(hint)
        return hint
    monkeypatch.setattr(panels, "_HoverHint", capture_hint)
    top = ctk.CTkToplevel(ui_root)
    top.geometry("1000x400+0+0")
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view.settings.rule_parameters.update(buy_ema_fast=2, buy_ema_slow=3,
                                         rsi_period=2, buy_ema_cross_sessions=7)
    view._build_order_preview_tab(top)
    raw = {**source(), "daily_down_count": 0}
    before = deepcopy((raw, view.settings.to_dict()))
    requested_windows = []
    def loader(_raw):
        requested_windows.append(view.settings.rule_parameters["buy_ema_cross_sessions"])
        return scan
    view._alternate_ema_history = loader
    try:
        top.update()
        view._render_ema_cross_preview({"ema_cross": raw})
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        view.preview_ema_swap.invoke()
        top.update_idletasks()
        assert requested_windows == [7]
        assert view.preview_rule_cross_title.cget("text") == "⏳ 7P"
        assert view.preview_rule_cross_counts.cget("text") == "1 CẮT - 7P"
        assert view._preview_ema_gate_evidence["state"] == "WAIT_UP"
        assert not view._preview_ema_gate_evidence["valid"]
        assert view.preview_rule_cross_detail._viking_compact_detail_text == "XUỐNG 05/10 09:16"
        assert "0 lên + 1 xuống" in view._ema_cross_hint()
        assert int(view.preview_ema_entry_gate.master.grid_info()["column"]) == 2
        phase = view.preview_rule_cross.master
        p2_labels = {phase._viking_title_widget, view.preview_ema_entry_gate,
                     view.preview_rule_cross_title, view.preview_rule_cross_counts,
                     view.preview_rule_cross, view.preview_rule_cross_detail,
                     view.preview_rule_buy_ema_key, view.preview_rule_sell_ema_key,
                     view.preview_rule_rsi_key, view.preview_rule_ema,
                     view.preview_rule_sell_ema, view.preview_rule_rsi}
        assert not p2_labels.intersection(hint.widget for hint in hints)
        assert {phase._viking_hint_icon, view.preview_ema_hint}.issubset(hint.widget for hint in hints)
        view.preview_ema_hint.invoke()
        popup = next(hint.popup for hint in hints if hint.widget is view.preview_ema_hint)
        assert popup is not None and "0 lên + 1 xuống" in popup.cget("text")
        assert (raw, view.settings.to_dict()) == before
    finally:
        for hint in hints:
            hint._hide()
        top.destroy()


def test_latest_cross_preserves_consumption_and_respects_the_selected_history_view():
    raw = {**source(), "mode": "VA", "cross_source": "LIVE", "cross_id": "USED-9",
           "cross_at": "2026-10-09T09:31:00+07:00", "last_down_at": "2026-10-09T09:30:00+07:00",
           "used": True, "ema_history_preview": {"ready": True,
                "cross": {"cross_at": "2026-10-13T09:16:00+07:00"},
                "last_down_at": "2026-10-13T10:43:00+07:00"}}
    before = deepcopy(raw)
    assert latest_cross(raw, now=NOW)["at"] == raw["cross_at"]
    viewed = {**raw, "mode": "SESSIONS"}
    assert latest_cross(viewed, now=NOW)["at"] == "2026-10-13T10:43:00+07:00"
    assert latest_cross(viewed, now=NOW)["direction"] == "DOWN"
    assert raw == before and viewed["cross_id"] == raw["cross_id"] and viewed["used"]
    assert latest_cross({"cross_at": "bad", "last_down_at": "2026-10-15T09:00:00+07:00"}, now=NOW) == {}


def test_seeded_down_timestamp_is_display_only_and_keeps_a_consumed_live_up():
    raw = {**source(), "mode": "VA", "cross_source": "LIVE", "cross_id": "USED-LIVE",
           "cross_at": "2026-10-09T09:31:00+07:00", "used": True}
    before = deepcopy(raw)
    down_at = NOW.replace(day=13, hour=10, minute=43)
    seeded = seed_lookback(raw, [(NOW.replace(day=12), {"buy_ema_fast": 9, "buy_ema_slow": 8}),
                                (down_at, {"buy_ema_fast": 7, "buy_ema_slow": 8})],
                           start=NOW.replace(day=9), now=NOW, pair="2/3", mode="SESSIONS",
                           sessions=3, exchange="HOSE")
    assert seeded["lookback_down_count"] == 1 and seeded["lookback_last_down_at"] == down_at.isoformat()
    assert seeded["cross_id"] == raw["cross_id"] and seeded["used"] and raw == before
    assert latest_cross(seeded, now=NOW)["direction"] == "UP"  # VA ignores the pre-admission scan.
    latest = latest_cross({**seeded, "mode": "SESSIONS"}, now=NOW)
    assert latest["direction"] == "DOWN" and latest["at"] == down_at.isoformat()


def test_three_session_expiry_can_remember_the_previous_fridays_up_cross(ui_root, monkeypatch):
    import customtkinter as ctk
    monkeypatch.setattr("viking_v2.dashboard.panels.market_now", lambda: NOW)
    top = ctk.CTkToplevel(ui_root)
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view.settings.rule_parameters.update(buy_ema_cross_mode="SESSIONS", buy_ema_cross_sessions=3)
    view._build_order_preview_tab(top)
    raw = {**source(), "profile": "3/6", "started_at": "2026-10-08T09:00:00+07:00",
           "current_fast": 23, "current_slow": 22, "daily_down_count": 0,
           "count_days": {"2026-10-09": {"up": 3, "down": 3}},
           "cross_source": "LIVE", "cross_id": "HDB-9", "cross_at": "2026-10-09T09:31:00+07:00",
           "last_down_at": "2026-10-09T09:30:00+07:00", "used": False,
           "lookback_ready": True, "lookback_started_at": "2026-10-09T00:00:00+07:00"}
    before = deepcopy(raw)
    try:
        view._render_ema_cross_preview({"ema_cross": raw})
        assert view.preview_rule_cross_counts.cget("text") == "6 CẮT - 3P"
        assert view._preview_ema_gate_evidence["state"] == "EXPIRED"
        assert view._preview_ema_gate_evidence["expiry_sessions"] == ["2026-10-09", "2026-10-12", "2026-10-13"]
        assert view.preview_rule_cross_detail.cget("text") == "LÊN 09/10 09:31"
        assert "hạn 13/10 14:45, gồm phiên cắt" in view._ema_cross_hint()
        assert "Lần cắt lên hết hạn" in view._ema_cross_hint()
        assert raw == before and not view._preview_ema_gate_evidence["valid"]
    finally:
        top.destroy()


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
        assert view.preview_rule_cross_detail.cget("text") == "THEO DÕI 13/10 09:00"
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "5 CẮT - 3P"
        assert view._preview_ema_gate_evidence["valid"]
        assert view.preview_rule_cross_detail.cget("text") == "LÊN 13/10 09:17"
        assert "Theo dõi từ 13/10 09:00" in view._ema_cross_hint()
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        assert not view._preview_ema_gate_evidence["valid"]
        view._alternate_ema_history = lambda _: {"ready": True, "start": "2026-10-09", "end": "2026-10-13",
                                               "up": 0, "down": 0, "cross": {}}
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 3P"
        assert view.preview_rule_cross_detail.cget("text") == "QUÉT 09–13/10"
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
        assert view.preview_rule_cross_detail.cget("text") == "CHƯA TẢI ĐƯỢC"
        assert "thử lại sau 60 giây" in view._ema_memory_preview_hint()
        view.preview_ema_swap.invoke()
        assert view.preview_rule_cross_counts.cget("text") == "0 CẮT - 0P"
        assert view.preview_rule_cross_detail.cget("text") == "THEO DÕI 01/10 09:00"
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
