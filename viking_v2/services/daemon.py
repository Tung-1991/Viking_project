from __future__ import annotations

import argparse
import signal
import threading
import time
from datetime import datetime

from .. import config
from ..config import load_settings
from ..connections.dnse.paper import PaperBroker
from ..connections.dnse.client import DNSEClient
from ..connections.dnse.websocket import DNSEMarketWS
from .runtime import setup_logging
from ..trading.market import MarketDataService
from ..trading.market import VN_TZ, market_phase, merge_tick_into_daily_bars
from ..models import RuntimeStatus
from ..trading.orders import OrderQueue
from ..trading.portfolio import PortfolioContextBuilder
from .runtime import RuntimeBridge
from ..storage import SignalLog, AtomicJSONStore
from ..rules.state import RuleStateStore
from ..rules.business import StaticRule, StaticRuleParameters, classify_market_state
from ..trading.state import TradeStateStore


def merge_live_tick(
    live_tick: dict | None,
    previous_tick: dict | None,
    fallback_tick: dict | None,
) -> dict:
    """Keep the last tradable price when WS only publishes bid/ask updates."""
    live = dict(live_tick or {})
    previous = dict(previous_tick or {})
    fallback = dict(fallback_tick or {})
    merged = dict(fallback)
    positive_price_fields = {
        "price", "lastPrice", "matchPrice", "expected_price", "expectedPrice",
        "reference", "referencePrice", "high", "low", "open", "bid", "ask",
    }
    for source in (previous, live):
        for key, value in source.items():
            if key in positive_price_fields:
                try:
                    if float(value or 0.0) <= 0:
                        continue
                except (TypeError, ValueError):
                    continue
            merged[key] = value
    live_has_price = any(
        float(live.get(key, 0.0) or 0.0) > 0
        for key in ("price", "lastPrice", "matchPrice", "expected_price", "expectedPrice")
    )
    merged["frozen"] = False
    merged["price_frozen"] = not live_has_price
    return merged


def run(account_id: str | None = None) -> int:
    bridge = RuntimeBridge(account_id)
    logger = setup_logging(bridge.log_dir, "daemon")
    # Every daemon process begins disarmed, even if a stale file said ON.
    runtime = bridge.disarm()
    client = DNSEClient(account_no=None if account_id in {None, "PAPER"} else account_id)
    ws = DNSEMarketWS(client.api_key, client.api_secret)
    market = MarketDataService(client, ws)
    settings = load_settings(account_id)
    rule = StaticRule(StaticRuleParameters.from_dict(settings.rule_parameters))
    rule_state = RuleStateStore(bridge.rule_state_path)
    trades = TradeStateStore(bridge.trade_state_path)
    queue = OrderQueue(bridge.pending_orders_path)
    signal_log = SignalLog(bridge.signal_log_path)
    portfolio_builder = PortfolioContextBuilder(
        queue, trades, rule_state, buy_fee_rate=lambda: settings.buy_fee_pct / 100.0,
    )
    paper = PaperBroker(
        bridge.paper_state_path,
        initial_balance=settings.paper_initial_balance,
        fee_rates=lambda: (
            settings.buy_fee_pct / 100.0,
            settings.sell_fee_pct / 100.0,
            settings.sell_tax_pct / 100.0,
        ),
        tick_provider=market.get_tick,
        working_dates_provider=lambda: [
            value for value in client.get_working_dates()
            if str(value)[:10] not in set(load_settings(account_id).trading_holidays)
        ],
    )
    market_cache = AtomicJSONStore(bridge.market_cache_path, default={})
    running = True
    status_lock = threading.RLock()
    heartbeat_stop = threading.Event()
    latest_status: RuntimeStatus | None = None
    last_status_write_error = 0.0

    def publish_status(status: RuntimeStatus) -> None:
        nonlocal latest_status, last_status_write_error
        with status_lock:
            status.heartbeat_at = time.time()
            latest_status = status
            try:
                bridge.write_status(status)
            except OSError:
                # Status is a UI bridge, not a reason to stop market/rule work.
                # The next loop and heartbeat will retry automatically.
                now = time.time()
                if now - last_status_write_error >= 30.0:
                    logger.exception("Could not persist daemon status; daemon remains running")
                    last_status_write_error = now

    def pulse_heartbeat() -> None:
        while not heartbeat_stop.wait(config.HEARTBEAT_SECONDS):
            with status_lock:
                if latest_status is None or latest_status.daemon_status != "RUNNING":
                    continue
                latest_status.heartbeat_at = time.time()
                try:
                    bridge.write_status(latest_status)
                except Exception:
                    logger.exception("Could not persist daemon heartbeat")

    def stop(_signum=None, _frame=None):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    connected = client.connect()
    symbols = list(runtime.watchlist)
    if connected and symbols:
        market.start(symbols)
    logger.info("Viking V2 daemon started; BOT is OFF.")
    last_symbols: list[str] = []
    previous_runtime_status = bridge.read_status()
    ticks: dict[str, dict] = dict(previous_runtime_status.get("ticks") or {})
    decisions: dict[str, dict] = dict(previous_runtime_status.get("decisions") or {})
    initial_phase = "CALENDAR_UNKNOWN" if connected else "NOT_CONFIGURED"
    publish_status(
        RuntimeStatus(
            heartbeat_at=time.time(),
            daemon_status="RUNNING",
            market_status=initial_phase if connected else "NOT_CONFIGURED",
            bot_enabled=False,
            active_symbols=symbols,
            ticks=ticks,
            decisions=decisions,
            api_health=market.health(),
            working_dates=[],
        )
    )
    heartbeat_thread = threading.Thread(
        target=pulse_heartbeat,
        name="viking-daemon-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    error = ""
    saved_market = market_cache.read()
    saved_market = saved_market if isinstance(saved_market, dict) else {}
    cached_working_dates: list[str] = [
        str(value)[:10]
        for value in (saved_market.get("working_dates") or [])
        if str(value).strip()
    ]
    bars_by_symbol: dict[str, list[dict]] = {
        str(key).upper(): list(value)
        for key, value in (saved_market.get("symbols") or {}).items()
        if isinstance(value, list)
    }
    vnindex_bars: list[dict] = list(saved_market.get("vnindex") or [])
    last_vnindex_refresh = 0.0
    last_symbol_history_refresh = 0.0
    settings_fingerprint = ""
    market_history_key = ""
    exit_code = 0
    try:
        while running:
            started = time.time()
            cycle_error = ""
            runtime = bridge.read_config()
            settings = load_settings(account_id)
            next_fingerprint = repr(settings.rule_parameters)
            if next_fingerprint != settings_fingerprint:
                rule = StaticRule(StaticRuleParameters.from_dict(settings.rule_parameters))
                settings_fingerprint = next_fingerprint
            symbols = list(runtime.watchlist)
            if symbols != last_symbols:
                market.set_symbols(symbols)
                if connected:
                    market.start(symbols)
                last_symbols = list(symbols)
            raw_working_dates = client.get_working_dates() if connected else []
            if raw_working_dates:
                cached_working_dates = [str(value)[:10] for value in raw_working_dates]
            blocked_dates = set(settings.trading_holidays)
            working_dates = [
                value for value in raw_working_dates
                if str(value)[:10] not in blocked_dates
            ]
            if not working_dates and cached_working_dates:
                working_dates = [
                    value for value in cached_working_dates
                    if str(value)[:10] not in blocked_dates
                ]
            if connected and not working_dates:
                # Never guess a tradable weekday when the DNSE calendar is
                # unavailable and no last-known-good calendar exists.
                phase = "CALENDAR_UNKNOWN"
            else:
                phase, _label = market_phase(
                    working_dates=working_dates if connected else None,
                    holidays=settings.trading_holidays,
                )
            if connected:
                live_phase = phase in {"ATO", "OPEN", "ATC"}
                now_ts = time.time()
                cache_changed = False
                vnindex_refresh_after = 30.0 if live_phase else 1800.0
                if not vnindex_bars or now_ts - last_vnindex_refresh >= vnindex_refresh_after:
                    vnindex_bars = market.get_daily_bars("VNINDEX", count=260)
                    last_vnindex_refresh = now_ts
                    cache_changed = True
                missing_symbols = [symbol for symbol in symbols if not bars_by_symbol.get(symbol)]
                refresh_symbol_history = (
                    bool(missing_symbols)
                    or now_ts - last_symbol_history_refresh >= 1800.0
                )
                if refresh_symbol_history:
                    targets = symbols if now_ts - last_symbol_history_refresh >= 1800.0 else missing_symbols
                    for symbol in targets:
                        bars_by_symbol[symbol] = market.get_daily_bars(symbol, count=260)
                    last_symbol_history_refresh = now_ts
                    cache_changed = True
                if cache_changed:
                    market_cache.write({
                        "updated_at": now_ts,
                        "working_dates": cached_working_dates,
                        "vnindex": vnindex_bars,
                        "symbols": bars_by_symbol,
                    })
                last_market_bar = vnindex_bars[-1] if vnindex_bars else {}
                market_session_key = str(last_market_bar.get("time", "") or datetime.now(VN_TZ).date().isoformat())
                next_history_key = f"{market_session_key}|{settings_fingerprint}"
                if vnindex_bars and next_history_key != market_history_key:
                    observations: list[tuple[str, str]] = []
                    previous_classified = "UNKNOWN"
                    start = min(len(vnindex_bars), max(1, int(rule.params.ma_period)))
                    for end in range(start, len(vnindex_bars) + 1):
                        historical_state, _ = classify_market_state(
                            vnindex_bars[:end],
                            previous_state=previous_classified,
                            params=rule.params,
                        )
                        if historical_state in {
                            "UPTREND", "DOWNTREND", "ACCUMULATION", "DISTRIBUTION"
                        }:
                            previous_classified = historical_state
                            historical_session = str(
                                vnindex_bars[end - 1].get("time", "") or end
                            )
                            observations.append((historical_state, historical_session))
                    rule_state.rebuild_market_confirmation(
                        observations,
                        rule.params.confirm_sessions,
                    )
                    market_history_key = next_history_key
                raw_market_state, _market_details = classify_market_state(
                    vnindex_bars,
                    previous_state=rule_state.confirmed_market_state(),
                    params=rule.params,
                )
                confirmed_market_state = rule_state.observe_market_candidate(
                    raw_market_state,
                    market_session_key,
                    rule.params.confirm_sessions,
                )
                market_confirmation = rule_state.market_confirmation(rule.params.confirm_sessions)
                if runtime.paper_mode:
                    balance = paper.get_balance()
                    positions = paper.get_positions()
                else:
                    balance = client.get_balance() or {}
                    positions = client.get_positions()
                for symbol in symbols:
                    try:
                        if phase in {"ATO", "OPEN", "ATC"}:
                            live_tick = market.get_tick(symbol)
                            if live_tick:
                                fallback_tick = market.frozen_tick_from_bars(
                                    symbol, bars_by_symbol.get(symbol, [])
                                )
                                tick = merge_live_tick(
                                    live_tick,
                                    ticks.get(symbol),
                                    fallback_tick,
                                )
                        else:
                            tick = ticks.get(symbol)
                            if tick:
                                tick = {**tick, "frozen": True}
                            else:
                                tick = market.frozen_tick_from_bars(symbol, bars_by_symbol.get(symbol, []))
                        if tick:
                            ticks[symbol] = tick
                            if live_phase:
                                bars_by_symbol[symbol] = merge_tick_into_daily_bars(
                                    bars_by_symbol.get(symbol, []),
                                    tick,
                                )
                            bars = bars_by_symbol.get(symbol, [])
                            portfolio_tick = dict(tick)
                            if bars:
                                portfolio_tick["daily_close"] = float(bars[-1].get("close", 0.0) or 0.0)
                                portfolio_tick["daily_bar_closed"] = bool(bars[-1].get("closed", False))
                            context = dict(tick)
                            context["market_phase"] = phase
                            context["bars"] = bars_by_symbol.get(symbol, [])
                            context["vnindex_bars"] = vnindex_bars
                            context["signal_mode"] = settings.signal_mode
                            context["previous_market_state"] = rule_state.confirmed_market_state()
                            context["confirmed_market_state"] = confirmed_market_state
                            context["market_confirmation"] = market_confirmation
                            candle_key = str((bars[-1] if bars else {}).get("time", "") or "")
                            exposure = rule.params.exposure.get(confirmed_market_state, 0.0)
                            portfolio = portfolio_builder.build(
                                symbol,
                                execution_mode="PAPER" if runtime.paper_mode else "REAL",
                                balance=balance,
                                positions=positions,
                                tick=portfolio_tick,
                                exposure=exposure,
                                max_positions=rule.params.max_positions,
                                no_compound_enabled=rule.params.no_compound_enabled,
                                corporate_actions=settings.corporate_actions,
                                working_dates=working_dates,
                            )
                            decision = rule.evaluate(context, portfolio)
                            decision.details["candle_key"] = candle_key
                            decision.details["order_budget"] = portfolio.get("order_budget", 0.0)
                            decision.details["trade_id"] = portfolio.get("trade_id", "")
                            decision.details["position_quantity"] = portfolio.get("position_quantity", 0)
                            decisions[symbol] = decision.to_dict()
                            # Sổ tín hiệu ghi cả khi bot tắt hoặc hết slot: nó
                            # dùng để chấm luật, không phải để chấm bot.
                            marks = decision.details.get("indicators") or {}
                            signal_log.record({
                                "timestamp": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "signal": decision.signal,
                                "price": portfolio_tick.get("price", 0.0),
                                "ema_fast": round(float(marks.get("buy_ema_fast") or 0.0), 4),
                                "ema_slow": round(float(marks.get("buy_ema_slow") or 0.0), 4),
                                "rsi": round(float(marks.get("rsi") or 0.0), 2),
                                "market_state": confirmed_market_state,
                                "acted": decision.action,
                                "blocked_by": "" if decision.action != "WAIT" else decision.reason,
                            })
                    except Exception as exc:
                        cycle_error = str(exc)
                        logger.warning("Market update %s failed: %s", symbol, exc)
            publish_status(
                RuntimeStatus(
                    heartbeat_at=time.time(),
                    daemon_status="RUNNING",
                    market_status=phase if connected else "NOT_CONFIGURED",
                    bot_enabled=bool(runtime.bot_enabled),
                    active_symbols=symbols,
                    ticks=ticks,
                    decisions=decisions,
                    api_health=market.health(),
                    error=cycle_error,
                    working_dates=working_dates,
                )
            )
            elapsed = time.time() - started
            time.sleep(max(0.05, config.DAEMON_LOOP_SECONDS - elapsed))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        logger.exception("Viking V2 daemon stopped by an unexpected error")
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=max(1.0, config.HEARTBEAT_SECONDS + 0.5))
        market.stop()
        client.close()
        publish_status(
            RuntimeStatus(
                heartbeat_at=time.time(),
                daemon_status="STOPPED",
                market_status="OFFLINE",
                bot_enabled=False,
                active_symbols=symbols,
                ticks=ticks,
                decisions=decisions,
                error=error,
                working_dates=[],
            )
        )
        logger.info("Viking V2 daemon stopped.")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Viking V2 static-rule daemon")
    parser.add_argument("--account", default=config.active_account_id())
    args = parser.parse_args(argv)
    return run(args.account)


if __name__ == "__main__":
    raise SystemExit(main())
