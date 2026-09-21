from __future__ import annotations

from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.services.signal_coordinator import (
    BuyAttempt,
    BuySlotAllocator,
    coordinate_buy_decisions,
    is_terminal_buy_block,
    rank_buy_decisions,
)


def _buy(symbol: str, signal_time: str) -> StrategyDecision:
    return StrategyDecision(
        "BUY", symbol, "BUY_SIGNAL", signal="BUY", market_state="ACCUMULATION",
        details={
            "candle_key": "2026-09-09",
            "buy_window": {"signal_time": signal_time},
        },
    )


def test_twenty_candidates_reserve_only_five_earliest_signals() -> None:
    watchlist = [f"S{index:02d}" for index in range(20)]
    decisions = {
        symbol: _buy(symbol, f"2026-09-09T09:{59-index:02d}:00+07:00")
        for index, symbol in enumerate(watchlist)
    }

    ranked = rank_buy_decisions(decisions, watchlist)
    allocator = BuySlotAllocator(5)
    selected = [row.symbol for row in ranked if allocator.reserve(row.symbol)]

    assert selected == ["S19", "S18", "S17", "S16", "S15"]
    assert allocator.used == 5
    assert allocator.available == 0


def test_coordinator_releases_local_failure_and_offers_slot_to_next_signal() -> None:
    watchlist = ["FPT", "MBB", "VCB"]
    decisions = {
        "FPT": _buy("FPT", "2026-09-09T09:00:00+07:00"),
        "MBB": _buy("MBB", "2026-09-09T09:01:00+07:00"),
        "VCB": _buy("VCB", "2026-09-09T09:02:00+07:00"),
    }
    allocator = BuySlotAllocator(2, ["TCB"])

    outcomes = coordinate_buy_decisions(
        decisions, watchlist, allocator, bot_enabled=True,
        plan=lambda row: (
            BuyAttempt(reason="NO_AVAILABLE_CAPITAL")
            if row.symbol == "FPT" else BuyAttempt(payload=row.symbol)
        ),
    )

    assert [(row.candidate.symbol, row.payload, row.blocked_by) for row in outcomes] == [
        ("FPT", None, "NO_AVAILABLE_CAPITAL"),
        ("MBB", "MBB", ""),
        ("VCB", None, "MAX_POSITIONS"),
    ]
    assert allocator.occupied_symbols == {"TCB", "MBB"}


def test_equal_signal_times_use_fa_watchlist_priority() -> None:
    watchlist = ["VCB", "FPT", "MBB"]
    same_time = "2026-09-09T10:15:00+07:00"
    decisions = {
        "MBB": _buy("MBB", same_time),
        "VCB": _buy("VCB", same_time),
        "FPT": _buy("FPT", same_time),
    }

    assert [row.symbol for row in rank_buy_decisions(decisions, watchlist)] == watchlist


def test_only_deliberate_buy_waits_can_mature_without_a_new_signal() -> None:
    assert not is_terminal_buy_block("BUY_CONFIRMATION_WAIT")
    assert not is_terminal_buy_block("BUY_WINDOW_WAIT")
    assert is_terminal_buy_block("BOT_OFF")
    assert is_terminal_buy_block("INSUFFICIENT_BUDGET_FOR_ROUND_LOT")
    assert is_terminal_buy_block("NO_LIVE_EXECUTION_PRICE")


def test_open_positions_pending_and_unknown_buys_all_own_slots() -> None:
    pending = OrderIntent.create("MBB", "BUY", 100, "MARKET", execution_mode="PAPER")
    unknown = OrderIntent.create("TCB", "BUY", 100, "MARKET", execution_mode="PAPER")
    unknown.status = "UNKNOWN"
    rejected = OrderIntent.create("ACB", "BUY", 100, "MARKET", execution_mode="PAPER")
    rejected.status = "REJECTED"
    allocator = BuySlotAllocator.from_runtime(
        5,
        [{"symbol": "VCB", "openQuantity": 100}, {"symbol": "FPT", "quantity": 200}],
        [pending, unknown, rejected],
        "PAPER",
    )

    assert allocator.occupied_symbols == {"VCB", "FPT", "MBB", "TCB"}
    assert allocator.reserve("HDB") is True
    assert allocator.reserve("VPB") is False
    assert allocator.used == 5


def test_real_and_paper_pending_orders_do_not_share_slots() -> None:
    paper = OrderIntent.create("MBB", "BUY", 100, "MARKET", execution_mode="PAPER")
    real = OrderIntent.create("TCB", "BUY", 100, "MARKET", execution_mode="REAL")
    allocator = BuySlotAllocator.from_runtime(5, [], [paper, real], "PAPER")
    assert allocator.occupied_symbols == {"MBB"}


def test_coordinator_reports_operator_pause_instead_of_generic_bot_off() -> None:
    decision = _buy("FPT", "2026-09-09T10:15:00+07:00")
    outcomes = coordinate_buy_decisions(
        {"FPT": decision}, ["FPT"], BuySlotAllocator(5),
        bot_enabled=False,
        disabled_reason="MANUAL_SELL_PAUSE",
        plan=lambda _row: BuyAttempt(payload="must-not-run"),
    )

    assert outcomes[0].blocked_by == "MANUAL_SELL_PAUSE"
