from __future__ import annotations

import json
import math

import viking_v2.rules.business as static_rule_module
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder
from viking_v2.rules.state import RuleStateStore
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.trading.state import TradeStateStore


def _bars(close: float = 104.0) -> list[dict[str, float | bool]]:
    return [
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000,
            "closed": True,
        }
        for _ in range(24)
    ]


def test_order_intent_roundtrip_keeps_manual_em_and_stop_loss(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = OrderIntent.create(
        "fpt",
        "BUY",
        100,
        "MARKET",
        trade_id="T1",
        em_modes=["NORMAL", "REMOVED", "NORMAL", "INVALID"],
        sl_mode="PERCENT",
        sl_value=-4.25,
    )
    queue.add(intent)

    restored = OrderQueue(tmp_path / "orders.json").get(intent.id)
    assert restored is not None
    assert restored.trade_id == "T1"
    assert restored.em_modes == ["NORMAL"]
    assert restored.sl_mode == "PERCENT"
    assert restored.sl_value == -4.25


def test_old_trade_json_is_safe_and_defaults_to_unmanaged(tmp_path):
    path = tmp_path / "trades.json"
    path.write_text(
        json.dumps(
            {
                "cycles": [{"id": "OLD", "symbol": "FPT", "status": "OPEN"}],
                "loss_streaks": {},
            }
        ),
        encoding="utf-8",
    )
    cycle = TradeStateStore(path).get("OLD")
    assert cycle is not None
    assert cycle.em_modes == []
    assert cycle.sl_mode == "DEFAULT"


def test_management_is_per_trade_and_persists(tmp_path):
    path = tmp_path / "trades.json"
    store = TradeStateStore(path)
    first = store.create("FPT", "PAPER", trade_id="A")
    store.create("VNM", "PAPER", trade_id="B")

    updated = store.update_management(
        first.id,
        em_modes=["NORMAL", "IND_EXIT"],
        sl_mode="PRICE",
        sl_value=92.5,
    )
    assert updated is not None
    assert TradeStateStore(path).get("A").em_modes == ["NORMAL", "IND_EXIT"]
    assert TradeStateStore(path).get("A").sl_value == 92.5
    assert TradeStateStore(path).get("B").em_modes == []


def test_no_hidden_global_exit_switch_can_sell_an_unmanaged_trade():
    params = StaticRuleParameters()
    decision = StaticRule(params).evaluate(
        {"symbol": "FPT", "bars": _bars(), "confirmed_market_state": "UPTREND"},
        {
            "position": {
                "quantity": 900,
                "avg_price": 100,
                "current_price": 104,
                "peak_profit_pct": 8,
                "highest_close": 120,
                "em_modes": [],
            }
        },
    )
    assert decision.action == "WAIT"
    assert decision.scope == "POSITION_MANAGEMENT"


def test_protect_evaluation_sells_configured_fraction_and_marks_one_event():
    decision = StaticRule().evaluate(
        {"symbol": "FPT", "bars": _bars(114), "confirmed_market_state": "UPTREND"},
        {
            "position": {
                "quantity": 900,
                "avg_price": 100,
                "current_price": 114,
                "peak_profit_pct": 21,
                "normal_armed": True,
                "em_modes": ["NORMAL"],
            }
        },
    )
    assert decision.details["triggered_events"] == ["NORMAL_PROTECTION"]
    assert math.isclose(decision.quantity_fraction, 1.0)


def test_global_exit_parameters_apply_immediately_without_trade_snapshot():
    context = {"symbol": "FPT", "bars": _bars(), "confirmed_market_state": "UPTREND"}
    # Peak profit 7.2% means a peak price of 107.2, so PROTECT fires once price
    # falls 2% below that, at 105.056 - not at a fixed 2 profit points.
    def at(price: float) -> dict:
        return {"position": {
            "quantity": 900, "avg_price": 100, "current_price": price,
            "peak_profit_pct": 7.2, "normal_armed": True, "em_modes": ["NORMAL"],
        }}

    rule = StaticRule(StaticRuleParameters(normal_arm_pct=7))
    assert rule.evaluate(context, at(105.1)).action == "WAIT"
    assert rule.evaluate(context, at(105.0)).action == "SELL"
    # Raising the arm threshold above the peak disarms NORMAL entirely.
    assert StaticRule(StaticRuleParameters(normal_arm_pct=9)).evaluate(context, at(103.5)).action == "WAIT"


def test_custom_percent_and_price_stop_loss_have_priority():
    context = {"symbol": "FPT", "bars": _bars(98), "confirmed_market_state": "UPTREND"}
    percent = StaticRule().evaluate(
        context,
        {"position": {"quantity": 100, "avg_price": 100, "current_price": 97.4, "sl_mode": "PERCENT", "sl_value": -2.5}},
    )
    price = StaticRule().evaluate(
        context,
        {"position": {"quantity": 100, "avg_price": 100, "current_price": 95, "sl_mode": "PRICE", "sl_value": 96}},
    )
    assert percent.event == price.event == "STOP_LOSS"
    assert percent.scope == price.scope == "POSITION_MANAGEMENT"


def test_manual_take_profit_beats_the_global_percentage_and_loses_to_stop_loss():
    """A target typed onto the position is an instruction, not a suggestion."""
    context = {"symbol": "FPT", "bars": _bars(98), "confirmed_market_state": "UPTREND"}

    def at(price, **position):
        return {"position": {"quantity": 100, "avg_price": 100, "current_price": price, **position}}

    rule = StaticRule(StaticRuleParameters(take_profit_pct=7.0))
    # No target typed and no TP tactic: the position simply runs.
    assert rule.evaluate(context, at(110)).action == "WAIT"
    # The tactic alone sells at the global percentage.
    assert rule.evaluate(context, at(110, em_modes=["TP"])).event == "TAKE_PROFIT"
    # A typed target replaces that percentage in both directions.
    assert rule.evaluate(context, at(105, tp_mode="PRICE", tp_value=104)).event == "TAKE_PROFIT"
    assert rule.evaluate(context, at(110, tp_mode="PRICE", tp_value=125, em_modes=["TP"])).action == "WAIT"
    assert rule.evaluate(context, at(116, tp_mode="PERCENT", tp_value=15)).event == "TAKE_PROFIT"
    assert rule.evaluate(context, at(110, tp_mode="PERCENT", tp_value=15, em_modes=["TP"])).action == "WAIT"
    # Stop loss still outranks any target.
    assert rule.evaluate(context, at(95, tp_mode="PRICE", tp_value=104)).event == "STOP_LOSS"


def test_whipsaw_switch_locks_entry_at_n_but_never_position_management(monkeypatch):
    monkeypatch.setattr(static_rule_module, "crossover_signal", lambda *_args, **_kwargs: "BUY")
    monkeypatch.setattr(static_rule_module, "crossover_count", lambda *_args: 3)
    context = {"symbol": "FPT", "bars": _bars(), "confirmed_market_state": "UPTREND"}
    portfolio = {"available_capital": 100_000_000, "open_positions": 0}

    assert StaticRule().evaluate(context, portfolio).reason == "WHIPSAW_LOCK"
    off = StaticRule(StaticRuleParameters(whipsaw_enabled=False)).evaluate(context, portfolio)
    assert off.action == "BUY"

    held = StaticRule().evaluate(
        context,
        {"position": {"quantity": 100, "avg_price": 100, "current_price": 100, "em_modes": []}},
    )
    assert held.scope == "POSITION_MANAGEMENT"
    assert held.reason == "HOLD_POSITION"


def test_non_compound_cap_is_real_paper_isolated_and_can_be_disabled(tmp_path):
    trades = TradeStateStore(tmp_path / "trades.json")
    cycle = trades.create("FPT", "PAPER", trade_id="LOSS")
    trades.record_buy_fill(cycle.id, 1_000, 100)
    trades.record_sell_fill(cycle.id, 1_000, 97)
    assert trades.capital_available("FPT", "PAPER", 120_000_000) == 97_000_000
    assert trades.capital_available("FPT", "REAL", 120_000_000) == 120_000_000

    builder = PortfolioContextBuilder(
        OrderQueue(tmp_path / "orders.json"),
        trades,
        RuleStateStore(tmp_path / "rule.json"),
    )
    common = dict(
        execution_mode="PAPER",
        balance={"equity": 600_000_000, "stock": {"availableCash": 600_000_000}},
        positions=[],
        tick={"price": 100},
        exposure=1.0,
        max_positions=5,
    )
    assert builder.build("FPT", **common, no_compound_enabled=True)["order_budget"] == 97_000_000
    assert builder.build("FPT", **common, no_compound_enabled=False)["order_budget"] == 120_000_000


def test_decision_scope_survives_json_roundtrip():
    decision = StrategyDecision(
        "SELL",
        "FPT",
        "STOP_LOSS",
        scope="POSITION_MANAGEMENT",
        quantity_fraction=1.0,
    )
    assert StrategyDecision.from_dict(decision.to_dict()).scope == "POSITION_MANAGEMENT"


def test_stop_loss_is_not_blocked_by_a_previously_seen_b_signal(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    state = RuleStateStore(tmp_path / "rule.json")
    trades = TradeStateStore(tmp_path / "trades.json")
    planner = StrategyOrderPlanner(queue, trades, state)
    assert state.claim_signal("FPT", "SELL", "2026-08-12")
    decision = StrategyDecision(
        "SELL",
        "FPT",
        "STOP_LOSS",
        event="STOP_LOSS",
        signal="SELL",
        scope="POSITION_MANAGEMENT",
        quantity_fraction=1.0,
    )
    result = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"price": 95, "bid": 95},
        portfolio={"trade_id": "T1", "position_quantity": 100},
        candle_key="2026-08-12",
    )
    assert result.intent is not None
    assert result.intent.source == "EM"
    assert result.intent.reason == "STOP_LOSS"
