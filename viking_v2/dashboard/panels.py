from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from ..rules.business import average_true_range_pct, indicator_snapshot, protect_level
from ..trading.market import VN_TZ, market_now, market_phase, merge_tick_into_daily_bars
from ..trading.portfolio import nav_from_balance, size_buy_order, stock_exposure_limit, validate_quantity
from ..trading.validation import decision_is_fresh, quote_is_fresh
from .view import (
    COL_BORDER, COL_GRAY, COL_GREEN, COL_MUTED, COL_SETTLEMENT_BG, COL_SETTLEMENT_TEXT,
    COL_PREVIEW_TEXT, COL_RED, COL_SURFACE, COL_SURFACE_2, COL_TEXT, COL_WARN, FONT_BOLD,
    COL_TITLE, FONT_PREVIEW_TITLE, FONT_PREVIEW_VALUE, _compact_vnd, _display_price,
    _number, _price_unit,
)
from .windows import (
    FONT_KEY,
    FONT_MONO_VALUE,
    FONT_VALUE,
    _HoverHint,
    fit_entry_text,
)


def _dynamic_atr_preview_text(
    details: dict[str, Any], params: dict[str, Any],
) -> str:
    """Render a compact state; full ATR explanation lives in the hover hint."""
    sell = _number(details.get("sell_share_pct", params.get("normal_sell_pct", 100.0)))
    dynamic = bool(details.get(
        "normal_dynamic_enabled", params.get("normal_dynamic_enabled", False),
    ))
    if not dynamic:
        return f"DYN OFF · BÁN {sell:g}%"
    atr = _number(details.get("normal_atr_pct"))
    if atr <= 0:
        return f"DYN · ATR -- · BÁN {sell:g}%"
    return f"DYN · ATR {atr:.2f}% · BÁN {sell:g}%"


def _preview_panel_height(viewport_pixels: int, widget_scaling: float) -> int:
    """Convert Tk's scaled viewport pixels back to CustomTkinter logical units."""

    scaling = max(0.1, float(widget_scaling or 1.0))
    logical_height = int(round(max(0, viewport_pixels) / scaling))
    return max(300, logical_height - 4)


class DashboardPanelsMixin:
    def _left_panel(self) -> None:
        # GROUP 1 — account snapshot and session state.
        account = ctk.CTkFrame(
            self.left, fg_color=COL_SURFACE, corner_radius=10,
            border_width=1, border_color=COL_BORDER,
        )
        account.pack(fill="x", pady=(2, 3), padx=6)
        account.grid_columnconfigure(0, weight=1)
        account.grid_columnconfigure(1, weight=0, minsize=180)

        self.lbl_equity = ctk.CTkLabel(
            account, text="----", font=("Segoe UI", 30, "bold"),
            text_color=COL_GREEN, anchor="w",
        )
        self.lbl_equity.grid(row=0, column=0, sticky="w", padx=(11, 5), pady=(5, 0))
        session_box = ctk.CTkFrame(
            account, width=180, height=52, fg_color="transparent",
        )
        session_box.grid(row=0, column=1, sticky="ne", padx=(3, 11), pady=(7, 0))
        session_box.pack_propagate(False)
        self.lbl_session = ctk.CTkLabel(
            session_box, text="PHIÊN: --", font=("Segoe UI", 11, "bold"),
            text_color=COL_MUTED, anchor="e", justify="right", width=176,
        )
        self.lbl_session.pack(fill="x")
        self.lbl_brain = ctk.CTkLabel(
            session_box, text="DAEMON: CHỜ", font=("Segoe UI", 11, "bold"),
            text_color=COL_WARN, anchor="e",
        )
        self.lbl_brain.pack(fill="x", pady=(2, 0))

        self.lbl_account = ctk.CTkLabel(
            account, text=f"ID: {self.account_id}  ·  PAPER",
            font=("Segoe UI", 11, "bold"), text_color=COL_TEXT, anchor="w",
            justify="left",
        )
        self.lbl_account.grid(
            row=1, column=0, columnspan=2, sticky="ew",
            padx=(11, 11), pady=(0, 2),
        )
        account_footer = ctk.CTkFrame(account, fg_color="transparent")
        account_footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=11, pady=(1, 5))
        account_footer.grid_columnconfigure(0, minsize=82)
        account_footer.grid_columnconfigure(1, minsize=120)
        account_footer.grid_columnconfigure(2, weight=1)
        self.lbl_pnl = ctk.CTkLabel(
            account_footer, text="PNL NGÀY: 0", font=("Segoe UI", 12, "bold"), anchor="w"
        )
        self.lbl_pnl.grid(row=0, column=0, sticky="w")
        self.lbl_cash = ctk.CTkLabel(
            account_footer, text="FEE NGÀY: 0", font=("Segoe UI", 12, "bold"),
            text_color=COL_WARN, anchor="w",
        )
        self.lbl_cash.grid(row=0, column=1, sticky="w", padx=(24, 0))
        self.btn_reset_daily_stats = ctk.CTkButton(
            account_footer, text="↻", width=25, height=22,
            font=("Segoe UI Symbol", 12, "bold"),
            fg_color="#282D34", hover_color="#3A414B",
            text_color=COL_TEXT, corner_radius=6,
            command=self._reset_daily_stats,
        )
        self.btn_reset_daily_stats.grid(row=0, column=3, sticky="e", padx=(14, 0))
        # Compatibility name for lightweight UI integrations which still
        # address the former fee-only control.
        self.btn_reset_daily_fee = self.btn_reset_daily_stats
        _HoverHint(
            self.btn_reset_daily_stats,
            "Reset PNL/phí và clear rule cooldown; tiền, vị thế, lịch sử vẫn giữ nguyên.",
        )

        # GROUP 2 — exactly four compact control rows.
        control = ctk.CTkFrame(
            self.left, fg_color=COL_SURFACE, corner_radius=10,
            border_width=1, border_color=COL_BORDER,
        )
        control.pack(fill="x", padx=6, pady=4)
        control.columnconfigure(0, minsize=55)
        control.columnconfigure(1, weight=1)

        def setting_row(index: int, label: str) -> ctk.CTkFrame:
            ctk.CTkLabel(
                control, text=label, font=FONT_KEY,
                text_color=COL_TITLE, anchor="w", justify="left",
            ).grid(row=index, column=0, sticky="ew", padx=(7, 6), pady=3)
            frame = ctk.CTkFrame(control, width=1, height=34, fg_color="transparent")
            frame.grid(row=index, column=1, sticky="ew", padx=(0, 7), pady=1)
            # Child buttons must consume the available panel width, not enlarge
            # the whole left dashboard column from their requested widths.
            frame.grid_propagate(False)
            return frame

        symbol_row = setting_row(0, "MÃ CK")
        symbol_row.grid_columnconfigure(0, weight=1)
        self.symbol = tk.StringVar(
            value=(self.settings.watchlist[0] if self.settings.watchlist else "FPT")
        )
        self.symbol_entry = ctk.CTkComboBox(
            symbol_row, values=self.settings.watchlist or ["FPT"],
            variable=self.symbol, command=self._symbol_changed,
            font=FONT_BOLD, height=32, corner_radius=7,
            border_color=COL_BORDER, fg_color=COL_SURFACE_2,
            button_color=COL_GRAY, button_hover_color="#4B515B",
        )
        self.symbol_entry.grid(row=0, column=0, sticky="ew")

        mode_row = setting_row(1, "MODE")
        mode_row.grid_columnconfigure(0, weight=4)
        mode_row.grid_columnconfigure(1, weight=1)
        self.mode = tk.StringVar(value="PAPER" if self.settings.paper_mode else "REAL")
        ctk.CTkSegmentedButton(
            mode_row, values=["REAL", "PAPER"], variable=self.mode,
            command=self._mode_changed, height=32, font=FONT_VALUE,
            width=1,
            selected_color=COL_GREEN, selected_hover_color="#16A34A",
            unselected_color=COL_GRAY, unselected_hover_color="#4B515B",
            text_color=COL_TEXT,
            corner_radius=7,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.bot_button = ctk.CTkButton(
            mode_row, text="BOT · OFF", height=32, font=("Segoe UI", 11, "bold"),
            width=1,
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=7,
            command=self._toggle_bot,
        )
        self.bot_button.grid(row=0, column=1, sticky="ew")
        _HoverHint(
            self.bot_button,
            "BOT ON (xanh): được phép tạo và gửi BUY tự động.\n"
            "BOT PAUSE (vàng): đang khóa BUY tạm thời sau SELL MANUAL; SELL/SL/PROTECT vẫn chạy.\n"
            "BOT OFF (xám): không tạo hoặc gửi BUY BOT.",
        )

        tools_row = setting_row(2, "TOOLS")
        tool_specs = [
            ("⚙  RULE", self._rule_settings),
            ("⛓  KẾT NỐI", self._advanced),
        ]
        for column, (text, command) in enumerate(tool_specs):
            tools_row.grid_columnconfigure(column, weight=1, uniform="tool")
            button = ctk.CTkButton(
                tools_row, text=text, height=32, font=("Segoe UI", 11, "bold"),
                width=1,
                fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=7,
                command=command,
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0))
            if column == 0:
                self.rule_button = button
            else:
                self.connection_button = button

        em_row = setting_row(3, "E/M")
        self._em_specs = {
            "normal_protection": "PROTECT",
            "indicator_exit": "E",
        }
        self._em_states = {key: False for key in self._em_specs}
        self._em_buttons: dict[str, ctk.CTkButton] = {}
        for column, key in enumerate(self._em_states):
            em_row.grid_columnconfigure(column, weight=1, uniform="em")
            title = self._em_specs[key]
            button = ctk.CTkButton(
                em_row, text=f"{title} · OFF", height=32,
                font=("Segoe UI", 11, "bold"),
                width=1,
                fg_color="#282D34", hover_color="#363C45",
                text_color=COL_MUTED, corner_radius=7,
                command=lambda name=key: self._toggle_em_frontend(name),
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0))
            self._em_buttons[key] = button

        # GROUP 3 — price first, compact action second, manual inputs last.
        order = ctk.CTkFrame(
            self.left, fg_color=COL_SURFACE, corner_radius=10,
            border_width=1, border_color=COL_BORDER,
        )
        order.pack(fill="x", padx=6, pady=3)

        quote = ctk.CTkFrame(order, fg_color=COL_SURFACE_2, corner_radius=8)
        quote.pack(fill="x", padx=8, pady=(7, 4))
        quote.grid_columnconfigure(0, weight=1)
        quote.grid_columnconfigure(1, weight=1)
        self.lbl_quote_symbol = ctk.CTkLabel(
            quote, text=self.symbol.get(), width=72, height=28,
            font=("Segoe UI", 14, "bold"), fg_color="#2B3440",
            corner_radius=7, text_color=COL_TEXT, anchor="center",
        )
        self.lbl_quote_symbol.grid(row=0, column=0, sticky="w", padx=(11, 5), pady=(6, 0))
        change_box = ctk.CTkFrame(quote, fg_color="transparent")
        change_box.grid(row=0, column=1, sticky="e", padx=(5, 9), pady=(6, 0))
        self.lbl_change = ctk.CTkLabel(
            change_box, text="--", font=FONT_MONO_VALUE,
            text_color=COL_MUTED, anchor="e",
        )
        self.lbl_change.pack(side="left")
        change_hint = ctk.CTkButton(
            change_box, text="ⓘ", width=24, height=24, corner_radius=7,
            font=("Segoe UI Symbol", 12, "bold"),
            fg_color="#343A43", hover_color="#4B515B", text_color=COL_TEXT,
        )
        change_hint.pack(side="left", padx=(7, 0))
        _HoverHint(
            change_hint,
            "Mức tăng hoặc giảm so với giá đóng cửa phiên trước. "
            "Mốc giá và dữ liệu hiện tại đều lấy từ DNSE; đây không phải giá đặt lệnh.",
        )
        self.lbl_price = ctk.CTkLabel(
            quote, text="---", font=("Cascadia Mono", 36, "bold"),
            text_color=COL_TEXT, anchor="center",
        )
        self.lbl_price.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 0))
        self.lbl_market = ctk.CTkLabel(
            quote, text="CHỜ DỮ LIỆU", font=("Segoe UI", 11, "bold"),
            text_color=COL_WARN, anchor="center",
        )
        self.lbl_market.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))

        preview = ctk.CTkFrame(quote, fg_color="transparent")
        preview.grid(row=3, column=0, columnspan=2, sticky="ew", padx=7, pady=(0, 6))
        preview.grid_columnconfigure(0, weight=1)

        pnl_panel = ctk.CTkFrame(preview, fg_color="#1B1F25", corner_radius=7)
        pnl_panel.grid(row=0, column=0, sticky="ew")
        pnl_panel.grid_columnconfigure((0, 1, 2), weight=1, uniform="pnl_preview")
        pnl_specs = (
            ("TP", "lbl_tp_title", "lbl_tp_preview", COL_GREEN),
            ("SL", "lbl_sl_title", "lbl_sl_preview", COL_RED),
            ("FEE", "lbl_fee_title", "lbl_fee_preview", COL_WARN),
        )
        pnl_hints = {
            "TP": "Lãi ước tính trước phí/thuế theo giá vào và khối lượng đang nhập hoặc AUTO.\n"
                  "100 CP × 20.000 đồng, TP +7% → +140.000 đồng. Chỉ preview, không phải lãi chắc chắn.",
            "SL": "Lỗ ước tính trước phí/thuế theo giá vào và khối lượng đang nhập hoặc AUTO.\n"
                  "100 CP × 20.000 đồng, SL −3,5% → −70.000 đồng; không bảo đảm khớp đúng giá SL.",
            "FEE": "Phí mua ước tính của khối lượng đang nhập hoặc AUTO, không gồm phí/thuế bán.\n"
                   "AUTO tính từ tiền/danh mục đúng sổ REAL/PAPER và P1/OVERRIDE; không cần có tín hiệu BUY.",
        }
        for column, (title, title_attr, value_attr, title_color) in enumerate(pnl_specs):
            box = ctk.CTkFrame(pnl_panel, fg_color="transparent")
            box.grid(row=0, column=column, sticky="nsew", padx=5, pady=5)
            title_label = ctk.CTkLabel(
                box, text=title, font=FONT_PREVIEW_TITLE, height=22,
                text_color=title_color, anchor="center",
            )
            title_label.pack(fill="x")
            _HoverHint(title_label, pnl_hints[title])
            label = ctk.CTkLabel(
                box, text="NA", font=FONT_PREVIEW_VALUE,
                text_color=COL_PREVIEW_TEXT, anchor="center", height=28,
            )
            label.pack(fill="x")
            setattr(self, title_attr, title_label)
            setattr(self, value_attr, label)

        money_panel = ctk.CTkFrame(preview, fg_color="#1B1F25", corner_radius=7)
        money_panel.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        money_panel.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            money_panel, text="TIỀN CK", font=FONT_PREVIEW_TITLE, height=28,
            text_color=COL_PREVIEW_TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(12, 6), pady=2)
        self.lbl_order_value = ctk.CTkLabel(
            money_panel, text="NA", font=FONT_PREVIEW_VALUE, height=28,
            text_color=COL_PREVIEW_TEXT, anchor="e",
        )
        self.lbl_order_value.grid(row=0, column=1, sticky="e", padx=(6, 12), pady=2)

        action = ctk.CTkFrame(order, width=1, height=38, fg_color="transparent")
        action.pack(fill="x", padx=9, pady=(1, 4))
        action.grid_propagate(False)
        action.grid_columnconfigure(1, weight=1)
        self.order_type = tk.StringVar(value="MARKET")
        self.order_type_menu = ctk.CTkOptionMenu(
            action, values=["MARKET", "LO", "ATO", "ATC"],
            variable=self.order_type, command=self._order_type_changed,
            width=115, height=38, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GRAY, button_color="#4B515B",
            button_hover_color="#59616D", corner_radius=8,
            dynamic_resizing=False,
        )
        self.order_type_menu.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.execute_button = ctk.CTkButton(
            action, text="CACHE", font=("Segoe UI", 13, "bold"),
            height=38, fg_color="#16A34A", hover_color="#15803D",
            border_width=1, border_color=COL_GREEN,
            corner_radius=8, command=lambda: self._submit("BUY"),
        )
        self.execute_button.grid(row=0, column=1, sticky="ew")
        action_hint = ctk.CTkButton(
            action, text="ⓘ", width=30, height=38, corner_radius=8,
            font=("Segoe UI Symbol", 13, "bold"),
            fg_color="#343A43", hover_color="#4B515B", text_color=COL_TEXT,
        )
        action_hint.grid(row=0, column=2, padx=(7, 0))
        _HoverHint(
            action_hint,
            "ĐẶT (xanh): gửi lệnh ngay vì đang đúng phiên và đã đủ điều kiện.\n"
            "CACHE (vàng): lưu lệnh trong máy, tự gửi khi đúng phiên hoặc khi có token.\n"
            "KIỂM TRA (đỏ): dữ liệu nhập chưa hợp lệ, chưa tạo lệnh.",
        )

        form = ctk.CTkFrame(order, fg_color="transparent")
        form.pack(fill="x", padx=8, pady=(1, 5))
        form.grid_columnconfigure((0, 1), weight=1, uniform="manual_input")
        self.quantity = self._manual_input(
            form, 0, "KHỐI LƯỢNG", "", placeholder="AUTO", row=0,
        )
        self.price = self._manual_input(form, 1, "GIÁ LO", "", row=0)
        self.tp = self._manual_input(
            form, 0, "TAKE PROFIT", self._default_tp_text(), dimmed=True, row=1,
        )
        self.sl = self._manual_input(
            form, 1, "STOP LOSS", "-3%", dimmed=True, row=1,
        )
        self._sl_manual_override = False
        entries = (self.quantity, self.price, self.tp)
        for entry in entries:
            entry.bind("<KeyRelease>", lambda _event: self._update_order_preview(), add="+")
            entry.bind("<KeyPress>", lambda _event, widget=entry: widget.configure(text_color=COL_TEXT), add="+")
        self.sl.bind("<KeyPress>", lambda _event: self.sl.configure(text_color=COL_TEXT), add="+")
        self.price.bind("<FocusIn>", self._activate_lo_input, add="+")
        self.sl.bind("<KeyRelease>", self._sl_edited, add="+")
        self.quantity.bind(
            "<Tab>",
            lambda _event: self._focus_widget(
                self.price if self.order_type.get() == "LO" else self.tp
            ),
        )
        self.price.bind("<Tab>", lambda _event: self._focus_widget(self.tp))
        self.tp.bind("<Tab>", lambda _event: self._focus_widget(self.sl))
        self.sl.bind("<Tab>", lambda _event: self._focus_widget(self.execute_button))
        self.execute_button.bind("<Tab>", lambda _event: self._focus_widget(self.quantity))
        self.quantity.bind("<Shift-Tab>", lambda _event: self._focus_widget(self.execute_button))
        self.price.bind("<Shift-Tab>", lambda _event: self._focus_widget(self.quantity))
        self.tp.bind(
            "<Shift-Tab>",
            lambda _event: self._focus_widget(
                self.price if self.order_type.get() == "LO" else self.quantity
            ),
        )
        self.sl.bind("<Shift-Tab>", lambda _event: self._focus_widget(self.tp))
        self.execute_button.bind("<Shift-Tab>", lambda _event: self._focus_widget(self.sl))
        self.execute_button.bind("<Return>", lambda _event: self.execute_button.invoke())
        self.execute_button.bind("<space>", lambda _event: self.execute_button.invoke())

        self._current_tick_price = 0.0
        self._current_tick: dict[str, Any] = {}
        self._current_market_status = ""
        self._sync_default_sl(force=True, refresh=False)
        self._update_order_preview()

        brand = ctk.CTkFrame(self.left, fg_color="transparent")
        brand.pack(fill="x", padx=6, pady=(1, 0))
        ctk.CTkLabel(
            brand, text="VIKING-beta", font=("Segoe UI", 19, "bold"),
            text_color="#A78BFA",
        ).pack()

    @staticmethod
    def _manual_input(
        frame: ctk.CTkFrame,
        column: int,
        label: str,
        value: str,
        disabled: bool = False,
        *,
        placeholder: str = "",
        dimmed: bool = False,
        row: int = 0,
    ) -> ctk.CTkEntry:
        box = ctk.CTkFrame(
            frame, width=1, fg_color=COL_SURFACE_2, corner_radius=8,
            border_width=1, border_color=COL_BORDER,
        )
        box.grid_columnconfigure(1, weight=1)
        box.grid(
            row=row, column=column,
            padx=(0 if column == 0 else 4, 0), pady=(0, 4), sticky="ew",
        )
        ctk.CTkLabel(
            box, text=label, width=96, font=FONT_KEY,
            text_color=COL_TITLE, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(8, 2), pady=4)
        entry = ctk.CTkEntry(
            box, width=1, font=FONT_MONO_VALUE, height=32,
            justify="right", fg_color="#1A1E23", border_width=0,
            text_color=COL_MUTED if dimmed else COL_TEXT,
            placeholder_text=placeholder,
            placeholder_text_color=COL_MUTED,
            takefocus=True,
        )
        entry.insert(0, value)
        entry.grid(row=0, column=1, sticky="ew", padx=(2, 5), pady=4)

        def fit_current_text(_event: Any = None) -> None:
            shown = str(entry.get() or entry.cget("placeholder_text") or "")
            fit_entry_text(entry, shown)

        entry.bind("<Configure>", fit_current_text, add="+")
        entry.bind("<KeyRelease>", fit_current_text, add="+")
        entry._viking_fit_text = fit_current_text
        entry.after_idle(fit_current_text)
        if disabled:
            entry.configure(state="disabled")
        return entry

    def _right_panel(self) -> None:
        header = ctk.CTkFrame(self.right, fg_color="transparent", height=36)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.table_title = ctk.CTkLabel(
            header, text="LỆNH ĐANG CHẠY", font=("Segoe UI", 17, "bold"),
            text_color=COL_TITLE,
        )
        self.table_title.pack(side="left")
        self.running_legend_button = ctk.CTkButton(
            header, text="ⓘ", width=28, height=28,
            font=("Segoe UI Symbol", 13, "bold"),
            fg_color="#2A2E34", hover_color="#4B515B",
            corner_radius=8, command=self._show_running_legend,
        )
        self.running_legend_button.pack(side="left", padx=(6, 0))
        self.info_button = ctk.CTkButton(
            header, text="▤ INFO", width=68, height=31, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=8,
            command=self._open_info_popup,
        )
        self.info_button.pack(side="left", padx=(6, 0))
        self.history_button = ctk.CTkButton(
            header, text="◷ LỊCH SỬ", width=88, height=31, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=8,
            command=self._open_history_popup,
        )
        self.history_button.pack(side="right")
        self.portfolio_button = ctk.CTkButton(
            header, text="▦ DANH MỤC", width=100, height=31, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=8,
            command=self._open_portfolio_popup,
        )
        self.portfolio_button.pack(side="right", padx=5)
        self.backtest_button = ctk.CTkButton(
            header, text="◫ BACKTEST", width=94, height=31, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=8,
            command=self._open_backtest_popup,
        )
        self.backtest_button.pack(side="right")
        self.cancel_button = ctk.CTkButton(
            header, text="✕ HỦY ĐÃ CHỌN", width=112, height=31, font=("Segoe UI", 11, "bold"),
            fg_color="#2A2E34", hover_color="#D97706", corner_radius=8,
            state="disabled",
            command=self._cancel_selected
        )
        self.cancel_button.pack(side="right", padx=(0, 5))

        self.tabs = ctk.CTkTabview(
            self.right, command=self._render_tables,
            fg_color=COL_SURFACE, border_width=1, border_color=COL_BORDER,
            segmented_button_fg_color=COL_SURFACE_2,
            segmented_button_selected_color=COL_GREEN,
            segmented_button_selected_hover_color="#16A34A",
            segmented_button_unselected_color=COL_GRAY,
            segmented_button_unselected_hover_color="#4B515B",
        )
        self.tabs.grid(row=1, column=0, sticky="nsew")
        self.trees: dict[str, ttk.Treeview] = {}
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Running.Treeview", background=COL_SURFACE_2, foreground=COL_TEXT,
            fieldbackground=COL_SURFACE_2, rowheight=48, font=("Segoe UI", 14),
            borderwidth=0, relief="flat", bordercolor=COL_BORDER,
            lightcolor=COL_BORDER, darkcolor=COL_BORDER,
        )
        style.configure(
            "Running.Treeview.Heading", background=COL_SURFACE, foreground=COL_TITLE,
            font=("Segoe UI", 14, "bold"), relief="flat", padding=(9, 9),
        )
        style.map(
            "Running.Treeview.Heading",
            background=[("active", "#252A32"), ("pressed", "#252A32")],
            foreground=[("active", COL_TITLE), ("pressed", COL_TITLE)],
        )
        style.map(
            "Running.Treeview",
            background=[("selected", "#5A471A")],
            foreground=[("selected", "#FFF3B0")],
        )
        for mode in ("CKCS REAL", "CKCS PAPER"):
            frame = self.tabs.add(mode)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            tree = ttk.Treeview(frame, show="headings", selectmode="extended", style="Running.Treeview")
            tree.tag_configure("buy_row", background="#193524", foreground=COL_TEXT)
            tree.tag_configure("sell_row", background="#3A2024", foreground=COL_TEXT)
            tree.tag_configure("pending_order", background="#42351B", foreground="#FDE68A")
            tree.tag_configure("settlement_order", background=COL_SETTLEMENT_BG, foreground=COL_SETTLEMENT_TEXT)
            tree.tag_configure("position_profit", background="#193524", foreground="#E7F8ED")
            tree.tag_configure("position_loss", background="#3A2024", foreground="#FBEAEC")
            tree.tag_configure("position_flat", background=COL_SURFACE_2, foreground=COL_TEXT)
            tree.tag_configure("position_waiting", background=COL_SETTLEMENT_BG, foreground=COL_SETTLEMENT_TEXT)
            tree.tag_configure("position_closing", background="#5A4214", foreground="#FFE0B2")
            tree.tag_configure("dnse_order", background="#123F6B", foreground="#D7ECFF")
            tree.tag_configure("partial_order", background="#6A3F08", foreground="#FFE0B2")
            tree.tag_configure("sending_order", background="#0B4F5C", foreground="#B2EBF2")
            tree.tag_configure("error_order", background="#5A1E1E", foreground="#FFCDD2")
            tree.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
            tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_cancel_button())
            tree.bind("<Button-1>", self._clear_running_selection_on_blank, add="+")
            tree.bind("<ButtonRelease-1>", self._running_action_click, add="+")
            tree.bind("<Escape>", lambda _event, widget=tree: self._clear_running_selection(widget), add="+")
            tree.bind("<Button-3>", self._running_right_click)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            scroll_x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            scroll_x.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=scroll.set, xscrollcommand=scroll_x.set)
            self.trees["REAL" if "REAL" in mode else "PAPER"] = tree
        self.tabs.set("CKCS PAPER" if self.settings.paper_mode else "CKCS REAL")
        try:
            self.tabs._segmented_button.configure(font=("Segoe UI", 12, "bold"))
        except AttributeError:
            pass

        log_frame = ctk.CTkFrame(
            self.right, height=360, fg_color=COL_SURFACE, corner_radius=10,
            border_width=1, border_color=COL_BORDER,
        )
        self.info_panel = log_frame
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        log_frame.grid_propagate(False)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        info_header = ctk.CTkFrame(log_frame, height=34, fg_color="transparent")
        info_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))
        info_header.grid_propagate(False)
        info_header.grid_columnconfigure(0, weight=1, uniform="info_header_side")
        info_header.grid_columnconfigure(1, weight=0)
        info_header.grid_columnconfigure(2, weight=1, uniform="info_header_side")
        ctk.CTkLabel(
            info_header, text="HỆ THỐNG", font=("Segoe UI", 14, "bold"),
            text_color=COL_TITLE, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(2, 12))
        self.info_tab_selector = ctk.CTkSegmentedButton(
            info_header, values=["PREVIEW", "Manual", "Bot"],
            width=300, height=28, dynamic_resizing=False,
            font=("Segoe UI", 11, "bold"),
            fg_color=COL_SURFACE_2,
            selected_color="#2563A6",
            selected_hover_color="#2E73BB",
            unselected_color=COL_GRAY,
            unselected_hover_color="#4B515B",
            command=self._select_info_tab,
        )
        self.info_tab_selector.grid(row=0, column=1)
        self._info_collapsed = False
        self.info_collapse_button = ctk.CTkButton(
            info_header,
            text="⌃",
            width=32,
            height=28,
            corner_radius=6,
            font=("Segoe UI Symbol", 14, "bold"),
            fg_color=COL_GRAY,
            hover_color="#4B515B",
            text_color=COL_TEXT,
            command=self._toggle_info_panel,
        )
        self.info_collapse_button.grid(row=0, column=2, sticky="e", padx=(8, 2))
        _HoverHint(
            self.info_collapse_button,
            "Thu gọn hoặc mở rộng khối HỆ THỐNG để nhường chỗ cho bảng lệnh.",
            placement="inside",
        )
        self.log_tabview = ctk.CTkTabview(
            log_frame, fg_color="#111318", corner_radius=0, border_width=0,
            segmented_button_fg_color=COL_SURFACE_2,
            segmented_button_selected_color="#2563A6",
            segmented_button_selected_hover_color="#2E73BB",
            segmented_button_unselected_color=COL_GRAY,
            segmented_button_unselected_hover_color="#4B515B",
            command=self._on_log_tab_change,
        )
        self.log_tabview.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.log_tab_keys = {"manual": "Manual", "bot": "Bot"}
        self.log_tab_unread = {"manual": False, "bot": False}

        preview_tab = self.log_tabview.add("PREVIEW")
        manual_tab = self.log_tabview.add("Manual")
        bot_tab = self.log_tabview.add("Bot")
        # The app owns the compact header above; collapse CTkTabview's duplicate
        # header so the page receives all remaining height and width.
        self.log_tabview._outer_spacing = 0
        self.log_tabview._outer_button_overhang = 0
        self.log_tabview._button_height = 0
        self.log_tabview._configure_grid()
        self.log_tabview._segmented_button.grid_forget()
        preview_tab.grid_columnconfigure(0, weight=1)
        preview_tab.grid_rowconfigure(0, weight=1)
        self.preview_scroll = ctk.CTkScrollableFrame(
            preview_tab,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_fg_color="#111318",
            scrollbar_button_color="#343A43",
            scrollbar_button_hover_color="#4B515B",
        )
        self.preview_scroll.grid(row=0, column=0, sticky="nsew")
        self.preview_scroll.grid_columnconfigure(0, weight=1)
        self._build_order_preview_tab(self.preview_scroll)

        for frame, target in ((manual_tab, "manual"), (bot_tab, "bot")):
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            log_shell = ctk.CTkFrame(frame, fg_color=COL_SURFACE_2, corner_radius=7)
            log_shell.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
            log_shell.grid_columnconfigure(0, weight=1)
            log_shell.grid_rowconfigure(0, weight=1)
            text = ctk.CTkTextbox(
                log_shell, font=("Cascadia Mono", 12), wrap="word",
                fg_color=COL_SURFACE_2, text_color=COL_TEXT,
                border_width=0, corner_radius=7, activate_scrollbars=False,
            )
            text.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
            scrollbar = ctk.CTkScrollbar(
                log_shell,
                width=10,
                fg_color=COL_SURFACE_2,
                button_color="#343A43",
                button_hover_color="#4B515B",
                command=text.yview,
            )
            scrollbar.grid(row=0, column=1, sticky="ns", padx=(3, 4), pady=6)
            text._textbox.configure(yscrollcommand=scrollbar.set)
            setattr(self, f"log_{target}", text)
            setattr(self, f"log_{target}_scrollbar", scrollbar)
        self.log_bot.insert(
            "end", "Mua tự động khởi động OFF; vị thế đang giữ vẫn được quản lý.\n"
        )
        self.log_tabview.set("PREVIEW")
        self.info_tab_selector.set("PREVIEW")
        self._refresh_full_order_preview()
        self._refresh_api_health_panel(self.bridge.read_status())
        self.after_idle(self._sync_preview_scrollbar)

    def _build_order_preview_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=0)
        # Keep the original compact height; explanations belong in hover hints.
        panel = ctk.CTkFrame(parent, height=300, fg_color=COL_SURFACE_2, corner_radius=8)
        panel.grid(row=0, column=0, sticky="ew")
        panel.grid_propagate(False)
        self.preview_focus_panel = panel
        panel.grid_columnconfigure(0, weight=3, uniform="preview_groups")
        panel.grid_columnconfigure(1, weight=2, uniform="preview_groups")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=0)

        order_group = ctk.CTkFrame(panel, width=1, fg_color=COL_SURFACE, corner_radius=7)
        order_group.grid(row=0, column=0, sticky="nsew", padx=(7, 3), pady=(7, 3))
        order_group.grid_propagate(False)
        rule_group = ctk.CTkFrame(panel, width=1, fg_color=COL_SURFACE, corner_radius=7)
        rule_group.grid(row=0, column=1, sticky="nsew", padx=(3, 7), pady=(7, 3))
        rule_group.grid_propagate(False)

        order_group.grid_columnconfigure(0, weight=1)
        order_group.grid_rowconfigure(1, weight=0)
        order_group.grid_rowconfigure(2, weight=1)
        order_header = ctk.CTkFrame(order_group, width=1, height=32, fg_color="transparent")
        order_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 1))
        order_header.grid_columnconfigure(0, weight=0, minsize=230)
        order_header.grid_columnconfigure(1, weight=1)
        order_header.grid_columnconfigure(2, weight=0)
        self.preview_order_title = ctk.CTkLabel(
            order_header, text="--- · PAPER · BUY · MARKET",
            width=230, height=22, font=("Segoe UI", 12, "bold"), text_color=COL_TITLE, anchor="w",
        )
        self.preview_order_title.grid(
            row=0, column=0, sticky="ew", padx=(2, 5)
        )
        self.preview_status_reason = ctk.CTkLabel(
            order_header, text="--", width=1, height=22, font=("Segoe UI", 11),
            text_color=COL_PREVIEW_TEXT, fg_color=COL_SURFACE_2, corner_radius=5,
            anchor="w", justify="left",
        )
        self.preview_status_reason.grid(
            row=0, column=1, sticky="ew", padx=5
        )
        self.preview_status_badge = ctk.CTkLabel(
            order_header, text="CHỜ", width=82, height=24,
            font=("Segoe UI", 10, "bold"), fg_color="#4A3B16",
            text_color="#FFF3B0", corner_radius=6,
        )
        self.preview_status_badge.grid(
            row=0, column=2, sticky="e", padx=(5, 0)
        )

        metrics = ctk.CTkFrame(order_group, height=76, fg_color="transparent")
        metrics.grid(row=1, column=0, sticky="ew", padx=6, pady=(3, 2))
        metrics.grid_propagate(False)
        for column in range(6):
            metrics.grid_columnconfigure(column, weight=1, uniform="preview_metrics")

        def metric_card(
            column: int,
            title: str,
            title_color: str = COL_TITLE,
            hint: str = "",
        ):
            card = ctk.CTkFrame(metrics, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
            card.grid_columnconfigure(0, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=f"{title}{'  ⓘ' if hint else ''}", height=22,
                font=("Segoe UI", 12, "bold", "italic"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=9, pady=(6, 0))
            if title == "KL":
                self.preview_qty_title = title_widget
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            value = ctk.CTkLabel(
                card, text="NA", width=1, height=30,
                font=("Cascadia Mono", 13), text_color=COL_TEXT,
                anchor="w", justify="left", wraplength=190,
            )
            value.grid(row=1, column=0, sticky="ew", padx=9, pady=(1, 6))
            return value

        self.preview_live_value = metric_card(0, "GIÁ TT", hint="Giá gần nhất bot nhận được; màu vàng là giá đang đứng hoặc ngoài phiên. Không bảo đảm đây là giá khớp.")
        self.preview_entry_value = metric_card(1, "GIÁ VÀO", hint="Giá dùng để ước tính lệnh. LO dùng giá nhập; MARKET dùng giá hiện tại, giá khớp do sàn quyết định.")
        self.preview_qty_value = metric_card(2, "KL", hint="AUTO tính từ vốn của đúng sổ REAL/PAPER, làm tròn lô 100. Nhập số lượng để đặt tay.\nVí dụ đủ tiền 450 CP → AUTO 400 CP.")
        self.preview_cash_value = metric_card(3, "TIỀN CK", hint="Giá vào × khối lượng, chưa gồm phí mua; không phải tiền khả dụng tài khoản.\n100 CP × 32.150 đồng = 3.215.000 đồng.")
        self.preview_fee_value = metric_card(4, "FEE", COL_WARN, "Phí mua ước tính, không gồm phí/thuế bán. Thiếu giá hoặc số lượng → —, không phải miễn phí.")
        self.preview_route_value = metric_card(
            5,
            "LỆNH",
            "#60A5FA",
            "ĐẶT: lệnh đủ điều kiện gửi trong phiên hiện tại.\n"
            "CACHE: giữ local, chờ đúng phiên hoặc OTP.",
        )

        management = ctk.CTkFrame(order_group, height=84, fg_color="transparent")
        management.grid(row=2, column=0, sticky="nsew", padx=6, pady=(3, 6))
        management.grid_propagate(False)
        management.grid_rowconfigure((0, 1), weight=1, uniform="preview_management_rows")
        for column in range(3):
            management.grid_columnconfigure(column, weight=1, uniform="preview_management")

        def level_card(
            row: int,
            column: int,
            title: str,
            title_color: str,
            *,
            columnspan: int = 1,
        ):
            card = ctk.CTkFrame(management, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(
                row=row, column=column, columnspan=columnspan,
                sticky="nsew", padx=3, pady=3,
            )
            card.grid_columnconfigure(0, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=title, width=1, height=16, font=("Segoe UI", 11, "bold", "italic"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=9, pady=(3, 0))
            value = ctk.CTkLabel(
                card, text="NA", width=1, height=18, font=("Cascadia Mono", 12),
                text_color=title_color, anchor="w",
            )
            value.grid(row=1, column=0, sticky="ew", padx=9, pady=(1, 0))
            detail = ctk.CTkLabel(
                card, text="", width=1, height=14, font=("Segoe UI", 10),
                text_color=COL_PREVIEW_TEXT, anchor="w",
            )
            detail.grid(row=2, column=0, sticky="ew", padx=9, pady=(0, 2))
            return title_widget, value, detail

        _tp_title, self.preview_tp_value, self.preview_tp_detail = level_card(0, 0, "TP MANUAL", COL_GREEN)
        _sl_title, self.preview_sl_value, self.preview_sl_detail = level_card(0, 1, "STOP LOSS", COL_RED)
        _HoverHint(_tp_title, "Mốc TP của lệnh MANUAL đang nhập, không phải số tiền lãi chắc chắn.\nVí dụ mua 100, TP +7% → giá kích hoạt 107; khớp thực tế và phí quyết định lãi ròng.")
        _HoverHint(_sl_title, "SL tính từ giá vốn khi vị thế có bật SL; kích hoạt bán 100% phần còn lại.\nMua 100, SL −3,5% → giá kích hoạt 96,5; không bảo đảm khớp đúng giá đó.")
        atr_title, self.preview_atr, self.preview_atr_detail = level_card(
            0, 2, "ATR14 · 1D", "#60A5FA",
        )
        _HoverHint(
            atr_title,
            self._atr_preview_hint,
            placement="inside",
        )
        self.preview_em_normal, self.preview_normal_value, self.preview_normal_detail = level_card(
            1, 0, "PROTECT · OFF", COL_RED, columnspan=2,
        )
        self.preview_em_exit, self.preview_exit_value, self.preview_exit_detail = level_card(
            1, 2, "E · OFF", COL_RED,
        )
        _HoverHint(self.preview_em_normal, "PROTECT theo đỉnh, không phải TP cố định. Dynamic là bảo vệ trước ARM.\nAUTO bán theo % đã đặt; ALERT chỉ ghi nhận. Các con số ở đây là preview, chưa có vị thế thì chưa có đỉnh thật.")
        _HoverHint(self.preview_em_exit, "E dùng EMA SELL/RSI để thoát. AUTO bán 100% phần còn lại; ALERT không đặt lệnh.\nE độc lập với ARM/PROTECT; OFF là chưa gắn E cho lệnh MANUAL này.")
        _HoverHint(self.preview_status_reason, lambda: self.preview_status_reason.cget("text"), placement="inside")

        rule_group.grid_columnconfigure(0, weight=1)
        for row in (1, 2, 3, 4):
            rule_group.grid_rowconfigure(row, weight=0)
        rule_header = ctk.CTkFrame(rule_group, height=18, fg_color="transparent")
        rule_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))
        rule_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            rule_header, text="QUYẾT ĐỊNH HỆ THỐNG", height=14, font=("Segoe UI", 13, "bold"),
            text_color="#60A5FA", anchor="w",
        ).grid(row=0, column=0, sticky="w")
        rule_hint = ctk.CTkButton(
            rule_header, text="ⓘ", width=22, height=20, corner_radius=6,
            font=("Segoe UI Symbol", 11, "bold"),
            fg_color="#343A43", hover_color="#4B515B", text_color=COL_TEXT,
        )
        rule_hint.grid(row=0, column=1, sticky="e", padx=(4, 5))
        _HoverHint(
            rule_hint,
            "P1: trạng thái VNINDEX từ dữ liệu 1D DNSE.\n"
            "P2: tín hiệu BUY/SELL từ các chỉ báo EMA/RSI đang bật.\n"
            "P3: vốn, số position và khóa bảo vệ.\n"
            "SL, TP, PROTECT và E bật/tắt theo từng vị thế; OFF chỉ chặn BUY BOT mới, vẫn quản lý vị thế.",
        )
        self.preview_rule_title = ctk.CTkLabel(
            rule_header, text="MUA · OFF", width=72, height=16,
            font=("Segoe UI", 11, "bold"), text_color=COL_RED,
            fg_color=COL_SURFACE_2, corner_radius=5,
        )
        self.preview_rule_title.grid(row=0, column=2, sticky="e")

        def phase_card(row: int, title: str, hint: str = ""):
            card = ctk.CTkFrame(rule_group, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=row, column=0, sticky="nsew", padx=8, pady=2)
            card.grid_columnconfigure(0, weight=0)
            card.grid_columnconfigure(1, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=f"{title}{'  ⓘ' if hint else ''}", height=18, font=("Segoe UI", 12, "bold", "italic"),
                text_color="#60A5FA", anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="w", padx=(8, 6), pady=4)
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            return card

        phase1 = phase_card(1, "P1 · VNINDEX", self._market_confirmation_hint)
        self.preview_rule_market = ctk.CTkLabel(
            phase1, text="VNINDEX --", width=1, height=16,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=300,
        )
        self.preview_rule_market.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=4)
        _HoverHint(self.preview_rule_market, self._market_confirmation_hint, placement="inside")
        self.preview_rule_market_detail = ctk.CTkLabel(
            phase1, text="CHỜ PHÂN LOẠI", height=14, font=("Segoe UI", 10),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_market_detail.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.preview_rule_market_detail.grid_remove()
        _HoverHint(self.preview_rule_market_detail, self._market_confirmation_hint, placement="inside")

        phase2 = phase_card(
            2,
            "P2 · BUY / E",
            self._indicator_preview_hint,
        )
        self.preview_rule_ema = ctk.CTkLabel(
            phase2, text="BUY EMA 3/6 · --/--", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=300,
        )
        self.preview_rule_ema.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=0
        )
        self.preview_rule_sell_ema = ctk.CTkLabel(
            phase2, text="SELL EMA 3/6 · --/--", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=300,
        )
        self.preview_rule_sell_ema.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=0
        )
        self.preview_rule_rsi = ctk.CTkLabel(
            phase2, text="RSI14 -- · WAIT", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=300,
        )
        self.preview_rule_rsi.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 3)
        )

        phase3 = phase_card(
            3,
            "P3 · VỐN & KHÓA",
            self._entry_capital_hint,
        )
        self.preview_rule_phase3 = ctk.CTkLabel(
            phase3, text="--/-- · --/MÃ", width=1, height=18, font=("Cascadia Mono", 11),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3.grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=4
        )
        _HoverHint(self.preview_rule_phase3, self._entry_capital_hint, placement="inside")
        self.preview_rule_phase3_detail = ctk.CTkLabel(
            phase3, text="AUTO --", height=16, font=("Segoe UI", 12),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3_detail.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 4)
        )
        self.preview_rule_phase3_guard = ctk.CTkLabel(
            phase3, text="WHIPSAW -- · LOSS --", font=("Segoe UI", 11),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3_guard.grid_remove()

        self.preview_rule_reason = ctk.CTkLabel(
            rule_group, text="CHỜ DỮ LIỆU", width=1, height=22,
            font=("Segoe UI", 9, "bold"), text_color=COL_WARN,
            fg_color=COL_SURFACE_2, corner_radius=6,
            anchor="w", justify="left", wraplength=300,
        )
        self.preview_rule_reason.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 6))

        health_group = ctk.CTkFrame(
            panel, fg_color=COL_SURFACE, corner_radius=7, height=36,
            border_width=1, border_color=COL_BORDER,
        )
        health_group.grid(row=1, column=0, columnspan=2, sticky="ew", padx=7, pady=(3, 7))
        health_group.grid_propagate(False)
        for column in range(7):
            health_group.grid_columnconfigure(column, weight=1, uniform="health_pills")
        health_title = ctk.CTkFrame(
            health_group, width=1, height=24, fg_color=COL_SURFACE_2, corner_radius=5,
        )
        health_title.grid(row=0, column=0, sticky="ew", padx=3, pady=5)
        health_title.grid_propagate(False)
        health_title.grid_columnconfigure(0, weight=1)
        self.preview_health_title = ctk.CTkLabel(
            health_title, text="HEALTH", width=1,
            height=20,
            font=("Segoe UI", 10, "bold", "italic"),
            text_color="#60A5FA", anchor="w",
        )
        self.preview_health_title.grid(row=0, column=0, sticky="ew", padx=(8, 1))
        self.preview_health_hint = ctk.CTkLabel(
            health_title, text="ⓘ", width=20, height=20,
            font=("Segoe UI Symbol", 10, "bold"), text_color=COL_TEXT,
            fg_color="#343A43", corner_radius=5,
        )
        self.preview_health_hint.grid(row=0, column=1, sticky="e", padx=(1, 3), pady=2)
        _HoverHint(
            self.preview_health_hint,
            "API OK: DNSE đang trả lời bình thường.\n"
            "OTP: chưa xác thực trading token; chỉ cần khi gửi, sửa hoặc hủy lệnh REAL.\n"
            "PRICE: ATO / OPEN / ATC / CLOSED theo phiên hiện tại.",
            placement="inside",
        )

        def health_cell(column: int, text: str) -> ctk.CTkLabel:
            label = ctk.CTkLabel(
                health_group, text=text, width=1, height=24,
                font=("Cascadia Mono", 11), text_color=COL_PREVIEW_TEXT,
                fg_color=COL_SURFACE_2, corner_radius=5,
            )
            label.grid(row=0, column=column, sticky="ew", padx=3, pady=5)
            return label

        self.preview_health_daemon = health_cell(1, "DAEMON --")
        self.preview_health_core = health_cell(2, "DNSE --")
        self.preview_health_ws = health_cell(3, "WS --")
        self.preview_health_rest = health_cell(4, "REST --")
        self.preview_health_token = health_cell(5, "TOKEN --")
        self.preview_health_trade = health_cell(6, "GIÁ --")

    @staticmethod
    def _preview_level_details(
        raw: str,
        entry_price: float,
        quantity: int,
        preview_capital: float = 0.0,
    ) -> tuple[str, float | None]:
        text = str(raw or "").strip().replace(",", "")
        if not text or entry_price <= 0:
            return "NA", None
        try:
            if text.endswith("%"):
                percent = float(text[:-1])
                target = entry_price * (1.0 + percent / 100.0)
                capital = (
                    entry_price * quantity * 1000.0
                    if quantity > 0 else max(0.0, preview_capital)
                )
                pnl = capital * percent / 100.0 if capital > 0 else None
                return f"{percent:+g}% · {_display_price(target)}", pnl
            target = _price_unit(float(text))
            capital = (
                entry_price * quantity * 1000.0
                if quantity > 0 else max(0.0, preview_capital)
            )
            pnl = (
                capital * (target - entry_price) / entry_price
                if capital > 0 and entry_price > 0 else None
            )
            return _display_price(target), pnl
        except ValueError:
            return "KHÔNG HỢP LỆ", None

    def _suggested_order_quantity(
        self,
        entry_price: float,
        status: dict[str, Any] | None = None,
        symbol: str | None = None,
    ) -> tuple[int, float, bool]:
        """Return quantity, target budget and whether the minimum-lot fallback was used."""
        status = self._book_preview_status(status)
        target = str(symbol or self.symbol.get() or "").strip().upper()
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(target) if isinstance(decisions.get(target), dict) else {}
        details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        if details.get("updated_at") and not decision_is_fresh(decision, target, self.mode.get()):
            details = {}
        checks = details.get("entry_checks") if isinstance(details.get("entry_checks"), dict) else {}
        if hasattr(self, "_preview_entry_checks"):
            current = self._preview_entry_checks(target, status)
            if current is not None:
                checks = current
        budget = _number(checks.get("order_budget", checks.get("available_capital")))
        available_cash = _number(checks.get("available_cash"))
        nav = _number(checks.get("nav"))
        minimum_room = _number(checks.get("minimum_order_room"))
        if checks.get("priority_capital_enabled"):
            minimum_room = min(minimum_room, budget)
            order_type = self.order_type.get() if hasattr(self, "order_type") else "MARKET"
            if str(order_type).upper() != "LO":
                bound = _number(checks.get("buy_budget_price"))
                if bound <= 0:
                    return 0, budget, False
                entry_price = max(entry_price, bound)
        sizing = size_buy_order(
            budget_vnd=budget,
            price_board=entry_price,
            available_cash=available_cash,
            nav=nav,
            force_min_lot_enabled=bool(checks.get("force_min_lot_enabled", False)),
            minimum_order_room_vnd=minimum_room,
            buy_fee_rate=_number(checks.get("buy_fee_rate")),
        )
        quantity = sizing.quantity
        forced_minimum = sizing.used_minimum
        if hasattr(self, "quantity") and not self.quantity.get().strip():
            quantity_text = f"{quantity:,} CP" if quantity > 0 else "AUTO"
            self.quantity.configure(
                placeholder_text=quantity_text
            )
            fit_callback = getattr(self.quantity, "_viking_fit_text", None)
            if callable(fit_callback):
                fit_callback()
        return quantity, budget, forced_minimum

    def _book_preview_status(self, status: dict[str, Any] | None = None) -> dict[str, Any]:
        """Select the viewed book, never borrow a decision from the other book."""
        status = status if isinstance(status, dict) else self.bridge.read_status()
        mode = str(self.mode.get()).upper() if hasattr(self, "mode") else "PAPER"
        books = status.get("decisions_by_mode")
        if isinstance(books, dict):
            decisions = books.get(mode) or {}
        else:
            default_paper = getattr(getattr(self, "settings", None), "paper_mode", True)
            active = str(status.get("execution_mode") or ("PAPER" if status.get("paper_mode", default_paper) else "REAL")).upper()
            decisions = status.get("decisions", {}) if active == mode else {}
        selected = {}
        for symbol, decision in decisions.items():
            if not isinstance(decision, dict):
                continue
            details = decision.get("details") or {}
            if str(details.get("execution_mode", mode)).upper() == mode:
                selected[symbol] = decision
        return {**status, "decisions": selected}

    def _preview_indicator_details(self, status: dict[str, Any], symbol: str) -> dict[str, Any]:
        """Read-only display fallback: daily bars are not trading decisions."""
        from datetime import datetime
        from ..rules.business import StaticRuleParameters

        symbol = str(symbol or "").strip().upper()
        status = self._book_preview_status(status)
        decision = (status.get("decisions") or {}).get(symbol) or {}
        details = dict(decision.get("details") or {})
        params = StaticRuleParameters.from_dict(self.settings.rule_parameters)
        expected_periods = {
            "buy_ema_fast_period": params.buy_ema_fast, "buy_ema_slow_period": params.buy_ema_slow,
            "sell_ema_fast_period": params.sell_ema_fast, "sell_ema_slow_period": params.sell_ema_slow,
            "rsi_period": params.rsi_period,
        }
        indicators = dict(details.get("indicators") or {})
        if any(indicators.get(key, value) != value for key, value in expected_periods.items()):
            indicators = {}  # Do not show a previous settings generation.
        source = "DECISION" if indicators else "MISSING"
        asof = details.get("updated_at", "")
        rows = []
        today = market_now().date()
        if getattr(self, "_preview_bars_symbol", "") == symbol:
            for raw in getattr(self, "_preview_bars", []):
                row = dict(raw)
                try:
                    day = datetime.fromtimestamp(float(row["time"]), VN_TZ).date()
                except (KeyError, ValueError, TypeError, OSError, OverflowError):
                    continue
                if day > today:
                    continue
                if day < today:
                    row["closed"] = True  # A saved live candle closes overnight.
                rows.append(row)
        if rows:
            completed = [row for row in rows if bool(row.get("closed", True))]
            working = rows
            if self.settings.signal_mode == "REALTIME":
                tick = (status.get("ticks") or {}).get(symbol) or {}
                if (not tick.get("frozen") and quote_is_fresh(tick, symbol)
                        and (status.get("symbol_phases") or {}).get(symbol) in {"ATO", "OPEN", "ATC"}):
                    working = merge_tick_into_daily_bars(rows, tick)
            else:
                working = completed
            calculated = indicator_snapshot(
                working, params.buy_ema_fast, params.buy_ema_slow, params.rsi_period,
                sell_fast=params.sell_ema_fast, sell_slow=params.sell_ema_slow,
            )
            if not indicators or any(indicators.get(key) is None for key in (
                "buy_ema_fast", "buy_ema_slow", "sell_ema_fast", "sell_ema_slow", "rsi",
            )):
                indicators = {**calculated, **{key: value for key, value in indicators.items() if value is not None}}
                source = "DAILY_PREVIEW"
                asof = datetime.fromtimestamp(float(working[-1]["time"]), VN_TZ).strftime("%Y-%m-%d %H:%M") if working else ""
            if _number(details.get("atr14_daily_pct")) <= 0 and len(completed) >= 14:
                details["atr14_daily_pct"] = average_true_range_pct(completed)
                details["atr14_daily_asof"] = completed[-1]["time"]
        details["indicators"] = {**expected_periods, **indicators}
        atr = _number(details.get("atr14_daily_pct"))
        details["dynamic_start_pct"] = atr * params.normal_atr_activation_multiplier if params.normal_atr_activation_enabled else 0.0
        details["dynamic_trail_pct"] = atr * params.normal_atr_multiplier if params.normal_atr_trail_enabled else 0.0
        self._preview_indicator_source = {"symbol": symbol, "source": source, "asof": asof,
                                          "atr_asof": details.get("atr14_daily_asof", "")}
        return details

    def _indicator_preview_hint(self) -> str:
        preview = getattr(self, "_preview_indicator_source", {})
        source = preview.get("source", "MISSING")
        text = (
            "EMA/RSI của đúng mã đang xem, dùng chu kỳ trong RULE. CLOSED dùng nến ngày đã đóng; REALTIME dùng nến đang chạy.\n"
            "Có quyết định: hiện chỉ số bot đã dùng. Chưa có quyết định: tính preview từ nến có sẵn; không tạo tín hiệu hoặc đặt lệnh.\n"
            "Nhịp phút: khi chưa có quyết định, preview tham khảo cập nhật theo giá; quyết định bot vẫn theo nhịp đã chọn.\n"
            "EMA nhanh > chậm không tự nó là lệnh BUY: còn phải có điểm cắt và đủ điều kiện RSI/rule."
        )
        if source == "MISSING":
            return text + "\nChưa có nến cho mã này: chờ daemon tải lịch sử. Có giá tức thời không đồng nghĩa đã có nến để tính chỉ số."
        return text + f"\nNguồn: {'quyết định bot' if source == 'DECISION' else 'preview nến'} · {preview.get('symbol', '')} · {preview.get('asof', '')}."

    def _atr_preview_hint(self) -> str:
        from datetime import datetime
        preview = getattr(self, "_preview_indicator_source", {})
        asof = preview.get("atr_asof", "")
        try:
            asof = datetime.fromtimestamp(float(asof), VN_TZ).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            asof = "chưa có đủ nến"
        return (
            "ATR14 · 1D: độ biến động theo % giá đóng cửa, tính bằng cùng công thức Wilder của bot. Không dùng nến hôm nay chưa đóng.\n"
            "START = ATR × hệ số bắt đầu; LÙI = ATR × hệ số lùi trong RULE → E/M.\n"
            "Ví dụ ATR 4%, START ×0,55 → 2,2%; LÙI ×0,8 → 3,2%. Các mức chỉ có tác dụng khi bật Dynamic.\n"
            f"Phiên cuối dùng tính ATR: {asof}. Dấu -- nghĩa là chưa đủ dữ liệu, không phải ATR bằng 0."
        )

    def _market_confirmation_hint(self) -> str:
        current = getattr(self, "_preview_market_confirmation", {})
        count, required = current.get("confirmation_count", 0), current.get("confirmation_required", 3)
        state = str(current.get("candidate_state", "UNKNOWN"))
        labels = {"UPTREND": "TĂNG", "DOWNTREND": "GIẢM", "ACCUMULATION": "TÍCH LŨY", "DISTRIBUTION": "PHÂN PHỐI"}
        allocation = getattr(self, "_preview_market_budget", {})
        active = labels.get(str(allocation.get("state", "UNKNOWN")), "CHƯA XÁC NHẬN")
        active_pct = _number(allocation.get("exposure_pct"))
        headline = f"ĐANG DÙNG: {active} · CP {active_pct:g}% / TIỀN {100 - active_pct:g}%.\n"
        if current.get("override_enabled"):
            explanation = "OVERRIDE: dùng trạng thái/tỷ trọng chọn tay, không chờ phiên xác nhận.\n"
        elif current.get("confirmation_pending"):
            from ..rules.business import StaticRuleParameters
            candidate_pct = StaticRuleParameters.from_dict(self.settings.rule_parameters).exposure.get(state)
            target = f" (CP {candidate_pct * 100:g}% / TIỀN {100 - candidate_pct * 100:g}%)" if candidate_pct is not None else ""
            explanation = (
                f"ĐANG XÁC NHẬN: {labels.get(state, 'ĐANG TÍNH')} · {count}/{required} PHIÊN.\n"
                f"Cần {required} phiên liên tiếp cùng trạng thái mới đổi sang {labels.get(state, 'trạng thái mới')}{target}. "
                f"Trong lúc chờ vẫn dùng {active}; ứng viên đổi thì đếm lại.\n"
            )
        else:
            explanation = "Hiện không có trạng thái mới đang chờ xác nhận.\n"
        nav = allocation.get("nav")
        example = (
            f"{allocation.get('mode', '')}: NAV {_compact_vnd(nav)} → CP tối đa "
            f"{_compact_vnd(allocation.get('stock_limit'))}, giữ theo P1 {_compact_vnd(allocation.get('cash_reserve'))}.\n"
            if nav is not None else "Chờ snapshot tài khoản để tính số tiền theo P1.\n"
        )
        return (f"{headline}{explanation}"
                "P1 TỰ ĐỘNG = tự phân loại VNINDEX, không phải bật BUY BOT; không chờ giảm giá để mua/bán.\n"
                "CP% / TIỀN% tính trên NAV của sổ REAL/PAPER đang xem: tiền + giá trị cổ phiếu.\n"
                f"{example}"
                "Đây là giới hạn phân bổ, không phải tỷ trọng đang nắm hay ngân sách của một lệnh. "
                "Đổi P1 không tự bán để cân lại danh mục.\n"
                f"{count}/{required} đếm phiên, không đếm lần quét bot. Đổi số phiên tại RULE → P1 → Xác nhận (phiên).")

    def _entry_capital_hint(self) -> str:
        return (
            "VỐN AUTO là ngân sách mua gợi ý cho mã đang chọn, tại thời điểm preview; tiền mua cổ trước phí.\n"
            "Ví dụ NAV 50 triệu × P1 90% / tối đa 5 mã = 9 triệu/mã. "
            "Tiền, room P1, BUY chờ và no-compound còn giới hạn con số thực tế.\n"
            "Priority vốn riêng dùng hạn mức × % sử dụng, trừ vốn đang giữ/BUY chờ và phí. "
            "Lô 100 và giá dự phòng khiến tiền mua thực tế thường thấp hơn ngân sách.\n"
            "MANUAL nhập khối lượng tự quyết, không bị ép về VỐN AUTO; vẫn kiểm tra tiền/phí và điều kiện lệnh. "
            "AUTO 100 khi bật chỉ nâng lên 1 lô nếu còn đủ tiền và room; không vượt cap Priority.\n"
            "BOT = số mã đang giữ/BUY chờ trên tối đa; Priority giữ slot bên trong tổng. "
            "Chống nhiễu và LOSS/BLOCK chỉ chặn BUY BOT mới, không chặn SELL hay lưu tín hiệu mua lại.\n"
            f"Hiện tại: {getattr(self, '_preview_entry_summary', 'chờ dữ liệu')}."
        )

    def _phase1_capital_preview(self, details: dict[str, Any]) -> dict[str, Any]:
        """Display the current book's P1 envelope, not its actual stock/cash mix."""
        from ..rules.business import StaticRuleParameters
        params = StaticRuleParameters.from_dict(self.settings.rule_parameters)
        mode = self.mode.get()
        state_fn = getattr(getattr(self, "rule_state", None), "confirmed_market_state", None)
        state = state_fn() if callable(state_fn) else "UNKNOWN"
        if self.settings.market_phase_override_enabled:
            exposure = self.settings.market_phase_override_exposure_pct / 100.0
            state = self.settings.market_phase_override
        elif callable(state_fn):
            exposure = params.exposure.get(state, 0.0)
        else:
            exposure = _number(details.get("exposure"))
            exposure = exposure / 100.0 if exposure > 1.0 else exposure
        exposure = stock_exposure_limit(1.0, exposure)
        balance, positions, _orders = getattr(self, "snapshots", {}).get(mode, ({}, [], []))
        nav = nav_from_balance(balance, positions) if balance else None
        stock_limit = stock_exposure_limit(nav, exposure) if nav is not None else None
        return {
            "mode": mode, "nav": nav, "state": state, "exposure_pct": exposure * 100.0,
            "state_authoritative": bool(callable(state_fn) or self.settings.market_phase_override_enabled),
            "stock_limit": stock_limit, "cash_reserve": nav - stock_limit if nav is not None else None,
        }

    def _refresh_full_order_preview(self, status: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "preview_order_title"):
            return
        status = self._book_preview_status(status)
        symbol = self.symbol.get().strip().upper()
        mode = self.mode.get()
        order_type = self.order_type.get().upper()
        live_price = float(getattr(self, "_current_tick_price", 0.0) or 0.0)
        tick = (status.get("ticks") or {}).get(symbol) or {}
        preview_market_status = str(
            status.get("market_status", getattr(self, "_current_market_status", "")) or ""
        ).upper()
        entry_price = live_price
        entry_detail = "LIVE"
        price_error = ""
        if order_type == "LO":
            try:
                entry_price = _price_unit(float(self.price.get().replace(",", "")))
                entry_detail = "LO"
                if entry_price <= 0:
                    price_error = "Lệnh LO cần giá lớn hơn 0"
            except ValueError:
                entry_price = 0.0
                price_error = "Giá LO không hợp lệ"
        elif order_type in {"ATO", "ATC"}:
            entry_price, entry_detail = self._auction_preview_price(
                order_type, live_price, tick, preview_market_status,
            )
        raw_quantity = self.quantity.get().strip().replace(",", "")
        auto_quantity = not raw_quantity
        budget = 0.0
        forced_minimum = False
        if auto_quantity:
            quantity, budget, forced_minimum = self._suggested_order_quantity(entry_price, status, symbol)
            quantity_error = ""
        else:
            try:
                quantity = int(raw_quantity)
                quantity_error = ""
            except ValueError:
                quantity = 0
                quantity_error = "KHỐI LƯỢNG KHÔNG HỢP LỆ"
        valid_quantity, quantity_reason, _normalized = validate_quantity(quantity)
        auto_block_reason = ""
        if auto_quantity and quantity <= 0:
            auto_block_reason = (
                "KHÔNG ĐỦ VỐN MUA 1 LÔ"
                if budget > 0 and entry_price > 0 else "CHỜ TÍNH KHỐI LƯỢNG"
            )
        gross = entry_price * max(0, quantity) * 1000.0
        preview_capital = gross
        estimated_fee = self._preview_buy_fee(gross, symbol, mode) if gross > 0 else None
        fee_value = _compact_vnd(estimated_fee) if estimated_fee is not None else "—"
        working_dates = status.get("working_dates") or None
        phase, phase_label = market_phase(
            working_dates=working_dates,
            holidays=self.settings.trading_holidays,
            exchange=self._symbol_exchange(symbol),
        )
        expected_phase = "OPEN" if order_type in {"MARKET", "LO"} else order_type
        due = phase == expected_phase
        if order_type == "MARKET":
            exchange = self._symbol_exchange(symbol)
            due = due or (phase == "ATO" and self.settings.allow_ato and exchange == "HOSE")
            due = due or (phase == "ATC" and self.settings.allow_atc and exchange != "UPCOM")
        token_ready = mode == "PAPER" or self.real.has_trading_token()
        invalid_reason = quantity_error or (
            quantity_reason if not auto_quantity and not valid_quantity else ""
        ) or price_error
        if auto_quantity and quantity <= 0:
            invalid_reason = auto_block_reason
        if not symbol:
            invalid_reason = "Chưa chọn mã chứng khoán"

        if invalid_reason:
            badge, badge_bg, badge_fg = "LỖI", "#5A1E1E", "#FFCDD2"
            reason = invalid_reason
            route = "CHẶN"
        elif auto_quantity:
            if forced_minimum:
                badge, badge_bg, badge_fg = "AUTO 100", "#6B4A0B", "#FFF3B0"
                reason = (
                    f"AUTO 100 · {_compact_vnd(gross)} > {_compact_vnd(budget)}"
                )
            else:
                badge, badge_bg, badge_fg = "THEO VỐN", "#123F6B", "#D7ECFF"
                reason = auto_block_reason or f"{quantity:,} CP · VỐN/MÃ {_compact_vnd(budget)}"
            route = "ĐẶT" if due else "CACHE"
        elif due and mode == "REAL" and not token_ready:
            badge, badge_bg, badge_fg = "TOKEN", "#4A3B16", "#FFF3B0"
            reason = f"ĐẶT · {phase} · OTP"
            route = "ĐẶT"
        elif due:
            badge, badge_bg, badge_fg = "READY", "#165C35", "#DFF7E8"
            reason = f"ĐẶT · {phase}"
            route = "GỬI DNSE"
        else:
            badge, badge_bg, badge_fg = "CACHE", "#4A3B16", "#FFF3B0"
            reason = f"CACHE · {expected_phase}"
            route = "CACHE"

        button_text, button_bg, button_hover, button_border = self._buy_button_presentation(
            invalid_reason, due, token_ready,
        )
        self.execute_button.configure(
            text=button_text,
            fg_color=button_bg,
            hover_color=button_hover,
            border_color=button_border,
        )

        tick_ts = float(tick.get("timestamp", 0.0) or 0.0)
        age = max(0.0, time.time() - tick_ts) if tick_ts else None
        market_status = str(status.get("market_status", phase) or phase).upper()
        market_active = market_status in {"ATO", "OPEN", "CONTINUOUS", "ATC"}
        frozen = (
            bool(tick.get("frozen"))
            or bool(tick.get("price_frozen"))
            or not market_active
            or bool(age is not None and age > 10.0)
        )
        tp_value, tp_pnl = self._preview_level_details(
            self.tp.get(), entry_price, quantity, preview_capital,
        )
        sl_value, sl_pnl = self._preview_level_details(
            self.sl.get(), entry_price, quantity, preview_capital,
        )

        self.preview_order_title.configure(text=f"{symbol or '---'} · {mode} · BUY · {order_type}")
        self.preview_status_badge.configure(text=badge, fg_color=badge_bg, text_color=badge_fg)
        self.preview_status_reason.configure(
            text=reason,
            text_color=badge_fg if invalid_reason else COL_PREVIEW_TEXT,
        )
        self.preview_live_value.configure(
            text=(
                _display_price(live_price)
                if live_price > 0 else "NA"
            ),
            text_color=COL_WARN if live_price > 0 and frozen else COL_TEXT,
        )
        self.preview_entry_value.configure(
            text=(
                _display_price(entry_price)
                if entry_price > 0 else "NA"
            ),
            text_color=COL_WARN if entry_price > 0 and frozen and order_type != "LO" else COL_TEXT,
        )
        quantity_source = "AUTO" if forced_minimum else "VỐN" if auto_quantity else "TAY"
        self.preview_qty_title.configure(
            text=f"KL · {quantity_source}",
            text_color=COL_WARN if auto_quantity else COL_PREVIEW_TEXT,
        )
        self.preview_qty_value.configure(
            text=f"{quantity:,}" if quantity > 0 else "< 100" if auto_quantity else "0",
            text_color=COL_WARN if auto_quantity else COL_TEXT,
        )
        self.preview_cash_value.configure(
            text=(
                _compact_vnd(gross) if gross > 0 else "--"
            )
        )
        self.preview_fee_value.configure(
            text=fee_value,
            text_color=COL_WARN,
        )
        self.preview_tp_value.configure(
            text=(
                tp_value.split('·')[-1].strip()
                if tp_pnl is not None and tp_pnl >= 0 else tp_value
            ),
            text_color=COL_GREEN if tp_pnl is not None and tp_pnl >= 0 else COL_TEXT,
        )
        self.preview_tp_detail.configure(
            text=f"+{_compact_vnd(tp_pnl)}" if tp_pnl is not None and tp_pnl >= 0 else ""
        )
        self.preview_sl_value.configure(
            text=(
                sl_value.split('·')[-1].strip()
                if sl_pnl is not None and sl_pnl <= 0 else sl_value
            ),
            text_color=COL_RED if sl_pnl is not None and sl_pnl <= 0 else COL_TEXT,
        )
        self.preview_sl_detail.configure(
            text=f"-{_compact_vnd(abs(sl_pnl))}" if sl_pnl is not None and sl_pnl <= 0 else ""
        )
        self.preview_route_value.configure(text=route, text_color=badge_fg)
        params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        indicator_exit_policy = str(
            params.get("indicator_exit_policy", "ALERT") or "ALERT"
        ).upper()
        normal_arm = float(params.get("normal_arm_pct", 7.0) or 7.0)
        normal_giveback = float(params.get("normal_giveback_pct", 2.0) or 2.0)
        normal_policy = str(params.get("normal_policy", "AUTO") or "AUTO").upper()
        normal_dynamic = bool(params.get("normal_dynamic_enabled", False))
        normal_repeat = bool(params.get("normal_repeat_enabled", False))
        normal_sell = float(params.get("normal_sell_pct", 100.0) or 100.0)
        normal_price = entry_price * (1.0 + normal_arm / 100.0) if entry_price > 0 else 0.0
        normal_preview_sell = protect_level(
            entry_price, normal_arm, normal_arm, normal_giveback,
            dynamic_enabled=normal_dynamic,
        ).trigger_price if entry_price > 0 else 0.0
        self.preview_normal_value.configure(
            text=(
                f"{_display_price(normal_price)} → {_display_price(normal_preview_sell)}"
                if normal_price > 0 else "CHƯA CÓ GIÁ"
            ),
        )
        self.preview_normal_detail.configure(
            text=(
                f"{normal_policy} · ARM {normal_arm:g}% · TRAIL {normal_giveback:g}% · BÁN {normal_sell:g}%"
                f"{' · LẶP' if normal_repeat and normal_sell < 100 else ''}"
            ),
        )
        em_labels = {
            "normal_protection": "PROTECT",
            "indicator_exit": "E",
        }
        em_widgets = {
            "normal_protection": self.preview_em_normal,
            "indicator_exit": self.preview_em_exit,
        }
        em_value_widgets = {
            "normal_protection": self.preview_normal_value,
            "indicator_exit": self.preview_exit_value,
        }
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(symbol) if isinstance(decisions.get(symbol), dict) else {}
        decision_details = self._preview_indicator_details(status, symbol)
        atr_pct = _number(decision_details.get("atr14_daily_pct"))
        start_pct = _number(decision_details.get("dynamic_start_pct"))
        trail_pct = _number(decision_details.get("dynamic_trail_pct"))
        self.preview_atr.configure(
            text=f"{atr_pct:.2f}%" if atr_pct > 0 else "--",
            text_color=(COL_GREEN if normal_dynamic and atr_pct > 0 else "#60A5FA"),
        )
        start_enabled = bool(params.get("normal_atr_activation_enabled", True))
        trail_enabled = bool(params.get("normal_atr_trail_enabled", True))
        self.preview_atr_detail.configure(
            text=(
                f"{'START ' + format(start_pct, '.2f') + '%' if start_enabled else 'START OFF'} · "
                f"{'LÙI ' + format(trail_pct, '.2f') + '%' if trail_enabled else 'LÙI OFF'}"
                if atr_pct > 0 else "START -- · LÙI --"
            ),
        )
        current_profit = decision_details.get("current_profit_pct")
        signal = str(decision.get("signal") or "--").upper()
        for key, widget in em_widgets.items():
            enabled = bool(self._em_states.get(key, False))
            state_label = (
                indicator_exit_policy
                if key == "indicator_exit" and enabled else "ON" if enabled else "OFF"
            )
            widget.configure(
                text=f"{em_labels[key]} · {state_label}",
                text_color=(
                    COL_WARN if key == "indicator_exit" and enabled and indicator_exit_policy == "ALERT"
                    else COL_GREEN if enabled else COL_MUTED
                ),
            )
            em_value_widgets[key].configure(
                text_color=(
                    COL_RED if key == "indicator_exit" and signal == "SELL"
                    else COL_GREEN if enabled else COL_TEXT
                )
            )
        exit_enabled = bool(self._em_states.get("indicator_exit", False))
        position_quantity = max(0, int(decision_details.get("position_quantity", 0) or 0))
        self._render_exit_sell_preview(
            signal, exit_enabled, position_quantity, indicator_exit_policy,
        )
        if current_profit is not None and self._em_states.get("normal_protection", False):
            protected = decision_details.get("normal_trigger_price")
            protect_state = str(decision_details.get("normal_state", "WAIT") or "WAIT").upper()
            effective_trail = decision_details.get("normal_effective_trail_pct")
            self.preview_normal_value.configure(
                text=(
                    f"{protect_state} · PNL {_number(current_profit):+.1f}% · PROTECT {_display_price(protected)}"
                    if protected is not None else
                    f"{protect_state} · PNL {_number(current_profit):+.1f}%"
                )
            )
            if effective_trail is not None:
                self.preview_normal_detail.configure(
                    text=_dynamic_atr_preview_text(decision_details, params),
                )
        self._refresh_rule_preview(status, symbol)

    def _refresh_rule_preview(self, status: dict[str, Any], symbol: str) -> None:
        if not hasattr(self, "preview_rule_market"):
            return
        status = self._book_preview_status(status)
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(symbol) if isinstance(decisions.get(symbol), dict) else {}
        details = self._preview_indicator_details(status, symbol)
        indicators = details.get("indicators") if isinstance(details.get("indicators"), dict) else {}
        rule_params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        buy_ema_enabled = bool(rule_params.get("buy_signal_use_ema", True))
        buy_rsi_enabled = bool(rule_params.get("buy_signal_use_rsi", True))
        sell_ema_enabled = bool(rule_params.get("sell_signal_use_ema", True))
        sell_rsi_enabled = bool(rule_params.get("sell_signal_use_rsi", True))

        action = str(decision.get("action") or "WAIT").upper()
        market_state = str(decision.get("market_state") or "UNKNOWN").upper()
        signal = str(decision.get("signal") or "--").upper()
        position_quantity = max(0, int(details.get("position_quantity", 0) or 0))
        self._render_exit_sell_preview(
            signal,
            bool(self._em_states.get("indicator_exit", False)),
            position_quantity,
            str(rule_params.get("indicator_exit_policy", "ALERT") or "ALERT").upper(),
        )
        bot_enabled = bool(status.get("bot_enabled", False))
        self.preview_rule_title.configure(
            text=f"MUA · {'ON' if bot_enabled else 'OFF'}",
            text_color=COL_GREEN if bot_enabled else COL_RED,
        )
        market_details = details.get("market") if isinstance(details.get("market"), dict) else {}
        confirmation_fn = getattr(getattr(self, "rule_state", None), "market_confirmation", None)
        if callable(confirmation_fn):
            confirmation = confirmation_fn(int(rule_params.get("confirm_sessions", 3)))
            market_details = {
                **market_details, "confirmed_state": confirmation["confirmed"],
                "candidate_state": confirmation["candidate"], "confirmation_count": confirmation["count"],
                "confirmation_required": confirmation["required"], "confirmation_pending": confirmation["pending"],
            }
        market_details = {**market_details, "override_enabled": self.settings.market_phase_override_enabled}
        if self.settings.market_phase_override_enabled:
            market_details["confirmation_pending"] = False
        self._preview_market_confirmation = market_details
        allocation = self._phase1_capital_preview(details)
        self._preview_market_budget = allocation
        display_state = str(
            allocation["state"] if allocation["state_authoritative"] else market_state
            or "UNKNOWN"
        ).upper()
        state_labels = {
            "UPTREND": "TĂNG",
            "DOWNTREND": "GIẢM",
            "ACCUMULATION": "TÍCH LŨY",
            "DISTRIBUTION": "PHÂN PHỐI",
            "TRANSITION": "ĐANG TÍNH",
            "UNKNOWN": "ĐANG TÍNH",
        }
        state_label = state_labels.get(display_state, display_state.replace("_", " "))
        market_color = (
            COL_GREEN if display_state in {"UPTREND", "ACCUMULATION"}
            else COL_RED if display_state in {"DOWNTREND", "DISTRIBUTION"}
            else COL_WARN
        )
        pending_confirmation = bool(market_details.get("confirmation_pending", False))
        exposure_pct = allocation["exposure_pct"]
        cash_pct = max(0.0, 100.0 - exposure_pct)
        self.preview_rule_market.configure(
            text=f"{state_label} · CP {exposure_pct:g}% · TIỀN {cash_pct:g}%",
            text_color=market_color,
        )
        market_notes: list[str] = []
        override_enabled = bool(market_details.get("override_enabled", False))
        if override_enabled:
            market_notes.append("OVERRIDE")
        elif not pending_confirmation:
            market_notes.append("P1 TỰ ĐỘNG")
        if pending_confirmation:
            confirmation_count = max(
                0, int(market_details.get("confirmation_count", 0) or 0),
            )
            confirmation_required = max(
                1, int(market_details.get("confirmation_required", 1) or 1),
            )
            market_notes.append(
                f"XÁC NHẬN {state_labels.get(str(market_details.get('candidate_state', '')).upper(), '?')} · {confirmation_count}/{confirmation_required} PHIÊN"
            )
        volume_confidence = str(market_details.get("volume_confidence") or "OFF").upper()
        if volume_confidence != "OFF":
            market_notes.append(f"VOLUME {volume_confidence}")
        updated_at = str(details.get("updated_at") or "")
        if len(updated_at) >= 16:
            market_notes.append(updated_at[11:16])
        self.preview_rule_market_detail.configure(
            text=" · ".join(market_notes),
            text_color=COL_WARN if pending_confirmation or override_enabled else COL_PREVIEW_TEXT,
        )
        if market_notes:
            self.preview_rule_market_detail.grid(
                row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 3),
            )
        else:
            self.preview_rule_market_detail.grid_remove()

        def ema_preview(prefix: str, key_prefix: str) -> tuple[str, str]:
            fast_period = int(indicators.get(f"{key_prefix}_ema_fast_period", indicators.get("ema_fast_period", 3)) or 3)
            slow_period = int(indicators.get(f"{key_prefix}_ema_slow_period", indicators.get("ema_slow_period", 6)) or 6)
            fast_value = indicators.get(f"{key_prefix}_ema_fast", indicators.get("ema_fast"))
            slow_value = indicators.get(f"{key_prefix}_ema_slow", indicators.get("ema_slow"))
            try:
                fast_number = float(fast_value)
                slow_number = float(slow_value)
            except (TypeError, ValueError):
                return f"{prefix} EMA {fast_period}/{slow_period} · --/--", COL_PREVIEW_TEXT
            relation = ">" if fast_number > slow_number else "<" if fast_number < slow_number else "="
            spread = abs(fast_number - slow_number)
            decimals = 2 if spread >= 0.01 else 3 if spread >= 0.001 else 4
            fast_text = f"{fast_number:,.{decimals}f}".rstrip("0").rstrip(".")
            slow_text = f"{slow_number:,.{decimals}f}".rstrip("0").rstrip(".")
            color = COL_GREEN if fast_number > slow_number else COL_RED if fast_number < slow_number else COL_TEXT
            return f"{prefix} EMA {fast_period}/{slow_period} · {fast_text}{relation}{slow_text}", color

        buy_ema_text, buy_ema_color = ema_preview("BUY", "buy")
        sell_ema_text, sell_ema_color = ema_preview("E", "sell")
        if not buy_ema_enabled:
            buy_ema_text, buy_ema_color = buy_ema_text.replace("BUY EMA", "BUY EMA OFF", 1), COL_MUTED
        if not sell_ema_enabled:
            sell_ema_text, sell_ema_color = sell_ema_text.replace("E EMA", "E EMA OFF", 1), COL_MUTED
        rsi_period = int(indicators.get("rsi_period", 14) or 14)
        current_rsi = indicators.get("rsi")
        previous_rsi = indicators.get("rsi_previous")
        try:
            rsi_number = float(current_rsi)
            previous_number = float(previous_rsi)
        except (TypeError, ValueError):
            rsi_number = previous_number = None
        if rsi_number is None:
            rsi_text, rsi_color = f"RSI{rsi_period} -- · BUY {'BẬT' if buy_rsi_enabled else 'TẮT'} · E {'BẬT' if sell_rsi_enabled else 'TẮT'}", COL_PREVIEW_TEXT
        else:
            arrow = "↑" if previous_number is not None and rsi_number > previous_number else "↓" if previous_number is not None and rsi_number < previous_number else "→"
            rsi_text = (
                f"RSI{rsi_period} {rsi_number:.1f} {arrow} · "
                f"BUY {'BẬT' if buy_rsi_enabled else 'TẮT'} · E {'BẬT' if sell_rsi_enabled else 'TẮT'}"
            )
            rsi_color = COL_GREEN if signal == "BUY" else COL_RED if signal == "SELL" else (
                COL_GREEN if previous_number is not None and rsi_number > previous_number
                else COL_RED if previous_number is not None and rsi_number < previous_number
                else COL_TEXT
            )
        self.preview_rule_ema.configure(
            text=buy_ema_text,
            text_color=buy_ema_color,
        )
        self.preview_rule_sell_ema.configure(text=sell_ema_text, text_color=sell_ema_color)
        self.preview_rule_rsi.configure(text=rsi_text, text_color=rsi_color)

        checks = details.get("entry_checks") if isinstance(details.get("entry_checks"), dict) else {}
        budget_fn = getattr(self, "_preview_entry_checks", None)
        budget_checks = budget_fn(symbol, status) if callable(budget_fn) else None
        if budget_checks is not None:
            # Keep signal guards/slot counts, but money comes from the current
            # book, exactly as in the AUTO ticket. No signal or broker call here.
            checks = {**checks, **budget_checks}
            for key in ("order_budget", "available_capital", "available_cash", "nav", "minimum_order_room"):
                checks[key] = budget_checks.get(key, 0.0)
        open_positions = max(0, int(checks.get("open_positions", 0) or 0))
        max_positions = max(0, int(checks.get("max_positions", 0) or 0))
        capital = _number(checks.get("order_budget") or checks.get("available_capital"))
        whipsaw_on = bool(checks.get("whipsaw_enabled", False))
        crosses = max(0, int(checks.get("whipsaw_crossovers", 0) or 0))
        whipsaw_limit = max(0, int(checks.get("whipsaw_limit", 0) or 0))
        losses = max(0, int(checks.get("loss_streak", 0) or 0))
        loss_limit = max(0, int(checks.get("loss_lock_count", 0) or 0))
        force_min_lot = bool(checks.get("force_min_lot_enabled", False))
        whipsaw_locked = bool(whipsaw_on and whipsaw_limit and crosses >= whipsaw_limit)
        loss_locked = bool(checks.get("loss_blocked")) or bool(loss_limit and losses >= loss_limit)
        guard_warn = whipsaw_locked or loss_locked
        slot_summary = getattr(self, "_slot_summary", {})
        if slot_summary.get("mode", self.mode.get()) != self.mode.get():
            slot_summary = {}
        slot_used = int(slot_summary.get("used", open_positions) or 0)
        slot_max = int(slot_summary.get("max", max_positions) or max_positions)
        pending_buys = int(slot_summary.get("pending", 0) or 0)
        priority_positions = int(slot_summary.get("priority", 0) or 0)
        manual_positions = int(slot_summary.get("manual", 0) or 0)
        bot_open_positions = int(slot_summary.get("bot_open", open_positions) or 0)
        total_positions = int(
            slot_summary.get("total", bot_open_positions + manual_positions) or 0
        )
        phase3_parts = [f"BOT {slot_used}/{slot_max or '--'}"]
        if priority_positions:
            phase3_parts.append(f"GIỮ {int(slot_summary.get('reserved', checks.get('priority_reserved', 0)) or 0)} SLOT")
        if manual_positions:
            phase3_parts.append(f"MANUAL {manual_positions}")
            phase3_parts.append(f"TỔNG {total_positions}")
        if pending_buys:
            phase3_parts.append(f"BUY CHỜ {pending_buys}")
        phase3_parts.append(
            f"VỐN AUTO {_compact_vnd(capital)}" if "order_budget" in (budget_checks if budget_checks is not None else checks)
            else "VỐN AUTO —"
        )
        self._preview_entry_summary = " · ".join(phase3_parts)
        self.preview_rule_phase3.configure(
            text=f"BOT {slot_used}/{slot_max or '--'} · {phase3_parts[-1].replace('VỐN AUTO', 'AUTO')}",
            text_color=COL_WARN if guard_warn else COL_TEXT,
        )
        symbol_tick = (status.get("ticks") or {}).get(symbol) or {}
        phase3_price = _number(
            symbol_tick.get("price")
            or symbol_tick.get("lastPrice")
            or symbol_tick.get("close")
            or symbol_tick.get("expected_price")
            or symbol_tick.get("bid")
            or symbol_tick.get("ask")
        )
        available_cash = _number(checks.get("available_cash"))
        nav = _number(checks.get("nav"))
        phase3_sizing = size_buy_order(
            budget_vnd=capital,
            price_board=phase3_price,
            available_cash=available_cash,
            nav=nav,
            force_min_lot_enabled=force_min_lot,
            minimum_order_room_vnd=_number(checks.get("minimum_order_room")),
            buy_fee_rate=_number(checks.get("buy_fee_rate")),
        )
        forced_minimum = phase3_sizing.used_minimum
        whipsaw_status = (
            "OFF" if not whipsaw_on
            else "KHÓA BUY" if whipsaw_locked
            else "OK"
        )
        self.preview_rule_phase3_detail.configure(
            text=(
                f"⚠ AUTO 100 · CHỐNG NHIỄU {whipsaw_status} · LỖ {losses}/{loss_limit or '--'}"
                if forced_minimum
                else f"CHỐNG NHIỄU {whipsaw_status} · LỖ {losses}/{loss_limit or '--'}"
            ),
            text_color=COL_RED if guard_warn else COL_WARN if forced_minimum else COL_PREVIEW_TEXT,
        )
        self.preview_rule_phase3_guard.configure(
            text=(
                f"WHIPSAW: {whipsaw_status} · LOSS: {losses}/{loss_limit or '--'}"
                f"{' · KHÓA MÃ' if loss_locked else ''}"
            ),
            text_color=COL_RED if guard_warn else COL_PREVIEW_TEXT,
        )
        reason = str(decision.get("reason") or "").upper()
        reason_labels = {
            "": "CHỜ DỮ LIỆU",
            "NO_NEW_BUY_SIGNAL": "CHỜ · CHƯA CÓ BUY",
            "BUY_SIGNAL": "BUY · ĐỦ ĐIỀU KIỆN",
            "SELL_SIGNAL": "SELL · TÍN HIỆU THOÁT",
            "HOLD_POSITION": "GIỮ VỊ THẾ",
            "BUY_ALREADY_PENDING": "ĐÃ CÓ LỆNH MUA CHỜ",
            "MAX_POSITIONS": "ĐÃ ĐỦ SỐ MÃ",
            "BOT_OFF": "BOT ĐANG TẮT",
            "MANUAL_SELL_PAUSE": "TẠM KHÓA BUY SAU BÁN TAY",
            "NO_AVAILABLE_CAPITAL": "KHÔNG ĐỦ CASH",
            "BROKER_REJECTED": "BROKER TỪ CHỐI",
            "BROKER_FAILED": "GỬI BROKER THẤT BẠI",
            "MARKET_STATE_UNKNOWN": "CHỜ · STATE CHƯA XÁC NHẬN",
            "WHIPSAW_LOCK": "TẠM KHÓA DO NHIỄU",
            "LOCKED_AFTER_3_LOSSES": "KHÓA SAU 3 LỆNH LỖ",
            "LOCKED_AFTER_LOSSES": "KHÓA SAU CHUỖI LỖ",
            "MANUAL_OR_EXTERNAL_POSITION": "VỊ THẾ MANUAL",
            "POSITION_MANAGED_SL_ONLY": "CHỈ THEO DÕI SL",
            "STOP_LOSS": "KÍCH HOẠT SL",
            "INDICATOR_EXIT": "KÍCH HOẠT EXIT SELL",
            "INDICATOR_EXIT_ALERT": "E ALERT · KHÔNG ĐẶT LỆNH",
            "PRICE_PROTECTION": "KÍCH HOẠT BẢO VỆ GIÁ",
            "NORMAL_PROTECTION": "PROTECT",
            "NORMAL_ARMED": "PROTECT AUTO ĐÃ KÍCH HOẠT",
            "BUY_CONFIRMATION_WAIT": "CHỜ XÁC NHẬN BUY",
            "BUY_WINDOW_WAIT": "CHỜ KHUNG GIỜ MUA",
            "BUY_WINDOW_BROKEN": "HỦY CHỜ GIỜ · ĐIỀU KIỆN BUY KHÔNG CÒN ĐẠT",
            "BUY_WINDOW_EXPIRED": "HẾT KHUNG GIỜ MUA",
            "BUY_WINDOW_MARKET_CLOSED": "CHỜ PHIÊN GIAO DỊCH",
            "BUY_FILTER_NEEDS_REALTIME": "LỌC BUY THEO GIỜ CẦN REALTIME",
            "BUY_CONFIRMATION_BROKEN": "HỦY BUY · TÍN HIỆU KHÔNG GIỮ ĐỦ",
            "BUY_CONFIRMATION_NEEDS_REALTIME": "XÁC NHẬN BUY CẦN REALTIME",
            "BUY_CONFIRMATION_NO_CONDITION": "XÁC NHẬN BUY CHƯA CHỌN CHỈ BÁO",
            "BUY_VOLUME_NOT_READY": "CHỜ BUY · CHƯA ĐỦ DỮ LIỆU VOLUME",
            "BUY_VOLUME_LOW": "CHỜ BUY · VOLUME CHƯA ĐẠT",
            "UNKNOWN_EXCHANGE": "CHƯA XÁC ĐỊNH SÀN",
        }
        reason_text = str(details.get("status_text") or "") or reason_labels.get(
            reason, reason.replace("_", " ") if reason else "CHỜ DỮ LIỆU"
        )
        if action == "WAIT" and signal == "SELL" and reason != "INDICATOR_EXIT_ALERT":
            reason_text = "CHỜ BUY"
        if reason == "BUY_SIGNAL":
            reason_color = COL_GREEN
        elif reason in {"SELL_SIGNAL", "INDICATOR_EXIT", "INDICATOR_EXIT_ALERT"}:
            reason_color = COL_RED
        elif action == "BUY":
            reason_color = COL_GREEN
        elif action == "SELL":
            reason_color = COL_RED
        else:
            reason_color = COL_WARN if action == "WAIT" else COL_PREVIEW_TEXT
        self.preview_rule_reason.configure(
            text=reason_text,
            text_color=reason_color,
        )

    def _select_info_tab(self, name: str) -> None:
        if not hasattr(self, "log_tabview"):
            return
        name = str(name or "PREVIEW").removesuffix(" *")
        if getattr(self, "_info_collapsed", False):
            self._set_info_panel_collapsed(False)
        self.log_tabview.set(name)
        self._on_log_tab_change()

    def _toggle_info_panel(self) -> None:
        self._set_info_panel_collapsed(not getattr(self, "_info_collapsed", False))

    def _set_info_panel_collapsed(self, collapsed: bool) -> None:
        if not hasattr(self, "info_panel") or not hasattr(self, "log_tabview"):
            return
        self._info_collapsed = bool(collapsed)
        if self._info_collapsed:
            self.log_tabview.grid_remove()
            self.info_panel.configure(height=44)
            self.info_panel.grid_rowconfigure(1, weight=0, minsize=0)
            self.right.grid_rowconfigure(2, weight=0, minsize=44)
            self.info_collapse_button.configure(text="⌄")
        else:
            self.log_tabview.grid()
            self.info_panel.configure(height=320)
            self.info_panel.grid_rowconfigure(1, weight=1, minsize=0)
            self.right.grid_rowconfigure(2, weight=0, minsize=320)
            self.info_collapse_button.configure(text="⌃")

    def _render_exit_sell_preview(
        self,
        signal: str,
        enabled: bool,
        position_quantity: int,
        policy: str = "ALERT",
    ) -> None:
        policy = str(policy or "ALERT").upper()
        if not enabled:
            value, detail, color = "CHƯA ÁP DỤNG", "ĐANG TẮT", COL_MUTED
        elif position_quantity <= 0:
            value, detail, color = "SAU KHI MUA", f"{policy} · CHỜ TÍN HIỆU", COL_PREVIEW_TEXT
        elif str(signal or "").upper() == "SELL":
            if policy == "ALERT":
                value, detail, color = "E ALERT", "KHÔNG ĐẶT LỆNH", COL_WARN
            else:
                value, detail, color = "SELL", "AUTO · BÁN HẾT", COL_RED
        else:
            detail = "ALERT · KHÔNG BÁN" if policy == "ALERT" else "AUTO · BÁN HẾT"
            value, color = "CHỜ TÍN HIỆU", COL_PREVIEW_TEXT
        self.preview_exit_value.configure(text=value, text_color=color)
        self.preview_exit_detail.configure(
            text=detail,
            text_color=COL_MUTED if not enabled else COL_TEXT,
        )

    def _on_log_tab_change(self) -> None:
        active = self.log_tabview.get() if hasattr(self, "log_tabview") else ""
        target = "manual" if active == "Manual" else "bot" if active == "Bot" else ""
        if target:
            self._set_log_unread(target, False)
        if active == "PREVIEW":
            self._refresh_full_order_preview()
            self.after_idle(self._sync_preview_scrollbar)
        if hasattr(self, "info_tab_selector"):
            self.info_tab_selector.set(active)

    def _set_log_unread(self, target: str, unread: bool) -> None:
        if target not in getattr(self, "log_tab_keys", {}):
            return
        self.log_tab_unread[target] = bool(unread)
        base = self.log_tab_keys[target]
        label = f"{base} *" if unread else base
        selector = getattr(self, "info_tab_selector", None)
        if selector is None:
            selector = getattr(self.log_tabview, "_segmented_button", None)
        try:
            for key, button in selector._buttons_dict.items():
                if key == base:
                    button.configure(text=label)
                    break
        except (AttributeError, tk.TclError):
            pass

    def _refresh_api_health_panel(self, status: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "preview_health_core"):
            return
        status = status if isinstance(status, dict) else self.bridge.read_status()
        daemon_health = status.get("api_health") if isinstance(status.get("api_health"), dict) else {}
        ws = daemon_health.get("websocket") if isinstance(daemon_health.get("websocket"), dict) else {}
        daemon_rest = daemon_health.get("rest") if isinstance(daemon_health.get("rest"), dict) else {}
        ui_rest = self.real.api_health()
        rest_rows = [row for row in (ui_rest, daemon_rest) if isinstance(row, dict)]
        latest = max(rest_rows, key=lambda row: int(row.get("total_requests", 0) or 0), default={})
        last_status = latest.get("last_status")

        heartbeat_age = max(0.0, time.time() - _number(status.get("heartbeat_at")))
        daemon_state = str(status.get("daemon_status") or "STARTING").upper()
        daemon_alive = bool(
            getattr(self, "daemon_process", None)
            and self.daemon_process.poll() is None
        )
        if daemon_alive and (
            heartbeat_age > 8 or daemon_state in {"STARTING", "STOPPED", "STALE"}
        ):
            daemon_state = "SYNC"
        elif heartbeat_age > 8:
            daemon_state = "STALE"
        daemon_ok = daemon_state == "RUNNING"
        self.preview_health_daemon.configure(
            text="DAEMON OK" if daemon_ok else "DAEMON SYNC" if daemon_state == "SYNC" else "DAEMON LỖI",
            text_color=COL_GREEN if daemon_ok else COL_WARN if daemon_state == "SYNC" else COL_RED,
        )

        configured = self.real.configured()
        total_requests = sum(int(row.get("total_requests", 0) or 0) for row in rest_rows)
        rest_ok = total_requests > 0 and isinstance(last_status, (int, float)) and 200 <= int(last_status) < 300
        rest_state = "OK" if rest_ok else "WAIT" if total_requests == 0 else "ERROR"
        dnse_ok = configured and rest_state != "ERROR"
        dnse_text = "OK" if dnse_ok else "CHỜ" if not configured else "LỖI"
        ws_online = bool(ws.get("connected") and ws.get("authenticated"))
        ws_connecting = bool(ws.get("running")) and not ws_online
        self.preview_health_core.configure(
            text=f"DNSE {dnse_text}",
            text_color=COL_GREEN if dnse_ok else COL_RED if configured else COL_WARN,
        )
        self.preview_health_ws.configure(
            text=f"WS {'OK' if ws_online else 'CHỜ' if ws_connecting else 'OFF'}",
            text_color=(
                COL_GREEN if ws_online
                else COL_WARN if ws_connecting or not configured
                else COL_RED
            ),
        )

        self.preview_health_rest.configure(
            text={"OK": "API OK", "WAIT": "API CHỜ", "ERROR": "API LỖI"}[rest_state],
            text_color=COL_GREEN if rest_ok else COL_RED if rest_state == "ERROR" else COL_PREVIEW_TEXT,
        )
        mode = self.mode.get()
        token_ready = self.real.has_trading_token()
        self.preview_health_token.configure(
            text=(
                "PAPER" if mode == "PAPER"
                else "OTP OK" if token_ready
                else "OTP"
            ),
            text_color=(
                COL_PREVIEW_TEXT if mode == "PAPER"
                else COL_GREEN if token_ready
                else COL_WARN
            ),
        )

        symbol = self.symbol.get().strip().upper()
        tick = (status.get("ticks") or {}).get(symbol) or {}
        tick_ts = float(tick.get("timestamp", 0.0) or 0.0)
        tick_age = max(0.0, time.time() - tick_ts) if tick_ts else None
        market_status = str(status.get("market_status", "OFFLINE") or "OFFLINE").upper()
        market_active = market_status in {"ATO", "OPEN", "CONTINUOUS", "ATC"}
        data_frozen = bool(tick) and (
            bool(tick.get("frozen"))
            or bool(tick.get("price_frozen"))
            or not market_active
            or bool(tick_age is not None and tick_age > 10.0)
        )
        data_ok = bool(tick and market_active)
        if market_status == "ATO":
            price_state = "ATO"
        elif market_status == "ATC":
            price_state = "ATC"
        elif market_status in {"OPEN", "CONTINUOUS"}:
            price_state = "MỞ"
        elif data_frozen:
            price_state = "ĐÓNG"
        elif tick_age is None:
            price_state = "CHỜ"
        else:
            price_state = "CHẬM"
        self.preview_health_trade.configure(
            text=f"GIÁ {price_state}",
            text_color=(
                COL_GREEN if data_ok
                else COL_PREVIEW_TEXT if price_state == "ĐÓNG"
                else COL_WARN if tick
                else COL_WARN
            ),
        )

        current_cycle_error = bool(str(status.get("error") or "").strip())
        ws_error = bool(configured and market_active and not ws_online and not ws_connecting)
        price_error = bool(market_active and not data_ok)
        has_error = bool(
            daemon_state in {"STOPPED", "STALE"}
            or rest_state == "ERROR"
            or ws_error
            or price_error
            or current_cycle_error
        )
        has_warning = bool(
            not has_error and (
                daemon_state == "SYNC"
                or not configured
                or ws_connecting
                or rest_state == "WAIT"
                or (mode == "REAL" and not token_ready)
            )
        )
        self.preview_health_title.configure(
            text="HEALTH LỖI" if has_error else "HEALTH CẢNH BÁO" if has_warning else "HEALTH OK",
            text_color=COL_RED if has_error else COL_WARN if has_warning else COL_GREEN,
        )

    def _show_running_legend(self) -> None:
        top = ctk.CTkToplevel(self)
        top.title("Chú thích lệnh đang chạy")
        top.geometry("570x520")
        top.minsize(520, 470)
        top.transient(self)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            top, text="CHÚ THÍCH BẢNG LỆNH",
            font=("Segoe UI", 17, "bold"), text_color=COL_TITLE,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        body = ctk.CTkScrollableFrame(
            top, fg_color=COL_SURFACE, corner_radius=9,
            border_width=1, border_color=COL_BORDER,
        )
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        body.grid_columnconfigure(1, weight=1)

        rows = (
            ("LÃI", "#193524", "Vị thế đang có PnL ròng dương."),
            ("LỖ", "#3A2024", "Vị thế đang có PnL ròng âm."),
            ("T+2", COL_SETTLEMENT_BG, "Cổ phiếu chưa được phép bán hoặc lệnh bán đang chờ cổ về."),
            ("CACHE", "#42351B", "Lệnh đang chờ phiên, chờ token hoặc tạm dừng trong app."),
            ("DNSE ĐANG KHỚP", "#123F6B", "Lệnh đã gửi DNSE và vẫn đang chờ khớp."),
            ("KHỚP MỘT PHẦN", "#6A3F08", "Mới khớp một phần khối lượng; phần còn lại vẫn chờ."),
            ("LỖI", "#5A1E1E", "Gửi lệnh lỗi hoặc chưa xác định được trạng thái."),
        )
        for index, (label, color, explanation) in enumerate(rows):
            chip = ctk.CTkLabel(
                body, text=label, width=142, height=34,
                font=("Segoe UI", 11, "bold"),
                fg_color=color, text_color=COL_TEXT, corner_radius=7,
            )
            chip.grid(row=index, column=0, sticky="ew", padx=(8, 10), pady=5)
            ctk.CTkLabel(
                body, text=explanation, font=("Segoe UI", 11),
                text_color=COL_TEXT, anchor="w", justify="left", wraplength=340,
            ).grid(row=index, column=1, sticky="ew", padx=(0, 8), pady=5)

        rule_row = len(rows)
        ctk.CTkLabel(
            body, text="TAG RULE", width=142, height=34,
            font=("Segoe UI", 11, "bold"),
            fg_color="#343A46", text_color=COL_TEXT, corner_radius=7,
        ).grid(row=rule_row, column=0, sticky="ew", padx=(8, 10), pady=(12, 5))
        ctk.CTkLabel(
            body,
            text=(
                "PROTECT: bảo vệ lợi nhuận theo rule đang chọn\n"
                "E: thoát phần còn lại khi có tín hiệu SELL\n"
                "WAIT: đang chờ  |  ARM: đã kích hoạt  |  DONE: đã xử lý"
            ),
            font=("Segoe UI", 11), text_color=COL_TEXT,
            anchor="w", justify="left", wraplength=340,
        ).grid(row=rule_row, column=1, sticky="ew", padx=(0, 8), pady=(12, 5))

        ctk.CTkLabel(
            top,
            text="Màu chỉ giúp nhận nhanh; tag chữ trên dòng là trạng thái chính xác.",
            font=("Segoe UI", 11), text_color=COL_MUTED,
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 14))

    def _sync_left_scrollbar(self) -> None:
        if not self.running or not self.left.winfo_exists():
            return
        try:
            canvas = self.left._parent_canvas
            scrollbar = self.left._scrollbar
            bounds = canvas.bbox("all")
            content_height = int(bounds[3] - bounds[1]) if bounds else 0
            needs_scroll = content_height > canvas.winfo_height() + 2
            visible = scrollbar.winfo_manager() == "grid"
            if needs_scroll and not visible:
                scrollbar.grid()
            elif not needs_scroll and visible:
                scrollbar.grid_remove()
                canvas.yview_moveto(0)
        except (AttributeError, tk.TclError):
            return

    def _sync_preview_scrollbar(self) -> None:
        """Fit PREVIEW to its viewport and scroll only when 300px cannot fit."""
        preview = getattr(self, "preview_scroll", None)
        if not self.running or preview is None or not preview.winfo_exists():
            return
        try:
            canvas = preview._parent_canvas
            scrollbar = preview._scrollbar
            panel = self.preview_focus_panel
            viewport_height = int(canvas.winfo_height())
            if viewport_height > 50:
                target_height = _preview_panel_height(
                    viewport_height,
                    float(panel._get_widget_scaling()),
                )
                current_height = int(float(panel.cget("height")))
                if abs(current_height - target_height) > 1:
                    panel.configure(height=target_height)
                    canvas.yview_moveto(0)
                    # Recheck after Tk has recalculated the canvas scrollregion.
                    self.after_idle(self._sync_preview_scrollbar)
                    return
            bounds = canvas.bbox("all")
            content_height = int(bounds[3] - bounds[1]) if bounds else 0
            needs_scroll = content_height > canvas.winfo_height() + 2
            visible = scrollbar.winfo_manager() == "grid"
            if needs_scroll and not visible:
                scrollbar.grid()
            elif not needs_scroll and visible:
                scrollbar.grid_remove()
                canvas.yview_moveto(0)
        except (AttributeError, tk.TclError):
            return
