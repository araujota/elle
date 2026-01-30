"""Root conftest for ELLE tests.

Provides PostgreSQL fixtures for database-dependent tests.
Tests that need a real database use the ``pg_db`` and ``pg_conn``
fixtures.  When PostgreSQL is not available, those tests are
automatically skipped.

Set ``ELLE_TEST_PG_URL`` to override the superuser connection
(default: ``dbname=postgres`` via Unix socket).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# PostgreSQL availability check
# ---------------------------------------------------------------------------

_pg_available: bool | None = None


def _check_pg() -> bool:
    """Check if PostgreSQL is reachable for testing."""
    global _pg_available
    if _pg_available is not None:
        return _pg_available

    try:
        import psycopg

        su_url = os.environ.get("ELLE_TEST_PG_URL", "dbname=postgres")
        with psycopg.connect(su_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        _pg_available = True
    except Exception:
        _pg_available = False
    return _pg_available


# ---------------------------------------------------------------------------
# Session-scoped database fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_db() -> Any:
    """Create an ephemeral PostgreSQL test database for the session.

    The database is created with all ELLE extensions (vector, pgcrypto,
    pg_trgm) and all 10 schemas.  Migrations are run so all tables exist.

    Yields the conninfo string for connecting to the test database.
    """
    if not _check_pg():
        pytest.skip("PostgreSQL not available")

    from elle.storage.testing import create_test_db, drop_test_db

    conninfo = create_test_db()
    yield conninfo
    drop_test_db(conninfo)


# ---------------------------------------------------------------------------
# Per-test connection fixture (SAVEPOINT rollback for isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_conn(pg_db: str) -> Any:
    """Per-test connection with SAVEPOINT rollback for isolation.

    Each test gets a connection from the test database.  A SAVEPOINT
    is created before the test runs and rolled back after, so each
    test sees a clean state without the overhead of recreating tables.
    """
    import psycopg
    from psycopg.rows import dict_row

    try:
        from pgvector.psycopg import register_vector
    except ImportError:
        register_vector = None  # type: ignore[assignment]

    with psycopg.connect(pg_db, row_factory=dict_row) as conn:
        if register_vector is not None:
            register_vector(conn)
        conn.execute("SAVEPOINT test_sp")
        yield conn
        conn.execute("ROLLBACK TO SAVEPOINT test_sp")


# ---------------------------------------------------------------------------
# Schema-specific connection helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def incidents_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to incidents schema."""
    pg_conn.execute("SET search_path TO incidents, public")
    return pg_conn


@pytest.fixture
def telemetry_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to telemetry schema."""
    pg_conn.execute("SET search_path TO telemetry, public")
    return pg_conn


@pytest.fixture
def manvault_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to manvault schema."""
    pg_conn.execute("SET search_path TO manvault, public")
    return pg_conn


@pytest.fixture
def reactive_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to reactive schema."""
    pg_conn.execute("SET search_path TO reactive, public")
    return pg_conn


@pytest.fixture
def reboot_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to reboot schema."""
    pg_conn.execute("SET search_path TO reboot, public")
    return pg_conn


@pytest.fixture
def autogen_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to autogen schema."""
    pg_conn.execute("SET search_path TO autogen, public")
    return pg_conn


@pytest.fixture
def mobile_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to mobile schema."""
    pg_conn.execute("SET search_path TO mobile, public")
    return pg_conn


@pytest.fixture
def docker_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to docker schema."""
    pg_conn.execute("SET search_path TO docker, public")
    return pg_conn


@pytest.fixture
def api_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to api schema."""
    pg_conn.execute("SET search_path TO api, public")
    return pg_conn


@pytest.fixture
def deps_conn(pg_conn: Any) -> Any:
    """Connection with search_path set to deps schema."""
    pg_conn.execute("SET search_path TO deps, public")
    return pg_conn
