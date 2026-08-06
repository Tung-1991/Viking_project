from datetime import datetime

from core.trade_manager import validate_advisory_levels
from telegram_notify import opportunity_alerts, settings, system_alerts
from telegram_notify.client import TelegramClient


class _FakeTimer:
    def __init__(self, delay, callback, kwargs=None):
        self.delay = delay
        self.callback = callback
        self.kwargs = kwargs or {}
        self.daemon = False

    def start(self):
        return None

    def cancel(self):
        return None


def _configure(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(opportunity_alerts, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(opportunity_alerts, "_market_session_open", lambda _symbol: True)
    monkeypatch.setattr(system_alerts, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(opportunity_alerts.threading, "Timer", _FakeTimer)
    # Tách rule thanh khoản khỏi các bài test mức giá, phiên và formatter.
    monkeypatch.setattr(
        "ai_advisor.scan_cache.liquidity_filter_allows", lambda _symbol: True
    )
    settings.save_settings(
        {
            "opportunity_alerts_enabled": True,
            "opportunity_chat_id": "-1002",
            "report_chat_id": "-1001",
            "system_alerts_enabled": True,
            "opportunity_ckps_enabled": True,
            "opportunity_ckcs_enabled": True,
            "opportunity_batch_minutes": 0.5,
        }
    )
    opportunity_alerts.reset_runtime_state()
    system_alerts._HEALTH.clear()


def _item(symbol="AAA", side="SELL", market="CKCS", quantity=0):
    return {
        "id": f"{symbol}-{side}",
        "symbol": symbol,
        "side": side,
        "market_type": market,
        "order_setup": {
            "ok": True,
            "price": 10.0,
            "sl": 12.0 if side == "SELL" else 8.0,
            "tp": 7.0 if side == "SELL" else 14.0,
            "lot": quantity,
            "display_quantity": quantity,
            "quantity_unit": "HĐ" if market == "CKPS" else "CP",
        },
        "context": {
            "group_details": {
                "G0": {"B": 0 if side == "SELL" else 4, "S": 4 if side == "SELL" else 0, "N": 3, "status": -1 if side == "SELL" else 1},
            },
            "group_rules": {"G0": "FIX"},
        },
    }


def test_level_validation_rejects_negative_or_wrong_direction():
    assert validate_advisory_levels("BUY", 10, 8, 14)
    assert validate_advisory_levels("SELL", 10, 12, 7)
    assert not validate_advisory_levels("SELL", 10, 12, -1)
    assert not validate_advisory_levels("SELL", 10, 12, 14)


def test_formatter_is_compact_and_uses_preview_levels(monkeypatch):
    monkeypatch.setattr(opportunity_alerts, "_priority_symbols", lambda: {"TDM"})
    monkeypatch.setattr(
        "core.storage_manager.get_brain_settings_for_symbol",
        lambda _symbol: {
            "G0_TIMEFRAME": "1d",
            "G1_TIMEFRAME": "1h",
            "indicators": {
                "ema": {"active": True, "groups": ["G0"]},
                "swing_point": {"active": True, "groups": ["G1"]},
            },
        },
    )
    text = opportunity_alerts.format_digest(
        [
            _item("VN30F1M", "SELL", "CKPS", 1),
            _item("TDM", "BUY", "CKCS", 100),
            _item("AAA", "SELL", "CKCS", 0),
        ],
        now=datetime(2026, 7, 27, 9, 30),
    )
    assert text.index("VN30F1M") < text.index("TDM") < text.index("AAA")
    assert "🔴 VN30F1M SHORT | Giá 10 | Entry NOW @10 (1 HĐ) | SL 12 | TP1 7" in text
    assert "⭐ PRIORITY" in text
    assert "🟢 TDM BUY | Giá 10 | Entry NOW @10 (100 CP) | CẮT 8 | TP1 14" in text
    assert "🔴 CKCS SELL" in text
    assert "AAA | Giá 10 | Entry NOW @10 | CẮT 12 | TP1 7" in text
    assert "G0 SELL 4/7 FIX" not in text
    assert "📊 TDM | G0 1D" in text
    assert "📊 AAA | G0 1D" in text
    assert "[📈 Exponential Moving Average 50]" in text
    assert "[↕️ Swing Point]" in text
    assert "PAPER" not in text and "REAL" not in text and "BOT_OFF" not in text
    assert text.startswith("⚡ CKPS")
    assert "RAT6 GỢI Ý BOT" not in text


def test_formatter_separates_current_price_from_entry_zone(monkeypatch):
    monkeypatch.setattr(opportunity_alerts, "_priority_symbols", lambda: set())
    item = _item("AAA", "BUY", "CKCS", 100)
    item["order_setup"]["current_price"] = 10.2
    item["order_setup"]["entry_price"] = 10.0
    item["order_setup"]["entry_zone"] = [9.8, 10.0]

    text = opportunity_alerts.format_digest([item])

    assert "🟢 CKCS BUY" in text
    assert "AAA | Giá 10.2 | Entry NOW 9.8-10 (100 CP)" in text


def test_formatter_uses_full_dynamic_indicator_names_entry_tactic_and_g2(monkeypatch):
    monkeypatch.setattr(opportunity_alerts, "_priority_symbols", lambda: set())
    monkeypatch.setattr(
        "core.storage_manager.get_brain_settings_for_symbol",
        lambda _symbol: {
            "G0_TIMEFRAME": "1h",
            "G1_TIMEFRAME": "15m",
            "G2_TIMEFRAME": "5m",
            "G3_TIMEFRAME": "1m",
            "voting_rules": {
                "G0": {"master_rule": "PASS"},
                "G1": {"master_rule": "FIX"},
                "G2": {"master_rule": "PASS"},
                "G3": {"master_rule": "IGNORE"},
            },
            "indicators": {
                "adx": {"active": True, "groups": ["G0", "G1"]},
                "ema": {
                    "active": True,
                    "groups": ["G0", "G1"],
                    "params": {"period": 50},
                    "group_params": {
                        "G0": {"period": 20},
                        "G1": {"period": 20},
                    },
                },
                "supertrend": {"active": True, "groups": ["G0", "G1"]},
                "macd": {"active": True, "groups": ["G1"]},
                "simple_breakout": {"active": True, "groups": ["G2"]},
            },
        },
    )
    item = _item("VN30F1M", "BUY", "CKPS", 1)
    item["context"] = {
        "group_timeframes": {"G0": "1h", "G1": "15m", "G2": "5m", "G3": "1m"},
        "group_rules": {"G0": "PASS", "G1": "FIX", "G2": "PASS", "G3": "IGNORE"},
        "group_details": {
            "G0": {"B": 3, "S": 0, "N": 0, "status": 1},
            "G1": {"B": 4, "S": 0, "N": 0, "status": 1},
            "G2": {"B": 1, "S": 0, "N": 0, "status": 1},
            "G3": {"B": 0, "S": 0, "N": 0, "status": 0},
        },
    }
    item["order_setup"]["entry_exit_tactic"] = "SWING_REJECTION->FALLBACK_R"

    text = opportunity_alerts.format_digest([item])

    assert (
        "G0 1H 🟢▲ 3/3 "
        "[🧭 Average Directional Index · 📈 Exponential Moving Average 20 · 🚦 Supertrend]"
    ) in text
    assert (
        "G1 15M 🟢▲ 4/4 "
        "[🧭 Average Directional Index · 📈 Exponential Moving Average 20 · "
        "🚦 Supertrend · 🌊 Moving Average Convergence Divergence]"
    ) in text
    assert "G2 5M 🟢▲ 1/1 [🚀 Simple Breakout]" in text
    assert "G3 " not in text
    assert "Exponential Moving Average 50" not in text
    assert "Entry RETEST @10" in text


def test_invalid_levels_are_archived_and_not_queued(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    archived = []
    monkeypatch.setattr(opportunity_alerts, "_archive_invalid", lambda item: archived.append(item["symbol"]))
    item = _item()
    item["order_setup"]["tp"] = -1

    result = opportunity_alerts.queue_opportunity(item)

    assert result["reason"] == "invalid_levels"
    assert archived == ["AAA"]


def test_old_preview_cache_is_not_reused_or_sent(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    archived = []
    monkeypatch.setattr(
        opportunity_alerts,
        "_archive_invalid",
        lambda item: archived.append(item["symbol"]),
    )
    item = _item()
    item["order_setup"]["preview_source"] = True

    result = opportunity_alerts.queue_opportunity(item)

    assert result["reason"] == "invalid_levels"
    assert archived == ["AAA"]


def test_non_level_setup_failure_is_not_archived(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    archived = []
    monkeypatch.setattr(opportunity_alerts, "_archive_invalid", lambda item: archived.append(item["symbol"]))
    item = _item()
    item["order_setup"] = {"ok": False, "error": "NO_ACCOUNT"}

    result = opportunity_alerts.queue_opportunity(item)

    assert result["reason"] == "setup_unavailable"
    assert archived == []


def test_opportunity_is_not_sent_outside_market_session(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(opportunity_alerts, "_market_session_open", lambda _symbol: False)

    result = opportunity_alerts.queue_opportunity(_item("AAA", "BUY"))

    assert result["reason"] == "outside_session"


def test_daily_digest_is_optional_and_defaults_off(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    current = settings.load_settings()
    current["opportunity_daily_digest_enabled"] = False
    settings.save_settings(current)

    result = opportunity_alerts.run_scheduled_digest(
        now=datetime(2026, 7, 27, 9, 30)
    )

    assert result["reason"] == "daily_digest_disabled"


def test_enabled_daily_digest_sends_morning_and_eod_once(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    current = settings.load_settings()
    current["opportunity_daily_digest_enabled"] = True
    settings.save_settings(current)
    sent = []
    monkeypatch.setattr(
        "core.market_calendar.date_status",
        lambda _now=None: {"status": "TRADING"},
    )
    monkeypatch.setattr(
        TelegramClient,
        "send_long_message",
        lambda self, chat_id, text, chunk_size=3500, title="": (
            sent.append(text) or {"ok": True}
        ),
    )
    opportunity_alerts.queue_opportunity(_item("AAA", "BUY"))

    morning = opportunity_alerts.run_scheduled_digest(
        now=datetime(2026, 7, 27, 9, 30)
    )
    duplicate = opportunity_alerts.run_scheduled_digest(
        now=datetime(2026, 7, 27, 10, 0)
    )
    opportunity_alerts.mark_wait("AAA")
    eod = opportunity_alerts.run_scheduled_digest(
        now=datetime(2026, 7, 27, 14, 50)
    )

    assert morning["ok"]
    assert duplicate["reason"] == "not_due"
    assert eod["ok"]
    assert len(sent) == 2


def test_volatility_alert_uses_opportunity_chat_even_when_suggestion_feed_is_off(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    current = settings.load_settings()
    current["opportunity_alerts_enabled"] = False
    settings.save_settings(current)
    sent = []
    monkeypatch.setattr(
        TelegramClient,
        "send_long_message",
        lambda self, chat_id, text, chunk_size=3500, title="": (
            sent.append((chat_id, text, title)) or {"ok": True}
        ),
    )

    result = opportunity_alerts.send_volatility_event(
        {
            "symbol": "VN30F1M",
            "direction": "DOWN",
            "change_points": -5.5,
            "threshold_unit": "POINTS",
            "window_seconds": 60,
            "reference_price": 1896.9,
            "current_price": 1891.4,
            "action": "ALERT_ONLY",
        }
    )

    assert result["ok"]
    assert sent[0][0] == "-1002"
    assert sent[0][1] == "🔴 VN30F1M | 1896.9→1891.4 | -5.50 điểm/60s"
    assert sent[0][2] == ""

    result_up = opportunity_alerts.send_volatility_event(
        {
            "symbol": "VN30F1M",
            "direction": "UP",
            "change_points": 5.1,
            "threshold_unit": "POINTS",
            "window_seconds": 58,
            "reference_price": 1829.4,
            "current_price": 1834.5,
            "action": "ALERT_ONLY",
        }
    )
    assert result_up["ok"]
    assert sent[1][1] == "🟢 VN30F1M | 1829.4→1834.5 | +5.10 điểm/58s"

    result_session = opportunity_alerts.send_volatility_event(
        {
            "symbol": "VN30F1M",
            "direction": "UP",
            "change_points": 20.0,
            "threshold_unit": "POINTS",
            "window_seconds": 3600,
            "movement_type": "SESSION",
            "reference_price": 1820.0,
            "current_price": 1840.0,
            "action": "ALERT_ONLY",
        }
    )
    assert result_session["ok"]
    assert sent[2][1] == "🟢 VN30F1M | 1820→1840 | +20.00 điểm/phiên"


def test_system_health_sends_once_after_60_seconds_and_once_on_recovery(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(system_alerts, "_send", lambda message, title="": sent.append(message) or {"ok": True})

    system_alerts.observe_health("MARKET DATA", "MARKET_DATA_DOWN", {"MARKET_DATA_DOWN"}, now=100)
    system_alerts.observe_health("MARKET DATA", "MARKET_DATA_DOWN", {"MARKET_DATA_DOWN"}, now=159)
    system_alerts.observe_health("MARKET DATA", "MARKET_DATA_DOWN", {"MARKET_DATA_DOWN"}, now=160)
    system_alerts.observe_health("MARKET DATA", "MARKET_DATA_DOWN", {"MARKET_DATA_DOWN"}, now=200)
    system_alerts.observe_health("MARKET DATA", "LIVE", {"MARKET_DATA_DOWN"}, now=201)
    system_alerts.observe_health("MARKET DATA", "LIVE", {"MARKET_DATA_DOWN"}, now=202)

    assert len(sent) == 2
    assert "kéo dài" in sent[0]
    assert "PHỤC HỒI" in sent[1]


def test_global_cooldown_extension_does_not_send_again(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(system_alerts, "_send", lambda message, title="": sent.append(message) or {"ok": True})

    first = system_alerts.observe_global_cooldown(1000, "BRAKE", now=100)
    extended = system_alerts.observe_global_cooldown(2000, "BRAKE", now=200)
    ended = system_alerts.observe_global_cooldown(0, "", now=2001)

    assert first["ok"]
    assert extended["reason"] == "unchanged"
    assert ended["ok"]
    assert len(sent) == 2
