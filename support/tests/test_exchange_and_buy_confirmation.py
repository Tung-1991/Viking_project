from __future__ import annotations

from datetime import datetime
import sqlite3

import pytest

from viking_v2.backtest.engine import BacktestEngine
from viking_v2.backtest.data import HistoricalDataStore
from viking_v2.backtest.models import BacktestConfig
from viking_v2.backtest.replay import ReplayDataStore, infer_exchange
from viking_v2.rules.business import (
    StaticRule,
    StaticRuleParameters,
    advance_buy_confirmation,
)
from viking_v2.rules.state import RuleStateStore
from viking_v2.models import OrderIntent
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.market import VN_TZ, active_trading_minutes, market_phase


def _indicators(*, ema: bool = True, rsi: bool = True) -> dict[str, float]:
    return {
        "buy_ema_fast": 10.1 if ema else 9.9,
        "buy_ema_slow": 10.0,
        "rsi": 51.0 if rsi else 49.0,
        "rsi_previous": 50.0,
    }


def _advance(
    state: dict, at: datetime, params: StaticRuleParameters, *,
    raw: bool = False, ema: bool = True, rsi: bool = True,
):
    return advance_buy_confirmation(
        state, raw_trigger=raw, indicators=_indicators(ema=ema, rsi=rsi),
        observed_at=at, exchange="HOSE", params=params,
    )


def test_exchange_phases_differ_at_same_time():
    at = datetime(2026, 9, 4, 9, 5, tzinfo=VN_TZ)
    assert market_phase(at, exchange="HOSE")[0] == "ATO"
    assert market_phase(at, exchange="HNX")[0] == "OPEN"
    assert market_phase(at, exchange="UPCOM")[0] == "OPEN"
    assert market_phase(at.replace(hour=14, minute=50), exchange="HNX")[0] == "CLOSED"
    assert market_phase(at.replace(hour=14, minute=50), exchange="UPCOM")[0] == "OPEN"


def test_hnx_two_minute_import_keeps_0900_and_marks_only_short_day_partial(tmp_path):
    source = tmp_path / "HNX_DLY_SHS, 2.csv"
    source.write_text(
        "time,open,high,low,close,Volume\n"
        "2026-09-03T02:04:00Z,14,14,14,14,1\n"
        "2026-09-03T07:44:00Z,14,14,14,14,1\n"
        "2026-09-04T02:00:00Z,14,14,14,14,1\n"
        "2026-09-04T07:44:00Z,14,14,14,14,1\n",
        encoding="utf-8",
    )
    store = ReplayDataStore(tmp_path / "replay")
    preview = store.import_file(source, price_scale=1)
    assert infer_exchange(source) == "HNX"
    assert preview.exchange == "HNX"
    assert preview.partial_dates == ["2026-09-03"]
    assert preview.full_days == 1
    dataset = store.list_datasets()[0]
    assert dataset.exchange == "HNX"
    rows, resolution, status = store.load_day("SHS", "2026-09-04")
    assert resolution == "2" and status == "FULL"
    assert datetime.fromtimestamp(rows[0]["time"], VN_TZ).strftime("%H:%M") == "09:00"


def test_legacy_replay_database_infers_and_persists_exchange(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    connection = sqlite3.connect(root / "replay.sqlite3")
    connection.executescript(
        """
        CREATE TABLE replay_bars (
            symbol TEXT, resolution TEXT, timestamp INTEGER, open REAL, high REAL,
            low REAL, close REAL, volume REAL, PRIMARY KEY(symbol,resolution,timestamp)
        );
        CREATE TABLE replay_days (
            symbol TEXT, resolution TEXT, day TEXT, status TEXT, bar_count INTEGER,
            first_timestamp INTEGER, last_timestamp INTEGER,
            PRIMARY KEY(symbol,resolution,day)
        );
        CREATE TABLE replay_datasets (
            symbol TEXT, resolution TEXT, source_name TEXT, source_hash TEXT,
            timezone TEXT, price_scale REAL, imported_at TEXT,
            PRIMARY KEY(symbol,resolution)
        );
        INSERT INTO replay_datasets VALUES(
            'SHS','2','HNX_DLY_SHS, 2.csv','x','Asia/Ho_Chi_Minh',1.0,'now'
        );
        """
    )
    connection.commit()
    connection.close()
    store = ReplayDataStore(root)
    assert store.list_datasets()[0].exchange == "HNX"
    assert store.exchange_for("SHS") == "HNX"


@pytest.mark.parametrize(
    ("require_ema", "require_rsi", "ema_ok", "rsi_ok", "expected"),
    [
        (True, False, True, False, "CONFIRMED"),
        (False, True, False, True, "CONFIRMED"),
        (True, True, False, True, "CANCELLED"),
    ],
)
def test_buy_confirmation_respects_selected_conditions(
    require_ema, require_rsi, ema_ok, rsi_ok, expected,
):
    params = StaticRuleParameters(
        buy_confirmation_enabled=True,
        buy_confirmation_minutes=5,
        buy_confirmation_require_ema=require_ema,
        buy_confirmation_require_rsi=require_rsi,
    )
    start = datetime(2026, 9, 4, 9, 20, tzinfo=VN_TZ)
    state, status, _ = _advance({}, start, params, raw=True)
    assert status == "WAITING"
    state, status, details = _advance(
        state, start.replace(minute=25), params, ema=ema_ok, rsi=rsi_ok,
    )
    assert status == expected
    if expected == "CONFIRMED":
        assert details["minutes_held"] == 5


def test_buy_confirmation_counts_only_exchange_minutes_across_lunch():
    start = datetime(2026, 9, 4, 11, 28, tzinfo=VN_TZ)
    end = datetime(2026, 9, 4, 13, 3, tzinfo=VN_TZ)
    assert active_trading_minutes(start, end, "HOSE") == 5
    params = StaticRuleParameters(
        buy_confirmation_enabled=True, buy_confirmation_minutes=5,
    )
    state, _, _ = _advance({}, start, params, raw=True)
    _state, status, _details = _advance(state, end, params)
    assert status == "CONFIRMED"


def test_buy_confirmation_restarts_when_its_rule_changes():
    start = datetime(2026, 9, 4, 9, 20, tzinfo=VN_TZ)
    original = StaticRuleParameters(
        buy_confirmation_enabled=True, buy_confirmation_minutes=5,
    )
    state, status, _ = _advance({}, start, original, raw=True)
    assert status == "WAITING"

    changed = StaticRuleParameters(
        buy_confirmation_enabled=True, buy_confirmation_minutes=10,
    )
    state, status, details = _advance(
        state, start.replace(minute=25), changed, raw=True,
    )

    assert status == "WAITING"
    assert details["minutes_held"] == 0
    assert state["started_at"] == start.replace(minute=25).isoformat()


def test_buy_confirmation_counts_only_declared_working_days():
    friday = datetime(2026, 9, 4, 14, 43, tzinfo=VN_TZ)
    monday = datetime(2026, 9, 7, 9, 3, tzinfo=VN_TZ)
    assert active_trading_minutes(friday, monday, "HNX") == 5
    assert active_trading_minutes(
        friday, monday, "HNX", working_dates=["2026-09-04"],
    ) == 2


def test_buy_confirmation_state_survives_store_restart(tmp_path):
    path = tmp_path / "rule-state.json"
    first = RuleStateStore(path)
    first.save_buy_confirmation("SHS", "PAPER", {"active": True, "started_at": "2026-09-04T09:00:00+07:00"})
    second = RuleStateStore(path)
    assert second.buy_confirmation("SHS", "PAPER")["active"] is True
    assert second.buy_confirmation("SHS", "REAL") == {}


def test_buy_confirmation_never_delays_stop_loss_sell():
    rule = StaticRule(StaticRuleParameters(
        buy_confirmation_enabled=True,
        buy_confirmation_minutes=120,
    ))
    decision = rule.evaluate({
        "symbol": "FPT",
        "exchange": "HOSE",
        "bars": [
            {"time": index, "close": 100.0 if index < 20 else 95.0, "closed": True}
            for index in range(21)
        ],
        "signal_mode": "CLOSED",
        "precomputed_market": {"candidate": "ACCUMULATION", "details": {}},
        "confirmed_market_state": "ACCUMULATION",
    }, {
        "position_quantity": 100,
        "position": {
            "quantity": 100,
            "avg_price": 100.0,
            "current_price": 95.0,
            "managed_by_app": True,
            "managed_by_bot": True,
        },
    })
    assert decision.action == "SELL"
    assert decision.event == "STOP_LOSS"


def test_order_queue_uses_each_symbols_phase(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    queue.add(OrderIntent.create("SHS", "BUY", 100, "MARKET", execution_mode="PAPER"))
    queue.add(OrderIntent.create(
        "FPT", "BUY", 100, "MARKET", execution_mode="PAPER", allow_ato=True,
    ))
    queue.add(OrderIntent.create("VIC", "BUY", 100, "MARKET", execution_mode="PAPER"))
    phases = {"SHS": "OPEN", "FPT": "ATO", "VIC": "ATO"}
    due = queue.claim_due(
        phase="CLOSED", execution_mode="PAPER", token_ready=True,
        phase_provider=lambda symbol: phases[symbol],
    )
    assert {intent.symbol for intent in due} == {"SHS", "FPT"}


@pytest.mark.parametrize("mode", ["DAILY", "AUTO_HYBRID"])
def test_minute_confirmation_rejects_non_replay_modes(tmp_path, mode):
    settings = BacktestConfig(
        ["FPT"], "2026-09-03", "2026-09-04", simulation_mode=mode,
        rule_parameters={
            "buy_confirmation_enabled": True,
            "buy_window_enabled": False,
        },
    )
    with pytest.raises(RuntimeError, match="chỉ chạy với MODE 2"):
        data = HistoricalDataStore(root=tmp_path / "data", fetcher=lambda *_args: None)
        BacktestEngine(data, ReplayDataStore(tmp_path / "replay")).run(settings)
