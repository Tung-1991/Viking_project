from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.panels import (
    DashboardPanelsMixin,
    _dynamic_atr_preview_text,
    _preview_panel_height,
)
from viking_v2.dashboard.view import COL_GRAY, COL_GREEN, COL_TEXT, COL_TITLE, COL_WARN
from viking_v2.dashboard.tables import (
    DashboardTablesMixin,
    RUNNING_COLUMNS,
    RUNNING_HEADERS,
    RUNNING_WIDTHS,
    VISIBLE_LOG_LINE_LIMIT,
)
from viking_v2.models import OrderIntent, TradeCycle
from viking_v2.dashboard.windows import (
    FONT_KEY,
    FONT_MONO_VALUE,
    FONT_TABLE_HEADING,
    FONT_TABLE_VALUE,
    FONT_VALUE,
    HINT_FONT,
    PALETTE,
    fit_entry_text,
)


class _Value:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


def test_dynamic_atr_preview_uses_the_selected_symbols_completed_daily_atr() -> None:
    params = {
        "normal_dynamic_enabled": True,
        "normal_atr_activation_enabled": True,
        "normal_atr_trail_enabled": True,
        "normal_atr_activation_multiplier": 0.6,
        "normal_atr_multiplier": 0.8,
        "normal_sell_pct": 100,
    }
    text = _dynamic_atr_preview_text({"normal_atr_pct": 4.118}, params)
    assert text == "DYN · ATR 4.12% · BÁN 100%"
    params["normal_atr_activation_enabled"] = False
    params["normal_atr_trail_enabled"] = False
    assert _dynamic_atr_preview_text({"normal_atr_pct": 4.118}, params) == text


def test_preview_height_reverses_customtkinter_dpi_scaling() -> None:
    assert _preview_panel_height(600, 2.0) == 300
    assert _preview_panel_height(720, 2.0) == 356
    assert _preview_panel_height(290, 1.0) == 300


def test_operator_typography_separates_keys_from_values() -> None:
    assert FONT_KEY == ("Segoe UI", 14, "bold", "italic")
    assert FONT_VALUE == ("Segoe UI", 14)
    assert FONT_TABLE_HEADING == ("Segoe UI", 16, "bold", "italic")
    assert FONT_TABLE_VALUE == ("Segoe UI", 14)
    assert HINT_FONT == ("Segoe UI", 20)
    title_rgb = tuple(int(COL_TITLE[index:index + 2], 16) for index in (1, 3, 5))
    text_rgb = tuple(int(COL_TEXT[index:index + 2], 16) for index in (1, 3, 5))
    brightness_ratio = sum(title_rgb) / sum(text_rgb)
    assert PALETTE["TITLE"] == COL_TITLE
    assert 0.75 <= brightness_ratio <= 0.85


def test_dynamic_entry_values_shrink_only_when_they_need_more_room(ui_root) -> None:
    import customtkinter as ctk

    entry = ctk.CTkEntry(ui_root, width=115, font=FONT_MONO_VALUE)
    entry.grid(row=0, column=0)
    ui_root.update_idletasks()
    try:
        assert fit_entry_text(entry, "100 CP") == 14
        long_size = fit_entry_text(entry, "999,999,999,999,999 CP")
        assert 11 <= long_size < 14
    finally:
        entry.destroy()


class _Label:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **options: object) -> None:
        self.options.update(options)


class _Entry(_Label):
    def __init__(self, value: str = "") -> None:
        super().__init__()
        self.value = value

    def get(self) -> str:
        return self.value


class _EntryPauseState:
    def __init__(self, active: bool) -> None:
        self.active = active

    def entry_pause(self, _mode: str) -> dict[str, object]:
        return {"active": self.active}


@pytest.mark.parametrize("mode,budget", [("REAL", 3_000_000), ("PAPER", 8_000_000)])
def test_preview_selects_book_and_rejects_other_book(mode, budget):
    subject = DashboardPanelsMixin()
    subject.mode = _Value(mode)
    subject.settings = SimpleNamespace(paper_mode=mode == "PAPER")
    decision = lambda value, book: {"symbol": "FPT", "details": {"execution_mode": book,
        "updated_at": time.time(), "entry_checks": {"order_budget": value, "available_cash": 100_000_000}}}
    status = {"decisions": {"FPT": decision(3_000_000, "REAL")}, "decisions_by_mode": {
        "REAL": {"FPT": decision(3_000_000, "REAL")}, "PAPER": {"FPT": decision(8_000_000, "PAPER")}}}
    assert subject._suggested_order_quantity(10, status, "FPT")[1] == budget
    status["decisions_by_mode"][mode] = {}
    assert subject._suggested_order_quantity(10, status, "FPT")[0] == 0
    status["decisions_by_mode"][mode] = {"FPT": decision(10_000_000, "PAPER" if mode == "REAL" else "REAL")}
    assert subject._suggested_order_quantity(10, status, "FPT")[0] == 0


def test_stale_preview_budget_and_explicit_zero_never_fall_back():
    subject = DashboardPanelsMixin()
    subject.mode = _Value("PAPER")
    subject.settings = SimpleNamespace(paper_mode=True)
    details = {"updated_at": time.time() - 60, "execution_mode": "PAPER", "entry_checks": {
        "order_budget": 10_000_000, "available_cash": 100_000_000}}
    status = {"decisions_by_mode": {"PAPER": {"FPT": {"details": details}}}}
    assert subject._suggested_order_quantity(10, status, "FPT")[0] == 0
    details["updated_at"] = time.time()
    details["entry_checks"].update(order_budget=0, available_capital=10_000_000)
    assert subject._suggested_order_quantity(10, status, "FPT")[0] == 0


@pytest.mark.parametrize("quantity,missing", [(0, True), (100, False)])
def test_main_preview_unknown_amount_is_dash_not_zero(quantity, missing):
    subject = DashboardActionsMixin()
    for key in ("lbl_order_value", "lbl_quote_symbol", "lbl_fee_preview", "lbl_tp_title", "lbl_sl_title", "lbl_tp_preview", "lbl_sl_preview"):
        setattr(subject, key, _Label())
    subject._refresh_main_quote_display = lambda: None
    subject._refresh_full_order_preview = lambda *_args: None
    subject.symbol, subject.mode, subject.order_type = _Value("DGC"), _Value("PAPER"), _Value("MARKET")
    subject.quantity, subject.tp, subject.sl = _Value(str(quantity) if quantity else ""), _Value("7%"), _Value("-3.5%")
    subject._current_tick_price = 32.15
    subject._suggested_order_quantity = lambda *_: (quantity, 0, False)
    subject._preview_buy_fee = lambda value, *_: value * 0.00045
    subject._preview_trigger = lambda key, raw: raw
    subject._update_order_preview()
    for key in ("lbl_fee_preview", "lbl_tp_preview", "lbl_sl_preview"):
        assert (getattr(subject, key).options["text"] == "—") is missing


@pytest.mark.parametrize("scaling", [1.0, 1.25])
def test_compact_rules_priority_draft_and_read_only_previews(ui_root, monkeypatch, scaling):
    import os
    from pathlib import Path
    import customtkinter as ctk
    from viking_v2.config import AppSettings
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections import window as connection_window
    from viking_v2.rules import window as rule_window
    from viking_v2.trading.state import TradeStateStore

    settings = AppSettings.from_dict({"watchlist": ["MSN", "CTS", "HDB", "IDC"],
        "priority_symbols": ["MSN", "CTS", "HDB", "IDC"], "priority_capital_enabled": True,
        "priority_total_capital": 50_000_000, "priority_allocations": {
            value: {"limit_vnd": (5 if value == "HDB" else 15) * 1_000_000, "use_pct": 50}
            for value in ["MSN", "CTS", "HDB", "IDC"]}})
    monkeypatch.setattr(rule_window, "save_settings", lambda *_: None)
    monkeypatch.setattr(connection_window, "save_settings", lambda *_: None)
    client = DNSEClient(account_no="OFFLINE_UI")
    ctk.set_appearance_mode("dark")
    ctk.set_widget_scaling(scaling)
    rule = connection = None
    captures = os.getenv("VIKING_CAPTURE_UI") == "1"

    def capture(top, name):
        ui_root.update_idletasks()
        ui_root.update()
        if captures:
            import ctypes
            import tkinter as tk
            from PIL import ImageGrab
            settled = tk.BooleanVar(master=ui_root, value=False)
            ui_root.after(180, lambda: settled.set(True))
            ui_root.wait_variable(settled)
            directory = Path(__file__).resolve().parents[2] / ".artifacts" / "ui-review"
            directory.mkdir(parents=True, exist_ok=True)
            hwnd = ctypes.windll.user32.GetParent(top.winfo_id())
            ImageGrab.grab(window=hwnd).save(directory / f"{name}-{scaling}.png")

    try:
        rule = rule_window.RuleSettingsPopup(ui_root, settings, "OFFLINE_UI", lambda: None,
            trade_state=TradeStateStore(Path(connection_window.config.RUNTIME_ROOT) / f"layout-{scaling}.json"))
        rule.top.geometry("1080x720+0+0")
        for name in rule.phase_tabs._tab_dict:
            rule.phase_tabs.set(name)
            capture(rule.top, name.split(" · ")[0].replace(" ", "-"))
            body = rule.ma_period.master.master.master if name.startswith("PHASE 1") else (
                rule.max_positions.master.master.master if name.startswith("PHASE 3") else rule.phase2_signal_card.master)
            cards = [child for child in body.winfo_children() if isinstance(child, ctk.CTkFrame) and int(child.grid_info().get("row", -1)) == 1]
            assert max(card.winfo_width() for card in cards) - min(card.winfo_width() for card in cards) <= 2
            assert max(card.winfo_height() for card in cards) - min(card.winfo_height() for card in cards) <= 2
        rule.tabs.set("E/M")
        capture(rule.top, "exit")
        protection_groups = (rule.exit_card, rule.protect_trail_card, rule.dynamic_settings_card)
        assert all(card.master.master is rule.protect_card for card in protection_groups)
        assert max(card.winfo_height() for card in protection_groups) - min(card.winfo_height() for card in protection_groups) <= 2
        assert max(card.winfo_width() for card in protection_groups) - min(card.winfo_width() for card in protection_groups) <= 2
        rule.normal_repeat.set(True)
        assert rule.repeat_switch.cget("state") == "disabled"
        assert "REPEAT OFF" in rule.execution_preview["NORMAL"].cget("text")
        rule.normal_sell.delete(0, "end")
        rule.normal_sell.insert(0, "50")
        rule._refresh_execution_preview()
        assert "CHƯA LƯU" in rule.execution_preview["NORMAL"].cget("text")
        assert "bán 50%" in rule.execution_preview["NORMAL"].cget("text")
        assert rule.repeat_switch.cget("state") == "normal"
        assert "REPEAT ON" in rule.execution_preview["NORMAL"].cget("text")
        assert settings.rule_parameters["normal_sell_pct"] == 100
        rule.normal_dynamic.set(True)
        assert "ATR ×" in rule.execution_preview["NORMAL"].cget("text")
        rule.tabs.set("THỰC THI")
        capture(rule.top, "execution")
        rule.save()
        assert "CHƯA LƯU" not in rule.execution_preview["NORMAL"].cget("text")
        assert settings.rule_parameters["normal_sell_pct"] == 50
        rule.loss_block.set(True)
        assert rule.loss_lock_hours.cget("state") == "disabled"
        rule._close()
        rule = None

        connection = connection_window.ConnectionPopup(ui_root, settings, "OFFLINE_UI", client, lambda: None)
        connection.top.geometry("1080x720+0+0")
        assert connection.dnse_key.cget("show") == "•"
        connection.tabs.set("DNSE")
        capture(connection.top, "connection")
        connection.tabs.set("TELEGRAM")
        capture(connection.top, "telegram")
        assert len({button.winfo_x() for button in connection.tele_event_hint_buttons.values()}) == 1
        connection.daily_stats_choice.set("CỘNG DỒN")
        connection._refresh_stats_time_state()
        assert connection.daily_stats_time.cget("state") == "disabled"
        connection._save_daily_stats_settings()
        assert settings.daily_stats_mode == "SINCE_RESET"
        connection.tabs.set("MÃ CK")
        capture(connection.top, "priority")
        connection.priority_total.delete(0, "end")
        connection.priority_total.insert(0, "60")
        connection._divide_priority_capital()
        assert connection._priority_allocations["HDB"]["limit_vnd"] == 15_000_000
        assert connection._priority_allocations["HDB"]["use_pct"] == 50
        assert settings.priority_allocations["HDB"]["limit_vnd"] == 5_000_000
        connection._save_priority()
        assert settings.priority_allocations["HDB"]["limit_vnd"] == 15_000_000
    finally:
        if rule and rule.top.winfo_exists():
            rule._close()
        if connection and connection.top.winfo_exists():
            connection._close()
        client.close()
        ctk.set_widget_scaling(1.0)


def test_bot_button_has_clear_on_pause_and_off_states() -> None:
    subject = DashboardActionsMixin()
    subject.bot_button = _Label()
    subject.mode = _Value("PAPER")
    subject._bot_enabled = False
    subject._bot_toggle_busy = False
    subject._bot_sync_until = 0.0

    subject.rule_state = _EntryPauseState(False)
    subject._paint_bot(True, force=True)
    assert subject.bot_button.options["text"] == "BOT · ON"
    assert subject.bot_button.options["fg_color"] == COL_GREEN

    subject.rule_state = _EntryPauseState(True)
    subject._paint_bot(True, force=True)
    assert subject.bot_button.options["text"] == "BOT · PAUSE"
    assert subject.bot_button.options["fg_color"] == COL_WARN

    subject._paint_bot(False, force=True)
    assert subject.bot_button.options["text"] == "BOT · OFF"
    assert subject.bot_button.options["fg_color"] == COL_GRAY


def test_auto_quantity_placeholder_is_compact_and_unambiguous() -> None:
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("AAA")
    subject.quantity = _Entry()

    quantity, _budget, _forced = subject._suggested_order_quantity(
        7.29,
        {
            "decisions": {
                "AAA": {
                    "details": {
                        "entry_checks": {
                            "order_budget": 1_400_000,
                            "available_cash": 100_000_000,
                            "nav": 100_000_000,
                        }
                    }
                }
            }
        },
    )

    assert quantity == 100
    assert subject.quantity.options["placeholder_text"] == "100 CP"


@pytest.mark.parametrize("editing", [True, False])
def test_auto_quantity_refresh_does_not_touch_the_focused_editor(editing) -> None:
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("AAA")
    subject.quantity = _Entry()
    subject.quantity._viking_editing = editing
    subject.quantity._viking_title_widget = _Label()
    status = {"decisions": {"AAA": {"details": {"entry_checks": {
        "order_budget": 1_400_000, "available_cash": 100_000_000, "nav": 100_000_000,
    }}}}}
    for _refresh in range(5):
        subject._suggested_order_quantity(7.29, status)
    assert ("placeholder_text" in subject.quantity.options) is (not editing)
    assert subject.quantity._viking_title_widget.options["text"] == "KL · AUTO"
    subject.quantity.value = "200"
    subject.quantity.options.clear()
    for _refresh in range(5):
        subject._suggested_order_quantity(7.29, status)
    assert "placeholder_text" not in subject.quantity.options
    assert subject.quantity.get() == "200"
    assert subject.quantity._viking_title_widget.options["text"] == "KL · TAY"
    assert "KL TAY" in subject._auto_quantity_hint()
    subject.quantity.value = ""
    subject.quantity._viking_editing = False
    subject._suggested_order_quantity(7.29, status)
    assert subject.quantity._viking_title_widget.options["text"] == "KL · AUTO"


class _Real:
    @staticmethod
    def api_health() -> dict[str, object]:
        return {"total_requests": 1, "last_status": 200, "last_error": ""}

    @staticmethod
    def configured() -> bool:
        return True

    @staticmethod
    def has_trading_token() -> bool:
        return True


def test_health_panel_renders_runtime_state_instead_of_staying_on_dashes() -> None:
    subject = DashboardPanelsMixin()
    subject.real = _Real()
    subject.mode = _Value("REAL")
    subject.symbol = _Value("AAA")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())

    now = time.time()
    subject._refresh_api_health_panel({
        "heartbeat_at": now,
        "daemon_status": "RUNNING",
        "market_status": "OPEN",
        "api_health": {
            "websocket": {"connected": True, "authenticated": True},
            "rest": {"total_requests": 1, "last_status": 200, "last_error": ""},
        },
        "ticks": {"AAA": {"price": 7.15, "timestamp": now}},
    })

    assert subject.preview_health_title.options["text"] == "HEALTH OK"
    assert subject.preview_health_daemon.options["text"] == "DAEMON OK"
    assert subject.preview_health_core.options["text"] == "DNSE OK"
    assert subject.preview_health_ws.options["text"] == "WS OK"
    assert subject.preview_health_rest.options["text"] == "API OK"
    assert subject.preview_health_token.options["text"] == "OTP OK"
    assert subject.preview_health_trade.options["text"] == "GIÁ MỞ"


@pytest.mark.parametrize("market,label,title", [
    ("CALENDAR_LOADING", "LỊCH CHỜ", "HEALTH CẢNH BÁO"),
    ("CALENDAR_UNKNOWN", "LỊCH LỖI", "HEALTH LỖI"),
])
def test_health_panel_distinguishes_calendar_loading_from_failure(market, label, title):
    subject = DashboardPanelsMixin()
    subject.real, subject.mode, subject.symbol = _Real(), _Value("REAL"), _Value("AAA")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())
    subject._refresh_api_health_panel({
        "heartbeat_at": time.time(), "daemon_status": "RUNNING", "market_status": market,
        "api_health": {"rest": {"total_requests": 1, "last_status": 200}},
        "ticks": {"AAA": {"price": 7.15, "timestamp": time.time(), "stale": True}},
    })
    assert subject.preview_health_title.options["text"] == title
    assert subject.preview_health_trade.options["text"] == label


def test_recent_previous_process_heartbeat_is_still_sync_after_restart():
    subject = DashboardPanelsMixin()
    subject.real, subject.mode, subject.symbol = _Real(), _Value("REAL"), _Value("AAA")
    subject.daemon_process = SimpleNamespace(poll=lambda: None)
    subject._daemon_started_at = time.time()
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())
    subject._refresh_api_health_panel({
        "heartbeat_at": subject._daemon_started_at - 1,
        "daemon_status": "RUNNING", "market_status": "CLOSED",
    })
    assert subject.preview_health_daemon.options["text"] == "DAEMON SYNC"
    assert subject.preview_health_title.options["text"] == "HEALTH CẢNH BÁO"


@pytest.mark.parametrize("phase", ["OPEN", "ATO", "ATC"])
@pytest.mark.parametrize("quote", [
    {"timestamp": 1.0}, {"stale": True}, {"frozen": True},
    {"symbol": "MSN"}, {"health": "ERROR"}, {"timestamp": "bad"},
])
def test_health_never_marks_unusable_quote_green(phase, quote):
    subject = DashboardPanelsMixin()
    subject.real, subject.mode, subject.symbol = _Real(), _Value("REAL"), _Value("AAA")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())
    subject._refresh_api_health_panel({
        "heartbeat_at": time.time(), "daemon_status": "RUNNING", "market_status": phase,
        "api_health": {"websocket": {"connected": True, "authenticated": True}},
        "ticks": {"AAA": {"timestamp": time.time(), "price": 7.15, **quote}},
    })
    assert subject.preview_health_title.options["text"] == "HEALTH LỖI"
    assert subject.preview_health_trade.options["text"] == "GIÁ CHẬM"
    assert subject.preview_health_trade.options["text_color"] != COL_GREEN


def test_health_uses_received_quote_time_not_last_match_time():
    subject = DashboardPanelsMixin()
    subject.real, subject.mode, subject.symbol = _Real(), _Value("REAL"), _Value("AAA")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())
    subject._refresh_api_health_panel({
        "heartbeat_at": time.time(), "daemon_status": "RUNNING", "market_status": "OPEN",
        "api_health": {"websocket": {"connected": True, "authenticated": True}},
        "ticks": {"AAA": {"timestamp": 1.0, "received_at": time.time(), "price": 7.15}},
    })
    assert subject.preview_health_title.options["text"] == "HEALTH OK"
    assert subject.preview_health_trade.options["text"] == "GIÁ MỞ"


def test_health_hint_explains_quote_loss_and_updates_after_actual_recovery(monkeypatch):
    from viking_v2.services.daemon import merge_live_tick
    from viking_v2.trading.validation import MAX_QUOTE_AGE
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    subject = DashboardPanelsMixin()
    subject.real, subject.mode, subject.symbol = _Real(), _Value("REAL"), _Value("MSN")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())
    status = {
        "heartbeat_at": 1000, "daemon_status": "RUNNING", "market_status": "OPEN",
        "api_health": {"websocket": {"connected": True, "authenticated": True}},
        "ticks": {"MSN": {"symbol": "MSN", "source": "REST", "price": 74.2,
                          "timestamp": 1000 - MAX_QUOTE_AGE - 1, "stale": True}},
    }
    subject.bridge = SimpleNamespace(read_status=lambda: status)
    subject._refresh_api_health_panel(status)
    before = subject._api_health_hint()
    assert "MSN · nguồn: REST (API dự phòng)" in before
    assert "BỊ LOẠI: Giá quá thời gian cho phép" in before
    assert "WS OK chỉ là kết nối" in before
    assert subject.preview_health_title.options["text"] == "HEALTH LỖI"
    status["ticks"]["MSN"] = merge_live_tick(
        {"symbol": "MSN", "source": "WS", "price": 74.4, "timestamp": 1000},
        status["ticks"]["MSN"], None,
    )
    subject._refresh_api_health_panel(status)
    after = subject._api_health_hint()
    assert "MSN · nguồn: WS (WebSocket)" in after and "HỢP LỆ" in after
    assert "0.0s trước" in after and "BỊ LOẠI" not in after
    assert subject.preview_health_title.options["text"] == "HEALTH OK"


def test_quote_hint_reads_current_selected_symbol_and_receipt_age(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("CTS")
    subject.bridge = SimpleNamespace(read_status=lambda: {
        "market_status": "OPEN", "ticks": {
            "CTS": {"source": "WS", "timestamp": 1, "received_at": 999},
            "MSN": {"source": "REST", "timestamp": 1, "stale": True},
        },
    })
    hint = subject._quote_health_hint()
    assert hint.startswith("CTS · nguồn: WS") and "1.0s trước" in hint
    assert "HỢP LỆ" in hint and "MSN" not in hint
    subject.symbol.value = "MSN"
    assert "BỊ LOẠI" in subject._quote_health_hint()


@pytest.mark.parametrize("phase", ["CLOSED", "LUNCH", "BREAK", "OFFLINE"])
def test_quote_hint_does_not_call_closed_session_a_live_quote_failure(phase):
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("MSN")
    subject._preview_health_status = {"market_status": phase, "ticks": {
        "MSN": {"source": "CLOSE", "timestamp": 1, "frozen": True},
    }}
    hint = subject._quote_health_hint()
    assert "Ngoài phiên" in hint and "BỊ LOẠI" not in hint


def test_quote_hint_uses_selected_symbols_session_not_another_open_exchange():
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("MSN")
    subject._preview_health_status = {
        "market_status": "OPEN", "symbol_phases": {"MSN": "CLOSED"},
        "ticks": {"MSN": {"source": "CLOSE", "timestamp": 1, "frozen": True}},
    }
    hint = subject._quote_health_hint()
    assert "Ngoài phiên" in hint and "BỊ LOẠI" not in hint


def test_health_hint_explains_cycle_stage_without_raw_exception_or_credentials():
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("MSN")
    subject._preview_health_status = {
        "market_status": "OPEN", "error": "raw error with SECRET",
        "cycle_error_context": {
            "symbol": "MSN", "stage": "INDICATORS", "exception_type": "TypeError",
        },
    }
    hint = subject._api_health_hint()
    assert "MSN · tính EMA/RSI/ATR · TypeError" in hint
    assert "daemon.log" in hint and "không ghi mỗi tick" in hint
    assert "SECRET" not in hint


def test_rule_and_order_hints_distinguish_waiting_queue_sent_and_partial_fill():
    subject = DashboardPanelsMixin()
    subject._preview_rule_decision = {
        "reason": "BUY_WINDOW_WAIT", "details": {"buy_window": {"start": "14:00"}},
    }
    assert "Có tín hiệu; chờ từ 14:00" in subject._rule_decision_hint()
    assert "Có tín hiệu ≠ đã gửi lệnh" in subject._rule_decision_hint()
    hint = subject._order_lifecycle_hint()
    assert "CACHE / CHỜ GỬI / CHỜ OTP" in hint
    assert "DNSE đã nhận; chưa có nghĩa đã khớp" in hint
    assert "x < y là khớp một phần" in hint
    assert "Telegram BUY báo đã xếp yêu cầu" in hint


@pytest.mark.parametrize("ui,daemon,label", [
    ({"total_requests": 1, "last_status": 500}, {"total_requests": 1000, "last_status": 200}, "API LỖI"),
    ({"total_requests": 1000, "last_status": 200}, {"total_requests": 1, "last_status": 500}, "API LỖI"),
    ({"total_requests": 1, "last_status": 0, "last_error": "timeout"}, {"total_requests": 1000, "last_status": 200}, "API LỖI"),
    ({"total_requests": 0, "last_status": None}, {"total_requests": 1, "last_status": 200}, "API OK"),
    ({"total_requests": 0, "last_status": None}, {"total_requests": 0, "last_status": None}, "API CHỜ"),
])
def test_health_checks_rest_clients_independently(ui, daemon, label):
    subject = DashboardPanelsMixin()
    subject.real = SimpleNamespace(api_health=lambda: ui, configured=lambda: True,
                                   has_trading_token=lambda: True)
    subject.mode, subject.symbol = _Value("REAL"), _Value("AAA")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())
    status = {"heartbeat_at": time.time(), "daemon_status": "RUNNING", "market_status": "OPEN",
              "api_health": {"rest": daemon, "websocket": {"connected": True, "authenticated": True}},
              "ticks": {"AAA": {"timestamp": time.time(), "price": 7.15}}}
    subject._refresh_api_health_panel(status)
    assert subject.preview_health_rest.options["text"] == label
    if label == "API LỖI":
        assert subject.preview_health_title.options["text"] == "HEALTH LỖI"
        # A successful request in that same client clears the current failure.
        ui.update(total_requests=1001, last_status=200, last_error="")
        daemon.update(total_requests=1001, last_status=200, last_error="")
        subject._refresh_api_health_panel(status)
        assert subject.preview_health_title.options["text"] == "HEALTH OK"


class _Tree:
    def __init__(self) -> None:
        self.columns: tuple[str, ...] = ()
        self.headings: dict[str, dict[str, object]] = {}
        self.column_options: dict[str, dict[str, object]] = {}

    def __getitem__(self, key: str) -> tuple[str, ...]:
        assert key == "columns"
        return self.columns

    def configure(self, **options: object) -> None:
        self.columns = tuple(options["columns"])

    def heading(self, column: str, **options: object) -> None:
        self.headings[column] = options

    def column(self, column: str, **options: object) -> None:
        self.column_options[column] = options


def test_running_table_keeps_the_completed_split_column_design() -> None:
    tree = _Tree()

    DashboardTablesMixin._configure_tree(tree, RUNNING_COLUMNS)

    assert tree.columns == RUNNING_COLUMNS
    assert [tree.headings[key]["text"] for key in RUNNING_COLUMNS] == [
        "Ticket",
        "Thời gian",
        "Thông tin Lệnh",
        "Chốt lời/Lỗ (SL|TP)",
        "Chi phí/Phí qua đêm",
        "Rủi ro/Kỳ vọng (%)",
        "PnL / MAE / MFE",
        "Trạng thái",
        "✖",
    ]
    assert all(tree.column_options[key]["width"] == RUNNING_WIDTHS[key] for key in RUNNING_COLUMNS)
    assert RUNNING_HEADERS["Order"] != "Lệnh / KL / SL / TP / FEE"


def test_running_headers_fit_their_rendered_font(ui_root) -> None:
    from tkinter import font as tkfont
    from tkinter import ttk

    tree = ttk.Treeview(ui_root, show="headings")
    tree.grid(row=0, column=0)
    try:
        DashboardTablesMixin._configure_tree(tree, RUNNING_COLUMNS)
        ui_root.update_idletasks()
        heading_font = tkfont.Font(
            root=ui_root, family=FONT_TABLE_HEADING[0],
            size=FONT_TABLE_HEADING[1], weight="bold", slant="italic",
        )
        for column in RUNNING_COLUMNS:
            required = heading_font.measure(RUNNING_HEADERS[column]) + 32
            assert int(tree.column(column, "width")) >= required
    finally:
        tree.destroy()


def _running_subject(tree, mode, items=(), positions=(), broker_orders=(), cycles=()):
    subject = DashboardTablesMixin()
    subject.trees = {mode: tree}
    subject._running_row_actions = {}
    subject.queue = SimpleNamespace(list_all=lambda: list(items))
    subject.snapshots = {mode: ({}, list(positions), list(broker_orders))}
    subject.settings = SimpleNamespace(rule_parameters={})
    subject.trade_state = SimpleNamespace(list_cycles=lambda: list(cycles))
    subject.rule_state = SimpleNamespace(position_metrics=lambda *_: {})
    subject._cached_fee_rate = lambda *_: None
    subject._preview_buy_fee = lambda *_: None
    subject._row_time = lambda value: str(value)
    subject._sync_cancel_button = lambda: None
    return subject


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_unchanged_rows_do_not_rebuild_table_or_reset_operator_state(ui_root, monkeypatch, mode):
    from tkinter import ttk

    tree = ttk.Treeview(ui_root, show="headings")
    items = [OrderIntent("keep-row", "FPT", "SELL", 100, "LO", limit_price=100,
                         execution_mode=mode, action="CLOSE")]
    subject = _running_subject(tree, mode, items)
    try:
        subject._render_tables({})
        iid, = tree.get_children()
        tree.selection_set(iid)
        tree.focus(iid)
        ui_root.update_idletasks()
        viewport = tree.yview()
        calls = []
        for name in ("insert", "delete", "move", "selection_set", "yview_moveto"):
            monkeypatch.setattr(tree, name, lambda *args, operation=name, **kwargs: calls.append(operation))
        real_item = tree.item

        def item(row, option=None, **options):
            if options:
                calls.append("item_update")
            return real_item(row, option, **options)

        monkeypatch.setattr(tree, "item", item)
        monkeypatch.setattr(subject, "_configure_tree", lambda *_: calls.append("configure"))
        subject._render_tables({})
        assert calls == []
        assert tuple(tree.selection()) == (iid,)
        assert tree.focus() == iid and tree.yview() == viewport
    finally:
        tree.destroy()


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_row_updates_partial_fills_and_action_metadata_without_reinsert(ui_root, monkeypatch, mode):
    from tkinter import ttk

    tree = ttk.Treeview(ui_root, show="headings")
    intent = OrderIntent("partial-row", "FPT", "BUY", 10000, "LO", limit_price=100,
                         execution_mode=mode, status="WORKING", broker_order_id="fake-id")
    subject = _running_subject(tree, mode, [intent])
    try:
        subject._render_tables({})
        iid = f"LOCAL:{intent.id}"
        tree.selection_set(iid)
        monkeypatch.setattr(tree, "insert", lambda *args, **kwargs: pytest.fail("reinserted existing order"))
        monkeypatch.setattr(tree, "delete", lambda *args: pytest.fail("deleted existing order"))
        intent.status = "PARTIAL"
        intent.filled_quantity = 8000
        subject._render_tables({})
        assert tuple(tree.item(iid, "tags")) == ("partial_order",)
        assert "Khớp 8000/10000" in tree.item(iid, "values")[6]
        assert subject._running_row_actions[mode][iid]["status"] == "PARTIAL"
        # Same rounded display price must not leave stale right-click action data.
        intent.limit_price = 100.000001
        subject._render_tables({})
        assert subject._running_row_actions[mode][iid]["price"] == 100.000001
        intent.status = "CANCEL_PENDING"
        subject._render_tables({})
        action = subject._running_row_actions[mode][iid]
        assert not action["editable"] and not action["cancellable"]
        assert tuple(tree.selection()) == (iid,)
    finally:
        tree.destroy()


def test_finished_order_is_removed_but_other_rows_and_selection_survive(ui_root):
    from tkinter import ttk

    tree = ttk.Treeview(ui_root, show="headings")
    first = OrderIntent("first", "FPT", "SELL", 100, "LO", execution_mode="REAL", action="CLOSE")
    second = OrderIntent("second", "VIX", "SELL", 100, "LO", execution_mode="REAL", action="CLOSE")
    items = [first, second]
    subject = _running_subject(tree, "REAL", items)
    try:
        subject._render_tables({})
        tree.selection_set("LOCAL:first", "LOCAL:second")
        first.status = "FILLED"
        subject._render_tables({})
        assert tree.get_children() == ("LOCAL:second",)
        assert tree.selection() == ("LOCAL:second",)
        assert set(subject._running_row_actions["REAL"]) == {"LOCAL:second"}
        second.status = "CANCELLED"
        subject._render_tables({})
        assert not tree.get_children() and not tree.selection()
        assert subject._running_row_actions["REAL"] == {}
    finally:
        tree.destroy()


def test_table_order_matches_current_queue_even_when_rows_are_reused(ui_root):
    from tkinter import ttk

    tree = ttk.Treeview(ui_root, show="headings")
    items = [OrderIntent(name, "FPT", "SELL", 100, "LO", execution_mode="REAL", action="CLOSE")
             for name in ("first", "second", "third")]
    subject = _running_subject(tree, "REAL", items)
    try:
        subject._render_tables({})
        assert tree.get_children() == ("LOCAL:third", "LOCAL:second", "LOCAL:first")
        items[:] = [items[2], items[0], items[1]]
        subject._render_tables({})
        assert tree.get_children() == ("LOCAL:second", "LOCAL:first", "LOCAL:third")
    finally:
        tree.destroy()


def test_display_cycle_snapshot_is_read_once_and_respects_mode_package_and_next_refresh(ui_root):
    from tkinter import ttk

    real_tree = ttk.Treeview(ui_root, show="headings")
    paper_tree = ttk.Treeview(ui_root, show="headings")
    cycles = [
        TradeCycle("closed", "FPT", "REAL", status="CLOSED"),
        TradeCycle("real-1", "FPT", "REAL", loan_package_id="1", sl_mode="PERCENT", sl_value=-3),
        TradeCycle("real-2", "FPT", "REAL", loan_package_id="2", sl_mode="PERCENT", sl_value=-8),
        TradeCycle("paper-1", "FPT", "PAPER", sl_mode="PERCENT", sl_value=-2),
    ]
    position = {"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 100, "costPrice": 100}
    subject = _running_subject(real_tree, "REAL", positions=[{**position, "loanPackageId": "2"}])
    subject.trees["PAPER"] = paper_tree
    subject.snapshots["PAPER"] = ({}, [position], [])
    reads = []

    def list_cycles():
        reads.append(1)
        return cycles

    subject.trade_state.list_cycles = list_cycles
    try:
        subject._render_tables({})
        assert len(reads) == 1
        real_id, = real_tree.get_children()
        paper_id, = paper_tree.get_children()
        assert subject._running_row_actions["REAL"][real_id]["trade_id"] == "real-2"
        assert subject._running_row_actions["PAPER"][paper_id]["trade_id"] == "paper-1"
        assert "(-8%)" in real_tree.item(real_id, "values")[3]
        assert "(-2%)" in paper_tree.item(paper_id, "values")[3]
        cycles[2] = TradeCycle("real-2", "FPT", "REAL", loan_package_id="2", sl_enabled=False)
        subject._render_tables({})
        assert len(reads) == 2
        assert "SL OFF" in real_tree.item(real_id, "values")[3]
    finally:
        real_tree.destroy()
        paper_tree.destroy()


def test_empty_tables_do_not_decode_historical_cycles(ui_root):
    from tkinter import ttk

    tree = ttk.Treeview(ui_root, show="headings")
    subject = _running_subject(tree, "REAL")
    subject.trade_state.list_cycles = lambda: pytest.fail("decoded unused historical ledger")
    try:
        subject._render_tables({})
        assert not tree.get_children()
    finally:
        tree.destroy()


def test_screen_log_is_bounded_but_every_message_still_reaches_logger(ui_root):
    import customtkinter as ctk

    manual = ctk.CTkTextbox(ui_root)
    bot = ctk.CTkTextbox(ui_root)
    subject = DashboardTablesMixin()
    recorded = []
    subject.logger = SimpleNamespace(info=lambda message, **_kwargs: recorded.append(message))
    subject.log_manual = manual
    subject.log_bot = bot
    subject._set_log_unread = lambda *_: None
    try:
        for index in range(VISIBLE_LOG_LINE_LIMIT + 7):
            subject._log(f"event-{index}")
        lines = manual.get("1.0", "end-1c").splitlines()
        assert len(lines) == VISIBLE_LOG_LINE_LIMIT
        assert lines[0].endswith("event-7")
        assert lines[-1].endswith(f"event-{VISIBLE_LOG_LINE_LIMIT + 6}")
        assert len(recorded) == VISIBLE_LOG_LINE_LIMIT + 7
        subject._log("bot event", "bot")
        assert "bot event" in bot.get("1.0", "end-1c")
        assert len(manual.get("1.0", "end-1c").splitlines()) == VISIBLE_LOG_LINE_LIMIT
        subject._log("line 1\nline 2\nline 3")
        assert len(manual.get("1.0", "end-1c").splitlines()) == VISIBLE_LOG_LINE_LIMIT
        assert "line 1\nline 2\nline 3" in manual.get("1.0", "end-1c")
    finally:
        manual.destroy()
        bot.destroy()


@pytest.mark.parametrize("label,otp_type,state,button_text", [
    ("EMAIL OTP", "email_otp", "normal", "GỬI EMAIL"),
    ("SMART OTP", "smart_otp", "disabled", "SMART OTP TRÊN APP"),
])
def test_otp_selector_routes_correct_method_without_sending_email_for_smart(label, otp_type, state, button_text):
    from viking_v2.connections.window import ConnectionPopup

    subject = SimpleNamespace(client=SimpleNamespace(otp_type=""), btn_send_otp=_Label())
    ConnectionPopup._otp_type_changed(subject, label)
    assert subject.client.otp_type == otp_type
    assert subject.btn_send_otp.options == {"state": state, "text": button_text}


class _Tabs:
    def __init__(self, value: str = "PREVIEW") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Selector(_Tabs):
    def __init__(self) -> None:
        super().__init__()
        self._buttons_dict = {name: _Label() for name in ("PREVIEW", "Manual", "Bot")}


def test_log_tabs_show_unread_star_and_clear_it_when_selected() -> None:
    subject = DashboardPanelsMixin()
    subject.log_tabview = _Tabs()
    subject.info_tab_selector = _Selector()
    subject.log_tab_keys = {"manual": "Manual", "bot": "Bot"}
    subject.log_tab_unread = {"manual": False, "bot": False}

    subject._set_log_unread("bot", True)

    assert subject.log_tab_unread["bot"] is True
    assert subject.info_tab_selector._buttons_dict["Bot"].options["text"] == "Bot *"

    subject._select_info_tab("Bot *")

    assert subject.log_tabview.get() == "Bot"
    assert subject.log_tab_unread["bot"] is False
    assert subject.info_tab_selector._buttons_dict["Bot"].options["text"] == "Bot"
    assert not hasattr(DashboardPanelsMixin, "_sync_info_selector_mode")


def test_settings_popups_open_and_have_no_overlapping_grid_controls(ui_root) -> None:
    import customtkinter as ctk

    from viking_v2.backtest.window import BacktestPopup
    from viking_v2.config import load_settings
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup
    from viking_v2.rules.window import RuleSettingsPopup

    root = ui_root
    client = DNSEClient(account_no="PAPER")
    popups = ()
    try:
        settings = load_settings("PAPER")
        popups = (
            ("RULE", RuleSettingsPopup(root, settings, "PAPER", lambda: None)),
            ("CONNECTION", ConnectionPopup(root, settings, "PAPER", client, lambda: None)),
            ("BACKTEST", BacktestPopup(root, settings, None)),
        )
        root.update_idletasks()
        clashes: list[str] = []

        def scan(label: str, widget: object) -> None:
            children = list(widget.winfo_children())
            if isinstance(widget, (ctk.CTkFrame, ctk.CTkScrollableFrame, ctk.CTkTabview)):
                occupied: dict[tuple[int, int], object] = {}
                for child in children:
                    info = child.grid_info()
                    if not info:
                        continue
                    row = int(info.get("row", 0))
                    column = int(info.get("column", 0))
                    rowspan = int(info.get("rowspan", 1))
                    columnspan = int(info.get("columnspan", 1))
                    for rr in range(row, row + rowspan):
                        for cc in range(column, column + columnspan):
                            if (rr, cc) in occupied:
                                clashes.append(f"{label} {type(widget).__name__} ô {(rr, cc)}")
                            occupied[(rr, cc)] = child
            for child in children:
                scan(label, child)

        for label, popup in popups:
            scan(label, popup.top)

        assert not clashes, "control bị đè lên nhau: " + ", ".join(clashes)
        rule_popup = popups[0][1]
        assert rule_popup.phase2_signal_card.master is rule_popup.phase2_mode_card.master
        assert int(rule_popup.phase2_mode_card.grid_info()["row"]) == 1
        assert int(rule_popup.phase2_mode_card.grid_info()["column"]) == 1
        assert int(rule_popup.phase2_confirmation_card.grid_info()["row"]) == 2
        connection_popup = popups[1][1]
        assert connection_popup.otp_card.master is connection_popup.dnse_compact_row
        assert connection_popup.paper_card.master is connection_popup.dnse_compact_row
        assert connection_popup.stats_card.master is connection_popup.dnse_compact_row
        assert int(connection_popup.otp_card.grid_info()["column"]) == 0
        assert int(connection_popup.paper_card.grid_info()["column"]) == 1
        assert int(connection_popup.stats_card.grid_info()["column"]) == 2
        assert {
            int(connection_popup.otp_card.grid_info()["row"]),
            int(connection_popup.paper_card.grid_info()["row"]),
            int(connection_popup.stats_card.grid_info()["row"]),
        } == {0}
        assert connection_popup.btn_save_dnse.cget("text") == "LƯU API"
        assert connection_popup.btn_clear_dnse.cget("text") == "XÓA API"
        assert connection_popup.save_token_switch.cget("text") == "LƯU TOKEN"
        assert connection_popup.daily_stats_choice.get() == "THEO NGÀY"
        assert connection_popup.daily_stats_segment.cget("values") == [
            "THEO NGÀY", "CỘNG DỒN",
        ]
        assert connection_popup.daily_stats_time.get() == "00:00"
        assert connection_popup.btn_save_daily_stats.cget("text") == "LƯU"
        assert connection_popup.tele_event_time_controls["buy_queued"] is connection_popup.tele_batch
        assert connection_popup.tele_event_time_controls["closed"].cget("text") == "1 TIN/VỊ THẾ"
        assert list(connection_popup.tele_event_labels) == [
            "buy_queued", "blocked_buy", "buy_lost", "protect", "indicator_exit", "closed",
            "external_sell", "corporate_action", "system",
        ]
        assert {int(button.grid_info()["column"]) for button in connection_popup.tele_event_hint_buttons.values()} == {1}
        assert connection_popup.save_telegram_token_switch.cget("text") == "LƯU BOT TOKEN"
        assert connection_popup.btn_clear_telegram_token.cget("text") == "XÓA"
        assert set(connection_popup.tele_cooldown_entries) == {
            "protect", "indicator_exit", "blocked_buy", "buy_lost", "corporate_action", "external_sell",
            "system",
        }
    finally:
        for _label, popup in popups:
            closer = getattr(popup, "close", None) or getattr(popup, "_close", None)
            if callable(closer) and popup.top.winfo_exists():
                closer()
        client.close()


@pytest.mark.parametrize("saved_mode", ["IMMEDIATE", "BATCH"])
def test_telegram_buy_delivery_selector_saves_draft_only_after_validating(
    ui_root, monkeypatch, saved_mode,
) -> None:
    from viking_v2.config import AppSettings
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup

    settings = AppSettings(telegram_buy_delivery_mode=saved_mode,
                           telegram_buy_batch_minutes=7).normalize()
    writes, applied = [], []
    monkeypatch.setattr("viking_v2.connections.window.save_settings",
                        lambda value, _account: writes.append(value.to_dict()))
    monkeypatch.setattr(ConnectionPopup, "_store_telegram_token", lambda *_args: "RAM")
    monkeypatch.setattr(ConnectionPopup, "show", lambda _self: None)
    client = DNSEClient(account_no="PAPER", api_key="", api_secret="")
    popup = ConnectionPopup(ui_root, settings, "PAPER", client, lambda: applied.append(True))
    try:
        assert popup.tele_buy_mode_selector.cget("values") == ["GỬI NGAY", "GOM TIN"]
        assert popup.tele_buy_mode.get() == ("GOM TIN" if saved_mode == "BATCH" else "GỬI NGAY")
        assert bool(popup.tele_batch.grid_info()) is (saved_mode == "BATCH")
        assert popup.tele_event_time_controls["buy_queued"] is popup.tele_batch
        original = settings.to_dict()
        popup.tele_chat.delete(0, "end")
        popup.tele_chat.insert(0, "changed-draft-chat")
        popup.tele_buy_mode_selector.set("GOM TIN")
        popup._telegram_buy_mode_changed()
        popup.tele_batch.delete(0, "end")
        popup.tele_batch.insert(0, "0")
        assert settings.to_dict() == original and writes == []  # Selection is a draft.
        popup._save_telegram()
        assert settings.to_dict() == original and writes == [] and applied == []
        assert "1 ĐẾN 120" in popup.tele_status.cget("text")
        popup.tele_buy_mode_selector.set("GỬI NGAY")
        popup._telegram_buy_mode_changed()
        assert popup.tele_batch.grid_info() == {} and popup.tele_batch_suffix.grid_info() == {}
        popup._save_telegram()  # Inactive invalid batch draft cannot prevent immediate delivery.
        assert settings.telegram_buy_delivery_mode == "IMMEDIATE"
        assert settings.telegram_buy_batch_minutes == 7 and len(writes) == len(applied) == 1
        popup.tele_buy_mode_selector.set("GOM TIN")
        popup._telegram_buy_mode_changed()
        assert popup.tele_batch.grid_info() and popup.tele_batch_suffix.grid_info()
        popup.tele_batch.delete(0, "end")
        popup.tele_batch.insert(0, "15")
        popup._save_telegram()
        assert settings.telegram_buy_delivery_mode == "BATCH"
        assert settings.telegram_buy_batch_minutes == 15 and len(writes) == len(applied) == 2
        assert writes[-1]["telegram_chat_id"] == "changed-draft-chat"
        assert settings.rule_parameters == original["rule_parameters"]
        assert settings.telegram_cooldown_minutes == original["telegram_cooldown_minutes"]
    finally:
        popup._close()
        client.close()


def test_telegram_buy_lost_option_and_cooldown_save_without_changing_trade_rules(ui_root, monkeypatch):
    from viking_v2.config import AppSettings
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup

    settings = AppSettings().normalize()
    before = settings.to_dict()
    writes = []
    monkeypatch.setattr("viking_v2.connections.window.save_settings", lambda value, _account: writes.append(value.to_dict()))
    monkeypatch.setattr(ConnectionPopup, "_store_telegram_token", lambda *_args: "RAM")
    monkeypatch.setattr(ConnectionPopup, "show", lambda _self: None)
    client = DNSEClient(account_no="PAPER", api_key="", api_secret="")
    popup = ConnectionPopup(ui_root, settings, "PAPER", client, lambda: None)
    try:
        assert popup.tele_event_labels["buy_lost"].cget("text") == "BUY · MẤT TÍN HIỆU"
        assert popup.tele_event_switches["buy_lost"].get()
        assert popup.tele_cooldown_entries["buy_lost"].get() == "60"
        popup.tele_event_switches["buy_lost"].set(False)
        popup.tele_cooldown_entries["buy_lost"].delete(0, "end")
        popup.tele_cooldown_entries["buy_lost"].insert(0, "-1")
        popup._save_telegram()
        assert settings.to_dict() == before and writes == []
        popup.tele_cooldown_entries["buy_lost"].delete(0, "end")
        popup.tele_cooldown_entries["buy_lost"].insert(0, "7")
        popup._save_telegram()
        assert settings.telegram_notifications["buy_lost"] is False
        assert settings.telegram_cooldown_minutes["buy_lost"] == 7 and len(writes) == 1
        assert settings.rule_parameters == before["rule_parameters"]
    finally:
        popup._close()
        client.close()


def test_volume_scanner_popup_has_safe_defaults_and_does_not_touch_watchlist(ui_root) -> None:
    from viking_v2.config import load_settings
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup

    settings = load_settings("PAPER")
    original_watchlist = list(settings.watchlist)
    client = DNSEClient(account_no="PAPER")
    popup = ConnectionPopup(ui_root, settings, "PAPER", client, lambda: None)
    try:
        assert popup.btn_volume_scanner.cget("text") == "LỌC VOLUME VN100"
        assert popup.btn_volume_scanner.master is popup.volume_scanner_card
        assert int(popup.volume_scanner_card.grid_info()["row"]) == 0
        assert "xuất Excel" in popup.volume_scanner_hint.cget("text")
        assert int(popup.priority_card.grid_info()["row"]) == 1
        assert popup.priority_picker.get() == settings.priority_symbols
        assert popup.btn_export_watchlist.cget("text") == "XUẤT EXCEL"
        assert popup.btn_import_watchlist.cget("text") == "NHẬP EXCEL"
        popup._open_volume_scanner()
        ui_root.update_idletasks()
        scanner = popup._volume_popup
        assert scanner is not None
        assert scanner.scan_count_entry.get() == "100"
        assert scanner.result_count_entry.get() == "20"
        assert scanner.sessions_choice.get() == "5"
        assert scanner.threshold_entry.get() == "20"
        assert scanner.direction_choice.get() == "CẢ HAI"
        assert scanner.scan_button.cget("text") == "BẮT ĐẦU LỌC"
        assert int(scanner.scope_group.grid_info()["column"]) == 0
        assert int(scanner.filter_group.grid_info()["column"]) == 1
        assert int(scanner.title_label.cget("font")[1]) >= 22
        assert scanner.export_button.cget("state") == "disabled"
        assert scanner.replace_button.cget("state") == "disabled"
        assert scanner.tree["columns"] == ("symbol", "previous", "recent", "change", "status")
        assert settings.watchlist == original_watchlist
    finally:
        popup._close()
        client.close()


def test_telegram_token_storage_can_be_session_only_or_persisted(
    ui_root, monkeypatch,
) -> None:
    from viking_v2.config import load_settings
    from viking_v2.connections.dnse.client import DNSEClient
    import viking_v2.connections.window as connection_window

    env_key = "VIKING_TEST_TELEGRAM_TOKEN"
    settings = load_settings("PAPER")
    settings.telegram_token_env = env_key
    monkeypatch.delenv(env_key, raising=False)
    writes: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        connection_window, "update_env",
        lambda values: writes.append(dict(values)),
    )
    client = DNSEClient(account_no="PAPER")
    popup = connection_window.ConnectionPopup(
        ui_root, settings, "PAPER", client, lambda: None,
    )
    try:
        popup.save_telegram_token_env.set(False)
        assert popup._store_telegram_token("ram-secret") == "RAM"
        assert writes[-1] == {env_key: None}
        assert ui_root._telegram_session_token == "ram-secret"

        popup.save_telegram_token_env.set(True)
        assert popup._store_telegram_token("saved-secret") == ".ENV"
        assert writes[-1] == {env_key: "saved-secret"}
        assert ui_root._telegram_session_token == "saved-secret"
    finally:
        popup._close()
        client.close()
        if hasattr(ui_root, "_telegram_session_token"):
            delattr(ui_root, "_telegram_session_token")


def test_phase2_preview_uses_readable_stacked_rows(ui_root) -> None:
    import customtkinter as ctk

    root = ui_root
    parent = None
    try:
        parent = ctk.CTkFrame(root)
        parent.grid(row=0, column=0, sticky="nsew")
        subject = DashboardPanelsMixin()
        subject._build_order_preview_tab(parent)

        assert int(subject.preview_rule_ema.grid_info()["row"]) == 1
        assert int(subject.preview_rule_sell_ema.grid_info()["row"]) == 2
        assert int(subject.preview_rule_rsi.grid_info()["row"]) == 3
        assert subject.preview_rule_ema.master is subject.preview_rule_sell_ema.master
        assert subject.preview_rule_ema.master is subject.preview_rule_rsi.master
        assert int(subject.preview_rule_phase3_detail.grid_info()["row"]) == 1

        root.update_idletasks()
        management_cards = (
            subject.preview_tp_value.master,
            subject.preview_sl_value.master,
            subject.preview_normal_value.master,
            subject.preview_exit_value.master,
        )
        assert {
            (int(card.grid_info()["row"]), int(card.grid_info()["column"]))
            for card in management_cards
        } == {(0, 0), (0, 1), (1, 0), (1, 2)}
        assert int(subject.preview_atr.master.grid_info()["row"]) == 0
        assert int(subject.preview_atr.master.grid_info()["column"]) == 2
        assert int(subject.preview_normal_value.master.grid_info()["columnspan"]) == 2
        for card in management_cards:
            content_bottom = max(
                child.winfo_y() + child.winfo_height()
                for child in card.winfo_children()
            )
            assert content_bottom <= card.winfo_height()
    finally:
        if parent is not None and parent.winfo_exists():
            parent.destroy()


def test_backtest_ui_covers_every_static_rule_parameter() -> None:
    from viking_v2.backtest.window import BACKTEST_RULE_KEYS
    from viking_v2.rules.business import StaticRuleParameters

    assert BACKTEST_RULE_KEYS == frozenset(StaticRuleParameters.__dataclass_fields__)


def test_signal_history_ui_names_cancellations_and_recording_time_without_creating_orders(ui_root):
    from copy import deepcopy
    from viking_v2.dashboard.windows import HistoryPopup

    rows = [
        {"timestamp": "2026-10-09 13:39:41", "symbol": "HDB", "signal": "BUY",
         "acted": "WAIT", "blocked_by": "BUY_WINDOW_BROKEN"},
        {"timestamp": "2026-10-09 14:00:01", "symbol": "HDB", "signal": "SELL",
         "acted": "WAIT", "blocked_by": "NO_NEW_BUY_SIGNAL"},
        {"timestamp": "2026-10-09 14:06:11", "symbol": "HDB", "signal": "SELL",
         "acted": "WAIT", "blocked_by": "NO_NEW_BUY_SIGNAL"},
    ]
    before = deepcopy(rows)
    popup = HistoryPopup(ui_root, rows_provider=lambda _mode: [], signals_provider=lambda: rows)
    try:
        popup.tabs.set("TÍN HIỆU")
        ui_root.update_idletasks()
        tree = popup.signal_tree
        assert tree.heading("#0", "text") == "NGÀY / MÃ / GIỜ GHI NHẬN"
        assert tree.heading("suggestion", "text") == "XỬ LÝ"
        assert tree.heading("display_signal", "text") == "SỰ KIỆN"
        parent = tree.get_children()[0]
        assert "Xếp 0" in tree.set(parent, "reason")
        symbol = tree.get_children(parent)[0]
        sell, cancelled = tree.get_children(symbol)
        assert tree.set(cancelled, "display_signal") == "MẤT ENTRY"
        assert tree.set(cancelled, "suggestion") == "Hủy chờ"
        assert tree.set(sell, "suggestion") == "Chỉ tín hiệu"
        assert tree.set(sell, "display_signal") == "EXIT · E"
        assert tree.item(sell, "text").strip() == "14:00:01 (×2 cũ)"
        assert rows == before
    finally:
        popup.close()
