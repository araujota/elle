"""Trend analysis models for predictive capabilities.

Defines data structures for metric aggregations, trend windows,
forecasts, and anomaly detection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Trend Window Models
# =============================================================================


TimeWindow = Literal["1h", "6h", "24h", "7d"]

ForecastMethod = Literal["linear", "holt_winters", "ar1", "seasonal"]


class MetricSample(BaseModel):
    """A single metric sample."""

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric name (e.g., 'disk./.used_pct')")
    value: float = Field(description="Metric value")
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WindowStats(BaseModel):
    """Statistics for a single time window."""

    model_config = ConfigDict(frozen=True)

    window: TimeWindow = Field(description="Time window (1h, 6h, 24h, 7d)")
    avg_value: float = Field(description="Average value in window")
    min_value: float = Field(description="Minimum value in window")
    max_value: float = Field(description="Maximum value in window")
    sample_count: int = Field(ge=0, default=0)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrendWindow(BaseModel):
    """Multi-window aggregation for a metric.

    Captures current value and rolling averages across
    multiple time windows for trend analysis.
    """

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric identifier")
    current: float = Field(description="Current/latest value")

    # Rolling averages
    avg_1h: float | None = Field(default=None, description="Average over last 1 hour")
    avg_6h: float | None = Field(default=None, description="Average over last 6 hours")
    avg_24h: float | None = Field(default=None, description="Average over last 24 hours")
    avg_7d: float | None = Field(default=None, description="Average over last 7 days")

    # Rate of change
    rate_of_change_per_hour: float = Field(
        default=0.0,
        description="Rate of change per hour (derivative)",
    )

    # Anomaly detection
    is_anomaly: bool = Field(
        default=False,
        description="Whether current value is anomalous",
    )
    anomaly_score: float | None = Field(
        default=None,
        description="Anomaly score (z-score or similar)",
    )

    # Metadata
    sample_count: int = Field(ge=0, default=0)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MetricBaseline(BaseModel):
    """Baseline statistics for anomaly detection.

    Computed from historical data using exponential moving average.
    """

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric identifier")
    baseline_mean: float = Field(description="Baseline mean value")
    baseline_stddev: float = Field(ge=0, description="Baseline standard deviation")
    samples: int = Field(ge=0, default=0, description="Number of samples in baseline")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Forecast Models
# =============================================================================


class Forecast(BaseModel):
    """Two-level forecast for a metric.

    Predicts future values based on current trends and detects
    when warning (prepare plan) and critical (act now) thresholds
    will be crossed.

    Two-level detection:
    - Warning level: "heading wrong direction" → prepare remediation plan
    - Critical level: "point of no return" → execute plan immediately
    """

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric identifier")
    current_value: float = Field(description="Current value")

    # Predictions
    predicted_value_24h: float = Field(description="Predicted value in 24 hours")
    predicted_value_7d: float = Field(description="Predicted value in 7 days")

    # Legacy single threshold (for backwards compatibility)
    threshold: float | None = Field(
        default=None,
        description="Critical threshold (e.g., 95% for disk)",
    )
    time_to_threshold_hours: float | None = Field(
        default=None,
        description="Estimated hours until threshold is crossed",
    )
    will_cross_threshold: bool = Field(
        default=False,
        description="Whether threshold will be crossed",
    )

    # Two-level thresholds
    warning_threshold: float | None = Field(
        default=None,
        description="Warning threshold - 'heading wrong direction' (prepare plan)",
    )
    critical_threshold: float | None = Field(
        default=None,
        description="Critical threshold - 'point of no return' (act now)",
    )

    # Warning level detection (prepare plan)
    will_cross_warning: bool = Field(
        default=False,
        description="Whether warning threshold will be crossed within forecast horizon",
    )
    time_to_warning_hours: float | None = Field(
        default=None,
        description="Estimated hours until warning threshold is crossed",
    )

    # Critical level detection (act now)
    will_cross_critical: bool = Field(
        default=False,
        description="Whether critical threshold will be crossed within forecast horizon",
    )
    time_to_critical_hours: float | None = Field(
        default=None,
        description="Estimated hours until critical threshold is crossed",
    )

    # Urgency classification - the key field for triggering reactive functions
    urgency: Literal["none", "prepare", "act_now"] = Field(
        default="none",
        description=(
            "'none': No action needed, "
            "'prepare': Warning threshold crossing imminent - prepare remediation plan, "
            "'act_now': Critical threshold crossing imminent - execute plan immediately"
        ),
    )

    # Confidence
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.5,
        description="Confidence in forecast (based on trend stability)",
    )

    # Metadata
    rate_of_change: float = Field(
        default=0.0,
        description="Rate of change per hour used for forecast",
    )
    # Forecasting method metadata (monitoring sprint)
    method: ForecastMethod | None = Field(
        default=None,
        description="Forecasting method used",
    )
    mape: float | None = Field(
        default=None,
        description="Mean Absolute Percentage Error from backtest",
    )
    seasonal_detected: bool = Field(
        default=False,
        description="Whether seasonal pattern was detected in data",
    )
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Anomaly Models
# =============================================================================


class AnomalyResult(BaseModel):
    """Result of anomaly detection for a metric.

    Indicates whether the current value is anomalous
    and provides context for understanding why.
    """

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric identifier")
    current_value: float = Field(description="Current value")
    is_anomaly: bool = Field(description="Whether value is anomalous")

    # Detection details
    anomaly_score: float = Field(
        description="Anomaly score (z-score: number of std devs from mean)",
    )
    threshold_used: float = Field(
        default=3.0,
        description="Z-score threshold used for detection",
    )

    # Context
    baseline_mean: float = Field(description="Baseline mean for comparison")
    baseline_stddev: float = Field(description="Baseline standard deviation")
    deviation_direction: Literal["above", "below", "normal"] = Field(
        description="Direction of deviation",
    )

    # Metadata
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Aggregated Trend Context
# =============================================================================


class TrendContext(BaseModel):
    """Aggregated trend context for multiple metrics.

    Provides a snapshot of system trends for decision-making
    in planning and pre-execution validation.
    """

    model_config = ConfigDict(frozen=True)

    # Resource trends
    disk_trends: dict[str, TrendWindow] = Field(
        default_factory=dict,
        description="Disk usage trends by mount point",
    )
    memory_trend: TrendWindow | None = Field(
        default=None,
        description="Memory usage trend",
    )
    cpu_trend: TrendWindow | None = Field(
        default=None,
        description="CPU usage trend",
    )
    swap_trend: TrendWindow | None = Field(
        default=None,
        description="Swap usage trend",
    )

    # Forecasts for critical metrics
    forecasts: dict[str, Forecast] = Field(
        default_factory=dict,
        description="Forecasts by metric",
    )

    # Active anomalies
    anomalies: tuple[AnomalyResult, ...] = Field(
        default_factory=tuple,
        description="Currently detected anomalies",
    )

    # Summary
    has_warnings: bool = Field(
        default=False,
        description="Whether any metrics have warnings",
    )
    warning_messages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Human-readable warning messages",
    )

    # Correlation alerts (monitoring sprint)
    correlation_alerts: tuple[CorrelationAlert, ...] = Field(
        default_factory=tuple,
        description="Active multi-variate correlation alerts",
    )

    # Metadata
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_metric(self, name: str) -> TrendWindow | None:
        """Get trend for a specific metric."""
        if name.startswith("disk."):
            mount = name.replace("disk.", "").replace(".used_pct", "")
            return self.disk_trends.get(mount)
        elif name == "mem.used_pct":
            return self.memory_trend
        elif name == "cpu.load":
            return self.cpu_trend
        elif name == "swap.used_pct":
            return self.swap_trend
        return None


# =============================================================================
# Pre-Execution Validation
# =============================================================================


class ValidationWarning(BaseModel):
    """A warning from pre-execution validation."""

    model_config = ConfigDict(frozen=True)

    severity: Literal["info", "warning", "critical"] = Field(description="Warning severity")
    message: str = Field(description="Human-readable warning message")
    metric: str | None = Field(default=None, description="Related metric")
    suggestion: str | None = Field(default=None, description="Suggested action")


class PlanValidationResult(BaseModel):
    """Result of plan validation against current trends.

    Indicates whether a plan is likely to succeed given
    current system state and trends.
    """

    model_config = ConfigDict(frozen=True)

    # Overall assessment
    likely_to_succeed: bool = Field(
        default=True,
        description="Whether plan is likely to succeed",
    )
    success_probability: float = Field(
        ge=0.0,
        le=1.0,
        default=0.8,
        description="Estimated success probability",
    )

    # Warnings
    warnings: tuple[ValidationWarning, ...] = Field(
        default_factory=tuple,
        description="Validation warnings",
    )
    has_warnings: bool = Field(default=False)
    has_critical_warnings: bool = Field(default=False)

    # Trend checks performed
    checks_performed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of checks performed",
    )

    # Drift detection
    drift_detected: bool = Field(
        default=False,
        description="Whether state drift from successful prior was detected",
    )
    drift_details: dict[str, Any] = Field(
        default_factory=dict,
        description="Details of detected drift",
    )


# =============================================================================
# Configuration
# =============================================================================


class CorrelationAlert(BaseModel):
    """Alert from multi-variate correlation detection."""

    model_config = ConfigDict(frozen=True)

    signature: str = Field(description="Correlation signature name")
    severity: Literal["warning", "critical"] = Field(description="Alert severity")
    description: str = Field(description="Human-readable description")
    contributing_metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Metrics that triggered this correlation",
    )
    recommended_action: str = Field(
        default="",
        description="Suggested investigation or remediation",
    )
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrendConfig(BaseModel):
    """Configuration for trend aggregation."""

    model_config = ConfigDict(frozen=True)

    # Aggregation intervals
    aggregation_interval_sec: int = Field(
        ge=60,
        default=300,
        description="How often to run aggregation (default: 5 min)",
    )

    # Anomaly detection
    anomaly_z_threshold: float = Field(
        ge=1.0,
        le=5.0,
        default=3.0,
        description="Z-score threshold for anomaly detection",
    )
    anomaly_min_samples: int = Field(
        ge=10,
        default=100,
        description="Minimum samples before anomaly detection",
    )

    # Forecast
    forecast_min_samples: int = Field(
        ge=5,
        default=10,
        description="Minimum samples for forecasting",
    )

    # Retention
    raw_retention_hours: int = Field(
        ge=1,
        default=168,
        description="Hours to retain raw metric samples (default: 7 days)",
    )
    aggregation_retention_days: int = Field(
        ge=1,
        default=90,
        description="Days to retain aggregations",
    )


# =============================================================================
# Tracked Metrics
# =============================================================================


# Metrics tracked by the aggregator
TRACKED_METRICS = [
    # Existing (9)
    "disk./.used_pct",
    "disk./home.used_pct",
    "disk./var.used_pct",
    "mem.used_pct",
    "mem.available_pct",
    "swap.used_pct",
    "cpu.load_1m",
    "cpu.load_5m",
    "cpu.load_15m",
    # Disk metrics (3)
    "disk./.inode_pct",
    "disk./var.inode_pct",
    "io.util_pct",
    # Network metrics (3)
    "net.tcp_retransmit_rate",
    "net.tcp_time_wait",
    "net.tcp_close_wait",
    # Thermal (2)
    "thermal.max_temp_c",
    "thermal.freq_ratio",
    # Process (2)
    "proc.zombie_count",
    "proc.total_count",
    # DNS (1)
    "dns.p95_ms",
    # Cgroup (2)
    "cgroup.max_mem_pct",
    "cgroup.max_cpu_throttle_pct",
    # PSI (2)
    "psi.cpu_avg10",
    "psi.memory_avg10",
]

# =============================================================================
# Two-Level Forecast Thresholds
# =============================================================================

# Warning thresholds - "heading wrong direction" (prepare remediation plan)
# When a metric is projected to cross this threshold, we prepare a plan
# but don't execute it yet
WARNING_THRESHOLDS = {
    "disk./.used_pct": 80.0,
    "disk./home.used_pct": 80.0,
    "disk./var.used_pct": 80.0,
    "mem.used_pct": 80.0,
    "swap.used_pct": 70.0,
    "cpu.load_1m": 3.0,
}

# Critical thresholds - "point of no return" (act immediately)
# When a metric is projected to cross this threshold, we execute
# the prepared remediation plan immediately
CRITICAL_THRESHOLDS = {
    "disk./.used_pct": 92.0,
    "disk./home.used_pct": 92.0,
    "disk./var.used_pct": 92.0,
    "mem.used_pct": 92.0,
    "swap.used_pct": 90.0,
    "cpu.load_1m": 4.0,
}

# Time-based urgency thresholds (hours)
# These determine when to trigger based on time-to-threshold
TIME_TO_WARNING_HOURS = 24.0  # Prepare plan when <24h to warning threshold
TIME_TO_CRITICAL_HOURS = 4.0  # Act immediately when <4h to critical threshold

# Legacy alias for backwards compatibility
METRIC_THRESHOLDS = WARNING_THRESHOLDS


# =============================================================================
# Forecast Urgency Computation
# =============================================================================


def compute_forecast_urgency(
    metric: str,
    current_value: float,
    rate_of_change: float,
    confidence: float = 0.5,
) -> tuple[Literal["none", "prepare", "act_now"], float | None, float | None]:
    """Compute the urgency level for a metric based on its forecast.

    Determines whether action is needed based on projected threshold crossings:
    - "prepare": Warning threshold will be crossed within TIME_TO_WARNING_HOURS
    - "act_now": Critical threshold will be crossed within TIME_TO_CRITICAL_HOURS
    - "none": No immediate action needed

    Args:
        metric: The metric identifier (e.g., "disk./.used_pct").
        current_value: Current metric value.
        rate_of_change: Rate of change per hour.
        confidence: Forecast confidence (0-1).

    Returns:
        Tuple of (urgency, time_to_warning_hours, time_to_critical_hours).
    """
    warning_threshold = WARNING_THRESHOLDS.get(metric)
    critical_threshold = CRITICAL_THRESHOLDS.get(metric)

    if warning_threshold is None or critical_threshold is None:
        return "none", None, None

    time_to_warning: float | None = None
    time_to_critical: float | None = None

    # Only calculate times if rate_of_change is positive (increasing toward thresholds)
    if rate_of_change > 0.001:  # Small epsilon to avoid division issues
        if current_value < warning_threshold:
            time_to_warning = (warning_threshold - current_value) / rate_of_change

        if current_value < critical_threshold:
            time_to_critical = (critical_threshold - current_value) / rate_of_change

    # Determine urgency level
    urgency: Literal["none", "prepare", "act_now"] = "none"

    # Check critical first (higher priority)
    if time_to_critical is not None and time_to_critical <= TIME_TO_CRITICAL_HOURS:
        urgency = "act_now"
    # Then check warning
    elif time_to_warning is not None and time_to_warning <= TIME_TO_WARNING_HOURS:
        urgency = "prepare"
    # Also trigger if already above thresholds
    elif current_value >= critical_threshold:
        urgency = "act_now"
        time_to_critical = 0.0
    elif current_value >= warning_threshold:
        urgency = "prepare"
        time_to_warning = 0.0

    return urgency, time_to_warning, time_to_critical


def create_forecast_with_urgency(
    metric: str,
    current_value: float,
    rate_of_change: float,
    confidence: float = 0.5,
    computed_at: datetime | None = None,
) -> Forecast:
    """Create a Forecast with two-level urgency computation.

    This is a convenience function that creates a fully-populated Forecast
    with warning and critical threshold detection.

    Args:
        metric: The metric identifier.
        current_value: Current metric value.
        rate_of_change: Rate of change per hour.
        confidence: Forecast confidence (0-1).
        computed_at: When the forecast was computed (defaults to now).

    Returns:
        Forecast with all urgency fields populated.
    """
    if computed_at is None:
        computed_at = datetime.now(timezone.utc)

    # Get thresholds
    warning_threshold = WARNING_THRESHOLDS.get(metric)
    critical_threshold = CRITICAL_THRESHOLDS.get(metric)

    # Compute predictions
    predicted_24h = current_value + (rate_of_change * 24.0)
    predicted_7d = current_value + (rate_of_change * 24.0 * 7.0)

    # Compute urgency
    urgency, time_to_warning, time_to_critical = compute_forecast_urgency(
        metric, current_value, rate_of_change, confidence
    )

    # Determine threshold crossing
    will_cross_warning = time_to_warning is not None
    will_cross_critical = time_to_critical is not None

    # Legacy fields (use critical threshold)
    threshold = critical_threshold
    time_to_threshold = time_to_critical
    will_cross_threshold = will_cross_critical

    return Forecast(
        metric=metric,
        current_value=current_value,
        predicted_value_24h=predicted_24h,
        predicted_value_7d=predicted_7d,
        # Legacy fields
        threshold=threshold,
        time_to_threshold_hours=time_to_threshold,
        will_cross_threshold=will_cross_threshold,
        # Two-level thresholds
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        # Warning level
        will_cross_warning=will_cross_warning,
        time_to_warning_hours=time_to_warning,
        # Critical level
        will_cross_critical=will_cross_critical,
        time_to_critical_hours=time_to_critical,
        # Urgency classification
        urgency=urgency,
        # Metadata
        confidence=confidence,
        rate_of_change=rate_of_change,
        computed_at=computed_at,
    )
