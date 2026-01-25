"""Database schema for dependency preferences and installation history.

Provides SQLite schema initialization for the dependency system.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Schema version for migrations
SCHEMA_VERSION = 1

# Default database path
DEFAULT_DB_PATH = Path("/var/lib/elle/dependencies.db")


# =============================================================================
# Table Definitions
# =============================================================================

PREFERENCES_TABLE = """
CREATE TABLE IF NOT EXISTS dependency_preferences (
    dependency TEXT PRIMARY KEY,
    preference TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

INSTALLATION_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS installation_history (
    id TEXT PRIMARY KEY,
    dependency TEXT NOT NULL,
    packages_json TEXT NOT NULL,
    success INTEGER NOT NULL,
    error_message TEXT,
    installed_at TEXT NOT NULL,
    duration_sec REAL
)
"""

META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_history_dependency ON installation_history(dependency)",
    "CREATE INDEX IF NOT EXISTS idx_history_installed_at ON installation_history(installed_at)",
]


# =============================================================================
# Database Connection
# =============================================================================


def get_db_path() -> Path:
    """Get the dependency store database path.

    Returns:
        Path to the database file.
    """
    return DEFAULT_DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the dependency store database.

    Creates the database directory if it doesn't exist (for non-system paths).

    Args:
        db_path: Override database path (for testing).

    Returns:
        SQLite connection with row factory set.
    """
    path = db_path or get_db_path()

    # For non-system paths, ensure directory exists
    if not str(path).startswith("/var/lib"):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize the database schema.

    Creates all tables and indexes if they don't exist.
    Safe to call multiple times.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Create tables
    cursor.execute(META_TABLE)
    cursor.execute(PREFERENCES_TABLE)
    cursor.execute(INSTALLATION_HISTORY_TABLE)

    # Create indexes
    for index_sql in INDEXES:
        cursor.execute(index_sql)

    # Set schema version
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )

    conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the schema is initialized.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cursor.fetchone()
        if not row:
            init_schema(conn)
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        init_schema(conn)
