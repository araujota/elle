"""Coverage tests for elle.daemon.telemetry.trends.

Targets the 38 missing lines (70.6% -> 90%+): compute_forecast_urgency,
create_forecast_with_urgency, TrendContext.get_metric for cpu/swap, and
CorrelationAlert/TrendConfig fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from elle.daemon.telemetry.trends import (
    CRITICAL_THRESHOLDS,
    METRIC_THRESHOLDS,
    TIME_TO_CRITICAL_HOURS,
    TIME_TO_WARNING_HOURS,
    TRACKED_METRICS,
    WARNING_THRESHOLDS,
    CorrelationAlert,
    Forecast,
    MetricSample,
    TrendConfig,
    TrendContext,
    TrendWindow,
    WindowStats,
    compute_forecast_urgency,
    create_forecast_with_urgency,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_warning_thresholds_exist(self):
        assert "disk./.used_pct" in WARNING_THRESHOLDS
        assert "mem.used_pct" in WARNING_THRESHOLDS
        assert WARNING_THRESHOLDS["disk./.used_pct"] == 80.0

    def test_critical_thresholds_exist(self):
        assert "disk./.used_pct" in CRITICAL_THRESHOLDS
        assert CRITICAL_THRESHOLDS["disk./.used_pct"] == 92.0

    def test_metric_thresholds_is_warning_alias(self):
        assert METRIC_THRESHOLDS is WARNING_THRESHOLDS

    def test_time_constants(self):
        assert TIME_TO_WARNING_HOURS == 24.0
        assert TIME_TO_CRITICAL_HOURS == 4.0

    def test_tracked_metrics_non_empty(self):
        assert len(TRACKED_METRICS) > 0
        assert "disk./.used_pct" in TRACKED_METRICS
        assert "mem.used_pct" in TRACKED_METRICS
        assert "cpu.load_1m" in TRACKED_METRICS


# ---------------------------------------------------------------------------
# MetricSample / WindowStats
# ---------------------------------------------------------------------------


class TestMetricSample:
    def test_create(self):
        s = MetricSample(metric="cpu.load_1m", value=2.5)
        assert s.metric == "cpu.load_1m"
        assert s.value == 2.5
        assert s.ts is not None


class TestWindowStats:
    def test_create(self):
        ws = WindowStats(
            window="1h",
            avg_value=80.0,
            min_value=75.0,
            max_value=85.0,
            sample_count=12,
        )
        assert ws.window == "1h"
        assert ws.avg_value == 80.0


# ---------------------------------------------------------------------------
# CorrelationAlert
# ---------------------------------------------------------------------------


class TestCorrelationAlert:
    def test_create(self):
        alert = CorrelationAlert(
            signature="disk_memory_pressure",
            severity="warning",
            description="High disk and memory usage detected",
            contributing_metrics={"disk./.used_pct": 90.0, "mem.used_pct": 85.0},
            recommended_action="Check for large files",
        )
        assert alert.signature == "disk_memory_pressure"
        assert alert.severity == "warning"
        assert "disk./.used_pct" in alert.contributing_metrics

    def test_defaults(self):
        alert = CorrelationAlert(
            signature="test",
            severity="critical",
            description="test alert",
        )
        assert alert.contributing_metrics == {}
        assert alert.recommended_action == ""
        assert alert.detected_at is not None


# ---------------------------------------------------------------------------
# TrendConfig
# ---------------------------------------------------------------------------


class TestTrendConfig:
    def test_defaults(self):
        cfg = TrendConfig()
        assert cfg.aggregation_interval_sec == 300
        assert cfg.anomaly_z_threshold == 3.0
        assert cfg.anomaly_min_samples == 100
        assert cfg.forecast_min_samples == 10
        assert cfg.raw_retention_hours == 168
        assert cfg.aggregation_retention_days == 90

    def test_custom(self):
        cfg = TrendConfig(
            aggregation_interval_sec=60,
            anomaly_z_threshold=2.0,
            anomaly_min_samples=50,
            forecast_min_samples=5,
            raw_retention_hours=24,
            aggregation_retention_days=30,
        )
        assert cfg.aggregation_interval_sec == 60
        assert cfg.raw_retention_hours == 24


# ---------------------------------------------------------------------------
# compute_forecast_urgency
# ---------------------------------------------------------------------------


class TestComputeForecastUrgency:
    def test_no_thresholds_defined(self):
        """Metric without thresholds returns 'none'."""
        urgency, tw, tc = compute_forecast_urgency("proc.zombie_count", 5.0, 1.0)
        assert urgency == "none"
        assert tw is None
        assert tc is None

    def test_none_urgency_low_value_low_rate(self):
        """Value far from thresholds with slow rate returns 'none'."""
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 30.0, 0.001)
        assert urgency == "none"

    def test_prepare_urgency_approaching_warning(self):
        """Value approaching warning threshold within TIME_TO_WARNING_HOURS."""
        # warning = 80, rate = 5.0/hr, current = 70
        # time_to_warning = (80 - 70) / 5.0 = 2.0 hours < 24 hours
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 70.0, 5.0)
        assert urgency == "prepare"
        assert tw is not None
        assert tw == pytest.approx(2.0)

    def test_act_now_approaching_critical(self):
        """Value approaching critical threshold within TIME_TO_CRITICAL_HOURS."""
        # critical = 92, rate = 10.0/hr, current = 89
        # time_to_critical = (92 - 89) / 10.0 = 0.3 hours < 4 hours
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 89.0, 10.0)
        assert urgency == "act_now"
        assert tc is not None
        assert tc == pytest.approx(0.3)

    def test_already_above_critical(self):
        """Value already above critical threshold returns 'act_now'."""
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 95.0, 0.0)
        assert urgency == "act_now"
        assert tc == 0.0

    def test_already_above_warning_below_critical(self):
        """Value above warning but below critical returns 'prepare'."""
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 85.0, 0.0)
        assert urgency == "prepare"
        assert tw == 0.0

    def test_negative_rate_no_urgency(self):
        """Negative (decreasing) rate means no threshold crossing."""
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 70.0, -1.0)
        assert urgency == "none"
        assert tw is None
        assert tc is None

    def test_very_small_rate_treated_as_zero(self):
        """Rate below epsilon (0.001) is treated as zero."""
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 70.0, 0.0005)
        assert urgency == "none"

    def test_critical_takes_priority_over_warning(self):
        """When both warning and critical are within window, act_now wins."""
        # Both within window: critical = 92, rate = 30/hr, current = 90
        # time_to_critical = 2/30 = 0.067h < 4h -> act_now
        urgency, tw, tc = compute_forecast_urgency("disk./.used_pct", 90.0, 30.0)
        assert urgency == "act_now"


# ---------------------------------------------------------------------------
# create_forecast_with_urgency
# ---------------------------------------------------------------------------


class TestCreateForecastWithUrgency:
    def test_basic_forecast(self):
        fc = create_forecast_with_urgency("disk./.used_pct", 50.0, 0.5, confidence=0.7)
        assert fc.metric == "disk./.used_pct"
        assert fc.current_value == 50.0
        assert fc.predicted_value_24h == pytest.approx(50.0 + 0.5 * 24)
        assert fc.predicted_value_7d == pytest.approx(50.0 + 0.5 * 24 * 7)
        assert fc.warning_threshold == 80.0
        assert fc.critical_threshold == 92.0
        assert fc.confidence == 0.7

    def test_forecast_with_urgency_prepare(self):
        # current=75, rate=1.0 -> time_to_warning = (80-75)/1 = 5h < 24h
        fc = create_forecast_with_urgency("disk./.used_pct", 75.0, 1.0)
        assert fc.urgency == "prepare"
        assert fc.will_cross_warning is True
        assert fc.time_to_warning_hours is not None

    def test_forecast_with_urgency_act_now(self):
        # current=91, rate=2.0 -> time_to_critical = (92-91)/2 = 0.5h < 4h
        fc = create_forecast_with_urgency("disk./.used_pct", 91.0, 2.0)
        assert fc.urgency == "act_now"
        assert fc.will_cross_critical is True
        assert fc.time_to_critical_hours is not None

    def test_forecast_with_urgency_none(self):
        fc = create_forecast_with_urgency("disk./.used_pct", 30.0, 0.0)
        assert fc.urgency == "none"
        assert fc.will_cross_warning is False
        assert fc.will_cross_critical is False

    def test_forecast_unknown_metric(self):
        fc = create_forecast_with_urgency("unknown.metric", 50.0, 1.0)
        assert fc.urgency == "none"
        assert fc.warning_threshold is None
        assert fc.critical_threshold is None

    def test_forecast_default_computed_at(self):
        fc = create_forecast_with_urgency("disk./.used_pct", 50.0, 0.0)
        assert fc.computed_at is not None

    def test_forecast_explicit_computed_at(self):
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        fc = create_forecast_with_urgency("disk./.used_pct", 50.0, 0.0, computed_at=ts)
        assert fc.computed_at == ts

    def test_legacy_threshold_is_critical(self):
        """Legacy 'threshold' field should equal critical_threshold."""
        fc = create_forecast_with_urgency("mem.used_pct", 50.0, 1.0)
        assert fc.threshold == fc.critical_threshold


# ---------------------------------------------------------------------------
# TrendContext.get_metric -- all branches
# ---------------------------------------------------------------------------


class TestTrendContextGetMetric:
    def _make_tw(self, metric, current=50.0):
        return TrendWindow(metric=metric, current=current)

    def test_get_disk_metric(self):
        tw = self._make_tw("disk./.used_pct", 85.0)
        ctx = TrendContext(disk_trends={"/": tw})
        result = ctx.get_metric("disk./.used_pct")
        assert result is not None
        assert result.current == 85.0

    def test_get_memory_metric(self):
        tw = self._make_tw("mem.used_pct", 70.0)
        ctx = TrendContext(memory_trend=tw)
        result = ctx.get_metric("mem.used_pct")
        assert result is not None
        assert result.current == 70.0

    def test_get_cpu_metric(self):
        tw = self._make_tw("cpu.load", 2.5)
        ctx = TrendContext(cpu_trend=tw)
        result = ctx.get_metric("cpu.load")
        assert result is not None
        assert result.current == 2.5

    def test_get_swap_metric(self):
        tw = self._make_tw("swap.used_pct", 15.0)
        ctx = TrendContext(swap_trend=tw)
        result = ctx.get_metric("swap.used_pct")
        assert result is not None
        assert result.current == 15.0

    def test_get_unknown_metric(self):
        ctx = TrendContext()
        result = ctx.get_metric("unknown.metric")
        assert result is None

    def test_get_disk_metric_not_present(self):
        ctx = TrendContext(disk_trends={})
        result = ctx.get_metric("disk./var.used_pct")
        assert result is None


# ---------------------------------------------------------------------------
# Forecast model fields coverage
# ---------------------------------------------------------------------------


class TestForecastModel:
    def test_two_level_fields(self):
        fc = Forecast(
            metric="disk./.used_pct",
            current_value=85.0,
            predicted_value_24h=90.0,
            predicted_value_7d=95.0,
            warning_threshold=80.0,
            critical_threshold=92.0,
            will_cross_warning=True,
            time_to_warning_hours=5.0,
            will_cross_critical=True,
            time_to_critical_hours=20.0,
            urgency="prepare",
            method="holt_winters",
            mape=0.05,
            seasonal_detected=True,
        )
        assert fc.urgency == "prepare"
        assert fc.method == "holt_winters"
        assert fc.mape == 0.05
        assert fc.seasonal_detected is True
        assert fc.will_cross_warning is True
        assert fc.will_cross_critical is True

    def test_forecast_defaults(self):
        fc = Forecast(
            metric="test",
            current_value=50.0,
            predicted_value_24h=52.0,
            predicted_value_7d=55.0,
        )
        assert fc.urgency == "none"
        assert fc.method is None
        assert fc.mape is None
        assert fc.seasonal_detected is False
        assert fc.rate_of_change == 0.0
