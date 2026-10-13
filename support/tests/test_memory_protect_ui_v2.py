"""Verify operator choices survive save and bot/backtest sync independently."""
import pytest

from viking_v2 import config
from viking_v2.config import AppSettings
from viking_v2.rules.window import RuleSettingsPopup
from viking_v2.rules.business import StaticRuleParameters


@pytest.mark.parametrize("memory_mode", ["∞ VA", "⏳ X PHIÊN"])
def test_rule_save_keeps_modes_bases_and_repeats_independent(ui_root,tmp_path,monkeypatch,memory_mode):
    monkeypatch.setattr(config,"ACCOUNTS_ROOT",tmp_path / "accounts")
    settings = AppSettings()
    popup = RuleSettingsPopup(ui_root,settings,"NEW_RULE",lambda:None)
    try:
        popup.buy_ema_cross_mode.set(memory_mode)
        if memory_mode == "⏳ X PHIÊN":
            popup.buy_ema_cross_sessions.delete(0,"end")
            popup.buy_ema_cross_sessions.insert(0,"5")
        popup.normal_sell_basis.set("GỐC")
        popup.dynamic_sell_basis.set("CÒN LẠI")
        popup.normal_repeat.set(False)
        popup.dynamic_repeat.set(True)
        popup.dynamic_sell.delete(0,"end")
        popup.dynamic_sell.insert(0,"25")
        popup.save()
        assert "ĐÃ LƯU" in popup.status.cget("text")
        params = StaticRuleParameters.from_dict(config.load_settings("NEW_RULE").rule_parameters)
        assert params.buy_ema_cross_mode == ("VA" if memory_mode == "∞ VA" else "SESSIONS")
        assert params.buy_ema_cross_sessions == (3 if memory_mode == "∞ VA" else 5)
        assert params.normal_sell_basis == "ORIGINAL" and params.dynamic_sell_basis == "REMAINING"
        assert params.normal_repeat_enabled is False and params.dynamic_repeat_enabled is True
        assert params.normal_sell_pct == 33 and params.dynamic_sell_pct == 25
        popup.dynamic_sell.delete(0,"end")
        popup.dynamic_sell.insert(0,"100")
        popup._refresh_execution_preview()
        assert popup.dynamic_repeat_switch.cget("state") == "disabled"
        assert "DYN ON 100% CÒN LẠI · LẶP OFF" in popup.execution_preview["NORMAL"].cget("text")
    finally:
        popup._close()


def test_backtest_sync_collect_keeps_the_two_sell_controls_and_memory(ui_root,tmp_path,monkeypatch):
    from viking_v2.backtest.window import BacktestPopup
    monkeypatch.setattr(config,"RUNTIME_ROOT",tmp_path / "runtime")
    monkeypatch.setattr(config,"ACCOUNTS_ROOT",tmp_path / "accounts")
    settings = AppSettings(rule_parameters={"buy_ema_cross_mode":"SESSIONS","buy_ema_cross_sessions":4,
        "normal_sell_pct":20,"normal_sell_basis":"ORIGINAL","normal_repeat_enabled":False,
        "dynamic_sell_pct":50,"dynamic_sell_basis":"REMAINING","dynamic_repeat_enabled":True,"protect_config_version":2})
    popup = BacktestPopup(ui_root,settings,None)
    try:
        monkeypatch.setattr(popup.data,"save_settings",lambda *_:None)
        popup._sync_from_bot()
        result = popup._collect().rule_parameters
        for key in ("buy_ema_cross_mode","buy_ema_cross_sessions","normal_sell_pct","normal_sell_basis",
                    "normal_repeat_enabled","dynamic_sell_pct","dynamic_sell_basis","dynamic_repeat_enabled"):
            assert result[key] == settings.rule_parameters[key]
    finally:
        popup.close()
