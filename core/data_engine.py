# -*- coding: utf-8 -*-
# FILE: core/data_engine.py
# V4.2.1: DYNAMIC TA OPTIMIZATION & MULTI-TIMEFRAME (KAISER EDITION)

import pandas as pd
import numpy as np
import copy
import json
import os
import logging
import threading
import time
from collections import deque
import config
import pandas_ta as ta 
from core.storage_manager import get_brain_settings_for_symbol
from core.market_structure import analyze_market_structure, write_structure_context
from core.dnse_connector import DNSEConnector
from core.dnse_ws import market_ws
from core.market_hours import is_symbol_network_window_open, is_symbol_trade_window_open

logger = logging.getLogger("DataEngine")

# Throttle cảnh báo OHLC lỗi lặp lại (cùng symbol+lý do): chỉ log lại sau N giây,
# tránh phình log khi 1 mã hỏng dữ liệu theo từng nhịp quét.
_OHLC_WARN_TS = {}
_OHLC_WARN_COOLDOWN = 300.0  # 5 phút


def _warn_ohlc_throttled(key, msg, *args):
    now = time.time()
    if (now - _OHLC_WARN_TS.get(key, 0.0)) >= _OHLC_WARN_COOLDOWN:
        logger.warning(msg, *args)
        _OHLC_WARN_TS[key] = now

# Khởi tạo DNSE API trực tiếp từ .env
dnse_api = DNSEConnector(
    api_key=os.getenv("DNSE_API_KEY", ""),
    api_secret=os.getenv("DNSE_API_SECRET", ""),
    account_no=os.getenv("DNSE_ACCOUNT_NO", "")
)

class DataEngine:
    def __init__(self):
        self.tf_map = {
            "1m": "1", "5m": "5", "15m": "15",
            "30m": "30", "1h": "1H", "4h": "1H",
            "1d": "1D"
        }
        self._last_tick = {}  # Cache tick real-time: {symbol: {bid, ask, last, ...}}
        self._bars_cache = {}
        self._ws_started = False
        # App UI sẽ chuyển sang consumer ngay khi dựng xong; daemon giữ owner=True.
        # Mặc định True bảo toàn hành vi cho script/test dùng DataEngine độc lập.
        self._market_data_owner = True
        self._shared_tick_provider = None
        self._market_state_provider = None
        self._market_state = "RECOVERING"
        self._market_state_since = time.time()
        self._ws_unavailable_since = 0.0
        self._ws_symbol_requested_at = {}
        self._rest_budget_lock = threading.Lock()
        self._rest_symbol_calls = deque()
        self.cache_stats = {
            "tick_hits": 0,
            "tick_misses": 0,
            "ohlc_hits": 0,
            "ohlc_misses": 0,
            "ws_hits": 0,
            "shared_hits": 0,
            "rest_fallbacks": 0,
            "rest_budget_skips": 0,
        }

    def configure_market_data_owner(self, owner, tick_provider=None, state_provider=None):
        """Chọn vai trò nguồn giá.

        owner=True: được mở market WS và gọi latest REST. owner=False: chỉ đọc feed
        chia sẻ do daemon cung cấp, tuyệt đối không tự gọi endpoint latest.
        """
        self._market_data_owner = bool(owner)
        self._shared_tick_provider = tick_provider
        self._market_state_provider = state_provider
        if not self._market_data_owner:
            try:
                market_ws.set_market_data_enabled(False)
            except Exception:
                pass

    def market_data_state(self):
        if not self._market_data_owner and callable(self._market_state_provider):
            try:
                state = str(self._market_state_provider() or "").upper()
                if state:
                    return state
            except Exception:
                pass
        return self._market_state

    def _set_market_state(self, state, reason=""):
        state = str(state or "RECOVERING").upper()
        if state != self._market_state:
            old = self._market_state
            self._market_state = state
            self._market_state_since = time.time()
            logger.info("Market data: %s -> %s%s", old, state, f" ({reason})" if reason else "")

    @staticmethod
    def _decorate_tick(tick, source, state):
        if not tick:
            return None
        row = dict(tick)
        now = time.time()
        ts = float(row.get("timestamp", row.get("tick_timestamp", 0.0)) or 0.0)
        row["timestamp"] = ts or now
        row["source"] = str(source or row.get("source") or "CACHE").upper()
        row["market_state"] = str(state or row.get("market_state") or "RECOVERING").upper()
        row["age_seconds"] = max(0.0, now - row["timestamp"])
        # Cache luôn là GIÁ CŨ, kể cả khi kết nối WebSocket toàn hệ thống
        # vẫn LIVE nhờ các mã khác.
        live_source = row["source"] in ("WS", "REST")
        live_state = row["market_state"] in ("LIVE", "REST FALLBACK", "REST_FALLBACK")
        row["freshness"] = "LIVE" if live_source and live_state else "STALE"
        row["stale"] = row["freshness"] != "LIVE"
        return row

    def _shared_tick(self, symbol):
        if not callable(self._shared_tick_provider):
            return None
        try:
            raw = self._shared_tick_provider(str(symbol).upper())
        except Exception:
            raw = None
        if not raw:
            return None
        state = str(raw.get("market_state") or self.market_data_state() or "RECOVERING").upper()
        tick = self._decorate_tick(raw, raw.get("source", "CACHE"), state)
        if tick:
            self._last_tick[str(symbol).upper()] = tick
            self.cache_stats["shared_hits"] += 1
        return tick

    def _claim_rest_symbol_budget(self):
        limit = max(0.0, float(getattr(config, "DNSE_MARKET_REST_MAX_SYMBOLS_PER_SECOND", 2.0) or 0.0))
        if limit <= 0:
            return False
        now = time.time()
        with self._rest_budget_lock:
            while self._rest_symbol_calls and now - self._rest_symbol_calls[0] >= 1.0:
                self._rest_symbol_calls.popleft()
            if len(self._rest_symbol_calls) >= max(1, int(limit)):
                self.cache_stats["rest_budget_skips"] += 1
                return False
            self._rest_symbol_calls.append(now)
            return True

    def _get_brain_settings(self, symbol=None):
        return get_brain_settings_for_symbol(symbol)

    def _ensure_ws_symbol(self, symbol):
        """Khởi động WS (1 lần) và subscribe symbol khi streaming được bật."""
        if not self._market_data_owner or not getattr(config, "DNSE_WS_ENABLED", False) or not market_ws.available:
            return
        if not is_symbol_network_window_open(symbol, include_preopen=True)[0]:
            market_ws.set_market_data_enabled(False)
            self._set_market_state("SLEEP", "market closed")
            if not market_ws.is_running():
                self._ws_started = market_ws.start()
            return
        market_ws.set_market_data_enabled(True)
        if not self._ws_started or not market_ws.is_running():
            self._ws_started = market_ws.start()
        market_ws.subscribe([symbol])
        self._ws_symbol_requested_at.setdefault(str(symbol).upper(), time.time())

    def set_stream_symbols(self, symbols):
        """Đồng bộ danh sách mã đang stream với watchlist hiện tại (gọi từ UI/daemon)."""
        if not self._market_data_owner or not getattr(config, "DNSE_WS_ENABLED", False) or not market_ws.available:
            return
        symbols = [str(symbol).upper() for symbol in (symbols or []) if symbol]
        if not any(is_symbol_network_window_open(symbol, include_preopen=True)[0] for symbol in symbols):
            market_ws.set_symbols(symbols)
            market_ws.set_market_data_enabled(False)
            self._set_market_state("SLEEP", "market closed")
            if not market_ws.is_running():
                self._ws_started = market_ws.start()
            return
        market_ws.set_market_data_enabled(True)
        if not self._ws_started or not market_ws.is_running():
            self._ws_started = market_ws.start()
        market_ws.set_symbols(symbols)

    # =========================================================================
    # [TỐI ƯU CPU] CHỈ TÍNH TOÁN CÁC INDICATOR ĐƯỢC BẬT (ON)
    # =========================================================================
    def _apply_ta(self, df, inds_config, tsl_config=None):
        if df is None or df.empty: 
            return df
        try:
            def needs(name):
                cfg = inds_config.get(name, {}) or {}
                return bool(cfg.get("active"))

            # ADX
            if needs("adx"):
                p = int(inds_config["adx"].get("params", {}).get("period", 14))
                df.ta.adx(length=p, append=True)
            
            # EMA Cơ bản
            if needs("ema"):
                p = int(inds_config["ema"].get("params", {}).get("period", 50))
                df.ta.ema(length=p, append=True)
            
            # EMA Cross (Cần 2 đường EMA)
            if needs("ema_cross"):
                f = int(inds_config["ema_cross"].get("params", {}).get("fast", 9))
                s = int(inds_config["ema_cross"].get("params", {}).get("slow", 21))
                df.ta.ema(length=f, append=True)
                df.ta.ema(length=s, append=True)
            
            # RSI
            if needs("rsi"):
                p = int(inds_config["rsi"].get("params", {}).get("period", 14))
                df.ta.rsi(length=p, append=True)
            
            # MACD
            if needs("macd"):
                f = int(inds_config["macd"].get("params", {}).get("fast", 12))
                s = int(inds_config["macd"].get("params", {}).get("slow", 26))
                sig = int(inds_config["macd"].get("params", {}).get("signal", 9))
                df.ta.macd(fast=f, slow=s, signal=sig, append=True)
            
            # Bollinger Bands
            if needs("bollinger_bands"):
                p = int(inds_config["bollinger_bands"].get("params", {}).get("period", 20))
                std = float(inds_config["bollinger_bands"].get("params", {}).get("std_dev", 2.0))
                df.ta.bbands(length=p, std=std, append=True)
            
            # Supertrend
            if needs("supertrend"):
                p = int(inds_config["supertrend"].get("params", {}).get("period", 10))
                m = float(inds_config["supertrend"].get("params", {}).get("multiplier", 3.0))
                df.ta.supertrend(length=p, multiplier=m, append=True)
            
            # Stochastic
            if needs("stochastic"):
                k = int(inds_config["stochastic"].get("params", {}).get("k", 14))
                d = int(inds_config["stochastic"].get("params", {}).get("d", 3))
                sm = int(inds_config["stochastic"].get("params", {}).get("smooth", 3))
                df.ta.stoch(k=k, d=d, smooth_k=sm, append=True)
            
            # Parabolic SAR
            if needs("psar") or (tsl_config and "PSAR_STEP" in tsl_config):
                # Ưu tiên thông số từ TSL Config nếu có
                step = 0.02
                max_step = 0.2
                if tsl_config and "PSAR_STEP" in tsl_config:
                    step = float(tsl_config["PSAR_STEP"])
                    max_step = float(tsl_config["PSAR_MAX"])
                elif inds_config.get("psar"):
                    step = float(inds_config["psar"].get("params", {}).get("step", 0.02))
                    max_step = float(inds_config["psar"].get("params", {}).get("max_step", 0.2))
                
                df.ta.psar(af0=step, af=step, max_af=max_step, append=True)

            # Pivot Points (Standard Floor)
            if needs("pivot_points"):
                prev_h = df['high'].shift(1)
                prev_l = df['low'].shift(1)
                prev_c = df['close'].shift(1)
                df['PP'] = (prev_h + prev_l + prev_c) / 3
                df['R1'] = (2 * df['PP']) - prev_l
                df['S1'] = (2 * df['PP']) - prev_h

            # ATR
            if needs("atr"):
                p = int(inds_config["atr"].get("params", {}).get("period", 14))
                df.ta.atr(length=p, append=True)
            
            # Nến Nhật
            is_candle_active = needs("candle")
            is_mc_active = needs("multi_candle")
            if is_candle_active or is_mc_active:
                O, H, L, C = df['open'], df['high'], df['low'], df['close']
                body = (C - O).abs()
                upper_shadow = H - df[['open', 'close']].max(axis=1)
                lower_shadow = df[['open', 'close']].min(axis=1) - L
                df["CDL_ENGULFING"] = np.where((C > O) & (C.shift(1) < O.shift(1)) & (C > O.shift(1)) & (O < C.shift(1)), 100,
                                      np.where((C < O) & (C.shift(1) > O.shift(1)) & (C < O.shift(1)) & (O > C.shift(1)), -100, 0))
                df["CDL_HAMMER"] = np.where((lower_shadow > 2 * body) & (upper_shadow < 0.2 * body) & (C > O), 100, 0)
                df["CDL_SHOOTINGSTAR"] = np.where((upper_shadow > 2 * body) & (lower_shadow < 0.2 * body) & (C < O), -100, 0)
                df["CDL_MORNINGSTAR"] = np.where((C.shift(2) < O.shift(2)) & (body.shift(1) < body.shift(2)*0.3) & (C > O) & (C > O.shift(2) + body.shift(2)/2), 100, 0)
                df["CDL_EVENINGSTAR"] = np.where((C.shift(2) > O.shift(2)) & (body.shift(1) < body.shift(2)*0.3) & (C < O) & (C < O.shift(2) - body.shift(2)/2), -100, 0)
                
        except Exception as e:
            logger.error(f"Lỗi tính toán thư viện pandas-ta: {e}")
            
        return df

    @staticmethod
    def _indicator_signature(name, cfg):
        return (
            str(name),
            json.dumps((cfg or {}).get("params", {}), sort_keys=True, default=str),
        )

    @staticmethod
    def _effective_group_indicators(indicators, group, include_trend=False):
        """Resolve params cho đúng group nhưng không thay đổi config gốc."""
        resolved = {}
        for name, raw_cfg in (indicators or {}).items():
            if not isinstance(raw_cfg, dict) or not raw_cfg.get("active", False):
                continue
            groups = raw_cfg.get("groups", [raw_cfg.get("group", "G2")])
            if isinstance(groups, str):
                groups = [groups]
            if group not in groups:
                continue
            cfg = copy.deepcopy(raw_cfg)
            params = copy.deepcopy(cfg.get("params", {}))
            group_params = cfg.get("group_params", {})
            if isinstance(group_params, dict) and isinstance(group_params.get(group), dict):
                params.update(group_params[group])
            cfg["params"] = params
            cfg["groups"] = [group]
            resolved[name] = cfg
        return resolved

    def _apply_trade_and_check_ta(self, df, trade_config, check_config, tsl_config=None):
        """Tính hai bộ trên cùng DataFrame; cấu hình trùng nhau chỉ tính một lần."""
        if df is None or df.empty:
            return df
        calculated = {}
        check_columns = {}
        base_columns = set(df.columns)

        for source, indicators in (("trade", trade_config), ("check", check_config)):
            for name, cfg in (indicators or {}).items():
                if not isinstance(cfg, dict) or not cfg.get("active"):
                    continue
                signature = self._indicator_signature(name, cfg)
                if signature not in calculated:
                    before = set(df.columns)
                    self._apply_ta(df, {name: cfg}, None)
                    calculated[signature] = sorted(set(df.columns) - before)
                if source == "check":
                    check_columns[name] = list(calculated[signature])

        # TSL có thể cần PSAR dù cả TRADE lẫn CHECK đều không bật module PSAR.
        if tsl_config and "PSAR_STEP" in tsl_config:
            self._apply_ta(df, {}, tsl_config)

        # attrs đi cùng DataFrame.copy() trong cache và không tham gia logic trade.
        df.attrs["check_indicator_columns"] = check_columns
        df.attrs["market_columns"] = sorted(base_columns)
        return df

    def _fetch_bars(self, symbol, timeframe_val, num_bars, inds_config, tsl_config=None, check_config=None):
        res = self.tf_map.get(str(timeframe_val).lower(), "15")
        
        # Tính toán thời gian (from, to)
        # Giả sử lấy nến quá khứ, 1 cây nến 15 phút = 900 giây
        multiplier_map = {"1": 60, "5": 300, "15": 900, "30": 1800, "1h": 3600, "1d": 86400}
        seconds_per_bar = multiplier_map.get(str(res).lower(), 900)
        
        to_ts = int(time.time())
        # [FIX CKVN - Audit F1] Cửa sổ thời gian phải nhân hệ số phủ phiên: TT VN chỉ mở ~5h/ngày
        # (công thức gốc Exness giả định 24h) — không nhân thì khung intraday nhận thiếu nến trầm trọng
        # (100 nến 1H = 100 giờ lịch ≈ chỉ ~22 nến giao dịch thật) -> EMA/SMA period dài ra NaN im lặng.
        if str(res).lower() == "1d":
            window_factor = float(getattr(config, "DNSE_OHLC_WINDOW_FACTOR_DAILY", 1.6) or 1.0)
        else:
            window_factor = float(getattr(config, "DNSE_OHLC_WINDOW_FACTOR_INTRADAY", 8.0) or 1.0)
        from_ts = to_ts - int(num_bars * seconds_per_bar * max(window_factor, 1.0))

        cache_ttl = float(getattr(config, "DNSE_OHLC_CACHE_TTL_SECONDS", 30.0) or 0.0)
        effective_cache_ttl = max(cache_ttl, float(seconds_per_bar))
        # [24/7] Ngoài giờ giao dịch OHLC không đổi -> đóng băng cache để khỏi gọi API liên tục.
        market_open = True
        try:
            if isinstance(dnse_api, DNSEConnector):
                market_open = bool(is_symbol_trade_window_open(symbol)[0])
        except Exception:
            market_open = True
        if not market_open:
            closed_ttl = float(getattr(config, "DNSE_OHLC_CACHE_TTL_CLOSED_SECONDS", 1800.0) or 0.0)
            if closed_ttl > 0:
                effective_cache_ttl = max(effective_cache_ttl, closed_ttl)
        inds_key = tuple(sorted(
            (
                str(k),
                bool((v or {}).get("active")),
                bool((v or {}).get("is_trend")),
                json.dumps((v or {}).get("params", {}), sort_keys=True, default=str),
            )
            for k, v in (inds_config or {}).items()
            if isinstance(v, dict)
        ))
        check_key = tuple(sorted(
            (
                str(k),
                bool((v or {}).get("active")),
                json.dumps((v or {}).get("params", {}), sort_keys=True, default=str),
            )
            for k, v in (check_config or {}).items()
            if isinstance(v, dict)
        ))
        # Ngoài giờ: bucket đóng băng NHƯNG xoay theo khối 6h. Bucket -1 cố định cũ có bug:
        # app chạy 24/7 cache nến 1d TRƯỚC PHIÊN (data chốt hôm qua) -> SAU PHIÊN vẫn trúng
        # key đó -> lượt quét EOD nhận nến cũ, không bao giờ thấy nến chốt hôm nay.
        # Khối 6h (mốc ~07h/13h/19h/01h VN) tách sáng-trước-phiên khỏi chiều-sau-phiên.
        cache_bucket = (-int(to_ts // 21600) - 1) if not market_open else int(to_ts / max(1, seconds_per_bar))
        try:
            market_type = dnse_api.market_type_for_symbol(symbol)
        except Exception:
            market_type = "DERIVATIVE"
        cache_key = (str(symbol).upper(), market_type, res, int(num_bars), cache_bucket, inds_key, check_key)
        if effective_cache_ttl > 0:
            cached = self._bars_cache.get(cache_key)
            if cached and (time.time() - cached["ts"]) < effective_cache_ttl:
                self.cache_stats["ohlc_hits"] += 1
                return cached["df"].copy()
        # Ngoài giờ tuyệt đối không gọi endpoint market-data DNSE. Account APIs vẫn hoạt động.
        # Dùng bản cache mới nhất cùng symbol/tf
        # dù TTL đã hết; nếu app vừa khởi động và chưa có cache thì trả DataFrame rỗng.
        if not market_open:
            candidates = [
                cached
                for key, cached in self._bars_cache.items()
                if len(key) >= 4
                and key[0] == str(symbol).upper()
                and key[1] == market_type
                and key[2] == res
                and key[3] == int(num_bars)
            ]
            if candidates:
                self.cache_stats["ohlc_hits"] += 1
                return max(candidates, key=lambda item: float(item.get("ts", 0.0)))["df"].copy()
            return pd.DataFrame()
        self.cache_stats["ohlc_misses"] += 1
        
        data = dnse_api.get_ohlc(symbol, res, from_ts, to_ts)

        required_keys = ("t", "o", "h", "l", "c", "v")
        if not isinstance(data, dict):
            _warn_ohlc_throttled(f"{symbol}|{res}|empty", "OHLC invalid for %s tf=%s res=%s: empty response", symbol, timeframe_val, res)
            return pd.DataFrame()
        missing_keys = [key for key in required_keys if key not in data]
        if missing_keys:
            _warn_ohlc_throttled(f"{symbol}|{res}|missing", "OHLC invalid for %s tf=%s res=%s: missing %s", symbol, timeframe_val, res, missing_keys)
            return pd.DataFrame()
        invalid_keys = [key for key in required_keys if not hasattr(data.get(key), "__len__") or data.get(key) is None]
        if invalid_keys:
            _warn_ohlc_throttled(f"{symbol}|{res}|null", "OHLC invalid for %s tf=%s res=%s: null/non-list %s", symbol, timeframe_val, res, invalid_keys)
            return pd.DataFrame()
        lengths = {key: len(data.get(key)) for key in required_keys}
        if not lengths["t"]:
            return pd.DataFrame()
        if len(set(lengths.values())) != 1:
            _warn_ohlc_throttled(f"{symbol}|{res}|mismatch", "OHLC invalid for %s tf=%s res=%s: length mismatch %s", symbol, timeframe_val, res, lengths)
            return pd.DataFrame()
            
        df = pd.DataFrame({
            'time': data['t'],
            'open': data['o'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c'],
            'volume': data['v']
        })
        df['time'] = pd.to_datetime(df['time'], unit='s')

        # [Audit F1] Chẩn đoán thiếu nến: nhận < 60% số nến yêu cầu -> indicator period dài có nguy cơ NaN
        if len(df) < int(num_bars * 0.6):
            _warn_ohlc_throttled(
                f"{symbol}|{res}|short",
                "OHLC %s tf=%s res=%s: nhận %s/%s nến — indicator period dài (EMA50, SMA20...) có thể NaN",
                symbol, timeframe_val, res, len(df), num_bars,
            )

        # TRADE và CHECK dùng chung nến/API; CHECK không được đưa vào signal generator.
        df = self._apply_trade_and_check_ta(df, inds_config, check_config or {}, tsl_config)
        if effective_cache_ttl > 0:
            self._bars_cache[cache_key] = {"ts": time.time(), "df": df.copy()}
            # [Audit F1 follow-up] Cap cũ 128 chỉ đủ ~42 mã × 3 khung — watchlist lớn sẽ thrash
            # (mỗi vòng đá văng cache mã đầu -> gọi API lại vô ích). Nâng mặc định 512, chỉnh qua env.
            max_entries = int(getattr(config, "DNSE_OHLC_CACHE_MAX_ENTRIES", 512) or 512)
            if len(self._bars_cache) > max_entries:
                evict = max(32, max_entries // 8)
                oldest = sorted(self._bars_cache.items(), key=lambda item: item[1]["ts"])[:evict]
                for old_key, _ in oldest:
                    self._bars_cache.pop(old_key, None)
        
        return df

    def _calc_atr(self, df, period=14):
        if df.empty: return 0.0001
        current_price = float(df['close'].iloc[-1])
        safe_fallback = current_price * 0.0005 

        if len(df) < period + 1:
            try:
                mean_range = (df['high'] - df['low']).mean()
                return float(mean_range) if mean_range > 0 else safe_fallback
            except:
                return safe_fallback
        try:
            high, low, close_prev = df['high'], df['low'], df['close'].shift(1)
            tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
            atr = tr.rolling(window=period).mean().iloc[-1]
            return safe_fallback if pd.isna(atr) or atr <= 0 else float(atr)
        except:
            return safe_fallback

    def _calc_swings(self, df, lookback=10, strength=2):
        if df.empty: return 0.0, 0.0
        try:
            lookback = max(int(lookback or 10), 5)
            strength = max(int(strength or 2), 1)
            recent_df = df.tail(max(lookback, strength * 2 + 1))
            highs = [float(value) for value in recent_df["high"].values]
            lows = [float(value) for value in recent_df["low"].values]

            # "Swing" phải là pivot đã xác nhận gần nhất, không phải đỉnh/đáy
            # tuyệt đối của cả cửa sổ. Cách cũ lấy max/min 50 nến làm SL/TP
            # intraday phình ra nhiều phiên và không còn đúng nghĩa Swing Retest.
            sh = None
            sl = None
            for idx in range(len(recent_df) - strength - 1, strength - 1, -1):
                if sh is None:
                    left = highs[idx - strength:idx]
                    right = highs[idx + 1:idx + strength + 1]
                    if left and right and all(highs[idx] > value for value in left + right):
                        sh = highs[idx]
                if sl is None:
                    left = lows[idx - strength:idx]
                    right = lows[idx + 1:idx + strength + 1]
                    if left and right and all(lows[idx] < value for value in left + right):
                        sl = lows[idx]
                if sh is not None and sl is not None:
                    break

            # Cửa sổ quá thẳng có thể chưa tạo đủ pivot; khi đó mới fallback
            # về biên gần nhất của chính cửa sổ đã cấu hình.
            if sh is None:
                sh = float(recent_df["high"].max())
            if sl is None:
                sl = float(recent_df["low"].min())
            if pd.isna(sh) or pd.isna(sl): return float(df['high'].iloc[-1]), float(df['low'].iloc[-1])
            return float(sh), float(sl)
        except:
            current_close = float(df['close'].iloc[-1])
            return current_close * 1.001, current_close * 0.999 

    def fetch_data_v4(self, symbol):
        """Kéo độc lập 4 chuỗi dữ liệu cho G0, G1, G2, G3 và tính Cản/ATR cho tất cả"""
        settings = self._get_brain_settings(symbol)
        inds_config = settings.get("indicators", {})
        check_config = settings.get("check_indicators", {})
        tsl_config = settings.get("TSL_CONFIG", getattr(config, "TSL_CONFIG", {}))
        
        tfs = {
            "G0": settings.get("G0_TIMEFRAME", getattr(config, "G0_TIMEFRAME", "1d")),
            "G1": settings.get("G1_TIMEFRAME", getattr(config, "G1_TIMEFRAME", "1h")),
            "G2": settings.get("G2_TIMEFRAME", getattr(config, "G2_TIMEFRAME", "15m")),
            "G3": settings.get("G3_TIMEFRAME", getattr(config, "G3_TIMEFRAME", "15m"))
        }
        
        num_bars = settings.get("NUM_H1_BARS", 100)
        
        # Mỗi group resolve tham số riêng; vẫn chỉ có đúng một lần gọi OHLC/group.
        dfs = {}
        for grp, tf in tfs.items():
            trade_group = self._effective_group_indicators(inds_config, grp, include_trend=True)
            check_group = self._effective_group_indicators(check_config, grp)
            dfs[grp] = self._fetch_bars(
                symbol,
                tf,
                num_bars,
                trade_group,
                tsl_config,
                check_config=check_group,
            )
        
        if any(df.empty for df in dfs.values()):
            return None, None

        current_price = float(dfs["G2"]['close'].iloc[-1])
        
        context = {
            "symbol": symbol,
            "current_price": current_price,
            # Giữ đúng mapping group -> timeframe đã resolve theo từng mã.
            # UI/Telegram phải đọc mapping này thay vì dán nhãn bằng timeframe
            # global trong config.py (đặc biệt VN30F1M có symbol override).
            "group_timeframes": dict(tfs),
            "check_indicator_columns": {
                grp: copy.deepcopy(df.attrs.get("check_indicator_columns", {}))
                for grp, df in dfs.items()
            },
        }

        # [NEW V4.4 FINAL] Lấy giá trị PSAR hiện tại cho TSL PSAR_TRAIL
        try:
            tsl_cfg = settings.get("TSL_CONFIG", getattr(config, "TSL_CONFIG", {}))
            psar_grp = tsl_cfg.get("PSAR_GROUP", "G2")
            if "DYNAMIC" in psar_grp:
                psar_grp = "G2"
            elif psar_grp not in dfs:
                psar_grp = None
            context["psar_group_resolved"] = psar_grp

            for grp, df_psar in dfs.items():
                psar_cols = [c for c in df_psar.columns if c.startswith("PSARl_") or c.startswith("PSARs_")]
                for col in psar_cols:
                    val = df_psar[col].iloc[-1]
                    if not pd.isna(val):
                        context[f"psar_{grp}"] = float(val)
                        break

            if f"psar_{psar_grp}" in context:
                context["psar"] = context[f"psar_{psar_grp}"]
        except Exception:
            pass

        # [FIX V4.4] Đồng bộ thông số Lookback/Period với cấu hình Indicator thay vì hardcode
        swing_cfg = inds_config.get("swing_point", {})
        swing_params = swing_cfg.get("params", {}) if isinstance(swing_cfg, dict) else {}
        swing_group_params = swing_cfg.get("group_params", {}) if isinstance(swing_cfg, dict) else {}
        if not isinstance(swing_params, dict):
            swing_params = {}
        if not isinstance(swing_group_params, dict):
            swing_group_params = {}
        atr_period = int(inds_config.get("atr", {}).get("params", {}).get("period", 14))

        for grp in ["G0", "G1", "G2", "G3"]:
            df_grp = dfs[grp]
            resolved_swing_params = dict(swing_params)
            group_override = swing_group_params.get(grp, {})
            if isinstance(group_override, dict):
                resolved_swing_params.update(group_override)
            swing_lookback = max(int(resolved_swing_params.get("lookback", 50) or 50), 5)
            swing_strength = max(int(resolved_swing_params.get("strength", 2) or 2), 1)
            sh, sl = self._calc_swings(
                df_grp,
                lookback=swing_lookback,
                strength=swing_strength,
            )
            atr = self._calc_atr(df_grp, period=atr_period)
            
            context[f"swing_high_{grp}"] = float(sh)
            context[f"swing_low_{grp}"] = float(sl)
            context[f"atr_{grp}"] = float(atr)
            context[f"swing_lookback_{grp}"] = int(swing_lookback)
            context[f"swing_strength_{grp}"] = int(swing_strength)
            try:
                ema20 = df_grp["close"].ewm(span=20, adjust=False).mean().iloc[-1]
                context[f"ema20_{grp}"] = float(ema20)
                context[f"EMA_20_{grp}"] = float(ema20)
            except Exception:
                pass
            try:
                bb_mid = df_grp["close"].rolling(window=20).mean().iloc[-1]
                if not pd.isna(bb_mid):
                    context[f"bb_mid_{grp}"] = float(bb_mid)
            except Exception:
                pass
            write_structure_context(
                context,
                grp,
                analyze_market_structure(df_grp, lookback=swing_lookback, strength=swing_strength),
            )
            try:
                candle = df_grp.iloc[-1]
                context[f"open_{grp}"] = float(candle["open"])
                context[f"high_{grp}"] = float(candle["high"])
                context[f"low_{grp}"] = float(candle["low"])
                context[f"close_{grp}"] = float(candle["close"])
            except Exception:
                pass

        context["atr_entry"] = context["atr_G2"]
        context["atr_trend"] = context["atr_G1"]
        context["swing_high_entry"] = context["swing_high_G2"]
        context["swing_low_entry"] = context["swing_low_G2"]
        context["swing_high_trend"] = context["swing_high_G1"]
        context["swing_low_trend"] = context["swing_low_G1"]

        return dfs, context

    def fetch_and_prepare(self, symbol):
        """HÀM CŨ: Phục vụ chạy Bot Daemon bản cũ tránh crash"""
        settings = self._get_brain_settings(symbol)
        inds_config = settings.get("indicators", {})
        tsl_config = settings.get("TSL_CONFIG", getattr(config, "TSL_CONFIG", {}))
        
        tf_entry = settings.get("entry_timeframe", "15m")
        tf_trend = settings.get("trend_timeframe", "1h")
        num_entry = settings.get("NUM_M15_BARS", 100)
        num_trend = settings.get("NUM_H1_BARS", 100)

        df_entry = self._fetch_bars(symbol, tf_entry, num_entry, inds_config, tsl_config)
        df_trend = self._fetch_bars(symbol, tf_trend, num_trend, inds_config, tsl_config)

        if df_entry.empty or df_trend.empty:
            return None, None, None

        current_price = float(df_entry['close'].iloc[-1])
        swing_lookback = int(inds_config.get("swing_point", {}).get("params", {}).get("lookback", 50))
        atr_period = int(inds_config.get("atr", {}).get("params", {}).get("period", 14))

        atr_entry = self._calc_atr(df_entry, period=atr_period)
        atr_trend = self._calc_atr(df_trend, period=atr_period)
        swing_h_entry, swing_l_entry = self._calc_swings(df_entry, lookback=swing_lookback)
        swing_h_trend, swing_l_trend = self._calc_swings(df_trend, lookback=swing_lookback)
        
        wave_up_dist = swing_h_trend - swing_l_trend
        fibo_618_support = swing_h_trend - (wave_up_dist * 0.618)
        fibo_618_resistance = swing_l_trend + (wave_up_dist * 0.618)

        try:
            ema50 = float(df_trend['close'].ewm(span=50, adjust=False).mean().iloc[-1])
            trend_status = "UP" if current_price > ema50 else "DOWN"
        except:
            trend_status = "NONE"

        context = {
            "symbol": symbol, "current_price": float(current_price),
            "entry_timeframe": tf_entry, "trend_timeframe": tf_trend,
            "trend": trend_status, "atr_entry": float(atr_entry), "atr_trend": float(atr_trend),
            "swing_high_entry": float(swing_h_entry), "swing_low_entry": float(swing_l_entry),
            "swing_high_trend": float(swing_h_trend), "swing_low_trend": float(swing_l_trend),
            "fibo_618_support": float(fibo_618_support), "fibo_618_resistance": float(fibo_618_resistance)
        }

        return df_entry, df_trend, context

    def fetch_realtime_tick(self, symbol):
        """Lấy tick từ một nguồn duy nhất.

        Daemon (owner) ưu tiên WS và chỉ dùng REST sau thời gian chờ reconnect. UI
        (consumer) chỉ đọc feed chia sẻ, không bao giờ chạm latest REST.
        """
        symbol = str(symbol or "").upper()
        if not symbol:
            return None
        if not self._market_data_owner:
            shared = self._shared_tick(symbol)
            if shared:
                return shared
            cached = self._last_tick.get(symbol)
            return self._decorate_tick(cached, "CACHE", self.market_data_state())

        # Ngoài giờ/nghỉ trưa chỉ trả cache giá; market channels tắt nhưng trading WS
        # (order/position) vẫn kết nối, và không fallback REST market-data.
        enforce_session_gate = isinstance(dnse_api, DNSEConnector)
        market_open = bool(is_symbol_trade_window_open(symbol)[0]) if enforce_session_gate else True
        network_open = bool(is_symbol_network_window_open(symbol, include_preopen=True)[0]) if enforce_session_gate else True
        if not network_open:
            market_ws.set_market_data_enabled(False)
            self._set_market_state("SLEEP", "market closed")
            if getattr(config, "DNSE_WS_ENABLED", False) and market_ws.available and not market_ws.is_running():
                self._ws_started = market_ws.start()
            return self._decorate_tick(self._last_tick.get(symbol), "CACHE", self._market_state)

        ws_enabled = bool(getattr(config, "DNSE_WS_ENABLED", False) and market_ws.available)
        ws_connected = False
        if ws_enabled:
            self._ensure_ws_symbol(symbol)
            ws_connected = bool(market_ws.is_connected())
            if ws_connected:
                # Trạng thái toàn hệ thống phản ánh sức khỏe kết nối, không phản
                # ánh việc từng mã đã kịp có tick hay chưa.
                self._ws_unavailable_since = 0.0
                self._set_market_state("LIVE", "WebSocket connected")
            ws_tick = market_ws.latest_tick(symbol)
            try:
                ws_generation = int((market_ws.snapshot() or {}).get("connection_generation", 0) or 0)
            except Exception:
                ws_generation = 0
            tick_generation = int((ws_tick or {}).get("connection_generation", ws_generation) or 0)
            if ws_connected and ws_tick and (not ws_generation or tick_generation == ws_generation):
                # Một mã có thể đứng giá lâu. Kết nối/heartbeat khỏe mới là tiêu chí
                # nguồn sống, không phải tuổi của riêng tick đó.
                tick = self._decorate_tick(ws_tick, "WS", "LIVE")
                self._last_tick[symbol] = tick
                self.cache_stats["ws_hits"] += 1
                return tick

        now = time.time()
        fallback_delay = max(0.0, float(getattr(config, "DNSE_WS_FALLBACK_DELAY_SECONDS", 5.0) or 0.0))
        if ws_enabled:
            if ws_connected:
                waiting_since = float(self._ws_symbol_requested_at.get(symbol, now) or now)
            else:
                if not self._ws_unavailable_since:
                    self._ws_unavailable_since = now
                waiting_since = self._ws_unavailable_since
            if (now - waiting_since) < fallback_delay:
                # Socket khỏe nhưng riêng mã này chưa có tick: chỉ đánh dấu
                # tick của mã là RECOVERING, không hạ cả hệ thống.
                if not ws_connected:
                    self._set_market_state("RECOVERING", "waiting for WebSocket")
                return self._decorate_tick(self._last_tick.get(symbol), "CACHE", "RECOVERING")

        # Warm-up chỉ dùng để thiết lập WebSocket. REST market data chỉ được gọi khi
        # ATO/OPEN/ATC thực sự bắt đầu.
        if not market_open:
            return self._decorate_tick(self._last_tick.get(symbol), "CACHE", self._market_state)

        cache_ttl = float(getattr(config, "DNSE_TICK_CACHE_TTL_SECONDS", 2.0) or 0.0)
        cached = self._last_tick.get(symbol)
        if cached and cache_ttl > 0 and (time.time() - float(cached.get("timestamp", 0.0) or 0.0)) < cache_ttl:
            self.cache_stats["tick_hits"] += 1
            return self._decorate_tick(cached, cached.get("source", "CACHE"), self._market_state)
        if not self._claim_rest_symbol_budget():
            symbol_state = "RECOVERING" if ws_connected else self._market_state
            return self._decorate_tick(cached, "CACHE", symbol_state)
        self.cache_stats["tick_misses"] += 1
        tick_data = {"symbol": symbol}
        try:
            # 1. Giá khớp lệnh gần nhất
            trade = dnse_api.get_latest_trade(symbol)
            if trade:
                tick_data["last"] = float(trade.get("matchPrice", 0))
                tick_data["high"] = float(trade.get("highestPrice", 0))
                tick_data["low"] = float(trade.get("lowestPrice", 0))
                tick_data["open"] = float(trade.get("openPrice", 0))
                tick_data["trade_time"] = trade.get("time", "")
            
            # 2. Bid/Ask sổ lệnh gần nhất
            quote = dnse_api.get_latest_quote(symbol)
            if quote:
                bids = quote.get("bid", [])
                offers = quote.get("offer", [])
                if bids:
                    tick_data["bid"] = float(bids[0].get("price", 0))
                if offers:
                    tick_data["ask"] = float(offers[0].get("price", 0))
                tick_data["quote_time"] = quote.get("time", "")
            
            # 3. Fallback: nếu thiếu bid/ask thì dùng last price.
            # Đánh dấu synthetic_quote để UI biết spread=0 là giả (ngoài giờ/nghỉ trưa),
            # tránh hiển thị "Spread: 0.00" như thể sổ lệnh đang khớp sát.
            if "last" in tick_data:
                if "bid" not in tick_data:
                    tick_data["bid"] = tick_data["last"]
                    tick_data["synthetic_quote"] = True
                if "ask" not in tick_data:
                    tick_data["ask"] = tick_data["last"]
                    tick_data["synthetic_quote"] = True
            
            # 4. Tính spread
            if "bid" in tick_data and "ask" in tick_data:
                tick_data["spread"] = round(tick_data["ask"] - tick_data["bid"], 2)
            
            tick_data["timestamp"] = time.time()
            
            if "last" in tick_data or "bid" in tick_data:
                tick_data = self._decorate_tick(tick_data, "REST", "REST FALLBACK")
                self._last_tick[symbol] = tick_data
                # REST chỉ dự phòng cho riêng mã thiếu tick nếu socket vẫn khỏe.
                # Chỉ đổi trạng thái toàn hệ thống khi WebSocket thực sự mất.
                if not ws_connected:
                    self._set_market_state("REST FALLBACK", "WebSocket unavailable")
                self.cache_stats["rest_fallbacks"] += 1
                return tick_data
                
        except Exception as e:
            logger.error(f"fetch_realtime_tick({symbol}) error: {e}")

        if not ws_connected:
            self._set_market_state("MARKET DATA DOWN", "WebSocket and REST unavailable")
            state = "MARKET DATA DOWN"
        else:
            state = "RECOVERING"
        return self._decorate_tick(self._last_tick.get(symbol), "CACHE", state)

    def get_api_health_snapshot(self):
        stats = {}
        try:
            stats = dnse_api.get_api_health_snapshot()
        except Exception:
            stats = {}
        try:
            ws_info = market_ws.snapshot()
        except Exception:
            ws_info = {}
        return {
            "owner_pid": os.getpid() if self._market_data_owner else None,
            "role": "OWNER" if self._market_data_owner else "CONSUMER",
            "market_state": self.market_data_state(),
            "market_state_since": self._market_state_since,
            "connector": stats,
            "cache": dict(self.cache_stats),
            "cache_sizes": {
                "ticks": len(self._last_tick),
                "ohlc": len(self._bars_cache),
            },
            "ws": ws_info,
        }

data_engine = DataEngine()
