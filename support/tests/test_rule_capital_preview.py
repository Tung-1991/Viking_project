"""P1/P3 explanations and amounts use the viewed book without broker writes."""
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.panels import DashboardPanelsMixin


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class Label:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)

    def grid(self, **_options):
        pass

    def grid_remove(self):
        pass


def subject(mode="REAL"):
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view.mode = Value(mode)
    view.rule_state = SimpleNamespace(confirmed_market_state=lambda: "UPTREND")
    view.snapshots = {
        "REAL": ({"equity": 50_000_000, "availableCash": 40_000_000}, [], []),
        "PAPER": ({"equity": 100_000_000, "availableCash": 90_000_000}, [], []),
    }
    return view


@pytest.mark.parametrize("mode,nav,stocks,cash", [
    ("REAL", 50_000_000, 45_000_000, 5_000_000),
    ("PAPER", 100_000_000, 90_000_000, 10_000_000),
])
def test_p1_percentages_translate_to_nav_of_viewed_book(mode, nav, stocks, cash):
    view = subject(mode)
    # Old decision money cannot overwrite the current snapshot/confirmed P1.
    amounts = view._phase1_capital_preview({"exposure": 0.1, "entry_checks": {"nav": 900_000_000}})
    assert amounts["nav"] == nav
    assert amounts["exposure_pct"] == 90
    assert amounts["stock_limit"] == stocks
    assert amounts["cash_reserve"] == cash
    assert view.snapshots[mode][0]["availableCash"] != cash  # Target is not actual cash.


@pytest.mark.parametrize("pct,stocks,cash", [(100, 50_000_000, 0), (0, 0, 50_000_000),
                                           (50, 25_000_000, 25_000_000)])
def test_override_zero_and_full_allocation_are_shown_without_waiting_for_decision(pct, stocks, cash):
    view = subject()
    view.settings.market_phase_override_enabled = True
    view.settings.market_phase_override_exposure_pct = pct
    amounts = view._phase1_capital_preview({"exposure": 0.9})
    assert amounts["stock_limit"] == stocks
    assert amounts["cash_reserve"] == cash
    assert amounts["exposure_pct"] == pct


def test_nav_includes_stock_value_when_balance_does_not_supply_nav():
    view = subject()
    view.snapshots["REAL"] = ({"availableCash": 20_000_000},
                              [{"symbol": "AAA", "quantity": 1000, "marketPrice": 30}], [])
    amounts = view._phase1_capital_preview({})
    assert amounts["nav"] == 50_000_000


def test_missing_snapshot_never_borrows_nav_from_other_book_or_old_decision():
    view = subject()
    view.snapshots.pop("REAL")
    amounts = view._phase1_capital_preview({"entry_checks": {"nav": 500_000_000}})
    assert amounts["nav"] is amounts["stock_limit"] is amounts["cash_reserve"] is None


def test_unconfirmed_p1_does_not_reuse_exposure_from_old_decision():
    view = subject()
    view.rule_state.confirmed_market_state = lambda: "UNKNOWN"
    amounts = view._phase1_capital_preview({"exposure": 0.9})
    assert amounts["exposure_pct"] == amounts["stock_limit"] == 0
    assert amounts["cash_reserve"] == 50_000_000
    assert amounts["state_authoritative"]


@pytest.mark.parametrize("budget", [4_000_000, 0, None])
def test_rule_preview_keeps_current_money_in_hint_not_an_extra_row(budget):
    view = subject()
    for name in ("market", "market_detail", "title", "ema", "sell_ema", "rsi",
                 "phase3", "phase3_detail", "phase3_guard", "reason"):
        setattr(view, f"preview_rule_{name}", Label())
    view._em_states = {}
    view._render_exit_sell_preview = lambda *_args: None
    view._slot_summary = {"mode": "REAL", "used": 0, "max": 5}
    view._preview_entry_checks = lambda *_args: ({"order_budget": budget, "available_cash": 40_000_000,
                                                  "nav": 50_000_000} if budget is not None else {})
    status = {"execution_mode": "REAL", "bot_enabled": True, "ticks": {"AAA": {"price": 7.25}},
              "decisions": {"AAA": {"market_state": "UPTREND", "action": "WAIT", "details": {
                  "exposure": 0.9, "entry_checks": {"order_budget": 9_000_000,
                                                    "available_capital": 9_000_000},
                  "market": {"confirmation_pending": True, "candidate_state": "DOWNTREND",
                             "confirmation_count": 1, "confirmation_required": 3},
              }}}}
    view._refresh_rule_preview(status, "AAA")
    assert "CP 90% · TIỀN 10%" in view.preview_rule_market.options["text"]
    assert not hasattr(view, "preview_rule_market_money")
    assert "XÁC NHẬN GIẢM · 1/3 PHIÊN" in view.preview_rule_market_detail.options["text"]
    expected = "4.00 tr" if budget else "0" if budget == 0 else "—"
    assert f"AUTO {expected}" in view.preview_rule_phase3.options["text"]
    hint = view._market_confirmation_hint()
    assert "NAV" in hint and "45.00 tr" in hint and "5.00 tr" in hint
    assert "không phải tỷ trọng đang nắm" in hint
    assert "1/3" in hint
    assert hint.startswith("ĐANG DÙNG: TĂNG")
    assert "Trong lúc chờ vẫn dùng TĂNG" in hint
    assert "CP 10% / TIỀN 90%" in hint
    assert "không phải bật BUY BOT" in hint
    assert "9 triệu/mã" in view._entry_capital_hint()
    assert f"VỐN AUTO {expected}" in view._entry_capital_hint()
    assert "MANUAL" in view._entry_capital_hint()


@pytest.mark.parametrize("state,pct", [("UPTREND", 90), ("ACCUMULATION", 100), ("DISTRIBUTION", 10)])
@pytest.mark.parametrize("scaling", [1.0, 1.5])
@pytest.mark.parametrize("width", [720, 1100])
def test_compact_rule_card_keeps_confirmation_and_guards_visible(ui_root, state, pct, scaling, width):
    import customtkinter as ctk
    import tkinter as tk
    top = ctk.CTkToplevel(ui_root)
    top.geometry(f"{width}x640+0+0")
    top.title("VIKING · OFFLINE PREVIEW")
    view = subject()
    ctk.set_widget_scaling(scaling)
    view.settings.market_phase_override_enabled = state != "UPTREND"
    view.settings.market_phase_override = state
    view.settings.market_phase_override_exposure_pct = pct
    try:
        view._build_order_preview_tab(top)
        view._em_states = {}
        view._render_exit_sell_preview = lambda *_args: None
        view._slot_summary = {"mode": "REAL", "used": 0, "max": 5}
        view._preview_entry_checks = lambda *_args: {"order_budget": 9_000_000,
                                                    "nav": 50_000_000, "available_cash": 40_000_000}
        from datetime import datetime, timedelta
        from viking_v2.trading.market import VN_TZ, market_now
        from viking_v2.dashboard.actions import DashboardActionsMixin
        view._preview_bars_symbol = "AAA"
        view._preview_bars = [
            {"time": int(datetime.combine(market_now().date() - timedelta(days=30-i), datetime.min.time(), VN_TZ).timestamp()),
             "open": 7+i*.01, "high": 7.2+i*.01, "low": 6.8+i*.01, "close": 7+i*.01, "closed": True}
            for i in range(30)
        ]
        view.symbol, view.order_type = Value("AAA"), Value("MARKET")
        view.quantity, view.tp, view.sl = Value("100"), Value("7%"), Value("-3.5%")
        view._current_tick_price = 7.25
        view._symbol_exchange = lambda *_args: "HOSE"
        view._preview_buy_fee = lambda gross, *_args: gross * .00045
        view._buy_button_presentation = DashboardActionsMixin._buy_button_presentation
        view.real = SimpleNamespace(has_trading_token=lambda: False)
        view.execute_button = Label()
        view._refresh_full_order_preview({"execution_mode": "REAL", "decisions": {"AAA": {
            "market_state": "UPTREND", "details": {"exposure": .9,
                "market": {"confirmation_pending": True, "candidate_state": "DOWNTREND",
                           "confirmation_count": 1, "confirmation_required": 3},
                "indicators": {"buy_ema_fast": 7.159, "buy_ema_slow": 7.156,
                               "sell_ema_fast": 7.159, "sell_ema_slow": 7.156,
                               "rsi": 48.7, "rsi_previous": 49.2},
            }}}})
        assert view.preview_atr.cget("text") != "--"
        assert "START --" not in view.preview_atr_detail.cget("text")
        assert "--/--" not in view.preview_rule_ema.cget("text")
        settled = tk.BooleanVar(master=ui_root, value=False)
        ui_root.after(220, lambda: settled.set(True))
        ui_root.wait_variable(settled)
        ui_root.update_idletasks()
        card = view.preview_rule_market.master
        from tkinter import font as tkfont
        for label in (view.preview_rule_market, view.preview_rule_phase3):
            rendered_font = tkfont.Font(root=ui_root, font=label._label.cget("font"))
            assert rendered_font.measure(label.cget("text")) <= label.winfo_width(), (
                label.cget("text"), label.cget("font"), rendered_font.actual(), label.winfo_width())
            assert label.cget("wraplength") == 0
        assert "TIỀN" in view.preview_rule_market.cget("text")
        assert not hasattr(view, "preview_rule_market_money")
        assert int(view.preview_focus_panel.cget("height")) == 300
        assert int(view.preview_rule_market_detail.grid_info()["row"]) == 1
        assert view.preview_rule_market_detail.winfo_y() + view.preview_rule_market_detail.winfo_height() <= card.winfo_height()
        assert view.preview_rule_reason.winfo_y() + view.preview_rule_reason.winfo_height() <= view.preview_rule_reason.master.winfo_height()
        for value in (view.preview_tp_value, view.preview_sl_value, view.preview_atr,
                      view.preview_normal_value, view.preview_exit_value):
            content_bottom = max(child.winfo_y() + child.winfo_height() for child in value.master.winfo_children())
            assert content_bottom <= value.master.winfo_height()
        import os
        if os.getenv("VIKING_CAPTURE_UI") == "1":
            import ctypes
            from pathlib import Path
            from PIL import ImageGrab
            from viking_v2.dashboard.windows import _HoverHint
            directory = Path(__file__).resolve().parents[2] / ".artifacts" / "ui-review"
            directory.mkdir(parents=True, exist_ok=True)
            hwnd = ctypes.windll.user32.GetParent(top.winfo_id())
            ImageGrab.grab(window=hwnd).save(directory / "rule-capital-preview.png")
            hint = _HoverHint(view.preview_rule_market_detail, view._market_confirmation_hint)
            hint._show()
            ui_root.update_idletasks()
            ImageGrab.grab(window=hwnd).save(directory / "rule-capital-hint.png")
            hint._hide()
    finally:
        top.destroy()
        ctk.set_widget_scaling(1.0)
