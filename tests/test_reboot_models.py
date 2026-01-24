"""Tests for reboot module Pydantic models."""

import pytest
from datetime import datetime

from elle.daemon.reboot.models import (
    GRUBState,
    PendingVerification,
    RebootIntent,
    RebootIntentSummary,
    RebootStatus,
    MAX_VERIFICATION_ATTEMPTS,
    VERIFICATION_DELAYS,
    CRITICAL_SERVICES,
)


class TestPendingVerification:
    """Tests for PendingVerification model."""

    def test_create_command_verification(self):
        """Test creating a command verification."""
        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="uname -r",
            expected_exit_code=0,
            required=True,
        )
        assert v.check_type == "command"
        assert v.check_command == "uname -r"
        assert v.expected_exit_code == 0
        assert v.required is True
        assert v.passed is None
        assert v.is_executed is False

    def test_create_service_verification(self):
        """Test creating a service verification."""
        v = PendingVerification(
            step_index=1,
            check_type="service_active",
            check_command="NetworkManager",
            required=True,
        )
        assert v.check_type == "service_active"
        assert v.check_command == "NetworkManager"

    def test_create_file_verification(self):
        """Test creating a file exists verification."""
        v = PendingVerification(
            step_index=2,
            check_type="file_exists",
            check_command="/etc/fstab",
            required=False,
        )
        assert v.check_type == "file_exists"
        assert v.required is False

    def test_create_port_verification(self):
        """Test creating a port listening verification."""
        v = PendingVerification(
            step_index=3,
            check_type="port_listening",
            check_command="127.0.0.1:22",
            required=True,
        )
        assert v.check_type == "port_listening"

    def test_is_executed_property(self):
        """Test is_executed property."""
        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="true",
        )
        assert v.is_executed is False

        v_executed = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="true",
            executed_at=datetime.utcnow(),
            passed=True,
        )
        assert v_executed.is_executed is True

    def test_model_is_frozen(self):
        """Test that model is immutable."""
        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="true",
        )
        with pytest.raises(Exception):  # ValidationError or AttributeError
            v.passed = True


class TestGRUBState:
    """Tests for GRUBState model."""

    def test_create_grub_state(self):
        """Test creating GRUB state."""
        state = GRUBState(
            default_entry="0",
            entries=("Ubuntu", "Advanced options"),
            oneshot_entry=None,
            saved_default="0",
        )
        assert state.default_entry == "0"
        assert len(state.entries) == 2
        assert state.oneshot_entry is None

    def test_grub_state_with_oneshot(self):
        """Test GRUB state with one-shot entry."""
        state = GRUBState(
            default_entry="saved",
            entries=("Ubuntu", "Ubuntu (recovery)"),
            oneshot_entry="1",
            saved_default="0",
        )
        assert state.oneshot_entry == "1"


class TestRebootIntent:
    """Tests for RebootIntent model."""

    def test_create_minimal_intent(self):
        """Test creating intent with minimal fields."""
        intent = RebootIntent(
            id="test-uuid",
            goal="Test reboot",
            task_description="Testing reboot persistence",
            reason="user_requested",
        )
        assert intent.id == "test-uuid"
        assert intent.goal == "Test reboot"
        assert intent.reason == "user_requested"
        assert intent.status == "pending"
        assert intent.outcome == "unknown"

    def test_create_full_intent(self):
        """Test creating intent with all fields."""
        now = datetime.utcnow()
        verifications = (
            PendingVerification(
                step_index=0,
                check_type="command",
                check_command="uname -r",
            ),
        )
        grub_state = GRUBState(
            default_entry="0",
            entries=("Ubuntu",),
        )

        intent = RebootIntent(
            id="test-uuid",
            incident_id="incident-uuid",
            created_at=now,
            updated_at=now,
            goal="Kernel update",
            task_description="Update to kernel 6.8",
            reason="kernel_update",
            reason_detail="Security patch",
            session_history=("apt update", "apt upgrade"),
            plan_json={"steps": ["update", "reboot"]},
            status="pending",
            pre_snapshot_json={"os": "Ubuntu 24.04"},
            boot_id="boot-123",
            grub_entry="0",
            grub_default_saved="0",
            grub_state=grub_state,
            verifications=verifications,
        )

        assert intent.incident_id == "incident-uuid"
        assert len(intent.session_history) == 2
        assert intent.grub_state is not None
        assert len(intent.verifications) == 1

    def test_intent_status_values(self):
        """Test valid status values."""
        statuses = [
            "pending", "rebooting", "verifying",
            "completed", "failed", "rolled_back", "cancelled"
        ]
        for status in statuses:
            intent = RebootIntent(
                id="test",
                goal="Test",
                task_description="Test",
                reason="user_requested",
                status=status,
            )
            assert intent.status == status

    def test_intent_reason_values(self):
        """Test valid reason values."""
        reasons = [
            "kernel_update", "driver_update", "config_change",
            "service_restart", "grub_update", "user_requested"
        ]
        for reason in reasons:
            intent = RebootIntent(
                id="test",
                goal="Test",
                task_description="Test",
                reason=reason,
            )
            assert intent.reason == reason


class TestRebootIntentSummary:
    """Tests for RebootIntentSummary model."""

    def test_create_summary(self):
        """Test creating intent summary."""
        summary = RebootIntentSummary(
            id="test-uuid",
            created_at=datetime.utcnow(),
            goal="Test reboot",
            reason="user_requested",
            status="completed",
            outcome="improved",
            verification_attempts=1,
        )
        assert summary.id == "test-uuid"
        assert summary.status == "completed"
        assert summary.outcome == "improved"


class TestRebootStatus:
    """Tests for RebootStatus model."""

    def test_create_status(self):
        """Test creating status report."""
        status = RebootStatus(
            total_intents=10,
            pending_count=1,
            rebooting_count=0,
            verifying_count=0,
            completed_count=7,
            failed_count=1,
            rolled_back_count=1,
            by_outcome={"improved": 7, "failed": 2},
        )
        assert status.total_intents == 10
        assert status.completed_count == 7

    def test_status_with_active(self):
        """Test status with active intent."""
        active = RebootIntentSummary(
            id="active-uuid",
            created_at=datetime.utcnow(),
            goal="Active reboot",
            reason="kernel_update",
            status="verifying",
            outcome="unknown",
            verification_attempts=1,
        )
        status = RebootStatus(
            total_intents=1,
            active_intent=active,
        )
        assert status.active_intent is not None
        assert status.active_intent.status == "verifying"


class TestConstants:
    """Tests for module constants."""

    def test_max_verification_attempts(self):
        """Test MAX_VERIFICATION_ATTEMPTS constant."""
        assert MAX_VERIFICATION_ATTEMPTS == 3

    def test_verification_delays(self):
        """Test VERIFICATION_DELAYS constant."""
        assert len(VERIFICATION_DELAYS) == 3
        assert VERIFICATION_DELAYS[0] == 0  # First attempt immediate
        assert VERIFICATION_DELAYS[1] > 0
        assert VERIFICATION_DELAYS[2] > VERIFICATION_DELAYS[1]

    def test_critical_services(self):
        """Test CRITICAL_SERVICES constant."""
        assert "NetworkManager" in CRITICAL_SERVICES
        assert "systemd-resolved" in CRITICAL_SERVICES
        # ssh or sshd should be present
        assert "ssh" in CRITICAL_SERVICES or "sshd" in CRITICAL_SERVICES
