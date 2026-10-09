from copy import deepcopy
import csv
from datetime import datetime, timedelta

import pytest

from viking_v2.backtest.data import VN_TZ
from viking_v2.rules.business import indicator_snapshot
from viking_v2.services.indicator_comparison import IndicatorComparisonStore, number_comparison


def chart_csv(path, *, count=260, final_price=22400):
    final = datetime(2026, 10, 9, tzinfo=VN_TZ)
    bars = []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "Volume"])
        for index in range(count):
            close = 21538.46156 + ((index % 11) - 5) * 38.461538
            if index == count - 1:
                close = final_price
            stamp = int((final - timedelta(days=count - index - 1)).timestamp())
            writer.writerow([stamp, close, close + 100, close - 100, close, 1000])
            bars.append({"time": stamp, "close": close / 1000})
    return bars


def event(**kwargs):
    return {"timestamp": "2026-10-09 14:00:01", "symbol": "HDB", "price": 22.2,
            "ema_fast": 22.3163, "ema_slow": 22.3459, "rsi": 52.29,
            "ema_fast_period": 3, "ema_slow_period": 6, "rsi_period": 14,
            "signal": "SELL", "acted": "WAIT", **kwargs}


def test_comparison_uses_precise_history_and_event_price_not_final_daily_close(tmp_path):
    path = tmp_path / "HOSE_HDB_1D.csv"
    bars = chart_csv(path, final_price=99000)
    store = IndicatorComparisonStore(tmp_path / "comparison")
    assert store.import_daily(path, "HDB") == 260
    original = [event()]
    before = deepcopy(original)
    compared = store.compare(original)[0]
    expected = indicator_snapshot([*bars[:-1], {"close": 22.2}])
    assert compared["ema_fast"] == pytest.approx(expected["ema_fast"])
    assert compared["rsi"] == pytest.approx(expected["rsi"])
    assert compared["rsi_previous"] == pytest.approx(expected["rsi_previous"])
    assert compared["rsi_previous_date"] == "2026-10-08"
    assert original == before and compared["signal"] == "SELL" and compared["acted"] == "WAIT"
    assert compared["price"] == 22.2
    assert compared["dnse_indicators"]["rsi"] == 52.29
    assert compared["comparison_source"] == "TRADINGVIEW"


def test_no_fake_tradingview_conversion_when_dataset_or_adjustment_basis_is_missing(tmp_path):
    store = IndicatorComparisonStore(tmp_path / "comparison")
    missing = store.compare([event()])[0]
    assert missing["comparison_error"] and missing["ema_fast"] == "" and missing["rsi"] == ""
    path = tmp_path / "HDB.csv"
    chart_csv(path)
    store.import_daily(path, "HDB")
    for day in ("2026-10-08", "2026-10-10"):
        row = store.compare([event(timestamp=f"{day} 14:00:01")])[0]
        assert "phiên chuẩn giá" in row["comparison_error"] and row["rsi_previous"] == ""


def test_chart_import_does_not_round_precision_and_survives_reopen(tmp_path):
    path = tmp_path / "HDB.csv"
    chart_csv(path)
    root = tmp_path / "comparison"
    IndicatorComparisonStore(root).import_daily(path, "HDB")
    reopened = IndicatorComparisonStore(root)
    assert reopened._store("HDB").read()["bars"][-2]["close"] != round(reopened._store("HDB").read()["bars"][-2]["close"], 2)
    assert reopened.compare([event()])[0]["rsi"] != ""


def test_import_rejects_short_history_and_unsafe_symbol_without_writing(tmp_path):
    store = IndicatorComparisonStore(tmp_path / "comparison")
    path = tmp_path / "HDB.csv"
    chart_csv(path, count=20)
    with pytest.raises(ValueError, match="100"):
        store.import_daily(path, "HDB")
    with pytest.raises(ValueError):
        store._store("../orders")
    assert not store.root.exists()


@pytest.mark.parametrize("a,b,expected", [
    (56.79, 56.86, "56.79 < 56.86"), (56.79, 56.79, "56.79 = 56.79"),
    (56.791, 56.789, "56.791 > 56.789"), (52.29, "", "—"), (float("nan"), 50, "—"),
])
def test_numeric_comparison_never_changes_the_raw_sign_due_to_rounding(a, b, expected):
    assert number_comparison(a, b) == expected


def test_history_toggle_is_read_only_and_symbols_are_expandable(ui_root, tmp_path):
    from viking_v2.dashboard.windows import HistoryPopup
    raw = [event(rsi_previous=56.79), event(symbol="MSN")]
    before = deepcopy(raw)
    popup = HistoryPopup(ui_root, lambda _mode: [], signals_provider=lambda: raw,
                         indicator_comparison=IndicatorComparisonStore(tmp_path / "comparison"))
    try:
        assert popup.indicator_basis == "DNSE"
        day = popup.signal_tree.get_children()[0]
        symbols = popup.signal_tree.get_children(day)
        assert len(symbols) == 2
        hdb = next(item for item in symbols if "HDB" in popup.signal_tree.item(item, "text"))
        row = popup.signal_tree.get_children(hdb)[0]
        assert popup.signal_tree.set(row, "rsi_comparison") == "52.29 < 56.79"
        popup.signal_tree.item(hdb, open=True)
        popup._change_indicator_basis("TRADINGVIEW")
        assert popup.signal_tree.item(hdb, "open")
        assert popup.signal_tree.set(popup.signal_tree.get_children(hdb)[0], "rsi_comparison") == "—"
        popup._change_indicator_basis("DNSE")
        assert raw == before and popup.signal_tree.set(popup.signal_tree.get_children(hdb)[0], "rsi_comparison") == "52.29 < 56.79"
    finally:
        popup.close()


def test_distinct_signal_cycles_are_not_hidden_even_in_the_same_second():
    from viking_v2.dashboard.windows import signal_rows_by_day
    rows = [event(signal_cycle="first"), event(signal_cycle="second")]
    grouped = signal_rows_by_day(rows)[0]
    assert len(grouped["rows"]) == 2
    assert len(signal_rows_by_day([rows[0], dict(rows[0])])[0]["rows"]) == 1


def test_history_excel_preserves_raw_values_and_never_executes_spreadsheet_formulas(ui_root, tmp_path, monkeypatch):
    from openpyxl import load_workbook
    from viking_v2.dashboard.windows import HistoryPopup
    target = tmp_path / "signals.xlsx"
    monkeypatch.setattr("viking_v2.dashboard.windows.filedialog.asksaveasfilename", lambda **_kwargs: str(target))
    monkeypatch.setattr("viking_v2.dashboard.windows.messagebox.showerror", lambda *_args, **_kwargs: pytest.fail("Export failed"))
    original = [event(symbol="=DANGEROUS()", rsi_previous=56.79)]
    # A historical broker field is untrusted text, even though imported chart symbols are validated.
    popup = HistoryPopup(ui_root, lambda _mode: [], signals_provider=lambda: original,
                         indicator_comparison=IndicatorComparisonStore(tmp_path / "comparison"))
    try:
        popup._export_signals()
        book = load_workbook(target)
        try:
            assert book.sheetnames == ["DNSE GỐC", "TRADINGVIEW ĐỐI CHIẾU"]
            sheet = book.worksheets[0]
            assert sheet["B2"].value == "=DANGEROUS()" and sheet["B2"].data_type == "s"
            assert "52.29 < 56.79" in [cell.value for cell in sheet[2]]
            assert book.worksheets[1].max_row == 2
        finally:
            book.close()
    finally:
        popup.close()
