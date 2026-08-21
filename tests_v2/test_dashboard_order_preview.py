from __future__ import annotations

from viking_v2.dashboard.actions import DashboardActionsMixin


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Label:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


def _main_quote_subject(order_type: str, price: str = ""):
    subject = DashboardActionsMixin()
    subject.order_type = _Value(order_type)
    subject.price = _Value(price)
    subject._current_tick_price = 69.2
    subject._current_market_status = "CLOSED"
    subject._current_tick = {
        "price": 69.2,
        "reference": 70.8,
        "open": 71.0,
        "timestamp": 1.0,
    }
    subject.lbl_price = _Label()
    subject.lbl_change = _Label()
    subject.lbl_market = _Label()
    return subject


def test_auction_preview_uses_expected_price_during_matching_session():
    price, label = DashboardActionsMixin._auction_preview_price(
        "ATO", 69.2, {"expected_price": 69.5, "open": 68.0}, "ATO"
    )
    assert price == 69.5
    assert label == "DỰ KHỚP ATO"


def test_auction_preview_uses_latest_open_and_close_outside_matching_session():
    ato_price, ato_label = DashboardActionsMixin._auction_preview_price(
        "ATO", 69.2, {"open": 70.1, "reference": 70.8}, "CLOSED"
    )
    atc_price, atc_label = DashboardActionsMixin._auction_preview_price(
        "ATC", 69.2, {"open": 70.1, "reference": 70.8}, "CLOSED"
    )
    assert (ato_price, ato_label) == (70.1, "ATO GẦN NHẤT")
    assert (atc_price, atc_label) == (69.2, "ATC GẦN NHẤT")


def test_atc_preview_before_atc_uses_previous_close_reference():
    price, label = DashboardActionsMixin._auction_preview_price(
        "ATC", 72.0, {"reference": 70.8}, "OPEN"
    )
    assert price == 70.8
    assert label == "ATC GẦN NHẤT"


def test_buy_button_follows_session_while_otp_is_reported_separately():
    assert DashboardActionsMixin._buy_button_presentation("", True, True)[0] == "ĐẶT"
    assert DashboardActionsMixin._buy_button_presentation("", False, True)[0] == "CACHE"
    assert DashboardActionsMixin._buy_button_presentation("", True, False)[0] == "ĐẶT"
    assert DashboardActionsMixin._buy_button_presentation("Sai giá", True, True)[0] == "KIỂM TRA"


def test_main_quote_follows_ato_selection_instead_of_staying_on_live_price():
    subject = _main_quote_subject("ATO")

    subject._refresh_main_quote_display()

    assert subject.lbl_price.options["text"] == "71,000"
    assert subject.lbl_market.options["text"] == "PHIÊN · CLOSED"


def test_main_quote_follows_limit_price_selection():
    subject = _main_quote_subject("LO", "70,500")

    subject._refresh_main_quote_display()

    assert subject.lbl_price.options["text"] == "70,500"
    assert subject.lbl_market.options["text"] == "PHIÊN · CLOSED"
