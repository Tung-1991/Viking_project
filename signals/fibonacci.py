# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    if not context:
        return 0
        
    # [FIX]: Lấy mốc Swing của G1 (hoặc G2) để đo Fibo, thay vì key cũ đã bị xóa
    sh = context.get("swing_high_G1", context.get("swing_high_trend", 0.0))
    sl = context.get("swing_low_G1", context.get("swing_low_trend", 0.0))
    
    if sh == 0.0 or sl == 0.0:
        return 0
        
    close = df['close'].iloc[-1]
    diff = sh - sl
    
    # Fibo thoái lui cơ bản
    fibo_382 = sl + diff * 0.382
    fibo_618 = sl + diff * 0.618
    
    # Bắt phản ứng giá với sai số từ giao diện (mặc định 0.1%)
    tolerance = float(params.get("tolerance", 0.001))
    if abs(close - fibo_618) / close < tolerance: return 1 
    if abs(close - fibo_382) / close < tolerance: return -1 
    
    return 0