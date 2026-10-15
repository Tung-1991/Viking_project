"""Visible column rules and status explain snapshots without changing orders."""
from copy import deepcopy
from datetime import datetime
from tkinter import ttk
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from viking_v2.dashboard.position_status import position_status, settlement_note
from viking_v2.dashboard.tables import RUNNING_WIDTHS
from viking_v2.models import OrderIntent, TradeCycle
from viking_v2.trading.market import VN_TZ
from support.tests.test_demo_order_display import table_subject


def status(**changes):
    args = dict(symbol="HDB", mode="REAL", quantity=400, sellable=0, settle="2026-10-15",
                price=23, entry=22.65, sl_enabled=True, sl_price=21.85725, tp_price=0, tp_pct=0,
                params={"normal_dynamic_enabled": True}, decision={}, normal_enabled=True,
                indicator_enabled=True, paused=False, protect_mode="AUTO", normal_state="WAIT",
                history_untrusted=False, now=datetime(2026, 10, 15, 11, 40, tzinfo=VN_TZ))
    args.update(changes)
    before = deepcopy(args)
    result = position_status(**args)
    assert args == before
    return result


def test_real_date_only_does_not_invent_a_settlement_hour_or_release_stock():
    text, hint = status()
    assert "BÁN ĐƯỢC 0 CP" in text and "CHỜ VỀ 400 CP" in text
    assert "15/10" in text and "CHỜ DNSE XÁC NHẬN" in text
    assert "13:00" not in text + hint
    assert "Chờ đánh giá vị thế mới từ daemon" in text
    assert "Chưa có yêu cầu SELL" in hint and "SL 21,857" in hint
    assert "ACCUMULATION" not in hint and "WAIT" not in hint


@pytest.mark.parametrize("mode,settle,expected", [
    ("PAPER", "2026-10-15", "CÒN 1g20p"),
    ("REAL", "2026-10-15T12:00:00+07:00", "CÒN 20p"),
    ("REAL", "2026-10-15T11:00:00+07:00", "CHỜ CẬP NHẬT"),
    ("REAL", "", "Chưa có lịch về"),
])
def test_countdown_uses_only_the_paper_schedule_or_a_provided_api_time(mode, settle, expected):
    text, hint = status(mode=mode, settle=settle)
    assert expected in text and "BÁN ĐƯỢC 0 CP" in text
    assert len(text.splitlines()) == 3


@pytest.mark.parametrize("details,unverified,expected", [
    ({"normal_atr_pct": 0}, False, "Chờ ATR14 từ nến ngày đã đóng"),
    ({"normal_atr_pct": 2.32, "normal_mfe_pct": .42, "normal_activation_mfe_pct": 1.276}, False, "Chưa START"),
    ({"normal_atr_pct": 2.32}, True, "cần đo lại MAE/MFE"),
])
def test_protection_replaces_generic_wait_with_the_specific_missing_condition(details, unverified, expected):
    text, _ = status(decision={"details": details}, history_untrusted=unverified)
    assert expected in text and "CHỜ DỮ LIỆU" not in text


@pytest.mark.parametrize("paused", [True, False])
def test_a_signal_without_a_sell_request_never_claims_sent_or_filled(paused):
    details = {"normal_trigger_price": 23.046, "protection_stage": "DYN", "normal_mfe_pct": 2.43}
    text, hint = status(decision={"details": details}, normal_state="DYN", paused=paused,
                        protect_mode="ALERT" if paused else "AUTO")
    assert "chưa tạo SELL" in text and "Đã chạm 23,046" in text
    assert "đã gửi" not in text and "đã khớp" not in text
    assert ("TỰ BÁN TẠM DỪNG" in text) is paused


def test_real_pending_sell_shows_progress_instead_of_a_signal_only_status():
    order = OrderIntent("partial", "HDB", "SELL", 400, "LO", action="CLOSE",
                        execution_mode="REAL", status="PARTIAL", filled_quantity=100)
    before = order.to_dict()
    text, hint = status(sellable=400, pending_close=order)
    assert "Khớp 100/400 CP" in text and "SELL đang khớp một phần" in text
    assert "Chưa có yêu cầu SELL" not in hint and order.to_dict() == before


def test_stt_parent_numbers_and_rules_follow_scroll_without_changing_selection_or_orders(ui_root):
    top = ctk.CTkToplevel(ui_root)
    top.transient("")
    top.geometry("1150x270")
    first, second = TradeCycle("A", "HDB", execution_mode="REAL"), TradeCycle("B", "MSN", execution_mode="REAL")
    for cycle in (first, second):
        cycle.record_buy_fill(100, 23)
    rows = [{"symbol": cycle.symbol, "tradeId": cycle.id, "openQuantity": 100, "tradeQuantity": 0,
             "costPrice": 23, "marketPrice": 23.1} for cycle in (first, second)]
    order = OrderIntent("child", "HDB", "BUY", 100, "LO", execution_mode="REAL", trade_id=first.id, limit_price=23)
    app, tree = table_subject(top, positions=rows, cycles=[first, second], items=[order])
    tree.pack(fill="both", expand=True)
    before = first.to_dict(), second.to_dict(), order.to_dict()
    try:
        app._render_tables({})
        roots = tree.get_children()
        assert tree["displaycolumns"][0] == "STT"
        assert [tree.set(iid, "STT") for iid in roots] == ["1", "2"]
        assert tree.set("LOCAL:child", "STT") == "1.1"
        tree.selection_set(roots[0])
        top.deiconify()
        top.update()
        cells = tree._viking_table_cells
        cells.draw()
        top.update_idletasks()
        order_label, fee_label = cells.labels[(roots[0], "Order")], cells.labels[(roots[0], "CostInfo")]
        assert order_label.winfo_y() == fee_label.winfo_y()
        assert order_label.cget("anchor") == fee_label.cget("anchor") == "nw"
        assert int(order_label.cget("pady")) == int(fee_label.cget("pady"))
        assert RUNNING_WIDTHS["Status"] > max(value for key, value in RUNNING_WIDTHS.items() if key != "Status")
        cells.forward(SimpleNamespace(x=5, y=5, state=0), "<Button-1>", fee_label)
        assert tree.selection() == (roots[0],)
        for fraction in (0., .35):
            tree.xview_moveto(fraction)
            top.update_idletasks()
            cells.draw()
            for column, rule in cells.rules.items():
                x, _, width, _ = tree.bbox(roots[0], column)
                assert rule.winfo_x() == x + width - 1
                assert rule.winfo_height() == tree.winfo_height()
        rows.reverse()
        app.snapshots["REAL"] = ({}, rows, [])
        app._render_tables({})
        assert tree.selection() == (roots[0],)
        assert tree.set(roots[0], "STT") == "2" and tree.set("LOCAL:child", "STT") == "2.1"
        assert (first.to_dict(), second.to_dict(), order.to_dict()) == before
    finally:
        top.destroy()
