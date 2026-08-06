# -*- coding: utf-8 -*-
import logging

import core.data_engine as de


def test_warn_ohlc_throttled_dedups_within_cooldown(caplog):
    de._OHLC_WARN_TS.clear()
    with caplog.at_level(logging.WARNING, logger="DataEngine"):
        de._warn_ohlc_throttled("FPT|15|empty", "OHLC invalid %s", "FPT")
        de._warn_ohlc_throttled("FPT|15|empty", "OHLC invalid %s", "FPT")
        de._warn_ohlc_throttled("FPT|15|empty", "OHLC invalid %s", "FPT")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "OHLC invalid" in r.getMessage()]
    assert len(warnings) == 1  # chỉ log 1 lần trong cooldown


def test_warn_ohlc_throttled_different_keys_each_log(caplog):
    de._OHLC_WARN_TS.clear()
    with caplog.at_level(logging.WARNING, logger="DataEngine"):
        de._warn_ohlc_throttled("FPT|15|empty", "OHLC invalid %s", "FPT")
        de._warn_ohlc_throttled("SSI|15|missing", "OHLC invalid %s", "SSI")
    warnings = [r for r in caplog.records if "OHLC invalid" in r.getMessage()]
    assert len(warnings) == 2  # key khác nhau -> log riêng
