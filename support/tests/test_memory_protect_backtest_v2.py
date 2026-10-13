"""End-to-end replay proof for retained EMA and independent protection sizing."""
from datetime import datetime, timedelta

import pytest

from viking_v2.backtest.data import HistoricalDataStore
from viking_v2.backtest.engine import BacktestEngine, _Position
from viking_v2.backtest.models import BacktestConfig
from viking_v2.backtest.replay import ReplayDataStore
from viking_v2.trading.market import VN_TZ


def replay_book(tmp_path, days, prices, *, daily_price=8):
    first = datetime.fromisoformat(days[0]).replace(tzinfo=VN_TZ)
    history = [first - timedelta(days=260-i) for i in range(260)]
    history += [datetime.fromisoformat(day).replace(tzinfo=VN_TZ) for day in days]
    values = [daily_price] * len(history)
    data = {"t": [int(at.timestamp()) for at in history],
            **{key: list(values) for key in ("o", "h", "l", "c")}, "v": [10000]*len(history)}
    store = HistoricalDataStore(root=tmp_path / "data", fetcher=lambda _s, res, *_: data if res == "1D" else None)
    replay = ReplayDataStore(store.root / "replay")
    source = tmp_path / "HOSE_DLY_FPT, 1.csv"
    lines = ["time,open,high,low,close,Volume"]
    for day, points in zip(days, prices):
        for clock, opened, closed in points:
            at = datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=VN_TZ)
            lines.append(f"{int(at.timestamp())},{opened},{max(opened,closed)},{min(opened,closed)},{closed},100")
    source.write_text("\n".join(lines)+"\n", encoding="utf-8")
    replay.import_file(source, price_scale=1)
    return BacktestEngine(store, replay)


@pytest.mark.parametrize("mode,va_lookback,expected", [("VA",False,0),("VA",True,1),("SESSIONS",False,1)])
def test_starting_replay_after_a_prior_session_cross_uses_configured_lookback(tmp_path,mode,va_lookback,expected):
    day = datetime(2026,10,5,tzinfo=VN_TZ)
    earlier = [day-timedelta(days=i) for i in range(60,0,-1) if (day-timedelta(days=i)).weekday()<5]
    days = [at.date().isoformat() for at in earlier]+['2026-10-05']
    points = []
    for date in days:
        if date < '2026-10-02':
            points.append([('09:15',8,8),('14:45',8,8)])
        elif date == '2026-10-02':
            points.append([('09:15',8,8),('14:09',8,9),('14:45',9,9)])
        else:
            points.append([('09:15',9,9),('14:00',9,9),('14:01',9,9),('14:45',9,9)])
    engine = replay_book(tmp_path,days,points)
    carry = {}
    result = engine.run(BacktestConfig(['FPT'],'2026-10-05','2026-10-05',initial_capital=100e6,
        fixed_market_phase='UPTREND',fixed_exposure_pct=100,simulation_mode='REPLAY',fill_session='CONTINUOUS',
         rule_parameters={'buy_ema_cross_mode':mode,'buy_ema_cross_sessions':3,
                          'buy_ema_va_lookback_enabled':va_lookback,'buy_signal_use_rsi':False,
                         'buy_window_enabled':True,'buy_window_start':'14:00','whipsaw_enabled':False}),save=False,carry=carry)
    buys = [event for event in result.events if event.side == 'BUY']
    assert len(buys) == expected
    if expected:
        memory = carry['session_crosses']['FPT']
        assert memory['cross_at'] == '2026-10-02T14:10:00+07:00'
        assert memory['used'] and memory['lookback_up_count'] == 1
        assert datetime.fromisoformat(buys[0].fill_time).strftime('%H:%M') == '14:01'


@pytest.mark.parametrize("mode,sessions,down,expected", [("VA",3,False,1), ("SESSIONS",3,False,1),
                                                       ("SESSIONS",1,False,0), ("VA",3,True,0)])
def test_atc_cross_survives_carry_to_next_buy_window_without_day2_cross(tmp_path, monkeypatch, mode, sessions, down, expected):
    import viking_v2.backtest.engine as module
    days = ["2026-10-09", "2026-10-12"]
    next_price = 7 if down else 9
    engine = replay_book(tmp_path, days, [
        [("09:15",8,8),("14:29",8,8),("14:30",8,9),("14:45",9,9)],
        [("09:15",next_price,next_price),("14:00",next_price,next_price),
         ("14:01",next_price,next_price),("14:45",next_price,next_price)],
    ])
    actual = module.indicator_snapshot
    def indicators(rows, *args, **kwargs):
        row = actual(rows, *args, **kwargs)
        date = datetime.fromtimestamp(rows[-1]["time"], VN_TZ).date().isoformat()
        row.update(rsi=60 if date == days[1] else 55, rsi_previous=55)
        return row
    monkeypatch.setattr(module, "indicator_snapshot", indicators)
    params = {"buy_ema_fast":2,"buy_ema_slow":3,"rsi_period":2,"buy_window_start":"14:00",
              "buy_window_enabled":True,"buy_ema_cross_mode":mode,"buy_ema_cross_sessions":sessions,
              "whipsaw_enabled":False,"max_positions":1,"no_compound_enabled":False}
    carry = {}
    def run(day):
        return engine.run(BacktestConfig(["FPT"],day,day,initial_capital=100e6,
                          fixed_market_phase="UPTREND",fixed_exposure_pct=100,
                          fill_session="CONTINUOUS",simulation_mode="REPLAY",rule_parameters=params,export_signals=True),
                          save=False,carry=carry)
    first = run(days[0])
    assert not [event for event in first.events if event.side == "BUY"]
    identity = carry["session_crosses"]["FPT"]["cross_id"]
    second = run(days[1])
    buys = [event for event in second.events if event.side == "BUY"]
    assert len(buys) == expected, [(row.get("time"),row["reason"],row["details"].get("ema_cross"),row["details"].get("indicators")) for row in second.signals]
    assert carry["session_crosses"]["FPT"]["cross_id"] == identity
    assert carry["session_crosses"]["FPT"]["up_count"] == 1
    if expected:
        assert datetime.fromisoformat(buys[0].fill_time).strftime("%H:%M") == "14:01"


@pytest.mark.parametrize("stage", ["ARM", "DYN"])
@pytest.mark.parametrize("basis,expected", [("ORIGINAL",[300,300,300]),("REMAINING",[300,200,100])])
def test_replay_repeats_on_lower_prices_not_stationary_and_uses_filled_original(tmp_path, stage, basis, expected):
    day = "2026-10-13"
    prices = [105,104,103] if stage == "ARM" else [102.8,102.7,102.6]
    points = [(f"09:{15+i:02d}",price,price) for i, price in enumerate(
        [prices[0],prices[0],prices[1],prices[1],prices[2],prices[2]])]
    points.append(("14:45",prices[-1],prices[-1]))
    engine = replay_book(tmp_path,[day],[points],daily_price=100)
    position = _Position("T","FPT",1000,1000,100,"2026-10-05","2026-10-07",0,100e6,["NORMAL"],
                         peak_profit_pct=10 if stage == "ARM" else 4, normal_armed=stage == "ARM",sl_pct=-3.5)
    config = BacktestConfig(["FPT"],day,day,initial_capital=100e6,simulation_mode="REPLAY",
                           fill_session="CONTINUOUS",rule_parameters={"buy_window_enabled":False,
                           "normal_sell_basis":basis,"dynamic_sell_basis":basis,"normal_atr_activation_enabled":False,
                           "normal_atr_trail_enabled":False,"normal_retention_until_enabled":False})
    result = engine.run(config,save=False,carry={"cash":0,"positions":{"FPT":position}})
    sells = [event for event in result.events if event.side == "SELL"]
    assert [event.quantity for event in sells] == expected
    assert all(event.reason == ("NORMAL_PROTECTION" if stage == "ARM" else "DYNAMIC_PROTECTION") for event in sells)
    assert position.quantity == 1000-sum(expected)


def test_e_auto_wins_full_remainder_over_protect_on_same_replay_observation(tmp_path, monkeypatch):
    import viking_v2.backtest.engine as module
    day = "2026-10-13"
    engine = replay_book(tmp_path,[day],[[("09:15",105,105),("09:16",104,104),("14:45",104,104)]],daily_price=100)
    actual = module.indicator_snapshot
    def e_marks(*args,**kwargs):
        marks = actual(*args,**kwargs)
        marks.update(sell_ema_fast=101 if args[0][-1].get("closed", True) else 99,
                     sell_ema_slow=100,rsi=54,rsi_previous=55)
        return marks
    monkeypatch.setattr(module,"indicator_snapshot",e_marks)
    position = _Position("T","FPT",1000,1000,100,"2026-10-05","2026-10-07",0,100e6,["NORMAL","IND_EXIT"],
                         peak_profit_pct=10,normal_armed=True,sl_pct=-3.5)
    result = engine.run(BacktestConfig(["FPT"],day,day,simulation_mode="REPLAY",fill_session="CONTINUOUS",
                        rule_parameters={"buy_window_enabled":False,"indicator_exit_policy":"AUTO"}),
                        save=False,carry={"cash":0,"positions":{"FPT":position}})
    sells = [event for event in result.events if event.side == "SELL"]
    assert len(sells) == 1 and sells[0].quantity == 1000
    assert sells[0].event == "INDICATOR_EXIT"
