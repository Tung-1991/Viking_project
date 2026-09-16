from __future__ import annotations

from datetime import datetime
import tkinter as tk
from typing import Any, Callable

import customtkinter as ctk

from ..config import AppSettings, save_settings
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
    ):
        self.parent = parent
        self.settings, self.account_id, self.on_saved = settings, account_id, on_saved
        self.on_visibility_changed = on_visibility_changed
        self.params = StaticRuleParameters.from_dict(settings.rule_parameters)
        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width, height = min(1080, screen_w - 60), min(720, screen_h - 90)
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 3)
        self.top = _window(parent, "VIKING RULE", f"{width}x{height}+{x}+{y}")
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
        self._phase2(phases.add("PHASE 2 · TÍN HIỆU"))
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
        line.pack(fill="x", padx=13, pady=10)
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
            row=row, column=column, columnspan=span, sticky="new",
            padx=6, pady=6,
        )
        card.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(9, 5))
        ctk.CTkLabel(
            header, text=title, font=("Segoe UI", 15, "bold"),
            text_color=self.TITLE, anchor="w",
        ).pack(side="left")
        if description:
            self._hint_icon(header, description).pack(side="left", padx=(8, 0))
        return card

    def _field(self, card: ctk.CTkFrame, label: str, value: Any, hint: str) -> ctk.CTkEntry:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=4)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            row, text=label, font=FONT_KEY, text_color=self.TITLE, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        entry = ctk.CTkEntry(
            row, width=92, height=34, justify="right",
            font=FONT_VALUE, fg_color="#181B20", border_color="#444B55",
            text_color=self.TEXT,
        )
        entry.insert(0, str(value))
        entry.grid(row=0, column=1, padx=(8, 5))
        self._hint_icon(row, hint).grid(row=0, column=2)
        return entry

    def _switch(self, card: ctk.CTkFrame, label: str, value: bool, hint: str) -> tk.BooleanVar:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=6)
        row.grid_columnconfigure(0, weight=1)
        variable = tk.BooleanVar(value=value)
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
        self.confirm_sessions = self._field(structure, "Xác nhận (phiên)", self.params.confirm_sessions, "Mặc định 3 phiên. Trạng thái ứng viên phải tồn tại đủ số phiên này mới được xác nhận.")

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

    def _phase2(self, frame: ctk.CTkFrame) -> None:
        body = self._content(frame, columns=2, weights=(3, 2))
        self._summary(
            body,
            "TÍN HIỆU 1D",
            "BUY / SELL dùng đúng các chỉ báo đang bật; bật EMA + RSI thì phải đạt cả hai",
            "BUY mở position mới. SELL thoát position khi EXIT SELL được bật cho trade. "
            "Hai cặp EMA điều chỉnh độc lập; RSI dùng chung và có thể bật/tắt riêng cho BUY/SELL.",
            columns=2,
        )

        signal = self._card(body, "CHỈ BÁO", "BUY và SELL dùng hai cặp EMA riêng; RSI dùng chung.", 1, 0)
        self.phase2_signal_card = signal

        right_column = ctk.CTkFrame(body, fg_color="transparent")
        right_column.grid(row=1, column=1, sticky="new")
        right_column.grid_columnconfigure(0, weight=1)
        self.phase2_right_column = right_column

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

        sell_group = indicator_group("SELL", "#EF4444")
        self.sell_ema_fast = self._field(sell_group, "EMA nhanh", self.params.sell_ema_fast, "Mặc định EMA3 dùng riêng cho tín hiệu SELL.")
        self.sell_ema_slow = self._field(sell_group, "EMA chậm", self.params.sell_ema_slow, "Mặc định EMA6; phải lớn hơn SELL EMA nhanh.")
        self.sell_signal_ema = tk.BooleanVar(value=self.params.sell_signal_use_ema)
        self.sell_signal_rsi = tk.BooleanVar(value=self.params.sell_signal_use_rsi)
        sell_conditions = ctk.CTkFrame(sell_group, fg_color="transparent")
        sell_conditions.pack(fill="x", padx=12, pady=(3, 8))
        for label, variable in (("DÙNG EMA", self.sell_signal_ema), ("DÙNG RSI", self.sell_signal_rsi)):
            ctk.CTkCheckBox(
                sell_conditions, text=label, variable=variable, width=105,
                font=("Segoe UI", 11, "bold"), fg_color="#EF4444",
                text_color=self.TEXT,
                command=self._refresh_exit_b_signal,
            ).pack(side="left", padx=(0, 12))

        rsi_group = indicator_group("RSI", "#60A5FA")
        self.rsi_period = self._field(rsi_group, "Chu kỳ", self.params.rsi_period, "Mặc định RSI14; hướng RSI chỉ được xét khi bật RSI cho BUY/SELL.")

        mode = self._card(
            right_column, "CÁCH ĐỌC NẾN & GIỜ MUA",
            "REALTIME dùng nến đang chạy; CLOSED chỉ dùng nến đã đóng. Khung giờ chỉ chặn BUY, không chặn SELL/SL.",
            0, 0,
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
            right_column, "XÁC NHẬN BUY",
            "Sau tín hiệu BUY, hệ thống kiểm tra các điều kiện EMA/RSI đã chọn tại mỗi lần quan sát cho đến đủ X phút giao dịch. SELL và SL không chờ.",
            1, 0,
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

    def _phase3(self, frame: ctk.CTkFrame) -> None:
        body = self._content(frame)
        self._summary(
            body,
            "VỐN & BẢO VỆ",
            f"WIN reset chu kỳ · {self.params.loss_lock_count} LOSS khóa BUY "
            f"{self.params.loss_lock_hours} giờ · position đang giữ vẫn được quản lý",
            "WIN/LOSS chỉ tính khi trade đóng hoàn toàn và đã trừ phí. Whipsaw và khóa LOSS chỉ chặn BUY/Re-entry mới; không tắt SL hoặc E/M của position đang giữ.",
        )

        capital = self._card(body, "VỐN", "Exposure ở Phase 1 vẫn là trần tổng danh mục. Nhóm này giới hạn số position và cách tái sử dụng vốn theo mã.", 1, 0)
        self.max_positions = self._field(capital, "Tối đa position", self.params.max_positions, "Số mã được giữ đồng thời. Mặc định 5.")
        self.no_compound = self._switch(
            capital, "KHÔNG COMPOUND", self.params.no_compound_enabled,
            "ON: vốn 100, lời thành 108 thì lần sau tối đa 100; lỗ còn 97 thì lần sau tối đa 97. OFF: chia lại theo NAV.",
        )
        self.force_min_lot = self._switch(
            capital, "AUTO 100 CP", self.params.force_min_lot_enabled,
            "ON: tính theo exposure trước. Nếu vốn/mã không đủ một lô, BOT có thể fallback sang 100 CP. "
            "Lệnh được vượt phần chia theo slot nhưng không vượt room Phase 1, vốn no-compound hoặc cash gồm phí.",
        )

        # Hai mức SL nằm ở tab E/M để đứng cùng các cách thoát vị thế.
        stops = self._card(body, "KHÓA SAU LỖ", "Bộ chặn entry sau chuỗi lệnh thua. Hai mức cắt lỗ nằm ở tab E/M.", 1, 1)
        self.loss_lock = self._field(stops, "Khóa sau LOSS", self.params.loss_lock_count, "Mặc định 3 LOSS liên tiếp; một WIN reset về 0.")
        self.loss_lock_hours = self._field(
            stops,
            "Mở lại sau (giờ)",
            self.params.loss_lock_hours,
            "Tính theo giờ đồng hồ kể từ lúc đóng lệnh lỗ đủ ngưỡng; có tính đêm, cuối tuần và ngày nghỉ.",
        )

        whip = self._card(body, "WHIPSAW", "Bộ chống nhiễu trước entry. Không thuộc E/M và không can thiệp position đang giữ.", 1, 2)
        self.whipsaw_enabled = self._switch(
            whip, "BẬT CHỐNG NHIỄU", self.params.whipsaw_enabled,
            "Nếu cặp EMA BUY crossover đạt N lần trong X phiên, khóa BUY/Re-entry mới; position đang giữ vẫn hoạt động.",
        )
        self.whipsaw_n = self._field(whip, "Số lần (N)", self.params.whipsaw_n, "Mặc định 3 lần crossover trong cửa sổ X sẽ khóa BUY/Re-entry.")
        self.whipsaw_x = self._field(whip, "Cửa sổ (phiên)", self.params.whipsaw_x, "Mặc định 7 phiên dùng để đếm số crossover.")

    def _exit_manager_tab(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        body = self._content(frame, columns=2)
        self._summary(
            body,
            "E/M · QUẢN LÝ THOÁT",
            "SL luôn bật · TP / PROTECT / E được gắn theo từng trade · thay đổi có hiệu lực ngay",
            "Popup này chỉ đặt tham số global. Tactic nào chạy cho trade nào được chọn ở panel manual hoặc popup QUẢN LÝ VỊ THẾ.",
            columns=2,
        )

        # Cắt lỗ và chốt lời là hai đầu của cùng một quyết định nên đứng chung
        # một thẻ; cắt lỗ không có công tắc vì nó luôn chạy.
        take = self._card(
            body, "CẮT LỖ (SL) VÀ CHỐT LỜI (TP)",
            "Cả hai đều bán sạch vị thế. Cắt lỗ luôn bật cho mọi trade và được xét trước mọi thứ khác. "
            "Chốt lời chỉ chạy với trade có gắn tactic TP.",
            1, 0,
        )
        self.initial_sl = self._field(
            take, "Cắt lỗ lệnh đầu (%)", self.params.initial_sl_pct,
            "Mặc định -3% tính từ giá vốn, kiểm tra realtime. Luôn bật, không tắt được.",
        )
        self.reentry_sl = self._field(
            take, "Cắt lỗ vào lại (%)", self.params.reentry_sl_pct,
            "Mặc định -2.1%, áp dụng cho lần vào lại sau một lệnh LOSS của cùng chu kỳ.",
        )
        self.take_profit = self._field(
            take, "Chốt lời (%)", self.params.take_profit_pct,
            "Mặc định +7% tính từ giá vốn. Đặt 0 để tắt hẳn.",
        )
        ctk.CTkLabel(take, text="KẾT QUẢ  ·  BÁN SẠCH VỊ THẾ  ·  CẮT LỖ ƯU TIÊN TRƯỚC CHỐT LỜI",
                     font=("Segoe UI", 12), text_color=self.TEXT).pack(anchor="w", padx=14, pady=(8, 12))

        normal = self._card(
            body, "PROTECT",
            "AUTO tự bán; ALERT chỉ log và gửi Telegram nếu công tắc thông báo đang bật.",
            1, 1,
        )
        policy_row = ctk.CTkFrame(normal, fg_color="transparent")
        policy_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(
            policy_row, text="MODE", font=FONT_KEY, text_color=self.TITLE,
        ).pack(side="left")
        self.normal_policy = tk.StringVar(value=self.params.normal_policy)
        ctk.CTkOptionMenu(
            policy_row, values=["AUTO", "ALERT"],
            variable=self.normal_policy, width=140, height=34,
            font=FONT_VALUE, fg_color=self.BLUE,
            button_color="#245C92", button_hover_color="#1D4D7B",
            command=self._select_normal_policy,
        ).pack(side="right")
        self._hint_icon(
            policy_row,
            "AUTO đặt lệnh khi chạm PROTECT. ALERT dùng cùng rule nhưng không đặt lệnh.",
        ).pack(side="right", padx=(0, 6))
        self.normal_arm = self._field(normal, "ARM %", self.params.normal_arm_pct, "MFE đạt mức này thì dùng đầy đủ TRAIL đã đặt.")
        self.normal_giveback = self._field(normal, "TRAIL %", self.params.normal_giveback_pct, "Sau ARM, giá kích hoạt khi giảm X% từ peak.")
        self.normal_atr_multiplier = self._field(
            normal, "ATR ×", self.params.normal_atr_multiplier,
            "DYNAMIC dưới ARM: khoảng thở = ATR14 của phiên T−1 × hệ số này.",
        )
        self.normal_sell = self._field(normal, "SELL %", self.params.normal_sell_pct, "Phần trăm khối lượng đang giữ tại mỗi lần PROTECT thực thi.")
        self.normal_dynamic = self._switch(
            normal, "DYNAMIC", self.params.normal_dynamic_enabled,
            "OFF: chờ đạt ARM. ON: dùng ATR14 phiên T−1 dưới ARM và chỉ quản lý khi mức PROTECT cao hơn SL.",
        )
        self.normal_repeat = self._switch(
            normal, "REPEAT", self.params.normal_repeat_enabled,
            "Chỉ dùng khi SELL dưới 100%. Peak mới phải vượt peak lần bán trước thêm ít nhất TRAIL%.",
        )
        self.normal_result = ctk.CTkLabel(normal, text="", font=("Segoe UI", 12), text_color=self.TEXT)
        self.normal_result.pack(anchor="w", padx=14, pady=(8, 12))

        self._refresh_share_labels()
        self.normal_sell.bind("<KeyRelease>", self._refresh_share_labels, add="+")
        self.normal_repeat.trace_add("write", lambda *_args: self._refresh_share_labels())

        indicator = self._card(
            body, "E · EXIT SELL",
            "Khi E được bật cho một trade, tín hiệu SELL xuất hiện sẽ đóng toàn bộ phần cổ phiếu còn lại. "
            "EMA SELL nhanh/chậm và RSI lấy từ setting Phase 2 rồi lưu vào settings.json theo account; daemon tự nhận lại sau khi lưu. "
            "Khối lượng bán 100% mới là rule cố định và không có ô điều chỉnh.",
            2, 0, span=2,
        )
        indicator_facts = ctk.CTkFrame(indicator, fg_color="transparent")
        indicator_facts.pack(fill="x", padx=10, pady=(2, 10))
        for column in range(4):
            indicator_facts.grid_columnconfigure(column, weight=1, uniform="em-facts")
        for column, (title, value) in enumerate((
            ("TÍN HIỆU", self._exit_b_signal_text()),
            ("CHỈNH TẠI", "PHASE 2 · EMA / RSI"),
            ("HÀNH ĐỘNG", "BÁN HẾT PHẦN CÒN LẠI"),
            ("KL BÁN", "100% · CỐ ĐỊNH"),
        )):
            fact = ctk.CTkFrame(indicator_facts, fg_color="#1A1E24", corner_radius=7)
            fact.grid(row=0, column=column, sticky="nsew", padx=3)
            ctk.CTkLabel(
                fact, text=title, font=FONT_KEY,
                text_color=self.TITLE, anchor="w",
            ).pack(fill="x", padx=10, pady=(7, 2))
            value_label = ctk.CTkLabel(
                fact, text=value, font=FONT_VALUE,
                text_color=self.TEXT, anchor="w", justify="left", wraplength=205,
            )
            value_label.pack(fill="x", padx=10, pady=(0, 7))
            if title == "TÍN HIỆU":
                self.exit_b_signal_label = value_label

        for entry in (self.sell_ema_fast, self.sell_ema_slow, self.rsi_period):
            entry.bind("<KeyRelease>", self._refresh_exit_b_signal, add="+")

    def _refresh_share_labels(self, _event: Any = None) -> None:
        """Say in words what the percent box will actually do."""
        for entry, label, fallback in (
            (self.normal_sell, self.normal_result, self.params.normal_sell_pct),
        ):
            if not label.winfo_exists():
                continue
            raw = str(entry.get()).strip()
            try:
                value = min(100.0, max(0.0, float(raw or fallback)))
            except ValueError:
                label.configure(text="KẾT QUẢ  ·  SỐ KHÔNG HỢP LỆ")
                continue
            label.configure(
                text="KẾT QUẢ  ·  BÁN SẠCH VỊ THẾ  ·  REPEAT KHÔNG ÁP DỤNG" if value >= 100
                else (
                    f"KẾT QUẢ  ·  BÁN {value:g}% PHẦN ĐANG GIỮ  ·  "
                    f"REPEAT {'ON' if self.normal_repeat.get() else 'OFF'}"
                ),
            )

    def _refresh_realtime_interval_state(self) -> None:
        if not hasattr(self, "realtime_interval_menu"):
            return
        self.realtime_interval_menu.configure(
            state="normal" if self.signal_mode.get() == "REALTIME" else "disabled",
        )

    def _select_normal_policy(self, value: str) -> None:
        self._refresh_share_labels()

    def _exit_b_signal_text(self) -> str:
        def value(entry: ctk.CTkEntry, fallback: int) -> str:
            current = str(entry.get()).strip()
            return current or str(fallback)

        fast = value(self.sell_ema_fast, self.params.sell_ema_fast)
        slow = value(self.sell_ema_slow, self.params.sell_ema_slow)
        rsi = value(self.rsi_period, self.params.rsi_period)
        conditions: list[str] = []
        if self.sell_signal_ema.get():
            conditions.append(f"EMA{fast} ↓ EMA{slow}")
        if self.sell_signal_rsi.get():
            conditions.append(f"RSI{rsi} GIẢM")
        return "\n".join(conditions) or "CHƯA CHỌN ĐIỀU KIỆN"

    def _refresh_exit_b_signal(self, _event: Any = None) -> None:
        label = getattr(self, "exit_b_signal_label", None)
        if label is not None and label.winfo_exists():
            label.configure(text=self._exit_b_signal_text())

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
            "Đợt ATO chạy 9h00-9h15, gom hết lệnh rồi khớp ở một giá mở cửa duy nhất.\n"
            "BẬT: lệnh chờ gửi vào đợt này, khớp đúng giá mở cửa nhưng không biết trước giá.\n"
            "TẮT: chờ tới sau 9h15 mới đặt, thấy giá nào khớp giá đó.\n"
            "Backtest có ô ĐỢT ATO đối ứng ở tab THAM SỐ, để hai bên giả định giống nhau."
        )
        self.execution_allow_atc = self._switch(
            orders, "CHO PHÉP ATC", self.settings.allow_atc,
            "Đợt ATC chạy 14h30-14h45, gom hết lệnh rồi khớp ở một giá đóng cửa duy nhất.\n"
            "BẬT: lệnh chờ gửi vào đợt này, khớp đúng giá đóng cửa nhưng không biết trước giá.\n"
            "TẮT: bỏ qua đợt này, lệnh chờ sang phiên sau.\n"
            "Backtest không mô phỏng được đợt ATC: tín hiệu chỉ có sau khi nến đóng lúc 14h45."
        )
        self.confirm_real_orders = self._switch(
            orders, "XÁC NHẬN LỆNH REAL", self.settings.confirm_real_orders,
            "ON: lệnh đặt tay ở tài khoản REAL phải xác nhận thêm một lần trước khi gửi. "
            "Không áp dụng cho PAPER và không làm dừng quyết định tự động của daemon.",
        )

        bot_em = self._card(
            body, "E/M MẶC ĐỊNH",
            "BOT và manual dùng chung TP, PROTECT và E. Các nút dưới đây chọn tactic mặc định gắn vào trade do BOT mở; SL luôn bật.",
            1, 1,
        )
        enabled_modes = set(self.settings.bot_em_modes)
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
            "IND_EXIT": self._switch(bot_em, "E · EXIT SELL", "IND_EXIT" in enabled_modes, "Mặc định ON. Có tín hiệu SELL từ EMA/RSI thì bán hết phần còn lại."),
        }

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
            "Mã được đánh dấu sẽ bị chặn BOT BUY. Nếu đang giữ position, Viking chỉ gửi cảnh báo Telegram để operator xử lý; hệ thống không tự SELL.",
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

    def save(self) -> None:
        try:
            ma_period = int(self._number(self.ma_period, "MA dài hạn"))
            pivot_left = int(self._number(self.pivot_left, "Pivot Left"))
            pivot_right = int(self._number(self.pivot_right, "Pivot Right"))
            confirm_sessions = int(self._number(self.confirm_sessions, "Xác nhận state"))
            volume_average = int(self._number(self.volume_average, "Volume Average"))
            buy_ema_fast = int(self._number(self.buy_ema_fast, "BUY EMA nhanh"))
            buy_ema_slow = int(self._number(self.buy_ema_slow, "BUY EMA chậm"))
            sell_ema_fast = int(self._number(self.sell_ema_fast, "SELL EMA nhanh"))
            sell_ema_slow = int(self._number(self.sell_ema_slow, "SELL EMA chậm"))
            rsi_period = int(self._number(self.rsi_period, "RSI"))
            buy_confirmation_minutes = int(self._number(
                self.buy_confirmation_minutes, "Xác nhận BUY",
            ))
            max_positions = int(self._number(self.max_positions, "Tối đa position"))
            loss_lock = int(self._number(self.loss_lock, "Khóa sau LOSS"))
            loss_lock_hours = int(self._number(self.loss_lock_hours, "Mở lại sau"))
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
                self.normal_atr_multiplier, "Hệ số ATR PROTECT",
            )
            normal_sell = self._nonnegative(self.normal_sell, "Protect bán bao nhiêu")
            if min(
                ma_period, pivot_left, pivot_right, confirm_sessions,
                volume_average, buy_ema_fast, sell_ema_fast, rsi_period,
                max_positions, loss_lock, loss_lock_hours, whipsaw_n,
            ) < 1:
                raise ValueError("Các chu kỳ và giới hạn phải lớn hơn 0")
            if buy_ema_slow <= buy_ema_fast:
                raise ValueError("BUY EMA chậm phải lớn hơn BUY EMA nhanh")
            if sell_ema_slow <= sell_ema_fast:
                raise ValueError("SELL EMA chậm phải lớn hơn SELL EMA nhanh")
            if whipsaw_x < 2:
                raise ValueError("Whipsaw X phải từ 2 phiên")
            if any(value < 0 or value > 100 for value in exposures.values()):
                raise ValueError("Exposure phải nằm trong 0–100%")
            if initial_sl >= 0 or reentry_sl >= 0:
                raise ValueError("Stop Loss phải là số âm")
            if high_volume < low_volume:
                raise ValueError("High Volume phải lớn hơn hoặc bằng Low Volume")
            if not 0 < normal_sell <= 100:
                raise ValueError("Tỷ lệ bán PROTECT phải lớn hơn 0 và không quá 100%")
            if not 0 < normal_atr_multiplier <= 10:
                raise ValueError("Hệ số ATR PROTECT phải lớn hơn 0 và không quá 10")
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
            self.params.sell_signal_use_ema = bool(self.sell_signal_ema.get())
            self.params.sell_signal_use_rsi = bool(self.sell_signal_rsi.get())
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
            self.params.whipsaw_enabled = bool(self.whipsaw_enabled.get())
            self.params.whipsaw_n = whipsaw_n
            self.params.whipsaw_x = whipsaw_x
            self.params.take_profit_pct = take_profit
            self.params.normal_policy = self.normal_policy.get()
            self.params.normal_arm_pct = normal_arm
            self.params.normal_giveback_pct = normal_giveback
            self.params.normal_atr_multiplier = normal_atr_multiplier
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
            self.settings.confirm_real_orders = bool(self.confirm_real_orders.get())
            self.settings.bot_em_modes = [
                name for name, variable in self.bot_em_vars.items() if bool(variable.get())
            ]
            self.settings.buy_fee_pct = min(5.0, self._nonnegative(self.buy_fee, "Phí mua"))
            self.settings.sell_fee_pct = min(5.0, self._nonnegative(self.sell_fee, "Phí bán"))
            self.settings.sell_tax_pct = min(5.0, self._nonnegative(self.sell_tax, "Thuế bán"))
            self.settings.sell_wait_policy = (
                "KEEP" if self.sell_wait_policy.get() == "VẪN BÁN" else "RECHECK"
            )
            self.settings.corporate_actions = [dict(item) for item in self._corporate_draft]
            self.settings.rule_parameters = self.params.to_dict()
            save_settings(self.settings, self.account_id)
            self.on_saved()
            self.status.configure(
                text="ĐÃ LƯU · ÁP DỤNG NGAY CẢ VỊ THẾ ĐANG MỞ",
                text_color=self.GREEN,
            )
        except (TypeError, ValueError) as exc:
            self.status.configure(text=f"KHÔNG THỂ LƯU · {exc}", text_color="#EF4444")
