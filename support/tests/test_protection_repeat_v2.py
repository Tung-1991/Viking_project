"""Real rule/planner/execution exercised with an immediate-fill offline broker."""
from datetime import datetime

import pytest

from viking_v2.models import BrokerOrderResult
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


class Broker:
    def __init__(self, quote):
        self.sent, self.quote, self.quantity = [], quote, 1000
        self.orders = []

    def has_trading_token(self):
        return True

    def get_positions(self, **_kwargs):
        return [{"symbol": "FAKE", "openQuantity": self.quantity, "tradeQuantity": self.quantity}]

    def get_orders(self, **_kwargs):
        return self.orders

    def place_order(self, intent):
        assert 0 < intent.quantity <= self.quantity
        self.sent.append(intent)
        self.quantity -= intent.quantity
        raw = {"orderId": str(len(self.sent)), "orderStatus": "Filled", "price_unit": "VND",
               "fillQuantity": intent.quantity, "averagePrice": self.quote["price"] * 1000,
               "fee": 0, "tax": 0}
        self.orders.append(raw)
        return BrokerOrderResult(True, "Filled", order_id=raw["orderId"], raw=raw)


def book(tmp_path, monkeypatch, *, basis="ORIGINAL", dynamic=False, repeat=True):
    clock = [datetime(2026, 10, 13, 14, tzinfo=VN_TZ).timestamp()]
    monkeypatch.setattr("time.time", lambda: clock[0])
    quote = {"symbol": "FAKE", "price": 105., "bid": 105., "ask": 105., "timestamp": clock[0], "source": "WS"}
    broker = Broker(quote)
    queue = OrderQueue(tmp_path / "orders.json")
    trades = TradeStateStore(tmp_path / "trades.json")
    rules = RuleStateStore(tmp_path / "rules.json")
    trades.create("FAKE", "REAL", trade_id="T", em_modes=["NORMAL"], sl_enabled=False)
    trades.record_buy_fill("T", 1000, 100)
    planner = StrategyOrderPlanner(queue, trades, rules)
    execution = ExecutionService(broker, broker, queue, JSONLineJournal(tmp_path / "events.jsonl"),
                                 trade_state=trades, rule_state=rules, quote_provider=lambda _: dict(quote))
    params = StaticRuleParameters(normal_sell_pct=33, normal_sell_basis=basis,
                                  normal_repeat_enabled=repeat, dynamic_sell_pct=33,
                                  dynamic_sell_basis=basis, dynamic_repeat_enabled=repeat,
                                  normal_atr_activation_enabled=False, normal_atr_trail_enabled=False,
                                  normal_retention_until_enabled=False)
    rule = StaticRule(params)
    base_peak = 4 if dynamic else 10
    def evaluate(price, *, send=True, peak=None):
        peak = base_peak if peak is None else peak
        clock[0] += 1
        quote.update(price=price, bid=price, ask=price, timestamp=clock[0])
        cycle = trades.get("T")
        rules.update_position_metrics("FAKE", "T", profit_pct=peak, market_price=price)
        if not dynamic or peak >= params.normal_arm_pct:
            rules.arm_normal("FAKE", "T")
        metrics = rules.position_metrics("FAKE", "T")
        portfolio = {"trade_id": "T", "position_quantity": cycle.open_quantity,
                     "position": {"quantity": cycle.open_quantity, "entry_quantity": cycle.entry_quantity,
                                  "avg_price": 100, "current_price": price, "peak_profit_pct": peak,
                                  "em_modes": ["NORMAL"], "sl_enabled": False, "managed_by_app": True,
                                  **metrics}}
        decision = rule.evaluate({"symbol": "FAKE", "bars": [], "signal_mode": "REALTIME",
                                  "confirmed_market_state": "UPTREND"}, portfolio)
        if decision.details.get("normal_trigger_price"):
            rules.update_protect_metrics("FAKE", "T", trigger_price=decision.details["normal_trigger_price"],
                                        atr_pct=0, atr_multiplier=.8,
                                        stage=decision.details.get("protection_stage", "ARM"))
        planned = planner.plan(decision, execution_mode="REAL", execution_style="MARKET",
                               tick=quote, portfolio=portfolio, candle_key=str(clock[0]))
        if send:
            execution.process_due(phase="OPEN", execution_mode="REAL")
        return decision, planned
    evaluate.params = params
    return broker, queue, trades, rules, execution, evaluate


@pytest.mark.parametrize("basis,expected", [("ORIGINAL", [300, 300, 300]), ("REMAINING", [300, 200, 100])])
@pytest.mark.parametrize("dynamic", [False, True])
def test_repeat_lower_prices_sell_correct_basis_and_never_exceed_position(tmp_path, monkeypatch, basis, expected, dynamic):
    broker, queue, trades, rules, execution, sample = book(tmp_path, monkeypatch, basis=basis, dynamic=dynamic)
    prices = [103.6, 103.5, 103.4] if dynamic else [105, 104, 103]
    for price in prices:
        decision, planned = sample(price)
        assert decision.action == "SELL" and planned.intent
        sample(price)
        sample(price)
    assert [order.quantity for order in broker.sent] == expected
    assert trades.get("T").sold_quantity == sum(expected)
    assert trades.get("T").open_quantity == 1000 - sum(expected)
    state = rules.position_metrics("FAKE", "T")
    assert state.get("dynamic_protection_count" if dynamic else "normal_protection_count") == 3
    assert state.get("normal_protection_count" if dynamic else "dynamic_protection_count", 0) == 0
    execution.reconcile_working("REAL")
    assert trades.get("T").sold_quantity == sum(expected)


def test_rebound_then_same_touch_sells_again_without_new_peak(tmp_path, monkeypatch):
    broker, _, _, _, _, sample = book(tmp_path, monkeypatch)
    sample(105)
    assert sample(108)[0].action == "WAIT"  # Still below historical peak 110.
    sample(105)
    assert [order.quantity for order in broker.sent] == [300, 300]


def test_pending_request_blocks_new_repeat_and_restart_keeps_consumption(tmp_path, monkeypatch):
    broker, queue, trades, rules, execution, sample = book(tmp_path, monkeypatch)
    first = sample(105, send=False)[1].intent
    assert first
    assert sample(104, send=False)[1].intent is None
    execution.process_due(phase="OPEN", execution_mode="REAL")
    restarted = RuleStateStore(tmp_path / "rules.json")
    assert restarted.position_metrics("FAKE", "T")["normal_protection_count"] == 1
    sample(105)
    assert len(broker.sent) == 1
    sample(104)
    assert len(broker.sent) == 1  # Same price at completed hand-off is not a fresh repeat.
    sample(103)
    assert len(broker.sent) == 2 and trades.get("T").open_quantity == 400


def test_repeat_off_stops_after_first_fill(tmp_path, monkeypatch):
    broker, _, _, _, _, sample = book(tmp_path, monkeypatch, repeat=False)
    sample(105)
    sample(104)
    sample(103)
    assert len(broker.sent) == 1


def test_legacy_dynamic_occurrence_migrates_once_without_consuming_arm(tmp_path):
    store = RuleStateStore(tmp_path / "legacy.json")
    store.update_position_metrics("FAKE","T",profit_pct=4,market_price=103.5)
    raw = store.store.read()
    row = raw["symbols"]["FAKE|T"]
    row.pop("protection_state_version",None)
    row.update(normal_protection_count=1,normal_protection_done=True,normal_last_trigger_peak_pct=4,
               normal_processed_order_ids=["OLD"],normal_trigger_price=103.6)
    store.store.write(raw)
    migrated = store.update_position_metrics("FAKE","T",profit_pct=10,market_price=105)
    assert migrated["dynamic_protection_count"] == 1 and migrated["dynamic_processed_order_ids"] == ["OLD"]
    assert migrated.get("normal_protection_count",0) == 0
    assert migrated["dynamic_trigger_price"] == 103.6 and migrated.get("normal_trigger_price",0) == 0
    assert store.update_position_metrics("FAKE","T",profit_pct=10,market_price=105)["dynamic_protection_count"] == 1


def test_dynamic_sale_and_repeat_do_not_consume_arm_settings(tmp_path, monkeypatch):
    broker, _, trades, rules, _, sample = book(tmp_path, monkeypatch, dynamic=True)
    sample.params.dynamic_sell_pct = 20
    sample.params.dynamic_repeat_enabled = False
    sample.params.normal_sell_pct = 50
    sample(103.5)
    sample(103.4)
    assert len(broker.sent) == 1
    sample(105, peak=10)
    assert [order.quantity for order in broker.sent] == [200, 500]
    state = rules.position_metrics("FAKE", "T")
    assert state["dynamic_protection_count"] == state["normal_protection_count"] == 1
    assert trades.get("T").open_quantity == 300


def test_partial_fills_count_one_repeat_and_stationary_completion_does_not_resell(tmp_path, monkeypatch):
    broker, queue, trades, rules, execution, sample = book(tmp_path, monkeypatch)
    immediate = broker.place_order
    def partial(intent):
        broker.sent.append(intent)
        broker.quantity -= 100
        broker.orders.append({"orderId": "1", "orderStatus": "PartiallyFilled", "quantity": intent.quantity,
                              "fillQuantity": 100, "averagePrice": 105000, "price_unit": "VND"})
        return BrokerOrderResult(True, "PartiallyFilled", order_id="1", raw=broker.orders[0])
    broker.place_order = partial
    broker.get_order = lambda *_args, **_kwargs: dict(broker.orders[0])
    sample(105)
    assert trades.get("T").sold_quantity == 100
    sample(104)
    assert len(broker.sent) == 1
    for filled in (200, 300):
        broker.quantity = 1000 - filled
        broker.orders[0].update(fillQuantity=filled, orderStatus="Filled" if filled == 300 else "PartiallyFilled")
        execution.reconcile_working("REAL")
    assert trades.get("T").sold_quantity == 300
    assert rules.position_metrics("FAKE", "T")["normal_protection_count"] == 1
    execution.reconcile_working("REAL")
    sample(104)
    assert len(broker.sent) == 1
    broker.place_order = immediate
    sample(103)
    assert len(broker.sent) == 2 and trades.get("T").sold_quantity == 600


def test_replayed_completion_cannot_erase_rebound_or_increment_repeat(tmp_path):
    store = RuleStateStore(tmp_path / "completion.json")
    store.update_position_metrics("FAKE","T",profit_pct=10,market_price=105)
    store.update_protect_metrics("FAKE","T",trigger_price=107,atr_pct=0,atr_multiplier=.8)
    store.mark_protection_done("FAKE","T",["NORMAL_PROTECTION"],execution_id="ONE",trigger_price=105,completed=True)
    store.update_position_metrics("FAKE","T",profit_pct=10,market_price=108)
    store.mark_protection_done("FAKE","T",["NORMAL_PROTECTION"],execution_id="ONE",trigger_price=108,completed=True)
    row = RuleStateStore(tmp_path / "completion.json").position_metrics("FAKE","T")
    assert row["normal_protection_count"] == 1 and row["normal_repeat_rearmed"]
    assert row["normal_last_trigger_price"] == 105


def test_unknown_result_never_posts_a_new_repeat_or_reposts_after_restart(tmp_path, monkeypatch):
    broker, queue, trades, rules, execution, sample = book(tmp_path, monkeypatch)
    def timeout(intent):
        broker.sent.append(intent)
        raise TimeoutError("fake broker accepted but response was lost")
    broker.place_order = timeout
    sample(105)
    assert queue.list_all()[0].status == "UNKNOWN"
    sample(104)
    restarted = ExecutionService(broker, broker, OrderQueue(tmp_path / "orders.json"),
        JSONLineJournal(tmp_path / "events.jsonl"), trade_state=trades,
        rule_state=RuleStateStore(tmp_path / "rules.json"), quote_provider=execution.quote_provider)
    restarted.process_due(phase="OPEN", execution_mode="REAL")
    sample(103)
    assert len(broker.sent) == 1 and trades.get("T").sold_quantity == 0


def test_cancelled_partial_anchors_repeat_at_completion_not_a_pending_price_move(tmp_path,monkeypatch):
    broker, queue, trades, rules, execution, sample = book(tmp_path,monkeypatch)
    immediate = broker.place_order
    def partial(intent):
        broker.sent.append(intent)
        broker.quantity -= 100
        broker.orders.append({"orderId":"1","orderStatus":"PartiallyFilled","quantity":intent.quantity,
                              "fillQuantity":100,"averagePrice":105000,"price_unit":"VND"})
        return BrokerOrderResult(True,"PartiallyFilled",order_id="1",raw=broker.orders[0])
    broker.place_order = partial
    broker.get_order = lambda *_args,**_kwargs: dict(broker.orders[0])
    sample(105)
    sample(104)
    broker.orders[0]["orderStatus"] = "Cancelled"
    execution.reconcile_working("REAL")
    assert queue.list_all()[0].status == "CANCELLED" and trades.get("T").sold_quantity == 100
    broker.place_order = immediate
    sample(104)
    assert len(broker.sent) == 1
    sample(103)
    assert len(broker.sent) == 2 and trades.get("T").sold_quantity == 400


@pytest.mark.parametrize("dynamic",[False,True])
@pytest.mark.parametrize("basis,expected",[("ORIGINAL",300),("REMAINING",100)])
def test_t2_recheck_keeps_sale_basis_and_stage_when_shares_arrive(tmp_path,monkeypatch,dynamic,basis,expected):
    broker,queue,trades,_,execution,sample = book(tmp_path,monkeypatch,dynamic=dynamic,basis=basis)
    trades.record_sell_fill("T",400,100)
    broker.quantity = 600
    price = 103.5 if dynamic else 105
    decision,plan = sample(price,send=False)
    assert plan.intent.quantity == expected
    queue.wait_for_settlement(plan.intent.id,"Fake T+2")
    execution.sell_decision_provider = lambda *_: decision.to_dict()
    sample(price)
    assert len(broker.sent) == 1 and broker.sent[0].quantity == expected
    assert broker.sent[0].details["protection_stage"] == ("DYN" if dynamic else "ARM")
    assert broker.sent[0].details["sell_basis"] == basis


@pytest.mark.parametrize("dynamic",[False,True])
def test_auto_to_alert_cancels_local_arm_and_dynamic_sells(tmp_path,monkeypatch,dynamic):
    broker,queue,_,_,execution,sample = book(tmp_path,monkeypatch,dynamic=dynamic)
    _,plan = sample(103.5 if dynamic else 105,send=False)
    cancelled,accepted = execution.cancel_unsubmitted_protect_sells("REAL")
    assert [intent.id for intent in cancelled] == [plan.intent.id]
    assert not accepted and not broker.sent and queue.get(plan.intent.id).status == "CANCELLED"


def test_dynamic_uses_protect_telegram_category_and_deduplicates_without_network(tmp_path,monkeypatch):
    from viking_v2.config import AppSettings
    from viking_v2.dashboard.actions import DashboardActionsMixin
    from viking_v2.connections.telegram import SignalTelegramService
    from viking_v2.models import StrategyDecision
    from support.tests.test_telegram_buy_lost import Telegram,ImmediateThread
    from support.tests.telegram_support import TELEGRAM_START,set_technical_test_session
    monkeypatch.setattr("time.time",lambda:TELEGRAM_START)
    monkeypatch.setattr("viking_v2.dashboard.actions.threading.Thread",ImmediateThread)
    app = DashboardActionsMixin()
    set_technical_test_session(app)
    app.settings = AppSettings(telegram_enabled=True).normalize()
    app.rule_state = RuleStateStore(tmp_path / "tele.json")
    client = Telegram()
    app.telegram = SignalTelegramService(client,chat_id="offline")
    decision = StrategyDecision("SELL","FAKE","DYNAMIC_PROTECTION",event="PRICE_PROTECTION",
        details={"triggered_events":["DYNAMIC_PROTECTION"],"protect_occurrence":"DYN:T:1",
                 "normal_dynamic_enabled":True,"normal_mfe_pct":4,"normal_peak_price":104,
                 "normal_trigger_price":103.6,"sell_share_pct":33})
    for _ in range(2):
        app._notify_rule_signal("FAKE",decision,{"price":103.5},execution_mode="REAL")
    assert len(client.sent) == 1 and "PROTECT" in client.sent[0]
