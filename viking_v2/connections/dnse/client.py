from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
import os
import random
import threading
import time
import math
from typing import Any, Callable

import requests

from ... import config
from ...models import BrokerOrderResult, OrderIntent
from ...trading.market import VN_TZ
from ...trading.portfolio import available_to_sell, dnse_price, price_in_band, validate_quantity
from .signing import generate_signature_header
from .snapshot_health import FAST_POLL_SECONDS, IDLE_POLL_SECONDS, safe_detail


logger = logging.getLogger("VIKING_V2.dnse")
SECDEF_TTL_SECONDS = 60.0
SECDEF_MAX_AGE_SECONDS = 300.0
SECDEF_RETRY_SECONDS = 30.0
SECDEF_RATE_LIMIT_SECONDS = 60.0


class BrokerSnapshotError(RuntimeError):
    """An unavailable/incomplete account snapshot is not an empty account."""

    def __init__(self, message: str, *, endpoint: str = "", status: int = 0, detail: str = ""):
        self.message = message
        self.endpoint, self.status, self.detail = endpoint, status, detail
        parts = [message]
        if endpoint:
            parts.extend((endpoint, f"HTTP {status}" if status else "MẠNG/TIMEOUT"))
        if detail:
            parts.append(detail)
        super().__init__(" · ".join(parts))


def _unwrap(data: Any, keys: tuple[str, ...] = ()) -> Any:
    value = data
    if isinstance(value, dict) and isinstance(value.get("data"), (dict, list)):
        value = value["data"]
    if keys and isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
    return value


def _first(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _account_rows(data: Any, key: str) -> list[dict[str, Any]]:
    body = _unwrap(data)
    rows = _unwrap(data, (key,))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BrokerSnapshotError(f"Invalid DNSE {key} response")
    if isinstance(body, dict) and int(body.get("total", len(rows)) or 0) > len(rows):
        raise BrokerSnapshotError(f"Incomplete DNSE {key} snapshot")
    return [{**row, "price_unit": "VND"} for row in rows]


class DNSEClient:
    """Small STOCK-only DNSE adapter; no strategy or UI settings live here."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        account_no: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.api_key = str(api_key if api_key is not None else os.getenv("DNSE_API_KEY", "")).strip()
        self.api_secret = str(api_secret if api_secret is not None else os.getenv("DNSE_API_SECRET", "")).strip()
        self.account_no = str(
            account_no
            if account_no is not None
            else (os.getenv("DNSE_STOCK_ACCOUNT_NO", "") or os.getenv("DNSE_ACCOUNT_NO", ""))
        ).strip()
        self.base_url = str(base_url or config.API_BASE_URL).rstrip("/")
        self.version = str(os.getenv("DNSE_API_VERSION", config.API_VERSION))
        self.otp_type = str(os.getenv("DNSE_OTP_TYPE", "email_otp"))
        self.session = session or requests.Session()
        self._now = now
        self.connected = False
        self.trading_token = str(os.getenv("DNSE_TRADING_TOKEN", "") or "").strip()
        try:
            self.trading_token_expires_at = float(
                os.getenv("DNSE_TRADING_TOKEN_EXPIRES_AT", "0") or 0.0
            )
        except (TypeError, ValueError):
            self.trading_token_expires_at = 0.0
        if self.trading_token_expires_at <= self._now():
            self.trading_token = ""
            self.trading_token_expires_at = 0.0
        self._endpoint_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._account_poll_seconds = FAST_POLL_SECONDS
        self._snapshot_errors: dict[str, dict[str, Any]] = {}
        self._snapshot_locks: dict[str, threading.RLock] = {}
        self._secdef_lock = threading.Lock()
        self._secdef_retry_after: dict[str, float] = {}
        self._secdef_rate_limit_until = 0.0
        self._health: dict[str, Any] = {
            "started_at": self._now(),
            "total_requests": 0,
            "last_endpoint": "",
            "last_status": None,
            "last_error": "",
            "last_latency_ms": 0.0,
            "by_endpoint": {},
            "rate_limits": {},
        }

    def connect(self) -> bool:
        self.connected = bool(self.api_key and self.api_secret and self.account_no)
        return self.connected

    def close(self) -> None:
        self.connected = False
        self.session.close()

    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.account_no)

    def has_trading_token(self) -> bool:
        return bool(self.trading_token and self._now() < self.trading_token_expires_at)

    def trading_token_seconds_left(self) -> float:
        return max(0.0, self.trading_token_expires_at - self._now()) if self.trading_token else 0.0

    def _headers(self, method: str, path: str, require_token: bool = False) -> dict[str, str]:
        signature, date_value = generate_signature_header(
            self.api_key, self.api_secret, method, path
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Signature": signature,
            "X-Aux-Date": date_value,
            "version": self.version,
        }
        if require_token:
            if not self.has_trading_token():
                raise RuntimeError("TRADING_TOKEN_REQUIRED")
            headers["trading-token"] = self.trading_token
        return headers

    @staticmethod
    def _family(method: str, path: str) -> str:
        pieces = [piece for piece in path.split("/") if piece]
        normalized = ["*" if piece.isdigit() or len(piece) > 20 else piece for piece in pieces]
        if len(normalized) > 1 and normalized[0] == "accounts":
            normalized[1] = "*"
        return f"{method.upper()} /{'/'.join(normalized)}"

    def _record(self, method: str, path: str, status: int, latency_ms: float, error: str = "", headers: Any = None) -> None:
        family = self._family(method, path)
        with self._lock:
            self._health["total_requests"] += 1
            self._health["last_endpoint"] = family
            self._health["last_status"] = status
            self._health["last_error"] = safe_detail(
                error, secrets=(self.api_key, self.api_secret, self.trading_token),
            )
            self._health["last_latency_ms"] = round(float(latency_ms or 0.0), 2)
            row = self._health["by_endpoint"].setdefault(family, {"requests": 0, "errors": 0})
            row["requests"] += 1
            if not (200 <= status < 300):
                row["errors"] += 1
            if headers is not None:
                self._health["rate_limits"][family] = {
                    "limit": headers.get("X-RateLimit-Limit"),
                    "remaining": headers.get("X-RateLimit-Remaining"),
                    "reset": headers.get("X-RateLimit-Reset"),
                }

    def api_health(self) -> dict[str, Any]:
        with self._lock:
            health = deepcopy(self._health)
            health["account_poll_seconds"] = self._account_poll_seconds
            health["snapshot_errors"] = {
                key: {field: value for field, value in row.items() if field != "exception"}
                for key, row in self._snapshot_errors.items()
            }
            return health

    def set_account_poll_interval(self, seconds: float) -> None:
        with self._lock:
            self._account_poll_seconds = (
                IDLE_POLL_SECONDS if seconds >= IDLE_POLL_SECONDS else FAST_POLL_SECONDS
            )

    def _snapshot_error(self, message: str, path: str, status: int, detail: str = "") -> BrokerSnapshotError:
        return BrokerSnapshotError(
            message, endpoint=self._family("GET", path), status=status,
            detail=safe_detail(detail, secrets=(self.api_key, self.api_secret, self.trading_token)),
        )

    def _cached_snapshot(self, key: str, loader: Callable[[], Any], force: bool) -> Any:
        """Idle backoff never returns an old balance in place of a failed read.

        Forced preflight reads bypass it. Going live or adding an unresolved
        order immediately shortens the retry interval again.
        """
        with self._lock:
            lock = self._snapshot_locks.setdefault(key, threading.RLock())
        with lock:
            with self._lock:
                failure = self._snapshot_errors.get(key)
                interval = self._account_poll_seconds
            now = self._now()
            if failure and not force and now < failure["last_error_at"] + interval:
                # Fresh exception avoids retaining another traceback per idle loop.
                previous = failure["exception"]
                raise BrokerSnapshotError(previous.message, endpoint=previous.endpoint,
                                          status=previous.status, detail=previous.detail)
            try:
                value = self._cached(key, interval, loader, force or bool(failure))
            except BrokerSnapshotError as exc:
                failed_at = self._now()
                with self._lock:
                    self._snapshot_errors[key] = {
                        "exception": exc, "endpoint": exc.endpoint, "status": exc.status,
                        "detail": exc.detail, "error": str(exc),
                        "first_error_at": failure["first_error_at"] if failure else failed_at,
                        "last_error_at": failed_at,
                        "last_success_at": self._cache.get(key, (0.0, None))[0],
                        "retry_at": failed_at + interval,
                    }
                raise
            with self._lock:
                self._snapshot_errors.pop(key, None)
            return value

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        require_token: bool = False,
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
    ) -> tuple[bool, Any, int, str]:
        if not self.connected and not self.connect():
            return False, None, 0, "DNSE_NOT_CONFIGURED"
        family = self._family(method, path)
        with self._lock:
            endpoint_lock = self._endpoint_locks.setdefault(family, threading.Lock())
        with endpoint_lock:
            retries = config.HTTP_RETRIES if method.upper() in {"GET", "HEAD"} else 0
            for attempt in range(retries + 1):
                try:
                    headers = self._headers(method, path, require_token)
                except RuntimeError as exc:
                    return False, None, 401, str(exc)
                started = time.perf_counter()
                try:
                    response = self.session.request(
                        method.upper(),
                        f"{self.base_url}{path}",
                        params=params,
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                    )
                    latency = (time.perf_counter() - started) * 1000.0
                    try:
                        data = response.json()
                    except ValueError:
                        data = {"text": response.text}
                    message = str(data.get("message", "") if isinstance(data, dict) else "")
                    self._record(
                        method, path, response.status_code, latency,
                        "" if 200 <= response.status_code < 300 else message or response.text,
                        response.headers,
                    )
                    if 200 <= response.status_code < 300:
                        return True, data, response.status_code, ""
                    if response.status_code == 401 and "token" in message.lower():
                        self.trading_token = ""
                        self.trading_token_expires_at = 0.0
                    if response.status_code == 429 and attempt < retries:
                        try:
                            delay = float(response.headers.get("Retry-After", "") or 0.0)
                        except (TypeError, ValueError):
                            delay = 0.0
                        time.sleep(min(5.0, delay or (2**attempt + random.random() / 4)))
                        continue
                    return False, data, response.status_code, message or response.text
                except Exception as exc:
                    latency = (time.perf_counter() - started) * 1000.0
                    self._record(method, path, 0, latency, str(exc))
                    if attempt >= retries:
                        return False, None, 0, str(exc)
            return False, None, 0, "REQUEST_FAILED"

    def _cached(self, key: str, ttl: float, loader: Callable[[], Any], force: bool = False) -> Any:
        now = self._now()
        if not force and key in self._cache and now - self._cache[key][0] < ttl:
            return deepcopy(self._cache[key][1])
        value = loader()
        if value is not None:
            self._cache[key] = (now, deepcopy(value))
        return value

    def get_accounts(self) -> dict[str, Any] | None:
        ok, data, _status, _message = self._request("GET", "/accounts")
        return data if ok and isinstance(data, dict) else None

    def get_balance(self, *, force: bool = False) -> dict[str, Any] | None:
        def load() -> dict[str, Any] | None:
            path = f"/accounts/{self.account_no}/balances"
            ok, data, status, message = self._request("GET", path)
            if not ok:
                raise self._snapshot_error("DNSE balance unavailable", path, status, message)
            body = _unwrap(data)
            if not isinstance(body, dict) or not isinstance(body.get("stock"), dict):
                raise self._snapshot_error("Invalid DNSE STOCK balance response", path, status, "INVALID_RESPONSE")
            return body

        return self._cached_snapshot("balance", load, force)

    def get_positions(self, *, force: bool = False) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]] | None:
            path = f"/accounts/{self.account_no}/positions"
            ok, data, status, message = self._request(
                "GET", path, params={"marketType": "STOCK", "pageSize": 1000}
            )
            if not ok:
                raise self._snapshot_error("DNSE positions unavailable", path, status, message)
            try:
                return _account_rows(data, "positions")
            except (BrokerSnapshotError, ValueError, TypeError) as exc:
                raise self._snapshot_error("Invalid/incomplete DNSE positions response", path, status, "INVALID_RESPONSE") from exc

        return self._cached_snapshot("positions", load, force) or []

    def get_stock_loan_packages(self, symbol: str, *, force: bool = False) -> list[dict[str, Any]]:
        """Return DNSE STOCK packages for ``symbol``.

        The cash package contains the account's actual broker buy/sell fee rates.
        Keeping this in the DNSE adapter avoids inventing a fee percentage in UI.
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return []

        def load() -> list[dict[str, Any]] | None:
            path = f"/accounts/{self.account_no}/loan-packages"
            ok, data, status, message = self._request(
                "GET",
                path,
                params={"marketType": "STOCK", "symbol": symbol},
            )
            if not ok:
                raise self._snapshot_error("DNSE loan packages unavailable", path, status, message)
            try:
                return _account_rows(data, "loanPackages")
            except (BrokerSnapshotError, ValueError, TypeError) as exc:
                raise self._snapshot_error("Invalid/incomplete DNSE loan packages response", path, status, "INVALID_RESPONSE") from exc

        return self._cached(f"stock_packages:{symbol}", 300.0, load, force) or []

    def get_stock_fee_rate(
        self,
        symbol: str,
        *,
        side: str = "BUY",
        force: bool = False,
    ) -> float | None:
        """Read the cash-account STOCK fee rate reported by DNSE."""
        packages = self.get_stock_loan_packages(symbol, force=force)
        if not packages:
            return None
        key = "brokerFirmSellingFeeRate" if str(side or "BUY").upper() == "SELL" else "brokerFirmBuyingFeeRate"
        rates: list[float] = []
        for package in packages:
            if key not in package:
                continue
            try:
                rates.append(max(0.0, float(package.get(key) or 0.0)))
            except (TypeError, ValueError):
                continue
        # Preserve the proven legacy behaviour: DNSE may return a promotional
        # zero-fee package beside the package actually usable by the order.
        # The highest reported rate is the safe preview until DNSE returns the
        # final fee on the order/execution itself.
        return max(rates) if rates else None

    def cash_package(self, symbol: str) -> dict[str, Any]:
        configured = str(os.getenv("DNSE_STOCK_CASH_PACKAGE_ID", "") or "")
        packages = self.get_stock_loan_packages(symbol)
        candidates = [row for row in packages if (
            (configured and str(row.get("id")) == configured)
            or (not configured and (
                row.get("isCash") is True
                or str(row.get("type", "")).upper() == "CASH"
                or float(row.get("initialRate", 0) or 0) == 1.0
            ))
        )]
        if len(candidates) != 1 or not candidates[0].get("id"):
            raise BrokerSnapshotError("Không xác định duy nhất gói giao dịch tiền mặt DNSE")
        selected = candidates[0]
        if selected.get("initialRate") is not None and float(selected["initialRate"]) != 1.0:
            raise BrokerSnapshotError("Gói cấu hình không phải tiền mặt 100%")
        return selected

    def get_buying_power(self, symbol: str, loan_package_id: str, price: float) -> dict[str, Any]:
        path = f"/accounts/{self.account_no}/ppse"
        ok, data, status, message = self._request(
            "GET", path,
            params={"marketType": "STOCK", "symbol": symbol, "loanPackageId": loan_package_id, "price": dnse_price(price)},
        )
        body = _unwrap(data)
        if not ok:
            raise self._snapshot_error("DNSE buying power unavailable", path, status, message)
        if not isinstance(body, dict) or "qmaxBuy" not in body:
            raise self._snapshot_error("Invalid/incomplete DNSE buying power response", path, status, "INVALID_RESPONSE")
        return body

    def get_orders(self, *, force: bool = False) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]] | None:
            path = f"/accounts/{self.account_no}/orders"
            ok, data, status, message = self._request(
                "GET",
                path,
                params={"marketType": "STOCK", "orderCategory": "NORMAL", "pageSize": 1000},
            )
            if not ok:
                raise self._snapshot_error("DNSE orders unavailable", path, status, message)
            try:
                return _account_rows(data, "orders")
            except (BrokerSnapshotError, ValueError, TypeError) as exc:
                raise self._snapshot_error("Invalid/incomplete DNSE orders response", path, status, "INVALID_RESPONSE") from exc

        return self._cached_snapshot("orders", load, force) or []

    def get_order_history(self, from_date: str, to_date: str) -> list[dict[str, Any]]:
        path = f"/accounts/{self.account_no}/orders/history"
        ok, data, status, message = self._request(
            "GET",
            path,
            params={"marketType": "STOCK", "from": str(from_date), "to": str(to_date), "pageSize": 1000},
        )
        if not ok:
            raise self._snapshot_error("DNSE order history unavailable", path, status, message)
        try:
            return _account_rows(data, "orders")
        except (BrokerSnapshotError, ValueError, TypeError) as exc:
            raise self._snapshot_error("Invalid/incomplete DNSE order history response", path, status, "INVALID_RESPONSE") from exc

    def get_order_detail(self, order_id: str, *, force: bool = False) -> dict[str, Any] | None:
        def load():
            ok, data, _status, _message = self._request(
                "GET", f"/accounts/{self.account_no}/orders/{order_id}",
                params={"marketType": "STOCK", "orderCategory": "NORMAL"},
            )
            value = _unwrap(data)
            return {**value, "price_unit": "VND"} if ok and isinstance(value, dict) else None
        return self._cached(f"order_detail:{order_id}", config.ORDERS_TTL_SECONDS, load, force=force)

    def get_executions(self, order_id: str) -> dict[str, Any] | list[Any] | None:
        ok, data, _status, _message = self._request(
            "GET",
            f"/accounts/{self.account_no}/executions/{order_id}",
            params={"marketType": "STOCK", "orderCategory": "NORMAL"},
        )
        return _unwrap(data) if ok else None

    def get_secdef(self, symbol: str) -> dict[str, Any] | None:
        """Share reference data across loops/books, without retrying every tick.

        Refresh once a minute. On transient failure, retain a successful response
        for at most five minutes and only within the same VN calendar day.
        Neither quotes nor trading signals/orders are cached here.
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return None
        # Single-flight covers both strategy books and parallel UI consumers.
        with self._secdef_lock:
            now = self._now()
            key = f"secdef:{symbol}"
            cached = self._cache.get(key)
            age = now - cached[0] if cached else float("inf")
            same_day = bool(cached and 0 <= age and (
                datetime.fromtimestamp(cached[0], VN_TZ).date()
                == datetime.fromtimestamp(now, VN_TZ).date()
            ))
            if same_day and age < SECDEF_TTL_SECONDS:
                return deepcopy(cached[1])
            recent = cached[1] if same_day and age <= SECDEF_MAX_AGE_SECONDS else None
            if now < max(self._secdef_retry_after.get(symbol, 0.0),
                         self._secdef_rate_limit_until):
                return deepcopy(recent)

            ok, data, status, _message = self._request("GET", f"/price/{symbol}/secdef")
            value = _unwrap(data)
            if isinstance(value, list):
                # Prefer the normal round-lot board when all boards are returned.
                value = next(
                    (row for row in value if isinstance(row, dict) and row.get("boardId") == "G1"),
                    next((row for row in value if isinstance(row, dict)), None),
                )
            completed = self._now()
            if ok and isinstance(value, dict) and value:
                self._cache[key] = (completed, deepcopy(value))
                self._secdef_retry_after.pop(symbol, None)
                return value

            self._secdef_retry_after[symbol] = completed + SECDEF_RETRY_SECONDS
            if status == 429:
                # One throttled symbol pauses this endpoint for the whole list.
                self._secdef_rate_limit_until = completed + SECDEF_RATE_LIMIT_SECONDS
            transient = status == 0 or status == 429 or status >= 500 or ok
            # Account/auth failures are not concealed by a successful old reply.
            if not transient:
                self._cache.pop(key, None)
            if transient and cached and 0 <= completed - cached[0] <= SECDEF_MAX_AGE_SECONDS and (
                datetime.fromtimestamp(cached[0], VN_TZ).date()
                == datetime.fromtimestamp(completed, VN_TZ).date()
            ):
                return deepcopy(cached[1])
            return None

    def get_latest_trade(self, symbol: str) -> dict[str, Any] | None:
        ok, data, _status, _message = self._request(
            "GET", f"/price/{symbol.upper()}/trades/latest", params={"boardId": "G1"}
        )
        value = _unwrap(data, ("trades",))
        if isinstance(value, list):
            value = value[0] if value else None
        return value if ok and isinstance(value, dict) else None

    def get_latest_quote(self, symbol: str) -> dict[str, Any] | None:
        ok, data, _status, _message = self._request("GET", f"/price/{symbol.upper()}/quotes/latest")
        value = _unwrap(data, ("quotes",))
        if isinstance(value, list):
            value = value[0] if value else None
        return value if ok and isinstance(value, dict) else None

    def get_ohlc(self, symbol: str, resolution: str, from_ts: int, to_ts: int) -> dict[str, Any] | None:
        symbol = str(symbol or "").upper()
        market_type = "INDEX" if symbol in {
            "VNINDEX", "VN30", "HNX", "HNX30", "UPCOM", "VNXALLSHARE", "VN100"
        } else "STOCK"
        ok, data, _status, _message = self._request(
            "GET",
            "/price/ohlc",
            params={
                "symbol": symbol,
                "type": market_type,
                "resolution": str(resolution),
                "from": str(int(from_ts)),
                "to": str(int(to_ts)),
            },
        )
        return data if ok and isinstance(data, dict) else None

    def get_working_dates(self, *, force: bool = False) -> list[str]:
        def load() -> list[str] | None:
            ok, data, _status, _message = self._request("GET", "/market/working-dates")
            if not ok:
                return None
            value = _unwrap(data, ("workingDates", "dates"))
            return [str(item)[:10] for item in value] if isinstance(value, list) else []

        return self._cached("working_dates", config.WORKING_DATES_TTL_SECONDS, load, force) or []

    def send_email_otp(self) -> BrokerOrderResult:
        ok, data, status, message = self._request("POST", "/registration/send-email-otp")
        return BrokerOrderResult(ok, "OTP_SENT" if ok else "FAILED", message=message, status_code=status, raw=data or {})

    def verify_otp(self, passcode: str, otp_type: str | None = None) -> BrokerOrderResult:
        ok, data, status, message = self._request(
            "POST",
            "/registration/trading-token",
            payload={"otpType": otp_type or self.otp_type, "passcode": str(passcode or "")},
        )
        value = _unwrap(data)
        token = ""
        if isinstance(value, dict):
            token = str(_first(value, "trading-token", "tradingToken", "token", default="") or "")
        if ok and token:
            self.trading_token = token
            self.trading_token_expires_at = self._now() + 8 * 60 * 60
            return BrokerOrderResult(True, "TOKEN_READY", message="Trading token ready", status_code=status, raw=data or {})
        return BrokerOrderResult(False, "FAILED", message=message or "Trading token missing", error=message, status_code=status, raw=data or {})

    @staticmethod
    def _result(ok: bool, data: Any, status: int, message: str) -> BrokerOrderResult:
        value = _unwrap(data)
        value = value if isinstance(value, dict) else {}
        order_id = str(_first(value, "orderId", "id", "orderID", default="") or "")
        order_status = str(_first(value, "orderStatus", "status", default="") or "")
        return BrokerOrderResult(
            ok=ok,
            status=order_status,
            order_id=order_id,
            message=str(_first(value, "message", "description", default=message) or message),
            error="" if ok else str(message or _first(value, "message", default="ORDER_FAILED")),
            status_code=status,
            raw={**value, "price_unit": "VND"},
        )

    def place_order(self, intent: OrderIntent) -> BrokerOrderResult:
        valid, reason, quantity = validate_quantity(intent.quantity)
        if not valid:
            return BrokerOrderResult(False, "REJECTED", message=reason, error="INVALID_QUANTITY")
        if not self.has_trading_token():
            return BrokerOrderResult(
                False,
                "WAITING_TOKEN",
                message="Trading token required",
                error="TRADING_TOKEN_REQUIRED",
            )
        try:
            package = self.cash_package(intent.symbol)
            package_id = str(package["id"])
            if intent.loan_package_id and intent.loan_package_id != package_id:
                return BrokerOrderResult(False, "REJECTED", error="CASH_PACKAGE_MISMATCH")
            intent.loan_package_id = package_id
        except (BrokerSnapshotError, ValueError, TypeError) as exc:
            return BrokerOrderResult(False, "REJECTED", message=str(exc), error="CASH_PACKAGE_UNAVAILABLE")
        if intent.side == "SELL":
            try:
                positions = self.get_positions(force=True)
            except BrokerSnapshotError as exc:
                return BrokerOrderResult(False, "REJECTED", message=str(exc), error="ACCOUNT_SNAPSHOT_UNAVAILABLE")
            positions = [row for row in positions if str(row.get("loanPackageId", "")) == package_id]
            available = available_to_sell(positions, intent.symbol)
            if available < quantity:
                return BrokerOrderResult(
                    False,
                    "REJECTED",
                    message=f"Chỉ có {available} CP {intent.symbol} bán được; yêu cầu {quantity}.",
                    error="INSUFFICIENT_SELLABLE",
                )
        secdef: dict[str, Any] = {}
        if intent.order_type in {"LO", "MARKET"}:
            secdef = self.get_secdef(intent.symbol) or {}
        api_limit_price = dnse_price(intent.limit_price) if intent.order_type == "LO" else 0.0
        if intent.order_type == "LO":
            floor_price = dnse_price(secdef.get("floorPrice", 0.0))
            ceiling_price = dnse_price(secdef.get("ceilingPrice", 0.0))
            if not math.isfinite(api_limit_price) or not price_in_band(api_limit_price, floor_price, ceiling_price):
                return BrokerOrderResult(
                    False,
                    "REJECTED",
                    message=f"Giá LO ngoài biên [{floor_price:g} .. {ceiling_price:g}].",
                    error="PRICE_OUT_OF_BAND",
                )
        order_type = intent.order_type
        if intent.order_type == "MARKET":
            market_id = str(
                secdef.get("marketId", secdef.get("market", secdef.get("exchange", ""))) or ""
            ).upper()
            if market_id in {"STO", "HOSE", "HSX"}:
                order_type = "MTL"
            elif market_id in {"STX", "HNX"}:
                order_type = "MTL"
            elif market_id in {"UPX", "UPCOM"}:
                return BrokerOrderResult(
                    False,
                    "REJECTED",
                    message="UPCOM không hỗ trợ lệnh thị trường; hãy dùng LO.",
                    error="UNSUPPORTED_MARKET_ORDER",
                )
            else:
                return BrokerOrderResult(
                    False,
                    "REJECTED",
                    message="Không xác định được sàn để chọn loại lệnh thị trường.",
                    error="UNKNOWN_EXCHANGE",
                )
        request_tag = intent.request_tag or f"V2:{intent.id[:8].upper()}:{intent.attempt}"
        intent.request_tag = request_tag
        payload = {
            "accountNo": self.account_no,
            "symbol": intent.symbol,
            "side": "NB" if intent.side == "BUY" else "NS",
            "quantity": quantity,
            "orderType": order_type,
            "price": float(api_limit_price),
            "remark": request_tag,
            "loanPackageId": int(package_id),
        }
        ok, data, status, message = self._request(
            "POST",
            "/accounts/orders",
            params={"marketType": "STOCK", "orderCategory": "NORMAL"},
            payload=payload,
            require_token=True,
        )
        result = self._result(ok, data, status, message)
        if not ok and (status == 0 or status >= 500):
            try:
                orders = self.get_orders(force=True)
            except BrokerSnapshotError:
                orders = []
            for order in orders:
                if request_tag == str(order.get("remark", "") or ""):
                    return self._result(True, order, 200, "Reconciled after transport error")
            result.status = "UNKNOWN"
            result.error = "ORDER_STATUS_UNKNOWN"
            result.message = f"Không xác định trạng thái lệnh {request_tag}; không tự gửi lại."
        if result.ok:
            self._cache.pop("orders", None)
            self._cache.pop("positions", None)
            self._cache.pop("balance", None)
        return result

    def replace_order(self, order_id: str, *, price: float, quantity: int) -> BrokerOrderResult:
        valid, reason, normalized = validate_quantity(quantity)
        if not valid:
            return BrokerOrderResult(False, "REJECTED", message=reason, error="INVALID_QUANTITY")
        api_price = dnse_price(price)
        detail = self.get_order_detail(order_id) or {}
        symbol = str(detail.get("symbol", "") or "").upper()
        if symbol:
            secdef = self.get_secdef(symbol) or {}
            floor_price = dnse_price(secdef.get("floorPrice", 0.0) or 0.0)
            ceiling_price = dnse_price(secdef.get("ceilingPrice", 0.0) or 0.0)
            if not price_in_band(api_price, floor_price, ceiling_price):
                return BrokerOrderResult(
                    False,
                    "REJECTED",
                    message=f"Giá sửa ngoài biên [{floor_price:g} .. {ceiling_price:g}].",
                    error="PRICE_OUT_OF_BAND",
                )
        ok, data, status, message = self._request(
            "PUT",
            f"/accounts/{self.account_no}/orders/{order_id}",
            params={"marketType": "STOCK", "orderCategory": "NORMAL"},
            payload={"price": api_price, "quantity": normalized},
            require_token=True,
        )
        self._cache.pop("orders", None)
        self._cache.pop(f"order_detail:{order_id}", None)
        result = self._result(ok, data, status, message)
        if not ok and (status == 0 or status >= 500):
            result.status, result.error = "UNKNOWN", "ORDER_STATUS_UNKNOWN"
        return result

    def cancel_order(self, order_id: str) -> BrokerOrderResult:
        ok, data, status, message = self._request(
            "DELETE",
            f"/accounts/{self.account_no}/orders/{order_id}",
            params={"marketType": "STOCK", "orderCategory": "NORMAL"},
            require_token=True,
        )
        self._cache.pop("orders", None)
        self._cache.pop(f"order_detail:{order_id}", None)
        result = self._result(ok, data, status, message)
        if not ok and (status == 0 or status >= 500):
            result.status, result.error = "UNKNOWN", "ORDER_STATUS_UNKNOWN"
        return result
