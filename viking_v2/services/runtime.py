from __future__ import annotations

from pathlib import Path
from collections import deque
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
import time
from typing import Any

from ..config import account_root
from ..models import RuntimeConfig, RuntimeStatus
from ..storage import AtomicJSONStore


class RuntimeBridge:
    def __init__(self, account_id: str | None = None, root: str | Path | None = None):
        self.root = Path(root) if root is not None else account_root(account_id)
        self.config_store = AtomicJSONStore(self.root / "runtime_config.json", default={})
        self.status_store = AtomicJSONStore(
            self.root / "runtime_status.json", default=RuntimeStatus.empty().to_dict
        )

    @property
    def pending_orders_path(self) -> Path:
        return self.root / "pending_orders.json"

    @property
    def paper_state_path(self) -> Path:
        return self.root / "paper_state.json"

    @property
    def trade_state_path(self) -> Path:
        return self.root / "trade_state.json"

    @property
    def rule_state_path(self) -> Path:
        return self.root / "rule_state.json"

    @property
    def market_cache_path(self) -> Path:
        return self.root / "market_bars.json"

    @property
    def journal_path(self) -> Path:
        return self.root / "order_journal.jsonl"

    @property
    def signal_log_path(self) -> Path:
        return self.root / "signal_log.csv"

    @property
    def history_csv_path(self) -> Path:
        return self.root / "order_history.csv"

    @property
    def daily_stats_path(self) -> Path:
        return self.root / "daily_stats.json"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    def read_config(self) -> RuntimeConfig:
        return RuntimeConfig.from_dict(self.config_store.read())

    def write_config(self, config: RuntimeConfig) -> None:
        config.updated_at = time.time()
        self.config_store.write(config.to_dict())

    def disarm(self, watchlist: list[str] | None = None, paper_mode: bool | None = None) -> RuntimeConfig:
        current = self.read_config()
        value = RuntimeConfig(
            watchlist=list(current.watchlist if watchlist is None else watchlist),
            paper_mode=current.paper_mode if paper_mode is None else bool(paper_mode),
            bot_enabled=False,
        )
        self.write_config(value)
        return value

    def read_status(self) -> dict[str, Any]:
        value = self.status_store.read()
        return value if isinstance(value, dict) else RuntimeStatus.empty().to_dict()

    def write_status(self, status: RuntimeStatus) -> None:
        self.status_store.write(status.to_dict())


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "process": record.processName,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        target = getattr(record, "ui_target", None)
        if target in {"manual", "bot"}:
            payload["ui_target"] = target
        return json.dumps(payload, ensure_ascii=False)


def recent_ui_logs(log_dir: str | Path, *, limit: int = 400) -> list[dict[str, str]]:
    """Read a bounded display-only tail; never reconstruct or submit orders."""
    root = Path(log_dir)
    if limit <= 0:
        return []
    entries: deque[dict[str, str]] = deque(maxlen=min(limit, 2000))
    try:
        rotated = sorted(root.glob("ui.jsonl.*"), key=lambda path: path.name)[-1:]
        paths = [*rotated, root / "ui.jsonl"]
        for path in paths:
            try:
                with path.open("rb") as stream:
                    size = stream.seek(0, 2)
                    offset = max(0, size - 1_048_576)
                    stream.seek(offset)
                    if offset:
                        stream.readline()  # Skip a possibly cut JSON record.
                    for raw in stream:
                        try:
                            record = json.loads(raw.decode("utf-8"))
                            if not isinstance(record, dict) or record.get("ui_target") not in {"manual", "bot"}:
                                continue
                            message, ts = record.get("message"), record.get("ts")
                            if not isinstance(message, str) or not isinstance(ts, str):
                                continue
                            datetime.fromisoformat(ts)
                            entries.append({"ts": ts, "target": record["ui_target"], "message": message[:4000]})
                        except (UnicodeError, ValueError, TypeError, RecursionError):
                            continue
            except OSError:
                continue
    except OSError:
        return []
    return list(entries)


def setup_logging(log_dir: str | Path, process_name: str, *, debug: bool = False) -> logging.Logger:
    root = Path(log_dir)
    root.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"VIKING_V2.{process_name}")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()
    text_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    text_handler = TimedRotatingFileHandler(
        root / f"{process_name}.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    text_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    text_handler.setFormatter(text_fmt)

    json_handler = TimedRotatingFileHandler(
        root / f"{process_name}.jsonl", when="midnight", backupCount=14, encoding="utf-8"
    )
    json_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    json_handler.setFormatter(JsonLineFormatter())

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(text_handler)
    logger.addHandler(json_handler)
    logger.addHandler(console)
    logger.propagate = False
    return logger
