from __future__ import annotations

from datetime import datetime, timedelta
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from tkinter import messagebox, ttk
from typing import Any

import customtkinter as ctk

from ..backtest.window import BacktestPopup
from ..backtest.data import HistoricalDataStore

from .. import config
from ..config import save_settings
from ..connections.telegram import SignalTelegramService, TelegramClient
from ..connections.window import ConnectionPopup
from ..exit_modes import EXIT_MODE_LABELS
from ..models import OrderIntent, RuntimeConfig, StrategyDecision, TradeCycle
from ..rules.window import RuleSettingsPopup
from ..services.signal_coordinator import (
    BuyAttempt,
    BuySlotAllocator,
    coordinate_buy_decisions,
    decision_signal_time,
    is_terminal_buy_block,
)
from ..storage import CSVOrderJournal
from ..trading.market import VN_TZ, market_phase, market_session_clock, normalize_exchange
from ..trading.portfolio import sell_quantity_for_fraction
from .view import (
    COL_GRAY, COL_GREEN, COL_MUTED, COL_PREVIEW_TEXT, COL_RED, COL_SURFACE_2,
    COL_TEXT, COL_TITLE, COL_WARN, _cash, _compact_vnd, _display_price, _equity, _number,
    _price_unit,
)
from .info import InfoPopup
from .windows import (
    FONT_KEY,
    FONT_MONO_VALUE,
    FONT_VALUE,
    DataTablePopup,
    HistoryPopup,
    minimize_popup,
)

# One list so a new tactic never has to be remembered in four separate places.
EM_TACTICS: tuple[tuple[str, str], ...] = (
    ("TP", EXIT_MODE_LABELS["TP"]),
    ("NORMAL", EXIT_MODE_LABELS["NORMAL"]),
    ("IND_EXIT", EXIT_MODE_LABELS["IND_EXIT"]),
)
EM_LABELS: dict[str, str] = dict(EM_TACTICS)
SIGNAL_HISTORY_ROW_LIMIT = 250
HISTORY_EVENT_LIMIT = 300
HISTORY_DAY_LIMIT = 7


class DashboardActionsMixin:
    def _post_ui(self, callback: Any) -> None:
        """Hand work back to Tk without calling Tcl from a worker thread."""
        if self.running:
            self._ui_callbacks.put(callback)

    def _drain_ui_callbacks(self) -> None:
        if not self.running:
            return
        while True:
            try:
                callback = self._ui_callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                self.logger.exception("UI callback failed")
        self.after(50, self._drain_ui_callbacks)

    @staticmethod
    def _focus_widget(widget: Any) -> str:
        widget.focus_set()
        return "break"

    def _order_type_changed(self, value: str) -> None:
        if value == "LO":
            self._populate_default_lo()
        self._update_order_preview()

    def _populate_default_lo(self) -> None:
        if not hasattr(self, "price") or str(self.price.get() or "").strip():
            return
        current = float(getattr(self, "_current_tick_price", 0.0) or 0.0)
        if current <= 0:
            return
        self.price.insert(0, _display_price(current))

    def _activate_lo_input(self, _event: Any = None) -> None:
        if self.order_type.get() != "LO":
            self.order_type.set("LO")
        self._populate_default_lo()
        self._update_order_preview()

    def _default_tp_text(self) -> str:
        params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        return f"{float(params.get('normal_arm_pct', 7.0) or 7.0):g}%"

    @staticmethod
    def _auction_preview_price(
        order_type: str,
        live_price: float,
        tick: dict[str, Any] | None,
        market_status: str,
    ) -> tuple[float, str]:
        kind = str(order_type or "").upper()
        if kind not in {"ATO", "ATC"}:
            return float(live_price or 0.0), "LIVE"
        tick = tick if isinstance(tick, dict) else {}
        phase = str(market_status or "").upper()
        expected = _price_unit(tick.get("expected_price", tick.get("expectedPrice", 0)))
        if phase == kind and expected > 0:
            return expected, f"DỰ KHỚP {kind}"
        if kind == "ATO":
            recent = _price_unit(tick.get("open", tick.get("openPrice", 0)))
        elif phase in {"ATO", "OPEN", "CONTINUOUS", "LUNCH"}:
            recent = _price_unit(tick.get("reference", tick.get("referencePrice", 0)))
        else:
            recent = float(live_price or 0.0)
        return (recent or float(live_price or 0.0)), f"{kind} GẦN NHẤT"

    @staticmethod
    def _buy_button_presentation(
        invalid_reason: str,
        due: bool,
        token_ready: bool,
    ) -> tuple[str, str, str, str]:
        if invalid_reason == "KHÔNG ĐỦ VỐN MUA 1 LÔ":
            return "KHÔNG ĐỦ VỐN", "#7F1D1D", "#991B1B", "#EF4444"
        if invalid_reason:
            return "KIỂM TRA", "#7F1D1D", "#991B1B", "#EF4444"
        # The primary action describes the market session. Missing OTP is shown
        # separately and the existing execution queue keeps the intent local.
        if due:
            return "ĐẶT", "#16A34A", "#15803D", "#22C55E"
        return "CACHE", "#B7791F", "#D97706", "#F59E0B"

    def _preview_buy_fee(self, gross: float, symbol: str, mode: str) -> float | None:
        gross = max(0.0, float(gross or 0.0))
        if gross <= 0:
            return 0.0
        if str(mode or "PAPER").upper() == "PAPER":
            return gross * self.settings.buy_fee_pct / 100.0
        rate = self._cached_fee_rate(symbol, "BUY")
        return gross * rate if rate is not None else None

    def _cached_fee_rate(self, symbol: str, side: str) -> float | None:
        """Return cached broker fee and refresh it without blocking Tk."""
        key = (str(symbol or "").strip().upper(), str(side or "BUY").upper())
        if not key[0] or not self.real.configured():
            return None
        if key in self._fee_rates:
            return self._fee_rates[key]
        if time.monotonic() < self._fee_rate_retry_after.get(key, 0.0):
            return None
        if key in self._fee_rate_pending or not self.running:
            return None
        self._fee_rate_pending.add(key)
        future = self._io_executor.submit(
            self.real.get_stock_fee_rate,
            key[0],
            side=key[1],
        )

        def completed(result: Any, cache_key: tuple[str, str] = key) -> None:
            try:
                value = result.result()
            except Exception as exc:
                value = None
                self.logger.warning("Fee refresh %s/%s failed: %s", *cache_key, exc)

            def apply() -> None:
                self._fee_rate_pending.discard(cache_key)
                if value is None:
                    self._fee_rate_retry_after[cache_key] = time.monotonic() + 30.0
                else:
                    self._fee_rates[cache_key] = float(value)
                    self._fee_rate_retry_after.pop(cache_key, None)
                if self.running:
                    self._update_order_preview()

            self._post_ui(apply)

        future.add_done_callback(completed)
        return None

    def _default_sl_text(self) -> str:
        params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        streak = self.trade_state.loss_streak(self.symbol.get(), self.mode.get())
        value = (
            float(params.get("reentry_sl_pct", -2.1) or -2.1)
            if streak > 0
            else float(params.get("initial_sl_pct", -3.0) or -3.0)
        )
        return f"{value:g}%"

    def _sync_default_sl(self, *, force: bool = False, refresh: bool = True) -> None:
        if not hasattr(self, "sl") or (self._sl_manual_override and not force):
            return
        value = self._default_sl_text()
        self.sl.delete(0, "end")
        self.sl.insert(0, value)
        self.sl.configure(text_color=COL_MUTED)
        self._sl_manual_override = False
        if refresh:
            self._update_order_preview()

    def _symbol_changed(self, _value: str) -> None:
        self._current_tick_price = 0.0
        if hasattr(self, "price"):
            self.price.delete(0, "end")
        self._sync_default_sl(force=True)

    def _sl_edited(self, _event: Any = None) -> None:
        raw = str(self.sl.get() or "").strip()
        self._sl_manual_override = bool(raw and raw != self._default_sl_text())
        self._update_order_preview()

    def _toggle_em_frontend(self, name: str) -> None:
        """Visual-only switches until Exit Manager execution is connected."""
        if name not in self._em_states:
            return
        enabled = not self._em_states[name]
        self._em_states[name] = enabled
        title = self._em_specs[name]
        self._em_buttons[name].configure(
            text=f"{title} · {'ON' if enabled else 'OFF'}",
            fg_color="#168A47" if enabled else "#282D34",
            hover_color="#1EA45A" if enabled else "#363C45",
            text_color=COL_TEXT if enabled else COL_MUTED,
        )
        self._update_order_preview()

    @staticmethod
    def _projected_pnl(
        raw: str,
        entry_price: float,
        quantity: int,
        preview_capital: float = 0.0,
    ) -> float | None:
        text = str(raw or "").strip().replace(",", "")
        if not text or entry_price <= 0:
            return None
        capital = (
            entry_price * quantity * 1000.0
            if quantity > 0 else max(0.0, preview_capital)
        )
        if capital <= 0:
            return None
        try:
            if text.endswith("%"):
                return capital * float(text[:-1]) / 100.0
            target = _price_unit(float(text))
            return capital * (target - entry_price) / entry_price
        except ValueError:
            return None

    @staticmethod
    def _preview_trigger(label: str, raw: str) -> str:
        text = str(raw or "").strip().replace(",", "")
        if not text:
            return label
        if text.endswith("%"):
            try:
                value = float(text[:-1])
                sign = "+" if value > 0 else ""
                return f"{label} · {sign}{value:g}%"
            except ValueError:
                return label
        try:
            return f"{label} · {_display_price(_price_unit(float(text)))}"
        except ValueError:
            return label

    def _refresh_main_quote_display(self) -> None:
        """Paint the main quote from the currently selected order type.

        The left quote panel is the source of truth for manual order entry.  The
        full PREVIEW panel mirrors this selection; it must not be the only place
        where an ATO/ATC/LO price is visible.
        """
        if not hasattr(self, "lbl_price"):
            return

        tick = getattr(self, "_current_tick", {})
        tick = tick if isinstance(tick, dict) else {}
        market_status = str(getattr(self, "_current_market_status", "") or "").upper()
        live_price = float(getattr(self, "_current_tick_price", 0.0) or 0.0)
        reference = _price_unit(tick.get("reference", tick.get("referencePrice", 0)))
        order_type = str(self.order_type.get() or "MARKET").upper()

        display_price = live_price
        display_state = "PHIÊN · CLOSED"
        price_is_frozen = False

        tick_ts = float(tick.get("timestamp", 0.0) or 0.0)
        tick_age = max(0.0, time.time() - tick_ts) if tick_ts else None
        active_market = market_status in {"ATO", "CONTINUOUS", "ATC", "OPEN"}
        live_is_frozen = bool(tick) and (
            bool(tick.get("frozen"))
            or bool(tick.get("price_frozen"))
            or not active_market
            or bool(tick_age is not None and tick_age > 10.0)
        )

        if order_type == "LO":
            try:
                display_price = _price_unit(float(self.price.get().replace(",", "")))
            except (TypeError, ValueError):
                display_price = 0.0
        elif order_type in {"ATO", "ATC"}:
            display_price, display_state = self._auction_preview_price(
                order_type, live_price, tick, market_status,
            )
            price_is_frozen = not display_state.startswith("DỰ KHỚP")
        elif live_is_frozen:
            price_is_frozen = True

        actual_phase = market_status
        known_phases = {
            "ATO", "ATC", "OPEN", "CONTINUOUS", "CLOSED", "LUNCH",
            "HOLIDAY", "WEEKEND",
        }
        if actual_phase not in known_phases:
            actual_phase = self._current_market_phase()
        if actual_phase == "ATO":
            display_state, session_color = "PHIÊN · ATO", COL_GREEN
        elif actual_phase == "ATC":
            display_state, session_color = "PHIÊN · ATC", COL_GREEN
        elif actual_phase in {"OPEN", "CONTINUOUS"}:
            display_state, session_color = "PHIÊN · LIVE", COL_GREEN
        else:
            display_state, session_color = "PHIÊN · CLOSED", COL_RED

        change = display_price - reference if display_price > 0 and reference > 0 else 0.0
        change_pct = change / reference * 100.0 if reference > 0 else 0.0
        change_color = COL_GREEN if change > 0 else COL_RED if change < 0 else COL_PREVIEW_TEXT
        if order_type == "LO":
            price_color = COL_TEXT if display_price > 0 else COL_RED
        elif price_is_frozen:
            price_color = COL_WARN
        else:
            price_color = change_color if display_price > 0 else COL_PREVIEW_TEXT

        self.lbl_price.configure(text=_display_price(display_price), text_color=price_color)
        self.lbl_change.configure(
            text=(
                f"REF {_display_price(reference)} · "
                f"{'↑' if change > 0 else '↓' if change < 0 else '·'} "
                f"{_display_price(abs(change)) if change else '0'} · {abs(change_pct):.2f}%"
                if display_price > 0 and reference > 0 else "--"
            ),
            text_color=change_color,
        )
        self.lbl_market.configure(text=display_state, text_color=session_color)

    def _update_order_preview(self) -> None:
        if not hasattr(self, "lbl_order_value"):
            return
        self._refresh_main_quote_display()
        symbol = self.symbol.get().strip().upper() or "---"
        self.lbl_quote_symbol.configure(text=symbol)
        order_type = self.order_type.get().upper()
        live_price = float(getattr(self, "_current_tick_price", 0.0) or 0.0)
        entry_price = live_price
        if order_type == "LO":
            try:
                entry_price = _price_unit(float(self.price.get().replace(",", "")))
            except ValueError:
                entry_price = 0.0
        elif order_type in {"ATO", "ATC"}:
            entry_price, _detail = self._auction_preview_price(
                order_type,
                live_price,
                getattr(self, "_current_tick", {}),
                getattr(self, "_current_market_status", ""),
            )
        raw_quantity = self.quantity.get().strip().replace(",", "")
        if raw_quantity:
            try:
                quantity = max(0, int(raw_quantity))
            except ValueError:
                quantity = 0
        else:
            quantity, budget, _forced_minimum = self._suggested_order_quantity(entry_price)
        gross = entry_price * quantity * 1000.0
        preview_capital = gross
        self.lbl_order_value.configure(
            text=f"{preview_capital:,.0f} ₫" if preview_capital > 0 else "--"
        )
        fee = self._preview_buy_fee(preview_capital, symbol, self.mode.get())
        fee_text = _compact_vnd(fee) if fee is not None else "--"
        self.lbl_fee_preview.configure(
            text=fee_text,
            text_color=COL_WARN,
        )
        tp_raw = self.tp.get()
        sl_raw = self.sl.get()
        self.lbl_tp_title.configure(text=self._preview_trigger("TP", tp_raw))
        self.lbl_sl_title.configure(text=self._preview_trigger("SL", sl_raw))
        tp_pnl = self._projected_pnl(tp_raw, entry_price, quantity, preview_capital)
        sl_pnl = self._projected_pnl(sl_raw, entry_price, quantity, preview_capital)
        tp_text = f"+{_compact_vnd(abs(tp_pnl))}" if tp_pnl is not None and tp_pnl >= 0 else "0"
        sl_text = f"-{_compact_vnd(abs(sl_pnl))}" if sl_pnl is not None and sl_pnl <= 0 else "0"
        self.lbl_tp_preview.configure(
            text=tp_text,
            text_color=COL_GREEN if tp_pnl is not None and tp_pnl >= 0 else COL_PREVIEW_TEXT,
        )
        self.lbl_sl_preview.configure(
            text=sl_text,
            text_color=COL_RED if sl_pnl is not None and sl_pnl <= 0 else COL_PREVIEW_TEXT,
        )
        self._refresh_full_order_preview()

    def _show_data_popup(
        self,
        key: str,
        title: str,
        columns: tuple[tuple[str, str, int, str], ...],
        provider: Any,
        *,
        summary_provider: Any = None,
        button: Any = None,
    ) -> None:
        existing = self._data_popups.get(key)
        if existing and existing.top.winfo_exists():
            existing.refresh()
            existing.show()
            return
        popup = DataTablePopup(
            self,
            title=title,
            columns=columns,
            rows_provider=provider,
            initial_mode=self.mode.get(),
            summary_provider=summary_provider,
            on_visibility_changed=(
                lambda visible: button.configure(
                    fg_color=COL_GREEN if visible else COL_GRAY,
                    hover_color="#16A34A" if visible else "#4B515B",
                )
            ) if button is not None else None,
        )
        self._data_popups[key] = popup

    def _open_portfolio_popup(self) -> None:
        columns = (
            ("symbol", "MÃ", 90, "center"),
            ("quantity", "SỞ HỮU", 110, "center"),
            ("sellable", "BÁN ĐƯỢC", 120, "center"),
            ("pending", "CHỜ T+", 105, "center"),
            ("odd", "LÔ LẺ", 90, "center"),
            ("cost", "GIÁ VỐN", 120, "center"),
            ("market", "GIÁ TT", 120, "center"),
            ("value", "GIÁ TRỊ", 150, "center"),
            ("pnl", "PNL", 150, "center"),
            ("pnl_pct", "PNL %", 100, "center"),
            ("targets", "SL / TP", 165, "center"),
            ("em", "E/M", 230, "center"),
            ("settle", "NGÀY VỀ", 110, "center"),
            ("trade", "TRADE ID", 145, "center"),
            ("note", "TRẠNG THÁI", 230, "w"),
        )
        self._show_data_popup(
            "portfolio", "Danh mục CKCS", columns, self._portfolio_popup_rows,
            summary_provider=self._portfolio_popup_summary,
            button=self.portfolio_button,
        )

    def _open_history_popup(self) -> None:
        popup = self._history_popup
        if popup and popup.top.winfo_exists():
            popup.refresh()
            popup.show()
        else:
            self._history_popup = HistoryPopup(
                self, self._history_popup_groups, initial_mode=self.mode.get(),
                signals_provider=self._signal_log_rows,
                on_visibility_changed=lambda visible: self.history_button.configure(
                    fg_color=COL_GREEN if visible else COL_GRAY,
                    hover_color="#16A34A" if visible else "#4B515B",
                ),
            )
        self._refresh_real_history_on_demand()

    def _open_info_popup(self) -> None:
        popup = self._info_popup
        if popup and popup.top.winfo_exists():
            popup.show()
            return
        self._info_popup = InfoPopup(
            self, lambda: self.settings, HistoricalDataStore(self.real),
            on_visibility_changed=lambda visible: self.info_button.configure(
                fg_color=COL_GREEN if visible else COL_GRAY,
                hover_color="#16A34A" if visible else "#4B515B",
            ),
        )

    def _minimize_popups_from_main_click(self, event: Any) -> None:
        """A main-window click minimizes viewers; it never destroys their state."""
        widget = event.widget
        openers = tuple(
            getattr(self, name, None) for name in (
                "rule_button", "connection_button", "backtest_button", "info_button",
                "history_button", "portfolio_button", "running_legend_button",
            )
        )
        cursor = widget
        while cursor is not None:
            if any(cursor is opener for opener in openers):
                return
            cursor = getattr(cursor, "master", None)
        for popup in (
            getattr(self, "_rule_settings_popup", None),
            getattr(self, "_advanced_popup", None),
            getattr(self, "_backtest_popup", None),
            getattr(self, "_info_popup", None),
            getattr(self, "_history_popup", None),
            *getattr(self, "_data_popups", {}).values(),
        ):
            minimize_popup(popup)

    def _open_backtest_popup(self) -> None:
        popup = self._backtest_popup
        if popup and popup.top.winfo_exists():
            popup.show()
            return
        self._backtest_popup = BacktestPopup(
            self,
            self.settings,
            self.real,
            on_visibility_changed=lambda visible: self.backtest_button.configure(
                fg_color=COL_GREEN if visible else COL_GRAY,
                hover_color="#16A34A" if visible else "#4B515B",
            ),
        )

    def _refresh_real_history_on_demand(self) -> None:
        """Load DNSE history only while the history viewer is in use."""
        if self._history_busy or not self.running or not self.real.configured():
            return
        self._history_busy = True
        today = datetime.now().date()
        future = self._io_executor.submit(
            self.real.get_order_history,
            (today - timedelta(days=30)).isoformat(),
            today.isoformat(),
        )

        def completed(result: Any) -> None:
            try:
                rows = list(result.result() or [])
                error = ""
            except Exception as exc:
                rows = []
                error = str(exc)

            def apply() -> None:
                self._history_busy = False
                if not self.running:
                    return
                if error:
                    self.logger.warning("History refresh failed: %s", error)
                else:
                    self.histories["REAL"] = rows
                popup = self._history_popup
                if popup and popup.top.winfo_exists():
                    popup.refresh()

            self._post_ui(apply)

        future.add_done_callback(completed)

    @staticmethod
    def _row_time(value: Any) -> str:
        try:
            number = float(value)
            if number > 0:
                return datetime.fromtimestamp(number).strftime("%d/%m/%Y %H:%M:%S")
        except (TypeError, ValueError, OSError):
            pass
        return str(value or "--")[:19].replace("T", " ")

    def _portfolio_popup_rows(self, mode: str) -> list[tuple[str, tuple[Any, ...], tuple[str, ...]]]:
        rows: list[tuple[str, tuple[Any, ...], tuple[str, ...]]] = []
        params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        for index, row in enumerate(self.snapshots.get(mode, ({}, [], []))[1]):
            quantity = int(_number(row.get("openQuantity", row.get("quantity", 0))))
            if quantity <= 0:
                continue
            sellable = int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0))))
            pending = max(0, quantity - sellable)
            cost = _price_unit(row.get("costPrice", row.get("averagePrice", 0)))
            market = _price_unit(row.get("marketPrice", row.get("price", cost)))
            value = market * quantity * 1000.0
            pnl = (market - cost) * quantity * 1000.0
            pnl_pct = ((market - cost) / cost * 100.0) if cost > 0 else 0.0
            symbol = str(row.get("symbol", row.get("instrumentId", "?")) or "?").upper()
            trade_id = str(row.get("tradeId", row.get("positionId", "")) or "")
            cycle = self.trade_state.get(trade_id) if trade_id else None
            if not cycle:
                cycle = self.trade_state.active_for(symbol, mode)
            if cycle:
                trade_id = cycle.id
            source = str(row.get("source", "DNSE" if mode == "REAL" else "PAPER") or "")
            if cycle and cycle.sl_mode == "PRICE" and cycle.sl_value > 0:
                sl_text = _display_price(cycle.sl_value)
            elif cycle and cycle.sl_mode == "PERCENT" and cycle.sl_value:
                sl_text = f"{-abs(cycle.sl_value):g}%"
            elif cycle:
                sl_text = f"{float(params.get('reentry_sl_pct', -2.1) if cycle.is_reentry else params.get('initial_sl_pct', -3.0) or -3.0):g}%"
            else:
                sl_text = "--"
            if cycle and cycle.tp_mode == "PRICE" and cycle.tp_value > 0:
                tp_text = _display_price(cycle.tp_value)
            elif cycle and cycle.tp_mode == "PERCENT" and cycle.tp_value > 0:
                tp_text = f"+{cycle.tp_value:g}%"
            elif cycle and str(cycle.source).upper() == "MANUAL":
                tp_text = "+7%"
            else:
                tp_text = "--"
            if cycle:
                enabled = set(cycle.em_modes)
                em_text = " · ".join(
                    f"{short} {'ON' if key in enabled else 'OFF'}"
                    for key, short in (("NORMAL", "PROTECT"), ("IND_EXIT", "E"))
                )
            else:
                em_text = "CHƯA QUẢN LÝ"
            settle = str(row.get("settleDate", "") or "")[:10]
            settle_short = f"{settle[8:10]}/{settle[5:7]}" if len(settle) == 10 else settle
            note = (
                f"T+ {pending} CP · {settle_short or '--'}"
                if pending else f"SẴN SÀNG · {source}"
            )
            tags = ("pending",) if pending else ("profit",) if pnl > 0 else ("loss",) if pnl < 0 else ()
            rows.append(
                (
                    f"POS:{mode}:{trade_id or index}",
                    (
                        symbol, quantity, sellable,
                        pending, quantity % 100, _display_price(cost), _display_price(market),
                        f"{value:,.0f} ₫", f"{pnl:+,.0f} ₫", f"{pnl_pct:+.2f}%",
                        f"{sl_text} / {tp_text}", em_text, settle_short or "--",
                        trade_id[:14] or "--", note,
                    ),
                    tags,
                )
            )
        return rows

    def _portfolio_popup_summary(self, mode: str) -> list[tuple[str, str, str]]:
        positions = self.snapshots.get(mode, ({}, [], []))[1]
        count = total_quantity = sellable_total = pending_total = odd_total = 0
        total_value = total_cost = 0.0
        for row in positions:
            quantity = int(_number(row.get("openQuantity", row.get("quantity", 0))))
            if quantity <= 0:
                continue
            sellable = int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0))))
            cost = _price_unit(row.get("costPrice", row.get("averagePrice", 0)))
            market = _price_unit(row.get("marketPrice", row.get("price", cost)))
            count += 1
            total_quantity += quantity
            sellable_total += sellable
            pending_total += max(0, quantity - sellable)
            odd_total += quantity % 100
            total_cost += cost * quantity * 1000.0
            total_value += market * quantity * 1000.0
        pnl = total_value - total_cost
        pnl_pct = pnl / total_cost * 100.0 if total_cost > 0 else 0.0
        pnl_color = COL_GREEN if pnl > 0 else COL_RED if pnl < 0 else COL_TEXT
        return [
            ("MÃ ĐANG GIỮ", str(count), COL_TEXT),
            ("GIÁ TRỊ", _compact_vnd(total_value), COL_TEXT),
            ("PNL", f"{_compact_vnd(pnl)} · {pnl_pct:+.2f}%", pnl_color),
            ("BÁN ĐƯỢC", f"{sellable_total:,} CP", COL_GREEN if sellable_total else COL_TEXT),
            ("CHỜ T+", f"{pending_total:,} CP", COL_WARN if pending_total else COL_TEXT),
            ("LÔ LẺ", f"{odd_total:,} CP", COL_WARN if odd_total else COL_TEXT),
        ]

    @staticmethod
    def _history_datetime(value: Any) -> datetime:
        try:
            number = float(value)
            if number > 0:
                return datetime.fromtimestamp(number)
        except (TypeError, ValueError, OSError):
            pass
        text = str(value or "").strip().replace("Z", "+00:00")
        for candidate in (text, text[:19]):
            try:
                parsed = datetime.fromisoformat(candidate)
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except ValueError:
                pass
        for pattern in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text[:19], pattern)
            except ValueError:
                pass
        return datetime.min

    def _signal_log_rows(self) -> list[dict[str, Any]]:
        """Only recent signal days are rendered; Excel keeps the full archive."""
        rows = self.signal_log.read_all(limit=SIGNAL_HISTORY_ROW_LIMIT)
        recent_days = sorted({str(row.get("timestamp", ""))[:10] for row in rows}, reverse=True)
        visible_days = set(recent_days[:HISTORY_DAY_LIMIT])
        return [row for row in rows if str(row.get("timestamp", ""))[:10] in visible_days]

    def _history_popup_groups(self, mode: str) -> list[dict[str, Any]]:
        """Build day -> trade -> event without counting order attempts as trades."""
        mode = "REAL" if str(mode).upper() == "REAL" else "PAPER"
        events: list[dict[str, Any]] = []
        seen_orders: set[str] = set()
        recent_events = CSVOrderJournal(self.bridge.history_csv_path).read_all(
            limit=HISTORY_EVENT_LIMIT,
        )
        for index, row in enumerate(recent_events):
            if str(row.get("execution_mode", "PAPER")).upper() != mode:
                continue
            item = dict(row)
            item["_source"] = "CSV"
            item["_index"] = index
            order_id = str(item.get("broker_order_id", "") or "")
            if order_id:
                seen_orders.add(order_id)
            events.append(item)

        source_rows = self.histories.get(mode, []) or self.snapshots.get(mode, ({}, [], []))[2]
        for index, raw in enumerate(source_rows):
            order_id = str(raw.get("orderId", raw.get("id", "")) or "")
            if order_id and order_id in seen_orders:
                continue
            events.append({
                "timestamp": raw.get("createdAt", raw.get("createdDate", "")),
                "broker_order_id": order_id,
                "trade_id": raw.get("tradeId", ""),
                "symbol": raw.get("symbol", raw.get("instrumentId", "?")),
                "side": raw.get("side", ""),
                "order_type": raw.get("orderType", ""),
                "limit_price": raw.get("averagePrice", raw.get("price", 0)),
                "quantity": raw.get("quantity", raw.get("orderQuantity", 0)),
                "filled_quantity": raw.get("fillQuantity", raw.get("filledQuantity", 0)),
                "remaining_quantity": raw.get("leaveQuantity", 0),
                "fee": raw.get("fee", 0),
                "tax": raw.get("tax", 0),
                "broker_status": raw.get("orderStatus", raw.get("status", "")),
                "message": raw.get("message", raw.get("result", "")),
                "_source": "DNSE" if mode == "REAL" else "PAPER",
                "_index": index,
            })

        cycles = {
            cycle.id: cycle for cycle in self.trade_state.list_cycles()
            if cycle.execution_mode == mode
        }
        by_trade: dict[str, list[dict[str, Any]]] = {}
        for item in events:
            trade_id = str(item.get("trade_id", "") or "").strip()
            fallback = str(item.get("cache_id", item.get("broker_order_id", "")) or "")
            key = trade_id or fallback or f"event-{item.get('_index', len(by_trade))}"
            by_trade.setdefault(key, []).append(item)

        days: dict[str, list[dict[str, Any]]] = {}
        for trade_id, trade_events in by_trade.items():
            trade_events.sort(key=lambda item: self._history_datetime(item.get("timestamp")))
            cycle = cycles.get(trade_id)
            last_dt = max(
                (self._history_datetime(item.get("timestamp")) for item in trade_events),
                default=datetime.min,
            )
            if cycle and cycle.closed_at:
                last_dt = datetime.fromtimestamp(cycle.closed_at)
            day_key = last_dt.strftime("%Y-%m-%d") if last_dt != datetime.min else "0000-00-00"
            symbol = cycle.symbol if cycle else str(trade_events[-1].get("symbol", "?"))
            quantity = cycle.entry_quantity if cycle else max(
                (int(_number(item.get("quantity", 0))) for item in trade_events), default=0,
            )
            entry = cycle.avg_entry_price if cycle else 0.0
            exit_price = cycle.avg_exit_price if cycle else 0.0
            if entry <= 0:
                buy_prices = [
                    _price_unit(item.get("limit_price", 0)) for item in trade_events
                    if str(item.get("side", "")).upper() == "BUY"
                ]
                entry = next((price for price in buy_prices if price > 0), 0.0)
            pnl = cycle.net_pnl if cycle else 0.0
            costs = cycle.fees_paid if cycle else sum(
                _number(item.get("fee", 0)) + _number(item.get("tax", 0)) for item in trade_events
            )
            status = cycle.status if cycle else str(trade_events[-1].get("broker_status", "") or "")
            reason = (cycle.exit_events[-1].replace("_", " ") if cycle and cycle.exit_events else "") or (
                "ĐANG GIỮ" if status == "OPEN" else status
            )
            trade_label = f"{symbol} · {quantity} CP · {_display_price(entry)}"
            if exit_price > 0:
                trade_label += f" → {_display_price(exit_price)}"
            trade_label += f" · {reason} · {status}"
            event_rows: list[dict[str, Any]] = []
            for event in trade_events:
                side = str(event.get("side", "") or "").upper()
                status_text = str(event.get("broker_status", event.get("queue_status", "")) or "").upper()
                quantity_value = int(_number(event.get("quantity", 0)))
                filled = int(_number(event.get("filled_quantity", 0)))
                remaining = int(_number(event.get("remaining_quantity", 0)))
                # Old journal rows were written before persisted fill quantities
                # were copied into CSV. FILLED itself is authoritative.
                if status_text in {"FILLED", "MATCHED"} and filled <= 0:
                    filled, remaining = quantity_value, 0
                price = _price_unit(event.get("limit_price", 0))
                if price <= 0 and status_text in {"FILLED", "MATCHED"} and cycle:
                    price = cycle.avg_entry_price if side == "BUY" else cycle.avg_exit_price
                price_qty = f"{_display_price(price)} · {filled or quantity_value} CP"
                if remaining > 0:
                    price_qty += f" · còn {remaining}"
                fee = _number(event.get("fee", 0))
                tax = _number(event.get("tax", 0))
                event_rows.append({
                    "label": f"{side or 'LỆNH'} · {str(event.get('order_type', '') or '')}",
                    "values": (
                        self._row_time(event.get("timestamp")),
                        str(event.get("broker_order_id", event.get("cache_id", "")) or "")[:18],
                        price_qty,
                        f"{_compact_vnd(fee)} / {_compact_vnd(tax)}",
                        "",
                        status_text,
                        str(event.get("message", event.get("error", "")) or ""),
                    ),
                    "tag": "cancelled" if status_text == "CANCELLED" else "buy" if side == "BUY" else "sell" if side == "SELL" else "pending",
                })
            days.setdefault(day_key, []).append({
                "label": trade_label,
                "values": ("", trade_id[:14], "", _compact_vnd(costs), _compact_vnd(pnl), status, ""),
                "tag": "trade_win" if status == "CLOSED" and pnl >= 0 else "trade_loss" if status == "CLOSED" else "trade_open",
                "events": event_rows,
                "pnl": pnl,
                "costs": costs,
                "closed": status == "CLOSED",
            })

        output: list[dict[str, Any]] = []
        for day_key in sorted(days, reverse=True):
            trades = days[day_key]
            date_label = day_key
            try:
                date_label = datetime.strptime(day_key, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                pass
            closed_count = sum(1 for item in trades if item["closed"])
            pnl = sum(float(item["pnl"]) for item in trades)
            costs = sum(float(item["costs"]) for item in trades)
            output.append({
                "label": f"{date_label} · {closed_count} giao dịch đóng · PNL {_compact_vnd(pnl)} · Phí/thuế {_compact_vnd(costs)}",
                "values": ("", "", "", "", "", "", ""),
                "trades": trades,
            })
        return output[:HISTORY_DAY_LIMIT]

    def _sync_cancel_button(self) -> None:
        mode = "REAL" if self.tabs.get() == "CKCS REAL" else "PAPER"
        tree = self.trees.get(mode)
        selected = [
            row_id for row_id in (tree.selection() if tree else ())
            if self._running_row_actions.get(mode, {}).get(row_id, {}).get("cancellable")
        ]
        self.cancel_button.configure(
            state="normal" if selected else "disabled",
            fg_color=COL_WARN if selected else "#2A2E34",
        )

    def _mode_changed(self, value: str) -> None:
        self.settings.paper_mode = value == "PAPER"
        save_settings(self.settings, self.account_id)
        current = self.bridge.read_config()
        self.bridge.write_config(RuntimeConfig(current.watchlist, self.settings.paper_mode, current.bot_enabled))
        self._paint_account()
        self._sync_default_sl(force=True)

    def _toggle_bot(self) -> None:
        if self._bot_toggle_busy:
            return
        enabled = not self._bot_enabled
        self._bot_toggle_busy = True
        self._paint_bot(enabled, force=True)

        def write_bridge() -> None:
            error = ""
            try:
                current = self.bridge.read_config()
                self.bridge.write_config(
                    RuntimeConfig(current.watchlist, current.paper_mode, enabled)
                )
            except Exception as exc:
                error = str(exc)
            self._post_ui(lambda: self._finish_bot_toggle(enabled, error))

        threading.Thread(target=write_bridge, name="viking-bot-toggle", daemon=True).start()

    def _finish_bot_toggle(self, enabled: bool, error: str = "") -> None:
        self._bot_toggle_busy = False
        if error:
            self._paint_bot(not enabled, force=True)
            self._log(f"Không đổi được trạng thái BOT: {error}", "bot")
            return
        self._bot_sync_until = time.time() + 3.0
        self._paint_bot(enabled, force=True)
        self._log(
            "MUA TỰ ĐỘNG ON · cho phép mở lệnh mới."
            if enabled
            else "MUA TỰ ĐỘNG OFF · ngừng mở lệnh mới; vị thế đang giữ vẫn được quản lý.",
            "bot",
        )

    def _paint_bot(self, enabled: bool, *, force: bool = False) -> None:
        enabled = bool(enabled)
        if not force and enabled != self._bot_enabled:
            if self._bot_toggle_busy or time.time() < self._bot_sync_until:
                return
        self._bot_enabled = enabled
        self.bot_button.configure(
            text=f"MUA TỰ ĐỘNG · {'ON' if enabled else 'OFF'}",
            fg_color=COL_GREEN if enabled else COL_GRAY,
            hover_color="#16A34A" if enabled else "#4B515B",
        )

    def _advanced(self) -> None:
        popup = self._advanced_popup
        if popup and popup.top.winfo_exists():
            popup.show()
            return
        self._advanced_popup = ConnectionPopup(
            self,
            self.settings,
            self.account_id,
            self.real,
            self._settings_saved,
            self._apply_dnse_account,
            lambda visible: self._set_tool_popup_state("CONNECTION", visible),
            self._reset_paper,
        )

    def _rule_settings(self) -> None:
        popup = self._rule_settings_popup
        if popup and popup.top.winfo_exists():
            popup.show()
            return
        self._rule_settings_popup = RuleSettingsPopup(
            self,
            self.settings,
            self.account_id,
            self._settings_saved,
            lambda visible: self._set_tool_popup_state("RULE", visible),
        )

    def _set_tool_popup_state(self, name: str, visible: bool) -> None:
        button = self.rule_button if name == "RULE" else self.connection_button
        button.configure(
            fg_color=COL_GREEN if visible else COL_GRAY,
            hover_color="#16A34A" if visible else "#4B515B",
        )

    def _reset_paper(self, balance: float) -> None:
        self.paper.initial_balance = float(balance)
        self.paper.reset(balance)
        self._log(f"PAPER reset · {balance:,.0f} VND")
        self._refresh_local()

    def _settings_saved(self) -> None:
        save_settings(self.settings, self.account_id)
        current = self.bridge.read_config()
        self.bridge.write_config(
            RuntimeConfig(
                watchlist=list(self.settings.watchlist),
                paper_mode=self.settings.paper_mode,
                bot_enabled=current.bot_enabled,
            )
        )
        self.symbol_entry.configure(values=self.settings.watchlist or ["FPT"])
        self._reload_telegram()

    def _apply_dnse_account(self, account_id: str) -> None:
        selected = config.normalize_account_id(account_id)
        if selected != config.normalize_account_id(self.account_id):
            self._log(f"Đổi workspace {self.account_id} → {selected}; đang khởi động lại Viking.")
            self.close()
            subprocess.Popen(
                [sys.executable, "-m", "viking_v2.main", "--account", selected],
                cwd=config.PROJECT_ROOT,
            )
            return
        self.real.api_key = os.getenv("DNSE_API_KEY", "")
        self.real.api_secret = os.getenv("DNSE_API_SECRET", "")
        self.real.account_no = selected
        self.real.connect()
        self._restart_daemon()

    def _stop_daemon(self) -> None:
        process = self.daemon_process
        self.daemon_process = None
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def _start_daemon(self) -> None:
        if not self.running or (self.daemon_process and self.daemon_process.poll() is None):
            return
        self.bridge.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.bridge.log_dir / "daemon-process.log").open("ab") as stream:
            self.daemon_process = subprocess.Popen(
                [sys.executable, "-m", "viking_v2.services.daemon", "--account", self.account_id],
                cwd=config.PROJECT_ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        self._daemon_started_at = time.time()
        self._daemon_dead_pid = 0
        self._daemon_restart_after = self._daemon_started_at + 2.0
        self._log("Daemon V2 đã khởi động.", "bot")

    def _restart_daemon(self) -> None:
        try:
            self._daemon_crash_times.clear()
            self._daemon_restart_blocked = False
            self._stop_daemon()
            self._start_daemon()
            self._log("Đã áp dụng kết nối DNSE và khởi động lại daemon.", "bot")
        except Exception as exc:
            self._log(f"Đã lưu DNSE nhưng không khởi động lại được daemon: {exc}", "bot")

    def _reload_telegram(self, *, force: bool = False) -> None:
        token = os.getenv(self.settings.telegram_token_env, "")
        signature = (
            bool(self.settings.telegram_enabled),
            str(self.settings.telegram_chat_id or "").strip(),
            str(token or "").strip(),
            int(self.settings.telegram_buy_batch_minutes),
        )
        if not force and signature == self._telegram_signature:
            return
        self.telegram = None
        self._telegram_signature = signature
        enabled, chat_id, token, batch_minutes = signature
        if enabled and chat_id and token:
            self.telegram = SignalTelegramService(
                TelegramClient(token),
                chat_id=chat_id,
                buy_batch_minutes=batch_minutes,
            )
            self._log(f"Telegram gom BUY {batch_minutes} phút; CLOSED gửi ngay đã áp dụng.")

    def _notify_rule_signal(
        self,
        symbol: str,
        decision: StrategyDecision,
        tick: dict[str, Any],
        *,
        signal_id: str = "",
        execution_mode: str = "",
    ) -> dict[str, Any] | None:
        service = self.telegram
        details = decision.details if isinstance(decision.details, dict) else {}
        candle_key = str(
            details.get("signal_cycle") or details.get("candle_key", "") or ""
        )
        raw_price = (
            tick.get("price")
            or tick.get("lastPrice")
            or tick.get("matchPrice")
            or tick.get("expected_price")
            or tick.get("expectedPrice")
            or tick.get("bid")
            or tick.get("ask")
            or 0
        )
        price = _price_unit(raw_price)
        signal = str(decision.signal or "").upper()

        if str(decision.event or "").upper() == "PROTECT_ALERT":
            if not service or not self.settings.telegram_signal_alerts:
                return None
            occurrence = str(details.get("protect_occurrence", "") or "")
            if not self.rule_state.claim_alert(
                f"PROTECT_ALERT|{str(execution_mode or '').upper()}|{symbol}",
                occurrence,
            ):
                return None
            threading.Thread(
                target=service.notify_protect_alert,
                kwargs={
                    "symbol": symbol,
                    "price": price,
                    "mfe_pct": float(details.get("normal_mfe_pct", 0.0) or 0.0),
                    "peak_price": float(details.get("normal_peak_price", 0.0) or 0.0),
                    "protect_price": float(details.get("normal_trigger_price", 0.0) or 0.0),
                    "sell_pct": float(details.get("sell_share_pct", 0.0) or 0.0),
                    "dynamic": bool(details.get("normal_dynamic_enabled", False)),
                },
                daemon=True,
            ).start()
            return None

        # A raw SELL signal is never sent to Telegram.  If the earlier BUY did
        # not become a real position, forget it silently so the next BUY can
        # receive a new ID.  An actual position keeps the ID until fully closed.
        if signal == "SELL":
            record = self.rule_state.active_telegram_signal(symbol)
            if record and not self.trade_state.get(str(record.get("id", ""))):
                self.rule_state.discard_telegram_signal(symbol)
            return None

        if not service:
            return None
        if decision.action != "BUY" or signal != "BUY" or not signal_id:
            self._notify_signal_only(
                service, symbol, signal, decision, price, execution_mode,
            )
            return None
        record = self.rule_state.open_telegram_signal(
            symbol,
            candle_key,
            price=price,
            market_state=decision.market_state,
            signal_id=signal_id,
        )
        if not record:
            return self.rule_state.active_telegram_signal(symbol)
        threading.Thread(
            target=service.notify_buy,
            kwargs={
                "symbol": symbol,
                "signal_id": str(record.get("id", "")),
                "price": price,
                "market_state": decision.market_state,
            },
            daemon=True,
        ).start()
        return record

    def _notify_signal_only(
        self, service: Any, symbol: str, signal: str, decision: Any, price: float,
        execution_mode: str,
    ) -> None:
        """Tell Telegram about signals the bot could not act on, once each."""
        if not self.settings.telegram_signal_alerts or signal not in {"BUY", "SELL"}:
            return
        details = decision.details if isinstance(decision.details, dict) else {}
        occurrence = "|".join(
            (
                signal,
                str(details.get("signal_cycle") or details.get("candle_key", "") or ""),
                str(decision.reason or ""),
            )
        )
        alert_key = (
            f"TELEGRAM_SIGNAL|{str(execution_mode or '').upper()}|{symbol}"
        )
        if not self.rule_state.claim_alert(alert_key, occurrence):
            return
        threading.Thread(
            target=service.notify_signal_only,
            kwargs={
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "market_state": decision.market_state,
                "blocked_by": decision.reason,
            },
            daemon=True,
        ).start()

    def _notify_bot_trade_event(
        self,
        event: str,
        cycle: TradeCycle,
        intent: OrderIntent,
    ) -> None:
        """Send the second Telegram message only after the position is closed."""
        if str(event or "").upper() != "CLOSED" or cycle.source != "BOT":
            return
        record = self.rule_state.claim_closed_telegram_signal(cycle.symbol, cycle.id)
        if not record or not self.telegram:
            return
        threading.Thread(
            target=self.telegram.notify_closed,
            kwargs={"cycle": cycle, "reason": intent.reason},
            daemon=True,
        ).start()

    def _submit(self, side: str) -> None:
        symbol = self.symbol.get().strip().upper()
        kind = self.order_type.get()
        limit_price = 0.0
        if kind == "LO":
            try:
                entered = float(self.price.get().replace(",", ""))
                limit_price = entered / 1000.0 if entered >= 1000 else entered
            except ValueError:
                messagebox.showerror("Manual order", "Giá LO không hợp lệ.", parent=self)
                return
            if limit_price <= 0:
                messagebox.showerror("Manual order", "Lệnh LO bắt buộc có giá.", parent=self)
                return
        raw_quantity = self.quantity.get().strip().replace(",", "")
        if raw_quantity:
            try:
                quantity = int(raw_quantity)
            except ValueError:
                messagebox.showerror("Manual order", "Khối lượng không hợp lệ.", parent=self)
                return
        else:
            entry_price = limit_price
            if kind != "LO":
                entry_price = float(getattr(self, "_current_tick_price", 0.0) or 0.0)
                if kind in {"ATO", "ATC"}:
                    entry_price, _detail = self._auction_preview_price(
                        kind,
                        entry_price,
                        getattr(self, "_current_tick", {}),
                        getattr(self, "_current_market_status", ""),
                    )
            quantity, _budget, _forced_minimum = self._suggested_order_quantity(entry_price)
            if quantity <= 0:
                messagebox.showerror(
                    "Đặt lệnh",
                    "Không đủ vốn mua tối thiểu 100 CP.",
                    parent=self,
                )
                return
        mode = self.mode.get()
        if mode == "REAL" and self.settings.confirm_real_orders:
            answer = messagebox.askyesno(
                "Xác nhận lệnh REAL",
                f"{side} {quantity} {symbol} • {kind}" + (f" @ {_display_price(limit_price)}" if kind == "LO" else ""),
                parent=self,
            )
            if not answer:
                return
        em_key_map = {
            "normal_protection": "NORMAL",
            "indicator_exit": "IND_EXIT",
        }
        em_modes = [
            em_key_map[key]
            for key, enabled in self._em_states.items()
            if enabled and key in em_key_map
        ]
        sl_mode, sl_value = "DEFAULT", 0.0
        sl_raw = str(self.sl.get() or "").strip().replace(",", "")
        try:
            if sl_raw == self._default_sl_text():
                sl_mode, sl_value = "DEFAULT", 0.0
            elif sl_raw.endswith("%"):
                sl_mode, sl_value = "PERCENT", float(sl_raw[:-1])
            elif sl_raw:
                sl_mode, sl_value = "PRICE", _price_unit(float(sl_raw))
        except ValueError:
            messagebox.showerror("Manual order", "Stop Loss không hợp lệ.", parent=self)
            return
        tp_mode, tp_value = "NONE", 0.0
        tp_raw = str(self.tp.get() or "").strip().replace(",", "")
        try:
            if tp_raw.endswith("%"):
                tp_mode, tp_value = "PERCENT", abs(float(tp_raw[:-1]))
            elif tp_raw:
                tp_mode, tp_value = "PRICE", _price_unit(float(tp_raw))
        except ValueError:
            messagebox.showerror("Manual order", "Take Profit không hợp lệ.", parent=self)
            return
        trade_id = uuid.uuid4().hex if side == "BUY" else ""
        exchange = self._symbol_exchange(symbol)
        if not exchange:
            messagebox.showerror("Order", f"Chưa xác định sàn của {symbol}.", parent=self)
            return
        if kind == "ATO" and exchange != "HOSE":
            messagebox.showerror("Order", f"{exchange} không dùng ATO.", parent=self)
            return
        if kind == "ATC" and exchange == "UPCOM":
            messagebox.showerror("Order", "UPCOM không có ATC.", parent=self)
            return
        runtime_status = self.bridge.read_status()
        runtime_decisions = (
            runtime_status.get("decisions")
            if isinstance(runtime_status.get("decisions"), dict) else {}
        )
        entry_decision = (
            runtime_decisions.get(symbol)
            if isinstance(runtime_decisions.get(symbol), dict) else {}
        )
        entry_details = (
            entry_decision.get("details")
            if isinstance(entry_decision.get("details"), dict) else {}
        )
        entry_checks = (
            entry_details.get("entry_checks")
            if isinstance(entry_details.get("entry_checks"), dict) else {}
        )
        intent = OrderIntent.create(
            symbol, side, quantity, kind,
            limit_price=limit_price, execution_mode=mode, source="MANUAL",
            trade_id=trade_id, action="OPEN" if side == "BUY" else "CLOSE",
            em_modes=em_modes, sl_mode=sl_mode, sl_value=sl_value,
            tp_mode=tp_mode, tp_value=tp_value,
            allow_ato=self.settings.allow_ato and self._symbol_exchange(symbol) == "HOSE",
            allow_atc=self.settings.allow_atc and exchange != "UPCOM",
            entry_market_state=str(entry_decision.get("market_state", "UNKNOWN") or "UNKNOWN"),
            entry_exposure=float(entry_details.get("exposure", 0.0) or 0.0),
            entry_budget=float(entry_checks.get("order_budget", 0.0) or 0.0),
        )
        phase = self._current_market_phase()
        result = self.execution.submit(intent, phase=phase, process_immediately=False)
        if symbol not in self.settings.watchlist:
            self.settings.watchlist.append(symbol)
            save_settings(self.settings, self.account_id)
            current = self.bridge.read_config()
            self.bridge.write_config(RuntimeConfig(self.settings.watchlist, current.paper_mode, current.bot_enabled))
        message = f"{mode} {side} {quantity} {symbol} {kind}: {result.status}"
        self._log(message)
        if result.status == "WAITING_TOKEN":
            messagebox.showwarning("Trading token", "Lệnh đã cache 24 giờ. Nhập OTP trong Advanced để tiếp tục.", parent=self)
        elif result.status in {"REJECTED", "FAILED"}:
            messagebox.showerror("Order", result.result or result.status, parent=self)
        self._refresh_local()

    def _cancel_selected(self) -> None:
        mode = "REAL" if self.tabs.get() == "CKCS REAL" else "PAPER"
        tree = self.trees[mode]
        actions = [
            self._running_row_actions[mode][row_id]
            for row_id in tree.selection()
            if row_id in self._running_row_actions[mode]
            and self._running_row_actions[mode][row_id].get("cancellable")
        ]
        if not actions:
            return
        if not messagebox.askyesno(
            "Hủy lệnh đã chọn",
            f"Hủy {len(actions)} lệnh đang chọn?",
            parent=self,
        ):
            return
        broker_actions = [item for item in actions if item.get("broker_order_id") and item.get("mode") == "REAL"]
        if broker_actions and not self.real.has_trading_token():
            messagebox.showwarning("Trading token", "Cần OTP hợp lệ để hủy các lệnh DNSE đã chọn.", parent=self)
            return

        def cancel_batch() -> None:
            messages: list[str] = []
            for action in actions:
                local_id = str(action.get("local_id", "") or "")
                broker_order_id = str(action.get("broker_order_id", "") or "")
                if broker_order_id and action.get("mode") == "REAL":
                    result = self.real.cancel_order(broker_order_id)
                    if result.ok and local_id:
                        self.queue.mark_broker_cancelled(local_id, result.message or "USER_CANCELLED_DNSE")
                    messages.append(
                        f"DNSE #{broker_order_id}: {result.status} {result.message or result.error}".strip()
                    )
                    continue
                cancelled = self.queue.cancel_local(local_id) if local_id else None
                messages.append(
                    f"CACHE #{local_id[:8]} · "
                    f"{'ĐÃ HỦY' if cancelled else 'KHÔNG THỂ HỦY'}"
                )

            def finish() -> None:
                for message in messages:
                    self._log(message)
                self._refresh_local()

            self._post_ui(finish)

        self._io_executor.submit(cancel_batch)

    def _running_right_click(self, event: Any) -> None:
        tree: ttk.Treeview = event.widget
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        mode = "REAL" if tree is self.trees.get("REAL") else "PAPER"
        selected = tuple(tree.selection())
        if row_id not in selected:
            tree.selection_set(row_id)
            selected = (row_id,)
        action = self._running_row_actions.get(mode, {}).get(row_id, {})
        menu = tk.Menu(self, tearoff=0, font=("Segoe UI", 11))
        if len(selected) == 1 and action.get("editable"):
            menu.add_command(label="✎  Sửa lệnh", command=lambda: self._edit_running_order(action))
        if len(selected) == 1 and action.get("kind") == "position":
            menu.add_command(
                label="⚙  Quản lý vị thế",
                command=lambda: self._show_position_management(action),
            )
            cycle = self.trade_state.get(str(action.get("trade_id", "") or ""))
            if cycle:
                menu.add_separator()
                for key, label in EM_TACTICS:
                    active = key in cycle.em_modes
                    menu.add_command(
                        label=f"{'✓' if active else '○'}  {label} · {'ON' if active else 'OFF'}",
                        command=lambda name=key: self._quick_toggle_position_mode(action, name),
                    )
                menu.add_command(
                    label="■  Tắt toàn bộ E/M",
                    command=lambda: self._set_position_modes(action, []),
                )
        cancellable_count = sum(
            1
            for selected_id in selected
            if self._running_row_actions.get(mode, {}).get(selected_id, {}).get("cancellable")
        )
        if cancellable_count:
            if menu.index("end") is not None:
                menu.add_separator()
            menu.add_command(
                label=f"✕  Hủy {cancellable_count} lệnh đã chọn",
                command=self._cancel_selected,
            )
        if menu.index("end") is not None:
            menu.post(event.x_root, event.y_root)

    def _set_position_modes(self, action: dict[str, Any], modes: list[str]) -> None:
        trade_id = str(action.get("trade_id", "") or "")
        cycle = self.trade_state.update_management(trade_id, em_modes=modes) if trade_id else None
        if not cycle:
            return
        self._log(f"[E/M] {cycle.execution_mode} {cycle.symbol} #{cycle.id[:8]}: {'+'.join(cycle.em_modes) or 'OFF'}")
        self._refresh_local()

    def _quick_toggle_position_mode(self, action: dict[str, Any], mode_name: str) -> None:
        trade_id = str(action.get("trade_id", "") or "")
        cycle = self.trade_state.get(trade_id) if trade_id else None
        if not cycle:
            return
        selected = set(cycle.em_modes)
        selected.remove(mode_name) if mode_name in selected else selected.add(mode_name)
        self._set_position_modes(action, sorted(selected))

    def _edit_running_order(self, action: dict[str, Any]) -> None:
        local_id = str(action.get("local_id", "") or "")
        item = self.queue.get(local_id) if local_id else None
        if not item and not action.get("broker_order_id"):
            return
        order_type = item.order_type if item else str(action.get("order_type", "LO") or "LO")
        quantity_value = item.quantity if item else int(action.get("quantity", 0) or 0)
        price_value = item.limit_price if item else float(action.get("price", 0.0) or 0.0)
        top = ctk.CTkToplevel(self)
        top.title("Sửa lệnh")
        top.geometry("460x260")
        top.transient(self)
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            top,
            text=f"{action.get('symbol', '')} · {order_type} · {action.get('mode', '')}",
            font=("Segoe UI", 15, "bold"), text_color=COL_TITLE,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 10))
        ctk.CTkLabel(top, text="Khối lượng", font=FONT_KEY, text_color=COL_TITLE).grid(
            row=1, column=0, sticky="w", padx=18, pady=7
        )
        quantity_entry = ctk.CTkEntry(top, font=FONT_MONO_VALUE)
        quantity_entry.insert(0, str(quantity_value))
        quantity_entry.grid(row=1, column=1, sticky="ew", padx=(8, 18), pady=7)
        ctk.CTkLabel(top, text="Giá LO", font=FONT_KEY, text_color=COL_TITLE).grid(
            row=2, column=0, sticky="w", padx=18, pady=7
        )
        price_entry = ctk.CTkEntry(top, font=FONT_MONO_VALUE)
        price_entry.insert(0, _display_price(price_value) if price_value > 0 else "")
        price_entry.grid(row=2, column=1, sticky="ew", padx=(8, 18), pady=7)
        if order_type != "LO":
            price_entry.configure(state="disabled")
        status = ctk.CTkLabel(top, text="", font=("Segoe UI", 11), text_color=COL_WARN)
        status.grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(5, 0))

        def save() -> None:
            try:
                quantity = int(quantity_entry.get().replace(",", ""))
                raw_price = float(price_entry.get().replace(",", "")) if order_type == "LO" else 0.0
                price = _price_unit(raw_price)
            except ValueError:
                status.configure(text="Khối lượng hoặc giá không hợp lệ.", text_color=COL_RED)
                return
            broker_order_id = str(action.get("broker_order_id", "") or "")
            if broker_order_id and action.get("mode") == "REAL":
                if not self.real.has_trading_token():
                    status.configure(text="Cần OTP hợp lệ để sửa lệnh DNSE.", text_color=COL_RED)
                    return
                status.configure(text="Đang gửi yêu cầu sửa DNSE…", text_color=COL_WARN)

                def replace_broker() -> None:
                    result = self.real.replace_order(broker_order_id, price=price, quantity=quantity)
                    if result.ok and local_id:
                        self.queue.mark_broker_replaced(
                            local_id, quantity=quantity, limit_price=price,
                            result=result.message or "USER_REPLACED_DNSE",
                        )

                    def finish() -> None:
                        status.configure(
                            text=result.message or result.error or result.status,
                            text_color=COL_GREEN if result.ok else COL_RED,
                        )
                        if result.ok:
                            self._refresh_local()

                    self._post_ui(finish)

                self._io_executor.submit(replace_broker)
                return
            updated = self.queue.replace_local(local_id, quantity=quantity, limit_price=price)
            if not updated:
                status.configure(text="Lệnh này không còn sửa được.", text_color=COL_RED)
                return
            self._log(f"Đã sửa cache #{local_id[:8]}: KL {quantity}" + (f" · LO {_display_price(price)}" if price else ""))
            self._refresh_local()
            top.destroy()

        ctk.CTkButton(
            top, text="LƯU THAY ĐỔI", height=36, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GREEN, hover_color="#16A34A", command=save,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=14)

    def _show_position_management(self, action: dict[str, Any]) -> None:
        symbol = str(action.get("symbol", "") or "").upper()
        mode = str(action.get("mode", "PAPER") or "PAPER").upper()
        trade_id = str(action.get("trade_id", "") or "")
        cycle = self.trade_state.get(trade_id) if trade_id else None
        if not cycle:
            cycle = self.trade_state.active_for(symbol, mode)
        row = action.get("position") if isinstance(action.get("position"), dict) else {}
        quantity = int(_number(row.get("openQuantity", row.get("quantity", 0))))
        avg_price = _price_unit(row.get("costPrice", row.get("averagePrice", 0)))

        tick = self._shared_tick(symbol) or {}
        market_price = _price_unit(
            tick.get("price") or tick.get("lastPrice") or tick.get("matchPrice")
            or row.get("marketPrice") or row.get("price") or avg_price
        )
        sellable = int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0))))
        settle = str(row.get("settleDate", "") or "")[:10]
        opened_at = cycle.opened_at if cycle else row.get("openedAt", row.get("createdAt", ""))
        fees = float(cycle.fees_paid if cycle else _number(row.get("buyFee", row.get("fee", 0))))
        pnl = (market_price - avg_price) * quantity * 1000.0 - fees if avg_price > 0 else 0.0
        pnl_pct = pnl / (avg_price * quantity * 1000.0) * 100.0 if avg_price > 0 and quantity > 0 else 0.0
        params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        take_profit = float(params.get("take_profit_pct", 7.0) or 7.0)
        normal_share = float(params.get("normal_sell_pct", 100.0) or 100.0)
        normal_arm = float(params.get("normal_arm_pct", 7.0) or 7.0)
        normal_giveback = float(params.get("normal_giveback_pct", 2.0) or 2.0)
        normal_policy = str(params.get("normal_policy", "AUTO") or "AUTO").upper()
        normal_dynamic = bool(params.get("normal_dynamic_enabled", False))
        normal_repeat = bool(params.get("normal_repeat_enabled", False)) and normal_share < 100.0

        top = ctk.CTkToplevel(self)
        top.title("Quản lý vị thế")
        top.geometry("1040x610")
        top.minsize(920, 570)
        top.transient(self)
        top.grab_set()
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top, text=f"{symbol} · {mode} · {quantity:,} CP",
            font=("Segoe UI", 18, "bold"), text_color=COL_TITLE,
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            top,
            text=(
                f"ID #{cycle.id[:12]} · Mở {self._row_time(opened_at)}" if cycle
                else "Vị thế ngoài hệ thống · cần xác nhận bắt đầu quản lý"
            ),
            font=("Segoe UI", 11), text_color=COL_WARN if not cycle else COL_TEXT,
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 10))

        summary = ctk.CTkFrame(top, fg_color=COL_SURFACE_2, corner_radius=9)
        summary.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))
        for col, weight in enumerate((3, 3, 4, 3, 5)):
            summary.grid_columnconfigure(col, weight=weight, minsize=140)
        settle_short = settle
        if len(settle) == 10:
            settle_short = f"{settle[8:10]}/{settle[5:7]}/{settle[:4]}"
        summary_values = (
            ("GIÁ VÀO", _display_price(avg_price), COL_TEXT),
            ("GIÁ HIỆN TẠI", _display_price(market_price), COL_TEXT),
            ("PNL", f"{pnl:+,.0f}đ · {pnl_pct:+.2f}%", COL_GREEN if pnl >= 0 else COL_RED),
            ("CÓ THỂ BÁN", f"{sellable:,} CP", COL_GREEN if sellable else COL_WARN),
            (
                "T+",
                (f"{quantity - sellable:,} CP" + (f" · {settle_short}" if settle_short else ""))
                if quantity > sellable else "ĐÃ VỀ",
                COL_WARN if quantity > sellable else COL_GREEN,
            ),
        )
        for col, (title, value, color) in enumerate(summary_values):
            card_color = COL_GRAY if col % 2 == 0 else "#343A43"
            card = ctk.CTkFrame(summary, fg_color=card_color, corner_radius=7)
            card.grid(row=0, column=col, sticky="nsew", padx=4, pady=6)
            ctk.CTkLabel(
                card, text=title, font=FONT_KEY,
                text_color=COL_TITLE,
            ).pack(anchor="w", padx=12, pady=(9, 3))
            ctk.CTkLabel(
                card, text=value, font=FONT_MONO_VALUE,
                text_color=color,
            ).pack(anchor="w", padx=12, pady=(0, 10))

        selected = set(cycle.em_modes if cycle else [])
        buttons: dict[str, ctk.CTkButton] = {}
        mode_frame = ctk.CTkFrame(top, fg_color=COL_SURFACE_2, corner_radius=9)
        mode_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=5)
        for column, (key, label) in enumerate(EM_TACTICS):
            mode_frame.grid_columnconfigure(column, weight=1, uniform="em")

            def toggle(name: str = key) -> None:
                if name in selected:
                    selected.remove(name)
                else:
                    selected.add(name)
                active = name in selected
                buttons[name].configure(
                    text=f"{EM_LABELS[name]} · {'ON' if active else 'OFF'}",
                    fg_color="#168A47" if active else "#282D34",
                    text_color=COL_TEXT if active else COL_MUTED,
                )
                refresh_preview()

            active = key in selected
            button = ctk.CTkButton(
                mode_frame, text=f"{label} · {'ON' if active else 'OFF'}",
                height=42, font=("Segoe UI", 11, "bold"), corner_radius=7,
                fg_color="#168A47" if active else "#282D34",
                hover_color="#1EA45A" if active else "#363C45",
                text_color=COL_TEXT if active else COL_MUTED,
                command=toggle,
            )
            button.grid(row=0, column=column, sticky="ew", padx=5, pady=7)
            buttons[key] = button

        targets = ctk.CTkFrame(top, fg_color=COL_SURFACE_2, corner_radius=9)
        targets.grid(row=4, column=0, sticky="ew", padx=20, pady=8)
        targets.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(targets, text="TAKE PROFIT", font=FONT_KEY, text_color=COL_GREEN).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 5))
        tp_entry = ctk.CTkEntry(targets, font=FONT_MONO_VALUE, height=38)
        if cycle and cycle.tp_mode == "PRICE":
            tp_entry.insert(0, _display_price(cycle.tp_value))
        elif cycle and cycle.tp_mode == "PERCENT":
            tp_entry.insert(0, f"{abs(cycle.tp_value):g}%")
        else:
            # Was a dead field before, so a stray save must not now stamp a real
            # target onto the position.  AUTO means "dùng tactic TP nếu bật".
            tp_entry.insert(0, "AUTO")
        tp_entry.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(10, 5))
        ctk.CTkLabel(targets, text="STOP LOSS", font=FONT_KEY, text_color=COL_RED).grid(row=0, column=2, sticky="w", padx=(12, 8), pady=(10, 5))
        sl_entry = ctk.CTkEntry(targets, font=FONT_MONO_VALUE, height=38)
        if cycle and cycle.sl_mode == "PRICE":
            sl_entry.insert(0, _display_price(cycle.sl_value))
        elif cycle and cycle.sl_mode == "PERCENT":
            sl_entry.insert(0, f"{-abs(cycle.sl_value):g}%")
        else:
            sl_entry.insert(0, "AUTO")
        sl_entry.grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=(10, 5))
        tp_preview = ctk.CTkLabel(targets, text="", font=("Segoe UI", 11), text_color=COL_GREEN)
        tp_preview.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 10))
        sl_preview = ctk.CTkLabel(targets, text="", font=("Segoe UI", 11), text_color=COL_RED)
        sl_preview.grid(row=1, column=2, columnspan=2, sticky="w", padx=12, pady=(2, 10))

        details = ctk.CTkFrame(top, fg_color=COL_SURFACE_2, corner_radius=9)
        details.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 8))
        details.grid_columnconfigure((0, 1, 2), weight=1, uniform="position_details")
        detail_values = (
            ("TP", f"Lãi chạm +{take_profit:g}% · bán sạch vị thế"),
            (
                "PROTECT",
                f"{normal_policy} · ARM {normal_arm:g}% · TRAIL {normal_giveback:g}% · SELL {normal_share:g}%"
                f" · DYN {'ON' if normal_dynamic else 'OFF'} · REPEAT {'ON' if normal_repeat else 'OFF'}",
            ),
            ("E", "Tín hiệu SELL · bán hết phần còn lại"),
        )
        for col, (title, value) in enumerate(detail_values):
            card_color = COL_GRAY if col % 2 == 0 else "#343A43"
            card = ctk.CTkFrame(details, fg_color=card_color, corner_radius=7)
            card.grid(row=0, column=col, sticky="ew", padx=4, pady=6)
            ctk.CTkLabel(card, text=title, font=FONT_KEY, text_color=COL_TITLE).pack(anchor="w", padx=12, pady=(9, 3))
            ctk.CTkLabel(card, text=value, font=FONT_VALUE, text_color=COL_TEXT, wraplength=300, justify="left").pack(anchor="w", padx=12, pady=(0, 10))

        status = ctk.CTkLabel(top, text="", font=("Segoe UI", 11), text_color=COL_WARN)
        status.grid(row=6, column=0, sticky="w", padx=20, pady=(2, 0))

        def parse_target(raw: str, *, stop: bool) -> tuple[str, float, float]:
            value = str(raw or "").strip().replace(",", "")
            if not stop and (not value or value.upper() == "AUTO"):
                # No target of its own: the TP tactic and its global % take over.
                return "NONE", 0.0, 0.0
            if stop and (not value or value.upper() == "AUTO"):
                pct = float(params.get("reentry_sl_pct", -2.1) if cycle and cycle.is_reentry else params.get("initial_sl_pct", -3.0) or -3.0)
                return "DEFAULT", 0.0, avg_price * (1.0 + pct / 100.0)
            if not stop and not value:
                return "NONE", 0.0, 0.0
            if value.endswith("%"):
                pct = float(value[:-1])
                pct = -abs(pct) if stop else abs(pct)
                return "PERCENT", abs(pct) if stop else pct, avg_price * (1.0 + pct / 100.0)
            price = _price_unit(float(value))
            return "PRICE", price, price

        def refresh_preview(_event: Any = None) -> None:
            try:
                _tm, _tv, tp_price = parse_target(tp_entry.get(), stop=False)
                _sm, _sv, sl_price = parse_target(sl_entry.get(), stop=True)
                if not tp_price and "TP" in selected and take_profit > 0:
                    # AUTO with the tactic on still has a real target: the global %.
                    tp_price = avg_price * (1.0 + take_profit / 100.0)
                tp_gain = (tp_price - avg_price) * quantity * 1000.0 if tp_price else 0.0
                sl_loss = (sl_price - avg_price) * quantity * 1000.0 if sl_price else 0.0
                tp_preview.configure(
                    text=f"{_display_price(tp_price)} · {tp_gain:+,.0f}đ" if tp_price
                    else "KHÔNG CHỐT LỜI · bật tactic TP hoặc gõ mức riêng",
                )
                sl_preview.configure(text=f"{_display_price(sl_price)} · {sl_loss:+,.0f}đ")
                status.configure(text="")
            except ValueError:
                status.configure(text="TP hoặc SL không hợp lệ.", text_color=COL_RED)

        tp_entry.bind("<KeyRelease>", refresh_preview)
        sl_entry.bind("<KeyRelease>", refresh_preview)
        refresh_preview()

        def save_management() -> None:
            nonlocal cycle
            try:
                sl_mode, sl_value, _sl_price = parse_target(sl_entry.get(), stop=True)
                tp_mode, tp_value, _tp_price = parse_target(tp_entry.get(), stop=False)
            except ValueError:
                status.configure(text="TP hoặc SL không hợp lệ.", text_color=COL_RED)
                return
            if not cycle:
                if quantity <= 0 or avg_price <= 0:
                    status.configure(text="Không đủ giá vốn/khối lượng để quản lý.", text_color=COL_RED)
                    return
                if not messagebox.askyesno(
                    "Bắt đầu quản lý",
                    f"Đưa vị thế {symbol} vào quản lý của Viking?",
                    parent=top,
                ):
                    return
                cycle = self.trade_state.create(
                    symbol, mode, source="EXTERNAL", trade_id=uuid.uuid4().hex,
                    em_modes=sorted(selected), sl_mode=sl_mode, sl_value=sl_value,
                    tp_mode=tp_mode, tp_value=tp_value,
                )
                self.trade_state.record_buy_fill(cycle.id, quantity, avg_price, 0.0)
                if symbol not in self.settings.watchlist:
                    self.settings.watchlist.append(symbol)
                    save_settings(self.settings, self.account_id)
                    current = self.bridge.read_config()
                    self.bridge.write_config(
                        RuntimeConfig(self.settings.watchlist, current.paper_mode, current.bot_enabled)
                    )
            else:
                cycle = self.trade_state.update_management(
                    cycle.id, em_modes=sorted(selected),
                    sl_mode=sl_mode, sl_value=sl_value,
                    tp_mode=tp_mode, tp_value=tp_value,
                )
            if not cycle:
                status.configure(text="Không cập nhật được chu kỳ giao dịch.", text_color=COL_RED)
                return
            self._log(
                f"[E/M] {mode} {symbol} #{cycle.id[:8]}: "
                f"{'+'.join(cycle.em_modes) or 'OFF'} · SL {cycle.sl_mode} {cycle.sl_value:g}"
                f" · TP {cycle.tp_mode} {cycle.tp_value:g}"
            )
            self._refresh_local()
            top.destroy()

        def turn_off_all() -> None:
            selected.clear()
            for name, button in buttons.items():
                label = EM_LABELS[name]
                button.configure(text=f"{label} · OFF", fg_color="#282D34", text_color=COL_MUTED)

        actions = ctk.CTkFrame(top, fg_color="transparent")
        actions.grid(row=7, column=0, sticky="ew", padx=20, pady=(6, 16))
        actions.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            actions, text="TẮT TOÀN BỘ E/M", width=170, height=40,
            font=("Segoe UI", 11, "bold"), fg_color="#3A3F47",
            command=turn_off_all,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            actions, text="LƯU QUẢN LÝ VỊ THẾ", height=40,
            font=("Segoe UI", 12, "bold"), fg_color=COL_GREEN,
            hover_color="#16A34A", command=save_management,
        ).grid(row=0, column=1, sticky="ew")

    def _start_services(self) -> None:
        if not self.running:
            return
        try:
            self._start_daemon()
        except Exception as exc:
            self._log(f"Không khởi động được daemon: {exc}", "bot")
        self._reload_telegram(force=True)

    def _shared_tick(self, symbol: str) -> dict[str, Any] | None:
        ticks = self.bridge.read_status().get("ticks") or {}
        tick = ticks.get(str(symbol).upper())
        return tick if isinstance(tick, dict) else None

    def _symbol_exchange(self, symbol: str | None = None) -> str:
        selected = str(symbol or self.symbol.get() or "").strip().upper()
        status = self.bridge.read_status()
        detected = (status.get("symbol_exchanges") or {}).get(selected, "")
        return normalize_exchange(detected or self.settings.symbol_exchanges.get(selected, ""))

    def _current_market_phase(self) -> str:
        status = self.bridge.read_status()
        working_dates = status.get("working_dates") or []
        if self.mode.get() == "REAL" and self.real.configured() and not working_dates:
            return "CALENDAR_UNKNOWN"
        exchange = self._symbol_exchange()
        if not exchange:
            return "UNKNOWN_EXCHANGE"
        return market_phase(
            working_dates=working_dates or None,
            holidays=self.settings.trading_holidays,
            exchange=exchange,
        )[0]

    def _poll_runtime(self) -> None:
        if not self.running:
            return
        now = time.time()
        process = self.daemon_process
        if process is not None and process.poll() is not None:
            exit_code = process.returncode
            if process.pid != self._daemon_dead_pid:
                self._daemon_dead_pid = process.pid
                self._daemon_crash_times = [
                    value for value in self._daemon_crash_times
                    if now - value <= 300.0
                ]
                self._daemon_crash_times.append(now)
                attempts = len(self._daemon_crash_times)
                self._daemon_restart_after = now + min(60.0, float(2 ** min(attempts, 5)))
                self._daemon_restart_blocked = attempts >= 5
                if self._daemon_restart_blocked:
                    self._log(
                        f"Daemon dừng ({exit_code}) 5 lần/5 phút; khóa tự khởi động. "
                        "Xem logs/daemon-process.log.",
                        "bot",
                    )
                else:
                    self._log(
                        f"Daemon dừng ({exit_code}); thử lại sau "
                        f"{max(1, int(self._daemon_restart_after - now))} giây.",
                        "bot",
                    )
            if not self._daemon_restart_blocked and now >= self._daemon_restart_after:
                self.daemon_process = None
                try:
                    self._start_daemon()
                except Exception as exc:
                    self._daemon_restart_after = now + 10.0
                    self._log(f"Không khởi động lại được daemon: {exc}", "bot")
        status = self.bridge.read_status()
        age = max(0.0, now - _number(status.get("heartbeat_at")))
        daemon = str(status.get("daemon_status", "STARTING"))
        daemon_alive = bool(self.daemon_process and self.daemon_process.poll() is None)
        if daemon_alive and (age > 8 or daemon in {"STARTING", "STOPPED", "STALE"}):
            daemon = "SYNC"
        elif age > 8:
            daemon = "STALE"
        market = str(status.get("market_status", "OFFLINE"))
        symbol = self.symbol.get().strip().upper()
        selected_exchange = self._symbol_exchange(symbol)
        selected_phase = str((status.get("symbol_phases") or {}).get(symbol, market) or market)
        healthy_daemon = daemon == "RUNNING"
        if healthy_daemon and now - self._daemon_started_at >= 60.0:
            self._daemon_crash_times.clear()
            self._daemon_restart_blocked = False
        if market == "CALENDAR_UNKNOWN":
            session_text, active_market = "CLOSED", False
        else:
            session_text, active_market = market_session_clock(
                working_dates=status.get("working_dates") or None,
                holidays=self.settings.trading_holidays,
                exchange=selected_exchange,
            )
        if selected_exchange:
            session_text = f"{symbol} · {selected_exchange}\n{session_text}"
        elif symbol:
            session_text, active_market = f"{symbol} · CHƯA XÁC ĐỊNH SÀN", False
        self.lbl_session.configure(
            text=session_text,
            text_color=COL_GREEN if active_market else COL_RED,
        )
        self.lbl_brain.configure(
            text=f"DAEMON: {daemon}",
            text_color=COL_GREEN if healthy_daemon else COL_WARN if daemon == "SYNC" else COL_RED,
        )
        self._paint_bot(bool(status.get("bot_enabled", False)))
        tick = (status.get("ticks") or {}).get(symbol) or {}
        raw_price = (
            tick.get("price")
            or tick.get("lastPrice")
            or tick.get("matchPrice")
            or tick.get("expected_price")
            or tick.get("expectedPrice")
            or tick.get("bid")
            or tick.get("ask")
            or 0
        )
        price = _price_unit(raw_price)
        self._current_tick_price = price
        self._current_tick = dict(tick) if isinstance(tick, dict) else {}
        self._current_market_status = selected_phase.upper()
        if self.order_type.get() == "LO":
            self._populate_default_lo()
        self._update_order_preview()
        if time.time() - self._last_running_render >= 2.0:
            self._render_tables(status)
        self._refresh_api_health_panel(status)
        self._consume_bot_decisions(status, daemon)
        self.after(1000, self._poll_runtime)

    @staticmethod
    def _blocked_decision(decision: StrategyDecision, reason: str) -> StrategyDecision:
        return StrategyDecision(
            "WAIT", decision.symbol, str(reason or "WAIT"), signal=decision.signal,
            market_state=decision.market_state, details=dict(decision.details or {}),
            scope=decision.scope,
        )

    def _plan_rule_decision(
        self, decision: StrategyDecision, tick: dict[str, Any], mode: str,
        *, available_cash: float | None = None,
    ) -> Any:
        details = decision.details if isinstance(decision.details, dict) else {}
        checks = details.get("entry_checks") if isinstance(details.get("entry_checks"), dict) else {}
        portfolio = {
            "order_budget": details.get("order_budget", 0.0),
            "trade_id": details.get("trade_id", ""),
            "position_quantity": details.get("position_quantity", 0),
        }
        if available_cash is not None:
            portfolio.update(
                available_cash=max(0.0, available_cash),
                nav=checks.get("nav", 0.0),
                minimum_order_room=min(
                    max(0.0, available_cash),
                    float(checks.get("minimum_order_room", available_cash) or available_cash),
                ),
                buy_fee_rate=checks.get("buy_fee_rate", 0.0),
            )
        return self.strategy_planner.plan(
            decision, execution_mode=mode, execution_style=self.settings.bot_order_mode,
            tick=tick, portfolio=portfolio,
            candle_key=str(
                details.get("signal_cycle") or details.get("candle_key", "") or ""
            ),
            allow_ato=self.settings.allow_ato and self._symbol_exchange(decision.symbol) == "HOSE",
            allow_atc=self.settings.allow_atc and self._symbol_exchange(decision.symbol) != "UPCOM",
            bot_em_modes=self.settings.bot_em_modes,
            sell_wait_policy=self.settings.sell_wait_policy,
        )

    def _record_signal_decision(
        self, decision: StrategyDecision, tick: dict[str, Any], mode: str,
        allocator: BuySlotAllocator,
    ) -> None:
        details = decision.details if isinstance(decision.details, dict) else {}
        marks = details.get("indicators") if isinstance(details.get("indicators"), dict) else {}
        confirmation = details.get("buy_confirmation") if isinstance(details.get("buy_confirmation"), dict) else {}
        window = details.get("buy_window") if isinstance(details.get("buy_window"), dict) else {}
        symbol = str(decision.symbol or "").upper()
        protect_alert = str(decision.event or "").upper() == "PROTECT_ALERT"
        raw_price = (
            tick.get("price") or tick.get("lastPrice") or tick.get("matchPrice")
            or tick.get("expected_price") or tick.get("expectedPrice")
            or tick.get("bid") or tick.get("ask") or 0
        )
        try:
            priority = self.settings.watchlist.index(symbol) + 1
        except ValueError:
            priority = 0
        self.signal_log.record({
            "timestamp": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "execution_mode": mode, "symbol": symbol,
            "signal": "PROTECT" if protect_alert else decision.signal,
            "price": _price_unit(raw_price),
            "ema_fast": round(float(marks.get("buy_ema_fast") or 0.0), 4),
            "ema_slow": round(float(marks.get("buy_ema_slow") or 0.0), 4),
            "rsi": round(float(marks.get("rsi") or 0.0), 2),
            "market_state": decision.market_state, "acted": decision.action,
            "blocked_by": "" if decision.action != "WAIT" else decision.reason,
            "candle_key": (
                details.get("protect_occurrence", "") if protect_alert
                else details.get("candle_key", "")
            ),
            "signal_cycle": (
                details.get("protect_occurrence", "") if protect_alert
                else details.get("signal_cycle", "")
            ),
            "exchange": self._symbol_exchange(symbol),
            "signal_time": decision_signal_time(decision),
            "decision_time": confirmation.get("observed_time") or datetime.now(VN_TZ).isoformat(),
            "confirmation_state": confirmation.get("state", "BYPASS"),
            "confirmation_minutes": int(confirmation.get("minutes_held", 0) or 0),
            "confirmation_required": confirmation.get("minutes_required", ""),
            "confirmation_ema": confirmation.get("ema_ok", ""),
            "confirmation_rsi": confirmation.get("rsi_ok", ""),
            "buy_window": f"{window.get('start', '')}–{window.get('end', '')}" if window else "",
            "buy_window_state": window.get("state", ""),
            "watchlist_priority": priority,
            "slot_usage": f"{allocator.used}/{allocator.max_positions}",
            "trade_id": details.get("trade_id", ""),
            "protect_mode": details.get("normal_policy", ""),
            "protect_state": details.get("normal_state", ""),
            "mfe_pct": details.get("normal_mfe_pct", ""),
            "peak_price": details.get("normal_peak_price", ""),
            "effective_trail_pct": details.get("normal_effective_trail_pct", ""),
            "protect_price": details.get("normal_trigger_price", ""),
            "sell_pct": details.get("sell_share_pct", ""),
            "hypothetical_quantity": (
                sell_quantity_for_fraction(
                    int(details.get("position_quantity", 0) or 0),
                    float(details.get("sell_share_pct", 0.0) or 0.0) / 100.0,
                ) if protect_alert else ""
            ),
        })

    def _claim_terminal_buy(self, decision: StrategyDecision, mode: str) -> None:
        if (
            str(decision.signal or "").upper() != "BUY"
            or not is_terminal_buy_block(decision.reason)
        ):
            return
        details = decision.details if isinstance(decision.details, dict) else {}
        self.rule_state.claim_signal(
            decision.symbol,
            "BUY",
            str(details.get("signal_cycle") or details.get("candle_key", "") or ""),
            stream=mode,
        )

    def _consume_bot_decisions(self, status: dict[str, Any], daemon_status: str) -> None:
        if daemon_status != "RUNNING":
            return
        runtime = self.bridge.read_config()
        mode = "PAPER" if runtime.paper_mode else "REAL"
        protect_policy = str(
            (self.settings.rule_parameters or {}).get("normal_policy", "AUTO") or "AUTO"
        ).upper()
        if protect_policy == "ALERT":
            cancelled: list[OrderIntent] = []
            broker_managed: list[OrderIntent] = []
            # PROTECT settings are global.  Clear safe local requests in both
            # execution books so changing PAPER/REAL later cannot revive an
            # AUTO request created before ALERT was selected.
            for protect_mode in ("PAPER", "REAL"):
                local_cancelled, local_broker_managed = (
                    self.execution.cancel_unsubmitted_protect_sells(protect_mode)
                )
                cancelled.extend(local_cancelled)
                broker_managed.extend(local_broker_managed)
            if cancelled:
                self._log(
                    f"[PROTECT] ALERT · đã hủy {len(cancelled)} lệnh chưa gửi.", "bot",
                )
            seen = getattr(self, "_protect_alert_broker_orders", set())
            current_ids = {item.id for item in broker_managed}
            for item in broker_managed:
                if item.id not in seen:
                    self._log(
                        f"[PROTECT] ALERT · {item.symbol} còn lệnh {item.status} đã lên luồng broker; "
                        "operator kiểm tra/hủy thủ công.",
                        "bot",
                    )
            self._protect_alert_broker_orders = current_ids
        bot_enabled = bool(status.get("bot_enabled", False))
        raw_decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decisions: dict[str, StrategyDecision] = {}
        for symbol, raw in raw_decisions.items():
            if not isinstance(raw, dict):
                continue
            try:
                decisions[str(symbol).upper()] = StrategyDecision.from_dict(raw)
            except Exception as exc:
                self._log(f"[RULE] Quyết định {symbol} không hợp lệ: {exc}", "bot")

        positions = self.snapshots.get(mode, ({}, [], []))[1]
        max_positions = int((self.settings.rule_parameters or {}).get("max_positions", 5) or 5)
        current_intents = self.queue.list_all()
        allocator = BuySlotAllocator.from_runtime(max_positions, positions, current_intents, mode)
        self._slot_summary = {
            "used": allocator.used, "max": allocator.max_positions,
            "pending": sum(
                1 for item in current_intents
                if item.side == "BUY" and item.execution_mode == mode
                and str(item.status).upper() not in {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}
            ),
        }
        ticks = status.get("ticks") if isinstance(status.get("ticks"), dict) else {}

        # Position exits and non-actionable observations never compete for BUY slots.
        for symbol, decision in decisions.items():
            if decision.action == "BUY" and str(decision.signal or "").upper() == "BUY":
                continue
            tick = ticks.get(symbol) if isinstance(ticks.get(symbol), dict) else {}
            self._notify_corporate_action(symbol, decision, mode)
            final = decision
            if decision.action == "SELL":
                if decision.scope != "POSITION_MANAGEMENT" and not bot_enabled:
                    final = self._blocked_decision(decision, "BOT_OFF")
                else:
                    result = self._plan_rule_decision(decision, tick, mode)
                    if result.intent:
                        self._log(
                            f"[RULE] {decision.event or decision.reason} → {mode} "
                            f"{result.intent.side} {result.intent.quantity} {symbol} {result.intent.order_type}",
                            "bot",
                        )
                    elif result.reason != decision.reason:
                        final = self._blocked_decision(decision, result.reason)
            self._claim_terminal_buy(final, mode)
            self._record_signal_decision(final, tick, mode, allocator)
            self._notify_rule_signal(symbol, final, tick, execution_mode=mode)

        ranked_buys = [
            item for item in decisions.values()
            if item.action == "BUY" and str(item.signal or "").upper() == "BUY"
        ]
        available_cash = max(
            (
                float((item.details.get("entry_checks") or {}).get("available_cash", 0.0) or 0.0)
                for item in ranked_buys
            ), default=0.0,
        )
        available_cash -= sum(
            max(0.0, float(item.entry_budget or 0.0))
            for item in current_intents
            if item.side == "BUY" and item.execution_mode == mode
            and str(item.status).upper() not in {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}
        )

        def plan_buy(candidate: Any) -> BuyAttempt:
            nonlocal available_cash
            decision = candidate.decision
            tick = ticks.get(candidate.symbol) if isinstance(ticks.get(candidate.symbol), dict) else {}
            result = self._plan_rule_decision(
                decision, tick, mode, available_cash=max(0.0, available_cash),
            )
            intent = result.intent
            if not intent:
                return BuyAttempt(reason=result.reason)
            price = _price_unit(tick.get("ask", tick.get("price", 0.0)) or 0.0)
            checks = decision.details.get("entry_checks") or {}
            fee_rate = float(checks.get("buy_fee_rate", 0.0) or 0.0)
            available_cash -= intent.quantity * price * 1000.0 * (1.0 + fee_rate)
            return BuyAttempt(payload=intent, reason="PLANNED")

        coordinated = coordinate_buy_decisions(
            decisions, self.settings.watchlist, allocator,
            bot_enabled=bot_enabled, plan=plan_buy,
        )
        for outcome in coordinated:
            symbol, decision = outcome.candidate.symbol, outcome.candidate.decision
            tick = ticks.get(symbol) if isinstance(ticks.get(symbol), dict) else {}
            self._notify_corporate_action(symbol, decision, mode)
            intent = outcome.payload
            final = (
                decision if intent
                else self._blocked_decision(decision, outcome.blocked_by)
            )
            if intent:
                self._log(
                    f"[RULE] {decision.event or decision.reason} → {mode} "
                    f"BUY {intent.quantity} {symbol} {intent.order_type}", "bot",
                )
                self._notify_rule_signal(
                    symbol, decision, tick,
                    signal_id=intent.trade_id, execution_mode=mode,
                )
            else:
                self._claim_terminal_buy(final, mode)
                self._notify_rule_signal(
                    symbol, final, tick, execution_mode=mode,
                )
            self._record_signal_decision(final, tick, mode, allocator)

        final_intents = self.queue.list_all()
        self._slot_summary.update(
            used=allocator.used,
            pending=sum(
                1 for item in final_intents
                if item.side == "BUY" and item.execution_mode == mode
                and str(item.status).upper()
                not in {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}
            ),
        )

    def _latest_sell_decision(self, symbol: str, execution_mode: str) -> dict[str, Any] | None:
        runtime = self.bridge.read_config()
        active_mode = "PAPER" if runtime.paper_mode else "REAL"
        if active_mode != str(execution_mode or "").upper():
            return None
        status = self.bridge.read_status()
        heartbeat = float(status.get("heartbeat_at", 0.0) or 0.0)
        if heartbeat <= 0 or time.time() - heartbeat > max(6.0, config.HEARTBEAT_SECONDS * 3):
            return None
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        value = decisions.get(str(symbol or "").upper())
        return value if isinstance(value, dict) else None

    def _notify_corporate_action(
        self,
        symbol: str,
        decision: StrategyDecision,
        execution_mode: str,
    ) -> None:
        details = decision.details if isinstance(decision.details, dict) else {}
        action = details.get("corporate_action") if isinstance(details.get("corporate_action"), dict) else {}
        if not action or not details.get("corporate_action_warning"):
            return
        occurrence = str(action.get("ex_date", "") or datetime.now().date().isoformat())
        key = f"CORPORATE_ACTION|{execution_mode}|{str(symbol or '').upper()}"
        if not self.rule_state.claim_alert(key, occurrence):
            return
        message = (
            f"[CHỐT QUYỀN] {str(symbol or '').upper()} đang có position. "
            f"Ngày GDKHQ {occurrence}; operator kiểm tra và xử lý thủ công."
        )
        self._log(message, "bot")
        if self.telegram:
            threading.Thread(
                target=self.telegram.notify_corporate_action,
                kwargs={"symbol": str(symbol or "").upper(), "ex_date": occurrence},
                daemon=True,
            ).start()

    def _record_failed_buy_execution(
        self,
        execution_mode: str,
        intent: OrderIntent,
        broker_result: Any,
    ) -> None:
        if intent.side != "BUY" or intent.source != "BOT":
            return
        status = str(getattr(broker_result, "status", "") or "").upper()
        ok = bool(getattr(broker_result, "ok", False))
        if ok and status not in {"REJECTED", "FAILED", "EXPIRED"}:
            return
        reason = {
            "REJECTED": "BROKER_REJECTED",
            "FAILED": "BROKER_FAILED",
            "EXPIRED": "BUY_WINDOW_EXPIRED",
        }.get(status, "BROKER_FAILED")
        self.rule_state.discard_telegram_signal(intent.symbol)
        decision = StrategyDecision(
            "WAIT", intent.symbol, reason, signal="BUY",
            details={
                "candle_key": intent.candle_key,
                "signal_cycle": intent.candle_key,
            },
        )
        mode = str(execution_mode or intent.execution_mode).upper()
        positions = self.snapshots.get(mode, ({}, [], []))[1]
        allocator = BuySlotAllocator.from_runtime(
            int((self.settings.rule_parameters or {}).get("max_positions", 5) or 5),
            positions,
            self.queue.list_all(),
            mode,
        )
        tick = self._shared_tick(intent.symbol) or {}
        self._record_signal_decision(decision, tick, mode, allocator)
        self._notify_rule_signal(
            intent.symbol, decision, tick, execution_mode=mode,
        )

    def _process_orders(self) -> None:
        if not self.running:
            return
        if self._order_worker_busy:
            self.after(500, self._process_orders)
            return
        self._order_worker_busy = True
        phase = self._current_market_phase()
        runtime_status = self.bridge.read_status()
        symbol_phases = dict(runtime_status.get("symbol_phases") or {})

        def phase_for(symbol: str) -> str:
            return str(symbol_phases.get(str(symbol).upper(), "UNKNOWN_EXCHANGE") or "UNKNOWN_EXCHANGE")

        def work() -> list[tuple[str, OrderIntent, Any]]:
            self.execution.reconcile_working("REAL")
            completed: list[tuple[str, OrderIntent, Any]] = []
            for selected_mode in ("PAPER", "REAL"):
                for intent, result in self.execution.process_due(
                    phase=phase,
                    execution_mode=selected_mode,
                    phase_provider=phase_for,
                ):
                    completed.append((selected_mode, intent, result))
            return completed

        future = self._io_executor.submit(work)

        def completed(result: Any) -> None:
            try:
                rows = result.result()
                error = ""
            except Exception as exc:
                rows = []
                error = str(exc)

            def apply() -> None:
                self._order_worker_busy = False
                if not self.running:
                    return
                if error:
                    self.logger.error("Execution worker failed: %s", error)
                for selected_mode, intent, broker_result in rows:
                    self._log(
                        f"{selected_mode} {intent.side} {intent.symbol}: "
                        f"{broker_result.status} {broker_result.message}"
                    )
                    self._record_failed_buy_execution(
                        selected_mode, intent, broker_result,
                    )
                self._refresh_local()
                self.after(1000, self._process_orders)

            self._post_ui(apply)

        future.add_done_callback(completed)

    def _refresh_snapshots(self) -> None:
        if not self.running:
            return
        if self._snapshot_busy:
            self.after(1000, self._refresh_snapshots)
            return
        self._snapshot_busy = True

        def work() -> dict[str, tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]]:
            values = {"PAPER": self.execution.account_snapshot("PAPER")}
            if self.real.configured():
                values["REAL"] = self.execution.account_snapshot("REAL")
            return values

        future = self._io_executor.submit(work)

        def completed(result: Any) -> None:
            try:
                values = result.result()
                error = ""
            except Exception as exc:
                values = {}
                error = str(exc)

            def apply() -> None:
                self._snapshot_busy = False
                if not self.running:
                    return
                if error:
                    self.logger.warning("Account refresh failed: %s", error)
                self.snapshots.update(values)
                if "PAPER" in values:
                    self.histories["PAPER"] = list(values["PAPER"][2])
                self._refresh_local()

            self._post_ui(apply)

        future.add_done_callback(completed)
        self.after(5000, self._refresh_snapshots)

    def _refresh_local(self) -> None:
        if not self.running:
            return
        self._paint_account()
        self._render_tables()

    def _paint_account(self) -> None:
        mode = self.mode.get()
        balance = self.snapshots.get(mode, ({}, [], []))[0]
        self.lbl_equity.configure(text=f"{_equity(balance):,.0f} ₫")
        pnl = _number(balance.get("realizedPnl", balance.get("pnl", 0.0)))
        self.lbl_pnl.configure(text=f"PNL: {pnl:,.0f}", text_color=COL_GREEN if pnl >= 0 else COL_RED)
        fee_today = self.daily_fees.total(mode)
        self.lbl_cash.configure(
            text=f"FEE: -{fee_today:,.0f}" if fee_today > 0 else "FEE: 0",
            text_color=COL_WARN,
        )
        self.lbl_account.configure(text=f"ID: {self.account_id}  ·  {mode}  ·  CASH {_cash(balance):,.0f}")

    def _reset_daily_fee(self) -> None:
        mode = self.mode.get()
        if not messagebox.askyesno(
            "Reset fee",
            f"Reset tổng phí hôm nay của {mode}?\nLịch sử CSV vẫn được giữ nguyên.",
            parent=self,
        ):
            return
        self.daily_fees.reset(mode)
        self._paint_account()
        self._log(f"[{mode}] Đã reset bộ đếm fee hôm nay; lịch sử CSV không bị xóa.")
