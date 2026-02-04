"""Database schema for dependency preferences and installation history.

Provides PostgreSQL schema initialization for the dependency system.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import psycopg

from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

# Schema version
SCHEMA_VERSION = 1

# PostgreSQL schema name
PG_SCHEMA = "deps"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

PREFERENCES_TABLE = """
CREATE TABLE IF NOT EXISTS dependency_preferences (
    dependency TEXT PRIMARY KEY,
    preference TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

INSTALLATION_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS installation_history (
    id TEXT PRIMARY KEY,
    dependency TEXT NOT NULL,
    packages_json JSONB NOT NULL,
    success BOOLEAN NOT NULL,
    error_message TEXT,
    installed_at TIMESTAMPTZ NOT NULL,
    duration_sec REAL
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_history_dependency ON installation_history(dependency)",
    "CREATE INDEX IF NOT EXISTS idx_history_installed_at ON installation_history(installed_at)",
]


# ---------------------------------------------------------------------------
# Migration v0 -> v1: Initial schema
# ---------------------------------------------------------------------------


def _migrate_to_v1(conn: psycopg.Connection) -> None:
    """Create the initial deps schema."""
    conn.execute(PREFERENCES_TABLE)
    conn.execute(INSTALLATION_HISTORY_TABLE)
    for idx in INDEXES:
        conn.execute(idx)


register_migration(PG_SCHEMA, 1, _migrate_to_v1)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection) -> None:
    """Ensure the deps schema is up to date.

    Args:
        conn: Active psycopg connection.
    """
    from elle.storage.migrate import run_migrations

    run_migrations(conn, PG_SCHEMA)


def prune_old_installation_history(conn: psycopg.Connection, retention_days: int = 90) -> int:
    """Delete installation history records older than *retention_days*.

    Args:
        conn: Active psycopg connection (caller manages commit).
        retention_days: Keep records newer than this many days.

    Returns:
        Number of records deleted.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    result = conn.execute("DELETE FROM installation_history WHERE installed_at < %s", (cutoff,))
    deleted = result.rowcount or 0
    if deleted:
        logger.info("Pruned %d installation history records older than %d days", deleted, retention_days)
    return deleted
