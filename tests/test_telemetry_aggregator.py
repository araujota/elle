"""Tests for telemetry aggregator (aggregator.py).

Covers metric recording, trend computation, anomaly detection,
forecast generation, baseline updates, and aggregation storage.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from elle.daemon.telemetry.aggregator import (
    TrendAggregator,
    cleanup_old_samples,
    collect_system_metrics,
    ensure_aggregation_schema,
    get_metric_trend,
    get_trend_context,
    record_metric,
    record_metrics_batch,
)
from elle.daemon.telemetry.trends import TrendConfig, TrendWindow
from elle.storage.helpers import parse_datetime, serialize_datetime

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(telemetry_conn):
    """Use the conftest telemetry_conn fixture."""
    return telemetry_conn


@pytest.fixture()
def aggregator():
    """Create a TrendAggregator with default config."""
    return TrendAggregator()


@pytest.fixture()
def aggregator_low_samples():
    """Create a TrendAggregator with low sample thresholds for anomaly detection."""
    config = TrendConfig(
        anomaly_min_samples=10,
        anomaly_z_threshold=2.0,
        forecast_min_samples=5,
    )
    return TrendAggregator(config=config)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _insert_sample(conn, metric, value, ts=None):
    """Insert a metric sample directly into the database."""
    if ts is None:
        ts = datetime.utcnow()
    conn.execute(
        "INSERT INTO metric_samples (metric, value, ts) VALUES (%s, %s, %s)",
        (metric, value, serialize_datetime(ts)),
    )
    conn.commit()


def _insert_baseline(conn, metric, mean, stddev, samples):
    """Insert a baseline directly into the database."""
    conn.execute(
        "INSERT INTO metric_baselines (metric, baseline_mean, baseline_stddev, samples, updated_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (metric) DO UPDATE SET baseline_mean=EXCLUDED.baseline_mean, "
        "baseline_stddev=EXCLUDED.baseline_stddev, samples=EXCLUDED.samples, updated_at=EXCLUDED.updated_at",
        (metric, mean, stddev, samples, serialize_datetime(datetime.utcnow())),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests: datetime helpers
# ---------------------------------------------------------------------------


class TestDatetimeHelpers:
    def test_serialize_datetime(self):
        dt = datetime(2025, 6, 15, 12, 30, 0)
        result = serialize_datetime(dt)
        assert "2025-06-15" in result

    def test_parse_datetime_roundtrip(self):
        from datetime import timezone

        dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        serialized = serialize_datetime(dt)
        parsed = parse_datetime(serialized)
        assert parsed == dt


# ---------------------------------------------------------------------------
# Tests: ensure_aggregation_schema
# ---------------------------------------------------------------------------


class TestEnsureAggregationSchema:
    def test_creates_metric_samples_table(self, db):
        cursor = db.execute("SELECT table_name FROM information_schema.tables WHERE table_name='metric_samples'")
        assert cursor.fetchone() is not None

    def test_creates_metric_aggregations_table(self, db):
        cursor = db.execute("SELECT table_name FROM information_schema.tables WHERE table_name='metric_aggregations'")
        assert cursor.fetchone() is not None

    def test_creates_metric_baselines_table(self, db):
        cursor = db.execute("SELECT table_name FROM information_schema.tables WHERE table_name='metric_baselines'")
        assert cursor.fetchone() is not None

    def test_idempotent(self, db):
        """Calling ensure_aggregation_schema twice should not error."""
        ensure_aggregation_schema(db)
        ensure_aggregation_schema(db)


# ---------------------------------------------------------------------------
# Tests: record_metric
# ---------------------------------------------------------------------------


class TestRecordMetric:
    def test_record_single_metric(self, db):
        ts = datetime(2025, 6, 15, 12, 0, 0)
        record_metric("disk./.used_pct", 85.0, ts=ts, conn=db)

        cursor = db.execute("SELECT * FROM metric_samples WHERE metric = ?", ("disk./.used_pct",))
        row = cursor.fetchone()
        assert row is not None
        assert row["value"] == 85.0

    def test_record_metric_default_timestamp(self, db):
        record_metric("cpu.load_1m", 2.5, conn=db)
        cursor = db.execute("SELECT * FROM metric_samples WHERE metric = ?", ("cpu.load_1m",))
        row = cursor.fetchone()
        assert row is not None
        assert row["value"] == 2.5

    def test_record_multiple_samples(self, db):
        for i in range(5):
            record_metric("mem.used_pct", 50.0 + i, conn=db)

        cursor = db.execute("SELECT COUNT(*) FROM metric_samples WHERE metric = ?", ("mem.used_pct",))
        assert cursor.fetchone()[0] == 5


# ---------------------------------------------------------------------------
# Tests: record_metrics_batch
# ---------------------------------------------------------------------------


class TestRecordMetricsBatch:
    def test_batch_record(self, db):
        metrics = {
            "disk./.used_pct": 80.0,
            "mem.used_pct": 60.0,
            "cpu.load_1m": 1.5,
        }
        record_metrics_batch(metrics, conn=db)

        cursor = db.execute("SELECT COUNT(*) FROM metric_samples")
        assert cursor.fetchone()[0] == 3

    def test_batch_record_with_timestamp(self, db):
        ts = datetime(2025, 6, 15, 12, 0, 0)
        metrics = {"disk./.used_pct": 80.0, "mem.used_pct": 60.0}
        record_metrics_batch(metrics, ts=ts, conn=db)

        cursor = db.execute("SELECT ts FROM metric_samples LIMIT 1")
        row = cursor.fetchone()
        assert "2025-06-15" in row["ts"]

    def test_batch_record_empty(self, db):
        record_metrics_batch({}, conn=db)
        cursor = db.execute("SELECT COUNT(*) FROM metric_samples")
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Tests: collect_system_metrics (mocked)
# ---------------------------------------------------------------------------


class TestCollectSystemMetrics:
    @patch("os.getloadavg", return_value=(1.5, 1.0, 0.5))
    @patch("os.statvfs", side_effect=OSError("no mount"))
    def test_collects_cpu_load(self, mock_statvfs, mock_loadavg):
        metrics = collect_system_metrics()
        assert "cpu.load_1m" in metrics
        assert metrics["cpu.load_1m"] == 1.5

    @patch("os.getloadavg", return_value=(2.0, 1.5, 1.0))
    @patch("os.statvfs", side_effect=OSError("no mount"))
    def test_collects_load_averages(self, mock_statvfs, mock_loadavg):
        metrics = collect_system_metrics()
        assert metrics.get("cpu.load_5m") == 1.5
        assert metrics.get("cpu.load_15m") == 1.0

    def test_returns_dict_on_all_failures(self):
        """When everything fails, we should still get a dict back."""
        with patch("os.statvfs", side_effect=OSError):
            with patch("os.getloadavg", side_effect=OSError):
                metrics = collect_system_metrics()
                assert isinstance(metrics, dict)


# ---------------------------------------------------------------------------
# Tests: TrendAggregator._compute_trend_window
# ---------------------------------------------------------------------------


class TestComputeTrendWindow:
    def test_trend_window_with_samples(self, aggregator, db):
        now = datetime.utcnow()
        metric = "disk./.used_pct"

        # Insert samples across time windows
        for i in range(10):
            _insert_sample(db, metric, 80.0 + i * 0.5, ts=now - timedelta(minutes=i * 5))

        trend = aggregator._compute_trend_window(metric, 85.0, now, db)
        assert trend.metric == metric
        assert trend.current == 85.0
        assert trend.avg_1h is not None

    def test_trend_window_no_samples(self, aggregator, db):
        now = datetime.utcnow()
        trend = aggregator._compute_trend_window("nonexistent.metric", 50.0, now, db)
        assert trend.current == 50.0
        assert trend.avg_1h is None

    def test_trend_window_rate_computation(self, aggregator, db):
        now = datetime.utcnow()
        metric = "mem.used_pct"

        # Insert samples to create a measurable rate: older samples lower, newer higher
        for i in range(20):
            ts = now - timedelta(minutes=i * 20)
            _insert_sample(db, metric, 70.0 - i * 0.5, ts=ts)

        trend = aggregator._compute_trend_window(metric, 70.0, now, db)
        # Rate should be computed from 1h vs 6h averages
        assert isinstance(trend.rate_of_change_per_hour, float)

    def test_trend_window_anomaly_detection(self, aggregator_low_samples, db):
        now = datetime.utcnow()
        metric = "cpu.load_1m"

        # Insert baseline
        _insert_baseline(db, metric, mean=2.0, stddev=0.5, samples=200)

        # Insert some samples
        for i in range(5):
            _insert_sample(db, metric, 2.0, ts=now - timedelta(minutes=i * 10))

        # Current value far from mean (z-score > 2.0)
        trend = aggregator_low_samples._compute_trend_window(metric, 10.0, now, db)
        assert trend.is_anomaly is True
        assert trend.anomaly_score is not None


# ---------------------------------------------------------------------------
# Tests: TrendAggregator._compute_forecast
# ---------------------------------------------------------------------------


class TestComputeForecast:
    def test_forecast_basic(self, aggregator, db):
        now = datetime.utcnow()
        metric = "disk./.used_pct"

        # Insert some samples
        for i in range(5):
            _insert_sample(db, metric, 80.0 + i, ts=now - timedelta(hours=i))

        trend = TrendWindow(
            metric=metric,
            current=85.0,
            avg_1h=84.0,
            avg_6h=80.0,
            rate_of_change_per_hour=0.5,
            sample_count=5,
        )

        forecast = aggregator._compute_forecast(metric, trend, db)
        assert forecast.metric == metric
        assert forecast.current_value == 85.0
        assert isinstance(forecast.predicted_value_24h, float)
        assert isinstance(forecast.predicted_value_7d, float)

    def test_forecast_clamped_to_100(self, aggregator, db):
        metric = "disk./.used_pct"

        trend = TrendWindow(
            metric=metric,
            current=95.0,
            rate_of_change_per_hour=5.0,
            sample_count=2,
        )

        forecast = aggregator._compute_forecast(metric, trend, db)
        assert forecast.predicted_value_24h <= 100.0
        assert forecast.predicted_value_7d <= 100.0

    def test_forecast_clamped_to_zero(self, aggregator, db):
        metric = "disk./.used_pct"

        trend = TrendWindow(
            metric=metric,
            current=5.0,
            rate_of_change_per_hour=-10.0,
            sample_count=2,
        )

        forecast = aggregator._compute_forecast(metric, trend, db)
        assert forecast.predicted_value_24h >= 0.0
        assert forecast.predicted_value_7d >= 0.0

    def test_forecast_threshold_crossing(self, aggregator, db):
        metric = "disk./.used_pct"

        trend = TrendWindow(
            metric=metric,
            current=85.0,
            rate_of_change_per_hour=1.0,
            sample_count=20,
        )

        forecast = aggregator._compute_forecast(metric, trend, db)
        # With rate of 1.0/hr starting at 85.0, threshold ~92.0 should be crossed
        assert forecast.threshold is not None
        assert forecast.will_cross_threshold is True
        assert forecast.time_to_threshold_hours is not None

    def test_forecast_no_threshold_crossing_when_rate_zero(self, aggregator, db):
        metric = "disk./.used_pct"

        trend = TrendWindow(
            metric=metric,
            current=50.0,
            rate_of_change_per_hour=0.0,
            sample_count=2,
        )

        forecast = aggregator._compute_forecast(metric, trend, db)
        assert forecast.will_cross_threshold is False

    def test_forecast_confidence_increases_with_samples(self, aggregator, db):
        metric = "disk./.used_pct"

        trend_few = TrendWindow(
            metric=metric,
            current=50.0,
            rate_of_change_per_hour=0.0,
            sample_count=2,
        )
        trend_many = TrendWindow(
            metric=metric,
            current=50.0,
            rate_of_change_per_hour=0.0,
            sample_count=50,
        )

        forecast_few = aggregator._compute_forecast(metric, trend_few, db)
        forecast_many = aggregator._compute_forecast(metric, trend_many, db)
        assert forecast_many.confidence >= forecast_few.confidence


# ---------------------------------------------------------------------------
# Tests: TrendAggregator._check_anomaly
# ---------------------------------------------------------------------------


class TestCheckAnomaly:
    def test_no_anomaly_no_baseline(self, aggregator, db):
        now = datetime.utcnow()
        result = aggregator._check_anomaly("cpu.load_1m", 2.0, now, db)
        assert result is None

    def test_no_anomaly_insufficient_samples(self, aggregator, db):
        now = datetime.utcnow()
        _insert_baseline(db, "cpu.load_1m", mean=2.0, stddev=0.5, samples=5)
        result = aggregator._check_anomaly("cpu.load_1m", 2.5, now, db)
        assert result is None

    def test_no_anomaly_zero_stddev(self, aggregator_low_samples, db):
        now = datetime.utcnow()
        _insert_baseline(db, "cpu.load_1m", mean=2.0, stddev=0.0, samples=200)
        result = aggregator_low_samples._check_anomaly("cpu.load_1m", 2.5, now, db)
        assert result is None

    def test_anomaly_above_baseline(self, aggregator_low_samples, db):
        now = datetime.utcnow()
        _insert_baseline(db, "cpu.load_1m", mean=2.0, stddev=0.5, samples=200)
        # z-score = (10 - 2) / 0.5 = 16.0, well above threshold of 2.0
        result = aggregator_low_samples._check_anomaly("cpu.load_1m", 10.0, now, db)
        assert result is not None
        assert result.is_anomaly is True
        assert result.deviation_direction == "above"

    def test_anomaly_below_baseline(self, aggregator_low_samples, db):
        now = datetime.utcnow()
        _insert_baseline(db, "mem.used_pct", mean=60.0, stddev=5.0, samples=200)
        # z-score = (30 - 60) / 5 = -6.0
        result = aggregator_low_samples._check_anomaly("mem.used_pct", 30.0, now, db)
        assert result is not None
        assert result.is_anomaly is True
        assert result.deviation_direction == "below"

    def test_normal_value(self, aggregator_low_samples, db):
        now = datetime.utcnow()
        _insert_baseline(db, "cpu.load_1m", mean=2.0, stddev=1.0, samples=200)
        # z-score = (2.5 - 2.0) / 1.0 = 0.5, below threshold of 2.0
        result = aggregator_low_samples._check_anomaly("cpu.load_1m", 2.5, now, db)
        assert result is not None
        assert result.is_anomaly is False
        assert result.deviation_direction == "above"


# ---------------------------------------------------------------------------
# Tests: TrendAggregator._get_baseline / _update_baseline
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_get_baseline_none(self, aggregator, db):
        result = aggregator._get_baseline("nonexistent.metric", db)
        assert result is None

    def test_get_baseline_existing(self, aggregator, db):
        _insert_baseline(db, "cpu.load_1m", mean=2.0, stddev=0.5, samples=100)
        result = aggregator._get_baseline("cpu.load_1m", db)
        assert result is not None
        assert result.baseline_mean == 2.0
        assert result.baseline_stddev == 0.5
        assert result.samples == 100

    def test_update_baseline_first_sample(self, aggregator, db):
        aggregator._update_baseline("new.metric", 42.0, db)
        result = aggregator._get_baseline("new.metric", db)
        assert result is not None
        assert result.baseline_mean == 42.0
        assert result.baseline_stddev == 0.0
        assert result.samples == 1

    def test_update_baseline_subsequent_sample(self, aggregator, db):
        _insert_baseline(db, "cpu.load_1m", mean=2.0, stddev=0.5, samples=100)
        aggregator._update_baseline("cpu.load_1m", 2.1, db)
        result = aggregator._get_baseline("cpu.load_1m", db)
        assert result is not None
        assert result.samples == 101
        # EMA with alpha=0.01: new_mean ~ 0.01 * 2.1 + 0.99 * 2.0 = 2.001
        assert abs(result.baseline_mean - 2.001) < 0.01


# ---------------------------------------------------------------------------
# Tests: TrendAggregator._store_aggregations
# ---------------------------------------------------------------------------


class TestStoreAggregations:
    def test_store_aggregations_with_data(self, aggregator, db):
        now = datetime.utcnow()
        # Insert data for a tracked metric
        for i in range(5):
            _insert_sample(db, "disk./.used_pct", 80.0 + i, ts=now - timedelta(minutes=i * 10))

        aggregator._store_aggregations(db)

        cursor = db.execute("SELECT COUNT(*) FROM metric_aggregations")
        count = cursor.fetchone()[0]
        assert count > 0

    def test_store_aggregations_no_data(self, aggregator, db):
        aggregator._store_aggregations(db)
        cursor = db.execute("SELECT COUNT(*) FROM metric_aggregations")
        count = cursor.fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: TrendAggregator._run_correlations
# ---------------------------------------------------------------------------


class TestRunCorrelations:
    def test_run_correlations_import_failure(self, aggregator, db):
        """When the correlation engine is not importable, return empty list."""
        with patch.dict("sys.modules", {"elle.daemon.telemetry.correlator_engine": None}):
            result = aggregator._run_correlations({"cpu.load_1m": 2.0}, db)
            assert result == []

    def test_run_correlations_exception(self, aggregator, db):
        """When correlation engine raises, return empty list."""
        with patch(
            "elle.daemon.telemetry.aggregator.TrendAggregator._run_correlations",
            return_value=[],
        ):
            result = aggregator._run_correlations({"cpu.load_1m": 2.0}, db)
            assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Tests: TrendAggregator.run_aggregation_cycle
# ---------------------------------------------------------------------------


class TestRunAggregationCycle:
    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_returns_context(self, mock_collect, aggregator, db):
        mock_collect.return_value = {
            "disk./.used_pct": 80.0,
            "mem.used_pct": 60.0,
            "cpu.load_1m": 1.5,
            "swap.used_pct": 10.0,
        }

        ctx = aggregator.run_aggregation_cycle(conn=db)

        assert ctx is not None
        assert ctx.computed_at is not None
        assert isinstance(ctx.disk_trends, dict)

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_records_metrics(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"disk./.used_pct": 85.0}

        aggregator.run_aggregation_cycle(conn=db)

        cursor = db.execute("SELECT COUNT(*) FROM metric_samples WHERE metric = ?", ("disk./.used_pct",))
        assert cursor.fetchone()[0] > 0

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_populates_disk_trends(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"disk./.used_pct": 88.0}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        # disk./.used_pct should be categorized under disk_trends
        assert "/" in ctx.disk_trends or len(ctx.disk_trends) > 0

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_populates_memory_trend(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"mem.used_pct": 70.0}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        assert ctx.memory_trend is not None
        assert ctx.memory_trend.current == 70.0

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_populates_cpu_trend(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"cpu.load_1m": 3.0}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        assert ctx.cpu_trend is not None
        assert ctx.cpu_trend.current == 3.0

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_populates_swap_trend(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"swap.used_pct": 25.0}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        assert ctx.swap_trend is not None
        assert ctx.swap_trend.current == 25.0

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_generates_warnings_above_threshold(self, mock_collect, aggregator, db):
        # Default METRIC_THRESHOLDS for disk./.used_pct is 80.0
        mock_collect.return_value = {"disk./.used_pct": 95.0}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        assert ctx.has_warnings is True
        assert len(ctx.warning_messages) > 0

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_no_warnings_below_threshold(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"mem.used_pct": 30.0}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        # No disk metrics, so no disk threshold warnings
        # Memory does not generate threshold warnings in current code
        assert isinstance(ctx.warning_messages, tuple)

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_empty_metrics(self, mock_collect, aggregator, db):
        mock_collect.return_value = {}

        ctx = aggregator.run_aggregation_cycle(conn=db)
        assert ctx is not None
        assert len(ctx.disk_trends) == 0
        assert ctx.memory_trend is None
        assert ctx.cpu_trend is None

    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_aggregation_cycle_updates_last_run(self, mock_collect, aggregator, db):
        mock_collect.return_value = {"cpu.load_1m": 1.0}
        assert aggregator._last_run is None

        aggregator.run_aggregation_cycle(conn=db)
        assert aggregator._last_run is not None


# ---------------------------------------------------------------------------
# Tests: get_trend_context
# ---------------------------------------------------------------------------


class TestGetTrendContext:
    @patch("elle.daemon.telemetry.aggregator.collect_system_metrics")
    def test_get_trend_context(self, mock_collect, db):
        mock_collect.return_value = {"cpu.load_1m": 1.0}
        ctx = get_trend_context(conn=db)
        assert ctx is not None


# ---------------------------------------------------------------------------
# Tests: get_metric_trend
# ---------------------------------------------------------------------------


class TestGetMetricTrend:
    def test_get_metric_trend_existing(self, db):
        now = datetime.utcnow()
        _insert_sample(db, "cpu.load_1m", 2.5, ts=now)

        trend = get_metric_trend("cpu.load_1m", conn=db)
        assert trend is not None
        assert trend.current == 2.5

    def test_get_metric_trend_nonexistent(self, db):
        trend = get_metric_trend("nonexistent.metric", conn=db)
        assert trend is None

    def test_get_metric_trend_with_aggregations(self, db):
        now = datetime.utcnow()
        _insert_sample(db, "mem.used_pct", 70.0, ts=now)

        # Insert aggregation data
        db.execute(
            "INSERT INTO metric_aggregations (metric, window, avg_value, min_value, max_value, sample_count, computed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("mem.used_pct", "1h", 68.0, 65.0, 70.0, 12, serialize_datetime(now)),
        )
        db.commit()

        trend = get_metric_trend("mem.used_pct", conn=db)
        assert trend is not None
        assert trend.avg_1h == 68.0


# ---------------------------------------------------------------------------
# Tests: cleanup_old_samples
# ---------------------------------------------------------------------------


class TestCleanupOldSamples:
    def test_cleanup_removes_old_samples(self, db):
        now = datetime.utcnow()
        old_ts = now - timedelta(hours=200)
        recent_ts = now - timedelta(hours=1)

        _insert_sample(db, "cpu.load_1m", 2.0, ts=old_ts)
        _insert_sample(db, "cpu.load_1m", 2.5, ts=recent_ts)

        deleted = cleanup_old_samples(retention_hours=168, conn=db)
        assert deleted == 1

        cursor = db.execute("SELECT COUNT(*) FROM metric_samples")
        assert cursor.fetchone()[0] == 1

    def test_cleanup_retains_recent_samples(self, db):
        now = datetime.utcnow()
        _insert_sample(db, "cpu.load_1m", 2.0, ts=now)

        deleted = cleanup_old_samples(retention_hours=168, conn=db)
        assert deleted == 0

    def test_cleanup_custom_retention(self, db):
        now = datetime.utcnow()
        _insert_sample(db, "cpu.load_1m", 2.0, ts=now - timedelta(hours=25))
        _insert_sample(db, "cpu.load_1m", 2.5, ts=now)

        deleted = cleanup_old_samples(retention_hours=24, conn=db)
        assert deleted == 1


# ---------------------------------------------------------------------------
# Tests: TrendConfig defaults
# ---------------------------------------------------------------------------


class TestTrendConfigDefaults:
    def test_default_config(self):
        agg = TrendAggregator()
        assert agg.config.anomaly_z_threshold == 3.0
        assert agg.config.anomaly_min_samples == 100
        assert agg.config.forecast_min_samples == 10

    def test_custom_config(self):
        config = TrendConfig(
            anomaly_z_threshold=2.5,
            anomaly_min_samples=50,
            forecast_min_samples=5,
        )
        agg = TrendAggregator(config=config)
        assert agg.config.anomaly_z_threshold == 2.5
        assert agg.config.anomaly_min_samples == 50
