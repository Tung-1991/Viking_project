from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any
import uuid

from .. import config
from ..exit_modes import normalize_exit_modes


VALID_PHASES = {"UPTREND", "DOWNTREND", "ACCUMULATION", "DISTRIBUTION"}

# A scenario may also opt out of Phase 1 entirely and pin one exposure itself.
NO_PHASE = "NONE"
SCENARIO_PHASES = VALID_PHASES | {NO_PHASE}
SIMULATION_MODES = {"DAILY", "REPLAY", "AUTO_HYBRID"}


def _iso_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date().isoformat()


@dataclass(slots=True)
class BacktestConfig:
    symbols: list[str]
    start_date: str
    end_date: str
    initial_capital: float = 1_000_000_000.0
    auto_market_phase: bool = False
    fixed_market_phase: str = "ACCUMULATION"
    fixed_exposure_pct: float = 60.0
    # False means Phase 1 was switched off for this run: every state shares one
    # exposure and the state label above is only a placeholder the rule accepts.
    use_market_phase: bool = True
    loss_lock_enabled: bool = False
    loss_lock_hours: int = 24
    priority_symbols: list[str] = field(default_factory=list)
    priority_capital_enabled: bool = False
    priority_total_capital: float = 0.0
    priority_allocations: dict[str, dict[str, float]] = field(default_factory=dict)
    whipsaw_enabled: bool = False
    em_modes: list[str] = field(default_factory=lambda: ["NORMAL", "IND_EXIT"])
    sell_wait_policy: str = "RECHECK"
    # ATO khớp ở giá mở cửa · CONTINUOUS khớp sau 9h15 như bot thật đang chạy
    fill_session: str = "ATO"
    # Costs travel with the run so a saved result can always be reproduced,
    # instead of silently following whatever the module constant is today.
    buy_fee_rate: float = config.DEFAULT_BUY_FEE_PCT / 100.0
    sell_fee_rate: float = config.DEFAULT_SELL_FEE_PCT / 100.0
    sell_tax_rate: float = config.DEFAULT_SELL_TAX_PCT / 100.0
    rule_parameters: dict[str, Any] = field(default_factory=dict)
    warmup_sessions: int = 250
    execution_resolution: str = "AUTO"
    # DAILY preserves historical behaviour. REPLAY is strict; AUTO_HYBRID
    # falls back to the daily engine for symbol-days without complete imports.
    simulation_mode: str = "DAILY"
    export_signals: bool = False
    run_name: str = ""
    # Persisted with the run so exchange-specific execution is reproducible.
    symbol_exchanges: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbols = list(dict.fromkeys(str(x).strip().upper() for x in self.symbols if str(x).strip()))
        if not self.symbols:
            raise ValueError("Backtest cần ít nhất một mã.")
        self.start_date = _iso_date(self.start_date)
        self.end_date = _iso_date(self.end_date)
        if self.end_date < self.start_date:
            raise ValueError("Ngày kết thúc phải từ ngày bắt đầu trở đi.")
        self.initial_capital = max(0.0, float(self.initial_capital or 0.0))
        if self.initial_capital <= 0:
            raise ValueError("Vốn đầu phải lớn hơn 0.")
        self.fixed_exposure_pct = min(100.0, max(0.0, float(self.fixed_exposure_pct or 0.0)))
        self.fixed_market_phase = str(self.fixed_market_phase or "ACCUMULATION").upper()
        if self.fixed_market_phase not in VALID_PHASES:
            raise ValueError("Phase cố định không hợp lệ.")
        self.loss_lock_hours = max(0, int(self.loss_lock_hours or 0))
        self.priority_symbols = list(dict.fromkeys(str(x).strip().upper() for x in self.priority_symbols if str(x).strip()))
        from ..rules.business import StaticRuleParameters
        if len(self.priority_symbols) > StaticRuleParameters.from_dict(self.rule_parameters).max_positions:
            raise ValueError("Số mã Priority vượt số vị thế BOT tối đa.")
        self.priority_allocations = config.normalize_priority_allocations(self.priority_allocations, self.priority_symbols)
        self.priority_total_capital = config.finite_nonnegative(self.priority_total_capital)
        if self.priority_capital_enabled:
            config.validate_priority_capital(self.priority_total_capital, self.priority_symbols, self.priority_allocations)
            # Replay currently has one entry/settlement lot per symbol. Refuse
            # to silently report results for a policy it does not simulate.
            if any(row["max_orders"] > 1 for row in self.priority_allocations.values()):
                raise ValueError("Backtest chưa mô phỏng MAX LỆNH > 1; đặt 1 khi backtest. LIVE/PAPER hỗ trợ mua thêm.")
        self.whipsaw_enabled = bool(self.whipsaw_enabled)
        self.em_modes = normalize_exit_modes(self.em_modes)
        self.sell_wait_policy = str(self.sell_wait_policy or "RECHECK").upper()
        if self.sell_wait_policy not in {"RECHECK", "KEEP"}:
            self.sell_wait_policy = "RECHECK"
        self.fill_session = str(self.fill_session or "ATO").upper()
        if self.fill_session not in {"ATO", "CONTINUOUS"}:
            self.fill_session = "ATO"
        self.rule_parameters = dict(self.rule_parameters or {})
        self.warmup_sessions = max(220, int(self.warmup_sessions or 250))
        self.execution_resolution = str(self.execution_resolution or "AUTO").upper()
        if self.execution_resolution not in {"AUTO", "1", "2", "3", "5", "15", "30", "1H", "1D"}:
            self.execution_resolution = "AUTO"
        self.simulation_mode = str(self.simulation_mode or "DAILY").strip().upper().replace(" ", "_")
        if self.simulation_mode not in SIMULATION_MODES:
            self.simulation_mode = "DAILY"
        self.export_signals = bool(self.export_signals)
        aliases = {"HOSE": "HOSE", "HSX": "HOSE", "STO": "HOSE",
                   "HNX": "HNX", "STX": "HNX", "UPCOM": "UPCOM", "UPX": "UPCOM"}
        self.symbol_exchanges = {
            str(symbol).strip().upper(): aliases[str(exchange).strip().upper()]
            for symbol, exchange in (self.symbol_exchanges or {}).items()
            if str(symbol).strip() and str(exchange).strip().upper() in aliases
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BacktestConfig":
        raw = dict(raw or {})
        if "em_modes" not in raw and "bot_em_modes" in raw:
            raw["em_modes"] = raw["bot_em_modes"]
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in raw.items() if key in allowed})


@dataclass(slots=True)
class BacktestSettings:
    """Backtest's own configuration, deliberately separate from the live bot.

    Changing a parameter here must never move the account the bot trades with;
    the popup's SYNC button is the only path from live settings into this file.
    """

    symbols: list[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 1_000_000_000.0
    auto_market_phase: bool = False
    fixed_market_phase: str = "ACCUMULATION"
    fixed_exposure_pct: float = 60.0
    # Mirrors the live bot, which ships with both guards on.
    loss_lock_enabled: bool = True
    loss_lock_hours: int = 24
    priority_symbols: list[str] = field(default_factory=list)
    priority_capital_enabled: bool = False
    priority_total_capital: float = 0.0
    priority_allocations: dict[str, dict[str, float]] = field(default_factory=dict)
    whipsaw_enabled: bool = True
    em_modes: list[str] = field(default_factory=lambda: ["NORMAL", "IND_EXIT"])
    sell_wait_policy: str = "RECHECK"
    fill_session: str = "ATO"
    buy_fee_pct: float = config.DEFAULT_BUY_FEE_PCT
    sell_fee_pct: float = config.DEFAULT_SELL_FEE_PCT
    sell_tax_pct: float = config.DEFAULT_SELL_TAX_PCT
    rule_parameters: dict[str, Any] = field(default_factory=config.default_rule_parameters)
    # New Mode 2 windows prefer imported intraday data. BacktestConfig itself
    # still defaults to DAILY so old serialized runs remain reproducible.
    simulation_mode: str = "AUTO_HYBRID"

    def __post_init__(self) -> None:
        self.symbols = list(dict.fromkeys(str(x).strip().upper() for x in self.symbols if str(x).strip()))
        self.initial_capital = max(0.0, float(self.initial_capital or 0.0)) or 1_000_000_000.0
        self.fixed_market_phase = str(self.fixed_market_phase or "ACCUMULATION").upper()
        if self.fixed_market_phase not in VALID_PHASES:
            self.fixed_market_phase = "ACCUMULATION"
        self.fixed_exposure_pct = min(100.0, max(0.0, float(self.fixed_exposure_pct or 0.0)))
        self.loss_lock_hours = max(0, int(self.loss_lock_hours or 0))
        self.em_modes = normalize_exit_modes(self.em_modes)
        self.sell_wait_policy = "KEEP" if str(self.sell_wait_policy).upper() == "KEEP" else "RECHECK"
        self.fill_session = "CONTINUOUS" if str(self.fill_session).upper() == "CONTINUOUS" else "ATO"
        self.simulation_mode = str(self.simulation_mode or "AUTO_HYBRID").strip().upper().replace(" ", "_")
        if self.simulation_mode not in SIMULATION_MODES:
            self.simulation_mode = "AUTO_HYBRID"
        for name in ("buy_fee_pct", "sell_fee_pct", "sell_tax_pct"):
            setattr(self, name, min(5.0, max(0.0, float(getattr(self, name) or 0.0))))
        # A settings file written before a parameter existed must still hand back
        # a complete set, otherwise every reader has to guess its own fallback.
        from ..rules.business import StaticRuleParameters

        self.rule_parameters = StaticRuleParameters.from_dict(
            config.merge_rule_parameters(self.rule_parameters)
        ).to_dict()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BacktestSettings":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in dict(raw or {}).items() if key in allowed})


@dataclass(slots=True)
class BacktestScenario:
    """One symbol, one window, one hand-labelled market state.

    Indicators and the exposure table are deliberately absent: they belong to
    the shared settings so a scenario cannot test a rule the bot cannot run.
    """

    name: str
    symbols: list[str]
    start_date: str
    end_date: str
    market_phase: str
    exposure_pct: float = 60.0
    # Each row is a self-contained test case: it carries its own slot count and
    # its own protection switches, so two rows can compare configurations over
    # the same window without touching the shared settings.
    max_positions: int = 5
    em_modes: list[str] = field(default_factory=lambda: ["NORMAL", "IND_EXIT"])
    whipsaw_enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        self.id = str(self.id or uuid.uuid4().hex)
        self.name = str(self.name or "Kịch bản").strip()
        self.symbols = list(dict.fromkeys(str(x).strip().upper() for x in self.symbols if str(x).strip()))
        if not self.symbols:
            raise ValueError("Kịch bản cần ít nhất một mã.")
        self.start_date = _iso_date(self.start_date)
        self.end_date = _iso_date(self.end_date)
        self.market_phase = str(self.market_phase or "").strip().upper()
        if self.market_phase not in SCENARIO_PHASES:
            raise ValueError("Kịch bản phải chọn một trạng thái thị trường.")
        # exposure_pct is only read when Phase 1 is switched off for this row.
        self.exposure_pct = min(100.0, max(0.0, float(self.exposure_pct or 0.0)))
        self.max_positions = max(1, int(self.max_positions or 1))
        self.em_modes = normalize_exit_modes(self.em_modes)
        self.whipsaw_enabled = bool(self.whipsaw_enabled)

    @property
    def uses_market_phase(self) -> bool:
        return self.market_phase != NO_PHASE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BacktestScenario":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in dict(raw or {}).items() if key in allowed})


@dataclass(slots=True)
class BacktestEvent:
    date: str
    trade_id: str
    symbol: str
    side: str
    event: str
    quantity: int
    price: float
    gross: float
    fee: float
    tax: float
    cash_after: float
    market_state: str = ""
    pnl: float = 0.0
    signal_date: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    cycle_id: str = ""
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    rsi_previous: float = 0.0
    rsi: float = 0.0
    profit_pct: float = 0.0
    peak_profit_pct: float = 0.0
    equity_after: float = 0.0
    signal_time: str = ""
    decision_time: str = ""
    fill_time: str = ""
    simulation_mode: str = "DAILY"
    source_resolution: str = "1D"
    data_quality: str = "FULL"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BacktestTrade:
    trade_id: str
    symbol: str
    opened_date: str
    closed_date: str = ""
    entry_quantity: int = 0
    remaining_quantity: int = 0
    avg_entry_price: float = 0.0
    avg_exit_price: float = 0.0
    fees: float = 0.0
    tax: float = 0.0
    net_pnl: float = 0.0
    outcome: str = "OPEN"
    exit_events: list[str] = field(default_factory=list)
    entry_market_state: str = ""
    entry_exposure_pct: float = 0.0
    entry_reason: str = ""
    entry_signal_date: str = ""
    entry_rule: str = ""
    # A re-entry keeps its cycle and only bumps the attempt: HSG-01, HSG-01.1.
    # The cycle ends on the first WIN, which is also when SL returns to -3%.
    cycle_id: str = ""
    sessions_held: int = 0
    entry_value: float = 0.0
    sl_pct: float = 0.0
    peak_profit_pct: float = 0.0
    mae_profit_pct: float = 0.0
    peak_at: str = ""
    entry_to_peak_hours: float = 0.0
    peak_to_exit_hours: float = 0.0
    max_giveback_pct: float = 0.0
    settlement_release_at: str = ""
    mfe_before_settlement_pct: float = 0.0
    mfe_after_settlement_pct: float | None = None
    mfe_peak_phase: str = ""
    profit_path: list[dict[str, Any]] = field(default_factory=list)
    normal_policy: str = "AUTO"
    normal_arm_time: str = ""
    normal_arm_price: float = 0.0
    mfe_after_arm_pct: float = 0.0
    mfe_extra_pct: float = 0.0
    exit_profit_pct: float = 0.0
    profit_giveback_pct: float = 0.0
    exit_mode: str = ""
    pnl_pct: float = 0.0
    equity_after: float = 0.0
    entry_ema_fast: float = 0.0
    entry_ema_slow: float = 0.0
    entry_rsi: float = 0.0
    exit_ema_fast: float = 0.0
    exit_ema_slow: float = 0.0
    exit_rsi: float = 0.0
    # Every sell of this round as {event, quantity, price}, so the report can
    # spell out "PROTECT 1500@21.24 + E 3000@21.09" instead of one blended
    # average price that never traded.
    exit_fills: list[dict[str, Any]] = field(default_factory=list)

    @property
    def stop_price(self) -> float:
        return self.avg_entry_price * (1.0 + self.sl_pct / 100.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BacktestResult:
    run_id: str
    config: BacktestConfig
    started_at: str
    completed_at: str
    initial_capital: float
    final_equity: float
    cash: float
    market_value: float
    net_pnl: float
    return_pct: float
    total_fees: float
    total_tax: float
    max_drawdown_pct: float
    buy_count: int
    sell_count: int
    closed_trades: int
    win_count: int
    loss_count: int
    win_rate_pct: float
    events: list[BacktestEvent] = field(default_factory=list)
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    phase_history: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["config"] = self.config.to_dict()
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BacktestResult":
        values = dict(raw or {})
        values["config"] = BacktestConfig.from_dict(values.get("config") or {})
        values["events"] = [BacktestEvent(**row) for row in values.get("events", [])]
        values["trades"] = [BacktestTrade(**row) for row in values.get("trades", [])]
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in allowed})
