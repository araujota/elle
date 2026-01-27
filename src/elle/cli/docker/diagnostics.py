"""Docker container diagnostics for ELLE.

DEPRECATION NOTICE:
    This module's functionality is now wrapped by the `docker.diagnose`
    capability. The agent loop can execute this capability via:

        execute_capability("docker.diagnose", {
            "container": "my-container",
            "log_lines": 50
        })

    Using the capability system ensures:
    - Results are recorded to the Incident Vault for pattern matching
    - Policy enforcement via the Policy Engine
    - Audit trails for compliance

    Direct use of `diagnose_container_restart()` remains supported for
    backwards compatibility, but new code should use the capability.

Provides natural language diagnosis of container issues including:
- Restart loop analysis
- Resource usage explanation
- Health check failures
- Dependency issues

Example:
    diagnosis = diagnose_container_restart("my-container")
    print(diagnosis.summary)  # "Container exited with OOM kill..."

    explanation = explain_resource_usage()
    print(explanation.summary)  # "postgres is using 2.1GB memory..."
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class ContainerInfo(BaseModel):
    """Information about a Docker container."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Container ID")
    name: str = Field(description="Container name")
    image: str = Field(description="Image name")
    status: str = Field(description="Container status")
    state: str = Field(description="Container state (running, exited, etc.)")
    exit_code: int | None = Field(default=None, description="Exit code if exited")
    started_at: datetime | None = Field(default=None, description="Start time")
    finished_at: datetime | None = Field(default=None, description="Finish time")
    restart_count: int = Field(default=0, description="Number of restarts")
    oom_killed: bool = Field(default=False, description="Killed by OOM")
    health_status: str | None = Field(default=None, description="Health check status")


class ContainerResourceUsage(BaseModel):
    """Resource usage for a container."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Container ID")
    name: str = Field(description="Container name")
    cpu_percent: float = Field(default=0.0, description="CPU usage percentage")
    memory_usage: int = Field(default=0, description="Memory usage in bytes")
    memory_limit: int = Field(default=0, description="Memory limit in bytes")
    memory_percent: float = Field(default=0.0, description="Memory usage percentage")
    network_rx: int = Field(default=0, description="Network bytes received")
    network_tx: int = Field(default=0, description="Network bytes transmitted")
    block_read: int = Field(default=0, description="Block I/O read bytes")
    block_write: int = Field(default=0, description="Block I/O write bytes")


class ContainerDiagnosis(BaseModel):
    """Diagnosis result for container restart issues."""

    model_config = ConfigDict(frozen=True)

    container: ContainerInfo = Field(description="Container information")
    summary: str = Field(description="Natural language summary of the issue")
    likely_cause: str = Field(description="Most likely cause of the restart")
    causes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Possible causes ranked by likelihood",
    )
    logs_excerpt: str = Field(default="", description="Relevant log excerpt")
    recommendations: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Recommendations to fix the issue",
    )
    severity: str = Field(
        default="warning",
        description="Issue severity (info, warning, error, critical)",
    )


class ResourceExplanation(BaseModel):
    """Natural language explanation of container resource usage."""

    model_config = ConfigDict(frozen=True)

    summary: str = Field(description="Overall summary of resource usage")
    containers: tuple[ContainerResourceUsage, ...] = Field(
        default_factory=tuple,
        description="Per-container resource usage",
    )
    top_cpu: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Top CPU consumers with explanations",
    )
    top_memory: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Top memory consumers with explanations",
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Resource warnings",
    )
    total_memory_used: int = Field(default=0, description="Total memory used")
    total_cpu_percent: float = Field(default=0.0, description="Total CPU percentage")


def _run_docker_command(args: list[str], timeout: float = 30.0) -> str | None:
    """Run a docker command and return output.

    Args:
        args: Command arguments (without 'docker' prefix).
        timeout: Timeout in seconds.

    Returns:
        Command output or None if failed.
    """
    try:
        result = subprocess.run(
            ["docker"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
        logger.debug(f"Docker command failed: {result.stderr}")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"Docker command timed out: {args}")
        return None
    except FileNotFoundError:
        logger.error("Docker not installed or not in PATH")
        return None
    except Exception as e:
        logger.error(f"Docker command error: {e}")
        return None


def _get_container_info(container_id: str) -> ContainerInfo | None:
    """Get detailed container information.

    Args:
        container_id: Container ID or name.

    Returns:
        ContainerInfo or None if not found.
    """
    output = _run_docker_command(
        [
            "inspect",
            "--format",
            "{{json .}}",
            container_id,
        ]
    )

    if not output:
        return None

    try:
        data = json.loads(output)
        state = data.get("State", {})
        config = data.get("Config", {})

        # Parse timestamps
        started_at = None
        finished_at = None
        if state.get("StartedAt"):
            try:
                # Handle Docker timestamp format
                ts = state["StartedAt"].replace("Z", "+00:00")
                started_at = datetime.fromisoformat(ts.split(".")[0])
            except ValueError:
                pass

        if state.get("FinishedAt"):
            try:
                ts = state["FinishedAt"].replace("Z", "+00:00")
                finished_at = datetime.fromisoformat(ts.split(".")[0])
            except ValueError:
                pass

        # Get health status
        health_status = None
        if "Health" in state:
            health_status = state["Health"].get("Status")

        return ContainerInfo(
            id=data.get("Id", container_id)[:12],
            name=data.get("Name", "").lstrip("/"),
            image=config.get("Image", "unknown"),
            status=state.get("Status", "unknown"),
            state=state.get("Status", "unknown"),
            exit_code=state.get("ExitCode"),
            started_at=started_at,
            finished_at=finished_at,
            restart_count=data.get("RestartCount", 0),
            oom_killed=state.get("OOMKilled", False),
            health_status=health_status,
        )
    except json.JSONDecodeError:
        logger.error("Failed to parse container inspect output")
        return None


def _get_container_logs(container_id: str, lines: int = 50) -> str:
    """Get recent container logs.

    Args:
        container_id: Container ID or name.
        lines: Number of lines to retrieve.

    Returns:
        Log output.
    """
    output = _run_docker_command(
        [
            "logs",
            "--tail",
            str(lines),
            container_id,
        ]
    )
    return output or ""


def _analyze_logs_for_errors(logs: str) -> tuple[str, ...]:
    """Analyze logs for common error patterns.

    Args:
        logs: Log content.

    Returns:
        Tuple of detected issues.
    """
    issues: list[str] = []

    # Common error patterns
    patterns = [
        (r"(?i)connection refused", "Connection refused to dependency"),
        (r"(?i)permission denied", "Permission denied error"),
        (r"(?i)out of memory|oom|memory allocation", "Memory exhaustion"),
        (r"(?i)disk.?full|no space left", "Disk space exhaustion"),
        (r"(?i)timeout|timed out", "Timeout waiting for resource"),
        (r"(?i)segmentation fault|segfault|sigsegv", "Application crash (segfault)"),
        (r"(?i)killed|sigkill|sigterm", "Process killed by signal"),
        (r"(?i)failed to bind|address.*in use", "Port already in use"),
        (r"(?i)database.*connection|db.*error", "Database connection issue"),
        (r"(?i)certificate|ssl|tls.*error", "SSL/TLS certificate issue"),
        (r"(?i)authentication|auth.*fail|401|403", "Authentication failure"),
        (r"(?i)dns.*fail|resolve.*fail|unknown host", "DNS resolution failure"),
    ]

    for pattern, description in patterns:
        if re.search(pattern, logs):
            issues.append(description)

    return tuple(issues)


def diagnose_container_restart(container_id: str) -> ContainerDiagnosis:
    """Diagnose why a container keeps restarting.

    Analyzes:
    - Exit code and OOM status
    - Health check failures
    - Recent logs for error patterns
    - Dependency issues

    Args:
        container_id: Container ID or name.

    Returns:
        ContainerDiagnosis with analysis and recommendations.
    """
    # Get container info
    info = _get_container_info(container_id)
    if not info:
        return ContainerDiagnosis(
            container=ContainerInfo(
                id=container_id[:12] if len(container_id) >= 12 else container_id,
                name=container_id,
                image="unknown",
                status="not found",
                state="not found",
            ),
            summary=f"Container '{container_id}' not found",
            likely_cause="Container does not exist or is not accessible",
            causes=("Container not found", "Docker daemon not running"),
            recommendations=(
                "Check if the container exists: docker ps -a",
                "Verify Docker daemon is running: systemctl status docker",
            ),
            severity="error",
        )

    # Get logs
    logs = _get_container_logs(container_id)
    log_issues = _analyze_logs_for_errors(logs)

    # Build diagnosis
    causes: list[str] = []
    recommendations: list[str] = []
    severity = "warning"

    # Check OOM kill
    if info.oom_killed:
        causes.append("Container was killed by OOM (Out of Memory)")
        recommendations.append("Increase memory limit with --memory flag")
        recommendations.append("Check for memory leaks in application")
        severity = "error"

    # Check exit code
    if info.exit_code is not None and info.exit_code != 0:
        exit_code_meanings = {
            1: "General error",
            126: "Command not executable",
            127: "Command not found",
            128: "Invalid exit argument",
            137: "Killed by SIGKILL (possibly OOM or manual kill)",
            139: "Segmentation fault",
            143: "Killed by SIGTERM",
            255: "Exit status out of range",
        }
        meaning = exit_code_meanings.get(info.exit_code, f"Exit code {info.exit_code}")
        causes.append(f"Exited with code {info.exit_code}: {meaning}")

        if info.exit_code == 137:
            recommendations.append("Check if container hit memory limit")
            recommendations.append("Review 'docker events' for kill signals")
        elif info.exit_code == 127:
            recommendations.append("Check if entrypoint/command exists in image")
            recommendations.append("Verify image was built correctly")

    # Check health status
    if info.health_status == "unhealthy":
        causes.append("Health check is failing")
        recommendations.append("Review health check command and thresholds")
        recommendations.append("Check container logs for health check errors")
        severity = "warning"

    # Add log-detected issues
    for issue in log_issues:
        if issue not in causes:
            causes.append(f"Log analysis: {issue}")

    # Check restart count
    if info.restart_count > 5:
        causes.append(f"Container has restarted {info.restart_count} times")
        recommendations.append("Consider debugging with: docker logs -f <container>")
        severity = "error"

    # Build summary
    if info.oom_killed:
        likely_cause = "Out of memory (OOM) kill"
        summary = (
            f"Container '{info.name}' was killed due to memory exhaustion. It has restarted {info.restart_count} times."
        )
    elif info.exit_code == 137:
        likely_cause = "Killed by SIGKILL (likely OOM or external kill)"
        summary = f"Container '{info.name}' was killed by SIGKILL. This often indicates OOM or manual termination."
    elif info.health_status == "unhealthy":
        likely_cause = "Failing health checks"
        summary = f"Container '{info.name}' is unhealthy. The health check command is failing."
    elif log_issues:
        likely_cause = log_issues[0]
        summary = f"Container '{info.name}' is experiencing issues. Log analysis suggests: {likely_cause}"
    else:
        likely_cause = "Unknown - check container logs"
        summary = f"Container '{info.name}' exited with code {info.exit_code}. Review logs for more details."

    # Default recommendations
    if not recommendations:
        recommendations = [
            f"Check logs: docker logs {container_id}",
            f"Inspect container: docker inspect {container_id}",
            "Review resource limits and dependencies",
        ]

    # Truncate logs for excerpt
    logs_excerpt = logs[-1000:] if len(logs) > 1000 else logs

    return ContainerDiagnosis(
        container=info,
        summary=summary,
        likely_cause=likely_cause,
        causes=tuple(causes) if causes else ("Unknown - check logs",),
        logs_excerpt=logs_excerpt,
        recommendations=tuple(recommendations),
        severity=severity,
    )


def explain_resource_usage(container_id: str | None = None) -> ResourceExplanation:
    """Explain container resource consumption in natural language.

    Args:
        container_id: Optional container ID to focus on. If None, shows all.

    Returns:
        ResourceExplanation with natural language summary.

    Example:
        >>> explain_resource_usage()
        ResourceExplanation(
            summary="postgres is using 2.1GB memory (85% of limit), "
                    "redis is CPU-bound at 95% utilization",
            ...
        )
    """
    # Get stats
    args = ["stats", "--no-stream", "--format", "{{json .}}"]
    if container_id:
        args.append(container_id)

    output = _run_docker_command(args)
    if not output:
        return ResourceExplanation(
            summary="Unable to get container stats. Is Docker running?",
            warnings=("Docker stats unavailable",),
        )

    containers: list[ContainerResourceUsage] = []
    total_memory = 0
    total_cpu = 0.0

    for line in output.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)

            # Parse memory usage
            mem_usage = _parse_size(data.get("MemUsage", "0B / 0B").split("/")[0].strip())
            mem_limit = _parse_size(data.get("MemUsage", "0B / 0B").split("/")[-1].strip())
            mem_percent = float(data.get("MemPerc", "0%").rstrip("%"))

            # Parse CPU
            cpu_percent = float(data.get("CPUPerc", "0%").rstrip("%"))

            # Parse network
            net_io = data.get("NetIO", "0B / 0B").split("/")
            net_rx = _parse_size(net_io[0].strip()) if len(net_io) > 0 else 0
            net_tx = _parse_size(net_io[-1].strip()) if len(net_io) > 1 else 0

            # Parse block I/O
            block_io = data.get("BlockIO", "0B / 0B").split("/")
            block_read = _parse_size(block_io[0].strip()) if len(block_io) > 0 else 0
            block_write = _parse_size(block_io[-1].strip()) if len(block_io) > 1 else 0

            usage = ContainerResourceUsage(
                id=data.get("ID", "")[:12],
                name=data.get("Name", "unknown"),
                cpu_percent=cpu_percent,
                memory_usage=mem_usage,
                memory_limit=mem_limit,
                memory_percent=mem_percent,
                network_rx=net_rx,
                network_tx=net_tx,
                block_read=block_read,
                block_write=block_write,
            )
            containers.append(usage)
            total_memory += mem_usage
            total_cpu += cpu_percent

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.debug(f"Failed to parse stats line: {e}")
            continue

    if not containers:
        return ResourceExplanation(
            summary="No running containers found",
            warnings=("No containers to report on",),
        )

    # Sort by memory usage
    sorted_by_memory = sorted(containers, key=lambda c: c.memory_usage, reverse=True)
    sorted_by_cpu = sorted(containers, key=lambda c: c.cpu_percent, reverse=True)

    # Build top consumers
    top_memory: list[str] = []
    top_cpu: list[str] = []
    warnings: list[str] = []

    for c in sorted_by_memory[:3]:
        mem_str = _format_size(c.memory_usage)
        if c.memory_percent > 80:
            top_memory.append(f"{c.name} is using {mem_str} ({c.memory_percent:.0f}% of limit) - HIGH")
            warnings.append(f"{c.name} is near memory limit ({c.memory_percent:.0f}%)")
        elif c.memory_percent > 50:
            top_memory.append(f"{c.name} is using {mem_str} ({c.memory_percent:.0f}% of limit)")
        else:
            top_memory.append(f"{c.name} is using {mem_str}")

    for c in sorted_by_cpu[:3]:
        if c.cpu_percent > 90:
            top_cpu.append(f"{c.name} is CPU-bound at {c.cpu_percent:.0f}% utilization")
            warnings.append(f"{c.name} is CPU-bound ({c.cpu_percent:.0f}%)")
        elif c.cpu_percent > 50:
            top_cpu.append(f"{c.name} is using {c.cpu_percent:.0f}% CPU")
        else:
            top_cpu.append(f"{c.name} is using {c.cpu_percent:.0f}% CPU")

    # Build summary
    summary_parts: list[str] = []
    if sorted_by_memory and sorted_by_memory[0].memory_percent > 50:
        c = sorted_by_memory[0]
        summary_parts.append(f"{c.name} is using {_format_size(c.memory_usage)} ({c.memory_percent:.0f}% of limit)")
    if sorted_by_cpu and sorted_by_cpu[0].cpu_percent > 50:
        c = sorted_by_cpu[0]
        summary_parts.append(f"{c.name} is CPU-bound at {c.cpu_percent:.0f}%")

    if not summary_parts:
        summary = f"{len(containers)} containers running with normal resource usage"
    else:
        summary = ", ".join(summary_parts)

    return ResourceExplanation(
        summary=summary,
        containers=tuple(containers),
        top_cpu=tuple(top_cpu),
        top_memory=tuple(top_memory),
        warnings=tuple(warnings),
        total_memory_used=total_memory,
        total_cpu_percent=total_cpu,
    )


def _parse_size(size_str: str) -> int:
    """Parse size string to bytes.

    Args:
        size_str: Size string like "1.5GiB" or "500MB".

    Returns:
        Size in bytes.
    """
    size_str = size_str.strip().upper()

    units = {
        "B": 1,
        "KB": 1000,
        "KIB": 1024,
        "MB": 1000 * 1000,
        "MIB": 1024 * 1024,
        "GB": 1000 * 1000 * 1000,
        "GIB": 1024 * 1024 * 1024,
        "TB": 1000 * 1000 * 1000 * 1000,
        "TIB": 1024 * 1024 * 1024 * 1024,
    }

    for unit, multiplier in sorted(units.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(unit):
            try:
                value = float(size_str[: -len(unit)].strip())
                return int(value * multiplier)
            except ValueError:
                return 0

    # Try parsing as plain number
    try:
        return int(float(size_str))
    except ValueError:
        return 0


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Human-readable size string.
    """
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
