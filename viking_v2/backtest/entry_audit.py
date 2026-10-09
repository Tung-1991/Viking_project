"""Intraday indicator replay, explicitly not an execution/broker reconstruction."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import math

from ..config import AppSettings
from ..rules.business import StaticRuleParameters, crossover_signal_from_snapshots, crossover_count, indicator_snapshot
from ..services.indicator_comparison import number_comparison
from ..trading.market import VN_TZ, market_phase, exchange_close_minute


def audit_entry_day(symbol: str, day: date, daily: list[dict], intraday: list[dict],
                    settings: AppSettings, *, exchange: str, baseline_source: str) -> list[dict]:
    """Append exactly one running 1D candle to prior daily history for each 1m close.

    Prices are in the app's 1000 VND unit. Never feed 1m closes into a 1m EMA/RSI,
    never include today's completed daily bar, and never infer OTP/cash/orders.
    """
    params = StaticRuleParameters.from_dict(settings.rule_parameters)
    base = sorted([dict(r) for r in daily
                   if datetime.fromtimestamp(float(r["time"]), VN_TZ).date() < day], key=lambda r: r["time"])
    if len(base) < max(100, params.rsi_period + 2, params.buy_ema_slow + 2):
        raise ValueError("Cần ít nhất 100 nến 1D trước phiên để đối chiếu ổn định.")
    points = sorted([dict(r) for r in intraday
                     if datetime.fromtimestamp(float(r["time"]), VN_TZ).date() == day], key=lambda r: r["time"])
    if len({r["time"] for r in points}) != len(points):
        raise ValueError("Nến intraday trùng thời điểm.")
    if not points or any(not math.isfinite(float(row.get("close", 0))) or float(row.get("close", 0)) <= 0 for row in [*base, *points]):
        raise ValueError("Thiếu nến intraday hoặc giá close không hợp lệ.")
    snapshot = {
        "rule_parameters": params.to_dict(), "baseline_source": baseline_source,
        "baseline_bars": len(base), "baseline_last_date": datetime.fromtimestamp(base[-1]["time"], VN_TZ).date().isoformat(),
        "baseline_sha256": hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest(),
        "source": "DNSE_1M_CLOSE_REPLAY", "timing": "Close nến tại cuối phút, không phải tick thực tế",
        "limits": "Không phát lại vốn/slot/OTP/lệnh thật; cần TRACE/nhật ký để kiểm chứng quyết định VPS",
    }
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
    def marks(rows):
        return indicator_snapshot(rows, params.buy_ema_fast, params.buy_ema_slow, params.rsi_period,
                                  sell_fast=params.sell_ema_fast, sell_slow=params.sell_ema_slow)
    previous = marks(base)
    result = []
    running = None
    for point in points:
        at = datetime.fromtimestamp(point["time"], VN_TZ) + timedelta(seconds=59)
        if running is None:
            running = {**point, "closed": False}
        else:
            running = {**running, "high": max(running["high"], point["high"]),
                       "low": min(running["low"], point["low"]), "close": point["close"],
                       "volume": running["volume"] + point["volume"]}
        rows = [*base, dict(running)]
        current = marks(rows)
        arguments = {"buy_use_ema": params.buy_signal_use_ema, "buy_use_rsi": params.buy_signal_use_rsi,
                     "sell_use_ema": params.sell_signal_use_ema, "sell_use_rsi": params.sell_signal_use_rsi}
        level_signal = crossover_signal_from_snapshots(current, previous, buy_signal_require_ema_cross=False, **arguments)
        cross_signal = crossover_signal_from_snapshots(current, previous, buy_signal_require_ema_cross=True, **arguments)
        selected = cross_signal if params.buy_signal_require_ema_cross else level_signal
        crosses = crossover_count(rows, params.buy_ema_fast, params.buy_ema_slow, params.whipsaw_x) if params.buy_signal_use_ema else 0
        phase = market_phase(at, working_dates=[day.isoformat()], holidays=settings.trading_holidays, exchange=exchange)[0]
        start = datetime.strptime(params.buy_window_start, "%H:%M").time()
        before_window = params.buy_window_enabled and at.time().replace(tzinfo=None) < start
        end = exchange_close_minute(exchange)
        window_ok = phase == "OPEN" and at.hour * 60 + at.minute <= end
        reason = ("EMA_CROSS_REQUIRED" if level_signal == "BUY" and selected != "BUY" else "EMA_RSI_NOT_MET")
        if selected == "BUY":
            reason = "BUY_WINDOW_WAIT" if before_window else "BUY_WINDOW_MARKET_CLOSED" if not window_ok else (
                "WHIPSAW_LOCK" if params.whipsaw_enabled and crosses >= params.whipsaw_n else "TECHNICAL_READY")
        result.append({
            "timestamp": at.isoformat(), "scheduled_at": datetime.fromtimestamp(point["time"], VN_TZ).isoformat(),
            "symbol": symbol, "execution_mode": "REPLAY", "price_vnd": point["close"] * 1000,
            "baseline_source": baseline_source,
            "price_source": "DNSE 1M CLOSE", "decision_time": at.isoformat(),
            "ema_fast": current["buy_ema_fast"], "ema_slow": current["buy_ema_slow"],
            "rsi": current["rsi"], "rsi_previous": current["rsi_previous"],
            "ema_fast_period": params.buy_ema_fast, "ema_slow_period": params.buy_ema_slow, "rsi_period": params.rsi_period,
            "rsi_previous_date": snapshot["baseline_last_date"],
            "ema_comparison": number_comparison(current["buy_ema_fast"], current["buy_ema_slow"], 4),
            "rsi_comparison": number_comparison(current["rsi"], current["rsi_previous"]),
            "entry": level_signal == "BUY", "ema_cross_required": params.buy_signal_require_ema_cross,
            "fresh_cross_entry": cross_signal == "BUY", "exit_e": selected == "SELL",
            "rule_action": "CHỜ KIỂM TRA VỐN/LỆNH" if reason == "TECHNICAL_READY" else "CHƯA ĐẠT",
            "reason": reason, "market_phase": phase, "buy_window_state": "WAIT" if before_window else "OPEN" if window_ok else "CLOSED",
            "reason_text": {
                "EMA_CROSS_REQUIRED": "Chưa có lần EMA vừa cắt lên",
                "EMA_RSI_NOT_MET": "Chưa đạt EMA/RSI",
                "BUY_WINDOW_WAIT": "Chờ giờ mua", "BUY_WINDOW_MARKET_CLOSED": "Ngoài giờ mua",
                "WHIPSAW_LOCK": "WHIPSAW khóa ENTRY", "TECHNICAL_READY": "Đạt kỹ thuật; chưa kiểm tra vốn/lệnh",
            }[reason],
            "whipsaw_count": crosses, "whipsaw_limit": params.whipsaw_n,
            "whipsaw_window": params.whipsaw_x, "whipsaw_on": params.whipsaw_enabled,
            "settings_hash": fingerprint, "settings": snapshot,
        })
        previous = current
    return result
