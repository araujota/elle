"""Incident Vault SQLite schema.

Defines the database schema for incident reports:
- incidents: Core incident records
- incident_actions: Ordered actions taken
- incident_snapshots: Pre/post system snapshots
- incident_events: Many-to-many link to telemetry events
- incident_embeddings: Vector storage for semantic search
- incidents_fts: FTS5 virtual table for lexical search

Schema version is tracked in the meta table for migrations.
"""

import sqlite3
from pathlib import Path

# Schema version - increment when schema changes
SCHEMA_VERSION = 1

# Database location
DB_PATH = Path("/var/lib/elle/incidents.db")

# Core incidents table
INCIDENTS_TABLE = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    -- Classification
    domain TEXT NOT NULL DEFAULT 'other',
    severity TEXT NOT NULL DEFAULT 'warning',
    status TEXT NOT NULL DEFAULT 'open',

    -- What happened
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    symptoms_json TEXT NOT NULL DEFAULT '[]',
    suspected_causes_json TEXT NOT NULL DEFAULT '[]',
    root_cause TEXT,

    -- Evidence
    event_ids_json TEXT NOT NULL DEFAULT '[]',
    log_snippets_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',

    -- Decision
    decision_json TEXT NOT NULL DEFAULT '{}',
    preconditions_json TEXT NOT NULL DEFAULT '[]',

    -- Outcome
    outcome TEXT NOT NULL DEFAULT 'unknown',
    verification_steps_json TEXT NOT NULL DEFAULT '[]',
    time_to_mitigate_sec INTEGER,
    time_to_resolve_sec INTEGER,

    -- Similarity
    fingerprint_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,

    -- Trigger
    trigger_source TEXT NOT NULL DEFAULT 'manual',
    trigger_command TEXT
)
"""

# Actions table (append-only log of what was done)
ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    kind TEXT NOT NULL,

    -- Action details
    command TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',

    -- Results
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    privileged INTEGER NOT NULL DEFAULT 0,

    -- Timing
    created_at TEXT NOT NULL,
    duration_ms INTEGER,

    UNIQUE(incident_id, step_index)
)
"""

# Snapshots table (pre/post system state)
SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    which TEXT NOT NULL CHECK (which IN ('pre', 'post')),
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,

    UNIQUE(incident_id, which)
)
"""

# Events link table (many-to-many)
EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_events (
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    PRIMARY KEY (incident_id, event_id)
)
"""

# Embeddings table (for semantic search)
EMBEDDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_embeddings (
    incident_id TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""

# Meta table for tracking state
META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# FTS5 virtual table for lexical search
# Using contentless FTS5 for simpler sync (no content= option)
INCIDENTS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts USING fts5(
    title, summary, symptoms_text, root_cause, tags_text,
    tokenize='porter unicode61'
)
"""

# Triggers to keep FTS5 in sync
# Use explicit rowid management since incidents use TEXT primary key
TRIGGER_AI = """
CREATE TRIGGER IF NOT EXISTS incidents_ai AFTER INSERT ON incidents BEGIN
    INSERT INTO incidents_fts(rowid, title, summary, symptoms_text, root_cause, tags_text)
    VALUES (
        new.rowid,
        new.title,
        new.summary,
        COALESCE(new.symptoms_json, ''),
        COALESCE(new.root_cause, ''),
        COALESCE(new.tags_json, '')
    );
END
"""

TRIGGER_AD = """
CREATE TRIGGER IF NOT EXISTS incidents_ad AFTER DELETE ON incidents BEGIN
    DELETE FROM incidents_fts WHERE rowid = old.rowid;
END
"""

TRIGGER_AU = """
CREATE TRIGGER IF NOT EXISTS incidents_au AFTER UPDATE ON incidents BEGIN
    DELETE FROM incidents_fts WHERE rowid = old.rowid;
    INSERT INTO incidents_fts(rowid, title, summary, symptoms_text, root_cause, tags_text)
    VALUES (
        new.rowid,
        new.title,
        new.summary,
        COALESCE(new.symptoms_json, ''),
        COALESCE(new.root_cause, ''),
        COALESCE(new.tags_json, '')
    );
END
"""

# Indexes for common queries
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_domain ON incidents(domain)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_outcome ON incidents(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_updated ON incidents(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_actions_incident ON incident_actions(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_incident ON incident_snapshots(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_event ON incident_events(event_id)",
]


def init_incident_schema(conn: sqlite3.Connection) -> None:
    """Initialize the Incident Vault schema.

    Creates all tables, triggers, and indexes if they don't exist.
    Safe to call multiple times.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # Create tables
    cursor.execute(INCIDENTS_TABLE)
    cursor.execute(ACTIONS_TABLE)
    cursor.execute(SNAPSHOTS_TABLE)
    cursor.execute(EVENTS_TABLE)
    cursor.execute(EMBEDDINGS_TABLE)
    cursor.execute(META_TABLE)

    # Create FTS table
    cursor.execute(INCIDENTS_FTS)

    # Create triggers
    cursor.execute(TRIGGER_AI)
    cursor.execute(TRIGGER_AD)
    cursor.execute(TRIGGER_AU)

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
    current = get_schema_version(conn) or 0

    # Future migrations would go here
    # if current < 2:
    #     _migrate_v1_to_v2(conn)

    # Update schema version
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def drop_all_tables(conn: sqlite3.Connection) -> None:
    """Drop all Incident Vault tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Drop triggers first
    cursor.execute("DROP TRIGGER IF EXISTS incidents_ai")
    cursor.execute("DROP TRIGGER IF EXISTS incidents_ad")
    cursor.execute("DROP TRIGGER IF EXISTS incidents_au")

    # Drop FTS table
    cursor.execute("DROP TABLE IF EXISTS incidents_fts")

    # Drop tables (order matters due to foreign keys)
    cursor.execute("DROP TABLE IF EXISTS incident_embeddings")
    cursor.execute("DROP TABLE IF EXISTS incident_events")
    cursor.execute("DROP TABLE IF EXISTS incident_snapshots")
    cursor.execute("DROP TABLE IF EXISTS incident_actions")
    cursor.execute("DROP TABLE IF EXISTS incidents")
    cursor.execute("DROP TABLE IF EXISTS meta")

    conn.commit()


def get_db_path() -> Path:
    """Get the Incident Vault database path.

    Uses a separate database file for incidents to keep it
    isolated from other ELLE databases.

    Returns:
        Path to the Incident Vault database.
    """
    return DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the Incident Vault database.

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
        init_incident_schema(conn)
