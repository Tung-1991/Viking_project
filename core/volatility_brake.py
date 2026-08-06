# -*- coding: utf-8 -*-
"""Theo dõi biến động giá, độc lập với indicator và không gọi thêm API."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
import time
from typing import Any, Deque, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import config


DEFAULT_SETTINGS = {
    "VOLATILITY_BRAKE_ENABLED": False,
    "VOLATILITY_BRAKE_SYMBOLS": ["VN30F1M"],
    "VOLATILITY_BRAKE_ACTION": "ALERT_ONLY",
    "VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES": 30.0,
    "VOLATILITY_BRAKE_TELEGRAM_ENABLED": True,
    "VOLATILITY_BRAKE_WINDOW_SECONDS": 60.0,
    "VOLATILITY_BRAKE_STOCK_PCT": 1.5,
    "VOLATILITY_BRAKE_DERIVATIVE_POINTS": 5.0,
    "VOLATILITY_BRAKE_CONFIRMATIONS": 2,
    # Nhịp đi xa trong phiên: bắt được xu hướng kéo dài mà cửa sổ 60 giây bỏ lỡ.
    "VOLATILITY_BRAKE_SESSION_ENABLED": True,
    "VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS": 20.0,
    "VOLATILITY_BRAKE_SESSION_STOCK_PCT": 3.0,
}

VALID_ACTIONS = {"ALERT_ONLY", "BLOCK_NEW_EXPOSURE", "CLOSE_ALL"}


def settings_from_safeguard(safeguard: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = safeguard if isinstance(safeguard, dict) else {}
    result = dict(DEFAULT_SETTINGS)
    result.update({key: source[key] for key in DEFAULT_SETTINGS if key in source})

    # 240 phút là mặc định cũ. Chỉ migrate đúng giá trị mặc định cũ;
    # các giá trị người dùng tự chỉnh được giữ nguyên.
    try:
        is_legacy_cooldown = (
            float(result.get("VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES", 30.0) or 0.0)
            == 240.0
        )
    except (TypeError, ValueError):
        is_legacy_cooldown = False
    if is_legacy_cooldown:
        result["VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES"] = 30.0

    result["VOLATILITY_BRAKE_ENABLED"] = bool(result["VOLATILITY_BRAKE_ENABLED"])
    symbols = result.get("VOLATILITY_BRAKE_SYMBOLS", [])
    if isinstance(symbols, str):
        symbols = symbols.replace(";", ",").split(",")
    result["VOLATILITY_BRAKE_SYMBOLS"] = list(
        dict.fromkeys(
            str(symbol or "").strip().upper()
            for symbol in (symbols or [])
            if str(symbol or "").strip()
        )
    )
    action = str(result.get("VOLATILITY_BRAKE_ACTION") or "ALERT_ONLY").strip().upper()
    result["VOLATILITY_BRAKE_ACTION"] = action if action in VALID_ACTIONS else "ALERT_ONLY"
    result["VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES"] = max(
        0.0, float(result["VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES"] or 0.0)
    )
    result["VOLATILITY_BRAKE_TELEGRAM_ENABLED"] = bool(
        result["VOLATILITY_BRAKE_TELEGRAM_ENABLED"]
    )
    result["VOLATILITY_BRAKE_WINDOW_SECONDS"] = max(
        5.0, float(result["VOLATILITY_BRAKE_WINDOW_SECONDS"] or 60.0)
    )
    result["VOLATILITY_BRAKE_STOCK_PCT"] = max(
        0.01, float(result["VOLATILITY_BRAKE_STOCK_PCT"] or 1.5)
    )
    result["VOLATILITY_BRAKE_DERIVATIVE_POINTS"] = max(
        0.01, float(result["VOLATILITY_BRAKE_DERIVATIVE_POINTS"] or 5.0)
    )
    result["VOLATILITY_BRAKE_CONFIRMATIONS"] = max(
        1, int(result["VOLATILITY_BRAKE_CONFIRMATIONS"] or 2)
    )
    result["VOLATILITY_BRAKE_SESSION_ENABLED"] = bool(
        result["VOLATILITY_BRAKE_SESSION_ENABLED"]
    )
    result["VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS"] = max(
        0.01,
        float(result["VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS"] or 20.0),
    )
    result["VOLATILITY_BRAKE_SESSION_STOCK_PCT"] = max(
        0.01,
        float(result["VOLATILITY_BRAKE_SESSION_STOCK_PCT"] or 3.0),
    )
    return result


def is_derivative_symbol(symbol: str) -> bool:
    symbol = str(symbol or "").strip().upper()
    configured = {
        str(item or "").strip().upper()
        for item in (getattr(config, "CKPS_SYMBOLS", []) or [])
    }
    return symbol.startswith("VN30F") or symbol in configured


class VolatilityBrakeDetector:
    """Phát hiện cả cú sốc ngắn và nhịp đi xa tích lũy trong phiên."""

    def __init__(self):
        self._history: Dict[str, Deque[Tuple[float, float]]] = defaultdict(deque)
        self._confirmations: Dict[str, int] = defaultdict(int)
        self._session_anchors: Dict[str, Tuple[str, float, float]] = {}

    def clear(self) -> None:
        self._history.clear()
        self._confirmations.clear()
        self._session_anchors.clear()

    @staticmethod
    def _market_day(timestamp: float) -> str:
        return datetime.fromtimestamp(
            timestamp,
            tz=ZoneInfo("Asia/Ho_Chi_Minh"),
        ).date().isoformat()

    @staticmethod
    def _event(
        *,
        movement_type: str,
        symbol: str,
        reference_ts: float,
        reference_price: float,
        now: float,
        price: float,
        derivative: bool,
        threshold: float,
    ) -> Dict[str, Any]:
        change_points = price - reference_price
        change_pct = (
            change_points / reference_price * 100.0 if reference_price > 0 else 0.0
        )
        return {
            "event": "VOLATILITY_BRAKE",
            "movement_type": movement_type,
            "symbol": symbol,
            "direction": "UP" if change_points > 0 else "DOWN",
            "reference_price": reference_price,
            "current_price": price,
            "change_points": change_points,
            "change_pct": change_pct,
            "window_seconds": max(1.0, now - reference_ts),
            "threshold": threshold,
            "threshold_unit": "POINTS" if derivative else "PERCENT",
            "triggered_at": now,
        }

    def _session_event(
        self,
        *,
        cfg: Dict[str, Any],
        symbol: str,
        session_anchor: Tuple[str, float, float],
        market_day: str,
        now: float,
        price: float,
        derivative: bool,
    ) -> Optional[Dict[str, Any]]:
        if not cfg["VOLATILITY_BRAKE_SESSION_ENABLED"]:
            return None
        _, session_ts, session_price = session_anchor
        session_points = price - session_price
        session_pct = session_points / session_price * 100.0
        session_threshold = (
            cfg["VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS"]
            if derivative
            else cfg["VOLATILITY_BRAKE_SESSION_STOCK_PCT"]
        )
        session_magnitude = abs(session_points) if derivative else abs(session_pct)
        if session_magnitude < session_threshold:
            return None
        self._session_anchors[symbol] = (market_day, now, price)
        return self._event(
            movement_type="SESSION",
            symbol=symbol,
            reference_ts=session_ts,
            reference_price=session_price,
            now=now,
            price=price,
            derivative=derivative,
            threshold=session_threshold,
        )

    def observe(
        self,
        symbol: str,
        price: float,
        safeguard: Optional[Dict[str, Any]],
        *,
        timestamp: Optional[float] = None,
        freshness: str = "FRESH",
    ) -> Optional[Dict[str, Any]]:
        cfg = settings_from_safeguard(safeguard)
        symbol = str(symbol or "").strip().upper()
        now = float(timestamp if timestamp is not None else time.time())
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None

        allowed_symbols = set(cfg["VOLATILITY_BRAKE_SYMBOLS"])
        if (
            not cfg["VOLATILITY_BRAKE_ENABLED"]
            or not symbol
            or symbol not in allowed_symbols
            or price <= 0
        ):
            self._confirmations[symbol] = 0
            return None
        if str(freshness or "").strip().upper() in {
            "STALE",
            "OLD",
            "GIÁ CŨ",
            "GIA_CU",
        }:
            self._confirmations[symbol] = 0
            return None

        market_day = self._market_day(now)
        session_anchor = self._session_anchors.get(symbol)
        if not session_anchor or session_anchor[0] != market_day:
            session_anchor = (market_day, now, price)
            self._session_anchors[symbol] = session_anchor

        window = cfg["VOLATILITY_BRAKE_WINDOW_SECONDS"]
        history = self._history[symbol]
        if history and now <= history[-1][0]:
            return None
        history.append((now, price))
        while history and history[0][0] < now - window:
            history.popleft()
        derivative = is_derivative_symbol(symbol)
        if len(history) < 2:
            return self._session_event(
                cfg=cfg,
                symbol=symbol,
                session_anchor=session_anchor,
                market_day=market_day,
                now=now,
                price=price,
                derivative=derivative,
            )

        reference_ts, reference_price = history[0]
        if reference_price <= 0 or now <= reference_ts:
            return None

        fast_threshold = (
            cfg["VOLATILITY_BRAKE_DERIVATIVE_POINTS"]
            if derivative
            else cfg["VOLATILITY_BRAKE_STOCK_PCT"]
        )
        fast_points = price - reference_price
        fast_pct = fast_points / reference_price * 100.0
        fast_magnitude = abs(fast_points) if derivative else abs(fast_pct)
        if fast_magnitude >= fast_threshold:
            self._confirmations[symbol] += 1
            if self._confirmations[symbol] >= cfg["VOLATILITY_BRAKE_CONFIRMATIONS"]:
                self._confirmations[symbol] = 0
                history.clear()
                return self._event(
                    movement_type="FAST",
                    symbol=symbol,
                    reference_ts=reference_ts,
                    reference_price=reference_price,
                    now=now,
                    price=price,
                    derivative=derivative,
                    threshold=fast_threshold,
                )
        else:
            self._confirmations[symbol] = 0

        return self._session_event(
            cfg=cfg,
            symbol=symbol,
            session_anchor=session_anchor,
            market_day=market_day,
            now=now,
            price=price,
            derivative=derivative,
        )
