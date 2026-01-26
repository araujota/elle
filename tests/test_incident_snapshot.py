"""Tests for system snapshot collection."""

import sys
from datetime import datetime

import pytest

from elle.daemon.incidents.models import Fingerprint, SystemSnapshot
from elle.daemon.incidents.snapshot import (
    collect_snapshot,
    diff_snapshots,
    extract_fingerprint,
)


class TestSnapshotCollection:
    """Tests for snapshot collection."""

    def test_collect_returns_snapshot(self):
        """Test that collect_snapshot returns a valid snapshot."""
        snapshot = collect_snapshot()

        assert isinstance(snapshot, SystemSnapshot)
        assert snapshot.os != ""
        assert snapshot.kernel != ""
        assert snapshot.uptime_sec >= 0

    def test_snapshot_has_cpu_load(self):
        """Test that snapshot includes CPU load."""
        snapshot = collect_snapshot()

        assert len(snapshot.cpu_load) == 3
        assert all(isinstance(x, float) for x in snapshot.cpu_load)

    @pytest.mark.skipif(sys.platform != "linux", reason="Memory reporting differs on non-Linux platforms")
    def test_snapshot_has_memory(self):
        """Test that snapshot includes memory info."""
        snapshot = collect_snapshot()

        assert snapshot.mem_total_mb > 0
        assert snapshot.mem_free_mb >= 0
        assert snapshot.mem_available_mb >= 0

    def test_snapshot_has_timestamp(self):
        """Test that snapshot has a collection timestamp."""
        before = datetime.utcnow()
        snapshot = collect_snapshot()
        after = datetime.utcnow()

        assert before <= snapshot.collected_at <= after

    def test_snapshot_disks_is_tuple(self):
        """Test that disks is a tuple (immutable)."""
        snapshot = collect_snapshot()
        assert isinstance(snapshot.disks, tuple)

    def test_snapshot_interfaces_is_tuple(self):
        """Test that interfaces is a tuple (immutable)."""
        snapshot = collect_snapshot()
        assert isinstance(snapshot.interfaces, tuple)


class TestFingerprintExtraction:
    """Tests for fingerprint extraction."""

    def test_extract_basic_fingerprint(self):
        """Test extracting a basic fingerprint."""
        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=3600,
            cpu_load=(1.5, 1.2, 1.0),
            mem_total_mb=16384,
            mem_free_mb=2000,
            mem_available_mb=4000,
        )

        fingerprint = extract_fingerprint(snapshot)

        assert isinstance(fingerprint, Fingerprint)
        assert fingerprint.cpu_pressure == 1.5
        # mem_pressure = 1 - (4000 / 16384) ≈ 0.756
        assert 0.7 < fingerprint.mem_pressure < 0.8

    def test_extract_with_disk_pressure(self):
        """Test fingerprint with disk pressure."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            disks=(
                {"mount": "/", "used_pct": 85, "avail_gb": 20},
                {"mount": "/home", "used_pct": 95, "avail_gb": 10},
            ),
        )

        fingerprint = extract_fingerprint(snapshot)

        # Should be max of disk percentages
        assert fingerprint.disk_pressure == 0.95

    def test_extract_with_entities(self):
        """Test fingerprint with entities."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )

        fingerprint = extract_fingerprint(
            snapshot,
            entities=["service:nginx", "interface:eth0"],
        )

        assert "service:nginx" in fingerprint.entities
        assert "interface:eth0" in fingerprint.entities

    def test_extract_with_event_counts(self):
        """Test fingerprint with event counts."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )

        fingerprint = extract_fingerprint(
            snapshot,
            oom_count_1h=5,
            service_failures_1h=2,
        )

        assert fingerprint.oom_count_1h == 5
        assert fingerprint.service_failures_1h == 2

    def test_extract_with_smart_data(self):
        """Test fingerprint with SMART data."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            smart=(
                {"dev": "/dev/nvme0", "health": "PASSED", "pct_used": 15, "media_errors": 0},
                {"dev": "/dev/sda", "health": "PASSED", "pct_used": 25, "media_errors": 2},
            ),
        )

        fingerprint = extract_fingerprint(snapshot)

        assert fingerprint.smart_pct_used_max == 25
        assert fingerprint.smart_media_errors == 2

    def test_extract_with_temps(self):
        """Test fingerprint with temperature data."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            temps=(
                {"sensor": "CPU", "celsius": 65},
                {"sensor": "GPU", "celsius": 72},
            ),
        )

        fingerprint = extract_fingerprint(snapshot)

        assert fingerprint.temp_max_c == 72


class TestSnapshotDiff:
    """Tests for snapshot diffing."""

    def test_diff_uptime(self):
        """Test diffing uptime."""
        before = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )
        after = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1100,
            cpu_load=(0.6, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )

        diff = diff_snapshots(before, after)

        assert diff["uptime_delta_sec"] == 100

    def test_diff_memory(self):
        """Test diffing memory."""
        before = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )
        after = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=5096,
            mem_available_mb=7144,
        )

        diff = diff_snapshots(before, after)

        assert diff["mem_free_delta_mb"] == 1000
        assert diff["mem_available_delta_mb"] == 1000

    def test_diff_disk_changes(self):
        """Test diffing disk usage."""
        before = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            disks=({"mount": "/", "used_pct": 80},),
        )
        after = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            disks=({"mount": "/", "used_pct": 70},),
        )

        diff = diff_snapshots(before, after)

        assert len(diff["disk_changes"]) == 1
        assert diff["disk_changes"][0]["mount"] == "/"
        assert diff["disk_changes"][0]["used_pct_delta"] == -10

    def test_diff_interface_changes(self):
        """Test diffing interface state."""
        before = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            interfaces=({"name": "eth0", "state": "DOWN"},),
        )
        after = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            interfaces=({"name": "eth0", "state": "UP"},),
        )

        diff = diff_snapshots(before, after)

        assert len(diff["interface_changes"]) == 1
        assert diff["interface_changes"][0]["name"] == "eth0"
        assert diff["interface_changes"][0]["state_before"] == "DOWN"
        assert diff["interface_changes"][0]["state_after"] == "UP"

    def test_diff_docker(self):
        """Test diffing docker containers."""
        before = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            docker_running=3,
            docker_exited=1,
        )
        after = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=1000,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            docker_running=2,
            docker_exited=2,
        )

        diff = diff_snapshots(before, after)

        assert diff["docker_running_delta"] == -1
        assert diff["docker_exited_delta"] == 1
