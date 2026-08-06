# -*- coding: utf-8 -*-
"""[RISK GATE] Van rủi ro per-lệnh dùng CHUNG cho bot + manual + telegram.

Gom các van cùng mục đích về một mối (mirror pattern core/margin_rules.py):
- evaluate(): trần % NAV mất-nếu-dính-SL cho 1 lệnh. Van duy nhất đo TIỀN-MẤT
  thay vì SIZE. Kế nhiệm vai trò STRICT_MIN_LOT (van đó chết vì
  dnse_connector.calculate_lot_size luôn clamp qty >= volume_min nên không bao
  giờ trả 0, trừ nhánh None do SL distance = 0).
- apply_stock_caps(): NAV cap + cash cap CKCS (move nguyên văn từ
  execute_bot_trade) để cả bot LẪN manual dùng chung một logic.

Config: đúng 2 key trong bot_safeguard (0 = tắt, không có công tắc riêng):
  RISK_GATE_MAX_PCT_PS — trần cho phái sinh (cao vì floor 1 HĐ)
  RISK_GATE_MAX_PCT_CS — trần cho cổ phiếu cơ sở
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import config
from core import settlement, stock_rules
from core.money import format_vnd_full

DEFAULT_RISK_GATE = {
    "RISK_GATE_MAX_PCT_PS": 10.0,
    "RISK_GATE_MAX_PCT_CS": 3.0,
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def settings_from_brain(brain: Dict[str, Any] | None) -> Dict[str, Any]:
    """Đọc 2 trần gate từ brain["bot_safeguard"], fallback config.BOT_SAFEGUARD rồi defaults."""
    cfg_defaults = getattr(config, "BOT_SAFEGUARD", {}) or {}
    settings = dict(DEFAULT_RISK_GATE)
    for key in DEFAULT_RISK_GATE:
        if key in cfg_defaults:
            settings[key] = _float(cfg_defaults.get(key), DEFAULT_RISK_GATE[key])
    raw = (brain or {}).get("bot_safeguard", {})
    if isinstance(raw, dict):
        for key in DEFAULT_RISK_GATE:
            if key in raw:
                settings[key] = _float(raw.get(key), settings[key])
    return settings


def evaluate(
    symbol: str,
    entry_price: float,
    sl_price: float,
    lot_size: float,
    contract_size: float,
    nav: float,
    settings: Dict[str, Any] | None,
    source: str = "MANUAL",
) -> Dict[str, Any]:
    """Đo tiền-mất-nếu-dính-SL so với NAV. Trả action OK/CONFIRM/BLOCK.

    Không bao giờ chặn vì thiếu data (parity với các van hiện có):
    threshold<=0 (tắt), sl<=0 (lệnh không SL/DCA con), nav<=0, lot<=0 -> OK + WARN.
    Vượt trần: MANUAL -> CONFIRM (user quyết); BOT/TELEGRAM -> BLOCK.
    """
    settings = settings or dict(DEFAULT_RISK_GATE)
    source = str(source or "MANUAL").upper()
    is_cs = settlement.is_cash_stock(symbol)
    threshold = _float(
        settings.get("RISK_GATE_MAX_PCT_CS" if is_cs else "RISK_GATE_MAX_PCT_PS"), 0.0
    )
    group_label = "CS" if is_cs else "PS"

    entry_price = _float(entry_price)
    sl_price = _float(sl_price)
    lot_size = _float(lot_size)
    contract_size = _float(contract_size, 1.0)
    nav = _float(nav)

    result: Dict[str, Any] = {
        "action": "OK",
        "passed": True,
        "risk_pct": 0.0,
        "est_loss": 0.0,
        "threshold": threshold,
        "checks": [],
        "msg": "",
    }

    skip_reason = ""
    if threshold <= 0:
        skip_reason = f"RISK GATE {group_label} đang tắt (0)"
    elif sl_price <= 0:
        skip_reason = "Lệnh không có SL -> bỏ qua gate"
    elif nav <= 0:
        skip_reason = "NAV không hợp lệ -> bỏ qua gate"
    elif lot_size <= 0:
        skip_reason = "Lot <= 0 -> bỏ qua gate"
    if skip_reason:
        result["checks"].append({"name": "Risk Gate", "status": "WARN", "msg": skip_reason})
        return result

    est_loss = abs(entry_price - sl_price) * lot_size * contract_size
    risk_pct = est_loss / nav * 100.0
    result["est_loss"] = est_loss
    result["risk_pct"] = risk_pct

    if risk_pct <= threshold:
        result["checks"].append(
            {
                "name": "Risk Gate",
                "status": "OK",
                "msg": f"{symbol}: risk {risk_pct:.1f}% NAV <= trần {group_label} {threshold:g}%",
            }
        )
        return result

    msg = (
        f"{symbol}: risk {risk_pct:.1f}% NAV ({format_vnd_full(est_loss)}) "
        f"> trần {group_label} {threshold:g}%"
    )
    result["passed"] = False
    result["msg"] = msg
    result["action"] = "CONFIRM" if source == "MANUAL" else "BLOCK"
    result["checks"].append({"name": "Risk Gate", "status": "FAIL", "msg": msg})
    return result


def apply_stock_caps(
    symbol: str,
    lot_size: float,
    current_price: float,
    acc_info: Dict[str, Any] | None,
    sym_info: Any,
    safeguard_cfg: Dict[str, Any] | None,
    log: Optional[Callable[..., Any]] = None,
    log_target: Optional[str] = "bot",
) -> Dict[str, Any]:
    """[NAV CAP] CKCS không đòn bẩy: giá trị 1 lệnh ≤ % NAV (chống SL hẹp -> lot khổng lồ,
    dồn vốn 1 mã) + [CASH CAP] notional không vượt tiền mặt khả dụng (chừa ~1% phí).

    Move nguyên văn từ execute_bot_trade để bot + manual dùng chung.
    Trả {"lot", "error", "capped_by"}; mã PS / nav_pct<=0 -> lot nguyên vẹn.
    """
    safeguard_cfg = safeguard_cfg or {}
    out = {"lot": float(lot_size), "error": "", "capped_by": ""}
    if not settlement.is_cash_stock(symbol):
        return out
    nav_pct = _float(
        safeguard_cfg.get(
            "STOCK_MAX_ORDER_NAV_PCT", getattr(config, "STOCK_MAX_ORDER_NAV_PCT", 20.0)
        ),
        0.0,
    )
    if nav_pct <= 0:
        return out
    nav = _float((acc_info or {}).get("equity", 0.0), 0.0)
    c_size = _float(getattr(sym_info, "trade_contract_size", 1.0), 1.0) or 1.0
    cap_value = nav * (nav_pct / 100.0)
    _cash = _float(
        (acc_info or {}).get("cash_available", 0.0)
        or (acc_info or {}).get("stock_cash", 0.0),
        0.0,
    )
    _capped_by = "NAV"
    if _cash > 0 and _cash * 0.99 < cap_value:
        cap_value = _cash * 0.99
        _capped_by = "TIỀN MẶT"
    cap_lot = stock_rules.max_shares_for_value(
        cap_value, current_price, c_size, stock_rules._round_lot()
    )
    if cap_lot <= 0:
        out["error"] = f"SAFEGUARD_FAIL|CKCS_CAP_TOO_SMALL|{symbol}: không đủ {_capped_by} cho 1 lô."
        out["capped_by"] = _capped_by
        return out
    if lot_size > cap_lot:
        if log is not None:
            try:
                if log_target:
                    log(f"[{_capped_by} CAP] {symbol}: giảm {lot_size:g}→{cap_lot:g} CP.", target=log_target)
                else:
                    log(f"[{_capped_by} CAP] {symbol}: giảm {lot_size:g}→{cap_lot:g} CP.")
            except Exception:
                pass
        out["lot"] = float(cap_lot)
        out["capped_by"] = _capped_by
    return out
