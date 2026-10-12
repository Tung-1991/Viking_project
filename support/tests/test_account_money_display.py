"""NAV and available cash are distinct; account display does not trade."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from viking_v2 import config
from viking_v2.dashboard.account_money import account_money, account_money_hint
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.connections.dnse.client import DNSEClient


def test_reported_nav_is_never_replaced_by_50m_available_cash():
    for balance in ({"netAssetValue": 49951941, "stock": {"availableCash": 50000000}},
                    {"stock": {"netAssetValue": 49951941, "availableCash": 50000000}}):
        result = account_money(balance, [])
        assert result["value"] == 49951941 and result["kind"] == "NAV"
        assert result["available"] == 50000000


def test_nav_estimate_uses_total_cash_and_debt_with_holdings_not_available_cash():
    # Synthetic components to verify arithmetic, not a retrieved live payload.
    balance = {"stock": {"totalCash": 50000000, "availableCash": 50000000, "totalDebt": 13608059}}
    positions = [{"symbol": "HDB", "openQuantity": 600, "marketPrice": 22600, "price_unit": "VND"}]
    before = deepcopy((balance, positions))
    result = account_money(balance, positions)
    assert result["value"] == 49951941 and result["kind"] == "ESTIMATED_NAV"
    assert result["stock_value"] == 13560000
    assert before == (balance, positions)
    hint = account_money_hint(result)
    assert "50,000,000" in hint and "13,608,059" in hint and "13,560,000" in hint
    assert "có thể khác NAV trên DNSE" in hint


@pytest.mark.parametrize("price,unit", [(22600, "VND"), (22.6, "BOARD")])
def test_position_units_and_waiting_tplus_shares_are_included(price, unit):
    balance = {"stock": {"totalCash": 36391941, "availableCash": 30000000, "totalDebt": 0}}
    positions = [{"symbol": "HDB", "openQuantity": 600, "tradeQuantity": 0, "marketPrice": price, "price_unit": unit}]
    assert account_money(balance, positions)["value"] == 49951941


@pytest.mark.parametrize("position", [
    {"openQuantity": 600, "costPrice": 22650},
    {"marketPrice": 22600},
    {"openQuantity": 600, "marketPrice": "nan"},
])
def test_missing_current_valuation_does_not_invent_nav_from_entry_price(position):
    result = account_money({"stock": {"totalCash": 50000000, "availableCash": 50000000, "totalDebt": 13000000}}, [position])
    assert result["kind"] == "CASH" and result["value"] == 50000000


@pytest.mark.parametrize("nav", [None, "", "nan", "inf", "bad"])
def test_invalid_or_absent_nav_does_not_claim_zero_assets(nav):
    result = account_money({"netAssetValue": nav, "stock": {"availableCash": 50000000}}, [])
    assert result["value"] == 50000000 and result["kind"] == "CASH"


def test_explicit_zero_nav_is_authoritative():
    assert account_money({"netAssetValue": 0, "stock": {"availableCash": 50000000}}, [])["value"] == 0


def test_missing_cash_is_distinct_from_actual_zero_cash():
    assert account_money({"stock": {}}, [])["value"] is None
    assert account_money({"stock": {"availableCash": 0}}, [])["value"] == 0


def test_updated_broker_nav_replaces_cached_prior_snapshot_without_orders():
    clock = [1000.0]
    responses = [{"netAssetValue": 50000000, "stock": {"availableCash": 50000000}},
                 {"netAssetValue": 49951941, "stock": {"availableCash": 36391941}}]
    client = DNSEClient(api_key="OFFLINE", api_secret="OFFLINE", account_no="OFFLINE", now=lambda: clock[0])
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET" and path.endswith("/balances")
        return True, deepcopy(responses[len(calls)-1]), 200, ""
    client._request = request
    first = client.get_balance()
    clock[0] += 6
    second = client.get_balance()
    assert account_money(first, [])["value"] == 50000000
    assert account_money(second, [])["value"] == 49951941
    assert len(calls) == 2 and first["netAssetValue"] == 50000000


@pytest.mark.parametrize("nav,expected,caption", [
    (49951941, "49,951,941 ₫", "TỔNG TÀI SẢN / NAV"),
    (None, "49,951,941 ₫", "NAV ƯỚC TÍNH · API"),
])
def test_actual_account_card_keeps_nav_separate_from_cash_without_changing_settings(nav, expected, caption):
    class Label:
        def configure(self, **values):
            self.values = values
    balance = {"stock": {"totalCash": 50000000, "availableCash": 50000000, "totalDebt": 13608059}}
    if nav is not None:
        balance["netAssetValue"] = nav
    positions = [{"symbol": "HDB", "openQuantity": 600, "marketPrice": 22600, "price_unit": "VND"}]
    app = DashboardActionsMixin()
    app.mode, app.account_id = SimpleNamespace(get=lambda: "REAL"), "OFFLINE"
    app.settings = config.AppSettings()
    before = app.settings.to_dict()
    app.snapshots = {"REAL": (balance, positions, [])}
    app.trade_state = SimpleNamespace(list_cycles=lambda: [])
    app.daily_fees = SimpleNamespace(summary=lambda *_args, **_kwargs: {})
    for name in ("equity", "equity_title", "account", "pnl", "cash"):
        setattr(app, f"lbl_{name}", Label())
    app._paint_account()
    assert app.lbl_equity.values["text"] == expected
    assert app.lbl_equity_title.values["text"] == caption
    assert app.lbl_account.values["text"].endswith("TIỀN KHẢ DỤNG 50,000,000")
    assert app.settings.to_dict() == before
