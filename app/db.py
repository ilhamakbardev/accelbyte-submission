"""SQLite datastore. Single-process, WAL mode, thread-safe via lock."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_db_path: str | None = None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    global _conn, _db_path
    path = db_path or config.DB_PATH
    with _lock:
        if _conn is None or _db_path != path:
            if _conn is not None:
                _conn.close()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL;")
            _conn.execute("PRAGMA foreign_keys=ON;")
            _conn.execute("PRAGMA busy_timeout=30000;")
            _db_path = path
            init_schema(_conn)
        return _conn


def init_schema(conn: sqlite3.Connection) -> None:
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                url TEXT NOT NULL,
                secret TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','delivered','failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_due
                ON events(status, next_attempt_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_sub
                ON events(subscription_id, status, created_at);
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES events(id),
                attempt_no INTEGER NOT NULL,
                status_code INTEGER,
                success INTEGER NOT NULL,
                error TEXT,
                attempted_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_attempts_event
                ON attempts(event_id, attempt_no);
            """
        )
        conn.commit()


def reset_for_tests(db_path: str) -> sqlite3.Connection:
    """Drop and recreate all tables. Test-only helper."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        p = Path(db_path)
        if p.exists():
            p.unlink()
        # Also clean WAL sidecars
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(db_path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        conn = get_conn(db_path)
        return conn
