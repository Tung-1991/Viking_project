# -*- coding: utf-8 -*-
"""Important, state-based Telegram alerts for the report channel."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Iterable

from .reporter import send_text_report
from .settings import account_dir, load_settings


_LOCK = threading.RLock()
_HEALTH: Dict[str, Dict[str, Any]] = {}


def state_path() -> str:
    return os.path.join(account_dir(), "telegram_system_alert_state.json")


def _read() -> Dict[str, Any]:
    try:
        with open(state_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: Dict[str, Any]) -> None:
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _enabled() -> bool:
    settings = load_settings()
    return bool(settings.get("system_alerts_enabled", True)) and bool(
        str(settings.get("report_chat_id") or "").strip()
    )


def _send(message: str, title: str = "RAT6 CẢNH BÁO HỆ THỐNG") -> Dict[str, Any]:
    if not _enabled():
        return {"ok": False, "skipped": True, "reason": "disabled"}
    return send_text_report(message, title=title, require_enabled=False)


def send_once(key: str, message: str, *, title: str = "RAT6 CẢNH BÁO HỆ THỐNG") -> Dict[str, Any]:
    """Persist a unique incident key so polling cannot spam the group."""
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "skipped": True, "reason": "missing_key"}
    with _LOCK:
        state = _read()
        sent = state.setdefault("sent", {})
        if key in sent:
            return {"ok": False, "skipped": True, "reason": "duplicate"}
    result = _send(message, title=title)
    if result.get("ok"):
        with _LOCK:
            state = _read()
            sent = state.setdefault("sent", {})
            sent[key] = time.time()
            cutoff = time.time() - 30 * 86400
            state["sent"] = {
                name: value for name, value in sent.items()
                if float(value or 0.0) >= cutoff
            }
            _write(state)
    return result


def observe_health(
    name: str,
    state: str,
    down_states: Iterable[str],
    *,
    now: float | None = None,
    threshold_seconds: float = 60.0,
) -> Dict[str, Any]:
    """Send once after a sustained outage and once when it recovers."""
    now = time.time() if now is None else float(now)
    name = str(name or "").upper()
    current = str(state or "UNKNOWN").upper()
    is_down = current in {str(value or "").upper() for value in down_states}
    with _LOCK:
        tracked = _HEALTH.setdefault(
            name,
            {"down_since": 0.0, "alerted": False, "last_state": ""},
        )
        if is_down:
            if not tracked["down_since"]:
                tracked["down_since"] = now
            tracked["last_state"] = current
            if tracked["alerted"] or now - tracked["down_since"] < threshold_seconds:
                return {"ok": False, "skipped": True, "reason": "waiting_or_sent"}
            tracked["alerted"] = True
            return _send(f"{name}: {current} kéo dài trên {int(threshold_seconds)} giây.")

        was_alerted = bool(tracked.get("alerted"))
        tracked.update({"down_since": 0.0, "alerted": False, "last_state": current})
        if was_alerted:
            return _send(f"{name}: ĐÃ PHỤC HỒI ({current}).")
    return {"ok": False, "skipped": True, "reason": "healthy"}


def observe_global_cooldown(until: float, reason: str = "", *, now: float | None = None) -> Dict[str, Any]:
    now = time.time() if now is None else float(now)
    active = float(until or 0.0) > now
    with _LOCK:
        state = _read()
        previous = state.get("global_cooldown", {}) if isinstance(state.get("global_cooldown"), dict) else {}
        was_active = bool(previous.get("active"))
        if active and not was_active:
            state["global_cooldown"] = {
                "active": True,
                "until": float(until),
                "reason": str(reason or ""),
            }
            _write(state)
            minutes = max(1, int((float(until) - now) / 60.0))
            return _send(
                f"GLOBAL COOLDOWN BẮT ĐẦU — còn khoảng {minutes} phút"
                f"{' | ' + str(reason) if reason else ''}."
            )
        if active and was_active:
            # Cooldown có thể được gia hạn; chỉ cập nhật mốc, không gửi lại.
            state["global_cooldown"] = {
                "active": True,
                "until": float(until),
                "reason": str(reason or previous.get("reason") or ""),
            }
            _write(state)
            return {"ok": False, "skipped": True, "reason": "unchanged"}
        if not active and was_active:
            state["global_cooldown"] = {"active": False, "until": 0.0, "reason": ""}
            _write(state)
            return _send("GLOBAL COOLDOWN ĐÃ HẾT.")
    return {"ok": False, "skipped": True, "reason": "unchanged"}


def notify_order_failure(result: Any, *, symbol: str = "", side: str = "") -> Dict[str, Any]:
    error = str(getattr(result, "error", "") or "")
    status = str(getattr(result, "status", "") or "")
    raw = getattr(result, "raw", {}) or {}
    request_id = str(raw.get("request_id") or raw.get("request_tag") or "")
    order_id = str(getattr(result, "order_id", "") or "")
    unique = request_id or order_id or f"{symbol}|{side}|{error}|{int(time.time() // 60)}"
    if error == "ORDER_STATUS_UNKNOWN" or status == "UNKNOWN":
        label = "TRẠNG THÁI LỆNH CHƯA XÁC ĐỊNH"
    else:
        label = "LỆNH REAL BỊ TỪ CHỐI"
    message = (
        f"{label}\n{str(symbol or '').upper()} {str(side or '').upper()}"
        f"\nMã lỗi: {error or status or 'UNKNOWN'}"
        f"\n{str(getattr(result, 'message', '') or '')}"
    )
    return send_once(f"ORDER|{unique}", message)


def notify_emergency_close_failure(failures: list[str]) -> Dict[str, Any]:
    failures = [str(item) for item in failures if str(item)]
    key = "|".join(sorted(failures)) or str(int(time.time() // 60))
    return send_once(
        f"EMERGENCY_CLOSE|{key}",
        "ĐÓNG LỆNH KHẨN CẤP THẤT BẠI\n" + "\n".join(failures or ["Không rõ vị thế"]),
    )
