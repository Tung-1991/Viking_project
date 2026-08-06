# -*- coding: utf-8 -*-
import time

from core.market_structure import structure_from_context


ENTRY_ORDER = ["SWING_REJECTION", "SWING_STRUCTURE", "FIB_RETRACE", "PULLBACK_ZONE", "FALLBACK_R"]


def default_entry_exit_config():
    return {
        # Engine thuần không tự bật E/E khi caller không truyền cấu hình.
        # Storage/UI mới là nơi quyết định mặc định BOT đang bật hay tắt.
        "enabled": False,
        "preview_only": False,
        "active_tactics": ["SWING_REJECTION"],
        "entry_tactics": ["SWING_REJECTION"],
        "exit_tactic": "FALLBACK_R",
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


def merge_config(cfg):
    base = default_entry_exit_config()
    if isinstance(cfg, dict):
        _merge_dict(base, cfg)
    return base


def format_decision(decision):
    status = decision.get("status", "OFF")
    entry = _short_mode(decision.get("entry_tactic", "OFF"))
    exit_tactic = _short_mode(decision.get("exit_tactic"))
    sl_source = _short_mode(decision.get("sl_source"))
    zone = decision.get("entry_zone")
    sl = decision.get("sl")
    tp = decision.get("tp")
    reason = decision.get("reason", "")
    if status == "OFF":
        return "E/E: OFF"
    parts = [f"E/E: {status} {entry}"]
    if zone:
        parts.append(f"Entry {decision.get('direction', '')} {zone[0]:.2f}-{zone[1]:.2f}")
    if sl:
        parts.append(f"SL theo {sl_source or entry} {sl:.2f}")
    elif decision.get("sl_source") == "SANDBOX":
        parts.append("SL theo SANDBOX")
    if decision.get("tp_disabled") or decision.get("tp_source") == "OFF":
        parts.append("TP OFF")
    elif tp:
        parts.append(f"TP theo {exit_tactic} {tp:.2f}" if exit_tactic else f"TP {tp:.2f}")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def evaluate_entry_exit(symbol, direction, price, context, cfg, pending=None):
    cfg = merge_config(cfg)
    direction = str(direction or "").upper()
    now = time.time()
    ttl = max(1, int(float(cfg.get("signal_ttl_seconds", 900) or 900)))

    if not cfg.get("enabled") or not cfg.get("active_tactics"):
        return _decision("OFF", symbol, direction, price, reason="Entry/Exit disabled")

    context = context or {}
    entry_tactics = _ordered_entry_tactics(cfg)
    if not entry_tactics:
        return _decision("OFF", symbol, direction, price, reason="No Entry mode enabled")

    waits = []
    errors = []
    for tactic in entry_tactics:
        entry_decision = _evaluate_entry_tactic(tactic, symbol, direction, price, context, cfg, ttl, now)
        if not entry_decision:
            continue

        if entry_decision["status"] == "READY":
            _apply_sl(entry_decision, price, context, cfg)
            _apply_exit(entry_decision, price, context, cfg)
            return entry_decision
        if entry_decision["status"] == "WAIT":
            _apply_sl(entry_decision, price, context, cfg)
            _apply_exit(entry_decision, price, context, cfg)
            waits.append(entry_decision)
            continue
        errors.append(entry_decision.get("reason", tactic))

    if waits:
        return _combine_waits(symbol, direction, price, waits, ttl, now)

    if _allow_missing_fallback(cfg):
        decision = _fallback_r_entry(symbol, direction, price, cfg, ttl, now)
        decision["reason"] = "Missing E/E data, fallback R"
        _apply_sl(decision, price, context, cfg)
        _apply_exit(decision, price, context, cfg)
        return decision

    return _decision(
        "ERROR",
        symbol,
        direction,
        price,
        reason="; ".join(errors) if errors else "No E/E tactic available",
        expires_at=now + ttl,
    )


def _ordered_entry_tactics(cfg):
    active = set(cfg.get("active_tactics") or [])
    selected = list(cfg.get("entry_tactics") or cfg.get("active_tactics") or [])
    selected = [t for t in selected if t in active or t == "FALLBACK_R"]
    if not selected:
        selected = list(active)
    selected = [t for t in selected if t in ENTRY_ORDER]
    non_r = [t for t in selected if t != "FALLBACK_R"]
    if "FALLBACK_R" in selected:
        non_r.append("FALLBACK_R")
    return non_r


def _evaluate_entry_tactic(tactic, symbol, direction, price, context, cfg, ttl, now):
    if tactic == "FALLBACK_R":
        return _fallback_r_entry(symbol, direction, price, cfg, ttl, now)
    if tactic == "SWING_REJECTION":
        return _swing_retest_entry(symbol, direction, price, context, cfg, ttl, now)
    if tactic == "SWING_STRUCTURE":
        return _swing_structure_entry(symbol, direction, price, context, cfg, ttl, now)
    if tactic == "FIB_RETRACE":
        return _fib_entry(symbol, direction, price, context, cfg, ttl, now)
    if tactic == "PULLBACK_ZONE":
        return _pull_entry(symbol, direction, price, context, cfg, ttl, now)
    return None


def _swing_retest_entry(symbol, direction, price, context, cfg, ttl, now):
    sw = cfg.get("swing_rejection", {})
    group = _resolve_group(sw.get("source_group", "G2"), context)
    sh, sl, atr = _swing_values(context, group)
    if not _positive(sh) or not _positive(sl) or not _positive(atr):
        return _missing(symbol, direction, price, "SWING_REJECTION", f"Missing swing/ATR {group}", cfg, ttl, now)
    max_atr = float(sw.get("max_atr_from_swing", 0.7) or 0.7)
    sl_buffer = float(sw.get("sl_atr_buffer", 0.2) or 0.2)
    if direction == "BUY":
        zone = (float(sl), float(sl) + float(atr) * max_atr)
        status = "READY" if zone[0] <= price <= zone[1] else "WAIT"
        stop = float(sl) - float(atr) * sl_buffer
    else:
        zone = (float(sh) - float(atr) * max_atr, float(sh))
        status = "READY" if zone[0] <= price <= zone[1] else "WAIT"
        stop = float(sh) + float(atr) * sl_buffer
    reason = "Giá đã vào vùng Swing Retest" if status == "READY" else _wait_zone_reason(direction, price, zone, atr, "Swing Retest")
    if status == "WAIT" and sw.get("allow_breakout_entry", False):
        max_breakout = float(sw.get("max_breakout_atr", 0.5) or 0.5)
        breakout_dist = _breakout_distance(direction, price, zone, atr)
        if breakout_dist is not None and breakout_dist <= max_breakout:
            status = "READY"
            reason = f"Giá đã phá khỏi vùng Swing Retest theo hướng {direction} ({breakout_dist:.2f} ATR)"
    if status == "READY" and sw.get("require_rejection_candle", False):
        rejection = _has_rejection_candle(direction, context, group)
        if rejection is None:
            return _missing(symbol, direction, price, "SWING_REJECTION", f"Missing rejection candle {group}", cfg, ttl, now)
        if not rejection:
            status = "WAIT"
            reason = "Chờ nến từ chối vùng Swing Retest"
        else:
            reason = "Giá đã vào vùng Swing Retest + có nến từ chối"
    return _decision(
        status,
        symbol,
        direction,
        price,
        entry_tactic="SWING_REJECTION",
        entry_zone=zone,
        natural_sl=stop,
        natural_sl_source=f"SWING_RETEST_{group}",
        reason=reason,
        expires_at=now + ttl,
    )


def _swing_structure_entry(symbol, direction, price, context, cfg, ttl, now):
    sw = cfg.get("swing_structure", {})
    group = _resolve_group(sw.get("source_group", "G2"), context)
    atr = context.get(f"atr_{group}") or context.get("atr_entry")
    if not _positive(atr):
        return _missing(symbol, direction, price, "SWING_STRUCTURE", f"Missing structure ATR {group}", cfg, ttl, now)
    ms = structure_from_context(context, group)
    bias = ms.get("bias")
    entry_atr = float(sw.get("entry_atr", 0.7) or 0.7)
    sl_buffer = float(sw.get("sl_atr_buffer", 0.2) or 0.2)
    atr = float(atr)

    if direction == "BUY":
        anchor = ms.get("hl")
        breakout = ms.get("hh")
        needed = "UP"
        if bias != needed or not _positive(anchor):
            return _decision(
                "WAIT",
                symbol,
                direction,
                price,
                entry_tactic="SWING_STRUCTURE",
                reason=f"Chờ UP structure HH/HL {group}",
                expires_at=now + ttl,
            )
        zone = (float(anchor), float(anchor) + atr * entry_atr)
        stop = float(anchor) - atr * sl_buffer
    else:
        anchor = ms.get("lh")
        breakout = ms.get("ll")
        needed = "DOWN"
        if bias != needed or not _positive(anchor):
            return _decision(
                "WAIT",
                symbol,
                direction,
                price,
                entry_tactic="SWING_STRUCTURE",
                reason=f"Chờ DOWN structure LH/LL {group}",
                expires_at=now + ttl,
            )
        zone = (float(anchor) - atr * entry_atr, float(anchor))
        stop = float(anchor) + atr * sl_buffer

    status = "READY" if zone[0] <= price <= zone[1] else "WAIT"
    reason = f"Giá đã vào vùng Swing Structure {needed}" if status == "READY" else _wait_zone_reason(direction, price, zone, atr, "Swing Structure")
    if status == "WAIT" and sw.get("allow_breakout_entry", True) and _positive(breakout):
        max_breakout = float(sw.get("max_breakout_atr", 0.5) or 0.5)
        breakout_dist = None
        if direction == "BUY" and price > float(breakout):
            breakout_dist = (float(price) - float(breakout)) / atr
        elif direction == "SELL" and price < float(breakout):
            breakout_dist = (float(breakout) - float(price)) / atr
        if breakout_dist is not None and breakout_dist <= max_breakout:
            status = "READY"
            reason = f"Giá phá cấu trúc {direction} ({breakout_dist:.2f} ATR)"

    return _decision(
        status,
        symbol,
        direction,
        price,
        entry_tactic="SWING_STRUCTURE",
        entry_zone=zone,
        natural_sl=stop,
        natural_sl_source=f"SWING_STRUCTURE_{group}",
        reason=reason,
        expires_at=now + ttl,
    )


def _fib_entry(symbol, direction, price, context, cfg, ttl, now):
    fib = cfg.get("fib_retrace", {})
    group = _resolve_group(fib.get("swing_source_group", "G2"), context)
    sh, sl, atr = _swing_values(context, group)
    if not _positive(sh) or not _positive(sl) or not _positive(atr):
        return _missing(symbol, direction, price, "FIB_RETRACE", f"Missing fib swing/ATR {group}", cfg, ttl, now)
    levels = _parse_levels(fib.get("entry_levels", "0.5,0.618"), [0.5, 0.618])
    leg = abs(float(sh) - float(sl))
    if leg <= 0:
        return _missing(symbol, direction, price, "FIB_RETRACE", "Invalid fib leg", cfg, ttl, now)
    if direction == "BUY":
        vals = [float(sh) - leg * lvl for lvl in levels]
        zone = (min(vals), max(vals))
        stop = float(sl) - float(atr) * float(fib.get("entry_tolerance_atr", 0.15) or 0.15)
    else:
        vals = [float(sl) + leg * lvl for lvl in levels]
        zone = (min(vals), max(vals))
        stop = float(sh) + float(atr) * float(fib.get("entry_tolerance_atr", 0.15) or 0.15)
    status = "READY" if zone[0] <= price <= zone[1] else "WAIT"
    return _decision(
        status,
        symbol,
        direction,
        price,
        entry_tactic="FIB_RETRACE",
        entry_zone=zone,
        natural_sl=stop,
        natural_sl_source=f"FIB_{group}",
        reason="Giá đã vào vùng FIB" if status == "READY" else _wait_zone_reason(direction, price, zone, atr, "FIB"),
        expires_at=now + ttl,
    )


def _pull_entry(symbol, direction, price, context, cfg, ttl, now):
    pull = cfg.get("pullback_zone", {})
    group = _resolve_group(cfg.get("sl_source_group", "G2"), context)
    atr = context.get(f"atr_{group}") or context.get("atr_entry")
    if not _positive(atr):
        return _missing(symbol, direction, price, "PULLBACK_ZONE", f"Missing ATR {group}", cfg, ttl, now)
    source = str(pull.get("source", "EMA20")).upper()
    zone_mid = None
    if source == "SWING":
        sh, sl, _ = _swing_values(context, group)
        zone_mid = sl if direction == "BUY" else sh
    elif source == "BB_MID":
        zone_mid = context.get(f"bb_mid_{group}") or context.get("bb_mid")
    else:
        zone_mid = context.get(f"ema20_{group}") or context.get("ema20") or context.get(f"EMA_20_{group}")
    if not _positive(zone_mid):
        return _missing(symbol, direction, price, "PULLBACK_ZONE", f"Missing pullback source {source}", cfg, ttl, now)
    dist = float(atr) * float(pull.get("max_atr_from_zone", 0.5) or 0.5)
    zone = (float(zone_mid) - dist, float(zone_mid) + dist)
    status = "READY" if zone[0] <= price <= zone[1] else "WAIT"
    sl_buffer = float(pull.get("sl_atr_buffer", 0.2) or 0.2)
    stop = zone[0] - float(atr) * sl_buffer if direction == "BUY" else zone[1] + float(atr) * sl_buffer
    return _decision(
        status,
        symbol,
        direction,
        price,
        entry_tactic="PULLBACK_ZONE",
        entry_zone=zone,
        natural_sl=stop,
        natural_sl_source=f"PULL_{source}",
        reason="Giá đã vào vùng Pullback" if status == "READY" else _wait_zone_reason(direction, price, zone, atr, "Pullback"),
        expires_at=now + ttl,
    )


def _fallback_r_entry(symbol, direction, price, cfg, ttl, now):
    return _decision(
        "READY",
        symbol,
        direction,
        price,
        entry_tactic="FALLBACK_R",
        reason="Fallback R entry",
        expires_at=now + ttl,
    )


def _apply_sl(decision, price, context, cfg):
    mode = str(cfg.get("sl_mode", "SANDBOX") or "SANDBOX").upper()
    if mode == "AUTO":
        mode = _auto_sl_tactic(decision.get("entry_tactic"))
    mode = {"SWING_RETEST": "SWING_REJECTION"}.get(mode, mode)

    if mode == "SANDBOX":
        decision["sl"] = None
        decision["sl_source"] = "SANDBOX"
    elif mode == decision.get("entry_tactic") and decision.get("natural_sl"):
        decision["sl"] = decision.get("natural_sl")
        decision["sl_source"] = decision.get("natural_sl_source")
    elif mode == "SWING_REJECTION":
        decision["sl"], decision["sl_source"] = _swing_retest_sl(decision.get("direction"), context, cfg)
    elif mode == "SWING_STRUCTURE":
        decision["sl"], decision["sl_source"] = _swing_structure_sl(decision.get("direction"), context, cfg)
    elif mode == "FIB_RETRACE":
        decision["sl"], decision["sl_source"] = _fib_sl(decision.get("direction"), context, cfg)
    elif mode == "PULLBACK_ZONE":
        if decision.get("entry_tactic") == "PULLBACK_ZONE" and decision.get("natural_sl"):
            decision["sl"] = decision.get("natural_sl")
            decision["sl_source"] = decision.get("natural_sl_source")
        else:
            decision["sl"], decision["sl_source"] = _pullback_sl(decision.get("direction"), context, cfg)
    else:
        decision["sl"] = None
        decision["sl_source"] = "SANDBOX"

    if decision.get("sl"):
        decision["risk_distance"] = abs(float(price) - float(decision["sl"]))


def _apply_exit(decision, price, context, cfg):
    exit_tactic = cfg.get("exit_tactic") or "AUTO"
    if str(exit_tactic).upper() in ("NO_TP", "OFF"):
        decision["exit_tactic"] = "NO_TP"
        decision["tp"] = 0.0
        decision["tp_source"] = "OFF"
        decision["tp_disabled"] = True
        return
    if exit_tactic == "AUTO":
        exit_tactic = _auto_exit_tactic(decision.get("entry_tactic"))
    decision["exit_tactic"] = exit_tactic
    if exit_tactic == "FIB_RETRACE":
        _apply_tactic_tp(decision, price, _fib_tp(decision.get("direction"), context, cfg), "FIB", cfg)
    elif exit_tactic in ("SWING_REJECTION", "SWING_STRUCTURE"):
        _apply_tactic_tp(
            decision,
            price,
            _swing_tp(
                decision.get("direction"),
                context,
                cfg,
                exit_tactic=exit_tactic,
            ),
            "SWING",
            cfg,
        )
    elif exit_tactic == "PULLBACK_ZONE":
        _apply_tactic_tp(
            decision,
            price,
            _pullback_tp(decision.get("direction"), price, context, cfg),
            "PULLBACK",
            cfg,
        )
    elif exit_tactic == "FALLBACK_R":
        _apply_r_tp(decision, price, cfg)
        if not _valid_tp_direction(decision.get("direction"), price, decision.get("tp")):
            # SL SANDBOX được BOT tính sau khi E/E READY; lúc này engine chưa
            # có khoảng R để dựng TP. Giữ READY và để BOT policy hoàn thiện TP.
            if decision.get("sl_source") == "SANDBOX":
                decision["tp"] = None
                decision["tp_source"] = "R:PENDING_SANDBOX_SL"
            else:
                _mark_missing_tp(decision, "R")
    else:
        _mark_missing_tp(decision, str(exit_tactic or "TP"))


def _apply_tactic_tp(decision, price, tp, source, cfg):
    """Dùng đúng TP của tactic; chỉ về R khi người dùng chủ động cho phép."""
    if _valid_tp_direction(decision.get("direction"), price, tp):
        decision["tp"] = float(tp)
        decision["tp_source"] = source
        return
    allow_r = _allow_missing_fallback(cfg) and bool(
        cfg.get("default_exit", {}).get("use_rr_tp", False)
    )
    if allow_r:
        _apply_r_tp(decision, price, cfg)
        if _valid_tp_direction(decision.get("direction"), price, decision.get("tp")):
            return
    _mark_missing_tp(decision, source)


def _valid_tp_direction(direction, price, tp):
    if not _positive(price) or not _positive(tp):
        return False
    if str(direction or "").upper() == "BUY":
        return float(tp) > float(price)
    if str(direction or "").upper() == "SELL":
        return float(tp) < float(price)
    return False


def _mark_missing_tp(decision, source):
    decision["tp"] = None
    decision["tp_source"] = f"{source}:MISSING"
    reason = str(decision.get("reason") or "").strip()
    suffix = f"Không có TP {source} hợp lệ"
    decision["reason"] = f"{reason} | {suffix}" if reason else suffix
    if decision.get("status") == "READY":
        decision["status"] = "ERROR"


def _combine_waits(symbol, direction, price, waits, ttl, now):
    reasons = [f"{_short_mode(w.get('entry_tactic'))}: {w.get('reason', 'WAIT')}" for w in waits]
    return _decision(
        "WAIT",
        symbol,
        direction,
        price,
        entry_tactic="MULTI_WAIT",
        reason="; ".join(reasons),
        wait_decisions=waits,
        expires_at=now + ttl,
    )


def _auto_sl_tactic(entry_tactic):
    if entry_tactic in ("SWING_REJECTION", "SWING_STRUCTURE", "FIB_RETRACE", "PULLBACK_ZONE"):
        return entry_tactic
    return "SANDBOX"


def _auto_exit_tactic(entry_tactic):
    if entry_tactic in ("SWING_REJECTION", "SWING_STRUCTURE", "FIB_RETRACE", "PULLBACK_ZONE"):
        return entry_tactic
    return "FALLBACK_R"


def _swing_retest_sl(direction, context, cfg):
    sw = cfg.get("swing_rejection", {})
    group = _resolve_group(sw.get("source_group", "G2"), context)
    sh, sl, atr = _swing_values(context, group)
    if not _positive(sh) or not _positive(sl) or not _positive(atr):
        return None, f"SWING_RETEST_{group}"
    buffer = float(atr) * float(sw.get("sl_atr_buffer", 0.2) or 0.2)
    return (float(sl) - buffer, f"SWING_RETEST_{group}") if direction == "BUY" else (float(sh) + buffer, f"SWING_RETEST_{group}")


def _swing_structure_sl(direction, context, cfg):
    sw = cfg.get("swing_structure", {})
    group = _resolve_group(sw.get("source_group", "G2"), context)
    atr = context.get(f"atr_{group}") or context.get("atr_entry")
    if not _positive(atr):
        return None, f"SWING_STRUCTURE_{group}"
    ms = structure_from_context(context, group)
    buffer = float(atr) * float(sw.get("sl_atr_buffer", 0.2) or 0.2)
    if direction == "BUY" and _positive(ms.get("hl")):
        return float(ms["hl"]) - buffer, f"SWING_STRUCTURE_{group}"
    if direction == "SELL" and _positive(ms.get("lh")):
        return float(ms["lh"]) + buffer, f"SWING_STRUCTURE_{group}"
    return None, f"SWING_STRUCTURE_{group}"


def _fib_sl(direction, context, cfg):
    fib = cfg.get("fib_retrace", {})
    group = _resolve_group(fib.get("swing_source_group", "G2"), context)
    sh, sl, atr = _swing_values(context, group)
    if not _positive(sh) or not _positive(sl) or not _positive(atr):
        return None, f"FIB_{group}"
    tol = float(atr) * float(fib.get("entry_tolerance_atr", 0.15) or 0.15)
    return (float(sl) - tol, f"FIB_{group}") if direction == "BUY" else (float(sh) + tol, f"FIB_{group}")


def _pullback_sl(direction, context, cfg):
    pull = cfg.get("pullback_zone", {})
    group = _resolve_group(cfg.get("sl_source_group", "G2"), context)
    atr = context.get(f"atr_{group}") or context.get("atr_entry")
    if not _positive(atr):
        return None, "PULLBACK"
    source = str(pull.get("source", "EMA20")).upper()
    zone_mid = None
    if source == "SWING":
        sh, sl, _ = _swing_values(context, group)
        zone_mid = sl if direction == "BUY" else sh
    elif source == "BB_MID":
        zone_mid = context.get(f"bb_mid_{group}") or context.get("bb_mid")
    else:
        zone_mid = context.get(f"ema20_{group}") or context.get("ema20") or context.get(f"EMA_20_{group}")
    if not _positive(zone_mid):
        return None, f"PULL_{source}"
    dist = float(atr) * float(pull.get("max_atr_from_zone", 0.5) or 0.5)
    zone = (float(zone_mid) - dist, float(zone_mid) + dist)
    buffer = float(atr) * float(pull.get("sl_atr_buffer", 0.2) or 0.2)
    return (zone[0] - buffer, f"PULL_{source}") if direction == "BUY" else (zone[1] + buffer, f"PULL_{source}")


def _fib_tp(direction, context, cfg):
    fib = cfg.get("fib_retrace", {})
    group = _resolve_group(fib.get("swing_source_group", "G2"), context)
    sh, sl, _ = _swing_values(context, group)
    if not _positive(sh) or not _positive(sl):
        return None
    levels = _parse_levels(fib.get("tp_levels", "1.272,1.618"), [1.272, 1.618])
    level = max(levels) if levels else 1.272
    leg = abs(float(sh) - float(sl))
    if leg <= 0:
        return None
    return float(sl) + leg * level if direction == "BUY" else float(sh) - leg * level


def _swing_tp(direction, context, cfg, exit_tactic="SWING_REJECTION"):
    tactic_key = (
        "swing_structure"
        if str(exit_tactic or "").upper() == "SWING_STRUCTURE"
        else "swing_rejection"
    )
    group = _resolve_group(
        (cfg.get(tactic_key, {}) or {}).get("source_group", "G2"),
        context,
    )
    sh, sl, atr = _swing_values(context, group)
    if str(exit_tactic or "").upper() == "SWING_STRUCTURE":
        structure = structure_from_context(context, group)
        if str(direction or "").upper() == "BUY":
            sh = structure.get("hh") or sh
        else:
            sl = structure.get("ll") or sl
    if not _positive(sh) or not _positive(sl):
        # Với Structure chỉ cần mục tiêu đối diện đúng chiều; không bắt buộc
        # phải có đủ cả swing high và low như Swing Retest.
        target = sh if str(direction or "").upper() == "BUY" else sl
        return float(target) if _positive(target) else None
    buffer = float(atr or 0) * float(
        (cfg.get(tactic_key, {}) or {}).get("sl_atr_buffer", 0.2) or 0.2
    )
    return float(sh) - buffer if direction == "BUY" else float(sl) + buffer


def _pullback_tp(direction, price, context, cfg):
    pull = cfg.get("pullback_zone", {})
    group = _resolve_group(cfg.get("sl_source_group", "G2"), context)
    atr = context.get(f"atr_{group}") or context.get("atr_entry")
    if not _positive(atr):
        return None
    mult = float(pull.get("tp_atr_multiplier", 1.5) or 1.5)
    return float(price) + float(atr) * mult if direction == "BUY" else float(price) - float(atr) * mult


def _apply_r_tp(decision, price, cfg):
    sl = decision.get("sl")
    if not sl:
        return
    rr = float(cfg.get("default_exit", {}).get("tp_rr_ratio", 1.5) or 1.5)
    dist = abs(float(price) - float(sl))
    decision["tp"] = float(price) + dist * rr if decision.get("direction") == "BUY" else float(price) - dist * rr
    decision["tp_source"] = "R"


def _missing(symbol, direction, price, tactic, reason, cfg, ttl, now):
    return _decision("ERROR", symbol, direction, price, entry_tactic=tactic, reason=reason, expires_at=now + ttl)


def _allow_missing_fallback(cfg):
    return str(cfg.get("missing_data_policy", "FALLBACK_R")).upper() == "FALLBACK_R"


def _has_rejection_candle(direction, context, group):
    try:
        o = float(context.get(f"open_{group}"))
        h = float(context.get(f"high_{group}"))
        l = float(context.get(f"low_{group}"))
        c = float(context.get(f"close_{group}"))
    except Exception:
        return None
    body = abs(c - o)
    full_range = h - l
    if full_range <= 0:
        return False
    body = max(body, full_range * 0.05)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    close_pos = (c - l) / full_range
    if direction == "BUY":
        return lower_wick >= body * 1.5 and close_pos >= 0.55
    return upper_wick >= body * 1.5 and close_pos <= 0.45


def _wait_zone_reason(direction, price, zone, atr, label):
    if not zone or not _positive(atr):
        return f"Chờ hồi về vùng {label}"
    price = float(price)
    atr = float(atr)
    if price < zone[0]:
        dist = (zone[0] - price) / atr
        side = "dưới"
    elif price > zone[1]:
        dist = (price - zone[1]) / atr
        side = "trên"
    else:
        return f"Chờ hồi về vùng {label}"
    return f"Chờ hồi về vùng {label} | Giá đang {side} vùng {dist:.2f} ATR"


def _breakout_distance(direction, price, zone, atr):
    if direction == "BUY" and price > zone[1]:
        return (float(price) - zone[1]) / float(atr)
    if direction == "SELL" and price < zone[0]:
        return (zone[0] - float(price)) / float(atr)
    return None


def _swing_values(context, group):
    return (
        context.get(f"swing_high_{group}"),
        context.get(f"swing_low_{group}"),
        context.get(f"atr_{group}"),
    )


def _resolve_group(group, context):
    if not group:
        return "G2"
    group = str(group)
    if "DYNAMIC" in group:
        market_mode = (context or {}).get("market_mode", "ANY")
        return "G1" if market_mode in ("TREND", "BREAKOUT") else "G2"
    if group == "BASE_SL":
        return "G2"
    return group


def _parse_levels(raw, default):
    try:
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(x.strip()) for x in str(raw).split(",") if x.strip()]
    except Exception:
        return default


def _positive(value):
    try:
        return value is not None and float(value) > 0
    except Exception:
        return False


def _decision(status, symbol, direction, price, **kwargs):
    data = {
        "status": status,
        "symbol": symbol,
        "direction": direction,
        "current_price": price,
        "entry_tactic": kwargs.pop("entry_tactic", "OFF"),
        "exit_tactic": kwargs.pop("exit_tactic", None),
        "entry_zone": kwargs.pop("entry_zone", None),
        "sl": kwargs.pop("sl", None),
        "tp": kwargs.pop("tp", None),
        "sl_source": kwargs.pop("sl_source", None),
        "tp_source": kwargs.pop("tp_source", None),
        "tp_disabled": kwargs.pop("tp_disabled", False),
        "reason": kwargs.pop("reason", ""),
        "expires_at": kwargs.pop("expires_at", None),
    }
    data.update(kwargs)
    return data


def _short_mode(mode):
    return {
        "FALLBACK_R": "R",
        "NO_TP": "NO TP",
        "OFF": "OFF",
        "SWING_REJECTION": "SWING RETEST",
        "SWING_RETEST": "SWING RETEST",
        "SWING_STRUCTURE": "SWING STRUCT",
        "FIB_RETRACE": "FIB",
        "PULLBACK_ZONE": "PULL",
        "MULTI_WAIT": "MULTI",
        "SANDBOX": "SANDBOX",
    }.get(mode, mode or "OFF")


def _merge_dict(dst, src):
    if not isinstance(src, dict):
        return dst
    for key, val in src.items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            _merge_dict(dst[key], val)
        else:
            dst[key] = val
    return dst
