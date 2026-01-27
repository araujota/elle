"""Trend aggregation for predictive capabilities.

Runs periodically to compute rolling aggregates, detect anomalies,
and generate forecasts from telemetry data.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Literal

from elle.daemon.telemetry.schema import ensure_schema, get_connection
from elle.daemon.telemetry.trends import (
    CRITICAL_THRESHOLDS,
    METRIC_THRESHOLDS,
    TRACKED_METRICS,
    AnomalyResult,
    Forecast,
    MetricBaseline,
    TrendConfig,
    TrendContext,
    TrendWindow,
)


@contextmanager
def _ensure_connection(
    conn: sqlite3.Connection | None = None,
) -> Iterator[sqlite3.Connection]:
    """Context manager that ensures a valid connection."""
    own_conn = conn is None
    if own_conn:
        actual_conn = get_connection()
    else:
        # conn is not None here due to the if check above
        actual_conn = conn  # type: ignore[assignment]
    try:
        yield actual_conn
    finally:
        if own_conn:
            actual_conn.close()


# =============================================================================
# Schema for aggregations
# =============================================================================


METRIC_SAMPLES_TABLE = """
CREATE TABLE IF NOT EXISTS metric_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    ts TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

METRIC_AGGREGATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS metric_aggregations (
    metric TEXT NOT NULL,
    window TEXT NOT NULL,
    avg_value REAL NOT NULL,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (metric, window)
)
"""

METRIC_BASELINES_TABLE = """
CREATE TABLE IF NOT EXISTS metric_baselines (
    metric TEXT PRIMARY KEY,
    baseline_mean REAL NOT NULL,
    baseline_stddev REAL NOT NULL,
    samples INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
"""

AGGREGATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_metric_samples_metric_ts ON metric_samples(metric, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_metric_samples_ts ON metric_samples(ts DESC)",
]


def ensure_aggregation_schema(conn: sqlite3.Connection) -> None:
    """Ensure aggregation tables exist."""
    cursor = conn.cursor()
    cursor.execute(METRIC_SAMPLES_TABLE)
    cursor.execute(METRIC_AGGREGATIONS_TABLE)
    cursor.execute(METRIC_BASELINES_TABLE)
    for index in AGGREGATION_INDEXES:
        cursor.execute(index)
    conn.commit()


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format."""
    return dt.isoformat()


def _parse_datetime(s: str) -> datetime:
    """Parse ISO format string to datetime."""
    return datetime.fromisoformat(s)


# =============================================================================
# Metric Collection
# =============================================================================


def collect_system_metrics() -> dict[str, float]:
    """Collect current system metrics.

    Returns:
        Dict mapping metric name to current value.
    """
    metrics: dict[str, float] = {}

    try:
        import os

        # Disk usage
        for mount in ["/", "/home", "/var"]:
            try:
                stat = os.statvfs(mount)
                total = stat.f_blocks * stat.f_frsize
                free = stat.f_bfree * stat.f_frsize
                if total > 0:
                    used_pct = ((total - free) / total) * 100
                    key = f"disk.{mount}.used_pct".replace("//", "/")
                    metrics[key] = round(used_pct, 2)
            except (OSError, FileNotFoundError):
                pass

        # Memory
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = int(parts[1].strip().split()[0])
                        meminfo[key] = value

                total = meminfo.get("MemTotal", 1)
                available = meminfo.get("MemAvailable", 0)
                used_pct = ((total - available) / total) * 100
                metrics["mem.used_pct"] = round(used_pct, 2)
                metrics["mem.available_pct"] = round((available / total) * 100, 2)

                swap_total = meminfo.get("SwapTotal", 0)
                swap_free = meminfo.get("SwapFree", 0)
                if swap_total > 0:
                    swap_used_pct = ((swap_total - swap_free) / swap_total) * 100
                    metrics["swap.used_pct"] = round(swap_used_pct, 2)
                else:
                    metrics["swap.used_pct"] = 0.0
        except (OSError, FileNotFoundError, KeyError):
            pass

        # CPU load
        try:
            load = os.getloadavg()
            metrics["cpu.load_1m"] = round(load[0], 2)
            metrics["cpu.load_5m"] = round(load[1], 2)
            metrics["cpu.load_15m"] = round(load[2], 2)
        except (OSError, AttributeError):
            pass

    except Exception:
        pass

    return metrics


def record_metric(
    metric: str,
    value: float,
    ts: datetime | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record a metric sample.

    Args:
        metric: Metric name.
        value: Metric value.
        ts: Timestamp (defaults to now).
        conn: SQLite connection.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_schema(c)
            ensure_aggregation_schema(c)

        if ts is None:
            ts = datetime.utcnow()

        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO metric_samples (metric, value, ts)
            VALUES (?, ?, ?)
            """,
            (metric, value, _serialize_datetime(ts)),
        )
        c.commit()


def record_metrics_batch(
    metrics: dict[str, float],
    ts: datetime | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record multiple metrics at once.

    Args:
        metrics: Dict of metric name to value.
        ts: Timestamp for all metrics.
        conn: SQLite connection.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_schema(c)
            ensure_aggregation_schema(c)

        if ts is None:
            ts = datetime.utcnow()

        ts_str = _serialize_datetime(ts)
        cursor = c.cursor()

        for metric, value in metrics.items():
            cursor.execute(
                """
                INSERT INTO metric_samples (metric, value, ts)
                VALUES (?, ?, ?)
                """,
                (metric, value, ts_str),
            )

        c.commit()


# =============================================================================
# Trend Aggregator
# =============================================================================


class TrendAggregator:
    """Runs periodically to compute metric aggregations.

    Collects system metrics, computes rolling averages,
    detects anomalies, and generates forecasts.
    """

    def __init__(
        self,
        config: TrendConfig | None = None,
    ):
        """Initialize the aggregator.

        Args:
            config: Aggregation configuration.
        """
        self.config = config or TrendConfig()
        self._last_run: datetime | None = None

    def run_aggregation_cycle(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> TrendContext:
        """Run a complete aggregation cycle.

        Collects metrics, computes aggregations, detects anomalies,
        and generates forecasts.

        Args:
            conn: SQLite connection.

        Returns:
            TrendContext with current trends.
        """
        with _ensure_connection(conn) as c:
            if conn is None:
                ensure_schema(c)
                ensure_aggregation_schema(c)

            now = datetime.utcnow()

            # Collect and record current metrics
            current_metrics = collect_system_metrics()
            record_metrics_batch(current_metrics, ts=now, conn=c)

            # Compute aggregations for each tracked metric
            disk_trends: dict[str, TrendWindow] = {}
            memory_trend: TrendWindow | None = None
            cpu_trend: TrendWindow | None = None
            swap_trend: TrendWindow | None = None
            forecasts: dict[str, Forecast] = {}
            anomalies: list[AnomalyResult] = []
            warnings: list[str] = []

            for metric in TRACKED_METRICS:
                current_value = current_metrics.get(metric)
                if current_value is None:
                    continue

                # Compute window stats
                trend = self._compute_trend_window(metric, current_value, now, c)

                # Categorize trend
                if metric.startswith("disk."):
                    mount = metric.replace("disk.", "").replace(".used_pct", "")
                    disk_trends[mount] = trend
                elif metric == "mem.used_pct":
                    memory_trend = trend
                elif metric == "cpu.load_1m":
                    cpu_trend = trend
                elif metric == "swap.used_pct":
                    swap_trend = trend

                # Generate forecast for disk metrics
                if metric.startswith("disk."):
                    forecast = self._compute_forecast(metric, trend, c)
                    forecasts[metric] = forecast

                    # Check for warnings
                    threshold = METRIC_THRESHOLDS.get(metric, 90.0)
                    if current_value >= threshold:
                        warnings.append(f"{metric}: {current_value:.1f}% (threshold: {threshold}%)")

                    if forecast.will_cross_threshold:
                        hours = forecast.time_to_threshold_hours or 0
                        warnings.append(f"{metric}: Will reach {forecast.threshold}% in ~{hours:.0f} hours")

                # Check for anomalies
                anomaly = self._check_anomaly(metric, current_value, now, c)
                if anomaly and anomaly.is_anomaly:
                    anomalies.append(anomaly)

                # Update baseline
                self._update_baseline(metric, current_value, c)

            # Update aggregation tables
            self._store_aggregations(c)

            self._last_run = now

            return TrendContext(
                disk_trends=disk_trends,
                memory_trend=memory_trend,
                cpu_trend=cpu_trend,
                swap_trend=swap_trend,
                forecasts=forecasts,
                anomalies=tuple(anomalies),
                has_warnings=len(warnings) > 0,
                warning_messages=tuple(warnings),
                computed_at=now,
            )

    def _compute_trend_window(
        self,
        metric: str,
        current_value: float,
        now: datetime,
        conn: sqlite3.Connection,
    ) -> TrendWindow:
        """Compute trend window for a metric."""
        cursor = conn.cursor()

        windows = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
        }

        averages: dict[str, float | None] = {}
        sample_count = 0

        for window_name, delta in windows.items():
            cutoff = now - delta
            cursor.execute(
                """
                SELECT AVG(value), COUNT(*) FROM metric_samples
                WHERE metric = ? AND ts >= ?
                """,
                (metric, _serialize_datetime(cutoff)),
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                averages[window_name] = round(row[0], 2)
                sample_count = max(sample_count, row[1])
            else:
                averages[window_name] = None

        # Compute rate of change (using 1h and 6h averages)
        rate = 0.0
        avg_1h = averages.get("1h")
        avg_6h = averages.get("6h")
        if avg_1h is not None and avg_6h is not None:
            diff = avg_1h - avg_6h
            rate = diff / 5.0  # Change over 5 hours

        # Check for anomaly
        is_anomaly = False
        anomaly_score = None
        baseline = self._get_baseline(metric, conn)
        if baseline and baseline.samples >= self.config.anomaly_min_samples:
            if baseline.baseline_stddev > 0:
                z_score = abs(current_value - baseline.baseline_mean) / baseline.baseline_stddev
                anomaly_score = z_score
                is_anomaly = z_score > self.config.anomaly_z_threshold

        return TrendWindow(
            metric=metric,
            current=current_value,
            avg_1h=averages.get("1h"),
            avg_6h=averages.get("6h"),
            avg_24h=averages.get("24h"),
            avg_7d=averages.get("7d"),
            rate_of_change_per_hour=rate,
            is_anomaly=is_anomaly,
            anomaly_score=anomaly_score,
            sample_count=sample_count,
            computed_at=now,
        )

    def _compute_forecast(
        self,
        metric: str,
        trend: TrendWindow,
        conn: sqlite3.Connection,
    ) -> Forecast:
        """Compute forecast for a metric."""
        current = trend.current
        rate = trend.rate_of_change_per_hour

        # Simple linear extrapolation
        predicted_24h = current + (rate * 24)
        predicted_7d = current + (rate * 24 * 7)

        # Clamp to valid range
        predicted_24h = max(0, min(100, predicted_24h))
        predicted_7d = max(0, min(100, predicted_7d))

        # Check threshold
        threshold = CRITICAL_THRESHOLDS.get(metric)
        time_to_threshold = None
        will_cross = False

        if threshold and rate > 0:
            remaining = threshold - current
            if remaining > 0:
                time_to_threshold = remaining / rate
                will_cross = time_to_threshold <= 168  # 7 days

        # Confidence based on sample count and trend stability
        confidence = 0.5
        if trend.sample_count >= self.config.forecast_min_samples:
            confidence = min(0.9, 0.5 + (trend.sample_count / 100) * 0.4)

        return Forecast(
            metric=metric,
            current_value=current,
            predicted_value_24h=round(predicted_24h, 2),
            predicted_value_7d=round(predicted_7d, 2),
            threshold=threshold,
            time_to_threshold_hours=time_to_threshold,
            will_cross_threshold=will_cross,
            confidence=confidence,
            rate_of_change=rate,
        )

    def _check_anomaly(
        self,
        metric: str,
        current_value: float,
        now: datetime,
        conn: sqlite3.Connection,
    ) -> AnomalyResult | None:
        """Check if current value is anomalous."""
        baseline = self._get_baseline(metric, conn)
        if not baseline or baseline.samples < self.config.anomaly_min_samples:
            return None

        if baseline.baseline_stddev == 0:
            return None

        z_score = (current_value - baseline.baseline_mean) / baseline.baseline_stddev
        is_anomaly = abs(z_score) > self.config.anomaly_z_threshold

        direction: Literal["above", "below", "normal"]
        if z_score > 0:
            direction = "above"
        elif z_score < 0:
            direction = "below"
        else:
            direction = "normal"

        return AnomalyResult(
            metric=metric,
            current_value=current_value,
            is_anomaly=is_anomaly,
            anomaly_score=abs(z_score),
            threshold_used=self.config.anomaly_z_threshold,
            baseline_mean=baseline.baseline_mean,
            baseline_stddev=baseline.baseline_stddev,
            deviation_direction=direction,
            detected_at=now,
        )

    def _get_baseline(
        self,
        metric: str,
        conn: sqlite3.Connection,
    ) -> MetricBaseline | None:
        """Get baseline for a metric."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM metric_baselines WHERE metric = ?",
            (metric,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return MetricBaseline(
            metric=row["metric"],
            baseline_mean=row["baseline_mean"],
            baseline_stddev=row["baseline_stddev"],
            samples=row["samples"],
            updated_at=_parse_datetime(row["updated_at"]),
        )

    def _update_baseline(
        self,
        metric: str,
        current_value: float,
        conn: sqlite3.Connection,
    ) -> None:
        """Update baseline using exponential moving average."""
        cursor = conn.cursor()

        existing = self._get_baseline(metric, conn)

        if existing:
            # EMA update
            alpha = 0.01  # Slow adaptation
            new_mean = alpha * current_value + (1 - alpha) * existing.baseline_mean

            # Welford's algorithm for variance
            diff = current_value - existing.baseline_mean
            new_samples = existing.samples + 1
            new_stddev = (
                (existing.baseline_stddev**2 * (new_samples - 1) + diff * (current_value - new_mean)) / new_samples
            ) ** 0.5

            cursor.execute(
                """
                UPDATE metric_baselines
                SET baseline_mean = ?, baseline_stddev = ?, samples = ?, updated_at = ?
                WHERE metric = ?
                """,
                (new_mean, new_stddev, new_samples, _serialize_datetime(datetime.utcnow()), metric),
            )
        else:
            # First sample
            cursor.execute(
                """
                INSERT INTO metric_baselines (metric, baseline_mean, baseline_stddev, samples, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (metric, current_value, 0.0, 1, _serialize_datetime(datetime.utcnow())),
            )

        conn.commit()

    def _store_aggregations(
        self,
        conn: sqlite3.Connection,
    ) -> None:
        """Store computed aggregations in database."""
        cursor = conn.cursor()
        now = datetime.utcnow()

        for metric in TRACKED_METRICS:
            for window, delta in [
                ("1h", timedelta(hours=1)),
                ("6h", timedelta(hours=6)),
                ("24h", timedelta(hours=24)),
                ("7d", timedelta(days=7)),
            ]:
                cutoff = now - delta
                cursor.execute(
                    """
                    SELECT AVG(value), MIN(value), MAX(value), COUNT(*)
                    FROM metric_samples
                    WHERE metric = ? AND ts >= ?
                    """,
                    (metric, _serialize_datetime(cutoff)),
                )
                row = cursor.fetchone()
                if row and row[0] is not None:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO metric_aggregations
                        (metric, window, avg_value, min_value, max_value, sample_count, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (metric, window, row[0], row[1], row[2], row[3], _serialize_datetime(now)),
                    )

        conn.commit()


# =============================================================================
# Public API
# =============================================================================


def get_trend_context(
    conn: sqlite3.Connection | None = None,
) -> TrendContext:
    """Get current trend context.

    Runs aggregation if needed and returns current trends.

    Args:
        conn: SQLite connection.

    Returns:
        TrendContext with current trends.
    """
    aggregator = TrendAggregator()
    return aggregator.run_aggregation_cycle(conn=conn)


def get_metric_trend(
    metric: str,
    conn: sqlite3.Connection | None = None,
) -> TrendWindow | None:
    """Get trend for a specific metric.

    Args:
        metric: Metric name.
        conn: SQLite connection.

    Returns:
        TrendWindow if metric exists, None otherwise.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_schema(c)
            ensure_aggregation_schema(c)

        cursor = c.cursor()

        # Get current value
        cursor.execute(
            """
            SELECT value FROM metric_samples
            WHERE metric = ?
            ORDER BY ts DESC LIMIT 1
            """,
            (metric,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        current: float = row["value"]

        # Get aggregations
        cursor.execute(
            """
            SELECT window, avg_value FROM metric_aggregations
            WHERE metric = ?
            """,
            (metric,),
        )
        aggregations = {row["window"]: row["avg_value"] for row in cursor.fetchall()}

        return TrendWindow(
            metric=metric,
            current=current,
            avg_1h=aggregations.get("1h"),
            avg_6h=aggregations.get("6h"),
            avg_24h=aggregations.get("24h"),
            avg_7d=aggregations.get("7d"),
        )


def cleanup_old_samples(
    retention_hours: int = 168,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Clean up old metric samples.

    Args:
        retention_hours: Hours of samples to retain.
        conn: SQLite connection.

    Returns:
        Number of rows deleted.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_schema(c)
            ensure_aggregation_schema(c)

        cutoff = datetime.utcnow() - timedelta(hours=retention_hours)
        cursor = c.cursor()
        cursor.execute(
            "DELETE FROM metric_samples WHERE ts < ?",
            (_serialize_datetime(cutoff),),
        )
        deleted: int = cursor.rowcount
        c.commit()
        return deleted
