# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import time
import uuid
import math
from datetime import datetime, timedelta
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

import config
from core import storage_manager

PENDING = "PENDING"
SENDING = "SENDING"
WORKING = "WORKING"
ERROR = "ERROR"
SENT = "SENT"
FAILED = "FAILED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
UNKNOWN = "UNKNOWN"

FINAL_STATUSES = {SENT, FAILED, EXPIRED, CANCELLED, UNKNOWN}
_LOCK = threading.RLock()


def _path() -> str:
    base = getattr(storage_manager, "_active_account_dir", "data") or "data"
    return os.path.join(base, "pending_orders.json")


def _now() -> float:
    return time.time()


def _expire_hours() -> float:
    default = float(getattr(config, "PENDING_ORDER_EXPIRE_HOURS", 24.0) or 24.0)
    try:
        brain = storage_manager.load_brain_settings()
        safe = brain.get("bot_safeguard", {}) if isinstance(brain, dict) else {}
        return max(0.01, float(safe.get("PENDING_ORDER_EXPIRE_HOURS", default)))
    except Exception:
        return max(0.01, default)


def _next_target_session_end(symbol: str, target: str, created_at: float) -> float:
    """Return the end of the next eligible trading phase.

    A wall-clock expiry must never remove an order before it has had at least
    one chance to reach its requested phase.  The cached DNSE working-date
    calendar is used when available; weekdays remain the safe fallback.
    """
    created = datetime.fromtimestamp(float(created_at))
    target = str(target or "OPEN").upper()
    is_derivative = str(symbol or "").upper().startswith("VN30F")
    if target == "ATO":
        end_minute = 540 if is_derivative else 555
    elif target == "ATC":
        end_minute = 885
    else:
        end_minute = 870

    try:
        from core.market_calendar import date_status
    except Exception:
        date_status = None

    for offset in range(0, 31):
        day = created + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        if date_status is not None:
            try:
                if str(date_status(day).get("status", "")).upper() in {
                    "WEEKEND",
                    "HOLIDAY",
                }:
                    continue
            except Exception:
                pass
        session_end = day.replace(
            hour=end_minute // 60,
            minute=end_minute % 60,
            second=0,
            microsecond=0,
        )
        if session_end > created:
            return session_end.timestamp()
    return created_at


def _order_expire_at(
    *,
    symbol: str,
    target: str,
    created_at: float,
    expire_hours: float,
) -> float:
    wall_clock_expiry = float(created_at) + (float(expire_hours) * 3600.0)
    next_session_end = _next_target_session_end(symbol, target, created_at)
    return max(wall_clock_expiry, next_session_end)


def _normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(entry or {})
    item.setdefault("id", str(uuid.uuid4()))
    action = str(item.get("action", "OPEN") or "OPEN").strip().upper()
    item["action"] = action if action in ("OPEN", "CLOSE") else "OPEN"
    item["symbol"] = str(item.get("symbol", "") or "").strip().upper()
    item["side"] = str(item.get("side", "BUY") or "BUY").strip().upper()
    item["preset"] = str(item.get("preset", getattr(config, "DEFAULT_PRESET", "SCALPING")) or "SCALPING")
    item["lot"] = float(item.get("lot", 0.0) or 0.0)
    item["entry_price"] = float(item.get("entry_price", 0.0) or 0.0)
    item["sl"] = float(item.get("sl", 0.0) or 0.0)
    item["tp"] = float(item.get("tp", 0.0) or 0.0)
    target = str(item.get("target", "") or "").upper()
    item["target"] = target if target in ("ATO", "ATC", "OPEN") else ("OPEN" if item["entry_price"] > 0 else "ATO")
    item.setdefault("created_at", _now())
    item.setdefault("expire_at", float(item["created_at"]) + (_expire_hours() * 3600.0))
    item["status"] = str(item.get("status", PENDING) or PENDING).upper()
    if item["action"] == "OPEN" and item["status"] == PENDING:
        item["expire_at"] = max(
            float(item.get("expire_at", 0.0) or 0.0),
            _next_target_session_end(
                item["symbol"],
                item["target"],
                float(item["created_at"]),
            ),
        )
    item.setdefault("note", "")
    item.setdefault("result", "")
    item.setdefault("dnse_order_id", "")
    item.setdefault("order_kind", item["target"] if item["target"] in ("ATO", "ATC") else "")
    item.setdefault("manual_entry_tactic", "")
    item.setdefault("lot_source", "")
    item.setdefault("sl_source", "")
    item.setdefault("tp_source", "")
    item.setdefault("entry_source", "")
    item.setdefault("plan", "")
    mode = str(item.get("entry_mode", "LIMIT" if item["entry_price"] > 0 else "MARKET") or "MARKET").upper()
    item["entry_mode"] = mode if mode in ("MARKET", "LIMIT") else "MARKET"
    item["wait_for_trigger"] = bool(item.get("wait_for_trigger", False))
    item["trigger_price"] = float(item.get("trigger_price", item["entry_price"]) or 0.0)
    item["slippage_ticks"] = max(0, int(item.get("slippage_ticks", 0) or 0))
    item.setdefault("opportunity_id", "")
    item["position_ticket"] = str(item.get("position_ticket", "") or "")
    item.setdefault("close_comment", "")
    item.setdefault("retry_at", 0.0)
    item["bypass_checklist"] = bool(item.get("bypass_checklist", False))
    # Lệnh hẹn cũ chưa có mode được coi là PAPER để không thể vô tình gửi tiền thật.
    mode = str(item.get("execution_mode", "PAPER") or "PAPER").strip().upper()
    item["execution_mode"] = mode if mode in ("PAPER", "REAL") else "PAPER"
    return item


def _read_unlocked() -> List[Dict[str, Any]]:
    path = _path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [_normalize(x) for x in data if isinstance(x, dict)]
    except Exception:
        return []
    return []


def _write_unlocked(items: List[Dict[str, Any]]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([_normalize(x) for x in items], f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def add_order(
    *,
    symbol: str,
    side: str,
    preset: str,
    lot: float = 0.0,
    entry_price: float = 0.0,
    sl: float = 0.0,
    tp: float = 0.0,
    target: Optional[str] = None,
    note: str = "",
    expire_hours: Optional[float] = None,
    manual_entry_tactic: str = "",
    lot_source: str = "",
    sl_source: str = "",
    tp_source: str = "",
    entry_source: str = "",
    plan: str = "",
    execution_mode: Optional[str] = None,
    entry_mode: Optional[str] = None,
    wait_for_trigger: bool = False,
    trigger_price: float = 0.0,
    slippage_ticks: int = 0,
    opportunity_id: str = "",
    bypass_checklist: bool = False,
) -> Dict[str, Any]:
    created_at = _now()
    hours = _expire_hours() if expire_hours is None else max(0.01, float(expire_hours))
    resolved_target = target or ("OPEN" if float(entry_price or 0.0) > 0 else "ATO")
    item = _normalize(
        {
            "id": str(uuid.uuid4()),
            "symbol": symbol,
            "side": side,
            "preset": preset,
            "lot": lot,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "target": resolved_target,
            "created_at": created_at,
            "expire_at": _order_expire_at(
                symbol=symbol,
                target=resolved_target,
                created_at=created_at,
                expire_hours=hours,
            ),
            "status": PENDING,
            "note": note,
            "manual_entry_tactic": manual_entry_tactic,
            "lot_source": lot_source,
            "sl_source": sl_source,
            "tp_source": tp_source,
            "entry_source": entry_source,
            "plan": plan,
            "execution_mode": execution_mode or (
                "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
            ),
            "entry_mode": entry_mode or ("LIMIT" if float(entry_price or 0.0) > 0 else "MARKET"),
            "wait_for_trigger": wait_for_trigger,
            "trigger_price": trigger_price or entry_price,
            "slippage_ticks": slippage_ticks,
            "opportunity_id": opportunity_id,
            "bypass_checklist": bypass_checklist,
        }
    )
    with _LOCK:
        items = _read_unlocked()
        items.append(item)
        _write_unlocked(items)
    return deepcopy(item)


def add_close_order(
    *,
    symbol: str,
    position_ticket: Any,
    side: str,
    lot: float = 0.0,
    execution_mode: Optional[str] = None,
    comment: str = "Manual_Close",
    note: str = "",
    entry_mode: str = "MARKET",
    limit_price: float = 0.0,
) -> Dict[str, Any]:
    """Cache a manual close until the next continuous OPEN session.

    Close requests do not expire: they remain pending until executed, cancelled
    by the user, or finalized because the position no longer exists.
    """
    created_at = _now()
    mode = str(
        execution_mode
        or ("PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL")
    ).strip().upper()
    mode = mode if mode in ("PAPER", "REAL") else "PAPER"
    close_mode = str(entry_mode or "MARKET").strip().upper()
    close_mode = close_mode if close_mode in ("MARKET", "LIMIT") else "MARKET"
    close_limit = max(0.0, float(limit_price or 0.0))
    if close_mode == "LIMIT" and close_limit <= 0:
        raise ValueError("LIMIT close requires limit_price > 0")
    ticket = str(position_ticket or "")
    with _LOCK:
        items = _read_unlocked()
        for existing in items:
            if (
                str(existing.get("action", "OPEN")).upper() == "CLOSE"
                and str(existing.get("position_ticket", "")) == ticket
                and str(existing.get("execution_mode", "PAPER")).upper() == mode
                and str(existing.get("status", "")).upper() not in FINAL_STATUSES
            ):
                return deepcopy(_normalize(existing))
        item = _normalize(
            {
                "id": str(uuid.uuid4()),
                "action": "CLOSE",
                "symbol": symbol,
                "side": side,
                "preset": "",
                "lot": lot,
                "entry_price": close_limit,
                "sl": 0.0,
                "tp": 0.0,
                "target": "OPEN",
                "created_at": created_at,
                "expire_at": 0.0,
                "status": PENDING,
                "note": note or "Đóng vị thế khi thị trường mở",
                "plan": (
                    f"OPEN -> CLOSE LIMIT @{close_limit:g} #{ticket}"
                    if close_mode == "LIMIT"
                    else f"OPEN -> CLOSE MARKET #{ticket}"
                ),
                "execution_mode": mode,
                "entry_mode": close_mode,
                # PAPER has no resting broker order.  Wait until its executable
                # bid/ask reaches the requested limit, then close at that tick.
                "wait_for_trigger": close_mode == "LIMIT" and mode == "PAPER",
                "trigger_price": close_limit,
                "position_ticket": ticket,
                "close_comment": comment,
            }
        )
        items.append(item)
        _write_unlocked(items)
    return deepcopy(item)


def list_all() -> List[Dict[str, Any]]:
    with _LOCK:
        return deepcopy(_read_unlocked())


def list_active() -> List[Dict[str, Any]]:
    return [x for x in list_all() if str(x.get("status", "")).upper() not in FINAL_STATUSES]


def find_active_close(
    position_ticket: Any,
    execution_mode: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the non-final close request for one position, if any."""
    ticket = str(position_ticket or "")
    mode = str(execution_mode or "").strip().upper()
    if not ticket:
        return None
    with _LOCK:
        for item in _read_unlocked():
            if str(item.get("action", "OPEN")).upper() != "CLOSE":
                continue
            if str(item.get("position_ticket", "")) != ticket:
                continue
            if mode and str(item.get("execution_mode", "PAPER")).upper() != mode:
                continue
            if str(item.get("status", "")).upper() in FINAL_STATUSES:
                continue
            return deepcopy(_normalize(item))
    return None


def mark(order_id: str, status: str, result: str = "", **updates: Any) -> Optional[Dict[str, Any]]:
    status = str(status or "").upper()
    with _LOCK:
        items = _read_unlocked()
        found = None
        for item in items:
            if str(item.get("id")) == str(order_id):
                item["status"] = status
                if result:
                    item["result"] = str(result)
                for key, value in updates.items():
                    item[key] = value
                if status in FINAL_STATUSES and "finalized_at" not in updates:
                    item["finalized_at"] = _now()
                found = _normalize(item)
                item.update(found)
                break
        _write_unlocked(items)
    return deepcopy(found) if found else None


def cancel(order_id: str, result: str = "User cancelled") -> Optional[Dict[str, Any]]:
    with _LOCK:
        items = _read_unlocked()
        found = None
        for item in items:
            if str(item.get("id")) == str(order_id):
                if str(item.get("status", "")).upper() in (
                    PENDING,
                    FAILED,
                    EXPIRED,
                    ERROR,
                ):
                    item["status"] = CANCELLED
                    item["result"] = result
                    found = _normalize(item)
                    item.update(found)
                break
        _write_unlocked(items)
    return deepcopy(found) if found else None


def delete_final(order_id: str) -> bool:
    with _LOCK:
        items = _read_unlocked()
        next_items = [
            item for item in items
            if not (str(item.get("id")) == str(order_id) and str(item.get("status", "")).upper() in FINAL_STATUSES)
        ]
        changed = len(next_items) != len(items)
        if changed:
            _write_unlocked(next_items)
        return changed


def expire_pending(now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = _now() if now is None else float(now)
    expired: List[Dict[str, Any]] = []
    with _LOCK:
        items = _read_unlocked()
        for item in items:
            expire_at = float(item.get("expire_at", 0.0) or 0.0)
            if (
                str(item.get("status", "")).upper() == PENDING
                and expire_at > 0
                and expire_at <= now
            ):
                item["status"] = EXPIRED
                item["result"] = "Expired before market phase"
                item["finalized_at"] = now
                expired.append(_normalize(item))
                item.update(expired[-1])
        if expired:
            _write_unlocked(items)
    return deepcopy(expired)


# Trạng thái cuối đã "chết" -> được phép dọn khỏi bảng running (SENT giữ lại vì đã lên sàn).
_PURGEABLE_STATUSES = {EXPIRED, FAILED, CANCELLED}


def purge_stale(max_age_sec: Optional[float] = None, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Xóa hẳn lệnh local đã EXPIRED/FAILED/CANCELLED quá lâu khỏi pending_orders.json.

    Để bảng "LỆNH ĐANG CHẠY" không giữ mãi lệnh chết. SENT không đụng (đã lên sàn).
    Mốc tuổi ưu tiên finalized_at, fallback expire_at/created_at cho item cũ chưa có field.
    """
    if max_age_sec is None:
        hours = float(getattr(config, "PENDING_PURGE_AFTER_HOURS", 2.0) or 0.0)
        max_age_sec = hours * 3600.0
    if max_age_sec <= 0:
        return []
    now = _now() if now is None else float(now)
    removed: List[Dict[str, Any]] = []
    with _LOCK:
        items = _read_unlocked()
        kept = []
        for item in items:
            status = str(item.get("status", "")).upper()
            is_final_close = (
                str(item.get("action", "OPEN")).upper() == "CLOSE"
                and status == SENT
            )
            if status in _PURGEABLE_STATUSES or is_final_close:
                ref = float(
                    item.get("finalized_at")
                    or item.get("expire_at")
                    or item.get("created_at")
                    or 0.0
                )
                if ref and (now - ref) >= max_age_sec:
                    removed.append(deepcopy(item))
                    continue
            kept.append(item)
        if removed:
            _write_unlocked(kept)
    return removed


def recover_stuck(max_age_sec: float = 600.0, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Đưa order kẹt SENDING quá lâu (app crash giữa claim→gửi) về PENDING để thử lại."""
    now = _now() if now is None else float(now)
    recovered: List[Dict[str, Any]] = []
    with _LOCK:
        items = _read_unlocked()
        changed = False
        for item in items:
            if str(item.get("status", "")).upper() == SENDING:
                claimed = float(item.get("claimed_at", 0.0) or item.get("created_at", 0.0) or 0.0)
                if now - claimed > max_age_sec:
                    item["status"] = PENDING
                    item["result"] = "Khôi phục từ trạng thái SENDING bị kẹt"
                    recovered.append(_normalize(item))
                    item.update(recovered[-1])
                    changed = True
        if changed:
            _write_unlocked(items)
    return deepcopy(recovered)


def claim_due(
    phase_fn: Callable[[str], Any],
    now: Optional[float] = None,
    limit: int = 20,
    quote_fn: Optional[Callable[[str, str], Optional[float]]] = None,
    point_fn: Optional[Callable[[str], float]] = None,
    include_inactive_closes: bool = False,
) -> List[Dict[str, Any]]:
    now = _now() if now is None else float(now)
    due: List[Dict[str, Any]] = []
    with _LOCK:
        items = _read_unlocked()
        changed = False
        current_mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
        for item in items:
            if len(due) >= limit:
                break
            status = str(item.get("status", "")).upper()
            if status != PENDING:
                continue
            item_mode = str(item.get("execution_mode", "PAPER")).upper()
            action = str(item.get("action", "OPEN") or "OPEN").upper()
            if (
                item_mode != current_mode
                and not (include_inactive_closes and action == "CLOSE")
            ):
                continue
            if float(item.get("retry_at", 0.0) or 0.0) > now:
                continue
            expire_at = float(item.get("expire_at", 0.0) or 0.0)
            if expire_at > 0 and expire_at <= now:
                item["status"] = EXPIRED
                item["result"] = "Expired before market phase"
                changed = True
                continue
            try:
                phase_result = phase_fn(str(item.get("symbol", "")))
                phase = phase_result[0] if isinstance(phase_result, (tuple, list)) else str(phase_result)
            except Exception:
                phase = ""
            target = str(item.get("target", "") or "").upper()
            if item.get("wait_for_trigger"):
                trigger = float(item.get("trigger_price", 0.0) or 0.0)
                if trigger <= 0 or quote_fn is None:
                    continue
                try:
                    quote = float(quote_fn(str(item.get("symbol", "")), str(item.get("side", "BUY"))) or 0.0)
                except Exception:
                    quote = 0.0
                if quote <= 0:
                    continue
                try:
                    point = max(1e-9, float(point_fn(str(item.get("symbol", ""))) if point_fn else 0.1))
                except Exception:
                    point = 0.1
                tolerance = int(item.get("slippage_ticks", 0) or 0) * point
                side = str(item.get("side", "BUY") or "BUY").upper()
                touched = quote <= trigger + tolerance if side == "BUY" else quote >= trigger - tolerance
                if not touched:
                    continue
                # BUY làm tròn lên, SELL làm tròn xuống để LO gần giá đang chạy nhưng
                # không vượt quá vùng chệch người dùng cho phép.
                if side == "BUY":
                    nearest = math.ceil((quote - 1e-12) / point) * point
                    nearest = min(nearest, trigger + tolerance)
                else:
                    nearest = math.floor((quote + 1e-12) / point) * point
                    nearest = max(nearest, trigger - tolerance)
                item["entry_price"] = round(nearest, 10)
                item["wait_for_trigger"] = False
                item["result"] = f"TRIGGERED quote={quote:g} limit={nearest:g}"
            if (
                (target == "ATO" and phase == "ATO")
                or (target == "ATC" and phase == "ATC")
                or (target == "OPEN" and phase == "OPEN")
            ):
                item["status"] = SENDING
                item["claimed_at"] = now
                normalized = _normalize(item)
                item.update(normalized)
                due.append(normalized)
                changed = True
        if changed:
            _write_unlocked(items)
    return deepcopy(due)
