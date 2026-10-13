"""Independent exit policies and legacy T+2 requests, entirely offline."""
from types import SimpleNamespace

import pytest

from viking_v2.config import merge_rule_parameters
from viking_v2.models import OrderIntent
from viking_v2.models import StrategyDecision
from viking_v2.rules.business import StaticRule, StaticRuleParameters, average_true_range_snapshot
from viking_v2.rules.protection import policy_for_stage
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue


@pytest.mark.parametrize("arm", ["AUTO", "ALERT"])
@pytest.mark.parametrize("dynamic", ["AUTO", "ALERT"])
@pytest.mark.parametrize("stage,peak,price", [("ARM", 10, 107), ("DYN", 4, 102.5)])
@pytest.mark.parametrize("sellable", [0, 600])
def test_rule_selects_exact_policy_without_queuing_t2(arm, dynamic, stage, peak, price, sellable):
    params = StaticRuleParameters(normal_policy=arm, dynamic_policy=dynamic,
                                  normal_atr_activation_enabled=False, normal_atr_trail_enabled=False)
    position = {"quantity": 600, "avg_price": 100, "current_price": price,
                "trade_quantity": sellable, "peak_profit_pct": peak, "normal_armed": stage == "ARM",
                "em_modes": ["NORMAL"], "sl_enabled": False}
    decision = StaticRule(params).evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"},
                                           {"position": position, "position_quantity": 600})
    expected = dynamic if stage == "DYN" else arm
    assert decision.details["normal_policy"] == expected
    assert decision.details["arm_policy"] == arm and decision.details["dynamic_policy"] == dynamic
    assert decision.details["protection_stage"] == stage
    if not sellable:
        assert decision.action == "WAIT" and decision.event == ""
        assert decision.reason == "PROTECT_WAITING_SETTLEMENT"
        assert "protect_occurrence" not in decision.details
    elif expected == "ALERT":
        assert (decision.action, decision.event) == ("WAIT", "PROTECT_ALERT")
    else:
        assert decision.action == "SELL" and decision.quantity_fraction == pytest.approx(.33)


@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
def test_old_accounts_and_direct_constructors_retain_shared_policy(policy):
    raw = {"normal_policy": policy}
    assert merge_rule_parameters(raw)["dynamic_policy"] == policy
    assert StaticRuleParameters.from_dict(raw).dynamic_policy == policy
    assert StaticRuleParameters(normal_policy=policy).dynamic_policy == policy
    split = StaticRuleParameters.from_dict({**raw, "dynamic_policy": "ALERT" if policy == "AUTO" else "AUTO"})
    assert policy_for_stage(split, "ARM") == policy
    assert policy_for_stage(split, "DYN") != policy
    assert StaticRuleParameters.from_dict(split.to_dict()) == split


def test_a_stale_daemon_auto_decision_does_not_queue_after_gui_alert_edit():
    from viking_v2.dashboard.actions import DashboardActionsMixin
    app = SimpleNamespace(settings=SimpleNamespace(rule_parameters={"normal_policy": "AUTO", "dynamic_policy": "ALERT"}))
    decision = StrategyDecision("SELL", "HDB", "DYNAMIC_PROTECTION", event="PRICE_PROTECTION",
                                details={"protection_stage": "DYN", "normal_policy": "AUTO"})
    result = DashboardActionsMixin._plan_rule_decision_locked(app, decision, {}, "REAL")
    assert result.intent is None and result.reason == "PROTECT_POLICY_CHANGED"


def test_old_saved_backtest_trade_keeps_its_shared_policy():
    from viking_v2.backtest.models import BacktestTrade
    legacy = BacktestTrade("T", "HDB", "2026-10-12", normal_policy="ALERT")
    assert legacy.dynamic_policy == "ALERT"
    split = BacktestTrade("T", "HDB", "2026-10-12", normal_policy="AUTO", dynamic_policy="ALERT")
    assert BacktestTrade(**split.to_dict()).dynamic_policy == "ALERT"


@pytest.mark.parametrize("status", ["PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT"])
@pytest.mark.parametrize("wait_policy", ["KEEP", "RECHECK"])
def test_dynamic_alert_cancels_only_its_unsent_requests_even_when_market_closed(tmp_path, status, wait_policy):
    queue = OrderQueue(tmp_path / "orders.json")
    intents = {}
    for name, reason in (("ARM", "NORMAL_PROTECTION"), ("DYN", "DYNAMIC_PROTECTION"), ("E", "INDICATOR_EXIT")):
        intent = OrderIntent.create("HDB", "SELL", 100, "MARKET", execution_mode="REAL", source="EM",
                                    reason=reason, sell_wait_policy=wait_policy)
        intent.status = status
        queue.add(intent)
        intents[name] = intent
    accepted = OrderIntent.create("HDB", "SELL", 100, "MARKET", execution_mode="REAL", source="EM",
                                   reason="DYNAMIC_PROTECTION")
    accepted.status, accepted.broker_order_id = "WORKING", "BROKER-ACCEPTED"
    queue.add(accepted)
    calls = []
    broker = SimpleNamespace(has_trading_token=lambda: False,
                             get_positions=lambda **_: [],
                             place_order=lambda order: calls.append(order))
    executor = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "events.jsonl"),
                                 protection_policy_provider=lambda stage: "ALERT" if stage == "DYN" else "AUTO")
    executor.process_due(phase="CLOSED", execution_mode="REAL")
    assert queue.get(intents["DYN"].id).status == "CANCELLED"
    assert queue.get(intents["ARM"].id).status == status
    assert queue.get(intents["E"].id).status == status
    assert queue.get(accepted.id).status == "WORKING"
    # Starting a new session with shares available does not revive the cancelled request.
    executor.process_due(phase="CLOSED", execution_mode="REAL")
    assert queue.get(intents["DYN"].id).status == "CANCELLED" and not calls


def test_alert_edit_during_account_read_is_rechecked_before_handoff(tmp_path, monkeypatch):
    from support.tests.test_protection_repeat_v2 import book
    broker, queue, _trades, _rules, executor, sample = book(tmp_path, monkeypatch, dynamic=True)
    _, plan = sample(103.5, send=False)
    executor.protection_policy_provider = lambda stage: policy_for_stage(sample.params, stage)
    original = broker.get_positions
    def positions(**kwargs):
        sample.params.dynamic_policy = "ALERT"
        return original(**kwargs)
    broker.get_positions = positions
    executor.process_due(phase="OPEN", execution_mode="REAL")
    assert not broker.sent
    assert queue.get(plan.intent.id).status == "CANCELLED"


@pytest.mark.parametrize("reason,details", [("PROTECT", {}), ("PRICE_PROTECTION", {"protection_stage": "DYN"}),
                                          ("PRICE_PROTECTION", {"triggered_events": ["DYNAMIC_PROTECTION"]})])
def test_old_protect_reason_cannot_escape_alert_or_revive_at_t2(tmp_path, reason, details):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = OrderIntent.create("HDB", "SELL", 600, "MARKET", execution_mode="REAL", source="EM",
                                reason=reason, details=details, sell_wait_policy="KEEP")
    intent.status = "WAITING_SETTLEMENT"
    queue.add(intent)
    broker = SimpleNamespace(has_trading_token=lambda: False)
    executor = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "events.jsonl"),
                                 protection_policy_provider=lambda stage: "ALERT" if stage == "DYN" else "AUTO")
    executor.process_due(phase="CLOSED", execution_mode="REAL")
    assert queue.get(intent.id).status == "CANCELLED" and not queue.get(intent.id).handed_off_at


def test_wilder_atr_value_and_percent_use_same_closed_candles():
    bars = [{"high": 25, "low": 23, "close": 24, "closed": True} for _ in range(14)]
    value, percent = average_true_range_snapshot(bars)
    assert value == 2 and percent == pytest.approx(2 / 24 * 100)
    assert average_true_range_snapshot([*bars, {"high": 99, "low": 1, "close": 50, "closed": False}]) == (value, percent)
    # An invalid close never changes the denominator used by the ATR result.
    assert average_true_range_snapshot([*bars, {"high": 99, "low": 1, "close": 0}]) == (value, percent)
    bars.append({"high": 26, "low": 22, "close": 25, "closed": True})
    value, percent = average_true_range_snapshot(bars)
    assert value == pytest.approx(2 + (4 - 2) / 14)
    assert percent == pytest.approx(value / 25 * 100)


@pytest.mark.parametrize("stage,peak,price", [("DYN", 4, 102.5), ("ARM", 10, 107)])
def test_backtest_uses_independent_stage_policy(stage, peak, price):
    from viking_v2.backtest.engine import _normal_policy_fill
    params = StaticRuleParameters(normal_policy="AUTO", dynamic_policy="ALERT")
    result = _normal_policy_fill(
        [{"open": price, "low": price, "high": price, "close": price}],
        policy=params.normal_policy, params=params, entry_price=100, peak_profit_pct=peak,
        already_armed=stage == "ARM", mfe_after_arm_pct=peak, arm_pct=7, giveback_pct=2.5,
        dynamic_enabled=True, retention_pct=72, atr_activation_enabled=False, atr_trail_enabled=False,
    )
    assert result.triggered and result.stage == stage
    assert (result.fill > 0) == (stage == "ARM")


@pytest.mark.parametrize("policy", ["AUTO", "ALERT"])
@pytest.mark.parametrize("stage,peak,price", [("DYN", 4, 102.5), ("ARM", 10, 107)])
def test_replay_t2_never_carries_protect_to_next_session(tmp_path, policy, stage, peak, price):
    from support.tests.test_memory_protect_backtest_v2 import replay_book
    from viking_v2.backtest.engine import _Position
    from viking_v2.backtest.models import BacktestConfig
    days = ["2026-10-13", "2026-10-14"]
    points = [[("09:15", price, price), ("14:00", price, price), ("14:45", price, price)],
              [("09:15", price, price), ("13:05", price, price), ("14:45", price, price)]]
    engine = replay_book(tmp_path, days, points, daily_price=100)
    position = _Position("T", "FPT", 600, 600, 100, "2026-10-12", "2026-10-14", 0, 100e6,
                         ["NORMAL"], peak_profit_pct=peak, normal_armed=stage == "ARM", sl_pct=-3.5)
    rules = {"buy_window_enabled": False, "normal_policy": policy, "dynamic_policy": policy,
             "normal_atr_activation_enabled": False, "normal_atr_trail_enabled": False}
    carry = {"cash": 0, "positions": {"FPT": position}}
    def run(day):
        return engine.run(BacktestConfig(["FPT"], day, day, simulation_mode="REPLAY", fill_session="CONTINUOUS",
                                         rule_parameters=rules), save=False, carry=carry)
    first = run(days[0])
    assert not [event for event in first.events if event.side == "SELL"]
    assert not carry["pending"]
    assert not [signal for signal in first.signals if signal.get("event") == "PROTECT_ALERT"]
    second = run(days[1])
    sells = [event for event in second.events if event.side == "SELL"]
    assert not carry["pending"]
    if policy == "ALERT":
        assert not sells
        assert any(signal.get("event") == "PROTECT_ALERT" for signal in second.signals)
    else:
        assert sells and all("13:05" <= event.fill_time[11:16] <= "14:45" for event in sells)
        assert sells[0].quantity == 100  # 33% of 600, rounded to a tradable lot.


@pytest.mark.parametrize("policy,queued", [("AUTO", False), ("AUTO", True), ("ALERT", False)])
def test_telegram_separates_condition_queue_and_execution(policy, queued):
    from viking_v2.connections.telegram import SignalTelegramService
    from support.tests.test_telegram_buy_lost import Telegram
    client = Telegram()
    service = SignalTelegramService(client, chat_id="offline")
    service.notify_protect_alert(symbol="HDB", price=22.85, mfe_pct=1.55, peak_price=23,
                                 protect_price=22.965, sell_pct=33, dynamic=True, policy=policy,
                                 stage="DYN", atr_value=.535, atr_pct=2.38,
                                 order_id="LOCAL-TEST" if queued else "", order_quantity=100 if queued else 0,
                                 order_status="PENDING" if queued else "")
    message = client.sent[0]
    assert "DYNAMIC · HDB" in message and "535" in message and "2.38%" in message
    assert "ĐÃ TẠO YÊU CẦU BÁN" not in message
    assert "100 CP" in message if queued else "100 CP" not in message
    assert "chưa xác nhận tạo/gửi/khớp" in message if policy == "AUTO" and not queued else True
    assert "chỉ báo, không đặt lệnh" in message if policy == "ALERT" else True
