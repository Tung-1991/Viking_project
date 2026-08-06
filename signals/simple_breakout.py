# -*- coding: utf-8 -*-
import pandas as pd


def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    lookback = int(params.get("lookback", 1))

    # 1. Lấy hệ số đệm ATR từ UI (Mặc định 0.0 nếu chưa cài)
    atr_buffer_multiplier = float(params.get("atr_buffer", 0.0))

    if df is None or len(df) < lookback + 1:
        return 0

    # 2. Lấy giá trị ATR thời gian thực từ context (Ưu tiên ATR của G2)
    # [FIX CKVN - Audit F3] Fallback cũ 0.0005 là scale pip forex (Exness) — vô nghĩa với giá CKVN.
    # Thiếu context thì dùng 0.1% giá hiện tại làm đệm tương đối.
    current_atr = context.get("atr_G2", 0.0) if context else 0.0
    if not current_atr:
        current_atr = float(df["close"].iloc[-1]) * 0.001

    # 3. Tính toán khoảng đệm thực tế (Giá trị tuyệt đối)
    actual_buffer = current_atr * atr_buffer_multiplier

    current_close = df["close"].iloc[-1]

    prev_high = df["high"].iloc[-(lookback + 1) : -1].max()
    prev_low = df["low"].iloc[-(lookback + 1) : -1].min()

    # 4. Áp dụng khoảng đệm vào điểm Breakout
    if current_close > (prev_high + actual_buffer):
        return 1  # BUY

    if current_close < (prev_low - actual_buffer):
        return -1  # SELL

    return 0  # ĐỨNG IM
