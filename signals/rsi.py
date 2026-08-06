# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    p = params.get("period", 14)
    upper = params.get("upper", 70)
    lower = params.get("lower", 30)
    
    col = f"RSI_{p}"
    
    if col not in df.columns:
        return 0
        
    rsi_val = df[col].iloc[-1]
    
    if rsi_val <= lower: return 1
    if rsi_val >= upper: return -1
    
    return 0