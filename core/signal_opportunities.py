# -*- coding: utf-8 -*-
"""Kho gợi ý BOT: tín hiệu quan sát được nhưng chưa phải lệnh giao dịch."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from core import storage_manager

ACTIVE = "SUGGESTED"
ACTIVATED = "ACTIVATED"
DISMISSED = "DISMISSED"
EXPIRED = "EXPIRED"

DEFAULT_SETTINGS = {
    "enabled": True,
    "show_in_running_table": True,
    "retention_hours": 24.0,
    "history_enabled": True,
    "default_order_mode": "MARKET",
    "default_slippage_ticks": 2,
}

_LOCK = threading.RLock()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _valid_setup(side: str, setup: Any) -> bool:
    if not isinstance(setup, dict) or setup.get("ok") is not True:
        return False
    side = str(side or "").upper()
    price = _float(setup.get("entry_price", setup.get("price")))
    sl = _float(setup.get("sl"))
    tp = _float(setup.get("tp"))
    if min(price, sl, tp) <= 0:
        return False
    return (sl < price < tp) if side == "BUY" else (tp < price < sl)


def _start_tracking(item: Dict[str, Any], now: float) -> bool:
    """Freeze the first valid Preview plan as one read-only virtual trade."""
    if isinstance(item.get("simulation"), dict):
        return False
    setup = item.get("order_setup")
    side = str(item.get("side") or "").upper()
    if not _valid_setup(side, setup):
        return False
    targets = []
    for value in setup.get("tp_targets") or [setup.get("tp")]:
        target = _float(value)
        if target > 0:
            targets.append(target)
    if not targets:
        return False
    entry = _float(setup.get("entry_price", setup.get("price")))
    quantity = _float(setup.get("display_quantity"))
    if quantity <= 0:
        quantity = _float(setup.get("lot"))
    item["simulation"] = {
        "status": "OPEN",
        "started_at": float(now),
        "entry": entry,
        "sl": _float(setup.get("sl")),
        "tp1": targets[0],
        "tp_targets": targets[:3],
        "quantity": quantity,
        "unit": str(setup.get("quantity_unit") or ("HĐ" if item.get("market_type") == "CKPS" else "CP")),
        "last_price": entry,
        "best_price": entry,
        "worst_price": entry,
    }
    return True


def _observe_item(item: Dict[str, Any], price: float, now: float) -> bool:
    simulation = item.get("simulation")
    if not isinstance(simulation, dict) or simulation.get("status") != "OPEN":
        return False
    price = _float(price)
    if price <= 0:
        return False
    side = str(item.get("side") or "").upper()
    entry = _float(simulation.get("entry"))
    sl = _float(simulation.get("sl"))
    tp1 = _float(simulation.get("tp1"))
    if min(entry, sl, tp1) <= 0:
        return False

    simulation["last_price"] = price
    simulation["last_seen_at"] = float(now)
    simulation["best_price"] = (
        max(_float(simulation.get("best_price"), entry), price)
        if side == "BUY"
        else min(_float(simulation.get("best_price"), entry), price)
    )
    simulation["worst_price"] = (
        min(_float(simulation.get("worst_price"), entry), price)
        if side == "BUY"
        else max(_float(simulation.get("worst_price"), entry), price)
    )

    outcome = ""
    close_price = price
    if side == "BUY":
        if price <= sl:
            outcome, close_price = "LOSS", sl
        elif price >= tp1:
            outcome, close_price = "WIN", tp1
    else:
        if price >= sl:
            outcome, close_price = "LOSS", sl
        elif price <= tp1:
            outcome, close_price = "WIN", tp1
    if not outcome:
        return True

    quantity = _float(simulation.get("quantity"))
    point_value = (
        _float(getattr(config, "DNSE_POINT_VALUE", 100000.0), 100000.0)
        if str(item.get("market_type") or "").upper() == "CKPS"
        else _float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0), 1000.0)
    )
    delta = close_price - entry if side == "BUY" else entry - close_price
    simulation.update(
        {
            "status": outcome,
            "result": "TP1" if outcome == "WIN" else "SL",
            "closed_at": float(now),
            "close_price": close_price,
            "pnl": delta * quantity * point_value,
        }
    )
    return True


def normalize_settings(value=None) -> Dict[str, Any]:
    result = dict(DEFAULT_SETTINGS)
    if isinstance(value, dict):
        result.update(value)
    result["enabled"] = bool(result.get("enabled", True))
    result["show_in_running_table"] = bool(result.get("show_in_running_table", True))
    result["history_enabled"] = bool(result.get("history_enabled", True))
    try:
        result["retention_hours"] = max(0.05, float(result.get("retention_hours", 24.0)))
    except (TypeError, ValueError):
        result["retention_hours"] = 24.0
    mode = str(result.get("default_order_mode", "MARKET") or "MARKET").upper()
    result["default_order_mode"] = mode if mode in {"MARKET", "LIMIT"} else "MARKET"
    try:
        result["default_slippage_ticks"] = max(0, int(result.get("default_slippage_ticks", 2)))
    except (TypeError, ValueError):
        result["default_slippage_ticks"] = 2
    return result


def load_settings() -> Dict[str, Any]:
    try:
        brain = storage_manager.load_brain_settings()
        return normalize_settings(brain.get("opportunity_settings", {}))
    except Exception:
        return normalize_settings()


def _base_dir() -> str:
    return getattr(storage_manager, "_active_account_dir", "data") or "data"


def active_path() -> str:
    return os.path.join(_base_dir(), "signal_opportunities.json")


def history_path() -> str:
    return os.path.join(_base_dir(), "signal_opportunity_history.json")


def _read(path: str) -> List[Dict[str, Any]]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _write(path: str, items: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _market_type(symbol: str) -> str:
    symbol = str(symbol or "").upper()
    derivatives = {str(x).upper() for x in getattr(config, "CKPS_SYMBOLS", []) or []}
    derivatives.update(str(x).upper() for x in getattr(config, "DERIVATIVE_REAL_SYMBOLS", []) or [])
    return "CKPS" if symbol.startswith("VN30F") or symbol in derivatives else "CKCS"


def _summary_context(context: Optional[dict]) -> dict:
    context = context if isinstance(context, dict) else {}
    keep = {
        "current_price", "bid", "ask", "market_mode", "mode_source", "block_reason",
        "trend_G0", "trend_G1", "trend_G2", "trend_G3", "latest_signal",
        "group_signals", "group_details", "group_rules",
        "atr_G0", "atr_G1", "atr_G2", "atr_G3",
        "swing_low_G0", "swing_low_G1", "swing_low_G2", "swing_low_G3",
        "swing_high_G0", "swing_high_G1", "swing_high_G2", "swing_high_G3",
    }
    return {key: deepcopy(value) for key, value in context.items() if key in keep}


def _archive_unlocked(item: Dict[str, Any], settings: Dict[str, Any]) -> None:
    if not settings.get("history_enabled", True):
        return
    history = _read(history_path())
    history.append(deepcopy(item))
    _write(history_path(), history)


def record_signal(
    signal: dict,
    block_reason: str = "BOT_OFF",
    now: Optional[float] = None,
    order_setup: Optional[dict] = None,
) -> Optional[dict]:
    settings = load_settings()
    if not settings.get("enabled", True):
        return None
    now = time.time() if now is None else float(now)
    symbol = str(signal.get("symbol", "") or "").upper()
    side = str(signal.get("action", "") or "").upper()
    if not symbol or side not in {"BUY", "SELL"}:
        return None
    mode = str(signal.get("execution_mode") or ("PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL")).upper()
    mode = mode if mode in {"PAPER", "REAL"} else "PAPER"
    context = _summary_context(signal.get("context"))
    detected_price = float(context.get("ask" if side == "BUY" else "bid", context.get("current_price", 0.0)) or 0.0)
    date_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    key = f"{date_key}|{mode}|{symbol}|{side}"

    with _LOCK:
        items = _read(active_path())
        found = next((item for item in items if item.get("dedupe_key") == key), None)
        if found is None:
            invalid_history = next(
                (
                    item for item in _read(history_path())
                    if item.get("dedupe_key") == key
                    and str(item.get("status") or "").upper() == "INVALID_LEVELS"
                ),
                None,
            )
            new_error = str((order_setup or {}).get("error") or "")
            if invalid_history is not None and new_error.startswith("INVALID_LEVELS"):
                return deepcopy(invalid_history)
        if found is None:
            found = {
                "id": str(uuid.uuid4()),
                "dedupe_key": key,
                "date": date_key,
                "symbol": symbol,
                "side": side,
                "market_type": _market_type(symbol),
                "execution_mode": mode,
                "status": ACTIVE,
                "first_seen_at": now,
                "last_seen_at": now,
                "expire_at": now + settings["retention_hours"] * 3600.0,
                "signal_count": 1,
                "detected_price": detected_price,
                "last_price": detected_price,
                "block_reason": str(block_reason or "BOT_OFF"),
                "market_mode": str(signal.get("market_mode") or context.get("market_mode") or "ANY"),
                "context": context,
                "order_setup": deepcopy(order_setup) if isinstance(order_setup, dict) else {},
            }
            _start_tracking(found, now)
            _observe_item(found, detected_price, now)
            items.append(found)
        else:
            found["last_seen_at"] = now
            found["expire_at"] = now + settings["retention_hours"] * 3600.0
            found["signal_count"] = int(found.get("signal_count", 0) or 0) + 1
            found["last_price"] = detected_price
            found["block_reason"] = str(block_reason or found.get("block_reason") or "BOT_OFF")
            found["market_mode"] = str(signal.get("market_mode") or context.get("market_mode") or found.get("market_mode") or "ANY")
            found["context"] = context
            if isinstance(order_setup, dict):
                current_setup = found.get("order_setup")
                current_version = int(_float((current_setup or {}).get("preview_version")))
                new_version = int(_float(order_setup.get("preview_version")))
                # Once a virtual trade starts, its entry/SL/TP must never move with price.
                # Before that point, allow a missing/legacy plan to be replaced by a valid one.
                if (
                    not isinstance(found.get("simulation"), dict)
                    and (
                        not _valid_setup(side, current_setup)
                        or new_version > current_version
                    )
                ):
                    found["order_setup"] = deepcopy(order_setup)
            _start_tracking(found, now)
            _observe_item(found, detected_price, now)
        _write(active_path(), items)
        return deepcopy(found)


def list_active(now: Optional[float] = None) -> List[Dict[str, Any]]:
    expire(now=now)
    with _LOCK:
        return deepcopy(_read(active_path()))


def get(opportunity_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        item = next((x for x in _read(active_path()) if str(x.get("id")) == str(opportunity_id)), None)
        return deepcopy(item) if item else None


def update_active(opportunity_id: str, **updates: Any) -> bool:
    """Bổ sung bản tính lệnh cho cache cũ mà không đổi trạng thái."""
    with _LOCK:
        rows = _read(active_path())
        changed = False
        for row in rows:
            if str(row.get("id")) == str(opportunity_id):
                safe_updates = deepcopy(updates)
                if isinstance(row.get("simulation"), dict):
                    safe_updates.pop("order_setup", None)
                row.update(safe_updates)
                if "order_setup" in safe_updates:
                    _start_tracking(row, time.time())
                changed = True
                break
        if changed:
            _write(active_path(), rows)
        return changed


def observe_price(symbol: str, price: float, now: Optional[float] = None) -> int:
    """Update virtual suggestions from an already available daemon price."""
    symbol = str(symbol or "").strip().upper()
    price = _float(price)
    if not symbol or price <= 0:
        return 0
    now = time.time() if now is None else float(now)
    changed = 0
    with _LOCK:
        rows = _read(active_path())
        for row in rows:
            if str(row.get("symbol") or "").upper() != symbol:
                continue
            if _observe_item(row, price, now):
                changed += 1
        if changed:
            _write(active_path(), rows)
    return changed


def finalize(opportunity_id: str, status: str, result: str = "", **updates: Any) -> Optional[Dict[str, Any]]:
    settings = load_settings()
    with _LOCK:
        items = _read(active_path())
        found = None
        kept = []
        for item in items:
            if str(item.get("id")) == str(opportunity_id):
                item.update(updates)
                item["status"] = str(status or "").upper()
                item["result"] = str(result or "")
                item["finalized_at"] = time.time()
                found = deepcopy(item)
            else:
                kept.append(item)
        if found:
            _write(active_path(), kept)
            _archive_unlocked(found, settings)
        return found


def dismiss(opportunity_id: str, result: str = "Người dùng xóa gợi ý") -> Optional[Dict[str, Any]]:
    return finalize(opportunity_id, DISMISSED, result)


def expire(now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else float(now)
    settings = load_settings()
    expired, kept = [], []
    with _LOCK:
        items = _read(active_path())
        for item in items:
            if float(item.get("expire_at", 0.0) or 0.0) <= now:
                item["status"] = EXPIRED
                item["result"] = "Gợi ý hết thời gian lưu"
                item["finalized_at"] = now
                expired.append(deepcopy(item))
            else:
                kept.append(item)
        if expired:
            _write(active_path(), kept)
            for item in expired:
                _archive_unlocked(item, settings)
    return expired


def list_history(include_active: bool = True) -> List[Dict[str, Any]]:
    with _LOCK:
        result = _read(history_path())
        if include_active:
            result.extend(_read(active_path()))
        return deepcopy(result)


def update_history(opportunity_id: str, **updates: Any) -> bool:
    with _LOCK:
        rows = _read(history_path())
        changed = False
        for row in rows:
            if str(row.get("id")) == str(opportunity_id):
                row.update(updates)
                changed = True
        if changed:
            _write(history_path(), rows)
        return changed
