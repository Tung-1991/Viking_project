from __future__ import annotations

import logging
from datetime import datetime
import threading
from typing import Any

import requests

from ..models import TradeCycle


logger = logging.getLogger("VIKING_V2.telegram")


class TelegramClient:
    def __init__(self, token: str, session: requests.Session | None = None, timeout: float = 20.0):
        self.token = str(token or "").strip()
        self.session = session or requests.Session()
        self.timeout = float(timeout)

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        if not self.token:
            raise RuntimeError("TELEGRAM_TOKEN_MISSING")
        response = self.session.post(
            f"https://api.telegram.org/bot{self.token}/{method}",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(str(data.get("description") or "TELEGRAM_ERROR"))
        return data.get("result")

    def send_message(self, chat_id: str, text: str) -> Any:
        return self._call("sendMessage", {"chat_id": str(chat_id), "text": str(text)[:4096]})


class SignalTelegramService:
    """Outbound-only notifications for BUY and its matching fully closed trade."""

    def __init__(
        self,
        client: TelegramClient,
        *,
        chat_id: str,
        buy_batch_minutes: float = 0.0,
    ):
        self.client = client
        self.chat_id = str(chat_id or "").strip()
        self.buy_batch_seconds = max(0.0, float(buy_batch_minutes or 0.0) * 60.0)
        self._buy_lock = threading.RLock()
        self._pending_buys: dict[str, dict[str, Any]] = {}
        self._buy_timer: threading.Timer | None = None
        self._buy_window_started: datetime | None = None

    @staticmethod
    def _price(value: float) -> str:
        return f"{float(value or 0.0) * 1000:,.0f}đ"

    @staticmethod
    def _money(value: float) -> str:
        return f"{float(value or 0.0):+,.0f}đ"

    @staticmethod
    def _em_name(value: str) -> str:
        return {
            "TP": "TP",
            "TAKE_PROFIT": "TP",
            "NORMAL": "NORMAL",
            "NORMAL_PROTECTION": "NORMAL",
            "HIGH": "HIGH",
            "HIGH_PROFIT_PROTECTION": "HIGH",
            "IND_EXIT": "EXIT SELL",
            "INDICATOR_EXIT": "EXIT SELL",
        }.get(str(value or "").strip().upper(), "")

    def _send(self, text: str) -> bool:
        if not self.chat_id:
            return False
        try:
            self.client.send_message(self.chat_id, text)
            return True
        except Exception as exc:
            logger.warning("Telegram trade notification failed: %s", exc)
            return False

    def notify_buy(
        self,
        *,
        symbol: str,
        signal_id: str,
        price: float,
        market_state: str,
    ) -> bool:
        symbol = str(symbol or "").strip().upper()
        signal_id = str(signal_id or "").strip().upper()
        if not symbol or not signal_id or float(price or 0.0) <= 0:
            return False
        item = {
            "symbol": symbol,
            "signal_id": signal_id,
            "price": float(price),
            "market_state": str(market_state or "UNKNOWN").upper(),
        }
        if self.buy_batch_seconds <= 0:
            return self._send(self._format_buys([item]))
        with self._buy_lock:
            self._pending_buys[symbol] = item
            if self._buy_timer is None:
                self._buy_window_started = datetime.now()
                self._buy_timer = threading.Timer(self.buy_batch_seconds, self.flush_buys)
                self._buy_timer.daemon = True
                self._buy_timer.start()
        return True

    def _format_buys(
        self,
        items: list[dict[str, Any]],
        *,
        started: datetime | None = None,
    ) -> str:
        if len(items) == 1:
            item = items[0]
            return "\n".join(
                (
                    f"🟢 BUY · {item['symbol']}",
                    f"ID: {item['signal_id']}",
                    f"Giá tín hiệu: {self._price(item['price'])}",
                    f"VNINDEX: {item['market_state']}",
                )
            )
        started = started or self._buy_window_started or datetime.now()
        lines = [
            f"🟢 BUY SIGNAL · {len(items)} MÃ",
            f"{started:%H:%M}–{datetime.now():%H:%M}",
            "",
        ]
        lines.extend(
            f"{item['symbol']} · {self._price(item['price'])} · {item['market_state']} · {item['signal_id']}"
            for item in sorted(items, key=lambda value: str(value.get("symbol", "")))
        )
        return "\n".join(lines)

    def flush_buys(self) -> bool:
        """Send one BUY digest for the current fixed batching window."""
        with self._buy_lock:
            timer = self._buy_timer
            self._buy_timer = None
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
            items = list(self._pending_buys.values())
            self._pending_buys.clear()
            started = self._buy_window_started
            self._buy_window_started = None
        if not items:
            return False
        sent = self._send(self._format_buys(items, started=started))
        with self._buy_lock:
            if not sent:
                for item in items:
                    self._pending_buys[str(item["symbol"])] = item
                if self._buy_timer is None:
                    self._buy_window_started = started or datetime.now()
                    self._buy_timer = threading.Timer(self.buy_batch_seconds, self.flush_buys)
                    self._buy_timer.daemon = True
                    self._buy_timer.start()
        return sent

    def notify_signal_only(
        self,
        *,
        symbol: str,
        signal: str,
        price: float,
        market_state: str,
        blocked_by: str = "",
    ) -> bool:
        """A signal the rule produced but the bot could not act on.

        Five slots and forty symbols means most signals never become orders;
        these are the ones that leave no other trace.
        """
        symbol = str(symbol or "").strip().upper()
        signal = str(signal or "").strip().upper()
        if not symbol or signal not in {"BUY", "SELL"}:
            return False
        why = str(blocked_by or "").strip()
        lines = [
            f"⚪ TÍN HIỆU {signal} · {symbol}",
            f"Giá {self._price(price)} · {str(market_state or 'UNKNOWN').upper()}",
        ]
        if why:
            lines.append(f"Bot không vào: {why}")
        return self._send(chr(10).join(lines))

    def notify_closed(self, *, cycle: TradeCycle, reason: str = "") -> bool:
        """Notify only after every share in the matching trade has been sold."""
        if cycle.status != "CLOSED" or cycle.open_quantity != 0:
            return False
        # Preserve lifecycle order when a trade closes inside the BUY window.
        self.flush_buys()
        invested = cycle.avg_entry_price * cycle.entry_quantity * 1000.0
        pnl_pct = cycle.net_pnl / invested * 100.0 if invested > 0 else 0.0
        close_reason = str(reason or "HOÀN TẤT").strip().upper().replace("_", " ")
        enabled_em = [self._em_name(value) for value in cycle.em_modes]
        enabled_em = list(dict.fromkeys(value for value in enabled_em if value))
        triggered_em = [self._em_name(value) for value in cycle.exit_events]
        triggered_em = list(dict.fromkeys(value for value in triggered_em if value))
        em_enabled_text = " · ".join(enabled_em) if enabled_em else "KHÔNG"
        em_triggered_text = (
            f"{len(triggered_em)} lần · " + " · ".join(f"{value} ×1" for value in triggered_em)
            if triggered_em
            else "0 lần"
        )
        return self._send(
            "\n".join(
                (
                    f"🔴 CLOSED · {cycle.symbol} · {cycle.execution_mode}",
                    f"ID: {cycle.id.upper()}",
                    f"Mua: {cycle.entry_quantity:,} CP @ {self._price(cycle.avg_entry_price)}",
                    f"Bán: {cycle.sold_quantity:,} CP @ {self._price(cycle.avg_exit_price)}",
                    f"Phí + thuế: {cycle.fees_paid:,.0f}đ",
                    f"Lãi/lỗ ròng: {self._money(cycle.net_pnl)} ({pnl_pct:+.2f}%)",
                    f"EM bật: {em_enabled_text}",
                    f"EM kích hoạt: {em_triggered_text}",
                    f"Lý do: {close_reason}",
                )
            )
        )
