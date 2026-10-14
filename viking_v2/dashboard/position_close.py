"""Review selected positions, then enqueue explicitly confirmed manual sells."""
from __future__ import annotations

import customtkinter as ctk
import tkinter as tk

from ..models import OrderIntent
from ..trading.orders import FINAL_STATUSES, OrderQueue
from ..trading.portfolio import round_lot_down
from .view import _display_price, _number
from .windows import _HoverHint


def close_rows(app, actions) -> list[dict]:
    rows, seen = [], set()
    pending = app.queue.list_all()
    for action in actions:
        if action.get("kind") != "position":
            continue
        symbol, mode = action["symbol"], action["mode"]
        trade_id = str(action.get("trade_id", ""))
        package = str(action.get("loan_package_id", ""))
        cycle = app.trade_state.get(trade_id) if trade_id else None
        if trade_id and (not cycle or cycle.status != "OPEN" or cycle.symbol != symbol or cycle.execution_mode != mode):
            continue
        if cycle and cycle.loan_package_id:
            if package and package != cycle.loan_package_id:
                continue
            package = cycle.loan_package_id
        key = (mode, symbol, trade_id, package)
        if key in seen:
            continue
        seen.add(key)
        positions = app.snapshots.get(mode, ({}, [], []))[1]
        matching = [row for row in positions if str(row.get("symbol", "")).upper() == symbol and
                    (not package or str(row.get("loanPackageId", "")) == package)]
        if not matching:
            continue
        ambiguous = len(matching) != 1
        position = matching[0] if not ambiguous else action.get("position", {})
        held = int(_number(position.get("openQuantity", position.get("quantity", 0))))
        if cycle:
            held = min(held, cycle.open_quantity)
        available = max(0, min(held, int(_number(position.get("tradeQuantity", position.get("sellableQuantity", 0))))))
        working = [item for item in pending if item.execution_mode == mode and item.symbol == symbol
                   and item.side == "SELL" and item.status not in FINAL_STATUSES
                   and (not package or not item.loan_package_id or item.loan_package_id == package)]
        known_ids = {value for item in pending if item.execution_mode == mode
                     for value in [item.broker_order_id, *item.broker_order_ids] if value}
        external = [row for row in app.snapshots.get(mode, ({}, [], []))[2]
                    if row.get("symbol") == symbol and str(row.get("side", "")).upper() in {"SELL", "NS"}
                    and str(row.get("id", row.get("orderId", ""))) not in known_ids
                    and OrderQueue._normalized_broker_status(str(row.get("orderStatus", row.get("status", "")))) not in FINAL_STATUSES
                    and (not package or str(row.get("loanPackageId", package)) == package)]
        quantity = 0 if working or external or ambiguous else round_lot_down(available)
        rows.append({**action, "loan_package_id": package, "quantity": quantity, "held": held, "sellable": available,
                     "waiting": max(0, held - available),
                     "note": "Chưa rõ vị thế/gói" if ambiguous else "Có SELL đang chờ" if working or external else
                             "Chờ cổ về" if quantity == 0 and available == 0 else "Chưa đủ lô 100" if quantity == 0 else "",
                     "cost": cycle.avg_entry_price if cycle else _number(position.get("costPrice")),
                     "price": _number(position.get("marketPrice"))})
    return rows


def submit_closes(app, reviewed: list[dict]) -> list:
    results = []
    # Re-read quantities and pending exits under the same transaction as queue
    # writes. Double clicks and an intervening automatic exit cannot oversell.
    with app.queue.store.database:
        latest = {(row["mode"], row["symbol"], row.get("trade_id", ""), row.get("loan_package_id", "")): row
                  for row in close_rows(app, reviewed)}
        for requested in reviewed:
            key = (requested["mode"], requested["symbol"], requested.get("trade_id", ""), requested.get("loan_package_id", ""))
            row = latest.pop(key, None)
            quantity = int(requested.get("quantity", 0))
            if not row or quantity <= 0 or quantity % 100 or quantity > row["quantity"]:
                raise ValueError(f"{requested['symbol']}: số cổ bán được hoặc lệnh chờ đã thay đổi; mở lại bảng.")
        for requested in reviewed:
            intent = OrderIntent.create(requested["symbol"], "SELL", int(requested["quantity"]), "MARKET",
                execution_mode=requested["mode"], source="MANUAL", action="CLOSE",
                trade_id=str(requested.get("trade_id", "")), reason="MANUAL_CLOSE_SELECTED",
                allow_ato=app.settings.allow_ato, allow_atc=app.settings.allow_atc)
            intent.loan_package_id = str(requested.get("loan_package_id", ""))
            cycle = app.trade_state.get(intent.trade_id) if intent.trade_id else None
            intent.deal_id = cycle.deal_id if cycle else str((requested.get("position") or {}).get("id", ""))
            results.append(app.execution.submit(intent, phase=app._current_market_phase(), process_immediately=False))
    return results


def show_close_positions(app, actions) -> None:
    rows = close_rows(app, actions)
    if not rows:
        return
    top = ctk.CTkToplevel(app)
    top.title("Đóng vị thế")
    top.geometry(f"620x{min(520, 140 + 40 * len(rows))}")
    top.transient(app)
    top.grab_set()
    top.grid_columnconfigure(0, weight=1)
    top.grid_rowconfigure(1, weight=1)
    ctk.CTkLabel(top, text=f"ĐÓNG VỊ THẾ · {len({row['symbol'] for row in rows})} MÃ", font=("Segoe UI", 16, "bold"),
                 anchor="w").grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
    body = ctk.CTkScrollableFrame(top, fg_color="#22262D")
    body.grid(row=1, column=0, sticky="nsew", padx=14, pady=4)
    body.grid_columnconfigure(1, weight=1)
    selections = []
    for index, row in enumerate(rows):
        selected = tk.BooleanVar(master=top, value=row["quantity"] > 0)
        check = ctk.CTkCheckBox(body, text=f"{row['symbol']} · {row['mode']}" +
                              (f" · Gói {row['loan_package_id']}" if row.get("loan_package_id") else ""), variable=selected,
                              width=145, height=26, font=("Segoe UI", 12, "bold"),
                              state="normal" if row["quantity"] else "disabled")
        check.grid(row=index, column=0, sticky="w", padx=8, pady=7)
        detail = f"Bán {row['quantity']:,} / giữ {row['held']:,} CP"
        if row["note"]:
            detail += f" · {row['note']}"
        elif row["waiting"]:
            detail += f" · Chờ về {row['waiting']:,}"
        label = ctk.CTkLabel(body, text=detail, font=("Segoe UI", 12), anchor="w")
        label.grid(row=index, column=1, sticky="ew", padx=8, pady=7)
        _HoverHint(label, "Bán tay theo giá thị trường, chỉ số cổ đã về theo lô 100.\n"
                   "Mã đang có SELL được bỏ qua để tránh bán trùng. Giá khớp do sàn quyết định.", placement="inside")
        selections.append((selected, row))
    status = ctk.CTkLabel(top, text="", height=22, font=("Segoe UI", 11), text_color="#F59E0B")
    status.grid(row=2, column=0, sticky="ew", padx=14)
    footer = ctk.CTkFrame(top, fg_color="transparent")
    footer.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 12))
    footer.grid_columnconfigure(0, weight=1)
    busy = False
    def submit():
        nonlocal busy
        if busy:
            return
        chosen = [row for selected, row in selections if selected.get() and row["quantity"]]
        if not chosen:
            status.configure(text="Chọn ít nhất một mã bán được.")
            return
        busy = True
        button.configure(state="disabled")
        try:
            results = submit_closes(app, chosen)
        except ValueError as exc:
            status.configure(text=str(exc))
            busy = False
            button.configure(state="normal")
            return
        for result in results:
            app._log(f"{result.symbol} · SELL {result.quantity:,} CP · {result.status}")
        app._refresh_local()
        top.destroy()
    button = ctk.CTkButton(footer, text="BÁN ĐÃ CHỌN", width=150, height=30,
                          font=("Segoe UI", 12, "bold"), fg_color="#B91C1C", command=submit)
    button.grid(row=0, column=1, padx=(8, 0))
    ctk.CTkButton(footer, text="ĐÓNG BẢNG", width=110, height=30,
                 fg_color="#3A3F47", command=top.destroy).grid(row=0, column=0, sticky="e")
