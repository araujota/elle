"""Tests for Docker capabilities."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from elle.capabilities.core.docker import (
    DOCKER_CAPABILITIES,
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


class TestDockerDiagnoseCapability:
    """Tests for DockerDiagnoseCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = DockerDiagnoseCapability()
        spec = cap.spec

        assert spec.name == "docker.diagnose"
        assert spec.domain == "docker"
        assert spec.risk == "none"
        assert spec.requires_privilege is False
        assert spec.side_effects == ()
        assert spec.idempotent is True

    @patch("elle.capabilities.core.docker._is_docker_available")
    def test_dry_run_docker_not_available(self, mock_available):
        """Test dry run when docker is not available."""
        mock_available.return_value = False

        cap = DockerDiagnoseCapability()
        input_data = DockerDiagnoseInput(container="test")

        result = cap.dry_run(input_data)

        assert result.is_valid is False
        assert "not available" in result.preview_text.lower()

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.capabilities.core.docker._run_docker_command")
    def test_dry_run_container_not_found(self, mock_run, mock_available):
        """Test dry run with nonexistent container."""
        mock_available.return_value = True
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        cap = DockerDiagnoseCapability()
        input_data = DockerDiagnoseInput(container="nonexistent")

        result = cap.dry_run(input_data)

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower()

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.cli.docker.diagnostics.diagnose_container_restart")
    def test_run_wraps_diagnostics_module(self, mock_diagnose, mock_available):
        """Should wrap existing diagnostics.py function."""
        mock_available.return_value = True

        # Create mock diagnosis result
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

        cap = DockerDiagnoseCapability()
        result = cap.run(DockerDiagnoseInput(container="abc123"))

        assert result.success is True
        assert result.output.likely_cause == "Out of memory (OOM) kill"
        assert result.output.severity == "error"
        assert result.output.oom_killed is True
        mock_diagnose.assert_called_once_with("abc123")

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.cli.docker.diagnostics.diagnose_container_restart")
    def test_evidence_includes_docker_commands(self, mock_diagnose, mock_available):
        """Evidence should list docker inspect/logs commands."""
        mock_available.return_value = True

        from elle.cli.docker.diagnostics import ContainerDiagnosis, ContainerInfo

        mock_container = ContainerInfo(
            id="test123",
            name="test",
            image="test:latest",
            status="exited",
            state="exited",
        )
        mock_diagnose.return_value = ContainerDiagnosis(
            container=mock_container,
            summary="Test",
            likely_cause="Unknown",
            severity="info",
        )

        cap = DockerDiagnoseCapability()
        result = cap.run(DockerDiagnoseInput(container="test123"))

        assert result.success is True
        assert "docker inspect test123" in result.evidence.commands_executed
        assert "docker logs" in str(result.evidence.commands_executed)

    @patch("elle.capabilities.core.docker._is_docker_available")
    @patch("elle.cli.docker.diagnostics.diagnose_container_restart")
    def test_handles_container_not_found(self, mock_diagnose, mock_available):
        """Should handle missing container gracefully."""
        mock_available.return_value = True
        mock_diagnose.side_effect = Exception("No such container: missing")

        cap = DockerDiagnoseCapability()
        result = cap.run(DockerDiagnoseInput(container="missing"))

        assert result.success is False
        assert "not found" in result.error.lower() or "No such container" in result.error


class TestDockerAutodiagnoseReactiveTemplate:
    """Tests for the reactive function template."""

    def test_template_exists(self):
        """Template should be importable."""
        from elle.reactive.templates import DOCKER_AUTO_DIAGNOSE

        assert DOCKER_AUTO_DIAGNOSE is not None
        assert DOCKER_AUTO_DIAGNOSE.id == "builtin:docker-auto-diagnose"

    def test_template_trigger_matches_death_events(self):
        """Template should trigger on die, oom, kill events."""
        from elle.reactive.templates import DOCKER_AUTO_DIAGNOSE

        trigger = DOCKER_AUTO_DIAGNOSE.trigger
        assert trigger.type == "event"
        assert trigger.event is not None
        assert trigger.event.source == "probe"
        assert trigger.event.category == "docker"

        # Check match patterns include death events
        match = trigger.event.match
        assert "$or" in match
        or_conditions = match["$or"]
        event_types = [c.get("_DOCKER_EVENT") for c in or_conditions]
        assert "die" in event_types
        assert "oom" in event_types
        assert "kill" in event_types

    def test_template_invokes_diagnose_capability(self):
        """Template action should invoke docker.diagnose."""
        from elle.reactive.templates import DOCKER_AUTO_DIAGNOSE

        assert len(DOCKER_AUTO_DIAGNOSE.actions) == 1
        assert DOCKER_AUTO_DIAGNOSE.actions[0].capability == "docker.diagnose"
        assert "container" in DOCKER_AUTO_DIAGNOSE.actions[0].input

    def test_template_has_rate_limiting(self):
        """Template should rate-limit to avoid crashloop spam."""
        from elle.reactive.templates import DOCKER_AUTO_DIAGNOSE

        policy = DOCKER_AUTO_DIAGNOSE.policy
        assert policy.max_frequency is not None
        assert policy.max_frequency == "1m"
        assert policy.require_confirmation is False

    def test_template_is_enabled_by_default(self):
        """Docker diagnose template should be enabled by default."""
        from elle.reactive.templates import DOCKER_AUTO_DIAGNOSE

        assert DOCKER_AUTO_DIAGNOSE.enabled is True

    def test_builtin_templates_list(self):
        """BUILTIN_TEMPLATES should include docker-auto-diagnose."""
        from elle.reactive.templates import BUILTIN_TEMPLATES

        template_ids = [t.id for t in BUILTIN_TEMPLATES]
        assert "builtin:docker-auto-diagnose" in template_ids


class TestDockerCapabilitiesRegistry:
    """Tests for Docker capabilities registration."""

    def test_all_capabilities_exported(self):
        """Test all capabilities are in DOCKER_CAPABILITIES."""
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
