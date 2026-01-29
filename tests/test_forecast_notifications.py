"""Tests for the forecast notification system."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock structlog before importing incident modules (needed by retriever.py)
if "structlog" not in sys.modules:
    sys.modules["structlog"] = MagicMock()

from elle.daemon.notifications.formatters import (
    format_forecast_body,
    format_forecast_title,
)
from elle.daemon.notifications.models import (
    NotificationCategory,
    NotificationUrgency,
    forecast_notification,
)

# =============================================================================
# Notification Template Tests
# =============================================================================


class TestForecastNotificationTemplate:
    """Tests for forecast_notification() template function."""

    def test_forecast_warning_notification(self):
        """Test forecast warning notification with plan."""
        n = forecast_notification(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            incident_id="FORECAST-ABC123",
            plan_summary="Clean old logs and temp files",
            cause_description="Log rotation not running",
        )

        assert n.category == NotificationCategory.FORECAST_WARNING
        assert n.context_id == "FORECAST-ABC123"
        assert n.context_type == "incident"
        assert n.timeout_ms == 0  # Don't auto-dismiss warnings

        # Should have Execute Now, Investigate, Dismiss actions
        action_ids = [a.id for a in n.actions]
        assert "execute_plan" in action_ids
        assert "investigate" in action_ids
        assert "dismiss" in action_ids

    def test_forecast_remediated_notification(self):
        """Test post-remediation notification."""
        n = forecast_notification(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            incident_id="FORECAST-ABC123",
            plan_summary="Clean old logs and temp files",
            auto_remediated=True,
            outcome="improved",
        )

        assert n.category == NotificationCategory.FORECAST_REMEDIATED
        assert n.context_id == "FORECAST-ABC123"
        assert n.timeout_ms == 8000  # Auto-dismiss after 8s

        # Should have View Details and Dismiss only
        action_ids = [a.id for a in n.actions]
        assert "view" in action_ids
        assert "dismiss" in action_ids
        assert "execute_plan" not in action_ids

    def test_forecast_warning_urgency(self):
        """Test forecast warning uses NORMAL urgency."""
        n = forecast_notification(
            metric="mem.used_pct",
            current_value=0.80,
            threshold=0.90,
            time_to_threshold_hours=2.0,
            incident_id="FORECAST-DEF456",
        )
        assert n.get_urgency() == NotificationUrgency.NORMAL

    def test_forecast_remediated_urgency(self):
        """Test forecast remediated uses LOW urgency."""
        n = forecast_notification(
            metric="mem.used_pct",
            current_value=0.80,
            threshold=0.90,
            time_to_threshold_hours=2.0,
            incident_id="FORECAST-DEF456",
            auto_remediated=True,
            outcome="improved",
        )
        assert n.get_urgency() == NotificationUrgency.LOW

    def test_forecast_warning_icon(self):
        """Test forecast warning uses warning icon."""
        n = forecast_notification(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            incident_id="FORECAST-ABC123",
        )
        assert n.get_icon() == "dialog-warning-symbolic"

    def test_forecast_remediated_icon(self):
        """Test forecast remediated uses ok icon."""
        n = forecast_notification(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            incident_id="FORECAST-ABC123",
            auto_remediated=True,
            outcome="improved",
        )
        assert n.get_icon() == "emblem-ok-symbolic"


# =============================================================================
# Formatter Tests
# =============================================================================


class TestFormatForecastTitle:
    """Tests for format_forecast_title()."""

    def test_disk_metric_warning(self):
        """Test title for disk metric warning."""
        title = format_forecast_title("disk./.used_pct")
        assert "Disk /" in title
        assert "Warning" in title

    def test_memory_metric_warning(self):
        """Test title for memory metric warning."""
        title = format_forecast_title("mem.used_pct")
        assert "Memory" in title
        assert "Warning" in title

    def test_cpu_metric_warning(self):
        """Test title for CPU metric."""
        title = format_forecast_title("cpu.load_avg")
        assert "CPU" in title

    def test_auto_remediated_title(self):
        """Test title for auto-remediated forecast."""
        title = format_forecast_title("disk./.used_pct", auto_remediated=True)
        assert "Auto-Remediated" in title
        assert "Disk /" in title

    def test_network_metric(self):
        """Test title for network metric."""
        title = format_forecast_title("net.eth0.errors")
        assert "Network" in title

    def test_unknown_metric(self):
        """Test title for unknown metric falls back to raw name."""
        title = format_forecast_title("custom.metric.value")
        assert "custom.metric.value" in title


class TestFormatForecastBody:
    """Tests for format_forecast_body()."""

    def test_percentage_metric_body(self):
        """Test body formatting for percentage metrics."""
        body = format_forecast_body(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
        )
        assert "85%" in body
        assert "90%" in body
        assert "4.5 hours" in body

    def test_body_with_cause(self):
        """Test body includes cause description."""
        body = format_forecast_body(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            cause_description="Log rotation not running",
        )
        assert "Cause: Log rotation not running" in body

    def test_body_with_plan_summary(self):
        """Test body includes plan summary."""
        body = format_forecast_body(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            plan_summary="Clean old logs",
        )
        assert "Plan: Clean old logs" in body

    def test_body_with_outcome(self):
        """Test body includes outcome instead of plan when remediated."""
        body = format_forecast_body(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            plan_summary="Clean old logs",
            outcome="improved",
        )
        assert "Result: improved" in body
        # Outcome replaces plan summary
        assert "Plan:" not in body

    def test_body_short_time_uses_minutes(self):
        """Test body uses minutes for short timeframes."""
        body = format_forecast_body(
            metric="mem.used_pct",
            current_value=0.88,
            threshold=0.90,
            time_to_threshold_hours=0.5,
        )
        assert "30 minutes" in body

    def test_body_no_time(self):
        """Test body handles None time_to_threshold."""
        body = format_forecast_body(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=None,
        )
        assert "Projected" not in body

    def test_body_truncates_long_plan_summary(self):
        """Test body truncates long plan summaries."""
        long_summary = "x" * 100
        body = format_forecast_body(
            metric="disk./.used_pct",
            current_value=0.85,
            threshold=0.90,
            time_to_threshold_hours=4.5,
            plan_summary=long_summary,
        )
        assert "..." in body

    def test_non_percentage_metric(self):
        """Test body formatting for non-percentage metrics."""
        body = format_forecast_body(
            metric="cpu.load_avg",
            current_value=8.5,
            threshold=12.0,
            time_to_threshold_hours=2.0,
        )
        assert "8.5" in body
        assert "12.0" in body


# =============================================================================
# Autonomy Check Tests
# =============================================================================


class TestCheckPlanAutonomy:
    """Tests for _check_plan_autonomy()."""

    def test_all_capabilities_allowed(self):
        """Test that all allowed capabilities returns True."""
        from elle.daemon.incidents.forecast_handler import _check_plan_autonomy

        with (
            patch("elle.capabilities.autonomy.get_autonomy_engine") as mock_engine_fn,
            patch("elle.capabilities.registry.get_registry") as mock_registry_fn,
        ):
            mock_engine = MagicMock()
            mock_engine.can_run_autonomously.return_value = (True, "base_level")
            mock_engine_fn.return_value = mock_engine

            mock_registry = MagicMock()
            mock_spec = MagicMock()
            mock_spec.risk = "low"
            mock_registry.get.return_value = mock_spec
            mock_registry_fn.return_value = mock_registry

            result = _check_plan_autonomy(("file.read", "service.status"))
            assert result is True

    def test_one_capability_blocked(self):
        """Test that one blocked capability returns False."""
        from elle.daemon.incidents.forecast_handler import _check_plan_autonomy

        with (
            patch("elle.capabilities.autonomy.get_autonomy_engine") as mock_engine_fn,
            patch("elle.capabilities.registry.get_registry") as mock_registry_fn,
        ):
            mock_engine = MagicMock()
            # First capability allowed, second blocked
            mock_engine.can_run_autonomously.side_effect = [
                (True, "base_level"),
                (False, "risk_blocked"),
            ]
            mock_engine_fn.return_value = mock_engine

            mock_registry = MagicMock()
            mock_spec = MagicMock()
            mock_spec.risk = "low"
            mock_registry.get.return_value = mock_spec
            mock_registry_fn.return_value = mock_registry

            result = _check_plan_autonomy(("file.read", "service.restart"))
            assert result is False

    def test_empty_capabilities_returns_false(self):
        """Test that empty capabilities returns False."""
        from elle.daemon.incidents.forecast_handler import _check_plan_autonomy

        result = _check_plan_autonomy(())
        assert result is False

    def test_unknown_capability_returns_false(self):
        """Test that unknown capability returns False."""
        from elle.daemon.incidents.forecast_handler import _check_plan_autonomy

        with (
            patch("elle.capabilities.autonomy.get_autonomy_engine"),
            patch("elle.capabilities.registry.get_registry") as mock_registry_fn,
        ):
            mock_registry = MagicMock()
            mock_registry.get.return_value = None  # Unknown
            mock_registry_fn.return_value = mock_registry

            result = _check_plan_autonomy(("unknown.capability",))
            assert result is False


# =============================================================================
# Cause Description Extraction Tests
# =============================================================================


class TestExtractCauseDescription:
    """Tests for _extract_cause_description()."""

    def test_explicit_cause_label(self):
        """Test extracting 'Cause:' labeled text."""
        from elle.daemon.incidents.forecast_handler import _extract_cause_description

        response = "Analysis complete.\nCause: Log rotation is not configured properly.\nSteps:"
        result = _extract_cause_description(response)
        assert result is not None
        assert "Log rotation" in result

    def test_root_cause_label(self):
        """Test extracting 'Root cause:' labeled text."""
        from elle.daemon.incidents.forecast_handler import _extract_cause_description

        response = "Root cause: Large database dump files accumulating in /var/tmp"
        result = _extract_cause_description(response)
        assert result is not None
        assert "database dump" in result

    def test_due_to_pattern(self):
        """Test extracting 'due to' pattern."""
        from elle.daemon.incidents.forecast_handler import _extract_cause_description

        response = "This is likely due to failed log rotation cron jobs"
        result = _extract_cause_description(response)
        assert result is not None
        assert "log rotation" in result

    def test_no_cause_returns_none(self):
        """Test returns None when no cause found."""
        from elle.daemon.incidents.forecast_handler import _extract_cause_description

        response = "Here is the remediation plan:\n1. Clean up old files\n2. Configure rotation"
        result = _extract_cause_description(response)
        assert result is None


# =============================================================================
# Execute Prepared Plan Tests
# =============================================================================


class TestExecutePreparedPlan:
    """Tests for execute_prepared_plan()."""

    @pytest.mark.asyncio
    async def test_no_prepared_plan(self):
        """Test execute with no prepared plan returns error."""
        from elle.daemon.incidents.forecast_handler import execute_prepared_plan

        with patch("elle.daemon.incidents.store.get_prepared_plan", return_value=None):
            result = await execute_prepared_plan("FORECAST-NONEXIST")

        assert result.success is False
        assert "No prepared plan" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_plan_success(self):
        """Test successful plan execution."""
        from elle.daemon.incidents.forecast_handler import execute_prepared_plan

        plan_data = {
            "plan_id": "test-plan-id",
            "incident_id": "FORECAST-TEST123",
            "metric": "disk./.used_pct",
            "current_value": 0.85,
            "threshold": 0.90,
            "urgency": "prepare",
            "summary": "Clean old logs",
            "steps": ("Remove old log files", "Configure log rotation"),
            "capabilities_to_execute": ("file.delete",),
            "confidence": 0.8,
        }

        mock_loop_result = MagicMock()
        mock_loop_result.success = True
        mock_loop_result.response = "Cleaned up 2GB of old logs"
        mock_loop_result.error = None

        mock_loop = AsyncMock()
        mock_loop.run.return_value = mock_loop_result

        with (
            patch("elle.daemon.incidents.store.get_prepared_plan", return_value=plan_data),
            patch("elle.cli.agentic.loop.AgenticLoop", return_value=mock_loop),
            patch(
                "elle.daemon.incidents.forecast_handler._update_incident_outcome",
                new_callable=AsyncMock,
            ),
            patch("elle.daemon.notifications.service.notify_forecast"),
        ):
            result = await execute_prepared_plan("FORECAST-TEST123")

        assert result.success is True
        assert result.executed is True
        assert result.incident_id == "FORECAST-TEST123"
