"""Database path, connections, transactions, and startup initialization."""

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from .schema import DEFAULT_CASH, DEFAULT_WATCHLIST, SCHEMA

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_current: ContextVar[sqlite3.Connection | None] = ContextVar("db_connection", default=None)


def get_db_path() -> Path:
    """Return DB_PATH from the environment, or <project_root>/db/finally.db."""
    return Path(os.environ.get("DB_PATH") or PROJECT_ROOT / "db" / "finally.db")


def now_iso() -> str:
    """Return the current time as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    """Return a new UUID string for a primary key."""
    return str(uuid.uuid4())


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Yield a connection; commit on success, roll back on error.

    Nested calls reuse the outer connection, so repository functions called
    inside a transaction() block all commit together.
    """
    outer = _current.get()
    if outer is not None:
        yield outer
        return
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    token = _current.set(conn)
    try:
        with conn:
            yield conn
    finally:
        _current.reset(token)
        conn.close()


def init_db() -> None:
    """Create the schema and seed default data if missing. Idempotent."""
    get_db_path().parent.mkdir(parents=True, exist_ok=True)
    with transaction() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT 1 FROM users_profile WHERE id = 'default'").fetchone():
            return
        conn.execute(
            "INSERT INTO users_profile (id, cash_balance, created_at) VALUES ('default', ?, ?)",
            (DEFAULT_CASH, now_iso()),
        )
        added_at = now_iso()
        conn.executemany(
            "INSERT OR IGNORE INTO watchlist (id, ticker, added_at) VALUES (?, ?, ?)",
            [(new_id(), ticker, added_at) for ticker in DEFAULT_WATCHLIST],
        )
