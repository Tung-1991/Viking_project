from __future__ import annotations

import os
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk

from .. import config
from ..config import AppSettings, load_settings, save_settings
from ..dashboard.windows import FONT_KEY, FONT_VALUE, PALETTE, SymbolPicker, _HoverHint, _window
from ..config import update_env
from .dnse.client import DNSEClient
from .telegram import TelegramClient


class ConnectionPopup:
    BG = "#15181D"
    SURFACE = "#22262D"
    SURFACE_2 = "#1B1F25"
    BORDER = "#343A43"
    TEXT = "#E8EBEF"
    TITLE = PALETTE["TITLE"]
    MUTED = "#C5CBD4"
    GREEN = "#22C55E"
    BLUE = "#2B6CB0"
    WARN = "#F59E0B"
    RED = "#EF4444"

    def __init__(
        self,
        parent: ctk.CTk,
        settings: AppSettings,
        account_id: str,
        client: DNSEClient,
        on_saved: Callable[[], None],
        on_apply_account: Callable[[str], None] | None = None,
        on_visibility_changed: Callable[[bool], None] | None = None,
        on_reset_paper: Callable[[float], None] | None = None,
    ):
        self.parent, self.settings, self.account_id = parent, settings, account_id
        self.client, self.on_saved = client, on_saved
        self.on_apply_account = on_apply_account
        self.on_visibility_changed = on_visibility_changed
        self.on_reset_paper = on_reset_paper
        self._tested_account: dict[str, str] | None = None
        self._watchlist_draft = list(settings.watchlist)
        self._holiday_draft = sorted(
            set(settings.custom_holidays).difference(config.DEFAULT_VN_TRADING_HOLIDAYS)
        )

        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width, height = min(860, screen_w - 60), min(560, screen_h - 70)
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 3)
        self.top = _window(parent, "VIKING · KẾT NỐI", f"{width}x{height}+{x}+{y}")
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.tk.call("wm", "transient", self.top._w, "")
        self.top.resizable(True, True)
        self.top.minsize(760, 500)
        self.top.configure(fg_color="#111318")
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
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        try:
            self.tabs._segmented_button.configure(font=("Segoe UI", 12, "bold"), text_color=self.TEXT)
        except AttributeError:
            pass
        self._connection_tab(self.tabs.add("DNSE"))
        self._watchlist_tab(self.tabs.add("MÃ CK"))
        self._telegram_tab(self.tabs.add("TELEGRAM"))
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

    def _post_ui(self, callback: Callable[[], None]) -> None:
        """Render a worker result through the dashboard's Tk callback queue."""
        def safe_callback() -> None:
            if self.top.winfo_exists():
                callback()

        post = getattr(self.parent, "_post_ui", None)
        if callable(post):
            post(safe_callback)

    def _run_shared_io(self, callback: Callable[[], None], name: str) -> None:
        executor = getattr(self.parent, "_io_executor", None)
        if executor is not None:
            executor.submit(callback)
        else:
            threading.Thread(target=callback, name=name, daemon=True).start()

    def _close(self) -> None:
        if self.on_visibility_changed:
            self.on_visibility_changed(False)
        if self.top.winfo_exists():
            self.top.destroy()

    def _body(self, frame: ctk.CTkFrame) -> ctk.CTkScrollableFrame:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", scrollbar_button_color="#343A43",
            scrollbar_button_hover_color="#4B515B",
        )
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        return body

    def _hint_icon(self, parent: Any, text: str) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent,
            text="?",
            width=24,
            height=24,
            corner_radius=12,
            fg_color=self.BLUE,
            hover_color="#245C92",
            font=("Segoe UI", 11, "bold"),
        )
        _HoverHint(button, text)
        return button

    def _card(self, parent: ctk.CTkFrame, title: str, hint: str = "") -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent, fg_color=self.SURFACE, corner_radius=10,
            border_width=1, border_color=self.BORDER,
        )
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            card, text=title, font=("Segoe UI", 15, "bold"),
            text_color=self.TITLE, anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 5))
        if hint:
            button = ctk.CTkButton(
                card, text="?", width=24, height=24, corner_radius=12,
                fg_color=self.BLUE, hover_color="#245C92",
                font=("Segoe UI", 12, "bold"),
            )
            button.grid(row=0, column=2, sticky="e", padx=10, pady=(8, 3))
            _HoverHint(button, hint)
        return card

    def _field(
        self,
        frame: ctk.CTkFrame,
        row: int,
        label: str,
        value: str = "",
        secret: bool = False,
        hint: str = "",
    ) -> ctk.CTkEntry:
        ctk.CTkLabel(
            frame, text=label, anchor="w", font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=4)
        entry = ctk.CTkEntry(
            frame, show="•" if secret else "", height=32,
            fg_color=self.SURFACE_2, border_color=self.BORDER,
            font=FONT_VALUE, text_color=self.TEXT,
        )
        entry.insert(0, value)
        entry.grid(
            row=row, column=1, columnspan=1 if hint else 2, sticky="ew",
            padx=(0, 8 if hint else 12), pady=4,
        )
        if hint:
            button = ctk.CTkButton(
                frame, text="?", width=24, height=24, corner_radius=12,
                fg_color=self.BLUE, hover_color="#245C92",
                font=("Segoe UI", 12, "bold"),
            )
            button.grid(row=row, column=2, padx=(0, 10), pady=4)
            _HoverHint(button, hint)
        return entry

    def _connection_tab(self, frame: ctk.CTkFrame) -> None:
        body = self._body(frame)
        body.grid_columnconfigure(0, weight=1)
        key_ready = bool(os.getenv("DNSE_API_KEY") and os.getenv("DNSE_API_SECRET"))
        # Token persistence remains an internal session choice.  The compact
        # DNSE card is only for API discovery; trading-token controls belong to
        # OTP handling and must not crowd the account result.
        self.save_token_env = tk.BooleanVar(value=bool(os.getenv("DNSE_TRADING_TOKEN", "").strip()))
        credentials = self._card(
            body, "KẾT NỐI DNSE",
            "Nhập API Key/Secret rồi bấm TEST. Viking gọi GET /accounts để kiểm tra và hiển thị "
            "tài khoản CKCS. LƯU ghi API và tài khoản hoạt động vào viking_v2/.env.",
        )
        credentials.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        credentials.grid_columnconfigure(1, weight=1)
        self.connection_summary = ctk.CTkLabel(
            credentials,
            text=(f"API ĐÃ CÓ  ·  TK {self.account_id}" if key_ready else "CHƯA KẾT NỐI"),
            font=("Segoe UI", 12, "bold"),
            text_color=self.GREEN if key_ready else self.WARN, anchor="w",
        )
        self.connection_summary.grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 3))
        self.dnse_key = self._field(
            credentials, 2, "API KEY", os.getenv("DNSE_API_KEY", ""),
        )
        self.dnse_secret = self._field(
            credentials, 3, "API SECRET", os.getenv("DNSE_API_SECRET", ""), True,
        )
        saved_account = str(self.client.account_no or "").strip()
        self.account_info = ctk.CTkFrame(credentials, fg_color=self.SURFACE_2, corner_radius=7)
        self.account_info.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(5, 3))
        self.account_info.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="dnse_account")
        self.account_info_labels: dict[str, ctk.CTkLabel] = {}
        account_values = {
            "ACCOUNT ID": saved_account or "--",
            "CUSTODY": os.getenv("DNSE_CUSTODY_CODE", "") or "--",
            "CHỦ TK": "--",
            "LOẠI": "CKCS" if saved_account else "--",
        }
        for column, (title, value) in enumerate(account_values.items()):
            cell = ctk.CTkFrame(self.account_info, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="nsew", padx=7, pady=4)
            ctk.CTkLabel(
                cell, text=title, font=FONT_KEY, text_color=self.TITLE,
            ).pack(side="left", padx=(0, 8))
            label = ctk.CTkLabel(
                cell, text=value, font=FONT_VALUE, text_color=self.TEXT,
            )
            label.pack(side="left")
            self.account_info_labels[title] = label
        connection_actions = ctk.CTkFrame(credentials, fg_color="transparent")
        connection_actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(3, 2))
        connection_actions.grid_columnconfigure(0, weight=1)
        self.token_bar = ctk.CTkFrame(
            connection_actions, width=390, height=34,
            fg_color=self.SURFACE_2, corner_radius=7,
        )
        self.token_bar.grid(row=0, column=0, sticky="w")
        self.token_bar.grid_propagate(False)
        self.token_bar.grid_columnconfigure(2, minsize=100)
        token_hint = ctk.CTkButton(
            self.token_bar, text="!", width=24, height=24, corner_radius=12,
            font=("Segoe UI", 12, "bold"), fg_color=self.WARN,
            hover_color="#D97706", text_color="#111318",
        )
        token_hint.grid(row=0, column=0, padx=(5, 5), pady=5)
        _HoverHint(
            token_hint,
            "Trading Token DNSE: tắt LƯU để chỉ dùng trong RAM; XÓA bỏ cả .env và RAM.",
        )
        ctk.CTkLabel(
            self.token_bar, text="TOKEN GD", width=82, anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=0, column=1, sticky="w")
        token_ready = self.client.has_trading_token()
        self.token_status = ctk.CTkLabel(
            self.token_bar,
            text=(
                ".ENV" if token_ready and self.save_token_env.get()
                else "RAM" if token_ready else "CHƯA CÓ"
            ),
            width=100, anchor="w", font=FONT_VALUE,
            text_color=self.GREEN if token_ready else self.WARN,
        )
        self.token_status.grid(row=0, column=2, sticky="w")
        self.save_token_switch = ctk.CTkSwitch(
            self.token_bar, text="LƯU", variable=self.save_token_env,
            onvalue=True, offvalue=False, width=82,
            font=("Segoe UI", 11, "bold"), progress_color=self.GREEN,
            command=self._token_storage_changed,
        )
        self.save_token_switch.grid(row=0, column=3, padx=(3, 6), pady=3)
        self.btn_clear_token = ctk.CTkButton(
            self.token_bar, text="XÓA", width=54, height=28,
            font=("Segoe UI", 11, "bold"), fg_color="#3A3F47",
            hover_color="#4B515B", command=self._clear_saved_token,
        )
        self.btn_clear_token.grid(row=0, column=4, padx=(0, 4), pady=3)
        self.btn_load_accounts = ctk.CTkButton(
            connection_actions, text="TEST", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._load_accounts,
        )
        self.btn_load_accounts.grid(row=0, column=1, padx=(0, 6))
        self.btn_save_dnse = ctk.CTkButton(
            connection_actions, text="LƯU", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_dnse,
        )
        self.btn_save_dnse.grid(row=0, column=2)
        self.dnse_status = ctk.CTkLabel(
            credentials, text="Nhập API rồi bấm TEST.",
            height=28, font=("Segoe UI", 12, "bold"), text_color=self.MUTED,
            wraplength=900, justify="left", anchor="w",
        )
        self.dnse_status.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=(1, 8))

        otp_card = self._card(
            body, "XÁC THỰC GIAO DỊCH",
            "Token mặc định chỉ giữ trong RAM. Bật LƯU TOKEN .ENV nếu muốn dùng lại sau khi mở app; "
            "tắt lưu sẽ xóa bản đã lưu nhưng vẫn giữ token của phiên hiện tại. XÓA TOKEN xóa cả file và RAM.",
        )
        otp_card.grid(row=1, column=0, sticky="ew", padx=6, pady=3)
        otp_card.grid_columnconfigure(1, weight=1)
        self.otp_type = tk.StringVar(value="SMART OTP" if self.client.otp_type == "smart_otp" else "EMAIL OTP")
        ctk.CTkLabel(
            otp_card, text="PHƯƠNG THỨC", anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=4)
        ctk.CTkSegmentedButton(
            otp_card, values=["EMAIL OTP", "SMART OTP"], variable=self.otp_type,
            width=280, height=32, selected_color=self.BLUE, selected_hover_color="#245C92",
            unselected_color="#3A3F47", unselected_hover_color="#4B515B",
            font=("Segoe UI", 11, "bold"), command=self._otp_type_changed,
        ).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=4)
        self.otp = self._field(
            otp_card, 2, "MÃ OTP", "", True,
        )
        otp_actions = ctk.CTkFrame(otp_card, fg_color="transparent")
        otp_actions.grid(row=3, column=0, columnspan=3, sticky="ew", padx=12, pady=(4, 8))
        otp_actions.grid_columnconfigure(0, weight=1)
        self.btn_send_otp = ctk.CTkButton(
            otp_actions, text="GỬI EMAIL", width=125, height=32, fg_color="#3A3F47",
            hover_color="#4B515B", font=("Segoe UI", 11, "bold"), command=self._send_otp,
        )
        self.btn_send_otp.grid(row=0, column=1, padx=(0, 6))
        self.btn_verify_otp = ctk.CTkButton(
            otp_actions, text="XÁC THỰC", width=110, height=32, fg_color=self.BLUE,
            hover_color="#245C92", font=("Segoe UI", 11, "bold"), command=self._verify_otp,
        )
        self.btn_verify_otp.grid(row=0, column=2)
        self._otp_type_changed(self.otp_type.get())

        paper_card = self._card(
            body, "PAPER",
            "LƯU đổi vốn mặc định. RESET đưa tài khoản PAPER về số vốn này và xóa trạng thái PAPER hiện tại.",
        )
        paper_card.grid(row=2, column=0, sticky="ew", padx=6, pady=3)
        paper_card.grid_columnconfigure(1, weight=1)
        self.paper_balance = self._field(
            paper_card,
            1,
            "VỐN PAPER",
            f"{self.settings.paper_initial_balance:,.0f}",
        )
        paper_actions = ctk.CTkFrame(paper_card, fg_color="transparent")
        paper_actions.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(5, 3))
        paper_actions.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            paper_actions, text="LƯU", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_paper_balance,
        ).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(
            paper_actions, text="RESET", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color="#3A3F47",
            hover_color="#4B515B", command=lambda: self._save_paper_balance(reset=True),
        ).grid(row=0, column=2)
        self.paper_status = ctk.CTkLabel(
            paper_card, text="", font=("Segoe UI", 11), text_color=self.MUTED, anchor="w",
        )
        self.paper_status.grid(row=3, column=0, columnspan=3, sticky="ew", padx=12, pady=(1, 10))

    @staticmethod
    def _parse_paper_balance(raw: str) -> float:
        text = str(raw or "").strip().replace(" ", "").replace("₫", "").replace("đ", "")
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", text):
            text = text.replace(",", "").replace(".", "")
        else:
            text = text.replace(",", ".")
        value = float(text)
        if value <= 0:
            raise ValueError("Vốn PAPER phải lớn hơn 0")
        return value

    def _save_paper_balance(self, reset: bool = False) -> None:
        try:
            balance = self._parse_paper_balance(self.paper_balance.get())
        except (TypeError, ValueError):
            self.paper_status.configure(text="VỐN PAPER KHÔNG HỢP LỆ", text_color=self.RED)
            return
        if reset and not messagebox.askyesno(
            "Reset PAPER",
            "Xóa trạng thái PAPER hiện tại và đặt lại vốn?",
            parent=self.top,
        ):
            return
        self.settings.paper_initial_balance = balance
        save_settings(self.settings, self.account_id)
        self.on_saved()
        if reset and self.on_reset_paper:
            self.on_reset_paper(balance)
        action = "ĐÃ RESET" if reset else "ĐÃ LƯU"
        self.paper_status.configure(
            text=f"{action} · {balance:,.0f} VND", text_color=self.GREEN,
        )

    def _watchlist_tab(self, frame: ctk.CTkFrame) -> None:
        body = self._body(frame)
        card = self._card(
            body, "MÃ THEO DÕI CKCS",
            "Nhập một hoặc nhiều mã, cách nhau bằng dấu phẩy hoặc khoảng trắng. "
            "Bot chỉ lấy dữ liệu và kiểm tra rule với danh sách đã lưu.",
        )
        card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)
        self.watchlist_picker = SymbolPicker(
            card, self._watchlist_draft,
            on_change=lambda values: setattr(self, "_watchlist_draft", list(values)),
        )
        self.watchlist_picker.grid(row=1, column=0, rowspan=3, sticky="ew", padx=12, pady=(2, 4))
        exchange_row = ctk.CTkFrame(card, fg_color="transparent")
        exchange_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(5, 3))
        exchange_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            exchange_row, text="SÀN DỰ PHÒNG", font=FONT_KEY,
            text_color=self.TITLE,
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.exchange_symbol = ctk.CTkOptionMenu(
            exchange_row, values=self._watchlist_draft or [""], width=110, height=32,
            font=FONT_VALUE, fg_color=self.BLUE, dynamic_resizing=False,
            command=lambda symbol: self.exchange_choice.set(
                self.settings.symbol_exchanges.get(str(symbol).upper(), "TỰ ĐỘNG")
            ),
        )
        self.exchange_symbol.grid(row=0, column=1, sticky="w")
        first_exchange_symbol = self._watchlist_draft[0] if self._watchlist_draft else ""
        self.exchange_choice = ctk.StringVar(
            value=self.settings.symbol_exchanges.get(first_exchange_symbol, "TỰ ĐỘNG")
        )
        self.exchange_menu = ctk.CTkOptionMenu(
            exchange_row, values=["TỰ ĐỘNG", "HOSE", "HNX", "UPCOM"],
            variable=self.exchange_choice, width=120, height=32,
            font=FONT_VALUE, fg_color=self.BLUE, dynamic_resizing=False,
        )
        self.exchange_menu.grid(row=0, column=2, padx=8)
        self._hint_icon(
            exchange_row,
            "Daemon ưu tiên sàn DNSE tự nhận diện và cache lại. Chỉ chọn tay khi trạng thái báo CHƯA XÁC ĐỊNH SÀN; TỰ ĐỘNG sẽ xóa lựa chọn tay.",
        ).grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(
            exchange_row, text="LƯU SÀN", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._save_exchange_override,
        ).grid(row=0, column=4)

        save_row = ctk.CTkFrame(card, fg_color="transparent")
        save_row.grid(row=5, column=0, sticky="ew", padx=12, pady=(5, 10))
        save_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            save_row, text="LƯU DANH SÁCH", width=140, height=32,
            font=("Segoe UI", 12, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_watchlist,
        ).grid(row=0, column=1)

        holiday = self._card(
            body,
            "NGÀY NGHỈ GIAO DỊCH",
            "Viking dùng lịch DNSE, tự chặn T7/CN và có sẵn lịch nghỉ giao dịch Việt Nam 2026. "
            "Chỉ thêm tại đây khi Sở công bố ngày nghỉ bổ sung.",
        )
        holiday.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        holiday.grid_columnconfigure(0, weight=1)
        holiday_row = ctk.CTkFrame(holiday, fg_color="transparent")
        holiday_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(2, 5))
        holiday_row.grid_columnconfigure(0, weight=1)
        self.holiday_entry = ctk.CTkEntry(
            holiday_row, height=34, placeholder_text="YYYY-MM-DD",
            fg_color=self.SURFACE_2, border_color=self.BORDER,
            font=("Cascadia Mono", 13), text_color=self.TEXT,
        )
        self.holiday_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.holiday_entry.bind("<Return>", lambda _event: self._add_holidays(), add="+")
        ctk.CTkButton(
            holiday_row, text="+ THÊM", width=90, height=34,
            font=("Segoe UI", 11, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._add_holidays,
        ).grid(row=0, column=1)
        self.holiday_chips = ctk.CTkFrame(holiday, fg_color=self.SURFACE_2, corner_radius=8)
        self.holiday_chips.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=4)
        for column in range(6):
            self.holiday_chips.grid_columnconfigure(column, weight=1)
        self.holiday_status = ctk.CTkLabel(
            holiday, text="", font=("Segoe UI", 11), text_color=self.MUTED, anchor="w",
        )
        self.holiday_status.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(3, 8))
        ctk.CTkButton(
            holiday, text="LƯU NGÀY NGHỈ", width=145, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_holidays,
        ).grid(row=3, column=2, sticky="e", padx=12, pady=(3, 8))
        self._render_holiday_chips()

    def _telegram_tab(self, frame: ctk.CTkFrame) -> None:
        body = self._body(frame)
        card = self._card(
            body, "THÔNG BÁO TELEGRAM",
            "Chỉ điều khiển gửi tin; không thay đổi AUTO/ALERT hay hành vi đặt lệnh.",
        )
        card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(1, weight=1)
        self.tele_enabled = tk.BooleanVar(value=self.settings.telegram_enabled)
        ctk.CTkSwitch(
            card, text="BẬT TELEGRAM", variable=self.tele_enabled,
            font=("Segoe UI", 12, "bold"), text_color=self.TEXT,
            progress_color=self.GREEN,
        ).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(2, 6)
        )
        self.tele_token = self._field(
            card, 2, "BOT TOKEN", os.getenv(self.settings.telegram_token_env, ""), True,
        )
        self.tele_chat = self._field(
            card, 3, "CHAT ID", self.settings.telegram_chat_id,
        )
        event_box = ctk.CTkFrame(
            card, fg_color=self.SURFACE_2, corner_radius=8,
            border_width=1, border_color=self.BORDER,
        )
        event_box.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 3))
        event_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            event_box, text="LOẠI THÔNG BÁO", font=("Segoe UI", 10, "bold"),
            text_color=self.MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=(5, 2))
        ctk.CTkLabel(
            event_box, text="GỬI", font=("Segoe UI", 10, "bold"),
            text_color=self.MUTED,
        ).grid(row=0, column=1, padx=4, pady=(5, 2))
        ctk.CTkLabel(
            event_box, text="THỜI GIAN", font=("Segoe UI", 10, "bold"),
            text_color=self.MUTED,
        ).grid(row=0, column=2, padx=4, pady=(5, 2))
        self.tele_event_switches: dict[str, tk.BooleanVar] = {}
        self.tele_cooldown_entries: dict[str, ctk.CTkEntry] = {}
        self.tele_event_time_controls: dict[str, Any] = {}
        rows = (
            ("buy_queued", "BOT BUY ĐÃ XẾP LỆNH", "Gom nhiều BUY BOT trong cửa sổ phút này thành một tin; MANUAL không gửi loại tin này.", "batch"),
            ("closed", "VỊ THẾ BOT ĐÃ ĐÓNG", "Chỉ gửi sau khi bán hết vị thế và chống trùng đúng một lần cho mỗi trade; không dùng cooldown thời gian.", "once"),
            ("protect", "PROTECT CHẠM MỨC", "AUTO vẫn bán dù OFF. ON = AUTO vừa bán vừa báo; ALERT chỉ báo và không bán.", "cooldown"),
            ("indicator_exit", "E · EXIT ALERT", "Báo khi E phát tín hiệu; E ở ALERT không đặt lệnh.", "cooldown"),
            (
                "blocked_buy", "TÍN HIỆU",
                "Gửi BUY đã xuất hiện trong bảng TÍN HIỆU nhưng không thành lệnh, "
                "ví dụ BOT OFF, đủ slot, khóa mua, thiếu vốn hoặc broker từ chối.",
                "cooldown",
            ),
            (
                "corporate_action", "LỊCH NGHỈ & CHỐT QUYỀN",
                "Báo ngày thị trường nghỉ và cảnh báo mã đang giữ tới ngày giao dịch không hưởng quyền.",
                "cooldown",
            ),
            ("external_sell", "SELL TRÊN DNSE APP", "Báo khi Viking phát hiện và đồng bộ một lệnh bán ngoài app Viking.", "cooldown"),
            (
                "system", "HỆ THỐNG",
                "Báo lỗi thật của daemon, lịch giao dịch, DNSE API/WS hoặc xử lý lệnh; "
                "không báo trạng thái chờ bình thường khi app vừa khởi động.",
                "cooldown",
            ),
        )
        notifications = self.settings.telegram_notifications
        cooldowns = self.settings.telegram_cooldown_minutes
        for row_index, (key, label, hint, timing_mode) in enumerate(rows, start=1):
            label_box = ctk.CTkFrame(event_box, fg_color="transparent")
            label_box.grid(row=row_index, column=0, sticky="ew", padx=(8, 2), pady=1)
            ctk.CTkLabel(
                label_box, text=label, font=("Segoe UI", 11, "bold"),
                text_color=self.TEXT, anchor="w",
            ).pack(side="left")
            self._hint_icon(label_box, hint).pack(side="left", padx=(5, 0))
            variable = tk.BooleanVar(value=bool(notifications.get(key, False)))
            self.tele_event_switches[key] = variable
            ctk.CTkSwitch(
                event_box, text="", variable=variable, width=38,
                progress_color=self.GREEN, button_color=self.TEXT,
            ).grid(row=row_index, column=1, padx=6, pady=1)
            if timing_mode in {"batch", "cooldown"}:
                entry = ctk.CTkEntry(
                    event_box, width=58, height=26, justify="center",
                    font=("Segoe UI", 11, "bold"),
                )
                entry.insert(
                    0,
                    str(
                        self.settings.telegram_buy_batch_minutes
                        if timing_mode == "batch"
                        else cooldowns.get(key, 0)
                    ),
                )
                entry.grid(row=row_index, column=2, padx=(4, 2), pady=1)
                self.tele_event_time_controls[key] = entry
                if timing_mode == "batch":
                    self.tele_batch = entry
                else:
                    self.tele_cooldown_entries[key] = entry
                suffix = ctk.CTkLabel(
                    event_box,
                    text="ph · gom" if timing_mode == "batch" else "ph",
                    font=("Segoe UI", 9), text_color=self.MUTED,
                )
                suffix.grid(row=row_index, column=3, sticky="w", padx=(0, 8))
            else:
                timing = ctk.CTkLabel(
                    event_box, text="1 LẦN/TRADE", font=("Segoe UI", 9, "bold"),
                    text_color=self.MUTED,
                )
                timing.grid(row=row_index, column=2, columnspan=2, padx=6)
                self.tele_event_time_controls[key] = timing
        tele_actions = ctk.CTkFrame(card, fg_color="transparent")
        tele_actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 3))
        tele_actions.grid_columnconfigure(0, weight=1)
        self.btn_tele_test = ctk.CTkButton(
            tele_actions, text="GỬI THỬ", width=100, height=32, fg_color="#3A3F47",
            hover_color="#4B515B", font=("Segoe UI", 11, "bold"), command=self._test_telegram,
        )
        self.btn_tele_test.grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(
            tele_actions, text="LƯU", width=90, height=32, fg_color=self.GREEN,
            hover_color="#16A34A", font=("Segoe UI", 11, "bold"), command=self._save_telegram,
        ).grid(row=0, column=2)
        self.tele_status = ctk.CTkLabel(
            card, text="",
            font=("Segoe UI", 11), text_color=self.MUTED, anchor="w",
        )
        self.tele_status.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=(1, 10))

    @staticmethod
    def _account_payload(data: Any) -> tuple[list[dict[str, Any]], str, str]:
        root = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        root = root if isinstance(root, dict) else {}
        rows = root.get("accounts") or []
        rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return rows, str(root.get("custodyCode", "") or ""), str(root.get("name", "") or "")

    @staticmethod
    def _dnse_error_message(error: Any) -> str:
        message = str(error or "").strip()
        lowered = message.lower()
        if "authorization field" in lowered or "signature" in lowered:
            return "XÁC THỰC API KHÔNG HỢP LỆ · KIỂM TRA API KEY VÀ API SECRET"
        if "timed out" in lowered or "timeout" in lowered:
            return "DNSE KHÔNG PHẢN HỒI · HÃY THỬ LẠI"
        return message or "KHÔNG TÌM THẤY TÀI KHOẢN CKCS"

    def _load_accounts(self) -> None:
        api_key, api_secret = self.dnse_key.get().strip(), self.dnse_secret.get().strip()
        if not api_key or not api_secret:
            self.dnse_status.configure(text="THIẾU API KEY HOẶC API SECRET", text_color=self.RED)
            return
        self._tested_account = None
        self.btn_load_accounts.configure(state="disabled", text="ĐANG TEST...")
        self.dnse_status.configure(text="Đang kiểm tra DNSE...", text_color=self.WARN)

        def worker() -> None:
            probe = DNSEClient(api_key=api_key, api_secret=api_secret, account_no="DISCOVERY")
            try:
                data = probe.get_accounts()
                health = probe.api_health()
            finally:
                probe.close()
            self._post_ui(lambda: self._finish_load_accounts(data, health))

        threading.Thread(target=worker, name="viking-dnse-accounts", daemon=True).start()

    def _finish_load_accounts(self, data: Any, health: dict[str, Any]) -> None:
        self.btn_load_accounts.configure(state="normal", text="TEST")
        rows, custody, customer_name = self._account_payload(data)
        stock_accounts = [
            row for row in rows
            if str(row.get("id", row.get("accountNo", "")) or "").strip()
            and ("dealAccount" not in row or bool(row.get("dealAccount")))
        ]
        if not stock_accounts:
            self._tested_account = None
            error = self._dnse_error_message(health.get("last_error"))
            self.dnse_status.configure(text=error, text_color=self.RED)
            return
        saved_account = str(self.client.account_no or "").strip()
        selected_row = next(
            (
                row for row in stock_accounts
                if str(row.get("id", row.get("accountNo", "")) or "").strip() == saved_account
            ),
            stock_accounts[0],
        )
        account_id = str(
            selected_row.get("id", selected_row.get("accountNo", "")) or ""
        ).strip()
        self._tested_account = {"id": account_id, "custody": custody}
        self.account_info_labels["ACCOUNT ID"].configure(text=account_id)
        self.account_info_labels["CUSTODY"].configure(text=custody or "--")
        self.account_info_labels["CHỦ TK"].configure(text=customer_name or "--")
        self.account_info_labels["LOẠI"].configure(text="CKCS")
        self.connection_summary.configure(
            text=f"TEST OK  ·  ACCOUNT {account_id}", text_color=self.GREEN,
        )
        self.dnse_status.configure(
            text="KẾT NỐI DNSE THÀNH CÔNG", text_color=self.GREEN,
        )

    def _save_dnse(self) -> None:
        api_key = self.dnse_key.get().strip()
        api_secret = self.dnse_secret.get().strip()
        selected = self._tested_account
        if not api_key or not api_secret:
            self.dnse_status.configure(text="THIẾU API KEY HOẶC API SECRET", text_color=self.RED)
            return
        if not selected or not selected.get("id"):
            self.dnse_status.configure(text="HÃY TEST API TRƯỚC", text_color=self.RED)
            return
        selected_id = selected["id"]
        update_env(
            {
                "DNSE_API_KEY": api_key,
                "DNSE_API_SECRET": api_secret,
                "DNSE_ACCOUNT_NO": selected_id,
                "DNSE_STOCK_ACCOUNT_NO": selected_id,
                "DNSE_CUSTODY_CODE": selected.get("custody", ""),
                "DNSE_OTP_TYPE": self.client.otp_type,
            }
        )
        load_settings(selected_id)  # Materialize the independent account workspace.
        self.client.api_key = api_key
        self.client.api_secret = api_secret
        self.client.account_no = selected_id
        self.client.connect()
        self.dnse_status.configure(text="ĐÃ LƯU · ĐANG ÁP DỤNG WORKSPACE", text_color=self.GREEN)
        self.on_saved()
        if self.on_apply_account:
            self.top.after(150, lambda: self.on_apply_account(selected_id))

    def _otp_type_changed(self, value: str) -> None:
        smart = str(value or "").upper() == "SMART OTP"
        self.client.otp_type = "smart_otp" if smart else "email_otp"
        self.btn_send_otp.configure(
            state="disabled" if smart else "normal",
            text="SMART OTP TRÊN APP" if smart else "GỬI EMAIL",
        )

    def _token_storage_changed(self) -> None:
        if self.save_token_env.get():
            if self.client.has_trading_token():
                update_env(
                    {
                        "DNSE_TRADING_TOKEN": self.client.trading_token,
                        "DNSE_TRADING_TOKEN_EXPIRES_AT": f"{self.client.trading_token_expires_at:.3f}",
                    }
                )
                self.token_status.configure(text=".ENV", text_color=self.GREEN)
                self.dnse_status.configure(text="TOKEN ĐÃ LƯU TRONG .ENV", text_color=self.GREEN)
            else:
                self.token_status.configure(text="CHỜ TOKEN", text_color=self.WARN)
                self.dnse_status.configure(
                    text="ĐÃ BẬT LƯU · TOKEN SẼ ĐƯỢC GHI SAU KHI XÁC THỰC OTP",
                    text_color=self.WARN,
                )
            return
        update_env(
            {
                "DNSE_TRADING_TOKEN": None,
                "DNSE_TRADING_TOKEN_EXPIRES_AT": None,
            }
        )
        has_token = self.client.has_trading_token()
        self.token_status.configure(
            text="RAM" if has_token else "CHƯA CÓ",
            text_color=self.GREEN if has_token else self.WARN,
        )
        self.dnse_status.configure(
            text="TOKEN CHỈ GIỮ TRONG RAM" if has_token else "CHƯA CÓ TRADING TOKEN",
            text_color=self.MUTED if has_token else self.WARN,
        )

    def _clear_saved_token(self) -> None:
        update_env(
            {
                "DNSE_TRADING_TOKEN": None,
                "DNSE_TRADING_TOKEN_EXPIRES_AT": None,
            }
        )
        self.client.trading_token = ""
        self.client.trading_token_expires_at = 0.0
        self.save_token_env.set(False)
        self.otp.delete(0, "end")
        self.token_status.configure(text="ĐÃ XÓA", text_color=self.WARN)
        self.dnse_status.configure(text="ĐÃ XÓA TOKEN KHỎI .ENV VÀ RAM", text_color=self.WARN)

    def _send_otp(self) -> None:
        if self.otp_type.get() == "SMART OTP":
            self.token_status.configure(text="SMART OTP", text_color=self.WARN)
            self.dnse_status.configure(text="Lấy Smart OTP trên ứng dụng DNSE.", text_color=self.MUTED)
            return
        self.btn_send_otp.configure(state="disabled", text="ĐANG GỬI...")

        def worker() -> None:
            result = self.client.send_email_otp()
            self._post_ui(lambda: self._otp_result(result, verify=False))

        self._run_shared_io(worker, "viking-send-otp")

    def _verify_otp(self) -> None:
        code = self.otp.get().strip()
        if not code:
            self.token_status.configure(text="NHẬP OTP", text_color=self.RED)
            self.dnse_status.configure(text="Hãy nhập mã OTP để xác thực.", text_color=self.RED)
            return
        self.btn_verify_otp.configure(state="disabled", text="ĐANG XÁC THỰC...")

        def worker() -> None:
            result = self.client.verify_otp(code)
            self._post_ui(lambda: self._otp_result(result, verify=True))

        self._run_shared_io(worker, "viking-verify-otp")

    def _otp_result(self, result: Any, *, verify: bool) -> None:
        self._otp_type_changed(self.otp_type.get())
        self.btn_verify_otp.configure(state="normal", text="XÁC THỰC")
        if result.ok and verify:
            if self.save_token_env.get():
                update_env(
                    {
                        "DNSE_TRADING_TOKEN": self.client.trading_token,
                        "DNSE_TRADING_TOKEN_EXPIRES_AT": f"{self.client.trading_token_expires_at:.3f}",
                    }
                )
            else:
                update_env(
                    {
                        "DNSE_TRADING_TOKEN": None,
                        "DNSE_TRADING_TOKEN_EXPIRES_AT": None,
                    }
                )
            hours = self.client.trading_token_seconds_left() / 3600.0
            text = ".ENV" if self.save_token_env.get() else "RAM"
            detail = f"Xác thực thành công · token ở {text} · còn khoảng {hours:.1f} giờ."
        elif result.ok:
            text = "OTP ĐÃ GỬI"
            detail = "OTP đã gửi; hãy kiểm tra email."
        else:
            text = "LỖI OTP"
            detail = result.message or result.error or "DNSE không phản hồi."
        self.token_status.configure(text=text, text_color=self.GREEN if result.ok else self.RED)
        self.dnse_status.configure(text=detail, text_color=self.GREEN if result.ok else self.RED)

    @staticmethod
    def _parse_holidays(raw: str) -> tuple[list[str], list[str]]:
        values = list(dict.fromkeys(
            item.strip() for item in re.split(r"[,;\s]+", str(raw or "")) if item.strip()
        ))
        valid: list[str] = []
        invalid: list[str] = []
        for value in values:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                invalid.append(value)
            else:
                valid.append(value)
        return valid, invalid

    def _render_holiday_chips(self) -> None:
        for child in self.holiday_chips.winfo_children():
            child.destroy()
        default_dates = list(config.DEFAULT_VN_TRADING_HOLIDAYS)
        for index, value in enumerate(default_dates):
            ctk.CTkLabel(
                self.holiday_chips,
                text=value,
                height=30,
                fg_color="#29313B",
                corner_radius=6,
                font=("Cascadia Mono", 11, "bold"),
                text_color=self.TEXT,
            ).grid(row=index // 6, column=index % 6, sticky="ew", padx=4, pady=5)
        offset = len(default_dates)
        for index, value in enumerate(self._holiday_draft):
            ctk.CTkButton(
                self.holiday_chips, text=f"{value}  ×", height=30,
                fg_color="#343A43", hover_color="#4B515B",
                font=("Cascadia Mono", 11, "bold"),
                command=lambda date_value=value: self._remove_holiday(date_value),
            ).grid(
                row=(offset + index) // 6,
                column=(offset + index) % 6,
                sticky="ew",
                padx=4,
                pady=5,
            )
        self.holiday_status.configure(
            text=(
                f"DNSE + T7/CN · MẶC ĐỊNH 2026: {len(default_dates)} NGÀY"
                f" · BỔ SUNG: {len(self._holiday_draft)}"
            ),
            text_color=self.MUTED,
        )

    def _add_holidays(self) -> None:
        values, invalid = self._parse_holidays(self.holiday_entry.get())
        if invalid:
            self.holiday_status.configure(
                text=f"NGÀY KHÔNG HỢP LỆ: {', '.join(invalid)}", text_color=self.RED,
            )
            return
        if not values:
            self.holiday_status.configure(text="HÃY NHẬP YYYY-MM-DD", text_color=self.WARN)
            return
        values = [value for value in values if value not in config.DEFAULT_VN_TRADING_HOLIDAYS]
        if not values:
            self.holiday_status.configure(
                text="NGÀY NÀY ĐÃ CÓ TRONG LỊCH MẶC ĐỊNH 2026",
                text_color=self.WARN,
            )
            return
        self._holiday_draft = sorted(set(self._holiday_draft + values))
        self.holiday_entry.delete(0, "end")
        self._render_holiday_chips()

    def _remove_holiday(self, value: str) -> None:
        self._holiday_draft = [item for item in self._holiday_draft if item != value]
        self._render_holiday_chips()

    def _save_holidays(self) -> None:
        self.settings.custom_holidays = list(self._holiday_draft)
        save_settings(self.settings, self.account_id)
        self.on_saved()
        self.holiday_status.configure(
            text=(
                f"ĐÃ ÁP DỤNG {len(config.DEFAULT_VN_TRADING_HOLIDAYS)} NGÀY MẶC ĐỊNH"
                f" + {len(self._holiday_draft)} NGÀY BỔ SUNG"
            ),
            text_color=self.GREEN,
        )

    def _save_watchlist(self) -> None:
        symbols = self.watchlist_picker.get()
        if not symbols:
            self.watchlist_picker.status.configure(text="CẦN ÍT NHẤT 1 MÃ CKCS", text_color=self.RED)
            return
        self.settings.watchlist = symbols
        self.exchange_symbol.configure(values=symbols)
        if self.exchange_symbol.get() not in symbols:
            self.exchange_symbol.set(symbols[0])
            self.exchange_choice.set(self.settings.symbol_exchanges.get(symbols[0], "TỰ ĐỘNG"))
        save_settings(self.settings, self.account_id)
        self.on_saved()
        self.watchlist_picker.status.configure(
            text=f"ĐÃ ÁP DỤNG {len(symbols)} MÃ · DAEMON TỰ NHẬN", text_color=self.GREEN,
        )

    def _save_exchange_override(self) -> None:
        symbol = str(self.exchange_symbol.get() or "").strip().upper()
        if not symbol:
            return
        exchange = str(self.exchange_choice.get() or "TỰ ĐỘNG").strip().upper()
        if exchange == "TỰ ĐỘNG":
            self.settings.symbol_exchanges.pop(symbol, None)
        else:
            self.settings.symbol_exchanges[symbol] = exchange
        save_settings(self.settings, self.account_id)
        self.on_saved()
        self.watchlist_picker.status.configure(
            text=f"{symbol} · {exchange} · DAEMON TỰ NHẬN", text_color=self.GREEN,
        )

    def _save_telegram(self) -> None:
        token = self.tele_token.get().strip()
        chat_id = self.tele_chat.get().strip()
        if self.tele_enabled.get() and (not token or not chat_id):
            self.tele_status.configure(text="BẬT TELEGRAM CẦN ĐỦ BOT TOKEN VÀ CHAT ID", text_color=self.RED)
            return
        self.settings.telegram_enabled = bool(self.tele_enabled.get())
        self.settings.telegram_chat_id = chat_id
        try:
            batch_minutes = int(float(self.tele_batch.get().strip()))
        except (TypeError, ValueError):
            self.tele_status.configure(text="GOM BUY PHẢI LÀ SỐ PHÚT", text_color=self.RED)
            return
        if not 1 <= batch_minutes <= 120:
            self.tele_status.configure(text="GOM BUY TỪ 1 ĐẾN 120 PHÚT", text_color=self.RED)
            return
        cooldowns: dict[str, int] = {}
        for key, entry in self.tele_cooldown_entries.items():
            try:
                value = int(float(entry.get().strip()))
            except (TypeError, ValueError):
                self.tele_status.configure(
                    text="COOLDOWN PHẢI LÀ SỐ PHÚT", text_color=self.RED,
                )
                return
            if not 0 <= value <= 10080:
                self.tele_status.configure(
                    text="COOLDOWN TỪ 0 ĐẾN 10080 PHÚT", text_color=self.RED,
                )
                return
            cooldowns[key] = value
        self.settings.telegram_buy_batch_minutes = batch_minutes
        self.settings.telegram_notifications = {
            key: bool(variable.get())
            for key, variable in self.tele_event_switches.items()
        }
        self.settings.telegram_cooldown_minutes = cooldowns
        update_env({self.settings.telegram_token_env: token})
        save_settings(self.settings, self.account_id)
        self.on_saved()
        self.tele_status.configure(text="ĐÃ LƯU & ÁP DỤNG TELEGRAM", text_color=self.GREEN)

    def _test_telegram(self) -> None:
        token, chat_id = self.tele_token.get().strip(), self.tele_chat.get().strip()
        if not token or not chat_id:
            self.tele_status.configure(text="THIẾU BOT TOKEN HOẶC CHAT ID", text_color=self.RED)
            return
        self.btn_tele_test.configure(state="disabled", text="ĐANG GỬI...")

        def worker() -> None:
            error = ""
            client = TelegramClient(token)
            try:
                client.send_message(chat_id, "Viking V2 · Telegram BUY/CLOSED hoạt động.")
            except Exception as exc:  # network/API detail is useful to the operator
                error = client.safe_error(exc)
            self._post_ui(lambda: self._finish_telegram_test(error))

        threading.Thread(target=worker, name="viking-telegram-test", daemon=True).start()

    def _finish_telegram_test(self, error: str) -> None:
        self.btn_tele_test.configure(state="normal", text="GỬI THỬ")
        self.tele_status.configure(
            text=f"GỬI THẤT BẠI · {error}" if error else "ĐÃ GỬI TIN NHẮN THỬ",
            text_color=self.RED if error else self.GREEN,
        )
