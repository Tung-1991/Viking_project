"""The displayed START must use actual cost and must never approve an exit."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.dashboard.protect_preview import dynamic_start_preview, start_caption


@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
def test_unfilled_order_shows_only_a_preview(policy):
    params = {"dynamic_policy": policy, "normal_atr_activation_multiplier": .55}
    details = {"atr14_daily_pct": 2.6, "peak_profit_pct": 99}
    before = deepcopy((params, details))
    value = dynamic_start_preview(71.6, params, details)
    assert value["start"] == pytest.approx(72.62388)
    assert value["state"] == "PREVIEW" and not value["position"]
    assert "72,624" in start_caption(value)
    assert (params, details) == before


@pytest.mark.parametrize("mfe,expected", [(1.42, "CHƯA TỚI"), (1.43, "ĐÃ BẬT"), (3, "ĐÃ BẬT"), (7, "SAU ARM")])
def test_position_activation_matches_protect_threshold(mfe, expected):
    value = dynamic_start_preview(71.4, {}, {"atr14_daily_pct": 2.6}, position=True, enabled=True, mfe=mfe)
    assert value["start"] == pytest.approx(72.42102)
    assert value["state"] == expected


def test_cost_basis_is_held_position_not_draft_price_and_books_are_separate():
    cycle = SimpleNamespace(open_quantity=100, avg_entry_price=71.4, em_modes=["NORMAL"], id="PAPER-MSN")
    seen = []
    def active(symbol, mode):
        seen.append((symbol, mode))
        return cycle if mode == "PAPER" else None
    app = DashboardPanelsMixin()
    app.settings = SimpleNamespace(rule_parameters={})
    app.mode = SimpleNamespace(get=lambda: "PAPER")
    app.trade_state = SimpleNamespace(active_for=active)
    app.rule_state = SimpleNamespace(position_metrics=lambda *_: {"peak_profit_pct": 3})
    details = {"atr14_daily_pct": 2.6}
    paper = app._dynamic_start_context("MSN", 71.6, details)
    assert paper["entry"] == 71.4 and paper["state"] == "ĐÃ BẬT"
    app.mode = SimpleNamespace(get=lambda: "REAL")
    real = app._dynamic_start_context("MSN", 71.6, details)
    assert real["entry"] == 71.6 and real["state"] == "PREVIEW"
    assert seen == [("MSN", "PAPER"), ("MSN", "REAL")]


@pytest.mark.parametrize("details,params,state", [
    ({}, {}, "CHỜ ATR"),
    ({"atr14_daily_pct": 2.6}, {"normal_dynamic_enabled": False}, "OFF"),
    ({"atr14_daily_pct": 2.6}, {"normal_atr_trail_enabled": False, "normal_retention_enabled": False}, "CHƯA CHỌN BẢO VỆ"),
])
def test_missing_data_and_disabled_layers_do_not_claim_activation(details, params, state):
    assert dynamic_start_preview(71.4, params, details, position=True, enabled=True, mfe=3)["state"] == state


@pytest.mark.parametrize("mfe,state", [(0, "CHƯA TỚI"), (.01, "ĐÃ BẬT")])
def test_start_off_requires_first_observed_gain(mfe, state):
    value = dynamic_start_preview(71.4, {"normal_atr_activation_enabled": False}, {}, position=True, enabled=True, mfe=mfe)
    assert value["state"] == state and "START CÓ LÃI" in start_caption(value)


def test_waiting_stock_preserves_start_activation_without_claiming_sell():
    value = dynamic_start_preview(22.65, {"dynamic_policy": "ALERT"}, {
        "atr14_daily_pct": 2.38, "protection_stage": "DYN", "normal_trigger_price": 23.082,
        "normal_state": "WAITING_SETTLEMENT",
    }, position=True, enabled=True, mfe=3)
    assert value["state"] == "ĐÃ BẬT"
    assert "SELL" not in start_caption(value)


@pytest.mark.parametrize("reset,expected", [(False, "ĐÃ BẬT"), (True, "CHƯA TỚI")])
def test_cached_peak_uses_the_same_optional_settlement_reset_as_live_rule(reset, expected):
    app = DashboardPanelsMixin()
    app.settings = SimpleNamespace(rule_parameters={"normal_t2_reset_enabled": reset})
    app.mode = SimpleNamespace(get=lambda: "PAPER")
    cycle = SimpleNamespace(open_quantity=100, avg_entry_price=71.4, em_modes=["NORMAL"], id="PAPER-MSN")
    app.trade_state = SimpleNamespace(active_for=lambda *_: cycle)
    app.rule_state = SimpleNamespace(position_metrics=lambda *_: {
        "peak_profit_pct": 3, "normal_sellable_peak_profit_pct": .4, "normal_t2_reset_applied": True,
    })
    value = app._dynamic_start_context("MSN", 71.6, {"atr14_daily_pct": 2.6})
    assert value["state"] == expected


@pytest.mark.parametrize("saved_retention,expected", [(72, "ĐÃ BẬT"), (90, "CHƯA TỚI")])
def test_cached_dynamic_floor_preserves_activation_unless_retention_was_edited(saved_retention, expected):
    app = DashboardPanelsMixin()
    app.settings = SimpleNamespace(rule_parameters={"normal_retention_pct": 72})
    app.mode = SimpleNamespace(get=lambda: "PAPER")
    cycle = SimpleNamespace(open_quantity=100, avg_entry_price=71.4, em_modes=["NORMAL"], id="PAPER-MSN")
    app.trade_state = SimpleNamespace(active_for=lambda *_: cycle)
    metrics = {"peak_profit_pct": 1.1, "dynamic_trigger_price": 72.114, "dynamic_retention_pct": saved_retention}
    app.rule_state = SimpleNamespace(position_metrics=lambda *_: metrics)
    details = {"atr14_daily_pct": 2.6}
    before = deepcopy((metrics, details))
    value = app._dynamic_start_context("MSN", 71.6, details)
    assert value["state"] == expected
    assert (metrics, details) == before
