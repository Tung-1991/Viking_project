from __future__ import annotations

from viking_v2.models import StrategyDecision
from viking_v2.trading.orders import OrderQueue
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.state import TradeStateStore


def _planner(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    return StrategyOrderPlanner(
        queue,
        TradeStateStore(tmp_path / "trades.json"),
        RuleStateStore(tmp_path / "rule.json"),
    ), queue


def test_buy_plan_uses_budget_round_lot_and_claims_signal_once(tmp_path):
    planner, queue = _planner(tmp_path)
    decision = StrategyDecision(
        "BUY", "FPT", "BUY_SIGNAL", event="ENTRY_BUY", signal="BUY",
        market_state="UPTREND", details={"telegram_signal_id": "ABCDEF1234"},
    )
    result = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"ask": 100},
        portfolio={"order_budget": 25_000_000},
        candle_key="2026-08-11",
        allow_ato=True,
        bot_em_modes=["NORMAL", "HIGH", "IND_EXIT"],
    )
    assert result.intent.quantity == 200
    assert result.intent.allow_ato is True
    assert result.intent.em_modes == ["NORMAL", "HIGH", "IND_EXIT"]
    assert result.intent.trade_id == "ABCDEF1234"
    assert len(queue.list_all()) == 1
    repeated = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"ask": 100},
        portfolio={"order_budget": 25_000_000},
        candle_key="2026-08-11",
    )
    assert repeated.intent is None
    assert repeated.reason == "BUY_ALREADY_PENDING"


def test_local_lo_stays_local_and_never_uses_ato_atc(tmp_path):
    planner, _queue = _planner(tmp_path)
    result = planner.plan(
        StrategyDecision("BUY", "FPT", "BUY_SIGNAL", event="ENTRY_BUY", signal="BUY"),
        execution_mode="REAL",
        execution_style="LO_LOCAL",
        tick={"ask": 100},
        # Budget must cover the buy fee as well, so it sits just above 100 lots.
        portfolio={"order_budget": 10_100_000},
        candle_key="1",
        allow_ato=True,
        allow_atc=True,
    )
    assert result.intent.order_type == "LO"
    assert result.intent.wait_for_trigger is True
    assert result.intent.limit_price == 100
    assert not result.intent.allow_ato and not result.intent.allow_atc


def test_force_min_lot_uses_100_shares_when_nav_and_cash_cover_it(tmp_path):
    planner, _queue = _planner(tmp_path)
    decision = StrategyDecision(
        "BUY",
        "FPT",
        "BUY_SIGNAL",
        event="ENTRY_BUY",
        signal="BUY",
        details={
            "entry_checks": {
                "force_min_lot_enabled": True,
                "available_cash": 40_000_000,
                "nav": 40_000_000,
            }
        },
    )
    result = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"ask": 69.2},
        portfolio={"order_budget": 800_000, "available_cash": 40_000_000, "nav": 40_000_000},
        candle_key="force-min-lot",
    )
    assert result.intent is not None
    assert result.intent.quantity == 100


def test_price_protection_sells_one_third_rounded_down(tmp_path):
    planner, _queue = _planner(tmp_path)
    decision = StrategyDecision(
        "SELL",
        "FPT",
        "NORMAL_PROTECTION",
        event="PRICE_PROTECTION",
        quantity_fraction=1 / 3,
        details={"triggered_events": ["NORMAL_PROTECTION"]},
    )
    result = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"bid": 100},
        portfolio={"position_quantity": 1000, "trade_id": "T1"},
        candle_key="1",
    )
    assert result.intent.quantity == 300
    assert result.intent.reason == "NORMAL_PROTECTION"


def test_price_protection_still_sells_one_lot_from_300_shares(tmp_path):
    planner, _queue = _planner(tmp_path)
    result = planner.plan(
        StrategyDecision(
            "SELL", "FPT", "NORMAL_PROTECTION", event="PRICE_PROTECTION",
            quantity_fraction=0.33,
        ),
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"bid": 100},
        portfolio={"position_quantity": 300, "trade_id": "T1"},
        candle_key="small-position",
    )
    assert result.intent is not None
    assert result.intent.quantity == 100


def test_full_exit_leaves_odd_lot_for_operator(tmp_path):
    planner, _queue = _planner(tmp_path)
    decision = StrategyDecision("SELL", "FPT", "SELL_SIGNAL", event="INDICATOR_EXIT", signal="SELL", quantity_fraction=1)
    result = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"bid": 100},
        portfolio={"position_quantity": 1050, "trade_id": "T1"},
        candle_key="1",
    )
    assert result.intent.quantity == 1000


def test_rule_sell_carries_t2_recheck_policy_and_signal_identity(tmp_path):
    planner, _queue = _planner(tmp_path)
    decision = StrategyDecision(
        "SELL", "FPT", "SELL_SIGNAL", event="INDICATOR_EXIT", signal="SELL", quantity_fraction=1,
    )
    result = planner.plan(
        decision,
        execution_mode="PAPER",
        execution_style="MARKET",
        tick={"bid": 100},
        portfolio={"position_quantity": 1000, "trade_id": "T1"},
        candle_key="2026-08-12",
        sell_wait_policy="RECHECK",
    )
    assert result.intent.sell_wait_policy == "RECHECK"
    assert result.intent.signal == "SELL"
    assert result.intent.candle_key == "2026-08-12"


def test_order_budget_takes_the_buy_fee_off_the_cash_first(tmp_path):
    """The budget handed to sizing is already money the account can commit."""
    from viking_v2.trading.portfolio import order_budget, size_buy_order

    common = dict(nav=10_000_000, exposure=1.0, max_positions=1,
                  current_stock_value=0.0, pending_buy_value=0.0,
                  available_cash=10_000_000)
    # Without a fee the whole balance is offered and the ticket costs more than
    # the account holds once the fee lands on top.
    bare = order_budget(**common)
    assert bare == 10_000_000
    assert size_buy_order(budget_vnd=bare, price_board=100).quantity == 100
    assert 100 * 100 * 1000 * 1.00045 > 10_000_000

    # With the fee taken off first the order fits, fee included.
    net = order_budget(**common, fee_rate=0.00045)
    assert net < 10_000_000
    assert size_buy_order(budget_vnd=net, price_board=100).quantity == 0

    richer = order_budget(**{**common, "nav": 10_100_000, "available_cash": 10_100_000},
                          fee_rate=0.00045)
    quantity = size_buy_order(budget_vnd=richer, price_board=100).quantity
    assert quantity == 100
    assert quantity * 100 * 1000 * 1.00045 <= 10_100_000
