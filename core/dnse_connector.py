# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
import json
import copy
import logging
import os
import random
import re
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

import config
from core import settlement, settlement_ledger, stock_rules
from core.dnse_signature import generate_signature_header


logger = logging.getLogger(__name__)

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
DNSE_POINT_VALUE = 100000.0


@dataclass
class BrokerTick:
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    spread: float = 0.0
    ceiling: float = 0.0
    floor: float = 0.0
    reference: float = 0.0
    timestamp: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerSymbolInfo:
    symbol: str
    point: float = 0.1
    trade_contract_size: float = DNSE_POINT_VALUE
    volume_min: float = 1.0
    volume_max: float = 200.0
    volume_step: float = 1.0
    trade_stops_level: float = 0.0
    spread: float = 0.0
    market_type: str = "DERIVATIVE"
    quantity_label: str = "Hợp đồng"
    quantity_unit: str = "HĐ"
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerPosition:
    ticket: str
    position_id: str
    order_id: str
    symbol: str
    type: int
    volume: float
    price_open: float
    price_current: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""
    magic: int = 0
    time: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerOrderResult:
    ok: bool
    order_id: str = ""
    position_id: str = ""
    status: str = ""
    message: str = ""
    error: str = ""
    status_code: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def order(self) -> str:
        return self.order_id or self.position_id

    @property
    def ticket(self) -> str:
        return self.order

    @property
    def retcode(self) -> int:
        """Mã trạng thái lệnh. 200=OK (DNSE), 0=Failed."""
        return 200 if self.ok else 0


@dataclass
class BrokerFeeProfile:
    broker_fee_per_contract: float = 0.0
    exchange_fee_per_contract: float = 2700.0
    clearing_fee_per_contract: float = 2550.0
    broker_fee_rate: float = 0.0
    tax_rate: float = 0.0
    initial_margin_rate: float = 0.0
    point_value: float = DNSE_POINT_VALUE
    market_type: str = "DERIVATIVE"
    quantity_unit: str = "HĐ"
    fee_available: bool = True
    source: str = "fallback"
    raw: Dict[str, Any] = field(default_factory=dict)

    def fixed_per_contract(self) -> float:
        return (
            float(self.broker_fee_per_contract or 0.0)
            + float(self.exchange_fee_per_contract or 0.0)
            + float(self.clearing_fee_per_contract or 0.0)
        )

    def estimate_fee(self, price: float, contracts: float, side: Any = None) -> float:
        qty = max(0.0, float(contracts or 0.0))
        fixed = self.fixed_per_contract() * qty
        notional = max(0.0, float(price or 0.0)) * qty * float(self.point_value or DNSE_POINT_VALUE)
        rate_fee = notional * float(self.broker_fee_rate or 0.0)
        market_type = str(self.market_type or "DERIVATIVE").upper()
        side_key = str(side or "").upper()
        if market_type == "DERIVATIVE":
            # TT 87/2026/TT-BTC: giá tính thuế HĐTL = giá thanh toán × hệ số
            # hợp đồng × số HĐ × tỷ lệ ký quỹ ban đầu / 2.
            tax_base = notional * float(self.initial_margin_rate or 0.0) / 2.0
            tax = tax_base * float(self.tax_rate or 0.0)
        else:
            # CKCS chỉ phát sinh thuế chuyển nhượng ở phía bán. side=None giữ
            # hành vi ước tính thận trọng cho caller cũ.
            is_sell = not side_key or side_key in {"1", "SELL", "SHORT", "NS", "S"}
            tax = notional * float(self.tax_rate or 0.0) if is_sell else 0.0
        return fixed + rate_fee + tax

    def as_dict(self) -> Dict[str, Any]:
        return {
            "broker_fee_per_contract": float(self.broker_fee_per_contract or 0.0),
            "exchange_fee_per_contract": float(self.exchange_fee_per_contract or 0.0),
            "clearing_fee_per_contract": float(self.clearing_fee_per_contract or 0.0),
            "broker_fee_rate": float(self.broker_fee_rate or 0.0),
            "tax_rate": float(self.tax_rate or 0.0),
            "initial_margin_rate": float(self.initial_margin_rate or 0.0),
            "point_value": float(self.point_value or DNSE_POINT_VALUE),
            "market_type": self.market_type,
            "quantity_unit": self.quantity_unit,
            "fee_available": bool(self.fee_available),
            "source": self.source,
            "raw": dict(self.raw or {}),
        }


def _first_value(data: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _deep_first_value(data: Any, keys: Iterable[str], default: Any = None) -> Any:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _deep_first_value(value, keys, None)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _deep_first_value(value, keys, None)
            if found not in (None, ""):
                return found
    return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _unwrap_payload(data: Any, collection_keys: Tuple[str, ...] = ()) -> Any:
    if not isinstance(data, dict):
        return data
    for key in collection_keys:
        value = data.get(key)
        if value is not None:
            return value
    for key in ("data", "items", "content", "result"):
        value = data.get(key)
        if value is not None:
            return value
    return data


class ModeBoundConnector:
    """Bind one shared DNSE connector to PAPER or REAL for background management."""

    def __init__(self, connector: "DNSEConnector", paper_mode: bool):
        self._connector = connector
        self._paper_mode = bool(paper_mode)

    def __getattr__(self, name):
        return getattr(self._connector, name)

    def _is_paper_mode(self) -> bool:
        return self._paper_mode

    def get_account_info(self, paper_mode: Optional[bool] = None):
        return self._connector.get_account_info(paper_mode=self._paper_mode)

    def get_positions(self, paper_mode: Optional[bool] = None):
        return self._connector.get_positions(paper_mode=self._paper_mode)

    def get_all_open_positions(self, paper_mode: Optional[bool] = None):
        return self._connector.get_all_open_positions(paper_mode=self._paper_mode)

    def get_orders(self, **params):
        params["paper_mode"] = self._paper_mode
        return self._connector.get_orders(**params)

    def place_order(
        self,
        symbol,
        order_type,
        lot,
        sl,
        tp,
        magic=0,
        comment="",
        order_kind=None,
        price=0.0,
    ) -> BrokerOrderResult:
        return self._connector.place_order(
            symbol,
            order_type,
            lot,
            sl,
            tp,
            magic,
            comment,
            order_kind=order_kind,
            price=price,
            paper_mode=self._paper_mode,
        )

    def place_close_limit_order(
        self,
        symbol,
        order_type,
        lot,
        price,
        comment="",
    ) -> BrokerOrderResult:
        return self._connector.place_close_limit_order(
            symbol,
            order_type,
            lot,
            price,
            comment=comment,
            paper_mode=self._paper_mode,
        )

    def cancel_order(self, order_id: str, **params) -> BrokerOrderResult:
        params["paper_mode"] = self._paper_mode
        return self._connector.cancel_order(order_id, **params)

    def get_paper_closed_trade(self, ticket: Any):
        if not self._paper_mode:
            return None
        return self._connector._paper().get_closed_trade(ticket)

    def modify_position(
        self, position_or_ticket: Any, sl: float = 0.0, tp: float = 0.0
    ) -> BrokerOrderResult:
        return self._connector.modify_position(
            position_or_ticket,
            sl,
            tp,
            paper_mode=self._paper_mode,
        )

    def close_position(
        self, position_or_ticket: Any, comment: str = ""
    ) -> BrokerOrderResult:
        return self._connector.close_position(
            position_or_ticket,
            comment,
            paper_mode=self._paper_mode,
        )


class DNSEConnector:
    _balances_403_muted = False
    _positions_403_muted = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        account_no: Optional[str] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        load_dotenv(encoding="utf-8-sig")
        def _clean_acc(value: str) -> str:
            # Số tiểu khoản là chuỗi số; loại các giá trị rác như "True"/"False"/"None".
            v = str(value or "").strip()
            return "" if v.lower() in ("true", "false", "none") else v

        explicit_account_no = _clean_acc(account_no)
        self.api_key = api_key or os.getenv("DNSE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("DNSE_API_SECRET", "")
        self.stock_account_no = explicit_account_no or _clean_acc(os.getenv("DNSE_STOCK_ACCOUNT_NO", ""))
        self.derivative_account_no = explicit_account_no or _clean_acc(os.getenv("DNSE_DERIVATIVE_ACCOUNT_NO", ""))
        self.custody_code = os.getenv("DNSE_CUSTODY_CODE", "")
        self.account_no = (
            explicit_account_no
            or self.derivative_account_no
            or self.stock_account_no
            or _clean_acc(os.getenv("DNSE_ACCOUNT_NO", ""))
        )
        self.otp_type = os.getenv("DNSE_OTP_TYPE", "email_otp")
        self.base_url = (base_url or os.getenv("DNSE_BASE_URL", "https://openapi.dnse.com.vn")).rstrip("/")
        self.session = session or requests.Session()
        self.is_connected = False
        self.trading_token: Optional[str] = None
        self.trading_token_expires_at: float = 0.0
        self.trading_token_persistent: bool = False
        self.last_request_time = 0.0
        # [FREEZE FIX] Khóa để giãn nhịp rate-limit nhất quán giữa các thread (UI + nền).
        # Trước đây 2 thread đọc last_request_time cùng lúc -> cùng bỏ qua wait -> burst -> 429.
        self._rate_lock = threading.Lock()
        self._quota_lock = threading.RLock()
        self._endpoint_throttle_until: Dict[str, float] = {}
        self._endpoint_request_locks: Dict[str, threading.Lock] = {}
        self._rate_limit_strikes: Dict[str, int] = {}
        self._network_error_active: set[str] = set()
        self.last_latency_ms = 0.0
        self.market_type = os.getenv("DNSE_MARKET_TYPE", "DERIVATIVE")
        self.order_category = os.getenv("DNSE_ORDER_CATEGORY", "NORMAL")
        self._paper_broker = None
        self._account_cache: Optional[Dict[str, Any]] = None
        self._account_cache_ts: float = 0.0
        self._account_unavailable_until: float = 0.0
        self._account_failure_streak: int = 0
        self._account_outage_logged: bool = False
        self._positions_cache: List[BrokerPosition] = []
        self._positions_cache_ts: float = 0.0
        self._positions_unavailable_until: float = 0.0
        self._positions_failure_streak: int = 0
        self._positions_outage_logged: bool = False
        self._positions_suppressed_errors: int = 0
        self._orders_cache: List[Dict[str, Any]] = []
        self._orders_cache_ts: float = 0.0
        # Orders belong to separate CKPS/CKCS accounts.  A single cache can
        # make a fresh derivative response incorrectly suppress a stock fetch
        # (or vice versa), so retain one snapshot per account + market.
        self._orders_cache_by_scope: Dict[
            Tuple[str, str],
            List[Dict[str, Any]],
        ] = {}
        self._orders_cache_ts_by_scope: Dict[Tuple[str, str], float] = {}
        # DNSE order service may temporarily return
        # ``400 AccountRepository: cannot find object`` during maintenance even
        # though GET /accounts and /balances still work.  Do not hammer the
        # endpoint every UI refresh while that repository is unavailable.
        self._orders_unavailable_until: float = 0.0
        self._orders_error_log_ts: float = 0.0
        self._orders_error_key: str = ""
        self._orders_suppressed_errors: int = 0
        self._orders_failure_streak: int = 0
        self._orders_outage_logged: bool = False
        self._read_auth_blocked_marker = None
        self._fee_profile_cache: Dict[str, BrokerFeeProfile] = {}
        self._fee_profile_cache_ts: Dict[str, float] = {}
        self._ppse_cache: Dict[str, Dict[str, Any]] = {}
        self._ppse_cache_ts: Dict[str, float] = {}
        self._tick_cache: Dict[str, BrokerTick] = {}
        self._tick_cache_ts: Dict[str, float] = {}
        self._market_tick_provider = None
        self._market_state_provider = None
        # Map alias phái sinh (symbolType: VN30F1M...) -> mã hợp đồng thật (41I1G6000...).
        # Mã thật ĐỔI theo tháng đáo hạn nên tra động từ /instruments rồi cache.
        self._symbol_map: Dict[str, str] = {}
        self._symbol_map_ts: float = 0.0
        self._derivative_real_symbols: set = set()
        self._working_dates: List[str] = []
        self._working_dates_ts: float = 0.0
        self.api_stats = {
            "started_at": time.time(),
            "total_requests": 0,
            "by_endpoint": {},
            "last_endpoint": "",
            "last_status": None,
            "last_error": "",
            "last_latency_ms": 0.0,
            "rate_limits": {},
            "throttled_endpoints": {},
            "suppressed_429": 0,
            "broker_api_state": "LIVE",
            "broker_api_consecutive_failures": 0,
        }
        self._load_token_from_disk()  # opt-in: nạp token đã lưu (nếu còn hiệu lực) để khỏi OTP lại sau restart

    @property
    def _is_connected(self) -> bool:
        return self.is_connected

    @_is_connected.setter
    def _is_connected(self, value: bool):
        self.is_connected = bool(value)

    def connect(self) -> bool:
        if not self.api_key or not self.api_secret or not self.account_no:
            logger.error("DNSE_API_KEY, DNSE_API_SECRET and DNSE_ACCOUNT_NO are required.")
            self.is_connected = False
            return False
        self.is_connected = True
        logger.info("Connected to DNSE OpenAPI for account %s.", self.account_no)
        return True

    def shutdown(self):
        self.is_connected = False
        self.session.close()

    def _is_paper_mode(self) -> bool:
        return bool(getattr(config, "PAPER_TRADING", True))

    def _paper(self):
        if self._paper_broker is None:
            from core.paper_broker import PaperBroker
            self._paper_broker = PaperBroker(
                self.account_no or "PAPER",
                tick_provider=self.get_tick,
                fee_profile_provider=self.get_fee_profile,
                working_dates_provider=self.get_working_dates,
            )
        return self._paper_broker

    def reset_paper(self, balance: Optional[float] = None) -> Dict[str, Any]:
        return self._paper().reset(balance)

    def get_paper_closed_trade(self, ticket: Any) -> Optional[Dict[str, Any]]:
        if not self._is_paper_mode():
            return None
        return self._paper().get_closed_trade(ticket)

    def get_paper_closed_trades(self) -> List[Dict[str, Any]]:
        return self._paper().get_closed_trades()

    def delete_paper_closed_trades_for_day(self, day: str) -> int:
        return self._paper().delete_closed_trades_for_day(day)

    def reset_session_caches(self):
        """Xoá cache tài khoản/vị thế — gọi khi đổi PAPER<->REAL để lần đọc sau lấy số liệu mới
        (không cần restart app). Token + market-data cache giữ nguyên."""
        self._account_cache = None
        self._account_cache_ts = 0.0
        self._account_unavailable_until = 0.0
        self._account_failure_streak = 0
        self._account_outage_logged = False
        self._positions_cache = []
        self._positions_cache_ts = 0.0
        self._positions_unavailable_until = 0.0
        self._positions_failure_streak = 0
        self._positions_outage_logged = False
        self._positions_suppressed_errors = 0
        self._orders_cache = []
        self._orders_cache_ts = 0.0
        self._orders_cache_by_scope = {}
        self._orders_cache_ts_by_scope = {}
        self._orders_unavailable_until = 0.0
        self._orders_failure_streak = 0
        self._orders_outage_logged = False
        self._orders_suppressed_errors = 0
        self._read_auth_blocked_marker = None

    def set_market_data_provider(self, tick_provider=None, state_provider=None):
        """Đặt feed giá dùng chung. Khi provider tồn tại connector không gọi latest REST."""
        self._market_tick_provider = tick_provider
        self._market_state_provider = state_provider

    def get_market_data_state(self) -> str:
        if callable(self._market_state_provider):
            try:
                return str(self._market_state_provider() or "RECOVERING").upper()
            except Exception:
                return "RECOVERING"
        return "LIVE"

    # ---- Lưu/nạp trading-token qua restart (opt-in, PERSIST_TRADING_TOKEN) ----
    def _token_file(self) -> Optional[str]:
        acc = str(self.account_no or "").strip()
        return os.path.join("data", acc, "trading_token.json") if acc else None

    def _configured_token_ttl_seconds(self) -> float:
        ttl_map = getattr(config, "DNSE_TOKEN_TTL_HOURS", {}) or {}
        ttl_hours = float(ttl_map.get(self.otp_type, 8.0) or 0.0)
        return max(0.0, ttl_hours * 3600.0)

    def _clear_trading_token(self, remove_file: bool = False):
        self.trading_token = None
        self.trading_token_expires_at = 0.0
        self.trading_token_persistent = False
        if remove_file:
            path = self._token_file()
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as exc:
                    logger.warning("Xoa trading-token cache loi: %s", exc)

    def _save_token_to_disk(self):
        if not bool(getattr(config, "PERSIST_TRADING_TOKEN", False)):
            return
        path = self._token_file()
        if not path or not self.trading_token:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "trading_token": self.trading_token,
                    "otp_type": self.otp_type,
                    "saved_at": time.time(),
                    "expires_at": self.trading_token_expires_at,
                    "persistent": self.trading_token_persistent,
                }, f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lưu trading-token lỗi: %s", exc)

    def _load_token_from_disk(self):
        if not bool(getattr(config, "PERSIST_TRADING_TOKEN", False)):
            return
        path = self._token_file()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            token = str(data.get("trading_token") or "")
            exp = float(data.get("expires_at") or 0.0)
            ttl_seconds = self._configured_token_ttl_seconds()
            if ttl_seconds > 0 and exp > time.time() + ttl_seconds + 300.0:
                logger.info("Bo qua trading-token cache cu co han vuot TTL DNSE.")
                return
            if token and time.time() < exp:
                self.trading_token = token
                self.trading_token_expires_at = exp
                self.trading_token_persistent = bool(data.get("persistent", False)) and ttl_seconds <= 0
                logger.info("Đã nạp trading-token đã lưu (còn hiệu lực).")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Nạp trading-token lỗi: %s", exc)

    def _account_pending_info(self, reason: str = "") -> Dict[str, Any]:
        # REAL chưa đọc được DNSE thì phải hiển thị chưa xác nhận/0, tuyệt đối
        # không mượn vốn PAPER 100 triệu làm số dư tạm.
        pending_balance = 0.0
        return {
            "login": self.account_no,
            "server": "DNSE_API_REAL_PENDING",
            "status": "ACCOUNT_PENDING",
            "balance": pending_balance,
            "equity": pending_balance,
            "margin": 0.0,
            "free_margin": pending_balance,
            "margin_free": pending_balance,
            "margin_level": 0.0,
            "reason": reason,
        }

    def _rate_limit(self):
        with self._rate_lock:
            now = time.time()
            wait_s = 0.1 - (now - self.last_request_time)
            if wait_s > 0:
                time.sleep(wait_s)
            self.last_request_time = time.time()

    def _record_api_request(self, method: str, path: str, status_code: int, latency_ms: float, error: str = ""):
        endpoint = f"{method.upper()} {path}"
        self.api_stats["total_requests"] = int(self.api_stats.get("total_requests", 0) or 0) + 1
        by_endpoint = self.api_stats.setdefault("by_endpoint", {})
        by_endpoint[endpoint] = int(by_endpoint.get(endpoint, 0) or 0) + 1
        self.api_stats["last_endpoint"] = endpoint
        self.api_stats["last_status"] = status_code
        self.api_stats["last_error"] = error or ""
        self.api_stats["last_latency_ms"] = float(latency_ms or 0.0)
        if not self._is_market_price_path(path):
            status = int(status_code or 0)
            error_text = str(error or "").lower()
            transient_backend_error = (
                status == 0
                or status >= 500
                or (
                    status == 400
                    and (
                        "accountrepository" in error_text
                        or "backend service" in error_text
                    )
                )
            )
            if 200 <= status < 500 and status != 429 and not transient_backend_error:
                self.api_stats["broker_api_consecutive_failures"] = 0
                self.api_stats["broker_api_state"] = "LIVE"
            elif transient_backend_error:
                failures = int(self.api_stats.get("broker_api_consecutive_failures", 0) or 0) + 1
                self.api_stats["broker_api_consecutive_failures"] = failures
                if failures >= 3:
                    self.api_stats["broker_api_state"] = "BROKER_API_DOWN"

    @staticmethod
    def _is_market_price_path(path: str) -> bool:
        return bool(re.match(r"^/price/[^/]+/(?:trades|quotes)/latest$", str(path or "")))

    @classmethod
    def _endpoint_family(cls, method: str, path: str) -> str:
        path_text = str(path or "")
        match = re.match(r"^/price/[^/]+/(trades|quotes)/latest$", path_text)
        if match:
            return f"{method.upper()} /price/*/{match.group(1)}/latest"
        return f"{method.upper()} {path_text}"

    @staticmethod
    def _parse_rate_reset(value: Any, now: Optional[float] = None) -> float:
        now = time.time() if now is None else float(now)
        raw = str(value or "").strip()
        if not raw:
            return 0.0
        try:
            numeric = float(raw)
            if numeric > 10_000_000_000:  # Unix milliseconds
                numeric /= 1000.0
            return numeric if numeric > now else now + max(0.0, numeric)
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(raw)
            return parsed.timestamp()
        except Exception:
            return 0.0

    def _record_rate_limit_headers(self, method: str, path: str, response: Any):
        endpoint = f"{method.upper()} {path}"
        family = self._endpoint_family(method, path)
        headers = getattr(response, "headers", {}) or {}
        limit_raw = headers.get("X-RateLimit-Limit")
        remaining_raw = headers.get("X-RateLimit-Remaining")
        reset_raw = headers.get("X-RateLimit-Reset")
        if limit_raw is None and remaining_raw is None and reset_raw is None:
            return
        now = time.time()
        try:
            limit = int(float(limit_raw)) if limit_raw is not None else None
        except (TypeError, ValueError):
            limit = None
        try:
            remaining = int(float(remaining_raw)) if remaining_raw is not None else None
        except (TypeError, ValueError):
            remaining = None
        reset_at = self._parse_rate_reset(reset_raw, now=now)
        with self._quota_lock:
            self.api_stats.setdefault("rate_limits", {})[endpoint] = {
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at or None,
                "updated_at": now,
            }
            if remaining is not None and remaining <= 1 and reset_at > now:
                self._endpoint_throttle_until[family] = reset_at
            self.api_stats["throttled_endpoints"] = {
                key: until
                for key, until in self._endpoint_throttle_until.items()
                if until > now
            }

    def _endpoint_throttle_seconds(self, method: str, path: str) -> float:
        endpoint = self._endpoint_family(method, path)
        now = time.time()
        with self._quota_lock:
            until = float(self._endpoint_throttle_until.get(endpoint, 0.0) or 0.0)
            if until <= now:
                self._endpoint_throttle_until.pop(endpoint, None)
                return 0.0
            return until - now

    def _throttle_endpoint(self, method: str, path: str, wait_seconds: float):
        endpoint = self._endpoint_family(method, path)
        until = time.time() + max(0.0, float(wait_seconds or 0.0))
        with self._quota_lock:
            self._endpoint_throttle_until[endpoint] = max(
                until, float(self._endpoint_throttle_until.get(endpoint, 0.0) or 0.0)
            )
            self.api_stats["throttled_endpoints"] = dict(self._endpoint_throttle_until)

    def get_api_health_snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._quota_lock:
            throttled = {
                endpoint: until
                for endpoint, until in self._endpoint_throttle_until.items()
                if until > now
            }
        with self._quota_lock:
            stats = copy.deepcopy(self.api_stats)
        return {
            **stats,
            "throttled_endpoints": throttled,
            "account_cache_age": max(0.0, time.time() - self._account_cache_ts) if self._account_cache else None,
            "positions_cache_age": max(0.0, time.time() - self._positions_cache_ts) if self._positions_cache_ts else None,
        }

    def _build_headers(self, method: str, path: str, *, require_trading_token: bool = False) -> Dict[str, str]:
        x_sig, date_str = generate_signature_header(self.api_key, self.api_secret, method, path)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Signature": x_sig,
            "X-Aux-Date": date_str,
            "version": os.getenv("DNSE_API_VERSION", "2026-05-07"),
        }
        # Trading token chỉ dành cho endpoint thực sự cần quyền giao dịch.
        # Gắn token cũ vào API chỉ-đọc (balances/positions/orders) có thể khiến
        # DNSE trả 401 dù API key/signature vẫn hoàn toàn hợp lệ.
        if require_trading_token and self.trading_token:
            headers["trading-token"] = self.trading_token
        if require_trading_token and not self.has_trading_token():
            raise RuntimeError("DNSE trading token is missing or expired. Verify OTP first.")
        return headers

    def _request(self, method: str, path: str, **kwargs) -> Tuple[bool, Any, int, str]:
        endpoint = self._endpoint_family(method, path)
        with self._quota_lock:
            request_lock = self._endpoint_request_locks.setdefault(endpoint, threading.Lock())
        # Serialize the same endpoint so one 429 response can close the gate before
        # another UI/daemon thread creates a retry storm.
        with request_lock:
            return self._request_serialized(method, path, **kwargs)

    def _request_serialized(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        require_trading_token: bool = False,
        timeout: float = 15.0,
    ) -> Tuple[bool, Any, int, str]:
        if not self.is_connected and not self.connect():
            return False, None, 0, "NOT_CONNECTED"
        if require_trading_token and not self.has_trading_token():
            message = "TRADING_TOKEN_REQUIRED: verify OTP before changing orders or positions."
            self._record_api_request(method, path, 401, 0.0, message)
            return False, None, 401, message
        throttle_seconds = self._endpoint_throttle_seconds(method, path)
        if throttle_seconds > 0:
            message = f"LOCAL_RATE_LIMIT: retry after {throttle_seconds:.1f}s"
            self.api_stats["suppressed_429"] = int(self.api_stats.get("suppressed_429", 0) or 0) + 1
            return False, None, 429, message
        max_429_retries = int(getattr(config, "DNSE_RATE_LIMIT_RETRIES", 1) or 0)
        attempt = 0
        try:
            while True:
                self._rate_limit()
                headers = self._build_headers(method, path, require_trading_token=require_trading_token)
                started = time.perf_counter()
                response = self.session.request(
                    method.upper(),
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_payload,
                    headers=headers,
                    timeout=timeout,
                )
                self.last_latency_ms = (time.perf_counter() - started) * 1000.0
                self._record_rate_limit_headers(method, path, response)
                try:
                    data = response.json()
                except ValueError:
                    data = {"text": response.text}
                if 200 <= response.status_code < 300:
                    family = self._endpoint_family(method, path)
                    self._rate_limit_strikes.pop(family, None)
                    if family in self._network_error_active:
                        logger.info("DNSE %s recovered.", family)
                        self._network_error_active.discard(family)
                    self._record_api_request(method, path, response.status_code, self.last_latency_ms)
                    return True, data, response.status_code, ""
                # [24/7] Dính rate-limit (429): chờ theo Retry-After rồi thử lại tối đa N lần.
                is_market_price = self._is_market_price_path(path)
                if response.status_code == 429 and attempt < max_429_retries and not is_market_price:
                    attempt += 1
                    try:
                        wait_s = float(response.headers.get("Retry-After", "") or 0.0)
                    except (TypeError, ValueError):
                        wait_s = 0.0
                    reset_at = self._parse_rate_reset(response.headers.get("X-RateLimit-Reset"))
                    reset_wait = max(0.0, reset_at - time.time()) if reset_at else 0.0
                    wait_s = wait_s if wait_s > 0 else (reset_wait if reset_wait > 0 else min(30.0, 2.0 ** attempt))
                    wait_s += random.uniform(0.05, min(0.5, max(0.05, wait_s * 0.1)))
                    self._throttle_endpoint(method, path, wait_s)
                    if wait_s > 30.0:
                        message = data.get("message") if isinstance(data, dict) else str(data)
                        self._record_api_request(method, path, 429, self.last_latency_ms, message or "RATE_LIMITED")
                        return False, data, 429, message or "RATE_LIMITED"
                    logger.warning("DNSE %s %s rate-limited (429). Backoff %.1fs (lần %d).", method.upper(), path, wait_s, attempt)
                    time.sleep(wait_s)
                    with self._quota_lock:
                        self._endpoint_throttle_until.pop(f"{method.upper()} {path}", None)
                    continue
                if response.status_code == 429:
                    try:
                        retry_after = float(response.headers.get("Retry-After", "") or 0.0)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    reset_at = self._parse_rate_reset(response.headers.get("X-RateLimit-Reset"))
                    reset_wait = max(0.0, reset_at - time.time()) if reset_at else 0.0
                    family = self._endpoint_family(method, path)
                    strikes = int(self._rate_limit_strikes.get(family, 0) or 0) + 1
                    self._rate_limit_strikes[family] = strikes
                    default_wait = min(300.0, 60.0 * (2 ** max(0, strikes - 1)))
                    wait_s = retry_after or reset_wait or default_wait
                    self._throttle_endpoint(method, path, wait_s)
                    logger.warning(
                        "DNSE %s bị giới hạn 429; khóa nhóm %.0fs.", family, wait_s
                    )
                message = data.get("message") if isinstance(data, dict) else str(data)
                if response.status_code == 401 and "token is invalid" in str(message or data).lower():
                    self._clear_trading_token(remove_file=True)
                self._record_api_request(method, path, response.status_code, self.last_latency_ms, message or response.text)
                return False, data, response.status_code, message or response.text
        except Exception as exc:
            self._record_api_request(method, path, 0, 0.0, str(exc))
            log_key = self._endpoint_family(method, path)
            if log_key not in self._network_error_active:
                logger.error("DNSE %s failed: %s", log_key, exc)
                self._network_error_active.add(log_key)
            return False, None, 0, str(exc)

    def has_trading_token(self) -> bool:
        return bool(self.trading_token and time.time() < self.trading_token_expires_at)

    def trading_token_seconds_left(self) -> float:
        """Số giây còn lại của trading-token (0 nếu chưa có/đã hết). Cho UI cảnh báo."""
        if not self.trading_token:
            return 0.0
        return max(0.0, float(self.trading_token_expires_at or 0.0) - time.time())

    def send_email_otp(self) -> bool:
        ok, data, status_code, message = self._request("POST", "/registration/send-email-otp")
        if not ok:
            logger.error("DNSE send email OTP failed [%s]: %s", status_code, message or data)
        return ok

    def verify_otp(self, otp_type: Optional[str], passcode: str) -> bool:
        payload = {"otpType": otp_type or self.otp_type, "passcode": str(passcode)}
        ok, data, status_code, message = self._request("POST", "/registration/trading-token", json_payload=payload)
        if not ok or not isinstance(data, dict):
            logger.error("DNSE OTP verification failed [%s]: %s", status_code, message or data)
            return False
        token = _first_value(data, ("tradingToken", "trading-token", "token", "data"))
        if isinstance(token, dict):
            token = _first_value(token, ("tradingToken", "trading-token", "token"))
        if not token:
            logger.error("DNSE OTP verification did not return trading token: %s", data)
            return False
        self.trading_token = str(token)
        self._read_auth_blocked_marker = None
        # DNSE docs: trading-token is valid for about 8 hours.
        ttl_seconds = self._configured_token_ttl_seconds()
        ttl_hours = ttl_seconds / 3600.0
        self.trading_token_persistent = ttl_hours <= 0
        if self.trading_token_persistent:
            self.trading_token_expires_at = time.time() + (10 * 365 * 24 * 3600)
            logger.info("DNSE trading token ready (%s — không tự hết hạn).", self.otp_type)
        else:
            self.trading_token_expires_at = time.time() + ttl_seconds
            logger.info("DNSE trading token ready (~%.0fh, %s).", ttl_hours, self.otp_type)
        self._save_token_to_disk()  # opt-in: lưu để restart khỏi OTP lại
        return True

    def get_account_info(self, paper_mode: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """Lấy tài sản theo mode; ``None`` giữ hành vi PAPER/REAL hiện tại."""
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return self._paper().get_account_info()
        now = time.time()
        if now < self._account_unavailable_until:
            if self._account_cache is not None:
                return dict(self._account_cache)
            return self._account_pending_info("DNSE balances đang chờ thử lại.")
        cache_ttl = float(getattr(config, "DNSE_ACCOUNT_CACHE_TTL_SECONDS", 5.0) or 0.0)
        if self._account_cache is not None and cache_ttl > 0 and (now - self._account_cache_ts) < cache_ttl:
            return dict(self._account_cache)
        stale_info = dict(self._account_cache) if self._account_cache is not None else None
        ok, data, status_code, message = self._request("GET", f"/accounts/{self.account_no}/balances")
        if not ok:
            self._account_failure_streak += 1
            base_backoff = max(
                30.0,
                float(getattr(config, "DNSE_BROKER_BACKOFF_SECONDS", 60.0) or 60.0),
            )
            backoff = min(300.0, base_backoff * (2 ** min(3, self._account_failure_streak - 1)))
            self._account_unavailable_until = now + backoff
            if status_code == 403:
                if not DNSEConnector._balances_403_muted:
                    logger.warning(
                        "DNSE balances unavailable [403]: %s. Retry in %.0fs.",
                        message or data,
                        backoff,
                    )
                    DNSEConnector._balances_403_muted = True
                if stale_info is not None:
                    return stale_info
                info = self._account_pending_info(message or "DNSE account access pending.")
                self._account_cache = dict(info)
                self._account_cache_ts = time.time()
                return info
            if not self._account_outage_logged:
                # Network exceptions are already logged once by _request(); only
                # add this line for an HTTP response from the broker.
                if int(status_code or 0) != 0:
                    logger.warning(
                        "DNSE balances temporarily unavailable [%s]: %s. Retry in %.0fs.",
                        status_code,
                        message or data,
                        backoff,
                    )
                self._account_outage_logged = True
                self._balances_error_logged = True
            if stale_info is not None:
                return stale_info
            info = self._account_pending_info(message or "DNSE account unavailable.")
            self._account_cache = dict(info)
            self._account_cache_ts = time.time()
            return info
        if self._account_outage_logged or self._account_failure_streak:
            logger.info("DNSE balances recovered.")
        self._account_unavailable_until = 0.0
        self._account_failure_streak = 0
        self._account_outage_logged = False
        self._balances_error_logged = False
        payload = _unwrap_payload(data)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict):
            payload = {}
        # [FIX] Response DNSE /balances có 2 khối: "stock" (totalCash/availableCash) và
        # "derivative" (remainSecure). KHÔNG có key top-level "balance"/"nav" -> trước đây ra 0.
        stock_blk = payload.get("stock") if isinstance(payload.get("stock"), dict) else {}
        deriv_blk = payload.get("derivative") if isinstance(payload.get("derivative"), dict) else {}
        stock_total = _to_float(stock_blk.get("totalCash"), 0.0)
        stock_avail = _to_float(stock_blk.get("availableCash"), stock_total)
        deriv_avail = _to_float(deriv_blk.get("remainSecure"), 0.0)
        legacy_bal = _to_float(_deep_first_value(payload, ("balance", "cashBalance", "totalAsset", "netAssetValue", "nav")), 0.0)
        balance = stock_total or deriv_avail or legacy_bal
        equity = _to_float(_deep_first_value(payload, ("equity", "netAssetValue", "nav", "totalAsset"), balance), balance)
        margin = _to_float(_deep_first_value(payload, ("margin", "marginValue", "derivativeMargin", "usedSecure")), 0.0)
        cash_available = _to_float(_deep_first_value(payload, ("cashAvailable", "cash_available", "cashBalanceAvailable", "availableCash")), 0.0) or stock_avail or deriv_avail
        buying_power = _to_float(_deep_first_value(payload, ("buyingPower", "buying_power", "purchasingPower")), 0.0) or stock_avail or deriv_avail
        available_balance = _to_float(_deep_first_value(payload, ("freeMargin", "availableBalance", "available_balance")), 0.0)
        free_margin = available_balance or cash_available or buying_power or (equity - margin)
        margin_debt = _to_float(_deep_first_value(payload, ("marginDebt", "margin_debt", "loan", "loanValue", "debt", "debtValue")), margin)
        rtt_raw = _deep_first_value(payload, ("rtt", "Rtt", "RTT", "marginRatio", "margin_ratio", "actualRatio"), None)
        rtt = None if rtt_raw in (None, "") else _to_float(rtt_raw, 0.0)
        margin_call_level = _to_float(_deep_first_value(payload, ("callRtt", "callMarginRatio", "marginCallLevel")), 87.0)
        margin_force_level = _to_float(_deep_first_value(payload, ("forceRtt", "forceSellRatio", "marginForceLevel")), 80.0)
        info = {
            "login": self.account_no,
            "server": "DNSE_API",
            "balance": balance,
            "equity": equity,
            "margin": margin,
            "free_margin": free_margin,
            "margin_free": free_margin,
            "margin_level": (equity / margin * 100.0) if margin else 0.0,
            "cash_available": cash_available,
            "buying_power": buying_power or free_margin,
            "margin_debt": margin_debt,
            "rtt": rtt,
            "margin_call_level": margin_call_level,
            "margin_force_level": margin_force_level,
            # Tách bạch 2 ví để UI hiện đúng (không gán nhầm tiền cơ sở thành ký quỹ phái sinh).
            "stock_cash": stock_avail or stock_total,
            "deriv_avail": deriv_avail,
            "deriv_used": _to_float(deriv_blk.get("usedSecure"), 0.0),
            "deriv_pending": _to_float(deriv_blk.get("pendingSecure"), 0.0),
            "deriv_hold_tax_fee": _to_float(deriv_blk.get("holdTaxAndFee"), 0.0),
            "raw": data,
        }
        self._account_cache = dict(info)
        self._account_cache_ts = time.time()
        return info

    def get_accounts(self) -> Dict[str, Any]:
        ok, data, status_code, message = self._request("GET", "/accounts")
        if not ok:
            logger.warning("DNSE get accounts failed [%s]: %s", status_code, message or data)
            return {"accounts": [], "error": message or data, "status_code": status_code}
        payload = _unwrap_payload(data)
        return payload if isinstance(payload, dict) else {"accounts": payload if isinstance(payload, list) else [], "raw": data}

    DERIVATIVE_TYPE_ALIASES = {"VN30F1M", "VN30F2M", "VN30F1Q", "VN30F2Q"}

    def _refresh_symbol_map(self) -> None:
        """Tra /instruments để map symbolType (VN30F1M) -> mã hợp đồng thật (41I1G6000). Cache 1h."""
        now = time.time()
        if self._symbol_map and (now - self._symbol_map_ts) < 3600.0:
            return
        try:
            ok, data, _status, _msg = self._request(
                "GET", "/instruments",
                params={"marketId": "DVX", "securityGroupId": "FU", "limit": 50},
            )
            if ok and isinstance(data, dict):
                m, real = {}, set()
                for it in data.get("data", []) or []:
                    st = str(it.get("symbolType", "") or "").upper()
                    sym = str(it.get("symbol", "") or "")
                    if st and sym:
                        m[st] = sym
                        real.add(sym.upper())
                if m:
                    self._symbol_map = m
                    self._derivative_real_symbols = real
                    self._symbol_map_ts = now
        except Exception:
            pass

    def resolve_symbol(self, symbol: str) -> str:
        """VN30F1M (alias) -> mã hợp đồng thật. Mã thường/đã thật thì giữ nguyên."""
        s = str(symbol or "").upper()
        if s not in self.DERIVATIVE_TYPE_ALIASES:
            return symbol
        self._refresh_symbol_map()
        return self._symbol_map.get(s, symbol)

    def market_type_for_symbol(self, symbol: str) -> str:
        sym = str(symbol or "").upper()
        derivatives = {str(s).upper() for s in getattr(config, "CKPS_SYMBOLS", []) or []}
        if (
            sym.startswith("VN30F")
            or sym in derivatives
            or sym in self.DERIVATIVE_TYPE_ALIASES
            or sym in self._derivative_real_symbols
        ):
            return "DERIVATIVE"
        return "STOCK"

    def account_no_for_symbol(self, symbol: Optional[str] = None) -> str:
        market_type = self.market_type_for_symbol(symbol or "")
        if market_type == "STOCK":
            return self.stock_account_no or self.account_no or self.derivative_account_no
        return self.derivative_account_no or self.account_no or self.stock_account_no

    def quantity_unit_for_symbol(self, symbol: Optional[str] = None) -> str:
        return "HĐ" if self.market_type_for_symbol(symbol or "") == "DERIVATIVE" else "CP"

    def quantity_label_for_symbol(self, symbol: Optional[str] = None) -> str:
        return "Hợp đồng" if self.market_type_for_symbol(symbol or "") == "DERIVATIVE" else "Cổ phiếu"

    def get_orders(
        self,
        paper_mode: Optional[bool] = None,
        **params,
    ) -> List[Dict[str, Any]]:
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return []
        symbol = params.pop("symbol", None)
        all_symbols = bool(params.pop("all_symbols", False))
        market_type = self.market_type_for_symbol(symbol) if symbol else self.market_type
        account_no = self.account_no_for_symbol(symbol) if symbol else self.account_no
        cache_scope = (str(account_no or ""), str(market_type or "").upper())
        scoped_caches = getattr(self, "_orders_cache_by_scope", {})
        scoped_timestamps = getattr(self, "_orders_cache_ts_by_scope", {})
        if cache_scope in scoped_caches:
            scope_rows = list(scoped_caches[cache_scope])
            scope_timestamp = float(scoped_timestamps.get(cache_scope, 0.0) or 0.0)
        elif not scoped_caches:
            # Compatibility for old sessions/tests that only have the legacy
            # single cache populated.
            scope_rows = list(getattr(self, "_orders_cache", []) or [])
            scope_timestamp = float(getattr(self, "_orders_cache_ts", 0.0) or 0.0)
        else:
            scope_rows = []
            scope_timestamp = 0.0

        def _cached_rows() -> List[Dict[str, Any]]:
            rows = list(scope_rows)
            if symbol and not all_symbols:
                wanted = str(symbol).upper()
                rows = [
                    item
                    for item in rows
                    if not str(item.get("symbol", "") or "").strip()
                    or str(item.get("symbol", "")).upper() == wanted
                ]
            return rows

        try:
            from core.dnse_ws import market_ws
            ws_connected = market_ws.is_connected()
            ws_events = market_ws.latest_order_events() if ws_connected else []
        except Exception:
            ws_connected = False
            ws_events = []
        if ws_events and scope_rows:
            merged = {str(item.get("id") or item.get("orderId")): dict(item) for item in scope_rows}
            for event in ws_events:
                event_symbol = str(event.get("symbol", "") or "")
                if (
                    event_symbol
                    and self.market_type_for_symbol(event_symbol) != market_type
                ):
                    continue
                merged[str(event.get("id") or event.get("orderId"))] = dict(event)
            rows = list(merged.values())
            if symbol and not all_symbols:
                rows = [item for item in rows if str(item.get("symbol", "")).upper() == str(symbol).upper()]
            return rows
        reconcile = float(getattr(config, "DNSE_WS_RECONCILE_SECONDS", 30.0) or 30.0)
        if ws_connected and scope_timestamp and (time.time() - scope_timestamp) < reconcile:
            return _cached_rows()
        cache_ttl = float(
            getattr(config, "DNSE_ORDERS_CACHE_TTL_SECONDS", 5.0) or 0.0
        )
        if (
            cache_ttl > 0
            and scope_timestamp
            and (time.time() - scope_timestamp) < cache_ttl
        ):
            return _cached_rows()

        # A known temporary DNSE order-repository outage is not an account
        # configuration error. Keep the last known rows and retry at a measured
        # interval instead of sending the same request every few seconds.
        now = time.time()
        if now < self._orders_unavailable_until:
            self._orders_suppressed_errors += 1
            return _cached_rows()
        auth_marker = (self.api_key, self.trading_token or "")
        if self._read_auth_blocked_marker == auth_marker:
            self._orders_suppressed_errors += 1
            return _cached_rows()

        query = {"marketType": market_type, **params}
        ok, data, status_code, message = self._request("GET", f"/accounts/{account_no}/orders", params=query)
        if not ok:
            error_text = str(message or data or "")
            error_key = f"{status_code}|{account_no}|{market_type}|{error_text}"
            account_repo_unavailable = (
                int(status_code or 0) == 400
                and "accountrepository" in error_text.lower()
            )
            backend_unavailable = (
                int(status_code or 0) in (0, 408, 425, 429, 500, 502, 503, 504)
                or account_repo_unavailable
                or "error in backend service" in error_text.lower()
            )
            if backend_unavailable:
                self._orders_failure_streak += 1
                base_backoff = max(
                    30.0,
                    float(
                        getattr(
                            config,
                            "DNSE_ACCOUNT_REPOSITORY_BACKOFF_SECONDS",
                            60.0,
                        )
                        or 60.0
                    ),
                )
                backoff = min(
                    300.0,
                    base_backoff * (2 ** min(3, self._orders_failure_streak - 1)),
                )
                self._orders_unavailable_until = now + backoff
            if int(status_code or 0) == 401:
                # Không tiếp tục gọi endpoint riêng tư mỗi vài giây với cùng
                # trạng thái xác thực. OTP mới hoặc reload setting sẽ mở lại.
                self._read_auth_blocked_marker = (
                    self.api_key,
                    self.trading_token or "",
                )

            if backend_unavailable:
                if not self._orders_outage_logged:
                    # Transport exception status=0 was already logged once in
                    # _request(); do not print a second line for the same outage.
                    if int(status_code or 0) != 0:
                        logger.warning(
                            "DNSE orders temporarily unavailable [%s] account=%s "
                            "market=%s; keep cache and retry with backoff %.0fs.",
                            status_code,
                            account_no,
                            market_type,
                            self._orders_unavailable_until - now,
                        )
                    self._orders_outage_logged = True
                self._orders_error_key = error_key
                self._orders_error_log_ts = now
            elif error_key != self._orders_error_key:
                if int(status_code or 0) != 401 or self._read_auth_blocked_marker:
                    logger.error(
                        "DNSE get orders failed [%s] account=%s market=%s: %s",
                        status_code,
                        account_no,
                        market_type,
                        error_text,
                    )
                self._orders_error_key = error_key
                self._orders_error_log_ts = now
            return _cached_rows()
        payload = _unwrap_payload(data, ("orders",))
        rows = payload if isinstance(payload, list) else []
        if self._orders_outage_logged:
            logger.info(
                "DNSE orders recovered account=%s market=%s (%d internal checks suppressed).",
                account_no,
                market_type,
                self._orders_suppressed_errors,
            )
        normalized_rows = [dict(item) for item in rows if isinstance(item, dict)]
        now_cached = time.time()
        self._orders_cache_by_scope[cache_scope] = normalized_rows
        self._orders_cache_ts_by_scope[cache_scope] = now_cached
        self._orders_cache = [
            dict(item)
            for scope_items in self._orders_cache_by_scope.values()
            for item in scope_items
        ]
        self._orders_cache_ts = now_cached
        self._orders_unavailable_until = 0.0
        self._orders_failure_streak = 0
        self._orders_outage_logged = False
        self._read_auth_blocked_marker = None
        self._orders_error_key = ""
        self._orders_suppressed_errors = 0
        if symbol and not all_symbols:
            wanted = str(symbol).upper()
            return [
                item
                for item in rows
                if not str(item.get("symbol", "") or "").strip()
                or str(item.get("symbol", "")).upper() == wanted
            ]
        return rows

    def _fallback_fee_profile(self, source: str = "fallback", market_type: str = "DERIVATIVE") -> BrokerFeeProfile:
        if str(market_type).upper() == "STOCK":
            return BrokerFeeProfile(
                broker_fee_per_contract=0.0,
                exchange_fee_per_contract=0.0,
                clearing_fee_per_contract=0.0,
                broker_fee_rate=float(getattr(config, "DNSE_STOCK_BROKER_FEE_RATE", 0.0) or 0.0),
                tax_rate=float(getattr(config, "DNSE_STOCK_TAX_RATE", 0.0) or 0.0),
                initial_margin_rate=0.0,
                point_value=float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0),
                market_type="STOCK",
                quantity_unit="CP",
                fee_available=source != "fallback_stock_fee_unavailable",
                source=source,
            )
        return BrokerFeeProfile(
            broker_fee_per_contract=float(getattr(config, "DNSE_BROKER_FEE_PER_CONTRACT", 0.0) or 0.0),
            exchange_fee_per_contract=float(getattr(config, "DNSE_EXCHANGE_FEE_PER_CONTRACT", 2700.0) or 0.0),
            clearing_fee_per_contract=float(getattr(config, "DNSE_CLEARING_FEE_PER_CONTRACT", 2550.0) or 0.0),
            tax_rate=float(getattr(config, "DNSE_TAX_RATE", 0.0) or 0.0),
            initial_margin_rate=float(
                getattr(config, "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", 0.20) or 0.20
            ),
            point_value=float(getattr(config, "DNSE_POINT_VALUE", DNSE_POINT_VALUE) or DNSE_POINT_VALUE),
            market_type="DERIVATIVE",
            quantity_unit="HĐ",
            source=source,
        )

    def _parse_fee_profile_from_loan_packages(self, data: Any, market_type: str = "DERIVATIVE") -> Optional[BrokerFeeProfile]:
        payload = _unwrap_payload(data, ("loanPackages",))
        if isinstance(payload, dict):
            payload = payload.get("loanPackages") or payload.get("items") or [payload]
        if not isinstance(payload, list) or not payload:
            return None
        if str(market_type).upper() == "STOCK":
            stock_pkgs = [item for item in payload if isinstance(item, dict)]
            if not stock_pkgs:
                return None

            def _pkg_rate(p):
                return max(
                    _to_float(p.get("brokerFirmBuyingFeeRate"), 0.0),
                    _to_float(p.get("brokerFirmSellingFeeRate"), 0.0),
                )

            # DNSE trả nhiều gói (có gói phí 0 khuyến mãi) -> lấy gói phí CAO NHẤT cho
            # ước tính an toàn, tránh hiện phí 0 sai.
            package = max(stock_pkgs, key=_pkg_rate)
            buy_rate = _to_float(package.get("brokerFirmBuyingFeeRate"), 0.0)
            sell_rate = _to_float(package.get("brokerFirmSellingFeeRate"), buy_rate)
            broker_rate = max(buy_rate, sell_rate)
            return BrokerFeeProfile(
                broker_fee_per_contract=0.0,
                exchange_fee_per_contract=0.0,
                clearing_fee_per_contract=0.0,
                broker_fee_rate=broker_rate,
                tax_rate=float(getattr(config, "DNSE_STOCK_TAX_RATE", 0.0) or 0.0),
                initial_margin_rate=0.0,
                point_value=float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0),
                market_type="STOCK",
                quantity_unit="CP",
                fee_available=True,
                source="dnse_stock_loan_package",
                raw=package,
            )
        package = next((item for item in payload if isinstance(item, dict) and item.get("tradingFee")), None)
        if not isinstance(package, dict):
            return None
        fee = package.get("tradingFee") or {}
        if not isinstance(fee, dict):
            return None
        broker_fee = _to_float(_first_value(fee, ("fixedTradingFee", "fixedDailyCloseTradingFee")), 0.0)
        if not broker_fee:
            progress = fee.get("progressTradingFee") or []
            if isinstance(progress, list) and progress:
                first = progress[0] if isinstance(progress[0], dict) else {}
                broker_fee = _to_float(first.get("fee"), 0.0)
        return BrokerFeeProfile(
            broker_fee_per_contract=broker_fee,
            exchange_fee_per_contract=float(getattr(config, "DNSE_EXCHANGE_FEE_PER_CONTRACT", 2700.0) or 0.0),
            clearing_fee_per_contract=float(getattr(config, "DNSE_CLEARING_FEE_PER_CONTRACT", 2550.0) or 0.0),
            tax_rate=float(getattr(config, "DNSE_TAX_RATE", 0.0) or 0.0),
            initial_margin_rate=_to_float(
                package.get("initialRate"),
                float(getattr(config, "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", 0.20) or 0.20),
            ),
            point_value=float(getattr(config, "DNSE_POINT_VALUE", DNSE_POINT_VALUE) or DNSE_POINT_VALUE),
            market_type="DERIVATIVE",
            quantity_unit="HĐ",
            source="dnse_loan_package",
            raw=package,
        )

    def get_fee_profile(self, symbol: Optional[str] = None, *, force_refresh: bool = False) -> BrokerFeeProfile:
        symbol_key = str(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M")).upper()
        market_type = self.market_type_for_symbol(symbol_key)
        account_no = self.account_no_for_symbol(symbol_key)
        cache_ttl = float(getattr(config, "DNSE_FEE_CACHE_TTL_SECONDS", 3600.0) or 0.0)
        cached = self._fee_profile_cache.get(symbol_key)
        if cached and not force_refresh and cache_ttl > 0 and (time.time() - self._fee_profile_cache_ts.get(symbol_key, 0.0)) < cache_ttl:
            return cached

        ok, data, status_code, message = self._request(
            "GET",
            f"/accounts/{account_no}/loan-packages",
            params={"marketType": market_type, "symbol": symbol_key},
        )
        if ok:
            fallback_source = "fallback_stock_fee_unavailable" if market_type == "STOCK" else "fallback_no_fee_package"
            profile = self._parse_fee_profile_from_loan_packages(data, market_type) or self._fallback_fee_profile(fallback_source, market_type)
        else:
            if status_code == 403 and not getattr(self, "_fee_profile_403_logged", False):
                logger.warning("DNSE loan packages failed [403]: %s. Using fallback fee profile.", message or data)
                self._fee_profile_403_logged = True
            fallback_source = "fallback_stock_fee_unavailable" if market_type == "STOCK" else "fallback_account_pending"
            profile = self._fallback_fee_profile(fallback_source, market_type)
        self._fee_profile_cache[symbol_key] = profile
        self._fee_profile_cache_ts[symbol_key] = time.time()
        return profile

    def get_derivative_margin_capacity(
        self,
        symbol: str,
        price: float,
        *,
        paper_mode: bool = False,
        positions: Optional[List[BrokerPosition]] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Ký quỹ một HĐ + cọc còn + sức mở do DNSE xác nhận.

        REAL dùng ``/ppse`` cho qmax. PAPER tính sức mở mô phỏng từ
        vốn paper trừ ký quỹ ước tính của các vị thế CKPS đang mở.
        """
        alias = str(symbol or "VN30F1M").upper()
        px = max(0.0, float(price or 0.0))
        profile = self.get_fee_profile(alias)
        initial_rate = float(
            getattr(profile, "initial_margin_rate", 0.0)
            or getattr(config, "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", 0.20)
            or 0.20
        )
        point_value = float(
            getattr(profile, "point_value", 0.0)
            or getattr(config, "DNSE_POINT_VALUE", DNSE_POINT_VALUE)
            or DNSE_POINT_VALUE
        )
        margin_per_contract = px * point_value * initial_rate if px > 0 else 0.0
        account = self.get_account_info(paper_mode=paper_mode) or {}

        if paper_mode:
            used = 0.0
            for pos in positions or self.get_positions(paper_mode=True):
                pos_symbol = str(getattr(pos, "symbol", "") or "")
                if self.market_type_for_symbol(pos_symbol) != "DERIVATIVE":
                    continue
                qty = abs(float(getattr(pos, "volume", 0.0) or 0.0))
                pos_price = float(
                    getattr(pos, "price_current", 0.0)
                    or getattr(pos, "price_open", 0.0)
                    or px
                )
                used += qty * pos_price * point_value * initial_rate
            total = float(account.get("equity", 0.0) or 0.0)
            remaining = max(0.0, total - used)
            qmax = int(remaining // margin_per_contract) if margin_per_contract > 0 else 0
            return {
                "ok": margin_per_contract > 0,
                "source": "PAPER_ESTIMATE",
                "symbol": alias,
                "price": px,
                "initial_rate": initial_rate,
                "margin_per_contract": margin_per_contract,
                "remaining_secure": remaining,
                "used_secure": used,
                "qmax_buy": qmax,
                "qmax_sell": qmax,
                "loan_package_id": getattr(profile, "raw", {}).get("id") if isinstance(getattr(profile, "raw", {}), dict) else None,
            }

        remaining = float(account.get("deriv_avail", 0.0) or 0.0)
        used = float(account.get("deriv_used", 0.0) or 0.0)
        package = getattr(profile, "raw", {}) if isinstance(getattr(profile, "raw", {}), dict) else {}
        package_id = package.get("id")
        result = {
            "ok": margin_per_contract > 0,
            "source": "FORMULA",
            "symbol": alias,
            "price": px,
            "initial_rate": initial_rate,
            "margin_per_contract": margin_per_contract,
            "remaining_secure": remaining,
            "used_secure": used,
            "pending_secure": float(account.get("deriv_pending", 0.0) or 0.0),
            "qmax_buy": int(remaining // margin_per_contract) if margin_per_contract > 0 else 0,
            "qmax_sell": int(remaining // margin_per_contract) if margin_per_contract > 0 else 0,
            "loan_package_id": package_id,
        }
        if not package_id or px <= 0:
            result["message"] = "DNSE chưa trả gói ký quỹ hoặc giá"
            return result

        real_symbol = self.resolve_symbol(alias)
        cache_key = alias
        ttl = 10.0
        cached = self._ppse_cache.get(cache_key)
        if cached and not force_refresh and (time.time() - self._ppse_cache_ts.get(cache_key, 0.0)) < ttl:
            merged = dict(result)
            merged.update(cached)
            return merged
        account_no = self.derivative_account_no or self.account_no
        ok, data, status_code, message = self._request(
            "GET",
            f"/accounts/{account_no}/ppse",
            params={
                "marketType": "DERIVATIVE",
                "symbol": real_symbol,
                "loanPackageId": str(package_id),
                "price": f"{px:g}",
            },
        )
        payload = _unwrap_payload(data) if ok else {}
        if ok and isinstance(payload, dict):
            api_values = {
                "ok": True,
                "source": "DNSE_PPSE",
                "qmax_buy": _to_int(payload.get("qmaxBuy"), 0),
                "qmax_sell": _to_int(payload.get("qmaxSell"), 0),
                "ppse_price": _to_float(payload.get("price"), px),
            }
            self._ppse_cache[cache_key] = dict(api_values)
            self._ppse_cache_ts[cache_key] = time.time()
            result.update(api_values)
        else:
            result["message"] = f"PPSE {status_code}: {message or 'không khả dụng'}"
        return result

    def calculate_trade_fee(self, symbol: str, price: float, contracts: float, side: Any = None) -> float:
        return self.get_fee_profile(symbol).estimate_fee(price, contracts, side=side)

    def get_order_detail(self, order_id: str) -> Optional[Dict[str, Any]]:
        if self._is_paper_mode():
            return None
        ok, data, status_code, message = self._request("GET", f"/accounts/{self.account_no}/orders/{order_id}")
        if not ok:
            logger.error("DNSE order detail failed [%s]: %s", status_code, message or data)
            return None
        payload = _unwrap_payload(data)
        return payload if isinstance(payload, dict) else {"raw": data}

    def get_position_detail(self, position_id: Any, symbol: str = "") -> Optional[Dict[str, Any]]:
        if self._is_paper_mode():
            return None
        market_type = self.market_type_for_symbol(symbol) if symbol else "DERIVATIVE"
        ok, data, status_code, message = self._request(
            "GET",
            f"/accounts/positions/{position_id}",
            params={"marketType": market_type},
        )
        if not ok:
            logger.error("DNSE position detail failed [%s]: %s", status_code, message or data)
            return None
        payload = _unwrap_payload(data)
        return payload if isinstance(payload, dict) else None

    def _position_from_raw(self, item: Dict[str, Any]) -> BrokerPosition:
        side = str(_first_value(item, ("side", "positionSide", "type", "positionType"), "")).upper()
        position_type = ORDER_TYPE_BUY if side in ("NB", "LONG", "BUY", "B") else ORDER_TYPE_SELL
        volume = _to_float(_first_value(item, ("volume", "quantity", "openQuantity", "netQuantity", "qty")), 0.0)
        entry = _to_float(_first_value(item, ("priceOpen", "price_open", "avgPrice", "averagePrice", "costPrice")), 0.0)
        current = _to_float(_first_value(item, ("priceCurrent", "marketPrice", "currentPrice", "lastPrice")), entry)
        position_id = str(_first_value(item, ("positionId", "id", "positionID"), ""))
        order_id = str(_first_value(item, ("orderId", "openOrderId", "orderID"), ""))
        ticket = position_id or order_id or str(_first_value(item, ("ticket",), ""))
        return BrokerPosition(
            ticket=ticket,
            position_id=position_id or ticket,
            order_id=order_id,
            symbol=str(_first_value(item, ("symbol", "symbolCode", "code"), "")).upper(),
            type=position_type,
            volume=volume,
            price_open=entry,
            price_current=current,
            profit=_to_float(_first_value(item, ("profit", "unrealizedPnl", "unrealizedProfit", "pnl")), 0.0),
            swap=_to_float(item.get("swap"), 0.0),
            commission=_to_float(_first_value(item, ("commission", "fee")), 0.0),
            sl=_to_float(_first_value(item, ("sl", "stopLoss", "cutLossPrice")), 0.0),
            tp=_to_float(_first_value(item, ("tp", "takeProfit", "takeProfitPrice")), 0.0),
            comment=str(_first_value(item, ("comment", "remark", "source"), "")),
            magic=_to_int(_first_value(item, ("magic", "magicNumber")), 0),
            time=_to_float(_first_value(item, ("time", "createdAt", "openTime")), time.time()),
            raw=item,
        )

    def get_positions(self, paper_mode: Optional[bool] = None) -> List[BrokerPosition]:
        """Lấy vị thế theo mode; ``None`` giữ hành vi PAPER/REAL hiện tại."""
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return self._paper().get_positions()
        try:
            from core.dnse_ws import market_ws
            ws_connected = market_ws.is_connected()
            ws_events = market_ws.latest_position_events() if ws_connected else []
        except Exception:
            ws_connected = False
            ws_events = []
        if ws_events and self._positions_cache:
            merged = {str(pos.position_id or pos.ticket): pos for pos in self._positions_cache}
            for event in ws_events:
                key = str(_first_value(event, ("positionId", "id", "positionID"), ""))
                status = str(event.get("status", "OPEN") or "OPEN").upper()
                if status == "CLOSED" or _to_float(event.get("openQuantity"), 0.0) <= 0:
                    merged.pop(key, None)
                elif key:
                    merged[key] = self._position_from_raw(event)
            self._positions_cache = list(merged.values())
            self._positions_cache_ts = time.time()
            return list(self._positions_cache)
        reconcile = float(getattr(config, "DNSE_WS_RECONCILE_SECONDS", 30.0) or 30.0)
        if ws_connected and self._positions_cache_ts and (time.time() - self._positions_cache_ts) < reconcile:
            return list(self._positions_cache)
        cache_ttl = float(getattr(config, "DNSE_POSITIONS_CACHE_TTL_SECONDS", 5.0) or 0.0)
        if cache_ttl > 0 and (time.time() - self._positions_cache_ts) < cache_ttl:
            return list(self._positions_cache)
        now = time.time()
        if now < self._positions_unavailable_until:
            self._positions_suppressed_errors += 1
            return list(self._positions_cache)
        auth_marker = (self.api_key, self.trading_token or "")
        if self._read_auth_blocked_marker == auth_marker:
            self._positions_suppressed_errors += 1
            return list(self._positions_cache)

        # Reset cờ "mute 403" theo NGÀY (đang mute vĩnh viễn cả session) — để ngày mới thấy lỗi lại.
        today = time.strftime("%Y-%m-%d")
        if getattr(self, "_mute_reset_date", None) != today:
            DNSEConnector._positions_403_muted = False
            DNSEConnector._balances_403_muted = False
            self._positions_error_logged = False
            self._balances_error_logged = False
            self._mute_reset_date = today

        query_targets: List[Tuple[str, str]] = []
        for market_type, account_no in (
            ("DERIVATIVE", self.derivative_account_no or self.account_no),
            ("STOCK", self.stock_account_no or self.account_no),
        ):
            if account_no and (market_type, account_no) not in query_targets:
                query_targets.append((market_type, account_no))

        positions: List[BrokerPosition] = []
        completed_targets = 0
        failure_status = 0
        failure_message = ""
        for market_type, account_no in query_targets:
            ok, data, status_code, message = self._request(
                "GET",
                f"/accounts/{account_no}/positions",
                params={"marketType": market_type},
            )
            if not ok:
                failure_status = int(status_code or 0)
                failure_message = str(message or data or "")
                if int(status_code or 0) == 401:
                    self._read_auth_blocked_marker = (
                        self.api_key,
                        self.trading_token or "",
                    )
                break
            completed_targets += 1
            payload = _unwrap_payload(data, ("positions",))
            if isinstance(payload, dict):
                payload = [payload]
            if isinstance(payload, list):
                positions.extend(self._position_from_raw(item) for item in payload if isinstance(item, dict))

        if completed_targets != len(query_targets):
            self._positions_failure_streak += 1
            base_backoff = max(
                30.0,
                float(getattr(config, "DNSE_BROKER_BACKOFF_SECONDS", 60.0) or 60.0),
            )
            backoff = min(
                300.0,
                base_backoff * (2 ** min(3, self._positions_failure_streak - 1)),
            )
            self._positions_unavailable_until = now + backoff
            if failure_status == 403:
                DNSEConnector._positions_403_muted = True
            if not self._positions_outage_logged:
                # Network exceptions were already logged once by _request().
                if failure_status != 0:
                    logger.warning(
                        "DNSE positions temporarily unavailable [%s]: %s. "
                        "Keep full cache and retry in %.0fs.",
                        failure_status,
                        failure_message,
                        backoff,
                    )
                self._positions_outage_logged = True
                self._positions_error_logged = True
            # Không bao giờ ghi đè cache bằng kết quả một phần. Nếu DERIVATIVE lỗi
            # nhưng STOCK thành công (hoặc ngược lại), vị thế cũ phải được giữ nguyên.
            return list(self._positions_cache)
        if self._positions_outage_logged or self._positions_failure_streak:
            logger.info(
                "DNSE positions recovered (%d internal checks suppressed).",
                self._positions_suppressed_errors,
            )
        self._positions_unavailable_until = 0.0
        self._positions_failure_streak = 0
        self._positions_outage_logged = False
        self._positions_suppressed_errors = 0
        self._positions_error_logged = False
        self._read_auth_blocked_marker = None
        try:
            settlement_ledger.enrich_positions(self.account_no, positions)
        except Exception as exc:
            logger.warning("Settlement ledger enrich failed: %s", exc)
        self._positions_cache = list(positions)
        self._positions_cache_ts = time.time()
        return positions

    def get_all_open_positions(
        self, paper_mode: Optional[bool] = None
    ) -> List[BrokerPosition]:
        return self.get_positions(paper_mode=paper_mode)

    def _order_result(self, ok: bool, data: Any, status_code: int, message: str = "") -> BrokerOrderResult:
        payload = _unwrap_payload(data) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        order_id = str(_first_value(payload, ("orderId", "id", "orderID"), ""))
        position_id = str(_first_value(payload, ("positionId", "positionID"), ""))
        status = str(_first_value(payload, ("status", "orderStatus"), ""))
        result_message = str(_first_value(payload, ("message", "description", "error"), message or ""))
        return BrokerOrderResult(
            ok=ok,
            order_id=order_id,
            position_id=position_id,
            status=status,
            message=result_message,
            error="" if ok else (message or result_message),
            status_code=status_code,
            raw=data if isinstance(data, dict) else {"raw": data},
        )

    def _normalize_side(self, order_type: Any) -> str:
        text = str(order_type).upper()
        return "NB" if text in ("0", "BUY", "LONG", "NB") else "NS"

    def _normalize_stock_order(self, symbol, order_type, volume, price, order_kind):
        """Chuẩn hoá khối lượng + kiểm luật cổ phiếu, dùng chung cho paper lẫn real.

        Trả (quantity:int, err:BrokerOrderResult|None).
        - Mọi mã: làm tròn số nguyên, tối thiểu 1.
        - Cổ phiếu (CKCS): lô chẵn 100 (làm tròn xuống, dưới 1 lô -> STOCK_ODD_LOT);
          lệnh LO (giá>0) phải nằm trong biên trần/sàn (-> PRICE_OUT_OF_BAND).
        - Phái sinh: không áp lô 100/biên ở đây.
        """
        quantity = max(1, int(round(float(volume or 0))))
        symbol_key = str(symbol or "").strip().upper()
        if not settlement.is_cash_stock(symbol_key):
            return quantity, None

        round_lot = int(getattr(config, "STOCK_ROUND_LOT", 100) or 100)
        quantity = stock_rules.round_lot_down(quantity, round_lot)
        if quantity < round_lot:
            return quantity, BrokerOrderResult(
                ok=False,
                error="STOCK_ODD_LOT",
                message=f"Cổ phiếu {symbol_key}: khối lượng phải là bội số {round_lot} (tối thiểu {round_lot} CP).",
            )

        order_type_name = order_kind or ("LO" if float(price or 0) > 0 else "MOK")
        if order_type_name == "LO" and float(price or 0) > 0:
            tick = self.get_tick(symbol_key)
            if tick is not None:
                band_pct = stock_rules.band_pct_for(symbol_key)
                fl, ce = stock_rules.resolve_band(tick.reference, tick.ceiling, tick.floor, band_pct)
                if not stock_rules.price_in_band(price, fl, ce):
                    return quantity, BrokerOrderResult(
                        ok=False,
                        error="PRICE_OUT_OF_BAND",
                        message=f"Cổ phiếu {symbol_key}: giá {float(price):g} ngoài biên [sàn {fl:g} .. trần {ce:g}].",
                    )
        return quantity, None

    def send_order(
        self,
        symbol: str,
        order_type: Any,
        volume: float,
        price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        magic: int = 0,
        order_kind: Optional[str] = None,
        paper_mode: Optional[bool] = None,
        allow_when_auto_disabled: bool = False,
    ) -> BrokerOrderResult:
        # Chuẩn hoá khối lượng + luật CK (lô 100 + biên trần/sàn) — áp cho CẢ paper lẫn real
        # để test paper sát thực tế. Phái sinh chỉ làm tròn số nguyên.
        quantity, norm_err = self._normalize_stock_order(symbol, order_type, volume, price, order_kind)
        if norm_err is not None:
            return norm_err
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return self._paper().place_order(
                symbol,
                order_type,
                quantity,
                price=price,
                sl=sl,
                tp=tp,
                magic=magic,
                comment=comment,
            )
        market_state = self.get_market_data_state()
        order_type_name = order_kind or ("LO" if float(price or 0) > 0 else "MOK")
        if market_state in {"MARKET DATA DOWN", "MARKET_DATA_DOWN"}:
            is_bot_order = "[BOT]" in str(comment or "").upper()
            if is_bot_order or order_type_name != "LO" or float(price or 0.0) <= 0:
                return BrokerOrderResult(
                    ok=False,
                    error="MARKET_DATA_DOWN",
                    message=(
                        "Mất dữ liệu giá: BOT/lệnh thị trường bị chặn. "
                        "Chỉ cho phép lệnh LO nhập giá thủ công sau xác nhận."
                    ),
                )
        if (
            not allow_when_auto_disabled
            and not bool(getattr(config, "AUTO_TRADE_ENABLED", False))
        ):
            return BrokerOrderResult(
                ok=False,
                error="AUTO_TRADE_DISABLED",
                message="AUTO_TRADE_ENABLED is False; refusing to send a new live DNSE order.",
            )
        side = self._normalize_side(order_type)
        market_type = self.market_type_for_symbol(symbol)
        account_no = self.account_no_for_symbol(symbol)
        symbol_key = str(symbol or "").strip().upper()
        is_stock = settlement.is_cash_stock(symbol_key)
        if side == "NS" and is_stock:
            enriched = settlement_ledger.enrich_positions(self.account_no, self.get_positions())
            available = settlement.available_to_sell(enriched, symbol_key)
            pending = settlement.pending_to_settle(enriched, symbol_key)
            if available < quantity:
                msg = (
                    f"Cổ phiếu {symbol_key} chỉ có {available:g} đã về, cần bán {quantity:g}; "
                    f"đang chờ về {pending:g}. Không bán khống CKCS."
                )
                return BrokerOrderResult(ok=False, error="STOCK_NOT_SETTLED_T2", message=msg)
        symbol = self.resolve_symbol(symbol)  # gửi mã hợp đồng thật cho lệnh phái sinh
        payload = {
            "accountNo": account_no,
            "symbol": str(symbol).upper(),
            "side": side,
            "quantity": quantity,
            "orderType": order_type_name,
            "price": float(price or 0),
        }
        request_id = uuid.uuid4().hex[:12].upper()
        request_tag = f"RID:{request_id}"
        payload["remark"] = f"{comment} {request_tag}".strip()
        if magic:
            payload["magicNumber"] = int(magic)
        if sl:
            payload["stopLoss"] = float(sl)
        if tp:
            payload["takeProfit"] = float(tp)

        ok, data, status_code, message = self._request(
            "POST",
            "/accounts/orders",
            params={"marketType": market_type, "orderCategory": self.order_category},
            json_payload=payload,
            require_trading_token=True,
        )
        result = self._order_result(ok, data, status_code, message)
        if not ok and status_code == 0:
            matched = self._reconcile_order_request(
                request_tag,
                account_no=account_no,
                market_type=market_type,
            )
            if matched:
                result = self._order_result(True, matched, 200, "")
                result.message = result.message or "Đã đối soát thấy lệnh tại DNSE sau khi kết nối gián đoạn."
            else:
                result.status = "UNKNOWN"
                result.error = "ORDER_STATUS_UNKNOWN"
                result.message = (
                    f"TRẠNG THÁI LỆNH CHƯA XÁC ĐỊNH ({request_tag}). "
                    "Không tự gửi lại; cần làm mới danh sách lệnh DNSE trước."
                )
                result.raw = {
                    "request_id": request_id,
                    "request_tag": request_tag,
                    "request_payload": payload,
                    "transport_error": message,
                }
        if not result.ok:
            logger.error("DNSE order failed: %s", result.error or result.raw)
            try:
                from telegram_notify.system_alerts import notify_order_failure

                notify_order_failure(
                    result,
                    symbol=symbol_key,
                    side="BUY" if side == "NB" else "SELL",
                )
            except Exception:
                pass
        elif side == "NB" and is_stock:
            ticket = result.position_id or result.order_id or result.ticket
            buy_date = datetime.now()
            try:
                settle = settlement.settle_date_str(buy_date, self.get_working_dates())
                settlement_ledger.record_buy(self.account_no, ticket, symbol_key, quantity, buy_date, settle)
            except Exception as exc:
                logger.warning("Settlement ledger record failed for %s %s: %s", symbol_key, ticket, exc)
        return result

    def _reconcile_order_request(self, request_tag: str, *, account_no: str, market_type: str):
        """Đối soát POST timeout bằng RID trong remark; không bao giờ gửi lại POST."""
        try:
            ok, data, _status, _message = self._request(
                "GET",
                f"/accounts/{account_no}/orders",
                params={"marketType": market_type},
                timeout=5.0,
            )
            if not ok:
                return None
            rows = _unwrap_payload(data, ("orders",))
            if isinstance(rows, dict):
                rows = [rows]
            for item in rows if isinstance(rows, list) else []:
                if request_tag in str(_first_value(item, ("remark", "comment", "note"), "")):
                    return item
        except Exception:
            pass
        return None

    def place_order(
        self,
        symbol,
        order_type,
        lot,
        sl,
        tp,
        magic=0,
        comment="",
        order_kind=None,
        price=0.0,
        paper_mode: Optional[bool] = None,
    ) -> BrokerOrderResult:
        # order_kind: None -> tự LO/MOK; "ATO"/"ATC" -> lệnh phiên định kỳ mở/đóng cửa.
        return self.send_order(
            symbol,
            order_type,
            lot,
            price=price,
            sl=sl,
            tp=tp,
            comment=comment,
            magic=magic,
            order_kind=order_kind,
            paper_mode=paper_mode,
        )

    def place_close_limit_order(
        self,
        symbol,
        order_type,
        lot,
        price,
        *,
        comment="",
        paper_mode: Optional[bool] = None,
    ) -> BrokerOrderResult:
        """Place an opposite LO to close risk without requiring BOT to be armed."""
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return BrokerOrderResult(
                ok=False,
                error="PAPER_LIMIT_CLOSE_USES_TRIGGER",
                message="PAPER LIMIT close is executed by the local price trigger.",
            )
        return self.send_order(
            symbol,
            order_type,
            lot,
            price=float(price or 0.0),
            sl=0.0,
            tp=0.0,
            comment=comment or "[USER]_CLOSE_LIMIT",
            magic=0,
            order_kind=None,
            paper_mode=False,
            allow_when_auto_disabled=True,
        )

    def replace_order(self, order_id: str, *, price: Optional[float] = None, quantity: Optional[float] = None, account_no: Optional[str] = None, symbol: Optional[str] = None) -> BrokerOrderResult:
        if self._is_paper_mode():
            return BrokerOrderResult(ok=False, order_id=str(order_id), error="PAPER_REPLACE_UNSUPPORTED", message="Paper orders are filled immediately.")
        _account_no = account_no or (self.account_no_for_symbol(symbol) if symbol else self.account_no)
        _market_type = self.market_type_for_symbol(symbol) if symbol else self.market_type
        payload: Dict[str, Any] = {}
        if price is not None:
            payload["price"] = float(price)
        if quantity is not None:
            payload["quantity"] = max(1, int(round(float(quantity))))
        ok, data, status_code, message = self._request(
            "PUT",
            f"/accounts/{_account_no}/orders/{order_id}",
            params={"marketType": _market_type},
            json_payload=payload,
            require_trading_token=True,
        )
        return self._order_result(ok, data, status_code, message)

    def cancel_order(
        self,
        order_id: str,
        *,
        account_no: Optional[str] = None,
        symbol: Optional[str] = None,
        paper_mode: Optional[bool] = None,
    ) -> BrokerOrderResult:
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return BrokerOrderResult(ok=False, order_id=str(order_id), error="PAPER_CANCEL_UNSUPPORTED", message="Paper orders are filled immediately.")
        _account_no = account_no or (self.account_no_for_symbol(symbol) if symbol else self.account_no)
        _market_type = self.market_type_for_symbol(symbol) if symbol else self.market_type
        ok, data, status_code, message = self._request(
            "DELETE",
            f"/accounts/{_account_no}/orders/{order_id}",
            params={"marketType": _market_type},
            require_trading_token=True,
        )
        return self._order_result(ok, data, status_code, message)

    def set_position_pnl_config(self, position_id: str, sl: float = 0.0, tp: float = 0.0) -> BrokerOrderResult:
        payload = {}
        if sl:
            payload["stopLoss"] = float(sl)
        if tp:
            payload["takeProfit"] = float(tp)
        if not payload:
            return BrokerOrderResult(ok=True, position_id=str(position_id), message="No PnL config change requested.")
        ok, data, status_code, message = self._request(
            "POST",
            f"/accounts/positions/{position_id}/pnl-configs",
            json_payload=payload,
            require_trading_token=True,
        )
        return self._order_result(ok, data, status_code, message)

    def modify_position(
        self,
        position_or_ticket: Any,
        sl: float = 0.0,
        tp: float = 0.0,
        *,
        paper_mode: Optional[bool] = None,
    ) -> BrokerOrderResult:
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return self._paper().modify_position(position_or_ticket, sl, tp)
        position_id = getattr(position_or_ticket, "position_id", None) or getattr(position_or_ticket, "ticket", None) or position_or_ticket
        result = self.set_position_pnl_config(str(position_id), sl, tp)
        if not result.ok:
            logger.warning("DNSE PnL config update failed/not supported for %s: %s", position_id, result.error)
        return result

    def close_position(
        self,
        position_or_ticket: Any,
        comment: str = "",
        *,
        paper_mode: Optional[bool] = None,
    ) -> BrokerOrderResult:
        use_paper = self._is_paper_mode() if paper_mode is None else bool(paper_mode)
        if use_paper:
            return self._paper().close_position(position_or_ticket, comment)
        position_id = getattr(position_or_ticket, "position_id", None) or getattr(position_or_ticket, "ticket", None) or position_or_ticket
        pos_obj = position_or_ticket if hasattr(position_or_ticket, "symbol") else None
        if pos_obj is None and isinstance(position_or_ticket, (str, int)):
            pos_key = str(position_id or "")
            pos_obj = next(
                (
                    p
                    for p in self.get_positions()
                    if str(getattr(p, "position_id", "") or getattr(p, "ticket", "") or "") == pos_key
                    or str(getattr(p, "ticket", "") or "") == pos_key
                ),
                None,
            )
        if pos_obj is not None and settlement.is_cash_stock(getattr(pos_obj, "symbol", "")):
            enriched = settlement_ledger.enrich_positions(self.account_no, [pos_obj])
            settle = (enriched[0] or {}).get("settle_date") if enriched else ""
            if settle and not settlement.is_settled(settle):
                msg = f"Cổ phiếu {getattr(pos_obj, 'symbol', '')} chưa về T+2 (về {str(settle)[:10]}), chưa đóng được."
                return BrokerOrderResult(ok=False, position_id=str(position_id), error="STOCK_NOT_SETTLED_T2", message=msg)
        ok, data, status_code, message = self._request(
            "POST",
            f"/accounts/positions/{position_id}/close",
            json_payload={"comment": comment} if comment else None,
            require_trading_token=True,
        )
        result = self._order_result(ok, data, status_code, message)
        if not result.position_id:
            result.position_id = str(position_id)
        if result.ok and pos_obj is not None and settlement.is_cash_stock(getattr(pos_obj, "symbol", "")):
            settlement_ledger.drop(self.account_no, position_id)
        return result

    def close_all_positions(self) -> List[BrokerOrderResult]:
        return [self.close_position(pos) for pos in self.get_positions()]

    def get_ohlc(self, symbol: str, resolution: str, from_ts: int, to_ts: int) -> Optional[Dict[str, Any]]:
        market_type = self.market_type_for_symbol(symbol)
        # LƯU Ý: endpoint OHLC nhận symbolType alias (VN30F1M) cho phái sinh — KHÔNG resolve
        # sang mã hợp đồng thật (41I1G6000 trả về 0 nến). Chỉ trades/quotes mới cần mã thật.
        ok, data, status_code, message = self._request(
            "GET",
            "/price/ohlc",
            params={
                "symbol": str(symbol).upper(),
                "type": market_type,
                "resolution": resolution,
                "from": str(int(from_ts)),
                "to": str(int(to_ts)),
            },
        )
        if not ok:
            # DataEngine chịu trách nhiệm gộp/throttle cảnh báo theo mã + khung.
            # Không log lại tại connector vì một lượt quét nhiều mã sẽ nhân đôi log.
            return None
        return data if isinstance(data, dict) else None

    def get_latest_trade(self, symbol: str, board_id: str = "G1") -> Optional[Dict[str, Any]]:
        symbol = self.resolve_symbol(symbol)
        ok, data, _status_code, _message = self._request(
            "GET",
            f"/price/{str(symbol).upper()}/trades/latest",
            params={"boardId": board_id},
        )
        if not ok:
            return None
        payload = _unwrap_payload(data, ("trades",))
        if isinstance(payload, list):
            return payload[0] if payload else None
        return payload if isinstance(payload, dict) else None

    def get_latest_quote(self, symbol: str, board_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        symbol = self.resolve_symbol(symbol)
        params = {"boardId": board_id} if board_id else {}
        ok, data, _status_code, _message = self._request(
            "GET",
            f"/price/{str(symbol).upper()}/quotes/latest",
            params=params,
        )
        if not ok:
            return None
        payload = _unwrap_payload(data, ("quotes",))
        if isinstance(payload, list):
            return payload[0] if payload else None
        return payload if isinstance(payload, dict) else None

    def get_working_dates(self) -> List[str]:
        """Danh sách ngày làm việc (YYYY-MM-DD) từ DNSE — cache 1 ngày — để tính T+2."""
        now = time.time()
        if self._working_dates and (now - self._working_dates_ts) < 86400.0:
            return self._working_dates
        ok, data, _s, _m = self._request("GET", "/market/working-dates")
        if ok and isinstance(data, dict):
            dates = [str(d)[:10] for d in (data.get("workingDates") or []) if d]
            if dates:
                self._working_dates = sorted(dates)
                self._working_dates_ts = now
        return self._working_dates

    def get_tick(self, symbol: str) -> Optional[BrokerTick]:
        sym_key = str(symbol).upper()
        if callable(self._market_tick_provider):
            try:
                raw = self._market_tick_provider(sym_key)
            except Exception:
                raw = None
            if isinstance(raw, BrokerTick):
                return raw
            if not isinstance(raw, dict):
                return None
            last = _to_float(_first_value(raw, ("last", "current_price", "price")), 0.0)
            bid = _to_float(raw.get("bid"), last)
            ask = _to_float(raw.get("ask"), last)
            if not (last or bid or ask):
                return None
            return BrokerTick(
                symbol=sym_key,
                bid=bid or last,
                ask=ask or last,
                last=last or bid or ask,
                high=_to_float(_first_value(raw, ("high", "day_high")), 0.0),
                low=_to_float(_first_value(raw, ("low", "day_low")), 0.0),
                open=_to_float(_first_value(raw, ("open", "day_open")), 0.0),
                spread=_to_float(raw.get("spread"), max(0.0, (ask or last) - (bid or last))),
                ceiling=_to_float(raw.get("ceiling"), 0.0),
                floor=_to_float(raw.get("floor"), 0.0),
                reference=_to_float(raw.get("reference"), 0.0),
                timestamp=_to_float(_first_value(raw, ("timestamp", "tick_timestamp")), time.time()),
                raw=dict(raw),
            )
        cache_ttl = float(getattr(config, "DNSE_TICK_CACHE_TTL_SECONDS", 2.0) or 0.0)
        if cache_ttl > 0 and sym_key in self._tick_cache and (time.time() - self._tick_cache_ts.get(sym_key, 0.0)) < cache_ttl:
            return self._tick_cache[sym_key]
        trade = self.get_latest_trade(symbol) or {}
        quote = self.get_latest_quote(symbol) or {}
        bid = ask = 0.0
        bids = quote.get("bid") or quote.get("bids") or []
        offers = quote.get("offer") or quote.get("ask") or quote.get("offers") or []
        if bids:
            bid = _to_float((bids[0] or {}).get("price") if isinstance(bids[0], dict) else bids[0])
        if offers:
            ask = _to_float((offers[0] or {}).get("price") if isinstance(offers[0], dict) else offers[0])
        last = _to_float(_first_value(trade, ("matchPrice", "price", "lastPrice")), 0.0)
        if not bid:
            bid = last
        if not ask:
            ask = last
        if not (last or bid or ask):
            return None
        # Giá trần/sàn/tham chiếu: DNSE có thể nằm ở trade hoặc quote -> gộp tìm.
        merged = {**(quote if isinstance(quote, dict) else {}), **(trade if isinstance(trade, dict) else {})}
        ceiling = _to_float(_first_value(merged, ("ceilingPrice", "ceiling", "maxPrice")), 0.0)
        floor = _to_float(_first_value(merged, ("floorPrice", "floor", "minPrice")), 0.0)
        reference = _to_float(_first_value(merged, ("referencePrice", "refPrice", "basicPrice", "priorClosePrice")), 0.0)
        tick = BrokerTick(
            symbol=sym_key,
            bid=bid,
            ask=ask,
            last=last or bid or ask,
            high=_to_float(_first_value(trade, ("highestPrice", "high")), 0.0),
            low=_to_float(_first_value(trade, ("lowestPrice", "low")), 0.0),
            open=_to_float(_first_value(trade, ("openPrice", "open")), 0.0),
            spread=round((ask - bid), 4) if ask and bid else 0.0,
            ceiling=ceiling,
            floor=floor,
            reference=reference,
            raw={"trade": trade, "quote": quote},
        )
        self._tick_cache[sym_key] = tick
        self._tick_cache_ts[sym_key] = time.time()
        return tick

    def get_symbol_info(self, symbol: str, poll_tick: bool = True) -> BrokerSymbolInfo:
        market_type = self.market_type_for_symbol(symbol)
        # [FIX 429] poll_tick=False: chỉ cần thông số hợp đồng (tĩnh theo loại thị trường),
        # không gọi /trades|quotes/latest -> tránh đập endpoint phái sinh nóng từ UI mỗi vòng.
        tick = self.get_tick(symbol) if poll_tick else None
        is_derivative = market_type == "DERIVATIVE"
        point_value = (
            float(getattr(config, "DNSE_POINT_VALUE", DNSE_POINT_VALUE) or DNSE_POINT_VALUE)
            if is_derivative
            else float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0)
        )
        # Cổ phiếu: lô chẵn 100 -> auto-lot tự làm tròn bội 100. Phái sinh giữ step=1.
        round_lot = float(getattr(config, "STOCK_ROUND_LOT", 100) or 100)
        volume_min = 1.0 if is_derivative else round_lot
        volume_step = float(getattr(config, "LOT_STEP", 1.0) or 1.0) if is_derivative else round_lot
        return BrokerSymbolInfo(
            symbol=str(symbol).upper(),
            point=float(getattr(config, "DNSE_PRICE_POINT", 0.1)),
            trade_contract_size=point_value,
            volume_min=volume_min,
            volume_max=float(getattr(config, "MAX_LOT_SIZE", 200.0) or 200.0) if is_derivative else 1000000.0,
            volume_step=volume_step,
            spread=float(tick.spread if tick else 0.0),
            market_type=market_type,
            quantity_label="Hợp đồng" if is_derivative else "Cổ phiếu",
            quantity_unit="HĐ" if is_derivative else "CP",
            raw=tick.raw if tick else {},
        )

    def calculate_profit(self, symbol: str, side: str, volume: float, entry_price: float, exit_price: float) -> float:
        direction = str(side).upper()
        diff = float(exit_price) - float(entry_price)
        if direction in ("SHORT", "SELL", "NS", "1"):
            diff = -diff
        point_value = float(self.get_symbol_info(symbol).trade_contract_size or 1.0)
        return diff * float(volume) * point_value

    def calculate_lot_size(
        self,
        symbol: str,
        risk_value: float,
        sl_price: float,
        order_type: Any,
        strict_fee_per_lot: float = 0.0,
        entry_price: Optional[float] = None,
    ) -> Tuple[Optional[float], float]:
        if entry_price is None:
            tick = self.get_tick(symbol)
            if tick:
                entry_price = tick.ask if self._normalize_side(order_type) == "NB" else tick.bid
            else:
                entry_price = 0.0
        distance = abs(float(entry_price or 0.0) - float(sl_price or 0.0))
        if distance <= 0:
            return None, sl_price
        # Chỉ cần thông số hợp đồng; giá vào đã có thì không được phát sinh thêm
        # lượt lấy tick/quote từ Preview hoặc các màn hình chỉ đọc.
        info = self.get_symbol_info(symbol, poll_tick=False)
        point_value = float(info.trade_contract_size or 1.0)
        risk_per_contract = (distance * point_value) + float(strict_fee_per_lot or 0.0)
        if risk_per_contract <= 0:
            return None, sl_price
        raw_qty = float(risk_value) / risk_per_contract
        step = max(float(info.volume_step or 1.0), 1.0)
        qty = round(raw_qty / step) * step
        qty = max(float(info.volume_min), min(float(qty), float(info.volume_max)))
        return qty, float(sl_price)
