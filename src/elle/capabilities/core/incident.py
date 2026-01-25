"""Incident management capabilities.

Provides typed, policy-governed operations for incident handling:
- incident.create - Open a new incident with context
- incident.attach - Attach evidence to an existing incident
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    SideEffect,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Models
# =============================================================================


class IncidentCreateInput(BaseModel):
    """Input for incident.create operation."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(
        description="Brief title for the incident",
        max_length=200,
    )
    domain: Literal["net", "disk", "oom", "docker", "auth", "pkg", "service", "fs", "other"] = Field(
        default="other",
        description="Incident domain category",
    )
    severity: Literal["info", "warning", "error", "critical"] = Field(
        default="warning",
        description="Incident severity level",
    )
    summary: str = Field(
        default="",
        description="Detailed summary of the incident",
        max_length=2000,
    )
    symptoms: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Observable symptoms",
    )
    suspected_causes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Suspected root causes",
    )
    trigger_source: Literal["reactive", "manual", "telemetry", "probe"] = Field(
        default="reactive",
        description="What triggered this incident",
    )
    trigger_command: str | None = Field(
        default=None,
        description="Command that triggered the incident (if applicable)",
    )
    event_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Related telemetry event IDs",
    )
    tags: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Tags for categorization",
    )
    attach_snapshot: bool = Field(
        default=False,
        description="Whether to capture a system snapshot",
    )
    notify: bool = Field(
        default=True,
        description="Whether to send notification",
    )


class IncidentCreateOutput(BaseModel):
    """Output from incident.create operation."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="Created incident ID")
    title: str = Field(description="Incident title")
    status: str = Field(description="Initial status")
    snapshot_attached: bool = Field(
        default=False,
        description="Whether a snapshot was attached",
    )
    notification_sent: bool = Field(
        default=False,
        description="Whether notification was sent",
    )


class IncidentAttachInput(BaseModel):
    """Input for incident.attach operation."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(
        description="Target incident ID",
    )
    attach_type: Literal["action", "log", "metric", "snapshot", "event"] = Field(
        description="Type of attachment",
    )

    # For action attachments
    action_kind: Literal["shell", "edit", "privileged", "verify", "rollback", "capability"] | None = Field(
        default=None,
        description="Kind of action (required for attach_type='action')",
    )
    command: str | None = Field(
        default=None,
        description="Command executed (for action type)",
    )
    exit_code: int | None = Field(
        default=None,
        description="Exit code (for action type)",
    )
    stdout: str | None = Field(
        default=None,
        description="Stdout output (truncated)",
        max_length=10000,
    )
    stderr: str | None = Field(
        default=None,
        description="Stderr output (truncated)",
        max_length=10000,
    )
    success: bool = Field(
        default=False,
        description="Whether the action succeeded",
    )
    duration_ms: int | None = Field(
        default=None,
        description="Action duration in milliseconds",
    )

    # For log attachments
    log_snippet: str | None = Field(
        default=None,
        description="Log snippet to attach",
        max_length=5000,
    )

    # For metric attachments
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Metric key-value pairs to attach",
    )

    # For event attachments
    event_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Event IDs to link",
    )

    # For snapshot attachments
    snapshot_which: Literal["pre", "post"] = Field(
        default="post",
        description="Snapshot type (pre or post)",
    )


class IncidentAttachOutput(BaseModel):
    """Output from incident.attach operation."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="Target incident ID")
    attach_type: str = Field(description="Type of attachment")
    attached: bool = Field(description="Whether attachment succeeded")
    details: str = Field(default="", description="Attachment details")


# =============================================================================
# Incident Create Capability
# =============================================================================


class IncidentCreateCapability(BaseCapability):
    """Create a new incident."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="incident.create",
            summary="Open a new incident with context and optional snapshot",
            domain="incident",
            risk="none",
            side_effects=(
                SideEffect(
                    kind="incident_created",
                    target="incident_vault",
                    reversible=False,
                    description="Incident created in vault",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="IncidentCreateInput",
            output_schema_name="IncidentCreateOutput",
        )

    def dry_run(self, input: IncidentCreateInput) -> DryRunResult:
        """Preview incident creation."""
        return DryRunResult(
            would_execute=(f"create incident: {input.title}",),
            would_modify=("incident_vault",),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would create {input.severity} incident: '{input.title}' (domain: {input.domain})",
            is_valid=True,
        )

    def run(self, input: IncidentCreateInput) -> CapabilityResult:
        """Create the incident."""
        start_time = time.time()

        try:
            from elle.daemon.incidents.store import (
                attach_snapshot,
                create_incident_draft,
                link_events,
                update_incident,
            )

            # Create the incident draft
            incident = create_incident_draft(
                title=input.title,
                domain=input.domain,
                severity=input.severity,
                trigger_source=input.trigger_source,
                trigger_command=input.trigger_command,
            )

            # Update with additional details
            update_incident(
                incident.incident_id,
                summary=input.summary,
                symptoms=list(input.symptoms),
                suspected_causes=list(input.suspected_causes),
                tags=list(input.tags),
            )

            # Link events if any
            if input.event_ids:
                link_events(incident.incident_id, list(input.event_ids))

            # Capture snapshot if requested
            snapshot_attached = False
            if input.attach_snapshot:
                try:
                    from elle.daemon.incidents.snapshot import collect_system_snapshot

                    snapshot = collect_system_snapshot()
                    attach_snapshot(incident.incident_id, "pre", snapshot)
                    snapshot_attached = True
                except Exception as e:
                    logger.warning(f"Failed to attach snapshot: {e}")

            # Send notification if requested
            notification_sent = False
            if input.notify:
                try:
                    from elle.daemon.notifications import notify_incident

                    notify_incident(
                        title=input.title,
                        severity=input.severity,
                        domain=input.domain,
                        incident_id=incident.incident_id,
                        summary=input.summary[:200] if input.summary else None,
                    )
                    notification_sent = True
                except ImportError:
                    logger.debug("Notification service not available")
                except Exception as e:
                    logger.warning(f"Failed to send notification: {e}")

            return CapabilityResult(
                success=True,
                output=IncidentCreateOutput(
                    incident_id=incident.incident_id,
                    title=input.title,
                    status="open",
                    snapshot_attached=snapshot_attached,
                    notification_sent=notification_sent,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="incident_created",
                        target="incident_vault",
                        reversible=False,
                        description=f"Created incident: {incident.incident_id}",
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Created {input.severity} incident: {input.title}",
                ),
            )

        except ImportError:
            return CapabilityResult(
                success=False,
                error="Incident vault module not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            logger.exception(f"Failed to create incident: {e}")
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Incident Attach Capability
# =============================================================================


class IncidentAttachCapability(BaseCapability):
    """Attach evidence to an existing incident."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="incident.attach",
            summary="Attach evidence (log, action, metric, snapshot) to an incident",
            domain="incident",
            risk="none",
            side_effects=(
                SideEffect(
                    kind="incident_created",
                    target="incident_vault",
                    reversible=False,
                    description="Evidence attached to incident",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="IncidentAttachInput",
            output_schema_name="IncidentAttachOutput",
        )

    def dry_run(self, input: IncidentAttachInput) -> DryRunResult:
        """Preview attachment."""
        return DryRunResult(
            would_execute=(f"attach {input.attach_type} to {input.incident_id}",),
            would_modify=("incident_vault",),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would attach {input.attach_type} to incident {input.incident_id[:8]}...",
            is_valid=True,
        )

    def run(self, input: IncidentAttachInput) -> CapabilityResult:
        """Attach evidence to the incident."""
        start_time = time.time()

        try:
            from elle.daemon.incidents.store import (
                append_action,
                attach_snapshot,
                get_incident,
                link_events,
                update_incident,
            )

            # Verify incident exists
            incident = get_incident(input.incident_id)
            if not incident:
                return CapabilityResult(
                    success=False,
                    error=f"Incident not found: {input.incident_id}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            details = ""

            if input.attach_type == "action":
                # Attach action record
                if not input.action_kind:
                    return CapabilityResult(
                        success=False,
                        error="action_kind is required for action attachments",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )

                action = append_action(
                    incident_id=input.incident_id,
                    kind=input.action_kind,
                    command=input.command,
                    exit_code=input.exit_code,
                    stdout=input.stdout,
                    stderr=input.stderr,
                    success=input.success,
                    duration_ms=input.duration_ms,
                )
                details = f"Added action #{action.step_index}: {input.action_kind}"

            elif input.attach_type == "log":
                # Attach log snippet
                if not input.log_snippet:
                    return CapabilityResult(
                        success=False,
                        error="log_snippet is required for log attachments",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )

                # Get current log snippets and add new one
                current_logs = list(incident.log_snippets)
                current_logs.append(input.log_snippet)
                update_incident(
                    input.incident_id,
                    log_snippets=current_logs,
                )
                details = f"Added log snippet ({len(input.log_snippet)} chars)"

            elif input.attach_type == "metric":
                # Attach metrics
                if not input.metrics:
                    return CapabilityResult(
                        success=False,
                        error="metrics dict is required for metric attachments",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )

                # Merge with current metrics
                current_metrics = dict(incident.metrics)
                current_metrics.update(input.metrics)
                update_incident(
                    input.incident_id,
                    metrics=current_metrics,
                )
                details = f"Added {len(input.metrics)} metrics"

            elif input.attach_type == "event":
                # Link events
                if not input.event_ids:
                    return CapabilityResult(
                        success=False,
                        error="event_ids is required for event attachments",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )

                linked = link_events(input.incident_id, list(input.event_ids))
                details = f"Linked {linked} events"

            elif input.attach_type == "snapshot":
                # Attach system snapshot
                try:
                    from elle.daemon.incidents.snapshot import collect_system_snapshot

                    snapshot = collect_system_snapshot()
                    attach_snapshot(input.incident_id, input.snapshot_which, snapshot)
                    details = f"Attached {input.snapshot_which} snapshot"
                except Exception as e:
                    return CapabilityResult(
                        success=False,
                        error=f"Failed to collect snapshot: {e}",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )

            else:
                return CapabilityResult(
                    success=False,
                    error=f"Unknown attach_type: {input.attach_type}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            return CapabilityResult(
                success=True,
                output=IncidentAttachOutput(
                    incident_id=input.incident_id,
                    attach_type=input.attach_type,
                    attached=True,
                    details=details,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="incident_created",
                        target="incident_vault",
                        reversible=False,
                        description=details,
                    ),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Attached {input.attach_type} to incident {input.incident_id}",
                ),
            )

        except ImportError:
            return CapabilityResult(
                success=False,
                error="Incident vault module not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            logger.exception(f"Failed to attach to incident: {e}")
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Exports
# =============================================================================

INCIDENT_CAPABILITIES = [
    IncidentCreateCapability,
    IncidentAttachCapability,
]
