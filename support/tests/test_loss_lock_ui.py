"""Native loss-lock controls, with isolated state and no broker/Telegram calls."""
from __future__ import annotations

import customtkinter as ctk
import pytest

from viking_v2.config import AppSettings, load_settings
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.trading.state import TradeStateStore


@pytest.fixture
def make_rule(ui_root, tmp_path):
    popups = []
    sequence = 0

    def create(*, mode="TIMED", paper=True, state=True):
        nonlocal sequence
        sequence += 1
        settings = AppSettings(paper_mode=paper, rule_parameters={
            "loss_lock_count": 3, "loss_lock_hours": 24, "loss_lock_mode": mode,
        }).normalize()
        trades = TradeStateStore(tmp_path / f"trades-{sequence}.json",
                                 loss_lock_policy=lambda: (3, "BLOCK")) if state else None
        rule = RuleSettingsPopup(ui_root, settings, f"LOSS_UI_{sequence}", lambda: None,
                                 trade_state=trades)
        popups.append(rule)
        return rule

    yield create
    for rule in popups:
        rule._close()


def _block(trades, symbol, mode):
    for _ in range(3):
        cycle = trades.create(symbol, mode)
        trades.record_buy_fill(cycle.id, 100, 20)
        trades.record_sell_fill(cycle.id, 100, 19)


@pytest.mark.parametrize("mode", ["TIMED", "BLOCK"])
@pytest.mark.parametrize("state", [True, False])
def test_loss_modes_construct_sync_and_save_without_changing_thresholds(make_rule, mode, state):
    rule = make_rule(mode=mode, state=state)
    assert rule.loss_lock_mode_selector.get() == ("KHÓA HẲN" if mode == "BLOCK" else "THEO GIỜ")
    assert rule.loss_lock_hours.cget("state") == ("disabled" if mode == "BLOCK" else "normal")
    assert rule.loss_lock_hours.master.winfo_manager() == ("" if mode == "BLOCK" else "pack")

    for choice, expected in [("KHÓA HẲN", "BLOCK"), ("THEO GIỜ", "TIMED")]:
        # Invoke the native segmented control's user callback, not only the BooleanVar.
        rule.loss_lock_mode_selector.set(choice, from_button_callback=True)
        assert rule.loss_block.get() == (expected == "BLOCK")
        assert rule.loss_lock_hours.cget("state") == ("disabled" if expected == "BLOCK" else "normal")
        assert rule.loss_lock_hours.master.winfo_manager() == ("" if expected == "BLOCK" else "pack")
        assert rule.loss_lock_hours.get() == "24"
        rule.save()
        assert "ĐÃ LƯU" in rule.status.cget("text")
        saved = load_settings(rule.account_id)
        assert saved.rule_parameters["loss_lock_mode"] == expected
        assert saved.rule_parameters["loss_lock_count"] == 3
        assert saved.rule_parameters["loss_lock_hours"] == 24

    rule.loss_block.set(True)
    assert rule.loss_lock_mode_selector.get() == "KHÓA HẲN"


def test_empty_book_and_existing_manual_locks_remain_clear_in_timed_mode(make_rule, monkeypatch):
    rule = make_rule()
    trades = rule.trade_state
    assert rule.block_symbol.get() == "Không có mã"
    assert rule.block_symbol.cget("state") == "disabled"
    assert rule.block_unlock.cget("text") == "CHƯA CÓ MÃ KHÓA TAY"
    assert rule.block_unlock.cget("state") == "disabled"
    assert rule.loss_unlock_row.winfo_manager() == ""
    monkeypatch.setattr("viking_v2.rules.window.messagebox.askyesno",
                        lambda *_a, **_kw: pytest.fail("Empty book must not prompt"))
    rule._unlock_block()

    _block(trades, "FPT", "PAPER")
    _block(trades, "SSI", "PAPER")
    _block(trades, "MSN", "REAL")
    rule._refresh_blocks()
    assert rule.block_symbol.cget("values") == ["FPT", "SSI"]
    assert rule.block_symbol.cget("state") == "normal"
    assert rule.block_unlock.cget("text") == "MỞ KHÓA MÃ"
    assert rule.block_unlock.cget("state") == "normal"
    assert rule.loss_unlock_row.winfo_manager() == "pack"
    rule.block_symbol.set("SSI")
    rule._refresh_blocks()
    assert rule.block_symbol.get() == "SSI"
    rule.loss_block.set(True)
    rule.loss_block.set(False)
    assert trades.loss_blocks("PAPER") == ["FPT", "SSI"]

    rule.block_book.set("REAL")
    rule._refresh_blocks()
    assert rule.block_symbol.get() == "MSN"
    assert rule.block_symbol.cget("values") == ["MSN"]

    # The other book's locks keep its selector reachable when this book is empty.
    trades.unlock_loss_block("MSN", "REAL")
    rule._refresh_blocks()
    assert rule.block_unlock.cget("state") == "disabled"
    assert rule.loss_unlock_row.winfo_manager() == "pack"


def test_timed_cooldown_is_not_mislabeled_as_manual_lock(make_rule):
    rule = make_rule()
    trades = rule.trade_state
    trades.loss_lock_policy = lambda: (3, "TIMED")
    _block(trades, "FPT", "PAPER")
    assert trades.is_loss_locked("FPT", "PAPER", lock_mode="TIMED")
    rule._refresh_blocks()
    assert rule.block_symbol.get() == "Không có mã"
    assert rule.block_unlock.cget("state") == "disabled"
    assert rule.loss_unlock_row.winfo_manager() == ""


def test_permanent_mode_does_not_validate_hidden_duration(make_rule):
    rule = make_rule()
    rule.loss_lock_hours.delete(0, "end")
    rule.loss_lock_hours.insert(0, "invalid")
    rule.loss_lock_mode_selector.set("KHÓA HẲN", from_button_callback=True)
    rule.save()
    saved = load_settings(rule.account_id)
    assert "ĐÃ LƯU" in rule.status.cget("text")
    assert saved.rule_parameters["loss_lock_mode"] == "BLOCK"
    assert saved.rule_parameters["loss_lock_hours"] == 24
    rule.loss_lock_mode_selector.set("THEO GIỜ", from_button_callback=True)
    rule.save()
    assert "KHÔNG THỂ LƯU" in rule.status.cget("text")
    assert load_settings(rule.account_id).rule_parameters["loss_lock_mode"] == "BLOCK"


def test_unlock_cancel_success_and_stale_book_selection(make_rule, monkeypatch):
    rule = make_rule(mode="BLOCK")
    trades = rule.trade_state
    _block(trades, "FPT", "PAPER")
    _block(trades, "FPT", "REAL")
    rule._refresh_blocks()
    monkeypatch.setattr("viking_v2.rules.window.messagebox.askyesno", lambda *_a, **_kw: False)
    rule._unlock_block()
    assert trades.loss_blocks("PAPER") == ["FPT"]
    assert trades.loss_streak("FPT", "PAPER") == 3

    monkeypatch.setattr("viking_v2.rules.window.messagebox.askyesno", lambda *_a, **_kw: True)
    rule._unlock_block()
    assert trades.loss_blocks("PAPER") == []
    assert trades.loss_streak("FPT", "PAPER") == 0
    assert trades.loss_blocks("REAL") == ["FPT"]
    assert rule.block_symbol.get() == "Không có mã"
    assert rule.block_unlock.cget("state") == "disabled"
    assert "ĐÃ MỞ KHÓA · FPT · PAPER" == rule.status.cget("text")

    # A selection left over from another book must not unlock that book's symbol.
    rule.block_symbol.set("FPT")
    monkeypatch.setattr("viking_v2.rules.window.messagebox.askyesno",
                        lambda *_a, **_kw: pytest.fail("Stale selection must not prompt"))
    rule._unlock_block()
    assert trades.loss_blocks("REAL") == ["FPT"]
    assert rule.block_symbol.get() == "Không có mã"


def test_lock_removed_while_confirmation_open_does_not_claim_success(make_rule, monkeypatch):
    rule = make_rule()
    trades = rule.trade_state
    _block(trades, "FPT", "PAPER")
    rule._refresh_blocks()

    def confirm(*_a, **_kw):
        trades.unlock_loss_block("FPT", "PAPER")
        return True

    monkeypatch.setattr("viking_v2.rules.window.messagebox.askyesno", confirm)
    rule._unlock_block()
    assert rule.status.cget("text") == "KHÓA ĐÃ THAY ĐỔI · FPT · PAPER"
    assert rule.block_unlock.cget("state") == "disabled"


@pytest.mark.parametrize("scaling", [1.0, 1.25, 1.5])
@pytest.mark.parametrize("width", [900, 1080])
def test_loss_card_controls_stay_inside_card_at_small_width_and_dpi(make_rule, ui_root, scaling, width):
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    try:
        rule = make_rule()
        rule.top.geometry(f"{width}x720+0+0")
        rule.tabs.set("NGHIỆP VỤ")
        rule.phase_tabs.set("PHASE 3 · VỐN & BẢO VỆ")
        ui_root.update()
        ui_root.update_idletasks()
        card = rule.loss_lock.master.master
        # Each native control and hint must remain within its row/card. No new rows.
        rows = [child for child in card.winfo_children() if isinstance(child, ctk.CTkFrame)]
        assert len(rows) == 5
        for row in rows:
            if not row.winfo_manager():
                continue
            assert row.winfo_x() + row.winfo_width() <= card.winfo_width() + 2
            for child in row.winfo_children():
                if isinstance(child, (ctk.CTkLabel, ctk.CTkEntry, ctk.CTkOptionMenu,
                                      ctk.CTkButton, ctk.CTkSegmentedButton)):
                    assert child.winfo_x() >= -2
                    assert child.winfo_x() + child.winfo_width() <= row.winfo_width() + 2
        assert rule.loss_unlock_row.winfo_manager() == ""
    finally:
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
