# -*- coding: utf-8 -*-

import config
from core.money import (
    format_vnd,
    money_input_from_display,
    money_input_to_display,
    money_unit_note,
    parse_money_unit_label,
)


def test_default_zero_trim_000(monkeypatch):
    monkeypatch.setattr(config, "MONEY_DISPLAY_ZERO_TRIM", "000", raising=False)

    assert format_vnd(1000) == "1"
    assert format_vnd(99337000) == "99,337"
    assert format_vnd(99922359) == "99,922"
    assert format_vnd(-620000, signed=True) == "-620"
    assert format_vnd(-785746, signed=True) == "-786"
    assert format_vnd(-37, signed=True) == "0"
    assert money_unit_note() == "Hiển thị tiền: bỏ 000 (1 = 1.000 VND)"


def test_zero_trim_none_and_six_zeros(monkeypatch):
    monkeypatch.setattr(config, "MONEY_DISPLAY_ZERO_TRIM", "NONE", raising=False)
    assert format_vnd(1000000) == "1,000,000"

    monkeypatch.setattr(config, "MONEY_DISPLAY_ZERO_TRIM", "000 000", raising=False)
    assert format_vnd(1000000) == "1"
    assert format_vnd(7210) == "0"
    assert parse_money_unit_label("triệu VND") == "M_VND"


def test_unit_display_round_trip():
    from core.money import unit_from_display, unit_to_display

    assert unit_to_display("USD") == "VND"
    assert unit_from_display("VND") == "USD"
    for unit in ("R", "%Equity", "PERCENT", "POINT", "ATR", "%R"):
        assert unit_to_display(unit) == unit
        assert unit_from_display(unit) == unit
    assert unit_from_display(unit_to_display("USD")) == "USD"
    assert unit_to_display(None) == "VND"
    assert unit_from_display(None) == "USD"


def test_money_settings_always_use_full_vnd_regardless_of_display(monkeypatch):
    monkeypatch.setattr(config, "MONEY_DISPLAY_ZERO_TRIM", "000", raising=False)

    assert money_input_to_display(500000, "USD") == "500000"
    assert money_input_to_display(5787, "VND") == "5787"
    assert money_input_from_display("500000", "VND") == 500000.0
    assert money_input_from_display("5787", "USD") == 5787.0

    monkeypatch.setattr(config, "MONEY_DISPLAY_ZERO_TRIM", "000 000", raising=False)
    assert money_input_to_display(5000000, "VND") == "5000000"
    assert money_input_from_display("5000000", "VND") == 5000000.0
    for unit in ("R", "%Equity", "PERCENT", "POINT", "ATR"):
        assert money_input_to_display(0.5, unit) == "0.5"
        assert money_input_from_display("0.5", unit) == 0.5
    value = money_input_from_display(money_input_to_display(25000, "USD"), "VND")
    assert value == 25000.0
    assert money_input_from_display("", "VND") == 0.0
    assert money_input_to_display(None, "VND") == "0"
