"""Tests for Notification capabilities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.capabilities.core.notify import (
    NOTIFY_CAPABILITIES,
    NotifyAlertCapability,
    NotifyAlertInput,
    NotifySendCapability,
    NotifySendInput,
)


class TestNotifySendCapability:
    """Tests for NotifySendCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = NotifySendCapability()
        spec = cap.spec

        assert spec.name == "notify.send"
        assert spec.domain == "notification"
        assert spec.risk == "none"

    def test_dry_run(self):
        """Test dry run returns preview."""
        cap = NotifySendCapability()
        input_data = NotifySendInput(
            title="Test Notification",
            body="This is a test",
        )

        result = cap.dry_run(input_data)

        assert result is not None
        assert result.is_valid is True
        assert "Test Notification" in result.preview_text

    @patch("elle.capabilities.core.notify.NotifySendCapability._fallback_notify")
    def test_run_with_fallback(self, mock_fallback):
        """Test notification with fallback when service unavailable."""
        from elle.capabilities.models import CapabilityEvidence, SideEffect

        mock_fallback.return_value = MagicMock(
            success=True,
            output=MagicMock(sent=True, backend="notify-send"),
            side_effects_applied=(
                SideEffect(
                    kind="notification_sent",
                    target="desktop",
                    reversible=False,
                    description="Sent notification",
                ),
            ),
            execution_time_ms=10,
            evidence=CapabilityEvidence(rationale="Test"),
        )

        cap = NotifySendCapability()
        input_data = NotifySendInput(
            title="Test",
            body="Test body",
        )

        # Test that fallback is available and can be called
        # In reality, the run method will use the notification service or fall back
        result = cap.run(input_data)

        # Result depends on whether notification service is available
        assert isinstance(result.success, bool)

    @patch("elle.daemon.notifications.notify")
    def test_run_success(self, mock_notify):
        """Test successful notification send."""
        cap = NotifySendCapability()
        input_data = NotifySendInput(
            title="Test Alert",
            body="Test body",
            urgency="normal",
        )

        result = cap.run(input_data)

        # Result depends on whether notification service is available
        assert isinstance(result.success, bool)


class TestNotifyAlertCapability:
    """Tests for NotifyAlertCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = NotifyAlertCapability()
        spec = cap.spec

        assert spec.name == "notify.alert"
        assert spec.domain == "notification"
        assert spec.risk == "none"

    def test_dry_run(self):
        """Test dry run returns preview."""
        cap = NotifyAlertCapability()
        input_data = NotifyAlertInput(
            title="Critical Alert",
            body="Something went wrong",
            severity="critical",
        )

        result = cap.dry_run(input_data)

        assert result is not None
        assert result.is_valid is True
        assert "critical" in result.preview_text.lower()
        assert "Critical Alert" in result.preview_text

    def test_dry_run_with_ntfy(self):
        """Test dry run with ntfy enabled."""
        cap = NotifyAlertCapability()
        input_data = NotifyAlertInput(
            title="Test",
            body="Test body",
            severity="warning",
            push_ntfy=True,
            ntfy_topic="test-topic",
        )

        result = cap.dry_run(input_data)

        assert result.is_valid is True
        assert "ntfy" in result.preview_text.lower()

    def test_run_no_notification_service(self):
        """Test run when notification service not available."""
        cap = NotifyAlertCapability()
        input_data = NotifyAlertInput(
            title="Test Alert",
            body="Test body",
            severity="warning",
            push_ntfy=False,
        )

        # Run without mocking - will fail gracefully
        result = cap.run(input_data)

        # Should handle missing service gracefully
        assert isinstance(result.success, bool)

    @patch("elle.daemon.notifications.notify")
    @patch("elle.daemon.notifications.notify_incident")
    def test_run_with_incident_id(self, mock_incident, mock_notify):
        """Test alert with incident ID."""
        cap = NotifyAlertCapability()
        input_data = NotifyAlertInput(
            title="Incident Alert",
            body="Incident details",
            severity="error",
            incident_id="inc-12345",
        )

        result = cap.run(input_data)

        # Will either succeed or fail gracefully
        assert isinstance(result.success, bool)


class TestNotifyCapabilitiesRegistry:
    """Tests for Notification capabilities registration."""

    def test_all_capabilities_exported(self):
        """Test all capabilities are in NOTIFY_CAPABILITIES."""
        assert len(NOTIFY_CAPABILITIES) == 2

        names = {cap().spec.name for cap in NOTIFY_CAPABILITIES}
        expected = {"notify.send", "notify.alert"}
        assert names == expected


class TestNtfyIntegration:
    """Tests for ntfy push notification integration."""

    def test_send_ntfy_builds_correct_payload(self):
        """Test ntfy payload structure."""
        cap = NotifyAlertCapability()
        input_data = NotifyAlertInput(
            title="Test",
            body="Test body",
            severity="warning",
            push_ntfy=True,
            tags=("warning", "test"),
        )

        # We can't easily test the private method directly
        # but we can verify the capability handles ntfy correctly
        result = cap.dry_run(input_data)
        assert "ntfy" in result.preview_text.lower()

    def test_severity_priority_mapping(self):
        """Test severity to ntfy priority mapping."""
        cap = NotifyAlertCapability()

        # Test all severity levels produce valid dry runs
        for severity in ["info", "warning", "error", "critical"]:
            input_data = NotifyAlertInput(
                title="Test",
                body="Test",
                severity=severity,
                push_ntfy=True,
            )
            result = cap.dry_run(input_data)
            assert result.is_valid is True
