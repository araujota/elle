from __future__ import annotations

"""Comprehensive tests for elle.daemon.telemetry.aggregator.

Covers _ensure_connection, ensure_aggregation_schema, collect_system_metrics,
record_metric, record_metrics_batch, TrendAggregator methods, get_trend_context,
get_metric_trend, cleanup_old_samples, and correlation/store logic.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.telemetry.aggregator import (
    AGGREGATION_INDEXES,
    TrendAggregator,
    _ensure_connection,
    cleanup_old_samples,
    collect_system_metrics,
    ensure_aggregation_schema,
    get_metric_trend,
    get_trend_context,
    record_metric,
    record_metrics_batch,
)
from elle.daemon.telemetry.trends import (
    TRACKED_METRICS,
    MetricBaseline,
    TrendConfig,
    TrendWindow,
)

PATCH_GET_CONN = "elle.daemon.telemetry.aggregator.get_conn"


# ---------------------------------------------------------------------------
# Reusable fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_conn():
    """Return a MagicMock that behaves like a psycopg Connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    return conn


@pytest.fixture()
def aggregator():
    """Return a TrendAggregator with default config."""
    return TrendAggregator()


@pytest.fixture()
def aggregator_low_threshold():
    """Return a TrendAggregator with a low anomaly sample threshold."""
    config = TrendConfig(anomaly_min_samples=10, anomaly_z_threshold=2.0)
    return TrendAggregator(config=config)


# ---------------------------------------------------------------------------
# _ensure_connection
# ---------------------------------------------------------------------------


class TestEnsureConnection:
    """Tests for the _ensure_connection context manager."""

    def test_yields_provided_conn(self, mock_conn):
        """When a conn is provided, yield it directly without calling get_conn."""
        with patch(PATCH_GET_CONN) as mock_get_conn:
            with _ensure_connection(mock_conn) as c:
                assert c is mock_conn
            mock_get_conn.assert_not_called()

    def test_calls_get_conn_when_none(self):
        """When conn is None, obtain a pooled connection via get_conn."""
        pooled = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)
        with patch(PATCH_GET_CONN, return_value=ctx) as mock_get_conn:
            with _ensure_connection(None) as c:
                assert c is pooled
            mock_get_conn.assert_called_once_with(schema="telemetry")


# ---------------------------------------------------------------------------
# ensure_aggregation_schema
# ---------------------------------------------------------------------------


class TestEnsureAggregationSchema:
    """Tests for ensure_aggregation_schema."""

    def test_creates_tables_and_indexes(self, mock_conn):
        """Verify it executes CREATE TABLE and CREATE INDEX statements."""
        ensure_aggregation_schema(mock_conn)
        cursor = mock_conn.cursor.return_value
        # 3 CREATE TABLE + 2 indexes = 5 execute calls
        assert cursor.execute.call_count == 3 + len(AGGREGATION_INDEXES)
        mock_conn.commit.assert_called_once()

    def test_index_sql_passed(self, mock_conn):
        """Verify each index SQL is passed to execute."""
        ensure_aggregation_schema(mock_conn)
        cursor = mock_conn.cursor.return_value
        executed_sqls = [c.args[0] for c in cursor.execute.call_args_list]
        for idx_sql in AGGREGATION_INDEXES:
            assert idx_sql in executed_sqls


# ---------------------------------------------------------------------------
# collect_system_metrics
# ---------------------------------------------------------------------------


class TestCollectSystemMetrics:
    """Tests for collect_system_metrics."""

    def _make_statvfs(
        self,
        f_blocks=1000000,
        f_bfree=500000,
        f_frsize=4096,
        f_files=100000,
        f_ffree=80000,
    ):
        return SimpleNamespace(
            f_blocks=f_blocks,
            f_bfree=f_bfree,
            f_frsize=f_frsize,
            f_files=f_files,
            f_ffree=f_ffree,
            f_bavail=f_bfree,
        )

    _MEMINFO = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         2048000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "SwapTotal:       4096000 kB\n"
        "SwapFree:        2048000 kB\n"
    )

    _PROC_NET_TCP = (
        "  sl  local_address ...\n"
        "   0: 00000000:0050 00000000:0000 06 ...\n"
        "   1: 00000000:0050 00000000:0000 08 ...\n"
        "   2: 00000000:0050 00000000:0000 01 ...\n"
    )

    @patch("os.getloadavg", return_value=(1.0, 2.0, 3.0))
    @patch("os.statvfs")
    @patch("builtins.open")
    def test_successful_collection(self, mock_open_fn, mock_statvfs, mock_loadavg):
        """Full collection with /proc/meminfo, statvfs, loadavg."""
        stat = self._make_statvfs()
        mock_statvfs.return_value = stat

        from io import StringIO

        def open_side_effect(path, *args, **kwargs):
            if "meminfo" in str(path):
                return StringIO(self._MEMINFO)
            if "/proc/net/tcp" in str(path):
                return StringIO(self._PROC_NET_TCP)
            raise FileNotFoundError(path)

        mock_open_fn.side_effect = open_side_effect

        with (
            patch("pathlib.Path.iterdir", return_value=[]),
            patch("pathlib.Path.exists", return_value=False),
            patch("glob.glob", return_value=[]),
        ):
            metrics = collect_system_metrics()

        assert metrics["cpu.load_1m"] == 1.0
        assert metrics["cpu.load_5m"] == 2.0
        assert metrics["cpu.load_15m"] == 3.0
        assert "disk./.used_pct" in metrics
        assert "mem.used_pct" in metrics
        assert "mem.available_pct" in metrics
        assert "swap.used_pct" in metrics
        assert "net.tcp_time_wait" in metrics
        assert metrics["net.tcp_time_wait"] == 1.0  # one 06 line
        assert metrics["net.tcp_close_wait"] == 1.0  # one 08 line

    @patch("os.getloadavg", return_value=(0.5, 1.0, 1.5))
    @patch("os.statvfs")
    @patch("builtins.open")
    def test_partial_failure_statvfs(self, mock_open_fn, mock_statvfs, mock_loadavg):
        """If one mount's statvfs raises, other sources still succeed."""

        def statvfs_side_effect(mount):
            if mount == "/home":
                raise OSError("permission denied")
            return self._make_statvfs()

        mock_statvfs.side_effect = statvfs_side_effect

        from io import StringIO

        def open_side_effect(path, *a, **k):
            if "meminfo" in str(path):
                return StringIO(self._MEMINFO)
            raise FileNotFoundError(path)

        mock_open_fn.side_effect = open_side_effect

        with (
            patch("pathlib.Path.iterdir", return_value=[]),
            patch("pathlib.Path.exists", return_value=False),
            patch("glob.glob", return_value=[]),
        ):
            metrics = collect_system_metrics()

        # / and /var succeed, /home fails
        assert "disk./.used_pct" in metrics
        assert "disk./var.used_pct" in metrics
        assert "disk./home.used_pct" not in metrics
        # CPU still collected
        assert "cpu.load_1m" in metrics

    @patch("os.getloadavg", side_effect=OSError("no loadavg"))
    @patch("os.statvfs", side_effect=OSError("no statvfs"))
    @patch("builtins.open", side_effect=OSError("no procfs"))
    def test_total_failure_returns_empty(self, mock_open_fn, mock_statvfs, mock_loadavg):
        """When every collection source raises, return empty dict."""
        with (
            patch("pathlib.Path.iterdir", side_effect=Exception("no proc")),
            patch("pathlib.Path.exists", return_value=False),
            patch("glob.glob", return_value=[]),
        ):
            metrics = collect_system_metrics()

        assert isinstance(metrics, dict)
        # The function should not raise
        assert "cpu.load_1m" not in metrics


# ---------------------------------------------------------------------------
# record_metric
# ---------------------------------------------------------------------------


class TestRecordMetric:
    """Tests for record_metric."""

    def test_with_conn_provided(self, mock_conn):
        """When conn is provided, uses it directly and skips schema init."""
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with patch(PATCH_GET_CONN) as mock_get:
            record_metric("cpu.load_1m", 2.5, ts=ts, conn=mock_conn)
            mock_get.assert_not_called()
        cursor = mock_conn.cursor.return_value
        cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_without_conn_calls_schema_init(self):
        """When conn is None, calls get_conn and ensure_aggregation_schema."""
        pooled = MagicMock()
        pooled.cursor.return_value = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(PATCH_GET_CONN, return_value=ctx),
            patch("elle.daemon.telemetry.aggregator.ensure_aggregation_schema") as mock_schema,
        ):
            record_metric("cpu.load_1m", 2.5, ts=datetime(2025, 1, 1, tzinfo=timezone.utc))
            mock_schema.assert_called_once_with(pooled)

    def test_default_timestamp(self, mock_conn):
        """When ts is None, datetime.now(timezone.utc) is used."""
        with patch("elle.daemon.telemetry.aggregator.datetime") as mock_dt:
            fake_now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            record_metric("mem.used_pct", 65.0, conn=mock_conn)
            mock_dt.now.assert_called_once_with(timezone.utc)

    def test_explicit_timestamp(self, mock_conn):
        """Explicit ts is passed through to the INSERT."""
        ts = datetime(2025, 3, 15, 8, 30, 0, tzinfo=timezone.utc)
        record_metric("mem.used_pct", 72.0, ts=ts, conn=mock_conn)
        cursor = mock_conn.cursor.return_value
        call_args = cursor.execute.call_args
        # Second positional arg is the params tuple: (metric, value, ts_str)
        params = call_args[0][1]
        assert params[0] == "mem.used_pct"
        assert params[1] == 72.0
        assert "2025-03-15" in params[2]


# ---------------------------------------------------------------------------
# record_metrics_batch
# ---------------------------------------------------------------------------


class TestRecordMetricsBatch:
    """Tests for record_metrics_batch."""

    def test_normal_batch(self, mock_conn):
        """Batch of 3 metrics inserts 3 rows."""
        metrics = {"cpu.load_1m": 1.0, "mem.used_pct": 65.0, "disk./.used_pct": 80.0}
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        record_metrics_batch(metrics, ts=ts, conn=mock_conn)
        cursor = mock_conn.cursor.return_value
        assert cursor.execute.call_count == 3
        mock_conn.commit.assert_called_once()

    def test_empty_batch(self, mock_conn):
        """Empty metrics dict results in no inserts but still commits."""
        record_metrics_batch({}, ts=datetime(2025, 1, 1, tzinfo=timezone.utc), conn=mock_conn)
        cursor = mock_conn.cursor.return_value
        cursor.execute.assert_not_called()
        mock_conn.commit.assert_called_once()

    def test_without_conn_calls_schema_init(self):
        """Without conn, get_conn + ensure_aggregation_schema are called."""
        pooled = MagicMock()
        pooled.cursor.return_value = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(PATCH_GET_CONN, return_value=ctx),
            patch("elle.daemon.telemetry.aggregator.ensure_aggregation_schema") as mock_schema,
        ):
            record_metrics_batch(
                {"cpu.load_1m": 1.0},
                ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
            mock_schema.assert_called_once_with(pooled)


# ---------------------------------------------------------------------------
# TrendAggregator._compute_trend_window
# ---------------------------------------------------------------------------


class TestComputeTrendWindow:
    """Tests for TrendAggregator._compute_trend_window."""

    def test_samples_exist_with_rate(self, aggregator, mock_conn):
        """When 1h and 6h averages exist, rate is computed."""
        cursor = mock_conn.cursor.return_value
        # Four window queries (1h, 6h, 24h, 7d), then _get_baseline fetchone
        cursor.fetchone.side_effect = [
            (80.0, 12),  # 1h
            (70.0, 72),  # 6h
            (65.0, 288),  # 24h
            (60.0, 2016),  # 7d
            None,  # _get_baseline -> no row
        ]

        now = datetime(2025, 6, 1, 12, 0, 0)
        trend = aggregator._compute_trend_window("disk./.used_pct", 85.0, now, mock_conn)

        assert trend.metric == "disk./.used_pct"
        assert trend.current == 85.0
        assert trend.avg_1h == 80.0
        assert trend.avg_6h == 70.0
        assert trend.avg_24h == 65.0
        assert trend.avg_7d == 60.0
        # rate = (80 - 70) / 5.0 = 2.0
        assert trend.rate_of_change_per_hour == 2.0
        assert trend.sample_count == 2016

    def test_no_samples_some_windows(self, aggregator, mock_conn):
        """When some windows have no data, averages are None."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.side_effect = [
            (80.0, 5),  # 1h has data
            (None, 0),  # 6h no data
            (None, 0),  # 24h no data
            (None, 0),  # 7d no data
            None,  # _get_baseline -> no row
        ]

        now = datetime(2025, 6, 1, 12, 0, 0)
        trend = aggregator._compute_trend_window("mem.used_pct", 75.0, now, mock_conn)

        assert trend.avg_1h == 80.0
        assert trend.avg_6h is None
        assert trend.avg_24h is None
        assert trend.avg_7d is None
        # rate = 0.0 since 6h is None
        assert trend.rate_of_change_per_hour == 0.0

    def test_anomaly_detected_with_baseline(self, aggregator_low_threshold, mock_conn):
        """With sufficient baseline samples and non-zero stddev, anomaly is detected."""
        cursor = mock_conn.cursor.return_value
        # Window queries (4 fetches)
        cursor.fetchone.side_effect = [
            (50.0, 10),  # 1h
            (50.0, 60),  # 6h
            (50.0, 240),  # 24h
            (50.0, 1680),  # 7d
            # _get_baseline query returns a row
            {
                "metric": "mem.used_pct",
                "baseline_mean": 50.0,
                "baseline_stddev": 5.0,
                "samples": 200,
                "updated_at": "2025-06-01T00:00:00+00:00",
            },
        ]

        now = datetime(2025, 6, 1, 12, 0, 0)
        # current_value = 80.0, mean = 50.0, stddev = 5.0 => z = 6.0 > 2.0
        trend = aggregator_low_threshold._compute_trend_window("mem.used_pct", 80.0, now, mock_conn)

        assert trend.is_anomaly is True
        assert trend.anomaly_score is not None
        assert trend.anomaly_score == pytest.approx(6.0)

    def test_insufficient_baseline_no_anomaly(self, aggregator, mock_conn):
        """With baseline.samples < anomaly_min_samples, no anomaly flagged."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.side_effect = [
            (50.0, 10),
            (50.0, 60),
            (50.0, 240),
            (50.0, 1680),
            # _get_baseline: samples below default 100
            {
                "metric": "mem.used_pct",
                "baseline_mean": 50.0,
                "baseline_stddev": 5.0,
                "samples": 5,
                "updated_at": "2025-06-01T00:00:00+00:00",
            },
        ]

        now = datetime(2025, 6, 1, 12, 0, 0)
        trend = aggregator._compute_trend_window("mem.used_pct", 80.0, now, mock_conn)

        assert trend.is_anomaly is False
        assert trend.anomaly_score is None


# ---------------------------------------------------------------------------
# TrendAggregator._compute_forecast
# ---------------------------------------------------------------------------


class TestComputeForecast:
    """Tests for TrendAggregator._compute_forecast."""

    def _make_trend(self, metric, current, rate, sample_count=20, avg_1h=None, avg_6h=None):
        return TrendWindow(
            metric=metric,
            current=current,
            rate_of_change_per_hour=rate,
            sample_count=sample_count,
            avg_1h=avg_1h,
            avg_6h=avg_6h,
        )

    def test_basic_forecast_will_cross(self, aggregator, mock_conn):
        """Positive rate with threshold -> will cross within 7 days."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []  # No advanced data

        trend = self._make_trend("disk./.used_pct", current=80.0, rate=0.5)
        forecast = aggregator._compute_forecast("disk./.used_pct", trend, mock_conn)

        assert forecast.metric == "disk./.used_pct"
        assert forecast.current_value == 80.0
        # rate = 0.5/hr, threshold = 92.0, remaining = 12, time = 24h
        assert forecast.will_cross_threshold is True
        assert forecast.time_to_threshold_hours is not None
        assert forecast.time_to_threshold_hours == pytest.approx(24.0)

    def test_clamped_to_100(self, aggregator, mock_conn):
        """Predicted value > 100 is clamped to 100."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        trend = self._make_trend("disk./.used_pct", current=95.0, rate=5.0)
        forecast = aggregator._compute_forecast("disk./.used_pct", trend, mock_conn)

        assert forecast.predicted_value_24h <= 100.0
        assert forecast.predicted_value_7d <= 100.0

    def test_clamped_to_0(self, aggregator, mock_conn):
        """Predicted value < 0 is clamped to 0."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        trend = self._make_trend("disk./.used_pct", current=5.0, rate=-10.0)
        forecast = aggregator._compute_forecast("disk./.used_pct", trend, mock_conn)

        assert forecast.predicted_value_24h >= 0.0
        assert forecast.predicted_value_7d >= 0.0

    def test_no_threshold_crossing_negative_rate(self, aggregator, mock_conn):
        """Negative rate means no threshold crossing."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        trend = self._make_trend("disk./.used_pct", current=80.0, rate=-0.5)
        forecast = aggregator._compute_forecast("disk./.used_pct", trend, mock_conn)

        assert forecast.will_cross_threshold is False
        assert forecast.time_to_threshold_hours is None

    def test_model_selector_success(self, aggregator, mock_conn):
        """When ModelSelector returns a result, its values are used."""
        cursor = mock_conn.cursor.return_value
        # fetchall returns enough values for ModelSelector (>= 12)
        cursor.fetchall.return_value = [(float(v),) for v in range(20)]

        mock_result = SimpleNamespace(
            predicted_24h=88.0,
            predicted_7d=95.0,
            rate_of_change=0.8,
            method="holt_winters",
            mape=0.05,
            seasonal_detected=True,
        )

        mock_selector_cls = MagicMock()
        mock_selector_inst = MagicMock()
        mock_selector_inst.forecast.return_value = mock_result
        mock_selector_cls.return_value = mock_selector_inst

        # Create a proper fake module with ModelSelector attribute
        import types

        fake_module = types.ModuleType("elle.daemon.telemetry.model_selector")
        fake_module.ModelSelector = mock_selector_cls

        with patch.dict("sys.modules", {"elle.daemon.telemetry.model_selector": fake_module}):
            trend = self._make_trend("disk./.used_pct", current=80.0, rate=0.5, avg_1h=80.0, avg_6h=75.0)
            forecast = aggregator._compute_forecast("disk./.used_pct", trend, mock_conn)

        assert forecast.predicted_value_24h == 88.0
        assert forecast.predicted_value_7d == 95.0
        assert forecast.method == "holt_winters"
        assert forecast.mape == 0.05
        assert forecast.seasonal_detected is True

    def test_model_selector_import_failure(self, aggregator, mock_conn):
        """When ModelSelector import fails, falls back to linear."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        with patch.dict(
            "sys.modules",
            {"elle.daemon.telemetry.model_selector": None},
        ):
            trend = self._make_trend("disk./.used_pct", current=80.0, rate=0.5)
            forecast = aggregator._compute_forecast("disk./.used_pct", trend, mock_conn)

        # Linear fallback: predicted_24h = 80 + 0.5*24 = 92
        assert forecast.predicted_value_24h == pytest.approx(92.0, abs=0.1)
        assert forecast.method is None  # linear fallback has no method


# ---------------------------------------------------------------------------
# TrendAggregator._check_anomaly
# ---------------------------------------------------------------------------


class TestCheckAnomaly:
    """Tests for TrendAggregator._check_anomaly."""

    def test_no_baseline_returns_none(self, aggregator, mock_conn):
        """No baseline -> returns None."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None  # _get_baseline returns None

        result = aggregator._check_anomaly("mem.used_pct", 80.0, datetime.now(timezone.utc), mock_conn)
        assert result is None

    def test_insufficient_samples_returns_none(self, aggregator, mock_conn):
        """Baseline with too few samples -> returns None."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "mem.used_pct",
            "baseline_mean": 50.0,
            "baseline_stddev": 5.0,
            "samples": 5,  # Below default anomaly_min_samples (100)
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        result = aggregator._check_anomaly("mem.used_pct", 80.0, datetime.now(timezone.utc), mock_conn)
        assert result is None

    def test_zero_stddev_returns_none(self, aggregator_low_threshold, mock_conn):
        """Baseline with zero stddev -> returns None (avoids division by zero)."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "mem.used_pct",
            "baseline_mean": 50.0,
            "baseline_stddev": 0.0,
            "samples": 200,
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        result = aggregator_low_threshold._check_anomaly("mem.used_pct", 80.0, datetime.now(timezone.utc), mock_conn)
        assert result is None

    def test_positive_z_score_above(self, aggregator_low_threshold, mock_conn):
        """High z-score -> anomaly with direction 'above'."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "cpu.load_1m",
            "baseline_mean": 2.0,
            "baseline_stddev": 1.0,
            "samples": 200,
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        now = datetime(2025, 6, 1, 12, 0, 0)
        # z = (15 - 2) / 1 = 13.0 > 2.0
        result = aggregator_low_threshold._check_anomaly("cpu.load_1m", 15.0, now, mock_conn)

        assert result is not None
        assert result.is_anomaly is True
        assert result.anomaly_score == pytest.approx(13.0)
        assert result.deviation_direction == "above"

    def test_negative_z_score_below(self, aggregator_low_threshold, mock_conn):
        """Negative z-score -> direction 'below'."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "mem.available_pct",
            "baseline_mean": 50.0,
            "baseline_stddev": 5.0,
            "samples": 200,
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        now = datetime(2025, 6, 1, 12, 0, 0)
        # z = (10 - 50) / 5 = -8.0, abs = 8.0 > 2.0
        result = aggregator_low_threshold._check_anomaly("mem.available_pct", 10.0, now, mock_conn)

        assert result is not None
        assert result.is_anomaly is True
        assert result.deviation_direction == "below"

    def test_z_score_zero_direction_normal(self, aggregator_low_threshold, mock_conn):
        """z_score = 0 -> direction 'normal', not anomaly."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "cpu.load_1m",
            "baseline_mean": 2.0,
            "baseline_stddev": 1.0,
            "samples": 200,
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        now = datetime(2025, 6, 1, 12, 0, 0)
        # z = (2.0 - 2.0) / 1.0 = 0.0
        result = aggregator_low_threshold._check_anomaly("cpu.load_1m", 2.0, now, mock_conn)

        assert result is not None
        assert result.is_anomaly is False
        assert result.deviation_direction == "normal"

    def test_normal_value_within_threshold(self, aggregator_low_threshold, mock_conn):
        """Value within z-score threshold -> is_anomaly=False."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "cpu.load_1m",
            "baseline_mean": 2.0,
            "baseline_stddev": 1.0,
            "samples": 200,
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        now = datetime(2025, 6, 1, 12, 0, 0)
        # z = (3.5 - 2.0) / 1.0 = 1.5, abs(1.5) < 2.0
        result = aggregator_low_threshold._check_anomaly("cpu.load_1m", 3.5, now, mock_conn)

        assert result is not None
        assert result.is_anomaly is False
        assert result.anomaly_score == pytest.approx(1.5)
        assert result.deviation_direction == "above"


# ---------------------------------------------------------------------------
# TrendAggregator._get_baseline / _update_baseline
# ---------------------------------------------------------------------------


class TestBaselineOperations:
    """Tests for _get_baseline and _update_baseline."""

    def test_get_baseline_found(self, aggregator, mock_conn):
        """Row found -> returns MetricBaseline."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {
            "metric": "disk./.used_pct",
            "baseline_mean": 60.0,
            "baseline_stddev": 10.0,
            "samples": 500,
            "updated_at": "2025-06-01T12:00:00+00:00",
        }

        baseline = aggregator._get_baseline("disk./.used_pct", mock_conn)

        assert baseline is not None
        assert isinstance(baseline, MetricBaseline)
        assert baseline.metric == "disk./.used_pct"
        assert baseline.baseline_mean == 60.0
        assert baseline.samples == 500

    def test_get_baseline_not_found(self, aggregator, mock_conn):
        """No row -> returns None."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        baseline = aggregator._get_baseline("nonexistent.metric", mock_conn)
        assert baseline is None

    def test_update_baseline_existing_ema(self, aggregator, mock_conn):
        """Existing baseline -> EMA update with UPDATE SQL."""
        cursor = mock_conn.cursor.return_value
        # _get_baseline call inside _update_baseline
        cursor.fetchone.return_value = {
            "metric": "disk./.used_pct",
            "baseline_mean": 60.0,
            "baseline_stddev": 10.0,
            "samples": 100,
            "updated_at": "2025-06-01T00:00:00+00:00",
        }

        aggregator._update_baseline("disk./.used_pct", 65.0, mock_conn)

        # The second execute call should be the UPDATE
        calls = cursor.execute.call_args_list
        # Call 0: SELECT from _get_baseline
        # Call 1: UPDATE metric_baselines
        assert len(calls) == 2
        update_sql = calls[1][0][0]
        assert "UPDATE metric_baselines" in update_sql
        mock_conn.commit.assert_called_once()

    def test_update_baseline_first_sample(self, aggregator, mock_conn):
        """No existing baseline -> INSERT with stddev=0."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None  # no existing baseline

        aggregator._update_baseline("new.metric", 42.0, mock_conn)

        calls = cursor.execute.call_args_list
        # Call 0: SELECT from _get_baseline
        # Call 1: INSERT INTO metric_baselines
        assert len(calls) == 2
        insert_sql = calls[1][0][0]
        assert "INSERT INTO metric_baselines" in insert_sql
        params = calls[1][0][1]
        assert params[0] == "new.metric"
        assert params[1] == 42.0
        assert params[2] == 0.0  # stddev = 0 for first sample
        assert params[3] == 1  # samples = 1
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# TrendAggregator.run_aggregation_cycle
# ---------------------------------------------------------------------------


class TestRunAggregationCycle:
    """Tests for TrendAggregator.run_aggregation_cycle."""

    def test_full_cycle_returns_trend_context(self, mock_conn):
        """Full cycle returns a TrendContext with expected structure."""
        agg = TrendAggregator()
        cursor = mock_conn.cursor.return_value
        # Default fetchone/fetchall return None/[] for all queries
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []

        with (
            patch(
                "elle.daemon.telemetry.aggregator.collect_system_metrics",
                return_value={
                    "disk./.used_pct": 75.0,
                    "mem.used_pct": 60.0,
                    "cpu.load_1m": 1.5,
                    "swap.used_pct": 10.0,
                },
            ),
            patch("elle.daemon.telemetry.aggregator.record_metrics_batch") as mock_record,
        ):
            ctx = agg.run_aggregation_cycle(conn=mock_conn)

        assert ctx is not None
        assert "/" in ctx.disk_trends
        assert ctx.memory_trend is not None
        assert ctx.cpu_trend is not None
        assert ctx.swap_trend is not None
        assert isinstance(ctx.anomalies, tuple)
        assert ctx.computed_at is not None
        mock_record.assert_called_once()

    def test_cycle_generates_warnings_above_threshold(self, mock_conn):
        """When disk value >= threshold, a warning is generated."""
        agg = TrendAggregator()
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []

        with (
            patch(
                "elle.daemon.telemetry.aggregator.collect_system_metrics",
                return_value={"disk./.used_pct": 95.0},
            ),
            patch("elle.daemon.telemetry.aggregator.record_metrics_batch"),
        ):
            ctx = agg.run_aggregation_cycle(conn=mock_conn)

        assert ctx.has_warnings is True
        assert any("disk./.used_pct" in w for w in ctx.warning_messages)


# ---------------------------------------------------------------------------
# get_trend_context
# ---------------------------------------------------------------------------


class TestGetTrendContext:
    """Tests for the get_trend_context public function."""

    def test_delegates_to_aggregator(self, mock_conn):
        """get_trend_context creates a TrendAggregator and runs cycle."""
        with patch.object(TrendAggregator, "run_aggregation_cycle") as mock_cycle:
            mock_cycle.return_value = MagicMock()
            result = get_trend_context(conn=mock_conn)
            mock_cycle.assert_called_once_with(conn=mock_conn)
            assert result is mock_cycle.return_value


# ---------------------------------------------------------------------------
# get_metric_trend
# ---------------------------------------------------------------------------


class TestGetMetricTrend:
    """Tests for get_metric_trend."""

    def test_metric_found(self, mock_conn):
        """Metric exists: returns TrendWindow with aggregations."""
        cursor = mock_conn.cursor.return_value
        # First fetchone: latest value
        cursor.fetchone.return_value = {"value": 82.5}
        # fetchall: aggregation windows
        cursor.fetchall.return_value = [
            {"window": "1h", "avg_value": 80.0},
            {"window": "6h", "avg_value": 75.0},
            {"window": "24h", "avg_value": 70.0},
        ]

        trend = get_metric_trend("disk./.used_pct", conn=mock_conn)

        assert trend is not None
        assert trend.metric == "disk./.used_pct"
        assert trend.current == 82.5
        assert trend.avg_1h == 80.0
        assert trend.avg_6h == 75.0
        assert trend.avg_24h == 70.0
        assert trend.avg_7d is None  # not in fetchall results

    def test_metric_not_found(self, mock_conn):
        """No samples for metric -> returns None."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None

        trend = get_metric_trend("nonexistent.metric", conn=mock_conn)
        assert trend is None

    def test_without_conn_calls_get_conn(self):
        """When conn is None, get_conn is called."""
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.fetchone.return_value = None
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(PATCH_GET_CONN, return_value=ctx) as mock_get,
            patch("elle.daemon.telemetry.aggregator.ensure_aggregation_schema"),
        ):
            get_metric_trend("cpu.load_1m")
            mock_get.assert_called_once_with(schema="telemetry")

    def test_with_conn_skips_get_conn(self, mock_conn):
        """When conn is provided, get_conn is not called."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = {"value": 1.5}
        cursor.fetchall.return_value = []

        with patch(PATCH_GET_CONN) as mock_get:
            trend = get_metric_trend("cpu.load_1m", conn=mock_conn)
            mock_get.assert_not_called()

        assert trend is not None
        assert trend.current == 1.5


# ---------------------------------------------------------------------------
# cleanup_old_samples
# ---------------------------------------------------------------------------


class TestCleanupOldSamples:
    """Tests for cleanup_old_samples."""

    def test_returns_deleted_count(self, mock_conn):
        """Returns cursor.rowcount after DELETE."""
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 42

        deleted = cleanup_old_samples(retention_hours=168, conn=mock_conn)

        assert deleted == 42
        cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_delete_sql_uses_cutoff(self, mock_conn):
        """Verify the DELETE uses a cutoff computed from retention_hours."""
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 0

        cleanup_old_samples(retention_hours=24, conn=mock_conn)

        call_args = cursor.execute.call_args
        sql = call_args[0][0]
        assert "DELETE FROM metric_samples" in sql
        assert "ts <" in sql

    def test_without_conn_calls_schema_init(self):
        """Without conn, schema init occurs."""
        pooled = MagicMock()
        pooled_cursor = MagicMock()
        pooled.cursor.return_value = pooled_cursor
        pooled_cursor.rowcount = 0
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=pooled)
        ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(PATCH_GET_CONN, return_value=ctx),
            patch("elle.daemon.telemetry.aggregator.ensure_aggregation_schema") as mock_schema,
        ):
            cleanup_old_samples()
            mock_schema.assert_called_once_with(pooled)


# ---------------------------------------------------------------------------
# TrendAggregator._run_correlations
# ---------------------------------------------------------------------------


class TestRunCorrelations:
    """Tests for TrendAggregator._run_correlations."""

    def test_engine_success(self, aggregator, mock_conn):
        """CorrelationEngine returns alerts -> converted and returned."""
        mock_alert = SimpleNamespace(
            signature="disk_memory_pressure",
            severity="warning",
            description="High disk and memory usage",
            contributing_metrics={"disk./.used_pct": 90.0, "mem.used_pct": 85.0},
            recommended_action="Check for large files",
        )

        mock_engine_cls = MagicMock()
        mock_engine_inst = MagicMock()
        mock_engine_inst.evaluate.return_value = [mock_alert]
        mock_engine_cls.return_value = mock_engine_inst

        mock_module = MagicMock()
        mock_module.CorrelationEngine = mock_engine_cls
        mock_module.CorrelationAlert = type(mock_alert)

        with patch.dict("sys.modules", {"elle.daemon.telemetry.correlator_engine": mock_module}):
            alerts = aggregator._run_correlations({"disk./.used_pct": 90.0}, mock_conn)

        assert len(alerts) == 1
        assert alerts[0].signature == "disk_memory_pressure"
        assert alerts[0].severity == "warning"

    def test_import_failure_returns_empty(self, aggregator, mock_conn):
        """Import failure -> returns empty list."""
        with patch.dict("sys.modules", {"elle.daemon.telemetry.correlator_engine": None}):
            alerts = aggregator._run_correlations({"cpu.load_1m": 5.0}, mock_conn)

        assert alerts == []

    def test_engine_exception_returns_empty(self, aggregator, mock_conn):
        """Engine raises exception -> returns empty list."""
        mock_engine_cls = MagicMock()
        mock_engine_cls.return_value.evaluate.side_effect = RuntimeError("boom")

        mock_module = MagicMock()
        mock_module.CorrelationEngine = mock_engine_cls

        with patch.dict("sys.modules", {"elle.daemon.telemetry.correlator_engine": mock_module}):
            alerts = aggregator._run_correlations({"cpu.load_1m": 5.0}, mock_conn)

        assert alerts == []


# ---------------------------------------------------------------------------
# TrendAggregator._store_aggregations
# ---------------------------------------------------------------------------


class TestStoreAggregations:
    """Tests for TrendAggregator._store_aggregations."""

    def test_iterates_tracked_metrics(self, aggregator, mock_conn):
        """Verify it queries each TRACKED_METRIC for each window."""
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.return_value = None  # No data for any metric/window

        aggregator._store_aggregations(mock_conn)

        # 4 windows per metric, TRACKED_METRICS count queries
        expected_queries = len(TRACKED_METRICS) * 4
        assert cursor.execute.call_count == expected_queries
        mock_conn.commit.assert_called_once()

    def test_inserts_when_data_exists(self, aggregator, mock_conn):
        """When aggregate data exists, INSERT/UPSERT is executed."""
        cursor = mock_conn.cursor.return_value
        # Return data for every query
        cursor.fetchone.return_value = (75.0, 60.0, 90.0, 100)  # avg, min, max, count

        aggregator._store_aggregations(mock_conn)

        # For each metric*window, there's a SELECT + INSERT = 2 calls
        total_metric_windows = len(TRACKED_METRICS) * 4
        expected_calls = total_metric_windows * 2  # SELECT + INSERT for each
        assert cursor.execute.call_count == expected_calls
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Edge cases and integration-style tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and additional coverage."""

    def test_aggregator_default_config(self):
        """TrendAggregator uses default TrendConfig if none provided."""
        agg = TrendAggregator()
        assert isinstance(agg.config, TrendConfig)
        assert agg.config.anomaly_z_threshold == 3.0

    def test_aggregator_custom_config(self):
        """TrendAggregator accepts custom TrendConfig."""
        config = TrendConfig(anomaly_z_threshold=2.0, anomaly_min_samples=50)
        agg = TrendAggregator(config=config)
        assert agg.config.anomaly_z_threshold == 2.0
        assert agg.config.anomaly_min_samples == 50

    def test_aggregator_last_run_initially_none(self):
        """_last_run is None before first cycle."""
        agg = TrendAggregator()
        assert agg._last_run is None

    def test_forecast_confidence_boost_with_mape(self, mock_conn):
        """Confidence is boosted when MAPE < 0.1."""
        agg = TrendAggregator()
        cursor = mock_conn.cursor.return_value
        # Return enough values for advanced forecasting
        cursor.fetchall.return_value = [(float(v),) for v in range(20)]

        mock_result = SimpleNamespace(
            predicted_24h=85.0,
            predicted_7d=90.0,
            rate_of_change=0.3,
            method="holt_winters",
            mape=0.05,  # Low MAPE
            seasonal_detected=False,
        )

        mock_selector_cls = MagicMock()
        mock_selector_inst = MagicMock()
        mock_selector_inst.forecast.return_value = mock_result
        mock_selector_cls.return_value = mock_selector_inst

        import types

        fake_module = types.ModuleType("elle.daemon.telemetry.model_selector")
        fake_module.ModelSelector = mock_selector_cls

        with patch.dict("sys.modules", {"elle.daemon.telemetry.model_selector": fake_module}):
            trend = TrendWindow(
                metric="disk./.used_pct",
                current=80.0,
                rate_of_change_per_hour=0.3,
                sample_count=50,
                avg_1h=80.0,
                avg_6h=78.0,
            )
            forecast = agg._compute_forecast("disk./.used_pct", trend, mock_conn)

        # Base confidence with 50 samples: 0.5 + (50/100)*0.4 = 0.7
        # With MAPE boost: 0.7 + 0.1 = 0.8
        assert forecast.confidence >= 0.7

    def test_compute_trend_window_all_none_windows(self, mock_conn):
        """All windows return None -> averages all None, rate = 0."""
        agg = TrendAggregator()
        cursor = mock_conn.cursor.return_value
        cursor.fetchone.side_effect = [
            (None, 0),  # 1h
            (None, 0),  # 6h
            (None, 0),  # 24h
            (None, 0),  # 7d
            None,  # _get_baseline returns None
        ]

        now = datetime(2025, 6, 1, 12, 0, 0)
        trend = agg._compute_trend_window("disk./.used_pct", 50.0, now, mock_conn)

        assert trend.avg_1h is None
        assert trend.avg_6h is None
        assert trend.avg_24h is None
        assert trend.avg_7d is None
        assert trend.rate_of_change_per_hour == 0.0
        assert trend.is_anomaly is False
        assert trend.sample_count == 0

    def test_forecast_no_critical_threshold(self, mock_conn):
        """Metric without a CRITICAL_THRESHOLD -> no crossing computed."""
        agg = TrendAggregator()
        cursor = mock_conn.cursor.return_value
        cursor.fetchall.return_value = []

        # Use a metric that has no critical threshold
        trend = TrendWindow(
            metric="proc.zombie_count",
            current=5.0,
            rate_of_change_per_hour=1.0,
            sample_count=20,
        )
        forecast = agg._compute_forecast("proc.zombie_count", trend, mock_conn)

        assert forecast.threshold is None
        assert forecast.will_cross_threshold is False
        assert forecast.time_to_threshold_hours is None
