"""Man Vault SQLite schema.

Defines the database schema for the Man Vault documentation index:
- docs: Full man page documents
- docs_fts: FTS5 virtual table for lexical search
- chunks: Document chunks for embeddings
- embeddings: Vector storage for semantic search
- meta: Key-value store for tracking state

Schema version is tracked in the meta table for migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Schema version - increment when schema changes
SCHEMA_VERSION = 1

# SQL statements for creating the schema
DOCS_TABLE = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    section TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'en',
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    package_hint TEXT,
    priority INTEGER DEFAULT 0,
    UNIQUE(name, section, lang)
)
"""

DOCS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    name, section, text,
    content='docs', content_rowid='id',
    tokenize='porter unicode61'
)
"""

# Triggers to keep FTS5 in sync with docs table
TRIGGER_AI = """
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, name, section, text)
    VALUES (new.id, new.name, new.section, new.text);
END
"""

TRIGGER_AD = """
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, name, section, text)
    VALUES('delete', old.id, old.name, old.section, old.text);
END
"""

TRIGGER_AU = """
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, name, section, text)
    VALUES('delete', old.id, old.name, old.section, old.text);
    INSERT INTO docs_fts(rowid, name, section, text)
    VALUES (new.id, new.name, new.section, new.text);
END
"""

CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading TEXT,
    start_line INTEGER,
    end_line INTEGER,
    UNIQUE(doc_id, chunk_index)
)
"""

EMBEDDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""

META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# Indexes for faster queries
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_docs_name ON docs(name)",
    "CREATE INDEX IF NOT EXISTS idx_docs_name_section ON docs(name, section)",
    "CREATE INDEX IF NOT EXISTS idx_docs_sha256 ON docs(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model)",
]


def init_manvault_schema(conn: sqlite3.Connection) -> None:
    """Initialize the Man Vault schema.

    Creates all tables, triggers, and indexes if they don't exist.
    Uses IF NOT EXISTS so it's safe to call multiple times.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")

    # Create tables
    cursor.execute(DOCS_TABLE)
    cursor.execute(DOCS_FTS)
    cursor.execute(CHUNKS_TABLE)
    cursor.execute(EMBEDDINGS_TABLE)
    cursor.execute(META_TABLE)

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
    _current = get_schema_version(conn) or 0  # For future migration checks

    # Future migrations would go here
    # if _current < 2:
    #     _migrate_v1_to_v2(conn)
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
    """Drop all Man Vault tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: SQLite connection.
    """
    cursor = conn.cursor()

    # Drop triggers first
    cursor.execute("DROP TRIGGER IF EXISTS docs_ai")
    cursor.execute("DROP TRIGGER IF EXISTS docs_ad")
    cursor.execute("DROP TRIGGER IF EXISTS docs_au")

    # Drop FTS table
    cursor.execute("DROP TABLE IF EXISTS docs_fts")

    # Drop tables (order matters due to foreign keys)
    cursor.execute("DROP TABLE IF EXISTS embeddings")
    cursor.execute("DROP TABLE IF EXISTS chunks")
    cursor.execute("DROP TABLE IF EXISTS docs")
    cursor.execute("DROP TABLE IF EXISTS meta")

    conn.commit()


def get_db_path() -> Path:
    """Get the Man Vault database path.

    Uses a separate database file for Man Vault to keep it
    isolated from the main ELLE database.

    Returns:
        Path to the Man Vault database.
    """
    db_dir = Path("/var/lib/elle")
    return db_dir / "manvault.db"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the Man Vault database.

    Creates the database directory if it doesn't exist (for user-local installs).

    Args:
        db_path: Override database path (for testing).

    Returns:
        SQLite connection with row factory set.
    """
    path = db_path or get_db_path()

    # For user-local paths, ensure directory exists
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
        init_manvault_schema(conn)
