from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
import uuid
from typing import Any, Literal

from .config import ORDER_TTL_SECONDS
from .exit_modes import normalize_exit_modes


Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LO", "ATO", "ATC"]
ExecutionMode = Literal["REAL", "PAPER"]
DecisionAction = Literal["WAIT", "BUY", "SELL"]
DecisionScope = Literal["ENTRY", "POSITION_MANAGEMENT"]
OrderAction = Literal["OPEN", "CLOSE"]
TradeStatus = Literal["OPEN", "CLOSED"]


@dataclass(slots=True)
class OrderIntent:
    id: str
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType
    limit_price: float = 0.0
    execution_mode: ExecutionMode = "PAPER"
    source: str = "MANUAL"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: str = "PENDING"
    result: str = ""
    broker_order_id: str = ""
    request_tag: str = ""
    claimed_at: float = 0.0
    trade_id: str = ""
    action: OrderAction = "OPEN"
    reason: str = ""
    wait_for_trigger: bool = False
    allow_ato: bool = False
    allow_atc: bool = False
    filled_quantity: int = 0
    remaining_quantity: int = -1
    eligible_session_seen: bool = False
    defer_expiry_until_eligible: bool = False
    attempt: int = 1
    working_quantity: int = 0
    broker_filled_quantity: int = 0
    broker_fee_logged: float = 0.0
    broker_tax_logged: float = 0.0
    broker_notional_logged: float = 0.0
    handed_off_at: float = 0.0
    loan_package_id: str = ""
    deal_id: str = ""
    broker_order_ids: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    em_modes: list[str] = field(default_factory=list)
    sl_enabled: bool = True
    sl_mode: str = "DEFAULT"
    sl_value: float = 0.0
    tp_mode: str = "NONE"
    tp_value: float = 0.0
    sell_wait_policy: str = "RECHECK"
    settlement_waited: bool = False
    signal: str = ""
    candle_key: str = ""
    entry_market_state: str = "UNKNOWN"
    entry_exposure: float = 0.0
    entry_budget: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    buy_window_start: str = ""
    buy_window_end: str = ""
    buy_window_date: str = ""

    def __post_init__(self) -> None:
        self.symbol = str(self.symbol or "").strip().upper()
        self.side = "SELL" if str(self.side).upper() == "SELL" else "BUY"
        self.order_type = str(self.order_type or "MARKET").upper()  # type: ignore[assignment]
        if self.order_type not in {"MARKET", "LO", "ATO", "ATC"}:
            raise ValueError(f"Unsupported order type: {self.order_type}")
        self.execution_mode = "REAL" if str(self.execution_mode).upper() == "REAL" else "PAPER"
        self.source = str(self.source or "MANUAL").strip().upper()
        self.quantity = int(self.quantity or 0)
        self.filled_quantity = max(0, int(self.filled_quantity or 0))
        self.remaining_quantity = max(0, self.quantity - self.filled_quantity)
        self.limit_price = float(self.limit_price or 0.0)
        self.created_at = float(self.created_at or time.time())
        self.expires_at = float(self.expires_at or (self.created_at + ORDER_TTL_SECONDS))
        if not self.id:
            self.id = uuid.uuid4().hex
        self.action = "CLOSE" if str(self.action).upper() == "CLOSE" else "OPEN"
        self.trade_id = str(self.trade_id or "")
        self.reason = str(self.reason or "")
        self.wait_for_trigger = bool(self.wait_for_trigger)
        self.allow_ato = bool(self.allow_ato)
        self.allow_atc = bool(self.allow_atc)
        self.eligible_session_seen = bool(self.eligible_session_seen)
        self.defer_expiry_until_eligible = bool(self.defer_expiry_until_eligible)
        self.attempt = max(1, int(self.attempt or 1))
        self.working_quantity = max(0, int(self.working_quantity or 0))
        self.broker_filled_quantity = max(0, int(self.broker_filled_quantity or 0))
        self.broker_fee_logged = max(0.0, float(self.broker_fee_logged or 0.0))
        self.broker_tax_logged = max(0.0, float(self.broker_tax_logged or 0.0))
        self.em_modes = normalize_exit_modes(self.em_modes)
        self.sl_enabled = bool(self.sl_enabled)
        self.sl_mode = str(self.sl_mode or "DEFAULT").strip().upper()
        if self.sl_mode not in {"DEFAULT", "PERCENT", "PRICE"}:
            self.sl_mode = "DEFAULT"
        self.sl_value = float(self.sl_value or 0.0)
        self.tp_mode = str(self.tp_mode or "NONE").strip().upper()
        if self.tp_mode not in {"NONE", "PERCENT", "PRICE"}:
            self.tp_mode = "NONE"
        self.tp_value = float(self.tp_value or 0.0)
        self.sell_wait_policy = str(self.sell_wait_policy or "RECHECK").strip().upper()
        if self.sell_wait_policy not in {"RECHECK", "KEEP"}:
            self.sell_wait_policy = "RECHECK"
        self.settlement_waited = bool(self.settlement_waited)
        self.signal = str(self.signal or "").strip().upper()
        self.candle_key = str(self.candle_key or "")
        self.entry_market_state = str(self.entry_market_state or "UNKNOWN").strip().upper()
        self.entry_exposure = max(0.0, float(self.entry_exposure or 0.0))
        self.entry_budget = max(0.0, float(self.entry_budget or 0.0))
        self.details = dict(self.details) if isinstance(self.details, dict) else {}

    @classmethod
    def create(
        cls,
        symbol: str,
        side: Side,
        quantity: int,
        order_type: OrderType,
        *,
        limit_price: float = 0.0,
        execution_mode: ExecutionMode = "PAPER",
        source: str = "MANUAL",
        trade_id: str = "",
        action: OrderAction = "OPEN",
        reason: str = "",
        wait_for_trigger: bool = False,
        allow_ato: bool = False,
        allow_atc: bool = False,
        defer_expiry_until_eligible: bool = False,
        em_modes: list[str] | None = None,
        sl_enabled: bool = True,
        sl_mode: str = "DEFAULT",
        sl_value: float = 0.0,
        tp_mode: str = "NONE",
        tp_value: float = 0.0,
        sell_wait_policy: str = "RECHECK",
        signal: str = "",
        candle_key: str = "",
        entry_market_state: str = "UNKNOWN",
        entry_exposure: float = 0.0,
        entry_budget: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> "OrderIntent":
        return cls(
            id=uuid.uuid4().hex,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            execution_mode=execution_mode,
            source=source,
            trade_id=trade_id,
            action=action,
            reason=reason,
            wait_for_trigger=wait_for_trigger,
            allow_ato=allow_ato,
            allow_atc=allow_atc,
            defer_expiry_until_eligible=defer_expiry_until_eligible,
            em_modes=list(em_modes or []),
            sl_enabled=sl_enabled,
            sl_mode=sl_mode,
            sl_value=sl_value,
            tp_mode=tp_mode,
            tp_value=tp_value,
            sell_wait_policy=sell_wait_policy,
            signal=signal,
            candle_key=candle_key,
            entry_market_state=entry_market_state,
            entry_exposure=entry_exposure,
            entry_budget=entry_budget,
            details=dict(details or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OrderIntent":
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class BrokerOrderResult:
    ok: bool
    status: str = ""
    order_id: str = ""
    message: str = ""
    error: str = ""
    status_code: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TradeCycle:
    id: str
    symbol: str
    execution_mode: ExecutionMode = "PAPER"
    source: str = "BOT"
    status: TradeStatus = "OPEN"
    entry_quantity: int = 0
    open_quantity: int = 0
    sold_quantity: int = 0
    avg_entry_price: float = 0.0
    avg_exit_price: float = 0.0
    net_pnl: float = 0.0
    fees_paid: float = 0.0
    is_reentry: bool = False
    opened_at: float = field(default_factory=time.time)
    closed_at: float = 0.0
    exit_events: list[str] = field(default_factory=list)
    em_modes: list[str] = field(default_factory=list)
    sl_enabled: bool = True
    sl_mode: str = "DEFAULT"
    sl_value: float = 0.0
    tp_mode: str = "NONE"
    tp_value: float = 0.0
    capital_principal: float = 0.0
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    external_progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    entry_market_state: str = "UNKNOWN"
    entry_exposure: float = 0.0
    entry_budget: float = 0.0
    loan_package_id: str = ""
    deal_id: str = ""

    def __post_init__(self) -> None:
        self.id = str(self.id or uuid.uuid4().hex)
        self.symbol = str(self.symbol or "").strip().upper()
        self.execution_mode = "REAL" if str(self.execution_mode).upper() == "REAL" else "PAPER"
        self.source = str(self.source or "BOT").upper()
        self.status = "CLOSED" if str(self.status).upper() == "CLOSED" else "OPEN"
        self.entry_quantity = max(0, int(self.entry_quantity or 0))
        self.open_quantity = max(0, int(self.open_quantity or 0))
        self.sold_quantity = max(0, int(self.sold_quantity or 0))
        self.avg_entry_price = max(0.0, float(self.avg_entry_price or 0.0))
        self.avg_exit_price = max(0.0, float(self.avg_exit_price or 0.0))
        self.net_pnl = float(self.net_pnl or 0.0)
        self.fees_paid = max(0.0, float(self.fees_paid or 0.0))
        self.exit_events = list(dict.fromkeys(str(value) for value in self.exit_events if str(value)))
        self.em_modes = normalize_exit_modes(self.em_modes)
        self.sl_enabled = bool(self.sl_enabled)
        self.sl_mode = str(self.sl_mode or "DEFAULT").strip().upper()
        if self.sl_mode not in {"DEFAULT", "PERCENT", "PRICE"}:
            self.sl_mode = "DEFAULT"
        self.sl_value = float(self.sl_value or 0.0)
        self.tp_mode = str(self.tp_mode or "NONE").strip().upper()
        if self.tp_mode not in {"NONE", "PERCENT", "PRICE"}:
            self.tp_mode = "NONE"
        self.tp_value = float(self.tp_value or 0.0)
        self.capital_principal = max(0.0, float(self.capital_principal or 0.0))
        self.buy_notional = max(0.0, float(self.buy_notional or 0.0))
        self.sell_notional = max(0.0, float(self.sell_notional or 0.0))
        if not self.buy_notional and self.entry_quantity:
            # Migrate the old ledger without erasing its recorded realized PnL.
            self.sell_notional = self.avg_exit_price * self.sold_quantity * 1000.0
            self.buy_notional = max(0.0, self.sell_notional + self.avg_entry_price * self.open_quantity * 1000.0 - self.net_pnl - self.fees_paid)
        self.entry_market_state = str(self.entry_market_state or "UNKNOWN").strip().upper()
        self.entry_exposure = max(0.0, float(self.entry_exposure or 0.0))
        self.entry_budget = max(0.0, float(self.entry_budget or 0.0))

    @property
    def outcome(self) -> str:
        if self.status != "CLOSED":
            return "OPEN"
        return "WIN" if self.net_pnl >= 0 else "LOSS"

    def record_buy_fill(self, quantity: int, price: float, fee: float = 0.0) -> None:
        quantity = max(0, int(quantity or 0))
        price = max(0.0, float(price or 0.0))
        fee = max(0.0, float(fee or 0.0))
        if quantity <= 0 or price <= 0:
            return
        previous_cost = self.avg_entry_price * self.open_quantity
        previous_quantity = self.open_quantity
        self.entry_quantity += quantity
        self.open_quantity += quantity
        self.avg_entry_price = (previous_cost + price * quantity) / (previous_quantity + quantity)
        self.status = "OPEN"
        self.closed_at = 0.0
        self.fees_paid += fee
        self.buy_notional += quantity * price * 1000.0
        self.refresh_pnl()
        self.capital_principal = max(
            self.capital_principal,
            self.avg_entry_price * self.open_quantity * 1000.0 + self.fees_paid,
        )

    def record_sell_fill(self, quantity: int, price: float, fee: float = 0.0, closed_at: float | None = None) -> int:
        quantity = min(max(0, int(quantity or 0)), self.open_quantity)
        price = max(0.0, float(price or 0.0))
        fee = max(0.0, float(fee or 0.0))
        if quantity <= 0 or price <= 0:
            return 0
        previous_exit_value = self.avg_exit_price * self.sold_quantity
        self.open_quantity -= quantity
        self.sold_quantity += quantity
        self.avg_exit_price = (previous_exit_value + price * quantity) / self.sold_quantity
        self.fees_paid += fee
        self.sell_notional += quantity * price * 1000.0
        self.refresh_pnl()
        if self.open_quantity == 0:
            self.status = "CLOSED"
            self.closed_at = float(closed_at or time.time())
        return quantity

    def refresh_pnl(self) -> None:
        # Cash-flow identity also works when broker snapshots arrive out of
        # execution order: sells - buys + remaining cost basis - fees.
        self.net_pnl = round(self.sell_notional - self.buy_notional + self.avg_entry_price * self.open_quantity * 1000.0 - self.fees_paid, 2)

    def sync_remaining_cost(self, price: float) -> None:
        if price > 0 and self.open_quantity > 0:
            self.avg_entry_price = float(price)
            self.refresh_pnl()

    def mark_exit_once(self, event: str) -> bool:
        event = str(event or "").strip().upper()
        if not event or event in self.exit_events:
            return False
        self.exit_events.append(event)
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TradeCycle":
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class StrategyDecision:
    action: DecisionAction
    symbol: str
    reason: str
    timestamp: float = field(default_factory=time.time)
    event: str = ""
    signal: str = ""
    market_state: str = "UNKNOWN"
    quantity_fraction: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    scope: DecisionScope = "ENTRY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StrategyDecision":
        raw = raw if isinstance(raw, dict) else {}
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class RuntimeConfig:
    watchlist: list[str]
    paper_mode: bool
    bot_enabled: bool = False
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "watchlist": list(dict.fromkeys(str(x).strip().upper() for x in self.watchlist if str(x).strip())),
            "paper_mode": bool(self.paper_mode),
            "bot_enabled": bool(self.bot_enabled),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "RuntimeConfig":
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            watchlist=list(raw.get("watchlist") or []),
            paper_mode=bool(raw.get("paper_mode", True)),
            bot_enabled=bool(raw.get("bot_enabled", False)),
            updated_at=float(raw.get("updated_at", 0.0) or 0.0),
        )


@dataclass(slots=True)
class RuntimeStatus:
    heartbeat_at: float
    daemon_status: str
    market_status: str
    bot_enabled: bool
    active_symbols: list[str]
    ticks: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions_by_mode: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    api_health: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    working_dates: list[str] = field(default_factory=list)
    symbol_exchanges: dict[str, str] = field(default_factory=dict)
    symbol_phases: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def empty(cls) -> "RuntimeStatus":
        return cls(time.time(), "STARTING", "OFFLINE", False, [])
