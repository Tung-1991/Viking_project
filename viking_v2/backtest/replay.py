from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import closing
from datetime import date, datetime, time
from hashlib import sha256
from pathlib import Path
import csv
import re
import sqlite3
from typing import Any, Iterable
from zoneinfo import ZoneInfo


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
REQUIRED_COLUMNS = ("time", "open", "high", "low", "close", "volume")


@dataclass(slots=True)
class ReplayDataset:
    symbol: str
    resolution: str
    bar_count: int
    coverage_start: str
    coverage_end: str
    full_days: int
    partial_days: int
    source_name: str = ""
    source_hash: str = ""
    timezone: str = "Asia/Ho_Chi_Minh"
    price_scale: float = 1.0
    imported_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReplayImportPreview:
    path: str
    symbol: str
    resolution: str
    timezone: str
    price_scale: float
    bar_count: int
    coverage_start: str
    coverage_end: str
    full_days: int
    partial_days: int
    partial_dates: list[str]
    source_hash: str
    inserted: int = 0
    replaced: int = 0
    unchanged: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def infer_symbol(path: str | Path) -> str:
    """TradingView names exports like ``HOSE_DLY_CTS, 1.csv``."""
    stem = Path(path).stem.strip()
    before_resolution = stem.rsplit(",", 1)[0].strip()
    tokens = [token for token in re.split(r"[_\s.-]+", before_resolution.upper()) if token]
    candidate = tokens[-1] if tokens else ""
    return candidate if re.fullmatch(r"[A-Z0-9]{2,12}", candidate) else ""


def normalize_resolution(value: str | int | None) -> str:
    raw = str(value or "").strip().upper().replace("MIN", "").replace("M", "")
    if raw.endswith("H") and raw[:-1].isdigit():
        minutes = int(raw[:-1]) * 60
        return "1H" if minutes == 60 else str(minutes)
    if raw.isdigit() and int(raw) > 0:
        return str(int(raw))
    return ""


def infer_resolution(path: str | Path) -> str:
    stem = Path(path).stem
    match = re.search(r",\s*([0-9]+\s*[mMhH]?)\s*$", stem)
    return normalize_resolution(match.group(1)) if match else ""


def resolution_minutes(value: str) -> int:
    normalized = normalize_resolution(value)
    if normalized == "1H":
        return 60
    return int(normalized) if normalized.isdigit() else 10**9


def _column_map(headers: Iterable[Any]) -> dict[str, str]:
    available = {str(value or "").strip().casefold(): str(value or "") for value in headers}
    missing = [name for name in REQUIRED_COLUMNS if name not in available]
    if missing:
        raise ValueError("Thiếu cột bắt buộc: " + ", ".join(missing))
    return {name: available[name] for name in REQUIRED_COLUMNS}


def _raw_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError("File CSV không có dòng tiêu đề.")
            columns = _column_map(reader.fieldnames)
            return [{key: row.get(source) for key, source in columns.items()} for row in reader]
    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover - dependency ships with the app
            raise RuntimeError("Thiếu thư viện openpyxl để đọc Excel.") from exc
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in book.worksheets:
                iterator = sheet.iter_rows(values_only=True)
                headers = next(iterator, None)
                if not headers:
                    continue
                try:
                    columns = _column_map(headers)
                except ValueError:
                    continue
                positions = {str(value or ""): index for index, value in enumerate(headers)}
                return [
                    {key: values[positions[source]] if positions[source] < len(values) else None
                     for key, source in columns.items()}
                    for values in iterator
                    if any(value is not None for value in values)
                ]
        finally:
            book.close()
        raise ValueError("Không có sheet nào chứa đủ cột OHLCV.")
    raise ValueError("Chỉ hỗ trợ file .csv hoặc .xlsx.")


def _timestamp(raw: Any, timezone_name: str) -> int:
    zone = ZoneInfo(timezone_name)
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, (int, float)):
        number = float(raw)
        if number > 10**12:
            number /= 1000.0
        return int(number)
    else:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("Timestamp trống.")
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            number = float(text)
            if number > 10**12:
                number /= 1000.0
            return int(number)
        value = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return int(value.timestamp())


def _inside_session(stamp: datetime) -> bool:
    current = stamp.time().replace(second=0, microsecond=0)
    return (
        time(9, 15) <= current <= time(11, 30)
        or time(13, 0) <= current <= time(14, 30)
        or current == time(14, 45)
    )


def _normalized_rows(
    path: Path,
    *,
    timezone_name: str,
    price_scale: float | None,
) -> tuple[list[dict[str, Any]], float]:
    source = _raw_rows(path)
    if not source:
        raise ValueError("File không có dữ liệu.")
    closes: list[float] = []
    for row in source[: min(500, len(source))]:
        try:
            closes.append(abs(float(row.get("close") or 0.0)))
        except (TypeError, ValueError):
            continue
    scale = float(price_scale or 0.0)
    if scale <= 0:
        useful = sorted(value for value in closes if value > 0)
        median = useful[len(useful) // 2] if useful else 0.0
        scale = 1000.0 if median >= 1000.0 else 1.0
    if scale <= 0:
        raise ValueError("Hệ số giá phải lớn hơn 0.")

    normalized: dict[int, dict[str, Any]] = {}
    for line, raw in enumerate(source, start=2):
        try:
            stamp = _timestamp(raw.get("time"), timezone_name)
            local = datetime.fromtimestamp(stamp, VN_TZ)
            if not _inside_session(local):
                raise ValueError(f"ngoài giờ giao dịch ({local:%H:%M})")
            opened = float(raw.get("open")) / scale
            high = float(raw.get("high")) / scale
            low = float(raw.get("low")) / scale
            close = float(raw.get("close")) / scale
            volume = float(raw.get("volume") or 0.0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Dòng {line} không hợp lệ: {exc}") from exc
        if min(opened, high, low, close) <= 0:
            raise ValueError(f"Dòng {line}: giá phải lớn hơn 0.")
        if high < max(opened, close, low) or low > min(opened, close, high):
            raise ValueError(f"Dòng {line}: OHLC không hợp lệ.")
        if volume < 0:
            raise ValueError(f"Dòng {line}: volume âm.")
        row = {
            "time": stamp,
            "open": opened,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "closed": True,
        }
        previous = normalized.get(stamp)
        if previous is not None and previous != row:
            raise ValueError(f"Timestamp {local.isoformat()} bị trùng với OHLCV khác nhau trong cùng file.")
        normalized[stamp] = row
    return [normalized[key] for key in sorted(normalized)], scale


def _day_summary(rows: list[dict[str, Any]]) -> tuple[str, str, int, int, list[str]]:
    grouped: dict[str, list[datetime]] = {}
    for row in rows:
        local = datetime.fromtimestamp(int(row["time"]), VN_TZ)
        grouped.setdefault(local.date().isoformat(), []).append(local)
    partial: list[str] = []
    for day, stamps in grouped.items():
        first, last = min(stamps), max(stamps)
        if first.time() > time(9, 15) or last.time() < time(14, 45):
            partial.append(day)
    days = sorted(grouped)
    return days[0], days[-1], len(days) - len(partial), len(partial), partial


class ReplayDataStore:
    """Transactional, resolution-aware storage for user-imported replay bars."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "replay.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS replay_bars (
                symbol TEXT NOT NULL,
                resolution TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, resolution, timestamp)
            );
            CREATE INDEX IF NOT EXISTS idx_replay_bars_range
                ON replay_bars(symbol, timestamp);
            CREATE TABLE IF NOT EXISTS replay_days (
                symbol TEXT NOT NULL,
                resolution TEXT NOT NULL,
                day TEXT NOT NULL,
                status TEXT NOT NULL,
                bar_count INTEGER NOT NULL,
                first_timestamp INTEGER NOT NULL,
                last_timestamp INTEGER NOT NULL,
                PRIMARY KEY (symbol, resolution, day)
            );
            CREATE TABLE IF NOT EXISTS replay_datasets (
                symbol TEXT NOT NULL,
                resolution TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                timezone TEXT NOT NULL,
                price_scale REAL NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (symbol, resolution)
            );
            """
        )
        return connection

    def inspect_file(
        self,
        path: str | Path,
        *,
        symbol: str | None = None,
        resolution: str | None = None,
        timezone_name: str = "Asia/Ho_Chi_Minh",
        price_scale: float | None = None,
    ) -> ReplayImportPreview:
        source = Path(path)
        if not source.is_file():
            raise ValueError("Không tìm thấy file import.")
        chosen_symbol = str(symbol or infer_symbol(source)).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", chosen_symbol):
            raise ValueError("Không xác định được mã; hãy nhập mã trước khi lưu.")
        chosen_resolution = normalize_resolution(resolution or infer_resolution(source))
        if not chosen_resolution or resolution_minutes(chosen_resolution) >= 24 * 60:
            raise ValueError("Không xác định được resolution intraday.")
        rows, scale = _normalized_rows(source, timezone_name=timezone_name, price_scale=price_scale)
        first, last, full, partial_count, partial = _day_summary(rows)
        digest = sha256(source.read_bytes()).hexdigest()
        return ReplayImportPreview(
            str(source), chosen_symbol, chosen_resolution, timezone_name, scale,
            len(rows), first, last, full, partial_count, partial, digest,
        )

    def import_file(self, path: str | Path, **kwargs: Any) -> ReplayImportPreview:
        preview = self.inspect_file(path, **kwargs)
        rows, _ = _normalized_rows(
            Path(path), timezone_name=preview.timezone, price_scale=preview.price_scale,
        )
        now = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection, connection:
            existing = {
                int(row["timestamp"]): tuple(float(row[key]) for key in ("open", "high", "low", "close", "volume"))
                for row in connection.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM replay_bars "
                    "WHERE symbol=? AND resolution=? AND timestamp BETWEEN ? AND ?",
                    (preview.symbol, preview.resolution, rows[0]["time"], rows[-1]["time"]),
                )
            }
            inserted = replaced = unchanged = 0
            values = []
            for row in rows:
                current = tuple(float(row[key]) for key in ("open", "high", "low", "close", "volume"))
                old = existing.get(int(row["time"]))
                if old is None:
                    inserted += 1
                elif old == current:
                    unchanged += 1
                else:
                    replaced += 1
                values.append((preview.symbol, preview.resolution, int(row["time"]), *current))
            connection.executemany(
                "INSERT INTO replay_bars(symbol,resolution,timestamp,open,high,low,close,volume) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,resolution,timestamp) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume",
                values,
            )
            connection.execute(
                "INSERT INTO replay_datasets(symbol,resolution,source_name,source_hash,timezone,price_scale,imported_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,resolution) DO UPDATE SET "
                "source_name=excluded.source_name,source_hash=excluded.source_hash,timezone=excluded.timezone,"
                "price_scale=excluded.price_scale,imported_at=excluded.imported_at",
                (preview.symbol, preview.resolution, Path(path).name, preview.source_hash,
                 preview.timezone, preview.price_scale, now),
            )
            self._refresh_days(connection, preview.symbol, preview.resolution)
        preview.inserted, preview.replaced, preview.unchanged = inserted, replaced, unchanged
        return preview

    @staticmethod
    def _refresh_days(connection: sqlite3.Connection, symbol: str, resolution: str) -> None:
        connection.execute("DELETE FROM replay_days WHERE symbol=? AND resolution=?", (symbol, resolution))
        grouped: dict[str, list[int]] = {}
        for row in connection.execute(
            "SELECT timestamp FROM replay_bars WHERE symbol=? AND resolution=? ORDER BY timestamp",
            (symbol, resolution),
        ):
            stamp = int(row["timestamp"])
            grouped.setdefault(datetime.fromtimestamp(stamp, VN_TZ).date().isoformat(), []).append(stamp)
        values = []
        for day, stamps in grouped.items():
            first = datetime.fromtimestamp(stamps[0], VN_TZ)
            last = datetime.fromtimestamp(stamps[-1], VN_TZ)
            status = "FULL" if first.time() <= time(9, 15) and last.time() >= time(14, 45) else "PARTIAL"
            values.append((symbol, resolution, day, status, len(stamps), stamps[0], stamps[-1]))
        connection.executemany(
            "INSERT INTO replay_days(symbol,resolution,day,status,bar_count,first_timestamp,last_timestamp) "
            "VALUES(?,?,?,?,?,?,?)",
            values,
        )

    def list_datasets(self) -> list[ReplayDataset]:
        if not self.path.exists():
            return []
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT symbol,resolution,source_name,source_hash,timezone,price_scale,imported_at "
                "FROM replay_datasets ORDER BY symbol,resolution"
            ).fetchall()
        result: list[ReplayDataset] = []
        with closing(self._connect()) as connection, connection:
            for row in rows:
                bar_count = connection.execute(
                    "SELECT COUNT(*) FROM replay_bars WHERE symbol=? AND resolution=?",
                    (row["symbol"], row["resolution"]),
                ).fetchone()[0]
                day_counts = connection.execute(
                    "SELECT MIN(day),MAX(day),SUM(status='FULL'),SUM(status='PARTIAL') FROM replay_days "
                    "WHERE symbol=? AND resolution=?",
                    (row["symbol"], row["resolution"]),
                ).fetchone()
                result.append(ReplayDataset(
                    row["symbol"], row["resolution"], int(bar_count or 0),
                    str(day_counts[0] or ""), str(day_counts[1] or ""),
                    int(day_counts[2] or 0), int(day_counts[3] or 0),
                    row["source_name"], row["source_hash"], row["timezone"],
                    float(row["price_scale"]), row["imported_at"],
                ))
        return result

    def delete_dataset(self, symbol: str, resolution: str) -> int:
        symbol = str(symbol).strip().upper()
        resolution = normalize_resolution(resolution)
        if not self.path.exists() or not symbol or not resolution:
            return 0
        with closing(self._connect()) as connection, connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM replay_bars WHERE symbol=? AND resolution=?", (symbol, resolution),
            ).fetchone()[0]
            connection.execute("DELETE FROM replay_bars WHERE symbol=? AND resolution=?", (symbol, resolution))
            connection.execute("DELETE FROM replay_days WHERE symbol=? AND resolution=?", (symbol, resolution))
            connection.execute("DELETE FROM replay_datasets WHERE symbol=? AND resolution=?", (symbol, resolution))
        return int(count or 0)

    def day_status(self, symbol: str, resolution: str, day: str | date) -> str:
        if not self.path.exists():
            return "MISSING"
        value = day.isoformat() if isinstance(day, date) else str(day)[:10]
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT status FROM replay_days WHERE symbol=? AND resolution=? AND day=?",
                (str(symbol).upper(), normalize_resolution(resolution), value),
            ).fetchone()
        return str(row[0]) if row else "MISSING"

    def load_day(
        self,
        symbol: str,
        day: str | date,
        *,
        resolution: str | None = None,
        complete_only: bool = True,
    ) -> tuple[list[dict[str, Any]], str, str]:
        if not self.path.exists():
            return [], "", "MISSING"
        day_text = day.isoformat() if isinstance(day, date) else str(day)[:10]
        symbol = str(symbol).strip().upper()
        with closing(self._connect()) as connection, connection:
            if resolution:
                candidates = [normalize_resolution(resolution)]
            else:
                candidates = [row[0] for row in connection.execute(
                    "SELECT resolution FROM replay_days WHERE symbol=? AND day=? ORDER BY resolution",
                    (symbol, day_text),
                )]
            candidates = sorted(set(filter(None, candidates)), key=resolution_minutes)
            for candidate in candidates:
                status_row = connection.execute(
                    "SELECT status,first_timestamp,last_timestamp FROM replay_days "
                    "WHERE symbol=? AND resolution=? AND day=?",
                    (symbol, candidate, day_text),
                ).fetchone()
                if not status_row:
                    continue
                status = str(status_row["status"])
                if complete_only and status != "FULL":
                    continue
                rows = [dict(row) for row in connection.execute(
                    "SELECT timestamp AS time,open,high,low,close,volume FROM replay_bars "
                    "WHERE symbol=? AND resolution=? AND timestamp BETWEEN ? AND ? ORDER BY timestamp",
                    (symbol, candidate, int(status_row["first_timestamp"]), int(status_row["last_timestamp"])),
                )]
                for row in rows:
                    row["closed"] = True
                return rows, candidate, status
        return [], "", "MISSING"
