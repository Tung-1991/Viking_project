# -*- coding: utf-8 -*-
# FILE: ui_entry_exit_popup.py

import json
import os
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import config
import core.storage_manager as storage_manager


def _speed_up_scroll(frame, factor=5):
    canvas = getattr(frame, "_parent_canvas", None)
    if canvas is None:
        return frame

    def on_mousewheel(event):
        step = int(-1 * (event.delta / 120) * factor) if getattr(event, "delta", 0) else 0
        if step:
            canvas.yview_scroll(step, "units")
        return "break"

    def on_button4(_event):
        canvas.yview_scroll(-factor, "units")
        return "break"

    def on_button5(_event):
        canvas.yview_scroll(factor, "units")
        return "break"

    def activate(_event):
        frame.bind_all("<MouseWheel>", on_mousewheel)
        frame.bind_all("<Button-4>", on_button4)
        frame.bind_all("<Button-5>", on_button5)

    def deactivate(_event):
        frame.unbind_all("<MouseWheel>")
        frame.unbind_all("<Button-4>")
        frame.unbind_all("<Button-5>")

    frame.bind("<Enter>", activate, add="+")
    frame.bind("<Leave>", deactivate, add="+")
    return frame


COL_GREEN = "#00C853"
COL_WARN = "#FFAB00"
COL_GRAY_BTN = "#424242"
COL_ACCENT = "#00838F"
COL_PANEL = "#202020"
COL_FIELD = "#303437"

ENTRY_EXIT_TACTICS = {
    "FALLBACK_R": "R",
    "SWING_REJECTION": "SWING RETEST",
    "SWING_STRUCTURE": "SWING STRUCT",
    "FIB_RETRACE": "FIB",
    "PULLBACK_ZONE": "PULL",
}

MISSING_DISPLAY = {
    "FALLBACK_R": "Thiếu dữ liệu thì dùng R",
    "BLOCK": "Thiếu dữ liệu thì chặn lệnh",
}

TP_POLICY_DISPLAY = {
    "FALLBACK_R": "Ưu tiên R",
    "BOT_DEFAULT": "Theo cấu hình bot",
    "TACTIC_FIRST": "Ưu tiên chiến thuật, thiếu thì R",
    "TACTIC_ONLY": "Chỉ dùng chiến thuật",
}

SL_SOURCE_DISPLAY = {
    "BASE_SL": "Theo SL gốc của bot",
    "G0": "G0",
    "G1": "G1",
    "G2": "G2",
    "G3": "G3",
    "DYNAMIC-G1/G2": "Động: G1 khi trend, G2 khi còn lại",
}


def _display(mapping, value, default):
    return mapping.get(value, mapping.get(default, default))


def _value(mapping, display, default):
    reverse = {v: k for k, v in mapping.items()}
    return reverse.get(display, display if display in mapping else default)


def default_entry_exit_config():
    return {
        "enabled": True,
        "preview_only": False,
        "active_tactics": ["SWING_REJECTION"],
        "entry_tactics": ["SWING_REJECTION"],
        "exit_tactic": "SWING_REJECTION",
        "sl_mode": "SANDBOX",
        "fallback_tactic": "OFF",
        "signal_ttl_seconds": 900,
        "missing_data_policy": "BLOCK",
        "tp_policy": "TACTIC_FIRST",
        "sl_source_group": "BASE_SL",
        "default_exit": {
            "use_rr_tp": True,
            "tp_rr_ratio": 1.5,
            "use_swing_tp": False,
        },
        "swing_rejection": {
            "source_group": "G2",
            "max_atr_from_swing": 0.7,
            "sl_atr_buffer": 0.2,
            "require_rejection_candle": False,
            "allow_breakout_entry": False,
            "max_breakout_atr": 0.5,
        },
        "swing_structure": {
            "source_group": "G2",
            "entry_atr": 0.7,
            "sl_atr_buffer": 0.2,
            "allow_breakout_entry": True,
            "max_breakout_atr": 0.5,
        },
        "fib_retrace": {
            "swing_source_group": "G2",
            "entry_levels": "0.5,0.618",
            "entry_tolerance_atr": 0.15,
            "tp_levels": "1.272,1.618",
            "use_tactic_tp": True,
        },
        "pullback_zone": {
            "source": "EMA20",
            "max_atr_from_zone": 0.5,
            "sl_atr_buffer": 0.2,
            "tp_atr_multiplier": 1.5,
        },
    }


def merge_cfg(base, source):
    if not isinstance(source, dict):
        return base
    for key, val in source.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            merge_cfg(base[key], val)
        else:
            base[key] = val
    return base


def _load_global_payload():
    try:
        if os.path.exists(storage_manager.BRAIN_FILE):
            with open(storage_manager.BRAIN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _load_global_cfg():
    payload = _load_global_payload()
    return merge_cfg(default_entry_exit_config(), payload.get("entry_exit", {}))


def _save_global_cfg(cfg):
    payload = _load_global_payload()
    payload["entry_exit"] = cfg
    storage_manager.save_brain_settings(payload)


def _hint(parent, text):
    frame = ctk.CTkFrame(
        parent,
        fg_color="#2A250D",
        corner_radius=6,
        border_width=1,
        border_color="#FFD600",
    )
    frame.pack(fill="x", padx=10, pady=(8, 10))
    ctk.CTkLabel(
        frame,
        text=text,
        text_color="#FFD600",
        font=("Arial", 12, "italic"),
        justify="left",
        anchor="w",
        wraplength=760,
    ).pack(fill="x", padx=10, pady=6)


def _section(parent, title, color="#00B8D4"):
    frame = ctk.CTkFrame(parent, fg_color=COL_PANEL, corner_radius=8, border_width=1, border_color=color)
    frame.pack(fill="x", padx=10, pady=8)
    ctk.CTkLabel(frame, text=title, font=("Roboto", 13, "bold"), text_color=color).grid(
        row=0, column=0, columnspan=6, sticky="w", padx=12, pady=(8, 6)
    )
    frame.grid_columnconfigure(0, minsize=145)
    frame.grid_columnconfigure(1, minsize=170)
    frame.grid_columnconfigure(2, minsize=140)
    frame.grid_columnconfigure(3, minsize=170)
    frame.grid_columnconfigure(4, minsize=80)
    frame.grid_columnconfigure(5, weight=1)
    return frame


def _field(frame, row, label, variable, values=None, width=130, col=0):
    ctk.CTkLabel(frame, text=label, text_color="#FFFFFF", anchor="w").grid(row=row, column=col, sticky="ew", padx=12, pady=5)
    if values:
        widget = ctk.CTkOptionMenu(frame, values=values, variable=variable, width=width, fg_color="#1f538d", button_color="#16406D")
    else:
        widget = ctk.CTkEntry(frame, textvariable=variable, width=width, justify="center", fg_color=COL_FIELD, border_color="#56616A")
    widget.grid(row=row, column=col + 1, sticky="w", padx=8, pady=5)
    return widget


def open_entry_exit_popup(app, override_symbol=None):
    top = ctk.CTkToplevel(app)
    top.title(f"Entry/Exit: {override_symbol}" if override_symbol else "Entry/Exit Configuration")
    screen_w = max(800, int(top.winfo_screenwidth() or 800))
    screen_h = max(600, int(top.winfo_screenheight() or 600))
    width = min(880, max(720, screen_w - 60))
    height = min(760, max(500, screen_h - 100))
    top.geometry(
        f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}"
    )
    top.minsize(min(760, width), min(560, height))
    top.attributes("-topmost", True)
    top.focus_force()
    if override_symbol:
        top.grab_set()

    if override_symbol:
        from core.storage_manager import load_symbol_overrides

        overrides = load_symbol_overrides()
        cfg = merge_cfg(
            default_entry_exit_config(),
            overrides.get(override_symbol, {}).get("entry_exit", {}),
        )
    else:
        cfg = _load_global_cfg()

    tabview = ctk.CTkTabview(top)
    tabview.pack(fill="both", expand=True, padx=12, pady=(10, 4))
    tab_basic = _speed_up_scroll(ctk.CTkScrollableFrame(tabview.add("Cơ bản"), fg_color="transparent"))
    tab_advanced = _speed_up_scroll(ctk.CTkScrollableFrame(tabview.add("Nâng cao"), fg_color="transparent"))
    tab_basic.pack(fill="both", expand=True, padx=4, pady=4)
    tab_advanced.pack(fill="both", expand=True, padx=4, pady=4)

    if not override_symbol:
        tab_overwrite = _speed_up_scroll(ctk.CTkScrollableFrame(tabview.add("Overwrite"), fg_color="transparent"))
        tab_overwrite.pack(fill="both", expand=True, padx=4, pady=4)

    var_signal_ttl = tk.StringVar()
    var_preview_only = tk.BooleanVar(value=False)
    var_tp_policy = tk.StringVar()
    var_sl_source = tk.StringVar()
    var_tp_rr = tk.StringVar()
    var_swing_group = tk.StringVar()
    var_swing_max_atr = tk.StringVar()
    var_swing_sl_buffer = tk.StringVar()
    var_swing_reject = tk.BooleanVar()
    var_swing_breakout = tk.BooleanVar()
    var_swing_breakout_atr = tk.StringVar()
    var_struct_group = tk.StringVar()
    var_struct_entry_atr = tk.StringVar()
    var_struct_sl_buffer = tk.StringVar()
    var_struct_breakout = tk.BooleanVar()
    var_struct_breakout_atr = tk.StringVar()
    var_fib_group = tk.StringVar()
    var_fib_entry = tk.StringVar()
    var_fib_tolerance = tk.StringVar()
    var_fib_tp = tk.StringVar()
    var_pull_source = tk.StringVar()
    var_pull_max_atr = tk.StringVar()
    var_pull_sl_buffer = tk.StringVar()
    var_pull_tp_atr = tk.StringVar()

    def load_into_form(next_cfg):
        next_cfg = merge_cfg(default_entry_exit_config(), next_cfg)
        var_preview_only.set(bool(next_cfg.get("preview_only", False)))
        var_signal_ttl.set(str(next_cfg.get("signal_ttl_seconds", 900)))
        var_tp_policy.set(_display(TP_POLICY_DISPLAY, next_cfg.get("tp_policy", "FALLBACK_R"), "FALLBACK_R"))
        var_sl_source.set(_display(SL_SOURCE_DISPLAY, next_cfg.get("sl_source_group", "BASE_SL"), "BASE_SL"))
        var_tp_rr.set(str(next_cfg.get("default_exit", {}).get("tp_rr_ratio", 1.5)))

        swing = next_cfg.get("swing_rejection", {})
        var_swing_group.set(swing.get("source_group", "G2"))
        var_swing_max_atr.set(str(swing.get("max_atr_from_swing", 0.7)))
        var_swing_sl_buffer.set(str(swing.get("sl_atr_buffer", 0.2)))
        var_swing_reject.set(bool(swing.get("require_rejection_candle", False)))
        var_swing_breakout.set(bool(swing.get("allow_breakout_entry", False)))
        var_swing_breakout_atr.set(str(swing.get("max_breakout_atr", 0.5)))

        struct = next_cfg.get("swing_structure", {})
        var_struct_group.set(struct.get("source_group", "G2"))
        var_struct_entry_atr.set(str(struct.get("entry_atr", 0.7)))
        var_struct_sl_buffer.set(str(struct.get("sl_atr_buffer", 0.2)))
        var_struct_breakout.set(bool(struct.get("allow_breakout_entry", True)))
        var_struct_breakout_atr.set(str(struct.get("max_breakout_atr", 0.5)))

        fib = next_cfg.get("fib_retrace", {})
        var_fib_group.set(fib.get("swing_source_group", "G2"))
        var_fib_entry.set(fib.get("entry_levels", "0.5,0.618"))
        var_fib_tolerance.set(str(fib.get("entry_tolerance_atr", 0.15)))
        var_fib_tp.set(fib.get("tp_levels", "1.272,1.618"))

        pull = next_cfg.get("pullback_zone", {})
        var_pull_source.set(pull.get("source", "EMA20"))
        var_pull_max_atr.set(str(pull.get("max_atr_from_zone", 0.5)))
        var_pull_sl_buffer.set(str(pull.get("sl_atr_buffer", 0.2)))
        var_pull_tp_atr.set(str(pull.get("tp_atr_multiplier", 1.5)))

    def collect_form():
        try:
            tp_rr = float(var_tp_rr.get() or 1.5)
            swing_max_atr = float(var_swing_max_atr.get() or 0.7)
            swing_sl_buffer = float(var_swing_sl_buffer.get() or 0.2)
            swing_breakout_atr = float(var_swing_breakout_atr.get() or 0.5)
            struct_entry_atr = float(var_struct_entry_atr.get() or 0.7)
            struct_sl_buffer = float(var_struct_sl_buffer.get() or 0.2)
            struct_breakout_atr = float(var_struct_breakout_atr.get() or 0.5)
            fib_tolerance = float(var_fib_tolerance.get() or 0.15)
            pull_max_atr = float(var_pull_max_atr.get() or 0.5)
            pull_sl_buffer = float(var_pull_sl_buffer.get() or 0.2)
            pull_tp_atr = float(var_pull_tp_atr.get() or 1.5)
            signal_ttl = int(float(var_signal_ttl.get() or 900))
        except ValueError as exc:
            raise ValueError("Có ô số đang nhập sai định dạng.") from exc

        entry_active = list(cfg.get("entry_tactics", []))
        active = list(cfg.get("active_tactics", []))
        exit_tactic = cfg.get("exit_tactic", "AUTO")
        sl_mode = cfg.get("sl_mode", "SANDBOX")
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "preview_only": bool(var_preview_only.get()),
            "active_tactics": active,
            "entry_tactics": entry_active or ["SWING_REJECTION"],
            "exit_tactic": exit_tactic,
            "sl_mode": sl_mode,
            "fallback_tactic": "FALLBACK_R",
            "signal_ttl_seconds": signal_ttl,
            "missing_data_policy": cfg.get("missing_data_policy", "FALLBACK_R"),
            "tp_policy": _value(TP_POLICY_DISPLAY, var_tp_policy.get(), "FALLBACK_R"),
            "sl_source_group": _value(SL_SOURCE_DISPLAY, var_sl_source.get(), "BASE_SL"),
            "default_exit": {
                "use_rr_tp": exit_tactic not in ("NO_TP", "OFF"),
                "tp_rr_ratio": tp_rr,
                "use_swing_tp": exit_tactic in ("SWING_REJECTION", "SWING_STRUCTURE"),
            },
            "swing_rejection": {
                "source_group": var_swing_group.get() or "G2",
                "max_atr_from_swing": swing_max_atr,
                "sl_atr_buffer": swing_sl_buffer,
                "require_rejection_candle": bool(var_swing_reject.get()),
                "allow_breakout_entry": bool(var_swing_breakout.get()),
                "max_breakout_atr": swing_breakout_atr,
            },
            "swing_structure": {
                "source_group": var_struct_group.get() or "G2",
                "entry_atr": struct_entry_atr,
                "sl_atr_buffer": struct_sl_buffer,
                "allow_breakout_entry": bool(var_struct_breakout.get()),
                "max_breakout_atr": struct_breakout_atr,
            },
            "fib_retrace": {
                "swing_source_group": var_fib_group.get() or "G2",
                "entry_levels": var_fib_entry.get() or "0.5,0.618",
                "entry_tolerance_atr": fib_tolerance,
                "tp_levels": var_fib_tp.get() or "1.272,1.618",
                "use_tactic_tp": True,
            },
            "pullback_zone": {
                "source": var_pull_source.get() or "EMA20",
                "max_atr_from_zone": pull_max_atr,
                "sl_atr_buffer": pull_sl_buffer,
                "tp_atr_multiplier": pull_tp_atr,
            },
        }

    load_into_form(cfg)

    header = ctk.CTkFrame(tab_basic, fg_color="#202020", corner_radius=8, border_width=1, border_color="#00B8D4")
    header.pack(fill="x", padx=10, pady=(4, 8))
    ctk.CTkLabel(
        header,
        text="THAM SỐ ENTRY / EXIT",
        font=("Roboto", 16, "bold"),
        text_color="#00B8D4",
    ).pack(anchor="w", padx=12, pady=10)
    _hint(
        tab_basic,
        "Chọn tactic/SL/TP ở Sandbox. Công tắc CHỈ PREVIEW bên dưới quyết định E/E chỉ hiển thị hay thật sự chặn BOT tới khi READY.",
    )
    mode_frame = ctk.CTkFrame(
        tab_basic,
        fg_color="#202020",
        corner_radius=8,
        border_width=1,
        border_color="#FFCA28",
    )
    mode_frame.pack(fill="x", padx=10, pady=(0, 8))
    ctk.CTkCheckBox(
        mode_frame,
        text=(
            "CHỈ PREVIEW — bật: chỉ xem; "
            "tắt: BOT phải chờ E/E READY mới được vào lệnh"
        ),
        variable=var_preview_only,
        font=("Roboto", 12, "bold"),
        text_color="#FFCA28",
    ).pack(anchor="w", padx=12, pady=10)

    f_r = _section(tab_basic, "1. R / FALLBACK TP", "#FFD600")
    _field(f_r, 1, "Tín hiệu có hiệu lực:", var_signal_ttl, width=90)
    ctk.CTkLabel(f_r, text="giây", text_color="#B0BEC5").grid(row=1, column=2, sticky="w", padx=0, pady=5)
    _field(f_r, 2, "TP theo R:", var_tp_rr, width=80)
    ctk.CTkLabel(f_r, text="1.5 = lời 1.5R khi dùng R TP hoặc khi tactic TP thiếu dữ liệu", text_color="#B0BEC5").grid(row=2, column=2, columnspan=3, sticky="w", padx=8, pady=5)
    ctk.CTkLabel(
        f_r,
        text="Popup này chỉ chỉnh tham số tactic. Chọn Entry Mode, Missing Data policy, SL Mode và TP Mode ở Sandbox.",
        text_color="#B0BEC5",
        wraplength=820,
        justify="left",
    ).grid(row=3, column=0, columnspan=5, sticky="w", padx=12, pady=(0, 8))

    f_swing = _section(tab_basic, "2. SWING RETEST / RANGE", "#00B8D4")
    _field(f_swing, 1, "Group swing:", var_swing_group, ["G0", "G1", "G2", "G3"], width=120)
    _field(f_swing, 1, "Vùng hồi:", var_swing_max_atr, width=90, col=2)
    ctk.CTkLabel(f_swing, text="ATR", text_color="#B0BEC5").grid(row=1, column=4, sticky="w", padx=0, pady=5)
    _field(f_swing, 2, "SL buffer:", var_swing_sl_buffer, width=90)
    ctk.CTkLabel(f_swing, text="ATR, đặt SL quanh swing khi Entry dùng SWING", text_color="#B0BEC5").grid(row=2, column=2, columnspan=2, sticky="w", padx=0, pady=5)
    ctk.CTkCheckBox(f_swing, text="Yêu cầu nến từ chối", variable=var_swing_reject).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(4, 10))
    ctk.CTkCheckBox(f_swing, text="Vào nếu giá đã phá khỏi vùng Swing", variable=var_swing_breakout).grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
    _field(f_swing, 4, "Đuổi tối đa:", var_swing_breakout_atr, width=80, col=2)
    ctk.CTkLabel(f_swing, text="ATR", text_color="#B0BEC5").grid(row=4, column=4, sticky="w", padx=0, pady=5)
    ctk.CTkLabel(f_swing, text="BUY cần râu dưới; SELL cần râu trên tại vùng Swing. Bật phá vùng nếu muốn vào thuận chiều khi giá đã chạy qua vùng hồi.", text_color="#B0BEC5").grid(row=5, column=0, columnspan=5, sticky="w", padx=12, pady=(0, 10))

    f_struct = _section(tab_basic, "3. SWING STRUCTURE (HH/HL/LH/LL)", "#26A69A")
    _field(f_struct, 1, "Group structure:", var_struct_group, ["G0", "G1", "G2", "G3", "DYNAMIC-G1/G2"], width=150)
    _field(f_struct, 1, "Vùng entry:", var_struct_entry_atr, width=90, col=2)
    ctk.CTkLabel(f_struct, text="ATR quanh HL/LH", text_color="#B0BEC5").grid(row=1, column=4, sticky="w", padx=0, pady=5)
    _field(f_struct, 2, "SL buffer:", var_struct_sl_buffer, width=90)
    ctk.CTkCheckBox(f_struct, text="Cho entry breakout khi phá HH/LL", variable=var_struct_breakout).grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 8))
    _field(f_struct, 3, "Breakout tối đa:", var_struct_breakout_atr, width=80, col=2)
    ctk.CTkLabel(f_struct, text="ATR", text_color="#B0BEC5").grid(row=3, column=4, sticky="w", padx=0, pady=5)
    ctk.CTkLabel(f_struct, text="UP: BUY quanh HL hoặc phá HH. DOWN: SELL quanh LH hoặc phá LL.", text_color="#B0BEC5").grid(row=4, column=0, columnspan=5, sticky="w", padx=12, pady=(0, 10))

    _hint(tab_advanced, "FIB và Pullback là tactic nâng cao. Chỉ chỉnh khi Ngài muốn đổi vùng vào/TP hoặc độ rộng vùng hồi.")
    f_fib = _section(tab_advanced, "4. FIB ENTRY / FIB TP", "#AB47BC")
    _field(f_fib, 1, "Group swing:", var_fib_group, ["G0", "G1", "G2", "G3", "DYNAMIC-G1/G2"], width=150)
    _field(f_fib, 2, "Vùng vào:", var_fib_entry, width=130)
    _field(f_fib, 2, "Vùng TP:", var_fib_tp, width=130, col=2)
    _field(f_fib, 3, "Dung sai:", var_fib_tolerance, width=90)
    ctk.CTkLabel(f_fib, text="ATR", text_color="#B0BEC5").grid(row=3, column=2, sticky="w", padx=0, pady=5)
    ctk.CTkLabel(f_fib, text="Muốn dùng FIB làm TP thì chọn FIB TP ở Sandbox.", text_color="#B0BEC5").grid(row=4, column=0, columnspan=4, sticky="w", padx=12, pady=(4, 10))

    f_pull = _section(tab_advanced, "5. PULLBACK ENTRY", "#00B8D4")
    _field(f_pull, 1, "Nguồn vùng hồi:", var_pull_source, ["EMA20", "BB_MID", "SWING"], width=140)
    _field(f_pull, 1, "Độ rộng vùng:", var_pull_max_atr, width=90, col=2)
    ctk.CTkLabel(f_pull, text="ATR", text_color="#B0BEC5").grid(row=1, column=4, sticky="w", padx=0, pady=5)
    _field(f_pull, 2, "SL buffer:", var_pull_sl_buffer, width=90)
    ctk.CTkLabel(f_pull, text="ATR, đặt SL quanh vùng hồi khi Entry dùng PULLBACK", text_color="#B0BEC5").grid(row=2, column=2, columnspan=2, sticky="w", padx=0, pady=5)
    _field(f_pull, 3, "TP Pullback:", var_pull_tp_atr, width=90)
    ctk.CTkLabel(f_pull, text="ATR tính từ giá vào lệnh", text_color="#B0BEC5").grid(row=3, column=2, columnspan=2, sticky="w", padx=0, pady=5)

    if not override_symbol:
        _hint(
            tab_overwrite,
            "Override dùng khi một symbol cần E/E khác Global. EDIT để cấu hình riêng, RESET để quay về Global.",
        )
        f_overview = _section(tab_overwrite, "OVERRIDE OVERVIEW", "#00B8D4")
        ctk.CTkLabel(f_overview, text="Symbol", font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(f_overview, text="Status", font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=1, column=1, sticky="w", padx=12, pady=(0, 4))
        f_overview_rows = ctk.CTkFrame(f_overview, fg_color="transparent")
        f_overview_rows.grid(row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 8))

        def load_ee_for_sym(sym):
            open_entry_exit_popup(app, override_symbol=sym)

        def refresh_ee_override_overview():
            for child in f_overview_rows.winfo_children():
                child.destroy()
            from core.storage_manager import load_symbol_overrides

            overrides = load_symbol_overrides()
            symbol_values = list(getattr(config, "COIN_LIST", []) or [getattr(config, "DEFAULT_SYMBOL", "ETHUSD")])
            for row, sym in enumerate(symbol_values):
                has_override = bool(overrides.get(sym, {}).get("entry_exit"))
                row_frame = ctk.CTkFrame(f_overview_rows, fg_color="#2F2A12" if has_override else "#242424", corner_radius=6)
                row_frame.grid(row=row, column=0, sticky="ew", padx=2, pady=2)
                row_frame.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(row_frame, text=sym, width=110, anchor="w", font=("Roboto", 12, "bold"), text_color="#FFFFFF").grid(row=0, column=0, sticky="w", padx=10, pady=6)
                ctk.CTkLabel(row_frame, text="OVERRIDE" if has_override else "GLOBAL", anchor="w", font=("Roboto", 11, "bold"), text_color=COL_WARN if has_override else "#B0BEC5").grid(row=0, column=1, sticky="w", padx=8, pady=6)
                ctk.CTkButton(row_frame, text="EDIT" if has_override else "SELECT", width=76, height=24, fg_color="#1f538d" if has_override else COL_GRAY_BTN, hover_color="#14375e" if has_override else "#616161", command=lambda s=sym: load_ee_for_sym(s)).grid(row=0, column=2, sticky="e", padx=(4, 6), pady=5)
                ctk.CTkButton(row_frame, text="RESET", width=70, height=24, fg_color="#B71C1C" if has_override else "#303030", hover_color="#7F0000" if has_override else "#303030", state="normal" if has_override else "disabled", command=lambda s=sym: reset_ee_for_sym(s)).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=5)

        def reset_ee_for_sym(sym):
            from core.storage_manager import load_symbol_overrides, save_symbol_overrides

            overrides = load_symbol_overrides()
            if sym in overrides and "entry_exit" in overrides[sym]:
                del overrides[sym]["entry_exit"]
                if not overrides[sym]:
                    del overrides[sym]
                save_symbol_overrides(overrides)
                if hasattr(app, "log_message"):
                    app.log_message(f"Đã reset Entry/Exit Override cho {sym}.", target="bot")
                refresh_ee_override_overview()

        refresh_ee_override_overview()

    def save_cfg():
        try:
            next_cfg = collect_form()
        except ValueError as exc:
            messagebox.showerror("Entry/Exit", str(exc), parent=top)
            return

        if override_symbol:
            from core.storage_manager import load_symbol_overrides, save_symbol_overrides

            overrides = load_symbol_overrides()
            overrides.setdefault(override_symbol, {})["entry_exit"] = next_cfg
            save_symbol_overrides(overrides)
            if hasattr(app, "log_message"):
                app.log_message(f"Đã lưu Entry/Exit riêng cho {override_symbol}.", target="bot")
            top.destroy()
            return

        _save_global_cfg(next_cfg)
        if hasattr(app, "entry_exit_tactic_states"):
            for key in app.entry_exit_tactic_states:
                app.entry_exit_tactic_states[key] = key in next_cfg.get("active_tactics", [])
        if hasattr(app, "update_entry_exit_buttons_ui"):
            app.update_entry_exit_buttons_ui()
        if hasattr(app, "log_message"):
            app.log_message("Đã lưu cấu hình Entry/Exit Global.", target="bot")
        top.destroy()

    ctk.CTkButton(
        top,
        text=f"SAVE ENTRY/EXIT {override_symbol}" if override_symbol else "SAVE ENTRY/EXIT GLOBAL",
        command=save_cfg,
        fg_color="#7B1FA2",
        hover_color="#006064",
        height=38,
        font=("Roboto", 13, "bold"),
    ).pack(fill="x", padx=20, pady=(4, 12))
