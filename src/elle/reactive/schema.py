"""Reactive Functions SQLite schema.

Defines the database schema for reactive functions:
- reactive_functions: Core function definitions
- execution_history: Record of function executions
- function_state: Rate limiting and state tracking

Schema version is tracked in the meta table for migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Schema version - increment when schema changes
SCHEMA_VERSION = 1

# Database location
DB_PATH = Path("/var/lib/elle/reactive.db")

# Core reactive functions table
REACTIVE_FUNCTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS reactive_functions (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT DEFAULT 'user',

    -- Core configuration (stored as JSON)
    trigger_json TEXT NOT NULL,
    condition_json TEXT,
    actions_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    state_json TEXT,

    -- Metadata
    tags_json TEXT,
    source_prompt TEXT
)
"""

# Execution history table (append-only log)
EXECUTION_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS execution_history (
    id TEXT PRIMARY KEY,
    function_id TEXT NOT NULL,
    function_name TEXT NOT NULL,
    triggered_at TEXT NOT NULL,

    -- Trigger context
    trigger_event_json TEXT,

    -- Condition evaluation
    condition_result INTEGER NOT NULL,
    condition_explanation TEXT NOT NULL DEFAULT '',

    -- Action execution
    actions_executed_json TEXT NOT NULL DEFAULT '[]',
    actions_results_json TEXT NOT NULL DEFAULT '[]',

    -- Outcome
    success INTEGER NOT NULL,
    error TEXT,
    execution_time_ms INTEGER NOT NULL DEFAULT 0,

    -- Incident integration
    incident_id TEXT,

    FOREIGN KEY (function_id) REFERENCES reactive_functions(id) ON DELETE CASCADE
)
"""

# Function state table (for rate limiting and counters)
FUNCTION_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS function_state (
    function_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT,
    updated_at TEXT,
    PRIMARY KEY (function_id, key),
    FOREIGN KEY (function_id) REFERENCES reactive_functions(id) ON DELETE CASCADE
)
"""

# Meta table for tracking schema state
META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# Indexes for common queries
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_functions_enabled ON reactive_functions(enabled)",
    "CREATE INDEX IF NOT EXISTS idx_functions_name ON reactive_functions(name)",
    "CREATE INDEX IF NOT EXISTS idx_functions_created ON reactive_functions(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_function ON execution_history(function_id)",
    "CREATE INDEX IF NOT EXISTS idx_executions_time ON execution_history(triggered_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_success ON execution_history(success)",
    "CREATE INDEX IF NOT EXISTS idx_state_function ON function_state(function_id)",
]


def init_reactive_schema(conn: sqlite3.Connection) -> None:
    """Initialize the Reactive Functions schema.

    Creates all tables and indexes if they don't exist.
    Safe to call multiple times.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # Create tables
    cursor.execute(REACTIVE_FUNCTIONS_TABLE)
    cursor.execute(EXECUTION_HISTORY_TABLE)
    cursor.execute(FUNCTION_STATE_TABLE)
    cursor.execute(META_TABLE)

    # Create indexes
    for index_sql in INDEXES:
        cursor.execute(index_sql)

    # Set schema version
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )

    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Get the current schema version.

    Args:
        conn: SQLite connection.

    Returns:
        Schema version, or None if not set.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cursor.fetchone()
        if row:
            return int(row[0])
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        pass
    return None


def needs_migration(conn: sqlite3.Connection) -> bool:
    """Check if the schema needs migration.

    Args:
        conn: SQLite connection.

    Returns:
        True if migration is needed.
    """
    version = get_schema_version(conn)
    if version is None:
        return True
    return version < SCHEMA_VERSION


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate the schema to the latest version.

    Args:
        conn: SQLite connection.
    """
    # Currently no migrations needed (v1)
    # Future migrations would go here

    # Update schema version
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def drop_all_tables(conn: sqlite3.Connection) -> None:
    """Drop all Reactive Functions tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Drop tables (order matters due to foreign keys)
    cursor.execute("DROP TABLE IF EXISTS function_state")
    cursor.execute("DROP TABLE IF EXISTS execution_history")
    cursor.execute("DROP TABLE IF EXISTS reactive_functions")
    cursor.execute("DROP TABLE IF EXISTS meta")

    conn.commit()


def get_db_path() -> Path:
    """Get the Reactive Functions database path.

    Returns:
        Path to the Reactive Functions database.
    """
    return DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the Reactive Functions database.

    Creates the database directory if it doesn't exist.

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
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the schema is initialized and up to date.

    Args:
        conn: SQLite connection.
    """
    if needs_migration(conn):
        init_reactive_schema(conn)
