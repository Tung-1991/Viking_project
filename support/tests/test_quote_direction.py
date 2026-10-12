"""Price direction backgrounds preserve reference colour and order inputs."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from viking_v2.dashboard.quote_direction import QuoteDirection, TICK_DOWN_BG, TICK_UP_BG, quote_background
from viking_v2.dashboard.view import COL_PREVIEW_TEXT, COL_RED, COL_SURFACE_2
from viking_v2.trading.market import VN_TZ


START = datetime(2026, 10, 12, 14, 0, tzinfo=VN_TZ).timestamp()


def tick(stamp, symbol="HDB", **changes):
    return {"symbol": symbol, "source": "WS", "timestamp": stamp, **changes}


def observe(state, price, index=0, symbol="HDB", **changes):
    now = START + index
    return state.observe(symbol, price, tick(now, symbol, **changes), "OPEN", now=now)


def test_up_down_equal_keep_latest_direction_and_do_not_mutate_tick():
    state = QuoteDirection()
    assert observe(state, 22.3) == COL_SURFACE_2
    assert observe(state, 22.4, 1) == TICK_UP_BG
    assert observe(state, 22.35, 2) == TICK_DOWN_BG
    assert observe(state, 22.35, 3) == TICK_DOWN_BG
    assert observe(state, 22.35, 4) == TICK_DOWN_BG
    assert observe(state, 22.4, 5) == TICK_UP_BG
    source = tick(START + 5)
    before = deepcopy(source)
    assert state.observe("HDB", 22.4, source, "OPEN", now=START + 5) == TICK_UP_BG
    assert source == before


@pytest.mark.parametrize("changes", [
    {"stale": True}, {"frozen": True}, {"price_frozen": True}, {"health": "REST_UNAVAILABLE"},
    {"source": "CLOSE"}, {"symbol": "MSN"}, {"timestamp": 0}, {"timestamp": float("nan")},
    {"timestamp": START - 11}, {"timestamp": START + 20},
])
def test_invalid_or_old_data_resets_and_next_valid_quote_starts_neutral(changes):
    state = QuoteDirection()
    observe(state, 22.3)
    assert observe(state, 22.4, 1) == TICK_UP_BG
    assert state.observe("HDB", 22.5, tick(START + 2, **changes), "OPEN", now=START + 2) == COL_SURFACE_2
    assert observe(state, 22.6, 3) == COL_SURFACE_2


@pytest.mark.parametrize("phase", ["LUNCH", "CLOSED", "HOLIDAY", "CALENDAR_UNKNOWN"])
def test_outside_session_resets_old_direction(phase):
    state = QuoteDirection()
    observe(state, 22.3)
    observe(state, 22.4, 1)
    assert state.observe("HDB", 22.4, tick(START + 2), phase, now=START + 2) == COL_SURFACE_2
    assert observe(state, 22.5, 3) == COL_SURFACE_2


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), None])
def test_bad_prices_clear_background(price):
    state = QuoteDirection()
    observe(state, 22.3)
    observe(state, 22.4, 1)
    assert observe(state, price, 2) == COL_SURFACE_2


def test_symbol_day_and_display_rounding_do_not_create_false_moves():
    state = QuoteDirection()
    observe(state, 22.3)
    observe(state, 22.4, 1)
    assert observe(state, 40, 2, "MSN") == COL_SURFACE_2
    assert observe(state, 40.00001, 3, "MSN") == COL_SURFACE_2
    assert observe(state, 40.001, 4, "MSN") == TICK_UP_BG
    tomorrow = START + timedelta(days=1).total_seconds()
    assert state.observe("MSN", 41, tick(tomorrow, "MSN"), "OPEN", now=tomorrow) == COL_SURFACE_2


def test_older_redraw_does_not_overwrite_last_observation():
    state = QuoteDirection()
    observe(state, 22.3)
    observe(state, 22.4, 2)
    assert state.observe("HDB", 22.2, tick(START + 1), "OPEN", now=START + 3) == COL_SURFACE_2
    assert observe(state, 22.35, 4) == TICK_DOWN_BG


def test_labels_keep_independent_observations_without_new_timers():
    subject = SimpleNamespace()
    for cell in ("main", "preview"):
        assert quote_background(subject, cell, "HDB", 22.3, tick(START), "OPEN", now=START) == COL_SURFACE_2
        assert quote_background(subject, cell, "HDB", 22.4, tick(START + 1), "OPEN", now=START + 1) == TICK_UP_BG
    assert set(subject.__dict__) == {"_quote_direction_states"}


def test_main_quote_keeps_reference_colour_and_limit_price_inputs(monkeypatch):
    from support.tests.test_dashboard_order_preview import _main_quote_subject
    import viking_v2.dashboard.actions as actions
    import viking_v2.dashboard.quote_direction as colors
    subject = _main_quote_subject("MARKET")
    subject.symbol.value = "HDB"
    subject._current_market_status = "OPEN"
    clock = [START]
    monkeypatch.setattr(actions.time, "time", lambda: clock[0])
    monkeypatch.setattr(colors.time, "time", lambda: clock[0])
    for index, (price, background, text_color) in enumerate([
        (22.3, COL_SURFACE_2, COL_RED), (22.4, TICK_UP_BG, COL_RED),
        (22.35, TICK_DOWN_BG, COL_RED), (22.35, TICK_DOWN_BG, COL_RED),
        (22.5, TICK_UP_BG, COL_PREVIEW_TEXT),
    ]):
        clock[0] = START + index
        subject._current_tick = tick(clock[0], reference=22.5)
        subject._current_tick_price = price
        subject._refresh_main_quote_display()
        assert subject.lbl_price.options["fg_color"] == background
        assert subject.lbl_price.options["text_color"] == text_color
    subject.order_type.value = "LO"
    subject.price.value = "22,200"
    subject._refresh_main_quote_display()
    assert subject.lbl_price.options["text"] == "22,200"
    assert subject.lbl_price.options["fg_color"] == COL_SURFACE_2
    assert subject.price.value == "22,200"
