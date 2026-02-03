"""Man Vault PostgreSQL schema.

Defines the database schema for the Man Vault documentation index.
Uses:
- tsvector GENERATED column for full-text search (replaces FTS5)
- vector(768) via pgvector for embeddings (replaces BLOB + struct.pack)
- TIMESTAMPTZ for all timestamps

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
PG_SCHEMA = "manvault"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DOCS_TABLE = """
CREATE TABLE IF NOT EXISTS docs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    section TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'en',
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    package_hint TEXT,
    priority INTEGER DEFAULT 0,
    doc_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(name, '') || ' ' ||
            coalesce(section, '') || ' ' ||
            coalesce(text, '')
        )
    ) STORED,
    UNIQUE(name, section, lang)
)
"""

CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
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
    embedding vector(768) NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""

META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_docs_name ON docs(name)",
    "CREATE INDEX IF NOT EXISTS idx_docs_name_section ON docs(name, section)",
    "CREATE INDEX IF NOT EXISTS idx_docs_sha256 ON docs(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_docs_tsv ON docs USING GIN(doc_tsv)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model)",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops)",
]


# ---------------------------------------------------------------------------
# Migration v0 -> v1: Initial schema
# ---------------------------------------------------------------------------


def _migrate_to_v1(conn: psycopg.Connection) -> None:
    """Create the initial manvault schema."""
    conn.execute(DOCS_TABLE)
    conn.execute(CHUNKS_TABLE)
    conn.execute(EMBEDDINGS_TABLE)
    conn.execute(META_TABLE)
    for idx in INDEXES:
        conn.execute(idx)


register_migration(PG_SCHEMA, 1, _migrate_to_v1)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection) -> None:
    """Ensure the manvault schema is up to date.

    Args:
        conn: Active psycopg connection.
    """
    from elle.storage.migrate import run_migrations

    run_migrations(conn, PG_SCHEMA)


def drop_all_tables(conn: psycopg.Connection) -> None:
    """Drop all Man Vault tables.

    WARNING: This destroys all data. Use only for testing.

    Args:
        conn: Active psycopg connection.
    """
    tables = ["embeddings", "chunks", "docs", "meta", "_meta"]
    for table in tables:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table)))
    conn.commit()
