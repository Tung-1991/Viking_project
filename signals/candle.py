# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    # Các cột được tính sẵn bởi ta.cdl_pattern trong DataEngine
    cols = ["CDL_ENGULFING", "CDL_HAMMER", "CDL_SHOOTINGSTAR"]
    
    for c in cols:
        if c in df.columns:
            val = df[c].iloc[-1]
            if val > 0: return 1
            if val < 0: return -1
            
    return 0