"""Rebranding must change presentation without moving accounts or sending I/O."""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from viking_v2 import config
from viking_v2.branding import APP_NAME, window_title
from viking_v2.connections.telegram import SignalTelegramService

ROOT = Path(__file__).resolve().parents[2]
UI_FILES = (
    "dashboard/window.py", "dashboard/panels.py", "dashboard/actions.py",
    "dashboard/windows.py", "dashboard/info.py", "dashboard/tables.py",
    "connections/window.py", "connections/telegram.py", "rules/window.py",
    "backtest/window.py", "main.py",
)


def test_brand_name_and_existing_runtime_paths():
    assert APP_NAME == "Money Hunter"
    assert window_title("KẾT NỐI") == "Money Hunter · KẾT NỐI"
    assert config.PACKAGE_ROOT.name == "viking_v2"
    # Branding does not rename the class imported by existing entrypoints/tools.
    from viking_v2.dashboard.window import VikingApp
    assert VikingApp.__name__ == "VikingApp"


@pytest.mark.parametrize("relative", UI_FILES)
def test_ui_literals_have_no_old_brand_except_internal_thread_names(relative):
    tree = ast.parse((ROOT / "viking_v2" / relative).read_text(encoding="utf-8-sig"))
    remaining = [node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and re.search(r"\bviking\b", node.value, re.IGNORECASE)
                 and not node.value.startswith("viking-")]
    assert not remaining, remaining


@pytest.mark.parametrize("kind,section", [
    ("rule", "RULE"), ("connection", "KẾT NỐI"), ("backtest", "BACKTEST"),
    ("history", "LỊCH SỬ"), ("table", "DANH MỤC"), ("volume", "LỌC VOLUME VN100"),
    ("info", "INFO"),
])
def test_actual_popup_titles_use_money_hunter(ui_root, kind, section):
    from viking_v2.backtest.window import BacktestPopup
    from viking_v2.backtest.data import HistoricalDataStore
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup, VolumeScannerPopup
    from viking_v2.dashboard.windows import DataTablePopup, HistoryPopup
    from viking_v2.dashboard.info import InfoPopup
    from viking_v2.rules.window import RuleSettingsPopup

    settings = config.AppSettings()
    client = DNSEClient()
    factories = {
        "rule": lambda: RuleSettingsPopup(ui_root, settings, "BRAND_TEST", lambda: None),
        "connection": lambda: ConnectionPopup(ui_root, settings, "BRAND_TEST", client, lambda: None),
        "backtest": lambda: BacktestPopup(ui_root, settings, None),
        "history": lambda: HistoryPopup(ui_root, lambda _mode: []),
        "table": lambda: DataTablePopup(ui_root, title="DANH MỤC",
                                         columns=(("symbol", "MÃ", 100, "w"),),
                                         rows_provider=lambda _mode: []),
        "volume": lambda: VolumeScannerPopup(ui_root, client, lambda callback: callback()),
        "info": lambda: InfoPopup(ui_root, lambda: settings, HistoricalDataStore(None)),
    }
    popup = factories[kind]()
    try:
        ui_root.update_idletasks()
        assert popup.top.title() == window_title(section)
    finally:
        getattr(popup, "close", getattr(popup, "_close", popup.top.destroy))()


@pytest.mark.parametrize("remaining", [0, 100])
def test_telegram_user_messages_use_new_brand_without_network(remaining):
    sent = []

    class LocalTelegram:
        def send_message(self, _chat, message):
            sent.append(message)

    service = SignalTelegramService(LocalTelegram(), chat_id="local-test")
    assert service.notify_external_sell(symbol="MSN", quantity=100, price=74,
                                        remaining_quantity=remaining)
    assert service.notify_system_alert(summary="CALENDAR_ERROR", execution_mode="REAL")
    assert service.notify_corporate_action(symbol="MSN", ex_date="2026-10-09")
    assert len(sent) == 3
    assert all(APP_NAME in message and "Viking" not in message for message in sent)


def test_info_uses_each_pages_settings_and_can_refresh_after_changes(ui_root):
    import customtkinter as ctk
    from viking_v2.backtest.data import HistoricalDataStore
    from viking_v2.dashboard.info import InfoPopup

    def labels(widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                result.append(child.cget("text"))
            result.extend(labels(child))
        return result

    settings = config.AppSettings(bot_sl_enabled=False, manual_sell_pause_minutes=45)
    popup = InfoPopup(ui_root, lambda: settings, HistoricalDataStore(None))
    try:
        real = labels(popup.bodies["REAL"])
        backtest = labels(popup.bodies["BACKTEST"])
        assert "OFF · LỆNH BOT MỚI" in real and "45 PHÚT" in real
        assert "OFF · LỆNH BOT MỚI" not in backtest
        assert "45 PHÚT" not in backtest and "KHÔNG ÁP DỤNG" in backtest
        settings.bot_sl_enabled = True
        settings.manual_sell_pause_minutes = 0
        popup.refresh()
        updated = labels(popup.bodies["REAL"])
        assert "OFF · LỆNH BOT MỚI" not in updated and "45 PHÚT" not in updated
        assert "OFF" in updated
    finally:
        popup.close()


def test_bat_branding_preserves_python_module_and_repository():
    batch = (ROOT / "START_SYSTEM.bat").read_text(encoding="utf-8")
    helper = (ROOT / "support/launcher.ps1").read_text(encoding="utf-8")
    assert "title Money Hunter" in batch and "VIKING V2" not in batch
    assert "https://github.com/Tung-1991/Viking_project.git" in helper
    assert "-m viking_v2.main" in helper
