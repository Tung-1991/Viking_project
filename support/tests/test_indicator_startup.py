"""Missing observations must wait, never crash or manufacture a crossover."""
from itertools import product
from datetime import timedelta
import time

import pytest

from viking_v2.rules.business import StaticRule, StaticRuleParameters, crossover_signal_from_snapshots, indicator_snapshot
from viking_v2.rules.state import RuleStateStore


def _current():
    return {
        "buy_ema_fast": 10.1, "buy_ema_slow": 10.0,
        "sell_ema_fast": 10.1, "sell_ema_slow": 10.0,
        "rsi": 55.0, "rsi_previous": 50.0,
        "sample_count": 30,
        "buy_ema_fast_period": 3, "buy_ema_slow_period": 6,
        "sell_ema_fast_period": 3, "sell_ema_slow_period": 6,
        "rsi_period": 14,
    }


def _previous():
    return {**_current(), "buy_ema_fast": 9.9, "sell_ema_fast": 9.9}


def test_live_ticks_change_today_rsi_but_keep_previous_completed_daily_rsi():
    from viking_v2.services.daemon import indicator_snapshot_at_close

    bars = [{"close": value, "closed": True} for value in
            ([20.0] * 16 + [20.2, 20.1, 20.3, 20.2, 20.4, 20.3])]
    previous_daily = indicator_snapshot(bars)["rsi"]
    bars.append({"close": 20.5, "closed": False})
    before = [dict(row) for row in bars]
    params = StaticRuleParameters()
    higher = indicator_snapshot_at_close(bars, 20.6, params)
    lower = indicator_snapshot_at_close(bars, 20.2, params)
    assert higher["rsi_previous"] == lower["rsi_previous"] == previous_daily
    assert higher["rsi"] > previous_daily > lower["rsi"]
    assert bars == before  # Each tick replaces the provisional daily close, not daily history.
    current = {**_current(), "rsi": 55.0, "rsi_previous": 50.0}
    previous_tick = {**_previous(), "rsi": 80.0}
    assert crossover_signal_from_snapshots(current, previous_tick) == "BUY"
    # Current RSI fell versus the prior tick, but is still above the daily baseline.


@pytest.mark.parametrize("previous", [None, {}, {"buy_ema_fast": None, "buy_ema_slow": None}])
@pytest.mark.parametrize("prefer", ["BUY", "SELL"])
@pytest.mark.parametrize("require_cross,expected", [(False, "BUY"), (True, "")])
def test_first_tick_without_previous_ema_obeys_optional_cross_filter(previous, prefer, require_cross, expected):
    assert crossover_signal_from_snapshots(
        _current(), previous, prefer=prefer, buy_signal_require_ema_cross=require_cross,
    ) == expected


@pytest.mark.parametrize("flags", list(product((False, True), repeat=4)))
def test_empty_history_is_safe_for_every_indicator_toggle(flags):
    buy_ema, buy_rsi, sell_ema, sell_rsi = flags
    assert crossover_signal_from_snapshots(
        indicator_snapshot([]), {},
        buy_use_ema=buy_ema, buy_use_rsi=buy_rsi,
        sell_use_ema=sell_ema, sell_use_rsi=sell_rsi,
    ) == ""


@pytest.mark.parametrize("source", ["current", "previous"])
@pytest.mark.parametrize("key", ["buy_ema_fast", "buy_ema_slow"])
@pytest.mark.parametrize("bad_value", [None, "invalid", float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("require_cross", [False, True])
def test_missing_or_invalid_ema_is_required_only_for_the_selected_buy_mode(source, key, bad_value, require_cross):
    current, previous = _current(), _previous()
    (current if source == "current" else previous)[key] = bad_value
    expected = "" if source == "current" or require_cross else "BUY"
    assert crossover_signal_from_snapshots(current, previous, buy_signal_require_ema_cross=require_cross) == expected


@pytest.mark.parametrize("key", ["rsi", "rsi_previous"])
@pytest.mark.parametrize("bad_value", [None, "invalid", float("nan"), float("inf"), -float("inf")])
def test_invalid_required_rsi_cannot_generate_buy(key, bad_value):
    current = _current()
    current[key] = bad_value
    assert crossover_signal_from_snapshots(current, _previous()) == ""


def test_missing_sell_ema_does_not_suppress_a_valid_buy():
    previous = _previous()
    previous.pop("sell_ema_fast")
    previous.pop("sell_ema_slow")
    assert crossover_signal_from_snapshots(_current(), previous) == "BUY"


def test_missing_buy_ema_does_not_suppress_a_valid_sell():
    current = {**_current(), "sell_ema_fast": 9.9, "rsi": 45.0}
    previous = {**_previous(), "sell_ema_fast": 10.1}
    previous.pop("buy_ema_fast")
    previous.pop("buy_ema_slow")
    assert crossover_signal_from_snapshots(current, previous, prefer="SELL") == "SELL"


def test_rsi_only_signal_does_not_require_previous_ema():
    assert crossover_signal_from_snapshots(
        _current(), {}, buy_use_ema=False, sell_use_ema=False,
    ) == "BUY"


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("require_cross", [False, True])
def test_cold_stream_accepts_levels_or_requires_a_cross_and_resets_on_period_change(tmp_path, mode, require_cross):
    state = RuleStateStore(tmp_path / "rules.json")
    baseline = _previous()
    prior = state.observe_indicators("MSN", mode, "2026-10-08", baseline)
    assert prior == {}
    assert crossover_signal_from_snapshots(baseline, prior, buy_signal_require_ema_cross=require_cross) == ""
    current = _current()
    prior = state.observe_indicators("MSN", mode, "2026-10-08", current)
    assert crossover_signal_from_snapshots(current, prior, buy_signal_require_ema_cross=require_cross) == "BUY"
    changed = {**current, "buy_ema_slow_period": 10}
    prior = state.observe_indicators("MSN", mode, "2026-10-08", changed)
    assert prior == {}
    assert crossover_signal_from_snapshots(changed, prior, buy_signal_require_ema_cross=require_cross) == ("" if require_cross else "BUY")


@pytest.mark.parametrize("require_cross,expected", [(False, "BUY"), (True, "WAIT")])
def test_actual_rule_keeps_indicators_visible_while_initialising_first_tick(require_cross, expected):
    bars = [{"close": value, "closed": True} for value in [100] * 15 + [99, 98, 97, 98, 100]]
    snapshot = indicator_snapshot(bars)
    decision = StaticRule(StaticRuleParameters(buy_signal_require_ema_cross=require_cross)).evaluate(
        {
            "symbol": "MSN", "bars": bars, "signal_mode": "REALTIME",
            "indicator_snapshot": snapshot, "previous_indicators": {},
            "previous_market_state": "UPTREND",
        },
        {"available_capital": 100_000_000, "open_positions": 0},
    )
    assert decision.action == expected
    assert decision.signal == ("BUY" if expected == "BUY" else "")
    assert decision.details["indicators"] == snapshot


@pytest.mark.parametrize("position,event", [
    ({"current_price": 96.4}, "STOP_LOSS"),
    ({"current_price": 108, "em_modes": ["TP"]}, "TAKE_PROFIT"),
    ({"current_price": 103.5, "peak_profit_pct": 7.2, "normal_armed": True, "em_modes": ["NORMAL"]}, "PRICE_PROTECTION"),
])
def test_initialising_ema_does_not_disable_existing_position_exits(position, event):
    bars = [{"close": 100, "closed": True} for _ in range(30)]
    decision = StaticRule().evaluate(
        {
            "symbol": "MSN", "bars": bars, "signal_mode": "REALTIME",
            "indicator_snapshot": indicator_snapshot(bars), "previous_indicators": {},
            "previous_market_state": "UPTREND",
        },
        {"position": {"quantity": 100, "avg_price": 100, **position}},
    )
    assert decision.action == "SELL"
    assert decision.event == event
    assert decision.quantity_fraction == 1.0


def _run_two_daemon_cycles(monkeypatch, tmp_path, *, mode="REAL", phase="OPEN", interval="TICK", fault="", cached_tick=None, source="WS"):
    from viking_v2 import config
    from viking_v2.models import RuntimeConfig
    from viking_v2.services import daemon
    from viking_v2.services.runtime import RuntimeBridge
    from viking_v2.trading.market import market_now
    from viking_v2.trading.orders import OrderQueue

    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    bridge = RuntimeBridge("COLD_START")
    symbols = ["MSN", "CTS", "HDB", "IDC"]
    bridge.write_config(RuntimeConfig(symbols, mode == "PAPER", False))
    if cached_tick is not None:
        bridge.status_store.write({"ticks": {
            symbol: {"symbol": symbol, "price": 99, "timestamp": 1, **cached_tick}
            for symbol in symbols
        }})
    settings = config.AppSettings(
        watchlist=symbols, priority_symbols=[], paper_mode=mode == "PAPER",
        signal_mode="REALTIME", realtime_indicator_interval=interval,
    )
    monkeypatch.setattr(daemon, "load_settings", lambda *_args: settings)
    handlers, statuses, cycles = {}, [], []
    monkeypatch.setattr(daemon.signal, "signal", lambda sig, handler: handlers.update(stop=handler))
    monkeypatch.setattr(daemon, "market_phase", lambda *args, **kwargs: (phase, phase))
    original_write = RuntimeBridge.write_status

    def publish(self, status):
        statuses.append(status.to_dict())
        original_write(self, status)

    def next_cycle(*_args):
        cycles.append(True)
        if len(cycles) >= 2:
            handlers["stop"]()

    monkeypatch.setattr(RuntimeBridge, "write_status", publish)
    monkeypatch.setattr(daemon.time, "sleep", next_cycle)
    today = market_now().date()
    rows = [
        {
            "time": (today - timedelta(days=29 - index)).isoformat(),
            "open": 100, "high": 101, "low": 99, "close": 100,
            "volume": 1_000_000, "closed": index < 29,
        } for index in range(30)
    ]

    class Client:
        api_key, api_secret = "OFFLINE", "OFFLINE"

        def __init__(self, **kwargs): self.snapshots = 0
        def connect(self): return True
        def close(self): pass
        def get_working_dates(self): return [today.isoformat()]
        def get_secdef(self, symbol): return {"marketId": "STO"}
        def get_positions(self): return []

        def get_balance(self):
            self.snapshots += 1
            if fault == "ACCOUNT_SNAPSHOT" and self.snapshots == 1:
                raise RuntimeError("offline account fault")
            return {"stock": {"availableCash": 100_000_000}}

    class Market:
        def __init__(self, *args): self.observations = {}
        def start(self, symbols): pass
        def set_symbols(self, symbols): pass
        def stop(self): pass
        def health(self): return {}
        def get_daily_bars(self, symbol, **kwargs): return [dict(row) for row in rows]
        def get_tick(self, symbol):
            self.observations[symbol] = self.observations.get(symbol, 0) + 1
            tick = {"symbol": symbol, "price": 100, "bid": 100, "ask": 100, "timestamp": time.time(), "source": source}
            if symbol == "HDB" and self.observations[symbol] == 1:
                if fault == "NO_QUOTE":
                    return None
                tick.update({
                    "QUOTE_TOO_OLD": {"timestamp": 1},
                    "MARKED_STALE": {"stale": True},
                    "FROZEN": {"frozen": True},
                    "SOURCE_ERROR": {"health": "REST_UNAVAILABLE"},
                    "SYMBOL_MISMATCH": {"symbol": "CTS"},
                    "MISSING_TIMESTAMP": {"timestamp": None},
                }.get(fault, {}))
            return tick
        def frozen_tick_from_bars(self, symbol, bars):
            return {**self.get_tick(symbol), "frozen": True}

    monkeypatch.setattr(daemon, "DNSEClient", Client)
    monkeypatch.setattr(daemon, "DNSEMarketWS", lambda *_args: object())
    monkeypatch.setattr(daemon, "MarketDataService", Market)
    if fault == "RULE_EVALUATION":
        original_evaluate = StaticRule.evaluate
        failures = []

        def evaluate(self, context, portfolio):
            if context["symbol"] == "HDB" and not failures:
                failures.append(True)
                raise TypeError("offline rule fault")
            return original_evaluate(self, context, portfolio)

        monkeypatch.setattr(StaticRule, "evaluate", evaluate)
    assert daemon.run("COLD_START") == 0
    assert OrderQueue(bridge.pending_orders_path).list_all() == []
    return statuses, bridge


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("phase", ["OPEN", "CLOSED"])
@pytest.mark.parametrize("interval", ["TICK", "1M", "2M"])
def test_real_daemon_cold_start_publishes_four_symbols_without_cycle_errors(monkeypatch, tmp_path, mode, phase, interval):
    statuses, _ = _run_two_daemon_cycles(monkeypatch, tmp_path, mode=mode, phase=phase, interval=interval)
    completed = [status for status in statuses if status["daemon_status"] == "RUNNING" and status["decisions"]]
    assert len(completed) >= 2
    for status in completed:
        assert status["error"] == ""
        assert status["cycle_error_context"] == {}
        assert set(status["decisions"]) == {"MSN", "CTS", "HDB", "IDC"}
        for decision in status["decisions"].values():
            assert decision["action"] == "WAIT"
            assert decision["details"]["indicators"]["buy_ema_fast"] is not None
            assert decision["details"]["indicators"]["rsi"] is not None
            assert decision["details"]["atr14_daily_pct"] > 0


@pytest.mark.parametrize("fault", ["RULE_EVALUATION", "ACCOUNT_SNAPSHOT"])
def test_real_daemon_publishes_error_context_traceback_and_clears_after_recovery(monkeypatch, tmp_path, fault):
    statuses, bridge = _run_two_daemon_cycles(monkeypatch, tmp_path, fault=fault)
    failed = [status for status in statuses if status["error"]]
    assert len(failed) == 1
    context = failed[0]["cycle_error_context"]
    assert context["stage"] == fault
    assert context["execution_mode"] == "REAL"
    if fault == "RULE_EVALUATION":
        assert context["symbol"] == "HDB"
        assert set(failed[0]["decisions"]) == {"MSN", "CTS", "IDC"}
    recovered = [status for status in statuses if status["daemon_status"] == "RUNNING" and status["decisions"]][-1]
    assert recovered["error"] == ""
    assert recovered["cycle_error_context"] == {}
    log = (bridge.log_dir / "daemon.log").read_text(encoding="utf-8")
    assert "Traceback" in log
    assert "offline" in log


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("source", ["WS", "REST"])
def test_daemon_restart_with_failed_cached_ticks_resumes_decisions(monkeypatch, tmp_path, mode, source):
    statuses, bridge = _run_two_daemon_cycles(
        monkeypatch, tmp_path, mode=mode, source=source,
        cached_tick={"stale": True, "frozen": True, "received_at": 1,
                     "health": "REST_UNAVAILABLE", "quote_issue": "SOURCE_ERROR"},
    )
    completed = [status for status in statuses if status["daemon_status"] == "RUNNING" and status["decisions"]]
    assert len(completed) == 2
    for status in completed:
        assert set(status["decisions"]) == {"MSN", "CTS", "HDB", "IDC"}
        assert status["error"] == ""
        for tick in status["ticks"].values():
            assert tick["source"] == source
            assert not tick.get("stale") and not tick.get("frozen")
            assert "health" not in tick and "quote_issue" not in tick and "received_at" not in tick
    assert "BỊ LOẠI" not in (bridge.log_dir / "daemon.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("fault", [
    "QUOTE_TOO_OLD", "MARKED_STALE", "FROZEN", "SOURCE_ERROR", "NO_QUOTE",
    "SYMBOL_MISMATCH", "MISSING_TIMESTAMP",
])
def test_daemon_rejects_bad_live_quote_then_recovers_and_logs_once(monkeypatch, tmp_path, fault):
    statuses, bridge = _run_two_daemon_cycles(monkeypatch, tmp_path, fault=fault)
    completed = [status for status in statuses if status["daemon_status"] == "RUNNING" and status["decisions"]]
    assert len(completed) == 2
    assert set(completed[0]["decisions"]) == {"MSN", "CTS", "IDC"}
    if fault == "NO_QUOTE":
        assert "HDB" not in completed[0]["ticks"]
    else:
        assert completed[0]["ticks"]["HDB"]["stale"]
        assert completed[0]["ticks"]["HDB"]["quote_issue"] == fault
    assert set(completed[1]["decisions"]) == {"MSN", "CTS", "HDB", "IDC"}
    assert not completed[1]["ticks"]["HDB"].get("stale")
    log = (bridge.log_dir / "daemon.log").read_text(encoding="utf-8")
    assert log.count("BỊ LOẠI") == 1
    assert log.count("PHỤC HỒI") == 1
