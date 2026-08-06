# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    """ATR đóng vai trò Filter, trả về 1 (Pass) hoặc 0 (Block), KHÔNG có -1"""
    p = params.get("period", 14)
    mult = params.get("multiplier", 1.5)
    
    col_atr = f"ATRr_{p}"
    
    if col_atr not in df.columns or not context:
        return 0
        
    current_atr = df[col_atr].iloc[-1]
    
    # [FIX]: Cập nhật key theo DataEngine V4.2 (Ưu tiên lấy ATR của G1 làm mốc nền)
    h1_atr = context.get("atr_G1", context.get("atr_trend", 0.0))
    
    if h1_atr == 0.0:
        return 0
        
    # Volatility Expansion: Trả về 1 nếu ATR hiện tại lớn hơn mức nền
    return 1 if current_atr > (h1_atr * mult) else 0