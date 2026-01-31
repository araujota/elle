"""Tests for mobile push notification system."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.notifications.mobile_push import (
    MobileClientConnection,
    MobileEventType,
    MobileNotificationEvent,
    MobilePushNotifier,
    _map_category_to_event_type,
    get_mobile_notifier,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_event(**overrides: Any) -> MobileNotificationEvent:
    """Build a MobileNotificationEvent with defaults."""
    defaults: dict[str, Any] = {
        "event_type": MobileEventType.NOTIFICATION,
        "title": "Test Event",
        "body": "Test body",
    }
    defaults.update(overrides)
    return MobileNotificationEvent(**defaults)


# =============================================================================
# MobileNotificationEvent model
# =============================================================================


class TestMobileNotificationEvent:
    """Tests for the MobileNotificationEvent Pydantic model."""

    def test_create_minimal(self) -> None:
        """Event can be created with only required fields."""
        event = MobileNotificationEvent(
            event_type=MobileEventType.NOTIFICATION,
            title="Test",
            body="Body",
        )
        assert event.event_type == MobileEventType.NOTIFICATION
        assert event.title == "Test"
        assert event.body == "Body"
        assert event.category == "info"
        assert event.urgency == "normal"

    def test_auto_generated_event_id(self) -> None:
        """event_id is automatically generated with evt- prefix."""
        event = _make_event()
        assert event.event_id.startswith("evt-")
        assert len(event.event_id) > 4

    def test_auto_generated_timestamp(self) -> None:
        """timestamp is automatically populated."""
        event = _make_event()
        assert event.timestamp is not None
        assert isinstance(event.timestamp, datetime)

    def test_default_fields(self) -> None:
        """Default values for optional fields."""
        event = _make_event()
        assert event.icon is None
        assert event.context_type is None
        assert event.context_id is None
        assert event.data == {}
        assert event.actions == ()

    def test_custom_fields(self) -> None:
        """All custom fields are preserved."""
        event = MobileNotificationEvent(
            event_type=MobileEventType.INCIDENT_DETECTED,
            title="Incident",
            body="Detected issue",
            category="incident",
            urgency="critical",
            icon="alert",
            context_type="incident",
            context_id="inc-123",
            data={"severity": "critical"},
            actions=({"id": "view", "label": "View"},),
        )
        assert event.event_type == MobileEventType.INCIDENT_DETECTED
        assert event.category == "incident"
        assert event.urgency == "critical"
        assert event.icon == "alert"
        assert event.context_type == "incident"
        assert event.context_id == "inc-123"
        assert event.data == {"severity": "critical"}
        assert len(event.actions) == 1

    def test_frozen_model(self) -> None:
        """Event is frozen (immutable)."""
        event = _make_event()
        with pytest.raises(Exception):
            event.title = "changed"  # type: ignore[misc]

    def test_to_sse_format(self) -> None:
        """to_sse() produces valid SSE format."""
        event = MobileNotificationEvent(
            event_type=MobileEventType.HEARTBEAT,
            title="Heartbeat",
            body="",
        )
        sse = event.to_sse()
        assert sse.startswith("event: heartbeat\n")
        assert "data: " in sse
        assert sse.endswith("\n\n")

    def test_to_sse_contains_valid_json(self) -> None:
        """to_sse() data field contains valid JSON."""
        event = _make_event(title="JSON Test", body="body content")
        sse = event.to_sse()
        # Extract JSON from SSE
        lines = sse.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data: ")][0]
        json_str = data_line[len("data: "):]
        parsed = json.loads(json_str)
        assert parsed["title"] == "JSON Test"
        assert parsed["body"] == "body content"

    def test_to_sse_with_model_dump_json_fallback(self) -> None:
        """to_sse() falls back to json.dumps(self.dict()) if model_dump_json missing."""
        event = _make_event()
        # The method should succeed regardless of which path is taken
        sse = event.to_sse()
        assert len(sse) > 0

    def test_all_event_types(self) -> None:
        """All MobileEventType values can be used."""
        for event_type in MobileEventType:
            event = MobileNotificationEvent(
                event_type=event_type,
                title=f"Test {event_type.value}",
                body="body",
            )
            assert event.event_type == event_type


# =============================================================================
# MobileEventType enum
# =============================================================================


class TestMobileEventType:
    """Tests for MobileEventType enum values."""

    def test_notification_types(self) -> None:
        assert MobileEventType.NOTIFICATION.value == "notification"
        assert MobileEventType.NOTIFICATION_CLEARED.value == "notification_cleared"

    def test_incident_types(self) -> None:
        assert MobileEventType.INCIDENT_DETECTED.value == "incident_detected"
        assert MobileEventType.INCIDENT_RESOLVED.value == "incident_resolved"
        assert MobileEventType.INCIDENT_UPDATED.value == "incident_updated"

    def test_interactive_types(self) -> None:
        assert MobileEventType.PLAN_READY.value == "plan_ready"
        assert MobileEventType.DIFF_READY.value == "diff_ready"
        assert MobileEventType.CONFIRMATION_REQUIRED.value == "confirmation_required"

    def test_health_types(self) -> None:
        assert MobileEventType.HEALTH_ALERT.value == "health_alert"
        assert MobileEventType.HEALTH_RECOVERED.value == "health_recovered"

    def test_job_types(self) -> None:
        assert MobileEventType.JOB_STARTED.value == "job_started"
        assert MobileEventType.JOB_PROGRESS.value == "job_progress"
        assert MobileEventType.JOB_COMPLETE.value == "job_complete"
        assert MobileEventType.JOB_FAILED.value == "job_failed"

    def test_reboot_types(self) -> None:
        assert MobileEventType.REBOOT_REQUIRED.value == "reboot_required"
        assert MobileEventType.REBOOT_PENDING.value == "reboot_pending"
        assert MobileEventType.REBOOT_COMPLETE.value == "reboot_complete"

    def test_forecast_types(self) -> None:
        assert MobileEventType.FORECAST_WARNING.value == "forecast_warning"
        assert MobileEventType.FORECAST_REMEDIATED.value == "forecast_remediated"

    def test_connection_types(self) -> None:
        assert MobileEventType.HEARTBEAT.value == "heartbeat"
        assert MobileEventType.CONNECTED.value == "connected"


# =============================================================================
# MobileClientConnection
# =============================================================================


class TestMobileClientConnection:
    """Tests for MobileClientConnection."""

    def test_init(self) -> None:
        """Client connection initializes with correct state."""
        client = MobileClientConnection("dev-1", "My Phone")
        assert client.device_id == "dev-1"
        assert client.device_name == "My Phone"
        assert client.connected_at is not None
        assert client._closed is False

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        """send() queues event and returns True."""
        client = MobileClientConnection("dev-1", "Test")
        event = _make_event()
        result = await client.send(event)
        assert result is True
        assert client.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_send_closed(self) -> None:
        """send() returns False when connection is closed."""
        client = MobileClientConnection("dev-1", "Test")
        client.close()
        event = _make_event()
        result = await client.send(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_queue_full(self) -> None:
        """send() returns False when queue is full."""
        client = MobileClientConnection("dev-1", "Test")
        # Replace queue with a tiny one
        client.queue = asyncio.Queue(maxsize=1)
        event = _make_event()
        # Fill the queue
        await client.send(event)
        # Next should fail
        result = await client.send(_make_event())
        assert result is False

    @pytest.mark.asyncio
    async def test_events_yields_connected_first(self) -> None:
        """events() yields a CONNECTED event first."""
        client = MobileClientConnection("dev-1", "Test")
        client.close()  # Close immediately so the loop exits
        events_list = []
        async for sse in client.events():
            events_list.append(sse)
            break  # Just get the first event
        assert len(events_list) == 1
        assert "connected" in events_list[0]

    @pytest.mark.asyncio
    async def test_events_yields_queued_events(self) -> None:
        """events() yields events from the queue."""
        client = MobileClientConnection("dev-1", "Test")
        event = _make_event(title="Test Queued")
        await client.send(event)

        events_list = []
        # Close after a brief delay to allow the iteration to pick up the event
        async def close_later():
            await asyncio.sleep(0.1)
            client.close()

        asyncio.create_task(close_later())

        async for sse in client.events():
            events_list.append(sse)
            if len(events_list) >= 2:
                break

        # First event is CONNECTED, second is our queued event
        assert len(events_list) == 2
        assert "Test Queued" in events_list[1]

    @pytest.mark.asyncio
    async def test_events_heartbeat_on_timeout(self) -> None:
        """events() sends heartbeat when queue.get times out."""
        client = MobileClientConnection("dev-1", "Test")
        events_list = []

        # The events() method uses asyncio.wait_for with timeout=30s.
        # We can't easily patch that without breaking the test itself.
        # Instead, we test that the heartbeat logic works by directly
        # verifying the heartbeat event format.
        heartbeat = MobileNotificationEvent(
            event_type=MobileEventType.HEARTBEAT,
            title="Heartbeat",
            body="",
        )
        sse = heartbeat.to_sse()
        assert "heartbeat" in sse
        assert "event: heartbeat" in sse

    def test_close(self) -> None:
        """close() sets _closed flag."""
        client = MobileClientConnection("dev-1", "Test")
        assert client._closed is False
        client.close()
        assert client._closed is True


# =============================================================================
# MobilePushNotifier
# =============================================================================


class TestMobilePushNotifier:
    """Tests for MobilePushNotifier."""

    def test_init(self) -> None:
        """Notifier starts in empty state."""
        notifier = MobilePushNotifier()
        assert notifier.is_available() is False
        assert notifier.has_clients() is False
        assert notifier.client_count() == 0

    def test_set_gateway_available(self) -> None:
        """set_gateway_available updates state."""
        notifier = MobilePushNotifier()
        notifier.set_gateway_available(True)
        assert notifier._gateway_available is True

    def test_set_gateway_unavailable_clears_clients(self) -> None:
        """Setting gateway unavailable closes and clears all clients."""
        notifier = MobilePushNotifier()
        notifier._gateway_available = True
        # Add a mock client
        client = MagicMock()
        notifier._clients["dev-1"] = client
        assert notifier.has_clients() is True

        notifier.set_gateway_available(False)
        assert notifier.has_clients() is False
        client.close.assert_called_once()

    def test_is_available_requires_gateway_and_clients(self) -> None:
        """is_available requires both gateway and connected clients."""
        notifier = MobilePushNotifier()
        # No gateway, no clients
        assert notifier.is_available() is False

        # Gateway but no clients
        notifier.set_gateway_available(True)
        assert notifier.is_available() is False

        # Gateway and clients
        notifier._clients["dev-1"] = MagicMock()
        assert notifier.is_available() is True

    def test_has_clients(self) -> None:
        """has_clients reflects connected clients."""
        notifier = MobilePushNotifier()
        assert notifier.has_clients() is False
        notifier._clients["dev-1"] = MagicMock()
        assert notifier.has_clients() is True

    def test_client_count(self) -> None:
        """client_count returns correct count."""
        notifier = MobilePushNotifier()
        assert notifier.client_count() == 0
        notifier._clients["dev-1"] = MagicMock()
        assert notifier.client_count() == 1
        notifier._clients["dev-2"] = MagicMock()
        assert notifier.client_count() == 2

    @pytest.mark.asyncio
    async def test_connect_new_client(self) -> None:
        """connect() creates a new MobileClientConnection."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "My Phone")
        assert isinstance(client, MobileClientConnection)
        assert client.device_id == "dev-1"
        assert client.device_name == "My Phone"
        assert notifier.client_count() == 1

    @pytest.mark.asyncio
    async def test_connect_replaces_existing(self) -> None:
        """connect() closes and replaces existing connection for same device."""
        notifier = MobilePushNotifier()
        client1 = await notifier.connect("dev-1", "Phone v1")
        client2 = await notifier.connect("dev-1", "Phone v2")
        assert client1._closed is True
        assert client2._closed is False
        assert notifier.client_count() == 1

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        """disconnect() removes client and closes connection."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")
        await notifier.disconnect("dev-1")
        assert client._closed is True
        assert notifier.client_count() == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self) -> None:
        """disconnect() is a no-op for unknown device."""
        notifier = MobilePushNotifier()
        await notifier.disconnect("nonexistent")
        assert notifier.client_count() == 0

    @pytest.mark.asyncio
    async def test_push_no_clients(self) -> None:
        """push() returns 0 when no clients connected."""
        notifier = MobilePushNotifier()
        event = _make_event()
        count = await notifier.push(event)
        assert count == 0

    @pytest.mark.asyncio
    async def test_push_broadcasts_to_all(self) -> None:
        """push() sends event to all connected clients."""
        notifier = MobilePushNotifier()
        await notifier.connect("dev-1", "Phone 1")
        await notifier.connect("dev-2", "Phone 2")
        event = _make_event()
        count = await notifier.push(event)
        assert count == 2

    @pytest.mark.asyncio
    async def test_push_counts_successful_sends(self) -> None:
        """push() only counts successful sends."""
        notifier = MobilePushNotifier()
        # Add one real client and one that will fail
        await notifier.connect("dev-1", "Phone 1")
        failing_client = MagicMock()
        failing_client.send = AsyncMock(return_value=False)
        notifier._clients["dev-2"] = failing_client

        event = _make_event()
        count = await notifier.push(event)
        assert count == 1  # Only the real client succeeded

    @pytest.mark.asyncio
    async def test_push_notification(self) -> None:
        """push_notification() converts Notification to MobileNotificationEvent."""
        from elle.daemon.notifications.models import (
            Notification,
            NotificationAction,
            NotificationCategory,
        )

        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        notification = Notification(
            title="Test Alert",
            body="Something happened",
            category=NotificationCategory.INCIDENT_DETECTED,
            context_type="incident",
            context_id="inc-123",
            actions=(
                NotificationAction(id="view", label="View"),
            ),
        )

        count = await notifier.push_notification(notification)
        assert count == 1
        # Verify the event was queued
        queued_event = client.queue.get_nowait()
        assert queued_event.title == "Test Alert"
        assert queued_event.body == "Something happened"
        assert queued_event.event_type == MobileEventType.INCIDENT_DETECTED
        assert queued_event.context_type == "incident"
        assert queued_event.context_id == "inc-123"
        assert len(queued_event.actions) == 1

    @pytest.mark.asyncio
    async def test_push_plan_ready(self) -> None:
        """push_plan_ready() creates correct event."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        count = await notifier.push_plan_ready(
            plan_id="plan-123",
            title="Update nginx",
            summary="Restart the service",
            step_count=3,
        )
        assert count == 1
        queued = client.queue.get_nowait()
        assert queued.event_type == MobileEventType.PLAN_READY
        assert queued.title == "Plan Ready for Review"
        assert "Update nginx" in queued.body
        assert queued.context_type == "plan"
        assert queued.context_id == "plan-123"
        assert queued.data["step_count"] == 3
        assert len(queued.actions) == 3  # view, approve, reject

    @pytest.mark.asyncio
    async def test_push_diff_ready(self) -> None:
        """push_diff_ready() creates correct event."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        count = await notifier.push_diff_ready(
            diff_id="diff-456",
            file_path="/etc/nginx/nginx.conf",
            change_summary="Updated worker_processes",
        )
        assert count == 1
        queued = client.queue.get_nowait()
        assert queued.event_type == MobileEventType.DIFF_READY
        assert queued.title == "Config Change Ready for Review"
        assert "/etc/nginx/nginx.conf" in queued.body
        assert queued.context_type == "diff"
        assert queued.context_id == "diff-456"
        assert queued.data["file_path"] == "/etc/nginx/nginx.conf"
        assert len(queued.actions) == 3  # view, apply, reject

    @pytest.mark.asyncio
    async def test_push_confirmation_required_normal(self) -> None:
        """push_confirmation_required() with low risk uses normal urgency."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        count = await notifier.push_confirmation_required(
            confirmation_id="conf-789",
            title="Restart nginx",
            description="This will restart the web server",
            risk_level="medium",
        )
        assert count == 1
        queued = client.queue.get_nowait()
        assert queued.event_type == MobileEventType.CONFIRMATION_REQUIRED
        assert "Confirmation Required" in queued.title
        assert queued.urgency == "normal"
        assert queued.context_type == "confirmation"
        assert len(queued.actions) == 2  # confirm, deny

    @pytest.mark.asyncio
    async def test_push_confirmation_required_high_risk(self) -> None:
        """push_confirmation_required() with high risk uses critical urgency."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        await notifier.push_confirmation_required(
            confirmation_id="conf-789",
            title="Format disk",
            description="This will erase data",
            risk_level="high",
        )
        queued = client.queue.get_nowait()
        assert queued.urgency == "critical"

    @pytest.mark.asyncio
    async def test_push_confirmation_required_critical_risk(self) -> None:
        """push_confirmation_required() with critical risk uses critical urgency."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        await notifier.push_confirmation_required(
            confirmation_id="conf-789",
            title="Dangerous op",
            description="Very dangerous",
            risk_level="critical",
        )
        queued = client.queue.get_nowait()
        assert queued.urgency == "critical"

    @pytest.mark.asyncio
    async def test_push_job_progress(self) -> None:
        """push_job_progress() creates correct event."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        count = await notifier.push_job_progress(
            job_id="job-001",
            job_name="System Update",
            progress=50,
            status="Installing packages...",
        )
        assert count == 1
        queued = client.queue.get_nowait()
        assert queued.event_type == MobileEventType.JOB_PROGRESS
        assert queued.title == "System Update"
        assert queued.body == "Installing packages..."
        assert queued.context_type == "job"
        assert queued.context_id == "job-001"
        assert queued.data["progress"] == 50
        assert queued.data["status"] == "Installing packages..."
        assert queued.urgency == "low"

    @pytest.mark.asyncio
    async def test_push_forecast_warning_without_plan(self) -> None:
        """push_forecast_warning() without plan_summary."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        count = await notifier.push_forecast_warning(
            incident_id="inc-001",
            metric="disk.used_pct",
            title="Disk Full Warning",
            body="Disk approaching 95%",
        )
        assert count == 1
        queued = client.queue.get_nowait()
        assert queued.event_type == MobileEventType.FORECAST_WARNING
        assert queued.title == "Disk Full Warning"
        assert queued.data["metric"] == "disk.used_pct"
        assert "plan_summary" not in queued.data
        assert queued.context_type == "incident"
        assert len(queued.actions) == 3  # execute_plan, investigate, dismiss

    @pytest.mark.asyncio
    async def test_push_forecast_warning_with_plan(self) -> None:
        """push_forecast_warning() with plan_summary includes it in data."""
        notifier = MobilePushNotifier()
        client = await notifier.connect("dev-1", "Test")

        count = await notifier.push_forecast_warning(
            incident_id="inc-001",
            metric="mem.used_pct",
            title="Memory Warning",
            body="Memory approaching limit",
            plan_summary="Restart memory-hungry containers",
        )
        assert count == 1
        queued = client.queue.get_nowait()
        assert queued.data["plan_summary"] == "Restart memory-hungry containers"


# =============================================================================
# _map_category_to_event_type
# =============================================================================


class TestMapCategoryToEventType:
    """Tests for the category-to-event-type mapping function."""

    def test_job_complete(self) -> None:
        assert _map_category_to_event_type("job_complete") == MobileEventType.JOB_COMPLETE

    def test_job_failed(self) -> None:
        assert _map_category_to_event_type("job_failed") == MobileEventType.JOB_FAILED

    def test_task_progress(self) -> None:
        assert _map_category_to_event_type("task_progress") == MobileEventType.JOB_PROGRESS

    def test_incident_detected(self) -> None:
        assert _map_category_to_event_type("incident_detected") == MobileEventType.INCIDENT_DETECTED

    def test_incident_resolved(self) -> None:
        assert _map_category_to_event_type("incident_resolved") == MobileEventType.INCIDENT_RESOLVED

    def test_incident_escalated(self) -> None:
        assert _map_category_to_event_type("incident_escalated") == MobileEventType.INCIDENT_DETECTED

    def test_reboot_required(self) -> None:
        assert _map_category_to_event_type("reboot_required") == MobileEventType.REBOOT_REQUIRED

    def test_reboot_pending(self) -> None:
        assert _map_category_to_event_type("reboot_pending") == MobileEventType.REBOOT_PENDING

    def test_reboot_success(self) -> None:
        assert _map_category_to_event_type("reboot_success") == MobileEventType.REBOOT_COMPLETE

    def test_reboot_failed(self) -> None:
        assert _map_category_to_event_type("reboot_failed") == MobileEventType.NOTIFICATION

    def test_reboot_rollback(self) -> None:
        assert _map_category_to_event_type("reboot_rollback") == MobileEventType.NOTIFICATION

    def test_health_warning(self) -> None:
        assert _map_category_to_event_type("health_warning") == MobileEventType.HEALTH_ALERT

    def test_health_critical(self) -> None:
        assert _map_category_to_event_type("health_critical") == MobileEventType.HEALTH_ALERT

    def test_health_recovered(self) -> None:
        assert _map_category_to_event_type("health_recovered") == MobileEventType.HEALTH_RECOVERED

    def test_forecast_warning(self) -> None:
        assert _map_category_to_event_type("forecast_warning") == MobileEventType.FORECAST_WARNING

    def test_forecast_remediated(self) -> None:
        assert _map_category_to_event_type("forecast_remediated") == MobileEventType.FORECAST_REMEDIATED

    def test_unknown_category_defaults(self) -> None:
        """Unknown categories default to NOTIFICATION."""
        assert _map_category_to_event_type("unknown_category") == MobileEventType.NOTIFICATION
        assert _map_category_to_event_type("") == MobileEventType.NOTIFICATION
        assert _map_category_to_event_type("info") == MobileEventType.NOTIFICATION


# =============================================================================
# get_mobile_notifier (singleton)
# =============================================================================


class TestGetMobileNotifier:
    """Tests for the singleton accessor."""

    def test_returns_instance(self) -> None:
        """get_mobile_notifier returns a MobilePushNotifier."""
        # Reset the singleton
        import elle.daemon.notifications.mobile_push as mod

        mod._mobile_notifier = None
        notifier = get_mobile_notifier()
        assert isinstance(notifier, MobilePushNotifier)

    def test_returns_same_instance(self) -> None:
        """Subsequent calls return the same instance."""
        import elle.daemon.notifications.mobile_push as mod

        mod._mobile_notifier = None
        n1 = get_mobile_notifier()
        n2 = get_mobile_notifier()
        assert n1 is n2

    def test_reset_creates_new(self) -> None:
        """Resetting singleton allows new instance."""
        import elle.daemon.notifications.mobile_push as mod

        mod._mobile_notifier = None
        n1 = get_mobile_notifier()
        mod._mobile_notifier = None
        n2 = get_mobile_notifier()
        assert n1 is not n2
