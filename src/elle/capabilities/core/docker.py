"""Docker operation capabilities.

Provides typed, policy-governed operations for Docker container management:
- docker.prune - Clean unused images/containers/volumes
- docker.stop - Stop container by name/ID
- docker.inspect - Get container state, health, memory, logs
- docker.rollback - Pin container to previous image tag
- docker.list - List containers with status filter
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Helper functions
# =============================================================================


def _run_docker_command(
    args: list[str],
    timeout: int = 60,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a docker command safely.

    Args:
        args: Command arguments after 'docker'.
        timeout: Command timeout in seconds.
        check: Whether to raise on non-zero exit.

    Returns:
        CompletedProcess with stdout/stderr.
    """
    cmd = ["docker"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _is_docker_available() -> bool:
    """Check if docker is available and running."""
    try:
        result = _run_docker_command(["info"], timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _parse_json_output(output: str) -> Any:
    """Parse JSON output from docker commands."""
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


# =============================================================================
# Input/Output Models
# =============================================================================


class DockerPruneInput(BaseModel):
    """Input for docker prune operation."""

    model_config = ConfigDict(frozen=True)

    all_images: bool = Field(
        default=False,
        description="Remove all unused images, not just dangling ones",
    )
    volumes: bool = Field(
        default=False,
        description="Also prune volumes",
    )
    force: bool = Field(
        default=True,
        description="Do not prompt for confirmation",
    )


class DockerPruneOutput(BaseModel):
    """Output from docker prune operation."""

    model_config = ConfigDict(frozen=True)

    containers_deleted: int = Field(
        default=0,
        description="Number of containers removed",
    )
    images_deleted: int = Field(
        default=0,
        description="Number of images removed",
    )
    volumes_deleted: int = Field(
        default=0,
        description="Number of volumes removed",
    )
    space_reclaimed_bytes: int = Field(
        default=0,
        description="Total space reclaimed in bytes",
    )
    space_reclaimed_human: str = Field(
        default="0B",
        description="Human-readable space reclaimed",
    )
    details: str = Field(
        default="",
        description="Detailed output from prune commands",
    )


class DockerStopInput(BaseModel):
    """Input for docker stop operation."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(
        description="Container name or ID to stop",
    )
    timeout: int = Field(
        default=10,
        ge=0,
        le=300,
        description="Seconds to wait for graceful stop before kill",
    )


class DockerStopOutput(BaseModel):
    """Output from docker stop operation."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(description="Container that was stopped")
    container_id: str = Field(description="Full container ID")
    was_running: bool = Field(
        default=True,
        description="Whether container was running before stop",
    )
    graceful: bool = Field(
        default=True,
        description="Whether container stopped gracefully",
    )


class DockerInspectInput(BaseModel):
    """Input for docker inspect operation."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(
        description="Container name or ID to inspect",
    )
    include_logs: bool = Field(
        default=True,
        description="Include recent log lines",
    )
    log_lines: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Number of log lines to include",
    )


class DockerInspectOutput(BaseModel):
    """Output from docker inspect operation."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(description="Container name")
    container_id: str = Field(description="Full container ID")
    image: str = Field(description="Image name:tag")
    status: str = Field(description="Container status (running, exited, etc.)")
    state: str = Field(description="State detail")
    exit_code: int | None = Field(
        default=None,
        description="Exit code if exited",
    )
    restart_count: int = Field(
        default=0,
        description="Number of restarts",
    )
    health: str | None = Field(
        default=None,
        description="Health status if health check configured",
    )
    memory_usage_bytes: int | None = Field(
        default=None,
        description="Current memory usage in bytes",
    )
    memory_limit_bytes: int | None = Field(
        default=None,
        description="Memory limit in bytes",
    )
    created_at: str = Field(
        default="",
        description="Container creation timestamp",
    )
    started_at: str = Field(
        default="",
        description="Last start timestamp",
    )
    logs_excerpt: str = Field(
        default="",
        description="Recent log lines",
    )


class DockerRollbackInput(BaseModel):
    """Input for docker rollback operation."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(
        description="Container name to rollback",
    )
    previous_image: str | None = Field(
        default=None,
        description="Specific image to rollback to (auto-detects if not specified)",
    )
    stop_timeout: int = Field(
        default=10,
        ge=0,
        le=300,
        description="Timeout for stopping the current container",
    )


class DockerRollbackOutput(BaseModel):
    """Output from docker rollback operation."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(description="Container name")
    old_image: str = Field(description="Previous image")
    new_image: str = Field(description="Image rolled back to")
    new_container_id: str = Field(description="New container ID")
    success: bool = Field(description="Whether rollback succeeded")


class DockerListInput(BaseModel):
    """Input for docker list operation."""

    model_config = ConfigDict(frozen=True)

    all_containers: bool = Field(
        default=False,
        description="Show all containers (default shows only running)",
    )
    filter_status: str | None = Field(
        default=None,
        description="Filter by status (running, exited, paused, etc.)",
    )
    filter_name: str | None = Field(
        default=None,
        description="Filter by container name pattern",
    )


class ContainerInfo(BaseModel):
    """Information about a single container."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Container ID (short)")
    name: str = Field(description="Container name")
    image: str = Field(description="Image name")
    status: str = Field(description="Status string")
    state: str = Field(description="State (running, exited, etc.)")
    created: str = Field(description="Creation time")


class DockerListOutput(BaseModel):
    """Output from docker list operation."""

    model_config = ConfigDict(frozen=True)

    containers: tuple[ContainerInfo, ...] = Field(
        default_factory=tuple,
        description="List of containers",
    )
    total: int = Field(
        default=0,
        description="Total number of containers matching filter",
    )


# =============================================================================
# Docker Prune Capability
# =============================================================================


class DockerPruneCapability(BaseCapability):
    """Clean unused Docker resources."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="docker.prune",
            summary="Clean unused Docker images, containers, and optionally volumes",
            domain="docker",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="container_change",
                    target="docker-system",
                    reversible=False,
                    description="Removes unused Docker resources",
                ),
            ),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="DockerPruneInput",
            output_schema_name="DockerPruneOutput",
        )

    def dry_run(self, input: DockerPruneInput) -> DryRunResult:
        """Preview docker prune."""
        if not _is_docker_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("Docker is not available or not running",),
                preview_text="Cannot prune: Docker is not available",
            )

        # Get current disk usage
        try:
            result = _run_docker_command(["system", "df", "--format", "json"])
            if result.returncode == 0:
                # Parse output to show what would be cleaned
                preview = "Would clean unused Docker resources:\n"
                preview += "- Stopped containers\n"
                preview += "- Unused networks\n"
                preview += "- Dangling images\n"
                if input.all_images:
                    preview += "- ALL unused images (not just dangling)\n"
                if input.volumes:
                    preview += "- Unused volumes\n"
                preview += "\nRun 'docker system df' for current usage."
        except Exception:
            preview = "Would clean unused Docker resources"

        return DryRunResult(
            would_execute=(
                "docker container prune -f",
                "docker image prune -f" + (" -a" if input.all_images else ""),
                "docker network prune -f",
            ) + (("docker volume prune -f",) if input.volumes else ()),
            would_modify=("docker-system",),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text=preview,
            is_valid=True,
        )

    def run(self, input: DockerPruneInput) -> CapabilityResult:
        """Execute docker prune."""
        start_time = time.time()

        if not _is_docker_available():
            return CapabilityResult(
                success=False,
                error="Docker is not available or not running",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        total_space = 0
        containers_deleted = 0
        images_deleted = 0
        volumes_deleted = 0
        details_parts: list[str] = []
        commands_executed: list[str] = []

        try:
            # Prune containers
            force_flag = ["-f"] if input.force else []
            result = _run_docker_command(["container", "prune"] + force_flag)
            commands_executed.append("docker container prune -f")
            if result.returncode == 0:
                # Parse output for count
                for line in result.stdout.split("\n"):
                    if "Deleted Containers:" in line:
                        containers_deleted = len([l for l in result.stdout.split("\n") if l.strip() and not l.startswith("Deleted") and not l.startswith("Total")])
                    if "Total reclaimed space:" in line:
                        details_parts.append(f"Containers: {line}")

            # Prune images
            image_flags = force_flag + (["-a"] if input.all_images else [])
            result = _run_docker_command(["image", "prune"] + image_flags)
            commands_executed.append(f"docker image prune -f{' -a' if input.all_images else ''}")
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "Total reclaimed space:" in line:
                        details_parts.append(f"Images: {line}")

            # Prune networks
            result = _run_docker_command(["network", "prune"] + force_flag)
            commands_executed.append("docker network prune -f")

            # Prune volumes if requested
            if input.volumes:
                result = _run_docker_command(["volume", "prune"] + force_flag)
                commands_executed.append("docker volume prune -f")
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "Total reclaimed space:" in line:
                            details_parts.append(f"Volumes: {line}")

            # Get summary using system prune dry-run to estimate
            # We already did the actual prune, so just report what we found
            details = "\n".join(details_parts) if details_parts else "Prune completed"

            return CapabilityResult(
                success=True,
                output=DockerPruneOutput(
                    containers_deleted=containers_deleted,
                    images_deleted=images_deleted,
                    volumes_deleted=volumes_deleted,
                    space_reclaimed_bytes=total_space,
                    space_reclaimed_human="See details",
                    details=details,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="container_change",
                        target="docker-system",
                        reversible=False,
                        description="Pruned unused Docker resources",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=tuple(commands_executed),
                    rationale="Cleaned unused Docker resources to free disk space",
                ),
            )

        except subprocess.TimeoutExpired:
            return CapabilityResult(
                success=False,
                error="Docker prune timed out",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Docker Stop Capability
# =============================================================================


class DockerStopCapability(BaseCapability):
    """Stop a running container."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="docker.stop",
            summary="Stop a running Docker container",
            domain="docker",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="container_change",
                    target="{container}",
                    reversible=True,
                    description="Container will be stopped",
                ),
            ),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="DockerStopInput",
            output_schema_name="DockerStopOutput",
        )

    def dry_run(self, input: DockerStopInput) -> DryRunResult:
        """Preview docker stop."""
        if not _is_docker_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("Docker is not available",),
                preview_text="Cannot stop: Docker is not available",
            )

        # Check if container exists and is running
        result = _run_docker_command([
            "inspect",
            "--format",
            "{{.State.Running}}",
            input.container,
        ])

        if result.returncode != 0:
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Container not found: {input.container}",),
                preview_text=f"Cannot stop: container '{input.container}' not found",
            )

        is_running = result.stdout.strip() == "true"

        if not is_running:
            return DryRunResult(
                is_valid=True,
                would_execute=(),
                would_modify=(),
                estimated_risk="none",
                requires_confirmation=False,
                preview_text=f"Container '{input.container}' is already stopped",
            )

        return DryRunResult(
            would_execute=(f"docker stop -t {input.timeout} {input.container}",),
            would_modify=(input.container,),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text=f"Would stop container '{input.container}' (timeout: {input.timeout}s)",
            is_valid=True,
        )

    def run(self, input: DockerStopInput) -> CapabilityResult:
        """Stop the container."""
        start_time = time.time()

        if not _is_docker_available():
            return CapabilityResult(
                success=False,
                error="Docker is not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        # Get container ID first
        id_result = _run_docker_command([
            "inspect",
            "--format",
            "{{.Id}}",
            input.container,
        ])

        if id_result.returncode != 0:
            return CapabilityResult(
                success=False,
                error=f"Container not found: {input.container}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        container_id = id_result.stdout.strip()

        # Check if running
        running_result = _run_docker_command([
            "inspect",
            "--format",
            "{{.State.Running}}",
            input.container,
        ])
        was_running = running_result.stdout.strip() == "true"

        if not was_running:
            return CapabilityResult(
                success=True,
                output=DockerStopOutput(
                    container=input.container,
                    container_id=container_id,
                    was_running=False,
                    graceful=True,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Container '{input.container}' was already stopped",
                ),
            )

        # Stop the container
        result = _run_docker_command(
            ["stop", "-t", str(input.timeout), input.container],
            timeout=input.timeout + 10,
        )

        if result.returncode != 0:
            return CapabilityResult(
                success=False,
                error=f"Failed to stop container: {result.stderr}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        return CapabilityResult(
            success=True,
            output=DockerStopOutput(
                container=input.container,
                container_id=container_id,
                was_running=True,
                graceful=True,
            ),
            side_effects_applied=(
                SideEffect(
                    kind="container_change",
                    target=input.container,
                    reversible=True,
                    description=f"Stopped container {input.container}",
                ),
            ),
            execution_time_ms=int((time.time() - start_time) * 1000),
            evidence=CapabilityEvidence(
                commands_executed=(f"docker stop -t {input.timeout} {input.container}",),
                rationale=f"Stopped container '{input.container}'",
            ),
        )

    def verify(self, input: DockerStopInput) -> VerificationResult:
        """Verify container was stopped."""
        result = _run_docker_command([
            "inspect",
            "--format",
            "{{.State.Running}}",
            input.container,
        ])

        if result.returncode != 0:
            return VerificationResult(
                passed=False,
                checks_performed=("container exists",),
                discrepancies=("Container no longer exists",),
            )

        is_running = result.stdout.strip() == "true"

        return VerificationResult(
            passed=not is_running,
            checks_performed=("container stopped",),
            actual_state={"running": is_running},
            expected_state={"running": False},
            discrepancies=() if not is_running else ("Container is still running",),
        )

    def rollback(
        self,
        input: DockerStopInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by starting the container."""
        if not result.output or not result.output.was_running:
            return RollbackResult(
                success=True,
                rolled_back=(),
            )

        start_result = _run_docker_command(["start", input.container])

        if start_result.returncode != 0:
            return RollbackResult(
                success=False,
                error=f"Failed to start container: {start_result.stderr}",
                failed_to_rollback=(input.container,),
            )

        return RollbackResult(
            success=True,
            rolled_back=(input.container,),
            commands_executed=(f"docker start {input.container}",),
        )


# =============================================================================
# Docker Inspect Capability
# =============================================================================


class DockerInspectCapability(BaseCapability):
    """Inspect a container's state and logs."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="docker.inspect",
            summary="Get container state, health, memory usage, and recent logs",
            domain="docker",
            risk="none",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="DockerInspectInput",
            output_schema_name="DockerInspectOutput",
        )

    def dry_run(self, input: DockerInspectInput) -> DryRunResult:
        """Preview docker inspect."""
        if not _is_docker_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("Docker is not available",),
                preview_text="Cannot inspect: Docker is not available",
            )

        # Check if container exists
        result = _run_docker_command([
            "inspect",
            "--format",
            "{{.Name}}",
            input.container,
        ])

        if result.returncode != 0:
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Container not found: {input.container}",),
                preview_text=f"Cannot inspect: container '{input.container}' not found",
            )

        return DryRunResult(
            would_execute=(f"docker inspect {input.container}",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would inspect container '{input.container}'",
            is_valid=True,
        )

    def run(self, input: DockerInspectInput) -> CapabilityResult:
        """Inspect the container."""
        start_time = time.time()

        if not _is_docker_available():
            return CapabilityResult(
                success=False,
                error="Docker is not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        # Get container details
        result = _run_docker_command(["inspect", input.container])

        if result.returncode != 0:
            return CapabilityResult(
                success=False,
                error=f"Container not found: {input.container}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            data = json.loads(result.stdout)
            if not data:
                raise ValueError("Empty inspect output")

            info = data[0]
            state = info.get("State", {})
            config = info.get("Config", {})

            # Get name without leading /
            name = info.get("Name", "").lstrip("/")

            # Get health if available
            health_state = state.get("Health", {})
            health = health_state.get("Status") if health_state else None

            # Get logs if requested
            logs_excerpt = ""
            if input.include_logs:
                logs_result = _run_docker_command([
                    "logs",
                    "--tail",
                    str(input.log_lines),
                    input.container,
                ])
                if logs_result.returncode == 0:
                    logs_excerpt = (logs_result.stdout + logs_result.stderr)[-2000:]

            # Get memory stats
            memory_usage = None
            memory_limit = None
            if state.get("Running"):
                stats_result = _run_docker_command([
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.MemUsage}}",
                    input.container,
                ])
                # Memory stats parsing is complex, leaving as None for now

            return CapabilityResult(
                success=True,
                output=DockerInspectOutput(
                    container=name,
                    container_id=info.get("Id", ""),
                    image=config.get("Image", ""),
                    status=state.get("Status", "unknown"),
                    state="running" if state.get("Running") else "stopped",
                    exit_code=state.get("ExitCode"),
                    restart_count=info.get("RestartCount", 0),
                    health=health,
                    memory_usage_bytes=memory_usage,
                    memory_limit_bytes=memory_limit,
                    created_at=info.get("Created", ""),
                    started_at=state.get("StartedAt", ""),
                    logs_excerpt=logs_excerpt,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=(f"docker inspect {input.container}",),
                    rationale=f"Inspected container '{name}'",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=f"Failed to parse inspect output: {e}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Docker Rollback Capability
# =============================================================================


class DockerRollbackCapability(BaseCapability):
    """Rollback a container to a previous image."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="docker.rollback",
            summary="Stop container and restart with previous image tag",
            domain="docker",
            risk="high",
            side_effects=(
                SideEffect(
                    kind="container_change",
                    target="{container}",
                    reversible=True,
                    description="Container will be recreated with previous image",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="DockerRollbackInput",
            output_schema_name="DockerRollbackOutput",
        )

    def dry_run(self, input: DockerRollbackInput) -> DryRunResult:
        """Preview docker rollback."""
        if not _is_docker_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("Docker is not available",),
                preview_text="Cannot rollback: Docker is not available",
            )

        # Get current container info
        result = _run_docker_command(["inspect", input.container])

        if result.returncode != 0:
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Container not found: {input.container}",),
                preview_text=f"Cannot rollback: container '{input.container}' not found",
            )

        try:
            data = json.loads(result.stdout)
            current_image = data[0].get("Config", {}).get("Image", "unknown")
        except Exception:
            current_image = "unknown"

        previous_image = input.previous_image or f"{current_image.rsplit(':', 1)[0]}:previous"

        return DryRunResult(
            would_execute=(
                f"docker stop {input.container}",
                f"docker rm {input.container}",
                f"docker run ... {previous_image}",
            ),
            would_modify=(input.container,),
            estimated_risk="high",
            requires_confirmation=True,
            preview_text=f"Would rollback '{input.container}' from {current_image} to {previous_image}",
            is_valid=True,
        )

    def run(self, input: DockerRollbackInput) -> CapabilityResult:
        """Execute docker rollback."""
        start_time = time.time()

        if not _is_docker_available():
            return CapabilityResult(
                success=False,
                error="Docker is not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        # Get current container info to preserve settings
        result = _run_docker_command(["inspect", input.container])

        if result.returncode != 0:
            return CapabilityResult(
                success=False,
                error=f"Container not found: {input.container}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            data = json.loads(result.stdout)
            info = data[0]
            config = info.get("Config", {})
            host_config = info.get("HostConfig", {})

            current_image = config.get("Image", "")
            name = info.get("Name", "").lstrip("/")

            # Determine previous image
            if input.previous_image:
                previous_image = input.previous_image
            else:
                # Try to find previous tag
                # This is a simplified approach - in practice you might want to
                # look at image history or use labels to track previous versions
                previous_image = f"{current_image.rsplit(':', 1)[0]}:previous"

            # Verify previous image exists
            img_result = _run_docker_command(["image", "inspect", previous_image])
            if img_result.returncode != 0:
                return CapabilityResult(
                    success=False,
                    error=f"Previous image not found: {previous_image}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            # Stop current container
            _run_docker_command(
                ["stop", "-t", str(input.stop_timeout), input.container],
                timeout=input.stop_timeout + 10,
            )

            # Rename current container to keep as backup
            backup_name = f"{name}_backup_{int(time.time())}"
            _run_docker_command(["rename", input.container, backup_name])

            # Recreate container with previous image
            # Build run command preserving important settings
            run_args = ["run", "-d", "--name", name]

            # Preserve environment
            for env in config.get("Env", []):
                run_args.extend(["-e", env])

            # Preserve port bindings
            ports = host_config.get("PortBindings", {})
            for container_port, bindings in ports.items():
                if bindings:
                    for binding in bindings:
                        host_port = binding.get("HostPort", "")
                        if host_port:
                            run_args.extend(["-p", f"{host_port}:{container_port.split('/')[0]}"])

            # Preserve restart policy
            restart = host_config.get("RestartPolicy", {})
            if restart.get("Name"):
                run_args.extend(["--restart", restart["Name"]])

            run_args.append(previous_image)

            # Add original command if any
            cmd = config.get("Cmd")
            if cmd:
                run_args.extend(cmd)

            run_result = _run_docker_command(run_args)

            if run_result.returncode != 0:
                # Rollback failed - try to restore backup
                _run_docker_command(["rename", backup_name, name])
                _run_docker_command(["start", name])
                return CapabilityResult(
                    success=False,
                    error=f"Failed to create new container: {run_result.stderr}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            new_container_id = run_result.stdout.strip()

            # Remove backup container
            _run_docker_command(["rm", backup_name])

            return CapabilityResult(
                success=True,
                output=DockerRollbackOutput(
                    container=name,
                    old_image=current_image,
                    new_image=previous_image,
                    new_container_id=new_container_id,
                    success=True,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="container_change",
                        target=name,
                        reversible=True,
                        description=f"Rolled back from {current_image} to {previous_image}",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=tuple(["docker " + " ".join(run_args)]),
                    rationale=f"Rolled back container '{name}' to {previous_image}",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Docker List Capability
# =============================================================================


class DockerListCapability(BaseCapability):
    """List Docker containers."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="docker.list",
            summary="List Docker containers with optional filtering",
            domain="docker",
            risk="none",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="DockerListInput",
            output_schema_name="DockerListOutput",
        )

    def dry_run(self, input: DockerListInput) -> DryRunResult:
        """Preview docker list."""
        if not _is_docker_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("Docker is not available",),
                preview_text="Cannot list: Docker is not available",
            )

        return DryRunResult(
            would_execute=("docker ps",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text="Would list Docker containers",
            is_valid=True,
        )

    def run(self, input: DockerListInput) -> CapabilityResult:
        """List containers."""
        start_time = time.time()

        if not _is_docker_available():
            return CapabilityResult(
                success=False,
                error="Docker is not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        # Build command
        args = ["ps", "--format", "json"]

        if input.all_containers:
            args.append("-a")

        if input.filter_status:
            args.extend(["--filter", f"status={input.filter_status}"])

        if input.filter_name:
            args.extend(["--filter", f"name={input.filter_name}"])

        result = _run_docker_command(args)

        if result.returncode != 0:
            return CapabilityResult(
                success=False,
                error=f"Failed to list containers: {result.stderr}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        containers: list[ContainerInfo] = []

        # Parse JSON output (one object per line)
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                containers.append(ContainerInfo(
                    id=data.get("ID", "")[:12],
                    name=data.get("Names", ""),
                    image=data.get("Image", ""),
                    status=data.get("Status", ""),
                    state=data.get("State", ""),
                    created=data.get("CreatedAt", ""),
                ))
            except json.JSONDecodeError:
                continue

        return CapabilityResult(
            success=True,
            output=DockerListOutput(
                containers=tuple(containers),
                total=len(containers),
            ),
            execution_time_ms=int((time.time() - start_time) * 1000),
            evidence=CapabilityEvidence(
                commands_executed=("docker " + " ".join(args),),
                rationale=f"Listed {len(containers)} containers",
            ),
        )


# =============================================================================
# Exports
# =============================================================================

DOCKER_CAPABILITIES = [
    DockerPruneCapability,
    DockerStopCapability,
    DockerInspectCapability,
    DockerRollbackCapability,
    DockerListCapability,
]
