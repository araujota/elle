"""PostgreSQL schema for telemetry event storage.

Defines tables and indexes for efficient event storage,
querying, and correlation.  Uses tsvector for full-text search
instead of SQLite FTS5.
"""

from __future__ import annotations

import logging

import psycopg

from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

# Schema version for migrations
SCHEMA_VERSION = 1

# PostgreSQL schema name
PG_SCHEMA = "telemetry"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    entity TEXT,
    fingerprint TEXT,
    raw TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', message)
    ) STORED
)
"""

EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_category ON events(category)",
    "CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity)",
    "CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity)",
    "CREATE INDEX IF NOT EXISTS idx_events_fingerprint ON events(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)",
    "CREATE INDEX IF NOT EXISTS idx_events_category_ts ON events(category, ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_message_tsv ON events USING GIN(message_tsv)",
]

PROBE_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS probe_results (
    id BIGSERIAL PRIMARY KEY,
    probe_name TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    success BOOLEAN NOT NULL,
    data TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

PROBE_RESULTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_probe_results_name_ts ON probe_results(probe_name, ts)",
]

FORECAST_MODEL_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS forecast_model_cache (
    metric TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    parameters TEXT,
    mape REAL,
    cached_at TIMESTAMPTZ NOT NULL
)
"""

CORRELATION_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS correlation_alerts (
    id BIGSERIAL PRIMARY KEY,
    signature TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    contributing_metrics TEXT,
    detected_at TIMESTAMPTZ NOT NULL
)
"""

HARDWARE_PROFILES_TABLE = """
CREATE TABLE IF NOT EXISTS hardware_profiles (
    id BIGSERIAL PRIMARY KEY,
    profile_hash TEXT NOT NULL,
    cpu_count INTEGER NOT NULL,
    ram_total_mb INTEGER NOT NULL,
    disk_info TEXT,
    gpu_count INTEGER DEFAULT 0,
    detected_at TIMESTAMPTZ NOT NULL
)
"""


# ---------------------------------------------------------------------------
# Migration v0 -> v1: Initial schema
# ---------------------------------------------------------------------------


def _migrate_to_v1(conn: psycopg.Connection) -> None:
    """Create the initial telemetry schema."""
    conn.execute(EVENTS_TABLE)
    for idx in EVENTS_INDEXES:
        conn.execute(idx)
    conn.execute(PROBE_RESULTS_TABLE)
    for idx in PROBE_RESULTS_INDEXES:
        conn.execute(idx)
    conn.execute(FORECAST_MODEL_CACHE_TABLE)
    conn.execute(CORRELATION_ALERTS_TABLE)
    conn.execute(HARDWARE_PROFILES_TABLE)


register_migration(PG_SCHEMA, 1, _migrate_to_v1)


# ---------------------------------------------------------------------------
# Convenience helpers (for backward compatibility during migration)
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection) -> None:
    """Ensure the telemetry schema is up to date.

    Args:
        conn: Active psycopg connection (search_path should be set
              to 'telemetry').
    """
    from elle.storage.migrate import run_migrations

    run_migrations(conn, PG_SCHEMA)


def get_table_stats(conn: psycopg.Connection) -> dict[str, int]:
    """Get row counts for telemetry tables.

    Args:
        conn: Active psycopg connection.

    Returns:
        Dict mapping table name to row count.
    """
    stats: dict[str, int] = {}
    for table in ["events", "probe_results"]:
        try:
            row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()  # noqa: S608  # nosec B608
            stats[table] = row["cnt"] if row else 0
        except Exception:
            stats[table] = 0
    return stats
