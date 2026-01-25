"""Tests for trend aggregation and forecasting."""

from datetime import datetime, timedelta

import pytest

from elle.daemon.telemetry.trends import (
    AnomalyResult,
    Forecast,
    MetricBaseline,
    PlanValidationResult,
    TrendContext,
    TrendWindow,
    ValidationWarning,
)


class TestTrendWindow:
    """Tests for TrendWindow model."""

    def test_create_trend_window(self):
        """Test creating a trend window."""
        window = TrendWindow(
            metric="disk./.used_pct",
            current=85.5,
            avg_1h=84.0,
            avg_6h=82.0,
            avg_24h=78.0,
            avg_7d=70.0,
            rate_of_change_per_hour=1.5,
            is_anomaly=False,
        )

        assert window.metric == "disk./.used_pct"
        assert window.current == 85.5
        assert window.rate_of_change_per_hour == 1.5
        assert window.is_anomaly is False

    def test_trend_window_with_anomaly(self):
        """Test trend window with anomaly."""
        window = TrendWindow(
            metric="cpu.load_1m",
            current=12.5,
            avg_1h=2.0,
            rate_of_change_per_hour=10.0,
            is_anomaly=True,
            anomaly_score=3.5,
        )

        assert window.is_anomaly is True
        assert window.anomaly_score == 3.5

    def test_trend_window_minimal(self):
        """Test trend window with minimal data."""
        window = TrendWindow(
            metric="mem.used_pct",
            current=65.0,
            rate_of_change_per_hour=0.0,
            is_anomaly=False,
        )

        assert window.avg_1h is None
        assert window.avg_6h is None
        assert window.avg_24h is None


class TestMetricBaseline:
    """Tests for MetricBaseline model."""

    def test_create_baseline(self):
        """Test creating a metric baseline."""
        baseline = MetricBaseline(
            metric="disk./.used_pct",
            baseline_mean=60.0,
            baseline_stddev=10.0,
            samples=1000,
        )

        assert baseline.metric == "disk./.used_pct"
        assert baseline.baseline_mean == 60.0
        assert baseline.baseline_stddev == 10.0
        assert baseline.samples == 1000


class TestForecast:
    """Tests for Forecast model."""

    def test_forecast_will_cross_threshold(self):
        """Test forecast that will cross threshold."""
        forecast = Forecast(
            metric="disk./.used_pct",
            current_value=85.0,
            predicted_value_24h=92.0,
            predicted_value_7d=100.0,
            threshold=95.0,
            time_to_threshold_hours=48.0,
            confidence=0.8,
            will_cross_threshold=True,
        )

        assert forecast.will_cross_threshold is True
        assert forecast.time_to_threshold_hours == 48.0
        assert forecast.threshold == 95.0

    def test_forecast_stable(self):
        """Test forecast for stable metric."""
        forecast = Forecast(
            metric="mem.used_pct",
            current_value=50.0,
            predicted_value_24h=52.0,
            predicted_value_7d=55.0,
            confidence=0.7,
            will_cross_threshold=False,
        )

        assert forecast.will_cross_threshold is False
        assert forecast.threshold is None
        assert forecast.time_to_threshold_hours is None


class TestAnomalyResult:
    """Tests for AnomalyResult model."""

    def test_create_anomaly(self):
        """Test creating an anomaly result."""
        anomaly = AnomalyResult(
            metric="cpu.load_1m",
            current_value=15.0,
            is_anomaly=True,
            anomaly_score=13.0,
            threshold_used=3.0,
            baseline_mean=2.0,
            baseline_stddev=1.0,
            deviation_direction="above",
        )

        assert anomaly.anomaly_score == 13.0
        assert anomaly.is_anomaly is True
        assert anomaly.deviation_direction == "above"

    def test_anomaly_below_baseline(self):
        """Test anomaly below baseline."""
        anomaly = AnomalyResult(
            metric="mem.available_pct",
            current_value=5.0,
            is_anomaly=True,
            anomaly_score=-4.0,
            baseline_mean=30.0,
            baseline_stddev=5.0,
            deviation_direction="below",
        )

        assert anomaly.deviation_direction == "below"

    def test_normal_result(self):
        """Test normal (non-anomaly) result."""
        result = AnomalyResult(
            metric="cpu.load_1m",
            current_value=2.5,
            is_anomaly=False,
            anomaly_score=0.5,
            baseline_mean=2.0,
            baseline_stddev=1.0,
            deviation_direction="normal",
        )

        assert result.is_anomaly is False
        assert result.deviation_direction == "normal"


class TestTrendContext:
    """Tests for TrendContext model."""

    def test_create_trend_context(self):
        """Test creating a trend context."""
        disk_trend = TrendWindow(
            metric="disk./.used_pct",
            current=85.0,
            rate_of_change_per_hour=0.5,
            is_anomaly=False,
        )

        context = TrendContext(
            disk_trends={"/": disk_trend},
            memory_trend=None,
            cpu_trend=None,
            swap_trend=None,
        )

        assert len(context.disk_trends) == 1
        assert "/" in context.disk_trends

    def test_get_metric(self):
        """Test getting a specific metric trend."""
        disk_trend = TrendWindow(
            metric="disk./.used_pct",
            current=85.0,
            rate_of_change_per_hour=0.5,
            is_anomaly=False,
        )
        mem_trend = TrendWindow(
            metric="mem.used_pct",
            current=70.0,
            rate_of_change_per_hour=0.0,
            is_anomaly=False,
        )

        context = TrendContext(
            disk_trends={"/": disk_trend},
            memory_trend=mem_trend,
        )

        # Get disk metric
        found = context.get_metric("disk./.used_pct")
        assert found is not None
        assert found.current == 85.0

        # Get memory metric
        mem = context.get_metric("mem.used_pct")
        assert mem is not None
        assert mem.current == 70.0

        # Non-existent metric
        not_found = context.get_metric("nonexistent")
        assert not_found is None

    def test_context_with_forecasts(self):
        """Test context with forecasts."""
        forecast = Forecast(
            metric="disk./.used_pct",
            current_value=85.0,
            predicted_value_24h=90.0,
            predicted_value_7d=95.0,
            confidence=0.8,
            will_cross_threshold=True,
        )

        context = TrendContext(
            forecasts={"disk./.used_pct": forecast},
        )

        assert "disk./.used_pct" in context.forecasts
        assert context.forecasts["disk./.used_pct"].predicted_value_24h == 90.0

    def test_context_with_anomalies(self):
        """Test context with anomalies."""
        anomaly = AnomalyResult(
            metric="cpu.load_1m",
            current_value=15.0,
            is_anomaly=True,
            anomaly_score=13.0,
            baseline_mean=2.0,
            baseline_stddev=1.0,
            deviation_direction="above",
        )

        context = TrendContext(
            anomalies=(anomaly,),
        )

        assert len(context.anomalies) == 1
        assert context.anomalies[0].is_anomaly is True

    def test_context_with_warnings(self):
        """Test context with warning messages."""
        context = TrendContext(
            has_warnings=True,
            warning_messages=("Disk usage above 85%", "Memory pressure increasing"),
        )

        assert context.has_warnings is True
        assert len(context.warning_messages) == 2


class TestValidationWarning:
    """Tests for ValidationWarning model."""

    def test_create_warning(self):
        """Test creating a validation warning."""
        warning = ValidationWarning(
            severity="warning",
            message="Disk at 92% - package install may fail",
            metric="disk./.used_pct",
            suggestion="Consider freeing up disk space first",
        )

        assert warning.severity == "warning"
        assert "92%" in warning.message
        assert warning.metric == "disk./.used_pct"

    def test_warning_without_metric(self):
        """Test warning without associated metric."""
        warning = ValidationWarning(
            severity="info",
            message="Service may need restart after configuration change",
        )

        assert warning.severity == "info"
        assert warning.metric is None
        assert warning.suggestion is None

    def test_critical_warning(self):
        """Test critical severity warning."""
        warning = ValidationWarning(
            severity="critical",
            message="Disk full - operation will fail",
            metric="disk./.used_pct",
        )

        assert warning.severity == "critical"


class TestPlanValidationResult:
    """Tests for PlanValidationResult model."""

    def test_validation_passed(self):
        """Test validation that passed."""
        result = PlanValidationResult(
            likely_to_succeed=True,
            success_probability=0.85,
            warnings=(),
            has_warnings=False,
            has_critical_warnings=False,
        )

        assert result.likely_to_succeed is True
        assert result.has_warnings is False
        assert result.success_probability == 0.85

    def test_validation_with_warnings(self):
        """Test validation with warnings."""
        warning = ValidationWarning(
            severity="warning",
            message="Low disk space",
        )

        result = PlanValidationResult(
            likely_to_succeed=True,
            success_probability=0.6,
            warnings=(warning,),
            has_warnings=True,
            has_critical_warnings=False,
        )

        assert result.likely_to_succeed is True
        assert result.has_warnings is True
        assert len(result.warnings) == 1

    def test_validation_with_critical(self):
        """Test validation with critical warnings."""
        warning = ValidationWarning(
            severity="critical",
            message="Disk full",
        )

        result = PlanValidationResult(
            likely_to_succeed=False,
            success_probability=0.1,
            warnings=(warning,),
            has_warnings=True,
            has_critical_warnings=True,
        )

        assert result.likely_to_succeed is False
        assert result.has_critical_warnings is True

    def test_validation_with_drift(self):
        """Test validation with detected drift."""
        result = PlanValidationResult(
            likely_to_succeed=True,
            success_probability=0.7,
            drift_detected=True,
            drift_details={"disk_pressure_delta": 0.15},
        )

        assert result.drift_detected is True
        assert "disk_pressure_delta" in result.drift_details

    def test_validation_checks(self):
        """Test validation with checks performed."""
        result = PlanValidationResult(
            likely_to_succeed=True,
            checks_performed=(
                "disk_space_check",
                "memory_check",
                "port_availability_check",
            ),
        )

        assert len(result.checks_performed) == 3
        assert "disk_space_check" in result.checks_performed
