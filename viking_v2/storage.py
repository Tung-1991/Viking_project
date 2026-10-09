from __future__ import annotations

import csv
from collections import deque
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Iterable
from zoneinfo import ZoneInfo


_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}
_REPLACE_RETRY_DELAYS = (0.005, 0.01, 0.02, 0.04, 0.08, 0.16, 0.25)
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _store_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _replace_with_retry(source: Path, target: Path) -> None:
    """Tolerate short Windows locks without hiding persistent disk errors."""
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {
                5, 32, 33,
            }
            if not retryable or attempt >= len(_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def _timestamp_month(value: Any) -> str:
    """Return YYYY-MM for ISO strings or epoch timestamps."""
    text = str(value or "").strip()
    if len(text) >= 7 and text[4] == "-" and text[7:8] in {"", "-", "T", " "}:
        return text[:7]
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m")
    except (TypeError, ValueError, OSError):
        return datetime.now().strftime("%Y-%m")


def _rewrite_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class MonthlyExcelArchive:
    """Small monthly workbooks keep the permanent audit trail bounded."""

    def __init__(self, source_path: str | Path, fields: tuple[str, ...], sheet: str):
        source = Path(source_path)
        self.root = source.parent / "excel_archive"
        self.prefix = source.stem
        self.fields = fields
        self.sheet = sheet
        self.last_error = ""

    @property
    def marker_path(self) -> Path:
        return self.root / f".{self.prefix}_bootstrapped"

    def path_for(self, timestamp: Any) -> Path:
        return self.root / f"{self.prefix}_{_timestamp_month(timestamp)}.xlsx"

    def append(self, row: dict[str, Any]) -> bool:
        return self._append_rows(self.path_for(row.get("timestamp")), [row])

    def _append_rows(self, target: Path, rows: list[dict[str, Any]]) -> bool:
        try:
            from openpyxl import Workbook, load_workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            self.last_error = str(exc)
            return False

        lock = _store_lock(target)
        with lock:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    book = load_workbook(target)
                    sheet = book[self.sheet] if self.sheet in book.sheetnames else book.active
                    existing_headers = tuple(
                        str(cell.value or "") for cell in sheet[1]
                    )
                    if existing_headers != self.fields:
                        if existing_headers != self.fields[:len(existing_headers)]:
                            raise ValueError("Excel archive schema does not match current fields")
                        header_fill = PatternFill("solid", fgColor="1B1F25")
                        for index, field in enumerate(
                            self.fields[len(existing_headers):], start=len(existing_headers) + 1,
                        ):
                            cell = sheet.cell(row=1, column=index, value=field)
                            cell.fill = header_fill
                            cell.font = Font(color="FFFFFF", bold=True)
                            sheet.column_dimensions[get_column_letter(index)].width = min(
                                32, max(12, len(field) + 2),
                            )
                else:
                    book = Workbook()
                    sheet = book.active
                    sheet.title = self.sheet
                    sheet.append(list(self.fields))
                    sheet.freeze_panes = "A2"
                    header_fill = PatternFill("solid", fgColor="1B1F25")
                    for index, cell in enumerate(sheet[1], start=1):
                        cell.fill = header_fill
                        cell.font = Font(color="FFFFFF", bold=True)
                        sheet.column_dimensions[get_column_letter(index)].width = min(
                            32, max(12, len(str(cell.value or "")) + 2),
                        )
                existing = {
                    tuple("" if cell.value is None else str(cell.value) for cell in excel_row)
                    for excel_row in sheet.iter_rows(min_row=2)
                }
                for row in rows:
                    values = tuple(row.get(field, "") for field in self.fields)
                    signature = tuple("" if value is None else str(value) for value in values)
                    if signature not in existing:
                        sheet.append(list(values))
                        existing.add(signature)
                sheet.auto_filter.ref = sheet.dimensions
                temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.tmp.xlsx")
                try:
                    book.save(temporary)
                    _replace_with_retry(temporary, target)
                finally:
                    book.close()
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
                self.last_error = ""
                return True
            except (OSError, ValueError) as exc:
                # The CSV remains authoritative if Excel is temporarily open
                # or locked; trading must never stop because of an archive.
                self.last_error = str(exc)
                return False

    def bootstrap_csv(self, source: Path, keep_rows: int) -> int:
        """Archive legacy CSV once, then keep only a small recent working set."""
        marker = self.marker_path
        with _store_lock(marker):
            try:
                if not source.exists() or source.stat().st_size == 0:
                    rows: list[dict[str, Any]] = []
                else:
                    with source.open("r", encoding="utf-8-sig", newline="") as handle:
                        rows = list(csv.DictReader(handle))
            except (OSError, csv.Error):
                return 0
            if marker.exists():
                return len(rows)

            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(_timestamp_month(row.get("timestamp")), []).append(row)
            if not all(
                self._append_rows(self.root / f"{self.prefix}_{month}.xlsx", month_rows)
                for month, month_rows in grouped.items()
            ):
                return len(rows)

            recent = rows[-keep_rows:] if keep_rows > 0 else rows
            if rows:
                _rewrite_csv(source, self.fields, recent)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
            return len(recent)

    def invalidate_bootstrap(self) -> None:
        try:
            self.marker_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_recent_single_line_csv(path: Path, limit: int) -> list[dict[str, Any]]:
    """Read only the tail of a single-line CSV instead of scanning years."""
    if limit <= 0:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    with path.open("rb") as handle:
        header_bytes = handle.readline()
        data_start = handle.tell()
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        chunks: list[bytes] = []
        line_count = 0
        while position > data_start and line_count <= limit:
            size = min(8192, position - data_start)
            position -= size
            handle.seek(position)
            chunk = handle.read(size)
            chunks.append(chunk)
            line_count += chunk.count(b"\n")
        tail = b"".join(reversed(chunks)).splitlines()[-limit:]
    header = next(csv.reader([header_bytes.decode("utf-8-sig").strip()]))
    return list(csv.DictReader(
        (line.decode("utf-8") for line in tail if line.strip()),
        fieldnames=header,
    ))


class AtomicJSONStore:
    def __init__(self, path: str | Path, default: Any = None):
        self.path = Path(path)
        self.default = default
        self._lock = _store_lock(self.path)

    def read(self) -> Any:
        with self._lock:
            try:
                with self.path.open("r", encoding="utf-8-sig") as handle:
                    return json.load(handle)
            except (OSError, ValueError, TypeError):
                if callable(self.default):
                    return self.default()
                return json.loads(json.dumps(self.default)) if self.default is not None else None

    def write(self, value: Any) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(tmp, self.path)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass


class JSONLineJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def append(self, value: dict[str, Any]) -> None:
        with self._lock:
            if value.get("event_id") and any(row.get("event_id") == value["event_id"] for row in self.read_all(limit=1000)):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")

    def read_all(self, limit: int = 0) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        except (OSError, ValueError):
            return []
        return rows[-limit:] if limit > 0 else rows


class SignalLog:
    """Every change of BUY/SELL signal, whether or not the bot could act.

    The bot only trades five symbols at a time, so most signals never become
    orders and leave no trace anywhere else.  This is the record used to judge
    the rule itself rather than the bot's ability to act on it.
    """

    FIELDS = (
        "timestamp", "execution_mode", "symbol", "signal", "price", "ema_fast", "ema_slow",
        "rsi", "market_state", "acted", "blocked_by", "exchange",
        "candle_key", "signal_cycle", "signal_time", "decision_time", "confirmation_state",
        "confirmation_minutes", "confirmation_required",
        "confirmation_ema", "confirmation_rsi",
        "buy_window", "buy_window_state", "watchlist_priority", "slot_usage",
        "trade_id", "indicator_exit_policy", "protect_mode", "protect_state", "mfe_pct", "peak_price",
        "effective_trail_pct", "atr_pct", "atr_multiplier", "protect_price",
        "sell_pct", "hypothetical_quantity",
        "rsi_previous", "rsi_previous_date", "ema_fast_period", "ema_slow_period",
        "rsi_period", "price_source", "indicator_source", "record_kind",
    )
    RECENT_CSV_ROWS = 500

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.state = AtomicJSONStore(
            self.path.with_name(f"{self.path.stem}_state.json"), default={}
        )
        raw_state = self.state.read()
        self._last: dict[str, str] = {
            str(symbol): str(value)
            for symbol, value in (raw_state.items() if isinstance(raw_state, dict) else [])
        }
        self._ensure_schema()
        self.excel_archive = MonthlyExcelArchive(self.path, self.FIELDS, "TÍN HIỆU")
        self._recent_count: int | None = None

    def record(self, row: dict[str, Any]) -> bool:
        """Write once per symbol/signal/candle, including across daemon restarts."""
        symbol = str(row.get("symbol", "") or "")
        signal = str(row.get("signal", "") or "")
        candle_key = str(row.get("candle_key", "") or "")
        signal_cycle = str(row.get("signal_cycle", "") or "")
        confirmation_state = str(row.get("confirmation_state", "") or "")
        confirmation_minutes = str(row.get("confirmation_minutes", "") or "")
        state_symbol = "|".join((
            str(row.get("execution_mode", "") or "").upper(), symbol,
        ))
        state_key = "|".join(value for value in (
            signal, signal_cycle or candle_key, confirmation_state, confirmation_minutes,
            str(row.get("buy_window_state", "") or ""),
            str(row.get("acted", "") or ""),
            str(row.get("blocked_by", "") or ""),
        ) if value)
        with self._lock:
            if not symbol or self._last.get(state_symbol) == state_key:
                return False
            self._last[state_symbol] = state_key
            self.state.write(self._last)
            if not signal:
                return False
            if self._recent_count is None:
                self._recent_count = self.excel_archive.bootstrap_csv(
                    self.path, self.RECENT_CSV_ROWS,
                )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.path.exists() or self.path.stat().st_size == 0
            saved_row = {key: row.get(key, "") for key in self.FIELDS}
            with self.path.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDS, extrasaction="ignore")
                if new_file:
                    writer.writeheader()
                writer.writerow(saved_row)
            self._recent_count += 1
            archived = self.excel_archive.append(saved_row)
            if archived and self._recent_count > self.RECENT_CSV_ROWS:
                recent = self.read_all(limit=self.RECENT_CSV_ROWS)
                _rewrite_csv(self.path, self.FIELDS, recent)
                self._recent_count = len(recent)
            elif not archived:
                self.excel_archive.invalidate_bootstrap()
        return True

    def _ensure_schema(self) -> None:
        """Upgrade the short recent CSV when new audit columns are introduced."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) == self.FIELDS:
                    return
                rows = list(reader)
            _rewrite_csv(
                self.path, self.FIELDS,
                [{key: row.get(key, "") for key in self.FIELDS} for row in rows],
            )
        except (OSError, UnicodeError, csv.Error):
            return

    def read_all(self, limit: int = 0) -> list[dict[str, Any]]:
        try:
            return _read_recent_single_line_csv(self.path, limit)
        except (OSError, ValueError, UnicodeError, csv.Error):
            return []


class CSVOrderJournal:
    """Append-only flat history mirror for independent UI/report consumption."""

    FIELDS = (
        "timestamp", "execution_mode", "cache_id", "broker_order_id", "trade_id",
        "symbol", "side", "action", "order_type", "limit_price", "quantity",
        "filled_quantity", "remaining_quantity", "source", "queue_status",
        "broker_status", "fee", "tax", "message", "error", "event_id",
    )
    RECENT_CSV_ROWS = 1000

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        SignalLog._ensure_schema(self)
        self.excel_archive = MonthlyExcelArchive(self.path, self.FIELDS, "LỆNH")
        self._recent_count: int | None = None

    def append_event(self, event: dict[str, Any]) -> None:
        intent = event.get("intent") if isinstance(event.get("intent"), dict) else {}
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        row = {
            "event_id": event.get("event_id", ""),
            "timestamp": event.get("ts", ""),
            "execution_mode": intent.get("execution_mode", ""),
            "cache_id": intent.get("id", ""),
            "broker_order_id": result.get("order_id", ""),
            "trade_id": intent.get("trade_id", ""),
            "symbol": intent.get("symbol", ""),
            "side": intent.get("side", ""),
            "action": intent.get("action", ""),
            "order_type": intent.get("order_type", ""),
            "limit_price": intent.get("limit_price", 0),
            "quantity": intent.get("quantity", 0),
            "filled_quantity": intent.get("filled_quantity", 0),
            "remaining_quantity": intent.get("remaining_quantity", 0),
            "source": intent.get("source", ""),
            "queue_status": event.get("queue_status", ""),
            "broker_status": result.get("status", ""),
            "fee": body.get("fee", body.get("totalFee", 0)) if isinstance(body, dict) else 0,
            "tax": body.get("tax", 0) if isinstance(body, dict) else 0,
            "message": result.get("message", ""),
            "error": result.get("error", ""),
        }
        with self._lock:
            if row["event_id"] and any(old.get("event_id") == row["event_id"] for old in self.read_all(limit=1000)):
                return
            if self._recent_count is None:
                self._recent_count = self.excel_archive.bootstrap_csv(
                    self.path, self.RECENT_CSV_ROWS,
                )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDS, extrasaction="ignore")
                if needs_header:
                    writer.writeheader()
                writer.writerow(row)
            self._recent_count += 1
            archived = self.excel_archive.append(row)
            if archived and self._recent_count > self.RECENT_CSV_ROWS:
                recent = self.read_all(limit=self.RECENT_CSV_ROWS)
                _rewrite_csv(self.path, self.FIELDS, recent)
                self._recent_count = len(recent)
            elif not archived:
                self.excel_archive.invalidate_bootstrap()

    def read_all(self, limit: int = 0) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader) if limit <= 0 else list(deque(reader, maxlen=limit))
        except (OSError, csv.Error):
            return []
        return rows


class DailyFeeTracker:
    """Shared dashboard PNL/fee counter backed by immutable audit data.

    DAILY mode rolls over at the configured Vietnam time. SINCE_RESET mode
    keeps accumulating. Scheduled/manual rollovers archive one summary and
    store a shared per-mode cutoff; audit journals and trade cycles stay intact.
    """

    def __init__(self, history_path: str | Path, state_path: str | Path):
        self.history = CSVOrderJournal(history_path)
        self.state = AtomicJSONStore(state_path, default={})
        self._lock = threading.RLock()

    @staticmethod
    def _timestamp(raw: Any) -> float:
        try:
            return float(raw or 0.0)
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                return 0.0

    @staticmethod
    def normalize_reset_time(value: Any) -> str:
        text = str(value or "00:00").strip()
        try:
            parsed = datetime.strptime(text, "%H:%M")
        except ValueError:
            return "00:00"
        return parsed.strftime("%H:%M")

    @classmethod
    def _period_start(cls, now: float, reset_time: str) -> float:
        current = datetime.fromtimestamp(now, VN_TZ)
        parsed = datetime.strptime(cls.normalize_reset_time(reset_time), "%H:%M")
        boundary = current.replace(
            hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0,
        )
        if current < boundary:
            boundary -= timedelta(days=1)
        return boundary.timestamp()

    @classmethod
    def _legacy_period_start(
        cls,
        row: dict[str, Any],
        reset_time: str,
        fallback: float,
    ) -> float:
        try:
            saved = datetime.strptime(str(row.get("date") or "")[:10], "%Y-%m-%d")
        except ValueError:
            return fallback
        parsed = datetime.strptime(cls.normalize_reset_time(reset_time), "%H:%M")
        return saved.replace(
            hour=parsed.hour, minute=parsed.minute,
            tzinfo=VN_TZ,
        ).timestamp()

    @staticmethod
    def _cycle_value(cycle: Any, name: str, default: Any = None) -> Any:
        if isinstance(cycle, dict):
            return cycle.get(name, default)
        return getattr(cycle, name, default)

    def _totals_between(
        self,
        mode: str,
        cycles: Iterable[Any],
        start: float,
        end: float,
    ) -> dict[str, float | int]:
        fees = 0.0
        for row in self.history.read_all():
            if str(row.get("execution_mode") or "").upper() != mode:
                continue
            timestamp = self._timestamp(row.get("timestamp"))
            if timestamp <= start or timestamp > end:
                continue
            try:
                signed = str(row.get("message", "")) in {"BROKER_COST_RECONCILED", "BROKER_FILL_RECONCILED"}
                fee = float(row.get("fee", 0.0) or 0.0)
                tax = float(row.get("tax", 0.0) or 0.0)
                if not signed:
                    fee, tax = abs(fee), abs(tax)
            except (TypeError, ValueError):
                continue
            fees += fee + tax

        pnl = 0.0
        closed_trades = 0
        for cycle in cycles:
            if str(self._cycle_value(cycle, "execution_mode", "")).upper() != mode:
                continue
            if str(self._cycle_value(cycle, "status", "")).upper() != "CLOSED":
                continue
            try:
                closed_at = float(self._cycle_value(cycle, "closed_at", 0.0) or 0.0)
                value = float(self._cycle_value(cycle, "net_pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if closed_at <= start or closed_at > end:
                continue
            pnl += value
            closed_trades += 1
        return {"pnl": pnl, "fees": fees, "closed_trades": closed_trades}

    @staticmethod
    def _archive(
        state: dict[str, Any],
        mode: str,
        start: float,
        end: float,
        values: dict[str, float | int],
        trigger: str,
    ) -> None:
        history = state.get("history") if isinstance(state.get("history"), list) else []
        key = f"{mode}|{start:.6f}|{end:.6f}|{trigger}"
        if any(str(item.get("key") or "") == key for item in history if isinstance(item, dict)):
            return
        history.append({
            "key": key,
            "mode": mode,
            "period_start": datetime.fromtimestamp(start, VN_TZ).isoformat(),
            "period_end": datetime.fromtimestamp(end, VN_TZ).isoformat(),
            "pnl": float(values.get("pnl", 0.0) or 0.0),
            "fees": float(values.get("fees", 0.0) or 0.0),
            "closed_trades": int(values.get("closed_trades", 0) or 0),
            "trigger": str(trigger or "SCHEDULED").upper(),
        })
        state["history"] = history[-730:]

    def summary(
        self,
        mode: str,
        cycles: Iterable[Any] = (),
        *,
        daily: bool = True,
        reset_time: str = "00:00",
        now: float | None = None,
    ) -> dict[str, float | int | bool]:
        current = float(now or datetime.now(VN_TZ).timestamp())
        selected_mode = "REAL" if str(mode).upper() == "REAL" else "PAPER"
        values = list(cycles)
        with self._lock:
            state = self.state.read()
            state = state if isinstance(state, dict) else {}
            row = state.get(selected_mode) if isinstance(state.get(selected_mode), dict) else {}
            row = dict(row)
            original_row = dict(row)
            normalized_time = self.normalize_reset_time(reset_time)
            current_period = self._period_start(current, normalized_time)
            stored_period = max(0.0, float(row.get("period_start", 0.0) or 0.0))
            if stored_period <= 0:
                stored_period = self._legacy_period_start(
                    row, normalized_time, current_period,
                )
            reset_at = max(0.0, float(row.get("reset_at", 0.0) or 0.0))
            rolled_over = False

            previous_reset_time = str(row.get("reset_time") or "")
            if previous_reset_time and previous_reset_time != normalized_time:
                # Editing the schedule rebases the current display period; it
                # must not masquerade as a naturally completed daily period.
                stored_period = current_period
                reset_at = 0.0

            if daily and stored_period < current_period:
                period_start = stored_period
                first_cutoff = max(period_start, reset_at)
                while period_start < current_period:
                    period_end = datetime.fromtimestamp(period_start, VN_TZ) + timedelta(days=1)
                    period_end_ts = min(period_end.timestamp(), current_period)
                    cutoff = max(period_start, first_cutoff)
                    archived = self._totals_between(
                        selected_mode, values, cutoff, period_end_ts,
                    )
                    self._archive(
                        state, selected_mode, cutoff, period_end_ts,
                        archived, "SCHEDULED",
                    )
                    period_start = period_end_ts
                    first_cutoff = period_start
                stored_period = current_period
                reset_at = 0.0
                rolled_over = True
            elif stored_period > current_period:
                stored_period = current_period
                reset_at = 0.0

            row.update({
                "date": datetime.fromtimestamp(current_period, VN_TZ).date().isoformat(),
                "period_start": stored_period,
                "reset_at": reset_at,
                "reset_time": normalized_time,
            })
            state[selected_mode] = row
            if row != original_row or rolled_over:
                self.state.write(state)
            cutoff = max(stored_period, reset_at) if daily else reset_at
            result = self._totals_between(selected_mode, values, cutoff, current)
            result["rolled_over"] = rolled_over
            return result

    def total(self, mode: str, now: float | None = None) -> float:
        return float(self.summary(mode, now=now)["fees"])

    def reset(
        self,
        mode: str,
        now: float | None = None,
        *,
        cycles: Iterable[Any] = (),
        daily: bool = True,
        reset_time: str = "00:00",
    ) -> dict[str, float | int | bool]:
        current = float(now or datetime.now(VN_TZ).timestamp())
        selected_mode = "REAL" if str(mode).upper() == "REAL" else "PAPER"
        snapshot = self.summary(
            selected_mode, cycles, daily=daily,
            reset_time=reset_time, now=current,
        )
        with self._lock:
            state = self.state.read()
            state = state if isinstance(state, dict) else {}
            row = state.get(selected_mode) if isinstance(state.get(selected_mode), dict) else {}
            row = dict(row)
            start = (
                max(
                    float(row.get("period_start", 0.0) or 0.0),
                    float(row.get("reset_at", 0.0) or 0.0),
                )
                if daily else float(row.get("reset_at", 0.0) or 0.0)
            )
            self._archive(state, selected_mode, start, current, snapshot, "MANUAL")
            row.update({
                "date": datetime.fromtimestamp(current, VN_TZ).date().isoformat(),
                "period_start": self._period_start(current, reset_time),
                "reset_at": current,
                "reset_time": self.normalize_reset_time(reset_time),
            })
            state[selected_mode] = row
            self.state.write(state)
        return snapshot
