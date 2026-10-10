from __future__ import annotations

import os
import math
from copy import deepcopy
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from .. import config
from ..branding import APP_NAME, window_title
from ..config import AppSettings, load_settings, save_settings
from ..dashboard.windows import FONT_KEY, FONT_VALUE, PALETTE, SymbolPicker, _HoverHint, _window
from ..config import update_env
from ..services.volume_scanner import (
    VN100_AS_OF,
    VolumeScanOptions,
    VolumeScanResult,
    VolumeScanRow,
    VolumeScanner,
    export_volume_scan,
    export_watchlist,
    import_watchlist,
)
from ..trading.market import MarketDataService
from ..trading.portfolio import cash_from_balance, nav_from_balance, priority_entry_orders
from .dnse.client import DNSEClient
from .dnse.websocket import DNSEMarketWS
from .telegram import TelegramClient


class VolumeScannerPopup:
    """VN100 volume scanner with explicit Excel export/watchlist replacement."""

    BG = "#111318"
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
        client: DNSEClient,
        post_ui: Callable[[Callable[[], None]], None],
        on_replace_watchlist: Callable[[list[str]], Any] | None = None,
    ):
        self.parent = parent
        self.client = client
        self.post_ui = post_ui
        self.on_replace_watchlist = on_replace_watchlist
        self.market = MarketDataService(client, DNSEMarketWS(client.api_key, client.api_secret))
        self.scanner = VolumeScanner(
            self.market.get_daily_bars,
            api_health=self.client.api_health,
        )
        self.rows: tuple[VolumeScanRow, ...] = ()
        self.busy = False

        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width, height = min(1050, screen_w - 50), min(700, screen_h - 70)
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 3)
        self.top = _window(parent, window_title("LỌC VOLUME VN100"), f"{width}x{height}+{x}+{y}")
        self.top.configure(fg_color=self.BG)
        self.top.minsize(900, 560)
        self.top.resizable(True, True)
        self.top.grid_rowconfigure(2, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        self.top.bind("<Escape>", lambda _event: self.hide(), add="+")

        header = ctk.CTkFrame(self.top, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 7))
        header.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(
            header, text="LỌC VOLUME VN100", font=("Segoe UI", 22, "bold"),
            text_color=self.TITLE, anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="So sánh volume trung bình của hai kỳ liền nhau · chỉ dùng các phiên đã đóng",
            font=("Segoe UI", 12), text_color=self.MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkLabel(
            header,
            text=f"Rổ VN100 theo vốn hóa · cập nhật {VN100_AS_OF}",
            font=("Segoe UI", 12), text_color=self.MUTED, anchor="e",
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        controls = ctk.CTkFrame(
            self.top, fg_color=self.SURFACE, corner_radius=10,
            border_width=1, border_color=self.BORDER,
        )
        controls.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        controls.grid_columnconfigure(0, weight=2, uniform="scanner_group")
        controls.grid_columnconfigure(1, weight=3, uniform="scanner_group")

        self.scope_group = self._parameter_group(
            controls,
            "1 · PHẠM VI KẾT QUẢ",
            "Chọn số mã đầu rổ VN100 sẽ quét và số dòng tối đa muốn nhận.",
        )
        self.scope_group.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        scope_fields = ctk.CTkFrame(self.scope_group, fg_color="transparent")
        scope_fields.grid(row=2, column=0, sticky="ew", padx=4, pady=(2, 4))
        scope_fields.grid_columnconfigure((0, 1), weight=1, uniform="scope")
        self.scan_count_entry = self._entry_control(
            scope_fields, 0, "SỐ MÃ QUÉT", "100", "Từ 1 đến 100 mã đầu rổ.",
        )
        self.result_count_entry = self._entry_control(
            scope_fields, 1, "SỐ MÃ LẤY", "20", "Không vượt quá số mã quét.",
        )

        self.filter_group = self._parameter_group(
            controls,
            "2 · ĐIỀU KIỆN LỌC",
            "So sánh trung bình kỳ gần nhất với kỳ ngay trước đó.",
        )
        self.filter_group.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        filter_fields = ctk.CTkFrame(self.filter_group, fg_color="transparent")
        filter_fields.grid(row=2, column=0, sticky="ew", padx=4, pady=(2, 4))
        filter_fields.grid_columnconfigure((0, 1, 2), weight=1, uniform="filter")
        self.sessions_choice = self._menu_control(
            filter_fields, 0, "CHU KỲ", ["5", "10"], "5", "Số phiên trong mỗi kỳ.",
        )
        self.threshold_entry = self._entry_control(
            filter_fields, 1, "NGƯỠNG (%)", "20", "Mức thay đổi tối thiểu.",
        )
        self.direction_choice = self._menu_control(
            filter_fields, 2, "HƯỚNG", ["TĂNG", "GIẢM", "CẢ HAI"], "CẢ HAI",
            "Chọn tăng, giảm hoặc cả hai.",
        )

        action_row = ctk.CTkFrame(controls, fg_color="transparent")
        action_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 11))
        action_row.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(
            action_row, text="Sẵn sàng · không tự sửa watchlist nếu chưa xác nhận",
            font=("Segoe UI", 12), text_color=self.MUTED, anchor="w",
        )
        self.status.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.scan_button = ctk.CTkButton(
            action_row, text="BẮT ĐẦU LỌC", width=150, height=40,
            font=("Segoe UI", 13, "bold"), fg_color=self.BLUE,
            hover_color=PALETTE["BLUE_HOVER"], command=self._start_scan,
        )
        self.scan_button.grid(row=0, column=1, padx=(0, 8))
        self.export_button = ctk.CTkButton(
            action_row, text="XUẤT EXCEL", width=150, height=40,
            font=("Segoe UI", 13, "bold"), fg_color="#2A2E34",
            hover_color=PALETTE["GREEN_HOVER"], state="disabled",
            command=self._export,
        )
        self.export_button.grid(row=0, column=2)
        self.replace_button = ctk.CTkButton(
            action_row, text="THAY WATCHLIST", width=165, height=40,
            font=("Segoe UI", 13, "bold"), fg_color="#2A2E34",
            hover_color=PALETTE["BLUE_HOVER"], state="disabled",
            command=self._replace_watchlist,
        )
        self.replace_button.grid(row=0, column=3, padx=(8, 0))

        table_frame = ctk.CTkFrame(
            self.top, fg_color=self.SURFACE, corner_radius=10,
            border_width=1, border_color=self.BORDER,
        )
        table_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "V2Volume.Treeview", background=self.SURFACE, foreground=self.TEXT,
            fieldbackground=self.SURFACE, rowheight=42,
            font=("Cascadia Mono", 13), borderwidth=0,
        )
        style.layout("V2Volume.Treeview", [("V2Volume.Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "V2Volume.Treeview.Heading", background=self.SURFACE_2,
            foreground=self.TITLE, font=("Segoe UI", 13, "bold"),
            relief="flat", padding=(9, 9),
        )
        style.map(
            "V2Volume.Treeview",
            background=[("selected", self.BLUE)], foreground=[("selected", "#FFFFFF")],
        )
        columns = ("symbol", "previous", "recent", "change", "status")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="none",
            style="V2Volume.Treeview",
        )
        for key, title, width_px, anchor in (
            ("symbol", "MÃ CK", 110, "center"),
            ("previous", "TB KỲ TRƯỚC", 190, "e"),
            ("recent", "TB KỲ GẦN NHẤT", 210, "e"),
            ("change", "% THAY ĐỔI", 170, "e"),
            ("status", "TRẠNG THÁI", 150, "center"),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width_px, minwidth=90, anchor=anchor, stretch=True)
        self.tree.tag_configure("increase", background="#172D20", foreground="#ECFDF3")
        self.tree.tag_configure("decrease", background="#321F23", foreground="#FFF1F2")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(5, 0), pady=5)
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns", pady=5, padx=(0, 5))
        self.tree.configure(yscrollcommand=yscroll.set)
        self.show()

    def _parameter_group(
        self, parent: ctk.CTkFrame, title: str, description: str,
    ) -> ctk.CTkFrame:
        group = ctk.CTkFrame(
            parent, fg_color=self.SURFACE_2, corner_radius=8,
            border_width=1, border_color=self.BORDER,
        )
        group.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            group, text=title, font=("Segoe UI", 14, "bold"),
            text_color=self.TITLE, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 0))
        ctk.CTkLabel(
            group, text=description, font=("Segoe UI", 11),
            text_color=self.MUTED, anchor="w", justify="left",
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 1))
        return group

    def _entry_control(
        self, parent: ctk.CTkFrame, column: int, label: str, value: str, hint: str,
    ) -> ctk.CTkEntry:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=0, column=column, sticky="nsew", padx=8, pady=(5, 4))
        cell.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cell, text=label, font=("Segoe UI", 13, "bold"),
            text_color=self.TITLE, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry = ctk.CTkEntry(
            cell, height=40, fg_color=self.BG, border_color="#46505D",
            font=("Cascadia Mono", 14), text_color=self.TEXT,
        )
        entry.grid(row=1, column=0, sticky="ew")
        entry.insert(0, value)
        ctk.CTkLabel(
            cell, text=hint, font=("Segoe UI", 11), text_color=self.MUTED,
            anchor="w", justify="left", wraplength=210,
        ).grid(row=2, column=0, sticky="ew", pady=(4, 0))
        return entry

    def _menu_control(
        self, parent: ctk.CTkFrame, column: int, label: str,
        values: list[str], value: str, hint: str,
    ) -> ctk.CTkOptionMenu:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=0, column=column, sticky="nsew", padx=8, pady=(5, 4))
        cell.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cell, text=label, font=("Segoe UI", 13, "bold"),
            text_color=self.TITLE, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        menu = ctk.CTkOptionMenu(
            cell, values=values, width=120, height=40,
            fg_color=self.BLUE, button_color=self.BLUE,
            button_hover_color=PALETTE["BLUE_HOVER"],
            font=("Segoe UI", 13, "bold"), dynamic_resizing=False,
        )
        menu.grid(row=1, column=0, sticky="ew")
        menu.set(value)
        ctk.CTkLabel(
            cell, text=hint, font=("Segoe UI", 11), text_color=self.MUTED,
            anchor="w", justify="left", wraplength=210,
        ).grid(row=2, column=0, sticky="ew", pady=(4, 0))
        return menu

    def show(self) -> None:
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    def hide(self) -> None:
        if self.top.winfo_exists():
            self.top.withdraw()

    def close(self) -> None:
        if self.top.winfo_exists():
            self.top.destroy()

    def _options(self) -> VolumeScanOptions:
        try:
            scan_count = int(self.scan_count_entry.get().strip())
            result_count = int(self.result_count_entry.get().strip())
            sessions = int(self.sessions_choice.get())
            threshold = float(self.threshold_entry.get().strip().replace(",", "."))
        except (TypeError, ValueError) as exc:
            raise ValueError("Số mã quét/lấy, chu kỳ và ngưỡng phải là số hợp lệ.") from exc
        return VolumeScanOptions(
            scan_count=scan_count,
            result_count=result_count,
            sessions=sessions,
            threshold_pct=threshold,
            direction=self.direction_choice.get(),
        ).validate()

    def _set_progress(self, completed: int, total: int, symbol: str) -> None:
        if self.busy and self.top.winfo_exists():
            self.status.configure(
                text=f"Đang quét {completed}/{total} · {symbol}", text_color=self.WARN,
            )

    def _start_scan(self) -> None:
        if self.busy:
            return
        try:
            options = self._options()
        except ValueError as exc:
            self.status.configure(text=str(exc), text_color=self.RED)
            return
        if not self.client.configured():
            self.status.configure(text="CHƯA CẤU HÌNH DNSE", text_color=self.RED)
            return

        self.busy = True
        self.rows = ()
        self.tree.delete(*self.tree.get_children())
        self.scan_button.configure(state="disabled", text="ĐANG LỌC...")
        self.export_button.configure(state="disabled", fg_color="#2A2E34")
        self.replace_button.configure(state="disabled", fg_color="#2A2E34")
        self.status.configure(text="Đang chuẩn bị dữ liệu DNSE...", text_color=self.WARN)

        def progress(completed: int, total: int, symbol: str) -> None:
            self.post_ui(lambda: self._set_progress(completed, total, symbol))

        def worker() -> None:
            try:
                result = self.scanner.scan(options, progress=progress)
                error = ""
            except Exception as exc:
                result = None
                error = str(exc)
            self.post_ui(lambda: self._finish_scan(result, error))

        threading.Thread(target=worker, name="viking-volume-scan", daemon=True).start()

    def _finish_scan(self, result: VolumeScanResult | None, error: str) -> None:
        if not self.top.winfo_exists():
            return
        self.busy = False
        self.scan_button.configure(state="normal", text="BẮT ĐẦU LỌC")
        if result is None:
            self.status.configure(text=f"LỌC THẤT BẠI · {error or 'Lỗi không xác định'}", text_color=self.RED)
            return

        self.rows = result.rows
        for row in self.rows:
            self.tree.insert(
                "", "end",
                values=(
                    row.symbol,
                    f"{row.previous_average:,.0f}",
                    f"{row.recent_average:,.0f}",
                    f"{row.change_pct:+.2f}%",
                    row.status,
                ),
                tags=("increase" if row.status == "TĂNG" else "decrease",),
            )
        summary = result.summary
        detail = (
            f"Đã quét {summary.scanned} · đạt {summary.matched} · hiển thị {summary.returned}"
            f" · thiếu dữ liệu {summary.insufficient} · TB trước = 0: {summary.zero_base}"
            f" · lỗi API: {summary.api_errors}"
        )
        if self.rows:
            self.status.configure(text=detail, text_color=self.GREEN)
            self.export_button.configure(state="normal", fg_color=self.GREEN)
            self.replace_button.configure(state="normal", fg_color=self.BLUE)
        else:
            self.status.configure(text=f"KHÔNG CÓ KẾT QUẢ · {detail}", text_color=self.WARN)

    def _export(self) -> None:
        if not self.rows:
            self.status.configure(text="Không có kết quả để xuất Excel.", text_color=self.WARN)
            return
        try:
            path = export_volume_scan(self.rows)
        except Exception as exc:
            self.status.configure(text=f"XUẤT EXCEL THẤT BẠI · {exc}", text_color=self.RED)
            return
        self.status.configure(text=f"ĐÃ XUẤT · {path}", text_color=self.GREEN)
        messagebox.showinfo("Lọc volume VN100", f"Đã xuất Excel:\n{path}", parent=self.top)

    def _replace_watchlist(self) -> None:
        symbols = [row.symbol for row in self.rows]
        if not symbols or self.on_replace_watchlist is None:
            self.status.configure(text="Không có kết quả để thay watchlist.", text_color=self.WARN)
            return
        if not messagebox.askyesno(
            "Thay watchlist",
            f"Xuất bản sao danh sách hiện tại rồi thay bằng {len(symbols)} mã đang hiển thị?",
            parent=self.top,
        ):
            return
        try:
            backup = self.on_replace_watchlist(symbols)
        except Exception as exc:
            self.status.configure(text=f"THAY WATCHLIST THẤT BẠI · {exc}", text_color=self.RED)
            return
        self.status.configure(
            text=f"ĐÃ THAY WATCHLIST · backup {backup}", text_color=self.GREEN,
        )


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
        self._priority_draft = list(settings.priority_symbols)
        self._priority_allocations = deepcopy(settings.priority_allocations)
        self._holiday_draft = sorted(
            set(settings.custom_holidays).difference(config.DEFAULT_VN_TRADING_HOLIDAYS)
        )
        self._volume_popup: VolumeScannerPopup | None = None

        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width, height = min(1080, screen_w - 60), min(720, screen_h - 70)
        x, y = max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 3)
        self.top = _window(parent, window_title("KẾT NỐI"), f"{width}x{height}+{x}+{y}")
        self.top.withdraw()
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
            command=self._refresh_priority_summary,
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
        self._refresh_priority_summary()
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        if self.on_visibility_changed:
            self.on_visibility_changed(True)

    def hide(self) -> None:
        if self._volume_popup and self._volume_popup.top.winfo_exists():
            self._volume_popup.hide()
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
        if self._volume_popup and self._volume_popup.top.winfo_exists():
            self._volume_popup.close()
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
            f"Nhập API Key/Secret rồi bấm TEST. {APP_NAME} gọi GET /accounts để kiểm tra và hiển thị "
            "tài khoản CKCS. LƯU ghi API và tài khoản hoạt động vào viking_v2/.env. "
            "XÓA API bỏ Key/Secret và Trading Token trên máy này; giữ tài khoản, settings và vị thế. "
            "Không hủy lệnh đã gửi DNSE; muốn giao dịch REAL tiếp phải nhập API và xác thực OTP lại.",
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
            credentials, 2, "API KEY", os.getenv("DNSE_API_KEY", ""), True,
        )
        self.dnse_key.grid_configure(columnspan=1, padx=(0, 8))
        def toggle_key() -> None:
            hidden = bool(self.dnse_key.cget("show"))
            self.dnse_key.configure(show="" if hidden else "•")
            self.key_visibility.configure(text="ẨN" if hidden else "HIỆN")
        self.key_visibility = ctk.CTkButton(credentials, text="HIỆN", width=60, height=30,
                                           fg_color=self.BLUE, command=toggle_key)
        self.key_visibility.grid(row=2, column=2, padx=(0, 12))
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
        self.btn_load_accounts = ctk.CTkButton(
            connection_actions, text="TEST", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._load_accounts,
        )
        self.btn_load_accounts.grid(row=0, column=1, padx=(0, 6))
        self.btn_save_dnse = ctk.CTkButton(
            connection_actions, text="LƯU API", width=100, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_dnse,
        )
        self.btn_save_dnse.grid(row=0, column=2, padx=(0, 6))
        self.btn_clear_dnse = ctk.CTkButton(
            connection_actions, text="XÓA API", width=90, height=32,
            font=("Segoe UI", 11, "bold"), fg_color="#7F1D1D",
            hover_color="#991B1B", command=self._clear_dnse,
        )
        self.btn_clear_dnse.grid(row=0, column=3)
        self.dnse_status = ctk.CTkLabel(
            credentials, text="Nhập API rồi bấm TEST.",
            height=28, font=("Segoe UI", 12, "bold"), text_color=self.MUTED,
            wraplength=900, justify="left", anchor="w",
        )
        self.dnse_status.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=(1, 8))

        self.dnse_compact_row = ctk.CTkFrame(body, fg_color="transparent")
        self.dnse_compact_row.grid(row=1, column=0, sticky="ew", padx=6, pady=3)
        self.dnse_compact_row.grid_columnconfigure(
            (0, 1, 2), weight=1, uniform="dnse_compact",
        )
        self.dnse_compact_row.grid_rowconfigure(0, weight=1)

        self.otp_card = self._card(
            self.dnse_compact_row, "XÁC THỰC DNSE",
            "OTP chỉ dùng một lần và không lưu. Sau khi OTP hợp lệ, DNSE cấp Trading Token. "
            "LƯU TOKEN giữ token đó qua lần mở app sau; XÓA bỏ token khỏi cả RAM và .env.",
        )
        self.otp_card.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self.otp_card.grid_columnconfigure((0, 1, 2), weight=1, uniform="otp_columns")
        self.otp_type = tk.StringVar(value="SMART OTP" if self.client.otp_type == "smart_otp" else "EMAIL OTP")
        ctk.CTkLabel(
            self.otp_card, text="OTP MỘT LẦN · KHÔNG LƯU", anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(3, 1))
        ctk.CTkSegmentedButton(
            self.otp_card, values=["EMAIL OTP", "SMART OTP"], variable=self.otp_type,
            height=32, selected_color=self.BLUE, selected_hover_color="#245C92",
            unselected_color="#3A3F47", unselected_hover_color="#4B515B",
            font=("Segoe UI", 11, "bold"), command=self._otp_type_changed,
        ).grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 5))
        ctk.CTkLabel(
            self.otp_card, text="MÃ OTP", anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(1, 1))
        self.otp = ctk.CTkEntry(
            self.otp_card, show="•", height=32,
            fg_color=self.SURFACE_2, border_color=self.BORDER,
            font=FONT_VALUE, text_color=self.TEXT,
        )
        self.otp.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 5))
        otp_actions = ctk.CTkFrame(self.otp_card, fg_color="transparent")
        otp_actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 6))
        otp_actions.grid_columnconfigure((0, 1), weight=1, uniform="otp_actions")
        self.btn_send_otp = ctk.CTkButton(
            otp_actions, text="GỬI OTP", height=32, fg_color="#3A3F47",
            hover_color="#4B515B", font=("Segoe UI", 11, "bold"), command=self._send_otp,
        )
        self.btn_send_otp.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_verify_otp = ctk.CTkButton(
            otp_actions, text="XÁC THỰC", height=32, fg_color=self.BLUE,
            hover_color="#245C92", font=("Segoe UI", 11, "bold"), command=self._verify_otp,
        )
        self.btn_verify_otp.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.token_bar = ctk.CTkFrame(
            self.otp_card, height=34, fg_color=self.SURFACE_2, corner_radius=7,
        )
        self.token_bar.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 9))
        self.token_bar.grid_columnconfigure(2, weight=1)
        token_hint = ctk.CTkButton(
            self.token_bar, text="!", width=24, height=24, corner_radius=12,
            font=("Segoe UI", 12, "bold"), fg_color=self.WARN,
            hover_color="#D97706", text_color="#111318",
        )
        token_hint.grid(row=0, column=0, padx=(5, 4), pady=5)
        _HoverHint(
            token_hint,
            "LƯU TOKEN: giữ DNSE Trading Token qua lần mở app sau trong .env. "
            "Tắt: chỉ giữ trong RAM. "
            "XÓA: bỏ token khỏi cả .env và RAM.",
        )
        ctk.CTkLabel(
            self.token_bar, text="TOKEN GD", anchor="w",
            font=("Segoe UI", 10, "bold"), text_color=self.TITLE,
        ).grid(row=0, column=1, sticky="w", padx=(0, 5))
        token_ready = self.client.has_trading_token()
        self.token_status = ctk.CTkLabel(
            self.token_bar,
            text=(
                ".ENV" if token_ready and self.save_token_env.get()
                else "RAM" if token_ready else "CHƯA CÓ"
            ),
            anchor="w", font=("Segoe UI", 11, "bold"),
            text_color=self.GREEN if token_ready else self.WARN,
        )
        self.token_status.grid(row=0, column=2, sticky="w", padx=(0, 3))
        self.save_token_switch = ctk.CTkSwitch(
            self.token_bar, text="LƯU TOKEN", variable=self.save_token_env,
            onvalue=True, offvalue=False, width=102,
            font=("Segoe UI", 10, "bold"), progress_color=self.GREEN,
            command=self._token_storage_changed,
        )
        self.save_token_switch.grid(row=0, column=3, padx=3, pady=3)
        self.btn_clear_token = ctk.CTkButton(
            self.token_bar, text="XÓA", width=45, height=26,
            font=("Segoe UI", 10, "bold"), fg_color="#3A3F47",
            hover_color="#4B515B", command=self._clear_saved_token,
        )
        self.btn_clear_token.grid(row=0, column=4, padx=(2, 5), pady=4)
        self._otp_type_changed(self.otp_type.get())

        self.paper_card = self._card(
            self.dnse_compact_row, "PAPER",
            "LƯU đổi vốn mặc định. RESET đưa tài khoản PAPER về số vốn này và xóa trạng thái PAPER hiện tại.",
        )
        self.paper_card.grid(row=0, column=1, sticky="nsew", padx=3)
        self.paper_card.grid_columnconfigure((0, 1, 2), weight=1, uniform="paper_columns")
        ctk.CTkLabel(
            self.paper_card, text="VỐN MẶC ĐỊNH", anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(3, 1))
        self.paper_balance = ctk.CTkEntry(
            self.paper_card, height=36,
            fg_color=self.SURFACE_2, border_color=self.BORDER,
            font=FONT_VALUE, text_color=self.TEXT,
        )
        self.paper_balance.insert(0, f"{self.settings.paper_initial_balance:,.0f}")
        self.paper_balance.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 8))
        paper_actions = ctk.CTkFrame(self.paper_card, fg_color="transparent")
        paper_actions.grid(row=3, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 6))
        paper_actions.grid_columnconfigure((0, 1), weight=1, uniform="paper_actions")
        ctk.CTkButton(
            paper_actions, text="LƯU VỐN", height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_paper_balance,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ctk.CTkButton(
            paper_actions, text="RESET", height=32,
            font=("Segoe UI", 11, "bold"), fg_color="#3A3F47",
            hover_color="#4B515B", command=lambda: self._save_paper_balance(reset=True),
        ).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.paper_status = ctk.CTkLabel(
            self.paper_card, text="LƯU chỉ đổi vốn mặc định", font=("Segoe UI", 10),
            text_color=self.MUTED, anchor="w", justify="left", wraplength=240,
        )
        self.paper_status.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 9))

        self.stats_card = self._card(
            self.dnse_compact_row, "THỐNG KÊ",
            "PNL là lãi/lỗ chu kỳ đã đóng, không phải lãi tạm tính. REAL/PAPER có sổ riêng.\n"
            "THEO NGÀY: chốt kỳ theo giờ GMT+7. CỘNG DỒN: tính từ lần ↻ gần nhất. Restart vẫn giữ.\n"
            "↻ reset bộ đếm và khóa chờ/cooldown, không mở BLOCK, không xóa vị thế.",
        )
        self.stats_card.grid(row=0, column=2, sticky="nsew", padx=(3, 0))
        self.stats_card.grid_columnconfigure((0, 1, 2), weight=1, uniform="stats_columns")
        ctk.CTkLabel(
            self.stats_card, text="CHẾ ĐỘ", anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(3, 1))
        stats_value = (
            "CỘNG DỒN"
            if self.settings.daily_stats_mode == "SINCE_RESET"
            else "THEO NGÀY"
        )
        self.daily_stats_choice = tk.StringVar(value=stats_value)
        self.daily_stats_segment = ctk.CTkSegmentedButton(
            self.stats_card, values=["THEO NGÀY", "CỘNG DỒN"],
            variable=self.daily_stats_choice,
            height=32,
            selected_color=self.BLUE,
            selected_hover_color="#245C92",
            unselected_color="#3A3F47",
            unselected_hover_color="#4B515B",
            font=("Segoe UI", 11, "bold"),
            command=lambda _value: self._refresh_stats_time_state(),
        )
        self.daily_stats_segment.grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 7),
        )
        ctk.CTkLabel(
            self.stats_card, text="GIỜ CHỐT NGÀY", anchor="w",
            font=FONT_KEY, text_color=self.TITLE,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 1))
        stats_time_row = ctk.CTkFrame(self.stats_card, fg_color="transparent")
        stats_time_row.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 6))
        stats_time_row.grid_columnconfigure(0, weight=1)
        self.daily_stats_time = ctk.CTkEntry(
            stats_time_row, height=32, placeholder_text="00:00",
            fg_color=self.SURFACE_2, border_color=self.BORDER,
            font=FONT_VALUE, text_color=self.TEXT,
        )
        self.daily_stats_time.insert(0, self.settings.daily_stats_reset_time)
        self.daily_stats_time.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.btn_save_daily_stats = ctk.CTkButton(
            stats_time_row, text="LƯU", width=68, height=32,
            font=("Segoe UI", 11, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_daily_stats_settings,
        )
        self.btn_save_daily_stats.grid(row=0, column=1, sticky="e")
        self.daily_stats_status = ctk.CTkLabel(
            self.stats_card,
            text=(
                "Cộng dồn tới khi bấm ↻"
                if self.settings.daily_stats_mode == "SINCE_RESET"
                else "Theo ngày · tự chốt đúng giờ"
            ),
            font=("Segoe UI", 10), text_color=self.MUTED,
            anchor="w", justify="left", wraplength=240,
        )
        self.daily_stats_status.grid(
            row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 9),
        )
        self._refresh_stats_time_state()

    def _refresh_stats_time_state(self) -> None:
        self.daily_stats_time.configure(state="normal" if self.daily_stats_choice.get() == "THEO NGÀY" else "disabled")

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

    def _save_daily_stats_settings(self) -> None:
        reset_time = str(self.daily_stats_time.get() or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reset_time):
            self.daily_stats_status.configure(
                text="GIỜ CHỐT KHÔNG HỢP LỆ · DÙNG HH:MM (00:00–23:59)",
                text_color=self.RED,
            )
            return
        value = self.daily_stats_choice.get()
        self.settings.daily_stats_mode = (
            "SINCE_RESET" if str(value or "").upper() in {"CỘNG DỒN", "TỪ LẦN RESET"} else "DAILY"
        )
        self.settings.daily_stats_reset_time = reset_time
        save_settings(self.settings, self.account_id)
        self.on_saved()
        detail = (
            "CỘNG DỒN TỪ LẦN RESET"
            if self.settings.daily_stats_mode == "SINCE_RESET"
            else f"TỰ CHỐT MỖI NGÀY LÚC {reset_time}"
        )
        self.daily_stats_status.configure(text=f"ĐÃ LƯU · {detail}", text_color=self.GREEN)

    def _watchlist_tab(self, frame: ctk.CTkFrame) -> None:
        body = self._body(frame)
        card = self._card(
            body, "MÃ THEO DÕI CKCS",
            "Nhập một hoặc nhiều mã, cách nhau bằng dấu phẩy hoặc khoảng trắng. "
            "Bot chỉ lấy dữ liệu và kiểm tra rule với danh sách đã lưu.",
        )
        card.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
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
        ctk.CTkLabel(
            save_row, text="Chỉ thay đổi khi bấm lưu", font=("Segoe UI", 11),
            text_color=self.MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.btn_import_watchlist = ctk.CTkButton(
            save_row, text="NHẬP EXCEL", width=115, height=38,
            font=("Segoe UI", 12, "bold"), fg_color="#3A3F47",
            hover_color="#4B515B", command=self._import_watchlist,
        )
        self.btn_import_watchlist.grid(row=0, column=1, padx=(0, 7))
        self.btn_export_watchlist = ctk.CTkButton(
            save_row, text="XUẤT EXCEL", width=115, height=38,
            font=("Segoe UI", 12, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._export_watchlist,
        )
        self.btn_export_watchlist.grid(row=0, column=2, padx=(0, 7))
        ctk.CTkButton(
            save_row, text="LƯU DANH SÁCH", width=150, height=38,
            font=("Segoe UI", 13, "bold"), fg_color=self.GREEN,
            hover_color="#16A34A", command=self._save_watchlist,
        ).grid(row=0, column=3)

        self.volume_scanner_card = self._card(
            body,
            "TIỆN ÍCH PHÂN TÍCH",
            "Bộ lọc đọc volume lịch sử từ DNSE. Kết quả chỉ thay watchlist khi operator chủ động xác nhận.",
        )
        self.volume_scanner_card.configure(fg_color="#1C2733", border_color="#315A85")
        self.volume_scanner_card.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self.volume_scanner_card.grid_columnconfigure(0, weight=1)
        self.volume_scanner_hint = ctk.CTkLabel(
            self.volume_scanner_card,
            text="So sánh volume trung bình 5/10 phiên của VN100 · xem kết quả trước, sau đó xuất Excel.",
            font=("Segoe UI", 12), text_color=self.MUTED, anchor="w", justify="left",
            wraplength=560,
        )
        self.volume_scanner_hint.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=(12, 14), pady=(2, 11),
        )
        self.btn_volume_scanner = ctk.CTkButton(
            self.volume_scanner_card, text="LỌC VOLUME VN100", width=190, height=40,
            font=("Segoe UI", 13, "bold"), fg_color=self.BLUE,
            hover_color="#245C92", command=self._open_volume_scanner,
        )
        self.btn_volume_scanner.grid(row=1, column=2, sticky="e", padx=12, pady=(2, 11))
        _HoverHint(
            self.btn_volume_scanner,
            "Mở popup lọc độc lập. Tiện ích không đọc hoặc sửa watchlist hiện tại.",
            placement="below",
        )

        self.priority_card = self._card(
            body,
            "MÃ PRIORITY",
            "Mỗi mã Priority giữ 1 slot trong tổng số mã BOT, không vượt hạn mức.\n"
            "Ví dụ tối đa 5, có 2 Priority → mã thường dùng tối đa 3 slot. "
            "Slot giữ chỗ không tự tạo BUY; vẫn cần tín hiệu, tiền và room P1.",
        )
        self.priority_card.configure(fg_color="#27231A", border_color="#7A5A18")
        self.priority_card.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        self.priority_card.grid_columnconfigure(0, weight=1)
        self.priority_picker = SymbolPicker(
            self.priority_card, self._priority_draft,
            columns=6, compact=True, placeholder="FPT, SSI",
            on_change=self._priority_changed,
            on_configure=self._configure_priority_symbol,
        )
        self.priority_picker.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 9))
        self.btn_save_priority = ctk.CTkButton(
            self.priority_card, text="LƯU PRIORITY", width=145, height=38,
            font=("Segoe UI", 12, "bold"), fg_color=self.WARN,
            hover_color="#D97706", text_color="#111318", command=self._save_priority,
        )
        self.btn_save_priority.grid(row=4, column=1, sticky="e", padx=12, pady=(0, 10))
        self.priority_status = ctk.CTkLabel(
            self.priority_card, text="", font=("Segoe UI", 11),
            text_color=self.MUTED, anchor="w",
        )
        self.priority_status.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 10))
        capital = ctk.CTkFrame(self.priority_card, fg_color="transparent")
        capital.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        self.priority_capital_enabled = tk.BooleanVar(value=self.settings.priority_capital_enabled)
        ctk.CTkSwitch(capital, text="VỐN RIÊNG", variable=self.priority_capital_enabled,
                      command=self._refresh_priority_summary).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(capital, text="TỔNG (triệu)").pack(side="left")
        self.priority_total = ctk.CTkEntry(capital, width=95)
        self.priority_total.insert(0, f"{self.settings.priority_total_capital / 1_000_000:g}")
        self.priority_total.pack(side="left", padx=8)
        self.priority_total.bind("<KeyRelease>", lambda _event: self._refresh_priority_summary(), add="+")
        ctk.CTkButton(capital, text="CHIA HẠN MỨC", width=120, command=self._divide_priority_capital).pack(side="left")
        self._hint_icon(capital,
            "OFF: mỗi Priority giữ ngân sách NAV × P1 / số mã BOT tối đa.\n"
            "100 triệu, P1 100%, tối đa 4 mã → mỗi mã 25 triệu, không phải một mã dùng hết tài khoản.\n"
            "ON: dùng hạn mức riêng gồm phí; 15 triệu × 50% → mỗi BUY ≤ 7,5 triệu. MAX 1 giữ 7,5 triệu; MAX 2 mua tổng ≤ 15 triệu.\n"
            "Quỹ 50 triệu trong tài khoản NAV 100 triệu → dành ngân sách 50 triệu cho Priority, không chuyển tiền DNSE.\n"
            "Tổng 50 triệu, tất cả dùng 50%, MAX 1 → cả nhóm được mua 25 triệu, để dành 25 triệu.\n"
            "P1 100% chỉ là trần cổ phiếu của tài khoản; không đổi % sử dụng từng mã.\n"
            "CÒN HẠN MỨC = ĐƯỢC MUA trừ vốn cổ đang giữ và BUY đang chờ, gồm phí. "
            "Đây không phải tiền khả dụng hay cam kết sẽ mua; vẫn kiểm tra tiền, room P1 và lô giao dịch.\n"
            "CHIA HẠN MỨC thay các hạn mức nháp bằng Tổng / số Priority, giữ % sử dụng và MAX LỆNH. "
            "Phải LƯU mới áp dụng, không tạo lệnh. Tiền giữ lại không cho mã khác mượn; vẫn chịu tiền thật và room P1."
        ).pack(side="left", padx=8)
        self.priority_summary = ctk.CTkFrame(self.priority_card, fg_color=self.SURFACE_2, corner_radius=7)
        self.priority_summary.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        self._refresh_priority_summary()

        holiday = self._card(
            body,
            "NGÀY NGHỈ GIAO DỊCH",
            f"{APP_NAME} dùng lịch DNSE, tự chặn T7/CN và có sẵn lịch nghỉ giao dịch Việt Nam 2026. "
            "Chỉ thêm tại đây khi Sở công bố ngày nghỉ bổ sung.",
        )
        holiday.grid(row=3, column=0, sticky="ew", padx=6, pady=6)
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

    def _open_volume_scanner(self) -> None:
        popup = self._volume_popup
        if popup and popup.top.winfo_exists():
            popup.show()
            return
        self._volume_popup = VolumeScannerPopup(
            self.top, self.client, self._post_ui, self._replace_watchlist_from_volume,
        )

    def _telegram_tab(self, frame: ctk.CTkFrame) -> None:
        body = self._body(frame)
        card = self._card(
            body, "THÔNG BÁO TELEGRAM",
            "Chỉ điều khiển gửi tin; không thay đổi AUTO/ALERT hay hành vi đặt lệnh.\n"
            "GOM: chờ gom tin BUY, không trì hoãn đặt lệnh. GIÃN: cách giữa các thông báo mới cùng loại/mã.\n"
            "BUY mặc định GỬI NGAY; chọn GOM TIN để gom theo số phút. GIÃN 0 = không giãn, vẫn chống trùng. "
            "Vị thế đóng gửi 1 tin tổng kết cho mỗi vị thế BOT đã bán hết, không phải giới hạn toàn bot một tin.\n"
            "Cần bật Telegram, bật loại tin và LƯU. GỬI THỬ kiểm tra token/chat ID; không đặt lệnh.",
        )
        card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(1, weight=1)
        self.tele_enabled = tk.BooleanVar(value=self.settings.telegram_enabled)
        self.save_telegram_token_env = tk.BooleanVar(
            value=bool(os.getenv(self.settings.telegram_token_env, "").strip()),
        )
        telegram_header = ctk.CTkFrame(card, fg_color="transparent")
        telegram_header.grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(2, 6),
        )
        telegram_header.grid_columnconfigure(1, weight=1)
        ctk.CTkSwitch(
            telegram_header, text="BẬT TELEGRAM", variable=self.tele_enabled,
            font=("Segoe UI", 12, "bold"), text_color=self.TEXT,
            progress_color=self.GREEN,
        ).grid(row=0, column=0, sticky="w")
        self.save_telegram_token_switch = ctk.CTkSwitch(
            telegram_header, text="LƯU BOT TOKEN",
            variable=self.save_telegram_token_env,
            onvalue=True, offvalue=False,
            font=("Segoe UI", 11, "bold"), text_color=self.TEXT,
            progress_color=self.GREEN,
            command=self._telegram_token_storage_changed,
        )
        self.save_telegram_token_switch.grid(row=0, column=2, sticky="e", padx=(8, 6))
        self.btn_clear_telegram_token = ctk.CTkButton(
            telegram_header, text="XÓA", width=54, height=28,
            font=("Segoe UI", 10, "bold"), fg_color="#3A3F47",
            hover_color="#4B515B", command=self._clear_telegram_token,
        )
        self.btn_clear_telegram_token.grid(row=0, column=3, sticky="e")
        _HoverHint(
            self.save_telegram_token_switch,
            "Bật: lưu Bot Token trong .env để dùng lại sau khi mở app. "
            "Tắt: token chỉ tồn tại trong RAM của phiên hiện tại.",
        )
        telegram_token = str(
            getattr(
                self.parent,
                "_telegram_session_token",
                os.getenv(self.settings.telegram_token_env, ""),
            )
            or ""
        )
        self.tele_token = self._field(card, 2, "BOT TOKEN", telegram_token, True)
        self.tele_chat = self._field(
            card, 3, "CHAT ID", self.settings.telegram_chat_id,
        )
        event_box = ctk.CTkFrame(
            card, fg_color=self.SURFACE_2, corner_radius=8,
            border_width=1, border_color=self.BORDER,
        )
        event_box.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 3))
        event_box.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(
            event_box, text="LOẠI THÔNG BÁO", font=("Segoe UI", 10, "bold"),
            text_color=self.MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=(5, 2))
        ctk.CTkLabel(
            event_box, text="GỬI", font=("Segoe UI", 10, "bold"),
            text_color=self.MUTED,
        ).grid(row=0, column=3, padx=4, pady=(5, 2))
        ctk.CTkLabel(
            event_box, text="CÁCH GỬI / GIÃN TIN", font=("Segoe UI", 10, "bold"),
            text_color=self.MUTED,
        ).grid(row=0, column=4, columnspan=2, padx=4, pady=(5, 2))
        self.tele_event_labels: dict[str, Any] = {}
        self.tele_event_hint_buttons: dict[str, Any] = {}
        self.tele_event_switches: dict[str, tk.BooleanVar] = {}
        self.tele_cooldown_entries: dict[str, ctk.CTkEntry] = {}
        self.tele_event_time_controls: dict[str, Any] = {}
        rows = (
            ("buy_queued", "BOT BUY ĐÃ XẾP LỆNH", "GỬI NGAY: mỗi yêu cầu BUY BOT báo một tin. GOM TIN: báo chung sau số phút.\nChưa xác nhận đã gửi/khớp. LƯU để áp dụng; chuyển sang GỬI NGAY gửi cả tin đang chờ.", "batch"),
            (
                "blocked_buy", "BUY · TÍN HIỆU EMA/RSI",
                "Báo điều kiện EMA/RSI đang bật, không lọc theo giờ mua, WHIPSAW, khóa lỗ hoặc vốn/slot.\n"
                "Gửi riêng: tin đầu gửi ngay, mặc định giãn 60 phút/mã/sổ. Bật gom tín hiệu: dùng chung phút GOM TIN. Chỉ báo kỹ thuật.",
                "cooldown",
            ),
            (
                "buy_lost", "BUY · MẤT TÍN HIỆU",
                "Báo EMA/RSI không còn đạt sau tin BUY đã gửi, nếu chưa tạo lệnh.\n"
                "Không hủy lệnh. Gửi riêng: mặc định giãn 60 phút/mã/sổ. Bật gom tín hiệu: mỗi mã/sổ chỉ giữ trạng thái mới nhất.",
                "cooldown",
            ),
            ("protect", "PROTECT CHẠM MỨC", "AUTO báo đã tạo yêu cầu bán, chưa xác nhận khớp. ALERT chỉ báo, không bán. Tắt tin không tắt PROTECT. Phút = giãn tin mới cùng mã, không trì hoãn SELL.", "cooldown"),
            ("indicator_exit", "E · EXIT ALERT", "Chỉ báo khi E ở ALERT: không đặt bán, nên cần chống lặp. E AUTO không gửi tin này; khi vị thế BOT bán hết sẽ báo CLOSED.", "cooldown"),
            ("closed", "VỊ THẾ BOT ĐÃ ĐÓNG", "Gửi 1 tin tổng kết khi vị thế BOT đã bán hết, không chờ phút. Ví dụ mua 1.000 CP: bán 500 chưa tổng kết; bán nốt 500 gửi 1 tin. Vị thế BOT tiếp theo có tin riêng. Cần bật Telegram + dòng này và LƯU.", "once"),
            ("external_sell", "SELL TRÊN DNSE APP", f"Báo khi {APP_NAME} phát hiện và đồng bộ một lệnh bán ngoài app {APP_NAME}. Phút = giãn các thông báo mới; 0 = không giãn thời gian, vẫn chống trùng theo lệnh.", "cooldown"),
            (
                "corporate_action", "LỊCH NGHỈ & CHỐT QUYỀN",
                "Báo ngày thị trường nghỉ và cảnh báo mã đang giữ tới ngày giao dịch không hưởng quyền.",
                "cooldown",
            ),
            (
                "system", "HỆ THỐNG",
                "Báo lỗi thật của daemon, lịch giao dịch, DNSE API/WS hoặc xử lý lệnh; "
                "không báo chờ bình thường. Tin đầu gửi ngay; mặc định giãn 30 phút.",
                "cooldown",
            ),
        )
        notifications = self.settings.telegram_notifications
        cooldowns = self.settings.telegram_cooldown_minutes
        for row_index, (key, label, hint, timing_mode) in enumerate(rows, start=1):
            label_widget = ctk.CTkLabel(
                event_box, text=label, font=("Segoe UI", 11, "bold"),
                text_color=self.TEXT, anchor="w",
            )
            label_widget.grid(row=row_index, column=0, sticky="w", padx=(10, 4), pady=1)
            self.tele_event_labels[key] = label_widget
            hint_button = self._hint_icon(event_box, hint)
            hint_button.grid(row=row_index, column=1, padx=(0, 8), pady=1)
            self.tele_event_hint_buttons[key] = hint_button
            variable = tk.BooleanVar(value=bool(notifications.get(key, False)))
            self.tele_event_switches[key] = variable
            ctk.CTkSwitch(
                event_box, text="", variable=variable, width=38,
                progress_color=self.GREEN, button_color=self.TEXT,
            ).grid(row=row_index, column=3, padx=6, pady=1)
            if timing_mode in {"batch", "cooldown"}:
                timing_parent = event_box
                if timing_mode == "batch":
                    timing_parent = ctk.CTkFrame(event_box, fg_color="transparent")
                    timing_parent.grid(row=row_index, column=4, columnspan=2, sticky="w", padx=(4, 8), pady=1)
                    self.tele_buy_mode = tk.StringVar(value=(
                        "GOM TIN" if self.settings.telegram_buy_delivery_mode == "BATCH" else "GỬI NGAY"
                    ))
                    self.tele_buy_mode_selector = ctk.CTkSegmentedButton(
                        timing_parent, values=["GỬI NGAY", "GOM TIN"],
                        variable=self.tele_buy_mode, width=150, height=26,
                        dynamic_resizing=False, font=("Segoe UI", 10, "bold"),
                        selected_color=self.BLUE, selected_hover_color="#245C92",
                        command=self._telegram_buy_mode_changed,
                    )
                    self.tele_buy_mode_selector.grid(row=0, column=0)
                entry = ctk.CTkEntry(
                    timing_parent, width=48 if timing_mode == "batch" else 58, height=26, justify="center",
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
                entry.grid(
                    row=0 if timing_mode == "batch" else row_index,
                    column=1 if timing_mode == "batch" else 4, padx=(4, 2), pady=1,
                )
                self.tele_event_time_controls[key] = entry
                if timing_mode == "batch":
                    self.tele_batch = entry
                else:
                    self.tele_cooldown_entries[key] = entry
                suffix = ctk.CTkLabel(
                    timing_parent,
                    text="ph" if timing_mode == "batch" else "ph · giãn",
                    font=("Segoe UI", 9), text_color=self.MUTED,
                )
                suffix.grid(
                    row=0 if timing_mode == "batch" else row_index,
                    column=2 if timing_mode == "batch" else 5, sticky="w", padx=(0, 8),
                )
                if timing_mode == "batch":
                    self.tele_batch_suffix = suffix
                    self._telegram_buy_mode_changed()
            else:
                timing = ctk.CTkLabel(
                    event_box, text="1 TIN/VỊ THẾ", font=("Segoe UI", 9, "bold"),
                    text_color=self.MUTED,
                )
                timing.grid(row=row_index, column=4, columnspan=2, padx=6)
                _HoverHint(timing, hint)
                self.tele_event_time_controls[key] = timing
        technical_batch = ctk.CTkFrame(card, fg_color="transparent")
        technical_batch.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 3))
        self.tele_batch_technical = tk.BooleanVar(value=self.settings.telegram_batch_technical_signals)
        self.tele_batch_technical_switch = ctk.CTkSwitch(
            technical_batch, text="GOM CẢ BUY KỸ THUẬT / MẤT BUY",
            variable=self.tele_batch_technical, font=("Segoe UI", 11, "bold"),
            progress_color=self.GREEN, command=self._telegram_buy_mode_changed,
        )
        self.tele_batch_technical_switch.grid(row=0, column=0, sticky="w")
        self._hint_icon(
            technical_batch,
            "Chỉ áp dụng khi chọn GOM TIN; dùng chung số phút của BUY đã xếp lệnh.\n"
            "Mỗi mã/sổ REAL hoặc PAPER chỉ một dòng trạng thái mới nhất. "
            "Các ô giãn BUY kỹ thuật/MẤT BUY áp dụng khi gửi riêng.\n"
            "PROTECT, E ALERT, CLOSED và lỗi hệ thống gửi riêng theo công tắc của chúng. "
            "Không đổi rule mua bán hoặc TRACE.",
        ).grid(row=0, column=1, padx=(8, 0))
        self._telegram_buy_mode_changed()
        tele_actions = ctk.CTkFrame(card, fg_color="transparent")
        tele_actions.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 3))
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
        self.tele_status.grid(row=7, column=0, columnspan=3, sticky="ew", padx=12, pady=(1, 10))

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
        self.btn_clear_dnse.configure(state="disabled")
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
        self.btn_clear_dnse.configure(state="normal")
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

    def _clear_dnse(self) -> None:
        if self.btn_verify_otp.cget("state") == "disabled":
            self.dnse_status.configure(text="CHỜ XÁC THỰC OTP HOÀN TẤT TRƯỚC KHI XÓA API", text_color=self.WARN)
            return
        if not messagebox.askyesno(
            "Xóa API DNSE",
            "Xóa API Key/Secret và Trading Token trên máy này?\n"
            "REAL ngừng kết nối/gửi lệnh mới cho đến khi nhập API và OTP lại.\n"
            "Giữ tài khoản, settings, vị thế; không hủy lệnh đã gửi DNSE.",
            parent=self.top,
        ):
            return
        try:
            update_env({
                "DNSE_API_KEY": None, "DNSE_API_SECRET": None,
                "DNSE_TRADING_TOKEN": None, "DNSE_TRADING_TOKEN_EXPIRES_AT": None,
            })
        except Exception:
            self.dnse_status.configure(text="XÓA THẤT BẠI · KIỂM TRA QUYỀN GHI .ENV", text_color=self.RED)
            return
        self.client.api_key = self.client.api_secret = self.client.trading_token = ""
        self.client.trading_token_expires_at = 0.0
        self.client.connect()
        self._tested_account = None
        for entry in (self.dnse_key, self.dnse_secret, self.otp):
            entry.delete(0, "end")
        self.dnse_key.configure(show="•")
        self.key_visibility.configure(text="HIỆN")
        self.save_token_env.set(False)
        self.token_status.configure(text="ĐÃ XÓA", text_color=self.WARN)
        self.connection_summary.configure(text="ĐÃ XÓA API · GIỮ WORKSPACE", text_color=self.WARN)
        self.dnse_status.configure(text="REAL CẦN API + OTP MỚI · KHÔNG HỦY LỆNH DNSE", text_color=self.WARN)
        # Restart the data daemon with cleared credentials, keeping this account's
        # runtime. Do not switch accounts, delete positions or cancel broker orders.
        if self.on_apply_account:
            self.on_apply_account(self.account_id)

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
                    text="ĐÃ BẬT LƯU TRADING TOKEN · CHỈ GHI SAU KHI OTP HỢP LỆ",
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
            # OTP is one-time input, never persistent state. Remove it from
            # the widget as soon as DNSE has exchanged it for a trading token.
            self.otp.delete(0, "end")
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

    def _backup_watchlist(self) -> Any:
        return export_watchlist(
            self.settings.watchlist,
            priority_symbols=self.settings.priority_symbols,
            symbol_exchanges=self.settings.symbol_exchanges,
        )

    def _apply_watchlist(
        self,
        symbols: list[str],
        *,
        priority_symbols: list[str] | None = None,
        symbol_exchanges: dict[str, str] | None = None,
        backup: bool = False,
    ) -> Any:
        values = list(dict.fromkeys(
            str(symbol or "").strip().upper()
            for symbol in symbols
            if str(symbol or "").strip()
        ))
        if not values:
            raise ValueError("Watchlist cần ít nhất một mã CK.")
        backup_path = self._backup_watchlist() if backup else None
        allowed = set(values)
        priority_source = (
            self.settings.priority_symbols if priority_symbols is None else priority_symbols
        )
        priority = [
            symbol for symbol in dict.fromkeys(
                str(value or "").strip().upper()
                for value in priority_source
                if str(value or "").strip()
            )
            if symbol in allowed
        ]
        exchange_source = self.settings.symbol_exchanges if symbol_exchanges is None else symbol_exchanges
        exchanges = {
            str(symbol).upper(): str(exchange).upper()
            for symbol, exchange in (exchange_source or {}).items()
            if str(symbol).upper() in allowed
        }
        self.settings.watchlist = values
        self.settings.priority_symbols = priority
        self.settings.symbol_exchanges = exchanges
        self._watchlist_draft = list(values)
        self._priority_draft = list(priority)
        self.watchlist_picker.set(values)
        self.priority_picker.set(priority)
        self.exchange_symbol.configure(values=values)
        if self.exchange_symbol.get() not in values:
            self.exchange_symbol.set(values[0])
        selected = self.exchange_symbol.get()
        self.exchange_choice.set(self.settings.symbol_exchanges.get(selected, "TỰ ĐỘNG"))
        save_settings(self.settings, self.account_id)
        self.on_saved()
        return backup_path

    def _save_watchlist(self) -> None:
        symbols = self.watchlist_picker.get()
        if not symbols:
            self.watchlist_picker.status.configure(text="CẦN ÍT NHẤT 1 MÃ CKCS", text_color=self.RED)
            return
        try:
            self._apply_watchlist(symbols, priority_symbols=self.priority_picker.get())
        except Exception as exc:
            self.watchlist_picker.status.configure(text=f"LƯU THẤT BẠI · {exc}", text_color=self.RED)
            return
        self.watchlist_picker.status.configure(
            text=f"ĐÃ ÁP DỤNG {len(symbols)} MÃ · DAEMON TỰ NHẬN", text_color=self.GREEN,
        )

    def _priority_total_vnd(self) -> float:
        value = float(self.priority_total.get().strip()) * 1_000_000
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Tổng vốn phải lớn hơn 0.")
        return value

    def _priority_changed(self, values: list[str]) -> None:
        self._priority_draft = list(values)
        if hasattr(self, "priority_summary"):
            self._refresh_priority_summary()

    def refresh_runtime_preview(self) -> None:
        """Use the dashboard's existing UI tick; no timer or broker request."""
        if (self.top.winfo_exists() and self.top.winfo_viewable()
                and self.tabs.get() == "MÃ CK"):
            self._refresh_priority_summary()

    def _refresh_priority_summary(self) -> None:
        if not hasattr(self, "priority_summary"):
            return
        overview, rows, invalid = self._priority_preview_data()
        fingerprint = (overview, tuple(rows), invalid)
        if fingerprint == getattr(self, "_priority_preview_fingerprint", None):
            return
        self._priority_preview_fingerprint = fingerprint
        for child in self.priority_summary.winfo_children():
            child.destroy()
        self.priority_preview = ctk.CTkLabel(
            self.priority_summary, text=overview, font=("Segoe UI", 11), anchor="w",
            justify="left", wraplength=950, text_color=self.RED if invalid else self.MUTED,
        )
        self.priority_preview.grid(row=0, column=0, columnspan=7, sticky="ew", padx=10, pady=(6, 5))
        _HoverHint(self.priority_preview, self._priority_preview_hint(rows, invalid))
        headings = (
            ("MÃ", "Mã Priority được cấu hình ở bảng này."),
            ("HẠN MỨC", "Ngân sách dành cho mã, gồm tiền mua và phí; không phải số tiền đã mua."),
            ("DÙNG", "% hạn mức cho một lần BUY. Ví dụ hạn mức 15 triệu, dùng 50%: mỗi lần ≤ 7,5 triệu, gồm phí."),
            ("LỆNH", "BUY BOT đã dùng/đang chờ / MAX LỆNH, theo REAL hoặc PAPER đang chọn.\n0/2: chưa mua, tối đa 2 lượt; 1/2: đã dùng hoặc đang chờ 1 lượt.\nKhớp từng phần tính 1 lượt; hủy chưa khớp không tính. Bán hết mới đếm lại. MANUAL không tính.\nVỐN RIÊNG OFF: tối đa 1 lượt. —/2: chưa đọc được bộ đếm, không có nghĩa là 0. Bản nháp phải LƯU PRIORITY mới áp dụng."),
            ("ĐƯỢC MUA", "Tổng tối đa = hạn mức × % sử dụng × MAX LỆNH, không vượt hạn mức. 15 triệu × 50% × 2 = 15 triệu, gồm phí."),
            ("ĐỂ DÀNH", "Hạn mức − được mua. Ví dụ 15 triệu − 7,5 triệu = để dành 7,5 triệu."),
            ("CÒN HẠN MỨC", "Được mua trừ vốn cổ đang giữ và BUY đang chờ; không phải tiền khả dụng."),
        )
        weights = (2, 4, 2, 3, 4, 4, 4)
        for column, (title, hint) in enumerate(headings):
            self.priority_summary.grid_columnconfigure(column, weight=weights[column], uniform="priority-money")
            heading = ctk.CTkLabel(self.priority_summary, text=title, width=0, font=("Segoe UI", 11, "bold"),
                                  text_color=self.MUTED)
            heading.grid(row=1, column=column, sticky="ew", padx=5, pady=3)
            _HoverHint(heading, hint)
        for index, values in enumerate(rows, 2):
            for column, value in enumerate(values):
                ctk.CTkLabel(self.priority_summary, text=value, width=0, font=("Segoe UI", 12),
                             text_color=self.TEXT).grid(row=index, column=column, sticky="ew", padx=5, pady=2)

    def _priority_preview_hint(self, rows: list[tuple[str, ...]], invalid: bool) -> str:
        """Explain the displayed draft without adding another money rule."""
        if invalid:
            return "Ngân sách phải > 0; tổng hạn mức từng mã không được vượt ngân sách. Chưa áp dụng bản nháp này."
        if not self.priority_capital_enabled.get():
            return (
                "VỐN RIÊNG OFF: mỗi mã dùng ngân sách theo NAV × P1 / tối đa mã BOT.\n"
                "Ví dụ 100 triệu × P1 100% / 4 mã = 25 triệu/mã. Bỏ qua hạn mức và % riêng đang nhập."
            )
        examples = []
        for symbol, cap, pct, _orders, buy, saved, _remaining in rows:
            allocation = self._priority_allocations.get(symbol, {})
            maximum = config.priority_max_orders(allocation.get("max_orders", 1))
            if maximum > 1:
                per_order = config.finite_nonnegative(allocation.get("limit_vnd")) * config.finite_nonnegative(allocation.get("use_pct", 100)) / 100
                examples.append(f"{symbol}: mỗi lần ≤ {per_order / 1_000_000:g} triệu, tối đa {maximum} lần; tổng ≤ {buy}; để dành {saved}.")
            else:
                examples.append(f"{symbol}: {cap} × {pct} = được mua {buy}; để dành {saved}.")
        return "\n".join([
            "ĐÂY LÀ HẠN MỨC CẤU HÌNH, không phải tiền đã mua hay số tiền được mua ngay.",
            *examples,
            "ĐƯỢC MUA là tổng mức trần của các mã, gồm phí; ĐỂ DÀNH là phần không dùng + phần chưa chia.",
            "CÒN HẠN MỨC trừ vốn cổ đang giữ và BUY đang chờ. Số mua thực tế còn theo tiền, P1 và lô 100 CP.",
            "P1 100% không tự đổi mức sử dụng 50% của mã thành 100%.",
            "MAX LỆNH đếm BUY đã khớp/đang chờ trong vị thế BOT; khớp nhiều đợt tính 1. Bán hết mới đếm lại.",
            "Mua thêm cần tín hiệu BUY mới; không mua thêm khi đang thoát/đã bán một phần. Cộng vào cùng vị thế và giá vốn bình quân.",
            "Tối đa mã BOT đếm mã, không đếm số lần mua. MAX LỆNH riêng chỉ áp dụng khi VỐN RIÊNG ON.",
            "TÀI SẢN NGOÀI NGÂN SÁCH = NAV − ngân sách Priority, không cộng thêm vào hạn mức của các mã này.",
            "Phải LƯU PRIORITY mới áp dụng bản nháp; không chuyển hay phong tỏa tiền tại DNSE.",
        ])

    def _priority_preview_data(self) -> tuple[str, list[tuple[str, ...]], bool]:
        """Read-only draft preview: reuse sizing rules, never queue or save a BUY."""
        def money(value: float) -> str:
            return f"{value / 1_000_000:,.3f}".rstrip("0").rstrip(".") + " tr"

        enabled = bool(self.priority_capital_enabled.get())
        symbols = self.priority_picker.get()
        allocations = config.normalize_priority_allocations(self._priority_allocations, symbols)
        mode_var = getattr(self.parent, "mode", None)
        mode = mode_var.get() if mode_var is not None else ("PAPER" if self.settings.paper_mode else "REAL")
        balance, positions, _orders = getattr(self.parent, "snapshots", {}).get(mode, ({}, [], []))
        nav, cash = (nav_from_balance(balance, positions), cash_from_balance(balance)) if balance else (None, None)
        overview = f"PREVIEW {mode} · TỔNG TÀI SẢN {money(nav) if nav is not None else '—'} · TIỀN KHẢ DỤNG {money(cash) if cash is not None else '—'}"
        invalid = False
        try:
            total = self._priority_total_vnd() if enabled else 0.0
            if enabled:
                config.validate_priority_capital(total, symbols, allocations)
        except (ValueError, TypeError):
            total, invalid = 0.0, True
        if enabled:
            allocated = sum(row["limit_vnd"] for row in allocations.values())
            buying = sum(config.priority_buy_limit(row) for row in allocations.values())
            if invalid:
                overview += "\nQUỸ KHÔNG HỢP LỆ · Tổng > 0 và tổng hạn mức ≤ quỹ"
            else:
                overview += f"\nPRIORITY {money(total)} = ĐƯỢC MUA {money(buying)} + ĐỂ DÀNH {money(total - buying)}"
                overview += f"\nHẠN MỨC ĐÃ CHIA {money(allocated)} · CHƯA CHIA {money(total - allocated)}"
                if nav is not None:
                    overview += f" · TÀI SẢN NGOÀI NGÂN SÁCH {money(max(0.0, nav - total))}"
                    if total > nav:
                        overview += " · QUỸ VƯỢT NAV"
        else:
            overview += "\nVỐN RIÊNG OFF · 100% ngân sách theo P1 / số mã BOT tối đa; không dùng hạn mức nháp"

        draft = deepcopy(self.settings)
        draft.priority_symbols = list(symbols)
        draft.priority_capital_enabled = enabled
        draft.priority_total_capital = total
        draft.priority_allocations = allocations
        checks_fn = getattr(self.parent, "_preview_entry_checks", None)
        status_fn = getattr(self.parent, "_book_preview_status", None)
        status = status_fn() if callable(status_fn) else {}
        rows = []
        for symbol in symbols:
            allocation = allocations.get(symbol, {"limit_vnd": 0.0, "use_pct": 100.0})
            cap, pct = allocation["limit_vnd"], allocation["use_pct"]
            capital, checks = {}, {}
            if not invalid and callable(checks_fn):
                checks = checks_fn(symbol, status, settings=draft) or {}
                capital = checks.get("priority_capital") or {}
            if not enabled:
                cap, pct = capital.get("limit_vnd"), 100.0
            maximum = config.priority_max_orders(allocation.get("max_orders", 1)) if enabled else 1
            used = checks.get("entry_orders_used")
            trades, queue = getattr(self.parent, "trade_state", None), getattr(self.parent, "queue", None)
            if used is None and trades is not None and queue is not None:
                # Counts are local and readable even before an account balance arrives.
                used = priority_entry_orders(symbol, mode, trades, queue, enabled,
                                             symbols, allocations)["entry_orders_used"]
            buy_limit = config.priority_buy_limit(allocation) if enabled else cap
            remaining = max(0.0, buy_limit - capital["committed_vnd"]) if buy_limit is not None and "committed_vnd" in capital else None
            rows.append((symbol, money(cap) if cap is not None else "THEO RULE", f"{pct:g}%",
                         f"{used if used is not None else '—'}/{maximum}",
                         money(buy_limit) if buy_limit is not None else "THEO P1",
                         money(cap - buy_limit) if cap is not None else "—",
                         money(remaining) if remaining is not None else "—"))
        return overview, rows, invalid

    def _divide_priority_capital(self) -> None:
        try:
            symbols = self.priority_picker.get()
            if not symbols:
                raise ValueError("Thêm mã Priority trước khi chia vốn.")
            limit = self._priority_total_vnd() // len(symbols)
            self._priority_allocations = {
                symbol: {"limit_vnd": limit, "use_pct": self._priority_allocations.get(symbol, {}).get("use_pct", 100.0),
                         "max_orders": config.priority_max_orders(self._priority_allocations.get(symbol, {}).get("max_orders", 1))}
                for symbol in symbols
            }
            self.priority_status.configure(text=f"BẢN NHÁP · {limit / 1_000_000:g} triệu/mã · cần LƯU", text_color=self.WARN)
            self._refresh_priority_summary()
        except (ValueError, TypeError) as exc:
            self.priority_status.configure(text=str(exc), text_color=self.RED)

    def _configure_priority_symbol(self, symbol: str) -> None:
        top = _window(self.top, f"PRIORITY · {symbol}", "480x390")
        row = self._priority_allocations.get(symbol, {"limit_vnd": 0.0, "use_pct": 100.0})
        ctk.CTkLabel(top, text=f"{symbol} · NGÂN SÁCH RIÊNG", font=("Segoe UI", 16, "bold")).pack(pady=10)
        ctk.CTkLabel(top, text="HẠN MỨC (triệu đồng, gồm phí)").pack()
        limit = ctk.CTkEntry(top)
        limit.insert(0, f"{row['limit_vnd'] / 1_000_000:g}")
        limit.pack()
        ctk.CTkLabel(top, text="MỖI LẦN MUA (%) · tính trên hạn mức").pack()
        use = ctk.CTkEntry(top)
        use.insert(0, f"{row['use_pct']:g}")
        use.pack()
        ctk.CTkLabel(top, text="MAX LỆNH · số lần BUY trong một vị thế BOT").pack(pady=(5, 0))
        maximum = ctk.CTkEntry(top)
        maximum.insert(0, str(config.priority_max_orders(row.get("max_orders", 1))))
        maximum.pack()
        _HoverHint(maximum, "1–100 lần. Khớp nhiều đợt vẫn là 1; hủy chưa khớp không tính. Bán hết mới đếm lại.\nMua thêm phải có tín hiệu mới; không mua khi đang thoát/đã bán một phần. Các lần mua dùng chung SL/PROTECT theo giá vốn bình quân.\nVí dụ hạn mức 15 triệu, mỗi lần 50%, MAX 2: mỗi lần ≤ 7,5 triệu, tổng ≤ 15 triệu gồm phí. Chỉ áp dụng khi VỐN RIÊNG ON; MANUAL không dùng giới hạn số lần BOT.")
        status = ctk.CTkLabel(top, text="Chỉ áp dụng khi VỐN RIÊNG được bật.", wraplength=450)
        status.pack(pady=5)

        def refresh(_event=None) -> None:
            try:
                allocation = self._priority_allocation_inputs(limit.get(), use.get(), maximum.get())
                once = allocation["limit_vnd"] * allocation["use_pct"] / 100
                total = config.priority_buy_limit(allocation)
                status.configure(text=f"Mỗi lần ≤ {once / 1_000_000:g} triệu · Tổng ≤ {total / 1_000_000:g} triệu\nĐể dành {(allocation['limit_vnd'] - total) / 1_000_000:g} triệu · gồm phí", text_color=self.MUTED)
            except (ValueError, TypeError):
                status.configure(text="Hạn mức ≥ 0; mỗi lần 0–100%; MAX LỆNH nguyên 1–100.", text_color=self.RED)
        for entry in (limit, use, maximum):
            entry.bind("<KeyRelease>", refresh)
        refresh()

        def apply() -> None:
            try:
                allocation = self._priority_allocation_inputs(limit.get(), use.get(), maximum.get())
                cap, pct = allocation["limit_vnd"], allocation["use_pct"]
                if symbol not in self.priority_picker.get():
                    raise ValueError("Mã đã bị bỏ khỏi Priority.")
                self._priority_allocations[symbol] = allocation
                self._refresh_priority_summary()
                self.priority_status.configure(
                    text=f"BẢN NHÁP · {symbol}: {cap / 1_000_000:g} triệu × {pct:g}% / lần · MAX {allocation['max_orders']} · cần LƯU", text_color=self.WARN,
                )
                top.destroy()
            except (ValueError, TypeError) as exc:
                status.configure(text=str(exc), text_color=self.RED)
        ctk.CTkButton(top, text="ÁP DỤNG BẢN NHÁP", command=apply).pack(pady=5)

    @staticmethod
    def _priority_allocation_inputs(limit: str, use: str, maximum: str) -> dict:
        cap, pct, count = float(limit) * 1_000_000, float(use), float(maximum)
        if not all(math.isfinite(value) for value in (cap, pct, count)) or cap < 0 or not 0 <= pct <= 100 or not count.is_integer() or not 1 <= count <= 100:
            raise ValueError("Hạn mức ≥ 0; mỗi lần 0–100%; MAX LỆNH nguyên 1–100.")
        return {"limit_vnd": cap, "use_pct": pct, "max_orders": int(count)}

    def _save_priority(self) -> None:
        priority = self.priority_picker.get()
        symbols = list(dict.fromkeys(self.watchlist_picker.get() + priority))
        try:
            from ..rules.business import StaticRuleParameters
            if len(priority) > StaticRuleParameters.from_dict(self.settings.rule_parameters).max_positions:
                raise ValueError("Số mã Priority vượt số mã BOT tối đa trong RULE.")
            enabled = bool(self.priority_capital_enabled.get())
            total = self._priority_total_vnd() if enabled else config.finite_nonnegative(self.priority_total.get()) * 1_000_000
            allocations = config.normalize_priority_allocations(self._priority_allocations, priority)
            if enabled:
                config.validate_priority_capital(total, priority, allocations)
            self.settings.priority_capital_enabled = enabled
            self.settings.priority_total_capital = total
            self.settings.priority_allocations = allocations
            self._apply_watchlist(symbols, priority_symbols=priority)
        except Exception as exc:
            self.priority_status.configure(text=f"LƯU THẤT BẠI · {exc}", text_color=self.RED)
            return
        self.priority_status.configure(
            text=f"ĐÃ LƯU {len(priority)} MÃ · GIỮ {len(priority)} SLOT TRONG HẠN MỨC BOT",
            text_color=self.GREEN,
        )

    def _export_watchlist(self) -> None:
        try:
            path = self._backup_watchlist()
        except Exception as exc:
            self.watchlist_picker.status.configure(text=f"XUẤT THẤT BẠI · {exc}", text_color=self.RED)
            return
        self.watchlist_picker.status.configure(text=f"ĐÃ XUẤT · {path.name}", text_color=self.GREEN)
        messagebox.showinfo("Xuất watchlist", f"Đã xuất Excel:\n{path}", parent=self.top)

    def _import_watchlist(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.top,
            title="Nhập watchlist",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not path:
            return
        try:
            imported = import_watchlist(path)
        except Exception as exc:
            self.watchlist_picker.status.configure(text=f"NHẬP THẤT BẠI · {exc}", text_color=self.RED)
            return
        if not messagebox.askyesno(
            "Replace watchlist",
            f"Xuất bản sao danh sách hiện tại rồi thay bằng {len(imported.symbols)} mã từ Excel?",
            parent=self.top,
        ):
            return
        try:
            backup = self._apply_watchlist(
                list(imported.symbols),
                priority_symbols=list(imported.priority_symbols),
                symbol_exchanges=imported.symbol_exchanges,
                backup=True,
            )
        except Exception as exc:
            self.watchlist_picker.status.configure(text=f"NHẬP THẤT BẠI · {exc}", text_color=self.RED)
            return
        self.watchlist_picker.status.configure(
            text=f"ĐÃ REPLACE {len(imported.symbols)} MÃ · backup {backup.name}",
            text_color=self.GREEN,
        )

    def _replace_watchlist_from_volume(self, symbols: list[str]) -> Any:
        backup = self._apply_watchlist(symbols, backup=True)
        self.watchlist_picker.status.configure(
            text=f"ĐÃ REPLACE {len(symbols)} MÃ TỪ VOLUME · backup {backup.name}",
            text_color=self.GREEN,
        )
        return backup

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

    def _store_telegram_token(self, token: str) -> str:
        """Apply Telegram token persistence without ever writing it to settings.json."""
        value = str(token or "").strip()
        env_key = self.settings.telegram_token_env
        if self.save_telegram_token_env.get() and value:
            update_env({env_key: value})
            setattr(self.parent, "_telegram_session_token", value)
            return ".ENV"
        update_env({env_key: None})
        if value:
            # Keep a session-only copy outside os.environ so reopening this
            # popup still shows LƯU BOT TOKEN as OFF.
            setattr(self.parent, "_telegram_session_token", value)
            return "RAM"
        setattr(self.parent, "_telegram_session_token", "")
        return "ĐÃ XÓA"

    def _telegram_token_storage_changed(self) -> None:
        token = self.tele_token.get().strip()
        if self.save_telegram_token_env.get() and not token:
            self.tele_status.configure(
                text="NHẬP BOT TOKEN RỒI BẤM LƯU", text_color=self.WARN,
            )
            return
        location = self._store_telegram_token(token)
        self.on_saved()
        self.tele_status.configure(
            text=(
                "BOT TOKEN ĐÃ LƯU TRONG .ENV"
                if location == ".ENV"
                else "BOT TOKEN CHỈ GIỮ TRONG RAM"
                if location == "RAM"
                else "CHƯA CÓ BOT TOKEN"
            ),
            text_color=self.GREEN if token else self.WARN,
        )

    def _clear_telegram_token(self) -> None:
        env_key = self.settings.telegram_token_env
        update_env({env_key: None})
        os.environ.pop(env_key, None)
        setattr(self.parent, "_telegram_session_token", "")
        self.tele_token.delete(0, "end")
        self.save_telegram_token_env.set(False)
        self.tele_enabled.set(False)
        self.settings.telegram_enabled = False
        save_settings(self.settings, self.account_id)
        self.on_saved()
        self.tele_status.configure(
            text="ĐÃ XÓA BOT TOKEN KHỎI .ENV VÀ RAM", text_color=self.WARN,
        )

    def _telegram_buy_mode_changed(self, _value: str = "") -> None:
        batch = self.tele_buy_mode.get() == "GOM TIN"
        for widget in (self.tele_batch, self.tele_batch_suffix):
            if batch:
                widget.grid()
            else:
                widget.grid_remove()
        switch = getattr(self, "tele_batch_technical_switch", None)
        if switch is not None:
            switch.configure(state="normal" if batch else "disabled")
            for key in ("blocked_buy", "buy_lost"):
                self.tele_cooldown_entries[key].configure(
                    state="disabled" if batch and self.tele_batch_technical.get() else "normal")

    def _save_telegram(self) -> None:
        token = self.tele_token.get().strip()
        chat_id = self.tele_chat.get().strip()
        if self.tele_enabled.get() and (not token or not chat_id):
            self.tele_status.configure(text="BẬT TELEGRAM CẦN ĐỦ BOT TOKEN VÀ CHAT ID", text_color=self.RED)
            return
        delivery_mode = "BATCH" if self.tele_buy_mode.get() == "GOM TIN" else "IMMEDIATE"
        batch_minutes = self.settings.telegram_buy_batch_minutes
        if delivery_mode == "BATCH":
            try:
                batch_minutes = int(float(self.tele_batch.get().strip()))
            except (TypeError, ValueError, OverflowError):
                self.tele_status.configure(text="GOM BUY PHẢI LÀ SỐ PHÚT", text_color=self.RED)
                return
            if not 1 <= batch_minutes <= 120:
                self.tele_status.configure(text="GOM BUY TỪ 1 ĐẾN 120 PHÚT", text_color=self.RED)
                return
        cooldowns: dict[str, int] = {}
        for key, entry in self.tele_cooldown_entries.items():
            if (key in {"blocked_buy", "buy_lost"} and delivery_mode == "BATCH"
                    and self.tele_batch_technical.get()):
                cooldowns[key] = self.settings.telegram_cooldown_minutes[key]
                continue
            try:
                value = int(float(entry.get().strip()))
            except (TypeError, ValueError, OverflowError):
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
        self.settings.telegram_enabled = bool(self.tele_enabled.get())
        self.settings.telegram_chat_id = chat_id
        self.settings.telegram_buy_delivery_mode = delivery_mode
        self.settings.telegram_buy_batch_minutes = batch_minutes
        self.settings.telegram_batch_technical_signals = bool(self.tele_batch_technical.get())
        self.settings.telegram_notifications = {
            key: bool(variable.get())
            for key, variable in self.tele_event_switches.items()
        }
        self.settings.telegram_cooldown_minutes = cooldowns
        location = self._store_telegram_token(token)
        save_settings(self.settings, self.account_id)
        self.on_saved()
        self.tele_status.configure(
            text=f"ĐÃ LƯU & ÁP DỤNG TELEGRAM · TOKEN {location}",
            text_color=self.GREEN,
        )

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
                client.send_message(chat_id, f"{APP_NAME} · Telegram BUY/CLOSED hoạt động.")
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
