"""Three UI lines explain the actual phase and never change execution state."""
from copy import deepcopy

import pytest

from support.tests.test_running_table_details import status
from viking_v2.config import merge_rule_parameters
from viking_v2.models import OrderIntent


def render(*, params=None, details=None, **kw):
    values = dict(sellable=400, price=23.1, params=merge_rule_parameters(params or {}),
                  decision={"signal": "HOLD", "details": details or {}},
                  metrics={"price_history_verified": True, "peak_profit_pct": .98})
    values.update(kw)
    text, hint = status(**values)
    lines = text.splitlines()
    assert len(lines) == 3
    assert "ARM" in lines[1] and "DYN" in lines[1] and "E " in lines[1]
    return lines, hint


def test_start_and_arm_show_exact_prices_and_nearest_distance_not_rounded_percent():
    lines, hint = render(entry=71.4, price=71.8, details={"normal_atr_pct": 2.48},
                         params={"dynamic_policy": "ALERT", "indicator_exit_policy": "AUTO"})
    assert lines[1] == "ARM AUTO: CHỜ 76,398 · DYN ALERT: START 72,374 · E AUTO: CHỜ"
    assert "Chưa START 72,374" in lines[2] and "Còn 574đ/CP" in lines[2]
    assert "MFE +0.98%" in hint


@pytest.mark.parametrize("retention,trail,floor,expected", [
    (True, True, 23.046, ("GIỮ 72%: 23,046", "ATR TRAIL: 22,769", "Cách mốc 54đ/CP")),
    (True, False, 23.046, ("GIỮ 72%: 23,046", "Cách mốc 54đ/CP")),
    (False, True, 22.769408, ("ATR TRAIL: 22,769", "Cách mốc 331đ/CP")),
])
def test_dynamic_shows_only_enabled_floors_and_the_distance_to_the_live_floor(retention, trail, floor, expected):
    lines, hint = render(normal_state="DYN", details={"protection_stage": "DYN", "normal_atr_pct": 2.32,
        "normal_mfe_pct": (23.2 / 22.65 - 1) * 100, "normal_trigger_price": floor},
        params={"normal_retention_enabled": retention, "normal_atr_trail_enabled": trail})
    assert "DYN AUTO: MỐC" in lines[1]
    for value in expected:
        assert value in lines[2]
    assert ("GIỮ 72%" in lines[2]) is retention
    assert ("ATR TRAIL" in lines[2]) is trail


def test_a_previously_higher_floor_is_explained_instead_of_showing_a_lower_new_one():
    lines, hint = render(price=23.15, normal_state="DYN", details={"protection_stage": "DYN", "normal_atr_pct": 2.32,
        "normal_mfe_pct": (23.2 / 22.65 - 1) * 100, "normal_trigger_price": 23.1})
    assert "GIỮ MỐC CŨ 23,100" in lines[2] and "50đ/CP" in lines[2]
    assert "GIỮ 72%: 23,046" in hint


@pytest.mark.parametrize("price,hit", [(109, False), (107, True)])
def test_after_arm_dynamic_yields_even_when_price_returns_below_arm(price, hit):
    lines, hint = render(entry=100, price=price, sl_price=96.5, normal_state="ARM",
        params={"dynamic_policy": "ALERT"},
        details={"protection_stage": "ARM", "normal_mfe_pct": 10, "normal_trigger_price": 107.25},
        metrics={"price_history_verified": True, "dynamic_trigger_price": 109})
    assert "ARM AUTO: MỐC 107,250" in lines[1] and "DYN: NHƯỜNG ARM" in lines[1]
    assert "ATR TRAIL" not in lines[2] and "GIỮ 72%" not in lines[2]
    assert ("Đã chạm 107,250" in lines[2]) is hit
    if not hit:
        assert "LÙI ĐỈNH 2.5%" in lines[2]
    assert "không chạy song song" in hint


def test_disabled_after_arm_does_not_silently_resume_dynamic():
    lines, _ = render(entry=100, price=109, params={"normal_arm_enabled": False},
                      details={"normal_mfe_pct": 10})
    assert "ARM TẮT" in lines[1] and "DYN: NHƯỜNG ARM" in lines[1]
    assert "Nhánh SAU ARM đang tắt" in lines[2]


@pytest.mark.parametrize("which", ["SL", "TP"])
def test_nearest_sl_or_tp_can_take_focus_instead_of_dynamic(which):
    lines, _ = render(entry=100, price=96.6 if which == "SL" else 106.9, sl_price=96.5,
        tp_price=107, tp_pct=7, params={"normal_dynamic_enabled": False}, details={"normal_mfe_pct": 6.9})
    assert f"{'GẦN SL 96,500' if which == 'SL' else 'TP CHỜ +7% @ 107,000'}" in lines[2]
    assert "100đ/CP" in lines[2]


@pytest.mark.parametrize("paused,sellable,ending", [
    (False, 400, "Chỉ báo theo ALERT; chưa tạo SELL"),
    (False, 0, "Chờ cổ bán được; chưa tạo SELL"),
    (True, 400, "TỰ BÁN TẠM DỪNG; chưa tạo SELL"),
])
def test_a_hit_takes_priority_over_nearby_targets_without_claiming_an_order(paused, sellable, ending):
    lines, hint = render(price=23, sellable=sellable, paused=paused, normal_state="DYN",
        params={"dynamic_policy": "ALERT"}, details={"normal_trigger_price": 23.046, "normal_mfe_pct": 2.43,
        "normal_atr_pct": 2.32, "protection_stage": "DYN"})
    assert "Đã chạm 23,046" in lines[2] and lines[2].endswith(ending)
    assert "SELL đã gửi" not in "\n".join(lines) and "Chưa có yêu cầu SELL" in hint


@pytest.mark.parametrize("reason,label", [("STOP_LOSS", "SL"), ("TAKE_PROFIT", "TP"),
                                         ("INDICATOR_EXIT", "E"), ("DYNAMIC_PROTECTION", "DYNAMIC")])
def test_real_sell_progress_is_on_first_line_and_its_cause_is_on_third(reason, label):
    order = OrderIntent("sell", "HDB", "SELL", 400, "LO", action="CLOSE", status="PARTIAL",
                        filled_quantity=100, execution_mode="REAL", reason=reason)
    before = deepcopy(order.to_dict())
    lines, _ = render(pending_close=order, details={"normal_atr_pct": 2.32})
    assert "Khớp 100/400 CP" in lines[0] and "SELL KHỚP MỘT PHẦN" in lines[0]
    assert f"THOÁT DO {label}" in lines[2] and "SELL đang khớp một phần" in lines[2]
    assert order.to_dict() == before


def test_e_is_indicator_based_and_can_preempt_protect():
    lines, _ = render(params={"indicator_exit_policy": "AUTO"}, normal_state="DYN",
        decision={"signal": "SELL", "details": {"normal_atr_pct": 2.32, "normal_mfe_pct": 2.43,
                                               "protection_stage": "DYN", "normal_trigger_price": 23.15}})
    assert "E AUTO: ĐẠT" in lines[1] and "E CÓ TÍN HIỆU EMA/RSI" in lines[2]
    assert "E AUTO: MỐC" not in lines[1]


@pytest.mark.parametrize("selected", [False, True])
def test_indicator_alert_does_not_hide_an_actionable_protection_exit(selected):
    decision = {"signal": "SELL", "details": {"normal_atr_pct": 2.32, "normal_mfe_pct": 2.43,
                "protection_stage": "DYN", "normal_trigger_price": 23.15}}
    if selected:
        decision["event"] = "PROTECT_ALERT"
    lines, _ = render(normal_state="DYN", decision=decision,
                       params={"indicator_exit_policy": "AUTO" if selected else "ALERT"})
    assert lines[2].startswith("PROTECT DYNAMIC") and "Đã chạm 23,150" in lines[2]


@pytest.mark.parametrize("kind,expected", [
    ("history", "cần đo lại MAE/MFE"), ("atr", "Chờ ATR14"), ("stale", "Chờ đánh giá"),
    ("no_floor", "chưa chọn giữ lãi hoặc ATR trail"), ("entry", "Chờ giá vốn hợp lệ"),
])
def test_missing_inputs_explain_what_is_missing_without_inventing_an_active_floor(kind, expected):
    kw = {"details": {"normal_atr_pct": 2.32}}
    if kind == "history": kw["history_untrusted"] = True
    elif kind == "atr": kw["details"] = {}
    elif kind == "stale": kw["decision"] = {}
    elif kind == "entry": kw["entry"] = 0
    else: kw["params"] = {"normal_retention_enabled": False, "normal_atr_trail_enabled": False}
    lines, _ = render(**kw)
    assert expected in lines[2] and "Đã chạm" not in lines[2]


def test_a_computed_floor_is_explicitly_a_preview_until_the_daemon_reports_it():
    lines, _ = render(details={"normal_atr_pct": 2.32, "normal_mfe_pct": 2.43})
    assert "PREVIEW PROTECT" in lines[2] and "Chờ daemon xác nhận" in lines[2]
    assert "MỐC" not in lines[1] and "Đã chạm" not in lines[2]


def test_sl_hit_has_priority_over_e_and_protect_hits():
    lines, _ = render(sl_price=23.12, normal_state="DYN", params={"indicator_exit_policy": "AUTO"},
        decision={"signal": "SELL", "details": {"normal_trigger_price": 23.15, "normal_mfe_pct": 2.43,
                                               "protection_stage": "DYN", "normal_atr_pct": 2.32}})
    assert lines[2].startswith("SL ĐÃ CHẠM 23,120")


def test_quote_ahead_of_saved_peak_waits_for_confirmation_instead_of_claiming_dynamic_activation():
    lines, _ = render(entry=71.4, price=72.5, details={"normal_atr_pct": 2.48})
    assert "GIÁ ĐÃ TỚI START 72,374" in lines[2] and "Chờ daemon cập nhật đỉnh" in lines[2]
    assert "MỐC" not in lines[1]


def test_e_cannot_claim_a_signal_when_the_indicator_sample_is_not_ready():
    lines, _ = render(params={"indicator_exit_policy": "AUTO"},
        decision={"signal": "SELL", "details": {"normal_atr_pct": 2.32, "indicators": {"signal_ready": False}}})
    assert "E AUTO: CHỜ MẪU" in lines[1] and "E CÓ TÍN HIỆU" not in lines[2]


def test_start_disabled_requires_an_observed_gain_and_does_not_activate_at_entry():
    lines, _ = render(entry=100, price=100, sl_price=96.5, params={"normal_atr_activation_enabled": False},
                      metrics={"price_history_verified": True, "peak_profit_pct": 0})
    assert "DYN AUTO: CHỜ CÓ LÃI" in lines[1]
    assert "Cần đỉnh vượt giá vốn 100,000" in lines[2] and "GIÁ ĐÃ TỚI START" not in lines[2]


@pytest.mark.parametrize("kind", ["start", "dynamic_paused", "arm", "partial"])
def test_three_lines_fit_the_table_column_without_taller_rows(ui_root, kind):
    from tkinter import font as tkfont, ttk
    from support.tests.test_demo_order_display import table_subject, position, decision
    cycle, row = position("PAPER", price=23.1, em_modes=["NORMAL", "IND_EXIT"])
    cycle.avg_entry_price = row["costPrice"] = 22.65
    raw = decision("PAPER", state="DYN", trigger=23.046, normal_atr_pct=2.32,
                   normal_mfe_pct=(23.2 / 22.65 - 1) * 100, protection_stage="DYN")
    raw["signal"] = "HOLD"
    if kind == "start":
        raw["details"].update(normal_state="WAIT", normal_trigger_price=0, normal_mfe_pct=.42)
    elif kind == "dynamic_paused":
        cycle.auto_sell_enabled = False
    elif kind == "arm":
        raw["details"].update(normal_state="ARM", normal_trigger_price=23.985,
                              normal_mfe_pct=(24.6 / 22.65 - 1) * 100, protection_stage="ARM")
        row["marketPrice"] = 24.1
    items = []
    if kind == "partial":
        items.append(OrderIntent("sell", "MSN", "SELL", 400, "LO", action="CLOSE", status="PARTIAL",
                                 filled_quantity=100, execution_mode="PAPER", trade_id=cycle.id, reason="STOP_LOSS"))
    app, tree = table_subject(ui_root, "PAPER", positions=[row], cycles=[cycle], items=items)
    try:
        app.settings.rule_parameters.update(dynamic_policy="ALERT", indicator_exit_policy="AUTO")
        app._render_tables({"decisions_by_mode": {"PAPER": {"MSN": raw}}})
        lines = tree.set(tree.get_children()[0], "Status").splitlines()
        style = ttk.Style(tree)
        font = tkfont.Font(root=ui_root, font=style.lookup(tree.cget("style"), "font"))
        from viking_v2.dashboard.tables import _running_scale
        padding = round(16 * _running_scale(ui_root))
        available = int(tree.column("Status", "minwidth")) - padding
        assert len(lines) == 3
        assert all(font.measure(line) <= available for line in lines), (lines, available)
        assert int(style.lookup(tree.cget("style"), "rowheight")) < font.metrics("linespace") * 4
    finally:
        tree.destroy()
