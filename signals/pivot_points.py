# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    # Tính tay bên DataEngine
    if "PP" not in df.columns or "S1" not in df.columns or "R1" not in df.columns:
        return 0
        
    close = df['close'].iloc[-1]
    s1 = df['S1'].iloc[-1]
    r1 = df['R1'].iloc[-1]
    
    if close <= s1: return 1
    if close >= r1: return -1
    
    return 0