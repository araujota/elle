"""Tests for V4 store operations (telemetry and control surface snapshots)."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from elle.daemon.incidents.schema import get_connection, init_incident_schema
from elle.daemon.incidents.store import (
    attach_control_surface_snapshot,
    attach_telemetry_snapshot,
    create_incident_draft,
    get_control_surface_snapshot,
    get_control_surface_snapshots,
    get_surface_hashes,
    get_telemetry_snapshot,
    get_telemetry_snapshots,
)
from elle.daemon.incidents.telemetry_snapshot import (
    CPUPressure,
    DiskIOPressure,
    KernelHardwareSignals,
    MemoryPressure,
    NetworkLiveness,
    TelemetrySnapshot,
)
from elle.daemon.incidents.control_surface import (
    ControlSurfaceSnapshot,
    HardwareIdentitySurface,
    KernelPolicySurface,
    NetworkControlSurface,
    PackageSurface,
    PrivilegeSurface,
    SchedulerSurface,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_incident_schema(conn)
    yield conn, db_path

    conn.close()
    db_path.unlink()


@pytest.fixture
def incident_with_db(temp_db):
    """Create an incident for testing."""
    conn, _ = temp_db
    incident = create_incident_draft(
        title="Test incident",
        domain="other",
        conn=conn,
    )
    return incident, conn


def make_telemetry_snapshot() -> TelemetrySnapshot:
    """Create a test telemetry snapshot."""
    return TelemetrySnapshot(
        cpu=CPUPressure(load_1m=1.5, load_5m=1.0, load_15m=0.5),
        memory=MemoryPressure(total_mb=8000, available_mb=4000),
        disk=DiskIOPressure(disks=()),
        network=NetworkLiveness(interfaces=()),
        kernel=KernelHardwareSignals(),
        hostname="testhost",
        uptime_sec=3600,
    )


def make_control_surface_snapshot() -> ControlSurfaceSnapshot:
    """Create a test control surface snapshot."""
    return ControlSurfaceSnapshot(
        packages=PackageSurface(packages=()),
        scheduler=SchedulerSurface(),
        privilege=PrivilegeSurface(),
        network_control=NetworkControlSurface(network_manager="NetworkManager"),
        kernel_policy=KernelPolicySurface(kernel_version="5.15.0"),
        hardware=HardwareIdentitySurface(cpu_cores=4, total_memory_mb=8000),
    )


class TestTelemetrySnapshotCRUD:
    """Tests for telemetry snapshot CRUD operations."""

    def test_attach_telemetry_snapshot(self, incident_with_db):
        """Should attach telemetry snapshot to incident."""
        incident, conn = incident_with_db
        snap = make_telemetry_snapshot()

        # Should not raise
        attach_telemetry_snapshot(incident.incident_id, "pre", snap, conn=conn)

    def test_get_telemetry_snapshot(self, incident_with_db):
        """Should retrieve attached telemetry snapshot."""
        incident, conn = incident_with_db
        snap = make_telemetry_snapshot()

        attach_telemetry_snapshot(incident.incident_id, "pre", snap, conn=conn)
        retrieved = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)

        assert retrieved is not None
        assert retrieved.cpu.load_1m == 1.5
        assert retrieved.hostname == "testhost"
        assert retrieved.uptime_sec == 3600

    def test_get_telemetry_snapshot_not_found(self, incident_with_db):
        """Should return None for missing snapshot."""
        incident, conn = incident_with_db
        retrieved = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)
        assert retrieved is None

    def test_get_telemetry_snapshots_both(self, incident_with_db):
        """Should retrieve both pre and post snapshots."""
        incident, conn = incident_with_db
        snap_pre = make_telemetry_snapshot()
        snap_post = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=0.5, load_5m=0.3, load_15m=0.2),
            memory=MemoryPressure(total_mb=8000, available_mb=6000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
            hostname="testhost",
            uptime_sec=7200,
        )

        attach_telemetry_snapshot(incident.incident_id, "pre", snap_pre, conn=conn)
        attach_telemetry_snapshot(incident.incident_id, "post", snap_post, conn=conn)

        snapshots = get_telemetry_snapshots(incident.incident_id, conn=conn)

        assert "pre" in snapshots
        assert "post" in snapshots
        assert snapshots["pre"].cpu.load_1m == 1.5
        assert snapshots["post"].cpu.load_1m == 0.5

    def test_attach_replaces_existing(self, incident_with_db):
        """Should replace existing snapshot for same incident/which."""
        incident, conn = incident_with_db
        snap1 = make_telemetry_snapshot()
        snap2 = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=9.9, load_5m=9.9, load_15m=9.9),
            memory=MemoryPressure(total_mb=8000, available_mb=1000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
        )

        attach_telemetry_snapshot(incident.incident_id, "pre", snap1, conn=conn)
        attach_telemetry_snapshot(incident.incident_id, "pre", snap2, conn=conn)

        retrieved = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)
        assert retrieved is not None
        assert retrieved.cpu.load_1m == 9.9


class TestControlSurfaceSnapshotCRUD:
    """Tests for control surface snapshot CRUD operations."""

    def test_attach_control_surface_snapshot(self, incident_with_db):
        """Should attach control surface snapshot to incident."""
        incident, conn = incident_with_db
        snap = make_control_surface_snapshot()

        # Should not raise
        attach_control_surface_snapshot(incident.incident_id, "pre", snap, conn=conn)

    def test_get_control_surface_snapshot(self, incident_with_db):
        """Should retrieve attached control surface snapshot."""
        incident, conn = incident_with_db
        snap = make_control_surface_snapshot()

        attach_control_surface_snapshot(incident.incident_id, "pre", snap, conn=conn)
        retrieved = get_control_surface_snapshot(incident.incident_id, "pre", conn=conn)

        assert retrieved is not None
        assert retrieved.kernel_policy.kernel_version == "5.15.0"
        assert retrieved.hardware.cpu_cores == 4
        assert retrieved.network_control.network_manager == "NetworkManager"

    def test_get_control_surface_snapshot_not_found(self, incident_with_db):
        """Should return None for missing snapshot."""
        incident, conn = incident_with_db
        retrieved = get_control_surface_snapshot(incident.incident_id, "pre", conn=conn)
        assert retrieved is None

    def test_get_control_surface_snapshots_both(self, incident_with_db):
        """Should retrieve both pre and post snapshots."""
        incident, conn = incident_with_db
        snap_pre = make_control_surface_snapshot()
        snap_post = ControlSurfaceSnapshot(
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(network_manager="netplan"),
            kernel_policy=KernelPolicySurface(kernel_version="5.16.0"),
            hardware=HardwareIdentitySurface(cpu_cores=4),
        )

        attach_control_surface_snapshot(incident.incident_id, "pre", snap_pre, conn=conn)
        attach_control_surface_snapshot(incident.incident_id, "post", snap_post, conn=conn)

        snapshots = get_control_surface_snapshots(incident.incident_id, conn=conn)

        assert "pre" in snapshots
        assert "post" in snapshots
        assert snapshots["pre"].kernel_policy.kernel_version == "5.15.0"
        assert snapshots["post"].kernel_policy.kernel_version == "5.16.0"

    def test_attach_replaces_existing(self, incident_with_db):
        """Should replace existing snapshot for same incident/which."""
        incident, conn = incident_with_db
        snap1 = make_control_surface_snapshot()
        snap2 = ControlSurfaceSnapshot(
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(kernel_version="6.0.0"),
            hardware=HardwareIdentitySurface(),
        )

        attach_control_surface_snapshot(incident.incident_id, "pre", snap1, conn=conn)
        attach_control_surface_snapshot(incident.incident_id, "pre", snap2, conn=conn)

        retrieved = get_control_surface_snapshot(incident.incident_id, "pre", conn=conn)
        assert retrieved is not None
        assert retrieved.kernel_policy.kernel_version == "6.0.0"


class TestSurfaceHashes:
    """Tests for surface hash operations."""

    def test_get_surface_hashes(self, incident_with_db):
        """Should retrieve surface hashes without full snapshot."""
        incident, conn = incident_with_db
        snap = make_control_surface_snapshot()

        attach_control_surface_snapshot(incident.incident_id, "pre", snap, conn=conn)
        hashes = get_surface_hashes(incident.incident_id, "pre", conn=conn)

        assert hashes is not None
        assert "kernel_policy" in hashes
        assert "hardware" in hashes
        assert "packages" in hashes

    def test_get_surface_hashes_not_found(self, incident_with_db):
        """Should return None for missing snapshot."""
        incident, conn = incident_with_db
        hashes = get_surface_hashes(incident.incident_id, "pre", conn=conn)
        assert hashes is None

    def test_surface_hashes_are_consistent(self, incident_with_db):
        """Surface hashes should match snapshot.surface_hashes()."""
        incident, conn = incident_with_db
        snap = make_control_surface_snapshot()
        expected_hashes = snap.surface_hashes()

        attach_control_surface_snapshot(incident.incident_id, "pre", snap, conn=conn)
        stored_hashes = get_surface_hashes(incident.incident_id, "pre", conn=conn)

        assert stored_hashes is not None
        for key, value in expected_hashes.items():
            assert stored_hashes.get(key) == value


class TestSnapshotSerialization:
    """Tests for snapshot serialization/deserialization."""

    def test_telemetry_round_trip_preserves_data(self, incident_with_db):
        """Telemetry snapshot data should survive round-trip."""
        incident, conn = incident_with_db
        snap = make_telemetry_snapshot()

        attach_telemetry_snapshot(incident.incident_id, "pre", snap, conn=conn)
        retrieved = get_telemetry_snapshot(incident.incident_id, "pre", conn=conn)

        assert retrieved is not None
        # Check all domains
        assert retrieved.cpu.load_1m == snap.cpu.load_1m
        assert retrieved.cpu.load_5m == snap.cpu.load_5m
        assert retrieved.memory.total_mb == snap.memory.total_mb
        assert retrieved.memory.available_mb == snap.memory.available_mb
        assert retrieved.hostname == snap.hostname
        assert retrieved.uptime_sec == snap.uptime_sec

    def test_control_surface_round_trip_preserves_data(self, incident_with_db):
        """Control surface snapshot data should survive round-trip."""
        incident, conn = incident_with_db
        snap = make_control_surface_snapshot()

        attach_control_surface_snapshot(incident.incident_id, "pre", snap, conn=conn)
        retrieved = get_control_surface_snapshot(incident.incident_id, "pre", conn=conn)

        assert retrieved is not None
        # Check all surfaces
        assert retrieved.kernel_policy.kernel_version == snap.kernel_policy.kernel_version
        assert retrieved.hardware.cpu_cores == snap.hardware.cpu_cores
        assert retrieved.hardware.total_memory_mb == snap.hardware.total_memory_mb
        assert retrieved.network_control.network_manager == snap.network_control.network_manager
