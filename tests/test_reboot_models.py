"""Tests for reboot module Pydantic models."""

from datetime import datetime

import pytest

from elle.daemon.reboot.models import (
    CRITICAL_SERVICES,
    MAX_VERIFICATION_ATTEMPTS,
    VERIFICATION_DELAYS,
    GRUBState,
    PendingVerification,
    RebootIntent,
    RebootIntentSummary,
    RebootStatus,
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
        statuses = ["pending", "rebooting", "verifying", "completed", "failed", "rolled_back", "cancelled"]
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
            "kernel_update",
            "driver_update",
            "config_change",
            "service_restart",
            "grub_update",
            "user_requested",
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


# ===========================================================================
# FailureDiagnostics
# ===========================================================================


class TestFailureDiagnostics:
    """Tests for FailureDiagnostics model and to_llm_context method."""

    def _make_diagnostics(self, **overrides):
        defaults = {
            "intent_goal": "Kernel update to 6.8",
            "intent_reason": "kernel_update",
        }
        defaults.update(overrides)
        from elle.daemon.reboot.models import FailureDiagnostics

        return FailureDiagnostics(**defaults)

    def test_create_minimal_diagnostics(self):
        diag = self._make_diagnostics()
        assert diag.intent_goal == "Kernel update to 6.8"
        assert diag.intent_reason == "kernel_update"
        assert diag.kernel_version is None
        assert diag.boot_mode is None
        assert len(diag.failed_checks) == 0

    def test_create_full_diagnostics(self):
        from elle.daemon.reboot.models import FailureDiagnostics

        diag = FailureDiagnostics(
            intent_goal="Kernel update",
            intent_reason="kernel_update",
            intent_reason_detail="Security CVE patch",
            commands_before_reboot=("apt update", "apt upgrade"),
            config_changes=("/etc/default/grub",),
            failed_checks=(
                {"check_type": "command", "check_command": "uname -r", "stderr": "wrong version"},
            ),
            passed_checks=(
                {"check_type": "service_active", "check_command": "ssh"},
            ),
            kernel_version="6.8.0-generic",
            previous_kernel="6.5.0-generic",
            boot_mode="UEFI",
            uptime_seconds=120,
            dmesg_errors=("error: module not found",),
            journal_errors=("Failed to start nginx",),
            failed_services=("nginx.service",),
            read_only_mounts=("/var",),
            missing_mounts=("/home",),
            disk_space_issues=("/ at 98%",),
            interfaces_down=("eth0",),
            network_errors=("Connection refused",),
            missing_modules=("nvidia",),
            module_errors=("nvidia: signature check failed",),
            current_grub_entry="0",
            previous_grub_entry="1",
            grub_entries_available=("Ubuntu", "Ubuntu (recovery)"),
            likely_causes=("Kernel module incompatibility",),
            suggested_investigation=("dmesg | grep nvidia", "journalctl -b -p err"),
        )
        assert diag.kernel_version == "6.8.0-generic"
        assert len(diag.commands_before_reboot) == 2
        assert len(diag.failed_checks) == 1
        assert len(diag.grub_entries_available) == 2

    def test_to_llm_context_minimal(self):
        diag = self._make_diagnostics()
        ctx = diag.to_llm_context()
        assert "## REBOOT FAILURE DIAGNOSTICS" in ctx
        assert "Kernel update to 6.8" in ctx
        assert "kernel_update" in ctx
        assert "Kernel: unknown" in ctx
        assert "Boot mode: unknown" in ctx

    def test_to_llm_context_with_reason_detail(self):
        diag = self._make_diagnostics(intent_reason_detail="CVE-2024-1234")
        ctx = diag.to_llm_context()
        assert "CVE-2024-1234" in ctx

    def test_to_llm_context_with_commands(self):
        diag = self._make_diagnostics(
            commands_before_reboot=tuple(f"cmd-{i}" for i in range(15)),
        )
        ctx = diag.to_llm_context()
        assert "Commands Executed Before Reboot" in ctx
        # Only last 10 commands should be shown
        assert "cmd-5" in ctx
        assert "cmd-14" in ctx

    def test_to_llm_context_with_failed_checks(self):
        diag = self._make_diagnostics(
            failed_checks=(
                {"check_type": "command", "check_command": "uname -r", "stderr": "error output"},
                {"check_type": "service_active", "check_command": "nginx"},
            ),
        )
        ctx = diag.to_llm_context()
        assert "Failed Verification Checks" in ctx
        assert "command" in ctx
        assert "uname -r" in ctx
        assert "error output" in ctx

    def test_to_llm_context_with_failed_check_no_stderr(self):
        diag = self._make_diagnostics(
            failed_checks=(
                {"check_type": "service_active", "check_command": "nginx"},
            ),
        )
        ctx = diag.to_llm_context()
        assert "Failed Verification Checks" in ctx
        assert "stderr" not in ctx

    def test_to_llm_context_with_dmesg_errors(self):
        diag = self._make_diagnostics(
            dmesg_errors=tuple(f"dmesg error line {i}" for i in range(20)),
        )
        ctx = diag.to_llm_context()
        assert "Kernel Errors (dmesg)" in ctx
        # Only first 15 lines shown
        assert "dmesg error line 0" in ctx
        assert "dmesg error line 14" in ctx

    def test_to_llm_context_with_journal_errors(self):
        diag = self._make_diagnostics(
            journal_errors=("Failed to start nginx.service",),
        )
        ctx = diag.to_llm_context()
        assert "System Journal Errors" in ctx
        assert "Failed to start nginx.service" in ctx

    def test_to_llm_context_with_failed_services(self):
        diag = self._make_diagnostics(
            failed_services=("nginx.service", "postgresql.service"),
        )
        ctx = diag.to_llm_context()
        assert "Failed Services" in ctx
        assert "nginx.service" in ctx
        assert "postgresql.service" in ctx

    def test_to_llm_context_with_read_only_mounts(self):
        diag = self._make_diagnostics(read_only_mounts=("/var",))
        ctx = diag.to_llm_context()
        assert "Read-Only Mounts (PROBLEM)" in ctx
        assert "/var" in ctx

    def test_to_llm_context_with_missing_mounts(self):
        diag = self._make_diagnostics(missing_mounts=("/home",))
        ctx = diag.to_llm_context()
        assert "Missing Mounts (PROBLEM)" in ctx
        assert "/home" in ctx

    def test_to_llm_context_with_disk_space_issues(self):
        diag = self._make_diagnostics(disk_space_issues=("/ at 98%",))
        ctx = diag.to_llm_context()
        assert "Disk Space Issues" in ctx
        assert "/ at 98%" in ctx

    def test_to_llm_context_with_interfaces_down(self):
        diag = self._make_diagnostics(interfaces_down=("eth0",))
        ctx = diag.to_llm_context()
        assert "Network Interfaces Down" in ctx
        assert "eth0" in ctx

    def test_to_llm_context_with_missing_modules(self):
        diag = self._make_diagnostics(missing_modules=("nvidia",))
        ctx = diag.to_llm_context()
        assert "Missing Kernel Modules" in ctx
        assert "nvidia" in ctx

    def test_to_llm_context_with_likely_causes(self):
        diag = self._make_diagnostics(likely_causes=("Driver incompatibility",))
        ctx = diag.to_llm_context()
        assert "Likely Root Causes" in ctx
        assert "Driver incompatibility" in ctx

    def test_to_llm_context_with_suggested_investigation(self):
        diag = self._make_diagnostics(
            suggested_investigation=("dmesg | grep nvidia",),
        )
        ctx = diag.to_llm_context()
        assert "Suggested Investigation Commands" in ctx
        assert "dmesg | grep nvidia" in ctx

    def test_to_llm_context_system_state_section(self):
        diag = self._make_diagnostics(
            kernel_version="6.8.0",
            previous_kernel="6.5.0",
            boot_mode="UEFI",
            uptime_seconds=300,
        )
        ctx = diag.to_llm_context()
        assert "System State" in ctx
        assert "Kernel: 6.8.0" in ctx
        assert "Previous kernel: 6.5.0" in ctx
        assert "Boot mode: UEFI" in ctx
        assert "Uptime: 300s" in ctx

    def test_to_llm_context_no_previous_kernel(self):
        diag = self._make_diagnostics(kernel_version="6.8.0")
        ctx = diag.to_llm_context()
        assert "Previous kernel" not in ctx

    def test_to_llm_context_no_uptime(self):
        diag = self._make_diagnostics()
        ctx = diag.to_llm_context()
        assert "Uptime:" not in ctx

    def test_diagnostics_model_is_frozen(self):
        diag = self._make_diagnostics()
        with pytest.raises(Exception):
            diag.intent_goal = "changed"

    def test_to_llm_context_full_output(self):
        """Test that full diagnostics produce all sections."""
        from elle.daemon.reboot.models import FailureDiagnostics

        diag = FailureDiagnostics(
            intent_goal="Test goal",
            intent_reason="user_requested",
            intent_reason_detail="Detail",
            commands_before_reboot=("cmd1",),
            failed_checks=({"check_type": "command", "check_command": "test", "stderr": "err"},),
            dmesg_errors=("dmesg err",),
            journal_errors=("journal err",),
            failed_services=("svc1",),
            read_only_mounts=("/ro",),
            missing_mounts=("/miss",),
            disk_space_issues=("disk issue",),
            interfaces_down=("lo",),
            missing_modules=("mod1",),
            likely_causes=("cause1",),
            suggested_investigation=("investigate1",),
            kernel_version="5.15",
            previous_kernel="5.14",
            boot_mode="BIOS",
            uptime_seconds=60,
        )
        ctx = diag.to_llm_context()
        # Verify all section headers present
        for header in [
            "REBOOT FAILURE DIAGNOSTICS",
            "Commands Executed Before Reboot",
            "Failed Verification Checks",
            "Kernel Errors (dmesg)",
            "System Journal Errors",
            "Failed Services",
            "Read-Only Mounts",
            "Missing Mounts",
            "Disk Space Issues",
            "Network Interfaces Down",
            "Missing Kernel Modules",
            "Likely Root Causes",
            "Suggested Investigation Commands",
            "System State",
        ]:
            assert header in ctx, f"Missing section: {header}"


# ===========================================================================
# Additional model constants
# ===========================================================================


class TestAdditionalConstants:
    """Tests for additional safety constants."""

    def test_verification_timeout(self):
        from elle.daemon.reboot.models import VERIFICATION_TIMEOUT_SEC

        assert VERIFICATION_TIMEOUT_SEC == 30

    def test_auto_rollback_delay(self):
        from elle.daemon.reboot.models import AUTO_ROLLBACK_DELAY_SEC

        assert AUTO_ROLLBACK_DELAY_SEC == 60

    def test_network_check_timeout(self):
        from elle.daemon.reboot.models import NETWORK_CHECK_TIMEOUT_SEC

        assert NETWORK_CHECK_TIMEOUT_SEC == 60

    def test_stale_reboot_threshold(self):
        from elle.daemon.reboot.models import STALE_REBOOT_THRESHOLD_HOURS

        assert STALE_REBOOT_THRESHOLD_HOURS == 24

    def test_journal_lines_to_capture(self):
        from elle.daemon.reboot.models import JOURNAL_LINES_TO_CAPTURE

        assert JOURNAL_LINES_TO_CAPTURE == 200

    def test_dmesg_lines_to_capture(self):
        from elle.daemon.reboot.models import DMESG_LINES_TO_CAPTURE

        assert DMESG_LINES_TO_CAPTURE == 100

    def test_dmesg_error_patterns(self):
        from elle.daemon.reboot.models import DMESG_ERROR_PATTERNS

        assert isinstance(DMESG_ERROR_PATTERNS, tuple)
        assert len(DMESG_ERROR_PATTERNS) > 0
        assert "panic" in DMESG_ERROR_PATTERNS
        assert "segfault" in DMESG_ERROR_PATTERNS
        assert "Out of memory" in DMESG_ERROR_PATTERNS

    def test_journal_error_patterns(self):
        from elle.daemon.reboot.models import JOURNAL_ERROR_PATTERNS

        assert isinstance(JOURNAL_ERROR_PATTERNS, tuple)
        assert len(JOURNAL_ERROR_PATTERNS) > 0
        assert "Failed to start" in JOURNAL_ERROR_PATTERNS
        assert "Permission denied" in JOURNAL_ERROR_PATTERNS

    def test_critical_mount_points(self):
        from elle.daemon.reboot.models import CRITICAL_MOUNT_POINTS

        assert "/" in CRITICAL_MOUNT_POINTS
        assert "/var" in CRITICAL_MOUNT_POINTS
        assert "/tmp" in CRITICAL_MOUNT_POINTS

    def test_disk_space_requirements(self):
        from elle.daemon.reboot.models import DISK_SPACE_REQUIREMENTS

        assert isinstance(DISK_SPACE_REQUIREMENTS, tuple)
        # Should have root, /var, /boot
        paths = [r[0] for r in DISK_SPACE_REQUIREMENTS]
        assert "/" in paths
        assert "/var" in paths
        assert "/boot" in paths
        # All minimums should be positive
        for _, min_mb in DISK_SPACE_REQUIREMENTS:
            assert min_mb > 0


# ===========================================================================
# Additional model edge cases
# ===========================================================================


class TestRebootStatusEdgeCases:
    """Additional edge case tests for RebootStatus."""

    def test_status_defaults(self):
        status = RebootStatus()
        assert status.total_intents == 0
        assert status.pending_count == 0
        assert status.rebooting_count == 0
        assert status.verifying_count == 0
        assert status.completed_count == 0
        assert status.failed_count == 0
        assert status.rolled_back_count == 0
        assert status.by_outcome == {}
        assert status.active_intent is None
        assert status.recent_intents == ()
        assert status.db_size_bytes == 0
        assert status.oldest_intent is None
        assert status.newest_intent is None

    def test_status_with_recent_intents(self):
        from datetime import datetime

        summary1 = RebootIntentSummary(
            id="uuid-1",
            created_at=datetime.utcnow(),
            goal="Goal 1",
            reason="kernel_update",
            status="completed",
            outcome="improved",
            verification_attempts=1,
        )
        summary2 = RebootIntentSummary(
            id="uuid-2",
            created_at=datetime.utcnow(),
            goal="Goal 2",
            reason="user_requested",
            status="failed",
            outcome="failed",
            verification_attempts=3,
        )
        status = RebootStatus(
            total_intents=2,
            completed_count=1,
            failed_count=1,
            recent_intents=(summary1, summary2),
            db_size_bytes=1024,
            oldest_intent=datetime(2024, 1, 1),
            newest_intent=datetime(2024, 6, 1),
        )
        assert len(status.recent_intents) == 2
        assert status.db_size_bytes == 1024
        assert status.oldest_intent is not None
        assert status.newest_intent is not None

    def test_status_model_is_frozen(self):
        status = RebootStatus()
        with pytest.raises(Exception):
            status.total_intents = 5


class TestPendingVerificationEdgeCases:
    """Additional edge case tests for PendingVerification."""

    def test_all_check_types(self):
        check_types = [
            "command",
            "service_active",
            "file_exists",
            "port_listening",
            "filesystem_writable",
            "mount_exists",
            "kernel_module",
            "dmesg_no_errors",
            "journal_no_errors",
            "disk_space",
            "network_interface",
        ]
        for ct in check_types:
            v = PendingVerification(
                step_index=0,
                check_type=ct,
                check_command="test",
            )
            assert v.check_type == ct

    def test_verification_with_results(self):
        from datetime import datetime

        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="uname -r",
            expected_exit_code=0,
            expected_output_contains="6.8",
            executed_at=datetime.utcnow(),
            exit_code=0,
            stdout="6.8.0-generic",
            stderr="",
            passed=True,
        )
        assert v.is_executed is True
        assert v.passed is True
        assert v.exit_code == 0
        assert v.stdout == "6.8.0-generic"

    def test_verification_with_failed_result(self):
        from datetime import datetime

        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="test -f /nonexistent",
            expected_exit_code=0,
            executed_at=datetime.utcnow(),
            exit_code=1,
            stdout="",
            stderr="file not found",
            passed=False,
        )
        assert v.is_executed is True
        assert v.passed is False
        assert v.exit_code == 1

    def test_verification_defaults(self):
        v = PendingVerification(
            step_index=0,
            check_type="command",
            check_command="true",
        )
        assert v.id is None
        assert v.reboot_intent_id is None
        assert v.expected_exit_code == 0
        assert v.expected_output_contains is None
        assert v.required is True
        assert v.executed_at is None
        assert v.exit_code is None
        assert v.stdout is None
        assert v.stderr is None
        assert v.passed is None

    def test_step_index_must_be_non_negative(self):
        with pytest.raises(Exception):
            PendingVerification(
                step_index=-1,
                check_type="command",
                check_command="true",
            )


class TestGRUBStateEdgeCases:
    """Additional edge case tests for GRUBState."""

    def test_grub_state_defaults(self):
        state = GRUBState(default_entry="0")
        assert state.entries == ()
        assert state.oneshot_entry is None
        assert state.saved_default is None

    def test_grub_state_frozen(self):
        state = GRUBState(default_entry="0")
        with pytest.raises(Exception):
            state.default_entry = "1"

    def test_grub_state_many_entries(self):
        entries = tuple(f"Ubuntu {i}" for i in range(10))
        state = GRUBState(
            default_entry="saved",
            entries=entries,
            saved_default="0",
        )
        assert len(state.entries) == 10


class TestRebootIntentEdgeCases:
    """Additional edge case tests for RebootIntent."""

    def test_intent_defaults(self):
        intent = RebootIntent(
            id="test",
            goal="Test",
            task_description="Test desc",
            reason="user_requested",
        )
        assert intent.incident_id is None
        assert intent.session_history == ()
        assert intent.plan_json == {}
        assert intent.pre_snapshot_json == {}
        assert intent.boot_id is None
        assert intent.grub_entry is None
        assert intent.grub_default_saved is None
        assert intent.grub_state is None
        assert intent.post_snapshot_json == {}
        assert intent.new_boot_id is None
        assert intent.verification_attempts == 0
        assert intent.last_verification_at is None
        assert intent.outcome_detail is None
        assert intent.verifications == ()

    def test_intent_outcome_values(self):
        outcomes = ["improved", "failed", "rolled_back", "unknown"]
        for outcome in outcomes:
            intent = RebootIntent(
                id="test",
                goal="Test",
                task_description="Test",
                reason="user_requested",
                outcome=outcome,
            )
            assert intent.outcome == outcome

    def test_intent_frozen(self):
        intent = RebootIntent(
            id="test",
            goal="Test",
            task_description="Test",
            reason="user_requested",
        )
        with pytest.raises(Exception):
            intent.status = "completed"
