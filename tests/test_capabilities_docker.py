"""Tests for Docker capabilities."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from elle.capabilities.core.docker import (
    DOCKER_CAPABILITIES,
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
)


class TestDockerPruneCapability:
    """Tests for DockerPruneCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = DockerPruneCapability()
        spec = cap.spec

        assert spec.name == "docker.prune"
        assert spec.domain == "docker"
        assert spec.risk == "medium"

    @patch("elle.capabilities.core.docker._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Test dry run when docker is not available."""
        mock_available.return_value = False

        cap = DockerPruneCapability()
        input_data = DockerPruneInput()

        result = cap.dry_run(input_data)

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_dry_run_success(self, mock_run, mock_available):
        """Test successful dry run."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        cap = DockerPruneCapability()
        input_data = DockerPruneInput(all_images=True, volumes=True)

        result = cap.dry_run(input_data)

        assert result.is_valid is True
        assert len(result.would_execute) > 0

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_run_success(self, mock_run, mock_available):
        """Test successful docker prune."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Total reclaimed space: 1GB",
        )

        cap = DockerPruneCapability()
        input_data = DockerPruneInput()

        result = cap.run(input_data)

        assert result.success is True
        assert result.output is not None

    @patch("elle.capabilities.core.docker._is_docker_available")
    def test_run_docker_not_available(self, mock_available):
        """Test run when docker is not available."""
        mock_available.return_value = False

        cap = DockerPruneCapability()
        input_data = DockerPruneInput()

        result = cap.run(input_data)

        assert result.success is False
        assert "not available" in result.error.lower()


class TestDockerStopCapability:
    """Tests for DockerStopCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = DockerStopCapability()
        spec = cap.spec

        assert spec.name == "docker.stop"
        assert spec.domain == "docker"
        assert spec.risk == "medium"

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_dry_run_container_not_found(self, mock_run, mock_available):
        """Test dry run with nonexistent container."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        cap = DockerStopCapability()
        input_data = DockerStopInput(container="nonexistent")

        result = cap.dry_run(input_data)

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower()

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_run_success(self, mock_run, mock_available):
        """Test successful container stop."""
        mock_available.return_value = True
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123def456"),  # inspect ID
            MagicMock(returncode=0, stdout="true"),  # inspect running
            MagicMock(returncode=0, stdout="abc123"),  # stop
        ]

        cap = DockerStopCapability()
        input_data = DockerStopInput(container="nginx")

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.container == "nginx"

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_run_already_stopped(self, mock_run, mock_available):
        """Test stopping already-stopped container."""
        mock_available.return_value = True
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="abc123def456"),  # inspect ID
            MagicMock(returncode=0, stdout="false"),  # inspect running (not running)
        ]

        cap = DockerStopCapability()
        input_data = DockerStopInput(container="nginx")

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.was_running is False


class TestDockerInspectCapability:
    """Tests for DockerInspectCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = DockerInspectCapability()
        spec = cap.spec

        assert spec.name == "docker.inspect"
        assert spec.domain == "docker"
        assert spec.risk == "none"

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_run_success(self, mock_run, mock_available):
        """Test successful container inspect."""
        mock_available.return_value = True

        # Mock inspect output - must be valid JSON
        import json
        inspect_data = [{
            "Id": "abc123def456",
            "Name": "/nginx",
            "State": {
                "Status": "running",
                "Running": True,
                "ExitCode": 0,
                "StartedAt": "2024-01-01T00:00:00Z"
            },
            "Config": {
                "Image": "nginx:latest"
            },
            "RestartCount": 0,
            "Created": "2024-01-01T00:00:00Z"
        }]
        inspect_json = json.dumps(inspect_data)

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=inspect_json),  # inspect
            MagicMock(returncode=0, stdout="test logs", stderr=""),  # logs
            MagicMock(returncode=0, stdout="10MiB / 100MiB"),  # stats
        ]

        cap = DockerInspectCapability()
        input_data = DockerInspectInput(container="nginx")

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.container == "nginx"
        assert result.output.status == "running"


class TestDockerRollbackCapability:
    """Tests for DockerRollbackCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = DockerRollbackCapability()
        spec = cap.spec

        assert spec.name == "docker.rollback"
        assert spec.domain == "docker"
        assert spec.risk == "high"

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_dry_run_success(self, mock_run, mock_available):
        """Test dry run preview."""
        mock_available.return_value = True

        inspect_json = """[{
            "Config": {"Image": "nginx:latest"}
        }]"""
        mock_run.return_value = MagicMock(returncode=0, stdout=inspect_json)

        cap = DockerRollbackCapability()
        input_data = DockerRollbackInput(
            container="nginx",
            previous_image="nginx:1.24",
        )

        result = cap.dry_run(input_data)

        assert result.is_valid is True
        assert "rollback" in result.preview_text.lower()


class TestDockerListCapability:
    """Tests for DockerListCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = DockerListCapability()
        spec = cap.spec

        assert spec.name == "docker.list"
        assert spec.domain == "docker"
        assert spec.risk == "none"

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_run_success(self, mock_run, mock_available):
        """Test successful container list."""
        mock_available.return_value = True

        # Mock ps output (JSON lines format)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"ID":"abc123","Names":"nginx","Image":"nginx:latest","Status":"Up 2 hours","State":"running","CreatedAt":"2024-01-01"}\n',
        )

        cap = DockerListCapability()
        input_data = DockerListInput()

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.total >= 1

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_run_empty_list(self, mock_run, mock_available):
        """Test list with no containers."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        cap = DockerListCapability()
        input_data = DockerListInput()

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.total == 0


class TestDockerCapabilitiesRegistry:
    """Tests for Docker capabilities registration."""

    def test_all_capabilities_exported(self):
        """Test all capabilities are in DOCKER_CAPABILITIES."""
        assert len(DOCKER_CAPABILITIES) == 5

        names = {cap().spec.name for cap in DOCKER_CAPABILITIES}
        expected = {
            "docker.prune",
            "docker.stop",
            "docker.inspect",
            "docker.rollback",
            "docker.list",
        }
        assert names == expected
