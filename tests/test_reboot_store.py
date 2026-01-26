"""Tests for reboot module CRUD operations."""

import tempfile
from pathlib import Path

import pytest

from elle.daemon.reboot.models import GRUBState, PendingVerification
from elle.daemon.reboot.schema import get_connection, init_reboot_schema
from elle.daemon.reboot.store import (
    add_verification,
    cancel_intent,
    create_intent,
    delete_intent,
    get_active_intent,
    get_intent,
    get_intent_count,
    get_intents_by_status,
    get_reboot_status,
    get_verifications,
    list_intents,
    list_recent_intents,
    mark_rebooting,
    reset_verifications,
    update_intent_status,
    update_verification_result,
)


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def conn(temp_db):
    """Create a connection to temporary database with schema."""
    connection = get_connection(temp_db)
    init_reboot_schema(connection)
    yield connection
    connection.close()


class TestCreateIntent:
    """Tests for create_intent function."""

    def test_create_minimal_intent(self, conn):
        """Test creating intent with minimal fields."""
        intent = create_intent(
            goal="Test reboot",
            task_description="Testing reboot persistence",
            reason="user_requested",
            conn=conn,
        )

        assert intent.id is not None
        assert intent.goal == "Test reboot"
        assert intent.task_description == "Testing reboot persistence"
        assert intent.reason == "user_requested"
        assert intent.status == "pending"
        assert intent.outcome == "unknown"

    def test_create_full_intent(self, conn):
        """Test creating intent with all fields."""
        grub_state = GRUBState(
            default_entry="0",
            entries=("Ubuntu",),
        )
        verifications = [
            PendingVerification(
                step_index=0,
                check_type="command",
                check_command="uname -r",
            ),
            PendingVerification(
                step_index=1,
                check_type="service_active",
                check_command="NetworkManager",
            ),
        ]

        intent = create_intent(
            goal="Kernel update",
            task_description="Update kernel to 6.8",
            reason="kernel_update",
            reason_detail="Security patches",
            incident_id="incident-123",
            session_history=["apt update", "apt upgrade"],
            plan_json={"steps": ["download", "install", "reboot"]},
            pre_snapshot_json={"os": "Ubuntu 24.04"},
            boot_id="boot-123",
            grub_entry="0",
            grub_default_saved="0",
            grub_state=grub_state,
            verifications=verifications,
            conn=conn,
        )

        assert intent.incident_id == "incident-123"
        assert intent.reason_detail == "Security patches"
        assert len(intent.session_history) == 2
        assert intent.boot_id == "boot-123"
        assert intent.grub_state is not None
        assert len(intent.verifications) == 2

    def test_create_intent_auto_id(self, conn):
        """Test that intent gets auto-generated UUID."""
        intent1 = create_intent(
            goal="Test 1",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        intent2 = create_intent(
            goal="Test 2",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        assert intent1.id != intent2.id
        assert len(intent1.id) == 36  # UUID format


class TestGetIntent:
    """Tests for get_intent function."""

    def test_get_existing_intent(self, conn):
        """Test getting an existing intent."""
        created = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        retrieved = get_intent(created.id, conn=conn)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.goal == created.goal

    def test_get_nonexistent_intent(self, conn):
        """Test getting a non-existent intent."""
        retrieved = get_intent("nonexistent-id", conn=conn)
        assert retrieved is None

    def test_get_intent_with_verifications(self, conn):
        """Test that get_intent includes verifications."""
        created = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            verifications=[
                PendingVerification(
                    step_index=0,
                    check_type="command",
                    check_command="true",
                ),
            ],
            conn=conn,
        )

        retrieved = get_intent(created.id, conn=conn)
        assert len(retrieved.verifications) == 1


class TestUpdateIntentStatus:
    """Tests for update_intent_status function."""

    def test_update_status(self, conn):
        """Test updating intent status."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        updated = update_intent_status(
            intent.id,
            status="rebooting",
            conn=conn,
        )

        assert updated is not None
        assert updated.status == "rebooting"
        assert updated.updated_at > intent.updated_at

    def test_update_with_outcome(self, conn):
        """Test updating status with outcome."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        updated = update_intent_status(
            intent.id,
            status="completed",
            outcome="improved",
            outcome_detail="All checks passed",
            conn=conn,
        )

        assert updated.status == "completed"
        assert updated.outcome == "improved"
        assert updated.outcome_detail == "All checks passed"

    def test_update_with_verification_attempts(self, conn):
        """Test updating verification attempts."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        updated = update_intent_status(
            intent.id,
            status="verifying",
            verification_attempts=2,
            conn=conn,
        )

        assert updated.verification_attempts == 2
        assert updated.last_verification_at is not None

    def test_update_nonexistent_intent(self, conn):
        """Test updating non-existent intent."""
        result = update_intent_status(
            "nonexistent",
            status="rebooting",
            conn=conn,
        )
        assert result is None


class TestMarkRebooting:
    """Tests for mark_rebooting function."""

    def test_mark_rebooting(self, conn):
        """Test marking intent as rebooting."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        updated = mark_rebooting(intent.id, "new-boot-id", conn=conn)

        assert updated.status == "rebooting"
        assert updated.boot_id == "new-boot-id"


class TestCancelIntent:
    """Tests for cancel_intent function."""

    def test_cancel_pending_intent(self, conn):
        """Test cancelling a pending intent."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        cancelled = cancel_intent(intent.id, conn=conn)

        assert cancelled is not None
        assert cancelled.status == "cancelled"

    def test_cancel_non_pending_fails(self, conn):
        """Test that cancelling non-pending intent fails."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        update_intent_status(intent.id, status="rebooting", conn=conn)

        cancelled = cancel_intent(intent.id, conn=conn)
        assert cancelled is None


class TestDeleteIntent:
    """Tests for delete_intent function."""

    def test_delete_intent(self, conn):
        """Test deleting an intent."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        result = delete_intent(intent.id, conn=conn)
        assert result is True

        retrieved = get_intent(intent.id, conn=conn)
        assert retrieved is None

    def test_delete_nonexistent(self, conn):
        """Test deleting non-existent intent."""
        result = delete_intent("nonexistent", conn=conn)
        assert result is False


class TestGetIntentsByStatus:
    """Tests for get_intents_by_status function."""

    def test_get_by_status(self, conn):
        """Test getting intents by status."""
        # Create intents with different statuses
        create_intent(
            goal="Pending 1",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        create_intent(
            goal="Pending 2",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        intent3 = create_intent(
            goal="Rebooting",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        update_intent_status(intent3.id, status="rebooting", conn=conn)

        pending = get_intents_by_status("pending", conn=conn)
        rebooting = get_intents_by_status("rebooting", conn=conn)

        assert len(pending) == 2
        assert len(rebooting) == 1
        assert rebooting[0].goal == "Rebooting"


class TestGetActiveIntent:
    """Tests for get_active_intent function."""

    def test_no_active_intent(self, conn):
        """Test when no active intent exists."""
        create_intent(
            goal="Pending",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        active = get_active_intent(conn=conn)
        assert active is None

    def test_rebooting_is_active(self, conn):
        """Test that rebooting status counts as active."""
        intent = create_intent(
            goal="Rebooting",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        mark_rebooting(intent.id, "boot-id", conn=conn)

        active = get_active_intent(conn=conn)
        assert active is not None
        assert active.status == "rebooting"

    def test_verifying_is_active(self, conn):
        """Test that verifying status counts as active."""
        intent = create_intent(
            goal="Verifying",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        update_intent_status(intent.id, status="verifying", conn=conn)

        active = get_active_intent(conn=conn)
        assert active is not None
        assert active.status == "verifying"


class TestListIntents:
    """Tests for list_intents function."""

    def test_list_all(self, conn):
        """Test listing all intents."""
        for i in range(5):
            create_intent(
                goal=f"Test {i}",
                task_description="Test",
                reason="user_requested",
                conn=conn,
            )

        intents = list_intents(conn=conn)
        assert len(intents) == 5

    def test_list_with_limit(self, conn):
        """Test listing with limit."""
        for i in range(5):
            create_intent(
                goal=f"Test {i}",
                task_description="Test",
                reason="user_requested",
                conn=conn,
            )

        intents = list_intents(limit=3, conn=conn)
        assert len(intents) == 3

    def test_list_with_filter(self, conn):
        """Test listing with status filter."""
        create_intent(
            goal="Pending",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        intent2 = create_intent(
            goal="Completed",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        update_intent_status(intent2.id, status="completed", outcome="improved", conn=conn)

        completed = list_intents(status="completed", conn=conn)
        assert len(completed) == 1
        assert completed[0].goal == "Completed"


class TestListRecentIntents:
    """Tests for list_recent_intents function."""

    def test_list_recent(self, conn):
        """Test listing recent intents."""
        create_intent(
            goal="Recent",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        recent = list_recent_intents(days=7, conn=conn)
        assert len(recent) == 1
        # Returns summaries
        assert hasattr(recent[0], "goal")


class TestVerificationCRUD:
    """Tests for verification CRUD operations."""

    def test_add_verification(self, conn):
        """Test adding a verification."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        verification = add_verification(
            intent.id,
            step_index=0,
            check_type="command",
            check_command="uname -r",
            expected_exit_code=0,
            required=True,
            conn=conn,
        )

        assert verification.id is not None
        assert verification.reboot_intent_id == intent.id
        assert verification.check_type == "command"

    def test_get_verifications(self, conn):
        """Test getting verifications for an intent."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )

        add_verification(intent.id, 0, "command", "cmd1", conn=conn)
        add_verification(intent.id, 1, "service_active", "svc1", conn=conn)

        verifications = get_verifications(intent.id, conn=conn)
        assert len(verifications) == 2
        assert verifications[0].step_index == 0
        assert verifications[1].step_index == 1

    def test_update_verification_result(self, conn):
        """Test updating verification with results."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        verification = add_verification(intent.id, 0, "command", "true", conn=conn)

        updated = update_verification_result(
            verification.id,
            exit_code=0,
            stdout="output",
            stderr="",
            passed=True,
            conn=conn,
        )

        assert updated is not None
        assert updated.passed is True
        assert updated.exit_code == 0
        assert updated.stdout == "output"
        assert updated.executed_at is not None

    def test_reset_verifications(self, conn):
        """Test resetting verifications."""
        intent = create_intent(
            goal="Test",
            task_description="Test",
            reason="user_requested",
            conn=conn,
        )
        verification = add_verification(intent.id, 0, "command", "true", conn=conn)
        update_verification_result(verification.id, 0, "out", "", True, conn=conn)

        # Verify it was updated
        verifications = get_verifications(intent.id, conn=conn)
        assert verifications[0].passed is True

        # Reset
        count = reset_verifications(intent.id, conn=conn)
        assert count == 1

        # Verify it was reset
        verifications = get_verifications(intent.id, conn=conn)
        assert verifications[0].passed is None
        assert verifications[0].executed_at is None


class TestGetRebootStatus:
    """Tests for get_reboot_status function."""

    def test_empty_status(self, conn):
        """Test status with no intents."""
        status = get_reboot_status(conn=conn)

        assert status.total_intents == 0
        assert status.pending_count == 0
        assert status.active_intent is None

    def test_status_with_intents(self, conn):
        """Test status with various intents."""
        # Create intents in different states
        create_intent(goal="Pending", task_description="T", reason="user_requested", conn=conn)

        rebooting = create_intent(goal="Rebooting", task_description="T", reason="user_requested", conn=conn)
        mark_rebooting(rebooting.id, "boot", conn=conn)

        completed = create_intent(goal="Completed", task_description="T", reason="user_requested", conn=conn)
        update_intent_status(completed.id, status="completed", outcome="improved", conn=conn)

        status = get_reboot_status(conn=conn)

        assert status.total_intents == 3
        assert status.pending_count == 1
        assert status.rebooting_count == 1
        assert status.completed_count == 1
        assert status.active_intent is not None
        assert status.active_intent.status == "rebooting"


class TestGetIntentCount:
    """Tests for get_intent_count function."""

    def test_count_empty(self, conn):
        """Test count with no intents."""
        assert get_intent_count(conn=conn) == 0

    def test_count_multiple(self, conn):
        """Test count with multiple intents."""
        for i in range(3):
            create_intent(
                goal=f"Test {i}",
                task_description="Test",
                reason="user_requested",
                conn=conn,
            )

        assert get_intent_count(conn=conn) == 3
