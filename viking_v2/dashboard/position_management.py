"""Compact TP/SL editing and ARM/DYNAMIC/E overrides for one position."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox
import uuid
import customtkinter as ctk

from ..config import save_settings
from ..services.runtime import RuntimeConfig
from ..trading.position_controls import position_exit_params, save_position_controls
from .view import _compact_vnd, _display_price, _number, _price_unit
from .windows import _HoverHint


def show_position_management(app, action: dict) -> None:
    symbol, mode = str(action.get("symbol", "")).upper(), str(action.get("mode", "PAPER")).upper()
    row = action.get("position") or {}
    cycle = app.trade_state.get(str(action.get("trade_id", ""))) if action.get("trade_id") else None
    if not cycle:
        cycle = app.trade_state.active_for(symbol, mode, str(row.get("loanPackageId", "")))
    quantity = min(int(_number(row.get("openQuantity", row.get("quantity", 0)))) or (cycle.open_quantity if cycle else 0),
                   cycle.open_quantity if cycle else int(_number(row.get("openQuantity", row.get("quantity", 0)))))
    entry = cycle.avg_entry_price if cycle else _price_unit(row.get("costPrice", row.get("averagePrice", 0)))
    tick = app._shared_tick(symbol) or {}
    price = _price_unit(tick.get("price") or row.get("marketPrice") or entry)
    sellable = max(0, min(quantity, int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0))))))
    rule_params = position_exit_params(app.settings.rule_parameters)
    params = position_exit_params(app.settings.rule_parameters, cycle)
    overrides = dict(cycle.exit_rule_overrides) if cycle else {}
    modes = set(cycle.em_modes) if cycle else set()
    pnl = (cycle.net_pnl if cycle else -_number(row.get("buyFee", 0))) + (price - entry) * quantity * 1000
    capital = cycle.buy_notional if cycle else entry * quantity * 1000
    top = ctk.CTkToplevel(app)
    top.title("Quản lý vị thế")
    top.geometry("620x330")
    top.minsize(580, 330)
    top.transient(app)
    top.grab_set()
    top.grid_columnconfigure(0, weight=1)
    header = ctk.CTkFrame(top, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
    header.grid_columnconfigure(0, weight=1)
    title = ctk.CTkLabel(header, text=f"{symbol} · {mode} · {quantity:,} CP", height=24, font=("Segoe UI", 16, "bold"), anchor="w")
    title.grid(row=0, column=0, sticky="w")
    _HoverHint(title, f"#{cycle.id if cycle else 'Ngoài hệ thống'}\nMở {app._row_time(cycle.opened_at if cycle else row.get('openedAt', ''))}", placement="inside")
    bot_on = tk.BooleanVar(master=top, value=bool(not cycle or cycle.bot_trade_enabled and cycle.auto_sell_enabled))
    bot_switch = ctk.CTkSwitch(header, text="BOT", variable=bot_on, width=75,
                              switch_width=32, switch_height=17, font=("Segoe UI", 11))
    bot_switch.grid(row=0, column=1, sticky="e")
    bot_state = ctk.CTkLabel(header, text="", height=20, width=46, font=("Segoe UI", 11))
    bot_state.grid(row=0, column=2, sticky="e")
    def refresh_bot(*_args):
        bot_state.configure(text="Bật" if bot_on.get() else "Alert", text_color="#C5CBD4" if bot_on.get() else "#F59E0B")
        bot_switch.configure(text_color="#E8EBEF" if bot_on.get() else "#F59E0B")
    bot_on.trace_add("write", refresh_bot)
    refresh_bot()
    _HoverHint(bot_switch, "Bật: cho phép BOT giao dịch vị thế theo cấu hình chính và chế độ bên dưới.\n"
               "Alert: chặn BUY thêm và mọi SELL tự động; giữ tín hiệu/Telegram và bán tay.\n"
               "Chỉ đổi vị thế này, không đổi RULE hay BOT chung. Lệnh đã gửi DNSE cần kiểm tra/hủy riêng.", placement="inside")
    summary = ctk.CTkFrame(top, fg_color="#22262D", corner_radius=7)
    summary.grid(row=1, column=0, sticky="ew", padx=12, pady=3)
    ctk.CTkLabel(summary, text=f"Vốn {_display_price(entry)} · Giá {_display_price(price)} · PnL {_compact_vnd(pnl)} ({pnl / capital * 100 if capital else 0:+.2f}%)",
                 height=20, font=("Segoe UI", 12), text_color="#22C55E" if pnl >= 0 else "#EF4444", anchor="w").pack(fill="x", padx=10, pady=(5, 0))
    ctk.CTkLabel(summary, text=f"Bán được {sellable:,} · Chờ về {quantity - sellable:,} CP" +
                 (f" · {row.get('settleDate', '')[:10]}" if quantity > sellable and row.get("settleDate") else ""),
                 height=20, font=("Segoe UI", 11), anchor="w").pack(fill="x", padx=10, pady=(0, 5))
    targets = ctk.CTkFrame(top, fg_color="#22262D", corner_radius=7)
    targets.grid(row=2, column=0, sticky="ew", padx=12, pady=3)
    targets.grid_columnconfigure((1, 3), weight=1)
    tp_on = tk.BooleanVar(master=top, value=bool(cycle and (cycle.tp_mode != "NONE" or "TP" in modes)))
    sl_on = tk.BooleanVar(master=top, value=cycle.sl_enabled if cycle else True)
    switches, entries, previews = {}, {}, {}
    for label, column, variable, color in (("TP", 0, tp_on, "#22C55E"), ("SL", 2, sl_on, "#EF4444")):
        switch = ctk.CTkSwitch(targets, text=label, variable=variable, width=70,
                              switch_width=30, switch_height=16, font=("Segoe UI", 12, "bold"), text_color=color)
        switch.grid(row=0, column=column, sticky="w", padx=(8, 4), pady=(6, 2))
        switches[label] = switch
        value = "AUTO"
        if cycle:
            target_mode, target_value = (cycle.tp_mode, cycle.tp_value) if label == "TP" else (cycle.sl_mode, cycle.sl_value)
            if target_mode == "PRICE":
                value = _display_price(target_value)
            elif target_mode == "PERCENT":
                value = f"{'-' if label == 'SL' else ''}{abs(target_value):g}%"
        field = ctk.CTkEntry(targets, height=28, font=("Segoe UI", 12), placeholder_text="Giá hoặc %")
        field.insert(0, value)
        field.grid(row=0, column=column + 1, sticky="ew", padx=(0, 8), pady=(6, 2))
        entries[label] = field
        def target_hint(label=label):
            baseline = rule_params["take_profit_pct" if label == "TP" else
                                   "reentry_sl_pct" if cycle and cycle.is_reentry else "initial_sl_pct"]
            return (f"{label} · RULE → E/M: {baseline:+g}%.\n"
                    f"Nhập giá (23,500) hoặc % ({'-3.5%' if label == 'SL' else '7%'}).\n"
                    "AUTO: theo ngưỡng RULE và nhận thay đổi mới. Nhập riêng: ghi đè, tô vàng dù trùng RULE.")
        _HoverHint(field, target_hint, placement="inside")
        preview = ctk.CTkLabel(targets, text="", height=22, font=("Segoe UI", 11), text_color=color, anchor="w")
        preview.grid(row=1, column=column, columnspan=2, sticky="ew", padx=8, pady=(0, 4))
        previews[label] = preview
    controls = ctk.CTkFrame(top, fg_color="#22262D", corner_radius=7)
    controls.grid(row=3, column=0, sticky="ew", padx=12, pady=3)
    controls.grid_columnconfigure(2, weight=1)
    policies = {}
    names = (("ARM", "normal_policy", "SAU ARM"), ("DYN", "dynamic_policy", "DYNAMIC"), ("E", "indicator_exit_policy", "E"))
    def rule_summaries():
        return {
        "ARM": f"ARM +{params.get('normal_arm_pct', 7):g}% · Lùi {params.get('normal_giveback_pct', 2.5):g}% · Bán {params.get('normal_sell_pct', 100):g}%",
        "DYN": f"ATR ×{params.get('normal_atr_activation_multiplier', .55):g}/{params.get('normal_atr_multiplier', .8):g} · Giữ {params.get('normal_retention_pct', 72):g}%",
        "E": f"EMA {params.get('sell_ema_fast', 3)}/{params.get('sell_ema_slow', 6)} · RSI {params.get('rsi_period', 14)} · Bán 100%",
        }
    summaries = rule_summaries()
    policy_refreshers = []
    for index, (stage, key, label) in enumerate(names):
        active = ("IND_EXIT" in modes if stage == "E" else
                  "NORMAL" in modes and overrides.get("normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled") is not False)
        initial = (overrides.get(key, "Theo RULE") if active else "OFF")
        variable = tk.StringVar(master=top, value=initial)
        policies[stage] = variable
        caption = ctk.CTkLabel(controls, text=label, width=85, height=22, anchor="w", font=("Segoe UI", 12, "bold"))
        caption.grid(row=index, column=0, sticky="w", padx=(9, 5), pady=4)
        menu = ctk.CTkOptionMenu(controls, values=["Theo RULE", "OFF", "ALERT", "AUTO"], variable=variable,
                               width=110, height=27, dynamic_resizing=False, font=("Segoe UI", 11))
        menu.grid(row=index, column=1, padx=(0, 8), pady=4)
        detail = ctk.CTkLabel(controls, text=summaries[stage], height=22, font=("Segoe UI", 11), anchor="w")
        detail.grid(row=index, column=2, sticky="ew", padx=(0, 8), pady=4)
        def show_policy(*_args, variable=variable, detail=detail, stage=stage, key=key):
            summaries.update(rule_summaries())
            explicit = variable.get() != "Theo RULE"
            inherited = rule_params[key]
            if stage == "DYN" and not rule_params.get("normal_dynamic_enabled"):
                inherited = "OFF"
            if variable.get() == "OFF":
                default_modes = set(app.settings.bot_em_modes)
                switch_key = "normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled"
                explicit = ((key in overrides or "IND_EXIT" in default_modes) if stage == "E" else
                            switch_key in overrides or "NORMAL" in modes or
                            "NORMAL" in default_modes and (stage != "DYN" or rule_params["normal_dynamic_enabled"]))
            if variable.get() == "Theo RULE":
                detail.configure(text=f"RULE {inherited} · {summaries[stage]}", text_color="#E8EBEF")
            else:
                detail.configure(text=f"RULE {inherited} · {summaries[stage]}", text_color="#F59E0B" if explicit else "#E8EBEF")
            menu_for_policy = policies[stage]._viking_menu
            menu_for_policy.configure(fg_color="#F59E0B" if explicit else "#2B6CB0",
                                     button_color="#D97706" if explicit else "#245C92",
                                     text_color="#171B21" if explicit else "#E8EBEF")
        variable._viking_menu = menu
        variable.trace_add("write", show_policy)
        policy_refreshers.append(show_policy)
        show_policy()
        _HoverHint(menu, "Theo RULE: lấy chế độ trong cấu hình chính, không ghi đè.\n"
                   "OFF: không theo dõi nhánh này. ALERT: chỉ báo. AUTO: tự bán.\n"
                   "Lựa chọn chỉ áp dụng vị thế đang mở; không đổi mã khác.", placement="inside")
        _HoverHint(detail, lambda stage=stage: rule_summaries()[stage] +
                   "\nNguồn: RULE → E/M. Ngưỡng lấy trực tiếp từ RULE; menu bên trái chỉ ghi đè chế độ vị thế.", placement="inside")
    status = ctk.CTkLabel(top, text="", height=20, font=("Segoe UI", 11), text_color="#F59E0B", anchor="w")
    status.grid(row=4, column=0, sticky="ew", padx=12, pady=(2, 0))
    def target(raw, stop):
        text = str(raw).strip().replace(",", "")
        if not text or text.upper() == "AUTO":
            pct = params.get("reentry_sl_pct" if cycle and cycle.is_reentry else "initial_sl_pct", -3.5) if stop else params.get("take_profit_pct", 7)
            return ("DEFAULT" if stop else "NONE"), 0., entry * (1 + float(pct) / 100)
        if text.endswith("%"):
            value = abs(float(text[:-1]))
            if not math.isfinite(value) or not 0 < value < (100 if stop else float("inf")):
                raise ValueError
            return "PERCENT", value, entry * (1 + (-value if stop else value) / 100)
        value = _price_unit(float(text))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return "PRICE", value, value
    def refresh(_event=None):
        try:
            for label, variable in (("TP", tp_on), ("SL", sl_on)):
                _, _, level = target(entries[label].get(), label == "SL")
                text = entries[label].get().strip().upper()
                explicit = text not in {"", "AUTO"} and (variable.get() or label == "SL")
                default_enabled = ("TP" in app.settings.bot_em_modes) if label == "TP" else app.settings.bot_sl_enabled
                changed = explicit or variable.get() != default_enabled
                base = rule_params["take_profit_pct" if label == "TP" else "reentry_sl_pct" if cycle and cycle.is_reentry else "initial_sl_pct"]
                previews[label].configure(text=(f"{_display_price(level)} · {_compact_vnd((level - entry) * quantity * 1000)}" if variable.get() else f"{label} OFF") +
                                          f" · RULE {base:+g}%",
                                          text_color="#F59E0B" if changed else "#22C55E" if label == "TP" else "#EF4444")
                entries[label].configure(text_color="#F59E0B" if explicit else "#E8EBEF",
                                         border_color="#F59E0B" if explicit else "#565B5E")
                switches[label].configure(text_color="#F59E0B" if changed else "#22C55E" if label == "TP" else "#EF4444")
            status.configure(text="")
        except (ValueError, TypeError, OverflowError):
            status.configure(text="TP hoặc SL không hợp lệ.", text_color="#F59E0B")
    for label in entries:
        entries[label].bind("<KeyRelease>", refresh)
        switches[label].configure(command=refresh)
    refresh()
    rule_refresh_job = None
    def refresh_rule():
        nonlocal rule_refresh_job
        latest = position_exit_params(app.settings.rule_parameters)
        if latest != rule_params:
            rule_params.clear()
            rule_params.update(latest)
            params.clear()
            params.update(position_exit_params(app.settings.rule_parameters, cycle))
            for update in policy_refreshers:
                update()
            refresh()
        rule_refresh_job = top.after(500, refresh_rule)
    def cleanup(event):
        if event.widget is top and rule_refresh_job is not None:
            top.after_cancel(rule_refresh_job)
    top.bind("<Destroy>", cleanup, add="+")
    rule_refresh_job = top.after(500, refresh_rule)
    def reset_to_rule():
        nonlocal modes, overrides
        latest = position_exit_params(app.settings.rule_parameters)
        rule_params.clear()
        rule_params.update(latest)
        params.clear()
        params.update(latest)
        modes = set(app.settings.bot_em_modes)
        overrides = {}
        for field in entries.values():
            field.delete(0, "end")
            field.insert(0, "AUTO")
        tp_on.set("TP" in modes)
        sl_on.set(app.settings.bot_sl_enabled)
        bot_on.set(True)
        for stage, variable in policies.items():
            tactic = "IND_EXIT" if stage == "E" else "NORMAL"
            variable.set("Theo RULE" if tactic in modes else "OFF")
        refresh()
        status.configure(text="Theo RULE · BOT Bật · Bấm LƯU để áp dụng.", text_color="#C5CBD4")
    def save():
        nonlocal cycle
        try:
            sl_mode, sl_value, _ = target(entries["SL"].get(), True)
            tp_mode, tp_value, _ = target(entries["TP"].get(), False)
        except (ValueError, TypeError, OverflowError):
            status.configure(text="TP hoặc SL không hợp lệ.", text_color="#F59E0B")
            return
        selected, saved_overrides = set(modes), dict(overrides)
        if tp_on.get():
            selected.add("TP")
        else:
            selected.discard("TP")
            tp_mode, tp_value = "NONE", 0.
        for stage, key, _label in names:
            value = policies[stage].get()
            if value == "Theo RULE":
                saved_overrides.pop(key, None)
            elif value in {"AUTO", "ALERT"}:
                saved_overrides[key] = value
            if stage == "E":
                selected.add("IND_EXIT") if value != "OFF" else selected.discard("IND_EXIT")
            else:
                switch_key = "normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled"
                if value == "Theo RULE":
                    saved_overrides.pop(switch_key, None)
                elif value == "OFF":
                    saved_overrides[switch_key] = False
                elif value in {"AUTO", "ALERT"}:
                    saved_overrides[switch_key] = True
        if policies["ARM"].get() != "OFF" or policies["DYN"].get() != "OFF":
            selected.add("NORMAL")
        else:
            selected.discard("NORMAL")
        # Preserve inherited OFF without stamping unnecessary overrides.
        for stage, key, _label in names:
            if (policies[stage].get() == "OFF" and "NORMAL" not in modes and stage != "E"
                    and policies["ARM"].get() == policies["DYN"].get() == "OFF"):
                saved_overrides.pop("normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled", None)
        if not cycle:
            if quantity <= 0 or entry <= 0:
                status.configure(text="Không đủ giá vốn/khối lượng để quản lý.")
                return
            if not messagebox.askyesno("Bắt đầu quản lý", f"Đưa vị thế {symbol} vào quản lý?", parent=top):
                return
            cycle = app.trade_state.create(symbol, mode, source="EXTERNAL_DNSE", trade_id=uuid.uuid4().hex,
                em_modes=sorted(selected), sl_enabled=sl_on.get(), auto_sell_enabled=bot_on.get(), bot_trade_enabled=bot_on.get(),
                sl_mode=sl_mode, sl_value=sl_value, tp_mode=tp_mode, tp_value=tp_value,
                loan_package_id=str(row.get("loanPackageId", "")), deal_id=str(row.get("id", "")))
            app.trade_state.record_buy_fill(cycle.id, quantity, entry, 0.)
            if symbol not in app.settings.watchlist:
                app.settings.watchlist.append(symbol)
                save_settings(app.settings, app.account_id)
                current = app.bridge.read_config()
                app.bridge.write_config(RuntimeConfig(app.settings.watchlist, current.paper_mode, current.bot_enabled))
        try:
            cycle, broker_pending = save_position_controls(app.trade_state, app.queue, cycle.id,
                em_modes=sorted(selected), sl_enabled=sl_on.get(), auto_sell_enabled=bot_on.get(), bot_trade_enabled=bot_on.get(),
                sl_mode=sl_mode, sl_value=sl_value, tp_mode=tp_mode, tp_value=tp_value, exit_rule_overrides=saved_overrides)
        except ValueError as exc:
            status.configure(text=str(exc))
            return
        if broker_pending:
            messagebox.showwarning("Lệnh tại DNSE", "Đã chặn lệnh tự động mới. Còn lệnh broker cần kiểm tra/hủy riêng.", parent=top)
        app._log(f"{symbol} · Đã lưu TP/SL và chế độ ARM/DYNAMIC/E")
        app._refresh_local()
        top.destroy()
    footer = ctk.CTkFrame(top, fg_color="transparent")
    footer.grid(row=5, column=0, sticky="ew", padx=12, pady=(4, 10))
    footer.grid_columnconfigure(1, weight=1)
    if cycle:
        repair = ctk.CTkButton(footer, text="Đo lại MAE/MFE", width=114, height=28, font=("Segoe UI", 11), fg_color="#3A3F47",
                              command=lambda: (top.destroy(), app._restart_position_metrics({**action, "trade_id": cycle.id})))
        repair.grid(row=0, column=0, sticky="w")
        _HoverHint(repair, "Đọc và xác nhận giá G1 trước khi bắt đầu khoảng đo mới. Giữ nguyên cổ/fill; tự bán tạm dừng.", placement="inside")
    reset = ctk.CTkButton(footer, text="VỀ RULE", width=90, height=28, font=("Segoe UI", 11), fg_color="#3A3F47", command=reset_to_rule)
    reset.grid(row=0, column=1, sticky="w", padx=(6, 8))
    _HoverHint(reset, "Bỏ ghi đè: TP/SL AUTO; bật/tắt theo RULE → THỰC THI, chế độ theo RULE → E/M.\n"
               "BOT vị thế trở về Bật. Chỉ áp dụng khi bấm LƯU. Giữ nguyên cổ, fill và MAE/MFE.", placement="inside")
    ctk.CTkButton(footer, text="LƯU", width=90, height=28, font=("Segoe UI", 12, "bold"), fg_color="#16A34A", command=save).grid(row=0, column=2)
    ctk.CTkButton(footer, text="ĐÓNG", width=80, height=28, font=("Segoe UI", 11), fg_color="#3A3F47", command=top.destroy).grid(row=0, column=3, padx=(8, 0))
