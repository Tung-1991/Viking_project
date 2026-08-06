# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import config
from core import market_calendar, market_hours
from core.data_engine import DataEngine


def _settings(**overrides):
    data = dict(market_calendar.DEFAULT_SETTINGS)
    data.update(overrides)
    return data


def _cache(start, end, working):
    data = market_calendar.empty_cache()
    data.update({
        "fetched_at": f"{start} 07:00:00",
        "last_attempt_date": start,
        "coverage_start": start,
        "coverage_end": end,
        "working_dates": list(working),
    })
    return data


def test_parse_date_text_is_strict_and_deduplicated():
    assert market_calendar.parse_date_text("2026-07-20\n2026-07-20, 2026-07-21") == [
        "2026-07-20", "2026-07-21"
    ]
    with pytest.raises(ValueError, match="sai định dạng"):
        market_calendar.parse_date_text("2026-07-20\n20/07/2026")


def test_refresh_calls_dnse_at_most_once_per_day(monkeypatch, tmp_path):
    target = tmp_path / "market_calendar_cache.json"
    monkeypatch.setattr(market_calendar, "load_settings", lambda: _settings())
    calls = []

    def provider():
        calls.append(True)
        return ["2026-07-20", "2026-07-21", "2026-07-22"]

    now = datetime(2026, 7, 20, 7, 0)
    first = market_calendar.refresh_from_dnse(provider, now=now, path=str(target))
    second = market_calendar.refresh_from_dnse(provider, now=now.replace(hour=10), path=str(target))
    assert len(calls) == 1
    assert first["working_dates"] == second["working_dates"]
    assert market_calendar.load_cache(str(target))["fetched_at"] == "2026-07-20 07:00:00"


def test_refresh_failure_uses_existing_cache_and_no_cache_stays_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(market_calendar, "load_settings", lambda: _settings())
    cached_path = tmp_path / "cached.json"
    market_calendar.save_cache(
        _cache("2026-07-01", "2026-12-31", ["2026-07-20"]), str(cached_path)
    )

    def failed():
        raise RuntimeError("offline")

    preserved = market_calendar.refresh_from_dnse(
        failed, now=datetime(2026, 7, 20, 7), path=str(cached_path)
    )
    assert preserved["working_dates"] == ["2026-07-20"]

    empty_path = tmp_path / "empty.json"
    empty = market_calendar.refresh_from_dnse(
        failed, now=datetime(2026, 7, 20, 7), path=str(empty_path)
    )
    status = market_calendar.date_status(
        date(2026, 7, 20), settings=_settings(), cache=empty
    )
    assert status == {
        "status": "UNKNOWN", "source": "FALLBACK", "confirmed": False, "date": "2026-07-20"
    }


def test_dnse_cache_distinguishes_working_day_and_weekday_holiday():
    cache = _cache(
        "2026-07-20", "2026-07-24", ["2026-07-20", "2026-07-22", "2026-07-23", "2026-07-24"]
    )
    assert market_calendar.date_status(date(2026, 7, 20), _settings(), cache)["status"] == "TRADING"
    assert market_calendar.date_status(date(2026, 7, 21), _settings(), cache)["status"] == "HOLIDAY"


def test_manual_closed_date_works_without_dnse_cache():
    status = market_calendar.date_status(
        date(2026, 7, 20),
        _settings(manual_closed_dates=["2026-07-20"]),
        market_calendar.empty_cache(),
    )
    assert status["status"] == "HOLIDAY" and status["source"] == "MANUAL"


def test_market_calendar_settings_round_trip_and_are_not_symbol_overridden(monkeypatch, tmp_path):
    import json
    import core.storage_manager as storage_manager

    brain_path = tmp_path / "brain_settings.json"
    override_path = tmp_path / "symbol_overrides.json"
    monkeypatch.setattr(storage_manager, "BRAIN_FILE", str(brain_path))
    monkeypatch.setattr(storage_manager, "SYMBOL_OVERRIDES_FILE", str(override_path))
    storage_manager.invalidate_settings_cache()
    brain = storage_manager.load_brain_settings()
    brain["market_calendar"] = _settings(
        avoid_vn30_expiry_entry=True,
        vn30_rebalance_dates=["2026-07-27"],
    )
    assert storage_manager.save_brain_settings(brain) is True
    override_path.write_text(
        json.dumps({"VN30F1M": {"sandbox": {"market_calendar": {"avoid_vn30_expiry_entry": False}}}}),
        encoding="utf-8",
    )
    storage_manager.invalidate_settings_cache()
    loaded = storage_manager.get_brain_settings_for_symbol("VN30F1M")["market_calendar"]
    assert loaded["avoid_vn30_expiry_entry"] is True
    assert loaded["vn30_rebalance_dates"] == ["2026-07-27"]


def test_expiry_is_third_thursday_and_moves_to_previous_working_day():
    base = market_calendar.third_thursday(2026, 8)
    assert base.weekday() == 3
    assert 15 <= base.day <= 21
    previous = base - timedelta(days=1)
    cache = _cache(
        "2026-08-01", "2026-08-31", [previous.strftime("%Y-%m-%d")]
    )
    expiry = market_calendar.vn30_expiry_date(2026, 8, _settings(), cache)
    assert expiry == previous


def test_front_contract_period_matches_current_vn30f1m_rotation():
    cache = market_calendar.empty_cache()
    first_date, last_date = market_calendar.vn30_front_contract_period(
        datetime(2026, 7, 30, 10, 0),
        _settings(),
        cache,
    )

    assert first_date == date(2026, 6, 19)
    assert last_date == date(2026, 8, 20)


def test_entry_phase_block_defaults_on_and_can_be_disabled(monkeypatch):
    monkeypatch.setattr(
        market_hours,
        "market_session_phase",
        lambda _symbol: ("ATO", "ATO"),
    )
    assert market_calendar.entry_phase_block_reason(
        "VN30F1M",
        _settings(),
    )[0] == "ATO_ENTRY_BLOCK"
    assert market_calendar.entry_phase_block_reason(
        "VN30F1M",
        _settings(block_entry_ato=False),
    ) is None

    monkeypatch.setattr(
        market_hours,
        "market_session_phase",
        lambda _symbol: ("ATC", "ATC"),
    )
    assert market_calendar.entry_phase_block_reason(
        "VN30F1M",
        _settings(),
    )[0] == "ATC_ENTRY_BLOCK"


def test_special_day_gate_only_blocks_vn30f_entry(monkeypatch):
    expiry = market_calendar.third_thursday(2026, 7)
    monkeypatch.setattr(market_calendar, "load_cache", lambda *_args, **_kwargs: market_calendar.empty_cache())
    expiry_settings = _settings(avoid_vn30_expiry_entry=True)
    result = market_calendar.bot_entry_block_reason("VN30F1M", "ENTRY", expiry_settings, expiry)
    assert result[0] == "VN30_EXPIRY_ENTRY_BLOCK"
    assert market_calendar.bot_entry_block_reason("VN30F1M", "DCA", expiry_settings, expiry) is None
    assert market_calendar.bot_entry_block_reason("FPT", "ENTRY", expiry_settings, expiry) is None
    assert market_calendar.bot_entry_block_reason("VN30F1M", "ENTRY", _settings(), expiry) is None

    rebalance_settings = _settings(
        avoid_vn30_rebalance_entry=True,
        vn30_rebalance_dates=["2026-07-27"],
    )
    result = market_calendar.bot_entry_block_reason(
        "VN30F1M", "ENTRY", rebalance_settings, date(2026, 7, 27)
    )
    assert result[0] == "VN30_REBALANCE_ENTRY_BLOCK"
    assert market_calendar.bot_entry_block_reason(
        "VN30F1M", "PCA", rebalance_settings, date(2026, 7, 27)
    ) is None


def test_ckcs_open_delay_only_blocks_bot_entry():
    settings = _settings(avoid_ckcs_open_entry=True, ckcs_entry_delay_minutes=15)
    blocked = market_calendar.bot_entry_block_reason(
        "AAA", "ENTRY", settings, datetime(2026, 7, 20, 9, 29, 59)
    )
    assert blocked[0] == "CKCS_OPEN_ENTRY_DELAY"
    assert market_calendar.bot_entry_block_reason(
        "AAA", "ENTRY", settings, datetime(2026, 7, 20, 9, 30, 0)
    ) is None
    assert market_calendar.bot_entry_block_reason(
        "AAA", "DCA", settings, datetime(2026, 7, 20, 9, 20, 0)
    ) is None


def test_market_hours_and_data_engine_do_not_fetch_ohlc_on_cached_holiday(monkeypatch, tmp_path):
    target = tmp_path / "calendar.json"
    monkeypatch.setattr(config, "MARKET_CALENDAR_CACHE_FILE", str(target), raising=False)
    market_calendar.save_cache(
        _cache("2026-07-20", "2026-07-24", ["2026-07-20", "2026-07-22"]), str(target)
    )
    monkeypatch.setattr(market_calendar, "load_settings", lambda: _settings())
    monkeypatch.setattr(market_hours, "_market_now", lambda: datetime(2026, 7, 21, 10, 0))
    assert market_hours.market_session_phase("VN30F1M")[0] == "HOLIDAY"
    assert market_hours.is_symbol_network_window_open("VN30F1M")[0] is False

    engine = DataEngine()
    monkeypatch.setattr(
        "core.data_engine.dnse_api.get_ohlc",
        lambda *_args, **_kwargs: pytest.fail("OHLC must not be called on a holiday"),
    )
    frame = engine._fetch_bars("VN30F1M", "15m", 20, {}, None)
    assert isinstance(frame, pd.DataFrame) and frame.empty


def test_trade_manager_gate_runs_before_account_or_tick(monkeypatch):
    from core.trade_manager import TradeManager
    import core.trade_manager as trade_manager_module

    expiry = market_calendar.third_thursday(2026, 7)
    monkeypatch.setattr(trade_manager_module, "is_symbol_trade_window_open", lambda _symbol: (True, ""))
    monkeypatch.setattr(market_calendar, "_market_now", lambda: datetime.combine(expiry, datetime.min.time()))
    monkeypatch.setattr(market_calendar, "load_cache", lambda *_args, **_kwargs: market_calendar.empty_cache())

    class Connector:
        def get_account_info(self):
            raise AssertionError("account must not be read after calendar ENTRY block")

    manager = TradeManager.__new__(TradeManager)
    manager.connector = Connector()
    manager.state = {}
    manager._sync_state_lifecycle = lambda: None
    manager._get_brain_settings = lambda _symbol=None: {
        "market_calendar": _settings(avoid_vn30_expiry_entry=True)
    }
    result = manager.execute_bot_trade("BUY", "VN30F1M", {}, signal_class="ENTRY")
    assert result.startswith("SAFEGUARD_FAIL|VN30_EXPIRY_ENTRY_BLOCK|")
