# -*- coding: utf-8 -*-
import json

import config
from core import signal_opportunities, storage_manager


def _isolate(monkeypatch, tmp_path):
    account = tmp_path / "ACC"
    account.mkdir()
    brain = account / "brain_settings.json"
    brain.write_text(json.dumps({
        "opportunity_settings": {
            "enabled": True,
            "retention_hours": 24,
            "history_enabled": True,
        }
    }), encoding="utf-8")
    monkeypatch.setattr(storage_manager, "_active_account_dir", str(account))
    monkeypatch.setattr(storage_manager, "BRAIN_FILE", str(brain))
    storage_manager.invalidate_settings_cache()
    return account


def test_signal_opportunity_deduplicates_and_archives(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    signal = {
        "symbol": "AAA",
        "action": "BUY",
        "market_mode": "BREAKOUT",
        "context": {"current_price": 7.2, "trend_G0": "UP"},
    }
    first = signal_opportunities.record_signal(
        signal,
        now=1000,
        order_setup={"ok": True, "lot": 300, "price": 7.2, "sl": 6.6, "tp": 8.1},
    )
    second = signal_opportunities.record_signal(
        signal,
        now=1100,
        order_setup={"ok": True, "lot": 400, "price": 7.3, "sl": 6.7, "tp": 8.2},
    )
    rows = signal_opportunities.list_active(now=1101)
    assert first["id"] == second["id"]
    assert len(rows) == 1 and rows[0]["signal_count"] == 2
    assert rows[0]["execution_mode"] == "PAPER"
    # Kế hoạch giả lập đầu tiên được đóng băng; giá/SL/TP không chạy theo
    # mỗi lần daemon lặp lại cùng một tín hiệu.
    assert rows[0]["order_setup"]["lot"] == 300
    assert rows[0]["order_setup"]["price"] == 7.2
    assert rows[0]["simulation"]["status"] == "OPEN"

    assert signal_opportunities.update_active(first["id"], note="test")
    assert signal_opportunities.get(first["id"])["note"] == "test"

    final = signal_opportunities.finalize(first["id"], signal_opportunities.ACTIVATED, "test")
    assert final["status"] == signal_opportunities.ACTIVATED
    assert signal_opportunities.list_active(now=1102) == []
    assert signal_opportunities.list_history(include_active=False)[0]["status"] == signal_opportunities.ACTIVATED


def test_signal_opportunity_expires_to_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    signal = {"symbol": "VN30F1M", "action": "SELL", "context": {"current_price": 1900}}
    item = signal_opportunities.record_signal(signal, now=1000)
    expired = signal_opportunities.expire(now=item["expire_at"] + 1)
    assert expired[0]["status"] == signal_opportunities.EXPIRED
    assert signal_opportunities.list_active(now=item["expire_at"] + 2) == []


def test_invalid_levels_do_not_spam_but_can_recover_same_day(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    signal = {"symbol": "AAA", "action": "SELL", "context": {"current_price": 10}}
    invalid_setup = {"ok": False, "error": "INVALID_LEVELS|TP"}
    first = signal_opportunities.record_signal(signal, now=1000, order_setup=invalid_setup)
    signal_opportunities.finalize(first["id"], "INVALID_LEVELS", "INVALID_LEVELS|TP")

    duplicate = signal_opportunities.record_signal(
        signal,
        now=1100,
        order_setup=invalid_setup,
    )
    recovered = signal_opportunities.record_signal(
        signal,
        now=1200,
        order_setup={"ok": True, "price": 10, "sl": 12, "tp": 7},
    )

    assert duplicate["id"] == first["id"]
    assert recovered["id"] != first["id"]
    assert recovered["status"] == signal_opportunities.ACTIVE


def test_virtual_buy_tracks_tp1_and_calculates_ckcs_pnl(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    signal = {"symbol": "AAA", "action": "BUY", "context": {"current_price": 10}}
    item = signal_opportunities.record_signal(
        signal,
        now=1000,
        order_setup={
            "ok": True,
            "price": 10,
            "entry_price": 10,
            "lot": 100,
            "quantity_unit": "CP",
            "sl": 9,
            "tp": 12,
            "tp_targets": [12, 12.5, 13],
        },
    )

    assert item["simulation"]["status"] == "OPEN"
    assert signal_opportunities.observe_price("AAA", 12.1, now=1100) == 1
    tracked = signal_opportunities.get(item["id"])["simulation"]
    assert tracked["status"] == "WIN"
    assert tracked["result"] == "TP1"
    assert tracked["close_price"] == 12
    assert tracked["pnl"] == 200000


def test_virtual_sell_tracks_sl_and_keeps_first_plan(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    signal = {"symbol": "AAA", "action": "SELL", "context": {"current_price": 10}}
    first = signal_opportunities.record_signal(
        signal,
        now=1000,
        order_setup={
            "ok": True,
            "price": 10,
            "lot": 100,
            "sl": 11,
            "tp": 8,
            "tp_targets": [8, 7.5, 7],
        },
    )
    signal["context"]["current_price"] = 10.2
    signal_opportunities.record_signal(
        signal,
        now=1050,
        order_setup={
            "ok": True,
            "price": 10.2,
            "lot": 100,
            "sl": 11.2,
            "tp": 8.2,
        },
    )

    frozen = signal_opportunities.get(first["id"])
    assert frozen["order_setup"]["price"] == 10
    assert frozen["order_setup"]["sl"] == 11
    assert signal_opportunities.observe_price("AAA", 11.1, now=1100) == 1
    tracked = signal_opportunities.get(first["id"])["simulation"]
    assert tracked["status"] == "LOSS"
    assert tracked["result"] == "SL"
    assert tracked["close_price"] == 11
    assert tracked["pnl"] == -100000
