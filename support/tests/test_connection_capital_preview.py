"""Connection controls and draft capital previews; no broker IO or real runtime."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from viking_v2 import config
from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.connections.window import ConnectionPopup
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import OrderIntent
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def preview(tmp_path, *, enabled=True, mode="PAPER"):
    settings = config.AppSettings.from_dict({
        "watchlist": ["MSN", "CTS"], "priority_symbols": ["MSN", "CTS"], "priority_capital_enabled": enabled,
        "priority_total_capital": 50_000_000,
        "priority_allocations": {
            "MSN": {"limit_vnd": 30_000_000, "use_pct": 50},
            "CTS": {"limit_vnd": 20_000_000, "use_pct": 100},
        },
        "market_phase_override_enabled": True, "market_phase_override_exposure_pct": 100,
        "buy_fee_pct": 0, "rule_parameters": {"max_positions": 4, "no_compound_enabled": False},
    })
    parent = DashboardActionsMixin()
    parent.mode, parent.settings = Value(mode), settings
    parent.snapshots = {
        "REAL": ({"equity": 100_000_000, "availableCash": 80_000_000}, [], []),
        "PAPER": ({"equity": 200_000_000, "availableCash": 190_000_000}, [], []),
    }
    parent.queue = OrderQueue(tmp_path / "orders.json")
    parent.trade_state = TradeStateStore(tmp_path / "trades.json")
    parent.rule_state = RuleStateStore(tmp_path / "rules.json")
    parent._book_preview_status = lambda: {}
    popup = ConnectionPopup.__new__(ConnectionPopup)
    popup.parent, popup.settings = parent, settings
    popup.priority_capital_enabled = Value(enabled)
    popup.priority_picker = Value(settings.priority_symbols)
    popup.priority_total = Value("50")
    popup._priority_allocations = deepcopy(settings.priority_allocations)
    return popup, parent


@pytest.mark.parametrize("mode,nav,cash", [("REAL", "100", "80"), ("PAPER", "200", "190")])
def test_private_pool_preview_shows_current_book_total_and_savings(tmp_path, mode, nav, cash):
    popup, parent = preview(tmp_path, mode=mode)
    before = asdict(parent.settings)
    overview, rows, invalid = popup._priority_preview_data()
    assert f"PREVIEW {mode} · TỔNG TÀI SẢN {nav} tr · TIỀN KHẢ DỤNG {cash} tr" in overview
    assert "PRIORITY 50 tr = ĐƯỢC MUA 35 tr + ĐỂ DÀNH 15 tr" in overview
    assert "HẠN MỨC ĐÃ CHIA 50 tr · CHƯA CHIA 0 tr" in overview
    assert rows == [
        ("MSN", "30 tr", "50%", "15 tr", "15 tr", "15 tr"),
        ("CTS", "20 tr", "100%", "20 tr", "0 tr", "20 tr"),
    ]
    assert not invalid
    assert asdict(parent.settings) == before
    assert parent.queue.list_all() == parent.trade_state.list_cycles() == []


@pytest.mark.parametrize("use_pct,buy,saved", [(50, 25, 25), (100, 50, 0)])
def test_va_50m_preview_is_group_total_not_25m_per_symbol(tmp_path, use_pct, buy, saved):
    popup, parent = preview(tmp_path)
    symbols = ["MSN", "CTS", "HDB", "IDC"]
    popup.priority_picker.value = symbols
    popup._priority_allocations = {
        symbol: {"limit_vnd": cap * 1_000_000, "use_pct": use_pct}
        for symbol, cap in zip(symbols, [15, 15, 5, 15])
    }
    parent.snapshots["PAPER"] = ({"equity": 99_973_842.9, "availableCash": 99_973_842.9}, [], [])
    before = asdict(parent.settings)
    overview, rows, invalid = popup._priority_preview_data()
    assert not invalid
    assert f"PRIORITY 50 tr = ĐƯỢC MUA {buy} tr + ĐỂ DÀNH {saved} tr" in overview
    assert "TÀI SẢN NGOÀI NGÂN SÁCH 49.974 tr" in overview
    assert len(overview.splitlines()) == 3
    assert rows[0] == ("MSN", "15 tr", f"{use_pct}%", "7.5 tr" if use_pct == 50 else "15 tr",
                       "7.5 tr" if use_pct == 50 else "0 tr", "7.5 tr" if use_pct == 50 else "15 tr")
    hint = popup._priority_preview_hint(rows, invalid)
    assert f"MSN: 15 tr × {use_pct}% = được mua " in hint
    assert f"HDB: 5 tr × {use_pct}% = được mua " in hint
    assert "P1 100% không tự đổi mức sử dụng 50%" in hint
    assert "không cộng thêm vào hạn mức" in hint
    assert asdict(parent.settings) == before
    assert parent.queue.list_all() == parent.trade_state.list_cycles() == []


def test_unallocated_pool_is_part_of_savings_not_extra_buying_power(tmp_path):
    popup, _parent = preview(tmp_path)
    popup.priority_total.value = "60"
    overview, rows, invalid = popup._priority_preview_data()
    assert not invalid
    assert "PRIORITY 60 tr = ĐƯỢC MUA 35 tr + ĐỂ DÀNH 25 tr" in overview
    assert "HẠN MỨC ĐÃ CHIA 50 tr · CHƯA CHIA 10 tr" in overview
    assert "phần không dùng + phần chưa chia" in popup._priority_preview_hint(rows, invalid)


def test_remaining_limit_deducts_holdings_and_pending_in_same_book(tmp_path):
    popup, parent = preview(tmp_path, mode="REAL")
    balance = parent.snapshots["REAL"][0]
    parent.snapshots["REAL"] = (balance, [{"symbol": "MSN", "quantity": 500,
                                         "costPrice": 20, "marketPrice": 25}], [])
    parent.queue.add(OrderIntent(id="real-pending", symbol="MSN", side="BUY", quantity=100,
                                order_type="LO", limit_price=20, execution_mode="REAL"))
    parent.queue.add(OrderIntent(id="paper-pending", symbol="MSN", side="BUY", quantity=400,
                                order_type="LO", limit_price=20, execution_mode="PAPER"))
    overview, rows, invalid = popup._priority_preview_data()
    assert rows[0][-1] == "3 tr"  # 15m limit - 10m held - 2m pending; other book excluded.
    assert "TIỀN KHẢ DỤNG 80 tr" in overview
    assert not invalid


def test_private_off_uses_original_p1_budget_ignoring_draft_limits(tmp_path):
    popup, parent = preview(tmp_path, enabled=False, mode="REAL")
    popup.priority_total.value = "not a number"
    popup._priority_allocations["MSN"] = {"limit_vnd": 1_000_000, "use_pct": 1}
    overview, rows, invalid = popup._priority_preview_data()
    assert "VỐN RIÊNG OFF" in overview
    assert "ĐƯỢC MUA" not in overview
    assert rows[0] == ("MSN", "25 tr", "100%", "25 tr", "0 tr", "25 tr")
    assert not invalid
    assert parent._preview_entry_checks("MSN", {})["order_budget"] == 25_000_000


def test_draft_total_and_limits_refresh_without_saving_or_placing_orders(tmp_path):
    popup, parent = preview(tmp_path)
    popup.priority_total.value = "60"
    popup._priority_allocations["MSN"]["limit_vnd"] = 40_000_000
    overview, rows, invalid = popup._priority_preview_data()
    assert "PRIORITY 60 tr = ĐƯỢC MUA 40 tr + ĐỂ DÀNH 20 tr" in overview
    assert "HẠN MỨC ĐÃ CHIA 60 tr" in overview
    assert rows[0][3] == "20 tr"
    assert parent.settings.priority_total_capital == 50_000_000
    assert parent.settings.priority_allocations["MSN"]["limit_vnd"] == 30_000_000
    assert parent.queue.list_all() == []
    assert not invalid


@pytest.mark.parametrize("total", ["", "nan", "inf", "-1", "40"])
def test_invalid_pool_is_marked_and_never_presents_remaining_as_spendable(tmp_path, total):
    popup, parent = preview(tmp_path)
    popup.priority_total.value = total
    parent._preview_entry_checks = lambda *_a, **_k: pytest.fail("Invalid draft must not size orders")
    overview, rows, invalid = popup._priority_preview_data()
    assert invalid and "QUỸ KHÔNG HỢP LỆ" in overview
    assert all(row[-1] == "—" for row in rows)
    assert parent.queue.list_all() == []


def test_preview_without_account_snapshot_does_not_invent_cash(tmp_path):
    popup, parent = preview(tmp_path)
    parent.snapshots = {}
    overview, rows, invalid = popup._priority_preview_data()
    assert "TỔNG TÀI SẢN — · TIỀN KHẢ DỤNG —" in overview
    assert "PRIORITY 50 tr" in overview
    assert rows[0][-1] == "—" and not invalid


@pytest.mark.parametrize("confirm,write_failure", [(False, False), (True, False), (True, True)])
def test_delete_api_is_confirmed_and_preserves_account_settings(
    tmp_path, ui_root, monkeypatch, confirm, write_failure,
):
    import viking_v2.connections.window as module
    private_path = tmp_path / ".env"
    values = {
        "DNSE_API_KEY": "test-key", "DNSE_API_SECRET": "test-secret",
        "DNSE_TRADING_TOKEN": "test-token", "DNSE_TRADING_TOKEN_EXPIRES_AT": "9999999999",
        "DNSE_ACCOUNT_NO": "TEST_ACCOUNT", "DNSE_STOCK_ACCOUNT_NO": "TEST_ACCOUNT",
        "DNSE_CUSTODY_CODE": "TEST_CUSTODY", "DNSE_OTP_TYPE": "email_otp",
        "TELEGRAM_BOT_TOKEN": "test-telegram",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config.update_env(values, path=private_path)
    original_file = private_path.read_bytes()
    client = DNSEClient(api_key="test-key", api_secret="test-secret", account_no="TEST_ACCOUNT")
    calls, writes = [], []

    def update(values):
        writes.append(values)
        if write_failure:
            raise OSError("test write error")
        config.update_env(values, path=private_path)

    monkeypatch.setattr(module, "update_env", update)
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *_a, **_k: confirm)
    settings = config.AppSettings()
    original_settings = asdict(settings)
    popup = ConnectionPopup(ui_root, settings, "TEST_ACCOUNT", client, lambda: None,
                            on_apply_account=calls.append)
    try:
        popup._clear_dnse()
        assert client.account_no == "TEST_ACCOUNT"
        assert asdict(settings) == original_settings
        if confirm and not write_failure:
            assert writes == [{key: None for key in (
                "DNSE_API_KEY", "DNSE_API_SECRET", "DNSE_TRADING_TOKEN", "DNSE_TRADING_TOKEN_EXPIRES_AT",
            )}]
            assert client.api_key == client.api_secret == client.trading_token == ""
            assert client.trading_token_expires_at == 0 and not client.connected
            assert popup.dnse_key.get() == popup.dnse_secret.get() == ""
            assert not popup.save_token_env.get()
            assert calls == ["TEST_ACCOUNT"]
            stored = private_path.read_text(encoding="utf-8")
            for key in values:
                if key not in writes[0]:
                    assert key in stored
        else:
            assert client.api_key == "test-key" and client.trading_token == "test-token"
            assert private_path.read_bytes() == original_file
            assert calls == []
            if write_failure:
                assert "XÓA THẤT BẠI" in popup.dnse_status.cget("text")
    finally:
        popup._close()
        client.close()


def test_priority_preview_widgets_update_without_rebuilding_unchanged_values(ui_root):
    client = DNSEClient(account_no="PAPER")
    popup = ConnectionPopup(ui_root, config.AppSettings(), "PAPER", client, lambda: None)
    try:
        initial = popup.priority_preview
        popup._refresh_priority_summary()
        assert popup.priority_preview is initial
        popup.priority_capital_enabled.set(True)
        popup.priority_total.delete(0, "end")
        popup.priority_total.insert(0, "50")
        popup._refresh_priority_summary()
        assert "PRIORITY 50 tr" in popup.priority_preview.cget("text")
        assert "CHƯA CHIA 50 tr" in popup.priority_preview.cget("text")
    finally:
        popup._close()
        client.close()


def test_priority_preview_and_money_headings_have_specific_hints(ui_root, monkeypatch):
    import viking_v2.connections.window as module
    attached = []
    real_hint = module._HoverHint

    def capture(widget, text, *args, **kwargs):
        attached.append((widget, text))
        return real_hint(widget, text, *args, **kwargs)

    monkeypatch.setattr(module, "_HoverHint", capture)
    client = DNSEClient(account_no="PAPER")
    popup = ConnectionPopup(ui_root, config.AppSettings.from_dict({
        "watchlist": ["MSN"], "priority_symbols": ["MSN"],
        "priority_capital_enabled": True, "priority_total_capital": 15_000_000,
        "priority_allocations": {"MSN": {"limit_vnd": 15_000_000, "use_pct": 50}},
    }), "PAPER", client, lambda: None)
    try:
        assert any(widget is popup.priority_preview and "MSN: 15 tr × 50%" in text
                   for widget, text in attached)
        heading_hints = {widget.cget("text"): text for widget, text in attached
                         if widget.master is popup.priority_summary and widget is not popup.priority_preview}
        assert "15 triệu × 50% = 7,5 triệu" in heading_hints["ĐƯỢC MUA"]
        assert "không phải % của cả tài khoản" in heading_hints["DÙNG (%)"]
        assert "không phải tiền khả dụng" in heading_hints["CÒN HẠN MỨC"]
    finally:
        popup._close()
        client.close()


@pytest.mark.parametrize("scale,width", [(1.0, 760), (1.0, 1080), (1.25, 760), (1.25, 1080)])
def test_va_priority_summary_stays_three_lines_at_supported_widths(ui_root, monkeypatch, scale, width):
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import font as tkfont
    symbols = ["MSN", "CTS", "HDB", "IDC"]
    settings = config.AppSettings.from_dict({
        "watchlist": symbols, "priority_symbols": symbols,
        "priority_capital_enabled": True, "priority_total_capital": 50_000_000,
        "priority_allocations": {symbol: {"limit_vnd": cap * 1_000_000, "use_pct": 50}
                                 for symbol, cap in zip(symbols, [15, 15, 5, 15])},
    })
    monkeypatch.setattr(ui_root, "mode", Value("PAPER"), raising=False)
    monkeypatch.setattr(ui_root, "snapshots", {
        "PAPER": ({"equity": 99_973_842.9, "availableCash": 99_973_842.9}, [], []),
    }, raising=False)
    client = DNSEClient(account_no="PAPER")
    ctk.set_widget_scaling(scale)
    popup = ConnectionPopup(ui_root, settings, "PAPER", client, lambda: None)
    try:
        popup.tabs.set("MÃ CK")
        popup.top.geometry(f"{int(width * scale)}x{int(720 * scale)}")
        settled = tk.BooleanVar(master=ui_root, value=False)
        ui_root.after(220, lambda: settled.set(True))
        ui_root.wait_variable(settled)
        ui_root.update_idletasks()
        label = popup.priority_preview
        lines = label.cget("text").splitlines()
        assert len(lines) == 3
        font = tkfont.Font(root=ui_root, font=label._label.cget("font"))
        assert max(font.measure(line) for line in lines) <= label.winfo_width()
        assert label._label.winfo_reqheight() < 4 * font.metrics("linespace")
        import os
        if os.getenv("VIKING_CAPTURE_UI") == "1" and scale == 1.0 and width == 1080:
            from pathlib import Path
            from PIL import ImageGrab
            path = Path(__file__).resolve().parents[2] / ".artifacts" / "ui-review" / "priority-money-clear.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            box = popup.priority_summary
            x, y = box.winfo_rootx(), box.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + box.winfo_width(), y + box.winfo_height()), all_screens=True).save(path)
    finally:
        popup._close()
        client.close()
        ctk.set_widget_scaling(1.0)
