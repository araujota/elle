"""Test database fixtures for ELLE.

Provides helpers for creating ephemeral PostgreSQL test databases
with all extensions and schemas.  Uses SAVEPOINT for near-instant
per-test cleanup.

Usage in conftest.py:
    from elle.storage.testing import create_test_db, drop_test_db

    @pytest.fixture(scope="session")
    def pg_db():
        conninfo = create_test_db()
        yield conninfo
        drop_test_db(conninfo)

    @pytest.fixture
    def pg_conn(pg_db):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(pg_db, row_factory=dict_row) as conn:
            conn.execute("SAVEPOINT test_sp")
            yield conn
            conn.execute("ROLLBACK TO SAVEPOINT test_sp")
"""

from __future__ import annotations

import logging
import os
import uuid

import psycopg
from psycopg.rows import dict_row

from elle.storage.config import EXTENSIONS, SCHEMAS, PostgresConfig

logger = logging.getLogger(__name__)


def _superuser_conninfo() -> str:
    """Build a connection string for the PostgreSQL superuser.

    Uses ``ELLE_TEST_PG_URL`` env var if set, otherwise connects
    via Unix socket as the current OS user to the ``postgres`` database.

    Returns:
        Connection string for database creation/deletion.
    """
    env = os.environ.get("ELLE_TEST_PG_URL")
    if env:
        return env
    return "dbname=postgres"


def create_test_db(config: PostgresConfig | None = None) -> str:
    """Create an ephemeral test database with all extensions and schemas.

    The database is named ``elle_test_<uuid>`` to avoid conflicts.

    Args:
        config: Optional base config (user/port/host are used).

    Returns:
        A ``conninfo`` string for the new database.
    """
    cfg = config or PostgresConfig()
    db_name = f"elle_test_{uuid.uuid4().hex[:12]}"

    # Create the database (requires superuser or CREATEDB privilege)
    su_conninfo = _superuser_conninfo()
    with psycopg.connect(su_conninfo, autocommit=True) as su_conn:
        su_conn.execute(f"CREATE DATABASE {db_name}")
        logger.info("Created test database: %s", db_name)

    # Build conninfo for the new database
    if cfg.host:
        test_conninfo = f"host={cfg.host} port={cfg.port} dbname={db_name} user={cfg.user}"
    else:
        test_conninfo = f"dbname={db_name}"

    # Install extensions and create schemas
    with psycopg.connect(test_conninfo, autocommit=True) as conn:
        for ext in EXTENSIONS:
            try:
                conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
            except psycopg.errors.InsufficientPrivilege:
                logger.warning("Cannot create extension %s (need superuser)", ext)
            except Exception:
                logger.warning("Extension %s not available, skipping", ext)

        for schema in SCHEMAS:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # Run migrations
    _run_test_migrations(test_conninfo)

    return test_conninfo


def _run_test_migrations(conninfo: str) -> None:
    """Run all registered migrations against the test database.

    Args:
        conninfo: Connection string for the test database.
    """
    from elle.storage.migrate import run_all_migrations
    from elle.storage.provision import _import_schema_modules

    _import_schema_modules()

    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        run_all_migrations(conn)


def drop_test_db(conninfo: str) -> None:
    """Drop a test database.

    Extracts the database name from the connection string and drops it.

    Args:
        conninfo: Connection string (as returned by :func:`create_test_db`).
    """
    # Parse dbname from conninfo
    db_name = None
    for part in conninfo.split():
        if part.startswith("dbname="):
            db_name = part.split("=", 1)[1]
            break

    if not db_name or not db_name.startswith("elle_test_"):
        logger.warning("Refusing to drop non-test database: %s", db_name)
        return

    su_conninfo = _superuser_conninfo()
    with psycopg.connect(su_conninfo, autocommit=True) as su_conn:
        # Terminate connections first
        su_conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid != pg_backend_pid()
            """,
            (db_name,),
        )
        su_conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
        logger.info("Dropped test database: %s", db_name)
