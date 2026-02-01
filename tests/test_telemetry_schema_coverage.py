"""Coverage tests for elle.daemon.telemetry.schema.

Covers 0% -> ~100%: constants, DDL strings, _migrate_to_v1,
ensure_schema, get_table_stats.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from elle.daemon.telemetry.schema import (
    CORRELATION_ALERTS_TABLE,
    EVENTS_INDEXES,
    EVENTS_TABLE,
    FORECAST_MODEL_CACHE_TABLE,
    HARDWARE_PROFILES_TABLE,
    PG_SCHEMA,
    PROBE_RESULTS_INDEXES,
    PROBE_RESULTS_TABLE,
    SCHEMA_VERSION,
    _migrate_to_v1,
    ensure_schema,
    get_table_stats,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_schema_version(self):
        assert SCHEMA_VERSION == 1

    def test_pg_schema(self):
        assert PG_SCHEMA == "telemetry"

    def test_events_table_ddl_contains_create(self):
        assert "CREATE TABLE" in EVENTS_TABLE
        assert "events" in EVENTS_TABLE
        assert "event_id" in EVENTS_TABLE
        assert "message_tsv" in EVENTS_TABLE

    def test_events_indexes_count(self):
        assert len(EVENTS_INDEXES) >= 8
        for idx in EVENTS_INDEXES:
            assert "CREATE INDEX" in idx

    def test_probe_results_table_ddl(self):
        assert "CREATE TABLE" in PROBE_RESULTS_TABLE
        assert "probe_results" in PROBE_RESULTS_TABLE

    def test_probe_results_indexes(self):
        assert len(PROBE_RESULTS_INDEXES) >= 1

    def test_forecast_model_cache_table(self):
        assert "CREATE TABLE" in FORECAST_MODEL_CACHE_TABLE
        assert "forecast_model_cache" in FORECAST_MODEL_CACHE_TABLE

    def test_correlation_alerts_table(self):
        assert "CREATE TABLE" in CORRELATION_ALERTS_TABLE
        assert "correlation_alerts" in CORRELATION_ALERTS_TABLE

    def test_hardware_profiles_table(self):
        assert "CREATE TABLE" in HARDWARE_PROFILES_TABLE
        assert "hardware_profiles" in HARDWARE_PROFILES_TABLE


# ---------------------------------------------------------------------------
# _migrate_to_v1
# ---------------------------------------------------------------------------


class TestMigrateToV1:
    def test_executes_all_ddl(self):
        """_migrate_to_v1 should execute all table CREATE and index statements."""
        conn = MagicMock()

        _migrate_to_v1(conn)

        # Expected calls:
        # 1 EVENTS_TABLE + len(EVENTS_INDEXES) + 1 PROBE_RESULTS_TABLE
        # + len(PROBE_RESULTS_INDEXES) + 1 FORECAST_MODEL_CACHE_TABLE
        # + 1 CORRELATION_ALERTS_TABLE + 1 HARDWARE_PROFILES_TABLE
        expected_count = (
            1  # events table
            + len(EVENTS_INDEXES)
            + 1  # probe_results table
            + len(PROBE_RESULTS_INDEXES)
            + 1  # forecast_model_cache
            + 1  # correlation_alerts
            + 1  # hardware_profiles
        )
        assert conn.execute.call_count == expected_count

    def test_executes_events_table_first(self):
        conn = MagicMock()
        _migrate_to_v1(conn)
        first_call = conn.execute.call_args_list[0]
        assert "events" in first_call[0][0].lower()

    def test_executes_all_events_indexes(self):
        conn = MagicMock()
        _migrate_to_v1(conn)
        executed_sqls = [c[0][0] for c in conn.execute.call_args_list]
        for idx_sql in EVENTS_INDEXES:
            assert idx_sql in executed_sqls

    def test_executes_probe_results_indexes(self):
        conn = MagicMock()
        _migrate_to_v1(conn)
        executed_sqls = [c[0][0] for c in conn.execute.call_args_list]
        for idx_sql in PROBE_RESULTS_INDEXES:
            assert idx_sql in executed_sqls


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    def test_delegates_to_run_migrations(self):
        conn = MagicMock()
        with patch("elle.storage.migrate.run_migrations") as mock_run:
            ensure_schema(conn)
            mock_run.assert_called_once_with(conn, "telemetry")


# ---------------------------------------------------------------------------
# get_table_stats
# ---------------------------------------------------------------------------


class TestGetTableStats:
    def test_returns_counts_for_both_tables(self):
        conn = MagicMock()
        # Simulate successful COUNT queries
        mock_result = MagicMock()
        mock_result.fetchone.return_value = {"cnt": 42}
        conn.execute.return_value = mock_result

        stats = get_table_stats(conn)

        assert "events" in stats
        assert "probe_results" in stats
        assert stats["events"] == 42
        assert stats["probe_results"] == 42

    def test_returns_zero_when_fetchone_is_none(self):
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        conn.execute.return_value = mock_result

        stats = get_table_stats(conn)

        assert stats["events"] == 0
        assert stats["probe_results"] == 0

    def test_returns_zero_on_exception(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("table does not exist")

        stats = get_table_stats(conn)

        assert stats["events"] == 0
        assert stats["probe_results"] == 0

    def test_mixed_success_and_failure(self):
        """One table succeeds, another raises."""
        conn = MagicMock()
        call_count = 0

        def side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            if "events" in sql:
                result = MagicMock()
                result.fetchone.return_value = {"cnt": 100}
                return result
            raise Exception("probe_results not found")

        conn.execute.side_effect = side_effect

        stats = get_table_stats(conn)

        assert stats["events"] == 100
        assert stats["probe_results"] == 0
