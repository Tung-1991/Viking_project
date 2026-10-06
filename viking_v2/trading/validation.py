from __future__ import annotations

from datetime import datetime
import math
import time
from typing import Any

from .. import config


MAX_QUOTE_AGE = max(20.0, float(config.REST_TICK_TTL_SECONDS) + 5.0)
MAX_DECISION_AGE = max(20.0, float(config.HEARTBEAT_SECONDS) * 4.0)


def timestamp_value(value: Any) -> float:
    try:
        if isinstance(value, str) and not value.replace('.', '', 1).isdigit():
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        return float(value or 0.0)
    except (ValueError, TypeError, OverflowError):
        return 0.0


def quote_is_fresh(quote: Any, symbol: str, *, now: float | None = None, require_timestamp: bool = True) -> bool:
    if not isinstance(quote, dict) or quote.get('stale') or quote.get('frozen'):
        return False
    if str(quote.get('symbol', symbol)).upper() != str(symbol).upper():
        return False
    if str(quote.get('health', 'OK')).upper() not in {'', 'OK', 'HEALTHY'}:
        return False
    observed = timestamp_value(quote.get('received_at', quote.get('timestamp')))
    checked = time.time() if now is None else now
    if not observed:
        return not require_timestamp
    return math.isfinite(observed) and -2 <= checked - observed <= MAX_QUOTE_AGE


def decision_is_fresh(raw: Any, symbol: str, mode: str, *, now: float | None = None) -> bool:
    if hasattr(raw, 'to_dict'):
        raw = raw.to_dict()
    if not isinstance(raw, dict) or str(raw.get('symbol', symbol)).upper() != str(symbol).upper():
        return False
    details = raw.get('details') or {}
    if str(details.get('execution_mode', mode)).upper() != str(mode).upper():
        return False
    observed = timestamp_value(details.get('updated_at', raw.get('timestamp')))
    checked = time.time() if now is None else now
    return bool(observed and math.isfinite(observed) and -2 <= checked - observed <= MAX_DECISION_AGE)
