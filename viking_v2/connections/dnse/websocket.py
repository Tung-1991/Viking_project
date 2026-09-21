from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Any, Iterable

from ...config import WS_URL


logger = logging.getLogger("VIKING_V2.market_ws")

try:
    import websocket
except Exception:  # pragma: no cover
    websocket = None

try:
    import msgpack
except Exception:  # pragma: no cover
    msgpack = None


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _best(levels: Any) -> float | None:
    if isinstance(levels, (int, float)):
        return float(levels)
    if isinstance(levels, list) and levels:
        return _best(levels[0])
    if isinstance(levels, dict):
        return _float(levels.get("price"))
    return None


class DNSEMarketWS:
    """Stock market-data stream ported from the proven legacy transport."""

    def __init__(self, api_key: str = "", api_secret: str = "", url: str = WS_URL):
        self.api_key = api_key or os.getenv("DNSE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("DNSE_API_SECRET", "")
        self.url = f"{url.rstrip('/')}/v1/stream?encoding=json"
        self._symbols: set[str] = set()
        self._subscribed: set[str] = set()
        self._ticks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._app: Any = None
        self._running = False
        self._stop_event = threading.Event()
        self._connected = False
        self._authenticated = False
        self._generation = 0
        self._last_message_at = 0.0
        self._last_pong_at = 0.0
        self._last_error = ""
        self._reconnects = 0
        self._consecutive_failures = 0

    def available(self) -> bool:
        return bool(websocket is not None and self.api_key and self.api_secret)

    def is_connected(self) -> bool:
        return bool(self._connected and self._authenticated)

    def set_symbols(self, symbols: Iterable[str]) -> None:
        desired = {str(item).strip().upper() for item in symbols if str(item).strip()}
        with self._lock:
            removed = self._symbols - desired
            added = desired - self._symbols
            self._symbols = desired
        if self.is_connected():
            if removed:
                self._send_channels("unsubscribe", removed)
            if added:
                self._send_channels("subscribe", added)

    def latest_tick(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._ticks.get(str(symbol).upper())
            return dict(value) if value else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self.available(),
                "running": self._running,
                "connected": self.is_connected(),
                "authenticated": self._authenticated,
                "symbols": sorted(self._symbols),
                "subscribed": sorted(self._subscribed),
                "last_message_at": self._last_message_at,
                "last_pong_at": self._last_pong_at,
                "reconnects": self._reconnects,
                "last_error": self._last_error,
            }

    def start(self) -> bool:
        if not self.available():
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="viking-dnse-ws", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        app = self._app
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        self._connected = False
        self._authenticated = False
        with self._lock:
            self._subscribed.clear()

    def _auth_payload(self) -> dict[str, Any]:
        timestamp = int(time.time())
        nonce = str(int(time.time() * 1_000_000))
        message = f"{self.api_key}:{timestamp}:{nonce}"
        signature = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"action": "auth", "api_key": self.api_key, "signature": signature, "timestamp": timestamp, "nonce": nonce}

    @staticmethod
    def _channels() -> list[str]:
        ticks = [f"tick.{board}.json" for board in ("G1", "G3", "G4", "G7", "T1", "T2", "T3", "T4", "T6")]
        quotes = [f"top_price.{board}.json" for board in ("G1", "G2", "G3", "G4", "G5", "G6", "G7")]
        expected = [f"expected_price.{board}.json" for board in ("G1", "G3", "G4", "G7")]
        return ticks + quotes + expected

    def _send_channels(self, action: str, symbols: Iterable[str]) -> None:
        values = sorted(set(symbols))
        if not self._app or not values:
            return
        self._app.send(json.dumps({"action": action, "channels": [{"name": channel, "symbols": values} for channel in self._channels()]}))
        with self._lock:
            if action == "subscribe":
                self._subscribed.update(values)
            else:
                self._subscribed.difference_update(values)

    def _on_open(self, app: Any) -> None:
        self._connected = True
        self._authenticated = False
        self._generation += 1
        app.send(json.dumps(self._auth_payload()))

    def _decode(self, raw: Any) -> dict[str, Any] | None:
        try:
            if isinstance(raw, (bytes, bytearray)):
                return msgpack.unpackb(raw, raw=False) if msgpack is not None else None
            if isinstance(raw, str):
                return json.loads(raw)
            return raw if isinstance(raw, dict) else None
        except Exception:
            return None

    def _on_message(self, app: Any, raw: Any) -> None:
        payload = self._decode(raw)
        if not isinstance(payload, dict):
            return
        self._last_message_at = time.time()
        action = str(payload.get("action", payload.get("a", ""))).lower()
        if action == "auth_success":
            self._authenticated = True
            self._consecutive_failures = 0
            self._last_error = ""
            with self._lock:
                symbols = set(self._symbols)
            self._send_channels("subscribe", symbols)
            generation = self._generation
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat, args=(generation,), name=f"viking-ws-heartbeat-{generation}", daemon=True
            )
            self._heartbeat_thread.start()
            return
        if action == "ping":
            app.send(json.dumps({"action": "pong"}))
            self._last_pong_at = time.time()
            return
        if action == "pong":
            self._last_pong_at = time.time()
            return
        if action in {"auth_error", "error"}:
            self._last_error = str(payload.get("message") or payload)
            return
        data = payload.get("data", payload)
        if isinstance(data, dict):
            self._ingest(data)

    def _ingest(self, data: dict[str, Any]) -> None:
        symbol = str(data.get("symbol", data.get("Symbol", ""))).upper()
        if not symbol:
            return
        now = time.time()
        with self._lock:
            tick = self._ticks.setdefault(symbol, {"symbol": symbol})
            last = _float(data.get("matchPrice"), None)
            expected = _float(data.get("expectedPrice"), None)
            if last is not None and last > 0:
                tick["price"] = last
                tick["high"] = _float(data.get("highestPrice"), tick.get("high", 0.0))
                tick["low"] = _float(data.get("lowestPrice"), tick.get("low", 0.0))
                tick["open"] = _float(data.get("openPrice"), tick.get("open", 0.0))
                tick["reference"] = _float(data.get("referencePrice", data.get("basicPrice")), tick.get("reference", 0.0))
                tick["volume"] = int(_float(data.get("totalVolumeTraded"), tick.get("volume", 0)) or 0)
            if expected is not None and expected > 0:
                tick["expected_price"] = expected
                tick["price"] = expected
            bid = _best(data.get("bid"))
            ask = _best(data.get("offer", data.get("ask")))
            if bid is not None:
                tick["bid"] = bid
            if ask is not None:
                tick["ask"] = ask
            if "price" in tick:
                tick.setdefault("bid", tick["price"])
                tick.setdefault("ask", tick["price"])
            tick["timestamp"] = now
            tick["source"] = "WS"

    def _heartbeat(self, generation: int) -> None:
        while self._running and self._connected and self._generation == generation:
            time.sleep(25.0)
            if not self._running or not self._connected or self._generation != generation:
                break
            try:
                self._app.send(json.dumps({"action": "ping"}))
            except Exception as exc:
                self._last_error = str(exc)
                break

    def _on_error(self, _app: Any, error: Any) -> None:
        self._last_error = str(error or "")

    def _on_close(self, _app: Any, _code: Any, _message: Any) -> None:
        self._connected = False
        self._authenticated = False

    def _run(self) -> None:
        while self._running:
            try:
                self._app = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                # Default TLS certificate verification remains enabled.
                self._app.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as exc:
                self._last_error = str(exc)
            self._connected = False
            self._authenticated = False
            with self._lock:
                self._subscribed.clear()
            if self._running:
                self._reconnects += 1
                self._consecutive_failures += 1
                delay = min(30.0, float(2 ** min(self._consecutive_failures, 5)))
                self._stop_event.wait(delay)
