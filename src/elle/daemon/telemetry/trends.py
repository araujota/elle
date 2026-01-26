"""Trend analysis models for predictive capabilities.

Defines data structures for metric aggregations, trend windows,
forecasts, and anomaly detection.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Trend Window Models
# =============================================================================


TimeWindow = Literal["1h", "6h", "24h", "7d"]


class MetricSample(BaseModel):
    """A single metric sample."""

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric name (e.g., 'disk./.used_pct')")
    value: float = Field(description="Metric value")
    ts: datetime = Field(default_factory=datetime.utcnow)


class WindowStats(BaseModel):
    """Statistics for a single time window."""

    model_config = ConfigDict(frozen=True)

    window: TimeWindow = Field(description="Time window (1h, 6h, 24h, 7d)")
    avg_value: float = Field(description="Average value in window")
    min_value: float = Field(description="Minimum value in window")
    max_value: float = Field(description="Maximum value in window")
    sample_count: int = Field(ge=0, default=0)
    computed_at: datetime = Field(default_factory=datetime.utcnow)


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
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class MetricBaseline(BaseModel):
    """Baseline statistics for anomaly detection.

    Computed from historical data using exponential moving average.
    """

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric identifier")
    baseline_mean: float = Field(description="Baseline mean value")
    baseline_stddev: float = Field(ge=0, description="Baseline standard deviation")
    samples: int = Field(ge=0, default=0, description="Number of samples in baseline")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Forecast Models
# =============================================================================


class Forecast(BaseModel):
    """Simple linear forecast for a metric.

    Predicts future values based on current trends.
    """

    model_config = ConfigDict(frozen=True)

    metric: str = Field(description="Metric identifier")
    current_value: float = Field(description="Current value")

    # Predictions
    predicted_value_24h: float = Field(description="Predicted value in 24 hours")
    predicted_value_7d: float = Field(description="Predicted value in 7 days")

    # Threshold crossing
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
    computed_at: datetime = Field(default_factory=datetime.utcnow)


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
    detected_at: datetime = Field(default_factory=datetime.utcnow)


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

    # Metadata
    computed_at: datetime = Field(default_factory=datetime.utcnow)

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
    "disk./.used_pct",
    "disk./home.used_pct",
    "disk./var.used_pct",
    "mem.used_pct",
    "mem.available_pct",
    "swap.used_pct",
    "cpu.load_1m",
    "cpu.load_5m",
    "cpu.load_15m",
]

# Default thresholds for warnings
METRIC_THRESHOLDS = {
    "disk./.used_pct": 90.0,
    "disk./home.used_pct": 90.0,
    "disk./var.used_pct": 90.0,
    "mem.used_pct": 90.0,
    "swap.used_pct": 80.0,
    "cpu.load_1m": 4.0,  # Depends on CPU count
}

# Critical thresholds (action required)
CRITICAL_THRESHOLDS = {
    "disk./.used_pct": 95.0,
    "disk./home.used_pct": 95.0,
    "disk./var.used_pct": 95.0,
    "mem.used_pct": 95.0,
    "swap.used_pct": 95.0,
}
