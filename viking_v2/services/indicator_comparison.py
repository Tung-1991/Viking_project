"""Read-only indicator comparison; never edits decisions, prices or orders.

TradingView's rounded values cannot be recovered from DNSE. This adapter
imports the actual daily chart CSV, retaining its precision. A CSV's last
session is its declared price-basis date. Without dated adjustment factors,
only that session's raw intraday prices can safely be combined with it.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any

from ..backtest.data import bar_date
from ..backtest.replay import _normalized_rows
from ..rules.business import indicator_snapshot
from ..storage import AtomicJSONStore


class IndicatorComparisonStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _store(self, symbol: str) -> AtomicJSONStore:
        symbol = str(symbol).strip().upper()
        if not symbol or not symbol.isalnum():
            raise ValueError("Mã chứng khoán không hợp lệ.")
        return AtomicJSONStore(self.root / f"{symbol}_tradingview_1D.json", default={})

    def import_daily(self, path: str | Path, symbol: str, exchange: str = "HOSE") -> int:
        rows, _scale = _normalized_rows(
            Path(path), resolution="1D", exchange=exchange,
            timezone_name="Asia/Ho_Chi_Minh", price_scale=1000.0,
        )
        dates = [bar_date(row).isoformat() for row in rows]
        if any(not isfinite(float(row[key])) for row in rows for key in ("open", "high", "low", "close", "volume")):
            raise ValueError("CSV chứa số không hợp lệ (NaN/Infinity).")
        if len(set(dates)) != len(rows):
            raise ValueError("Cần CSV khung 1D, không phải nhiều nến intraday trong một ngày.")
        if len(rows) < 100:
            raise ValueError("Cần ít nhất 100 nến 1D để đối chiếu EMA/RSI; nên xuất toàn bộ lịch sử chart.")
        self._store(symbol).write({
            "source": "TRADINGVIEW_CSV", "symbol": symbol.upper(),
            "basis_date": dates[-1], "imported_at": datetime.now().isoformat(),
            "bars": rows,
        })
        return len(rows)

    def compare(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        datasets: dict[str, Any] = {}
        output = []
        for original in rows:
            row = dict(original)
            symbol = str(row.get("symbol", "")).upper()
            if symbol not in datasets:
                try:
                    loaded = self._store(symbol).read() if symbol else {}
                    datasets[symbol] = loaded if isinstance(loaded, dict) else {}
                except ValueError:
                    datasets[symbol] = {}
            data = datasets[symbol]
            row["comparison_source"] = "TRADINGVIEW"
            # Keep the complete original record separate from computed values.
            row["dnse_indicators"] = {key: original.get(key, "") for key in (
                "ema_fast", "ema_slow", "rsi", "rsi_previous", "rsi_previous_date",
            )}
            row.update(ema_fast="", ema_slow="", rsi="", rsi_previous="", rsi_previous_date="")
            day = str(row.get("timestamp", ""))[:10]
            if not data.get("bars"):
                row["comparison_error"] = "Chưa nạp CSV TradingView 1D của mã"
            elif day != data.get("basis_date"):
                row["comparison_error"] = "CSV khác phiên chuẩn giá; cần CSV/hệ số đúng phiên"
            else:
                try:
                    history = [bar for bar in data["bars"] if bar_date(bar).isoformat() < day]
                    price = float(original["price"])
                    if not isfinite(price) or price <= 0:
                        raise ValueError("Giá intraday không hợp lệ")
                    values = indicator_snapshot(
                        [*history, {"close": price}],
                        int(original.get("ema_fast_period") or 3),
                        int(original.get("ema_slow_period") or 6),
                        int(original.get("rsi_period") or 14),
                    )
                    if values["rsi_previous"] is None:
                        raise ValueError("Thiếu lịch sử để tính RSI")
                    row.update(ema_fast=values["ema_fast"], ema_slow=values["ema_slow"],
                               rsi=values["rsi"], rsi_previous=values["rsi_previous"],
                               rsi_previous_date=bar_date(history[-1]).isoformat())
                    row["comparison_entry"] = values["ema_fast"] > values["ema_slow"] and values["rsi"] > values["rsi_previous"]
                    row["comparison_periods_defaulted"] = not all(original.get(key) for key in (
                        "ema_fast_period", "ema_slow_period", "rsi_period",
                    ))
                except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError) as exc:
                    row["comparison_error"] = str(exc)
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
