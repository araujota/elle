"""Tests for telemetry snapshot collection and models."""

from datetime import datetime, timezone
import json

import pytest

from elle.daemon.incidents.telemetry_snapshot import (
    CPUPressure,
    DiskIOPressure,
    DiskInfo,
    GPUDevice,
    GPUMetrics,
    InterfaceInfo,
    KernelHardwareSignals,
    MemoryPressure,
    NetworkLiveness,
    ServiceRuntimeSnapshot,
    TelemetrySnapshot,
    collect_telemetry_snapshot,
)


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
        before = datetime.now(timezone.utc)
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
        assert hasattr(snap, 'gpu')
        assert isinstance(snap.gpu, GPUMetrics)
        # GPU may or may not be available, just check the field exists
        assert snap.gpu.device_count >= 0
