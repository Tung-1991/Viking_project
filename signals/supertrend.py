# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    p = params.get("period", 10)
    m = float(params.get("multiplier", 3.0))
    
    # Cột hướng của Supertrend (1 là Up, -1 là Down)
    col_dir = f"SUPERTd_{p}_{m}"
    
    if col_dir not in df.columns:
        return 0
        
    val = df[col_dir].iloc[-1]
    
    if val == 1: return 1
    if val == -1: return -1
    
    return 0