"""ELLE Desktop and Mobile Notification System.

Provides user-friendly notifications for ELLE events:
- Job/task completion
- Incident detection and resolution
- Reboot status updates
- System health warnings
- Plan/diff review requests
- Confirmation prompts

Notifications are delivered to:
- Desktop: via libnotify/notify-send with action buttons
- Mobile: via Server-Sent Events when mobile gateway is active

Usage:
    from elle.daemon.notifications import (
        notify,
        notify_job_complete,
        notify_incident,
        notify_reboot_status,
        notify_health,
        notify_error,
        get_mobile_notifier,
    )

    # Simple notification (desktop + mobile)
    notify("Task Complete", "Your backup finished successfully")

    # Job completion
    notify_job_complete("System Update", success=True)

    # Incident with rich context
    notify_incident(
        title="Disk usage critical on /var",
        severity="critical",
        domain="disk",
        incident_id="abc-123",
        summary="95% disk usage detected",
        symptoms=["Cannot write to /var/log"],
        metrics={"disk_pct": 0.95, "mount": "/var"},
    )

    # Push-specific notifications to mobile
    notifier = get_mobile_notifier()
    if notifier.is_available():
        await notifier.push_plan_ready(
            plan_id="plan-123",
            title="Cleanup old logs",
            summary="Remove logs older than 30 days",
            step_count=3,
        )

        await notifier.push_diff_ready(
            diff_id="diff-456",
            file_path="/etc/nginx/nginx.conf",
            change_summary="Add rate limiting configuration",
        )

        await notifier.push_confirmation_required(
            confirmation_id="conf-789",
            title="Delete old backups",
            description="This will remove 50 backup files (2.3 GB)",
            risk_level="medium",
        )

Backend Support:
    Desktop:
        - Primary: libnotify via PyGObject (full action support)
        - Fallback: notify-send command (limited action support)

    Mobile:
        - SSE endpoint at /events on mobile gateway
        - Automatic push when mobile gateway is running
        - Heartbeat to keep connections alive

Action Handling:
    Desktop:
        - "Open ELLE" / "Investigate" / "View Details" - Opens terminal with ELLE
        - "Cancel" (for reboot) - Cancels the pending reboot
        - "Dismiss" - Closes notification without action

    Mobile:
        - Actions included in event payload for mobile app to handle
        - Context IDs allow follow-up API calls

Terminal Support:
    Automatically detects and uses available terminal emulator:
    gnome-terminal, konsole, xfce4-terminal, mate-terminal,
    tilix, kitty, alacritty, x-terminal-emulator, xterm
"""

# Formatters (for building human-readable messages)
from elle.daemon.notifications.formatters import (
    format_disk_issue,
    format_docker_issue,
    format_entities,
    format_entity,
    format_event_message,
    format_health_body,
    format_health_title,
    format_incident_body,
    format_incident_title,
    format_memory_issue,
    format_network_issue,
    format_oom_event,
    format_percentage,
    format_service_issue,
    format_temperature_issue,
)

# Mobile push (for real-time notifications to mobile devices)
from elle.daemon.notifications.mobile_push import (
    MobileClientConnection,
    MobileEventType,
    MobileNotificationEvent,
    MobilePushNotifier,
    get_mobile_notifier,
)

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
    # Formatters
    "format_disk_issue",
    "format_docker_issue",
    "format_entities",
    "format_entity",
    "format_event_message",
    "format_health_body",
    "format_health_title",
    "format_incident_body",
    "format_incident_title",
    "format_memory_issue",
    "format_network_issue",
    "format_oom_event",
    "format_percentage",
    "format_service_issue",
    "format_temperature_issue",
    # Mobile push
    "MobileClientConnection",
    "MobileEventType",
    "MobileNotificationEvent",
    "MobilePushNotifier",
    "get_mobile_notifier",
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
