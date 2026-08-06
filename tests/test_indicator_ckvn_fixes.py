# -*- coding: utf-8 -*-
"""Tests cho các fix audit Exness -> CKVN (AUDIT_INDICATOR_CKVN.md F1/F2/F3)."""

from types import SimpleNamespace

import pandas as pd

import config
import core.data_engine as data_engine_module
import core.market_hours as market_hours
from core.data_engine import DataEngine
from signals import simple_breakout, swing_point, volume


# ---------------------------------------------------------------- F1: cửa sổ fetch nến
def test_fetch_bars_window_factor(monkeypatch):
    engine = DataEngine()
    captured = {}

    def fake_get_ohlc(symbol, res, from_ts, to_ts):
        captured[res] = (from_ts, to_ts)
        return {"t": [1], "o": [1.0], "h": [1.0], "l": [1.0], "c": [1.0], "v": [1.0]}

    fake_dnse = SimpleNamespace(market_type_for_symbol=lambda _s: "STOCK", get_ohlc=fake_get_ohlc)
    monkeypatch.setattr(data_engine_module, "dnse_api", fake_dnse)
    monkeypatch.setattr(config, "DNSE_OHLC_WINDOW_FACTOR_INTRADAY", 8.0, raising=False)
    monkeypatch.setattr(config, "DNSE_OHLC_WINDOW_FACTOR_DAILY", 1.6, raising=False)

    engine._fetch_bars("FPT", "1h", 100, {}, None)
    f, t = captured["1H"]
    # Cửa sổ phải = 100 nến × 3600s × hệ số 8 (TT VN mở ~5h/ngày, không phải 24h như forex)
    assert (t - f) == int(100 * 3600 * 8.0)

    engine._fetch_bars("FPT", "1d", 100, {}, None)
    f, t = captured["1D"]
    assert (t - f) == int(100 * 86400 * 1.6)


def test_fetch_bars_window_factor_never_below_1(monkeypatch):
    engine = DataEngine()
    captured = {}

    def fake_get_ohlc(symbol, res, from_ts, to_ts):
        captured[res] = (from_ts, to_ts)
        return {"t": [1], "o": [1.0], "h": [1.0], "l": [1.0], "c": [1.0], "v": [1.0]}

    fake_dnse = SimpleNamespace(market_type_for_symbol=lambda _s: "STOCK", get_ohlc=fake_get_ohlc)
    monkeypatch.setattr(data_engine_module, "dnse_api", fake_dnse)
    monkeypatch.setattr(config, "DNSE_OHLC_WINDOW_FACTOR_INTRADAY", 0.0, raising=False)

    engine._fetch_bars("FPT", "15m", 50, {}, None)
    f, t = captured["15"]
    assert (t - f) >= 50 * 900  # config 0/hỏng -> tối thiểu giữ cửa sổ gốc


# ---------------------------------------------------------------- F2: volume nến chưa đóng
def _vol_df(spike_on_closed=True):
    rows = 30
    vols = [1000.0] * rows
    if spike_on_closed:
        vols[-2] = 5000.0
    else:
        vols[-1] = 5000.0
    return pd.DataFrame({
        "open": [10.0] * rows,
        "close": [10.5] * rows,  # nến xanh
        "volume": vols,
    })


def test_volume_uses_closed_bar_in_session(monkeypatch):
    monkeypatch.setattr(market_hours, "is_symbol_trade_window_open", lambda _s: (True, ""))
    ctx = {"symbol": "FPT"}
    # Spike nằm ở nến ĐÃ ĐÓNG -> trong phiên vẫn bắt được
    assert volume.get_signal_vector(_vol_df(spike_on_closed=True), {}, ctx) == 1
    # Spike nằm ở nến ĐANG CHẠY -> trong phiên bỏ qua (chưa đủ dữ liệu phiên)
    assert volume.get_signal_vector(_vol_df(spike_on_closed=False), {}, ctx) == 0


def test_volume_full_df_when_market_closed(monkeypatch):
    monkeypatch.setattr(market_hours, "is_symbol_trade_window_open", lambda _s: (False, "closed"))
    # Ngoài phiên nến cuối đã final -> xét bình thường
    assert volume.get_signal_vector(_vol_df(spike_on_closed=False), {}, {"symbol": "FPT"}) == 1


def test_volume_legacy_without_context():
    # Không có context/symbol -> giữ nguyên hành vi cũ (xét nến cuối)
    assert volume.get_signal_vector(_vol_df(spike_on_closed=False), {}, None) == 1


# ---------------------------------------------------------------- F3: fallback ATR theo giá
def test_simple_breakout_fallback_scales_with_price():
    rows = 10
    df = pd.DataFrame({
        "open": [1300.0] * rows,
        "high": [1300.0] * rows,
        "low": [1295.0] * rows,
        "close": [1300.0] * (rows - 1) + [1300.5],  # vượt đỉnh cũ 0.5 điểm
    })
    # Không context: fallback = 0.1% × 1300.5 ≈ 1.3 điểm đệm -> vượt 0.5 điểm CHƯA đủ breakout
    # (fallback cũ 0.0005 ≈ 0 đệm sẽ bắn BUY sai)
    assert simple_breakout.get_signal_vector(df, {"lookback": 1, "atr_buffer": 1.0}, None) == 0
    # Có ATR thật trong context thì dùng ATR thật
    assert simple_breakout.get_signal_vector(df, {"lookback": 1, "atr_buffer": 1.0}, {"atr_G2": 0.2}) == 1


def test_swing_point_fallback_gives_usable_tolerance():
    rows = 60
    highs = [103.0] * rows
    lows = [101.0] * rows
    closes = [102.0] * rows
    highs[30] = 105.0   # swing high (xa giá -> không kích SELL)
    lows[40] = 100.0    # swing low
    closes[-1] = 100.04  # chạm đáy trong dung sai 0.1% × 0.5 = ~0.05
    df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
    # Fallback cũ 0.0005 tuyệt đối -> dung sai ~0.00025, tín hiệu chết. Fallback mới theo giá -> BUY.
    assert swing_point.get_signal_vector(df, {"lookback": 50, "strength": 2, "atr_buffer": 0.5}, None) == 1
