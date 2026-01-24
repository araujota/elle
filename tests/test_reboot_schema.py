"""Tests for reboot module SQLite schema."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from elle.daemon.reboot.schema import (
    SCHEMA_VERSION,
    drop_all_tables,
    ensure_schema,
    get_connection,
    get_schema_version,
    init_reboot_schema,
    needs_migration,
)


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def conn(temp_db):
    """Create a connection to temporary database."""
    connection = get_connection(temp_db)
    yield connection
    connection.close()


class TestSchemaInitialization:
    """Tests for schema initialization."""

    def test_init_schema_creates_tables(self, conn):
        """Test that init creates all required tables."""
        init_reboot_schema(conn)

        cursor = conn.cursor()

        # Check reboot_intents table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reboot_intents'"
        )
        assert cursor.fetchone() is not None

        # Check pending_verifications table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_verifications'"
        )
        assert cursor.fetchone() is not None

        # Check meta table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
        assert cursor.fetchone() is not None

    def test_init_schema_creates_indexes(self, conn):
        """Test that init creates indexes."""
        init_reboot_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = [row[0] for row in cursor.fetchall()]

        # Check key indexes exist
        assert "idx_intents_status" in indexes
        assert "idx_intents_boot_id" in indexes
        assert "idx_verifications_intent" in indexes

    def test_init_schema_sets_version(self, conn):
        """Test that init sets schema version."""
        init_reboot_schema(conn)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION

    def test_init_schema_idempotent(self, conn):
        """Test that init can be called multiple times safely."""
        init_reboot_schema(conn)
        init_reboot_schema(conn)
        init_reboot_schema(conn)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION


class TestSchemaVersion:
    """Tests for schema version management."""

    def test_get_version_before_init(self, conn):
        """Test getting version before schema is initialized."""
        version = get_schema_version(conn)
        assert version is None

    def test_get_version_after_init(self, conn):
        """Test getting version after initialization."""
        init_reboot_schema(conn)
        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION

    def test_needs_migration_before_init(self, conn):
        """Test needs_migration before initialization."""
        assert needs_migration(conn) is True

    def test_needs_migration_after_init(self, conn):
        """Test needs_migration after initialization."""
        init_reboot_schema(conn)
        assert needs_migration(conn) is False


class TestEnsureSchema:
    """Tests for ensure_schema function."""

    def test_ensure_schema_initializes(self, conn):
        """Test that ensure_schema initializes if needed."""
        ensure_schema(conn)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION

    def test_ensure_schema_idempotent(self, conn):
        """Test that ensure_schema is idempotent."""
        ensure_schema(conn)
        ensure_schema(conn)
        ensure_schema(conn)

        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION


class TestDropAllTables:
    """Tests for drop_all_tables function."""

    def test_drop_tables(self, conn):
        """Test dropping all tables."""
        init_reboot_schema(conn)

        # Verify tables exist
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reboot_intents'"
        )
        assert cursor.fetchone() is not None

        # Drop all
        drop_all_tables(conn)

        # Verify tables are gone
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reboot_intents'"
        )
        assert cursor.fetchone() is None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_verifications'"
        )
        assert cursor.fetchone() is None

    def test_drop_tables_idempotent(self, conn):
        """Test that drop is idempotent."""
        drop_all_tables(conn)
        drop_all_tables(conn)  # Should not raise


class TestTableStructure:
    """Tests for table structure."""

    def test_intents_columns(self, conn):
        """Test reboot_intents table has expected columns."""
        init_reboot_schema(conn)

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(reboot_intents)")
        columns = {row[1] for row in cursor.fetchall()}

        expected = {
            "id", "incident_id", "created_at", "updated_at",
            "goal", "task_description", "reason", "reason_detail",
            "session_history_json", "plan_json", "status",
            "pre_snapshot_json", "boot_id",
            "grub_entry", "grub_default_saved", "grub_state_json",
            "post_snapshot_json", "new_boot_id",
            "verification_attempts", "last_verification_at",
            "outcome", "outcome_detail"
        }
        assert expected.issubset(columns)

    def test_verifications_columns(self, conn):
        """Test pending_verifications table has expected columns."""
        init_reboot_schema(conn)

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(pending_verifications)")
        columns = {row[1] for row in cursor.fetchall()}

        expected = {
            "id", "reboot_intent_id", "check_type", "check_command",
            "expected_exit_code", "expected_output_contains",
            "step_index", "required",
            "executed_at", "exit_code", "stdout", "stderr", "passed"
        }
        assert expected.issubset(columns)

    def test_foreign_key_constraint(self, conn):
        """Test that foreign key constraint is enforced."""
        init_reboot_schema(conn)

        cursor = conn.cursor()

        # Try to insert verification with non-existent intent
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO pending_verifications
                (reboot_intent_id, step_index, check_type, check_command)
                VALUES ('non-existent', 0, 'command', 'true')
                """
            )
            conn.commit()


class TestStatusConstraints:
    """Tests for status column constraints."""

    def test_valid_status_values(self, conn):
        """Test that valid status values are accepted."""
        init_reboot_schema(conn)

        cursor = conn.cursor()
        valid_statuses = [
            "pending", "rebooting", "verifying",
            "completed", "failed", "rolled_back", "cancelled"
        ]

        for i, status in enumerate(valid_statuses):
            cursor.execute(
                """
                INSERT INTO reboot_intents (id, created_at, updated_at, goal, task_description, reason, status)
                VALUES (?, datetime('now'), datetime('now'), 'Test', 'Test', 'user_requested', ?)
                """,
                (f"test-{i}", status)
            )
        conn.commit()

    def test_invalid_status_rejected(self, conn):
        """Test that invalid status values are rejected."""
        init_reboot_schema(conn)

        cursor = conn.cursor()

        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO reboot_intents (id, created_at, updated_at, goal, task_description, reason, status)
                VALUES ('test', datetime('now'), datetime('now'), 'Test', 'Test', 'user_requested', 'invalid_status')
                """
            )
            conn.commit()

    def test_valid_outcome_values(self, conn):
        """Test that valid outcome values are accepted."""
        init_reboot_schema(conn)

        cursor = conn.cursor()
        valid_outcomes = ["improved", "failed", "rolled_back", "unknown"]

        for i, outcome in enumerate(valid_outcomes):
            cursor.execute(
                """
                INSERT INTO reboot_intents (id, created_at, updated_at, goal, task_description, reason, outcome)
                VALUES (?, datetime('now'), datetime('now'), 'Test', 'Test', 'user_requested', ?)
                """,
                (f"test-{i}", outcome)
            )
        conn.commit()

    def test_valid_check_type_values(self, conn):
        """Test that valid check_type values are accepted."""
        init_reboot_schema(conn)

        cursor = conn.cursor()

        # First create an intent
        cursor.execute(
            """
            INSERT INTO reboot_intents (id, created_at, updated_at, goal, task_description, reason)
            VALUES ('test-intent', datetime('now'), datetime('now'), 'Test', 'Test', 'user_requested')
            """
        )

        valid_types = ["command", "service_active", "file_exists", "port_listening"]

        for i, check_type in enumerate(valid_types):
            cursor.execute(
                """
                INSERT INTO pending_verifications
                (reboot_intent_id, step_index, check_type, check_command)
                VALUES ('test-intent', ?, ?, 'test')
                """,
                (i, check_type)
            )
        conn.commit()


class TestCascadeDelete:
    """Tests for cascade delete behavior."""

    def test_deleting_intent_deletes_verifications(self, conn):
        """Test that deleting an intent cascades to verifications."""
        init_reboot_schema(conn)

        cursor = conn.cursor()

        # Create intent
        cursor.execute(
            """
            INSERT INTO reboot_intents (id, created_at, updated_at, goal, task_description, reason)
            VALUES ('test-intent', datetime('now'), datetime('now'), 'Test', 'Test', 'user_requested')
            """
        )

        # Create verification
        cursor.execute(
            """
            INSERT INTO pending_verifications
            (reboot_intent_id, step_index, check_type, check_command)
            VALUES ('test-intent', 0, 'command', 'true')
            """
        )
        conn.commit()

        # Verify verification exists
        cursor.execute("SELECT COUNT(*) FROM pending_verifications")
        assert cursor.fetchone()[0] == 1

        # Delete intent
        cursor.execute("DELETE FROM reboot_intents WHERE id = 'test-intent'")
        conn.commit()

        # Verify verification is also deleted
        cursor.execute("SELECT COUNT(*) FROM pending_verifications")
        assert cursor.fetchone()[0] == 0
