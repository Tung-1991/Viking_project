# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    p = params.get("period", 14)
    strong = params.get("strong", 23)
    
    col_adx = f"ADX_{p}"
    col_dmp = f"DMP_{p}" # +DI
    col_dmn = f"DMN_{p}" # -DI
    
    if col_adx not in df.columns:
        return 0
        
    adx_val = df[col_adx].iloc[-1]
    dmp_val = df[col_dmp].iloc[-1]
    dmn_val = df[col_dmn].iloc[-1]
    
    if adx_val >= strong:
        if dmp_val > dmn_val: return 1
        if dmn_val > dmp_val: return -1
        
    return 0