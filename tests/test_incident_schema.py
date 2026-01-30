"""Tests for Incident Vault schema."""

from __future__ import annotations

import pytest

from elle.daemon.incidents.schema import (
    SCHEMA_VERSION,
    drop_all_tables,
    ensure_schema,
    get_schema_version,
    init_incident_schema,
    needs_migration,
)


@pytest.fixture
def conn(incidents_conn):
    """Provide an incidents-schema connection for testing."""
    return incidents_conn


class TestSchemaCreation:
    """Tests for schema creation."""

    def test_init_creates_incidents_table(self, conn):
        """Test that init creates the incidents table."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'incidents'"
        ).fetchone()
        assert row is not None

    def test_init_creates_actions_table(self, conn):
        """Test that init creates the incident_actions table."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'incident_actions'"
        ).fetchone()
        assert row is not None

    def test_init_creates_snapshots_table(self, conn):
        """Test that init creates the incident_snapshots table."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'incident_snapshots'"
        ).fetchone()
        assert row is not None

    def test_init_creates_events_table(self, conn):
        """Test that init creates the incident_events table."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'incident_events'"
        ).fetchone()
        assert row is not None

    def test_init_creates_embeddings_table(self, conn):
        """Test that init creates the incident_embeddings table."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'incident_embeddings'"
        ).fetchone()
        assert row is not None

    def test_init_creates_fts_index(self, conn):
        """Test that init creates the full-text search index."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'incidents_fts_idx'"
        ).fetchone()
        assert row is not None

    def test_init_creates_meta_table(self, conn):
        """Test that init creates the meta table."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'meta'"
        ).fetchone()
        assert row is not None


class TestSchemaVersion:
    """Tests for schema versioning."""

    def test_schema_version_set(self, conn):
        """Test that schema version is set after init."""
        init_incident_schema(conn)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION

    def test_schema_version_none_before_init(self, conn):
        """Test that schema version is None before init."""
        version = get_schema_version(conn)
        assert version is None

    def test_needs_migration_before_init(self, conn):
        """Test that migration is needed before init."""
        assert needs_migration(conn) is True

    def test_no_migration_after_init(self, conn):
        """Test that migration is not needed after init."""
        init_incident_schema(conn)
        assert needs_migration(conn) is False


class TestEnsureSchema:
    """Tests for ensure_schema function."""

    def test_ensure_creates_schema(self, conn):
        """Test that ensure_schema creates the schema."""
        ensure_schema(conn)

        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM information_schema.tables "
            "WHERE table_schema = 'incidents'"
        ).fetchone()
        assert row["cnt"] > 0

    def test_ensure_idempotent(self, conn):
        """Test that ensure_schema is idempotent."""
        ensure_schema(conn)
        ensure_schema(conn)  # Should not raise

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION


class TestDropTables:
    """Tests for dropping tables."""

    def test_drop_all_tables(self, conn):
        """Test that drop_all_tables removes all tables."""
        init_incident_schema(conn)
        drop_all_tables(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'incidents'"
        ).fetchone()
        assert row is None


class TestIndexes:
    """Tests for indexes."""

    def test_indexes_created(self, conn):
        """Test that indexes are created."""
        init_incident_schema(conn)

        rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'incidents'").fetchall()
        indexes = [row["indexname"] for row in rows]

        assert "idx_incidents_status" in indexes
        assert "idx_incidents_domain" in indexes
        assert "idx_actions_incident" in indexes

    def test_v4_indexes_created(self, conn):
        """Test that V4 indexes are created."""
        init_incident_schema(conn)

        rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'incidents'").fetchall()
        indexes = [row["indexname"] for row in rows]

        assert "idx_telemetry_snapshots_incident" in indexes
        assert "idx_control_surface_snapshots_incident" in indexes
        assert "idx_control_surface_hashes" in indexes


class TestV4Tables:
    """Tests for V4 schema tables (telemetry/control surface)."""

    def test_telemetry_snapshots_table_created(self, conn):
        """Test that telemetry_snapshots table is created."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'telemetry_snapshots'"
        ).fetchone()
        assert row is not None

    def test_control_surface_snapshots_table_created(self, conn):
        """Test that control_surface_snapshots table is created."""
        init_incident_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'control_surface_snapshots'"
        ).fetchone()
        assert row is not None

    def test_telemetry_snapshots_columns(self, conn):
        """Test that telemetry_snapshots has expected columns."""
        init_incident_schema(conn)

        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'telemetry_snapshots'"
        ).fetchall()
        columns = {row["column_name"] for row in rows}

        assert "id" in columns
        assert "incident_id" in columns
        assert "which" in columns
        assert "snapshot_json" in columns
        assert "collected_at" in columns

    def test_control_surface_snapshots_columns(self, conn):
        """Test that control_surface_snapshots has expected columns."""
        init_incident_schema(conn)

        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'control_surface_snapshots'"
        ).fetchall()
        columns = {row["column_name"] for row in rows}

        assert "id" in columns
        assert "incident_id" in columns
        assert "which" in columns
        assert "snapshot_json" in columns
        assert "surface_hashes" in columns
        assert "collected_at" in columns

    def test_telemetry_which_constraint(self, conn):
        """Test that telemetry_snapshots enforces which constraint."""
        init_incident_schema(conn)

        # First create an incident
        conn.execute(
            """
            INSERT INTO incidents (id, created_at, updated_at, title)
            VALUES ('test-id', '2024-01-01', '2024-01-01', 'Test')
            """
        )

        # Valid insert
        conn.execute(
            """
            INSERT INTO telemetry_snapshots (incident_id, which, snapshot_json, collected_at)
            VALUES ('test-id', 'pre', '{}', '2024-01-01')
            """
        )

        # Invalid 'which' value should fail
        import psycopg.errors
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                INSERT INTO telemetry_snapshots (incident_id, which, snapshot_json, collected_at)
                VALUES ('test-id', 'invalid', '{}', '2024-01-01')
                """
            )

    def test_control_surface_which_constraint(self, conn):
        """Test that control_surface_snapshots enforces which constraint."""
        init_incident_schema(conn)

        # First create an incident
        conn.execute(
            """
            INSERT INTO incidents (id, created_at, updated_at, title)
            VALUES ('test-id', '2024-01-01', '2024-01-01', 'Test')
            """
        )

        # Valid insert
        conn.execute(
            """
            INSERT INTO control_surface_snapshots
            (incident_id, which, snapshot_json, surface_hashes, collected_at)
            VALUES ('test-id', 'post', '{}', '{}', '2024-01-01')
            """
        )

        # Invalid 'which' value should fail
        import psycopg.errors
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                INSERT INTO control_surface_snapshots
                (incident_id, which, snapshot_json, surface_hashes, collected_at)
                VALUES ('test-id', 'invalid', '{}', '{}', '2024-01-01')
                """
            )


class TestTriggers:
    """Tests for full-text search triggers."""

    def test_fts_trigger_on_insert(self, conn):
        """Test that FTS tsvector is updated on insert."""
        init_incident_schema(conn)

        conn.execute(
            """
            INSERT INTO incidents (
                id, created_at, updated_at, title, summary
            ) VALUES (
                'test-id', '2024-01-01', '2024-01-01', 'Test Title', 'Test summary'
            )
            """
        )

        # Check tsvector full-text search
        result = conn.execute(
            "SELECT id FROM incidents WHERE fts @@ plainto_tsquery('english', 'Test')"
        ).fetchone()
        assert result is not None

    def test_fts_trigger_on_delete(self, conn):
        """Test that row removal clears FTS matches."""
        init_incident_schema(conn)

        conn.execute(
            """
            INSERT INTO incidents (
                id, created_at, updated_at, title, summary
            ) VALUES (
                'test-id', '2024-01-01', '2024-01-01', 'UniqueTitle', 'Summary'
            )
            """
        )

        conn.execute("DELETE FROM incidents WHERE id = 'test-id'")

        # Check FTS - should not find
        result = conn.execute(
            "SELECT id FROM incidents WHERE fts @@ plainto_tsquery('english', 'UniqueTitle')"
        ).fetchone()
        assert result is None
