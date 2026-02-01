"""Tests for schema.py to boost coverage.

Tests table DDL constants, migration function, and helper functions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.schema import (
    ACTIONS_TABLE,
    CLOUD_SUBMISSION_QUEUE_TABLE,
    CONFIG_STATES_TABLE,
    CONTROL_SURFACE_SNAPSHOTS_TABLE,
    DECISION_RECORDS_TABLE,
    DOMAIN_EFFICACY_TABLE,
    EMBEDDINGS_TABLE,
    ENTITY_EFFICACY_TABLE,
    EVENTS_TABLE,
    INCIDENTS_TABLE,
    INDEXES,
    PG_SCHEMA,
    SCHEMA_VERSION,
    SEARCH_TSV_TRIGGER,
    SEARCH_TSV_TRIGGER_FN,
    SNAPSHOTS_TABLE,
    SOLUTION_APPROACH_EFFICACY_TABLE,
    TELEMETRY_SNAPSHOTS_TABLE,
    _migrate_to_v6,
    drop_all_tables,
    ensure_schema,
)


class TestSchemaConstants:
    @pytest.mark.timeout(10)
    def test_schema_version(self):
        assert SCHEMA_VERSION == 6

    @pytest.mark.timeout(10)
    def test_pg_schema(self):
        assert PG_SCHEMA == "incidents"

    @pytest.mark.timeout(10)
    def test_incidents_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS incidents" in INCIDENTS_TABLE
        assert "id TEXT PRIMARY KEY" in INCIDENTS_TABLE
        assert "search_tsv TSVECTOR" in INCIDENTS_TABLE
        assert "forecast_metric TEXT" in INCIDENTS_TABLE

    @pytest.mark.timeout(10)
    def test_actions_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS incident_actions" in ACTIONS_TABLE
        assert "REFERENCES incidents(id)" in ACTIONS_TABLE

    @pytest.mark.timeout(10)
    def test_snapshots_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS incident_snapshots" in SNAPSHOTS_TABLE

    @pytest.mark.timeout(10)
    def test_events_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS incident_events" in EVENTS_TABLE

    @pytest.mark.timeout(10)
    def test_embeddings_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS incident_embeddings" in EMBEDDINGS_TABLE
        assert "vector(768)" in EMBEDDINGS_TABLE

    @pytest.mark.timeout(10)
    def test_decision_records_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS decision_records" in DECISION_RECORDS_TABLE

    @pytest.mark.timeout(10)
    def test_config_states_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS config_states" in CONFIG_STATES_TABLE

    @pytest.mark.timeout(10)
    def test_domain_efficacy_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS domain_efficacy" in DOMAIN_EFFICACY_TABLE

    @pytest.mark.timeout(10)
    def test_entity_efficacy_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS entity_efficacy" in ENTITY_EFFICACY_TABLE

    @pytest.mark.timeout(10)
    def test_solution_approach_efficacy_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS solution_approach_efficacy" in SOLUTION_APPROACH_EFFICACY_TABLE

    @pytest.mark.timeout(10)
    def test_telemetry_snapshots_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS telemetry_snapshots" in TELEMETRY_SNAPSHOTS_TABLE

    @pytest.mark.timeout(10)
    def test_control_surface_snapshots_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS control_surface_snapshots" in CONTROL_SURFACE_SNAPSHOTS_TABLE

    @pytest.mark.timeout(10)
    def test_cloud_submission_queue_table_ddl(self):
        assert "CREATE TABLE IF NOT EXISTS cloud_submission_queue" in CLOUD_SUBMISSION_QUEUE_TABLE

    @pytest.mark.timeout(10)
    def test_search_tsv_trigger_fn(self):
        assert "incidents_search_tsv_update" in SEARCH_TSV_TRIGGER_FN
        assert "to_tsvector" in SEARCH_TSV_TRIGGER_FN

    @pytest.mark.timeout(10)
    def test_search_tsv_trigger(self):
        assert "incidents_search_tsv_trg" in SEARCH_TSV_TRIGGER

    @pytest.mark.timeout(10)
    def test_indexes_list(self):
        assert len(INDEXES) > 0
        for idx in INDEXES:
            assert idx.startswith("CREATE INDEX IF NOT EXISTS")


class TestMigrateToV6:
    @pytest.mark.timeout(10)
    def test_creates_all_tables_and_indexes(self):
        conn = MagicMock()
        _migrate_to_v6(conn)

        # Count the execute calls
        call_count = conn.execute.call_count
        # 13 tables + trigger fn + drop trigger + trigger + N indexes
        expected_min = 13 + 3 + len(INDEXES)
        assert call_count >= expected_min

        # Verify key table creation calls
        all_sql = [str(c) for c in conn.execute.call_args_list]
        sql_joined = " ".join(all_sql)
        assert "incidents" in sql_joined
        assert "incident_actions" in sql_joined
        assert "incident_snapshots" in sql_joined


class TestDropAllTables:
    @pytest.mark.timeout(10)
    def test_drops_tables(self):
        conn = MagicMock()
        drop_all_tables(conn)

        # Should drop trigger, function, and all tables
        assert conn.execute.call_count >= 14  # trigger + fn + 12 tables + _meta
        conn.commit.assert_called_once()

        # Check specific DROP calls
        all_sql = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("DROP TRIGGER" in s for s in all_sql)
        assert any("DROP FUNCTION" in s for s in all_sql)
        assert any("incidents" in s for s in all_sql)


class TestEnsureSchema:
    @pytest.mark.timeout(10)
    def test_calls_run_migrations(self):
        conn = MagicMock()
        with patch("elle.storage.migrate.run_migrations") as mock_run:
            ensure_schema(conn)
            mock_run.assert_called_once_with(conn, PG_SCHEMA)
