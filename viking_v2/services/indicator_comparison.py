"""Read-only DNSE indicator normalization; never rewrites trading evidence.

Use the daemon's daily-history cache and each observation's recorded price.
Canonical units, daily dates and Wilder/EMA arithmetic are not a conversion
to an independently adjusted TradingView feed. Never guess adjustment factors
or round input prices to force a match with another chart.
"""
from __future__ import annotations

from datetime import date, datetime
import json
from math import isfinite
from pathlib import Path
from typing import Any

from ..rules.business import indicator_snapshot
from ..trading.market import VN_TZ


class DNSEIndicatorNormalizer:
    """Calculate display values from an existing cache, without any API writes.

    The daemon already downloads DNSE daily bars. Reading one coherent JSON
    snapshot also avoids a second feed/API and never replaces the original log.
    """

    def __init__(self, cache_path: str | Path):
        self.cache_path = Path(cache_path)

    def signature(self) -> tuple[int, int] | None:
        try:
            stat = self.cache_path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    @staticmethod
    def _history(raw: Any, scale: float) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise ValueError("Chưa có lịch sử DNSE của mã")
        sessions: dict[date, dict[str, Any]] = {}
        for bar in raw:
            if not isinstance(bar, dict):
                raise ValueError("Lịch sử DNSE có nến không hợp lệ")
            timestamp = float(bar.get("time", 0))
            close = float(bar.get("close", 0)) / scale
            if not isfinite(timestamp) or timestamp <= 0 or not isfinite(close) or close <= 0:
                raise ValueError("Lịch sử DNSE có giá/ngày không hợp lệ")
            day = datetime.fromtimestamp(timestamp, VN_TZ).date()
            previous = sessions.get(day)
            if previous and previous["close"] != close:
                raise ValueError("Lịch sử không phải một nến 1D mỗi phiên")
            sessions[day] = {"time": timestamp, "close": close, "date": day}
        return [sessions[day] for day in sorted(sessions)]

    def normalize(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep numbers on failure; normalized numbers are a separate projection.

        History is truncated strictly before the event's Vietnamese session.
        A later cache can have corrected/adjusted history; report its timestamp,
        rather than claiming to recover the old decision or another chart feed.
        """
        error, data = "", {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("symbols"), dict):
                raise ValueError("Chưa có lịch sử DNSE")
        except FileNotFoundError:
            data = {}
            error = "Chưa có lịch sử DNSE; chờ dữ liệu rồi bấm LÀM MỚI"
        except (OSError, ValueError, TypeError):
            data = {}
            error = "Chưa đọc được lịch sử DNSE; giữ số đã ghi"
        if data.get("resolution", "1D") != "1D":
            error = "Cần lịch sử nến 1D, không tính EMA/RSI ngày bằng nến phút"
        unit = str(data.get("price_unit", "THOUSAND_VND")).upper()
        if unit not in {"THOUSAND_VND", "VND"}:
            error = "Chưa xác định đơn vị giá lịch sử DNSE"
        scale = 1000.0 if unit == "VND" else 1.0
        try:
            cache_at = datetime.fromtimestamp(float(data.get("updated_at", 0)), VN_TZ).isoformat(timespec="seconds") if data.get("updated_at") else ""
        except (TypeError, ValueError, OverflowError, OSError):
            cache_at = ""
        histories: dict[str, tuple[list[dict[str, Any]], str]] = {}
        calculated: dict[tuple, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        output = []
        for original in rows:
            row = dict(original)
            row["dnse_indicators"] = {key: original.get(key, "") for key in (
                "ema_fast", "ema_slow", "rsi", "rsi_previous", "rsi_previous_date",
            )}
            row["normalization_source"] = "DNSE"
            row["normalization_cache_at"] = cache_at
            row["normalization_error"] = error
            if error:
                output.append(row)
                continue
            try:
                symbol = str(original.get("symbol", "")).strip().upper()
                if not symbol or not symbol.isalnum():
                    raise ValueError("Mã không hợp lệ")
                timestamp = datetime.fromisoformat(str(original.get("timestamp", "")).replace("Z", "+00:00"))
                day = (timestamp.astimezone(VN_TZ) if timestamp.tzinfo else timestamp).date()
                if symbol not in histories:
                    try:
                        histories[symbol] = (self._history(data["symbols"].get(symbol), scale), "")
                    except (ValueError, TypeError, OverflowError, OSError):
                        histories[symbol] = ([], "Lịch sử DNSE của mã thiếu hoặc không hợp lệ")
                history, history_error = histories[symbol]
                if history_error:
                    raise ValueError(history_error)
                history = [bar for bar in history if bar["date"] < day]
                # Event CSV uses thousands of VND; captures also retain explicit VND.
                price = (float(original["price_vnd"]) / 1000.0
                         if original.get("price_vnd") not in (None, "") else float(original["price"]))
                if not isfinite(price) or price <= 0:
                    raise ValueError("Thiếu giá tại giờ ghi; không dùng giá cuối ngày thay thế")
                periods, defaults = [], []
                for key, default in (("ema_fast_period", 3), ("ema_slow_period", 6), ("rsi_period", 14)):
                    raw = original.get(key)
                    if raw in (None, ""):
                        raw = default
                        defaults.append(key)
                    value = float(raw)
                    if not isfinite(value) or not value.is_integer() or not 1 <= value <= 1000:
                        raise ValueError("Chu kỳ chỉ báo đã ghi không hợp lệ")
                    periods.append(int(value))
                fast, slow, rsi_period = periods
                if len(history) < max(fast, slow, rsi_period + 1):
                    raise ValueError("Chưa đủ nến DNSE trước phiên để tính EMA/RSI")
                key = (symbol, day, price, *periods)
                if key not in calculated:
                    values = indicator_snapshot([*history, {"close": price}], fast, slow, rsi_period)
                    calculated[key] = values, history
                values, history = calculated[key]
                row.update(ema_fast=values["ema_fast"], ema_slow=values["ema_slow"],
                           rsi=values["rsi"], rsi_previous=values["rsi_previous"],
                           rsi_previous_date=history[-1]["date"].isoformat(),
                           normalization_ok=True, normalization_periods=periods,
                           normalization_defaults=defaults, normalization_bars=len(history),
                           normalization_history_start=history[0]["date"].isoformat(),
                           normalization_history_end=history[-1]["date"].isoformat(),
                           normalization_condition=(values["ema_fast"] > values["ema_slow"]
                                                    and values["rsi"] > values["rsi_previous"]))
            except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
                row["normalization_error"] = str(exc)
            output.append(row)
        return output


def number_comparison(left: Any, right: Any, decimals: int = 2) -> str:
    """Display the raw comparison, increasing precision rather than lying."""
    try:
        a, b = float(left), float(right)
        if not isfinite(a) or not isfinite(b):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return "—"
    sign = ">" if a > b else "<" if a < b else "="
    for places in range(decimals, 13):
        if a == b or f"{a:.{places}f}" != f"{b:.{places}f}":
            return f"{a:.{places}f} {sign} {b:.{places}f}"
    return f"{a!r} {sign} {b!r}"


def rsi_observation_display(current: Any, previous: Any) -> tuple[str, str]:
    """Keep available RSI evidence visible without inventing the missing side.

    Display only: does not calculate a new baseline or change the entry rule.
    """
    def valid(value: Any) -> float | None:
        try:
            number = float(value)
            return number if isfinite(number) and 0 <= number <= 100 else None
        except (TypeError, ValueError, OverflowError):
            return None

    now, prior = valid(current), valid(previous)
    if now is not None and prior is not None:
        return number_comparison(now, prior), ""
    if now is not None:
        return f"{now:.2f} · Trước: —", "Thiếu RSI trước"
    if prior is not None:
        return f"— · Trước: {prior:.2f}", "Thiếu RSI hiện tại"
    return "—", "Thiếu RSI"
