from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .. import config
from ..storage import AtomicJSONStore


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def bar_date(row: dict[str, Any]) -> date:
    return datetime.fromtimestamp(int(float(row.get("time", 0) or 0)), VN_TZ).date()


def normalize_ohlc(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    keys = ("t", "o", "h", "l", "c", "v")
    if any(not isinstance(data.get(key), list) for key in keys):
        return []
    length = len(data["t"])
    if length <= 0 or any(len(data[key]) != length for key in keys):
        return []
    rows: list[dict[str, Any]] = []
    for index in range(length):
        try:
            rows.append({
                "time": int(float(data["t"][index])),
                "open": float(data["o"][index]),
                "high": float(data["h"][index]),
                "low": float(data["l"][index]),
                "close": float(data["c"][index]),
                "volume": float(data["v"][index]),
                "closed": True,
            })
        except (TypeError, ValueError, OverflowError):
            continue
    return sorted({int(row["time"]): row for row in rows}.values(), key=lambda row: int(row["time"]))


class HistoricalDataStore:
    """DNSE history adapter with an isolated cache for each resolution."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        root: str | Path | None = None,
        fetcher: Callable[[str, str, int, int], dict[str, Any] | None] | None = None,
    ):
        self.client = client
        self.fetcher = fetcher or (client.get_ohlc if client is not None else None)
        self.root = Path(root or (config.RUNTIME_ROOT / "backtest"))
        self.cache_dir = self.root / "cache"
        self.runs_dir = self.root / "runs"
        self.scenarios_path = self.root / "scenarios.json"
        self.settings_path = self.root / "settings.json"

    def _store(self, symbol: str, resolution: str = "1D") -> AtomicJSONStore:
        safe = "".join(char for char in str(symbol).upper() if char.isalnum() or char in "_-")
        safe_resolution = str(resolution or "1D").upper().replace("/", "_")
        return AtomicJSONStore(
            self.cache_dir / f"{safe}_{safe_resolution}.json",
            default={"symbol": safe, "resolution": safe_resolution, "bars": []},
        )

    @staticmethod
    def _timestamp(value: date, *, end: bool = False) -> int:
        local = datetime.combine(value + (timedelta(days=1) if end else timedelta()), datetime.min.time(), VN_TZ)
        return int(local.timestamp()) - (1 if end else 0)

    @staticmethod
    def _chunk_days(resolution: str) -> int:
        return {
            "1": 7,
            "3": 14,
            "5": 30,
            "15": 90,
            "30": 180,
            "1H": 365,
            "1D": 730,
            "1W": 1460,
        }.get(str(resolution or "1D").upper(), 30)

    def load_bars(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
        *,
        resolution: str = "1D",
        warmup_sessions: int = 0,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        symbol = str(symbol or "").strip().upper()
        resolution = str(resolution or "1D").upper()
        start_date = datetime.strptime(str(start)[:10], "%Y-%m-%d").date() if not isinstance(start, date) else start
        end_date = datetime.strptime(str(end)[:10], "%Y-%m-%d").date() if not isinstance(end, date) else end
        warmup_days = max(400, int(warmup_sessions * 1.75)) if warmup_sessions else 0
        requested_start = start_date - timedelta(days=warmup_days)
        store = self._store(symbol, resolution)
        raw = store.read()
        cached = [dict(row) for row in (raw.get("bars") if isinstance(raw, dict) else []) if isinstance(row, dict)]
        cached = sorted({int(row.get("time", 0)): row for row in cached if row.get("time")}.values(), key=lambda row: int(row["time"]))
        coverage_start: date | None = None
        coverage_end: date | None = None
        if isinstance(raw, dict):
            try:
                if raw.get("coverage_start"):
                    coverage_start = datetime.strptime(str(raw["coverage_start"])[:10], "%Y-%m-%d").date()
                if raw.get("coverage_end"):
                    coverage_end = datetime.strptime(str(raw["coverage_end"])[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                coverage_start = coverage_end = None
        covered = bool(
            coverage_start is not None
            and coverage_end is not None
            and coverage_start <= requested_start
            and coverage_end >= end_date
        )
        # Compatibility for caches created before explicit coverage metadata.
        if not covered and cached:
            covered = (
                bar_date(cached[0]) <= requested_start + timedelta(days=7)
                and bar_date(cached[-1]) >= end_date - timedelta(days=7)
            )
        if force or not covered:
            if self.fetcher is None:
                if not cached:
                    raise RuntimeError(f"Không có nguồn dữ liệu lịch sử cho {symbol}.")
            else:
                if progress:
                    progress(f"Đang tải {symbol} · {resolution}...")
                fetched: list[dict[str, Any]] = []
                cursor = requested_start
                while cursor <= end_date:
                    chunk_end = min(end_date, cursor + timedelta(days=self._chunk_days(resolution)))
                    payload = self.fetcher(symbol, resolution, self._timestamp(cursor), self._timestamp(chunk_end, end=True))
                    fetched.extend(normalize_ohlc(payload))
                    cursor = chunk_end + timedelta(days=1)
                if fetched:
                    cached = sorted(
                        {int(row["time"]): row for row in [*cached, *fetched]}.values(),
                        key=lambda row: int(row["time"]),
                    )
                new_start = min(requested_start, coverage_start) if coverage_start else requested_start
                new_end = max(end_date, coverage_end) if coverage_end else end_date
                store.write({
                    "symbol": symbol,
                    "resolution": resolution,
                    "updated_at": time.time(),
                    "coverage_start": new_start.isoformat(),
                    "coverage_end": new_end.isoformat(),
                    "bars": cached,
                })
                if not fetched and not cached:
                    raise RuntimeError(f"DNSE không trả dữ liệu {resolution} cho {symbol}.")
        return [dict(row) for row in cached if requested_start <= bar_date(row) <= end_date]

    def load_daily(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
        *,
        warmup_sessions: int = 250,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        return self.load_bars(
            symbol,
            start,
            end,
            resolution="1D",
            warmup_sessions=warmup_sessions,
            force=force,
            progress=progress,
        )

    def load_execution(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
        *,
        resolution: str = "AUTO",
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[list[dict[str, Any]], str, list[str]]:
        """Load execution bars. AUTO prefers 5-minute data, then 15-minute, then daily."""
        requested = str(resolution or "AUTO").upper()
        candidates = ("5", "15", "1D") if requested == "AUTO" else (requested,)
        errors: list[str] = []
        for candidate in candidates:
            try:
                rows = self.load_bars(
                    symbol,
                    start,
                    end,
                    resolution=candidate,
                    force=force,
                    progress=progress,
                )
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            if rows:
                warnings = []
                if candidate != candidates[0]:
                    warnings.append(f"{symbol}: thiếu dữ liệu {candidates[0]}, dùng {candidate}.")
                if candidate != "1D":
                    requested_start = (
                        datetime.strptime(str(start)[:10], "%Y-%m-%d").date()
                        if not isinstance(start, date) else start
                    )
                    requested_end = (
                        datetime.strptime(str(end)[:10], "%Y-%m-%d").date()
                        if not isinstance(end, date) else end
                    )
                    actual_start, actual_end = bar_date(rows[0]), bar_date(rows[-1])
                    if actual_start > requested_start + timedelta(days=7) or actual_end < requested_end - timedelta(days=7):
                        warnings.append(
                            f"{symbol}: {candidate} chỉ có {actual_start.isoformat()}–{actual_end.isoformat()}; "
                            "ngày thiếu dùng nến 1D."
                        )
                return rows, candidate, warnings
        raise RuntimeError("; ".join(errors) or f"Không có dữ liệu thực thi cho {symbol}.")

    def save_run(self, result: Any) -> Path:
        path = self.runs_dir / f"{result.run_id}.json"
        AtomicJSONStore(path).write(result.to_dict())
        return path

    def list_runs(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            raw = AtomicJSONStore(path, default={}).read()
            if isinstance(raw, dict):
                rows.append(raw)
        return rows

    def load_settings(self) -> dict[str, Any]:
        raw = AtomicJSONStore(self.settings_path, default={}).read()
        return dict(raw) if isinstance(raw, dict) else {}

    def save_settings(self, values: dict[str, Any]) -> None:
        AtomicJSONStore(self.settings_path, default={}).write(dict(values or {}))

    def load_scenarios(self) -> list[dict[str, Any]]:
        raw = AtomicJSONStore(self.scenarios_path, default=[]).read()
        return [dict(row) for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []

    def save_scenarios(self, rows: list[dict[str, Any]]) -> None:
        AtomicJSONStore(self.scenarios_path, default=[]).write(rows)
