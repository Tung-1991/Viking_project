"""Operator-requested restart of price observations, preserving every broker fill."""
from __future__ import annotations

import time
from .. import config
from .quote_boards import MATCHED_DATA_POLICY
from .validation import quote_is_fresh


def pause_position_auto_sell(trades, queue, trade_id: str, *, enabled: bool):
    with trades.store.database:
        cycle = trades.update_management(trade_id, auto_sell_enabled=enabled)
        if not cycle:
            raise ValueError("Vị thế đã thay đổi hoặc đã đóng.")
        cancelled, broker_pending = [], []
        if not enabled:
            for item in queue.list_all():
                if (item.trade_id != trade_id or item.execution_mode != cycle.execution_mode
                        or item.side != "SELL" or item.source not in {"BOT", "EM"}
                        or item.status in {"FILLED", "CANCELLED", "EXPIRED", "REJECTED", "FAILED"}):
                    continue
                if item.broker_order_id or item.handed_off_at:
                    broker_pending.append(item.id)
                    continue
                if item.status == "SENDING":
                    updated = (queue.release(item.id, "CANCELLED", "POSITION_AUTO_SELL_PAUSED")
                               if item.filled_quantity else queue.cancel_claimed_local(item.id, "POSITION_AUTO_SELL_PAUSED"))
                else:
                    updated = queue.cancel_local(item.id)
                if updated:
                    cancelled.append(item.id)
        return cycle, cancelled, broker_pending


def restart_price_observations(trades, rules, queue, trade_id: str, quote: dict) -> dict:
    """Begin at an identified fresh matched quote; do not invent missing history."""
    with trades.store.database:
        cycle = trades.get(trade_id)
        if not cycle or cycle.status != "OPEN" or cycle.open_quantity <= 0:
            raise ValueError("Vị thế đã thay đổi hoặc đã đóng.")
        if (not quote_is_fresh(quote, cycle.symbol) or quote.get("boardId") != "G1"
                or quote.get("price_source") != "MATCH" or quote.get("market_data_policy") != MATCHED_DATA_POLICY):
            raise ValueError("Cần giá khớp lệnh G1 mới; lấy lại giá rồi thử lại.")
        price = float(quote.get("price", 0) or 0)
        if not 0 < price < float("inf") or cycle.avg_entry_price <= 0 or cycle.buy_notional <= 0:
            raise ValueError("Chưa đủ giá vốn, vốn mua gốc hoặc giá G1 hợp lệ.")
        _, cancelled, broker_pending = pause_position_auto_sell(trades, queue, trade_id, enabled=False)
        broker_pending = list(dict.fromkeys([*broker_pending, *[
            item.id for item in queue.list_all()
            if item.trade_id == trade_id and item.execution_mode == cycle.execution_mode and item.side == "SELL"
            and item.status not in {"FILLED", "CANCELLED", "EXPIRED", "REJECTED", "FAILED"}
            and (item.broker_order_id or item.handed_off_at)]]))
        if broker_pending:
            # This is deliberately a committed hold only; a reset cannot race
            # a working broker SELL. The operator must reconcile/cancel it first.
            return {"blocked_by_broker_orders": broker_pending, "cancelled_local": cancelled}
        profit = (price / cycle.avg_entry_price - 1) * 100
        pnl = cycle.net_pnl + (price - cycle.avg_entry_price) * cycle.open_quantity * 1000
        if cycle.execution_mode == "PAPER":
            pnl -= price * cycle.open_quantity * 1000 * (config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE)
        rules.update_position_metrics(cycle.symbol, trade_id, profit_pct=profit, net_pnl=pnl,
            market_price=price, entry_notional_vnd=cycle.buy_notional, market_data_policy=MATCHED_DATA_POLICY)
        return rules.replace_verified_price_metrics(cycle.symbol, trade_id, metrics={
            "peak_profit_pct": max(0, profit), "mfe_pct": max(0, profit), "mae_pct": min(0, profit),
            "market_price": price, "current_profit_pct": profit, "current_net_pnl": pnl,
            "mae_net_pnl": min(0, pnl), "mfe_net_pnl": max(0, pnl), "entry_notional_vnd": cycle.buy_notional,
        }, evidence={"boardId": "G1", "source": "OPERATOR_G1_RESTART", "started_at": time.time(),
                     "quote_price": price, "quote_exchange_time": quote.get("exchange_time"),
                     "coverage": "FROM_RESTART", "cancelled_local": cancelled})
