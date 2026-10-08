from __future__ import annotations

from datetime import datetime
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

from .. import config
from ..trading.orders import CLAIMABLE_STATUSES, FINAL_STATUSES, LOCALLY_CONTROLLABLE_STATUSES
from ..trading.portfolio import account_price
from ..trading.validation import decision_is_fresh, decisions_for_mode
from .view import _compact_vnd, _display_price, _number, _price_unit


RUNNING_COLUMNS = (
    "Ticket",
    "Time",
    "Order",
    "Targets",
    "CostInfo",
    "RR",
    "PnL_MAE_MFE",
    "Status",
    "X",
)
RUNNING_HEADERS = {
    "Ticket": "Ticket",
    "Time": "Thời gian",
    "Order": "Thông tin Lệnh",
    "Targets": "Chốt lời/Lỗ (SL|TP)",
    "CostInfo": "Chi phí/Phí qua đêm",
    "RR": "Rủi ro/Kỳ vọng (%)",
    "PnL_MAE_MFE": "PnL / MAE / MFE",
    "Status": "Trạng thái",
    "X": "✖",
}
RUNNING_WIDTHS = {
    "Ticket": 145,
    "Time": 165,
    "Order": 430,
    "Targets": 310,
    "CostInfo": 310,
    "RR": 280,
    "PnL_MAE_MFE": 390,
    "Status": 920,
    "X": 56,
}
RUNNING_ANCHORS = {
    "Ticket": "center",
    "Time": "center",
    "Order": "w",
    "Targets": "center",
    "CostInfo": "center",
    "RR": "center",
    "PnL_MAE_MFE": "center",
    "Status": "w",
    "X": "center",
}
VISIBLE_LOG_LINE_LIMIT = 2000


class DashboardTablesMixin:
    @staticmethod
    def _running_column_width(tree: ttk.Treeview, column: str) -> int:
        """Return a width that fits both content policy and the rendered header."""
        configured = RUNNING_WIDTHS.get(column, 180)
        try:
            family, size, *styles = ("Segoe UI", 14, "bold")
            options: dict[str, Any] = {
                "root": tree.winfo_toplevel(), "family": family, "size": size,
            }
            if "bold" in styles:
                options["weight"] = "bold"
            if "italic" in styles:
                options["slant"] = "italic"
            measured = tkfont.Font(**options).measure(RUNNING_HEADERS.get(column, column))
            return max(configured, measured + 32)
        except (AttributeError, RuntimeError, TypeError, ValueError, tk.TclError):
            return configured

    def _clear_running_selection_on_blank(self, event: Any) -> None:
        """Clear a sticky selection when the user clicks the empty table area."""
        tree = event.widget
        if tree.identify_row(event.y):
            return
        selected = tree.selection()
        if selected:
            tree.selection_remove(*selected)
            self._sync_cancel_button()

    def _clear_running_selection(self, tree: ttk.Treeview) -> None:
        selected = tree.selection()
        if selected:
            tree.selection_remove(*selected)
            self._sync_cancel_button()

    def _running_action_click(self, event: Any) -> None:
        """Restore the original last-column cancel affordance for open orders."""
        tree = event.widget
        if tree.identify("region", event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) != f"#{len(RUNNING_COLUMNS)}":
            return
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        mode = "REAL" if tree is self.trees.get("REAL") else "PAPER"
        action = self._running_row_actions.get(mode, {}).get(row_id, {})
        if not action.get("cancellable"):
            return
        tree.selection_set(row_id)
        self._sync_cancel_button()
        self.after_idle(self._cancel_selected)

    @staticmethod
    def _configure_tree(tree: ttk.Treeview, columns: tuple[str, ...]) -> None:
        tree.configure(columns=columns)
        for column in columns:
            width = DashboardTablesMixin._running_column_width(tree, column)
            tree.heading(
                column,
                text=RUNNING_HEADERS.get(column, column),
                anchor=RUNNING_ANCHORS.get(column, "w"),
            )
            tree.column(
                column,
                width=width,
                minwidth=width,
                anchor=RUNNING_ANCHORS.get(column, "w"),
                stretch=False,
            )

    @staticmethod
    def _update_running_row(
        tree: ttk.Treeview,
        existing: dict[str, dict[str, Any]],
        row_order: list[str],
        *,
        iid: str,
        tags: tuple[str, ...],
        values: tuple[str, ...],
    ) -> None:
        """Update display cells only; action metadata is rebuilt separately."""
        row_order.append(iid)
        previous = existing.get(iid)
        if previous is None:
            tree.insert("", "end", iid=iid, tags=tags, values=values)
        elif tuple(previous["values"]) != values or tuple(previous["tags"]) != tags:
            tree.item(iid, tags=tags, values=values)

    def _render_tables(self, runtime_status: dict[str, Any] | None = None) -> None:
        runtime_status = runtime_status if isinstance(runtime_status, dict) else self.bridge.read_status()
        runtime_ticks = runtime_status.get("ticks") if isinstance(runtime_status.get("ticks"), dict) else {}
        self._last_running_render = time.time()
        columns = RUNNING_COLUMNS
        local_by_mode = {"REAL": [], "PAPER": []}
        local_items = self.queue.list_all()
        if hasattr(self, "_order_log_states"):
            self._sync_order_progress(local_items)
        for item in local_items:
            if item.status.upper() not in FINAL_STATUSES:
                local_by_mode[item.execution_mode].append(item)
        # One fresh read per render, not one full ledger decode per visible row.
        # These indexes are local display snapshots; no trading decision uses them.
        needs_cycles = (
            any(self.snapshots.get(mode, ({}, [], []))[1] for mode in self.trees)
            or any(local_by_mode.values())
        )
        cycles = self.trade_state.list_cycles() if needs_cycles else []
        cycles_by_id = {cycle.id: cycle for cycle in cycles}
        active_cycles: dict[tuple[str, ...], Any] = {}
        for cycle in cycles:
            if cycle.status == "OPEN":
                key = (cycle.symbol, cycle.execution_mode)
                active_cycles.setdefault(key, cycle)
                active_cycles.setdefault((*key, cycle.loan_package_id), cycle)
        for mode, tree in self.trees.items():
            runtime_decisions = decisions_for_mode(
                runtime_status, mode, default_paper=getattr(self.settings, "paper_mode", True),
            )
            selected_before = tuple(tree.selection())
            yview_before = tree.yview()
            previous_order = tuple(tree.get_children())
            existing = {iid: tree.item(iid) for iid in previous_order}
            row_order: list[str] = []
            self._running_row_actions[mode] = {}
            if tuple(tree["columns"]) != columns:
                self._configure_tree(tree, columns)
            balance, positions, broker_orders = self.snapshots.get(mode, ({}, [], []))
            params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
            take_profit_pct = float(params.get("take_profit_pct", 7.0) or 7.0)
            normal_tp = float(params.get("normal_arm_pct", 7.0) or 7.0)
            close_items = [item for item in local_by_mode[mode] if item.action == "CLOSE"]
            consumed_close_ids: set[str] = set()

            for index, row in enumerate(positions):
                symbol = str(row.get("symbol", row.get("instrumentId", "?")) or "?").upper()
                quantity = int(_number(row.get("openQuantity", row.get("quantity", 0))))
                if quantity <= 0:
                    continue
                sellable = int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0))))
                avg_price = account_price(row, row.get("costPrice", row.get("averagePrice", 0)))
                tick = runtime_ticks.get(symbol) if isinstance(runtime_ticks.get(symbol), dict) else {}
                tick_price = _price_unit(
                    tick.get("price")
                    or tick.get("lastPrice")
                    or tick.get("matchPrice")
                    or tick.get("expected_price")
                    or tick.get("bid")
                    or tick.get("ask")
                    or 0
                )
                market_price = tick_price or account_price(row, row.get("marketPrice", row.get("price", 0))) or avg_price
                trade_id = str(row.get("tradeId", row.get("positionId", "")) or "")
                cycle = cycles_by_id.get(trade_id) if trade_id else None
                if cycle and (cycle.execution_mode != mode or cycle.symbol != symbol):
                    cycle = None
                    trade_id = ""
                if not cycle:
                    package_id = str(row.get("loanPackageId", "") or "")
                    key = (symbol, mode, package_id) if package_id else (symbol, mode)
                    cycle = active_cycles.get(key)
                if cycle:
                    trade_id = cycle.id
                metrics = self.rule_state.position_metrics(symbol, trade_id) if trade_id else {}
                cycle_modes = set(cycle.em_modes if cycle else [])
                sl_enabled = bool(cycle.sl_enabled) if cycle else False
                if not sl_enabled:
                    sl_pct = 0.0
                    sl_price = 0.0
                elif cycle and cycle.sl_mode == "PRICE" and cycle.sl_value > 0 and avg_price > 0:
                    sl_pct = (cycle.sl_value / avg_price - 1.0) * 100.0
                    sl_price = float(cycle.sl_value)
                elif cycle and cycle.sl_mode == "PERCENT" and cycle.sl_value:
                    sl_pct = -abs(float(cycle.sl_value))
                    sl_price = avg_price * (1.0 + sl_pct / 100.0) if avg_price > 0 else 0.0
                else:
                    sl_pct = float(
                        params.get("reentry_sl_pct", -2.5)
                        if cycle and cycle.is_reentry
                        else params.get("initial_sl_pct", -3.5)
                        or -3.5
                    )
                    sl_price = avg_price * (1.0 + sl_pct / 100.0) if avg_price > 0 else 0.0
                if cycle and cycle.tp_mode == "PRICE" and cycle.tp_value > 0:
                    tp_price = float(cycle.tp_value)
                    tp_pct = (cycle.tp_value / avg_price - 1.0) * 100.0 if avg_price > 0 else 0.0
                elif cycle and cycle.tp_mode == "PERCENT" and cycle.tp_value > 0:
                    tp_pct = float(cycle.tp_value)
                    tp_price = avg_price * (1.0 + tp_pct / 100.0) if avg_price > 0 else 0.0
                elif "TP" in cycle_modes:
                    tp_pct = take_profit_pct
                    tp_price = avg_price * (1.0 + tp_pct / 100.0) if avg_price > 0 else 0.0
                else:
                    tp_pct = 0.0
                    tp_price = 0.0
                gross = avg_price * quantity * 1000.0
                market_value = market_price * quantity * 1000.0
                buy_fee = _number(row.get("buyFee", row.get("fee", 0)))
                if mode == "PAPER":
                    estimated_exit_cost = market_value * (
                        config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE
                    )
                else:
                    buy_rate = self._cached_fee_rate(symbol, "BUY")
                    sell_rate = self._cached_fee_rate(symbol, "SELL")
                    if buy_fee <= 0 and buy_rate is not None:
                        buy_fee = gross * buy_rate
                    estimated_exit_cost = market_value * sell_rate if sell_rate is not None else 0.0
                fallback_pnl = (
                    float(cycle.net_pnl) if cycle else -abs(buy_fee)
                ) + (market_price - avg_price) * quantity * 1000.0 - estimated_exit_cost
                pnl = float(metrics.get("current_net_pnl", fallback_pnl) or 0.0)
                mae = float(metrics.get("mae_net_pnl", min(0.0, pnl)) or 0.0)
                mfe = float(metrics.get("mfe_net_pnl", max(0.0, pnl)) or 0.0)
                pnl_pct = pnl / gross * 100.0 if gross > 0 else 0.0
                mae_pct = mae / gross * 100.0 if gross > 0 else 0.0
                mfe_pct = mfe / gross * 100.0 if gross > 0 else 0.0
                pending = max(0, quantity - sellable)
                settle = str(row.get("settleDate", "") or "")[:10]
                row_source = str(row.get("source", "") or "").upper()
                if cycle:
                    source = {
                        "BOT": "BOT",
                        "MANUAL": "MANUAL_APP",
                        "MANUAL_VIKING": "MANUAL_APP",
                        "EXTERNAL": "EXTERNAL_DNSE",
                        "EXTERNAL_DNSE": "EXTERNAL_DNSE",
                    }.get(str(cycle.source).upper(), str(cycle.source).upper())
                    if quantity > cycle.open_quantity:
                        source += "+EXTERNAL_DNSE"
                elif row_source == "BOT":
                    source = "BOT"
                elif row_source in {"MANUAL", "MANUAL_VIKING"}:
                    source = "MANUAL_APP"
                else:
                    source = "EXTERNAL_DNSE" if mode == "REAL" else (row_source or "PAPER")
                opened = row.get("openedAt", row.get("createdAt", row.get("time", "")))
                if not opened and cycle:
                    opened = cycle.opened_at
                iid = f"POSITION:{mode}:{trade_id or symbol}:{index}"
                peak_pct = float(metrics.get("peak_profit_pct", pnl_pct) or pnl_pct)
                take_profit_enabled = tp_price > 0
                normal_enabled = "NORMAL" in cycle_modes
                indicator_enabled = "IND_EXIT" in cycle_modes
                decision = runtime_decisions.get(symbol) if isinstance(runtime_decisions.get(symbol), dict) else {}
                if (not decision_is_fresh(decision, symbol, mode)
                        or ((decision.get("details") or {}).get("trade_id")
                            and (decision.get("details") or {})["trade_id"] != trade_id)):
                    decision = {}
                decision_details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
                repeat_effective = bool(
                    params.get("normal_repeat_enabled", False)
                    and float(params.get("normal_sell_pct", 100.0) or 100.0) < 100.0
                )
                normal_state = (
                    "OFF" if not normal_enabled else
                    "DONE" if metrics.get("normal_protection_done") and not repeat_effective else
                    str(decision_details.get("normal_state", "WAIT") or "WAIT").upper()
                )
                decision_checks = (
                    decision_details.get("entry_checks")
                    if isinstance(decision_details.get("entry_checks"), dict) else {}
                )
                indicator_policy = str(
                    decision_details.get(
                        "indicator_exit_policy",
                        params.get("indicator_exit_policy", "ALERT"),
                    ) or "ALERT"
                ).upper()
                indicator_signal = str(decision.get("signal", "")).upper() == "SELL"
                indicator_state = (
                    "OFF" if not indicator_enabled
                    else f"{indicator_policy}·SIGNAL" if indicator_signal
                    else f"{indicator_policy}·WAIT"
                )
                protect_mode = str(
                    decision_details.get("normal_policy", params.get("normal_policy", "AUTO")) or "AUTO"
                ).upper()
                protect_mfe = decision_details.get("normal_mfe_pct")
                protect_trail = decision_details.get("normal_effective_trail_pct")
                protect_price = decision_details.get("normal_trigger_price")
                protect_atr = decision_details.get("normal_atr_pct")
                protect_atr_activation_multiplier = decision_details.get(
                    "normal_atr_activation_multiplier",
                    params.get("normal_atr_activation_multiplier", 0.6),
                )
                protect_activation_mfe = decision_details.get("normal_activation_mfe_pct")
                protect_atr_multiplier = decision_details.get(
                    "normal_atr_multiplier", params.get("normal_atr_multiplier", 0.6),
                )
                protect_retention = decision_details.get(
                    "normal_retention_pct", params.get("normal_retention_pct", 0.0),
                )
                protect_retention_until = decision_details.get(
                    "normal_retention_until_pct",
                    params.get("normal_retention_until_pct", 0.0),
                )
                dynamic_active = bool(decision_details.get(
                    "normal_dynamic_enabled", params.get("normal_dynamic_enabled", False),
                ))
                start_active = dynamic_active and bool(decision_details.get(
                    "normal_atr_activation_enabled", params.get("normal_atr_activation_enabled", True),
                ))
                atr_active = dynamic_active and bool(decision_details.get(
                    "normal_atr_trail_enabled", params.get("normal_atr_trail_enabled", True),
                ))
                keep_active = dynamic_active and bool(decision_details.get(
                    "normal_retention_enabled", params.get("normal_retention_enabled", True),
                )) and _number(protect_retention) > 0
                until_active = bool(decision_details.get(
                    "normal_retention_until_enabled", params.get("normal_retention_until_enabled", True),
                ))
                start_text = (
                    f"START {_number(protect_activation_mfe):.1f}% "
                    f"(ATR1D T−1 {_number(protect_atr):.1f}%×{_number(protect_atr_activation_multiplier):g})"
                    if start_active and protect_atr is not None and protect_activation_mfe is not None
                    else "START CHỜ ATR" if start_active else "START OFF"
                )
                atr_text = (
                    f"LÙI ATR {_number(protect_atr) * _number(protect_atr_multiplier):.1f}% "
                    f"({_number(protect_atr_multiplier):g}×ATR)"
                    if atr_active and protect_atr is not None
                    else "LÙI CHỜ ATR" if atr_active else "ATR TRAIL OFF"
                )
                until_text = f"{_number(protect_retention_until):g}%" if until_active else "ARM"
                keep_text = (
                    f"GIỮ {_number(protect_retention):g}%→{until_text}"
                    if keep_active else "GIỮ OFF"
                )
                protect_sell = decision_details.get(
                    "sell_share_pct", params.get("normal_sell_pct", 100.0),
                )
                pending_close = next(
                    (
                        item for item in close_items
                        if item.id not in consumed_close_ids
                        and (item.trade_id == trade_id or (not item.trade_id and item.symbol == symbol))
                    ),
                    None,
                )
                if pending_close:
                    consumed_close_ids.add(pending_close.id)
                    pending_event = str(pending_close.reason or "").upper()
                    if (
                        pending_close.status.upper() == "WAITING_SETTLEMENT"
                        and pending_event in {"NORMAL_PROTECTION", "PRICE_PROTECTION"}
                    ):
                        normal_state = "T+2"
                settle_short = f"{settle[8:10]}/{settle[5:7]}" if len(settle) == 10 else settle
                settlement_status = "✓ĐÃ VỀ"
                if pending:
                    settlement_status = (
                        f"T+ CACHE·{settle_short or '--'}·{pending}CP"
                        if pending_close and pending_close.status.upper() == "WAITING_SETTLEMENT"
                        else f"T+·{settle_short or '--'}·{pending}CP"
                    )
                stored_entry_state = str(
                    cycle.entry_market_state if cycle else ""
                ).strip().upper()
                entry_state = (
                    stored_entry_state
                    if stored_entry_state not in {"", "UNKNOWN"}
                    else str(decision.get("market_state", "UNKNOWN") or "UNKNOWN").upper()
                )
                entry_exposure = float(
                    (cycle.entry_exposure if cycle else 0.0)
                    or decision_details.get("exposure", 0.0)
                    or 0.0
                )
                entry_budget = float(
                    (cycle.entry_budget if cycle else 0.0)
                    or decision_checks.get("order_budget", 0.0)
                    or 0.0
                )
                exposure_pct = entry_exposure * 100.0 if 0 < entry_exposure <= 1.0 else entry_exposure
                entry_context = entry_state
                if exposure_pct > 0:
                    entry_context += f" {exposure_pct:g}%"
                if entry_budget > 0:
                    entry_context += f" · {entry_budget / 1_000_000.0:.2f}tr"
                take_profit_state = (
                    "OFF" if not take_profit_enabled
                    else "HIT" if market_price + 1e-9 >= tp_price else "WAIT"
                )
                status_parts = [
                    entry_context,
                    settlement_status,
                    f"TP {take_profit_state}" + (f"·+{tp_pct:g}%" if take_profit_enabled else ""),
                    (
                        f"PROTECT {protect_mode}/{normal_state}"
                        f"·MFE {_number(protect_mfe):.1f}%"
                        f"·TRAIL {_number(protect_trail):.1f}%"
                        f"·{start_text}"
                        f"·{atr_text}"
                        f"·{keep_text}"
                        f"·{_display_price(protect_price)}"
                        f"·SELL {_number(protect_sell):g}%"
                    ) if normal_enabled and protect_mfe is not None and protect_trail is not None else (
                        f"PROTECT {protect_mode}/{normal_state}"
                        + ("·CHỜ DỮ LIỆU" if normal_state != "DONE" else "")
                        + f"·SELL {_number(protect_sell):g}%"
                    ) if normal_enabled else "PROTECT OFF",
                    f"E {indicator_state}",
                ]
                if cycle and cycle.is_reentry:
                    status_parts.append("[REENTRY]")
                if pending_close and pending_close.status.upper() != "WAITING_SETTLEMENT":
                    close_label = {
                        "PENDING": "ĐÓNG·CACHE",
                        "WAITING_TOKEN": "ĐÓNG·OTP",
                        "SENDING": "ĐÓNG·GỬI",
                        "WORKING": "ĐÓNG·KHỚP",
                        "PARTIAL": "ĐÓNG·MỘT PHẦN",
                        "UNKNOWN": "ĐÓNG·KIỂM TRA",
                    }.get(pending_close.status.upper(), "ĐÓNG·CHỜ")
                    status_parts.insert(0, close_label)
                if pending_close and pending_close.status.upper() == "WAITING_SETTLEMENT":
                    row_tag = "position_waiting"
                elif pending_close:
                    row_tag = "position_closing"
                elif pending:
                    row_tag = "position_waiting"
                elif pnl > 0:
                    row_tag = "position_profit"
                elif pnl < 0:
                    row_tag = "position_loss"
                else:
                    row_tag = "position_flat"
                pnl_icon = "▲" if pnl > 0 else "▼" if pnl < 0 else "•"
                risk_pct = abs(sl_pct) if sl_pct else 0.0
                reward_pct = tp_pct if tp_price > 0 else 0.0
                rr_text = (
                    f"R {risk_pct:.2f}% · E {reward_pct:.2f}% · 1:{reward_pct / risk_pct:.2f}"
                    if risk_pct > 0 and reward_pct > 0
                    else (f"R -- · E {reward_pct:.2f}%" if not sl_enabled else f"R {risk_pct:.2f}% · E --")
                )
                sl_text = (
                    f"SL▼ {_display_price(sl_price)} ({sl_pct:+g}%)"
                    if sl_enabled else "SL OFF"
                )
                self._update_running_row(
                    tree, existing, row_order, iid=iid, tags=(row_tag,),
                    values=(
                        f"#{(trade_id or str(row.get('positionId', index)))[:12]}",
                        self._row_time(opened),
                        f"{mode} · {source} · BUY {symbol} @ {_display_price(avg_price)} · KL {quantity}",
                        f"{sl_text} · "
                        + (f"TP▲ {_display_price(tp_price)} ({tp_pct:+g}%)" if tp_price > 0 else "TP▲ --"),
                        f"FEE {_compact_vnd(buy_fee)} · DỰ KIẾN BÁN {_compact_vnd(estimated_exit_cost)}",
                        rr_text,
                        f"{pnl_icon}{_compact_vnd(pnl)} ({pnl_pct:+.2f}%)"
                        f" · ↘{_compact_vnd(mae)} ({mae_pct:+.2f}%)"
                        f" · ↗{_compact_vnd(mfe)} ({mfe_pct:+.2f}%)",
                        " · ".join(status_parts),
                        "",
                    ),
                )
                self._running_row_actions[mode][iid] = {
                    "kind": "position", "mode": mode, "symbol": symbol,
                    "trade_id": trade_id, "position": row,
                    "local_id": pending_close.id if pending_close else "",
                    "broker_order_id": pending_close.broker_order_id if pending_close else "",
                    "cancellable": bool(pending_close and (
                        pending_close.status.upper() in LOCALLY_CONTROLLABLE_STATUSES
                        or (mode == "REAL" and pending_close.broker_order_id
                            and pending_close.status.upper() in {"WORKING", "PARTIAL"})
                    )),
                }

            # Include close requests merged into a position row as well.
            local_broker_ids = {value for item in local_by_mode[mode]
                                for value in [item.broker_order_id, *item.broker_order_ids] if value}
            for item in reversed(local_by_mode[mode]):
                if item.id in consumed_close_ids:
                    continue
                if item.broker_order_id:
                    local_broker_ids.add(item.broker_order_id)
                local_broker_ids.update(item.broker_order_ids)
                price_text = _display_price(item.limit_price) if item.limit_price > 0 else item.order_type
                gross = item.limit_price * item.quantity * 1000.0 if item.limit_price > 0 else 0.0
                cycle = cycles_by_id.get(item.trade_id) if item.trade_id else None
                display_source = {
                    "MANUAL": "MANUAL_APP",
                    "MANUAL_VIKING": "MANUAL_APP",
                    "EXTERNAL": "EXTERNAL_DNSE",
                    "EXTERNAL_DNSE": "EXTERNAL_DNSE",
                }.get(str(item.source).upper(), str(item.source).upper())
                item_modes = set(cycle.em_modes if cycle else item.em_modes)
                item_take_profit = "TP" in item_modes or (item.tp_mode in {"PRICE", "PERCENT"} and item.tp_value > 0)
                item_normal = "NORMAL" in item_modes
                item_indicator = "IND_EXIT" in item_modes
                item_sl_enabled = bool(cycle.sl_enabled) if cycle else bool(item.sl_enabled)
                if not item_sl_enabled:
                    item_sl_label = "OFF"
                elif item.sl_mode == "PRICE" and item.sl_value > 0:
                    item_sl_label = _display_price(item.sl_value)
                elif item.sl_mode == "PERCENT" and item.sl_value:
                    item_sl_label = f"{-abs(item.sl_value):+g}%"
                else:
                    item_sl_label = f"{float(params.get('initial_sl_pct', -3.5) or -3.5):+g}%"
                if item.tp_mode == "PRICE" and item.tp_value > 0:
                    item_tp_label = _display_price(item.tp_value)
                elif item.tp_mode == "PERCENT" and item.tp_value > 0:
                    item_tp_label = f"+{item.tp_value:g}%"
                elif item_take_profit:
                    item_tp_label = f"+{take_profit_pct:g}%"
                else:
                    item_tp_label = "--"
                target_text = (
                    "ĐÓNG VỊ THẾ" if item.action == "CLOSE"
                    else f"SL {item_sl_label}   ·   TP {item_tp_label}"
                )
                em_text = (
                    f"TP {item_tp_label if item_take_profit else 'OFF'}"
                    f"   ·   PROTECT {'+' + format(normal_tp, 'g') + '%' if item_normal else 'OFF'}"
                    f"   ·   E {'ON' if item_indicator else 'OFF'}"
                    if item.action == "OPEN" else "--"
                )
                status_upper = item.status.upper()
                is_working = status_upper in {"WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"}
                cancellable = status_upper in LOCALLY_CONTROLLABLE_STATUSES or (
                    mode == "REAL" and bool(item.broker_order_id) and is_working
                )
                editable = status_upper in LOCALLY_CONTROLLABLE_STATUSES or (
                    mode == "REAL" and bool(item.broker_order_id) and item.order_type == "LO" and is_working
                )
                if status_upper in {"CANCEL_PENDING", "REPLACE_PENDING", "UNKNOWN"}:
                    editable = cancellable = False
                iid = f"LOCAL:{item.id}"
                tag = (
                    "settlement_order" if status_upper == "WAITING_SETTLEMENT" else
                    "partial_order" if status_upper == "PARTIAL" else
                    "sending_order" if status_upper == "SENDING" else
                    "error_order" if status_upper == "UNKNOWN" else
                    "dnse_order" if is_working else
                    "pending_order"
                )
                status_label = {
                    "PENDING": "[CACHE] CHỜ GỬI",
                    "WAITING_TOKEN": "[CACHE] CHỜ TOKEN",
                    "WAITING_SETTLEMENT": "[T+2] CHỜ CỔ VỀ",
                    "PAUSED": "[CACHE] TẠM DỪNG",
                    "SENDING": "[DNSE] ĐANG GỬI",
                    "WORKING": "[DNSE] CHỜ KHỚP",
                    "PARTIAL": "[DNSE] KHỚP MỘT PHẦN",
                    "UNKNOWN": "[DNSE] CHƯA RÕ TRẠNG THÁI",
                    "CANCEL_PENDING": "[DNSE] CHỜ XÁC NHẬN HỦY",
                    "REPLACE_PENDING": "[DNSE] CHỜ XÁC NHẬN SỬA",
                }.get(status_upper, f"[{status_upper}]")
                if mode == "PAPER":
                    status_label = status_label.replace("[DNSE]", "[PAPER]")
                estimated_fee = (
                    self._preview_buy_fee(gross, item.symbol, mode)
                    if item.side == "BUY" else None
                )
                fee_text = _compact_vnd(estimated_fee) if estimated_fee is not None else "--"
                risk_text = item_sl_label
                reward_text = item_tp_label
                self._update_running_row(
                    tree, existing, row_order, iid=iid, tags=(tag,),
                    values=(
                        f"[CACHE] {item.id[:8]}",
                        self._row_time(item.created_at),
                        f"{mode} · {display_source} · {item.side} {item.symbol} @ {price_text} · KL {item.quantity}",
                        target_text,
                        f"FEE {fee_text}" if gross > 0 else f"HẾT HẠN {self._row_time(item.expires_at)}",
                        f"R {risk_text} · E {reward_text}" if item.action == "OPEN" else "--",
                        f"-- · -- · -- · Khớp {item.filled_quantity}/{item.quantity}",
                        f"{status_label} · {em_text} · {item.result or item.reason or 'ĐANG CHỜ'}",
                        "✖" if cancellable else "",
                    ),
                )
                self._running_row_actions[mode][iid] = {
                    "kind": "local", "mode": mode, "symbol": item.symbol,
                    "local_id": item.id, "broker_order_id": item.broker_order_id,
                    "order_type": item.order_type, "quantity": item.quantity,
                    "price": item.limit_price, "editable": editable, "cancellable": cancellable,
                    "pausable": status_upper in CLAIMABLE_STATUSES,
                    "resumable": status_upper == "PAUSED",
                    "status": status_upper, "source": item.source, "side": item.side,
                    "result": item.result, "reason": item.reason,
                    "expires_at": item.expires_at,
                }

            for index, row in enumerate(reversed(broker_orders)):
                order_id = str(row.get("orderId", row.get("id", "")) or "")
                if order_id and order_id in local_broker_ids:
                    continue
                status = str(row.get("orderStatus", row.get("status", "")) or "").upper()
                compact = status.replace("_", "").replace(" ", "")
                partial = "PART" in compact
                pending_ack = {
                    "PENDINGCANCEL": "CHỜ XÁC NHẬN HỦY", "CANCELPENDING": "CHỜ XÁC NHẬN HỦY",
                    "PENDINGREPLACE": "CHỜ XÁC NHẬN SỬA", "REPLACEPENDING": "CHỜ XÁC NHẬN SỬA",
                    "UNKNOWN": "CHƯA RÕ TRẠNG THÁI",
                }.get(compact, "")
                if not pending_ack and not partial and any(token in compact for token in ("FILLED", "MATCHED", "CANCEL", "REJECT", "EXPIRED", "DONE", "COMPLETED")):
                    continue
                symbol = str(row.get("symbol", row.get("instrumentId", "?")) or "?").upper()
                side_raw = str(row.get("side", "") or "").upper()
                side = "BUY" if side_raw in {"NB", "BUY"} else "SELL" if side_raw in {"NS", "SELL"} else side_raw
                kind = str(row.get("orderType", "") or "")
                price = account_price(row, row.get("price", row.get("orderPrice", 0)))
                quantity = int(_number(row.get("quantity", row.get("orderQuantity", 0))))
                filled = int(_number(row.get("fillQuantity", row.get("filledQuantity", 0))))
                remaining = int(_number(row.get("leaveQuantity", max(0, quantity - filled))))
                gross = price * quantity * 1000.0
                fee = _number(row.get("fee", row.get("totalFee", 0)))
                if fee <= 0 and gross > 0:
                    fee_rate = self._cached_fee_rate(symbol, side)
                    if fee_rate is not None:
                        fee = gross * fee_rate
                iid = f"BROKER:{mode}:{order_id or index}"
                self._update_running_row(
                    tree, existing, row_order, iid=iid,
                    tags=(("error_order",) if compact == "UNKNOWN" else ("partial_order",) if partial else ("dnse_order",)),
                    values=(
                        f"[DNSE] {(order_id or str(index))[:10]}",
                        self._row_time(row.get('createdAt', row.get('createdDate', ''))),
                        f"{mode} · EXTERNAL_DNSE · {side} {symbol} @ {_display_price(price) if price else kind} · KL {quantity}",
                        "SL/TP SAU KHI KHỚP" if side == "BUY" else "ĐÓNG VỊ THẾ",
                        f"FEE {_compact_vnd(fee)}",
                        "--",
                        f"-- · -- · -- · Khớp {filled}/{quantity}",
                        f"[DNSE][{pending_ack or ('PARTIAL' if partial else 'WORKING')}] · CÒN {remaining}",
                        "✖" if mode == "REAL" and bool(order_id) and not pending_ack else "",
                    ),
                )
                self._running_row_actions[mode][iid] = {
                    "kind": "broker", "mode": mode, "symbol": symbol,
                    "broker_order_id": order_id, "order_type": kind,
                    "quantity": quantity, "filled_quantity": filled, "price": price,
                    "editable": mode == "REAL" and kind == "LO" and not pending_ack,
                    "cancellable": mode == "REAL" and bool(order_id) and not pending_ack,
                }
            current_ids = set(row_order)
            removed = [iid for iid in previous_order if iid not in current_ids]
            if removed:
                tree.delete(*removed)
            if tuple(tree.get_children()) != tuple(row_order):
                for index, iid in enumerate(row_order):
                    tree.move(iid, "", index)
            restored = tuple(iid for iid in selected_before if iid in current_ids)
            if tuple(tree.selection()) != restored:
                tree.selection_set(restored)
            if yview_before and previous_order != tuple(row_order):
                tree.yview_moveto(yview_before[0])
        self._sync_cancel_button()

    def _log(self, message: str, target: str = "manual") -> None:
        self.logger.info(message)
        target = "bot" if target == "bot" else "manual"
        widget = self.log_bot if target == "bot" else self.log_manual
        if widget.winfo_exists():
            widget.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
            # Bound the on-screen Text buffer for 24/7 sessions, not the file log.
            last_line = int(widget.index("end-1c").split(".", 1)[0])
            if last_line > VISIBLE_LOG_LINE_LIMIT + 1:
                widget.delete("1.0", f"{last_line - VISIBLE_LOG_LINE_LIMIT}.0")
            widget.see("end")
            active = self.log_tabview.get() if hasattr(self, "log_tabview") else ""
            active_target = "bot" if active == "Bot" else "manual" if active == "Manual" else ""
            if active_target != target:
                self._set_log_unread(target, True)

    def close(self) -> None:
        self.running = False
        self.execution.stopping = True
        if getattr(self, "telegram", None):
            self.telegram.cancel_pending_buys()
        for popup in (
            getattr(self, "_backtest_popup", None),
            getattr(self, "_history_popup", None),
            getattr(self, "_info_popup", None),
        ):
            if popup and popup.top.winfo_exists():
                popup.close()
        for popup in (
            getattr(self, "_rule_settings_popup", None),
            getattr(self, "_advanced_popup", None),
        ):
            if popup and popup.top.winfo_exists():
                popup._close()
        try:
            self.bridge.disarm(self.settings.watchlist, self.settings.paper_mode)
        except Exception:
            pass
        self._stop_daemon()
        self._io_executor.shutdown(wait=True, cancel_futures=True)
        self.real.close()
        if getattr(self, "_account_lease", None):
            self._account_lease.close()
        self.destroy()
