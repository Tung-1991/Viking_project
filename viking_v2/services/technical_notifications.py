"""Session boundaries for technical notices; independent of trading decisions."""
from datetime import datetime, timedelta
import time

from ..trading.market import VN_TZ, exchange_close_minute, market_phase, normalize_exchange


ACTIVE_PHASES = {"ATO", "OPEN", "ATC"}
TECHNICAL_CATEGORIES = {"blocked_buy", "buy_lost"}


def notification_session(symbol, settings, status=None, *, exchange="", mode="", now=None):
    now = datetime.fromtimestamp(time.time(), VN_TZ) if now is None else now.astimezone(VN_TZ)
    status = status if isinstance(status, dict) else {}
    exchange = normalize_exchange((status.get("symbol_exchanges") or {}).get(symbol)
                                  or exchange or getattr(settings, "symbol_exchanges", {}).get(symbol))
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        minutes=exchange_close_minute(exchange)) if exchange else None
    dates = status.get("working_dates")
    unavailable = status.get("market_status") in {"CALENDAR_UNKNOWN", "CALENDAR_LOADING"}
    phase = ("CALENDAR_UNKNOWN" if unavailable or (mode == "REAL" and not dates) else
             market_phase(now, working_dates=dates, holidays=getattr(settings, "trading_holidays", []),
                          exchange=exchange)[0])
    return {"exchange": exchange, "day": now.date().isoformat(), "phase": phase,
            "active": phase in ACTIVE_PHASES, "ends_at": end.timestamp() if end else 0,
            "observed_at": now.isoformat(),
            "ended": bool(end and now >= end) or phase in {"WEEKEND", "HOLIDAY"}}


def technical_delivery(notice, settings, status=None, *, now=None):
    """Validate the original session, including notices retained by older builds."""
    now = datetime.fromtimestamp(time.time(), VN_TZ) if now is None else now.astimezone(VN_TZ)
    payload, session = notice.get("payload") or {}, notice.get("session") or {}
    # Older digests coalesced new values while retaining the first created_at.
    # Without a saved observation clock, that timestamp cannot date the values.
    if notice.get("batch_technical") and not payload.get("observed_at"):
        return None
    status = status if isinstance(status, dict) else {}
    exchange = normalize_exchange(session.get("exchange")
                                  or (status.get("symbol_exchanges") or {}).get(notice.get("symbol"))
                                  or getattr(settings, "symbol_exchanges", {}).get(notice.get("symbol")))
    try:
        original = payload.get("observed_at")
        at = datetime.fromisoformat(str(original)) if original else datetime.fromtimestamp(
            float(notice["created_at"]), VN_TZ)
        at = at.astimezone(VN_TZ) if at.tzinfo else at.replace(tzinfo=VN_TZ)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    if (not exchange or at.date() != now.date() or (at - now).total_seconds() > 5
            or market_phase(at, holidays=getattr(settings, "trading_holidays", []), exchange=exchange)[0]
            not in ACTIVE_PHASES):
        return None
    end = at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        minutes=exchange_close_minute(exchange))
    closed = now >= end
    return {"observed_at": at.isoformat(), "delayed": closed or (now - at).total_seconds() >= 60,
            "session_closed": closed}
