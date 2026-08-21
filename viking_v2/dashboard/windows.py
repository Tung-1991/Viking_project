from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import customtkinter as ctk


# One palette for every popup, so the app cannot drift into four colour schemes.
PALETTE = {
    "BG": "#111318",        # window background
    "PANEL": "#15181D",     # tab body
    "SURFACE": "#22262D",   # cards and table rows
    "SURFACE_2": "#1B1F25", # inputs, headers, striped rows
    "BORDER": "#343A43",    # every border and chip
    "SLATE": "#3A3F47",     # neutral buttons
    "SLATE_HOVER": "#4B515B",
    "TEXT": "#E8EBEF",
    "MUTED": "#C5CBD4",
    "DIM": "#6B7480",
    "BLUE": "#2B6CB0",
    "BLUE_HOVER": "#245C92",
    "GREEN": "#22C55E",
    "GREEN_HOVER": "#16A34A",
    "RED": "#EF4444",
    "WARN": "#F59E0B",
}


def install_fast_scroll(root: ctk.CTk, pixels: int = 300) -> None:
    """Replace CustomTkinter's mouse wheel with a pixel-sized step.

    The stock handler scrolls in canvas "units", which on these long panels is
    a few pixels per notch and makes every popup feel stuck.  One binding here
    covers every scrollable frame in the app because the wheel is bound to the
    application-wide "all" tag.
    """
    root.unbind_all("<MouseWheel>")

    def wheel(event: Any) -> str | None:
        widget = event.widget
        while widget is not None:
            if isinstance(widget, tk.Canvas):
                first, last = widget.yview()
                if (first, last) == (0.0, 1.0):
                    return None
                box = widget.bbox("all")
                total = max(1, (box[3] - box[1]) if box else 1)
                notches = int(event.delta / 120) or (1 if event.delta > 0 else -1)
                widget.yview_moveto(first - notches * pixels / total)
                return "break"
            widget = getattr(widget, "master", None)
        return None

    root.bind_all("<MouseWheel>", wheel, add="+")


def parse_symbols(raw: str) -> tuple[list[str], list[str]]:
    """Split a free-text ticker list into accepted and rejected symbols."""
    values = list(dict.fromkeys(
        item.strip().upper() for item in re.split(r"[,;\s]+", str(raw or "")) if item.strip()
    ))
    invalid = [item for item in values if not re.fullmatch(r"[A-Z][A-Z0-9]{2,11}", item)]
    return [item for item in values if item not in invalid], invalid


class SymbolPicker(ctk.CTkFrame):
    """Shared ticker picker: one input plus removable chips.

    Both the connection popup's watchlist and the backtest's symbol list use
    this, so the two screens can never drift apart in look or in validation.
    """

    SURFACE_2 = PALETTE["SURFACE_2"]
    BORDER = PALETTE["BORDER"]
    MUTED = PALETTE["MUTED"]
    BLUE = PALETTE["BLUE"]
    RED = PALETTE["RED"]
    WARN = PALETTE["WARN"]

    def __init__(
        self,
        parent: Any,
        symbols: list[str] | None = None,
        *,
        columns: int = 8,
        placeholder: str = "FPT, SSI, VCB",
        on_change: Callable[[list[str]], None] | None = None,
        compact: bool = False,
        **kwargs: Any,
    ):
        super().__init__(parent, fg_color="transparent", **kwargs)
        # Compact drops the separate status line and shrinks the chips, for
        # forms where vertical space is scarce.
        self.compact = bool(compact)
        self.columns = max(1, int(columns))
        self.on_change = on_change
        self._symbols = list(dict.fromkeys(str(v).strip().upper() for v in (symbols or []) if str(v).strip()))
        self.grid_columnconfigure(0, weight=1)

        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        add_row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            add_row, height=38, placeholder_text=placeholder,
            fg_color=self.SURFACE_2, border_color=self.BORDER,
            font=("Cascadia Mono", 15),
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", lambda _event: self.add_from_entry(), add="+")
        ctk.CTkButton(
            add_row, text="+ THÊM", width=100, height=38, font=("Segoe UI", 13, "bold"),
            fg_color=self.BLUE, hover_color=PALETTE["BLUE_HOVER"], command=self.add_from_entry,
        ).grid(row=0, column=1)

        self.chips = ctk.CTkFrame(self, fg_color=self.SURFACE_2, corner_radius=8)
        self.chips.grid(row=1, column=0, sticky="ew")
        self.chips.grid_columnconfigure(tuple(range(self.columns)), weight=1)
        self.status = ctk.CTkLabel(
            self, text="", font=("Segoe UI", 13), text_color=self.MUTED, anchor="w",
        )
        if not self.compact:
            self.status.grid(row=2, column=0, sticky="w", pady=(3, 0))
        self.render()

    def get(self) -> list[str]:
        return list(self._symbols)

    def set(self, symbols: list[str]) -> None:
        self._symbols = list(dict.fromkeys(str(v).strip().upper() for v in symbols if str(v).strip()))
        self.render()

    def add_from_entry(self) -> None:
        symbols, invalid = parse_symbols(self.entry.get())
        if invalid:
            self.status.configure(text=f"MÃ KHÔNG HỢP LỆ: {', '.join(invalid)}", text_color=self.RED)
            return
        if not symbols:
            self.status.configure(text="HÃY NHẬP MÃ CKCS", text_color=self.WARN)
            return
        self._symbols = list(dict.fromkeys(self._symbols + symbols))
        self.entry.delete(0, "end")
        self.render()

    def remove(self, symbol: str) -> None:
        self._symbols = [value for value in self._symbols if value != symbol]
        self.render()

    def render(self) -> None:
        for child in self.chips.winfo_children():
            child.destroy()
        if not self._symbols:
            ctk.CTkLabel(
                self.chips, text="CHƯA CÓ MÃ", font=("Segoe UI", 13, "bold"), text_color=self.WARN,
            ).grid(row=0, column=0, sticky="w", padx=10, pady=10)
        for index, symbol in enumerate(self._symbols):
            ctk.CTkButton(
                self.chips, text=f"{symbol}  ×", width=98, height=30 if self.compact else 36,
                fg_color=PALETTE["BORDER"], hover_color=PALETTE["SLATE_HOVER"],
                font=("Cascadia Mono", 13, "bold"),
                command=lambda value=symbol: self.remove(value),
            ).grid(row=index // self.columns, column=index % self.columns,
                   sticky="ew", padx=4, pady=2 if self.compact else 5)
        self.status.configure(
            text=f"{len(self._symbols)} MÃ · CLICK × ĐỂ BỎ", text_color=self.MUTED,
        )
        if self.on_change:
            self.on_change(list(self._symbols))

class _StableToplevel(ctk.CTkToplevel):
    # CustomTkinter redraws the Windows title bar by withdrawing the window.
    # That conflicts with the app's own hide/show popup behaviour.
    _deactivate_windows_window_header_manipulation = True

def _window(parent: ctk.CTk, title: str, geometry: str = "560x420") -> ctk.CTkToplevel:
    top = _StableToplevel(parent)
    top.title(title)
    top.geometry(geometry)
    top.transient(parent)
    top.grab_set()
    top.grid_columnconfigure(0, weight=1)
    return top

class _HoverHint:
    def __init__(self, widget: Any, text: str, placement: str = "side"):
        self.widget, self.text, self.popup = widget, text, None
        self.placement = placement
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event: Any = None) -> None:
        if self.popup or not self.text:
            return
        # Keep the hint inside the same Tk window. On Windows with DPI scaling,
        # mixing winfo_root* coordinates with a new Toplevel can place the hint
        # hundreds of pixels away from its icon.
        host = self.widget.winfo_toplevel()
        self.popup = tk.Label(
            host, text=self.text, justify="left", wraplength=460,
            bg="#252A31", fg="#F5F7FA", padx=20, pady=16,
            font=("Segoe UI", 20), relief="solid", borderwidth=1,
        )
        self.popup.update_idletasks()
        width, height = self.popup.winfo_reqwidth(), self.popup.winfo_reqheight()
        host.update_idletasks()
        host_width = max(1, host.winfo_width())
        host_height = max(1, host.winfo_height())
        widget_x = self.widget.winfo_rootx() - host.winfo_rootx()
        widget_y = self.widget.winfo_rooty() - host.winfo_rooty()
        widget_width = self.widget.winfo_width()
        widget_height = self.widget.winfo_height()

        if self.placement in {"below", "inside"}:
            x = widget_x
            y = widget_y + widget_height + 8
            if y + height > host_height - 12:
                y = widget_y - height - 8
        else:
            x = widget_x + widget_width + 8
            if x + width > host_width - 12:
                # Keep the tooltip edge next to the icon instead of pinning the
                # tooltip to a distant screen corner.
                x = widget_x - width - 8
            y = widget_y - 4
            if y + height > host_height - 12:
                y = widget_y - height - 8

        x = min(max(8, x), max(8, host_width - width - 12))
        y = min(max(8, y), max(8, host_height - height - 12))
        self.popup.place(x=x, y=y)
        self.popup.lift()

    def _hide(self, _event: Any = None) -> None:
        if self.popup:
            self.popup.destroy()
            self.popup = None

class DataTablePopup:
    """Reusable, non-modal CKCS viewer used by the main toolbar.

    The window is deliberately detached from Tk's transient-window lifecycle:
    clicking the dashboard only withdraws it, and opening it again restores the
    same instance.  This matches the RULE/CONNECTION popup behaviour.
    """

    def __init__(
        self,
        parent: ctk.CTk,
        *,
        title: str,
        columns: tuple[tuple[str, str, int, str], ...],
        rows_provider: Callable[[str], list[tuple[str, tuple[Any, ...], tuple[str, ...]]]],
        initial_mode: str = "PAPER",
        summary_provider: Callable[[str], list[tuple[str, str, str]]] | None = None,
        on_visibility_changed: Callable[[bool], None] | None = None,
    ):
        self.parent = parent
        self.rows_provider = rows_provider
        self.summary_provider = summary_provider
        self.on_visibility_changed = on_visibility_changed
        self.columns = columns
        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        parent_w = max(0, int(parent.winfo_width() or 0))
        parent_h = max(0, int(parent.winfo_height() or 0))
        width = min(screen_w - 48, max(1080, parent_w - 36))
        height = min(screen_h - 80, max(650, parent_h - 52))
        x = max(16, int(parent.winfo_rootx()) + max(0, (parent_w - width) // 2))
        y = max(16, int(parent.winfo_rooty()) + max(0, (parent_h - height) // 2))
        self.top = _window(parent, f"VIKING · {title.upper()}", f"{width}x{height}+{x}+{y}")
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.tk.call("wm", "transient", self.top._w, "")
        self.top.resizable(True, True)
        self.top.minsize(900, 480)
        self.top.configure(fg_color=PALETTE["BG"])
        self.top.grid_columnconfigure(0, weight=1)
        self.top.grid_rowconfigure(2, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.hide(), add="+")

        header = ctk.CTkFrame(self.top, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 5))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text=title.upper(), font=("Segoe UI", 22, "bold"),
            text_color=PALETTE["TEXT"], anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header, text="↻  LÀM MỚI", width=126, height=36,
            font=("Segoe UI", 13, "bold"), fg_color=PALETTE["BLUE"],
            hover_color=PALETTE["BLUE_HOVER"], command=self.refresh,
        ).grid(row=0, column=1, sticky="e")

        self.summary = ctk.CTkFrame(
            self.top, height=78, fg_color=PALETTE["SURFACE"],
            corner_radius=10, border_width=1, border_color=PALETTE["BORDER"],
        )
        self.summary.grid(row=1, column=0, sticky="ew", padx=14, pady=(1, 5))
        self.summary.grid_propagate(False)
        self.summary_labels: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        for column in range(6):
            self.summary.grid_columnconfigure(column, weight=1, uniform="portfolio-summary")
            cell = ctk.CTkFrame(self.summary, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="nsew", padx=8, pady=8)
            label = ctk.CTkLabel(
                cell, text="--", font=("Segoe UI", 12, "bold"),
                text_color=PALETTE["MUTED"], anchor="center",
            )
            label.pack(fill="x")
            value = ctk.CTkLabel(
                cell, text="--", font=("Cascadia Mono", 16, "bold"),
                text_color=PALETTE["TEXT"], anchor="center",
            )
            value.pack(fill="x", pady=(2, 0))
            self.summary_labels.append((label, value))

        self.tabs = ctk.CTkTabview(
            self.top,
            command=self._refresh_summary,
            fg_color=PALETTE["PANEL"], border_width=1,
            border_color=PALETTE["BORDER"],
            segmented_button_selected_color=PALETTE["GREEN"],
            segmented_button_selected_hover_color=PALETTE["GREEN_HOVER"],
            segmented_button_unselected_color=PALETTE["SLATE"],
            segmented_button_unselected_hover_color=PALETTE["SLATE_HOVER"],
        )
        self.tabs.grid(row=2, column=0, sticky="nsew", padx=14, pady=(2, 14))
        try:
            self.tabs._segmented_button.configure(font=("Segoe UI", 14, "bold"))
        except AttributeError:
            pass

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "V2Popup.Treeview", background=PALETTE["SURFACE"], foreground=PALETTE["TEXT"],
            fieldbackground=PALETTE["SURFACE"], rowheight=48, font=("Segoe UI", 15),
            borderwidth=0, relief="flat",
        )
        style.layout("V2Popup.Treeview", [("V2Popup.Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "V2Popup.Treeview.Heading", background=PALETTE["SURFACE_2"],
            foreground=PALETTE["TEXT"], font=("Segoe UI", 15, "bold"),
            relief="flat", padding=(10, 11),
        )
        style.map(
            "V2Popup.Treeview",
            background=[("selected", "#2B6CB0")], foreground=[("selected", "#FFFFFF")],
        )
        for orientation in ("Vertical", "Horizontal"):
            style.configure(
                f"V2Popup.{orientation}.TScrollbar",
                background="#30353D", troughcolor="#181B20",
                bordercolor="#181B20", arrowcolor="#98A2B3",
                lightcolor="#30353D", darkcolor="#30353D",
            )

        self.trees: dict[str, ttk.Treeview] = {}
        self.empty_labels: dict[str, ctk.CTkLabel] = {}
        keys = tuple(item[0] for item in columns)
        for mode in ("REAL", "PAPER"):
            frame = self.tabs.add(f"CKCS {mode}")
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            tree = ttk.Treeview(
                frame, columns=keys, show="headings", selectmode="extended",
                style="V2Popup.Treeview",
            )
            for key, heading, width_px, anchor in columns:
                tree.heading(key, text=heading)
                tree.column(
                    key, width=width_px, minwidth=min(width_px, 72),
                    anchor=anchor, stretch=True,
                )
            tree.tag_configure("profit", background="#193524", foreground="#EAFBF0")
            tree.tag_configure("loss", background="#3A2024", foreground="#FFF1F2")
            tree.tag_configure("pending", background="#42351B", foreground="#FEF3C7")
            tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=(4, 0))
            yscroll = ttk.Scrollbar(
                frame, orient="vertical", command=tree.yview,
                style="V2Popup.Vertical.TScrollbar",
            )
            xscroll = ttk.Scrollbar(
                frame, orient="horizontal", command=tree.xview,
                style="V2Popup.Horizontal.TScrollbar",
            )
            yscroll.grid(row=0, column=1, sticky="ns", pady=(4, 0))
            xscroll.grid(row=1, column=0, sticky="ew", padx=(4, 0))
            tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            self.trees[mode] = tree
            empty = ctk.CTkLabel(
                frame, text="CHƯA CÓ DỮ LIỆU", font=("Segoe UI", 14, "bold"),
                text_color="#667085", fg_color="#22262D",
            )
            self.empty_labels[mode] = empty
        self.tabs.set("CKCS REAL" if str(initial_mode).upper() == "REAL" else "CKCS PAPER")
        self.refresh()
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

    def close(self) -> None:
        if self.on_visibility_changed:
            self.on_visibility_changed(False)
        if self.top.winfo_exists():
            self.top.destroy()

    def _active_mode(self) -> str:
        try:
            return "REAL" if self.tabs.get().upper().endswith("REAL") else "PAPER"
        except (AttributeError, tk.TclError):
            return "PAPER"

    def _refresh_summary(self) -> None:
        values = self.summary_provider(self._active_mode()) if self.summary_provider else []
        values = list(values[:6])
        while len(values) < 6:
            values.append(("", "", PALETTE["TEXT"]))
        for (label_widget, value_widget), (label, value, color) in zip(self.summary_labels, values):
            label_widget.configure(text=str(label), text_color=PALETTE["MUTED"])
            value_widget.configure(text=str(value), text_color=str(color or PALETTE["TEXT"]))

    def refresh(self) -> None:
        for mode, tree in self.trees.items():
            tree.delete(*tree.get_children())
            rows = self.rows_provider(mode)
            for iid, values, tags in rows:
                safe_iid = str(iid or "")
                kwargs: dict[str, Any] = {"values": values, "tags": tags}
                if safe_iid and not tree.exists(safe_iid):
                    kwargs["iid"] = safe_iid
                tree.insert("", "end", **kwargs)
            empty = self.empty_labels[mode]
            if rows:
                empty.place_forget()
            else:
                empty.place(relx=0.5, rely=0.5, anchor="center")
        self._refresh_summary()


class HistoryPopup:
    """Trade history grouped as day -> trade -> broker/local events.

    A BUY, a cancelled waiting SELL and the eventual filled SELL are three
    events of one trade, not three independent trades.  The hierarchy makes
    that relationship explicit while still preserving the audit trail.
    """

    COLUMNS = (
        ("time", "THỜI GIAN", 155, "center"),
        ("order", "ORDER ID", 145, "center"),
        ("price_qty", "GIÁ / KHỐI LƯỢNG", 210, "center"),
        ("cost", "PHÍ / THUẾ", 145, "center"),
        ("pnl", "PNL", 145, "center"),
        ("status", "TRẠNG THÁI", 170, "center"),
        ("note", "GHI CHÚ", 390, "w"),
    )

    def __init__(
        self,
        parent: ctk.CTk,
        rows_provider: Callable[[str], list[dict[str, Any]]],
        *,
        initial_mode: str = "PAPER",
        on_visibility_changed: Callable[[bool], None] | None = None,
        signals_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ):
        self.parent = parent
        self.rows_provider = rows_provider
        self.signals_provider = signals_provider
        self.on_visibility_changed = on_visibility_changed
        parent.update_idletasks()
        screen_w = max(1100, int(parent.winfo_screenwidth() or 1100))
        screen_h = max(700, int(parent.winfo_screenheight() or 700))
        width = min(screen_w - 48, max(1120, int(parent.winfo_width() or 0) - 36))
        height = min(screen_h - 80, max(680, int(parent.winfo_height() or 0) - 52))
        x = max(16, int(parent.winfo_rootx()) + max(0, (int(parent.winfo_width() or 0) - width) // 2))
        y = max(16, int(parent.winfo_rooty()) + max(0, (int(parent.winfo_height() or 0) - height) // 2))
        self.top = _window(parent, "VIKING · LỊCH SỬ", f"{width}x{height}+{x}+{y}")
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.tk.call("wm", "transient", self.top._w, "")
        self.top.resizable(True, True)
        self.top.minsize(980, 560)
        self.top.configure(fg_color=PALETTE["BG"])
        self.top.grid_columnconfigure(0, weight=1)
        self.top.grid_rowconfigure(1, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.hide(), add="+")

        header = ctk.CTkFrame(self.top, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="LỊCH SỬ GIAO DỊCH", font=("Segoe UI", 24, "bold"),
            text_color=PALETTE["TEXT"], anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.subtitle = ctk.CTkLabel(
            header, text="Mỗi ngày → mỗi giao dịch → các lần đặt/hủy/khớp",
            font=("Segoe UI", 15), text_color=PALETTE["MUTED"], anchor="w",
        )
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkButton(
            header, text="↻  LÀM MỚI", width=126, height=36,
            font=("Segoe UI", 13, "bold"), fg_color=PALETTE["BLUE"],
            hover_color=PALETTE["BLUE_HOVER"], command=self.refresh,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        self.tabs = ctk.CTkTabview(
            self.top, fg_color=PALETTE["PANEL"], border_width=1,
            border_color=PALETTE["BORDER"],
            segmented_button_selected_color=PALETTE["GREEN"],
            segmented_button_selected_hover_color=PALETTE["GREEN_HOVER"],
            segmented_button_unselected_color=PALETTE["SLATE"],
            segmented_button_unselected_hover_color=PALETTE["SLATE_HOVER"],
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 14))
        try:
            self.tabs._segmented_button.configure(font=("Segoe UI", 16, "bold"))
        except AttributeError:
            pass

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "History.Treeview", background=PALETTE["SURFACE"], foreground=PALETTE["TEXT"],
            fieldbackground=PALETTE["SURFACE"], rowheight=56,
            font=("Segoe UI", 17), borderwidth=0,
        )
        style.layout("History.Treeview", [("History.Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "History.Treeview.Heading", background=PALETTE["SURFACE_2"],
            foreground=PALETTE["TEXT"], font=("Segoe UI", 17, "bold"),
            relief="flat", padding=(10, 11),
        )
        style.map(
            "History.Treeview",
            background=[("selected", "#2B6CB0")], foreground=[("selected", "#FFFFFF")],
        )

        self.trees: dict[str, ttk.Treeview] = {}
        self.empty_labels: dict[str, ctk.CTkLabel] = {}
        keys = tuple(item[0] for item in self.COLUMNS)
        for mode in ("REAL", "PAPER"):
            frame = self.tabs.add(f"CKCS {mode}")
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(0, weight=1)
            tree = ttk.Treeview(
                frame, columns=keys, show="tree headings", selectmode="browse",
                style="History.Treeview",
            )
            tree.heading("#0", text="NGÀY / GIAO DỊCH / SỰ KIỆN", anchor="w")
            tree.column("#0", width=510, minwidth=360, anchor="w", stretch=True)
            for key, title, width_px, anchor in self.COLUMNS:
                tree.heading(key, text=title, anchor=anchor)
                tree.column(key, width=width_px, minwidth=min(width_px, 100), anchor=anchor, stretch=True)
            tree.tag_configure("day", background="#171B20", foreground="#FFFFFF", font=("Segoe UI", 18, "bold"))
            tree.tag_configure("trade_win", background="#173322", foreground="#EAFBF0", font=("Segoe UI", 17, "bold"))
            tree.tag_configure("trade_loss", background="#382126", foreground="#FFF1F2", font=("Segoe UI", 17, "bold"))
            tree.tag_configure("trade_open", background="#3C321B", foreground="#FEF3C7", font=("Segoe UI", 17, "bold"))
            tree.tag_configure("buy", foreground="#65D991")
            tree.tag_configure("sell", foreground="#FF8A8A")
            tree.tag_configure("cancelled", foreground="#F6C35B")
            tree.tag_configure("pending", foreground="#F6C35B")
            tree.grid(row=0, column=0, sticky="nsew", padx=(5, 0), pady=(5, 0))
            yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            yscroll.grid(row=0, column=1, sticky="ns", pady=(5, 0))
            xscroll.grid(row=1, column=0, sticky="ew", padx=(5, 0))
            tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            self.trees[mode] = tree
            self.empty_labels[mode] = ctk.CTkLabel(
                frame, text="CHƯA CÓ GIAO DỊCH", font=("Segoe UI", 18, "bold"),
                text_color=PALETTE["DIM"], fg_color=PALETTE["SURFACE"],
            )
        self._build_signal_tab()
        self.tabs.set("CKCS REAL" if str(initial_mode).upper() == "REAL" else "CKCS PAPER")
        self.refresh()
        self.show()

    SIGNAL_COLUMNS = (
        ("timestamp", "THỜI GIAN", 190, "center"),
        ("symbol", "MÃ", 100, "center"),
        ("signal", "TÍN HIỆU", 130, "center"),
        ("price", "GIÁ", 120, "center"),
        ("ema_fast", "EMA NHANH", 140, "center"),
        ("ema_slow", "EMA CHẬM", 140, "center"),
        ("rsi", "RSI", 100, "center"),
        ("market_state", "THỊ TRƯỜNG", 175, "center"),
        ("blocked_by", "BOT KHÔNG VÀO VÌ", 300, "w"),
    )

    def _build_signal_tab(self) -> None:
        """Every signal the rule produced, whether the bot could act or not."""
        frame = self.tabs.add("TÍN HIỆU")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        keys = tuple(item[0] for item in self.SIGNAL_COLUMNS)
        tree = ttk.Treeview(
            frame, columns=keys, show="headings", selectmode="browse",
            style="History.Treeview",
        )
        for key, title, width_px, anchor in self.SIGNAL_COLUMNS:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width_px, minwidth=min(width_px, 90), anchor=anchor, stretch=True)
        tree.tag_configure("buy", foreground="#65D991")
        tree.tag_configure("sell", foreground="#FF8A8A")
        tree.grid(row=0, column=0, sticky="nsew", padx=(5, 0), pady=(5, 0))
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        yscroll.grid(row=0, column=1, sticky="ns", pady=(5, 0))
        xscroll.grid(row=1, column=0, sticky="ew", padx=(5, 0))
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.signal_tree = tree
        self.signal_empty = ctk.CTkLabel(
            frame, text="CHƯA GHI ĐƯỢC TÍN HIỆU NÀO", font=("Segoe UI", 18, "bold"),
            text_color=PALETTE["DIM"], fg_color=PALETTE["SURFACE"],
        )
        ctk.CTkButton(
            frame, text="XUẤT EXCEL", width=150, height=36,
            font=("Segoe UI", 13, "bold"), fg_color=PALETTE["BLUE"],
            command=self._export_signals,
        ).grid(row=2, column=0, sticky="e", padx=(0, 6), pady=(6, 4))

    def _export_signals(self) -> None:
        """Write the signal log to a workbook next to the CSV it came from."""
        rows = list(self.signals_provider() if self.signals_provider else [])
        if not rows:
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError:
            return
        titles = [title for _key, title, *_rest in self.SIGNAL_COLUMNS]
        keys = [key for key, *_rest in self.SIGNAL_COLUMNS]
        book = Workbook()
        sheet = book.active
        sheet.title = "TÍN HIỆU"
        sheet.append(titles)
        for row in rows:
            sheet.append([row.get(key, "") for key in keys])
        head_fill = PatternFill("solid", fgColor="1B1F25")
        for cell in sheet[1]:
            cell.fill = head_fill
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center")
        sheet.freeze_panes = "A2"
        for column in range(1, sheet.max_column + 1):
            widest = max(
                (len(str(sheet.cell(row=r, column=column).value or ""))
                 for r in range(1, min(sheet.max_row, 400) + 1)),
                default=12,
            )
            sheet.column_dimensions[get_column_letter(column)].width = min(40, max(12, widest + 2))
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        path = Path.home() / "Downloads" / f"VIKING · TÍN HIỆU · {stamp}.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        book.save(path)
        self.subtitle.configure(text=f"Đã xuất {len(rows)} dòng ra {path}")


    def _refresh_signals(self) -> None:
        tree = getattr(self, "signal_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        tree.delete(*tree.get_children())
        rows = list(self.signals_provider() if self.signals_provider else [])
        for row in reversed(rows):
            signal = str(row.get("signal", "")).upper()
            tag = "buy" if signal == "BUY" else "sell" if signal == "SELL" else ""
            tree.insert(
                "", "end", tags=(tag,) if tag else (),
                values=tuple(row.get(key, "") for key, *_ in self.SIGNAL_COLUMNS),
            )
        if rows:
            self.signal_empty.place_forget()
        else:
            self.signal_empty.place(relx=0.5, rely=0.5, anchor="center")
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
        if self.on_visibility_changed:
            self.on_visibility_changed(False)
        if self.top.winfo_exists():
            self.top.destroy()

    def refresh(self) -> None:
        self._refresh_signals()
        for mode, tree in self.trees.items():
            tree.delete(*tree.get_children())
            groups = self.rows_provider(mode)
            for day in groups:
                day_id = tree.insert(
                    "", "end", text=str(day.get("label", "")),
                    values=tuple(day.get("values", ())), tags=("day",), open=True,
                )
                for trade in day.get("trades", []):
                    trade_id = tree.insert(
                        day_id, "end", text=str(trade.get("label", "")),
                        values=tuple(trade.get("values", ())),
                        tags=(str(trade.get("tag", "trade_open")),), open=True,
                    )
                    for event in trade.get("events", []):
                        tree.insert(
                            trade_id, "end", text=str(event.get("label", "")),
                            values=tuple(event.get("values", ())),
                            tags=(str(event.get("tag", "pending")),),
                        )
            empty = self.empty_labels[mode]
            if groups:
                empty.place_forget()
            else:
                empty.place(relx=0.5, rely=0.5, anchor="center")
