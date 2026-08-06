# -*- coding: utf-8 -*-
"""Ràng buộc giao dịch cổ phiếu cơ sở (CKCS) trên DNSE.

Hai luật chính (chỉ áp cho CKCS, KHÔNG áp phái sinh):
1. Khối lượng lệnh thường phải là bội số 100 cổ phiếu (lô chẵn). Lẻ -> làm tròn XUỐNG.
2. Giá lệnh LO phải nằm trong biên độ trần/sàn của sàn niêm yết
   (HOSE ±7%, HNX ±10%, UPCOM ±15%).

Module này thuần (không phụ thuộc network) để dễ test. Giá trần/sàn ưu tiên lấy
từ quote DNSE (ceiling/floor); nếu thiếu thì tính từ giá tham chiếu × biên độ sàn.
"""

from __future__ import annotations

from typing import Tuple

import config

ROUND_LOT_DEFAULT = 100


def _round_lot() -> int:
    return int(getattr(config, "STOCK_ROUND_LOT", ROUND_LOT_DEFAULT) or ROUND_LOT_DEFAULT)


def round_lot_down(volume, lot: int = 0) -> int:
    """Làm tròn XUỐNG bội số lô. VD lot=100: 150 -> 100, 90 -> 0, 250 -> 200."""
    step = int(lot) if lot else _round_lot()
    if step <= 0:
        step = ROUND_LOT_DEFAULT
    try:
        vol = int(float(volume))
    except (TypeError, ValueError):
        return 0
    if vol <= 0:
        return 0
    return (vol // step) * step


def max_shares_for_value(value_cap, price, point_value, lot: int = 0) -> int:
    """Số CP lô-chẵn tối đa cho 1 GIÁ TRỊ tiền tối đa (notional ≤ value_cap).

    notional 1 CP = price * point_value (CKCS: point_value = 1000). Làm tròn XUỐNG bội lô.
    VD value_cap=20tr, price=33.65, pv=1000, lô=100 -> 20.000.000/33.650 = 594 -> 500 CP.
    Thiếu dữ liệu / cap < 1 lô -> 0.
    """
    step = int(lot) if lot else _round_lot()
    if step <= 0:
        step = ROUND_LOT_DEFAULT
    try:
        per_share = float(price) * float(point_value)
        cap = float(value_cap)
    except (TypeError, ValueError):
        return 0
    if per_share <= 0 or cap <= 0:
        return 0
    raw = int(cap // per_share)
    return (raw // step) * step


def band_pct_for(symbol) -> float:
    """Biên độ % theo sàn niêm yết của mã. Mặc định HOSE (0.07)."""
    sym = str(symbol or "").strip().upper()
    exch_map = getattr(config, "STOCK_SYMBOL_EXCHANGE", {}) or {}
    exchange = str(exch_map.get(sym) or getattr(config, "STOCK_DEFAULT_EXCHANGE", "HOSE")).upper()
    bands = getattr(config, "STOCK_EXCHANGE_BANDS", {}) or {}
    try:
        return float(bands.get(exchange, bands.get("HOSE", 0.07)))
    except (TypeError, ValueError):
        return 0.07


def resolve_band(reference, ceiling, floor, band_pct) -> Tuple[float, float]:
    """Trả (floor_price, ceiling_price).

    Ưu tiên ceiling/floor DNSE (>0). Thiếu -> tính reference*(1±band_pct).
    Thiếu cả reference -> (0.0, 0.0) nghĩa là 'không xác định, bỏ qua check'.
    """
    ce = float(ceiling or 0.0)
    fl = float(floor or 0.0)
    if ce > 0 and fl > 0:
        return (fl, ce)
    ref = float(reference or 0.0)
    pct = float(band_pct or 0.0)
    if ref > 0 and pct > 0:
        # Nếu DNSE chỉ trả 1 cận, giữ cận đó, suy cận còn lại từ ref.
        return (fl or ref * (1.0 - pct), ce or ref * (1.0 + pct))
    return (0.0, 0.0)


def price_in_band(price, floor_price, ceiling_price) -> bool:
    """True nếu band không xác định (0,0) hoặc floor <= price <= ceiling.

    Dùng dung sai nhỏ để tránh sai số dấu phẩy động ngay tại biên.
    """
    fl = float(floor_price or 0.0)
    ce = float(ceiling_price or 0.0)
    if fl <= 0 and ce <= 0:
        return True
    p = float(price or 0.0)
    eps = 1e-9
    return (fl - eps) <= p <= (ce + eps)
