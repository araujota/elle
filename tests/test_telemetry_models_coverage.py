"""Coverage tests for elle.daemon.telemetry.models.

Targets the 7 missing lines (85.5% -> 100%): compute_fingerprint regex branch,
QueueStats.utilization property (max_size==0 and >0), DaemonStatus fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from elle.daemon.telemetry.models import (
    DaemonStatus,
    ProbeResult,
    QueueStats,
    TelemetryEvent,
)

# ---------------------------------------------------------------------------
# TelemetryEvent
# ---------------------------------------------------------------------------


class TestTelemetryEvent:
    def test_defaults(self):
        ev = TelemetryEvent(source="journal", message="test message")
        assert ev.event_id is not None
        assert len(ev.event_id) == 12
        assert ev.severity == "info"
        assert ev.category == "other"
        assert ev.entity is None
        assert ev.fingerprint is None
        assert ev.raw == {}

    def test_all_fields(self):
        ev = TelemetryEvent(
            event_id="abc123def456",
            ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
            source="kernel",
            severity="error",
            category="oom",
            message="Out of memory: killed process 1234",
            entity="service:nginx",
            fingerprint="aabbccdd11223344",
            raw={"PRIORITY": "3"},
        )
        assert ev.source == "kernel"
        assert ev.severity == "error"
        assert ev.entity == "service:nginx"
        assert ev.raw == {"PRIORITY": "3"}

    def test_frozen(self):
        ev = TelemetryEvent(source="journal", message="test")
        with pytest.raises(ValidationError):
            ev.message = "changed"


class TestComputeFingerprint:
    """Cover compute_fingerprint including regex substitution branches."""

    def test_basic_fingerprint(self):
        fp = TelemetryEvent.compute_fingerprint("oom", "service:nginx", "OOM killed")
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_fingerprint_without_entity(self):
        fp = TelemetryEvent.compute_fingerprint("disk", None, "Disk full")
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_fingerprint_strips_dates(self):
        """Dates like 2025-01-15 should be removed for normalization."""
        fp1 = TelemetryEvent.compute_fingerprint("service", "nginx", "Error on 2025-01-15 at startup")
        fp2 = TelemetryEvent.compute_fingerprint("service", "nginx", "Error on 2026-03-20 at startup")
        assert fp1 == fp2

    def test_fingerprint_strips_times(self):
        """Times like 12:30:45 should be removed for normalization."""
        fp1 = TelemetryEvent.compute_fingerprint("auth", None, "Login failed at 08:15:30 from host")
        fp2 = TelemetryEvent.compute_fingerprint("auth", None, "Login failed at 23:59:59 from host")
        assert fp1 == fp2

    def test_fingerprint_strips_numbers(self):
        """Bare numbers should be removed for normalization."""
        fp1 = TelemetryEvent.compute_fingerprint("oom", "nginx", "killed process 1234 (score 500)")
        fp2 = TelemetryEvent.compute_fingerprint("oom", "nginx", "killed process 5678 (score 900)")
        assert fp1 == fp2

    def test_fingerprint_different_categories_differ(self):
        fp1 = TelemetryEvent.compute_fingerprint("oom", None, "memory pressure")
        fp2 = TelemetryEvent.compute_fingerprint("disk", None, "memory pressure")
        assert fp1 != fp2

    def test_fingerprint_case_insensitive(self):
        fp1 = TelemetryEvent.compute_fingerprint("oom", None, "OOM killed process")
        fp2 = TelemetryEvent.compute_fingerprint("oom", None, "oom killed process")
        assert fp1 == fp2


# ---------------------------------------------------------------------------
# ProbeResult
# ---------------------------------------------------------------------------


class TestProbeResult:
    def test_defaults(self):
        pr = ProbeResult(probe_name="disk_check")
        assert pr.success is True
        assert pr.data == {}
        assert pr.error is None
        assert pr.events == ()

    def test_failed_probe(self):
        pr = ProbeResult(
            probe_name="smart_check",
            success=False,
            error="SMART read failed",
            data={"exit_code": 1},
        )
        assert pr.success is False
        assert pr.error == "SMART read failed"

    def test_probe_with_events(self):
        ev = TelemetryEvent(source="probe", message="Disk warning")
        pr = ProbeResult(probe_name="disk_check", events=(ev,))
        assert len(pr.events) == 1
        assert pr.events[0].source == "probe"


# ---------------------------------------------------------------------------
# QueueStats -- utilization property
# ---------------------------------------------------------------------------


class TestQueueStats:
    def test_utilization_normal(self):
        qs = QueueStats(name="raw", size=50, max_size=100)
        assert qs.utilization == 0.5

    def test_utilization_empty(self):
        qs = QueueStats(name="raw", size=0, max_size=100)
        assert qs.utilization == 0.0

    def test_utilization_full(self):
        qs = QueueStats(name="raw", size=100, max_size=100)
        assert qs.utilization == 1.0

    def test_utilization_zero_max_returns_zero(self):
        """When max_size is 0, utilization returns 0.0 to avoid division by zero."""
        qs = QueueStats(name="raw", size=0, max_size=0)
        assert qs.utilization == 0.0

    def test_dropped_and_processed(self):
        qs = QueueStats(name="event", size=10, max_size=1000, dropped=5, processed=500)
        assert qs.dropped == 5
        assert qs.processed == 500


# ---------------------------------------------------------------------------
# DaemonStatus
# ---------------------------------------------------------------------------


class TestDaemonStatus:
    def test_minimal(self):
        raw_q = QueueStats(name="raw", size=0, max_size=1000)
        event_q = QueueStats(name="event", size=0, max_size=5000)
        ds = DaemonStatus(
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            uptime_sec=3600,
            pid=12345,
            raw_queue=raw_q,
            event_queue=event_q,
        )
        assert ds.pid == 12345
        assert ds.healthy is True
        assert ds.journal_active is False
        assert ds.kernel_active is False
        assert ds.probes_active is False
        assert ds.api_active is False
        assert ds.events_total == 0
        assert ds.events_1h == 0
        assert ds.incidents_total == 0
        assert ds.incidents_open == 0
        assert ds.errors == ()

    def test_full_status(self):
        raw_q = QueueStats(name="raw", size=50, max_size=1000)
        event_q = QueueStats(name="event", size=200, max_size=5000)
        ds = DaemonStatus(
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            uptime_sec=86400,
            pid=1,
            journal_active=True,
            kernel_active=True,
            probes_active=True,
            api_active=True,
            raw_queue=raw_q,
            event_queue=event_q,
            events_total=10000,
            events_1h=500,
            incidents_total=50,
            incidents_open=3,
            healthy=True,
            errors=("slow query detected",),
        )
        assert ds.journal_active is True
        assert ds.events_total == 10000
        assert len(ds.errors) == 1

    def test_unhealthy_status(self):
        raw_q = QueueStats(name="raw", size=0, max_size=1000)
        event_q = QueueStats(name="event", size=0, max_size=5000)
        ds = DaemonStatus(
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            uptime_sec=0,
            pid=1,
            raw_queue=raw_q,
            event_queue=event_q,
            healthy=False,
            errors=("journal reader crashed", "kernel watcher timeout"),
        )
        assert ds.healthy is False
        assert len(ds.errors) == 2
