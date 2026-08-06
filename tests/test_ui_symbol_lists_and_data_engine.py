# -*- coding: utf-8 -*-
import csv
from types import SimpleNamespace

import config
import core.data_engine as data_engine_module
import pandas as pd
from core.data_engine import DataEngine
import ui_bot_strategy
import ui_popups
from core import storage_manager


def test_ui_symbol_helpers_use_ckps_and_ckcs_watchlists(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["vn30f1m", "VN30F1M"], raising=False)
    monkeypatch.setattr(config, "CKCS_WATCHLIST", ["fpt", "SSI", "fpt"], raising=False)
    monkeypatch.setattr(config, "COIN_LIST", ["OLDCOIN"], raising=False)

    assert ui_bot_strategy._build_trade_symbol_list() == ["VN30F1M", "FPT", "SSI"]
    assert ui_popups._build_watch_symbols() == ["VN30F1M", "FPT", "SSI"]


def test_fetch_bars_returns_empty_when_ohlc_t_is_none(monkeypatch):
    engine = DataEngine()
    fake_dnse = SimpleNamespace(
        market_type_for_symbol=lambda _symbol: "STOCK",
        get_ohlc=lambda *_args: {"t": None, "o": [], "h": [], "l": [], "c": [], "v": []},
    )
    monkeypatch.setattr(data_engine_module, "dnse_api", fake_dnse)

    df = engine._fetch_bars("FPT", "15m", 10, {}, None)

    assert df.empty


def test_inactive_trend_role_does_not_activate_indicator():
    indicators = {
        "simple_breakout": {
            "active": False,
            "is_trend": True,
            "groups": ["G2"],
            "params": {"lookback": 2},
        }
    }

    assert DataEngine._effective_group_indicators(
        indicators,
        "G2",
        include_trend=True,
    ) == {}


def test_calc_swings_uses_latest_confirmed_pivots_not_window_extremes():
    engine = DataEngine()
    frame = pd.DataFrame(
        {
            "high": [30.0, 12.0, 14.0, 11.0, 15.0, 10.0, 13.0, 11.0, 12.0],
            "low": [5.0, 8.0, 9.0, 7.0, 10.0, 6.0, 9.0, 7.5, 8.0],
            "close": [10.0] * 9,
        }
    )

    swing_high, swing_low = engine._calc_swings(frame, lookback=9, strength=1)

    assert swing_high == 13.0
    assert swing_low == 7.5


def test_old_paper_receipt_is_visible_as_history_row(monkeypatch):
    monkeypatch.setattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0, raising=False)
    row = ui_popups._paper_closed_trade_history_row(
        {
            "ticket": "PAPER-3",
            "symbol": "MBS",
            "volume": 100.0,
            "price_open": 21.9,
            "price_close": 18.8,
            "profit": -313711.5,
            "reason": "MANUAL_CLOSE",
            "closed_at": 1784602290.0,
        },
        {
            "trade_sl": {"PAPER-3": 17.3},
            "trade_tp": {"PAPER-3": 31.1},
            "trade_tactics": {"PAPER-3": "BE+SWING"},
            "trade_excursions": {"PAPER-3": {"mae_usd": -250000.0, "mfe_usd": -230000.0}},
            "bot_last_entry_times": {"MBS": 1783562479.0},
        },
    )

    assert row[1:4] == ["PAPER-3", "MBS", "BUY"]
    assert row[8] == "-3711.50"
    assert row[9] == "-310000.00"
    assert row[13] == "PAPER_20260721"
    assert row[6:8] == ["17.30000", "31.10000"]
    assert row[12] == "CLOSED_RECEIPT | BE+SWING"
    assert row[14:16] == ["-313711.50", "-230000.00"]
    assert row[18] == "18.80000"
    assert ui_popups._history_row_close_day(row) == "2026-07-21"


def test_history_summary_uses_net_pnl_and_worst_excursion():
    win = ["", "1", "FPT", "BUY", "100", "10", "9", "12", "-20", "100", "TP", "PAPER", "", "", "-50", "150", "", "2026-07-22T10:00:00", "11"]
    fee_turns_gross_win_into_loss = ["", "2", "SSI", "BUY", "100", "20", "19", "23", "-120", "100", "SL", "PAPER", "", "", "-10", "80", "", "2026-07-22T10:05:00", "20"]

    summary = ui_popups._history_session_summary([win, fee_turns_gross_win_into_loss])

    assert summary["gross_pnl"] == 200.0
    assert summary["fee"] == -140.0
    assert summary["net_pnl"] == 60.0
    assert summary["wins"] == 1
    assert summary["winrate"] == 50.0
    assert summary["worst_mae"] == -50.0
    assert summary["best_mfe"] == 150.0


def test_history_rows_are_split_into_four_market_mode_tabs():
    def row(ticket, symbol):
        return ["", ticket, symbol]

    assert ui_popups._history_row_scope(row("123", "VN30F1M")) == "Phái sinh"
    assert ui_popups._history_row_scope(row("124", "FPT")) == "CKCS"
    assert ui_popups._history_row_scope(row("PAPER-1", "VN30F1M")) == "Paper-PS"
    assert ui_popups._history_row_scope(row("PAPER-2", "FPT")) == "Paper-CKCS"


def test_delete_history_day_uses_close_date(monkeypatch, tmp_path):
    path = tmp_path / "trade_history_master.csv"
    header = [
        "Time", "Ticket", "Symbol", "Type", "Vol", "Entry", "SL", "TP", "Fee",
        "PnL ($)", "Reason", "Market Mode", "Trigger", "Session_ID", "MAE ($)",
        "MFE ($)", "Open Time", "Close Time", "Exit Price",
    ]
    rows = [
        ["", "PAPER-1", "FPT", "BUY", "100", "10", "9", "12", "-10", "100", "TP", "PAPER", "", "20260721_A", "-20", "110", "", "2026-07-21T10:00:00", "11"],
        ["", "PAPER-2", "SSI", "BUY", "100", "20", "19", "23", "-10", "100", "TP", "PAPER", "", "20260722_A", "-20", "110", "", "2026-07-22T10:00:00", "21"],
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    monkeypatch.setattr(storage_manager, "MASTER_LOG_FILE", str(path))

    assert storage_manager.delete_history_day("2026-07-21") == 1
    with open(path, "r", newline="", encoding="utf-8") as handle:
        remaining = list(csv.reader(handle))
    assert [row[1] for row in remaining[1:]] == ["PAPER-2"]
