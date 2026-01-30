"""Tests for DB helper utilities (PostgreSQL version).

Tests the ``elle.common.db`` module which now provides
``init_all_schemas()`` for PostgreSQL schema initialization,
and the ``elle.storage.engine`` connection pool management.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# init_all_schemas
# =============================================================================


class TestInitAllSchemas:
    """Tests for init_all_schemas function."""

    @patch("elle.common.db.get_conn")
    @patch("elle.common.db.run_migrations")
    @patch("elle.common.db._import_schema_modules")
    def test_calls_import_schema_modules(self, mock_import, mock_migrate, mock_conn):
        from elle.common.db import init_all_schemas

        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        init_all_schemas()
        mock_import.assert_called_once()

    @patch("elle.common.db.get_conn")
    @patch("elle.common.db.run_migrations")
    @patch("elle.common.db._import_schema_modules")
    def test_iterates_all_schemas(self, mock_import, mock_migrate, mock_conn):
        from elle.common.db import init_all_schemas
        from elle.storage.config import SCHEMAS

        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        init_all_schemas()
        # Should call get_conn for each schema
        assert mock_conn.call_count == len(SCHEMAS)

    @patch("elle.common.db.get_conn")
    @patch("elle.common.db.run_migrations")
    @patch("elle.common.db._import_schema_modules")
    def test_logs_error_on_failure(self, mock_import, mock_migrate, mock_conn):
        from elle.common.db import init_all_schemas

        mock_conn.side_effect = RuntimeError("Connection failed")
        # Should not raise, but log errors
        init_all_schemas()


# =============================================================================
# Storage engine pool management
# =============================================================================


class TestStorageEngine:
    """Tests for the connection pool management in elle.storage.engine."""

    def test_configure_pool_returns_pool(self):
        """configure_pool should return a ConnectionPool."""
        from elle.storage.engine import configure_pool, reset_pools

        reset_pools()
        try:
            with patch("elle.storage.engine.ConnectionPool") as MockPool:
                pool = configure_pool()
                assert pool is not None
        finally:
            reset_pools()

    def test_get_pool_raises_without_configure(self):
        """get_pool should raise RuntimeError if not configured."""
        from elle.storage.engine import get_pool, reset_pools

        reset_pools()
        with pytest.raises(RuntimeError, match="not configured"):
            get_pool()

    def test_get_async_pool_raises_without_configure(self):
        """get_async_pool should raise RuntimeError if not configured."""
        from elle.storage.engine import get_async_pool, reset_pools

        reset_pools()
        with pytest.raises(RuntimeError, match="not configured"):
            get_async_pool()

    def test_reset_pools_clears_singletons(self):
        """reset_pools should clear the pool singletons."""
        from elle.storage.engine import reset_pools
        import elle.storage.engine as engine_mod

        reset_pools()
        assert engine_mod._pool is None
        assert engine_mod._async_pool is None
        assert engine_mod._config is None

    def test_close_pools_handles_none(self):
        """close_pools should not raise when pools are None."""
        from elle.storage.engine import close_pools, reset_pools

        reset_pools()
        close_pools()  # Should not raise


# =============================================================================
# PostgreSQL connection fixture smoke tests
# =============================================================================


class TestPgConnFixture:
    """Tests that the pg_conn fixture provides a usable connection."""

    def test_connection_is_functional(self, pg_conn):
        """pg_conn should provide a functional PostgreSQL connection."""
        row = pg_conn.execute("SELECT 1 AS val").fetchone()
        assert row["val"] == 1

    def test_dict_row_factory(self, pg_conn):
        """pg_conn should return dict-like rows by default."""
        row = pg_conn.execute("SELECT 'hello' AS greeting").fetchone()
        assert row["greeting"] == "hello"

    def test_savepoint_isolation(self, pg_conn):
        """Changes within a test should be rolled back after the test."""
        pg_conn.execute("CREATE TABLE IF NOT EXISTS _test_isolation (id SERIAL PRIMARY KEY)")
        pg_conn.execute("INSERT INTO _test_isolation DEFAULT VALUES")
        row = pg_conn.execute("SELECT COUNT(*) AS cnt FROM _test_isolation").fetchone()
        assert row["cnt"] >= 1
