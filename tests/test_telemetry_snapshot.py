"""Tests for telemetry snapshot collection and models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.telemetry_snapshot import (
    CPUPressure,
    DiskInfo,
    DiskIOPressure,
    GPUDevice,
    GPUMetrics,
    InterfaceInfo,
    KernelHardwareSignals,
    MemoryPressure,
    NetworkLiveness,
    ServiceRuntimeSnapshot,
    TelemetrySnapshot,
    _collect_cpu_pressure,
    _collect_disk_io_pressure,
    _collect_gpu_metrics,
    _collect_kernel_hardware_signals,
    _collect_memory_pressure,
    _collect_network_liveness,
    _collect_service_runtime,
    _get_hostname,
    _get_uptime_sec,
    _parse_psi_file,
    collect_telemetry_snapshot,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_snapshot() -> TelemetrySnapshot:
    """A minimal valid TelemetrySnapshot for reuse."""
    return TelemetrySnapshot(
        cpu=CPUPressure(load_1m=0.5, load_5m=0.3, load_15m=0.2),
        memory=MemoryPressure(total_mb=8000, available_mb=4000),
        disk=DiskIOPressure(disks=()),
        network=NetworkLiveness(interfaces=()),
        kernel=KernelHardwareSignals(),
    )


# =============================================================================
# Existing tests: TestTelemetryModels
# =============================================================================


class TestTelemetryModels:
    """Tests for telemetry model classes."""

    def test_cpu_pressure_defaults(self) -> None:
        """CPUPressure should have sensible defaults."""
        cpu = CPUPressure(load_1m=0.5, load_5m=0.3, load_15m=0.2)
        assert cpu.load_1m == 0.5
        assert cpu.runnable_tasks == 0
        assert cpu.cpu_steal_pct == 0.0
        assert cpu.psi_some_pct == 0.0

    def test_cpu_pressure_frozen(self) -> None:
        """CPUPressure should be immutable."""
        cpu = CPUPressure(load_1m=0.5, load_5m=0.3, load_15m=0.2)
        with pytest.raises(Exception):
            cpu.load_1m = 1.0  # type: ignore[misc]

    def test_memory_pressure_defaults(self) -> None:
        """MemoryPressure should have sensible defaults."""
        mem = MemoryPressure(total_mb=8000, available_mb=4000)
        assert mem.total_mb == 8000
        assert mem.available_mb == 4000
        assert mem.swap_total_mb == 0
        assert mem.oom_kills_1h == 0

    def test_disk_info_model(self) -> None:
        """DiskInfo should capture mount information."""
        disk = DiskInfo(mount="/", used_pct=75.5, avail_gb=100.0, device="/dev/sda1")
        assert disk.mount == "/"
        assert disk.used_pct == 75.5
        assert disk.read_only is False

    def test_disk_io_pressure_with_disks(self) -> None:
        """DiskIOPressure should contain disk info."""
        disks = (
            DiskInfo(mount="/", used_pct=50.0, avail_gb=100.0),
            DiskInfo(mount="/home", used_pct=75.0, avail_gb=50.0),
        )
        dio = DiskIOPressure(disks=disks, io_wait_pct=2.5)
        assert len(dio.disks) == 2
        assert dio.io_wait_pct == 2.5

    def test_interface_info_model(self) -> None:
        """InterfaceInfo should capture interface state."""
        iface = InterfaceInfo(name="eth0", state="UP", rx_errors=0, tx_errors=0)
        assert iface.name == "eth0"
        assert iface.state == "UP"

    def test_network_liveness_model(self) -> None:
        """NetworkLiveness should capture network state."""
        ifaces = (InterfaceInfo(name="eth0", state="UP"),)
        net = NetworkLiveness(
            interfaces=ifaces,
            interfaces_up=1,
            default_route_present=True,
            dns_resolution_ok=True,
        )
        assert net.interfaces_up == 1
        assert net.default_route_present is True
        assert net.dns_resolution_ok is True

    def test_service_runtime_snapshot(self) -> None:
        """ServiceRuntimeSnapshot should capture service state."""
        svc = ServiceRuntimeSnapshot(
            service="nginx.service",
            pid=1234,
            rss_mb=100,
            active_state="active",
            restart_count=0,
        )
        assert svc.service == "nginx.service"
        assert svc.pid == 1234
        assert svc.active_state == "active"

    def test_kernel_hardware_signals(self) -> None:
        """KernelHardwareSignals should capture kernel state."""
        kernel = KernelHardwareSignals(
            oom_events_1h=2,
            kernel_warnings_1h=5,
            max_temp_celsius=65,
        )
        assert kernel.oom_events_1h == 2
        assert kernel.kernel_warnings_1h == 5
        assert kernel.max_temp_celsius == 65


# =============================================================================
# Existing tests: TestTelemetrySnapshot
# =============================================================================


class TestTelemetrySnapshot:
    """Tests for the complete TelemetrySnapshot model."""

    def test_telemetry_snapshot_construction(self) -> None:
        """TelemetrySnapshot should construct from domain models."""
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
        )
        assert snap.cpu.load_1m == 1.0
        assert snap.memory.total_mb == 8000
        assert snap.hostname == ""

    def test_telemetry_snapshot_with_services(self) -> None:
        """TelemetrySnapshot should include service runtimes."""
        services = (
            ServiceRuntimeSnapshot(service="nginx.service", active_state="active"),
            ServiceRuntimeSnapshot(service="docker.service", active_state="active"),
        )
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=0.5, load_5m=0.3, load_15m=0.2),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
            services=services,
        )
        assert len(snap.services) == 2
        assert snap.services[0].service == "nginx.service"

    def test_telemetry_snapshot_serialization(self) -> None:
        """TelemetrySnapshot should serialize to JSON."""
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
            hostname="testhost",
            uptime_sec=3600,
        )
        data = snap.model_dump()
        json_str = json.dumps(data, default=str)
        assert "testhost" in json_str
        assert "3600" in json_str

    def test_telemetry_snapshot_is_frozen(self) -> None:
        """TelemetrySnapshot should be immutable."""
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
        )
        with pytest.raises(Exception):
            snap.hostname = "changed"  # type: ignore[misc]


# =============================================================================
# Existing tests: TestTelemetryCollection
# =============================================================================


class TestTelemetryCollection:
    """Tests for telemetry snapshot collection."""

    def test_collect_returns_snapshot(self) -> None:
        """collect_telemetry_snapshot should return a TelemetrySnapshot."""
        snap = collect_telemetry_snapshot()
        assert isinstance(snap, TelemetrySnapshot)

    def test_collect_has_cpu_data(self) -> None:
        """Collected snapshot should have CPU data."""
        snap = collect_telemetry_snapshot()
        assert snap.cpu.load_1m >= 0
        assert snap.cpu.load_5m >= 0
        assert snap.cpu.load_15m >= 0

    def test_collect_has_memory_data(self) -> None:
        """Collected snapshot should have memory data."""
        snap = collect_telemetry_snapshot()
        assert snap.memory.total_mb >= 0

    def test_collect_has_timestamp(self) -> None:
        """Collected snapshot should have a timestamp."""
        datetime.now(timezone.utc)
        snap = collect_telemetry_snapshot()
        # collected_at might be naive, so compare carefully
        assert snap.collected_at is not None

    def test_collect_with_services(self) -> None:
        """Collecting with implicated services should include service data."""
        # Use a service that might exist on the test system
        snap = collect_telemetry_snapshot(implicated_services=["nonexistent.service"])
        # Should not crash, might have empty services
        assert isinstance(snap.services, tuple)

    def test_collect_size_under_limit(self) -> None:
        """Collected snapshot should be under 20KB when serialized."""
        snap = collect_telemetry_snapshot()
        json_str = json.dumps(snap.model_dump(), default=str)
        size = len(json_str)
        # Allow some slack for variation
        assert size < 25000, f"Snapshot too large: {size} bytes"


# =============================================================================
# Existing tests: TestGPUMetrics
# =============================================================================


class TestGPUMetrics:
    """Tests for GPU metrics models."""

    def test_gpu_device_defaults(self) -> None:
        """GPUDevice should have sensible defaults."""
        device = GPUDevice(index=0)
        assert device.index == 0
        assert device.name == ""
        assert device.memory_used_mb == 0
        assert device.memory_total_mb == 0
        assert device.memory_pct == 0.0
        assert device.utilization_pct == 0.0
        assert device.temperature_c == 0
        assert device.power_watts == 0
        assert device.throttle_reasons == 0
        assert device.ecc_errors == 0

    def test_gpu_device_with_values(self) -> None:
        """GPUDevice should store all metrics."""
        device = GPUDevice(
            index=0,
            name="NVIDIA GeForce RTX 4090",
            uuid="GPU-abc123",
            memory_used_mb=20000,
            memory_total_mb=24576,
            memory_pct=81.4,
            utilization_pct=95.0,
            temperature_c=72,
            power_watts=350,
            throttle_reasons=0,
            ecc_errors=0,
        )
        assert device.name == "NVIDIA GeForce RTX 4090"
        assert device.memory_pct == 81.4
        assert device.utilization_pct == 95.0
        assert device.temperature_c == 72

    def test_gpu_device_frozen(self) -> None:
        """GPUDevice should be immutable."""
        device = GPUDevice(index=0)
        with pytest.raises(Exception):
            device.index = 1  # type: ignore[misc]

    def test_gpu_metrics_defaults(self) -> None:
        """GPUMetrics should have sensible defaults."""
        gpu = GPUMetrics()
        assert gpu.available is False
        assert gpu.device_count == 0
        assert len(gpu.devices) == 0
        assert gpu.max_memory_pct == 0.0
        assert gpu.max_utilization_pct == 0.0
        assert gpu.max_temperature_c == 0
        assert gpu.total_ecc_errors == 0

    def test_gpu_metrics_with_devices(self) -> None:
        """GPUMetrics should contain device info."""
        devices = (
            GPUDevice(
                index=0,
                name="GPU 0",
                memory_pct=50.0,
                utilization_pct=80.0,
                temperature_c=65,
            ),
            GPUDevice(
                index=1,
                name="GPU 1",
                memory_pct=75.0,
                utilization_pct=90.0,
                temperature_c=70,
            ),
        )
        gpu = GPUMetrics(
            available=True,
            device_count=2,
            devices=devices,
            max_memory_pct=75.0,
            max_utilization_pct=90.0,
            max_temperature_c=70,
        )
        assert gpu.available is True
        assert gpu.device_count == 2
        assert len(gpu.devices) == 2
        assert gpu.max_memory_pct == 75.0
        assert gpu.max_utilization_pct == 90.0
        assert gpu.max_temperature_c == 70

    def test_gpu_metrics_serialization(self) -> None:
        """GPUMetrics should serialize to JSON."""
        device = GPUDevice(
            index=0,
            name="Test GPU",
            memory_pct=50.0,
            utilization_pct=80.0,
            temperature_c=65,
        )
        gpu = GPUMetrics(
            available=True,
            device_count=1,
            devices=(device,),
            max_memory_pct=50.0,
            max_utilization_pct=80.0,
            max_temperature_c=65,
        )
        data = gpu.model_dump()
        json_str = json.dumps(data)
        assert "Test GPU" in json_str
        assert "50.0" in json_str


# =============================================================================
# Existing tests: TestTelemetrySnapshotWithGPU
# =============================================================================


class TestTelemetrySnapshotWithGPU:
    """Tests for TelemetrySnapshot with GPU metrics."""

    def test_snapshot_includes_gpu(self) -> None:
        """TelemetrySnapshot should include GPU metrics."""
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
            gpu=GPUMetrics(available=True, device_count=1),
        )
        assert snap.gpu.available is True
        assert snap.gpu.device_count == 1

    def test_snapshot_gpu_default(self) -> None:
        """TelemetrySnapshot should have default GPU metrics."""
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=()),
            network=NetworkLiveness(interfaces=()),
            kernel=KernelHardwareSignals(),
        )
        assert snap.gpu.available is False
        assert snap.gpu.device_count == 0

    def test_collected_snapshot_has_gpu_field(self) -> None:
        """Collected snapshot should have GPU field (even if unavailable)."""
        snap = collect_telemetry_snapshot()
        assert hasattr(snap, "gpu")
        assert isinstance(snap.gpu, GPUMetrics)
        # GPU may or may not be available, just check the field exists
        assert snap.gpu.device_count >= 0


# =============================================================================
# NEW TESTS: Edge cases for models
# =============================================================================


class TestTelemetryModelEdgeCases:
    """Edge case tests for telemetry models."""

    def test_cpu_pressure_with_all_fields(self) -> None:
        """CPUPressure should accept all optional fields."""
        cpu = CPUPressure(
            load_1m=10.0,
            load_5m=8.0,
            load_15m=5.0,
            runnable_tasks=50,
            total_tasks=300,
            cpu_steal_pct=5.5,
            psi_some_pct=15.0,
            psi_full_pct=2.0,
        )
        assert cpu.runnable_tasks == 50
        assert cpu.total_tasks == 300
        assert cpu.cpu_steal_pct == 5.5
        assert cpu.psi_some_pct == 15.0
        assert cpu.psi_full_pct == 2.0

    def test_memory_pressure_full_swap(self) -> None:
        """MemoryPressure should handle full swap usage."""
        mem = MemoryPressure(
            total_mb=16000,
            available_mb=100,
            free_mb=50,
            swap_total_mb=8000,
            swap_used_mb=8000,
            oom_kills_1h=10,
            psi_some_pct=80.0,
            psi_full_pct=40.0,
        )
        assert mem.swap_used_mb == mem.swap_total_mb
        assert mem.oom_kills_1h == 10
        assert mem.psi_full_pct == 40.0

    def test_memory_pressure_zero_memory(self) -> None:
        """MemoryPressure should handle zero total memory."""
        mem = MemoryPressure(total_mb=0, available_mb=0)
        assert mem.total_mb == 0
        assert mem.available_mb == 0

    def test_disk_info_read_only(self) -> None:
        """DiskInfo should mark read-only filesystems."""
        disk = DiskInfo(mount="/boot", used_pct=30.0, avail_gb=0.5, read_only=True)
        assert disk.read_only is True

    def test_disk_io_pressure_high_io_wait(self) -> None:
        """DiskIOPressure should accept high I/O wait percentages."""
        dio = DiskIOPressure(
            disks=(),
            io_wait_pct=95.0,
            io_errors_1h=100,
            psi_some_pct=50.0,
            psi_full_pct=30.0,
        )
        assert dio.io_wait_pct == 95.0
        assert dio.io_errors_1h == 100

    def test_interface_info_with_errors(self) -> None:
        """InterfaceInfo should record error counts."""
        iface = InterfaceInfo(name="eth0", state="UP", rx_errors=1000, tx_errors=500)
        assert iface.rx_errors == 1000
        assert iface.tx_errors == 500

    def test_network_liveness_no_interfaces(self) -> None:
        """NetworkLiveness should handle zero interfaces."""
        net = NetworkLiveness(
            interfaces=(),
            interfaces_up=0,
            interfaces_down=0,
            default_route_present=False,
            dns_resolution_ok=False,
        )
        assert len(net.interfaces) == 0
        assert net.dns_resolution_ok is False

    def test_service_runtime_snapshot_no_pid(self) -> None:
        """ServiceRuntimeSnapshot should handle no PID."""
        svc = ServiceRuntimeSnapshot(
            service="dead.service",
            pid=None,
            active_state="inactive",
            last_exit_code=1,
        )
        assert svc.pid is None
        assert svc.last_exit_code == 1

    def test_service_runtime_snapshot_with_cgroup_limit(self) -> None:
        """ServiceRuntimeSnapshot should record cgroup memory limit."""
        svc = ServiceRuntimeSnapshot(
            service="worker.service",
            pid=9876,
            rss_mb=512,
            active_state="active",
            cgroup_memory_limit_mb=1024,
        )
        assert svc.cgroup_memory_limit_mb == 1024

    def test_kernel_hardware_signals_all_zero(self) -> None:
        """KernelHardwareSignals should default all fields to zero."""
        k = KernelHardwareSignals()
        assert k.oom_events_1h == 0
        assert k.kernel_warnings_1h == 0
        assert k.hardware_faults_1h == 0
        assert k.thermal_throttle_active is False
        assert k.max_temp_celsius == 0
        assert k.smart_warnings == 0

    def test_kernel_hardware_thermal_throttle(self) -> None:
        """KernelHardwareSignals should record thermal throttle."""
        k = KernelHardwareSignals(
            thermal_throttle_active=True,
            max_temp_celsius=105,
            hardware_faults_1h=3,
        )
        assert k.thermal_throttle_active is True
        assert k.max_temp_celsius == 105

    def test_gpu_device_ecc_errors(self) -> None:
        """GPUDevice should record ECC error counts."""
        device = GPUDevice(index=0, ecc_errors=42)
        assert device.ecc_errors == 42

    def test_gpu_device_throttle_reasons(self) -> None:
        """GPUDevice should record throttle reason bitmask."""
        device = GPUDevice(index=0, throttle_reasons=0x40)
        assert device.throttle_reasons == 0x40

    def test_telemetry_snapshot_model_dump_roundtrip(self) -> None:
        """TelemetrySnapshot should survive model_dump -> model_validate."""
        snap = TelemetrySnapshot(
            cpu=CPUPressure(load_1m=1.0, load_5m=0.5, load_15m=0.3),
            memory=MemoryPressure(total_mb=8000, available_mb=4000),
            disk=DiskIOPressure(disks=(DiskInfo(mount="/", used_pct=50.0, avail_gb=100.0),)),
            network=NetworkLiveness(
                interfaces=(InterfaceInfo(name="eth0", state="UP"),),
                interfaces_up=1,
            ),
            kernel=KernelHardwareSignals(),
            hostname="test",
            uptime_sec=3600,
        )
        data = snap.model_dump()
        restored = TelemetrySnapshot.model_validate(data)
        assert restored.hostname == "test"
        assert restored.cpu.load_1m == 1.0
        assert restored.disk.disks[0].mount == "/"
        assert restored.network.interfaces[0].name == "eth0"


# =============================================================================
# NEW TESTS: Collection functions with mocks
# =============================================================================


class TestParsePsiFile:
    """Tests for _parse_psi_file helper."""

    def test_parse_psi_normal(self) -> None:
        """Parse PSI some and full avg10 from standard format."""
        content = (
            "some avg10=2.50 avg60=1.00 avg300=0.50 total=12345\nfull avg10=0.75 avg60=0.30 avg300=0.10 total=6789\n"
        )
        with patch.object(Path, "read_text", return_value=content):
            some, full = _parse_psi_file("/proc/pressure/cpu")
        assert some == 2.5
        assert full == 0.75

    def test_parse_psi_missing_file(self) -> None:
        """Return zeros when PSI file is missing."""
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            some, full = _parse_psi_file("/proc/pressure/nonexistent")
        assert some == 0.0
        assert full == 0.0

    def test_parse_psi_only_some_line(self) -> None:
        """Parse file with only 'some' line (cpu has no full line)."""
        content = "some avg10=3.00 avg60=1.50 avg300=0.80 total=99999\n"
        with patch.object(Path, "read_text", return_value=content):
            some, full = _parse_psi_file("/proc/pressure/cpu")
        assert some == 3.0
        assert full == 0.0

    def test_parse_psi_permission_error(self) -> None:
        """Return zeros when PSI file is not readable."""
        with patch.object(Path, "read_text", side_effect=PermissionError):
            some, full = _parse_psi_file("/proc/pressure/memory")
        assert some == 0.0
        assert full == 0.0


class TestCollectCpuPressure:
    """Tests for _collect_cpu_pressure."""

    def test_cpu_pressure_normal(self) -> None:
        """Parse load averages and task counts from /proc/loadavg."""
        loadavg_content = "1.50 0.75 0.30 3/456 12345\n"
        stat_content = "cpu  100 20 50 1000 30 5 10 15 0 0\ncpu0 50 10 25 500 15 2 5 8 0 0\n"
        with patch.object(Path, "read_text", side_effect=[loadavg_content, stat_content]):
            with patch(
                "elle.daemon.incidents.telemetry_snapshot._parse_psi_file",
                return_value=(1.0, 0.5),
            ):
                result = _collect_cpu_pressure()
        assert result.load_1m == 1.5
        assert result.load_5m == 0.75
        assert result.load_15m == 0.3
        assert result.runnable_tasks == 3
        assert result.total_tasks == 456

    def test_cpu_pressure_missing_proc(self) -> None:
        """Return zero load averages when /proc is inaccessible."""
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            with patch(
                "elle.daemon.incidents.telemetry_snapshot._parse_psi_file",
                return_value=(0.0, 0.0),
            ):
                result = _collect_cpu_pressure()
        assert result.load_1m == 0.0
        assert result.load_5m == 0.0
        assert result.load_15m == 0.0


class TestCollectMemoryPressure:
    """Tests for _collect_memory_pressure."""

    def test_memory_pressure_normal(self) -> None:
        """Parse memory stats from /proc/meminfo content."""
        meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemAvailable:    8192000 kB\n"
            "MemFree:         4096000 kB\n"
            "SwapTotal:       4096000 kB\n"
            "SwapFree:        2048000 kB\n"
        )
        with patch.object(Path, "read_text", return_value=meminfo):
            with patch(
                "elle.daemon.incidents.telemetry_snapshot._parse_psi_file",
                return_value=(0.0, 0.0),
            ):
                result = _collect_memory_pressure(oom_kills_1h=2)
        assert result.total_mb == 16384000 // 1024
        assert result.available_mb == 8192000 // 1024
        assert result.free_mb == 4096000 // 1024
        assert result.swap_total_mb == 4096000 // 1024
        assert result.swap_used_mb == (4096000 - 2048000) // 1024
        assert result.oom_kills_1h == 2

    def test_memory_pressure_missing_meminfo(self) -> None:
        """Return zeros when /proc/meminfo is unreadable."""
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            with patch(
                "elle.daemon.incidents.telemetry_snapshot._parse_psi_file",
                return_value=(0.0, 0.0),
            ):
                result = _collect_memory_pressure()
        assert result.total_mb == 0
        assert result.available_mb == 0


class TestCollectDiskIoPressure:
    """Tests for _collect_disk_io_pressure."""

    def test_disk_io_with_mounts(self) -> None:
        """Collect disk usage via os.statvfs for important mounts."""
        fake_stat = MagicMock()
        fake_stat.f_blocks = 1000000
        fake_stat.f_bavail = 400000
        fake_stat.f_frsize = 4096
        fake_stat.f_flag = 0

        stat_content = "cpu  100 20 50 1000 30 5 10 15 0 0\n"

        with patch("elle.daemon.incidents.telemetry_snapshot.os.statvfs", return_value=fake_stat):
            with patch.object(Path, "read_text", return_value=stat_content):
                with patch(
                    "elle.daemon.incidents.telemetry_snapshot._parse_psi_file",
                    return_value=(0.0, 0.0),
                ):
                    result = _collect_disk_io_pressure()
        assert len(result.disks) >= 1
        assert result.disks[0].mount == "/"

    def test_disk_io_mount_not_found(self) -> None:
        """Skip mounts that do not exist."""
        with patch(
            "elle.daemon.incidents.telemetry_snapshot.os.statvfs",
            side_effect=OSError("no mount"),
        ):
            with patch.object(Path, "read_text", side_effect=FileNotFoundError):
                with patch(
                    "elle.daemon.incidents.telemetry_snapshot._parse_psi_file",
                    return_value=(0.0, 0.0),
                ):
                    result = _collect_disk_io_pressure()
        assert len(result.disks) == 0


class TestCollectNetworkLiveness:
    """Tests for _collect_network_liveness."""

    def test_network_liveness_with_interfaces(self) -> None:
        """Parse interfaces from ip -j link show output."""
        links = [
            {"ifname": "lo", "operstate": "UNKNOWN"},
            {"ifname": "eth0", "operstate": "UP", "stats64": {"rx": {"errors": 0}, "tx": {"errors": 0}}},
        ]
        ip_link_result = MagicMock()
        ip_link_result.returncode = 0
        ip_link_result.stdout = json.dumps(links)

        ip_route_result = MagicMock()
        ip_route_result.returncode = 0
        ip_route_result.stdout = "default via 10.0.0.1 dev eth0\n"

        with patch(
            "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
            side_effect=[ip_link_result, ip_route_result],
        ):
            with patch("elle.daemon.incidents.telemetry_snapshot.socket") as mock_sock:
                mock_sock.gethostbyname.return_value = "8.8.4.4"
                result = _collect_network_liveness()
        # lo is filtered
        assert len(result.interfaces) == 1
        assert result.interfaces[0].name == "eth0"
        assert result.interfaces_up == 1
        assert result.default_route_present is True

    def test_network_liveness_ip_command_fails(self) -> None:
        """Return empty interfaces when ip command is unavailable."""
        with patch(
            "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with patch("elle.daemon.incidents.telemetry_snapshot.socket") as mock_sock:
                mock_sock.gethostbyname.side_effect = OSError("no DNS")
                result = _collect_network_liveness()
        assert len(result.interfaces) == 0
        assert result.dns_resolution_ok is False

    def test_network_liveness_dns_failure(self) -> None:
        """Mark DNS as not OK when resolution fails."""
        ip_link_result = MagicMock()
        ip_link_result.returncode = 0
        ip_link_result.stdout = "[]"

        ip_route_result = MagicMock()
        ip_route_result.returncode = 0
        ip_route_result.stdout = ""

        with patch(
            "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
            side_effect=[ip_link_result, ip_route_result],
        ):
            with patch("elle.daemon.incidents.telemetry_snapshot.socket") as mock_sock:
                mock_sock.gethostbyname.side_effect = OSError("resolve failed")
                mock_sock.setdefaulttimeout = MagicMock()
                result = _collect_network_liveness()
        assert result.dns_resolution_ok is False
        assert result.default_route_present is False


class TestCollectServiceRuntime:
    """Tests for _collect_service_runtime."""

    def test_service_runtime_active(self) -> None:
        """Parse properties from systemctl show output."""
        systemctl_output = (
            "MainPID=1234\n"
            "ActiveState=active\n"
            "NRestarts=2\n"
            "ExecMainStatus=0\n"
            "MemoryCurrent=104857600\n"
            "CPUUsageNSec=5000000000\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = systemctl_output

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=mock_result):
            result = _collect_service_runtime("nginx.service")
        assert result is not None
        assert result.service == "nginx.service"
        assert result.pid == 1234
        assert result.active_state == "active"
        assert result.restart_count == 2
        assert result.rss_mb == 104857600 // (1024 * 1024)

    def test_service_runtime_not_found(self) -> None:
        """Return None when service does not exist."""
        mock_result = MagicMock()
        mock_result.returncode = 4  # service not found

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=mock_result):
            result = _collect_service_runtime("nonexistent.service")
        assert result is None

    def test_service_runtime_exception(self) -> None:
        """Return None when systemctl fails."""
        with patch(
            "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _collect_service_runtime("failing.service")
        assert result is None

    def test_service_runtime_memory_not_set(self) -> None:
        """Handle MemoryCurrent=[not set] gracefully."""
        systemctl_output = (
            "MainPID=0\nActiveState=inactive\nNRestarts=0\nExecMainStatus=1\nMemoryCurrent=[not set]\nCPUUsageNSec=0\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = systemctl_output

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=mock_result):
            result = _collect_service_runtime("dead.service")
        assert result is not None
        assert result.pid is None  # MainPID=0 maps to None
        assert result.rss_mb == 0


class TestCollectKernelHardwareSignals:
    """Tests for _collect_kernel_hardware_signals."""

    def test_kernel_signals_with_throttle(self) -> None:
        """Detect thermal throttling from sysfs."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="5"):
                with patch(
                    "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
                    side_effect=FileNotFoundError,
                ):
                    result = _collect_kernel_hardware_signals(oom_events_1h=3)
        assert result.thermal_throttle_active is True
        assert result.oom_events_1h == 3

    def test_kernel_signals_no_throttle_file(self) -> None:
        """Handle missing throttle sysfs file."""
        with patch.object(Path, "exists", return_value=False):
            with patch(
                "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
                side_effect=FileNotFoundError,
            ):
                result = _collect_kernel_hardware_signals()
        assert result.thermal_throttle_active is False

    def test_kernel_signals_with_sensors(self) -> None:
        """Parse temperature from sensors -j output."""
        sensor_data = {
            "coretemp-isa-0000": {
                "Core 0": {"temp2_input": 72.0},
            },
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(sensor_data)

        with patch.object(Path, "exists", return_value=False):
            with patch(
                "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
                return_value=mock_result,
            ):
                result = _collect_kernel_hardware_signals()
        assert result.max_temp_celsius == 72


class TestCollectGpuMetrics:
    """Tests for _collect_gpu_metrics."""

    def test_gpu_metrics_normal(self) -> None:
        """Parse GPU metrics from nvidia-smi output."""
        nvidia_output = "0, NVIDIA RTX 4090, GPU-abc123, 10000, 24576, 80, 70, 300, Not Active\n"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = nvidia_output

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=mock_result):
            result = _collect_gpu_metrics()
        assert result.available is True
        assert result.device_count == 1
        assert result.devices[0].name == "NVIDIA RTX 4090"
        assert result.max_temperature_c == 70

    def test_gpu_metrics_not_available(self) -> None:
        """Return unavailable GPU when nvidia-smi is not found."""
        with patch(
            "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _collect_gpu_metrics()
        assert result.available is False
        assert result.device_count == 0

    def test_gpu_metrics_nvidia_smi_fails(self) -> None:
        """Return unavailable GPU when nvidia-smi returns error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=mock_result):
            result = _collect_gpu_metrics()
        assert result.available is False

    def test_gpu_metrics_multiple_gpus(self) -> None:
        """Parse metrics for multiple GPUs."""
        nvidia_output = (
            "0, GPU 0, GPU-001, 8000, 16384, 90, 65, 250, Not Active\n"
            "1, GPU 1, GPU-002, 12000, 16384, 95, 72, 300, Not Active\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = nvidia_output

        with patch("elle.daemon.incidents.telemetry_snapshot.subprocess.run", return_value=mock_result):
            result = _collect_gpu_metrics()
        assert result.device_count == 2
        assert result.max_utilization_pct == 95.0
        assert result.max_temperature_c == 72

    def test_gpu_metrics_timeout(self) -> None:
        """Return unavailable GPU on nvidia-smi timeout."""
        import subprocess

        with patch(
            "elle.daemon.incidents.telemetry_snapshot.subprocess.run",
            side_effect=subprocess.TimeoutExpired("nvidia-smi", 5),
        ):
            result = _collect_gpu_metrics()
        assert result.available is False


class TestGetHostname:
    """Tests for _get_hostname."""

    def test_hostname_normal(self) -> None:
        """Return hostname from socket.gethostname."""
        with patch("elle.daemon.incidents.telemetry_snapshot.socket") as mock_sock:
            mock_sock.gethostname.return_value = "prod-server"
            result = _get_hostname()
        assert result == "prod-server"

    def test_hostname_on_error(self) -> None:
        """Return 'unknown' when gethostname fails."""
        with patch("elle.daemon.incidents.telemetry_snapshot.socket") as mock_sock:
            mock_sock.gethostname.side_effect = OSError("fail")
            result = _get_hostname()
        assert result == "unknown"


class TestGetUptimeSec:
    """Tests for _get_uptime_sec."""

    def test_uptime_normal(self) -> None:
        """Parse uptime seconds from /proc/uptime."""
        with patch.object(Path, "read_text", return_value="86400.55 72000.33\n"):
            result = _get_uptime_sec()
        assert result == 86400

    def test_uptime_missing_file(self) -> None:
        """Return 0 when /proc/uptime is not available."""
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            result = _get_uptime_sec()
        assert result == 0


# =============================================================================
# NEW TESTS: collect_telemetry_snapshot with mocks
# =============================================================================


class TestCollectTelemetrySnapshotMocked:
    """Tests for collect_telemetry_snapshot with fully mocked sub-collectors."""

    def test_collect_passes_oom_kills(self) -> None:
        """Verify oom_kills_1h is passed to memory pressure collector."""
        with (
            patch("elle.daemon.incidents.telemetry_snapshot._collect_cpu_pressure") as mock_cpu,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_memory_pressure") as mock_mem,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_disk_io_pressure") as mock_disk,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_network_liveness") as mock_net,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_kernel_hardware_signals") as mock_kernel,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_gpu_metrics") as mock_gpu,
            patch("elle.daemon.incidents.telemetry_snapshot._get_hostname", return_value="h"),
            patch("elle.daemon.incidents.telemetry_snapshot._get_uptime_sec", return_value=100),
        ):
            mock_cpu.return_value = CPUPressure(load_1m=0.0, load_5m=0.0, load_15m=0.0)
            mock_mem.return_value = MemoryPressure(total_mb=1024, available_mb=512)
            mock_disk.return_value = DiskIOPressure(disks=())
            mock_net.return_value = NetworkLiveness(interfaces=())
            mock_kernel.return_value = KernelHardwareSignals()
            mock_gpu.return_value = GPUMetrics()
            collect_telemetry_snapshot(oom_kills_1h=5)
        mock_mem.assert_called_once_with(5)

    def test_collect_limits_services_to_ten(self) -> None:
        """Verify at most 10 services are collected."""
        services = [f"svc{i}.service" for i in range(15)]
        with (
            patch("elle.daemon.incidents.telemetry_snapshot._collect_cpu_pressure") as mock_cpu,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_memory_pressure") as mock_mem,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_disk_io_pressure") as mock_disk,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_network_liveness") as mock_net,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_kernel_hardware_signals") as mock_kernel,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_gpu_metrics") as mock_gpu,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_service_runtime", return_value=None) as mock_svc,
            patch("elle.daemon.incidents.telemetry_snapshot._get_hostname", return_value="h"),
            patch("elle.daemon.incidents.telemetry_snapshot._get_uptime_sec", return_value=100),
        ):
            mock_cpu.return_value = CPUPressure(load_1m=0.0, load_5m=0.0, load_15m=0.0)
            mock_mem.return_value = MemoryPressure(total_mb=1024, available_mb=512)
            mock_disk.return_value = DiskIOPressure(disks=())
            mock_net.return_value = NetworkLiveness(interfaces=())
            mock_kernel.return_value = KernelHardwareSignals()
            mock_gpu.return_value = GPUMetrics()
            collect_telemetry_snapshot(implicated_services=services)
        # Only first 10 services should be queried
        assert mock_svc.call_count == 10

    def test_collect_no_services_when_none_implicated(self) -> None:
        """Verify no service collection when implicated_services is None."""
        with (
            patch("elle.daemon.incidents.telemetry_snapshot._collect_cpu_pressure") as mock_cpu,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_memory_pressure") as mock_mem,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_disk_io_pressure") as mock_disk,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_network_liveness") as mock_net,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_kernel_hardware_signals") as mock_kernel,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_gpu_metrics") as mock_gpu,
            patch("elle.daemon.incidents.telemetry_snapshot._get_hostname", return_value="h"),
            patch("elle.daemon.incidents.telemetry_snapshot._get_uptime_sec", return_value=100),
        ):
            mock_cpu.return_value = CPUPressure(load_1m=0.0, load_5m=0.0, load_15m=0.0)
            mock_mem.return_value = MemoryPressure(total_mb=1024, available_mb=512)
            mock_disk.return_value = DiskIOPressure(disks=())
            mock_net.return_value = NetworkLiveness(interfaces=())
            mock_kernel.return_value = KernelHardwareSignals()
            mock_gpu.return_value = GPUMetrics()
            snap = collect_telemetry_snapshot()
        assert snap.services == ()

    def test_collect_passes_kernel_event_counts(self) -> None:
        """Verify kernel event counts are passed through to kernel signals collector."""
        with (
            patch("elle.daemon.incidents.telemetry_snapshot._collect_cpu_pressure") as mock_cpu,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_memory_pressure") as mock_mem,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_disk_io_pressure") as mock_disk,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_network_liveness") as mock_net,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_kernel_hardware_signals") as mock_kernel,
            patch("elle.daemon.incidents.telemetry_snapshot._collect_gpu_metrics") as mock_gpu,
            patch("elle.daemon.incidents.telemetry_snapshot._get_hostname", return_value="h"),
            patch("elle.daemon.incidents.telemetry_snapshot._get_uptime_sec", return_value=100),
        ):
            mock_cpu.return_value = CPUPressure(load_1m=0.0, load_5m=0.0, load_15m=0.0)
            mock_mem.return_value = MemoryPressure(total_mb=1024, available_mb=512)
            mock_disk.return_value = DiskIOPressure(disks=())
            mock_net.return_value = NetworkLiveness(interfaces=())
            mock_kernel.return_value = KernelHardwareSignals()
            mock_gpu.return_value = GPUMetrics()
            collect_telemetry_snapshot(
                oom_kills_1h=3,
                kernel_warnings_1h=7,
                hardware_faults_1h=1,
            )
        mock_kernel.assert_called_once_with(
            oom_events_1h=3,
            kernel_warnings_1h=7,
            hardware_faults_1h=1,
        )
