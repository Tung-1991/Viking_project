# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    p = params.get("period", 50)
    col = f"EMA_{p}"
    
    if col not in df.columns:
        return 0
        
    close = df['close'].iloc[-1]
    ema_val = df[col].iloc[-1]
    
    if close > ema_val: return 1
    if close < ema_val: return -1
    
    return 0