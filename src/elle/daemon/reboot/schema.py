"""Reboot Intent PostgreSQL schema.

Defines the database schema for reboot persistence:
- reboot_intents: Core intent records with state machine
- pending_verifications: Post-boot verification checks

Schema version is tracked in the ``_meta`` table.
"""

from __future__ import annotations

import logging

import psycopg

from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

# Schema version
SCHEMA_VERSION = 2

# PostgreSQL schema name
PG_SCHEMA = "reboot"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

INTENTS_TABLE = """
CREATE TABLE IF NOT EXISTS reboot_intents (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,

    -- Intent buffer
    goal TEXT NOT NULL,
    task_description TEXT NOT NULL,

    -- Reboot reason
    reason TEXT NOT NULL,
    reason_detail TEXT,

    -- Session trace
    session_history_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    plan_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- State machine
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'rebooting', 'verifying', 'completed', 'failed', 'rolled_back', 'cancelled')),

    -- Pre-reboot state
    pre_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    boot_id TEXT,

    -- GRUB configuration
    grub_entry TEXT,
    grub_default_saved TEXT,
    grub_state_json JSONB,

    -- Post-reboot results
    post_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    new_boot_id TEXT,
    verification_attempts INTEGER NOT NULL DEFAULT 0,
    last_verification_at TIMESTAMPTZ,

    -- Outcome
    outcome TEXT NOT NULL DEFAULT 'unknown'
        CHECK (outcome IN ('improved', 'failed', 'rolled_back', 'unknown')),
    outcome_detail TEXT
)
"""

VERIFICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_verifications (
    id SERIAL PRIMARY KEY,
    reboot_intent_id TEXT NOT NULL REFERENCES reboot_intents(id) ON DELETE CASCADE,

    -- Check definition
    check_type TEXT NOT NULL
        CHECK (check_type IN (
            'command', 'service_active', 'file_exists', 'port_listening',
            'filesystem_writable', 'mount_exists', 'kernel_module',
            'dmesg_no_errors', 'journal_no_errors', 'disk_space', 'network_interface'
        )),
    check_command TEXT NOT NULL,
    expected_exit_code INTEGER NOT NULL DEFAULT 0,
    expected_output_contains TEXT,

    step_index INTEGER NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,

    -- Results (filled post-boot)
    executed_at TIMESTAMPTZ,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    passed BOOLEAN,

    UNIQUE(reboot_intent_id, step_index)
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_intents_status ON reboot_intents(status)",
    "CREATE INDEX IF NOT EXISTS idx_intents_outcome ON reboot_intents(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_intents_created ON reboot_intents(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_intents_updated ON reboot_intents(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_intents_boot_id ON reboot_intents(boot_id)",
    "CREATE INDEX IF NOT EXISTS idx_intents_incident ON reboot_intents(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_verifications_intent ON pending_verifications(reboot_intent_id)",
    "CREATE INDEX IF NOT EXISTS idx_verifications_passed ON pending_verifications(passed)",
]


# ---------------------------------------------------------------------------
# Migration v0 -> v2: Full schema (includes v2 check types)
# ---------------------------------------------------------------------------


def _migrate_to_v2(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Create the full reboot schema at v2."""
    conn.execute(INTENTS_TABLE)
    conn.execute(VERIFICATIONS_TABLE)
    for idx in INDEXES:
        conn.execute(idx)


register_migration(PG_SCHEMA, 2, _migrate_to_v2)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Ensure the reboot schema is up to date.

    Args:
        conn: Active psycopg connection.
    """
    from elle.storage.migrate import run_migrations

    run_migrations(conn, PG_SCHEMA)


def drop_all_tables(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Drop all Reboot tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: Active psycopg connection.
    """
    tables = ["pending_verifications", "reboot_intents", "_meta"]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.commit()
