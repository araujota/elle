"""Tests for the storage migration runner."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup for psycopg before any elle.storage imports
# ---------------------------------------------------------------------------

_UndefinedTable = type("UndefinedTable", (Exception,), {})
_mock_pg = MagicMock()
_mock_pg.errors = MagicMock()
_mock_pg.errors.UndefinedTable = _UndefinedTable
_mock_pg.Connection = MagicMock

_mock_pg_rows = MagicMock()
_mock_pg_pool = MagicMock()

# Patch sys.modules BEFORE importing elle.storage.migrate
# This avoids the elle.storage.__init__ -> elle.storage.engine -> psycopg chain
_modules_to_mock = {
    "psycopg": _mock_pg,
    "psycopg.errors": _mock_pg.errors,
    "psycopg.rows": _mock_pg_rows,
    "psycopg_pool": _mock_pg_pool,
}

for mod_name, mock_mod in _modules_to_mock.items():
    if mod_name not in sys.modules:
        sys.modules[mod_name] = mock_mod

from elle.storage.migrate import (  # noqa: E402
    _ensure_meta_table,
    _migrations,
    _target_versions,
    get_schema_version,
    register_migration,
    run_all_migrations,
    run_migrations,
)


@pytest.fixture(autouse=True)
def reset_migrate_state():
    """Reset module-level state between tests."""
    _migrations.clear()
    _target_versions.clear()
    yield
    _migrations.clear()
    _target_versions.clear()


# ===========================================================================
# register_migration
# ===========================================================================


class TestRegisterMigration:
    def test_register_single_migration(self):
        fn = MagicMock()
        register_migration("test_schema", 1, fn)

        assert "test_schema" in _migrations
        assert len(_migrations["test_schema"]) == 1
        assert _migrations["test_schema"][0] == (1, fn)
        assert _target_versions["test_schema"] == 1

    def test_register_multiple_migrations(self):
        fn1 = MagicMock()
        fn2 = MagicMock()
        fn3 = MagicMock()

        register_migration("schema_a", 1, fn1)
        register_migration("schema_a", 2, fn2)
        register_migration("schema_a", 3, fn3)

        assert len(_migrations["schema_a"]) == 3
        assert _target_versions["schema_a"] == 3

    def test_migrations_sorted_by_version(self):
        fn1 = MagicMock()
        fn2 = MagicMock()
        fn3 = MagicMock()

        # Register out of order
        register_migration("schema_b", 3, fn3)
        register_migration("schema_b", 1, fn1)
        register_migration("schema_b", 2, fn2)

        versions = [v for v, _ in _migrations["schema_b"]]
        assert versions == [1, 2, 3]

    def test_register_separate_schemas(self):
        fn_a = MagicMock()
        fn_b = MagicMock()

        register_migration("alpha", 1, fn_a)
        register_migration("beta", 1, fn_b)

        assert "alpha" in _migrations
        assert "beta" in _migrations
        assert _target_versions["alpha"] == 1
        assert _target_versions["beta"] == 1

    def test_target_version_tracks_highest(self):
        register_migration("s", 2, MagicMock())
        register_migration("s", 1, MagicMock())  # Lower version registered after

        assert _target_versions["s"] == 2


# ===========================================================================
# get_schema_version
# ===========================================================================


class TestGetSchemaVersion:
    def test_returns_version_from_meta_table(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"value": "3"}

        result = get_schema_version(conn, "test_schema")
        assert result == 3
        conn.execute.assert_called_once_with("SELECT value FROM _meta WHERE key = 'schema_version'")

    def test_returns_zero_when_no_row(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        result = get_schema_version(conn, "test_schema")
        assert result == 0

    def test_returns_zero_on_undefined_table(self):
        conn = MagicMock()
        conn.execute.side_effect = _UndefinedTable("table not found")

        result = get_schema_version(conn, "test_schema")
        assert result == 0
        conn.rollback.assert_called_once()

    def test_returns_zero_on_generic_exception(self):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("unexpected")

        result = get_schema_version(conn, "test_schema")
        assert result == 0
        conn.rollback.assert_called_once()


# ===========================================================================
# _ensure_meta_table
# ===========================================================================


class TestEnsureMetaTable:
    def test_creates_meta_table(self):
        conn = MagicMock()
        _ensure_meta_table(conn, "my_schema")
        conn.execute.assert_called_once()
        call_sql = conn.execute.call_args[0][0]
        assert "my_schema._meta" in call_sql
        assert "CREATE TABLE IF NOT EXISTS" in call_sql


# ===========================================================================
# run_migrations
# ===========================================================================


class TestRunMigrations:
    def test_no_migrations_returns_current(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        result = run_migrations(conn, "empty_schema")
        assert result == 0

    def test_already_at_target_version(self):
        register_migration("s", 1, MagicMock())

        conn = MagicMock()

        def side_effect(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.fetchone.return_value = {"value": "1"}
            return mock_result

        conn.execute.side_effect = side_effect

        result = run_migrations(conn, "s")
        assert result == 1

    def test_runs_pending_migrations(self):
        fn1 = MagicMock()
        fn2 = MagicMock()
        register_migration("s", 1, fn1)
        register_migration("s", 2, fn2)

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        result = run_migrations(conn, "s")
        assert result == 2
        fn1.assert_called_once_with(conn)
        fn2.assert_called_once_with(conn)
        conn.commit.assert_called_once()

    def test_skips_already_applied_migrations(self):
        fn1 = MagicMock()
        fn2 = MagicMock()
        register_migration("s", 1, fn1)
        register_migration("s", 2, fn2)

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"value": "1"}

        result = run_migrations(conn, "s")
        assert result == 2
        fn1.assert_not_called()
        fn2.assert_called_once_with(conn)

    def test_updates_meta_version_after_each_migration(self):
        fn1 = MagicMock()
        fn2 = MagicMock()
        register_migration("s", 1, fn1)
        register_migration("s", 2, fn2)

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        run_migrations(conn, "s")

        # The execute calls should include INSERT INTO _meta for each version
        insert_calls = [c for c in conn.execute.call_args_list if len(c[0]) >= 2 and isinstance(c[0][1], tuple)]
        assert len(insert_calls) == 2
        assert insert_calls[0][0][1] == ("1",)
        assert insert_calls[1][0][1] == ("2",)


# ===========================================================================
# run_all_migrations
# ===========================================================================


class TestRunAllMigrations:
    def test_runs_all_schemas(self):
        fn_a = MagicMock()
        fn_b = MagicMock()
        register_migration("alpha", 1, fn_a)
        register_migration("beta", 1, fn_b)

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        results = run_all_migrations(conn)
        assert "alpha" in results
        assert "beta" in results
        assert results["alpha"] == 1
        assert results["beta"] == 1
        fn_a.assert_called_once()
        fn_b.assert_called_once()

    def test_sets_search_path_for_each_schema(self):
        register_migration("alpha", 1, MagicMock())
        register_migration("beta", 1, MagicMock())

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None

        run_all_migrations(conn)

        search_path_calls = [c for c in conn.execute.call_args_list if len(c[0]) >= 1 and "search_path" in str(c[0][0])]
        assert len(search_path_calls) == 2

    def test_empty_registry(self):
        conn = MagicMock()
        results = run_all_migrations(conn)
        assert results == {}
