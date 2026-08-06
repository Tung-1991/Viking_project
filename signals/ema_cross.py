# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    f = params.get("fast", 9)
    s = params.get("slow", 21)
    
    col_f = f"EMA_{f}"
    col_s = f"EMA_{s}"
    
    if col_f not in df.columns or col_s not in df.columns or len(df) < 2:
        return 0
        
    fast_curr, slow_curr = df[col_f].iloc[-1], df[col_s].iloc[-1]
    fast_prev, slow_prev = df[col_f].iloc[-2], df[col_s].iloc[-2]
    
    # Giao cắt hướng lên
    if fast_prev <= slow_prev and fast_curr > slow_curr: 
        return 1
    # Giao cắt hướng xuống
    if fast_prev >= slow_prev and fast_curr < slow_curr: 
        return -1
        
    return 0