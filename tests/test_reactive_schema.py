"""Tests for the reactive functions PostgreSQL schema module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup for psycopg before any elle.storage imports
# ---------------------------------------------------------------------------

_mock_pg = MagicMock()
_mock_pg.errors = MagicMock()
_mock_pg.errors.UndefinedTable = type("UndefinedTable", (Exception,), {})
_mock_pg.Connection = MagicMock

_mock_pg_rows = MagicMock()
_mock_pg_pool = MagicMock()

_modules_to_mock = {
    "psycopg": _mock_pg,
    "psycopg.errors": _mock_pg.errors,
    "psycopg.rows": _mock_pg_rows,
    "psycopg_pool": _mock_pg_pool,
}

for mod_name, mock_mod in _modules_to_mock.items():
    if mod_name not in sys.modules:
        sys.modules[mod_name] = mock_mod

from elle.reactive.schema import (  # noqa: E402
    EXECUTION_HISTORY_TABLE,
    FUNCTION_STATE_TABLE,
    INDEXES,
    PG_SCHEMA,
    REACTIVE_FUNCTIONS_TABLE,
    SCHEMA_VERSION,
    _migrate_to_v1,
    drop_all_tables,
    ensure_schema,
)
from elle.storage.migrate import (  # noqa: E402
    _migrations,
    _target_versions,
    register_migration,
)


@pytest.fixture(autouse=True)
def preserve_migrate_state():
    """Save and restore migration registry around each test.

    Note: we do NOT clear or modify the registry before/during the test.
    We only restore the original state after the test completes.
    """
    saved_migrations = {k: list(v) for k, v in _migrations.items()}
    saved_targets = dict(_target_versions)
    yield
    # Only restore if something changed
    _migrations.clear()
    _migrations.update(saved_migrations)
    _target_versions.clear()
    _target_versions.update(saved_targets)


# ===========================================================================
# Schema Constants
# ===========================================================================


class TestSchemaConstants:
    def test_schema_version(self):
        assert SCHEMA_VERSION == 1

    def test_pg_schema_name(self):
        assert PG_SCHEMA == "reactive"


# ===========================================================================
# DDL Strings
# ===========================================================================


class TestDDLStrings:
    def test_reactive_functions_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS reactive_functions" in REACTIVE_FUNCTIONS_TABLE
        assert "id TEXT PRIMARY KEY" in REACTIVE_FUNCTIONS_TABLE
        assert "name TEXT UNIQUE NOT NULL" in REACTIVE_FUNCTIONS_TABLE
        assert "trigger_json JSONB NOT NULL" in REACTIVE_FUNCTIONS_TABLE
        assert "actions_json JSONB NOT NULL" in REACTIVE_FUNCTIONS_TABLE
        assert "policy_json JSONB NOT NULL" in REACTIVE_FUNCTIONS_TABLE

    def test_execution_history_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS execution_history" in EXECUTION_HISTORY_TABLE
        assert "id TEXT PRIMARY KEY" in EXECUTION_HISTORY_TABLE
        assert "function_id TEXT NOT NULL" in EXECUTION_HISTORY_TABLE
        assert "success BOOLEAN NOT NULL" in EXECUTION_HISTORY_TABLE
        assert "FOREIGN KEY (function_id)" in EXECUTION_HISTORY_TABLE

    def test_function_state_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS function_state" in FUNCTION_STATE_TABLE
        assert "function_id TEXT NOT NULL" in FUNCTION_STATE_TABLE
        assert "key TEXT NOT NULL" in FUNCTION_STATE_TABLE
        assert "PRIMARY KEY (function_id, key)" in FUNCTION_STATE_TABLE

    def test_indexes(self):
        assert isinstance(INDEXES, list)
        assert len(INDEXES) > 0
        for idx in INDEXES:
            assert "CREATE INDEX IF NOT EXISTS" in idx


# ===========================================================================
# Migration Registration
# ===========================================================================


class TestMigrationRegistration:
    def test_migration_registered(self):
        # Re-register if state was cleared by another test module
        from elle.reactive.schema import _migrate_to_v1

        if "reactive" not in _migrations:
            register_migration("reactive", 1, _migrate_to_v1)
        assert "reactive" in _migrations
        versions = [v for v, _ in _migrations["reactive"]]
        assert 1 in versions

    def test_migration_target_version(self):
        from elle.reactive.schema import _migrate_to_v1

        if "reactive" not in _migrations:
            register_migration("reactive", 1, _migrate_to_v1)
        assert _target_versions.get("reactive", 0) >= 1


# ===========================================================================
# _migrate_to_v1
# ===========================================================================


class TestMigrateToV1:
    def test_creates_all_tables_and_indexes(self):
        conn = MagicMock()
        _migrate_to_v1(conn)

        # Should have executed 3 tables + N indexes
        expected_calls = 3 + len(INDEXES)
        assert conn.execute.call_count == expected_calls

    def test_creates_reactive_functions_table(self):
        conn = MagicMock()
        _migrate_to_v1(conn)

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert first_call_sql == REACTIVE_FUNCTIONS_TABLE

    def test_creates_execution_history_table(self):
        conn = MagicMock()
        _migrate_to_v1(conn)

        second_call_sql = conn.execute.call_args_list[1][0][0]
        assert second_call_sql == EXECUTION_HISTORY_TABLE

    def test_creates_function_state_table(self):
        conn = MagicMock()
        _migrate_to_v1(conn)

        third_call_sql = conn.execute.call_args_list[2][0][0]
        assert third_call_sql == FUNCTION_STATE_TABLE


# ===========================================================================
# ensure_schema
# ===========================================================================


class TestEnsureSchema:
    def test_ensure_schema_calls_run_migrations(self):
        conn = MagicMock()
        # ensure_schema does a local import: from elle.storage.migrate import run_migrations
        with patch("elle.storage.migrate.run_migrations") as mock_run:
            ensure_schema(conn)
            mock_run.assert_called_once_with(conn, "reactive")


# ===========================================================================
# drop_all_tables
# ===========================================================================


class TestDropAllTables:
    def test_drops_all_tables(self):
        conn = MagicMock()
        drop_all_tables(conn)

        # Should drop: function_state, execution_history, reactive_functions, _meta
        assert conn.execute.call_count == 4
        dropped = [
            conn.execute.call_args_list[i][0][0]
            for i in range(4)
        ]
        assert any("function_state" in d for d in dropped)
        assert any("execution_history" in d for d in dropped)
        assert any("reactive_functions" in d for d in dropped)
        assert any("_meta" in d for d in dropped)

    def test_drops_with_cascade(self):
        conn = MagicMock()
        drop_all_tables(conn)

        for call_args in conn.execute.call_args_list:
            sql = call_args[0][0]
            assert "CASCADE" in sql

    def test_commits_after_drop(self):
        conn = MagicMock()
        drop_all_tables(conn)
        conn.commit.assert_called_once()

    def test_drop_order(self):
        """Tables should be dropped in correct dependency order."""
        conn = MagicMock()
        drop_all_tables(conn)

        call_sqls = [c[0][0] for c in conn.execute.call_args_list]
        # function_state and execution_history should be dropped before reactive_functions
        state_idx = next(i for i, s in enumerate(call_sqls) if "function_state" in s)
        history_idx = next(i for i, s in enumerate(call_sqls) if "execution_history" in s)
        functions_idx = next(i for i, s in enumerate(call_sqls) if "reactive_functions" in s)
        assert state_idx < functions_idx
        assert history_idx < functions_idx
