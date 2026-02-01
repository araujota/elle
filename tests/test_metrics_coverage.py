"""Coverage tests for elle.daemon.observability.metrics.

Targets:
- ObservabilityMetrics model creation with all fields
- format_prometheus with labels, MTTR, severity, domain metrics
- collect_observability_metrics with mocked subsystems
- _get_incident_statistics, _get_event_statistics, etc.
- _get_cloud_queue_stats with RuntimeError and Exception paths
- _get_reactive_statistics
- _get_efficacy_statistics
- _get_system_health
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.observability.metrics import (
    ObservabilityMetrics,
    _get_cloud_queue_stats,
    _get_reactive_statistics,
    _get_system_health,
    collect_observability_metrics,
    format_prometheus,
)


# =============================================================================
# ObservabilityMetrics model tests
# =============================================================================


class TestObservabilityMetricsModel:
    """Tests for the ObservabilityMetrics Pydantic model."""

    def test_default_values(self):
        """Model should have sensible defaults."""
        m = ObservabilityMetrics()
        assert m.total_events_1h == 0
        assert m.total_events_24h == 0
        assert m.total_incidents == 0
        assert m.open_incidents == 0
        assert m.mttr_overall_sec is None
        assert m.telemetryd_healthy is True
        assert m.cloud_healthy is True
        assert m.cloud_queue_pending == 0
        assert m.reactive_functions_enabled == 0

    def test_all_fields_populated(self):
        """Model should accept all fields."""
        m = ObservabilityMetrics(
            total_events_1h=100,
            total_events_24h=2000,
            events_by_severity={"warning": 50, "error": 10},
            events_by_source={"journal": 80, "kernel": 20},
            total_incidents=42,
            open_incidents=3,
            incidents_by_domain={"service": 20, "network": 12},
            incidents_by_outcome={"improved": 30, "no_change": 5},
            mttr_overall_sec=120.5,
            mttr_by_domain={"service": 60.0, "network": 180.0},
            capability_success_rates={"service.restart": 0.95},
            capability_executions_1h=15,
            active_warnings=2,
            active_criticals=1,
            forecasts_by_metric={"disk_usage": {"urgency": "prepare"}},
            telemetryd_healthy=True,
            daemon_uptime_sec=86400.0,
            last_event_age_sec=5.0,
            reactive_functions_enabled=3,
            reactive_executions_1h=7,
            cloud_queue_pending=2,
            cloud_retries_total=10,
            cloud_success_total=8,
            cloud_failed_permanent=1,
            cloud_healthy=False,
        )
        assert m.total_events_1h == 100
        assert m.events_by_severity["warning"] == 50
        assert m.mttr_overall_sec == 120.5
        assert m.cloud_healthy is False


# =============================================================================
# format_prometheus tests
# =============================================================================


class TestFormatPrometheus:
    """Tests for Prometheus format output."""

    def test_basic_metrics_output(self):
        """Should produce valid Prometheus format with HELP, TYPE, and values."""
        m = ObservabilityMetrics(
            total_events_1h=10,
            total_events_24h=200,
            total_incidents=5,
            open_incidents=1,
        )
        output = format_prometheus(m)

        assert "# HELP elle_events_total_1h" in output
        assert "# TYPE elle_events_total_1h gauge" in output
        assert "elle_events_total_1h 10" in output
        assert "elle_events_total_24h 200" in output
        assert "elle_incidents_total 5" in output
        assert "# TYPE elle_incidents_total counter" in output
        assert "elle_incidents_open 1" in output

    def test_metrics_with_labels(self):
        """Should produce label-formatted metrics for severity and domain."""
        m = ObservabilityMetrics(
            events_by_severity={"warning": 50, "error": 10},
            incidents_by_domain={"service": 20},
            incidents_by_outcome={"improved": 15},
            capability_success_rates={"service.restart": 0.95},
            mttr_by_domain={"service": 60.0},
        )
        output = format_prometheus(m)

        assert 'severity="warning"' in output
        assert 'severity="error"' in output
        assert 'domain="service"' in output
        assert 'outcome="improved"' in output
        assert 'capability="service.restart"' in output

    def test_mttr_included_when_present(self):
        """MTTR metrics should appear when mttr_overall_sec is not None."""
        m = ObservabilityMetrics(mttr_overall_sec=120.5)
        output = format_prometheus(m)
        assert "elle_mttr_seconds 120.5" in output

    def test_mttr_absent_when_none(self):
        """MTTR metric should not appear when mttr_overall_sec is None."""
        m = ObservabilityMetrics(mttr_overall_sec=None)
        output = format_prometheus(m)
        assert "elle_mttr_seconds" not in output

    def test_cloud_metrics(self):
        """Cloud queue metrics should be present."""
        m = ObservabilityMetrics(
            cloud_queue_pending=5,
            cloud_retries_total=20,
            cloud_success_total=15,
            cloud_failed_permanent=3,
            cloud_healthy=False,
        )
        output = format_prometheus(m)

        assert "elle_cloud_queue_pending 5" in output
        assert "elle_cloud_retries_total 20" in output
        assert "elle_cloud_success_total 15" in output
        assert "elle_cloud_failed_permanent 3" in output
        assert "elle_cloud_healthy 0" in output  # False -> 0

    def test_boolean_metrics(self):
        """Boolean metrics should be 0 or 1."""
        m_healthy = ObservabilityMetrics(telemetryd_healthy=True, cloud_healthy=True)
        output_healthy = format_prometheus(m_healthy)
        assert "elle_telemetryd_healthy 1" in output_healthy
        assert "elle_cloud_healthy 1" in output_healthy

        m_unhealthy = ObservabilityMetrics(telemetryd_healthy=False, cloud_healthy=False)
        output_unhealthy = format_prometheus(m_unhealthy)
        assert "elle_telemetryd_healthy 0" in output_unhealthy
        assert "elle_cloud_healthy 0" in output_unhealthy

    def test_reactive_metrics(self):
        """Reactive function metrics should be present."""
        m = ObservabilityMetrics(
            reactive_functions_enabled=5,
            reactive_executions_1h=12,
        )
        output = format_prometheus(m)
        assert "elle_reactive_functions_enabled 5" in output
        assert "elle_reactive_executions_1h 12" in output

    def test_forecast_metrics(self):
        """Forecast metrics should be present."""
        m = ObservabilityMetrics(active_warnings=2, active_criticals=1)
        output = format_prometheus(m)
        assert "elle_forecasts_active_warnings 2" in output
        assert "elle_forecasts_active_criticals 1" in output

    def test_system_health_metrics(self):
        """System health metrics should be present."""
        m = ObservabilityMetrics(
            daemon_uptime_sec=3600.0,
            last_event_age_sec=2.5,
        )
        output = format_prometheus(m)
        assert "elle_daemon_uptime_seconds 3600.0" in output
        assert "elle_last_event_age_seconds 2.5" in output

    def test_output_ends_with_newline(self):
        """Prometheus output should end with a newline."""
        m = ObservabilityMetrics()
        output = format_prometheus(m)
        assert output.endswith("\n")


# =============================================================================
# collect_observability_metrics tests
# =============================================================================


class TestCollectObservabilityMetrics:
    """Tests for the async metrics collection function."""

    @pytest.mark.asyncio
    async def test_collect_handles_all_subsystem_failures(self):
        """Should return defaults when all subsystem collection fails."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                side_effect=Exception("DB error"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                side_effect=Exception("DB error"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                side_effect=Exception("DB error"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                side_effect=Exception("DB error"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                side_effect=Exception("DB error"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                side_effect=Exception("DB error"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_cloud_queue_stats",
                side_effect=Exception("error"),
            ),
        ):
            metrics = await collect_observability_metrics()

        assert metrics.total_events_1h == 0
        assert metrics.total_incidents == 0
        assert metrics.mttr_overall_sec is None

    @pytest.mark.asyncio
    async def test_collect_merges_subsystem_data(self):
        """Should merge successful subsystem data."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                return_value={"total_incidents": 42, "open_incidents": 3},
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                return_value={"total_events_1h": 100},
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                return_value={"capability_executions_1h": 5},
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                return_value={"active_warnings": 1},
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                return_value={"reactive_functions_enabled": 2},
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                return_value={"telemetryd_healthy": True},
            ),
            patch(
                "elle.daemon.observability.metrics._get_cloud_queue_stats",
                return_value={"cloud_queue_pending": 1},
            ),
        ):
            metrics = await collect_observability_metrics()

        assert metrics.total_incidents == 42
        assert metrics.total_events_1h == 100
        assert metrics.active_warnings == 1
        assert metrics.reactive_functions_enabled == 2
        assert metrics.cloud_queue_pending == 1


# =============================================================================
# _get_cloud_queue_stats tests
# =============================================================================


class TestGetCloudQueueStats:
    """Tests for _get_cloud_queue_stats error paths."""

    def test_runtime_error_from_queue(self):
        """RuntimeError from get_cloud_queue should be caught silently (line 466-468)."""
        mock_worker = MagicMock()
        mock_worker.stats.cloud_healthy = True

        with (
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                side_effect=RuntimeError("Queue not configured"),
            ),
            patch(
                "elle.daemon.incidents.cloud_retry_worker.get_cloud_retry_worker",
                return_value=mock_worker,
            ),
        ):
            stats = _get_cloud_queue_stats()

        assert stats["cloud_queue_pending"] == 0
        assert stats["cloud_healthy"] is True

    def test_general_exception_from_queue(self):
        """General exception from get_cloud_queue should be caught (line 469-470)."""
        mock_queue = MagicMock()
        mock_queue.get_metrics.side_effect = ValueError("bad data")

        with (
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                return_value=mock_queue,
            ),
            patch(
                "elle.daemon.incidents.cloud_retry_worker.get_cloud_retry_worker",
                side_effect=Exception("worker error"),
            ),
        ):
            stats = _get_cloud_queue_stats()

        assert stats["cloud_queue_pending"] == 0

    def test_successful_cloud_stats(self):
        """Should populate all cloud stats on success."""
        mock_metrics = MagicMock()
        mock_metrics.pending_count = 3
        mock_metrics.total_retries = 10
        mock_metrics.succeeded_count = 7
        mock_metrics.failed_permanent_count = 2

        mock_queue = MagicMock()
        mock_queue.get_metrics.return_value = mock_metrics

        mock_worker = MagicMock()
        mock_worker.stats.cloud_healthy = False

        with (
            patch(
                "elle.daemon.incidents.cloud_queue.get_cloud_queue",
                return_value=mock_queue,
            ),
            patch(
                "elle.daemon.incidents.cloud_retry_worker.get_cloud_retry_worker",
                return_value=mock_worker,
            ),
        ):
            stats = _get_cloud_queue_stats()

        assert stats["cloud_queue_pending"] == 3
        assert stats["cloud_retries_total"] == 10
        assert stats["cloud_success_total"] == 7
        assert stats["cloud_failed_permanent"] == 2
        assert stats["cloud_healthy"] is False


# =============================================================================
# _get_reactive_statistics tests
# =============================================================================


class TestGetReactiveStatistics:
    """Tests for _get_reactive_statistics."""

    @pytest.mark.asyncio
    async def test_reactive_stats_success(self):
        """Should populate reactive stats on success."""
        with (
            patch("elle.reactive.store.get_function_count", return_value=5),
            patch("elle.reactive.store.get_execution_count", return_value=12),
        ):
            stats = await _get_reactive_statistics()

        assert stats["reactive_functions_enabled"] == 5
        assert stats["reactive_executions_1h"] == 12

    @pytest.mark.asyncio
    async def test_reactive_stats_import_error(self):
        """Should handle import errors gracefully."""
        with patch(
            "elle.reactive.store.get_function_count",
            side_effect=Exception("import failed"),
        ):
            stats = await _get_reactive_statistics()

        assert stats["reactive_functions_enabled"] == 0
        assert stats["reactive_executions_1h"] == 0


# =============================================================================
# _get_system_health tests
# =============================================================================


class TestGetSystemHealth:
    """Tests for _get_system_health."""

    @pytest.mark.asyncio
    async def test_system_health_returns_defaults(self):
        """Should return default health stats."""
        stats = await _get_system_health()

        assert stats["telemetryd_healthy"] is True
        assert stats["daemon_uptime_sec"] == 0.0
        assert stats["last_event_age_sec"] == 0.0
