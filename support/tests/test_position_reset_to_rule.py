"""Reset is a draft until SAVE; fills and price observations survive it."""
import customtkinter as ctk
import pytest

from viking_v2.config import AppSettings
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.models import OrderIntent
from viking_v2.trading.position_controls import position_override_items
from support.tests.test_demo_order_display import _widgets
from support.tests.test_position_repair_and_tplus_alerts import account, g1


def prepare(root, monkeypatch, tmp_path, settings):
    trades, rules, queue, cycle = account(tmp_path)
    trades.update_management(cycle.id, em_modes=[], auto_sell_enabled=False, sl_enabled=False,
        sl_mode="PRICE", sl_value=20, tp_mode="PERCENT", tp_value=12,
        exit_rule_overrides={"normal_policy": "ALERT", "normal_arm_enabled": False,
                             "dynamic_policy": "AUTO", "normal_dynamic_enabled": False,
                             "indicator_exit_policy": "AUTO"})
    other = trades.create("FPT", "REAL", em_modes=["NORMAL"])
    trades.record_buy_fill(other.id, 100, 100)
    manual = queue.add(OrderIntent.create("HDB", "SELL", 100, "LO", limit_price=25,
                                         execution_mode="REAL", trade_id=cycle.id, source="MANUAL"))
    for key, value in {"trade_state": trades, "rule_state": rules, "queue": queue, "settings": settings,
                       "_shared_tick": lambda *_a: g1(), "_row_time": lambda *_a: "14:09",
                       "_log": lambda *_a: None, "_refresh_local": lambda: None}.items():
        monkeypatch.setattr(root, key, value, raising=False)
    return trades, rules, queue, trades.get(cycle.id), trades.get(other.id), manual


def popup(root, cycle):
    previous = set(root.winfo_children())
    DashboardActionsMixin._show_position_management(root, {"symbol": "HDB", "mode": "REAL", "trade_id": cycle.id,
        "position": {"openQuantity": 400, "tradeQuantity": 400, "costPrice": 22.65, "marketPrice": 23.1}})
    top, = [w for w in root.winfo_children() if w not in previous and isinstance(w, ctk.CTkToplevel)]
    return top


@pytest.mark.parametrize("modes,sl,dynamic", [(["NORMAL", "IND_EXIT"], True, True),
                                             ([], False, False), (["TP", "IND_EXIT"], True, False),
                                             (["TP", "NORMAL"], False, False)])
@pytest.mark.parametrize("scale", [1., 1.5])
def test_reset_uses_current_rule_and_removes_overrides_only_when_saved(ui_root, monkeypatch, tmp_path, modes, sl, dynamic, scale):
    settings = AppSettings(bot_em_modes=modes, bot_sl_enabled=sl,
        rule_parameters={"take_profit_pct": 9, "initial_sl_pct": -4, "normal_policy": "AUTO",
                         "dynamic_policy": "ALERT", "indicator_exit_policy": "ALERT", "normal_dynamic_enabled": dynamic})
    trades, rules, queue, cycle, other, manual = prepare(ui_root, monkeypatch, tmp_path, settings)
    before, history, pending = cycle.to_dict(), rules.position_metrics(cycle.symbol, cycle.id), manual.to_dict()
    saved_settings = settings.to_dict()
    ctk.set_widget_scaling(scale)
    ctk.set_window_scaling(scale)
    top = popup(ui_root, cycle)
    try:
        top.transient("")
        top.deiconify()
        ui_root.update_idletasks()
        widgets = list(_widgets(top))
        reset = next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "VỀ RULE")
        reset.invoke()
        assert trades.get(cycle.id).to_dict() == before
        assert rules.position_metrics(cycle.symbol, cycle.id) == history and queue.get(manual.id).to_dict() == pending
        assert all(w.get() == "AUTO" for w in widgets if isinstance(w, ctk.CTkEntry) and w.cget("placeholder_text") == "Giá hoặc %")
        menus = [w for w in widgets if isinstance(w, ctk.CTkOptionMenu)]
        assert [w.get() for w in menus] == ["AUTO · RULE" if "NORMAL" in modes else "OFF",
                                            ("ALERT · RULE" if dynamic else "OFF · RULE") if "NORMAL" in modes else "OFF",
                                            "ALERT · RULE" if "IND_EXIT" in modes else "OFF"]
        assert all(w.cget("fg_color") != "#F59E0B" for w in menus)
        for w in widgets:
            if isinstance(w, ctk.CTkButton):
                assert w.winfo_rootx() + w.winfo_width() <= top.winfo_rootx() + top.winfo_width() + 2
        assert any(isinstance(w, ctk.CTkLabel) and "BOT Bật" in w.cget("text") and "LƯU" in w.cget("text") for w in widgets)
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        after = trades.get(cycle.id)
        assert set(after.em_modes) == set(modes) and after.exit_rule_overrides == {}
        assert after.sl_enabled == sl and after.auto_sell_enabled is True
        assert (after.sl_mode, after.sl_value, after.tp_mode, after.tp_value) == ("DEFAULT", 0, "NONE", 0)
        assert position_override_items(settings, after) == []
        expected = {**before, "em_modes": after.em_modes, "exit_rule_overrides": {}, "auto_sell_enabled": True,
                    "sl_enabled": sl, "sl_mode": "DEFAULT", "sl_value": 0., "tp_mode": "NONE", "tp_value": 0.}
        assert after.to_dict() == expected
        assert trades.get(other.id).to_dict() == other.to_dict()
        assert rules.position_metrics(cycle.symbol, cycle.id) == history
        assert queue.get(manual.id).to_dict() == pending and settings.to_dict() == saved_settings
    finally:
        if top.winfo_exists():
            top.destroy()
        ctk.set_widget_scaling(1.)
        ctk.set_window_scaling(1.)


def test_reset_then_close_discards_draft_and_reset_then_edit_saves_only_new_override(ui_root, monkeypatch, tmp_path):
    settings = AppSettings()
    trades, rules, queue, cycle, other, manual = prepare(ui_root, monkeypatch, tmp_path, settings)
    before, history = cycle.to_dict(), rules.position_metrics(cycle.symbol, cycle.id)
    top = popup(ui_root, cycle)
    try:
        widgets = list(_widgets(top))
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "VỀ RULE").invoke()
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "ĐÓNG").invoke()
        assert trades.get(cycle.id).to_dict() == before
        top = popup(ui_root, cycle)
        widgets = list(_widgets(top))
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "VỀ RULE").invoke()
        menus = [w for w in widgets if isinstance(w, ctk.CTkOptionMenu)]
        menus[0].set("ALERT")
        next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget("text") == "LƯU").invoke()
        after = trades.get(cycle.id)
        assert after.exit_rule_overrides == {"normal_policy": "ALERT", "normal_arm_enabled": True}
        assert after.tp_mode == "NONE" and after.sl_mode == "DEFAULT"
        assert after.open_quantity == 400 and after.sold_quantity == 200
        assert rules.position_metrics(cycle.symbol, cycle.id) == history
        assert queue.get(manual.id).status == "PENDING"
    finally:
        if top.winfo_exists():
            top.destroy()
