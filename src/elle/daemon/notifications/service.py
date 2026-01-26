"""Notification service for ELLE desktop notifications.

Sends user-friendly notifications using libnotify (via PyGObject) with
fallback to notify-send. Supports action buttons that can open the
ELLE REPL in a terminal.

Usage:
    from elle.daemon.notifications import notify, notify_job_complete

    # Simple notification
    notify("Task Complete", "Your download finished successfully")

    # Pre-built templates
    notify_job_complete("Kernel update", success=True)
    notify_incident("disk full on /var", severity="warning", domain="disk")
    notify_reboot_status("success", goal="Install security updates")
"""

import asyncio
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from elle.daemon.notifications.models import (
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

logger = logging.getLogger(__name__)

# Application identity
APP_NAME = "ELLE"
APP_ID = "com.elle.assistant"

# Terminal emulators to try (in order of preference)
TERMINAL_EMULATORS = [
    ("gnome-terminal", ["gnome-terminal", "--", "elle"]),
    ("konsole", ["konsole", "-e", "elle"]),
    ("xfce4-terminal", ["xfce4-terminal", "-e", "elle"]),
    ("mate-terminal", ["mate-terminal", "-e", "elle"]),
    ("tilix", ["tilix", "-e", "elle"]),
    ("kitty", ["kitty", "elle"]),
    ("alacritty", ["alacritty", "-e", "elle"]),
    ("x-terminal-emulator", ["x-terminal-emulator", "-e", "elle"]),
    ("xterm", ["xterm", "-e", "elle"]),
]

# Notification history (for rate limiting)
_notification_history: dict[str, float] = {}
RATE_LIMIT_SECONDS = 5  # Minimum seconds between identical notifications


# =============================================================================
# Libnotify Backend (Primary)
# =============================================================================

_gi_available = False
_notify_initialized = False

try:
    import gi
    gi.require_version('Notify', '0.7')
    from gi.repository import GLib, Notify
    _gi_available = True
except (ImportError, ValueError) as e:
    logger.debug(f"PyGObject/libnotify not available: {e}")
    Notify = None
    GLib = None


def _init_libnotify() -> bool:
    """Initialize libnotify if available."""
    global _notify_initialized

    if not _gi_available:
        return False

    if _notify_initialized:
        return True

    try:
        if not Notify.is_initted():
            Notify.init(APP_NAME)
        _notify_initialized = True
        return True
    except Exception as e:
        logger.warning(f"Failed to initialize libnotify: {e}")
        return False


def _urgency_to_libnotify(urgency: NotificationUrgency) -> Any:
    """Convert our urgency to libnotify urgency."""
    if not _gi_available:
        return None

    mapping = {
        NotificationUrgency.LOW: Notify.Urgency.LOW,
        NotificationUrgency.NORMAL: Notify.Urgency.NORMAL,
        NotificationUrgency.CRITICAL: Notify.Urgency.CRITICAL,
    }
    return mapping.get(urgency, Notify.Urgency.NORMAL)


def _send_via_libnotify(notification: Notification) -> NotificationResult:
    """Send notification using libnotify."""
    if not _init_libnotify():
        return NotificationResult(
            success=False,
            error="libnotify not available",
            method="libnotify",
        )

    try:
        # Create notification
        n = Notify.Notification.new(
            notification.title,
            notification.body,
            notification.get_icon(),
        )

        # Set urgency
        n.set_urgency(_urgency_to_libnotify(notification.get_urgency()))

        # Set timeout
        if notification.timeout_ms > 0:
            n.set_timeout(notification.timeout_ms)
        else:
            n.set_timeout(Notify.EXPIRES_NEVER)

        # Set app name and category
        n.set_app_name(APP_NAME)
        n.set_category(notification.category.value)

        # Add actions
        for action in notification.actions:
            n.add_action(
                action.id,
                action.label,
                _create_action_callback(action, notification),
                None,
            )

        # Add default action (click on body)
        if notification.default_action:
            n.add_action(
                "default",
                "Default",
                _create_action_callback(
                    NotificationAction(id=notification.default_action, label="Default"),
                    notification,
                ),
                None,
            )

        # Show notification
        success = n.show()

        return NotificationResult(
            success=success,
            notification_id=n.props.id if hasattr(n.props, 'id') else None,
            method="libnotify",
        )

    except Exception as e:
        logger.error(f"Failed to send notification via libnotify: {e}")
        return NotificationResult(
            success=False,
            error=str(e),
            method="libnotify",
        )


def _create_action_callback(
    action: NotificationAction,
    notification: Notification,
) -> Callable:
    """Create a callback for notification action."""

    def callback(notif: Any, action_id: str, user_data: Any) -> None:
        logger.debug(f"Notification action triggered: {action_id}")

        # Handle the action
        if action.command:
            # Custom command
            try:
                subprocess.Popen(
                    action.command,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                logger.error(f"Failed to execute action command: {e}")

        elif action.id in ("open", "investigate", "view", "default"):
            # Open ELLE REPL with context
            _open_elle_repl(notification.context_type, notification.context_id)

        elif action.id == "confirm":
            # Confirmation action - open ELLE with command
            _open_elle_repl(notification.context_type, notification.context_id)

        elif action.id == "cancel":
            # Cancel action
            if notification.context_type == "reboot" and notification.context_id:
                _cancel_reboot(notification.context_id)

        elif action.id == "dismiss":
            # Just dismiss, no action needed
            pass

    return callback


# =============================================================================
# notify-send Backend (Fallback)
# =============================================================================


def _send_via_notify_send(notification: Notification) -> NotificationResult:
    """Send notification using notify-send command."""
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return NotificationResult(
            success=False,
            error="notify-send not found",
            method="notify-send",
        )

    try:
        cmd = [
            notify_send,
            "--app-name", APP_NAME,
            "--icon", notification.get_icon(),
            "--urgency", notification.get_urgency().value,
        ]

        # Add timeout
        if notification.timeout_ms > 0:
            cmd.extend(["--expire-time", str(notification.timeout_ms)])

        # Add category hint
        cmd.extend(["--category", notification.category.value])

        # Add actions (notify-send 0.8+ supports this)
        for action in notification.actions:
            cmd.extend(["--action", f"{action.id}={action.label}"])

        # Add title and body
        cmd.extend([notification.title, notification.body])

        # Run notify-send
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            # Check if an action was returned (notify-send prints action ID)
            if result.stdout.strip():
                action_id = result.stdout.strip()
                _handle_notify_send_action(action_id, notification)

            return NotificationResult(
                success=True,
                method="notify-send",
            )
        else:
            return NotificationResult(
                success=False,
                error=result.stderr or f"Exit code {result.returncode}",
                method="notify-send",
            )

    except subprocess.TimeoutExpired:
        return NotificationResult(
            success=False,
            error="notify-send timed out",
            method="notify-send",
        )
    except Exception as e:
        logger.error(f"Failed to send notification via notify-send: {e}")
        return NotificationResult(
            success=False,
            error=str(e),
            method="notify-send",
        )


def _handle_notify_send_action(action_id: str, notification: Notification) -> None:
    """Handle action from notify-send response."""
    if action_id in ("open", "investigate", "view", "default"):
        _open_elle_repl(notification.context_type, notification.context_id)
    elif action_id == "confirm" and notification.context_type == "reboot":
        _open_elle_repl("reboot", notification.context_id)


# =============================================================================
# Action Handlers
# =============================================================================


@lru_cache(maxsize=1)
def _find_terminal() -> tuple[str, list[str]] | None:
    """Find an available terminal emulator."""
    for name, cmd in TERMINAL_EMULATORS:
        if shutil.which(name):
            return (name, cmd)
    return None


def _open_elle_repl(
    context_type: str | None = None,
    context_id: str | None = None,
) -> bool:
    """Open ELLE REPL in a terminal.

    Args:
        context_type: Type of context to jump to (incident, reboot, etc.)
        context_id: ID of the context item.

    Returns:
        True if terminal was opened successfully.
    """
    terminal = _find_terminal()
    if not terminal:
        logger.error("No terminal emulator found")
        return False

    name, base_cmd = terminal

    # Build the elle command with context
    elle_cmd = ["elle"]
    if context_type and context_id:
        # Add command to show context on startup
        if context_type == "incident":
            elle_cmd = ["elle", "incident", context_id]
        elif context_type == "reboot":
            elle_cmd = ["elle", "reboot", context_id]
        # For other types, just open ELLE

    # Build terminal command
    if name == "gnome-terminal":
        cmd = ["gnome-terminal", "--"] + elle_cmd
    elif name == "konsole":
        cmd = ["konsole", "-e"] + elle_cmd
    elif name == "kitty":
        cmd = ["kitty"] + elle_cmd
    elif name == "alacritty":
        cmd = ["alacritty", "-e"] + elle_cmd
    else:
        # Generic: use -e with space-joined command
        cmd = [name, "-e", " ".join(elle_cmd)]

    try:
        # Set DISPLAY if not set (for headless daemon)
        env = os.environ.copy()
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        logger.info(f"Opened ELLE REPL in {name}")
        return True

    except Exception as e:
        logger.error(f"Failed to open terminal: {e}")
        return False


def _cancel_reboot(intent_id: str) -> None:
    """Cancel a pending reboot."""
    try:
        from elle.daemon.reboot import get_manager

        manager = get_manager()
        asyncio.create_task(manager.cancel_pending_reboot(intent_id))
        logger.info(f"Cancelled reboot intent: {intent_id}")
    except Exception as e:
        logger.error(f"Failed to cancel reboot: {e}")


# =============================================================================
# Rate Limiting
# =============================================================================


def _should_send(notification: Notification) -> bool:
    """Check if notification should be sent (rate limiting)."""
    import time

    # Create a key from notification content
    key = f"{notification.category}:{notification.title}:{notification.body[:50]}"

    now = time.time()
    last_sent = _notification_history.get(key, 0)

    if now - last_sent < RATE_LIMIT_SECONDS:
        logger.debug(f"Rate limiting notification: {notification.title}")
        return False

    _notification_history[key] = now

    # Clean old entries
    cutoff = now - 60
    for k in list(_notification_history.keys()):
        if _notification_history[k] < cutoff:
            del _notification_history[k]

    return True


# =============================================================================
# Public API
# =============================================================================


def send(notification: Notification) -> NotificationResult:
    """Send a notification to the desktop and mobile devices.

    Tries libnotify first (full action support), falls back to notify-send.
    Also pushes to connected mobile devices if the gateway is active.

    Args:
        notification: The notification to send.

    Returns:
        NotificationResult with status.
    """
    # Check rate limiting
    if not _should_send(notification):
        return NotificationResult(
            success=True,
            error="Rate limited",
            method="rate_limit",
        )

    # Try to push to mobile devices (non-blocking)
    _push_to_mobile(notification)

    # Try libnotify first (supports actions)
    if _gi_available:
        result = _send_via_libnotify(notification)
        if result.success:
            return result
        logger.debug(f"libnotify failed, trying notify-send: {result.error}")

    # Fall back to notify-send
    return _send_via_notify_send(notification)


def _push_to_mobile(notification: Notification) -> None:
    """Push notification to mobile devices if gateway is active.

    Args:
        notification: The notification to push.
    """
    try:
        from elle.daemon.notifications.mobile_push import get_mobile_notifier

        notifier = get_mobile_notifier()
        if notifier.is_available():
            # Run async push in background
            asyncio.create_task(notifier.push_notification(notification))
    except Exception as e:
        logger.debug(f"Mobile push failed: {e}")


def notify(
    title: str,
    body: str,
    category: NotificationCategory = NotificationCategory.INFO,
    urgency: NotificationUrgency | None = None,
    icon: str | None = None,
    timeout_ms: int = 5000,
    actions: list[NotificationAction] | None = None,
    context_type: str | None = None,
    context_id: str | None = None,
) -> NotificationResult:
    """Send a notification with the given parameters.

    Convenience function for creating and sending a notification.

    Args:
        title: Notification title.
        body: Notification body text.
        category: Notification category.
        urgency: Override urgency level.
        icon: Override icon name.
        timeout_ms: Notification timeout (0 = no timeout).
        actions: Action buttons.
        context_type: Type of context (for action handling).
        context_id: Context ID (for action handling).

    Returns:
        NotificationResult with status.
    """
    notification = Notification(
        title=title,
        body=body,
        category=category,
        urgency=urgency,
        icon=icon,
        timeout_ms=timeout_ms,
        actions=tuple(actions) if actions else (),
        default_action="open" if actions else None,
        context_type=context_type,
        context_id=context_id,
    )
    return send(notification)


# =============================================================================
# Convenience Functions
# =============================================================================


def notify_job_complete(
    job_name: str,
    success: bool = True,
    details: str | None = None,
) -> NotificationResult:
    """Send a job completion notification."""
    notification = job_complete_notification(job_name, success, details)
    return send(notification)


def notify_incident(
    title: str,
    severity: str,
    domain: str,
    incident_id: str,
    summary: str | None = None,
    *,
    symptoms: list[str] | tuple[str, ...] | None = None,
    metrics: dict[str, Any] | None = None,
    entities: list[str] | tuple[str, ...] | None = None,
    suspected_causes: list[str] | tuple[str, ...] | None = None,
    entity: str | None = None,
) -> NotificationResult:
    """Send an incident detection notification with rich context.

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
        NotificationResult with status.
    """
    notification = incident_notification(
        title,
        severity,
        domain,
        incident_id,
        summary,
        symptoms=symptoms,
        metrics=metrics,
        entities=entities,
        suspected_causes=suspected_causes,
        entity=entity,
    )
    return send(notification)


def notify_reboot_status(
    status: str,
    goal: str,
    intent_id: str = "",
    details: str | None = None,
) -> NotificationResult:
    """Send a reboot status notification.

    Args:
        status: One of 'required', 'pending', 'success', 'failed', 'rollback'.
        goal: The goal of the reboot operation.
        intent_id: The reboot intent ID.
        details: Additional details.

    Returns:
        NotificationResult with status.
    """
    notification = reboot_notification(status, goal, intent_id, details)
    return send(notification)


def notify_health(
    component: str,
    message: str,
    severity: str = "warning",
    *,
    metrics: dict[str, Any] | None = None,
) -> NotificationResult:
    """Send a system health notification with context.

    Args:
        component: System component (disk, memory, network, etc.).
        message: Health message.
        severity: One of 'warning', 'critical', 'recovered'.
        metrics: Optional metrics for context (used_pct, available_mb, etc.).

    Returns:
        NotificationResult with status.
    """
    notification = health_notification(
        severity, component, message, severity, metrics=metrics
    )
    return send(notification)


def notify_error(
    title: str,
    message: str,
    context_type: str | None = None,
    context_id: str | None = None,
) -> NotificationResult:
    """Send an error notification."""
    return notify(
        title=title,
        body=message,
        category=NotificationCategory.ERROR,
        timeout_ms=0,  # Don't auto-dismiss errors
        actions=[NotificationAction(id="open", label="View Details")],
        context_type=context_type,
        context_id=context_id,
    )


def notify_info(
    title: str,
    message: str,
) -> NotificationResult:
    """Send an informational notification."""
    return notify(
        title=title,
        body=message,
        category=NotificationCategory.INFO,
        timeout_ms=5000,
    )


# =============================================================================
# Async Wrappers
# =============================================================================


async def send_async(notification: Notification) -> NotificationResult:
    """Send a notification asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, send, notification)


async def notify_async(
    title: str,
    body: str,
    **kwargs: Any,
) -> NotificationResult:
    """Send a notification asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: notify(title, body, **kwargs))


# =============================================================================
# Service Management
# =============================================================================


class NotificationService:
    """Background service for managing notifications.

    Provides queued notification sending and notification history tracking.
    """

    def __init__(self) -> None:
        """Initialize the notification service."""
        self._running = False
        self._queue: asyncio.Queue[Notification] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._history: list[tuple[Notification, NotificationResult]] = []
        self._max_history = 100

    async def start(self) -> None:
        """Start the notification service."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._process_queue())
        logger.info("Notification service started")

    async def stop(self) -> None:
        """Stop the notification service."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Notification service stopped")

    async def _process_queue(self) -> None:
        """Process queued notifications."""
        while self._running:
            try:
                notification = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0,
                )
                result = await send_async(notification)
                self._add_to_history(notification, result)

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing notification: {e}")

    def queue(self, notification: Notification) -> None:
        """Queue a notification for sending."""
        try:
            self._queue.put_nowait(notification)
        except asyncio.QueueFull:
            logger.warning("Notification queue full, dropping notification")

    def _add_to_history(
        self,
        notification: Notification,
        result: NotificationResult,
    ) -> None:
        """Add notification to history."""
        self._history.append((notification, result))
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_history(
        self,
        limit: int = 20,
    ) -> list[tuple[Notification, NotificationResult]]:
        """Get recent notification history."""
        return self._history[-limit:]


# Module-level service instance
_service: NotificationService | None = None


def get_service() -> NotificationService:
    """Get the notification service singleton."""
    global _service
    if _service is None:
        _service = NotificationService()
    return _service
