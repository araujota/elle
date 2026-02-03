"""Tests for observability metrics collection and Prometheus export."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.observability.metrics import (
    ObservabilityMetrics,
    collect_observability_metrics,
    format_prometheus,
)

# =============================================================================
# ObservabilityMetrics Model Tests
# =============================================================================


class TestObservabilityMetricsModel:
    """Tests for the ObservabilityMetrics Pydantic model."""

    def test_default_construction(self):
        """Test constructing ObservabilityMetrics with all defaults."""
        metrics = ObservabilityMetrics()

        assert metrics.total_events_1h == 0
        assert metrics.total_events_24h == 0
        assert metrics.events_by_severity == {}
        assert metrics.events_by_source == {}
        assert metrics.total_incidents == 0
        assert metrics.open_incidents == 0
        assert metrics.incidents_by_domain == {}
        assert metrics.incidents_by_outcome == {}
        assert metrics.mttr_overall_sec is None
        assert metrics.mttr_by_domain == {}
        assert metrics.capability_success_rates == {}
        assert metrics.capability_executions_1h == 0
        assert metrics.active_warnings == 0
        assert metrics.active_criticals == 0
        assert metrics.forecasts_by_metric == {}
        assert metrics.telemetryd_healthy is True
        assert metrics.daemon_uptime_sec == 0.0
        assert metrics.last_event_age_sec == 0.0
        assert metrics.reactive_functions_enabled == 0
        assert metrics.reactive_executions_1h == 0
        assert isinstance(metrics.collected_at, datetime)

    def test_full_construction(self):
        """Test constructing ObservabilityMetrics with all fields populated."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        metrics = ObservabilityMetrics(
            total_events_1h=150,
            total_events_24h=3200,
            events_by_severity={"error": 10, "warning": 40, "info": 100},
            events_by_source={"journal": 80, "kernel": 50, "ebpf": 20},
            total_incidents=42,
            open_incidents=3,
            incidents_by_domain={"net": 15, "disk": 12, "service": 15},
            incidents_by_outcome={"improved": 30, "no_change": 10, "worse": 2},
            mttr_overall_sec=345.6,
            mttr_by_domain={"net": 120.0, "disk": 400.5},
            capability_success_rates={"service.restart": 0.95, "file.write": 0.88},
            capability_executions_1h=7,
            active_warnings=2,
            active_criticals=1,
            forecasts_by_metric={"disk.used_pct": {"urgency": "prepare"}},
            telemetryd_healthy=False,
            daemon_uptime_sec=86400.0,
            last_event_age_sec=5.2,
            reactive_functions_enabled=4,
            reactive_executions_1h=12,
            collected_at=now,
        )

        assert metrics.total_events_1h == 150
        assert metrics.total_events_24h == 3200
        assert metrics.events_by_severity["error"] == 10
        assert metrics.total_incidents == 42
        assert metrics.open_incidents == 3
        assert metrics.mttr_overall_sec == 345.6
        assert metrics.mttr_by_domain["net"] == 120.0
        assert metrics.capability_success_rates["service.restart"] == 0.95
        assert metrics.capability_executions_1h == 7
        assert metrics.active_warnings == 2
        assert metrics.active_criticals == 1
        assert metrics.telemetryd_healthy is False
        assert metrics.daemon_uptime_sec == 86400.0
        assert metrics.reactive_functions_enabled == 4
        assert metrics.collected_at == now

    def test_model_is_frozen(self):
        """Test that the model is frozen (immutable)."""
        metrics = ObservabilityMetrics()
        with pytest.raises(Exception):
            metrics.total_events_1h = 999

    def test_empty_dict_fields_are_independent(self):
        """Test that default dict fields are independent between instances."""
        m1 = ObservabilityMetrics()
        m2 = ObservabilityMetrics()
        # Each instance should have its own dict, not share a reference
        assert m1.events_by_severity is not m2.events_by_severity
        assert m1.incidents_by_domain is not m2.incidents_by_domain

    def test_large_values(self):
        """Test metrics with very large values."""
        metrics = ObservabilityMetrics(
            total_events_1h=10_000_000,
            total_events_24h=500_000_000,
            total_incidents=1_000_000,
            daemon_uptime_sec=31_536_000.0,  # 1 year in seconds
        )
        assert metrics.total_events_1h == 10_000_000
        assert metrics.total_events_24h == 500_000_000
        assert metrics.daemon_uptime_sec == 31_536_000.0


# =============================================================================
# Prometheus Format Tests
# =============================================================================


class TestFormatPrometheus:
    """Tests for Prometheus exposition format output."""

    @pytest.fixture()
    def empty_metrics(self):
        """Metrics with all default/empty values."""
        return ObservabilityMetrics(
            collected_at=datetime(2025, 1, 15, 12, 0, 0),
        )

    @pytest.fixture()
    def populated_metrics(self):
        """Metrics with representative data populated."""
        return ObservabilityMetrics(
            total_events_1h=100,
            total_events_24h=2400,
            events_by_severity={"error": 5, "warning": 30},
            events_by_source={"journal": 60, "kernel": 40},
            total_incidents=20,
            open_incidents=2,
            incidents_by_domain={"net": 8, "disk": 12},
            incidents_by_outcome={"improved": 15, "no_change": 5},
            mttr_overall_sec=250.5,
            mttr_by_domain={"net": 180.0, "disk": 300.0},
            capability_success_rates={"service.restart": 0.92},
            capability_executions_1h=5,
            active_warnings=1,
            active_criticals=0,
            telemetryd_healthy=True,
            daemon_uptime_sec=3600.0,
            last_event_age_sec=2.5,
            reactive_functions_enabled=3,
            reactive_executions_1h=8,
            collected_at=datetime(2025, 1, 15, 12, 0, 0),
        )

    def test_output_ends_with_newline(self, empty_metrics):
        """Test that Prometheus output ends with a newline."""
        output = format_prometheus(empty_metrics)
        assert output.endswith("\n")

    def test_output_is_string(self, empty_metrics):
        """Test that output is a string."""
        output = format_prometheus(empty_metrics)
        assert isinstance(output, str)

    def test_contains_help_lines(self, empty_metrics):
        """Test that output contains HELP comment lines."""
        output = format_prometheus(empty_metrics)
        assert "# HELP" in output

    def test_contains_type_lines(self, empty_metrics):
        """Test that output contains TYPE comment lines."""
        output = format_prometheus(empty_metrics)
        assert "# TYPE" in output

    def test_metric_name_prefix(self, empty_metrics):
        """Test that all metric names have the elle_ prefix."""
        output = format_prometheus(empty_metrics)
        for line in output.strip().split("\n"):
            if line.startswith("#"):
                # HELP and TYPE lines should reference elle_ prefixed names
                assert "elle_" in line
            elif line.strip():
                # Metric value lines should start with elle_
                assert line.startswith("elle_")

    def test_event_gauge_metrics(self, populated_metrics):
        """Test that event count metrics are present."""
        output = format_prometheus(populated_metrics)
        assert "elle_events_total_1h 100" in output
        assert "elle_events_total_24h 2400" in output

    def test_incident_counter_metric(self, populated_metrics):
        """Test that incident total is formatted as a counter."""
        output = format_prometheus(populated_metrics)
        assert "# TYPE elle_incidents_total counter" in output
        assert "elle_incidents_total 20" in output

    def test_open_incidents_gauge(self, populated_metrics):
        """Test that open incidents is a gauge metric."""
        output = format_prometheus(populated_metrics)
        assert "# TYPE elle_incidents_open gauge" in output
        assert "elle_incidents_open 2" in output

    def test_labeled_severity_metrics(self, populated_metrics):
        """Test metrics with severity labels."""
        output = format_prometheus(populated_metrics)
        assert 'elle_events_by_severity{severity="error"} 5' in output
        assert 'elle_events_by_severity{severity="warning"} 30' in output

    def test_labeled_domain_metrics(self, populated_metrics):
        """Test metrics with domain labels."""
        output = format_prometheus(populated_metrics)
        assert 'elle_incidents_by_domain{domain="net"} 8' in output
        assert 'elle_incidents_by_domain{domain="disk"} 12' in output

    def test_labeled_outcome_metrics(self, populated_metrics):
        """Test metrics with outcome labels."""
        output = format_prometheus(populated_metrics)
        assert 'elle_incidents_by_outcome{outcome="improved"} 15' in output
        assert 'elle_incidents_by_outcome{outcome="no_change"} 5' in output

    def test_mttr_metric(self, populated_metrics):
        """Test MTTR metric is present when set."""
        output = format_prometheus(populated_metrics)
        assert "elle_mttr_seconds 250.5" in output

    def test_mttr_absent_when_none(self, empty_metrics):
        """Test MTTR metric is absent when None."""
        output = format_prometheus(empty_metrics)
        assert "elle_mttr_seconds " not in output

    def test_mttr_by_domain_labels(self, populated_metrics):
        """Test MTTR by domain with labels."""
        output = format_prometheus(populated_metrics)
        assert 'elle_mttr_by_domain_seconds{domain="net"} 180.0' in output
        assert 'elle_mttr_by_domain_seconds{domain="disk"} 300.0' in output

    def test_capability_success_rate_labels(self, populated_metrics):
        """Test capability success rate with labels."""
        output = format_prometheus(populated_metrics)
        assert 'elle_capability_success_rate{capability="service.restart"} 0.92' in output

    def test_capability_executions_metric(self, populated_metrics):
        """Test capability executions count."""
        output = format_prometheus(populated_metrics)
        assert "elle_capability_executions_1h 5" in output

    def test_forecast_metrics(self, populated_metrics):
        """Test forecast warning and critical gauges."""
        output = format_prometheus(populated_metrics)
        assert "elle_forecasts_active_warnings 1" in output
        assert "elle_forecasts_active_criticals 0" in output

    def test_telemetryd_healthy_true(self, populated_metrics):
        """Test telemetryd healthy flag as 1."""
        output = format_prometheus(populated_metrics)
        assert "elle_telemetryd_healthy 1" in output

    def test_telemetryd_healthy_false(self):
        """Test telemetryd healthy flag as 0 when unhealthy."""
        metrics = ObservabilityMetrics(telemetryd_healthy=False)
        output = format_prometheus(metrics)
        assert "elle_telemetryd_healthy 0" in output

    def test_daemon_uptime_metric(self, populated_metrics):
        """Test daemon uptime seconds metric."""
        output = format_prometheus(populated_metrics)
        assert "elle_daemon_uptime_seconds 3600.0" in output

    def test_last_event_age_metric(self, populated_metrics):
        """Test last event age metric."""
        output = format_prometheus(populated_metrics)
        assert "elle_last_event_age_seconds 2.5" in output

    def test_reactive_function_metrics(self, populated_metrics):
        """Test reactive function metrics."""
        output = format_prometheus(populated_metrics)
        assert "elle_reactive_functions_enabled 3" in output
        assert "elle_reactive_executions_1h 8" in output

    def test_empty_dicts_produce_no_labeled_lines(self, empty_metrics):
        """Test that empty dicts do not produce labeled metric lines."""
        output = format_prometheus(empty_metrics)
        # No severity labels should appear
        assert "severity=" not in output
        # No domain labels should appear from incidents_by_domain
        assert 'domain="' not in output
        # No outcome labels should appear
        assert "outcome=" not in output
        # No capability labels should appear
        assert "capability=" not in output

    def test_help_and_type_before_value(self, populated_metrics):
        """Test that HELP and TYPE lines appear before the metric value."""
        output = format_prometheus(populated_metrics)
        lines = output.strip().split("\n")
        # Find a metric and verify ordering
        for i, line in enumerate(lines):
            if line.startswith("elle_events_total_1h "):
                # The two preceding lines should be HELP and TYPE
                assert lines[i - 2].startswith("# HELP elle_events_total_1h")
                assert lines[i - 1].startswith("# TYPE elle_events_total_1h")
                break
        else:
            pytest.fail("Could not find elle_events_total_1h metric line")

    def test_multiple_labels_in_single_metric(self):
        """Test that multiple label entries are correctly formatted."""
        metrics = ObservabilityMetrics(
            events_by_severity={"error": 1, "warning": 2, "info": 3},
        )
        output = format_prometheus(metrics)
        assert 'severity="error"' in output
        assert 'severity="warning"' in output
        assert 'severity="info"' in output


# =============================================================================
# collect_observability_metrics Tests
# =============================================================================


class TestCollectObservabilityMetrics:
    """Tests for the async metrics collection function."""

    @pytest.mark.asyncio
    async def test_returns_observability_metrics(self):
        """Test that collect returns an ObservabilityMetrics instance."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await collect_observability_metrics()
            assert isinstance(result, ObservabilityMetrics)

    @pytest.mark.asyncio
    async def test_aggregates_all_subsystem_stats(self):
        """Test that stats from all subsystems are aggregated."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                new_callable=AsyncMock,
                return_value={"total_incidents": 10, "open_incidents": 2},
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                new_callable=AsyncMock,
                return_value={"total_events_1h": 50},
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                new_callable=AsyncMock,
                return_value={"capability_executions_1h": 3},
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                new_callable=AsyncMock,
                return_value={"active_warnings": 1},
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                new_callable=AsyncMock,
                return_value={"reactive_functions_enabled": 5},
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                new_callable=AsyncMock,
                return_value={"daemon_uptime_sec": 7200.0},
            ),
        ):
            result = await collect_observability_metrics()
            assert result.total_incidents == 10
            assert result.open_incidents == 2
            assert result.total_events_1h == 50
            assert result.capability_executions_1h == 3
            assert result.active_warnings == 1
            assert result.reactive_functions_enabled == 5
            assert result.daemon_uptime_sec == 7200.0

    @pytest.mark.asyncio
    async def test_graceful_on_incident_stats_failure(self):
        """Test that collection continues when incident stats fail."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB unavailable"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                new_callable=AsyncMock,
                return_value={"total_events_1h": 25},
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await collect_observability_metrics()
            # Should still return a result with the event data
            assert isinstance(result, ObservabilityMetrics)
            assert result.total_events_1h == 25

    @pytest.mark.asyncio
    async def test_graceful_on_event_stats_failure(self):
        """Test that collection continues when event stats fail."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                new_callable=AsyncMock,
                return_value={"total_incidents": 5},
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB locked"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await collect_observability_metrics()
            assert isinstance(result, ObservabilityMetrics)
            assert result.total_incidents == 5

    @pytest.mark.asyncio
    async def test_graceful_on_all_stats_failure(self):
        """Test that collection returns defaults when all subsystems fail."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
        ):
            result = await collect_observability_metrics()
            assert isinstance(result, ObservabilityMetrics)
            # Defaults should apply
            assert result.total_events_1h == 0
            assert result.total_incidents == 0

    @pytest.mark.asyncio
    async def test_collected_at_is_set(self):
        """Test that collected_at is populated with current time."""
        with (
            patch(
                "elle.daemon.observability.metrics._get_incident_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_event_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_efficacy_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_forecast_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_reactive_statistics",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "elle.daemon.observability.metrics._get_system_health",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await collect_observability_metrics()
            assert result.collected_at is not None
            assert isinstance(result.collected_at, datetime)


# =============================================================================
# Internal Collector Tests (mocked DB)
# =============================================================================


class TestGetIncidentStatistics:
    """Tests for _get_incident_statistics with mocked DB."""

    @pytest.mark.asyncio
    async def test_returns_incident_counts(self):
        """Test that incident statistics are read from the DB."""
        from elle.daemon.observability.metrics import _get_incident_statistics

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Set up cursor.execute / fetchone / fetchall responses
        # Source accesses rows by column name (dict keys), not position
        mock_cursor.fetchone.side_effect = [
            {"cnt": 42},  # total incidents
            {"cnt": 3},  # open incidents
            {"avg_ttr": 250.0},  # MTTR overall
        ]
        mock_cursor.fetchall.side_effect = [
            [{"domain": "net", "cnt": 20}, {"domain": "disk", "cnt": 22}],  # by domain
            [{"outcome": "improved", "cnt": 30}, {"outcome": "no_change", "cnt": 12}],  # by outcome
            [{"domain": "net", "avg_ttr": 120.0}, {"domain": "disk", "avg_ttr": 300.5}],  # MTTR by domain
        ]

        # The function does `from elle.storage.engine import get_conn`
        # inside the try block, so we patch at the source module.
        with patch("elle.storage.engine.get_conn") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            mock_get.return_value.__exit__.return_value = False
            stats = await _get_incident_statistics()

        assert stats["total_incidents"] == 42
        assert stats["open_incidents"] == 3
        assert stats["incidents_by_domain"] == {"net": 20, "disk": 22}
        assert stats["incidents_by_outcome"] == {"improved": 30, "no_change": 12}
        assert stats["mttr_overall_sec"] == 250.0
        assert stats["mttr_by_domain"] == {"net": 120.0, "disk": 300.5}

    @pytest.mark.asyncio
    async def test_returns_defaults_on_db_error(self):
        """Test that defaults are returned when the DB is unavailable."""
        from elle.daemon.observability.metrics import _get_incident_statistics

        with patch(
            "elle.storage.engine.get_conn",
            side_effect=RuntimeError("DB missing"),
        ):
            stats = await _get_incident_statistics()

        assert stats["total_incidents"] == 0
        assert stats["open_incidents"] == 0
        assert stats["mttr_overall_sec"] is None


class TestGetEventStatistics:
    """Tests for _get_event_statistics with mocked DB."""

    @pytest.mark.asyncio
    async def test_returns_event_counts(self):
        """Test that event statistics are read from the DB."""
        from elle.daemon.observability.metrics import _get_event_statistics

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            {"cnt": 75},  # events 1h
            {"cnt": 1800},  # events 24h
        ]
        mock_cursor.fetchall.side_effect = [
            [{"severity": "error", "cnt": 5}, {"severity": "warning", "cnt": 20}],  # by severity
            [{"source": "journal", "cnt": 50}, {"source": "kernel", "cnt": 25}],  # by source
        ]

        with patch("elle.storage.engine.get_conn") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            mock_get.return_value.__exit__.return_value = False
            stats = await _get_event_statistics()

        assert stats["total_events_1h"] == 75
        assert stats["total_events_24h"] == 1800
        assert stats["events_by_severity"] == {"error": 5, "warning": 20}
        assert stats["events_by_source"] == {"journal": 50, "kernel": 25}

    @pytest.mark.asyncio
    async def test_returns_defaults_on_db_error(self):
        """Test that defaults are returned when the telemetry DB is unavailable."""
        from elle.daemon.observability.metrics import _get_event_statistics

        with patch(
            "elle.storage.engine.get_conn",
            side_effect=RuntimeError("DB missing"),
        ):
            stats = await _get_event_statistics()

        assert stats["total_events_1h"] == 0
        assert stats["total_events_24h"] == 0
        assert stats["events_by_severity"] == {}


class TestGetReactiveStatistics:
    """Tests for _get_reactive_statistics with mocked store."""

    @pytest.mark.asyncio
    async def test_returns_reactive_counts(self):
        """Test that reactive function stats are fetched from store."""
        from elle.daemon.observability.metrics import _get_reactive_statistics

        with (
            patch(
                "elle.reactive.store.get_function_count",
                return_value=6,
            ),
            patch(
                "elle.reactive.store.get_execution_count",
                return_value=15,
            ),
        ):
            stats = await _get_reactive_statistics()

        assert stats["reactive_functions_enabled"] == 6
        assert stats["reactive_executions_1h"] == 15

    @pytest.mark.asyncio
    async def test_returns_defaults_on_import_error(self):
        """Test that defaults are returned when reactive store is unavailable."""
        from elle.daemon.observability.metrics import _get_reactive_statistics

        with patch(
            "elle.reactive.store.get_function_count",
            side_effect=ImportError("module not found"),
        ):
            stats = await _get_reactive_statistics()

        assert stats["reactive_functions_enabled"] == 0
        assert stats["reactive_executions_1h"] == 0


class TestGetSystemHealth:
    """Tests for _get_system_health."""

    @pytest.mark.asyncio
    async def test_returns_default_health(self):
        """Test that system health returns defaults when daemon is available."""
        from unittest.mock import MagicMock, patch

        from elle.daemon.observability.metrics import _get_system_health

        mock_status = MagicMock()
        mock_status.healthy = True
        mock_status.uptime_sec = 42

        mock_daemon = MagicMock()
        mock_daemon.get_status.return_value = mock_status

        with patch.dict(
            "sys.modules",
            {"elle.daemon.api.routes": MagicMock(get_daemon=MagicMock(return_value=mock_daemon))},
        ):
            stats = await _get_system_health()

        assert stats["telemetryd_healthy"] is True
        assert stats["daemon_uptime_sec"] == 42.0
        assert stats["last_event_age_sec"] == -1.0

    @pytest.mark.asyncio
    async def test_returns_fallback_when_daemon_unavailable(self):
        """Test that system health returns fallback defaults when daemon is unavailable."""
        from unittest.mock import patch

        from elle.daemon.observability.metrics import _get_system_health

        with patch.dict(
            "sys.modules",
            {"elle.daemon.api.routes": None},
        ):
            stats = await _get_system_health()

        assert stats["telemetryd_healthy"] is False
        assert stats["daemon_uptime_sec"] == 0.0
        assert stats["last_event_age_sec"] == -1.0
