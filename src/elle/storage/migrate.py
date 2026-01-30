"""Lightweight schema migration runner for ELLE.

Each schema tracks its version in a ``_meta`` table.  Migrations
are plain Python functions registered per schema via
:func:`register_migration`.

Usage:
    # Register migrations (typically at module level in schema files):
    register_migration("incidents", 2, migrate_v1_to_v2)
    register_migration("incidents", 3, migrate_v2_to_v3)

    # Run pending migrations:
    with get_conn(schema="incidents") as conn:
        run_migrations(conn, "incidents")
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import psycopg

logger = logging.getLogger(__name__)

# Registry: schema_name -> sorted list of (version, callable)
_migrations: dict[str, list[tuple[int, Callable[[psycopg.Connection], None]]]] = {}  # type: ignore[type-arg]

# Current target version per schema (set by register_migration calls)
_target_versions: dict[str, int] = {}


def register_migration(
    schema: str,
    version: int,
    fn: Callable[[psycopg.Connection], None],  # type: ignore[type-arg]
) -> None:
    """Register a migration function for a schema.

    Args:
        schema: Schema name (e.g. ``"incidents"``).
        version: The version this migration brings the schema *to*.
        fn: A callable that takes a psycopg connection and applies
            the migration DDL/DML.  It should NOT commit.
    """
    if schema not in _migrations:
        _migrations[schema] = []
    _migrations[schema].append((version, fn))
    _migrations[schema].sort(key=lambda t: t[0])

    # Track highest registered version
    current_target = _target_versions.get(schema, 0)
    if version > current_target:
        _target_versions[schema] = version


def get_schema_version(conn: psycopg.Connection, schema: str) -> int:  # type: ignore[type-arg]
    """Read the current version from the schema's ``_meta`` table.

    Args:
        conn: Active connection (search_path should already be set).
        schema: Schema name.

    Returns:
        Current version, or 0 if the meta table does not exist.
    """
    try:
        row = conn.execute(
            "SELECT value FROM _meta WHERE key = 'schema_version'"
        ).fetchone()
        if row:
            return int(row["value"])
    except psycopg.errors.UndefinedTable:
        conn.rollback()
    except Exception:
        conn.rollback()
    return 0


def _ensure_meta_table(conn: psycopg.Connection, schema: str) -> None:  # type: ignore[type-arg]
    """Create the ``_meta`` table if it does not exist.

    Args:
        conn: Active connection.
        schema: Schema name (used for qualified table name).
    """
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema}._meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


def run_migrations(conn: psycopg.Connection, schema: str) -> int:  # type: ignore[type-arg]
    """Apply all pending migrations for *schema*.

    Args:
        conn: Active connection with search_path already set.
        schema: Schema name.

    Returns:
        The schema version after migrations.
    """
    _ensure_meta_table(conn, schema)
    current = get_schema_version(conn, schema)
    target = _target_versions.get(schema, 0)

    if current >= target:
        return current

    pending = [
        (v, fn)
        for (v, fn) in _migrations.get(schema, [])
        if v > current
    ]

    for version, fn in pending:
        logger.info("Migrating %s: v%d -> v%d", schema, current, version)
        fn(conn)
        conn.execute(
            """
            INSERT INTO _meta (key, value)
            VALUES ('schema_version', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (str(version),),
        )
        current = version

    conn.commit()
    logger.info("Schema %s is now at v%d", schema, current)
    return current


def run_all_migrations(conn: psycopg.Connection) -> dict[str, int]:  # type: ignore[type-arg]
    """Run migrations for every registered schema.

    Temporarily switches ``search_path`` for each schema.

    Args:
        conn: Active connection.

    Returns:
        Dict mapping schema name to its final version.
    """
    results: dict[str, int] = {}
    for schema in sorted(_migrations.keys()):
        conn.execute("SET search_path TO %s, public", (schema,))
        results[schema] = run_migrations(conn, schema)
    return results
