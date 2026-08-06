# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    step = params.get("step", 0.02)
    max_step = params.get("max_step", 0.2)
    
    col_l = f"PSARl_{step}_{max_step}"
    col_s = f"PSARs_{step}_{max_step}"
    
    # PSAR Long tồn tại (ko bị NaN) -> Xu hướng tăng
    if col_l in df.columns and pd.notna(df[col_l].iloc[-1]): return 1
    # PSAR Short tồn tại -> Xu hướng giảm
    if col_s in df.columns and pd.notna(df[col_s].iloc[-1]): return -1
    
    return 0