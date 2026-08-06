# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
import os

import pandas as pd
import pytest

import config
from ai_advisor import paths, scan_cache


NOW = datetime(2026, 7, 7, 10, 30)  # thứ Ba, trong phiên sáng


def make_daily_df(days=30, last_is_today=True, base_price=28.0, base_vol=1_000_000):
    """DF khung ngày synthetic: giá tăng dần, volume tăng dần, nến cuối = hôm nay."""
    end = NOW.date() if last_is_today else NOW.date() - timedelta(days=1)
    times = pd.to_datetime([end - timedelta(days=days - 1 - i) for i in range(days)])
    closes = [base_price + i * 0.1 for i in range(days)]
    vols = [base_vol + i * 10_000 for i in range(days)]
    df = pd.DataFrame({
        "time": times,
        "open": [c - 0.05 for c in closes],
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": vols,
    })
    return df


def make_intraday_df(bars=50):
    times = pd.to_datetime([NOW - timedelta(minutes=15 * (bars - i)) for i in range(bars)])
    closes = [30.0 + i * 0.01 for i in range(bars)]
    return pd.DataFrame({
        "time": times,
        "open": closes, "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes], "close": closes,
        "volume": [10_000] * bars,
    })


def make_context(market_open=True, price=31.0):
    return {
        "symbol": "HPG", "current_price": price, "market_open": market_open,
        "trend_G0": "UP", "trend_G1": "UP", "trend_G2": "NONE", "trend_G3": "NONE",
        "market_mode": "TRENDING", "mode_source": "G0", "block_reason": "OK / Ready",
        "group_signals": {"G0": 1, "G1": 1, "G2": 0, "G3": 0},
        "ema20_G0": 30.0,
    }


@pytest.fixture
def dfs():
    return {"G0": make_daily_df(), "G1": make_intraday_df(), "G2": make_intraday_df(), "G3": make_intraday_df()}


# ---------------------------------------------------------------- hàm thuần
def test_session_elapsed_fraction():
    assert scan_cache.session_elapsed_fraction(datetime(2026, 7, 7, 8, 0)) == 0.0
    assert scan_cache.session_elapsed_fraction(datetime(2026, 7, 7, 15, 0)) == 1.0
    mid = scan_cache.session_elapsed_fraction(datetime(2026, 7, 7, 10, 15))
    assert 0.25 < mid < 0.35  # 75/255 phút


def test_pick_daily_df(dfs):
    grp, df = scan_cache.pick_daily_df(dfs)
    assert grp == "G0"
    assert df is dfs["G0"]


def test_price_block_partial_bar(dfs):
    snap = scan_cache.compute_snapshot(dfs, make_context(), 0, now=NOW)
    price = snap["price"]
    assert price["daily_bar_is_today"] is True
    # pct_1d so current (31.0) với close hôm QUA (nến -2), không phải nến đang hình thành
    prev_close = dfs["G0"]["close"].iloc[-2]
    assert price["pct_1d"] == round((31.0 - prev_close) / prev_close * 100, 2)
    assert price["pct_1w"] is not None
    assert price["high_1w"] >= price["low_1w"]


def test_volume_block_partial_excludes_forming_bar(dfs):
    snap = scan_cache.compute_snapshot(dfs, make_context(market_open=True), 0, now=NOW)
    vol = snap["volume"]
    assert vol["is_partial_bar"] is True
    # avg20 tính trên nến ĐÃ ĐÓNG (loại nến hôm nay)
    expected_avg = dfs["G0"]["volume"].iloc[:-1].tail(20).mean()
    assert vol["avg20"] == round(float(expected_avg), 0)
    assert vol["ratio"] is not None
    assert vol["projected_ratio"] > vol["ratio"]  # pro-rate phiên mới trôi ~30%
    assert vol["trend_5d"] == "đi ngang"  # volume tăng ~5%/tuần < ngưỡng 10%


def test_volume_block_closed_market(dfs):
    now_closed = NOW.replace(hour=15, minute=0)
    snap = scan_cache.compute_snapshot(dfs, make_context(market_open=False), 0, now=now_closed)
    assert snap["volume"]["is_partial_bar"] is False
    assert snap["volume"]["projected_ratio"] is None


def test_intraday_builds_today_ohlcv_when_daily_bar_is_not_finished():
    dfs = {
        "G0": make_daily_df(last_is_today=False),
        "G2": make_intraday_df(bars=8),
    }
    snap = scan_cache.compute_snapshot(dfs, make_context(price=30.07), 0, now=NOW)
    intraday = dfs["G2"]
    today = intraday[intraday["time"].dt.date == NOW.date()]
    assert snap["price"]["open"] == today.iloc[0]["open"]
    assert snap["price"]["high"] == today["high"].max()
    assert snap["price"]["low"] == today["low"].min()
    assert snap["price"]["close"] == 30.07
    assert snap["volume"]["today"] == today["volume"].sum()


def test_check_indicator_extraction_is_dynamic(dfs):
    dfs["G0"]["MACD_12_26_9"] = 0.15
    dfs["G0"]["MACDh_12_26_9"] = 0.03
    dfs["G0"]["BBL_20_2.0"] = 29.0
    dfs["G0"]["BBU_20_2.0"] = 33.0
    context = make_context()
    context["check_indicator_columns"] = {"G0": {
        "macd": ["MACD_12_26_9", "MACDh_12_26_9"],
        "bollinger_bands": ["BBL_20_2.0", "BBU_20_2.0"],
    }}
    settings = {
        "indicators": {"rsi": {"active": True, "groups": ["G0"], "params": {"period": 14}}},
        "check_indicators": {
            "macd": {"active": True, "groups": ["G0"], "params": {"fast": 12, "slow": 26, "signal": 9}},
            "bollinger_bands": {"active": True, "groups": ["G0"], "params": {"period": 20, "std_dev": 2.0}},
        },
    }
    snap = scan_cache.compute_snapshot(dfs, context, 1, now=NOW, settings=settings)
    g0 = snap["check"]["groups"]["G0"]
    assert set(g0) == {"macd", "bollinger_bands"}
    assert g0["macd"]["metrics"]["MACD_12_26_9"] == 0.15
    assert "rsi" not in g0
    assert snap["bot"]["latest_signal"] == 1
    assert snap["bot"]["market_mode"] == "TRENDING"


def test_merge_sample_and_signal_dedup(dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 0, now=NOW)
    entry = scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    assert entry["samples"] == 1 and entry["first_scan"] == "10:30"
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW + timedelta(minutes=20))
    assert entry["samples"] == 2 and entry["last_scan"] == "10:50"

    ctx = make_context()
    assert scan_cache.record_signal_event(cache, "HPG", "BUY", ctx, now=NOW) is True
    # Cùng chiều trong 30 phút -> dedup
    assert scan_cache.record_signal_event(cache, "HPG", "BUY", ctx, now=NOW + timedelta(minutes=10)) is False
    # Khác chiều -> ghi
    assert scan_cache.record_signal_event(cache, "HPG", "SELL", ctx, now=NOW + timedelta(minutes=10)) is True
    # Cùng chiều sau 30 phút -> ghi
    assert scan_cache.record_signal_event(cache, "HPG", "BUY", ctx, now=NOW + timedelta(minutes=40)) is True
    day = NOW.strftime("%Y-%m-%d")
    assert len(cache["symbols"]["HPG"]["days"][day]["signals"]) == 3
    assert scan_cache.derive_weekly(cache["symbols"]["HPG"]) == {"buy": 2, "sell": 1}


def test_empty_check_does_not_create_empty_segment(dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 0, now=NOW)
    entry = scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    assert entry["check_segments"] == []


def test_prune_retention(dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 0, now=NOW)
    for i in range(15):
        scan_cache.merge_sample(cache, "HPG", snap, now=NOW - timedelta(days=14 - i))
    scan_cache.prune(cache, retention_days=10)
    assert len(cache["symbols"]["HPG"]["days"]) == 10
    assert min(cache["symbols"]["HPG"]["days"]) == (NOW - timedelta(days=9)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- I/O + recorder
@pytest.fixture
def tmp_account(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "account_dir", lambda: str(tmp_path))
    return tmp_path


def test_save_load_round_trip(tmp_account, dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 1, now=NOW)
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    assert scan_cache.save_cache(cache) is True
    loaded = scan_cache.load_cache()
    assert loaded["symbols"]["HPG"]["days"][NOW.strftime("%Y-%m-%d")]["samples"] == 1


def test_scan_settings_survive_brain_settings_reload(monkeypatch, tmp_path):
    import core.storage_manager as storage_manager
    from ai_advisor import history

    monkeypatch.setattr(storage_manager, "BRAIN_FILE", str(tmp_path / "brain_settings.json"))
    monkeypatch.setattr(history, "ensure_config_snapshot", lambda reason="": "test")
    monkeypatch.setattr(history, "record_event", lambda *args, **kwargs: None)
    storage_manager.invalidate_settings_cache()
    brain = storage_manager.load_brain_settings()
    brain["SCAN_SNAPSHOT_ENABLED"] = False
    brain["SCAN_SNAPSHOT_INTERVAL_MINUTES"] = 12.5
    brain["SCAN_SNAPSHOT_RETENTION_DAYS"] = 250
    brain["SCAN_SNAPSHOT_SYMBOLS"] = ["VN30F1M", "HPG"]
    assert storage_manager.save_brain_settings(brain) is True
    storage_manager.invalidate_settings_cache()
    loaded = storage_manager.load_brain_settings()
    assert loaded["SCAN_SNAPSHOT_ENABLED"] is False
    assert loaded["SCAN_SNAPSHOT_INTERVAL_MINUTES"] == 12.5
    assert loaded["SCAN_SNAPSHOT_RETENTION_DAYS"] == 250
    assert loaded["SCAN_SNAPSHOT_SYMBOLS"] == ["VN30F1M", "HPG"]


def test_legacy_cache_migration_keeps_all_selected_market_symbols(tmp_account, dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 1, now=NOW)
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    scan_cache.merge_sample(cache, "VN30F1M", snap, now=NOW)
    legacy = paths.legacy_scan_cache_path()
    assert scan_cache.save_cache(cache, legacy) is True

    migrated = scan_cache.load_cache()
    assert set(migrated["symbols"]) == {"HPG", "VN30F1M"}
    assert paths.scan_cache_path() != legacy
    assert __import__("os").path.isfile(legacy)
    assert __import__("os").path.isfile(paths.scan_cache_path())


def test_recorder_accepts_derivative_when_selected_for_raw_data(tmp_account, dfs):
    rec = scan_cache.ScanSnapshotRecorder()
    assert rec.maybe_record("VN30F1M", dfs, make_context(), 1, now=NOW) is True
    rec.flush()
    assert "VN30F1M" in scan_cache.load_cache()["symbols"]


def test_load_corrupt_file_recovers(tmp_account, tmp_path):
    (tmp_path / "scan_snapshot_cache.json").write_text("{hỏng json", encoding="utf-8")
    loaded = scan_cache.load_cache()
    assert loaded == scan_cache.empty_cache()


def test_recorder_throttle_and_signal(tmp_account, monkeypatch, dfs):
    monkeypatch.setattr(config, "SCAN_SNAPSHOT_INTERVAL_MINUTES", 15, raising=False)
    rec = scan_cache.ScanSnapshotRecorder()
    ctx = make_context()
    rec.maybe_record("HPG", dfs, ctx, 0, now=NOW)
    rec.maybe_record("HPG", dfs, ctx, 0, now=NOW + timedelta(minutes=1))  # trong interval -> bỏ qua
    rec.flush()
    day = NOW.strftime("%Y-%m-%d")
    saved = scan_cache.load_cache()
    assert saved["symbols"]["HPG"]["days"][day]["samples"] == 1

    # Tín hiệu BUY ghi NGAY bất chấp throttle
    rec.maybe_record("HPG", dfs, ctx, 1, now=NOW + timedelta(minutes=2))
    rec.flush()
    saved = scan_cache.load_cache()
    assert saved["symbols"]["HPG"]["days"][day]["signals"][0]["side"] == "BUY"


def test_recorder_eod_final(tmp_account, monkeypatch, dfs):
    monkeypatch.setattr(config, "SCAN_SNAPSHOT_INTERVAL_MINUTES", 15, raising=False)
    rec = scan_cache.ScanSnapshotRecorder()
    now_closed = NOW.replace(hour=15, minute=5)
    rec.maybe_record(
        "HPG",
        dfs,
        make_context(market_open=True),
        0,
        now=NOW.replace(hour=14, minute=30),
    )
    ctx = make_context(market_open=False)
    rec.maybe_record("HPG", dfs, ctx, 0, now=now_closed)
    rec.flush()
    day = NOW.strftime("%Y-%m-%d")
    saved = scan_cache.load_cache()
    entry = saved["symbols"]["HPG"]["days"][day]
    assert entry["eod_final"] is True
    # Đã final -> lần quét sau ngoài giờ không ghi thêm
    rec.maybe_record("HPG", dfs, ctx, 0, now=now_closed + timedelta(minutes=30))
    rec.flush()
    assert scan_cache.load_cache()["symbols"]["HPG"]["days"][day]["samples"] == entry["samples"]


def test_after_1445_does_not_create_new_daily_snapshot(tmp_account, dfs):
    rec = scan_cache.ScanSnapshotRecorder()
    rec.maybe_record(
        "HPG",
        dfs,
        make_context(market_open=False),
        0,
        now=NOW.replace(hour=15, minute=0),
    )
    rec.flush()
    assert scan_cache.load_cache()["symbols"] == {}


def test_finalize_closed_day_marks_stale_morning_data_incomplete(tmp_account, dfs):
    rec = scan_cache.ScanSnapshotRecorder()
    rec.maybe_record("HPG", dfs, make_context(market_open=True), 0, now=NOW)
    before = rec.status()
    assert rec.finalize_closed_day(["HPG"], now=NOW.replace(hour=15, minute=0)) is True
    rec.flush()
    entry = scan_cache.load_cache()["symbols"]["HPG"]["days"][NOW.strftime("%Y-%m-%d")]
    assert entry["eod_final"] is False
    assert entry["day_status"] == "INCOMPLETE"
    assert entry["samples"] == 1
    assert before["symbols"] == 1


def test_legacy_false_eod_is_demoted_when_loaded(tmp_account):
    day = NOW.strftime("%Y-%m-%d")
    cache = {
        "schema_version": 2,
        "updated_at": f"{day} 17:00:00",
        "symbols": {
            "HPG": {
                "days": {
                    day: {
                        "samples": 3,
                        "first_scan": "09:51",
                        "last_scan": "10:21",
                        "day_status": "EOD",
                        "eod_final": True,
                    }
                }
            }
        },
    }
    assert scan_cache.save_cache(cache) is True
    entry = scan_cache.load_cache()["symbols"]["HPG"]["days"][day]
    assert entry["eod_final"] is False
    assert entry["day_status"] == "INCOMPLETE"


def test_report_session_quality_rejects_old_or_morning_only_data(dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 0, now=NOW)
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    quality = scan_cache.report_session_quality(
        cache,
        "afternoon",
        now=NOW.replace(hour=15),
        selected_symbols=["HPG"],
    )
    assert quality["ready"] is False
    assert quality["covered_entries"] == 0

    cache = scan_cache.empty_cache()
    scan_cache.merge_sample(
        cache,
        "HPG",
        snap,
        now=NOW.replace(hour=14, minute=30),
    )
    quality = scan_cache.report_session_quality(
        cache,
        "afternoon",
        now=NOW.replace(hour=15),
        selected_symbols=["HPG"],
    )
    assert quality["ready"] is True
    assert quality["covered_entries"] == 1


def test_report_skip_warning_is_not_repeated_for_same_coverage(
    tmp_account, dfs, monkeypatch, caplog
):
    from ai_advisor import scan_report

    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 0, now=NOW)
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    assert scan_cache.save_cache(cache) is True
    monkeypatch.setattr(scan_cache, "selected_research_symbols", lambda: ["HPG"])
    scan_report._REPORT_SKIP_LOG_SIGNATURES.clear()

    with caplog.at_level("WARNING"):
        assert scan_report.export_ckcs_report(
            report_session="morning",
            require_current_session=True,
        ) is None
        assert scan_report.export_ckcs_report(
            report_session="morning",
            require_current_session=True,
        ) is None

    messages = [
        record.getMessage()
        for record in caplog.records
        if "CKCS morning report skipped" in record.getMessage()
    ]
    assert len(messages) == 1


# ---------------------------------------------------------------- renderer + API section
def _build_populated_cache(dfs):
    cache = scan_cache.empty_cache()
    snap = scan_cache.compute_snapshot(dfs, make_context(), 1, now=NOW)
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW - timedelta(days=1))
    scan_cache.merge_sample(cache, "HPG", snap, now=NOW)
    scan_cache.record_signal_event(cache, "HPG", "BUY", make_context(), now=NOW)
    return cache


def test_compact_summary_render(dfs):
    from ai_advisor import scan_report

    cache = _build_populated_cache(dfs)
    text = scan_report.build_compact_summary(cache)
    assert "CÁCH ĐỌC DỮ LIỆU" in text
    assert "CHECK" in text
    assert "## HPG" in text
    assert "BUY 10:30" in text
    # Kho trống -> chuỗi rỗng (để build_api_sections bỏ qua)
    assert scan_report.build_compact_summary(scan_cache.empty_cache()) == ""


def test_full_report_render(dfs):
    from ai_advisor import scan_report

    cache = _build_populated_cache(dfs)
    text = scan_report.render_full_report(cache)
    assert "### Biến động toàn khoảng" in text
    assert "## HPG — 2 ngày giao dịch" in text
    assert "BOT signal" in text
    assert "## TÓM TẮT TOÀN BỘ DANH SÁCH" in text
    assert "WATCH/CHỜ MUA/MUA/HOLD/GIẢM/EXIT/LOẠI" in text
    assert "vùng mua" in text
    assert "app không tự biến kết quả AI thành lệnh CKCS" in text
    assert "- CHECK segment" not in text
    assert "Không có số liệu CHECK" not in text


def test_period_summary_uses_observed_price_when_legacy_high_low_are_missing():
    from ai_advisor import scan_report

    days = [
        ("2026-07-01", {"price": {"current": 10.0, "high": None, "low": None}}),
        ("2026-07-02", {"price": {"current": 12.0, "high": 11.0, "low": 10.5}}),
    ]
    text = "\n".join(scan_report._period_summary(days))
    assert "cao nhất 12" in text
    assert "thấp nhất 10" in text


def test_export_ckcs_report_is_separate_from_advisor_api(tmp_account, dfs, monkeypatch):
    from ai_advisor import api_client, scan_report
    monkeypatch.setattr(scan_cache, "selected_research_symbols", lambda: ["HPG"])

    # Kho trống -> không sinh file; Advisor API không có dữ liệu quét CKCS.
    assert scan_report.export_ckcs_report() is None
    sections = dict(api_client.build_api_sections())
    assert "scan_summary.md" not in sections
    assert "scan_report.md" not in sections

    cache = _build_populated_cache(dfs)
    snap = scan_cache.compute_snapshot(dfs, make_context(), 1, now=NOW)
    scan_cache.merge_sample(cache, "VN30F1M", snap, now=NOW)
    scan_cache.save_cache(cache)
    report = paths.scan_report_path()
    for legacy in paths.legacy_scan_session_report_paths():
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        with open(legacy, "w", encoding="utf-8") as handle:
            handle.write("legacy")
    result = scan_report.export_ckcs_report(report_days=15, output_path=report)
    assert result is not None and result["symbols"] == 1
    assert os.path.exists(report)
    assert all(not os.path.exists(path) for path in paths.legacy_scan_session_report_paths())
    assert not os.path.exists(os.path.join(paths.advisor_root(), "scan_summary.md"))
    report_text = open(report, encoding="utf-8").read()
    assert "## HPG" in report_text
    assert "## VN30F1M" not in report_text

    monkeypatch.setattr(scan_cache, "selected_research_symbols", lambda: ["HPG", "VN30F1M"])
    result = scan_report.export_ckcs_report(report_days=15, output_path=report)
    assert result["symbols"] == 2
    assert "## VN30F1M" in open(report, encoding="utf-8").read()

    sections = dict(api_client.build_api_sections())
    assert "scan_summary.md" not in sections
    assert "scan_report.md" not in sections


def test_copy_for_llm_button_and_legacy_manual_report_are_removed(tmp_account):
    import main

    assert not hasattr(main.BotUI, "copy_ckcs_report_ui")
    assert not hasattr(main.BotUI, "generate_ckcs_report_ui")
    assert hasattr(main.BotUI, "refresh_current_ckcs_report_ui")

def test_recorder_skips_non_trading_day(tmp_account, monkeypatch):
    """Cuối tuần: nến ngày cuối KHÔNG phải hôm nay -> không tạo entry rác."""
    monkeypatch.setattr(config, "SCAN_SNAPSHOT_INTERVAL_MINUTES", 15, raising=False)
    intraday_stale = make_intraday_df()
    intraday_stale["time"] = intraday_stale["time"] - timedelta(days=1)
    dfs_stale = {"G0": make_daily_df(last_is_today=False), "G1": intraday_stale,
                 "G2": intraday_stale, "G3": intraday_stale}
    rec = scan_cache.ScanSnapshotRecorder()
    rec.maybe_record("HPG", dfs_stale, make_context(market_open=False), 0, now=NOW)
    rec.flush()
    assert "HPG" not in scan_cache.load_cache()["symbols"]
