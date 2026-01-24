"""SQLite schema for telemetry event storage.

Defines tables and indexes for efficient event storage,
querying, and correlation.
"""

import sqlite3
from pathlib import Path

# Default database path
DB_PATH = Path("/var/lib/elle/elle.db")


# Schema version for migrations
SCHEMA_VERSION = 1


# Main events table
EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    entity TEXT,
    fingerprint TEXT,
    raw TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# Indexes for common queries
EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_category ON events(category)",
    "CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity)",
    "CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity)",
    "CREATE INDEX IF NOT EXISTS idx_events_fingerprint ON events(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)",
    # Composite index for common queries
    "CREATE INDEX IF NOT EXISTS idx_events_category_ts ON events(category, ts)",
]

# FTS5 table for full-text search on messages
EVENTS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    message,
    content='events',
    content_rowid='id',
    tokenize='porter unicode61'
)
"""

# Triggers to keep FTS in sync
EVENTS_FTS_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
        INSERT INTO events_fts(rowid, message) VALUES (new.id, new.message);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
        INSERT INTO events_fts(events_fts, rowid, message) VALUES('delete', old.id, old.message);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
        INSERT INTO events_fts(events_fts, rowid, message) VALUES('delete', old.id, old.message);
        INSERT INTO events_fts(rowid, message) VALUES (new.id, new.message);
    END
    """,
]

# Probe results table for historical probe data
PROBE_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS probe_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    probe_name TEXT NOT NULL,
    ts TEXT NOT NULL,
    success INTEGER NOT NULL,
    data TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

PROBE_RESULTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_probe_results_name_ts ON probe_results(probe_name, ts)",
]

# Schema version table
VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the telemetry database.

    Args:
        db_path: Override database path (for testing).

    Returns:
        SQLite connection with row factory set.
    """
    path = db_path or DB_PATH

    # Ensure directory exists (for non-system paths)
    if not str(path).startswith("/var/lib"):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Enable foreign keys and WAL mode for better concurrency
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure database schema is up to date.

    Creates tables and indexes if they don't exist.
    Handles migrations for schema updates.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Create version table first
    cursor.execute(VERSION_TABLE)

    # Check current version
    cursor.execute("SELECT MAX(version) FROM schema_version")
    row = cursor.fetchone()
    current_version = row[0] if row[0] is not None else 0

    if current_version < SCHEMA_VERSION:
        # Apply schema
        cursor.execute(EVENTS_TABLE)
        for index in EVENTS_INDEXES:
            cursor.execute(index)

        # FTS table and triggers
        cursor.execute(EVENTS_FTS)
        for trigger in EVENTS_FTS_TRIGGERS:
            cursor.execute(trigger)

        # Probe results
        cursor.execute(PROBE_RESULTS_TABLE)
        for index in PROBE_RESULTS_INDEXES:
            cursor.execute(index)

        # Record version
        cursor.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )

        conn.commit()


def drop_schema(conn: sqlite3.Connection) -> None:
    """Drop all tables (for testing only).

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Drop FTS first (triggers depend on it)
    cursor.execute("DROP TABLE IF EXISTS events_fts")

    # Drop triggers
    cursor.execute("DROP TRIGGER IF EXISTS events_ai")
    cursor.execute("DROP TRIGGER IF EXISTS events_ad")
    cursor.execute("DROP TRIGGER IF EXISTS events_au")

    # Drop main tables
    cursor.execute("DROP TABLE IF EXISTS events")
    cursor.execute("DROP TABLE IF EXISTS probe_results")
    cursor.execute("DROP TABLE IF EXISTS schema_version")

    conn.commit()


def get_table_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get row counts for all tables.

    Args:
        conn: SQLite connection.

    Returns:
        Dict mapping table name to row count.
    """
    cursor = conn.cursor()
    stats = {}

    for table in ["events", "probe_results"]:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            stats[table] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            stats[table] = 0

    return stats


def get_db_size(db_path: Path | None = None) -> int:
    """Get database file size in bytes.

    Args:
        db_path: Database path.

    Returns:
        File size in bytes.
    """
    path = db_path or DB_PATH
    if path.exists():
        return path.stat().st_size
    return 0
