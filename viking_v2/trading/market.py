from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable
import time

from .. import config

if TYPE_CHECKING:
    from ..connections.dnse.client import DNSEClient
    from ..connections.dnse.websocket import DNSEMarketWS


VN_TZ = timezone(timedelta(hours=7))
STOCK_SETTLEMENT_RELEASE = datetime.min.time().replace(hour=13)
VALID_EXCHANGES = {"HOSE", "HNX", "UPCOM"}


def parse_clock_minute(value: str) -> int:
    parts = str(value).strip().split(":")
    if len(parts) != 2 or any(len(part) != 2 or not part.isdigit() for part in parts):
        raise ValueError("Giờ mua phải có dạng HH:MM, ví dụ 14:00.")
    hour, minute = map(int, parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Giờ mua không hợp lệ.")
    return hour * 60 + minute


def validate_buy_window(start: str, end: str) -> tuple[int, int]:
    left, right = parse_clock_minute(start), parse_clock_minute(end)
    if not 540 <= left < right <= 900:
        raise ValueError("Khung giờ mua phải nằm trong 09:00–15:00; TỪ phải trước ĐẾN.")
    return left, right


def in_buy_window(now: datetime, start: str, end: str) -> bool:
    left, right = validate_buy_window(start, end)
    local = now.astimezone(VN_TZ) if now.tzinfo else now.replace(tzinfo=VN_TZ)
    return left <= local.hour * 60 + local.minute < right

_EXCHANGE_ALIASES = {
    "HOSE": "HOSE", "HSX": "HOSE", "STO": "HOSE",
    "HNX": "HNX", "STX": "HNX",
    "UPCOM": "UPCOM", "UPX": "UPCOM",
}


def normalize_exchange(value: Any) -> str:
    """Return the canonical cash-equity exchange, or an empty string."""
    return _EXCHANGE_ALIASES.get(str(value or "").strip().upper(), "")


def exchange_sessions(exchange: str) -> tuple[tuple[int, int, str], ...]:
    """Trading windows as [start, end) minute offsets in Vietnam time."""
    market = normalize_exchange(exchange)
    if market == "HOSE":
        return ((540, 555, "ATO"), (555, 690, "OPEN"), (780, 870, "OPEN"), (870, 885, "ATC"))
    if market == "HNX":
        return ((540, 690, "OPEN"), (780, 870, "OPEN"), (870, 885, "ATC"))
    if market == "UPCOM":
        return ((540, 690, "OPEN"), (780, 900, "OPEN"))
    return ()


def exchange_open_minute(exchange: str) -> int:
    """First expected executable print used to assess replay completeness."""
    return 555 if normalize_exchange(exchange) == "HOSE" else 540


def exchange_close_minute(exchange: str) -> int:
    return 900 if normalize_exchange(exchange) == "UPCOM" else 885


def phase_at_minute(exchange: str, minute: int) -> str:
    for start, end, phase in exchange_sessions(exchange):
        if start <= int(minute) < end:
            return phase
    if 690 <= int(minute) < 780:
        return "LUNCH"
    return "CLOSED"


def active_trading_minutes(
    start: datetime,
    end: datetime,
    exchange: str,
    *,
    working_dates: Iterable[str] | None = None,
    holidays: Iterable[str] | None = None,
) -> float:
    """Elapsed exchange minutes, excluding lunch and closed trading days."""
    market = normalize_exchange(exchange)
    if not market or end <= start:
        return 0.0
    left = start.astimezone(VN_TZ) if start.tzinfo else start.replace(tzinfo=VN_TZ)
    right = end.astimezone(VN_TZ) if end.tzinfo else end.replace(tzinfo=VN_TZ)
    total = 0.0
    current = left.date()
    while current <= right.date():
        if is_working_day(current, working_dates, holidays):
            midnight = datetime.combine(current, datetime.min.time(), VN_TZ)
            for open_minute, close_minute, _phase in exchange_sessions(market):
                overlap_start = max(left, midnight + timedelta(minutes=open_minute))
                overlap_end = min(right, midnight + timedelta(minutes=close_minute))
                if overlap_end > overlap_start:
                    total += (overlap_end - overlap_start).total_seconds() / 60.0
        current += timedelta(days=1)
    return total


def stock_is_sellable_after_settlement(
    settle_date: str | date | datetime,
    at: datetime,
) -> bool:
    """Whether a cash-equity position may be sold under the T+2 timetable.

    VSDC members finish allocating settled stock before the afternoon session.
    Live trading still trusts the broker's ``tradeQuantity`` because it knows
    the actual allocation time.  PAPER and backtest have no broker allocation
    event, so 13:00 Vietnam time is their deterministic, fail-safe boundary.
    """
    settled = _as_date(settle_date)
    local = at.astimezone(VN_TZ) if at.tzinfo else at.replace(tzinfo=VN_TZ)
    if local.date() != settled:
        return local.date() > settled
    return local.time().replace(tzinfo=None) >= STOCK_SETTLEMENT_RELEASE

def market_now() -> datetime:
    return datetime.now(VN_TZ)

def market_phase(
    now: datetime | None = None,
    working_dates: Iterable[str] | None = None,
    holidays: Iterable[str] | None = None,
    exchange: str = "HOSE",
) -> tuple[str, str]:
    now = now.astimezone(VN_TZ) if now and now.tzinfo else (now.replace(tzinfo=VN_TZ) if now else market_now())
    if now.weekday() >= 5:
        return "WEEKEND", "NGHỈ T7/CN"
    if now.strftime("%Y-%m-%d") in {str(item)[:10] for item in holidays or []}:
        return "HOLIDAY", "NGHỈ ĐÃ THÊM"
    if working_dates is not None and now.strftime("%Y-%m-%d") not in {
        str(item)[:10] for item in working_dates
    }:
        return "HOLIDAY", "NGHỈ LỄ"
    market = normalize_exchange(exchange)
    if not market:
        return "UNKNOWN_EXCHANGE", "CHƯA XÁC ĐỊNH SÀN"
    minute = now.hour * 60 + now.minute
    if minute < 540:
        return "CLOSED", "CHƯA MỞ"
    phase = phase_at_minute(market, minute)
    if phase == "ATO":
        return "ATO", "ATO"
    if phase == "OPEN":
        return "OPEN", "MỞ"
    if phase == "LUNCH":
        return "LUNCH", "NGHỈ TRƯA"
    if phase == "ATC":
        return "ATC", "ATC"
    return "CLOSED", "ĐÓNG PHIÊN"


def market_session_clock(
    now: datetime | None = None,
    working_dates: Iterable[str] | None = None,
    holidays: Iterable[str] | None = None,
    exchange: str = "HOSE",
) -> tuple[str, bool]:
    """Return a compact phase, trading window and countdown without seconds."""
    now = now.astimezone(VN_TZ) if now and now.tzinfo else (
        now.replace(tzinfo=VN_TZ) if now else market_now()
    )
    market = normalize_exchange(exchange)
    phase, _ = market_phase(now, working_dates, holidays, market)
    def remaining(target: datetime) -> str:
        total_minutes = max(0, int((target - now).total_seconds() + 59) // 60)
        days, day_minutes = divmod(total_minutes, 24 * 60)
        if days:
            hours = day_minutes // 60
            return f"{days}N{hours:02d}G" if hours else f"{days}N"
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}G{minutes:02d}P" if hours else f"{minutes}P"

    def next_open() -> datetime | None:
        working_set = {
            str(value)[:10] for value in (working_dates or []) if str(value).strip()
        }
        holiday_set = {str(value)[:10] for value in (holidays or [])}

        def is_working_day(value: date) -> bool:
            key = value.isoformat()
            if value.weekday() >= 5 or key in holiday_set:
                return False
            return key in working_set if working_set else True

        if phase == "LUNCH":
            return now.replace(hour=13, minute=0, second=0, microsecond=0)
        if phase == "CLOSED" and now.hour < 9 and is_working_day(now.date()):
            return now.replace(hour=9, minute=0, second=0, microsecond=0)

        for offset in range(1, 371):
            candidate = now.date() + timedelta(days=offset)
            if is_working_day(candidate):
                return datetime.combine(candidate, datetime.min.time(), VN_TZ).replace(hour=9)
        return None

    def closed_label() -> str:
        target = next_open()
        if target is None:
            return "CLOSED · LỊCH CHƯA SẴN SÀNG"
        if target.date() == now.date():
            when = target.strftime("%H:%M")
        elif target.date() == now.date() + timedelta(days=1):
            when = f"MAI {target:%H:%M}"
        else:
            weekday = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")[target.weekday()]
            when = f"{weekday} {target:%H:%M}"
        return f"CLOSED · MỞ {when} · {remaining(target)}"

    if phase == "ATO":
        target = now.replace(hour=9, minute=15, second=0, microsecond=0)
        return f"ATO 09:00-09:15 · {remaining(target)}", True
    if phase == "OPEN":
        if now.hour < 12:
            target = now.replace(hour=11, minute=30, second=0, microsecond=0)
            opening = "09:15" if market == "HOSE" else "09:00"
            return f"LIVE {opening}-11:30 · {remaining(target)}", True
        close_hour, close_minute = ((15, 0) if market == "UPCOM" else (14, 30))
        target = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        return f"LIVE 13:00-{close_hour:02d}:{close_minute:02d} · {remaining(target)}", True
    if phase == "ATC":
        target = now.replace(hour=14, minute=45, second=0, microsecond=0)
        return f"ATC 14:30-14:45 · {remaining(target)}", True
    if phase == "LUNCH":
        return closed_label(), False
    if phase in {"WEEKEND", "HOLIDAY", "CLOSED"}:
        return closed_label(), False
    return "CLOSED", False

def due_phase(order_type: str) -> str:
    kind = str(order_type or "").upper()
    return kind if kind in {"ATO", "ATC"} else "OPEN"

def order_is_due(order_type: str, phase: str) -> bool:
    return due_phase(order_type) == str(phase or "").upper()


def merge_tick_into_daily_bars(
    rows: list[dict[str, Any]],
    tick: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Update today's 1D candle from WS without refetching full OHLC history."""
    values = [dict(row) for row in rows if isinstance(row, dict)]
    tick = tick if isinstance(tick, dict) else {}

    def board_price(raw: Any) -> float:
        try:
            value = max(0.0, float(raw or 0.0))
        except (TypeError, ValueError):
            return 0.0
        return value / 1000.0 if value >= 1000.0 else value

    price = board_price(
        tick.get("price")
        or tick.get("lastPrice")
        or tick.get("matchPrice")
        or tick.get("expected_price")
        or tick.get("expectedPrice")
    )
    if price <= 0:
        return values
    current = now.astimezone(VN_TZ) if now and now.tzinfo else (
        now.replace(tzinfo=VN_TZ) if now else datetime.now(VN_TZ)
    )
    today = current.date()
    latest = values[-1] if values else None
    latest_date = None
    if latest:
        try:
            latest_date = datetime.fromtimestamp(float(latest.get("time", 0.0)), VN_TZ).date()
        except (TypeError, ValueError, OSError, OverflowError):
            latest_date = None
    open_price = board_price(tick.get("open")) or price
    high_price = board_price(tick.get("high")) or price
    low_price = board_price(tick.get("low")) or price
    try:
        volume = max(0.0, float(tick.get("volume", 0.0) or 0.0))
    except (TypeError, ValueError):
        volume = 0.0
    if latest is not None and latest_date == today:
        latest["open"] = float(latest.get("open", 0.0) or open_price) or open_price
        latest["high"] = max(float(latest.get("high", price) or price), high_price, price)
        old_low = float(latest.get("low", price) or price)
        latest["low"] = min(old_low, low_price, price)
        latest["close"] = price
        latest["volume"] = max(float(latest.get("volume", 0.0) or 0.0), volume)
        latest["closed"] = False
    else:
        values.append(
            {
                "time": int(current.timestamp()),
                "open": open_price,
                "high": max(open_price, high_price, price),
                "low": min(open_price, low_price, price),
                "close": price,
                "volume": volume,
                "closed": False,
            }
        )
    return values[-260:]

def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()

def is_working_day(
    value: date,
    working_dates: Iterable[str] | None = None,
    holidays: Iterable[str] | None = None,
) -> bool:
    if value.weekday() >= 5:
        return False
    if value.isoformat() in {str(item)[:10] for item in holidays or []}:
        return False
    if working_dates is None:
        return True
    known = {str(item)[:10] for item in working_dates}
    return value.isoformat() in known

def previous_working_day(ex_date: str | date | datetime, working_dates: Iterable[str] | None = None) -> date:
    current = _as_date(ex_date) - timedelta(days=1)
    for _index in range(370):
        if is_working_day(current, working_dates):
            return current
        current -= timedelta(days=1)
    raise ValueError("Không tìm được phiên trước ngày chốt quyền")

def action_for_symbol(
    actions: Iterable[dict[str, Any]],
    symbol: str,
    *,
    today: str | date | datetime,
    working_dates: Iterable[str] | None = None,
) -> dict[str, Any]:
    target = str(symbol or "").upper()
    current = _as_date(today)
    for raw in actions or []:
        if str(raw.get("symbol", "") or "").upper() != target:
            continue
        try:
            ex_date = _as_date(str(raw.get("ex_date", "")))
            exit_date = previous_working_day(ex_date, working_dates)
        except (TypeError, ValueError):
            continue
        enabled = bool(raw.get("sell_enabled", False))
        active = bool(raw.get("enabled", True)) and current <= ex_date
        return {
            "symbol": target,
            "ex_date": ex_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "sell_enabled": enabled,
            "exit_due": enabled and current == exit_date,
            "warning_due": current >= exit_date and current <= ex_date,
            "active": active,
            "blocks_entry": active,
        }
    return {}

class MarketDataService:
    def __init__(self, client: DNSEClient, ws: DNSEMarketWS | None = None):
        self.client = client
        self.ws = ws or DNSEMarketWS(client.api_key, client.api_secret)
        self._rest_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._rest_retry_after: dict[str, float] = {}
        self._bars_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def start(self, symbols: Iterable[str]) -> None:
        self.ws.set_symbols(symbols)
        self.ws.start()

    def stop(self) -> None:
        self.ws.stop()

    def set_symbols(self, symbols: Iterable[str]) -> None:
        self.ws.set_symbols(symbols)

    @staticmethod
    def _normalize(symbol: str, trade: dict[str, Any] | None, quote: dict[str, Any] | None) -> dict[str, Any]:
        trade = trade or {}
        quote = quote or {}
        bids = quote.get("bid") or quote.get("bids") or []
        offers = quote.get("offer") or quote.get("offers") or []
        bid = float((bids[0] or {}).get("price", 0.0) or 0.0) if bids and isinstance(bids[0], dict) else 0.0
        ask = float((offers[0] or {}).get("price", 0.0) or 0.0) if offers and isinstance(offers[0], dict) else 0.0
        price = float(trade.get("matchPrice", trade.get("price", 0.0)) or 0.0)
        return {
            "symbol": symbol.upper(),
            "price": price,
            "bid": bid,
            "ask": ask,
            "reference": float(trade.get("referencePrice", trade.get("basicPrice", 0.0)) or 0.0),
            "high": float(trade.get("highestPrice", 0.0) or 0.0),
            "low": float(trade.get("lowestPrice", 0.0) or 0.0),
            "open": float(trade.get("openPrice", 0.0) or 0.0),
            "volume": int(trade.get("totalVolumeTraded", 0) or 0),
            "timestamp": time.time(),
            "source": "REST",
        }

    def get_tick(self, symbol: str, *, force_rest: bool = False) -> dict[str, Any] | None:
        symbol = str(symbol).upper()
        now = time.time()
        if not force_rest:
            ws_tick = self.ws.latest_tick(symbol)
            if ws_tick and now - float(ws_tick.get("timestamp", 0.0) or 0.0) <= 10.0:
                return ws_tick
        cached = self._rest_cache.get(symbol)
        if cached and now - cached[0] < config.REST_TICK_TTL_SECONDS:
            return dict(cached[1])
        if not force_rest and now < self._rest_retry_after.get(symbol, 0.0):
            return {**cached[1], "stale": True, "health": "REST_UNAVAILABLE"} if cached else None
        trade = self.client.get_latest_trade(symbol)
        quote = self.client.get_latest_quote(symbol)
        if not trade and not quote:
            self._rest_retry_after[symbol] = now + config.REST_TICK_TTL_SECONDS
            return {**cached[1], "stale": True, "health": "REST_UNAVAILABLE"} if cached else None
        result = self._normalize(symbol, trade, quote)
        self._rest_cache[symbol] = (now, result)
        self._rest_retry_after.pop(symbol, None)
        return result

    def health(self) -> dict[str, Any]:
        return {"websocket": self.ws.snapshot(), "rest": self.client.api_health()}

    @staticmethod
    def frozen_tick_from_bars(symbol: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Build a display-only last-price tick when the live session is closed."""
        bars = [row for row in rows if isinstance(row, dict)]
        if not bars:
            return None
        latest = bars[-1]
        try:
            price = float(latest.get("close", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        previous = bars[-2] if len(bars) >= 2 else {}
        def number(raw: Any, default: float) -> float:
            try:
                return float(raw if raw is not None and raw != "" else default)
            except (TypeError, ValueError):
                return default
        try:
            reference = float(previous.get("close", latest.get("open", price)) or price)
            timestamp = float(latest.get("time", 0.0) or 0.0)
        except (TypeError, ValueError):
            reference, timestamp = price, 0.0
        return {
            "symbol": str(symbol or "").upper(),
            "price": price,
            "bid": price,
            "ask": price,
            "reference": reference,
            "high": number(latest.get("high"), price),
            "low": number(latest.get("low"), price),
            "open": number(latest.get("open"), price),
            "volume": int(number(latest.get("volume"), 0.0)),
            "timestamp": timestamp,
            "source": "CLOSE",
            "frozen": True,
        }

    @staticmethod
    def _normalize_ohlc(
        data: dict[str, Any] | None, exchange: str = "HOSE",
    ) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        keys = ("t", "o", "h", "l", "c", "v")
        if any(not isinstance(data.get(key), list) for key in keys):
            return []
        length = len(data["t"])
        if length == 0 or any(len(data[key]) != length for key in keys):
            return []
        now = market_now()
        phase = market_phase(now, exchange=exchange)[0]
        today = now.date()
        close_minute = exchange_close_minute(exchange)
        today_is_closed = phase == "CLOSED" and now.hour * 60 + now.minute >= close_minute
        rows: list[dict[str, Any]] = []
        for index in range(length):
            try:
                timestamp = int(float(data["t"][index]))
                bar_date = datetime.fromtimestamp(timestamp, VN_TZ).date()
                rows.append(
                    {
                        "time": timestamp,
                        "open": float(data["o"][index]),
                        "high": float(data["h"][index]),
                        "low": float(data["l"][index]),
                        "close": float(data["c"][index]),
                        "volume": float(data["v"][index]),
                        # LUNCH and pre-open are not a completed daily candle.
                        # CLOSED mode must never consume a morning-only bar.
                        "closed": bar_date < today or (bar_date == today and today_is_closed),
                    }
                )
            except (TypeError, ValueError, OverflowError):
                continue
        return rows

    def get_daily_bars(
        self, symbol: str, *, count: int = 260, force: bool = False,
        exchange: str = "HOSE",
    ) -> list[dict[str, Any]]:
        symbol = str(symbol or "").upper()
        now = time.time()
        cached = self._bars_cache.get(symbol)
        phase = market_phase(exchange=exchange)[0]
        ttl = 30.0 if phase in {"ATO", "OPEN", "ATC"} else 1800.0
        if cached and not force and now - cached[0] < ttl:
            return [dict(row) for row in cached[1]]
        window = max(220, int(count or 260))
        data = self.client.get_ohlc(symbol, "1D", int(now - window * 86400 * 1.8), int(now))
        rows = self._normalize_ohlc(data, exchange)
        if rows:
            rows = rows[-window:]
            self._bars_cache[symbol] = (now, rows)
            return [dict(row) for row in rows]
        return [dict(row) for row in cached[1]] if cached else []
