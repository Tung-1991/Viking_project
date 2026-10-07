from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote

import pytest
import requests

from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.connections.dnse.signing import generate_signature_header
from viking_v2.models import OrderIntent


class Response:
    def __init__(self, status=200, data=None, headers=None, text=""):
        self.status_code = status
        self._data = {} if data is None else data
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._data


class Session:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.responses.pop(0) if self.responses else Response()
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass


def client(session):
    value = DNSEClient(api_key="key", api_secret="secret", account_no="123", session=session)
    value.connect()
    value.trading_token = "token"
    value.trading_token_expires_at = value._now() + 1000
    value.cash_package = lambda _symbol: {"id": 1, "initialRate": 1, "brokerFirmBuyingFeeRate": 0.0015}
    return value


def test_signature_matches_hmac_contract():
    date = "Thu, 06 Aug 2026 01:00:00 +0000"
    nonce = "abc"
    signature, actual_date = generate_signature_header("key", "secret", "GET", "/accounts", date_value=date, nonce=nonce)
    signing = f"(request-target): get /accounts\nx-aux-date: {date}\nnonce: {nonce}"
    digest = hmac.new(b"secret", signing.encode(), hashlib.sha256).digest()
    expected = quote(base64.b64encode(digest).decode(), safe="")
    assert actual_date == date
    assert f'signature="{expected}"' in signature
    assert 'headers="(request-target) x-aux-date"' in signature
    assert 'nonce="abc"' in signature


@pytest.mark.parametrize("otp_type", ["email_otp", "smart_otp"])
def test_email_and_smart_otp_use_selected_type_and_create_trading_token(otp_type):
    session = Session([Response(data={"tradingToken": "fake-otp-token"})])
    value = client(session)
    value.otp_type = otp_type
    now = value._now()
    result = value.verify_otp("fake-code")
    assert result.ok and result.status == "TOKEN_READY"
    method, url, kwargs = session.calls[-1]
    assert method == "POST" and url.endswith("/registration/trading-token")
    assert kwargs["json"] == {"otpType": otp_type, "passcode": "fake-code"}
    assert value.trading_token == "fake-otp-token"
    assert now + 8 * 60 * 60 <= value.trading_token_expires_at <= value._now() + 8 * 60 * 60


@pytest.mark.parametrize(
    ("method", "expected_path", "expected_params"),
    [
        ("get_positions", "/accounts/123/positions", {"marketType": "STOCK", "pageSize": 1000}),
        ("get_orders", "/accounts/123/orders", {"marketType": "STOCK", "orderCategory": "NORMAL", "pageSize": 1000}),
        ("get_order_detail", "/accounts/123/orders/88", {"marketType": "STOCK", "orderCategory": "NORMAL"}),
        ("get_executions", "/accounts/123/executions/88", {"marketType": "STOCK", "orderCategory": "NORMAL"}),
    ],
)
def test_stock_endpoints_have_required_market_type(method, expected_path, expected_params):
    session = Session([Response(data={"positions": [], "orders": []})])
    value = client(session)
    if method in {"get_order_detail", "get_executions"}:
        getattr(value, method)("88")
    else:
        getattr(value, method)(force=True)
    _, url, kwargs = session.calls[-1]
    assert url.endswith(expected_path)
    assert kwargs["params"] == expected_params


def test_market_data_and_working_date_endpoints():
    session = Session([Response(data={}), Response(data={}), Response(data={}), Response(data={"workingDates": ["2026-08-06"]})])
    value = client(session)
    value.get_latest_trade("fpt")
    value.get_latest_quote("fpt")
    value.get_ohlc("fpt", "15", 1, 2)
    assert value.get_working_dates(force=True) == ["2026-08-06"]
    assert session.calls[0][1].endswith("/price/FPT/trades/latest")
    assert session.calls[2][2]["params"]["type"] == "STOCK"


def test_vnindex_ohlc_uses_index_market_type():
    session = Session([Response(data={"t": [], "o": [], "h": [], "l": [], "c": [], "v": []})])
    value = client(session)
    value.get_ohlc("VNINDEX", "1D", 1, 2)
    assert session.calls[0][2]["params"]["type"] == "INDEX"


def test_secdef_selects_round_lot_board_and_history_is_stock():
    session = Session(
        [
            Response(data=[{"boardId": "G4", "floorPrice": 1}, {"boardId": "G1", "floorPrice": 90}]),
            Response(data={"orders": [{"id": 1}]}),
        ]
    )
    value = client(session)
    assert value.get_secdef("fpt")["floorPrice"] == 90
    assert value.get_order_history("2026-08-01", "2026-08-06") == [{"id": 1, "price_unit": "VND"}]
    assert session.calls[-1][2]["params"]["marketType"] == "STOCK"


def test_stock_fee_rate_comes_from_dnse_cash_package_and_is_cached():
    session = Session(
        [
            Response(
                data={
                    "symbol": "FPT",
                    "marketType": "STOCK",
                    "loanPackages": [
                        {
                            "id": 1,
                            "type": "M",
                            "initialRate": 0.5,
                            "brokerFirmBuyingFeeRate": 0.00045,
                        },
                        {
                            "id": 2,
                            "type": "N",
                            "initialRate": 1,
                            "brokerFirmBuyingFeeRate": 0.0015,
                            "brokerFirmSellingFeeRate": 0.0018,
                        },
                    ],
                }
            )
        ]
    )
    value = client(session)

    assert value.get_stock_fee_rate("fpt", side="BUY") == pytest.approx(0.0015)
    assert value.get_stock_fee_rate("FPT", side="SELL") == pytest.approx(0.0018)
    assert len(session.calls) == 1
    assert session.calls[0][1].endswith("/accounts/123/loan-packages")
    assert session.calls[0][2]["params"] == {"marketType": "STOCK", "symbol": "FPT"}


def test_stock_fee_rate_is_unknown_when_dnse_omits_the_field():
    session = Session([Response(data={"loanPackages": [{"type": "N"}]})])
    value = client(session)

    assert value.get_stock_fee_rate("FPT", side="BUY") is None


def test_stock_fee_rate_ignores_promotional_zero_when_dnse_returns_a_paid_package():
    session = Session(
        [
            Response(
                data={
                    "loanPackages": [
                        {"type": "N", "brokerFirmBuyingFeeRate": 0},
                        {"type": "M", "brokerFirmBuyingFeeRate": 0.00045},
                    ]
                }
            )
        ]
    )
    value = client(session)

    assert value.get_stock_fee_rate("FPT", side="BUY") == pytest.approx(0.00045)


def test_order_intent_defaults_to_recheck_waiting_sell_condition():
    intent = OrderIntent.create("FPT", "SELL", 100, "MARKET")
    assert intent.sell_wait_policy == "RECHECK"


def test_place_replace_cancel_are_stock_normal(monkeypatch):
    session = Session(
        [
            Response(data={"id": "1", "orderStatus": "New"}),
            Response(data={"id": "1", "symbol": "FPT"}),
            Response(data={"id": "1"}),
            Response(data={"id": "1"}),
        ]
    )
    value = client(session)
    monkeypatch.setattr(value, "get_secdef", lambda _symbol: {"floorPrice": 90_000, "ceilingPrice": 110_000})
    result = value.place_order(OrderIntent.create("FPT", "BUY", 100, "LO", limit_price=100, execution_mode="REAL"))
    assert result.ok and result.order_id == "1"
    value.replace_order("1", price=101, quantity=100)
    value.cancel_order("1")
    assert [call[0] for call in session.calls] == ["POST", "GET", "PUT", "DELETE"]
    for _, _, kwargs in (session.calls[0], session.calls[2], session.calls[3]):
        assert kwargs["params"] == {"marketType": "STOCK", "orderCategory": "NORMAL"}
        assert kwargs["headers"]["trading-token"] == "token"
    assert session.calls[0][2]["json"]["orderType"] == "LO"
    assert session.calls[0][2]["json"]["price"] == 100_000.0
    assert session.calls[2][2]["json"]["price"] == 101_000.0
    assert session.calls[0][2]["json"]["remark"].startswith("V2:")


def test_market_intent_uses_exchange_supported_mtl(monkeypatch):
    session = Session([Response(data={"id": "1", "orderStatus": "New"})])
    value = client(session)
    monkeypatch.setattr(value, "get_secdef", lambda _symbol: {"marketId": "STO"})
    value.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    assert session.calls[0][2]["json"]["orderType"] == "MTL"
    assert session.calls[0][2]["json"]["price"] == 0.0


def test_upcom_market_order_fails_closed_before_submission(monkeypatch):
    session = Session()
    value = client(session)
    monkeypatch.setattr(value, "get_secdef", lambda _symbol: {"marketId": "UPX"})
    result = value.place_order(
        OrderIntent.create("ABC", "BUY", 100, "MARKET", execution_mode="REAL")
    )
    assert not result.ok and result.error == "UNSUPPORTED_MARKET_ORDER"
    assert session.calls == []


def test_rate_limit_retries_once(monkeypatch):
    session = Session([Response(429, {"message": "slow"}, {"Retry-After": "0.01"}), Response(200, {"ok": True})])
    value = client(session)
    monkeypatch.setattr("viking_v2.connections.dnse.client.time.sleep", lambda _delay: None)
    assert value.get_accounts() == {"ok": True}
    assert len(session.calls) == 2
    assert value.api_health()["total_requests"] == 2


def test_transport_timeout_reconciles_by_remark_without_resend(monkeypatch):
    session = Session([requests.Timeout("lost"), requests.Timeout("lost")])
    value = client(session)
    monkeypatch.setattr(value, "get_orders", lambda force=False: [])
    monkeypatch.setattr(value, "get_secdef", lambda _symbol: {"marketId": "STO"})
    result = value.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    assert not result.ok
    assert result.error == "ORDER_STATUS_UNKNOWN"
    assert len(session.calls) == 1


def test_expired_token_stops_before_network():
    session = Session()
    value = client(session)
    value.trading_token_expires_at = 0
    result = value.place_order(OrderIntent.create("FPT", "BUY", 100, "MARKET", execution_mode="REAL"))
    assert not result.ok
    assert result.error == "TRADING_TOKEN_REQUIRED"
    assert session.calls == []


def test_saved_trading_token_is_restored_only_before_expiry(monkeypatch):
    monkeypatch.setenv("DNSE_TRADING_TOKEN", "saved-token")
    monkeypatch.setenv("DNSE_TRADING_TOKEN_EXPIRES_AT", "2000")
    active = DNSEClient(
        api_key="key", api_secret="secret", account_no="123",
        session=Session(), now=lambda: 1000.0,
    )
    assert active.has_trading_token()
    assert active.trading_token == "saved-token"
    assert active.trading_token_seconds_left() == 1000.0

    expired = DNSEClient(
        api_key="key", api_secret="secret", account_no="123",
        session=Session(), now=lambda: 2000.0,
    )
    assert not expired.has_trading_token()
    assert expired.trading_token == ""
