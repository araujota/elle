"""Telemetry snapshot - continuous evidence of what's happening.

Captures point-in-time system telemetry for incident analysis.
Telemetry changes every minute on a healthy system and represents
current system load and health indicators.

Design principles:
- Telemetry is evidence, not state
- Snapshots are summaries, not streams
- Everything scoped to an incident
- Collection must be cheap (<100ms)
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Domain 1: CPU & Scheduler Pressure
# =============================================================================


class CPUPressure(BaseModel):
    """CPU and scheduler pressure metrics."""

    model_config = ConfigDict(frozen=True)

    # Load averages
    load_1m: float = Field(ge=0.0, description="1-minute load average")
    load_5m: float = Field(ge=0.0, description="5-minute load average")
    load_15m: float = Field(ge=0.0, description="15-minute load average")

    # Scheduler state
    runnable_tasks: int = Field(ge=0, default=0, description="Currently runnable tasks")
    total_tasks: int = Field(ge=0, default=0, description="Total tasks")

    # VM-specific
    cpu_steal_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="CPU steal percentage (VM signal)")

    # Pressure Stall Information (kernel 4.20+)
    psi_some_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="PSI some: % time at least one task stalled on CPU",
    )
    psi_full_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="PSI full: % time all tasks stalled on CPU",
    )


# =============================================================================
# Domain 2: Memory & Pressure
# =============================================================================


class MemoryPressure(BaseModel):
    """Memory pressure metrics - highest signal for system health."""

    model_config = ConfigDict(frozen=True)

    # Basic memory stats
    total_mb: int = Field(ge=0, description="Total memory in MB")
    available_mb: int = Field(ge=0, description="Available memory in MB")
    free_mb: int = Field(ge=0, default=0, description="Free memory in MB")

    # Swap
    swap_total_mb: int = Field(ge=0, default=0, description="Total swap in MB")
    swap_used_mb: int = Field(ge=0, default=0, description="Used swap in MB")

    # OOM tracking
    oom_kills_1h: int = Field(ge=0, default=0, description="OOM kills in last hour")

    # Pressure Stall Information
    psi_some_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="PSI some: % time at least one task stalled on memory",
    )
    psi_full_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="PSI full: % time all tasks stalled on memory",
    )


# =============================================================================
# Domain 3: Disk & I/O Pressure
# =============================================================================


class DiskInfo(BaseModel):
    """Information about a single disk mount."""

    model_config = ConfigDict(frozen=True)

    mount: str = Field(description="Mount point")
    used_pct: float = Field(ge=0.0, le=100.0, description="Usage percentage")
    avail_gb: float = Field(ge=0.0, description="Available space in GB")
    device: str = Field(default="", description="Device path")
    read_only: bool = Field(default=False, description="Mounted read-only")


class DiskIOPressure(BaseModel):
    """Disk and I/O pressure metrics."""

    model_config = ConfigDict(frozen=True)

    # Disk usage
    disks: tuple[DiskInfo, ...] = Field(default_factory=tuple, description="Disk mount information")

    # I/O pressure
    io_wait_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="CPU time waiting for I/O")

    # I/O errors
    io_errors_1h: int = Field(ge=0, default=0, description="I/O errors in last hour")

    # Pressure Stall Information
    psi_some_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="PSI some: % time at least one task stalled on I/O",
    )
    psi_full_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="PSI full: % time all tasks stalled on I/O",
    )


# =============================================================================
# Domain 4: Network Liveness
# =============================================================================


class InterfaceInfo(BaseModel):
    """Information about a network interface."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Interface name")
    state: str = Field(description="Operational state (UP/DOWN/UNKNOWN)")
    rx_errors: int = Field(ge=0, default=0, description="Receive errors")
    tx_errors: int = Field(ge=0, default=0, description="Transmit errors")


class NetworkLiveness(BaseModel):
    """Network liveness - binary connectivity checks."""

    model_config = ConfigDict(frozen=True)

    # Interface states
    interfaces: tuple[InterfaceInfo, ...] = Field(default_factory=tuple, description="Network interfaces")
    interfaces_up: int = Field(ge=0, default=0, description="Count of UP interfaces")
    interfaces_down: int = Field(ge=0, default=0, description="Count of DOWN interfaces")

    # Routing
    default_route_present: bool = Field(default=False, description="Has default route")

    # DNS resolution
    dns_resolution_ok: bool = Field(default=False, description="DNS resolution working")


# =============================================================================
# Domain 5: Service-Scoped Runtime
# =============================================================================


class ServiceRuntimeSnapshot(BaseModel):
    """Runtime metrics for a specific service - only for implicated services."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(description="Service unit name")
    pid: int | None = Field(default=None, description="Main process PID")

    # Resource usage
    rss_mb: int = Field(ge=0, default=0, description="Resident set size in MB")
    cpu_pct: float = Field(ge=0.0, default=0.0, description="CPU percentage")

    # Systemd state
    active_state: str = Field(default="unknown", description="Active state")
    restart_count: int = Field(ge=0, default=0, description="Number of restarts (NRestarts)")
    last_exit_code: int | None = Field(default=None, description="Last exit status")

    # Cgroup limits (if applicable)
    cgroup_memory_limit_mb: int | None = Field(default=None, description="Memory limit from cgroup")


# =============================================================================
# Domain 6: Kernel & Hardware Signals
# =============================================================================


class KernelHardwareSignals(BaseModel):
    """Kernel and hardware health signals."""

    model_config = ConfigDict(frozen=True)

    # Event counts
    oom_events_1h: int = Field(ge=0, default=0, description="OOM events in last hour")
    kernel_warnings_1h: int = Field(ge=0, default=0, description="Kernel warnings in last hour")
    hardware_faults_1h: int = Field(ge=0, default=0, description="Hardware faults in last hour")

    # Thermal
    thermal_throttle_active: bool = Field(default=False, description="CPU thermal throttling active")
    max_temp_celsius: int = Field(default=0, description="Maximum temperature reading")

    # SMART summary
    smart_warnings: int = Field(ge=0, default=0, description="Drives with SMART warnings")


# =============================================================================
# Domain 7: GPU Metrics
# =============================================================================


class GPUDevice(BaseModel):
    """Single GPU device metrics."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0, description="GPU index")
    name: str = Field(default="", description="GPU name, e.g., 'NVIDIA RTX 4090'")
    uuid: str = Field(default="", description="GPU UUID")

    # Memory
    memory_used_mb: int = Field(ge=0, default=0, description="Used VRAM in MB")
    memory_total_mb: int = Field(ge=0, default=0, description="Total VRAM in MB")
    memory_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="VRAM usage percentage")

    # Utilization
    utilization_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="GPU utilization percentage")

    # Thermal
    temperature_c: int = Field(ge=0, default=0, description="GPU temperature in Celsius")

    # Power
    power_watts: int = Field(ge=0, default=0, description="Power draw in watts")

    # Throttling
    throttle_reasons: int = Field(ge=0, default=0, description="Throttle reason bitmask")

    # ECC errors
    ecc_errors: int = Field(ge=0, default=0, description="Total ECC errors")


class GPUMetrics(BaseModel):
    """GPU subsystem metrics."""

    model_config = ConfigDict(frozen=True)

    # Availability
    available: bool = Field(default=False, description="Whether GPU monitoring is available")
    device_count: int = Field(ge=0, default=0, description="Number of GPUs detected")

    # Devices
    devices: tuple[GPUDevice, ...] = Field(default_factory=tuple, description="Per-GPU metrics")

    # Aggregated metrics (for quick fingerprinting)
    max_memory_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="Max VRAM usage across GPUs")
    max_utilization_pct: float = Field(ge=0.0, le=100.0, default=0.0, description="Max utilization across GPUs")
    max_temperature_c: int = Field(ge=0, default=0, description="Max temperature across GPUs")
    total_ecc_errors: int = Field(ge=0, default=0, description="Total ECC errors across GPUs")


# =============================================================================
# Complete Telemetry Snapshot
# =============================================================================


class TelemetrySnapshot(BaseModel):
    """Complete telemetry snapshot - what's happening right now.

    Captures point-in-time system metrics that change frequently.
    Use for incident evidence and before/after comparison.

    Typical size: 10-20KB JSON
    Collection time: <100ms
    """

    model_config = ConfigDict(frozen=True)

    # Domains
    cpu: CPUPressure
    memory: MemoryPressure
    disk: DiskIOPressure
    network: NetworkLiveness
    kernel: KernelHardwareSignals

    # GPU (optional - only present if NVIDIA GPU available)
    gpu: GPUMetrics = Field(default_factory=GPUMetrics, description="GPU metrics (if available)")

    # Service-scoped (only for implicated services)
    services: tuple[ServiceRuntimeSnapshot, ...] = Field(
        default_factory=tuple,
        description="Runtime metrics for implicated services",
    )

    # Metadata
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    hostname: str = Field(default="", description="System hostname")
    uptime_sec: int = Field(ge=0, default=0, description="System uptime in seconds")


# =============================================================================
# Collection Functions
# =============================================================================


def _parse_psi_file(path: str) -> tuple[float, float]:
    """Parse PSI (Pressure Stall Information) file.

    Returns (some_pct, full_pct) for avg10 window.
    """
    try:
        content = Path(path).read_text()
        some_pct = 0.0
        full_pct = 0.0

        for line in content.strip().split("\n"):
            # Format: some avg10=0.00 avg60=0.00 avg300=0.00 total=12345
            if line.startswith("some"):
                match = re.search(r"avg10=(\d+\.\d+)", line)
                if match:
                    some_pct = float(match.group(1))
            elif line.startswith("full"):
                match = re.search(r"avg10=(\d+\.\d+)", line)
                if match:
                    full_pct = float(match.group(1))

        return some_pct, full_pct
    except Exception:
        return 0.0, 0.0


def _collect_cpu_pressure() -> CPUPressure:
    """Collect CPU pressure metrics."""
    # Load averages and runnable tasks from /proc/loadavg
    load_1m, load_5m, load_15m = 0.0, 0.0, 0.0
    runnable_tasks, total_tasks = 0, 0

    try:
        content = Path("/proc/loadavg").read_text()
        parts = content.split()
        load_1m = float(parts[0])
        load_5m = float(parts[1])
        load_15m = float(parts[2])

        # Field 4 is runnable/total, e.g., "1/234"
        if "/" in parts[3]:
            runnable_str, total_str = parts[3].split("/")
            runnable_tasks = int(runnable_str)
            total_tasks = int(total_str)
    except Exception:
        pass

    # CPU steal from /proc/stat
    cpu_steal_pct = 0.0
    try:
        content = Path("/proc/stat").read_text()
        for line in content.split("\n"):
            if line.startswith("cpu "):
                # cpu user nice system idle iowait irq softirq steal guest guest_nice
                parts = line.split()
                if len(parts) >= 9:
                    # Steal is field 9 (index 8)
                    steal = int(parts[8])
                    total = sum(int(p) for p in parts[1:])
                    if total > 0:
                        cpu_steal_pct = (steal / total) * 100.0
                break
    except Exception:
        pass

    # PSI for CPU
    psi_some, psi_full = _parse_psi_file("/proc/pressure/cpu")

    return CPUPressure(
        load_1m=round(load_1m, 2),
        load_5m=round(load_5m, 2),
        load_15m=round(load_15m, 2),
        runnable_tasks=runnable_tasks,
        total_tasks=total_tasks,
        cpu_steal_pct=round(cpu_steal_pct, 2),
        psi_some_pct=psi_some,
        psi_full_pct=psi_full,
    )


def _collect_memory_pressure(oom_kills_1h: int = 0) -> MemoryPressure:
    """Collect memory pressure metrics."""
    total_mb, available_mb, free_mb = 0, 0, 0
    swap_total_mb, swap_free_mb = 0, 0

    try:
        content = Path("/proc/meminfo").read_text()
        for line in content.split("\n"):
            if line.startswith("MemTotal:"):
                total_mb = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                available_mb = int(line.split()[1]) // 1024
            elif line.startswith("MemFree:"):
                free_mb = int(line.split()[1]) // 1024
            elif line.startswith("SwapTotal:"):
                swap_total_mb = int(line.split()[1]) // 1024
            elif line.startswith("SwapFree:"):
                swap_free_mb = int(line.split()[1]) // 1024
    except Exception:
        pass

    # PSI for memory
    psi_some, psi_full = _parse_psi_file("/proc/pressure/memory")

    return MemoryPressure(
        total_mb=total_mb,
        available_mb=available_mb,
        free_mb=free_mb,
        swap_total_mb=swap_total_mb,
        swap_used_mb=swap_total_mb - swap_free_mb,
        oom_kills_1h=oom_kills_1h,
        psi_some_pct=psi_some,
        psi_full_pct=psi_full,
    )


def _collect_disk_io_pressure(io_errors_1h: int = 0) -> DiskIOPressure:
    """Collect disk and I/O pressure metrics."""
    disks: list[DiskInfo] = []

    # Disk usage via statvfs (faster than df subprocess)
    important_mounts = ("/", "/home", "/var", "/tmp", "/boot")
    for mount in important_mounts:
        try:
            stat = os.statvfs(mount)
            total_blocks = stat.f_blocks
            avail_blocks = stat.f_bavail
            block_size = stat.f_frsize

            if total_blocks > 0:
                used_pct = ((total_blocks - avail_blocks) / total_blocks) * 100.0
                avail_gb = (avail_blocks * block_size) / (1024 * 1024 * 1024)

                # Check if read-only
                read_only = bool(stat.f_flag & os.ST_RDONLY)

                disks.append(
                    DiskInfo(
                        mount=mount,
                        used_pct=round(used_pct, 1),
                        avail_gb=round(avail_gb, 2),
                        read_only=read_only,
                    )
                )
        except OSError:
            # Mount doesn't exist
            pass

    # I/O wait from /proc/stat
    io_wait_pct = 0.0
    try:
        content = Path("/proc/stat").read_text()
        for line in content.split("\n"):
            if line.startswith("cpu "):
                # cpu user nice system idle iowait irq softirq steal guest guest_nice
                parts = line.split()
                if len(parts) >= 6:
                    iowait = int(parts[5])
                    total = sum(int(p) for p in parts[1:])
                    if total > 0:
                        io_wait_pct = (iowait / total) * 100.0
                break
    except Exception:
        pass

    # PSI for I/O
    psi_some, psi_full = _parse_psi_file("/proc/pressure/io")

    return DiskIOPressure(
        disks=tuple(disks),
        io_wait_pct=round(io_wait_pct, 2),
        io_errors_1h=io_errors_1h,
        psi_some_pct=psi_some,
        psi_full_pct=psi_full,
    )


def _collect_network_liveness() -> NetworkLiveness:
    """Collect network liveness metrics."""
    interfaces: list[InterfaceInfo] = []
    up_count = 0
    down_count = 0

    # Get interface info via ip command (fast and reliable)
    try:
        result = subprocess.run(
            ["ip", "-j", "link", "show"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            import json

            links = json.loads(result.stdout)
            for link in links:
                name = link.get("ifname", "")
                if name == "lo":  # Skip loopback
                    continue

                state = link.get("operstate", "UNKNOWN")
                stats = link.get("stats64", {})
                rx_errors = stats.get("rx", {}).get("errors", 0)
                tx_errors = stats.get("tx", {}).get("errors", 0)

                interfaces.append(
                    InterfaceInfo(
                        name=name,
                        state=state,
                        rx_errors=rx_errors,
                        tx_errors=tx_errors,
                    )
                )

                if state == "UP":
                    up_count += 1
                elif state == "DOWN":
                    down_count += 1
    except Exception:
        pass

    # Check for default route
    default_route_present = False
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        default_route_present = bool(result.stdout.strip())
    except Exception:
        pass

    # Quick DNS resolution test
    dns_resolution_ok = False
    try:
        # Use a short timeout UDP check
        socket.setdefaulttimeout(1.0)
        socket.gethostbyname("dns.google")
        dns_resolution_ok = True
    except Exception:
        pass
    finally:
        socket.setdefaulttimeout(None)

    return NetworkLiveness(
        interfaces=tuple(interfaces),
        interfaces_up=up_count,
        interfaces_down=down_count,
        default_route_present=default_route_present,
        dns_resolution_ok=dns_resolution_ok,
    )


def _collect_service_runtime(service: str) -> ServiceRuntimeSnapshot | None:
    """Collect runtime metrics for a specific service."""
    try:
        # Get service info via systemctl show
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service,
                "--property=MainPID,ActiveState,NRestarts,ExecMainStatus,MemoryCurrent,CPUUsageNSec",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        props: dict[str, str] = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                props[key] = val

        pid = int(props.get("MainPID", "0")) or None
        active_state = props.get("ActiveState", "unknown")
        restart_count = int(props.get("NRestarts", "0"))

        exit_status = props.get("ExecMainStatus", "")
        last_exit_code = int(exit_status) if exit_status.isdigit() else None

        # Memory from MemoryCurrent (in bytes)
        mem_current = props.get("MemoryCurrent", "")
        rss_mb = 0
        if mem_current.isdigit():
            rss_mb = int(mem_current) // (1024 * 1024)
        elif mem_current == "[not set]":
            # Try to get from /proc if we have PID
            if pid:
                try:
                    status = Path(f"/proc/{pid}/status").read_text()
                    for line in status.split("\n"):
                        if line.startswith("VmRSS:"):
                            rss_mb = int(line.split()[1]) // 1024
                            break
                except Exception:
                    pass

        # CPU percentage (approximate from CPUUsageNSec if available)
        cpu_pct = 0.0

        # Cgroup memory limit
        cgroup_limit = None
        if pid:
            try:
                # Try cgroup v2 first
                cgroup_path = Path(f"/proc/{pid}/cgroup")
                if cgroup_path.exists():
                    content = cgroup_path.read_text()
                    for line in content.split("\n"):
                        if "0::" in line:
                            cgroup_name = line.split("::")[-1].strip()
                            mem_max = Path(f"/sys/fs/cgroup{cgroup_name}/memory.max")
                            if mem_max.exists():
                                val = mem_max.read_text().strip()
                                if val != "max" and val.isdigit():
                                    cgroup_limit = int(val) // (1024 * 1024)
                            break
            except Exception:
                pass

        return ServiceRuntimeSnapshot(
            service=service,
            pid=pid,
            rss_mb=rss_mb,
            cpu_pct=cpu_pct,
            active_state=active_state,
            restart_count=restart_count,
            last_exit_code=last_exit_code,
            cgroup_memory_limit_mb=cgroup_limit,
        )
    except Exception:
        return None


def _collect_kernel_hardware_signals(
    oom_events_1h: int = 0,
    kernel_warnings_1h: int = 0,
    hardware_faults_1h: int = 0,
) -> KernelHardwareSignals:
    """Collect kernel and hardware signals."""
    # Check thermal throttling
    thermal_throttle_active = False
    try:
        throttle_path = Path("/sys/devices/system/cpu/cpu0/thermal_throttle/core_throttle_count")
        if throttle_path.exists():
            count = int(throttle_path.read_text().strip())
            thermal_throttle_active = count > 0
    except Exception:
        pass

    # Get max temperature (try sensors)
    max_temp_celsius = 0
    try:
        result = subprocess.run(
            ["sensors", "-j"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            for _adapter, sensors in data.items():
                if isinstance(sensors, dict):
                    for _sensor_name, readings in sensors.items():
                        if isinstance(readings, dict):
                            for key, value in readings.items():
                                if key.endswith("_input") and isinstance(value, (int, float)):
                                    temp = int(value)
                                    if temp > max_temp_celsius:
                                        max_temp_celsius = temp
    except Exception:
        pass

    return KernelHardwareSignals(
        oom_events_1h=oom_events_1h,
        kernel_warnings_1h=kernel_warnings_1h,
        hardware_faults_1h=hardware_faults_1h,
        thermal_throttle_active=thermal_throttle_active,
        max_temp_celsius=max_temp_celsius,
        smart_warnings=0,  # Requires smartctl which is slow
    )


def _get_hostname() -> str:
    """Get system hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _collect_gpu_metrics() -> GPUMetrics:
    """Collect GPU metrics from nvidia-smi.

    Uses nvidia-smi for simplicity. Falls back gracefully if not available.

    Returns:
        GPUMetrics with current GPU state, or empty metrics if no GPU.
    """
    try:
        # Query GPU metrics via nvidia-smi
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,uuid,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,clocks_throttle_reasons.active",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return GPUMetrics(available=False)

        devices: list[GPUDevice] = []
        max_memory_pct = 0.0
        max_utilization_pct = 0.0
        max_temperature_c = 0

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue

            try:
                index = int(parts[0])
                name = parts[1]
                uuid = parts[2]

                # Memory (nvidia-smi returns in MiB)
                memory_used_mb = int(float(parts[3])) if parts[3] != "[N/A]" else 0
                memory_total_mb = int(float(parts[4])) if parts[4] != "[N/A]" else 0
                memory_pct = (memory_used_mb / memory_total_mb * 100.0) if memory_total_mb > 0 else 0.0

                # Utilization
                utilization_pct = float(parts[5]) if parts[5] != "[N/A]" else 0.0

                # Temperature
                temperature_c = int(float(parts[6])) if parts[6] != "[N/A]" else 0

                # Power (may not be available on all GPUs)
                power_watts = 0
                if len(parts) > 7 and parts[7] != "[N/A]":
                    try:
                        power_watts = int(float(parts[7]))
                    except ValueError:
                        pass

                # Throttle reasons (bitmask, may not be available)
                throttle_reasons = 0
                if len(parts) > 8 and parts[8] != "[N/A]" and parts[8] != "Not Active":
                    # nvidia-smi returns hex or "Not Active"
                    try:
                        if parts[8].startswith("0x"):
                            throttle_reasons = int(parts[8], 16)
                    except ValueError:
                        pass

                device = GPUDevice(
                    index=index,
                    name=name,
                    uuid=uuid,
                    memory_used_mb=memory_used_mb,
                    memory_total_mb=memory_total_mb,
                    memory_pct=round(memory_pct, 1),
                    utilization_pct=utilization_pct,
                    temperature_c=temperature_c,
                    power_watts=power_watts,
                    throttle_reasons=throttle_reasons,
                    ecc_errors=0,  # Would need separate query for ECC
                )
                devices.append(device)

                # Track aggregates
                if memory_pct > max_memory_pct:
                    max_memory_pct = memory_pct
                if utilization_pct > max_utilization_pct:
                    max_utilization_pct = utilization_pct
                if temperature_c > max_temperature_c:
                    max_temperature_c = temperature_c

            except (ValueError, IndexError):
                continue

        if not devices:
            return GPUMetrics(available=False)

        return GPUMetrics(
            available=True,
            device_count=len(devices),
            devices=tuple(devices),
            max_memory_pct=round(max_memory_pct, 1),
            max_utilization_pct=round(max_utilization_pct, 1),
            max_temperature_c=max_temperature_c,
            total_ecc_errors=sum(d.ecc_errors for d in devices),
        )

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return GPUMetrics(available=False)


def _get_uptime_sec() -> int:
    """Get system uptime in seconds."""
    try:
        content = Path("/proc/uptime").read_text()
        return int(float(content.split()[0]))
    except Exception:
        return 0


def collect_telemetry_snapshot(
    implicated_services: list[str] | None = None,
    oom_kills_1h: int = 0,
    io_errors_1h: int = 0,
    kernel_warnings_1h: int = 0,
    hardware_faults_1h: int = 0,
) -> TelemetrySnapshot:
    """Collect a complete telemetry snapshot.

    Gathers point-in-time system telemetry for incident evidence.
    Designed to complete in <100ms.

    Args:
        implicated_services: Service names to collect runtime metrics for.
        oom_kills_1h: OOM kill count from telemetry events.
        io_errors_1h: I/O error count from telemetry events.
        kernel_warnings_1h: Kernel warning count from telemetry events.
        hardware_faults_1h: Hardware fault count from telemetry events.

    Returns:
        TelemetrySnapshot with current system state.
    """
    # Collect service runtimes for implicated services
    service_snapshots: list[ServiceRuntimeSnapshot] = []
    if implicated_services:
        for svc in implicated_services[:10]:  # Limit to 10 services
            snap = _collect_service_runtime(svc)
            if snap:
                service_snapshots.append(snap)

    return TelemetrySnapshot(
        cpu=_collect_cpu_pressure(),
        memory=_collect_memory_pressure(oom_kills_1h),
        disk=_collect_disk_io_pressure(io_errors_1h),
        network=_collect_network_liveness(),
        kernel=_collect_kernel_hardware_signals(
            oom_events_1h=oom_kills_1h,
            kernel_warnings_1h=kernel_warnings_1h,
            hardware_faults_1h=hardware_faults_1h,
        ),
        gpu=_collect_gpu_metrics(),
        services=tuple(service_snapshots),
        collected_at=datetime.now(timezone.utc),
        hostname=_get_hostname(),
        uptime_sec=_get_uptime_sec(),
    )
