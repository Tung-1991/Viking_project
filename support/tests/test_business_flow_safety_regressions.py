"""Offline business-flow safety regressions. No live broker or Telegram.

Assertions preserve the financial invariants from the independent audit.
Run only through support/tools/run_offline.py.
"""
from copy import deepcopy
from types import SimpleNamespace
import time

import pytest

from viking_v2.models import OrderIntent, StrategyDecision, BrokerOrderResult
from viking_v2.rules.planner import StrategyOrderPlanner
from viking_v2.rules.state import RuleStateStore
from viking_v2.storage import JSONLineJournal, DailyFeeTracker
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder
from viking_v2.trading.state import TradeStateStore
from viking_v2 import config
from viking_v2.dashboard.actions import DashboardActionsMixin
import viking_v2.dashboard.actions as actions_module
from viking_v2.connections.dnse.client import DNSEClient


class OfflineBroker:
    def __init__(self):
        self.sent = []
        self.orders = []
        self.positions = [{'symbol': 'FPT', 'openQuantity': 100, 'tradeQuantity': 100}]
        self.result = BrokerOrderResult(True, 'New', order_id='B1', raw={'fillQuantity': 0})

    def has_trading_token(self):
        return True

    def get_positions(self, **_kwargs):
        return deepcopy(self.positions)

    def get_orders(self, **_kwargs):
        return deepcopy(self.orders)

    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        return self.result


class OfflineCashBroker(DNSEClient):
    """Exercise the DNSE-specific preflight without any HTTP operation."""
    def __init__(self, cash, positions=None, fee_rate=0, equity=100e6):
        super().__init__(api_key='AUDIT-FAKE', api_secret='AUDIT-FAKE', account_no='AUDIT')
        self.cash, self.positions, self.fee_rate = cash, deepcopy(positions or []), fee_rate
        self.equity = equity
        self.sent, self.orders = [], []

    def has_trading_token(self):
        return True

    def cash_package(self, _symbol):
        return {'id': 1, 'initialRate': 1, 'brokerFirmBuyingFeeRate': self.fee_rate}

    def get_balance(self, **_kwargs):
        return {'equity': self.equity, 'stock': {'availableCash': self.cash, 'totalCash': 100e6}}

    def get_positions(self, **_kwargs):
        return deepcopy(self.positions)

    def get_orders(self, **_kwargs):
        return deepcopy(self.orders)

    def get_buying_power(self, *_args):
        return {'qmaxBuy': 10000}

    def get_secdef(self, _symbol):
        return {'ceilingPrice': 20000}

    def place_order(self, intent):
        self.sent.append(deepcopy(intent))
        price = intent.limit_price if intent.order_type == 'LO' else 20
        gross = intent.quantity * price * 1000
        self.cash -= gross * (1 + self.fee_rate)
        self.positions.append(dict(symbol=intent.symbol, openQuantity=intent.quantity,
            tradeQuantity=0, costPrice=price * 1000, marketPrice=price * 1000,
            price_unit='VND', loanPackageId='1', id='D' + str(len(self.sent))))
        return BrokerOrderResult(True, 'Filled', order_id='B' + str(len(self.sent)), raw={
            'fillQuantity': intent.quantity, 'averagePrice': price * 1000,
            'price_unit': 'VND', 'fee': gross * self.fee_rate})


def stores(root):
    return (OrderQueue(root / 'orders.json'), TradeStateStore(root / 'trades.json'),
            RuleStateStore(root / 'rules.json'))


def engine(root, broker, **kwargs):
    queue, trades, rules = stores(root)
    service = ExecutionService(broker, broker, queue, JSONLineJournal(root / 'journal.jsonl'),
                               trade_state=trades, rule_state=rules, **kwargs)
    return service, queue, trades, rules


def tick(symbol='FPT', price=100):
    return dict(symbol=symbol, price=price, bid=price, ask=price,
                source='WS', timestamp=time.time())


def test_exit_claim_must_not_survive_failure_to_persist_the_exit_intent(tmp_path, monkeypatch):
    queue, trades, rules = stores(tmp_path)
    cycle = trades.create('FPT', 'REAL', trade_id='T1', em_modes=['IND_EXIT'])
    trades.record_buy_fill(cycle.id, 100, 100)
    decision = StrategyDecision('SELL', 'FPT', 'SELL_SIGNAL', event='INDICATOR_EXIT',
        signal='SELL', quantity_fraction=1, scope='POSITION_MANAGEMENT')
    planner = StrategyOrderPlanner(queue, trades, rules)
    arguments = dict(execution_mode='REAL', execution_style='MARKET', tick=tick(),
                     portfolio={'position_quantity': 100, 'trade_id': 'T1'}, candle_key='2026-10-09')
    with monkeypatch.context() as patch:
        def fail(_intent):
            raise OSError('offline queue write failure')
        patch.setattr(queue, 'add', fail)
        with pytest.raises(OSError):
            planner.plan(decision, **arguments)
    restarted_queue, restarted_trades, restarted_rules = stores(tmp_path)
    restarted = StrategyOrderPlanner(restarted_queue, restarted_trades, restarted_rules)
    retried = restarted.plan(decision, **arguments)
    assert retried.intent is not None, retried.reason
    assert len(restarted_queue.list_all()) == 1


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('price_field', ['missing', 'zero'])
def test_incomplete_submission_fill_must_recover_when_broker_supplies_price(tmp_path, side, price_field):
    broker = OfflineBroker()
    service, queue, trades, _rules = engine(tmp_path, broker)
    if side == 'SELL':
        trades.create('FPT', 'REAL', trade_id='T1')
        trades.record_buy_fill('T1', 100, 100)
    raw = {'fillQuantity': 100, 'price_unit': 'VND'}
    if price_field == 'zero':
        raw['averagePrice'] = 0
    broker.result = DNSEClient._result(True, {'id': 'B1', 'orderStatus': 'Filled', **raw}, 201, '')
    intent = queue.add(OrderIntent.create('FPT', side, 100, 'MARKET', execution_mode='REAL',
        source='BOT' if side == 'BUY' else 'EM', trade_id='T1',
        action='OPEN' if side == 'BUY' else 'CLOSE', reason='STOP_LOSS' if side == 'SELL' else ''))
    service.process_due(phase='OPEN', execution_mode='REAL')
    broker.orders = [dict(id='B1', symbol='FPT', side='NB' if side == 'BUY' else 'NS',
        orderStatus='Filled', fillQuantity=100, quantity=100, averagePrice=100000, price_unit='VND')]
    restarted, _queue, _trades, _rules = engine(tmp_path, broker)
    restarted.reconcile_working('REAL')
    actual = trades.get('T1')
    expected = 100 if side == 'BUY' else 0
    assert actual and actual.open_quantity == expected, (
        queue.get(intent.id).to_dict(), actual.to_dict() if actual else None)
    assert len(broker.sent) == 1


def test_bot_exposure_reserves_confirmed_fill_when_position_snapshot_lags(tmp_path):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1', source='BOT')
    trades.record_buy_fill('T1', 100, 100)
    builder = PortfolioContextBuilder(queue, trades, rules)
    # The confirmed holding consumes all 10M of the 50% exposure limit.
    # Cash is up to date; the successful broker position response lags the fill.
    context = builder.build('VNM', execution_mode='REAL', balance={
        'equity': 20_000_000, 'stock': {'availableCash': 10_000_000}},
        positions=[], tick=tick('VNM', 20), exposure=.5, max_positions=5,
        no_compound_enabled=False)
    balance = {'equity': 20e6, 'stock': {'availableCash': 10e6}}
    app = headless_app(tmp_path, balance=balance, positions=[], symbols=['VNM'])
    broker = OfflineCashBroker(10e6, equity=20e6)
    app.real = broker
    decision = StrategyDecision('BUY', 'VNM', 'BUY_SIGNAL', signal='BUY',
        event='ENTRY_BUY', market_state='ACCUMULATION', details={
            'order_budget': context['order_budget'], 'entry_checks': context,
            'exposure': .5, 'candle_key': '2026-10-09'})
    app._plan_rule_decision(decision, tick('VNM', 20), 'REAL', available_cash=10e6)
    service = ExecutionService(broker, broker, app.queue,
        JSONLineJournal(tmp_path / 'journal.jsonl'), trade_state=app.trade_state,
        rule_state=app.rule_state, quote_provider=lambda symbol: tick(symbol, 20),
        bot_entry_guard=app._check_bot_entry_limits)
    service.process_due(phase='OPEN', execution_mode='REAL')
    assert not broker.sent, {'context': context, 'sent': [(item.symbol, item.quantity) for item in broker.sent]}
    assert context['order_budget'] == 0, context


def test_timeout_remains_unknown_and_is_never_automatically_posted_again(tmp_path):
    broker = OfflineBroker()
    broker.result = BrokerOrderResult(False, 'UNKNOWN', error='ORDER_STATUS_UNKNOWN')
    service, queue, trades, rules = engine(tmp_path, broker)
    intent = queue.add(OrderIntent.create('FPT', 'BUY', 100, 'MARKET', execution_mode='REAL'))
    service.process_due(phase='OPEN', execution_mode='REAL')
    restarted, _queue, _trades, _rules = engine(tmp_path, broker)
    queue.recover_claims()
    for _ in range(3):
        restarted.process_due(phase='OPEN', execution_mode='REAL')
    assert queue.get(intent.id).status == 'UNKNOWN' and len(broker.sent) == 1


def headless_app(root, *, balance, positions, symbols):
    app = DashboardActionsMixin()
    app.queue, app.trade_state, app.rule_state = stores(root)
    app.strategy_planner = StrategyOrderPlanner(app.queue, app.trade_state, app.rule_state)
    app.settings = config.AppSettings.from_dict({
        'watchlist': symbols, 'bot_order_mode': 'MARKET', 'buy_fee_pct': 0,
        'priority_capital_enabled': False, 'priority_symbols': [],
        'rule_parameters': {'max_positions': 5, 'no_compound_enabled': False},
    })
    app.snapshots = {'REAL': (balance, positions, [])}
    app.bridge = SimpleNamespace(read_config=lambda: SimpleNamespace(
        paper_mode=False, bot_enabled=True))
    app._symbol_exchange = lambda _symbol=None: 'HOSE'
    app._log = lambda *_args, **_kwargs: None
    app._record_signal_decision = lambda *_args, **_kwargs: None
    app._notify_corporate_action = lambda *_args, **_kwargs: None
    app._notify_rule_signal = lambda *_args, **_kwargs: None
    return app


def test_simultaneous_bot_signals_share_remaining_exposure_not_just_cash(tmp_path):
    balance = {'equity': 100e6, 'stock': {'availableCash': 60e6}}
    # A MANUAL holding uses 40M of the 50M total exposure, but no BOT slot.
    positions = [{'symbol': 'FPT', 'openQuantity': 400, 'tradeQuantity': 400,
                  'costPrice': 100000, 'marketPrice': 100000, 'price_unit': 'VND',
                  'source': 'MANUAL'}]
    broker = OfflineCashBroker(60e6, positions)
    app = headless_app(tmp_path, balance=balance, positions=positions, symbols=['VNM', 'MBB'])
    app.real = broker
    builder = PortfolioContextBuilder(app.queue, app.trade_state, app.rule_state,
                                      buy_fee_rate=lambda: 0)
    decisions = {}
    for symbol in app.settings.watchlist:
        context = builder.build(symbol, execution_mode='REAL', balance=balance,
            positions=positions, tick=tick(symbol, 20), exposure=.5,
            max_positions=5, no_compound_enabled=False)
        assert context['order_budget'] == 10e6
        decisions[symbol] = StrategyDecision('BUY', symbol, 'BUY_SIGNAL',
            event='ENTRY_BUY', signal='BUY', market_state='ACCUMULATION', details={
                'execution_mode': 'REAL', 'updated_at': time.time(),
                'candle_key': '2026-10-09', 'exposure': .5,
                'order_budget': context['order_budget'], 'entry_checks': context,
            }).to_dict()
    app._consume_book_decisions({'bot_enabled': True, 'decisions': decisions,
        'ticks': {symbol: tick(symbol, 20) for symbol in app.settings.watchlist}},
        'RUNNING', 'REAL', False)
    pending = app.queue.list_all()
    assert [(item.symbol, item.quantity) for item in pending] == [('VNM', 500)]
    execution = ExecutionService(broker, broker, app.queue,
        JSONLineJournal(tmp_path / 'journal.jsonl'), trade_state=app.trade_state,
        rule_state=app.rule_state, quote_provider=lambda symbol: tick(symbol, 20),
        bot_entry_guard=app._check_bot_entry_limits)
    execution.process_due(phase='OPEN', execution_mode='REAL')
    committed = 40e6 + sum(item.quantity * 20 * 1000 for item in broker.sent)
    assert committed <= 50e6, [(item.symbol, item.quantity) for item in broker.sent]
    assert [(item.symbol, item.quantity) for item in broker.sent] == [('VNM', 500)]


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_complete_submission_fill_is_idempotent_across_restart(tmp_path, side):
    broker = OfflineBroker()
    service, queue, trades, _rules = engine(tmp_path, broker)
    if side == 'SELL':
        trades.create('FPT', 'REAL', trade_id='T1')
        trades.record_buy_fill('T1', 100, 100)
    broker.result = BrokerOrderResult(True, 'Filled', order_id='B1', raw={
        'fillQuantity': 100, 'averagePrice': 100000, 'price_unit': 'VND'})
    intent = queue.add(OrderIntent.create('FPT', side, 100, 'MARKET',
        execution_mode='REAL', trade_id='T1', source='BOT' if side == 'BUY' else 'EM',
        action='OPEN' if side == 'BUY' else 'CLOSE'))
    service.process_due(phase='OPEN', execution_mode='REAL')
    broker.orders = [dict(id='B1', symbol='FPT', side='NB' if side == 'BUY' else 'NS',
        orderStatus='Filled', fillQuantity=100, quantity=100, averagePrice=100000, price_unit='VND')]
    restarted, _queue, _trades, _rules = engine(tmp_path, broker)
    for _ in range(3):
        restarted.reconcile_working('REAL')
    actual = trades.get('T1')
    assert actual.open_quantity == (100 if side == 'BUY' else 0)
    assert actual.entry_quantity == 100 and len(broker.sent) == 1
    assert queue.get(intent.id).status == 'FILLED'


def test_complete_position_snapshot_enforces_bot_exposure(tmp_path):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1', source='BOT')
    trades.record_buy_fill('T1', 100, 100)
    builder = PortfolioContextBuilder(queue, trades, rules)
    context = builder.build('VNM', execution_mode='REAL', balance={
        'equity': 20e6, 'stock': {'availableCash': 10e6}},
        positions=[dict(symbol='FPT', openQuantity=100, costPrice=100000,
                        marketPrice=100000, price_unit='VND')],
        tick=tick('VNM', 20), exposure=.5, max_positions=5, no_compound_enabled=False)
    assert context['order_budget'] == 0


@pytest.mark.parametrize('outcome', ['REJECTED', 'UNKNOWN', 'ACCEPTED'])
def test_replace_request_outcome_is_recoverable_in_actual_ui_handler(tmp_path, monkeypatch, outcome):
    widgets, entries, buttons = [], [], []

    class Widget:
        def __init__(self, *_args, **kwargs):
            self.options, self.value = kwargs, ''
            widgets.append(self)

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

        def configure(self, **kwargs):
            self.options.update(kwargs)

        def insert(self, _index, value):
            self.value = str(value)

        def get(self):
            return self.value

    def entry(*args, **kwargs):
        obj = Widget(*args, **kwargs)
        entries.append(obj)
        return obj

    def button(*args, **kwargs):
        obj = Widget(*args, **kwargs)
        buttons.append(obj)
        return obj

    for name in ['CTkToplevel', 'CTkLabel', 'CTkFrame']:
        monkeypatch.setattr(actions_module.ctk, name, Widget)
    monkeypatch.setattr(actions_module.ctk, 'CTkEntry', entry)
    monkeypatch.setattr(actions_module.ctk, 'CTkButton', button)
    monkeypatch.setattr(actions_module, '_HoverHint', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions_module.tk, 'BooleanVar', lambda **kwargs: SimpleNamespace(
        get=lambda: kwargs['value'], set=lambda _value: None))

    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    app = DashboardActionsMixin()
    app.queue, app.trade_state, app.rule_state, app.real = queue, trades, rules, broker
    app.settings = config.AppSettings.from_dict({})
    app._io_executor = SimpleNamespace(submit=lambda fn: fn())
    app._post_ui = lambda fn: fn()
    app._refresh_local = lambda: None
    replace_calls = []
    def replace(order_id, **kwargs):
        replace_calls.append((order_id, kwargs))
        if outcome == 'REJECTED':
            return BrokerOrderResult(False, 'REJECTED', status_code=400, error='INVALID_REPLACE')
        if outcome == 'UNKNOWN':
            return BrokerOrderResult(False, 'UNKNOWN', error='ORDER_STATUS_UNKNOWN')
        return BrokerOrderResult(True, 'New', order_id='B1')
    broker.replace_order = replace
    intent = queue.add(OrderIntent.create('FPT', 'BUY', 100, 'LO', limit_price=100,
                                         source='MANUAL', execution_mode='REAL'))
    queue._update(intent.id, status='WORKING', broker_order_id='B1',
                  broker_order_ids=['B1'], working_quantity=100)
    app._edit_running_order({'local_id': intent.id, 'broker_order_id': 'B1',
        'symbol': 'FPT', 'side': 'BUY', 'mode': 'REAL', 'order_type': 'LO',
        'status': 'WORKING', 'cancellable': True})
    entries[0].value = '200'
    buttons[-1].options['command']()
    assert len(replace_calls) == 1
    broker.orders = [dict(id='B1', symbol='FPT', side='NB', orderStatus='New',
        quantity=200 if outcome == 'ACCEPTED' else 100, price=100000,
        fillQuantity=0, price_unit='VND')]
    for _ in range(3):
        service.reconcile_working('REAL')
    actual = queue.get(intent.id)
    expected = 'REPLACE_PENDING' if outcome == 'UNKNOWN' else 'WORKING'
    assert actual.status == expected, actual.to_dict()
    if outcome == 'ACCEPTED':
        assert actual.quantity == 200
    elif outcome == 'REJECTED':
        assert 'requested_replace' not in actual.details


@pytest.mark.parametrize('middle_fee', ['missing', 'refund', 'complete'])
def test_external_partial_fill_cost_baseline_survives_incomplete_or_corrected_fee(tmp_path, middle_fee):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    cycle = trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    cycle.opened_at = time.time() - 60
    trades.save(cycle)
    trades.record_buy_fill('T1', 300, 100)

    def positions(quantity):
        return [dict(symbol='FPT', id='D1', loanPackageId='1', openQuantity=quantity,
                     tradeQuantity=quantity, costPrice=100000, price_unit='VND')]

    def row(filled, fee):
        value = dict(id='EXT1', symbol='FPT', loanPackageId='1', side='NS',
                     quantity=300, fillQuantity=filled, orderStatus='PartiallyFilled',
                     averagePrice=101000, price_unit='VND', modifiedDate=time.time())
        if fee is not None:
            value.update(fee=fee, tax=0)
        return value

    first_fee = 20000 if middle_fee == 'refund' else 10000
    service.reconcile_external_sells(positions(200), [row(100, first_fee)])
    assert trades.get('T1').fees_paid == first_fee
    middle = {'missing': None, 'refund': 15000, 'complete': 20000}[middle_fee]
    service.reconcile_external_sells(positions(100), [row(200, middle)])
    final_fee = 15000 if middle_fee == 'refund' else 20000
    # A final complete fee arrives with no additional quantity. Repeated reads
    # must apply exactly the cumulative broker cost, never charge it twice.
    final_row = row(200, final_fee)
    for _ in range(3):
        service.reconcile_external_sells(positions(100), [final_row])
    actual = trades.get('T1')
    assert actual.open_quantity == 100 and actual.sold_quantity == 200
    assert (actual.fees_paid, actual.net_pnl) == (final_fee, 200000 - final_fee), actual.to_dict()
    totals = DailyFeeTracker(tmp_path / 'order_history.csv', tmp_path / 'daily.json').summary(
        'REAL', [actual], daily=False, now=time.time() + 1)
    assert totals['fees'] == final_fee


@pytest.mark.parametrize('cash, fee_rate, should_send', [(0, 0, False), (2e6, .0015, False), (2003000, .0015, True)])
def test_real_cash_preflight_includes_fees_and_never_uses_total_cash(tmp_path, cash, fee_rate, should_send):
    broker = OfflineCashBroker(cash, fee_rate=fee_rate)
    service, queue, trades, rules = engine(tmp_path, broker,
        quote_provider=lambda symbol: tick(symbol, 20))
    item = queue.add(OrderIntent.create('VNM', 'BUY', 100, 'LO', limit_price=20,
        execution_mode='REAL', source='MANUAL', trade_id='T1'))
    service.process_due(phase='OPEN', execution_mode='REAL')
    assert bool(broker.sent) == should_send
    assert queue.get(item.id).status == ('FILLED' if should_send else 'REJECTED')


@pytest.mark.parametrize('sellable, requested, expected', [(0, 100, 0), (300, 300, 100)])
def test_managed_sell_waits_for_settlement_and_never_sells_external_quantity(tmp_path, sellable, requested, expected):
    broker = OfflineBroker()
    broker.positions = [dict(symbol='FPT', openQuantity=300, tradeQuantity=sellable)]
    service, queue, trades, rules = engine(tmp_path, broker)
    trades.create('FPT', 'REAL', trade_id='T1')
    trades.record_buy_fill('T1', 100, 100)
    item = queue.add(OrderIntent.create('FPT', 'SELL', requested, 'MARKET',
        execution_mode='REAL', source='EM', trade_id='T1', reason='STOP_LOSS'))
    service.process_due(phase='OPEN', execution_mode='REAL', allow_bot_buys=False)
    assert (broker.sent[0].quantity if broker.sent else 0) == expected
    if not expected:
        assert queue.get(item.id).status == 'WAITING_SETTLEMENT'


@pytest.mark.parametrize('phase, allow_bot_buys', [('HOLIDAY', True), ('OPEN', False)])
def test_closed_session_or_bot_off_prevents_bot_buy_handoff(tmp_path, phase, allow_bot_buys):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    queue.add(OrderIntent.create('FPT', 'BUY', 100, 'MARKET',
        execution_mode='REAL', source='BOT'))
    service.process_due(phase=phase, execution_mode='REAL', allow_bot_buys=allow_bot_buys)
    assert broker.sent == []


def test_fill_transaction_failure_replays_durable_result_after_restart(tmp_path, monkeypatch):
    broker = OfflineBroker()
    broker.positions = []
    broker.result = BrokerOrderResult(True, 'Filled', order_id='B1', raw={
        'fillQuantity': 100, 'averagePrice': 100000, 'price_unit': 'VND'})
    service, queue, trades, rules = engine(tmp_path, broker)
    item = queue.add(OrderIntent.create('FPT', 'BUY', 100, 'MARKET',
        execution_mode='REAL', source='BOT', trade_id='T1'))
    with monkeypatch.context() as patch:
        def fail(*_args, **_kwargs):
            raise OSError('offline trade ledger write failure')
        patch.setattr(trades, 'record_buy_fill', fail)
        with pytest.raises(OSError):
            service.process_due(phase='OPEN', execution_mode='REAL')
    assert queue.get(item.id).status == 'SENDING' and trades.get('T1') is None
    restarted, _queue, _trades, _rules = engine(tmp_path, broker)
    assert _trades.get('T1').open_quantity == 100
    restarted.process_due(phase='OPEN', execution_mode='REAL')
    assert queue.get(item.id).status == 'FILLED' and len(broker.sent) == 1


@pytest.mark.parametrize('queue_write_fails', [True, False])
def test_decision_poll_must_schedule_next_cycle_after_queue_write_error(tmp_path, monkeypatch, queue_write_fails):
    app = headless_app(tmp_path, balance={}, positions=[], symbols=['FPT'])
    app.running, app.daemon_process = True, SimpleNamespace(poll=lambda: None)
    app._daemon_started_at = time.time() - 120
    app._daemon_crash_times, app._daemon_restart_blocked = [], False
    app.symbol = SimpleNamespace(get=lambda: 'FPT')
    app.order_type = SimpleNamespace(get=lambda: 'MARKET')
    app.mode = SimpleNamespace(get=lambda: 'REAL')
    app.lbl_session = app.lbl_brain = SimpleNamespace(configure=lambda **_kwargs: None)
    app._paint_bot = lambda *_args, **_kwargs: None
    app._refresh_preview_bars = lambda *_args: None
    app._update_order_preview = lambda: None
    app._refresh_api_health_panel = lambda *_args: None
    app._last_running_render = time.time()
    app.trade_state.create('FPT', 'REAL', trade_id='T1', em_modes=['IND_EXIT'])
    app.trade_state.record_buy_fill('T1', 100, 100)
    decision = StrategyDecision('SELL', 'FPT', 'SELL_SIGNAL', event='INDICATOR_EXIT',
        signal='SELL', quantity_fraction=1, scope='POSITION_MANAGEMENT', details={
            'execution_mode': 'REAL', 'updated_at': time.time(), 'trade_id': 'T1',
            'position_quantity': 100, 'candle_key': '2026-10-09'})
    status = {'heartbeat_at': time.time(), 'daemon_status': 'RUNNING',
        'market_status': 'OPEN', 'bot_enabled': True, 'ticks': {'FPT': tick()},
        'decisions': {'FPT': decision.to_dict()}}
    app.bridge.read_status = lambda: status
    app._consume_bot_decisions = lambda value, daemon: app._consume_book_decisions(
        value, daemon, 'REAL', False)
    for name in ['_notify_market_holiday', '_notify_system_health',
                 '_capture_signal_trace', '_retry_telegram_notices']:
        monkeypatch.setattr(DashboardActionsMixin, name, lambda *_args, **_kwargs: None)
    scheduled = []
    app.after = lambda delay, fn: scheduled.append((delay, fn))
    original_add = app.queue.add
    if queue_write_fails:
        def fail(_intent):
            raise OSError('offline order queue write failure')
        monkeypatch.setattr(app.queue, 'add', fail)
    try:
        app._poll_runtime()
    except OSError:
        pass  # Tk logs the exception, but does not retry this after callback.
    assert any(fn == app._poll_runtime for _delay, fn in scheduled), scheduled
    assert app._decision_poll_fault is queue_write_fails
    if queue_write_fails:
        assert app.queue.list_all() == []
        monkeypatch.setattr(app.queue, 'add', original_add)
        app._poll_runtime()
        assert app._decision_poll_fault is False
        assert len(app.queue.list_all()) == 1
        app._poll_runtime()
        assert len(app.queue.list_all()) == 1 and len(scheduled) == 3


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('status', ['Filled', 'PartiallyFilled', 'Cancelled'])
def test_missing_submission_price_keeps_reservation_until_complete_reconciliation(tmp_path, side, status):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    if side == 'SELL':
        trades.create('FPT', 'REAL', trade_id='T1')
        trades.record_buy_fill('T1', 300, 100)
        broker.positions = [dict(symbol='FPT', openQuantity=300, tradeQuantity=300)]
    broker.result = DNSEClient._result(True, dict(id='B1', orderStatus=status,
        fillQuantity=100, price_unit='VND', fee=4500, tax=0), 201, '')
    item = queue.add(OrderIntent.create('FPT', side, 300, 'MARKET', execution_mode='REAL',
        source='BOT' if side == 'BUY' else 'EM', trade_id='T1',
        details={'reservation_price': 100}, sell_wait_policy='KEEP'))
    service.process_due(phase='OPEN', execution_mode='REAL')
    unknown = queue.get(item.id)
    assert (unknown.status, unknown.filled_quantity, unknown.remaining_quantity,
            unknown.broker_filled_quantity, unknown.broker_notional_logged) == ('UNKNOWN', 0, 300, 0, 0)
    assert trades.get('T1') is None if side == 'BUY' else trades.get('T1').open_quantity == 300
    restarted, queue, trades, rules = engine(tmp_path, broker)
    restarted.process_due(phase='OPEN', execution_mode='REAL')
    assert len(broker.sent) == 1
    broker.orders = [dict(id='B1', symbol='FPT', side='NB' if side == 'BUY' else 'NS',
        orderStatus='Cancelled', quantity=300, fillQuantity=100,
        averagePrice=100000, price_unit='VND', fee=4500, tax=0)]
    for _ in range(3):
        restarted.reconcile_working('REAL')
    assert queue.get(item.id).status == 'CANCELLED'
    assert trades.get('T1').open_quantity == (100 if side == 'BUY' else 200)
    assert len(broker.sent) == 1
    totals = DailyFeeTracker(tmp_path / 'order_history.csv', tmp_path / 'daily.json').summary(
        'REAL', daily=False, now=time.time() + 1)
    assert totals['fees'] == trades.get('T1').fees_paid == 4500


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_signal_claim_and_replacement_cancellation_roll_back_with_failed_intent(tmp_path, monkeypatch, side):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1')
    trades.record_buy_fill('T1', 100, 100)
    old = None
    if side == 'SELL':
        old = queue.add(OrderIntent.create('FPT', 'SELL', 100, 'MARKET',
            execution_mode='REAL', source='EM', trade_id='T1', reason='NORMAL_PROTECTION'))
    decision = StrategyDecision(side, 'FPT', side + '_SIGNAL', signal=side,
        event='STOP_LOSS' if side == 'SELL' else 'ENTRY_BUY',
        scope='POSITION_MANAGEMENT' if side == 'SELL' else 'ENTRY')
    arguments = dict(execution_mode='REAL', execution_style='MARKET', tick=tick(),
        portfolio={'position_quantity': 100, 'trade_id': 'T1', 'order_budget': 10e6},
        candle_key='2026-10-09')
    planner = StrategyOrderPlanner(queue, trades, rules)
    original_add = queue.add
    def fail_after_write(intent):
        original_add(intent)
        raise OSError('failure after intent write, before commit')
    with monkeypatch.context() as patch:
        patch.setattr(queue, 'add', fail_after_write)
        with pytest.raises(OSError):
            planner.plan(decision, **arguments)
    restarted_queue, restarted_trades, restarted_rules = stores(tmp_path)
    assert len(restarted_queue.list_all()) == (1 if old else 0)
    if old:
        assert restarted_queue.get(old.id).status == 'PENDING'
    retried = StrategyOrderPlanner(restarted_queue, restarted_trades, restarted_rules).plan(decision, **arguments)
    assert retried.intent is not None
    if old:
        assert restarted_queue.get(old.id).status == 'CANCELLED'


@pytest.mark.parametrize('broker_quantity, package, expected_value', [
    (0, '1', 10e6), (50, '1', 10e6), (100, '1', 10e6),
    (200, '1', 20e6), (100, '2', 20e6), (100, '', 20e6),
])
def test_confirmed_exposure_merges_partial_snapshots_by_package(tmp_path, broker_quantity, package, expected_value):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    trades.record_buy_fill('T1', 100, 100)
    rows = [dict(symbol='FPT', loanPackageId=package, openQuantity=broker_quantity,
                 costPrice=100000, marketPrice=100000, price_unit='VND')]
    context = PortfolioContextBuilder(queue, trades, rules).build('VNM', execution_mode='REAL',
        balance={'equity': 40e6, 'availableCash': 20e6}, positions=rows, tick=tick('VNM', 20),
        exposure=.5, max_positions=5, no_compound_enabled=False)
    assert context['current_stock_value'] == expected_value


@pytest.mark.parametrize('mode', ['REAL', 'PAPER'])
def test_final_capital_guard_blocks_stale_bot_intent_without_priority(tmp_path, mode):
    broker = OfflineCashBroker(60e6, [dict(symbol='FPT', openQuantity=500,
        marketPrice=100000, costPrice=100000, price_unit='VND')])
    app = headless_app(tmp_path, balance=broker.get_balance(), positions=[], symbols=['VNM'])
    app.real = app.paper = broker
    intent = OrderIntent.create('VNM', 'BUY', 100, 'MARKET', execution_mode=mode,
        source='BOT', entry_market_state='ACCUMULATION', entry_exposure=.5)
    assert app._check_bot_entry_limits(intent, tick('VNM', 20)) == 'PORTFOLIO_EXPOSURE_LIMIT'
    app._decision_poll_fault = True
    assert app._check_bot_entry_limits(intent, tick('VNM', 20)) == 'DECISION_POLL_UNAVAILABLE'


def test_market_bot_sizing_and_final_guard_use_price_bound_without_priority(tmp_path):
    broker = OfflineCashBroker(10e6)
    broker.get_secdef = lambda _symbol: {'ceilingPrice': 21400}
    app = headless_app(tmp_path, balance={'equity': 100e6, 'availableCash': 10e6}, positions=[], symbols=['VNM'])
    app.real = broker
    quote = {**tick('VNM', 20), 'ceiling_price': 21400}
    decision = StrategyDecision('BUY', 'VNM', 'BUY_SIGNAL', signal='BUY', market_state='ACCUMULATION',
        details={'exposure': .5, 'order_budget': 10e6, 'candle_key': '2026-10-09'})
    result = app._plan_rule_decision(decision, quote, 'REAL', available_cash=10e6)
    assert result.intent.quantity == 400
    assert result.intent.details['reservation_price'] == 21.4
    assert app._check_bot_entry_limits(result.intent, quote) == ''
    # A previously sized 500-share ticket is too large at the permitted price.
    result.intent.quantity = result.intent.remaining_quantity = 500
    assert app._check_bot_entry_limits(result.intent, quote) == 'PORTFOLIO_EXPOSURE_LIMIT'


def test_replace_rejection_never_revives_an_order_filled_during_the_request(tmp_path):
    queue, trades, rules = stores(tmp_path)
    item = queue.add(OrderIntent.create('FPT', 'BUY', 100, 'LO', limit_price=100))
    queue._update(item.id, status='WORKING', broker_order_id='B1')
    queue.mark_broker_replaced(item.id, quantity=200, broker_quantity=200, limit_price=100)
    queue._update(item.id, status='FILLED', filled_quantity=100, remaining_quantity=0)
    restored = queue.reject_broker_replace(item.id, 'REJECTED')
    assert restored.status == 'FILLED' and 'requested_replace' not in restored.details


@pytest.mark.parametrize('missing_component', ['fee', 'tax'])
def test_external_costs_preserve_the_omitted_component_then_apply_zero_refund(tmp_path, missing_component):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    cycle = trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    cycle.opened_at = time.time() - 60
    trades.save(cycle)
    trades.record_buy_fill('T1', 300, 100)
    def position(quantity):
        return [dict(symbol='FPT', loanPackageId='1', id='D1', openQuantity=quantity,
            costPrice=100000, price_unit='VND')]
    first = dict(id='EXT1', symbol='FPT', loanPackageId='1', side='NS', quantity=300,
        fillQuantity=100, averagePrice=101000, price_unit='VND',
        modifiedDate=time.time(), fee=10000, tax=20000)
    service.reconcile_external_sells(position(200), [first])
    middle = {**first, 'fillQuantity': 200, 'fee': 15000, 'tax': 25000}
    middle.pop(missing_component)
    service.reconcile_external_sells(position(100), [middle])
    assert trades.get('T1').fees_paid == 35000
    final = {**middle, 'fee': 0, 'tax': 0}
    for _ in range(3):
        service.reconcile_external_sells(position(100), [final])
    actual = trades.get('T1')
    assert actual.fees_paid == 0 and actual.net_pnl == 200000


def test_buy_partial_fill_crash_restart_settlement_unknown_sell_and_full_exit(tmp_path, monkeypatch):
    broker = OfflineBroker()
    broker.positions = []
    service, queue, trades, rules = engine(tmp_path, broker, quote_provider=lambda symbol: tick(symbol))
    broker.result = BrokerOrderResult(True, 'PartiallyFilled', order_id='B1', raw=dict(
        fillQuantity=100, averagePrice=100000, price_unit='VND', fee=4500, tax=0))
    buy = queue.add(OrderIntent.create('FPT', 'BUY', 300, 'MARKET', execution_mode='REAL',
        source='BOT', trade_id='T1', details={'reservation_price': 100}))
    service.process_due(phase='OPEN', execution_mode='REAL')
    assert trades.get('T1').open_quantity == 100
    def buy_row(filled, average, fee):
        return dict(id='B1', symbol='FPT', side='NB', quantity=300, fillQuantity=filled,
            averagePrice=average, price_unit='VND', fee=fee, tax=0,
            orderStatus='Filled' if filled == 300 else 'PartiallyFilled')
    broker.orders = [buy_row(200, 0, 9090)]
    service.reconcile_working('REAL')
    assert trades.get('T1').open_quantity == queue.get(buy.id).filled_quantity == 100
    broker.orders = [buy_row(200, 101000, 9090)]
    service, queue, trades, rules = engine(tmp_path, broker, quote_provider=lambda symbol: tick(symbol))
    service.reconcile_working('REAL')
    assert trades.get('T1').open_quantity == 200
    broker.orders = [buy_row(300, 102000, 13770)]
    with monkeypatch.context() as patch:
        patch.setattr(trades, 'record_buy_fill', lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('ledger unavailable')))
        with pytest.raises(OSError):
            service.reconcile_working('REAL')
    assert queue.get(buy.id).filled_quantity == trades.get('T1').open_quantity == 200
    service, queue, trades, rules = engine(tmp_path, broker, quote_provider=lambda symbol: tick(symbol))
    assert trades.get('T1').open_quantity == 300 and queue.get(buy.id).status == 'FILLED'
    assert trades.get('T1').buy_notional == 30.6e6 and trades.get('T1').fees_paid == 13770
    sell = queue.add(OrderIntent.create('FPT', 'SELL', 300, 'MARKET', execution_mode='REAL',
        source='EM', trade_id='T1', reason='STOP_LOSS', sell_wait_policy='KEEP'))
    broker.positions = [dict(symbol='FPT', openQuantity=300, tradeQuantity=0)]
    service.process_due(phase='OPEN', execution_mode='REAL')
    assert queue.get(sell.id).status == 'WAITING_SETTLEMENT' and len(broker.sent) == 1
    broker.positions[0]['tradeQuantity'] = 100
    broker.result = BrokerOrderResult(False, 'UNKNOWN', error='ORDER_STATUS_UNKNOWN')
    service.process_due(phase='OPEN', execution_mode='REAL')
    assert len(broker.sent) == 2 and queue.get(sell.id).status == 'UNKNOWN'
    tag = broker.sent[-1].request_tag
    service, queue, trades, rules = engine(tmp_path, broker, quote_provider=lambda symbol: tick(symbol))
    service.process_due(phase='OPEN', execution_mode='REAL')
    assert len(broker.sent) == 2
    first_sell = dict(id='S1', symbol='FPT', side='NS', remark=tag, orderStatus='Filled',
        quantity=100, fillQuantity=100, averagePrice=105000, price_unit='VND', fee=4725, tax=10500)
    broker.orders.append(first_sell)
    for _ in range(3):
        service.reconcile_working('REAL')
    assert trades.get('T1').open_quantity == 200 and queue.get(sell.id).status == 'WAITING_SETTLEMENT'
    broker.positions = [dict(symbol='FPT', openQuantity=200, tradeQuantity=200)]
    broker.result = BrokerOrderResult(True, 'Filled', order_id='S2', raw=dict(
        fillQuantity=200, averagePrice=104000, price_unit='VND', fee=9360, tax=20800))
    service.process_due(phase='OPEN', execution_mode='REAL')
    broker.orders.append(dict(id='S2', symbol='FPT', side='NS', orderStatus='Filled',
        quantity=200, **broker.result.raw))
    service, queue, trades, rules = engine(tmp_path, broker)
    for _ in range(3):
        service.reconcile_working('REAL')
    closed = trades.get('T1')
    assert (closed.status, closed.entry_quantity, closed.sold_quantity, closed.open_quantity) == ('CLOSED', 300, 300, 0)
    assert (closed.fees_paid, closed.net_pnl) == (59155, 640845)
    totals = DailyFeeTracker(tmp_path / 'order_history.csv', tmp_path / 'daily.json').summary(
        'REAL', [closed], daily=False, now=time.time() + 1)
    assert (totals['fees'], totals['pnl'], totals['closed_trades']) == (59155, 640845, 1)
    assert queue.get(sell.id).status == 'FILLED'
    assert len(broker.sent) == 3


@pytest.mark.parametrize('conflict', ['duplicate_signal', 'broker_exit'])
def test_rejected_exit_replacement_preserves_existing_protection(tmp_path, conflict):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1')
    trades.record_buy_fill('T1', 100, 100)
    protection = queue.add(OrderIntent.create('FPT', 'SELL', 100, 'MARKET',
        execution_mode='REAL', source='EM', trade_id='T1', reason='NORMAL_PROTECTION'))
    if conflict == 'duplicate_signal':
        assert rules.claim_signal('FPT', 'SELL', '2026-10-09', stream='REAL')
    else:
        active = queue.add(OrderIntent.create('FPT', 'SELL', 100, 'MARKET',
            execution_mode='REAL', source='EM', trade_id='T1', reason='STOP_LOSS'))
        queue._update(active.id, status='WORKING', broker_order_id='S1')
    decision = StrategyDecision('SELL', 'FPT', 'SELL_SIGNAL', signal='SELL',
        event='INDICATOR_EXIT', scope='POSITION_MANAGEMENT')
    result = StrategyOrderPlanner(queue, trades, rules).plan(decision, execution_mode='REAL',
        execution_style='MARKET', tick=tick(), candle_key='2026-10-09',
        portfolio={'trade_id': 'T1', 'position_quantity': 100})
    assert result.intent is None
    assert queue.get(protection.id).status == 'PENDING'


def test_repeated_external_fee_corrections_remain_signed_in_daily_history(tmp_path):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    cycle = trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    cycle.opened_at = time.time() - 60
    trades.save(cycle)
    trades.record_buy_fill('T1', 300, 100)
    positions = [dict(symbol='FPT', loanPackageId='1', id='D1', openQuantity=200,
        costPrice=100000, price_unit='VND')]
    row = dict(id='EXT1', symbol='FPT', loanPackageId='1', side='NS', quantity=300,
        fillQuantity=100, averagePrice=101000, price_unit='VND',
        modifiedDate=time.time(), fee=10000, tax=0)
    tracker = DailyFeeTracker(tmp_path / 'order_history.csv', tmp_path / 'daily.json')
    for fee in (10000, 20000, 10000, 20000, 0, 0):
        row.update(fee=fee, modifiedDate=time.time())
        for _ in range(2):
            service.reconcile_external_sells(positions, [row])
        assert trades.get('T1').fees_paid == fee
        assert tracker.summary('REAL', daily=False, now=time.time() + 1)['fees'] == fee


def test_filled_submission_without_explicit_quantity_keeps_notional_and_cost_baseline(tmp_path):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    broker.result = BrokerOrderResult(True, 'Filled', order_id='B1', raw=dict(
        averagePrice=100000, price_unit='VND', feeRate=.00045, tax=0))
    item = queue.add(OrderIntent.create('FPT', 'BUY', 100, 'MARKET', execution_mode='REAL',
        source='BOT', trade_id='T1'))
    service.process_due(phase='OPEN', execution_mode='REAL')
    recorded = queue.get(item.id)
    assert (recorded.broker_notional_logged, recorded.broker_fee_logged) == (10e6, 4500)
    assert trades.get('T1').fees_paid == 4500
    broker.orders = [dict(id='B1', symbol='FPT', side='NB', orderStatus='Filled', quantity=100,
        fillQuantity=100, **broker.result.raw)]
    for _ in range(3):
        service.reconcile_working('REAL')
    assert trades.get('T1').fees_paid == 4500 and len(broker.sent) == 1
    totals = DailyFeeTracker(tmp_path / 'order_history.csv', tmp_path / 'daily.json').summary(
        'REAL', daily=False, now=time.time() + 1)
    assert totals['fees'] == 4500


def test_legacy_external_cost_sum_waits_for_both_components_without_inventing_tax(tmp_path):
    service, queue, trades, rules = engine(tmp_path, OfflineBroker())
    cycle = trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    cycle.opened_at = time.time() - 60
    cycle.record_buy_fill(300, 100)
    cycle.record_sell_fill(100, 101, 30000)
    cycle.external_progress = {'EXT1': {'filled': 100, 'notional': 10.1e6, 'cost': 30000}}
    trades.save(cycle)
    positions = [dict(symbol='FPT', loanPackageId='1', id='D1', openQuantity=100,
        costPrice=100000, price_unit='VND')]
    partial = dict(id='EXT1', symbol='FPT', loanPackageId='1', side='NS', quantity=300,
        fillQuantity=200, averagePrice=101000, price_unit='VND',
        modifiedDate=time.time(), fee=15000)
    service.reconcile_external_sells(positions, [partial])
    assert trades.get('T1').fees_paid == 30000
    assert 'tax' not in trades.get('T1').external_progress['EXT1']
    complete = {**partial, 'tax': 20000}
    for _ in range(3):
        service.reconcile_external_sells(positions, [complete])
    assert trades.get('T1').fees_paid == 35000
    assert trades.get('T1').open_quantity == 100


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('old_reconciled_price', [False, True])
def test_legacy_zero_notional_fill_is_quarantined_and_keeps_capital_reserved(tmp_path, side, old_reconciled_price):
    broker = OfflineBroker()
    service, queue, trades, rules = engine(tmp_path, broker)
    trades.create('FPT', 'REAL', trade_id='T1')
    if side == 'SELL':
        trades.record_buy_fill('T1', 100, 100)
    item = queue.add(OrderIntent.create('FPT', side, 100, 'MARKET', execution_mode='REAL',
        source='BOT' if side == 'BUY' else 'EM', trade_id='T1', details={'reservation_price': 100}))
    # Persist exactly the quantity advancement an older build performed,
    # without inventing evidence that the missing ledger fill was applied.
    queue.finish(item, BrokerOrderResult(True, 'Filled', order_id='B1', raw=dict(
        fillQuantity=100, averagePrice=0, price_unit='VND')), submitted_quantity=100)
    before = trades.get('T1').to_dict()
    broker.orders = [dict(id='B1', symbol='FPT', side='NB' if side == 'BUY' else 'NS',
        orderStatus='Filled', quantity=100, fillQuantity=100, averagePrice=100000, price_unit='VND')]
    if old_reconciled_price:
        # The old build advanced its notional too, still with delta quantity0
        # and no ledger fill. A positive current notional does not prove safety.
        queue.reconcile_broker(queue.get(item.id), broker.orders[0])
    service, queue, trades, rules = engine(tmp_path, broker)
    for _ in range(3):
        service.reconcile_working('REAL')
        service.process_due(phase='OPEN', execution_mode='REAL')
    quarantined = queue.get(item.id)
    assert quarantined.status == 'UNKNOWN'
    assert quarantined.details['fill_accounting_reconcile_required'] == 'B1'
    assert quarantined.details['unaccounted_fill_quantity'] == 100
    assert 'LEGACY_FILL_ACCOUNTING_UNVERIFIED' in quarantined.result
    assert trades.get('T1').to_dict() == before and broker.sent == []
    if side == 'BUY':
        context = PortfolioContextBuilder(queue, trades, rules).build('VNM', execution_mode='REAL',
            balance={'equity': 20e6, 'availableCash': 10e6}, positions=[], tick=tick('VNM', 20),
            exposure=.5, max_positions=5, no_compound_enabled=False)
        assert context['pending_buy_value'] == 10e6 and context['order_budget'] == 0
    app = headless_app(tmp_path, balance={}, positions=[], symbols=['VNM'])
    buy = OrderIntent.create('VNM', 'BUY', 100, 'MARKET', source='BOT', execution_mode='REAL')
    assert app._check_bot_entry_limits(buy, tick('VNM', 20)) == 'FILL_ACCOUNTING_RECONCILE_REQUIRED'


@pytest.mark.parametrize('manual_buy', [True, False])
@pytest.mark.parametrize('broker_package, expected_cost', [('1', 10e6), ('2', 20e6)])
def test_priority_reserves_confirmed_holdings_by_package_for_manual_and_bot(tmp_path, manual_buy, broker_package, expected_cost):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    trades.record_buy_fill('T1', 100, 100)
    rows = [dict(symbol='FPT', loanPackageId=broker_package, openQuantity=100,
        costPrice=100000, marketPrice=100000, price_unit='VND')]
    context = PortfolioContextBuilder(queue, trades, rules).build('FPT', execution_mode='REAL',
        balance={'equity': 100e6, 'availableCash': 80e6}, positions=rows, tick=tick(),
        exposure=1, max_positions=5, no_compound_enabled=False,
        priority_symbols=['FPT'], priority_capital_enabled=True, priority_total_capital=20e6,
        priority_allocations={'FPT': {'limit_vnd': 20e6, 'use_pct': 100, 'max_orders': 3}},
        budget_only=True, manual_buy=manual_buy)
    assert context['priority_capital']['holding_cost_vnd'] == expected_cost
    assert context['current_stock_value'] == expected_cost
    assert context['order_budget'] == 20e6 - expected_cost


@pytest.mark.parametrize('row_prices', [{}, {'marketPrice': 0, 'costPrice': 100000}])
@pytest.mark.parametrize('broker_quantity', [100, 50])
def test_confirmed_fill_stays_reserved_when_matching_snapshot_has_no_usable_price(tmp_path, row_prices, broker_quantity):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    trades.record_buy_fill('T1', 100, 100)
    row = dict(symbol='FPT', loanPackageId='1', openQuantity=broker_quantity, price_unit='VND', **row_prices)
    context = PortfolioContextBuilder(queue, trades, rules).build('VNM', execution_mode='REAL',
        balance={'equity': 20e6, 'availableCash': 10e6}, positions=[row], tick=tick('VNM', 20),
        exposure=.5, max_positions=5, no_compound_enabled=False)
    assert context['current_stock_value'] == 10e6 and context['order_budget'] == 0


def test_external_closed_cycle_fee_corrections_update_capital_pnl_and_history(tmp_path):
    service, queue, trades, rules = engine(tmp_path, OfflineBroker())
    cycle = trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    cycle.opened_at = time.time() - 60
    trades.save(cycle)
    trades.record_buy_fill('T1', 300, 100)
    row = dict(id='EXT1', symbol='FPT', loanPackageId='1', side='NS', quantity=300,
        fillQuantity=300, averagePrice=99900, price_unit='VND',
        modifiedDate=time.time(), fee=20000, tax=0)
    tracker = DailyFeeTracker(tmp_path / 'order_history.csv', tmp_path / 'daily.json')
    for fee in (20000, 10000, 30000, 20000, 0, 0):
        row.update(fee=fee, modifiedDate=time.time())
        for _ in range(2):
            service.reconcile_external_sells([], [row])
        actual = trades.get('T1')
        assert actual.status == 'CLOSED'
        assert actual.fees_paid == fee and actual.net_pnl == -30000 - fee
        assert trades.capital_available('FPT', 'REAL', 1e9) == 30e6 - 30000 - fee
        assert tracker.summary('REAL', daily=False, now=time.time() + 1)['fees'] == fee


@pytest.mark.parametrize('broker_cost, expected_committed', [(50000, 20e6), (100000, 20e6), (110000, 22e6)])
def test_priority_matching_quantity_never_releases_confirmed_acquisition_cost(tmp_path, broker_cost, expected_committed):
    queue, trades, rules = stores(tmp_path)
    trades.create('FPT', 'REAL', trade_id='T1', loan_package_id='1')
    trades.record_buy_fill('T1', 200, 100)
    context = PortfolioContextBuilder(queue, trades, rules).build('FPT', execution_mode='REAL',
        balance={'equity': 100e6, 'availableCash': 80e6}, tick=tick(),
        positions=[dict(symbol='FPT', loanPackageId='1', openQuantity=200,
            costPrice=broker_cost, marketPrice=100000, price_unit='VND')],
        exposure=1, max_positions=5, no_compound_enabled=False,
        priority_symbols=['FPT'], priority_capital_enabled=True, priority_total_capital=20e6,
        priority_allocations={'FPT': {'limit_vnd': 20e6, 'use_pct': 100}},
        budget_only=True, manual_buy=True)
    assert context['priority_capital']['holding_cost_vnd'] == expected_committed
    assert context['order_budget'] == 0
