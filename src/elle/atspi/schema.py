"""SQLite schema for the UI Recipe database.

Provides database initialization and connection management
for storing UI recipes, elements, executions, and versions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


# Default database path
DEFAULT_DB_PATH = Path("/var/lib/elle/recipes.db")

# Schema version for migrations
SCHEMA_VERSION = 1


# =============================================================================
# Schema Definition
# =============================================================================

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Core recipe storage
CREATE TABLE IF NOT EXISTS recipes (
    recipe_id TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    app_desktop_id TEXT,
    app_version TEXT,
    root_window TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    learn_method TEXT NOT NULL DEFAULT 'passive',
    version INTEGER DEFAULT 1,
    confidence REAL DEFAULT 1.0,
    last_success_at TEXT,
    failure_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    elements_json TEXT NOT NULL DEFAULT '[]',
    navigation_paths_json TEXT DEFAULT '{}'
);

-- Index for fast app lookup
CREATE INDEX IF NOT EXISTS idx_recipes_app ON recipes(app_name);

-- Index for confidence-based ordering
CREATE INDEX IF NOT EXISTS idx_recipes_confidence ON recipes(confidence DESC);

-- Index for desktop ID lookup
CREATE INDEX IF NOT EXISTS idx_recipes_desktop_id ON recipes(app_desktop_id);

-- Element index for fast full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS recipe_elements USING fts5(
    recipe_id,
    element_name,
    element_role,
    element_path,
    tokenize='porter unicode61'
);

-- Execution history (links to incidents.db)
CREATE TABLE IF NOT EXISTS recipe_executions (
    execution_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL,
    incident_id TEXT,
    task_request TEXT NOT NULL,
    actions_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT DEFAULT 'pending',
    adaptation_notes TEXT,
    FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id) ON DELETE CASCADE
);

-- Index for recipe execution lookup
CREATE INDEX IF NOT EXISTS idx_executions_recipe ON recipe_executions(recipe_id);

-- Index for incident linking
CREATE INDEX IF NOT EXISTS idx_executions_incident ON recipe_executions(incident_id);

-- Recipe versions for rollback
CREATE TABLE IF NOT EXISTS recipe_versions (
    version_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    elements_json TEXT NOT NULL,
    navigation_paths_json TEXT,
    change_reason TEXT DEFAULT '',
    FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id) ON DELETE CASCADE
);

-- Index for version lookup
CREATE INDEX IF NOT EXISTS idx_versions_recipe ON recipe_versions(recipe_id, version DESC);

-- Named navigation targets (frequently used paths)
CREATE TABLE IF NOT EXISTS navigation_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    element_path TEXT NOT NULL,
    element_role TEXT,
    element_name TEXT,
    usage_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id) ON DELETE CASCADE,
    UNIQUE(recipe_id, target_name)
);

-- Index for target lookup
CREATE INDEX IF NOT EXISTS idx_targets_recipe ON navigation_targets(recipe_id);
"""


# =============================================================================
# Connection Management
# =============================================================================


_connection: sqlite3.Connection | None = None


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a database connection.

    Creates a new connection with row factory enabled.
    Caller is responsible for closing the connection.

    Args:
        db_path: Path to database file. Uses default if not provided.

    Returns:
        SQLite connection with row factory.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    path = Path(db_path)

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def get_shared_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get the shared database connection.

    Creates a connection if needed, reuses existing one otherwise.
    Use this for read operations to avoid connection overhead.

    Args:
        db_path: Path to database file. Uses default if not provided.

    Returns:
        SQLite connection with row factory.
    """
    global _connection

    if _connection is None:
        _connection = get_connection(db_path)
        ensure_schema(_connection)

    return _connection


def close_shared_connection() -> None:
    """Close the shared database connection."""
    global _connection

    if _connection is not None:
        _connection.close()
        _connection = None


# =============================================================================
# Schema Management
# =============================================================================


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the database schema is up to date.

    Creates tables if they don't exist and runs
    any pending migrations.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Check if schema_version table exists
    cursor.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='schema_version'
        """
    )

    if cursor.fetchone() is None:
        # Fresh database, create schema
        _create_schema(conn)
    else:
        # Check version and migrate if needed
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        current_version = row[0] if row and row[0] else 0

        if current_version < SCHEMA_VERSION:
            _migrate_schema(conn, current_version, SCHEMA_VERSION)


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create the database schema from scratch.

    Args:
        conn: SQLite connection.
    """
    from datetime import datetime

    cursor = conn.cursor()

    # Execute schema SQL
    cursor.executescript(SCHEMA_SQL)

    # Record schema version
    cursor.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, datetime.utcnow().isoformat()),
    )

    conn.commit()


def _migrate_schema(
    conn: sqlite3.Connection,
    from_version: int,
    to_version: int,
) -> None:
    """Migrate the database schema.

    Args:
        conn: SQLite connection.
        from_version: Current schema version.
        to_version: Target schema version.
    """
    from datetime import datetime

    cursor = conn.cursor()

    # Apply migrations in order
    for version in range(from_version + 1, to_version + 1):
        migration_sql = _get_migration_sql(version)
        if migration_sql:
            cursor.executescript(migration_sql)

        # Record migration
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, datetime.utcnow().isoformat()),
        )

    conn.commit()


def _get_migration_sql(version: int) -> str | None:
    """Get migration SQL for a specific version.

    Args:
        version: Target version.

    Returns:
        SQL to migrate to this version, or None if no migration needed.
    """
    migrations = {
        # Future migrations go here
        # 2: "ALTER TABLE recipes ADD COLUMN new_field TEXT;",
    }

    return migrations.get(version)


# =============================================================================
# Database Utilities
# =============================================================================


def get_db_stats(conn: sqlite3.Connection | None = None) -> dict:
    """Get database statistics.

    Args:
        conn: SQLite connection. Uses shared connection if not provided.

    Returns:
        Dictionary with stats: recipe_count, element_count, execution_count, etc.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_shared_connection()

    try:
        cursor = conn.cursor()

        stats = {}

        # Recipe count
        cursor.execute("SELECT COUNT(*) FROM recipes")
        stats["recipe_count"] = cursor.fetchone()[0]

        # Execution count
        cursor.execute("SELECT COUNT(*) FROM recipe_executions")
        stats["execution_count"] = cursor.fetchone()[0]

        # Version count
        cursor.execute("SELECT COUNT(*) FROM recipe_versions")
        stats["version_count"] = cursor.fetchone()[0]

        # Navigation target count
        cursor.execute("SELECT COUNT(*) FROM navigation_targets")
        stats["target_count"] = cursor.fetchone()[0]

        # Schema version
        cursor.execute("SELECT MAX(version) FROM schema_version")
        stats["schema_version"] = cursor.fetchone()[0]

        # Database size
        cursor.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
        stats["db_size_bytes"] = cursor.fetchone()[0]

        return stats

    finally:
        if own_conn:
            pass  # Don't close shared connection


def vacuum_db(conn: sqlite3.Connection | None = None) -> None:
    """Vacuum the database to reclaim space.

    Args:
        conn: SQLite connection.
    """
    if conn is None:
        conn = get_shared_connection()

    conn.execute("VACUUM")
