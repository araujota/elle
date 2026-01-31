"""Tests for Docker capabilities."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.docker import (
    DOCKER_CAPABILITIES,
    DockerConfigureEnvCapability,
    DockerConfigureEnvInput,
    DockerDiagnoseCapability,
    DockerDiagnoseInput,
    DockerInspectCapability,
    DockerInspectInput,
    DockerListCapability,
    DockerListInput,
    DockerPruneCapability,
    DockerPruneInput,
    DockerRollbackCapability,
    DockerRollbackInput,
    DockerStopCapability,
    DockerStopInput,
    EnvVarConfig,
    _is_docker_available,
    _parse_json_output,
    _run_docker_command,
)

# Shared patch target prefix for module-level helpers
_D = "elle.capabilities.core.docker"


# =============================================================================
# Helper function tests
# =============================================================================


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    @patch(f"{_D}.subprocess.run")
    def test_run_docker_command_prepends_docker(self, mock_run):
        """_run_docker_command prepends 'docker' to args."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = _run_docker_command(["ps", "-a"])

        mock_run.assert_called_once_with(
            ["docker", "ps", "-a"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0

    @patch(f"{_D}._run_docker_command")
    def test_is_docker_available_returns_true(self, mock_run):
        """_is_docker_available returns True when docker info succeeds."""
        mock_run.return_value = MagicMock(returncode=0)

        assert _is_docker_available() is True
        mock_run.assert_called_once_with(["info"], timeout=5)

    @patch(f"{_D}._run_docker_command")
    def test_is_docker_available_returns_false_on_error(self, mock_run):
        """_is_docker_available returns False when docker info fails."""
        mock_run.return_value = MagicMock(returncode=1)

        assert _is_docker_available() is False

    @patch(f"{_D}._run_docker_command")
    def test_is_docker_available_returns_false_on_timeout(self, mock_run):
        """_is_docker_available returns False on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=5)

        assert _is_docker_available() is False

    @patch(f"{_D}._run_docker_command")
    def test_is_docker_available_returns_false_on_file_not_found(self, mock_run):
        """_is_docker_available returns False when docker binary not found."""
        mock_run.side_effect = FileNotFoundError("docker not found")

        assert _is_docker_available() is False

    def test_parse_json_output_valid(self):
        """_parse_json_output parses valid JSON."""
        result = _parse_json_output('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_output_invalid(self):
        """_parse_json_output returns None for invalid JSON."""
        result = _parse_json_output("not json at all")
        assert result is None


# =============================================================================
# Docker Prune Capability
# =============================================================================


class TestDockerPruneCapability:
    """Tests for DockerPruneCapability."""

    def setup_method(self):
        self.cap = DockerPruneCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.prune"
        assert spec.domain == "docker"
        assert spec.risk == "medium"
        assert spec.idempotent is True
        assert spec.trust_level == "core"
        assert spec.requires_privilege is False
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].kind == "container_change"
        assert spec.side_effects[0].reversible is False

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Dry run returns invalid when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.dry_run(DockerPruneInput())

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()
        assert len(result.validation_errors) > 0

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_success_basic(self, mock_run, mock_available):
        """Dry run shows preview for basic prune without all_images or volumes."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        result = self.cap.dry_run(DockerPruneInput())

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "medium"
        # Should include container, image, network prune commands
        assert len(result.would_execute) == 3

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_with_all_images_and_volumes(self, mock_run, mock_available):
        """Dry run includes volume prune when volumes=True."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        result = self.cap.dry_run(DockerPruneInput(all_images=True, volumes=True))

        assert result.is_valid is True
        # 4 commands: container, image -a, network, volume
        assert len(result.would_execute) == 4
        assert any("volume" in cmd for cmd in result.would_execute)
        assert any("-a" in cmd for cmd in result.would_execute)

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_exception_in_system_df(self, mock_run, mock_available):
        """Dry run handles exception from docker system df gracefully."""
        mock_available.return_value = True
        mock_run.side_effect = Exception("Connection refused")

        result = self.cap.dry_run(DockerPruneInput())

        assert result.is_valid is True
        assert result.preview_text == "Would clean unused Docker resources"

    @patch(f"{_D}._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Run returns failure when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.run(DockerPruneInput())

        assert result.success is False
        assert "not available" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_success_basic(self, mock_run, mock_available):
        """Run executes prune commands and returns success."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Total reclaimed space: 1GB",
        )

        result = self.cap.run(DockerPruneInput())

        assert result.success is True
        assert result.output is not None
        assert result.evidence.commands_executed is not None
        assert len(result.evidence.commands_executed) > 0
        assert result.side_effects_applied[0].kind == "container_change"

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_with_volumes(self, mock_run, mock_available):
        """Run includes volume prune when volumes=True."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Total reclaimed space: 500MB",
        )

        result = self.cap.run(DockerPruneInput(volumes=True))

        assert result.success is True
        # Should have executed volume prune too
        assert any("volume" in cmd for cmd in result.evidence.commands_executed)

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_with_deleted_containers_output(self, mock_run, mock_available):
        """Run parses container deletion count from output."""
        mock_available.return_value = True

        prune_output = "Deleted Containers:\nabc123\ndef456\nTotal reclaimed space: 100MB"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=prune_output,
        )

        result = self.cap.run(DockerPruneInput())

        assert result.success is True

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_timeout_expired(self, mock_run, mock_available):
        """Run handles TimeoutExpired during prune."""
        mock_available.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=60)

        result = self.cap.run(DockerPruneInput())

        assert result.success is False
        assert "timed out" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_generic_exception(self, mock_run, mock_available):
        """Run handles generic exceptions."""
        mock_available.return_value = True
        mock_run.side_effect = OSError("Permission denied")

        result = self.cap.run(DockerPruneInput())

        assert result.success is False
        assert "Permission denied" in result.error


# =============================================================================
# Docker Stop Capability
# =============================================================================


class TestDockerStopCapability:
    """Tests for DockerStopCapability."""

    def setup_method(self):
        self.cap = DockerStopCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.stop"
        assert spec.domain == "docker"
        assert spec.risk == "medium"
        assert spec.idempotent is True
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].reversible is True

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Dry run returns invalid when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.dry_run(DockerStopInput(container="nginx"))

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_container_not_found(self, mock_run, mock_available):
        """Dry run returns invalid when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = self.cap.dry_run(DockerStopInput(container="nonexistent"))

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_container_already_stopped(self, mock_run, mock_available):
        """Dry run reports no-op when container is already stopped."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="false")

        result = self.cap.dry_run(DockerStopInput(container="nginx"))

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert "already stopped" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_running_container(self, mock_run, mock_available):
        """Dry run shows stop command for running container."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="true")

        result = self.cap.dry_run(DockerStopInput(container="nginx", timeout=15))

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "medium"
        assert "nginx" in result.preview_text

    @patch(f"{_D}._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Run returns failure when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.run(DockerStopInput(container="nginx"))

        assert result.success is False
        assert "not available" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_container_not_found(self, mock_run, mock_available):
        """Run returns failure when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such container")

        result = self.cap.run(DockerStopInput(container="missing"))

        assert result.success is False
        assert "not found" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_already_stopped(self, mock_run, mock_available):
        """Run succeeds when container is already stopped (idempotent)."""
        mock_available.return_value = True
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123def456"),  # inspect ID
            MagicMock(returncode=0, stdout="false"),  # inspect running
        ]

        result = self.cap.run(DockerStopInput(container="nginx"))

        assert result.success is True
        assert result.output.was_running is False
        assert result.output.container == "nginx"
        assert result.output.container_id == "abc123def456"

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_stop_success(self, mock_run, mock_available):
        """Run successfully stops a running container."""
        mock_available.return_value = True
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123def456"),  # inspect ID
            MagicMock(returncode=0, stdout="true"),  # inspect running
            MagicMock(returncode=0, stdout="abc123"),  # stop
        ]

        result = self.cap.run(DockerStopInput(container="nginx", timeout=15))

        assert result.success is True
        assert result.output.container == "nginx"
        assert result.output.was_running is True
        assert result.output.graceful is True
        assert len(result.side_effects_applied) == 1
        assert result.evidence.commands_executed == ("docker stop -t 15 nginx",)

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_stop_fails(self, mock_run, mock_available):
        """Run returns failure when docker stop command fails."""
        mock_available.return_value = True
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123def456"),  # inspect ID
            MagicMock(returncode=0, stdout="true"),  # inspect running
            MagicMock(returncode=1, stdout="", stderr="Cannot stop container"),  # stop
        ]

        result = self.cap.run(DockerStopInput(container="nginx"))

        assert result.success is False
        assert "Cannot stop container" in result.error

    @patch(f"{_D}._run_docker_command")
    def test_verify_container_stopped(self, mock_run):
        """Verify confirms container is not running."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false")

        result = self.cap.verify(DockerStopInput(container="nginx"))

        assert result.passed is True
        assert result.actual_state == {"running": False}
        assert result.expected_state == {"running": False}

    @patch(f"{_D}._run_docker_command")
    def test_verify_container_still_running(self, mock_run):
        """Verify reports failure if container is still running."""
        mock_run.return_value = MagicMock(returncode=0, stdout="true")

        result = self.cap.verify(DockerStopInput(container="nginx"))

        assert result.passed is False
        assert len(result.discrepancies) > 0

    @patch(f"{_D}._run_docker_command")
    def test_verify_container_not_found(self, mock_run):
        """Verify returns failure if container no longer exists."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = self.cap.verify(DockerStopInput(container="nginx"))

        assert result.passed is False
        assert "no longer exists" in result.discrepancies[0].lower()

    @patch(f"{_D}._run_docker_command")
    def test_rollback_starts_previously_running_container(self, mock_run):
        """Rollback starts the container that was previously running."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        input_data = DockerStopInput(container="nginx")
        run_result = MagicMock()
        run_result.output = MagicMock()
        run_result.output.was_running = True

        rollback = self.cap.rollback(input_data, run_result)

        assert rollback.success is True
        assert "nginx" in rollback.rolled_back
        mock_run.assert_called_once_with(["start", "nginx"])

    @patch(f"{_D}._run_docker_command")
    def test_rollback_skips_if_was_not_running(self, mock_run):
        """Rollback is no-op if container was not running before stop."""
        input_data = DockerStopInput(container="nginx")
        run_result = MagicMock()
        run_result.output = MagicMock()
        run_result.output.was_running = False

        rollback = self.cap.rollback(input_data, run_result)

        assert rollback.success is True
        assert rollback.rolled_back == ()
        mock_run.assert_not_called()

    @patch(f"{_D}._run_docker_command")
    def test_rollback_skips_if_no_output(self, mock_run):
        """Rollback is no-op if run result had no output."""
        input_data = DockerStopInput(container="nginx")
        run_result = MagicMock()
        run_result.output = None

        rollback = self.cap.rollback(input_data, run_result)

        assert rollback.success is True
        assert rollback.rolled_back == ()
        mock_run.assert_not_called()

    @patch(f"{_D}._run_docker_command")
    def test_rollback_failure_propagated(self, mock_run):
        """Rollback reports failure when docker start fails."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Permission denied")

        input_data = DockerStopInput(container="nginx")
        run_result = MagicMock()
        run_result.output = MagicMock()
        run_result.output.was_running = True

        rollback = self.cap.rollback(input_data, run_result)

        assert rollback.success is False
        assert "Permission denied" in rollback.error


# =============================================================================
# Docker Inspect Capability
# =============================================================================


class TestDockerInspectCapability:
    """Tests for DockerInspectCapability."""

    def setup_method(self):
        self.cap = DockerInspectCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.inspect"
        assert spec.domain == "docker"
        assert spec.risk == "none"
        assert spec.side_effects == ()
        assert spec.idempotent is True

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Dry run returns invalid when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.dry_run(DockerInspectInput(container="nginx"))

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_container_not_found(self, mock_run, mock_available):
        """Dry run returns invalid when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = self.cap.dry_run(DockerInspectInput(container="nonexistent"))

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_success(self, mock_run, mock_available):
        """Dry run shows inspect command for existing container."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="/nginx")

        result = self.cap.dry_run(DockerInspectInput(container="nginx"))

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"

    @patch(f"{_D}._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Run returns failure when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.run(DockerInspectInput(container="nginx"))

        assert result.success is False
        assert "not available" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_container_not_found(self, mock_run, mock_available):
        """Run returns failure when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such container")

        result = self.cap.run(DockerInspectInput(container="missing"))

        assert result.success is False
        assert "not found" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_success_running_container(self, mock_run, mock_available):
        """Run inspects a running container with logs and stats."""
        mock_available.return_value = True

        inspect_data = [
            {
                "Id": "abc123def456",
                "Name": "/nginx",
                "State": {
                    "Status": "running",
                    "Running": True,
                    "ExitCode": 0,
                    "StartedAt": "2024-01-01T00:00:00Z",
                    "Health": {"Status": "healthy"},
                },
                "Config": {"Image": "nginx:latest"},
                "RestartCount": 2,
                "Created": "2024-01-01T00:00:00Z",
            }
        ]
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(inspect_data)),  # inspect
            MagicMock(returncode=0, stdout="access log line", stderr=""),  # logs
            MagicMock(returncode=0, stdout="50MiB / 256MiB"),  # stats
        ]

        result = self.cap.run(DockerInspectInput(container="nginx"))

        assert result.success is True
        assert result.output.container == "nginx"
        assert result.output.container_id == "abc123def456"
        assert result.output.image == "nginx:latest"
        assert result.output.status == "running"
        assert result.output.state == "running"
        assert result.output.restart_count == 2
        assert result.output.health == "healthy"
        assert "access log line" in result.output.logs_excerpt

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_stopped_container_no_stats(self, mock_run, mock_available):
        """Run inspects a stopped container (no stats call)."""
        mock_available.return_value = True

        inspect_data = [
            {
                "Id": "abc123",
                "Name": "/myapp",
                "State": {
                    "Status": "exited",
                    "Running": False,
                    "ExitCode": 137,
                    "StartedAt": "2024-01-01T00:00:00Z",
                },
                "Config": {"Image": "myapp:v2"},
                "RestartCount": 0,
                "Created": "2024-01-01T00:00:00Z",
            }
        ]
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(inspect_data)),  # inspect
            MagicMock(returncode=0, stdout="error log", stderr=""),  # logs
        ]

        result = self.cap.run(DockerInspectInput(container="myapp"))

        assert result.success is True
        assert result.output.state == "stopped"
        assert result.output.exit_code == 137
        assert result.output.memory_usage_bytes is None

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_without_logs(self, mock_run, mock_available):
        """Run with include_logs=False skips log retrieval."""
        mock_available.return_value = True

        inspect_data = [
            {
                "Id": "abc123",
                "Name": "/nginx",
                "State": {"Status": "running", "Running": False, "ExitCode": 0, "StartedAt": ""},
                "Config": {"Image": "nginx:latest"},
                "RestartCount": 0,
                "Created": "",
            }
        ]
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(inspect_data)),  # inspect only
        ]

        result = self.cap.run(DockerInspectInput(container="nginx", include_logs=False))

        assert result.success is True
        assert result.output.logs_excerpt == ""

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_inspect_json_parse_failure(self, mock_run, mock_available):
        """Run handles invalid JSON from docker inspect."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json")

        result = self.cap.run(DockerInspectInput(container="nginx"))

        assert result.success is False
        assert "parse" in result.error.lower() or "Failed" in result.error

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_inspect_empty_output(self, mock_run, mock_available):
        """Run handles empty list from docker inspect."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")

        result = self.cap.run(DockerInspectInput(container="nginx"))

        assert result.success is False
        assert "empty" in result.error.lower() or "Failed" in result.error


# =============================================================================
# Docker Rollback Capability
# =============================================================================


class TestDockerRollbackCapability:
    """Tests for DockerRollbackCapability."""

    def setup_method(self):
        self.cap = DockerRollbackCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.rollback"
        assert spec.domain == "docker"
        assert spec.risk == "high"
        assert spec.idempotent is False
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].reversible is True

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Dry run returns invalid when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.dry_run(DockerRollbackInput(container="nginx"))

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_container_not_found(self, mock_run, mock_available):
        """Dry run returns invalid when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = self.cap.dry_run(DockerRollbackInput(container="missing"))

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_success_with_explicit_image(self, mock_run, mock_available):
        """Dry run preview with explicit previous_image."""
        mock_available.return_value = True
        inspect_json = json.dumps([{"Config": {"Image": "nginx:latest"}}])
        mock_run.return_value = MagicMock(returncode=0, stdout=inspect_json)

        result = self.cap.dry_run(DockerRollbackInput(container="nginx", previous_image="nginx:1.24"))

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "high"
        assert "rollback" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_auto_detects_previous_image(self, mock_run, mock_available):
        """Dry run auto-constructs previous image tag when not specified."""
        mock_available.return_value = True
        inspect_json = json.dumps([{"Config": {"Image": "myapp:v3"}}])
        mock_run.return_value = MagicMock(returncode=0, stdout=inspect_json)

        result = self.cap.dry_run(DockerRollbackInput(container="myapp"))

        assert result.is_valid is True
        assert "myapp:previous" in result.preview_text

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_inspect_json_parse_failure(self, mock_run, mock_available):
        """Dry run handles unparseable inspect JSON."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json")

        result = self.cap.dry_run(DockerRollbackInput(container="nginx"))

        # Should still be valid, falls back to 'unknown' image
        assert result.is_valid is True
        assert "unknown" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Run returns failure when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.run(DockerRollbackInput(container="nginx"))

        assert result.success is False
        assert "not available" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_container_not_found(self, mock_run, mock_available):
        """Run returns failure when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such container")

        result = self.cap.run(DockerRollbackInput(container="missing"))

        assert result.success is False
        assert "not found" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_previous_image_not_found(self, mock_run, mock_available):
        """Run returns failure when previous image does not exist locally."""
        mock_available.return_value = True

        inspect_json = json.dumps(
            [
                {
                    "Config": {"Image": "nginx:latest", "Env": [], "Cmd": None},
                    "HostConfig": {"PortBindings": {}, "RestartPolicy": {}},
                    "Name": "/nginx",
                }
            ]
        )
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=inspect_json),  # inspect container
            MagicMock(returncode=1, stdout="", stderr="No such image"),  # image inspect
        ]

        result = self.cap.run(DockerRollbackInput(container="nginx", previous_image="nginx:old"))

        assert result.success is False
        assert "not found" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    @patch(f"{_D}.time")
    def test_run_success_full_rollback(self, mock_time, mock_run, mock_available):
        """Run successfully rolls back container to previous image."""
        mock_available.return_value = True
        mock_time.time.return_value = 1700000000.0

        inspect_json = json.dumps(
            [
                {
                    "Config": {
                        "Image": "nginx:latest",
                        "Env": ["FOO=bar"],
                        "Cmd": ["nginx", "-g", "daemon off;"],
                    },
                    "HostConfig": {
                        "PortBindings": {
                            "80/tcp": [{"HostPort": "8080"}],
                        },
                        "RestartPolicy": {"Name": "always"},
                    },
                    "Name": "/nginx",
                }
            ]
        )

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=inspect_json),  # inspect container
            MagicMock(returncode=0, stdout=""),  # image inspect (exists)
            MagicMock(returncode=0, stdout=""),  # stop
            MagicMock(returncode=0, stdout=""),  # rename
            MagicMock(returncode=0, stdout="new_container_id_123"),  # run
            MagicMock(returncode=0, stdout=""),  # rm backup
        ]

        result = self.cap.run(DockerRollbackInput(container="nginx", previous_image="nginx:1.24"))

        assert result.success is True
        assert result.output.container == "nginx"
        assert result.output.old_image == "nginx:latest"
        assert result.output.new_image == "nginx:1.24"
        assert result.output.new_container_id == "new_container_id_123"
        assert result.output.success is True

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    @patch(f"{_D}.time")
    def test_run_rollback_failure_restores_backup(self, mock_time, mock_run, mock_available):
        """Run restores backup container when new container creation fails."""
        mock_available.return_value = True
        mock_time.time.return_value = 1700000000.0

        inspect_json = json.dumps(
            [
                {
                    "Config": {"Image": "nginx:latest", "Env": [], "Cmd": None},
                    "HostConfig": {"PortBindings": {}, "RestartPolicy": {}},
                    "Name": "/nginx",
                }
            ]
        )

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=inspect_json),  # inspect container
            MagicMock(returncode=0, stdout=""),  # image inspect
            MagicMock(returncode=0, stdout=""),  # stop
            MagicMock(returncode=0, stdout=""),  # rename
            MagicMock(returncode=1, stdout="", stderr="Failed to start"),  # run fails
            MagicMock(returncode=0, stdout=""),  # rename back
            MagicMock(returncode=0, stdout=""),  # start original
        ]

        result = self.cap.run(DockerRollbackInput(container="nginx", previous_image="nginx:1.24"))

        assert result.success is False
        assert "Failed to start" in result.error

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_generic_exception(self, mock_run, mock_available):
        """Run handles generic exceptions."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json")

        result = self.cap.run(DockerRollbackInput(container="nginx"))

        assert result.success is False
        assert result.error is not None


# =============================================================================
# Docker List Capability
# =============================================================================


class TestDockerListCapability:
    """Tests for DockerListCapability."""

    def setup_method(self):
        self.cap = DockerListCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.list"
        assert spec.domain == "docker"
        assert spec.risk == "none"
        assert spec.side_effects == ()
        assert spec.idempotent is True

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Dry run returns invalid when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.dry_run(DockerListInput())

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_success(self, mock_available):
        """Dry run shows docker ps command."""
        mock_available.return_value = True

        result = self.cap.dry_run(DockerListInput())

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert "docker ps" in result.would_execute

    @patch(f"{_D}._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Run returns failure when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.run(DockerListInput())

        assert result.success is False
        assert "not available" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_success_single_container(self, mock_run, mock_available):
        """Run lists a single container."""
        mock_available.return_value = True
        container_json = json.dumps(
            {
                "ID": "abc123def456",
                "Names": "nginx",
                "Image": "nginx:latest",
                "Status": "Up 2 hours",
                "State": "running",
                "CreatedAt": "2024-01-01",
            }
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=container_json + "\n")

        result = self.cap.run(DockerListInput())

        assert result.success is True
        assert result.output.total == 1
        assert result.output.containers[0].name == "nginx"
        assert result.output.containers[0].id == "abc123def456"

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_success_multiple_containers(self, mock_run, mock_available):
        """Run lists multiple containers."""
        mock_available.return_value = True
        lines = "\n".join(
            [
                json.dumps(
                    {
                        "ID": "aaa111",
                        "Names": "nginx",
                        "Image": "nginx:latest",
                        "Status": "Up",
                        "State": "running",
                        "CreatedAt": "2024-01-01",
                    }
                ),
                json.dumps(
                    {
                        "ID": "bbb222",
                        "Names": "redis",
                        "Image": "redis:7",
                        "Status": "Up",
                        "State": "running",
                        "CreatedAt": "2024-01-01",
                    }
                ),
            ]
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=lines)

        result = self.cap.run(DockerListInput())

        assert result.success is True
        assert result.output.total == 2

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_empty_list(self, mock_run, mock_available):
        """Run returns empty list when no containers match."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        result = self.cap.run(DockerListInput())

        assert result.success is True
        assert result.output.total == 0
        assert result.output.containers == ()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_with_all_containers_flag(self, mock_run, mock_available):
        """Run passes -a flag when all_containers=True."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        self.cap.run(DockerListInput(all_containers=True))

        args = mock_run.call_args[0][0]
        assert "-a" in args

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_with_status_filter(self, mock_run, mock_available):
        """Run passes status filter."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        self.cap.run(DockerListInput(filter_status="exited"))

        args = mock_run.call_args[0][0]
        assert "--filter" in args
        assert "status=exited" in args

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_with_name_filter(self, mock_run, mock_available):
        """Run passes name filter."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        self.cap.run(DockerListInput(filter_name="web"))

        args = mock_run.call_args[0][0]
        assert "--filter" in args
        assert "name=web" in args

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_command_failure(self, mock_run, mock_available):
        """Run returns failure when docker ps command fails."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="daemon not running")

        result = self.cap.run(DockerListInput())

        assert result.success is False
        assert "daemon not running" in result.error

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_run_skips_invalid_json_lines(self, mock_run, mock_available):
        """Run gracefully skips lines that are not valid JSON."""
        mock_available.return_value = True
        valid_line = json.dumps(
            {
                "ID": "aaa111",
                "Names": "nginx",
                "Image": "nginx:latest",
                "Status": "Up",
                "State": "running",
                "CreatedAt": "2024-01-01",
            }
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"header warning\n{valid_line}\ninvalid json\n",
        )

        result = self.cap.run(DockerListInput())

        assert result.success is True
        assert result.output.total == 1


# =============================================================================
# Docker Diagnose Capability
# =============================================================================


class TestDockerDiagnoseCapability:
    """Tests for DockerDiagnoseCapability."""

    def setup_method(self):
        self.cap = DockerDiagnoseCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.diagnose"
        assert spec.domain == "docker"
        assert spec.risk == "none"
        assert spec.requires_privilege is False
        assert spec.side_effects == ()
        assert spec.idempotent is True

    @patch(f"{_D}._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Dry run returns invalid when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.dry_run(DockerDiagnoseInput(container="test"))

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_container_not_found(self, mock_run, mock_available):
        """Dry run returns invalid when container does not exist."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = self.cap.dry_run(DockerDiagnoseInput(container="nonexistent"))

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower()

    @patch(f"{_D}._is_docker_available")
    @patch(f"{_D}._run_docker_command")
    def test_dry_run_success(self, mock_run, mock_available):
        """Dry run shows inspect and log commands."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="/myapp")

        result = self.cap.dry_run(DockerDiagnoseInput(container="myapp", log_lines=100))

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert any("inspect" in cmd for cmd in result.would_execute)
        assert any("logs" in cmd for cmd in result.would_execute)

    @patch(f"{_D}._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Run returns failure when Docker is not available."""
        mock_available.return_value = False

        result = self.cap.run(DockerDiagnoseInput(container="test"))

        assert result.success is False
        assert "not available" in result.error.lower()

    @patch(f"{_D}._is_docker_available")
    @patch("elle.cli.docker.diagnostics.diagnose_container_restart")
    def test_run_success(self, mock_diagnose, mock_available):
        """Run wraps diagnostics module and returns structured output."""
        mock_available.return_value = True

        from elle.cli.docker.diagnostics import ContainerDiagnosis, ContainerInfo

        mock_container = ContainerInfo(
            id="abc123def456",
            name="test-nginx",
            image="nginx:latest",
            status="exited",
            state="exited",
            exit_code=137,
            restart_count=3,
            oom_killed=True,
        )
        mock_diagnosis = ContainerDiagnosis(
            container=mock_container,
            summary="Container was killed due to memory exhaustion",
            likely_cause="Out of memory (OOM) kill",
            causes=("Container was killed by OOM",),
            logs_excerpt="Out of memory: Killed process",
            recommendations=("Increase memory limit",),
            severity="error",
        )
        mock_diagnose.return_value = mock_diagnosis

        result = self.cap.run(DockerDiagnoseInput(container="abc123"))

        assert result.success is True
        assert result.output.likely_cause == "Out of memory (OOM) kill"
        assert result.output.severity == "error"
        assert result.output.oom_killed is True
        assert result.output.exit_code == 137
        assert result.output.restart_count == 3
        mock_diagnose.assert_called_once_with("abc123")

    @patch(f"{_D}._is_docker_available")
    @patch("elle.cli.docker.diagnostics.diagnose_container_restart")
    def test_run_container_not_found_error(self, mock_diagnose, mock_available):
        """Run handles 'No such container' error from diagnostics."""
        mock_available.return_value = True
        mock_diagnose.side_effect = Exception("No such container: missing")

        result = self.cap.run(DockerDiagnoseInput(container="missing"))

        assert result.success is False
        assert "not found" in result.error.lower() or "No such container" in result.error

    @patch(f"{_D}._is_docker_available")
    @patch("elle.cli.docker.diagnostics.diagnose_container_restart")
    def test_run_generic_error(self, mock_diagnose, mock_available):
        """Run handles generic exception from diagnostics."""
        mock_available.return_value = True
        mock_diagnose.side_effect = RuntimeError("Connection refused")

        result = self.cap.run(DockerDiagnoseInput(container="test"))

        assert result.success is False
        assert "Connection refused" in result.error


# =============================================================================
# Docker Configure Env Capability
# =============================================================================


class TestDockerConfigureEnvCapability:
    """Tests for DockerConfigureEnvCapability."""

    def setup_method(self):
        self.cap = DockerConfigureEnvCapability()

    def test_spec_metadata(self):
        """Test capability spec has correct metadata."""
        spec = self.cap.spec
        assert spec.name == "docker.configure-env"
        assert spec.domain == "docker"
        assert spec.risk == "low"
        assert spec.idempotent is True
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].kind == "file_write"

    @patch("elle.cli.docker.env_profiles.extract_image_family")
    def test_dry_run_success(self, mock_extract):
        """Dry run shows env var configuration preview."""
        mock_extract.return_value = "postgres"

        input_data = DockerConfigureEnvInput(
            image="postgres:16",
            env_vars=(
                EnvVarConfig(name="POSTGRES_PASSWORD", value="secret", is_sensitive=True),
                EnvVarConfig(name="POSTGRES_DB", value="mydb"),
            ),
        )

        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert "postgres" in result.preview_text
        assert "2" in result.preview_text
        assert result.requires_confirmation is True  # has sensitive vars
        assert result.estimated_risk == "low"

    @patch("elle.cli.docker.env_profiles.extract_image_family")
    def test_dry_run_no_sensitive_vars_no_confirmation(self, mock_extract):
        """Dry run does not require confirmation when no sensitive vars."""
        mock_extract.return_value = "redis"

        input_data = DockerConfigureEnvInput(
            image="redis:7",
            env_vars=(EnvVarConfig(name="REDIS_PORT", value="6379"),),
        )

        result = self.cap.dry_run(input_data)

        assert result.is_valid is True
        assert result.requires_confirmation is False

    def test_dry_run_import_error(self):
        """Dry run handles ImportError when env_profiles module unavailable."""
        input_data = DockerConfigureEnvInput(
            image="postgres:16",
            env_vars=(EnvVarConfig(name="FOO", value="bar"),),
        )

        with patch(
            "elle.capabilities.core.docker.DockerConfigureEnvCapability.dry_run",
            wraps=self.cap.dry_run,
        ):
            with patch(
                "builtins.__import__",
                side_effect=ImportError("No module named 'elle.cli.docker.env_profiles'"),
            ):
                result = self.cap.dry_run(input_data)

                assert result.is_valid is False
                assert "not available" in result.preview_text.lower()

    @patch("elle.cli.docker.env_profiles.extract_image_family")
    @patch("elle.storage.engine.get_conn")
    def test_run_success_with_persist(self, mock_conn, mock_extract):
        """Run persists env vars to database."""
        mock_extract.return_value = "postgres"
        mock_context = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_context)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        input_data = DockerConfigureEnvInput(
            image="postgres:16",
            env_vars=(
                EnvVarConfig(name="POSTGRES_PASSWORD", value="secret", is_sensitive=True),
                EnvVarConfig(name="POSTGRES_DB", value="mydb"),
            ),
            persist=True,
        )

        result = self.cap.run(input_data)

        assert result.success is True
        assert result.output.image == "postgres:16"
        assert result.output.image_family == "postgres"
        assert result.output.vars_saved == 2
        assert result.output.persisted is True
        assert len(result.side_effects_applied) == 1

    @patch("elle.cli.docker.env_profiles.extract_image_family")
    def test_run_success_without_persist(self, mock_extract):
        """Run without persist skips database write."""
        mock_extract.return_value = "redis"

        input_data = DockerConfigureEnvInput(
            image="redis:7",
            env_vars=(EnvVarConfig(name="REDIS_PORT", value="6379"),),
            persist=False,
        )

        result = self.cap.run(input_data)

        assert result.success is True
        assert result.output.persisted is False
        assert result.side_effects_applied == ()

    @patch("elle.cli.docker.env_profiles.extract_image_family")
    @patch("elle.storage.engine.get_conn")
    def test_run_database_error(self, mock_conn, mock_extract):
        """Run handles database errors gracefully."""
        mock_extract.return_value = "postgres"
        mock_conn.side_effect = Exception("Database connection failed")

        input_data = DockerConfigureEnvInput(
            image="postgres:16",
            env_vars=(EnvVarConfig(name="FOO", value="bar"),),
            persist=True,
        )

        result = self.cap.run(input_data)

        assert result.success is False
        assert "Database connection failed" in result.error

    def test_run_import_error(self):
        """Run handles ImportError when modules unavailable."""
        input_data = DockerConfigureEnvInput(
            image="postgres:16",
            env_vars=(EnvVarConfig(name="FOO", value="bar"),),
        )

        with patch(
            "builtins.__import__",
            side_effect=ImportError("No module"),
        ):
            result = self.cap.run(input_data)

            assert result.success is False
            assert result.error is not None


# =============================================================================
# Input Validation Tests
# =============================================================================


class TestDockerInputValidation:
    """Tests for Pydantic input model validation."""

    def test_stop_input_requires_container(self):
        """DockerStopInput requires a container name."""
        with pytest.raises(pydantic.ValidationError):
            DockerStopInput()

    def test_stop_input_timeout_range(self):
        """DockerStopInput timeout must be between 0 and 300."""
        with pytest.raises(pydantic.ValidationError):
            DockerStopInput(container="nginx", timeout=-1)
        with pytest.raises(pydantic.ValidationError):
            DockerStopInput(container="nginx", timeout=301)

    def test_stop_input_valid_timeout_boundaries(self):
        """DockerStopInput accepts boundary timeout values."""
        inp_zero = DockerStopInput(container="nginx", timeout=0)
        assert inp_zero.timeout == 0
        inp_max = DockerStopInput(container="nginx", timeout=300)
        assert inp_max.timeout == 300

    def test_inspect_input_log_lines_range(self):
        """DockerInspectInput log_lines must be between 1 and 200."""
        with pytest.raises(pydantic.ValidationError):
            DockerInspectInput(container="nginx", log_lines=0)
        with pytest.raises(pydantic.ValidationError):
            DockerInspectInput(container="nginx", log_lines=201)

    def test_diagnose_input_log_lines_range(self):
        """DockerDiagnoseInput log_lines must be between 10 and 500."""
        with pytest.raises(pydantic.ValidationError):
            DockerDiagnoseInput(container="nginx", log_lines=5)
        with pytest.raises(pydantic.ValidationError):
            DockerDiagnoseInput(container="nginx", log_lines=501)

    def test_rollback_input_stop_timeout_range(self):
        """DockerRollbackInput stop_timeout must be between 0 and 300."""
        with pytest.raises(pydantic.ValidationError):
            DockerRollbackInput(container="nginx", stop_timeout=-1)
        with pytest.raises(pydantic.ValidationError):
            DockerRollbackInput(container="nginx", stop_timeout=301)

    def test_prune_input_defaults(self):
        """DockerPruneInput has correct defaults."""
        inp = DockerPruneInput()
        assert inp.all_images is False
        assert inp.volumes is False
        assert inp.force is True

    def test_list_input_defaults(self):
        """DockerListInput has correct defaults."""
        inp = DockerListInput()
        assert inp.all_containers is False
        assert inp.filter_status is None
        assert inp.filter_name is None

    def test_frozen_model_immutability(self):
        """Input models are frozen (immutable)."""
        inp = DockerStopInput(container="nginx")
        with pytest.raises(pydantic.ValidationError):
            inp.container = "redis"


# =============================================================================
# Capabilities Registry
# =============================================================================


class TestDockerCapabilitiesRegistry:
    """Tests for Docker capabilities registration."""

    def test_all_capabilities_exported(self):
        """DOCKER_CAPABILITIES contains all 7 capability classes."""
        assert len(DOCKER_CAPABILITIES) == 7

        names = {cap().spec.name for cap in DOCKER_CAPABILITIES}
        expected = {
            "docker.prune",
            "docker.stop",
            "docker.inspect",
            "docker.rollback",
            "docker.list",
            "docker.diagnose",
            "docker.configure-env",
        }
        assert names == expected

    def test_all_capabilities_are_docker_domain(self):
        """All exported capabilities belong to the docker domain."""
        for cap_cls in DOCKER_CAPABILITIES:
            cap = cap_cls()
            assert cap.spec.domain == "docker", f"{cap.spec.name} is not docker domain"

    def test_all_capabilities_are_core_trust(self):
        """All exported capabilities have core trust level."""
        for cap_cls in DOCKER_CAPABILITIES:
            cap = cap_cls()
            assert cap.spec.trust_level == "core", f"{cap.spec.name} is not core trust"
