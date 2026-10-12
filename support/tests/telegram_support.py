"""Explicit trading-session context for offline notification fixtures."""
from datetime import datetime

from viking_v2.trading.market import VN_TZ


TELEGRAM_START = datetime(2026, 10, 12, 9, 15, tzinfo=VN_TZ).timestamp()


def set_technical_test_session(subject):
    subject._technical_signal_status = {
        "market_status": "OPEN", "working_dates": ["2026-10-12"],
        "symbol_exchanges": {symbol: "HOSE" for symbol in
                             ["HDB", "MSN", "IDC", "FPT", "CTS"]
                             + [f"S{index:02d}" for index in range(150)]
                             + [f"S{index:03d}" for index in range(150)]},
    }
