from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk

from ..config import AppSettings, account_root, load_settings, save_settings
from ..branding import APP_NAME, window_title
from ..dashboard.windows import FONT_KEY, FONT_VALUE, PALETTE, _HoverHint, _window
from .business import StaticRuleParameters
from ..trading.market import validate_buy_window


class RuleSettingsPopup:
    BG = "#181B20"
    SURFACE = "#22262D"
    BORDER = "#30353D"
    TEXT = "#E8EBEF"
    TITLE = PALETTE["TITLE"]
    MUTED = "#C5CBD4"
    GREEN = "#22C55E"
    BLUE = "#2B6CB0"
    WARN = "#F59E0B"

    def __init__(
        self,
        parent: ctk.CTk,
        settings: AppSettings,
        account_id: str,
        on_saved: Callable[[], None],
        on_visibility_changed: Callable[[bool], None] | None = None,
        *, trade_state: Any = None,
    ):
        self.parent = parent
        self.settings, self.account_id, self.on_saved = settings, account_id, on_saved
        self.on_visibility_changed = on_visibility_changed
        self.trade_state = trade_state
        self.params = StaticRuleParameters.from_dict(settings.rule_parameters)
        self._setting_traces = []
        # The strategy still computes one RSI series for BUY and E. Mirror the
        # setting in both cards so editing either place cannot silently diverge.
        self.shared_rsi_period = tk.StringVar(value=str(self.params.rsi_period))
        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width, height = min(1080, screen_w - 60), min(720, screen_h - 90)
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 3)
        self.top = _window(parent, window_title("RULE"), f"{width}x{height}+{x}+{y}")
        self.top.withdraw()
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.tk.call("wm", "transient", self.top._w, "")
        self.top.resizable(True, True)
        self.top.minsize(900, 600)
        self.top.configure(fg_color="#111318")
        self.top.grid_columnconfigure(0, weight=1)
        self.top.grid_rowconfigure(0, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self.top.bind("<Escape>", lambda _event: self.hide(), add="+")

        self.tabs = ctk.CTkTabview(
            self.top, fg_color=self.BG, border_width=1, border_color=self.BORDER,
            segmented_button_selected_color=self.BLUE,
            segmented_button_selected_hover_color="#245C92",
            segmented_button_unselected_color="#3A3F47",
            segmented_button_unselected_hover_color="#4B515B",
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 5))
        self._tab_font(self.tabs, 12)
        self._business_tab(self.tabs.add("NGHIỆP VỤ"))
        self._exit_manager_tab(self.tabs.add("E/M"))
        self._execution_tab(self.tabs.add("THỰC THI"))
        self._install_execution_preview()

        footer = ctk.CTkFrame(self.top, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=14, pady=(3, 12))
        self.status = ctk.CTkLabel(
            footer, text="Các thay đổi được daemon nhận tự động sau khi lưu.",
            font=("Segoe UI", 12), text_color=self.TEXT,
        )
        self.status.pack(side="left")
        ctk.CTkButton(
            footer, text="LƯU THAY ĐỔI", width=170, height=38,
            font=("Segoe UI", 12, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self.save,
        ).pack(side="right")
        self.show()

    def show(self) -> None:
        if self.trade_state and hasattr(self, "block_book"):
            self._refresh_blocks()
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

    def _close(self) -> None:
        for variable, handle in [*getattr(self, "_preview_traces", []), *self._setting_traces]:
            variable.trace_remove("write", handle)
        self._preview_traces = []
        self._setting_traces = []
        if self.on_visibility_changed:
            self.on_visibility_changed(False)
        if self.top.winfo_exists():
            self.top.destroy()

    @staticmethod
    def _tab_font(tabview: ctk.CTkTabview, size: int) -> None:
        try:
            tabview._segmented_button.configure(
                font=("Segoe UI", size, "bold"), text_color="#E8EBEF",
            )
        except AttributeError:
            pass

    def _business_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        phases = ctk.CTkTabview(
            frame, fg_color="#15181D",
            segmented_button_selected_color=self.GREEN,
            segmented_button_selected_hover_color="#16A34A",
            segmented_button_unselected_color="#3A3F47",
        )
        self.phase_tabs = phases
        phases.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self._tab_font(phases, 12)
        self._phase1(phases.add("PHASE 1 · THỊ TRƯỜNG"))
        self._phase2(phases.add("PHASE 2 · ENTRY BUY"))
        self._phase3(phases.add("PHASE 3 · VỐN & BẢO VỆ"))

    def _content(
        self,
        parent: ctk.CTkFrame,
        columns: int = 3,
        *,
        weights: tuple[int, ...] | None = None,
    ) -> ctk.CTkScrollableFrame:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(
            parent,
            fg_color="transparent",
            scrollbar_button_color=self.BORDER,
            scrollbar_button_hover_color="#4B515B",
        )
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        for column in range(columns):
            if weights:
                body.grid_columnconfigure(column, weight=weights[column])
            else:
                body.grid_columnconfigure(column, weight=1, uniform="rule")
        body.grid_rowconfigure(1, weight=0)
        return body

    def _hint_icon(self, parent: Any, text: str) -> ctk.CTkLabel:
        icon = ctk.CTkLabel(
            parent, text="?", width=22, height=22,
            font=("Segoe UI", 12, "bold"), fg_color=self.BLUE,
            text_color=self.TEXT, corner_radius=11, cursor="hand2",
        )
        _HoverHint(icon, text)
        return icon

    def _summary(
        self,
        parent: ctk.CTkFrame,
        title: str,
        text: str,
        hint: str,
        *,
        columns: int = 3,
    ) -> ctk.CTkFrame:
        box = ctk.CTkFrame(
            parent, fg_color="#1D232A", corner_radius=8,
            border_width=1, border_color=self.BORDER,
        )
        box.grid(row=0, column=0, columnspan=columns, sticky="ew", padx=5, pady=(4, 6))
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(fill="x", padx=13, pady=6)
        ctk.CTkLabel(
            line, text=title, font=("Segoe UI", 15, "bold"),
            text_color=self.TITLE, anchor="w",
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            line, text=text, font=("Segoe UI", 12),
            text_color=self.TEXT, anchor="w", justify="left", wraplength=650,
        ).pack(side="left")
        self._hint_icon(line, hint).pack(side="left", padx=(10, 0))
        return box

    def _card(
        self, parent: ctk.CTkFrame, title: str, description: str,
        row: int, column: int, *, span: int = 1,
    ) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent, fg_color=self.SURFACE, corner_radius=9,
            border_width=1, border_color=self.BORDER,
        )
        card.grid(
            row=row, column=column, columnspan=span, sticky="nsew",
            padx=6, pady=6,
        )
        card.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(8, 3))
        card._viking_header = header
        ctk.CTkLabel(
            header, text=title, font=("Segoe UI", 15, "bold"),
            text_color=self.TITLE, anchor="w",
        ).pack(side="left")
        if description:
            self._hint_icon(header, description).pack(side="left", padx=(8, 0))
        return card

    def _field(
        self, card: ctk.CTkFrame, label: str, value: Any, hint: str,
        *, variable: tk.StringVar | None = None,
    ) -> ctk.CTkEntry:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=3)
        row.grid_columnconfigure(0, weight=1)
        caption = ctk.CTkLabel(
            row, text=label, width=1, font=FONT_KEY, text_color=self.TITLE,
            anchor="w", justify="left",
        )
        caption.grid(row=0, column=0, sticky="ew")

        def wrap_caption(_event: Any) -> None:
            length = max(40, int(caption.winfo_width() / caption._get_widget_scaling()) - 4)
            if caption.cget("wraplength") != length:
                caption.configure(wraplength=length)

        caption.bind("<Configure>", wrap_caption, add="+")
        entry = ctk.CTkEntry(
            row, width=92, height=34, justify="right",
            font=FONT_VALUE, fg_color="#181B20", border_color="#444B55",
            text_color=self.TEXT, textvariable=variable,
        )
        if variable is None:
            entry.insert(0, str(value))
        entry.grid(row=0, column=1, padx=(8, 5))
        self._hint_icon(row, hint).grid(row=0, column=2)
        return entry

    def _dynamic_field(
        self, card: ctk.CTkFrame, label: str, value: float,
        enabled: bool, hint: str,
    ) -> tuple[ctk.CTkEntry, tk.BooleanVar]:
        """Keep each pre-ARM switch, value and explanation on one compact row."""
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=3)
        row.grid_columnconfigure(1, weight=1)
        toggle = tk.BooleanVar(value=enabled)
        ctk.CTkSwitch(
            row, text="", width=36, variable=toggle, font=("Segoe UI", 11),
            progress_color=self.GREEN, button_color=self.TEXT, text_color=self.TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            row, text=label, font=("Segoe UI", 11),
            text_color=self.TEXT, anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(2, 2))
        entry = ctk.CTkEntry(
            row, width=58, height=32, justify="right", font=("Segoe UI", 12),
            fg_color="#181B20", border_color="#444B55", text_color=self.TEXT,
        )
        entry.insert(0, str(value))
        entry.grid(row=0, column=2, padx=(2, 4))
        self._hint_icon(row, hint).grid(row=0, column=3)
        return entry, toggle

    def _switch(self, card: ctk.CTkFrame, label: str, value: bool, hint: str) -> tk.BooleanVar:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=6)
        row.grid_columnconfigure(0, weight=1)
        variable = tk.BooleanVar(value=value)
        variable._viking_row = row
        ctk.CTkSwitch(
            row, text=label, variable=variable, font=("Segoe UI", 12),
            progress_color=self.GREEN, button_color=self.TEXT, text_color=self.TEXT,
        ).grid(row=0, column=0, sticky="w")
        self._hint_icon(row, hint).grid(row=0, column=1)
        return variable

    def _phase1(self, frame: ctk.CTkFrame) -> None:
        body = self._content(frame)
        self._summary(
            body,
            "VNINDEX · 1D",
            "4 trạng thái thị trường → giới hạn tỷ trọng cổ phiếu",
            "UPTREND: HH + HL và ở trên vùng MA. DOWNTREND: LH + LL và ở dưới vùng MA. "
            "ACCUMULATION/DISTRIBUTION dùng cấu trúc Pivot đi ngang. Volume chỉ xác nhận độ tin cậy, không tự tạo trạng thái.",
        )

        structure = self._card(
            body, "CẤU TRÚC",
            "Nhóm nhận diện xu hướng VNINDEX bằng MA dài hạn, Pivot và số phiên xác nhận.",
            1, 0,
        )
        self.ma_period = self._field(structure, "MA dài hạn", self.params.ma_period, "Mặc định 200. Đường MA dùng xác định bối cảnh dài hạn của VNINDEX.")
        self.pivot_left = self._field(structure, "Pivot trái", self.params.pivot_left, "Mặc định 3 nến bên trái để xác nhận một Pivot.")
        self.pivot_right = self._field(structure, "Pivot phải", self.params.pivot_right, "Mặc định 3 nến bên phải để xác nhận một Pivot.")
        self.pivot_horizontal = self._field(structure, "Pivot ngang (%)", self.params.pivot_horizontal_pct, "Mặc định 1%. Sai số tối đa để coi hai Pivot đang đi ngang.")
        self.ma_zone = self._field(structure, "Vùng MA (%)", self.params.ma_zone_pct, "Mặc định 1%. Vùng đệm quanh MA dài hạn.")
        self.confirm_sessions = self._field(structure, "Xác nhận (phiên)", self.params.confirm_sessions,
            "Số phiên giao dịch liên tiếp cần để đổi trạng thái P1. Mặc định 3, chỉnh được.\n"
            "CHỜ TĂNG 1/3 = ứng viên TĂNG mới đạt 1 phiên, chưa thay trạng thái cũ. "
            "Không phải 3 lần quét và không phải chờ riêng từng lệnh mua.")

        volume = self._card(
            body, "KHỐI LƯỢNG",
            "Volume chỉ tính nhãn độ tin cậy cho trạng thái thị trường; không đổi state và không tạo tín hiệu BUY/SELL.",
            1, 1,
        )
        self.volume_confirmation = self._switch(
            volume, "HIỆN ĐỘ TIN CẬY", self.params.volume_confirmation,
            "Mặc định OFF. ON để tính và hiện nhãn CAO/TRUNG BÌNH/THẤP theo volume; nhãn này không thay đổi quyết định của bot.",
        )
        self.volume_average = self._field(volume, "Trung bình (phiên)", self.params.volume_average_sessions, "Mặc định 20 phiên dùng tính volume trung bình.")
        self.high_volume = self._field(volume, "Volume cao (%)", self.params.high_volume_ratio * 100, "Mặc định 150%. Từ mức này trở lên được coi là volume cao.")
        self.low_volume = self._field(volume, "Volume thấp (%)", self.params.low_volume_ratio * 100, "Mặc định 80%. Dưới mức này được coi là volume thấp.")

        exposure = self._card(
            body, "TỶ TRỌNG TỐI ĐA",
            "Phần trăm NAV tối đa được phép nằm trong cổ phiếu ở từng trạng thái VNINDEX.",
            1, 2,
        )
        self.exp_up = self._field(exposure, "Tăng (%)", self.params.exposure["UPTREND"] * 100, "UPTREND. Mặc định tối đa 90% NAV.")
        self.exp_down = self._field(exposure, "Giảm (%)", self.params.exposure["DOWNTREND"] * 100, "DOWNTREND. Mặc định tối đa 10% NAV.")
        self.exp_acc = self._field(exposure, "Tích lũy (%)", self.params.exposure["ACCUMULATION"] * 100, "ACCUMULATION. Mặc định tối đa 60% NAV.")
        self.exp_dist = self._field(exposure, "Phân phối (%)", self.params.exposure["DISTRIBUTION"] * 100, "DISTRIBUTION. Mặc định tối đa 50% NAV.")

        override = self._card(
            body, "TỶ TRỌNG CHỌN TAY (P1)",
            "BẬT: dùng CP% nhập bên dưới, không chờ VNINDEX xác nhận. Ví dụ CP 100% = P1 không yêu cầu giữ tiền.\n"
            "Tên trạng thái là lựa chọn tay, không phải kết luận VNINDEX. Vẫn phải đủ tiền/hạn mức; không tự bán cổ phiếu.",
            2, 0, span=3,
        )
        override_row = ctk.CTkFrame(override, fg_color="transparent")
        override_row.pack(fill="x", padx=12, pady=(2, 10))
        override_row.grid_columnconfigure(3, weight=1)
        self.market_phase_override_enabled = tk.BooleanVar(
            value=self.settings.market_phase_override_enabled,
        )
        ctk.CTkSwitch(
            override_row, text="BẬT", variable=self.market_phase_override_enabled,
            font=("Segoe UI", 12, "bold"), progress_color=self.WARN,
            text_color=self.TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.market_phase_override = tk.StringVar(
            value=self.settings.market_phase_override,
        )
        ctk.CTkOptionMenu(
            override_row,
            values=["UPTREND", "ACCUMULATION", "DISTRIBUTION", "DOWNTREND"],
            variable=self.market_phase_override, width=170, height=34,
            font=("Segoe UI", 12), fg_color=self.BLUE,
            button_color="#245C92", button_hover_color="#1D4D7B",
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            override_row, text="CP %", font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=0, column=2, sticky="w", padx=(18, 7))
        self.market_phase_override_exposure = ctk.CTkEntry(
            override_row, width=78, height=34, justify="right",
            font=FONT_VALUE, fg_color="#181B20", border_color="#444B55",
            text_color=self.TEXT,
        )
        self.market_phase_override_exposure.insert(
            0, f"{self.settings.market_phase_override_exposure_pct:g}",
        )
        self.market_phase_override_exposure.grid(row=0, column=3, sticky="w")

    def _phase2(self, frame: ctk.CTkFrame) -> None:
        body = self._content(frame, columns=2)
        self._summary(
            body,
            "ENTRY BUY · TÍN HIỆU 1D",
            "Chỉ cấu hình điểm mua tại đây; E · EXIT SELL chỉnh riêng trong tab E/M",
            "BUY mở position mới. EMA BUY tách khỏi EMA SELL. "
            "Chu kỳ RSI hiện dùng chung cho BUY và E; đổi ở một tab sẽ hiện ngay ở tab kia.",
            columns=2,
        )

        signal = self._card(body, "CHỈ BÁO BUY", "Chỉ báo mở vị thế; E có điều khiển riêng ở tab E/M.", 1, 0)
        self.phase2_signal_card = signal

        self.phase2_right_column = body

        def indicator_group(title: str, color: str) -> ctk.CTkFrame:
            group = ctk.CTkFrame(
                signal, fg_color="#1A1E24", corner_radius=7,
                border_width=1, border_color=self.BORDER,
            )
            group.pack(fill="x", padx=12, pady=4)
            ctk.CTkLabel(
                group, text=title, height=22, font=("Segoe UI", 12, "bold"),
                text_color=color, anchor="w",
            ).pack(fill="x", padx=12, pady=(6, 0))
            return group

        buy_group = indicator_group("BUY", self.GREEN)
        self.buy_ema_fast = self._field(buy_group, "EMA nhanh", self.params.buy_ema_fast, "Mặc định EMA3 dùng riêng cho tín hiệu BUY.")
        self.buy_ema_slow = self._field(buy_group, "EMA chậm", self.params.buy_ema_slow, "Mặc định EMA6; phải lớn hơn BUY EMA nhanh.")
        self.buy_signal_ema = tk.BooleanVar(value=self.params.buy_signal_use_ema)
        self.buy_signal_rsi = tk.BooleanVar(value=self.params.buy_signal_use_rsi)
        buy_conditions = ctk.CTkFrame(buy_group, fg_color="transparent")
        buy_conditions.pack(fill="x", padx=12, pady=(3, 8))
        for label, variable in (("DÙNG EMA", self.buy_signal_ema), ("DÙNG RSI", self.buy_signal_rsi)):
            ctk.CTkCheckBox(
                buy_conditions, text=label, variable=variable, width=105,
                font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
                text_color=self.TEXT,
            ).pack(side="left", padx=(0, 12))

        self.buy_signal_require_ema_cross = self._switch(
            buy_group, "BUY CẦN EMA VỪA VƯỢT LÊN", self.params.buy_signal_require_ema_cross,
            "BẬT (mặc định): trước ≤, lần này > mới tạo BUY mới (REALTIME: hai lần quan sát; CLOSED: hai nến ngày).\n"
            "TẮT: EMA nhanh đang > EMA chậm là đạt phần EMA, kể cả lúc bật BOT.\n"
            "RSI vẫn so với phiên trước; giờ mua, vốn và WHIPSAW vẫn kiểm tra. Không áp dụng khi DÙNG EMA tắt; không đổi E/SELL.",
        )

        self.buy_rsi_period = self._field(
            buy_group, "RSI BUY / E", self.params.rsi_period,
            "Backend hiện dùng một chu kỳ RSI cho cả BUY và E. Ô này đồng bộ với ô RSI trong E/M.",
            variable=self.shared_rsi_period,
        )
        self.buy_signal_session_cross_enabled = self._switch(
            buy_group, "GIỮ LẦN CẮT TRONG PHIÊN", self.params.buy_signal_session_cross_enabled,
            "BẬT: nhận lần cắt lên còn hiệu lực trong ngày, kể cả sau restart.\n"
            "Khôi phục bằng giá phút đã đóng; EMA vẫn tính trên nến ngày. Hủy khi EMA xuống hoặc hết phiên.\n"
            "Một lần cắt chỉ tạo một lệnh. RSI, giờ mua và vốn vẫn kiểm tra.\n"
            "Chỉ dùng với REALTIME + DÙNG EMA + BUY CẦN EMA VỪA VƯỢT LÊN. TẮT: giữ cách bắt cắt mới.",
        )

        mode = self._card(
            body, "NẾN & GIỜ MUA",
            "REALTIME dùng nến đang chạy; CLOSED chỉ dùng nến đã đóng. Khung giờ chỉ chặn BUY, không chặn SELL/SL.",
            1, 1,
        )
        self.phase2_mode_card = mode
        self.signal_mode = tk.StringVar(value=self.settings.signal_mode)
        mode_row = ctk.CTkFrame(mode, fg_color="transparent")
        mode_row.pack(fill="x", padx=12, pady=6)
        mode_row.grid_columnconfigure(0, weight=1)
        menu = ctk.CTkOptionMenu(
            mode_row, values=["REALTIME", "CLOSED"], variable=self.signal_mode,
            height=36, font=("Segoe UI", 12), fg_color=self.BLUE,
            button_color="#245C92", button_hover_color="#1D4D7B",
            command=lambda _value: self._refresh_realtime_interval_state(),
        )
        menu.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            mode,
            text="REALTIME  ·  phản ứng trong phiên\nCLOSED    ·  chờ nến ngày đóng",
            justify="left", anchor="w", font=("Segoe UI", 12), text_color=self.TEXT,
        ).pack(fill="x", padx=12, pady=(8, 10))

        interval_row = ctk.CTkFrame(mode, fg_color="transparent")
        interval_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(
            interval_row, text="NHỊP EMA/RSI", font=FONT_KEY,
            text_color=self.TITLE,
        ).pack(side="left")
        self.realtime_indicator_interval = tk.StringVar(
            value=self.settings.realtime_indicator_interval,
        )
        self.realtime_interval_menu = ctk.CTkOptionMenu(
            interval_row, values=["TICK", "1M", "2M", "5M"],
            variable=self.realtime_indicator_interval, width=105, height=32,
            font=("Segoe UI", 12), fg_color=self.BLUE,
            button_color="#245C92", button_hover_color="#1D4D7B",
        )
        self.realtime_interval_menu.pack(side="right", padx=(8, 0))
        self._hint_icon(
            interval_row,
            "EMA/RSI vẫn tính trên nến 1D. TICK dùng giá mới nhất; 1M/2M/5M chỉ "
            "nhận giá cuối của bucket vừa hoàn thành. SL, TP và bảo vệ giá luôn chạy theo tick.",
        ).pack(side="right")
        self._refresh_realtime_interval_state()

        self.buy_window_enabled = tk.BooleanVar(value=self.params.buy_window_enabled)
        window_row = ctk.CTkFrame(mode, fg_color="transparent")
        window_row.pack(fill="x", padx=12, pady=(7, 10))
        ctk.CTkSwitch(
            window_row, text="CHỈ MUA TỪ", variable=self.buy_window_enabled,
            font=("Segoe UI", 12, "bold"), progress_color=self.GREEN,
            text_color=self.TEXT,
        ).pack(side="left")
        for _label, key in (("GIỜ", "start"),):
            entry = ctk.CTkEntry(window_row, width=65, height=32, font=("Segoe UI", 12))
            entry.insert(0, getattr(self.params, f"buy_window_{key}"))
            entry.pack(side="left", padx=(10, 7))
            setattr(self, f"buy_window_{key}", entry)
        self._hint_icon(
            window_row,
            "Mặc định OFF, giờ bắt đầu 14:00 (giờ Việt Nam), chỉnh được. BUY từ giờ này đến hết phiên hợp lệ của sàn. "
            "Không có giờ kết thúc tự đặt; quyền ATO/ATC vẫn theo cấu hình thực thi. "
            "Tín hiệu trước giờ được nhớ trong ngày; EMA/RSI mất điều kiện thì hủy. "
            "Nếu bật xác nhận X phút, bắt đầu đếm khi vào khung giờ. SELL/SL không bị giới hạn. "
            "Lệnh chưa gửi hết hạn khi hết khung; lệnh đã gửi sàn vẫn theo cơ chế khớp của sàn.",
        ).pack(side="left")

        confirmation = self._card(
            body, "XÁC NHẬN BUY",
            "Sau tín hiệu BUY, hệ thống kiểm tra các điều kiện EMA/RSI đã chọn tại mỗi lần quan sát cho đến đủ X phút giao dịch. SELL và SL không chờ.",
            2, 0,
        )
        self.phase2_confirmation_card = confirmation
        row = ctk.CTkFrame(confirmation, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(2, 6))
        self.buy_confirmation_enabled = tk.BooleanVar(value=self.params.buy_confirmation_enabled)
        self.buy_confirmation_ema = tk.BooleanVar(value=self.params.buy_confirmation_require_ema)
        self.buy_confirmation_rsi = tk.BooleanVar(value=self.params.buy_confirmation_require_rsi)
        ctk.CTkSwitch(
            row, text="BẬT", variable=self.buy_confirmation_enabled,
            font=("Segoe UI", 12, "bold"), progress_color=self.GREEN,
            text_color=self.TEXT,
        ).pack(side="left")
        self.buy_confirmation_minutes = ctk.CTkEntry(
            row, width=72, height=32, justify="right", font=("Segoe UI", 12),
            fg_color="#181B20", border_color="#444B55", text_color=self.TEXT,
        )
        self.buy_confirmation_minutes.insert(0, str(self.params.buy_confirmation_minutes))
        self.buy_confirmation_minutes.pack(side="left", padx=(12, 5))
        ctk.CTkLabel(row, text="PHÚT", font=("Segoe UI", 12), text_color=self.TEXT).pack(side="left")

        conditions = ctk.CTkFrame(confirmation, fg_color="transparent")
        conditions.pack(fill="x", padx=12, pady=(2, 12))
        for label, variable in (("EMA", self.buy_confirmation_ema), ("RSI", self.buy_confirmation_rsi)):
            ctk.CTkCheckBox(
                conditions, text=label, variable=variable, width=68,
                font=("Segoe UI", 12, "bold"), fg_color=self.GREEN,
                text_color=self.TEXT,
            ).pack(side="left", padx=(0, 12))

        buy_volume = self._card(
            body, "VOLUME ENTRY",
            "Mặc định OFF. Khi ON, BUY chỉ được qua nếu volume tích lũy của nến hiện tại đạt tỷ lệ tối thiểu so với trung bình các phiên đã đóng trước đó. Không dùng dữ liệu tương lai.",
            2, 1,
        )
        self.buy_volume_enabled = self._switch(
            buy_volume, "DÙNG VOLUME", self.params.buy_volume_enabled,
            "OFF không ảnh hưởng chiến lược hiện tại. ON biến volume thành điều kiện bắt buộc trước khi tạo BUY.",
        )
        self.buy_volume_average = self._field(
            buy_volume, "Trung bình (phiên)", self.params.buy_volume_average_sessions,
            "Số phiên ngày đã đóng dùng làm mẫu. Mặc định 20.",
        )
        self.buy_volume_minimum = self._field(
            buy_volume, "Tối thiểu (%)", self.params.buy_volume_min_ratio * 100.0,
            "100% nghĩa volume hiện tại phải ít nhất bằng trung bình các phiên trước. Nếu chưa đủ lịch sử, BUY bị chặn rõ lý do.",
        )
    def _phase3(self, frame: ctk.CTkFrame) -> None:
        body = self._content(frame)
        self._summary(
            body,
            "VỐN & BẢO VỆ",
            "Giới hạn vốn, vị thế và các khóa BUY an toàn",
            "WIN/LOSS chỉ tính khi trade đóng hoàn toàn và đã trừ phí. Whipsaw và khóa LOSS chỉ chặn BUY/Re-entry mới; không tắt SL hoặc E/M của position đang giữ.",
        )

        capital = self._card(body, "VỐN", "Exposure ở Phase 1 vẫn là trần tổng danh mục. Nhóm này giới hạn số position và cách tái sử dụng vốn theo mã.", 1, 0)
        self.max_positions = self._field(capital, "Tối đa mã BOT", self.params.max_positions,
            "Tổng số mã BOT, gồm vị thế và BUY đang chờ. Priority giữ slot bên trong tổng này.\n"
            "Ví dụ tối đa 5, có 2 Priority → mã thường dùng tối đa 3 slot. MANUAL không chiếm slot BOT.")
        self.no_compound = self._switch(
            capital, "KHÔNG COMPOUND", self.params.no_compound_enabled,
            "ON: vốn 100, lời thành 108 thì lần sau tối đa 100; lỗ còn 97 thì lần sau tối đa 97. OFF: chia lại theo NAV.",
        )
        self.force_min_lot = self._switch(
            capital, "AUTO 100 CP", self.params.force_min_lot_enabled,
            "ON: tính theo exposure trước. Nếu vốn/mã không đủ một lô, BOT có thể fallback sang 100 CP. "
            "Lệnh được vượt phần chia theo slot nhưng không vượt room Phase 1, vốn no-compound hoặc cash gồm phí.",
        )
        self.manual_sell_pause = self._field(
            capital,
            "BUY sau bán tay (phút)",
            self.settings.manual_sell_pause_minutes,
            "Khi một lệnh SELL nguồn MANUAL khớp, BOT khóa tạo và gửi BUY mới trong số phút này. "
            "Mặc định 15; nhập 0 để tắt. SELL/SL/PROTECT, quản lý vị thế và lệnh MANUAL vẫn chạy. "
            "Hết giờ BOT tự mở lại, không cần bật lại nút BOT.",
        )

        # Hai mức SL nằm ở tab E/M để đứng cùng các cách thoát vị thế.
        stops = self._card(body, "KHÓA SAU LỖ",
            "Chỉ khóa BOT mua thêm mã vừa lỗ liên tiếp; REAL/PAPER tính riêng.\n"
            "Mua tay và các cách bán vẫn hoạt động. Không phải nút bật/tắt BOT.", 1, 1)
        self.loss_lock = self._field(stops, "Lỗ liên tiếp", self.params.loss_lock_count,
            "Ví dụ 3: đóng 3 vị thế lỗ liên tiếp sau phí của cùng mã thì khóa BOT mua mã đó.\n"
            "Một vị thế có nhiều lần khớp vẫn chỉ tính một lần lỗ. Có lãi thì reset chuỗi đếm, không mở khóa tay.")
        self.loss_lock_hours = self._field(
            stops,
            "Khóa trong (giờ)",
            self.params.loss_lock_hours,
            "THEO GIỜ: BOT được mua lại sau số giờ này, tính từ lần đóng lỗ đủ ngưỡng.\n"
            "Có tính đêm và ngày nghỉ. KHÓA HẲN giữ khóa đến khi mở tay, không dùng số giờ.",
        )
        lock_mode_row = ctk.CTkFrame(stops, fg_color="transparent")
        lock_mode_row.pack(fill="x", padx=12, pady=6)
        lock_mode_row.grid_columnconfigure(0, weight=1)
        self.loss_block = tk.BooleanVar(value=self.params.loss_lock_mode == "BLOCK")
        self.loss_lock_mode_selector = ctk.CTkSegmentedButton(
            lock_mode_row, values=["THEO GIỜ", "KHÓA HẲN"], height=28,
            font=("Segoe UI", 12, "bold"), selected_color=self.BLUE,
            selected_hover_color="#245C92", unselected_color="#3A3F47",
            unselected_hover_color="#4B515B",
            command=lambda value: self.loss_block.set(value == "KHÓA HẲN"),
        )
        self.loss_lock_mode_selector.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._hint_icon(lock_mode_row,
            "THEO GIỜ: hết số giờ đã đặt thì BOT được mua lại.\n"
            "KHÓA HẲN: khóa đến khi chọn mã và bấm MỞ KHÓA, kể cả sau khi khởi động lại.\n"
            "Đổi sang THEO GIỜ không xóa khóa hẳn đã có; các mã đó vẫn phải mở tay."
        ).grid(row=0, column=1)

        def refresh_lock_hours(*_args: Any) -> None:
            blocked = self.loss_block.get()
            self.loss_lock_hours.configure(state="disabled" if blocked else "normal")
            self.loss_lock_mode_selector.set("KHÓA HẲN" if blocked else "THEO GIỜ")
            if blocked:
                self.loss_lock_hours.master.pack_forget()
            else:
                self.loss_lock_hours.master.pack(fill="x", padx=12, pady=3, before=lock_mode_row)

        self._setting_traces.append((self.loss_block, self.loss_block.trace_add("write", refresh_lock_hours)))
        refresh_lock_hours()
        if self.trade_state:
            row = ctk.CTkFrame(stops, fg_color="transparent")
            self.loss_unlock_row = row
            row.pack(fill="x", padx=12, pady=6)
            row.grid_columnconfigure(1, weight=1)
            self.block_book = tk.StringVar(value="PAPER" if self.settings.paper_mode else "REAL")
            ctk.CTkOptionMenu(row, values=["REAL", "PAPER"], variable=self.block_book, width=82,
                              command=lambda _value: self._refresh_blocks()).grid(row=0, column=0, sticky="ew", padx=(0, 4))
            self.block_symbol = ctk.CTkOptionMenu(row, values=["Không có mã"], width=110)
            self.block_symbol.grid(row=0, column=1, sticky="ew", padx=(4, 0))
            self.block_unlock = ctk.CTkButton(row, text="MỞ KHÓA MÃ", width=90, command=self._unlock_block)
            self.block_unlock.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
            _HoverHint(self.block_unlock,
                "Chọn sổ REAL/PAPER rồi chọn mã cần mở khóa tay.\n"
                "Không có mã: sổ này chưa có khóa tay. Khóa theo giờ tự hết, không hiện ở đây.")
            self._refresh_blocks()

        whip = self._card(body, "WHIPSAW", "Bộ chống nhiễu trước entry. Không thuộc E/M và không can thiệp position đang giữ.", 1, 2)
        self.whipsaw_enabled = self._switch(
            whip, "BẬT WHIPSAW", self.params.whipsaw_enabled,
            "Nếu cặp EMA BUY crossover đạt N lần trong X phiên, khóa BUY/Re-entry mới; position đang giữ vẫn hoạt động.",
        )
        self.whipsaw_n = self._field(whip, "Số lần (N)", self.params.whipsaw_n, "Mặc định 3 lần crossover trong cửa sổ X sẽ khóa BUY/Re-entry.")
        self.whipsaw_x = self._field(whip, "Cửa sổ (phiên)", self.params.whipsaw_x, "Mặc định 7 phiên dùng để đếm số crossover.")

    def _exit_manager_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        body = self._content(frame, columns=2)
        # The SL/TP and PROTECT headers already describe the page.
        # Avoid a third banner that pushes the useful controls below the fold.
        take = self._card(
            body, "SL / TP · BÁN 100%",
            "Cả hai đều bán sạch vị thế. SL của BOT có thể bật/tắt ở tab THỰC THI và được lưu theo từng trade; TP chỉ chạy khi trade có gắn TP.",
            1, 0, span=2,
        )
        limits = ctk.CTkFrame(take, fg_color="transparent")
        limits.pack(fill="x", padx=4, pady=(0, 6))
        limits.grid_columnconfigure((0, 1, 2), weight=1, uniform="exit-limits")
        limit_cells = []
        for column in range(3):
            cell = ctk.CTkFrame(limits, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="nsew")
            limit_cells.append(cell)
        self.initial_sl = self._field(
            limit_cells[0], "SL đầu (%)", self.params.initial_sl_pct,
            "Mặc định -3.5% tính từ giá vốn, kiểm tra realtime khi SL của trade đang ON.",
        )
        self.reentry_sl = self._field(
            limit_cells[1], "SL vào lại (%)", self.params.reentry_sl_pct,
            "Mặc định -2.5%, áp dụng cho lần vào lại sau một lệnh LOSS của cùng chu kỳ.",
        )
        self.take_profit = self._field(
            limit_cells[2], "TP (%)", self.params.take_profit_pct,
            "Mặc định +7% tính từ giá vốn. Đặt 0 để tắt hẳn.",
        )

        self.protect_card = self._card(
            body, "PROTECT · BẢO VỆ & THOÁT",
            "Ba cách bảo vệ nằm cùng khung: sau ARM, Dynamic trước ARM, và tín hiệu E.\n"
            "PROTECT/Dynamic dùng chung MODE và KL bán. E có MODE riêng, AUTO bán 100% và không đợi ARM.\n"
            "Công tắc E/M của từng vị thế quyết định có dùng PROTECT/E hay không.",
            2, 0, span=2,
        )
        groups = ctk.CTkFrame(self.protect_card, fg_color="transparent")
        groups.pack(fill="x", padx=4, pady=(0, 6))
        groups.grid_columnconfigure((0, 1, 2), weight=1, uniform="protect-groups")
        self.exit_left_column = groups
        normal = self._card(
            groups, "SAU ARM",
            "Đạt mức lãi ARM → theo dõi đỉnh → giá lùi TRAIL% từ đỉnh thì chạm PROTECT.\n"
            "Mua 100; ARM 7%; đỉnh 110; TRAIL 2,5% → chạm mức 107,25. MFE là lãi đỉnh từng đạt.",
            0, 0,
        )
        self.protect_trail_card = normal
        self.normal_arm = self._field(normal, "ARM (%)", self.params.normal_arm_pct,
            "MFE ≥ ARM mới bật bảo vệ sau ARM. Mua 100, ARM 7% → từng lên ít nhất 107; không bán ngay tại 107.")
        self.normal_giveback = self._field(normal, "Lùi đỉnh (%)", self.params.normal_giveback_pct,
            "TRAIL: giảm X% từ giá đỉnh → chạm PROTECT.\nĐỉnh 110, lùi 2,5% → 107,25; không trừ từ giá mua.")
        policy_row = ctk.CTkFrame(normal, fg_color="transparent")
        policy_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(
            policy_row, text="MODE", font=FONT_KEY, text_color=self.TITLE,
        ).pack(side="left")
        self.normal_policy = tk.StringVar(value=self.params.normal_policy)
        ctk.CTkOptionMenu(
            policy_row, values=["AUTO", "ALERT"],
            variable=self.normal_policy, width=95, height=34,
            font=FONT_VALUE, fg_color=self.BLUE,
            button_color="#245C92", button_hover_color="#1D4D7B",
            command=self._select_normal_policy,
        ).pack(side="right")
        self._hint_icon(
            policy_row,
            "Dùng chung cho PROTECT sau ARM và Dynamic trước ARM.\n"
            "AUTO: chạm mức thì tạo SELL theo KL bán bên dưới. ALERT: chỉ ghi nhận, không bán.\n"
            "Telegram bật riêng tại KẾT NỐI → TELEGRAM; không bảo đảm khớp đúng giá chạm.",
        ).pack(side="right", padx=(0, 6))
        self.normal_sell = self._field(
            normal, "KL bán (%)", self.params.normal_sell_pct,
            "Phần trăm số cổ còn được bot quản lý, không phải % lãi hoặc % giá. Áp dụng PROTECT và Dynamic ở MODE AUTO.\n"
            "1.000 CP: 100% → bán hết 1.000; 50% → bán 500. Làm tròn lô, tối thiểu 100 CP.\n"
            "Chưa đủ cổ được phép bán thì xử lý theo SELL CHỜ T+; E/SL/TP vẫn bán 100%, không dùng tỷ lệ này.",
        )
        self.normal_repeat = self._switch(
            normal, "LẶP BÁN", self.params.normal_repeat_enabled,
            "REPEAT chỉ có tác dụng khi KL bán dưới 100%. OFF: PROTECT/Dynamic tác động một lần trong chu kỳ.\n"
            "ON: phần còn lại được bảo vệ tiếp khi có đỉnh mới đủ cao theo TRAIL.\n"
            "KL bán 100% đã bán hết → không còn cổ để lặp. E/SL/TP không dùng REPEAT.",
        )
        self.repeat_switch = next(child for child in self.normal_repeat._viking_row.winfo_children()
                                  if isinstance(child, ctk.CTkSwitch))
        ctk.CTkLabel(normal, text="Đạt ARM → theo đỉnh → chạm mức.\nMODE / KL bán dùng cả Dynamic.",
                    font=("Segoe UI", 11), text_color=self.MUTED, justify="left", anchor="w",
                    wraplength=230).pack(fill="x", padx=12, pady=(0, 4))

        dynamic_box = self._card(
            groups, "DYNAMIC",
            "TRƯỚC ARM: ON bảo vệ bằng ATR và/hoặc phần lãi giữ lại; OFF chỉ đợi ARM.\n"
            "Chạm mức → dùng CHẠM MỨC và KL bán ở nhóm SAU ARM. Không thay SL, E hoặc trail sau ARM.",
            0, 1,
        )
        self.dynamic_settings_card = dynamic_box
        dynamic_header = dynamic_box._viking_header
        self.normal_dynamic = tk.BooleanVar(value=self.params.normal_dynamic_enabled)
        ctk.CTkSwitch(
            dynamic_header, text="", width=36, variable=self.normal_dynamic,
            font=("Segoe UI", 12, "bold"), progress_color=self.GREEN,
            button_color=self.TEXT, text_color=self.TEXT,
        ).pack(side="left", before=next(child for child in dynamic_header.winfo_children()
                                        if isinstance(child, ctk.CTkLabel)), padx=(0, 6))
        dynamic_groups = ctk.CTkFrame(dynamic_box, fg_color="transparent")
        dynamic_groups.pack(fill="x", padx=8, pady=(4, 2))
        dynamic_groups.grid_columnconfigure(0, weight=1)

        atr_box = ctk.CTkFrame(
            dynamic_groups, fg_color="#20252C", corner_radius=7,
            border_width=1, border_color=self.BORDER,
        )
        atr_box.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.dynamic_atr_card = atr_box
        ctk.CTkLabel(
            atr_box, text="ATR14 · PHIÊN TRƯỚC",
            font=("Segoe UI", 11, "bold"), text_color="#60A5FA", anchor="w",
        ).pack(fill="x", padx=12, pady=(6, 2))
        self.normal_atr_activation_multiplier, self.normal_atr_activation_enabled = self._dynamic_field(
            atr_box, "BẮT ĐẦU ×", self.params.normal_atr_activation_multiplier,
            self.params.normal_atr_activation_enabled,
            "ON: bắt đầu Dynamic khi MFE ≥ ATR% × hệ số. ATR14 lấy phiên ngày đã đóng gần nhất.\n"
            "ATR 4%, START ×0,55 → từng lãi 2,2%. OFF: bỏ ngưỡng ATR, vẫn cần từng có lãi.",
        )
        self.normal_atr_multiplier, self.normal_atr_trail_enabled = self._dynamic_field(
            atr_box, "LÙI ĐỈNH ×", self.params.normal_atr_multiplier,
            self.params.normal_atr_trail_enabled,
            "ON: khoảng lùi từ đỉnh = ATR% × hệ số.\nATR 4%, TRAIL ×0,8 → lùi 3,2% từ đỉnh. "
            "OFF: bỏ trail ATR; GIỮ LÃI vẫn dùng nếu bật.",
        )

        retention_box = ctk.CTkFrame(
            dynamic_groups, fg_color="#20252C", corner_radius=7,
            border_width=1, border_color=self.BORDER,
        )
        retention_box.grid(row=1, column=0, sticky="ew")
        self.dynamic_retention_card = retention_box
        ctk.CTkLabel(
            retention_box, text="GIỮ LÃI THEO ĐỈNH",
            font=("Segoe UI", 11, "bold"), text_color="#60A5FA", anchor="w",
        ).pack(fill="x", padx=12, pady=(6, 2))
        self.normal_retention_pct, self.normal_retention_enabled = self._dynamic_field(
            retention_box, "GIỮ LÃI (%)", self.params.normal_retention_pct,
            self.params.normal_retention_enabled,
            "ON: giữ X% phần lãi tại đỉnh, không phải X% giá cổ phiếu.\n"
            "Mua 100, đỉnh 104, giữ 90% → mức bảo vệ 103,6. Nếu trail ATR cũng bật, dùng mức cao hơn.",
        )
        self.normal_retention_until_pct, self.normal_retention_until_enabled = self._dynamic_field(
            retention_box, "TỚI MFE (%)", self.params.normal_retention_until_pct,
            self.params.normal_retention_until_enabled,
            "ON: chỉ nâng mức GIỮ LÃI khi MFE dưới ngưỡng này; mức đã lưu không hạ.\n"
            "MFE 5% = mua 100, từng lên 105. OFF: nâng GIỮ LÃI tới trước ARM. Không tác dụng khi GIỮ LÃI tắt.",
        )
        indicator = self._card(
            groups, "E · BÁN 100%",
            "OFF nằm ở công tắc E/M của từng trade. Khi E được bật: ALERT chỉ ghi nhận; AUTO bán toàn bộ phần còn lại. "
            "EMA SELL chỉnh riêng tại đây; chu kỳ RSI hiện dùng chung với BUY. "
            "E độc lập với ARM và KL bán của PROTECT. Lưu áp dụng cho tài khoản đang chọn, kể cả vị thế đang mở.",
            0, 2,
        )
        self.exit_card = indicator
        indicator_policy_row = ctk.CTkFrame(indicator, fg_color="transparent")
        indicator_policy_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(
            indicator_policy_row, text="MODE", font=FONT_KEY, text_color=self.TITLE,
        ).pack(side="left")
        self.indicator_exit_policy = tk.StringVar(
            value=self.params.indicator_exit_policy
        )
        ctk.CTkOptionMenu(
            indicator_policy_row, values=["ALERT", "AUTO"],
            variable=self.indicator_exit_policy, width=95, height=34,
            font=FONT_VALUE, fg_color=self.BLUE,
            button_color="#245C92", button_hover_color="#1D4D7B",
        ).pack(side="right")
        self._hint_icon(
            indicator_policy_row,
            "ALERT: E vẫn đọc EMA SELL + RSI nhưng không đặt lệnh. Muốn gửi tin, bật E · EXIT ALERT trong TELEGRAM. "
            "AUTO: E bán 100% phần còn lại, độc lập với ARM của PROTECT. "
            "Công tắc THỰC THI chỉ chọn E mặc định cho trade BOT mới; trade đang giữ chỉnh E/M riêng.",
        ).pack(side="right", padx=(0, 6))
        self.sell_ema_fast = self._field(
            indicator, "EMA nhanh", self.params.sell_ema_fast,
            "E chính: EMA nhanh cắt xuống EMA chậm. Tách biệt EMA BUY ở Phase 2.",
        )
        self.sell_ema_slow = self._field(
            indicator, "EMA chậm", self.params.sell_ema_slow,
            "E chính: phải lớn hơn EMA SELL nhanh.",
        )
        self.rsi_period = self._field(
            indicator, "RSI BUY / E", self.params.rsi_period,
            "Chu kỳ RSI hiện dùng chung. Chỉnh ở đây cũng đổi RSI của BUY, và ngược lại.",
            variable=self.shared_rsi_period,
        )
        self.sell_signal_ema = tk.BooleanVar(value=self.params.sell_signal_use_ema)
        self.sell_signal_rsi = tk.BooleanVar(value=self.params.sell_signal_use_rsi)
        sell_conditions = ctk.CTkFrame(indicator, fg_color="transparent")
        sell_conditions.pack(fill="x", padx=12, pady=(3, 7))
        for label, variable in (("EMA", self.sell_signal_ema), ("RSI", self.sell_signal_rsi)):
            ctk.CTkCheckBox(
                sell_conditions, text=label, variable=variable, width=80,
                font=("Segoe UI", 11, "bold"), fg_color="#EF4444",
                text_color=self.TEXT,
            ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(indicator, text="AUTO bán 100% · ALERT chỉ báo.\nE không chờ ARM của PROTECT.",
                    font=("Segoe UI", 11), text_color=self.MUTED, justify="left", anchor="w",
                    wraplength=230).pack(fill="x", padx=12, pady=(0, 4))


    def _refresh_realtime_interval_state(self) -> None:
        if not hasattr(self, "realtime_interval_menu"):
            return
        self.realtime_interval_menu.configure(
            state="normal" if self.signal_mode.get() == "REALTIME" else "disabled",
        )

    def _select_normal_policy(self, value: str) -> None:
        return None

    def _execution_tab(self, frame: ctk.CTkFrame) -> None:
        body = self._content(frame, columns=2)
        self._summary(
            body,
            "THỰC THI",
            "BUY / SELL → KIỂM TRA → GỬI DNSE HOẶC CHỜ TRÊN APP",
            "Tab này chỉ quy định cách bot thực hiện quyết định đã có. Không tạo thêm tín hiệu BUY/SELL, "
            "không thay đổi Phase 1–3 và không tạo safeguard ẩn.",
            columns=2,
        )

        orders = self._card(
            body, "ĐẶT LỆNH",
            "MARKET gửi theo phiên đang được cho phép. LO LOCAL chỉ theo dõi giá trên app; khi giá chạm giới hạn mới gửi một lệnh LO thật lên DNSE.",
            1, 0,
        )
        self.execution_order_mode = tk.StringVar(
            value="LO LOCAL" if self.settings.bot_order_mode == "LO_LOCAL" else "MARKET"
        )
        order_menu = ctk.CTkSegmentedButton(
            orders,
            values=["MARKET", "LO LOCAL"],
            variable=self.execution_order_mode,
            height=36,
            font=("Segoe UI", 12, "bold"),
            selected_color=self.BLUE,
            selected_hover_color="#245C92",
            unselected_color="#343A43",
            unselected_hover_color="#444B55",
        )
        order_menu.pack(fill="x", padx=12, pady=(7, 8))
        self.execution_allow_ato = self._switch(
            orders, "CHO PHÉP ATO", self.settings.allow_ato,
            "BẬT: cho phép gửi trong đợt mở cửa HOSE (09:00–09:15), giá khớp chưa biết trước.\n"
            "TẮT: yêu cầu đang chờ chỉ gửi từ phiên liên tục. Không bảo đảm giá màn hình là giá khớp. "
            "BUY BOT bị bỏ qua không tự trở thành lệnh chờ."
        )
        self.execution_allow_atc = self._switch(
            orders, "CHO PHÉP ATC", self.settings.allow_atc,
            "BẬT: cho phép gửi trong đợt đóng cửa HOSE/HNX (14:30–14:45), giá khớp chưa biết trước.\n"
            "TẮT: không gửi trong ATC. Yêu cầu local vẫn theo hạn/điều kiện của lệnh; "
            "không có nghĩa tín hiệu BUY cũ tự được mua phiên sau."
        )
        self.skip_order_popups = self._switch(
            orders, "BỎ POPUP ĐẶT LỆNH", self.settings.skip_order_popups,
            "BẬT (mặc định): BUY/SELL tay không mở Yes/No hay popup báo lỗi; "
            "xem kết quả ở PREVIEW và log Manual.\n"
            "TẮT: hiện popup; REAL hỏi xác nhận, PAPER không hỏi Yes/No.\n"
            "Không bỏ kiểm tra tiền, hạn mức, khối lượng, OTP và phiên. "
            "Ngoài phiên vẫn xếp lệnh chờ, không gửi ngay DNSE.",
        )

        bot_em = self._card(
            body, "E/M MẶC ĐỊNH",
            "Các nút dưới đây được chụp vào từng trade mới do BOT mở. Đổi cấu hình không sửa vị thế đang giữ; manual quản lý riêng.",
            1, 1,
        )
        enabled_modes = set(self.settings.bot_em_modes)
        self.bot_sl_enabled = self._switch(
            bot_em, "SL", self.settings.bot_sl_enabled,
            "Mặc định ON. OFF chỉ áp dụng cho trade BOT mở sau khi lưu; vị thế đã có không mất SL. "
            "Khi OFF, vị thế chỉ còn TP/PROTECT/E hoặc operator bảo vệ.",
        )
        self.bot_em_vars = {
            "TP": self._switch(
                bot_em, "TP",
                "TP" in enabled_modes,
                "Mặc định OFF. Lãi chạm mức Chốt lời thì bán sạch vị thế ngay; "
                "chạy được cả khi ba tactic kia đều tắt.",
            ),
            "NORMAL": self._switch(
                bot_em, "PROTECT", "NORMAL" in enabled_modes,
                "Mặc định ON. Đạt ngưỡng rồi giảm từ peak thì bán một lần theo tỷ lệ PROTECT đã đặt.",
            ),
            "IND_EXIT": self._switch(
                bot_em, "E · EXIT SELL", "IND_EXIT" in enabled_modes,
                "OFF: bỏ E khỏi trade BOT mới. ON: E chạy theo MODE ALERT/AUTO trong tab E/M; "
                "ALERT không đặt lệnh, AUTO bán 100% phần còn lại.",
            ),
        }

        self.execution_preview = {}
        preview_vars = {"SL": self.bot_sl_enabled, **self.bot_em_vars}
        for key in ("SL", "TP", "NORMAL", "IND_EXIT"):
            row = preview_vars[key]._viking_row
            row.grid_columnconfigure(0, weight=0)
            row.grid_columnconfigure(1, weight=1)
            for child in row.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.grid_configure(column=2)
            label = ctk.CTkLabel(row, text="", width=1, font=("Segoe UI", 12),
                                 text_color=self.MUTED, anchor="w", justify="left", wraplength=270)
            label.grid(row=0, column=1, sticky="ew", padx=8)
            def wrap_preview(_event: Any, widget: ctk.CTkLabel = label) -> None:
                length = max(60, int(widget.winfo_width() / widget._get_widget_scaling()) - 12)
                if widget.cget("wraplength") != length:
                    widget.configure(wraplength=length)
            label.bind("<Configure>", wrap_preview, add="+")
            _HoverHint(label, lambda widget=label: "PREVIEW đọc bản nháp ở tab E/M; cần LƯU để áp dụng.\n" + widget.cget("text"))
            self.execution_preview[key] = label

        costs = self._card(
            body, "PHÍ VÀ THUẾ",
            "Dùng cho hai việc: chừa sẵn tiền phí khi tính khối lượng mua, và tính lãi lỗ "
            "cho tài khoản giấy. Mặc định là biểu phí DNSE.",
            2, 0,
        )
        self.buy_fee = self._field(costs, "Phí mua (%)", self.settings.buy_fee_pct, "Mặc định 0.045% theo biểu phí DNSE.")
        self.sell_fee = self._field(costs, "Phí bán (%)", self.settings.sell_fee_pct, "Mặc định 0.045% theo biểu phí DNSE.")
        self.sell_tax = self._field(costs, "Thuế bán (%)", self.settings.sell_tax_pct, "0.1% theo luật, không đổi được ngoài đời.")

        settlement = self._card(
            body, "SELL CHỜ T+",
            "Áp dụng cho SL, TP, PROTECT và E khi cổ phiếu chưa về đủ để bán; T+2 chỉ sẵn sàng từ phiên chiều.",
            2, 1,
        )
        self.sell_wait_policy = tk.StringVar(
            value="VẪN BÁN" if self.settings.sell_wait_policy == "KEEP" else "KIỂM TRA LẠI"
        )
        ctk.CTkSegmentedButton(
            settlement,
            values=["KIỂM TRA LẠI", "VẪN BÁN"],
            variable=self.sell_wait_policy,
            height=36,
            font=("Segoe UI", 12, "bold"),
            selected_color=self.BLUE,
            selected_hover_color="#245C92",
            unselected_color="#343A43",
            unselected_hover_color="#444B55",
        ).pack(fill="x", padx=12, pady=(7, 10))
        ctk.CTkLabel(
            settlement,
            text="MẶC ĐỊNH · KIỂM TRA LẠI\n"
                 "Cổ về và điều kiện SELL còn đúng → bán\n"
                 "Điều kiện đã mất → bỏ phần đang chờ",
            font=("Segoe UI", 12), text_color=self.TEXT,
            justify="left", anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 12))

        corporate = self._card(
            body, "CHỐT QUYỀN",
            f"Mã được đánh dấu sẽ bị chặn BOT BUY. Nếu đang giữ position, {APP_NAME} chỉ gửi cảnh báo Telegram để operator xử lý; hệ thống không tự SELL.",
            3, 0, span=2,
        )
        self._corporate_draft = [dict(item) for item in self.settings.corporate_actions]
        editor = ctk.CTkFrame(corporate, fg_color="transparent")
        editor.pack(fill="x", padx=12, pady=(4, 6))
        editor.grid_columnconfigure(2, weight=1)
        self.corporate_symbol = ctk.CTkEntry(
            editor, width=105, height=34, placeholder_text="MÃ CK",
            font=("Segoe UI", 12), text_color=self.TEXT,
        )
        self.corporate_symbol.grid(row=0, column=0, padx=(0, 7))
        self.corporate_date = ctk.CTkEntry(
            editor, width=180, height=34, placeholder_text="NGÀY GDKHQ · YYYY-MM-DD",
            font=("Segoe UI", 12), text_color=self.TEXT,
        )
        self.corporate_date.grid(row=0, column=1, padx=(0, 7))
        ctk.CTkButton(
            editor, text="+ THÊM MÃ", width=110, height=34,
            font=("Segoe UI", 12, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._add_corporate_action,
        ).grid(row=0, column=2, sticky="w")
        self.corporate_rows = ctk.CTkFrame(corporate, fg_color="#1A1E24", corner_radius=7)
        self._render_corporate_actions()

    def _execution_preview_values(self) -> dict[str, str]:
        get = lambda entry: entry.get().strip() or "—"
        dynamic = ""
        if self.normal_dynamic.get():
            start = get(self.normal_atr_activation_multiplier) if self.normal_atr_activation_enabled.get() else "OFF"
            trail = get(self.normal_atr_multiplier) if self.normal_atr_trail_enabled.get() else "OFF"
            retain = get(self.normal_retention_pct) if self.normal_retention_enabled.get() else "OFF"
            cutoff = f"{get(self.normal_retention_until_pct)}%" if self.normal_retention_until_enabled.get() else "ARM"
            dynamic = f"\nATR ×{start}/{trail} · giữ {retain + '%' if retain != 'OFF' else retain} tới {cutoff}"
        return {
            "SL": f"SL {get(self.initial_sl)}% / vào lại {get(self.reentry_sl)}% · bán 100%",
            "TP": f"TP +{get(self.take_profit)}% · bán 100%",
            "NORMAL": (f"PROTECT {self.normal_policy.get()} · ARM {get(self.normal_arm)}% · lùi {get(self.normal_giveback)}%"
                       f"\nDYN {'ON' if self.normal_dynamic.get() else 'OFF'} · bán {get(self.normal_sell)}% · REPEAT {'ON' if self._protect_repeat_enabled() else 'OFF'}{dynamic}"),
            "IND_EXIT": (f"E {self.indicator_exit_policy.get()} · {'bán 100%' if self.indicator_exit_policy.get() == 'AUTO' else 'chỉ báo'}\n"
                         f"{f'EMA {get(self.sell_ema_fast)}/{get(self.sell_ema_slow)}' if self.sell_signal_ema.get() else 'EMA OFF'} · "
                         f"{f'RSI{get(self.rsi_period)}' if self.sell_signal_rsi.get() else 'RSI OFF'}"),
        }

    def _protect_repeat_enabled(self) -> bool:
        try:
            return self.normal_repeat.get() and 0 < float(self.normal_sell.get()) < 100.0
        except ValueError:
            return False

    def _refresh_execution_preview(self, *_args: Any) -> None:
        try:
            partial = 0 < float(self.normal_sell.get()) < 100.0
        except ValueError:
            partial = False
        self.repeat_switch.configure(state="normal" if partial else "disabled")
        values = self._execution_preview_values()
        flags = {"SL": self.bot_sl_enabled.get(), **{key: value.get() for key, value in self.bot_em_vars.items()}}
        for key, label in self.execution_preview.items():
            dirty = values[key] != self._saved_execution_preview[key] or flags[key] != self._saved_execution_flags[key]
            label.configure(text=f"PREVIEW{' · CHƯA LƯU' if dirty else ''} · {'ON' if flags[key] else 'OFF'}\n{values[key]}",
                            text_color=self.WARN if dirty else self.MUTED)

    def _install_execution_preview(self) -> None:
        self._saved_execution_preview = self._execution_preview_values()
        self._saved_execution_flags = {"SL": self.bot_sl_enabled.get(), **{key: value.get() for key, value in self.bot_em_vars.items()}}
        self._preview_traces = []
        variables = [self.normal_policy, self.indicator_exit_policy, self.normal_dynamic, self.normal_repeat,
                     self.shared_rsi_period, self.bot_sl_enabled, *self.bot_em_vars.values(),
                     self.normal_atr_activation_enabled, self.normal_atr_trail_enabled,
                     self.normal_retention_enabled, self.normal_retention_until_enabled,
                     self.sell_signal_ema, self.sell_signal_rsi]
        for variable in variables:
            self._preview_traces.append((variable, variable.trace_add("write", self._refresh_execution_preview)))
        for entry in (self.initial_sl, self.reentry_sl, self.take_profit, self.normal_arm, self.normal_giveback,
                      self.normal_sell, self.sell_ema_fast, self.sell_ema_slow,
                      self.normal_atr_activation_multiplier, self.normal_atr_multiplier,
                      self.normal_retention_pct, self.normal_retention_until_pct):
            entry.bind("<KeyRelease>", self._refresh_execution_preview, add="+")
        self._refresh_execution_preview()

    def _add_corporate_action(self) -> None:
        symbol = self.corporate_symbol.get().strip().upper()
        ex_date = self.corporate_date.get().strip()
        if not symbol:
            self.status.configure(text="CHỐT QUYỀN · CHƯA NHẬP MÃ", text_color="#EF4444")
            return
        try:
            parsed = datetime.strptime(ex_date, "%Y-%m-%d").date()
        except ValueError:
            self.status.configure(text="CHỐT QUYỀN · NGÀY PHẢI CÓ DẠNG YYYY-MM-DD", text_color="#EF4444")
            return
        self._corporate_draft = [
            item for item in self._corporate_draft
            if str(item.get("symbol", "") or "").upper() != symbol
        ]
        self._corporate_draft.append({
            "symbol": symbol,
            "ex_date": parsed.isoformat(),
            "enabled": True,
            "sell_enabled": False,
        })
        self.corporate_symbol.delete(0, "end")
        self.corporate_date.delete(0, "end")
        self._render_corporate_actions()

    def _remove_corporate_action(self, symbol: str) -> None:
        self._corporate_draft = [
            item for item in self._corporate_draft
            if str(item.get("symbol", "") or "").upper() != str(symbol or "").upper()
        ]
        self._render_corporate_actions()

    def _render_corporate_actions(self) -> None:
        for child in self.corporate_rows.winfo_children():
            child.destroy()
        if not self._corporate_draft:
            self.corporate_rows.pack_forget()
            return
        if not self.corporate_rows.winfo_manager():
            self.corporate_rows.pack(fill="x", padx=12, pady=(2, 10))
        for item in sorted(self._corporate_draft, key=lambda row: (str(row.get("ex_date", "")), str(row.get("symbol", "")))):
            symbol = str(item.get("symbol", "") or "").upper()
            row = ctk.CTkFrame(self.corporate_rows, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=3)
            ctk.CTkLabel(
                row, text=f"{symbol}  ·  {item.get('ex_date', '')}",
                font=("Segoe UI", 12, "bold"), text_color=self.TEXT,
            ).pack(side="left")
            ctk.CTkButton(
                row, text="BỎ", width=58, height=26,
                font=("Segoe UI", 11, "bold"), fg_color="#3A3F47",
                hover_color="#EF4444",
                command=lambda value=symbol: self._remove_corporate_action(value),
            ).pack(side="right")

    @staticmethod
    def _number(entry: ctk.CTkEntry, label: str) -> float:
        try:
            return float(str(entry.get()).strip())
        except ValueError as exc:
            raise ValueError(f"{label} phải là số") from exc

    def _nonnegative(self, entry: ctk.CTkEntry, label: str) -> float:
        value = self._number(entry, label)
        if value < 0:
            raise ValueError(f"{label} không được là số âm")
        return value

    def _refresh_blocks(self) -> None:
        values = self.trade_state.loss_blocks(self.block_book.get())
        self.block_symbol.configure(values=values or ["Không có mã"], state="normal" if values else "disabled")
        if self.block_symbol.get() not in values:
            self.block_symbol.set(values[0] if values else "Không có mã")
        self.block_unlock.configure(
            state="normal" if values else "disabled",
            text="MỞ KHÓA MÃ" if values else "CHƯA CÓ MÃ KHÓA TAY",
        )
        # Only show the book/symbol selector when there is a permanent lock
        # to manage. Keep both books reachable even if the selected one is empty.
        if self.trade_state.loss_blocks("REAL") or self.trade_state.loss_blocks("PAPER"):
            self.loss_unlock_row.pack(fill="x", padx=12, pady=6)
        else:
            self.loss_unlock_row.pack_forget()

    def _unlock_block(self) -> None:
        mode, symbol = self.block_book.get(), self.block_symbol.get()
        if symbol not in self.trade_state.loss_blocks(mode):
            self._refresh_blocks()
            return
        if not messagebox.askyesno(
            "Mở khóa BOT mua", f"Cho phép BOT mua lại {symbol} · {mode}?\n"
            "Chuỗi lỗ của mã này sẽ về 0. BOT vẫn cần tín hiệu mua mới.", parent=self.top,
        ):
            return
        unlocked = self.trade_state.unlock_loss_block(symbol, mode)
        self._refresh_blocks()
        self.status.configure(
            text=f"{'ĐÃ MỞ KHÓA' if unlocked else 'KHÓA ĐÃ THAY ĐỔI'} · {symbol} · {mode}",
            text_color=self.GREEN if unlocked else self.WARN,
        )

    def save(self) -> None:
        try:
            ma_period = int(self._number(self.ma_period, "MA dài hạn"))
            pivot_left = int(self._number(self.pivot_left, "Pivot Left"))
            pivot_right = int(self._number(self.pivot_right, "Pivot Right"))
            confirm_sessions = int(self._number(self.confirm_sessions, "Xác nhận state"))
            volume_average = int(self._number(self.volume_average, "Volume Average"))
            buy_volume_average = int(self._number(
                self.buy_volume_average, "Volume Entry Average",
            ))
            buy_volume_minimum = self._nonnegative(
                self.buy_volume_minimum, "Volume Entry tối thiểu",
            )
            override_exposure = self._nonnegative(
                self.market_phase_override_exposure, "Override P1 tỷ trọng",
            )
            buy_ema_fast = int(self._number(self.buy_ema_fast, "BUY EMA nhanh"))
            buy_ema_slow = int(self._number(self.buy_ema_slow, "BUY EMA chậm"))
            sell_ema_fast = int(self._number(self.sell_ema_fast, "SELL EMA nhanh"))
            sell_ema_slow = int(self._number(self.sell_ema_slow, "SELL EMA chậm"))
            rsi_period = int(self._number(self.rsi_period, "RSI"))
            buy_confirmation_minutes = int(self._number(
                self.buy_confirmation_minutes, "Xác nhận BUY",
            ))
            max_positions = int(self._number(self.max_positions, "Tối đa position"))
            if max_positions < len(set(self.settings.priority_symbols)):
                raise ValueError("Tối đa mã BOT phải đủ số mã Priority đã lưu")
            loss_lock = int(self._number(self.loss_lock, "Lỗ liên tiếp"))
            loss_lock_hours = (self.params.loss_lock_hours if self.loss_block.get()
                               else int(self._number(self.loss_lock_hours, "Khóa trong (giờ)")))
            manual_sell_pause = int(self._number(
                self.manual_sell_pause, "Dừng BUY sau bán tay",
            ))
            whipsaw_n = int(self._number(self.whipsaw_n, "Whipsaw N"))
            whipsaw_x = int(self._number(self.whipsaw_x, "Whipsaw X"))
            exposures = {
                "ACCUMULATION": self._number(self.exp_acc, "Exposure Accumulation"),
                "DISTRIBUTION": self._number(self.exp_dist, "Exposure Distribution"),
                "UPTREND": self._number(self.exp_up, "Exposure Uptrend"),
                "DOWNTREND": self._number(self.exp_down, "Exposure Downtrend"),
            }
            initial_sl = self._number(self.initial_sl, "SL lệnh đầu")
            reentry_sl = self._number(self.reentry_sl, "SL Re-entry")
            pivot_horizontal = self._nonnegative(self.pivot_horizontal, "Pivot ngang")
            ma_zone = self._nonnegative(self.ma_zone, "Vùng MA")
            high_volume = self._nonnegative(self.high_volume, "High Volume")
            low_volume = self._nonnegative(self.low_volume, "Low Volume")
            take_profit = self._nonnegative(self.take_profit, "Chốt lời")
            normal_arm = self._nonnegative(self.normal_arm, "Normal kích hoạt")
            normal_giveback = self._nonnegative(self.normal_giveback, "Normal giveback")
            normal_atr_multiplier = self._number(
                self.normal_atr_multiplier, "Hệ số ATR TRAIL PROTECT",
            )
            normal_atr_activation_multiplier = self._number(
                self.normal_atr_activation_multiplier, "Hệ số ATR START PROTECT",
            )
            normal_retention_pct = self._nonnegative(
                self.normal_retention_pct, "Tỷ lệ giữ MFE PROTECT",
            )
            normal_retention_until_pct = self._nonnegative(
                self.normal_retention_until_pct, "Ngưỡng giữ MFE PROTECT",
            )
            normal_sell = self._nonnegative(self.normal_sell, "Protect bán bao nhiêu")
            if min(
                ma_period, pivot_left, pivot_right, confirm_sessions,
                volume_average, buy_volume_average, buy_ema_fast, sell_ema_fast, rsi_period,
                max_positions, loss_lock, loss_lock_hours, whipsaw_n,
            ) < 1:
                raise ValueError("Các chu kỳ và giới hạn phải lớn hơn 0")
            if buy_ema_slow <= buy_ema_fast:
                raise ValueError("BUY EMA chậm phải lớn hơn BUY EMA nhanh")
            if sell_ema_slow <= sell_ema_fast:
                raise ValueError("SELL EMA chậm phải lớn hơn SELL EMA nhanh")
            if whipsaw_x < 2:
                raise ValueError("Whipsaw X phải từ 2 phiên")
            if not 0 <= manual_sell_pause <= 1440:
                raise ValueError("Dừng BUY sau bán tay phải từ 0 đến 1440 phút")
            if any(value < 0 or value > 100 for value in exposures.values()):
                raise ValueError("Exposure phải nằm trong 0–100%")
            if not 0 <= override_exposure <= 100:
                raise ValueError("Override P1 phải nằm trong 0–100%")
            if not 0 < buy_volume_minimum <= 1000:
                raise ValueError("Volume Entry tối thiểu phải lớn hơn 0 và không quá 1000%")
            if initial_sl >= 0 or reentry_sl >= 0:
                raise ValueError("Stop Loss phải là số âm")
            if high_volume < low_volume:
                raise ValueError("High Volume phải lớn hơn hoặc bằng Low Volume")
            if not 0 < normal_sell <= 100:
                raise ValueError("Tỷ lệ bán PROTECT phải lớn hơn 0 và không quá 100%")
            if not 0 < normal_atr_multiplier <= 10:
                raise ValueError("Hệ số ATR TRAIL PROTECT phải lớn hơn 0 và không quá 10")
            if not 0 < normal_atr_activation_multiplier <= 10:
                raise ValueError("Hệ số ATR START PROTECT phải lớn hơn 0 và không quá 10")
            if not 0 <= normal_retention_pct <= 100:
                raise ValueError("Tỷ lệ giữ MFE PROTECT phải từ 0 đến 100%")
            if (
                self.normal_retention_enabled.get()
                and self.normal_retention_until_enabled.get()
                and normal_retention_pct > 0
                and not 0 < normal_retention_until_pct <= normal_arm
            ):
                raise ValueError("Ngưỡng giữ MFE PROTECT phải nằm từ 0 đến ARM")
            if not (self.buy_signal_ema.get() or self.buy_signal_rsi.get()):
                raise ValueError("Tín hiệu BUY phải bật ít nhất EMA hoặc RSI")
            if not (self.sell_signal_ema.get() or self.sell_signal_rsi.get()):
                raise ValueError("Tín hiệu SELL phải bật ít nhất EMA hoặc RSI")
            if not 1 <= buy_confirmation_minutes <= 120:
                raise ValueError("Xác nhận BUY phải từ 1 đến 120 phút")
            if self.buy_confirmation_enabled.get() and not (
                self.buy_confirmation_ema.get() or self.buy_confirmation_rsi.get()
            ):
                raise ValueError("Xác nhận BUY phải chọn ít nhất EMA hoặc RSI")
            if self.buy_confirmation_enabled.get() and self.signal_mode.get() != "REALTIME":
                raise ValueError("Xác nhận BUY theo phút cần CÁCH ĐỌC NẾN = REALTIME")
            window_start = self.buy_window_start.get().strip()
            validate_buy_window(window_start, "15:00")
            if self.buy_window_enabled.get() and self.signal_mode.get() != "REALTIME":
                raise ValueError("Khung giờ mua cần CÁCH ĐỌC NẾN = REALTIME")

            self.params.ma_period = ma_period
            self.params.pivot_left = pivot_left
            self.params.pivot_right = pivot_right
            self.params.pivot_horizontal_pct = pivot_horizontal
            self.params.ma_zone_pct = ma_zone
            self.params.confirm_sessions = confirm_sessions
            self.params.volume_confirmation = bool(self.volume_confirmation.get())
            self.params.volume_average_sessions = volume_average
            self.params.high_volume_ratio = high_volume / 100.0
            self.params.low_volume_ratio = low_volume / 100.0
            self.params.exposure = {key: value / 100.0 for key, value in exposures.items()}
            self.params.buy_ema_fast = buy_ema_fast
            self.params.buy_ema_slow = buy_ema_slow
            self.params.sell_ema_fast = sell_ema_fast
            self.params.sell_ema_slow = sell_ema_slow
            self.params.rsi_period = rsi_period
            self.params.buy_signal_use_ema = bool(self.buy_signal_ema.get())
            self.params.buy_signal_use_rsi = bool(self.buy_signal_rsi.get())
            self.params.buy_signal_require_ema_cross = bool(self.buy_signal_require_ema_cross.get())
            self.params.buy_signal_session_cross_enabled = bool(self.buy_signal_session_cross_enabled.get())
            self.params.buy_volume_enabled = bool(self.buy_volume_enabled.get())
            self.params.buy_volume_average_sessions = buy_volume_average
            self.params.buy_volume_min_ratio = buy_volume_minimum / 100.0
            self.params.sell_signal_use_ema = bool(self.sell_signal_ema.get())
            self.params.sell_signal_use_rsi = bool(self.sell_signal_rsi.get())
            self.params.indicator_exit_policy = self.indicator_exit_policy.get()
            self.params.buy_confirmation_enabled = bool(self.buy_confirmation_enabled.get())
            self.params.buy_confirmation_minutes = buy_confirmation_minutes
            self.params.buy_confirmation_require_ema = bool(self.buy_confirmation_ema.get())
            self.params.buy_confirmation_require_rsi = bool(self.buy_confirmation_rsi.get())
            self.params.buy_window_enabled = bool(self.buy_window_enabled.get())
            self.params.buy_window_start = window_start
            self.settings.signal_mode = self.signal_mode.get()
            self.settings.realtime_indicator_interval = self.realtime_indicator_interval.get()
            self.params.max_positions = max_positions
            self.params.no_compound_enabled = bool(self.no_compound.get())
            self.params.force_min_lot_enabled = bool(self.force_min_lot.get())
            self.params.initial_sl_pct = initial_sl
            self.params.reentry_sl_pct = reentry_sl
            self.params.loss_lock_count = loss_lock
            self.params.loss_lock_hours = loss_lock_hours
            self.params.loss_lock_mode = "BLOCK" if self.loss_block.get() else "TIMED"
            self.settings.manual_sell_pause_minutes = manual_sell_pause
            self.params.whipsaw_enabled = bool(self.whipsaw_enabled.get())
            self.params.whipsaw_n = whipsaw_n
            self.params.whipsaw_x = whipsaw_x
            self.params.take_profit_pct = take_profit
            self.params.normal_policy = self.normal_policy.get()
            self.params.normal_arm_pct = normal_arm
            self.params.normal_giveback_pct = normal_giveback
            self.params.normal_atr_activation_multiplier = normal_atr_activation_multiplier
            self.params.normal_atr_multiplier = normal_atr_multiplier
            self.params.normal_atr_activation_enabled = bool(self.normal_atr_activation_enabled.get())
            self.params.normal_atr_trail_enabled = bool(self.normal_atr_trail_enabled.get())
            self.params.normal_retention_pct = normal_retention_pct
            self.params.normal_retention_until_pct = normal_retention_until_pct
            self.params.normal_retention_enabled = bool(self.normal_retention_enabled.get())
            self.params.normal_retention_until_enabled = bool(self.normal_retention_until_enabled.get())
            self.params.normal_sell_pct = normal_sell
            self.params.normal_dynamic_enabled = bool(self.normal_dynamic.get())
            self.params.normal_repeat_enabled = bool(self.normal_repeat.get())
            self.params.validate()
            # Activation is per trade. These legacy booleans must never act as
            # an invisible global master after the new UI is saved.
            self.settings.bot_order_mode = (
                "LO_LOCAL" if self.execution_order_mode.get() == "LO LOCAL" else "MARKET"
            )
            self.settings.allow_ato = bool(self.execution_allow_ato.get())
            self.settings.allow_atc = bool(self.execution_allow_atc.get())
            self.settings.skip_order_popups = bool(self.skip_order_popups.get())
            self.settings.bot_sl_enabled = bool(self.bot_sl_enabled.get())
            self.settings.bot_em_modes = [
                name for name, variable in self.bot_em_vars.items() if bool(variable.get())
            ]
            self.settings.market_phase_override_enabled = bool(
                self.market_phase_override_enabled.get()
            )
            self.settings.market_phase_override = self.market_phase_override.get()
            self.settings.market_phase_override_exposure_pct = override_exposure
            self.settings.buy_fee_pct = min(5.0, self._nonnegative(self.buy_fee, "Phí mua"))
            self.settings.sell_fee_pct = min(5.0, self._nonnegative(self.sell_fee, "Phí bán"))
            self.settings.sell_tax_pct = min(5.0, self._nonnegative(self.sell_tax, "Thuế bán"))
            self.settings.sell_wait_policy = (
                "KEEP" if self.sell_wait_policy.get() == "VẪN BÁN" else "RECHECK"
            )
            self.settings.corporate_actions = [dict(item) for item in self._corporate_draft]
            self.settings.rule_parameters = self.params.to_dict()
            # Recording has one owner: History. A RULE popup may have been
            # opened before History saved a new schedule, so retain that schedule.
            if (account_root(self.account_id) / "settings.json").exists():
                recording = load_settings(self.account_id)
                for key in ("signal_trace_enabled", "signal_trace_interval_minutes",
                            "signal_trace_start", "signal_trace_end"):
                    setattr(self.settings, key, getattr(recording, key))
            save_settings(self.settings, self.account_id)
            self._saved_execution_preview = self._execution_preview_values()
            self._saved_execution_flags = {"SL": self.bot_sl_enabled.get(), **{key: value.get() for key, value in self.bot_em_vars.items()}}
            self._refresh_execution_preview()
            self.on_saved()
            if self.trade_state:
                self._refresh_blocks()
            self.status.configure(
                text="ĐÃ LƯU · DAEMON TỰ ĐỘNG NHẬN CẤU HÌNH",
                text_color=self.GREEN,
            )
        except (TypeError, ValueError) as exc:
            self.status.configure(text=f"KHÔNG THỂ LƯU · {exc}", text_color="#EF4444")
