"""Tests for service capabilities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.service import (
    ServiceInput,
    ServiceRestartCapability,
    ServiceStartCapability,
    ServiceStatusCapability,
    ServiceStopCapability,
)
from elle.capabilities.models import CapabilityResult

# =============================================================================
# Mock helpers
# =============================================================================


def _make_subprocess_result(
    command: str = "systemctl test",
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    denied: bool = False,
    deny_reason=None,
    deny_explanation: str | None = None,
):
    """Create a mock SubprocessResult with correct property semantics."""
    result = MagicMock()
    result.command = command
    result.exit_code = exit_code
    result.stdout = stdout
    result.stderr = stderr
    result.timed_out = timed_out
    result.denied = denied
    result.deny_reason = deny_reason
    result.deny_explanation = deny_explanation
    result.success = exit_code == 0 and not timed_out and not denied
    result.failed = not result.success
    return result


# =============================================================================
# Service Restart Tests
# =============================================================================


class TestServiceRestartCapability:
    def setup_method(self):
        self.cap = ServiceRestartCapability()

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_happy_path_restart(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("active", "running", 1234),
            ("active", "running", 5678),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.service == "nginx"
        assert result.output.previous_state == "active/running"
        assert result.output.new_state == "active/running"
        assert result.output.pid == 5678
        assert len(result.side_effects_applied) == 1
        mock_run.assert_called_once_with("restart", "nginx", 30)

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_timeout_handling(self, mock_state, mock_run):
        mock_state.return_value = ("active", "running", 1234)
        mock_run.return_value = (False, "", "Command timed out after 30 seconds", -1)

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is False
        assert "timed out" in (result.error or "").lower()

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_already_running_still_restarts(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("active", "running", 100),
            ("active", "running", 200),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="ssh")
        result = self.cap.run(inp)

        assert result.success is True
        mock_run.assert_called_once_with("restart", "ssh", 30)

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_permission_denied_from_systemctl(self, mock_state, mock_run):
        mock_state.return_value = ("active", "running", 1234)
        mock_run.return_value = (
            False,
            "",
            "Failed to restart nginx.service: Access denied",
            1,
        )

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Access denied" in (result.error or "")

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_rollback_on_verify_failure(self, mock_state, mock_run):
        # Run succeeds
        mock_state.side_effect = [
            ("active", "running", 1234),
            ("active", "running", 5678),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        run_result = self.cap.run(inp)
        assert run_result.success is True

        # But verify fails
        mock_state.side_effect = None
        mock_state.return_value = ("failed", "failed", None)
        verify_result = self.cap.verify(inp)
        assert verify_result.passed is False
        assert len(verify_result.discrepancies) > 0

        # Trigger rollback (another restart)
        mock_run.return_value = (True, "", "", 0)
        rollback_result = self.cap.rollback(inp, run_result)
        assert rollback_result.success is True
        assert "nginx.service" in rollback_result.rolled_back

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_with_custom_timeout(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("active", "running", 1234),
            ("active", "running", 5678),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx", timeout_sec=120)
        result = self.cap.run(inp)

        assert result.success is True
        mock_run.assert_called_once_with("restart", "nginx", 120)

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_stderr_on_failure(self, mock_state, mock_run):
        mock_state.return_value = ("inactive", "dead", None)
        mock_run.return_value = (
            False,
            "",
            "Job for nginx.service failed because the control process exited",
            1,
        )

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is False
        assert result.error == "Job for nginx.service failed because the control process exited"
        assert result.evidence.commands_executed == ("systemctl restart nginx",)

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_evidence_includes_systemctl_citation(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("active", "running", 1234),
            ("active", "running", 5678),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert "systemctl(1)" in result.evidence.man_vault_citations
        assert "systemctl restart nginx" in result.evidence.commands_executed


# =============================================================================
# Service Start Tests
# =============================================================================


class TestServiceStartCapability:
    def setup_method(self):
        self.cap = ServiceStartCapability()

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_start_stopped_service(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("inactive", "dead", None),
            ("active", "running", 9999),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.service == "nginx"
        assert result.output.previous_state == "inactive"
        assert result.output.new_state == "active"
        assert result.output.pid == 9999
        mock_run.assert_called_once_with("start", "nginx", 30)

    @patch("elle.capabilities.core.service._get_service_state")
    def test_already_running_dry_run_shows_no_op(self, mock_state):
        mock_state.return_value = ("active", "running", 1234)

        inp = ServiceInput(service="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.would_execute == ()
        assert "already active" in result.preview_text

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_missing_unknown_service(self, mock_state, mock_run):
        mock_state.return_value = ("inactive", "dead", None)
        mock_run.return_value = (
            False,
            "",
            "Failed to start nonexistent.service: Unit nonexistent.service not found.",
            5,
        )

        inp = ServiceInput(service="nonexistent")
        result = self.cap.run(inp)

        assert result.success is False
        assert "not found" in (result.error or "").lower()

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_start_failure(self, mock_state, mock_run):
        mock_state.return_value = ("inactive", "dead", None)
        mock_run.return_value = (False, "", "systemctl start failed with exit code 1", 1)

        inp = ServiceInput(service="broken")
        result = self.cap.run(inp)

        assert result.success is False
        assert result.output is None

    @patch("elle.capabilities.core.service._get_service_state")
    def test_verify_after_start(self, mock_state):
        mock_state.return_value = ("active", "running", 1234)

        inp = ServiceInput(service="nginx")
        result = self.cap.verify(inp)

        assert result.passed is True
        assert result.expected_state == {"active_state": "active"}
        assert result.actual_state == {"active_state": "active"}
        assert result.discrepancies == ()

    @patch("elle.capabilities.core.service._run_systemctl")
    def test_rollback_stops_service(self, mock_run):
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        dummy_result = CapabilityResult(success=True)
        rollback = self.cap.rollback(inp, dummy_result)

        assert rollback.success is True
        assert "nginx.service" in rollback.rolled_back
        assert rollback.commands_executed == ("systemctl stop nginx",)
        mock_run.assert_called_once_with("stop", "nginx", 30)


# =============================================================================
# Service Stop Tests
# =============================================================================


class TestServiceStopCapability:
    def setup_method(self):
        self.cap = ServiceStopCapability()

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_stop_running_service(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("active", "running", 1234),
            ("inactive", "dead", None),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.service == "nginx"
        assert result.output.previous_state == "active"
        assert result.output.new_state == "inactive"
        assert result.output.pid is None
        mock_run.assert_called_once_with("stop", "nginx", 30)

    @patch("elle.capabilities.core.service._get_service_state")
    def test_already_stopped_dry_run_shows_not_running(self, mock_state):
        mock_state.return_value = ("inactive", "dead", None)

        inp = ServiceInput(service="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.would_execute == ()
        assert "not running" in result.preview_text

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_stop_failure(self, mock_state, mock_run):
        mock_state.return_value = ("active", "running", 1234)
        mock_run.return_value = (
            False,
            "",
            "Failed to stop nginx.service: Operation refused",
            1,
        )

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Operation refused" in (result.error or "")

    @patch("elle.capabilities.core.service._get_service_state")
    def test_verify_after_stop_checks_inactive_or_failed(self, mock_state):
        # inactive passes
        mock_state.return_value = ("inactive", "dead", None)
        inp = ServiceInput(service="nginx")
        result = self.cap.verify(inp)
        assert result.passed is True

        # failed also passes (service is not active)
        mock_state.return_value = ("failed", "failed", None)
        result = self.cap.verify(inp)
        assert result.passed is True

        # active does not pass
        mock_state.return_value = ("active", "running", 1234)
        result = self.cap.verify(inp)
        assert result.passed is False
        assert len(result.discrepancies) > 0

    @patch("elle.capabilities.core.service._run_systemctl")
    def test_rollback_starts_service(self, mock_run):
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="nginx")
        dummy_result = CapabilityResult(success=True)
        rollback = self.cap.rollback(inp, dummy_result)

        assert rollback.success is True
        assert "nginx.service" in rollback.rolled_back
        assert rollback.commands_executed == ("systemctl start nginx",)
        mock_run.assert_called_once_with("start", "nginx", 30)

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_evidence_tracks_commands(self, mock_state, mock_run):
        mock_state.side_effect = [
            ("active", "running", 1234),
            ("inactive", "dead", None),
        ]
        mock_run.return_value = (True, "", "", 0)

        inp = ServiceInput(service="ssh")
        result = self.cap.run(inp)

        assert result.success is True
        assert "systemctl stop ssh" in result.evidence.commands_executed
        assert "systemctl(1)" in result.evidence.man_vault_citations
        assert "Stopped ssh" in result.evidence.rationale


# =============================================================================
# Service Status Tests
# =============================================================================


class TestServiceStatusCapability:
    def setup_method(self):
        self.cap = ServiceStatusCapability()

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_active_service_status(self, mock_run):
        mock_run.return_value = _make_subprocess_result(
            stdout="ActiveState=active\nSubState=running\nMainPID=1234\nDescription=The NGINX HTTP Server\nLoadState=loaded",
        )

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.service == "nginx"
        assert result.output.active_state == "active"
        assert result.output.sub_state == "running"
        assert result.output.pid == 1234
        assert result.output.description == "The NGINX HTTP Server"
        assert result.output.loaded is True

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_inactive_service(self, mock_run):
        mock_run.return_value = _make_subprocess_result(
            stdout="ActiveState=inactive\nSubState=dead\nMainPID=0\nDescription=The NGINX HTTP Server\nLoadState=loaded",
        )

        inp = ServiceInput(service="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.active_state == "inactive"
        assert result.output.sub_state == "dead"
        assert result.output.pid is None  # PID=0 treated as None

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_failed_service_state(self, mock_run):
        mock_run.return_value = _make_subprocess_result(
            stdout="ActiveState=failed\nSubState=failed\nMainPID=0\nDescription=Broken Service\nLoadState=loaded",
        )

        inp = ServiceInput(service="broken")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.active_state == "failed"
        assert result.output.sub_state == "failed"

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_pid_zero_treated_as_none(self, mock_run):
        mock_run.return_value = _make_subprocess_result(
            stdout="ActiveState=inactive\nSubState=dead\nMainPID=0\nDescription=Stopped\nLoadState=loaded",
        )

        inp = ServiceInput(service="stopped-svc")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.pid is None


# =============================================================================
# Dry Run Tests
# =============================================================================


class TestServiceDryRun:
    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run_restart_shows_confirmation_required(self, mock_state):
        mock_state.return_value = ("active", "running", 1234)

        cap = ServiceRestartCapability()
        inp = ServiceInput(service="nginx")
        result = cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "medium"
        assert "systemctl restart nginx" in result.would_execute
        assert "nginx.service" in result.would_modify
        assert "nginx" in result.preview_text

    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run_start_already_active_no_op(self, mock_state):
        mock_state.return_value = ("active", "running", 1234)

        cap = ServiceStartCapability()
        inp = ServiceInput(service="nginx")
        result = cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.would_execute == ()
        assert result.would_modify == ()
        assert result.estimated_risk == "none"
        assert "already active" in result.preview_text

    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run_stop_not_running_no_op(self, mock_state):
        mock_state.return_value = ("inactive", "dead", None)

        cap = ServiceStopCapability()
        inp = ServiceInput(service="nginx")
        result = cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.would_execute == ()
        assert result.would_modify == ()
        assert result.estimated_risk == "none"
        assert "not running" in result.preview_text

    def test_dry_run_status_read_only_no_confirmation(self):
        cap = ServiceStatusCapability()
        inp = ServiceInput(service="nginx")
        result = cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert result.would_modify == ()
        assert "systemctl status nginx" in result.would_execute


# =============================================================================
# Rollback Tests
# =============================================================================


class TestServiceRollback:
    @patch("elle.capabilities.core.service._run_systemctl")
    def test_rollback_restart_re_restarts(self, mock_run):
        mock_run.return_value = (True, "", "", 0)

        cap = ServiceRestartCapability()
        inp = ServiceInput(service="nginx")
        dummy_result = CapabilityResult(success=True)
        rollback = cap.rollback(inp, dummy_result)

        assert rollback.success is True
        assert "nginx.service" in rollback.rolled_back
        assert rollback.commands_executed == ("systemctl restart nginx",)

    @patch("elle.capabilities.core.service._run_systemctl")
    def test_rollback_start_stops(self, mock_run):
        mock_run.return_value = (True, "", "", 0)

        cap = ServiceStartCapability()
        inp = ServiceInput(service="nginx")
        dummy_result = CapabilityResult(success=True)
        rollback = cap.rollback(inp, dummy_result)

        assert rollback.success is True
        assert "nginx.service" in rollback.rolled_back
        assert rollback.commands_executed == ("systemctl stop nginx",)

    @patch("elle.capabilities.core.service._run_systemctl")
    def test_rollback_stop_starts(self, mock_run):
        mock_run.return_value = (True, "", "", 0)

        cap = ServiceStopCapability()
        inp = ServiceInput(service="nginx")
        dummy_result = CapabilityResult(success=True)
        rollback = cap.rollback(inp, dummy_result)

        assert rollback.success is True
        assert "nginx.service" in rollback.rolled_back
        assert rollback.commands_executed == ("systemctl start nginx",)

    @patch("elle.capabilities.core.service._run_systemctl")
    def test_rollback_failure_propagated(self, mock_run):
        mock_run.return_value = (False, "", "Access denied", 1)

        cap = ServiceRestartCapability()
        inp = ServiceInput(service="nginx")
        dummy_result = CapabilityResult(success=True)
        rollback = cap.rollback(inp, dummy_result)

        assert rollback.success is False
        assert rollback.error == "Access denied"
        assert rollback.rolled_back == ()
        assert "nginx.service" in rollback.failed_to_rollback


# =============================================================================
# Input Validation Tests
# =============================================================================


class TestServiceInputValidation:
    def test_empty_service_name_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            ServiceInput(service="")

    def test_timeout_below_1_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            ServiceInput(service="nginx", timeout_sec=0)

    def test_timeout_above_300_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            ServiceInput(service="nginx", timeout_sec=301)
