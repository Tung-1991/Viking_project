from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from ..config import AppSettings
from ..dashboard.windows import PALETTE, SymbolPicker, _HoverHint, _window
from ..rules.business import StaticRuleParameters
from .data import HistoricalDataStore
from .engine import BacktestEngine
from .models import (
    NO_PHASE,
    VALID_PHASES,
    BacktestConfig,
    BacktestResult,
    BacktestScenario,
    BacktestSettings,
)
from .report import common_label, export_run_excel, workbook_name


COL_BG = PALETTE["PANEL"]
COL_SURFACE = PALETTE["SURFACE"]
COL_SURFACE_2 = PALETTE["SURFACE_2"]
COL_BORDER = PALETTE["BORDER"]
COL_TEXT = PALETTE["TEXT"]
COL_MUTED = PALETTE["MUTED"]
COL_DIM = PALETTE["DIM"]
COL_BLUE = PALETTE["BLUE"]
COL_BLUE_HOVER = PALETTE["BLUE_HOVER"]
COL_GREEN = PALETTE["GREEN"]
COL_GREEN_HOVER = PALETTE["GREEN_HOVER"]
COL_SLATE = PALETTE["SLATE"]
COL_SLATE_HOVER = PALETTE["SLATE_HOVER"]
COL_RED = PALETTE["RED"]
COL_WARN = PALETTE["WARN"]

FONT = "Segoe UI"
MONO = "Cascadia Mono"

MODE_1 = "MODE 1"
MODE_2 = "MODE 2"
MODE_SETTING = "THAM SỐ"
NO_PHASE_LABEL = "KHÔNG DÙNG PHASE 1"

PHASE_NAMES = (
    ("UPTREND", "UPTREND · TĂNG"),
    ("ACCUMULATION", "ACCUMULATION · TÍCH LŨY"),
    ("DISTRIBUTION", "DISTRIBUTION · PHÂN PHỐI"),
    ("DOWNTREND", "DOWNTREND · GIẢM"),
)

DEFAULT_SCENARIOS = (
    {"name": "HSG · GIAI ĐOẠN 1", "symbols": ["HSG"], "start_date": "2019-01-02", "end_date": "2020-03-31", "market_phase": "ACCUMULATION"},
    {"name": "HSG · GIAI ĐOẠN 2", "symbols": ["HSG"], "start_date": "2020-04-03", "end_date": "2021-10-15", "market_phase": "UPTREND"},
    {"name": "HSG · GIAI ĐOẠN 3", "symbols": ["HSG"], "start_date": "2021-10-19", "end_date": "2022-11-17", "market_phase": "DOWNTREND"},
    {"name": "HSG · GIAI ĐOẠN 4", "symbols": ["HSG"], "start_date": "2022-11-17", "end_date": "2025-04-25", "market_phase": "ACCUMULATION"},
)

# Rule parameters grouped the way the business rule is written, so a field is
# looked up where the operator already expects it to live.
PHASE_GROUPS = (
    (
        "PHASE 1 · CÁCH ĐỌC VNINDEX",
        "Chỉ ảnh hưởng tới việc xếp thị trường vào trạng thái nào.",
        (
            ("MA DÀI HẠN", "ma_period", "Số phiên của đường trung bình dùng xác nhận bối cảnh dài hạn."),
            ("PIVOT TRÁI", "pivot_left", "Số nến bên trái để xác nhận một đỉnh hoặc đáy."),
            ("PIVOT PHẢI", "pivot_right", "Số nến bên phải để xác nhận một đỉnh hoặc đáy."),
            ("SỐ PHIÊN XÁC NHẬN", "confirm_sessions", "Trạng thái mới phải giữ đủ bấy nhiêu phiên mới được công nhận."),
        ),
    ),
    (
        "PHASE 2 · ĐIỂM MUA BÁN",
        "Mua khi EMA nhanh cắt lên EMA chậm và RSI tăng. Bán khi cắt xuống và RSI giảm.",
        (
            ("EMA MUA NHANH", "buy_ema_fast", "EMA nhanh của tín hiệu mua."),
            ("EMA MUA CHẬM", "buy_ema_slow", "EMA chậm của tín hiệu mua."),
            ("EMA BÁN NHANH", "sell_ema_fast", "EMA nhanh của tín hiệu thoát."),
            ("EMA BÁN CHẬM", "sell_ema_slow", "EMA chậm của tín hiệu thoát."),
            ("RSI", "rsi_period", "Số phiên tính RSI."),
        ),
    ),
    (
        "PHASE 3 · VỐN VÀ THOÁT VỊ THẾ",
        "Vốn mỗi lệnh bằng tài sản nhân tỷ trọng chia cho số mã tối đa.",
        (
            ("CHỐT LỜI %", "take_profit_pct", "Lãi chạm mức này thì bán sạch vị thế. Chỉ chạy khi bật ô TP."),
            ("CẮT LỖ LỆNH ĐẦU %", "initial_sl_pct", "Cắt lỗ cho lệnh đầu mỗi chu kỳ. Nhập số âm."),
            ("CẮT LỖ VÀO LẠI %", "reentry_sl_pct", "Cắt lỗ cho lệnh vào lại sau khi vừa lỗ. Nhập số âm."),
            ("NORMAL · LÃI %", "normal_arm_pct", "Lãi phải từng đạt mức này thì Normal mới bắt đầu theo dõi."),
            ("NORMAL · TỤT GIÁ %", "normal_giveback_pct", "Giá giảm bấy nhiêu phần trăm khỏi giá cao nhất thì Normal bán."),
            ("NORMAL · BÁN %", "normal_sell_pct", "Bán bao nhiêu phần trăm khối lượng đang giữ khi Normal kích hoạt. Mặc định 33. Đặt 100 để bán sạch."),
            ("HIGH · LÃI %", "high_profit_arm_pct", "Lãi phải từng đạt mức này thì High mới bắt đầu theo dõi."),
            ("HIGH · TỤT CLOSE %", "high_profit_close_drawdown_pct", "Giá đóng cửa giảm bấy nhiêu khỏi đỉnh thì High bán."),
            ("HIGH · BÁN %", "high_sell_pct", "Bán bao nhiêu phần trăm khối lượng đang giữ khi High kích hoạt. Mặc định 33. Đặt 100 để bán sạch."),
            ("WHIPSAW · SỐ LẦN CẮT", "whipsaw_n", "EMA cắt qua lại bao nhiêu lần thì khóa mua mã đó."),
            ("WHIPSAW · SỐ PHIÊN ĐẾM", "whipsaw_x", "Đếm số lần cắt trong bấy nhiêu phiên gần nhất."),
        ),
    ),
)

INTEGER_KEYS = {
    "ma_period", "pivot_left", "pivot_right", "confirm_sessions",
    "buy_ema_fast", "buy_ema_slow", "sell_ema_fast", "sell_ema_slow",
    "rsi_period", "max_positions", "whipsaw_n", "whipsaw_x",
}


class BacktestPopup:
    def __init__(
        self,
        parent: ctk.CTk,
        settings: AppSettings,
        client: Any,
        *,
        on_visibility_changed: Callable[[bool], None] | None = None,
    ):
        self.parent = parent
        self.settings = settings
        self.client = client
        self.on_visibility_changed = on_visibility_changed
        self.data = HistoricalDataStore(client)
        self.engine = BacktestEngine(self.data)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viking-backtest")
        self.cancel_event = threading.Event()
        self.running = False
        self._running_mode = MODE_1
        self._last_file = None
        self.show_log = False
        self.config = self._load_settings()
        self.scenarios = self._load_scenarios()
        self._rule_entries: dict[str, ctk.CTkEntry] = {}

        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width, height = min(1180, screen_w - 60), min(800, screen_h - 80)
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 3)
        self.top = _window(parent, "VIKING · BACKTEST", f"{width}x{height}+{x}+{y}")
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.tk.call("wm", "transient", self.top._w, "")
        self.top.resizable(True, True)
        self.top.minsize(980, 640)
        self.top.configure(fg_color=PALETTE["BG"])
        self.top.grid_rowconfigure(0, weight=1)
        self.top.grid_columnconfigure(0, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.hide(), add="+")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Backtest.Treeview", background=COL_SURFACE, foreground=COL_TEXT,
            fieldbackground=COL_SURFACE, rowheight=50, font=(FONT, 18), borderwidth=0,
        )
        style.configure(
            "Backtest.Treeview.Heading", background=COL_SURFACE_2, foreground=COL_TEXT,
            font=(FONT, 15, "bold"), relief="flat", padding=(12, 13),
        )
        style.map(
            "Backtest.Treeview",
            background=[("selected", COL_BLUE)], foreground=[("selected", "#FFFFFF")],
        )

        self.tabs = ctk.CTkTabview(
            self.top, fg_color=COL_BG, border_width=1, border_color=COL_BORDER,
            segmented_button_selected_color=COL_BLUE,
            segmented_button_selected_hover_color=COL_BLUE_HOVER,
            segmented_button_unselected_color=COL_SLATE,
            segmented_button_unselected_hover_color=COL_SLATE_HOVER,
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))
        try:
            self.tabs._segmented_button.configure(font=(FONT, 15, "bold"), text_color=COL_TEXT)
        except AttributeError:
            pass
        tab_one, tab_two, tab_set = (
            self.tabs.add(MODE_1), self.tabs.add(MODE_2), self.tabs.add(MODE_SETTING),
        )
        # Shared fields are built first so the mode tabs can read them.
        self._setting_tab(tab_set)
        self._mode1_tab(tab_one)
        self._mode2_tab(tab_two)
        self._footer()
        self._refresh_capital_hint()
        self.show()

    # ------------------------------------------------------------------ setup

    def _load_settings(self) -> BacktestSettings:
        values = BacktestSettings.from_dict(self.data.load_settings())
        if not values.symbols:
            values.symbols = list(self.settings.watchlist)
        if not values.start_date:
            values.start_date = (date.today() - timedelta(days=365 * 4)).isoformat()
        if not values.end_date:
            values.end_date = date.today().isoformat()
        if not values.rule_parameters:
            values.rule_parameters = StaticRuleParameters.from_dict(self.settings.rule_parameters).to_dict()
        return values

    def _load_scenarios(self) -> list[BacktestScenario]:
        rows = self.data.load_scenarios() or list(DEFAULT_SCENARIOS)
        scenarios: list[BacktestScenario] = []
        for row in rows:
            try:
                scenarios.append(BacktestScenario.from_dict(row))
            except (TypeError, ValueError):
                continue
        return scenarios

    # ----------------------------------------------------------- small pieces

    @staticmethod
    def _entry(parent: Any, value: str = "", width: int = 150) -> ctk.CTkEntry:
        entry = ctk.CTkEntry(
            parent, width=width, height=38, font=(FONT, 15),
            fg_color=COL_SURFACE_2, border_color=COL_BORDER,
        )
        entry.insert(0, value)
        return entry

    @staticmethod
    def _label(parent: Any, text: str, size: int = 15, *, bold: bool = False, color: str = COL_TEXT) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent, text=text, anchor="w", text_color=color,
            font=(FONT, size, "bold") if bold else (FONT, size),
        )

    def _card(self, parent: Any, title: str, subtitle: str = "") -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=COL_SURFACE, corner_radius=8, border_width=1, border_color=COL_BORDER)
        self._label(frame, title, 16, bold=True).grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(13, 2))
        if subtitle:
            ctk.CTkLabel(
                frame, text=subtitle, font=(FONT, 14), text_color=COL_MUTED,
                anchor="w", justify="left", wraplength=980,
            ).grid(row=1, column=0, columnspan=6, sticky="w", padx=16, pady=(0, 8))
        return frame

    @staticmethod
    def _hint(parent: Any, text: str, mark: str = "?", color: str = COL_SLATE) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent, text=mark, width=28, height=28, corner_radius=14,
            font=(FONT, 13, "bold"), fg_color=color, hover_color=COL_BLUE,
        )
        # Keep the hover object reachable so callers can rewrite a live note.
        button.hover = _HoverHint(button, text, placement="below")
        return button

    # ---------------------------------------------------------------- MODE 1

    def _mode1_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(frame, fg_color="transparent", scrollbar_button_color=COL_BORDER)
        body.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        body.grid_columnconfigure(0, weight=1)

        picker_card = self._card(
            body, "MÃ ĐEM CHẠY",
            "Chạy rule trên nhiều mã cùng lúc trong một khoảng thời gian liền mạch.",
        )
        picker_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        picker_card.grid_columnconfigure(0, weight=1)
        self.symbol_picker = SymbolPicker(
            picker_card, self.config.symbols, on_change=lambda _values: self._refresh_capital_hint(),
        )
        self.symbol_picker.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkButton(
            picker_card, text="LẤY TOÀN BỘ WATCHLIST", width=225, height=36,
            font=(FONT, 13, "bold"), fg_color=COL_SLATE, hover_color=COL_BLUE,
            command=lambda: self.symbol_picker.set(list(self.settings.watchlist)),
        ).grid(row=3, column=0, sticky="e", padx=16, pady=(0, 14))

        period = self._card(body, "KHOẢNG THỜI GIAN")
        period.grid(row=1, column=0, sticky="ew")
        period.grid_columnconfigure(4, weight=1)
        self._label(period, "TỪ NGÀY").grid(row=2, column=0, sticky="w", padx=(16, 8), pady=(0, 12))
        self.start_entry = self._entry(period, self.config.start_date, 150)
        self.start_entry.grid(row=2, column=1, sticky="w", pady=(0, 12))
        self._label(period, "ĐẾN NGÀY").grid(row=2, column=2, sticky="w", padx=(24, 8), pady=(0, 12))
        self.end_entry = self._entry(period, self.config.end_date, 150)
        self.end_entry.grid(row=2, column=3, sticky="w", pady=(0, 12))
        self.capital_hint = self._label(period, "", 14, color=COL_MUTED)
        self.capital_hint.grid(row=3, column=0, columnspan=5, sticky="w", padx=16, pady=(0, 14))

        # MODE 1 carries its own switches, exactly like a MODE 2 row, so neither
        # mode has to reach into the other tab to change how it runs.
        guard = self._card(body, "CÔNG TẮC CHO MODE 1")
        guard.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        guard.grid_columnconfigure(9, weight=1)
        # Two short lines instead of one long one: the single row ran past the
        # right edge of the card and cut the WHIPSAW switch off entirely.
        top = ctk.CTkFrame(guard, fg_color="transparent")
        top.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
        self._label(top, "TỐI ĐA SỐ MÃ", 13, bold=True).grid(row=0, column=0, sticky="w")
        self.mode1_slots = self._entry(top, f"{self.config.rule_parameters.get('max_positions', 5)}", 80)
        self.mode1_slots.grid(row=0, column=1, sticky="w", padx=(8, 12))
        self.mode1_slots.bind("<KeyRelease>", lambda _e: self._refresh_capital_hint())
        self.mode1_slots_preview = self._label(top, "", 14, color=COL_WARN)
        self.mode1_slots_preview.grid(row=0, column=2, sticky="w")

        low = ctk.CTkFrame(guard, fg_color="transparent")
        low.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        self._label(low, "BẢO VỆ", 13, bold=True).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.em_normal = ctk.BooleanVar(value="NORMAL" in self.config.em_modes)
        self.em_high = ctk.BooleanVar(value="HIGH" in self.config.em_modes)
        self.em_exit = ctk.BooleanVar(value="IND_EXIT" in self.config.em_modes)
        self.em_tp = ctk.BooleanVar(value="TP" in self.config.em_modes)
        for index, (label, var) in enumerate((
            ("TP", self.em_tp), ("NORMAL", self.em_normal),
            ("HIGH", self.em_high), ("EXIT", self.em_exit),
        )):
            ctk.CTkCheckBox(
                low, text=label, variable=var, font=(FONT, 13, "bold"),
                fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER,
                checkbox_width=22, checkbox_height=22,
            ).grid(row=0, column=1 + index, sticky="w", padx=(0, 14))
        ctk.CTkSwitch(
            low, text="WHIPSAW", variable=self.whipsaw,
            font=(FONT, 13, "bold"), progress_color=COL_GREEN,
        ).grid(row=0, column=5, sticky="w", padx=(12, 0))

        # Which state MODE 1 runs under is a MODE 1 decision, so it lives here.
        # The percentages behind each state are shared with MODE 2 and stay in
        # the THAM SỐ tab.
        phase = self._card(
            body, "TRẠNG THÁI THỊ TRƯỜNG",
            "Chỉ áp dụng cho MODE 1. Phần trăm vốn của từng trạng thái khai báo ở tab THAM SỐ "
            "vì MODE 2 cũng dùng chung.",
        )
        phase.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        phase.grid_columnconfigure(2, weight=1)
        self.auto_phase = ctk.BooleanVar(value=self.config.auto_market_phase)
        switch_row = ctk.CTkFrame(phase, fg_color="transparent")
        switch_row.grid(row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 4))
        ctk.CTkSwitch(
            switch_row, text="TỰ NHẬN DIỆN TỪ VNINDEX", variable=self.auto_phase,
            command=self._sync_phase_controls, font=(FONT, 14, "bold"), progress_color=COL_GREEN,
        ).grid(row=0, column=0, sticky="w")
        self._hint(
            switch_row,
            "Bật: mỗi ngày tự xếp VNINDEX vào một trong bốn trạng thái rồi lấy tỷ trọng tương ứng.\n"
            "Tắt: dùng đúng một trạng thái bạn chọn suốt kỳ.",
        ).grid(row=0, column=1, padx=10)

        self.fixed_block = ctk.CTkFrame(phase, fg_color="transparent")
        self.fixed_block.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 16))
        self._label(self.fixed_block, "DÙNG TRẠNG THÁI", 14).grid(row=0, column=0, sticky="w")
        self.fixed_phase = ctk.CTkOptionMenu(
            self.fixed_block, values=sorted(VALID_PHASES), height=38, font=(FONT, 15),
            fg_color=COL_BLUE, width=230, command=lambda _v: self._refresh_capital_hint(),
        )
        self.fixed_phase.set(self.config.fixed_market_phase)
        self.fixed_phase.grid(row=0, column=1, sticky="w", padx=12)
        self._sync_phase_controls()

    def _refresh_capital_hint(self) -> None:
        if not hasattr(self, "capital_hint") or not hasattr(self, "exposure_entries"):
            return
        try:
            capital = float(self.capital_entry.get().replace(",", "").strip() or 0)
            slots = max(1, int(float(self.mode1_slots.get().strip() or 5)))
            rates = {state: float(entry.get().strip() or 0) for state, entry in self.exposure_entries.items()}
            if self.auto_phase.get():
                low, high = min(rates.values()), max(rates.values())
                budget = f"{capital * low / 100 / slots:,.0f} – {capital * high / 100 / slots:,.0f} đ tùy trạng thái"
            else:
                chosen = self.fixed_phase.get()
                budget = f"{capital * rates.get(chosen, 0.0) / 100 / slots:,.0f} đ"
        except (TypeError, ValueError, KeyError):
            self.capital_hint.configure(text="")
            return
        self.capital_hint.configure(
            text=f"{len(self.symbol_picker.get())} mã · giữ tối đa {slots} mã cùng lúc · "
                 f"mỗi lệnh khoảng {budget}",
        )
        if hasattr(self, "mode1_slots_preview"):
            if self.auto_phase.get():
                low, high = min(rates.values()), max(rates.values())
                self.mode1_slots_preview.configure(
                    text=f"→ mỗi lệnh {capital * low / 100 / slots:,.0f}"
                         f" – {capital * high / 100 / slots:,.0f} đ   (Phase 1 tự đổi ÷ {slots})",
                )
            else:
                chosen = self.fixed_phase.get()
                pct = rates.get(chosen, 0.0)
                self.mode1_slots_preview.configure(
                    text=f"→ mỗi lệnh {capital * pct / 100 / slots:,.0f} đ   "
                         f"({chosen} {pct:g}% ÷ {slots})",
                )
        if hasattr(self, "scenario_hint"):
            try:
                slots = max(1, int(float(self.scenario_slots.get().strip() or 5)))
            except (TypeError, ValueError):
                pass
            top = max(rates, key=rates.get)
            lines = [
                f"Vốn {capital:,.0f} đ chia cho {slots} slot theo ô TỐI ĐA SỐ MÃ của dòng này.",
                "MODE 2 dùng một tài khoản xuyên suốt: dòng sau nhận vốn, vị thế, lệnh chờ "
                "và trạng thái bảo vệ từ dòng trước.",
            ]
            for state in ("UPTREND", "ACCUMULATION", "DISTRIBUTION", "DOWNTREND"):
                lines.append(
                    f"  {state} {rates.get(state, 0):g}%  →  vào lệnh "
                    f"{capital * rates.get(state, 0) / 100 / slots:,.0f} đ"
                )
            if slots > 1:
                lines.append(
                    f"Phần còn lại nằm tiền mặt, nên lãi lỗ hiện ra nhỏ hơn {slots} lần "
                    f"so với sức sinh lời thật của mã đó."
                )
                lines.append("Muốn kịch bản dùng trọn tỷ trọng thì đặt TỐI ĐA SỐ MÃ = 1 ngay trên dòng.")
            else:
                lines.append(f"TỐI ĐA SỐ MÃ = 1 nên kịch bản dùng trọn tỷ trọng, "
                             f"ví dụ {top} là {capital * rates.get(top, 0) / 100:,.0f} đ.")
            self.scenario_hint.hover.text = "\n".join(lines)

    # ---------------------------------------------------------------- MODE 2

    def _mode2_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        # The table keeps the spare height and never shrinks past a
        # readable size, however tall the editor below grows.
        frame.grid_rowconfigure(1, weight=1, minsize=300)
        # The how-to lives behind ! so the table gets the vertical space.
        note = ctk.CTkFrame(frame, fg_color="transparent")
        note.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 6))
        self._hint(
            note,
            "Mỗi dòng là một giai đoạn liên tiếp của cùng một tài khoản.\n"
            "Chọn các dòng cần chạy rồi bấm CHẠY MODE 2.\n"
            "Hệ thống chạy theo thời gian; vốn, vị thế, lệnh chờ, chuỗi LOSS và thời hạn khóa "
            "được chuyển sang dòng kế tiếp. Mỗi dòng vẫn có kết quả riêng trong cùng file Excel.",
            mark="!", color=COL_BLUE,
        ).grid(row=0, column=0)
        self._label(note, "CÁCH DÙNG", 13, bold=True, color=COL_MUTED).grid(row=0, column=1, padx=10)

        holder = ctk.CTkFrame(frame, fg_color="transparent")
        holder.grid(row=1, column=0, sticky="nsew", padx=14)
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        # Text columns read left aligned; fixed widths plus a horizontal bar so
        # the last column is never silently clipped.
        spec = (
            ("name", "TÊN", 260, "w"), ("symbol", "MÃ", 150, "w"),
            ("start", "TỪ", 118, "center"), ("end", "ĐẾN", 118, "center"),
            ("phase", "TRẠNG THÁI", 170, "w"), ("exposure", "TỶ TRỌNG", 115, "center"),
            ("slots", "SỐ MÃ", 90, "center"), ("guard", "BẢO VỆ", 190, "center"),
            ("whipsaw", "WHIPSAW", 110, "center"),
        )
        self.scenario_tree = ttk.Treeview(
            holder, columns=[key for key, *_ in spec], show="headings",
            selectmode="extended", style="Backtest.Treeview",
        )
        for key, heading, width, anchor in spec:
            # stretch spreads any spare width across every column, so the table
            # always fills the frame instead of leaving a dead strip on the right.
            self.scenario_tree.heading(key, text=heading, anchor=anchor)
            self.scenario_tree.column(key, width=width, minwidth=width, anchor=anchor, stretch=True)
        self.scenario_tree.tag_configure("even", background="#22262D", foreground=COL_TEXT)
        self.scenario_tree.tag_configure("odd", background="#1D2127", foreground=COL_TEXT)
        self.scenario_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.scenario_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(holder, orient="horizontal", command=self.scenario_tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.scenario_tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        self.scenario_tree.bind("<<TreeviewSelect>>", self._load_selected_scenario)
        self._refresh_scenarios()
        self._refresh_selection_preview()

        edit = ctk.CTkFrame(frame, fg_color=COL_SURFACE, corner_radius=8, border_width=1, border_color=COL_BORDER)
        edit.grid(row=3, column=0, sticky="ew", padx=14, pady=10)
        for col in range(5):
            edit.grid_columnconfigure(col, weight=1)
        self.scenario_name = self._entry(edit, "Giai đoạn mới")
        self.scenario_start = self._entry(edit, "2020-01-01", 150)
        today = date.today()
        self.scenario_end = ctk.CTkComboBox(
            edit, values=[today.isoformat(), date(today.year - 1, 12, 31).isoformat()],
            height=38, font=(FONT, 15), fg_color=COL_SURFACE_2, border_color=COL_BORDER,
            button_color=COL_BLUE, button_hover_color=COL_BLUE_HOVER, dropdown_font=(FONT, 14),
            command=lambda _v: self._refresh_scenario_preview(),
        )
        self.scenario_end.set(today.isoformat())
        self.scenario_phase = ctk.CTkOptionMenu(
            edit, values=[*sorted(VALID_PHASES), NO_PHASE_LABEL], height=38,
            font=(FONT, 15), fg_color=COL_BLUE, command=lambda _v: self._sync_scenario_phase(),
        )
        self.scenario_exposure = self._entry(edit, "60", 90)
        self.scenario_exposure.bind("<KeyRelease>", lambda _e: self._refresh_scenario_preview())
        for col, (label, widget) in enumerate(zip(
            ("TÊN", "TỪ", "ĐẾN", "TRẠNG THÁI", "TỶ TRỌNG %"),
            (self.scenario_name, self.scenario_start, self.scenario_end,
             self.scenario_phase, self.scenario_exposure),
        )):
            self._label(edit, label, 13, bold=True).grid(
                row=0, column=col, sticky="w", padx=(16 if col == 0 else 6, 6), pady=(12, 3),
            )
            widget.grid(row=1, column=col, sticky="ew", padx=(14 if col == 0 else 5, 5), pady=(0, 8))
        self.scenario_exposure_note = self._hint(edit, "", mark="?")
        self.scenario_exposure_note.grid(row=1, column=5, sticky="w", padx=(4, 12), pady=(0, 8))

        # Switches belong to the row, not to the shared settings, so two rows can
        # test two configurations over the same window.
        guards = ctk.CTkFrame(edit, fg_color="transparent")
        guards.grid(row=2, column=0, columnspan=6, sticky="ew", padx=16, pady=(2, 6))
        self._label(guards, "TỐI ĐA SỐ MÃ", 13, bold=True).grid(row=0, column=0, sticky="w")
        self.scenario_slots = self._entry(guards, "5", 80)
        self.scenario_slots.grid(row=0, column=1, sticky="w", padx=(8, 10))
        self.scenario_slots.bind("<KeyRelease>", lambda _e: self._refresh_scenario_preview())
        self.scenario_slots_preview = self._label(guards, "", 14, color=COL_WARN)
        self.scenario_slots_preview.grid(row=0, column=2, columnspan=8, sticky="w", padx=(0, 26))
        # Hàng riêng cho công tắc: nhét chung một hàng thì dòng xem trước tiền
        # đẩy nút WHIPSAW ra ngoài mép thẻ.
        self._label(guards, "BẢO VỆ", 13, bold=True).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.scenario_em = {}
        for index, (key, label) in enumerate((
            ("TP", "TP"), ("NORMAL", "NORMAL"), ("HIGH", "HIGH"), ("IND_EXIT", "EXIT"),
        )):
            var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(
                guards, text=label, variable=var, font=(FONT, 13, "bold"),
                fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER, checkbox_width=22, checkbox_height=22,
            ).grid(row=1, column=1 + index, sticky="w", padx=(0, 12), pady=(6, 0))
            self.scenario_em[key] = var
        self.scenario_whipsaw = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            guards, text="WHIPSAW", variable=self.scenario_whipsaw,
            font=(FONT, 13, "bold"), progress_color=COL_GREEN,
        ).grid(row=0, column=8, sticky="w", padx=(18, 0))
        self._hint(
            guards,
            "Ba ô này và số mã tối đa thuộc riêng dòng này.\nHai dòng cùng giai đoạn nhưng khác cấu hình sẽ cho hai kết quả để so.\nCác ngưỡng số như 7%, 3%, 20%, 5% vẫn lấy chung từ tab THAM SỐ.",
        ).grid(row=0, column=9, padx=14)

        # A scenario may hold several symbols; capital is still split by the
        # TỐI ĐA SỐ MÃ slot count, exactly like MODE 1.
        self.scenario_symbols = SymbolPicker(
            edit, [], columns=8, compact=True,
            placeholder="MÃ TRONG KỊCH BẢN — ví dụ HSG, HPG",
        )
        self.scenario_symbols.grid(row=3, column=0, columnspan=6, sticky="ew", padx=16, pady=(0, 10))
        self._sync_scenario_phase()

        # Says out loud what CHẠY MODE 2 is about to do and what file it writes.
        self.selection_preview = self._label(frame, "", 14, bold=True, color=COL_WARN)
        self.selection_preview.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 6))
        self.selection_file = self.selection_preview

        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._refresh_selection_preview()
        actions.grid_columnconfigure(1, weight=1)
        # A one-symbol scenario only fills one slot, so the run deploys a
        # fraction of the exposure.  The note behind ! carries the live number.
        self.scenario_hint = self._hint(actions, "", mark="!", color=COL_WARN)
        self.scenario_hint.grid(row=0, column=0, sticky="w")
        self._label(actions, "VỐN KỊCH BẢN THỰC SỰ DÙNG", 13, bold=True, color=COL_WARN).grid(
            row=0, column=1, sticky="w", padx=10,
        )
        ctk.CTkButton(
            actions, text="LƯU DÒNG", width=140, height=38, font=(FONT, 14, "bold"),
            fg_color=COL_SLATE, hover_color=COL_BLUE, command=self._save_scenario,
        ).grid(row=0, column=2, padx=5)
        ctk.CTkButton(
            actions, text="XÓA DÒNG", width=140, height=38, font=(FONT, 14, "bold"),
            fg_color=COL_SLATE, hover_color=COL_RED, command=self._delete_scenario,
        ).grid(row=0, column=3, padx=5)

    def _refresh_scenarios(self) -> None:
        for item in self.scenario_tree.get_children():
            self.scenario_tree.delete(item)
        for index, row in enumerate(self.scenarios):
            self.scenario_tree.insert(
                "", "end", iid=row.id,
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    row.name, ", ".join(row.symbols), row.start_date, row.end_date,
                    row.market_phase if row.uses_market_phase else "KHÔNG DÙNG PHASE 1",
                    "theo bảng" if row.uses_market_phase else f"{row.exposure_pct:g}%",
                    row.max_positions,
                    "+".join(m.replace("IND_EXIT", "EXIT") for m in row.em_modes) or "không",
                    "BẬT" if row.whipsaw_enabled else "TẮT",
                ),
            )

    def _selected_scenarios(self) -> list[BacktestScenario]:
        chosen = set(self.scenario_tree.selection())
        return [row for row in self.scenarios if row.id in chosen]

    def _refresh_selection_preview(self) -> None:
        """Name the rows that will run and the file they will land in."""
        if not hasattr(self, "selection_preview"):
            return
        rows = self._selected_scenarios()
        if not rows:
            self.selection_preview.configure(
                text="CHƯA CHỌN DÒNG NÀO — bấm một dòng ở bảng trên", text_color=COL_WARN,
            )
            return
        names = ", ".join(row.name for row in rows)
        target = workbook_name(
            MODE_2,
            symbols=[s for row in rows for s in row.symbols],
            start=min(row.start_date for row in rows),
            end=max(row.end_date for row in rows),
            runs=len(rows), stamp="ngay gio chay",
            label=common_label([row.name for row in rows]),
        )
        self.selection_preview.configure(
            text=f"CHẠY MODE 2 sẽ chạy {len(rows)} kịch bản: {names}   →   1 file: {target}",
            text_color=COL_GREEN,
        )

    def _refresh_scenario_preview(self) -> None:
        """Show, next to the slot box, what one order is actually worth."""
        if not hasattr(self, "scenario_slots_preview"):
            return
        try:
            capital = float(self.capital_entry.get().replace(",", "").strip() or 0)
            slots = max(1, int(float(self.scenario_slots.get().strip() or 1)))
            phase = self.scenario_phase.get()
            if phase == NO_PHASE_LABEL:
                pct = float(self.scenario_exposure.get().strip() or 0)
                source = "tỷ trọng tự nhập"
            else:
                pct = float(self.exposure_entries[phase].get().strip() or 0)
                source = phase
        except (TypeError, ValueError, KeyError):
            self.scenario_slots_preview.configure(text="")
            return
        self.scenario_slots_preview.configure(
            text=f"→ mỗi lệnh {capital * pct / 100 / slots:,.0f} đ   "
                 f"({source} {pct:g}% ÷ {slots})",
        )

    def _sync_scenario_phase(self) -> None:
        """Only a row that opted out of Phase 1 types its own exposure."""
        off = self.scenario_phase.get() == NO_PHASE_LABEL
        self.scenario_exposure.configure(state="normal" if off else "disabled")
        self.scenario_exposure_note.hover.text = (
            "Không dùng Phase 1: dòng này giữ nguyên tỷ trọng bạn điền suốt giai đoạn."
            if off else
            "Tỷ trọng lấy từ bảng bốn trạng thái ở tab THAM SỐ, không nhập ở đây."
        )
        self._refresh_scenario_preview()

    def _load_selected_scenario(self, _event: Any = None) -> None:
        self._refresh_selection_preview()
        rows = self._selected_scenarios()
        if len(rows) != 1:
            return
        row = rows[0]
        for entry, value in ((self.scenario_name, row.name), (self.scenario_start, row.start_date)):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.scenario_end.set(row.end_date)
        self.scenario_symbols.set(list(row.symbols))
        self.scenario_slots.delete(0, "end")
        self.scenario_slots.insert(0, str(row.max_positions))
        for key, var in self.scenario_em.items():
            var.set(key in row.em_modes)
        self.scenario_whipsaw.set(row.whipsaw_enabled)
        self.scenario_phase.set(row.market_phase if row.uses_market_phase else NO_PHASE_LABEL)
        self.scenario_exposure.configure(state="normal")
        self.scenario_exposure.delete(0, "end")
        self.scenario_exposure.insert(0, f"{row.exposure_pct:g}")
        self._sync_scenario_phase()

    def _save_scenario(self) -> None:
        rows = self._selected_scenarios()
        try:
            phase = self.scenario_phase.get()
            row = BacktestScenario(
                id=rows[0].id if len(rows) == 1 else "",
                name=self.scenario_name.get(), symbols=self.scenario_symbols.get(),
                start_date=self.scenario_start.get(), end_date=self.scenario_end.get(),
                market_phase=NO_PHASE if phase == NO_PHASE_LABEL else phase,
                exposure_pct=float(self.scenario_exposure.get() or 0),
                max_positions=int(float(self.scenario_slots.get() or 5)),
                em_modes=[key for key, var in self.scenario_em.items() if var.get()],
                whipsaw_enabled=bool(self.scenario_whipsaw.get()),
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Kịch bản", str(exc), parent=self.top)
            return
        # Editing keeps the row where it was; only a brand new row is appended.
        index = next((i for i, item in enumerate(self.scenarios) if item.id == row.id), None)
        if index is None:
            self.scenarios.append(row)
        else:
            self.scenarios[index] = row
        self.data.save_scenarios([item.to_dict() for item in self.scenarios])
        self._refresh_scenarios()
        self._refresh_selection_preview()

    def _delete_scenario(self) -> None:
        rows = self._selected_scenarios()
        if not rows:
            messagebox.showinfo("Kịch bản", "Chọn dòng muốn xóa.", parent=self.top)
            return
        remove = {row.id for row in rows}
        self.scenarios = [item for item in self.scenarios if item.id not in remove]
        self.data.save_scenarios([item.to_dict() for item in self.scenarios])
        self._refresh_scenarios()
        self._refresh_selection_preview()

    # -------------------------------------------------------------- THAM SỐ

    def _setting_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(frame, fg_color="transparent", scrollbar_button_color=COL_BORDER)
        body.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        body.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=6, pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Cả MODE 1 và MODE 2 đều dùng những tham số ở đây. "
                 "Backtest có file riêng nên sửa ở đây không đụng tới bot đang chạy.",
            font=(FONT, 14), text_color=COL_MUTED, anchor="w", justify="left", wraplength=700,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header, text="ĐỒNG BỘ TỪ BOT", width=185, height=38, font=(FONT, 13, "bold"),
            fg_color=COL_BLUE, hover_color=COL_BLUE_HOVER, command=self._sync_from_bot,
        ).grid(row=0, column=1, padx=4)

        money = self._card(
            body, "VỐN",
            "Vốn khởi đầu của MODE 1. Với MODE 2, số vốn này chỉ áp dụng cho dòng đầu; "
            "các dòng sau tiếp tục đúng tài khoản của dòng trước.",
        )
        money.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        money.grid_columnconfigure(2, weight=1)
        self._label(money, "VỐN (ĐỒNG)", 14).grid(row=2, column=0, sticky="w", padx=(16, 10), pady=(0, 16))
        self.capital_entry = self._entry(money, f"{int(self.config.initial_capital)}", 230)
        self.capital_entry.grid(row=2, column=1, sticky="w", pady=(0, 16))
        self.capital_entry.bind("<KeyRelease>", lambda _e: self._refresh_capital_hint())

        self._market_card(body, row_index=2)
        row_index = 3
        for title, subtitle, fields in PHASE_GROUPS:
            card = self._card(body, title, subtitle)
            card.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
            for col in range(4):
                card.grid_columnconfigure(col, weight=1)
            for index, (label, key, hint) in enumerate(fields):
                grid_row, grid_col = divmod(index, 2)
                cell = ctk.CTkFrame(card, fg_color="transparent")
                cell.grid(row=2 + grid_row, column=grid_col * 2, columnspan=2, sticky="ew", padx=(16, 12), pady=4)
                cell.grid_columnconfigure(1, weight=1)
                self._label(cell, label, 14).grid(row=0, column=0, sticky="w")
                entry = self._entry(cell, f"{self.config.rule_parameters.get(key, '')}", 95)
                entry.grid(row=0, column=1, sticky="e", padx=8)
                self._hint(cell, hint).grid(row=0, column=2, sticky="e")
                self._rule_entries[key] = entry
            ctk.CTkLabel(card, text="", height=6).grid(row=2 + (len(fields) + 1) // 2, column=0)
            row_index += 1

        self._cost_card(body, row_index)
        self._switch_card(body, row_index + 1)

    def _market_card(self, body: Any, *, row_index: int = 1) -> None:
        card = self._card(
            body, "TỶ TRỌNG VỐN THEO TRẠNG THÁI THỊ TRƯỜNG",
            "Được dùng bao nhiêu phần trăm tài sản để mua cổ phiếu ứng với mỗi trạng thái. "
            "MODE 1 lấy theo trạng thái chọn ở tab của nó, MODE 2 lấy theo trạng thái ghi trên từng dòng kịch bản.",
        )
        card.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(3, weight=1)

        self.exposure_entries: dict[str, ctk.CTkEntry] = {}
        saved = self.config.rule_parameters.get("exposure") or {}
        for index, (state, caption) in enumerate(PHASE_NAMES):
            self._label(card, caption, 14).grid(
                row=2 + index // 2, column=(index % 2) * 2, sticky="w", padx=(16, 8), pady=6,
            )
            entry = self._entry(card, f"{float(saved.get(state, 0.0)) * 100:g}", 85)
            entry.grid(row=2 + index // 2, column=(index % 2) * 2 + 1, sticky="w", padx=(0, 30), pady=6)
            entry.bind("<KeyRelease>", lambda _e: self._refresh_capital_hint())
            self.exposure_entries[state] = entry
        ctk.CTkLabel(card, text="", height=6).grid(row=4, column=0)

    def _cost_card(self, body: Any, row_index: int) -> None:
        card = self._card(
            body, "PHÍ VÀ THUẾ",
            "Mỗi vòng mua bán mất phí mua cộng phí bán cộng thuế bán. "
            "Thuế 0,1% là luật, phí thì tùy công ty chứng khoán.",
        )
        card.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(3, weight=1)
        self.cost_entries: dict[str, ctk.CTkEntry] = {}
        fields = (
            ("PHÍ MUA %", "buy_fee_pct", self.config.buy_fee_pct,
             "Phần trăm trên giá trị lệnh mua."),
            ("PHÍ BÁN %", "sell_fee_pct", self.config.sell_fee_pct,
             "Phần trăm trên giá trị lệnh bán."),
            ("THUẾ BÁN %", "sell_tax_pct", self.config.sell_tax_pct,
             "Thuế thu nhập cá nhân khi bán, luật quy định 0,1%."),
        )
        for index, (label, key, value, hint) in enumerate(fields):
            cell = ctk.CTkFrame(card, fg_color="transparent")
            cell.grid(row=2, column=index, sticky="w", padx=(16 if index == 0 else 24, 0), pady=(0, 6))
            self._label(cell, label, 14).grid(row=0, column=0, sticky="w", padx=(0, 8))
            entry = self._entry(cell, f"{value:g}", 85)
            entry.grid(row=0, column=1, sticky="w")
            entry.bind("<KeyRelease>", lambda _e: self._refresh_cost_hint())
            self._hint(cell, hint).grid(row=0, column=2, padx=8)
            self.cost_entries[key] = entry
        ctk.CTkButton(
            card, text="LẤY PHÍ TỪ DNSE", width=185, height=36, font=(FONT, 13, "bold"),
            fg_color=COL_SLATE, hover_color=COL_BLUE, command=self._fetch_fee_rates,
        ).grid(row=2, column=3, sticky="e", padx=16, pady=(0, 6))
        self.cost_hint = self._label(card, "", 14, color=COL_MUTED)
        self.cost_hint.grid(row=3, column=0, columnspan=4, sticky="w", padx=16, pady=(0, 14))
        self._refresh_cost_hint()

    def _refresh_cost_hint(self) -> None:
        try:
            rates = {k: float(e.get().strip() or 0) for k, e in self.cost_entries.items()}
        except (TypeError, ValueError):
            self.cost_hint.configure(text="")
            return
        total = sum(rates.values())
        self.cost_hint.configure(
            text=f"Một vòng mua bán mất {total:g}%. "
                 f"Ví dụ lệnh 100 triệu mất {100_000_000 * total / 100:,.0f} đ.",
        )

    def _fetch_fee_rates(self) -> None:
        """Ask DNSE for this account's real fee, falling back to a clear note."""
        symbols = self.symbol_picker.get()
        getter = getattr(self.client, "get_stock_fee_rate", None)
        if not symbols or not callable(getter):
            self._say("Chưa kết nối DNSE hoặc chưa chọn mã, không lấy được biểu phí.", "warn")
            return
        try:
            buy = getter(symbols[0], side="BUY")
            sell = getter(symbols[0], side="SELL")
        except Exception as exc:
            self._say(f"DNSE không trả về biểu phí: {exc}", "error")
            return
        if buy is None and sell is None:
            self._say(f"DNSE chưa có biểu phí cho {symbols[0]}, giữ nguyên số đang điền", "warn")
            return
        for key, value in (("buy_fee_pct", buy), ("sell_fee_pct", sell)):
            if value is None:
                continue
            entry = self.cost_entries[key]
            entry.delete(0, "end")
            entry.insert(0, f"{float(value) * 100:g}")
        self._refresh_cost_hint()
        self._say(f"Đã lấy biểu phí DNSE theo mã {symbols[0]}", "ok")

    def _switch_card(self, body: Any, row_index: int) -> None:
        card = self._card(
            body, "CÔNG TẮC DÙNG CHUNG",
            "Hai mục này áp dụng cho cả hai mode. Bảo vệ vị thế và whipsaw nằm ở tab của từng mode.",
        )
        card.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(3, weight=1)

        self.loss_lock = ctk.BooleanVar(value=self.config.loss_lock_enabled)
        self.whipsaw = ctk.BooleanVar(value=self.config.whipsaw_enabled)
        lock_row = ctk.CTkFrame(card, fg_color="transparent")
        lock_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=16, pady=4)
        ctk.CTkSwitch(
            lock_row, text="KHÓA MÃ SAU 3 LỆNH LỖ LIÊN TIẾP", variable=self.loss_lock,
            font=(FONT, 14, "bold"), progress_color=COL_GREEN,
        ).grid(row=0, column=0, sticky="w")
        self._hint(
            lock_row,
            "Một mã thua ba lệnh liên tiếp thì ngừng mua mã đó.\n"
            "Bot thật có người gỡ khóa, backtest thì không, nên phải tự mở lại sau số giờ bên cạnh.",
        ).grid(row=0, column=1, padx=10)
        self._label(lock_row, "MỞ KHÓA SAU (GIỜ)", 14).grid(row=0, column=2, sticky="w", padx=(28, 10))
        self.cooldown_entry = self._entry(lock_row, str(self.config.loss_lock_hours), 90)
        self.cooldown_entry.grid(row=0, column=3, sticky="w")

        fill_row = ctk.CTkFrame(card, fg_color="transparent")
        fill_row.grid(row=4, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self._label(fill_row, "ĐỢT ATO", 14).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.fill_session = ctk.CTkOptionMenu(
            fill_row, values=["CHO PHÉP · khớp giá mở cửa", "KHÔNG · khớp sau 9h15"],
            height=38, font=(FONT, 14), fg_color=COL_BLUE, width=265,
        )
        self.fill_session.set(
            "KHÔNG · khớp sau 9h15" if self.config.fill_session == "CONTINUOUS"
            else "CHO PHÉP · khớp giá mở cửa"
        )
        self.fill_session.grid(row=0, column=1, sticky="w")
        self._hint(
            fill_row,
            "Đối ứng với công tắc CHO PHÉP ATO của bot thật, ở popup RULE tab THỰC THI.\n"
            "CHO PHÉP: lệnh vào đợt ATO 9h00-9h15, khớp ở giá mở cửa nến ngày.\n"
            "KHÔNG: lệnh chỉ vào sau 9h15, khớp ở nến 15 phút đầu của khớp liên tục.\n"
            "DNSE chỉ cấp nến 15 phút cho ba tháng gần nhất; ngoài khoảng đó vẫn dùng giá mở cửa"
            " và lần chạy sẽ kèm cảnh báo.",
        ).grid(row=0, column=2, padx=10)

        sell_row = ctk.CTkFrame(card, fg_color="transparent")
        sell_row.grid(row=5, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self._label(sell_row, "BÁN KHI CỔ VỀ", 14).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.sell_wait = ctk.CTkOptionMenu(
            sell_row, values=["KIỂM TRA LẠI ĐIỀU KIỆN", "BÁN THEO YÊU CẦU CŨ"],
            height=38, font=(FONT, 14), fg_color=COL_BLUE, width=265,
        )
        self.sell_wait.set("BÁN THEO YÊU CẦU CŨ" if self.config.sell_wait_policy == "KEEP" else "KIỂM TRA LẠI ĐIỀU KIỆN")
        self.sell_wait.grid(row=0, column=1, sticky="w")
        self._hint(
            sell_row,
            "Cổ phiếu mua hôm nay phải hai phiên sau mới bán được.\n"
            "KIỂM TRA LẠI: tới lúc bán được thì xem điều kiện thoát còn đúng không, hết đúng thì thôi.\n"
            "BÁN THEO YÊU CẦU CŨ: đã ra lệnh bán thì cổ về bao nhiêu bán bấy nhiêu.",
        ).grid(row=0, column=2, padx=10)


    def _sync_phase_controls(self) -> None:
        if self.auto_phase.get():
            self.fixed_block.grid_remove()
        else:
            self.fixed_block.grid()
        self._refresh_capital_hint()

    def _sync_from_bot(self) -> None:
        params = StaticRuleParameters.from_dict(self.settings.rule_parameters).to_dict()
        exposure = params.get("exposure") or {}
        for key, entry in self._rule_entries.items():
            entry.delete(0, "end")
            entry.insert(0, f"{params.get(key, '')}")
        for state, entry in self.exposure_entries.items():
            entry.delete(0, "end")
            entry.insert(0, f"{float(exposure.get(state, 0.0)) * 100:g}")
        self.whipsaw.set(bool(params.get("whipsaw_enabled", False)))
        self.em_normal.set("NORMAL" in self.settings.bot_em_modes)
        self.em_high.set("HIGH" in self.settings.bot_em_modes)
        self.em_exit.set("IND_EXIT" in self.settings.bot_em_modes)
        self.em_tp.set("TP" in self.settings.bot_em_modes)
        self.mode1_slots.delete(0, "end")
        self.mode1_slots.insert(0, f"{params.get('max_positions', 5)}")
        self.sell_wait.set(
            "BÁN THEO YÊU CẦU CŨ" if self.settings.sell_wait_policy == "KEEP" else "KIỂM TRA LẠI ĐIỀU KIỆN"
        )
        self.fill_session.set("CHO PHÉP · khớp giá mở cửa" if self.settings.allow_ato
                              else "KHÔNG · khớp sau 9h15")
        self._refresh_capital_hint()
        self._say("Đã lấy tham số từ bot đang chạy", "ok")

    # ------------------------------------------------------------- TỔNG QUAN









    # ---------------------------------------------------------------- footer

    def _footer(self) -> None:
        bar = ctk.CTkFrame(self.top, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(6, 12))
        bar.grid_columnconfigure(0, weight=1)
        panel = ctk.CTkFrame(
            bar, fg_color=COL_SURFACE, corner_radius=8,
            border_width=1, border_color=COL_BORDER,
        )
        panel.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        panel.grid_columnconfigure(1, weight=1)
        self.status_dot = self._label(panel, "●", 16, color=COL_DIM)
        self.status_dot.grid(row=0, column=0, sticky="w", padx=(14, 8), pady=10)
        self.status = self._label(panel, "Sẵn sàng", 14, color=COL_MUTED)
        self.status.grid(row=0, column=1, sticky="w", pady=10)
        self.progress_bar = ctk.CTkProgressBar(bar, width=200, height=10, progress_color=COL_GREEN)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=1, padx=12)
        self.progress_bar.grid_remove()
        self.open_button = ctk.CTkButton(
            bar, text="MỞ THƯ MỤC", width=160, height=40, font=(FONT, 14, "bold"),
            fg_color=COL_SLATE, hover_color=COL_BLUE, command=self._open_output,
        )
        self.open_button.grid(row=0, column=2, padx=5)
        self.cancel_button = ctk.CTkButton(
            bar, text="DỪNG", width=95, height=40, font=(FONT, 14, "bold"),
            fg_color=COL_SLATE, hover_color=COL_RED, state="disabled", command=self.cancel,
        )
        self.cancel_button.grid(row=0, column=3, padx=5)
        self.download_button = ctk.CTkButton(
            bar, text="TẢI DỮ LIỆU", width=155, height=40, font=(FONT, 14, "bold"),
            fg_color=COL_BLUE, hover_color=COL_BLUE_HOVER, command=self.download_data,
        )
        self.download_button.grid(row=0, column=4, padx=5)
        self.run_mode1 = ctk.CTkButton(
            bar, text="CHẠY MODE 1", width=175, height=40, font=(FONT, 15, "bold"),
            fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER, command=self.run_mode_1,
        )
        self.run_mode1.grid(row=0, column=5, padx=5)
        self.run_mode2 = ctk.CTkButton(
            bar, text="CHẠY MODE 2", width=175, height=40, font=(FONT, 15, "bold"),
            fg_color=COL_BLUE, hover_color=COL_BLUE_HOVER, command=self.run_mode_2,
        )
        self.run_mode2.grid(row=0, column=6, padx=5)

    # ------------------------------------------------------------ read state

    def _collect(self) -> BacktestSettings:
        """Read every widget into the backtest's own settings and persist it."""
        params = dict(self.config.rule_parameters)
        exposure = dict(params.get("exposure") or {})
        for key, entry in self._rule_entries.items():
            raw = entry.get().strip()
            if not raw:
                continue
            try:
                params[key] = int(raw) if key in INTEGER_KEYS else float(raw)
            except ValueError as exc:
                raise ValueError(f"Giá trị không hợp lệ ở ô {key}: {raw!r}") from exc
        for state, entry in self.exposure_entries.items():
            raw = entry.get().strip()
            if not raw:
                continue
            try:
                exposure[state] = float(raw) / 100.0
            except ValueError as exc:
                raise ValueError(f"Tỷ trọng {state} không hợp lệ: {raw!r}") from exc
        params["exposure"] = exposure
        params["whipsaw_enabled"] = bool(self.whipsaw.get())
        try:
            params["max_positions"] = max(1, int(float(self.mode1_slots.get().strip() or 5)))
        except ValueError as exc:
            raise ValueError(f"TỐI ĐA SỐ MÃ không hợp lệ: {self.mode1_slots.get()!r}") from exc
        chosen = self.fixed_phase.get()
        values = BacktestSettings(
            symbols=self.symbol_picker.get(),
            start_date=self.start_entry.get().strip(),
            end_date=self.end_entry.get().strip(),
            initial_capital=float(self.capital_entry.get().replace(",", "").strip() or 0),
            auto_market_phase=bool(self.auto_phase.get()),
            fixed_market_phase=chosen,
            fixed_exposure_pct=float(exposure.get(chosen, 0.0)) * 100.0,
            loss_lock_enabled=bool(self.loss_lock.get()),
            loss_lock_hours=int(float(self.cooldown_entry.get().strip() or 0)),
            whipsaw_enabled=bool(self.whipsaw.get()),
            em_modes=[
                name for name, variable in (
                    ("TP", self.em_tp), ("NORMAL", self.em_normal),
                    ("HIGH", self.em_high), ("IND_EXIT", self.em_exit),
                ) if variable.get()
            ],
            sell_wait_policy="KEEP" if self.sell_wait.get().startswith("BÁN") else "RECHECK",
            fill_session="CONTINUOUS" if self.fill_session.get().startswith("KHÔNG") else "ATO",
            buy_fee_pct=float(self.cost_entries["buy_fee_pct"].get().strip() or 0),
            sell_fee_pct=float(self.cost_entries["sell_fee_pct"].get().strip() or 0),
            sell_tax_pct=float(self.cost_entries["sell_tax_pct"].get().strip() or 0),
            rule_parameters=params,
        )
        self.config = values
        self.data.save_settings(values.to_dict())
        return values

    # ---------------------------------------------------------------- running

    def run_mode_1(self) -> None:
        try:
            values = self._collect()
            settings = BacktestConfig(
                symbols=values.symbols, start_date=values.start_date, end_date=values.end_date,
                initial_capital=values.initial_capital,
                auto_market_phase=values.auto_market_phase,
                fixed_market_phase=values.fixed_market_phase,
                fixed_exposure_pct=values.fixed_exposure_pct,
                loss_lock_enabled=values.loss_lock_enabled, loss_lock_hours=values.loss_lock_hours,
                whipsaw_enabled=values.whipsaw_enabled, em_modes=values.em_modes,
                sell_wait_policy=values.sell_wait_policy, fill_session=values.fill_session,
                rule_parameters=values.rule_parameters,
                buy_fee_rate=values.buy_fee_pct / 100.0,
                sell_fee_rate=values.sell_fee_pct / 100.0,
                sell_tax_rate=values.sell_tax_pct / 100.0,
                # Name carries the exit stack so eight runs land in eight
                # files instead of overwriting each other.
                run_name="MODE 1 · " + ("+".join(
                    m.replace("IND_EXIT", "E").replace("NORMAL", "N").replace("HIGH", "H")
                    for m in values.em_modes) or "SL"),
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Backtest", str(exc), parent=self.top)
            return
        self._start(MODE_1, [lambda: self.engine.run(
            settings, progress=self._progress, cancelled=self.cancel_event.is_set,
        )])

    def run_mode_2(self) -> None:
        rows = self._selected_scenarios()
        if not rows:
            self.tabs.set(MODE_2)
            messagebox.showinfo("MODE 2", "Chọn dòng muốn chạy ở tab MODE 2.", parent=self.top)
            return
        try:
            values = self._collect()
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Backtest", str(exc), parent=self.top)
            return

        # Picking several rows means one account walking through them in order:
        # what the previous period ended with is what the next one starts with.
        rows = sorted(rows, key=lambda row: (row.start_date, row.end_date))
        account: dict[str, Any] = {}
        carry = {"capital": values.initial_capital}

        def job(scenario: BacktestScenario) -> Callable[[], BacktestResult]:
            def run() -> BacktestResult:
                result = self.engine.run_scenario(
                    scenario, initial_capital=carry["capital"], carry=account,
                    rule_parameters=values.rule_parameters,
                    loss_lock_enabled=values.loss_lock_enabled,
                    loss_lock_hours=values.loss_lock_hours,
                    sell_wait_policy=values.sell_wait_policy,
                    fill_session=values.fill_session,
                    buy_fee_rate=values.buy_fee_pct / 100.0,
                    sell_fee_rate=values.sell_fee_pct / 100.0,
                    sell_tax_rate=values.sell_tax_pct / 100.0,
                    progress=self._progress, cancelled=self.cancel_event.is_set,
                )
                carry["capital"] = result.final_equity
                return result

            return run

        self._start(MODE_2, [job(row) for row in rows])

    def _start(self, mode: str, jobs: list[Callable[[], BacktestResult]]) -> None:
        if self.running or not jobs:
            return
        self._running_mode = mode
        self.running = True
        self.cancel_event.clear()
        for button in (self.download_button, self.run_mode1, self.run_mode2):
            button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_bar.set(0)
        self.progress_bar.grid()
        self._say("Đang chuẩn bị...", "busy")

        def work() -> list[BacktestResult]:
            results: list[BacktestResult] = []
            for index, job in enumerate(jobs, start=1):
                if self.cancel_event.is_set():
                    break
                if len(jobs) > 1:
                    self._progress(0.0, f"Lần chạy {index}/{len(jobs)}")
                results.append(job())
            if not results:
                raise RuntimeError("Đã dừng.")
            return results

        self.worker.submit(work).add_done_callback(lambda task: self._post(lambda: self._finished(task)))

    def _finished(self, task: Any) -> None:
        self.running = False
        for button in (self.download_button, self.run_mode1, self.run_mode2):
            button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.progress_bar.grid_remove()
        try:
            results = task.result()
        except Exception as exc:
            self._say(str(exc), "error")
            return
        try:
            path = export_run_excel(
                results, self.data.runs_dir / "exports",
                mode=self._running_mode, stamp=datetime.now().strftime("%d-%m %H%M"),
            )
        except Exception as exc:
            self._say(f"Chạy xong nhưng không ghi được file: {exc}", "error")
            return
        self._last_file = path
        self._say(f"Xong · {self._summary(results)}", "ok")

    def _say(self, text: str, tone: str = "idle") -> None:
        """Single place that writes the status line, so tone and text agree."""
        colour = {
            "idle": COL_MUTED, "busy": COL_TEXT,
            "ok": COL_GREEN, "warn": COL_WARN, "error": COL_RED,
        }.get(tone, COL_MUTED)
        self.status_dot.configure(text_color=colour)
        self.status.configure(text=text, text_color=colour)

    @staticmethod
    def _summary(results: list[BacktestResult]) -> str:
        def line(item: BacktestResult) -> str:
            return (f"{item.return_pct:+.2f}%  ·  sụt {item.max_drawdown_pct:.1f}%  ·  "
                    f"{item.closed_trades} lượt  ·  thắng {item.win_rate_pct:.0f}%")

        if len(results) == 1:
            return line(results[0])
        best = max(results, key=lambda item: item.return_pct)
        worst = min(results, key=lambda item: item.return_pct)
        return (f"{len(results)} lần chạy  ·  cao nhất {best.return_pct:+.2f}%  ·  "
                f"thấp nhất {worst.return_pct:+.2f}%")

    def cancel(self) -> None:
        self.cancel_event.set()
        self._say("Đang dừng...", "warn")

    def _progress(self, value: float, text: str) -> None:
        bounded = min(1.0, max(0.0, value))
        self._post(lambda: (
            self.progress_bar.set(bounded),
            self._say(f"{text} · {bounded * 100:.0f}%", "busy"),
        ))

    def download_data(self) -> None:
        if self.running:
            return
        try:
            values = self._collect()
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Backtest", str(exc), parent=self.top)
            return
        # Cover both modes in one fetch: MODE 1's list plus every scenario symbol.
        scenario_symbols = [s for row in self.scenarios for s in row.symbols]
        symbols = list(dict.fromkeys(["VNINDEX", *values.symbols, *scenario_symbols]))
        if len(symbols) <= 1:
            messagebox.showinfo(
                "Backtest", "Chưa có mã nào ở tab MODE 1 lẫn kịch bản MODE 2.", parent=self.top,
            )
            return
        # Widen the window so a scenario that reaches today still gets its bars.
        first = min([values.start_date] + [row.start_date for row in self.scenarios])
        last = max([values.end_date] + [row.end_date for row in self.scenarios])
        self.running = True
        self.cancel_event.clear()
        for button in (self.download_button, self.run_mode1, self.run_mode2):
            button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_bar.set(0)
        self.progress_bar.grid()

        def fetch() -> tuple[int, int]:
            bars = 0
            for index, symbol in enumerate(symbols, start=1):
                if self.cancel_event.is_set():
                    raise RuntimeError("Đã dừng tải dữ liệu.")
                self._progress((index - 1) / len(symbols) * .5, f"Nến ngày · {symbol} ({index}/{len(symbols)})")
                bars += len(self.data.load_daily(symbol, first, last, warmup_sessions=250))
            for index, symbol in enumerate(values.symbols, start=1):
                if self.cancel_event.is_set():
                    raise RuntimeError("Đã dừng tải dữ liệu.")
                self._progress(.5 + index / max(1, len(values.symbols)) * .5,
                               f"Nến thực thi · {symbol} ({index}/{len(values.symbols)})")
                rows, _resolution, _warnings = self.data.load_execution(
                    symbol, first, last, resolution="AUTO",
                )
                bars += len(rows)
            return len(symbols), bars

        def done(task: Any) -> None:
            self.running = False
            for button in (self.download_button, self.run_mode1, self.run_mode2):
                button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.progress_bar.grid_remove()
            try:
                count, bars = task.result()
            except Exception as exc:
                self._say(str(exc), "error")
                return
            self._say(f"Đã có dữ liệu · {count} mã · {bars:,} nến", "ok")

        self.worker.submit(fetch).add_done_callback(lambda task: self._post(lambda: done(task)))

    def _open_output(self) -> None:
        """Open the results folder, highlighting the file the last run wrote."""
        folder = self.data.runs_dir / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        target = self._last_file if self._last_file and self._last_file.exists() else None
        if target is None:
            existing = sorted(folder.rglob("*.xlsx"), key=lambda f: f.stat().st_mtime)
            target = existing[-1] if existing else None
            if target is None:
                self._say("Thư mục kết quả đang trống, chạy backtest trước đã", "warn")
        try:
            if sys.platform.startswith("win"):
                if target:
                    subprocess.Popen(f'explorer /select,"{target}"')
                else:
                    os.startfile(folder)  # noqa: S606 - the user's own output folder
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showinfo(
                "Backtest", f"Thư mục kết quả:\n{folder}\n\n{exc}", parent=self.top,
            )

    # ------------------------------------------------------------- lifecycle

    def show(self) -> None:
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        if self.on_visibility_changed:
            self.on_visibility_changed(True)

    def hide(self) -> None:
        if self.top.winfo_exists():
            self.top.withdraw()

    def close(self) -> None:
        self.cancel_event.set()
        self.worker.shutdown(wait=False, cancel_futures=True)
        if self.on_visibility_changed:
            self.on_visibility_changed(False)
        if self.top.winfo_exists():
            self.top.destroy()

    def _post(self, callback: Callable[[], None]) -> None:
        post = getattr(self.parent, "_post_ui", None)
        if callable(post):
            post(lambda: self.top.winfo_exists() and callback())
        elif self.top.winfo_exists():
            self.top.after(0, callback)
