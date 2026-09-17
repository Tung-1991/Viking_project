from __future__ import annotations

from viking_v2 import config
from viking_v2.trading.portfolio import affordable_quantity, order_budget, size_buy_order
from viking_v2.models import OrderIntent
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.state import TradeStateStore


def test_budget_is_nav_exposure_divided_by_max_positions_and_capped_by_remaining():
    assert order_budget(
        nav=1_000_000_000,
        exposure=0.5,
        max_positions=5,
        current_stock_value=0,
        pending_buy_value=0,
        available_cash=1_000_000_000,
    ) == 100_000_000
    assert affordable_quantity(100_000_000, 95) == 1000


def test_shared_buy_sizing_uses_budget_then_explicit_minimum_fallback():
    normal = size_buy_order(
        budget_vnd=100_000_000,
        price_board=95,
        available_cash=1_000_000_000,
        nav=1_000_000_000,
        force_min_lot_enabled=True,
    )
    minimum = size_buy_order(
        budget_vnd=800_000,
        price_board=69.2,
        available_cash=40_000_000,
        nav=40_000_000,
        force_min_lot_enabled=True,
    )
    assert (normal.quantity, normal.used_minimum) == (1000, False)
    assert (minimum.quantity, minimum.used_minimum) == (100, True)


def test_minimum_lot_never_breaks_exposure_room_or_ignores_fee():
    no_room = size_buy_order(
        budget_vnd=0, price_board=100, available_cash=20_000_000,
        nav=100_000_000, force_min_lot_enabled=True,
        minimum_order_room_vnd=9_000_000,
    )
    no_fee_cash = size_buy_order(
        budget_vnd=500_000, price_board=100, available_cash=10_000_000,
        nav=100_000_000, force_min_lot_enabled=True,
        minimum_order_room_vnd=20_000_000, buy_fee_rate=0.001,
    )
    assert no_room.quantity == 0
    assert no_fee_cash.quantity == 0


def test_pending_buy_reserves_exposure_and_duplicate_symbol(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    queue.add(OrderIntent.create("FPT", "BUY", 1000, "LO", limit_price=100))
    builder = PortfolioContextBuilder(
        queue,
        TradeStateStore(tmp_path / "trades.json"),
        RuleStateStore(tmp_path / "rule.json"),
    )
    context = builder.build(
        "FPT",
        execution_mode="PAPER",
        balance={"equity": 1_000_000_000, "stock": {"availableCash": 1_000_000_000}},
        positions=[],
        tick={"ask": 100},
        exposure=0.5,
        max_positions=5,
    )
    assert context["pending_buy"] is True
    assert context["pending_buy_value"] == 100_000_000
    assert context["order_budget"] == 100_000_000
    assert context["open_positions"] == 1


def test_unknown_buy_keeps_capital_reserved_until_reconciliation(tmp_path):
    queue = OrderQueue(tmp_path / "orders.json")
    intent = queue.add(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100))
    queue._update(intent.id, status="UNKNOWN", request_tag="V2:UNKNOWN:1")
    builder = PortfolioContextBuilder(
        queue,
        TradeStateStore(tmp_path / "trades.json"),
        RuleStateStore(tmp_path / "rule.json"),
    )
    context = builder.build(
        "FPT",
        execution_mode="PAPER",
        balance={"equity": 100_000_000, "stock": {"availableCash": 100_000_000}},
        positions=[],
        tick={"price": 100, "ask": 100},
        exposure=1.0,
        max_positions=5,
    )
    assert context["pending_buy"] is True
    assert context["pending_buy_value"] == 10_000_000


def test_external_position_blocks_bot_management_but_is_visible(tmp_path):
    builder = PortfolioContextBuilder(
        OrderQueue(tmp_path / "orders.json"),
        TradeStateStore(tmp_path / "trades.json"),
        RuleStateStore(tmp_path / "rule.json"),
    )
    context = builder.build(
        "FPT",
        execution_mode="REAL",
        balance={"equity": 100_000_000, "stock": {"availableCash": 0}},
        positions=[{"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 100, "costPrice": 100, "marketPrice": 101}],
        tick={"price": 101},
        exposure=0.9,
        max_positions=5,
    )
    assert context["position"]["managed_by_bot"] is False
    assert context["position"]["quantity"] == 100


def test_real_positions_use_board_price_and_aggregate_same_symbol_rows(tmp_path):
    builder = PortfolioContextBuilder(
        OrderQueue(tmp_path / "orders-units.json"),
        TradeStateStore(tmp_path / "trades-units.json"),
        RuleStateStore(tmp_path / "rules-units.json"),
    )
    context = builder.build(
        "FPT",
        execution_mode="REAL",
        balance={"equity": 100_000_000, "stock": {"availableCash": 0}},
        positions=[
            {"symbol": "FPT", "openQuantity": 100, "tradeQuantity": 100,
             "costPrice": 100_000, "marketPrice": 101_000},
            {"symbol": "FPT", "openQuantity": 200, "tradeQuantity": 0,
             "costPrice": 102_000, "marketPrice": 101_000},
        ],
        tick={"price": 101.5},
        exposure=0.9,
        max_positions=5,
    )
    assert context["position"]["quantity"] == 300
    assert context["position"]["trade_quantity"] == 100
    assert context["position"]["sellable"] is False
    assert context["position"]["avg_price"] == 101.33333333333333
    assert context["position"]["current_price"] == 101.5


def test_live_weak_exit_only_sees_fully_sellable_position(tmp_path):
    builder = PortfolioContextBuilder(
        OrderQueue(tmp_path / "orders-sellable.json"),
        TradeStateStore(tmp_path / "trades-sellable.json"),
        RuleStateStore(tmp_path / "rules-sellable.json"),
    )
    base = {
        "symbol": "FPT", "openQuantity": 300,
        "costPrice": 100_000, "marketPrice": 99_000,
    }
    kwargs = dict(
        execution_mode="REAL",
        balance={"equity": 100_000_000, "stock": {"availableCash": 0}},
        tick={"price": 99}, exposure=0.9, max_positions=5,
    )
    partial = builder.build("FPT", positions=[{**base, "tradeQuantity": 100}], **kwargs)
    complete = builder.build("FPT", positions=[{**base, "tradeQuantity": 300}], **kwargs)
    assert partial["position"]["sellable"] is False
    assert complete["position"]["sellable"] is True


def test_paper_position_persists_net_pnl_mae_mfe_input_after_fees(tmp_path):
    trades = TradeStateStore(tmp_path / "trades.json")
    rules = RuleStateStore(tmp_path / "rule.json")
    cycle = trades.create("FPT", "PAPER", trade_id="T1")
    buy_value = 100 * 100 * 1000
    trades.record_buy_fill("T1", 100, 100, buy_value * config.PAPER_BUY_FEE_RATE)
    builder = PortfolioContextBuilder(OrderQueue(tmp_path / "orders.json"), trades, rules)
    context = builder.build(
        "FPT",
        execution_mode="PAPER",
        balance={"equity": 100_000_000, "stock": {"availableCash": 90_000_000}},
        positions=[{
            "symbol": "FPT", "tradeId": cycle.id, "source": "BOT",
            "openQuantity": 100, "tradeQuantity": 100,
            "costPrice": 100, "marketPrice": 107,
        }],
        tick={"price": 107},
        exposure=0.9,
        max_positions=5,
    )
    expected = (
        700_000
        - buy_value * config.PAPER_BUY_FEE_RATE
        - 107 * 100 * 1000 * (config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE)
    )
    assert context["position"]["current_net_pnl"] == expected
    metrics = rules.position_metrics("FPT", "T1")
    assert metrics["current_net_pnl"] == expected
    assert metrics["mfe_net_pnl"] == expected


def test_portfolio_context_auto_unlocks_loss_streak_after_24_hours(tmp_path):
    trades = TradeStateStore(tmp_path / "trades-lock.json")
    lock_started = 1_800_000_000.0
    for index in range(3):
        cycle = trades.create("FPT", "PAPER")
        trades.record_buy_fill(cycle.id, 100, 100)
        trades.record_sell_fill(
            cycle.id,
            100,
            99,
            closed_at=lock_started - 2 + index,
        )
    builder = PortfolioContextBuilder(
        OrderQueue(tmp_path / "orders-lock.json"),
        trades,
        RuleStateStore(tmp_path / "rules-lock.json"),
    )
    common = dict(
        symbol="FPT",
        execution_mode="PAPER",
        balance={"equity": 100_000_000, "stock": {"availableCash": 100_000_000}},
        positions=[],
        tick={"price": 100},
        exposure=1.0,
        max_positions=1,
        loss_lock_count=3,
        loss_lock_hours=24,
    )

    locked = builder.build(**common, now=lock_started + 24 * 3600 - 1)
    unlocked = builder.build(**common, now=lock_started + 24 * 3600)

    assert locked["loss_streak"] == 3
    assert unlocked["loss_streak"] == 0
