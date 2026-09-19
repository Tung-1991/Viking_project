from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from ..config import AppSettings
from ..dashboard.windows import (
    FONT_TABLE_HEADING,
    FONT_TABLE_VALUE,
    FONT_VALUE,
    PALETTE,
    SymbolPicker,
    _HoverHint,
    _window,
)
from ..exit_modes import exit_mode_label
from ..rules.business import StaticRuleParameters
from ..trading.market import normalize_exchange, validate_buy_window
from .data import HistoricalDataStore
from .engine import BacktestEngine, exit_comparison_variants
from .models import (
    NO_PHASE,
    VALID_PHASES,
    BacktestConfig,
    BacktestResult,
    BacktestScenario,
    BacktestSettings,
)
from .report import common_label, export_run_excel, workbook_name
from .replay import infer_exchange, infer_resolution, infer_symbol


COL_BG = PALETTE["PANEL"]
COL_SURFACE = PALETTE["SURFACE"]
COL_SURFACE_2 = PALETTE["SURFACE_2"]
COL_BORDER = PALETTE["BORDER"]
COL_TEXT = PALETTE["TEXT"]
COL_TITLE = PALETTE["TITLE"]
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
NO_PHASE_LABEL = "TỰ NHẬP TỶ TRỌNG"

SIMULATION_MODE_HELP = {
    "DAILY": "Dùng nến 1D, kiểm tra sau khi đóng ngày. Chạy nhanh nhưng có thể vào chậm hoặc mất tín hiệu trong phiên.",
    "REPLAY": "Phát lại từng nến intraday để bắt tín hiệu trong phiên. Thiếu dữ liệu của mã/ngày nào thì dừng.",
    "AUTO_HYBRID": "Có intraday thì dùng REPLAY; ngày thiếu hoặc PARTIAL tự dùng DAILY và ghi cảnh báo.",
}

PHASE_NAMES = (
    ("UPTREND", "UPTREND · TĂNG"),
    ("ACCUMULATION", "ACCUMULATION · TÍCH LŨY"),
    ("DISTRIBUTION", "DISTRIBUTION · PHÂN PHỐI"),
    ("DOWNTREND", "DOWNTREND · GIẢM"),
)

# Do not silently repopulate the user's scenario table with demo HSG rows after
# they intentionally clear it.  Real scenarios are persisted in scenarios.json.
DEFAULT_SCENARIOS: tuple[dict[str, Any], ...] = ()

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
            ("PIVOT NGANG %", "pivot_horizontal_pct", "Sai số tối đa để coi hai Pivot đang đi ngang."),
            ("VÙNG MA %", "ma_zone_pct", "Vùng đệm quanh MA dài hạn khi phân loại thị trường."),
            ("SỐ PHIÊN XÁC NHẬN", "confirm_sessions", "Trạng thái mới phải giữ đủ bấy nhiêu phiên mới được công nhận."),
            ("VOLUME TB (PHIÊN)", "volume_average_sessions", "Số phiên dùng để tính volume trung bình."),
            ("VOLUME CAO (TỶ LỆ)", "high_volume_ratio", "Ví dụ 1.5 nghĩa là 150% volume trung bình."),
            ("VOLUME THẤP (TỶ LỆ)", "low_volume_ratio", "Ví dụ 0.8 nghĩa là 80% volume trung bình."),
        ),
    ),
    (
        "PHASE 2 · ENTRY BUY",
        "Chỉ cấu hình EMA BUY ở đây; chu kỳ RSI hiện dùng chung với E · EXIT SELL.",
        (
            ("EMA MUA NHANH", "buy_ema_fast", "EMA nhanh của tín hiệu mua."),
            ("EMA MUA CHẬM", "buy_ema_slow", "EMA chậm của tín hiệu mua."),
            ("RSI BUY / E", "rsi_period", "Một chu kỳ RSI dùng chung cho BUY và E trong backend hiện tại."),
        ),
    ),
    (
        "E · EXIT SELL",
        "Rule thoát riêng: EMA SELL không thay đổi EMA BUY ở Phase 2.",
        (
            ("EMA SELL NHANH", "sell_ema_fast", "EMA nhanh của E chính."),
            ("EMA SELL CHẬM", "sell_ema_slow", "EMA chậm của E chính."),
            (
                "E SỚM TỪ LỖ %", "sellable_weak_exit_loss_pct",
                "Nhánh thử nghiệm sau T+2, mặc định -1%. Chỉ có tác dụng khi bật E SỚM bên dưới và chạy REPLAY.",
            ),
        ),
    ),
    (
        "PHASE 3 · VỐN VÀ THOÁT VỊ THẾ",
        "Vốn mỗi lệnh bằng tài sản nhân tỷ trọng chia cho số mã tối đa.",
        (
            ("CHỐT LỜI %", "take_profit_pct", "Lãi chạm mức này thì bán sạch vị thế. Chỉ chạy khi bật ô TP."),
            ("CẮT LỖ LỆNH ĐẦU %", "initial_sl_pct", "Cắt lỗ cho lệnh đầu mỗi chu kỳ. Nhập số âm."),
            ("CẮT LỖ VÀO LẠI %", "reentry_sl_pct", "Cắt lỗ cho lệnh vào lại sau khi vừa lỗ. Nhập số âm."),
            ("PROTECT · ARM %", "normal_arm_pct", "MFE đạt mức này thì PROTECT dùng đầy đủ TRAIL."),
            (
                "PROTECT · TRAIL %", "normal_giveback_pct",
                "Sau ARM, giá kích hoạt khi giảm X% từ peak.",
            ),
            (
                "PROTECT · START ATR ×", "normal_atr_activation_multiplier",
                "ATR14(T−1): T−1 là phiên ngày đã đóng gần nhất, KHÔNG phải ATR trừ 1. "
                "ATR theo đơn vị giá được chia cho giá đóng T−1 để ra ATR%. "
                "Khi công tắc START ON: lãi cao nhất từ lúc mua phải đạt ATR% × hệ số ở ô này. "
                "OFF: không đợi ATR; bắt đầu sau khi lệnh từng có lãi >0. Trong T+2 chưa bán được.",
            ),
            (
                "PROTECT · TRAIL ATR ×", "normal_atr_multiplier",
                "ON: khoảng giá được lùi = ATR% phiên trước × hệ số, tính theo % của giá cao nhất đã thấy. "
                "OFF: bỏ cách bảo vệ bằng ATR; START và GIỮ LÃI có công tắc riêng.",
            ),
            (
                "PROTECT · GIỮ MFE %", "normal_retention_pct",
                "ON: mức bảo vệ = giá mua + (giá cao nhất từng thấy − giá mua) × tỷ lệ này. "
                "Mua 100, từng lên 104, giữ 87,5% ⇒ mức 103,5; không bảo đảm bán đúng 103,5. "
                "OFF: bỏ cách giữ lãi; vẫn lưu con số để bật lại.",
            ),
            (
                "PROTECT · GIỮ ĐẾN %", "normal_retention_until_pct",
                "ON và nhập 5: chỉ nâng theo GIỮ LÃI khi đỉnh lãi dưới 5% (mua 100 là chưa tới 105). "
                "Từ 5% tới ARM 7%, mức cũ không hạ; ATR nếu ON vẫn có thể nâng. "
                "OFF: GIỮ LÃI tiếp tục tới ARM 7%. Trong mẫu 7 mã hiện tại, mốc 5% cho PnL 599,53 triệu, "
                "còn giữ tới ARM chỉ đạt 466,33 triệu; chưa xác nhận ngoài mẫu.",
            ),
            ("PROTECT · SELL %", "normal_sell_pct", "Mỗi lần AUTO bán X% lượng còn lại; SELL 100% làm REPEAT vô hiệu."),
            ("WHIPSAW · SỐ LẦN CẮT", "whipsaw_n", "EMA cắt qua lại bao nhiêu lần thì khóa mua mã đó."),
            ("WHIPSAW · SỐ PHIÊN ĐẾM", "whipsaw_x", "Đếm số lần cắt trong bấy nhiêu phiên gần nhất."),
            ("LOSS · SỐ LỆNH KHÓA", "loss_lock_count", "Số lệnh lỗ liên tiếp làm khóa BUY mới."),
        ),
    ),
)

PHASE_PARAMETER_KEYS = frozenset(
    key for _title, _subtitle, fields in PHASE_GROUPS for _label, key, _help in fields
)
BACKTEST_RULE_KEYS = PHASE_PARAMETER_KEYS | {
    "exposure", "whipsaw_enabled", "loss_lock_hours", "max_positions",
    "normal_policy", "normal_dynamic_enabled", "normal_repeat_enabled",
    "normal_atr_activation_enabled", "normal_atr_trail_enabled",
    "normal_retention_enabled", "normal_retention_until_enabled",
    # Research setting round-trips through BacktestSettings; UI control follows
    # only if the T+2 candidate passes review.
    "normal_t2_reset_enabled",
    "sellable_weak_exit_enabled", "sellable_weak_exit_loss_pct",
    "volume_confirmation", "no_compound_enabled", "force_min_lot_enabled",
    "buy_signal_use_ema", "buy_signal_use_rsi",
    "sell_signal_use_ema", "sell_signal_use_rsi",
    "buy_confirmation_enabled", "buy_confirmation_minutes",
    "buy_confirmation_require_ema", "buy_confirmation_require_rsi",
    "buy_window_enabled", "buy_window_start",
}

INTEGER_KEYS = {
    "ma_period", "pivot_left", "pivot_right", "confirm_sessions",
    "volume_average_sessions", "loss_lock_count",
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
        self.replay = self.engine.replay
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viking-backtest")
        self.cancel_event = threading.Event()
        self.running = False
        self._running_mode = MODE_1
        self._last_file = None
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
            fieldbackground=COL_SURFACE, rowheight=48, font=FONT_TABLE_VALUE, borderwidth=0,
        )
        style.configure(
            "Backtest.Treeview.Heading", background=COL_SURFACE_2, foreground=COL_TITLE,
            font=FONT_TABLE_HEADING, relief="flat", padding=(10, 9),
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
            self.tabs._segmented_button.configure(font=(FONT, 12, "bold"), text_color=COL_TEXT)
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
            parent, width=width, height=38, font=FONT_VALUE,
            fg_color=COL_SURFACE_2, border_color=COL_BORDER,
            text_color=COL_TEXT,
        )
        entry.insert(0, value)
        return entry

    @staticmethod
    def _label(
        parent: Any,
        text: str,
        size: int = 15,
        *,
        bold: bool = False,
        color: str | None = None,
    ) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent, text=text, anchor="w",
            text_color=color or (COL_TITLE if bold else COL_TEXT),
            font=(FONT, size, "bold", "italic") if bold else (FONT, size),
        )

    def _card(self, parent: Any, title: str, subtitle: str = "") -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color=COL_SURFACE, corner_radius=8, border_width=1, border_color=COL_BORDER)
        frame.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=6, sticky="ew", padx=16, pady=(10, 6))
        self._label(header, title, 16, bold=True).pack(side="left")
        if subtitle:
            self._hint(header, subtitle).pack(side="left", padx=(8, 0))
        return frame

    @staticmethod
    def _hint(parent: Any, text: str, mark: str = "?", color: str = COL_SLATE) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent, text=mark, width=24, height=24, corner_radius=12,
            font=(FONT, 11, "bold"), fg_color=color, hover_color=COL_BLUE,
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
            font=(FONT, 12, "bold"), fg_color=COL_SLATE, hover_color=COL_BLUE,
            command=lambda: self.symbol_picker.set(list(self.settings.watchlist)),
        ).grid(row=3, column=0, sticky="e", padx=16, pady=(0, 14))

        period = self._card(body, "KHOẢNG THỜI GIAN")
        period.grid(row=1, column=0, sticky="ew")
        period.grid_columnconfigure(4, weight=1)
        self._label(period, "TỪ NGÀY", bold=True).grid(row=2, column=0, sticky="w", padx=(16, 8), pady=(0, 12))
        self.start_entry = self._entry(period, self.config.start_date, 150)
        self.start_entry.grid(row=2, column=1, sticky="w", pady=(0, 12))
        self._label(period, "ĐẾN NGÀY", bold=True).grid(row=2, column=2, sticky="w", padx=(24, 8), pady=(0, 12))
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
        self.em_exit = ctk.BooleanVar(value="IND_EXIT" in self.config.em_modes)
        self.em_tp = ctk.BooleanVar(value="TP" in self.config.em_modes)
        for index, (label, var) in enumerate((
            ("TP", self.em_tp), ("PROTECT", self.em_normal), ("E", self.em_exit),
        )):
            ctk.CTkCheckBox(
                low, text=label, variable=var, font=(FONT, 12, "bold"),
                fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER,
                checkbox_width=22, checkbox_height=22, text_color=COL_TEXT,
            ).grid(row=0, column=1 + index, sticky="w", padx=(0, 14))
        ctk.CTkSwitch(
            low, text="WHIPSAW", variable=self.whipsaw,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
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
            command=self._sync_phase_controls, font=(FONT, 12, "bold"),
            progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=0, column=0, sticky="w")
        self._hint(
            switch_row,
            "Bật: mỗi ngày tự xếp VNINDEX vào một trong bốn trạng thái rồi lấy tỷ trọng tương ứng.\n"
            "Tắt: dùng đúng một trạng thái bạn chọn suốt kỳ.",
        ).grid(row=0, column=1, padx=10)
        self.fixed_block = ctk.CTkFrame(phase, fg_color="transparent")
        self.fixed_block.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 16))
        self._label(self.fixed_block, "DÙNG TRẠNG THÁI", 14, bold=True).grid(row=0, column=0, sticky="w")
        self.fixed_phase = ctk.CTkOptionMenu(
            self.fixed_block, values=sorted(VALID_PHASES), height=38, font=(FONT, 12),
            fg_color=COL_BLUE, width=230, dynamic_resizing=False,
            command=lambda _v: self._refresh_capital_hint(),
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

    # ---------------------------------------------------------------- MODE 2

    def _mode2_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", scrollbar_button_color=COL_BORDER,
        )
        body.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, minsize=380)
        frame = body

        # Keep the only instruction needed for this screen visible.
        note = ctk.CTkFrame(frame, fg_color="transparent")
        note.grid(row=1, column=0, sticky="ew", padx=14, pady=(8, 6))
        note.grid_columnconfigure(1, weight=1)
        self._label(note, "KỊCH BẢN MODE 2", 14, bold=True).grid(
            row=0, column=0, sticky="w", padx=(0, 8),
        )
        self._hint(
            note,
            "Mỗi dòng là một giai đoạn liên tiếp của cùng một tài khoản. Hệ thống chạy theo thời gian; "
            "vốn, vị thế, lệnh chờ, chuỗi LOSS và thời hạn khóa được chuyển sang dòng kế tiếp. "
            "Mỗi dòng vẫn có kết quả riêng trong cùng file Excel.",
        ).grid(row=0, column=1, sticky="w")
        self._label(
            note,
            "Chọn các dòng cần chạy. Dòng sau kế thừa vốn, vị thế, lệnh chờ và trạng thái bảo vệ của dòng trước.",
            13, color=COL_MUTED,
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        holder = ctk.CTkFrame(frame, fg_color="transparent")
        holder.grid(row=2, column=0, sticky="nsew", padx=14)
        holder.configure(height=380)
        holder.grid_propagate(False)
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        # Text columns read left aligned; fixed widths plus a horizontal bar so
        # the last column is never silently clipped.
        spec = (
            ("name", "TÊN", 340, "w"), ("symbol", "MÃ", 110, "w"),
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

        self._replay_panel(frame)

        edit = ctk.CTkFrame(frame, fg_color=COL_SURFACE, corner_radius=8, border_width=1, border_color=COL_BORDER)
        edit.grid(row=3, column=0, sticky="ew", padx=14, pady=10)
        for col in range(5):
            edit.grid_columnconfigure(col, weight=1, uniform="scenario_fields")
        edit.grid_columnconfigure(3, minsize=230)
        edit.grid_columnconfigure(4, minsize=170)
        self.scenario_name = self._entry(edit, "Giai đoạn mới")
        self.scenario_start = self._entry(edit, "2020-01-01", 150)
        today = date.today()
        self.scenario_end = ctk.CTkComboBox(
            edit, values=[today.isoformat(), date(today.year - 1, 12, 31).isoformat()],
            height=38, font=(FONT, 12), fg_color=COL_SURFACE_2, border_color=COL_BORDER,
            button_color=COL_BLUE, button_hover_color=COL_BLUE_HOVER, dropdown_font=(FONT, 11),
            command=lambda _v: self._refresh_scenario_preview(),
        )
        self.scenario_end.set(today.isoformat())
        self.scenario_phase = ctk.CTkOptionMenu(
            edit, values=[*sorted(VALID_PHASES), NO_PHASE_LABEL], width=230, height=38,
            font=(FONT, 12), dropdown_font=(FONT, 11), fg_color=COL_BLUE,
            dynamic_resizing=False, command=lambda _v: self._sync_scenario_phase(),
        )
        self.scenario_exposure = self._entry(edit, "60", 90)
        self._scenario_manual_exposure = "60"
        self._scenario_phase_was_manual = False
        self.scenario_exposure.bind("<KeyRelease>", lambda _e: self._refresh_scenario_preview())
        for col, (label, widget) in enumerate(zip(
            ("TÊN", "TỪ", "ĐẾN", "TRẠNG THÁI", "TỶ TRỌNG (%)"),
            (self.scenario_name, self.scenario_start, self.scenario_end,
             self.scenario_phase, self.scenario_exposure),
        )):
            self._label(edit, label, 13, bold=True).grid(
                row=0, column=col, sticky="w", padx=(16 if col == 0 else 6, 6), pady=(12, 3),
            )
            widget.grid(row=1, column=col, sticky="ew", padx=(14 if col == 0 else 5, 5), pady=(0, 8))
        self.scenario_exposure_note = self._label(
            edit,
            "Tỷ trọng lấy từ tab THAM SỐ; chỉ nhập tại đây khi chọn TỰ NHẬP TỶ TRỌNG.",
            12, color=COL_MUTED,
        )
        self.scenario_exposure_note.configure(width=1, justify="left", wraplength=620)
        self.scenario_exposure_note.grid(
            row=2, column=3, columnspan=2, sticky="w", padx=6, pady=(0, 6),
        )

        # Switches belong to the row, not to the shared settings, so two rows can
        # test two configurations over the same window.
        guards = ctk.CTkFrame(edit, fg_color="transparent")
        guards.grid(row=3, column=0, columnspan=5, sticky="ew", padx=16, pady=(2, 6))
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
            ("TP", "TP"), ("NORMAL", "PROTECT"), ("IND_EXIT", "E"),
        )):
            var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(
                guards, text=label, variable=var, font=(FONT, 12, "bold"),
                fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER,
                checkbox_width=22, checkbox_height=22, text_color=COL_TEXT,
            ).grid(row=1, column=1 + index, sticky="w", padx=(0, 12), pady=(6, 0))
            self.scenario_em[key] = var
        self.scenario_whipsaw = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            guards, text="WHIPSAW", variable=self.scenario_whipsaw,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=1, column=5, sticky="w", padx=(18, 0), pady=(6, 0))
        self._hint(
            guards,
            "Số mã tối đa, TP/PROTECT/E và WHIPSAW thuộc riêng dòng kịch bản này. "
            "Các ngưỡng số vẫn lấy chung từ tab THAM SỐ.",
        ).grid(row=1, column=6, padx=10, pady=(6, 0))
        # A scenario may hold several symbols; capital is still split by the
        # TỐI ĐA SỐ MÃ slot count, exactly like MODE 1.
        self.scenario_symbols = SymbolPicker(
            edit, [], columns=8, compact=True,
            placeholder="MÃ TRONG KỊCH BẢN — ví dụ HSG, HPG",
        )
        self.scenario_symbols.grid(row=4, column=0, columnspan=5, sticky="ew", padx=16, pady=(0, 10))
        self._sync_scenario_phase()

        # Says out loud what CHẠY MODE 2 is about to do and what file it writes.
        self.selection_preview = self._label(frame, "", 14, bold=True, color=COL_WARN)
        self.selection_preview.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 6))

        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._refresh_selection_preview()
        actions.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            actions, text="LƯU DÒNG", width=140, height=36, font=(FONT, 12, "bold"),
            fg_color=COL_SLATE, hover_color=COL_BLUE, command=self._save_scenario,
        ).grid(row=0, column=1, padx=5)
        ctk.CTkButton(
            actions, text="XÓA DÒNG", width=140, height=36, font=(FONT, 12, "bold"),
            fg_color=COL_SLATE, hover_color=COL_RED, command=self._delete_scenario,
        ).grid(row=0, column=2, padx=5)
        self.compare_exit_button = ctk.CTkButton(
            actions, text="SO SÁNH 4 MODE PROTECT", width=290, height=36,
            font=(FONT, 12, "bold"), fg_color=COL_BLUE,
            hover_color=COL_BLUE_HOVER, command=self.run_exit_comparison,
        )
        self.compare_exit_button.grid(row=0, column=3, padx=5)

    def _replay_panel(self, frame: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(
            frame, fg_color=COL_SURFACE, corner_radius=8,
            border_width=1, border_color=COL_BORDER,
        )
        panel.grid(row=0, column=0, sticky="ew", padx=14, pady=(2, 8))
        panel.grid_columnconfigure(3, weight=1)
        self._label(panel, "CÁCH CHẠY", 12, bold=True).grid(
            row=0, column=0, sticky="w", padx=(14, 8), pady=(10, 5),
        )
        self.simulation_mode = ctk.StringVar(value=self.config.simulation_mode.replace("_", " "))
        self.replay_mode_menu = ctk.CTkOptionMenu(
            panel, values=["DAILY", "REPLAY", "AUTO HYBRID"],
            variable=self.simulation_mode, width=175, height=32,
            font=(FONT, 12, "bold"), fg_color=COL_BLUE, dynamic_resizing=False,
            command=lambda _value: self._sync_replay_mode(),
        )
        self.replay_mode_menu.grid(row=0, column=1, sticky="w", padx=8, pady=(10, 5))
        self._hint(
            panel,
            "DAILY: nhanh, dùng nến ngày đã đóng.\n"
            "REPLAY: phát từng nến intraday; thiếu ngày nào thì dừng.\n"
            "AUTO HYBRID: có intraday thì replay, ngày thiếu tự dùng DAILY và ghi cảnh báo.",
        ).grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(10, 5))
        self.replay_mode_description = self._label(
            panel, "", 11, color=COL_MUTED,
        )
        self.replay_mode_description.grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=(10, 5))

        data_actions = ctk.CTkFrame(panel, fg_color="transparent")
        data_actions.grid(row=1, column=0, columnspan=7, sticky="ew", padx=12, pady=(3, 5))
        self.replay_import_button = ctk.CTkButton(
            data_actions, text="IMPORT REPLAY", width=140, height=32,
            font=(FONT, 12, "bold"), fg_color=COL_GREEN,
            hover_color=COL_GREEN_HOVER, command=self.import_replay_file,
        )
        self.replay_import_button.pack(side="left", padx=(0, 6))
        self.replay_delete_button = ctk.CTkButton(
            data_actions, text="XÓA DATA", width=95, height=32,
            font=(FONT, 12, "bold"), fg_color=COL_SLATE,
            hover_color=COL_RED, command=self.delete_replay_data,
        )
        self.replay_delete_button.pack(side="left", padx=(0, 18))
        self.replay_exchange = ctk.StringVar(value="HOSE")
        self.replay_exchange_menu = ctk.CTkOptionMenu(
            data_actions, values=["HOSE", "HNX", "UPCOM"], variable=self.replay_exchange,
            width=100, height=32, font=(FONT, 12, "bold"), fg_color=COL_BLUE,
            dynamic_resizing=False,
        )
        self.replay_exchange_menu.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            data_actions, text="LƯU SÀN", width=90, height=32,
            font=(FONT, 12, "bold"), fg_color=COL_SLATE,
            hover_color=COL_BLUE, command=self._set_replay_exchange,
        ).pack(side="left")

        columns = (
            ("symbol", "MÃ", 75), ("exchange", "SÀN", 80), ("resolution", "RES", 65),
            ("bars", "SỐ NẾN", 110), ("start", "TỪ", 135), ("end", "ĐẾN", 135),
            ("full", "FULL", 75), ("partial", "PARTIAL", 90),
            ("source", "FILE NGUỒN", 360),
        )
        holder = ctk.CTkFrame(panel, fg_color="transparent")
        holder.grid(row=2, column=0, columnspan=7, sticky="ew", padx=12, pady=(4, 3))
        holder.grid_columnconfigure(0, weight=1)
        self.replay_tree = ttk.Treeview(
            holder, columns=[key for key, _, _ in columns], show="headings",
            height=3, selectmode="browse", style="Backtest.Treeview",
        )
        for key, heading, width in columns:
            self.replay_tree.heading(
                key, text=heading, anchor="w" if key == "source" else "center",
            )
            self.replay_tree.column(
                key, width=width, minwidth=width,
                anchor="w" if key == "source" else "center", stretch=key == "source",
            )
        self.replay_tree.grid(row=0, column=0, sticky="ew")
        self.replay_tree.bind("<<TreeviewSelect>>", self._replay_dataset_selected, add="+")
        replay_scroll = ttk.Scrollbar(holder, orient="vertical", command=self.replay_tree.yview)
        replay_scroll.grid(row=0, column=1, sticky="ns")
        self.replay_tree.configure(yscrollcommand=replay_scroll.set)
        self._label(
            panel,
            "RES = độ phân giải · FULL = ngày đủ phiên · PARTIAL = ngày thiếu đầu hoặc cuối phiên",
            11, color=COL_MUTED,
        ).grid(row=3, column=0, columnspan=7, sticky="w", padx=14, pady=(0, 8))
        self._refresh_replay_datasets()
        self._sync_replay_mode()

    def _sync_replay_mode(self) -> None:
        mode = self.simulation_mode.get().strip().upper().replace(" ", "_")
        if hasattr(self, "replay_mode_description"):
            self.replay_mode_description.configure(
                text=SIMULATION_MODE_HELP.get(mode, "")
            )
        if hasattr(self, "run_mode2"):
            self.run_mode2.configure(text="CHẠY MODE 2", width=140)
        if hasattr(self, "selection_preview"):
            self._refresh_selection_preview()

    def _refresh_replay_datasets(self) -> None:
        if not hasattr(self, "replay_tree"):
            return
        for item in self.replay_tree.get_children():
            self.replay_tree.delete(item)
        for dataset in self.replay.list_datasets():
            key = f"{dataset.symbol}:{dataset.resolution}"
            self.replay_tree.insert("", "end", iid=key, values=(
                dataset.symbol, dataset.exchange or "CHƯA CHỌN", dataset.resolution, f"{dataset.bar_count:,}",
                dataset.coverage_start, dataset.coverage_end,
                dataset.full_days, dataset.partial_days, dataset.source_name,
            ))

    def _replay_dataset_selected(self, _event: Any = None) -> None:
        selected = self.replay_tree.selection()
        if not selected:
            return
        values = self.replay_tree.item(selected[0], "values")
        if len(values) > 1 and str(values[1]) in {"HOSE", "HNX", "UPCOM"}:
            self.replay_exchange.set(str(values[1]))

    def _replay_import_options(self, path: str) -> dict[str, Any] | None:
        dialog = ctk.CTkToplevel(self.top)
        dialog.title("IMPORT DỮ LIỆU REPLAY")
        dialog.geometry("650x440")
        dialog.resizable(False, False)
        dialog.transient(self.top)
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)
        values = {
            "symbol": ctk.StringVar(value=infer_symbol(path)),
            "resolution": ctk.StringVar(value=infer_resolution(path)),
            "exchange": ctk.StringVar(value=infer_exchange(path)),
            "timezone_name": ctk.StringVar(value="Asia/Ho_Chi_Minh"),
            "price_scale": ctk.StringVar(value="AUTO"),
        }
        labels = (
            ("MÃ", "symbol"), ("SÀN", "exchange"), ("RESOLUTION", "resolution"),
            ("TIMEZONE", "timezone_name"), ("HỆ SỐ GIÁ", "price_scale"),
        )
        self._label(dialog, "KIỂM TRA TRƯỚC KHI IMPORT", 17, bold=True).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 12),
        )
        for row, (label, key) in enumerate(labels, start=1):
            self._label(dialog, label, 13, bold=True).grid(
                row=row, column=0, sticky="w", padx=18, pady=6,
            )
            widget = (
                ctk.CTkOptionMenu(
                    dialog, values=["", "HOSE", "HNX", "UPCOM"], variable=values[key],
                    height=36, font=(FONT, 12), fg_color=COL_BLUE, dynamic_resizing=False,
                )
                if key == "exchange" else
                ctk.CTkEntry(
                    dialog, textvariable=values[key], height=36, font=(FONT, 12),
                    fg_color=COL_SURFACE_2, border_color=COL_BORDER,
                )
            )
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 18), pady=6)
        stats = self._label(dialog, "Đang kiểm tra file…", 13, color=COL_MUTED)
        stats.grid(row=6, column=0, columnspan=2, sticky="w", padx=18, pady=(12, 8))
        result: dict[str, Any] = {}

        def inspect() -> Any | None:
            try:
                raw_scale = values["price_scale"].get().strip().upper()
                scale = None if raw_scale in {"", "AUTO"} else float(raw_scale)
                preview = self.replay.inspect_file(
                    path, symbol=values["symbol"].get(),
                    resolution=values["resolution"].get(),
                    exchange=values["exchange"].get(),
                    timezone_name=values["timezone_name"].get(), price_scale=scale,
                )
            except Exception as exc:
                stats.configure(text=f"Lỗi: {exc}", text_color=COL_RED)
                return None
            partial = ", ".join(preview.partial_dates[:4]) or "không"
            stats.configure(
                text=(
                    f"{preview.symbol} · {preview.exchange} · {preview.resolution} · {preview.bar_count:,} nến\n"
                    f"{preview.coverage_start} → {preview.coverage_end} · "
                    f"FULL {preview.full_days} · PARTIAL {preview.partial_days}: {partial}\n"
                    f"Giá chia {preview.price_scale:g} · UTC được đổi sang giờ Việt Nam"
                ),
                text_color=COL_GREEN,
            )
            return preview

        def accept() -> None:
            preview = inspect()
            if preview is None:
                return
            result.update(
                symbol=preview.symbol, resolution=preview.resolution, exchange=preview.exchange,
                timezone_name=preview.timezone, price_scale=preview.price_scale,
            )
            dialog.destroy()

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", padx=18, pady=14)
        ctk.CTkButton(
            buttons, text="KIỂM TRA", width=125, height=36,
            fg_color=COL_SLATE, hover_color=COL_BLUE, command=inspect,
        ).grid(row=0, column=0, padx=5)
        ctk.CTkButton(
            buttons, text="IMPORT", width=125, height=36,
            fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER, command=accept,
        ).grid(row=0, column=1, padx=5)
        ctk.CTkButton(
            buttons, text="HỦY", width=90, height=36,
            fg_color=COL_SLATE, hover_color=COL_RED, command=dialog.destroy,
        ).grid(row=0, column=2, padx=5)
        dialog.after(50, inspect)
        self.top.wait_window(dialog)
        return result or None

    def import_replay_file(self) -> None:
        if self.running:
            return
        path = filedialog.askopenfilename(
            parent=self.top, title="Chọn dữ liệu TradingView",
            filetypes=[("TradingView", "*.csv *.xlsx"), ("CSV", "*.csv"), ("Excel", "*.xlsx")],
        )
        if not path:
            return
        options = self._replay_import_options(path)
        if not options:
            return
        self.running = True
        self.replay_import_button.configure(state="disabled")
        self.replay_delete_button.configure(state="disabled")
        self.run_mode2.configure(state="disabled")
        self.compare_exit_button.configure(state="disabled")
        self._say("Đang import dữ liệu REPLAY…", "busy")

        def done(task: Any) -> None:
            self.running = False
            self.replay_import_button.configure(state="normal")
            self.replay_delete_button.configure(state="normal")
            self.run_mode2.configure(state="normal")
            self.compare_exit_button.configure(state="normal")
            try:
                preview = task.result()
            except Exception as exc:
                self._say(str(exc), "error")
                return
            self._refresh_replay_datasets()
            self._say(
                f"Đã import {preview.symbol} · {preview.resolution}: "
                f"mới {preview.inserted:,}, thay {preview.replaced:,}, trùng {preview.unchanged:,}",
                "ok",
            )

        task = self.worker.submit(lambda: self.replay.import_file(path, **options))
        task.add_done_callback(lambda future: self._post(lambda: done(future)))

    def delete_replay_data(self) -> None:
        if self.running:
            return
        selected = self.replay_tree.selection()
        if not selected:
            messagebox.showinfo("REPLAY", "Chọn một dòng dữ liệu cần xóa.", parent=self.top)
            return
        symbol, resolution = selected[0].split(":", 1)
        if not messagebox.askyesno(
            "Xóa dữ liệu REPLAY",
            f"Xóa toàn bộ nến {symbol} · {resolution}?\n\nCache DNSE, scenario và kết quả không bị ảnh hưởng.",
            parent=self.top,
        ):
            return
        deleted = self.replay.delete_dataset(symbol, resolution)
        self._refresh_replay_datasets()
        self._say(f"Đã xóa {deleted:,} nến REPLAY của {symbol} · {resolution}.", "ok")

    def _set_replay_exchange(self) -> None:
        selected = self.replay_tree.selection()
        if not selected:
            messagebox.showinfo("SÀN", "Chọn một dòng dữ liệu trước.", parent=self.top)
            return
        symbol, resolution = selected[0].split(":", 1)
        try:
            self.replay.set_exchange(symbol, resolution, self.replay_exchange.get())
        except ValueError as exc:
            messagebox.showerror("SÀN", str(exc), parent=self.top)
            return
        self._refresh_replay_datasets()
        self._say(f"Đã lưu {symbol} · {self.replay_exchange.get()}.", "ok")

    def _refresh_scenarios(self) -> None:
        for item in self.scenario_tree.get_children():
            self.scenario_tree.delete(item)
        for index, row in enumerate(self.scenarios):
            self.scenario_tree.insert(
                "", "end", iid=row.id,
                tags=("even" if index % 2 == 0 else "odd",),
                values=(
                    row.name, ", ".join(row.symbols), row.start_date, row.end_date,
                    row.market_phase if row.uses_market_phase else NO_PHASE_LABEL,
                    "THEO THAM SỐ" if row.uses_market_phase else f"TỰ NHẬP · {row.exposure_pct:g}%",
                    row.max_positions,
                    "+".join(exit_mode_label(mode) for mode in row.em_modes) or "không",
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
        mode = (
            self.simulation_mode.get().strip().upper()
            if hasattr(self, "simulation_mode") else "DAILY"
        )
        rows = self._selected_scenarios()
        if not rows:
            self.selection_preview.configure(
                text=f"{mode} · CHƯA CHỌN KỊCH BẢN — bấm một hoặc nhiều dòng ở bảng trên",
                text_color=COL_WARN,
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
            text=f"{mode} · {len(rows)} kịch bản: {names}   →   1 file: {target}",
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
                source = "TỰ NHẬP"
            else:
                pct = float(self.exposure_entries[phase].get().strip() or 0)
                source = f"THEO {phase}"
        except (TypeError, ValueError, KeyError):
            self.scenario_slots_preview.configure(text="")
            return
        self.scenario_slots_preview.configure(
            text=f"→ mỗi lệnh {capital * pct / 100 / slots:,.0f} đ   "
                 f"({source} {pct:g}% ÷ {slots})",
        )

    def _sync_scenario_phase(self) -> None:
        """Show the effective exposure without losing the manual draft."""
        phase = self.scenario_phase.get()
        manual = phase == NO_PHASE_LABEL
        if getattr(self, "_scenario_phase_was_manual", False):
            current = self.scenario_exposure.get().strip()
            if current:
                self._scenario_manual_exposure = current

        value = self._scenario_manual_exposure
        if not manual:
            value = self.exposure_entries.get(phase).get().strip() if phase in self.exposure_entries else "0"

        self.scenario_exposure.configure(state="normal")
        self.scenario_exposure.delete(0, "end")
        self.scenario_exposure.insert(0, value)
        self.scenario_exposure.configure(state="normal" if manual else "disabled")
        self._scenario_phase_was_manual = manual
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
        self._scenario_manual_exposure = f"{row.exposure_pct:g}"
        self._scenario_phase_was_manual = False
        self.scenario_phase.set(row.market_phase if row.uses_market_phase else NO_PHASE_LABEL)
        self.scenario_exposure.configure(state="normal")
        self.scenario_exposure.delete(0, "end")
        self.scenario_exposure.insert(0, self._scenario_manual_exposure)
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
        title = ctk.CTkFrame(header, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        self._label(
            title, "CẤU HÌNH RIÊNG CỦA BACKTEST", 12, bold=True, color=COL_MUTED,
        ).pack(side="left")
        self._hint(
            title,
            "Sửa tại đây không đổi bot đang chạy.\n"
            "Phase 1 chỉ tác động MODE 1 khi tự nhận diện VNINDEX.\n"
            "Xác nhận BUY theo phút và giờ mua chỉ chạy MODE 2 với REPLAY intraday FULL.",
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            header, text="ĐỒNG BỘ TỪ BOT", width=185, height=36, font=(FONT, 12, "bold"),
            fg_color=COL_BLUE, hover_color=COL_BLUE_HOVER, command=self._sync_from_bot,
        ).grid(row=0, column=1, padx=4)

        money = self._card(
            body, "VỐN",
            "Vốn khởi đầu của MODE 1. Với MODE 2, số vốn này chỉ áp dụng cho dòng đầu; "
            "các dòng sau tiếp tục đúng tài khoản của dòng trước.",
        )
        money.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        money.grid_columnconfigure(2, weight=1)
        self._label(money, "VỐN (ĐỒNG)", 14, bold=True).grid(row=2, column=0, sticky="w", padx=(16, 10), pady=(0, 16))
        self.capital_entry = self._entry(money, f"{int(self.config.initial_capital)}", 230)
        self.capital_entry.grid(row=2, column=1, sticky="w", pady=(0, 16))
        self.capital_entry.bind("<KeyRelease>", lambda _e: self._refresh_capital_hint())

        self._market_card(body, row_index=2)
        exit_params = StaticRuleParameters.from_dict(self.config.rule_parameters)
        self.sell_signal_ema = ctk.BooleanVar(value=exit_params.sell_signal_use_ema)
        self.sell_signal_rsi = ctk.BooleanVar(value=exit_params.sell_signal_use_rsi)
        self.sellable_weak_exit = ctk.BooleanVar(
            value=exit_params.sellable_weak_exit_enabled,
        )
        self.dynamic_subrules: dict[str, ctk.BooleanVar] = {}
        row_index = 3
        for title, subtitle, fields in PHASE_GROUPS:
            card = self._card(body, title, subtitle)
            card.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
            for col in range(4):
                card.grid_columnconfigure(col, weight=1)
            for index, (label, key, help_text) in enumerate(fields):
                grid_row, grid_col = divmod(index, 2)
                cell = ctk.CTkFrame(card, fg_color="transparent")
                cell.grid(row=2 + grid_row, column=grid_col * 2, columnspan=2, sticky="ew", padx=(16, 12), pady=4)
                cell.grid_columnconfigure(1, weight=1)
                self._label(cell, label, 14, bold=True).grid(row=0, column=0, sticky="w")
                entry = self._entry(cell, f"{self.config.rule_parameters.get(key, '')}", 95)
                entry.grid(row=0, column=1, sticky="e", padx=(8, 5))
                self._hint(cell, help_text).grid(row=0, column=2, sticky="e")
                self._rule_entries[key] = entry
            if title == "E · EXIT SELL":
                self.exit_card = card
                controls = ctk.CTkFrame(card, fg_color="transparent")
                controls.grid(
                    row=2 + (len(fields) + 1) // 2, column=0,
                    columnspan=4, sticky="w", padx=16, pady=(6, 4),
                )
                for column, (label, variable) in enumerate((
                    ("DÙNG EMA", self.sell_signal_ema),
                    ("DÙNG RSI", self.sell_signal_rsi),
                )):
                    ctk.CTkCheckBox(
                        controls, text=label, variable=variable, width=105,
                        font=(FONT, 11, "bold"), fg_color=COL_GREEN,
                        text_color=COL_TEXT,
                    ).grid(row=0, column=column, padx=(0, 12))
                ctk.CTkSwitch(
                    controls, text="E SỚM SAU T+2", variable=self.sellable_weak_exit,
                    font=(FONT, 12, "bold"), progress_color=COL_GREEN,
                    text_color=COL_TEXT,
                ).grid(row=0, column=2, padx=(12, 0))
                self._hint(
                    controls,
                    "Nhánh thử nghiệm, mặc định OFF. Chỉ xét khi cổ bán được, lỗ chạm ngưỡng, "
                    "EMA2 dưới EMA4 và RSI giảm. Backtest yêu cầu REPLAY intraday.",
                ).grid(row=0, column=3, padx=10)
            padding_row = 2 + (len(fields) + 1) // 2
            if title == "E · EXIT SELL":
                padding_row += 1
            if any(key == "normal_atr_activation_multiplier" for _label, key, _help in fields):
                toggle_hints = (
                    ("normal_atr_activation_enabled", "START ATR", "OFF: không đợi ATR; bắt đầu sau khi lệnh từng có lãi >0. ATR14(T−1) là ATR 14 phiên ngày đã đóng tới hết phiên trước, không phải ATR trừ 1."),
                    ("normal_atr_trail_enabled", "TRAIL ATR", "OFF: bỏ mức bảo vệ tính từ ATR; GIỮ LÃI vẫn có thể chạy."),
                    ("normal_retention_enabled", "GIỮ LÃI", "OFF: bỏ mức bảo vệ giữ một phần lãi cao nhất; con số phần trăm vẫn được lưu."),
                    ("normal_retention_until_enabled", "MỐC GIỮ ĐẾN", "OFF: bỏ mốc 5%; nếu GIỮ LÃI ON thì tiếp tục nâng mức bảo vệ tới ARM 7%."),
                )
                for index, (key, label, hint) in enumerate(toggle_hints):
                    grid_row, grid_col = divmod(index, 2)
                    group = ctk.CTkFrame(card, fg_color="transparent")
                    group.grid(
                        row=padding_row + grid_row, column=grid_col * 2,
                        columnspan=2, sticky="w", padx=(16, 12), pady=4,
                    )
                    variable = ctk.BooleanVar(value=bool(getattr(exit_params, key)))
                    self.dynamic_subrules[key] = variable
                    ctk.CTkSwitch(
                        group, text=label, variable=variable,
                        font=(FONT, 11, "bold"), progress_color=COL_GREEN,
                        text_color=COL_TEXT,
                    ).pack(side="left")
                    self._hint(group, hint).pack(side="left", padx=(5, 0))
                padding_row += 2
            ctk.CTkLabel(card, text="", height=6).grid(row=padding_row, column=0)
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
            self._label(card, caption, 14, bold=True).grid(
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
        for index, (label, key, value, help_text) in enumerate(fields):
            cell = ctk.CTkFrame(card, fg_color="transparent")
            cell.grid(row=2, column=index, sticky="w", padx=(16 if index == 0 else 24, 0), pady=(0, 6))
            self._label(cell, label, 14, bold=True).grid(row=0, column=0, sticky="w", padx=(0, 8))
            entry = self._entry(cell, f"{value:g}", 85)
            entry.grid(row=0, column=1, sticky="w")
            entry.bind("<KeyRelease>", lambda _e: self._refresh_cost_hint())
            self._hint(cell, help_text).grid(row=0, column=2, padx=8)
            self.cost_entries[key] = entry
        ctk.CTkButton(
            card, text="LẤY PHÍ TỪ DNSE", width=185, height=36, font=(FONT, 12, "bold"),
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
            "Bộ lọc BUY theo phút/giờ cần Mode 2 với dữ liệu intraday đầy đủ.",
        )
        card.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(3, weight=1)

        self.loss_lock = ctk.BooleanVar(value=self.config.loss_lock_enabled)
        self.whipsaw = ctk.BooleanVar(value=self.config.whipsaw_enabled)
        params = StaticRuleParameters.from_dict(self.config.rule_parameters)
        lock_row = ctk.CTkFrame(card, fg_color="transparent")
        lock_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=16, pady=4)
        ctk.CTkSwitch(
            lock_row, text=f"KHÓA MÃ SAU {params.loss_lock_count} LỆNH LỖ", variable=self.loss_lock,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=0, column=0, sticky="w")
        self._hint(
            lock_row,
            "Đủ số LOSS liên tiếp theo tham số Phase 3 thì khóa BUY mã đó. Backtest tự mở lại sau số giờ bên cạnh.",
        ).grid(row=0, column=1, padx=10)
        self._label(lock_row, "MỞ SAU (GIỜ)", 14, bold=True).grid(row=0, column=2, sticky="w", padx=(22, 8))
        self.cooldown_entry = self._entry(lock_row, str(self.config.loss_lock_hours), 90)
        self.cooldown_entry.grid(row=0, column=3, sticky="w")

        signal_toggle_row = ctk.CTkFrame(card, fg_color="transparent")
        signal_toggle_row.grid(row=3, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self.buy_signal_ema = ctk.BooleanVar(value=params.buy_signal_use_ema)
        self.buy_signal_rsi = ctk.BooleanVar(value=params.buy_signal_use_rsi)
        self._label(signal_toggle_row, "TÍN HIỆU BUY", 13, bold=True).grid(row=0, column=0, sticky="w", padx=(0, 14))
        for column, (label, variable) in enumerate((
            ("BUY EMA", self.buy_signal_ema),
            ("BUY RSI", self.buy_signal_rsi),
        ), start=1):
            ctk.CTkCheckBox(
                signal_toggle_row, text=label, variable=variable, width=96,
                font=(FONT, 11, "bold"), fg_color=COL_GREEN, text_color=COL_TEXT,
            ).grid(row=0, column=column, sticky="w", padx=(0, 12))

        rule_toggle_row = ctk.CTkFrame(card, fg_color="transparent")
        rule_toggle_row.grid(row=4, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self.volume_confirmation = ctk.BooleanVar(value=params.volume_confirmation)
        self.no_compound = ctk.BooleanVar(value=params.no_compound_enabled)
        self.force_min_lot = ctk.BooleanVar(value=params.force_min_lot_enabled)
        for column, (label, variable, help_text) in enumerate((
            (
                "ĐỘ TIN CẬY VOLUME", self.volume_confirmation,
                "Tính và hiện nhãn CAO/TRUNG BÌNH/THẤP; không thay đổi state hoặc quyết định giao dịch.",
            ),
            (
                "KHÔNG COMPOUND", self.no_compound,
                "Lãi không làm tăng vốn tối đa cho lượt sau; lỗ vẫn làm giảm phần vốn còn dùng được.",
            ),
            (
                "AUTO 100 CP", self.force_min_lot,
                "Nếu ngân sách hợp lệ chưa đủ một lô, cho phép mua tối thiểu 100 CP khi vẫn còn room và đủ tiền gồm phí.",
            ),
        )):
            group = ctk.CTkFrame(rule_toggle_row, fg_color="transparent")
            group.grid(row=0, column=column, sticky="w", padx=(0, 22))
            ctk.CTkSwitch(
                group, text=label, variable=variable,
                font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
            ).pack(side="left")
            self._hint(group, help_text).pack(side="left", padx=(6, 0))

        confirm_row = ctk.CTkFrame(card, fg_color="transparent")
        confirm_row.grid(row=5, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self.buy_confirmation = ctk.BooleanVar(value=params.buy_confirmation_enabled)
        self.buy_confirmation_ema = ctk.BooleanVar(value=params.buy_confirmation_require_ema)
        self.buy_confirmation_rsi = ctk.BooleanVar(value=params.buy_confirmation_require_rsi)
        ctk.CTkSwitch(
            confirm_row, text="XÁC NHẬN BUY", variable=self.buy_confirmation,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.buy_confirmation_minutes = self._entry(
            confirm_row, str(params.buy_confirmation_minutes), 72,
        )
        self.buy_confirmation_minutes.grid(row=0, column=1, padx=(12, 4))
        self._label(confirm_row, "PHÚT", 13).grid(row=0, column=2, sticky="w")
        ctk.CTkCheckBox(
            confirm_row, text="EMA", variable=self.buy_confirmation_ema,
            font=(FONT, 12, "bold"), fg_color=COL_GREEN, width=68, text_color=COL_TEXT,
        ).grid(row=0, column=3, padx=(18, 0))
        ctk.CTkCheckBox(
            confirm_row, text="RSI", variable=self.buy_confirmation_rsi,
            font=(FONT, 12, "bold"), fg_color=COL_GREEN, width=68, text_color=COL_TEXT,
        ).grid(row=0, column=4, padx=(10, 0))
        self._hint(
            confirm_row,
            "Mặc định OFF. Tại mỗi nến quan sát, điều kiện đã chọn phải còn đạt cho đến đủ 1–120 phút giao dịch.\n"
            "Chỉ dùng với MODE 2 · REPLAY intraday FULL; SELL và SL không chờ.",
        ).grid(row=0, column=5, padx=10)

        window_row = ctk.CTkFrame(card, fg_color="transparent")
        window_row.grid(row=6, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self.buy_window_enabled = ctk.BooleanVar(value=params.buy_window_enabled)
        ctk.CTkSwitch(
            window_row, text="CHỈ MUA TỪ", variable=self.buy_window_enabled,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        for column, label, key in ((1, "GIỜ", "start"),):
            self._label(window_row, label, 13).grid(row=0, column=column, padx=(0, 6))
            entry = self._entry(window_row, getattr(params, f"buy_window_{key}"), 80)
            entry.grid(row=0, column=column + 1, padx=(0, 12))
            setattr(self, f"buy_window_{key}", entry)
        self._hint(
            window_row,
            "Chỉ xét BUY từ giờ đặt đến hết phiên hợp lệ của sàn, giờ Việt Nam.\n"
            "Tín hiệu trước giờ được giữ trong ngày nếu điều kiện còn đạt; không chuyển sang sáng hôm sau. SELL/SL không chờ.",
        ).grid(row=0, column=3)

        normal_row = ctk.CTkFrame(card, fg_color="transparent")
        normal_row.grid(row=7, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self._label(normal_row, "PROTECT MODE", 14, bold=True).grid(
            row=0, column=0, sticky="w", padx=(0, 12),
        )
        self.normal_policy = ctk.StringVar(value=params.normal_policy)
        self.normal_policy_menu = ctk.CTkOptionMenu(
            normal_row, values=["AUTO", "ALERT"],
            variable=self.normal_policy, width=180, height=36,
            font=FONT_VALUE, fg_color=COL_BLUE, dynamic_resizing=False,
        )
        self.normal_policy_menu.grid(row=0, column=1, sticky="w")
        self._hint(
            normal_row,
            "AUTO phát lệnh bán. ALERT dùng cùng điều kiện nhưng chỉ log; Telegram theo công tắc thông báo chung.",
        ).grid(row=0, column=2, padx=10)

        protect_option_row = ctk.CTkFrame(card, fg_color="transparent")
        protect_option_row.grid(row=8, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self.normal_dynamic = ctk.BooleanVar(value=params.normal_dynamic_enabled)
        self.normal_repeat = ctk.BooleanVar(value=params.normal_repeat_enabled)
        ctk.CTkSwitch(
            protect_option_row, text="DYNAMIC", variable=self.normal_dynamic,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(0, 18))
        ctk.CTkSwitch(
            protect_option_row, text="REPEAT", variable=self.normal_repeat,
            font=(FONT, 12, "bold"), progress_color=COL_GREEN, text_color=COL_TEXT,
        ).grid(row=0, column=1, sticky="w", padx=(0, 18))
        self._hint(
            protect_option_row,
            "DYNAMIC dùng ATR14 phiên T−1 để bảo vệ dưới ARM khi mức PROTECT cao hơn SL. "
            "REPEAT chỉ có tác dụng khi SELL < 100% và cần peak mới cao hơn peak lần trước ít nhất TRAIL%.",
        ).grid(row=0, column=2, padx=10)

        fill_row = ctk.CTkFrame(card, fg_color="transparent")
        fill_row.grid(row=9, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self._label(fill_row, "ĐỢT ATO", 14, bold=True).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.fill_session = ctk.CTkOptionMenu(
            fill_row, values=["CHO PHÉP · khớp giá mở cửa", "KHÔNG · khớp sau 9h15"],
            height=36, font=FONT_VALUE, fg_color=COL_BLUE, width=265,
            dynamic_resizing=False,
        )
        self.fill_session.set(
            "KHÔNG · khớp sau 9h15" if self.config.fill_session == "CONTINUOUS"
            else "CHO PHÉP · khớp giá mở cửa"
        )
        self.fill_session.grid(row=0, column=1, sticky="w")
        self._hint(
            fill_row,
            "Đối ứng công tắc ATO của bot thật; chỉ tác động mã HOSE.\n"
            "CHO PHÉP: khớp giá mở cửa. KHÔNG: khớp sau 9h15; thiếu intraday thì kết quả ghi cảnh báo.",
        ).grid(row=0, column=2, padx=10)

        sell_row = ctk.CTkFrame(card, fg_color="transparent")
        sell_row.grid(row=10, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 4))
        self._label(sell_row, "BÁN KHI CỔ VỀ", 14, bold=True).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.sell_wait = ctk.CTkOptionMenu(
            sell_row, values=["KIỂM TRA LẠI ĐIỀU KIỆN", "BÁN THEO YÊU CẦU CŨ"],
            height=36, font=FONT_VALUE, fg_color=COL_BLUE, width=265,
            dynamic_resizing=False,
        )
        self.sell_wait.set("BÁN THEO YÊU CẦU CŨ" if self.config.sell_wait_policy == "KEEP" else "KIỂM TRA LẠI ĐIỀU KIỆN")
        self.sell_wait.grid(row=0, column=1, sticky="w")
        self._hint(
            sell_row,
            "Cổ phiếu chỉ bán được từ phiên chiều (13:00) ngày T+2; cuối tuần và ngày nghỉ không được tính.\n"
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
        self.volume_confirmation.set(bool(params.get("volume_confirmation", False)))
        self.no_compound.set(bool(params.get("no_compound_enabled", True)))
        self.force_min_lot.set(bool(params.get("force_min_lot_enabled", True)))
        self.buy_signal_ema.set(bool(params.get("buy_signal_use_ema", True)))
        self.buy_signal_rsi.set(bool(params.get("buy_signal_use_rsi", True)))
        self.sell_signal_ema.set(bool(params.get("sell_signal_use_ema", True)))
        self.sell_signal_rsi.set(bool(params.get("sell_signal_use_rsi", True)))
        self.sellable_weak_exit.set(bool(params.get("sellable_weak_exit_enabled", False)))
        self.buy_confirmation.set(bool(params.get("buy_confirmation_enabled", False)))
        self.buy_confirmation_ema.set(bool(params.get("buy_confirmation_require_ema", True)))
        self.buy_confirmation_rsi.set(bool(params.get("buy_confirmation_require_rsi", True)))
        self.buy_confirmation_minutes.delete(0, "end")
        self.buy_confirmation_minutes.insert(0, str(params.get("buy_confirmation_minutes", 5)))
        self.buy_window_enabled.set(bool(params.get("buy_window_enabled", False)))
        normal_policy = str(params.get("normal_policy", "AUTO") or "AUTO").upper()
        self.normal_policy.set(normal_policy if normal_policy in {"AUTO", "ALERT"} else "AUTO")
        self.normal_dynamic.set(bool(params.get("normal_dynamic_enabled", False)))
        self.normal_repeat.set(bool(params.get("normal_repeat_enabled", False)))
        for key, variable in self.dynamic_subrules.items():
            variable.set(bool(params.get(key, True)))
        for key, default in (("start", "14:00"),):
            entry = getattr(self, f"buy_window_{key}")
            entry.delete(0, "end")
            entry.insert(0, str(params.get(f"buy_window_{key}", default)))
        self.em_normal.set("NORMAL" in self.settings.bot_em_modes)
        self.em_exit.set("IND_EXIT" in self.settings.bot_em_modes)
        self.em_tp.set("TP" in self.settings.bot_em_modes)
        self.mode1_slots.delete(0, "end")
        self.mode1_slots.insert(0, f"{params.get('max_positions', 5)}")
        self.cooldown_entry.delete(0, "end")
        self.cooldown_entry.insert(0, str(params.get("loss_lock_hours", 24)))
        for key, value in (
            ("buy_fee_pct", self.settings.buy_fee_pct),
            ("sell_fee_pct", self.settings.sell_fee_pct),
            ("sell_tax_pct", self.settings.sell_tax_pct),
        ):
            entry = self.cost_entries[key]
            entry.delete(0, "end")
            entry.insert(0, f"{float(value):g}")
        self.sell_wait.set(
            "BÁN THEO YÊU CẦU CŨ" if self.settings.sell_wait_policy == "KEEP" else "KIỂM TRA LẠI ĐIỀU KIỆN"
        )
        self.fill_session.set("CHO PHÉP · khớp giá mở cửa" if self.settings.allow_ato
                              else "KHÔNG · khớp sau 9h15")
        self._refresh_cost_hint()
        self._refresh_capital_hint()
        self._say("Đã lấy tham số từ bot đang chạy", "ok")

    def _resolve_symbol_exchanges(self, symbols: list[str]) -> dict[str, str]:
        """Resolve every exchange explicitly; DAILY must never guess HOSE."""
        resolved: dict[str, str] = {}
        getter = getattr(self.client, "get_secdef", None)
        for symbol in dict.fromkeys(str(value).upper() for value in symbols):
            exchange = normalize_exchange(self.settings.symbol_exchanges.get(symbol))
            if not exchange:
                exchange = normalize_exchange(self.replay.exchange_for(symbol))
            if not exchange and callable(getter):
                try:
                    secdef = getter(symbol) or {}
                except Exception:
                    secdef = {}
                exchange = normalize_exchange(
                    secdef.get("marketId", secdef.get("market", secdef.get("exchange", "")))
                )
            if not exchange:
                raise ValueError(
                    f"Chưa xác định được sàn của {symbol}; hãy đặt sàn ở KẾT NỐI hoặc IMPORT REPLAY."
                )
            resolved[symbol] = exchange
        return resolved

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
            bar, text="MỞ THƯ MỤC", width=125, height=34, font=(FONT, 12, "bold"),
            fg_color=COL_SLATE, hover_color=COL_BLUE, command=self._open_output,
        )
        self.open_button.grid(row=0, column=2, padx=5)
        self.clear_cache_button = ctk.CTkButton(
            bar, text="XÓA CACHE", width=105, height=34, font=(FONT, 12, "bold"),
            fg_color=COL_SLATE, hover_color=COL_RED, command=self.clear_downloaded_data,
        )
        self.clear_cache_button.grid(row=0, column=3, padx=5)
        self.cancel_button = ctk.CTkButton(
            bar, text="DỪNG", width=75, height=34, font=(FONT, 12, "bold"),
            fg_color=COL_SLATE, hover_color=COL_RED, state="disabled", command=self.cancel,
        )
        self.cancel_button.grid(row=0, column=4, padx=5)
        self.download_button = ctk.CTkButton(
            bar, text="TẢI DNSE", width=110, height=34, font=(FONT, 12, "bold"),
            fg_color=COL_BLUE, hover_color=COL_BLUE_HOVER, command=self.download_data,
        )
        self.download_button.grid(row=0, column=5, padx=5)
        self.run_mode1 = ctk.CTkButton(
            bar, text="CHẠY MODE 1", width=140, height=34, font=(FONT, 12, "bold"),
            fg_color=COL_GREEN, hover_color=COL_GREEN_HOVER, command=self.run_mode_1,
        )
        self.run_mode1.grid(row=0, column=6, padx=5)
        self.run_mode2 = ctk.CTkButton(
            bar, text="CHẠY MODE 2", width=140, height=34, font=(FONT, 12, "bold"),
            fg_color=COL_BLUE, hover_color=COL_BLUE_HOVER, command=self.run_mode_2,
        )
        self.run_mode2.grid(row=0, column=7, padx=5)
        self._sync_replay_mode()

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
        params["volume_confirmation"] = bool(self.volume_confirmation.get())
        params["no_compound_enabled"] = bool(self.no_compound.get())
        params["force_min_lot_enabled"] = bool(self.force_min_lot.get())
        if not (self.buy_signal_ema.get() or self.buy_signal_rsi.get()):
            raise ValueError("TÍN HIỆU BUY phải bật ít nhất EMA hoặc RSI")
        if not (self.sell_signal_ema.get() or self.sell_signal_rsi.get()):
            raise ValueError("TÍN HIỆU SELL phải bật ít nhất EMA hoặc RSI")
        params["buy_signal_use_ema"] = bool(self.buy_signal_ema.get())
        params["buy_signal_use_rsi"] = bool(self.buy_signal_rsi.get())
        params["sell_signal_use_ema"] = bool(self.sell_signal_ema.get())
        params["sell_signal_use_rsi"] = bool(self.sell_signal_rsi.get())
        params["sellable_weak_exit_enabled"] = bool(self.sellable_weak_exit.get())
        try:
            confirmation_minutes = int(float(self.buy_confirmation_minutes.get().strip() or 5))
        except ValueError as exc:
            raise ValueError("XÁC NHẬN BUY phải là số phút") from exc
        if not 1 <= confirmation_minutes <= 120:
            raise ValueError("XÁC NHẬN BUY phải từ 1 đến 120 phút")
        if self.buy_confirmation.get() and not (
            self.buy_confirmation_ema.get() or self.buy_confirmation_rsi.get()
        ):
            raise ValueError("XÁC NHẬN BUY phải chọn ít nhất EMA hoặc RSI")
        params["buy_confirmation_enabled"] = bool(self.buy_confirmation.get())
        params["buy_confirmation_minutes"] = confirmation_minutes
        params["buy_confirmation_require_ema"] = bool(self.buy_confirmation_ema.get())
        params["buy_confirmation_require_rsi"] = bool(self.buy_confirmation_rsi.get())
        window_start = self.buy_window_start.get().strip()
        validate_buy_window(window_start, "15:00")
        params.update(buy_window_enabled=bool(self.buy_window_enabled.get()),
                      buy_window_start=window_start)
        params["normal_policy"] = self.normal_policy.get()
        params["normal_dynamic_enabled"] = bool(self.normal_dynamic.get())
        params["normal_repeat_enabled"] = bool(self.normal_repeat.get())
        for key, variable in self.dynamic_subrules.items():
            params[key] = bool(variable.get())
        params.pop("buy_window_end", None)
        try:
            params["max_positions"] = int(float(self.mode1_slots.get().strip() or 5))
        except ValueError as exc:
            raise ValueError(f"TỐI ĐA SỐ MÃ không hợp lệ: {self.mode1_slots.get()!r}") from exc
        try:
            loss_lock_hours = int(float(self.cooldown_entry.get().strip() or 0))
        except ValueError as exc:
            raise ValueError("MỞ KHÓA SAU phải là số giờ") from exc
        params["loss_lock_hours"] = loss_lock_hours
        normalized = StaticRuleParameters.from_dict(params).validate()
        params = normalized.to_dict()
        fees = {
            key: float(self.cost_entries[key].get().strip() or 0)
            for key in ("buy_fee_pct", "sell_fee_pct", "sell_tax_pct")
        }
        if any(value < 0 or value > 5 for value in fees.values()):
            raise ValueError("Phí và thuế phải nằm trong 0–5%")
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
            loss_lock_hours=loss_lock_hours,
            whipsaw_enabled=bool(self.whipsaw.get()),
            em_modes=[
                name for name, variable in (
                    ("TP", self.em_tp), ("NORMAL", self.em_normal),
                    ("IND_EXIT", self.em_exit),
                ) if variable.get()
            ],
            sell_wait_policy="KEEP" if self.sell_wait.get().startswith("BÁN") else "RECHECK",
            fill_session="CONTINUOUS" if self.fill_session.get().startswith("KHÔNG") else "ATO",
            buy_fee_pct=fees["buy_fee_pct"],
            sell_fee_pct=fees["sell_fee_pct"],
            sell_tax_pct=fees["sell_tax_pct"],
            rule_parameters=params,
            simulation_mode=self.simulation_mode.get().strip().upper().replace(" ", "_"),
        )
        self.config = values
        self.data.save_settings(values.to_dict())
        return values

    # ---------------------------------------------------------------- running

    def run_mode_1(self) -> None:
        try:
            values = self._collect()
            if values.rule_parameters.get("buy_confirmation_enabled"):
                raise ValueError("XÁC NHẬN BUY theo phút chỉ chạy bằng MODE 2 · REPLAY")
            if values.rule_parameters.get("buy_window_enabled"):
                raise ValueError("KHUNG GIỜ MUA chỉ chạy bằng MODE 2 · REPLAY")
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
                symbol_exchanges=self._resolve_symbol_exchanges(values.symbols),
                # Name carries the exit stack so eight runs land in eight
                # files instead of overwriting each other.
                run_name="MODE 1 · " + ("+".join(
                    m.replace("IND_EXIT", "E").replace("NORMAL", "PROTECT")
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
            if (
                (values.rule_parameters.get("buy_confirmation_enabled") or values.rule_parameters.get("buy_window_enabled"))
                and values.simulation_mode != "REPLAY"
            ):
                raise ValueError("Lọc BUY theo phút/giờ cần chọn CÁCH CHẠY = REPLAY")
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Backtest", str(exc), parent=self.top)
            return

        # Picking several rows means one account walking through them in order:
        # what the previous period ended with is what the next one starts with.
        try:
            rows = sorted(rows, key=lambda row: (row.start_date, row.end_date))
            previous_end = ""
            for row in rows:
                if previous_end and row.start_date <= previous_end:
                    raise ValueError(
                        f"Kịch bản bị chồng thời gian tại {row.name}; ngày bắt đầu phải sau {previous_end}."
                    )
                previous_end = row.end_date
            selected_exchanges = self._resolve_symbol_exchanges([
                symbol for row in rows for symbol in row.symbols
            ])
        except ValueError as exc:
            messagebox.showerror("MODE 2", str(exc), parent=self.top)
            return
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
                    simulation_mode=values.simulation_mode,
                    symbol_exchanges=selected_exchanges,
                    progress=self._progress, cancelled=self.cancel_event.is_set,
                )
                carry["capital"] = result.final_equity
                return result

            return run

        self._start(MODE_2, [job(row) for row in rows])

    def run_exit_comparison(self) -> None:
        """Run AUTO/ALERT × DYNAMIC OFF/ON independently."""
        rows = self._selected_scenarios()
        if not rows:
            self.tabs.set(MODE_2)
            messagebox.showinfo(
                "SO SÁNH EXIT", "Chọn ít nhất một dòng kịch bản MODE 2.", parent=self.top,
            )
            return
        try:
            values = self._collect()
            if (
                values.rule_parameters.get("buy_confirmation_enabled")
                or values.rule_parameters.get("buy_window_enabled")
            ) and values.simulation_mode != "REPLAY":
                raise ValueError("Lọc BUY theo phút/giờ cần chọn CÁCH CHẠY = REPLAY")
            exchanges = self._resolve_symbol_exchanges([
                symbol for row in rows for symbol in row.symbols
            ])
        except (TypeError, ValueError) as exc:
            messagebox.showerror("SO SÁNH EXIT", str(exc), parent=self.top)
            return

        jobs: list[Callable[[], BacktestResult]] = []
        for selected in rows:
            for scenario, params in exit_comparison_variants(
                selected, values.rule_parameters,
            ):
                def run(
                    scenario: BacktestScenario = scenario,
                    params: dict[str, Any] = params,
                ) -> BacktestResult:
                    result = self.engine.run_scenario(
                        scenario,
                        initial_capital=values.initial_capital,
                        rule_parameters=params,
                        loss_lock_enabled=values.loss_lock_enabled,
                        loss_lock_hours=values.loss_lock_hours,
                        sell_wait_policy=values.sell_wait_policy,
                        fill_session=values.fill_session,
                        buy_fee_rate=values.buy_fee_pct / 100.0,
                        sell_fee_rate=values.sell_fee_pct / 100.0,
                        sell_tax_rate=values.sell_tax_pct / 100.0,
                        simulation_mode=values.simulation_mode,
                        symbol_exchanges=exchanges,
                        progress=self._progress,
                        cancelled=self.cancel_event.is_set,
                    )
                    result.data_quality["comparison"] = "INDEPENDENT_EXIT_POLICY"
                    return result

                jobs.append(run)
        self._start("MODE 2 · EXIT", jobs)

    def _start(self, mode: str, jobs: list[Callable[[], BacktestResult]]) -> None:
        if self.running or not jobs:
            return
        self._running_mode = mode
        self.running = True
        self.cancel_event.clear()
        for button in (self.clear_cache_button, self.download_button, self.run_mode1, self.run_mode2,
                       self.compare_exit_button,
                       self.replay_import_button, self.replay_delete_button):
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
        for button in (self.clear_cache_button, self.download_button, self.run_mode1, self.run_mode2,
                       self.compare_exit_button,
                       self.replay_import_button, self.replay_delete_button):
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
        # Execution bars matter to MODE 2 as well (SL/TP/PROTECT fills), so a
        # symbol that exists only in a scenario must not be limited to 1D data.
        scenario_symbols = [s for row in self.scenarios for s in row.symbols]
        execution_symbols = list(dict.fromkeys([*values.symbols, *scenario_symbols]))
        symbols = ["VNINDEX", *execution_symbols]
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
        for button in (self.clear_cache_button, self.download_button, self.run_mode1, self.run_mode2,
                       self.compare_exit_button,
                       self.replay_import_button, self.replay_delete_button):
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
            for index, symbol in enumerate(execution_symbols, start=1):
                if self.cancel_event.is_set():
                    raise RuntimeError("Đã dừng tải dữ liệu.")
                self._progress(.5 + index / max(1, len(execution_symbols)) * .5,
                               f"Nến thực thi · {symbol} ({index}/{len(execution_symbols)})")
                rows, _resolution, _warnings = self.data.load_execution(
                    symbol, first, last, resolution="AUTO",
                )
                bars += len(rows)
            return len(symbols), bars

        def done(task: Any) -> None:
            self.running = False
            for button in (self.clear_cache_button, self.download_button, self.run_mode1, self.run_mode2,
                           self.compare_exit_button,
                           self.replay_import_button, self.replay_delete_button):
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

    def clear_downloaded_data(self) -> None:
        """Delete downloaded OHLC caches only; preserve settings, scenarios and reports."""
        if self.running:
            return
        cache_root = self.data.cache_dir.resolve()
        files = [
            path for path in self.data.cache_dir.glob("*.json")
            if path.is_file() and path.resolve().parent == cache_root
        ]
        if not files:
            messagebox.showinfo("Backtest", "Cache dữ liệu đang trống.", parent=self.top)
            return
        total_bytes = sum(path.stat().st_size for path in files)
        size_mb = total_bytes / (1024 * 1024)
        if not messagebox.askyesno(
            "Xóa cache backtest",
            f"Xóa {len(files)} file dữ liệu ({size_mb:.1f} MB)?\n\n"
            "Scenario, thiết lập và file Excel kết quả vẫn được giữ nguyên.",
            parent=self.top,
        ):
            return
        deleted = 0
        failures: list[str] = []
        for path in files:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
        if failures:
            self._say(f"Đã xóa {deleted}/{len(files)} file; lỗi {len(failures)} file.", "error")
            return
        self._say(f"Đã xóa {deleted} file cache ({size_mb:.1f} MB).", "ok")

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
        if self.on_visibility_changed:
            self.on_visibility_changed(False)

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
