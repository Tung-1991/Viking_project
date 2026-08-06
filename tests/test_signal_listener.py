# -*- coding: utf-8 -*-
from core.signal_listener import SignalListener


class FakeTradeManager:
    pass


def _listener():
    return SignalListener(
        trade_manager=FakeTradeManager(),
        get_auto_trade_cb=lambda: False,
        get_preset_cb=lambda: "",
        get_tsl_mode_cb=lambda: "",
        ui_heartbeat_cb=lambda payload: None,
        log_cb=lambda msg, error=False: None,
    )


def test_telegram_signal_phase_persists_across_restart(monkeypatch, tmp_path):
    monkeypatch.setattr("core.signal_listener._get_telegram_signal_phase_file", lambda: str(tmp_path / "phases.json"))
    sent = []
    listener = _listener()
    listener._save_telegram_signal_phase("ETHUSD", "BUY")

    def fake_send(*_args, **_kwargs):
        sent.append(True)
        return {"ok": True}

    monkeypatch.setattr("telegram_notify.signal_bridge.maybe_send_signal_proposal", fake_send)

    listener._process_signal({"signal_id": "S2", "symbol": "ETHUSD", "action": "BUY", "signal_class": "ENTRY"})
    assert sent == []

    listener._process_signal({"signal_id": "S3", "symbol": "ETHUSD", "action": "SELL", "signal_class": "ENTRY"})
    assert sent == [True]


def test_none_resets_telegram_signal_phase(monkeypatch, tmp_path):
    monkeypatch.setattr("core.signal_listener._get_telegram_signal_phase_file", lambda: str(tmp_path / "phases.json"))
    listener = _listener()
    listener._save_telegram_signal_phase("ETHUSD", "BUY")

    listener._process_signal({"signal_id": "S1", "symbol": "ETHUSD", "action": "NONE", "signal_class": "ENTRY"})

    assert listener._load_telegram_signal_phases() == {}


def test_auto_trade_gate_symbol_aware():
    """Callback symbol-aware: gate riêng theo nhóm mã (CKPS vs CKCS)."""
    # CKPS bật, CKCS tắt: VN30F1M -> True; cổ phiếu cơ sở (FPT) -> False.
    flags = {"VN30F1M": True, "FPT": False}
    listener = _listener()
    listener.get_auto_trade = lambda symbol=None: bool(flags.get(str(symbol or "").upper(), False))
    assert listener._auto_trade_for("VN30F1M") is True
    assert listener._auto_trade_for("FPT") is False


def test_auto_trade_gate_legacy_noarg():
    """Callback cũ no-arg vẫn chạy (tương thích ngược)."""
    listener = _listener()
    listener.get_auto_trade = lambda: True
    assert listener._auto_trade_for("VN30F1M") is True
    assert listener._auto_trade_for("FPT") is True


def test_opportunity_uses_bot_plan_callback():
    listener = _listener()
    called = []
    listener.build_opportunity_plan = lambda symbol, side, **kwargs: (
        called.append((symbol, side, kwargs))
        or {"ok": True, "price": 10, "sl": 9, "tp": 12}
    )

    result = listener._build_opportunity_setup(
        "AAA", "BUY", {"current_price": 10}, "TREND"
    )

    assert result["plan_source"] == "BOT"
    assert result["plan_version"] == 1
    assert called == [
        (
            "AAA",
            "BUY",
            {"context": {"current_price": 10}, "market_mode": "TREND"},
        )
    ]


def test_opportunity_does_not_fall_back_to_legacy_order_builder():
    listener = _listener()

    result = listener._build_opportunity_setup(
        "AAA", "BUY", {"current_price": 10}, "TREND"
    )

    assert result == {
        "ok": False,
        "error": "BOT_PLAN_SOURCE_UNAVAILABLE",
        "plan_source": "BOT",
        "plan_version": 1,
    }


def test_record_and_queue_opportunity_is_independent_of_bot_result(monkeypatch):
    listener = _listener()
    listener.build_opportunity_plan = lambda *_args, **_kwargs: {
        "ok": True,
        "price": 10,
        "sl": 9,
        "tp": 12,
    }
    recorded = []
    queued = []

    def fake_record(signal, block_reason, order_setup):
        item = {
            "symbol": signal["symbol"],
            "block_reason": block_reason,
            "order_setup": order_setup,
        }
        recorded.append(item)
        return item

    monkeypatch.setattr(
        "core.signal_opportunities.record_signal",
        fake_record,
    )
    monkeypatch.setattr(
        "telegram_notify.opportunity_alerts.queue_opportunity",
        lambda item, log_cb=None: queued.append(item) or {"ok": True},
    )

    result = listener._record_and_queue_opportunity(
        {"symbol": "VN30F1M", "signal_class": "ENTRY"},
        "BUY",
        {"current_price": 10},
        "TREND",
        block_reason="SUCCESS|PAPER-1",
    )

    assert result is recorded[0]
    assert queued == recorded
    assert recorded[0]["block_reason"] == "SUCCESS|PAPER-1"
    assert recorded[0]["order_setup"]["plan_source"] == "BOT"


def test_telegram_uses_latest_bot_plan_while_statistics_keep_frozen_plan(monkeypatch):
    listener = _listener()
    listener.build_opportunity_plan = lambda *_args, **_kwargs: {
        "ok": True,
        "price": 1900,
        "sl": 1890,
        "tp": 1915,
    }
    frozen = {
        "symbol": "VN30F1M",
        "order_setup": {
            "ok": True,
            "price": 1880,
            "sl": 1870,
            "tp": 1895,
        },
    }
    queued = []

    monkeypatch.setattr(
        "core.signal_opportunities.record_signal",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        "telegram_notify.opportunity_alerts.queue_opportunity",
        lambda item, log_cb=None: queued.append(item) or {"ok": True},
    )

    result = listener._record_and_queue_opportunity(
        {"symbol": "VN30F1M", "signal_class": "ENTRY"},
        "BUY",
        {"current_price": 1900},
        "TREND",
        block_reason="BOT_OFF",
    )

    assert result is frozen
    assert frozen["order_setup"]["price"] == 1880
    assert queued[0] is not frozen
    assert queued[0]["order_setup"]["price"] == 1900
    assert queued[0]["order_setup"]["plan_source"] == "BOT"


def test_telegram_receives_current_wait_plan_instead_of_frozen_valid_plan(monkeypatch):
    listener = _listener()
    listener.build_opportunity_plan = lambda *_args, **_kwargs: {
        "ok": False,
        "error": "ENTRY_EXIT_WAIT|RETEST",
    }
    frozen = {
        "symbol": "VN30F1M",
        "order_setup": {
            "ok": True,
            "price": 1880,
            "sl": 1870,
            "tp": 1895,
        },
    }
    queued = []

    monkeypatch.setattr(
        "core.signal_opportunities.record_signal",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        "telegram_notify.opportunity_alerts.queue_opportunity",
        lambda item, log_cb=None: queued.append(item) or {
            "ok": False,
            "reason": "setup_unavailable",
        },
    )

    listener._record_and_queue_opportunity(
        {"symbol": "VN30F1M", "signal_class": "ENTRY"},
        "BUY",
        {"current_price": 1900},
        "TREND",
        block_reason="BOT_OFF",
    )

    assert queued[0]["order_setup"]["ok"] is False
    assert queued[0]["order_setup"]["error"].startswith("ENTRY_EXIT_WAIT")
    assert frozen["order_setup"]["price"] == 1880


def test_successful_bot_execution_still_queues_telegram_opportunity(monkeypatch):
    listener = _listener()
    listener.get_auto_trade = lambda symbol=None: True
    listener.trade_manager.execute_bot_trade = (
        lambda **_kwargs: "SUCCESS|PAPER-1"
    )
    queued = []
    listener._record_and_queue_opportunity = (
        lambda signal, action, context, market_mode, block_reason: queued.append(
            (signal["symbol"], action, block_reason)
        )
    )

    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr("core.signal_listener.threading.Thread", ImmediateThread)

    listener._process_signal(
        {
            "symbol": "VN30F1M",
            "action": "BUY",
            "signal_class": "ENTRY",
            "market_mode": "TREND",
            "context": {"current_price": 1880.0},
        }
    )

    assert queued == [("VN30F1M", "BUY", "SUCCESS|PAPER-1")]
