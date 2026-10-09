"""Read-only account polling and safe diagnostics, independent of trading rules."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from ...trading.market import VN_TZ

FAST_POLL_SECONDS = 5.0
IDLE_POLL_SECONDS = 300.0
IDLE_ALERT_SECONDS = 600.0


def account_poll_interval(
    phase: str, orders: Iterable[Any] | None, *, symbol_phases: dict | None = None,
) -> float:
    """Slow only a known closed market with no unresolved REAL broker order."""
    if str(phase).upper() != "CLOSED" or orders is None:
        return FAST_POLL_SECONDS
    if any(str(value).upper() != "CLOSED" for value in (symbol_phases or {}).values()):
        return FAST_POLL_SECONDS
    terminal = {"FILLED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"}
    unresolved = {"SENDING", "WORKING", "PARTIAL", "UNKNOWN", "CANCEL_PENDING", "REPLACE_PENDING"}
    for order in orders:
        get = order.get if isinstance(order, dict) else lambda key, default=None: getattr(order, key, default)
        if str(get("execution_mode", "REAL")).upper() == "PAPER":
            continue
        state = str(get("status", "")).upper()
        if state in unresolved or (state not in terminal and get("broker_order_id")):
            return FAST_POLL_SECONDS
    return IDLE_POLL_SECONDS


def safe_detail(value: Any, *, secrets: Iterable[str] = ()) -> str:
    """Keep diagnostic text without credentials, JWTs, or injected log lines."""
    text = str(value or "")
    for secret in sorted({str(item) for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:x-api-key|x-signature|api[_-]?secret|api[_-]?key|trading[_-]?token|authorization|token|secret|password)"
        r"[\s\"']*[:=][\s\"']*)[^\s,;&\"']+", r"\1[REDACTED]", text,
    )
    return " ".join(text.split())[:220]


def error_summary(error: Exception, *, secrets: Iterable[str] = ()) -> str:
    """Snapshot exceptions already include a masked endpoint and HTTP status."""
    return safe_detail(f"{type(error).__name__}: {error}", secrets=secrets)


def snapshot_failure_summary(failure: dict, poll_seconds: float, *, secrets: Iterable[str] = ()) -> str:
    """Human-readable failure; all detail originates from the failed read."""
    try:
        good_at = float(failure.get("last_success_at") or 0)
        good_text = datetime.fromtimestamp(good_at, VN_TZ).strftime("%d/%m %H:%M:%S") if good_at > 0 else "chưa có"
    except (TypeError, ValueError, OverflowError, OSError):
        good_text = "chưa có"
    idle = poll_seconds == IDLE_POLL_SECONDS
    retry = "5 phút" if idle else "5 giây"
    detail = safe_detail(failure.get("error") or "chưa có chi tiết", secrets=secrets)
    return (
        f"{'NGOÀI PHIÊN · ' if idle else ''}Lỗi đồng bộ tài khoản REAL: {detail}\n"
        f"Đồng bộ tốt gần nhất: {good_text} · Thử lại sau {retry}.\n"
        "Giữ bản cũ; không dùng bản cũ để gửi lệnh khi đọc tài khoản lỗi."
    )
