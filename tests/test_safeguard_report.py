# -*- coding: utf-8 -*-
import core.storage_manager as storage_manager
from core import safeguard_report


def _fake_brain(symbol=None):
    return {
        "bot_safeguard": {
            "MAX_DAILY_LOSS_PERCENT": 2.5,
            "MAX_OPEN_POSITIONS": 3,
            "MAX_TRADES_PER_DAY": 30,
            "BOT_ORDER_MODE": "AUTO" if symbol == "VN30F1M" else "NORMAL",
            "BOT_ATC_EXIT": False,
        }
    }


def test_effective_safeguard_reads_merged_brain(monkeypatch):
    monkeypatch.setattr(storage_manager, "get_brain_settings_for_symbol", _fake_brain)
    sg = safeguard_report.effective_safeguard("VN30F1M")
    assert sg["MAX_OPEN_POSITIONS"] == 3
    assert sg["BOT_ORDER_MODE"] == "AUTO"
    sg2 = safeguard_report.effective_safeguard("FPT")
    assert sg2["BOT_ORDER_MODE"] == "NORMAL"


def test_format_effective_table_lists_symbols_and_labels(monkeypatch):
    monkeypatch.setattr(storage_manager, "get_brain_settings_for_symbol", _fake_brain)
    table = safeguard_report.format_effective_table(["VN30F1M", "FPT"])
    assert "VN30F1M" in table
    assert "FPT" in table
    assert "Lỗ tối đa/ngày (%)=2.5" in table
    assert "Vị thế mở tối đa=3" in table


def test_format_effective_table_handles_empty_config(monkeypatch):
    monkeypatch.setattr(storage_manager, "get_brain_settings_for_symbol", lambda symbol=None: {})
    table = safeguard_report.format_effective_table(["FPT"])
    assert "chưa có cấu hình" in table


def test_log_effective_safeguard_never_raises(monkeypatch):
    monkeypatch.setattr(storage_manager, "get_brain_settings_for_symbol", lambda symbol=None: (_ for _ in ()).throw(RuntimeError("boom")))

    class _Logger:
        def info(self, *_a, **_k):
            raise RuntimeError("logger down")

    # Không được ném lỗi ra ngoài dù resolver/logger đều hỏng.
    safeguard_report.log_effective_safeguard(_Logger(), ["FPT"])
