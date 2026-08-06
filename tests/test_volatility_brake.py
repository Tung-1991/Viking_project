from types import SimpleNamespace
import time

from core import storage_manager
from core.checklist_manager import ChecklistManager
from core.volatility_brake import VolatilityBrakeDetector, settings_from_safeguard
from ai_advisor import scan_report
from bot_daemon import StandaloneBotDaemon


def _settings(**overrides):
    data = {
        "VOLATILITY_BRAKE_ENABLED": True,
        "VOLATILITY_BRAKE_SYMBOLS": ["AAA", "VN30F1M"],
        "VOLATILITY_BRAKE_ACTION": "ALERT_ONLY",
        "VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES": 240,
        "VOLATILITY_BRAKE_TELEGRAM_ENABLED": False,
        "VOLATILITY_BRAKE_WINDOW_SECONDS": 60,
        "VOLATILITY_BRAKE_STOCK_PCT": 1.5,
        "VOLATILITY_BRAKE_DERIVATIVE_POINTS": 5,
        "VOLATILITY_BRAKE_CONFIRMATIONS": 2,
        "VOLATILITY_BRAKE_SESSION_ENABLED": True,
        "VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS": 20,
        "VOLATILITY_BRAKE_SESSION_STOCK_PCT": 3,
    }
    data.update(overrides)
    return data


def test_stock_brake_triggers_after_two_confirmations():
    detector = VolatilityBrakeDetector()
    assert detector.observe("AAA", 100.0, _settings(), timestamp=1000) is None
    assert detector.observe("AAA", 98.4, _settings(), timestamp=1030) is None
    event = detector.observe("AAA", 98.3, _settings(), timestamp=1031)

    assert event["event"] == "VOLATILITY_BRAKE"
    assert event["symbol"] == "AAA"
    assert event["direction"] == "DOWN"
    assert event["threshold_unit"] == "PERCENT"


def test_derivative_brake_uses_points_and_ignores_stale_tick():
    detector = VolatilityBrakeDetector()
    cfg = _settings(VOLATILITY_BRAKE_CONFIRMATIONS=1)
    assert detector.observe("VN30F1M", 1900.0, cfg, timestamp=1000) is None
    assert (
        detector.observe(
            "VN30F1M", 1890.0, cfg, timestamp=1030, freshness="STALE"
        )
        is None
    )
    event = detector.observe("VN30F1M", 1894.0, cfg, timestamp=1031)

    assert event["threshold_unit"] == "POINTS"
    assert event["change_points"] == -6.0


def test_unlisted_symbol_never_triggers():
    detector = VolatilityBrakeDetector()
    cfg = _settings(
        VOLATILITY_BRAKE_SYMBOLS=["VN30F1M"],
        VOLATILITY_BRAKE_CONFIRMATIONS=1,
    )
    assert detector.observe("AAA", 100.0, cfg, timestamp=1000) is None
    assert detector.observe("AAA", 90.0, cfg, timestamp=1030) is None


def test_derivative_session_move_catches_gradual_trend_missed_by_60_seconds():
    detector = VolatilityBrakeDetector()
    cfg = _settings(
        VOLATILITY_BRAKE_SYMBOLS=["VN30F1M"],
        VOLATILITY_BRAKE_CONFIRMATIONS=2,
    )
    assert detector.observe("VN30F1M", 1820.0, cfg, timestamp=1_000) is None
    assert detector.observe("VN30F1M", 1824.0, cfg, timestamp=1_120) is None
    assert detector.observe("VN30F1M", 1832.0, cfg, timestamp=1_240) is None
    event = detector.observe("VN30F1M", 1840.0, cfg, timestamp=1_360)

    assert event["movement_type"] == "SESSION"
    assert event["direction"] == "UP"
    assert event["change_points"] == 20.0
    assert event["window_seconds"] == 360.0


def test_session_anchor_resets_on_new_market_day():
    detector = VolatilityBrakeDetector()
    cfg = _settings(
        VOLATILITY_BRAKE_SYMBOLS=["VN30F1M"],
        VOLATILITY_BRAKE_CONFIRMATIONS=2,
    )
    assert detector.observe("VN30F1M", 1820.0, cfg, timestamp=1_000) is None
    # Sang ngày khác không được so với giá phiên trước.
    assert detector.observe("VN30F1M", 1850.0, cfg, timestamp=90_000) is None


def test_invalid_action_falls_back_to_alert_only():
    cfg = settings_from_safeguard(
        {"VOLATILITY_BRAKE_ENABLED": True, "VOLATILITY_BRAKE_ACTION": "WRONG"}
    )
    assert cfg["VOLATILITY_BRAKE_ACTION"] == "ALERT_ONLY"


def test_old_default_cooldown_migrates_but_custom_value_is_preserved():
    migrated = settings_from_safeguard(
        {"VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES": 240}
    )
    custom = settings_from_safeguard(
        {"VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES": 45}
    )
    assert migrated["VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES"] == 30
    assert custom["VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES"] == 45


def test_volatility_cooldown_is_separate_for_up_and_down():
    daemon = StandaloneBotDaemon.__new__(StandaloneBotDaemon)
    now = time.time()
    daemon._volatility_symbol_cooldowns = {
        "VN30F1M|UP": now + 60,
    }
    assert daemon._volatility_symbol_on_cooldown("VN30F1M", "UP")
    assert not daemon._volatility_symbol_on_cooldown("VN30F1M", "DOWN")


class _Connector:
    _is_connected = True
    last_latency_ms = 0.0

    def __init__(self):
        self.positions = [
            SimpleNamespace(symbol="AAA", comment="[BOT]", magic=0, type=0),
            SimpleNamespace(symbol="POW", comment="[BOT]", magic=0, type=0),
            SimpleNamespace(symbol="MBS", comment="[BOT]", magic=0, type=0),
        ]

    def get_all_open_positions(self):
        return list(self.positions)


def _state():
    state = {
        "date": storage_manager.get_today_str(),
        "starting_balance": 100_000_000,
        "bot_pnl_today": 0,
        "bot_trades_today": 0,
        "bot_daily_loss_count": 0,
    }
    storage_manager.apply_state_defaults(state)
    return state


def _safeguard():
    return {
        "MAX_DAILY_LOSS_PERCENT": 2.5,
        "MAX_OPEN_POSITIONS": 3,
        "MAX_TRADES_PER_DAY": 30,
        "MAX_LOSING_STREAK": 3,
        "MAX_POS_PER_SYMBOL": 1,
        "CHECK_PING": False,
        "CHECK_SPREAD": False,
        "COOLDOWN_MINUTES": 0,
    }


def test_priority_bypasses_only_total_open_position_limit(monkeypatch):
    monkeypatch.setattr(
        "core.checklist_manager.is_symbol_trade_window_open",
        lambda _symbol: (True, ""),
    )
    manager = ChecklistManager(_Connector())

    normal = manager.run_bot_safeguard_checks(
        {"balance": 100_000_000},
        _state(),
        "FPT",
        _safeguard(),
        "ENTRY",
        "BUY",
        priority_symbol=False,
    )
    priority = manager.run_bot_safeguard_checks(
        {"balance": 100_000_000},
        _state(),
        "FPT",
        _safeguard(),
        "ENTRY",
        "BUY",
        priority_symbol=True,
    )

    assert not normal["passed"]
    assert priority["passed"]
    assert any(item["name"] == "Priority" for item in priority["checks"])


def test_priority_does_not_bypass_global_cooldown(monkeypatch):
    monkeypatch.setattr(
        "core.checklist_manager.is_symbol_trade_window_open",
        lambda _symbol: (True, ""),
    )
    state = _state()
    state["cooldown_until"] = time.time() + 300

    result = ChecklistManager(_Connector()).run_bot_safeguard_checks(
        {"balance": 100_000_000},
        state,
        "FPT",
        _safeguard(),
        "ENTRY",
        "BUY",
        priority_symbol=True,
    )

    assert not result["passed"]
    assert result["checks"][0]["name"] == "Global Cooldown"


def test_md_event_only_updates_existing_reports(monkeypatch, tmp_path):
    manual = tmp_path / "scan_report.md"
    manual.write_text("# Current\n", encoding="utf-8")
    monkeypatch.setattr(scan_report.paths, "scan_report_path", lambda: str(manual))
    event = {
        "symbol": "AAA",
        "direction": "DOWN",
        "change_pct": -2.0,
        "threshold_unit": "PERCENT",
        "window_seconds": 60,
        "triggered_at": 1000,
        "closed_positions": 1,
        "failed_positions": 0,
        "cooldown_hours": 4,
    }

    updated = scan_report.append_volatility_event_to_existing_reports(event)

    assert updated == [str(manual)]
    assert "PHANH BIẾN ĐỘNG" in manual.read_text(encoding="utf-8")


def test_alert_only_does_not_touch_orders_or_global_cooldown(monkeypatch):
    daemon = StandaloneBotDaemon.__new__(StandaloneBotDaemon)
    daemon._volatility_brake_lock = __import__("threading").Lock()
    daemon._volatility_brake_inflight = True
    daemon._active_symbols = ["VN30F1M"]
    daemon.pending_signals = [{"symbol": "VN30F1M"}]
    daemon.connector = SimpleNamespace(get_all_open_positions=lambda: [])
    state = _state()
    state["cooldown_until"] = 0.0
    saved = {}

    monkeypatch.setattr(storage_manager, "load_state", lambda: state)
    monkeypatch.setattr(
        storage_manager, "save_state", lambda value: saved.update(value)
    )
    monkeypatch.setattr(
        daemon,
        "_arm_volatility_global_cooldown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("alert-only must not arm cooldown")
        ),
    )
    monkeypatch.setattr(
        daemon,
        "_cancel_pending_entries_for_brake",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("alert-only must not cancel orders")
        ),
    )
    monkeypatch.setattr(
        daemon,
        "_close_all_for_volatility_brake",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("alert-only must not close positions")
        ),
    )
    monkeypatch.setattr(
        scan_report, "append_volatility_event_to_existing_reports", lambda _event: []
    )

    daemon._run_volatility_brake_action(
        {
            "symbol": "VN30F1M",
            "direction": "DOWN",
            "change_points": -6.0,
            "change_pct": -0.3,
            "threshold_unit": "POINTS",
            "window_seconds": 60,
            "triggered_at": time.time(),
        },
        _settings(VOLATILITY_BRAKE_ACTION="ALERT_ONLY"),
    )

    assert daemon.pending_signals == [{"symbol": "VN30F1M"}]
    assert saved["cooldown_until"] == 0.0
    assert saved["active_brake"]["global"] is None
    assert saved["volatility_events"][-1]["action"] == "ALERT_ONLY"
