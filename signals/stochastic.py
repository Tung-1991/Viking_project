# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    k = params.get("k", 14)
    d = params.get("d", 3)
    sm = params.get("smooth", 3)
    upper = params.get("upper", 80)
    lower = params.get("lower", 20)
    
    col_k = f"STOCHk_{k}_{d}_{sm}"
    col_d = f"STOCHd_{k}_{d}_{sm}"
    
    if col_k not in df.columns or col_d not in df.columns:
        return 0
        
    k_val = df[col_k].iloc[-1]
    d_val = df[col_d].iloc[-1]
    
    # Giao cắt ở vùng quá bán
    if k_val < lower and k_val > d_val: return 1
    # Giao cắt ở vùng quá mua
    if k_val > upper and k_val < d_val: return -1
    
    return 0