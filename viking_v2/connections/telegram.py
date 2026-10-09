from __future__ import annotations

import logging
from datetime import datetime
import threading
from typing import Any

import requests

from ..models import TradeCycle
from ..branding import APP_NAME
from ..trading.market import VN_TZ


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

    def safe_error(self, error: object) -> str:
        """Never expose the bot token through requests' URL-rich errors."""
        message = str(error or "TELEGRAM_ERROR")
        return message.replace(self.token, "<REDACTED>") if self.token else message


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
        self._buy_generation = 0

    def configure_buy_delivery(self, *, buy_batch_minutes: float) -> None:
        """Keep queued notices when switching delivery; never block the UI on I/O."""
        seconds = max(0.0, float(buy_batch_minutes or 0.0) * 60.0)
        with self._buy_lock:
            if seconds == self.buy_batch_seconds:
                return
            self.buy_batch_seconds = seconds
            if self._buy_timer is not None:
                self._buy_timer.cancel()
                self._buy_timer = None
            if self._pending_buys:
                elapsed = (
                    (datetime.now() - self._buy_window_started).total_seconds()
                    if self._buy_window_started else 0.0
                )
                # A zero-delay timer flushes already queued notices in its worker.
                self._buy_timer = threading.Timer(max(0.0, seconds - elapsed), self.flush_buys)
                self._buy_timer.daemon = True
                self._buy_timer.start()

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
            "NORMAL": "PROTECT",
            "NORMAL_PROTECTION": "PROTECT",
            "IND_EXIT": "E",
            "INDICATOR_EXIT": "E",
        }.get(str(value or "").strip().upper(), "")

    def _send(self, text: str) -> bool:
        if not self.chat_id:
            return False
        try:
            self.client.send_message(self.chat_id, text)
            return True
        except Exception as exc:
            safe_error = getattr(self.client, "safe_error", None)
            message = safe_error(exc) if callable(safe_error) else "TELEGRAM_SEND_FAILED"
            logger.warning("Telegram trade notification failed: %s", message)
            return False

    def notify_buy(
        self,
        *,
        symbol: str,
        signal_id: str,
        price: float,
        market_state: str,
        execution_mode: str = "",
        order_info: dict[str, Any] | None = None,
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
            "execution_mode": str(execution_mode or "").upper(),
            "order_info": dict(order_info or {}),
        }
        with self._buy_lock:
            key = self._buy_key(item)
            if key in self._pending_buys:
                return True  # Already retained for delivery; do not send twice.
            if self.buy_batch_seconds > 0:
                self._pending_buys[key] = item
                if self._buy_timer is None:
                    self._buy_window_started = datetime.now()
                    self._buy_timer = threading.Timer(self.buy_batch_seconds, self.flush_buys)
                    self._buy_timer.daemon = True
                    self._buy_timer.start()
                return True
            generation = self._buy_generation
        if self._send(self._format_buys([item])):
            return True
        with self._buy_lock:
            if generation == self._buy_generation:
                item["immediate_delivery"] = True
                self._pending_buys.setdefault(key, item)
                if self._buy_timer is None:
                    self._buy_window_started = datetime.now()
                    self._buy_timer = threading.Timer(60.0, self.flush_buys)
                    self._buy_timer.daemon = True
                    self._buy_timer.start()
        return False

    def _format_buys(
        self,
        items: list[dict[str, Any]],
        *,
        started: datetime | None = None,
    ) -> str:
        if len(items) == 1:
            item = items[0]
            if item.get("order_info"):
                return "\n".join([*self._compact_buy_lines(item), "⏳ Chưa xác nhận đã gửi/khớp."])
            return "\n".join(
                (
                    f"🟢 BUY · {item['symbol']}"
                    + (f" · {item['execution_mode']}" if item.get("execution_mode") else "")
                    + " · ĐÃ XẾP LỆNH",
                    f"ID: {item['signal_id']}",
                    f"Giá tín hiệu: {self._price(item['price'])}",
                    f"VNINDEX: {item['market_state']}",
                    "Thông báo tạo yêu cầu; không xác nhận đã gửi/khớp.",
                )
            )
        started = started or self._buy_window_started or datetime.now()
        codes = len({item["symbol"] for item in items})
        lines = [
            f"🟢 BUY ĐÃ XẾP LỆNH · {len(items)} MÃ" if codes == len(items) else f"🟢 BUY ĐÃ XẾP LỆNH · {len(items)} LỆNH · {codes} MÃ",
            f"{started:%H:%M}–{datetime.now():%H:%M}",
            "",
        ]
        for item in sorted(items, key=lambda value: str(value.get("symbol", ""))):
            if item.get("order_info"):
                lines.extend(self._compact_buy_lines(item))
            else:
                lines.append(
                    f"{item['symbol']} · "
                    + (f"{item.get('execution_mode')} · " if item.get("execution_mode") else "")
                    + f"{self._price(item['price'])} · {item['market_state']} · {item['signal_id']}"
                )
        lines.append("⏳ Chưa xác nhận đã gửi/khớp." if any(item.get("order_info") for item in items)
                     else "Thông báo tạo yêu cầu; không xác nhận đã gửi/khớp.")
        return "\n".join(lines)

    def _compact_buy_lines(self, item: dict[str, Any]) -> list[str]:
        info = item["order_info"]
        created = datetime.fromtimestamp(float(info["created_at"]), VN_TZ)

        def amount(value: float) -> str:
            return f"{value / 1_000_000:.2f} tr" if value >= 1_000_000 else f"{value / 1000:.1f}K"

        fee = float(info["fee_vnd"])
        gross = float(info["gross_vnd"])
        budget = float(info["budget_vnd"])
        return [
            f"🟢 BUY · {item['symbol']} · {item['execution_mode']} · ĐÃ XẾP LỆNH · #{str(info['order_id'])[:8]}",
            f"🕒 {created:%H:%M} · {info['order_type']} · {int(info['quantity'])} CP · Giá tín hiệu {self._price(item['price'])}",
            f"💰 Dự trù {amount(gross)} (gồm phí {amount(fee)}) / vốn {amount(budget)} · Giá tính vốn {self._price(info['budget_price'])}",
        ]

    @staticmethod
    def _buy_key(item: dict[str, Any]) -> str:
        return f"{item.get('execution_mode', '')}|{item['symbol']}|{item['signal_id']}"

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
            generation = self._buy_generation
        if not items:
            return False
        # Retry immediate notices individually; they must not become a digest.
        failed = []
        batched = []
        for item in items:
            with self._buy_lock:
                if generation != self._buy_generation:
                    return False  # Category/destination changed while a prior send was in flight.
            if item.get("immediate_delivery"):
                if not self._send(self._format_buys([item])):
                    failed.append(item)
            else:
                batched.append(item)
        if batched:
            with self._buy_lock:
                if generation != self._buy_generation:
                    return False
            if not self._send(self._format_buys(batched, started=started)):
                failed.extend(batched)
        sent = not failed
        with self._buy_lock:
            if not sent and generation == self._buy_generation:
                for item in failed:
                    self._pending_buys.setdefault(self._buy_key(item), item)
                if self._buy_timer is None:
                    self._buy_window_started = started or datetime.now()
                    # Switching to immediate must not cause a zero-delay retry loop.
                    self._buy_timer = threading.Timer(max(60.0, self.buy_batch_seconds), self.flush_buys)
                    self._buy_timer.daemon = True
                    self._buy_timer.start()
        return sent

    def cancel_pending_buys(self) -> int:
        """Drop an unsent BUY digest when the operator turns that category OFF."""
        with self._buy_lock:
            self._buy_generation += 1
            timer = self._buy_timer
            self._buy_timer = None
            if timer is not None:
                timer.cancel()
            count = len(self._pending_buys)
            self._pending_buys.clear()
            self._buy_window_started = None
            return count

    def notify_signal_only(
        self,
        *,
        symbol: str,
        signal: str,
        price: float,
        market_state: str,
        blocked_by: str = "",
        execution_mode: str = "",
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
        why = {
            "BOT_OFF": "BOT đang tắt quyền mua.",
            "NO_AVAILABLE_CAPITAL": "Không đủ vốn được phép dùng.",
            "MAX_POSITIONS": "Đã đủ số mã BOT.",
            "MAX_SYMBOL_ORDERS": "Đã đủ số lần BUY của mã.",
            "WHIPSAW_LOCK": "WHIPSAW đang khóa BUY.",
            "LOCKED_AFTER_LOSSES": "Đang khóa BUY sau chuỗi lỗ.",
            "BUY_WINDOW_WAIT": "Chờ khung giờ mua đã cài.",
            "BUY_WINDOW_MARKET_CLOSED": "Chờ phiên giao dịch.",
            "BUY_WINDOW_EXPIRED": "Đã hết khung giờ mua.",
            "BUY_CONFIRMATION_WAIT": "Chờ tín hiệu giữ đủ thời gian xác nhận.",
        }.get(why, why)
        lines = [
            f"⚪ TÍN HIỆU {signal} · {symbol}"
            + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
            f"Giá {self._price(price)} · {str(market_state or 'UNKNOWN').upper()}",
        ]
        if why:
            lines.append(f"CHƯA GỬI: {why}")
        else:
            lines.append("Chỉ có tín hiệu; chưa gửi lệnh.")
        return self._send(chr(10).join(lines))

    def notify_technical_buy(
        self, *, symbol: str, price: float, market_state: str,
        indicators: dict[str, Any], ema_enabled: bool, rsi_enabled: bool,
        execution_mode: str = "",
    ) -> bool:
        """Report the technical BUY candidate, never an execution outcome."""
        lines = [
            f"⚪ TÍN HIỆU BUY · {str(symbol).upper()}"
            + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
            f"Giá {self._price(price)} · {str(market_state).upper()}",
        ]
        if ema_enabled:
            lines.append(
                f"EMA {int(indicators['buy_ema_fast_period'])}/{int(indicators['buy_ema_slow_period'])}: "
                f"{self._price(indicators['buy_ema_fast'])} > {self._price(indicators['buy_ema_slow'])}"
            )
        if rsi_enabled:
            lines.append(
                f"RSI{int(indicators['rsi_period'])}: "
                f"{float(indicators['rsi_previous']):.2f} → {float(indicators['rsi']):.2f} · tăng"
            )
        lines.append("Chỉ báo kỹ thuật; không xác nhận đã gửi/khớp lệnh.")
        return self._send("\n".join(lines))

    @staticmethod
    def _comparison(left: float, right: float, *, scale: float = 1.0) -> str:
        """Compare unrounded values; expand precision if two labels collide."""
        left, right = float(left), float(right)
        relation = ">" if left > right else "<" if left < right else "="
        for digits in range(2, 7):
            first, second = f"{left * scale:,.{digits}f}", f"{right * scale:,.{digits}f}"
            if first != second or relation == "=":
                break
        else:
            first, second = repr(left * scale), repr(right * scale)
        return f"{first} {relation} {second}"

    def notify_buy_lost(
        self, *, symbol: str, price: float, indicators: dict[str, Any],
        ema_enabled: bool, rsi_enabled: bool, observed_at: str,
        execution_mode: str = "",
    ) -> bool:
        lines = [
            f"⚫ MẤT BUY · {str(symbol).upper()} · {str(execution_mode).upper()} · {observed_at[11:19]}",
            f"Giá {self._price(price)}",
        ]
        if ema_enabled:
            lines.append(
                f"EMA {int(indicators['buy_ema_fast_period'])}/{int(indicators['buy_ema_slow_period'])}: "
                + self._comparison(indicators["buy_ema_fast"], indicators["buy_ema_slow"], scale=1000)
            )
        if rsi_enabled:
            lines.append(
                f"RSI{int(indicators['rsi_period'])} nay / phiên trước: "
                + self._comparison(indicators["rsi"], indicators["rsi_previous"])
            )
        lines.append("Chưa tạo lệnh.")
        return self._send("\n".join(lines))

    def notify_protect_alert(
        self,
        *,
        symbol: str,
        price: float,
        mfe_pct: float,
        peak_price: float,
        protect_price: float,
        sell_pct: float,
        dynamic: bool,
        atr_pct: float = 0.0,
        atr_activation_multiplier: float = 0.0,
        atr_multiplier: float = 0.0,
        atr_activation_enabled: bool = True,
        atr_trail_enabled: bool = True,
        retention_pct: float = 0.0,
        retention_until_pct: float = 0.0,
        retention_enabled: bool = True,
        retention_until_enabled: bool = True,
        policy: str = "ALERT",
        execution_mode: str = "",
    ) -> bool:
        """Report a PROTECT hit; RULE policy remains the source of action."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return False
        policy = "AUTO" if str(policy or "").upper() == "AUTO" else "ALERT"
        start_label = (
            f"×{float(atr_activation_multiplier):g}"
            if dynamic and atr_activation_enabled else "OFF"
        )
        trail_label = (
            f"×{float(atr_multiplier):g}" if dynamic and atr_trail_enabled else "OFF"
        )
        keep_label = (
            f"Giữ {float(retention_pct):g}% lãi cao nhất "
            + (f"tới MFE {float(retention_until_pct):g}%" if retention_until_enabled else "tới ARM")
            if dynamic and retention_enabled and float(retention_pct) > 0 else "Giữ lãi: OFF"
        )
        return self._send(
            "\n".join((
                f"🟠 PROTECT HIT · {symbol}"
                + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
                f"Giá: {self._price(price)} · MFE {float(mfe_pct):+.2f}%",
                f"Peak: {self._price(peak_price)} · PROTECT: {self._price(protect_price)}",
                f"ATR14 (nến ngày đã đóng tới phiên trước): {float(atr_pct):.2f}%",
                f"START ATR: {start_label} · TRAIL ATR: {trail_label}",
                keep_label,
                f"Khối lượng {float(sell_pct):g}% · DYNAMIC {'ON' if dynamic else 'OFF'}",
                (
                    "AUTO · ĐÃ TẠO YÊU CẦU BÁN."
                    if policy == "AUTO"
                    else "ALERT chỉ ghi nhận, không đặt lệnh."
                ),
            ))
        )

    def notify_external_sell(
        self,
        *,
        symbol: str,
        quantity: int,
        price: float,
        remaining_quantity: int,
    ) -> bool:
        symbol = str(symbol or "").strip().upper()
        if not symbol or int(quantity or 0) <= 0:
            return False
        return self._send(
            "\n".join(
                (
                    f"🟠 EXTERNAL SELL · {symbol}",
                    f"Đã đồng bộ: {int(quantity):,} CP @ {self._price(price)}",
                    (
                        f"Vị thế {APP_NAME} còn: {int(remaining_quantity):,} CP"
                        if int(remaining_quantity or 0) > 0
                        else f"Vị thế {APP_NAME} đã đóng hoàn toàn."
                    ),
                )
            )
        )

    def notify_indicator_exit_alert(
        self,
        *,
        symbol: str,
        price: float,
        ema_fast_period: int,
        ema_slow_period: int,
        ema_fast: float,
        ema_slow: float,
        rsi_period: int,
        rsi: float,
        rsi_previous: float,
        ema_enabled: bool = True,
        rsi_enabled: bool = True,
        execution_mode: str = "",
    ) -> bool:
        """Report the original VA indicator exit without placing an order."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return False
        lines = [
            f"🔴 E ALERT · {symbol}"
            + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
            f"Giá: {self._price(price)}",
        ]
        if ema_enabled:
            lines.append(
                (
                    f"EMA {int(ema_fast_period)}/{int(ema_slow_period)}: "
                    f"{self._price(ema_fast)} / {self._price(ema_slow)} · EMA nhanh < EMA chậm"
                )
            )
        if rsi_enabled:
            lines.append(
                (
                    f"RSI{int(rsi_period)}: {float(rsi_previous):.2f} → "
                    f"{float(rsi):.2f} · giảm"
                )
            )
        lines.append("ALERT chỉ ghi nhận, không đặt lệnh.")
        return self._send("\n".join(lines))

    def notify_corporate_action(
        self, *, symbol: str, ex_date: str, execution_mode: str = "",
    ) -> bool:
        symbol = str(symbol or "").strip().upper()
        ex_date = str(ex_date or "").strip()
        if not symbol or not ex_date:
            return False
        return self._send(
            "\n".join(
                (
                    f"⚠️ CHỐT QUYỀN · {symbol}"
                    + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
                    f"Ngày GDKHQ: {ex_date}",
                    f"Đang có vị thế · {APP_NAME} không tự bán · operator kiểm tra thủ công.",
                )
            )
        )

    def notify_market_holiday(
        self, *, holiday_date: str, execution_mode: str = "",
    ) -> bool:
        holiday_date = str(holiday_date or "").strip()
        if not holiday_date:
            return False
        return self._send(
            "\n".join(
                (
                    "📅 NGHỈ GIAO DỊCH"
                    + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
                    f"Ngày: {holiday_date}",
                    "BOT không tạo BUY mới; vị thế và lệnh đang chờ vẫn được giữ an toàn.",
                )
            )
        )

    def notify_system_alert(
        self, *, summary: str, execution_mode: str = "", severity: str = "ERROR",
    ) -> bool:
        summary = str(summary or "").strip()
        if not summary:
            return False
        return self._send(
            "\n".join(
                (
                    ("🟡 HỆ THỐNG · CẢNH BÁO" if severity == "WARNING" else "🔴 HỆ THỐNG CẦN KIỂM TRA")
                    + (f" · {str(execution_mode).upper()}" if execution_mode else ""),
                    summary,
                    f"Xem HEALTH và log {APP_NAME} để kiểm tra chi tiết.",
                )
            )
        )

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
                    f"E/M bật: {em_enabled_text}",
                    f"E/M kích hoạt: {em_triggered_text}",
                    f"Lý do: {close_reason}",
                )
            )
        )
