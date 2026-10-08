"""Offline ticket feedback: missing data is not zero money; caps stay binding."""
from types import SimpleNamespace
from datetime import datetime

import pytest

from viking_v2 import config
from viking_v2.dashboard.actions import DashboardActionsMixin
from viking_v2.dashboard.panels import DashboardPanelsMixin, _auto_quantity_feedback
from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.rules.state import RuleStateStore
from viking_v2.models import OrderIntent
from viking_v2.services.daemon import tick_with_price_bound
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


class Label:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


def value(text):
    return SimpleNamespace(get=lambda: text)


@pytest.mark.parametrize("checks,budget,reason,waiting", [
    ({}, 0, "CHỜ TIỀN TÀI KHOẢN", True),
    ({"order_budget": 0, "available_cash": 0}, 0, "TIỀN KHẢ DỤNG = 0", False),
    ({"order_budget": 0, "available_cash": 50_000_000, "exposure": 0}, 0, "P1 CHƯA CHO PHÉP MUA", False),
    ({"order_budget": 0, "available_cash": 50_000_000, "exposure_room": 0}, 0, "HẾT ROOM P1", False),
    ({"order_budget": 0, "available_cash": 50_000_000, "no_compound_limited": True}, 0, "HẾT VỐN NO-COMPOUND", False),
    ({"order_budget": 0, "available_cash": 50_000_000}, 0, "NGÂN SÁCH MUA = 0", False),
    ({"order_budget": 1_000_000, "available_cash": 1_000_000}, 1_000_000, "THIẾU TIỀN CHO 100 CP", False),
    ({"order_budget": 1_000_000, "available_cash": 50_000_000}, 1_000_000, "VỐN AUTO CHƯA ĐỦ 100 CP", False),
])
def test_feedback_distinguishes_limits_from_missing_money(checks, budget, reason, waiting):
    feedback = _auto_quantity_feedback(checks, 74.2, budget, 0)
    assert feedback["reason"] == reason
    assert feedback["waiting"] is waiting


def ticket(tmp_path, mode="REAL", cash=50_000_000):
    class Ticket(DashboardPanelsMixin, DashboardActionsMixin):
        pass

    view = Ticket()
    view.settings = config.AppSettings.from_dict({
        "watchlist": ["MSN", "CTS", "HDB", "IDC"],
        "priority_symbols": ["MSN", "CTS", "HDB", "IDC"],
        "priority_capital_enabled": True, "priority_total_capital": 50_000_000,
        "priority_allocations": {symbol: {"limit_vnd": limit, "use_pct": 50}
                                 for symbol, limit in [("MSN", 15_000_000), ("CTS", 15_000_000),
                                                       ("HDB", 5_000_000), ("IDC", 15_000_000)]},
        "market_phase_override_enabled": True, "market_phase_override_exposure_pct": 100,
        "rule_parameters": {"max_positions": 4, "force_min_lot_enabled": True},
    })
    view.mode, view.symbol, view.order_type = value(mode), value("MSN"), value("MARKET")
    view.queue = OrderQueue(tmp_path / "queue.json")
    view.trade_state = TradeStateStore(tmp_path / "trades.json")
    view.rule_state = RuleStateStore(tmp_path / "rules.json")
    view.snapshots = {mode: ({"equity": cash, "availableCash": cash}, [], [])}
    view.quantity = Label()
    view.quantity.get = lambda: ""
    return view


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_msn_market_cap_50_percent_cannot_be_forced_over_budget(tmp_path, mode):
    view = ticket(tmp_path, mode)
    status = {"ticks": {"MSN": {"price": 74.2, "ceiling_price": 80.4}}}
    qty, budget, forced = view._suggested_order_quantity(74.2, status, "MSN")
    assert (qty, forced) == (0, False)
    assert budget == pytest.approx(7_500_000 / 1.00045)
    feedback = view._preview_auto_feedback
    assert feedback["reason"] == "HẠN MỨC CHƯA ĐỦ 100 CP"
    assert "80,400" in feedback["hint"]
    assert "15.00 tr × 50%" in feedback["hint"]
    assert "42.50 tr" in feedback["hint"]
    # A priced LO fits the same cap; no business rule or quantity was loosened.
    view.order_type = value("LO")
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100
    assert view.queue.list_all() == []
    assert view.trade_state.list_cycles() == []


def test_msn_zero_budget_explains_reserved_cap_not_empty_account(tmp_path):
    view = ticket(tmp_path, cash=40_000_000)
    assert view._suggested_order_quantity(74.2, {"ticks": {"MSN": {"ceiling_price": 80.4}}}, "MSN") == (0, 0, False)
    assert view._preview_auto_feedback["reason"] == "VỐN ĐÃ GIỮ CHO PRIORITY"
    assert "40,000,000 đ" in view._preview_auto_feedback["hint"]


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("pending_status", ["PENDING", "WAITING_TOKEN", "WORKING"])
def test_vps_pending_msn_buy_uses_remaining_cap_and_recovers_after_cancel(tmp_path, mode, pending_status):
    view = ticket(tmp_path, mode)
    view.settings.buy_fee_pct = 0.12
    view._fee_rates = {("MSN", "BUY"): 0.0012}
    view.settings.priority_allocations = {
        symbol: {"limit_vnd": (5 if symbol == "IDC" else 15) * 1_000_000, "use_pct": 100}
        for symbol in ("MSN", "CTS", "HDB", "IDC")
    }
    status = {"ticks": {"MSN": {"price": 74.2, "ceiling_price": 79.3}}}
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100
    intent = OrderIntent.create("MSN", "BUY", 100, "MARKET", execution_mode=mode, source="MANUAL")
    intent.details["reservation_price"] = 74.2
    intent.status = pending_status
    view.queue.add(intent)
    checks = view._preview_entry_checks("MSN", status)
    assert checks["available_cash"] == 50_000_000
    assert checks["symbol_pending_buy_count"] == 1
    assert checks["symbol_pending_buy_quantity"] == 100
    assert checks["priority_capital"]["pending_cost_vnd"] == pytest.approx(7_428_904)
    assert checks["priority_capital"]["holding_cost_vnd"] == 0
    quantity, budget, forced = view._suggested_order_quantity(74.2, status, "MSN")
    assert (quantity, forced) == (0, False)
    assert budget == pytest.approx((15_000_000 - 7_428_904) / 1.0012)
    feedback = view._preview_auto_feedback
    assert feedback["reason"] == "BUY ĐANG CHỜ"
    assert feedback["pending"] is True
    assert "100 CP chưa khớp" in feedback["hint"]
    assert "7.43 tr" in feedback["hint"] and "7.57 tr" in feedback["hint"] and "7.94 tr" in feedback["hint"]
    assert "hủy yêu cầu cũ" in feedback["hint"]
    assert len(feedback["hint"].splitlines()) == 5
    assert len(view.queue.list_all()) == 1
    if pending_status == "WORKING":
        # A real in-flight order is released only by a confirmed broker outcome.
        view.queue._update(intent.id, status="CANCELLED")
    else:
        assert view.queue.cancel_local(intent.id)
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100
    assert view._preview_auto_feedback["pending"] is False
    assert view._preview_entry_checks("MSN", status)["symbol_pending_buy_count"] == 0


def test_pending_real_buy_does_not_reserve_paper_money(tmp_path):
    view = ticket(tmp_path, "PAPER")
    view.settings.priority_allocations["MSN"]["use_pct"] = 100
    intent = OrderIntent.create("MSN", "BUY", 100, "MARKET", execution_mode="REAL")
    intent.details["reservation_price"] = 74.2
    view.queue.add(intent)
    status = {"ticks": {"MSN": {"ceiling_price": 79.3}}}
    checks = view._preview_entry_checks("MSN", status)
    assert checks["symbol_pending_buy_count"] == 0
    assert checks["priority_capital"]["pending_cost_vnd"] == 0
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_tiny_cash_reports_missing_money_before_reserved_priority(tmp_path, mode):
    view = ticket(tmp_path, mode=mode, cash=2192)
    view.settings.priority_allocations["MSN"]["use_pct"] = 100
    quantity, budget, forced = view._suggested_order_quantity(
        74.2, {"ticks": {"MSN": {"price": 74.2, "ceiling_price": 80.4}}}, "MSN",
    )
    assert (quantity, budget, forced) == (0, 0, False)
    feedback = view._preview_auto_feedback
    assert feedback["reason"] == "THIẾU TIỀN CHO 100 CP"
    assert not feedback["waiting"]
    assert "2,192 đ" in feedback["hint"] and "80,400" in feedback["hint"]
    assert "VỐN ĐÃ GIỮ" not in feedback["hint"]
    assert "không phải tiền sẵn có" in feedback["hint"]
    assert len(feedback["hint"].splitlines()) <= 5
    assert not view.queue.list_all()


def test_missing_bound_does_not_fabricate_a_minimum_lot_cash_failure():
    feedback = _auto_quantity_feedback(
        {"order_budget": 2192, "available_cash": 2192}, 74.2, 2192, 0, missing_bound=True,
    )
    assert feedback["waiting"] and feedback["reason"] == "CHỜ GIÁ TRẦN TÍNH KL"


def test_status_hover_reports_actual_price_error_not_previous_auto_failure():
    view = DashboardPanelsMixin()
    view.preview_status_reason = SimpleNamespace(cget=lambda _key: "Giá LO không hợp lệ")
    view._preview_auto_feedback = {"reason": "THIẾU TIỀN CHO 100 CP", "hint": "old cash failure"}
    assert view._order_status_hint() == "Giá LO không hợp lệ"


def test_paper_100m_without_own_cap_can_buy_msn(tmp_path):
    view = ticket(tmp_path, mode="PAPER", cash=100_000_000)
    view.settings.priority_capital_enabled = False
    qty, budget, forced = view._suggested_order_quantity(
        74.2, {"ticks": {"MSN": {"ceiling_price": 80.4}}}, "MSN")
    # Three other envelopes reserve their purchase fee as well.
    assert budget == pytest.approx((100_000_000 - 3 * 25_000_000 * 1.00045) / 1.00045)
    assert (qty, forced) == (300, False)
    assert view._preview_auto_feedback["reason"] == ""
    # Turning the explicit envelope back on caps the same 100m account at 7.5m.
    view.settings.priority_capital_enabled = True
    assert view._suggested_order_quantity(74.2, {"ticks": {"MSN": {"ceiling_price": 80.4}}}, "MSN")[0] == 0
    assert view._preview_auto_feedback["reason"] == "HẠN MỨC CHƯA ĐỦ 100 CP"


def test_paper_switch_and_reference_recovery_never_misreport_zero_cash(tmp_path, monkeypatch):
    view = ticket(tmp_path, mode="PAPER", cash=99_973_842.9)
    value = DNSEClient(api_key="fake", api_secret="fake", account_no="offline")
    clock = [datetime(2026, 10, 8, 14, tzinfo=VN_TZ).timestamp()]
    value._now = lambda: clock[0]
    responses = iter([(False, None, 429, "rate limited"),
                      (True, {"ceilingPrice": 80.4}, 200, ""),
                      (False, None, 503, "unavailable")])
    monkeypatch.setattr(value, "_request", lambda *_a, **_kw: next(responses))
    tick = {"symbol": "MSN", "price": 74.2, "ceiling_price": 0}
    tick = tick_with_price_bound(tick, value.get_secdef("MSN"))
    status = {"ticks": {"MSN": tick}}
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 0
    assert view._preview_auto_feedback["reason"] == "CHỜ GIÁ TRẦN TÍNH KL"
    assert view._preview_auto_feedback["waiting"]
    assert view.snapshots["PAPER"][0]["availableCash"] == 99_973_842.9

    # A successful reference distinguishes a real envelope limit from API failure.
    clock[0] += 61
    status["ticks"]["MSN"] = tick_with_price_bound(tick, value.get_secdef("MSN"))
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 0
    assert view._preview_auto_feedback["reason"] == "HẠN MỨC CHƯA ĐỦ 100 CP"
    view.settings.priority_allocations["MSN"]["use_pct"] = 100
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100

    # A short outage cannot turn the recovered bound back into zero.
    clock[0] += 61
    status["ticks"]["MSN"] = tick_with_price_bound(tick, value.get_secdef("MSN"))
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100
    view.snapshots["REAL"] = ({"equity": 0, "availableCash": 0}, [], [])
    view.mode = value_control = SimpleNamespace(get=lambda: "REAL")
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 0
    value_control.get = lambda: "PAPER"
    assert view._suggested_order_quantity(74.2, status, "MSN")[0] == 100
    assert view.queue.list_all() == []
    assert view.trade_state.list_cycles() == []


@pytest.mark.parametrize("definition", [None, {}, {"ceilingPrice": 0},
                                        {"ceilingPrice": "bad"}, {"ceilingPrice": float("nan")}])
def test_expired_or_invalid_reference_cannot_reuse_old_tick_bound(definition):
    old = {"symbol": "MSN", "price": 74.2, "ceiling_price": 80.4}
    updated = tick_with_price_bound(old, definition)
    assert "ceiling_price" not in updated
    assert updated["price"] == 74.2
    assert old["ceiling_price"] == 80.4


@pytest.mark.parametrize("failed_book", ["REAL", "PAPER", "RECONCILE"])
def test_failed_account_refresh_does_not_discard_other_book(tmp_path, failed_book):
    from concurrent.futures import Future
    view = ticket(tmp_path, mode="PAPER", cash=100_000_000)
    failed_side = "REAL" if failed_book == "RECONCILE" else failed_book
    previous = ({"stock": {"availableCash": 123_000}}, [], [])
    view.snapshots = {failed_side: previous}
    view.histories = {}
    view.running, view._snapshot_busy, view.telegram = True, False, None
    view.real = SimpleNamespace(configured=lambda: True)
    view.logger = SimpleNamespace(warning=lambda *_args: None)
    view._refresh_local = lambda: None
    view._post_ui = lambda callback: callback()
    view.after = lambda *_args: None
    def snapshot(mode):
        if mode == failed_book:
            raise RuntimeError(f"OFFLINE {mode} FAILED")
        return ({"equity": 100_000_000, "stock": {"availableCash": 100_000_000}}, [], [{"id": mode}])
    def reconcile(*_args):
        if failed_book == "RECONCILE":
            raise RuntimeError("OFFLINE RECONCILE FAILED")
        return []
    def submit(work):
        result = Future()
        try:
            result.set_result(work())
        except Exception as error:
            result.set_exception(error)
        return result
    view.execution = SimpleNamespace(account_snapshot=snapshot, reconcile_external_sells=reconcile)
    view._io_executor = SimpleNamespace(submit=submit)
    view._refresh_snapshots()
    good_book = "REAL" if failed_book == "PAPER" else "PAPER"
    assert view.snapshots[good_book][0]["stock"]["availableCash"] == 100_000_000
    assert not view._snapshot_busy
    assert view.snapshots[failed_side] == previous
    if good_book == "PAPER":
        assert view.histories["PAPER"] == [{"id": "PAPER"}]
        view.settings.priority_capital_enabled = False
        assert view._suggested_order_quantity(74.2, {"ticks": {"MSN": {"ceiling_price": 80.4}}}, "MSN")[0] == 300


def test_missing_bound_updates_old_quantity_placeholder_and_waits(tmp_path):
    view = ticket(tmp_path)
    view._suggested_order_quantity(74.2, {"ticks": {"MSN": {"ceiling_price": 80.4}}}, "MSN")
    view.order_type = value("LO")
    assert view._suggested_order_quantity(74.2, {}, "MSN")[0] == 100
    assert view.quantity.options["placeholder_text"] == "100 CP"
    view.order_type = value("MARKET")
    assert view._suggested_order_quantity(74.2, {}, "MSN")[0] == 0
    assert view.quantity.options["placeholder_text"] == "AUTO"
    assert view._preview_auto_feedback["waiting"]
    assert view._preview_auto_feedback["reason"] == "CHỜ GIÁ TRẦN TÍNH KL"


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("manual", [False, True])
def test_full_preview_zero_cash_is_blocked_but_missing_snapshot_is_waiting(tmp_path, missing, manual):
    view = ticket(tmp_path, cash=0)
    if missing:
        view.snapshots["REAL"] = ({}, [], [])
    view.tp, view.sl = value("7%"), value("-3.5%")
    view._current_tick_price = 74.2
    view._symbol_exchange = lambda *_args: "HOSE"
    view._preview_buy_fee = lambda *_args: 0.0
    view._preview_indicator_details = lambda *_args: {}
    view._refresh_rule_preview = lambda *_args: None
    view._em_states = {}
    if manual:
        view.quantity.get = lambda: "100"
        view._preview_auto_feedback = {"hint": "WRONG BOOK OLD MONEY"}
    view.real = SimpleNamespace(has_trading_token=lambda: True)
    for name in ("order_title", "status_badge", "status_reason", "live_value", "entry_value",
                 "qty_title", "qty_value", "cash_value", "fee_value", "tp_value", "tp_detail",
                 "sl_value", "sl_detail", "route_value", "normal_value", "normal_detail",
                 "em_normal", "em_exit", "exit_value", "exit_detail", "atr", "atr_detail"):
        setattr(view, f"preview_{name}", Label())
    view.execute_button = Label()
    view._refresh_full_order_preview({"market_status": "CLOSED", "ticks": {"MSN": {"ceiling_price": 80.4}}})
    assert "WRONG BOOK" not in view._auto_quantity_hint()
    if manual:
        assert view.preview_qty_value.options["text"] == "100"
        assert view.preview_status_badge.options["text"] == "CACHE"
        assert view.queue.list_all() == []
        return
    assert view.preview_status_badge.options["text"] == ("CHỜ" if missing else "LỖI")
    assert view.preview_status_reason.options["text"] == ("CHỜ TIỀN TÀI KHOẢN" if missing else "TIỀN KHẢ DỤNG = 0")
    assert view.preview_route_value.options["text"] == ("CHỜ" if missing else "CHẶN")
    assert view.execute_button.options["text"] == ("CHỜ DỮ LIỆU" if missing else "KIỂM TRA")
    assert view.preview_qty_value.options["text"] == ("—" if missing else "< 100")
    assert view.queue.list_all() == []


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
def test_full_preview_pending_buy_is_a_compact_warning_not_a_system_error(tmp_path, mode):
    view = ticket(tmp_path, mode)
    view.settings.priority_allocations["MSN"]["use_pct"] = 100
    intent = OrderIntent.create("MSN", "BUY", 100, "MARKET", execution_mode=mode)
    intent.details["reservation_price"] = 74.2
    view.queue.add(intent)
    view.tp, view.sl = value("7%"), value("-3.5%")
    view._current_tick_price = 74.2
    view._symbol_exchange = lambda *_args: "HOSE"
    view._preview_buy_fee = lambda *_args: 0.0
    view._preview_indicator_details = lambda *_args: {}
    view._refresh_rule_preview = lambda *_args: None
    view._em_states = {}
    view.real = SimpleNamespace(has_trading_token=lambda: True)
    for name in ("order_title", "status_badge", "status_reason", "live_value", "entry_value",
                 "qty_title", "qty_value", "cash_value", "fee_value", "tp_value", "tp_detail",
                 "sl_value", "sl_detail", "route_value", "normal_value", "normal_detail",
                 "em_normal", "em_exit", "exit_value", "exit_detail", "atr", "atr_detail"):
        setattr(view, f"preview_{name}", Label())
    view.preview_status_reason.cget = lambda key: view.preview_status_reason.options[key]
    view.execute_button = Label()
    view._refresh_full_order_preview({"market_status": "CLOSED", "ticks": {"MSN": {"ceiling_price": 79.3}}})
    assert view.preview_status_badge.options["text"] == "BUY CHỜ"
    assert view.preview_status_reason.options["text"] == "BUY ĐANG CHỜ"
    assert view.preview_route_value.options["text"] == "CHỜ"
    assert view.execute_button.options["text"] == "BUY ĐANG CHỜ"
    assert view.execute_button.options["fg_color"] == "#4A3B16"
    assert "100 CP chưa khớp" in view._order_status_hint()
    assert "100 CP chưa khớp" in view._auto_quantity_hint()
    assert len(view.queue.list_all()) == 1


@pytest.mark.parametrize("balance,expected", [({}, "—"), ({"availableCash": 0}, "0")])
def test_account_cash_display_does_not_claim_zero_when_snapshot_is_missing(balance, expected):
    view = DashboardActionsMixin()
    view.mode, view.account_id = value("REAL"), "OFFLINE"
    view.settings = config.AppSettings()
    view.snapshots = {"REAL": (balance, [], [])}
    view.daily_fees = SimpleNamespace(summary=lambda *_args, **_kwargs: {})
    view.trade_state = SimpleNamespace(list_cycles=lambda: [])
    for name in ("equity", "account", "pnl", "cash"):
        setattr(view, f"lbl_{name}", Label())
    view._paint_account()
    assert view.lbl_account.options["text"].endswith(f"CASH {expected}")
    assert view.lbl_equity.options["text"] == ("0 ₫" if balance else "CHỜ TÀI KHOẢN")
