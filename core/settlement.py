# -*- coding: utf-8 -*-
"""T+2 settlement cho cổ phiếu cơ sở (CKCS).

Phái sinh (VN30F / CKPS) KHÔNG áp dụng — mua/bán liên tục như cũ.

Luật (tài khoản thường):
- Mua T+0 hôm nay → cổ phiếu về tài khoản T+2 (2 ngày làm việc sau), từ đó mới bán được.
- Chỉ bán được cổ phiếu ĐÃ SỞ HỮU và ĐÃ VỀ; không bán khống.

Module này thuần (không phụ thuộc network) để dễ test. `working_dates` (danh sách
ngày làm việc 'YYYY-MM-DD' lấy từ DNSE /market-working-dates) được truyền vào;
nếu rỗng thì fallback = cộng ngày, bỏ Thứ 7/CN (không trừ lễ).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import config

SETTLEMENT_DAYS = 2  # T+2
_DATE_FMT = "%Y-%m-%d"


def is_cash_stock(symbol: Any) -> bool:
    """True nếu là cổ phiếu cơ sở (CKCS); False nếu phái sinh (VN30F / CKPS)."""
    s = str(symbol or "").upper()
    if not s:
        return False
    if s.startswith("VN30F"):
        return False
    deriv = {str(x).upper() for x in getattr(config, "CKPS_SYMBOLS", []) or []}
    if s in deriv:
        return False
    # mã hợp đồng phái sinh thật (vd 41I1G6000) — nhận diện theo cờ nếu có
    deriv_real = {str(x).upper() for x in getattr(config, "DERIVATIVE_REAL_SYMBOLS", []) or []}
    if s in deriv_real:
        return False
    return True


def add_working_days(start_date: datetime, days: int, working_dates: Optional[List[str]] = None) -> datetime:
    """Cộng `days` ngày làm việc kể từ start_date.

    Có working_dates (từ API) → dùng đúng lịch (trừ lễ + cuối tuần).
    Không có → fallback bỏ Thứ 7/CN.
    """
    if working_dates:
        sd = start_date.strftime(_DATE_FMT)
        after = sorted(d for d in working_dates if d > sd)
        if len(after) >= days:
            return datetime.strptime(after[days - 1], _DATE_FMT)
        # thiếu dữ liệu → rơi xuống fallback
    d = start_date
    added = 0
    while added < days:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # 0-4 = T2-T6
            added += 1
    return d


def settle_date(buy_date: datetime, working_dates: Optional[List[str]] = None) -> datetime:
    """Ngày cổ phiếu về (T+2) tính từ ngày mua."""
    return add_working_days(buy_date, SETTLEMENT_DAYS, working_dates)


def settle_date_str(buy_date: datetime, working_dates: Optional[List[str]] = None) -> str:
    return settle_date(buy_date, working_dates).strftime(_DATE_FMT)


def is_settled(settle_date_value: Any, today: Optional[datetime] = None) -> bool:
    """CK đã về chưa (so với hôm nay)."""
    if not settle_date_value:
        return True  # không có thông tin → coi như đã về (an toàn cho mã cũ)
    today = today or datetime.now()
    try:
        sd = datetime.strptime(str(settle_date_value)[:10], _DATE_FMT)
    except (ValueError, TypeError):
        return True
    return today.date() >= sd.date()


def available_to_sell(positions: Iterable[Dict[str, Any]], symbol: str, today: Optional[datetime] = None) -> float:
    """Tổng khối lượng cổ phiếu `symbol` đang giữ (long) ĐÃ VỀ, có thể bán."""
    sym = str(symbol or "").upper()
    total = 0.0
    for pos in positions or []:
        if str(pos.get("symbol", "")).upper() != sym:
            continue
        if int(pos.get("type", 0)) != 0:  # chỉ long (BUY=0)
            continue
        if is_settled(pos.get("settle_date"), today):
            total += float(pos.get("volume", 0.0) or 0.0)
    return total


def pending_to_settle(positions: Iterable[Dict[str, Any]], symbol: str, today: Optional[datetime] = None) -> float:
    """Khối lượng cổ phiếu `symbol` CHƯA về (chờ T+2)."""
    sym = str(symbol or "").upper()
    total = 0.0
    for pos in positions or []:
        if str(pos.get("symbol", "")).upper() != sym:
            continue
        if int(pos.get("type", 0)) != 0:
            continue
        if not is_settled(pos.get("settle_date"), today):
            total += float(pos.get("volume", 0.0) or 0.0)
    return total
