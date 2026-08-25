from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from ..trading.market import market_phase
from ..trading.portfolio import size_buy_order, validate_quantity
from .view import (
    COL_BORDER, COL_GRAY, COL_GREEN, COL_MUTED, COL_PREVIEW_TEXT, COL_RED,
    COL_SURFACE, COL_SURFACE_2, COL_TEXT, COL_WARN, FONT_BOLD,
    FONT_PREVIEW_TITLE, FONT_PREVIEW_VALUE, _compact_vnd, _display_price,
    _number, _price_unit,
)
from .windows import _HoverHint


class DashboardPanelsMixin:
    def _left_panel(self) -> None:
        # GROUP 1 — account snapshot and session state.
        account = ctk.CTkFrame(
            self.left, fg_color=COL_SURFACE, corner_radius=10,
            border_width=1, border_color=COL_BORDER,
        )
        account.pack(fill="x", pady=(4, 5), padx=6)
        account.grid_columnconfigure(0, weight=1)
        account.grid_columnconfigure(1, weight=0, minsize=142)

        self.lbl_equity = ctk.CTkLabel(
            account, text="----", font=("Segoe UI", 30, "bold"),
            text_color=COL_GREEN, anchor="w",
        )
        self.lbl_equity.grid(row=0, column=0, sticky="w", padx=(11, 5), pady=(6, 0))
        session_box = ctk.CTkFrame(account, fg_color="transparent")
        session_box.grid(row=0, column=1, sticky="ne", padx=(3, 11), pady=(8, 0))
        self.lbl_session = ctk.CTkLabel(
            session_box, text="PHIÊN: --", font=("Segoe UI", 10, "bold"),
            text_color=COL_MUTED, anchor="e", width=140,
        )
        self.lbl_session.pack(fill="x")
        self.lbl_brain = ctk.CTkLabel(
            session_box, text="DAEMON: CHỜ", font=("Segoe UI", 10, "bold"),
            text_color=COL_WARN, anchor="e",
        )
        self.lbl_brain.pack(fill="x", pady=(2, 0))

        self.lbl_account = ctk.CTkLabel(
            account, text=f"ID: {self.account_id}  ·  PAPER",
            font=("Segoe UI", 10), text_color=COL_TEXT, anchor="w",
            wraplength=245, justify="left",
        )
        self.lbl_account.grid(row=1, column=0, sticky="ew", padx=(11, 5), pady=(0, 2))
        account_footer = ctk.CTkFrame(account, fg_color="transparent")
        account_footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=11, pady=(2, 7))
        account_footer.grid_columnconfigure(0, minsize=82)
        account_footer.grid_columnconfigure(1, minsize=120)
        account_footer.grid_columnconfigure(2, weight=1)
        self.lbl_pnl = ctk.CTkLabel(
            account_footer, text="PNL: 0", font=("Segoe UI", 12, "bold"), anchor="w"
        )
        self.lbl_pnl.grid(row=0, column=0, sticky="w")
        self.lbl_cash = ctk.CTkLabel(
            account_footer, text="FEE: 0", font=("Segoe UI", 12, "bold"),
            text_color=COL_WARN, anchor="w",
        )
        self.lbl_cash.grid(row=0, column=1, sticky="w", padx=(24, 0))
        self.btn_reset_daily_fee = ctk.CTkButton(
            account_footer, text="↻", width=25, height=22,
            font=("Segoe UI Symbol", 12, "bold"),
            fg_color="#282D34", hover_color="#3A414B",
            text_color=COL_TEXT, corner_radius=6,
            command=self._reset_daily_fee,
        )
        self.btn_reset_daily_fee.grid(row=0, column=3, sticky="e", padx=(14, 0))

        # GROUP 2 — exactly four compact control rows.
        control = ctk.CTkFrame(
            self.left, fg_color=COL_SURFACE, corner_radius=10,
            border_width=1, border_color=COL_BORDER,
        )
        control.pack(fill="x", padx=6, pady=5)
        control.columnconfigure(0, minsize=47)
        control.columnconfigure(1, weight=1)

        def setting_row(index: int, label: str) -> ctk.CTkFrame:
            ctk.CTkLabel(
                control, text=label, font=("Segoe UI", 10, "bold"),
                text_color=COL_MUTED, anchor="e",
            ).grid(row=index, column=0, sticky="e", padx=(7, 6), pady=3)
            frame = ctk.CTkFrame(control, fg_color="transparent")
            frame.grid(row=index, column=1, sticky="ew", padx=(0, 7), pady=3)
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
        mode_row.grid_columnconfigure(0, weight=3)
        mode_row.grid_columnconfigure(1, weight=2)
        self.mode = tk.StringVar(value="PAPER" if self.settings.paper_mode else "REAL")
        ctk.CTkSegmentedButton(
            mode_row, values=["REAL", "PAPER"], variable=self.mode,
            command=self._mode_changed, height=32, font=("Segoe UI", 11),
            selected_color=COL_GREEN, selected_hover_color="#16A34A",
            unselected_color=COL_GRAY, unselected_hover_color="#4B515B",
            text_color=COL_TEXT,
            corner_radius=7,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.bot_button = ctk.CTkButton(
            mode_row, text="BOT · OFF", height=32, font=("Segoe UI", 10, "bold"),
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=7,
            command=self._toggle_bot,
        )
        self.bot_button.grid(row=0, column=1, sticky="ew")

        tools_row = setting_row(2, "TOOLS")
        tool_specs = [
            ("⚙  RULE", self._rule_settings),
            ("⛓  KẾT NỐI", self._advanced),
        ]
        for column, (text, command) in enumerate(tool_specs):
            tools_row.grid_columnconfigure(column, weight=1, uniform="tool")
            button = ctk.CTkButton(
                tools_row, text=text, height=32, font=("Segoe UI", 10, "bold"),
                fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=7,
                command=command,
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0))
            if column == 0:
                self.rule_button = button
            else:
                self.connection_button = button

        em_row = setting_row(3, "EM")
        self._em_specs = {
            "normal_protection": "NORMAL",
            "high_profit_protection": "HIGH",
            "indicator_exit": "EXIT SELL",
        }
        self._em_states = {key: False for key in self._em_specs}
        self._em_buttons: dict[str, ctk.CTkButton] = {}
        for column, key in enumerate(self._em_states):
            em_row.grid_columnconfigure(column, weight=1, uniform="em")
            title = self._em_specs[key]
            button = ctk.CTkButton(
                em_row, text=f"{title} · OFF", height=36,
                font=("Segoe UI", 10, "bold"),
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
        order.pack(fill="x", padx=6, pady=4)

        quote = ctk.CTkFrame(order, fg_color=COL_SURFACE_2, corner_radius=8)
        quote.pack(fill="x", padx=8, pady=(8, 5))
        quote.grid_columnconfigure(0, weight=1)
        quote.grid_columnconfigure(1, weight=1)
        self.lbl_quote_symbol = ctk.CTkLabel(
            quote, text=self.symbol.get(), width=72, height=28,
            font=("Segoe UI", 14, "bold"), fg_color="#2B3440",
            corner_radius=7, text_color=COL_TEXT, anchor="center",
        )
        self.lbl_quote_symbol.grid(row=0, column=0, sticky="w", padx=(11, 5), pady=(7, 0))
        change_box = ctk.CTkFrame(quote, fg_color="transparent")
        change_box.grid(row=0, column=1, sticky="e", padx=(5, 9), pady=(7, 0))
        self.lbl_change = ctk.CTkLabel(
            change_box, text="--", font=("Cascadia Mono", 10, "bold"),
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
            quote, text="---", font=("Cascadia Mono", 37, "bold"),
            text_color=COL_TEXT, anchor="center",
        )
        self.lbl_price.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 0))
        self.lbl_market = ctk.CTkLabel(
            quote, text="CHỜ DỮ LIỆU", font=("Segoe UI", 11, "bold"),
            text_color=COL_WARN, anchor="center",
        )
        self.lbl_market.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))

        preview = ctk.CTkFrame(quote, fg_color="transparent")
        preview.grid(row=3, column=0, columnspan=2, sticky="ew", padx=7, pady=(0, 7))
        preview.grid_columnconfigure(0, weight=1)

        pnl_panel = ctk.CTkFrame(preview, fg_color="#1B1F25", corner_radius=7)
        pnl_panel.grid(row=0, column=0, sticky="ew")
        pnl_panel.grid_columnconfigure((0, 1, 2), weight=1, uniform="pnl_preview")
        pnl_specs = (
            ("TP", "lbl_tp_title", "lbl_tp_preview", COL_GREEN),
            ("SL", "lbl_sl_title", "lbl_sl_preview", COL_RED),
            ("FEE", "lbl_fee_title", "lbl_fee_preview", COL_WARN),
        )
        for column, (title, title_attr, value_attr, title_color) in enumerate(pnl_specs):
            box = ctk.CTkFrame(pnl_panel, fg_color="transparent")
            box.grid(row=0, column=column, sticky="nsew", padx=5, pady=5)
            title_label = ctk.CTkLabel(
                box, text=title, font=FONT_PREVIEW_TITLE, height=22,
                text_color=title_color, anchor="center",
            )
            title_label.pack(fill="x")
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

        action = ctk.CTkFrame(order, fg_color="transparent")
        action.pack(fill="x", padx=9, pady=(1, 5))
        action.grid_columnconfigure(1, weight=1)
        self.order_type = tk.StringVar(value="MARKET")
        ctk.CTkOptionMenu(
            action, values=["MARKET", "LO", "ATO", "ATC"],
            variable=self.order_type, command=self._order_type_changed,
            width=115, height=42, font=("Segoe UI", 11, "bold"),
            fg_color=COL_GRAY, button_color="#4B515B",
            button_hover_color="#59616D", corner_radius=8,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.execute_button = ctk.CTkButton(
            action, text="CACHE", font=("Segoe UI", 13, "bold"),
            height=42, fg_color="#16A34A", hover_color="#15803D",
            border_width=1, border_color=COL_GREEN,
            corner_radius=8, command=lambda: self._submit("BUY"),
        )
        self.execute_button.grid(row=0, column=1, sticky="ew")
        action_hint = ctk.CTkButton(
            action, text="ⓘ", width=30, height=42, corner_radius=8,
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
        form.pack(fill="x", padx=8, pady=(1, 7))
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
            entry.bind("<KeyRelease>", lambda _event: self._update_order_preview())
            entry.bind("<KeyPress>", lambda _event, widget=entry: widget.configure(text_color=COL_TEXT), add="+")
        self.sl.bind("<KeyPress>", lambda _event: self.sl.configure(text_color=COL_TEXT), add="+")
        self.price.bind("<FocusIn>", self._activate_lo_input, add="+")
        self.sl.bind("<KeyRelease>", self._sl_edited)
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
        brand.pack(fill="x", padx=6, pady=(4, 1))
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
            box, text=label, width=82, font=("Segoe UI", 9, "bold"),
            text_color=COL_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(8, 2), pady=4)
        entry = ctk.CTkEntry(
            box, width=1, font=("Cascadia Mono", 12, "bold"), height=32,
            justify="right", fg_color="#1A1E23", border_width=0,
            text_color=COL_MUTED if dimmed else COL_TEXT,
            placeholder_text=placeholder,
            placeholder_text_color=COL_MUTED,
            takefocus=True,
        )
        entry.insert(0, value)
        entry.grid(row=0, column=1, sticky="ew", padx=(2, 5), pady=4)
        if disabled:
            entry.configure(state="disabled")
        return entry

    def _right_panel(self) -> None:
        header = ctk.CTkFrame(self.right, fg_color="transparent", height=36)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.table_title = ctk.CTkLabel(
            header, text="LỆNH ĐANG CHẠY", font=("Segoe UI", 21, "bold"),
            text_color=COL_TEXT,
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
            fieldbackground=COL_SURFACE_2, rowheight=56, font=("Segoe UI", 18),
            borderwidth=0,
        )
        style.configure(
            "Running.Treeview.Heading", background=COL_SURFACE, foreground=COL_TEXT,
            font=("Segoe UI", 20, "bold"), relief="flat", padding=(10, 10),
        )
        style.map(
            "Running.Treeview.Heading",
            background=[("active", "#252A32"), ("pressed", "#252A32")],
            foreground=[("active", COL_TEXT), ("pressed", COL_TEXT)],
        )
        style.map(
            "Running.Treeview",
            background=[("selected", "#343A43")],
            foreground=[("selected", "#FFFFFF")],
        )
        for mode in ("CKCS REAL", "CKCS PAPER"):
            frame = self.tabs.add(mode)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            tree = ttk.Treeview(frame, show="headings", selectmode="extended", style="Running.Treeview")
            tree.tag_configure("buy_row", background="#193524", foreground=COL_TEXT)
            tree.tag_configure("sell_row", background="#3A2024", foreground=COL_TEXT)
            tree.tag_configure("pending_order", background="#42351B", foreground="#FDE68A")
            tree.tag_configure("position_profit", background="#193524", foreground="#E7F8ED")
            tree.tag_configure("position_loss", background="#3A2024", foreground="#FBEAEC")
            tree.tag_configure("position_flat", background=COL_SURFACE_2, foreground=COL_TEXT)
            tree.tag_configure("position_waiting", background="#4A3B16", foreground="#FFF3B0")
            tree.tag_configure("position_closing", background="#5A4214", foreground="#FFE0B2")
            tree.tag_configure("dnse_order", background="#123F6B", foreground="#D7ECFF")
            tree.tag_configure("partial_order", background="#6A3F08", foreground="#FFE0B2")
            tree.tag_configure("sending_order", background="#0B4F5C", foreground="#B2EBF2")
            tree.tag_configure("error_order", background="#5A1E1E", foreground="#FFCDD2")
            tree.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
            tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_cancel_button())
            tree.bind("<Button-1>", self._clear_running_selection_on_blank, add="+")
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
            self.right, height=320, fg_color=COL_SURFACE, corner_radius=10,
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
            text_color=COL_TEXT, anchor="w",
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
        self.info_tab_dropdown = ctk.CTkOptionMenu(
            info_header,
            values=["PREVIEW", "Manual", "Bot"],
            width=156,
            height=28,
            dynamic_resizing=False,
            font=("Segoe UI", 11, "bold"),
            fg_color="#2563A6",
            button_color="#245C92",
            button_hover_color="#2E73BB",
            dropdown_fg_color=COL_SURFACE_2,
            dropdown_hover_color=COL_GRAY,
            command=self._select_info_tab,
        )
        self.info_tab_dropdown.grid(row=0, column=1)
        self.info_tab_dropdown.grid_remove()
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
            "end", "BOT khởi động OFF. Quyết định chỉ thực thi khi được bật.\n"
        )
        self.log_tabview.set("PREVIEW")
        self.info_tab_selector.set("PREVIEW")
        self.info_tab_dropdown.set("PREVIEW")
        self.after_idle(self._sync_info_selector_mode)
        self._refresh_full_order_preview()
        self._refresh_api_health_panel(self.bridge.read_status())

    def _build_order_preview_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=0)
        # Keep enough real height for P1/P2/P3 and the decision reason. The
        # parent is scrollable, so a short viewport scrolls instead of clipping
        # the final guard/status lines.
        panel = ctk.CTkFrame(parent, height=260, fg_color=COL_SURFACE_2, corner_radius=8)
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
        order_header.grid_columnconfigure(0, weight=0)
        order_header.grid_columnconfigure(1, weight=1)
        order_header.grid_columnconfigure(2, weight=0)
        self.preview_order_title = ctk.CTkLabel(
            order_header, text="--- · PAPER · BUY · MARKET",
            width=1, height=22, font=("Segoe UI", 13, "bold"), text_color=COL_TEXT, anchor="w",
        )
        self.preview_order_title.grid(
            row=0, column=0, sticky="ew", padx=(2, 5)
        )
        self.preview_status_reason = ctk.CTkLabel(
            order_header, text="--", width=1, height=22, font=("Segoe UI", 10),
            text_color=COL_PREVIEW_TEXT, fg_color=COL_SURFACE_2, corner_radius=5,
            anchor="w", justify="left",
        )
        self.preview_status_reason.grid(
            row=0, column=1, sticky="ew", padx=5
        )
        self.preview_status_badge = ctk.CTkLabel(
            order_header, text="CHỜ", width=82, height=24,
            font=("Segoe UI", 9, "bold"), fg_color="#4A3B16",
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
            title_color: str = COL_PREVIEW_TEXT,
            hint: str = "",
        ):
            card = ctk.CTkFrame(metrics, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
            card.grid_columnconfigure(0, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=f"{title}{'  ⓘ' if hint else ''}", height=22,
                font=("Segoe UI", 10, "bold"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=9, pady=(6, 0))
            if title == "KL":
                self.preview_qty_title = title_widget
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            value = ctk.CTkLabel(
                card, text="NA", width=1, height=30,
                font=("Cascadia Mono", 12, "bold"), text_color=COL_TEXT,
                anchor="w", justify="left", wraplength=190,
            )
            value.grid(row=1, column=0, sticky="ew", padx=9, pady=(1, 6))
            return value

        self.preview_live_value = metric_card(0, "GIÁ TT")
        self.preview_entry_value = metric_card(1, "GIÁ VÀO")
        self.preview_qty_value = metric_card(2, "KL")
        self.preview_cash_value = metric_card(3, "TIỀN CK")
        self.preview_fee_value = metric_card(4, "FEE", COL_WARN)
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
        management.grid_rowconfigure(0, weight=1)
        for column in range(5):
            management.grid_columnconfigure(column, weight=1, uniform="preview_management")

        def level_card(column: int, title: str, title_color: str):
            card = ctk.CTkFrame(management, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=0, column=column, sticky="nsew", padx=3)
            card.grid_columnconfigure(0, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=title, width=1, height=22, font=("Segoe UI", 10, "bold"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=9, pady=(7, 0))
            value = ctk.CTkLabel(
                card, text="NA", width=1, height=24, font=("Cascadia Mono", 11, "bold"),
                text_color=title_color, anchor="w",
            )
            value.grid(row=1, column=0, sticky="ew", padx=9, pady=(1, 0))
            detail = ctk.CTkLabel(
                card, text="", width=1, height=22, font=("Segoe UI", 10),
                text_color=COL_PREVIEW_TEXT, anchor="w",
            )
            detail.grid(row=2, column=0, sticky="ew", padx=9, pady=(0, 6))
            return title_widget, value, detail

        _tp_title, self.preview_tp_value, self.preview_tp_detail = level_card(0, "TP MANUAL", COL_GREEN)
        _sl_title, self.preview_sl_value, self.preview_sl_detail = level_card(1, "STOP LOSS", COL_RED)
        self.preview_em_normal, self.preview_normal_value, self.preview_normal_detail = level_card(2, "NORMAL · OFF", COL_RED)
        self.preview_em_high, self.preview_high_value, self.preview_high_detail = level_card(3, "HIGH · OFF", COL_RED)
        self.preview_em_exit, self.preview_exit_value, self.preview_exit_detail = level_card(4, "EXIT SELL · OFF", COL_RED)

        rule_group.grid_columnconfigure(0, weight=1)
        for row in (1, 2, 3, 4):
            rule_group.grid_rowconfigure(row, weight=0)
        rule_header = ctk.CTkFrame(rule_group, height=18, fg_color="transparent")
        rule_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))
        rule_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            rule_header, text="QUYẾT ĐỊNH BOT", height=14, font=("Segoe UI", 11, "bold"),
            text_color="#60A5FA", anchor="w",
        ).grid(row=0, column=0, sticky="w")
        rule_hint = ctk.CTkButton(
            rule_header, text="ⓘ", width=22, height=20, corner_radius=6,
            font=("Segoe UI Symbol", 10, "bold"),
            fg_color="#343A43", hover_color="#4B515B", text_color=COL_TEXT,
        )
        rule_hint.grid(row=0, column=1, sticky="e", padx=(4, 5))
        _HoverHint(
            rule_hint,
            "P1: trạng thái VNINDEX từ dữ liệu 1D DNSE.\n"
            "P2: tín hiệu BUY/SELL từ EMA và RSI.\n"
            "P3: vốn, số position và khóa bảo vệ.\n"
            "Bán 1/3 chỉ thuộc NORMAL/HIGH trong Exit Manager.",
        )
        self.preview_rule_title = ctk.CTkLabel(
            rule_header, text="BOT · OFF", width=72, height=16,
            font=("Segoe UI", 10, "bold"), text_color=COL_RED,
            fg_color=COL_SURFACE_2, corner_radius=5,
        )
        self.preview_rule_title.grid(row=0, column=2, sticky="e")

        def phase_card(row: int, title: str, hint: str = ""):
            card = ctk.CTkFrame(rule_group, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=row, column=0, sticky="nsew", padx=8, pady=2)
            card.grid_columnconfigure(0, weight=0)
            card.grid_columnconfigure(1, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=f"{title}{'  ⓘ' if hint else ''}", font=("Segoe UI", 10, "bold"),
                text_color="#60A5FA", anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="w", padx=(8, 6), pady=(3, 1))
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            return card

        phase1 = phase_card(1, "P1 · VNINDEX")
        self.preview_rule_market = ctk.CTkLabel(
            phase1, text="VNINDEX --", width=1, height=16,
            font=("Cascadia Mono", 11, "bold"), text_color=COL_PREVIEW_TEXT,
            anchor="e", justify="right",
        )
        self.preview_rule_market.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=4)
        self.preview_rule_market_detail = ctk.CTkLabel(
            phase1, text="CHỜ PHÂN LOẠI", font=("Segoe UI", 9),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_market_detail.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.preview_rule_market_detail.grid_remove()

        phase2 = phase_card(
            2,
            "P2 · BUY / SELL",
            "RSI hiển thị giá trị hiện tại và chiều thay đổi so với phiên trước.\n"
            "WAIT nghĩa là hiện chưa xuất hiện điểm cắt đủ điều kiện BUY hoặc SELL.",
        )
        for column in range(3):
            phase2.grid_columnconfigure(column, weight=1, uniform="rule_phase2")
        self.preview_rule_ema = ctk.CTkLabel(
            phase2, text="BUY EMA 3/6 · --/--", width=1, height=22,
            font=("Cascadia Mono", 10, "bold"), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left",
        )
        self.preview_rule_ema.grid(
            row=1, column=0, sticky="ew", padx=(8, 5), pady=(0, 3)
        )
        self.preview_rule_sell_ema = ctk.CTkLabel(
            phase2, text="SELL EMA 3/6 · --/--", width=1, height=22,
            font=("Cascadia Mono", 10, "bold"), text_color=COL_PREVIEW_TEXT,
            anchor="center", justify="center",
        )
        self.preview_rule_sell_ema.grid(
            row=1, column=1, sticky="ew", padx=5, pady=(0, 3)
        )
        self.preview_rule_rsi = ctk.CTkLabel(
            phase2, text="RSI14 -- · WAIT", width=1, height=22,
            font=("Cascadia Mono", 10, "bold"), text_color=COL_PREVIEW_TEXT,
            anchor="e", justify="right",
        )
        self.preview_rule_rsi.grid(
            row=1, column=2, sticky="ew", padx=(5, 8), pady=(0, 3)
        )

        phase3 = phase_card(
            3,
            "P3 · GIỚI HẠN",
            "VỊ THẾ: số mã đang giữ / số mã tối đa. VỐN/MÃ là mức vốn mục tiêu do Phase 1 chia.\n"
            "AUTO 100 CP: hệ thống luôn tính theo vốn Phase 1 trước. Nếu vốn/mã không mua đủ "
            "một lô nhưng NAV và cash còn đủ, hệ thống fallback sang 100 CP.\n"
            "WHIPSAW GUARD: đếm số lần EMA cắt qua lại trong cửa sổ thời gian đã cấu hình. "
            "Ví dụ 1 lần; khóa từ 3 lần trong 7 phiên. Chỉ khóa BUY mới, "
            "không ảnh hưởng position đang giữ.\n"
            "LỖ: số lệnh lỗ liên tiếp / mức khóa mã.",
        )
        self.preview_rule_phase3 = ctk.CTkLabel(
            phase3, text="--/-- · --/MÃ", width=1, font=("Cascadia Mono", 11, "bold"),
            text_color=COL_PREVIEW_TEXT, anchor="e",
        )
        self.preview_rule_phase3.grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=(3, 1)
        )
        phase3.grid_columnconfigure(2, weight=1)
        self.preview_rule_phase3_detail = ctk.CTkLabel(
            phase3, text="AUTO --", font=("Segoe UI", 10, "bold"),
            text_color=COL_PREVIEW_TEXT, anchor="e",
        )
        self.preview_rule_phase3_detail.grid(
            row=0, column=2, sticky="ew", padx=(8, 10), pady=(3, 1)
        )
        self.preview_rule_phase3_guard = ctk.CTkLabel(
            phase3, text="WHIPSAW -- · LOSS --", font=("Segoe UI", 9),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3_guard.grid_remove()

        self.preview_rule_reason = ctk.CTkLabel(
            rule_group, text="CHỜ DỮ LIỆU", width=1, height=26,
            font=("Segoe UI", 11, "bold"), text_color=COL_WARN,
            fg_color=COL_SURFACE_2, corner_radius=6,
            anchor="w", justify="left",
        )
        self.preview_rule_reason.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 6))

        health_group = ctk.CTkFrame(panel, fg_color=COL_SURFACE, corner_radius=7, height=36)
        health_group.grid(row=1, column=0, columnspan=2, sticky="ew", padx=7, pady=(3, 7))
        health_group.grid_propagate(False)
        for column in range(7):
            health_group.grid_columnconfigure(column, weight=1, uniform="health_pills")
        self.preview_health_title = ctk.CTkLabel(
            health_group, text="HEALTH ⓘ", width=1, font=("Segoe UI", 10, "bold"),
            text_color="#60A5FA", anchor="w",
        )
        self.preview_health_title.grid(row=0, column=0, sticky="ew", padx=(10, 3))
        _HoverHint(
            self.preview_health_title,
            "API OK: DNSE đang trả lời bình thường.\n"
            "OTP: chưa xác thực trading token; chỉ cần khi gửi, sửa hoặc hủy lệnh REAL.\n"
            "PRICE: ATO / OPEN / ATC / CLOSED theo phiên hiện tại.",
            placement="inside",
        )

        def health_cell(column: int, text: str) -> ctk.CTkLabel:
            label = ctk.CTkLabel(
                health_group, text=text, width=1, height=24,
                font=("Cascadia Mono", 9, "bold"), text_color=COL_PREVIEW_TEXT,
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
        status = status if isinstance(status, dict) else self.bridge.read_status()
        target = str(symbol or self.symbol.get() or "").strip().upper()
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(target) if isinstance(decisions.get(target), dict) else {}
        details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        checks = details.get("entry_checks") if isinstance(details.get("entry_checks"), dict) else {}
        budget = _number(checks.get("order_budget") or checks.get("available_capital"))
        available_cash = _number(checks.get("available_cash"))
        nav = _number(checks.get("nav"))
        sizing = size_buy_order(
            budget_vnd=budget,
            price_board=entry_price,
            available_cash=available_cash,
            nav=nav,
            force_min_lot_enabled=bool(checks.get("force_min_lot_enabled", False)),
        )
        quantity = sizing.quantity
        forced_minimum = sizing.used_minimum
        if hasattr(self, "quantity") and not self.quantity.get().strip():
            self.quantity.configure(
                placeholder_text=(
                    f"{quantity:,} · {'AUTO' if forced_minimum else 'VỐN'}"
                    if quantity > 0 else "THEO VỐN"
                )
            )
        return quantity, budget, forced_minimum

    def _refresh_full_order_preview(self, status: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "preview_order_title"):
            return
        status = status if isinstance(status, dict) else self.bridge.read_status()
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
        estimated_fee = self._preview_buy_fee(gross, symbol, mode)
        fee_value = _compact_vnd(estimated_fee) if estimated_fee is not None else "--"
        working_dates = status.get("working_dates") or None
        phase, phase_label = market_phase(
            working_dates=working_dates,
            holidays=self.settings.trading_holidays,
        )
        expected_phase = "OPEN" if order_type in {"MARKET", "LO"} else order_type
        due = phase == expected_phase
        if order_type == "MARKET":
            due = due or (phase == "ATO" and self.settings.allow_ato)
            due = due or (phase == "ATC" and self.settings.allow_atc)
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
            route = "KHÔNG ĐẶT"
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
        normal_arm = float(params.get("normal_arm_pct", 7.0) or 7.0)
        normal_giveback = float(params.get("normal_giveback_pct", 3.0) or 3.0)
        high_arm = float(params.get("high_profit_arm_pct", 20.0) or 20.0)
        high_drawdown = float(params.get("high_profit_close_drawdown_pct", 5.0) or 5.0)
        normal_price = entry_price * (1.0 + normal_arm / 100.0) if entry_price > 0 else 0.0
        high_price = entry_price * (1.0 + high_arm / 100.0) if entry_price > 0 else 0.0
        normal_preview_sell = (
            entry_price * (1.0 + (normal_arm - normal_giveback) / 100.0)
            if entry_price > 0 else 0.0
        )
        high_preview_sell = (
            high_price * (1.0 - high_drawdown / 100.0)
            if high_price > 0 else 0.0
        )
        self.preview_normal_value.configure(
            text=(
                f"{_display_price(normal_price)} → {_display_price(normal_preview_sell)}"
                if normal_price > 0 else "CHƯA CÓ GIÁ"
            ),
        )
        self.preview_normal_detail.configure(
            text=f"+{normal_arm:g}/-{normal_giveback:g} · BÁN {float(params.get('normal_sell_pct', 33) or 33):g}%")
        self.preview_high_value.configure(
            text=(
                f"{_display_price(high_price)} → {_display_price(high_preview_sell)}"
                if high_price > 0 else "CHƯA CÓ GIÁ"
            ),
        )
        self.preview_high_detail.configure(
            text=f"+{high_arm:g}/-{high_drawdown:g} · BÁN {float(params.get('high_sell_pct', 33) or 33):g}%")
        em_labels = {
            "normal_protection": "NORMAL",
            "high_profit_protection": "HIGH",
            "indicator_exit": "EXIT SELL",
        }
        em_widgets = {
            "normal_protection": self.preview_em_normal,
            "high_profit_protection": self.preview_em_high,
            "indicator_exit": self.preview_em_exit,
        }
        em_value_widgets = {
            "normal_protection": self.preview_normal_value,
            "high_profit_protection": self.preview_high_value,
            "indicator_exit": self.preview_exit_value,
        }
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(symbol) if isinstance(decisions.get(symbol), dict) else {}
        decision_details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        current_profit = decision_details.get("current_profit_pct")
        peak_profit = decision_details.get("peak_profit_pct")
        signal = str(decision.get("signal") or "--").upper()
        for key, widget in em_widgets.items():
            enabled = bool(self._em_states.get(key, False))
            widget.configure(
                text=f"{em_labels[key]} · {'ON' if enabled else 'OFF'}",
                text_color=COL_GREEN if enabled else COL_MUTED,
            )
            em_value_widgets[key].configure(
                text_color=(
                    COL_RED if key == "indicator_exit" and signal == "SELL"
                    else COL_GREEN if enabled else COL_TEXT
                )
            )
        exit_enabled = bool(self._em_states.get("indicator_exit", False))
        position_quantity = max(0, int(decision_details.get("position_quantity", 0) or 0))
        self._render_exit_sell_preview(signal, exit_enabled, position_quantity)
        if current_profit is not None and self._em_states.get("normal_protection", False):
            self.preview_normal_value.configure(
                text=f"PNL {_number(current_profit):+.1f}% · PEAK -{normal_giveback:g}Đ · BÁN {float(params.get('normal_sell_pct', 33) or 33):g}%"
            )
        if peak_profit is not None and self._em_states.get("high_profit_protection", False):
            self.preview_high_value.configure(
                text=f"PEAK {_number(peak_profit):+.1f}% · CLOSE -{high_drawdown:g}% · BÁN {float(params.get('high_sell_pct', 33) or 33):g}%"
            )
        self._refresh_rule_preview(status, symbol)

    def _refresh_rule_preview(self, status: dict[str, Any], symbol: str) -> None:
        if not hasattr(self, "preview_rule_market"):
            return
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(symbol) if isinstance(decisions.get(symbol), dict) else {}
        details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        indicators = details.get("indicators") if isinstance(details.get("indicators"), dict) else {}

        action = str(decision.get("action") or "WAIT").upper()
        market_state = str(decision.get("market_state") or "UNKNOWN").upper()
        signal = str(decision.get("signal") or "--").upper()
        position_quantity = max(0, int(details.get("position_quantity", 0) or 0))
        self._render_exit_sell_preview(
            signal,
            bool(self._em_states.get("indicator_exit", False)),
            position_quantity,
        )
        signal_label = "BUY" if signal == "BUY" else "SELL" if signal == "SELL" else "WAIT"
        bot_enabled = bool(status.get("bot_enabled", False))
        self.preview_rule_title.configure(
            text=f"BOT · {'ON' if bot_enabled else 'OFF'}",
            text_color=COL_GREEN if bot_enabled else COL_RED,
        )
        market_details = details.get("market") if isinstance(details.get("market"), dict) else {}
        display_state = str(
            market_details.get("display_state")
            or market_details.get("candidate_state")
            or market_state
            or "UNKNOWN"
        ).upper()
        state_labels = {
            "UPTREND": "UPTREND",
            "DOWNTREND": "DOWNTREND",
            "ACCUMULATION": "ACCUMULATION",
            "DISTRIBUTION": "DISTRIBUTION",
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
        exposure = _number(details.get("exposure"))
        exposure_pct = exposure * 100.0 if abs(exposure) <= 1.0 else exposure
        cash_pct = max(0.0, 100.0 - exposure_pct)
        self.preview_rule_market.configure(
            text=f"{state_label} ({exposure_pct:g}/{cash_pct:g})",
            text_color=market_color,
        )
        self.preview_rule_market_detail.configure(
            text="ĐANG XÁC NHẬN" if pending_confirmation else "",
            text_color=COL_WARN if pending_confirmation else COL_PREVIEW_TEXT,
        )

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
            fast_text = f"{fast_number:,.2f}".rstrip("0").rstrip(".")
            slow_text = f"{slow_number:,.2f}".rstrip("0").rstrip(".")
            color = COL_GREEN if fast_number > slow_number else COL_RED if fast_number < slow_number else COL_TEXT
            return f"{prefix} EMA {fast_period}/{slow_period} · {fast_text}{relation}{slow_text}", color

        buy_ema_text, buy_ema_color = ema_preview("BUY", "buy")
        sell_ema_text, sell_ema_color = ema_preview("SELL", "sell")
        rsi_period = int(indicators.get("rsi_period", 14) or 14)
        current_rsi = indicators.get("rsi")
        previous_rsi = indicators.get("rsi_previous")
        try:
            rsi_number = float(current_rsi)
            previous_number = float(previous_rsi)
        except (TypeError, ValueError):
            rsi_number = previous_number = None
        if rsi_number is None:
            rsi_text, rsi_color = f"RSI{rsi_period} -- · {signal_label}", COL_PREVIEW_TEXT
        else:
            arrow = "↑" if previous_number is not None and rsi_number > previous_number else "↓" if previous_number is not None and rsi_number < previous_number else "→"
            rsi_text = f"RSI{rsi_period} {rsi_number:.1f} {arrow} · {signal_label}"
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
        loss_locked = bool(loss_limit and losses >= loss_limit)
        guard_warn = whipsaw_locked or loss_locked
        phase3_parts = [f"{open_positions}/{max_positions or '--'}"]
        if capital > 0:
            phase3_parts.append(f"{_compact_vnd(capital)}/MÃ")
        self.preview_rule_phase3.configure(
            text=" · ".join(phase3_parts),
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
        )
        forced_minimum = phase3_sizing.used_minimum
        whipsaw_status = (
            "OFF" if not whipsaw_on
            else "KHÓA BUY" if whipsaw_locked
            else "OK"
        )
        self.preview_rule_phase3_detail.configure(
            text=(
                f"⚠ AUTO 100 · WHIPSAW {whipsaw_status} · LOSS {losses}/{loss_limit or '--'}"
                if forced_minimum
                else f"WHIPSAW {whipsaw_status} · LOSS {losses}/{loss_limit or '--'}"
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
            "NO_AVAILABLE_CAPITAL": "KHÔNG ĐỦ CASH",
            "MARKET_STATE_UNKNOWN": "CHỜ · STATE CHƯA XÁC NHẬN",
            "WHIPSAW_LOCK": "TẠM KHÓA DO NHIỄU",
            "LOCKED_AFTER_3_LOSSES": "KHÓA SAU 3 LỆNH LỖ",
            "MANUAL_OR_EXTERNAL_POSITION": "VỊ THẾ MANUAL",
            "POSITION_MANAGED_SL_ONLY": "CHỈ THEO DÕI SL",
            "STOP_LOSS": "KÍCH HOẠT SL",
            "INDICATOR_EXIT": "KÍCH HOẠT EXIT SELL",
            "PRICE_PROTECTION": "KÍCH HOẠT BẢO VỆ GIÁ",
            "NORMAL_PROTECTION": "NORMAL",
            "HIGH_PROFIT_PROTECTION": "HIGH",
        }
        reason_text = reason_labels.get(reason, reason.replace("_", " ") if reason else "CHỜ DỮ LIỆU")
        if action == "WAIT" and signal == "SELL":
            reason_text = "CHỜ BUY"
        if reason == "BUY_SIGNAL":
            reason_color = COL_GREEN
        elif reason in {"SELL_SIGNAL", "INDICATOR_EXIT"}:
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
    ) -> None:
        if not enabled:
            value, detail, color = "CHƯA ÁP DỤNG", "ĐANG TẮT", COL_MUTED
        elif position_quantity <= 0:
            value, detail, color = "SAU KHI MUA", "CHỜ TÍN HIỆU", COL_PREVIEW_TEXT
        elif str(signal or "").upper() == "SELL":
            value, detail, color = "SELL", "BÁN HẾT", COL_RED
        else:
            value, detail, color = "CHỜ TÍN HIỆU", "BÁN HẾT", COL_PREVIEW_TEXT
        self.preview_exit_value.configure(text=value, text_color=color)
        self.preview_exit_detail.configure(
            text=detail,
            text_color=COL_MUTED if not enabled else COL_TEXT,
        )

    def _sync_info_selector_mode(self) -> None:
        """Use a compact dropdown only when the main window is narrow."""
        if not self.running:
            return
        if not hasattr(self, "info_tab_selector") or not hasattr(self, "info_tab_dropdown"):
            return
        try:
            if not self.winfo_exists() or not self.info_tab_selector.winfo_exists() or not self.info_tab_dropdown.winfo_exists():
                return
            window_scale = max(0.1, float(ctk.ScalingTracker.get_window_scaling(self)))
        except (AttributeError, TypeError, ValueError, tk.TclError):
            return
        try:
            logical_width = int(self.winfo_width() or 0) / window_scale
            compact = logical_width < 1280
            if compact:
                self.info_tab_selector.grid_remove()
                self.info_tab_dropdown.grid()
            else:
                self.info_tab_dropdown.grid_remove()
                self.info_tab_selector.grid()
        except tk.TclError:
            return

    def _on_log_tab_change(self) -> None:
        active = self.log_tabview.get() if hasattr(self, "log_tabview") else ""
        target = "manual" if active == "Manual" else "bot" if active == "Bot" else ""
        if target:
            self._set_log_unread(target, False)
        if active == "PREVIEW":
            self._refresh_full_order_preview()
        if hasattr(self, "info_tab_selector"):
            self.info_tab_selector.set(active)
        if hasattr(self, "info_tab_dropdown"):
            self.info_tab_dropdown.set(active)

    def _set_log_unread(self, target: str, unread: bool) -> None:
        if target not in getattr(self, "log_tab_keys", {}):
            return
        self.log_tab_unread[target] = bool(unread)
        base = self.log_tab_keys[target]
        label = f"{base} *" if unread else base
        selector = getattr(self, "info_tab_selector", self.log_tabview._segmented_button)
        try:
            for key, button in selector._buttons_dict.items():
                if key == base:
                    button.configure(text=label)
                    break
        except (AttributeError, tk.TclError):
            pass
        dropdown = getattr(self, "info_tab_dropdown", None)
        if dropdown is not None:
            values = [
                "PREVIEW",
                "Manual *" if self.log_tab_unread.get("manual") else "Manual",
                "Bot *" if self.log_tab_unread.get("bot") else "Bot",
            ]
            dropdown.configure(values=values)
            active = self.log_tabview.get() if hasattr(self, "log_tabview") else "PREVIEW"
            selected = next((value for value in values if value.removesuffix(" *") == active), active)
            dropdown.set(selected)

    @staticmethod
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
        last_error = next((str(row.get("last_error") or "") for row in rest_rows if row.get("last_error")), "")

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
            text=f"DAEMON {daemon_state}",
            text_color=COL_GREEN if daemon_ok else COL_WARN if daemon_state == "SYNC" else COL_RED,
        )

        configured = self.real.configured()
        dnse_ok = configured and not last_error
        dnse_text = "READY" if dnse_ok else "N/A" if not configured else "ERROR"
        ws_online = bool(ws.get("connected") and ws.get("authenticated"))
        ws_connecting = bool(ws.get("running")) and not ws_online
        self.preview_health_core.configure(
            text=f"DNSE {dnse_text}",
            text_color=COL_GREEN if dnse_ok else COL_RED if configured else COL_WARN,
        )
        self.preview_health_ws.configure(
            text=f"WS {'ONLINE' if ws_online else 'CONNECTING' if ws_connecting else 'OFFLINE'}",
            text_color=(
                COL_GREEN if ws_online
                else COL_WARN if ws_connecting or not configured
                else COL_RED
            ),
        )

        total_requests = sum(int(row.get("total_requests", 0) or 0) for row in rest_rows)
        rest_ok = total_requests > 0 and isinstance(last_status, (int, float)) and 200 <= int(last_status) < 300
        rest_state = "OK" if rest_ok else "WAIT" if total_requests == 0 else "ERROR"
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
            price_state = "OPEN"
        elif data_frozen:
            price_state = "CLOSED"
        elif tick_age is None:
            price_state = "CHỜ"
        else:
            price_state = f"LIVE {tick_age:.0f}s"
        self.preview_health_trade.configure(
            text=f"PRICE {price_state}",
            text_color=(
                COL_GREEN if data_ok
                else COL_RED if price_state == "CLOSED"
                else COL_PREVIEW_TEXT if tick
                else COL_WARN
            ),
        )

        error_text = str(ws.get("last_error") or last_error or status.get("error") or "")
        self.preview_health_title.configure(
            text="HEALTH LỖI ⓘ" if error_text else "HEALTH ⓘ",
            text_color=COL_RED if error_text else "#60A5FA",
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
            font=("Segoe UI", 17, "bold"), text_color=COL_TEXT,
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
            ("T+2 / CACHE", "#4A3B16", "Cổ phiếu chưa về hoặc lệnh đang chờ trong app."),
            ("DNSE ĐANG KHỚP", "#123F6B", "Lệnh đã gửi DNSE và vẫn đang chờ khớp."),
            ("KHỚP MỘT PHẦN", "#6A3F08", "Mới khớp một phần khối lượng; phần còn lại vẫn chờ."),
            ("LỖI", "#5A1E1E", "Gửi lệnh lỗi hoặc chưa xác định được trạng thái."),
        )
        for index, (label, color, explanation) in enumerate(rows):
            chip = ctk.CTkLabel(
                body, text=label, width=142, height=34,
                font=("Segoe UI", 10, "bold"),
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
            font=("Segoe UI", 10, "bold"),
            fg_color="#343A46", text_color=COL_TEXT, corner_radius=7,
        ).grid(row=rule_row, column=0, sticky="ew", padx=(8, 10), pady=(12, 5))
        ctk.CTkLabel(
            body,
            text=(
                "+7: bảo vệ Normal  |  +20: bảo vệ High\n"
                "EXIT SELL: thoát phần còn lại khi có tín hiệu SELL\n"
                "WAIT: đang chờ  |  ARM: đã kích hoạt  |  DONE: đã xử lý"
            ),
            font=("Segoe UI", 11), text_color=COL_TEXT,
            anchor="w", justify="left", wraplength=340,
        ).grid(row=rule_row, column=1, sticky="ew", padx=(0, 8), pady=(12, 5))

        ctk.CTkLabel(
            top,
            text="Màu chỉ giúp nhận nhanh; tag chữ trên dòng là trạng thái chính xác.",
            font=("Segoe UI", 10), text_color=COL_MUTED,
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
