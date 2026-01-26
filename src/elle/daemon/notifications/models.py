"""Pydantic models for the notification system.

Defines notification types, urgency levels, and message templates
for user-friendly desktop notifications.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotificationUrgency(str, Enum):
    """Notification urgency level (maps to libnotify urgency)."""

    LOW = "low"  # Informational, non-critical
    NORMAL = "normal"  # Standard notifications
    CRITICAL = "critical"  # Requires immediate attention


class NotificationCategory(str, Enum):
    """Categories of notifications ELLE can send."""

    # Job/Task notifications
    JOB_COMPLETE = "job_complete"
    JOB_FAILED = "job_failed"
    TASK_PROGRESS = "task_progress"

    # Incident notifications
    INCIDENT_DETECTED = "incident_detected"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_ESCALATED = "incident_escalated"

    # Reboot notifications
    REBOOT_REQUIRED = "reboot_required"
    REBOOT_PENDING = "reboot_pending"
    REBOOT_SUCCESS = "reboot_success"
    REBOOT_FAILED = "reboot_failed"
    REBOOT_ROLLBACK = "reboot_rollback"

    # System health
    HEALTH_WARNING = "health_warning"
    HEALTH_CRITICAL = "health_critical"
    HEALTH_RECOVERED = "health_recovered"

    # General
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# Map categories to default urgency levels
CATEGORY_URGENCY: dict[NotificationCategory, NotificationUrgency] = {
    NotificationCategory.JOB_COMPLETE: NotificationUrgency.NORMAL,
    NotificationCategory.JOB_FAILED: NotificationUrgency.NORMAL,
    NotificationCategory.TASK_PROGRESS: NotificationUrgency.LOW,
    NotificationCategory.INCIDENT_DETECTED: NotificationUrgency.NORMAL,
    NotificationCategory.INCIDENT_RESOLVED: NotificationUrgency.LOW,
    NotificationCategory.INCIDENT_ESCALATED: NotificationUrgency.CRITICAL,
    NotificationCategory.REBOOT_REQUIRED: NotificationUrgency.NORMAL,
    NotificationCategory.REBOOT_PENDING: NotificationUrgency.NORMAL,
    NotificationCategory.REBOOT_SUCCESS: NotificationUrgency.NORMAL,
    NotificationCategory.REBOOT_FAILED: NotificationUrgency.CRITICAL,
    NotificationCategory.REBOOT_ROLLBACK: NotificationUrgency.CRITICAL,
    NotificationCategory.HEALTH_WARNING: NotificationUrgency.NORMAL,
    NotificationCategory.HEALTH_CRITICAL: NotificationUrgency.CRITICAL,
    NotificationCategory.HEALTH_RECOVERED: NotificationUrgency.LOW,
    NotificationCategory.INFO: NotificationUrgency.LOW,
    NotificationCategory.WARNING: NotificationUrgency.NORMAL,
    NotificationCategory.ERROR: NotificationUrgency.CRITICAL,
}

# Map categories to icons
CATEGORY_ICONS: dict[NotificationCategory, str] = {
    NotificationCategory.JOB_COMPLETE: "emblem-ok-symbolic",
    NotificationCategory.JOB_FAILED: "dialog-error-symbolic",
    NotificationCategory.TASK_PROGRESS: "emblem-synchronizing-symbolic",
    NotificationCategory.INCIDENT_DETECTED: "dialog-warning-symbolic",
    NotificationCategory.INCIDENT_RESOLVED: "emblem-ok-symbolic",
    NotificationCategory.INCIDENT_ESCALATED: "dialog-error-symbolic",
    NotificationCategory.REBOOT_REQUIRED: "system-reboot-symbolic",
    NotificationCategory.REBOOT_PENDING: "system-reboot-symbolic",
    NotificationCategory.REBOOT_SUCCESS: "emblem-ok-symbolic",
    NotificationCategory.REBOOT_FAILED: "dialog-error-symbolic",
    NotificationCategory.REBOOT_ROLLBACK: "edit-undo-symbolic",
    NotificationCategory.HEALTH_WARNING: "dialog-warning-symbolic",
    NotificationCategory.HEALTH_CRITICAL: "dialog-error-symbolic",
    NotificationCategory.HEALTH_RECOVERED: "emblem-ok-symbolic",
    NotificationCategory.INFO: "dialog-information-symbolic",
    NotificationCategory.WARNING: "dialog-warning-symbolic",
    NotificationCategory.ERROR: "dialog-error-symbolic",
}


class NotificationAction(BaseModel):
    """An action button on a notification."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Action identifier")
    label: str = Field(description="Button label shown to user")
    command: str | None = Field(
        default=None,
        description="Command to execute when clicked",
    )


class Notification(BaseModel):
    """A notification to display to the user."""

    model_config = ConfigDict(frozen=True)

    # Core fields
    title: str = Field(description="Notification title/summary")
    body: str = Field(description="Notification body text")
    category: NotificationCategory = Field(
        default=NotificationCategory.INFO,
        description="Notification category",
    )

    # Display options
    urgency: NotificationUrgency | None = Field(
        default=None,
        description="Override default urgency for category",
    )
    icon: str | None = Field(
        default=None,
        description="Override default icon for category",
    )
    timeout_ms: int = Field(
        default=5000,
        ge=0,
        description="Notification timeout (0 = no timeout)",
    )

    # Actions
    actions: tuple[NotificationAction, ...] = Field(
        default_factory=tuple,
        description="Action buttons on notification",
    )
    default_action: str | None = Field(
        default=None,
        description="Action ID to trigger when notification body is clicked",
    )

    # Context
    context_id: str | None = Field(
        default=None,
        description="ID for context (incident_id, intent_id, etc.)",
    )
    context_type: str | None = Field(
        default=None,
        description="Type of context (incident, reboot, job, etc.)",
    )

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = Field(
        default="elle",
        description="Source component that created notification",
    )

    def get_urgency(self) -> NotificationUrgency:
        """Get the effective urgency level."""
        if self.urgency:
            return self.urgency
        return CATEGORY_URGENCY.get(self.category, NotificationUrgency.NORMAL)

    def get_icon(self) -> str:
        """Get the effective icon name."""
        if self.icon:
            return self.icon
        return CATEGORY_ICONS.get(self.category, "dialog-information-symbolic")


class NotificationResult(BaseModel):
    """Result of sending a notification."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether notification was sent")
    notification_id: int | None = Field(
        default=None,
        description="ID from notification daemon (for updates)",
    )
    error: str | None = Field(
        default=None,
        description="Error message if failed",
    )
    method: str = Field(
        default="unknown",
        description="Method used (libnotify, notify-send, dbus)",
    )


# =============================================================================
# Pre-built notification templates
# =============================================================================


def job_complete_notification(
    job_name: str,
    success: bool = True,
    details: str | None = None,
) -> Notification:
    """Create a job completion notification."""
    if success:
        return Notification(
            title="Job Complete",
            body=f"{job_name} completed successfully." + (f"\n{details}" if details else ""),
            category=NotificationCategory.JOB_COMPLETE,
            actions=(NotificationAction(id="open", label="Open ELLE"),),
            default_action="open",
        )
    else:
        return Notification(
            title="Job Failed",
            body=f"{job_name} failed." + (f"\n{details}" if details else ""),
            category=NotificationCategory.JOB_FAILED,
            actions=(NotificationAction(id="open", label="View Details"),),
            default_action="open",
        )


def incident_notification(
    title: str,
    severity: str,
    domain: str,
    incident_id: str,
    summary: str | None = None,
    *,
    symptoms: tuple[str, ...] | list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    entities: tuple[str, ...] | list[str] | None = None,
    suspected_causes: tuple[str, ...] | list[str] | None = None,
    entity: str | None = None,
) -> Notification:
    """Create an incident detection notification with rich context.

    Args:
        title: Incident title.
        severity: Incident severity (info, warning, error, critical).
        domain: Incident domain (disk, net, oom, service, etc.).
        incident_id: Incident UUID for context linking.
        summary: Optional incident summary.
        symptoms: Optional list of symptom descriptions.
        metrics: Optional metrics dictionary (disk_pct, mem_pressure, etc.).
        entities: Optional list of affected entities.
        suspected_causes: Optional list of suspected causes.
        entity: Primary affected entity (e.g., "service:nginx").

    Returns:
        Notification configured for the incident.
    """
    from elle.daemon.notifications.formatters import (
        format_incident_body,
        format_incident_title,
    )

    urgency = NotificationUrgency.CRITICAL if severity in ("critical", "error") else NotificationUrgency.NORMAL
    category = (
        NotificationCategory.INCIDENT_ESCALATED if severity == "critical" else NotificationCategory.INCIDENT_DETECTED
    )

    # Format title with context
    formatted_title = format_incident_title(domain, title, severity, entity)

    # Format body with available context
    formatted_body = format_incident_body(
        domain=domain,
        summary=summary,
        symptoms=list(symptoms) if symptoms else None,
        metrics=metrics,
        entities=list(entities) if entities else None,
        suspected_causes=list(suspected_causes) if suspected_causes else None,
    )

    # Fallback if formatters return empty
    if not formatted_body:
        formatted_body = f"[{domain.upper()}] {title}"
        if summary:
            formatted_body += f"\n{summary[:100]}"

    return Notification(
        title=formatted_title,
        body=formatted_body,
        category=category,
        urgency=urgency,
        context_id=incident_id,
        context_type="incident",
        timeout_ms=0 if severity == "critical" else 10000,
        actions=(
            NotificationAction(id="open", label="Investigate"),
            NotificationAction(id="dismiss", label="Dismiss"),
        ),
        default_action="open",
    )


def reboot_notification(
    status: str,
    goal: str,
    intent_id: str,
    details: str | None = None,
) -> Notification:
    """Create a reboot-related notification."""
    templates = {
        "required": (
            "Reboot Required",
            f"A reboot is needed to: {goal}",
            NotificationCategory.REBOOT_REQUIRED,
            ("confirm", "Reboot Now"),
        ),
        "pending": (
            "Reboot Pending",
            f"Preparing to reboot for: {goal}",
            NotificationCategory.REBOOT_PENDING,
            ("cancel", "Cancel"),
        ),
        "success": (
            "Reboot Successful",
            f"System rebooted and verified: {goal}",
            NotificationCategory.REBOOT_SUCCESS,
            ("open", "View Details"),
        ),
        "failed": (
            "Reboot Verification Failed",
            f"Issues detected after reboot: {goal}" + (f"\n{details}" if details else ""),
            NotificationCategory.REBOOT_FAILED,
            ("open", "Investigate"),
        ),
        "rollback": (
            "Automatic Rollback",
            f"System rolled back due to: {details or 'verification failure'}",
            NotificationCategory.REBOOT_ROLLBACK,
            ("open", "View Details"),
        ),
    }

    title, body, category, action = templates.get(
        status,
        ("Reboot Update", goal, NotificationCategory.INFO, ("open", "Open")),
    )

    return Notification(
        title=title,
        body=body,
        category=category,
        context_id=intent_id,
        context_type="reboot",
        timeout_ms=0 if status in ("failed", "rollback") else 10000,
        actions=(NotificationAction(id=action[0], label=action[1]),),
        default_action="open",
    )


def health_notification(
    status: str,
    component: str,
    message: str,
    severity: str = "warning",
    *,
    metrics: dict[str, Any] | None = None,
) -> Notification:
    """Create a system health notification with context.

    Args:
        status: Health status (warning, critical, recovered).
        component: System component (disk, memory, cpu, network, etc.).
        message: Health message.
        severity: Alert severity.
        metrics: Optional metrics for context (used_pct, available_mb, etc.).

    Returns:
        Notification configured for the health alert.
    """
    from elle.daemon.notifications.formatters import (
        format_health_body,
        format_health_title,
    )

    category_map = {
        "warning": NotificationCategory.HEALTH_WARNING,
        "critical": NotificationCategory.HEALTH_CRITICAL,
        "recovered": NotificationCategory.HEALTH_RECOVERED,
    }

    # Format title with severity context
    formatted_title = format_health_title(component, severity)

    # Format body with metrics context
    formatted_body = format_health_body(component, message, metrics)

    return Notification(
        title=formatted_title,
        body=formatted_body,
        category=category_map.get(severity, NotificationCategory.HEALTH_WARNING),
        timeout_ms=0 if severity == "critical" else 8000,
        actions=(NotificationAction(id="open", label="View Status"),),
        default_action="open",
    )
