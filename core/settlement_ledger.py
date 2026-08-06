# -*- coding: utf-8 -*-
"""Ledger T+2 cho vị thế CKCS REAL.

DNSE positions thật không luôn trả ngày cổ phiếu về, nên module này lưu dấu ngày mua
theo ticket/order/position để enrich lại `settle_date` khi đọc positions.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List

from core import settlement


LEDGER_FILE = "settlement_ledger.json"


def _account_dir(account: Any) -> str:
    account_key = str(account or "").strip()
    return os.path.join("data", account_key) if account_key else "data"


def ledger_path(account: Any) -> str:
    return os.path.join(_account_dir(account), LEDGER_FILE)


def _load(account: Any) -> Dict[str, Dict[str, Any]]:
    path = ledger_path(account)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(account: Any, data: Dict[str, Dict[str, Any]]) -> None:
    path = ledger_path(account)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp_path, path)


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    return text[:10]


def _pos_get(pos: Any, key: str, default: Any = None) -> Any:
    if isinstance(pos, dict):
        return pos.get(key, default)
    return getattr(pos, key, default)


def _pos_raw(pos: Any) -> Dict[str, Any]:
    raw = _pos_get(pos, "raw", {}) or {}
    return raw if isinstance(raw, dict) else {}


def _pos_keys(pos: Any) -> List[str]:
    raw = _pos_raw(pos)
    keys = []
    for value in (
        _pos_get(pos, "position_id"),
        _pos_get(pos, "ticket"),
        _pos_get(pos, "order_id"),
        raw.get("positionId"),
        raw.get("positionID"),
        raw.get("orderId"),
        raw.get("orderID"),
        raw.get("id"),
    ):
        text = str(value or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def _entry_aliases(key: str, entry: Dict[str, Any]) -> List[str]:
    aliases = [str(key)]
    for field in ("ticket", "position_id", "order_id"):
        text = str(entry.get(field) or "").strip()
        if text and text not in aliases:
            aliases.append(text)
    return aliases


def record_buy(account: Any, ticket: Any, symbol: str, volume: float, buy_date: Any, settle_date: Any) -> Dict[str, Any]:
    """Ghi ngày mua CKCS REAL vào ledger."""
    key = str(ticket or "").strip()
    if not key:
        return {}
    data = _load(account)
    entry = {
        "ticket": key,
        "position_id": key,
        "order_id": key,
        "symbol": str(symbol or "").strip().upper(),
        "volume": float(volume or 0.0),
        "buy_date": _date_text(buy_date),
        "settle_date": _date_text(settle_date),
    }
    data[key] = entry
    _save(account, data)
    return entry


def drop(account: Any, ticket: Any) -> None:
    key = str(ticket or "").strip()
    if not key:
        return
    data = _load(account)
    changed = False
    for ledger_key, entry in list(data.items()):
        if key in _entry_aliases(ledger_key, entry):
            data.pop(ledger_key, None)
            changed = True
    if changed:
        _save(account, data)


def _as_settlement_dict(pos: Any, settle_date: Any = None) -> Dict[str, Any]:
    raw = _pos_raw(pos)
    settle = settle_date if settle_date is not None else raw.get("settle_date")
    return {
        "symbol": str(_pos_get(pos, "symbol", "") or "").upper(),
        "type": int(_pos_get(pos, "type", 0) or 0),
        "volume": float(_pos_get(pos, "volume", 0.0) or 0.0),
        "settle_date": settle or "",
    }


def enrich_positions(account: Any, positions: Iterable[Any]) -> List[Dict[str, Any]]:
    """Enrich positions bằng settle_date trong ledger và prune entry không còn vị thế.

    Trả về list dict chuẩn cho `settlement.available_to_sell/pending_to_settle`.
    Nếu `positions` là object có `raw`, hàm cũng mutate `raw["settle_date"]` để UI dùng lại.
    """
    positions = list(positions or [])
    data = _load(account)
    live_keys = set()
    enriched: List[Dict[str, Any]] = []

    for pos in positions:
        pos_keys = _pos_keys(pos)
        live_keys.update(pos_keys)
        match_key = None
        match_entry = None
        for ledger_key, entry in data.items():
            if any(key in _entry_aliases(ledger_key, entry) for key in pos_keys):
                match_key = ledger_key
                match_entry = entry
                break

        settle = None
        if match_entry and settlement.is_cash_stock(_pos_get(pos, "symbol")) and int(_pos_get(pos, "type", 0) or 0) == 0:
            settle = match_entry.get("settle_date")
            raw = _pos_raw(pos)
            raw["settle_date"] = settle
            raw["buy_date"] = match_entry.get("buy_date", "")
            if not isinstance(pos, dict):
                try:
                    pos.raw = raw
                except Exception:
                    pass
            else:
                pos["raw"] = raw
            # Bổ sung alias mới để lần sau match ổn định hơn.
            changed = False
            for field, value in (("position_id", _pos_get(pos, "position_id")), ("order_id", _pos_get(pos, "order_id")), ("ticket", _pos_get(pos, "ticket"))):
                text = str(value or "").strip()
                if text and match_entry.get(field) != text:
                    match_entry[field] = text
                    changed = True
            if changed and match_key:
                data[match_key] = match_entry

        enriched.append(_as_settlement_dict(pos, settle))

    pruned = {
        key: entry
        for key, entry in data.items()
        if any(alias in live_keys for alias in _entry_aliases(key, entry))
    }
    if pruned != data:
        _save(account, pruned)
    elif pruned:
        _save(account, pruned)
    return enriched
