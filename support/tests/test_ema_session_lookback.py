"""Prior-session evidence, consumption and real submission through offline fakes."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from viking_v2.models import BrokerOrderResult
from viking_v2.rules.business import StaticRule, StaticRuleParameters, indicator_snapshot
from viking_v2.rules.ema_memory import lookback_start, memory_caption, memory_fields, memory_hint
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.session_cross import SessionCrossService, SessionPriceCache
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.market import VN_TZ
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore

NOW = datetime(2026, 10, 5, 14, 20, tzinfo=VN_TZ)


class HistoricalBroker:
    def __init__(self, crossed="2026-10-02", *, fail=False):
        self.crossed = datetime.fromisoformat(crossed).replace(tzinfo=VN_TZ, hour=14, minute=10)
        self.calls, self.fail = [], fail

    def get_ohlc(self, symbol, resolution, left, right):
        self.calls.append((symbol, resolution, left, right))
        if self.fail:
            return None
        stamps = list(range(left, right + 1, 60))
        prices = [105. if stamp + 60 >= self.crossed.timestamp() else 100. for stamp in stamps]
        return {"s": "ok", "t": stamps, **{key: prices for key in ("o", "h", "l", "c")}, "v": [100]*len(stamps)}


def setup(tmp_path, *, mode="SESSIONS", crossed="2026-10-02", closed=False, fail=False):
    broker = HistoricalBroker(crossed, fail=fail)
    state = RuleStateStore(tmp_path / "rules.json")
    state.sync_ema_watchlist(["TEST"], "REAL", now=NOW)
    service = SessionCrossService(SessionPriceCache(tmp_path / "minutes.json", broker), state)
    params = StaticRuleParameters(buy_ema_cross_mode=mode, buy_ema_cross_sessions=3,
                                 whipsaw_enabled=False, buy_signal_use_rsi=False)
    rows = []
    for i in range(100):
        at = NOW.replace(hour=0, minute=0) - timedelta(days=100-i)
        if at.weekday() < 5:
            # Establish a strict EMA-fast-below-slow baseline. A flat 100
            # history makes the EMAs equal, which is neutral and cannot cross.
            price = (105. if at.date() >= broker.crossed.date()
                     else 100. + .1 * (broker.crossed.date() - at.date()).days)
            rows.append({"time": at.timestamp(), "close": price, "closed": True})
    if not closed:
        rows.append({"time": NOW.replace(hour=0, minute=0).timestamp(), "close": 105., "closed": False})
    def sample(now=NOW):
        if closed:
            return service.observe_closed("TEST", "REAL", rows, params, now, "HOSE")
        return service.observe("TEST", "REAL", rows, indicator_snapshot(rows,3,6,14), params, now, "HOSE")
    return broker, state, service, params, rows, sample


@pytest.mark.parametrize("crossed,state_name,remaining", [
    ("2026-10-02", "SESSION_ACTIVE", 2), ("2026-10-01", "SESSION_ACTIVE", 1),
    ("2026-09-30", "EXPIRED", 0), ("2026-09-29", "WAIT_DOWN", 0)])
def test_scan_prior_three_trading_sessions_without_renewing_old_cross(tmp_path, crossed, state_name, remaining):
    broker, state, service, params, rows, sample = setup(tmp_path, crossed=crossed)
    result = sample()
    assert result['state'] == state_name and result['remaining_sessions'] == remaining
    assert result['lookback_started_at'] == '2026-09-30T00:00:00+07:00'
    if state_name in {'SESSION_ACTIVE','EXPIRED'}:
        assert result['cross_at'] == broker.crossed.isoformat()
        assert result['cross_source'] == 'HISTORY'
        assert result['lookback_up_count'] == 1 and result['up_count'] == 0
        assert '↑1 ↓0' in memory_caption(result)
        assert memory_fields(result)['ema_memory_up_count'] == 1
    fetched = [datetime.fromtimestamp(call[2],VN_TZ).date() for call in broker.calls]
    assert {str(day) for day in fetched} == {'2026-09-30','2026-10-01','2026-10-02','2026-10-05'}
    assert all(day.weekday() < 5 for day in fetched)


def test_va_still_ignores_pre_admission_and_never_fetches_lookback(tmp_path):
    broker, _, _, _, _, sample = setup(tmp_path, mode='VA')
    result = sample()
    assert result['state'] == 'WAIT_DOWN' and not result.get('cross_id')
    assert broker.calls == [] and 'chỉ ghi nhận từ khi thêm mã' in memory_hint(result)


def test_va_opt_in_scans_prior_three_sessions_then_retains_cross_across_restart(tmp_path):
    broker, state, service, params, rows, sample = setup(tmp_path, mode='VA')
    params.buy_ema_va_lookback_enabled = True
    result = sample()
    assert result['state'] == 'SESSION_ACTIVE'
    assert result['cross_at'] == broker.crossed.isoformat()
    assert result['cross_source'] == 'VA_HISTORY'
    assert result['expires_at'] == 0
    assert '↑1 ↓0' in memory_caption(result)
    assert broker.calls
    assert state.claim_session_cross('TEST', 'REAL', result['cross_id'], now=NOW.timestamp())
    restarted = SessionCrossService(SessionPriceCache(tmp_path/'minutes.json', broker),
                                    RuleStateStore(tmp_path/'rules.json'))
    later = restarted.observe('TEST', 'REAL', rows, indicator_snapshot(rows,3,6,14),
                              params, NOW+timedelta(days=1), 'HOSE')
    assert later['cross_id'] == result['cross_id'] and later['used']
    assert later['state'] == 'SESSION_USED' and later['expires_at'] == 0


def test_va_lookback_reactivation_scans_before_original_watch_start(tmp_path):
    _, _, _, params, _, sample = setup(tmp_path)
    sample()
    params.buy_ema_cross_mode = 'VA'
    params.buy_ema_va_lookback_enabled = False
    assert not sample().get('cross_id')
    params.buy_ema_va_lookback_enabled = True
    later = sample(NOW + timedelta(days=4))
    assert later['lookback_requested_at'] == (NOW + timedelta(days=4)).isoformat()
    assert later['lookback_anchor_at'] == NOW.isoformat()
    assert later['lookback_started_at'] == '2026-09-30T00:00:00+07:00'


def test_va_lookback_enabled_days_later_uses_original_watch_start(tmp_path):
    _, _, _, params, _, sample = setup(tmp_path, mode='VA')
    before = sample()
    assert before['started_at'] == NOW.isoformat()
    params.buy_ema_va_lookback_enabled = True
    later = sample(NOW + timedelta(days=4))
    assert later['lookback_requested_at'] == (NOW + timedelta(days=4)).isoformat()
    assert later['lookback_anchor_at'] == NOW.isoformat()
    assert later['lookback_started_at'] == '2026-09-30T00:00:00+07:00'


def test_old_toggle_anchored_scan_is_rebuilt_without_resetting_live_counts(tmp_path):
    _, state, _, params, _, sample = setup(tmp_path, mode='VA')
    before = sample()
    before.update(up_count=19, down_count=18,
                  lookback_key='3/6:3:REALTIME:VA', lookback_ready=True,
                  lookback_started_at='2026-10-06T00:00:00+07:00',
                  va_lookback_active=True)
    state.save_session_cross('TEST', 'REAL', before)
    params.buy_ema_va_lookback_enabled = True
    later = sample(NOW + timedelta(days=4))
    assert later['lookback_key'].startswith('ADMISSION:')
    assert later['lookback_started_at'] == '2026-09-30T00:00:00+07:00'
    assert later['up_count'] == 19 and later['down_count'] == 18


def test_first_fresh_quote_up_against_historical_minute_cannot_leak_into_va(tmp_path):
    broker,_,_,params,_,sample = setup(tmp_path,crossed='2026-10-05')
    broker.crossed = NOW  # The completed history was below; first current quote is above.
    result = sample()
    assert result['valid'] and result['cross_source'] == 'HISTORY'
    assert result['lookback_up_count'] == 1 and result['up_count'] == 0
    params.buy_ema_cross_mode = 'VA'
    assert sample()['state'] == 'WAIT_DOWN'


def test_closed_uses_only_completed_daily_crosses_and_no_minute_request(tmp_path):
    broker, _, _, _, _, sample = setup(tmp_path, closed=True)
    result = sample()
    assert result['valid'] and result['cross_at'] == '2026-10-02T14:45:00+07:00'
    assert result['observation_mode'] == 'CLOSED' and broker.calls == []


def test_used_cross_and_cache_survive_restart_mode_toggles_and_period_changes(tmp_path):
    broker, state, service, params, rows, sample = setup(tmp_path)
    first = sample()
    assert state.claim_session_cross('TEST','REAL',first['cross_id'],now=NOW.timestamp())
    calls = len(broker.calls)
    params.buy_ema_cross_mode = 'VA'
    assert sample()['state'] == 'WAIT_DOWN'
    params.buy_ema_cross_mode = 'SESSIONS'
    again = sample()
    assert again['state'] == 'SESSION_USED' and again['cross_id'] == first['cross_id']
    params.buy_ema_cross_sessions = 4
    again = sample()
    assert again['cross_id'] == first['cross_id'] and again['used']
    assert not state.claim_session_cross('TEST','REAL',again['cross_id'],now=NOW.timestamp())
    assert len(broker.calls) > calls  # Only the newly requested older session.
    calls = len(broker.calls)
    restarted = SessionCrossService(SessionPriceCache(tmp_path/'minutes.json',broker),RuleStateStore(tmp_path/'rules.json'))
    again = restarted.observe('TEST','REAL',rows,indicator_snapshot(rows,3,6,14),params,NOW,'HOSE')
    assert again['cross_id'] == first['cross_id'] and again['used'] and len(broker.calls) == calls


def test_missing_history_blocks_historical_buy_then_retry_recovers_without_recount(tmp_path):
    broker, _, _, _, _, sample = setup(tmp_path,fail=True)
    assert not sample()['valid']
    calls = len(broker.calls)
    assert not sample(NOW+timedelta(seconds=5))['valid'] and len(broker.calls) == calls
    broker.fail = False
    result = sample(NOW+timedelta(seconds=60))
    assert result['valid'] and result['lookback_up_count'] == 1, str(result)
    calls = len(broker.calls)
    assert sample(NOW+timedelta(seconds=61))['cross_id'] == result['cross_id']
    assert len(broker.calls) == calls


def test_missing_pre_admission_history_does_not_block_a_new_verified_live_cross(tmp_path):
    broker, _, service, params, rows, _ = setup(tmp_path,fail=True)
    below = indicator_snapshot([*rows[:-1],{**rows[-1],'close':95}],3,6,14)
    service.observe('TEST','REAL',rows,below,params,NOW,'HOSE')
    result = service.observe('TEST','REAL',rows,indicator_snapshot(rows,3,6,14),params,NOW+timedelta(seconds=1),'HOSE')
    assert result['valid'] and result['cross_source'] == 'LIVE' and result['lookback_ready'] is False


def test_existing_version_two_live_cross_still_works_if_historical_api_is_down(tmp_path):
    broker,state,service,params,rows,sample = setup(tmp_path,mode='VA',fail=True)
    below = indicator_snapshot([*rows[:-1],{**rows[-1],'close':95}],3,6,14)
    service.observe('TEST','REAL',rows,below,params,NOW,'HOSE')
    first = sample(NOW+timedelta(seconds=1))
    first.pop('cross_source')  # Real state saved by 43b8aeb had no source marker.
    state.save_session_cross('TEST','REAL',first)
    params.buy_ema_cross_mode = 'SESSIONS'
    result = sample(NOW+timedelta(seconds=2))
    assert result['valid'] and result['cross_id'] == first['cross_id']


def test_switching_va_to_sessions_does_not_recount_or_reuse_a_consumed_live_cross(tmp_path):
    broker,state,service,params,rows,sample = setup(tmp_path,mode='VA')
    below = indicator_snapshot([*rows[:-1],{**rows[-1],'close':95}],3,6,14)
    service.observe('TEST','REAL',rows,below,params,NOW,'HOSE')
    first = sample(NOW+timedelta(seconds=1))
    assert state.claim_session_cross('TEST','REAL',first['cross_id'],now=NOW.timestamp()+1)
    params.buy_ema_cross_mode = 'SESSIONS'
    result = sample(NOW+timedelta(seconds=2))
    assert result['cross_id'] == first['cross_id'] and result['used']
    assert result['up_count'] == 1 and result['lookback_up_count'] == 1
    assert result['lookback_down_count'] == 1  # Bridge from history above to the admission baseline below.
    assert '↑2 ↓1' in memory_caption(result)


@pytest.mark.parametrize('reply',[{'s':'ok'}, {'s':'ok','t':[],'c':[]}, {'s':'ok','t':[1],'c':[105],'o':[],'h':[],'l':[],'v':[]}])
def test_malformed_minute_history_never_authorizes_a_historical_buy(tmp_path,reply):
    broker,_,_,_,_,sample = setup(tmp_path)
    broker.get_ohlc = lambda *_: reply
    result = sample()
    assert not result['valid'] and not result['lookback_ready']


def test_previous_sessions_skip_weekend_holidays_and_reject_incomplete_calendar():
    assert lookback_start(NOW,3).date().isoformat() == '2026-09-30'
    assert lookback_start(NOW,3,holidays=['2026-10-02']).date().isoformat() == '2026-09-29'
    assert lookback_start(NOW,3,working_dates=['2026-10-01']) is None


def test_historical_cross_reaches_fake_market_broker_once_and_rsi_guard_still_blocks(tmp_path,monkeypatch):
    monkeypatch.setattr('time.time',lambda:NOW.timestamp())
    _, state, _, params, rows, sample = setup(tmp_path)
    cross = sample()
    marks = indicator_snapshot(rows,3,6,14)
    context = {'symbol':'TEST','exchange':'HOSE','bars':rows,'signal_mode':'REALTIME',
               'indicator_snapshot':marks,'previous_indicators':marks,'previous_market_state':'UPTREND',
               'session_ema_cross':cross}
    portfolio = {'available_capital':25e6,'available_cash':50e6,'nav':50e6,'order_budget':25e6,'open_positions':0}
    queue, trades = OrderQueue(tmp_path/'orders.json'),TradeStateStore(tmp_path/'trades.json')
    queue._now = lambda: NOW.timestamp()
    decision = StaticRule(params).evaluate(context,portfolio)
    assert decision.action == 'BUY'
    params.buy_signal_use_rsi = True
    assert StaticRule(params).evaluate({**context,'indicator_snapshot':{**marks,'rsi':50,'rsi_previous':60}},portfolio).action == 'WAIT'
    intent = StrategyOrderPlanner(queue,trades,state).plan(decision,execution_mode='REAL',execution_style='MARKET',
        tick={'ask':105},portfolio=portfolio,candle_key=cross['cross_id']).intent
    assert intent is not None
    sent = []
    def send(order):
        sent.append(order)
        return BrokerOrderResult(True,'WORKING',order_id='FAKE-HISTORY')
    broker = SimpleNamespace(has_trading_token=lambda:True,place_order=send)
    execution = ExecutionService(broker,broker,queue,JSONLineJournal(tmp_path/'journal.jsonl'),
        quote_provider=lambda _: {'symbol':'TEST','price':105,'ask':105,'timestamp':NOW.timestamp(),'source':'WS'},
        trade_state=trades,rule_state=state)
    execution.process_due(phase='OPEN',execution_mode='REAL')
    execution.process_due(phase='OPEN',execution_mode='REAL')
    assert len(sent) == 1 and sent[0].order_type == 'MARKET', str(queue.get(intent.id))
    assert queue.get(intent.id).status == 'WORKING'
