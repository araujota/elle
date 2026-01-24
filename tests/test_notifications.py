"""Tests for the notification module."""

import pytest

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


class TestNotificationModel:
    """Tests for Notification model."""

    def test_create_simple_notification(self):
        """Test creating a simple notification."""
        n = Notification(
            title="Test",
            body="Test body",
        )
        assert n.title == "Test"
        assert n.body == "Test body"
        assert n.category == NotificationCategory.INFO
        assert n.timeout_ms == 5000

    def test_notification_with_category(self):
        """Test notification with category."""
        n = Notification(
            title="Job Done",
            body="Your task completed",
            category=NotificationCategory.JOB_COMPLETE,
        )
        assert n.category == NotificationCategory.JOB_COMPLETE
        assert n.get_urgency() == NotificationUrgency.NORMAL
        assert n.get_icon() == "emblem-ok-symbolic"

    def test_notification_urgency_override(self):
        """Test urgency override."""
        n = Notification(
            title="Warning",
            body="Something happened",
            category=NotificationCategory.INFO,
            urgency=NotificationUrgency.CRITICAL,
        )
        assert n.get_urgency() == NotificationUrgency.CRITICAL

    def test_notification_icon_override(self):
        """Test icon override."""
        n = Notification(
            title="Custom",
            body="Custom icon",
            icon="custom-icon",
        )
        assert n.get_icon() == "custom-icon"

    def test_notification_with_actions(self):
        """Test notification with actions."""
        n = Notification(
            title="Action",
            body="Click me",
            actions=(
                NotificationAction(id="open", label="Open"),
                NotificationAction(id="dismiss", label="Dismiss"),
            ),
            default_action="open",
        )
        assert len(n.actions) == 2
        assert n.actions[0].id == "open"
        assert n.default_action == "open"

    def test_notification_with_context(self):
        """Test notification with context."""
        n = Notification(
            title="Incident",
            body="New incident",
            context_type="incident",
            context_id="abc-123",
        )
        assert n.context_type == "incident"
        assert n.context_id == "abc-123"


class TestNotificationAction:
    """Tests for NotificationAction model."""

    def test_create_action(self):
        """Test creating an action."""
        a = NotificationAction(id="test", label="Test Action")
        assert a.id == "test"
        assert a.label == "Test Action"
        assert a.command is None

    def test_action_with_command(self):
        """Test action with custom command."""
        a = NotificationAction(
            id="custom",
            label="Custom",
            command="echo hello",
        )
        assert a.command == "echo hello"


class TestNotificationResult:
    """Tests for NotificationResult model."""

    def test_success_result(self):
        """Test success result."""
        r = NotificationResult(
            success=True,
            notification_id=123,
            method="libnotify",
        )
        assert r.success is True
        assert r.notification_id == 123
        assert r.error is None

    def test_failure_result(self):
        """Test failure result."""
        r = NotificationResult(
            success=False,
            error="Not available",
            method="notify-send",
        )
        assert r.success is False
        assert r.error == "Not available"


class TestNotificationTemplates:
    """Tests for notification template functions."""

    def test_job_complete_success(self):
        """Test job complete notification (success)."""
        n = job_complete_notification("Backup", success=True)
        assert "Backup" in n.body
        assert "completed successfully" in n.body
        assert n.category == NotificationCategory.JOB_COMPLETE

    def test_job_complete_failure(self):
        """Test job complete notification (failure)."""
        n = job_complete_notification("Download", success=False, details="Connection refused")
        assert "Download" in n.body
        assert "failed" in n.body
        assert "Connection refused" in n.body
        assert n.category == NotificationCategory.JOB_FAILED

    def test_incident_notification(self):
        """Test incident notification."""
        n = incident_notification(
            title="High CPU usage",
            severity="warning",
            domain="cpu",
            incident_id="inc-123",
            summary="CPU at 95%",
        )
        assert "High CPU usage" in n.body
        assert n.context_id == "inc-123"
        assert n.context_type == "incident"
        assert n.category == NotificationCategory.INCIDENT_DETECTED

    def test_incident_critical(self):
        """Test critical incident notification."""
        n = incident_notification(
            title="Disk failure",
            severity="critical",
            domain="disk",
            incident_id="inc-456",
        )
        assert n.category == NotificationCategory.INCIDENT_ESCALATED
        assert n.get_urgency() == NotificationUrgency.CRITICAL
        assert n.timeout_ms == 0  # Doesn't auto-dismiss

    def test_reboot_required(self):
        """Test reboot required notification."""
        n = reboot_notification(
            status="required",
            goal="Install kernel updates",
            intent_id="rbt-123",
        )
        assert "Reboot Required" in n.title
        assert "kernel updates" in n.body
        assert n.category == NotificationCategory.REBOOT_REQUIRED

    def test_reboot_success(self):
        """Test reboot success notification."""
        n = reboot_notification(
            status="success",
            goal="Update completed",
            intent_id="rbt-456",
        )
        assert "Successful" in n.title
        assert n.category == NotificationCategory.REBOOT_SUCCESS

    def test_reboot_failed(self):
        """Test reboot failed notification."""
        n = reboot_notification(
            status="failed",
            goal="Kernel update",
            intent_id="rbt-789",
            details="Critical services down",
        )
        assert "Failed" in n.title
        assert "Critical services" in n.body
        assert n.category == NotificationCategory.REBOOT_FAILED
        assert n.timeout_ms == 0

    def test_reboot_rollback(self):
        """Test reboot rollback notification."""
        n = reboot_notification(
            status="rollback",
            goal="Driver update",
            intent_id="rbt-000",
            details="Network interface not responding",
        )
        assert "Rollback" in n.title
        assert n.category == NotificationCategory.REBOOT_ROLLBACK

    def test_health_warning(self):
        """Test health warning notification."""
        n = health_notification(
            status="warning",
            component="Memory",
            message="Memory usage at 85%",
            severity="warning",
        )
        assert "Memory" in n.title
        assert "85%" in n.body
        assert n.category == NotificationCategory.HEALTH_WARNING

    def test_health_critical(self):
        """Test health critical notification."""
        n = health_notification(
            status="critical",
            component="Disk",
            message="Root filesystem full",
            severity="critical",
        )
        assert n.category == NotificationCategory.HEALTH_CRITICAL
        assert n.get_urgency() == NotificationUrgency.CRITICAL


class TestCategoryMappings:
    """Tests for category mappings."""

    def test_all_categories_have_urgency(self):
        """Test all categories have default urgency."""
        for category in NotificationCategory:
            assert category in CATEGORY_URGENCY

    def test_all_categories_have_icon(self):
        """Test all categories have default icon."""
        for category in NotificationCategory:
            assert category in CATEGORY_ICONS


class TestNotificationUrgency:
    """Tests for urgency levels."""

    def test_urgency_values(self):
        """Test urgency enum values."""
        assert NotificationUrgency.LOW.value == "low"
        assert NotificationUrgency.NORMAL.value == "normal"
        assert NotificationUrgency.CRITICAL.value == "critical"


class TestNotificationCategory:
    """Tests for notification categories."""

    def test_job_categories(self):
        """Test job-related categories."""
        assert NotificationCategory.JOB_COMPLETE.value == "job_complete"
        assert NotificationCategory.JOB_FAILED.value == "job_failed"

    def test_incident_categories(self):
        """Test incident-related categories."""
        assert NotificationCategory.INCIDENT_DETECTED.value == "incident_detected"
        assert NotificationCategory.INCIDENT_RESOLVED.value == "incident_resolved"
        assert NotificationCategory.INCIDENT_ESCALATED.value == "incident_escalated"

    def test_reboot_categories(self):
        """Test reboot-related categories."""
        assert NotificationCategory.REBOOT_REQUIRED.value == "reboot_required"
        assert NotificationCategory.REBOOT_SUCCESS.value == "reboot_success"
        assert NotificationCategory.REBOOT_FAILED.value == "reboot_failed"
        assert NotificationCategory.REBOOT_ROLLBACK.value == "reboot_rollback"

    def test_health_categories(self):
        """Test health-related categories."""
        assert NotificationCategory.HEALTH_WARNING.value == "health_warning"
        assert NotificationCategory.HEALTH_CRITICAL.value == "health_critical"
        assert NotificationCategory.HEALTH_RECOVERED.value == "health_recovered"
