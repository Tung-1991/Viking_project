# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    p = params.get("period", 20)
    std = float(params.get("std_dev", 2.0))
    
    col_l = f"BBL_{p}_{std}"
    col_u = f"BBU_{p}_{std}"
    
    if col_l not in df.columns or col_u not in df.columns:
        return 0
        
    close = df['close'].iloc[-1]
    
    # Chạm hoặc đâm thủng band dưới -> Mua (1)
    if close <= df[col_l].iloc[-1]: 
        return 1
    # Chạm hoặc đâm thủng band trên -> Bán (-1)
    if close >= df[col_u].iloc[-1]: 
        return -1
        
    return 0