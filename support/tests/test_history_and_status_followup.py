"""Forward calendars must not block history; position previews stay read-only."""
from copy import deepcopy
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from support.tests.test_demo_order_display import table_subject, position, decision
from support.tests.test_rule_capital_preview import subject as preview_subject
from support.tests.test_position_history import HistoryClient, NOW, historical_account, payload, stamp
from viking_v2.dashboard.hint_box import HINT_SIZE, COMPACT_HINT_SIZE
from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.dashboard.position_status import preview_position_status
from viking_v2.dashboard.tables import RUNNING_COLUMNS
from viking_v2.dashboard.windows import _HoverHint
from viking_v2.models import OrderIntent
from viking_v2.trading.position_history import load_position_history


@pytest.mark.parametrize("calendar", [["2026-10-14", "2026-10-15", "2027-01-04"], []])
def test_forward_only_or_empty_calendar_recovers_three_old_sessions_from_index(tmp_path, calendar):
    trades, rules, _, cycle = historical_account(tmp_path)
    client = HistoryClient()
    client.calendar = calendar
    before = cycle.to_dict(), rules.position_metrics(cycle.symbol, cycle.id)
    history = load_position_history(client, cycle, now=NOW)
    assert history["session_dates"] == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert history["high"] == 24 and history["low"] == 21
    assert client.calls[0][:2] == ("VNINDEX", "1D")
    assert len([call for call in client.calls if call[1] == "1"]) == 3
    assert (trades.get(cycle.id).to_dict(), rules.position_metrics(cycle.symbol, cycle.id)) == before


def test_historical_calendar_uses_market_sessions_not_a_suspended_stocks_dates(tmp_path):
    _, _, _, cycle = historical_account(tmp_path, entered=stamp(8, "10:00:00"))
    client = HistoryClient()
    client.calendar = ["2026-10-14", "2026-10-15"]
    client.minutes[13] = payload([])
    with pytest.raises(ValueError, match="Thiếu nến phút phiên 13/10"):
        load_position_history(client, cycle, now=NOW)
    client.minutes[13] = payload([(stamp(13, "09:30:00"), 23, 24, 21, 23, 100)])
    history = load_position_history(client, cycle, now=NOW, holidays=["2026-10-13"])
    assert history["session_dates"] == ["2026-10-09", "2026-10-12", "2026-10-14"]


def test_three_session_request_for_a_new_purchase_never_imports_pre_entry_prices(tmp_path):
    _, _, _, cycle = historical_account(tmp_path, entered=stamp(14, "09:30:35"))
    client = HistoryClient()
    client.calendar = ["2026-10-14", "2026-10-15"]
    client.minutes[14] = payload([(stamp(14, "09:31:00"), 23, 23.5, 22.5, 23, 100)])
    result = load_position_history(client, cycle, now=NOW)
    assert result["session_dates"] == ["2026-10-14"] and result["loaded_sessions"] == 1
    assert result["high"] == 23.5 and result["low"] == 22.5
    assert all(call[0] != "VNINDEX" for call in client.calls)


@pytest.mark.parametrize("tab", ["TỔNG QUAN", "HỆ THỐNG"])
def test_preview_renders_holdings_and_current_sell_progress_instead_of_only_buy_reason(ui_root, tab):
    top = ctk.CTkToplevel(ui_root)
    top.geometry("1000x460+0+0")
    view = preview_subject("PAPER")
    cycle, row = position("PAPER", price=10.1, em_modes=["NORMAL", "IND_EXIT"])
    row.update(tradeQuantity=0, settleDate="2026-10-15")
    view.snapshots["PAPER"] = ({}, [row], [])
    view.trade_state = SimpleNamespace(active_for=lambda *_: cycle, get=lambda *_: cycle)
    view.rule_state.position_metrics = lambda *_: {
        "price_history_verified": True, "market_data_policy": "G1_MATCH_V1", "peak_profit_pct": .42}
    orders = []
    view.queue = SimpleNamespace(list_all=lambda: orders)
    view._em_states = {"normal_protection": True, "indicator_exit": True}
    raw = decision("PAPER", state="WAIT", normal_atr_pct=2.32, normal_trigger_price=0)
    raw.update(signal="HOLD", action="WAIT", reason="NO_NEW_BUY_SIGNAL")
    state = {"decisions_by_mode": {"PAPER": {"MSN": raw}}}
    view._preview_indicator_details = lambda *_: raw["details"]
    try:
        view._build_order_preview_tab(top)
        view._select_preview_tab(tab)
        view._refresh_rule_preview(state, "MSN")
        top.update()
        assert "Chờ 100 CP" in view.preview_rule_reason.cget("text")
        assert "Chưa START" in view.preview_rule_reason.cget("text")
        assert "SL" in view._rule_decision_hint() and "Chưa có yêu cầu SELL" in view._rule_decision_hint()
        assert view.preview_rule_reason.winfo_rooty() + view.preview_rule_reason.winfo_height() <= view.preview_health_group.winfo_rooty()
        orders.append(OrderIntent("sell", "MSN", "SELL", 100, "LO", action="CLOSE", execution_mode="PAPER",
                                  trade_id=cycle.id, status="PARTIAL", filled_quantity=25))
        view._refresh_rule_preview(state, "MSN")
        top.update()
        assert "Khớp 25/100 CP" in view.preview_rule_reason.cget("text")
        assert "SELL đang khớp một phần" in view._rule_decision_hint()
        # A ticker switch must remove the previous position's hint.
        view._refresh_rule_preview(state, "AAA")
        assert not view._preview_position_status
        assert "GIỮ 100 CP" not in view._rule_decision_hint()
    finally:
        top.destroy()


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("state", ["settling", "partial", "paused", "stale", "mfe_fallback"])
def test_preview_and_table_share_position_status_without_changing_fills_or_buy_slots(ui_root, mode, state):
    cycle, row = position(mode, price=10.1, em_modes=["NORMAL", "IND_EXIT"])
    raw = decision(mode, state="WAIT", normal_atr_pct=2.32, normal_trigger_price=0)
    raw["signal"] = "HOLD"
    if state == "settling":
        row.update(tradeQuantity=0, settleDate="2026-10-15")
    elif state == "paused":
        cycle.auto_sell_enabled = False
    elif state == "stale":
        raw["details"]["updated_at"] = 1
    items = []
    if state == "partial":
        items.append(OrderIntent("sell", "MSN", "SELL", 100, "LO", action="CLOSE", execution_mode=mode,
                                 trade_id=cycle.id, status="PARTIAL", filled_quantity=25))
    app, tree = table_subject(ui_root, mode, positions=[row], cycles=[cycle], items=items)
    app.mode = SimpleNamespace(get=lambda: mode)
    app.trade_state.active_for = lambda symbol, book: cycle if (symbol, book) == (cycle.symbol, mode) else None
    app.trade_state.get = lambda identifier: cycle if identifier == cycle.id else None
    metrics = {"price_history_verified": True, "market_data_policy": "G1_MATCH_V1", "peak_profit_pct": .42}
    app.rule_state.position_metrics = lambda *_: metrics
    other = "REAL" if mode == "PAPER" else "PAPER"
    status = {"decisions_by_mode": {mode: {"MSN": raw}, other: {"MSN": decision(other, state="DYN")}}}
    before = deepcopy((cycle.to_dict(), row, metrics, status, [item.to_dict() for item in items]))
    try:
        app._render_tables(status)
        result = preview_position_status(app, "MSN", status)
        iid, = tree.get_children()
        assert result[0] == tree.set(iid, "Status")
        assert result[1] == tree._viking_row_hints[iid]["Status"]
        if state == "partial":
            assert "Khớp 25/100 CP" in result[0]
        elif state == "settling":
            assert "CHỜ VỀ 100 CP" in result[0]
        elif state == "paused":
            assert "TỰ BÁN TẠM DỪNG" in result[0]
        elif state == "stale":
            assert "Chờ đánh giá" in result[0] and "Đã chạm" not in result[0]
        else:
            assert "MFE +0.42%" in result[0] and "Chưa có đỉnh" not in result[0]
        assert preview_position_status(app, "AAA", status) is None
        assert (cycle.to_dict(), row, metrics, status, [item.to_dict() for item in items]) == before
    finally:
        tree.destroy()


@pytest.mark.parametrize("scale", [1., 1.5, 2.])
def test_hints_fit_short_text_keep_details_width_and_scroll_long_content(ui_root, scale):
    ctk.set_widget_scaling(scale)
    top = ctk.CTkToplevel(ui_root)
    top.geometry(f"{round(760 * scale)}x{round(430 * scale)}+0+0")
    top.transient("")
    label = ctk.CTkLabel(top, text="Trạng thái")
    label.pack()
    app, tree = table_subject(top)
    tree.pack(fill="both", expand=True)
    app._configure_tree(tree, RUNNING_COLUMNS)
    hint = tree._viking_column_hint
    short, long = "SL BẬT · TP TẮT", "\n".join(f"Dòng {n}: Chi tiết trạng thái vị thế và tiến độ SELL." for n in range(70))
    tree._viking_row_hints = {"row": {"Status": short}}
    tree.insert("", "end", iid="row")
    try:
        top.update()
        hint.target = ("row", "Status")
        hint.show(hint.target, 20, 45)
        top.update()
        native_scale = ctk.ScalingTracker.get_widget_scaling(top)
        expected = (round(HINT_SIZE[0] * native_scale), round(HINT_SIZE[1] * native_scale))
        short_height = hint.popup.winfo_height()
        assert hint.popup.winfo_width() == expected[0]
        assert short_height < expected[1] / 2
        assert float(hint.popup.body.yview()[1]) == 1
        hint.hide()
        tree._viking_row_hints["row"]["CostInfo"] = short
        hint.target = ("row", "CostInfo")
        hint.show(hint.target, 20, 45)
        top.update()
        assert hint.popup.winfo_width() <= round(COMPACT_HINT_SIZE[0] * native_scale)
        assert hint.popup.winfo_height() <= short_height
        assert float(hint.popup.body.yview()[1]) == 1
        hint.hide()
        tree._viking_row_hints["row"]["Status"] = long
        hint.target = ("row", "Status")
        hint.show(hint.target, 20, 45)
        top.update()
        assert (hint.popup.winfo_width(), hint.popup.winfo_height()) == expected
        body = hint.popup.body
        assert body.cget("state") == "disabled" and float(body.yview()[1]) < 1
        body.yview_moveto(1)
        assert float(body.yview()[0]) > 0
        assert "Dòng 69" in body.get("1.0", "end")
        hint.hide()
        preview = _HoverHint(label, long, placement="inside", fixed_size=True)
        preview._show()
        top.update()
        assert (preview.popup.winfo_width(), preview.popup.winfo_height()) == expected
        assert preview.popup.winfo_x() + preview.popup.winfo_width() <= top.winfo_width()
        assert preview.popup.winfo_y() + preview.popup.winfo_height() <= top.winfo_height()
        preview._hide()
    finally:
        top.destroy()
        ctk.set_widget_scaling(1.)
