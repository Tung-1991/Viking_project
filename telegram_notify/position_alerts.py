# -*- coding: utf-8 -*-
"""Immediate, read-only Telegram alerts for positions that are already open."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Iterable

from .client import TelegramClient
from .settings import account_dir, load_settings


_LOCK = threading.RLock()


def state_path() -> str:
    return os.path.join(account_dir(), "telegram_position_alert_state.json")


def _read_state() -> Dict[str, Any]:
    try:
        with open(state_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(data: Dict[str, Any]) -> None:
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _number(value: float) -> str:
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


def _mode(ticket: str) -> str:
    return "PAPER" if str(ticket or "").upper().startswith("PAPER-") else "REAL"


def _send(message: str, setting_key: str) -> Dict[str, Any]:
    settings = load_settings()
    if not settings.get(setting_key):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    chat_id = str(settings.get("opportunity_chat_id") or "").strip()
    if not chat_id:
        return {"ok": False, "skipped": True, "reason": "missing_chat_id"}
    return TelegramClient(
        token_env=settings.get("bot_token_env", "TELE_BOT_KEY"),
    ).send_long_message(
        chat_id,
        message,
        chunk_size=settings.get("chunk_size", 3500),
        title="",
    )


def _observe_zone(
    key: str,
    active: bool,
    message: str,
    *,
    now: float,
    cooldown_seconds: float,
) -> Dict[str, Any]:
    with _LOCK:
        state = _read_state()
        zones = state.setdefault("zones", {})
        tracked = zones.setdefault(key, {"active": False, "last_sent": 0.0})
        if not active:
            if tracked.get("active"):
                tracked["active"] = False
                _write_state(state)
            return {"ok": False, "skipped": True, "reason": "outside_zone"}
        if tracked.get("active"):
            return {"ok": False, "skipped": True, "reason": "already_alerted"}
        if now - float(tracked.get("last_sent", 0.0) or 0.0) < cooldown_seconds:
            tracked["active"] = True
            _write_state(state)
            return {"ok": False, "skipped": True, "reason": "cooldown"}

    result = _send(message, "position_level_alerts_enabled")
    if result.get("ok"):
        with _LOCK:
            state = _read_state()
            state.setdefault("zones", {})[key] = {
                "active": True,
                "last_sent": now,
            }
            _write_state(state)
    return result


def observe_position(
    position: Any,
    current_price: float,
    *,
    initial_r_dist: float = 0.0,
    now: float | None = None,
) -> list[Dict[str, Any]]:
    """Optionally alert once when an open position approaches its SL or TP."""
    settings = load_settings()
    if not settings.get("position_level_alerts_enabled", False):
        return []
    now = time.time() if now is None else float(now)
    current = float(current_price or getattr(position, "price_current", 0.0) or 0.0)
    entry = float(getattr(position, "price_open", 0.0) or 0.0)
    sl = float(getattr(position, "sl", 0.0) or 0.0)
    tp = float(getattr(position, "tp", 0.0) or 0.0)
    ticket = str(getattr(position, "ticket", "") or "")
    symbol = str(getattr(position, "symbol", "") or "").upper()
    is_long = int(getattr(position, "type", 0) or 0) == 0
    risk = abs(float(initial_r_dist or 0.0))
    if risk <= 0 and entry > 0 and sl > 0:
        risk = abs(entry - sl)
    if not ticket or not symbol or current <= 0 or risk <= 0:
        return []

    threshold_r = float(settings.get("position_alert_distance_r", 0.2) or 0.2)
    cooldown = (
        float(settings.get("position_alert_cooldown_minutes", 15.0) or 15.0)
        * 60.0
    )
    direction = "LONG" if is_long else "SHORT"
    mode = _mode(ticket)
    results: list[Dict[str, Any]] = []

    if sl > 0:
        distance = (current - sl) if is_long else (sl - current)
        distance_r = distance / risk
        results.append(
            _observe_zone(
                f"{mode}|{ticket}|SL",
                -1e-9 <= distance_r <= threshold_r + 1e-9,
                (
                    f"⚠️ VỊ THẾ ĐANG MỞ GẦN SL\n"
                    f"{mode} | {symbol} {direction} | #{ticket}\n"
                    f"Giá {_number(current)} | SL {_number(sl)} | còn {max(0.0, distance_r):.2f}R\n"
                    "Chỉ cảnh báo để kiểm tra; không tự đóng lệnh."
                ),
                now=now,
                cooldown_seconds=cooldown,
            )
        )

    if tp > 0:
        distance = (tp - current) if is_long else (current - tp)
        distance_r = distance / risk
        results.append(
            _observe_zone(
                f"{mode}|{ticket}|TP",
                -1e-9 <= distance_r <= threshold_r + 1e-9,
                (
                    f"🎯 VỊ THẾ ĐANG MỞ GẦN TP\n"
                    f"{mode} | {symbol} {direction} | #{ticket}\n"
                    f"Giá {_number(current)} | TP {_number(tp)} | còn {max(0.0, distance_r):.2f}R\n"
                    "Chỉ cảnh báo để theo dõi; không tự chốt lệnh."
                ),
                now=now,
                cooldown_seconds=cooldown,
            )
        )
    return results


def observe_reversal(
    positions: Iterable[Any],
    symbol: str,
    signal_action: str,
    *,
    current_price: float = 0.0,
) -> list[Dict[str, Any]]:
    """Alert once per signal phase when an open position gets an opposite signal."""
    settings = load_settings()
    if not settings.get("position_reversal_alerts_enabled", True):
        return []
    symbol = str(symbol or "").upper()
    action = str(signal_action or "NONE").upper()
    results: list[Dict[str, Any]] = []

    with _LOCK:
        state = _read_state()
        phases = state.setdefault("reversal_phases", {})
        changed = False
        for position in positions or []:
            if str(getattr(position, "symbol", "") or "").upper() != symbol:
                continue
            ticket = str(getattr(position, "ticket", "") or "")
            is_long = int(getattr(position, "type", 0) or 0) == 0
            opposite = "SELL" if is_long else "BUY"
            key = f"{_mode(ticket)}|{ticket}"
            if action != opposite:
                if phases.pop(key, None) is not None:
                    changed = True
                continue
            if phases.get(key) == action:
                results.append(
                    {"ok": False, "skipped": True, "reason": "already_alerted"}
                )
                continue

            direction = "LONG" if is_long else "SHORT"
            price_line = (
                f"\nGiá hiện tại {_number(current_price)}" if current_price > 0 else ""
            )
            result = _send(
                f"🔄 TÍN HIỆU ĐẢO CHIỀU TRÊN VỊ THẾ ĐANG MỞ\n"
                f"{_mode(ticket)} | {symbol} {direction} | #{ticket}\n"
                f"Tín hiệu mới: {action}{price_line}\n"
                "Gợi ý kiểm tra thoát lệnh; cảnh báo này không tự đóng.",
                "position_reversal_alerts_enabled",
            )
            results.append(result)
            if result.get("ok"):
                phases[key] = action
                changed = True
        if changed:
            _write_state(state)
    return results
