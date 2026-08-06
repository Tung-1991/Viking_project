# -*- coding: utf-8 -*-
import pandas as pd


def _completed_work_frame(df: pd.DataFrame, context: dict = None) -> pd.DataFrame:
    """Dùng nến đã đóng trong phiên để tránh so volume dở dang với nến đủ."""
    work = df
    try:
        symbol = (context or {}).get("symbol")
        if symbol and len(df) > 1:
            from core.market_hours import is_symbol_trade_window_open
            if is_symbol_trade_window_open(symbol)[0]:
                work = df.iloc[:-1]
    except Exception:
        work = df
    return work


def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    p = params.get("period", 20)
    mult = params.get("multiplier", 1.1)

    # [FIX CKVN - Audit F2] Trong phiên, nến cuối CHƯA ĐÓNG: volume mới tích một phần phiên mà
    # đem so SMA của các nến đủ thì gần như không bao giờ vượt ngưỡng (đặc biệt khung D1).
    # -> Đang trong phiên thì xét trên nến ĐÃ ĐÓNG gần nhất; ngoài phiên nến cuối đã final, giữ nguyên.
    work = _completed_work_frame(df, context)

    if len(work) < p:
        return 0

    # Tính SMA Volume thủ công nhẹ nhàng vì DataEngine không tính
    vol_sma = work['volume'].rolling(window=p).mean().iloc[-1]
    curr_vol = work['volume'].iloc[-1]

    # Nếu Volume đột biến
    if curr_vol > (vol_sma * mult):
        close = work['close'].iloc[-1]
        open_p = work['open'].iloc[-1]

        # Nến xanh volume lớn
        if close > open_p: return 1
        # Nến đỏ volume lớn
        if close < open_p: return -1

    return 0


def get_check_metrics(df: pd.DataFrame, params: dict, context: dict = None) -> dict:
    """Các số liệu gọn để CHECK/RAW report tự xuất mà không viết cứng cột."""
    period = max(1, int(params.get("period", 20) or 20))
    work = _completed_work_frame(df, context)
    if work is None or work.empty or "volume" not in work:
        return {}

    current = float(work["volume"].iloc[-1] or 0.0)
    average = float(work["volume"].tail(period).mean() or 0.0)
    ratio = current / average if average > 0 else None
    direction = "FLAT"
    if "open" in work and "close" in work:
        open_price = float(work["open"].iloc[-1] or 0.0)
        close_price = float(work["close"].iloc[-1] or 0.0)
        direction = "UP" if close_price > open_price else "DOWN" if close_price < open_price else "FLAT"

    return {
        "volume": current,
        f"volume_sma_{period}": average,
        f"volume_ratio_{period}": ratio,
        "candle_direction": direction,
    }
