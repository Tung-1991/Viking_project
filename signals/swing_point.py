# -*- coding: utf-8 -*-
# FILE: signals/swing_point.py
# V4.2: PIVOT STRENGTH DETECTION (KAISER EDITION)

import pandas as pd
import numpy as np

def get_signal_vector(df, params, context=None):
    if df is None or len(df) < 20: return 0
    
    lookback = int(params.get("lookback", 50))
    strength = int(params.get("strength", 2))
    atr_buffer = float(params.get("atr_buffer", 0.5))
    
    # Lấy dữ liệu gần nhất để xét
    df_slice = df.tail(lookback + strength).copy()
    highs = df_slice['high'].values
    lows = df_slice['low'].values
    close = df_slice['close'].values[-1]
    
    # Lấy ATR từ context để tính khoảng cách
    # [FIX CKVN - Audit F3] Fallback cũ 0.0005 là scale pip forex (Exness) — với giá CKVN nó ≈ 0
    # khiến vùng dung sai chạm hỗ trợ/kháng cự gần như chết. Thiếu context -> dùng 0.1% giá.
    atr = context.get("atr_G2", 0.0) if context else 0.0
    if not atr:
        atr = float(close) * 0.001

    last_swing_high = None
    last_swing_low = None

    # Duyệt ngược để tìm Swing High/Low gần nhất thỏa mãn "strength"
    for i in range(len(df_slice) - strength - 1, strength, -1):
        # 1. Tìm Swing High (Đỉnh cụm nến)
        if last_swing_high is None:
            is_high = True
            for j in range(1, strength + 1):
                if highs[i] <= highs[i-j] or highs[i] <= highs[i+j]:
                    is_high = False
                    break
            if is_high: last_swing_high = highs[i]

        # 2. Tìm Swing Low (Đáy cụm nến)
        if last_swing_low is None:
            is_low = True
            for j in range(1, strength + 1):
                if lows[i] >= lows[i-j] or lows[i] >= lows[i+j]:
                    is_low = False
                    break
            if is_low: last_swing_low = lows[i]
        
        if last_swing_high and last_swing_low: break

    # LOGIC RA TÍN HIỆU
    # Nếu giá chạm vùng Swing Low (Support) trong khoảng cách ATR cho phép -> BUY
    if last_swing_low and close <= (last_swing_low + (atr * atr_buffer)):
        if close >= last_swing_low: # Vẫn nằm trên đáy
            return 1
            
    # Nếu giá chạm vùng Swing High (Resistance) trong khoảng cách ATR cho phép -> SELL
    if last_swing_high and close >= (last_swing_high - (atr * atr_buffer)):
        if close <= last_swing_high: # Vẫn nằm dưới đỉnh
            return -1

    return 0