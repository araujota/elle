"""Reactive Functions PostgreSQL schema.

Defines the database schema for reactive functions:
- reactive_functions: Core function definitions
- execution_history: Record of function executions
- function_state: Rate limiting and state tracking

Schema version is tracked in the ``_meta`` table.
"""

from __future__ import annotations

import logging

import psycopg
from psycopg import sql

from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

# Schema version
SCHEMA_VERSION = 1

# PostgreSQL schema name
PG_SCHEMA = "reactive"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

REACTIVE_FUNCTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS reactive_functions (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    created_by TEXT DEFAULT 'user',

    -- Core configuration (stored as JSONB)
    trigger_json JSONB NOT NULL,
    condition_json JSONB,
    actions_json JSONB NOT NULL,
    policy_json JSONB NOT NULL,
    state_json JSONB,

    -- Metadata
    tags_json JSONB,
    source_prompt TEXT
)
"""

EXECUTION_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS execution_history (
    id TEXT PRIMARY KEY,
    function_id TEXT NOT NULL,
    function_name TEXT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL,

    -- Trigger context
    trigger_event_json JSONB,

    -- Condition evaluation
    condition_result BOOLEAN NOT NULL,
    condition_explanation TEXT NOT NULL DEFAULT '',

    -- Action execution
    actions_executed_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    actions_results_json JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Outcome
    success BOOLEAN NOT NULL,
    error TEXT,
    execution_time_ms INTEGER NOT NULL DEFAULT 0,

    -- Incident integration
    incident_id TEXT,

    FOREIGN KEY (function_id) REFERENCES reactive_functions(id) ON DELETE CASCADE
)
"""

FUNCTION_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS function_state (
    function_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json JSONB,
    updated_at TIMESTAMPTZ,
    PRIMARY KEY (function_id, key),
    FOREIGN KEY (function_id) REFERENCES reactive_functions(id) ON DELETE CASCADE
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_functions_enabled ON reactive_functions(enabled)",
    "CREATE INDEX IF NOT EXISTS idx_functions_name ON reactive_functions(name)",
    "CREATE INDEX IF NOT EXISTS idx_functions_created ON reactive_functions(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_function ON execution_history(function_id)",
    "CREATE INDEX IF NOT EXISTS idx_executions_time ON execution_history(triggered_at)",
    "CREATE INDEX IF NOT EXISTS idx_executions_success ON execution_history(success)",
    "CREATE INDEX IF NOT EXISTS idx_state_function ON function_state(function_id)",
]


# ---------------------------------------------------------------------------
# Migration v0 -> v1: Initial schema
# ---------------------------------------------------------------------------


def _migrate_to_v1(conn: psycopg.Connection) -> None:
    """Create the initial reactive schema."""
    conn.execute(REACTIVE_FUNCTIONS_TABLE)
    conn.execute(EXECUTION_HISTORY_TABLE)
    conn.execute(FUNCTION_STATE_TABLE)
    for idx in INDEXES:
        conn.execute(idx)


register_migration(PG_SCHEMA, 1, _migrate_to_v1)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection) -> None:
    """Ensure the reactive schema is up to date.

    Args:
        conn: Active psycopg connection.
    """
    from elle.storage.migrate import run_migrations

    run_migrations(conn, PG_SCHEMA)


def drop_all_tables(conn: psycopg.Connection) -> None:
    """Drop all Reactive Functions tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: Active psycopg connection.
    """
    tables = ["function_state", "execution_history", "reactive_functions", "_meta"]
    for table in tables:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table)))
    conn.commit()
