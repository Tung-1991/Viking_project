# -*- coding: utf-8 -*-
# FILE: ui_panels.py
# V8.4.1: UPDATED UI PANELS - OPTIMIZED TOP HEADER & CONTEXT PREVIEW (KAISER EDITION)

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import config
from core.money import money_unit_note

# --- HẰNG SỐ UI ---
FONT_MAIN = ("Roboto", 13)
FONT_BOLD = ("Roboto", 13, "bold")
FONT_EQUITY = ("Roboto", 29, "bold")
FONT_PNL = ("Roboto", 17, "bold")
FONT_SECTION = ("Roboto", 12, "bold")
FONT_BIG_VAL = ("Consolas", 18, "bold")
FONT_PRICE = ("Roboto", 23, "bold")
FONT_FEE = ("Roboto", 13, "bold")

COL_GREEN = "#00C853"
COL_RED = "#D50000"
COL_BLUE_ACCENT = "#0D47A1"
COL_BLUE_ACCENT_HOVER = "#0A3578"
COL_WARN = "#FFAB00"
COL_BOT_TAG = "#E040FB"
COL_SETTING = "#0E7490"
COL_SETTING_HOVER = "#155E75"


def setup_left_panel(app, parent):
    """Xây dựng toàn bộ thanh điều khiển bên trái"""

    # 1. TOP HEADER (Equity & Info) - COMPACT 2 CỘT
    f_top = ctk.CTkFrame(parent, fg_color="#1a1a1a", corner_radius=8)
    f_top.pack(fill="x", pady=(3, 3), padx=5)
    # Giữ đủ chỗ cho PNL · Phí · nút reset; nếu cột trái bị ép quá hẹp
    # CustomTkinter sẽ co nút cuối thành một vệt xám không còn thấy icon.
    f_top.grid_columnconfigure(0, weight=1, minsize=340)
    f_top.grid_columnconfigure(1, weight=0)

    # Cột trái: Equity (to) · ID·Server · PNL·Phí·reset
    f_left = ctk.CTkFrame(f_top, fg_color="transparent")
    f_left.grid(row=0, column=0, sticky="nw", padx=(8, 4), pady=2)
    app.lbl_equity = ctk.CTkLabel(f_left, text="----", font=FONT_EQUITY, text_color=COL_GREEN)
    app.lbl_equity.pack(anchor="w")
    app.lbl_acc_info = ctk.CTkLabel(
        f_left, text="ID: ---  ·  ---", font=("Roboto", 9), text_color="white", anchor="w",
    )
    app.lbl_acc_info.pack(anchor="w", pady=(0, 1))
    # Tiền mặt / Giá trị CP -> chuyển hết vào popup "Danh mục CP" cho gọn header.
    f_pnl = ctk.CTkFrame(f_left, fg_color="transparent")
    f_pnl.pack(anchor="w", fill="x")
    app.lbl_stats = ctk.CTkLabel(f_pnl, text="PNL: 0", font=FONT_PNL, text_color="white")
    app.lbl_stats.pack(side="left", padx=(0, 12))
    app.lbl_fee_today = ctk.CTkLabel(f_pnl, text="Phí: 0", font=FONT_FEE, text_color="white")
    app.lbl_fee_today.pack(side="left")
    app.btn_reset_daily_stats = ctk.CTkButton(
        f_pnl,
        text="↻",
        width=34,
        height=22,
        font=("Segoe UI Symbol", 14, "bold"),
        fg_color="#333",
        hover_color="#444",
        command=app.reset_daily_stats,
    )
    app.btn_reset_daily_stats.pack(side="left", padx=(10, 0))

    # Cột phải (dồn lên): Đơn vị tiền · PHIÊN · BRAIN
    f_right = ctk.CTkFrame(f_top, fg_color="transparent")
    f_right.grid(row=0, column=1, sticky="ne", padx=(4, 8), pady=2)
    app.lbl_money_unit_note = ctk.CTkLabel(
        f_right, text=money_unit_note(), font=("Roboto", 9, "bold"), text_color="white", anchor="e",
    )
    app.lbl_money_unit_note.pack(anchor="e")
    app.lbl_session = ctk.CTkLabel(f_right, text="PHIÊN: --", font=("Roboto", 11, "bold"), text_color="#90A4AE", anchor="e")
    app.lbl_session.pack(anchor="e", pady=(2, 0))
    app.lbl_brain_status = ctk.CTkLabel(f_right, text="BRAIN: CHỜ...", font=("Roboto", 11, "bold"), text_color="#FF8F00", anchor="e")
    app.lbl_brain_status.pack(anchor="e", pady=(2, 0))

    # 2. SETTINGS PANEL
    f_set = ctk.CTkFrame(parent, fg_color="#1f1f1f", corner_radius=8)
    f_set.pack(fill="x", padx=5, pady=(5, 6))
    f_set.columnconfigure(0, minsize=42)
    f_set.columnconfigure(1, weight=1)

    def setting_label(row, text):
        ctk.CTkLabel(
            f_set,
            text=text,
            font=("Roboto", 11, "bold"),
            text_color="white",
            anchor="e",
        ).grid(row=row, column=0, sticky="e", padx=(6, 6), pady=2)

    def setting_row(row):
        frame = ctk.CTkFrame(f_set, fg_color="transparent")
        frame.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=2)
        return frame

    def stretch_columns(frame, widths):
        for col, width in enumerate(widths):
            frame.grid_columnconfigure(col, weight=1, minsize=width)

    def set_force_button_state():
        enabled = app.var_bypass_checklist.get()
        app.btn_force.configure(
            text="⚠ BYPASS" if enabled else "BYPASS",
            fg_color="#EF6C00" if enabled else "#424242",
            hover_color="#E65100" if enabled else "#616161",
        )

    def toggle_force_button():
        app.var_bypass_checklist.set(not app.var_bypass_checklist.get())
        set_force_button_state()

    setting_label(0, "Mã CK")
    f_coin_row = setting_row(0)
    stretch_columns(f_coin_row, (112, 154))
    
    app.cbo_symbol = ctk.CTkOptionMenu(
        f_coin_row,
        values=["VN30F1M"],
        font=FONT_BOLD,
        width=112,
        height=30,
        command=app.on_symbol_change,
    )
    app.cbo_symbol.set("VN30F1M")
    app.cbo_symbol.grid(row=0, column=0, sticky="ew", padx=(0, 6))

    app.cbo_market_type = ctk.CTkOptionMenu(
        f_coin_row,
        values=["CK Phái Sinh", "CK Cơ Sở"],
        font=FONT_MAIN,
        width=154,
        height=30,
        command=app.on_market_type_change,
    )
    app.cbo_market_type.set("CK Phái Sinh")
    app.cbo_market_type.grid(row=0, column=1, sticky="ew")

    setting_label(1, "MODE")
    f_account_row = setting_row(1)
    stretch_columns(f_account_row, (124, 82, 100))
    
    app.seg_paper_mode = ctk.CTkSegmentedButton(
        f_account_row,
        values=["REAL", "PAPER"],
        font=FONT_BOLD,
        width=124,
        height=30,
        command=app.on_paper_mode_change,
        selected_color="#D32F2F",  # Red for REAL initially (will update dynamically)
        selected_hover_color="#B71C1C",
    )
    # Default to PAPER
    current_mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
    app.seg_paper_mode.set(current_mode)
    app.seg_paper_mode.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        f_account_row,
        text="\u2699 MANUAL PRESET",
        width=92,
        height=30,
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_preset_config_popup,
    ).grid(row=0, column=2, sticky="ew")
    app.btn_force = ctk.CTkButton(
        f_account_row,
        text="BYPASS",
        font=("Roboto", 10, "bold"),
        text_color="white",
        width=82,
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=toggle_force_button,
    )
    app.btn_force.grid(row=0, column=1, sticky="ew", padx=(0, 6))
    set_force_button_state()

    setting_label(2, "TSL")
    f_tsl_row = setting_row(2)
    stretch_columns(f_tsl_row, (36, 40, 46, 54, 48, 48, 42))
    app.btn_tactic_be = ctk.CTkButton(
        f_tsl_row, text="BE", width=36, height=30, command=lambda: app.toggle_tactic("BE")
    )
    app.btn_tactic_be.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    app.btn_tactic_pnl = ctk.CTkButton(
        f_tsl_row, text="PNL", width=40, height=30, command=lambda: app.toggle_tactic("PNL")
    )
    app.btn_tactic_pnl.grid(row=0, column=1, sticky="ew", padx=3)
    app.btn_tactic_step = ctk.CTkButton(
        f_tsl_row, text="STEP", width=46, height=30, command=lambda: app.toggle_tactic("STEP_R")
    )
    app.btn_tactic_step.grid(row=0, column=2, sticky="ew", padx=3)
    app.btn_tactic_swing = ctk.CTkButton(
        f_tsl_row, text="SWING", width=54, height=30, command=lambda: app.toggle_tactic("SWING")
    )
    app.btn_tactic_swing.grid(row=0, column=3, sticky="ew", padx=3)
    app.btn_tactic_cash = ctk.CTkButton(
        f_tsl_row, text="CASH", width=48, height=30, command=lambda: app.toggle_tactic("BE_CASH")
    )
    app.btn_tactic_cash.grid(row=0, column=4, sticky="ew", padx=3)
    app.btn_tactic_psar = ctk.CTkButton(
        f_tsl_row, text="PSAR", width=48, height=30, command=lambda: app.toggle_tactic("PSAR_TRAIL")
    )
    app.btn_tactic_psar.grid(row=0, column=5, sticky="ew", padx=3)
    ctk.CTkButton(
        f_tsl_row,
        text="TSL",
        width=42,
        height=30,
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_tsl_popup,
    ).grid(row=0, column=6, sticky="ew", padx=(3, 0))

    # Manual Entry/Exit quick modes. These buttons only drive Manual
    # Preview/Preset; BOT runtime keeps its own Entry/Exit configuration.
    setting_label(3, "E/E")
    f_entry = setting_row(3)
    stretch_columns(f_entry, (36, 62, 62, 40, 42, 42))
    app.btn_manual_entry_r = ctk.CTkButton(
        f_entry,
        text="R",
        width=36,
        height=30,
        command=lambda: app.set_manual_entry_exit_quick_mode("R"),
    )
    app.btn_manual_entry_r.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    app.btn_manual_entry_retest = ctk.CTkButton(
        f_entry,
        text="RETEST",
        width=62,
        height=30,
        command=lambda: app.set_manual_entry_exit_quick_mode("RETEST"),
    )
    app.btn_manual_entry_retest.grid(row=0, column=1, sticky="ew", padx=3)
    app.btn_manual_entry_struct = ctk.CTkButton(
        f_entry,
        text="STRUCT",
        width=62,
        height=30,
        command=lambda: app.set_manual_entry_exit_quick_mode("STRUCT"),
    )
    app.btn_manual_entry_struct.grid(row=0, column=2, sticky="ew", padx=3)
    app.btn_manual_entry_fib = ctk.CTkButton(
        f_entry,
        text="FIB",
        width=40,
        height=30,
        command=lambda: app.set_manual_entry_exit_quick_mode("FIB"),
    )
    app.btn_manual_entry_fib.grid(row=0, column=3, sticky="ew", padx=3)
    app.btn_manual_entry_pull = ctk.CTkButton(
        f_entry,
        text="PULL",
        width=42,
        height=30,
        command=lambda: app.set_manual_entry_exit_quick_mode("PULL"),
    )
    app.btn_manual_entry_pull.grid(row=0, column=4, sticky="ew", padx=3)
    ctk.CTkButton(
        f_entry,
        text="EE",
        width=42,
        height=30,
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_preset_config_popup,
    ).grid(row=0, column=5, sticky="ew", padx=(3, 0))

    setting_label(4, "DEF")
    f_def = setting_row(4)
    stretch_columns(f_def, (50, 50, 50, 58, 110))
    app.btn_tactic_dca = ctk.CTkButton(
        f_def, text="DCA", width=50, height=30, command=lambda: app.toggle_tactic("AUTO_DCA")
    )
    app.btn_tactic_dca.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    app.btn_tactic_pca = ctk.CTkButton(
        f_def, text="PCA", width=50, height=30, command=lambda: app.toggle_tactic("AUTO_PCA")
    )
    app.btn_tactic_pca.grid(row=0, column=1, sticky="ew", padx=3)
    app.btn_tactic_rev_c = ctk.CTkButton(
        f_def, text="REV", width=50, height=30, command=lambda: app.toggle_tactic("REV_C")
    )
    app.btn_tactic_rev_c.grid(row=0, column=2, sticky="ew", padx=3)
    app.btn_tactic_anti_cash = ctk.CTkButton(
        f_def, text="A.CUT", width=58, height=30, command=lambda: app.toggle_tactic("ANTI_CASH")
    )
    app.btn_tactic_anti_cash.grid(row=0, column=3, sticky="ew", padx=3)
    ctk.CTkButton(
        f_def,
        text="\u2699 AI ADVISOR",
        width=110,
        height=30,
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_advisor_popup,
    ).grid(row=0, column=4, sticky="ew", padx=(3, 0))

    setting_label(5, "BOT")
    f_bot_row = setting_row(5)
    stretch_columns(f_bot_row, (20, 88, 96, 96))
    # [2-BOT] Hai đèn riêng: PS = Phái sinh (CKPS), CS = Cơ sở (CKCS).
    f_lights = ctk.CTkFrame(f_bot_row, fg_color="transparent")
    f_lights.grid(row=0, column=0, padx=(0, 6))
    ctk.CTkLabel(f_lights, text="PS", font=("Roboto", 8, "bold"), text_color="gray").grid(row=0, column=0, padx=(0, 1))
    app.ind_light_ckps = ctk.CTkFrame(f_lights, width=12, height=12, corner_radius=6, fg_color=COL_RED)
    app.ind_light_ckps.grid(row=0, column=1, padx=(0, 5))
    ctk.CTkLabel(f_lights, text="CS", font=("Roboto", 8, "bold"), text_color="gray").grid(row=0, column=2, padx=(0, 1))
    app.ind_light_ckcs = ctk.CTkFrame(f_lights, width=12, height=12, corner_radius=6, fg_color=COL_RED)
    app.ind_light_ckcs.grid(row=0, column=3)
    # Đèn tổng (legacy) — giữ widget cho code cũ tham chiếu, không hiển thị.
    app.ind_auto_light = ctk.CTkFrame(f_lights, width=1, height=1, corner_radius=1, fg_color=COL_RED)
    ctk.CTkButton(
        f_bot_row,
        text="\u2699 BOT",
        width=88,
        height=30,
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_bot_setting_popup,
    ).grid(row=0, column=1, sticky="ew", padx=(0, 3))
    app.btn_strategy = ctk.CTkButton(
        f_bot_row,
        text="\u2699 SANDBOX",
        width=96,
        height=30,
        font=("Roboto", 11, "bold"),
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_strategy_sandbox,
    )
    app.btn_strategy.grid(row=0, column=2, sticky="ew", padx=(3, 0))

    ctk.CTkButton(
        f_bot_row,
        text="⚙ ADVANCED",
        width=96,
        height=30,
        font=("Roboto", 9, "bold"),
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
        command=app.open_advanced_tools_popup,
    ).grid(row=0, column=3, sticky="ew", padx=(6, 0))

    app.update_tactic_buttons_ui()
    app.update_entry_exit_buttons_ui()
    app.update_manual_entry_exit_buttons_ui()

    # 3. MANUAL INPUT PANEL
    f_input = ctk.CTkFrame(parent, fg_color="transparent")
    f_input.pack(fill="x", padx=5, pady=(2, 0))
    for column in range(4):
        f_input.grid_columnconfigure(
            column,
            weight=1,
            uniform="manual_input",
        )

    def make_inp(p, t, v, c):
        # CTkFrame/CTkEntry mặc định đòi chiều rộng riêng khá lớn. Bốn ô cộng
        # lại sẽ đẩy hàng nhập vượt khỏi canvas và làm hai mép bị che.
        f = ctk.CTkFrame(
            p,
            width=1,
            fg_color="#2b2b2b",
            corner_radius=6,
        )
        f.grid(row=0, column=c, padx=3, sticky="ew")
        lbl = ctk.CTkLabel(f, text=t, font=("Roboto", 10, "bold"), text_color="white")
        lbl.pack(pady=(0, 0))
        ctk.CTkEntry(
            f,
            textvariable=v,
            width=1,
            font=("Consolas", 14, "bold"),
            height=26,
            justify="center",
            fg_color="transparent",
            border_width=0,
        ).pack(fill="x")
        return lbl

    app.lbl_manual_qty_title = make_inp(f_input, "Hợp đồng", app.var_manual_lot, 0)
    make_inp(f_input, "Giá vào (LO)", app.var_manual_entry, 1)
    make_inp(f_input, "TP (Price)", app.var_manual_tp, 2)
    make_inp(f_input, "SL (Price)", app.var_manual_sl, 3)
    for _manual_var in (app.var_manual_lot, app.var_manual_entry, app.var_manual_tp, app.var_manual_sl):
        try:
            _manual_var.trace_add("write", app.on_manual_input_change)
        except Exception:
            pass

    # --- PHẦN ĐÃ FIX: MULTI-TF CONTEXT PREVIEW (V8.4.1) ---
    f_context = ctk.CTkFrame(parent, fg_color="#1E1E1E", corner_radius=6)
    f_context.pack(fill="x", padx=5, pady=(5, 5))

    # Dòng 1: Chế độ (Mode) & Xu hướng (Trend)
    app.lbl_market_mode = ctk.CTkLabel(
        f_context,
        text="Mode: -- | Trend: --",
        font=("Roboto", 13, "bold"),
        text_color="#29B6F6",
        anchor="w",
    )
    app.lbl_market_mode.pack(fill="x", padx=10, pady=(5, 0))

    # Dòng 2: Khung chứa Dropdown chọn G và Thông số H/L/ATR
    f_context_bottom = ctk.CTkFrame(f_context, fg_color="transparent")
    f_context_bottom.pack(fill="x", padx=5, pady=(2, 5))

    app.var_dashboard_tf = tk.StringVar(value="G1")
    app.cbo_dashboard_tf = ctk.CTkOptionMenu(
        f_context_bottom,
        values=["G0", "G1", "G2", "G3"],
        variable=app.var_dashboard_tf,
        width=60,
        height=24,
        font=("Roboto", 11, "bold"),
    )
    app.cbo_dashboard_tf.pack(side="left", padx=5)

    # Label DUY NHẤT để hiển thị Swing/ATR (Xóa các bản trùng lặp cũ)
    # Label DUY NHẤT để hiển thị Swing/ATR
    app.lbl_market_context = ctk.CTkLabel(
        f_context_bottom,
        text="H: -- | L: -- | ATR: --",
        font=("Consolas", 14, "bold"),
        text_color="#78909C",
        anchor="w",
    )
    app.lbl_market_context.pack(side="left", fill="x", expand=True, padx=5)
    f_context.pack_forget()

    _unified = bool(getattr(config, "UNIFIED_ORDER_BUTTON", True))
    # [GỘP NÚT] Tick "Thị trường" + dropdown ATO/ATC nằm ở CỘT TRÁI của giá (trong dashboard).
    # Nút EXECUTE BUY giữ nguyên vị trí cũ (ngay dưới ô nhập).

    app.btn_action = ctk.CTkButton(
        parent,
        text="EXECUTE BUY",
        font=("Roboto", 14, "bold"),
        height=30,
        fg_color=COL_GREEN,
        hover_color="#009624",
        command=app.on_click_smart_order if _unified else app.on_click_trade,
    )
    app.btn_action.pack(fill="x", padx=10, pady=(2, 3))

    # Nút "LIMIT ORDER" cũ — ẩn khi gộp nút (giữ widget để code cũ không lỗi).
    app.btn_schedule_order = ctk.CTkButton(
        parent,
        text="LIMIT ORDER",
        font=("Roboto", 13, "bold"),
        height=30,
        fg_color="#455A64",
        hover_color="#546E7A",
        command=app.on_click_schedule_order,
    )
    if not _unified:
        app.btn_schedule_order.pack(fill="x", padx=10, pady=(0, 4))

    # 4. LIVE DASHBOARD
    f_dashboard = ctk.CTkFrame(
        parent, fg_color="#252526", corner_radius=8, border_width=1, border_color="#333"
    )
    f_dashboard.pack(fill="x", padx=5, pady=(4, 8))

    f_head_db = ctk.CTkFrame(f_dashboard, fg_color="transparent")
    f_head_db.pack(fill="x", padx=10, pady=(4, 0))
    app.lbl_prev_lot = ctk.CTkLabel(
        f_head_db, text="HĐ: 0", font=FONT_BOLD, text_color="#FFD700"
    )
    app.lbl_prev_lot.pack(side="left")
    app.lbl_limit_order_hint = ctk.CTkLabel(
        f_head_db,
        text="",
        font=("Roboto", 10),
        text_color="#B0BEC5",
        anchor="center",
        justify="left",
        width=260,
    )
    app.lbl_limit_order_hint.pack_forget()
    # Trạng thái T+2 (Đã về/Chờ về) hiển thị TRONG bảng lệnh theo từng vị thế, không ở header.
    app.lbl_fee_info = ctk.CTkLabel(
        f_head_db, text="Phí: 0", font=FONT_FEE, text_color="#FFD700"
    )
    app.lbl_fee_info.pack(side="right")
    f_price_row = ctk.CTkFrame(f_dashboard, fg_color="transparent")
    f_price_row.pack(fill="x", padx=8, pady=(4, 2))
    f_price_row.grid_columnconfigure(0, minsize=116)
    f_price_row.grid_columnconfigure(1, weight=1)
    f_price_row.grid_columnconfigure(2, minsize=106)

    app.frame_trade_mode = ctk.CTkFrame(
        f_price_row, fg_color="#424242", corner_radius=6
    )
    app.frame_trade_mode.grid(row=0, column=0, sticky="nw", padx=(0, 8))
    if _unified:
        # [GỘP NÚT] Bên trái giá: dropdown Hẹn phiên ATO/ATC, giữ layout gọn như bản cũ.
        app.cbo_schedule_session = ctk.CTkOptionMenu(
            app.frame_trade_mode,
            values=["⚡ NORMAL", "☀️ ATO", "🌙 ATC"],
            width=104,
            height=26,
            font=("Roboto", 11, "bold"),
            fg_color="#00838F",
            button_color="#006064",
            command=app.on_schedule_session_change,
        )
        app.cbo_schedule_session.set("⚡ NORMAL")
        app.cbo_schedule_session.pack(fill="x", padx=4, pady=4)
    else:
        # Kiểu lệnh cũ: NORMAL (liên tục) | ATO (mở cửa) | ATC (đóng cửa).
        app.cbo_trade_mode = ctk.CTkOptionMenu(
            app.frame_trade_mode,
            values=["NORMAL", "ATO", "ATC"],
            variable=app.var_manual_trade_mode,
            width=98,
            height=24,
            font=("Roboto", 10, "bold"),
            fg_color="#00838F",
            button_color="#006064",
            command=app.on_manual_trade_mode_change,
        )
        app.cbo_trade_mode.pack(fill="x", padx=4, pady=4)

    app.lbl_dashboard_price = ctk.CTkLabel(
        f_price_row, text="----.--", font=("Roboto", 28, "bold"), text_color="white"
    )
    app.lbl_dashboard_price.grid(row=0, column=1, sticky="ew", padx=(0, 0))
    # [GỘP NÚT] Tick "Thị trường" nằm dưới giá, đúng bố cục cũ: trái ATO, giữa giá, phải BUY/SELL.
    if _unified:
        app.chk_market_order = ctk.CTkCheckBox(
            f_price_row,
            text="Thị trường",
            variable=app.var_manual_market,
            font=("Roboto", 10, "bold"),
            checkbox_width=16,
            checkbox_height=16,
            command=app.refresh_limit_order_hint,
        )
        app.chk_market_order.grid(row=1, column=1, pady=(0, 0))

    app.frame_direction = ctk.CTkFrame(f_price_row, fg_color="#424242", corner_radius=6)
    app.frame_direction.grid(row=0, column=2, sticky="ne", padx=(8, 0))
    app.btn_dir_buy = ctk.CTkButton(
        app.frame_direction,
        text="BUY",
        width=96,
        height=24,
        font=("Roboto", 10, "bold"),
        fg_color=COL_GREEN,
        hover_color="#009624",
        command=lambda: app.on_direction_change("BUY"),
    )
    app.btn_dir_buy.pack(fill="x", padx=4, pady=(3, 1))
    app.btn_dir_sell = ctk.CTkButton(
        app.frame_direction,
        text="SELL",
        width=96,
        height=24,
        font=("Roboto", 10, "bold"),
        fg_color="#424242",
        hover_color="#616161",
        command=lambda: app.on_direction_change("SELL"),
    )
    app.btn_dir_sell.pack(fill="x", padx=4, pady=(1, 3))

    # Dòng trạng thái/hẹn lệnh màu vàng đặt sát hàng điều khiển để panel bớt trống.
    app.lbl_order_status = ctk.CTkLabel(
        f_dashboard,
        text="",
        font=("Roboto", 11, "bold"),
        text_color="#FFD54F",
        anchor="center",
        justify="center",
    )
    if _unified:
        app.lbl_order_status.pack(fill="x", padx=10, pady=(0, 0))

    app.lbl_contract_dates = ctk.CTkLabel(
        f_dashboard,
        text="",
        font=("Consolas", 10, "bold"),
        text_color="#90A4AE",
        anchor="center",
        justify="center",
    )
    app.lbl_contract_dates.pack(fill="x", padx=10, pady=(0, 1))

    # Trần/Tham chiếu/Sàn (1 dòng nhỏ). Cổ phiếu HOSE ±7%; phái sinh VN30F cũng có biên.
    # Trần/Tham chiếu/Sàn của ngày — để biết khoảng giá khi đặt lệnh thị trường/ATO/ATC.
    app.lbl_band_info = ctk.CTkLabel(
        f_dashboard, text="", font=("Consolas", 11, "bold"), text_color="#FFCC80"
    )
    # Chỉ pack khi main.py có dữ liệu trần/tham chiếu/sàn; tránh giữ khoảng đen khi rỗng.
    app.lbl_band_info_pack_options = {"fill": "x", "padx": 8, "pady": (0, 1)}

    app.dashboard_separator = ctk.CTkFrame(
        f_dashboard, height=1, fg_color="#444"
    )
    app.dashboard_separator.pack(fill="x", padx=5)
    f_grid_db = ctk.CTkFrame(f_dashboard, fg_color="transparent")
    f_grid_db.pack(fill="x", padx=8, pady=(4, 7))
    f_grid_db.columnconfigure((0, 1), weight=1)

    f_rew = ctk.CTkFrame(f_grid_db, fg_color="transparent")
    f_rew.grid(row=0, column=0, sticky="nsew", padx=2)
    app.lbl_head_tp = ctk.CTkLabel(
        f_rew, text="TARGET (TP)", font=("Roboto", 9), text_color=COL_GREEN
    )
    app.lbl_head_tp.pack()
    app.lbl_prev_tp = ctk.CTkLabel(
        f_rew, text="---", font=("Consolas", 12), text_color=COL_GREEN
    )
    app.lbl_prev_tp.pack(pady=(2, 0))
    app.lbl_prev_rew = ctk.CTkLabel(
        f_rew, text="+0", font=("Consolas", 13, "bold"), text_color=COL_GREEN
    )
    app.lbl_prev_rew.pack(pady=(0, 2))

    f_risk = ctk.CTkFrame(f_grid_db, fg_color="transparent")
    f_risk.grid(row=0, column=1, sticky="nsew", padx=2)
    app.lbl_head_sl = ctk.CTkLabel(
        f_risk, text="STOPLOSS (SL)", font=("Roboto", 9), text_color=COL_RED
    )
    app.lbl_head_sl.pack()
    app.lbl_prev_sl = ctk.CTkLabel(
        f_risk, text="---", font=("Consolas", 12), text_color=COL_RED
    )
    app.lbl_prev_sl.pack(pady=(2, 0))
    app.lbl_prev_risk = ctk.CTkLabel(
        f_risk, text="-0", font=("Consolas", 13, "bold"), text_color=COL_RED
    )
    app.lbl_prev_risk.pack(pady=(0, 2))

    f_preview_tabs = ctk.CTkFrame(f_dashboard, fg_color="transparent")
    f_preview_body = ctk.CTkFrame(f_dashboard, fg_color="transparent", height=1)
    f_preview_body.pack_propagate(False)

    def show_preview_tab(tab_name):
        if hasattr(app, "lbl_tsl_preview"):
            app.lbl_tsl_preview.pack_forget()
        if hasattr(app, "lbl_entry_exit_preview"):
            app.lbl_entry_exit_preview.pack_forget()

        if tab_name == "E/E":
            app.lbl_entry_exit_preview.pack(fill="x", padx=2, pady=(2, 0))
        else:
            app.lbl_tsl_preview.pack(fill="x", padx=2, pady=(2, 0))

    app.seg_preview_mode = ctk.CTkSegmentedButton(
        f_preview_tabs,
        values=["TSL", "E/E"],
        font=("Roboto", 10, "bold"),
        height=24,
        command=show_preview_tab,
        selected_color="#1f538d",
        selected_hover_color="#14375e",
    )
    # Preview chi tiet da chuyen sang tab Preview ben khu log.

    app.lbl_tsl_preview = ctk.CTkLabel(
        f_preview_body,
        text="TSL: OFF",
        font=("Roboto", 11),
        text_color="#2196F3",
        anchor="w",
        justify="left",
        wraplength=720,
    )
    app.lbl_entry_exit_preview = ctk.CTkLabel(
        f_preview_body,
        text="E/E: OFF",
        font=("Roboto", 11, "bold"),
        text_color="#00B8D4",
        anchor="w",
        justify="left",
        wraplength=720,
    )
    app.seg_preview_mode.set("TSL")

    # 5. EXECUTION CONTROLS
    app.on_manual_trade_mode_change(app.var_manual_trade_mode.get())

    # 6. SYSTEM HEALTH
    f_sys = ctk.CTkFrame(parent, fg_color="#1a1a1a")
    f_sys.pack(fill="x", padx=5, pady=(5, 6))
    ctk.CTkLabel(
        f_sys, text=" TRẠNG THÁI HỆ THỐNG", font=("Roboto", 11, "bold"), text_color="white"
    ).pack(anchor="w", padx=5, pady=(5, 0))

    app.check_labels = {}
    checks = ["Mạng/Spread", "Daily Loss", "Số Lệnh Thua", "Số Lệnh", "Trạng thái"]
    for name in checks:
        l = ctk.CTkLabel(
            f_sys, text=f"• {name}", font=("Roboto", 12), text_color="white", anchor="w"
        )
        l.pack(fill="x", padx=10)
        app.check_labels[name] = l


def setup_portfolio_tree(app, parent):
    """Bảng Danh mục cổ phiếu cơ sở (CKCS) — read-only (Phase 1).

    Mỗi mã 1 dòng: KL sở hữu / KL bán được (đã về T+2) / chờ về / giá vốn /
    giá hiện tại / giá trị / lãi-lỗ. Mã lô lẻ được tô màu + ghi chú.
    Dữ liệu nạp ở main.update_portfolio_table().
    """
    f_port = ctk.CTkFrame(parent, fg_color="#2b2b2b")
    f_port.pack(fill="both", expand=True)

    style = ttk.Style()
    style.configure(
        "Portfolio.Treeview",
        background="#2b2b2b",
        foreground="#ECEFF1",
        fieldbackground="#2b2b2b",
        rowheight=50,
        font=("Consolas", 18),
    )
    style.configure(
        "Portfolio.Treeview.Heading",
        background="#1f1f1f",
        foreground="#E0E0E0",
        font=("Roboto", 20, "bold"),
        relief="flat",
    )

    cols = (
        "Symbol", "Qty", "Sellable", "Pending",
        "AvgCost", "Price", "Value", "PnL", "Note",
    )
    app.tree_portfolio = ttk.Treeview(
        f_port, columns=cols, show="headings", style="Portfolio.Treeview", selectmode="browse",
    )
    app.tree_portfolio.tag_configure("odd_lot", background="#5c3a17", foreground="#FFD7A0")
    app.tree_portfolio.tag_configure("profit_row", background="#234d20", foreground="#e0e0e0")
    app.tree_portfolio.tag_configure("loss_row", background="#5c1a1b", foreground="#e0e0e0")
    app.tree_portfolio.tag_configure("flat_row", background="#2b2b2b", foreground="#e0e0e0")

    headers = [
        "Mã", "KL sở hữu", "KL bán được", "Chờ về",
        "Giá vốn", "Giá hiện tại", "Giá trị", "Lãi/Lỗ (%)", "Ghi chú",
    ]
    widths = [160, 200, 220, 160, 220, 220, 320, 360, 420]
    anchors = ["center", "e", "e", "e", "e", "e", "e", "e", "w"]
    for c, h, w, a in zip(cols, headers, widths, anchors):
        app.tree_portfolio.heading(c, text=h)
        app.tree_portfolio.column(c, width=w, anchor=a, minwidth=w, stretch=False)

    sb = ttk.Scrollbar(f_port, orient="vertical", command=app.tree_portfolio.yview)
    sb_x = ttk.Scrollbar(f_port, orient="horizontal", command=app.tree_portfolio.xview)
    app.tree_portfolio.configure(yscrollcommand=sb.set, xscrollcommand=sb_x.set)
    app.tree_portfolio.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    sb_x.grid(row=1, column=0, sticky="ew")
    f_port.grid_rowconfigure(0, weight=1)
    f_port.grid_columnconfigure(0, weight=1)


def setup_right_panel(app, parent):
    """Xây dựng khung theo dõi lệnh (Treeview) và Log (Text)"""

    # 1. HEADER ROW
    f_head = ctk.CTkFrame(parent, fg_color="transparent", height=30)
    f_head.pack(fill="x", pady=(0, 5))
    ctk.CTkLabel(
        f_head, text="DANH SÁCH LỆNH ĐANG CHẠY", font=("Roboto", 16, "bold")
    ).pack(side="left")

    try:
        from core import signal_opportunities

        app.var_show_bot_opportunities.set(
            bool(signal_opportunities.load_settings().get("show_in_running_table", True))
        )
    except Exception:
        app.var_show_bot_opportunities.set(True)

    ctk.CTkButton(
        f_head,
        text="Lịch sử",
        width=80,
        height=24,
        command=app.show_history_popup,
        fg_color="#444",
    ).pack(side="right")
    ctk.CTkButton(
        f_head,
        text="? Màu",
        width=62,
        height=24,
        command=app.show_running_color_legend,
        fg_color="#5D4037",
        hover_color="#795548",
    ).pack(side="right", padx=(5, 0))
    ctk.CTkCheckBox(
        f_head,
        text="Hiện gợi ý",
        variable=app.var_show_bot_opportunities,
        command=app.on_show_bot_opportunities_change,
        width=95,
        checkbox_width=20,
        checkbox_height=20,
        font=("Roboto", 11),
    ).pack(side="right", padx=5)
    ctk.CTkButton(
        f_head,
        text="📊 Danh mục",
        width=130,
        height=24,
        command=app.open_portfolio_popup,
        fg_color=COL_SETTING,
        hover_color=COL_SETTING_HOVER,
    ).pack(side="right", padx=5)
    ctk.CTkButton(
        f_head,
        text="Đóng hết",
        width=70,
        height=24,
        fg_color="#D50000",
        hover_color="#B71C1C",
        command=app.close_all_trades,
    ).pack(side="right", padx=5)
    ctk.CTkButton(
        f_head,
        text="Đóng mục chọn",
        width=110,
        height=24,
        fg_color="#FF8F00",
        hover_color="#FF6F00",
        command=app.close_selected_trades,
    ).pack(side="right", padx=5)

    # 2. BỐN BẢNG TÁCH CKPS/CKCS × REAL/PAPER để không lẫn lệnh và PnL.
    running_tabs = ctk.CTkTabview(parent, fg_color="#2b2b2b")
    running_tabs.pack(fill="both", expand=True)
    running_scope_names = ["CKPS REAL", "CKCS REAL", "CKPS PAPER", "CKCS PAPER"]
    running_frames = {name: running_tabs.add(name) for name in running_scope_names}

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Running.Treeview",
        background="#2b2b2b",
        foreground="#ECEFF1",
        fieldbackground="#2b2b2b",
        rowheight=50,
        font=("Consolas", 18),
    )
    style.configure(
        "Running.Treeview.Heading",
        background="#1f1f1f",
        foreground="#e0e0e0",
        font=("Roboto", 20, "bold"),
        relief="flat",
    )
    style.map("Running.Treeview", background=[("selected", "#3949ab")])

    cols = (
        "Ticket",
        "Time",
        "Order",
        "Targets",
        "CostInfo",
        "RR",
        "PnL_MAE_MFE",
        "Status",
        "X",
    )
    headers = [
        "Ticket",
        "Thời gian",
        "Thông tin Lệnh",
        "Chốt lời/Lỗ (SL|TP)",
        "Chi phí/Phí qua đêm",
        "Rủi ro/Kỳ vọng (%)",
        "Lợi nhuận",
        "Trạng thái",
        "✖",
    ]
    widths = [200, 200, 560, 400, 400, 440, 440, 640, 60]
    anchors = [
        "center",
        "center",
        "w",
        "center",
        "center",
        "center",
        "center",
        "w",
        "center",
    ]

    def make_running_tree(frame):
        container = ctk.CTkFrame(frame, fg_color="#2b2b2b")
        container.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            container, columns=cols, show="headings", style="Running.Treeview", selectmode="extended"
        )
        tree.tag_configure("buy_row", background="#1f4630", foreground="#e0e0e0")
        tree.tag_configure("sell_row", background="#4a2424", foreground="#e0e0e0")
        tree.tag_configure("external_position", background="#37474F", foreground="#ECEFF1")
        tree.tag_configure("position_closing", background="#51491b", foreground="#FFF3B0")
        tree.tag_configure("position_close_error", background="#5A1E1E", foreground="#FFCDD2")
        tree.tag_configure("pending_order", background="#5c5417", foreground="#FFF3B0")
        tree.tag_configure("matched_stock", background="#5c3a17", foreground="#FFD7A0")
        tree.tag_configure("local_pending", background="#5c5417", foreground="#FFF3B0")
        tree.tag_configure("local_sending", background="#0b4f5c", foreground="#B2EBF2")
        tree.tag_configure("dnse_order", background="#123f6b", foreground="#D7ECFF")
        tree.tag_configure("dnse_partial", background="#6a3f08", foreground="#FFE0B2")
        tree.tag_configure("order_failed", background="#7a1f1f", foreground="#FFCDD2")
        tree.tag_configure("order_cancelled", background="#303030", foreground="#B0BEC5")
        tree.tag_configure("bot_opportunity", background="#40205c", foreground="#F3E5F5")
        for c, h, w, a in zip(cols, headers, widths, anchors):
            tree.heading(c, text="PnL / MAE / MFE" if c == "PnL_MAE_MFE" else h)
            tree.column(c, width=w, anchor=a, minwidth=w, stretch=False)
        sb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        sb_x = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        tree.bind("<ButtonRelease-1>", app.on_tree_click)
        tree.bind("<Button-3>", app.on_tree_right_click)
        return tree

    app.running_tabs = running_tabs
    app.running_trees = {name: make_running_tree(running_frames[name]) for name in running_scope_names}
    initial_scope = "CKPS PAPER" if getattr(config, "PAPER_TRADING", True) else "CKPS REAL"
    running_tabs.set(initial_scope)
    app.tree = app.running_trees[initial_scope]  # tương thích các đường gọi cũ

    def on_running_tab_change():
        selected = running_tabs.get()
        app.tree = app.running_trees.get(selected, app.tree)

    running_tabs.configure(command=on_running_tab_change)

    # 3. LOGGING CONSOLE (2 TAB: MANUAL & BOT)
    f_log = ctk.CTkFrame(parent, height=370, fg_color="#1e1e1e")
    f_log.pack(fill="x", pady=(10, 0))
    f_log.pack_propagate(False)

    f_log_head = ctk.CTkFrame(f_log, fg_color="transparent", height=25)
    f_log_head.pack(fill="x", padx=5, pady=2)
    ctk.CTkLabel(
        f_log_head,
        text="HỆ THỐNG GHI NHẬT KÝ (LOG)",
        font=("Roboto", 12, "bold"),
        text_color="white",
    ).pack(side="left")
    ctk.CTkCheckBox(
        f_log_head,
        text="Xác nhận đóng lệnh",
        variable=app.var_confirm_close,
        font=("Roboto", 11),
        checkbox_width=16,
        checkbox_height=16,
    ).pack(side="right")

    # Tabview chứa 2 tab Log
    log_tabview = ctk.CTkTabview(
        f_log,
        height=320,
        fg_color="#121212",
        segmented_button_fg_color="#2b2b2b",
        segmented_button_selected_color="#1565C0",
        segmented_button_unselected_color="#333333",
    )
    log_tabview.pack(fill="both", expand=True, padx=5, pady=(0, 5))

    def _clear_active_log():
        """Xóa log ở tab đang được chọn"""
        active_tab = log_tabview.get()
        if "Preview" in active_tab:
            app.refresh_manual_preview_tab()
            return
        if "API Health" in active_tab:
            app.refresh_api_health_panel()
            return
        if "Bot-Log" in active_tab:
            widget = app.txt_log_bot_log
        elif "Bot" in active_tab:
            widget = app.txt_log_bot
        else:
            widget = app.txt_log_manual

        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    ctk.CTkButton(
        f_log_head,
        text="Xóa log",
        width=74,
        height=22,
        fg_color="#444",
        hover_color="#c62828",
        font=("Roboto", 11),
        command=_clear_active_log,
    ).pack(side="right", padx=(0, 8))

    # Bản xem trước cho lệnh tay, luôn đọc PRESET đang chọn.
    tab_preview = log_tabview.add("Manual Preview")

    tab_manual = log_tabview.add("📋 Manual")
    tab_bot = log_tabview.add("🤖 Bot")
    tab_bot_log = log_tabview.add("🤖 Bot-Log")
    tab_api_health = log_tabview.add("API Health")

    app.log_tabview = log_tabview
    app.log_tab_keys = {
        "manual": "📋 Manual",
        "bot": "🤖 Bot",
        "bot-log": "🤖 Bot-Log",
    }
    app.log_tab_unread = {k: False for k in app.log_tab_keys}

    def _clear_unread_after_click(event=None):
        try:
            tab_text = event.widget.cget("text") if event and event.widget else log_tabview.get()
            app.after(20, lambda t=tab_text: app.clear_log_unread_by_tab_name(t))
            if "Preview" in str(tab_text):
                app.after(25, app.refresh_manual_preview_tab)
            if "API Health" in str(tab_text):
                app.after(25, app.refresh_api_health_panel)
        except Exception:
            app.after(60, app.clear_active_log_unread)

    def _on_log_tab_change():
        app.clear_active_log_unread()
        try:
            if "Preview" in str(log_tabview.get()):
                app.refresh_manual_preview_tab()
            if "API Health" in str(log_tabview.get()):
                app.refresh_api_health_panel()
        except Exception:
            pass

    try:
        log_tabview.configure(command=_on_log_tab_change)
        log_tabview._segmented_button.bind("<ButtonRelease-1>", _clear_unread_after_click)
        for _btn in log_tabview._segmented_button._buttons_dict.values():
            _btn.bind("<ButtonRelease-1>", _clear_unread_after_click)
    except Exception:
        pass

    # --- Tab Preview ---
    preview_body = ctk.CTkScrollableFrame(
        tab_preview,
        fg_color="#071113",
        scrollbar_button_color="#164B52",
        scrollbar_button_hover_color="#1D626B",
    )
    preview_body.pack(fill="both", expand=True, padx=4, pady=4)

    preview_panel = ctk.CTkFrame(
        preview_body,
        fg_color="#0D1719",
        corner_radius=8,
        border_width=1,
        border_color="#00C853",
    )
    preview_panel.pack(fill="x", padx=4, pady=(4, 6))
    preview_panel.grid_columnconfigure(0, weight=1)
    preview_panel.grid_columnconfigure(1, minsize=132)

    preview_head = ctk.CTkFrame(preview_panel, fg_color="transparent")
    preview_head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 2))
    preview_head.grid_columnconfigure(2, weight=1)

    lbl_title = ctk.CTkLabel(
        preview_head,
        text="MANUAL PREVIEW",
        font=("Roboto", 16, "bold"),
        text_color="#26C6DA",
        anchor="w",
    )
    lbl_title.grid(row=0, column=0, padx=(0, 10), pady=2, sticky="w")

    app.lbl_preview_symbol = ctk.CTkLabel(
        preview_head,
        text=app.cbo_symbol.get(),
        font=("Roboto", 12, "bold"),
        text_color="#ECEFF1",
        fg_color="#172A2D",
        corner_radius=6,
        width=120,
        height=30,
    )
    app.lbl_preview_symbol.grid(row=0, column=1, padx=(0, 10), pady=2, sticky="w")

    lbl_badge = ctk.CTkLabel(
        preview_head,
        text="CHỜ",
        font=("Roboto", 13, "bold"),
        text_color="#FFB300",
        anchor="e",
    )
    lbl_badge.grid(row=0, column=2, padx=(8, 0), pady=2, sticky="e")

    _preset_val = getattr(config, "DEFAULT_PRESET", "SCALPING")
    _preset_sl_group = config.PRESETS.get(_preset_val, {}).get("MANUAL_SL_GROUP", config.PRESETS.get(_preset_val, {}).get("MANUAL_SWING_SL_GROUP", "G2"))
    _preset_sl_group = "DYNAMIC" if "DYNAMIC" in str(_preset_sl_group) else str(_preset_sl_group or "G2")
    _preset_tp_group = config.PRESETS.get(_preset_val, {}).get("MANUAL_TP_GROUP", config.PRESETS.get(_preset_val, {}).get("MANUAL_SWING_TP_GROUP", _preset_sl_group))
    _preset_tp_group = "DYNAMIC" if "DYNAMIC" in str(_preset_tp_group) else str(_preset_tp_group or "G2")
    _sl_mode = str(config.PRESETS.get(_preset_val, {}).get("MANUAL_SL_MODE", "PERCENT") or "PERCENT").upper()
    _tp_mode = str(config.PRESETS.get(_preset_val, {}).get("MANUAL_TP_MODE", "RR") or "RR").upper()
    try:
        _preview_symbol = app.cbo_symbol.get()
        _preview_context = getattr(app, "latest_market_context", {}).get(_preview_symbol, {}) or {}
        _tf_display = {
            group: app._group_tf_label(group, _preview_symbol, _preview_context)
            for group in ("G0", "G1", "G2", "G3")
        }
        _tf_display["DYNAMIC"] = "DYNAMIC"
        if _sl_mode == "SANDBOX":
            _preset_sl_group = app._sandbox_sl_group_for_symbol(
                _preview_symbol,
                _preview_context,
                _preset_sl_group,
            )
    except Exception:
        _tf_display = {
            "G0": f"G0 ({getattr(config, 'G0_TIMEFRAME', '1d')})",
            "G1": f"G1 ({getattr(config, 'G1_TIMEFRAME', '1h')})",
            "G2": f"G2 ({getattr(config, 'G2_TIMEFRAME', '15m')})",
            "G3": f"G3 ({getattr(config, 'G3_TIMEFRAME', '15m')})",
            "DYNAMIC": "DYNAMIC",
        }
    app.var_preview_sl_group = tk.StringVar(value=_tf_display.get(_preset_sl_group, _tf_display["G2"]))
    app.var_preview_tp_group = tk.StringVar(value=_tf_display.get(_preset_tp_group, _tf_display["G2"]))
    _mode_display = {
        "PERCENT": "Percent",
        "SANDBOX": "Swing Retest",
        "RR": "RR",
        "SWING": "Swing Retest",
        "SWING_REJECTION": "Swing Retest",
        "SWING_RETEST": "Swing Retest",
        "SWING_STRUCTURE": "Swing Struct",
        "SWING_STRUCT": "Swing Struct",
        "FIB": "FIB",
        "PULLBACK": "Pullback",
        "PULLBACK_ZONE": "Pullback",
    }
    app.var_preview_sl_mode = tk.StringVar(value=_mode_display.get(_sl_mode, "Percent"))
    app.var_preview_tp_mode = tk.StringVar(value=_mode_display.get(_tp_mode, "RR"))
    app.var_preview_tf = app.var_preview_sl_group
    tf_values = [_tf_display[group] for group in ("G0", "G1", "G2", "G3", "DYNAMIC")]
    selector_row = ctk.CTkFrame(preview_panel, fg_color="#102326", corner_radius=6)
    selector_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(2, 5))
    selector_row.grid_columnconfigure(1, weight=1)
    selector_row.grid_columnconfigure(3, weight=1)
    ctk.CTkLabel(
        selector_row,
        text="SL",
        font=("Roboto", 11, "bold"),
        text_color="#FFB3AD",
        width=34,
        anchor="w",
    ).grid(row=0, column=0, padx=(8, 4), pady=5, sticky="w")
    app.cbo_preview_sl_mode = ctk.CTkOptionMenu(
        selector_row,
        values=["Percent", "Swing Retest", "Swing Struct", "FIB", "Pullback"],
        variable=app.var_preview_sl_mode,
        width=132,
        height=28,
        font=("Roboto", 12, "bold"),
        command=app.on_preview_sl_mode_change,
    )
    app.cbo_preview_sl_mode.grid(row=0, column=1, padx=(0, 8), pady=5, sticky="ew")
    ctk.CTkLabel(
        selector_row,
        text="TP",
        font=("Roboto", 11, "bold"),
        text_color="#9AFFC4",
        width=34,
        anchor="w",
    ).grid(row=0, column=2, padx=(0, 4), pady=5, sticky="w")
    app.cbo_preview_tp_mode = ctk.CTkOptionMenu(
        selector_row,
        values=["OFF", "RR", "Swing Retest", "Swing Struct", "FIB", "Pullback"],
        variable=app.var_preview_tp_mode,
        width=132,
        height=28,
        font=("Roboto", 12, "bold"),
        command=app.on_preview_tp_mode_change,
    )
    app.cbo_preview_tp_mode.grid(row=0, column=3, padx=(0, 8), pady=5, sticky="ew")
    ctk.CTkLabel(
        selector_row,
        text="TF",
        font=("Roboto", 11, "bold"),
        text_color="#B2EBF2",
        width=34,
        anchor="w",
    ).grid(row=0, column=4, padx=(0, 4), pady=5, sticky="w")
    app.var_preview_tp_group = app.var_preview_sl_group
    app.cbo_preview_sl_group = ctk.CTkOptionMenu(
        selector_row,
        values=tf_values,
        variable=app.var_preview_sl_group,
        width=112,
        height=28,
        font=("Roboto", 12, "bold"),
        command=app.on_preview_group_change,
    )
    app.cbo_preview_sl_group.grid(row=0, column=5, padx=(0, 8), pady=5, sticky="e")
    app.cbo_preview_tp_group = app.cbo_preview_sl_group

    app.chk_preview_trade_after_apply = ctk.CTkCheckBox(
        preview_head,
        text="Trade after Apply",
        variable=app.var_preview_trade_after_apply,
        font=("Roboto", 11, "bold"),
        text_color="#FFD600",
        checkbox_width=18,
        checkbox_height=18,
    )
    app.chk_preview_trade_after_apply.grid(row=0, column=3, padx=(12, 0), pady=2, sticky="e")

    app.preview_cards = {}
    card_key = "primary"
    lbl_meta = ctk.CTkLabel(
        preview_panel,
        text="--",
        font=("Consolas", 12, "bold"),
        text_color="#78909C",
        anchor="w",
    )
    lbl_meta.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4))

    def _preview_line(parent, row, title, color, wrap=1200):
        ctk.CTkLabel(
            parent,
            text=title,
            font=("Roboto", 11, "bold"),
            text_color=color,
            anchor="w",
            width=74,
        ).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=0)
        val = ctk.CTkLabel(
            parent,
            text="--",
            font=("Consolas", 12, "bold"),
            text_color=color,
            anchor="w",
            justify="left",
            wraplength=wrap,
            height=16,
        )
        val.grid(row=row, column=1, sticky="ew", padx=(0, 4), pady=0)
        return val

    levels = ctk.CTkFrame(preview_panel, fg_color="transparent")
    levels.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 0))
    levels.grid_columnconfigure(1, weight=1)
    level_widgets = {}
    target_widgets = {}
    level_widgets["entry_signal"] = _preview_line(levels, 0, "Entry", "#B2EBF2")
    level_widgets["entry"] = level_widgets["entry_signal"]
    level_widgets["entry_zone"] = level_widgets["entry_signal"]
    ctk.CTkLabel(
        levels,
        text="SL / TP",
        font=("Roboto", 11, "bold"),
        text_color="#B2EBF2",
        anchor="w",
        width=74,
    ).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=0)
    sltp_line = ctk.CTkFrame(levels, fg_color="transparent")
    sltp_line.grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=0)
    sltp_line.grid_columnconfigure(1, weight=1)
    level_widgets["sl"] = ctk.CTkLabel(
        sltp_line,
        text="SL --",
        font=("Consolas", 12, "bold"),
        text_color="#FF5252",
        anchor="w",
    )
    level_widgets["sl"].grid(row=0, column=0, sticky="w", padx=(0, 18))
    level_widgets["tp_main"] = ctk.CTkLabel(
        sltp_line,
        text="TP1 -- | TP2 -- | TP3 --",
        font=("Consolas", 12, "bold"),
        text_color="#69F0AE",
        anchor="w",
        justify="left",
        wraplength=900,
    )
    level_widgets["tp_main"].grid(row=0, column=1, sticky="ew")
    level_widgets["rr"] = level_widgets["tp_main"]
    level_widgets["stats"] = _preview_line(levels, 2, "Risk", "#FFD600", wrap=900)
    level_widgets["tsl"] = _preview_line(levels, 3, "TSL", "#9AFFC4", wrap=900)
    level_widgets["ee_detail"] = _preview_line(levels, 4, "E/E", "#FFD600", wrap=780)

    chips = ctk.CTkFrame(preview_panel, fg_color="transparent")
    chips.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))
    chips.grid_remove()
    for col in range(4):
        chips.grid_columnconfigure(col, weight=1, uniform="chips")
    chip_widgets = []
    for idx in range(4):
        chip_box = ctk.CTkFrame(
            chips,
            fg_color="#132326",
            border_width=1,
            border_color="#37565C",
            corner_radius=6,
        )
        chip_box.grid(row=0, column=idx, sticky="ew", padx=3, pady=2)
        chip_label = ctk.CTkLabel(
            chip_box,
            text="--",
            text_color="#D9EEF2",
            font=("Roboto", 11, "bold"),
            anchor="w",
            justify="left",
            wraplength=320,
        )
        chip_label.pack(fill="x", padx=8, pady=4)
        chip_widgets.append((chip_box, chip_label))

    lbl_reason = ctk.CTkLabel(
        preview_panel,
        text="--",
        font=("Roboto", 11),
        text_color="#B0BEC5",
        anchor="w",
        justify="left",
        wraplength=1280,
    )
    lbl_reason.grid(row=5, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

    btn_apply = ctk.CTkButton(
        preview_head,
        text="APPLY",
        width=116,
        height=40,
        fg_color="#164B52",
        hover_color="#1D626B",
        font=("Roboto", 11, "bold"),
        command=lambda k=card_key: app.apply_manual_preview_setup(k),
    )
    btn_apply.grid(row=0, column=4, sticky="e", padx=(12, 0), pady=2)

    app.preview_cards[card_key] = {
        "frame": preview_panel,
        "title": lbl_title,
        "badge": lbl_badge,
        "meta": lbl_meta,
        "levels": level_widgets,
        "targets": target_widgets,
        "chips": chips,
        "chip_widgets": chip_widgets,
        "reason": lbl_reason,
        "apply": btn_apply,
    }

    app.refresh_manual_preview_tab()

    # --- Tab Manual ---
    app.txt_log_manual = tk.Text(
        tab_manual,
        font=("Consolas", 18),
        bg="#121212",
        fg="#e0e0e0",
        bd=0,
        highlightthickness=0,
        state="disabled",
        wrap="none",
    )
    sb_manual_x = ttk.Scrollbar(
        tab_manual, orient="horizontal", command=app.txt_log_manual.xview
    )
    app.txt_log_manual.configure(xscrollcommand=sb_manual_x.set)
    sb_manual_x.pack(fill="x", side="bottom")
    app.txt_log_manual.pack(fill="both", expand=True)
    app.txt_log_manual.tag_config("INFO", foreground="#b0bec5")
    app.txt_log_manual.tag_config("SUCCESS", foreground=COL_GREEN)
    app.txt_log_manual.tag_config("ERROR", foreground=COL_RED)
    app.txt_log_manual.tag_config("WARN", foreground=COL_WARN)
    app.txt_log_manual.tag_config("BLUE", foreground="#29B6F6")

    # --- Tab Bot ---
    app.txt_log_bot = tk.Text(
        tab_bot,
        font=("Consolas", 18),
        bg="#121212",
        fg="#e0e0e0",
        bd=0,
        highlightthickness=0,
        state="disabled",
        wrap="none",
    )
    sb_bot_x = ttk.Scrollbar(
        tab_bot, orient="horizontal", command=app.txt_log_bot.xview
    )
    app.txt_log_bot.configure(xscrollcommand=sb_bot_x.set)
    sb_bot_x.pack(fill="x", side="bottom")
    app.txt_log_bot.pack(fill="both", expand=True)
    app.txt_log_bot.tag_config("INFO", foreground="#b0bec5")
    app.txt_log_bot.tag_config("SUCCESS", foreground=COL_GREEN)
    app.txt_log_bot.tag_config("ERROR", foreground=COL_RED)
    app.txt_log_bot.tag_config("WARN", foreground=COL_WARN)
    app.txt_log_bot.tag_config("BLUE", foreground="#29B6F6")

    # --- Tab Bot-Log ---
    app.txt_log_bot_log = tk.Text(
        tab_bot_log,
        font=("Consolas", 18),
        bg="#121212",
        fg="#e0e0e0",
        bd=0,
        highlightthickness=0,
        state="disabled",
        wrap="none",
    )
    sb_bot_log_x = ttk.Scrollbar(
        tab_bot_log, orient="horizontal", command=app.txt_log_bot_log.xview
    )
    app.txt_log_bot_log.configure(xscrollcommand=sb_bot_log_x.set)
    sb_bot_log_x.pack(fill="x", side="bottom")
    app.txt_log_bot_log.pack(fill="both", expand=True)
    app.txt_log_bot_log.tag_config("INFO", foreground="#b0bec5")
    app.txt_log_bot_log.tag_config("SUCCESS", foreground=COL_GREEN)
    app.txt_log_bot_log.tag_config("ERROR", foreground=COL_RED)
    app.txt_log_bot_log.tag_config("WARN", foreground=COL_WARN)
    app.txt_log_bot_log.tag_config("BLUE", foreground="#29B6F6")

    # --- Tab API Health ---
    api_health_body = ctk.CTkFrame(tab_api_health, fg_color="#121212")
    api_health_body.pack(fill="both", expand=True, padx=6, pady=6)
    api_head = ctk.CTkFrame(api_health_body, fg_color="transparent")
    api_head.pack(fill="x", padx=8, pady=(8, 4))
    app.lbl_api_health_summary = ctk.CTkLabel(
        api_head,
        text="API Health: --",
        font=("Roboto", 13, "bold"),
        text_color="white",
        anchor="w",
    )
    app.lbl_api_health_summary.pack(side="left", fill="x", expand=True)
    ctk.CTkButton(
        api_head,
        text="Refresh",
        width=86,
        height=26,
        fg_color="#1565C0",
        command=app.refresh_api_health_panel,
    ).pack(side="right")

    api_cards = ctk.CTkFrame(api_health_body, fg_color="transparent")
    api_cards.pack(fill="x", padx=4, pady=(2, 7))
    for index in range(4):
        api_cards.grid_columnconfigure(index, weight=1)

    def _health_card(column, title):
        card = ctk.CTkFrame(api_cards, fg_color="#252526", corner_radius=6)
        card.grid(row=0, column=column, sticky="ew", padx=4)
        ctk.CTkLabel(
            card,
            text=title,
            font=("Roboto", 10, "bold"),
            text_color="#90A4AE",
        ).pack(anchor="w", padx=8, pady=(5, 0))
        value = ctk.CTkLabel(
            card,
            text="--",
            font=("Roboto", 13, "bold"),
            text_color="white",
            anchor="w",
        )
        value.pack(fill="x", padx=8, pady=(0, 6))
        return value

    app.lbl_api_health_state = _health_card(0, "TRẠNG THÁI GIÁ")
    app.lbl_api_health_source = _health_card(1, "NGUỒN MÃ ĐANG CHỌN")
    app.lbl_api_health_ws = _health_card(2, "WEBSOCKET")
    app.lbl_api_health_rest = _health_card(3, "REST DỰ PHÒNG")
    app.lbl_api_health_explain = ctk.CTkLabel(
        api_health_body,
        text=(
            "WS = DNSE đẩy giá realtime · REST = app hỏi giá có giới hạn · "
            "CACHE = giá gần nhất, có thể đã cũ"
        ),
        font=("Roboto", 10),
        text_color="#B0BEC5",
        anchor="w",
    )
    app.lbl_api_health_explain.pack(fill="x", padx=10, pady=(0, 5))

    api_health_cols = ctk.CTkFrame(api_health_body, fg_color="transparent")
    api_health_cols.pack(fill="both", expand=True)
    api_health_cols.grid_columnconfigure(0, weight=1)
    api_health_cols.grid_columnconfigure(1, weight=1)
    api_health_cols.grid_rowconfigure(0, weight=1)
    app.txt_api_health = tk.Text(
        api_health_cols,
        font=("Consolas", 13),
        bg="#121212",
        fg="white",
        bd=0,
        highlightthickness=0,
        state="disabled",
        wrap="none",
        height=10,
    )
    app.txt_api_health.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    app.txt_api_health_detail = tk.Text(
        api_health_cols,
        font=("Consolas", 13),
        bg="#121212",
        fg="white",
        bd=0,
        highlightthickness=0,
        state="disabled",
        wrap="word",
        height=10,
    )
    app.txt_api_health_detail.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    sb_api_x = ttk.Scrollbar(api_health_body, orient="horizontal", command=app.txt_api_health.xview)
    app.txt_api_health.configure(xscrollcommand=sb_api_x.set)
    sb_api_x.pack(fill="x", side="bottom")
    app.refresh_api_health_panel()

    # Giữ backward compat: txt_log trỏ vào manual (cho các module cũ)
    app.txt_log = app.txt_log_manual

