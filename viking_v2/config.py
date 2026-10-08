from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

from .exit_modes import normalize_exit_modes


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
ENV_PATH = PACKAGE_ROOT / ".env"
RUNTIME_ROOT = PACKAGE_ROOT / "runtime"
ACCOUNTS_ROOT = RUNTIME_ROOT / "accounts"

load_dotenv(ENV_PATH, encoding="utf-8-sig", override=False)


# Technical constants are deliberately not exposed in the UI.
ORDER_TTL_SECONDS = 24 * 60 * 60
HTTP_TIMEOUT_SECONDS = 15.0
HTTP_RETRIES = 1
HEARTBEAT_SECONDS = 2.0
DAEMON_LOOP_SECONDS = 1.0
# REST is only a fallback while the realtime WebSocket is unavailable. Keep it
# deliberately slower so a disconnected WS cannot create an API request storm.
REST_TICK_TTL_SECONDS = 15.0
ACCOUNT_TTL_SECONDS = 5.0
ORDERS_TTL_SECONDS = 5.0
POSITIONS_TTL_SECONDS = 5.0
WORKING_DATES_TTL_SECONDS = 24 * 60 * 60
STOCK_ROUND_LOT = 100
# One canonical fallback for live sizing, PAPER and backtest.  A successful
# DNSE fee lookup is saved into AppSettings and therefore supersedes these
# values everywhere; individual modules must not invent their own fallback.
DEFAULT_BUY_FEE_PCT = 0.045
DEFAULT_SELL_FEE_PCT = 0.045
DEFAULT_SELL_TAX_PCT = 0.1
PAPER_BUY_FEE_RATE = DEFAULT_BUY_FEE_PCT / 100.0
PAPER_SELL_FEE_RATE = DEFAULT_SELL_FEE_PCT / 100.0
PAPER_SELL_TAX_RATE = DEFAULT_SELL_TAX_PCT / 100.0
API_BASE_URL = os.getenv("DNSE_BASE_URL", "https://openapi.dnse.com.vn").rstrip("/")
API_VERSION = os.getenv("DNSE_API_VERSION", "2026-05-07")
WS_URL = os.getenv("DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn")

# Official Vietnam stock-exchange closures for 2026 (VNX/HNX schedule).
# DNSE working dates remain the primary calendar; this list is a local
# fail-safe and custom_holidays only contains user additions.
DEFAULT_VN_TRADING_HOLIDAYS = (
    "2026-01-01",
    "2026-01-02",
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-04-27",
    "2026-04-30",
    "2026-05-01",
    "2026-08-31",
    "2026-09-01",
    "2026-09-02",
)

# The stock universe carried over from the stable CKCS system.  Keeping the
# default in code means a new account starts with the same universe even when
# DNSE_CKCS_WATCHLIST is not present in .env.
DEFAULT_CKCS_WATCHLIST = (
    "AAA", "ANV", "CTD", "CTP", "DC4", "DBC", "DCM", "DGC", "DGW", "DIG",
    "DRC", "DXS", "GEG", "HAG", "HAP", "HAR", "HPG", "HPX", "HT1", "IDC",
    "ITC", "JVC", "KDH", "LDG", "MBS", "NHA", "NLG", "NVL", "PDR", "PLX",
    "PNJ", "POW", "QCG", "SCR", "SCS", "TDM", "TLG", "TV2", "VIX", "VPG",
)

# RULE decides whether an event acts (AUTO) or only observes (ALERT).
# Telegram delivery is configured separately and only in the Telegram panel.
TELEGRAM_NOTIFICATION_DEFAULTS: dict[str, bool] = {
    "buy_queued": True,
    "closed": True,
    "protect": False,
    "indicator_exit": True,
    "blocked_buy": False,
    "corporate_action": True,
    "external_sell": True,
    "system": True,
}
TELEGRAM_COOLDOWN_DEFAULTS: dict[str, int] = {
    "protect": 0,
    "indicator_exit": 30,
    "blocked_buy": 30,
    "corporate_action": 1440,
    "external_sell": 0,
    "system": 15,
}

# Canonical operating defaults shared by LIVE, PAPER and backtest.  Keep the
# complete rule here so a new account and an older sparse settings file start
# from the same reviewed strategy instead of inheriting dataclass fallbacks.
DEFAULT_RULE_PARAMETERS: dict[str, Any] = {
    "ma_period": 200,
    "pivot_left": 3,
    "pivot_right": 3,
    "pivot_horizontal_pct": 1.0,
    "ma_zone_pct": 1.0,
    "confirm_sessions": 3,
    "volume_confirmation": False,
    "volume_average_sessions": 20,
    "high_volume_ratio": 1.5,
    "low_volume_ratio": 0.8,
    "buy_ema_fast": 3,
    "buy_ema_slow": 6,
    "sell_ema_fast": 3,
    "sell_ema_slow": 6,
    "rsi_period": 14,
    "buy_signal_use_ema": True,
    "buy_signal_use_rsi": True,
    "buy_volume_enabled": False,
    "buy_volume_average_sessions": 20,
    "buy_volume_min_ratio": 1.0,
    "sell_signal_use_ema": True,
    "sell_signal_use_rsi": True,
    "indicator_exit_policy": "ALERT",
    "max_positions": 5,
    "initial_sl_pct": -3.5,
    "reentry_sl_pct": -2.5,
    "loss_lock_count": 3,
    "loss_lock_hours": 24,
    "loss_lock_mode": "TIMED",
    "no_compound_enabled": True,
    "force_min_lot_enabled": True,
    "take_profit_pct": 7.0,
    "normal_arm_pct": 7.0,
    "normal_sell_pct": 100.0,
    "normal_giveback_pct": 2.5,
    "normal_atr_activation_multiplier": 0.55,
    "normal_atr_multiplier": 0.8,
    "normal_atr_activation_enabled": True,
    "normal_atr_trail_enabled": True,
    "normal_retention_pct": 90.0,
    "normal_retention_until_pct": 5.0,
    "normal_retention_enabled": True,
    "normal_retention_until_enabled": True,
    "normal_policy": "AUTO",
    "normal_dynamic_enabled": True,
    "normal_t2_reset_enabled": False,
    "normal_repeat_enabled": False,
    "whipsaw_enabled": True,
    "whipsaw_n": 3,
    "whipsaw_x": 7,
    "buy_confirmation_enabled": False,
    "buy_confirmation_minutes": 5,
    "buy_confirmation_require_ema": True,
    "buy_confirmation_require_rsi": True,
    "buy_window_enabled": True,
    "buy_window_start": "14:00",
    "exposure": {
        "ACCUMULATION": 0.60,
        "DISTRIBUTION": 0.50,
        "UPTREND": 0.90,
        "DOWNTREND": 0.10,
    },
}


def default_rule_parameters() -> dict[str, Any]:
    """Return an isolated copy so settings normalization cannot mutate defaults."""

    return deepcopy(DEFAULT_RULE_PARAMETERS)


def merge_rule_parameters(values: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay saved values on operating defaults while preserving migrations."""

    configured = dict(values) if isinstance(values, dict) else {}
    legacy_fast = configured.get("ema_fast")
    legacy_slow = configured.get("ema_slow")
    if legacy_fast is not None:
        configured.setdefault("buy_ema_fast", legacy_fast)
        configured.setdefault("sell_ema_fast", legacy_fast)
    if legacy_slow is not None:
        configured.setdefault("buy_ema_slow", legacy_slow)
        configured.setdefault("sell_ema_slow", legacy_slow)
    if (
        "normal_atr_multiplier" in configured
        and "normal_atr_activation_multiplier" not in configured
    ):
        configured["normal_atr_activation_multiplier"] = configured["normal_atr_multiplier"]
    configured_exposure = configured.get("exposure")
    merged = {**default_rule_parameters(), **configured}
    if isinstance(configured_exposure, dict):
        merged["exposure"] = {
            **default_rule_parameters()["exposure"],
            **configured_exposure,
        }
    return merged


def _watchlist_from_env() -> list[str]:
    raw = os.getenv("DNSE_CKCS_WATCHLIST", "")
    configured = list(
        dict.fromkeys(item.strip().upper() for item in raw.split(",") if item.strip())
    )
    return configured or list(DEFAULT_CKCS_WATCHLIST)


def active_account_id() -> str:
    for key in ("DNSE_STOCK_ACCOUNT_NO", "DNSE_ACCOUNT_NO"):
        value = str(os.getenv(key, "") or "").strip()
        if value and value.lower() not in {"none", "true", "false"}:
            return value
    return "PAPER"


def normalize_account_id(account_id: str | None) -> str:
    value = str(account_id or "PAPER").strip().upper() or "PAPER"
    # Account IDs are directory names.  Never allow separators or traversal.
    return re.sub(r"[^A-Z0-9_.-]", "_", value).strip(".") or "PAPER"


def account_root(account_id: str | None = None) -> Path:
    account = normalize_account_id(account_id or active_account_id())
    return ACCOUNTS_ROOT / account


def update_env(values: dict[str, str | None], path: str | Path = ENV_PATH) -> None:
    """Atomically update selected private values in Viking V2's own .env."""
    target = Path(path)
    try:
        current = target.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        current = []
    removals = {str(key) for key, value in values.items() if value is None}
    pending = {str(key): str(value) for key, value in values.items() if value is not None}
    output: list[str] = []
    for line in current:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in removals:
            continue
        output.append(f"{key}={pending.pop(key)}" if key in pending else line)
    output.extend(f"{key}={value}" for key, value in pending.items())
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    for key, value in values.items():
        if value is None:
            os.environ.pop(str(key), None)
        else:
            os.environ[str(key)] = str(value)


def finite_nonnegative(value: Any) -> float:
    try:
        number = float(value or 0.0)
        return number if math.isfinite(number) and number >= 0 else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def normalize_priority_allocations(raw: Any, symbols: Any) -> dict[str, dict[str, float]]:
    allowed = {str(symbol).strip().upper() for symbol in symbols}
    result = {}
    for symbol, row in (raw.items() if isinstance(raw, dict) else []):
        symbol = str(symbol).strip().upper()
        if symbol not in allowed or not isinstance(row, dict):
            continue
        result[symbol] = {
            "limit_vnd": finite_nonnegative(row.get("limit_vnd")),
            "use_pct": min(100.0, finite_nonnegative(row.get("use_pct", 100.0))),
        }
    return result


def validate_priority_capital(total: float, symbols: Any, allocations: Any) -> None:
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Tổng vốn Priority phải lớn hơn 0 và hữu hạn.")
    normalized = normalize_priority_allocations(allocations, symbols)
    if sum(row["limit_vnd"] for row in normalized.values()) > total + 0.01:
        raise ValueError("Tổng hạn mức từng mã không được vượt tổng vốn Priority.")


@dataclass(slots=True)
class AppSettings:
    watchlist: list[str] = field(default_factory=_watchlist_from_env)
    # Symbols promoted inside the watchlist. They keep every normal entry
    # guard, but are ranked first and may bypass only the BOT slot quota.
    priority_symbols: list[str] = field(default_factory=list)
    priority_capital_enabled: bool = False
    priority_total_capital: float = 0.0
    priority_allocations: dict[str, dict[str, float]] = field(default_factory=dict)
    # Manual fallback only. LIVE/PAPER normally learns the exchange from DNSE.
    symbol_exchanges: dict[str, str] = field(default_factory=dict)
    paper_mode: bool = True
    paper_initial_balance: float = 100_000_000.0
    # Dashboard account counters either roll with the Vietnam calendar day or
    # accumulate until the operator resets their shared display cutoff.
    daily_stats_mode: str = "DAILY"
    daily_stats_reset_time: str = "00:00"
    confirm_real_orders: bool = True
    telegram_enabled: bool = False
    telegram_chat_id: str = ""
    telegram_token_env: str = "TELE_BOT_KEY"
    telegram_buy_batch_minutes: int = 30
    # Per-event delivery controls shared by PAPER and REAL.
    telegram_notifications: dict[str, bool] = field(
        default_factory=lambda: dict(TELEGRAM_NOTIFICATION_DEFAULTS)
    )
    telegram_cooldown_minutes: dict[str, int] = field(
        default_factory=lambda: dict(TELEGRAM_COOLDOWN_DEFAULTS)
    )
    bot_order_mode: str = "MARKET"
    # Operational guard: after a MANUAL SELL fill, pause only new BOT BUYs.
    manual_sell_pause_minutes: int = 15
    allow_ato: bool = False
    allow_atc: bool = False
    # Defaults attached to each new BOT trade. Existing/manual trades keep
    # their own persisted management flags.
    bot_sl_enabled: bool = True
    bot_em_modes: list[str] = field(default_factory=lambda: ["NORMAL", "IND_EXIT"])
    # Optional live/PAPER Phase-1 override. The automatic classifier keeps
    # running for observability while this state/exposure drives new entries.
    market_phase_override_enabled: bool = False
    market_phase_override: str = "ACCUMULATION"
    market_phase_override_exposure_pct: float = 60.0
    # Real DNSE rates.  The bot needs them to leave room for the fee when
    # sizing an order, and the paper broker charges with them.
    buy_fee_pct: float = DEFAULT_BUY_FEE_PCT
    sell_fee_pct: float = DEFAULT_SELL_FEE_PCT
    sell_tax_pct: float = DEFAULT_SELL_TAX_PCT
    sell_wait_policy: str = "RECHECK"
    # Live/PAPER operation follows the unfinished 1D candle so EMA/RSI can
    # react during the session. Backtest explicitly opts into CLOSED when it
    # only has completed daily candles.
    signal_mode: str = "REALTIME"
    # REALTIME still calculates indicators on the unfinished 1D candle.  This
    # setting only controls how often a new provisional close is accepted.
    realtime_indicator_interval: str = "TICK"
    rule_parameters: dict[str, Any] = field(default_factory=default_rule_parameters)
    corporate_actions: list[dict[str, Any]] = field(default_factory=list)
    custom_holidays: list[str] = field(default_factory=list)

    @property
    def trading_holidays(self) -> list[str]:
        """Official exchange closures plus account-specific additions."""
        return sorted(set(DEFAULT_VN_TRADING_HOLIDAYS).union(self.custom_holidays))

    def normalize(self) -> "AppSettings":
        self.watchlist = list(
            dict.fromkeys(str(item).strip().upper() for item in self.watchlist if str(item).strip())
        )
        if not self.watchlist:
            self.watchlist = list(DEFAULT_CKCS_WATCHLIST)
        watchlist_set = set(self.watchlist)
        self.priority_symbols = [
            symbol for symbol in dict.fromkeys(
                str(item).strip().upper()
                for item in self.priority_symbols
                if str(item).strip()
            )
            if symbol in watchlist_set
        ]
        self.priority_capital_enabled = bool(self.priority_capital_enabled)
        self.priority_total_capital = finite_nonnegative(self.priority_total_capital)
        self.priority_allocations = normalize_priority_allocations(
            self.priority_allocations, self.priority_symbols,
        )
        aliases = {"HOSE": "HOSE", "HSX": "HOSE", "STO": "HOSE",
                   "HNX": "HNX", "STX": "HNX", "UPCOM": "UPCOM", "UPX": "UPCOM"}
        self.symbol_exchanges = {
            str(symbol).strip().upper(): aliases[str(exchange).strip().upper()]
            for symbol, exchange in (self.symbol_exchanges or {}).items()
            if str(symbol).strip() and str(exchange).strip().upper() in aliases
        }
        self.paper_initial_balance = max(0.0, float(self.paper_initial_balance or 0.0))
        self.daily_stats_mode = str(self.daily_stats_mode or "DAILY").strip().upper()
        if self.daily_stats_mode not in {"DAILY", "SINCE_RESET"}:
            self.daily_stats_mode = "DAILY"
        self.daily_stats_reset_time = str(self.daily_stats_reset_time or "00:00").strip()
        try:
            self.daily_stats_reset_time = datetime.strptime(
                self.daily_stats_reset_time, "%H:%M",
            ).strftime("%H:%M")
        except ValueError:
            self.daily_stats_reset_time = "00:00"
        self.telegram_chat_id = str(self.telegram_chat_id or "").strip()
        self.telegram_token_env = str(self.telegram_token_env or "TELE_BOT_KEY").strip()
        try:
            self.telegram_buy_batch_minutes = max(
                1, min(120, int(float(self.telegram_buy_batch_minutes or 30)))
            )
        except (TypeError, ValueError):
            self.telegram_buy_batch_minutes = 30
        raw_notifications = (
            self.telegram_notifications
            if isinstance(self.telegram_notifications, dict) else {}
        )
        self.telegram_notifications = {
            key: bool(raw_notifications.get(key, default))
            for key, default in TELEGRAM_NOTIFICATION_DEFAULTS.items()
        }
        raw_cooldowns = (
            self.telegram_cooldown_minutes
            if isinstance(self.telegram_cooldown_minutes, dict) else {}
        )
        normalized_cooldowns: dict[str, int] = {}
        for key, default in TELEGRAM_COOLDOWN_DEFAULTS.items():
            try:
                normalized_cooldowns[key] = max(
                    0, min(10080, int(float(raw_cooldowns.get(key, default))))
                )
            except (TypeError, ValueError):
                normalized_cooldowns[key] = default
        self.telegram_cooldown_minutes = normalized_cooldowns
        self.bot_order_mode = str(self.bot_order_mode or "MARKET").strip().upper()
        if self.bot_order_mode not in {"MARKET", "LO_LOCAL"}:
            self.bot_order_mode = "MARKET"
        try:
            self.manual_sell_pause_minutes = max(
                0, min(1440, int(float(self.manual_sell_pause_minutes or 0))),
            )
        except (TypeError, ValueError):
            self.manual_sell_pause_minutes = 15
        self.allow_ato = bool(self.allow_ato)
        self.allow_atc = bool(self.allow_atc)
        self.bot_sl_enabled = bool(self.bot_sl_enabled)
        for name in ("buy_fee_pct", "sell_fee_pct", "sell_tax_pct"):
            setattr(self, name, min(5.0, max(0.0, float(getattr(self, name) or 0.0))))
        self.bot_em_modes = normalize_exit_modes(self.bot_em_modes)
        self.market_phase_override_enabled = bool(self.market_phase_override_enabled)
        self.market_phase_override = str(
            self.market_phase_override or "ACCUMULATION"
        ).strip().upper()
        if self.market_phase_override not in {
            "UPTREND", "DOWNTREND", "ACCUMULATION", "DISTRIBUTION",
        }:
            self.market_phase_override = "ACCUMULATION"
        try:
            self.market_phase_override_exposure_pct = min(
                100.0,
                max(0.0, float(self.market_phase_override_exposure_pct or 0.0)),
            )
        except (TypeError, ValueError):
            self.market_phase_override_exposure_pct = 60.0
        self.sell_wait_policy = str(self.sell_wait_policy or "RECHECK").strip().upper()
        if self.sell_wait_policy not in {"RECHECK", "KEEP"}:
            self.sell_wait_policy = "RECHECK"
        self.signal_mode = str(self.signal_mode or "REALTIME").strip().upper()
        if self.signal_mode not in {"REALTIME", "CLOSED"}:
            self.signal_mode = "REALTIME"
        self.realtime_indicator_interval = str(
            self.realtime_indicator_interval or "TICK"
        ).strip().upper()
        if self.realtime_indicator_interval not in {"TICK", "1M", "2M", "5M"}:
            self.realtime_indicator_interval = "TICK"
        self.rule_parameters = merge_rule_parameters(self.rule_parameters)
        self.corporate_actions = [
            dict(item) for item in self.corporate_actions if isinstance(item, dict) and str(item.get("symbol", "")).strip()
        ]
        holidays: list[str] = []
        for raw in self.custom_holidays or []:
            text = str(raw or "").strip()[:10]
            try:
                datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                continue
            if text not in DEFAULT_VN_TRADING_HOLIDAYS:
                holidays.append(text)
        self.custom_holidays = sorted(set(holidays))
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalize())

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "AppSettings":
        raw = raw if isinstance(raw, dict) else {}
        # Migrate the former all-in-one alert switch once, without retaining
        # it in the current settings model.
        if "telegram_notifications" not in raw and "telegram_signal_alerts" in raw:
            legacy = bool(raw.get("telegram_signal_alerts"))
            raw = dict(raw)
            raw["telegram_notifications"] = {
                **TELEGRAM_NOTIFICATION_DEFAULTS,
                "protect": legacy,
                "indicator_exit": legacy,
                "blocked_buy": legacy,
            }
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in raw.items() if key in allowed}).normalize()


def load_settings(account_id: str | None = None) -> AppSettings:
    path = account_root(account_id) / "settings.json"
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return AppSettings.from_dict(json.load(handle))
    except (OSError, ValueError, TypeError):
        settings = AppSettings().normalize()
        if not path.exists():
            save_settings(settings, account_id)
        return settings


def save_settings(settings: AppSettings, account_id: str | None = None) -> Path:
    path = account_root(account_id) / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    return path
