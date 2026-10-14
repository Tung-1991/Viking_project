from __future__ import annotations

from datetime import datetime
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

from .. import config
from ..services.runtime import recent_ui_logs
from ..trading.orders import CLAIMABLE_STATUSES, FINAL_STATUSES, LOCALLY_CONTROLLABLE_STATUSES
from ..trading.portfolio import account_price
from ..trading.validation import decision_is_fresh, decisions_for_mode
from .view import _compact_vnd, _display_price, _number, _price_unit
from .account_money import displayed_account_snapshot


RUNNING_COLUMNS = (
    "Ticket",
    "Time",
    "Order",
    "Targets",
    "CostInfo",
    "RR",
    "PnL_MAE_MFE",
    "Status",
    "Override",
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
    "Override": "BOT / Override",
}
RUNNING_WIDTHS = {
    "Ticket": 170,
    "Time": 230,
    "Order": 560,
    "Targets": 390,
    "CostInfo": 390,
    "RR": 310,
    "PnL_MAE_MFE": 660,
    "Status": 900,
    "Override": 160,
}
RUNNING_FONT = ("Segoe UI", 16)
RUNNING_HEADING_FONT = ("Segoe UI", 15, "bold")
RUNNING_COLUMN_PADDING = 48
# Measure representative cells with the same font that operators see. In
# particular, PNL/MAE/MFE must have room for all three amounts and percentages.
RUNNING_SAMPLES = {
    "Ticket": "#b7d6775a40d9",
    "Time": "13/10/2026 14:09:45",
    "Order": "REAL · BOT · BUY HDB @ 23,150 · KL 600",
    "Targets": "SL▼ 21,857 (-3.5%) · TP▲ 24,770 (+7.0%)",
    "CostInfo": "PHÍ MUA ~16.3K · DỰ KIẾN BÁN 16.7K",
    "RR": "R 3.50% · E 7.00% · 1:2.00",
    "PnL_MAE_MFE": "▲300.0K (+2.21%) · MAE 22,450 (-0.88%) · MFE 23,450 (+3.53%)",
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
    "Override": "center",
}
VISIBLE_LOG_LINE_LIMIT = 2000


class DashboardTablesMixin:
    @staticmethod
    def _running_column_width(tree: ttk.Treeview, column: str) -> int:
        """Fit the header and representative content without reducing the font."""
        configured = RUNNING_WIDTHS.get(column, 180)
        try:
            root = tree.winfo_toplevel()
            heading = tkfont.Font(root=root, font=RUNNING_HEADING_FONT)
            body = tkfont.Font(root=root, font=RUNNING_FONT)
            measured = max(heading.measure(RUNNING_HEADERS.get(column, column)),
                           body.measure(RUNNING_SAMPLES.get(column, "")))
            return max(configured, measured + RUNNING_COLUMN_PADDING)
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

    def _select_all_running_positions(self, event: Any) -> str:
        tree = event.widget
        mode = "REAL" if tree is self.trees.get("REAL") else "PAPER"
        actions = self._running_row_actions.get(mode, {})
        positions = [iid for iid in tree.get_children() if actions.get(iid, {}).get("kind") == "position"]
        tree.selection_set(positions or tree.get_children())
        self._sync_cancel_button()
        return "break"

    @staticmethod
    def _configure_tree(tree: ttk.Treeview, columns: tuple[str, ...]) -> None:
        tree.configure(columns=columns, show="tree headings")
        tree.heading("#0", text="")
        tree.column("#0", width=40, minwidth=40, stretch=False)
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
        if isinstance(tree, ttk.Treeview) and not hasattr(tree, "_viking_column_hint"):
            from .running_hints import RunningTableHint
            tree._viking_column_hint = RunningTableHint(tree, columns)
        if isinstance(tree, ttk.Treeview) and not hasattr(tree, "_viking_override_cells"):
            from .override_cells import OverrideCells
            tree._viking_override_cells = OverrideCells(tree)

    @staticmethod
    def _running_rows(tree: ttk.Treeview, parent: str = "") -> tuple[str, ...]:
        """Read all rows, including children of collapsed positions."""
        rows: list[str] = []
        for iid in tree.get_children(parent):
            rows.append(iid)
            rows.extend(DashboardTablesMixin._running_rows(tree, iid))
        return tuple(rows)

    @staticmethod
    def _running_position_parent(actions: dict[str, dict[str, Any]], intent: Any) -> str:
        """Group only an order with one explicitly linked position in this book."""
        if not intent.trade_id:
            return ""
        candidates = [
            iid for iid, action in actions.items()
            if action.get("kind") == "position"
            and action.get("symbol") == intent.symbol
            and action.get("trade_id") == intent.trade_id
            and (not intent.loan_package_id
                 or action.get("loan_package_id") == intent.loan_package_id)
        ]
        return candidates[0] if len(candidates) == 1 else ""

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
            any(displayed_account_snapshot(self, mode)[1] for mode in self.trees)
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
            tree._viking_row_hints = {}
            tree._viking_override_rows = {}
            runtime_decisions = decisions_for_mode(
                runtime_status, mode, default_paper=getattr(self.settings, "paper_mode", True),
            )
            selected_before = tuple(tree.selection())
            yview_before = tree.yview()
            previous_order = self._running_rows(tree)
            previous_parents = {tree.parent(iid) for iid in previous_order if tree.parent(iid)}
            existing = {iid: tree.item(iid) for iid in previous_order}
            row_order: list[str] = []
            row_parents: dict[str, str] = {}
            self._running_row_actions[mode] = {}
            if tuple(tree["columns"]) != columns:
                self._configure_tree(tree, columns)
            balance, positions, broker_orders = displayed_account_snapshot(self, mode)
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
                from ..trading.position_controls import position_exit_params
                params = position_exit_params(self.settings.rule_parameters, cycle)
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
                buy_fee_text = f"PHÍ MUA {_compact_vnd(buy_fee)}"
                if mode == "PAPER":
                    estimated_exit_cost = market_value * (
                        config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE
                    )
                else:
                    buy_rate = self._cached_fee_rate(symbol, "BUY")
                    sell_rate = self._cached_fee_rate(symbol, "SELL")
                    if buy_fee <= 0 and buy_rate is not None:
                        buy_fee = gross * buy_rate
                        buy_fee_text = f"PHÍ MUA ~{_compact_vnd(buy_fee)}"
                    elif buy_fee <= 0 and not any(key in row for key in ("buyFee", "fee")):
                        buy_fee_text = "PHÍ MUA CHỜ DNSE"
                    if cycle and cycle.fees_paid > 0:
                        buy_fee_text = f"PHÍ ĐÃ GHI {_compact_vnd(cycle.fees_paid)}"
                    estimated_exit_cost = market_value * sell_rate if sell_rate is not None else 0.0
                fallback_pnl = (
                    float(cycle.net_pnl) if cycle else -abs(buy_fee)
                ) + (market_price - avg_price) * quantity * 1000.0 - estimated_exit_cost
                pnl = float(metrics.get("current_net_pnl", fallback_pnl) or 0.0)
                mae = float(metrics.get("mae_net_pnl", min(0.0, pnl)) or 0.0)
                mfe = float(metrics.get("mfe_net_pnl", max(0.0, pnl)) or 0.0)
                basis = (cycle.buy_notional if cycle else 0) or _number(metrics.get("entry_notional_vnd")) or gross
                pnl_pct = pnl / basis * 100.0 if basis > 0 else 0.0
                history_verified = metrics.get("price_history_verified") is True
                history_untrusted = (metrics.get("price_history_verified") is False or (
                    "peak_profit_pct" in metrics and metrics.get("market_data_policy") != "G1_MATCH_V1"))
                extrema_text = "MAE — · MFE — · CHỜ XÁC MINH"
                if history_verified:
                    mae_pct, mfe_pct = _number(metrics.get("mae_pct")), _number(metrics.get("mfe_pct"))
                    mae_price, mfe_price = avg_price * (1 + mae_pct / 100), avg_price * (1 + mfe_pct / 100)
                    extrema_text = f"MAE {_display_price(mae_price)} ({mae_pct:+.2f}%) · MFE {_display_price(mfe_price)} ({mfe_pct:+.2f}%)"
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
                if cycle and (not cycle.auto_sell_enabled or not cycle.bot_trade_enabled):
                    indicator_policy = "ALERT"
                indicator_signal = str(decision.get("signal", "")).upper() == "SELL"
                indicator_state = (
                    "OFF" if not indicator_enabled
                    else f"{indicator_policy}·SIGNAL" if indicator_signal
                    else f"{indicator_policy}·WAIT"
                )
                from ..rules.protection import policy_for_stage
                protect_mode = policy_for_stage(params, str(decision_details.get("protection_stage", "ARM")))
                if cycle and (not cycle.auto_sell_enabled or not cycle.bot_trade_enabled):
                    protect_mode = "ALERT"
                if (cycle and ((str(decision_details.get("protection_stage", "ARM")) == "ARM" and
                                params.get("normal_arm_enabled") is False) or
                               (str(decision_details.get("protection_stage", "ARM")) == "DYN" and
                                params.get("normal_dynamic_enabled") is False))):
                    protect_mode = "OFF"
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
                        and pending_event in {"NORMAL_PROTECTION", "DYNAMIC_PROTECTION", "PRICE_PROTECTION"}
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
                settlement_status = (
                    f"BÁN ĐƯỢC {sellable} CP · CHỜ VỀ {pending} CP · {settlement_status}"
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
                compact_settlement = f"BÁN ĐƯỢC {sellable} CP" + (f" · CHỜ VỀ {pending} CP" if pending else "")
                paused = bool(cycle and (not cycle.auto_sell_enabled or not cycle.bot_trade_enabled))
                compact_status = [compact_settlement,
                    f"TP {take_profit_state}" + (f"·+{tp_pct:g}%" if take_profit_enabled else ""),
                    "PROTECT CHỜ XÁC MINH" if normal_enabled and history_untrusted else
                    "PROTECT ALERT" if normal_enabled and paused else
                    f"PROTECT {protect_mode}/{normal_state}" + ("·CHỜ DỮ LIỆU" if protect_mfe is None and normal_state != "DONE" else "")
                    if normal_enabled else "PROTECT OFF",
                    "E ALERT" if indicator_enabled and paused else f"E {indicator_state}"]
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
                    compact_status.insert(0, close_label)
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
                from ..trading.position_controls import position_override_items, position_bot_cell
                override_items = position_override_items(self.settings, cycle)
                if override_items:
                    tree._viking_override_rows[iid] = override_items
                self._update_running_row(
                    tree, existing, row_order, iid=iid, tags=(row_tag,),
                    values=(
                        f"#{(trade_id or str(row.get('positionId', index)))[:12]}",
                        self._row_time(opened),
                        f"{mode} · {source} · BUY {symbol} @ {_display_price(avg_price)} · KL {quantity}",
                        f"{sl_text} · "
                        + (f"TP▲ {_display_price(tp_price)} ({tp_pct:+g}%)" if tp_price > 0 else "TP▲ --"),
                        f"{buy_fee_text} · DỰ KIẾN BÁN {_compact_vnd(estimated_exit_cost)}",
                        rr_text,
                        f"{pnl_icon}{_compact_vnd(pnl)} ({pnl_pct:+.2f}%)"
                        f" · {extrema_text}",
                        " · ".join(value for value in compact_status if value),
                        position_bot_cell(self.settings, cycle),
                    ),
                )
                evidence = metrics.get("price_history_evidence") or {}
                since = evidence.get("started_at") if evidence.get("coverage") == "FROM_RESTART" else None
                period = f"Đo lại từ {self._row_time(since)}" if since else "Theo dõi từ khi mua"
                tree._viking_row_hints[iid] = {
                    "Ticket": f"Vị thế #{trade_id}\nChuột phải → Mở bảng: sửa TP/SL, ARM/DYNAMIC/E hoặc đo lại MAE/MFE.",
                    "Order": f"{mode} · {source} · {symbol}\nGiá vốn {_display_price(avg_price)} · Còn {quantity:,} CP"
                             + (f"\nĐã mua {cycle.entry_quantity:,} CP · Bán khớp {cycle.sold_quantity:,} CP" if cycle else ""),
                    "PnL_MAE_MFE": f"PnL {_compact_vnd(pnl)} / vốn mua gốc {_compact_vnd(basis)} = {pnl_pct:+.2f}%\n"
                                    f"MAE/MFE theo giá vốn {_display_price(avg_price)}. {period}.\n"
                                    + (extrema_text if history_verified else
                                       "Lịch sử cũ chưa xác minh; không dùng cho PROTECT. Mở bảng vị thế để đo lại từ giá G1."),
                    "Status": "\n".join(status_parts) + ("\nBOT vị thế: Alert, SL/TP/PROTECT/E chỉ báo." if cycle and not cycle.bot_trade_enabled else
                                                             "\nTỰ BÁN TẠM DỪNG: SL/TP/PROTECT/E chỉ báo." if paused else ""),
                    "Override": "Cấu hình riêng của vị thế:\n" + "\n".join(override_items) +
                                "\nTP/SL AUTO hoặc chế độ Theo RULE để bỏ ghi đè."
                                if override_items else "BOT vị thế: Bật, tuân theo BOT chung và RULE.\nKhông có cấu hình riêng. X = ghi đè; chuột phải → Mở bảng để chỉnh."
                                if cycle else "Chưa quản lý vị thế này; chuột phải → Mở bảng để cấu hình.",
                }
                self._running_row_actions[mode][iid] = {
                    "kind": "position", "mode": mode, "symbol": symbol,
                    "trade_id": trade_id, "position": row,
                    "loan_package_id": str(row.get("loanPackageId", cycle.loan_package_id if cycle else "") or ""),
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
                from ..trading.position_controls import position_override_items, position_bot_cell
                override_items = position_override_items(self.settings, cycle or item) if cycle or item.action == "OPEN" else []
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
                remaining_label = f"CÒN {item.remaining_quantity}"
                if status_upper in {"WORKING", "PARTIAL"}:
                    remaining_label += " CHỜ KHỚP"
                elif status_upper == "WAITING_SETTLEMENT":
                    remaining_label += " CHỜ CỔ VỀ ĐỂ BÁN"
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
                        f"{status_label} · Khớp {item.filled_quantity}/{item.quantity} · "
                        f"{remaining_label} · {em_text} · {item.result or item.reason or 'ĐANG CHỜ'}",
                        position_bot_cell(self.settings, cycle) if cycle else
                        ("Bật · X" if override_items else "Bật") if item.source in {"BOT", "EM"} else
                        "X" if override_items else "",
                    ),
                )
                tree._viking_row_hints[iid] = {"Override": "\n".join(override_items) if override_items else "Không có cấu hình riêng."}
                if override_items:
                    tree._viking_override_rows[iid] = override_items
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
                parent = self._running_position_parent(self._running_row_actions[mode], item)
                if parent:
                    row_parents[iid] = parent

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
                        f"[DNSE][{pending_ack or ('PARTIAL' if partial else 'WORKING')}] · "
                        f"Khớp {filled}/{quantity} · CÒN {remaining}"
                        + (" CHỜ KHỚP" if not pending_ack else ""),
                        "",
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
            root_order = [iid for iid in row_order if iid not in row_parents]
            child_order: dict[str, list[str]] = {}
            for iid in row_order:
                if iid in row_parents:
                    child_order.setdefault(row_parents[iid], []).append(iid)
            # Move live rows before deleting an obsolete parent. Treeview
            # deletes descendants too; a lagging position snapshot must not
            # hide its still-working order or lose the operator's selection.
            if tuple(tree.get_children()) != tuple(root_order):
                for index, iid in enumerate(root_order):
                    tree.move(iid, "", index)
            for parent, children in child_order.items():
                if tuple(tree.get_children(parent)) != tuple(children):
                    for index, iid in enumerate(children):
                        tree.move(iid, parent, index)
                if parent not in previous_parents:
                    tree.item(parent, open=True)
            removed = [iid for iid in previous_order if iid not in current_ids]
            if removed:
                removed_ids = set(removed)
                tree.delete(*(iid for iid in removed if tree.parent(iid) not in removed_ids))
            restored = tuple(iid for iid in selected_before if iid in current_ids)
            if tuple(tree.selection()) != restored:
                tree.selection_set(restored)
            if yview_before and previous_order != self._running_rows(tree):
                tree.yview_moveto(yview_before[0])
            cells = getattr(tree, "_viking_override_cells", None)
            if cells:
                cells.schedule()
        self._sync_cancel_button()

    def _log(self, message: str, target: str = "manual") -> None:
        target = "bot" if target == "bot" else "manual"
        self.logger.info(message, extra={"ui_target": target})
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

    def _restore_visible_logs(self) -> None:
        """Restore text only: no queue changes, unread marks, or log replay."""
        if getattr(self, "_visible_logs_restored", False):
            return
        self._visible_logs_restored = True
        recent = recent_ui_logs(self.bridge.log_dir)
        for target in ("manual", "bot"):
            widget = self.log_bot if target == "bot" else self.log_manual
            rows = [row for row in recent if row["target"] == target][-200:]
            if rows and widget.winfo_exists():
                lines = [f"[{row['ts'][:19].replace('T', ' ')}] {row['message']}" for row in rows]
                widget.insert("end", "Nhật ký đã lưu · chỉ xem lại, không gửi lại lệnh\n" + "\n".join(lines) + "\n")
                last_line = int(widget.index("end-1c").split(".", 1)[0])
                if last_line > VISIBLE_LOG_LINE_LIMIT + 1:
                    widget.delete("1.0", f"{last_line - VISIBLE_LOG_LINE_LIMIT}.0")
                widget.see("end")

    def close(self) -> None:
        self.running = False
        self._stop_ema_history_preview()
        self._cancel_rule_preload()
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
