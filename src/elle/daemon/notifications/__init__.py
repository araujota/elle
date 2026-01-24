"""ELLE Desktop Notification System.

Provides user-friendly desktop notifications for ELLE events:
- Job/task completion
- Incident detection and resolution
- Reboot status updates
- System health warnings

Notifications support action buttons that can open the ELLE REPL
in a terminal for immediate investigation.

Usage:
    from elle.daemon.notifications import (
        notify,
        notify_job_complete,
        notify_incident,
        notify_reboot_status,
        notify_health,
        notify_error,
    )

    # Simple notification
    notify("Task Complete", "Your backup finished successfully")

    # Job completion
    notify_job_complete("System Update", success=True)

    # Incident (with click-to-investigate)
    notify_incident(
        title="Disk usage critical on /var",
        severity="critical",
        domain="disk",
        incident_id="abc-123",
        summary="95% disk usage detected",
    )

    # Reboot status
    notify_reboot_status(
        status="success",
        goal="Install kernel security updates",
        intent_id="xyz-789",
    )

    # Health warning
    notify_health(
        component="Memory",
        message="High memory pressure detected (85% used)",
        severity="warning",
    )

    # Error notification (doesn't auto-dismiss)
    notify_error(
        title="Service Failed",
        message="nginx failed to start after configuration change",
    )

Backend Support:
    - Primary: libnotify via PyGObject (full action support)
    - Fallback: notify-send command (limited action support)

Action Handling:
    When users click on notification actions:
    - "Open ELLE" / "Investigate" / "View Details" - Opens terminal with ELLE
    - "Cancel" (for reboot) - Cancels the pending reboot
    - "Dismiss" - Closes notification without action

Terminal Support:
    Automatically detects and uses available terminal emulator:
    gnome-terminal, konsole, xfce4-terminal, mate-terminal,
    tilix, kitty, alacritty, x-terminal-emulator, xterm
"""

# Models
from elle.daemon.notifications.models import (
    CATEGORY_ICONS,
    CATEGORY_URGENCY,
    Notification,
    NotificationAction,
    NotificationCategory,
    NotificationResult,
    NotificationUrgency,
    health_notification,
    incident_notification,
    job_complete_notification,
    reboot_notification,
)

# Service
from elle.daemon.notifications.service import (
    NotificationService,
    get_service,
    notify,
    notify_async,
    notify_error,
    notify_health,
    notify_incident,
    notify_info,
    notify_job_complete,
    notify_reboot_status,
    send,
    send_async,
)

__all__ = [
    # Constants
    "CATEGORY_ICONS",
    "CATEGORY_URGENCY",
    # Models
    "Notification",
    "NotificationAction",
    "NotificationCategory",
    "NotificationResult",
    "NotificationUrgency",
    # Template functions
    "health_notification",
    "incident_notification",
    "job_complete_notification",
    "reboot_notification",
    # Service
    "NotificationService",
    "get_service",
    # Public API
    "notify",
    "notify_async",
    "notify_error",
    "notify_health",
    "notify_incident",
    "notify_info",
    "notify_job_complete",
    "notify_reboot_status",
    "send",
    "send_async",
]
