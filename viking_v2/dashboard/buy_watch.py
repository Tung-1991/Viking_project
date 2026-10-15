"""Review the quantity, LO price and session before activating a passive BUY."""
from datetime import datetime

import customtkinter as ctk
import tkinter as tk

from ..trading.buy_watch import watch_armable
from ..trading.portfolio import board_price
from .view import _display_price


def show_arm_buy(app, action):
    item = app.queue.get(str(action.get("local_id", "")))
    if not item or not watch_armable(item, app.queue._now()):
        return
    watch = item.details["buy_watch"]
    day = datetime.fromisoformat(watch["session_date"]).strftime("%d/%m/%Y")
    tick = app._shared_tick(item.symbol) or {}
    price = item.limit_price or float(item.details.get("signal_price", 0) or 0) or board_price(tick.get("price", 0))
    top = ctk.CTkToplevel(app)
    top.title(f"Bật mua tay · {item.symbol}")
    top.geometry("600x430")
    top.minsize(580, 430)
    top.transient(app)
    top.grab_set()
    top.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(top, text=f"▶ MUA TAY · {item.symbol} · {item.execution_mode}", font=("Segoe UI", 16, "bold"), text_color="#5EEAD4").grid(
        row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 8))
    ctk.CTkLabel(top, text=f"Chỉ gửi LO trong phiên liên tục ngày {day} (1 phiên kế tiếp).\n"
        "Không cần tín hiệu BUY mới; được trước 14h và khi MUA AUTO OFF.\n"
        "Giá LO do Ngài chọn, không tự nâng giá.\n"
        "Khớp tính 1 lượt BUY, quản lý SL/TP/Protect theo cấu hình.\n"
        "Khớp 0: hết phiên thì hết hiệu lực, không tự mua lại.", justify="left", anchor="w", wraplength=560).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=5)
    quantity = tk.StringVar(master=top, value=str(item.quantity))
    limit_price = tk.StringVar(master=top, value=_display_price(price) if price > 0 else "")
    for row, label, variable in ((2, "Khối lượng (CP)", quantity), (3, "Giá LO (đồng/CP)", limit_price)):
        ctk.CTkLabel(top, text=label).grid(row=row, column=0, sticky="w", padx=18, pady=7)
        ctk.CTkEntry(top, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=18, pady=7)
    status = ctk.CTkLabel(top, text="Nhấn BẬT MUA TAY mới xác nhận.\nLO chỉ khớp khi có bên bán phù hợp.", wraplength=560, text_color="#F59E0B")
    status.grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=8)
    busy = False

    def submit():
        nonlocal busy
        if busy:
            return
        try:
            chosen_quantity = int(quantity.get().replace(",", "").strip())
            chosen_price = board_price(float(limit_price.get().replace(",", "").strip()))
        except (ValueError, TypeError, OverflowError):
            status.configure(text="Nhập khối lượng và giá LO hợp lệ.")
            return
        busy = True
        button.configure(state="disabled")
        modes, sl_enabled = list(app.settings.bot_em_modes), bool(app.settings.bot_sl_enabled)
        future = app._io_executor.submit(lambda: app.execution.arm_zero_fill_buy(item.id,
            quantity=chosen_quantity, limit_price=chosen_price, em_modes=modes, sl_enabled=sl_enabled))

        def ready(result):
            try:
                intent, error = result.result(), ""
            except Exception as exc:
                intent, error = None, str(exc) if isinstance(exc, ValueError) else "Chưa bật được mua; kiểm tra kết nối và thử lại."
            def apply():
                nonlocal busy
                busy = False
                if error:
                    if top.winfo_exists():
                        status.configure(text=error)
                        button.configure(state="normal")
                    return
                app._log(f"{intent.execution_mode} {intent.symbol} · BẬT MUA TAY · LO {intent.quantity:,} @ {_display_price(intent.limit_price)} · phiên {day}")
                app._refresh_local()
                if top.winfo_exists():
                    top.destroy()
            app._post_ui(apply)
        future.add_done_callback(ready)

    button = ctk.CTkButton(top, text="BẬT MUA TAY", command=submit, fg_color="#15803D")
    button.grid(row=5, column=1, sticky="e", padx=18, pady=10)
    ctk.CTkButton(top, text="ĐÓNG", command=top.destroy, fg_color="#3A3F47").grid(row=5, column=0, padx=18, pady=10)
