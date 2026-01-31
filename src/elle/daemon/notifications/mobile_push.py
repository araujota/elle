"""Mobile push notification system for ELLE.

Pushes notifications to connected mobile devices via Server-Sent Events (SSE).
When the mobile gateway is active, notifications are broadcast to all
connected mobile clients in real-time.

Usage:
    from elle.daemon.notifications.mobile_push import (
        get_mobile_notifier,
        MobileNotificationEvent,
    )

    # Get the singleton
    notifier = get_mobile_notifier()

    # Push a notification
    await notifier.push(notification)

    # Or check if mobile is available
    if notifier.is_available():
        await notifier.push(notification)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from elle.daemon.notifications.models import Notification

logger = logging.getLogger(__name__)


# =============================================================================
# Mobile Notification Models
# =============================================================================


class MobileEventType(str, Enum):
    """Types of events pushed to mobile clients."""

    # Notifications
    NOTIFICATION = "notification"
    NOTIFICATION_CLEARED = "notification_cleared"

    # System events
    INCIDENT_DETECTED = "incident_detected"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_UPDATED = "incident_updated"

    # Interactive prompts
    PLAN_READY = "plan_ready"
    DIFF_READY = "diff_ready"
    CONFIRMATION_REQUIRED = "confirmation_required"

    # Health and status
    HEALTH_ALERT = "health_alert"
    HEALTH_RECOVERED = "health_recovered"

    # Jobs
    JOB_STARTED = "job_started"
    JOB_PROGRESS = "job_progress"
    JOB_COMPLETE = "job_complete"
    JOB_FAILED = "job_failed"

    # Reboot
    REBOOT_REQUIRED = "reboot_required"
    REBOOT_PENDING = "reboot_pending"
    REBOOT_COMPLETE = "reboot_complete"

    # Forecast
    FORECAST_WARNING = "forecast_warning"
    FORECAST_REMEDIATED = "forecast_remediated"

    # Connection
    HEARTBEAT = "heartbeat"
    CONNECTED = "connected"


class MobileNotificationEvent(BaseModel):
    """An event to push to mobile clients.

    This is the envelope format for all mobile push events.
    """

    model_config = ConfigDict(frozen=True)

    # Event identity
    event_id: str = Field(
        default_factory=lambda: f"evt-{uuid.uuid4().hex[:16]}",
        description="Unique event identifier",
    )
    event_type: MobileEventType = Field(description="Type of event")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the event was created",
    )

    # Notification content
    title: str = Field(description="Notification title")
    body: str = Field(description="Notification body text")
    category: str = Field(default="info", description="Notification category")
    urgency: str = Field(default="normal", description="Urgency level")
    icon: str | None = Field(default=None, description="Icon name")

    # Context for follow-up
    context_type: str | None = Field(
        default=None,
        description="Type of context (incident, job, reboot, plan, diff)",
    )
    context_id: str | None = Field(
        default=None,
        description="ID for follow-up (incident_id, job_id, etc.)",
    )

    # Additional data
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional event-specific data",
    )

    # Actions available on mobile
    actions: tuple[dict[str, str], ...] = Field(
        default_factory=tuple,
        description="Available actions (id, label pairs)",
    )

    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        # Pydantic v1/v2 compatibility
        try:
            json_str = self.model_dump_json()
        except AttributeError:
            import json

            json_str = json.dumps(self.dict())
        return f"event: {self.event_type.value}\ndata: {json_str}\n\n"


# =============================================================================
# Mobile Client Connection
# =============================================================================


class MobileClientConnection:
    """A connected mobile client awaiting events."""

    def __init__(self, device_id: str, device_name: str):
        self.device_id = device_id
        self.device_name = device_name
        self.connected_at = datetime.utcnow()
        self.queue: asyncio.Queue[MobileNotificationEvent] = asyncio.Queue()
        self._closed = False

    async def send(self, event: MobileNotificationEvent) -> bool:
        """Queue an event for this client.

        Args:
            event: The event to send.

        Returns:
            True if queued successfully.
        """
        if self._closed:
            return False

        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning(f"Event queue full for device {self.device_id}")
            return False

    async def events(self) -> AsyncIterator[str]:
        """Iterate over events for this client.

        Yields:
            SSE-formatted event strings.
        """
        # Send connected event
        connected = MobileNotificationEvent(
            event_type=MobileEventType.CONNECTED,
            title="Connected",
            body="Receiving notifications from ELLE",
            data={"device_id": self.device_id},
        )
        yield connected.to_sse()

        # Stream events from queue
        while not self._closed:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=30.0)
                yield event.to_sse()
            except TimeoutError:
                # Send heartbeat to keep connection alive
                heartbeat = MobileNotificationEvent(
                    event_type=MobileEventType.HEARTBEAT,
                    title="Heartbeat",
                    body="",
                )
                yield heartbeat.to_sse()
            except asyncio.CancelledError:
                break

    def close(self) -> None:
        """Close this connection."""
        self._closed = True


# =============================================================================
# Mobile Push Notifier
# =============================================================================


class MobilePushNotifier:
    """Manages push notifications to mobile devices.

    Singleton that tracks connected clients and broadcasts events.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MobileClientConnection] = {}
        self._lock = asyncio.Lock()
        self._gateway_available = False

    def set_gateway_available(self, available: bool) -> None:
        """Set whether the mobile gateway is available.

        Args:
            available: True if gateway is running.
        """
        self._gateway_available = available
        if not available:
            # Close all client connections
            for client in self._clients.values():
                client.close()
            self._clients.clear()

    def is_available(self) -> bool:
        """Check if mobile push is available.

        Returns:
            True if gateway is running and clients are connected.
        """
        return self._gateway_available and len(self._clients) > 0

    def has_clients(self) -> bool:
        """Check if any clients are connected.

        Returns:
            True if at least one client is connected.
        """
        return len(self._clients) > 0

    def client_count(self) -> int:
        """Get the number of connected clients.

        Returns:
            Number of connected mobile clients.
        """
        return len(self._clients)

    async def connect(self, device_id: str, device_name: str) -> MobileClientConnection:
        """Register a new client connection.

        Args:
            device_id: The device's unique ID.
            device_name: Human-readable device name.

        Returns:
            MobileClientConnection for the client to use.
        """
        async with self._lock:
            # Close existing connection for this device
            if device_id in self._clients:
                self._clients[device_id].close()

            client = MobileClientConnection(device_id, device_name)
            self._clients[device_id] = client
            logger.info(f"Mobile client connected: {device_name} ({device_id})")
            return client

    async def disconnect(self, device_id: str) -> None:
        """Disconnect a client.

        Args:
            device_id: The device to disconnect.
        """
        async with self._lock:
            if device_id in self._clients:
                self._clients[device_id].close()
                del self._clients[device_id]
                logger.info(f"Mobile client disconnected: {device_id}")

    async def push(self, event: MobileNotificationEvent) -> int:
        """Push an event to all connected clients.

        Args:
            event: The event to broadcast.

        Returns:
            Number of clients that received the event.
        """
        if not self._clients:
            return 0

        count = 0
        async with self._lock:
            for client in list(self._clients.values()):
                if await client.send(event):
                    count += 1

        if count > 0:
            logger.debug(f"Pushed {event.event_type.value} to {count} mobile clients")

        return count

    async def push_notification(self, notification: Notification) -> int:
        """Push a desktop notification to mobile clients.

        Converts the Notification model to a MobileNotificationEvent.

        Args:
            notification: The notification to push.

        Returns:
            Number of clients that received the notification.
        """
        # Map category to event type
        event_type = _map_category_to_event_type(notification.category.value)

        # Build actions list
        actions = tuple({"id": action.id, "label": action.label} for action in notification.actions)

        event = MobileNotificationEvent(
            event_type=event_type,
            title=notification.title,
            body=notification.body,
            category=notification.category.value,
            urgency=notification.get_urgency().value,
            icon=notification.get_icon(),
            context_type=notification.context_type,
            context_id=notification.context_id,
            actions=actions,
        )

        return await self.push(event)

    async def push_plan_ready(
        self,
        plan_id: str,
        title: str,
        summary: str,
        step_count: int,
    ) -> int:
        """Push a plan review notification.

        Args:
            plan_id: Unique plan identifier.
            title: Plan title.
            summary: Brief plan summary.
            step_count: Number of steps in the plan.

        Returns:
            Number of clients notified.
        """
        event = MobileNotificationEvent(
            event_type=MobileEventType.PLAN_READY,
            title="Plan Ready for Review",
            body=f"{title}\n{summary}",
            category="plan",
            urgency="normal",
            context_type="plan",
            context_id=plan_id,
            data={"step_count": step_count},
            actions=(
                {"id": "view", "label": "View Plan"},
                {"id": "approve", "label": "Approve"},
                {"id": "reject", "label": "Reject"},
            ),
        )
        return await self.push(event)

    async def push_diff_ready(
        self,
        diff_id: str,
        file_path: str,
        change_summary: str,
    ) -> int:
        """Push a diff review notification.

        Args:
            diff_id: Unique diff identifier.
            file_path: Path of the file being changed.
            change_summary: Brief summary of changes.

        Returns:
            Number of clients notified.
        """
        event = MobileNotificationEvent(
            event_type=MobileEventType.DIFF_READY,
            title="Config Change Ready for Review",
            body=f"{file_path}\n{change_summary}",
            category="diff",
            urgency="normal",
            context_type="diff",
            context_id=diff_id,
            data={"file_path": file_path},
            actions=(
                {"id": "view", "label": "View Diff"},
                {"id": "apply", "label": "Apply"},
                {"id": "reject", "label": "Reject"},
            ),
        )
        return await self.push(event)

    async def push_confirmation_required(
        self,
        confirmation_id: str,
        title: str,
        description: str,
        risk_level: str = "medium",
    ) -> int:
        """Push a confirmation request.

        Args:
            confirmation_id: Unique confirmation identifier.
            title: What needs confirmation.
            description: Details about the action.
            risk_level: Risk level (low, medium, high, critical).

        Returns:
            Number of clients notified.
        """
        urgency = "critical" if risk_level in ("high", "critical") else "normal"

        event = MobileNotificationEvent(
            event_type=MobileEventType.CONFIRMATION_REQUIRED,
            title=f"Confirmation Required: {title}",
            body=description,
            category="confirmation",
            urgency=urgency,
            context_type="confirmation",
            context_id=confirmation_id,
            data={"risk_level": risk_level},
            actions=(
                {"id": "confirm", "label": "Confirm"},
                {"id": "deny", "label": "Deny"},
            ),
        )
        return await self.push(event)

    async def push_job_progress(
        self,
        job_id: str,
        job_name: str,
        progress: int,
        status: str,
    ) -> int:
        """Push a job progress update.

        Args:
            job_id: Job identifier.
            job_name: Human-readable job name.
            progress: Progress percentage (0-100).
            status: Current status message.

        Returns:
            Number of clients notified.
        """
        event = MobileNotificationEvent(
            event_type=MobileEventType.JOB_PROGRESS,
            title=job_name,
            body=status,
            category="job_progress",
            urgency="low",
            context_type="job",
            context_id=job_id,
            data={"progress": progress, "status": status},
        )
        return await self.push(event)

    async def push_forecast_warning(
        self,
        incident_id: str,
        metric: str,
        title: str,
        body: str,
        plan_summary: str | None = None,
    ) -> int:
        """Push a forecast warning notification to mobile clients.

        Args:
            incident_id: Linked incident ID.
            metric: Metric that triggered the forecast.
            title: Notification title.
            body: Notification body.
            plan_summary: Brief plan summary.

        Returns:
            Number of clients notified.
        """
        data: dict[str, Any] = {"metric": metric}
        if plan_summary:
            data["plan_summary"] = plan_summary

        event = MobileNotificationEvent(
            event_type=MobileEventType.FORECAST_WARNING,
            title=title,
            body=body,
            category="forecast_warning",
            urgency="normal",
            context_type="incident",
            context_id=incident_id,
            data=data,
            actions=(
                {"id": "execute_plan", "label": "Execute Now"},
                {"id": "investigate", "label": "Investigate"},
                {"id": "dismiss", "label": "Dismiss"},
            ),
        )
        return await self.push(event)


def _map_category_to_event_type(category: str) -> MobileEventType:
    """Map notification category to mobile event type.

    Args:
        category: Notification category value.

    Returns:
        Appropriate MobileEventType.
    """
    category_map = {
        "job_complete": MobileEventType.JOB_COMPLETE,
        "job_failed": MobileEventType.JOB_FAILED,
        "task_progress": MobileEventType.JOB_PROGRESS,
        "incident_detected": MobileEventType.INCIDENT_DETECTED,
        "incident_resolved": MobileEventType.INCIDENT_RESOLVED,
        "incident_escalated": MobileEventType.INCIDENT_DETECTED,
        "reboot_required": MobileEventType.REBOOT_REQUIRED,
        "reboot_pending": MobileEventType.REBOOT_PENDING,
        "reboot_success": MobileEventType.REBOOT_COMPLETE,
        "reboot_failed": MobileEventType.NOTIFICATION,
        "reboot_rollback": MobileEventType.NOTIFICATION,
        "health_warning": MobileEventType.HEALTH_ALERT,
        "health_critical": MobileEventType.HEALTH_ALERT,
        "health_recovered": MobileEventType.HEALTH_RECOVERED,
        "forecast_warning": MobileEventType.FORECAST_WARNING,
        "forecast_remediated": MobileEventType.FORECAST_REMEDIATED,
    }
    return category_map.get(category, MobileEventType.NOTIFICATION)


# =============================================================================
# Singleton
# =============================================================================

_mobile_notifier: MobilePushNotifier | None = None


def get_mobile_notifier() -> MobilePushNotifier:
    """Get the mobile push notifier singleton.

    Returns:
        MobilePushNotifier instance.
    """
    global _mobile_notifier
    if _mobile_notifier is None:
        _mobile_notifier = MobilePushNotifier()
    return _mobile_notifier
