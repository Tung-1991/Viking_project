"""Display-only price direction; no timers, I/O or trading mutations."""
from dataclasses import dataclass
from datetime import datetime
import math
import time

from ..trading.market import VN_TZ
from ..trading.validation import quote_is_fresh, timestamp_value
from .view import COL_SURFACE_2


TICK_UP_BG = "#153D2B"
TICK_DOWN_BG = "#49262B"
PRICE_DIRECTION_HINT = (
    "Nền so với giá hiển thị trước đó của cùng mã: xanh tăng, đỏ giảm.\n"
    "Giá đứng giữ màu của lần đổi gần nhất. Đổi mã/giá cũ/ngoài phiên: nền xám.\n"
    "Màu chữ ô giá chính và ↑/↓ ở REF vẫn so với giá tham chiếu."
)


@dataclass
class QuoteDirection:
    symbol: str = ""
    price: float = 0.0
    observed_at: float = 0.0
    background: str = COL_SURFACE_2

    def observe(self, symbol, price, tick, phase, *, frozen=False, now=None):
        checked = time.time() if now is None else now
        symbol = str(symbol).strip().upper()
        stamp = timestamp_value(tick.get("received_at", tick.get("timestamp"))) if isinstance(tick, dict) else 0
        try:
            price = float(price)
        except (TypeError, ValueError, OverflowError):
            price = 0.0
        valid = (
            symbol and math.isfinite(price) and price > 0
            and str(phase).upper() in {"OPEN", "CONTINUOUS", "ATO", "ATC"}
            and not frozen and quote_is_fresh(tick, symbol, now=checked)
            and not tick.get("price_frozen") and str(tick.get("source", "")).upper() != "CLOSE"
            and checked - stamp <= 10
        )
        if not valid:
            self.symbol, self.price, self.observed_at, self.background = symbol, 0.0, 0.0, COL_SURFACE_2
            return self.background
        # Compare the same rounded VND that the label shows, not invisible
        # floating-point differences in the thousand-VND internal price.
        price = round(price * 1000 if price < 1000 else price)
        if symbol != self.symbol or not self.price or (
            datetime.fromtimestamp(stamp, VN_TZ).date() != datetime.fromtimestamp(self.observed_at, VN_TZ).date()
        ):
            self.symbol, self.price, self.observed_at, self.background = symbol, price, stamp, COL_SURFACE_2
        elif stamp < self.observed_at:
            return COL_SURFACE_2  # An older redraw cannot change the comparison baseline.
        else:
            if price != self.price:
                self.background = TICK_UP_BG if price > self.price else TICK_DOWN_BG
            self.price, self.observed_at = price, stamp
        return self.background


def quote_background(subject, cell, symbol, price, tick, phase, *, frozen=False, now=None):
    states = getattr(subject, "_quote_direction_states", None)
    if states is None:
        subject._quote_direction_states = states = {}
    state = states.get(cell)
    if state is None:
        states[cell] = state = QuoteDirection()
    return state.observe(symbol, price, tick, phase, frozen=frozen, now=now)
