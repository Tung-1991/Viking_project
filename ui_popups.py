# -*- coding: utf-8 -*-

# FILE: ui_popups.py

# V3.8: SUPREME FINAL - TRANSIENT LOCK & TACTIC PREVIEW (KAISER EDITION)


import customtkinter as ctk

import tkinter as tk

from tkinter import ttk, messagebox

import config
import csv
import os
import json
import threading
from datetime import datetime

from core.money import (
    format_vnd_full,
    money_input_from_display,
    money_input_to_display,
    money_setting_hint,
    unit_from_display,
    unit_to_display,
)


def _require_broker_success(result, action="Cập nhật lệnh"):
    if getattr(result, "ok", False):
        return result
    message = (
        getattr(result, "message", "")
        or getattr(result, "error", "")
        or "Broker không xác nhận thành công."
    )
    raise RuntimeError(f"{action} thất bại: {message}")


def _fit_popup(window, preferred_w, preferred_h, min_w=620, min_h=460):
    """Keep popups inside the usable RDP/VPS screen and center them."""
    screen_w = max(800, int(window.winfo_screenwidth() or 800))
    screen_h = max(600, int(window.winfo_screenheight() or 600))
    available_w = max(720, screen_w - 60)
    available_h = max(500, screen_h - 100)
    width = min(int(preferred_w), available_w)
    height = min(int(preferred_h), available_h)
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.minsize(min(int(min_w), width), min(int(min_h), height))


def _build_watch_symbols():
    symbols = []
    for raw in list(getattr(config, "CKPS_SYMBOLS", []) or []) + list(getattr(config, "CKCS_WATCHLIST", []) or []):
        sym = str(raw or "").strip().upper()
        if sym and sym not in symbols:
            symbols.append(sym)
    if not symbols:
        default_symbol = str(getattr(config, "DEFAULT_SYMBOL", "VN30F1M") or "VN30F1M").strip().upper()
        if default_symbol:
            symbols.append(default_symbol)
    return symbols


def _paper_closed_trade_history_row(item, fallback_state=None):
    """Đổi biên nhận PAPER thành đúng cấu trúc một dòng lịch sử chi tiết."""
    if not isinstance(item, dict) or not item.get("ticket"):
        return None
    ticket = str(item.get("ticket"))
    symbol = str(item.get("symbol", "")).upper()
    fallback_state = fallback_state if isinstance(fallback_state, dict) else {}
    fallback_direction = fallback_state.get("trade_directions", {}).get(ticket, "BUY")
    side = (
        "BUY"
        if int(item.get("type", 0) or 0) == 0 and str(fallback_direction).upper() != "SELL"
        else "SELL"
    )
    volume = float(item.get("volume", 0.0) or 0.0)
    entry = float(item.get("price_open", 0.0) or 0.0)
    close = float(item.get("price_close", entry) or entry)
    net_pnl = float(item.get("profit", 0.0) or 0.0)
    fee = abs(float(item.get("fee", item.get("commission", 0.0)) or 0.0))
    if fee <= 0 and entry and close and volume:
        from core import settlement

        point_value = (
            float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0)
            if settlement.is_cash_stock(symbol)
            else float(getattr(config, "DNSE_POINT_VALUE", 100000.0) or 100000.0)
        )
        direction = 1.0 if side == "BUY" else -1.0
        gross = (close - entry) * direction * volume * point_value
        fee = max(0.0, gross - net_pnl)
    gross_pnl = net_pnl + fee
    opened = float(
        item.get("open_time", 0.0)
        or fallback_state.get("bot_last_entry_times", {}).get(symbol, 0.0)
        or 0.0
    )
    closed = float(item.get("closed_at", 0.0) or 0.0)
    sl = float(item.get("sl", 0.0) or fallback_state.get("trade_sl", {}).get(ticket, 0.0) or 0.0)
    tp = float(item.get("tp", 0.0) or fallback_state.get("trade_tp", {}).get(ticket, 0.0) or 0.0)
    excursion = fallback_state.get("trade_excursions", {}).get(ticket, {}) or {}
    mae_raw = item.get("mae")
    if mae_raw is None:
        mae_raw = excursion.get("mae", excursion.get("mae_usd"))
    mfe_raw = item.get("mfe")
    if mfe_raw is None:
        mfe_raw = excursion.get("mfe", excursion.get("mfe_usd"))
    mae = min(float(mae_raw), net_pnl) if mae_raw is not None else None
    mfe = max(float(mfe_raw), net_pnl) if mfe_raw is not None else None
    opened_dt = datetime.fromtimestamp(opened) if opened else None
    closed_dt = datetime.fromtimestamp(closed) if closed else None
    if opened_dt and closed_dt and opened_dt.date() == closed_dt.date():
        time_display = f"{opened_dt:%H:%M:%S} → {closed_dt:%H:%M:%S}"
    elif opened_dt and closed_dt:
        time_display = f"{opened_dt:%d/%m %H:%M} → {closed_dt:%d/%m %H:%M}"
    elif closed_dt:
        time_display = f"{closed_dt:%Y-%m-%d %H:%M:%S}"
    else:
        time_display = ""
    session_day = (closed_dt or opened_dt or datetime.now()).strftime("%Y%m%d")
    return [
        time_display,
        ticket,
        symbol,
        side,
        f"{volume:g}",
        f"{entry:.5f}",
        f"{sl:.5f}",
        f"{tp:.5f}",
        f"{-fee:.2f}",
        f"{gross_pnl:.2f}",
        str(item.get("reason", "CLOSED")),
        "PAPER",
        " | ".join(
            part
            for part in ("CLOSED_RECEIPT", str(fallback_state.get("trade_tactics", {}).get(ticket, "") or ""))
            if part
        ),
        f"PAPER_{session_day}",
        f"{mae:.2f}" if mae is not None else "",
        f"{mfe:.2f}" if mfe is not None else "",
        opened_dt.isoformat(timespec="seconds") if opened_dt else "",
        closed_dt.isoformat(timespec="seconds") if closed_dt else "",
        f"{close:.5f}",
    ]


def _history_float(value, default=0.0):
    try:
        return float(str(value).replace("$", "").replace("VND", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _history_row_net_pnl(row):
    return _history_float(row[9] if len(row) > 9 else 0.0) + _history_float(row[8] if len(row) > 8 else 0.0)


def _history_row_close_datetime(row):
    candidates = []
    if len(row) > 17 and row[17]:
        candidates.append(str(row[17]))
    if row:
        candidates.append(str(row[0]))
    for value in candidates:
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                pass
    session_id = str(row[13] if len(row) > 13 else "")
    digits = "".join(ch for ch in session_id if ch.isdigit())[:8]
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%Y%m%d")
        except ValueError:
            pass
    return None


def _history_row_close_day(row):
    closed = _history_row_close_datetime(row)
    return closed.strftime("%Y-%m-%d") if closed else "Không rõ ngày"


def _history_row_scope(row, derivative_symbols=None):
    derivative_symbols = {str(symbol).upper() for symbol in (derivative_symbols or [])}
    ticket = str(row[1] if len(row) > 1 else "").upper()
    symbol = str(row[2] if len(row) > 2 else "").upper()
    is_paper = ticket.startswith("PAPER") or ticket.startswith("#PAPER")
    is_derivative = symbol.startswith("VN30F") or symbol in derivative_symbols
    if is_paper:
        return "Paper-PS" if is_derivative else "Paper-CKCS"
    return "Phái sinh" if is_derivative else "CKCS"


def _history_session_summary(rows):
    summary = {
        "trades": 0,
        "buys": 0,
        "sells": 0,
        "wins": 0,
        "gross_pnl": 0.0,
        "fee": 0.0,
        "net_pnl": 0.0,
        "worst_mae": None,
        "best_mfe": None,
    }
    for row in rows:
        gross = _history_float(row[9] if len(row) > 9 else 0.0)
        fee = _history_float(row[8] if len(row) > 8 else 0.0)
        net = gross + fee
        summary["trades"] += 1
        summary["buys"] += int(str(row[3] if len(row) > 3 else "").upper() == "BUY")
        summary["sells"] += int(str(row[3] if len(row) > 3 else "").upper() == "SELL")
        summary["wins"] += int(net > 0)
        summary["gross_pnl"] += gross
        summary["fee"] += fee
        summary["net_pnl"] += net
        if len(row) > 14 and str(row[14]).strip():
            mae = min(_history_float(row[14]), net)
            summary["worst_mae"] = mae if summary["worst_mae"] is None else min(summary["worst_mae"], mae)
        if len(row) > 15 and str(row[15]).strip():
            mfe = max(_history_float(row[15]), net)
            summary["best_mfe"] = mfe if summary["best_mfe"] is None else max(summary["best_mfe"], mfe)
    summary["winrate"] = (summary["wins"] / summary["trades"] * 100.0) if summary["trades"] else 0.0
    return summary


def _bring_popup_to_front(window, delay_ms=150):
    """Restore and raise a popup; normal Windows focus controls its stacking."""
    try:
        try:
            window.deiconify()
            if window.state() not in {"normal", "zoomed"}:
                window.state("normal")
        except Exception:
            pass
        window.attributes("-topmost", True)
        window.lift()
        window.focus_force()

        def release_topmost():
            try:
                if window.winfo_exists():
                    window.attributes("-topmost", False)
            except Exception:
                pass

        window.after(delay_ms, release_topmost)
    except Exception:
        pass
    return window


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


ADVISOR_FILES_HELP_TEXT = """AI ADVISOR GỒM HAI PHẦN

1) ĐÁNH GIÁ BOT — data/<tài khoản>/advisor/

Tự tạo nếu thiếu:
- advisor_prompt.md: luật giao việc cho AI.
- advisor_flow.md: giải thích nghiệp vụ BOT.
- user_context.md: mục tiêu/câu hỏi của người dùng.
- expert_context.md: ghi chú chuyên gia dùng để đối chiếu.

Tạo hoặc ghi đè khi bấm LÀM MỚI DỮ LIỆU BOT:
- technical_settings.json: setting BOT tại thời điểm tạo.
- advisor_export.xlsx: lệnh, PnL, phí và thống kê BOT.

Chỉ có sau khi gọi API thành công:
- advisor_response.md: kết quả AI gần nhất.

Khi gửi qua trình duyệt, mở thẳng thư mục advisor và chọn các file cần dùng.

2) DỮ LIỆU THỊ TRƯỜNG CKCS — data/<tài khoản>/ckcs_research/

App tự cập nhật trong phiên:
- scan_snapshot_cache.json: kho nội bộ theo mã/ngày, giữ mặc định 250 ngày.
- ckcs_shortlist.md: danh sách tín hiệu CKPS/CKCS đã qua kiểm tra mức giá; thanh khoản chỉ lọc CKCS; luôn ghi đè.

App tạo/ghi đè báo cáo:
- 11:35 và 14:50 cùng cập nhật một file `scan_report.md`; lần sau ghi đè lần trước.
- Làm mới thủ công cũng cập nhật đúng file này.
- Lịch sử nhiều ngày không mất vì được lấy từ `scan_snapshot_cache.json`.

Người dùng tự điền:
- private_context.md: tin chuyên gia, nhận định cá nhân, vốn và thời gian nắm giữ.

Chỉ có khi bật/gọi API:
- ckcs_response_morning.md hoặc ckcs_response_afternoon.md.

RAW chỉ bổ sung bối cảnh thị trường/nghiên cứu CKCS; không sửa BOT và không đặt lệnh.
Khi gửi qua trình duyệt, chọn ckcs_shortlist.md + scan_report.md + private_context.md.
LLM đọc shortlist trước, phân tích riêng CKPS/CKCS, rồi tra RAW nhiều ngày trong scan_report.md.
Không cần gửi scan_snapshot_cache.json vì đây là kho nội bộ dài.

API là phần nâng cao và mặc định tắt; tắt API không phát sinh request hoặc phí.
Không còn external_package, scan_report_morning.md, scan_report_afternoon.md, scan_summary.md, file latest hoặc file version phụ.
"""


def open_advisor_files_help(app, parent=None):
    parent = parent or app
    popup = ctk.CTkToplevel(parent)
    popup.title("Giải thích file BOT Advisor và CKCS Research")
    _fit_popup(popup, 820, 720, 680, 560)
    _bring_popup_to_front(popup)

    ctk.CTkLabel(
        popup,
        text="FILE NÀO TỰ TẠO, FILE NÀO PHẢI BẤM NÚT?",
        font=("Roboto", 18, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=14, pady=(12, 6))
    text_body = ctk.CTkFrame(popup, fg_color="transparent")
    text_body.pack(fill="both", expand=True, padx=14, pady=6)
    text_body.grid_columnconfigure(0, weight=1)
    text_body.grid_rowconfigure(0, weight=1)
    text_widget = tk.Text(
        text_body,
        font=("Consolas", 14),
        bg="#151515",
        fg="#E0E0E0",
        insertbackground="white",
        wrap="word",
        padx=8,
        pady=6,
        spacing1=2,
        spacing3=2,
        bd=0,
        highlightthickness=0,
    )
    text_widget.grid(row=0, column=0, sticky="nsew")
    help_scroll = ttk.Scrollbar(text_body, orient="vertical", command=text_widget.yview)
    help_scroll.grid(row=0, column=1, sticky="ns")
    text_widget.configure(yscrollcommand=help_scroll.set)
    text_widget.insert("1.0", ADVISOR_FILES_HELP_TEXT)
    text_widget.configure(state="disabled")

    buttons = ctk.CTkFrame(popup, fg_color="transparent")
    buttons.pack(fill="x", padx=14, pady=(4, 12))

    def _copy_help():
        popup.clipboard_clear()
        popup.clipboard_append(ADVISOR_FILES_HELP_TEXT)
        popup.update()

    ctk.CTkButton(buttons, text="SAO CHÉP GIẢI THÍCH", command=_copy_help).pack(side="left")
    ctk.CTkButton(
        buttons,
        text="ĐÓNG",
        width=90,
        fg_color="#424242",
        command=popup.destroy,
    ).pack(side="right")


def open_advisor_popup(app):
    existing = getattr(app, "_advisor_popup", None)
    try:
        if existing is not None and existing.winfo_exists():
            _bring_popup_to_front(existing, delay_ms=300)
            existing.after_idle(
                lambda popup=existing: _bring_popup_to_front(popup, delay_ms=300)
            )
            return existing
    except Exception:
        app._advisor_popup = None

    top = ctk.CTkToplevel(app)
    app._advisor_popup = top

    def clear_popup_reference(event=None):
        if event is not None and event.widget is not top:
            return
        if getattr(app, "_advisor_popup", None) is top:
            app._advisor_popup = None

    top.bind("<Destroy>", clear_popup_reference, add="+")
    top.protocol("WM_DELETE_WINDOW", top.withdraw)
    top.title("AI ADVISOR")
    _fit_popup(top, 820, 540, 650, 460)
    top.resizable(True, True)

    root = ctk.CTkFrame(top, fg_color="#1E1E1E", corner_radius=0)
    root.pack(fill="both", expand=True, padx=10, pady=10)

    advisor_header = ctk.CTkFrame(root, fg_color="transparent")
    advisor_header.pack(fill="x", padx=8, pady=(3, 1))
    title_group = ctk.CTkFrame(advisor_header, fg_color="transparent")
    title_group.pack(side="left")
    ctk.CTkLabel(title_group, text="AI ADVISOR", font=("Roboto", 16, "bold"), text_color="#80DEEA").pack(anchor="w")
    ctk.CTkLabel(
        title_group,
        text="Báo cáo BOT · dữ liệu CKCS · Telegram · OpenAI",
        font=("Roboto", 9),
        text_color="#90A4AE",
    ).pack(anchor="w")
    ctk.CTkButton(
        advisor_header,
        text="? GIẢI THÍCH FILE",
        width=155,
        height=30,
        fg_color="#455A64",
        hover_color="#546E7A",
        command=lambda: open_advisor_files_help(app, top),
    ).pack(side="right")

    tabs = ctk.CTkTabview(root)
    tabs.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    tab_run = tabs.add("BÁO CÁO BOT")
    tab_ckcs_root = tabs.add("BÁO CÁO CKCS")
    tab_telegram = tabs.add("TELEGRAM")
    tab_edit = tabs.add("OPENAI API")
    # Các frame cũ chỉ giữ phần xử lý nội bộ; giao diện hiển thị được dựng gọn ở dưới.
    tab_ckcs = ctk.CTkFrame(tab_ckcs_root)
    tab_ckcs_api = ctk.CTkFrame(tab_ckcs_root)
    api_legacy = ctk.CTkFrame(tab_edit, fg_color="transparent")
    edit_body = _speed_up_scroll(ctk.CTkScrollableFrame(api_legacy, fg_color="transparent"))
    edit_body.pack(fill="both", expand=True, padx=0, pady=0)

    bot_page = _speed_up_scroll(
        ctk.CTkScrollableFrame(tab_run, fg_color="transparent"), factor=7
    )
    bot_page.pack(fill="both", expand=True, padx=2, pady=2)

    bot_panel = ctk.CTkFrame(bot_page, fg_color="#252526", corner_radius=8)
    bot_panel.pack(fill="x", padx=8, pady=(4, 8))
    ctk.CTkLabel(
        bot_panel,
        text=(
            "Tạo dữ liệu để AI đánh giá cách BOT đang chạy: setting, lịch sử lệnh và kết quả. "
            "Không dùng kho CKCS và không tự thay đổi lệnh."
        ),
        font=("Roboto", 11, "bold"),
        text_color="#80DEEA",
        wraplength=560,
        anchor="w",
        justify="left",
    ).pack(anchor="w", padx=12, pady=(12, 6))

    status_row = ctk.CTkFrame(bot_panel, fg_color="#1E1E1E", corner_radius=6)
    status_row.pack(fill="x", padx=10, pady=(2, 8))
    ctk.CTkLabel(status_row, text="Trạng thái", font=("Roboto", 12, "bold"), text_color="gray").pack(side="left", padx=10, pady=8)
    app.lbl_advisor_status = ctk.CTkLabel(
        status_row,
        text=getattr(app, "advisor_last_export_status", "Never"),
        font=("Roboto", 12, "bold"),
        text_color="#00C853" if "OK" in getattr(app, "advisor_last_export_status", "") else "gray",
        anchor="e",
    )
    app.lbl_advisor_status.pack(side="right", fill="x", expand=True, padx=10, pady=8)

    settings = ctk.CTkFrame(bot_panel, fg_color="transparent", corner_radius=6)
    settings.pack(fill="x", padx=10, pady=(0, 4))
    settings.grid_columnconfigure(1, weight=1)
    settings.grid_columnconfigure(3, weight=1)

    def _save_advisor_schedule(_event=None, silent=False):
        return app.save_advisor_schedule_settings(silent=silent)

    ctk.CTkLabel(
        settings,
        text="CÁCH TẠO BÁO CÁO",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(3, 3))
    ctk.CTkLabel(settings, text="Dữ liệu bao nhiêu ngày", font=("Roboto", 11, "bold")).grid(
        row=1, column=0, sticky="w", padx=(10, 5), pady=4
    )
    advisor_days_entry = ctk.CTkEntry(
        settings, textvariable=app.var_advisor_export_days, height=30, placeholder_text="VD: 7"
    )
    advisor_days_entry.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=4)
    advisor_days_entry.bind("<FocusOut>", lambda event: _save_advisor_schedule(event, silent=True))
    advisor_days_entry.bind("<Return>", _save_advisor_schedule)

    ctk.CTkLabel(settings, text="Cách chạy", font=("Roboto", 11, "bold")).grid(
        row=1, column=2, sticky="w", padx=(5, 5), pady=4
    )
    advisor_mode_menu = ctk.CTkOptionMenu(
        settings,
        values=["Manual Only", "API Trigger"],
        variable=app.var_advisor_mode,
        height=30,
    )
    advisor_mode_menu.grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=4)

    ctk.CTkLabel(settings, text="Giờ tự chạy", font=("Roboto", 11, "bold")).grid(
        row=2, column=0, sticky="w", padx=(10, 5), pady=4
    )
    advisor_time_entry = ctk.CTkEntry(
        settings, textvariable=app.var_advisor_fixed_time, height=30, placeholder_text="HH:MM"
    )
    advisor_time_entry.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=4)
    advisor_time_entry.bind("<FocusOut>", lambda event: _save_advisor_schedule(event, silent=True))
    advisor_time_entry.bind("<Return>", _save_advisor_schedule)

    emergency_check = ctk.CTkCheckBox(
        settings,
        text="Gọi AI khi BOT bị khóa khẩn cấp",
        variable=app.var_advisor_global_emergency,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    )
    emergency_check.grid(row=2, column=2, columnspan=2, sticky="w", padx=(5, 10), pady=4)

    ctk.CTkCheckBox(
        settings,
        text="Cho AI đọc kết quả lần trước để so sánh",
        variable=app.var_advisor_send_response_file,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=4)

    ctk.CTkButton(
        settings,
        text="LƯU CẤU HÌNH",
        height=34,
        fg_color="#00695C",
        hover_color="#004D40",
        command=_save_advisor_schedule,
    ).grid(row=3, column=2, columnspan=2, sticky="ew", padx=(5, 10), pady=4)

    ctk.CTkLabel(
        settings,
        text=(
            "Manual Only: chỉ chạy khi bấm nút. API Trigger: tự tạo dữ liệu BOT và gọi OpenAI đúng giờ, "
            "có thể phát sinh phí. “Khóa khẩn cấp” chỉ dùng ở API Trigger."
        ),
        font=("Roboto", 10),
        text_color="#90CAF9",
        wraplength=880,
        justify="left",
    ).grid(row=4, column=0, columnspan=4, sticky="w", padx=10, pady=(3, 7))

    def _advisor_mode_changed(_value=None):
        is_api = app.var_advisor_mode.get() == "API Trigger"
        advisor_time_entry.configure(state="normal" if is_api else "disabled")
        emergency_check.configure(state="normal" if is_api else "disabled")
        _save_advisor_schedule(silent=True)

    advisor_mode_menu.configure(command=_advisor_mode_changed)
    _advisor_mode_changed()

    # --- Kho dữ liệu CKCS độc lập; không gọi LLM và không tác động BOT ---
    ckcs_body = _speed_up_scroll(ctk.CTkScrollableFrame(tab_ckcs, fg_color="transparent"))
    ckcs_body.pack(fill="both", expand=True, padx=8, pady=8)

    warning = ctk.CTkFrame(ckcs_body, fg_color="#3A2E08", corner_radius=6)
    warning.pack(fill="x", padx=6, pady=(0, 10))
    ctk.CTkLabel(
        warning,
        text="KHO DỮ LIỆU chỉ thu thập và tổng hợp các mã đã chọn; không gọi LLM và không tác động BOT.",
        font=("Roboto", 11, "bold"),
        text_color="#FFD54F",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", padx=10, pady=8)

    ckcs_settings = ctk.CTkFrame(ckcs_body, fg_color="#252526", corner_radius=6)
    ckcs_settings.pack(fill="x", padx=6, pady=(0, 10))
    ckcs_settings.grid_columnconfigure(1, weight=1)
    var_scan_snapshot = tk.BooleanVar(value=bool(getattr(config, "SCAN_SNAPSHOT_ENABLED", True)))
    var_scan_interval = tk.StringVar(
        value=f"{getattr(config, 'SCAN_SNAPSHOT_INTERVAL_MINUTES', 15):g}"
    )
    var_retention_days = tk.StringVar(
        value=str(getattr(config, "SCAN_SNAPSHOT_RETENTION_DAYS", 250))
    )
    e_scan_interval = ctk.CTkEntry(
        ckcs_settings, textvariable=var_scan_interval, width=110, height=28
    )
    e_retention_days = ctk.CTkEntry(
        ckcs_settings, textvariable=var_retention_days, width=110, height=28
    )

    def _save_scan_snapshot():
        try:
            enabled = bool(var_scan_snapshot.get())
            interval = max(1.0, float(var_scan_interval.get() or 15))
            retention = max(1, int(var_retention_days.get() or 250))
            from core import env_utils
            import core.storage_manager as storage_manager

            env_utils.update_env({
                "SCAN_SNAPSHOT_ENABLED": "true" if enabled else "false",
                "SCAN_SNAPSHOT_INTERVAL_MINUTES": str(interval),
                "SCAN_SNAPSHOT_RETENTION_DAYS": str(retention),
            })
            config.SCAN_SNAPSHOT_ENABLED = enabled
            config.SCAN_SNAPSHOT_INTERVAL_MINUTES = interval
            config.SCAN_SNAPSHOT_RETENTION_DAYS = retention
            data = storage_manager.load_brain_settings()
            data["SCAN_SNAPSHOT_ENABLED"] = enabled
            data["SCAN_SNAPSHOT_INTERVAL_MINUTES"] = interval
            data["SCAN_SNAPSHOT_RETENTION_DAYS"] = retention
            storage_manager.save_brain_settings(data)
            storage_manager.invalidate_settings_cache()
            app._set_ckcs_raw_status(
                f"Đã lưu | {'ON' if enabled else 'OFF'} | {interval:g} phút | giữ {retention} ngày"
            )
        except Exception as exc:
            app._set_ckcs_raw_status("Lưu setting lỗi", str(exc))

    ctk.CTkLabel(
        ckcs_settings, text="THU THẬP CÁC MÃ ĐÃ CHỌN", font=("Roboto", 13, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))
    ctk.CTkCheckBox(
        ckcs_settings,
        text="Bật lưu dữ liệu quét trong phiên",
        variable=var_scan_snapshot,
        font=("Roboto", 12, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=6)
    ctk.CTkLabel(
        ckcs_settings,
        text="Chọn/bỏ từng mã tại Advanced → Cache & Mã.",
        font=("Roboto", 10, "bold"),
        text_color="#90CAF9",
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 5))
    ctk.CTkLabel(ckcs_settings, text="Chu kỳ cập nhật (phút)", font=("Roboto", 11, "bold")).grid(row=3, column=0, sticky="w", padx=10, pady=5)
    e_scan_interval.grid(row=3, column=1, sticky="e", padx=10, pady=5)
    ctk.CTkLabel(ckcs_settings, text="Số ngày giữ dữ liệu", font=("Roboto", 11, "bold")).grid(row=4, column=0, sticky="w", padx=10, pady=5)
    e_retention_days.grid(row=4, column=1, sticky="e", padx=10, pady=5)
    ctk.CTkButton(
        ckcs_settings, text="LƯU SETTING", height=30, fg_color="#00695C", hover_color="#004D40", command=_save_scan_snapshot
    ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 10))

    status_box = ctk.CTkFrame(ckcs_body, fg_color="#252526", corner_radius=6)
    status_box.pack(fill="x", padx=6, pady=(0, 10))
    app.lbl_ckcs_raw_status = ctk.CTkLabel(
        status_box,
        text=getattr(app, "ckcs_raw_last_status", "Kho CKCS: đang đọc..."),
        font=("Roboto", 11, "bold"),
        text_color="#B0BEC5",
        anchor="w",
        justify="left",
        wraplength=560,
    )
    app.lbl_ckcs_raw_status.pack(fill="x", padx=10, pady=9)

    raw_info = ctk.CTkFrame(ckcs_body, fg_color="#252526", corner_radius=6)
    raw_info.pack(fill="x", padx=6, pady=(0, 10))
    ctk.CTkLabel(
        raw_info,
        text="FILE NỘI BỘ",
        font=("Roboto", 13, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=10, pady=(10, 3))
    ctk.CTkLabel(
        raw_info,
        text=(
            "scan_snapshot_cache.json được app tự cập nhật theo mã/ngày. "
            "Đây là kho nguồn để tạo báo cáo, không phải file gửi trực tiếp cho LLM."
        ),
        font=("Roboto", 11),
        text_color="#CFD8DC",
        wraplength=820,
        justify="left",
    ).pack(anchor="w", padx=10, pady=(0, 7))
    ctk.CTkButton(
        raw_info,
        text="MỞ THƯ MỤC DỮ LIỆU",
        height=30,
        fg_color="#424242",
        command=app.open_ckcs_research_folder,
    ).pack(fill="x", padx=10, pady=(0, 10))

    def _open_private_context():
        from ai_advisor import paths as advisor_paths

        advisor_paths.ensure_ckcs_research_dir()
        path = advisor_paths.research_private_context_path()
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "# PRIVATE CONTEXT — LỌC CKCS\n\n"
                    "## Cách dùng 3 file\n\n"
                    "1. ckcs_shortlist.md: danh sách tín hiệu CKPS/CKCS cần phân tích.\n"
                    "2. scan_report.md: RAW nhiều ngày; chỉ tra các mã trong shortlist và "
                    "VN30/VN30F làm bối cảnh.\n"
                    "3. private_context.md: bốn tiêu chí và yêu cầu đầu ra dưới đây.\n\n"
                    "Không cần phân tích lại toàn bộ danh sách mã. Nếu shortlist rỗng, "
                    "cho phép kết luận chưa có mã phù hợp.\n\n"
                    "## 1. Thanh khoản\n\n"
                    "Dùng PASS/FAIL do app tính từ số phiên và ngưỡng đang cấu hình.\n\n"
                    "## 2. Tăng trưởng doanh thu/lợi nhuận\n\n"
                    "Tìm nguồn công khai trên web để đánh giá chu kỳ và tăng trưởng 5 năm; "
                    "tách hoạt động cốt lõi khỏi khoản bất thường.\n\n"
                    "## 3. Định giá doanh nghiệp\n\n"
                    "Tìm và đối chiếu nguồn định giá công khai. Ưu tiên giá thị trường thấp hơn "
                    "giá hợp lý khoảng 20%; nói rõ nguồn, ngày và độ chắc chắn.\n\n"
                    "## 4. Lọc kỹ thuật\n\n"
                    "Đọc đúng CHECK/Lego, group, timeframe và rule thực tế trong scan_report.md; "
                    "không tự giả định một bộ indicator khác.\n\n"
                    "## Đầu ra cần nhất\n\n"
                    "Xếp hạng ngắn; với mỗi mã ghi rõ ENTRY/vùng mua, điều kiện kích hoạt, "
                    "mức không mua đuổi, CẮT/điểm nhận định sai, TP hoặc trailing, "
                    "thời gian giữ, tỷ trọng và ngày hết hiệu lực.\n"
                )
        open_advisor_file_editor(app, path, "private_context.md — CKCS Research")

    def _open_ckcs_shortlist():
        from telegram_notify.opportunity_alerts import refresh_shortlist

        path = refresh_shortlist()
        open_advisor_file_editor(app, path, "ckcs_shortlist.md — tự ghi đè")

    def _refresh_scan_status():
        try:
            if not app.lbl_ckcs_raw_status.winfo_exists():
                return
            from ai_advisor.scan_cache import recorder

            current = recorder.status()
            app.lbl_ckcs_raw_status.configure(
                text=(
                    f"Kho RAW: {current['symbols']} mã đã lưu | {current['days']} ngày | "
                    f"đang chọn {current.get('selected_symbols', 0)} mã | "
                    f"hôm nay {current['today_status']} ({current['today_samples']} lượt cập nhật) | "
                    f"cập nhật {current['updated_at'] or '—'}"
                ),
                text_color="#B0BEC5",
            )
            top.after(2000, _refresh_scan_status)
        except Exception:
            top.after(5000, _refresh_scan_status)

    _refresh_scan_status()

    # --- CKCS API: hai lịch cập nhật cùng một report và tùy chọn gửi LLM ---
    ckcs_api_body = _speed_up_scroll(ctk.CTkScrollableFrame(tab_ckcs_api, fg_color="transparent"))
    ckcs_api_body.pack(fill="both", expand=True, padx=8, pady=8)

    ckcs_api_warning = ctk.CTkFrame(ckcs_api_body, fg_color="#3A2E08", corner_radius=6)
    ckcs_api_warning.pack(fill="x", padx=6, pady=(0, 10))
    ctk.CTkLabel(
        ckcs_api_warning,
        text=(
            "Phần này cập nhật một scan_report.md để Ngài tự gửi qua trình duyệt. "
            "Không tự chọn mã, không sửa setting và không đặt lệnh."
        ),
        font=("Roboto", 11, "bold"),
        text_color="#FFD54F",
        wraplength=760,
        justify="left",
    ).pack(anchor="w", padx=10, pady=8)

    ckcs_api_status_box = ctk.CTkFrame(ckcs_api_body, fg_color="#252526", corner_radius=6)
    ckcs_api_status_box.pack(fill="x", padx=6, pady=(0, 10))
    app.lbl_ckcs_api_status = ctk.CTkLabel(
        ckcs_api_status_box,
        text=getattr(app, "ckcs_api_last_status", "Chưa chạy CKCS API"),
        font=("Roboto", 11, "bold"),
        text_color="#B0BEC5",
        anchor="w",
        justify="left",
        wraplength=760,
    )
    app.lbl_ckcs_api_status.pack(fill="x", padx=10, pady=9)

    ckcs_common = ctk.CTkFrame(ckcs_api_body, fg_color="#252526", corner_radius=6)
    ckcs_common.pack(fill="x", padx=6, pady=(0, 10))
    ckcs_common.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        ckcs_common,
        text="CẤU HÌNH CHUNG",
        font=("Roboto", 13, "bold"),
        text_color="#80DEEA",
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))
    ctk.CTkLabel(
        ckcs_common,
        text="Số ngày giao dịch đưa vào mỗi báo cáo",
        font=("Roboto", 11, "bold"),
    ).grid(row=1, column=0, sticky="w", padx=10, pady=6)
    ckcs_api_days = ctk.CTkEntry(
        ckcs_common,
        textvariable=app.var_ckcs_report_days,
        width=110,
        height=28,
        placeholder_text="VD: 15",
    )
    ckcs_api_days.grid(row=1, column=1, sticky="e", padx=10, pady=6)
    ckcs_api_days.bind("<FocusOut>", lambda event: _save_advisor_schedule(event, silent=True))
    ckcs_api_days.bind("<Return>", _save_advisor_schedule)
    ctk.CTkLabel(
        ckcs_common,
        text=(
            "Làm mới thủ công cập nhật scan_report.md; lịch sau sẽ ghi đè bằng dữ liệu mới hơn. "
            "API dùng chung cấu hình tại tab API NÂNG CAO và mặc định tắt."
        ),
        font=("Roboto", 10, "bold"),
        text_color="#90CAF9",
        wraplength=740,
        justify="left",
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))
    common_actions = ctk.CTkFrame(ckcs_common, fg_color="transparent")
    common_actions.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
    for index in range(2):
        common_actions.grid_columnconfigure(index, weight=1)
    ctk.CTkButton(
        common_actions,
        text="ĐIỀN PRIVATE CONTEXT",
        height=30,
        fg_color="#8D6E00",
        hover_color="#A67C00",
        command=_open_private_context,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ctk.CTkButton(
        common_actions,
        text="LÀM MỚI BÁO CÁO HIỆN TẠI",
        height=30,
        fg_color="#00695C",
        hover_color="#004D40",
        command=app.refresh_current_ckcs_report_ui,
    ).grid(row=0, column=1, sticky="ew", padx=4)
    ctk.CTkButton(
        ckcs_common,
        text="MỞ THƯ MỤC CKCS",
        height=28,
        fg_color="#424242",
        command=app.open_ckcs_research_folder,
    ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))

    sessions_grid = ctk.CTkFrame(ckcs_api_body, fg_color="transparent")
    sessions_grid.pack(fill="x", padx=6, pady=(0, 10))
    sessions_grid.grid_columnconfigure(0, weight=1, uniform="ckcs_sessions")
    sessions_grid.grid_columnconfigure(1, weight=1, uniform="ckcs_sessions")

    def _add_ckcs_session_box(parent, column, session, title, report_var, time_var):
        box = ctk.CTkFrame(parent, fg_color="#252526", corner_radius=6)
        box.grid(row=0, column=column, sticky="nsew", padx=(0, 5) if column == 0 else (5, 0))
        box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            box,
            text=title,
            font=("Roboto", 13, "bold"),
            text_color="#80DEEA",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))
        ctk.CTkLabel(box, text="Giờ chạy", font=("Roboto", 11, "bold")).grid(
            row=1, column=0, sticky="w", padx=10, pady=5
        )
        time_entry = ctk.CTkEntry(box, textvariable=time_var, width=100, height=28, placeholder_text="HH:MM")
        time_entry.grid(row=1, column=1, sticky="e", padx=10, pady=5)
        time_entry.bind("<FocusOut>", lambda event: _save_advisor_schedule(event, silent=True))
        time_entry.bind("<Return>", _save_advisor_schedule)
        ctk.CTkCheckBox(
            box,
            text="Tự tạo báo cáo mỗi ngày giao dịch",
            variable=report_var,
            command=lambda: _save_advisor_schedule(silent=True),
            font=("Roboto", 11, "bold"),
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=5)
        report_name = f"scan_report_{session}.md"
        ctk.CTkLabel(
            box,
            text=f"Báo cáo: {report_name}\nAPI tùy chọn nằm tại tab API NÂNG CAO",
            font=("Consolas", 10, "bold"),
            text_color="#FFD54F",
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 7))
        actions = ctk.CTkFrame(box, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
        actions.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            actions,
            text="TẠO FILE",
            height=30,
            fg_color="#00695C",
            hover_color="#004D40",
            command=lambda s=session: app.run_ckcs_session_ui(s, send_api=False),
        ).grid(row=0, column=0, sticky="ew")
        file_actions = ctk.CTkFrame(box, fg_color="transparent")
        file_actions.grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        file_actions.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            file_actions,
            text="MỞ BÁO CÁO",
            height=28,
            fg_color="#424242",
            command=lambda s=session: app.open_ckcs_session_file(s, response=False),
        ).grid(row=0, column=0, sticky="ew")

    _add_ckcs_session_box(
        sessions_grid,
        0,
        "morning",
        "PHIÊN SÁNG",
        app.var_ckcs_auto_report_morning,
        app.var_ckcs_morning_time,
    )
    _add_ckcs_session_box(
        sessions_grid,
        1,
        "afternoon",
        "CUỐI NGÀY",
        app.var_ckcs_auto_report_afternoon,
        app.var_ckcs_afternoon_time,
    )
    ctk.CTkButton(
        ckcs_api_body,
        text="LƯU LỊCH CKCS",
        height=38,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=_save_advisor_schedule,
    ).pack(fill="x", padx=6, pady=(0, 6))
    ctk.CTkButton(
        ckcs_api_body,
        text="MỞ THƯ MỤC CKCS",
        height=30,
        fg_color="#424242",
        command=app.open_ckcs_research_folder,
    ).pack(fill="x", padx=6, pady=(0, 10))

    # Giao diện CKCS gọn: kho RAW và lịch báo cáo nằm trong cùng một panel.
    ckcs_compact_body = _speed_up_scroll(
        ctk.CTkScrollableFrame(tab_ckcs_root, fg_color="transparent"), factor=7
    )
    ckcs_compact_body.pack(fill="both", expand=True, padx=8, pady=8)
    ckcs_panel = ctk.CTkFrame(ckcs_compact_body, fg_color="#252526", corner_radius=8)
    ckcs_panel.pack(fill="x", padx=6, pady=4)
    ckcs_panel.grid_columnconfigure(1, weight=1)
    ckcs_panel.grid_columnconfigure(3, weight=1)

    ctk.CTkLabel(
        ckcs_panel,
        text="THU THẬP VÀ TẠO BÁO CÁO CKCS",
        font=("Roboto", 14, "bold"),
        text_color="#80DEEA",
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(12, 2))
    ctk.CTkLabel(
        ckcs_panel,
        text=(
            "App gom dữ liệu các mã đã chọn vào kho theo mã/ngày, rồi cập nhật một scan_report.md. "
            "Phần này không chọn mã, không gọi AI và không tác động BOT."
        ),
        font=("Roboto", 10, "bold"),
        text_color="#FFD54F",
        wraplength=560,
        anchor="w",
        justify="left",
    ).grid(row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 10))

    ctk.CTkCheckBox(
        ckcs_panel,
        text="Bật lưu dữ liệu trong phiên",
        variable=var_scan_snapshot,
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=5)
    ctk.CTkLabel(
        ckcs_panel,
        text="Chọn mã tại Advanced → Cache & Mã",
        font=("Roboto", 10),
        text_color="#90CAF9",
    ).grid(row=2, column=2, columnspan=2, sticky="e", padx=12, pady=5)

    ctk.CTkLabel(ckcs_panel, text="Cập nhật kho mỗi (phút)", font=("Roboto", 11, "bold")).grid(
        row=3, column=0, sticky="w", padx=(12, 6), pady=5
    )
    ctk.CTkEntry(
        ckcs_panel, textvariable=var_scan_interval, width=110, height=28
    ).grid(row=3, column=1, sticky="ew", padx=(0, 16), pady=5)
    ctk.CTkLabel(ckcs_panel, text="Giữ lịch sử (ngày giao dịch)", font=("Roboto", 11, "bold")).grid(
        row=3, column=2, sticky="w", padx=(6, 6), pady=5
    )
    ctk.CTkEntry(
        ckcs_panel, textvariable=var_retention_days, width=110, height=28
    ).grid(row=3, column=3, sticky="ew", padx=(0, 12), pady=5)

    ctk.CTkLabel(ckcs_panel, text="Số ngày đưa vào báo cáo", font=("Roboto", 11, "bold")).grid(
        row=4, column=0, sticky="w", padx=(12, 6), pady=5
    )
    ckcs_report_days_entry = ctk.CTkEntry(
        ckcs_panel,
        textvariable=app.var_ckcs_report_days,
        width=110,
        height=28,
    )
    ckcs_report_days_entry.grid(row=4, column=1, sticky="ew", padx=(0, 16), pady=5)
    ctk.CTkLabel(
        ckcs_panel,
        text="15 nghĩa là 15 ngày giao dịch, không phải 15 lần quét.",
        font=("Roboto", 10),
        text_color="#B0BEC5",
    ).grid(row=4, column=2, columnspan=2, sticky="w", padx=(6, 12), pady=5)

    ctk.CTkCheckBox(
        ckcs_panel,
        text="Lọc thanh khoản bình quân",
        variable=app.var_ckcs_liquidity_filter_enabled,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=5)
    liquidity_labels = ctk.CTkFrame(ckcs_panel, fg_color="transparent")
    liquidity_labels.grid(row=5, column=2, sticky="w", padx=(6, 6), pady=5)
    ctk.CTkLabel(
        liquidity_labels,
        text="Số phiên",
        font=("Roboto", 10, "bold"),
    ).pack(side="left", padx=(0, 28))
    ctk.CTkLabel(
        liquidity_labels,
        text="Ngưỡng tỷ",
        font=("Roboto", 10, "bold"),
    ).pack(side="left")
    liquidity_controls = ctk.CTkFrame(ckcs_panel, fg_color="transparent")
    liquidity_controls.grid(row=5, column=3, sticky="ew", padx=(0, 12), pady=5)
    liquidity_controls.grid_columnconfigure((0, 1), weight=1)
    liquidity_sessions_entry = ctk.CTkEntry(
        liquidity_controls,
        textvariable=app.var_ckcs_liquidity_sessions,
        width=55,
        height=28,
    )
    liquidity_sessions_entry.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    liquidity_entry = ctk.CTkEntry(
        liquidity_controls,
        textvariable=app.var_ckcs_liquidity_min_billion,
        width=55,
        height=28,
    )
    liquidity_entry.grid(row=0, column=1, sticky="ew", padx=3)
    ctk.CTkButton(
        liquidity_controls,
        text="ÁP DỤNG",
        width=75,
        height=28,
        fg_color="#2E7D32",
        command=lambda: _save_advisor_schedule(silent=False),
    ).grid(row=0, column=2, sticky="e", padx=(3, 0))
    ctk.CTkLabel(
        ckcs_panel,
        text=(
            "Số phiên chỉnh 5–100. App tự tính từ nến ngày DNSE; CKCS không đạt sẽ không "
            "gửi vào nhóm Gợi ý BOT; RAW và lệnh BOT không bị thay đổi."
        ),
        font=("Roboto", 9),
        text_color="#90CAF9",
        anchor="w",
        justify="left",
    ).grid(row=6, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 5))

    ctk.CTkCheckBox(
        ckcs_panel,
        text="Cập nhật cùng file lúc",
        variable=app.var_ckcs_auto_report_morning,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=7, column=0, sticky="w", padx=12, pady=5)
    morning_time_entry = ctk.CTkEntry(
        ckcs_panel, textvariable=app.var_ckcs_morning_time, width=100, height=28
    )
    morning_time_entry.grid(row=7, column=1, sticky="ew", padx=(0, 16), pady=5)
    ctk.CTkCheckBox(
        ckcs_panel,
        text="Cập nhật lại và ghi đè lúc",
        variable=app.var_ckcs_auto_report_afternoon,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=7, column=2, sticky="w", padx=(6, 6), pady=5)
    afternoon_time_entry = ctk.CTkEntry(
        ckcs_panel, textvariable=app.var_ckcs_afternoon_time, width=100, height=28
    )
    afternoon_time_entry.grid(row=7, column=3, sticky="ew", padx=(0, 12), pady=5)

    def _save_ckcs_compact():
        _save_scan_snapshot()
        _save_advisor_schedule(silent=True)
        app._set_ckcs_api_status("Đã lưu cấu hình kho và lịch báo cáo")

    for entry in (
        ckcs_report_days_entry,
        liquidity_sessions_entry,
        liquidity_entry,
        morning_time_entry,
        afternoon_time_entry,
    ):
        entry.bind("<FocusOut>", lambda _event: _save_advisor_schedule(silent=True))
        entry.bind("<Return>", lambda _event: _save_advisor_schedule(silent=False))

    ckcs_files_card = ctk.CTkFrame(ckcs_panel, fg_color="#1E1E1E", corner_radius=8)
    ckcs_files_card.grid(row=8, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 5))
    ctk.CTkLabel(
        ckcs_files_card,
        text="FILE CKCS RESEARCH",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=10, pady=(8, 1))
    ctk.CTkLabel(
        ckcs_files_card,
        text=(
            "Ba file dùng cho LLM: shortlist CKPS/CKCS, RAW nhiều ngày và hướng dẫn riêng. "
            "Shortlist cùng scan_report luôn được ghi đè bằng dữ liệu mới."
        ),
        font=("Roboto", 9),
        text_color="#B0BEC5",
    ).pack(anchor="w", padx=10, pady=(0, 5))

    ckcs_file_buttons = ctk.CTkFrame(ckcs_files_card, fg_color="transparent")
    ckcs_file_buttons.pack(fill="x", padx=10, pady=(0, 8))
    ckcs_file_buttons.grid_columnconfigure((0, 1, 2), weight=1)
    ctk.CTkButton(
        ckcs_file_buttons,
        text="SỬA private_context.md",
        height=32,
        font=("Roboto", 9, "bold"),
        fg_color="#424242",
        command=_open_private_context,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ctk.CTkButton(
        ckcs_file_buttons,
        text="MỞ / SỬA ckcs_shortlist.md",
        height=32,
        font=("Roboto", 9, "bold"),
        fg_color="#424242",
        command=_open_ckcs_shortlist,
    ).grid(row=0, column=1, sticky="ew", padx=4)
    ctk.CTkButton(
        ckcs_file_buttons,
        text="MỞ scan_report.md",
        height=32,
        font=("Roboto", 9, "bold"),
        fg_color="#424242",
        command=lambda: app.open_ckcs_session_file("morning", response=False),
    ).grid(row=0, column=2, sticky="ew", padx=(4, 0))

    compact_actions = ctk.CTkFrame(ckcs_panel, fg_color="transparent")
    compact_actions.grid(row=9, column=0, columnspan=4, sticky="ew", padx=12, pady=(4, 8))
    compact_actions.grid_columnconfigure(0, weight=2)
    compact_actions.grid_columnconfigure((1, 2), weight=1)
    ctk.CTkButton(
        compact_actions,
        text="TẠO BÁO CÁO HIỆN TẠI",
        height=38,
        fg_color="#00695C",
        hover_color="#004D40",
        command=app.refresh_current_ckcs_report_ui,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ctk.CTkButton(
        compact_actions,
        text="MỞ THƯ MỤC",
        height=38,
        fg_color="#424242",
        command=app.open_ckcs_research_folder,
    ).grid(row=0, column=1, sticky="ew", padx=4)
    ctk.CTkButton(
        compact_actions,
        text="LƯU CẤU HÌNH",
        height=38,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=_save_ckcs_compact,
    ).grid(row=0, column=2, sticky="ew", padx=(4, 0))

    app.lbl_ckcs_raw_status = ctk.CTkLabel(
        ckcs_panel,
        text=getattr(app, "ckcs_raw_last_status", "Kho CKCS: đang đọc..."),
        font=("Roboto", 10, "bold"),
        text_color="#B0BEC5",
        anchor="w",
        justify="left",
        wraplength=560,
    )
    app.lbl_ckcs_raw_status.grid(row=10, column=0, columnspan=4, sticky="ew", padx=12, pady=(2, 2))
    app.lbl_ckcs_api_status = ctk.CTkLabel(
        ckcs_panel,
        text=getattr(app, "ckcs_api_last_status", "Báo cáo chưa được tạo trong lần mở app này"),
        font=("Roboto", 10),
        text_color="#90CAF9",
        anchor="w",
        justify="left",
        wraplength=560,
    )
    app.lbl_ckcs_api_status.grid(row=11, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 12))
    # Giữ các widget xử lý cũ ở trạng thái không hiển thị. Một số CTkEntry vẫn
    # còn trace trên StringVar; destroy chúng tại đây sẽ làm callback gọi vào
    # widget đã mất khi app cập nhật setting.

    api_panel = ctk.CTkFrame(edit_body, fg_color="#252526", corner_radius=8)
    api_panel.pack(fill="x", padx=10, pady=(8, 10))
    ctk.CTkLabel(
        api_panel,
        text="OPENAI API — TÙY CHỌN NÂNG CAO",
        font=("Roboto", 14, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=12, pady=(12, 0))
    ctk.CTkLabel(
        api_panel,
        text=(
            "Chỉ cần cấu hình phần này khi muốn app tự gửi dữ liệu cho AI. "
            "Để tắt thì app vẫn tạo file để Ngài tự tải lên ChatGPT bằng trình duyệt."
        ),
        font=("Roboto", 10),
        text_color="#B0BEC5",
        wraplength=900,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(2, 6))

    api_hint = ctk.CTkFrame(api_panel, fg_color="transparent", corner_radius=6)
    api_hint.pack(fill="x", padx=10, pady=(8, 8))
    ctk.CTkLabel(
        api_hint,
        text="API KEY — chỉ đọc từ môi trường Windows, không ghi token vào file setting",
        font=("Roboto", 10, "bold"),
        text_color="#FBC02D",
    ).pack(anchor="w", padx=10, pady=(7, 2))
    from core import env_utils as secret_env

    var_openai_key = tk.StringVar(value="")
    api_secret_row = ctk.CTkFrame(api_hint, fg_color="transparent")
    api_secret_row.pack(fill="x", padx=10, pady=(2, 4))
    ctk.CTkLabel(
        api_secret_row,
        text="OPENAI_API_KEY",
        width=135,
        anchor="w",
        font=("Consolas", 11, "bold"),
    ).pack(side="left")
    openai_key_entry = ctk.CTkEntry(
        api_secret_row,
        textvariable=var_openai_key,
        show="*",
        placeholder_text="Dán API key mới — để trống không thay đổi",
        height=30,
    )
    openai_key_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
    openai_key_visible = tk.BooleanVar(value=False)

    def toggle_openai_key():
        openai_key_visible.set(not openai_key_visible.get())
        openai_key_entry.configure(show="" if openai_key_visible.get() else "*")

    api_key_status = ctk.CTkLabel(
        api_hint,
        text=(
            "● ĐÃ CÓ KEY" if secret_env.user_environment_secret_present("OPENAI_API_KEY")
            else "○ CHƯA CÓ KEY"
        ),
        font=("Roboto", 10, "bold"),
        text_color=(
            "#81C784" if secret_env.user_environment_secret_present("OPENAI_API_KEY")
            else "#FFB74D"
        ),
        anchor="w",
    )

    def save_openai_key():
        try:
            secret_env.set_user_environment_secret("OPENAI_API_KEY", var_openai_key.get())
            var_openai_key.set("")
            openai_key_visible.set(False)
            openai_key_entry.configure(show="*")
            api_key_status.configure(
                text="● ĐÃ LƯU KEY VÀO WINDOWS — app dùng được ngay",
                text_color="#81C784",
            )
            app._set_advisor_status("OPENAI_API_KEY đã được lưu")
        except Exception as exc:
            api_key_status.configure(text=f"Lưu key lỗi: {exc}", text_color="#EF5350")
            app._set_advisor_status("Lưu OPENAI_API_KEY lỗi", str(exc))

    ctk.CTkButton(
        api_secret_row,
        text="HIỆN",
        width=65,
        height=30,
        fg_color="#424242",
        command=toggle_openai_key,
    ).pack(side="left", padx=(0, 6))
    ctk.CTkButton(
        api_secret_row,
        text="LƯU KEY",
        width=105,
        height=30,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=save_openai_key,
    ).pack(side="left")
    api_key_status.pack(fill="x", padx=10, pady=(0, 8))

    bot_files_slot = ctk.CTkFrame(bot_panel, fg_color="transparent")
    bot_files_slot.pack(fill="x", padx=10, pady=(2, 4))

    buttons = ctk.CTkFrame(bot_panel, fg_color="transparent")
    buttons.pack(fill="x", padx=10, pady=(6, 10))
    ctk.CTkButton(buttons, text="TẠO / LÀM MỚI FILE BOT", height=40, fg_color="#00695C", hover_color="#004D40", command=app.generate_advisor_package_ui).pack(side="left", fill="x", expand=True, padx=(0, 5))
    ctk.CTkButton(buttons, text="MỞ THƯ MỤC", width=150, height=40, fg_color="#424242", hover_color="#616161", command=app.open_advisor_folder).pack(side="left", padx=5)
    ctk.CTkButton(buttons, text="GỌI OPENAI NGAY", width=190, height=40, fg_color="#1f538d", hover_color="#14375e", command=app.send_advisor_api_now).pack(side="left", padx=(5, 0))

    from ai_advisor import api_client
    from ai_advisor.exporter import (
        cleanup_legacy_advisor_artifacts,
        ensure_advisor_flow,
        ensure_expert_context,
        ensure_user_context,
    )

    api_client.ensure_advisor_prompt()
    ensure_advisor_flow()
    ensure_user_context()
    ensure_expert_context()
    cleanup_legacy_advisor_artifacts()
    if not os.path.exists(api_client.paths.advisor_api_settings_path()):
        api_client.save_api_settings(api_client.DEFAULT_API_SETTINGS)
    api_settings = api_client.load_api_settings()

    ckcs_api_advanced = ctk.CTkFrame(api_panel, fg_color="transparent", corner_radius=6)
    ckcs_api_advanced.pack(fill="x", padx=10, pady=(8, 8))
    ctk.CTkLabel(
        ckcs_api_advanced,
        text="CKCS API — TÙY CHỌN NÂNG CAO",
        font=("Roboto", 12, "bold"),
        text_color="#FFD54F",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        ckcs_api_advanced,
        text=(
            "Mặc định tắt. Khi bật, app gửi báo cáo phiên + private_context.md; "
            "chỉ lưu kết quả, không tạo lệnh CKCS."
        ),
        font=("Roboto", 10, "bold"),
        text_color="#D7CCC8",
        wraplength=820,
        justify="left",
    ).pack(anchor="w", padx=10, pady=(0, 7))
    ckcs_api_options = ctk.CTkFrame(ckcs_api_advanced, fg_color="transparent")
    ckcs_api_options.pack(fill="x", padx=10, pady=(0, 6))
    for idx in range(2):
        ckcs_api_options.grid_columnconfigure(idx, weight=1)
    ctk.CTkCheckBox(
        ckcs_api_options,
        text="Tự gửi API sau báo cáo sáng",
        variable=app.var_ckcs_send_api_morning,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
    ctk.CTkCheckBox(
        ckcs_api_options,
        text="Tự gửi API sau báo cáo chiều",
        variable=app.var_ckcs_send_api_afternoon,
        command=lambda: _save_advisor_schedule(silent=True),
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=4)
    ckcs_api_actions = ctk.CTkFrame(ckcs_api_advanced, fg_color="transparent")
    ckcs_api_actions.pack(fill="x", padx=10, pady=(0, 10))
    for idx in range(4):
        ckcs_api_actions.grid_columnconfigure(idx, weight=1)
    ctk.CTkButton(
        ckcs_api_actions,
        text="GỬI API SÁNG",
        height=29,
        command=lambda: app.run_ckcs_session_ui("morning", send_api=True),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
    ctk.CTkButton(
        ckcs_api_actions,
        text="MỞ KẾT QUẢ SÁNG",
        height=29,
        fg_color="#424242",
        command=lambda: app.open_ckcs_session_file("morning", response=True),
    ).grid(row=0, column=1, sticky="ew", padx=3)
    ctk.CTkButton(
        ckcs_api_actions,
        text="GỬI API CHIỀU",
        height=29,
        command=lambda: app.run_ckcs_session_ui("afternoon", send_api=True),
    ).grid(row=0, column=2, sticky="ew", padx=3)
    ctk.CTkButton(
        ckcs_api_actions,
        text="MỞ KẾT QUẢ CHIỀU",
        height=29,
        fg_color="#424242",
        command=lambda: app.open_ckcs_session_file("afternoon", response=True),
    ).grid(row=0, column=3, sticky="ew", padx=(3, 0))

    from telegram_notify import reporter as telegram_reporter
    from telegram_notify import settings as telegram_settings
    from telegram_notify.control import CONTROL_HELP_TEXT

    tg_settings = telegram_settings.load_settings()
    var_tg_enabled = tk.BooleanVar(value=bool(tg_settings.get("enabled")))
    var_tg_system_alerts = tk.BooleanVar(
        value=bool(tg_settings.get("system_alerts_enabled", True))
    )
    var_tg_control_enabled = tk.BooleanVar(value=bool(tg_settings.get("control_enabled")))
    var_tg_signal_enabled = tk.BooleanVar(value=bool(tg_settings.get("signal_proposals_enabled")))
    var_tg_opportunity_enabled = tk.BooleanVar(
        value=bool(tg_settings.get("opportunity_alerts_enabled"))
    )
    var_tg_opportunity_daily_digest = tk.BooleanVar(
        value=bool(tg_settings.get("opportunity_daily_digest_enabled", False))
    )
    var_tg_env = tk.StringVar(value=str(tg_settings.get("bot_token_env", "TELE_BOT_KEY")))
    var_tg_report_chat = tk.StringVar(value=str(tg_settings.get("report_chat_id", "1003772881044")))
    var_tg_opportunity_chat = tk.StringVar(
        value=str(tg_settings.get("opportunity_chat_id", ""))
    )
    var_tg_control_chat = tk.StringVar(value=str(tg_settings.get("control_chat_id", "1003941549878")))
    var_tg_owner_id = tk.StringVar(value=str(tg_settings.get("owner_user_id", "")))
    var_tg_operator_ids = tk.StringVar(value=str(tg_settings.get("operator_user_ids", "")))
    var_tg_poll_interval = tk.StringVar(value=str(tg_settings.get("control_poll_interval_seconds", 2.0)))
    var_tg_signal_cooldown = tk.StringVar(value=str(tg_settings.get("signal_proposal_cooldown_minutes", 15.0)))
    var_tg_opportunity_cooldown = tk.StringVar(
        value=str(tg_settings.get("opportunity_duplicate_cooldown_minutes", 0.0))
    )
    var_tg_opportunity_batch = tk.StringVar(
        value=str(tg_settings.get("opportunity_batch_minutes", 5.0))
    )
    var_tg_opportunity_mode = tk.StringVar(
        value=str(tg_settings.get("opportunity_mode_filter", "ALL"))
    )
    var_tg_opportunity_ckps = tk.BooleanVar(
        value=bool(tg_settings.get("opportunity_ckps_enabled", True))
    )
    var_tg_opportunity_ckcs = tk.BooleanVar(
        value=bool(tg_settings.get("opportunity_ckcs_enabled", True))
    )
    var_tg_reversal_alerts = tk.BooleanVar(
        value=bool(tg_settings.get("position_reversal_alerts_enabled", True))
    )
    var_tg_level_alerts = tk.BooleanVar(
        value=bool(tg_settings.get("position_level_alerts_enabled", False))
    )
    var_tg_chunk = tk.StringVar(value=str(tg_settings.get("chunk_size", 3500)))
    var_tg_token = tk.StringVar(value="")

    telegram_legacy = ctk.CTkFrame(tab_telegram, fg_color="transparent")
    tg_body = _speed_up_scroll(ctk.CTkScrollableFrame(telegram_legacy, fg_color="transparent"), factor=12)
    tg_body.pack(fill="both", expand=True, padx=10, pady=10)
    tg_body.grid_columnconfigure(0, weight=1)

    tg_status = ctk.CTkFrame(tg_body, fg_color="#252526", corner_radius=6)
    tg_status.grid(row=0, column=0, sticky="ew", pady=(0, 10))
    ctk.CTkLabel(
        tg_status,
        text="TELEGRAM — BA KÊNH ĐỘC LẬP",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        tg_status,
        text=(
            "Báo cáo AI, gợi ý BOT và điều khiển từ xa dùng Chat ID riêng. "
            "Gợi ý BOT chỉ là thông báo, không có nút đặt lệnh."
        ),
        font=("Roboto", 11, "bold"),
        text_color="#FBC02D",
        anchor="w",
    ).pack(anchor="w", padx=10, pady=(0, 6))

    tg_env_row = ctk.CTkFrame(tg_status, fg_color="transparent")
    tg_env_row.pack(fill="x", padx=10, pady=(0, 4))
    ctk.CTkLabel(
        tg_env_row,
        text="TELE_BOT_KEY",
        width=135,
        anchor="w",
        font=("Consolas", 11, "bold"),
    ).pack(side="left")
    tg_token_entry = ctk.CTkEntry(
        tg_env_row,
        textvariable=var_tg_token,
        show="*",
        placeholder_text="Dán Telegram bot token mới — để trống không thay đổi",
        height=30,
    )
    tg_token_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
    tg_token_visible = tk.BooleanVar(value=False)
    tg_token_status = ctk.CTkLabel(
        tg_status,
        text=(
            "● ĐÃ CÓ TOKEN" if secret_env.user_environment_secret_present(var_tg_env.get())
            else "○ CHƯA CÓ TOKEN"
        ),
        font=("Roboto", 10, "bold"),
        text_color=(
            "#81C784" if secret_env.user_environment_secret_present(var_tg_env.get())
            else "#FFB74D"
        ),
        anchor="w",
    )

    def toggle_telegram_token():
        tg_token_visible.set(not tg_token_visible.get())
        tg_token_entry.configure(show="" if tg_token_visible.get() else "*")

    def save_telegram_token():
        try:
            env_name = (var_tg_env.get() or "TELE_BOT_KEY").strip() or "TELE_BOT_KEY"
            secret_env.set_user_environment_secret(env_name, var_tg_token.get())
            var_tg_token.set("")
            tg_token_visible.set(False)
            tg_token_entry.configure(show="*")
            tg_token_status.configure(
                text=f"● ĐÃ LƯU {env_name} VÀO WINDOWS — dùng được ngay",
                text_color="#81C784",
            )
            app._set_advisor_status(f"{env_name} đã được lưu")
        except Exception as exc:
            tg_token_status.configure(text=f"Lưu token lỗi: {exc}", text_color="#EF5350")
            app._set_advisor_status("Lưu Telegram token lỗi", str(exc))

    ctk.CTkButton(
        tg_env_row,
        text="HIỆN",
        width=65,
        height=30,
        fg_color="#424242",
        command=toggle_telegram_token,
    ).pack(side="left", padx=(0, 6))
    ctk.CTkButton(
        tg_env_row,
        text="LƯU TOKEN",
        width=110,
        height=30,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=save_telegram_token,
    ).pack(side="left")
    tg_token_status.pack(fill="x", padx=10, pady=(0, 8))

    tg_compact = ctk.CTkFrame(tg_status, fg_color="#1E1E1E", corner_radius=6)
    tg_compact.pack(fill="x", padx=10, pady=(0, 8))
    tg_compact.grid_columnconfigure(0, weight=1)

    tg_settings_col = ctk.CTkFrame(tg_compact, fg_color="transparent", corner_radius=6)
    tg_settings_col.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
    tg_settings_col.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        tg_settings_col,
        text="1. NƠI NHẬN",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 6))

    tg_rules_col = ctk.CTkFrame(tg_compact, fg_color="transparent", corner_radius=6)
    tg_rules_col.grid(row=1, column=0, sticky="ew", padx=4, pady=(2, 4))
    tg_rules_col.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        tg_rules_col,
        text="2. GỬI GÌ VÀ CHỐNG SPAM",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 6))

    def _tg_row(parent, label, variable, row):
        ctk.CTkLabel(parent, text=label, font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(
            row=row, column=0, sticky="w", padx=(10, 0), pady=5
        )
        ctk.CTkEntry(parent, textvariable=variable, height=28).grid(
            row=row, column=1, sticky="ew", padx=10, pady=5
        )

    def _tg_check(parent, label, variable, row):
        ctk.CTkCheckBox(
            parent,
            text=label,
            variable=variable,
            font=("Roboto", 12, "bold"),
            checkbox_width=18,
            checkbox_height=18,
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=6)

    _tg_row(tg_settings_col, "Tên biến chứa token", var_tg_env, 1)
    _tg_row(tg_settings_col, "Chat ID nhận báo cáo AI", var_tg_report_chat, 2)
    _tg_row(tg_settings_col, "Chat ID nhận gợi ý BOT", var_tg_opportunity_chat, 3)
    _tg_row(tg_settings_col, "Chat ID điều khiển từ xa", var_tg_control_chat, 4)
    _tg_row(tg_settings_col, "Telegram User ID của chủ", var_tg_owner_id, 5)
    _tg_row(tg_settings_col, "Độ dài mỗi phần báo cáo", var_tg_chunk, 6)

    _tg_check(tg_rules_col, "Gửi báo cáo AI", var_tg_enabled, 1)
    _tg_check(tg_rules_col, "Gửi gợi ý BOT (chỉ thông báo)", var_tg_opportunity_enabled, 2)
    ctk.CTkLabel(
        tg_rules_col,
        text="Chỉ nhận gợi ý từ",
        font=("Roboto", 11, "bold"),
        text_color="#D7DCE2",
    ).grid(row=3, column=0, sticky="w", padx=(10, 0), pady=5)
    ctk.CTkOptionMenu(
        tg_rules_col,
        values=["PAPER", "REAL", "ALL"],
        variable=var_tg_opportunity_mode,
        height=28,
    ).grid(row=3, column=1, sticky="ew", padx=10, pady=5)
    _tg_check(tg_rules_col, "Nhận gợi ý CKPS", var_tg_opportunity_ckps, 4)
    _tg_check(tg_rules_col, "Nhận gợi ý CKCS", var_tg_opportunity_ckcs, 5)
    _tg_row(tg_rules_col, "Lặp cùng mã (phút)", var_tg_opportunity_cooldown, 6)
    _tg_row(tg_rules_col, "Gộp và gửi mỗi (phút)", var_tg_opportunity_batch, 7)
    _tg_check(
        tg_rules_col,
        "Cảnh báo đảo chiều vị thế đang mở",
        var_tg_reversal_alerts,
        8,
    )
    _tg_check(
        tg_rules_col,
        "Cảnh báo vị thế gần SL/TP",
        var_tg_level_alerts,
        9,
    )
    _tg_check(tg_rules_col, "Bật điều khiển từ xa (nâng cao)", var_tg_control_enabled, 10)
    _tg_check(tg_rules_col, "Cho phép duyệt signal từ xa (nâng cao)", var_tg_signal_enabled, 11)
    _tg_row(tg_rules_col, "Chu kỳ đọc lệnh điều khiển (giây)", var_tg_poll_interval, 12)
    _tg_row(tg_rules_col, "Cooldown signal điều khiển (phút)", var_tg_signal_cooldown, 13)
    ctk.CTkLabel(
        tg_rules_col,
        text=(
            "Gợi ý trùng mã + BUY/SELL chỉ gửi lại sau cooldown. "
            "Nhiều mã xuất hiện gần nhau được gom thành một tin theo chu kỳ gộp."
        ),
        font=("Roboto", 10),
        text_color="#90CAF9",
        wraplength=880,
        justify="left",
    ).grid(row=14, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))

    tg_buttons = ctk.CTkFrame(tg_compact, fg_color="transparent")
    tg_buttons.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 8))
    tg_buttons.grid_columnconfigure((0, 1, 2, 3), weight=1)

    def save_telegram_settings():
        try:
            saved = telegram_settings.save_settings(
                {
                    "enabled": var_tg_enabled.get(),
                    "system_alerts_enabled": var_tg_system_alerts.get(),
                    "control_enabled": var_tg_control_enabled.get(),
                    "signal_proposals_enabled": var_tg_signal_enabled.get(),
                    "opportunity_alerts_enabled": var_tg_opportunity_enabled.get(),
                    "opportunity_daily_digest_enabled": var_tg_opportunity_daily_digest.get(),
                    "bot_token_env": var_tg_env.get(),
                    "report_chat_id": var_tg_report_chat.get(),
                    "opportunity_chat_id": var_tg_opportunity_chat.get(),
                    "control_chat_id": var_tg_control_chat.get(),
                    "owner_user_id": var_tg_owner_id.get(),
                    "operator_user_ids": "",
                    "control_poll_interval_seconds": var_tg_poll_interval.get(),
                    "signal_proposal_cooldown_minutes": var_tg_signal_cooldown.get(),
                    "opportunity_duplicate_cooldown_minutes": var_tg_opportunity_cooldown.get(),
                    "opportunity_batch_minutes": var_tg_opportunity_batch.get(),
                    "opportunity_mode_filter": var_tg_opportunity_mode.get(),
                    "opportunity_ckps_enabled": var_tg_opportunity_ckps.get(),
                    "opportunity_ckcs_enabled": var_tg_opportunity_ckcs.get(),
                    "position_reversal_alerts_enabled": var_tg_reversal_alerts.get(),
                    "position_level_alerts_enabled": var_tg_level_alerts.get(),
                    "chunk_size": var_tg_chunk.get(),
                }
            )
            var_tg_enabled.set(bool(saved.get("enabled")))
            var_tg_system_alerts.set(bool(saved.get("system_alerts_enabled", True)))
            var_tg_control_enabled.set(bool(saved.get("control_enabled")))
            var_tg_signal_enabled.set(bool(saved.get("signal_proposals_enabled")))
            var_tg_opportunity_enabled.set(bool(saved.get("opportunity_alerts_enabled")))
            var_tg_opportunity_daily_digest.set(
                bool(saved.get("opportunity_daily_digest_enabled", False))
            )
            var_tg_env.set(str(saved.get("bot_token_env", "TELE_BOT_KEY")))
            var_tg_report_chat.set(str(saved.get("report_chat_id", "")))
            var_tg_opportunity_chat.set(str(saved.get("opportunity_chat_id", "")))
            var_tg_control_chat.set(str(saved.get("control_chat_id", "")))
            var_tg_owner_id.set(str(saved.get("owner_user_id", "")))
            var_tg_operator_ids.set("")
            var_tg_poll_interval.set(str(saved.get("control_poll_interval_seconds", 2.0)))
            var_tg_signal_cooldown.set(str(saved.get("signal_proposal_cooldown_minutes", 15.0)))
            var_tg_opportunity_cooldown.set(
                str(saved.get("opportunity_duplicate_cooldown_minutes", 0.0))
            )
            var_tg_opportunity_batch.set(
                str(saved.get("opportunity_batch_minutes", 5.0))
            )
            var_tg_opportunity_mode.set(
                str(saved.get("opportunity_mode_filter", "ALL"))
            )
            var_tg_opportunity_ckps.set(
                bool(saved.get("opportunity_ckps_enabled", True))
            )
            var_tg_opportunity_ckcs.set(
                bool(saved.get("opportunity_ckcs_enabled", True))
            )
            var_tg_reversal_alerts.set(
                bool(saved.get("position_reversal_alerts_enabled", True))
            )
            var_tg_level_alerts.set(
                bool(saved.get("position_level_alerts_enabled", False))
            )
            var_tg_chunk.set(str(saved.get("chunk_size", 3500)))
            app._set_advisor_status("Telegram settings saved")
            return saved
        except Exception as exc:
            app._set_advisor_status("Telegram settings ERR", str(exc))
            return None

    def _telegram_md_path(channel):
        filename = (
            "telegram_report.md"
            if channel == "report"
            else "telegram_opportunity.md"
        )
        return os.path.join(api_client.paths.advisor_root(), filename)

    def _ensure_telegram_md(channel):
        path = _telegram_md_path(channel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            channel_name = (
                "BÁO CÁO & CẢNH BÁO"
                if channel == "report"
                else "GỢI Ý BOT"
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    f"# {channel_name}\n\n"
                    "Điền nội dung cần gửi vào đây rồi bấm LƯU & GỬI.\n"
                )
        return path

    def _send_telegram_md(channel, text, parent):
        saved = save_telegram_settings()
        if not saved:
            return False
        setting_key = "report_chat_id" if channel == "report" else "opportunity_chat_id"
        channel_name = (
            "BÁO CÁO & CẢNH BÁO"
            if channel == "report"
            else "GỢI Ý BOT"
        )
        chat_id = str(saved.get(setting_key, "") or "").strip()
        if not chat_id:
            messagebox.showwarning(
                "Thiếu Chat ID",
                f"Hãy nhập Chat ID trong khung {channel_name}.",
                parent=parent,
            )
            return False
        try:
            text = str(text or "").strip()
            if not text:
                messagebox.showwarning(
                    "File MD đang trống",
                    "Hãy điền nội dung trước khi gửi.",
                    parent=parent,
                )
                return False
            from telegram_notify.client import TelegramClient

            client = TelegramClient(
                token_env=saved.get("bot_token_env", "TELE_BOT_KEY"),
                allow_insecure_ssl=True,
            )
            result = client.send_long_message(
                chat_id,
                text,
                chunk_size=saved.get("chunk_size", 3500),
                title=f"RAT6 — {channel_name}",
            )
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "Telegram không trả về lý do")
            app._set_advisor_status(f"Đã gửi MD → {channel_name} ({chat_id})")
            messagebox.showinfo(
                "Đã gửi Telegram",
                f"Đã gửi tới nhóm {channel_name}.\nChat ID: {chat_id}",
                parent=parent,
            )
            return True
        except Exception as exc:
            app._set_advisor_status(f"Gửi MD tới {channel_name} lỗi", str(exc))
            messagebox.showerror(
                "Gửi file MD thất bại",
                f"Chat ID: {chat_id}\nLỗi: {exc}",
                parent=parent,
            )
            return False

    def open_telegram_md(channel):
        try:
            path = _ensure_telegram_md(channel)
            title = os.path.basename(path)
            open_advisor_file_editor(
                app,
                path,
                title,
                send_callback=lambda text, parent: _send_telegram_md(
                    channel, text, parent
                ),
            )
        except Exception as exc:
            messagebox.showerror("Mở file MD lỗi", str(exc), parent=top)

    def open_telegram_report_sender():
        saved = save_telegram_settings()
        if not saved:
            return

        sender = ctk.CTkToplevel(top)
        sender.title("RAT-report Sender")
        _fit_popup(sender, 820, 620, 640, 460)
        _bring_popup_to_front(sender)

        body = ctk.CTkFrame(sender, fg_color="#1E1E1E", corner_radius=0)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            body,
            text="SEND TO RAT-REPORT",
            font=("Roboto", 16, "bold"),
            text_color="#80DEEA",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            body,
            text="Paste -> Send. Noi dung dai tu chia chunk.",
            font=("Roboto", 11, "bold"),
            text_color="gray",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        txt_report = ctk.CTkTextbox(body, font=("Consolas", 12))
        txt_report.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        txt_report.focus_set()

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        actions.grid_columnconfigure((0, 1), weight=1)

        def send_report():
            text = txt_report.get("1.0", "end").strip()
            if not text:
                app._set_advisor_status("Telegram report empty")
                return
            diag = telegram_reporter.report_diagnostics()
            app.log_message(
                "[TELEGRAM] Report diagnostics: "
                f"settings={diag.get('settings_path')} "
                f"env={diag.get('token_env')} "
                f"token_present={diag.get('token_present')} "
                f"token_len={diag.get('token_length')} "
                f"insecure_ssl={diag.get('insecure_ssl')} "
                f"chat={diag.get('report_chat_id')}",
                target="manual",
            )
            result = telegram_reporter.send_text_report(
                text,
                title="RAT6 CKVN Advisor Report",
                require_enabled=False,
            )
            if result.get("ok"):
                app._set_advisor_status("Telegram report sent")
                app.log_message(
                    f"[TELEGRAM] Report sent ({result.get('sent', 0)} parts).",
                    target="manual",
                )
            else:
                err = result.get("error", "Telegram report failed")
                result_diag = result.get("diagnostics") or diag
                detail = (
                    f"{err}\n\n"
                    f"Settings: {result_diag.get('settings_path')}\n"
                    f"ENV: {result_diag.get('token_env')} "
                    f"(present={result_diag.get('token_present')}, len={result_diag.get('token_length')})\n"
                    f"Insecure SSL: {result_diag.get('insecure_ssl')}\n"
                    f"Chat ID: {result_diag.get('report_chat_id')}"
                )
                app._set_advisor_status("Telegram report ERR", err)
                app.log_message(f"[TELEGRAM] Report failed: {detail}", error=True, target="manual")
                messagebox.showerror("Telegram report", detail, parent=sender)

        def clear_report():
            txt_report.delete("1.0", "end")
            app._set_advisor_status("Telegram report cleared")

        ctk.CTkButton(
            actions,
            text="Send RAT-report",
            height=34,
            fg_color="#1f538d",
            hover_color="#14375e",
            command=send_report,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(
            actions,
            text="Xoa",
            height=34,
            fg_color="#424242",
            hover_color="#616161",
            command=clear_report,
        ).grid(row=0, column=1, sticky="ew", padx=(5, 0))

    def open_telegram_help():
        helper = ctk.CTkToplevel(top)
        helper.title("CONTROL — Hướng dẫn")
        _fit_popup(helper, 760, 560, 620, 460)
        _bring_popup_to_front(helper)

        body = ctk.CTkFrame(helper, fg_color="#1E1E1E", corner_radius=0)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            body,
            text="CONTROL — HƯỚNG DẪN",
            font=("Roboto", 16, "bold"),
            text_color="#80DEEA",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            body,
            text="Lệnh mẫu và cách chủ tài khoản duyệt bằng nút Telegram.",
            font=("Roboto", 11, "bold"),
            text_color="#FBC02D",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        txt_help = ctk.CTkTextbox(body, font=("Consolas", 12), wrap="word")
        txt_help.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        txt_help.insert("1.0", CONTROL_HELP_TEXT)
        txt_help.configure(state="disabled")

        def copy_help():
            try:
                helper.clipboard_clear()
                helper.clipboard_append(CONTROL_HELP_TEXT)
                helper.update()
                app._set_advisor_status("Telegram help copied")
            except Exception:
                pass

        ctk.CTkButton(
            body,
            text="Copy",
            height=34,
            fg_color="#424242",
            hover_color="#616161",
            command=copy_help,
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

    ctk.CTkButton(
        tg_buttons,
        text="HƯỚNG DẪN ĐIỀU KHIỂN",
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=open_telegram_help,
    ).grid(row=0, column=3, sticky="ew", padx=(5, 0))

    ctk.CTkButton(
        tg_buttons,
        text="LƯU TELEGRAM",
        height=30,
        fg_color="#00695C",
        hover_color="#004D40",
        command=save_telegram_settings,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
    ctk.CTkButton(
        tg_buttons,
        text="MD BÁO CÁO",
        height=30,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=lambda: open_telegram_md("report"),
    ).grid(row=0, column=1, sticky="ew", padx=5)
    ctk.CTkButton(
        tg_buttons,
        text="MD GỢI Ý BOT",
        height=30,
        fg_color="#1f538d",
        hover_color="#14375e",
        command=lambda: open_telegram_md("opportunity"),
    ).grid(row=0, column=2, sticky="ew", padx=5)

    # Giao diện Telegram chính: đủ setting nhưng chia đúng nghiệp vụ và tận dụng chiều ngang.
    telegram_page = _speed_up_scroll(
        ctk.CTkScrollableFrame(tab_telegram, fg_color="transparent"), factor=7
    )
    telegram_page.pack(fill="both", expand=True, padx=8, pady=5)
    telegram_page.grid_columnconfigure((0, 1), weight=1, uniform="telegram_groups")

    token_card = ctk.CTkFrame(telegram_page, fg_color="#252526", corner_radius=8)
    token_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
    token_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        token_card, text="TELEGRAM BOT", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        token_card,
        text="Một token dùng chung; mỗi nghiệp vụ gửi tới Chat ID riêng.",
        font=("Roboto", 9),
        text_color="#B0BEC5",
    ).grid(row=0, column=1, columnspan=3, sticky="w", padx=6, pady=(8, 2))
    ctk.CTkLabel(token_card, text="Token", font=("Roboto", 10, "bold")).grid(
        row=1, column=0, sticky="w", padx=10, pady=(2, 8)
    )
    compact_token_entry = ctk.CTkEntry(
        token_card,
        textvariable=var_tg_token,
        show="*",
        height=32,
        placeholder_text="Dán token mới; để trống nếu đã lưu",
    )
    compact_token_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(2, 8))
    compact_token_visible = tk.BooleanVar(value=False)
    compact_token_status = ctk.CTkLabel(
        token_card,
        text="CÓ TOKEN" if secret_env.user_environment_secret_present(var_tg_env.get()) else "CHƯA CÓ",
        font=("Roboto", 9, "bold"),
        text_color="#81C784" if secret_env.user_environment_secret_present(var_tg_env.get()) else "#FFB74D",
    )
    compact_token_status.grid(row=0, column=4, sticky="e", padx=(6, 10), pady=(8, 2))

    def toggle_compact_token():
        compact_token_visible.set(not compact_token_visible.get())
        compact_token_entry.configure(show="" if compact_token_visible.get() else "*")

    def save_compact_token():
        try:
            env_name = (var_tg_env.get() or "TELE_BOT_KEY").strip() or "TELE_BOT_KEY"
            secret_env.set_user_environment_secret(env_name, var_tg_token.get())
            var_tg_token.set("")
            compact_token_visible.set(False)
            compact_token_entry.configure(show="*")
            compact_token_status.configure(text="ĐÃ LƯU", text_color="#81C784")
            app._set_advisor_status("Telegram token đã lưu")
        except Exception as exc:
            compact_token_status.configure(text=f"LỖI: {exc}", text_color="#EF5350")

    ctk.CTkButton(
        token_card, text="HIỆN", width=65, height=32, fg_color="#424242", command=toggle_compact_token
    ).grid(row=1, column=2, padx=3, pady=(2, 8))
    ctk.CTkButton(
        token_card, text="LƯU TOKEN", width=105, height=32, fg_color="#2E7D32", command=save_compact_token
    ).grid(row=1, column=3, padx=3, pady=(2, 8))

    def compact_field(parent, row, label, variable, hint=""):
        ctk.CTkLabel(parent, text=label, font=("Roboto", 10, "bold")).grid(
            row=row, column=0, sticky="w", padx=(10, 5), pady=3
        )
        ctk.CTkEntry(parent, textvariable=variable, height=30).grid(
            row=row, column=1, sticky="ew", padx=(0, 10), pady=3
        )
        if hint:
            ctk.CTkLabel(
                parent,
                text=hint,
                font=("Roboto", 9),
                text_color="#90CAF9",
                anchor="w",
                justify="left",
                wraplength=300,
            ).grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))

    report_card = ctk.CTkFrame(telegram_page, fg_color="#252526", corner_radius=8)
    report_card.grid(row=1, column=0, sticky="nsew", padx=(0, 3), pady=3)
    report_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        report_card, text="BÁO CÁO & CẢNH BÁO", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 3))
    compact_field(
        report_card,
        1,
        "Chat ID",
        var_tg_report_chat,
        "Nhận kết quả OpenAI và các lỗi hệ thống quan trọng; không nhận cảnh báo biến động giá.",
    )
    ctk.CTkCheckBox(
        report_card,
        text="Tự gửi KẾT QUẢ OPENAI sau khi API chạy xong",
        variable=var_tg_enabled,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=4)
    ctk.CTkCheckBox(
        report_card,
        text="Gửi cảnh báo hệ thống quan trọng",
        variable=var_tg_system_alerts,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=4)
    compact_field(
        report_card,
        5,
        "Độ dài mỗi tin",
        var_tg_chunk,
        "Mặc định 3500; báo cáo dài sẽ tự chia phần.",
    )
    suggestion_card = ctk.CTkFrame(telegram_page, fg_color="#252526", corner_radius=8)
    suggestion_card.grid(row=1, column=1, sticky="nsew", padx=(3, 0), pady=3)
    suggestion_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        suggestion_card, text="GỢI Ý BOT — CHỈ THÔNG BÁO", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 3))
    compact_field(
        suggestion_card,
        1,
        "Chat ID",
        var_tg_opportunity_chat,
        "Gửi các cơ hội xuất hiện khi BOT không vào lệnh; không có nút mua/bán.",
    )
    ctk.CTkCheckBox(
        suggestion_card,
        text="Bật gửi gợi ý",
        variable=var_tg_opportunity_enabled,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=3, column=0, sticky="w", padx=10, pady=3)
    var_tg_opportunity_mode.set("ALL")
    ctk.CTkLabel(
        suggestion_card,
        text="Tất cả PAPER/REAL",
        text_color="#90CAF9",
        anchor="e",
    ).grid(row=3, column=1, sticky="ew", padx=(0, 10), pady=3)
    market_checks = ctk.CTkFrame(suggestion_card, fg_color="transparent")
    market_checks.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=2)
    ctk.CTkCheckBox(
        market_checks, text="CKPS", variable=var_tg_opportunity_ckps, font=("Roboto", 10, "bold"),
        checkbox_width=18, checkbox_height=18
    ).pack(side="left", padx=(0, 20))
    ctk.CTkCheckBox(
        market_checks, text="CKCS", variable=var_tg_opportunity_ckcs, font=("Roboto", 10, "bold"),
        checkbox_width=18, checkbox_height=18
    ).pack(side="left")
    compact_field(
        suggestion_card,
        5,
        "Gom gợi ý trong (phút)",
        var_tg_opportunity_batch,
    )
    ctk.CTkLabel(
        suggestion_card,
        text="Mặc định 5 phút; chỉ gom gợi ý BOT. Cảnh báo biến động giá vẫn gửi ngay.",
        font=("Roboto", 9),
        text_color="#90CAF9",
        wraplength=300,
        anchor="w",
        justify="left",
    ).grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(1, 5))
    ctk.CTkCheckBox(
        suggestion_card,
        text="Cảnh báo đảo chiều vị thế đang mở",
        variable=var_tg_reversal_alerts,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=7, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))
    ctk.CTkCheckBox(
        suggestion_card,
        text="Cảnh báo vị thế gần SL/TP",
        variable=var_tg_level_alerts,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=8, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))
    ctk.CTkCheckBox(
        suggestion_card,
        text="Gửi thêm bản tổng 09:30 và 14:50",
        variable=var_tg_opportunity_daily_digest,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=9, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 7))
    manual_report_actions = ctk.CTkFrame(telegram_page, fg_color="#252526", corner_radius=8)
    manual_report_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 5))
    manual_report_actions.grid_columnconfigure((0, 1), weight=1)
    ctk.CTkLabel(
        manual_report_actions,
        text=(
            "Mỗi nhóm có một file MD riêng. Bấm đúng nhóm, điền nội dung rồi chọn LƯU & GỬI."
        ),
        font=("Roboto", 9),
        text_color="#90CAF9",
        anchor="w",
    ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(7, 4))
    ctk.CTkButton(
        manual_report_actions,
        text="MD — BÁO CÁO & CẢNH BÁO",
        height=36,
        fg_color="#9A7B00",
        hover_color="#755E00",
        command=lambda: open_telegram_md("report"),
    ).grid(row=1, column=0, sticky="ew", padx=(10, 4), pady=(0, 9))
    ctk.CTkButton(
        manual_report_actions,
        text="MD — GỢI Ý BOT",
        height=36,
        fg_color="#1f538d",
        hover_color="#14375e",
        command=lambda: open_telegram_md("opportunity"),
    ).grid(row=1, column=1, sticky="ew", padx=(4, 10), pady=(0, 9))

    control_card = ctk.CTkFrame(telegram_page, fg_color="#252526", corner_radius=8)
    control_card.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 5))
    control_card.grid_columnconfigure(1, weight=1)
    control_card.grid_columnconfigure(3, weight=1)
    ctk.CTkLabel(
        control_card, text="CONTROL — TÙY CHỌN NÂNG CAO", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        control_card,
        text="Chỉ bật khi muốn đọc lệnh Telegram hoặc duyệt signal bằng nút. Chỉ nhận tin thì để tắt.",
        font=("Roboto", 9),
        text_color="#B0BEC5",
    ).grid(row=0, column=1, columnspan=3, sticky="e", padx=10, pady=(8, 2))
    ctk.CTkCheckBox(
        control_card, text="Bật CONTROL", variable=var_tg_control_enabled, font=("Roboto", 10, "bold"),
        checkbox_width=18, checkbox_height=18
    ).grid(row=1, column=0, sticky="w", padx=10, pady=3)
    ctk.CTkEntry(control_card, textvariable=var_tg_control_chat, height=30, placeholder_text="Chat ID CONTROL").grid(
        row=1, column=1, sticky="ew", padx=(0, 10), pady=3
    )
    ctk.CTkLabel(control_card, text="Owner User ID", font=("Roboto", 10, "bold")).grid(
        row=1, column=2, sticky="w", padx=(8, 5), pady=3
    )
    ctk.CTkEntry(control_card, textvariable=var_tg_owner_id, height=30).grid(
        row=1, column=3, sticky="ew", padx=(0, 10), pady=3
    )
    ctk.CTkCheckBox(
        control_card, text="Duyệt signal bằng nút Telegram", variable=var_tg_signal_enabled,
        font=("Roboto", 10, "bold"), checkbox_width=18, checkbox_height=18
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=3)
    ctk.CTkLabel(control_card, text="Đọc CONTROL mỗi (giây)", font=("Roboto", 10, "bold")).grid(
        row=2, column=2, sticky="w", padx=(8, 5), pady=3
    )
    ctk.CTkEntry(control_card, textvariable=var_tg_poll_interval, height=30).grid(
        row=2, column=3, sticky="ew", padx=(0, 10), pady=3
    )
    ctk.CTkLabel(control_card, text="Cooldown duyệt (phút)", font=("Roboto", 10, "bold")).grid(
        row=3, column=2, sticky="w", padx=(8, 5), pady=3
    )
    ctk.CTkEntry(control_card, textvariable=var_tg_signal_cooldown, height=30).grid(
        row=3, column=3, sticky="ew", padx=(0, 10), pady=3
    )
    ctk.CTkLabel(
        control_card,
        text="Owner User ID giới hạn người được phép điều khiển.",
        font=("Roboto", 9),
        text_color="#90CAF9",
    ).grid(row=4, column=2, columnspan=2, sticky="w", padx=(8, 10), pady=(0, 6))

    telegram_actions = ctk.CTkFrame(telegram_page, fg_color="transparent")
    telegram_actions.grid(row=4, column=0, columnspan=2, sticky="ew")
    telegram_actions.grid_columnconfigure((0, 1), weight=1)
    ctk.CTkButton(
        telegram_actions, text="LƯU TOÀN BỘ", height=40, fg_color="#00695C", command=save_telegram_settings
    ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
    ctk.CTkButton(
        telegram_actions, text="HƯỚNG DẪN CONTROL", height=40, fg_color="#424242", command=open_telegram_help
    ).grid(row=0, column=1, sticky="ew", padx=(3, 0))
    # Không destroy khối xử lý cũ: CTkEntry gắn StringVar vẫn cần tồn tại để
    # CustomTkinter không gọi callback vào widget đã bị xóa.

    files_box = ctk.CTkFrame(api_panel, fg_color="transparent", corner_radius=6)
    files_box.pack(fill="x", padx=10, pady=(10, 8))
    ctk.CTkLabel(
        files_box,
        text="NỘI DUNG BOT ADVISOR",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        files_box,
        text="Chỉ các file nội dung dưới đây được sửa tay; setting và lịch sử giao dịch do app tự sinh.",
        font=("Roboto", 10, "bold"),
        text_color="gray",
    ).pack(anchor="w", padx=10, pady=(0, 8))
    file_buttons = ctk.CTkFrame(files_box, fg_color="transparent")
    file_buttons.pack(fill="x", padx=10, pady=(0, 10))
    for idx in range(5):
        file_buttons.grid_columnconfigure(idx, weight=1)

    def _open_latest_advisor_response():
        response_path = api_client.paths.advisor_response_path()
        if not os.path.isfile(response_path):
            messagebox.showinfo(
                "Chưa có kết quả API",
                "advisor_response.md chỉ được tạo sau khi gọi API thành công.",
                parent=top,
            )
            return
        open_advisor_file_editor(app, response_path, "advisor_response.md")

    ctk.CTkButton(
        file_buttons,
        text="PROMPT",
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=lambda: open_advisor_file_editor(app, api_client.paths.advisor_prompt_path(), "advisor_prompt.md"),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ctk.CTkButton(
        file_buttons,
        text="LUỒNG BOT",
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=lambda: open_advisor_file_editor(app, api_client.paths.advisor_flow_path(), "advisor_flow.md"),
    ).grid(row=0, column=1, sticky="ew", padx=4)
    ctk.CTkButton(
        file_buttons,
        text="YÊU CẦU CỦA NGÀI",
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=lambda: open_advisor_file_editor(app, api_client.paths.user_context_path(), "user_context.md"),
    ).grid(row=0, column=2, sticky="ew", padx=4)
    ctk.CTkButton(
        file_buttons,
        text="Ý KIẾN CHUYÊN GIA",
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=lambda: open_advisor_file_editor(app, api_client.paths.expert_context_path(), "expert_context.md"),
    ).grid(row=0, column=3, sticky="ew", padx=(4, 0))
    ctk.CTkButton(
        file_buttons,
        text="KẾT QUẢ GẦN NHẤT",
        height=30,
        fg_color="#424242",
        hover_color="#616161",
        command=_open_latest_advisor_response,
    ).grid(row=0, column=4, sticky="ew", padx=(4, 0))

    edit_top = ctk.CTkFrame(api_panel, fg_color="transparent", corner_radius=6)
    edit_top.pack(fill="x", padx=10, pady=(0, 8))
    edit_top.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        edit_top,
        text="MODEL VÀ GIỚI HẠN API",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        edit_top,
        text="Dùng chung cho BOT Advisor và CKCS API. Thay đổi tại đây không làm thay đổi logic BOT.",
        font=("Roboto", 10, "bold"),
        text_color="gray",
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

    var_provider = tk.StringVar(value=str(api_settings.get("provider", api_client.DEFAULT_PROVIDER)))
    var_model = tk.StringVar(value=str(api_settings.get("model", api_client.DEFAULT_MODEL)))
    var_reasoning = tk.StringVar(value=str(api_settings.get("reasoning_effort", "medium")))
    var_prompt_limit = tk.StringVar(value=str(api_settings.get("advisor_prompt_limit", 200000)))
    var_flow_limit = tk.StringVar(value=str(api_settings.get("advisor_flow_limit", 200000)))
    var_context_limit = tk.StringVar(value=str(api_settings.get("user_context_limit", 100000)))
    var_tech_limit = tk.StringVar(value=str(api_settings.get("technical_settings_limit", 1000000)))
    var_workbook_rows = tk.StringVar(value=str(api_settings.get("workbook_limit_rows", 80)))
    var_response_limit = tk.StringVar(value=str(api_settings.get("previous_response_limit", 60000)))
    var_max_output = tk.StringVar(value=str(api_settings.get("max_output_tokens", api_client.DEFAULT_MAX_OUTPUT_TOKENS)))
    var_web_search = tk.BooleanVar(value=bool(api_settings.get("web_search_enabled", True)))

    def _edit_row(label, variable, row, values=None):
        ctk.CTkLabel(edit_top, text=label, font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        if values:
            ctk.CTkOptionMenu(edit_top, values=values, variable=variable, width=150, height=28).grid(row=row, column=1, sticky="e", padx=10, pady=4)
        else:
            ctk.CTkEntry(edit_top, textvariable=variable, width=150, height=28).grid(row=row, column=1, sticky="e", padx=10, pady=4)

    # Provider + Model (model phụ thuộc provider đang chọn).
    ctk.CTkLabel(edit_top, text="Nhà cung cấp", font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=2, column=0, sticky="w", padx=10, pady=4)
    ctk.CTkOptionMenu(edit_top, values=list(api_client._providers().keys()), variable=var_provider, width=150, height=28, command=lambda _v=None: _on_provider_change()).grid(row=2, column=1, sticky="e", padx=10, pady=4)

    ctk.CTkLabel(edit_top, text="Model", font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=3, column=0, sticky="w", padx=10, pady=4)
    cbo_model = ctk.CTkOptionMenu(edit_top, values=api_client.models_for(var_provider.get()), variable=var_model, width=150, height=28)
    cbo_model.grid(row=3, column=1, sticky="e", padx=10, pady=4)

    def _on_provider_change():
        models = api_client.models_for(var_provider.get())
        cbo_model.configure(values=models)
        if var_model.get() not in models:
            var_model.set(models[0] if models else "")

    _edit_row("Mức suy luận", var_reasoning, 4, ["none", "low", "medium", "high", "xhigh", "max"])
    _edit_row("Giới hạn technical_settings (ký tự)", var_tech_limit, 5)
    _edit_row("Số dòng Excel mỗi sheet", var_workbook_rows, 6)
    _edit_row("Giới hạn token đầu ra", var_max_output, 7)

    ctk.CTkCheckBox(
        edit_top,
        text="Bật tìm kiếm web",
        variable=var_web_search,
        font=("Roboto", 11, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=8, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 4))

    limit_buttons = ctk.CTkFrame(edit_top, fg_color="transparent")
    limit_buttons.grid(row=9, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 10))

    def save_api_edit():
        try:
            saved = api_client.save_api_settings(
                {
                    "provider": var_provider.get(),
                    "model": var_model.get(),
                    "reasoning_effort": var_reasoning.get(),
                    "advisor_prompt_limit": var_prompt_limit.get(),
                    "advisor_flow_limit": var_flow_limit.get(),
                    "user_context_limit": var_context_limit.get(),
                    "technical_settings_limit": var_tech_limit.get(),
                    "workbook_limit_rows": var_workbook_rows.get(),
                    "previous_response_limit": var_response_limit.get(),
                    "max_output_tokens": var_max_output.get(),
                    "web_search_enabled": var_web_search.get(),
                }
            )
            var_provider.set(str(saved.get("provider", api_client.DEFAULT_PROVIDER)))
            cbo_model.configure(values=api_client.models_for(var_provider.get()))
            var_model.set(str(saved.get("model", api_client.DEFAULT_MODEL)))
            var_reasoning.set(str(saved.get("reasoning_effort", "medium")))
            var_prompt_limit.set(str(saved.get("advisor_prompt_limit")))
            var_flow_limit.set(str(saved.get("advisor_flow_limit")))
            var_context_limit.set(str(saved.get("user_context_limit")))
            var_tech_limit.set(str(saved.get("technical_settings_limit")))
            var_workbook_rows.set(str(saved.get("workbook_limit_rows")))
            var_response_limit.set(str(saved.get("previous_response_limit")))
            var_max_output.set(str(saved.get("max_output_tokens")))
            var_web_search.set(bool(saved.get("web_search_enabled", True)))
            app.preview_advisor_api_payload()
            app._set_advisor_status("API settings saved")
        except Exception as exc:
            app._set_advisor_status("API settings ERR", str(exc))

    ctk.CTkButton(limit_buttons, text="LƯU CẤU HÌNH API", height=30, fg_color="#00695C", hover_color="#004D40", command=save_api_edit).pack(side="left", fill="x", expand=True, padx=(0, 6))
    ctk.CTkButton(limit_buttons, text="ƯỚC TÍNH TOKEN / PHÍ", width=190, height=30, fg_color="#1f538d", hover_color="#14375e", command=app.preview_advisor_api_payload).pack(side="right")

    preview_box = ctk.CTkFrame(api_panel, fg_color="transparent", corner_radius=6)
    preview_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    app.lbl_advisor_api_preview = ctk.CTkLabel(
        preview_box,
        text=getattr(app, "advisor_api_preview_text", "API payload: not estimated"),
        font=("Roboto", 12, "bold"),
        text_color="#E3F2FD",
        anchor="w",
        justify="left",
    )
    app.lbl_advisor_api_preview.pack(fill="x", padx=10, pady=(8, 4))
    app.lbl_advisor_api_preview_detail = ctk.CTkLabel(
        preview_box,
        text=getattr(app, "advisor_api_preview_detail_text", ""),
        font=("Consolas", 11, "bold"),
        text_color="#B3E5FC",
        anchor="w",
        justify="left",
        wraplength=560,
    )
    app.lbl_advisor_api_preview_detail.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    api_page = _speed_up_scroll(ctk.CTkScrollableFrame(tab_edit, fg_color="transparent"), factor=7)
    api_page.pack(fill="both", expand=True, padx=8, pady=5)

    compact_key_card = ctk.CTkFrame(api_page, fg_color="#252526", corner_radius=8)
    compact_key_card.pack(fill="x", pady=(0, 6))
    compact_key_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        compact_key_card, text="OPENAI KEY", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        compact_key_card,
        text="Lưu trong Windows User Environment; không ghi key vào JSON hoặc Git.",
        font=("Roboto", 9),
        text_color="#FBC02D",
    ).grid(row=0, column=1, columnspan=3, sticky="w", padx=6, pady=(8, 2))
    compact_openai_key = tk.StringVar(value="")
    compact_openai_visible = tk.BooleanVar(value=False)
    compact_openai_entry = ctk.CTkEntry(
        compact_key_card,
        textvariable=compact_openai_key,
        show="*",
        height=32,
        placeholder_text="Dán key mới; để trống nếu đã lưu",
    )
    compact_openai_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(10, 5), pady=(2, 8))
    compact_openai_status = ctk.CTkLabel(
        compact_key_card,
        text="CÓ KEY" if secret_env.user_environment_secret_present("OPENAI_API_KEY") else "CHƯA CÓ",
        font=("Roboto", 9, "bold"),
        text_color="#81C784" if secret_env.user_environment_secret_present("OPENAI_API_KEY") else "#FFB74D",
    )
    compact_openai_status.grid(row=0, column=4, sticky="e", padx=(5, 10), pady=(8, 2))

    def toggle_compact_openai():
        compact_openai_visible.set(not compact_openai_visible.get())
        compact_openai_entry.configure(show="" if compact_openai_visible.get() else "*")

    def save_compact_openai():
        try:
            secret_env.set_user_environment_secret("OPENAI_API_KEY", compact_openai_key.get())
            compact_openai_key.set("")
            compact_openai_visible.set(False)
            compact_openai_entry.configure(show="*")
            compact_openai_status.configure(text="ĐÃ LƯU", text_color="#81C784")
            app._set_advisor_status("OPENAI_API_KEY đã lưu")
        except Exception as exc:
            compact_openai_status.configure(text=f"LỖI: {exc}", text_color="#EF5350")

    ctk.CTkButton(
        compact_key_card, text="HIỆN", width=65, height=32, fg_color="#424242", command=toggle_compact_openai
    ).grid(row=1, column=2, padx=3, pady=(2, 8))
    ctk.CTkButton(
        compact_key_card, text="LƯU KEY", width=100, height=32, fg_color="#2E7D32", command=save_compact_openai
    ).grid(row=1, column=3, padx=3, pady=(2, 8))

    model_card = ctk.CTkFrame(api_page, fg_color="#252526", corner_radius=8)
    model_card.pack(fill="x", pady=3)
    for column in (1, 3, 5):
        model_card.grid_columnconfigure(column, weight=1)
    ctk.CTkLabel(
        model_card, text="MODEL VÀ CÁCH GỌI", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=6, sticky="w", padx=10, pady=(8, 3))
    ctk.CTkLabel(model_card, text="Nhà cung cấp", font=("Roboto", 10, "bold")).grid(
        row=1, column=0, sticky="w", padx=(10, 4), pady=4
    )
    compact_provider_menu = ctk.CTkOptionMenu(
        model_card, values=list(api_client._providers().keys()), variable=var_provider, height=30
    )
    compact_provider_menu.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=4)
    ctk.CTkLabel(model_card, text="Model", font=("Roboto", 10, "bold")).grid(
        row=1, column=2, sticky="w", padx=(4, 4), pady=4
    )
    compact_model_menu = ctk.CTkOptionMenu(
        model_card, values=api_client.models_for(var_provider.get()), variable=var_model, height=30
    )
    compact_model_menu.grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=4)
    ctk.CTkLabel(model_card, text="Mức suy luận", font=("Roboto", 10, "bold")).grid(
        row=1, column=4, sticky="w", padx=(4, 4), pady=4
    )
    ctk.CTkOptionMenu(
        model_card,
        values=["none", "low", "medium", "high", "xhigh", "max"],
        variable=var_reasoning,
        height=30,
    ).grid(row=1, column=5, sticky="ew", padx=(0, 10), pady=4)
    ctk.CTkCheckBox(
        model_card,
        text="Bật tìm kiếm web",
        variable=var_web_search,
        font=("Roboto", 10, "bold"),
        checkbox_width=18,
        checkbox_height=18,
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(3, 8))
    ctk.CTkLabel(
        model_card,
        text="Dùng chung cho BÁO CÁO BOT và BÁO CÁO CKCS; không thay đổi logic giao dịch.",
        font=("Roboto", 9),
        text_color="#90CAF9",
    ).grid(row=2, column=2, columnspan=4, sticky="e", padx=10, pady=(3, 8))

    def compact_provider_changed(_value=None):
        models = api_client.models_for(var_provider.get())
        compact_model_menu.configure(values=models)
        cbo_model.configure(values=models)
        if var_model.get() not in models:
            var_model.set(models[0] if models else "")

    compact_provider_menu.configure(command=compact_provider_changed)

    auto_card = ctk.CTkFrame(api_page, fg_color="#252526", corner_radius=8)
    auto_card.pack(fill="x", pady=3)
    auto_card.grid_columnconfigure((0, 1, 2, 3), weight=1)
    ctk.CTkLabel(
        auto_card, text="CKCS API — TÙY CHỌN", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        auto_card,
        text="Bật để tự gửi báo cáo + private_context.md lên OpenAI; chỉ lưu kết quả, không tạo lệnh CKCS.",
        font=("Roboto", 9),
        text_color="#B0BEC5",
    ).grid(row=0, column=1, columnspan=3, sticky="e", padx=10, pady=(8, 2))
    ctk.CTkCheckBox(
        auto_card, text="Tự gọi sau báo cáo sáng", variable=app.var_ckcs_send_api_morning,
        command=lambda: _save_advisor_schedule(silent=True), font=("Roboto", 10, "bold"),
        checkbox_width=18, checkbox_height=18
    ).grid(row=1, column=0, sticky="w", padx=10, pady=4)
    ctk.CTkCheckBox(
        auto_card, text="Tự gọi sau báo cáo chiều", variable=app.var_ckcs_send_api_afternoon,
        command=lambda: _save_advisor_schedule(silent=True), font=("Roboto", 10, "bold"),
        checkbox_width=18, checkbox_height=18
    ).grid(row=1, column=1, sticky="w", padx=10, pady=4)
    ctk.CTkButton(
        auto_card, text="GỌI API SÁNG", height=34,
        command=lambda: app.run_ckcs_session_ui("morning", send_api=True)
    ).grid(row=1, column=2, sticky="ew", padx=4, pady=4)
    ctk.CTkButton(
        auto_card, text="GỌI API CHIỀU", height=34,
        command=lambda: app.run_ckcs_session_ui("afternoon", send_api=True)
    ).grid(row=1, column=3, sticky="ew", padx=(4, 10), pady=4)
    ctk.CTkButton(
        auto_card, text="MỞ KẾT QUẢ SÁNG", height=32, fg_color="#424242",
        command=lambda: app.open_ckcs_session_file("morning", response=True)
    ).grid(row=2, column=2, sticky="ew", padx=4, pady=(0, 8))
    ctk.CTkButton(
        auto_card, text="MỞ KẾT QUẢ CHIỀU", height=32, fg_color="#424242",
        command=lambda: app.open_ckcs_session_file("afternoon", response=True)
    ).grid(row=2, column=3, sticky="ew", padx=(4, 10), pady=(0, 8))

    content_card = ctk.CTkFrame(bot_files_slot, fg_color="#1E1E1E", corner_radius=8)
    content_card.pack(fill="x")
    ctk.CTkLabel(
        content_card, text="FILE BOT ADVISOR", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).pack(anchor="w", padx=10, pady=(8, 1))
    ctk.CTkLabel(
        content_card,
        text="Bốn file đầu là nội dung gửi AI; JSON và Excel được tạo mới từ setting, lệnh và kết quả BOT.",
        font=("Roboto", 9),
        text_color="#B0BEC5",
    ).pack(anchor="w", padx=10, pady=(0, 5))
    compact_files = ctk.CTkFrame(content_card, fg_color="transparent")
    compact_files.pack(fill="x", padx=10, pady=(0, 4))
    for column in range(4):
        compact_files.grid_columnconfigure(column, weight=1)
    file_specs = [
        ("advisor_prompt.md", lambda: open_advisor_file_editor(app, api_client.paths.advisor_prompt_path(), "advisor_prompt.md")),
        ("advisor_flow.md", lambda: open_advisor_file_editor(app, api_client.paths.advisor_flow_path(), "advisor_flow.md")),
        ("user_context.md", lambda: open_advisor_file_editor(app, api_client.paths.user_context_path(), "user_context.md")),
        ("expert_context.md", lambda: open_advisor_file_editor(app, api_client.paths.expert_context_path(), "expert_context.md")),
    ]
    for column, (label, command) in enumerate(file_specs):
        ctk.CTkButton(
            compact_files, text=label, height=32, font=("Roboto", 9, "bold"), fg_color="#424242", command=command
        ).grid(row=0, column=column, sticky="ew", padx=3)

    def _open_generated_advisor_file(path, label):
        if not os.path.isfile(path):
            messagebox.showinfo(
                "AI Advisor",
                f"Chưa có {label}.\nBấm TẠO / LÀM MỚI FILE BOT trước.",
                parent=top,
            )
            return
        os.startfile(os.path.abspath(path))

    generated_files = ctk.CTkFrame(content_card, fg_color="transparent")
    generated_files.pack(fill="x", padx=10, pady=(0, 8))
    generated_files.grid_columnconfigure((0, 1, 2), weight=1)
    ctk.CTkButton(
        generated_files,
        text="technical_settings.json",
        height=32,
        font=("Roboto", 9, "bold"),
        fg_color="#37474F",
        command=lambda: _open_generated_advisor_file(
            api_client.paths.technical_settings_path(), "technical_settings.json"
        ),
    ).grid(row=0, column=0, sticky="ew", padx=3)
    ctk.CTkButton(
        generated_files,
        text="advisor_export.xlsx",
        height=32,
        font=("Roboto", 9, "bold"),
        fg_color="#37474F",
        command=lambda: _open_generated_advisor_file(
            api_client.paths.export_path(), "advisor_export.xlsx"
        ),
    ).grid(row=0, column=1, sticky="ew", padx=3)
    ctk.CTkButton(
        generated_files,
        text="advisor_response.md",
        height=32,
        font=("Roboto", 9, "bold"),
        fg_color="#37474F",
        command=_open_latest_advisor_response,
    ).grid(row=0, column=2, sticky="ew", padx=3)

    limits_card = ctk.CTkFrame(api_page, fg_color="#252526", corner_radius=8)
    limits_card.pack(fill="x", pady=3)
    limits_card.grid_columnconfigure((1, 3, 5, 7), weight=1)
    ctk.CTkLabel(
        limits_card, text="GIỚI HẠN DỮ LIỆU GỬI API", font=("Roboto", 12, "bold"), text_color="#80DEEA"
    ).grid(row=0, column=0, columnspan=8, sticky="w", padx=10, pady=(8, 2))

    limit_specs = [
        ("Prompt", var_prompt_limit),
        ("Luồng BOT", var_flow_limit),
        ("Context", var_context_limit),
        ("Setting", var_tech_limit),
        ("Dòng Excel", var_workbook_rows),
        ("Kết quả cũ", var_response_limit),
        ("Token đầu ra", var_max_output),
    ]
    for index, (label, variable) in enumerate(limit_specs):
        row = 1 + index // 4
        pair = index % 4
        column = pair * 2
        ctk.CTkLabel(limits_card, text=label, font=("Roboto", 9, "bold")).grid(
            row=row, column=column, sticky="w", padx=(10 if column == 0 else 5, 3), pady=4
        )
        ctk.CTkEntry(limits_card, textvariable=variable, height=30).grid(
            row=row, column=column + 1, sticky="ew", padx=(0, 8), pady=4
        )
    ctk.CTkLabel(
        limits_card,
        text="Các số trên giới hạn lượng dữ liệu gửi để kiểm soát kích thước và phí; không ảnh hưởng dữ liệu lưu trên máy.",
        font=("Roboto", 9),
        text_color="#90CAF9",
    ).grid(row=3, column=0, columnspan=8, sticky="w", padx=10, pady=(0, 7))

    api_actions = ctk.CTkFrame(api_page, fg_color="transparent")
    api_actions.pack(fill="x", pady=(4, 2))
    ctk.CTkButton(
        api_actions, text="LƯU TOÀN BỘ CẤU HÌNH API", height=40, fg_color="#00695C", command=save_api_edit
    ).pack(side="left", fill="x", expand=True, padx=(0, 4))
    ctk.CTkButton(
        api_actions, text="ƯỚC TÍNH TOKEN / PHÍ", width=210, height=40, command=app.preview_advisor_api_payload
    ).pack(side="right", padx=(4, 0))
    app.lbl_advisor_api_preview = ctk.CTkLabel(
        api_page,
        text=getattr(app, "advisor_api_preview_text", "Chưa ước tính dữ liệu API"),
        font=("Roboto", 10, "bold"),
        text_color="#E3F2FD",
        anchor="w",
    )
    app.lbl_advisor_api_preview.pack(fill="x", pady=(3, 0))
    app.lbl_advisor_api_preview_detail = ctk.CTkLabel(
        api_page,
        text=getattr(app, "advisor_api_preview_detail_text", ""),
        font=("Consolas", 9),
        text_color="#B3E5FC",
        anchor="w",
        justify="left",
        wraplength=900,
    )
    app.lbl_advisor_api_preview_detail.pack(fill="x", pady=(0, 5))
    tabs.set("BÁO CÁO BOT")
    top.after_idle(lambda: _bring_popup_to_front(top, delay_ms=300))
    return top

# --- BẢNG MÀU & FONT CHUẨN ---

def open_advisor_file_editor(app, path, title, send_callback=None):
    top = ctk.CTkToplevel(app)
    top.title(f"Edit {title}")
    _fit_popup(top, 760, 620, 620, 460)
    _bring_popup_to_front(top)

    root = ctk.CTkFrame(top, fg_color="#1E1E1E", corner_radius=0)
    root.pack(fill="both", expand=True, padx=10, pady=10)
    ctk.CTkLabel(
        root,
        text=title,
        font=("Roboto", 16, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=8, pady=(6, 2))
    ctk.CTkLabel(
        root,
        text=path,
        font=("Consolas", 10),
        text_color="gray",
    ).pack(anchor="w", padx=8, pady=(0, 8))

    text = ctk.CTkTextbox(root, font=("Consolas", 12), wrap="word")
    text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text.insert("1.0", f.read())
    except Exception as exc:
        text.insert("1.0", f"# Failed to read file\n\n{exc}")

    footer = ctk.CTkFrame(root, fg_color="transparent")
    footer.pack(fill="x", padx=8, pady=(0, 6))
    status = ctk.CTkLabel(footer, text="", font=("Roboto", 11, "bold"), text_color="gray")
    status.pack(side="left", fill="x", expand=True)

    def reload_file():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            text.delete("1.0", "end")
            text.insert("1.0", content)
            status.configure(text="Reloaded", text_color="#00C853")
        except Exception as exc:
            status.configure(text=f"Reload failed: {exc}", text_color="#D50000")

    def save_file():
        try:
            content = text.get("1.0", "end-1c")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            status.configure(text="Saved", text_color="#00C853")
            if "CKCS" in title:
                saved_name = os.path.basename(path)
                if hasattr(app, "_set_ckcs_raw_status"):
                    app._set_ckcs_raw_status(f"Đã lưu {saved_name}")
                if hasattr(app, "_set_ckcs_api_status"):
                    app._set_ckcs_api_status(f"Đã lưu {saved_name}")
            elif hasattr(app, "_set_advisor_status"):
                app._set_advisor_status(f"{title} saved")
            return content
        except Exception as exc:
            status.configure(text=f"Save failed: {exc}", text_color="#D50000")
            return None

    def save_and_send():
        content = save_file()
        if content is None or send_callback is None:
            return
        if send_callback(content, top):
            status.configure(text="Đã lưu và gửi", text_color="#00C853")

    def open_folder():
        try:
            os.startfile(os.path.dirname(path))
        except Exception as exc:
            status.configure(text=f"Open folder failed: {exc}", text_color="#D50000")

    ctk.CTkButton(
        footer,
        text="Open Folder",
        width=115,
        height=32,
        fg_color="#424242",
        hover_color="#616161",
        command=open_folder,
    ).pack(side="right", padx=(8, 0))
    ctk.CTkButton(
        footer,
        text="Reload",
        width=90,
        height=32,
        fg_color="#424242",
        hover_color="#616161",
        command=reload_file,
    ).pack(side="right", padx=(8, 0))
    ctk.CTkButton(
        footer,
        text="Save",
        width=100,
        height=32,
        fg_color="#00695C",
        hover_color="#004D40",
        command=save_file,
    ).pack(side="right", padx=(8, 0))
    if send_callback is not None:
        ctk.CTkButton(
            footer,
            text="LƯU & GỬI",
            width=125,
            height=32,
            fg_color="#1f538d",
            hover_color="#14375e",
            command=save_and_send,
        ).pack(side="right", padx=(8, 0))
    ctk.CTkButton(
        footer,
        text="Close",
        width=90,
        height=32,
        fg_color="#424242",
        hover_color="#616161",
        command=top.destroy,
    ).pack(side="right")


def open_advisor_context_editor(app):
    from ai_advisor.exporter import ensure_user_context

    open_advisor_file_editor(app, ensure_user_context(), "user_context.md")


FONT_BOLD = ("Roboto", 13, "bold")

COL_GREEN = "#00C853"

COL_RED = "#D50000"

COL_BLUE_ACCENT = "#1565C0"

COL_GRAY_BTN = "#424242"

COL_WARN = "#FFAB00"

COL_BOT_TAG = "#E040FB"


def _add_popup_hint(parent, text, padx=15, pady=(5, 10), wraplength=900):
    import customtkinter as ctk
    hint_f = ctk.CTkFrame(
        parent,
        fg_color="#332B00",
        corner_radius=6,
        border_width=1,
        border_color="#FFD600",
    )
    hint_f.pack(fill="x", padx=padx, pady=pady)
    ctk.CTkLabel(
        hint_f,
        text=text,
        font=("Arial", 12, "italic"),
        text_color="#FFD600",
        justify="left",
        anchor="w",
        wraplength=wraplength,
    ).pack(fill="x", padx=10, pady=6)
    return hint_f

# ==============================================================================

# 1. POPUP CẤU HÌNH TỪNG CẶP GIAO DỊCH (SYMBOL CONFIG)

# ==============================================================================


def open_symbol_config_popup(app, symbol, on_change=None):
    import json
    import core.storage_manager as storage_manager
    symbol = str(symbol or "").strip().upper()
    cfg_path = storage_manager.BRAIN_FILE
    existing_data = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except:
            pass
    symbol_configs = existing_data.get("symbol_configs", {})
    sym_cfg = symbol_configs.get(symbol, {})
    has_symbol_override = symbol in symbol_configs and bool(sym_cfg)
    top = ctk.CTkToplevel(app)
    top.title(f"Cấu hình riêng: {symbol}")
    _fit_popup(top, 720, 720, 620, 520)
    _bring_popup_to_front(top)
    body = _speed_up_scroll(ctk.CTkScrollableFrame(top, fg_color="transparent"))
    body.pack(fill="both", expand=True, padx=12, pady=(10, 4))
    top.grab_set()  # Khóa (Block) cửa sổ mẹ, bắt buộc người dùng thao tác trên popup này
    ctk.CTkLabel(
        body,
        text=f"THIẾT LẬP SAFEGUARD: {symbol}",
        font=FONT_BOLD,
        text_color="#2196F3",
    ).pack(pady=10)
    ctk.CTkLabel(
        body,
        text="ĐANG GHI ĐÈ SYMBOL"
        if has_symbol_override
        else "ĐANG DÙNG GLOBAL DEFAULT",
        font=("Roboto", 12, "bold"),
        text_color=COL_WARN if has_symbol_override else "#9E9E9E",
    ).pack(pady=(0, 6))
    _add_popup_hint(
        body,
        "- Cấu hình này chỉ áp dụng cho symbol đang chọn.\n"
        "- Max lệnh tối đa là tổng số ENTRY gốc của symbol.\n"
        "- Max lệnh cùng chiều chặn stack quá nhiều BUY hoặc SELL; 0 = tắt, chỉ dùng tổng.\n"
        "- Fixed Lot > 0 sẽ bỏ qua risk %, dùng lot cố định.\n"
        "- Watermark/Basket/Max Lot là hàng rào riêng trước khi bot vào hoặc giữ lệnh.",
        padx=20,
        pady=(0, 5),
        wraplength=620,
    )
    f_grid = ctk.CTkFrame(body, fg_color="transparent")
    f_grid.pack(fill="x", padx=20, pady=10)
    f_grid.grid_columnconfigure(0, weight=1)
    # Max Orders
    ctk.CTkLabel(f_grid, text="Max Lệnh Tối Đa:").grid(
        row=0, column=0, sticky="w", pady=10
    )
    e_max_orders = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_max_orders.insert(0, str(sym_cfg.get("max_orders", 1)))
    e_max_orders.grid(row=0, column=1, sticky="e", pady=10)
    ctk.CTkLabel(f_grid, text="Max Lệnh Cùng Chiều (0=Tắt):").grid(
        row=1, column=0, sticky="w", pady=10
    )
    e_max_same_direction = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_max_same_direction.insert(0, str(sym_cfg.get("max_same_direction_orders", 0)))
    e_max_same_direction.grid(row=1, column=1, sticky="e", pady=10)
    # Max Spread
    ctk.CTkLabel(f_grid, text="Max Spread (points):").grid(
        row=2, column=0, sticky="w", pady=10
    )
    e_max_spread = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_max_spread.insert(0, str(sym_cfg.get("max_spread", 150)))
    e_max_spread.grid(row=2, column=1, sticky="e", pady=10)
    # Max Ping
    ctk.CTkLabel(f_grid, text="Max Ping (ms):").grid(
        row=3, column=0, sticky="w", pady=10
    )
    e_max_ping = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_max_ping.insert(0, str(sym_cfg.get("max_ping", 150)))
    e_max_ping.grid(row=3, column=1, sticky="e", pady=10)

    # [NEW V4.4] Fixed Lot Mode
    ctk.CTkLabel(
        f_grid,
        text="Fixed Lot (0 = Tắt):",
        text_color="#FFB300",
        font=("Roboto", 12, "bold"),
    ).grid(row=4, column=0, sticky="w", pady=10)
    e_fixed_lot = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_fixed_lot.insert(0, str(sym_cfg.get("fixed_lot", 0.0)))
    e_fixed_lot.grid(row=4, column=1, sticky="e", pady=10)

    # [NEW V4.4] Max Lot Cap
    ctk.CTkLabel(
        f_grid,
        text="Max Lot Cap (0=Tắt):",
        text_color="#FFB300",
        font=("Roboto", 12, "bold"),
    ).grid(row=5, column=0, sticky="w", pady=10)
    e_max_lot_cap = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_max_lot_cap.insert(0, str(sym_cfg.get("max_lot_cap", 0.0)))
    e_max_lot_cap.grid(row=5, column=1, sticky="e", pady=10)

    # [NEW V5] Watermark & Options
    ctk.CTkLabel(f_grid, text="Watermark Trigger:", text_color="#00C853").grid(
        row=6, column=0, sticky="w", pady=10
    )
    e_wm_trigger = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_wm_trigger.insert(0, money_input_to_display(sym_cfg.get("watermark_trigger", 0.0), sym_cfg.get("watermark_trigger_unit", "USD")))
    e_wm_trigger.grid(row=6, column=1, sticky="e", pady=10)
    cbo_wm_trigger_unit = ctk.CTkOptionMenu(f_grid, values=["VND", "%Equity"], width=90)
    cbo_wm_trigger_unit.set(unit_to_display(sym_cfg.get("watermark_trigger_unit", "USD")))
    cbo_wm_trigger_unit.grid(row=6, column=2, sticky="w", padx=(8, 0), pady=10)
    ctk.CTkLabel(f_grid, text="Watermark Sụt giảm:", text_color="#00C853").grid(
        row=7, column=0, sticky="w", pady=10
    )
    e_wm_drawdown = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_wm_drawdown.insert(0, money_input_to_display(sym_cfg.get("watermark_drawdown", 0.0), sym_cfg.get("watermark_drawdown_unit", "USD")))
    e_wm_drawdown.grid(row=7, column=1, sticky="e", pady=10)
    cbo_wm_drawdown_unit = ctk.CTkOptionMenu(
        f_grid, values=["VND", "%Equity"], width=90
    )
    cbo_wm_drawdown_unit.set(unit_to_display(sym_cfg.get("watermark_drawdown_unit", "USD")))
    cbo_wm_drawdown_unit.grid(row=7, column=2, sticky="w", padx=(8, 0), pady=10)
    ctk.CTkLabel(f_grid, text="SL Tối thiểu (Points):").grid(
        row=8, column=0, sticky="w", pady=10
    )
    e_min_sl = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_min_sl.insert(0, str(sym_cfg.get("min_sl_points", 0)))
    e_min_sl.grid(row=8, column=1, sticky="e", pady=10)
    ctk.CTkLabel(f_grid, text="Max Basket Drawdown (DCA/PCA):").grid(
        row=9, column=0, sticky="w", pady=10
    )
    e_basket_dd = ctk.CTkEntry(f_grid, width=100, justify="center")
    e_basket_dd.insert(0, money_input_to_display(sym_cfg.get("max_basket_drawdown", 0.0), sym_cfg.get("max_basket_drawdown_unit", "USD")))
    e_basket_dd.grid(row=9, column=1, sticky="e", pady=10)
    cbo_basket_dd_unit = ctk.CTkOptionMenu(f_grid, values=["VND", "%Equity"], width=90)
    cbo_basket_dd_unit.set(unit_to_display(sym_cfg.get("max_basket_drawdown_unit", "USD")))
    cbo_basket_dd_unit.grid(row=9, column=2, sticky="w", padx=(8, 0), pady=10)
    var_reject_lot = ctk.BooleanVar(value=sym_cfg.get("reject_on_max_lot", False))
    ctk.CTkCheckBox(
        f_grid,
        text="Hủy lệnh nếu vượt Max Lot (Tắt = Ép bằng Max Lot)",
        variable=var_reject_lot,
        font=("Roboto", 11),
    ).grid(row=10, column=0, columnspan=2, sticky="w", pady=10)
    ctk.CTkLabel(
        f_grid,
        text=money_setting_hint(),
        text_color="#FFD54F",
        font=("Arial", 11, "italic"),
        wraplength=580,
        justify="left",
    ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(2, 8))

    def save_sym():
        try:
            mo = int(e_max_orders.get())
            msd = int(e_max_same_direction.get())
            ms = int(e_max_spread.get())
            mp = int(e_max_ping.get())
            if "symbol_configs" not in existing_data:
                existing_data["symbol_configs"] = {}
            existing_data["symbol_configs"][symbol] = {
                "max_orders": mo,
                "max_same_direction_orders": max(0, msd),
                "max_spread": ms,
                "max_ping": mp,
                "fixed_lot": float(e_fixed_lot.get()),
                "max_lot_cap": float(e_max_lot_cap.get()),
                "watermark_trigger": money_input_from_display(e_wm_trigger.get(), cbo_wm_trigger_unit.get()),
                "watermark_trigger_unit": unit_from_display(cbo_wm_trigger_unit.get()),
                "watermark_drawdown": money_input_from_display(e_wm_drawdown.get(), cbo_wm_drawdown_unit.get()),
                "watermark_drawdown_unit": unit_from_display(cbo_wm_drawdown_unit.get()),
                "min_sl_points": int(e_min_sl.get()),
                "max_basket_drawdown": money_input_from_display(e_basket_dd.get(), cbo_basket_dd_unit.get()),
                "max_basket_drawdown_unit": unit_from_display(cbo_basket_dd_unit.get()),
                "reject_on_max_lot": var_reject_lot.get(),
            }
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=4)
            from core.storage_manager import invalidate_settings_cache
            invalidate_settings_cache()
            app.log_message(f"✅ Đã lưu cấu hình riêng cho {symbol}.", target="bot")
            if callable(on_change):
                on_change()
            top.destroy()
        except ValueError:
            messagebox.showerror(
                "Lỗi", "Dữ liệu nhập sai, vui lòng nhập số nguyên!", parent=top
            )

    def reset_sym():
        if (
            "symbol_configs" in existing_data
            and symbol in existing_data["symbol_configs"]
        ):
            existing_data["symbol_configs"].pop(symbol, None)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=4, ensure_ascii=False)
            from core.storage_manager import invalidate_settings_cache
            invalidate_settings_cache()
            app.log_message(
                f"↩ Đã reset cấu hình riêng cho {symbol}; dùng Global default.",
                target="bot",
            )
            if callable(on_change):
                on_change()
        top.destroy()
    f_actions = ctk.CTkFrame(top, fg_color="transparent")
    f_actions.pack(pady=15, fill="x", padx=30)
    ctk.CTkButton(
        f_actions,
        text="LƯU CẤU HÌNH",
        fg_color=COL_GREEN,
        font=FONT_BOLD,
        height=40,
        command=save_sym,
    ).pack(side="left", expand=True, fill="x", padx=(0, 8))
    ctk.CTkButton(
        f_actions,
        text="RESET VỀ GLOBAL",
        fg_color="#5D4037" if has_symbol_override else "#424242",
        hover_color="#795548",
        font=FONT_BOLD,
        height=40,
        state="normal" if has_symbol_override else "disabled",
        command=reset_sym,
    ).pack(side="left", expand=True, fill="x", padx=(8, 0))

# ==============================================================================

# 2. POPUP CẤU HÌNH LÕI (CHỈ CÒN SAFETY & WATCHLIST)

# ==============================================================================


def open_bot_setting_popup(app):

    top = ctk.CTkToplevel(app)
    top.title("Cấu hình Lõi Hệ Thống (Core Settings)")
    _fit_popup(top, 1050, 720, 860, 560)
    _bring_popup_to_front(top)
    # top.transient(app) # Khóa Z-index, luôn nổi trên App chính
    safeguard_tabs = ctk.CTkTabview(top)
    safeguard_tabs.pack(fill="both", expand=True, padx=15, pady=(10, 0))
    tab_core_root = safeguard_tabs.add("GIỚI HẠN BOT")
    tab_volatility_root = safeguard_tabs.add("THEO DÕI BIẾN ĐỘNG")
    tab_core = _speed_up_scroll(
        ctk.CTkScrollableFrame(tab_core_root, fg_color="transparent")
    )
    tab_core.pack(fill="both", expand=True, padx=5, pady=5)
    tab_volatility = _speed_up_scroll(
        ctk.CTkScrollableFrame(tab_volatility_root, fg_color="transparent")
    )
    tab_volatility.pack(fill="both", expand=True, padx=5, pady=5)
    # Switch Auto Trade
    f_auto = ctk.CTkFrame(tab_core, fg_color="transparent")
    f_auto.pack(fill="x", pady=10)
    ctk.CTkLabel(
        f_auto, text="Tự động bóp cò khi Brain có tín hiệu (bật riêng từng nhóm):", text_color="gray"
    ).pack()
    # [2-BOT] Hai công tắc riêng: Phái sinh (CKPS) | Cơ sở (CKCS).
    f_grp = ctk.CTkFrame(f_auto, fg_color="transparent")
    f_grp.pack(pady=5)
    ctk.CTkSwitch(
        f_grp,
        text="BOT PHÁI SINH (VN30F)",
        variable=app.var_bot_ckps,
        font=("Roboto", 14, "bold"),
        progress_color=COL_GREEN,
        fg_color=COL_RED,
        command=lambda: app.on_bot_group_toggle("CKPS"),
    ).pack(side="left", padx=12)
    ctk.CTkSwitch(
        f_grp,
        text="BOT CƠ SỞ (CKCS)",
        variable=app.var_bot_ckcs,
        font=("Roboto", 14, "bold"),
        progress_color=COL_GREEN,
        fg_color=COL_RED,
        command=lambda: app.on_bot_group_toggle("CKCS"),
    ).pack(side="left", padx=12)
    # Công tắc TỔNG (legacy) — bật/tắt cả 2 nhóm cùng lúc.
    sw_auto = ctk.CTkSwitch(
        f_auto,
        text="TẤT CẢ (AUTO-TRADING DAEMON)",
        variable=app.var_auto_trade,
        font=("Roboto", 12, "bold"),
        progress_color=COL_GREEN,
        fg_color=COL_RED,
        command=app.on_auto_trade_toggle,
    )
    sw_auto.pack(pady=(8, 5))
    ctk.CTkFrame(tab_core, height=2, fg_color="#333").pack(fill="x", padx=30, pady=5)
    # Watchlist (Đã chuyển lên đầu)
    ctk.CTkLabel(
        tab_core,
        text="WATCHLIST - BOT CHỈ QUÉT CÁC COIN SAU:",
        font=FONT_BOLD,
        text_color="#2196F3",
    ).pack(pady=(5, 5))
    _add_popup_hint(
        tab_core,
        "- Watchlist quyết định symbol bot được quét; nút bánh răng là safeguard riêng từng symbol.\n"
        "- AUTO-TRADING bật/tắt bóp cò thật, nhưng preview/context vẫn có thể chạy để quan sát.\n"
        "- Cấu hình Global là mặc định; cấu hình riêng theo symbol sẽ ghi đè.",
        padx=30,
        pady=(0, 10),
    )
    f_coins = ctk.CTkFrame(tab_core, fg_color="transparent")
    f_coins.pack(fill="x", padx=30, pady=(0, 10))
    app.bot_coin_vars = {}
    allowed_list = [str(s or "").strip().upper() for s in getattr(config, "BOT_ACTIVE_SYMBOLS", []) or []]
    symbol_cfg_buttons = {}

    def _symbol_has_override(symbol_name):
        try:
            import json
            import core.storage_manager as storage_manager
            cfg_path = storage_manager.BRAIN_FILE
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return bool(data.get("symbol_configs", {}).get(symbol_name))
        except Exception:
            pass
        return False

    def refresh_symbol_cfg_buttons():
        for symbol_name, btn in symbol_cfg_buttons.items():
            has_override = _symbol_has_override(symbol_name)
            btn.configure(
                text="⚙*" if has_override else "⚙",
                fg_color=COL_WARN if has_override else "#444",
                hover_color="#FFB300" if has_override else "#666",
                text_color="#212121" if has_override else "#FFFFFF",
            )
    # Danh sách mã bot có thể quét: CKPS (VN30F) + CKCS (cổ phiếu cơ sở nhập tay).
    # Bot chỉ trade mã được tick; chưa tick thì không đụng tới.
    watch_symbols = _build_watch_symbols()

    # Tách 2 nhóm cho dễ phân biệt: Phái sinh (CKPS) | Cơ sở (CKCS).
    from core import settlement as _settlement
    ckps_syms = [s for s in watch_symbols if not _settlement.is_cash_stock(s)]
    ckcs_syms = [s for s in watch_symbols if _settlement.is_cash_stock(s)]

    def _make_coin_cell(parent, coin, row, col):
        var = tk.BooleanVar(value=(coin in allowed_list))
        app.bot_coin_vars[coin] = var
        f_single_coin = ctk.CTkFrame(parent, fg_color="transparent")
        f_single_coin.grid(row=row, column=col, sticky="w", pady=4, padx=8)
        ctk.CTkCheckBox(
            f_single_coin, text=coin, variable=var, font=("Consolas", 13), width=80
        ).pack(side="left")
        has_override = _symbol_has_override(coin)
        btn_cfg = ctk.CTkButton(
            f_single_coin,
            text="⚙*" if has_override else "⚙",
            width=25, height=20,
            fg_color=COL_WARN if has_override else "#444",
            hover_color="#FFB300" if has_override else "#666",
            text_color="#212121" if has_override else "#FFFFFF",
            command=lambda c=coin: open_symbol_config_popup(app, c, on_change=refresh_symbol_cfg_buttons),
        )
        btn_cfg.pack(side="left", padx=(5, 0))
        symbol_cfg_buttons[coin] = btn_cfg

    f_two = ctk.CTkFrame(f_coins, fg_color="transparent")
    f_two.pack(fill="x")

    # Cột trái: PHÁI SINH (CKPS = VN30F)
    f_ckps = ctk.CTkFrame(f_two, fg_color="#16212e", corner_radius=8)
    f_ckps.grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=2)
    ctk.CTkLabel(f_ckps, text="PHÁI SINH (CKPS)", font=("Roboto", 11, "bold"), text_color="#4FC3F7").grid(
        row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))
    for i, coin in enumerate(ckps_syms):
        _make_coin_cell(f_ckps, coin, 1 + i, 0)
    if not ckps_syms:
        ctk.CTkLabel(f_ckps, text="(VN30F1M)", text_color="#607D8B").grid(row=1, column=0, padx=8, pady=4)

    # Cột phải: CƠ SỞ (CKCS = cổ phiếu)
    f_ckcs = ctk.CTkFrame(f_two, fg_color="#2a2412", corner_radius=8)
    f_ckcs.grid(row=0, column=1, sticky="nw", pady=2)
    ctk.CTkLabel(f_ckcs, text="CƠ SỞ (CKCS)", font=("Roboto", 11, "bold"), text_color="#FFD54F").grid(
        row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))
    if ckcs_syms:
        for i, coin in enumerate(ckcs_syms):
            _make_coin_cell(f_ckcs, coin, 1 + (i // 2), i % 2)
    else:
        ctk.CTkLabel(f_ckcs, text="(nhập mã ở ⚙ ADVANCED → Cache & Mã)", text_color="#607D8B").grid(
            row=1, column=0, columnspan=2, padx=8, pady=4)

    def refresh_watch_symbol_controls():
        try:
            if top.winfo_exists():
                top.destroy()
        except Exception:
            pass
        open_bot_setting_popup(app)

    app._refresh_bot_settings_symbols = refresh_watch_symbol_controls

    def _clear_symbol_refresh_hook(event=None):
        if event is not None and getattr(event, "widget", None) is not top:
            return
        if getattr(app, "_refresh_bot_settings_symbols", None) is refresh_watch_symbol_controls:
            try:
                delattr(app, "_refresh_bot_settings_symbols")
            except Exception:
                pass

    top.bind("<Destroy>", _clear_symbol_refresh_hook, add="+")
    ctk.CTkFrame(tab_core, height=2, fg_color="#333").pack(fill="x", padx=30, pady=5)
    # Safety Guard (Bot ONLY - Độc lập hoàn toàn với Manual)
    ctk.CTkLabel(
        tab_core,
        text="HÀNG RÀO BẢO VỆ BOT (BOT SAFEGUARD)",
        font=FONT_BOLD,
        text_color="#FFB300",
    ).pack(pady=(5, 5))
    _add_popup_hint(
        tab_core,
        "- Global Brake chặn toàn bot khi chạm ngưỡng lỗ/streak/cooldown.\n"
        "- Safeguard bảo vệ lợi nhuận, rổ DCA/PCA, SL tối thiểu và điều kiện TP.\n"
        "- TP R/Swing ở đây là TP bot cũ; Entry/Exit Mode có thể override TP sau này theo tp_policy.\n"
        "- Giá trị 0 thường là tắt giới hạn tương ứng.",
        padx=30,
        pady=(0, 10),
    )

    # --- [NEW] LIVE PREVIEW ---
    from core.storage_manager import load_state, save_state
    import time
    st = load_state()
    start_bal = st.get("starting_balance", 0)
    pnl = st.get("bot_pnl_today", 0.0)
    loss_pct = (pnl / start_bal * 100) if start_bal > 0 else 0
    trades = st.get("bot_trades_today", 0)
    losses = st.get("bot_daily_loss_count", 0)
    cooldown_until = st.get("cooldown_until", 0.0)
    f_preview = ctk.CTkFrame(tab_core, fg_color="#1E1E1E", corner_radius=8)
    f_preview.pack(fill="x", padx=15, pady=(0, 10))
    cooldown_str = "Sẵn sàng"
    now = time.time()
    if now < cooldown_until:
        rem = int((cooldown_until - now) / 60)
        cooldown_str = f"BỊ CHẶN ({rem} phút)"
    pnl_color = COL_GREEN if loss_pct >= 0 else COL_RED
    preview_text = f"PNL Today: {loss_pct:+.2f}% | Lệnh: {trades} | Thua: {losses} | Cooldown: {cooldown_str}"
    ctk.CTkLabel(
        f_preview,
        text="LIVE PREVIEW:",
        font=("Roboto", 12, "bold"),
        text_color="#00E676",
    ).pack(side="left", padx=10, pady=8)
    lbl_preview = ctk.CTkLabel(
        f_preview,
        text=preview_text,
        font=("Consolas", 12, "bold"),
        text_color=pnl_color,
    )
    lbl_preview.pack(side="left", padx=10, pady=8)
    f_iso = ctk.CTkFrame(tab_core, fg_color="#171717", corner_radius=8)
    f_iso.pack(fill="x", padx=15, pady=(0, 10))

    def render_isolation_preview():
        for child in f_iso.winfo_children():
            child.destroy()
        latest_state = load_state()
        now_ts = time.time()
        active_iso = []
        for sym, deadline in latest_state.get("bot_last_fail_times", {}).items():
            try:
                deadline = float(deadline)
            except (TypeError, ValueError):
                continue
            if deadline > now_ts:
                rem = int(deadline - now_ts)
                active_iso.append((sym, rem))
        ctk.CTkLabel(
            f_iso,
            text="Isolation:",
            font=("Roboto", 11, "bold"),
            text_color="#FFB300" if active_iso else "#757575",
        ).pack(side="left", padx=(10, 6), pady=6)
        if not active_iso:
            ctk.CTkLabel(
                f_iso,
                text="Không có",
                font=("Consolas", 11),
                text_color="#757575",
            ).pack(side="left", padx=4, pady=6)
            return

        def reset_symbol(sym):
            latest = load_state()
            latest.get("bot_last_fail_times", {}).pop(sym, None)
            latest.get("bot_symbol_losing_streak", {}).pop(sym, None)
            save_state(latest)
            if hasattr(app, "trade_mgr"):
                app.trade_mgr.state = latest
            if hasattr(app, "log_message"):
                app.log_message(f"✅ Đã reset isolation cho {sym}.", target="bot")
            render_isolation_preview()
        for sym, rem in active_iso:
            time_str = (
                f"{rem // 3600}h{(rem % 3600) // 60}m"
                if rem >= 3600
                else f"{rem // 60}m"
            )
            ctk.CTkLabel(
                f_iso,
                text=f"{sym} {time_str}",
                font=("Consolas", 11, "bold"),
                text_color="#FF5252",
            ).pack(side="left", padx=(8, 3), pady=6)
            ctk.CTkButton(
                f_iso,
                text="Reset",
                width=54,
                height=24,
                fg_color="#424242",
                hover_color="#616161",
                command=lambda s=sym: reset_symbol(s),
            ).pack(side="left", padx=(0, 6), pady=6)
    render_isolation_preview()
    f_safety = ctk.CTkFrame(tab_core, fg_color="#2b2b2b", corner_radius=8)
    f_safety.pack(fill="x", padx=15, pady=5)
    f_safety.columnconfigure((0, 2), weight=1)

    # [FIX] Đọc safeguard từ brain_settings.json TRƯỚC, fallback về config.py
    safe_cfg = {}
    try:
        import json as _json
        import core.storage_manager as storage_manager
        _cfg_path = storage_manager.BRAIN_FILE
        if os.path.exists(_cfg_path):
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                safe_cfg = _json.load(_f).get("bot_safeguard", {})
    except Exception:
        pass

    action_labels = {
        "CHỈ CẢNH BÁO": "ALERT_ONLY",
        "CHẶN BOT TĂNG VỊ THẾ": "BLOCK_NEW_EXPOSURE",
        "KHẨN CẤP — ĐÓNG HẾT": "CLOSE_ALL",
    }
    action_values = {value: label for label, value in action_labels.items()}
    var_volatility_brake = ctk.BooleanVar(
        value=bool(safe_cfg.get("VOLATILITY_BRAKE_ENABLED", False))
    )
    var_volatility_telegram = ctk.BooleanVar(
        value=bool(safe_cfg.get("VOLATILITY_BRAKE_TELEGRAM_ENABLED", True))
    )
    var_volatility_session = ctk.BooleanVar(
        value=bool(safe_cfg.get("VOLATILITY_BRAKE_SESSION_ENABLED", True))
    )
    vol_panel = ctk.CTkFrame(
        tab_volatility, fg_color="#20252B", border_width=1, border_color="#FFB300"
    )
    vol_panel.pack(fill="x", padx=15, pady=12)
    ctk.CTkLabel(
        vol_panel,
        text="THEO DÕI TĂNG / GIẢM MẠNH",
        text_color="#FFD54F",
        font=("Roboto", 16, "bold"),
    ).pack(anchor="w", padx=18, pady=(15, 4))
    ctk.CTkLabel(
        vol_panel,
        text=(
            "Chỉ các mã bên dưới mới được kiểm tra. Dùng chung giá của daemon, "
            "không mở thêm WebSocket hay vòng gọi API riêng."
        ),
        text_color="#B0BEC5",
        wraplength=840,
        justify="left",
    ).pack(anchor="w", padx=18, pady=(0, 10))

    vol_grid = ctk.CTkFrame(vol_panel, fg_color="transparent")
    vol_grid.pack(fill="x", padx=18, pady=5)
    vol_grid.columnconfigure(1, weight=1)
    ctk.CTkCheckBox(
        vol_grid,
        text="Bật theo dõi biến động",
        variable=var_volatility_brake,
        text_color="#FFD54F",
        font=("Roboto", 13, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
    ctk.CTkLabel(vol_grid, text="Mã giám sát:").grid(
        row=1, column=0, sticky="w", padx=(0, 12), pady=6
    )
    e_volatility_symbols = ctk.CTkEntry(vol_grid)
    configured_symbols = safe_cfg.get("VOLATILITY_BRAKE_SYMBOLS", ["VN30F1M"])
    if isinstance(configured_symbols, str):
        configured_symbols = configured_symbols.replace(";", ",").split(",")
    e_volatility_symbols.insert(
        0,
        ", ".join(
            str(symbol or "").strip().upper()
            for symbol in (configured_symbols or ["VN30F1M"])
            if str(symbol or "").strip()
        ),
    )
    e_volatility_symbols.grid(row=1, column=1, sticky="ew", pady=6)
    ctk.CTkLabel(vol_grid, text="Hành động khi kích hoạt:").grid(
        row=2, column=0, sticky="w", padx=(0, 12), pady=6
    )
    cbo_volatility_action = ctk.CTkOptionMenu(
        vol_grid,
        values=list(action_labels),
        fg_color="#455A64",
        button_color="#37474F",
    )
    cbo_volatility_action.set(
        action_values.get(
            str(safe_cfg.get("VOLATILITY_BRAKE_ACTION", "ALERT_ONLY")).upper(),
            "CHỈ CẢNH BÁO",
        )
    )
    cbo_volatility_action.grid(row=2, column=1, sticky="ew", pady=6)

    fields = ctk.CTkFrame(vol_panel, fg_color="transparent")
    fields.pack(fill="x", padx=18, pady=5)
    for column in range(4):
        fields.columnconfigure(column, weight=1)

    def _vol_entry(label, value, row, column):
        box = ctk.CTkFrame(fields, fg_color="transparent")
        box.grid(row=row, column=column, sticky="ew", padx=6, pady=5)
        ctk.CTkLabel(box, text=label).pack(anchor="w")
        entry = ctk.CTkEntry(box, justify="center")
        entry.insert(0, str(value))
        entry.pack(fill="x", pady=(2, 0))
        return entry

    e_volatility_window = _vol_entry(
        "Cửa sổ (giây)",
        safe_cfg.get("VOLATILITY_BRAKE_WINDOW_SECONDS", 60.0),
        0,
        0,
    )
    e_volatility_derivative_points = _vol_entry(
        "CKPS (điểm)",
        safe_cfg.get("VOLATILITY_BRAKE_DERIVATIVE_POINTS", 5.0),
        0,
        1,
    )
    e_volatility_stock_pct = _vol_entry(
        "CKCS (%)",
        safe_cfg.get("VOLATILITY_BRAKE_STOCK_PCT", 1.5),
        0,
        2,
    )
    e_volatility_confirmations = _vol_entry(
        "Số lần xác nhận",
        safe_cfg.get("VOLATILITY_BRAKE_CONFIRMATIONS", 2),
        0,
        3,
    )
    e_volatility_symbol_cooldown = _vol_entry(
        "Cooldown từng mã (phút)",
        30.0
        if float(safe_cfg.get("VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES", 30.0) or 0.0) == 240.0
        else safe_cfg.get("VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES", 30.0),
        1,
        0,
    )
    ctk.CTkCheckBox(
        fields,
        text="Bắt thêm nhịp đi xa trong phiên",
        variable=var_volatility_session,
    ).grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(24, 5))
    e_volatility_session_derivative = _vol_entry(
        "CKPS cả nhịp (điểm)",
        safe_cfg.get("VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS", 20.0),
        2,
        0,
    )
    e_volatility_session_stock = _vol_entry(
        "CKCS cả nhịp (%)",
        safe_cfg.get("VOLATILITY_BRAKE_SESSION_STOCK_PCT", 3.0),
        2,
        1,
    )
    ctk.CTkCheckBox(
        fields,
        text="Gửi biến động giá vào nhóm Gợi ý BOT",
        variable=var_volatility_telegram,
    ).grid(row=2, column=2, columnspan=2, sticky="w", padx=6, pady=(24, 5))
    ctk.CTkLabel(
        vol_panel,
        text=(
            "Hai luật độc lập: cú sốc ngắn 5 điểm/60 giây và nhịp dài 20 điểm từ mốc gần nhất. "
            "Mặc định CHỈ CẢNH BÁO không sửa lệnh, không khóa BOT. "
            "ĐÓNG HẾT chỉ chạy khi Ngài chủ động chọn; khi đó cả vị thế tay cũng bị đóng."
        ),
        text_color="#FF8A80",
        font=("Arial", 11, "italic"),
        wraplength=840,
        justify="left",
    ).pack(anchor="w", padx=18, pady=(8, 16))

    # --- [GROUP 1: ⚠️ PHANH KHẨN CẤP GLOBAL (EMERGENCY)] ---
    f_global = ctk.CTkFrame(f_safety, border_width=1, border_color="#F44336")
    f_global.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=5, pady=8)
    ctk.CTkLabel(
        f_global,
        text="⚠️ PHANH KHẨN CẤP (GLOBAL BRAKE)",
        text_color="#F44336",
        font=("Roboto", 13, "bold"),
    ).pack(pady=5)
    f_gl_content = ctk.CTkFrame(f_global, fg_color="transparent")
    f_gl_content.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(f_gl_content, text="Bot Max Loss/Ngày (%):").grid(
        row=0, column=0, sticky="w", padx=10, pady=5
    )
    e_max_loss = ctk.CTkEntry(f_gl_content, width=70, justify="center")
    e_max_loss.insert(0, str(safe_cfg.get("MAX_DAILY_LOSS_PERCENT", 2.5)))
    e_max_loss.grid(row=0, column=1, sticky="w", padx=10, pady=5)
    ctk.CTkLabel(f_gl_content, text="Bot Max Thua (Streak):").grid(
        row=0, column=2, sticky="w", padx=10, pady=5
    )
    e_max_streak = ctk.CTkEntry(f_gl_content, width=70, justify="center")
    e_max_streak.insert(0, str(safe_cfg.get("MAX_LOSING_STREAK", 3)))
    e_max_streak.grid(row=0, column=3, sticky="w", padx=10, pady=5)
    ctk.CTkLabel(
        f_gl_content, text="Global Cooldown (Giờ):", font=("Roboto", 12, "bold")
    ).grid(row=1, column=0, sticky="w", padx=10, pady=5)
    e_global_cooldown = ctk.CTkEntry(
        f_gl_content, width=70, justify="center", fg_color="#311B92"
    )
    e_global_cooldown.insert(0, str(safe_cfg.get("GLOBAL_COOLDOWN_HOURS", 4.0)))
    e_global_cooldown.grid(row=1, column=1, sticky="w", padx=10, pady=5)
    var_gl_on_sg = ctk.BooleanVar(
        value=safe_cfg.get("APPLY_GLOBAL_COOLDOWN_ON_SAFEGUARD", False)
    )
    chk_gl_on_sg = ctk.CTkCheckBox(
        f_gl_content,
        text="Dính Basket/Watermark -> Chặn Global luôn",
        variable=var_gl_on_sg,
        text_color="#FF5252",
        font=("Arial", 11, "italic"),
    )
    chk_gl_on_sg.grid(row=1, column=2, columnspan=2, sticky="w", padx=10, pady=5)
    ctk.CTkLabel(
        f_gl_content,
        text="Theo dõi tăng/giảm mạnh đã chuyển sang tab THEO DÕI BIẾN ĐỘNG.",
        text_color="#90CAF9",
        font=("Arial", 10, "italic"),
        wraplength=520,
        justify="left",
    ).grid(row=2, column=0, columnspan=4, sticky="w", padx=10, pady=5)

    # --- [GROUP 2: 📉 SAFEGUARD & PROFIT (PROTECTION)] ---
    f_sg = ctk.CTkFrame(f_safety, border_width=1, border_color="#00C853")
    f_sg.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=5, pady=8)
    ctk.CTkLabel(
        f_sg,
        text="📉 BẢO VỆ LỢI NHUẬN & RỔ LỆNH (SAFEGUARD)",
        text_color="#00C853",
        font=("Roboto", 13, "bold"),
    ).pack(pady=5)
    f_sg_content = ctk.CTkFrame(f_sg, fg_color="transparent")
    f_sg_content.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(f_sg_content, text="Watermark Global:").grid(
        row=0, column=0, sticky="w", padx=10, pady=5
    )
    e_gl_wm_trigger = ctk.CTkEntry(f_sg_content, width=60, justify="center")
    e_gl_wm_trigger.insert(0, money_input_to_display(safe_cfg.get("WATERMARK_TRIGGER", 0.0), safe_cfg.get("WATERMARK_TRIGGER_UNIT", "USD")))
    e_gl_wm_trigger.grid(row=0, column=1, sticky="w", padx=5, pady=5)
    cbo_gl_wm_trigger_unit = ctk.CTkOptionMenu(
        f_sg_content, values=["VND", "%Equity"], width=90
    )
    cbo_gl_wm_trigger_unit.set(unit_to_display(safe_cfg.get("WATERMARK_TRIGGER_UNIT", "USD")))
    cbo_gl_wm_trigger_unit.grid(row=0, column=2, sticky="w", padx=(0, 10), pady=5)
    ctk.CTkLabel(f_sg_content, text="Drawdown:").grid(
        row=0, column=3, sticky="w", padx=10, pady=5
    )
    e_gl_wm_drawdown = ctk.CTkEntry(f_sg_content, width=60, justify="center")
    e_gl_wm_drawdown.insert(0, money_input_to_display(safe_cfg.get("WATERMARK_DRAWDOWN", 0.0), safe_cfg.get("WATERMARK_DRAWDOWN_UNIT", "USD")))
    e_gl_wm_drawdown.grid(row=0, column=4, sticky="w", padx=5, pady=5)
    cbo_gl_wm_drawdown_unit = ctk.CTkOptionMenu(
        f_sg_content, values=["VND", "%Equity"], width=90
    )
    cbo_gl_wm_drawdown_unit.set(unit_to_display(safe_cfg.get("WATERMARK_DRAWDOWN_UNIT", "USD")))
    cbo_gl_wm_drawdown_unit.grid(row=0, column=5, sticky="w", padx=(0, 10), pady=5)
    ctk.CTkLabel(f_sg_content, text="Max Basket Loss (DCA/PCA):").grid(
        row=1, column=0, sticky="w", padx=10, pady=5
    )
    e_gl_basket_dd = ctk.CTkEntry(f_sg_content, width=60, justify="center")
    e_gl_basket_dd.insert(0, money_input_to_display(safe_cfg.get("MAX_BASKET_DRAWDOWN_USD", 0.0), safe_cfg.get("MAX_BASKET_DRAWDOWN_UNIT", "USD")))
    e_gl_basket_dd.grid(row=1, column=1, sticky="w", padx=5, pady=5)
    cbo_gl_basket_dd_unit = ctk.CTkOptionMenu(
        f_sg_content, values=["VND", "%Equity"], width=90
    )
    cbo_gl_basket_dd_unit.set(unit_to_display(safe_cfg.get("MAX_BASKET_DRAWDOWN_UNIT", "USD")))
    cbo_gl_basket_dd_unit.grid(row=1, column=2, sticky="w", padx=(0, 10), pady=5)
    ctk.CTkLabel(f_sg_content, text="SL Tối thiểu (pts):").grid(
        row=1, column=3, sticky="w", padx=10, pady=5
    )
    e_gl_min_sl = ctk.CTkEntry(f_sg_content, width=60, justify="center")
    e_gl_min_sl.insert(0, str(safe_cfg.get("MIN_SL_POINTS", 0)))
    e_gl_min_sl.grid(row=1, column=4, sticky="w", padx=5, pady=5)
    # [CKCS] Cap giá trị 1 lệnh cổ phiếu cơ sở theo % NAV (0 = tắt). Chống SL hẹp -> lot khổng lồ.
    ctk.CTkLabel(f_sg_content, text="Cap %NAV/mã CKCS:", text_color="#FFB300").grid(
        row=1, column=5, sticky="w", padx=(10, 2), pady=5
    )
    e_nav_cap = ctk.CTkEntry(f_sg_content, width=55, justify="center")
    e_nav_cap.insert(0, str(safe_cfg.get("STOCK_MAX_ORDER_NAV_PCT", getattr(config, "STOCK_MAX_ORDER_NAV_PCT", 20.0))))
    e_nav_cap.grid(row=1, column=6, sticky="w", padx=2, pady=5)
    # Dòng TP & Safeguard bổ sung
    var_bot_use_swing_tp = ctk.BooleanVar(value=safe_cfg.get("BOT_USE_SWING_TP", False))
    var_bot_use_rr_tp = ctk.BooleanVar(value=safe_cfg.get("BOT_USE_RR_TP", True))

    class _HiddenValue:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value
    e_bot_tp_rr = _HiddenValue(str(safe_cfg.get("BOT_TP_RR_RATIO", 1.5)))
    var_strict_min_lot = ctk.BooleanVar(value=safe_cfg.get("STRICT_MIN_LOT", False))
    ctk.CTkCheckBox(
        f_sg_content,
        text="Strict Min Lot",
        variable=var_strict_min_lot,
        text_color="#F44336",
        font=("Roboto", 11, "bold"),
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=2)
    var_gl_reject_lot = ctk.BooleanVar(value=safe_cfg.get("REJECT_ON_MAX_LOT", False))
    ctk.CTkCheckBox(
        f_sg_content,
        text="Hủy lệnh vượt Max Lot",
        variable=var_gl_reject_lot,
        font=("Roboto", 11),
    ).grid(row=2, column=2, columnspan=2, sticky="w", padx=10, pady=2)
    # [CKCS] Ép lên 1 lô chẵn khi lô tính theo rủi ro < tối thiểu (thay vì bỏ lệnh).
    var_force_min_lot = ctk.BooleanVar(value=safe_cfg.get("FORCE_MIN_LOT", False))
    ctk.CTkCheckBox(
        f_sg_content,
        text="Ép lô tối thiểu CKCS (100 CP)",
        variable=var_force_min_lot,
        text_color="#FFB300",
        font=("Roboto", 11, "bold"),
    ).grid(row=2, column=4, columnspan=2, sticky="w", padx=10, pady=2)

    # --- [NEW V5.2] GLOBAL BRAKE MODE ---
    ctk.CTkLabel(f_sg_content, text="Global Brake Mode:").grid(
        row=3, column=0, sticky="w", padx=10, pady=(10, 0)
    )
    current_brake_mode = safe_cfg.get("GLOBAL_BRAKE_MODE", "Mode 1: Total Freeze")
    cbo_brake_mode = ctk.CTkOptionMenu(
        f_sg_content,
        values=["Mode 1: Total Freeze", "Mode 2: Symbol Isolation"],
        width=200,
    )
    cbo_brake_mode.set(current_brake_mode)
    cbo_brake_mode.grid(
        row=3, column=1, columnspan=3, sticky="w", padx=10, pady=(10, 0)
    )

    # --- [NEW] Kiểu lệnh phiên định kỳ ATO/ATC ---
    ctk.CTkLabel(f_sg_content, text="Kiểu khớp bot:").grid(row=4, column=0, sticky="w", padx=10, pady=(10, 0))
    cbo_bot_order_mode = ctk.CTkOptionMenu(f_sg_content, values=["NORMAL", "AUTO"], width=140)
    cbo_bot_order_mode.set(str(safe_cfg.get("BOT_ORDER_MODE", "NORMAL")).upper())
    cbo_bot_order_mode.grid(row=4, column=1, sticky="w", padx=10, pady=(10, 0))
    var_bot_atc_exit = ctk.BooleanVar(value=bool(safe_cfg.get("BOT_ATC_EXIT", False)))
    ctk.CTkCheckBox(
        f_sg_content, text="Đóng vị thế phiên ATC (cuối ngày)", variable=var_bot_atc_exit, font=("Roboto", 11),
    ).grid(row=4, column=2, columnspan=2, sticky="w", padx=10, pady=(10, 0))
    # [BOT LO] Kiểu lệnh vào: MARKET (khớp ngay) | LO (đặt limit tại giá hiện tại, không đuổi giá).
    ctk.CTkLabel(f_sg_content, text="Kiểu lệnh vào:").grid(row=4, column=4, sticky="w", padx=10, pady=(10, 0))
    cbo_bot_entry_order = ctk.CTkOptionMenu(f_sg_content, values=["MARKET", "LO"], width=100)
    cbo_bot_entry_order.set(str(safe_cfg.get("BOT_ENTRY_ORDER_TYPE", "MARKET")).upper())
    cbo_bot_entry_order.grid(row=4, column=5, sticky="w", padx=10, pady=(10, 0))
    ctk.CTkLabel(
        f_sg_content,
        text="AUTO: trong phiên ATO/ATC bot đặt lệnh ATO/ATC, ngoài phiên thì khớp liên tục (LO/MOK).",
        font=("Arial", 10, "italic"), text_color="#90A4AE", justify="left", wraplength=560,
    ).grid(row=5, column=0, columnspan=4, sticky="w", padx=10, pady=(2, 4))
    # [RISK GATE] Trần %NAV mất-nếu-dính-SL cho 1 lệnh (0 = tắt). Bot/telegram chặn cứng,
    # manual hỏi xác nhận. Dùng chung cho cả bot lẫn manual — chỉ 2 con số này, không option khác.
    ctk.CTkLabel(
        f_sg_content, text="RISK GATE %NAV/lệnh (0=tắt) — PS:", text_color="#FFB300",
        font=("Roboto", 11, "bold"),
    ).grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 5))
    e_risk_gate_ps = ctk.CTkEntry(f_sg_content, width=55, justify="center")
    e_risk_gate_ps.insert(0, str(safe_cfg.get("RISK_GATE_MAX_PCT_PS", getattr(config, "BOT_SAFEGUARD", {}).get("RISK_GATE_MAX_PCT_PS", 10.0))))
    e_risk_gate_ps.grid(row=6, column=2, sticky="w", padx=2, pady=(6, 5))
    ctk.CTkLabel(f_sg_content, text="CS:", text_color="#FFB300", font=("Roboto", 11, "bold")).grid(
        row=6, column=3, sticky="e", padx=(10, 2), pady=(6, 5)
    )
    e_risk_gate_cs = ctk.CTkEntry(f_sg_content, width=55, justify="center")
    e_risk_gate_cs.insert(0, str(safe_cfg.get("RISK_GATE_MAX_PCT_CS", getattr(config, "BOT_SAFEGUARD", {}).get("RISK_GATE_MAX_PCT_CS", 3.0))))
    e_risk_gate_cs.grid(row=6, column=4, sticky="w", padx=2, pady=(6, 5))
    ctk.CTkLabel(
        f_sg_content,
        text=money_setting_hint(),
        text_color="#FFD54F",
        font=("Arial", 11, "italic"),
        wraplength=760,
        justify="left",
    ).grid(row=7, column=0, columnspan=7, sticky="w", padx=10, pady=(4, 6))

    # --- [GROUP 3: 🛡️ ĐIỀU KIỆN VẬN HÀNH (OPERATIONAL)] ---
    f_op = ctk.CTkFrame(f_safety, border_width=1, border_color="#2196F3")
    f_op.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=5, pady=8)
    ctk.CTkLabel(
        f_op,
        text="🛡️ ĐIỀU KIỆN VẬN HÀNH (OPERATIONAL)",
        text_color="#2196F3",
        font=("Roboto", 13, "bold"),
    ).pack(pady=5)
    f_op_content = ctk.CTkFrame(f_op, fg_color="transparent")
    f_op_content.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(f_op_content, text="Max Lệnh Mở:").grid(
        row=0, column=0, sticky="w", padx=10, pady=5
    )
    e_max_open = ctk.CTkEntry(f_op_content, width=60, justify="center")
    e_max_open.insert(0, str(safe_cfg.get("MAX_OPEN_POSITIONS", 3)))
    e_max_open.grid(row=0, column=1, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_op_content, text="Bot Cooldown (M):").grid(
        row=0, column=2, sticky="w", padx=10, pady=5
    )
    e_cooldown = ctk.CTkEntry(f_op_content, width=60, justify="center")
    e_cooldown.insert(0, str(safe_cfg.get("COOLDOWN_MINUTES", 1)))
    e_cooldown.grid(row=0, column=3, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_op_content, text="Tổng Lệnh/Ngày:").grid(
        row=1, column=0, sticky="w", padx=10, pady=5
    )
    e_max_trades = ctk.CTkEntry(f_op_content, width=60, justify="center")
    e_max_trades.insert(0, str(safe_cfg.get("MAX_TRADES_PER_DAY", 30)))
    e_max_trades.grid(row=1, column=1, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_op_content, text="Chế độ tính Loss:").grid(
        row=1, column=2, sticky="w", padx=10, pady=5
    )
    cbo_loss_mode = ctk.CTkOptionMenu(
        f_op_content, values=["TOTAL", "STREAK"], width=80, height=24
    )
    cbo_loss_mode.set(safe_cfg.get("LOSS_COUNT_MODE", "TOTAL"))
    cbo_loss_mode.grid(row=1, column=3, sticky="w", padx=5, pady=5)
    var_check_ping = ctk.BooleanVar(value=safe_cfg.get("CHECK_PING", True))
    ctk.CTkCheckBox(
        f_op_content, text="Ping (ms):", variable=var_check_ping, font=("Roboto", 11)
    ).grid(row=2, column=0, sticky="w", padx=10, pady=5)
    e_max_ping = ctk.CTkEntry(f_op_content, width=60, justify="center")
    e_max_ping.insert(0, str(safe_cfg.get("MAX_PING_MS", 150)))
    e_max_ping.grid(row=2, column=1, sticky="w", padx=5, pady=5)
    var_check_spread = ctk.BooleanVar(value=safe_cfg.get("CHECK_SPREAD", True))
    ctk.CTkCheckBox(
        f_op_content,
        text="Spread (điểm giá):",
        variable=var_check_spread,
        font=("Roboto", 11),
    ).grid(row=2, column=2, sticky="w", padx=10, pady=5)
    e_max_spread = ctk.CTkEntry(f_op_content, width=60, justify="center")
    e_max_spread.insert(0, str(safe_cfg.get("MAX_SPREAD_POINTS", 5)))
    e_max_spread.grid(row=2, column=3, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_op_content, text="Nghỉ sau đóng (s):", text_color="#FFB300").grid(
        row=3, column=0, sticky="w", padx=10, pady=5
    )
    e_post_close = ctk.CTkEntry(f_op_content, width=60, justify="center")
    e_post_close.insert(0, str(safe_cfg.get("POST_CLOSE_COOLDOWN", 0)))
    e_post_close.grid(row=3, column=1, sticky="w", padx=5, pady=5)

    # --- [GROUP 4: ⚙️ HỆ THỐNG & TẦN SUẤT (SYSTEM)] ---
    f_sys = ctk.CTkFrame(f_safety, border_width=1, border_color="#757575")
    f_sys.grid(row=3, column=0, columnspan=4, sticky="nsew", padx=5, pady=8)
    ctk.CTkLabel(
        f_sys,
        text="⚙️ HỆ THỐNG & TẦN SUẤT (SYSTEM)",
        text_color="#757575",
        font=("Roboto", 13, "bold"),
    ).pack(pady=5)
    f_sys_content = ctk.CTkFrame(f_sys, fg_color="transparent")
    f_sys_content.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(f_sys_content, text="Loop (s):").grid(
        row=0, column=0, sticky="w", padx=10, pady=5
    )
    e_daemon_loop = ctk.CTkEntry(f_sys_content, width=50, justify="center")
    e_daemon_loop.insert(0, str(safe_cfg.get("DAEMON_LOOP_DELAY", 15)))
    e_daemon_loop.grid(row=0, column=1, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_sys_content, text="Nhồi (s):").grid(
        row=0, column=2, sticky="w", padx=10, pady=5
    )
    e_scan_delay = ctk.CTkEntry(f_sys_content, width=50, justify="center")
    e_scan_delay.insert(0, str(safe_cfg.get("DCA_PCA_SCAN_INTERVAL", 2)))
    e_scan_delay.grid(row=0, column=3, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_sys_content, text="Nến Trend:").grid(
        row=1, column=0, sticky="w", padx=10, pady=5
    )
    e_num_h1 = ctk.CTkEntry(f_sys_content, width=50, justify="center")
    e_num_h1.insert(0, str(safe_cfg.get("NUM_H1_BARS", 70)))
    e_num_h1.grid(row=1, column=1, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_sys_content, text="Nến Entry:").grid(
        row=1, column=2, sticky="w", padx=10, pady=5
    )
    e_num_m15 = ctk.CTkEntry(f_sys_content, width=50, justify="center")
    e_num_m15.insert(0, str(safe_cfg.get("NUM_M15_BARS", 70)))
    e_num_m15.grid(row=1, column=3, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_sys_content, text="Log Spam (M):").grid(
        row=2, column=0, sticky="w", padx=10, pady=5
    )
    e_log_cooldown = ctk.CTkEntry(f_sys_content, width=50, justify="center")
    e_log_cooldown.insert(0, str(safe_cfg.get("LOG_COOLDOWN_MINUTES", 60)))
    e_log_cooldown.grid(row=2, column=1, sticky="w", padx=5, pady=5)
    ctk.CTkLabel(f_sys_content, text="Hen expire (h):").grid(
        row=2, column=2, sticky="w", padx=10, pady=5
    )
    e_pending_expire = ctk.CTkEntry(f_sys_content, width=50, justify="center")
    e_pending_expire.insert(0, str(safe_cfg.get("PENDING_ORDER_EXPIRE_HOURS", 24)))
    e_pending_expire.grid(row=2, column=3, sticky="w", padx=5, pady=5)

    # [HINT / LEGEND AT BOTTOM]
    f_hint = ctk.CTkFrame(f_safety, fg_color="#212121")
    f_hint.grid(row=4, column=0, columnspan=4, sticky="nsew", padx=5, pady=5)
    for text, color in [
        ("Phanh Global", "#F44336"),
        ("Bảo vệ", "#00C853"),
        ("Điều kiện", "#2196F3"),
        ("Hệ thống", "#BDBDBD"),
    ]:
        ctk.CTkLabel(
            f_hint,
            text=f"● {text}",
            font=("Arial", 10, "italic"),
            text_color=color,
        ).pack(side="left", padx=(10, 2), pady=2)

    def save():
        try:
            import json, os
            import core.storage_manager as storage_manager
            cfg_path = storage_manager.BRAIN_FILE
            existing_data = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            volatility_symbols = list(
                dict.fromkeys(
                    symbol.strip().upper()
                    for symbol in e_volatility_symbols.get().replace(";", ",").split(",")
                    if symbol.strip()
                )
            )
            if var_volatility_brake.get() and not volatility_symbols:
                raise ValueError("Phải nhập ít nhất một mã giám sát biến động.")
            if "bot_safeguard" not in existing_data:
                existing_data["bot_safeguard"] = {}
            existing_data["bot_safeguard"].update(
                {
                    "MAX_DAILY_LOSS_PERCENT": float(e_max_loss.get()),
                    "MAX_OPEN_POSITIONS": int(e_max_open.get()),
                    "MAX_TRADES_PER_DAY": int(e_max_trades.get()),
                    "MAX_LOSING_STREAK": int(e_max_streak.get()),
                    "LOSS_COUNT_MODE": cbo_loss_mode.get(),
                    "COOLDOWN_MINUTES": int(e_cooldown.get()),
                    "NUM_H1_BARS": int(e_num_h1.get()),
                    "NUM_M15_BARS": int(e_num_m15.get()),
                    "CHECK_PING": var_check_ping.get(),
                    "MAX_PING_MS": int(e_max_ping.get()),
                    "CHECK_SPREAD": var_check_spread.get(),
                    "MAX_SPREAD_POINTS": float(e_max_spread.get()),
                    "DAEMON_LOOP_DELAY": float(e_daemon_loop.get()),
                    "DCA_PCA_SCAN_INTERVAL": float(e_scan_delay.get()),
                    "LOG_COOLDOWN_MINUTES": float(e_log_cooldown.get()),
                    "BOT_USE_SWING_TP": var_bot_use_swing_tp.get(),
                    "BOT_USE_RR_TP": var_bot_use_rr_tp.get(),
                    "BOT_TP_RR_RATIO": float(e_bot_tp_rr.get()),
                    "STRICT_MIN_LOT": var_strict_min_lot.get(),
                    "FORCE_MIN_LOT": var_force_min_lot.get(),
                    "STOCK_MAX_ORDER_NAV_PCT": float(e_nav_cap.get() or 0.0),
                    "RISK_GATE_MAX_PCT_PS": float(e_risk_gate_ps.get() or 0.0),
                    "RISK_GATE_MAX_PCT_CS": float(e_risk_gate_cs.get() or 0.0),
                    "POST_CLOSE_COOLDOWN": int(e_post_close.get()),
                    "GLOBAL_COOLDOWN_HOURS": float(e_global_cooldown.get()),
                    "APPLY_GLOBAL_COOLDOWN_ON_SAFEGUARD": var_gl_on_sg.get(),
                    "VOLATILITY_BRAKE_ENABLED": var_volatility_brake.get(),
                    "VOLATILITY_BRAKE_SYMBOLS": volatility_symbols,
                    "VOLATILITY_BRAKE_ACTION": action_labels[
                        cbo_volatility_action.get()
                    ],
                    "VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES": max(
                        0.0, float(e_volatility_symbol_cooldown.get())
                    ),
                    "VOLATILITY_BRAKE_TELEGRAM_ENABLED": var_volatility_telegram.get(),
                    "VOLATILITY_BRAKE_WINDOW_SECONDS": max(
                        5.0, float(e_volatility_window.get())
                    ),
                    "VOLATILITY_BRAKE_STOCK_PCT": max(
                        0.01, float(e_volatility_stock_pct.get())
                    ),
                    "VOLATILITY_BRAKE_DERIVATIVE_POINTS": max(
                        0.01, float(e_volatility_derivative_points.get())
                    ),
                    "VOLATILITY_BRAKE_CONFIRMATIONS": max(
                        1, int(e_volatility_confirmations.get())
                    ),
                    "VOLATILITY_BRAKE_SESSION_ENABLED": var_volatility_session.get(),
                    "VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS": max(
                        0.01, float(e_volatility_session_derivative.get())
                    ),
                    "VOLATILITY_BRAKE_SESSION_STOCK_PCT": max(
                        0.01, float(e_volatility_session_stock.get())
                    ),
                    "WATERMARK_TRIGGER": money_input_from_display(e_gl_wm_trigger.get(), cbo_gl_wm_trigger_unit.get()),
                    "WATERMARK_TRIGGER_UNIT": unit_from_display(cbo_gl_wm_trigger_unit.get()),
                    "WATERMARK_DRAWDOWN": money_input_from_display(e_gl_wm_drawdown.get(), cbo_gl_wm_drawdown_unit.get()),
                    "WATERMARK_DRAWDOWN_UNIT": unit_from_display(cbo_gl_wm_drawdown_unit.get()),
                    "MIN_SL_POINTS": int(e_gl_min_sl.get()),
                    "MAX_BASKET_DRAWDOWN_USD": money_input_from_display(e_gl_basket_dd.get(), cbo_gl_basket_dd_unit.get()),
                    "MAX_BASKET_DRAWDOWN_UNIT": unit_from_display(cbo_gl_basket_dd_unit.get()),
                    "REJECT_ON_MAX_LOT": var_gl_reject_lot.get(),
                    "GLOBAL_BRAKE_MODE": cbo_brake_mode.get(),
                    "BOT_ORDER_MODE": cbo_bot_order_mode.get(),
                    "BOT_ENTRY_ORDER_TYPE": cbo_bot_entry_order.get(),
                    "BOT_ATC_EXIT": var_bot_atc_exit.get(),
                    "PENDING_ORDER_EXPIRE_HOURS": float(e_pending_expire.get()),
                }
            )
            existing_data["BOT_ACTIVE_SYMBOLS"] = [
                coin for coin, var in app.bot_coin_vars.items() if var.get()
            ]
            config.BOT_ACTIVE_SYMBOLS = existing_data["BOT_ACTIVE_SYMBOLS"]
            # (Cache/WebSocket + watchlist CKCS đã chuyển sang ⚙ ADVANCED → tab "Cache & Mã".)

            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=4)
            from core.storage_manager import invalidate_settings_cache
            invalidate_settings_cache()
            if hasattr(app, "reload_config_from_json"):
                app.reload_config_from_json()
            app.log_message("✅ Đã cập nhật đầy đủ Bot Settings.", target="bot")
            top.destroy()
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Lỗi", f"Lỗi lưu cấu hình: {e}", parent=top)
    ctk.CTkButton(
        top,
        text="LƯU CẤU HÌNH BOT SETTINGS",
        fg_color=COL_BLUE_ACCENT,
        height=45,
        font=("Roboto", 13, "bold"),
        command=save,
    ).pack(pady=15, fill="x", padx=40)

# ==============================================================================

# DNSE ACCOUNT PICKER (dùng chung cho popup PRESET)

# ==============================================================================


def build_dnse_account_picker(app, parent):
    """Section cho phép tải danh sách tiểu khoản DNSE và lưu lựa chọn vào .env."""
    import threading
    from core import env_utils
    from get_accounts import fetch_accounts

    frame = ctk.CTkFrame(parent, fg_color="#1f2a33", corner_radius=8)
    frame.pack(fill="x", padx=8, pady=(0, 12))
    ctk.CTkLabel(
        frame, text="TÀI KHOẢN DNSE", font=FONT_BOLD, text_color="#4FC3F7"
    ).pack(anchor="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(
        frame,
        text="Nhập API key/secret rồi bấm 'Tải tài khoản' để chọn tiểu khoản. Lựa chọn được ghi vào .env.",
        font=("Arial", 11, "italic"),
        text_color="#90A4AE",
        wraplength=470,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(0, 8))

    f_key = ctk.CTkFrame(frame, fg_color="transparent")
    f_key.pack(fill="x", padx=12)
    ctk.CTkLabel(f_key, text="API Key", width=70, anchor="w").grid(row=0, column=0, sticky="w", pady=3)
    e_api_key = ctk.CTkEntry(f_key, width=320)
    e_api_key.insert(0, env_utils.get_env_value("DNSE_API_KEY", "") or "")
    e_api_key.grid(row=0, column=1, sticky="ew", pady=3, padx=(6, 0))
    ctk.CTkLabel(f_key, text="API Secret", width=70, anchor="w").grid(row=1, column=0, sticky="w", pady=3)
    e_api_secret = ctk.CTkEntry(f_key, width=320, show="*")
    e_api_secret.insert(0, env_utils.get_env_value("DNSE_API_SECRET", "") or "")
    e_api_secret.grid(row=1, column=1, sticky="ew", pady=3, padx=(6, 0))
    f_key.grid_columnconfigure(1, weight=1)

    lbl_status = ctk.CTkLabel(frame, text="", font=("Roboto", 11), text_color="#B0BEC5", wraplength=470, justify="left")
    lbl_status.pack(anchor="w", padx=12, pady=(6, 2))

    cbo_account = ctk.CTkOptionMenu(frame, values=["(chưa tải)"], width=440)
    cbo_account.pack(fill="x", padx=12, pady=(2, 6))

    # accounts_map: hiển thị -> dict tiểu khoản
    accounts_map = {}

    # Mở lại popup vẫn phải thấy tiểu khoản đã lưu, không tạo cảm giác setting mất.
    saved_account = str(env_utils.get_env_value("DNSE_ACCOUNT_NO", "") or "").strip()
    if saved_account:
        saved_label = f"{saved_account} [đã lưu]"
        accounts_map[saved_label] = {
            "id": saved_account,
            "stock": str(env_utils.get_env_value("DNSE_STOCK_ACCOUNT_NO", "") or "").strip(),
            "derivative": str(env_utils.get_env_value("DNSE_DERIVATIVE_ACCOUNT_NO", "") or "").strip(),
            "status": "SAVED",
            "custody": str(env_utils.get_env_value("DNSE_CUSTODY_CODE", "") or "").strip(),
        }
        cbo_account.configure(values=[saved_label])
        cbo_account.set(saved_label)
        lbl_status.configure(
            text=f"Đã lưu tiểu khoản {saved_account}. Bấm 'Tải tài khoản' nếu muốn đổi.",
            text_color="#81C784",
        )

    def _set_status(text, color="#B0BEC5"):
        lbl_status.configure(text=text, text_color=color)

    def _populate(data):
        accounts_map.clear()
        options = []
        for acc in data.get("accounts", []) or []:
            # `id` là SỐ tiểu khoản thật; dealAccount/derivativeAccount là CỜ boolean
            # báo tiểu khoản này trade được cơ sở / phái sinh.
            acc_id = str(acc.get("id", "") or "")
            can_deal = bool(acc.get("dealAccount"))
            can_deriv = bool(acc.get("derivativeAccount"))
            status = (acc.get("derivative", {}) or {}).get("status", "?")
            caps = []
            if can_deal:
                caps.append("Cơ sở")
            if can_deriv:
                caps.append("Phái sinh")
            label = f"{acc_id} [{'+'.join(caps) or '—'}] {status}"
            accounts_map[label] = {
                "id": acc_id,
                "stock": acc_id if can_deal else "",
                "derivative": acc_id if can_deriv else "",
                "status": status,
                "custody": data.get("custodyCode", ""),
            }
            options.append(label)
        if options:
            cbo_account.configure(values=options)
            cbo_account.set(options[0])
            _set_status(
                f"Tải thành công: {data.get('name', '')} (Custody {data.get('custodyCode', '')}). "
                f"Có {len(options)} tiểu khoản.",
                "#81C784",
            )
        else:
            cbo_account.configure(values=["(không có tiểu khoản)"])
            cbo_account.set("(không có tiểu khoản)")
            _set_status("Không tìm thấy tiểu khoản nào.", "#FFB74D")

    def _on_load():
        api_key = e_api_key.get().strip()
        api_secret = e_api_secret.get().strip()
        if not api_key or not api_secret:
            _set_status("Thiếu API key hoặc secret.", "#E57373")
            return
        _set_status("Đang tải tài khoản...", "#B0BEC5")

        def _worker():
            try:
                status_code, data = fetch_accounts(api_key, api_secret)
            except Exception as exc:  # noqa: BLE001
                app.after(0, lambda: _set_status(f"Lỗi tải tài khoản: {exc}", "#E57373"))
                return
            if status_code == 200 and isinstance(data, dict):
                app.after(0, lambda: _populate(data))
            else:
                app.after(0, lambda: _set_status(f"HTTP {status_code}: {data}", "#E57373"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_save():
        label = cbo_account.get()
        info = accounts_map.get(label)
        if not info:
            _set_status("Hãy tải và chọn một tiểu khoản trước.", "#E57373")
            return
        api_key = e_api_key.get().strip()
        api_secret = e_api_secret.get().strip()
        if not api_key or not api_secret:
            _set_status("Không lưu: API key hoặc API secret đang trống.", "#E57373")
            return
        updates = {
            "DNSE_API_KEY": api_key,
            "DNSE_API_SECRET": api_secret,
            "DNSE_ACCOUNT_NO": info["id"],
            "DNSE_STOCK_ACCOUNT_NO": info["stock"],
            "DNSE_DERIVATIVE_ACCOUNT_NO": info["derivative"],
            "DNSE_CUSTODY_CODE": info["custody"],
        }
        try:
            env_utils.update_env(updates)
        except Exception as exc:  # noqa: BLE001
            _set_status(f"Không ghi được .env: {exc}", "#E57373")
            return

        # Áp dụng ngay cho connector đang chạy và khởi động lại riêng daemon.
        # Không cần đóng/mở toàn bộ app chỉ để BRAIN nhận credential mới.
        try:
            connector = app.connector
            connector.api_key = api_key
            connector.api_secret = api_secret
            connector.account_no = info["id"]
            connector.stock_account_no = info["stock"]
            connector.derivative_account_no = info["derivative"]
            connector.custody_code = info["custody"]
            connector.reset_session_caches()
            connector.connect()

            daemon_process = getattr(app, "daemon_process", None)
            if daemon_process and getattr(daemon_process, "poll", lambda: None)() is None:
                daemon_process.terminate()
                daemon_process.wait(timeout=5)
            daemon_output = getattr(app, "daemon_output_file", None)
            if daemon_output and not daemon_output.closed:
                daemon_output.close()
            app.daemon_process = None
            app.start_daemon_process()
        except Exception as exc:  # noqa: BLE001
            _set_status(
                f"Đã lưu đủ .env nhưng kết nối lại chưa thành công: {exc}",
                "#FFB74D",
            )
            return
        warn = ""
        if str(info["status"]).upper() not in {"ACTIVE", "SAVED"}:
            warn = f" | CẢNH BÁO: phái sinh (CKPS) trạng thái '{info['status']}', chưa sẵn sàng."
        _set_status(
            f"Đã lưu đủ API key/secret và TK {info['id']}; đang kết nối lại BRAIN.{warn}",
            "#81C784" if not warn else "#FFB74D",
        )

    f_btn = ctk.CTkFrame(frame, fg_color="transparent")
    f_btn.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkButton(f_btn, text="Tải tài khoản", width=140, fg_color="#0277BD", command=_on_load).pack(side="left")
    ctk.CTkButton(f_btn, text="Lưu lựa chọn vào .env", width=180, fg_color="#2E7D32", command=_on_save).pack(side="left", padx=(8, 0))


# ==============================================================================

# ADVANCED — TRUNG TÂM CÀI ĐẶT (TÀI KHOẢN/.ENV + OTP)

# ==============================================================================


def build_cache_and_symbols_tab(app, parent):
    """Tab gom: watchlist CKCS (mã cơ sở) + Cache/WebSocket. Có nút lưu riêng."""
    from core import env_utils
    top = parent.winfo_toplevel()
    frame = _speed_up_scroll(ctk.CTkScrollableFrame(parent, fg_color="transparent"))
    frame.pack(fill="both", expand=True, padx=6, pady=6)

    cache_panel = ctk.CTkFrame(
        frame,
        fg_color="#20262A",
        corner_radius=10,
        border_width=1,
        border_color="#37474F",
    )
    cache_panel.pack(fill="x", padx=6, pady=(4, 10))
    ctk.CTkLabel(
        cache_panel,
        text="CACHE & DANH SÁCH MÃ",
        font=("Roboto", 13, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=14, pady=(12, 6))

    # --- Watchlist CKCS (mã cơ sở nhập tay) ---
    ctk.CTkLabel(cache_panel, text="WATCHLIST CKCS (MÃ CƠ SỞ) — NHẬP TAY, CÁCH NHAU DẤU PHẨY",
                 font=FONT_BOLD, text_color="#26C6DA").pack(pady=(6, 2))
    _add_popup_hint(
        cache_panel,
        "- CKPS (phái sinh) chỉ trade VN30F, watchlist khoá sẵn.\n"
        "- CKCS (cơ sở) nhập mã tại đây, vd: FPT,SSI,VCB. Lưu vào .env (DNSE_CKCS_WATCHLIST).",
        padx=18, pady=(0, 6),
    )
    e_ckcs = ctk.CTkEntry(cache_panel, justify="center")
    e_ckcs.insert(0, env_utils.get_env_value("DNSE_CKCS_WATCHLIST", "") or "")
    e_ckcs.pack(fill="x", padx=18, pady=(0, 10))

    # --- Mã CKCS ưu tiên: được quét trước và chỉ vượt giới hạn tổng vị thế mở. ---
    import core.storage_manager as storage_manager

    brain_now = storage_manager.load_brain_settings()
    initial_priority = {
        str(item).strip().upper()
        for item in (brain_now.get("PRIORITY_SYMBOLS", []) or [])
        if str(item).strip()
    }
    priority_vars = {}
    f_priority = ctk.CTkFrame(cache_panel, fg_color="#302B1F", corner_radius=8)
    f_priority.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkLabel(
        f_priority,
        text="⭐ MÃ CKCS PRIORITY",
        font=("Roboto", 12, "bold"),
        text_color="#FFD54F",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        f_priority,
        text=(
            "Được kiểm tra trước và được vượt đúng giới hạn Max Lệnh Mở. "
            "Vẫn phải có tín hiệu tốt và vẫn chịu mọi rule rủi ro/cooldown khác."
        ),
        font=("Roboto", 10, "bold"),
        text_color="#B0BEC5",
        wraplength=620,
        justify="left",
    ).pack(anchor="w", padx=10, pady=(0, 6))
    priority_grid = ctk.CTkFrame(f_priority, fg_color="transparent")
    priority_grid.pack(fill="x", padx=8, pady=(0, 4))

    def _priority_available_symbols():
        return list(dict.fromkeys(
            str(item).strip().upper()
            for item in e_ckcs.get().split(",")
            if str(item).strip()
        ))

    def _rebuild_priority_buttons():
        previous = {symbol: bool(var.get()) for symbol, var in priority_vars.items()}
        for child in priority_grid.winfo_children():
            child.destroy()
        priority_vars.clear()
        for index, symbol in enumerate(_priority_available_symbols()):
            selected = previous.get(symbol, symbol in initial_priority)
            var = ctk.BooleanVar(value=selected)
            priority_vars[symbol] = var
            ctk.CTkCheckBox(
                priority_grid,
                text=symbol,
                variable=var,
                width=120,
                checkbox_width=18,
                checkbox_height=18,
            ).grid(row=index // 4, column=index % 4, sticky="w", padx=6, pady=4)

    _rebuild_priority_buttons()
    ctk.CTkButton(
        f_priority,
        text="CẬP NHẬT TỪ Ô WATCHLIST",
        width=190,
        height=27,
        fg_color="#6D5D20",
        command=_rebuild_priority_buttons,
    ).pack(anchor="w", padx=10, pady=(2, 8))

    # --- Danh sách mã RAW DATA độc lập với quyền trade của BOT ---
    raw_setting = brain_now.get("SCAN_SNAPSHOT_SYMBOLS", _build_watch_symbols())
    initial_raw = {
        str(item).strip().upper()
        for item in (raw_setting or [])
        if str(item).strip()
    }
    raw_vars = {}
    f_raw = ctk.CTkFrame(cache_panel, fg_color="#232D33", corner_radius=8)
    f_raw.pack(fill="x", padx=12, pady=(0, 10))
    ctk.CTkLabel(
        f_raw,
        text="MÃ ĐƯỢC QUÉT VÀ GHI VÀO RAW DATA",
        font=("Roboto", 12, "bold"),
        text_color="#80DEEA",
    ).pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkLabel(
        f_raw,
        text="Độc lập với quyền đặt lệnh BOT. Mặc định chọn toàn bộ mã hiện có, kể cả VN30F.",
        font=("Roboto", 10, "bold"),
        text_color="#B0BEC5",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", padx=10, pady=(0, 6))
    raw_grid = ctk.CTkFrame(f_raw, fg_color="transparent")
    raw_grid.pack(fill="x", padx=8, pady=(0, 4))

    def _raw_available_symbols():
        ckcs = [str(item).strip().upper() for item in e_ckcs.get().split(",") if str(item).strip()]
        ckps = [str(item).strip().upper() for item in (getattr(config, "CKPS_SYMBOLS", []) or []) if str(item).strip()]
        return list(dict.fromkeys(ckps + ckcs))

    def _rebuild_raw_symbol_buttons(select_new=True):
        previous = {symbol: bool(var.get()) for symbol, var in raw_vars.items()}
        for child in raw_grid.winfo_children():
            child.destroy()
        raw_vars.clear()
        for index, symbol in enumerate(_raw_available_symbols()):
            selected = previous.get(symbol, symbol in initial_raw if not previous else bool(select_new))
            var = ctk.BooleanVar(value=selected)
            raw_vars[symbol] = var
            ctk.CTkCheckBox(
                raw_grid,
                text=symbol,
                variable=var,
                width=120,
                checkbox_width=18,
                checkbox_height=18,
            ).grid(row=index // 4, column=index % 4, sticky="w", padx=6, pady=4)

    _rebuild_raw_symbol_buttons(select_new=False)
    raw_actions = ctk.CTkFrame(f_raw, fg_color="transparent")
    raw_actions.pack(fill="x", padx=8, pady=(2, 8))
    ctk.CTkButton(
        raw_actions,
        text="CHỌN TẤT CẢ",
        width=115,
        height=27,
        command=lambda: [var.set(True) for var in raw_vars.values()],
    ).pack(side="left", padx=(0, 5))
    ctk.CTkButton(
        raw_actions,
        text="BỎ TẤT CẢ",
        width=105,
        height=27,
        fg_color="#616161",
        command=lambda: [var.set(False) for var in raw_vars.values()],
    ).pack(side="left", padx=5)
    ctk.CTkButton(
        raw_actions,
        text="CẬP NHẬT TỪ Ô WATCHLIST",
        height=27,
        fg_color="#455A64",
        command=_rebuild_raw_symbol_buttons,
    ).pack(side="left", padx=5)

    # --- Cache & Market Data ---
    ctk.CTkLabel(cache_panel, text="CACHE & MARKET DATA", font=FONT_BOLD, text_color="#FFD54F").pack(pady=(4, 2))
    _add_popup_hint(
        cache_panel,
        "- Bật WebSocket để stream giá real-time → giảm REST, add nhiều mã không bị BAN.\n"
        "- TTL cache REST (giây): tăng = gọi API ít hơn, giảm = cập nhật nhanh hơn.\n"
        "- WebSocket lỗi sẽ tự fallback REST.",
        padx=18, pady=(0, 6),
    )
    f_cache = ctk.CTkFrame(cache_panel, fg_color="#2b2b2b", corner_radius=8)
    f_cache.pack(fill="x", padx=12, pady=(0, 8))
    var_ws_enabled = ctk.BooleanVar(value=bool(getattr(config, "DNSE_WS_ENABLED", False)))
    ctk.CTkSwitch(
        f_cache, text="WEBSOCKET STREAMING", variable=var_ws_enabled,
        progress_color=COL_GREEN, fg_color=COL_RED, font=("Roboto", 12, "bold"),
    ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=8)

    def _cache_entry(label, value, row, col):
        ctk.CTkLabel(f_cache, text=label).grid(row=row, column=col, sticky="w", padx=10, pady=4)
        e = ctk.CTkEntry(f_cache, width=70, justify="center")
        e.insert(0, str(value))
        e.grid(row=row, column=col + 1, sticky="w", padx=10, pady=4)
        return e

    e_ttl_tick = _cache_entry("Tick TTL (s)", getattr(config, "DNSE_TICK_CACHE_TTL_SECONDS", 2.0), 1, 0)
    e_ttl_ohlc = _cache_entry("OHLC TTL (s)", getattr(config, "DNSE_OHLC_CACHE_TTL_SECONDS", 30.0), 1, 2)
    e_ttl_acc = _cache_entry("Account TTL (s)", getattr(config, "DNSE_ACCOUNT_CACHE_TTL_SECONDS", 5.0), 2, 0)
    e_ttl_pos = _cache_entry("Positions TTL (s)", getattr(config, "DNSE_POSITIONS_CACHE_TTL_SECONDS", 5.0), 2, 2)

    lbl_ws_status = ctk.CTkLabel(f_cache, text="WS: ...", font=("Consolas", 11), text_color="#90A4AE", justify="left")
    lbl_ws_status.grid(row=3, column=0, columnspan=4, sticky="w", padx=10, pady=(4, 8))

    def _refresh_ws_status():
        if not lbl_ws_status.winfo_exists():
            return
        try:
            from core.data_engine import data_engine
            snap = data_engine.get_api_health_snapshot().get("ws", {}) or {}
            if not snap.get("available"):
                txt = "WS: chưa cài websocket-client (đang dùng REST)"
            elif not snap.get("enabled"):
                txt = "WS: đang TẮT (dùng REST + cache)"
            else:
                state = "CONNECTED" if snap.get("connected") else "reconnecting..."
                txt = (
                    f"WS: {state} | subscribed {len(snap.get('subscribed', []))} | "
                    f"msg {snap.get('messages', 0)} | reconnects {snap.get('reconnects', 0)}"
                )
            lbl_ws_status.configure(text=txt)
        except Exception:
            pass
        try:
            parent.winfo_toplevel().after(2000, _refresh_ws_status)
        except Exception:
            pass

    _refresh_ws_status()

    # --- Nâng cao (ít dùng, mặc định ẩn): WS URL / encoding / board ---
    adv_state = {"open": False}
    btn_adv = ctk.CTkButton(
        cache_panel, text="▸ Nâng cao (WebSocket URL / encoding / board)", width=340,
        fg_color="#37474F", hover_color="#455A64", anchor="w", font=("Roboto", 11),
    )
    btn_adv.pack(fill="x", padx=12, pady=(2, 2))
    f_adv = ctk.CTkFrame(cache_panel, fg_color="#2b2b2b", corner_radius=8)

    def _adv_entry(label, value, row):
        ctk.CTkLabel(f_adv, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=4)
        e = ctk.CTkEntry(f_adv, width=300)
        e.insert(0, str(value))
        e.grid(row=row, column=1, sticky="w", padx=10, pady=4)
        return e

    e_ws_url = _adv_entry("WS URL", getattr(config, "DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn"), 0)
    e_ws_enc = _adv_entry("WS Encoding", getattr(config, "DNSE_WS_ENCODING", "json"), 1)
    e_ws_board = _adv_entry("WS Board ID", getattr(config, "DNSE_WS_BOARD_ID", "G1"), 2)

    def _toggle_adv():
        adv_state["open"] = not adv_state["open"]
        if adv_state["open"]:
            f_adv.pack(fill="x", padx=12, pady=(0, 8), after=btn_adv)
            btn_adv.configure(text="▾ Nâng cao (WebSocket URL / encoding / board)")
        else:
            f_adv.pack_forget()
            btn_adv.configure(text="▸ Nâng cao (WebSocket URL / encoding / board)")

    btn_adv.configure(command=_toggle_adv)

    lbl_msg = ctk.CTkLabel(cache_panel, text="", font=("Roboto", 11), text_color="#B0BEC5", wraplength=620, justify="left")
    lbl_msg.pack(anchor="w", padx=14, pady=(2, 2))

    def _save():
        try:
            ckcs_list = [s.strip().upper() for s in e_ckcs.get().split(",") if s.strip()]
            ttl_tick = float(e_ttl_tick.get() or 2.0)
            ttl_ohlc = float(e_ttl_ohlc.get() or 30.0)
            ttl_acc = float(e_ttl_acc.get() or 5.0)
            ttl_pos = float(e_ttl_pos.get() or 2.0)
            ws_url = (e_ws_url.get() or "wss://ws-openapi.dnse.com.vn").strip()
            ws_enc = (e_ws_enc.get() or "json").strip()
            ws_board = (e_ws_board.get() or "G1").strip()
            env_utils.update_env({
                "DNSE_WS_ENABLED": "true" if var_ws_enabled.get() else "false",
                "DNSE_TICK_CACHE_TTL_SECONDS": str(ttl_tick),
                "DNSE_OHLC_CACHE_TTL_SECONDS": str(ttl_ohlc),
                "DNSE_ACCOUNT_CACHE_TTL_SECONDS": str(ttl_acc),
                "DNSE_POSITIONS_CACHE_TTL_SECONDS": str(ttl_pos),
                "DNSE_CKCS_WATCHLIST": ",".join(ckcs_list),
                "DNSE_WS_URL": ws_url,
                "DNSE_WS_ENCODING": ws_enc,
                "DNSE_WS_BOARD_ID": ws_board,
            })
            config.DNSE_WS_ENABLED = bool(var_ws_enabled.get())
            config.DNSE_TICK_CACHE_TTL_SECONDS = ttl_tick
            config.DNSE_OHLC_CACHE_TTL_SECONDS = ttl_ohlc
            config.DNSE_ACCOUNT_CACHE_TTL_SECONDS = ttl_acc
            config.DNSE_POSITIONS_CACHE_TTL_SECONDS = ttl_pos
            config.DNSE_WS_URL = ws_url
            config.DNSE_WS_ENCODING = ws_enc
            config.DNSE_WS_BOARD_ID = ws_board
            config.CKCS_WATCHLIST = ckcs_list
            raw_symbols = [symbol for symbol, var in raw_vars.items() if var.get()]
            priority_symbols = [
                symbol for symbol, var in priority_vars.items() if var.get()
            ]
            config.SCAN_SNAPSHOT_SYMBOLS = raw_symbols
            config.PRIORITY_SYMBOLS = priority_symbols
            brain = storage_manager.load_brain_settings()
            brain["SCAN_SNAPSHOT_SYMBOLS"] = raw_symbols
            brain["PRIORITY_SYMBOLS"] = priority_symbols
            if not storage_manager.save_brain_settings(brain):
                raise OSError("Không lưu được danh sách mã RAW DATA/PRIORITY")
            try:
                if hasattr(app, "on_market_type_change") and hasattr(app, "cbo_market_type"):
                    app.on_market_type_change(app.cbo_market_type.get())
            except Exception:
                pass
            try:
                refresh_symbols = getattr(app, "_refresh_bot_settings_symbols", None)
                if callable(refresh_symbols):
                    refresh_symbols()
            except Exception:
                pass
            try:
                from core.data_engine import data_engine
                if config.DNSE_WS_ENABLED:
                    data_engine.set_stream_symbols(
                        list(dict.fromkeys(
                            list(getattr(config, "BOT_ACTIVE_SYMBOLS", [])) + raw_symbols
                        ))
                    )
            except Exception:
                pass
            lbl_msg.configure(
                text=(
                    f"Đã lưu watchlist {len(ckcs_list)} mã; RAW DATA chọn "
                    f"{len(raw_symbols)}/{len(raw_vars)} mã; "
                    f"PRIORITY {len(priority_symbols)} mã."
                ),
                text_color="#81C784",
            )
        except Exception as exc:  # noqa: BLE001
            lbl_msg.configure(text=f"Lỗi lưu: {exc}", text_color="#E57373")

    ctk.CTkButton(cache_panel, text="LƯU CACHE & MÃ", width=180, fg_color="#2E7D32", command=_save).pack(anchor="w", padx=14, pady=(2, 14))

    # --- Sao lưu/chuyển máy: tách phần có thể public và phần riêng tư ---
    transfer_panel = ctk.CTkFrame(
        frame,
        fg_color="#252229",
        corner_radius=10,
        border_width=1,
        border_color="#4A3B52",
    )
    transfer_panel.pack(fill="x", padx=6, pady=(0, 10))
    ctk.CTkLabel(
        transfer_panel,
        text="SAO LƯU / CHUYỂN MÁY",
        font=("Roboto", 12, "bold"),
        text_color="#90CAF9",
        wraplength=520,
        justify="left",
    ).pack(anchor="w", padx=14, pady=(12, 2))
    ctk.CTkLabel(
        transfer_panel,
        text=(
            "PUBLIC: rule, risk, watchlist, indicator, preset, lịch Advisor — có thể đưa GitHub.\n"
            "PRIVATE: context cá nhân/chuyên gia và lịch sử RAW — tự chép, không đưa GitHub.\n"
            "ROLLBACK: chỉ giữ một bản setting trước lần khôi phục gần nhất.\n"
            "Không sao lưu .env, DNSE/OpenAI/Telegram key, chat ID, token, lệnh, PnL hoặc lịch sử giao dịch."
        ),
        font=("Roboto", 10, "bold"),
        text_color="#B0BEC5",
        wraplength=650,
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 7))

    def _active_transfer_account():
        account_dir = os.path.abspath(str(getattr(storage_manager, "_active_account_dir", "data")))
        if os.path.basename(account_dir).lower() in {"data", ""}:
            raise RuntimeError("Chưa xác định tài khoản đang dùng.")
        return account_dir

    def _export_settings():
        try:
            import settings_transfer

            result = settings_transfer.export_split_settings(_active_transfer_account())
            lbl_transfer_msg.configure(
                text=(
                    f"Đã tạo bản sao: PUBLIC {len(result['public_files'])} file, "
                    f"PRIVATE {len(result['private_files'])} file."
                ),
                text_color="#81C784",
            )
        except Exception as exc:  # noqa: BLE001
            lbl_transfer_msg.configure(text=f"Không tạo được bản sao: {exc}", text_color="#E57373")

    def _import_settings():
        try:
            import settings_transfer

            if bool(getattr(getattr(app, "var_auto_trade", None), "get", lambda: False)()):
                raise RuntimeError("Hãy tắt BOT trước khi khôi phục setting.")
            package = settings_transfer.PUBLIC_COPY_ROOT
            if not (package / "manifest.json").is_file():
                raise FileNotFoundError("Chưa có bản sao. Hãy bấm TẠO BẢN SAO trước.")
            if not messagebox.askyesno(
                "Khôi phục setting",
                (
                    "Khôi phục PUBLIC và PRIVATE đi cùng (nếu có) vào tài khoản hiện tại?\n"
                    "Setting cũ sẽ được giữ trong một thư mục ROLLBACK và ghi đè bản rollback cũ."
                ),
                parent=top,
            ):
                return
            result = settings_transfer.import_split_settings(
                package,
                _active_transfer_account(),
                include_private=True,
            )
            try:
                if hasattr(app, "reload_config_from_json"):
                    app.reload_config_from_json()
            except Exception:
                pass
            lbl_transfer_msg.configure(
                text=f"Đã khôi phục {result['restored']} file.",
                text_color="#FFB74D",
            )
            if messagebox.askyesno(
                "Khôi phục hoàn tất",
                "Khởi động lại app ngay để áp dụng toàn bộ setting?",
                parent=top,
            ):
                import subprocess
                import sys

                project_root = os.path.dirname(os.path.abspath(__file__))
                main_script = os.path.join(project_root, "main.py")
                helper = (
                    "import subprocess,sys,time; "
                    "time.sleep(3); "
                    "subprocess.Popen([sys.argv[1], sys.argv[2]], cwd=sys.argv[3])"
                )
                if os.environ.get("RAT_LAUNCHED_BY_WATCHDOG") != "1":
                    subprocess.Popen(
                        [sys.executable, "-c", helper, sys.executable, main_script, project_root],
                        cwd=project_root,
                    )
                app.on_closing()
            else:
                lbl_transfer_msg.configure(
                    text="Đã khôi phục. Setting mới có hiệu lực đầy đủ ở lần mở app tiếp theo.",
                    text_color="#FFB74D",
                )
        except Exception as exc:  # noqa: BLE001
            lbl_transfer_msg.configure(text=f"Không khôi phục được: {exc}", text_color="#E57373")

    def _check_settings():
        try:
            import settings_transfer

            package = settings_transfer.PUBLIC_COPY_ROOT
            if not (package / "manifest.json").is_file():
                raise FileNotFoundError("Chưa có bản sao để kiểm tra.")
            public = settings_transfer.validate_package(package)
            private_dir = settings_transfer.PRIVATE_COPY_ROOT
            private_count = 0
            if (private_dir / "manifest.json").is_file():
                private_count = len(settings_transfer.validate_package(private_dir)["files"])
            raw_note = ""
            if private_count and "scan_snapshot_cache.json" in settings_transfer.validate_package(private_dir)["files"]:
                raw_note = " (có RAW)"
            lbl_transfer_msg.configure(
                text=f"Gói hợp lệ: PUBLIC {len(public['files'])}, PRIVATE {private_count} file{raw_note}.",
                text_color="#81C784",
            )
        except Exception as exc:  # noqa: BLE001
            lbl_transfer_msg.configure(text=f"Gói không hợp lệ: {exc}", text_color="#E57373")

    def _open_settings_folder():
        try:
            import settings_transfer

            settings_transfer.open_copy_folder()
            lbl_transfer_msg.configure(text="Đã mở thư mục sao lưu.", text_color="#81C784")
        except Exception as exc:  # noqa: BLE001
            lbl_transfer_msg.configure(text=f"Không mở được thư mục: {exc}", text_color="#E57373")

    def _delete_settings():
        try:
            import settings_transfer

            package = settings_transfer.PUBLIC_COPY_ROOT
            if not (package / "manifest.json").is_file():
                raise FileNotFoundError("Chưa có bản sao để xóa.")
            settings_transfer.validate_package(package)
            if not messagebox.askyesno(
                "Xóa bản sao",
                "Xóa toàn bộ file trong PUBLIC và PRIVATE?",
                parent=top,
            ):
                return
            settings_transfer.delete_package(package)
            lbl_transfer_msg.configure(text="Đã xóa bản sao.", text_color="#81C784")
        except Exception as exc:  # noqa: BLE001
            lbl_transfer_msg.configure(text=f"Không xóa được: {exc}", text_color="#E57373")

    bundle_row = ctk.CTkFrame(transfer_panel, fg_color="transparent")
    bundle_row.pack(fill="x", padx=14, pady=(0, 4))
    ctk.CTkButton(bundle_row, text="TẠO BẢN SAO", width=135, fg_color="#1565C0", hover_color="#0D47A1", command=_export_settings).pack(side="left")
    ctk.CTkButton(bundle_row, text="KHÔI PHỤC", width=125, fg_color="#6A1B9A", hover_color="#4A148C", command=_import_settings).pack(side="left", padx=(7, 0))
    ctk.CTkButton(bundle_row, text="KIỂM TRA", width=110, fg_color="#455A64", hover_color="#37474F", command=_check_settings).pack(side="left", padx=(7, 0))
    ctk.CTkButton(bundle_row, text="MỞ THƯ MỤC", width=120, fg_color="#455A64", hover_color="#37474F", command=_open_settings_folder).pack(side="left", padx=(7, 0))
    ctk.CTkButton(bundle_row, text="XÓA", width=65, fg_color="#8E2424", hover_color="#641818", command=_delete_settings).pack(side="left", padx=(7, 0))
    lbl_transfer_msg = ctk.CTkLabel(
        transfer_panel,
        text="",
        font=("Roboto", 11),
        text_color="#B0BEC5",
        wraplength=650,
        justify="left",
    )
    lbl_transfer_msg.pack(anchor="w", padx=14, pady=(3, 12))


def build_manual_margin_tab(app, parent):
    """Manual-only CKCS margin settings. Bot margin stays hard-disabled in v1."""
    from core import margin_rules, storage_manager

    frame = _speed_up_scroll(ctk.CTkScrollableFrame(parent, fg_color="transparent"))
    frame.pack(fill="both", expand=True, padx=6, pady=6)

    brain = storage_manager.load_brain_settings()
    settings = margin_rules.settings_from_brain(brain)

    ctk.CTkLabel(frame, text="CKCS MARGIN MANUAL", font=FONT_BOLD, text_color="#FFD54F").pack(anchor="w", padx=14, pady=(8, 2))
    _add_popup_hint(
        frame,
        "- V1 chỉ hỗ trợ lệnh tay CKCS. Bot không dùng margin dù buying power cao.\n"
        "- Nếu DNSE không trả RTT/cash rõ ràng: app hiển thị UNKNOWN và mặc định chặn mở margin.\n"
        "- Lệnh vẫn đi theo tiểu khoản hiện tại; app không tự gọi API vay/deal margin mới.",
        padx=14,
        pady=(0, 8),
    )

    f = ctk.CTkFrame(frame, fg_color="#1f2a33", corner_radius=8)
    f.pack(fill="x", padx=12, pady=(0, 8))
    f.grid_columnconfigure(1, weight=1)

    var_enable = ctk.BooleanVar(value=bool(settings.get("ENABLE_MANUAL_MARGIN")))
    ctk.CTkSwitch(
        f,
        text="ENABLE_MANUAL_MARGIN",
        variable=var_enable,
        progress_color=COL_GREEN,
        fg_color=COL_RED,
        font=("Roboto", 12, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 8))

    ctk.CTkLabel(f, text="MARGIN_RISK_BASE", anchor="w").grid(row=1, column=0, sticky="w", padx=12, pady=4)
    var_base = ctk.StringVar(value=str(settings.get("MARGIN_RISK_BASE", "EQUITY_NAV")))
    ctk.CTkOptionMenu(f, variable=var_base, values=["EQUITY_NAV", "FREE_CASH"], width=180).grid(row=1, column=1, sticky="w", padx=8, pady=4)

    entries = {}

    def _entry(row, key, label):
        ctk.CTkLabel(f, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=12, pady=4)
        e = ctk.CTkEntry(f, width=120, justify="center")
        e.insert(0, str(settings.get(key, "")))
        e.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        entries[key] = e

    _entry(2, "MAX_MARGIN_ORDER_VALUE_PCT", "Max order value (% NAV)")
    _entry(3, "MIN_RTT_TO_OPEN", "Min RTT to open")
    _entry(4, "CALL_RTT", "Call RTT")
    _entry(5, "FORCE_RTT", "Force RTT")
    _entry(6, "MAX_MANUAL_MARGIN_LOSS_PCT", "Max manual loss (% NAV)")

    ctk.CTkLabel(
        f,
        text="BOT_ALLOW_MARGIN = False (hard disabled v1)",
        text_color="#FF8A80",
        font=("Roboto", 12, "bold"),
    ).grid(row=7, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 10))

    f_snap = ctk.CTkFrame(frame, fg_color="#2b2b2b", corner_radius=8)
    f_snap.pack(fill="x", padx=12, pady=(0, 8))
    lbl_snap = ctk.CTkLabel(f_snap, text="Account margin snapshot: ...", justify="left", anchor="w", font=("Consolas", 11), text_color="#B0BEC5")
    lbl_snap.pack(fill="x", padx=12, pady=10)

    def _refresh_snapshot():
        try:
            acc = app.connector.get_account_info() if getattr(app, "connector", None) else {}
            snap = margin_rules.account_snapshot(acc, settings)
            rtt = snap.get("rtt")
            rtt_txt = "UNKNOWN" if rtt is None else f"{float(rtt):.1f}%"
            lbl_snap.configure(
                text=(
                    f"cash_available={format_vnd_full(snap.get('cash_available', 0))} | "
                    f"buying_power={format_vnd_full(snap.get('buying_power', 0))} | "
                    f"margin_debt={format_vnd_full(snap.get('margin_debt', 0))} | RTT={rtt_txt}"
                )
            )
        except Exception as exc:
            lbl_snap.configure(text=f"Account margin snapshot: ERROR {exc}", text_color="#E57373")

    _refresh_snapshot()

    lbl_msg = ctk.CTkLabel(frame, text="", text_color="#B0BEC5", wraplength=560, justify="left")
    lbl_msg.pack(anchor="w", padx=14, pady=(2, 4))

    def _save():
        try:
            next_settings = margin_rules.settings_from_brain({"manual_margin": settings})
            next_settings["ENABLE_MANUAL_MARGIN"] = bool(var_enable.get())
            next_settings["MARGIN_RISK_BASE"] = str(var_base.get() or "EQUITY_NAV").upper()
            for key, entry in entries.items():
                next_settings[key] = float(entry.get() or margin_rules.DEFAULT_MANUAL_MARGIN[key])
            next_settings["BOT_ALLOW_MARGIN"] = False
            brain_now = storage_manager.load_brain_settings()
            brain_now["manual_margin"] = next_settings
            storage_manager.save_brain_settings(brain_now)
            config.MANUAL_MARGIN_CONFIG = dict(next_settings)
            settings.update(next_settings)
            lbl_msg.configure(text="Đã lưu CKCS margin manual. Bot margin vẫn OFF.", text_color="#81C784")
            _refresh_snapshot()
        except Exception as exc:
            lbl_msg.configure(text=f"Lỗi lưu margin: {exc}", text_color="#E57373")

    buttons = ctk.CTkFrame(frame, fg_color="transparent")
    buttons.pack(fill="x", padx=12, pady=(0, 12))
    ctk.CTkButton(buttons, text="Lưu Margin Manual", width=170, fg_color="#2E7D32", command=_save).pack(side="left")
    ctk.CTkButton(buttons, text="Refresh Snapshot", width=150, fg_color="#455A64", command=_refresh_snapshot).pack(side="left", padx=(8, 0))


def prompt_pending_close_mode(app, position):
    """Choose MARKET or LIMIT for a manual close cached outside OPEN."""
    symbol = str(getattr(position, "symbol", "") or "").upper()
    current_price = float(
        getattr(position, "price_current", 0.0)
        or getattr(position, "price_open", 0.0)
        or 0.0
    )
    result = {"value": None}
    top = ctk.CTkToplevel(app)
    top.title("Đóng lệnh ngoài phiên")
    top.geometry("460x290")
    top.resizable(False, False)
    top.transient(app)

    ctk.CTkLabel(
        top,
        text=f"ĐÓNG {symbol} KHI PHIÊN LIÊN TỤC MỞ",
        font=("Roboto", 16, "bold"),
        text_color="#FFD54F",
    ).pack(pady=(18, 10))

    mode_var = tk.StringVar(value="MARKET")
    mode = ctk.CTkSegmentedButton(
        top,
        values=["MARKET", "LIMIT"],
        variable=mode_var,
        height=34,
    )
    mode.pack(fill="x", padx=28)

    price_frame = ctk.CTkFrame(top, fg_color="transparent")
    price_frame.pack(fill="x", padx=28, pady=(12, 2))
    ctk.CTkLabel(price_frame, text="Giá LIMIT:", width=100, anchor="w").pack(side="left")
    price_entry = ctk.CTkEntry(price_frame, justify="center")
    price_entry.insert(0, app._price_internal_to_input(current_price, symbol))
    price_entry.pack(side="left", fill="x", expand=True)

    hint = ctk.CTkLabel(
        top,
        text="MARKET ưu tiên thoát. LIMIT bảo vệ giá nhưng có thể không khớp.",
        text_color="#B0BEC5",
        wraplength=400,
        justify="left",
    )
    hint.pack(fill="x", padx=28, pady=(8, 2))
    error = ctk.CTkLabel(top, text="", text_color="#EF5350")
    error.pack(fill="x", padx=28)

    def refresh_mode(*_args):
        price_entry.configure(
            state="normal" if mode_var.get().upper() == "LIMIT" else "disabled"
        )

    def accept():
        selected = mode_var.get().upper()
        limit_price = 0.0
        if selected == "LIMIT":
            limit_price = app._price_input_to_internal(price_entry.get(), symbol)
            if limit_price <= 0:
                error.configure(text="LIMIT phải có giá lớn hơn 0.")
                return
        result["value"] = {
            "entry_mode": selected,
            "limit_price": limit_price,
        }
        top.destroy()

    def cancel():
        result["value"] = None
        top.destroy()

    mode.configure(command=lambda _value: refresh_mode())
    refresh_mode()
    buttons = ctk.CTkFrame(top, fg_color="transparent")
    buttons.pack(fill="x", padx=28, pady=(12, 16))
    ctk.CTkButton(
        buttons,
        text="HỦY",
        width=120,
        fg_color="#455A64",
        command=cancel,
    ).pack(side="left")
    ctk.CTkButton(
        buttons,
        text="XÁC NHẬN",
        fg_color="#EF6C00",
        hover_color="#E65100",
        command=accept,
    ).pack(side="right", fill="x", expand=True, padx=(12, 0))

    top.protocol("WM_DELETE_WINDOW", cancel)
    top.grab_set()
    app.wait_window(top)
    return result["value"]


def build_market_calendar_tab(app, parent):
    """Lịch giao dịch global: DNSE working dates + ngày né ENTRY VN30F."""
    import threading
    import core.storage_manager as storage_manager
    from core import market_calendar

    body = _speed_up_scroll(ctk.CTkScrollableFrame(parent, fg_color="transparent"))
    body.pack(fill="both", expand=True, padx=6, pady=6)
    settings = market_calendar.normalize_settings(
        storage_manager.load_brain_settings().get("market_calendar", {})
    )

    status_box = ctk.CTkFrame(body, fg_color="#1f2a33", corner_radius=8)
    status_box.pack(fill="x", padx=8, pady=(4, 10))
    ctk.CTkLabel(
        status_box,
        text="TRẠNG THÁI LỊCH DNSE",
        font=FONT_BOLD,
        text_color="#4FC3F7",
    ).pack(anchor="w", padx=12, pady=(10, 3))
    lbl_status = ctk.CTkLabel(
        status_box, text="Đang đọc lịch...", justify="left", anchor="w", wraplength=590
    )
    lbl_status.pack(fill="x", padx=12, pady=(0, 6))

    button_row = ctk.CTkFrame(status_box, fg_color="transparent")
    button_row.pack(fill="x", padx=12, pady=(0, 10))

    def refresh_status():
        info = market_calendar.calendar_summary()
        labels = {
            "TRADING": "HÔM NAY CÓ GIAO DỊCH",
            "HOLIDAY": "HÔM NAY NGHỈ LỄ",
            "WEEKEND": "HÔM NAY NGHỈ CUỐI TUẦN",
            "UNKNOWN": "LỊCH DNSE CHƯA XÁC NHẬN",
        }
        text = (
            f"{labels.get(info.get('status'), info.get('status'))} | nguồn: {info.get('source')}\n"
            f"Cập nhật: {info.get('fetched_at') or 'chưa có'} | "
            f"phủ lịch: {info.get('coverage_start') or '—'} → {info.get('coverage_end') or '—'}\n"
            f"Đáo hạn VN30F gần nhất: {info.get('next_expiry') or '—'}"
        )
        if info.get("last_error"):
            text += f"\nLần tải gần nhất lỗi: {info['last_error']}"
        color = "#81C784" if info.get("status") == "TRADING" else (
            "#FFB74D" if info.get("status") in {"HOLIDAY", "WEEKEND"} else "#E57373"
        )
        lbl_status.configure(text=text, text_color=color)

    btn_refresh = None

    def refresh_dnse():
        btn_refresh.configure(state="disabled", text="Đang tải...")

        def worker():
            try:
                from core.data_engine import dnse_api

                market_calendar.refresh_from_dnse(dnse_api.get_working_dates)
            finally:
                app.after(0, lambda: (btn_refresh.configure(state="normal", text="Làm mới từ DNSE"), refresh_status()))

        threading.Thread(target=worker, daemon=True).start()

    btn_refresh = ctk.CTkButton(
        button_row,
        text="Làm mới từ DNSE",
        width=150,
        fg_color="#1565C0",
        hover_color="#0D47A1",
        command=refresh_dnse,
    )
    btn_refresh.pack(side="left")

    config_box = ctk.CTkFrame(body, fg_color="#252526", corner_radius=8)
    config_box.pack(fill="x", padx=8, pady=(0, 10))
    ctk.CTkLabel(
        config_box, text="LỊCH NGHỈ VÀ NGÀY NÉ ENTRY", font=FONT_BOLD, text_color="#FFD54F"
    ).pack(anchor="w", padx=12, pady=(10, 4))

    var_use_dnse = tk.BooleanVar(value=settings["use_dnse_working_dates"])
    var_block_ato = tk.BooleanVar(value=settings["block_entry_ato"])
    var_block_atc = tk.BooleanVar(value=settings["block_entry_atc"])
    var_expiry = tk.BooleanVar(value=settings["avoid_vn30_expiry_entry"])
    var_rebalance = tk.BooleanVar(value=settings["avoid_vn30_rebalance_entry"])
    var_ckcs_open_delay = tk.BooleanVar(value=settings["avoid_ckcs_open_entry"])
    ctk.CTkCheckBox(
        config_box,
        text="Tự lấy ngày giao dịch/lễ Tết từ DNSE",
        variable=var_use_dnse,
        font=("Roboto", 12, "bold"),
    ).pack(anchor="w", padx=12, pady=5)
    phase_row = ctk.CTkFrame(config_box, fg_color="transparent")
    phase_row.pack(fill="x", padx=12, pady=5)
    ctk.CTkCheckBox(
        phase_row,
        text="Chặn ENTRY ATO",
        variable=var_block_ato,
        font=("Roboto", 12, "bold"),
    ).pack(side="left")
    ctk.CTkCheckBox(
        phase_row,
        text="Chặn ENTRY ATC",
        variable=var_block_atc,
        font=("Roboto", 12, "bold"),
    ).pack(side="left", padx=(22, 0))
    ctk.CTkCheckBox(
        config_box,
        text="Né ngày đáo hạn VN30F (app tự tính)",
        variable=var_expiry,
        font=("Roboto", 12, "bold"),
    ).pack(anchor="w", padx=12, pady=5)
    ctk.CTkCheckBox(
        config_box,
        text="Né ngày đổi rổ VN30 (dùng danh sách bên dưới)",
        variable=var_rebalance,
        font=("Roboto", 12, "bold"),
    ).pack(anchor="w", padx=12, pady=5)
    delay_row = ctk.CTkFrame(config_box, fg_color="transparent")
    delay_row.pack(fill="x", padx=12, pady=5)
    ctk.CTkCheckBox(
        delay_row,
        text="BOT CKCS chờ sau ATO",
        variable=var_ckcs_open_delay,
        font=("Roboto", 12, "bold"),
    ).pack(side="left")
    e_ckcs_delay = ctk.CTkEntry(delay_row, width=65, justify="center")
    e_ckcs_delay.insert(0, str(settings["ckcs_entry_delay_minutes"]))
    e_ckcs_delay.pack(side="left", padx=(12, 5))
    ctk.CTkLabel(delay_row, text="phút (09:15 + số phút)", text_color="#B0BEC5").pack(side="left")

    ctk.CTkLabel(
        config_box,
        text="Ngày nghỉ bổ sung — mỗi dòng một ngày YYYY-MM-DD",
        text_color="#B0BEC5",
    ).pack(anchor="w", padx=12, pady=(10, 2))
    txt_closed = ctk.CTkTextbox(config_box, height=80)
    txt_closed.pack(fill="x", padx=12, pady=(0, 6))
    txt_closed.insert("1.0", "\n".join(settings["manual_closed_dates"]))

    ctk.CTkLabel(
        config_box,
        text="Ngày đổi rổ VN30 — mỗi dòng một ngày YYYY-MM-DD",
        text_color="#B0BEC5",
    ).pack(anchor="w", padx=12, pady=(8, 2))
    txt_rebalance = ctk.CTkTextbox(config_box, height=80)
    txt_rebalance.pack(fill="x", padx=12, pady=(0, 6))
    txt_rebalance.insert("1.0", "\n".join(settings["vn30_rebalance_dates"]))

    ctk.CTkLabel(
        config_box,
        text="Chỉ chặn ENTRY mới; đóng lệnh và quản lý vị thế vẫn chạy. Manual chỉ vượt khi bật BYPASS.",
        font=("Roboto", 11, "bold"),
        text_color="#FFB74D",
        wraplength=590,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(8, 5))
    lbl_save = ctk.CTkLabel(config_box, text="", text_color="#81C784")
    lbl_save.pack(anchor="w", padx=12, pady=(0, 3))

    def save_settings():
        try:
            next_settings = market_calendar.normalize_settings({
                "use_dnse_working_dates": var_use_dnse.get(),
                "manual_closed_dates": market_calendar.parse_date_text(txt_closed.get("1.0", "end")),
                "block_entry_ato": var_block_ato.get(),
                "block_entry_atc": var_block_atc.get(),
                "avoid_vn30_expiry_entry": var_expiry.get(),
                "avoid_vn30_rebalance_entry": var_rebalance.get(),
                "vn30_rebalance_dates": market_calendar.parse_date_text(txt_rebalance.get("1.0", "end")),
                "avoid_ckcs_open_entry": var_ckcs_open_delay.get(),
                "ckcs_entry_delay_minutes": int(e_ckcs_delay.get() or 0),
            })
            brain = storage_manager.load_brain_settings()
            brain["market_calendar"] = next_settings
            if not storage_manager.save_brain_settings(brain):
                raise RuntimeError("Không ghi được brain_settings.json")
            lbl_save.configure(text="Đã lưu lịch thị trường.", text_color="#81C784")
            refresh_status()
        except Exception as exc:
            lbl_save.configure(text=str(exc), text_color="#E57373")

    ctk.CTkButton(
        config_box,
        text="LƯU LỊCH THỊ TRƯỜNG",
        height=36,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=save_settings,
    ).pack(fill="x", padx=12, pady=(4, 12))
    refresh_status()


def build_bot_opportunity_tab(app, parent):
    """Cấu hình kho gợi ý BOT khi tín hiệu chưa được phép thành lệnh."""
    from core import signal_opportunities, storage_manager

    body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=8, pady=8)
    settings = signal_opportunities.normalize_settings(
        storage_manager.load_brain_settings().get("opportunity_settings", {})
    )

    ctk.CTkLabel(body, text="GỢI Ý BOT TRÊN RUNNING TABLE", font=FONT_BOLD, text_color="#CE93D8").pack(anchor="w", padx=12, pady=(10, 4))
    ctk.CTkLabel(
        body,
        text=(
            "BOT tắt hoặc lệnh bị chặn: tín hiệu được giữ thành dòng GỢI Ý, chưa gửi DNSE. "
            "Chỉ khi chuột phải và KÍCH HOẠT thì nó mới trở thành lệnh MARKET/LIMIT."
        ),
        text_color="#B0BEC5",
        wraplength=570,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(0, 10))

    var_enabled = tk.BooleanVar(value=settings["enabled"])
    var_history = tk.BooleanVar(value=settings["history_enabled"])
    var_show_running = tk.BooleanVar(value=settings["show_in_running_table"])
    ctk.CTkCheckBox(body, text="Bật kho gợi ý BOT", variable=var_enabled).pack(anchor="w", padx=12, pady=5)
    ctk.CTkCheckBox(body, text="Hiện gợi ý trên bảng lệnh đang chạy", variable=var_show_running).pack(anchor="w", padx=12, pady=5)
    ctk.CTkCheckBox(body, text="Ghi lịch sử gợi ý theo ngày", variable=var_history).pack(anchor="w", padx=12, pady=5)

    grid = ctk.CTkFrame(body, fg_color="#252526")
    grid.pack(fill="x", padx=12, pady=10)
    ctk.CTkLabel(grid, text="Giữ gợi ý (giờ):").grid(row=0, column=0, sticky="w", padx=10, pady=8)
    e_hours = ctk.CTkEntry(grid, width=80, justify="center")
    e_hours.insert(0, str(settings["retention_hours"]))
    e_hours.grid(row=0, column=1, sticky="w", padx=5, pady=8)
    ctk.CTkLabel(grid, text="Kiểu kích hoạt mặc định:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
    cbo_mode = ctk.CTkOptionMenu(grid, values=["MARKET", "LIMIT"], width=110)
    cbo_mode.set(settings["default_order_mode"])
    cbo_mode.grid(row=1, column=1, sticky="w", padx=5, pady=8)
    ctk.CTkLabel(grid, text="LIMIT chệch tối đa (bước giá):").grid(row=2, column=0, sticky="w", padx=10, pady=8)
    e_ticks = ctk.CTkEntry(grid, width=80, justify="center")
    e_ticks.insert(0, str(settings["default_slippage_ticks"]))
    e_ticks.grid(row=2, column=1, sticky="w", padx=5, pady=8)

    lbl = ctk.CTkLabel(body, text="", text_color="#81C784")
    lbl.pack(anchor="w", padx=12, pady=4)

    def save():
        try:
            next_settings = signal_opportunities.normalize_settings({
                "enabled": var_enabled.get(),
                "show_in_running_table": var_show_running.get(),
                "retention_hours": float(e_hours.get()),
                "history_enabled": var_history.get(),
                "default_order_mode": cbo_mode.get(),
                "default_slippage_ticks": int(e_ticks.get()),
            })
            brain = storage_manager.load_brain_settings()
            brain["opportunity_settings"] = next_settings
            if not storage_manager.save_brain_settings(brain):
                raise RuntimeError("Không ghi được brain_settings.json")
            app.var_show_bot_opportunities.set(next_settings["show_in_running_table"])
            app.on_show_bot_opportunities_change()
            lbl.configure(text="Đã lưu cấu hình gợi ý BOT.", text_color="#81C784")
        except Exception as exc:
            lbl.configure(text=f"Lỗi: {exc}", text_color="#E57373")

    ctk.CTkButton(body, text="LƯU CẤU HÌNH GỢI Ý", height=38, fg_color="#6A1B9A", command=save).pack(fill="x", padx=12, pady=(4, 12))


def build_money_display_tab(app, parent):
    """Một setting duy nhất cho mọi số tiền trên UI, TSL, E/E và lịch sử."""
    from core import env_utils, storage_manager
    from core.money import normalize_zero_trim, set_money_display_zero_trim

    body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=8, pady=8)

    ctk.CTkLabel(
        body,
        text="RÚT GỌN SỐ TIỀN HIỂN THỊ",
        font=("Roboto", 16, "bold"),
        text_color="#4FC3F7",
    ).pack(anchor="w", padx=12, pady=(12, 4))
    ctk.CTkLabel(
        body,
        text=(
            "Chỉ thay cách nhìn trên màn hình. Giá trị lưu, tính rủi ro, phí, "
            "PNL và lệnh gửi DNSE vẫn giữ nguyên VND thật. Setting này áp dụng "
            "chung cho các nhãn đọc trên dashboard, TSL, E/E, popup, log và lịch sử. "
            "Mọi ô setting tiền luôn nhập đủ VND thật và không bị nhân/chia theo lựa chọn này."
        ),
        wraplength=570,
        justify="left",
        text_color="#B0BEC5",
    ).pack(anchor="w", padx=12, pady=(0, 14))

    current = normalize_zero_trim()
    display_current = "Không bỏ" if current == "NONE" else current
    var_trim = tk.StringVar(value=display_current)
    selector = ctk.CTkSegmentedButton(
        body,
        values=["Không bỏ", "000", "000 000"],
        variable=var_trim,
        height=38,
        font=("Roboto", 13, "bold"),
    )
    selector.pack(fill="x", padx=12, pady=(0, 14))

    preview = ctk.CTkLabel(
        body,
        text="",
        justify="left",
        anchor="w",
        font=("Consolas", 13),
        text_color="#E0F2F1",
    )
    preview.pack(fill="x", padx=12, pady=(0, 14))

    ctk.CTkLabel(
        body,
        text=(
            "QUY TẮC Ô SETTING: luôn nhập đủ VND thật. Ví dụ muốn 500.000 VND "
            "phải nhập 500000 ở mọi chế độ hiển thị. Không tự thêm hoặc bớt số 0."
        ),
        wraplength=570,
        justify="left",
        text_color="#FFB300",
        font=("Roboto", 11, "bold"),
    ).pack(fill="x", padx=12, pady=(0, 14))

    def _selected_trim():
        value = str(var_trim.get() or "000")
        return "NONE" if value == "Không bỏ" else value

    def refresh_preview(*_args):
        trim = _selected_trim()
        scale = 1.0 if trim == "NONE" else (1000000.0 if trim == "000 000" else 1000.0)

        def f(value):
            amount = float(value) / scale
            digits = 0 if scale == 1 else (3 if scale == 1000 else 6)
            text = f"{amount:,.{digits}f}"
            return text.rstrip("0").rstrip(".") if "." in text else text

        rule = (
            "Không bỏ số 0: 1 = 1 VND"
            if trim == "NONE"
            else f"Bỏ {trim}: 1 = {int(scale):,} VND"
        )
        preview.configure(
            text=(
                f"{rule}\n\n"
                f"1.000 VND       → {f(1000)}\n"
                f"220.000 VND     → {f(220000)}\n"
                f"-191.847 VND    → {f(-191847)}\n"
                f"99.337.000 VND  → {f(99337000)}"
            )
        )

    selector.configure(command=lambda _value: refresh_preview())
    refresh_preview()

    status = ctk.CTkLabel(body, text="", text_color="#81C784")
    status.pack(fill="x", padx=12, pady=(2, 6))

    def save_display():
        try:
            trim = set_money_display_zero_trim(_selected_trim())
            brain = storage_manager.load_brain_settings()
            brain["MONEY_DISPLAY_ZERO_TRIM"] = trim
            brain["MONEY_DISPLAY_UNIT"] = getattr(config, "MONEY_DISPLAY_UNIT", "K_VND")
            if not storage_manager.save_brain_settings(brain):
                raise RuntimeError("Không ghi được brain_settings.json")
            env_utils.update_env({"MONEY_DISPLAY_ZERO_TRIM": trim})
            if hasattr(app, "lbl_money_unit_note"):
                from core.money import money_unit_note
                app.lbl_money_unit_note.configure(text=money_unit_note())
            status.configure(
                text="Đã áp dụng ngay. Popup đang mở cần đóng/mở lại để vẽ lại số.",
                text_color="#81C784",
            )
            app.log_message(f"[DISPLAY] Rút gọn tiền = {trim}", target="manual")
        except Exception as exc:
            status.configure(text=f"Lỗi lưu hiển thị: {exc}", text_color="#E57373")

    ctk.CTkButton(
        body,
        text="LƯU VÀ ÁP DỤNG HIỂN THỊ",
        height=40,
        fg_color="#2E7D32",
        hover_color="#1B5E20",
        command=save_display,
    ).pack(fill="x", padx=12, pady=(4, 14))


def open_advanced_tools_popup(app):
    """Trung tâm cài đặt hệ thống: tài khoản DNSE/.env + xác thực OTP (trading-token)."""
    import os
    import threading
    from core.data_engine import dnse_api

    top = ctk.CTkToplevel(app)
    top.title("Advanced — Cài đặt hệ thống")
    _fit_popup(top, 680, 760, 580, 560)
    _bring_popup_to_front(top)

    tabs = ctk.CTkTabview(top)
    tabs.pack(fill="both", expand=True, padx=8, pady=8)
    tab_sys = tabs.add("Hệ thống")
    tab_cache = tabs.add("Cache & Mã")
    tab_margin = tabs.add("Margin CKCS")
    tab_display = tabs.add("HIỂN THỊ")
    tab_calendar = tabs.add("LỊCH THỊ TRƯỜNG")
    tab_opportunities = tabs.add("GỢI Ý BOT")

    # Tab 2: gom cache + watchlist CKCS
    build_cache_and_symbols_tab(app, tab_cache)
    build_manual_margin_tab(app, tab_margin)
    build_money_display_tab(app, tab_display)
    build_market_calendar_tab(app, tab_calendar)
    build_bot_opportunity_tab(app, tab_opportunities)

    # Tab 1: tài khoản / cấu hình / OTP
    body = _speed_up_scroll(ctk.CTkScrollableFrame(tab_sys, fg_color="transparent"))
    body.pack(fill="both", expand=True, padx=6, pady=6)

    # --- 1) Tài khoản DNSE / .env ---
    build_dnse_account_picker(app, body)

    # --- 2) Cấu hình chung (.env) ---
    from core import env_utils
    f_cfg = ctk.CTkFrame(body, fg_color="#1f2a33", corner_radius=8)
    f_cfg.pack(fill="x", padx=8, pady=(4, 12))
    ctk.CTkLabel(f_cfg, text="CẤU HÌNH CHUNG (.env)", font=FONT_BOLD, text_color="#4FC3F7").pack(anchor="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(
        f_cfg,
        text="Lưu vào .env. BASE_URL / API_VERSION / ACCOUNT_NO / PAPER cần khởi động lại để áp dụng đầy đủ. "
        "Cache / WebSocket / TTL nằm trong popup ⚙ BOT.",
        font=("Arial", 11, "italic"),
        text_color="#90A4AE",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(0, 8))

    f_grid = ctk.CTkFrame(f_cfg, fg_color="transparent")
    f_grid.pack(fill="x", padx=12, pady=(0, 4))
    f_grid.grid_columnconfigure(1, weight=1)

    def _cfg_row(r, label, value):
        ctk.CTkLabel(f_grid, text=label, anchor="w", width=160).grid(row=r, column=0, sticky="w", pady=3)
        e = ctk.CTkEntry(f_grid, width=320)
        e.insert(0, str(value or ""))
        e.grid(row=r, column=1, sticky="ew", pady=3, padx=(6, 0))
        return e

    e_acc_no = _cfg_row(0, "DNSE_ACCOUNT_NO", env_utils.get_env_value("DNSE_ACCOUNT_NO", ""))
    e_base = _cfg_row(1, "DNSE_BASE_URL", env_utils.get_env_value("DNSE_BASE_URL", "https://openapi.dnse.com.vn"))
    e_ver = _cfg_row(2, "DNSE_API_VERSION", env_utils.get_env_value("DNSE_API_VERSION", "2026-05-07"))
    # Đọc thẳng từ .env (tránh hiện rỗng khi config trong RAM còn cũ).
    e_ckps = _cfg_row(3, "CKPS (VN30F)", env_utils.get_env_value("DNSE_CKPS_WATCHLIST", "VN30F1M"))
    # CKCS (mã cơ sở) đã chuyển sang tab "Cache & Mã".

    f_opt = ctk.CTkFrame(f_cfg, fg_color="transparent")
    f_opt.pack(fill="x", padx=12, pady=(2, 4))
    ctk.CTkLabel(f_opt, text="OTP type:").pack(side="left", padx=(0, 6))
    var_otp_type = ctk.StringVar(value=env_utils.get_env_value("DNSE_OTP_TYPE", "email_otp"))
    ctk.CTkOptionMenu(f_opt, values=["email_otp", "smart_otp"], variable=var_otp_type, width=140).pack(side="left", padx=(0, 16))
    # (PAPER/REAL chuyển ở nút MODE trên panel chính — không để trùng ở đây.)

    lbl_cfg_msg = ctk.CTkLabel(f_cfg, text="", font=("Roboto", 11), text_color="#B0BEC5", wraplength=560, justify="left")
    lbl_cfg_msg.pack(anchor="w", padx=12, pady=(2, 2))

    def _save_cfg():
        ckps = [s.strip().upper() for s in e_ckps.get().split(",") if s.strip()]
        try:
            env_utils.update_env({
                "DNSE_ACCOUNT_NO": e_acc_no.get().strip(),
                "DNSE_BASE_URL": e_base.get().strip(),
                "DNSE_API_VERSION": e_ver.get().strip(),
                "DNSE_OTP_TYPE": var_otp_type.get(),
                "DNSE_CKPS_WATCHLIST": ",".join(ckps),
            })
        except Exception as exc:  # noqa: BLE001
            lbl_cfg_msg.configure(text=f"Không ghi được .env: {exc}", text_color="#E57373")
            return
        config.CKPS_SYMBOLS = ckps or ["VN30F1M"]
        # Cập nhật dropdown Mã CK ngay (khỏi cần restart) theo chế độ đang chọn.
        try:
            if hasattr(app, "on_market_type_change") and hasattr(app, "cbo_market_type"):
                app.on_market_type_change(app.cbo_market_type.get())
        except Exception:
            pass
        lbl_cfg_msg.configure(
            text="Đã lưu. Danh sách mã đã cập nhật vào dropdown. (BASE_URL/API_VERSION/ACCOUNT_NO/PAPER cần khởi động lại.)",
            text_color="#81C784",
        )

    ctk.CTkButton(f_cfg, text="Lưu cấu hình chung", width=180, fg_color="#2E7D32", command=_save_cfg).pack(anchor="w", padx=12, pady=(2, 10))

    # --- 3) Xác thực OTP / Trading-token ---
    f_otp = ctk.CTkFrame(body, fg_color="#1f2a33", corner_radius=8)
    f_otp.pack(fill="x", padx=8, pady=(4, 12))
    ctk.CTkLabel(
        f_otp, text="XÁC THỰC OTP (TRADING TOKEN ~8 GIỜ)", font=FONT_BOLD, text_color="#4FC3F7"
    ).pack(anchor="w", padx=12, pady=(10, 2))
    ctk.CTkLabel(
        f_otp,
        text="Bấm 'Gửi OTP email' để DNSE gửi mã về email, nhập mã rồi 'Xác thực'. "
        "Token hiệu lực ~8 giờ, cần để đặt lệnh thật (market data không cần).",
        font=("Arial", 11, "italic"),
        text_color="#90A4AE",
        wraplength=560,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(0, 8))

    f_otp_row = ctk.CTkFrame(f_otp, fg_color="transparent")
    f_otp_row.pack(fill="x", padx=12, pady=(0, 4))
    e_otp = ctk.CTkEntry(f_otp_row, width=200, justify="center", placeholder_text="Nhập mã OTP")
    lbl_otp_msg = ctk.CTkLabel(f_otp, text="", font=("Roboto", 11), text_color="#B0BEC5", wraplength=560, justify="left")
    lbl_otp_status = ctk.CTkLabel(f_otp, text="", font=("Roboto", 11), text_color="#B0BEC5", wraplength=560, justify="left")

    def _set_otp_msg(text, color="#B0BEC5"):
        if lbl_otp_msg.winfo_exists():
            lbl_otp_msg.configure(text=text, text_color=color)

    def _refresh_token_status():
        try:
            # [FIX] Token nằm trên connector đặt lệnh (app.connector), không phải dnse_api.
            ok = app.connector.has_trading_token()
        except Exception:
            ok = False
        if lbl_otp_status.winfo_exists():
            lbl_otp_status.configure(
                text=("✅ Đã có trading-token (≈8h)." if ok else "Chưa xác thực — chưa đặt được lệnh thật."),
                text_color="#81C784" if ok else "#FFB74D",
            )

    def _send_otp():
        _set_otp_msg("Đang gửi OTP...", "#B0BEC5")

        def _w():
            try:
                ok = app.connector.send_email_otp()
            except Exception as exc:  # noqa: BLE001
                app.after(0, lambda: _set_otp_msg(f"Lỗi gửi OTP: {exc}", "#E57373"))
                return
            app.after(0, lambda: _set_otp_msg(
                "Đã gửi OTP về email." if ok else "Gửi OTP thất bại.",
                "#81C784" if ok else "#E57373",
            ))

        threading.Thread(target=_w, daemon=True).start()

    def _verify_otp():
        code = e_otp.get().strip()
        if not code:
            _set_otp_msg("Nhập mã OTP trước.", "#E57373")
            return
        otp_type = os.getenv("DNSE_OTP_TYPE", "email_otp")
        _set_otp_msg("Đang xác thực...", "#B0BEC5")

        def _w():
            try:
                ok = app.connector.verify_otp(otp_type, code)
            except Exception as exc:  # noqa: BLE001
                app.after(0, lambda: _set_otp_msg(f"Lỗi xác thực: {exc}", "#E57373"))
                return

            def _done():
                _set_otp_msg("✅ Xác thực thành công." if ok else "❌ Xác thực thất bại.", "#81C784" if ok else "#E57373")
                _refresh_token_status()

            app.after(0, _done)

        threading.Thread(target=_w, daemon=True).start()

    ctk.CTkButton(f_otp_row, text="Gửi OTP email", width=140, fg_color="#0277BD", command=_send_otp).pack(side="left")
    e_otp.pack(side="left", padx=(8, 8))
    ctk.CTkButton(f_otp_row, text="Xác thực", width=110, fg_color="#2E7D32", command=_verify_otp).pack(side="left")

    lbl_otp_msg.pack(anchor="w", padx=12, pady=(4, 2))
    lbl_otp_status.pack(anchor="w", padx=12, pady=(0, 4))

    # [opt-in] Lưu token qua restart — cảnh báo bảo mật.
    var_persist_token = ctk.BooleanVar(value=bool(getattr(config, "PERSIST_TRADING_TOKEN", False)))

    def _toggle_persist():
        from core import env_utils
        on = bool(var_persist_token.get())
        config.PERSIST_TRADING_TOKEN = on
        try:
            env_utils.update_env({"PERSIST_TRADING_TOKEN": "True" if on else "False"})
        except Exception:
            pass
        # Bật + đang có token -> lưu ngay; tắt -> không xoá file cũ ở đây (an toàn, để user tự dọn).
        if on:
            try:
                app.connector._save_token_to_disk()
            except Exception:
                pass

    ctk.CTkCheckBox(
        f_otp_row.master, text="Lưu token qua restart (⚠ rủi ro: ai có file cũng đặt lệnh được)",
        variable=var_persist_token, command=_toggle_persist,
        text_color="#FFB74D", font=("Roboto", 11),
    ).pack(anchor="w", padx=12, pady=(0, 10))
    _refresh_token_status()


# ==============================================================================

# 2. POPUP PRESET (CÓ LIVE PREVIEW ĐẦY ĐỦ)

# ==============================================================================


def build_paper_config_tab(app, parent):
    """Tab Paper: chỉ vốn ảo + reset. Phí/spread/cách tính đều mô phỏng như tài khoản thật."""
    from core import env_utils
    from core.money import money_input_from_display, money_input_to_display, money_setting_hint, money_unit_note

    frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=6, pady=6)
    ctk.CTkLabel(frame, text="CẤU HÌNH PAPER (TÀI KHOẢN ẢO)", font=FONT_BOLD, text_color="#FFD54F").pack(anchor="w", pady=(8, 2))
    ctk.CTkLabel(
        frame,
        text="Paper chỉ khác tài khoản thật ở VỐN ẢO. Phí, spread và cách tính đều mô phỏng như thật.",
        font=("Arial", 11, "italic"),
        text_color="#90A4AE",
        wraplength=460,
        justify="left",
    ).pack(anchor="w", pady=(0, 10))

    ctk.CTkLabel(frame, text="Vốn ảo ban đầu (VND):").pack(anchor="w")
    e_bal = ctk.CTkEntry(frame, justify="center", width=240)
    e_bal.insert(0, money_input_to_display(getattr(config, "PAPER_INITIAL_BALANCE", 100000000.0), "VND"))
    e_bal.pack(anchor="w", pady=(2, 8))
    ctk.CTkLabel(
        frame,
        text=money_setting_hint(),
        font=("Arial", 11, "italic"),
        text_color="#FFD54F",
        wraplength=460,
        justify="left",
    ).pack(anchor="w", pady=(0, 8))

    lbl_msg = ctk.CTkLabel(frame, text="", font=("Roboto", 11), text_color="#B0BEC5", wraplength=460, justify="left")

    def _save_balance():
        try:
            bal = money_input_from_display(e_bal.get(), "VND")
        except ValueError:
            lbl_msg.configure(text="Vốn ảo phải là số.", text_color="#E57373")
            return
        try:
            env_utils.update_env({"PAPER_INITIAL_BALANCE": str(bal)})
        except Exception as exc:  # noqa: BLE001
            lbl_msg.configure(text=f"Không ghi được .env: {exc}", text_color="#E57373")
            return
        config.PAPER_INITIAL_BALANCE = bal
        lbl_msg.configure(text=f"Đã lưu vốn ảo: {format_vnd_full(bal)} ({money_unit_note()}).", text_color="#81C784")

    def _reset_paper():
        try:
            bal = money_input_from_display(e_bal.get(), "VND") or None
        except ValueError:
            bal = None
        try:
            app.connector.reset_paper(bal)
        except Exception as exc:  # noqa: BLE001
            lbl_msg.configure(text=f"Reset thất bại: {exc}", text_color="#E57373")
            return
        if hasattr(app, "log_message"):
            app.log_message("✅ Đã reset tài khoản Paper về vốn ảo ban đầu.", target="bot")
        lbl_msg.configure(text="Đã reset tài khoản Paper về vốn ảo ban đầu.", text_color="#81C784")

    f_btn = ctk.CTkFrame(frame, fg_color="transparent")
    f_btn.pack(anchor="w", pady=(4, 6))
    ctk.CTkButton(f_btn, text="Lưu vốn ảo", width=130, fg_color="#2E7D32", command=_save_balance).pack(side="left")
    ctk.CTkButton(f_btn, text="Reset Paper", width=130, fg_color="#0277BD", command=_reset_paper).pack(side="left", padx=(8, 0))
    lbl_msg.pack(anchor="w", pady=(4, 2))


def open_preset_config_popup(app):

    presets = list(config.PRESETS.keys()) or ["SCALPING"]
    p_name = getattr(config, "DEFAULT_PRESET", presets[0])
    if p_name not in config.PRESETS:
        p_name = presets[0]
    data = config.PRESETS.get(p_name, {})
    top = ctk.CTkToplevel(app)
    top.title(f"Preset: {p_name}")
    _fit_popup(top, 760, 800, 720, 520)
    _bring_popup_to_front(top)

    # 2 tab: 'Cố định' (rule lệnh, 1 preset duy nhất) + 'Paper' (vốn ảo)
    tabs = ctk.CTkTabview(top)
    tabs.pack(fill="both", expand=True, padx=8, pady=(8, 4))
    tab_fixed = tabs.add("Cố định")
    tab_paper = tabs.add("Paper")

    body = _speed_up_scroll(ctk.CTkScrollableFrame(tab_fixed, fg_color="transparent"))
    body.pack(fill="both", expand=True, padx=4, pady=4)
    acc = app.connector.get_account_info()
    eq = acc["equity"] if acc else 1000.0
    cp = 1000.0
    try:
        _tk = app.connector.get_tick(app.cbo_symbol.get())
        if _tk is not None:
            cp = float(getattr(_tk, "ask", 0) or getattr(_tk, "last", 0) or 1000.0)
    except Exception:
        cp = 1000.0

    build_paper_config_tab(app, tab_paper)

    ctk.CTkLabel(body, text=f"PRESET: {p_name}", font=FONT_BOLD).pack(pady=10)
    _add_popup_hint(
        body,
        "- Preset này dùng cho lệnh manual theo preset đang chọn.\n"
        "- Manual input ngoài panel luôn ưu tiên hơn preset.\n"
        "- Preset chỉ định rule riêng cho SL và TP manual; không đổi BOT/Telegram.\n"
        "- Group đọc timeframe của mã đang chọn: VN30F G1=M15; CKCS G1=H1.",
        padx=20,
        pady=(0, 10),
        wraplength=680,
    )
    ctk.CTkLabel(body, text="Risk Per Trade (%):").pack()
    e_risk = ctk.CTkEntry(body, justify="center")
    e_risk.insert(0, str(data.get("RISK_PERCENT", 0.3)))
    e_risk.pack()
    lbl_h_risk = ctk.CTkLabel(
        body, text="~ -0", text_color="#CFD8DC", font=("Roboto", 11)
    )
    lbl_h_risk.pack(pady=(0, 5))
    ctk.CTkLabel(body, text="Stop Loss (%):").pack()
    e_sl = ctk.CTkEntry(body, justify="center")
    e_sl.insert(0, str(data.get("SL_PERCENT", 0.5)))
    e_sl.pack()
    lbl_h_sl = ctk.CTkLabel(
        body, text="~ Price: 0.00", text_color="#CFD8DC", font=("Roboto", 11),
        wraplength=460,
    )
    lbl_h_sl.pack(pady=(0, 5))
    ctk.CTkLabel(
        body,
        text="RR chính khi chọn RR · dự phòng khi mục tiêu kỹ thuật lỗi:",
    ).pack()
    e_tp = ctk.CTkEntry(body, justify="center")
    e_tp.insert(0, str(data.get("TP_RR_RATIO", 2.0)))
    e_tp.pack()
    lbl_h_tp = ctk.CTkLabel(
        body, text="~ +0", text_color="#CFD8DC", font=("Roboto", 11)
    )
    lbl_h_tp.pack(pady=(0, 10))

    # [NEW] Thêm Checkbox Strict Risk (Tính phí Spread/Comm)
    var_strict = ctk.BooleanVar(value=data.get("STRICT_RISK", False))
    chk_strict = ctk.CTkCheckBox(
        body,
        text="Strict Risk: lot đã trừ spread/comm",
        variable=var_strict,
        text_color="#FF6E66",
        font=("Roboto", 12, "bold"),
    )
    chk_strict.pack(pady=(5, 10))

    # --- Manual SL/TP rules ---
    sl_mode_default = str(data.get("MANUAL_SL_MODE") or ("SWING" if data.get("USE_SWING_SL", False) else "PERCENT")).upper()
    tp_mode_default = str(data.get("MANUAL_TP_MODE") or ("SWING" if data.get("USE_SWING_TP", False) else "RR")).upper()
    _manual_mode_display = {
        "PERCENT": "Percent",
        "SANDBOX": "Swing Retest",
        "RR": "RR",
        "OFF": "OFF",
        "NO_TP": "OFF",
        "SWING": "Swing Retest",
        "SWING_REJECTION": "Swing Retest",
        "SWING_RETEST": "Swing Retest",
        "SWING_STRUCTURE": "Swing Struct",
        "SWING_STRUCT": "Swing Struct",
        "FIB": "FIB",
        "PULLBACK": "Pullback",
        "PULLBACK_ZONE": "Pullback",
    }
    var_manual_sl_mode = tk.StringVar(value=_manual_mode_display.get(sl_mode_default, "Percent"))
    var_manual_tp_mode = tk.StringVar(value=_manual_mode_display.get(tp_mode_default, "RR"))
    var_swing_sl = ctk.BooleanVar(value="SWING" in sl_mode_default)
    var_manual_sl_group = tk.StringVar(value=data.get("MANUAL_SL_GROUP", data.get("MANUAL_SWING_SL_GROUP", "G2")))
    var_manual_sl_buffer = tk.StringVar(value=str(data.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2))))
    var_swing_tp = ctk.BooleanVar(value="SWING" in tp_mode_default)
    var_manual_tp_group = tk.StringVar(value=data.get("MANUAL_TP_GROUP", data.get("MANUAL_SWING_TP_GROUP", data.get("MANUAL_SWING_SL_GROUP", "G2"))))

    f_sl_rule = ctk.CTkFrame(
        body,
        fg_color="#142124",
        corner_radius=8,
        border_width=1,
        border_color="#37565C",
    )
    f_sl_rule.pack(fill="x", padx=20, pady=(0, 10))
    ctk.CTkLabel(
        f_sl_rule,
        text="PREVIEW LỆNH TAY — STOP LOSS",
        font=("Roboto", 13, "bold"),
        text_color="#FFB3AD",
    ).pack(anchor="w", padx=14, pady=(10, 4))
    f_manual_sl_group = ctk.CTkFrame(f_sl_rule, fg_color="transparent")
    f_manual_sl_group.pack(fill="x", padx=14, pady=(0, 12))
    ctk.CTkLabel(
        f_manual_sl_group,
        text="Mode:",
        width=82,
        anchor="w",
        text_color="#D9EEF2",
    ).pack(side="left")
    ctk.CTkOptionMenu(
        f_manual_sl_group,
        values=["Percent", "Swing Retest", "Swing Struct", "FIB", "Pullback"],
        variable=var_manual_sl_mode,
        width=140,
        command=lambda v: var_swing_sl.set("Swing" in v),
    ).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        f_manual_sl_group,
        text="Group:",
        width=54,
        anchor="w",
        text_color="#D9EEF2",
    ).pack(side="left")
    ctk.CTkOptionMenu(
        f_manual_sl_group,
        values=["G0", "G1", "G2", "G3", "DYNAMIC"],
        variable=var_manual_sl_group,
        width=90,
    ).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        f_manual_sl_group,
        text="Buffer:",
        width=58,
        anchor="w",
        text_color="#D9EEF2",
    ).pack(side="left")
    ctk.CTkEntry(f_manual_sl_group, textvariable=var_manual_sl_buffer, width=58, justify="center").pack(side="left")
    ctk.CTkLabel(
        f_sl_rule,
        text=(
            "Preset lệnh tay dùng Group/Buffer riêng tại đây; không đọc hoặc ghi đè SL của BOT."
        ),
        font=("Roboto", 11, "italic"),
        text_color="#90CAF9",
        wraplength=820,
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 9))

    f_tp_rule = ctk.CTkFrame(
        body,
        fg_color="#142124",
        corner_radius=8,
        border_width=1,
        border_color="#37565C",
    )
    f_tp_rule.pack(fill="x", padx=20, pady=(0, 12))
    ctk.CTkLabel(
        f_tp_rule,
        text="PREVIEW LỆNH TAY — TAKE PROFIT",
        font=("Roboto", 13, "bold"),
        text_color="#9AFFC4",
    ).pack(anchor="w", padx=14, pady=(10, 4))
    f_manual_tp_group = ctk.CTkFrame(f_tp_rule, fg_color="transparent")
    f_manual_tp_group.pack(fill="x", padx=14, pady=(0, 12))
    ctk.CTkLabel(
        f_manual_tp_group,
        text="Mode:",
        width=82,
        anchor="w",
        text_color="#D9EEF2",
    ).pack(side="left")
    ctk.CTkOptionMenu(
        f_manual_tp_group,
        values=["OFF", "RR", "Swing Retest", "Swing Struct", "FIB", "Pullback"],
        variable=var_manual_tp_mode,
        width=140,
        command=lambda v: var_swing_tp.set("Swing" in v),
    ).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        f_manual_tp_group,
        text="Group:",
        width=54,
        anchor="w",
        text_color="#D9EEF2",
    ).pack(side="left")
    ctk.CTkOptionMenu(
        f_manual_tp_group,
        values=["G0", "G1", "G2", "G3", "DYNAMIC"],
        variable=var_manual_tp_group,
        width=90,
    ).pack(side="left", padx=(0, 8))
    def live(*args):
        try:
            r, s, t = (
                float(e_risk.get() or 0),
                float(e_sl.get() or 0),
                float(e_tp.get() or 0),
            )
            risk_usd = eq * (r / 100)
            lbl_h_risk.configure(
                text=f"(~ Mất {format_vnd_full(risk_usd)} nếu dính SL)", text_color="#EF5350"
            )
            # [SANDBOX-FETCH] Hint SL theo đúng mode đang chọn (không chỉ Percent)
            mode_disp = str(var_manual_sl_mode.get() or "Percent")
            if mode_disp == "Percent":
                lbl_h_sl.configure(
                    text=f"(~ Đặt SL quanh {app._fmt_price(cp * (1 - s / 100), app.cbo_symbol.get())} cho BUY)",
                    text_color="#CFD8DC",
                )
            else:
                grp = str(var_manual_sl_group.get() or "G2")
                ctx = {}
                _sym = ""
                try:
                    _sym = str(app.cbo_symbol.get() or "").strip()
                    _ctxs = getattr(app, "latest_market_context", {}) or {}
                    ctx = _ctxs.get(_sym) or _ctxs.get(_sym.upper()) or {}
                except Exception:
                    ctx = {}
                effective_mult = None
                if mode_disp == "SL Sandbox" and _sym:
                    try:
                        brain = app.trade_mgr._get_brain_settings(_sym) or {}
                        risk_tsl = brain.get("risk_tsl", {}) or {}
                        grp = app._sandbox_sl_group_for_symbol(
                            _sym,
                            ctx,
                            risk_tsl.get("base_sl", grp),
                        )
                        effective_mult = float(
                            risk_tsl.get(
                                "sl_atr_multiplier",
                                getattr(config, "sl_atr_multiplier", 0.2),
                            )
                            or 0.2
                        )
                    except Exception:
                        effective_mult = None
                if "DYNAMIC" in grp:
                    grp = "G1" if str(ctx.get("market_mode", "ANY")).upper() in ("TREND", "BREAKOUT") else "G2"
                atr_v = float(ctx.get(f"atr_{grp}", 0) or 0)
                sw_lo = float(ctx.get(f"swing_low_{grp}", 0) or 0)
                if atr_v > 0 and sw_lo > 0:
                    try:
                        mult = (
                            effective_mult
                            if effective_mult is not None
                            else float(var_manual_sl_buffer.get() or 0.2)
                        )
                    except Exception:
                        mult = 0.2
                    _sl_est = sw_lo - atr_v * mult
                    if cp > 0 and _sl_est >= cp:
                        # Swing nằm trên giá hiện tại -> BUY sẽ fallback Percent (wrong-side guard)
                        lbl_h_sl.configure(
                            text=f"(⚠ {mode_disp} {grp}: SL ~ {_sl_est:.2f} ≥ giá {cp:.2f} — swing sai phía, lệnh BUY sẽ dùng Percent {s:g}%)",
                            text_color="#EF5350",
                        )
                    else:
                        lbl_h_sl.configure(
                            text=f"({mode_disp} {grp}, buffer {mult:g} ATR: SL ~ {_sl_est:.2f} cho BUY)",
                            text_color="#FFB74D",
                        )
                else:
                    lbl_h_sl.configure(
                        text=f"({mode_disp}: SL theo swing±ATR — chưa có data, tạm fallback {s:g}%)",
                        text_color="#FFB74D",
                    )
            tp_mode_now = str(var_manual_tp_mode.get() or "RR")
            rr_role = "TP chính" if tp_mode_now == "RR" else "mức dự phòng"
            lbl_h_tp.configure(
                text=f"(~ {format_vnd_full(risk_usd * t)} tại {t:g}R · {rr_role})",
                text_color="#66BB6A",
            )
        except ValueError:
            pass
    e_risk.bind("<KeyRelease>", live)
    e_sl.bind("<KeyRelease>", live)
    e_tp.bind("<KeyRelease>", live)
    var_manual_sl_mode.trace_add("write", lambda *a: live())
    var_manual_sl_group.trace_add("write", lambda *a: live())
    var_manual_tp_mode.trace_add("write", lambda *a: live())
    live()

    def save_preset():
        sl_raw = str(var_manual_sl_mode.get() or "Percent").upper()
        tp_raw = str(var_manual_tp_mode.get() or "RR").upper()
        sl_mode = (
            "SWING_STRUCTURE" if "STRUCT" in sl_raw
            else "SWING_REJECTION" if "RETEST" in sl_raw
            else "SANDBOX" if "SANDBOX" in sl_raw
            else "FIB" if "FIB" in sl_raw
            else "PULLBACK" if "PULL" in sl_raw
            else "PERCENT"
        )
        tp_mode = (
            "OFF" if "OFF" in tp_raw
            else "SWING_STRUCTURE" if "STRUCT" in tp_raw
            else "SWING_REJECTION" if "RETEST" in tp_raw
            else "FIB" if "FIB" in tp_raw
            else "PULLBACK" if "PULL" in tp_raw
            else "RR"
        )
        config.PRESETS[p_name].update(
            {
                "RISK_PERCENT": float(e_risk.get()),
                "SL_PERCENT": float(e_sl.get()),
                "TP_RR_RATIO": float(e_tp.get()),
                "STRICT_RISK": var_strict.get(),
                "MANUAL_SL_MODE": sl_mode,
                "MANUAL_TP_MODE": tp_mode,
                "USE_SWING_SL": sl_mode in ("SWING_REJECTION", "SWING_STRUCTURE"),
                "USE_SWING_TP": tp_mode in ("SWING_REJECTION", "SWING_STRUCTURE"),
                "MANUAL_SL_GROUP": var_manual_sl_group.get() or "G2",
                "MANUAL_TP_GROUP": var_manual_tp_group.get() or "G2",
                "MANUAL_SWING_SL_GROUP": var_manual_sl_group.get() or "G2",
                "MANUAL_SWING_TP_GROUP": var_manual_tp_group.get() or "G2",
                "MANUAL_SWING_SL_ATR_MULT": float(var_manual_sl_buffer.get() or 0.2),
            }
        )
        app.save_settings()
        if hasattr(app, "var_preview_sl_group"):
            display = var_manual_sl_group.get() or "G2"
            app.var_preview_sl_group.set(display)
        if hasattr(app, "var_preview_tp_group"):
            display = var_manual_tp_group.get() or "G2"
            app.var_preview_tp_group.set(display)
        if hasattr(app, "var_preview_sl_mode"):
            app.var_preview_sl_mode.set(_manual_mode_display.get(sl_mode, "Percent"))
        if hasattr(app, "var_preview_tp_mode"):
            app.var_preview_tp_mode.set(_manual_mode_display.get(tp_mode, "RR"))
        if hasattr(app, "update_manual_entry_exit_buttons_ui"):
            app.update_manual_entry_exit_buttons_ui()
        app.refresh_manual_preview_tab()
        top.destroy()
    ctk.CTkButton(top, text="LƯU PRESET", command=save_preset, fg_color=COL_GREEN).pack(
        pady=20, fill="x", padx=30
    )

# ==============================================================================

# 3. POPUP TSL (CÓ BE SOFT/SMART, PNL LEVELS +, STEP R)

# ==============================================================================


def open_tsl_popup(app, override_symbol=None):

    top = ctk.CTkToplevel(app)
    title = "TSL Logic Configuration"
    if override_symbol:
        title += f" - CẤU HÌNH CON: {override_symbol}"
    top.title(title)
    _fit_popup(top, 980, 780, 860, 600)
    _bring_popup_to_front(top)
    top.resizable(True, True)  # Khôi phục tính năng co giãn/phóng to
    if override_symbol:
        top.grab_set()  # Modal: Không cho chạm vào UI mẹ khi đang chỉnh UI con
    tsl_cfg = config.TSL_CONFIG.copy()
    tsl_logic_mode = getattr(config, "TSL_LOGIC_MODE", "STATIC")
    if override_symbol:
        from core.storage_manager import get_brain_settings_for_symbol
        brain = get_brain_settings_for_symbol(override_symbol)
        if "TSL_CONFIG" in brain:
            tsl_cfg.update(brain["TSL_CONFIG"])
        tsl_logic_mode = brain.get("TSL_LOGIC_MODE", tsl_logic_mode)

    # [FIX V4.4] CHIA LÀM 2 TAB GỌN GÀNG THEO YÊU CẦU CỦA BOSS
    tabview = ctk.CTkTabview(top, height=620)
    try:
        tabview.configure(
            fg_color="#181818",
            border_width=1,
            border_color="#7B1FA2",
            segmented_button_fg_color="#2A2A2A",
            segmented_button_selected_color="#1f538d",
            segmented_button_selected_hover_color="#14375e",
            segmented_button_unselected_color="#3A3A3A",
            segmented_button_unselected_hover_color="#4A4A4A",
            text_color="#F3E5F5",
        )
    except Exception:
        pass
    tabview.pack(fill="both", expand=True, padx=10, pady=5)
    tab_basic_root = tabview.add("Basic (BE, PNL, STEP)")
    tab_adv_root = tabview.add("Advanced (CASH, PSAR)")
    tab_basic = _speed_up_scroll(ctk.CTkScrollableFrame(tab_basic_root, fg_color="transparent"))
    tab_basic.pack(fill="both", expand=True, padx=4, pady=4)
    tab_adv = _speed_up_scroll(ctk.CTkScrollableFrame(tab_adv_root, fg_color="transparent"))
    tab_adv.pack(fill="both", expand=True, padx=4, pady=4)
    if not override_symbol:
        tab_ow = tabview.add("Overwrite (Mẹ-Con)")
        # OVERRIDE OVERVIEW
        f_overview = ctk.CTkFrame(tab_ow, fg_color="#2b2b2b", corner_radius=8)
        f_overview.pack(fill="x", padx=15, pady=8)
        ctk.CTkLabel(
            f_overview,
            text="OVERRIDE OVERVIEW",
            font=("Roboto", 13, "bold"),
            text_color="#00B8D4",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 6))
        ctk.CTkLabel(
            f_overview, text="Symbol", font=("Roboto", 11, "bold"), text_color="#D7DCE2"
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(
            f_overview, text="Status", font=("Roboto", 11, "bold"), text_color="#D7DCE2"
        ).grid(row=1, column=1, sticky="w", padx=12, pady=(0, 4))
        f_overview_rows = ctk.CTkFrame(f_overview, fg_color="transparent")
        f_overview_rows.grid(
            row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 8)
        )

        def load_tsl_for_sym(sym):
            open_tsl_popup(app, override_symbol=sym)

        def reset_tsl_for_sym(sym):
            from core.storage_manager import (
                load_symbol_overrides,
                save_symbol_overrides,
            )
            overrides = load_symbol_overrides()
            if sym in overrides and "tsl" in overrides[sym]:
                del overrides[sym]["tsl"]
                save_symbol_overrides(overrides)
                if hasattr(app, "log_message"):
                    app.log_message(
                        f"✅ Đã reset TSL Override cho {sym}.", target="bot"
                    )
                refresh_tsl_override_overview()

    def sec(parent, t):
        color = "#03A9F4"
        upper_t = str(t).upper()
        if "BREAK" in upper_t or "BE_" in upper_t:
            color = "#00E676"
        elif "PNL" in upper_t:
            color = "#FFD600"
        elif "STEP" in upper_t:
            color = "#29B6F6"
        elif "CASH" in upper_t:
            color = "#AB47BC"
        elif "PSAR" in upper_t or "MFE" in upper_t or "HARD" in upper_t:
            color = "#FFB300"
        frame = ctk.CTkFrame(parent, fg_color="#202020", corner_radius=8, border_width=1, border_color=color)
        ctk.CTkLabel(
            frame,
            text=t,
            font=("Roboto", 12, "bold"),
            text_color=color,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 6))
        return frame

    def compact_entry(entry):
        try:
            entry.configure(fg_color="#2F3336", border_color="#56616A", justify="center")
        except Exception:
            pass
        return entry

    def compact_menu(menu):
        try:
            menu.configure(
                fg_color="#1f538d",
                button_color="#14375e",
                button_hover_color="#0D47A1",
                text_color="#FFFFFF",
            )
        except Exception:
            pass
        return menu

    def field(parent, row, col, label, widget, label_color="#FFFFFF"):
        parent.grid_columnconfigure(col * 2, weight=0)
        parent.grid_columnconfigure(col * 2 + 1, weight=1)
        ctk.CTkLabel(
            parent,
            text=label,
            text_color=label_color,
            anchor="w",
        ).grid(row=row, column=col * 2, sticky="w", padx=(12, 6), pady=6)
        widget.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 16), pady=6)
        return widget

    def hint_label(parent, text, wrap=820):
        ctk.CTkLabel(
            parent,
            text=text,
            text_color="#B0BEC5",
            font=("Arial", 11, "italic"),
            wraplength=wrap,
            justify="left",
            anchor="w",
        ).grid(
            row=99, column=0, columnspan=8, sticky="ew", padx=12, pady=(4, 10)
        )

    # ================= TAB 1: BASIC =================
    _add_popup_hint(
        tab_basic,
        "- BE_SL là loss-side guard: âm tới trigger R thì kéo SL sát giá theo step R.\n"
        "- PNL Levels khóa lãi theo % win; STEP R bám theo từng bậc R.\n"
        "- Chỉ tactic được bật ở lệnh/Bot TSL mới dùng các tham số này.",
        padx=15,
        pady=(10, 5),
        wraplength=820,
    )
    f_be = sec(tab_basic, "1. BREAK-EVEN SL (BE_SL)")
    f_be.pack(fill="x", padx=15, pady=(8, 8))
    f_be_r1 = ctk.CTkFrame(f_be, fg_color="transparent")
    f_be_r1.pack(fill="x")
    f_be_r2 = ctk.CTkFrame(f_be, fg_color="transparent")
    f_be_r2.pack(fill="x", pady=(5, 0))
    f_be_r3 = ctk.CTkFrame(f_be, fg_color="transparent")
    f_be_r3.pack(fill="x", pady=(5, 0))
    ctk.CTkLabel(
        f_be_r1, text="BE Loss Guard", text_color="#00B8D4", font=("Roboto", 12, "bold")
    ).pack(side="left", padx=5)
    cbo_be_sl_unit = ctk.CTkOptionMenu(
        f_be_r1, values=["R", "VND", "PERCENT", "POINT"], width=90
    )
    cbo_be_sl_unit.set(unit_to_display(tsl_cfg.get("BE_SL_LOSS_UNIT", "R")))
    cbo_be_sl_unit.pack(side="left", padx=5)
    ctk.CTkLabel(f_be_r1, text="Loss Trig:").pack(side="left", padx=(8, 2))
    e_be_sl_loss_trigger = ctk.CTkEntry(f_be_r1, width=55)
    e_be_sl_loss_trigger.insert(0, money_input_to_display(tsl_cfg.get("BE_SL_LOSS_TRIGGER", 0.5), tsl_cfg.get("BE_SL_LOSS_UNIT", "R")))
    e_be_sl_loss_trigger.pack(side="left", padx=(5, 10))
    ctk.CTkLabel(f_be_r1, text="Step:").pack(side="left", padx=(8, 2))
    e_be_sl_loss_step = ctk.CTkEntry(f_be_r1, width=55)
    e_be_sl_loss_step.insert(0, money_input_to_display(tsl_cfg.get("BE_SL_LOSS_STEP", 0.15), tsl_cfg.get("BE_SL_LOSS_UNIT", "R")))
    e_be_sl_loss_step.pack(side="left", padx=(5, 10))
    ctk.CTkLabel(f_be_r1, text="Guard Buf:").pack(side="left", padx=(8, 2))
    e_be_sl_guard_buffer = ctk.CTkEntry(f_be_r1, width=55)
    e_be_sl_guard_buffer.insert(0, money_input_to_display(tsl_cfg.get("BE_SL_GUARD_BUFFER", 0.075), tsl_cfg.get("BE_SL_LOSS_UNIT", "R")))
    e_be_sl_guard_buffer.pack(side="left", padx=(5, 10))
    ctk.CTkLabel(f_be_r2, text="Re-entry Lock(s):").pack(side="left", padx=(8, 2))
    e_be_sl_reentry_lock = ctk.CTkEntry(f_be_r2, width=80)
    e_be_sl_reentry_lock.insert(0, str(tsl_cfg.get("BE_SL_REENTRY_LOCK_SEC", 1800)))
    e_be_sl_reentry_lock.pack(side="left", padx=(5, 10))
    ctk.CTkLabel(
        f_be_r3,
        text="RECOVERY_GUARD: âm tới Loss Trig thì arm; hồi lên đủ Step thì đặt virtual guard dưới mức hồi tốt nhất theo Guard Buf. Hồi tiếp thì guard nâng lên; thủng guard thì bot close và khóa vào lại.",
        text_color="#B0BEC5",
        font=("Arial", 11, "italic"),
        wraplength=620,
    ).pack(side="left", padx=5)
    try:
        for row_frame in (f_be_r1, f_be_r2, f_be_r3):
            row_frame.pack_forget()
            for child in row_frame.winfo_children():
                child.pack_forget()
        f_be_r1.pack(fill="x", padx=8)
        f_be_r2.pack(fill="x", padx=8)
        f_be_r3.pack(fill="x", padx=12, pady=(0, 10))
        compact_menu(cbo_be_sl_unit)
        compact_entry(e_be_sl_loss_trigger)
        compact_entry(e_be_sl_loss_step)
        compact_entry(e_be_sl_guard_buffer)
        compact_entry(e_be_sl_reentry_lock)
        field(f_be_r1, 0, 0, "BE Loss Guard", cbo_be_sl_unit, "#00B8D4")
        field(f_be_r1, 0, 1, "Loss Trig", e_be_sl_loss_trigger)
        field(f_be_r1, 0, 2, "Step", e_be_sl_loss_step)
        field(f_be_r1, 0, 3, "Guard Buf", e_be_sl_guard_buffer)
        field(f_be_r2, 0, 0, "Re-entry Lock(s)", e_be_sl_reentry_lock)
        ctk.CTkLabel(
            f_be_r3,
            text="RECOVERY_GUARD: âm tới Loss Trig thì arm; hồi lên đủ Step thì đặt virtual guard dưới mức hồi tốt nhất theo Guard Buf. Hồi tiếp thì guard nâng lên; thủng guard thì bot close và khóa vào lại.",
            text_color="#B0BEC5",
            font=("Arial", 11, "italic"),
            wraplength=820,
            justify="left",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
    except Exception:
        pass
    f_pnl = sec(tab_basic, "2. KHÓA LÃI PNL (LEVELS)")
    f_pnl.pack(fill="x", padx=15, pady=(0, 8))
    scroll_pnl = _speed_up_scroll(ctk.CTkScrollableFrame(f_pnl, height=125, fg_color="#181818"))
    scroll_pnl.pack(fill="x", padx=10, pady=(0, 6))
    scroll_pnl.grid_columnconfigure(0, weight=1)
    pnl_entries = []

    def add_p(v1=0.0, v2=0.0):
        r = ctk.CTkFrame(scroll_pnl, fg_color="transparent")
        r.pack(fill="x", pady=2)
        e1, e2 = compact_entry(ctk.CTkEntry(r, width=70)), compact_entry(ctk.CTkEntry(r, width=70))
        e1.insert(0, str(v1))
        e1.pack(side="left", padx=(6, 8), pady=3)
        ctk.CTkLabel(r, text="% Win -> Lock %", width=130, anchor="w").pack(side="left", padx=5)
        e2.insert(0, str(v2))
        e2.pack(side="left", padx=(8, 6), pady=3)
        pnl_entries.append((r, e1, e2))
    for lvl in tsl_cfg.get("PNL_LEVELS", []):
        add_p(lvl[0], lvl[1])
    f_pbtns = ctk.CTkFrame(f_pnl, fg_color="transparent")
    f_pbtns.pack(fill="x", padx=10, pady=(0, 10))
    ctk.CTkButton(f_pbtns, text="+", width=44, height=28, command=lambda: add_p(0.0, 0.0)).pack(
        side="left", padx=5
    )
    ctk.CTkButton(
        f_pbtns,
        text="-",
        width=40,
        height=28,
        command=lambda: pnl_entries.pop()[0].destroy() if pnl_entries else None,
    ).pack(side="left", padx=5)
    f_step = sec(tab_basic, "3. STEP R (TRAIL)")
    f_step.pack(fill="x", padx=15, pady=(0, 8))
    f_step_row = ctk.CTkFrame(f_step, fg_color="transparent")
    f_step_row.pack(fill="x", padx=8, pady=(0, 10))
    e_sz = compact_entry(ctk.CTkEntry(f_step_row, width=70))
    e_sz.insert(0, str(tsl_cfg.get("STEP_R_SIZE", 1.0)))
    field(f_step_row, 0, 0, "Size(R)", e_sz)
    e_rt = compact_entry(ctk.CTkEntry(f_step_row, width=70))
    e_rt.insert(0, str(tsl_cfg.get("STEP_R_RATIO", 0.8)))
    field(f_step_row, 0, 1, "Lock(0-1)", e_rt)

    # ================= TAB 2: ADVANCED =================
    _add_popup_hint(
        tab_adv,
        "- Swing/PSAR dùng group được chọn để bám cấu trúc giá.\n"
        "- CASH trail khóa lãi theo VND/Percent/Point; One-Time chỉ khóa một lần.\n"
        "- ANTI CASH giữ tên cũ nhưng có thêm MAE/MFE theo từng ticket.\n"
        "- MAE = âm sâu nhất của ticket; MFE = lời cao nhất của ticket.\n"
        "- Hard Stop là cầu dao lỗ; MFE Giveback chống trả lại lợi nhuận.",
        padx=15,
        pady=(10, 5),
        wraplength=820,
    )
    f_swing_man = sec(tab_adv, "4. MANUAL SWING (Bám nến)")
    f_swing_man.pack(fill="x", padx=15)
    cbo_swing_grp = ctk.CTkOptionMenu(
        f_swing_man, values=["G0", "G1", "G2", "G3", "DYNAMIC-G1/G2"], width=100
    )
    cbo_swing_grp.set(tsl_cfg.get("SWING_GROUP", "G2"))
    cbo_swing_grp.pack(side="right")
    ctk.CTkLabel(f_swing_man, text="Group Theo Dõi:").pack(side="left")
    f_swing_logic = ctk.CTkFrame(f_swing_man, fg_color="transparent")
    f_swing_logic.pack(fill="x", pady=(8, 4))
    ctk.CTkLabel(f_swing_logic, text="Swing TSL Logic Mode:").pack(side="left", padx=5)
    cbo_tsl_logic_mode = ctk.CTkOptionMenu(
        f_swing_logic,
        values=["STATIC", "DYNAMIC", "AGGRESSIVE"],
        width=140,
    )
    cbo_tsl_logic_mode.set(tsl_logic_mode)
    cbo_tsl_logic_mode.pack(side="left", padx=8)
    ctk.CTkLabel(
        f_swing_man,
        text=(
            "Chỉ dùng cho TSL tactic SWING sau khi lệnh đã mở. "
            "Không liên quan tới SL ban đầu của bot/manual theo Swing + ATR buffer. "
            "STATIC giữ mốc swing, DYNAMIC/AGGRESSIVE bám đuôi chủ động hơn."
        ),
        text_color="#B0BEC5",
        font=("Arial", 11, "italic"),
        wraplength=620,
        justify="left",
    ).pack(anchor="w", padx=5, pady=(0, 5))
    f_cash = sec(tab_adv, "5. BE HARD CASH (Thang cuốn VND/Point/%/R)")
    f_cash.pack(fill="x", padx=15, pady=(0, 8))
    f_cash_r1 = ctk.CTkFrame(f_cash, fg_color="transparent")
    f_cash_r1.pack(fill="x")
    f_cash_r2 = ctk.CTkFrame(f_cash, fg_color="transparent")
    f_cash_r2.pack(fill="x", pady=(5, 0))
    f_cash_r3 = ctk.CTkFrame(f_cash, fg_color="transparent")
    f_cash_r3.pack(fill="x", pady=(5, 0))
    cbo_cash_type = ctk.CTkOptionMenu(
        f_cash_r1, values=["VND", "PERCENT", "POINT", "R"], width=80
    )
    cbo_cash_type.set(unit_to_display(tsl_cfg.get("BE_CASH_TYPE", "USD")))
    cbo_cash_type.pack(side="left", padx=5)
    lbl_cash_trig = ctk.CTkLabel(f_cash_r1, text="Trig:")
    lbl_cash_trig.pack(side="left", padx=2)
    e_cash_trig = ctk.CTkEntry(f_cash_r1, width=50)
    e_cash_trig.insert(0, money_input_to_display(tsl_cfg.get("BE_TRIGGER", 10.0), tsl_cfg.get("BE_CASH_TYPE", "USD")))
    e_cash_trig.pack(side="left", padx=2)
    lbl_cash_step = ctk.CTkLabel(f_cash_r1, text="Step:")
    lbl_cash_step.pack(side="left", padx=2)
    e_cash_val = ctk.CTkEntry(f_cash_r1, width=50)
    e_cash_val.insert(0, money_input_to_display(tsl_cfg.get("BE_VALUE", 20.0), tsl_cfg.get("BE_CASH_TYPE", "USD")))
    e_cash_val.pack(side="left", padx=2)
    cbo_cash_strat = ctk.CTkOptionMenu(
        f_cash_r1,
        values=["TRAILING (Gap)", "LOCK (Tight)", "SOFT LOCK (Buffer)"],
        width=145,
    )
    cbo_cash_strat.set(tsl_cfg.get("BE_CASH_STRAT", "TRAILING (Gap)"))
    cbo_cash_strat.pack(side="left", padx=5)
    var_cash_fee_protect = ctk.BooleanVar(
        value=tsl_cfg.get("BE_CASH_FEE_PROTECT", True)
    )
    chk_cash_fee = ctk.CTkCheckBox(
        f_cash_r2, text="Fee Protect", variable=var_cash_fee_protect, width=60
    )
    chk_cash_fee.pack(side="left", padx=5)
    var_be_one_time = ctk.BooleanVar(value=tsl_cfg.get("ONE_TIME_BE", False))
    chk_be_one_time = ctk.CTkCheckBox(
        f_cash_r2, text="One-Time (Chỉ khóa mốc 1)", variable=var_be_one_time, width=60
    )
    chk_be_one_time.pack(side="left", padx=5)
    lbl_cash_buffer = ctk.CTkLabel(f_cash_r3, text="Buffer:")
    lbl_cash_buffer.pack(side="left", padx=2)
    cbo_cash_buffer_type = ctk.CTkOptionMenu(
        f_cash_r3, values=["VND", "PERCENT", "POINT", "ATR", "R"], width=90
    )
    cbo_cash_buffer_type.set(unit_to_display(tsl_cfg.get("BE_CASH_SOFT_BUFFER_TYPE", "USD")))
    cbo_cash_buffer_type.pack(side="left", padx=2)
    e_cash_buffer = ctk.CTkEntry(f_cash_r3, width=55)
    e_cash_buffer.insert(0, money_input_to_display(tsl_cfg.get("BE_CASH_SOFT_BUFFER", 3.0), tsl_cfg.get("BE_CASH_SOFT_BUFFER_TYPE", "USD")))
    e_cash_buffer.pack(side="left", padx=2)
    lbl_cash_min_lock = ctk.CTkLabel(f_cash_r3, text="Min Lock:")
    lbl_cash_min_lock.pack(side="left", padx=(10, 2))
    # Min Lock không có dropdown unit -> luôn là tiền theo setting HIỂN THỊ.
    e_cash_min_lock = ctk.CTkEntry(f_cash_r3, width=55)
    e_cash_min_lock.insert(0, money_input_to_display(tsl_cfg.get("BE_CASH_MIN_LOCK", 0.0), "VND"))
    e_cash_min_lock.pack(side="left", padx=2)
    lbl_cash_help = ctk.CTkLabel(
        f_cash,
        text=(
            "SOFT LOCK: khóa = target - buffer; Min Lock là sàn khóa tối thiểu nếu kết quả còn dương. "
            "Ô setting tiền luôn nhập đủ VND thật: muốn 500.000 VND phải nhập 500000. "
            "Lựa chọn bỏ 000 chỉ áp dụng cho số hiển thị, không tác động số nhập."
        ),
        text_color="#B0BEC5",
        font=("Arial", 11, "italic"),
        wraplength=820,
    )
    lbl_cash_help.pack(anchor="w", padx=8, pady=(4, 0))
    try:
        for row_frame in (f_cash_r1, f_cash_r2, f_cash_r3):
            for child in row_frame.winfo_children():
                child.pack_forget()
            row_frame.grid_columnconfigure(9, weight=1)
        lbl_cash_help.pack_forget()
        compact_menu(cbo_cash_type)
        compact_menu(cbo_cash_strat)
        compact_menu(cbo_cash_buffer_type)
        compact_entry(e_cash_trig)
        compact_entry(e_cash_val)
        compact_entry(e_cash_buffer)
        compact_entry(e_cash_min_lock)
        ctk.CTkLabel(f_cash_r1, text="Unit:", text_color="#B0BEC5").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=5)
        cbo_cash_type.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=5)
        lbl_cash_trig.grid(row=0, column=2, sticky="w", padx=(0, 4), pady=5)
        e_cash_trig.grid(row=0, column=3, sticky="w", padx=(0, 12), pady=5)
        lbl_cash_step.grid(row=0, column=4, sticky="w", padx=(0, 4), pady=5)
        e_cash_val.grid(row=0, column=5, sticky="w", padx=(0, 12), pady=5)
        ctk.CTkLabel(f_cash_r1, text="Strategy:", text_color="#B0BEC5").grid(row=0, column=6, sticky="w", padx=(0, 4), pady=5)
        cbo_cash_strat.grid(row=0, column=7, sticky="w", padx=(0, 8), pady=5)
        chk_cash_fee.grid(row=0, column=0, sticky="w", padx=(8, 16), pady=5)
        chk_be_one_time.grid(row=0, column=1, columnspan=4, sticky="w", padx=(0, 8), pady=5)
        lbl_cash_buffer.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=5)
        cbo_cash_buffer_type.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=5)
        e_cash_buffer.grid(row=0, column=2, sticky="w", padx=(0, 16), pady=5)
        lbl_cash_min_lock.grid(row=0, column=3, sticky="w", padx=(0, 4), pady=5)
        e_cash_min_lock.grid(row=0, column=4, sticky="w", padx=(0, 8), pady=5)
        lbl_cash_help.configure(wraplength=820, justify="left", anchor="w")
        lbl_cash_help.pack(fill="x", padx=12, pady=(4, 10))
    except Exception:
        pass
    f_psar = sec(tab_adv, "6. PSAR TRAILING (Đuổi chấm)")
    f_psar.pack(fill="x", padx=15, pady=(0, 8))
    f_psar_row1 = ctk.CTkFrame(f_psar, fg_color="transparent")
    f_psar_row1.pack(fill="x", padx=8, pady=2)
    f_psar_row1.grid_columnconfigure(1, weight=1)
    cbo_psar_grp = ctk.CTkOptionMenu(
        f_psar_row1, values=["G0", "G1", "G2", "G3", "DYNAMIC-G1/G2"], width=80
    )
    cbo_psar_grp.set(tsl_cfg.get("PSAR_GROUP", "G2"))
    compact_menu(cbo_psar_grp)
    ctk.CTkLabel(f_psar_row1, text="Group:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    cbo_psar_grp.grid(row=0, column=1, sticky="w", pady=4)
    f_psar_row2 = ctk.CTkFrame(f_psar, fg_color="transparent")
    f_psar_row2.pack(fill="x", padx=8, pady=2)
    f_psar_row2.grid_columnconfigure(4, weight=1)
    e_psar_step = ctk.CTkEntry(f_psar_row2, width=60)
    e_psar_step.insert(0, str(tsl_cfg.get("PSAR_STEP", 0.02)))
    compact_entry(e_psar_step)
    e_psar_max = ctk.CTkEntry(f_psar_row2, width=60)
    e_psar_max.insert(0, str(tsl_cfg.get("PSAR_MAX", 0.2)))
    compact_entry(e_psar_max)
    ctk.CTkLabel(f_psar_row2, text="Step:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    e_psar_step.grid(row=0, column=1, sticky="w", padx=(0, 18), pady=4)
    ctk.CTkLabel(f_psar_row2, text="Max:").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=4)
    e_psar_max.grid(row=0, column=3, sticky="w", padx=(0, 18), pady=4)
    f_psar_row3 = ctk.CTkFrame(f_psar, fg_color="transparent")
    f_psar_row3.pack(fill="x", padx=8, pady=2)
    e_psar_min_rr = ctk.CTkEntry(f_psar_row3, width=60)
    e_psar_min_rr.insert(0, str(tsl_cfg.get("PSAR_MIN_RR", 0.0)))
    compact_entry(e_psar_min_rr)
    ctk.CTkLabel(f_psar_row3, text="Min RR kích hoạt:").pack(side="left")
    e_psar_min_rr.pack(side="left", padx=(0, 18))
    ctk.CTkLabel(
        f_psar,
        text="Min RR dung cash-R: 0.5R = loi 50% so tien risk ban dau cua lenh. Neu thieu risk VND thi fallback theo khoang gia SL.",
        text_color="#B0BEC5",
        font=("Arial", 11, "italic"),
        wraplength=760,
    ).pack(anchor="w", padx=8, pady=(0, 4))
    f_psar_row4 = ctk.CTkFrame(f_psar, fg_color="transparent")
    f_psar_row4.pack(fill="x", padx=8, pady=2)
    var_psar_profit_only = ctk.BooleanVar(value=tsl_cfg.get("PSAR_PROFIT_ONLY", True))
    ctk.CTkCheckBox(
        f_psar_row4,
        text="PSAR chi keo SL khi da hoa von",
        variable=var_psar_profit_only,
        width=220,
    ).pack(side="left", padx=5)
    f_psar_row5 = ctk.CTkFrame(f_psar, fg_color="transparent")
    f_psar_row5.pack(fill="x", padx=8, pady=(2, 8))
    e_psar_profit_buffer = ctk.CTkEntry(f_psar_row5, width=60)
    e_psar_profit_buffer.insert(0, str(tsl_cfg.get("PSAR_PROFIT_BUFFER_POINTS", 0)))
    compact_entry(e_psar_profit_buffer)
    ctk.CTkLabel(f_psar_row5, text="BE Buffer Points:").pack(side="left", padx=(0, 8))
    e_psar_profit_buffer.pack(side="left", padx=(0, 18))
    f_anti = sec(tab_adv, "7. ANTI CASH")
    f_anti.pack(fill="x", padx=15, pady=(0, 8))
    _add_popup_hint(
        f_anti,
        "- Hard Stop: cắt lỗ cứng theo ngưỡng đã chọn.\n"
        "- MAE Guard: chỉ cắt khi lệnh âm đủ sâu, giữ đủ lâu và MFE cao nhất vẫn thấp hơn Low MFE.\n"
        "- MFE Guard: bảo vệ lãi nổi, cắt khi lệnh trả lại quá nhiều hoặc tụt về Floor.",
        padx=8,
        pady=(4, 8),
        wraplength=820,
    )

    def anti_row(parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(anchor="w", fill="x", pady=4)
        return row

    def anti_field(parent, label, value, width=82):
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(side="left", padx=(8, 18))
        ctk.CTkLabel(group, text=label).pack(side="left", padx=(0, 6))
        entry = ctk.CTkEntry(group, width=width)
        entry.insert(0, str(value))
        entry.pack(side="left")
        return entry

    def anti_money_field(parent, label, value, unit):
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(side="left", padx=(8, 18))
        ctk.CTkLabel(group, text=label).pack(side="left", padx=(0, 6))
        entry = ctk.CTkEntry(group, width=74)
        if unit in ("%R", "PERCENT_R"):
            try:
                value = float(value or 0.0) / 100.0
            except (TypeError, ValueError):
                value = 0.0
            entry.insert(0, str(value))
        else:
            # Ô setting tiền luôn dùng VND thật; unit R/%Equity giữ nguyên số.
            entry.insert(0, money_input_to_display(value, unit))
        entry.pack(side="left", padx=(0, 6))
        unit_menu = ctk.CTkOptionMenu(group, values=["VND", "R", "%Equity"], width=92)
        unit_menu.set("R" if unit in ("%R", "PERCENT_R") else unit_to_display(unit or "USD"))
        unit_menu.pack(side="left")
        return entry, unit_menu
    ctk.CTkLabel(
        f_anti,
        text="⚠️ Ô VND luôn nhập đủ tiền thật: 500.000 VND nhập 500000. Bỏ 000 chỉ đổi số hiển thị, không đổi setting.",
        font=("Roboto", 10, "italic"), text_color="#FFB300",
    ).pack(anchor="w", padx=8, pady=(2, 0))
    f_anti_grid = ctk.CTkFrame(f_anti, fg_color="transparent")
    f_anti_grid.pack(anchor="center", pady=(4, 2))
    row_hard = anti_row(f_anti_grid)
    e_anti_usd, cbo_anti_usd_unit = anti_money_field(
        row_hard,
        "Hard Stop:",
        tsl_cfg.get("ANTI_CASH_USD", 250000.0),
        tsl_cfg.get("ANTI_CASH_HARD_STOP_UNIT", "USD"),
    )
    e_anti_time = anti_field(
        row_hard, "Time Cut (s):", tsl_cfg.get("ANTI_CASH_TIME", 60), width=86
    )
    var_anti_time_en = ctk.BooleanVar(value=tsl_cfg.get("ANTI_CASH_TIME_ENABLE", True))
    ctk.CTkCheckBox(
        row_hard, text="Dùng Time Cut", variable=var_anti_time_en, width=130
    ).pack(side="left", padx=(8, 18))
    row_mae = anti_row(f_anti_grid)
    var_anti_mae_en = ctk.BooleanVar(value=tsl_cfg.get("ANTI_CASH_MAE_ENABLE", True))
    ctk.CTkCheckBox(
        row_mae, text="MAE Guard", variable=var_anti_mae_en, width=120
    ).pack(side="left", padx=(8, 18))
    e_anti_mae_loss, cbo_anti_mae_loss_unit = anti_money_field(
        row_mae,
        "Max Loss:",
        tsl_cfg.get("ANTI_CASH_MAE_MAX_LOSS_USD", 25.0),
        tsl_cfg.get("ANTI_CASH_MAE_MAX_LOSS_UNIT", "USD"),
    )
    e_anti_mae_hold = anti_field(
        row_mae, "Hold(s):", tsl_cfg.get("ANTI_CASH_MAE_MIN_HOLD_SEC", 300), width=86
    )
    e_anti_mae_low_mfe, cbo_anti_mae_low_mfe_unit = anti_money_field(
        row_mae,
        "Low MFE:",
        tsl_cfg.get("ANTI_CASH_MAE_LOW_MFE_USD", 5.0),
        tsl_cfg.get("ANTI_CASH_MAE_LOW_MFE_UNIT", "USD"),
    )
    row_mfe = anti_row(f_anti_grid)
    var_anti_mfe_en = ctk.BooleanVar(value=tsl_cfg.get("ANTI_CASH_MFE_ENABLE", True))
    ctk.CTkCheckBox(
        row_mfe, text="MFE Guard", variable=var_anti_mfe_en, width=120
    ).pack(side="left", padx=(8, 18))
    e_anti_mfe_trig, cbo_anti_mfe_trig_unit = anti_money_field(
        row_mfe,
        "Trigger:",
        tsl_cfg.get("ANTI_CASH_MFE_TRIGGER_USD", 30.0),
        tsl_cfg.get("ANTI_CASH_MFE_TRIGGER_UNIT", "USD"),
    )
    e_anti_mfe_giveback, cbo_anti_mfe_giveback_unit = anti_money_field(
        row_mfe,
        "Giveback:",
        tsl_cfg.get("ANTI_CASH_MFE_GIVEBACK_USD", 20.0),
        tsl_cfg.get("ANTI_CASH_MFE_GIVEBACK_UNIT", "USD"),
    )
    e_anti_mfe_floor, cbo_anti_mfe_floor_unit = anti_money_field(
        row_mfe,
        "Floor:",
        tsl_cfg.get("ANTI_CASH_MFE_FLOOR_USD", 0.0),
        tsl_cfg.get("ANTI_CASH_MFE_FLOOR_UNIT", "USD"),
    )
    row_reentry = anti_row(f_anti_grid)
    e_anti_reentry = anti_field(
        row_reentry,
        "Re-entry Lock(s):",
        tsl_cfg.get("ANTI_CASH_REENTRY_LOCK_SEC", 900),
        width=86,
    )
    ctk.CTkLabel(
        row_reentry,
        text="sau khi ANTI CASH cắt cùng chiều",
        text_color="#BDBDBD",
    ).pack(side="left", padx=(0, 10))

    def save():
        try:
            output_tsl = {
                "BE_CASH_TYPE": unit_from_display(cbo_cash_type.get()),
                "BE_TRIGGER": money_input_from_display(e_cash_trig.get(), cbo_cash_type.get()),
                "BE_VALUE": money_input_from_display(e_cash_val.get(), cbo_cash_type.get()),
                "BE_CASH_STRAT": cbo_cash_strat.get(),
                "BE_CASH_FEE_PROTECT": var_cash_fee_protect.get(),
                "BE_CASH_SOFT_BUFFER_TYPE": unit_from_display(cbo_cash_buffer_type.get()),
                "BE_CASH_SOFT_BUFFER": money_input_from_display(e_cash_buffer.get(), cbo_cash_buffer_type.get()),
                "BE_CASH_MIN_LOCK": money_input_from_display(e_cash_min_lock.get(), "VND"),
                "BE_MODE": "LOSS_GUARD",
                "BE_OFFSET_RR": 0.0,
                "BE_SL_LOSS_ENABLE": True,
                "BE_SL_LOSS_UNIT": unit_from_display(cbo_be_sl_unit.get()),
                "BE_SL_LOSS_TRIGGER": money_input_from_display(e_be_sl_loss_trigger.get(), cbo_be_sl_unit.get()),
                "BE_SL_LOSS_STEP": money_input_from_display(e_be_sl_loss_step.get(), cbo_be_sl_unit.get()),
                "BE_SL_GUARD_BUFFER": money_input_from_display(e_be_sl_guard_buffer.get(), cbo_be_sl_unit.get()),
                "BE_SL_LOSS_ACTION": "RECOVERY_GUARD",
                "BE_SL_REENTRY_LOCK_SEC": int(e_be_sl_reentry_lock.get()),
                "ONE_TIME_BE": var_be_one_time.get(),
                "PNL_LEVELS": sorted(
                    [
                        [float(e1.get()), float(e2.get())]
                        for r, e1, e2 in pnl_entries
                        if e1.get()
                    ],
                    key=lambda x: x[0],
                ),
                "STEP_R_SIZE": float(e_sz.get()),
                "STEP_R_RATIO": float(e_rt.get()),
                "SWING_GROUP": cbo_swing_grp.get(),
                "PSAR_GROUP": cbo_psar_grp.get(),
                "PSAR_STEP": float(e_psar_step.get()),
                "PSAR_MAX": float(e_psar_max.get()),
                "PSAR_MIN_RR": float(e_psar_min_rr.get()),
                "PSAR_PROFIT_ONLY": var_psar_profit_only.get(),
                "PSAR_PROFIT_BUFFER_POINTS": float(e_psar_profit_buffer.get()),
                "ANTI_CASH_USD": money_input_from_display(e_anti_usd.get(), cbo_anti_usd_unit.get()),
                "ANTI_CASH_HARD_STOP_UNIT": unit_from_display(cbo_anti_usd_unit.get()),
                "ANTI_CASH_TIME": int(e_anti_time.get()),
                "ANTI_CASH_TIME_ENABLE": var_anti_time_en.get(),
                "ANTI_CASH_MAE_ENABLE": var_anti_mae_en.get(),
                "ANTI_CASH_MAE_MAX_LOSS_USD": money_input_from_display(e_anti_mae_loss.get(), cbo_anti_mae_loss_unit.get()),
                "ANTI_CASH_MAE_MAX_LOSS_UNIT": unit_from_display(cbo_anti_mae_loss_unit.get()),
                "ANTI_CASH_MAE_MIN_HOLD_SEC": int(e_anti_mae_hold.get()),
                "ANTI_CASH_MAE_LOW_MFE_USD": money_input_from_display(e_anti_mae_low_mfe.get(), cbo_anti_mae_low_mfe_unit.get()),
                "ANTI_CASH_MAE_LOW_MFE_UNIT": unit_from_display(cbo_anti_mae_low_mfe_unit.get()),
                "ANTI_CASH_MFE_ENABLE": var_anti_mfe_en.get(),
                "ANTI_CASH_MFE_TRIGGER_USD": money_input_from_display(e_anti_mfe_trig.get(), cbo_anti_mfe_trig_unit.get()),
                "ANTI_CASH_MFE_TRIGGER_UNIT": unit_from_display(cbo_anti_mfe_trig_unit.get()),
                "ANTI_CASH_MFE_GIVEBACK_USD": money_input_from_display(e_anti_mfe_giveback.get(), cbo_anti_mfe_giveback_unit.get()),
                "ANTI_CASH_MFE_GIVEBACK_UNIT": unit_from_display(cbo_anti_mfe_giveback_unit.get()),
                "ANTI_CASH_MFE_FLOOR_USD": money_input_from_display(e_anti_mfe_floor.get(), cbo_anti_mfe_floor_unit.get()),
                "ANTI_CASH_MFE_FLOOR_UNIT": unit_from_display(cbo_anti_mfe_floor_unit.get()),
                "ANTI_CASH_REENTRY_LOCK_SEC": int(e_anti_reentry.get()),
            }
            new_tsl_logic_mode = cbo_tsl_logic_mode.get()
            if override_symbol:
                from core.storage_manager import (
                    load_symbol_overrides,
                    save_symbol_overrides,
                )
                overrides = load_symbol_overrides()
                if override_symbol not in overrides:
                    overrides[override_symbol] = {}
                if "tsl" not in overrides[override_symbol]:
                    overrides[override_symbol]["tsl"] = {}
                overrides[override_symbol]["tsl"]["TSL_CONFIG"] = output_tsl
                overrides[override_symbol]["tsl"]["TSL_LOGIC_MODE"] = new_tsl_logic_mode
                save_symbol_overrides(overrides)
                app.log_message(
                    f"✅ TSL Override Saved for {override_symbol}.", target="bot"
                )
                top.destroy()
                return
            config.TSL_CONFIG.update(output_tsl)
            config.TSL_LOGIC_MODE = new_tsl_logic_mode
            app.save_settings()
            app.log_message("✅ TSL Saved.", target="bot")
            top.destroy()
        except:
            messagebox.showerror("Lỗi", "Cấu hình sai!", parent=top)

    def refresh_tsl_override_overview():
        for child in f_overview_rows.winfo_children():
            child.destroy()
        try:
            from core.storage_manager import load_symbol_overrides
            overrides = load_symbol_overrides()
        except:
            overrides = {}
        symbol_values = _build_watch_symbols()
        for row, sym in enumerate(symbol_values):
            has_override = bool(overrides.get(sym, {}).get("tsl"))
            row_frame = ctk.CTkFrame(
                f_overview_rows,
                fg_color="#2F2A12" if has_override else "#242424",
                corner_radius=6,
            )
            row_frame.grid(row=row, column=0, sticky="ew", padx=2, pady=2)
            row_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                row_frame,
                text=sym,
                width=110,
                anchor="w",
                font=("Roboto", 12, "bold"),
                text_color="#FFFFFF",
            ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
            ctk.CTkLabel(
                row_frame,
                text="OVERRIDE" if has_override else "GLOBAL",
                anchor="w",
                font=("Roboto", 11, "bold"),
                text_color="#FFAB00" if has_override else "#B0BEC5",
            ).grid(row=0, column=1, sticky="w", padx=8, pady=6)
            ctk.CTkButton(
                row_frame,
                text="EDIT" if has_override else "SELECT",
                width=76,
                height=24,
                fg_color="#1f538d" if has_override else "#424242",
                hover_color="#14375e" if has_override else "#616161",
                command=lambda s=sym: load_tsl_for_sym(s),
            ).grid(row=0, column=2, sticky="e", padx=(4, 6), pady=5)
            ctk.CTkButton(
                row_frame,
                text="RESET",
                width=70,
                height=24,
                fg_color="#B71C1C" if has_override else "#303030",
                hover_color="#7F0000" if has_override else "#303030",
                state="normal" if has_override else "disabled",
                command=lambda s=sym: reset_tsl_for_sym(s),
            ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=5)
    if not override_symbol:
        refresh_tsl_override_overview()
    ctk.CTkButton(
        top,
        text="SAVE TSL LOGIC",
        fg_color="#7B1FA2",
        hover_color="#4A148C",
        height=40,
        font=("Roboto", 13, "bold"),
        command=save,
    ).pack(pady=(6, 14), fill="x", padx=70)
    if override_symbol:

        def reset_tsl_override():
            from core.storage_manager import (
                load_symbol_overrides,
                save_symbol_overrides,
            )
            overrides = load_symbol_overrides()
            if override_symbol in overrides and "tsl" in overrides[override_symbol]:
                del overrides[override_symbol]["tsl"]
                save_symbol_overrides(overrides)
                app.log_message(
                    f"✅ TSL Override Reset for {override_symbol}.", target="bot"
                )
                top.destroy()
        ctk.CTkButton(
            top,
            text="🗑️ RESET (VỀ MẶC ĐỊNH)",
            fg_color="#D50000",
            hover_color="#B71C1C",
            height=40,
            font=FONT_BOLD,
            command=reset_tsl_override,
        ).pack(pady=(0, 15), fill="x", padx=40)
    if not override_symbol:

        def build_overwrite_tab():
            f = _speed_up_scroll(ctk.CTkScrollableFrame(tab_ow))
            f.pack(fill="both", expand=True, padx=5, pady=5)
            ctk.CTkLabel(
                f,
                text="CẤU HÌNH GHI ĐÈ (PER-SYMBOL OVERRIDE)",
                font=("Roboto", 14, "bold"),
            ).pack(pady=10)
            _add_popup_hint(
                f,
                "- Symbol có override sẽ dùng TSL riêng thay cho Global.\n"
                "- Reset override = xóa TSL con, quay về TSL mẹ.\n"
                "- Override chỉ áp dụng cho symbol được chọn.",
                padx=10,
                pady=(0, 10),
                wraplength=400,
            )
            grid_frame = ctk.CTkFrame(f, fg_color="transparent")
            grid_frame.pack(pady=10)
            from core.storage_manager import get_brain_settings_for_symbol
            from core.storage_manager import load_symbol_overrides
            brain = get_brain_settings_for_symbol()
            symbols = _build_watch_symbols()
            overrides = load_symbol_overrides()
            row, col = 0, 0
            for sym in symbols:
                has_override = sym in overrides and "tsl" in overrides[sym]
                color = "#00C853" if has_override else "#424242"
                btn = ctk.CTkButton(
                    grid_frame,
                    text=f"{sym} {'(Có)' if has_override else ''}",
                    fg_color=color,
                    command=lambda s=sym: open_tsl_popup(app, s),
                )
                btn.grid(row=row, column=col, padx=5, pady=5)
                col += 1
                if col > 3:
                    col = 0
                    row += 1
        build_overwrite_tab()

# ==============================================================================

# 4. POPUP EDIT ORDER (FULL FEATURES: MATH SL, PRESET TP, DCA/PCA, TACTIC TOGGLES)

# ==============================================================================


def open_edit_popup(app, ticket):

    finder = getattr(app, "_find_open_position", None)
    if callable(finder):
        pos = finder(ticket)
    else:
        pos = next(
            (
                p
                for p in app.connector.get_all_open_positions()
                if str(getattr(p, "ticket", "") or "") == str(ticket)
            ),
            None,
        )
    if not pos:
        return
    manager_selector = getattr(app, "_trade_manager_for_position", None)
    position_trade_mgr = (
        manager_selector(pos) if callable(manager_selector) else app.trade_mgr
    )
    top = ctk.CTkToplevel(app)
    top.title(f"Sửa lệnh #{ticket}")
    _fit_popup(top, 450, 830, 420, 560)
    _bring_popup_to_front(top)
    # top.transient(app)
    is_buy = pos.type == 0
    try:
        position_account = app.connector.get_account_info(
            paper_mode=str(ticket).upper().startswith("PAPER")
        )
    except TypeError:
        position_account = app.connector.get_account_info()
    except Exception:
        position_account = None
    bal = float((position_account or {}).get("balance", 1000.0) or 1000.0)
    ctk.CTkLabel(top, text="NEW SL:", font=FONT_BOLD).pack(pady=(10, 2))
    e_sl = ctk.CTkEntry(top, justify="center")
    e_sl.insert(0, app._price_internal_to_input(pos.sl, pos.symbol))
    e_sl.pack()
    lbl_h_sl = ctk.CTkLabel(
        top, text="~ -0", text_color="gray", font=("Roboto", 11)
    )
    lbl_h_sl.pack(pady=(0, 5))
    ctk.CTkLabel(top, text="NEW TP:", font=FONT_BOLD).pack(pady=(5, 2))
    e_tp = ctk.CTkEntry(top, justify="center")
    e_tp.insert(0, app._price_internal_to_input(pos.tp, pos.symbol))
    e_tp.pack()
    lbl_h_tp = ctk.CTkLabel(
        top, text="~ +0", text_color="gray", font=("Roboto", 11)
    )
    lbl_h_tp.pack(pady=(0, 5))
    # Khung chứa Live Tactic Preview
    f_tactic_preview = ctk.CTkFrame(top, fg_color="#1a1a1a", corner_radius=6)
    f_tactic_preview.pack(fill="x", padx=20, pady=5)
    lbl_tactic_preview = ctk.CTkLabel(
        f_tactic_preview,
        text="TSL Preview",
        text_color="#29B6F6",
        font=("Consolas", 12),
    )
    lbl_tactic_preview.pack(pady=5)
    cur_t = position_trade_mgr.get_trade_tactic(ticket)
    cur_modes = cur_t.split("+")
    states = {
        "BE": "BE" in cur_modes,
        "PNL": "PNL" in cur_modes,
        "STEP": "STEP_R" in cur_modes,
        "SWING": "SWING" in cur_modes,
        "CASH": "BE_CASH" in cur_modes,
        "PSAR": "PSAR_TRAIL" in cur_modes,
        "REV": "REV_C" in cur_modes,
        "A.CUT": "ANTI_CASH" in cur_modes,
    }
    if states["CASH"]:
        states["BE"] = False

    def live_edit(*args):
        try:
            nsl = app._price_input_to_internal(e_sl.get(), pos.symbol)
            ntp = app._price_input_to_internal(e_tp.get(), pos.symbol)
            try:
                contract_size = float(app.connector.get_symbol_info(pos.symbol, poll_tick=False).trade_contract_size or 1.0)
            except TypeError:
                contract_size = float(app.connector.get_symbol_info(pos.symbol).trade_contract_size or 1.0)
            except Exception:
                contract_size = 100000.0 if str(pos.symbol).upper().startswith("VN30F") else 1000.0
            if nsl > 0:
                dist = abs(pos.price_open - nsl)
                loss = dist * pos.volume * contract_size
                lbl_h_sl.configure(
                    text=f"~ -{format_vnd_full(loss)} ({loss / bal * 100:.2f}%)",
                    text_color="#EF5350",
                )
            if ntp > 0:
                p_dist = abs(pos.price_open - ntp)
                prof = p_dist * pos.volume * contract_size
                lbl_h_tp.configure(text=f"~ +{format_vnd_full(prof)}", text_color="#66BB6A")
            else:
                lbl_h_tp.configure(text="~ Thả rông (Vô cực)", text_color="#29B6F6")
            # Cập nhật Live Trigger Price Preview
            if nsl > 0:
                r_dist = abs(pos.price_open - nsl)
                if r_dist > 0:
                    preview_txts = []
                    if states["BE"]:
                        trig_r = config.TSL_CONFIG.get("BE_SL_LOSS_TRIGGER", 0.5)
                        trig_p = (
                            pos.price_open - (trig_r * r_dist)
                            if is_buy
                            else pos.price_open + (trig_r * r_dist)
                        )
                        preview_txts.append(f"BE_SL Loss @ {app._fmt_price(trig_p, pos.symbol)}")
                    if states["STEP"]:
                        sz = config.TSL_CONFIG.get("STEP_R_SIZE", 1.0)
                        trig_p = (
                            pos.price_open + (sz * r_dist)
                            if is_buy
                            else pos.price_open - (sz * r_dist)
                        )
                        preview_txts.append(f"Step 1 @ {app._fmt_price(trig_p, pos.symbol)}")
                    if states["PNL"] and config.TSL_CONFIG.get("PNL_LEVELS"):
                        lvl = config.TSL_CONFIG["PNL_LEVELS"][0]
                        preview_txts.append(f"PNL @ Lãi {lvl[0]}%")
                    if states["SWING"]:
                        preview_txts.append("SWING (Đuổi theo nến H1/M15)")
                    if states["CASH"]:
                        preview_txts.append(
                            f"CASH TRAIL Bậc thang (Step: {config.TSL_CONFIG.get('BE_VALUE', 5)})"
                        )
                    if states["PSAR"]:
                        preview_txts.append("PSAR TRAIL")
                    if states["REV"]:
                        preview_txts.append("Close on Reverse")
                    if states["A.CUT"]:
                        preview_txts.append("Anti-Cash")
                    if preview_txts:
                        lbl_tactic_preview.configure(
                            text="Dự kiến Trigger TSL:\n" + " | ".join(preview_txts)
                        )
                    else:
                        lbl_tactic_preview.configure(text="TSL: OFF")
        except:
            pass
    e_sl.bind("<KeyRelease>", live_edit)
    e_tp.bind("<KeyRelease>", live_edit)

    # [UPGRADED] Math SL với dropdown chọn Group
    f_math = ctk.CTkFrame(top, fg_color="#1a1a1a", corner_radius=6)
    f_math.pack(fill="x", padx=20, pady=(5, 0))
    ctk.CTkLabel(
        f_math, text="TREND/RANGE Group:", font=("Roboto", 11), text_color="gray"
    ).pack(side="left", padx=(8, 4))
    var_sl_group = ctk.StringVar(value="G2")
    cbo_sl_group = ctk.CTkOptionMenu(
        f_math,
        values=["G0", "G1", "G2", "G3", "DYNAMIC-G1/G2"],
        variable=var_sl_group,
        width=130,
        height=26,
        fg_color="#2b2b2b",
        button_color="#1565C0",
        command=lambda _: do_math(),
    )
    cbo_sl_group.pack(side="left", padx=4)

    def do_math():
        ctx = app.latest_market_context.get(pos.symbol, {})
        group = var_sl_group.get()
        # Xử lý DYNAMIC: tự chọn group dựa trên Market Mode
        if "DYNAMIC" in group:
            mode = ctx.get("market_mode", "ANY")
            group = "G1" if mode in ["TREND", "BREAKOUT"] else "G2"
            var_sl_group.set(f"→{group}")  # Hiển thị group thực tế đã chọn
        val = ctx.get(f"swing_low_{group}" if is_buy else f"swing_high_{group}")
        atr_val = ctx.get(f"atr_{group}")
        if val and str(val) != "--" and atr_val:
            brain = position_trade_mgr._get_brain_settings()
            mult = float(
                brain.get("risk_tsl", {}).get(
                    "sl_atr_multiplier", getattr(config, "sl_atr_multiplier", 0.2)
                )
            )
            calc_sl = (
                float(val) - (float(atr_val) * mult)
                if is_buy
                else float(val) + (float(atr_val) * mult)
            )
            e_sl.delete(0, "end")
            e_sl.insert(0, f"{calc_sl:.5f}")
            do_tp()  # Tự động cập nhật TP theo SL mới
            live_edit()
        else:
            messagebox.showwarning(
                "Không có dữ liệu",
                f"Không tìm thấy Swing/ATR của {group} cho {pos.symbol}.\nThử chọn Group khác.",
                parent=top,
            )
    ctk.CTkButton(
        f_math,
        text="Lấy Math SL",
        height=26,
        fg_color="#1565C0",
        hover_color="#0D47A1",
        font=("Roboto", 12, "bold"),
        command=do_math,
    ).pack(side="left", padx=8, pady=4)
    f_ast = ctk.CTkFrame(top, fg_color="transparent")
    f_ast.pack(pady=(6, 0))

    def do_tp():
        try:
            rr = config.PRESETS.get(getattr(config, "DEFAULT_PRESET", "SCALPING"), {}).get("TP_RR_RATIO", 1.5)
            sl_value = app._price_input_to_internal(e_sl.get(), pos.symbol)
            tp = pos.price_open + (
                abs(pos.price_open - sl_value) * rr
                if is_buy
                else -abs(pos.price_open - sl_value) * rr
            )
            e_tp.delete(0, "end")
            e_tp.insert(0, app._price_internal_to_input(tp, pos.symbol))
            live_edit()
        except:
            pass

    def do_clear_tp():
        e_tp.delete(0, "end")
        e_tp.insert(0, "0.0")
        live_edit()

    def do_swing_tp():
        ctx = app.latest_market_context.get(pos.symbol, {})
        group = var_sl_group.get()
        if "DYNAMIC" in group:
            mode = ctx.get("market_mode", "ANY")
            group = "G1" if mode in ["TREND", "BREAKOUT"] else "G2"
        val = ctx.get(f"swing_high_{group}" if is_buy else f"swing_low_{group}")
        atr_val = ctx.get(f"atr_{group}")
        if val and str(val) != "--" and atr_val:
            brain = position_trade_mgr._get_brain_settings()
            mult = float(brain.get("risk_tsl", {}).get("sl_atr_multiplier", 0.2))
            calc_tp = (
                float(val) - (float(atr_val) * mult)
                if is_buy
                else float(val) + (float(atr_val) * mult)
            )
            e_tp.delete(0, "end")
            e_tp.insert(0, f"{calc_tp:.5f}")
            live_edit()
        else:
            messagebox.showwarning("Lỗi", "Không có dữ liệu Swing TP", parent=top)
    ctk.CTkButton(
        f_ast, text="Lấy Preset TP", width=105, fg_color="#2E7D32", command=do_tp
    ).pack(side="left", padx=2)
    ctk.CTkButton(
        f_ast, text="Lấy Swing TP", width=105, fg_color="#66BB6A", command=do_swing_tp
    ).pack(side="left", padx=2)
    ctk.CTkButton(
        f_ast, text="Bỏ TP (Vô cực)", width=105, fg_color="#455A64", command=do_clear_tp
    ).pack(side="right", padx=2)
    f_tactic_row = ctk.CTkFrame(top, fg_color="transparent")
    f_tactic_row.pack(pady=(10, 2))
    ctk.CTkLabel(
        f_tactic_row, text="TACTIC:", font=("Roboto", 11, "bold"), text_color="gray"
    ).pack(side="left", padx=(0, 5))
    btns = {}

    def tog(k):
        states[k] = not states[k]
        if k == "CASH" and states[k]:
            states["BE"] = False
        elif k == "BE" and states[k]:
            states["CASH"] = False
        for key in ("BE", "CASH", k):
            if key in btns:
                btns[key].configure(
                    fg_color=COL_BLUE_ACCENT if states[key] else COL_GRAY_BTN
                )
        live_edit()
    # Dòng 1: TACTIC (6 nút giống hệt Panel)
    tactic_widths = {
        "BE": 32,
        "PNL": 28,
        "STEP": 32,
        "SWING": 38,
        "CASH": 38,
        "PSAR": 38,
    }
    for k in ["BE", "PNL", "STEP", "SWING", "CASH", "PSAR"]:
        btns[k] = ctk.CTkButton(
            f_tactic_row,
            text=k,
            width=tactic_widths[k],
            fg_color=COL_BLUE_ACCENT if states[k] else COL_GRAY_BTN,
            command=lambda x=k: tog(x),
        )
        btns[k].pack(side="left", padx=1)
    f_def_row = ctk.CTkFrame(top, fg_color="transparent")
    f_def_row.pack(pady=(2, 10))
    ctk.CTkLabel(
        f_def_row, text="DEF:", font=("Roboto", 11, "bold"), text_color="gray"
    ).pack(side="left", padx=(0, 5))
    # Dòng 2: DEF (DCA, PCA checkboxes + REV, A.CUT buttons)
    chk_dca = ctk.CTkCheckBox(f_def_row, text="DCA", font=("Roboto", 11), width=45)
    chk_dca.pack(side="left", padx=4)
    chk_pca = ctk.CTkCheckBox(f_def_row, text="PCA", font=("Roboto", 11), width=45)
    chk_pca.pack(side="left", padx=4)
    if "AUTO_DCA" in cur_t:
        chk_dca.select()
    if "AUTO_PCA" in cur_t:
        chk_pca.select()
    def_widths = {"REV": 34, "A.CUT": 38}
    for k in ["REV", "A.CUT"]:
        btns[k] = ctk.CTkButton(
            f_def_row,
            text=k,
            width=def_widths[k],
            fg_color=COL_BLUE_ACCENT if states[k] else COL_GRAY_BTN,
            command=lambda x=k: tog(x),
        )
        btns[k].pack(side="left", padx=1)

    # --- Entry/Exit Mode Row ---
    f_ee_row = ctk.CTkFrame(top, fg_color="#1a1a1a", corner_radius=6)
    f_ee_row.pack(fill="x", padx=20, pady=(4, 6))
    ctk.CTkLabel(
        f_ee_row, text="E/E Mode:", font=("Roboto", 11, "bold"), text_color="#B0BEC5"
    ).pack(side="left", padx=(8, 4), pady=6)
    ee_options_map = {
        "OFF": "OFF",
        "R": "FALLBACK_R",
        "RETEST": "SWING_REJECTION",
        "STRUCT": "SWING_STRUCTURE",
        "FIB": "FIB_RETRACE",
        "PULL": "PULLBACK_ZONE",
    }
    current_ee = position_trade_mgr.get_trade_entry_exit_tactic(ticket)
    var_ee = ctk.StringVar(value=current_ee if current_ee else "OFF")
    ee_btns = {}

    def set_ee(label):
        var_ee.set(label)
        for lb, btn in ee_btns.items():
            is_active = (lb == label)
            btn.configure(
                fg_color="#E65100" if is_active else COL_GRAY_BTN,
                text_color="white",
            )
    for lb in ee_options_map:
        # Xác định nút nào đang active dựa trên giá trị hiện tại
        val = ee_options_map[lb]
        is_active = (current_ee == val) or (lb == "OFF" and (not current_ee or current_ee == "OFF"))
        b = ctk.CTkButton(
            f_ee_row,
            text=lb,
            width=46 if lb in ("RETEST", "STRUCT") else 36,
            height=26,
            fg_color="#E65100" if is_active else COL_GRAY_BTN,
            hover_color="#BF360C" if is_active else "#616161",
            font=("Roboto", 11, "bold"),
            command=lambda x=lb: set_ee(x),
        )
        b.pack(side="left", padx=2, pady=4)
        ee_btns[lb] = b
    live_edit()  # Lần gọi đầu tiên khi mở popup

    btn_update = None

    def save_e():
        try:
            ensure_for_mode = getattr(app, "_ensure_trading_otp_for_mode", None)
            otp_ok = (
                ensure_for_mode(str(ticket).upper().startswith("PAPER"))
                if callable(ensure_for_mode)
                else app._ensure_trading_otp()
            )
            if not otp_ok:
                return
            new_sl = app._price_input_to_internal(e_sl.get(), pos.symbol)
            new_tp = app._price_input_to_internal(e_tp.get(), pos.symbol)
            act = []
            for k, v in states.items():
                if v:
                    if k == "STEP":
                        act.append("STEP_R")
                    elif k == "CASH":
                        act.append("BE_CASH")
                    elif k == "PSAR":
                        act.append("PSAR_TRAIL")
                    elif k == "REV":
                        act.append("REV_C")
                    elif k == "A.CUT":
                        act.append("ANTI_CASH")
                    else:
                        act.append(k)
            final_t = "+".join(act) if act else "OFF"
            if chk_dca.get():
                final_t += "+AUTO_DCA"
            if chk_pca.get():
                final_t += "+AUTO_PCA"
            # Lưu Entry/Exit Mode
            selected_ee_label = var_ee.get()
            new_ee_tactic = ee_options_map.get(selected_ee_label, "OFF")
            btn_update.configure(state="disabled", text="ĐANG CẬP NHẬT...")

            def _finish_success():
                app.log_message(
                    f"[CẬP NHẬT LỆNH] #{ticket}: SL/TP đã được broker xác nhận | "
                    f"TSL={final_t} | E/E={new_ee_tactic}"
                )
                top.destroy()

            def _finish_error(error):
                try:
                    btn_update.configure(state="normal", text="CẬP NHẬT LỆNH")
                    messagebox.showerror("Không cập nhật được", str(error), parent=top)
                except Exception:
                    pass

            def _update_worker():
                try:
                    modifier = getattr(app, "_modify_position_for_mode", None)
                    modify_result = (
                        modifier(pos, new_sl, new_tp)
                        if callable(modifier)
                        else app.connector.modify_position(pos, new_sl, new_tp)
                    )
                    _require_broker_success(modify_result, "Cập nhật SL/TP")
                    position_trade_mgr.update_trade_tactic(ticket, final_t)
                    position_trade_mgr.update_trade_entry_exit_tactic(
                        ticket,
                        new_ee_tactic,
                    )
                    app.after(0, _finish_success)
                except Exception as error:
                    app.after(0, lambda error=error: _finish_error(error))

            threading.Thread(target=_update_worker, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Lỗi", str(e), parent=top)
    btn_update = ctk.CTkButton(
        top,
        text="CẬP NHẬT LỆNH",
        height=45,
        fg_color="#2e7d32",
        font=FONT_BOLD,
        command=save_e,
    )
    btn_update.pack(pady=20, fill="x", padx=40)

def show_history_popup(app):

    top = ctk.CTkToplevel(app)
    top.title("Lịch sử giao dịch theo chiến thuật")
    _fit_popup(top, 1400, 650, 1000, 550)
    _bring_popup_to_front(top)

    history_tabs = ctk.CTkTabview(top)
    history_tabs.pack(fill="both", expand=True)
    # 4 tab tách theo loại thị trường (PS/CKCS) × thật/paper. Phân loại từ ticket + symbol.
    SCOPES = ["Phái sinh", "CKCS", "Paper-PS", "Paper-CKCS"]
    scope_tabs = {name: history_tabs.add(name) for name in SCOPES}
    opportunity_tab = history_tabs.add("Gợi ý BOT")

    cols = (
        "Thời gian", "Ticket", "Mã", "Chiều", "KL", "Giá vào", "Giá đóng", "SL", "TP",
        "Phí", "Lãi/Lỗ ròng", "MAE", "MFE", "Lý do đóng", "Chiến thuật/Nguồn",
    )
    widths = [300, 150, 130, 110, 110, 140, 140, 140, 140, 120, 175, 140, 140, 260, 360]

    style = ttk.Style()
    style.configure(
        "History.Treeview",
        background="#242424",
        foreground="#ECEFF1",
        fieldbackground="#242424",
        rowheight=50,
        font=("Consolas", 18),
    )
    style.configure(
        "History.Treeview.Heading",
        background="#1f1f1f",
        foreground="#e0e0e0",
        font=("Roboto", 20, "bold"),
        relief="flat",
    )

    def make_tree(parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=6, pady=6)
        tree = ttk.Treeview(frame, columns=cols, show="tree headings", style="History.Treeview")
        yscrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscrollbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscrollbar.set, xscrollcommand=xscrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tree.column("#0", width=325, minwidth=325, anchor="w", stretch=False)
        tree.heading("#0", text="Ngày đóng")
        for col, width in zip(cols, widths):
            tree.heading(col, text=col)
            tree.column(col, width=width, minwidth=width, anchor="center", stretch=False)
        return tree

    trees = {name: make_tree(scope_tabs[name]) for name in SCOPES}

    opp_cols = (
        "First", "Last", "Mode", "Market", "Symbol", "Side", "Price", "Qty",
        "SL", "TP", "Risk / Reward", "TSL", "Count", "Blocked", "Result",
    )
    opp_toolbar = ctk.CTkFrame(opportunity_tab, fg_color="transparent")
    opp_toolbar.pack(fill="x", padx=6, pady=(4, 0))
    ctk.CTkLabel(
        opp_toolbar,
        text="Gợi ý hợp lệ được theo dõi giả lập bằng giá daemon; không đặt lệnh.",
        text_color="#90CAF9",
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        opp_toolbar,
        text="⟳ LÀM MỚI",
        width=120,
        height=28,
        command=lambda: load_data(),
    ).pack(side="right", padx=4)
    opp_frame = ctk.CTkFrame(opportunity_tab, fg_color="transparent")
    opp_frame.pack(fill="both", expand=True, padx=6, pady=6)
    opportunity_tree = ttk.Treeview(opp_frame, columns=opp_cols, show="tree headings", style="History.Treeview")
    opp_y = ttk.Scrollbar(opp_frame, orient="vertical", command=opportunity_tree.yview)
    opp_x = ttk.Scrollbar(opp_frame, orient="horizontal", command=opportunity_tree.xview)
    opportunity_tree.configure(yscrollcommand=opp_y.set, xscrollcommand=opp_x.set)
    opportunity_tree.grid(row=0, column=0, sticky="nsew")
    opp_y.grid(row=0, column=1, sticky="ns")
    opp_x.grid(row=1, column=0, sticky="ew")
    opp_frame.grid_rowconfigure(0, weight=1)
    opp_frame.grid_columnconfigure(0, weight=1)
    opportunity_tree.heading("#0", text="Ngày")
    opportunity_tree.column("#0", width=180, minwidth=180, anchor="w", stretch=False)
    opp_widths = [150, 150, 100, 100, 110, 90, 120, 100, 120, 120, 220, 240, 90, 360, 300]
    for col, width in zip(opp_cols, opp_widths):
        opportunity_tree.heading(col, text=col)
        opportunity_tree.column(col, width=width, minwidth=width, anchor="center", stretch=False)

    from core.storage_manager import MASTER_LOG_FILE
    csv_path = MASTER_LOG_FILE

    _derivatives = {str(s).upper() for s in getattr(config, "CKPS_SYMBOLS", []) or []}
    _deriv_reals = {str(s).upper() for s in getattr(config, "DERIVATIVE_REAL_SYMBOLS", []) or []}

    def to_float(val, default=0.0):
        return _history_float(val, default)

    def row_scope(row):
        return _history_row_scope(row, _derivatives | _deriv_reals)

    def clear_trees():
        for tree in trees.values():
            if tree.winfo_exists():
                for item in tree.get_children():
                    tree.delete(item)
        for item in opportunity_tree.get_children():
            opportunity_tree.delete(item)

    def fmt_money(val):
        return format_vnd_full(val, signed=True)

    def insert_sessions(tree, sessions):
        sorted_sessions = sorted(sessions.keys(), reverse=True)
        for sid in sorted_sessions:
            rows = sorted(
                sessions[sid],
                key=lambda row: _history_row_close_datetime(row) or datetime.min,
                reverse=True,
            )
            summary = _history_session_summary(rows)
            parent_id = tree.insert(
                "", "end", text=f"Ngày: {sid}",
                values=(
                    f"Ròng ngày: {fmt_money(summary['net_pnl'])}",
                    "",
                    "",
                    f"Mua:{summary['buys']} | Bán:{summary['sells']}",
                    f"Thắng: {summary['winrate']:.1f}%",
                    "",
                    "",
                    "",
                    "",
                    fmt_money(summary["fee"]),
                    fmt_money(summary["net_pnl"]),
                    fmt_money(summary["worst_mae"]) if summary["worst_mae"] is not None else "--",
                    fmt_money(summary["best_mfe"]) if summary["best_mfe"] is not None else "--",
                    "",
                    "",
                ),
            )
            for row in rows:
                reason = row[10]
                if reason == "Basket_TP" and to_float(row[9]) < 0:
                    reason = "Basket_TP_Order_Loss"
                net_pnl = _history_row_net_pnl(row)
                mae = ""
                if len(row) > 14 and str(row[14]).strip():
                    mae = f"{min(to_float(row[14]), net_pnl):.2f}"
                mfe = ""
                if len(row) > 15 and str(row[15]).strip():
                    mfe = f"{max(to_float(row[15]), net_pnl):.2f}"
                tree.insert(
                    parent_id, "end", text="",
                    values=(
                        row[0], row[1], row[2], row[3], row[4], row[5],
                        row[18] if len(row) > 18 and str(row[18]).strip() else "--",
                        row[6], row[7], row[8], f"{net_pnl:.2f}",
                        mae,
                        mfe,
                        reason,
                        row[12] if len(row) > 12 else "",
                    ),
                )
            if sid == sorted_sessions[0]:
                tree.item(parent_id, open=True)

    def load_data():
        clear_trees()
        try:
            from core import signal_opportunities
            records = signal_opportunities.list_history(include_active=True)
            by_day = {}
            for item in records:
                day = str(item.get("date") or datetime.fromtimestamp(float(item.get("first_seen_at", 0) or 0)).strftime("%Y-%m-%d"))
                by_day.setdefault(day, []).append(item)
            for day in sorted(by_day, reverse=True):
                rows = by_day[day]
                outcomes = [
                    str((item.get("simulation") or {}).get("status") or "").upper()
                    for item in rows
                    if isinstance(item.get("simulation"), dict)
                ]
                wins = outcomes.count("WIN")
                losses = outcomes.count("LOSS")
                opens = outcomes.count("OPEN")
                decided = wins + losses
                summary = (
                    f"{day} — {len(rows)} gợi ý | OPEN {opens} | "
                    f"WIN {wins} | LOSS {losses}"
                )
                if decided:
                    summary += f" | Winrate {wins / decided * 100.0:.1f}%"
                parent = opportunity_tree.insert(
                    "",
                    "end",
                    text=summary,
                    open=(day == sorted(by_day, reverse=True)[0]),
                )
                for item in sorted(rows, key=lambda x: float(x.get("first_seen_at", 0) or 0), reverse=True):
                    first = float(item.get("first_seen_at", 0) or 0)
                    last = float(item.get("last_seen_at", first) or first)
                    setup = item.get("order_setup", {}) if isinstance(item.get("order_setup"), dict) else {}
                    simulation = item.get("simulation", {}) if isinstance(item.get("simulation"), dict) else {}
                    price = float(setup.get("price", item.get("detected_price", 0)) or 0)
                    lot = float(setup.get("display_quantity", 0) or setup.get("lot", 0) or 0)
                    sl = float(setup.get("sl", 0) or 0)
                    tp = float(setup.get("tp", 0) or 0)
                    risk = float(setup.get("risk_amount", setup.get("risk_usd", 0)) or 0)
                    reward = float(setup.get("reward_amount", setup.get("reward_usd", 0)) or 0)
                    targets = []
                    for raw in setup.get("tp_targets") or ([tp] if tp else []):
                        value = to_float(raw)
                        if value > 0:
                            targets.append(f"{value:g}")
                    tp_text = " / ".join(targets[:3]) if targets else "--"
                    setup_error = str(setup.get("error") or "").strip()
                    simulation_status = str(simulation.get("status") or "").upper()
                    if simulation_status in {"WIN", "LOSS"}:
                        result = (
                            f"{simulation_status} {simulation.get('result', '')} "
                            f"@{to_float(simulation.get('close_price')):g} | "
                            f"PnL {format_vnd_full(to_float(simulation.get('pnl')), signed=True)}"
                        ).strip()
                    elif simulation_status == "OPEN":
                        started_at = to_float(simulation.get("started_at"))
                        started_text = (
                            datetime.fromtimestamp(started_at).strftime("%H:%M:%S")
                            if started_at
                            else "--"
                        )
                        result = (
                            f"OPEN từ {started_text} | "
                            f"giá cuối {to_float(simulation.get('last_price')):g}"
                        )
                    elif setup.get("ok") is False or not setup:
                        result = f"KHÔNG THEO DÕI | {setup_error or 'CHƯA CÓ PREVIEW'}"
                    else:
                        result = str(item.get("order_status") or item.get("status") or "")
                    if item.get("order_result"):
                        result += f" | {item.get('order_result')}"
                    opportunity_tree.insert(
                        parent,
                        "end",
                        text="",
                        values=(
                            datetime.fromtimestamp(first).strftime("%H:%M:%S") if first else "--",
                            datetime.fromtimestamp(last).strftime("%H:%M:%S") if last else "--",
                            item.get("execution_mode", ""),
                            item.get("market_type", ""),
                            item.get("symbol", ""),
                            item.get("side", ""),
                            f"{price:g}" if price else "--",
                            f"{lot:g}" if lot else "--",
                            f"{sl:g}" if sl else "--",
                            tp_text,
                            f"-{format_vnd_full(risk)} | +{format_vnd_full(reward)}" if risk or reward else "--",
                            setup.get("tactic", "--"),
                            item.get("signal_count", 1),
                            item.get("block_reason", ""),
                            result,
                        ),
                    )
        except Exception as exc:
            if hasattr(app, "log_message"):
                app.log_message(f"[HISTORY] Load gợi ý BOT lỗi: {exc}", target="manual")
        try:
            scope_sessions = {name: {} for name in SCOPES}
            records_by_ticket = {}
            paper_history_state = {}
            try:
                from core import storage_manager

                legacy_state_path = storage_manager.STATE_FILE
                if os.path.exists(legacy_state_path):
                    with open(legacy_state_path, "r", encoding="utf-8") as state_file:
                        loaded_state = json.load(state_file)
                        if isinstance(loaded_state, dict):
                            paper_history_state.update(loaded_state)
                live_state = getattr(getattr(app, "trade_mgr", None), "state", {})
                if isinstance(live_state, dict):
                    for key, value in live_state.items():
                        if isinstance(value, dict) and isinstance(paper_history_state.get(key), dict):
                            paper_history_state[key].update(value)
                        else:
                            paper_history_state[key] = value
            except Exception:
                paper_history_state = getattr(getattr(app, "trade_mgr", None), "state", {}) or {}
            if os.path.exists(csv_path):
                with open(csv_path, mode="r", encoding="utf-8") as file_obj:
                    reader = csv.reader(file_obj)
                    next(reader, None)
                    for row in reader:
                        if len(row) >= 14:
                            records_by_ticket[row[1]] = row
            try:
                getter = getattr(app.connector, "get_paper_closed_trades", None)
                for item in getter() if callable(getter) else []:
                    row = _paper_closed_trade_history_row(item, paper_history_state)
                    if row and row[1] not in records_by_ticket:
                        records_by_ticket[row[1]] = row
            except Exception as exc:
                if hasattr(app, "log_message"):
                    app.log_message(f"[HISTORY] Load biên nhận PAPER lỗi: {exc}", target="manual")

            for row in records_by_ticket.values():
                scope = row_scope(row)
                close_day = _history_row_close_day(row)
                scope_sessions[scope].setdefault(close_day, []).append(row)
            for scope, tree in trees.items():
                insert_sessions(tree, scope_sessions[scope])
        except Exception as exc:
            if hasattr(app, "log_message"):
                app.log_message(f"[HISTORY] Load failed: {exc}", target="manual")

    def delete_session(session_id):
        if messagebox.askyesno(
            "Cảnh báo",
            f"Bạn có chắc muốn xóa lịch sử các lệnh đã đóng ngày [{session_id}] không?",
            parent=top,
        ):
            from core.storage_manager import delete_history_day
            delete_history_day(session_id)
            paper_delete = getattr(app.connector, "delete_paper_closed_trades_for_day", None)
            if callable(paper_delete):
                paper_delete(session_id)
            if hasattr(app, "log_message"):
                app.log_message(f"Đã xóa lịch sử lệnh đóng ngày {session_id}.", target="manual")
            load_data()

    def bind_tree_menu(tree):
        def on_right_click(event):
            row_id = tree.identify_row(event.y)
            if not row_id:
                return
            tree.selection_set(row_id)
            if tree.parent(row_id) != "":
                return
            session_text = tree.item(row_id, "text")
            session_id = session_text.replace("Ngày: ", "")
            menu = tk.Menu(top, tearoff=0, font=("Roboto", 11))
            menu.add_command(label=f"Xóa lịch sử ngày [{session_id}]", command=lambda: delete_session(session_id))
            menu.post(event.x_root, event.y_root)
        tree.bind("<Button-3>", on_right_click)

    for tree in trees.values():
        bind_tree_menu(tree)
    load_data()
def open_minibrain_popup(app, title, mb_cfg, on_save_callback):
    """
    [NEW V5.1] Popup cài đặt Mini-Brain 1-Group độc lập cho DCA/PCA
    """
    from tkinter import messagebox
    import customtkinter as ctk
    import config as _cfg
    top = ctk.CTkToplevel()
    top.title(title)
    _fit_popup(top, 700, 520, 620, 460)
    _bring_popup_to_front(top)
    top.grab_set()
    f_top = ctk.CTkFrame(top)
    f_top.pack(fill="x", padx=10, pady=10)
    var_active = ctk.BooleanVar(value=mb_cfg.get("active", False))
    ctk.CTkCheckBox(
        f_top, text="Bật Mini-Brain", variable=var_active, font=("Roboto", 13, "bold")
    ).pack(side="left", padx=10)
    ctk.CTkLabel(f_top, text="Timeframe:").pack(side="left", padx=(20, 5))
    cbo_tf = ctk.CTkComboBox(
        f_top, values=["1m", "5m", "15m", "30m", "1h", "4h"], width=80
    )
    cbo_tf.set(mb_cfg.get("timeframe", "15m"))
    cbo_tf.pack(side="left", padx=5)
    f_rules = ctk.CTkFrame(top)
    f_rules.pack(fill="x", padx=10, pady=5)
    ctk.CTkLabel(f_rules, text="Max Opposite (Phiếu ngược tối đa):").pack(
        side="left", padx=10, pady=5
    )
    e_max_opp = ctk.CTkEntry(f_rules, width=60, justify="center")
    e_max_opp.insert(0, str(mb_cfg.get("max_opposite", 0)))
    e_max_opp.pack(side="left", padx=5)
    ctk.CTkLabel(f_rules, text="Max None (Phiếu trắng tối đa):").pack(
        side="left", padx=20, pady=5
    )
    e_max_none = ctk.CTkEntry(f_rules, width=60, justify="center")
    e_max_none.insert(0, str(mb_cfg.get("max_none", 0)))
    e_max_none.pack(side="left", padx=5)
    f_inds = ctk.CTkFrame(top)
    f_inds.pack(fill="both", expand=True, padx=10, pady=10)
    ctk.CTkLabel(f_inds, text="CHỌN CHỈ BÁO", font=("Roboto", 12, "bold")).pack(pady=5)
    inds_cfg = mb_cfg.get("indicators", {})
    vars_dict = {}
    # Dùng SANDBOX_CONFIG["indicators"] làm nguồn danh sách indicator (không dùng INDICATOR_DEFINITIONS không tồn tại)
    all_indicators = _cfg.SANDBOX_CONFIG.get("indicators", {})
    LABEL_MAP = {
        "adx": "ADX",
        "ema": "EMA",
        "swing_point": "Swing Point",
        "atr": "ATR",
        "pivot_points": "Pivot Points",
        "ema_cross": "EMA Cross",
        "volume": "Volume",
        "supertrend": "SuperTrend",
        "psar": "PSAR",
        "bollinger_bands": "Bollinger Bands",
        "fibonacci": "Fibonacci",
        "rsi": "RSI",
        "stochastic": "Stochastic",
        "macd": "MACD",
        "multi_candle": "Multi-Candle",
        "candle": "Candle",
        "simple_breakout": "Simple Breakout",
    }
    grid_f = ctk.CTkFrame(f_inds, fg_color="transparent")
    grid_f.pack(fill="both", expand=True, padx=5, pady=5)
    r, c = 0, 0
    for key in all_indicators.keys():
        is_on = inds_cfg.get(key, {}).get("active", False)
        var = ctk.BooleanVar(value=is_on)
        vars_dict[key] = var
        label = LABEL_MAP.get(key, key.upper())
        ctk.CTkCheckBox(grid_f, text=label, variable=var).grid(
            row=r, column=c, sticky="w", padx=10, pady=5
        )
        c += 1
        if c > 2:
            c = 0
            r += 1

    def save_mb():
        try:
            new_cfg = {
                "active": var_active.get(),
                "timeframe": cbo_tf.get(),
                "max_opposite": int(e_max_opp.get()),
                "max_none": int(e_max_none.get()),
                "indicators": {},
            }
            for k, v in vars_dict.items():
                if v.get():
                    # Chỉ lưu trạng thái active, không lưu cứng params để Mini-Brain tự mượn params từ Sandbox
                    new_cfg["indicators"][k] = {"active": True}
            on_save_callback(new_cfg)
            top.destroy()
        except ValueError as e:
            messagebox.showerror("Lỗi", f"Vui lòng nhập số hợp lệ: {e}", parent=top)
    ctk.CTkButton(
        top,
        text="LƯU MINI-BRAIN",
        fg_color="#FBC02D",
        text_color="#212121",
        font=("Roboto", 13, "bold"),
        command=save_mb,
    ).pack(pady=10)


def _open_stock_portfolio_popup_legacy(app):
    """Cửa sổ Danh mục cổ phiếu nắm giữ (CKCS) — read-only.

    Liệt kê mọi mã đang giữ (gộp lô cùng mã), sắp theo giá trị giảm dần, kèm
    tách Tổng / Tiền mặt / Giá trị cổ phiếu. Dữ liệu nạp ở app.update_portfolio_table().
    """
    import ui_panels

    existing = getattr(app, "portfolio_popup", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                _bring_popup_to_front(existing)
                app.update_portfolio_table()
                return
        except Exception:
            pass

    top = ctk.CTkToplevel(app)
    top.title("Danh mục cổ phiếu nắm giữ")
    _fit_popup(top, 1500, 620, 1000, 420)
    _bring_popup_to_front(top)
    app.portfolio_popup = top

    # Thanh tách tài sản: Tổng · Tiền mặt · Giá trị CP
    f_sum = ctk.CTkFrame(top, fg_color="#1a1a1a", corner_radius=8)
    f_sum.pack(fill="x", padx=8, pady=(8, 4))
    app.lbl_port_total = ctk.CTkLabel(
        f_sum, text="Tổng tài sản: --", font=("Roboto", 16, "bold"), text_color="#00C853",
    )
    app.lbl_port_total.pack(side="left", padx=12, pady=8)
    app.lbl_port_cash = ctk.CTkLabel(
        f_sum, text="Tiền mặt: --", font=("Roboto", 14, "bold"), text_color="#90CAF9",
    )
    app.lbl_port_cash.pack(side="left", padx=12)
    # Sức mua = tiền + vay được (chỉ hiện khi khác tiền mặt -> có margin).
    app.lbl_port_avail = ctk.CTkLabel(
        f_sum, text="", font=("Roboto", 14, "bold"), text_color="#80DEEA",
    )
    app.lbl_port_avail.pack(side="left", padx=12)
    app.lbl_port_stock = ctk.CTkLabel(
        f_sum, text="Cổ phiếu: --", font=("Roboto", 14, "bold"), text_color="#FFCC80",
    )
    app.lbl_port_stock.pack(side="left", padx=12)
    # Tổng riêng phần lô lẻ (cần ra app DNSE bán).
    app.lbl_port_odd = ctk.CTkLabel(
        f_sum, text="Lô lẻ: --", font=("Roboto", 14, "bold"), text_color="#FFD7A0",
    )
    app.lbl_port_odd.pack(side="left", padx=12)
    # Nợ vay & cổ tức sắp về — chỉ hiện khi > 0 (đỡ rối với tài khoản cash-only).
    app.lbl_port_debt = ctk.CTkLabel(
        f_sum, text="", font=("Roboto", 14, "bold"), text_color="#EF9A9A",
    )
    app.lbl_port_debt.pack(side="left", padx=12)
    app.lbl_port_dividend = ctk.CTkLabel(
        f_sum, text="", font=("Roboto", 14, "bold"), text_color="#A5D6A7",
    )
    app.lbl_port_dividend.pack(side="left", padx=12)
    ctk.CTkButton(
        f_sum, text="⟳ Làm mới", width=100, height=26,
        command=app.update_portfolio_table,
    ).pack(side="right", padx=10)

    # Bảng danh mục (dựng lại app.tree_portfolio trong cửa sổ này)
    body = ctk.CTkFrame(top, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    ui_panels.setup_portfolio_tree(app, body)

    def _on_close():
        app.portfolio_popup = None
        app.tree_portfolio = None
        app.lbl_port_total = None
        app.lbl_port_cash = None
        app.lbl_port_avail = None
        app.lbl_port_stock = None
        app.lbl_port_odd = None
        app.lbl_port_debt = None
        app.lbl_port_dividend = None
        try:
            top.destroy()
        except Exception:
            pass

    top.protocol("WM_DELETE_WINDOW", _on_close)
    app.update_portfolio_table()


# Phiên bản danh mục hợp nhất: khai báo sau hàm cũ để giữ tương thích
# cho các plugin/import cũ nhưng thay giao diện thực tế bằng bốn tab.
def show_running_color_legend(app):
    """Chú thích màu và thao tác chuột phải của bảng lệnh đang chạy."""
    current = getattr(app, "running_color_legend_popup", None)
    try:
        if current is not None and current.winfo_exists():
            current.lift()
            current.focus_force()
            return
    except Exception:
        pass

    top = ctk.CTkToplevel(app)
    top.title("Chú thích bảng lệnh đang chạy")
    _fit_popup(top, 720, 610, 620, 470)
    _bring_popup_to_front(top)
    app.running_color_legend_popup = top

    ctk.CTkLabel(
        top,
        text="MÀU DÒNG & TRẠNG THÁI",
        font=("Roboto", 18, "bold"),
        text_color="#E0E0E0",
    ).pack(anchor="w", padx=18, pady=(16, 4))
    ctk.CTkLabel(
        top,
        text="Màu chỉ giúp nhận dạng loại dòng. Trạng thái ghi trên chính dòng mới là kết quả cuối cùng.",
        text_color="#B0BEC5",
        justify="left",
        wraplength=670,
    ).pack(anchor="w", padx=18, pady=(0, 10))

    body = ctk.CTkScrollableFrame(top, fg_color="#202020")
    body.pack(fill="both", expand=True, padx=14, pady=8)
    legend = [
        ("#40205c", "GỢI Ý BOT / CACHE", "BOT đã tính giá, số lượng, SL/TP nhưng chưa gửi mua/bán. Chuột phải để chỉnh và kích hoạt."),
        ("#5c5417", "LỆNH ĐANG CHỜ", "Đang chờ giá hoặc chờ đúng phiên để gửi."),
        ("#51491b", "VỊ THẾ CHỜ ĐÓNG", "Yêu cầu đóng đã được cache. Bấm ↩ hoặc chuột phải để hủy trước khi app bắt đầu gửi."),
        ("#0b4f5c", "ĐANG GỬI", "App đang gửi lệnh tới DNSE; không bấm gửi lại."),
        ("#123f6b", "DNSE ĐÃ NHẬN", "DNSE đã nhận lệnh và đang chờ khớp."),
        ("#6a3f08", "KHỚP MỘT PHẦN", "Một phần khối lượng đã khớp, phần còn lại vẫn đang chờ."),
        ("#5c3a17", "CKCS ĐÃ KHỚP", "Cổ phiếu đã mua; xem cột trạng thái để biết đang chờ T+2 hay đã về."),
        ("#1f4630", "VỊ THẾ BUY / LONG", "Vị thế mua đang mở; màu không đại diện lãi/lỗ."),
        ("#4a2424", "VỊ THẾ SELL / SHORT", "Vị thế bán đang mở; màu không đại diện lãi/lỗ."),
        ("#7a1f1f", "LỆNH LỖI", "Lệnh gửi thất bại; chuột phải để xem lỗi."),
        ("#303030", "ĐÃ HỦY / HẾT HẠN", "Lệnh chờ đã bị hủy hoặc quá thời gian hiệu lực."),
    ]
    for color, title, detail in legend:
        row = ctk.CTkFrame(body, fg_color="#292929", corner_radius=6)
        row.pack(fill="x", padx=5, pady=4)
        swatch = ctk.CTkFrame(row, width=38, height=46, fg_color=color, corner_radius=5)
        swatch.pack(side="left", padx=8, pady=7)
        swatch.pack_propagate(False)
        text_frame = ctk.CTkFrame(row, fg_color="transparent")
        text_frame.pack(side="left", fill="x", expand=True, padx=(2, 8), pady=5)
        ctk.CTkLabel(text_frame, text=title, font=("Roboto", 12, "bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(
            text_frame,
            text=detail,
            text_color="#C7C7C7",
            anchor="w",
            justify="left",
            wraplength=590,
        ).pack(fill="x")

    ctk.CTkLabel(
        top,
        text=(
            "Chuột phải: GỢI Ý = chỉnh/kích hoạt hoặc xóa; LIMIT đang chờ = hủy; "
            "lệnh DNSE = hủy; vị thế đang mở = sửa SL/TP/TSL/E-E hoặc đóng vị thế."
        ),
        text_color="#FFD180",
        justify="left",
        wraplength=680,
    ).pack(fill="x", padx=18, pady=(4, 14))

    def _close():
        app.running_color_legend_popup = None
        top.destroy()

    top.protocol("WM_DELETE_WINDOW", _close)


def open_portfolio_popup(app):
    """Danh mục tách CKPS/CKCS và REAL/PAPER, chỉ đọc."""
    existing = getattr(app, "portfolio_popup", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                _bring_popup_to_front(existing)
                app.update_portfolio_table(force_portfolio=True)
                return
        except Exception:
            pass

    top = ctk.CTkToplevel(app)
    top.title("Danh mục & sức mua")
    _fit_popup(top, 1500, 680, 1000, 460)
    _bring_popup_to_front(top)
    app.portfolio_popup = top

    header = ctk.CTkFrame(top, fg_color="#1a1a1a", corner_radius=8)
    header.pack(fill="x", padx=8, pady=(8, 4))
    ctk.CTkLabel(
        header,
        text="DANH MỤC & SỨC MỞ",
        font=("Roboto", 16, "bold"),
        text_color="#E0E0E0",
    ).pack(side="left", padx=12, pady=8)
    ctk.CTkButton(
        header,
        text="⟳ Làm mới",
        width=110,
        height=28,
        command=lambda: app.update_portfolio_table(force_portfolio=True),
    ).pack(side="right", padx=10)

    scopes = ["CKPS REAL", "CKCS REAL", "CKPS PAPER", "CKCS PAPER"]
    tabs = ctk.CTkTabview(top, fg_color="#242424")
    tabs.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    frames = {scope: tabs.add(scope) for scope in scopes}
    app.portfolio_tabs = tabs
    app.portfolio_trees = {}
    app.portfolio_summary_labels = {}

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

    def make_summary(parent):
        bar = ctk.CTkFrame(parent, fg_color="#1a1a1a", corner_radius=6)
        bar.pack(fill="x", padx=2, pady=(2, 6))
        labels = []
        colors = ["#00C853", "#90CAF9", "#FFCC80", "#80DEEA", "#FFD7A0", "#A5D6A7"]
        for color in colors:
            label = ctk.CTkLabel(
                bar,
                text="--",
                font=("Roboto", 13, "bold"),
                text_color=color,
            )
            label.pack(side="left", padx=10, pady=7)
            labels.append(label)
        return labels

    def make_tree(parent, derivative=False):
        holder = ctk.CTkFrame(parent, fg_color="#2b2b2b")
        holder.pack(fill="both", expand=True)
        if derivative:
            cols = ("Symbol", "Side", "Qty", "Entry", "Price", "SL", "TP", "Margin", "PnL", "Status")
            headers = ("Mã", "Chiều", "HĐ", "Giá vào", "Giá hiện tại", "SL", "TP", "Ký quỹ ước tính", "Lãi/Lỗ", "Trạng thái")
            widths = (150, 100, 90, 150, 170, 130, 130, 210, 190, 240)
        else:
            cols = ("Symbol", "Qty", "Sellable", "Pending", "AvgCost", "Price", "Value", "PnL", "Note")
            headers = ("Mã", "KL sở hữu", "KL bán được", "Chờ về", "Giá vốn", "Giá hiện tại", "Giá trị", "Lãi/Lỗ (%)", "Ghi chú")
            widths = (150, 170, 190, 140, 170, 170, 230, 250, 340)
        tree = ttk.Treeview(
            holder,
            columns=cols,
            show="headings",
            style="Portfolio.Treeview",
            selectmode="browse",
        )
        for tag, bg, fg in (
            ("profit_row", "#234d20", "#e0e0e0"),
            ("loss_row", "#5c1a1b", "#e0e0e0"),
            ("flat_row", "#2b2b2b", "#e0e0e0"),
            ("odd_lot", "#5c3a17", "#FFD7A0"),
            ("buy_row", "#234d20", "#e0e0e0"),
            ("sell_row", "#5c1a1b", "#e0e0e0"),
        ):
            tree.tag_configure(tag, background=bg, foreground=fg)
        for col, heading, width in zip(cols, headers, widths):
            tree.heading(col, text=heading)
            tree.column(
                col,
                width=width,
                minwidth=width,
                anchor="w" if col in ("Note", "Status") else "center",
                stretch=False,
            )
        scroll_y = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        scroll_x = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        return tree

    for scope in scopes:
        app.portfolio_summary_labels[scope] = make_summary(frames[scope])
        app.portfolio_trees[scope] = make_tree(
            frames[scope],
            derivative=scope.startswith("CKPS"),
        )
    initial_scope = (
        ("CKPS" if app._is_derivative_symbol(app.cbo_symbol.get()) else "CKCS")
        + (" PAPER" if getattr(config, "PAPER_TRADING", True) else " REAL")
    )
    tabs.set(initial_scope)
    app.tree_portfolio = None

    def _on_close():
        app.portfolio_popup = None
        app.tree_portfolio = None
        app.portfolio_tabs = None
        app.portfolio_trees = {}
        app.portfolio_summary_labels = {}
        try:
            top.destroy()
        except Exception:
            pass

    top.protocol("WM_DELETE_WINDOW", _on_close)
    app.update_portfolio_table(force_portfolio=True)



