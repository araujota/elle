"""Database utilities for ELLE.

SQLite databases:
- Main DB: /var/lib/elle/elle.db (telemetry events)
- Man Vault: /var/lib/elle/manvault.db (documentation index)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Main ELLE database path
DB_PATH = Path("/var/lib/elle/elle.db")

# Event table schema
EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    raw TEXT NOT NULL
)
"""

EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the ELLE database.

    Args:
        db_path: Override database path (for testing).

    Returns:
        SQLite connection with row factory set.
    """
    path = db_path or DB_PATH

    # Ensure directory exists for user-local paths
    if not str(path).startswith("/var/lib"):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def ensure_connection(
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """Context manager that ensures a valid connection.

    If conn is provided, uses it directly. Otherwise creates a new connection
    using get_connection and closes it when done.

    Args:
        conn: Existing connection to use (optional).
        db_path: Database path to use if creating a new connection (optional).

    Yields:
        A valid sqlite3.Connection.

    Example:
        # Before (causes union-attr errors):
        def store_event(event, conn=None):
            own = conn is None
            if own:
                conn = get_connection()
            try:
                cursor = conn.cursor()  # Error: conn could be None
                ...
            finally:
                if own:
                    conn.close()

        # After (type-safe):
        def store_event(event, conn=None):
            with ensure_connection(conn) as c:
                cursor = c.cursor()  # c is always Connection
                ...
    """
    own_conn = conn is None
    if own_conn:
        actual_conn = get_connection(db_path)
    else:
        # conn is definitely not None here due to the check above
        assert conn is not None  # Type narrowing for mypy
        actual_conn = conn
    try:
        yield actual_conn
    finally:
        if own_conn:
            actual_conn.close()


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Initialize the database schema.

    Creates:
    - events table for telemetry

    Args:
        conn: SQLite connection. Creates new if not provided.
    """
    with ensure_connection(conn) as c:
        cursor = c.cursor()
        cursor.execute(EVENTS_SCHEMA)
        cursor.execute(EVENTS_INDEX)
        c.commit()


def init_all_schemas() -> None:
    """Initialize all database schemas.

    Initializes:
    - Main ELLE database (telemetry events)
    - Man Vault database (documentation index)
    - Incident Vault database (incident reports)

    Safe to call multiple times.
    """
    # Initialize main database
    init_db()

    # Initialize Man Vault database
    from elle.daemon.manvault.schema import ensure_schema as ensure_manvault
    from elle.daemon.manvault.schema import get_connection as get_manvault_conn

    manvault_conn = get_manvault_conn()
    try:
        ensure_manvault(manvault_conn)
    finally:
        manvault_conn.close()

    # Initialize Incident Vault database
    from elle.daemon.incidents.schema import ensure_schema as ensure_incidents
    from elle.daemon.incidents.schema import get_connection as get_incidents_conn

    incidents_conn = get_incidents_conn()
    try:
        ensure_incidents(incidents_conn)
    finally:
        incidents_conn.close()
