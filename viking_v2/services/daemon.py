from __future__ import annotations

import argparse
import math
import signal
import threading
import time
from datetime import datetime
from typing import Any, Iterable

from .. import config
from ..branding import APP_NAME
from ..config import load_settings
from ..connections.dnse.paper import PaperBroker
from ..connections.dnse.client import DNSEClient
from ..connections.dnse.snapshot_health import account_poll_interval
from ..connections.dnse.websocket import DNSEMarketWS
from .runtime import setup_logging
from ..trading.market import MarketDataService
from ..trading.market import VN_TZ, market_phase, merge_tick_into_daily_bars, normalize_exchange
from ..models import RuntimeStatus, StrategyDecision
from ..trading.orders import FINAL_STATUSES, OrderQueue
from ..trading.portfolio import PortfolioContextBuilder, sell_quantity_for_fraction
from .runtime import RuntimeBridge
from ..storage import AtomicJSONStore
from ..rules.state import RuleStateStore
from ..trading.validation import MAX_DECISION_AGE, quote_diagnostics
from ..rules.business import (
    StaticRule,
    StaticRuleParameters,
    average_true_range_pct,
    classify_market_state,
    indicator_snapshot,
)
from ..rules.entry_filters import apply_buy_filters
from ..trading.state import TradeStateStore
from ..trading.durable import AccountLease


def merge_live_tick(
    live_tick: dict | None,
    previous_tick: dict | None,
    fallback_tick: dict | None,
) -> dict:
    """Keep price context, but take receipt/health only from this observation."""
    live = dict(live_tick or {})
    previous = dict(previous_tick or {})
    fallback = dict(fallback_tick or {})
    merged = dict(fallback)
    observation_fields = {
        "stale", "frozen", "health", "timestamp", "received_at", "source", "quote_issue",
    }
    for key in observation_fields:
        merged.pop(key, None)
    positive_price_fields = {
        "price", "lastPrice", "matchPrice", "expected_price", "expectedPrice",
        "reference", "referencePrice", "high", "low", "open", "bid", "ask",
    }
    for source in (previous, live):
        for key, value in source.items():
            if source is previous and key in observation_fields:
                continue
            if key in positive_price_fields:
                try:
                    if not math.isfinite(float(value or 0.0)) or float(value or 0.0) <= 0:
                        continue
                except (TypeError, ValueError):
                    continue
            merged[key] = value
    def positive_live_price(key: str) -> bool:
        try:
            value = float(live.get(key) or 0.0)
            return math.isfinite(value) and value > 0
        except (TypeError, ValueError):
            return False

    live_has_price = any(positive_live_price(key) for key in (
        "price", "lastPrice", "matchPrice", "expected_price", "expectedPrice",
    ))
    # Never overwrite flags carried by an unhealthy NEW quote. Cached flags
    # above are discarded; quote validation still checks the new timestamp.
    merged["frozen"] = bool(live.get("frozen", not bool(live)))
    merged["price_frozen"] = not live_has_price
    return merged


class QuoteHealthMonitor:
    """One loss/recovery log per symbol episode, shared across REAL/PAPER."""

    def __init__(self, logger: Any):
        self.logger = logger
        self._issues: dict[str, str] = {}

    def observe(self, symbol: str, quote: Any, *, now: float | None = None) -> dict:
        details = quote_diagnostics(quote, symbol, now=now)
        reason = details["reason"]
        previous = self._issues.get(symbol, "")
        if (reason and not previous) or (not reason and previous):
            age = details["age_seconds"]
            age_text = f"{age:.1f}s" if age is not None else "--"
            try:
                received = datetime.fromtimestamp(details["observed_at"], VN_TZ).isoformat(timespec="seconds")
            except (TypeError, ValueError, OverflowError, OSError):
                received = "--"
            log = self.logger.warning if reason else self.logger.info
            log(
                "[GIÁ] %s · %s · %s · nhận %s · tuổi %s · %s",
                symbol, details["source"], "BỊ LOẠI" if reason else "PHỤC HỒI",
                received, age_text, details["reason_text"],
            )
        self._issues[symbol] = reason
        return details

    def pause(self, symbol: str) -> None:
        """A closed session is not a lost live feed."""
        self._issues.pop(symbol, None)


def tick_with_price_bound(tick: dict, secdef: dict | None) -> dict:
    """Use the adapter's bounded-age reference, never an old tick's daily bound."""
    updated = dict(tick)
    try:
        ceiling = float((secdef or {}).get("ceilingPrice", 0.0) or 0.0)
    except (TypeError, ValueError):
        ceiling = 0.0
    if math.isfinite(ceiling) and ceiling > 0:
        updated["ceiling_price"] = ceiling
    else:
        updated.pop("ceiling_price", None)
    return updated


def realtime_indicator_bucket(at: datetime, interval: str) -> int:
    """Return the exchange-aligned minute bucket containing ``at``."""
    minutes = {"1M": 1, "2M": 2, "5M": 5}.get(str(interval or "").upper(), 0)
    if minutes <= 0:
        return 0
    local = at.astimezone(VN_TZ) if at.tzinfo else at.replace(tzinfo=VN_TZ)
    return int(local.timestamp()) // (minutes * 60)


def indicator_snapshot_at_close(
    bars: list[dict],
    close_price: float,
    params: StaticRuleParameters,
) -> dict:
    """Calculate the shared EMA/RSI snapshot with a frozen provisional D1 close."""
    sampled = [dict(row) for row in bars if isinstance(row, dict)]
    if sampled and float(close_price or 0.0) > 0:
        sampled[-1]["close"] = float(close_price)
    return indicator_snapshot(
        sampled,
        params.buy_ema_fast,
        params.buy_ema_slow,
        params.rsi_period,
        sell_fast=params.sell_ema_fast,
        sell_slow=params.sell_ema_slow,
    )


def active_runtime_symbols(
    watchlist: Iterable[str],
    priority_symbols: Iterable[str],
    cycles: Iterable[Any],
    intents: Iterable[Any],
    execution_mode: str,
) -> list[str]:
    """Keep removed-but-active trades managed without allowing new re-entry."""
    mode = str(execution_mode or "PAPER").strip().upper()
    values: list[str] = []

    def add(raw: Any) -> None:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in values:
            values.append(symbol)

    for symbol in watchlist:
        add(symbol)
    for symbol in priority_symbols:
        add(symbol)
    for cycle in cycles:
        if (
            str(getattr(cycle, "execution_mode", "") or "").upper() == mode
            and str(getattr(cycle, "status", "") or "").upper() == "OPEN"
            and int(getattr(cycle, "open_quantity", 0) or 0) > 0
        ):
            add(getattr(cycle, "symbol", ""))
    for intent in intents:
        if (
            str(getattr(intent, "execution_mode", "") or "").upper() == mode
            and str(getattr(intent, "status", "") or "").upper() not in FINAL_STATUSES
        ):
            add(getattr(intent, "symbol", ""))
    return values


def run(account_id: str | None = None) -> int:
    bridge = RuntimeBridge(account_id)
    worker_lease = AccountLease(bridge.root, "market-worker.lock")
    logger = setup_logging(bridge.log_dir, "daemon")
    # Every daemon process begins disarmed, even if a stale file said ON.
    runtime = bridge.disarm()
    client = DNSEClient(account_no=None if account_id in {None, "PAPER"} else account_id)
    ws = DNSEMarketWS(client.api_key, client.api_secret)
    market = MarketDataService(client, ws)
    settings = load_settings(account_id)
    rule = StaticRule(StaticRuleParameters.from_dict(settings.rule_parameters))
    rule_state = RuleStateStore(bridge.rule_state_path)
    rule_state.discard_buy_candidates(recheck_current_conditions=not (
        rule.params.buy_signal_use_ema and rule.params.buy_signal_require_ema_cross
    ))
    trades = TradeStateStore(bridge.trade_state_path)
    queue = OrderQueue(bridge.pending_orders_path)
    queue.discard_unsubmitted_bot_buys("Restart: bỏ BUY tự động chưa gửi")
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
    execution_mode = "PAPER" if runtime.paper_mode else "REAL"
    symbols = list(dict.fromkeys(symbol for book in (execution_mode, "REAL" if execution_mode == "PAPER" else "PAPER")
        for symbol in active_runtime_symbols(runtime.watchlist, settings.priority_symbols, trades.list_cycles(), queue.list_all(), book)))
    if connected and symbols:
        market.start(symbols)
    logger.info("%s daemon started; BOT is OFF.", APP_NAME)
    last_symbols: list[str] = []
    previous_runtime_status = bridge.read_status()
    ticks: dict[str, dict] = {symbol: {**tick, "stale": True} for symbol, tick in (previous_runtime_status.get("ticks") or {}).items() if isinstance(tick, dict)}
    quote_health = QuoteHealthMonitor(logger)
    decisions: dict[str, dict] = {}
    last_account_error_logs: dict[str, tuple] = {}
    # No calendar request has completed yet: loading is not a failed request.
    initial_phase = "CALENDAR_LOADING" if connected else "NOT_CONFIGURED"
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
    cached_exchanges: dict[str, str] = {
        str(key).upper(): normalize_exchange(value)
        for key, value in (saved_market.get("exchanges") or {}).items()
        if normalize_exchange(value)
    }
    exchange_retry_after: dict[str, float] = {}
    last_vnindex_refresh = 0.0
    last_symbol_history_refresh = 0.0
    settings_fingerprint = ""
    market_history_key = ""
    exit_code = 0
    try:
        while running:
            started = time.time()
            cycle_error = ""
            cycle_error_context: dict[str, str] = {}
            runtime = bridge.read_config()
            settings = load_settings(account_id)
            next_fingerprint = repr(settings.rule_parameters)
            if next_fingerprint != settings_fingerprint:
                next_params = StaticRuleParameters.from_dict(settings.rule_parameters)
                next_requires_cross = next_params.buy_signal_use_ema and next_params.buy_signal_require_ema_cross
                current_requires_cross = rule.params.buy_signal_use_ema and rule.params.buy_signal_require_ema_cross
                if next_requires_cross != current_requires_cross:
                    rule_state.discard_buy_candidates(
                        recheck_current_conditions=not next_requires_cross,
                    )
                rule = StaticRule(next_params)
                settings_fingerprint = next_fingerprint
            execution_mode = "PAPER" if runtime.paper_mode else "REAL"
            priority_symbol_set = set(settings.priority_symbols)
            entry_symbols = set(runtime.watchlist) | priority_symbol_set
            symbols = list(dict.fromkeys(symbol for book in (execution_mode, "REAL" if execution_mode == "PAPER" else "PAPER")
                for symbol in active_runtime_symbols(runtime.watchlist, settings.priority_symbols, trades.list_cycles(), queue.list_all(), book)))
            active_symbol_set = set(symbols)
            decisions_by_mode = {"PAPER": {}, "REAL": {}}
            decisions = {
                symbol: value for symbol, value in decisions.items()
                if symbol in active_symbol_set
            }
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
            exchange_cache_changed = False
            resolved_exchanges = dict(cached_exchanges)
            if connected and not working_dates:
                # Never guess a tradable weekday when the DNSE calendar is
                # unavailable and no last-known-good calendar exists.
                phase = "CALENDAR_UNKNOWN"
                symbol_phases = {symbol: phase for symbol in symbols}
            else:
                now_market = datetime.now(VN_TZ)
                for symbol in symbols:
                    if cached_exchanges.get(symbol):
                        continue
                    detected = ""
                    if connected and time.time() >= exchange_retry_after.get(symbol, 0.0):
                        secdef = client.get_secdef(symbol) or {}
                        detected = normalize_exchange(
                            secdef.get("marketId", secdef.get("market", secdef.get("exchange", "")))
                        )
                    if detected:
                        cached_exchanges[symbol] = detected
                        resolved_exchanges[symbol] = detected
                        exchange_cache_changed = True
                    elif connected:
                        exchange_retry_after[symbol] = time.time() + 300.0
                    if not detected:
                        manual = normalize_exchange(settings.symbol_exchanges.get(symbol))
                        if manual:
                            resolved_exchanges[symbol] = manual
                symbol_phases = {
                    symbol: market_phase(
                        now_market,
                        working_dates=working_dates if connected else None,
                        holidays=settings.trading_holidays,
                        exchange=resolved_exchanges.get(symbol, ""),
                    )[0]
                    for symbol in symbols
                }
                active = [value for value in symbol_phases.values() if value in {"ATO", "OPEN", "ATC"}]
                phase = active[0] if active else next(iter(symbol_phases.values()), "CLOSED")
            if latest_status and phase == "CALENDAR_UNKNOWN" and latest_status.market_status != phase:
                logger.warning("Trading calendar unavailable; no usable DNSE/cached calendar. New submissions remain blocked.")
            elif latest_status and working_dates and latest_status.market_status in {"CALENDAR_LOADING", "CALENDAR_UNKNOWN"}:
                logger.info("Trading calendar ready (%d dates).", len(working_dates))
            if latest_status and latest_status.market_status == "CALENDAR_LOADING":
                # Publish the calendar result before downloading symbol histories.
                # Missing calendars stay fail-closed; cached startup quotes remain stale.
                publish_status(
                    RuntimeStatus(
                        heartbeat_at=time.time(), daemon_status="RUNNING",
                        market_status=phase, bot_enabled=bool(runtime.bot_enabled),
                        active_symbols=symbols, ticks=ticks, api_health=market.health(),
                        working_dates=working_dates,
                        symbol_exchanges={symbol: resolved_exchanges.get(symbol, "") for symbol in symbols},
                        symbol_phases=symbol_phases,
                    )
                )
            if connected:
                configure_polling = getattr(client, "set_account_poll_interval", None)
                if callable(configure_polling):
                    configure_polling(account_poll_interval(
                        phase, queue.list_all(), symbol_phases=symbol_phases,
                    ))
                live_phase = any(value in {"ATO", "OPEN", "ATC"} for value in symbol_phases.values())
                now_ts = time.time()
                cache_changed = exchange_cache_changed
                vnindex_refresh_after = 30.0 if live_phase else 1800.0
                if not vnindex_bars or now_ts - last_vnindex_refresh >= vnindex_refresh_after:
                    vnindex_bars = market.get_daily_bars("VNINDEX", count=260, exchange="HOSE")
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
                        bars_by_symbol[symbol] = market.get_daily_bars(
                            symbol, count=260, exchange=cached_exchanges.get(symbol, ""),
                        )
                    last_symbol_history_refresh = now_ts
                    cache_changed = True
                if cache_changed:
                    market_cache.write({
                        "updated_at": now_ts,
                        "working_dates": cached_working_dates,
                        "exchanges": cached_exchanges,
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
                override_enabled = bool(settings.market_phase_override_enabled)
                effective_market_state = (
                    settings.market_phase_override
                    if override_enabled else confirmed_market_state
                )
                effective_exposure = (
                    settings.market_phase_override_exposure_pct / 100.0
                    if override_enabled
                    else rule.params.exposure.get(effective_market_state, 0.0)
                )
                active_mode = "PAPER" if runtime.paper_mode else "REAL"
                decisions_by_mode = {"PAPER": {}, "REAL": {}}
                management_modes = {cycle.execution_mode for cycle in trades.list_cycles() if cycle.status == "OPEN"}
                for decision_mode in (active_mode, "REAL" if active_mode == "PAPER" else "PAPER"):
                    if decision_mode != active_mode and decision_mode not in management_modes:
                        continue
                    decisions = decisions_by_mode[decision_mode]
                    try:
                        if decision_mode == "PAPER":
                            balance = paper.get_balance()
                            positions = paper.get_positions()
                        else:
                            balance = client.get_balance() or {}
                            positions = client.get_positions()
                    except Exception as exc:
                        cycle_error = f"{decision_mode} account snapshot unavailable: {exc}"
                        cycle_error_context = {
                            "execution_mode": decision_mode, "stage": "ACCOUNT_SNAPSHOT",
                            "exception_type": type(exc).__name__,
                        }
                        snapshot_errors = ((client.api_health() or {}).get("snapshot_errors") or {}) if decision_mode == "REAL" and hasattr(client, "api_health") else {}
                        snapshot_error = next((
                            error for error in snapshot_errors.values()
                            if error.get("error") == str(exc)
                        ), {})
                        if snapshot_error:
                            cycle_error_context["snapshot_error"] = snapshot_error
                        signature = (type(exc).__name__, str(exc))
                        if last_account_error_logs.get(decision_mode) != signature:
                            logger.warning("Account snapshot %s failed: %s", decision_mode, exc,
                                           exc_info=not bool(snapshot_error))
                            last_account_error_logs[decision_mode] = signature
                        continue
                    if last_account_error_logs.pop(decision_mode, None) is not None:
                        logger.info("Account snapshot %s recovered", decision_mode)
                    cycle_decision_time = datetime.now(VN_TZ)
                    for symbol in symbols:
                        cycle_stage = "MARKET_DATA"
                        try:
                            symbol_phase = symbol_phases.get(symbol, "UNKNOWN_EXCHANGE")
                            symbol_exchange = resolved_exchanges.get(symbol, "")
                            symbol_live = symbol_phase in {"ATO", "OPEN", "ATC"}
                            tick = None
                            if symbol_live:
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
                                quote_health.pause(symbol)
                                tick = ticks.get(symbol)
                                if tick:
                                    tick = {**tick, "frozen": True}
                                else:
                                    tick = market.frozen_tick_from_bars(symbol, bars_by_symbol.get(symbol, []))
                            if symbol_live:
                                quote_details = quote_health.observe(symbol, tick)
                                if not quote_details["valid"]:
                                    rejected = tick or ticks.get(symbol)
                                    if rejected:
                                        ticks[symbol] = {
                                            **rejected, "stale": True,
                                            "quote_issue": quote_details["reason"],
                                        }
                                    rule_state.save_buy_confirmation(symbol, decision_mode, {})
                                    tick = None
                            elif tick and str(tick.get("symbol", symbol)).upper() != symbol:
                                tick = None
                            if not tick:
                                decisions.pop(symbol, None)
                            if tick:
                                if settings.priority_capital_enabled or settings.priority_symbols:
                                    tick = tick_with_price_bound(tick, client.get_secdef(symbol))
                                ticks[symbol] = tick
                                if symbol_live:
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
                                context["market_phase"] = symbol_phase
                                context["max_observation_gap_seconds"] = MAX_DECISION_AGE
                                context["exchange"] = symbol_exchange
                                context["bars"] = bars_by_symbol.get(symbol, [])
                                context["vnindex_bars"] = vnindex_bars
                                context["signal_mode"] = settings.signal_mode
                                context["previous_market_state"] = rule_state.confirmed_market_state()
                                context["confirmed_market_state"] = effective_market_state
                                context["market_confirmation"] = market_confirmation
                                context["effective_exposure"] = effective_exposure
                                context["entry_allowed"] = symbol in entry_symbols
                                context["priority_entry"] = symbol in priority_symbol_set
                                context["market_override"] = {
                                    "enabled": override_enabled,
                                    "state": settings.market_phase_override,
                                    "exposure": settings.market_phase_override_exposure_pct / 100.0,
                                    "auto_state": confirmed_market_state,
                                }
                                candle_key = str((bars[-1] if bars else {}).get("time", "") or "")
                                cycle_stage = "INDICATORS"
                                current_indicators: dict = {}
                                if settings.signal_mode == "REALTIME" and bars:
                                    stream = decision_mode
                                    interval = settings.realtime_indicator_interval
                                    context["indicator_interval"] = interval
                                    if interval == "TICK":
                                        current_indicators = indicator_snapshot_at_close(
                                            bars, float(bars[-1].get("close", 0.0) or 0.0), rule.params,
                                        )
                                        context["previous_indicators"] = rule_state.observe_indicators(
                                            symbol, stream, candle_key, current_indicators,
                                        )
                                    else:
                                        observed_at = cycle_decision_time
                                        closed_bars = [
                                            row for row in bars
                                            if isinstance(row, dict) and bool(row.get("closed", True))
                                        ]
                                        baseline = indicator_snapshot_at_close(
                                            closed_bars,
                                            float((closed_bars[-1] if closed_bars else {}).get("close", 0.0) or 0.0),
                                            rule.params,
                                        )
                                        observation = rule_state.observe_indicator_bucket(
                                            symbol,
                                            stream,
                                            observed_at.date().isoformat(),
                                            interval,
                                            realtime_indicator_bucket(observed_at, interval),
                                            float(bars[-1].get("close", 0.0) or 0.0),
                                            baseline,
                                            lambda frozen_close, source=bars: indicator_snapshot_at_close(
                                                source, frozen_close, rule.params,
                                            ),
                                        )
                                        current_indicators = dict(observation.get("current") or baseline)
                                        context["previous_indicators"] = dict(
                                            observation.get("previous") or current_indicators
                                        )
                                        accepted_bucket = int(observation.get("bucket", 0) or 0)
                                        candle_key = f"{candle_key}|{interval}|{accepted_bucket or 'INIT'}"
                                    context["indicator_snapshot"] = current_indicators
                                exposure = effective_exposure
                                cycle_stage = "PORTFOLIO"
                                portfolio = portfolio_builder.build(
                                    symbol,
                                    execution_mode=decision_mode,
                                    balance=balance,
                                    positions=positions,
                                    tick=portfolio_tick,
                                    exposure=exposure,
                                    max_positions=rule.params.max_positions,
                                    priority_symbols=settings.priority_symbols,
                                    priority_capital_enabled=settings.priority_capital_enabled,
                                    priority_total_capital=settings.priority_total_capital,
                                    priority_allocations=settings.priority_allocations,
                                    no_compound_enabled=rule.params.no_compound_enabled,
                                    loss_lock_count=rule.params.loss_lock_count,
                                    loss_lock_hours=rule.params.loss_lock_hours,
                                    loss_lock_mode=rule.params.loss_lock_mode,
                                    corporate_actions=settings.corporate_actions,
                                    working_dates=working_dates,
                                    normal_t2_reset_enabled=bool(
                                        rule.params.normal_dynamic_enabled
                                        and rule.params.normal_t2_reset_enabled
                                        and rule.params.normal_policy == "AUTO"
                                    ),
                                    normal_arm_pct=rule.params.normal_arm_pct,
                                )
                                cycle_stage = "RULE_EVALUATION"
                                if not symbol_exchange:
                                    decision = StrategyDecision(
                                        "WAIT", symbol, "UNKNOWN_EXCHANGE",
                                        market_state=effective_market_state,
                                        details={"indicators": current_indicators if settings.signal_mode == "REALTIME" and bars else {}},
                                    )
                                else:
                                    decision = rule.evaluate(context, portfolio)
                                completed_daily = [
                                    row for row in bars
                                    if isinstance(row, dict) and bool(row.get("closed", True))
                                ]
                                atr14_daily_pct = average_true_range_pct(completed_daily)
                                decision.details["atr14_daily_pct"] = atr14_daily_pct
                                decision.details["atr14_daily_asof"] = (
                                    (completed_daily[-1] if completed_daily else {}).get("time", "")
                                )
                                decision.details["dynamic_start_pct"] = (
                                    atr14_daily_pct * rule.params.normal_atr_activation_multiplier
                                    if rule.params.normal_atr_activation_enabled else 0.0
                                )
                                decision.details["dynamic_trail_pct"] = (
                                    atr14_daily_pct * rule.params.normal_atr_multiplier
                                    if rule.params.normal_atr_trail_enabled else 0.0
                                )
                                decision.details["updated_at"] = cycle_decision_time.isoformat()
                                decision.details["execution_mode"] = decision_mode
                                cycle_stage = "RULE_STATE"
                                trade_id = str(portfolio.get("trade_id", "") or "")
                                protect_state = str(
                                    decision.details.get("normal_state", "") or ""
                                ).upper()
                                if trade_id and protect_state in {"DYN", "ARM", "ALERT", "REARM"}:
                                    protect_state_row = rule_state.update_protect_metrics(
                                        symbol,
                                        trade_id,
                                        trigger_price=float(
                                            decision.details.get("normal_trigger_price", 0.0) or 0.0
                                        ),
                                        atr_pct=float(
                                            decision.details.get("normal_atr_pct", 0.0) or 0.0
                                        ),
                                        atr_multiplier=float(
                                            decision.details.get("normal_atr_multiplier", 0.0) or 0.0
                                        ),
                                        atr_activation_multiplier=float(
                                            decision.details.get(
                                                "normal_atr_activation_multiplier", 0.0,
                                            ) or 0.0
                                        ),
                                        retention_pct=float(
                                            decision.details.get("normal_retention_pct", 0.0) or 0.0
                                        ),
                                        retention_until_pct=float(
                                            decision.details.get(
                                                "normal_retention_until_pct", 0.0,
                                            ) or 0.0
                                        ),
                                    )
                                    if protect_state_row:
                                        decision.details["normal_trigger_price"] = float(
                                            protect_state_row.get("normal_trigger_price", 0.0) or 0.0
                                        )
                                if trade_id and (
                                    decision.reason == "NORMAL_ARMED"
                                    or bool(decision.details.get("normal_should_arm"))
                                ):
                                    rule_state.arm_normal(symbol, trade_id)
                                if trade_id and decision.reason == "PROTECT_ALERT":
                                    alert_state = rule_state.mark_protection_alert(
                                        symbol,
                                        trade_id,
                                        occurrence=str(decision.details.get("protect_occurrence", "") or ""),
                                        trigger_peak_pct=float(
                                            decision.details.get("normal_trigger_peak_pct", 0.0) or 0.0
                                        ),
                                        rearm_mfe_pct=float(
                                            decision.details.get("normal_rearm_after_pct", 0.0) or 0.0
                                        ),
                                    )
                                    decision.details["normal_event_count"] = int(
                                        alert_state.get("normal_alert_count", 0) or 0
                                    )
                                    logger.info(
                                        "PROTECT ALERT symbol=%s trade=%s price=%.4f mfe=%.4f peak=%.4f "
                                        "effective_trail=%.4f atr=%.4f start_multiplier=%.4f "
                                        "trail_multiplier=%.4f "
                                        "protect=%.4f sell=%.2f hypothetical_qty=%d "
                                        "occurrence=%s",
                                        symbol,
                                        trade_id,
                                        float(decision.details.get("current_price", 0.0) or 0.0),
                                        float(decision.details.get("normal_mfe_pct", 0.0) or 0.0),
                                        float(decision.details.get("normal_peak_price", 0.0) or 0.0),
                                        float(decision.details.get("normal_effective_trail_pct", 0.0) or 0.0),
                                        float(decision.details.get("normal_atr_pct", 0.0) or 0.0),
                                        float(
                                            decision.details.get(
                                                "normal_atr_activation_multiplier", 0.0,
                                            ) or 0.0
                                        ),
                                        float(decision.details.get("normal_atr_multiplier", 0.0) or 0.0),
                                        float(decision.details.get("normal_trigger_price", 0.0) or 0.0),
                                        float(decision.details.get("sell_share_pct", 0.0) or 0.0),
                                        sell_quantity_for_fraction(
                                            int(portfolio.get("position_quantity", 0) or 0),
                                            float(decision.details.get("sell_share_pct", 0.0) or 0.0) / 100.0,
                                        ),
                                        str(decision.details.get("protect_occurrence", "") or ""),
                                    )
                                stream = decision_mode
                                next_filters, decision = apply_buy_filters(
                                    rule, decision, context, portfolio,
                                    rule_state.buy_confirmation(symbol, stream),
                                    observed_at=cycle_decision_time, exchange=symbol_exchange,
                                    working_dates=working_dates, holidays=settings.trading_holidays,
                                )
                                rule_state.save_buy_confirmation(symbol, stream, next_filters)
                                first_seen = rule_state.observe_signal_time(
                                    symbol, stream, decision.signal, candle_key,
                                    cycle_decision_time.isoformat(),
                                )
                                if first_seen:
                                    decision.details.setdefault("signal_time", first_seen)
                                    decision.details["signal_cycle"] = (
                                        f"{candle_key}|{first_seen}"
                                    )
                                decision.details["candle_key"] = candle_key
                                decision.details["order_budget"] = portfolio.get("order_budget", 0.0)
                                decision.details["trade_id"] = portfolio.get("trade_id", "")
                                decision.details["position_quantity"] = portfolio.get("position_quantity", 0)
                                decisions[symbol] = decision.to_dict()
                        except Exception as exc:
                            decisions.pop(symbol, None)
                            cycle_error = str(exc)
                            cycle_error_context = {
                                "symbol": symbol, "execution_mode": decision_mode,
                                "stage": cycle_stage, "exception_type": type(exc).__name__,
                            }
                            logger.warning(
                                "Market update %s (%s) failed at %s: %s",
                                symbol, decision_mode, cycle_stage, exc, exc_info=True,
                            )
                decisions = decisions_by_mode[active_mode]
            publish_status(
                RuntimeStatus(
                    heartbeat_at=time.time(),
                    daemon_status="RUNNING",
                    market_status=phase if connected else "NOT_CONFIGURED",
                    bot_enabled=bool(runtime.bot_enabled),
                    active_symbols=symbols,
                    ticks=ticks,
                    decisions=decisions,
                    decisions_by_mode=decisions_by_mode,
                    api_health=market.health(),
                    error=cycle_error,
                    cycle_error_context=cycle_error_context,
                    working_dates=working_dates,
                    symbol_exchanges={symbol: resolved_exchanges.get(symbol, "") for symbol in symbols},
                    symbol_phases=symbol_phases,
                )
            )
            elapsed = time.time() - started
            time.sleep(max(0.05, config.DAEMON_LOOP_SECONDS - elapsed))
    except Exception as exc:
        exit_code = 1
        error = str(exc)
        logger.exception("%s daemon stopped by an unexpected error", APP_NAME)
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
        logger.info("%s daemon stopped.", APP_NAME)
        worker_lease.close()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} static-rule daemon")
    parser.add_argument("--account", default=config.active_account_id())
    args = parser.parse_args(argv)
    return run(args.account)


if __name__ == "__main__":
    raise SystemExit(main())
