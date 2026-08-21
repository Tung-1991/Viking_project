from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from viking_v2.backtest.data import HistoricalDataStore
from viking_v2 import config as app_config
from viking_v2.backtest.engine import BacktestEngine, _normal_trail_fill, _opening_fill_price
from viking_v2.backtest.models import BacktestConfig, BacktestScenario, BacktestSettings, BacktestTrade
from viking_v2.backtest.report import ROUND_HEADERS, exit_detail, export_run_excel


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _payload(values: list[float], start: datetime) -> dict[str, list[float]]:
    timestamps = [int((start + timedelta(days=index)).timestamp()) for index in range(len(values))]
    return {
        "t": timestamps,
        "o": list(values),
        "h": [value * 1.01 for value in values],
        "l": [value * 0.99 for value in values],
        "c": list(values),
        "v": [1_000_000.0] * len(values),
    }


def test_historical_store_caches_dnse_daily_data(tmp_path):
    calls: list[str] = []
    start = datetime(2022, 1, 1, tzinfo=VN_TZ)
    payload = _payload([10.0] * 700, start)

    def fetch(symbol, resolution, from_ts, to_ts):
        calls.append(symbol)
        return payload

    store = HistoricalDataStore(root=tmp_path, fetcher=fetch)
    first = store.load_daily("FPT", "2023-08-01", "2023-12-01", warmup_sessions=260)
    assert first
    assert calls
    calls.clear()
    second = store.load_daily("FPT", "2023-08-01", "2023-12-01", warmup_sessions=260)
    assert second == first
    assert calls == []


def test_backtest_reuses_static_rule_and_fills_next_session(tmp_path):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = (
        [10.0] * 260
        + [10.0 - 0.05 * index for index in range(1, 8)]
        + [9.65 + 0.12 * index for index in range(1, 12)]
        + [10.97 - 0.15 * index for index in range(1, 12)]
    )
    payload = _payload(values, start)
    store = HistoricalDataStore(root=tmp_path, fetcher=lambda *_args: payload)
    first_test_day = (start + timedelta(days=255)).date().isoformat()
    last_test_day = (start + timedelta(days=len(values) - 1)).date().isoformat()
    result = BacktestEngine(store).run(BacktestConfig(
        symbols=["FPT"],
        start_date=first_test_day,
        end_date=last_test_day,
        initial_capital=100_000_000,
        auto_market_phase=False,
        fixed_market_phase="UPTREND",
        fixed_exposure_pct=100,
        em_modes=["IND_EXIT"],
        loss_lock_enabled=True,
        loss_lock_hours=48,
    ), save=False)
    sides = [event.side for event in result.events]
    assert sides == ["BUY", "SELL"]
    # Signal M occurs on index 268; execution must wait until index 269 open.
    assert result.events[0].date == (start + timedelta(days=269)).date().isoformat()
    assert result.closed_trades == 1
    assert result.total_fees > 0
    assert result.total_tax > 0
    assert result.config.rule_parameters["whipsaw_enabled"] is False
    assert result.data_quality["signal_resolution"] == "1D"
    assert result.data_quality["execution_resolution"]["FPT"] in {"5", "15", "1D"}


@pytest.mark.parametrize("slots, floor, ceiling", [(5, 150_000_000, 190_000_000),
                                                   (1, 800_000_000, 910_000_000)])
def test_capital_is_split_by_the_slot_count_exactly_as_entered(tmp_path, slots, floor, ceiling):
    """Exposure is divided by the entered slot count, never by the symbol count.

    One symbol with five slots deliberately deploys a fifth of the exposure and
    leaves the rest in cash, matching how the live bot sizes an order.
    """
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [10.0] * 260 + [9.8, 9.6, 9.7, 10.0, 10.3, 10.1, 9.8, 9.5]
    store = HistoricalDataStore(root=tmp_path / str(slots), fetcher=lambda *_a: _payload(values, start))
    result = BacktestEngine(store).run(BacktestConfig(
        symbols=["FPT"],
        start_date=(start + timedelta(days=255)).date().isoformat(),
        end_date=(start + timedelta(days=len(values) - 1)).date().isoformat(),
        initial_capital=1_000_000_000,
        fixed_market_phase="UPTREND", fixed_exposure_pct=90,
        rule_parameters={"max_positions": slots},
    ), save=False)
    buys = [event for event in result.events if event.side == "BUY"]
    assert buys, "kịch bản một mã phải vào được lệnh"
    assert floor <= buys[0].gross <= ceiling


def test_scenario_without_a_market_state_is_rejected():
    # A scenario is only meaningful once its state is chosen, so it is refused
    # outright rather than kept around as a disabled row nobody understands.
    with pytest.raises(ValueError):
        BacktestScenario("Sideway / Recovery", ["HSG"], "2022-11-17", "2025-04-25", "")


def test_loss_cooldown_is_calendar_hours():
    config = BacktestConfig(["FPT"], "2024-01-01", "2024-02-01", loss_lock_hours=48)
    assert config.loss_lock_hours == 48


def test_backtest_defaults_are_neutral_and_independent():
    config = BacktestConfig(["FPT"], "2024-01-01", "2024-02-01")
    assert config.initial_capital == 1_000_000_000
    assert config.auto_market_phase is False
    assert config.fixed_market_phase == "ACCUMULATION"
    assert config.fixed_exposure_pct == 60
    assert config.loss_lock_enabled is False
    assert config.whipsaw_enabled is False
    assert config.em_modes == ["NORMAL", "HIGH", "IND_EXIT"]
    assert config.execution_resolution == "AUTO"
    assert config.buy_fee_rate == pytest.approx(app_config.DEFAULT_BUY_FEE_PCT / 100.0)
    assert BacktestSettings().buy_fee_pct == pytest.approx(app_config.DEFAULT_BUY_FEE_PCT)


def test_continuous_fill_uses_first_candle_open_at_or_after_0915():
    day = datetime(2026, 8, 20, tzinfo=VN_TZ)
    bars = [
        {"time": int(day.replace(hour=9, minute=0).timestamp()), "open": 100.0, "close": 101.0},
        {"time": int(day.replace(hour=9, minute=15).timestamp()), "open": 102.0, "close": 109.0},
        {"time": int(day.replace(hour=9, minute=30).timestamp()), "open": 110.0, "close": 111.0},
    ]
    assert _opening_fill_price(bars, "ATO") == 100.0
    # 102 is observable at 09:15; 109 is only known after that candle closes.
    assert _opening_fill_price(bars, "CONTINUOUS") == 102.0


def test_mode2_carry_keeps_pending_order_and_does_not_replay_boundary_day(tmp_path):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [10.0] * 260 + [10.0 - 0.05 * index for index in range(1, 8)]
    values += [9.65 + 0.12 * index for index in range(1, 12)]
    store = HistoricalDataStore(root=tmp_path, fetcher=lambda *_args: _payload(values, start))
    carry: dict = {}
    first = BacktestEngine(store).run(BacktestConfig(
        symbols=["FPT"],
        start_date=(start + timedelta(days=255)).date().isoformat(),
        end_date=(start + timedelta(days=268)).date().isoformat(),
        initial_capital=100_000_000,
        fixed_market_phase="UPTREND", fixed_exposure_pct=100,
        em_modes=["IND_EXIT"],
    ), carry=carry, save=False)
    assert first.events == []
    assert "FPT" in carry["pending"]

    second = BacktestEngine(store).run(BacktestConfig(
        symbols=["FPT"],
        # Deliberately overlap the boundary; the account must not replay it.
        start_date=(start + timedelta(days=268)).date().isoformat(),
        end_date=(start + timedelta(days=270)).date().isoformat(),
        initial_capital=100_000_000,
        fixed_market_phase="UPTREND", fixed_exposure_pct=100,
        em_modes=["IND_EXIT"],
    ), carry=carry, save=False)
    buys = [event for event in second.events if event.side == "BUY"]
    assert len(buys) == 1
    assert buys[0].date == (start + timedelta(days=269)).date().isoformat()
    assert second.equity_curve[0]["date"] == (start + timedelta(days=269)).date().isoformat()


def test_execution_data_auto_falls_back_and_keeps_resolution_caches_separate(tmp_path):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    payload = _payload([10.0] * 20, start)
    calls: list[str] = []

    def fetch(_symbol, resolution, _from_ts, _to_ts):
        calls.append(resolution)
        return payload if resolution == "15" else None

    store = HistoricalDataStore(root=tmp_path, fetcher=fetch)
    rows, resolution, warnings = store.load_execution("FPT", "2023-01-01", "2023-01-20")
    assert rows
    assert resolution == "15"
    assert warnings
    assert (tmp_path / "cache" / "FPT_15.json").exists()
    assert (tmp_path / "cache" / "FPT_5.json").exists()
    calls.clear()
    second_rows, second_resolution, _ = store.load_execution("FPT", "2023-01-01", "2023-01-20")
    assert second_rows == rows
    assert second_resolution == "15"
    assert calls == []


def test_scenario_takes_indicators_and_exposure_from_shared_settings(tmp_path):
    """A scenario pins symbol, window and state only.

    Letting a scenario carry its own EMA would test a regime-switching rule
    the live bot cannot execute.
    """
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [100 + index * 0.1 for index in range(300)]
    store = HistoricalDataStore(root=tmp_path, fetcher=lambda *_args: _payload(values, start))
    scenario = BacktestScenario(
        "HSG · UPTREND", ["HSG"],
        (start + timedelta(days=255)).date().isoformat(),
        (start + timedelta(days=299)).date().isoformat(),
        "UPTREND",
    )
    assert not hasattr(scenario, "buy_ema_fast")
    result = BacktestEngine(store).run_scenario(
        scenario, initial_capital=1_000_000_000,
        rule_parameters={"sell_ema_fast": 5, "sell_ema_slow": 10,
                         "exposure": {"UPTREND": 0.9}},
        save=False,
    )
    assert result.config.rule_parameters["sell_ema_fast"] == 5
    assert result.config.fixed_exposure_pct == 90


def test_excel_export_gives_each_run_its_own_sheet(tmp_path):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [10.0] * 260 + [9.8, 9.6, 9.7, 10.0, 10.3, 10.1, 9.8, 9.5]
    store = HistoricalDataStore(root=tmp_path / "data", fetcher=lambda *_a: _payload(values, start))
    result = BacktestEngine(store).run(BacktestConfig(
        symbols=["FPT"],
        start_date=(start + timedelta(days=255)).date().isoformat(),
        end_date=(start + timedelta(days=len(values) - 1)).date().isoformat(),
        fixed_market_phase="ACCUMULATION", em_modes=["IND_EXIT"], run_name="MODE 1",
    ), save=False)
    path = export_run_excel(result, tmp_path / "exports", mode="MODE 1", stamp="0819")
    # The name has to say what is inside instead of a random run id.
    assert "MODE 1" in path.name and "FPT" in path.name and "0819" in path.name
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True)
    assert book.sheetnames == ["MODE 1", "THÔNG TIN"]
    headers = [cell.value for cell in next(book["MODE 1"].iter_rows(min_row=1, max_row=1))]
    # Both modes keep the same shape so their sheets can be compared or pasted
    # together; no blended average exit price anywhere.
    # The run name is the sheet tab now, so that column is gone from the rows.
    assert headers == list(ROUND_HEADERS[1:])
    assert headers[0] == "LƯỢT"
    assert "GIÁ RA" not in headers
    assert {"CẮT LỖ", "PHIÊN", "THOÁT BỞI", "EMA RA", "RSI RA"} <= set(headers)


def test_exit_detail_names_every_sell_with_its_own_price():
    trade = BacktestTrade("t", "HSG", "2022-02-09", exit_fills=[
        {"event": "NORMAL_PROTECTION", "quantity": 1500, "price": 21.24},
        {"event": "INDICATOR_EXIT", "quantity": 3000, "price": 21.09},
    ])
    assert exit_detail(trade) == "NORMAL 1,500@21.24 + EXIT 3,000@21.09"
    assert exit_detail(BacktestTrade("t", "HSG", "2022-02-09")) == "CÒN MỞ"


def test_normal_intraday_trail_does_not_use_same_bar_future_low():
    bars = [
        {"open": 106.0, "high": 108.0, "low": 104.0, "close": 105.0},
        {"open": 105.0, "high": 105.5, "low": 104.0, "close": 104.5},
    ]
    fill, peak = _normal_trail_fill(
        bars, entry_price=100.0, peak_profit_pct=0.0,
        arm_pct=7.0, giveback_pct=3.0,
    )
    assert peak == pytest.approx(8.0)
    # Giveback is 3% of the peak price: 100 x 1.08 x 0.97, not 100 x 1.05.
    assert fill == pytest.approx(104.76)


def test_scenario_accepts_runtime_callbacks_without_putting_them_in_config(tmp_path):
    start = datetime(2023, 1, 1, tzinfo=VN_TZ)
    values = [100 + index * 0.1 for index in range(300)]
    store = HistoricalDataStore(root=tmp_path, fetcher=lambda *_args: _payload(values, start))
    scenario = BacktestScenario(
        "VA", ["HSG"],
        (start + timedelta(days=255)).date().isoformat(),
        (start + timedelta(days=299)).date().isoformat(),
        "ACCUMULATION",
    )
    progress: list[float] = []
    result = BacktestEngine(store).run_scenario(
        scenario,
        initial_capital=1_000_000_000,
        progress=lambda value, _text: progress.append(value),
        cancelled=lambda: False,
        save=False,
    )
    assert result.config.run_name == "VA"
    assert progress
