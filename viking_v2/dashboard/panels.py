from __future__ import annotations

import time
import math
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from ..branding import APP_NAME
from ..rules.business import average_true_range_pct, indicator_snapshot, protect_level
from ..trading.market import VN_TZ, market_now, market_phase, merge_tick_into_daily_bars
from ..trading.portfolio import (
    nav_from_balance, position_quantity as holding_quantity,
    size_buy_order, stock_exposure_limit, validate_quantity,
)
from ..trading.validation import decision_is_fresh, decisions_for_mode, quote_diagnostics, quote_is_fresh
from .view import (
    COL_BORDER, COL_GRAY, COL_GREEN, COL_MUTED, COL_SETTLEMENT_BG, COL_SETTLEMENT_TEXT,
    COL_PREVIEW_TEXT, COL_RED, COL_SURFACE, COL_SURFACE_2, COL_TEXT, COL_WARN, FONT_BOLD,
    COL_TITLE, FONT_PREVIEW_TITLE, FONT_PREVIEW_VALUE, _compact_vnd, _display_price,
    _number, _price_unit, _active_order_notice, _render_order_notice,
)
from .windows import (
    FONT_KEY,
    FONT_MONO_VALUE,
    FONT_VALUE,
    _HoverHint,
    fit_entry_text,
    fit_label_text,
)


def _auto_quantity_feedback(
    checks: dict[str, Any], price: float, budget: float, quantity: int,
    *, missing_bound: bool = False,
) -> dict[str, Any]:
    """Explain existing sizing limits; this never changes the money or quantity."""
    money_ready = "order_budget" in checks or "available_capital" in checks
    priority = checks.get("priority_capital") or {}
    cash = _number(checks.get("available_cash"))
    minimum = price * 100_000 * (1 + _number(checks.get("buy_fee_rate")))
    waiting, reason = False, ""
    if not money_ready:
        waiting, reason = True, "CHỜ TIỀN TÀI KHOẢN"
    elif quantity <= 0:
        if priority.get("reason") == "INVALID_PRIORITY_CAPITAL":
            reason = "KIỂM TRA VỐN PRIORITY"
        elif "available_cash" in checks and cash <= 0:
            reason = "TIỀN KHẢ DỤNG = 0"
        elif "exposure" in checks and _number(checks.get("exposure")) <= 0:
            reason = "P1 CHƯA CHO PHÉP MUA"
        elif "exposure_room" in checks and _number(checks.get("exposure_room")) <= 0:
            reason = "HẾT ROOM P1"
        elif ("available_cash" in checks and price > 0 and not missing_bound
              and cash < minimum):
            # An account short of even one reserved lot is not primarily
            # blocked by another symbol's configured Priority allowance.
            reason = "THIẾU TIỀN CHO 100 CP"
        elif (_number(checks.get("symbol_pending_buy_count")) > 0
              and price > 0 and not missing_bound):
            reason = "BUY ĐANG CHỜ"
        elif budget <= 0 and priority.get("reason") == "PRIORITY_CAPITAL_LIMIT":
            reason = (
                "HẾT HẠN MỨC MÃ"
                if _number(priority.get("committed_vnd")) >= _number(priority.get("buy_limit_vnd"))
                and _number(priority.get("limit_vnd")) > 0
                else "VỐN ĐÃ GIỮ CHO PRIORITY"
            )
        elif budget <= 0:
            reason = "HẾT VỐN NO-COMPOUND" if checks.get("no_compound_limited") else "NGÂN SÁCH MUA = 0"
        elif missing_bound:
            waiting, reason = True, "CHỜ GIÁ TRẦN TÍNH KL"
        elif price <= 0:
            waiting, reason = True, "CHỜ GIÁ TÍNH KL"
        else:
            reason = "HẠN MỨC CHƯA ĐỦ 100 CP" if checks.get("priority_capital_enabled") else "VỐN AUTO CHƯA ĐỦ 100 CP"
    if reason == "BUY ĐANG CHỜ":
        remaining = max(0.0, _number(priority.get("buy_limit_vnd")) - _number(priority.get("committed_vnd")))
        quantity_waiting = int(_number(checks.get("symbol_pending_buy_quantity")))
        limits = (
            f"Hạn mức mã: {_compact_vnd(priority.get('buy_limit_vnd'))} · "
            f"đã mua {_compact_vnd(priority.get('holding_cost_vnd'))} · "
            f"BUY chờ giữ {_compact_vnd(priority.get('pending_cost_vnd'))} · còn {_compact_vnd(remaining)} (gồm phí)."
            if priority else f"Vốn đặt thêm: {_compact_vnd(budget)} chưa phí."
        )
        return {"waiting": False, "pending": True, "reason": reason, "hint": "\n".join([
            f"BUY ĐANG CHỜ · {quantity_waiting:,} CP chưa khớp",
            limits,
            f"100 CP mới cần {_compact_vnd(minimum)} gồm phí, tính tại {_display_price(price)} đ.",
            f"Tiền tài khoản: {_compact_vnd(cash)}; MARKET dự trù giá trần, LO theo giá nhập.",
            "Muốn đặt lại: hủy yêu cầu cũ ở bảng lệnh, chờ xác nhận hủy thành công.",
        ])}
    lines = [reason] if reason else []
    if money_ready:
        lines.append(f"Tiền khả dụng: {cash:,.0f} đ · AUTO: {_compact_vnd(budget)} chưa phí.")
        if price > 0 and not missing_bound:
            lines.append(f"100 CP + phí: {_compact_vnd(minimum)} (dự trù giá {_display_price(price)} đ).")
        if priority.get("limit_vnd"):
            lines.append(
                f"Priority: {_compact_vnd(priority['limit_vnd'])} × {_number(priority.get('use_pct')):g}%"
                f" → {_compact_vnd(priority.get('per_order_limit_vnd', priority.get('buy_limit_vnd')))} / lệnh, gồm phí."
            )
            if _number(priority.get("committed_vnd")) > 0:
                remaining = max(0.0, _number(priority.get("buy_limit_vnd")) - _number(priority.get("committed_vnd")))
                lines.append(
                    f"Đã mua: {_compact_vnd(priority.get('holding_cost_vnd'))} · "
                    f"BUY đang chờ giữ: {_compact_vnd(priority.get('pending_cost_vnd'))} · "
                    f"Còn hạn mức: {_compact_vnd(remaining)} (gồm phí)."
                )
        if _number(checks.get("symbol_pending_buy_count")) > 0:
            lines.append(
                f"Có {int(_number(checks['symbol_pending_buy_count']))} yêu cầu BUY đang chờ, "
                f"còn {int(_number(checks.get('symbol_pending_buy_quantity'))):,} CP chưa khớp."
            )
        if reason in {"THIẾU TIỀN CHO 100 CP", "TIỀN KHẢ DỤNG = 0"}:
            lines.append("Cần tăng tiền khả dụng; hạn mức không phải tiền sẵn có.")
        elif priority and quantity <= 0:
            lines.append(f"Dành cho mã khác/tiết kiệm: {_compact_vnd(priority.get('reserved_cash'))}.")
        if checks.get("priority_capital_enabled") and reason not in {"THIẾU TIỀN CHO 100 CP", "TIỀN KHẢ DỤNG = 0"}:
            lines.append("MARKET tính theo giá trần; LO theo giá nhập.")
    else:
        lines.append("Chưa nhận được số dư của sổ đang xem; không có nghĩa tài khoản hết tiền.")
    return {"waiting": waiting, "reason": reason, "hint": "\n".join(lines),
            "pending": reason == "BUY ĐANG CHỜ"}


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
        }
        for column, (title, title_attr, value_attr, title_color) in enumerate(pnl_specs):
            box = ctk.CTkFrame(pnl_panel, fg_color="transparent")
            box.grid(row=0, column=column, sticky="nsew", padx=5, pady=5)
            title_label = ctk.CTkLabel(
                box, text=title, font=FONT_PREVIEW_TITLE, height=22,
                text_color=title_color, anchor="center",
            )
            title_label.pack(fill="x")
            _HoverHint(title_label, self._ticket_fee_hint if title == "FEE" else pnl_hints[title])
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
        self.lbl_order_value_title = ctk.CTkLabel(
            money_panel, text="VỐN + PHÍ", font=FONT_PREVIEW_TITLE, height=28,
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.lbl_order_value_title.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=2)
        _HoverHint(self.lbl_order_value_title, self._ticket_money_hint)
        self.lbl_order_value = ctk.CTkLabel(
            money_panel, text="NA", font=FONT_PREVIEW_VALUE, height=28,
            text_color=COL_PREVIEW_TEXT, anchor="e",
        )
        self.lbl_order_value.grid(row=0, column=1, sticky="e", padx=(6, 12), pady=2)
        _HoverHint(self.lbl_order_value, self._ticket_money_hint)

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
            form, 0, "KL · AUTO", "", placeholder="AUTO", row=0,
        )
        _HoverHint(self.quantity._viking_title_widget, self._auto_quantity_hint)
        self.quantity.bind("<FocusOut>", lambda _event: self._update_order_preview(), add="+")
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
            brand, text=APP_NAME, font=("Segoe UI", 19, "bold"),
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
        title_widget = ctk.CTkLabel(
            box, text=label, width=96, font=FONT_KEY,
            text_color=COL_TITLE, anchor="w",
        )
        title_widget.grid(row=0, column=0, sticky="w", padx=(8, 2), pady=4)
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
        entry._viking_title_widget = title_widget
        entry._viking_editing = False
        entry.bind("<FocusIn>", lambda _event: setattr(entry, "_viking_editing", True), add="+")
        entry.bind("<FocusOut>", lambda _event: setattr(entry, "_viking_editing", False), add="+")
        # The whole input card is clickable, not just its inner Tk text area.
        for click_target in (box, title_widget, entry._canvas):
            click_target.bind("<Button-1>", lambda _event: entry.focus_set(), add="+")

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
        _HoverHint(
            self.running_legend_button,
            "Bấm xem chú thích màu và trạng thái lệnh.\n"
            "Màu chờ xử lý ưu tiên hơn màu lãi/lỗ; CHỜ KHỚP chưa phải đã mua.",
            placement="inside",
        )
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
        self.info_title = ctk.CTkLabel(
            info_header, text="HỆ THỐNG  ⓘ", font=("Segoe UI", 14, "bold"),
            text_color=COL_TITLE, anchor="w",
        )
        self.info_title.grid(row=0, column=0, sticky="w", padx=(2, 12))
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
        # CTkSegmentedButton.bind raises NotImplementedError; attach the
        # explanation to a stable label, not to the composite tab control.
        _HoverHint(
            self.info_title,
            "PREVIEW: tính thử, không gửi lệnh.\n"
            "Manual: thao tác tay, kể cả lần bị chặn. Bot: lệnh tự động và quản lý thoát.\n"
            "Từ bản này, nhật ký lưu kèm tab trên máy; mở lại app nạp dòng gần nhất. * = có dòng mới chưa đọc.",
            placement="inside",
        )
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
        restore_logs = getattr(self, "_restore_visible_logs", None)
        if callable(restore_logs):
            restore_logs()
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
        _HoverHint(self.preview_status_reason, self._order_status_hint, placement="inside")
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
            # Give the longer money value room without adding a second row.
            metrics.grid_columnconfigure(column, weight=2 if column == 3 else 1, uniform="preview_metrics")

        def metric_card(
            column: int,
            title: str,
            title_color: str = COL_TITLE,
            hint: Any = "",
        ):
            card = ctk.CTkFrame(metrics, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
            card.grid_columnconfigure(0, weight=1)
            title_widget = ctk.CTkLabel(
                card, text=f"{title}{'  ⓘ' if hint else ''}", height=22,
                font=("Segoe UI", 12, "bold", "italic"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=2, pady=(6, 0))
            if title == "KL":
                self.preview_qty_title = title_widget
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            title_widget.bind("<Configure>", lambda _event, widget=title_widget: fit_label_text(
                widget, base_font=("Segoe UI", 12, "bold", "italic"), minimum_size=5), add="+")
            value = ctk.CTkLabel(
                card, text="NA", width=1, height=30,
                font=("Cascadia Mono", 13), text_color=COL_TEXT,
                anchor="w", justify="left", wraplength=0,
            )
            value.grid(row=1, column=0, sticky="ew", padx=2, pady=(1, 6))
            value.bind("<Configure>", lambda _event, widget=value: fit_label_text(
                widget, base_font=("Cascadia Mono", 13), minimum_size=5), add="+")
            if hint:
                _HoverHint(value, hint, placement="inside")
            return value

        self.preview_live_value = metric_card(0, "GIÁ TT", hint=self._quote_health_hint)
        self.preview_entry_value = metric_card(1, "GIÁ VÀO", hint="Giá ước tính khớp để preview TP/SL; LO dùng giá nhập.\nKhi giữ vốn Priority, VỐN + PHÍ dự trù theo giá trần, không phải giá khớp chắc chắn.")
        self.preview_qty_value = metric_card(2, "KL", hint=self._auto_quantity_hint)
        self.preview_cash_value = metric_card(3, "VỐN + PHÍ", hint=self._ticket_money_hint)
        self.preview_fee_value = metric_card(4, "FEE", COL_WARN, self._ticket_fee_hint)
        self.preview_route_value = metric_card(
            5,
            "LỆNH",
            "#60A5FA",
            self._order_lifecycle_hint,
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
        for widget in (self.preview_em_normal, self.preview_normal_value, self.preview_normal_detail):
            _HoverHint(widget, self._protect_preview_hint, placement="inside")
        for widget in (self.preview_em_exit, self.preview_exit_value, self.preview_exit_detail):
            _HoverHint(widget, self._exit_preview_hint, placement="inside")
        self.preview_normal_detail.bind("<Configure>", lambda _event: self._fit_protect_preview(), add="+")
        for widget, font in ((self.preview_normal_value, ("Cascadia Mono", 12)),
                             (self.preview_exit_value, ("Cascadia Mono", 12)),
                             (self.preview_exit_detail, ("Segoe UI", 10))):
            widget.bind("<Configure>", lambda _event, label=widget, base=font:
                        fit_label_text(label, base_font=base), add="+")

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
            card._viking_title_widget = title_widget
            compact_title = {"P1 · VNINDEX": "P1 · VNI", "P3 · VỐN & KHÓA": "P3 · VỐN"}.get(title)
            if compact_title:
                def resize_title(_event):
                    logical_width = card.winfo_width() / card._get_widget_scaling()
                    shown = f"{title.split(' · ')[0] if logical_width < 250 else compact_title if logical_width < 350 else title}  ⓘ"
                    if title_widget.cget("text") != shown:
                        title_widget.configure(text=shown)
                card.bind("<Configure>", resize_title, add="+")
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            return card

        phase1 = phase_card(1, "P1 · VNINDEX", self._market_confirmation_hint)
        self.preview_rule_market = ctk.CTkLabel(
            phase1, text="VNINDEX --", width=1, height=16,
            font=("Segoe UI", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_market.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=4)
        self.preview_rule_market.bind("<Configure>", lambda _event: fit_label_text(
            self.preview_rule_market, base_font=("Segoe UI", 12)), add="+")
        _HoverHint(self.preview_rule_market, self._market_confirmation_hint, placement="inside")
        self.preview_rule_market_detail = ctk.CTkLabel(
            phase1, text="CHỜ PHÂN LOẠI", height=14, font=("Segoe UI", 10),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_market_detail.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.preview_rule_market_detail.grid_remove()
        _HoverHint(self.preview_rule_market_detail, self._market_confirmation_hint, placement="inside")
        # This is a display switch, not the trading override control in RULE.
        self.preview_p1_swap = ctk.CTkButton(
            phase1, text="⇄", width=24, height=20, corner_radius=5,
            font=("Segoe UI Symbol", 12), fg_color="#343A43", hover_color="#4B515B",
            command=self._swap_phase1_preview,
        )
        self.preview_p1_swap.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=3)
        _HoverHint(self.preview_p1_swap,
                   "Đổi góc xem P1: chọn tay ↔ theo rule VNINDEX.\n"
                   "Chỉ xem tham khảo; không đổi setting, vốn hoặc đặt lệnh.", placement="inside")

        def fit_p1_row(_event):
            # Very narrow/DPI-scaled cards use the existing second line for
            # the P1 caption, leaving the percentages a full-width first line.
            narrow = phase1.winfo_width() / phase1._get_widget_scaling() < 250
            if getattr(phase1, "_viking_narrow", None) != narrow:
                phase1._viking_narrow = narrow
                if narrow:
                    phase1._viking_title_widget.grid_remove()
                else:
                    phase1._viking_title_widget.grid()
                self.preview_rule_market.grid(column=0 if narrow else 1, columnspan=2 if narrow else 1,
                                              padx=(8, 4) if narrow else (0, 4))
                note = self.preview_rule_market_detail.cget("text").removeprefix("P1 · ")
                self.preview_rule_market_detail.configure(text=f"P1 · {note}" if narrow else note)
            fit_label_text(self.preview_rule_market, base_font=("Segoe UI", 12))
            fit_label_text(self.preview_rule_market_detail, base_font=("Segoe UI", 10), minimum_size=7)
        phase1.bind("<Configure>", fit_p1_row, add="+")

        phase2 = phase_card(
            2,
            "P2 · BUY / E",
            self._indicator_preview_hint,
        )
        phase2._viking_title_widget.grid(columnspan=2)
        for row, name in ((1, "buy_ema"), (2, "sell_ema"), (3, "rsi")):
            key = ctk.CTkLabel(
                phase2, text="--:", width=1, height=14,
                font=("Segoe UI", 11), text_color=COL_PREVIEW_TEXT, anchor="w",
            )
            key.grid(row=row, column=0, sticky="w", padx=(8, 6), pady=(0, 3) if row == 3 else 0)
            setattr(self, f"preview_rule_{name}_key", key)
            _HoverHint(key, self._indicator_preview_hint, placement="inside")
        self.preview_rule_ema = ctk.CTkLabel(
            phase2, text="-- / --", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_ema.grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=0
        )
        self.preview_rule_sell_ema = ctk.CTkLabel(
            phase2, text="-- / --", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_sell_ema.grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=0
        )
        self.preview_rule_rsi = ctk.CTkLabel(
            phase2, text="--", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_rsi.grid(
            row=3, column=1, sticky="ew", padx=(0, 8), pady=(0, 3)
        )
        for value in (self.preview_rule_ema, self.preview_rule_sell_ema, self.preview_rule_rsi):
            value.bind("<Configure>", lambda _event, widget=value: fit_label_text(
                widget, base_font=("Cascadia Mono", 12), minimum_size=7), add="+")
            _HoverHint(value, self._indicator_preview_hint, placement="inside")

        phase3 = phase_card(
            3,
            "P3 · VỐN & KHÓA",
            self._entry_capital_hint,
        )
        self.preview_rule_phase3 = ctk.CTkLabel(
            phase3, text="--/-- · --/MÃ", width=1, height=18, font=("Segoe UI", 11),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3.grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=4
        )
        self.preview_rule_phase3.bind("<Configure>", lambda _event: fit_label_text(
            self.preview_rule_phase3, base_font=("Segoe UI", 11), minimum_size=7), add="+")
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
        _HoverHint(self.preview_rule_reason, self._rule_decision_hint, placement="inside")

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
            self._api_health_hint,
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
        _HoverHint(self.preview_health_trade, self._quote_health_hint, placement="inside")

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
        missing_bound = False
        if checks.get("priority_capital_enabled"):
            minimum_room = min(minimum_room, budget)
            order_type = self.order_type.get() if hasattr(self, "order_type") else "MARKET"
            if str(order_type).upper() != "LO":
                bound = _number(checks.get("buy_budget_price"))
                missing_bound = bound <= 0
                entry_price = max(entry_price, bound)
        sizing = size_buy_order(
            budget_vnd=budget,
            price_board=0.0 if missing_bound else entry_price,
            available_cash=available_cash,
            nav=nav,
            force_min_lot_enabled=bool(checks.get("force_min_lot_enabled", False)),
            minimum_order_room_vnd=minimum_room,
            buy_fee_rate=_number(checks.get("buy_fee_rate")),
        )
        quantity = sizing.quantity
        forced_minimum = sizing.used_minimum
        self._preview_auto_feedback = _auto_quantity_feedback(
            checks, entry_price, budget, quantity, missing_bound=missing_bound,
        )
        if hasattr(self, "quantity"):
            manual = bool(self.quantity.get().strip())
            title_widget = getattr(self.quantity, "_viking_title_widget", None)
            if title_widget is not None:
                title_widget.configure(text="KL · TAY" if manual else "KL · AUTO")
            quantity_text = f"{quantity:,} CP" if quantity > 0 else "AUTO"
            editing = bool(getattr(self.quantity, "_viking_editing", getattr(self.quantity, "_is_focused", False)))
            # CTkEntry.configure(placeholder_text=...) activates a placeholder
            # even while focused. Native typing then becomes invisible to get()
            # and the next preview refresh overwrites it. Never configure it
            # during editing, and avoid re-inserting an unchanged suggestion.
            if (not manual and not editing
                    and getattr(self.quantity, "_viking_auto_placeholder", None) != quantity_text):
                self.quantity.configure(placeholder_text=quantity_text)
                self.quantity._viking_auto_placeholder = quantity_text
                fit_callback = getattr(self.quantity, "_viking_fit_text", None)
                if callable(fit_callback):
                    fit_callback()
        return quantity, budget, forced_minimum

    def _order_status_hint(self) -> str:
        notice = _active_order_notice(self)
        if notice:
            return str(notice["hint"])
        reason = str(self.preview_status_reason.cget("text") or "")
        manual_feedback = getattr(self, "_preview_manual_feedback", {})
        if reason and reason == manual_feedback.get("reason"):
            return str(manual_feedback.get("hint") or reason)
        feedback = getattr(self, "_preview_auto_feedback", {})
        if reason == feedback.get("reason"):
            return str(feedback.get("hint") or reason)
        return reason or "Chờ tính preview; chưa gửi lệnh."

    @staticmethod
    def _order_lifecycle_hint() -> str:
        return (
            "PREVIEW / ĐẶT: mới kiểm tra, chưa gửi lệnh.\n"
            "CACHE / CHỜ GỬI / CHỜ OTP: yêu cầu còn trong app.\n"
            "ĐÃ GỬI: DNSE đã nhận; chưa có nghĩa đã khớp.\n"
            "KHỚP x/y: đã mua/bán x trên y CP đặt; x < y là khớp một phần.\n"
            "Telegram BUY báo đã xếp yêu cầu, không xác nhận khớp."
        )

    def _rule_decision_hint(self) -> str:
        decision = getattr(self, "_preview_rule_decision", {})
        decision = decision if isinstance(decision, dict) else {}
        details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        window = details.get("buy_window") if isinstance(details.get("buy_window"), dict) else {}
        status_text = str(details.get("status_text") or "")
        if decision.get("reason") == "BUY_WINDOW_WAIT":
            status_text = f"Có tín hiệu; chờ từ {window.get('start') or 'giờ mua đã cài'} và kiểm tra lại điều kiện."
        elif not decision:
            status_text = "Chưa có quyết định mới; xem hint GIÁ/HEALTH để biết dữ liệu có hợp lệ không."
        return "\n".join(filter(None, (
            status_text,
            "Có tín hiệu ≠ đã gửi lệnh. Bị chặn/chờ giờ: chưa gửi.",
            "BUY đủ điều kiện vẫn cần BOT ON, vốn và OTP REAL hợp lệ.",
            "Đã gửi/khớp: xem cột Trạng thái và KL KHỚP trong bảng lệnh.",
        )))

    def _health_hint_status(self) -> dict[str, Any]:
        bridge = getattr(self, "bridge", None)
        if bridge is not None:
            try:
                status = bridge.read_status()
                if isinstance(status, dict):
                    return status
            except (OSError, ValueError):
                pass
        status = getattr(self, "_preview_health_status", {})
        return status if isinstance(status, dict) else {}

    def _quote_health_hint(self) -> str:
        status = self._health_hint_status()
        symbol = self.symbol.get().strip().upper()
        ticks = status.get("ticks") if isinstance(status.get("ticks"), dict) else {}
        tick = ticks.get(symbol)
        details = quote_diagnostics(tick, symbol)
        source = {"WS": "WS (WebSocket)", "REST": "REST (API dự phòng)",
                  "CLOSE": "giá đóng cửa", "UNKNOWN": "chưa rõ"}[details["source"]]
        try:
            received = datetime.fromtimestamp(details["observed_at"], VN_TZ).strftime("%H:%M:%S")
        except (TypeError, ValueError, OverflowError, OSError):
            received = "--"
        age = details["age_seconds"]
        age_text = f"{age:.1f}s trước" if age is not None and age >= 0 else "không hợp lệ" if age is not None else "--"
        phases = status.get("symbol_phases") if isinstance(status.get("symbol_phases"), dict) else {}
        phase = str(phases.get(symbol) or status.get("market_status") or "").upper()
        if phase in {"CLOSED", "LUNCH", "BREAK", "OFFLINE"}:
            state = "Ngoài phiên: giá chỉ để xem, không đánh giá chậm."
        else:
            state = "HỢP LỆ" if details["valid"] else f"BỊ LOẠI: {details['reason_text']}"
        return (
            f"{symbol} · nguồn: {source}\n"
            f"Nhận lúc {received} (giờ VN) · {age_text}\n"
            f"{state} · giới hạn {details['max_age_seconds']:g}s trong phiên.\n"
            "WS OK chỉ là kết nối; giá từng mã được kiểm tra riêng. Giá này không bảo đảm khớp."
        )

    def _api_health_hint(self) -> str:
        status = self._health_hint_status()
        phase = str(status.get("market_status") or "").upper()
        context = status.get("cycle_error_context") if isinstance(status.get("cycle_error_context"), dict) else {}
        if phase == "CALENDAR_LOADING":
            extra = "LỊCH CHỜ: đang tải lịch, chưa phải lỗi."
        elif phase == "CALENDAR_UNKNOWN":
            extra = "LỊCH LỖI: chưa có lịch giao dịch dùng được."
        elif status.get("error"):
            stage = {
                "ACCOUNT_SNAPSHOT": "đọc tiền/danh mục",
                "MARKET_DATA": "đọc giá",
                "INDICATORS": "tính EMA/RSI/ATR",
                "PORTFOLIO": "tính vốn",
                "RULE_EVALUATION": "kiểm tra rule",
                "RULE_STATE": "lưu trạng thái rule",
            }.get(str(context.get("stage") or ""), "chưa rõ bước lỗi")
            extra = f"Lỗi chu kỳ: {context.get('symbol') or '--'} · {stage} · {context.get('exception_type') or '--'}."
        else:
            extra = "API OK là DNSE đang trả lời; không bảo đảm giá từng mã còn mới."
        return (
            self._quote_health_hint() + "\n" + extra
            + "\nOTP: cần khi gửi/sửa/hủy lệnh REAL; PAPER không cần OTP."
            + "\nMất/phục hồi giá: có ghi trong daemon.log, không ghi mỗi tick."
        )

    def _ticket_money_hint(self) -> str:
        return str(getattr(self, "_preview_ticket_money", {}).get("hint") or "Chờ tính vốn và phí của lệnh đang xem.")

    def _ticket_fee_hint(self) -> str:
        money = getattr(self, "_preview_ticket_money", {})
        if money.get("fee") is None:
            return "Chưa có giá/khối lượng hoặc phí DNSE; dấu — không phải miễn phí."
        return f"Phí mua {money['fee']:,.0f} đ đã nằm trong VỐN + PHÍ; không cộng lần nữa. Không gồm phí/thuế bán."

    def _auto_quantity_hint(self) -> str:
        return (
            "KL AUTO: ô trống; số CP hiện mờ là gợi ý theo ngân sách, chưa phải lệnh đã mua.\n"
            "KL TAY: nhập 100, 200… CP; vẫn giữ vốn Priority, không tự chia lại hạn mức. Xóa hết để trở lại AUTO.\n"
            + str((getattr(self, "_preview_manual_feedback", {}) if self.quantity.get().strip()
                   else getattr(self, "_preview_auto_feedback", {})).get("hint", "Chờ tính vốn."))
        )

    def _book_preview_status(self, status: dict[str, Any] | None = None) -> dict[str, Any]:
        """Select the viewed book, never borrow a decision from the other book."""
        status = status if isinstance(status, dict) else self.bridge.read_status()
        mode = str(self.mode.get()).upper() if hasattr(self, "mode") else "PAPER"
        selected = decisions_for_mode(status, mode, default_paper=getattr(
            getattr(self, "settings", None), "paper_mode", True,
        ))
        return {**status, "decisions": selected}

    def _preview_indicator_details(self, status: dict[str, Any], symbol: str) -> dict[str, Any]:
        """Read-only display fallback: daily bars are not trading decisions."""
        from datetime import datetime
        from ..rules.business import StaticRuleParameters

        symbol = str(symbol or "").strip().upper()
        status = self._book_preview_status(status)
        decision = (status.get("decisions") or {}).get(symbol) or {}
        details = dict(decision.get("details") or {})
        mode = str(self.mode.get()).upper() if hasattr(self, "mode") else "PAPER"
        if (details.get("updated_at") or decision.get("timestamp")) and not decision_is_fresh(decision, symbol, mode):
            details = {}  # A stopped daemon must not pin old EMA/RSI over newer preview bars.
        params = StaticRuleParameters.from_dict(self.settings.rule_parameters)
        expected_periods = {
            "buy_ema_fast_period": params.buy_ema_fast, "buy_ema_slow_period": params.buy_ema_slow,
            "sell_ema_fast_period": params.sell_ema_fast, "sell_ema_slow_period": params.sell_ema_slow,
            "rsi_period": params.rsi_period,
        }
        indicators = dict(details.get("indicators") or {}) if isinstance(details.get("indicators"), dict) else {}
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
        params = self.settings.rule_parameters
        text = (
            "EMA: nhanh so với chậm. Ví dụ 73,76 > 72,70; chưa đủ để tự mua, còn cần điểm cắt và các điều kiện khác.\n"
            f"Lọc RSI: BUY {'BẬT' if params.get('buy_signal_use_rsi', True) else 'TẮT'} / "
            f"E {'BẬT' if params.get('sell_signal_use_rsi', True) else 'TẮT'}. ↑/↓ so với nến trước; đây không phải công tắc tự mua/bán.\n"
            "Bot OFF vẫn tính chỉ số. Preview không tạo tín hiệu hoặc đặt lệnh."
        )
        if source == "MISSING":
            return text + "\nDấu --: chưa đủ nến; chờ daemon tải lịch sử."
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
            "ATR14: mức biến động 14 nến ngày đã đóng; không dùng nến hôm nay chưa đóng.\n"
            "Dynamic: START = ATR × hệ số bắt đầu; LÙI = ATR × hệ số lùi. Ví dụ ATR 4% × 0,55 → START 2,2%.\n"
            f"Phiên cuối: {asof}. -- = chưa đủ dữ liệu, không phải 0."
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
        manual_view = bool(current.get("manual_view", current.get("override_enabled")))
        alternate = bool(current.get("alternate_view"))
        if manual_view:
            headline = f"TỶ TRỌNG CHỌN TAY: CP {active_pct:g}% / TIỀN {100 - active_pct:g}%.\n"
            explanation = "Tỷ trọng nhập trong RULE, không phải xu hướng VNINDEX thực tế.\n"
        elif current.get("confirmation_pending"):
            from ..rules.business import StaticRuleParameters
            candidate_pct = StaticRuleParameters.from_dict(self.settings.rule_parameters).exposure.get(state)
            target = f" (CP {candidate_pct * 100:g}% / TIỀN {100 - candidate_pct * 100:g}%)" if candidate_pct is not None else ""
            explanation = (
                f"Xác nhận {labels.get(state, 'ĐANG TÍNH')}: {count}/{required} phiên{target}. "
                f"Cần {required} phiên liên tiếp mới đổi; đang chờ vẫn dùng {active}.\n"
            )
        else:
            explanation = "Hiện không có trạng thái mới đang chờ xác nhận.\n"
        if alternate:
            headline = headline.replace("ĐANG DÙNG:", "THEO RULE:").rstrip() + " THAM KHẢO.\n"
        if current.get("override_enabled") and not manual_view:
            explanation = explanation.rstrip() + " Đang giao dịch theo tỷ trọng chọn tay.\n"
        elif alternate and manual_view:
            explanation = explanation.rstrip() + " Đang giao dịch theo rule VNINDEX.\n"
        nav = allocation.get("nav")
        example = (
            f"{allocation.get('mode', '')}: NAV {_compact_vnd(nav)} → CP tối đa "
            f"{_compact_vnd(allocation.get('stock_limit'))}, giữ tiền {_compact_vnd(allocation.get('cash_reserve'))}.\n"
            if nav is not None else "Chờ snapshot tài khoản để tính số tiền theo P1.\n"
        )
        return (f"{headline}{explanation}"
                "CP% / TIỀN% tính trên NAV = tiền + giá trị cổ phiếu của sổ đang xem.\n"
                f"{example}"
                "⇄ chỉ đổi góc xem, không đổi setting. Đây là giới hạn vốn, không phải lệnh mua/bán.")

    def _entry_capital_hint(self) -> str:
        summary = getattr(self, "_preview_entry_summary", "chờ dữ liệu")
        # The ticket hint already explains money; this hint explains slots.
        return (
            f"{summary}.\n"
            "Mã BOT 0/4 = đang giữ hoặc chờ mua 0 mã, tối đa 4 mã; không phải số lệnh.\n"
            "Vốn mua là gợi ý cho mã đang chọn, chưa phí. Ví dụ 50 triệu × 90% / 5 mã = 9 triệu/mã; Priority dùng hạn mức riêng.\n"
            "WHIPSAW = khóa BUY khi EMA cắt qua lại quá nhiều; LỖ = chuỗi lỗ / ngưỡng khóa. Không chặn SELL.\n"
            "MANUAL nhập KL tự chọn, vẫn kiểm tra tiền/phí và điều kiện lệnh; không dùng giới hạn số lần BUY BOT."
        )

    def _protect_preview_hint(self) -> str:
        preview = getattr(self, "_preview_protect_prices", {})
        arm, sell = preview.get("arm", 0), preview.get("sell", 0)
        example = (
            f"Nếu đỉnh chỉ tới ARM {_display_price(arm)} đ → mốc bán {_display_price(sell)} đ.\n"
            if arm > 0 else "Chưa có giá vào để tính ARM và mốc bán.\n"
        )
        actual = preview.get("actual_sell")
        if actual is not None and actual > 0:
            example = f"Mốc bán của vị thế: {_display_price(actual)} đ; theo đỉnh đã ghi nhận.\n"
        return (
            "ARM: mức lãi bắt đầu bảo vệ theo đỉnh. BÁN: giá lùi khỏi đỉnh kích hoạt PROTECT.\n"
            f"{example}"
            "Đỉnh cao hơn → mốc bán tăng theo; đây không phải giá khớp được bảo đảm.\n"
            f"ARM {preview.get('arm_pct', 7):g}% · TRAIL {preview.get('trail_pct', 2.5):g}% · "
            f"BÁN {preview.get('sell_pct', 100):g}% phần còn lại. Khi bật PROTECT: AUTO tự bán; ALERT chỉ báo."
            " Dynamic có thể bảo vệ trước ARM."
        )

    def _fit_protect_preview(self) -> None:
        label = self.preview_normal_detail
        full, compact = getattr(self, "_preview_protect_detail", ("", ""))
        if isinstance(label, ctk.CTkLabel) and full:
            narrow = label.winfo_width() / label._get_widget_scaling() < 300
            shown = compact if narrow else full
            if label.cget("text") != shown:
                label.configure(text=shown)
            fit_label_text(label, base_font=("Segoe UI", 10))

    def _exit_preview_hint(self) -> str:
        preview = getattr(self, "_preview_exit_state", {})
        price = _number(preview.get("price"))
        price_text = f"{_display_price(price)} đ" if price > 0 else "chưa có giá"
        signal = preview.get("signal", "--")
        enabled = bool(preview.get("enabled"))
        quantity = int(preview.get("quantity", 0))
        policy = preview.get("policy", "ALERT")
        status = ("CHƯA CÓ VỊ THẾ" if quantity <= 0 else
                  "CÓ TÍN HIỆU THOÁT" if signal == "SELL" else "CHỜ TÍN HIỆU THOÁT")
        behavior = ("tự gửi bán 100% phần còn lại khi có tín hiệu và đủ điều kiện lệnh."
                    if policy == "AUTO" else "chỉ báo tín hiệu, không đặt lệnh.")
        behavior = (f"{policy}: {behavior}" if enabled else
                    f"OFF: chỉ preview. Khi bật {policy}: {behavior}")
        return (
            "E thoát theo EMA SELL/RSI, không có một giá kích hoạt cố định như SL.\n"
            f"Giá thị trường: {price_text} · {status}.\n"
            f"{behavior}\n"
            "Giá hiển thị để tham khảo, không bảo đảm giá khớp. Chưa có vị thế thì không gửi SELL."
        )

    def _exit_preview_quantity(self, details: dict[str, Any], symbol: str) -> int:
        # Account holdings exist even when there is no bot decision yet.
        snapshot = getattr(self, "snapshots", {}).get(self.mode.get())
        if snapshot is not None:
            return sum(holding_quantity(row) for row in snapshot[1]
                       if isinstance(row, dict) and str(row.get("symbol", "")).upper() == symbol.upper())
        return max(0, int(_number(details.get("position_quantity"))))

    def _exit_preview_signal(self, decision: dict[str, Any], symbol: str) -> str:
        details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        if (decision.get("timestamp") or details.get("updated_at")) and not decision_is_fresh(decision, symbol, self.mode.get()):
            return "--"
        return str(decision.get("signal") or "--").upper()

    def _phase1_capital_preview(self, details: dict[str, Any], *, manual: bool | None = None) -> dict[str, Any]:
        """Display the current book's P1 envelope, not its actual stock/cash mix."""
        from ..rules.business import StaticRuleParameters
        params = StaticRuleParameters.from_dict(self.settings.rule_parameters)
        mode = self.mode.get()
        state_fn = getattr(getattr(self, "rule_state", None), "confirmed_market_state", None)
        state = state_fn() if callable(state_fn) else "UNKNOWN"
        override = self.settings.market_phase_override_enabled if manual is None else manual
        market = details.get("market") if isinstance(details.get("market"), dict) else {}
        if override:
            exposure = self.settings.market_phase_override_exposure_pct / 100.0
            state = self.settings.market_phase_override
        elif callable(state_fn):
            exposure = params.exposure.get(state, 0.0)
        elif manual is False:
            # Never reuse an override's exposure or selected state as the
            # automatic market result when the rule-state reader is absent.
            state = str(market.get("confirmed_state") or "UNKNOWN").upper()
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
            "state_authoritative": bool(callable(state_fn) or override or manual is False),
            "stock_limit": stock_limit, "cash_reserve": nav - stock_limit if nav is not None else None,
        }

    def _swap_phase1_preview(self) -> None:
        """Switch the P1 view only; never save settings or refresh an order."""
        self._preview_p1_alternate = not getattr(self, "_preview_p1_alternate", False)
        details, state = getattr(self, "_preview_p1_data", ({}, "UNKNOWN"))
        self._render_phase1_preview(details, state)

    def _render_phase1_preview(self, details: dict[str, Any], market_state: str = "UNKNOWN") -> None:
        self._preview_p1_data = (details, market_state)
        alternate = bool(getattr(self, "_preview_p1_alternate", False))
        override = bool(self.settings.market_phase_override_enabled)
        manual_view = override != alternate
        market = dict(details.get("market") or {}) if isinstance(details.get("market"), dict) else {}
        confirmation_fn = getattr(getattr(self, "rule_state", None), "market_confirmation", None)
        if callable(confirmation_fn):
            confirmation = confirmation_fn(int(self.settings.rule_parameters.get("confirm_sessions", 3)))
            market.update(confirmed_state=confirmation["confirmed"], candidate_state=confirmation["candidate"],
                          confirmation_count=confirmation["count"], confirmation_required=confirmation["required"],
                          confirmation_pending=confirmation["pending"])
        elif override and not manual_view:
            # Older decision payloads hide pending confirmation in override.
            # Read its explicit automatic state, never its override state.
            confirmed = str(market.get("confirmed_state") or "UNKNOWN").upper()
            candidate = str(market.get("candidate_state") or market.get("auto_display_state") or "UNKNOWN").upper()
            market.update(candidate_state=candidate,
                          confirmation_pending=confirmed == "UNKNOWN" or candidate != confirmed)
        market.update(override_enabled=override, manual_view=manual_view, alternate_view=alternate)
        if manual_view:
            market["confirmation_pending"] = False
        self._preview_market_confirmation = market
        allocation = self._phase1_capital_preview(details, manual=manual_view) if alternate else self._phase1_capital_preview(details)
        self._preview_market_budget = allocation
        display_state = str(allocation["state"] if allocation["state_authoritative"] else market_state or "UNKNOWN").upper()
        labels = {"UPTREND": "TĂNG", "DOWNTREND": "GIẢM", "ACCUMULATION": "TÍCH LŨY",
                  "DISTRIBUTION": "PHÂN PHỐI", "TRANSITION": "ĐANG TÍNH", "UNKNOWN": "ĐANG TÍNH"}
        color = (COL_GREEN if display_state in {"UPTREND", "ACCUMULATION"} else
                 COL_RED if display_state in {"DOWNTREND", "DISTRIBUTION"} else COL_WARN)
        pct = allocation["exposure_pct"]
        value = f"CP: {pct:g}% · TIỀN: {max(0.0, 100 - pct):g}%"
        if not manual_view:
            value = f"{labels.get(display_state, 'ĐANG TÍNH')} · {value}"
        self.preview_rule_market.configure(text=value, text_color=COL_PREVIEW_TEXT if manual_view else color)
        pending = bool(market.get("confirmation_pending"))
        notes = ["TỶ TRỌNG CHỌN TAY"] if manual_view else []
        if pending:
            count = max(0, int(market.get("confirmation_count", 0) or 0))
            required = max(1, int(market.get("confirmation_required", 3) or 3))
            candidate = labels.get(str(market.get("candidate_state", "")).upper(), "ĐANG TÍNH")
            notes.append(f"XÁC NHẬN {candidate} · {count}/{required} PHIÊN")
        elif not manual_view:
            notes.append("P1 TỰ ĐỘNG")
        if alternate:
            notes = ["CHỌN TAY · THAM KHẢO" if manual_view else "THEO RULE · THAM KHẢO"]
            if pending:
                notes = [f"THEO RULE · CHỜ {candidate} {count}/{required}"]
        else:
            volume = str(market.get("volume_confidence") or "OFF").upper()
            if volume != "OFF":
                notes.append(f"VOLUME {volume}")
            updated = str(details.get("updated_at") or "")
            if len(updated) >= 16:
                notes.append(updated[11:16])
        narrow = getattr(getattr(self.preview_rule_market, "master", None), "_viking_narrow", False)
        self.preview_rule_market_detail.configure(
            text=("P1 · " if narrow else "") + " · ".join(notes),
            text_color=COL_WARN if pending or manual_view or alternate else COL_PREVIEW_TEXT,
        )
        self.preview_rule_market_detail.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 3))
        fit_label_text(self.preview_rule_market, base_font=("Segoe UI", 12))
        fit_label_text(self.preview_rule_market_detail, base_font=("Segoe UI", 10), minimum_size=7)

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
        # Keep the AUTO hint current even when the operator types a MANUAL
        # quantity or switches books; never show the previous book's money.
        suggested_quantity, suggested_budget, suggested_minimum = self._suggested_order_quantity(entry_price, status, symbol)
        if auto_quantity:
            quantity, budget, forced_minimum = suggested_quantity, suggested_budget, suggested_minimum
            quantity_error = ""
        else:
            try:
                quantity = int(raw_quantity)
                quantity_error = ""
            except ValueError:
                quantity = 0
                quantity_error = "KHỐI LƯỢNG KHÔNG HỢP LỆ"
        valid_quantity, quantity_reason, _normalized = validate_quantity(quantity)
        self._preview_manual_feedback = (
            self._manual_buy_capital_feedback(
                symbol, mode, quantity, order_type, entry_price,
                tick=tick,
            ) if valid_quantity and not price_error and hasattr(self, "_manual_buy_capital_feedback") else {}
        )
        manual_capital_reason = str(self._preview_manual_feedback.get("reason") or "")
        manual_waiting = bool(self._preview_manual_feedback.get("waiting"))
        auto_block_reason = ""
        auto_waiting = False
        pending_block = False
        if auto_quantity and quantity <= 0:
            feedback = getattr(self, "_preview_auto_feedback", {})
            auto_waiting = bool(feedback.get("waiting"))
            pending_block = bool(feedback.get("pending"))
            auto_block_reason = str(feedback.get("reason") or "CHỜ TÍNH KHỐI LƯỢNG")
        gross = entry_price * max(0, quantity) * 1000.0
        preview_capital = gross
        money = self._ticket_money_preview(symbol, mode, order_type, quantity, entry_price, tick)
        estimated_fee = money["fee"]
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
        ) or price_error or manual_capital_reason
        if auto_quantity and quantity <= 0:
            invalid_reason = price_error or auto_block_reason
        if not symbol:
            invalid_reason = "Chưa chọn mã chứng khoán"
        auto_waiting = auto_waiting and bool(symbol) and not price_error

        if auto_waiting or manual_waiting:
            badge, badge_bg, badge_fg = "CHỜ", "#4A3B16", "#FFF3B0"
            reason, route = invalid_reason, "CHỜ"
        elif pending_block and not price_error and not quantity_error and bool(symbol):
            badge, badge_bg, badge_fg = "BUY CHỜ", "#4A3B16", "#FFF3B0"
            reason, route = auto_block_reason, "CHỜ"
        elif invalid_reason:
            badge, badge_bg, badge_fg = "CHẶN" if manual_capital_reason else "LỖI", "#5A1E1E", "#FFCDD2"
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
        if auto_waiting or manual_waiting:
            button_text, button_bg, button_hover, button_border = "CHỜ DỮ LIỆU", "#4A3B16", "#605020", COL_WARN
        elif pending_block and not price_error and bool(symbol):
            button_text, button_bg, button_hover, button_border = "BUY ĐANG CHỜ", "#4A3B16", "#605020", COL_WARN
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
            text=f"{quantity:,}" if quantity > 0 else "—" if auto_waiting else "< 100" if auto_quantity else "0",
            text_color=COL_WARN if auto_quantity else COL_TEXT,
        )
        self.preview_cash_value.configure(
            text=(
                _compact_vnd(money["total"]) if money["total"] is not None else
                "CHỜ GIÁ" if money["waiting_price"] else "CHỜ PHÍ" if money["gross"] > 0 else "--"
            )
        )
        fit_label_text(self.preview_cash_value, base_font=("Cascadia Mono", 13), minimum_size=5)
        self.preview_fee_value.configure(
            text=fee_value,
            text_color=COL_WARN,
        )
        fit_label_text(self.preview_fee_value, base_font=("Cascadia Mono", 13), minimum_size=5)
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
        self._preview_protect_prices = {"arm": normal_price, "sell": normal_preview_sell,
                                        "arm_pct": normal_arm, "trail_pct": normal_giveback,
                                        "sell_pct": normal_sell}
        self.preview_normal_value.configure(
            text=(
                f"ARM: {_display_price(normal_price)} · BÁN: {_display_price(normal_preview_sell)}"
                if normal_price > 0 else "CHƯA CÓ GIÁ"
            ),
        )
        self.preview_normal_detail.configure(
            text=(
                f"{normal_policy} · ARM {normal_arm:g}% · TRAIL {normal_giveback:g}% · BÁN {normal_sell:g}%"
                f"{' · LẶP' if normal_repeat and normal_sell < 100 else ''}"
            ),
        )
        self._preview_protect_detail = (
            f"{normal_policy} · ARM {normal_arm:g}% · TRAIL {normal_giveback:g}% · BÁN {normal_sell:g}%"
            f"{' · LẶP' if normal_repeat and normal_sell < 100 else ''}",
            f"{normal_policy} · BÁN {normal_sell:g}% · LÙI {normal_giveback:g}%"
            f"{' · LẶP' if normal_repeat and normal_sell < 100 else ''}",
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
        signal = self._exit_preview_signal(decision, symbol)
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
        position_quantity = self._exit_preview_quantity(decision_details, symbol)
        self._render_exit_sell_preview(
            signal, exit_enabled, position_quantity, indicator_exit_policy,
        )
        if current_profit is not None and _number(decision_details.get("normal_trigger_price")) > 0:
            protected = decision_details.get("normal_trigger_price")
            protect_state = str(decision_details.get("normal_state", "WAIT") or "WAIT").upper()
            effective_trail = decision_details.get("normal_effective_trail_pct")
            self._preview_protect_prices["actual_sell"] = _number(protected)
            state_label = {"WAIT": "CHỜ", "ARM": "ĐÃ ARM", "DYN": "DYNAMIC",
                           "ARMED": "ĐÃ ARM", "DYNAMIC": "DYNAMIC", "ALERT": "CHẠM MỐC",
                           "TRIGGERED": "CHẠM MỐC"}.get(protect_state, protect_state)
            self.preview_normal_value.configure(
                text=(
                    f"{state_label} · BÁN: {_display_price(protected)}"
                    if _number(protected) > 0 else
                    f"{state_label} · PNL {_number(current_profit):+.1f}%"
                )
            )
            if effective_trail is not None:
                self._preview_protect_detail = (
                    _dynamic_atr_preview_text(decision_details, params),
                    f"{normal_policy} · BÁN {normal_sell:g}% · LÙI {_number(effective_trail):g}%",
                )
                self.preview_normal_detail.configure(
                    text=_dynamic_atr_preview_text(decision_details, params),
                )
        fit_label_text(self.preview_normal_value, base_font=("Cascadia Mono", 12))
        fit_label_text(self.preview_normal_detail, base_font=("Segoe UI", 10))
        self._fit_protect_preview()
        self._refresh_rule_preview(status, symbol)
        _render_order_notice(self)

    def _refresh_rule_preview(self, status: dict[str, Any], symbol: str) -> None:
        if not hasattr(self, "preview_rule_market"):
            return
        status = self._book_preview_status(status)
        decisions = status.get("decisions") if isinstance(status.get("decisions"), dict) else {}
        decision = decisions.get(symbol) if isinstance(decisions.get(symbol), dict) else {}
        self._preview_rule_decision = decision
        details = self._preview_indicator_details(status, symbol)
        indicators = details.get("indicators") if isinstance(details.get("indicators"), dict) else {}
        rule_params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        buy_ema_enabled = bool(rule_params.get("buy_signal_use_ema", True))
        sell_ema_enabled = bool(rule_params.get("sell_signal_use_ema", True))

        action = str(decision.get("action") or "WAIT").upper()
        market_state = str(decision.get("market_state") or "UNKNOWN").upper()
        signal = self._exit_preview_signal(decision, symbol)
        position_quantity = self._exit_preview_quantity(details, symbol)
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
        self._render_phase1_preview(details, market_state)

        def ema_preview(prefix: str, key_prefix: str) -> tuple[str, str, str]:
            fast_period = int(indicators.get(f"{key_prefix}_ema_fast_period", indicators.get("ema_fast_period", 3)) or 3)
            slow_period = int(indicators.get(f"{key_prefix}_ema_slow_period", indicators.get("ema_slow_period", 6)) or 6)
            fast_value = indicators.get(f"{key_prefix}_ema_fast", indicators.get("ema_fast"))
            slow_value = indicators.get(f"{key_prefix}_ema_slow", indicators.get("ema_slow"))
            key = f"EMA {prefix} {fast_period}/{slow_period}:"
            try:
                fast_number = float(fast_value)
                slow_number = float(slow_value)
            except (TypeError, ValueError):
                return key, "-- / --", COL_PREVIEW_TEXT
            if not math.isfinite(fast_number) or not math.isfinite(slow_number):
                return key, "-- / --", COL_PREVIEW_TEXT
            relation = ">" if fast_number > slow_number else "<" if fast_number < slow_number else "="
            spread = abs(fast_number - slow_number)
            decimals = 2 if spread >= 0.01 else 3 if spread >= 0.001 else 4
            fast_text = f"{fast_number:,.{decimals}f}"
            slow_text = f"{slow_number:,.{decimals}f}"
            color = COL_GREEN if fast_number > slow_number else COL_RED if fast_number < slow_number else COL_TEXT
            return key, f"{fast_text} {relation} {slow_text}", color

        buy_ema_key, buy_ema_text, buy_ema_color = ema_preview("BUY", "buy")
        sell_ema_key, sell_ema_text, sell_ema_color = ema_preview("E", "sell")
        if not buy_ema_enabled:
            buy_ema_key, buy_ema_color = buy_ema_key.replace(":", " OFF:"), COL_MUTED
        if not sell_ema_enabled:
            sell_ema_key, sell_ema_color = sell_ema_key.replace(":", " OFF:"), COL_MUTED
        rsi_period = int(indicators.get("rsi_period", 14) or 14)
        current_rsi = indicators.get("rsi")
        previous_rsi = indicators.get("rsi_previous")
        try:
            rsi_number = float(current_rsi)
        except (TypeError, ValueError):
            rsi_number = None
        if rsi_number is not None and (not math.isfinite(rsi_number) or not 0 <= rsi_number <= 100):
            rsi_number = None
        try:
            previous_number = float(previous_rsi)
        except (TypeError, ValueError):
            previous_number = None
        if previous_number is not None and (not math.isfinite(previous_number) or not 0 <= previous_number <= 100):
            previous_number = None
        if rsi_number is None:
            rsi_text, rsi_color = "--", COL_PREVIEW_TEXT
        else:
            arrow = ("" if previous_number is None else "↑" if rsi_number > previous_number
                     else "↓" if rsi_number < previous_number else "→")
            rsi_text = (
                f"{rsi_number:.1f} {arrow}".strip()
            )
            rsi_color = COL_GREEN if signal == "BUY" else COL_RED if signal == "SELL" else (
                COL_GREEN if previous_number is not None and rsi_number > previous_number
                else COL_RED if previous_number is not None and rsi_number < previous_number
                else COL_TEXT
            )
        for name, key, value, color, widget in (
            ("buy_ema", buy_ema_key, buy_ema_text, buy_ema_color, self.preview_rule_ema),
            ("sell_ema", sell_ema_key, sell_ema_text, sell_ema_color, self.preview_rule_sell_ema),
            ("rsi", f"RSI{rsi_period}:", rsi_text, rsi_color, self.preview_rule_rsi),
        ):
            key_widget = getattr(self, f"preview_rule_{name}_key", None)
            if key_widget is not None:
                key_widget.configure(text=key)
            widget.configure(text=value if key_widget is not None else f"{key} {value}", text_color=color)
            fit_label_text(widget, base_font=("Cascadia Mono", 12), minimum_size=7)

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
        phase3_parts = [f"Mã BOT: {slot_used}/{slot_max or '--'}"]
        if priority_positions:
            phase3_parts.append(f"GIỮ {int(slot_summary.get('reserved', checks.get('priority_reserved', 0)) or 0)} SLOT")
        if manual_positions:
            phase3_parts.append(f"MANUAL {manual_positions}")
            phase3_parts.append(f"TỔNG {total_positions}")
        if pending_buys:
            phase3_parts.append(f"BUY CHỜ {pending_buys}")
        phase3_parts.append(
            f"Vốn mua: {_compact_vnd(capital) if capital else '0 đ'}" if "order_budget" in (budget_checks if budget_checks is not None else checks)
            else "Vốn mua: —"
        )
        self._preview_entry_summary = " · ".join(phase3_parts)
        self.preview_rule_phase3.configure(
            text=f"Mã: {slot_used}/{slot_max or '--'} · {phase3_parts[-1]}",
            text_color=COL_WARN if guard_warn else COL_TEXT,
        )
        fit_label_text(self.preview_rule_phase3, base_font=("Segoe UI", 11), minimum_size=7)
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
                f"⚠ AUTO 100 · WHIPSAW: {whipsaw_status} · LỖ: {losses}/{loss_limit or '--'}"
                if forced_minimum
                else f"WHIPSAW: {whipsaw_status} · LỖ: {losses}/{loss_limit or '--'}"
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
            "MAX_SYMBOL_ORDERS": "ĐỦ SỐ LẦN BUY MÃ",
            "POSITION_NOT_READY_FOR_ADD": "CHƯA ĐƯỢC MUA THÊM",
            "BOT_OFF": "BOT ĐANG TẮT",
            "MANUAL_SELL_PAUSE": "TẠM KHÓA BUY SAU BÁN TAY",
            "NO_AVAILABLE_CAPITAL": "KHÔNG ĐỦ CASH",
            "BROKER_REJECTED": "BROKER TỪ CHỐI",
            "BROKER_FAILED": "GỬI BROKER THẤT BẠI",
            "MARKET_STATE_UNKNOWN": "CHỜ · STATE CHƯA XÁC NHẬN",
            "WHIPSAW_LOCK": "WHIPSAW · KHÓA BUY",
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
        price = _number(getattr(self, "_current_tick_price", 0))
        signal = str(signal or "--").upper()
        self._preview_exit_state = {"price": price, "signal": signal, "enabled": enabled,
                                    "quantity": position_quantity, "policy": policy}
        market_price = f"TT: {_display_price(price)}" if price > 0 else "CHỜ GIÁ TT"
        # Enable switches control execution, not read-only preview data.
        if position_quantity <= 0:
            value, detail, color = market_price, "CHƯA VỊ THẾ", COL_PREVIEW_TEXT
        elif signal == "SELL":
            if policy == "ALERT":
                value, detail, color = market_price, "SELL · CHỈ BÁO", COL_WARN
            else:
                value, detail, color = market_price, "SELL · 100%", COL_RED
        else:
            detail = "CHỜ · CHỈ BÁO" if policy == "ALERT" else "CHỜ SELL · 100%"
            value, color = market_price, COL_PREVIEW_TEXT
        if not enabled:
            color = COL_MUTED
        self.preview_exit_value.configure(text=value, text_color=color)
        self.preview_exit_detail.configure(
            text=detail,
            text_color=COL_MUTED if not enabled else COL_TEXT,
        )
        fit_label_text(self.preview_exit_value, base_font=("Cascadia Mono", 12))
        fit_label_text(self.preview_exit_detail, base_font=("Segoe UI", 10))

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
        self._preview_health_status = status
        daemon_health = status.get("api_health") if isinstance(status.get("api_health"), dict) else {}
        ws = daemon_health.get("websocket") if isinstance(daemon_health.get("websocket"), dict) else {}
        daemon_rest = daemon_health.get("rest") if isinstance(daemon_health.get("rest"), dict) else {}
        ui_rest = self.real.api_health()
        rest_rows = [row for row in (ui_rest, daemon_rest) if isinstance(row, dict)]
        # Each process has its own request counter. A busy healthy daemon must
        # not conceal an account/API failure in the UI client, or vice versa.
        observed_rest = [row for row in rest_rows if int(row.get("total_requests", 0) or 0) > 0]
        rest_error = any(
            not isinstance(row.get("last_status"), (int, float))
            or not 200 <= row["last_status"] < 300
            or bool(row.get("last_error"))
            for row in observed_rest
        )

        heartbeat_age = max(0.0, time.time() - _number(status.get("heartbeat_at")))
        daemon_state = str(status.get("daemon_status") or "STARTING").upper()
        daemon_alive = bool(
            getattr(self, "daemon_process", None)
            and self.daemon_process.poll() is None
        )
        if daemon_alive and (
            heartbeat_age > 8 or daemon_state in {"STARTING", "STOPPED", "STALE"}
            or _number(status.get("heartbeat_at")) < float(getattr(self, "_daemon_started_at", 0.0) or 0.0)
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
        rest_ok = bool(observed_rest) and not rest_error
        rest_state = "ERROR" if rest_error else "OK" if rest_ok else "WAIT"
        dnse_ok = configured and rest_state != "ERROR"
        dnse_text = "OK" if dnse_ok else "CHỜ" if not configured else "LỖI"
        ws_online = bool(ws.get("connected") and ws.get("authenticated"))
        ws_connecting = bool(ws.get("running")) and not ws_online
        market_status = str(status.get("market_status", "OFFLINE") or "OFFLINE").upper()
        market_active = market_status in {"ATO", "OPEN", "CONTINUOUS", "ATC"}
        self.preview_health_core.configure(
            text=f"DNSE {dnse_text}",
            text_color=COL_GREEN if dnse_ok else COL_RED if configured else COL_WARN,
        )
        self.preview_health_ws.configure(
            text=f"WS {'OK' if ws_online else 'CHỜ' if ws_connecting else 'OFF'}",
            text_color=(
                COL_GREEN if ws_online
                else COL_WARN if ws_connecting or not configured
                else COL_PREVIEW_TEXT if not market_active
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
        data_ok = market_active and quote_is_fresh(tick, symbol)
        if market_active and not data_ok:
            price_state = "CHẬM" if tick else "CHỜ"
        elif market_status == "ATO":
            price_state = "ATO"
        elif market_status == "ATC":
            price_state = "ATC"
        elif market_status in {"OPEN", "CONTINUOUS"}:
            price_state = "MỞ"
        elif tick and not market_active:
            price_state = "ĐÓNG"
        else:
            price_state = "CHỜ"
        calendar_loading = market_status == "CALENDAR_LOADING"
        calendar_error = market_status == "CALENDAR_UNKNOWN"
        self.preview_health_trade.configure(
            text="LỊCH CHỜ" if calendar_loading else "LỊCH LỖI" if calendar_error else f"GIÁ {price_state}",
            text_color=(
                COL_RED if calendar_error
                else COL_WARN if calendar_loading
                else COL_GREEN if data_ok
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
            or calendar_error
        )
        has_warning = bool(
            not has_error and (
                daemon_state == "SYNC"
                or calendar_loading
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
            ("CACHE", "#42351B", "Chưa gửi: chờ phiên, token hoặc tạm dừng trong app."),
            ("ĐANG GỬI", "#0B4F5C", "Đang xử lý gửi; chưa được coi là đã khớp."),
            ("CHỜ KHỚP", "#123F6B", "Chờ khớp hoặc xác nhận sửa/hủy. REAL: DNSE; PAPER: mô phỏng."),
            ("KHỚP MỘT PHẦN", "#6A3F08", "Mới khớp một phần khối lượng; phần còn lại vẫn chờ."),
            ("T+2", COL_SETTLEMENT_BG, "Cổ chưa được phép bán hoặc SELL đang chờ cổ về."),
            ("CHỜ ĐÓNG", "#5A4214", "Vị thế có yêu cầu SELL đang xử lý; đọc trạng thái chữ để biết bước."),
            ("LÃI", "#193524", "Vị thế đang có PnL ròng dương."),
            ("LỖ", "#3A2024", "Vị thế đang có PnL ròng âm."),
            ("HÒA VỐN", COL_SURFACE_2, "Vị thế có PnL ròng bằng 0."),
            ("UNKNOWN", "#5A1E1E", "Chưa rõ kết quả. Đối chiếu lệnh; không tự gửi lại để tránh trùng."),
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
                "E: AUTO bán 100% phần còn lại; ALERT chỉ báo\n"
                "WAIT: đang chờ  |  ARM: đã kích hoạt  |  DONE: đã xử lý"
            ),
            font=("Segoe UI", 11), text_color=COL_TEXT,
            anchor="w", justify="left", wraplength=340,
        ).grid(row=rule_row, column=1, sticky="ew", padx=(0, 8), pady=(12, 5))

        ctk.CTkLabel(
            top,
            text="Chờ xử lý ưu tiên màu lãi/lỗ. Dòng chọn đổi màu; đọc trạng thái chữ.",
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
