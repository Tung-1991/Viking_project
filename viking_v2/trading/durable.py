"""Small account-local transaction store. Settings and market caches stay JSON.

The existing store interfaces are intentionally retained. Legacy financial JSON
is imported once, backed up, and never treated as an empty account on corruption.
No network operation belongs inside one of these transactions.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Any

from ..branding import APP_NAME


class StateCorruptionError(RuntimeError):
    pass


class AccountDatabase:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._lock.acquire()
        try:
            if not getattr(self._local, "depth", 0):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute("PRAGMA synchronous=FULL")
                    connection.execute("CREATE TABLE IF NOT EXISTS documents (name TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                    connection.execute("CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0)")
                    connection.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0)")
                    connection.execute("CREATE TABLE IF NOT EXISTS broker_owners (id TEXT PRIMARY KEY, intent_id TEXT NOT NULL)")
                    connection.execute("CREATE INDEX IF NOT EXISTS inbox_pending ON inbox(applied)")
                    connection.execute("CREATE INDEX IF NOT EXISTS events_pending ON events(exported)")
                    connection.execute("BEGIN IMMEDIATE")
                except BaseException:
                    connection.close()
                    raise
                self._local.connection = connection
                self._local.failed = False
            self._local.depth = getattr(self._local, "depth", 0) + 1
            return self
        except BaseException:
            self._lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self._local.failed = True
        self._local.depth -= 1
        try:
            if not self._local.depth:
                connection = self._local.connection
                try:
                    connection.execute("ROLLBACK" if self._local.failed else "COMMIT")
                finally:
                    connection.close()
                    del self._local.connection
        finally:
            self._lock.release()
        return False

    @property
    def connection(self) -> sqlite3.Connection:
        return self._local.connection

    def put_result(self, key: str, payload: dict[str, Any]) -> None:
        with self:
            self.connection.execute(
                "INSERT OR IGNORE INTO inbox(id,payload) VALUES (?,?)",
                (key, json.dumps(payload, ensure_ascii=False, allow_nan=False)),
            )

    def pending_results(self) -> list[tuple[str, dict[str, Any]]]:
        with self:
            return [(key, json.loads(payload)) for key, payload in self.connection.execute(
                "SELECT id,payload FROM inbox WHERE applied=0 ORDER BY rowid"
            )]

    def result_applied(self, key: str) -> bool:
        row = self.connection.execute("SELECT applied FROM inbox WHERE id=?", (key,)).fetchone()
        return bool(row and row[0])

    def mark_applied(self, key: str) -> None:
        self.connection.execute("UPDATE inbox SET applied=1 WHERE id=?", (key,))

    def add_event(self, event: dict[str, Any]) -> None:
        self.connection.execute("INSERT OR IGNORE INTO events(id,payload) VALUES (?,?)",
                                (event["event_id"], json.dumps(event, ensure_ascii=False, allow_nan=False)))

    def pending_events(self) -> list[dict[str, Any]]:
        with self:
            return [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM events WHERE exported=0 ORDER BY rowid")]

    def mark_exported(self, event_id: str) -> None:
        with self:
            self.connection.execute("UPDATE events SET exported=1 WHERE id=?", (event_id,))

    def bind_order(self, broker_id: str, intent_id: str) -> None:
        if not broker_id:
            return
        with self:
            existing = self.connection.execute("SELECT intent_id FROM broker_owners WHERE id=?", (broker_id,)).fetchone()
            if existing and existing[0] != intent_id:
                raise StateCorruptionError("Broker order ID belongs to a different intent")
            self.connection.execute("INSERT OR IGNORE INTO broker_owners(id,intent_id) VALUES (?,?)", (broker_id, intent_id))

    def owned_order_ids(self) -> set[str]:
        with self:
            return {row[0] for row in self.connection.execute("SELECT id FROM broker_owners")}


class AccountLease:
    """OS-held lifetime lease, released automatically on a process crash."""
    def __init__(self, directory: Path, name: str = "execution.lock"):
        directory.mkdir(parents=True, exist_ok=True)
        self._handle = (directory / name).open("a+b")
        self._handle.seek(0, 2)
        if not self._handle.tell():
            self._handle.write(b"0")
            self._handle.flush()
        self._handle.seek(0)
        try:
            import os
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            raise RuntimeError(f"Tài khoản đã có một instance {APP_NAME} đang chạy") from exc

    def close(self):
        if not self._handle.closed:
            self._handle.close()


_databases: dict[Path, AccountDatabase] = {}
_databases_lock = threading.Lock()


def account_database(directory: Path) -> AccountDatabase:
    path = (directory / "trading.sqlite3").resolve()
    with _databases_lock:
        return _databases.setdefault(path, AccountDatabase(path))


class DurableJSONStore:
    def __init__(self, path: str | Path, default: Any = None, validator=None):
        self.path = Path(path)
        self.default = default
        self.validator = validator
        self.database = account_database(self.path.parent)
        self.transaction = self.database

    def _default(self):
        return self.default() if callable(self.default) else deepcopy(self.default)

    def _validate(self, value):
        expected = self._default()
        if expected is not None and not isinstance(value, type(expected)):
            raise ValueError("Unexpected financial state structure")
        if isinstance(expected, dict):
            for key, prototype in expected.items():
                if key in value and isinstance(prototype, (dict, list)) and not isinstance(value[key], type(prototype)):
                    raise ValueError(f"Invalid financial state field: {key}")
        json.dumps(value, allow_nan=False)
        return self.validator(value) if self.validator else value

    def _load(self):
        row = self.database.connection.execute(
            "SELECT payload FROM documents WHERE name=?", (self.path.name,)
        ).fetchone()
        if row:
            try:
                value = json.loads(row[0])
                return self._validate(value)
            except (ValueError, TypeError) as exc:
                raise StateCorruptionError(f"Invalid financial state: {self.path.name}") from exc
        value = self._default()
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8-sig") as handle:
                    value = json.load(handle)
                value = self._validate(value)
            except (OSError, ValueError, TypeError) as exc:
                raise StateCorruptionError(f"Cannot import financial state: {self.path.name}") from exc
            backup = self.path.parent / "migration-backup" / self.path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(self.path, backup)
        self.database.connection.execute(
            "INSERT INTO documents(name,payload) VALUES (?,?)",
            (self.path.name, json.dumps(value, ensure_ascii=False, allow_nan=False)),
        )
        return value

    def read(self):
        with self.transaction:
            return self._load()

    def write(self, value):
        with self.transaction:
            self._load()  # Import/validate an existing file before replacing it.
            value = self._validate(value)
            self.database.connection.execute(
                "UPDATE documents SET payload=? WHERE name=?",
                (json.dumps(value, ensure_ascii=False, allow_nan=False), self.path.name),
            )
