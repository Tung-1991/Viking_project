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
from .view import COL_GRAY, COL_GREEN, COL_TEXT, _compact_vnd, _display_price, _number, _price_unit
from .windows import HINT_FONT, _HoverHint, _window


def _read_position_context(app, action: dict):
    symbol, mode = str(action.get("symbol", "")).upper(), str(action.get("mode", "PAPER")).upper()
    row = action.get("position") or {}
    cycle = app.trade_state.get(str(action.get("trade_id", ""))) if action.get("trade_id") else None
    if not cycle:
        cycle = app.trade_state.active_for(symbol, mode, str(row.get("loanPackageId", "")))
    return cycle, app._shared_tick(symbol) or {}


def _position_window(app):
    top = _window(app, "Quản lý vị thế", "640x300")
    top.minsize(620, 1)
    top.resizable(True, False)
    top.configure(fg_color="#181B20")
    top.transient(app)
    top.grab_set()
    top.grid_columnconfigure((0, 1), weight=1, uniform="position-panels")
    top.bind("<Escape>", lambda _event: top.destroy())
    return top


def show_position_management(app, action: dict) -> None:
    executor = getattr(app, "_position_read_executor", None)
    if executor is None:
        context = _read_position_context(app, action)
        _populate_position_management(app, action, _position_window(app), *context)
        return

    # Display the window immediately. Account locks and JSON reads stay off Tk,
    # on a display-only worker independent of broker/order requests.
    top = _position_window(app)
    loading = ctk.CTkLabel(top, text="ĐANG ĐỌC VỊ THẾ…", font=("Segoe UI", 14, "bold"))
    loading.grid(row=0, column=0, columnspan=2, pady=40)
    close = ctk.CTkButton(top, text="ĐÓNG", width=90, height=32,
                         fg_color="#3A3F47", command=top.destroy)
    close.grid(row=1, column=0, columnspan=2, sticky="e", padx=12, pady=12)
    future = executor.submit(_read_position_context, app, action)
    top._viking_load_future = future
    top._viking_load_poll = None

    def poll():
        top._viking_load_poll = None
        if not future.done():
            top._viking_load_poll = top.after(20, poll)
            return
        try:
            context = future.result()
        except Exception:
            loading.configure(text="KHÔNG ĐỌC ĐƯỢC VỊ THẾ · ĐÓNG VÀ MỞ LẠI", text_color="#EF4444")
            logger = getattr(app, "logger", None)
            if logger is not None:
                logger.exception("Position display read failed")
            return
        loading.destroy()
        close.destroy()
        _populate_position_management(app, action, top, *context)

    def cancel_read(event):
        if event.widget is top:
            if top._viking_load_poll is not None:
                top.after_cancel(top._viking_load_poll)
                top._viking_load_poll = None
            future.cancel()
    top.bind("<Destroy>", cancel_read, add="+")
    top._viking_load_poll = top.after(20, poll)


def _populate_position_management(app, action: dict, top, cycle, tick) -> None:
    symbol, mode = str(action.get("symbol", "")).upper(), str(action.get("mode", "PAPER")).upper()
    row = action.get("position") or {}
    quantity = min(int(_number(row.get("openQuantity", row.get("quantity", 0)))) or (cycle.open_quantity if cycle else 0),
                   cycle.open_quantity if cycle else int(_number(row.get("openQuantity", row.get("quantity", 0)))))
    entry = cycle.avg_entry_price if cycle else _price_unit(row.get("costPrice", row.get("averagePrice", 0)))
    price = _price_unit(tick.get("price") or row.get("marketPrice") or entry)
    sellable = max(0, min(quantity, int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0))))))
    rule_params = position_exit_params(app.settings.rule_parameters)
    params = position_exit_params(app.settings.rule_parameters, cycle)
    overrides = dict(cycle.exit_rule_overrides) if cycle else {}
    modes = set(cycle.em_modes) if cycle else set()
    pnl = (cycle.net_pnl if cycle else -_number(row.get("buyFee", 0))) + (price - entry) * quantity * 1000
    capital = cycle.buy_notional if cycle else entry * quantity * 1000
    def hint(widget, text):
        return _HoverHint(widget, text, placement="inside", font=HINT_FONT,
                          max_width=round(580 * top._get_window_scaling()), padding=(14, 10))
    def hint_icon(parent, text):
        icon = ctk.CTkLabel(parent, text="?", width=22, height=22, corner_radius=11,
                           fg_color="#2B6CB0", text_color="#E8EBEF", cursor="hand2",
                           font=("Segoe UI", 12, "bold"))
        hint(icon, text)
        return icon
    position_panel = ctk.CTkFrame(top, fg_color="#22262D", corner_radius=8,
                                border_width=1, border_color="#30353D", width=1, height=1)
    position_panel.grid(row=0, column=0, sticky="nsew", padx=(12, 4), pady=(10, 4))
    exit_panel = ctk.CTkFrame(top, fg_color="#22262D", corner_radius=8,
                            border_width=1, border_color="#30353D", width=1, height=1)
    exit_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 12), pady=(10, 4))
    header = ctk.CTkFrame(position_panel, fg_color="transparent", height=1)
    header.pack(fill="x", padx=10, pady=(8, 4))
    header.grid_columnconfigure(0, weight=1)
    title = ctk.CTkLabel(header, text=f"{symbol} · {mode} · {quantity:,} CP", height=24, font=("Segoe UI", 14, "bold"), anchor="w")
    title.grid(row=0, column=0, sticky="w")
    ticket_hint = f"#{cycle.id if cycle else 'Ngoài hệ thống'}\nMở {app._row_time(cycle.opened_at if cycle else row.get('openedAt', ''))}"
    hint(title, ticket_hint)
    hint_icon(header, ticket_hint).grid(row=0, column=1, padx=(8, 0))
    bot_row = ctk.CTkFrame(position_panel, fg_color="transparent", height=1)
    bot_row.pack(fill="x", padx=10, pady=(0, 4))
    bot_on = tk.BooleanVar(master=top, value=bool(not cycle or cycle.bot_trade_enabled and cycle.auto_sell_enabled))
    bot_button = ctk.CTkButton(
        bot_row, text="", width=146, height=32, corner_radius=7,
        font=("Segoe UI", 11, "bold"), text_color=COL_TEXT,
        command=lambda: bot_on.set(not bot_on.get()),
    )
    bot_button.pack(side="left")
    def refresh_bot(*_args):
        enabled = bot_on.get()
        bot_button.configure(
            text="BOT · ON" if enabled else "BOT · OFF",
            fg_color=COL_GREEN if enabled else COL_GRAY,
            hover_color="#16A34A" if enabled else "#4B515B",
        )
    bot_on.trace_add("write", refresh_bot)
    refresh_bot()
    hint(bot_button, "ON (xanh): cho phép BOT giao dịch vị thế theo cấu hình chính và chế độ bên dưới.\n"
               "OFF (xám): chỉ báo; chặn BUY thêm và mọi SELL tự động, giữ tín hiệu/Telegram và bán tay.\n"
               "Chỉ đổi vị thế này, không đổi RULE hay BOT chung. Lệnh đã gửi DNSE cần kiểm tra/hủy riêng.")
    summary = ctk.CTkFrame(position_panel, fg_color="transparent", width=1, height=1)
    summary.pack(fill="x", padx=10, pady=(0, 6))
    ctk.CTkLabel(summary, text=f"Vốn {_display_price(entry)} · Giá {_display_price(price)}",
                 height=20, font=("Segoe UI", 12), anchor="w").pack(fill="x")
    ctk.CTkLabel(summary, text=f"PnL {_compact_vnd(pnl)} ({pnl / capital * 100 if capital else 0:+.2f}%)",
                 height=20, font=("Segoe UI", 12, "bold"), text_color="#22C55E" if pnl >= 0 else "#EF4444", anchor="w").pack(fill="x")
    ctk.CTkLabel(summary, text=f"Bán được {sellable:,} · Chờ về {quantity - sellable:,} CP" +
                 (f" · {row.get('settleDate', '')[:10]}" if quantity > sellable and row.get("settleDate") else ""),
                 height=20, font=("Segoe UI", 12), anchor="w").pack(fill="x")
    targets = ctk.CTkFrame(position_panel, fg_color="transparent", width=1, height=1)
    targets.pack(fill="x", padx=10, pady=(0, 8))
    targets.grid_columnconfigure(0, weight=1)
    tp_on = tk.BooleanVar(master=top, value=bool(cycle and (cycle.tp_mode != "NONE" or "TP" in modes)))
    sl_on = tk.BooleanVar(master=top, value=cycle.sl_enabled if cycle else True)
    switches, entries, previews = {}, {}, {}
    for label, column, variable, color in (("TP", 0, tp_on, "#22C55E"), ("SL", 1, sl_on, "#EF4444")):
        cell = ctk.CTkFrame(targets, fg_color="transparent", width=1, height=1)
        cell.grid(row=column, column=0, sticky="ew", pady=(4, 2))
        cell.grid_columnconfigure(1, weight=1)
        switch = ctk.CTkSwitch(cell, text=label, variable=variable, width=62,
                              switch_width=30, switch_height=16, font=("Segoe UI", 12, "bold"), text_color=color)
        switch.grid(row=0, column=0, sticky="w", padx=(0, 6))
        switches[label] = switch
        value = "AUTO"
        if cycle:
            target_mode, target_value = (cycle.tp_mode, cycle.tp_value) if label == "TP" else (cycle.sl_mode, cycle.sl_value)
            if target_mode == "PRICE":
                value = _display_price(target_value)
            elif target_mode == "PERCENT":
                value = f"{'-' if label == 'SL' else ''}{abs(target_value):g}%"
        field = ctk.CTkEntry(cell, width=100, height=28, font=("Segoe UI", 12), placeholder_text="Giá hoặc %")
        field.insert(0, value)
        field.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        entries[label] = field
        def target_hint(label=label):
            baseline = rule_params["take_profit_pct" if label == "TP" else
                                   "reentry_sl_pct" if cycle and cycle.is_reentry else "initial_sl_pct"]
            return (f"{label} · RULE → E/M: {baseline:+g}%.\n"
                    f"Nhập giá (23,500) hoặc % ({'-3.5%' if label == 'SL' else '7%'}).\n"
                    "AUTO: theo ngưỡng RULE và nhận thay đổi mới. Nhập riêng: ghi đè, tô vàng dù trùng RULE.")
        hint(field, target_hint)
        hint_icon(cell, target_hint).grid(row=0, column=2)
        preview = ctk.CTkLabel(cell, text="", width=1, height=22, font=("Segoe UI", 12), text_color=color, anchor="w")
        preview.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        previews[label] = preview
    ctk.CTkLabel(exit_panel, text="CHẾ ĐỘ THOÁT", height=24,
                 font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 4))
    controls = ctk.CTkFrame(exit_panel, fg_color="transparent", width=1, height=1)
    controls.pack(fill="both", expand=True, padx=10, pady=(0, 8))
    controls.grid_columnconfigure(0, weight=1)
    controls.grid_rowconfigure((0, 1, 2), weight=1, uniform="position-policies")
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
    def inherited_policy(stage, key):
        if stage == "DYN" and not rule_params.get("normal_dynamic_enabled"):
            return "OFF"
        return rule_params[key]
    def follows_rule(value):
        return value == "Theo RULE" or value.endswith(" · RULE")
    for index, (stage, key, label) in enumerate(names):
        active = ("IND_EXIT" in modes if stage == "E" else
                  "NORMAL" in modes and overrides.get("normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled") is not False)
        initial = (overrides.get(key, "Theo RULE") if active else "OFF")
        variable = tk.StringVar(master=top, value=initial)
        policies[stage] = variable
        group = ctk.CTkFrame(controls, fg_color="transparent", width=1, height=1)
        group.grid(row=index, column=0, sticky="nsew", pady=(0, 4))
        group.grid_columnconfigure(1, weight=1)
        caption = ctk.CTkLabel(group, text=label, width=74, height=22, anchor="w", font=("Segoe UI", 12, "bold"))
        caption.grid(row=0, column=0, sticky="w")
        menu = ctk.CTkOptionMenu(group, values=["Theo RULE", "OFF", "ALERT", "AUTO"], variable=variable,
                               width=116, height=28, dynamic_resizing=False, font=("Segoe UI", 12))
        menu.grid(row=0, column=1, sticky="e", padx=8)
        detail = ctk.CTkLabel(group, text=summaries[stage], width=1, height=22,
                             font=("Segoe UI", 12), anchor="w", justify="left", wraplength=0)
        detail.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 2))
        def show_policy(*_args, variable=variable, detail=detail, stage=stage, key=key):
            summaries.update(rule_summaries())
            inherited = inherited_policy(stage, key)
            inherited_label = f"{inherited} · RULE"
            menu_for_policy = policies[stage]._viking_menu
            menu_for_policy.configure(values=[inherited_label, "OFF", "ALERT", "AUTO"])
            if follows_rule(variable.get()) and variable.get() != inherited_label:
                variable.set(inherited_label)
            explicit = not follows_rule(variable.get())
            if variable.get() == "OFF":
                default_modes = set(app.settings.bot_em_modes)
                switch_key = "normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled"
                explicit = ((key in overrides or "IND_EXIT" in default_modes) if stage == "E" else
                            switch_key in overrides or "NORMAL" in modes or
                            "NORMAL" in default_modes and (stage != "DYN" or rule_params["normal_dynamic_enabled"]))
            detail.configure(text=f"RULE {inherited} · {summaries[stage]}",
                             text_color="#F59E0B" if explicit else "#E8EBEF")
            menu_for_policy.configure(fg_color="#F59E0B" if explicit else "#2B6CB0",
                                     button_color="#D97706" if explicit else "#245C92",
                                     text_color="#171B21" if explicit else "#E8EBEF")
        variable._viking_menu = menu
        variable.trace_add("write", show_policy)
        policy_refreshers.append(show_policy)
        show_policy()
        policy_hint = ("AUTO/ALERT/OFF · RULE: chế độ thực tế từ cấu hình chính, tự nhận thay đổi mới.\n"
                   "OFF: không theo dõi nhánh này. ALERT: chỉ báo. AUTO: tự bán.\n"
                   "Lựa chọn chỉ áp dụng vị thế đang mở; không đổi mã khác.")
        hint(menu, policy_hint)
        detail_hint = lambda stage=stage: rule_summaries()[stage] + "\nNguồn: RULE → E/M.\n" + policy_hint
        hint(detail, detail_hint)
        hint_icon(group, detail_hint).grid(row=0, column=2)
    status = ctk.CTkLabel(top, text="", height=20, font=("Segoe UI", 12), text_color="#F59E0B", anchor="w")
    status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 0))
    status.grid_remove()
    def show_status(text, color="#F59E0B"):
        status.configure(text=text, text_color=color)
        if text:
            status.grid()
        else:
            status.grid_remove()
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
            show_status("")
        except (ValueError, TypeError, OverflowError):
            show_status("TP hoặc SL không hợp lệ.")
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
        show_status("Theo RULE · BOT Bật · Bấm LƯU để áp dụng.", "#C5CBD4")
    def save():
        nonlocal cycle
        try:
            sl_mode, sl_value, _ = target(entries["SL"].get(), True)
            tp_mode, tp_value, _ = target(entries["TP"].get(), False)
        except (ValueError, TypeError, OverflowError):
            show_status("TP hoặc SL không hợp lệ.")
            return
        selected, saved_overrides = set(modes), dict(overrides)
        if tp_on.get():
            selected.add("TP")
        else:
            selected.discard("TP")
            tp_mode, tp_value = "NONE", 0.
        for stage, key, _label in names:
            value = policies[stage].get()
            if follows_rule(value):
                saved_overrides.pop(key, None)
            elif value in {"AUTO", "ALERT"}:
                saved_overrides[key] = value
            if stage == "E":
                selected.add("IND_EXIT") if value not in {"OFF", "OFF · RULE"} else selected.discard("IND_EXIT")
            else:
                switch_key = "normal_arm_enabled" if stage == "ARM" else "normal_dynamic_enabled"
                if follows_rule(value):
                    saved_overrides.pop(switch_key, None)
                elif value == "OFF":
                    saved_overrides[switch_key] = False
                elif value in {"AUTO", "ALERT"}:
                    saved_overrides[switch_key] = True
        if policies["ARM"].get() not in {"OFF", "OFF · RULE"} or policies["DYN"].get() not in {"OFF", "OFF · RULE"}:
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
                show_status("Không đủ giá vốn/khối lượng để quản lý.")
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
            show_status(str(exc))
            return
        if broker_pending:
            messagebox.showwarning("Lệnh tại DNSE", "Đã chặn lệnh tự động mới. Còn lệnh broker cần kiểm tra/hủy riêng.", parent=top)
        app._log(f"{symbol} · Đã lưu TP/SL và chế độ ARM/DYNAMIC/E")
        app._refresh_local()
        top.destroy()
    footer = ctk.CTkFrame(top, fg_color="transparent")
    footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 10))
    footer.grid_columnconfigure(1, weight=1)
    if cycle:
        repair_row = ctk.CTkFrame(footer, fg_color="transparent", height=1)
        repair_row.grid(row=0, column=0, sticky="w")
        def reload_history():
            from ..trading.position_history import session_count
            try:
                count = session_count(session_entry.get())
            except ValueError as exc:
                show_status(str(exc))
                return
            top.destroy()
            app._restart_position_metrics({**action, "trade_id": cycle.id}, sessions=count)
        repair = ctk.CTkButton(repair_row, text="Đo lại MAE/MFE", width=114, height=28, font=("Segoe UI", 11), fg_color="#3A3F47",
                              command=reload_history)
        repair.pack(side="left")
        ctk.CTkLabel(repair_row, text="Số phiên:", font=("Segoe UI", 11), height=28).pack(side="left", padx=(8, 4))
        session_entry = ctk.CTkEntry(repair_row, width=40, height=28, font=("Segoe UI", 12), justify="center")
        session_entry.insert(0, "3")
        session_entry.pack(side="left")
        history_hint = ("Tải đỉnh/đáy từ nến phút của số phiên giao dịch gần nhất (1–30, mặc định 3),\n"
                        "gồm phiên hôm nay nếu đã bắt đầu; loại giá trước lúc mua.\n"
                        "Bỏ nến phút chứa thời điểm mua. Giữ nguyên cổ/fill; tự bán tạm dừng sau xác nhận.")
        hint(repair, history_hint)
        hint(session_entry, history_hint)
    reset = ctk.CTkButton(footer, text="VỀ RULE", width=90, height=28, font=("Segoe UI", 11), fg_color="#3A3F47", command=reset_to_rule)
    reset.grid(row=0, column=1, sticky="w", padx=(6, 8))
    hint(reset, "Bỏ ghi đè: TP/SL AUTO; bật/tắt theo RULE → THỰC THI, chế độ theo RULE → E/M.\n"
               "BOT vị thế trở về Bật. Chỉ áp dụng khi bấm LƯU. Giữ nguyên cổ, fill và MAE/MFE.")
    ctk.CTkButton(footer, text="LƯU", width=90, height=28, font=("Segoe UI", 12, "bold"), fg_color="#16A34A", command=save).grid(row=0, column=2)
    ctk.CTkButton(footer, text="ĐÓNG", width=80, height=28, font=("Segoe UI", 11), fg_color="#3A3F47", command=top.destroy).grid(row=0, column=3, padx=(8, 0))
    def fit_height(_event=None):
        scale = top._get_window_scaling()
        minimum_width = max(620, math.ceil(top.winfo_reqwidth() / scale))
        width = max(round(top._current_width), minimum_width)
        height = math.ceil(top.winfo_reqheight() / scale)
        top.minsize(minimum_width, 1)
        if width != round(top._current_width) or height != round(top._current_height):
            top.geometry(f"{width}x{height}")
    position_panel.bind("<Configure>", fit_height, add="+")
    exit_panel.bind("<Configure>", fit_height, add="+")
    footer.bind("<Configure>", fit_height, add="+")
    layout_job = top.after_idle(fit_height)
    top.bind("<Destroy>", lambda event: top.after_cancel(layout_job) if event.widget is top else None, add="+")
