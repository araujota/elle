"""Trend aggregation for predictive capabilities.

Runs periodically to compute rolling aggregates, detect anomalies,
and generate forecasts from telemetry data.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Literal

import psycopg

from elle.daemon.telemetry.trends import (
    CRITICAL_THRESHOLDS,
    METRIC_THRESHOLDS,
    TRACKED_METRICS,
    AnomalyResult,
    CorrelationAlert,
    Forecast,
    MetricBaseline,
    TrendConfig,
    TrendContext,
    TrendWindow,
)
from elle.storage.engine import get_conn
from elle.storage.helpers import parse_datetime, serialize_datetime


@contextmanager
def _ensure_connection(
    conn: psycopg.Connection | None = None,
) -> Iterator[psycopg.Connection]:
    """Context manager that ensures a valid connection.

    If *conn* is provided, yields it directly (caller owns lifecycle).
    Otherwise, obtains a pooled connection via ``get_conn``.
    """
    if conn is not None:
        yield conn
    else:
        with get_conn(schema="telemetry") as pooled:
            yield pooled


# =============================================================================
# Schema for aggregations
# =============================================================================


METRIC_SAMPLES_TABLE = """
CREATE TABLE IF NOT EXISTS metric_samples (
    id SERIAL PRIMARY KEY,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    ts TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (NOW()::TEXT)
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


def ensure_aggregation_schema(conn: psycopg.Connection) -> None:
    """Ensure aggregation tables exist."""
    cursor = conn.cursor()
    cursor.execute(METRIC_SAMPLES_TABLE)
    cursor.execute(METRIC_AGGREGATIONS_TABLE)
    cursor.execute(METRIC_BASELINES_TABLE)
    for index in AGGREGATION_INDEXES:
        cursor.execute(index)
    conn.commit()


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

    # New metrics (monitoring sprint)
    try:
        # Inode usage
        for mount in ["/", "/var"]:
            try:
                stat = os.statvfs(mount)
                total = stat.f_files
                free = stat.f_ffree
                if total > 0:
                    inode_pct = ((total - free) / total) * 100
                    key = f"disk.{mount}.inode_pct".replace("//", "/")
                    metrics[key] = round(inode_pct, 2)
            except (OSError, FileNotFoundError):
                pass

        # TCP state counts from /proc/net/tcp
        try:
            tcp_states: dict[str, int] = {}
            with open("/proc/net/tcp") as f:
                next(f)
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4:
                        state = parts[3].upper()
                        tcp_states[state] = tcp_states.get(state, 0) + 1
            metrics["net.tcp_time_wait"] = float(tcp_states.get("06", 0))
            metrics["net.tcp_close_wait"] = float(tcp_states.get("08", 0))
        except (OSError, FileNotFoundError):
            pass

        # TCP retransmit rate from /proc/net/snmp
        try:
            with open("/proc/net/snmp") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if line.startswith("Tcp:") and i + 1 < len(lines):
                    headers = line.split()
                    values = lines[i + 1].split()
                    if len(headers) == len(values):
                        data = dict(zip(headers[1:], values[1:], strict=False))
                        out_segs = int(data.get("OutSegs", "0"))
                        retrans = int(data.get("RetransSegs", "0"))
                        if out_segs > 0:
                            metrics["net.tcp_retransmit_rate"] = round(retrans / out_segs, 6)
                    break
        except (OSError, FileNotFoundError, ValueError):
            pass

        # Temperature
        try:
            import glob

            max_temp = 0
            for zone in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
                try:
                    with open(zone) as f:
                        temp = int(f.read().strip()) // 1000
                        max_temp = max(max_temp, temp)
                except (OSError, ValueError):
                    pass
            if max_temp > 0:
                metrics["thermal.max_temp_c"] = float(max_temp)
        except Exception:
            pass

        # Zombie/process count
        try:
            from pathlib import Path

            zombie_count = 0
            total_count = 0
            proc_dir = Path("/proc")
            for entry in proc_dir.iterdir():
                if entry.name.isdigit():
                    total_count += 1
                    try:
                        content = (entry / "stat").read_text()
                        close_paren = content.rfind(")")
                        if close_paren >= 0 and len(content) > close_paren + 2:
                            if content[close_paren + 2] == "Z":
                                zombie_count += 1
                    except (OSError, PermissionError):
                        pass
            metrics["proc.zombie_count"] = float(zombie_count)
            metrics["proc.total_count"] = float(total_count)
        except Exception:
            pass

        # PSI pressure
        try:
            from pathlib import Path

            for resource, key in [("cpu", "psi.cpu_avg10"), ("memory", "psi.memory_avg10")]:
                psi_path = Path(f"/proc/pressure/{resource}")
                if psi_path.exists():
                    content = psi_path.read_text()
                    for line in content.split("\n"):
                        if line.startswith("some"):
                            for part in line.split():
                                if part.startswith("avg10="):
                                    metrics[key] = float(part.split("=")[1])
                                    break
                            break
        except (OSError, ValueError):
            pass

    except Exception:
        pass

    return metrics


def record_metric(
    metric: str,
    value: float,
    ts: datetime | None = None,
    conn: psycopg.Connection | None = None,
) -> None:
    """Record a metric sample.

    Args:
        metric: Metric name.
        value: Metric value.
        ts: Timestamp (defaults to now).
        conn: PostgreSQL connection.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_aggregation_schema(c)

        if ts is None:
            ts = datetime.utcnow()

        cursor = c.cursor()
        cursor.execute(
            """
            INSERT INTO metric_samples (metric, value, ts)
            VALUES (%s, %s, %s)
            """,
            (metric, value, serialize_datetime(ts)),
        )
        c.commit()


def record_metrics_batch(
    metrics: dict[str, float],
    ts: datetime | None = None,
    conn: psycopg.Connection | None = None,
) -> None:
    """Record multiple metrics at once.

    Args:
        metrics: Dict of metric name to value.
        ts: Timestamp for all metrics.
        conn: PostgreSQL connection.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_aggregation_schema(c)

        if ts is None:
            ts = datetime.utcnow()

        ts_str = serialize_datetime(ts)
        cursor = c.cursor()

        for metric, value in metrics.items():
            cursor.execute(
                """
                INSERT INTO metric_samples (metric, value, ts)
                VALUES (%s, %s, %s)
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
        conn: psycopg.Connection | None = None,
    ) -> TrendContext:
        """Run a complete aggregation cycle.

        Collects metrics, computes aggregations, detects anomalies,
        and generates forecasts.

        Args:
            conn: PostgreSQL connection.

        Returns:
            TrendContext with current trends.
        """
        with _ensure_connection(conn) as c:
            if conn is None:
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

            # Run correlation detection
            correlation_alerts_list = self._run_correlations(current_metrics, c)
            for alert in correlation_alerts_list:
                warnings.append(f"[{alert.severity}] {alert.signature}: {alert.description}")

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
                correlation_alerts=tuple(correlation_alerts_list),
                computed_at=now,
            )

    def _compute_trend_window(
        self,
        metric: str,
        current_value: float,
        now: datetime,
        conn: psycopg.Connection,
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
                WHERE metric = %s AND ts >= %s
                """,
                (metric, serialize_datetime(cutoff)),
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
        conn: psycopg.Connection,
    ) -> Forecast:
        """Compute forecast for a metric using best available method."""
        current = trend.current
        rate = trend.rate_of_change_per_hour

        # Try advanced forecasting via ModelSelector
        method = None
        mape = None
        seasonal_detected = False
        predicted_24h = current + (rate * 24)
        predicted_7d = current + (rate * 24 * 7)

        try:
            from elle.daemon.telemetry.model_selector import ModelSelector

            selector = ModelSelector()
            # Get raw samples for advanced forecasting
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM metric_samples WHERE metric = %s ORDER BY ts ASC",
                (metric,),
            )
            values = [row[0] for row in cursor.fetchall()]
            if len(values) >= 12:
                result = selector.forecast(
                    metric,
                    values,
                    current,
                    trend.avg_1h or current,
                    trend.avg_6h or current,
                )
                if result:
                    predicted_24h = result.predicted_24h
                    predicted_7d = result.predicted_7d
                    rate = result.rate_of_change
                    method = result.method
                    mape = result.mape
                    seasonal_detected = result.seasonal_detected
        except Exception:
            pass  # Fall back to linear

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
        # Boost confidence if we have MAPE data
        if mape is not None and mape < 0.1:
            confidence = min(0.95, confidence + 0.1)

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
            method=method,
            mape=mape,
            seasonal_detected=seasonal_detected,
        )

    def _check_anomaly(
        self,
        metric: str,
        current_value: float,
        now: datetime,
        conn: psycopg.Connection,
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
        conn: psycopg.Connection,
    ) -> MetricBaseline | None:
        """Get baseline for a metric."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM metric_baselines WHERE metric = %s",
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
            updated_at=parse_datetime(row["updated_at"]),
        )

    def _update_baseline(
        self,
        metric: str,
        current_value: float,
        conn: psycopg.Connection,
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
                SET baseline_mean = %s, baseline_stddev = %s, samples = %s, updated_at = %s
                WHERE metric = %s
                """,
                (new_mean, new_stddev, new_samples, serialize_datetime(datetime.utcnow()), metric),
            )
        else:
            # First sample
            cursor.execute(
                """
                INSERT INTO metric_baselines (metric, baseline_mean, baseline_stddev, samples, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (metric, current_value, 0.0, 1, serialize_datetime(datetime.utcnow())),
            )

        conn.commit()

    def _run_correlations(
        self,
        metrics: dict[str, float],
        conn: psycopg.Connection,
    ) -> list[CorrelationAlert]:
        """Run multi-variate correlation detection."""
        try:
            from elle.daemon.telemetry.correlator_engine import (
                CorrelationAlert as EngineAlert,
            )
            from elle.daemon.telemetry.correlator_engine import (
                CorrelationEngine,
            )

            engine = CorrelationEngine()
            engine_alerts: list[EngineAlert] = engine.evaluate(metrics, {})
            # Convert from correlator_engine.CorrelationAlert to trends.CorrelationAlert
            return [
                CorrelationAlert(
                    signature=a.signature,
                    severity=a.severity,
                    description=a.description,
                    contributing_metrics=a.contributing_metrics,
                    recommended_action=a.recommended_action,
                )
                for a in engine_alerts
            ]
        except Exception:
            return []

    def _store_aggregations(
        self,
        conn: psycopg.Connection,
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
                    WHERE metric = %s AND ts >= %s
                    """,
                    (metric, serialize_datetime(cutoff)),
                )
                row = cursor.fetchone()
                if row and row[0] is not None:
                    cursor.execute(
                        """
                        INSERT INTO metric_aggregations
                        (metric, window, avg_value, min_value, max_value, sample_count, computed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (metric, window)
                        DO UPDATE SET avg_value = EXCLUDED.avg_value,
                                      min_value = EXCLUDED.min_value,
                                      max_value = EXCLUDED.max_value,
                                      sample_count = EXCLUDED.sample_count,
                                      computed_at = EXCLUDED.computed_at
                        """,
                        (metric, window, row[0], row[1], row[2], row[3], serialize_datetime(now)),
                    )

        conn.commit()


# =============================================================================
# Public API
# =============================================================================


def get_trend_context(
    conn: psycopg.Connection | None = None,
) -> TrendContext:
    """Get current trend context.

    Runs aggregation if needed and returns current trends.

    Args:
        conn: PostgreSQL connection.

    Returns:
        TrendContext with current trends.
    """
    aggregator = TrendAggregator()
    return aggregator.run_aggregation_cycle(conn=conn)


def get_metric_trend(
    metric: str,
    conn: psycopg.Connection | None = None,
) -> TrendWindow | None:
    """Get trend for a specific metric.

    Args:
        metric: Metric name.
        conn: PostgreSQL connection.

    Returns:
        TrendWindow if metric exists, None otherwise.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_aggregation_schema(c)

        cursor = c.cursor()

        # Get current value
        cursor.execute(
            """
            SELECT value FROM metric_samples
            WHERE metric = %s
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
            WHERE metric = %s
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
    conn: psycopg.Connection | None = None,
) -> int:
    """Clean up old metric samples.

    Args:
        retention_hours: Hours of samples to retain.
        conn: PostgreSQL connection.

    Returns:
        Number of rows deleted.
    """
    with _ensure_connection(conn) as c:
        if conn is None:
            ensure_aggregation_schema(c)

        cutoff = datetime.utcnow() - timedelta(hours=retention_hours)
        cursor = c.cursor()
        cursor.execute(
            "DELETE FROM metric_samples WHERE ts < %s",
            (serialize_datetime(cutoff),),
        )
        deleted: int = cursor.rowcount
        c.commit()
        return deleted
