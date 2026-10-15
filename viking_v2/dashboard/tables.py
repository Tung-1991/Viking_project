from __future__ import annotations

from datetime import datetime
import math
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

from .. import config
from ..services.runtime import recent_ui_logs
from ..trading.orders import CLAIMABLE_STATUSES, FINAL_STATUSES, LOCALLY_CONTROLLABLE_STATUSES
from ..trading.market import VN_TZ
from ..trading.buy_watch import watch_visible, watch_armable, submitted_day
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
    "Bot",
    "Override",
    "STT",
)
RUNNING_HEADERS = {
    "Ticket": "Ticket",
    "Time": "Thời gian",
    "Order": "Lệnh / Vị thế",
    "Targets": "SL / TP / PROTECT",
    "CostInfo": "Phí mua / bán",
    "RR": "Rủi ro/Kỳ vọng (%)",
    "PnL_MAE_MFE": "PnL · MAE / MFE",
    "Status": "Trạng thái",
    "Bot": "BOT",
    "Override": "Override",
    "STT": "STT",
}
RUNNING_WIDTHS = {
    "Ticket": 170,
    "Time": 150,
    "Order": 245,
    "Targets": 270,
    "CostInfo": 125,
    "RR": 310,
    "PnL_MAE_MFE": 270,
    "Status": 520,
    "Bot": 85,
    "Override": 80,
    "STT": 52,
}
RUNNING_FONT = ("Segoe UI", 14)
RUNNING_HEADING_FONT = ("Segoe UI", 14, "bold")
RUNNING_COLUMN_PADDING = 20
# Measure representative cells with the same font that operators see. In
# particular, PNL/MAE/MFE must have room for all three amounts and percentages.
RUNNING_SAMPLES = {
    "Ticket": "#b7d6775a40d9",
    "Time": "13/10/2026 14:09:45",
    "Order": "BUY HDB · KL 600 @ 23,150\nREAL · BOT · 13/10 14:09\nARM 24,236 · START 22,939",
    "Targets": "SL -3.5% -317.1K @ 21,857\nTP +7% +634.2K @ 24,236\nPROTECT DYN 23,046 · ALERT",
    "CostInfo": "MUA ~16.3K\nBÁN ~16.7K",
    "RR": "R 3.50% · E 7.00% · 1:2.00",
    "PnL_MAE_MFE": "PnL +300.0K (+2.21%)\nMAE -0.88% · 22,450 · -80.0K\nMFE +3.53% · 23,450 · +320.0K",
    "Status": "BÁN ĐƯỢC 400 CP · CHỜ VỀ 100 CP · 15/10 13:00 · CÒN 1g20p\nARM AUTO: CHỜ 76,398 · DYN ALERT: START 72,374 · E AUTO: CHỜ\nGIỮ 72%: 23,046 · ATR TRAIL: 22,769 · Cách mốc 54đ/CP",
}
RUNNING_ANCHORS = {
    "Ticket": "center",
    "Time": "center",
    "Order": "w",
    "Targets": "w",
    "CostInfo": "w",
    "RR": "center",
    "PnL_MAE_MFE": "w",
    "Status": "w",
    "Bot": "center",
    "Override": "center",
    "STT": "center",
}
VISIBLE_LOG_LINE_LIMIT = 2000


def _trade_money(value: float, *, signed: bool = False) -> str:
    return f"{float(value) / 1000:+,.1f}K" if signed else f"{float(value) / 1000:,.1f}K"


def _running_scale(root) -> float:
    try:
        return ScalingTracker.get_widget_scaling(root)
    except (AttributeError, KeyError):
        return 1.0


def running_font(root, *, heading=False) -> tuple:
    base = RUNNING_HEADING_FONT if heading else RUNNING_FONT
    # ttk does not receive CTk's Windows DPI/widget scaling callbacks.
    return (base[0], -round(base[1] * _running_scale(root)), *base[2:])


def running_row_height(root) -> int:
    return max(round(56 * _running_scale(root)),
               tkfont.Font(root=root, font=running_font(root)).metrics("linespace") * 3 + round(8 * _running_scale(root)))


def _order_caption(mode: str, source: str, side: str, symbol: str, quantity: int, price: str, opened: str) -> str:
    source = source.replace("EXTERNAL_DNSE", "DNSE").replace("MANUAL_APP", "TAY")
    clock = str(opened or "")
    for pattern in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            clock = datetime.strptime(clock[:19].replace("T", " "), pattern).strftime("%d/%m %H:%M")
            break
        except ValueError:
            continue
    return (f"{side} {symbol} · KL {quantity:,} @ {price}\n{mode} · {source}"
            + (f" · {clock}" if clock and clock not in {"--", "None"} else ""))


class DashboardTablesMixin:
    @staticmethod
    def _running_column_width(tree: ttk.Treeview, column: str) -> int:
        """Fit the header and representative content without reducing the font."""
        configured = RUNNING_WIDTHS.get(column, 180)
        try:
            root = tree.winfo_toplevel()
            scale = _running_scale(root)
            heading = tkfont.Font(root=root, font=running_font(root, heading=True))
            body = tkfont.Font(root=root, font=running_font(root))
            measured = max(heading.measure(RUNNING_HEADERS.get(column, column)),
                           *(body.measure(line) for line in RUNNING_SAMPLES.get(column, "").split("\n")))
            return max(round(configured * scale), measured + round(RUNNING_COLUMN_PADDING * scale))
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
        if isinstance(tree, ttk.Treeview):
            tree.configure(displaycolumns=("STT", *(key for key in columns if key not in {"Ticket", "Time", "RR", "STT"})))
        tree.heading("#0", text="")
        tree.column("#0", width=24, minwidth=24, stretch=False)
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
                stretch=column == "Status",
            )
        if isinstance(tree, ttk.Treeview) and not hasattr(tree, "_viking_column_hint"):
            from .running_hints import RunningTableHint
            tree._viking_column_hint = RunningTableHint(tree, columns)
        if isinstance(tree, ttk.Treeview) and not hasattr(tree, "_viking_override_cells"):
            from .override_cells import OverrideCells
            tree._viking_override_cells = OverrideCells(tree)
        if isinstance(tree, ttk.Treeview) and not hasattr(tree, "_viking_table_cells"):
            from .table_cells import RunningTableCells
            tree._viking_table_cells = RunningTableCells(tree)
        if isinstance(tree, ttk.Treeview) and not hasattr(tree, "_viking_running_scaling"):
            def rescale(*_scaling):
                root = tree.winfo_toplevel()
                name = str(tree.cget("style")) or "Running.Treeview"
                tree.configure(style=name)
                style = ttk.Style(tree)
                style.configure(name, font=running_font(root), rowheight=running_row_height(root))
                scale = _running_scale(root)
                style.configure(name + ".Heading", font=running_font(root, heading=True),
                                padding=(round(8 * scale), round(5 * scale)))
                for key in columns:
                    width = DashboardTablesMixin._running_column_width(tree, key)
                    tree.column(key, width=width, minwidth=width)
                tree._viking_table_cells.schedule()
            tree._viking_running_scaling = rescale
            ScalingTracker.add_widget(rescale, tree)
            tree.bind("<Destroy>", lambda event: ScalingTracker.remove_widget(rescale, tree)
                      if event.widget is tree else None, add="+")
            rescale()

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
        # Keep stable value indexes for row actions; STT is displayed first.
        if len(values) == len(RUNNING_COLUMNS) - 1:
            number = str(previous["values"][-1]) if previous and len(previous["values"]) == len(RUNNING_COLUMNS) else ""
            values = (*values, number)
        if previous is None:
            tree.insert("", "end", iid=iid, tags=tags, values=values)
        elif tuple(map(str, previous["values"])) != tuple(map(str, values)) or tuple(previous["tags"]) != tags:
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
            finished = float(item.details.get("finished_at") or item.handed_off_at or item.claimed_at or item.created_at)
            show_result = (item.status in FINAL_STATUSES - {"FILLED"}
                           and datetime.fromtimestamp(finished, VN_TZ).date() == datetime.now(VN_TZ).date())
            if item.status.upper() not in FINAL_STATUSES or show_result or watch_visible(item, time.time()):
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
            close_items = [item for item in local_by_mode[mode] if item.action == "CLOSE" and item.status not in FINAL_STATUSES]
            consumed_close_ids: set[str] = set()
            position_ids: dict[str, int] = {}

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
                fee_value = row.get("buyFee", row.get("fee"))
                try:
                    fee_known = fee_value is not None and math.isfinite(float(fee_value)) and float(fee_value) >= 0
                except (ValueError, TypeError, OverflowError):
                    fee_known = False
                buy_fee = float(fee_value) if fee_known else 0.0
                buy_fee_text = f"PHÍ MUA {_compact_vnd(buy_fee)}"
                if mode == "PAPER":
                    estimated_exit_cost = market_value * (
                        config.PAPER_SELL_FEE_RATE + config.PAPER_SELL_TAX_RATE
                    )
                else:
                    buy_rate = self._cached_fee_rate(symbol, "BUY")
                    sell_rate = self._cached_fee_rate(symbol, "SELL")
                    if not fee_known and buy_rate is not None:
                        buy_fee = gross * buy_rate
                        buy_fee_text = f"PHÍ MUA ~{_compact_vnd(buy_fee)}"
                    elif not fee_known:
                        buy_fee_text = "PHÍ MUA CHỜ DNSE"
                    if cycle and cycle.fees_paid > 0:
                        buy_fee_text = f"PHÍ ĐÃ GHI {_compact_vnd(cycle.fees_paid)}"
                    estimated_exit_cost = market_value * sell_rate if sell_rate is not None else 0.0
                fallback_pnl = (
                    float(cycle.net_pnl) if cycle else -abs(buy_fee)
                ) + (market_price - avg_price) * quantity * 1000.0 - estimated_exit_cost
                pnl = float(metrics.get("current_net_pnl", fallback_pnl) or 0.0)
                basis = (cycle.buy_notional if cycle else 0) or _number(metrics.get("entry_notional_vnd")) or gross
                pnl_pct = pnl / basis * 100.0 if basis > 0 else 0.0
                history_verified = metrics.get("price_history_verified") is True
                history_untrusted = (metrics.get("price_history_verified") is False or (
                    "peak_profit_pct" in metrics and metrics.get("market_data_policy") != "G1_MATCH_V1"))
                extrema_text = "MAE — · MFE —\nCHỜ XÁC MINH"
                if history_verified:
                    mae_pct, mfe_pct = _number(metrics.get("mae_pct")), _number(metrics.get("mfe_pct"))
                    mae_price, mfe_price = avg_price * (1 + mae_pct / 100), avg_price * (1 + mfe_pct / 100)
                    extrema_text = (f"MAE {mae_pct:+.2f}% · {_display_price(mae_price)} · "
                                    f"{_trade_money((mae_price - avg_price) * quantity * 1000, signed=True)}\n"
                                    f"MFE {mfe_pct:+.2f}% · {_display_price(mfe_price)} · "
                                    f"{_trade_money((mfe_price - avg_price) * quantity * 1000, signed=True)}")
                pending = max(0, quantity - sellable)
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
                broker_position = row.get("id", row.get("positionId", row.get("loanPackageId", "")))
                position_key = f"POSITION:{mode}:{trade_id or symbol}:{broker_position}"
                occurrence = position_ids.get(position_key, 0)
                position_ids[position_key] = occurrence + 1
                iid = position_key if not occurrence else f"{position_key}:ROW{occurrence}"
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
                from ..rules.protection import policy_for_stage
                protect_mode = policy_for_stage(params, str(decision_details.get("protection_stage", "ARM")))
                if cycle and (not cycle.auto_sell_enabled or not cycle.bot_trade_enabled):
                    protect_mode = "ALERT"
                if (cycle and ((str(decision_details.get("protection_stage", "ARM")) == "ARM" and
                                params.get("normal_arm_enabled") is False) or
                               (str(decision_details.get("protection_stage", "ARM")) == "DYN" and
                                params.get("normal_dynamic_enabled") is False))):
                    protect_mode = "OFF"
                protect_price = decision_details.get("normal_trigger_price")
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
                paused = bool(cycle and (not cycle.auto_sell_enabled or not cycle.bot_trade_enabled))
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
                    f"SL {sl_pct:+g}% {_trade_money((sl_price - avg_price) * quantity * 1000)} @ {_display_price(sl_price)}"
                    if sl_enabled else "SL OFF"
                )
                target_lines = [sl_text,
                    (f"TP +{tp_pct:g}% +{_trade_money((tp_price - avg_price) * quantity * 1000)} @ {_display_price(tp_price)}"
                     if tp_price > 0 else "TP OFF")]
                activation_text = ""
                if normal_enabled:
                    from .protect_preview import dynamic_start_preview
                    activation = dynamic_start_preview(avg_price, params, decision_details)
                    arm_enabled = params.get("normal_arm_enabled", True)
                    arm_text = _display_price(avg_price * (1 + _number(params.get("normal_arm_pct", 7)) / 100)) if arm_enabled else "OFF"
                    start_price = activation["start"]
                    start_target = (_display_price(start_price) if start_price > 0 else "CHỜ ATR") if activation["start_enabled"] else "CÓ LÃI"
                    if activation["state"] == "OFF":
                        start_target = "OFF"
                    activation_text = f"ARM {arm_text} · START {start_target}"
                    stage = str(decision_details.get("protection_stage", "ARM"))
                    floor = _number(protect_price)
                    floor_text = (_display_price(floor) if floor > 0 else "CHỜ DỮ LIỆU") if not history_untrusted else "CHỜ XÁC MINH"
                    target_lines.append(f"PROTECT {stage} {floor_text} · {protect_mode}")
                from ..trading.position_controls import position_override_items, position_bot_cell
                override_items = position_override_items(self.settings, cycle)
                if override_items:
                    tree._viking_override_rows[iid] = override_items
                from .position_status import position_status
                status_text, status_hint = position_status(
                    symbol=symbol, mode=mode, quantity=quantity, sellable=sellable,
                    settle=row.get("settleDate"), price=market_price, entry=avg_price,
                    sl_enabled=sl_enabled, sl_price=sl_price, tp_price=tp_price, tp_pct=tp_pct,
                    params=params, decision=decision, normal_enabled=normal_enabled,
                    indicator_enabled=indicator_enabled, paused=paused, protect_mode=protect_mode,
                    normal_state=normal_state, history_untrusted=history_untrusted, pending_close=pending_close, metrics=metrics,
                )
                self._update_running_row(
                    tree, existing, row_order, iid=iid, tags=(row_tag,),
                    values=(
                        f"#{(trade_id or str(row.get('positionId', index)))[:12]}",
                        self._row_time(opened),
                        _order_caption(mode, source, "BUY", symbol, quantity, _display_price(avg_price), self._row_time(opened))
                        + (f"\n{activation_text}" if activation_text else ""),
                        "\n".join(target_lines),
                        f"{buy_fee_text.replace('PHÍ MUA', 'MUA').replace('PHÍ ĐÃ GHI', 'ĐÃ GHI')}\nBÁN "
                        + (f"~{_trade_money(estimated_exit_cost)}" if mode == "PAPER" or sell_rate is not None else "CHỜ DNSE"),
                        rr_text,
                        f"PnL {pnl_icon}{_trade_money(pnl, signed=True)} ({pnl_pct:+.2f}%)\n{extrema_text}",
                        status_text,
                        position_bot_cell(self.settings, cycle),
                        "X" if override_items else "—",
                    ),
                )
                evidence = metrics.get("price_history_evidence") or {}
                since = evidence.get("started_at") if evidence.get("coverage") == "FROM_RESTART" else None
                period = f"Đo lại từ {self._row_time(since)}" if since else "Theo dõi từ khi mua"
                if evidence.get("coverage") == "HISTORICAL_SESSIONS":
                    period = (f"Đã tải {evidence.get('loaded_sessions')}/{evidence.get('requested_sessions')} phiên · "
                              f"từ {self._row_time(evidence.get('started_at'))} đến {self._row_time(evidence.get('ended_at'))}; "
                              "tiếp tục theo dõi giá mới")
                    if evidence.get("entry_minute_omitted"):
                        period += " (bỏ nến phút chứa lúc mua)"
                    if evidence.get("pending_sessions"):
                        period += f" · Chờ nến {', '.join(evidence['pending_sessions'])}; tự tải bổ sung"
                tree._viking_row_hints[iid] = {
                    "Ticket": f"Vị thế #{trade_id}\nChuột phải → Mở bảng: sửa TP/SL, ARM/DYNAMIC/E hoặc đo lại MAE/MFE.",
                    "Order": f"{mode} · {source} · {symbol}\nMở {self._row_time(opened)}\nGiá vốn {_display_price(avg_price)} · Còn {quantity:,} CP"
                             + (f"\n{activation_text}" if activation_text else "")
                             + (f"\nĐã mua {cycle.entry_quantity:,} CP · Bán khớp {cycle.sold_quantity:,} CP" if cycle else ""),
                    "Targets": f"Còn {quantity:,} CP · Lãi/lỗ tại SL/TP trước phí và trượt giá.\n"
                               f"SL {_display_price(sl_price)} ({sl_pct:+g}%) · TP {_display_price(tp_price)} ({tp_pct:+g}%)\n"
                               "ARM/START bật theo dõi (xem cột Lệnh); PROTECT là mốc giá lùi để xét bán.\n"
                               + (f"{activation_text}\n" if activation_text else "") + "\n".join(target_lines[2:]),
                    "CostInfo": "Phí đã ghi theo sổ giao dịch; ~ là ước tính, CHỜ DNSE là chưa có dữ liệu.\n"
                                "Phí bán dự kiến không đồng nghĩa đã bán, chưa tự trừ thêm vào PnL REAL.",
                    "Bot": "Quyền tự động của vị thế. BOT chung chỉ bật/tắt BUY mới; SL/TP/PROTECT/E theo cấu hình vị thế.",
                    "PnL_MAE_MFE": f"PnL toàn deal: đã chốt + phần còn giữ, theo phí đã ghi nhận.\nPnL {_compact_vnd(pnl)} / vốn mua gốc {_compact_vnd(basis)} = {pnl_pct:+.2f}%\n"
                                    f"MAE/MFE theo giá vốn {_display_price(avg_price)}. {period}.\n"
                                    f"Tiền MAE/MFE = chênh lệch giá × {quantity:,} CP còn giữ, trước phí; không cộng phần đã chốt.\n"
                                    + (f"Đáy {_display_price(mae_price)} · Đỉnh {_display_price(mfe_price)}\n" if history_verified else "")
                                    + (extrema_text if history_verified else
                                       "Lịch sử cũ chưa xác minh; không dùng cho PROTECT. Mở bảng vị thế để đo lại từ giá G1."),
                    "Status": status_hint,
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
                                if not watch_visible(item, time.time()) or submitted_day(item) == datetime.now(VN_TZ).date().isoformat()
                                for value in [item.broker_order_id, *item.broker_order_ids] if value}
            for item in reversed(local_by_mode[mode]):
                if item.id in consumed_close_ids:
                    continue
                passive_watch = watch_visible(item, time.time())
                if not passive_watch or submitted_day(item) == datetime.now(VN_TZ).date().isoformat():
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
                elif status_upper == "WAITING_REMAINDER":
                    remaining_label += " MUA NỐT PHIÊN KẾ TIẾP"
                elif status_upper in FINAL_STATUSES:
                    remaining_label += " · YÊU CẦU ĐÃ KẾT THÚC"
                is_working = status_upper in {"WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"}
                cancellable = status_upper in LOCALLY_CONTROLLABLE_STATUSES or (
                    mode == "REAL" and bool(item.broker_order_id) and is_working
                )
                if status_upper == "WAITING_REMAINDER":
                    cancellable = True
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
                    "WAITING_REMAINDER": "MUA NỐT PHẦN DƯ · CHỜ PHIÊN KẾ TIẾP",
                    "CANCELLED": "ĐÃ HỦY", "EXPIRED": "HẾT HIỆU LỰC",
                    "REJECTED": "BỊ TỪ CHỐI", "FAILED": "THẤT BẠI",
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
                if item.side == "BUY" and item.source == "BOT" and status_upper != "WAITING_REMAINDER":
                    status_label = ("MUA NỐT PHẦN DƯ" if item.details.get("buy_remainder") else "MUA MỚI") + " · " + status_label
                watch = item.details.get("buy_watch") or {}
                if passive_watch:
                    status_label = "LƯU THAM KHẢO · " + status_label
                    remaining_label = (f"ĐÃ BẬT MUA TAY #{watch['armed_order_id'][:8]}" if watch.get("armed_order_id")
                                       else ("CHƯA BẬT MUA" if watch.get("session_date") else "CHỜ LỊCH · CHƯA BẬT MUA")
                                       + " · KHÔNG GIỮ VỐN/LƯỢT MUA")
                elif item.details.get("operator_armed_buy"):
                    status_label = "MUA BẬT TAY · " + status_label
                estimated_fee = (
                    self._preview_buy_fee(gross, item.symbol, mode)
                    if item.side == "BUY" else None
                )
                fee_text = _compact_vnd(estimated_fee) if estimated_fee is not None else "--"
                cost_text = f"FEE {fee_text}" if gross > 0 else f"HẾT HẠN {self._row_time(item.expires_at)}"
                if passive_watch:
                    cost_text = ("LƯU ĐẾN " + datetime.fromtimestamp(float(watch["visible_until"]), VN_TZ).strftime("%d/%m %H:%M")
                                 if watch.get("visible_until") else "CHỜ LỊCH GIAO DỊCH")
                risk_text = item_sl_label
                reward_text = item_tp_label
                self._update_running_row(
                    tree, existing, row_order, iid=iid, tags=(tag,),
                    values=(
                        f"[CACHE] {item.id[:8]}",
                        self._row_time(item.created_at),
                        _order_caption(mode, display_source, item.side, item.symbol, item.quantity, price_text, self._row_time(item.created_at)),
                        target_text,
                        cost_text,
                        f"R {risk_text} · E {reward_text}" if item.action == "OPEN" else "--",
                        f"-- · -- · -- · Khớp {item.filled_quantity}/{item.quantity}",
                        f"{status_label} · Khớp {item.filled_quantity}/{item.quantity}\n"
                        f"{remaining_label} · {em_text}\n{item.result or item.reason or 'ĐANG CHỜ'}",
                        "Chưa bật mua" if passive_watch and not watch.get("armed_order_id") else
                        "Đã bật tay" if passive_watch else
                        position_bot_cell(self.settings, cycle) if cycle else "Bật" if item.source in {"BOT", "EM"} else "—",
                        "X" if override_items else "—",
                    ),
                )
                tree._viking_row_hints[iid] = {
                    "Order": f"{mode} · {display_source} · {item.side} {item.symbol}\n"
                             f"Tạo {self._row_time(item.created_at)} · KL {item.quantity:,} @ {price_text}",
                    "Override": "\n".join(override_items) if override_items else "Không có cấu hình riêng.",
                }
                if override_items:
                    tree._viking_override_rows[iid] = override_items
                self._running_row_actions[mode][iid] = {
                    "kind": "local", "mode": mode, "symbol": item.symbol,
                    "local_id": item.id, "broker_order_id": "" if status_upper == "WAITING_REMAINDER" else item.broker_order_id,
                    "order_type": item.order_type, "quantity": item.quantity,
                    "price": item.limit_price, "editable": editable, "cancellable": cancellable,
                    "pausable": status_upper in CLAIMABLE_STATUSES,
                    "resumable": status_upper == "PAUSED",
                    "status": status_upper, "source": item.source, "side": item.side,
                    "result": item.result, "reason": item.reason,
                    "expires_at": item.expires_at,
                    "buy_watch": passive_watch,
                    "armable": watch_armable(item, time.time()) and not any(
                        other.symbol == item.symbol and other.execution_mode == mode and other.status not in FINAL_STATUSES
                        for other in local_items) and not any(
                        candidate.symbol == item.symbol and candidate.execution_mode == mode and candidate.status == "OPEN"
                        and candidate.open_quantity > 0 for candidate in cycles),
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
                        _order_caption(mode, "EXTERNAL_DNSE", side, symbol, quantity, _display_price(price) if price else kind,
                                       self._row_time(row.get('createdAt', row.get('createdDate', '')))),
                        "SL/TP SAU KHI KHỚP" if side == "BUY" else "ĐÓNG VỊ THẾ",
                        f"FEE {_compact_vnd(fee)}",
                        "--",
                        f"-- · -- · -- · Khớp {filled}/{quantity}",
                        f"[DNSE][{pending_ack or ('PARTIAL' if partial else 'WORKING')}]\n"
                        f"Khớp {filled}/{quantity} · CÒN {remaining}"
                        + (" CHỜ KHỚP" if not pending_ack else ""),
                        "",
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
                tree._viking_row_hints[iid] = {
                    "Order": f"{mode} · EXTERNAL_DNSE · {side} {symbol}\n"
                             f"Tạo {self._row_time(row.get('createdAt', row.get('createdDate', '')))}\n"
                             f"Mã lệnh {order_id} · KL {quantity:,}",
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
            for number, iid in enumerate(root_order, 1):
                label = str(number)
                if tree.set(iid, "STT") != label:
                    tree.set(iid, "STT", label)
                for child_number, child in enumerate(child_order.get(iid, []), 1):
                    label = f"{number}.{child_number}"
                    if tree.set(child, "STT") != label:
                        tree.set(child, "STT", label)
            restored = tuple(iid for iid in selected_before if iid in current_ids)
            if tuple(tree.selection()) != restored:
                tree.selection_set(restored)
            if yview_before and previous_order != self._running_rows(tree):
                tree.yview_moveto(yview_before[0])
            cells = getattr(tree, "_viking_override_cells", None)
            if cells:
                cells.schedule()
            decorations = getattr(tree, "_viking_table_cells", None)
            if decorations:
                decorations.schedule()
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
        self._position_read_executor.shutdown(wait=False, cancel_futures=True)
        self.real.close()
        if getattr(self, "_account_lease", None):
            self._account_lease.close()
        self.destroy()
