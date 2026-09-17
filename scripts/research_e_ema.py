"""Compare E EMA and post-T+2 weakness on seven intraday replay cohorts.

Research only: this script never changes account settings or saves a run.
"""

from __future__ import annotations

import argparse
from collections import Counter

from viking_v2.backtest.data import HistoricalDataStore
from viking_v2.backtest.engine import BacktestEngine
from viking_v2.backtest.models import BacktestConfig
from viking_v2.backtest.report import settlement_mfe_stats
from viking_v2.rules.business import StaticRuleParameters


CASES = (
    ("CTS", "2026-03-14", "2026-08-20", "HOSE"),
    ("FTS", "2026-03-02", "2026-09-03", "HOSE"),
    ("SHS", "2026-03-02", "2026-09-03", "HNX"),
    ("SSI", "2026-03-02", "2026-09-03", "HOSE"),
    ("VIX", "2026-03-02", "2026-09-03", "HOSE"),
    ("VND", "2026-03-02", "2026-09-03", "HOSE"),
    ("DPM", "2026-07-07", "2026-09-10", "HOSE"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sell-ema", choices=("2/4", "3/6"), default="3/6")
    parser.add_argument("--weak-loss", type=float, default=None,
                        help="Enable the post-T+2 bearish-state E branch at this loss percent.")
    parser.add_argument("--symbol", choices=("ALL", *(case[0] for case in CASES)), default="ALL")
    parser.add_argument("--fill-session", choices=("ATO", "CONTINUOUS"), default="ATO")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--compare", action="store_true",
                        help="Also replay the same settings with weak exit disabled.")
    args = parser.parse_args()

    sell_fast, sell_slow = map(int, args.sell_ema.split("/"))
    params = StaticRuleParameters().to_dict()
    params.update(
        buy_ema_fast=3,
        buy_ema_slow=6,
        sell_ema_fast=sell_fast,
        sell_ema_slow=sell_slow,
        buy_window_enabled=True,
        buy_window_start="14:00",
        max_positions=1,
        initial_sl_pct=-4.0,
        reentry_sl_pct=-2.1,
        normal_arm_pct=7.0,
        normal_giveback_pct=2.5,
        normal_sell_pct=100.0,
        normal_dynamic_enabled=True,
        normal_atr_activation_multiplier=0.6,
        normal_atr_multiplier=0.8,
        normal_retention_pct=87.5,
        normal_retention_until_pct=5.0,
        normal_policy="AUTO",
        normal_repeat_enabled=False,
        normal_t2_reset_enabled=False,
        no_compound_enabled=True,
        force_min_lot_enabled=True,
    )
    if args.weak_loss is not None:
        params["sellable_weak_exit_enabled"] = True
        params["sellable_weak_exit_loss_pct"] = args.weak_loss
    engine = BacktestEngine(HistoricalDataStore())
    pnl = 0.0
    mfe = 0.0
    trades = 0
    exits: Counter[str] = Counter()
    exit_pnl: Counter[str] = Counter()
    weak_exits = 0
    baseline_pnl = baseline_mfe = 0.0
    matched_entries = baseline_entries = candidate_entries = 0
    worst_dd = baseline_worst_dd = 0.0
    for symbol, start, end, exchange in CASES:
        if args.symbol != "ALL" and symbol != args.symbol:
            continue
        config = BacktestConfig(
            [symbol], start, end,
            initial_capital=1_000_000_000,
            auto_market_phase=False,
            fixed_market_phase="ACCUMULATION",
            fixed_exposure_pct=60,
            use_market_phase=False,
            loss_lock_enabled=False,
            whipsaw_enabled=False,
            em_modes=["NORMAL", "IND_EXIT"],
            sell_wait_policy="RECHECK",
            fill_session=args.fill_session,
            rule_parameters=params,
            simulation_mode="REPLAY",
            execution_resolution="AUTO",
            symbol_exchanges={symbol: exchange},
        )
        result = engine.run(config, save=False)
        worst_dd = max(worst_dd, result.max_drawdown_pct)
        if args.compare:
            baseline_config = BacktestConfig.from_dict({
                **config.to_dict(),
                "rule_parameters": {**params, "sellable_weak_exit_enabled": False},
            })
            baseline = engine.run(baseline_config, save=False)
            baseline_pnl += baseline.net_pnl
            baseline_mfe += float(settlement_mfe_stats(baseline)["after_best"])
            baseline_worst_dd = max(baseline_worst_dd, baseline.max_drawdown_pct)
            old_buys = [(event.fill_time, event.price) for event in baseline.events if event.side == "BUY"]
            new_buys = [(event.fill_time, event.price) for event in result.events if event.side == "BUY"]
            matched_entries += sum(old == new for old, new in zip(old_buys, new_buys))
            baseline_entries += len(old_buys)
            candidate_entries += len(new_buys)
            print("COMPARE", symbol, round(baseline.net_pnl), round(result.net_pnl),
                  "buys", len(old_buys), len(new_buys),
                  "matched", sum(old == new for old, new in zip(old_buys, new_buys)),
                  "dd", round(baseline.max_drawdown_pct, 2), round(result.max_drawdown_pct, 2),
                  flush=True)
        stats = settlement_mfe_stats(result)
        pnl += result.net_pnl
        mfe += float(stats["after_best"])
        trades += result.closed_trades
        exits.update(trade.exit_mode or "OPEN" for trade in result.trades)
        for trade in result.trades:
            exit_pnl[trade.exit_mode or "OPEN"] += trade.net_pnl
        weak_exits += sum(
            event.reason == "SELLABLE_WEAK_EXIT" for event in result.events
        )
        print(symbol, round(result.net_pnl), round(float(stats["after_best"])), result.closed_trades, flush=True)
        if args.details:
            for trade in result.trades:
                print(trade.cycle_id, trade.opened_date, trade.closed_date, trade.exit_mode,
                      round(trade.net_pnl), trade.avg_entry_price, trade.avg_exit_price,
                      flush=True)
    print("TOTAL", round(pnl), round(mfe), round(pnl / mfe * 100, 2) if mfe else 0.0,
          trades, dict(exits), flush=True)
    print("EXIT_PNL", {key: round(value) for key, value in exit_pnl.items()}, flush=True)
    print("WEAK_E_EXITS", weak_exits, flush=True)
    if args.compare:
        print("BASELINE", round(baseline_pnl), round(baseline_mfe),
              round(baseline_worst_dd, 2), flush=True)
        print("DELTA", round(pnl - baseline_pnl), round(mfe - baseline_mfe),
              "ENTRIES", matched_entries, baseline_entries, candidate_entries,
              "WORST_DD", round(baseline_worst_dd, 2), round(worst_dd, 2), flush=True)


if __name__ == "__main__":
    main()
