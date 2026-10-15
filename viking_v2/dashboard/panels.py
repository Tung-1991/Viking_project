from __future__ import annotations

import time
import math
import re
import tkinter as tk
from pathlib import Path
import os
from datetime import datetime
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from ..branding import APP_NAME, APP_VERSION
from ..connections.dnse.snapshot_health import (
    FAST_POLL_SECONDS, IDLE_POLL_SECONDS, end_of_day_order_pause,
    rest_is_end_of_day_order_pause, safe_detail, snapshot_failure_summary,
)
from ..rules.business import (
    StaticRuleParameters, average_true_range_pct, average_true_range_snapshot,
    indicator_snapshot, protect_level, protect_peak_for_sellable_phase,
)
from ..rules.protection import dynamic_floor_previous, policy_for_stage
from ..rules.observations import ema_cross_caption, finite
from ..trading.market import VN_TZ, exchange_close_minute, market_now, market_phase, merge_tick_into_daily_bars
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
    StatusLabel,
    _HoverHint,
    fit_entry_text,
    fit_label_text,
)
from .tables import RUNNING_FONT, RUNNING_HEADING_FONT, running_row_height
from .quote_direction import PRICE_DIRECTION_HINT, quote_background
from .protect_preview import dynamic_start_preview, start_caption


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
    if details.get("protection_stage") == "ARM":
        sell = _number(details.get("sell_share_pct", params.get("normal_sell_pct", 33)))
        basis = "GỐC" if details.get("sell_basis", params.get("normal_sell_basis", "ORIGINAL")) == "ORIGINAL" else "CÒN"
        policy = policy_for_stage(params, "ARM")
        return f"ARM {policy} · " + ("CHỈ BÁO" if policy == "ALERT" else f"BÁN {sell:g}% {basis}")
    sell = _number(details.get("sell_share_pct") if details.get("protection_stage") == "DYN"
                   else params.get("dynamic_sell_pct", 33.0))
    dynamic = bool(details.get(
        "normal_dynamic_enabled", params.get("normal_dynamic_enabled", False),
    ))
    if not dynamic:
        return f"DYN OFF · BÁN {sell:g}%"
    policy = policy_for_stage(params, "DYN")
    if policy == "ALERT":
        return "DYN ALERT · CHỈ BÁO"
    atr = _number(details.get("normal_atr_pct"))
    if atr <= 0:
        return f"DYN AUTO · ATR -- · BÁN {sell:g}%"
    return f"DYN AUTO · ATR {atr:.2f}% · BÁN {sell:g}%"


def _rsi_comparison_preview(current: Any, previous: Any) -> tuple[str, str]:
    """Show the rule's daily RSI baseline, not a previous-tick comparison."""
    def valid(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) and 0 <= number <= 100 else None

    current_number, previous_number = valid(current), valid(previous)
    decimals = 2
    if current_number is not None and previous_number is not None:
        # Do not display two apparently equal numbers beside an up/down arrow.
        while (current_number != previous_number and decimals < 6
               and f"{current_number:.{decimals}f}" == f"{previous_number:.{decimals}f}"):
            decimals += 1
    current_text = "--" if current_number is None else f"{current_number:.{decimals}f}"
    previous_text = "--" if previous_number is None else f"{previous_number:.{decimals}f}"
    text = f"{previous_text} → {current_text}"
    if current_number is None or previous_number is None:
        return text, COL_PREVIEW_TEXT
    if current_number > previous_number:
        return f"{text} ↑", COL_GREEN
    if current_number < previous_number:
        return f"{text} ↓", COL_RED
    return f"{text} =", COL_TEXT


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

        nav_header = ctk.CTkFrame(account, fg_color="transparent")
        nav_header.grid(row=0, column=0, sticky="ew", padx=11, pady=(4, 0))
        self.lbl_equity_title = ctk.CTkLabel(nav_header, text="TỔNG TÀI SẢN / NAV",
                                            font=("Segoe UI", 13, "bold"), text_color=COL_MUTED, anchor="w")
        self.lbl_equity_title.pack(side="left")
        self.btn_refresh_account = ctk.CTkButton(
            nav_header, text="↻ NAV", width=64, height=25,
            font=("Segoe UI", 11, "bold"),
            fg_color="#282D34", hover_color="#3A414B",
            text_color=COL_TEXT, corner_radius=6,
            command=self._refresh_account_now,
        )
        self.btn_refresh_account.pack(side="right", padx=(4, 0))
        _HoverHint(self.btn_refresh_account, "Đọc lại tiền, vị thế và giá đóng cửa từ DNSE ngay.")
        self.lbl_equity = ctk.CTkLabel(
            account, text="----", font=("Segoe UI", 30, "bold"),
            text_color=COL_GREEN, anchor="w",
        )
        self.lbl_equity.grid(row=1, column=0, sticky="ew", padx=(11, 5), pady=(0, 0))
        self.lbl_equity.bind("<Configure>", lambda _event: fit_label_text(
            self.lbl_equity, base_font=("Segoe UI", 30, "bold"), minimum_size=14,
        ), add="+")
        _HoverHint(self.lbl_equity, self._account_money_hint)
        _HoverHint(self.lbl_equity_title, self._account_money_hint)
        session_box = ctk.CTkFrame(
            account, width=180, height=52, fg_color="transparent",
        )
        session_box.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(3, 11), pady=(7, 0))
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
            row=2, column=0, columnspan=2, sticky="ew",
            padx=(11, 11), pady=(0, 2),
        )
        account_footer = ctk.CTkFrame(account, fg_color="transparent")
        account_footer.grid(row=3, column=0, columnspan=2, sticky="ew", padx=11, pady=(1, 5))
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
        _HoverHint(self.lbl_cash, "Phí đã ghi nhận từ broker; CHỜ DNSE nghĩa là lệnh khớp nhưng chưa có phí.\n"
                   "Phí có dấu ~ ở bảng lệnh là ước tính, không cộng vào số phí đã ghi nhận.\n"
                   "Chốt ngày theo giờ đã đặt; giữ kết quả đến 09:00 sáng hôm sau.")
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
            mode_row, text="MUA AUTO · OFF", height=32, font=("Segoe UI", 11, "bold"),
            width=1,
            fg_color=COL_GRAY, hover_color="#4B515B", corner_radius=7,
            command=self._toggle_bot,
        )
        self.bot_button.grid(row=0, column=1, sticky="ew")
        _HoverHint(
            self.bot_button,
            "MUA AUTO ON (xanh): được phép tạo và gửi BUY tự động.\n"
            "CHỜ (vàng): đang khóa BUY tạm thời sau SELL MANUAL; SELL/SL/PROTECT vẫn chạy.\n"
            "MUA AUTO OFF (xám): không tạo hoặc gửi BUY BOT; vị thế đang giữ vẫn được quản lý.",
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
            text_color=COL_TEXT, anchor="center", corner_radius=4,
        )
        self.lbl_price.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 0))
        _HoverHint(self.lbl_price, PRICE_DIRECTION_HINT)
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
            brand, text=f"{APP_NAME} · v{APP_VERSION}", font=("Segoe UI", 19, "bold"),
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
            "Bấm mũi tên bên vị thế để bung/gọn lệnh đang chờ.\n"
            "CHỜ KHỚP chưa mua được; CHỜ VỀ đã khớp nhưng chưa bán được.\n"
            "Nút ✖ trên lệnh con yêu cầu hủy phần chưa khớp.",
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
            header, text="HỦY LỆNH CHỜ", width=112, height=31, font=("Segoe UI", 11, "bold"),
            fg_color="#2A2E34", hover_color="#D97706", corner_radius=8,
            state="disabled",
            command=self._cancel_selected
        )
        self.cancel_button.pack(side="right", padx=(0, 5))
        _HoverHint(self.cancel_button, "Hủy yêu cầu BUY/SELL còn chờ gửi hoặc chờ khớp của dòng chọn.\n"
                   "Ví dụ: BUY 100 CP chưa khớp → hủy phần còn chờ.\n"
                   "CHỜ VỀ T+2 là cổ đã mua, không phải lệnh còn chờ để hủy.\n"
                   "Nút xám: dòng chọn không có yêu cầu hủy được.", placement="inside")
        self.close_selected_button = ctk.CTkButton(
            header, text="BÁN ĐÃ CHỌN", width=115, height=31, font=("Segoe UI", 11, "bold"),
            fg_color="#B91C1C", hover_color="#DC2626", corner_radius=8,
            state="disabled", command=self._close_running_selection)
        self.close_selected_button.pack(side="right", padx=(0, 5))
        _HoverHint(self.close_selected_button,
                   "Ctrl + click: chọn nhiều mã. Shift + click: chọn một dải. Ctrl + A: chọn tất cả vị thế.\n"
                   "Mở bảng kiểm tra mã và số cổ trước khi xác nhận bán.", placement="inside")

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
            fieldbackground=COL_SURFACE_2, rowheight=running_row_height(self), font=RUNNING_FONT,
            borderwidth=0, relief="flat", bordercolor=COL_BORDER,
            lightcolor=COL_BORDER, darkcolor=COL_BORDER,
        )
        style.configure(
            "Running.Treeview.Heading", background=COL_SURFACE, foreground=COL_TITLE,
            font=RUNNING_HEADING_FONT, relief="flat", padding=(10, 6),
        )
        style.map(
            "Running.Treeview.Heading",
            background=[("active", "#252A32"), ("pressed", "#252A32")],
            foreground=[("active", COL_TITLE), ("pressed", COL_TITLE)],
        )
        style.map(
            "Running.Treeview",
            background=[("selected", "#393347")],
            foreground=[("selected", "#FFFFFF")],
        )
        for mode in ("CKCS REAL", "CKCS PAPER"):
            frame = self.tabs.add(mode)
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            tree = ttk.Treeview(frame, show="tree headings", selectmode="extended", style="Running.Treeview")
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
            tree.bind("<Escape>", lambda _event, widget=tree: self._clear_running_selection(widget), add="+")
            tree.bind("<Button-3>", self._running_right_click)
            tree.bind("<Control-a>", self._select_all_running_positions, add="+")
            tree.bind("<Control-A>", self._select_all_running_positions, add="+")
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
        self.info_header = info_header
        info_header.grid_columnconfigure(0, weight=0)
        info_header.grid_columnconfigure(1, weight=0)
        info_header.grid_columnconfigure(2, weight=1)
        info_header.grid_columnconfigure(3, weight=0)
        info_header.grid_columnconfigure(4, weight=0)
        self.info_title = ctk.CTkLabel(
            info_header, text="HỆ THỐNG  ⓘ", font=("Segoe UI", 14, "bold"),
            text_color=COL_TITLE, anchor="w",
        )
        self.info_title.grid(row=0, column=0, sticky="w", padx=(2, 12))
        self.info_tab_selector = ctk.CTkSegmentedButton(
            info_header, values=["PREVIEW", "Manual", "Bot"],
            width=240, height=28, dynamic_resizing=False,
            font=("Segoe UI", 11, "bold"),
            fg_color=COL_SURFACE_2,
            selected_color="#2563A6",
            selected_hover_color="#2E73BB",
            unselected_color=COL_GRAY,
            unselected_hover_color="#4B515B",
            command=self._select_info_tab,
        )
        self.info_tab_selector.grid(row=0, column=3, padx=(8, 0))
        def fit_info_header(_event=None):
            width = info_header.winfo_width() / info_header._get_widget_scaling()
            compact = width < 760
            self.info_title.configure(text="ⓘ" if compact else "HỆ THỐNG  ⓘ", width=20 if compact else 140)
            self.info_tab_selector.configure(width=210 if width < 700 else 240)
            if hasattr(self, "preview_tab_selector"):
                self.preview_tab_selector.configure(width=300 if width < 700 else 320)
        info_header.bind("<Configure>", fit_info_header, add="+")
        # CTkSegmentedButton.bind raises NotImplementedError; attach the
        # explanation to a stable label, not to the composite tab control.
        _HoverHint(
            self.info_title,
            "PREVIEW: xem lệnh và tín hiệu.\nManual / Bot: nhật ký thao tác tay / tự động.\n* = có dòng mới chưa đọc.",
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
        self.info_collapse_button.grid(row=0, column=4, sticky="e", padx=(8, 2))
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
        self.preview_container = preview_tab
        self._build_order_preview_tab(preview_tab, health_parent=log_frame, selector_parent=info_header)

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
        self._select_info_tab("PREVIEW")
        self._select_preview_tab("TỔNG QUAN")
        self._refresh_api_health_panel(self.bridge.read_status())

    def _build_order_preview_tab(self, parent: ctk.CTkFrame, *, health_parent=None, selector_parent=None) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        panel = ctk.CTkFrame(parent, height=260, fg_color=COL_SURFACE_2, corner_radius=8)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_propagate(False)
        self.preview_focus_panel = panel
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0 if selector_parent is not None else 1, weight=1)
        if selector_parent is None:
            page_header = ctk.CTkFrame(panel, height=28, fg_color="transparent")
            page_header.grid(row=0, column=0, sticky="ew", padx=6, pady=(3, 2))
            page_header.grid_propagate(False)
            page_header.grid_columnconfigure(0, weight=1)
        else:
            page_header = selector_parent
        self.preview_tab_selector = ctk.CTkSegmentedButton(
            page_header, values=["TỔNG QUAN", "LỆNH", "HỆ THỐNG"], width=320, height=28,
            dynamic_resizing=False, font=("Segoe UI", 11, "bold"),
            selected_color="#2563A6", selected_hover_color="#2E73BB",
            unselected_color=COL_GRAY, unselected_hover_color="#4B515B",
            command=self._select_preview_tab,
        )
        self.preview_tab_selector.grid(row=0, column=1 if selector_parent is not None else 0,
                                       sticky="w" if selector_parent is not None else "")
        pages = ctk.CTkFrame(panel, width=1, fg_color="transparent")
        pages.grid(row=0 if selector_parent is not None else 1, column=0, sticky="nsew", padx=4, pady=4)
        pages.grid_propagate(False)
        pages.grid_columnconfigure(0, weight=1)
        pages.grid_rowconfigure(0, weight=1)
        # Keep both pages measured in the same slot; switching only raises a
        # page, so changing tabs cannot resize the panel or shift its values.
        order_group = ctk.CTkFrame(pages, width=1, fg_color=COL_SURFACE, corner_radius=7)
        order_group.grid(row=0, column=0, sticky="nsew")
        order_group.grid_propagate(False)
        rule_group = ctk.CTkFrame(pages, width=1, fg_color=COL_SURFACE, corner_radius=7)
        rule_group.grid(row=0, column=0, sticky="nsew")
        rule_group.grid_propagate(False)
        self.preview_pages = {"TỔNG QUAN": pages, "LỆNH": order_group, "HỆ THỐNG": rule_group}
        self.preview_page_area = pages
        pages.bind("<Configure>", self._layout_preview_pages, add="+")

        order_group.grid_columnconfigure(0, weight=1)
        order_group.grid_rowconfigure(1, weight=0)
        order_group.grid_rowconfigure(2, weight=1)
        order_header = ctk.CTkFrame(order_group, width=1, height=32, fg_color="transparent")
        order_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 1))
        order_header.grid_columnconfigure(0, weight=2)
        order_header.grid_columnconfigure(1, weight=3)
        order_header.grid_columnconfigure(2, weight=0)
        self.preview_order_title = StatusLabel(
            order_header, text="--- · PAPER · BUY · MARKET",
            width=1, height=22, font=("Segoe UI", 12, "bold"), text_color=COL_TITLE, anchor="w",
        )
        self.preview_order_title.grid(
            row=0, column=0, sticky="ew", padx=(2, 5)
        )
        self.preview_status_reason = StatusLabel(
            order_header, text="--", width=1, height=22, font=("Segoe UI", 11),
            text_color=COL_PREVIEW_TEXT, fg_color=COL_SURFACE_2, corner_radius=5,
            anchor="w", justify="left",
        )
        self.preview_status_reason.grid(
            row=0, column=1, sticky="ew", padx=5
        )
        _HoverHint(self.preview_status_reason, self._order_status_hint, placement="inside", fixed_size=True)
        self.preview_status_badge = StatusLabel(
            order_header, text="CHỜ", width=82, height=24,
            font=("Segoe UI", 10, "bold"), fg_color="#4A3B16",
            text_color="#FFF3B0", corner_radius=6,
        )
        self.preview_status_badge.grid(
            row=0, column=2, sticky="e", padx=(5, 0)
        )
        def fit_order_header(_event=None):
            narrow = order_header.winfo_width() / order_header._get_widget_scaling() < 600
            if getattr(order_header, "_viking_narrow", None) != narrow:
                order_header._viking_narrow = narrow
                order_header.configure(height=52 if narrow else 32)
                self.preview_order_title.grid_configure(columnspan=2 if narrow else 1)
                self.preview_status_reason.grid_configure(row=1 if narrow else 0,
                                                         column=0 if narrow else 1,
                                                         columnspan=3 if narrow else 1)
            fit_label_text(self.preview_order_title, base_font=("Segoe UI", 12, "bold"), minimum_size=10)
            fit_label_text(self.preview_status_reason, base_font=("Segoe UI", 11), minimum_size=10)
        order_header.bind("<Configure>", fit_order_header, add="+")
        for widget in (self.preview_order_title, self.preview_status_reason):
            widget.bind("<Configure>", fit_order_header, add="+")
        self._fit_order_preview_header = fit_order_header

        metrics = ctk.CTkFrame(order_group, height=62, fg_color="transparent")
        metrics.grid(row=1, column=0, sticky="ew", padx=6, pady=(3, 2))
        metrics.grid_propagate(False)
        self._preview_metric_cards = []
        self.preview_metrics = metrics
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
            title_widget = StatusLabel(
                card, text=f"{title}{'  ⓘ' if hint else ''}", height=18,
                font=("Segoe UI", 12, "bold", "italic"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=2, pady=(4, 0))
            if title == "KL":
                self.preview_qty_title = title_widget
            if hint:
                _HoverHint(title_widget, hint, placement="inside")
            title_widget.bind("<Configure>", lambda _event, widget=title_widget: fit_label_text(
                widget, base_font=("Segoe UI", 12, "bold", "italic"), minimum_size=5), add="+")
            value = StatusLabel(
                card, text="NA", width=1, height=24,
                font=("Cascadia Mono", 13), text_color=COL_TEXT,
                anchor="w", justify="left", wraplength=0,
            )
            value.grid(row=1, column=0, sticky="ew", padx=2, pady=(1, 4))
            value.bind("<Configure>", lambda _event, widget=value: fit_label_text(
                widget, base_font=("Cascadia Mono", 13), minimum_size=5), add="+")
            if hint:
                _HoverHint(value, hint, placement="inside")
            self._preview_metric_cards.append((card, title_widget, value, title_widget.cget("text")))
            return value

        self.preview_live_value = metric_card(
            0, "GIÁ TT", hint=self._quote_health_hint,
        )
        self.preview_live_value.configure(corner_radius=4)
        self.preview_entry_value = metric_card(1, "GIÁ VÀO", hint="Giá dùng tính TP/SL; LO lấy giá nhập.\nVốn MARKET có thể dự trù theo giá trần.")
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
        self.preview_management = management
        self._preview_level_cards = []
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
                sticky="nsew", padx=3, pady=2,
            )
            card.grid_columnconfigure(0, weight=1)
            title_widget = StatusLabel(
                card, text=title, width=1, height=14, font=("Segoe UI", 11, "bold", "italic"),
                text_color=title_color, anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="ew", padx=9, pady=(1, 0))
            value = StatusLabel(
                card, text="NA", width=1, height=16, font=("Cascadia Mono", 12),
                text_color=title_color, anchor="w",
            )
            value.grid(row=1, column=0, sticky="ew", padx=9, pady=0)
            detail = StatusLabel(
                card, text="", width=1, height=12, font=("Segoe UI", 10),
                text_color=COL_PREVIEW_TEXT, anchor="w",
            )
            detail.grid(row=2, column=0, sticky="ew", padx=9, pady=(0, 1))
            detail.bind("<Configure>", lambda _event: fit_label_text(
                detail, base_font=("Segoe UI", 10), minimum_size=9), add="+")
            self._preview_level_cards.append((title_widget, value, detail))
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
        self.preview_exit_value.grid_configure(padx=4)
        self.preview_exit_detail.grid_configure(padx=2)
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
        StatusLabel(
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
            "P1: tỷ trọng cổ / tiền.\nP2: EMA và RSI.\nP3: vốn và khóa BUY.",
        )
        self.preview_rule_title = StatusLabel(
            rule_header, text="MUA · OFF", width=72, height=16,
            font=("Segoe UI", 11, "bold"), text_color=COL_RED,
            fg_color=COL_SURFACE_2, corner_radius=5,
        )
        self.preview_rule_title.grid(row=0, column=2, sticky="e")

        def phase_card(row: int, title: str, hint: str = "", *, icon_hint: bool = False):
            card = ctk.CTkFrame(rule_group, width=1, fg_color=COL_SURFACE_2, corner_radius=6)
            card.grid(row=row, column=0, sticky="nsew", padx=8, pady=2)
            card.grid_columnconfigure(0, weight=0)
            card.grid_columnconfigure(1, weight=1)
            title_host = card
            if icon_hint:
                title_host = ctk.CTkFrame(card, fg_color="transparent")
                title_host.grid(row=0, column=0, sticky="w", padx=(8, 6), pady=4)
            title_widget = StatusLabel(
                title_host, text=f"{title}{'  ⓘ' if hint and not icon_hint else ''}", height=18, font=("Segoe UI", 12, "bold", "italic"),
                text_color="#60A5FA", anchor="w",
            )
            title_widget.grid(row=0, column=0, sticky="w", padx=0 if icon_hint else (8, 6),
                              pady=0 if icon_hint else 4)
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
                if icon_hint:
                    icon = ctk.CTkButton(
                        title_host, text="ⓘ", width=22, height=22, corner_radius=5,
                        font=("Segoe UI Symbol", 12, "bold"),
                        fg_color="#343A43", hover_color="#4B515B", text_color="#60A5FA",
                    )
                    icon.grid(row=0, column=1, padx=(4, 0))
                    help_hint = _HoverHint(icon, hint, placement="inside")
                    icon.configure(command=help_hint._show)
                    card._viking_hint_icon = icon
                else:
                    _HoverHint(title_widget, hint, placement="inside")
            return card

        phase1 = phase_card(1, "P1 · VNINDEX", self._market_confirmation_hint)
        self.preview_rule_market = StatusLabel(
            phase1, text="VNINDEX --", width=1, height=16,
            font=("Segoe UI", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_market.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=4)
        self.preview_rule_market.bind("<Configure>", lambda _event: fit_label_text(
            self.preview_rule_market, base_font=("Segoe UI", 12)), add="+")
        _HoverHint(self.preview_rule_market, self._market_confirmation_hint, placement="inside")
        self.preview_rule_market_detail = StatusLabel(
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
            icon_hint=True,
        )
        phase2._viking_title_widget.grid(columnspan=1)
        phase2.grid_columnconfigure(1, weight=2, minsize=165)
        phase2.grid_columnconfigure(2, weight=2, minsize=140)
        # Keep mode and condition slots fixed so swapping VA/X cannot move values.
        mode_header = ctk.CTkFrame(phase2, width=165, height=24, fg_color="transparent")
        mode_header.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=3)
        mode_header.grid_propagate(False)
        mode_header.grid_columnconfigure(0, weight=1)
        self.preview_rule_cross_title = StatusLabel(
            mode_header, text="∞ VA", width=1, height=18,
            font=("Segoe UI", 10, "bold"), text_color=COL_PREVIEW_TEXT, anchor="e",
        )
        self.preview_rule_cross_title.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        cross_header = ctk.CTkFrame(phase2, width=140, height=24, fg_color="transparent")
        cross_header.grid(row=0, column=2, sticky="ew", padx=(4, 6), pady=3)
        cross_header.grid_propagate(False)
        cross_header.grid_columnconfigure(0, weight=1)
        self.preview_ema_entry_gate = StatusLabel(
            cross_header, text="EMA CẮT: CHỜ", width=1, height=18,
            font=("Segoe UI", 10, "bold"), text_color=COL_MUTED, anchor="w",
        )
        self.preview_ema_entry_gate.grid(row=0, column=0, sticky="ew")
        self.preview_ema_hint = ctk.CTkButton(
            cross_header, text="ⓘ", width=22, height=22, corner_radius=5,
            font=("Segoe UI Symbol", 12, "bold"),
            fg_color="#343A43", hover_color="#4B515B", text_color=COL_TEXT,
        )
        self.preview_ema_hint.grid(row=0, column=1, padx=(3, 0))
        cross_hint = _HoverHint(self.preview_ema_hint, self._ema_cross_hint, placement="inside")
        self.preview_ema_hint.configure(command=cross_hint._show)
        self.preview_ema_swap = ctk.CTkButton(
            cross_header, text="⇄", width=22, height=20, corner_radius=4,
            font=("Segoe UI Symbol", 12), fg_color="#343A43", hover_color="#4B515B",
            command=self._swap_ema_preview,
        )
        self.preview_ema_swap.grid(row=0, column=2, padx=(3, 0))
        self.preview_rule_cross_counts = StatusLabel(
            phase2, text="↑— ↓—", width=1, height=22,
            font=("Segoe UI", 14, "bold"), text_color=COL_MUTED, anchor="w",
        )
        self.preview_rule_cross_counts.grid(row=1, column=2, sticky="ew", padx=8)
        self.preview_rule_cross_detail = StatusLabel(
            phase2, text="", width=1, height=14,
            font=("Segoe UI", 10), text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_cross_detail.grid(row=2, column=2, sticky="ew", padx=8)
        self.preview_rule_cross = self.preview_rule_cross_detail  # One shared information line.
        for row, name in ((1, "buy_ema"), (2, "sell_ema"), (3, "rsi")):
            key = StatusLabel(
                phase2, text="--:", width=1, height=14,
                font=("Segoe UI", 11), text_color=COL_PREVIEW_TEXT, anchor="w",
            )
            key.grid(row=row, column=0, sticky="w", padx=(8, 6), pady=(0, 3) if row == 3 else 0)
            setattr(self, f"preview_rule_{name}_key", key)
        self.preview_rule_ema = StatusLabel(
            phase2, text="-- / --", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_ema.grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=0
        )
        self.preview_rule_sell_ema = StatusLabel(
            phase2, text="-- / --", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_sell_ema.grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=0
        )
        self.preview_rule_rsi = StatusLabel(
            phase2, text="--", width=1, height=14,
            font=("Cascadia Mono", 12), text_color=COL_PREVIEW_TEXT,
            anchor="w", justify="left", wraplength=0,
        )
        self.preview_rule_rsi.grid(
            row=3, column=1, sticky="ew", padx=(0, 8), pady=(0, 3)
        )
        def fit_phase2_row(_event=None):
            narrow = phase2.winfo_width() / phase2._get_widget_scaling() < 440
            if getattr(phase2, "_viking_cross_narrow", None) != narrow:
                phase2._viking_cross_narrow = narrow
                phase2.grid_columnconfigure(1, weight=2, minsize=115 if narrow else 165)
                phase2.grid_columnconfigure(2, weight=2, minsize=140)
                mode_header.configure(width=115 if narrow else 165)
                phase2._viking_title_widget.grid(columnspan=1)
                phase2._viking_title_widget.configure(text="P2" if narrow else "P2 · BUY / E")
                for value in (self.preview_rule_ema, self.preview_rule_sell_ema, self.preview_rule_rsi):
                    value.grid(padx=(0, 0) if narrow else (0, 8))
                self.preview_rule_cross_detail.grid_configure(padx=(4, 6))
                self.preview_rule_cross_counts.grid_configure(padx=(4, 6))
            gate = getattr(self.preview_ema_entry_gate, "_viking_full_gate_text", self.preview_ema_entry_gate.cget("text"))
            self.preview_ema_entry_gate.configure(text=gate.removeprefix("EMA "))
            fit_label_text(self.preview_ema_entry_gate, base_font=("Segoe UI", 10, "bold"), minimum_size=9)
            for name in ("buy_ema", "sell_ema", "rsi"):
                key_widget = getattr(self, f"preview_rule_{name}_key")
                key_widget.grid_configure(padx=(6, 4) if narrow else (8, 6))
                key = getattr(key_widget, "_viking_full_key_text", key_widget.cget("text"))
                key_widget.configure(text=key.replace("EMA BUY ", "BUY ").replace("EMA E ", "E ") if narrow else key)
                value_widget = {"buy_ema": self.preview_rule_ema, "sell_ema": self.preview_rule_sell_ema,
                                "rsi": self.preview_rule_rsi}[name]
                value = getattr(value_widget, "_viking_full_value_text", value_widget.cget("text"))
                if narrow:
                    value = value.replace(" → ", "→").replace(" > ", ">").replace(" < ", "<").replace(" = ", "=")
                    value = value.replace(" ↑", "↑").replace(" ↓", "↓")
                    if name == "rsi":
                        value = re.sub(r"(\.\d*?)0+(?=[→↑↓=]|$)", lambda match: match.group(1).rstrip("."), value)
                value_widget.configure(text=value)
            detail = getattr(self.preview_rule_cross_detail, "_viking_full_detail_text", self.preview_rule_cross_detail.cget("text"))
            compact_detail = getattr(self.preview_rule_cross_detail, "_viking_compact_detail_text", detail)
            self.preview_rule_cross_detail.configure(text=compact_detail if narrow else detail)
            fit_label_text(self.preview_rule_cross_detail, base_font=("Segoe UI", 10), minimum_size=9)
        self._fit_phase2_row = fit_phase2_row
        phase2.bind("<Configure>", fit_phase2_row, add="+")

        phase3 = phase_card(
            3,
            "P3 · VỐN & KHÓA",
            self._entry_capital_hint,
        )
        self.preview_rule_phase3 = StatusLabel(
            phase3, text="--/-- · --/MÃ", width=1, height=18, font=("Segoe UI", 11),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3.grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=4
        )
        self.preview_rule_phase3.bind("<Configure>", lambda _event: fit_label_text(
            self.preview_rule_phase3, base_font=("Segoe UI", 11), minimum_size=7), add="+")
        _HoverHint(self.preview_rule_phase3, self._entry_capital_hint, placement="inside")
        self.preview_rule_phase3_detail = StatusLabel(
            phase3, text="AUTO --", width=1, height=16, font=("Segoe UI", 12),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3_detail.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 4)
        )
        self.preview_rule_phase3_detail.bind("<Configure>", lambda _event: fit_label_text(
            self.preview_rule_phase3_detail, base_font=("Segoe UI", 12), minimum_size=7), add="+")
        _HoverHint(self.preview_rule_phase3_detail, self._whipsaw_preview_hint, placement="inside")
        self.preview_rule_phase3_guard = StatusLabel(
            phase3, text="WHIPSAW -- · LOSS --", font=("Segoe UI", 11),
            text_color=COL_PREVIEW_TEXT, anchor="w",
        )
        self.preview_rule_phase3_guard.grid_remove()

        self.preview_rule_reason = StatusLabel(
            rule_group, text="CHỜ DỮ LIỆU", width=1, height=22,
            font=("Segoe UI", 9, "bold"), text_color=COL_WARN,
            fg_color=COL_SURFACE_2, corner_radius=6,
            anchor="w", justify="left", wraplength=300,
        )
        self.preview_rule_reason.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 6))
        _HoverHint(self.preview_rule_reason, self._rule_decision_hint, placement="inside", fixed_size=True)

        health_host = health_parent if health_parent is not None else parent
        health_group = ctk.CTkFrame(
            health_host, fg_color=COL_SURFACE, corner_radius=7, height=38,
            border_width=1, border_color=COL_BORDER,
        )
        health_group.grid(row=2 if health_parent is not None else 1, column=0,
                          sticky="ew", padx=8, pady=(3, 6))
        self.preview_health_group = health_group
        health_group.grid_propagate(False)
        for column, weight in enumerate((20, 18, 14, 10, 14, 12, 16)):
            health_group.grid_columnconfigure(column, weight=weight, uniform="health_pills")
        health_title = ctk.CTkFrame(
            health_group, width=1, height=26, fg_color=COL_SURFACE_2, corner_radius=5,
        )
        health_title.grid(row=0, column=0, sticky="ew", padx=3, pady=5)
        health_title.grid_propagate(False)
        health_title.grid_columnconfigure(0, weight=1)
        self.preview_health_title = StatusLabel(
            health_title, text="HEALTH", width=1,
            height=20,
            font=("Segoe UI", 10, "bold", "italic"),
            text_color="#60A5FA", anchor="w",
        )
        self.preview_health_title.grid(row=0, column=0, sticky="ew", padx=(8, 1))
        self.preview_health_title.configure(cursor="hand2")
        self.preview_health_title.bind("<Button-1>", self._show_api_health_details, add="+")
        self.preview_health_title.bind("<Configure>", self._fit_preview_health_title, add="+")
        self.preview_health_hint = StatusLabel(
            health_title, text="ⓘ", width=20, height=20,
            font=("Segoe UI Symbol", 10, "bold"), text_color=COL_TEXT,
            fg_color="#343A43", corner_radius=5,
        )
        self.preview_health_hint.grid(row=0, column=1, sticky="e", padx=(1, 3), pady=2)
        self.preview_health_hint.bind("<Button-1>", self._copy_api_health_hint, add="+")
        _HoverHint(
            self.preview_health_hint,
            self._api_health_preview_hint,
            placement="inside",
        )

        def health_cell(column: int, text: str) -> ctk.CTkLabel:
            label = StatusLabel(
                health_group, text=text, width=1, height=26,
                font=("Segoe UI", 10), text_color=COL_PREVIEW_TEXT,
                fg_color=COL_SURFACE_2, corner_radius=5,
            )
            label.grid(row=0, column=column, sticky="ew", padx=3, pady=5)
            def fit_cell(_event=None):
                label.configure(wraplength=0 if column == 5 else max(
                    1, label.winfo_width() / label._get_widget_scaling() - 6))
                if column == 5:
                    fit_label_text(label, base_font=("Segoe UI", 10), minimum_size=8)
            label.bind("<Configure>", fit_cell, add="+")
            return label

        self.preview_health_daemon = health_cell(1, "DAEMON --")
        self.preview_health_core = health_cell(2, "DNSE --")
        self.preview_health_ws = health_cell(3, "WS --")
        self.preview_health_rest = health_cell(4, "REST --")
        self.preview_health_token = health_cell(5, "TOKEN --")
        self.preview_health_trade = health_cell(6, "GIÁ --")
        _HoverHint(self.preview_health_trade, self._quote_health_hint, placement="inside")
        self._select_preview_tab("LỆNH")

    def _select_preview_tab(self, name: str) -> None:
        if name not in self.preview_pages:
            return
        if getattr(self, "_info_collapsed", False):
            self._set_info_panel_collapsed(False)
        self._preview_active_tab = name
        self._layout_preview_pages()
        self.preview_tab_selector.set(name)

    def _layout_preview_pages(self, _event=None) -> None:
        name = getattr(self, "_preview_active_tab", "LỆNH")
        pages = self.preview_page_area
        order, rules = self.preview_pages["LỆNH"], self.preview_pages["HỆ THỐNG"]
        overview = name == "TỔNG QUAN"
        compact = overview and pages.winfo_width() / pages._get_widget_scaling() < 940
        layout = "compact_overview" if compact else "overview" if overview else "single"
        scale = pages._get_widget_scaling()
        if (getattr(self, "_preview_page_layout", None) != layout
                or getattr(self, "_preview_page_scale", None) != scale):
            self._preview_page_layout = layout
            self._preview_page_scale = scale
            pages.grid_columnconfigure(0, weight=3 if overview else 1,
                                       minsize=int((260 if compact else 480) * scale) if overview else 0)
            pages.grid_columnconfigure(1, weight=2 if overview else 0,
                                       minsize=int((360 if compact else 440) * scale) if overview else 0)
            pages.grid_rowconfigure(0, weight=1, minsize=0)
            order.grid(row=0, column=0, columnspan=1 if overview else 2,
                       sticky="nsew", padx=(0, 3) if overview else 0, pady=0)
            rules.grid(row=0, column=1 if overview else 0, columnspan=1 if overview else 2,
                       sticky="nsew", padx=(3, 0) if overview else 0, pady=0)
            self.preview_metrics.configure(height=96 if compact else 62)
            self.preview_management.grid_rowconfigure((0, 1), minsize=int(52 * scale))
            for column in range(6):
                self.preview_metrics.grid_columnconfigure(
                    column, weight=(1 if column < 2 else 0) if compact else (2 if column == 3 else 1),
                    uniform="compact_metrics" if compact and column < 2 else "" if compact else "preview_metrics")
            for index, (card, key, value, full_key) in enumerate(self._preview_metric_cards):
                card.grid(row=index // 2 if compact else 0, column=index % 2 if compact else index,
                          sticky="nsew", padx=3, pady=2 if compact else 3)
                card.grid_columnconfigure(0, weight=0 if compact else 1)
                card.grid_columnconfigure(1, weight=1 if compact else 0)
                key.configure(text=("TT", "VÀO", "KL", "VỐN", "FEE", "LỆNH")[index] if compact else
                              getattr(key, "_viking_full_metric_key", full_key), width=45 if compact else 1)
                key.grid(row=0, column=0, sticky="ew", padx=4 if compact else 2,
                         pady=2 if compact else (4, 0))
                value.grid(row=0 if compact else 1, column=1 if compact else 0, sticky="ew",
                           padx=4 if compact else 2, pady=2 if compact else (1, 4))
            for fields in self._preview_level_cards:
                for label in fields:
                    label.grid_configure(padx=4 if compact else 9)
            for callback in ("_fit_order_preview_header", "_fit_phase2_row", "_fit_protect_preview"):
                fit = getattr(self, callback, None)
                if callable(fit):
                    fit()
        if not overview:
            self.preview_pages[name].tkraise()

    def _fit_preview_health_title(self, _event=None) -> None:
        label = self.preview_health_title
        if not isinstance(label, ctk.CTkLabel):
            return
        full = getattr(label, "_viking_health_full_text", "HEALTH")
        compact = label.winfo_width() / label._get_widget_scaling() < 120
        short = "CHÚ Ý" if full == "HEALTH CẢNH BÁO" else full.removeprefix("HEALTH ")
        label.configure(text=short if compact else full)
        fit_label_text(label, base_font=("Segoe UI", 10, "bold", "italic"), minimum_size=8)

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
            "ĐẶT / CACHE / CHỜ OTP: yêu cầu còn trong app.\n"
            "ĐÃ GỬI: DNSE nhận lệnh.\n"
            "KHỚP x/y: x CP đã khớp trên y CP đặt."
        )

    def _rule_decision_hint(self) -> str:
        position = getattr(self, "_preview_position_status", None)
        if position:
            return position[1]
        decision = getattr(self, "_preview_rule_decision", {})
        decision = decision if isinstance(decision, dict) else {}
        details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
        window = details.get("buy_window") if isinstance(details.get("buy_window"), dict) else {}
        status_text = str(details.get("status_text") or "")
        if decision.get("reason") == "BUY_WINDOW_WAIT":
            status_text = f"Có tín hiệu; chờ từ {window.get('start') or 'giờ mua đã cài'} và kiểm tra lại điều kiện."
        elif not decision:
            status_text = "Chưa có quyết định mới."
        return "\n".join(filter(None, (
            status_text,
            "BUY cần BOT ON, đủ vốn và OTP REAL.",
            "Lệnh gửi/khớp: xem bảng lệnh.",
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
            f"{symbol} · nguồn: {source} · nhận {received}\n"
            f"{state} · {age_text} · hạn {details['max_age_seconds']:g}s trong phiên."
        )

    def _api_health_preview_hint(self) -> str:
        return (
            "DAEMON: xử lý rule · DNSE/API: đọc tài khoản · WS: nhận giá.\n"
            "OTP: quyền gửi lệnh REAL.\nBấm HEALTH để xem lỗi và MỞ LOG; ⓘ để sao chép."
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
        health = status.get("api_health") if isinstance(status.get("api_health"), dict) else {}
        daemon_rest = health.get("rest") or {}
        ui_rest = self.real.api_health() if hasattr(getattr(self, "real", None), "api_health") else {}
        failures = []
        secrets = tuple(getattr(getattr(self, "real", None), key, "") for key in ("api_key", "api_secret", "trading_token"))
        for origin, rest in (("UI", ui_rest or {}), ("DAEMON", daemon_rest)):
            if not isinstance(rest, dict):
                continue
            last_status = rest.get("last_status")
            if int(rest.get("total_requests", 0) or 0) > 0 and (
                not isinstance(last_status, (int, float)) or not 200 <= last_status < 300
                or rest.get("last_error")
            ):
                code = (f"HTTP {int(last_status)}" if isinstance(last_status, (int, float)) and math.isfinite(last_status) and last_status
                        else "MẠNG/TIMEOUT" if not last_status else "HTTP CHƯA RÕ")
                detail = safe_detail(
                    f"{rest.get('last_endpoint') or 'chưa rõ endpoint'} · {code} · {rest.get('last_error') or 'chưa có chi tiết'}",
                    secrets=secrets,
                )
                request_at = _number(rest.get("last_request_at"))
                clock = datetime.fromtimestamp(request_at, VN_TZ).strftime("%d/%m %H:%M:%S") if request_at > 0 else ""
                failures.append(f"{origin} · Lần REST gần nhất: {detail}" + (f" · {clock}" if clock else ""))
            raw = rest.get("snapshot_errors") if isinstance(rest, dict) else None
            for row in (raw.values() if isinstance(raw, dict) else ()):
                if isinstance(row, dict):
                    if end_of_day_order_pause(row, phase):
                        failures.append(
                            "DNSE tạm ngừng đọc danh sách lệnh để xử lý cuối ngày (HTTP 400).\n"
                            "Tiền/cổ hiển thị theo dữ liệu đọc được; đối soát lệnh chờ API hoạt động lại.\n"
                            + safe_detail(row.get("error"), secrets=secrets)
                        )
                        continue
                    failures.append(snapshot_failure_summary(
                        row, IDLE_POLL_SECONDS if rest.get("account_poll_seconds") == IDLE_POLL_SECONDS else FAST_POLL_SECONDS,
                        secrets=secrets,
                    ))
        if failures:
            keep_context = phase in {"CALENDAR_LOADING", "CALENDAR_UNKNOWN"} or (
                bool(status.get("error")) and context.get("stage") != "ACCOUNT_SNAPSHOT"
            )
            extra = "\n".join(dict.fromkeys(([extra] if keep_context else []) + failures))
        return (
            self._quote_health_hint() + "\n" + extra
            + "\nOTP: cần khi gửi/sửa/hủy lệnh REAL; PAPER không cần OTP."
            + "\nWS OK chỉ là kết nối; xem GIÁ để biết mẫu còn mới."
            + "\nMất/phục hồi giá: có ghi trong daemon.log, không ghi mỗi tick."
            + "\nBấm ⓘ cạnh HEALTH để sao chép chẩn đoán này."
        )

    def _show_api_health_details(self, _event=None) -> None:
        from .windows import _window
        top = _window(self, "HEALTH · Chi tiết lỗi", "760x480")
        top.minsize(620, 360)
        top.grid_rowconfigure(0, weight=1)
        top.transient(self)
        log_dir = getattr(getattr(self, "bridge", None), "log_dir", None)
        paths = (f"\n\nThư mục log: {log_dir}\ndaemon.log: backend / API · ui.log: giao diện."
                 if log_dir is not None else "\nChưa có thư mục log tài khoản.")
        text = self._api_health_hint() + paths
        report = ctk.CTkTextbox(top, font=("Segoe UI", 14), wrap="word")
        report.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        report.insert("1.0", text)
        report.configure(state="disabled")
        controls = ctk.CTkFrame(top, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=12, pady=(6, 12))
        controls.grid_columnconfigure(0, weight=1)
        def open_logs():
            from tkinter import messagebox
            if log_dir is None or not Path(log_dir).is_dir():
                messagebox.showinfo("Log", f"Chưa có thư mục log.\n{log_dir or ''}", parent=top)
                return
            try:
                os.startfile(str(Path(log_dir).resolve()))
            except OSError:
                messagebox.showerror("Log", f"Không mở được thư mục:\n{log_dir}", parent=top)
        def copy():
            self.clipboard_clear()
            self.clipboard_append(text)
        ctk.CTkButton(controls, text="MỞ LOG", width=110, command=open_logs).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(controls, text="SAO CHÉP", width=110, command=copy).grid(row=0, column=1, padx=8)
        ctk.CTkButton(controls, text="ĐÓNG", width=100, command=top.destroy).grid(row=0, column=2)
        top.bind("<Escape>", lambda _event: top.destroy())

    def _copy_api_health_hint(self, _event: Any = None) -> None:
        # Copy the same sanitized, read-only text as the tooltip, without I/O.
        self.clipboard_clear()
        self.clipboard_append(self._api_health_hint())
        self.preview_health_hint.configure(text="✓")

        def restore() -> None:
            if self.running and self.preview_health_hint.winfo_exists():
                self.preview_health_hint.configure(text="ⓘ")
        self.after(1500, restore)

    def _ticket_money_hint(self) -> str:
        return str(getattr(self, "_preview_ticket_money", {}).get("hint") or "Chờ tính vốn và phí của lệnh đang xem.")

    def _ticket_fee_hint(self) -> str:
        money = getattr(self, "_preview_ticket_money", {})
        if money.get("fee") is None:
            return "Chưa có giá/khối lượng hoặc phí DNSE; dấu — không phải miễn phí."
        return f"Phí mua {money['fee']:,.0f} đ đã nằm trong VỐN + PHÍ; không cộng lần nữa. Không gồm phí/thuế bán."

    def _auto_quantity_hint(self) -> str:
        feedback = (getattr(self, "_preview_manual_feedback", {}) if self.quantity.get().strip()
                    else getattr(self, "_preview_auto_feedback", {}))
        current = str(feedback.get("hint") or "").splitlines()
        return ("KL AUTO: tính số CP theo ngân sách của mã.\nKL TAY: nhập số CP; xóa hết để về AUTO."
                " Đổi ngân sách tại RULE → Priority."
                + (f"\n{current[0]}" if current else ""))

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
        saved_cross = dict(details.get("ema_cross") or {})
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
        asof = indicators.get("indicator_observed_at") or details.get("updated_at", "")
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
            if len(completed) >= 14:
                cached_value, cached_pct = average_true_range_snapshot(completed)
                reported_pct = _number(details.get("atr14_daily_pct"))
                if reported_pct <= 0:
                    details["atr14_daily_value"], details["atr14_daily_pct"] = cached_value, cached_pct
                    details["atr14_daily_asof"] = completed[-1]["time"]
                elif _number(details.get("atr14_daily_value")) <= 0 and math.isclose(reported_pct, cached_pct, rel_tol=1e-6):
                    details["atr14_daily_value"] = cached_value
        details["indicators"] = {**expected_periods, **indicators}
        if source != "DECISION":
            details.pop("ema_cross", None)  # A price-only fallback cannot prove an observed crossing.
        phase = str((status.get("symbol_phases") or {}).get(symbol) or status.get("market_status") or "").upper()
        read_memory = getattr(getattr(self, "rule_state", None), "session_cross", None)
        current_memory = dict(read_memory(symbol, mode) or {}) if callable(read_memory) else {}
        profile = f"{params.buy_ema_fast}/{params.buy_ema_slow}"
        # The displayed decision may be stale after the market closes. Prefer
        # the newest book-local durable memory, including a newer watch reset.
        candidates = (current_memory, saved_cross)
        memory = max(
            (row for row in candidates if row and row.get("profile") in {None, "", profile}),
            key=lambda row: finite(row.get("observed_at_epoch")) or finite(row.get("started_at_epoch")) or 0,
            default={},
        )
        if memory.get("tracking_version") == 2:
            from ..rules.ema_memory import refresh_memory
            details["ema_memory_preview_source"] = dict(memory)
            memory = refresh_memory(memory, now=datetime.now(VN_TZ), mode=params.buy_ema_cross_mode,
                                    sessions=params.buy_ema_cross_sessions, exchange=self._symbol_exchange(symbol),
                                    holidays=self.settings.trading_holidays,
                                    va_lookback_enabled=params.buy_ema_va_lookback_enabled)
            if phase in {"CLOSED", "WEEKEND", "HOLIDAY"}:
                memory.update(state="MARKET_CLOSED", valid=False)
            elif source != "DECISION":
                memory.update(state="UNKNOWN", valid=False)
            details["ema_cross"] = memory
        if phase in {"CLOSED", "WEEKEND", "HOLIDAY"}:
            # Display history separately from the evidence that can approve BUY.
            now = market_now()
            exchange = (status.get("symbol_exchanges") or {}).get(symbol) or "HOSE"
            details["ema_cross_preview_closed"] = True
            details["ema_cross_preview_expired"] = (phase in {"WEEKEND", "HOLIDAY"}
                or now.hour * 60 + now.minute >= exchange_close_minute(exchange))
            details["ema_cross_last_preview"] = self._last_closed_ema_preview(
                symbol, mode, params, saved_cross, rows,
            )
        atr = _number(details.get("atr14_daily_pct"))
        details["dynamic_start_pct"] = atr * params.normal_atr_activation_multiplier if params.normal_atr_activation_enabled else 0.0
        details["dynamic_trail_pct"] = atr * params.normal_atr_multiplier if params.normal_atr_trail_enabled else 0.0
        self._preview_indicator_source = {"symbol": symbol, "source": source, "asof": asof,
                                          "signal_ready": indicators.get("signal_ready"),
                                          "atr_asof": details.get("atr14_daily_asof", "")}
        return details

    def _last_closed_ema_preview(self, symbol, mode, params, saved_cross, rows) -> dict[str, Any]:
        profile = f"{params.buy_ema_fast}/{params.buy_ema_slow}"
        candidates = [saved_cross]
        rule_state = getattr(self, "rule_state", None)
        if rule_state is not None:
            candidates.append(rule_state.session_cross(symbol, mode))
        now = market_now()
        for row in sorted(candidates, key=lambda value: finite(value.get("observed_at_epoch")) or 0, reverse=True):
            stamp, expires = finite(row.get("observed_at_epoch")), finite(row.get("expires_at"))
            fast, slow = finite(row.get("current_fast")), finite(row.get("current_slow"))
            closed_observation = (stamp is not None and 0 < stamp <= now.timestamp()
                and (row.get("tracking_version") == 2 or (expires is not None and stamp < expires <= now.timestamp())))
            if (row.get("profile") != profile or not row.get("current_ready") or fast is None or slow is None
                    or not closed_observation):
                continue
            try:
                at = datetime.fromtimestamp(stamp, VN_TZ)
            except (ValueError, OverflowError, OSError):
                continue
            clock = at.strftime("%H:%M" if at.date() == now.date() else "%d/%m %H:%M")
            return {"fast": fast, "slow": slow, "label": f"CUỐI {clock}", "source": "OBSERVED"}
        closed = [row for row in rows if bool(row.get("closed", True))]
        if closed:
            marks = indicator_snapshot(closed, params.buy_ema_fast, params.buy_ema_slow, params.rsi_period)
            fast, slow = finite(marks.get("buy_ema_fast")), finite(marks.get("buy_ema_slow"))
            if fast is not None and slow is not None:
                at = datetime.fromtimestamp(float(closed[-1]["time"]), VN_TZ)
                return {"fast": fast, "slow": slow, "label": f"ĐÓNG {at:%d/%m}", "source": "DAILY_CLOSE"}
        return {}

    def _indicator_preview_hint(self) -> str:
        preview = getattr(self, "_preview_indicator_source", {})
        source = preview.get("source", "MISSING")
        params = self.settings.rule_parameters
        buy_ema_rule = (
            "BUY cần EMA nhanh cắt từ dưới lên trên EMA chậm."
            if params.get("buy_signal_require_ema_cross", True)
            else "BUY chỉ cần EMA nhanh > EMA chậm, không cần lần cắt."
        ) if params.get("buy_signal_use_ema", True) else "BUY không dùng điều kiện EMA."
        rsi_basis = (
            "RSI: phiên trước → phiên nay, không so với tick trước."
            if str(self.settings.signal_mode).upper() == "REALTIME"
            else "RSI: hai phiên đã đóng liên tiếp, không so với tick trước."
        )
        basis = "cập nhật theo giá trong phiên" if str(self.settings.signal_mode).upper() == "REALTIME" else "chỉ dùng nến đã đóng"
        text = (
            f"EMA 1D: {basis}.\n{buy_ema_rule}\n"
            f"{rsi_basis} BUY cần tăng, E cần giảm; bằng không đạt.\n"
            "Preview chỉ để xem, không tạo tín hiệu hoặc đặt lệnh."
        )
        if preview.get("signal_ready") is False:
            return text + "\nChờ mẫu phút; chỉ số nền 1D chưa dùng để BUY/E."
        if source == "MISSING":
            return text + "\nDấu --: chưa đủ nến; chờ daemon tải lịch sử."
        return text

    def _render_ema_cross_preview(self, details: dict[str, Any]) -> None:
        widget = getattr(self, "preview_rule_cross", None)
        if widget is None:
            return
        params = self.settings.rule_parameters
        required = bool(params.get("buy_signal_use_ema", True)
                        and params.get("buy_signal_require_ema_cross", True))
        self._preview_ema_details = details
        evidence = dict(details.get("ema_cross") or {})
        gate_evidence = evidence
        raw = dict(details.get("ema_memory_preview_source") or evidence)
        rule_mode = params.get("buy_ema_cross_mode", "VA")
        view_mode = ("SESSIONS" if rule_mode == "VA" else "VA") if getattr(self, "_preview_ema_alternate", False) else rule_mode
        tracking = raw.get("tracking_version") == 2
        history_status = {}
        loader = getattr(self, "_alternate_ema_history", None)
        if tracking and view_mode == "SESSIONS" and getattr(self, "_preview_ema_alternate", False) and callable(loader):
            from .ema_history import with_history
            history_status = loader(raw)
            raw = with_history(raw, history_status)
        self._preview_ema_history_status = history_status
        if tracking:
            from ..rules.ema_memory import refresh_memory
            if raw.get("cross_id") and not raw.get("cross_source"):
                raw["cross_source"] = "LIVE"
            evidence = refresh_memory(raw, now=market_now(), mode=view_mode,
                                      sessions=int(params.get("buy_ema_cross_sessions", 3)),
                                      exchange=raw.get("exchange") or "HOSE", holidays=self.settings.trading_holidays,
                                      va_lookback_enabled=bool(params.get("buy_ema_va_lookback_enabled", False)))
            if (view_mode == "SESSIONS" or (view_mode == "VA" and params.get("buy_ema_va_lookback_enabled"))) and raw.get("lookback_ready") is not True and raw.get("cross_source") != "LIVE":
                evidence.update(ready=False, valid=False, state="UNKNOWN")
            gate_evidence = dict(evidence)
            if details.get("ema_cross_preview_closed"):
                evidence["valid"] = False
                if evidence["state"] in {"SESSION_ACTIVE", "WAIT_UP", "WAIT_DOWN"}:
                    evidence["state"] = "MARKET_CLOSED"
        if (details.get("ema_cross_preview_expired") and not evidence.get("tracking_version") == 2
                and required and self.settings.signal_mode == "REALTIME"):
            evidence.update(state="EXPIRED")
        if not required and not tracking:
            evidence.update(required=False, state="OFF")
        if not tracking:
            gate_evidence = evidence
        caption, role = ema_cross_caption(evidence, None if details.get("ema_cross_preview_expired")
                                         else details.get("buy_window"))
        # Report the EMA prerequisite independently of the market being closed
        # and of the remaining RSI, money, bot and order-execution checks.
        gate_state = gate_evidence.get("state", "UNKNOWN")
        if not required:
            gate_text, gate_color = "EMA CẮT: OFF", COL_MUTED
        elif gate_state == "UNKNOWN":
            gate_text, gate_color = "EMA CẮT: CHỜ", COL_MUTED
        else:
            allowed = (bool(gate_evidence.get("ready") and gate_evidence.get("valid")
                            and not gate_evidence.get("used") and gate_state == "SESSION_ACTIVE")
                       if tracking else role == "ok")
            gate_text, gate_color = ("EMA CẮT: ĐẠT", COL_GREEN) if allowed else ("EMA CẮT: CHƯA ĐẠT", COL_WARN)
        self.preview_ema_entry_gate._viking_full_gate_text = gate_text
        self.preview_ema_entry_gate.configure(text=gate_text, text_color=gate_color)
        self._preview_ema_gate_evidence = gate_evidence
        self._preview_ema_cross_evidence = (caption, evidence)
        prior_sessions = (int(params.get("buy_ema_cross_sessions", 3))
                          if view_mode == "SESSIONS" or params.get("buy_ema_va_lookback_enabled") else 0)
        if tracking:
            from .ema_history import view_counts
            counts = view_counts(evidence, now=market_now(), prior_sessions=prior_sessions,
                                 holidays=self.settings.trading_holidays)
            if history_status and not history_status.get("ready"):
                counts.update(up=None, down=None)
            total_crosses = counts["up"] + counts["down"] if counts["up"] is not None else "—"
            self.preview_rule_cross_counts.configure(
                text=f"{total_crosses} CẮT · VA" if counts.get("retained") else f"{total_crosses} CẮT - {prior_sessions}P",
                text_color={"ok": COL_GREEN, "wait": COL_WARN}.get(role, COL_MUTED),
            )
            self.preview_rule_cross_counts.grid()
        else:
            self.preview_rule_cross_counts.configure(text=f"— CẮT - {prior_sessions}P", text_color=COL_MUTED)
        title = (f"∞ VA+{int(params.get('buy_ema_cross_sessions', 3))}P" if params.get("buy_ema_va_lookback_enabled") else "∞ VA") if view_mode == "VA" else f"⏳ {int(params.get('buy_ema_cross_sessions', 3))}P"
        self.preview_rule_cross_title.configure(text=title, text_color=COL_WARN if getattr(self, "_preview_ema_alternate", False) else COL_PREVIEW_TEXT)
        from .ema_history import preview_info
        information = dict(evidence)
        if not tracking and not information.get("cross_at"):
            saved = (details.get("buy_window") or {}).get("ema_cross") or {}
            if saved.get("crossed_up"):
                information["cross_at"] = saved.get("cross_at")
        text, info_role = preview_info(information, now=market_now(), mode=view_mode,
                                      sessions=int(params.get("buy_ema_cross_sessions", 3)),
                                      holidays=self.settings.trading_holidays, history_status=history_status)
        widget._viking_full_detail_text = widget._viking_compact_detail_text = text
        widget.configure(text=text, text_color={"ok": COL_GREEN, "down": COL_RED,
                                               "wait": COL_WARN}.get(info_role, COL_MUTED))
        fit = getattr(self, "_fit_phase2_row", None)
        if callable(fit) and widget.master.winfo_width() > 1:
            fit()

    def _swap_ema_preview(self) -> None:
        self._preview_ema_alternate = not getattr(self, "_preview_ema_alternate", False)
        self._render_ema_cross_preview(getattr(self, "_preview_ema_details", {}))

    def _ema_entry_gate_hint(self) -> str:
        evidence = getattr(self, "_preview_ema_gate_evidence", {})
        baseline = evidence.get("baseline") or {}
        fast, slow = finite(baseline.get("buy_ema_fast")), finite(baseline.get("buy_ema_slow"))
        params = self.settings.rule_parameters
        pair = f"EMA{params.get('buy_ema_fast', 3)}", f"EMA{params.get('buy_ema_slow', 6)}"
        if fast is None or slow is None:
            initial = "Chưa có mẫu đầu đã lưu."
        elif fast < slow:
            initial = f"Mẫu đầu: {pair[0]} < {pair[1]}. Chỉ cần một lần cắt lên."
        elif fast > slow:
            initial = f"Mẫu đầu: {pair[0]} > {pair[1]}. Chưa có lần cắt: chờ xuống rồi lên."
        else:
            initial = "Mẫu đầu bằng nhau: chưa tính là giao cắt."
        return initial + "\nĐẠT chỉ là điều kiện EMA; BUY còn RSI, giờ mua và vốn."

    def _ema_cross_hint(self) -> str:
        """Explain the counter and entry condition only from the explicit help icon."""
        lines = self._ema_memory_preview_hint().splitlines()
        evidence = getattr(self, "_preview_ema_gate_evidence", {})
        params = self.settings.rule_parameters
        fast, slow = params.get("buy_ema_fast", 3), params.get("buy_ema_slow", 6)
        state = evidence.get("state", "UNKNOWN")
        condition = {
            "WAIT_UP": f"Chờ EMA{fast} cắt từ dưới lên trên EMA{slow}.",
            "WAIT_DOWN": "Đang ở trên, chưa có cắt lên: chờ xuống rồi lên.",
            "SESSION_ACTIVE": "Cắt lên đạt; BUY còn RSI, giờ mua và vốn.",
            "SESSION_USED": "Lần cắt lên đã dùng; cần lần cắt lên mới.",
            "EXPIRED": "Lần cắt lên hết hạn; cần lần cắt lên mới.",
        }.get(state, "Chưa đủ dữ liệu xác nhận cắt lên.")
        if evidence.get("tracking_version") != 2:
            condition = getattr(self, "_preview_ema_cross_evidence", (condition, {}))[0].removeprefix("CẮT EMA · ")
        if not (params.get("buy_signal_use_ema", True) and params.get("buy_signal_require_ema_cross", True)):
            condition = "Điều kiện chờ EMA cắt lên đang OFF."
        from .ema_history import watch_started
        admitted = watch_started(evidence, now=market_now())
        started = f"\nTheo dõi từ {admitted:%d/%m %H:%M}." if admitted else ""
        return f"{lines[0]}\n{condition}\n{lines[-1]}{started}"

    def _ema_memory_preview_hint(self) -> str:
        from .ema_history import view_counts
        _caption, evidence = getattr(self, "_preview_ema_cross_evidence", ("", {}))
        active = self.settings.rule_parameters.get("buy_ema_cross_mode", "VA")
        mode = evidence.get("mode", active)
        sessions = int(self.settings.rule_parameters.get("buy_ema_cross_sessions", 3))
        scan = sessions if mode == "SESSIONS" or self.settings.rule_parameters.get("buy_ema_va_lookback_enabled") else 0
        counts = view_counts(evidence, now=market_now(), prior_sessions=scan,
                             holidays=self.settings.trading_holidays)
        period = ((f"{counts['start']} → {counts['end']}" if counts['start'] else "Chưa đủ lịch phiên")
                  if scan else "Từ lúc theo dõi mã (VA)")
        counted = (f"{period}: {counts['up']} lên + {counts['down']} xuống."
                   if counts["up"] is not None else f"{period}: chưa đủ dữ liệu để đếm.")
        if counts["partial"]:
            counted += f" Đếm từ {str(counts['from'])[11:16]}."
        if counts.get("retained"):
            from ..rules.ema_memory import memory_day_counts
            daily = memory_day_counts(evidence, now=market_now())
            if daily["up"] is not None:
                counted += f"\nHôm nay: {daily['up']} lên + {daily['down']} xuống."
        history_status = getattr(self, "_preview_ema_history_status", {})
        if history_status.get("pending"):
            counted = f"Đang tải lịch sử {scan} phiên trước."
        elif history_status and not history_status.get("ready"):
            counted = {
                "CONNECTION": "Cần kết nối DNSE để tải lịch sử.",
                "DAILY_HISTORY": "Chưa đủ nến ngày nền để tính EMA; thử lại sau 60 giây.",
                "CALENDAR": "Chưa đủ lịch phiên để quét.",
            }.get(history_status.get("issue"), "API lịch sử chưa sẵn sàng; thử lại sau 60 giây.")
        when = str(evidence.get("cross_at") or "")
        remembered = f" {when[8:10]}/{when[5:7]}" if len(when) >= 10 else ""
        if mode == "SESSIONS":
            expiry = finite(evidence.get("expires_at"))
            deadline = datetime.fromtimestamp(expiry, VN_TZ) if expiry else None
            policy = (f"Còn {evidence.get('remaining_sessions', 0)}/{sessions}P; hạn {deadline:%d/%m %H:%M}, gồm phiên cắt."
                      if deadline else f"Cắt lên có hiệu lực {sessions} phiên, tính cả phiên cắt.")
        else:
            policy = f"VA: nhớ lần cắt lên{remembered} tới khi dùng."
        history = (f"{scan}P: hôm nay + {scan} phiên trước; bỏ ngày nghỉ." if scan else
                   "VA: đếm các lần cắt đã quan sát từ lúc theo dõi, giữ qua ngày và restart.")
        return f"{counted}\n{history}\n{policy} ⇄ chỉ đổi góc xem."

    def _atr_preview_hint(self) -> str:
        from datetime import datetime
        preview = getattr(self, "_preview_indicator_source", {})
        asof = preview.get("atr_asof", "")
        try:
            asof = datetime.fromtimestamp(float(asof), VN_TZ).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            asof = "chưa có đủ nến"
        return (
            "ATR14: biến động giá, lấy nến ngày đã đóng.\n"
            "% = ATR / giá đóng phiên cuối. START / LÙI dùng % × hệ số.\n"
            f"Phiên cuối: {asof}."
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
            explanation = "Tỷ trọng chọn tay trong RULE.\n"
        elif current.get("confirmation_pending"):
            from ..rules.business import StaticRuleParameters
            candidate_pct = StaticRuleParameters.from_dict(self.settings.rule_parameters).exposure.get(state)
            target = f" (CP {candidate_pct * 100:g}% / TIỀN {100 - candidate_pct * 100:g}%)" if candidate_pct is not None else ""
            explanation = (
                f"Xác nhận {labels.get(state, 'ĐANG TÍNH')}: {count}/{required} phiên{target}; "
                f"đang chờ vẫn dùng {active}.\n"
            )
        else:
            explanation = "Tỷ trọng theo VNINDEX.\n"
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
                f"{example}"
                "⇄ chỉ đổi góc xem.")

    def _entry_capital_hint(self) -> str:
        summary = getattr(self, "_preview_entry_summary", "chờ dữ liệu")
        return (
            f"{summary}.\n"
            "Mã = số mã đang giữ hoặc chờ mua / tối đa.\n"
            "AUTO tính số CP theo ngân sách mã đang chọn. Đổi tiền mua tại RULE → Priority; MANUAL dùng KL nhập tay."
        )

    def _whipsaw_preview_hint(self) -> str:
        summary = getattr(self, "_preview_whipsaw_summary", "WHIPSAW: chờ dữ liệu")
        return (f"{summary}.\n"
                "Số lần cắt / ngưỡng khóa BUY; đếm giữa nến ngày, không cộng từng tick VA.\n"
                "LỖ: chuỗi lỗ / ngưỡng khóa. SELL vẫn hoạt động.")

    def _protect_preview_hint(self) -> str:
        preview = getattr(self, "_preview_protect_prices", {})
        start = getattr(self, "_preview_dynamic_start", {})
        if start.get("auto_sell_paused"):
            return "Tự bán vị thế đang tạm dừng; SL/TP/PROTECT/E chỉ báo.\nChuột phải vị thế để bật lại khi muốn tiếp tục."
        if start.get("history_unverified"):
            return "Lịch sử đỉnh/đáy chưa xác minh từ khớp lệnh G1. PROTECT đang khóa.\nChuột phải vị thế để đo lại MAE/MFE; không xóa vị thế hoặc lịch sử khớp."
        arm, sell = preview.get("arm", 0), preview.get("sell", 0)
        example = (
            f"Nếu đỉnh chỉ tới ARM {_display_price(arm)} đ → mốc bán {_display_price(sell)} đ.\n"
            if arm > 0 else "Chưa có giá vào để tính ARM và mốc bán.\n"
        )
        actual = preview.get("actual_sell")
        if actual is not None and actual > 0:
            example = f"Mốc bán của vị thế: {_display_price(actual)} đ; theo đỉnh đã ghi nhận.\n"
        start = getattr(self, "_preview_dynamic_start", {})
        if start:
            basis = "giá vốn vị thế" if start["position"] else "giá vào lệnh nháp"
            target = f"{_display_price(start['start'])}đ" if start["start"] > 0 else "chờ dữ liệu"
            if not start["start_enabled"]:
                target = "sau lần có lãi đầu tiên"
            stage = preview.get("actual_stage", "ARM" if start["state"] == "SAU ARM" else "DYN")
            policy = policy_for_stage(self.settings.rule_parameters or {}, stage)
            action = "chỉ cảnh báo" if policy == "ALERT" else "xét bán theo KL"
            floor = f"MỐC {_display_price(actual)}đ · " if actual is not None and actual > 0 else ""
            return (
                f"Theo {basis}: {_display_price(start['entry'])}đ.\n"
                f"START {target} · {start['state']}.\n"
                f"{floor}{stage} {policy}: {action} khi giá lùi chạm mốc theo đỉnh."
            )
        return (
            "START bật theo dõi; giá lùi chạm MỐC/BÁN mới xử lý.\n"
            "ARM: bắt đầu theo đỉnh. Đỉnh cao hơn → mốc BÁN tăng.\n"
            f"{example}"
            "ARM và DYNAMIC có AUTO/ALERT riêng; AUTO bán, ALERT chỉ báo.\n"
            "Chưa có cổ bán được: không tạo SELL PROTECT."
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

    def _dynamic_start_context(self, symbol: str, draft_entry: float, details: dict[str, Any]) -> dict[str, Any]:
        params = self.settings.rule_parameters or {}
        entry, position, enabled, mfe = draft_entry, False, False, None
        current, profit = finite(details.get("current_price")), finite(details.get("current_profit_pct"))
        if current is not None and current > 0 and profit is not None and profit > -100:
            entry, position = current / (1 + profit / 100), True
            enabled = "NORMAL" in (details.get("em_modes") or [])
            mfe = finite(details.get("normal_mfe_pct", details.get("peak_profit_pct")))
        store = getattr(self, "trade_state", None)
        active = store.active_for(symbol, self.mode.get()) if store is not None else None
        paused = details.get("auto_sell_enabled") is False or details.get("bot_trade_enabled") is False
        unverified = bool(details.get("protection_history_unverified"))
        if active and active.open_quantity > 0 and active.avg_entry_price > 0:
            from ..trading.position_controls import position_exit_params
            params = position_exit_params(params, active)
            entry, position, enabled = active.avg_entry_price, True, "NORMAL" in active.em_modes
            paused = not getattr(active, "auto_sell_enabled", True) or not getattr(active, "bot_trade_enabled", True)
            if getattr(self, "rule_state", None) is not None:
                metrics = self.rule_state.position_metrics(symbol, active.id)
                unverified = unverified or (bool(metrics) and metrics.get("price_history_verified") is not True)
                if unverified:
                    mfe = None
                    details = dict(details, normal_trigger_price=0)
                elif mfe is None:
                    mfe = finite(metrics.get("peak_profit_pct"))
                if mfe is not None and not unverified:
                    rule = StaticRuleParameters.from_dict(params)
                    mfe = protect_peak_for_sellable_phase(
                        mfe, finite(metrics.get("normal_sellable_peak_profit_pct")) or 0,
                        rule.normal_arm_pct,
                        reset_applied=bool(rule.normal_dynamic_enabled and rule.normal_t2_reset_enabled
                                           and metrics.get("normal_t2_reset_applied")),
                    )
                    if mfe + 1e-9 < rule.normal_arm_pct:
                        details = dict(details, protection_stage="DYN", normal_trigger_price=dynamic_floor_previous(
                            metrics, rule.normal_retention_pct,
                        ))
        preview = dynamic_start_preview(entry, params, details, position=position, enabled=enabled, mfe=mfe)
        preview.update(auto_sell_paused=paused, history_unverified=unverified)
        if paused:
            preview["policy"] = "ALERT"
        return preview

    def _exit_preview_hint(self) -> str:
        preview = getattr(self, "_preview_exit_state", {})
        price = _number(preview.get("price"))
        price_text = f"{_display_price(price)} đ" if price > 0 else "chưa có giá"
        signal = preview.get("signal", "--")
        enabled = bool(preview.get("enabled"))
        quantity = int(preview.get("quantity", 0))
        policy = preview.get("policy", "ALERT")
        status = ("CHƯA CÓ CỔ" if quantity <= 0 else
                  "CÓ TÍN HIỆU THOÁT" if signal == "SELL" else "CHỜ TÍN HIỆU THOÁT")
        behavior = ("tự gửi bán 100% phần còn lại khi có tín hiệu và đủ điều kiện lệnh."
                    if policy == "AUTO" else "chỉ báo tín hiệu, không đặt lệnh.")
        behavior = (f"{policy}: {behavior}" if enabled else
                    f"OFF: chỉ preview. Khi bật {policy}: {behavior}")
        return (
            f"E thoát theo EMA/RSI · giữ {quantity:,} CP · {status}.\n"
            f"Giá TT: {price_text}.\n"
            f"{behavior}"
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
        fit_header = getattr(self, "_fit_order_preview_header", None)
        if callable(fit_header):
            fit_header()
        self.preview_live_value.configure(
            text=(
                _display_price(live_price)
                if live_price > 0 else "NA"
            ),
            text_color=COL_WARN if live_price > 0 and frozen else COL_TEXT,
            fg_color=quote_background(self, "preview", symbol, live_price, tick, market_status, frozen=frozen),
        )
        self.preview_entry_value.configure(
            text=(
                _display_price(entry_price)
                if entry_price > 0 else "NA"
            ),
            text_color=COL_WARN if entry_price > 0 and frozen and order_type != "LO" else COL_TEXT,
        )
        quantity_source = "AUTO" if forced_minimum else "VỐN" if auto_quantity else "TAY"
        self.preview_qty_title._viking_full_metric_key = f"KL · {quantity_source}"
        self.preview_qty_title.configure(
            text="KL" if getattr(self, "_preview_page_layout", "") == "compact_overview" else f"KL · {quantity_source}",
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
            text=tp_value.split('·')[-1].strip(),
            text_color=COL_GREEN if tp_pnl is not None and tp_pnl >= 0 else COL_TEXT,
        )
        self.preview_tp_detail.configure(
            text=f"+{_compact_vnd(tp_pnl)}" if tp_pnl is not None and tp_pnl >= 0 else ""
        )
        self.preview_sl_value.configure(
            text=sl_value.split('·')[-1].strip(),
            text_color=COL_RED if sl_pnl is not None and sl_pnl <= 0 else COL_TEXT,
        )
        self.preview_sl_detail.configure(
            text=f"-{_compact_vnd(abs(sl_pnl))}" if sl_pnl is not None and sl_pnl <= 0 else ""
        )
        self.preview_route_value.configure(text=route, text_color=badge_fg)
        params = self.settings.rule_parameters if isinstance(self.settings.rule_parameters, dict) else {}
        trade_store = getattr(self, "trade_state", None)
        active_cycle = trade_store.active_for(self.symbol.get(), self.mode.get()) if trade_store else None
        if active_cycle:
            from ..trading.position_controls import position_exit_params
            params = position_exit_params(params, active_cycle)
        indicator_exit_policy = str(
            params.get("indicator_exit_policy", "ALERT") or "ALERT"
        ).upper()
        normal_arm = float(params.get("normal_arm_pct", 7.0) or 7.0)
        normal_giveback = float(params.get("normal_giveback_pct", 2.0) or 2.0)
        normal_policy = str(params.get("normal_policy", "AUTO") or "AUTO").upper()
        dynamic_policy = policy_for_stage(params, "DYN")
        normal_dynamic = bool(params.get("normal_dynamic_enabled", False))
        normal_repeat = bool(params.get("normal_repeat_enabled", True))
        normal_sell = float(params.get("normal_sell_pct", 33.0) or 33.0)
        normal_basis = "GỐC" if params.get("normal_sell_basis", "ORIGINAL") == "ORIGINAL" else "CÒN"
        dynamic_sell = float(params.get("dynamic_sell_pct", 33.0) or 33.0)
        dynamic_basis = "GỐC" if params.get("dynamic_sell_basis", "ORIGINAL") == "ORIGINAL" else "CÒN"
        dynamic_repeat = bool(params.get("dynamic_repeat_enabled", True) and dynamic_sell < 100)
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
                f"ARM: {_display_price(normal_price)} · {'MỐC' if normal_policy == 'ALERT' else 'BÁN'}: {_display_price(normal_preview_sell)}"
                if normal_price > 0 else "CHƯA CÓ GIÁ"
            ),
        )
        self.preview_normal_detail.configure(
            text=(
                f"{normal_policy} · ARM {normal_arm:g}% · TRAIL {normal_giveback:g}% · BÁN {normal_sell:g}%"
                f"{' · LẶP' if normal_repeat and normal_sell < 100 else ''}"
            ),
        )
        arm_detail = f"ARM {normal_policy} · " + ("CHỈ BÁO" if normal_policy == "ALERT"
                     else f"{normal_sell:g}% {normal_basis}{' ↻' if normal_repeat and normal_sell < 100 else ''}")
        dynamic_detail = (f"DYN {dynamic_policy} · " + ("CHỈ BÁO" if dynamic_policy == "ALERT"
                          else f"{dynamic_sell:g}% {dynamic_basis}{' ↻' if dynamic_repeat else ''}")) if normal_dynamic else "DYN OFF"
        self._preview_protect_detail = (f"{arm_detail} · {dynamic_detail}", f"ARM {normal_policy} · DYN {dynamic_policy if normal_dynamic else 'OFF'}")
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
        atr_value = _number(decision_details.get("atr14_daily_value"))
        start_pct = _number(decision_details.get("dynamic_start_pct"))
        trail_pct = _number(decision_details.get("dynamic_trail_pct"))
        self.preview_atr.configure(
            text=(f"{_display_price(atr_value)}đ {atr_pct:.2f}%" if atr_value > 0
                  else f"{atr_pct:.2f}%" if atr_pct > 0 else "--"),
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
                (indicator_exit_policy if key == "indicator_exit" else
                 normal_policy if normal_policy == dynamic_policy or not normal_dynamic else "AUTO/ALERT") if enabled else "OFF"
            )
            widget.configure(
                text=f"{em_labels[key]} · {state_label}",
                text_color=(
                    COL_WARN if enabled and (indicator_exit_policy if key == "indicator_exit" else normal_policy) == "ALERT"
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
            normal_policy = policy_for_stage(params, str(decision_details.get("protection_stage", "ARM")))
            protected = decision_details.get("normal_trigger_price")
            protect_state = str(decision_details.get("normal_state", "WAIT") or "WAIT").upper()
            effective_trail = decision_details.get("normal_effective_trail_pct")
            self._preview_protect_prices["actual_sell"] = _number(protected)
            self._preview_protect_prices["actual_stage"] = str(decision_details.get("protection_stage", "ARM"))
            state_label = {"WAIT": "CHỜ", "ARM": "ĐÃ ARM", "DYN": "DYNAMIC",
                           "WAITING_SETTLEMENT": "CHỜ CỔ VỀ",
                           "REARM": "CHỜ LẶP", "DONE": "ĐÃ BÁN",
                           "ARMED": "ĐÃ ARM", "DYNAMIC": "DYNAMIC", "ALERT": "CHẠM MỐC",
                           "TRIGGERED": "CHẠM MỐC"}.get(protect_state, protect_state)
            self.preview_normal_value.configure(
                text=(
                    f"{state_label} · {'MỐC' if normal_policy == 'ALERT' else 'BÁN'}: {_display_price(protected)}"
                    if _number(protected) > 0 else
                    f"{state_label} · PNL {_number(current_profit):+.1f}%"
                )
            )
            if effective_trail is not None:
                selected_pct = _number(decision_details.get("sell_share_pct", normal_sell))
                selected_basis = "GỐC" if decision_details.get("sell_basis", "ORIGINAL") == "ORIGINAL" else "CÒN"
                self._preview_protect_prices["sell_pct"] = selected_pct
                stage_label = "DYN" if decision_details.get("protection_stage") == "DYN" else "ARM"
                self._preview_protect_detail = (
                    _dynamic_atr_preview_text(decision_details, params),
                    f"{stage_label} {normal_policy} · " + ("CHỈ BÁO" if normal_policy == "ALERT"
                        else f"BÁN {selected_pct:g}% {selected_basis}"),
                )
                self.preview_normal_detail.configure(
                    text=_dynamic_atr_preview_text(decision_details, params),
                )
        fit_label_text(self.preview_normal_value, base_font=("Cascadia Mono", 12))
        self._preview_dynamic_start = self._dynamic_start_context(symbol, entry_price, decision_details)
        preview_entry = self._preview_dynamic_start["entry"]
        if self._preview_dynamic_start["position"] and preview_entry > 0:
            self._preview_protect_prices["arm"] = preview_entry * (1 + normal_arm / 100)
            self._preview_protect_prices["sell"] = protect_level(preview_entry, normal_arm, normal_arm, normal_giveback).trigger_price
            if not (current_profit is not None and _number(decision_details.get("normal_trigger_price")) > 0):
                self.preview_normal_value.configure(text=(
                    f"ARM: {_display_price(self._preview_protect_prices['arm'])} · "
                    f"{'MỐC' if policy_for_stage(params, 'ARM') == 'ALERT' else 'BÁN'}: {_display_price(self._preview_protect_prices['sell'])}"
                ))
                fit_label_text(self.preview_normal_value, base_font=("Cascadia Mono", 12))
        self._preview_protect_detail = (
            start_caption(self._preview_dynamic_start), start_caption(self._preview_dynamic_start, compact=True),
        )
        protect_on = self._preview_dynamic_start["enabled"] if self._preview_dynamic_start["position"] else self._em_states.get("normal_protection", False)
        self.preview_em_normal.configure(
            text=f"PROTECT · {'ON' if protect_on else 'OFF'} · ARM {policy_for_stage(params, 'ARM')}",
            text_color=COL_GREEN if protect_on else COL_MUTED,
        )
        if self._preview_dynamic_start.get("auto_sell_paused"):
            self.preview_em_normal.configure(text="PROTECT · TẠM DỪNG", text_color=COL_WARN)
            self.preview_normal_value.configure(text="TỰ BÁN TẠM DỪNG")
            self.preview_em_exit.configure(text="E · ALERT", text_color=COL_WARN)
        elif self._preview_dynamic_start.get("history_unverified"):
            self.preview_normal_value.configure(text="CHỜ XÁC MINH GIÁ")
            self._preview_protect_prices.pop("actual_sell", None)
        fit_label_text(self.preview_normal_value, base_font=("Cascadia Mono", 12))
        self.preview_normal_detail.configure(text=self._preview_protect_detail[0])
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
            key = f"{'E EXIT' if prefix == 'E' else 'EMA BUY'} {fast_period}/{slow_period}:"
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
        rsi_text, rsi_color = _rsi_comparison_preview(
            indicators.get("rsi"), indicators.get("rsi_previous"),
        )
        for name, key, value, color, widget in (
            ("buy_ema", buy_ema_key, buy_ema_text, buy_ema_color, self.preview_rule_ema),
            ("sell_ema", sell_ema_key, sell_ema_text, sell_ema_color, self.preview_rule_sell_ema),
            ("rsi", f"RSI{rsi_period}:", rsi_text, rsi_color, self.preview_rule_rsi),
        ):
            key_widget = getattr(self, f"preview_rule_{name}_key", None)
            if key_widget is not None:
                key_widget._viking_full_key_text = key
                key_widget.configure(text=key)
            widget.configure(text=value if key_widget is not None else f"{key} {value}", text_color=color)
            widget._viking_full_value_text = value if key_widget is not None else f"{key} {value}"

        self._render_ema_cross_preview(details)

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
        rule_params = self.settings.rule_parameters or {}
        whipsaw_on = bool(checks.get("whipsaw_enabled", rule_params.get("whipsaw_enabled", False)))
        whipsaw_has_count = checks.get("whipsaw_crossovers") is not None
        crosses = max(0, int(checks.get("whipsaw_crossovers", 0) or 0))
        whipsaw_limit = max(0, int(checks.get("whipsaw_limit", rule_params.get("whipsaw_n", 0)) or 0))
        whipsaw_window = max(0, int(checks.get("whipsaw_window", rule_params.get("whipsaw_x", 0)) or 0))
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
            else "CHỜ DỮ LIỆU" if not whipsaw_has_count or not whipsaw_limit or not whipsaw_window
            else "KHÓA BUY" if whipsaw_locked
            else "OK"
        )
        self._preview_whipsaw_summary = (
            f"WHIPSAW: {crosses if whipsaw_has_count else '--'}/{whipsaw_limit or '--'}"
            f" · {whipsaw_window or '--'} nến 1D · {whipsaw_status}"
        )
        self.preview_rule_phase3_detail.configure(
            text=(
                f"⚠ AUTO 100 · {self._preview_whipsaw_summary} · LỖ: {losses}/{loss_limit or '--'}"
                if forced_minimum
                else f"{self._preview_whipsaw_summary} · LỖ: {losses}/{loss_limit or '--'}"
            ),
            text_color=COL_RED if guard_warn else COL_WARN if forced_minimum else COL_PREVIEW_TEXT,
        )
        fit_label_text(self.preview_rule_phase3_detail, base_font=("Segoe UI", 12), minimum_size=7)
        self.preview_rule_phase3_guard.configure(
            text=(
                f"{self._preview_whipsaw_summary} · LOSS: {losses}/{loss_limit or '--'}"
                f"{' · KHÓA MÃ' if loss_locked else ''}"
            ),
            text_color=COL_RED if guard_warn else COL_PREVIEW_TEXT,
        )
        reason = str(decision.get("reason") or "").upper()
        reason_labels = {
            "": "CHỜ DỮ LIỆU",
            "NO_NEW_BUY_SIGNAL": "CHỜ · CHƯA CÓ BUY",
            "INDICATOR_BUCKET_WAIT": "CHỜ · MẪU CHỈ BÁO PHÚT",
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
            "PORTFOLIO_EXPOSURE_LIMIT": "KHÔNG ĐỦ HẠN MỨC DANH MỤC",
            "PRIORITY_CAPITAL_LIMIT": "KHÔNG ĐỦ HẠN MỨC PRIORITY",
            "DECISION_POLL_UNAVAILABLE": "TẠM CHẶN BUY · ĐANG PHỤC HỒI",
            "ENTRY_LIMIT_SNAPSHOT_UNAVAILABLE": "CHỜ KIỂM TRA VỐN TÀI KHOẢN",
            "FILL_ACCOUNTING_RECONCILE_REQUIRED": "CHỜ ĐỐI SOÁT SỔ VỊ THẾ",
            "BROKER_REJECTED": "BROKER TỪ CHỐI",
            "BROKER_FAILED": "GỬI BROKER THẤT BẠI",
            "MARKET_STATE_UNKNOWN": "CHỜ · STATE CHƯA XÁC NHẬN",
            "WHIPSAW_LOCK": "WHIPSAW · KHÓA BUY",
            "LOCKED_AFTER_3_LOSSES": "KHÓA SAU 3 LỆNH LỖ",
            "LOCKED_AFTER_LOSSES": "KHÓA SAU CHUỖI LỖ",
            "MANUAL_OR_EXTERNAL_POSITION": "VỊ THẾ MANUAL",
            "POSITION_MANAGED_SL_ONLY": "CHỈ THEO DÕI SL",
            "POSITION_BOT_ALERT": "BOT VỊ THẾ ALERT · CHỈ TÍN HIỆU",
            "POSITION_AUTO_SELL_PAUSED": "TỰ BÁN TẠM DỪNG · CHỈ TÍN HIỆU",
            "STOP_LOSS": "KÍCH HOẠT SL",
            "INDICATOR_EXIT": "KÍCH HOẠT EXIT SELL",
            "INDICATOR_EXIT_ALERT": "E ALERT · KHÔNG ĐẶT LỆNH",
            "PRICE_PROTECTION": "KÍCH HOẠT BẢO VỆ GIÁ",
            "PROTECT_WAITING_SETTLEMENT": "PROTECT: CHỜ CỔ VỀ · CHƯA TẠO SELL",
            "PROTECT_POLICY_CHANGED": "PROTECT ALERT · CHƯA TẠO SELL",
            "NORMAL_PROTECTION": "PROTECT", "DYNAMIC_PROTECTION": "PROTECT DYNAMIC",
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
        if reason == "PROTECT_WAITING_SETTLEMENT":
            stage_label = "DYNAMIC" if details.get("protection_stage") == "DYN" else "ARM"
            reason_text = f"{stage_label}: CHỜ CỔ VỀ · CHƯA TẠO SELL"
        elif reason == "PROTECT_ALERT":
            stage_label = "DYNAMIC" if details.get("protection_stage") == "DYN" else "ARM"
            reason_text = f"{stage_label} ALERT · KHÔNG ĐẶT LỆNH"
        if action == "WAIT" and signal == "SELL" and reason not in {
            "INDICATOR_EXIT_ALERT", "PROTECT_WAITING_SETTLEMENT", "PROTECT_ALERT", "PROTECT_POLICY_CHANGED", "NORMAL_ARMED",
            "POSITION_BOT_ALERT", "POSITION_AUTO_SELL_PAUSED",
        }:
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
        from .position_status import preview_position_status
        self._preview_position_status = preview_position_status(self, symbol, status)
        reason_font = ("Segoe UI", 9, "bold")
        if self._preview_position_status:
            availability, execution, protection = self._preview_position_status[0].splitlines()
            urgent = any(word in execution for word in ("SELL", "ĐÃ CHẠM", "TẠM DỪNG", "CÓ TÍN HIỆU"))
            compact = availability.replace("BÁN ĐƯỢC ", "Bán ").replace("CHỜ VỀ ", "Chờ ").replace("CÒN", "Còn")
            reason_text = f"{compact}\n{execution if urgent else protection}"
            reason_color = COL_WARN if urgent or "CHỜ VỀ" in availability else COL_PREVIEW_TEXT
            reason_font = ("Segoe UI", 11)
        self.preview_rule_reason.configure(text=reason_text, text_color=reason_color, font=reason_font)

    def _select_info_tab(self, name: str) -> None:
        if not hasattr(self, "log_tabview"):
            return
        name = str(name or "PREVIEW").removesuffix(" *")
        if getattr(self, "_info_collapsed", False):
            self._set_info_panel_collapsed(False)
        # CTkTabview.set defers hiding other pages by 100ms. Use its native
        # click path so fast clicks cannot leave a previous log over PREVIEW.
        self.log_tabview._segmented_button.set(name)
        self.log_tabview._segmented_button_callback(name)

    def _toggle_info_panel(self) -> None:
        self._set_info_panel_collapsed(not getattr(self, "_info_collapsed", False))

    def _set_info_panel_collapsed(self, collapsed: bool) -> None:
        if not hasattr(self, "info_panel") or not hasattr(self, "log_tabview"):
            return
        self._info_collapsed = bool(collapsed)
        if self._info_collapsed:
            self.log_tabview.grid_remove()
            self.info_panel.configure(height=84)
            self.info_panel.grid_rowconfigure(1, weight=0, minsize=0)
            self.right.grid_rowconfigure(2, weight=0, minsize=84)
            self.info_collapse_button.configure(text="⌄")
        else:
            self.log_tabview.grid()
            self.info_panel.configure(height=360)
            self.info_panel.grid_rowconfigure(1, weight=1, minsize=0)
            self.right.grid_rowconfigure(2, weight=0, minsize=360)
            self.info_collapse_button.configure(text="⌃")

    def _render_exit_sell_preview(
        self,
        signal: str,
        enabled: bool,
        position_quantity: int,
        policy: str = "ALERT",
    ) -> None:
        policy = str(policy or "ALERT").upper()
        if position_quantity > 0 and getattr(self, "_preview_dynamic_start", {}).get("auto_sell_paused"):
            policy = "ALERT"
        price = _number(getattr(self, "_current_tick_price", 0))
        signal = str(signal or "--").upper()
        self._preview_exit_state = {"price": price, "signal": signal, "enabled": enabled,
                                    "quantity": position_quantity, "policy": policy}
        market_price = f"TT: {_display_price(price)}" if price > 0 else "CHỜ GIÁ TT"
        # Enable switches control execution, not read-only preview data.
        if position_quantity <= 0:
            value, detail, color = market_price, "CHƯA CÓ CỔ", COL_PREVIEW_TEXT
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
        if hasattr(self, "info_tab_selector"):
            self.info_tab_selector.set(active)
        if hasattr(self, "preview_tab_selector") and self.preview_tab_selector.master is getattr(self, "info_header", None):
            if active == "PREVIEW":
                self.preview_tab_selector.grid()
            else:
                self.preview_tab_selector.grid_remove()

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
        market_status = str(status.get("market_status", "OFFLINE") or "OFFLINE").upper()
        # Each process has its own request counter. A busy healthy daemon must
        # not conceal an account/API failure in the UI client, or vice versa.
        observed_rest = [row for row in rest_rows if int(row.get("total_requests", 0) or 0) > 0]
        problem_rest = [row for row in rest_rows if bool(row.get("snapshot_errors")) or (
            int(row.get("total_requests", 0) or 0) > 0 and (
                not isinstance(row.get("last_status"), (int, float))
                or not 200 <= row["last_status"] < 300
                or bool(row.get("last_error"))
            )
        )]
        rest_error = bool(problem_rest)
        order_pause = rest_error and all(rest_is_end_of_day_order_pause(row, market_status) for row in problem_rest)

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
        rest_state = "ORDER_PAUSE" if order_pause else "ERROR" if rest_error else "OK" if rest_ok else "WAIT"
        dnse_ok = configured and rest_state not in {"ERROR", "ORDER_PAUSE"}
        dnse_text = "CUỐI NGÀY" if order_pause else "OK" if dnse_ok else "CHỜ" if not configured else "LỖI"
        ws_online = bool(ws.get("connected") and ws.get("authenticated"))
        ws_connecting = bool(ws.get("running")) and not ws_online
        market_active = market_status in {"ATO", "OPEN", "CONTINUOUS", "ATC"}
        self.preview_health_core.configure(
            text=f"DNSE {dnse_text}",
            text_color=COL_WARN if order_pause else COL_GREEN if dnse_ok else COL_RED if configured else COL_WARN,
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
            text={"OK": "API OK", "WAIT": "API CHỜ", "ERROR": "API LỖI", "ORDER_PAUSE": "API CUỐI NGÀY"}[rest_state],
            text_color=COL_WARN if order_pause else COL_GREEN if rest_ok else COL_RED if rest_state == "ERROR" else COL_PREVIEW_TEXT,
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
        context = status.get("cycle_error_context") or {}
        if (order_pause and isinstance(context, dict) and context.get("stage") == "ACCOUNT_SNAPSHOT"
                and isinstance(context.get("snapshot_error"), dict)
                and end_of_day_order_pause(context["snapshot_error"], market_status)):
            current_cycle_error = False
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
                or order_pause
                or (mode == "REAL" and not token_ready)
            )
        )
        health_title = "HEALTH LỖI" if has_error else "HEALTH CẢNH BÁO" if has_warning else "HEALTH OK"
        self.preview_health_title._viking_health_full_text = health_title
        self.preview_health_title.configure(
            text=health_title,
            text_color=COL_RED if has_error else COL_WARN if has_warning else COL_GREEN,
        )
        self._fit_preview_health_title()
        fit_label_text(self.preview_health_token, base_font=("Segoe UI", 10), minimum_size=8)

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
