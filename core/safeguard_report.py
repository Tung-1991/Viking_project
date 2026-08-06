# -*- coding: utf-8 -*-
"""Tổng hợp & hiển thị 'luật bot đang áp' (effective safeguard).

Luật bot nằm rải 3 lớp: config.py (mặc định) -> brain_settings.json (chỉnh chung)
-> symbol_overrides (chỉnh riêng từng mã). `get_brain_settings_for_symbol(symbol)` đã
merge sẵn 3 lớp này; module đây chỉ trích các field quan trọng và format thành bảng
dễ đọc để LOG ra lúc khởi động, giúp operator thấy ngay bot đang bị giới hạn bởi gì.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import config

# (key trong bot_safeguard, nhãn tiếng Việt) — chỉ các luật người vận hành quan tâm.
KEY_FIELDS = [
    ("MAX_DAILY_LOSS_PERCENT", "Lỗ tối đa/ngày (%)"),
    ("MAX_OPEN_POSITIONS", "Vị thế mở tối đa"),
    ("MAX_TRADES_PER_DAY", "Lệnh tối đa/ngày"),
    ("MAX_LOSING_STREAK", "Chuỗi thua tối đa"),
    ("VOLATILITY_BRAKE_ENABLED", "Phanh biến động"),
    ("VOLATILITY_BRAKE_WINDOW_SECONDS", "Cửa sổ biến động (s)"),
    ("VOLATILITY_BRAKE_STOCK_PCT", "Ngưỡng CKCS (%)"),
    ("VOLATILITY_BRAKE_DERIVATIVE_POINTS", "Ngưỡng CKPS (điểm)"),
    ("COOLDOWN_MINUTES", "Nghỉ giữa lệnh (phút)"),
    ("BOT_ORDER_MODE", "Kiểu khớp (NORMAL/AUTO ATO-ATC)"),
    ("BOT_ATC_EXIT", "Đóng vị thế phiên ATC cuối ngày"),
    ("CHECK_SPREAD", "Chặn spread bất thường"),
    ("MAX_SPREAD_POINTS", "Spread tối đa"),
    ("CHECK_PING", "Chặn theo ping"),
    ("MAX_PING_MS", "Ping tối đa (ms)"),
    ("BOT_USE_TP", "Đặt TP cho bot"),
    ("BOT_TP_RR_RATIO", "Tỉ lệ R cho TP"),
]


def effective_safeguard(symbol: Optional[str] = None) -> Dict[str, Any]:
    """Trả về bot_safeguard đã merge 3 lớp cho 1 mã (hoặc global nếu symbol=None)."""
    # Import lười để tránh phụ thuộc vòng khi nạp module sớm.
    from core.storage_manager import get_brain_settings_for_symbol

    brain = get_brain_settings_for_symbol(symbol) or {}
    return brain.get("bot_safeguard", {}) or {}


def format_effective_table(symbols: Optional[List[str]] = None) -> str:
    """Bảng nhiều dòng: mỗi mã active + giá trị luật đang thực sự áp."""
    if symbols is None:
        symbols = list(getattr(config, "BOT_ACTIVE_SYMBOLS", []) or [])
    if not symbols:
        symbols = [None]  # chỉ có global

    lines = ["⚙️ LUẬT BOT ĐANG ÁP (effective safeguard, đã gộp config + chỉnh chung + chỉnh riêng):"]
    for sym in symbols:
        sg = effective_safeguard(sym)
        label = sym if sym else "(global)"
        if not sg:
            lines.append(f"  • {label}: (chưa có cấu hình safeguard)")
            continue
        parts = []
        for key, name in KEY_FIELDS:
            if key in sg:
                parts.append(f"{name}={sg[key]}")
        lines.append(f"  • {label}: " + "; ".join(parts))
    return "\n".join(lines)


def log_effective_safeguard(logger, symbols: Optional[List[str]] = None) -> None:
    """Log bảng luật đang áp (không bao giờ ném lỗi ra ngoài)."""
    try:
        logger.info(format_effective_table(symbols))
    except Exception:
        pass
