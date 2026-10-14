"""Matched-board isolation, safe legacy extrema, and durable ARM notification."""
from copy import deepcopy
from types import SimpleNamespace

import msgpack
import pytest

from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.connections.dnse.websocket import DNSEMarketWS
from viking_v2.models import OrderIntent, StrategyDecision
from viking_v2.rules.business import StaticRule, StaticRuleParameters
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.market import MarketDataService
from viking_v2.trading.quote_boards import MATCHED_DATA_POLICY
from viking_v2.trading.state import TradeStateStore
from viking_v2.trading.portfolio import PortfolioContextBuilder
from viking_v2.trading.execution import ExecutionService
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.validation import quote_diagnostics, quote_is_fresh
from support.tests.test_live_safety import service, Broker
from support.tests.test_business_flow_safety_regressions import tick, headless_app
from support.tests.test_telegram_buy_lost import subject
from support.tests.telegram_support import TELEGRAM_START


def test_subscriptions_use_only_regular_board():
    assert DNSEMarketWS._channels() == ["tick.G1.json", "top_price.G1.json", "expected_price.G1.json"]


@pytest.mark.parametrize("board", ["T1", "T2", "T3", "T4", "T6", "G4", "G3", "G7", "", "UNKNOWN"])
def test_other_board_cannot_change_price_book_extrema_volume_or_freshness(monkeypatch, board):
    clock = [1000.0]
    monkeypatch.setattr("viking_v2.connections.dnse.websocket.time.time", lambda: clock[0])
    ws = DNSEMarketWS("offline", "offline")
    ws._ingest({"symbol": "HDB", "boardId": "G1", "matchPrice": 23.25,
                "highestPrice": 23.45, "lowestPrice": 23.1, "totalVolumeTraded": 10000,
                "bid": [{"price": 23.2}], "offer": [{"price": 23.25}]})
    before = ws.latest_tick("HDB")
    clock[0] += 30
    ws._ingest({"symbol": "HDB", "boardId": board, "matchPrice": 24.6,
                "highestPrice": 24.6, "lowestPrice": 21.1, "totalVolumeTraded": 922823,
                "bid": [{"price": 24.6}], "offer": [{"price": 24.65}]})
    assert ws.latest_tick("HDB") == before
    assert not quote_is_fresh(before, "HDB", now=clock[0])


@pytest.mark.parametrize("encoding", ["json", "msgpack"])
def test_envelope_board_is_checked_and_retained(encoding):
    ws = DNSEMarketWS("offline", "offline")
    def send(channel, data):
        payload = {"channel": channel, "data": {"symbol": "HDB", **data}}
        ws._on_message(None, msgpack.packb(payload, use_bin_type=True) if encoding == "msgpack" else payload)
    send("tick.G1.json", {"matchPrice": 23.25, "time": "2026-10-14T02:40:20Z"})
    before = ws.latest_tick("HDB")
    assert before["boardId"] == "G1" and before["market_data_policy"] == MATCHED_DATA_POLICY
    assert before["exchange_time"] == "2026-10-14T02:40:20Z"
    send("tick.T1.json", {"boardId": "G1", "matchPrice": 24.6})
    send("tick.G1.json", {"boardId": "T1", "matchPrice": 24.6})
    assert ws.latest_tick("HDB") == before


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -10, "broken"])
def test_invalid_match_does_not_refresh_last_valid_price(monkeypatch, price):
    clock = [1000.0]
    monkeypatch.setattr("viking_v2.connections.dnse.websocket.time.time", lambda: clock[0])
    ws = DNSEMarketWS("offline", "offline")
    ws._ingest({"symbol": "HDB", "boardId": "G1", "matchPrice": 23.2})
    before = ws.latest_tick("HDB")
    clock[0] += 30
    ws._ingest({"symbol": "HDB", "boardId": "G1", "matchPrice": price})
    assert ws.latest_tick("HDB")["price"] == before["price"]
    assert ws.latest_tick("HDB")["received_at"] == before["received_at"]


@pytest.mark.parametrize("method,field", [("get_latest_trade", "trades"), ("get_latest_quote", "quotes")])
def test_rest_selects_g1_even_if_negotiated_board_arrives_first(monkeypatch, method, field):
    client = DNSEClient(api_key="offline", api_secret="offline")
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return True, {field: [{"symbol": "HDB", "boardId": "T1", "matchPrice": 24.6},
                             {"symbol": "HDB", "boardId": "G1", "matchPrice": 23.25}]}, 200, ""
    monkeypatch.setattr(client, "_request", request)
    result = getattr(client, method)("HDB")
    assert result["matchPrice"] == 23.25 and result["boardId"] == "G1"
    assert calls[0]["params"] == {"boardId": "G1"}


@pytest.mark.parametrize("data", [
    {"symbol": "HDB", "boardId": "T1", "matchPrice": 24.6},
    {"symbol": "FPT", "boardId": "G1", "matchPrice": 24.6},
    [{"symbol": "HDB", "matchPrice": 24.6}, {"symbol": "HDB", "matchPrice": 23.25}],
])
def test_rest_rejects_wrong_or_ambiguous_board(monkeypatch, data):
    client = DNSEClient(api_key="offline", api_secret="offline")
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: (True, {"trades": data}, 200, ""))
    assert client.get_latest_trade("HDB") is None


def test_single_legacy_rest_row_is_scoped_by_g1_request(monkeypatch):
    client = DNSEClient(api_key="offline", api_secret="offline")
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: (
        True, {"trades": {"symbol": "HDB", "matchPrice": 23.25}}, 200, ""))
    assert client.get_latest_trade("HDB")["boardId"] == "G1"


def test_quote_only_rest_cannot_make_old_matched_price_fresh():
    client = SimpleNamespace(get_latest_trade=lambda _s: None,
        get_latest_quote=lambda _s: {"symbol": "HDB", "boardId": "G1", "bid": [{"price": 23.2}]})
    market = MarketDataService(client, SimpleNamespace(latest_tick=lambda _s: None))
    assert market.get_tick("HDB") is None


def test_rest_normalization_does_not_merge_another_board_quote():
    result = MarketDataService._normalize("HDB",
        {"symbol": "HDB", "boardId": "G1", "matchPrice": 23.25},
        {"symbol": "HDB", "boardId": "G4", "bid": [{"price": 24.6}]})
    assert result["price"] == 23.25 and result["bid"] == 0


@pytest.mark.parametrize("changes,reason", [({"boardId": "T1"}, "WRONG_BOARD"),
                                          ({"boardId": "G1", "price_source": "EXPECTED"}, "INDICATIVE_PRICE")])
def test_execution_quote_guard_rejects_negotiated_or_indicative_price(changes, reason):
    quote = {**tick("HDB", 24.6), **changes}
    assert not quote_is_fresh(quote, "HDB")
    assert quote_diagnostics(quote, "HDB")["reason"] == reason


def test_matched_feed_never_arms_hdb_on_negotiated_trade(tmp_path):
    ws = DNSEMarketWS("offline", "offline")
    state = RuleStateStore(tmp_path / "rules.json")
    rule = StaticRule(StaticRuleParameters(normal_policy="AUTO", dynamic_policy="ALERT"))
    for board, price in [("G1", 22.65), ("G1", 23.45), ("T1", 24.6), ("T1", 21.1), ("G1", 23.2)]:
        ws._ingest({"symbol": "HDB", "boardId": board, "matchPrice": price})
        current = ws.latest_tick("HDB")["price"]
        metrics = state.update_position_metrics("HDB", "trade", profit_pct=(current / 22.65 - 1) * 100,
            market_price=current, net_pnl=(current - 22.65) * 600000,
            entry_notional_vnd=13_590_000, market_data_policy=MATCHED_DATA_POLICY)
        decision = rule.evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"},
            {"position": {"quantity": 600, "avg_price": 22.65, "current_price": current,
                          "trade_quantity": 600, "sl_enabled": False, "em_modes": ["NORMAL"], **metrics}})
        assert decision.action != "SELL" and decision.reason != "NORMAL_ARMED"
    assert metrics["peak_profit_pct"] == pytest.approx(3.53200883)
    assert metrics["mae_net_pnl"] == 0


def test_original_capital_denominator_survives_partial_sell(tmp_path):
    state = RuleStateStore(tmp_path / "rules.json")
    state.update_position_metrics("HDB", "trade", profit_pct=8.6092715,
        net_pnl=1_170_000, market_price=24.6, entry_notional_vnd=13_590_000)
    row = state.update_position_metrics("HDB", "trade", profit_pct=2.428256,
        net_pnl=320_000, market_price=23.2, entry_notional_vnd=9_060_000)
    assert row["mfe_net_pnl_pct"] == pytest.approx(8.6092715)
    assert row["current_net_pnl_pct"] == pytest.approx(2.35467255)
    assert row["entry_notional_vnd"] == 13_590_000


def test_legacy_peak_is_quarantined_until_explicit_verified_rebuild(tmp_path):
    state = RuleStateStore(tmp_path / "rules.json")
    state.update_position_metrics("HDB", "trade", profit_pct=8.6092715, net_pnl=1_170_000, market_price=24.6)
    state.arm_normal("HDB", "trade")
    row = state.update_position_metrics("HDB", "trade", profit_pct=2.428256, net_pnl=320_000,
        market_price=23.2, entry_notional_vnd=13_590_000, market_data_policy=MATCHED_DATA_POLICY)
    assert row["price_history_verified"] is False and row["peak_profit_pct"] > 7
    position = {"quantity": 400, "avg_price": 22.65, "current_price": 23.2,
                "trade_quantity": 400, "sl_enabled": False, "em_modes": ["NORMAL"], **row}
    decision = StaticRule().evaluate({"symbol": "HDB", "bars": [], "confirmed_market_state": "UPTREND"},
                                     {"position": position})
    assert decision.action == "WAIT" and decision.reason == "PROTECT_HISTORY_UNVERIFIED"
    trades = TradeStateStore(tmp_path / "trades.json")
    cycle = trades.create("HDB", "REAL", trade_id="trade")
    trades.record_buy_fill(cycle.id, 600, 22.65)
    trades.record_sell_fill(cycle.id, 200, 23.15)
    state.mark_protection_done("HDB", "trade", ["NORMAL_PROTECTION"], execution_id="first", completed=True)
    state.mark_protection_done("HDB", "trade", ["NORMAL_PROTECTION"], execution_id="second", completed=True)
    before = trades.get(cycle.id).to_dict()
    repaired = state.replace_verified_price_metrics("HDB", "trade", metrics={
        "peak_profit_pct": 3.53200883, "mfe_pct": 3.53200883, "mae_pct": -.8830022,
        "current_profit_pct": 2.428256, "market_price": 23.2, "current_net_pnl": 320000,
        "mae_net_pnl": -120000, "mfe_net_pnl": 480000, "entry_notional_vnd": 13590000,
    }, evidence={"boardId": "G1", "source": "reviewed matched history"})
    assert repaired["price_history_verified"] is True and "normal_armed" not in repaired
    assert repaired["normal_protection_count"] == 2
    assert repaired["price_history_before_repair"]["peak_profit_pct"] > 7
    assert trades.get(cycle.id).to_dict() == before and before["open_quantity"] == 400


@pytest.mark.parametrize("changes", [{"frozen": True}, {"stale": True}, {"boardId": "T1"},
                                     {"price_source": "EXPECTED"}])
def test_nonmatched_observation_cannot_change_extrema_or_trigger_stop_loss(tmp_path, changes):
    app = headless_app(tmp_path, balance={"equity": 100e6, "availableCash": 100e6},
        positions=[{"symbol": "FPT", "openQuantity": 600, "tradeQuantity": 600,
                    "costPrice": 22.65, "marketPrice": 23.2}], symbols=["FPT"])
    cycle = app.trade_state.create("FPT", "REAL", em_modes=["NORMAL"])
    app.trade_state.record_buy_fill(cycle.id, 600, 22.65)
    builder = PortfolioContextBuilder(app.queue, app.trade_state, app.rule_state, buy_fee_rate=lambda: 0)
    def build(quote):
        return builder.build("FPT", execution_mode="REAL", balance=app.snapshots["REAL"][0],
            positions=app.snapshots["REAL"][1], tick=quote, exposure=1, max_positions=4)
    matched = {**tick("FPT", 23.2), "boardId": "G1", "price_source": "MATCH",
               "market_data_policy": MATCHED_DATA_POLICY}
    build(matched)
    before = app.rule_state.position_metrics("FPT", cycle.id)
    portfolio = build({**matched, "price": 21.1, **changes})
    assert app.rule_state.position_metrics("FPT", cycle.id) == before
    decision = StaticRule().evaluate({"symbol": "FPT", "bars": [], "confirmed_market_state": "UPTREND"}, portfolio)
    assert decision.action == "WAIT" and decision.reason == "NO_MATCHED_POSITION_PRICE"


def test_verified_protect_intent_still_reaches_broker(tmp_path):
    app = headless_app(tmp_path, balance={"equity": 100e6, "availableCash": 100e6},
        positions=[{"symbol": "FPT", "openQuantity": 600, "tradeQuantity": 600,
                    "costPrice": 22.65, "marketPrice": 23.8}], symbols=["FPT"])
    cycle = app.trade_state.create("FPT", "REAL", em_modes=["NORMAL"], sl_enabled=False)
    app.trade_state.record_buy_fill(cycle.id, 600, 22.65)
    builder = PortfolioContextBuilder(app.queue, app.trade_state, app.rule_state, buy_fee_rate=lambda: 0)
    def quote(price):
        return {**tick("FPT", price), "boardId": "G1", "price_source": "MATCH",
                "market_data_policy": MATCHED_DATA_POLICY}
    def build(price):
        return builder.build("FPT", execution_mode="REAL", balance=app.snapshots["REAL"][0],
            positions=app.snapshots["REAL"][1], tick=quote(price), exposure=1, max_positions=4)
    build(24.6)  # Genuine G1 matched high in this synthetic control case.
    app.rule_state.arm_normal("FPT", cycle.id)
    portfolio = build(23.8)
    decision = StaticRule().evaluate({"symbol": "FPT", "bars": [], "confirmed_market_state": "UPTREND"}, portfolio)
    assert decision.action == "SELL"
    decision.details.update(trade_id=cycle.id, position_quantity=600)
    plan = app._plan_rule_decision(decision, quote(23.8), "REAL")
    assert plan.intent and app._protection_history_allowed(plan.intent)
    broker = Broker()
    engine = ExecutionService(broker, broker, app.queue, JSONLineJournal(tmp_path / "events.jsonl"),
        trade_state=app.trade_state, rule_state=app.rule_state, quote_provider=lambda _s: quote(23.8),
        protection_history_allowed_provider=app._protection_history_allowed)
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert [r.id for r in broker.sent] == [plan.intent.id]


def test_repaired_history_rejects_pre_repair_decision_and_intent(tmp_path):
    app = headless_app(tmp_path, balance={"equity": 100e6, "availableCash": 100e6}, positions=[], symbols=["FPT"])
    app.rule_state.update_position_metrics("FPT", "trade", profit_pct=8, market_price=24.5,
        net_pnl=1e6, entry_notional_vnd=13590000, market_data_policy=MATCHED_DATA_POLICY)
    stale = OrderIntent.create("FPT", "SELL", 100, "MARKET", trade_id="trade", source="EM",
        reason="NORMAL_PROTECTION", details={"market_data_policy": MATCHED_DATA_POLICY,
                                            "price_history_repaired_at": 0.0})
    assert app._protection_history_allowed(stale)
    repaired = app.rule_state.replace_verified_price_metrics("FPT", "trade", metrics={
        "peak_profit_pct": 3.5, "mfe_pct": 3.5, "mae_pct": -1, "current_profit_pct": 2,
        "market_price": 23.1, "current_net_pnl": 280000, "mae_net_pnl": -135900,
        "mfe_net_pnl": 475650, "entry_notional_vnd": 13590000,
    }, evidence={"boardId": "G1", "source": "reviewed history"})
    assert not app._protection_history_allowed(stale)
    decision = StrategyDecision("SELL", "FPT", "NORMAL_PROTECTION", details={
        **stale.details, "trade_id": "trade"})
    assert app._plan_rule_decision(decision, tick(), "REAL").reason == "PROTECT_HISTORY_UNVERIFIED"
    stale.details["price_history_repaired_at"] = repaired["price_history_repaired_at"]
    assert app._protection_history_allowed(stale)


@pytest.mark.parametrize("mode", ["REAL", "PAPER"])
@pytest.mark.parametrize("reason", ["NORMAL_PROTECTION", "DYNAMIC_PROTECTION"])
@pytest.mark.parametrize("status", ["PENDING", "WAITING_TOKEN", "WAITING_SETTLEMENT", "PAUSED"])
def test_old_unsent_protect_cannot_bypass_legacy_history_gate(tmp_path, mode, reason, status):
    engine, broker, queue, _trades, _rules = service(tmp_path,
        protection_history_allowed_provider=lambda *_a: False)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", source="EM",
                                        execution_mode=mode, reason=reason))
    queue._update(intent.id, status=status)
    engine.process_due(phase="CLOSED", execution_mode=mode)
    assert queue.get(intent.id).status == "CANCELLED" and broker.sent == []


def test_history_gate_is_rechecked_after_slow_account_read(tmp_path):
    allowed = {"value": True}
    engine, broker, queue, _trades, _rules = service(tmp_path,
        protection_history_allowed_provider=lambda *_a: allowed["value"])
    original = broker.get_positions
    def positions(**kwargs):
        allowed["value"] = False
        return original(**kwargs)
    broker.get_positions = positions
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", source="EM",
                                        execution_mode="REAL", reason="NORMAL_PROTECTION"))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert broker.sent == [] and queue.get(intent.id).status == "CANCELLED"


@pytest.mark.parametrize("reason,source", [("STOP_LOSS", "EM"), ("TAKE_PROFIT", "EM"),
                                        ("INDICATOR_EXIT", "EM"), ("MANUAL", "MANUAL")])
def test_legacy_protect_gate_does_not_disable_independent_exits(tmp_path, reason, source):
    engine, broker, queue, _trades, _rules = service(tmp_path,
        protection_history_allowed_provider=lambda *_a: False)
    intent = queue.add(OrderIntent.create("FPT", "SELL", 100, "MARKET", source=source,
                                        execution_mode="REAL", reason=reason))
    engine.process_due(phase="OPEN", execution_mode="REAL")
    assert [r.id for r in broker.sent] == [intent.id]


def test_arm_notification_survives_missed_event_and_failed_delivery(subject, monkeypatch):
    value, client = subject
    notice = {"symbol": "HDB", "execution_mode": "REAL", "price": 24.5,
              "mfe_pct": 8.6, "peak_price": 24.6, "protect_price": 23.985,
              "arm_pct": 7, "policy": "AUTO", "sellable_quantity": 0, "armed_at": TELEGRAM_START}
    original = value.telegram.notify_protect_armed
    monkeypatch.setattr(value.telegram, "notify_protect_armed", lambda **_k: False)
    decision = StrategyDecision("WAIT", "HDB", "HOLD_POSITION", details={
        "trade_id": "trade", "protect_arm_notice": notice})
    for _ in range(3):
        value._notify_rule_signal("HDB", decision, {"price": 24.5}, execution_mode="REAL")
    assert len(value.rule_state.pending_telegram_notices()) == 1 and not client.sent
    value.rule_state = RuleStateStore(value.rule_state.store.path)
    monkeypatch.setattr("viking_v2.dashboard.actions.time.time", lambda: TELEGRAM_START + 61)
    monkeypatch.setattr(value.telegram, "notify_protect_armed", original)
    value._retry_telegram_notices()
    assert len(client.sent) == 1 and "PROTECT ARM" in client.sent[0]
    assert "Chờ cổ về" in client.sent[0] and "Chưa tạo SELL" in client.sent[0]
    value._notify_rule_signal("HDB", decision, {"price": 24.5}, execution_mode="REAL")
    assert len(client.sent) == 1
