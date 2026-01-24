"""Tests for Incident Vault schema."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from elle.daemon.incidents.schema import (
    SCHEMA_VERSION,
    drop_all_tables,
    ensure_schema,
    get_connection,
    get_schema_version,
    init_incident_schema,
    needs_migration,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    yield conn, db_path

    conn.close()
    db_path.unlink()


class TestSchemaCreation:
    """Tests for schema creation."""

    def test_init_creates_incidents_table(self, temp_db):
        """Test that init creates the incidents table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'"
        )
        assert cursor.fetchone() is not None

    def test_init_creates_actions_table(self, temp_db):
        """Test that init creates the incident_actions table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incident_actions'"
        )
        assert cursor.fetchone() is not None

    def test_init_creates_snapshots_table(self, temp_db):
        """Test that init creates the incident_snapshots table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incident_snapshots'"
        )
        assert cursor.fetchone() is not None

    def test_init_creates_events_table(self, temp_db):
        """Test that init creates the incident_events table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incident_events'"
        )
        assert cursor.fetchone() is not None

    def test_init_creates_embeddings_table(self, temp_db):
        """Test that init creates the incident_embeddings table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incident_embeddings'"
        )
        assert cursor.fetchone() is not None

    def test_init_creates_fts_table(self, temp_db):
        """Test that init creates the FTS5 table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents_fts'"
        )
        assert cursor.fetchone() is not None

    def test_init_creates_meta_table(self, temp_db):
        """Test that init creates the meta table."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
        assert cursor.fetchone() is not None


class TestSchemaVersion:
    """Tests for schema versioning."""

    def test_schema_version_set(self, temp_db):
        """Test that schema version is set after init."""
        conn, _ = temp_db
        init_incident_schema(conn)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION

    def test_schema_version_none_before_init(self, temp_db):
        """Test that schema version is None before init."""
        conn, _ = temp_db
        version = get_schema_version(conn)
        assert version is None

    def test_needs_migration_before_init(self, temp_db):
        """Test that migration is needed before init."""
        conn, _ = temp_db
        assert needs_migration(conn) is True

    def test_no_migration_after_init(self, temp_db):
        """Test that migration is not needed after init."""
        conn, _ = temp_db
        init_incident_schema(conn)
        assert needs_migration(conn) is False


class TestEnsureSchema:
    """Tests for ensure_schema function."""

    def test_ensure_creates_schema(self, temp_db):
        """Test that ensure_schema creates the schema."""
        conn, _ = temp_db
        ensure_schema(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        count = cursor.fetchone()[0]
        assert count > 0

    def test_ensure_idempotent(self, temp_db):
        """Test that ensure_schema is idempotent."""
        conn, _ = temp_db
        ensure_schema(conn)
        ensure_schema(conn)  # Should not raise

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION


class TestDropTables:
    """Tests for dropping tables."""

    def test_drop_all_tables(self, temp_db):
        """Test that drop_all_tables removes all tables."""
        conn, _ = temp_db
        init_incident_schema(conn)
        drop_all_tables(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'"
        )
        assert cursor.fetchone() is None


class TestIndexes:
    """Tests for indexes."""

    def test_indexes_created(self, temp_db):
        """Test that indexes are created."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cursor.fetchall()]

        assert "idx_incidents_status" in indexes
        assert "idx_incidents_domain" in indexes
        assert "idx_actions_incident" in indexes


class TestTriggers:
    """Tests for FTS triggers."""

    def test_fts_trigger_on_insert(self, temp_db):
        """Test that FTS is updated on insert."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO incidents (
                id, created_at, updated_at, title, summary
            ) VALUES (
                'test-id', '2024-01-01', '2024-01-01', 'Test Title', 'Test summary'
            )
            """
        )
        conn.commit()

        # Check FTS
        cursor.execute(
            "SELECT * FROM incidents_fts WHERE incidents_fts MATCH 'Test'"
        )
        result = cursor.fetchone()
        assert result is not None

    def test_fts_trigger_on_delete(self, temp_db):
        """Test that FTS is updated on delete."""
        conn, _ = temp_db
        init_incident_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO incidents (
                id, created_at, updated_at, title, summary
            ) VALUES (
                'test-id', '2024-01-01', '2024-01-01', 'UniqueTitle', 'Summary'
            )
            """
        )
        conn.commit()

        cursor.execute("DELETE FROM incidents WHERE id = 'test-id'")
        conn.commit()

        # Check FTS - should not find
        cursor.execute(
            "SELECT * FROM incidents_fts WHERE incidents_fts MATCH 'UniqueTitle'"
        )
        result = cursor.fetchone()
        assert result is None
