"""The P1 swap and exit prices are display-only, never trading commands."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.dashboard.view import COL_MUTED, COL_RED, COL_WARN
from viking_v2.rules.business import protect_level
from viking_v2.trading.market import market_now


class Label:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)

    def grid(self, **_options):
        pass


def preview(mode="PAPER"):
    view = DashboardPanelsMixin()
    view.settings = AppSettings()
    view.mode = SimpleNamespace(get=lambda: mode)
    view.snapshots = {
        "PAPER": ({"equity": 100_000_000}, [], []),
        "REAL": ({"equity": 50_000_000}, [{"symbol": "MSN", "openQuantity": 200}], []),
    }
    view.preview_rule_market, view.preview_rule_market_detail = Label(), Label()
    view.preview_exit_value, view.preview_exit_detail = Label(), Label()
    return view


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("override", [True, False])
def test_p1_swap_only_changes_view_and_preserves_active_allocation(mode, override):
    view = preview(mode)
    view.settings.market_phase_override_enabled = override
    view.settings.market_phase_override_exposure_pct = 100
    view.rule_state = SimpleNamespace(
        confirmed_market_state=lambda: "UPTREND",
        market_confirmation=lambda _n: {"confirmed": "UPTREND", "candidate": "DOWNTREND",
                                        "count": 1, "required": 3, "pending": True},
    )
    details = {"market": {"display_state": "ACCUMULATION", "override_enabled": True,
                           "confirmation_pending": False}, "exposure": 1.0}
    settings_before, snapshots_before, data_before = deepcopy(view.settings.to_dict()), deepcopy(view.snapshots), deepcopy(details)
    active_before = view._phase1_capital_preview(details)
    view._render_phase1_preview(details)
    initial = view.preview_rule_market.options["text"]
    view._swap_phase1_preview()
    assert view._preview_market_budget["exposure_pct"] == (90 if override else 100)
    assert ("TĂNG" in view.preview_rule_market.options["text"]) == override
    if override:
        assert "CHỜ GIẢM 1/3" in view.preview_rule_market_detail.options["text"]
        assert "THAM KHẢO" in view._market_confirmation_hint()
        assert "Đang giao dịch theo tỷ trọng chọn tay" in view._market_confirmation_hint()
    else:
        assert "CHỌN TAY · THAM KHẢO" in view.preview_rule_market_detail.options["text"]
    assert view._phase1_capital_preview(details) == active_before
    view._render_phase1_preview(details)  # Poll refresh must keep the chosen view.
    assert view._preview_p1_alternate
    view._swap_phase1_preview()
    assert view.preview_rule_market.options["text"] == initial
    assert view.settings.to_dict() == settings_before
    assert view.snapshots == snapshots_before and details == data_before
    assert view.mode.get() == mode


def test_auto_view_does_not_invent_override_trend_or_exposure_without_rule_state():
    view = preview()
    view.settings.market_phase_override_enabled = True
    view.settings.market_phase_override_exposure_pct = 100
    view._render_phase1_preview({"exposure": 1.0, "market": {"display_state": "ACCUMULATION"}})
    view._swap_phase1_preview()
    assert view._preview_market_budget["state"] == "UNKNOWN"
    assert view._preview_market_budget["exposure_pct"] == 0
    assert "TÍCH LŨY" not in view.preview_rule_market.options["text"]
    assert "ĐANG TÍNH" in view.preview_rule_market.options["text"]


def test_auto_view_reads_confirmed_state_not_override_display_state():
    view = preview()
    view.settings.market_phase_override_enabled = True
    view._render_phase1_preview({"exposure": 1.0, "market": {
        "display_state": "ACCUMULATION", "auto_display_state": "DOWNTREND",
        "confirmed_state": "DOWNTREND", "candidate_state": "DOWNTREND",
    }})
    view._swap_phase1_preview()
    assert view._preview_market_budget["exposure_pct"] == 10
    assert view.preview_rule_market.options["text"] == "GIẢM · CP: 10% · TIỀN: 90%"


@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
@pytest.mark.parametrize("quantity", [0, 200])
@pytest.mark.parametrize("signal", ["--", "BUY", "SELL"])
def test_e_shows_market_price_and_signal_without_fabricating_a_fixed_exit(policy, quantity, signal):
    view = preview()
    view._current_tick_price = 74.2
    view._render_exit_sell_preview(signal, True, quantity, policy)
    assert view.preview_exit_value.options["text"] == "TT: 74,200"
    detail = view.preview_exit_detail.options["text"]
    if quantity == 0:
        assert detail == "CHƯA VỊ THẾ"
        assert view.preview_exit_value.options["text_color"] != COL_RED
    elif policy == "ALERT":
        assert "CHỈ BÁO" in detail and "100%" not in detail
        if signal == "SELL":
            assert view.preview_exit_value.options["text_color"] == COL_WARN
    else:
        assert detail == ("SELL · 100%" if signal == "SELL" else "CHỜ SELL · 100%")
    hint = view._exit_preview_hint()
    assert "không có một giá kích hoạt cố định" in hint
    assert "74,200 đ" in hint and "không bảo đảm giá khớp" in hint


@pytest.mark.parametrize("price", [0, None, float("nan"), float("inf")])
def test_e_missing_price_stays_unknown(price):
    view = preview()
    view._current_tick_price = price
    view._render_exit_sell_preview("SELL", True, 100, "AUTO")
    assert view.preview_exit_value.options["text"] == "CHỜ GIÁ TT"
    assert "chưa có giá" in view._exit_preview_hint()


def test_e_off_never_looks_like_an_executable_sell():
    view = preview()
    view._current_tick_price = 74.2
    view._render_exit_sell_preview("SELL", False, 200, "AUTO")
    assert view.preview_exit_value.options["text"] == "CHƯA ÁP DỤNG"
    assert view.preview_exit_value.options["text_color"] == COL_MUTED
    assert view.preview_exit_detail.options["text"] == "ĐANG TẮT"


def test_e_holdings_are_from_selected_book_not_old_bot_decision():
    view = preview("PAPER")
    assert view._exit_preview_quantity({"position_quantity": 200}, "MSN") == 0
    view.mode = SimpleNamespace(get=lambda: "REAL")
    assert view._exit_preview_quantity({}, "MSN") == 200
    assert view._exit_preview_quantity({}, "AAA") == 0


def test_stale_e_signal_is_not_displayed_as_an_active_sell():
    view = preview()
    raw = {"signal": "SELL", "details": {
        "updated_at": (market_now() - timedelta(minutes=5)).isoformat(), "execution_mode": "PAPER",
    }}
    assert view._exit_preview_signal(raw, "MSN") == "--"
    raw["details"]["updated_at"] = market_now().isoformat()
    assert view._exit_preview_signal(raw, "MSN") == "SELL"


def test_protect_example_prices_and_actual_position_hint_are_distinct():
    view = preview()
    value = protect_level(74.2, 7, 7, 2.5)
    view._preview_protect_prices = {"arm": 74.2 * 1.07, "sell": value.trigger_price}
    hint = view._protect_preview_hint()
    assert "ARM 79,394 đ → mốc bán 77,409 đ" in hint
    assert "Nếu đỉnh chỉ tới" in hint and "Đỉnh cao hơn" in hint
    view._preview_protect_prices["actual_sell"] = 80.0
    assert "Mốc bán của vị thế: 80,000 đ" in view._protect_preview_hint()
    assert "Nếu đỉnh chỉ tới" not in view._protect_preview_hint()
