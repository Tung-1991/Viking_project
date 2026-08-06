# -*- coding: utf-8 -*-
import pandas as pd

def get_signal_vector(df: pd.DataFrame, params: dict, context: dict = None) -> int:
    """Core Signal V3.0"""
    bullish_cols = ["CDL_ENGULFING", "CDL_MORNINGSTAR", "CDL_HAMMER"]
    bearish_cols = ["CDL_ENGULFING", "CDL_EVENINGSTAR", "CDL_SHOOTINGSTAR"]
    
    bull_score = sum(1 for c in bullish_cols if c in df.columns and df[c].iloc[-1] > 0)
    bear_score = sum(1 for c in bearish_cols if c in df.columns and df[c].iloc[-1] < 0)
    
    if bull_score > 0 and bear_score == 0: return 1
    if bear_score > 0 and bull_score == 0: return -1
    
    return 0

def get_pullback_confirmation(df_closed: pd.DataFrame, ema_closed: pd.Series, mc_config: dict) -> str:
    """Hàm Utility phục vụ logic nhồi lệnh (DCA/PCA) cho Trade Manager"""
    if df_closed.empty or ema_closed.empty:
        return "NONE"
        
    close = df_closed['close'].iloc[-1]
    open_p = df_closed['open'].iloc[-1]
    ema_val = ema_closed.iloc[-1]
    
    # Logic xác nhận cơ bản: Nến đóng cửa thuận chiều và vượt EMA
    if close > open_p and close > ema_val:
        return "BUY"
    if close < open_p and close < ema_val:
        return "SELL"
        
    return "NONE"