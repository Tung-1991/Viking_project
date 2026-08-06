# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    f = params.get("fast", 12)
    s = params.get("slow", 26)
    sig = params.get("signal", 9)
    
    col_m = f"MACD_{f}_{s}_{sig}"
    col_h = f"MACDh_{f}_{s}_{sig}" # Histogram
    
    if col_m not in df.columns or col_h not in df.columns:
        return 0
        
    macd_val = df[col_m].iloc[-1]
    hist_val = df[col_h].iloc[-1]
    
    # MACD trên 0 và Histogram dương
    if macd_val > 0 and hist_val > 0: return 1
    # MACD dưới 0 và Histogram âm
    if macd_val < 0 and hist_val < 0: return -1
    
    return 0