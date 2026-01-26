"""Tests for service capabilities."""

from unittest.mock import MagicMock, patch

import pytest

from elle.capabilities.core.service import (
    ServiceInput,
    ServiceRestartCapability,
    ServiceStartCapability,
    ServiceStatusCapability,
    ServiceStopCapability,
)

# =============================================================================
# Mock helpers
# =============================================================================


def mock_run_safe(success=True, stdout="", stderr="", exit_code=0):
    """Create a mock for run_safe."""
    result = MagicMock()
    result.success = success
    result.stdout = stdout
    result.stderr = stderr
    result.exit_code = exit_code
    return result


# =============================================================================
# Service Restart Tests
# =============================================================================


class TestServiceRestartCapability:
    """Tests for ServiceRestartCapability."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cap = ServiceRestartCapability()

    def test_spec(self):
        """Test capability spec."""
        spec = self.cap.spec
        assert spec.name == "service.restart"
        assert spec.domain == "service"
        assert spec.risk == "medium"
        assert spec.requires_privilege is True
        assert spec.idempotent is True

    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run(self, mock_state):
        """Test dry run preview."""
        mock_state.return_value = ("active", "running", 1234)

        input_data = ServiceInput(service="nginx")
        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert "nginx" in result.preview_text
        assert "active/running" in result.preview_text

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_run_success(self, mock_state, mock_run):
        """Test successful restart."""
        mock_state.side_effect = [
            ("active", "running", 1234),  # Before
            ("active", "running", 5678),  # After
        ]
        mock_run.return_value = (True, "", "", 0)

        input_data = ServiceInput(service="nginx")
        result = self.cap.run(input_data)

        assert result.success is True
        assert result.output.service == "nginx"
        assert result.output.new_state == "active/running"

    @patch("elle.capabilities.core.service._run_systemctl")
    @patch("elle.capabilities.core.service._get_service_state")
    def test_run_failure(self, mock_state, mock_run):
        """Test failed restart."""
        mock_state.return_value = ("inactive", "dead", None)
        mock_run.return_value = (False, "", "Service not found", 1)

        input_data = ServiceInput(service="nonexistent")
        result = self.cap.run(input_data)

        assert result.success is False
        assert "Service not found" in (result.error or "")

    @patch("elle.capabilities.core.service._get_service_state")
    def test_verify_success(self, mock_state):
        """Test successful verification."""
        mock_state.return_value = ("active", "running", 1234)

        input_data = ServiceInput(service="nginx")
        result = self.cap.verify(input_data)

        assert result.passed is True
        assert len(result.discrepancies) == 0

    @patch("elle.capabilities.core.service._get_service_state")
    def test_verify_failure(self, mock_state):
        """Test failed verification."""
        mock_state.return_value = ("failed", "failed", None)

        input_data = ServiceInput(service="nginx")
        result = self.cap.verify(input_data)

        assert result.passed is False
        assert len(result.discrepancies) > 0


# =============================================================================
# Service Start Tests
# =============================================================================


class TestServiceStartCapability:
    """Tests for ServiceStartCapability."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cap = ServiceStartCapability()

    def test_spec(self):
        """Test capability spec."""
        spec = self.cap.spec
        assert spec.name == "service.start"
        assert spec.risk == "low"
        assert spec.requires_privilege is True

    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run_already_active(self, mock_state):
        """Test dry run when service already active."""
        mock_state.return_value = ("active", "running", 1234)

        input_data = ServiceInput(service="nginx")
        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert "already active" in result.preview_text

    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run_inactive(self, mock_state):
        """Test dry run when service inactive."""
        mock_state.return_value = ("inactive", "dead", None)

        input_data = ServiceInput(service="nginx")
        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert "systemctl start nginx" in result.would_execute


# =============================================================================
# Service Stop Tests
# =============================================================================


class TestServiceStopCapability:
    """Tests for ServiceStopCapability."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cap = ServiceStopCapability()

    def test_spec(self):
        """Test capability spec."""
        spec = self.cap.spec
        assert spec.name == "service.stop"
        assert spec.risk == "medium"
        assert spec.requires_privilege is True

    @patch("elle.capabilities.core.service._get_service_state")
    def test_dry_run_not_running(self, mock_state):
        """Test dry run when service not running."""
        mock_state.return_value = ("inactive", "dead", None)

        input_data = ServiceInput(service="nginx")
        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert "not running" in result.preview_text

    @patch("elle.capabilities.core.service._get_service_state")
    def test_verify_stopped(self, mock_state):
        """Test verification when stopped."""
        mock_state.return_value = ("inactive", "dead", None)

        input_data = ServiceInput(service="nginx")
        result = self.cap.verify(input_data)

        assert result.passed is True


# =============================================================================
# Service Status Tests
# =============================================================================


class TestServiceStatusCapability:
    """Tests for ServiceStatusCapability."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cap = ServiceStatusCapability()

    def test_spec(self):
        """Test capability spec."""
        spec = self.cap.spec
        assert spec.name == "service.status"
        assert spec.risk == "none"
        assert spec.requires_privilege is False
        assert len(spec.side_effects) == 0  # Read-only

    def test_dry_run(self):
        """Test dry run preview."""
        input_data = ServiceInput(service="nginx")
        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert result.requires_confirmation is False

    @patch("elle.cli.subprocess_runner.run_safe")
    def test_run_success(self, mock_run):
        """Test successful status check."""
        mock_run.return_value = mock_run_safe(
            success=True,
            stdout="ActiveState=active\nSubState=running\nMainPID=1234\nDescription=nginx\nLoadState=loaded",
        )

        input_data = ServiceInput(service="nginx")
        result = self.cap.run(input_data)

        assert result.success is True
        assert result.output.service == "nginx"
        assert result.output.active_state == "active"
        assert result.output.sub_state == "running"
        assert result.output.pid == 1234
        assert result.output.loaded is True


# =============================================================================
# Input Validation Tests
# =============================================================================


class TestServiceInputValidation:
    """Tests for ServiceInput validation."""

    def test_valid_input(self):
        """Test valid input."""
        input_data = ServiceInput(service="nginx")
        assert input_data.service == "nginx"
        assert input_data.timeout_sec == 30  # Default

    def test_custom_timeout(self):
        """Test custom timeout."""
        input_data = ServiceInput(service="nginx", timeout_sec=60)
        assert input_data.timeout_sec == 60

    def test_empty_service_invalid(self):
        """Test that empty service name is invalid."""
        with pytest.raises(Exception):  # ValidationError
            ServiceInput(service="")

    def test_timeout_bounds(self):
        """Test timeout bounds."""
        # Too low
        with pytest.raises(Exception):
            ServiceInput(service="nginx", timeout_sec=0)

        # Too high
        with pytest.raises(Exception):
            ServiceInput(service="nginx", timeout_sec=500)
