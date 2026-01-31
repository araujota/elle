"""Incident Vault PostgreSQL schema.

Defines the database schema for incident reports.  Uses:
- JSONB for structured nested data (replaces TEXT JSON columns)
- TIMESTAMPTZ for all timestamps (replaces TEXT ISO 8601)
- tsvector + GIN index for full-text search (replaces FTS5)
- vector(768) via pgvector for embeddings (replaces BLOB + struct.pack)

Schema version is tracked in the ``_meta`` table.
Current version: 6
"""

from __future__ import annotations

import logging

import psycopg

from elle.storage.migrate import register_migration

logger = logging.getLogger(__name__)

# Schema version - increment when schema changes
SCHEMA_VERSION = 6

# PostgreSQL schema name
PG_SCHEMA = "incidents"


# ---------------------------------------------------------------------------
# Table DDL
# ---------------------------------------------------------------------------

INCIDENTS_TABLE = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,

    -- Classification
    domain TEXT NOT NULL DEFAULT 'other',
    severity TEXT NOT NULL DEFAULT 'warning',
    status TEXT NOT NULL DEFAULT 'open',

    -- What happened
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    symptoms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    suspected_causes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    root_cause TEXT,

    -- Evidence
    event_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    log_snippets_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Decision
    decision_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    preconditions_json JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Outcome
    outcome TEXT NOT NULL DEFAULT 'unknown',
    verification_steps_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    time_to_mitigate_sec INTEGER,
    time_to_resolve_sec INTEGER,

    -- Similarity
    fingerprint_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence REAL NOT NULL DEFAULT 0.0,

    -- Trigger
    trigger_source TEXT NOT NULL DEFAULT 'manual',
    trigger_command TEXT,

    -- V5: Forecast incident tracking
    forecast_metric TEXT,
    forecast_urgency TEXT,
    prepared_plan_json JSONB,
    plan_executed_at TIMESTAMPTZ,

    -- Full-text search (trigger-maintained because JSONB columns are included)
    search_tsv TSVECTOR
)
"""

ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_actions (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    kind TEXT NOT NULL,

    -- Action details
    command TEXT,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Results
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    privileged BOOLEAN NOT NULL DEFAULT FALSE,

    -- Timing
    created_at TIMESTAMPTZ NOT NULL,
    duration_ms INTEGER,

    UNIQUE(incident_id, step_index)
)
"""

SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_snapshots (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    which TEXT NOT NULL CHECK (which IN ('pre', 'post')),
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,

    UNIQUE(incident_id, which)
)
"""

EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_events (
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    PRIMARY KEY (incident_id, event_id)
)
"""

EMBEDDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_embeddings (
    incident_id TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    embedding vector(768) NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""

DECISION_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS decision_records (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(id) ON DELETE CASCADE,
    chosen_approach TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    confidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    planned_commands_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    decided_at TIMESTAMPTZ NOT NULL
)
"""

CONFIG_STATES_TABLE = """
CREATE TABLE IF NOT EXISTS config_states (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    snapshot_which TEXT NOT NULL CHECK (snapshot_which IN ('pre', 'post')),
    path TEXT NOT NULL,
    sha256_before TEXT,
    sha256_after TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime TIMESTAMPTZ,
    backup_path TEXT,
    UNIQUE(incident_id, snapshot_which, path)
)
"""

DOMAIN_EFFICACY_TABLE = """
CREATE TABLE IF NOT EXISTS domain_efficacy (
    domain TEXT PRIMARY KEY,
    total_incidents INTEGER NOT NULL DEFAULT 0,
    improved_count INTEGER NOT NULL DEFAULT 0,
    partial_count INTEGER NOT NULL DEFAULT 0,
    no_change_count INTEGER NOT NULL DEFAULT 0,
    worse_count INTEGER NOT NULL DEFAULT 0,
    success_rate REAL NOT NULL DEFAULT 0.5,
    last_updated TIMESTAMPTZ NOT NULL
)
"""

ENTITY_EFFICACY_TABLE = """
CREATE TABLE IF NOT EXISTS entity_efficacy (
    entity TEXT PRIMARY KEY,
    total_incidents INTEGER NOT NULL DEFAULT 0,
    improved_count INTEGER NOT NULL DEFAULT 0,
    partial_count INTEGER NOT NULL DEFAULT 0,
    no_change_count INTEGER NOT NULL DEFAULT 0,
    worse_count INTEGER NOT NULL DEFAULT 0,
    success_rate REAL NOT NULL DEFAULT 0.5,
    last_updated TIMESTAMPTZ NOT NULL
)
"""

SOLUTION_APPROACH_EFFICACY_TABLE = """
CREATE TABLE IF NOT EXISTS solution_approach_efficacy (
    approach_signature TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    precondition_summary TEXT NOT NULL DEFAULT '',
    key_commands_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_uses INTEGER NOT NULL DEFAULT 0,
    improved_count INTEGER NOT NULL DEFAULT 0,
    partial_count INTEGER NOT NULL DEFAULT 0,
    no_change_count INTEGER NOT NULL DEFAULT 0,
    worse_count INTEGER NOT NULL DEFAULT 0,
    success_rate REAL NOT NULL DEFAULT 0.5,
    avg_time_to_resolve_sec INTEGER,
    last_used TIMESTAMPTZ NOT NULL
)
"""

TELEMETRY_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry_snapshots (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    which TEXT NOT NULL CHECK (which IN ('pre', 'post')),
    snapshot_json JSONB NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    UNIQUE(incident_id, which)
)
"""

CONTROL_SURFACE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS control_surface_snapshots (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    which TEXT NOT NULL CHECK (which IN ('pre', 'post')),
    snapshot_json JSONB NOT NULL,
    surface_hashes TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    UNIQUE(incident_id, which)
)
"""

CLOUD_SUBMISSION_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS cloud_submission_queue (
    id SERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    original_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_flight', 'succeeded', 'failed_permanent')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 20,
    next_retry_at TIMESTAMPTZ NOT NULL,
    last_error TEXT,
    enqueued_at TIMESTAMPTZ NOT NULL,
    last_attempted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cloud_id TEXT
)
"""

# FTS trigger function (PL/pgSQL)
# Maintains search_tsv by concatenating text fields + JSONB text casts
SEARCH_TSV_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION incidents_search_tsv_update()
RETURNS trigger AS $$
BEGIN
    NEW.search_tsv := to_tsvector('english',
        coalesce(NEW.title, '') || ' ' ||
        coalesce(NEW.summary, '') || ' ' ||
        coalesce(NEW.root_cause, '') || ' ' ||
        coalesce(NEW.symptoms_json::text, '') || ' ' ||
        coalesce(NEW.tags_json::text, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

SEARCH_TSV_TRIGGER = """
CREATE TRIGGER incidents_search_tsv_trg
    BEFORE INSERT OR UPDATE ON incidents
    FOR EACH ROW
    EXECUTE FUNCTION incidents_search_tsv_update()
"""


# Indexes
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_domain ON incidents(domain)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_outcome ON incidents(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_updated ON incidents(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_search_tsv ON incidents USING GIN(search_tsv)",
    "CREATE INDEX IF NOT EXISTS idx_actions_incident ON incident_actions(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_incident ON incident_snapshots(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_event ON incident_events(event_id)",
    # Decision & config
    "CREATE INDEX IF NOT EXISTS idx_decision_records_incident ON decision_records(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_config_states_incident ON config_states(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_config_states_path ON config_states(path)",
    # Efficacy
    "CREATE INDEX IF NOT EXISTS idx_domain_efficacy_success ON domain_efficacy(success_rate DESC)",
    "CREATE INDEX IF NOT EXISTS idx_entity_efficacy_success ON entity_efficacy(success_rate DESC)",
    "CREATE INDEX IF NOT EXISTS idx_entity_efficacy_total ON entity_efficacy(total_incidents DESC)",
    "CREATE INDEX IF NOT EXISTS idx_approach_efficacy_domain ON solution_approach_efficacy(domain)",
    "CREATE INDEX IF NOT EXISTS idx_approach_efficacy_success ON solution_approach_efficacy(success_rate DESC)",
    # Telemetry / control surface snapshots
    "CREATE INDEX IF NOT EXISTS idx_telemetry_snapshots_incident ON telemetry_snapshots(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_control_surface_snapshots_incident ON control_surface_snapshots(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_control_surface_hashes ON control_surface_snapshots(surface_hashes)",
    # Forecast tracking
    "CREATE INDEX IF NOT EXISTS idx_incidents_forecast_metric ON incidents(forecast_metric)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_forecast_urgency ON incidents(forecast_urgency)",
    # Cloud queue
    "CREATE INDEX IF NOT EXISTS idx_cloud_queue_status ON cloud_submission_queue(status)",
    "CREATE INDEX IF NOT EXISTS idx_cloud_queue_next_retry ON cloud_submission_queue(next_retry_at)",
    "CREATE INDEX IF NOT EXISTS idx_cloud_queue_incident ON cloud_submission_queue(incident_id)",
    # Embedding HNSW index for semantic search
    "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON incident_embeddings USING hnsw (embedding vector_cosine_ops)",
]


# ---------------------------------------------------------------------------
# Migration: v0 -> v6 (single step for fresh installs)
# ---------------------------------------------------------------------------


def _migrate_to_v6(conn: psycopg.Connection) -> None:
    """Create the full incidents schema at v6."""
    # Tables
    conn.execute(INCIDENTS_TABLE)
    conn.execute(ACTIONS_TABLE)
    conn.execute(SNAPSHOTS_TABLE)
    conn.execute(EVENTS_TABLE)
    conn.execute(EMBEDDINGS_TABLE)
    conn.execute(DECISION_RECORDS_TABLE)
    conn.execute(CONFIG_STATES_TABLE)
    conn.execute(DOMAIN_EFFICACY_TABLE)
    conn.execute(ENTITY_EFFICACY_TABLE)
    conn.execute(SOLUTION_APPROACH_EFFICACY_TABLE)
    conn.execute(TELEMETRY_SNAPSHOTS_TABLE)
    conn.execute(CONTROL_SURFACE_SNAPSHOTS_TABLE)
    conn.execute(CLOUD_SUBMISSION_QUEUE_TABLE)

    # FTS trigger
    conn.execute(SEARCH_TSV_TRIGGER_FN)
    # Drop trigger first to avoid "already exists" on re-run
    conn.execute("DROP TRIGGER IF EXISTS incidents_search_tsv_trg ON incidents")
    conn.execute(SEARCH_TSV_TRIGGER)

    # Indexes
    for idx in INDEXES:
        conn.execute(idx)


register_migration(PG_SCHEMA, 6, _migrate_to_v6)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection) -> None:
    """Ensure the incidents schema is up to date.

    Args:
        conn: Active psycopg connection.
    """
    from elle.storage.migrate import run_migrations

    run_migrations(conn, PG_SCHEMA)


def drop_all_tables(conn: psycopg.Connection) -> None:
    """Drop all Incident Vault tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: Active psycopg connection.
    """
    # Drop trigger and function
    conn.execute("DROP TRIGGER IF EXISTS incidents_search_tsv_trg ON incidents")
    conn.execute("DROP FUNCTION IF EXISTS incidents_search_tsv_update()")

    # Drop tables (order matters due to foreign keys)
    tables = [
        "cloud_submission_queue",
        "control_surface_snapshots",
        "telemetry_snapshots",
        "solution_approach_efficacy",
        "entity_efficacy",
        "domain_efficacy",
        "config_states",
        "decision_records",
        "incident_embeddings",
        "incident_events",
        "incident_snapshots",
        "incident_actions",
        "incidents",
        "_meta",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    conn.commit()
