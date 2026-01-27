"""Unified input model for the agentic system.

Provides a normalized entry point for all triggers:
- User messages (questions and tasks)
- Daemon-detected incidents (auto or manual)
- Command failures (fixit requests)

This module ensures all inputs flow through a single path regardless
of their origin, enabling consistent retrieval, execution, and audit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InputSource(str, Enum):
    """Source of the agentic input."""

    USER = "user"  # User typed question/task
    INCIDENT_AUTO = "incident_auto"  # Daemon detected, auto-triggered
    INCIDENT_MANUAL = "incident_manual"  # User requested incident handling
    COMMAND_FAILURE = "command_failure"  # Command failed, needs fixit


class FailedCommand(BaseModel):
    """Details of a failed command for fixit handling."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The command that failed")
    exit_code: int = Field(description="Exit code of the failed command")
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")
    cwd: str = Field(default="", description="Working directory when command ran")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the command failed",
    )


class SystemFingerprint(BaseModel):
    """System state fingerprint for context matching.

    A simplified fingerprint for quick matching without full snapshot.
    """

    model_config = ConfigDict(frozen=True)

    # Pressure indicators (0.0-1.0)
    memory_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    disk_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    cpu_pressure: float = Field(default=0.0, ge=0.0, le=1.0)

    # Service states
    failed_services: tuple[str, ...] = Field(default_factory=tuple)
    degraded_services: tuple[str, ...] = Field(default_factory=tuple)

    # Container states
    unhealthy_containers: tuple[str, ...] = Field(default_factory=tuple)
    restarting_containers: tuple[str, ...] = Field(default_factory=tuple)

    # Network indicators
    network_errors: bool = Field(default=False)
    dns_issues: bool = Field(default=False)

    # Hardware indicators
    disk_degradation: bool = Field(default=False)
    thermal_throttling: bool = Field(default=False)

    @classmethod
    def empty(cls) -> SystemFingerprint:
        """Create an empty fingerprint."""
        return cls()

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> SystemFingerprint:
        """Create a fingerprint from a SystemSnapshot.

        Args:
            snapshot: SystemSnapshot from daemon/incidents/models.py

        Returns:
            Simplified fingerprint for quick matching.
        """
        if snapshot is None:
            return cls.empty()

        # Calculate pressure from snapshot data
        memory_pressure = 0.0
        disk_pressure = 0.0
        cpu_pressure = 0.0

        if hasattr(snapshot, "memory") and snapshot.memory:
            used = getattr(snapshot.memory, "used_pct", 0.0)
            memory_pressure = min(1.0, used / 100.0)

        if hasattr(snapshot, "disk") and snapshot.disk:
            # Average disk usage across mounts
            usages = [
                getattr(d, "used_pct", 0.0)
                for d in snapshot.disk
                if hasattr(d, "used_pct")
            ]
            if usages:
                disk_pressure = min(1.0, max(usages) / 100.0)

        if hasattr(snapshot, "cpu") and snapshot.cpu:
            load = getattr(snapshot.cpu, "load_1m", 0.0)
            cores = getattr(snapshot.cpu, "cores", 1)
            cpu_pressure = min(1.0, load / max(cores, 1))

        # Extract failed/degraded services
        failed_services: list[str] = []
        degraded_services: list[str] = []
        if hasattr(snapshot, "services") and snapshot.services:
            for svc in snapshot.services:
                if hasattr(svc, "state"):
                    if svc.state == "failed":
                        failed_services.append(getattr(svc, "name", "unknown"))
                    elif svc.state in ("degraded", "activating"):
                        degraded_services.append(getattr(svc, "name", "unknown"))

        # Extract unhealthy containers
        unhealthy_containers: list[str] = []
        restarting_containers: list[str] = []
        if hasattr(snapshot, "docker") and snapshot.docker:
            for c in snapshot.docker:
                if hasattr(c, "status"):
                    if "unhealthy" in c.status.lower():
                        unhealthy_containers.append(getattr(c, "name", "unknown"))
                    elif "restarting" in c.status.lower():
                        restarting_containers.append(getattr(c, "name", "unknown"))

        return cls(
            memory_pressure=memory_pressure,
            disk_pressure=disk_pressure,
            cpu_pressure=cpu_pressure,
            failed_services=tuple(failed_services),
            degraded_services=tuple(degraded_services),
            unhealthy_containers=tuple(unhealthy_containers),
            restarting_containers=tuple(restarting_containers),
        )


class AgenticInput(BaseModel):
    """Unified input model for the agentic system.

    Normalizes all trigger types into a single model that can be
    processed by the unified retrieval pipeline and execution loop.

    Examples:
        # User question
        AgenticInput(
            source=InputSource.USER,
            user_message="why is nginx failing?",
        )

        # Daemon-detected incident
        AgenticInput(
            source=InputSource.INCIDENT_AUTO,
            incident_id="abc123",
        )

        # Command failure
        AgenticInput(
            source=InputSource.COMMAND_FAILURE,
            failed_command=FailedCommand(command="nginx -t", exit_code=1, stderr="..."),
        )
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    input_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID for this input",
    )
    source: InputSource = Field(description="Where this input originated")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this input was created",
    )

    # Content (one of these should be set based on source)
    user_message: str | None = Field(
        default=None,
        description="User's question or task request (for USER source)",
    )
    incident_id: str | None = Field(
        default=None,
        description="Incident ID to handle (for INCIDENT_* sources)",
    )
    failed_command: FailedCommand | None = Field(
        default=None,
        description="Failed command details (for COMMAND_FAILURE source)",
    )

    # Context
    system_fingerprint: SystemFingerprint | None = Field(
        default=None,
        description="System state fingerprint for context matching",
    )
    session_id: str | None = Field(
        default=None,
        description="Session ID for audit trail linking",
    )

    # Metadata
    entities: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Extracted entities (commands, services, files)",
    )
    domain_hint: str | None = Field(
        default=None,
        description="Domain hint from classification (net, disk, docker, etc.)",
    )

    @property
    def query_text(self) -> str:
        """Get primary text for retrieval queries.

        Returns:
            Text suitable for searching incidents/man vault.
        """
        if self.user_message:
            return self.user_message

        if self.failed_command:
            parts = [f"Command failed: {self.failed_command.command}"]
            if self.failed_command.stderr:
                parts.append(self.failed_command.stderr[:500])
            return " ".join(parts)

        if self.incident_id:
            return f"incident:{self.incident_id}"

        return ""

    @property
    def is_task(self) -> bool:
        """Check if this input requests a system change.

        Returns:
            True if this is a task/action request vs a question.
        """
        # Incident handling is always a task
        if self.source in (InputSource.INCIDENT_AUTO, InputSource.INCIDENT_MANUAL):
            return True

        # Command failures requesting fixit are tasks
        if self.source == InputSource.COMMAND_FAILURE:
            return True

        # For user messages, check for task indicators
        if self.user_message:
            msg_lower = self.user_message.lower()
            task_words = (
                "restart",
                "start",
                "stop",
                "enable",
                "disable",
                "install",
                "remove",
                "delete",
                "create",
                "configure",
                "fix",
                "repair",
            )
            return any(word in msg_lower for word in task_words)

        return False

    @classmethod
    def from_user_message(
        cls,
        message: str,
        *,
        session_id: str | None = None,
        entities: tuple[str, ...] = (),
        domain_hint: str | None = None,
        fingerprint: SystemFingerprint | None = None,
    ) -> AgenticInput:
        """Create an AgenticInput from a user message.

        Args:
            message: User's question or task request.
            session_id: Optional session ID for tracking.
            entities: Extracted entities from classification.
            domain_hint: Domain hint from classification.
            fingerprint: Optional system fingerprint.

        Returns:
            AgenticInput configured for user message.
        """
        return cls(
            source=InputSource.USER,
            user_message=message,
            session_id=session_id,
            entities=entities,
            domain_hint=domain_hint,
            system_fingerprint=fingerprint,
        )

    @classmethod
    def from_incident(
        cls,
        incident_id: str,
        *,
        auto_triggered: bool = False,
        session_id: str | None = None,
        fingerprint: SystemFingerprint | None = None,
    ) -> AgenticInput:
        """Create an AgenticInput from a daemon incident.

        Args:
            incident_id: ID of the incident to handle.
            auto_triggered: Whether this was auto-triggered by daemon.
            session_id: Optional session ID for tracking.
            fingerprint: Optional system fingerprint.

        Returns:
            AgenticInput configured for incident handling.
        """
        return cls(
            source=InputSource.INCIDENT_AUTO if auto_triggered else InputSource.INCIDENT_MANUAL,
            incident_id=incident_id,
            session_id=session_id,
            system_fingerprint=fingerprint,
        )

    @classmethod
    def from_failed_command(
        cls,
        command: str,
        exit_code: int,
        *,
        stdout: str = "",
        stderr: str = "",
        cwd: str = "",
        session_id: str | None = None,
        fingerprint: SystemFingerprint | None = None,
    ) -> AgenticInput:
        """Create an AgenticInput from a failed command.

        Args:
            command: The command that failed.
            exit_code: Exit code of the failed command.
            stdout: Standard output from the command.
            stderr: Standard error from the command.
            cwd: Working directory when command ran.
            session_id: Optional session ID for tracking.
            fingerprint: Optional system fingerprint.

        Returns:
            AgenticInput configured for command failure handling.
        """
        return cls(
            source=InputSource.COMMAND_FAILURE,
            failed_command=FailedCommand(
                command=command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                cwd=cwd,
            ),
            session_id=session_id,
            system_fingerprint=fingerprint,
        )
