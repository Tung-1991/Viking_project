"""Explain position management from existing snapshots without changing trades."""
from datetime import datetime
import math

from ..trading.market import VN_TZ, STOCK_SETTLEMENT_RELEASE
from ..rules.observations import finite
from .view import _display_price, _number


def settlement_note(mode, settle, *, now=None):
    if not settle:
        return "Chưa có lịch về", "API chưa có ngày/giờ thanh toán cho phần cổ đang chờ."
    try:
        raw = str(settle).strip()
        target = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        has_time = len(raw) > 10
        if mode == "PAPER" and not has_time:
            target = datetime.combine(target.date(), STOCK_SETTLEMENT_RELEASE)
            has_time = True
        target = target.replace(tzinfo=VN_TZ) if target.tzinfo is None else target.astimezone(VN_TZ)
        now = now or datetime.now(VN_TZ)
        day = target.strftime("%d/%m")
        if not has_time:
            note = f"DỰ KIẾN {day}" if target.date() > now.date() else f"{day} · CHỜ DNSE XÁC NHẬN"
            return note, f"DNSE chỉ trả ngày {day}, chưa có giờ cổ được phép bán. Căn cứ số CP bán được từ API."
        minutes = math.ceil((target - now).total_seconds() / 60)
        when = target.strftime("%d/%m %H:%M")
        if minutes <= 0:
            return f"{when} · CHỜ CẬP NHẬT", "Đã tới mốc thanh toán; số cổ bán được vẫn chờ dữ liệu tài khoản cập nhật."
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        remaining = f"{days}n {hours}g" if days else f"{hours}g{minutes:02d}p" if hours else f"{minutes}p"
        origin = "PAPER theo lịch mô phỏng" if mode == "PAPER" else "DNSE theo giờ API cung cấp"
        return f"{when} · CÒN {remaining}", f"{origin}: {when}, còn {remaining}; hết giờ không tự xác nhận cổ đã về."
    except (ValueError, TypeError, OverflowError):
        return "Chờ xác nhận lịch về", "Ngày thanh toán chưa hợp lệ; giữ nguyên số cổ bán được từ API."


def position_status(*, symbol, mode, quantity, sellable, settle, price, entry, sl_enabled, sl_price,
                    tp_price, tp_pct, params, decision, normal_enabled, indicator_enabled, paused,
                    protect_mode, normal_state, history_untrusted, pending_close=None, metrics=None, now=None):
    details = decision.get("details") or {}
    pending = max(0, quantity - sellable)
    availability = f"BÁN ĐƯỢC {sellable:,} CP" + (f" · CHỜ VỀ {pending:,} CP" if pending else "")
    settlement = "Số cổ đã có thể bán theo snapshot tài khoản."
    if pending:
        note, settlement = settlement_note(mode, settle, now=now)
        availability += f" · {note}"
    sl_hit = sl_enabled and price > 0 and sl_price > 0 and price <= sl_price + 1e-9
    tp_hit = tp_price > 0 and price + 1e-9 >= tp_price
    sl = "SL TẮT" if not sl_enabled else "SL ĐÃ CHẠM" if sl_hit else "SL BẬT"
    tp = "TP TẮT" if tp_price <= 0 else f"TP {'ĐÃ CHẠM' if tp_hit else 'CHỜ'} +{tp_pct:g}%"
    policy = "ALERT" if paused else str(details.get("indicator_exit_policy", params.get("indicator_exit_policy", "ALERT"))).upper()
    e_signal = indicator_enabled and str(decision.get("signal", "")).upper() == "SELL"
    e = "E TẮT" if not indicator_enabled else f"E {policy}: {'có tín hiệu' if e_signal else 'chờ tín hiệu'}"
    floor = _number(details.get("normal_trigger_price"))
    stage = str(details.get("protection_stage", ""))
    mfe = finite(details.get("normal_mfe_pct"))
    metrics = metrics or {}
    saved_mfe = finite(metrics.get("peak_profit_pct", metrics.get("mfe_pct")))
    if mfe is None and saved_mfe is not None and metrics.get("price_history_verified") is True and not history_untrusted:
        from ..rules.business import protect_peak_for_sellable_phase
        mfe = protect_peak_for_sellable_phase(
            saved_mfe,
            _number(metrics.get("normal_sellable_peak_profit_pct")), _number(params.get("normal_arm_pct", 7)),
            reset_applied=bool(params.get("normal_dynamic_enabled", True) and params.get("normal_t2_reset_enabled")
                               and metrics.get("normal_t2_reset_applied")),
        )
    atr = _number(details.get("normal_atr_pct", details.get("atr14_daily_pct")))
    protect_hit = bool(normal_enabled and not history_untrusted and not details.get("protection_history_unverified")
                       and floor > 0 and price > 0
                       and price <= floor and normal_state in {"ARM", "DYN", "ALERT", "T+2", "WAITING_SETTLEMENT"})
    if not normal_enabled or protect_mode == "OFF":
        protection = "PROTECT TẮT" if not normal_enabled else f"PROTECT {stage or 'nhánh hiện tại'} TẮT"
    elif history_untrusted or details.get("protection_history_unverified"):
        protection = f"PROTECT {protect_mode} · Chờ xác minh đỉnh/đáy; cần đo lại MAE/MFE"
    elif normal_state == "DONE":
        protection = f"PROTECT {protect_mode} · Đã hoàn tất lượt bảo vệ đã cấu hình"
    elif normal_state == "REARM":
        protection = f"PROTECT {protect_mode} · Chờ điều kiện lặp lại sau lần báo/bán trước"
    elif protect_hit:
        protection = f"PROTECT {stage or 'DYNAMIC'} {protect_mode} · Đã chạm {_display_price(floor)}"
    elif normal_state == "ALERT":
        protection = f"PROTECT {protect_mode} · Đã cảnh báo lượt này; chờ điều kiện tiếp"
    elif normal_state in {"ARM", "DYN"} and floor > 0:
        protection = f"PROTECT {'SAU ARM' if normal_state == 'ARM' else 'DYNAMIC'} {protect_mode} · Theo đỉnh, mốc bán {_display_price(floor)}"
    elif not decision:
        protection = f"PROTECT {protect_mode} · Chờ đánh giá vị thế mới từ daemon"
    elif (params.get("normal_dynamic_enabled", True) and atr <= 0
          and (params.get("normal_atr_activation_enabled", True)
               or params.get("normal_atr_trail_enabled", True) and not params.get("normal_retention_enabled", True))):
        protection = f"PROTECT {protect_mode} · Chờ ATR14 từ nến ngày đã đóng"
    elif mfe is None:
        protection = f"PROTECT {protect_mode} · Chưa có đỉnh theo dõi của vị thế"
    else:
        activation = _number(details.get("normal_activation_mfe_pct"), atr * _number(params.get("normal_atr_activation_multiplier", .55)))
        arm = _number(params.get("normal_arm_pct", 7))
        if params.get("normal_dynamic_enabled", True) and activation > 0:
            condition = "Chưa START" if mfe + 1e-9 < activation else "Đạt START; chờ mốc bảo vệ"
            protection = f"PROTECT {protect_mode} · {condition}: MFE {mfe:+.2f}% / cần +{activation:.2f}%"
        else:
            condition = "Chưa ARM" if mfe + 1e-9 < arm else "Đạt ARM; chờ mốc bảo vệ"
            protection = f"PROTECT {protect_mode} · {condition}: MFE {mfe:+.2f}% / cần +{arm:g}%"
    controls = f"{sl} · {tp} · {e}"
    order = "Chưa có yêu cầu SELL đang chờ; tín hiệu không đồng nghĩa đã gửi hoặc đã khớp."
    trigger = " / ".join(label for hit, label in ((sl_hit, "SL ĐÃ CHẠM"), (tp_hit, f"TP ĐÃ CHẠM +{tp_pct:g}%"),
                         (e_signal, "E CÓ TÍN HIỆU"), (protect_hit, "PROTECT ĐÃ CHẠM")) if hit)
    if pending_close is not None:
        state = pending_close.status.upper()
        order = {"PENDING": "SELL đã xếp hàng, chưa gửi", "WAITING_TOKEN": "SELL chờ OTP, chưa gửi",
                 "WAITING_SETTLEMENT": "SELL chờ cổ về, chưa gửi", "SENDING": "SELL đang gửi, chưa xác nhận broker",
                 "WORKING": "SELL đã gửi, chờ khớp", "PARTIAL": "SELL đang khớp một phần",
                 "UNKNOWN": "SELL chưa rõ kết quả, cần đối soát", "CANCEL_PENDING": "SELL chờ xác nhận hủy",
                 "REPLACE_PENDING": "SELL chờ xác nhận sửa", "PAUSED": "SELL đang tạm dừng"}.get(state, f"SELL {state}")
        order += f" · Khớp {pending_close.filled_quantity:,}/{pending_close.quantity:,} CP"
        summary = order
    elif paused:
        summary = "TỰ BÁN TẠM DỪNG · " + (f"{trigger}; chưa tạo SELL" if trigger else "Chỉ báo; chưa tạo SELL")
    elif trigger and sellable < 100:
        summary = f"{trigger} · Chờ cổ bán được; chưa tạo SELL"
    elif trigger:
        summary = f"{trigger} · Chưa có yêu cầu SELL" + (" · Chỉ báo" if (e_signal and policy == "ALERT") or (protect_hit and protect_mode == "ALERT") else "")
    else:
        summary = controls
    sl_detail = f"SL {_display_price(sl_price)} · {sl}" if sl_enabled else sl
    tp_detail = f"TP {_display_price(tp_price)} · {tp}" if tp_price > 0 else tp
    if sl_enabled and not sl_hit and price > sl_price > 0:
        sl_detail += f" (cách {_display_price(price - sl_price)}đ)"
    if tp_price > 0 and not tp_hit and price > 0:
        tp_detail += f" (còn {_display_price(tp_price - price)}đ)"
    marks = details.get("indicators") or {}
    evidence = []
    if indicator_enabled and params.get("sell_signal_use_ema", True):
        fast, slow = finite(marks.get("sell_ema_fast")), finite(marks.get("sell_ema_slow"))
        if fast is not None and slow is not None:
            evidence.append(f"EMA {params.get('sell_ema_fast', 3)}/{params.get('sell_ema_slow', 6)}: "
                            f"{_display_price(fast)} {'<' if fast < slow else '>' if fast > slow else '='} {_display_price(slow)}")
    if indicator_enabled and params.get("sell_signal_use_rsi", True):
        rsi, previous = finite(marks.get("rsi")), finite(marks.get("rsi_previous"))
        if rsi is not None and previous is not None:
            evidence.append(f"RSI{params.get('rsi_period', 14)}: {previous:.2f} → {rsi:.2f}")
    if evidence:
        e += " · " + " · ".join(evidence)
    hints = [f"{symbol} · {mode} · GIỮ {quantity:,} CP · Giá {_display_price(price)} / vốn {_display_price(entry)}", availability, settlement,
             order, f"{sl_detail} · {tp_detail}", e, protection]
    if paused:
        hints.insert(4, "TỰ BÁN TẠM DỪNG: SL/TP/PROTECT/E chỉ báo; muốn tự bán cần bật lại BOT vị thế.")
    return "\n".join((availability, summary, protection)), "\n".join(hints)


def preview_position_status(view, symbol, status):
    """Use the same position status as the table, independently of a BUY draft."""
    from .account_money import displayed_account_snapshot
    from .view import _price_unit
    from ..trading.portfolio import account_price
    from ..trading.position_controls import position_exit_params, exit_stage_policy
    from ..trading.orders import FINAL_STATUSES
    from ..trading.validation import decision_is_fresh, decisions_for_mode

    if not hasattr(view, "snapshots"):
        return None
    mode = str(view.mode.get()).upper()
    _, rows, _ = displayed_account_snapshot(view, mode)
    matches = [row for row in rows if str(row.get("symbol", row.get("instrumentId", ""))).upper() == symbol
               and _number(row.get("openQuantity", row.get("quantity", 0))) > 0]
    if not matches:
        return None
    store = getattr(view, "trade_state", None)
    active = store.active_for(symbol, mode) if store else None
    # Multiple loan packages can hold the same symbol. Pick the exact cycle,
    # never combine settlement and SELL progress from different packages.
    row = next((row for row in matches if active and (
        str(row.get("tradeId", row.get("positionId", ""))) == active.id or
        getattr(active, "loan_package_id", "") and str(row.get("loanPackageId", "")) == active.loan_package_id)), matches[0])
    row_id = str(row.get("tradeId", row.get("positionId", "")))
    cycle = store.get(row_id) if store and row_id else None
    if cycle and (cycle.symbol != symbol or cycle.execution_mode != mode):
        cycle = None
    if cycle is None and active and (not row.get("loanPackageId") or str(row["loanPackageId"]) == active.loan_package_id):
        cycle = active
    trade_id = cycle.id if cycle else row_id
    rule_store = getattr(view, "rule_state", None)
    metrics = rule_store.position_metrics(symbol, trade_id) if rule_store and trade_id else {}
    decision = decisions_for_mode(status, mode, default_paper=getattr(view.settings, "paper_mode", True)).get(symbol) or {}
    if not decision_is_fresh(decision, symbol, mode) or (
            (decision.get("details") or {}).get("trade_id") not in {None, "", trade_id}):
        decision = {}
    details = decision.get("details") or {}
    params = position_exit_params(view.settings.rule_parameters, cycle)
    entry = account_price(row, row.get("costPrice", row.get("averagePrice", 0)))
    tick = (status.get("ticks") or {}).get(symbol) or {}
    price = _price_unit(next((tick.get(key) for key in ("price", "lastPrice", "matchPrice", "expected_price", "bid", "ask") if tick.get(key)), 0))
    price = price or account_price(row, row.get("marketPrice", row.get("price", 0))) or entry
    modes = set(cycle.em_modes if cycle else ())
    sl_enabled = bool(cycle and cycle.sl_enabled)
    sl_pct = -abs(cycle.sl_value) if cycle and cycle.sl_mode == "PERCENT" and cycle.sl_value else _number(
        params.get("reentry_sl_pct" if cycle and cycle.is_reentry else "initial_sl_pct", -3.5))
    sl_price = (cycle.sl_value if cycle and cycle.sl_mode == "PRICE" and cycle.sl_value > 0 else entry * (1 + sl_pct / 100)) if sl_enabled else 0
    tp_pct, tp_price = 0, 0
    if cycle and cycle.tp_mode == "PRICE" and cycle.tp_value > 0:
        tp_price = cycle.tp_value
        tp_pct = (tp_price / entry - 1) * 100 if entry > 0 else 0
    elif (cycle and cycle.tp_mode == "PERCENT" and cycle.tp_value > 0) or "TP" in modes:
        tp_pct = cycle.tp_value if cycle.tp_mode == "PERCENT" and cycle.tp_value > 0 else _number(params.get("take_profit_pct", 7))
        tp_price = entry * (1 + tp_pct / 100)
    paused = bool(cycle and (not cycle.auto_sell_enabled or not cycle.bot_trade_enabled))
    stage = str(details.get("protection_stage", "ARM"))
    policy = "ALERT" if paused else exit_stage_policy(params, cycle, stage)
    normal_state = str(details.get("normal_state", "WAIT") or "WAIT").upper()
    if "NORMAL" not in modes:
        normal_state = "OFF"
    elif metrics.get("normal_protection_done") and not (params.get("normal_repeat_enabled") and _number(params.get("normal_sell_pct", 100)) < 100):
        normal_state = "DONE"
    queue = getattr(view, "queue", None)
    pending = next((item for item in queue.list_all() if item.execution_mode == mode and item.action == "CLOSE"
                    and item.status.upper() not in FINAL_STATUSES and (
                        item.trade_id == trade_id or not item.trade_id and item.symbol == symbol)), None) if queue else None
    if pending and pending.status.upper() == "WAITING_SETTLEMENT" and str((pending.details or {}).get("event", pending.reason)) in {
            "NORMAL_PROTECTION", "DYNAMIC_PROTECTION", "PRICE_PROTECTION"}:
        normal_state = "T+2"
    untrusted = metrics.get("price_history_verified") is False or (
        "peak_profit_pct" in metrics and metrics.get("market_data_policy") != "G1_MATCH_V1")
    return position_status(symbol=symbol, mode=mode, quantity=int(_number(row.get("openQuantity", row.get("quantity", 0)))),
        sellable=int(_number(row.get("tradeQuantity", row.get("sellableQuantity", 0)))), settle=row.get("settleDate"),
        price=price, entry=entry, sl_enabled=sl_enabled, sl_price=sl_price, tp_price=tp_price, tp_pct=tp_pct,
        params=params, decision=decision, normal_enabled="NORMAL" in modes, indicator_enabled="IND_EXIT" in modes,
        paused=paused, protect_mode=policy, normal_state=normal_state, history_untrusted=untrusted,
        metrics=metrics, pending_close=pending)
