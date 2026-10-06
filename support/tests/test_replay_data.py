from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from viking_v2.backtest.replay import ReplayDataStore, infer_resolution, infer_symbol


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _csv(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "time,open,high,low,close,Volume\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_tradingview_name_extracts_cts_and_resolution():
    name = "HOSE_DLY_CTS, 1.csv"
    assert infer_symbol(name) == "CTS"
    assert infer_resolution(name) == "1"


def test_import_normalizes_utc_price_and_is_idempotent(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_CTS, 1.csv", [
        "2026-08-20T02:15:00Z,21900,21950,21900,21950,5300",
        "2026-08-20T07:45:00Z,21800,21800,21800,21800,19800",
        "2026-08-21T02:15:00Z,22000,22000,22000,22000,100",
        "2026-08-21T06:53:00Z,23300,23300,23300,23300,300",
    ])
    store = ReplayDataStore(tmp_path / "replay")
    preview = store.import_file(source)
    assert preview.symbol == "CTS"
    assert preview.resolution == "1"
    assert preview.price_scale == 1000
    assert preview.full_days == 1
    assert preview.partial_dates == ["2026-08-21"]
    assert preview.inserted == 4

    again = store.import_file(source)
    assert again.inserted == 0
    assert again.unchanged == 4
    datasets = store.list_datasets()
    assert len(datasets) == 1
    assert datasets[0].bar_count == 4
    assert datasets[0].coverage_start == "2026-08-20"
    assert datasets[0].coverage_end == "2026-08-21"
    assert datasets[0].partial_days == 1

    rows, resolution, status = store.load_day("CTS", "2026-08-20")
    assert resolution == "1" and status == "FULL"
    assert rows[0]["open"] == pytest.approx(21.9)
    assert datetime.fromtimestamp(rows[0]["time"], VN_TZ).strftime("%H:%M") == "09:15"
    assert store.load_day("CTS", "2026-08-21") == ([], "", "MISSING")
    partial, _, status = store.load_day("CTS", "2026-08-21", complete_only=False)
    assert partial and status == "PARTIAL"


def test_delete_replay_dataset_does_not_touch_other_files(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_CTS, 1.csv", [
        "2026-08-20T02:15:00Z,21900,21900,21900,21900,100",
        "2026-08-20T07:45:00Z,21800,21800,21800,21800,100",
    ])
    unrelated = tmp_path / "cache" / "CTS_1D.json"
    unrelated.parent.mkdir()
    unrelated.write_text("keep", encoding="utf-8")
    store = ReplayDataStore(tmp_path / "replay")
    store.import_file(source)
    assert store.delete_dataset("CTS", "1") == 2
    assert store.list_datasets() == []
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_conflicting_duplicate_inside_one_file_is_rejected(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_CTS, 1.csv", [
        "2026-08-20T02:15:00Z,21900,21900,21900,21900,100",
        "2026-08-20T02:15:00Z,22000,22000,22000,22000,100",
    ])
    with pytest.raises(ValueError, match="bị trùng"):
        ReplayDataStore(tmp_path / "replay").inspect_file(source)


def test_xlsx_import_finds_ohlcv_sheet_and_uses_local_time_for_naive_cells(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "HOSE_DLY_CTS, 5.xlsx"
    book = Workbook()
    cover = book.active
    cover.title = "README"
    cover.append(["not", "market", "data"])
    sheet = book.create_sheet("Bars")
    sheet.append(["TIME", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"])
    sheet.append([datetime(2026, 8, 20, 9, 15), 21.9, 22.0, 21.8, 21.95, 100])
    sheet.append([datetime(2026, 8, 20, 14, 45), 21.95, 22.0, 21.9, 22.0, 200])
    book.save(path)

    store = ReplayDataStore(tmp_path / "replay")
    preview = store.import_file(path)
    assert preview.resolution == "5"
    assert preview.price_scale == 1
    rows, _, status = store.load_day("CTS", "2026-08-20")
    assert status == "FULL"
    assert datetime.fromtimestamp(rows[0]["time"], VN_TZ).strftime("%H:%M") == "09:15"


def test_two_minute_tradingview_buckets_cover_ato_and_atc(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_VIX, 2.csv", [
        "2026-03-02T02:14:00Z,17000,17100,16900,17050,100",
        "2026-03-02T02:16:00Z,17050,17100,17000,17050,200",
        "2026-03-02T07:44:00Z,17050,17050,17000,17000,300",
    ])
    store = ReplayDataStore(tmp_path / "replay")
    preview = store.import_file(source)

    assert preview.resolution == "2"
    assert preview.full_days == 1
    assert preview.partial_days == 0
    rows, resolution, status = store.load_day("VIX", "2026-03-02")
    assert resolution == "2" and status == "FULL"
    assert datetime.fromtimestamp(rows[0]["time"], VN_TZ).strftime("%H:%M") == "09:14"
    assert datetime.fromtimestamp(rows[-1]["time"], VN_TZ).strftime("%H:%M") == "14:44"


def test_missing_opening_bucket_is_allowed_when_next_real_bar_exists(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_VIX, 2.csv", [
        "2026-03-09T02:16:00Z,17000,17100,16900,17050,100",
        "2026-03-09T07:44:00Z,17050,17050,17000,17000,300",
    ])
    preview = ReplayDataStore(tmp_path / "replay").import_file(source)
    assert preview.full_days == 1
    assert preview.partial_days == 0


def test_full_day_can_start_on_first_trade_a_few_minutes_after_open(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_CTS, 1.csv", [
        "2026-05-06T02:18:00Z,20500,20500,20500,20500,100",
        "2026-05-06T07:45:00Z,21100,21100,21100,21100,200",
    ])
    preview = ReplayDataStore(tmp_path / "replay").import_file(source)
    assert preview.full_days == 1
    assert preview.partial_days == 0


def test_replay_can_aggregate_full_intraday_days_for_indicator_warmup(tmp_path):
    source = _csv(tmp_path / "HOSE_DLY_FPT, 2.csv", [
        "2026-03-02T02:14:00Z,10000,10100,9900,10050,100",
        "2026-03-02T07:44:00Z,10050,10300,10000,10200,200",
        "2026-03-03T02:14:00Z,10200,10200,10100,10150,300",
        "2026-03-03T07:44:00Z,10150,10400,10100,10300,400",
    ])
    store = ReplayDataStore(tmp_path / "replay")
    store.import_file(source)

    rows = store.load_daily_aggregates("FPT", before="2026-03-04")

    assert len(rows) == 2
    assert rows[0]["open"] == pytest.approx(10.0)
    assert rows[0]["high"] == pytest.approx(10.3)
    assert rows[0]["low"] == pytest.approx(9.9)
    assert rows[0]["close"] == pytest.approx(10.2)
    assert rows[0]["volume"] == pytest.approx(300)
