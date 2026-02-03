"""IncidentContext - mandatory incident recording middleware.

Provides an async context manager that ensures incidents are always created
and finalized, with proper recording of actions and outcomes.

This is ESSENTIAL for the Spine architecture:
    DAEMON -> SIGNALS -> INCIDENT REPORT -> AGENT LOOP -> CAPABILITIES -> OUTCOME -> INCIDENT MEMORY

Key features:
- Automatic incident creation on context entry
- Automatic incident finalization on context exit
- Action recording with full provenance
- Support for nested contexts (capability execution within user tasks)
- Configurable recording modes (required, best_effort, disabled)

Usage:
    async with IncidentContext.for_user_task(
        user_input="restart nginx",
        classified_intent="system_task",
    ) as ctx:
        # Incident created automatically

        await ctx.record_action(
            kind="capability",
            command="service.restart",
            payload={"service": "nginx"},
            success=True,
        )

        ctx.set_outcome(success=True, summary="Nginx restarted")

    # Incident finalized automatically
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from elle.daemon.incidents.models import IncidentReport

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class RecordingMode(Enum):
    """How to handle incident recording."""

    REQUIRED = "required"  # Fail if recording fails
    BEST_EFFORT = "best_effort"  # Log warning and continue on failure
    DISABLED = "disabled"  # Skip recording entirely


class ActionKind(Enum):
    """Kind of action being recorded."""

    CAPABILITY = "capability"
    SHELL = "shell"
    VERIFY = "verify"
    ARM = "arm"
    OTHER = "other"


# =============================================================================
# Models
# =============================================================================


class ActionRecord(BaseModel):
    """Record of an action taken during incident handling."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(description="Action kind: capability, shell, verify, arm")
    command: str = Field(description="Command or capability name")
    payload: dict[str, Any] = Field(default_factory=dict)
    success: bool = Field(default=True)
    output: str = Field(default="")
    error: str | None = Field(default=None)
    duration_ms: int = Field(default=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IncidentOutcome(BaseModel):
    """Outcome of incident handling."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(default=False)
    summary: str = Field(default="")
    status: str = Field(default="open")  # open, mitigated, resolved
    outcome: str = Field(default="no_change")  # improved, partial, no_change, worse


# =============================================================================
# IncidentContext
# =============================================================================


class IncidentContext:
    """Context manager for incident recording.

    Ensures incidents are properly created at the start and finalized
    at the end, with all actions recorded along the way.

    Use the class methods to create context instances:
    - for_user_task(): User-initiated tasks
    - for_reactive_function(): Reactive automation
    - for_capability(): Capability execution (nested)
    """

    def __init__(
        self,
        incident_id: str | None = None,
        title: str = "",
        domain: str = "other",
        severity: str = "info",
        trigger_source: str = "manual",
        parent_incident_id: str | None = None,
        recording_mode: RecordingMode = RecordingMode.BEST_EFFORT,
    ) -> None:
        """Initialize the incident context.

        Args:
            incident_id: Existing incident ID to update (None to create new).
            title: Incident title.
            domain: Incident domain (service, docker, net, etc.).
            severity: Severity level (info, warning, error, critical).
            trigger_source: What triggered this (user_task, reactive, daemon, etc.).
            parent_incident_id: Parent incident if this is nested.
            recording_mode: How to handle recording failures.
        """
        self._incident_id = incident_id
        self._title = title
        self._domain = domain
        self._severity = severity
        self._trigger_source = trigger_source
        self._parent_incident_id = parent_incident_id
        self._recording_mode = recording_mode

        self._actions: list[ActionRecord] = []
        self._outcome = IncidentOutcome()
        self._started_at: datetime | None = None
        self._finalized = False
        self._incident: IncidentReport | None = None
        self._function_id: str | None = None
        self._trigger_event: dict[str, Any] | None = None

    @property
    def incident_id(self) -> str:
        """Get the incident ID."""
        return self._incident_id or ""

    @property
    def is_recording(self) -> bool:
        """Check if recording is enabled."""
        return self._recording_mode != RecordingMode.DISABLED

    @property
    def actions(self) -> list[ActionRecord]:
        """Get all recorded actions."""
        return list(self._actions)

    # -------------------------------------------------------------------------
    # Factory Methods
    # -------------------------------------------------------------------------

    @classmethod
    @asynccontextmanager
    async def for_user_task(
        cls,
        user_input: str,
        classified_intent: str,
        recording_mode: RecordingMode = RecordingMode.BEST_EFFORT,
    ) -> AsyncIterator[IncidentContext]:
        """Create context for a user-initiated task.

        Args:
            user_input: The user's input.
            classified_intent: How the input was classified.
            recording_mode: How to handle recording failures.

        Yields:
            IncidentContext for the task.
        """
        # Infer domain and title
        domain = _infer_domain_from_input(user_input, classified_intent)
        title = _generate_title(user_input, classified_intent)
        trigger_source = "user_task" if classified_intent == "system_task" else "user_query"

        ctx = cls(
            title=title,
            domain=domain,
            severity="info",
            trigger_source=trigger_source,
            recording_mode=recording_mode,
        )

        try:
            await ctx._enter()
            yield ctx
        finally:
            await ctx._exit()

    @classmethod
    @asynccontextmanager
    async def for_reactive_function(
        cls,
        function_id: str,
        function_name: str,
        trigger_event: dict[str, Any] | None = None,
        recording_mode: RecordingMode = RecordingMode.BEST_EFFORT,
    ) -> AsyncIterator[IncidentContext]:
        """Create context for a reactive function execution.

        Args:
            function_id: ID of the reactive function.
            function_name: Name of the function.
            trigger_event: Optional trigger event data.
            recording_mode: How to handle recording failures.

        Yields:
            IncidentContext for the execution.
        """
        title = f"[Reactive] {function_name}"
        domain = _infer_domain_from_function(function_name)

        ctx = cls(
            title=title,
            domain=domain,
            severity="info",
            trigger_source="reactive",
            recording_mode=recording_mode,
        )
        ctx._function_id = function_id
        ctx._trigger_event = trigger_event

        try:
            await ctx._enter()
            yield ctx
        finally:
            await ctx._exit()

    @classmethod
    @asynccontextmanager
    async def for_capability(
        cls,
        capability_name: str,
        parent_incident_id: str | None = None,
        recording_mode: RecordingMode = RecordingMode.BEST_EFFORT,
    ) -> AsyncIterator[IncidentContext]:
        """Create context for a capability execution.

        This can be nested within a user task or reactive function context.

        Args:
            capability_name: Name of the capability being executed.
            parent_incident_id: Parent incident ID if nested.
            recording_mode: How to handle recording failures.

        Yields:
            IncidentContext for the capability.
        """
        # Parse domain from capability name
        domain = capability_name.split(".")[0] if "." in capability_name else "other"
        title = f"[Capability] {capability_name}"

        ctx = cls(
            incident_id=parent_incident_id,  # Update existing if parent provided
            title=title,
            domain=domain,
            severity="info",
            trigger_source="capability",
            parent_incident_id=parent_incident_id,
            recording_mode=recording_mode,
        )

        try:
            await ctx._enter()
            yield ctx
        finally:
            await ctx._exit()

    # -------------------------------------------------------------------------
    # Recording Methods
    # -------------------------------------------------------------------------

    async def record_action(
        self,
        kind: str,
        command: str,
        payload: dict[str, Any] | None = None,
        success: bool = True,
        output: str = "",
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Record an action taken during incident handling.

        Args:
            kind: Action kind (capability, shell, verify, arm).
            command: Command or capability name.
            payload: Additional action details.
            success: Whether the action succeeded.
            output: Output from the action.
            error: Error message if failed.
            duration_ms: Action duration in milliseconds.
        """
        if not self.is_recording:
            return

        action = ActionRecord(
            kind=kind,
            command=command,
            payload=payload or {},
            success=success,
            output=output,
            error=error,
            duration_ms=duration_ms,
        )
        self._actions.append(action)

        # Also append to incident vault
        if self._incident_id:
            try:
                from elle.daemon.incidents.store import append_action

                append_action(
                    incident_id=self._incident_id,
                    kind=kind,
                    command=command,
                    payload=payload or {},
                    stdout=output[:5000] if output else None,
                    stderr=error,
                    success=success,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                self._handle_recording_error(f"Failed to append action: {e}")

    def set_outcome(
        self,
        success: bool,
        summary: str = "",
        status: str | None = None,
        outcome: str | None = None,
    ) -> None:
        """Set the outcome of incident handling.

        Args:
            success: Whether handling succeeded.
            summary: Summary of what happened.
            status: Incident status (open, mitigated, resolved).
            outcome: Outcome type (improved, partial, no_change, worse).
        """
        if status is None:
            status = "resolved" if success else "open"
        if outcome is None:
            outcome = "improved" if success else "no_change"

        self._outcome = IncidentOutcome(
            success=success,
            summary=summary,
            status=status,
            outcome=outcome,
        )

    # -------------------------------------------------------------------------
    # Lifecycle Methods
    # -------------------------------------------------------------------------

    async def _enter(self) -> None:
        """Enter the context - create incident if needed."""
        self._started_at = datetime.now(timezone.utc)

        if not self.is_recording:
            # Generate a tracking ID even if not recording
            self._incident_id = str(uuid4())
            return

        try:
            # If we have an existing incident ID, verify it exists
            if self._incident_id:
                from elle.daemon.incidents.store import get_incident

                incident = get_incident(self._incident_id)
                if incident:
                    self._incident = incident
                    return

            # Create new incident
            from elle.daemon.incidents.store import create_incident_draft

            incident = create_incident_draft(
                title=self._title,
                domain=self._domain,
                severity=self._severity,
                trigger_source=self._trigger_source,
            )
            self._incident_id = incident.incident_id
            self._incident = incident
            logger.debug(f"Created incident {self._incident_id}")

        except Exception as e:
            self._handle_recording_error(f"Failed to create incident: {e}")
            # Generate tracking ID even on failure
            self._incident_id = self._incident_id or str(uuid4())

    async def _exit(self) -> None:
        """Exit the context - finalize incident."""
        if self._finalized:
            return
        self._finalized = True

        if not self.is_recording or not self._incident_id:
            return

        try:
            from elle.daemon.incidents.store import update_incident

            # Build summary if not set
            summary = self._outcome.summary
            if not summary:
                summary = self._build_auto_summary()

            # Calculate confidence
            confidence = self._calculate_confidence()

            update_incident(
                self._incident_id,
                status=self._outcome.status,
                summary=summary,
                outcome=self._outcome.outcome,
                confidence=confidence,
                decision={
                    "action_count": len(self._actions),
                    "success": self._outcome.success,
                    "trigger_source": self._trigger_source,
                },
            )
            logger.debug(
                f"Finalized incident {self._incident_id}: status={self._outcome.status}, actions={len(self._actions)}"
            )

        except Exception as e:
            self._handle_recording_error(f"Failed to finalize incident: {e}")

    def _handle_recording_error(self, message: str) -> None:
        """Handle recording errors based on mode."""
        if self._recording_mode == RecordingMode.REQUIRED:
            raise IncidentRecordingError(message)
        else:
            logger.warning(message)

    def _build_auto_summary(self) -> str:
        """Build automatic summary from recorded actions."""
        parts = []

        if self._title:
            parts.append(self._title)

        if self._actions:
            successful = sum(1 for a in self._actions if a.success)
            failed = len(self._actions) - successful
            parts.append(f"Executed {len(self._actions)} actions ({successful} successful, {failed} failed).")

        if self._outcome.success:
            parts.append("Completed successfully.")
        else:
            parts.append("Encountered errors.")

        return " ".join(parts)

    def _calculate_confidence(self) -> float:
        """Calculate confidence score for the incident."""
        if not self._actions:
            return 0.5

        # Base confidence
        confidence = 0.5

        # Boost for successful actions
        successful = sum(1 for a in self._actions if a.success)
        if self._actions:
            success_rate = successful / len(self._actions)
            confidence += success_rate * 0.3

        # Boost for successful outcome
        if self._outcome.success:
            confidence += 0.2

        return min(1.0, confidence)


# =============================================================================
# Exceptions
# =============================================================================


class IncidentRecordingError(Exception):
    """Raised when incident recording fails in REQUIRED mode."""

    pass


# =============================================================================
# Helper Functions
# =============================================================================


def _infer_domain_from_input(user_input: str, classified_intent: str) -> str:
    """Infer incident domain from user input."""
    lower_input = user_input.lower()

    domain_keywords = {
        "net": ["network", "connection", "dns", "firewall", "port", "ip", "ping", "curl"],
        "docker": ["docker", "container", "compose", "image"],
        "service": ["service", "systemd", "restart", "stop", "start", "nginx", "apache"],
        "disk": ["disk", "storage", "mount", "space", "full"],
        "oom": ["memory", "oom", "ram", "swap"],
        "pkg": ["package", "install", "apt", "dpkg", "upgrade"],
        "auth": ["permission", "sudo", "password", "login", "ssh"],
        "fs": ["file", "config", "edit", "delete", "copy"],
    }

    for domain, keywords in domain_keywords.items():
        if any(kw in lower_input for kw in keywords):
            return domain

    return "other"


def _infer_domain_from_function(function_name: str) -> str:
    """Infer domain from reactive function name."""
    name_lower = function_name.lower()

    if "docker" in name_lower or "container" in name_lower:
        return "docker"
    if "service" in name_lower or "systemd" in name_lower:
        return "service"
    if "disk" in name_lower or "storage" in name_lower:
        return "disk"
    if "network" in name_lower or "net" in name_lower:
        return "net"
    if "memory" in name_lower or "oom" in name_lower:
        return "oom"

    return "other"


def _generate_title(user_input: str, classified_intent: str) -> str:
    """Generate incident title from user input."""
    title = user_input.strip()
    if len(title) > 100:
        title = title[:97] + "..."

    if classified_intent == "system_question":
        if not title.endswith("?"):
            title = f"Question: {title}"
    elif classified_intent == "system_task":
        title = f"Task: {title}"

    return title
